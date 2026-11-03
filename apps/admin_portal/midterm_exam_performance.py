from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from types import SimpleNamespace
from urllib.parse import urlencode

from django.core import signing
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import (
    BigIntegerField, Case, CharField, Count, F, IntegerField, Max, Min, OuterRef,
    Q, Subquery, Sum, Value, When,
)
from django.db.models.functions import Cast, Coalesce, Lower, Round
from django.http import Http404
from django.urls import reverse

from apps.academics.models import FacultyAssignment
from apps.admin_portal.services import AdminScopeService
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.departmental_exams.exam_units import (
    ExaminationUnit, configuration_compatibility_key, resolve_examination_unit,
)
from apps.departmental_exams.models import (
    CourseExamConfiguration, CycleCourse, CycleCourseOffering,
    ExamCourseEquivalencyMembership, ExaminationCycle,
)
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseBaseValueOverride, CourseTemplateAssignment, GradeSubmission,
    GradingTemplate, GradingTemplatePeriod, StudentPeriodGrade, TenantGradingProfile,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.rbac.models import UserPermission, UserRole
from apps.tenants.models import Department


def _resolve_report_unit(cycle_course):
    """Read historical units without applying the builder's classification rule.

    Keep the other validate_examination_unit invariants here. The shared
    resolver still rejects ambiguous membership; builder validation is unchanged.
    """
    unit = resolve_examination_unit(cycle_course, validate=False)
    if not unit.grouped:
        return unit
    if unit.group.cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
        raise ValidationError("Course equivalency requires an Automatic Generation cycle.")
    if len(unit.members) < 2 or unit.primary.id not in unit.member_ids:
        raise ValidationError("An examination unit requires two members and its active primary.")
    if any(member.cycle_id != unit.group.cycle_id for member in unit.members):
        raise ValidationError("Equivalency member cycle scope is inconsistent.")
    if any(member.inclusion_status != CycleCourse.InclusionStatus.INCLUDED for member in unit.members):
        raise ValidationError("All equivalency members must be Included.")
    configurations = {
        row.cycle_course_id: row
        for row in CourseExamConfiguration.objects.filter(cycle_course_id__in=unit.member_ids)
    }
    if any(member.id not in configurations for member in unit.members):
        raise ValidationError("Every equivalency member requires an examination configuration.")
    primary_key = configuration_compatibility_key(configurations[unit.primary.id])
    if any(configuration_compatibility_key(row) != primary_key for row in configurations.values()):
        raise ValidationError("Equivalency members must have compatible examination configurations.")
    return unit


