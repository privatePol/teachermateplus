"""Isolated, read-only learning analysis. Never compute or finalize academic records."""
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
from itertools import combinations

from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.utils import timezone

from apps.academics.models import ActiveGradingPeriodSetting, CourseOffering, FacultyAssignment, TenantTermGradingPeriod
from apps.admin_portal.academic_performance import AcademicPerformanceInsightService as Reference
from apps.admin_portal.insights import (
    LEADERSHIP_PERMISSION, MIN_STUDENTS, Selection, campus_allowed, component_signature, leadership_access,
)
from apps.admin_portal.insights_generation import RecordsChanged, encoded, fingerprint
from apps.admin_portal.services import AdminScopeService
from apps.enrollment.models import Enrollment
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.grading.models import (
    GradeActivity, GradeCorrectionUnlockWindow, GradeSubmission, GradeSubmissionReopenRequest,
    GradingPeriodLock, GradingTemplatePeriod, StudentActivityScore, StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.grading.tabulation import PeriodSummaryLayoutService
from apps.students.models import Student
from apps.rbac.models import UserPermission, UserRole
from django.utils.crypto import salted_hmac


PERIODS = (("PRELIM", "Prelim"), ("MIDTERM", "Midterm"), ("PREFINAL", "Pre-Final"), ("FINAL", "Final"))
PATTERNS = ("Moved Together", "Moved Opposite", "Mixed / Stable")
MEANINGFUL_CHANGE = Decimal("3.00")
INSUFFICIENT = "Insufficient Comparable Data"
METRICS = ("quizzes", "quiz", "quiz_grade", "activities", "standing", "exam", "periodic")
DISCLOSURE_POLICY_VERSION = "component-and-paired-populations-v1"
COMPARABILITY_POLICY_VERSION = "period-component-effective-modes-and-comparison-v2"
QUIZ_DISPLAY_POLICY_VERSION = "saved-gradebook-quiz-and-descriptive-points-v2-mixed-unavailable"


def key(period):
    return GradingGovernanceService.canonical_correction_period_key(period)


def exact_average(values):
    """Preserve score ratios and class means until after threshold comparisons."""
    return sum((Fraction(v) for v in values), Fraction(0)) / len(values) if values else None


def displayed(value):
    return GradingGovernanceService._round(Decimal(value.numerator) / Decimal(value.denominator)) if value is not None else None


def average(values):
    return displayed(exact_average(values))


def performance_change(previous, current):
    """Call only for comparable percentage scales; missing values have no direction."""
    if previous is None or current is None:
        return None
    delta = Fraction(current) - Fraction(previous)
    return "IMPROVING" if delta >= MEANINGFUL_CHANGE else "DECLINING" if delta <= -MEANINGFUL_CHANGE else "STABLE"


class LearningSelection(Selection):
    # Accepted, currently scoped assignments remain selectable even when a closed
    # offering has no finalized results yet; report availability is separate.
    include_offerings_without_grade_records = True
    assignment_related = Selection.assignment_related + (
        "offering__campus", "offering__academic_year", "offering__term", "offering__department",
        "offering__program__department", "offering__section__program__department",
    )

    @staticmethod
    def assignment_scope(tenant_id, campus_ids):
        # Both NULL means this offering's scope, never a global teaching grant.
        return Selection.assignment_scope(tenant_id, campus_ids) | Q(
            tenant__isnull=True, campus__isnull=True, accepted_at__isnull=False,
        )

    @staticmethod
    def assigned_permission(request, code, campus_id):
        """Use assigned grants, including global grants, without the superuser shortcut."""
        scope = (Q(tenant_id=request.scope["tenant_id"]) | Q(tenant__isnull=True)) & (
            Q(campus_id=campus_id) | Q(campus__isnull=True))
        direct = UserPermission.objects.filter(scope, user=request.user, permission__code=code,
                                               permission__is_active=True)
        if direct.filter(grant_type="DENY").exists():
            return False
        return (PermissionService.has_assigned_permission(
            request.user, code, tenant_id=request.scope["tenant_id"], campus_id=campus_id, exact_scope=True,
        ) or direct.filter(grant_type="ALLOW").exists() or UserRole.objects.filter(
            scope, user=request.user, is_active=True, role__is_active=True,
            role__role_permissions__permission__code=code,
            role__role_permissions__permission__is_active=True,
        ).exists())

    def monitoring_scope(self, request):
        tenant_id = request.scope["tenant_id"]
        self.monitoring_roles = list(UserRole.objects.filter(
            Q(tenant_id=tenant_id) | Q(tenant__isnull=True), user=request.user,
            is_active=True, role__is_active=True,
        ).exclude(role__code="FACULTY").select_related("role", "department"))
        codes = {row.role.code.upper() for row in self.monitoring_roles}
        compare = bool(codes & ({"CAO", "SUPER_ADMIN"} | Reference.DEAN_CODES))
        campuses, leadership, self.institution_campuses = [], set(), set()
        for campus in AdminScopeService.active_scoped_campuses(request).filter(tenant_id=tenant_id):
            if not compare and campus.id != request.scope.get("campus_id"):
                continue
            superadmin = any(row.role.code == "SUPER_ADMIN" and row.campus_id in (None, campus.id)
                             for row in self.monitoring_roles)
            explicit = request.user.is_superuser or superadmin
            permitted = (all(self.assigned_permission(request, code, campus.id)
                             for code in ("admin_portal.access", "grading_analytics.read"))
                         if explicit else campus_allowed(request, campus.id))
            if not permitted:
                continue
            campuses.append(campus)
            if superadmin:
                self.institution_campuses.add(campus.id)
            if (self.assigned_permission(request, LEADERSHIP_PERMISSION, campus.id) if explicit
                    else leadership_access(request, campus.id)):
                leadership.add(campus.id)
        return {"can_compare_campuses": compare}, campuses, leadership

    def faculty_scope(self, request):
        if not request.user.is_superuser:
            scoped = AdminScopeService._faculty_ids_by_monitoring_campus(request, include_all_campuses=True)
        else:
            # Reuse role hierarchy, not the shared helper's superuser bypass.
            scoped = {}
        for campus in self.campuses:
            if campus.id not in self.institution_campuses and not request.user.is_superuser:
                continue
            roles = [row for row in self.monitoring_roles if row.campus_id in (None, campus.id)]
            departments = None
            if campus.id not in self.institution_campuses:
                if not any(row.department_id is None for row in roles):
                    departments = ScopeService.expand_department_ids(
                        [row.department_id for row in roles if row.department.is_active
                         and row.department.tenant_id == request.scope["tenant_id"]
                         and row.department.campus_id == campus.id],
                        tenant_id=request.scope["tenant_id"], campus_id=campus.id,
                    )
                if roles and all(row.role.code == "COLLEGE_DEAN" for row in self.monitoring_roles):
                    departments = AdminScopeService._college_dean_area_chair_department_ids(
                        request, tenant_ids=[request.scope["tenant_id"]], campus_ids=[campus.id],
                        dean_department_ids=departments,
                    ) if departments is None or departments else []
            faculty = UserRole.objects.filter(
                Q(tenant_id=request.scope["tenant_id"]) | Q(
                    tenant__isnull=True, user__default_tenant_id=request.scope["tenant_id"]),
                Q(campus_id=campus.id) | Q(campus__isnull=True, user__default_campus_id=campus.id),
                is_active=True, role__is_active=True, role__code="FACULTY", user__is_active=True,
            )
            if departments is not None:
                faculty = faculty.filter(Q(department_id__in=departments) | Q(
                    department__isnull=True, user__default_campus_id=campus.id,
                    user__default_department_id__in=departments,
                ))
            scoped[campus.id] = list(faculty.values_list("user_id", flat=True).distinct())
        return scoped

    def __init__(self, request):
        super().__init__(request)
        self._contexts = {}
        self.normalize_programs()

    def normalize_programs(self):
        tenant_id = self.request.scope["tenant_id"]
        # Resolve optional/stale offering program values through the already
        # validated section program for this read-only analysis instance only.
        for assignment in self.assignments:
            offering = assignment.offering
            program = offering.program
            if (not program or not program.is_active or not program.department.is_active
                    or program.tenant_id != tenant_id or program.campus_id != offering.campus_id):
                offering.program_id = offering.section.program_id

    def refresh_offerings(self):
        """Hydrate academic objects after the opening digest without rebuilding RBAC.

        The closing, independent selection still revalidates assignments and grants.
        Clear resolved configuration so a reused selection never mixes generations.
        """
        related = [path.removeprefix("offering__") for path in self.assignment_related if path.startswith("offering__")]
        offerings = CourseOffering.objects.filter(pk__in=self.offerings).select_related(*related).in_bulk()
        if offerings.keys() != self.offerings.keys():
            raise RecordsChanged
        self.offerings = offerings
        for assignment in self.assignments:
            assignment.offering = self.offerings[assignment.offering_id]
        self._periods.clear()
        self._contexts.clear()
        self.normalize_programs()

    @staticmethod
    def configuration_key(offering):
        return (offering.course_id, offering.campus_id, offering.department_id,
                offering.program_id, offering.section.program_id, offering.term_id)

    def prepare_periods(self, offerings):
        """Resolve each official configuration once and batch its active periods."""
        templates, keys = {}, {}
        for offering in offerings:
            context_key = self.configuration_key(offering)
            if context_key in self._periods or context_key in keys:
                continue
            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
            except ValidationError:
                self._periods[context_key] = []
                continue
            templates[template.id] = template
            keys[context_key] = template.id
        periods = defaultdict(list)
        for period in GradingTemplatePeriod.objects.filter(template_id__in=templates, is_active=True).order_by("sequence_no", "id"):
            period.template = templates[period.template_id]
            periods[period.template_id].append(period)
        for context_key, template_id in keys.items():
            self._periods[context_key] = periods[template_id]

    def grading_context(self, offering, period):
        # Only template/profile/formula/transmutation facts are consumed below;
        # their resolution does not depend on the individual grading period.
        context_key = self.configuration_key(offering)
        if context_key not in self._contexts:
            self._contexts[context_key] = (
                Reference._grading_context(offering, period),
                str(FacultyGradingService.resolve_base_value(offering, period.template)),
            )
        return self._contexts[context_key]

    def assignment_source(self, request):
        """Same supervision chain; optional offering.program uses its valid section program."""
        tenant_id = request.scope["tenant_id"]
        supervised = Q(pk__in=[])
        for campus_id, faculty_ids in self.faculty_scope(request).items():
            supervised |= Q(offering__campus_id=campus_id, faculty_user_id__in=faculty_ids)
        source = FacultyAssignment.objects.filter(
            supervised, offering__tenant_id__in=AdminScopeService.active_scoped_tenants(request).values("id"),
            offering__term_id__in=AdminScopeService.active_scoped_terms(request).values("id"),
            offering__is_active=True, offering__department__is_active=True,
            offering__course__is_active=True, offering__section__is_active=True,
            offering__campus__tenant_id=tenant_id,
            offering__department__tenant_id=tenant_id,
            offering__department__campus_id=F("offering__campus_id"),
            offering__section__department__is_active=True,
            offering__section__department__tenant_id=tenant_id,
            offering__section__department__campus_id=F("offering__campus_id"),
            offering__section__program__is_active=True, offering__section__program__department__is_active=True,
            offering__section__program__department__tenant_id=tenant_id,
            offering__section__program__department__campus_id=F("offering__campus_id"),
            offering__section__tenant_id=tenant_id, offering__section__campus_id=F("offering__campus_id"),
            offering__section__program__tenant_id=tenant_id,
            offering__section__program__campus_id=F("offering__campus_id"),
        ).filter(
            Q(offering__program__isnull=True) | Q(
                offering__program__is_active=True, offering__program__department__is_active=True,
                offering__program__tenant_id=tenant_id, offering__program__campus_id=F("offering__campus_id"),
            ) | Q(offering__section__program__is_active=True,
                  offering__section__program__department__is_active=True,
                  offering__section__program__tenant_id=tenant_id,
                  offering__section__program__campus_id=F("offering__campus_id"))
        ).filter(Q(offering__course__department__isnull=True) | Q(offering__course__department__is_active=True))
        return AdminScopeService._visible_queryset(request, source)

    def options(self, selected):
        # Resolve inexpensive selectors only. No grade reads or report generation on GET.
        filters = dict(selected)
        options = {}

        def choose(name, values):
            options[name] = values
            if str(filters.get(name) or "") not in {str(row["value"]) for row in values}:
                filters[name] = "" if name == "course_code" else None

        choose("campus_id", [{"value": c.id, "label": c.name} for c in self.campuses])
        if not self.role["can_compare_campuses"] and self.campuses:
            filters["campus_id"] = self.campuses[0].id
        source = self.matching({"campus_id": filters["campus_id"]})
        years = {o.academic_year_id: o.academic_year.code for o in source}
        choose("academic_year_id", [{"value": pk, "label": label} for pk, label in sorted(years.items())])
        source = [o for o in source if o.academic_year_id == filters["academic_year_id"]]
        terms = {o.term_id: o.term.name for o in source}
        choose("term_id", [{"value": pk, "label": label} for pk, label in sorted(terms.items())])
        source = [o for o in source if o.term_id == filters["term_id"]]
        courses = {o.course.code: o.course.title for o in source}
        choose("course_code", [{"value": code, "label": f"{code} — {title}"} for code, title in sorted(courses.items())])
        choose("offering_id", [{"value": o.id, "label": f"{o.course.code} / {o.section.code} — {o.campus.code}"}
                               for o in source if filters["course_code"] and o.course.code == filters["course_code"]])
        return filters, options


def governance_reason(offering, period, active, catalog, submission, lock, blocked, now):
    """Read persisted authority only: no deadline advancement, auto-locking or window cleanup."""
    if not active or active.period.tenant_id != offering.tenant_id or active.period.term_id != offering.term_id:
        return "Official active grading period is unavailable."
    matches = [p for p in catalog if key(p) == key(period)]
    if len(matches) != 1:
        return "Official grading-period mapping is unavailable or ambiguous."
    logical = matches[0]
    earlier = logical.sequence_no < active.period.sequence_no
    # Final has no following period. Require explicit closure AND a completed submission.
    final_closed = (key(period) == "FINAL" and logical.id == active.period_id
                    and not any(p.sequence_no > logical.sequence_no for p in catalog)
                    and lock and lock.is_locked and lock.deadline_at and now > lock.deadline_at)
    if not earlier and not final_closed:
        return "Ongoing or future period; not concluded."
    if blocked or (submission and submission.status == GradeSubmission.Status.REOPENED):
        return "Reopen or correction window is active."
    if not submission or submission.status != GradeSubmission.Status.SUBMITTED or not submission.submitted_at:
        return "Concluded-period submission is not finalized."
    if submission.tenant_id != offering.tenant_id or submission.campus_id != offering.campus_id:
        return "Academic record scope is unavailable."
    return ""


def learning_fingerprint(selection, filters, now=None):
    """Extend the proven record digest with closure evidence and clock-boundary decisions."""
    now = now or timezone.now()
    ids = [o.id for o in selection.matching({k: filters[k] for k in ("academic_year_id", "term_id")})]
    tenant_id = selection.request.scope["tenant_id"]
    # Reject pre-threshold cached reports/tokens even when academic records are unchanged.
    payload = [fingerprint(selection, filters), "percentage-point-patterns-v2", str(MEANINGFUL_CHANGE),
               DISCLOSURE_POLICY_VERSION, COMPARABILITY_POLICY_VERSION, QUIZ_DISPLAY_POLICY_VERSION]
    # Saved records can outlive enrollment links. Their students' eligibility must
    # invalidate snapshots too, even when the shared enrollment digest omits them.
    payload.append(list(Student.objects.filter(pk__in=StudentPeriodGrade.objects.filter(
        offering_id__in=ids).values("student_id")).order_by("pk").values_list(
            "id", "tenant_id", "campus_id", "department_id", "program_id", "is_active")))
    for qs in (
        ActiveGradingPeriodSetting.objects.filter(tenant_id=tenant_id, term_id=filters["term_id"]),
        TenantTermGradingPeriod.objects.filter(tenant_id=tenant_id, term_id=filters["term_id"]),
        GradeSubmissionReopenRequest.objects.filter(offering_id__in=ids),
        GradeCorrectionUnlockWindow.objects.filter(offering_id__in=ids),
    ):
        payload.append([qs.model._meta.label_lower, list(qs.order_by("pk").values())])
    windows = GradeCorrectionUnlockWindow.objects.filter(offering_id__in=ids).select_related("correction_request")
    payload.append([(w.pk, w.start_at <= now <= w.end_at, w.correction_request.status) for w in windows.order_by("pk")])
    reopens = GradeSubmissionReopenRequest.objects.filter(offering_id__in=ids, status="APPROVED").order_by("pk")
    payload.append([(r.pk, GradingGovernanceService.is_reopen_request_window_active(r, at=now)) for r in reopens])
    locks = GradingPeriodLock.objects.filter(tenant_id=tenant_id, term_id=filters["term_id"], is_active=True).order_by("pk")
    payload.append([(row.pk, bool(row.deadline_at and now > row.deadline_at)) for row in locks])
    return salted_hmac("learning-insights-generation-v1", encoded(payload), algorithm="sha256").hexdigest()


def score_percentage(score, activity):
    mode = FacultyGradingService.resolve_score_input_mode(
        template_component=activity.template_component, template_subcomponent=activity.template_subcomponent,
        template_detail=activity.template_detail,
    )
    value = score.raw_score
    if value is None or not value.is_finite():
        return None
    if mode == "DIRECT_PERCENTAGE":
        return Fraction(value) if 0 <= value <= 100 else None
    if mode == "RAW_BASE50" and activity.total_score > 0 and 0 <= value <= activity.total_score:
        return Fraction(value) * 100 / Fraction(activity.total_score)
    return None


def activity_population(activities, scores, cohort):
    """Membership evidence shared by numeric analysis and the full privacy basis."""
    recorded, quizzes = [], []
    for activity in activities:
        evidence = scores.get(activity.id, {})
        if not any(student_id in cohort and value is not None for student_id, value in evidence.items()):
            continue
        recorded.append(activity)
        if FacultyGradingService.is_quiz_activity(activity):
            quizzes.append(activity)
    quiz_cohort = {sid for sid in cohort if quizzes and all(scores[a.id].get(sid) is not None for a in quizzes)}
    return recorded, quizzes, quiz_cohort


def unsupported_mixed_quiz_rollup(quizzes, components):
    """A mixed weighted detail category has no supported standalone quiz rollup.

    Use the same configured-code classification as is_quiz_activity, including
    quiz-only parents whose details are individual quiz labels (Q1, Q2, etc.).
    Inspect prefetched definitions only; missing project scores do not make its
    configured weight disappear or turn the remaining category into quizzes.
    """
    sub_ids = {activity.template_subcomponent_id for activity in quizzes}
    is_quiz_code = FacultyGradingService._is_quiz_template_code
    for component in components:
        for sub in component.subcomponents.all():
            if not sub.is_active or sub.pk not in sub_ids or sub.detail_computation_mode != "WEIGHTED_DETAILS":
                continue
            parent_is_quiz = is_quiz_code(component.code) or is_quiz_code(sub.code)
            detail_kinds = {parent_is_quiz or is_quiz_code(detail.code)
                            for detail in sub.details.all() if detail.is_active}
            if detail_kinds == {True, False}:
                return True
    return False


def gradebook_quiz_values(quizzes, grade_scores, cohort, components):
    """Reuse Gradebook rounding/rollups with already loaded definitions/scores."""
    if unsupported_mixed_quiz_rollup(quizzes, components):
        return {}
    grouped = defaultdict(list)
    for activity in quizzes:
        grouped[(activity.template_component_id, activity.template_subcomponent_id)].append(activity)
    plans, blocks = [], []
    for component in components:
        sections, section_plans = [], []
        subcomponents = {sub.pk: sub for sub in component.subcomponents.all() if sub.is_active}
        for (component_id, sub_id), rows in grouped.items():
            if component_id != component.pk:
                continue
            sub = subcomponents.get(sub_id)
            details = [detail for detail in sub.details.all() if detail.is_active] if sub else []
            if (component.weight_percentage < 0 or (sub_id is None and subcomponents)
                or (sub and (sub.weight_percentage < 0 or sub.detail_computation_mode not in {"WEIGHTED_DETAILS", "AVERAGE_ACTIVITIES"}))
                or any(detail.weight_percentage < 0 for detail in details)):
                return {}
            sections.append({"color_class": "summary-group-quizzes", "weight_percentage": sub.weight_percentage if sub else Decimal(100)})
            section_plans.append((sub, details, rows))
        if sections:
            blocks.append({"weight_percentage": component.weight_percentage, "sections": sections})
            plans.append(section_plans)
    layout = {"class_standing_blocks": blocks}
    result = {}
    for sid in cohort:
        if not quizzes or not all(grade_scores.get(a.id, {}).get(sid) is not None for a in quizzes):
            continue
        values = []
        for section_plans in plans:
            sections = []
            for sub, details, rows in section_plans:
                if details and (sub.detail_computation_mode != "AVERAGE_ACTIVITIES" or any(a.template_detail_id for a in rows)):
                    detail_scores = []
                    for detail in details:
                        observations = [grade_scores[a.id][sid] for a in rows if a.template_detail_id == detail.pk]
                        detail_scores.append((detail.weight_percentage, observations if sub.detail_computation_mode == "AVERAGE_ACTIVITIES"
                                              else PeriodSummaryLayoutService.average(observations)))
                    value = FacultyGradingService.aggregate_detail_scores(subcomponent=sub, detail_scores=detail_scores)
                else:
                    value = PeriodSummaryLayoutService.average([grade_scores[a.id][sid] for a in rows])
                sections.append({"average": value})
            values.append({"sections": sections})
        value = PeriodSummaryLayoutService.print_group_average(layout, {"class_standing_blocks": values}, quizzes=True)
        if value is not None and value.is_finite() and 0 <= value <= 100:
            result[sid] = value
    return result


def activity_values(activities, scores, cohort, complete_cohort=None, grade_scores=None, components=()):
    recorded, quizzes, quiz_cohort = activity_population(activities, scores, cohort)
    unsupported = unsupported_mixed_quiz_rollup(quizzes, components)
    if unsupported:
        quiz_cohort = set()
    quiz_values = {}
    if quizzes:
        for student_id in quiz_cohort:
            observations = [scores[a.id].get(student_id) for a in quizzes]
            quiz_values[student_id] = exact_average(observations)
    complete_cohort = set(complete_cohort if complete_cohort is not None else cohort)
    quiz_exact = (exact_average([quiz_values[sid] for sid in complete_cohort])
                  if quizzes and complete_cohort and complete_cohort.issubset(quiz_values) else None)
    # Gradebook summaries use saved converted activity scores and round each
    # student's quiz mean before the class mean. Never convert that score again.
    grade_scores = grade_scores or {}
    quiz_grades = gradebook_quiz_values(quizzes, grade_scores, quiz_cohort, components)
    quiz_grade = (PeriodSummaryLayoutService.average([quiz_grades[sid] for sid in complete_cohort])
                  if complete_cohort and complete_cohort.issubset(quiz_grades) else None)
    return {"quizzes": len(quizzes) if recorded else None, "activities": len(recorded) if recorded else None,
            "quiz": displayed(quiz_exact), "quiz_grade": quiz_grade,
            "comparison_values": {"quiz": quiz_exact, "quiz_grade": exact_average([quiz_grades[sid] for sid in complete_cohort]) if quiz_grade is not None else None},
            "quiz_unsupported": unsupported,
            "quiz_unavailable_reason": "Quiz averages unavailable: unsupported mixed quiz/non-quiz weighted detail category." if unsupported else "",
            "quiz_values": quiz_values, "quiz_grades": quiz_grades}


def scale_definitions(period):
    """Only a single major bucket has a directly established 0–100 grade scale.

    Multiple buckets are stored as sums by the official calculator. Keep those
    original grade points; do not invent weighting or percentage normalization.
    """
    components = list(period.components.filter(is_active=True).prefetch_related("subcomponents__details"))
    exam = [c for c in components if c.is_exam_component]
    standing = [c for c in components if not c.is_exam_component]
    modes = {"RAW_BASE50", "DIRECT_PERCENTAGE"}
    def known(rows):
        return len(rows) == 1 and rows[0].score_input_mode in modes and all(
            s.weight_percentage >= 0 and s.detail_computation_mode in {"WEIGHTED_DETAILS", "AVERAGE_ACTIVITIES"}
            and (s.score_input_mode in modes | {"INHERIT"}) and all(
                d.weight_percentage >= 0 and d.score_input_mode in modes | {"INHERIT"} for d in s.details.all() if d.is_active
            ) for s in rows[0].subcomponents.all() if s.is_active
        )
    return {"standing": known(standing), "exam": known(exam), "periodic": False}, components


def comparable_component_signature(period):
    """Ignore only the owning logical period's recognized code prefix.

    Retain every semantic code suffix, weight, effective scoring mode, examination/
    attendance flag and nested computation rule from the shared signature. That
    shared signature and stored template codes remain unchanged for other reports.
    """
    prefixes = {
        "PRELIM": ("PRELIM",), "MIDTERM": ("MIDTERM", "MID_TERM", "MID-TERM"),
        "PREFINAL": ("PREFINAL", "PRE_FINAL", "PRE-FINAL"), "FINAL": ("FINAL",),
    }.get(key(period), ())
    ambiguous = False

    def identity(code):
        for prefix in prefixes:
            marker = prefix + "_"
            if code.upper().startswith(marker) and len(code) > len(marker):
                return code[len(marker):]
        return code

    def normalize(rows, inherited_mode=None):
        nonlocal ambiguous
        result = []
        for row in rows:
            values = list(row)
            values[0] = identity(row[0])
            # INHERIT and an explicit identical override resolve to the same
            # Gradebook calculation. Unknown modes remain distinct/fail closed.
            if values[2] == "INHERIT" and inherited_mode in {"RAW_BASE50", "DIRECT_PERCENTAGE"}:
                values[2] = inherited_mode
            if isinstance(row[-1], tuple):
                values[-1] = normalize(row[-1], values[2])
            result.append(tuple(values))
        if len({row[0] for row in result}) != len(result):
            ambiguous = True
        return tuple(sorted(result, key=lambda row: row[0]))

    signature = normalize(component_signature(period))
    # Colliding normalized identities cannot establish cross-period equivalence.
    return ("ambiguous-component-identities", period.pk, signature) if ambiguous else signature


def period_comparison(history, classification):
    """Describe already released period means; never create another population."""
    concluded = [row for row in history if row.get("concluded")]
    by_period = {row["period"]: row for row in concluded}
    pair = [by_period[name] for name in (classification.get("from"), classification.get("to")) if name in by_period]
    if len(pair) != 2:
        pair = concluded[-2:]
    previous, current = (pair if len(pair) == 2 else ({}, pair[-1] if pair else {}))
    compatible = bool(previous and current and previous["signature"] == current["signature"])

    def values(metric, label, count=False):
        left, right = previous.get(metric), current.get(metric)
        comparable = (count or compatible) and all(
            metric in {"quizzes", "quiz", "quiz_grade"} or row.get("scales", {}).get(metric)
            for row in (previous, current))
        change = None
        if left is not None and right is not None and comparable:
            first = previous.get("comparison_values", {}).get(metric, left)
            last = current.get("comparison_values", {}).get(metric, right)
            if first is not None and last is not None:
                delta = displayed(Fraction(last) - Fraction(first))
                change = (f"{int(delta):+d}" if count else f"{delta:+.2f}") if delta else ("0" if count else "0.00")
        def display(value):
            return None if value is None else str(value) if count else f"{value:.2f}"
        return {"indicator": label, "previous": display(left), "current": display(right), "change": change}

    return {"previous": previous.get("period", "Earlier period"), "current": current.get("period", "Later period"),
            "rows": [values(metric, label, metric == "quizzes") for metric, label in (
                ("quizzes", "Quiz Count"), ("quiz_grade", "Quiz Average (Gradebook points)"),
                ("standing", "Class Standing"), ("exam", "Examination Average"), ("periodic", "Periodic Average"))],
            "raw": values("quiz", "Raw Quiz Percentage (%)"),
            "note": ("Two concluded periods are required to show changes." if len(pair) < 2 else
                     "Changes in grade averages are unavailable because grading configurations are incompatible." if not compatible else
                     "Change is the later period average minus the earlier average, calculated before display rounding."),
            "basis": "Descriptive period averages use each period's available class data. Pattern classification separately requires matched students and uses raw quiz percentages."}


def classify(history, minimum_matched_students=1, disclosed_groups=None):
    """Classify the latest comparable pair using one internally matched population."""
    metrics = ("quiz", "standing", "exam")
    usable = [r for r in history if r.get("eligible")]
    reasons = {
        "concluded_periods": "Two concluded periods with valid finalized results are required.",
        "finalized_records": "Insufficient finalized student records across concluded periods.",
        "grading_configuration": "Grading configurations are incompatible across concluded periods.",
        "unsupported_quiz": "Unsupported mixed quiz scoring configuration; quiz comparison is unavailable.",
        "missing_measurements": "Required quiz, examination or class-standing measurements are unavailable.",
        "matched_records": "Insufficient matched finalized student records with all required measurements.",
        "privacy_minimum": "Comparison withheld under the applicable minimum-student privacy rule.",
        "privacy_overlap": "Comparison withheld under privacy rules for overlapping or complementary student groups.",
        "unsupported_scale": "Unsupported scoring configuration; comparable percentage-based component scales are unavailable.",
    }
    failure = None
    def reject(code):
        nonlocal failure
        # Preserve the actual reason for the most recent attempted pair, rather
        # than overwrite it with an older pair's unrelated failure.
        if failure is None:
            failure = code
    for current in reversed(usable):
        for previous in reversed([r for r in usable if r["sequence"] < current["sequence"]]):
            if current["signature"] != previous["signature"]:
                reject("grading_configuration")
                continue
            if any(row.get("quiz_unsupported") for row in (previous, current)):
                reject("unsupported_quiz")
                continue
            if any(not row.get("scales", {}).get(m) for row in (previous, current) for m in ("standing", "exam")):
                reject("unsupported_scale")
                continue
            current_values = current.get("pattern_values", current.get("student_values"))
            previous_values = previous.get("pattern_values", previous.get("student_values"))
            if current_values is not None and previous_values is not None:
                matched = set(current_values) & set(previous_values)
                matched = {sid for sid in matched if all(
                    current_values[sid].get(metric) is not None
                    and previous_values[sid].get(metric) is not None
                    for metric in metrics
                )}
                if len(matched) < minimum_matched_students:
                    missing = any(not row.get("pattern_values") and "contributors" in row
                                  and any(not row["contributors"].get(m) for m in metrics)
                                  for row in (previous, current))
                    reject("missing_measurements" if missing else "privacy_minimum" if matched else "matched_records")
                    continue
                if disclosed_groups is not None and frozenset(matched) not in disclosed_groups:
                    reject("privacy_overlap")
                    continue
                values = {
                    metric: (exact_average([previous_values[sid][metric] for sid in matched]),
                             exact_average([current_values[sid][metric] for sid in matched]))
                    for metric in metrics
                }
                if any(v is None or v < 0 or v > 100 for pair in values.values() for v in pair):
                    reject("unsupported_scale")
                    continue
            else:
                # Retain compatibility for pure classifier callers; production rows
                # always carry student_values and take the matched-population path.
                if current.get("cohort") != previous.get("cohort"):
                    reject("matched_records")
                    continue
                values = {m: (previous.get("comparison_values", previous).get(m),
                              current.get("comparison_values", current).get(m)) for m in metrics}
                if any(v is None or v < 0 or v > 100 for pair in values.values() for v in pair):
                    reject("missing_measurements")
                    continue
                matched = set(current.get("cohort", ()))
            changes = {m: performance_change(*values[m]) for m in metrics}
            directions = [changes[m] for m in metrics]
            if directions[0] == directions[1] == directions[2] and directions[0] != "STABLE":
                category = PATTERNS[0]
                direction = "Improved together" if directions[0] == "IMPROVING" else "Declined together"
            elif directions[0] == directions[1] and {directions[0], directions[2]} == {"IMPROVING", "DECLINING"}:
                category, direction = PATTERNS[1], "Quiz and class standing moved opposite to examination results"
            else:
                category, direction = PATTERNS[2], "Mixed directions or stable results"
            return {"category": category, "detail": direction, "from": previous["period"], "to": current["period"],
                    "changes": changes, "quiz_count_change": current["quizzes"] - previous["quizzes"],
                    "activity_count_change": current["activities"] - previous["activities"],
                    "matched_students": len(matched)}
    if failure is None:
        failure = ("privacy_minimum" if any(row.get("privacy_withheld") and 0 < len(row.get("cohort", ())) < minimum_matched_students for row in history) else
                   "privacy_overlap" if any(row.get("privacy_withheld") for row in history) else
                   "finalized_records" if sum(bool(row.get("concluded")) for row in history) >= 2 else "concluded_periods")
    return {"category": INSUFFICIENT, "detail": reasons[failure], "reason_code": failure, "matched_students": None}


def disclosed_populations(histories, active_enrolled, leadership_ids):
    """Plan all component and potential paired releases before optional filters.

    Membership atoms protect intersections and excluded complements, including
    full active-roster aggregates available through related authorized reports.
    A small group that is never releasable adds no equation to the disclosure
    basis; an authorized leadership release does, even when that group is small.
    """
    groups = set()

    def include(offering_id, population):
        population = frozenset(population)
        if population and (len(population) >= MIN_STUDENTS or offering_id in leadership_ids):
            groups.add(population)

    for pk, history in histories.items():
        include(pk, active_enrolled[pk])
        rows = [row for row in history if row["eligible"]]
        for row in rows:
            include(pk, row["cohort"])
            for contributors in row["contributors"].values():
                include(pk, contributors)
        for previous, current in combinations(rows, 2):
            if previous["signature"] == current["signature"] and all(
                row["scales"].get(metric) for row in (previous, current) for metric in ("standing", "exam")
            ):
                include(pk, set(previous["pattern_values"]) & set(current["pattern_values"]))
        # There are at most four logical periods. Include every possible line
        # population, so withholding a period cannot create an unplanned subset.
        for size in range(1, len(rows) + 1):
            for subset in combinations(rows, size):
                if len({row["signature"] for row in subset}) == 1:
                    include(pk, set.intersection(*(set(row["student_values"]) for row in subset)))

    memberships = defaultdict(set)
    for population in groups:
        for sid in population:
            memberships[sid].add(population)
    atoms = Counter(frozenset(member) for member in memberships.values())
    hidden = {population for member, count in atoms.items() if count < MIN_STUDENTS for population in member}
    # Withholding only the larger population is insufficient: that aggregate may
    # already be known from another authorized request. Withhold its connected
    # overlap family, including subsets that exclude the protected small atom.
    pending = list(hidden)
    while pending:
        for sid in pending.pop():
            for population in memberships[sid] - hidden:
                hidden.add(population)
                pending.append(population)
    return {population for population in groups - hidden if len(population) >= MIN_STUDENTS}


def student_eligible(student):
    return (student.is_active and student.department.is_active
            and (not student.program_id or student.program.is_active))


def privacy_history_row(finalized, scales, signature, period_authorized, activities, scores, enrolled, active_enrolled, base_value, grade_scores, components):
    """Keep actual contributor/matched sets without calculating unrelated averages.

    These private rows are used only by disclosed_populations, never by charts,
    classifications, public results or snapshots. Eligibility and scale checks
    match the numeric path, including historical and partially finalized records.
    """
    _, quizzes, quiz_cohort = activity_population(activities, scores, enrolled) if period_authorized else ([], [], set())
    if unsupported_mixed_quiz_rollup(quizzes, components):
        quiz_cohort = set()
    scales = dict(scales)
    for metric in ("standing", "exam"):
        if any(metric in values and not 0 <= values[metric] <= 100 for values in finalized.values()):
            scales[metric] = False
    if not 0 <= Decimal(base_value) <= 100:
        scales = dict.fromkeys(scales, False)
    contributors = {metric: {sid for sid, values in finalized.items() if metric in values}
                    for metric in ("standing", "exam", "periodic")}
    contributors["quiz"] = set(active_enrolled) if quizzes and active_enrolled and active_enrolled <= quiz_cohort else set()
    grade_cohort = {sid for sid in quiz_cohort if all(grade_scores.get(a.id, {}).get(sid) is not None for a in quizzes)}
    contributors["quiz_grade"] = set(active_enrolled) if active_enrolled and active_enrolled <= grade_cohort else set()
    return {"eligible": period_authorized and bool(finalized), "cohort": set(finalized),
            "contributors": contributors, "signature": signature, "scales": scales,
            "pattern_values": {sid for sid, values in finalized.items() if sid in quiz_cohort
                               and all(metric in values for metric in ("standing", "exam"))},
            "student_values": {sid for sid, values in finalized.items() if sid in quiz_cohort and len(values) == 3}}


def build_report(selection, filters, now=None):
    now = now or timezone.now()
    # Membership disclosure is planned across the authorized year/term before optional filters.
    universe = selection.matching({k: filters[k] for k in ("academic_year_id", "term_id")})
    chosen = [o for o in selection.matching(filters) if not filters.get("offering_id") or o.id == filters["offering_id"]]
    numeric_ids = {o.id for o in chosen}
    if filters.get("course_code"):
        numeric_ids.update(o.id for o in selection.matching({**filters, "offering_id": None}))
    selection.prepare_periods(universe)
    ids = [o.id for o in universe]
    offerings = {o.id: o for o in universe}
    tenant_id = selection.request.scope["tenant_id"]
    enrolled = defaultdict(set)
    active_enrolled = defaultdict(set)
    invalid = set()
    # Keep scoped historical roster links too: a dropped/transferred student may
    # have valid finalized evidence in both periods even when no longer active.
    enrollment_rows = Enrollment.objects.filter(course_offering_id__in=ids).select_related(
        "student__department", "student__program")
    for e in enrollment_rows:
        o = offerings[e.course_offering_id]
        if (e.tenant_id, e.campus_id, e.academic_year_id, e.term_id, e.student.tenant_id, e.student.campus_id) != (
            o.tenant_id, o.campus_id, o.academic_year_id, o.term_id, o.tenant_id, o.campus_id
        ):
            invalid.add(o.id)
        elif student_eligible(e.student):
            enrolled[o.id].add(e.student_id)
            if e.is_active and e.enrollment_status not in Enrollment.NON_ACTIVE_GRADING_STATUSES:
                active_enrolled[o.id].add(e.student_id)
    grades = defaultdict(dict)
    for g in StudentPeriodGrade.objects.filter(offering_id__in=ids).select_related("student__department", "student__program"):
        o = offerings[g.offering_id]
        if (g.tenant_id, g.campus_id, g.student.tenant_id, g.student.campus_id) != (o.tenant_id, o.campus_id, o.tenant_id, o.campus_id):
            invalid.add(o.id)
        elif not student_eligible(g.student):
            continue
        elif g.student_id in enrolled[o.id]:
            grades[(o.id, g.template_period_id)][g.student_id] = g
        else:
            invalid.add(o.id)
    submissions = {(s.offering_id, s.template_period_id): s for s in GradeSubmission.objects.filter(offering_id__in=ids)}
    active = {a.campus_id: a for a in ActiveGradingPeriodSetting.objects.filter(
        tenant_id=tenant_id, term_id=filters["term_id"], is_active=True, period__is_active=True,
    ).select_related("period")}
    catalog = list(TenantTermGradingPeriod.objects.filter(tenant_id=tenant_id, term_id=filters["term_id"], is_active=True))
    # Same course-over-campus precedence and newest-lock ordering as resolve_lock.
    locks = {}
    for lock in GradingPeriodLock.objects.filter(
        tenant_id=tenant_id, academic_year_id=filters["academic_year_id"], term_id=filters["term_id"],
        campus_id__in={o.campus_id for o in universe}, is_active=True,
    ).order_by("-updated_at"):
        lock_key = GradingGovernanceService._normalize_period_key(lock.period_code)
        if lock.scope_type == GradingPeriodLock.ScopeType.COURSE:
            locks.setdefault((lock.campus_id, lock_key, lock.course_offering_id), lock)
        elif lock.scope_type == GradingPeriodLock.ScopeType.CAMPUS and lock.course_offering_id is None:
            locks.setdefault((lock.campus_id, lock_key, None), lock)
    blocked = set(GradeCorrectionUnlockWindow.objects.filter(
        offering_id__in=ids, is_active=True, is_consumed=False, start_at__lte=now, end_at__gte=now,
        correction_request__status="APPROVED",
    ).values_list("offering_id", "template_period_id"))
    for r in GradeSubmissionReopenRequest.objects.filter(offering_id__in=ids, status="APPROVED"):
        s = submissions.get((r.offering_id, r.template_period_id))
        if GradingGovernanceService.is_reopen_request_window_active(r, at=now) and (
            not s or not s.submitted_at or not r.reviewed_at or r.reviewed_at >= s.submitted_at
        ):
            blocked.add((r.offering_id, r.template_period_id))
    activities = defaultdict(list)
    valid_activities = {}
    for a in GradeActivity.objects.filter(offering_id__in=ids, is_active=True,
        template_component__is_active=True).select_related("template_component", "template_subcomponent", "template_detail"):
        o = offerings[a.offering_id]
        if ((a.tenant_id, a.campus_id) != (o.tenant_id, o.campus_id)
            or a.template_component.template_period_id != a.template_period_id
            or (a.template_subcomponent_id and (not a.template_subcomponent.is_active or a.template_subcomponent.template_component_id != a.template_component_id))
            or (a.template_detail_id and (not a.template_detail.is_active or a.template_detail.template_subcomponent_id != a.template_subcomponent_id))
            or a.template_component.is_exam_component):
            continue
        # Attendance is not a GradeActivity count and exam-tagged quizzes are never class standing.
        if a.template_subcomponent_id and a.template_subcomponent.is_attendance_component:
            continue
        valid_activities[a.id] = a
        activities[(a.offering_id, a.template_period_id)].append(a)
    scores = defaultdict(dict)
    grade_scores = defaultdict(dict)
    for s in StudentActivityScore.objects.filter(activity_id__in=valid_activities, is_active=True, is_excused=False):
        a = valid_activities[s.activity_id]
        if s.student_id in enrolled[a.offering_id]:
            value = score_percentage(s, a)
            scores[a.id][s.student_id] = value
            # The actual configured mode/base was applied by Gradebook when
            # saving computed_score. Read that authoritative rounded value;
            # unknown modes, invalid raw evidence and invalid grades stay absent.
            if value is not None and s.computed_score is not None and s.computed_score.is_finite() and 0 <= s.computed_score <= 100:
                grade_scores[a.id][s.student_id] = s.computed_score
    histories, structures = {}, {}
    for o in universe:
        history = []
        counts = Counter(key(p) for p in selection.periods(o))
        for p in selection.periods(o):
            if key(p) not in dict(PERIODS):
                continue
            pair = (o.id, p.id)
            lock_key = GradingGovernanceService._normalize_period_key(p.code or p.name)
            lock = locks.get((o.campus_id, lock_key, o.id)) or locks.get((o.campus_id, lock_key, None))
            reason = governance_reason(o, p, active.get(o.campus_id), catalog, submissions.get(pair), lock, pair in blocked, now)
            if counts[key(p)] != 1:
                reason = "Grading-period mapping is ambiguous."
            saved = grades[pair]
            if o.id in invalid:
                reason = "Academic record scope is unavailable."
            period_authorized = not reason
            if p.id not in structures:
                scales, components = scale_definitions(p)
                structures[p.id] = (scales, components, comparable_component_signature(p))
            scales, components, structure = structures[p.id]
            context, base_value = selection.grading_context(o, p)
            signature = (o.course_id, context["template_id"], context["profile_id"], context["formula_mode"],
                         tuple(context["signature"][3]), base_value, structure)
            finalized = {}
            if not reason:
                for sid in enrolled[o.id]:
                    grade = saved.get(sid)
                    if not grade or not grade.is_finalized:
                        continue
                    values = {name: getattr(grade, field) for name, field in (
                        ("standing", "class_standing_grade"), ("exam", "exam_grade"), ("periodic", "period_grade"),
                    )}
                    values = {name: value for name, value in values.items()
                              if value is not None and value.is_finite() and value >= 0}
                    if not values:
                        continue
                    finalized[sid] = values
                if not finalized:
                    reason = "No valid finalized student results are available for this concluded period."
                elif any(sid not in finalized or len(finalized[sid]) < 3 for sid in active_enrolled[o.id]):
                    reason = "Partial finalized component results; missing values are excluded and remain unavailable."
            if o.id not in numeric_ids:
                history.append(privacy_history_row(
                    finalized, scales, signature, period_authorized, activities[pair], scores,
                    enrolled[o.id], active_enrolled[o.id], base_value, grade_scores, components,
                ))
                continue
            activity_data = activity_values(activities[pair], scores, enrolled[o.id], active_enrolled[o.id], grade_scores, components) if period_authorized else {
                "quizzes": None, "activities": None, "quiz": None, "quiz_grade": None,
                "comparison_values": {"quiz": None, "quiz_grade": None}, "quiz_values": {}, "quiz_grades": {},
            }
            student_values, pattern_values = {}, {}
            for sid, values in finalized.items():
                if sid in activity_data["quiz_values"]:
                    student_components = {**values, "quiz": activity_data["quiz_values"][sid],
                                          "quiz_grade": activity_data["quiz_grades"].get(sid)}
                    if all(student_components.get(metric) is not None for metric in ("quiz", "standing", "exam")):
                        pattern_values[sid] = {metric: student_components[metric] for metric in ("quiz", "standing", "exam")}
                    if all(student_components.get(metric) is not None for metric in ("quiz", "standing", "exam", "periodic")):
                        student_values[sid] = student_components
            row = {"period": dict(PERIODS)[key(p)], "code": key(p), "export_code": p.code, "sequence": dict((k, n) for n, (k, _) in enumerate(PERIODS))[key(p)],
                   "concluded": period_authorized, "eligible": period_authorized and bool(finalized), "reason": reason,
                   "cohort": set(finalized), "signature": signature,
                   "contributors": {metric: {sid for sid, values in finalized.items() if metric in values}
                                    for metric in ("standing", "exam", "periodic")},
                   "scales": dict(scales), "student_values": student_values, "pattern_values": pattern_values,
                   "quiz_unsupported": activity_data.get("quiz_unsupported", False),
                   **{m: None for m in METRICS}, "quizzes": activity_data["quizzes"], "activities": activity_data["activities"]}
            row["quiz"] = activity_data["quiz"]
            row["quiz_grade"] = activity_data["quiz_grade"]
            row["contributors"]["quiz"] = set(active_enrolled[o.id]) if row["quiz"] is not None else set()
            row["contributors"]["quiz_grade"] = set(active_enrolled[o.id]) if row["quiz_grade"] is not None else set()
            row["comparison_values"] = dict(activity_data["comparison_values"])
            if finalized:
                for name in ("standing", "exam", "periodic"):
                    component_values = [values[name] for values in finalized.values() if name in values]
                    exact = exact_average(component_values)
                    row[name] = displayed(exact)
                    row["comparison_values"][name] = exact
                    if name in ("standing", "exam") and any(not 0 <= value <= 100 for value in component_values):
                        row["scales"][name] = False
                periodic_values = [values["periodic"] for values in finalized.values() if "periodic" in values]
                row["scales"]["periodic"] = (bool(periodic_values) and all(0 <= value <= 100 for value in periodic_values)
                    and sum(c.weight_percentage for c in components) == 100 and all(0 <= c.weight_percentage <= 100 for c in components)
                    and all(row["scales"][name] for name in ("standing", "exam"))
                    and all(0 <= Decimal(band[2]) <= 100 for band in context["signature"][3]))
                if not 0 <= Decimal(base_value) <= 100:
                    row["scales"] = dict.fromkeys(row["scales"], False)
                if row["quiz"] is None and not row["reason"]:
                    row["reason"] = (activity_data.get("quiz_unavailable_reason")
                                     or "Quiz average unavailable: no students have valid non-excused scores for every recorded quiz.")
            history.append(row)
        histories[o.id] = sorted(history, key=lambda r: r["sequence"]) if o.id in numeric_ids else history
    leadership_ids = {pk for pk, offering in offerings.items() if selection.allows_aggregates(offering)}
    disclosed_groups = disclosed_populations(histories, active_enrolled, leadership_ids)
    for pk, history in histories.items():
        if pk in leadership_ids or pk not in numeric_ids:
            continue
        for r in history:
            withheld = False
            for metric, contributors in r["contributors"].items():
                if r[metric] is not None and frozenset(contributors) not in disclosed_groups:
                    r[metric] = None
                    r["comparison_values"][metric] = None
                    withheld = True
            if r["eligible"] and not any(r[metric] is not None for metric in ("quiz", "standing", "exam", "periodic")):
                r.update(eligible=False, reason="Class results withheld by aggregate privacy rules.")
                r.update({m: None for m in METRICS})
            elif withheld:
                r["reason"] = "Some component results are withheld by aggregate privacy rules."
            r["privacy_withheld"] = withheld
    classifications = []
    for offering in chosen:
        minimum = 1 if selection.allows_aggregates(offering) else MIN_STUDENTS
        classifications.append({"id": offering.id, "course": offering.course.code, "section": offering.section.code,
                                "campus": offering.campus.code,
                                **classify(histories[offering.id], minimum_matched_students=minimum,
                                           disclosed_groups=None if offering.id in leadership_ids else disclosed_groups)})
    pattern_counts = Counter(c["category"] for c in classifications if c["category"] in PATTERNS)
    denominator = sum(pattern_counts.values())
    patterns = [{"label": label, "count": pattern_counts[label],
                 "percentage": average([Decimal(pattern_counts[label]) * 100 / denominator]) if denominator else None} for label in PATTERNS]
    rejection_counts = Counter(c["detail"] for c in classifications if c["category"] == INSUFFICIENT)
    pattern_reasons = [{"detail": detail, "count": count} for detail, count in rejection_counts.items()]
    overall = []
    for code, label in PERIODS:
        rows = [r for o in chosen for r in histories[o.id] if r["code"] == code and r["eligible"]]
        if not rows:
            continue
        compatible = len({r["signature"] for r in rows}) == 1
        entry = {"period": label, "code": code, "sections": len(rows), "eligible": True, "reason": "",
                 "scales": {name: compatible and all(r["scales"][name] for r in rows) for name in ("standing", "exam", "periodic")}}
        for metric in METRICS:
            values = [r[metric] for r in rows if r[metric] is not None]
            entry[metric] = ((PeriodSummaryLayoutService.average(values) if metric == "quiz_grade" else average(values))
                             if compatible or metric in ("quizzes", "activities") else None)
            entry[metric + "_sections"] = len(values) if entry[metric] is not None else 0
        if not compatible:
            entry["reason"] = "Choose one compatible course group for score averages. Counts and within-section patterns remain available."
        entry["cohort_basis"] = frozenset((o.id, tuple(sorted(r["cohort"]))) for o in chosen for r in histories[o.id] if r["code"] == code and r["eligible"])
        entry["quiz_grade_basis"] = frozenset(o.id for o in chosen for r in histories[o.id]
                                             if r["code"] == code and r["eligible"] and r["quiz_grade"] is not None)
        entry["signature"] = rows[0]["signature"] if compatible else None
        overall.append(entry)
    selected_id = filters.get("offering_id")
    selected_course = bool(filters.get("course_code"))
    table = histories.get(selected_id, []) if selected_id else overall
    comparison = period_comparison(table, classifications[0]) if selected_id and classifications else None
    line, line_note = [], "Choose one course and compatible grading definitions for matched progression."
    line_mode = "matched"
    if selected_id:
        line_rows = [r for r in table if r["eligible"]]
        minimum = 1 if selection.allows_aggregates(selection.offerings[selected_id]) else MIN_STUDENTS
        compatible = bool(line_rows) and len({r["signature"] for r in line_rows}) == 1
        paired = set.intersection(*(set(r["student_values"]) for r in line_rows)) if compatible else set()
        if len(paired) >= minimum and (selected_id in leadership_ids or frozenset(paired) in disclosed_groups):
            by_code = {}
            for row in line_rows:
                paired_values = {metric: exact_average([row["student_values"][sid][metric] for sid in paired])
                                 for metric in ("quiz", "standing", "exam", "periodic")}
                item = {"period": row["period"], **{metric: displayed(value) if value is not None and
                    (metric == "quiz" or row["scales"].get(metric)) else None
                    for metric, value in paired_values.items()}, "matched_students": len(paired)}
                item["quiz_grade"] = (PeriodSummaryLayoutService.average([row["student_values"][sid]["quiz_grade"] for sid in paired])
                                      if all(row["student_values"][sid].get("quiz_grade") is not None for sid in paired) else None)
                line.append(item)
                by_code[row["code"]] = item
            for row in table:
                item = by_code.get(row["code"])
                if item:
                    row["matched_students"] = len(paired)
            line_note = f"Progression uses the same {len(paired)} students with valid records in every displayed period; activity counts remain class-level."
        else:
            line_note = ("Progression is withheld because matched students or their excluded complements do not meet aggregate privacy rules."
                         if paired and frozenset(paired) not in disclosed_groups and selected_id not in leadership_ids else
                         "Progression is unavailable because concluded periods do not share enough students with valid comparable records or compatible grading definitions.")
            for row in table:
                if row["eligible"]:
                    row["matched_students"] = None
    else:
        line_rows = [r for r in table if r.get("eligible")]
        consistent_basis = len({r["cohort_basis"] for r in line_rows}) <= 1
        consistent_signature = len({r["signature"] for r in line_rows}) <= 1
        if consistent_basis and consistent_signature:
            line = [{"period": r["period"], **{metric: r[metric] if metric == "quiz" or r["scales"].get(metric) else None
                    for metric in ("quiz", "standing", "exam", "periodic")}} for r in line_rows]
            line_note = "Period averages appear only when authorized sections share compatible grading definitions and cohort coverage."
            for item, row in zip(line, line_rows):
                item["quiz_grade"] = row["quiz_grade"]
            if len({r["quiz_grade_basis"] for r in line_rows}) > 1:
                line_mode = "descriptive"
                line_note = "Descriptive period averages: the converted quiz values have different contributing sections; points are not connected into a matched trend."

    # Descriptive points repeat only already-disclosable class/period aggregates.
    # They introduce no new matched population and never imply a valid trend.
    if not line:
        line = [{"period": row["period"], "quiz": row["quiz"], "quiz_grade": row["quiz_grade"],
                 **{metric: row[metric] if row["scales"].get(metric) else None
                    for metric in ("standing", "exam", "periodic")}}
                for row in table if row.get("eligible") and any(row.get(metric) is not None
                    for metric in ("quiz", "quiz_grade", "standing", "exam", "periodic"))]
        line_mode = "descriptive"
        if line:
            line_note = "Descriptive concluded-period class averages only; points are not connected because matched progression is unavailable. " + line_note
    if selected_id and classifications and classifications[0]["category"] == INSUFFICIENT and line:
        line_mode = "descriptive"
        line_note += " Formal classification unavailable: " + classifications[0]["detail"]
    line = [point for point in line if any(point.get(metric) is not None
            for metric in ("quiz_grade", "standing", "exam", "periodic"))]

    bars, comparison_rows, bar_period, comparison_note = [], [], "", ""
    if selected_course:
        course_offerings = [o for o in selection.matching({**filters, "offering_id": None})
                            if o.course.code == filters["course_code"]]
        bar_code = next((code for code, _ in reversed(PERIODS) if any(
            r["concluded"] for offering in course_offerings for r in histories[offering.id] if r["code"] == code
        )), None)
        bar_period = dict(PERIODS).get(bar_code, "")
        period_rows = {o.id: next((r for r in histories[o.id] if r["code"] == bar_code and r["concluded"]), None)
                       if bar_code else None for o in course_offerings}
        available = [r for r in period_rows.values() if r and r["eligible"]]
        compatible = bool(available) and len({r["signature"] for r in available}) == 1 and all(
            all(r["scales"].get(metric) for metric in ("standing", "exam")) for r in available
        )
        comparison_note = ("Section score averages are withheld from direct comparison because grading arrangements or percentage scales differ."
                           if available and not compatible else
                           "No selected section has finalized results for a concluded period." if not available else "")
        for offering in course_offerings:
            row = period_rows.get(offering.id)
            reason = row["reason"] if row else ""
            if not row:
                reason = f"No concluded-period results are available for {bar_period or 'a concluded period'}."
            values = row if row and compatible else {}
            minimum = 1 if selection.allows_aggregates(offering) else MIN_STUDENTS
            category = classify(histories[offering.id], minimum_matched_students=minimum,
                                disclosed_groups=None if offering.id in leadership_ids else disclosed_groups)["category"]
            comparison_rows.append({"section": f"{offering.section.code} / {offering.campus.code}",
                "status": reason if reason else ("Comparable results available." if compatible else "Not comparable across selected sections."),
                "quizzes": row["quizzes"] if row else None, "activities": row["activities"] if row else None,
                "quiz": values.get("quiz"), "quiz_grade": values.get("quiz_grade"),
                "standing": values.get("standing") if values.get("scales", {}).get("standing") else None,
                "exam": values.get("exam") if values.get("scales", {}).get("exam") else None,
                "periodic": values.get("periodic") if values.get("scales", {}).get("periodic") else None,
                "pattern": category})
        if compatible:
            bars = [{"section": r["section"], "quiz": r["quiz"], "quiz_grade": r["quiz_grade"], "quizzes": r["quizzes"],
                     "standing": r["standing"], "exam": r["exam"]} for r in comparison_rows]
    public_table = [{k: v for k, v in r.items() if k not in {
        "cohort", "signature", "sequence", "cohort_basis", "quiz_grade_basis", "comparison_values", "student_values", "pattern_values", "contributors", "quiz_unsupported", "privacy_withheld",
    }} for r in table]
    return {"rows": public_table, "patterns": patterns, "classifications": classifications,
            "period_comparison": comparison, "pattern_reasons": pattern_reasons,
            "comparison_rows": comparison_rows, "comparison_note": comparison_note,
            "analyzed": denominator, "insufficient": len(chosen) - denominator, "sections": len(chosen),
            "view": "Individual Section Progression" if selected_id else
                    "Course Performance Comparison" if selected_course else "Overall Academic Performance",
            "class_label": (f"{chosen[0].course.code} / {chosen[0].section.code} — {chosen[0].campus.code}"
                            if selected_id and chosen else filters.get("course_code") or ""),
            "export_periods": sorted({(r["export_code"], r["period"]) for o in chosen for r in histories[o.id] if r["eligible"]}),
            "threshold": str(MEANINGFUL_CHANGE),
            "charts": {"line": line, "line_mode": line_mode, "bars": bars, "bar_period": bar_period, "patterns": patterns, "threshold": str(MEANINGFUL_CHANGE)},
            "line_note": line_note}
