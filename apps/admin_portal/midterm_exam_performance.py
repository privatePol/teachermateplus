from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q

from apps.academics.models import FacultyAssignment
from apps.admin_portal.services import AdminScopeService
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.departmental_exams.exam_units import ExaminationUnit, resolve_examination_unit
from apps.departmental_exams.models import (
    CycleCourse, CycleCourseOffering, ExamCourseEquivalencyMembership, ExaminationCycle,
)
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseBaseValueOverride, CourseTemplateAssignment, GradeSubmission,
    GradingTemplate, GradingTemplatePeriod, StudentPeriodGrade, TenantGradingProfile,
)
from apps.grading.services import FacultyGradingService
from apps.rbac.models import UserPermission, UserRole
from apps.tenants.models import Department


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
        for period in GradingTemplatePeriod.objects.filter(
            template_id__in=templates, is_active=True, code=period_code,
        ).order_by("sequence_no", "id"):
            self.periods.setdefault(period.template_id, period)
        self.tenant_threshold = SystemSettingService.get(
            "PASSING_GRADE_THRESHOLD", tenant_id=tenant_id, default="75",
        )

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
        profile = self._profile(offering)
        template = (
            self.assignments.get((offering.course_id, offering.term_id))
            or self.assignments.get((offering.course_id, None))
            or (profile.grading_template if profile else self.fallback)
        )
        period = self.periods.get(template.id) if template else None
        if period is None:
            return None, None
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
        )