class _BulkGradingConfiguration:
    """Request-local inputs for the selected units, never a cross-request cache.

    Match FacultyGradingService's resolution precedence and the analytics
    signature. Parity tests guard this read-only projection of those rules.
    Offerings must have course, section and term selected already.
    """

    def __init__(self, offerings, *, tenant_id, term_id, period_code):
        course_ids = {offering.course_id for offering in offerings}
        campus_ids = {offering.campus_id for offering in offerings}
        program_ids = {offering.program_id or offering.section.program_id for offering in offerings}
        self.parents = dict(
            Department.objects.filter(tenant_id=tenant_id).values_list("id", "parent_id")
        )
        self.ancestors = {}
        self.assignments = {}
        templates = {}
        for assignment in (
            CourseTemplateAssignment.objects.filter(
                course_id__in=course_ids, is_active=True,
                grading_template__is_active=True, grading_template__is_published=True,
            )
            .filter(Q(effective_from_term_id=term_id) | Q(effective_from_term__isnull=True))
            .select_related("grading_template").order_by("-created_at")
        ):
            self.assignments.setdefault(
                (assignment.course_id, assignment.effective_from_term_id), assignment.grading_template,
            )
            templates[assignment.grading_template_id] = assignment.grading_template
        self.overrides = {}
        for override in (
            CourseBaseValueOverride.objects.filter(course_id__in=course_ids, is_active=True)
            .filter(Q(effective_from_term_id=term_id) | Q(effective_from_term__isnull=True))
            .order_by("-effective_from_term_id", "-created_at")
        ):
            self.overrides.setdefault(override.course_id, override.base_value)
        self.profiles = defaultdict(list)
        for profile in (
            TenantGradingProfile.objects.filter(
                tenant_id=tenant_id, is_active=True,
                grading_template__is_active=True, grading_template__is_published=True,
            )
            .filter(Q(course_id__in=course_ids) | Q(course__isnull=True))
            .filter(Q(campus_id__in=campus_ids) | Q(campus__isnull=True))
            .filter(Q(program_id__in=program_ids) | Q(program__isnull=True))
            .filter(Q(effective_from_term_id=term_id) | Q(effective_from_term__isnull=True))
            .select_related("grading_template")
        ):
            self.profiles[profile.course_id].append(profile)
            templates[profile.grading_template_id] = profile.grading_template
        self.fallback = (
            GradingTemplate.objects.filter(tenant_id=tenant_id, is_active=True, is_published=True)
            .order_by("-published_at", "-created_at").first()
        )
        if self.fallback:
            templates[self.fallback.id] = self.fallback
        self.periods = {}
        self.ambiguous_period_templates = set()
        period_rows = GradingTemplatePeriod.objects.filter(
            template_id__in=templates, is_active=True,
        ).order_by("sequence_no", "id")
        if period_code != ExaminationCycle.ExamPeriod.FINAL:
            # Keep the established exact physical Midterm resolution unchanged.
            for period in period_rows.filter(code=period_code):
                self.periods.setdefault(period.template_id, period)
        else:
            candidates = defaultdict(list)
            for period in period_rows:
                code = self._final_period_key(period.code)
                name = self._final_period_key(period.name)
                if "FINAL" not in (code, name):
                    continue
                # Do not infer custom codes from a label or choose between two
                # physical Final records, even when only one has results.
                if ((code and code != "FINAL")
                        or name in {"PRELIM", "MIDTERM", "PREFINAL"}):
                    self.ambiguous_period_templates.add(period.template_id)
                else:
                    candidates[period.template_id].append(period)
            for template_id, matches in candidates.items():
                if len(matches) != 1:
                    self.ambiguous_period_templates.add(template_id)
                if template_id not in self.ambiguous_period_templates:
                    self.periods[template_id] = matches[0]
        self.tenant_threshold = SystemSettingService.get(
            "PASSING_GRADE_THRESHOLD", tenant_id=tenant_id, default="75",
        )

    @staticmethod
    def _final_period_key(value):
        # Correction governance supplies exact FINAL / FINAL EXAM normalization.
        # Faculty tabulation and GradeExplanationService also explicitly identify
        # FX and FINALS as Final. Never change governance's global alias policy.
        key = GradingGovernanceService._normalize_period_key(value)
        return "FINAL" if key in {"FX", "FINALS"} else key

    def _profile(self, offering):
        if offering.department_id not in self.ancestors:
            ancestors = []
            department_id = offering.department_id
            while department_id in self.parents and department_id not in ancestors:
                ancestors.append(department_id)
                department_id = self.parents[department_id]
            self.ancestors[offering.department_id] = ancestors
        ancestor_ids = self.ancestors[offering.department_id]
        ancestor_rank = {value: index for index, value in enumerate(ancestor_ids)}
        program_id = offering.program_id or offering.section.program_id
        course_type = (offering.course.course_type or "").strip()
        term_type = (offering.term.term_type or "").strip()
        candidates = []
        for profile in self.profiles[offering.course_id] + self.profiles[None]:
            if (
                profile.campus_id not in (None, offering.campus_id)
                or (profile.department_id is not None and profile.department_id not in ancestor_rank)
                or profile.program_id not in (None, program_id)
                or profile.term_type not in (None, "", term_type)
            ):
                continue
            if course_type:
                if profile.course_type not in (None, "") and profile.course_type.casefold() != course_type.casefold():
                    continue
            elif profile.course_type not in (None, "") and profile.course_id != offering.course_id:
                continue
            candidates.append(profile)
        return min(candidates, key=lambda profile: (
            -bool(profile.course_id), -bool((profile.course_type or "").strip()),
            -bool(profile.program_id), -bool(profile.department_id),
            ancestor_rank.get(profile.department_id, 999), -bool(profile.campus_id),
            -bool(term_type and (profile.term_type or "").strip() == term_type),
            -bool(profile.effective_from_term_id), profile.priority,
            0 if profile.is_default else 1, -profile.id,
        ), default=None)

    def resolve(self, offering):
        period, signature, _ambiguous = self.resolve_with_state(offering)
        return period, signature

    def resolve_with_state(self, offering):
        profile = self._profile(offering)
        template = (
            self.assignments.get((offering.course_id, offering.term_id))
            or self.assignments.get((offering.course_id, None))
            or (profile.grading_template if profile else self.fallback)
        )
        period = self.periods.get(template.id) if template else None
        if period is None:
            return None, None, bool(template and template.pk in self.ambiguous_period_templates)
        mode = (
            profile.period_grade_formula_mode if profile else None
        ) or TenantGradingProfile.PeriodGradeFormulaMode.WEIGHTED_COMPONENTS
        table = []
        if mode == TenantGradingProfile.PeriodGradeFormulaMode.DEPED_TRANSMUTATION:
            table = FacultyGradingService.normalized_deped_transmutation_table(
                (profile.period_grade_formula_json or {}).get("transmutation_table")
            )
        threshold = Decimal("75.00")
        for value in (
            profile.passing_grade_threshold if profile else None,
            template.passing_grade_threshold, self.tenant_threshold,
        ):
            if value is not None:
                try:
                    threshold = FacultyGradingService._round(Decimal(str(value)))
                    break
                except (ArithmeticError, ValueError, TypeError):
                    continue
        base_value = next(value for value in (
            self.overrides.get(offering.course_id),
            profile.default_base_value if profile else None,
            offering.course.default_base_value, template.default_base_value, Decimal("50"),
        ) if value is not None)
        return period, (
            template.id, profile.id if profile else None, mode,
            tuple((str(row["min"]), str(row["max"]), str(row["grade"])) for row in table),
            str(threshold), period.id, str(Decimal(base_value).quantize(Decimal("0.01"))),
        ), False


