"""Isolated, read-only learning analysis. Never compute or finalize academic records."""
from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import F, Q
from django.utils import timezone

from apps.academics.models import ActiveGradingPeriodSetting, FacultyAssignment, TenantTermGradingPeriod
from apps.admin_portal.academic_performance import AcademicPerformanceInsightService as Reference
from apps.admin_portal.insights import MIN_STUDENTS, Selection, component_signature
from apps.admin_portal.insights_generation import encoded, fingerprint
from apps.admin_portal.services import AdminScopeService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeActivity, GradeCorrectionUnlockWindow, GradeSubmission, GradeSubmissionReopenRequest,
    GradingPeriodLock, StudentActivityScore, StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from django.utils.crypto import salted_hmac


PERIODS = (("PRELIM", "Prelim"), ("MIDTERM", "Midterm"), ("PREFINAL", "Pre-Final"), ("FINAL", "Final"))
PATTERNS = ("Moved Together", "Moved Opposite", "Mixed / Unchanged")
INSUFFICIENT = "Insufficient Comparable Data"
METRICS = ("quizzes", "quiz", "activities", "standing", "exam", "periodic")


def key(period):
    return GradingGovernanceService.canonical_correction_period_key(period)


def average(values):
    return GradingGovernanceService._round(sum((Decimal(v) for v in values), Decimal("0")) / Decimal(len(values))) if values else None


