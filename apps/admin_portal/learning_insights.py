"""Isolated, read-only learning analysis. Never compute or finalize academic records."""
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
from itertools import combinations

from django.db.models import F, Q
from django.utils import timezone

from apps.academics.models import ActiveGradingPeriodSetting, FacultyAssignment, TenantTermGradingPeriod
from apps.admin_portal.academic_performance import AcademicPerformanceInsightService as Reference
from apps.admin_portal.insights import (
    LEADERSHIP_PERMISSION, MIN_STUDENTS, Selection, campus_allowed, component_signature, leadership_access,
)
from apps.admin_portal.insights_generation import encoded, fingerprint
from apps.admin_portal.services import AdminScopeService
from apps.enrollment.models import Enrollment
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.grading.models import (
    GradeActivity, GradeCorrectionUnlockWindow, GradeSubmission, GradeSubmissionReopenRequest,
    GradingPeriodLock, StudentActivityScore, StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.students.models import Student
from apps.rbac.models import UserPermission, UserRole
from django.utils.crypto import salted_hmac


PERIODS = (("PRELIM", "Prelim"), ("MIDTERM", "Midterm"), ("PREFINAL", "Pre-Final"), ("FINAL", "Final"))
PATTERNS = ("Moved Together", "Moved Opposite", "Mixed / Stable")
MEANINGFUL_CHANGE = Decimal("3.00")
INSUFFICIENT = "Insufficient Comparable Data"
METRICS = ("quizzes", "quiz", "activities", "standing", "exam", "periodic")
DISCLOSURE_POLICY_VERSION = "component-and-paired-populations-v1"


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
        tenant_id = request.scope["tenant_id"]
        # Resolve optional/stale offering program values through the already
        # validated section program for this read-only analysis instance only.
        for assignment in self.assignments:
            offering = assignment.offering
            program = offering.program
            if (not program or not program.is_active or not program.department.is_active
                    or program.tenant_id != tenant_id or program.campus_id != offering.campus_id):
                offering.program_id = offering.section.program_id

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
               DISCLOSURE_POLICY_VERSION]
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


def activity_values(activities, scores, cohort, complete_cohort=None):
    recorded, quizzes = [], []
    for activity in activities:
        evidence = scores.get(activity.id, {})
        if not any(student_id in cohort and value is not None for student_id, value in evidence.items()):
            continue
        recorded.append(activity)
        if FacultyGradingService.is_quiz_activity(activity):
            quizzes.append(activity)
    quiz_values = {}
    if quizzes:
        for student_id in cohort:
            observations = [scores[a.id].get(student_id) for a in quizzes]
            if all(value is not None for value in observations):
                quiz_values[student_id] = exact_average(observations)
    complete_cohort = set(complete_cohort if complete_cohort is not None else cohort)
    quiz_exact = (exact_average([quiz_values[sid] for sid in complete_cohort])
                  if quizzes and complete_cohort and complete_cohort.issubset(quiz_values) else None)
    return {"quizzes": len(quizzes) if recorded else None, "activities": len(recorded) if recorded else None,
            "quiz": displayed(quiz_exact), "comparison_values": {"quiz": quiz_exact},
            "quiz_values": quiz_values}


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


def classify(history, minimum_matched_students=1, disclosed_groups=None):
    """Classify the latest comparable pair using one internally matched population."""
    metrics = ("quiz", "standing", "exam")
    usable = [r for r in history if r.get("eligible")]
    failure = "Two comparable concluded periods with valid finalized results are required."
    for current in reversed(usable):
        for previous in reversed([r for r in usable if r["sequence"] < current["sequence"]]):
            if current["signature"] != previous["signature"]:
                failure = "Grading methods or component scales differ between concluded periods."
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
                    failure = "Too few students have valid comparable records in both periods to meet the applicable privacy minimum."
                    continue
                if disclosed_groups is not None and frozenset(matched) not in disclosed_groups:
                    failure = "Paired results withheld because overlapping populations or excluded students do not meet aggregate privacy rules."
                    continue
                if any(not previous.get("scales", {}).get(m) or not current.get("scales", {}).get(m)
                       for m in ("standing", "exam")):
                    failure = "Verified percentage-based grading scales are unavailable for one or more components."
                    continue
                values = {
                    metric: (exact_average([previous_values[sid][metric] for sid in matched]),
                             exact_average([current_values[sid][metric] for sid in matched]))
                    for metric in metrics
                }
                if any(v is None or v < 0 or v > 100 for pair in values.values() for v in pair):
                    failure = "Component values do not establish reliable percentage-point differences."
                    continue
            else:
                # Retain compatibility for pure classifier callers; production rows
                # always carry student_values and take the matched-population path.
                if current.get("cohort") != previous.get("cohort"):
                    failure = "Student records are not comparable across the concluded periods."
                    continue
                if any(not previous.get("scales", {}).get(m) or not current.get("scales", {}).get(m)
                       for m in ("standing", "exam")):
                    failure = "Verified percentage-based grading scales are unavailable for one or more components."
                    continue
                values = {m: (previous.get("comparison_values", previous).get(m),
                              current.get("comparison_values", current).get(m)) for m in metrics}
                if any(v is None or v < 0 or v > 100 for pair in values.values() for v in pair):
                    failure = "Verified percentage-based component results are unavailable."
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
    return {"category": INSUFFICIENT, "detail": failure, "matched_students": None}


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