class MidtermExamPerformanceReportService:
    """Build an aggregate-only report from finalized official Midterm exam grades."""

    PERMISSION_CODE = "grading_analytics.read"
    PERIOD_CODE = "MIDTERM"
    UNITS_PER_PAGE = 10
    ROWS_PER_PAGE = 50

    STATUS_COMPLETE = "Complete"
    STATUS_NOT_COMPARABLE = "Not comparable"
    STATUS_NO_ROSTER = "No eligible roster"
    STATUS_NOT_SUBMITTED = "Midterm grades not submitted"
    STATUS_REOPENED = "Midterm submission reopened"
    STATUS_INCOMPLETE = "Incomplete finalized results"
    STATUS_NO_PERIOD = "Midterm grading period unavailable"
    STATUS_INVALID_SCALE = "Official result outside 0-100 scale"
    STATUS_UNCLASSIFIED = "Legacy classification unconfirmed"
    STATUS_INVALID_UNIT = "Invalid examination unit"

    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def cycle_options(cls, request):
        tenant_id = getattr(request, "scope", {}).get("tenant_id")
        term_ids = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        return (
            ExaminationCycle.objects.filter(
                tenant_id=tenant_id,
                term_id__in=term_ids,
                exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
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
    def build_report(cls, request, cycle):
        campuses = cls._authorized_campuses(request, cycle.tenant_id) if cycle else []
        campus_ids = [campus.id for campus in campuses]
        course_code = request.GET.get("course_code", "").strip()[:64]
        report = {
            "rows": [], "complete_count": 0, "incomplete_count": 0,
            "selected_course_code": course_code, "unit_page": None,
            "row_page": None, "total_offerings": 0,
        }
        if cycle is None or not campus_ids:
            return report

        scoped_offering_ids = cls._scoped_offering_ids(request, cycle, campus_ids)
        snapshots_scope = (
            CycleCourseOffering.objects.filter(
                cycle_course__cycle=cycle,
                cycle_course__inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
                offering_id__in=scoped_offering_ids,
                campus_id__in=campus_ids,
            )
        )
        visible_courses = list(
            snapshots_scope.order_by().values_list(
                "cycle_course_id", "cycle_course__course__code",
            ).distinct()
        )
        if not visible_courses:
            return report
        memberships_by_member = defaultdict(list)
        for member_id, group_id in ExamCourseEquivalencyMembership.objects.filter(
            cycle_course_id__in=[member_id for member_id, _code in visible_courses],
            active_marker=1,
            group__is_active=True,
        ).values_list("cycle_course_id", "group_id"):
            memberships_by_member[member_id].append(group_id)

        units = {}
        for member_id, code in visible_courses:
            memberships = memberships_by_member[member_id]
            unit_key = ("group", memberships[0]) if len(memberships) == 1 else ("course", member_id)
            unit = units.setdefault(unit_key, {"member_ids": set(), "codes": set()})
            unit["member_ids"].add(member_id)
            unit["codes"].add(code)
        unit_list = sorted(
            (unit for unit in units.values()
             if not course_code or any(code.casefold() == course_code.casefold() for code in unit["codes"])),
            key=lambda unit: (min(code.casefold() for code in unit["codes"]), min(unit["member_ids"])),
        )
        unit_page = Paginator(unit_list, cls.UNITS_PER_PAGE).get_page(request.GET.get("unit_page"))
        report["unit_page"] = unit_page
        selected_member_ids = {
            member_id for unit in unit_page.object_list for member_id in unit["member_ids"]
        }
        if not selected_member_ids:
            return report

        snapshots = list(
            snapshots_scope.filter(cycle_course_id__in=selected_member_ids)
            .select_related(
                "cycle_course__course", "cycle_course__cycle",
                "offering__campus", "offering__course", "offering__department",
                "offering__program", "offering__section", "offering__term",
            )
            .order_by("offering__campus__code", "offering__course__code", "offering__section__code", "offering_id")
        )
        if not snapshots:
            return report

        offering_ids = [snapshot.offering_id for snapshot in snapshots]
        faculty_by_offering = {}
        for assignment in (
            FacultyAssignment.objects.filter(
                offering_id__in=offering_ids,
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            )
            .select_related("faculty_user")
            .order_by("offering_id", "-is_primary", "-accepted_at", "-assigned_at")
        ):
            faculty_by_offering.setdefault(assignment.offering_id, assignment.faculty_user)

        eligible_by_offering = defaultdict(set)
        for offering_id, student_id in (
            Enrollment.objects.filter(
                course_offering_id__in=offering_ids,
                is_active=True,
                student__is_active=True,
                student__department__is_active=True,
            )
            .filter(Q(student__program__isnull=True) | Q(student__program__is_active=True))
            .exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)
            .values_list("course_offering_id", "student_id")
        ):
            eligible_by_offering[offering_id].add(student_id)

        period_by_context = {}
        signature_by_context = {}
        configuration = _BulkGradingConfiguration(
            [snapshot.offering for snapshot in snapshots], tenant_id=cycle.tenant_id,
            term_id=cycle.term_id, period_code=cls.PERIOD_CODE,
        )
        for snapshot in snapshots:
            offering = snapshot.offering
            context_key = (
                offering.course_id, offering.campus_id, offering.department_id,
                offering.program_id or offering.section.program_id, offering.term_id,
            )
            if context_key in period_by_context:
                continue
            period_by_context[context_key], signature_by_context[context_key] = configuration.resolve(offering)

        period_ids = {period.id for period in period_by_context.values() if period is not None}
        submission_by_key = {
            (offering_id, period_id): status
            for offering_id, period_id, status in GradeSubmission.objects.filter(
                offering_id__in=offering_ids,
                template_period_id__in=period_ids,
            ).values_list("offering_id", "template_period_id", "status")
        }
        grades_by_key = defaultdict(dict)
        for offering_id, period_id, student_id, grade, finalized in (
            StudentPeriodGrade.objects.filter(
                offering_id__in=offering_ids,
                template_period_id__in=period_ids,
            ).values_list("offering_id", "template_period_id", "student_id", "exam_grade", "is_finalized")
        ):
            if student_id in eligible_by_offering[offering_id]:
                grades_by_key[(offering_id, period_id)][student_id] = (grade, finalized)

        visible_member_ids = {snapshot.cycle_course_id for snapshot in snapshots}
        units_by_member = {}
        rows = []
        for snapshot in snapshots:
            cycle_course = snapshot.cycle_course
            if cycle_course.id not in units_by_member:
                try:
                    unit = resolve_examination_unit(cycle_course)
                    unit_error = False
                except ValidationError:
                    unit = ExaminationUnit(primary=cycle_course, members=(cycle_course,))
                    unit_error = True
                for member in unit.members:
                    units_by_member[member.id] = (unit, unit_error)
            unit, unit_error = units_by_member[cycle_course.id]
            offering = snapshot.offering
            context_key = (
                offering.course_id, offering.campus_id, offering.department_id,
                offering.program_id or offering.section.program_id, offering.term_id,
            )
            rows.append(cls._build_row(
                snapshot=snapshot, unit=unit, unit_error=unit_error,
                visible_member_ids=visible_member_ids,
                faculty=faculty_by_offering.get(offering.id),
                eligible_student_ids=eligible_by_offering[offering.id],
                period=period_by_context[context_key],
                context_signature=signature_by_context[context_key],
                submission_by_key=submission_by_key,
                grades_by_key=grades_by_key,
            ))

        cls._apply_ranks(rows)
        rows.sort(key=lambda row: (
            row["course_group"].casefold(), row["campus_code"].casefold(),
            row["course_code"].casefold(), row["section_code"].casefold(), row["offering_id"],
        ))
        row_page = Paginator(rows, cls.ROWS_PER_PAGE).get_page(request.GET.get("row_page"))
        report.update({
            "rows": list(row_page.object_list),
            "row_page": row_page,
            "total_offerings": len(rows),
            "complete_count": sum(row["rank"] is not None for row in rows),
            "incomplete_count": sum(row["rank"] is None for row in rows),
        })
        return report

    @classmethod
    def _build_row(cls, *, snapshot, unit, unit_error, visible_member_ids, faculty,
                   eligible_student_ids, period, context_signature, submission_by_key,
                   grades_by_key):
        offering = snapshot.offering
        member_codes = tuple(sorted(
            member.course.code for member in unit.members if member.id in visible_member_ids
        ))
        all_members_visible = len(member_codes) == len(unit.members)
        course_group = (
            unit.group.name if all_members_visible else "Scoped examination unit"
        ) if unit.group is not None else unit.primary.course.code
        unit_key = ("group", unit.group.id) if unit.group is not None else ("course", unit.primary.id)
        faculty_name = ((faculty.full_name or "").strip() or faculty.username) if faculty else "Unassigned"
        classification = snapshot.cycle_course.exam_classification
        row = {
            "unit_key": unit_key,
            "course_group": course_group,
            "member_codes": member_codes,
            "classification": classification,
            "classification_label": snapshot.cycle_course.get_exam_classification_display(),
            "campus_code": offering.campus.code,
            "campus_name": offering.campus.name,
            "course_code": offering.course.code,
            "faculty_name": faculty_name,
            "section_code": offering.section.code,
            "student_count": len(eligible_student_ids),
            "highest_score": None,
            "lowest_score": None,
            "class_average": None,
            "rank": None,
            "data_status": "",
            "status_detail": "",
            "offering_id": offering.id,
            "context_signature": context_signature,
        }

        if unit_error:
            row["data_status"] = cls.STATUS_INVALID_UNIT
            return row
        if classification == CycleCourse.ExamClassification.UNCLASSIFIED_LEGACY:
            row["data_status"] = cls.STATUS_UNCLASSIFIED
            return row
        if not eligible_student_ids:
            row["data_status"] = cls.STATUS_NO_ROSTER
            return row

        if period is None:
            row["data_status"] = cls.STATUS_NO_PERIOD
            return row

        submission_status = submission_by_key.get((offering.id, period.id))
        if submission_status is None or submission_status == GradeSubmission.Status.DRAFT:
            row["data_status"] = cls.STATUS_NOT_SUBMITTED
            return row
        if submission_status == GradeSubmission.Status.REOPENED:
            row["data_status"] = cls.STATUS_REOPENED
            return row

        result_rows = list(grades_by_key[(offering.id, period.id)].values())
        if len(result_rows) != len(eligible_student_ids) or any(
            exam_grade is None or not is_finalized
            for exam_grade, is_finalized in result_rows
        ):
            row["data_status"] = cls.STATUS_INCOMPLETE
            return row

        values = [Decimal(exam_grade) for exam_grade, _is_finalized in result_rows]
        if any(value < Decimal("0") or value > Decimal("100") for value in values):
            row["data_status"] = cls.STATUS_INVALID_SCALE
            return row

        row.update(
            {
                "highest_score": cls._round(max(values)),
                "lowest_score": cls._round(min(values)),
                "class_average": cls._round(sum(values) / Decimal(len(values))),
                "data_status": cls.STATUS_COMPLETE,
            }
        )
        return row

    @staticmethod
    def _round(value):
        return Decimal(value).quantize(Decimal("0.01"))

    @classmethod
    def _apply_ranks(cls, rows):
        complete_by_unit = defaultdict(list)
        for row in rows:
            if row["data_status"] == cls.STATUS_COMPLETE:
                complete_by_unit[row["unit_key"]].append(row)
        for unit_rows in complete_by_unit.values():
            if len({row["context_signature"] for row in unit_rows}) > 1:
                for row in unit_rows:
                    row["data_status"] = cls.STATUS_NOT_COMPARABLE
                    row["status_detail"] = "Different grading configurations within this examination unit."
                continue
            unit_rows.sort(
                key=lambda row: (
                    -row["class_average"],
                    row["campus_code"].casefold(),
                    row["course_code"].casefold(),
                    row["section_code"].casefold(),
                    row["offering_id"],
                )
            )
            prior_average = None
            prior_rank = None
            for position, row in enumerate(unit_rows, start=1):
                if row["class_average"] == prior_average:
                    row["rank"] = prior_rank
                else:
                    row["rank"] = position
                    prior_rank = position
                    prior_average = row["class_average"]