class MidtermExamPerformanceReportService:
    """Periodical class aggregates; legacy name retained for existing callers.

    Official stored exam results remain the source of existing class statistics.
    They are not asserted to be a standalone transmuted assessment grade.
    """

    PERMISSION_CODE = "grading_analytics.read"
    PERIOD_CODE = "MIDTERM"
    COURSES_PER_PAGE = 5
    ROWS_PER_PAGE = 50

    STATUS_COMPLETE = "Complete"
    STATUS_NOT_COMPARABLE = "Not comparable"
    STATUS_NO_ROSTER = "No eligible roster"
    STATUS_NOT_SUBMITTED = "Midterm grades not submitted"
    STATUS_REOPENED = "Midterm submission reopened"
    STATUS_INCOMPLETE = "Incomplete finalized results"
    STATUS_NO_PERIOD = "Midterm grading period unavailable"
    STATUS_INVALID_SCALE = "Official result outside 0-100 scale"
    STATUS_INVALID_UNIT = "Invalid examination unit"
    STATUS_AMBIGUOUS_PERIOD = "Ambiguous Final grading period"
    HISTORY_BLOCKER = (
        "Historical grading compatibility is unverified: retained submissions "
        "identify the template and period but do not retain the applied formula, "
        "base, transmutation and passing rules. Previous rank and movement are unavailable."
    )

    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    GRADE_BLOCKER = (
        "Standalone examination transmutation and its historical passing threshold "
        "are not captured with the finalized result. DepEd transmutation applies "
        "to the weighted period grade, not automatically to the exam alone. "
        "Passed counts and student rankings are unavailable until an authoritative "
        "exam-grade definition and historical configuration are established."
    )
    COMPARABILITY_BLOCKER = (
        "Cross-section and cross-campus student rankings also require verified "
        "assessment identity or equivalence; a shared grade scale is insufficient."
    )

    @staticmethod
    def selected_exam_period(request):
        value = request.GET.get("exam_period", "MIDTERM")
        return value if value in ExaminationCycle.ExamPeriod.values else "MIDTERM"

    @classmethod
    def cycle_options(cls, request):
        tenant_id = getattr(request, "scope", {}).get("tenant_id")
        term_ids = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        return (
            ExaminationCycle.objects.filter(
                tenant_id=tenant_id,
                term_id__in=term_ids,
                exam_period=cls.selected_exam_period(request),
                is_active=True,
            )
            .select_related("academic_year", "term")
            .order_by("-academic_year__start_date", "-term__sequence_no", "-id")
        )

    @classmethod
    def selected_cycle(cls, request, cycle_options=None):
        options = cycle_options if cycle_options is not None else cls.cycle_options(request)
        selected_id = cls._safe_int(request.GET.get("cycle_id"))
        if selected_id:
            return options.filter(pk=selected_id).first()
        return options.first()

    @classmethod
    def _authorized_campuses(cls, request, tenant_id):
        return [
            campus
            for campus in AdminScopeService.active_scoped_campuses(request).filter(tenant_id=tenant_id)
            if cls._has_campus_permission(request.user, tenant_id=tenant_id, campus_id=campus.id)
        ]

    @classmethod
    def _has_campus_permission(cls, user, *, tenant_id, campus_id):
        scoped_permissions = PermissionService._scoped_user_permissions(
            user, tenant_id=tenant_id, campus_id=campus_id,
        ).filter(permission__code=cls.PERMISSION_CODE)
        if scoped_permissions.filter(grant_type=UserPermission.GrantType.DENY).exists():
            return False
        return PermissionService.has_permission(
            user, cls.PERMISSION_CODE, tenant_id=tenant_id, campus_id=campus_id,
        )

    @classmethod
    def _faculty_scope_for_campus(cls, request, *, tenant_id, campus_id):
        """Derive department scope only from grants for this report permission."""
        if not cls._has_campus_permission(request.user, tenant_id=tenant_id, campus_id=campus_id):
            return [], []
        directly_allowed = PermissionService._scoped_user_permissions(
            request.user, tenant_id=tenant_id, campus_id=campus_id,
        ).filter(
            permission__code=cls.PERMISSION_CODE,
            grant_type=UserPermission.GrantType.ALLOW,
        ).exists()
        if request.user.is_superuser or directly_allowed:
            department_ids = None
        else:
            grants = list(
                UserRole.objects.filter(
                    user=request.user,
                    is_active=True,
                    role__is_active=True,
                    role__role_permissions__permission__code=cls.PERMISSION_CODE,
                    role__role_permissions__permission__is_active=True,
                )
                .exclude(role__code="FACULTY")
                .filter(Q(tenant_id=tenant_id) | Q(tenant__isnull=True))
                .filter(Q(campus_id=campus_id) | Q(campus__isnull=True))
                .select_related("role", "department")
                .distinct()
            )
            if not grants:
                return [], []
            if any(grant.department_id is None for grant in grants):
                department_ids = None
            else:
                grant_department_ids = {
                    grant.department_id
                    for grant in grants
                    if grant.department.tenant_id == tenant_id
                    and grant.department.campus_id == campus_id
                    and grant.department.is_active
                }
                if not grant_department_ids:
                    return [], []
                if {grant.role.code for grant in grants} == {AdminScopeService.COLLEGE_DEAN_ROLE_CODE}:
                    department_ids = AdminScopeService._college_dean_area_chair_department_ids(
                        request,
                        tenant_ids=[tenant_id],
                        campus_ids=[campus_id],
                        dean_department_ids=list(grant_department_ids),
                    )
                else:
                    department_ids = ScopeService.expand_department_ids(
                        grant_department_ids,
                        tenant_id=tenant_id,
                        campus_id=campus_id,
                    )
                if not department_ids:
                    return [], []

        faculty_roles = (
            UserRole.objects.filter(
                role__code="FACULTY",
                role__is_active=True,
                is_active=True,
                user__is_active=True,
            )
            .filter(
                Q(tenant_id=tenant_id)
                | (Q(tenant__isnull=True) & Q(user__default_tenant_id=tenant_id))
            )
            .filter(
                Q(campus_id=campus_id)
                | (Q(campus__isnull=True) & Q(user__default_campus_id=campus_id))
            )
        )
        if department_ids is not None:
            faculty_roles = faculty_roles.filter(
                Q(department_id__in=department_ids)
                | (
                    Q(department__isnull=True)
                    & Q(user__default_campus_id=campus_id)
                    & Q(user__default_department_id__in=department_ids)
                )
            )
        return list(faculty_roles.values_list("user_id", flat=True).distinct()), department_ids

    @classmethod
    def _scoped_offering_ids(cls, request, cycle, campus_ids):
        assignment_scope = Q(pk__in=[])
        for campus_id in campus_ids:
            faculty_ids, department_ids = cls._faculty_scope_for_campus(
                request,
                tenant_id=cycle.tenant_id,
                campus_id=campus_id,
            )
            if faculty_ids:
                scoped = Q(offering__campus_id=campus_id, faculty_user_id__in=faculty_ids)
                if department_ids is not None:
                    scoped &= Q(offering__department_id__in=department_ids)
                assignment_scope |= scoped
        return (
            FacultyAssignment.objects.filter(
                assignment_scope,
                offering__tenant_id=cycle.tenant_id,
                offering__academic_year_id=cycle.academic_year_id,
                offering__term_id=cycle.term_id,
                offering__is_active=True,
                offering__department__is_active=True,
                offering__course__is_active=True,
                offering__section__is_active=True,
                offering__section__department__is_active=True,
                offering__section__program__is_active=True,
                offering__section__program__department__is_active=True,
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                faculty_user__is_active=True,
            )
            .filter(
                Q(offering__program__isnull=True)
                | Q(offering__program__is_active=True, offering__program__department__is_active=True)
            )
            .values_list("offering_id", flat=True)
            .distinct()
        )

    @classmethod
    def _snapshot_scope(cls, request, cycle, campus_ids):
        return CycleCourseOffering.objects.filter(
            cycle_course__cycle=cycle,
            cycle_course__inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
            offering_id__in=cls._scoped_offering_ids(request, cycle, campus_ids),
            campus_id__in=campus_ids,
        )

    @classmethod
    def top_performers(cls, request, cycle, course_id):
        """Fail closed at the unresolved grade-definition boundary, after scope checks.

        Do not release identities, score rows, or a fabricated transmutation.
        This action is deliberately lazy and issues no student-result query.
        """
        if cycle is None:
            raise Http404("Examination course unavailable.")
        campuses = cls._authorized_campuses(request, cycle.tenant_id)
        scope = cls._snapshot_scope(request, cycle, [campus.id for campus in campuses])
        campus_ids = set(scope.filter(offering__course_id=course_id).order_by().values_list(
            "campus_id", flat=True,
        ))
        if not campus_ids:
            raise Http404("Examination course unavailable.")
        # Identity permission is independent of aggregate analytics permission.
        for campus_id in campus_ids:
            permissions = PermissionService._scoped_user_permissions(
                request.user, tenant_id=cycle.tenant_id, campus_id=campus_id,
            ).filter(permission__code="gradebook.view_student_identity")
            if (permissions.filter(grant_type=UserPermission.GrantType.DENY).exists()
                    or not PermissionService.has_permission(
                        request.user, "gradebook.view_student_identity",
                        tenant_id=cycle.tenant_id, campus_id=campus_id,
                    )):
                return {"available": False, "message": "Student identity access is required for this course scope."}
        return {"available": False, "message": cls.GRADE_BLOCKER + " " + cls.COMPARABILITY_BLOCKER}

    @classmethod
    def build_report(cls, request, cycle):
        campuses = cls._authorized_campuses(request, cycle.tenant_id) if cycle else []
        campus_ids = [campus.id for campus in campuses]
        course_code = request.GET.get("course_code", "").strip()[:64]
        report = {
            "exam_period": cycle.exam_period if cycle else cls.selected_exam_period(request),
            "rows": [], "course_groups": [], "complete_count": 0, "incomplete_count": 0,
            "selected_course_code": course_code, "unit_page": None,
            "row_page": None, "total_offerings": 0, "total_rows": 0, "next_url": "", "restart_required": False,
        }
        if cycle is None or not campus_ids:
            report["restart_required"] = bool(request.GET.get("cursor"))
            return report
        snapshots_scope = cls._snapshot_scope(request, cycle, campus_ids)
        visible_courses = sorted(snapshots_scope.order_by().values_list(
            "cycle_course_id", "cycle_course__course__code", "cycle_course__course__title",
        ).distinct())
        memberships = defaultdict(list)
        for member_id, group_id in ExamCourseEquivalencyMembership.objects.filter(
            cycle_course_id__in=[item[0] for item in visible_courses],
            active_marker=1, group__is_active=True,
        ).values_list("cycle_course_id", "group_id"):
            memberships[member_id].append(group_id)
        units = defaultdict(set)
        unit_keys = {}
        for member_id, code, title in visible_courses:
            groups = memberships[member_id]
            key = ("group", groups[0]) if len(groups) == 1 else ("course", member_id)
            units[key].add(member_id)
            unit_keys[member_id] = key
        matching_units = {
            unit_keys[member_id] for member_id, code, title in visible_courses
            if not course_code or code.casefold() == course_code.casefold()
        }
        courses = sorted(
            [item for item in visible_courses if unit_keys[item[0]] in matching_units],
            key=lambda item: (item[1].casefold(), item[0]),
        )
        report["total_rows"] = snapshots_scope.filter(
            cycle_course_id__in=[item[0] for item in courses],
        ).count()
        course_page = Paginator(courses, cls.COURSES_PER_PAGE).get_page(request.GET.get("unit_page"))
        report["unit_page"] = course_page
        selected_ids = {item[0] for item in course_page.object_list}
        if not selected_ids:
            report["restart_required"] = bool(request.GET.get("cursor"))
            return report
        ranking_ids = set().union(*(units[unit_keys[item]] for item in selected_ids))
        ranking_scope = snapshots_scope.filter(cycle_course_id__in=ranking_ids)
        summaries, signatures, unit_details, _histogram, incompatible = cls._ranked_summaries(ranking_scope, cycle)

        # A signed continuation is bound to the actor, filters and current batch.
        # Changes to scope/rosters/results within a course batch require a fresh
        # page rather than appending rows whose rank/order may have changed.
        version = snapshots_scope.aggregate(
            count=Count("pk"), identities=Sum("pk"), updated=Max("updated_at"),
            classification_updated=Max("cycle_course__updated_at"),
            offering_updated=Max("offering__updated_at"),
            section_updated=Max("offering__section__updated_at"),
            campus_updated=Max("offering__campus__updated_at"),
        )
        catalog = hashlib.sha256(json.dumps(
            [visible_courses, sorted((key, value) for key, value in unit_keys.items()), version],
            sort_keys=True, default=str,
        ).encode()).hexdigest()
        fingerprint = hashlib.sha256(json.dumps(
            [visible_courses, summaries, signatures, version], sort_keys=True, default=str,
        ).encode()).hexdigest()
        binding = [request.user.pk, cycle.pk, course_code, course_page.number]
        token = request.GET.get("cursor")
        if token:
            try:
                state = signing.loads(token, salt="midterm-report", max_age=3600)
                if (state["binding"] != binding
                        or state["catalog"] != catalog
                        or state["row"] != cls._safe_int(request.GET.get("row_page", "1"))
                        or (state["version"] is not None and state["version"] != fingerprint)):
                    raise signing.BadSignature("Report changed")
            except (signing.BadSignature, KeyError, TypeError):
                report["restart_required"] = True
                return report

        # Reuse the already evaluated full-unit aggregates. No ranking CASE is
        # sent back to SQL and pagination never recomputes the population.
        display = [row for row in summaries if row["cycle_course_id"] in selected_ids]
        display.sort(key=lambda row: (
            row["_course_order"], row["_rank"] is None, row["_rank"] or 0,
        ))
        row_page = Paginator(display, cls.ROWS_PER_PAGE).get_page(request.GET.get("row_page"))
        page_values = list(row_page.object_list)
        snapshots = {
            snapshot.pk: snapshot for snapshot in ranking_scope.filter(
                pk__in=[item["pk"] for item in page_values],
            ).select_related("offering__campus", "offering__course", "offering__section", "cycle_course")
        }
        if len(snapshots) != len(page_values):
            report["restart_required"] = True
            return report
        faculty_by_offering = {}
        for assignment in FacultyAssignment.objects.filter(
            offering_id__in=[snapshot.offering_id for snapshot in snapshots.values()],
            is_active=True, response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        ).select_related("faculty_user").order_by(
            "offering_id", "-is_primary", "-accepted_at", "-assigned_at", "pk",
        ):
            faculty_by_offering.setdefault(assignment.offering_id, assignment.faculty_user)
        rows = []
        course_groups = {}
        for summary in page_values:
            snapshot = snapshots[summary["pk"]]
            offering = snapshot.offering
            faculty = faculty_by_offering.get(offering.id)
            unit, unit_error = unit_details[snapshot.cycle_course_id]
            member_codes = tuple(sorted(
                member.course.code for member in unit.members if member.id in ranking_ids
            ))
            status = summary["_status"]
            complete = status == cls.STATUS_COMPLETE
            if complete and summary["_unit"] in incompatible:
                status = cls.STATUS_NOT_COMPARABLE
            row = {
                "offering_id": offering.id, "course_id": offering.course_id,
                "course_code": offering.course.code, "course_title": offering.course.title,
                "course_group": (unit.group.name if len(member_codes) == len(unit.members)
                                 else "Scoped examination unit") if unit.group else unit.primary.course.code,
                "member_codes": member_codes, "unit_key": unit_keys[snapshot.cycle_course_id],
                "campus_code": offering.campus.code, "campus_name": offering.campus.name,
                "faculty_name": ((faculty.full_name or "").strip() or faculty.username) if faculty else "Unassigned",
                "section_code": offering.section.code, "student_count": summary["_roster"],
                "highest_score": cls._round(summary["_highest"]) if complete else None,
                "lowest_score": cls._round(summary["_lowest"]) if complete else None,
                "class_average": cls._average(summary["_sum_hundredths"], summary["_roster"]) if complete else None,
                "rank": summary["_rank"],
                "passed_count": None, "passing_rate": None,
                "grade_blocker": cls.GRADE_BLOCKER,
                # submit_period retains IDs/labels, not applied grading rules.
                # Current profiles cannot establish historical compatibility.
                "midterm_rank": None, "rank_movement": None,
                "history_blocker": cls.HISTORY_BLOCKER,
                "data_status": status, "context_signature": signatures[summary["_context"]],
                "status_detail": ("Different grading configurations within this examination unit."
                                  if status == cls.STATUS_NOT_COMPARABLE else ""),
            }
            if cycle.exam_period == ExaminationCycle.ExamPeriod.FINAL:
                row["data_status"] = row["data_status"].replace("Midterm", "Final")
            rows.append(row)
            group = course_groups.setdefault(offering.course_id, {
                "id": offering.course_id, "code": offering.course.code,
                "title": offering.course.title, "rows": [],
                "is_deptal": snapshot.cycle_course.exam_classification == CycleCourse.ExamClassification.DEPARTMENTAL,
                "top_url": reverse("admin_portal:periodical_exam_top_performers", args=[offering.course_id]) + "?" + urlencode({
                    "cycle_id": cycle.pk, "exam_period": cycle.exam_period,
                }),
            })
            group["rows"].append(row)
        next_course = course_page.number
        next_row = None
        if row_page.has_next():
            next_row = row_page.next_page_number()
        elif course_page.has_next():
            next_course = course_page.next_page_number()
            next_row = 1
        if next_row:
            cursor = signing.dumps({
                "binding": [request.user.pk, cycle.pk, course_code, next_course],
                "row": next_row,
                "catalog": catalog,
                "version": fingerprint if next_course == course_page.number else None,
            }, salt="midterm-report", compress=True)
            report["next_url"] = "?" + urlencode({
                "cycle_id": cycle.pk, "exam_period": cycle.exam_period, "course_code": course_code,
                "unit_page": next_course, "row_page": next_row, "cursor": cursor,
            })
        report.update(
            rows=rows, course_groups=list(course_groups.values()), row_page=row_page,
            total_offerings=row_page.paginator.count,
            complete_count=sum(row["rank"] is not None for row in rows),
            incomplete_count=sum(row["rank"] is None for row in rows),
        )
        return report

    @classmethod
    def _ranked_summaries(cls, snapshots, cycle):
        queryset, signatures, unit_details = cls._summaries(snapshots, cycle)
        # Aggregate rows only: no student identities/results or off-page models.
        # Keep database collation order for campus/section ties via stable sorting.
        summaries = list(queryset.order_by(
            Lower("cycle_course__course__code"), "cycle_course_id",
            Lower("offering__campus__code"), Lower("offering__section__code"), "offering_id",
        ).values("pk", "offering_id", "cycle_course_id", "_context", "_unit",
                 "_roster", "_status", "_highest", "_lowest", "_sum_hundredths"))
        buckets = {}
        course_order = {}
        for row in summaries:
            row["_course_order"] = course_order.setdefault(row["cycle_course_id"], len(course_order))
            row["_rank"] = None
            if row["_status"] != cls.STATUS_COMPLETE:
                continue
            key = (row["_unit"], row["_context"], row["_sum_hundredths"], row["_roster"])
            item = buckets.setdefault(key, dict(zip(
                ("_unit", "_context", "_sum_hundredths", "_roster"), key,
            ), frequency=0, identities=0))
            item["frequency"] += 1
            item["identities"] += row["pk"]
        histogram = sorted(buckets.values(), key=lambda item: (
            item["_unit"], item["_context"], item["_sum_hundredths"], item["_roster"],
        ))
        by_unit = defaultdict(list)
        for item in histogram:
            item["average"] = cls._average(item["_sum_hundredths"], item["_roster"])
            by_unit[item["_unit"]].append(item)
        incompatible = set()
        ranks = {}
        for unit_id, items in by_unit.items():
            if len({signatures[item["_context"]] for item in items}) > 1:
                incompatible.add(unit_id)
                continue
            frequency = defaultdict(int)
            for item in items:
                frequency[item["average"]] += item["frequency"]
            position = 1
            for average in sorted(frequency, reverse=True):
                ranks[(unit_id, average)] = position
                position += frequency[average]
        for row in summaries:
            if row["_status"] == cls.STATUS_COMPLETE and row["_unit"] not in incompatible:
                row["_rank"] = ranks[(row["_unit"], cls._average(row["_sum_hundredths"], row["_roster"]))]
        return summaries, signatures, unit_details, histogram, incompatible

    @classmethod
    def _summaries(cls, snapshots, cycle):
        """SQL aggregates across authorized units; hydrate only the display page."""
        contexts = list(snapshots.order_by().annotate(
            program=Coalesce("offering__program_id", "offering__section__program_id"),
        ).values_list(
            "offering__course_id", "offering__campus_id", "offering__department_id", "program",
            "offering__course__course_type", "offering__course__default_base_value",
        ).distinct())
        contexts.sort(key=lambda item: tuple(str(value) for value in item))
        inputs = [
            SimpleNamespace(
                course_id=course_id, campus_id=campus_id, department_id=department_id,
                program_id=program_id, section=SimpleNamespace(program_id=program_id),
                term_id=cycle.term_id, term=cycle.term,
                course=SimpleNamespace(course_type=course_type, default_base_value=base),
            ) for course_id, campus_id, department_id, program_id, course_type, base in contexts
        ]
        configuration = _BulkGradingConfiguration(
            inputs, tenant_id=cycle.tenant_id, term_id=cycle.term_id, period_code=cycle.exam_period,
        )
        signatures, periods = {}, {}
        ambiguous_contexts = []
        context_cases = []
        period_cases = []
        for number, offering in enumerate(inputs):
            period, signatures[number], ambiguous = configuration.resolve_with_state(offering)
            periods[number] = period.pk if period else None
            if ambiguous:
                ambiguous_contexts.append(number)
            match = Q(
                offering__course_id=offering.course_id, offering__campus_id=offering.campus_id,
                offering__department_id=offering.department_id, _program=offering.program_id,
            )
            context_cases.append(When(match, then=Value(number)))
            period_cases.append(When(match, then=Value(periods[number])))
        unit_details = {}
        for member in CycleCourse.objects.filter(
            pk__in=snapshots.values("cycle_course_id"),
        ).select_related("course", "cycle"):
            if member.pk in unit_details:
                continue
            try:
                unit, invalid = _resolve_report_unit(member), False
            except ValidationError:
                unit, invalid = ExaminationUnit(primary=member, members=(member,)), True
            for item in unit.members:
                unit_details[item.pk] = (unit, invalid)
        snapshots = snapshots.annotate(
            _program=Coalesce("offering__program_id", "offering__section__program_id"),
        ).annotate(
            _context=Case(*context_cases, output_field=IntegerField()),
            _unit=Case(*[
                When(cycle_course_id=member_id, then=Value(unit.primary.pk))
                for member_id, (unit, invalid) in unit_details.items()
            ], output_field=IntegerField()),
        ).annotate(_period=Case(*period_cases, output_field=IntegerField()))
        eligible = Enrollment.objects.filter(
            course_offering_id=OuterRef("offering_id"), is_active=True,
            student__is_active=True, student__department__is_active=True,
        ).filter(
            Q(student__program__isnull=True) | Q(student__program__is_active=True),
        ).exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)
        roster = eligible.order_by().values("course_offering_id").annotate(n=Count("student_id", distinct=True))
        grades = StudentPeriodGrade.objects.filter(
            offering_id=OuterRef("offering_id"), template_period_id=OuterRef("_period"),
            student_id__in=eligible.values("student_id"),
        ).order_by().values("offering_id").annotate(
            finalized=Count("pk", filter=Q(is_finalized=True, exam_grade__isnull=False)),
            highest=Max("exam_grade"), lowest=Min("exam_grade"),
            # Official grades have two decimal places. Normalize each value to
            # integer hundredths before summing: SQLite stores decimal values as
            # floats, so summing first would make equality and ties unreliable.
            # Round only removes binary representation noise before the cast;
            # displayed-average rounding is performed with Decimal below.
            total_hundredths=Sum(Cast(Round(F("exam_grade") * 100), BigIntegerField())),
        )
        submissions = GradeSubmission.objects.filter(
            offering_id=OuterRef("offering_id"), template_period_id=OuterRef("_period"),
        ).order_by().values("status")[:1]
        snapshots = snapshots.annotate(
            _roster=Coalesce(Subquery(roster.values("n")), 0),
            _finalized=Coalesce(Subquery(grades.values("finalized")), 0),
            _highest=Subquery(grades.values("highest")), _lowest=Subquery(grades.values("lowest")),
            _sum_hundredths=Subquery(grades.values("total_hundredths")), _submission=Subquery(submissions),
        ).annotate(_status=Case(
            When(cycle_course_id__in=[key for key, (_, invalid) in unit_details.items() if invalid],
                 then=Value(cls.STATUS_INVALID_UNIT)),
            When(_context__in=ambiguous_contexts, then=Value(cls.STATUS_AMBIGUOUS_PERIOD)),
            When(_roster=0, then=Value(cls.STATUS_NO_ROSTER)),
            When(_period__isnull=True, then=Value(cls.STATUS_NO_PERIOD)),
            When(_submission=GradeSubmission.Status.REOPENED, then=Value(cls.STATUS_REOPENED)),
            When(~Q(_submission=GradeSubmission.Status.SUBMITTED) | Q(_submission__isnull=True),
                 then=Value(cls.STATUS_NOT_SUBMITTED)),
            When(~Q(_finalized=F("_roster")), then=Value(cls.STATUS_INCOMPLETE)),
            When(Q(_lowest__lt=0) | Q(_highest__gt=100), then=Value(cls.STATUS_INVALID_SCALE)),
            default=Value(cls.STATUS_COMPLETE), output_field=CharField(),
        ))
        return snapshots, signatures, unit_details

    @staticmethod
    def _round(value):
        return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

    @classmethod
    def _average(cls, total_hundredths, student_count):
        with localcontext() as context:
            context.prec = 28
            context.rounding = ROUND_HALF_EVEN
            return cls._round(Decimal(total_hundredths) / Decimal(student_count * 100))