def build_report(selection, filters, now=None):
    now = now or timezone.now()
    # Membership disclosure is planned across the authorized year/term before optional filters.
    universe = selection.matching({k: filters[k] for k in ("academic_year_id", "term_id")})
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
    for s in StudentActivityScore.objects.filter(activity_id__in=valid_activities, is_active=True, is_excused=False):
        a = valid_activities[s.activity_id]
        if s.student_id in enrolled[a.offering_id]:
            scores[a.id][s.student_id] = score_percentage(s, a)
    histories, structures, contexts = {}, {}, {}
    for o in universe:
        history = []
        counts = Counter(key(p) for p in selection.periods(o))
        for p in selection.periods(o):
            if key(p) not in dict(PERIODS):
                continue
            pair = (o.id, p.id)
            lock = GradingGovernanceService.resolve_lock(offering=o, template_period=p)
            reason = governance_reason(o, p, active.get(o.campus_id), catalog, submissions.get(pair), lock, pair in blocked, now)
            if counts[key(p)] != 1:
                reason = "Grading-period mapping is ambiguous."
            saved = grades[pair]
            if o.id in invalid:
                reason = "Academic record scope is unavailable."
            period_authorized = not reason
            if p.id not in structures:
                scales, components = scale_definitions(p)
                structures[p.id] = (scales, components, component_signature(p))
            scales, components, structure = structures[p.id]
            context_key = (o.course_id, o.campus_id, o.department_id, o.program_id or o.section.program_id, o.term_id, p.id)
            if context_key not in contexts:
                contexts[context_key] = (Reference._grading_context(o, p), str(FacultyGradingService.resolve_base_value(o, p.template)))
            context, base_value = contexts[context_key]
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
            activity_data = activity_values(activities[pair], scores, enrolled[o.id], active_enrolled[o.id]) if period_authorized else {
                "quizzes": None, "activities": None, "quiz": None, "comparison_values": {"quiz": None}, "quiz_values": {},
            }
            student_values, pattern_values = {}, {}
            for sid, values in finalized.items():
                if sid in activity_data["quiz_values"]:
                    student_components = {**values, "quiz": activity_data["quiz_values"][sid]}
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
                   **{m: None for m in METRICS}, "quizzes": activity_data["quizzes"], "activities": activity_data["activities"]}
            row["quiz"] = activity_data["quiz"]
            row["contributors"]["quiz"] = set(active_enrolled[o.id]) if row["quiz"] is not None else set()
            row["comparison_values"] = dict(activity_data["comparison_values"])
            if finalized:
                for name in ("standing", "exam", "periodic"):
                    component_values = [values[name] for values in finalized.values() if name in values]
                    exact = exact_average(component_values)
                    row[name] = displayed(exact)
                    if name in ("standing", "exam"):
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
                    row["reason"] = "Quiz average unavailable: no students have valid non-excused scores for every recorded quiz."
            history.append(row)
        histories[o.id] = sorted(history, key=lambda r: r["sequence"])
    leadership_ids = {pk for pk, offering in offerings.items() if selection.allows_aggregates(offering)}
    disclosed_groups = disclosed_populations(histories, active_enrolled, leadership_ids)
    for pk, history in histories.items():
        if pk in leadership_ids:
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
    chosen = [o for o in selection.matching(filters) if not filters.get("offering_id") or o.id == filters["offering_id"]]
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
            entry[metric] = average(values) if compatible or metric in ("quizzes", "activities") else None
            entry[metric + "_sections"] = len(values) if entry[metric] is not None else 0
        if not compatible:
            entry["reason"] = "Choose one compatible course group for score averages. Counts and within-section patterns remain available."
        entry["cohort_basis"] = frozenset((o.id, tuple(sorted(r["cohort"]))) for o in chosen for r in histories[o.id] if r["code"] == code and r["eligible"])
        entry["signature"] = rows[0]["signature"] if compatible else None
        overall.append(entry)
    selected_id = filters.get("offering_id")
    selected_course = bool(filters.get("course_code"))
    table = histories.get(selected_id, []) if selected_id else overall
    line, line_note = [], "Choose one course and compatible grading definitions to plot raw period averages."
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
                "quiz": values.get("quiz"), "standing": values.get("standing") if values.get("scales", {}).get("standing") else None,
                "exam": values.get("exam") if values.get("scales", {}).get("exam") else None,
                "periodic": values.get("periodic") if values.get("scales", {}).get("periodic") else None,
                "pattern": category})
        if compatible:
            bars = [{"section": r["section"], "quiz": r["quiz"], "quizzes": r["quizzes"],
                     "standing": r["standing"], "exam": r["exam"]} for r in comparison_rows]
    public_table = [{k: v for k, v in r.items() if k not in {
        "cohort", "signature", "sequence", "cohort_basis", "comparison_values", "student_values", "pattern_values", "contributors",
    }} for r in table]
    return {"rows": public_table, "patterns": patterns, "classifications": classifications,
            "comparison_rows": comparison_rows, "comparison_note": comparison_note,
            "analyzed": denominator, "insufficient": len(chosen) - denominator, "sections": len(chosen),
            "view": "Individual Section Progression" if selected_id else
                    "Course Performance Comparison" if selected_course else "Overall Academic Performance",
            "class_label": (f"{chosen[0].course.code} / {chosen[0].section.code} — {chosen[0].campus.code}"
                            if selected_id and chosen else filters.get("course_code") or ""),
            "export_periods": sorted({(r["export_code"], r["period"]) for o in chosen for r in histories[o.id] if r["eligible"]}),
            "threshold": str(MEANINGFUL_CHANGE),
            "charts": {"line": line, "bars": bars, "bar_period": bar_period, "patterns": patterns, "threshold": str(MEANINGFUL_CHANGE)},
            "line_note": line_note}