class LearningSelection(Selection):
    @staticmethod
    def assignment_source(request):
        """Same supervision chain; optional offering.program uses its valid section program."""
        tenant_id = request.scope["tenant_id"]
        supervised = Q(pk__in=[])
        for campus_id, faculty_ids in AdminScopeService._faculty_ids_by_monitoring_campus(
            request, include_all_campuses=True,
        ).items():
            supervised |= Q(offering__campus_id=campus_id, faculty_user_id__in=faculty_ids)
        source = FacultyAssignment.objects.filter(
            supervised, offering__tenant_id__in=AdminScopeService.active_scoped_tenants(request).values("id"),
            offering__term_id__in=AdminScopeService.active_scoped_terms(request).values("id"),
            offering__is_active=True, offering__department__is_active=True,
            offering__course__is_active=True, offering__section__is_active=True,
            offering__section__department__is_active=True,
            offering__section__program__is_active=True, offering__section__program__department__is_active=True,
            offering__section__tenant_id=tenant_id, offering__section__campus_id=F("offering__campus_id"),
            offering__section__program__tenant_id=tenant_id,
            offering__section__program__campus_id=F("offering__campus_id"),
        ).filter(
            Q(offering__program__isnull=True) | Q(
                offering__program__is_active=True, offering__program__department__is_active=True,
                offering__program__tenant_id=tenant_id, offering__program__campus_id=F("offering__campus_id"),
            )
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
    payload = [fingerprint(selection, filters)]
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
    if not value.is_finite():
        return None
    if mode == "DIRECT_PERCENTAGE":
        return value if 0 <= value <= 100 else None
    if mode == "RAW_BASE50" and activity.total_score > 0 and 0 <= value <= activity.total_score:
        return value * 100 / activity.total_score
    return None


def activity_values(activities, scores, cohort):
    recorded, quizzes = [], []
    for activity in activities:
        evidence = scores.get(activity.id, {})
        if not any(student_id in cohort and value is not None for student_id, value in evidence.items()):
            continue
        recorded.append(activity)
        if FacultyGradingService.is_quiz_activity(activity):
            quizzes.append(activity)
    quiz = None
    if quizzes and all(set(scores[a.id]) == cohort and all(v is not None for v in scores[a.id].values()) for a in quizzes):
        quiz = average([sum(scores[a.id].values(), Decimal("0")) / Decimal(len(cohort)) for a in quizzes])
    return {"quizzes": len(quizzes) if recorded else None, "activities": len(recorded) if recorded else None, "quiz": quiz}


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


def classify(history):
    """Most recent matching pair; a missing period never becomes an artificial zero."""
    usable = [r for r in history if r["eligible"] and all(r[m] is not None for m in ("quiz", "standing", "exam"))]
    for current in reversed(usable):
        for previous in reversed([r for r in usable if r["sequence"] < current["sequence"]]):
            if current["signature"] != previous["signature"] or current["cohort"] != previous["cohort"]:
                continue
            deltas = {m: current[m] - previous[m] for m in ("quiz", "standing", "exam", "quizzes", "activities")}
            signs = [0 if deltas[m] == 0 else 1 if deltas[m] > 0 else -1 for m in ("quiz", "standing", "exam")]
            if signs[0] == signs[1] == signs[2] and signs[0]:
                category = PATTERNS[0]
                direction = "Improved together" if signs[0] > 0 else "Declined together"
            elif signs[0] == signs[1] and signs[0] and signs[2] == -signs[0]:
                category, direction = PATTERNS[1], "Quiz and class standing moved opposite to examination results"
            else:
                category, direction = PATTERNS[2], "Mixed directions or unchanged results"
            return {"category": category, "detail": direction, "from": previous["period"], "to": current["period"],
                    "quiz_count_change": deltas["quizzes"], "activity_count_change": deltas["activities"]}
    return {"category": INSUFFICIENT, "detail": "Two comparable concluded periods with the same cohort are required."}


def build_report(selection, filters, now=None):
    now = now or timezone.now()
    # Membership disclosure is planned across the authorized year/term before optional filters.
    universe = selection.matching({k: filters[k] for k in ("academic_year_id", "term_id")})
    ids = [o.id for o in universe]
    offerings = {o.id: o for o in universe}
    tenant_id = selection.request.scope["tenant_id"]
    enrolled = defaultdict(set)
    invalid = set()
    enrollment_rows = Enrollment.objects.filter(course_offering_id__in=ids, is_active=True,
        student__is_active=True, student__department__is_active=True).filter(
        Q(student__program__isnull=True) | Q(student__program__is_active=True)
    ).exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES).select_related("student")
    for e in enrollment_rows:
        o = offerings[e.course_offering_id]
        if (e.tenant_id, e.campus_id, e.academic_year_id, e.term_id, e.student.tenant_id, e.student.campus_id) != (
            o.tenant_id, o.campus_id, o.academic_year_id, o.term_id, o.tenant_id, o.campus_id
        ):
            invalid.add(o.id)
        else:
            enrolled[o.id].add(e.student_id)
    grades = defaultdict(dict)
    for g in StudentPeriodGrade.objects.filter(offering_id__in=ids).select_related("student"):
        o = offerings[g.offering_id]
        if (g.tenant_id, g.campus_id, g.student.tenant_id, g.student.campus_id) != (o.tenant_id, o.campus_id, o.tenant_id, o.campus_id):
            invalid.add(o.id)
        grades[(o.id, g.template_period_id)][g.student_id] = g
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
            cohort = enrolled[o.id]
            saved = grades[pair]
            complete = bool(cohort) and all(student_id in saved and saved[student_id].is_finalized and all(
                getattr(saved[student_id], f) is not None and getattr(saved[student_id], f).is_finite() and getattr(saved[student_id], f) >= 0
                for f in ("class_standing_grade", "exam_grade", "period_grade")
            ) for student_id in cohort)
            if not reason and not complete:
                reason = "Complete finalized class results are unavailable."
            if o.id in invalid:
                reason = "Academic record scope is unavailable."
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
            row = {"period": dict(PERIODS)[key(p)], "code": key(p), "export_code": p.code, "sequence": dict((k, n) for n, (k, _) in enumerate(PERIODS))[key(p)],
                   "eligible": not reason, "reason": reason, "cohort": cohort, "signature": signature,
                   "scales": dict(scales), **{m: None for m in METRICS}}
            if not reason:
                row.update(activity_values(activities[pair], scores, cohort))
                if row["quiz"] is None:
                    row["reason"] = "Quiz average unavailable: recorded quizzes need valid non-excused scores for the complete class cohort."
                for name, field in (("standing", "class_standing_grade"), ("exam", "exam_grade"), ("periodic", "period_grade")):
                    row[name] = average([getattr(saved[s], field) for s in cohort])
                    if name in ("standing", "exam") and any(not 0 <= getattr(saved[s], field) <= 100 for s in cohort):
                        row["scales"][name] = False
                row["scales"]["periodic"] = (all(0 <= saved[s].period_grade <= 100 for s in cohort)
                    and sum(c.weight_percentage for c in components) == 100 and all(0 <= c.weight_percentage <= 100 for c in components)
                    and all(row["scales"][name] for name in ("standing", "exam"))
                    and all(0 <= Decimal(band[2]) <= 100 for band in context["signature"][3]))
                if not 0 <= Decimal(base_value) <= 100:
                    row["scales"] = dict.fromkeys(row["scales"], False)
            history.append(row)
        histories[o.id] = sorted(history, key=lambda r: r["sequence"])
    memberships = defaultdict(set)
    for pk, history in histories.items():
        for r in history:
            if r["eligible"]:
                for sid in r["cohort"]:
                    memberships[sid].add((pk, r["code"]))
    atoms = Counter(frozenset(v) for v in memberships.values())
    hidden = {pair for pairs, n in atoms.items() if n < MIN_STUDENTS for pair in pairs}
    for pk, history in histories.items():
        if selection.allows_aggregates(offerings[pk]):
            continue
        for r in history:
            if (pk, r["code"]) in hidden or len(r["cohort"]) < MIN_STUDENTS:
                r.update(eligible=False, reason="Class results withheld by aggregate privacy rules.")
                r.update({m: None for m in METRICS})
    chosen = [o for o in selection.matching(filters) if not filters.get("offering_id") or o.id == filters["offering_id"]]
    classifications = [{"id": o.id, "course": o.course.code, "section": o.section.code, "campus": o.campus.code,
                        **classify(histories[o.id])} for o in chosen]
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
    table = histories.get(selected_id, []) if selected_id else overall
    # Do not connect overall averages when the underlying classes/cohorts or definitions change.
    line = []
    line_rows = [r for r in table if r["eligible"]]
    consistent_basis = bool(selected_id) or len({r["cohort_basis"] for r in line_rows}) <= 1
    consistent_signature = len({r["signature"] for r in line_rows}) <= 1
    for r in line_rows:
        line.append({"period": r["period"], **{m: r[m] if consistent_basis and consistent_signature and (
            m == "quiz" or r["scales"].get(m)
        ) else None for m in ("quiz", "standing", "exam", "periodic")}})
    if selected_id and len({tuple(sorted(r["cohort"])) for r in line_rows}) > 1:
        line = [{"period": r["period"], **{m: None for m in ("quiz", "standing", "exam", "periodic")}} for r in line_rows]
    # Direct section bars: one course, most recent common concluded period and identical arrangement.
    bars = []
    bar_period = ""
    if filters.get("course_code"):
        for code, label in reversed(PERIODS):
            candidates = [(o, r) for o in selection.matching({**filters, "offering_id": None})
                          for r in histories[o.id] if r["code"] == code and r["eligible"]]
            if candidates:
                if len({r["signature"] for _, r in candidates}) == 1:
                    bars = [{"section": f"{o.section.code} / {o.campus.code}", "quiz": r["quiz"],
                             "quizzes": r["quizzes"], "exam": r["exam"] if r["scales"]["exam"] else None} for o, r in candidates]
                    bar_period = label
                break
    public_table = [{k: v for k, v in r.items() if k not in {"cohort", "signature", "sequence", "cohort_basis"}} for r in table]
    return {"rows": public_table, "patterns": patterns, "classifications": classifications,
            "analyzed": denominator, "insufficient": len(chosen) - denominator, "sections": len(chosen),
            "view": "Class Performance Analysis" if selected_id else "Overall Learning Performance",
            "class_label": next((f"{o.course.code} / {o.section.code} — {o.campus.code}" for o in chosen), "") if selected_id else "",
            "export_periods": sorted({(r["export_code"], r["period"]) for o in chosen for r in histories[o.id] if r["eligible"]}),
            "charts": {"line": line, "bars": bars, "bar_period": bar_period, "patterns": patterns},
            "line_note": "Progression requires the same class cohort and compatible grading definitions; only verified 0–100 grade scales are plotted."}
