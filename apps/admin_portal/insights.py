"""Insights-only selection and disclosure. Protected reports use their original service."""
from collections import Counter, defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Exists, F, OuterRef, Q

from apps.admin_portal.academic_performance import AcademicPerformanceInsightService as Official
from apps.admin_portal.services import AdminScopeService
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.enrollment.models import Enrollment
from apps.grading.models import GradeActivity, StudentActivityScore, StudentPeriodGrade
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.rbac.models import UserPermission, UserRole
from apps.tenants.models import Department


MIN_STUDENTS = 5
UNAVAILABLE = "Not Available"
SUPPRESSED = "Suppressed (privacy)"
LEADERSHIP_PERMISSION = "faculty_gradebook_monitor.read"


def faculty_label(user):
    given = " ".join(part.strip() for part in (user.first_name, user.middle_name) if part and part.strip())
    return f"{user.last_name.strip()}, {given}".strip(", ") if user.last_name else given or user.username


def leadership_access(request, campus_id):
    """Existing supervised-gradebook authority permits class aggregates, never identities.

    This permission is seeded for academic leaders and can be explicitly assigned
    to other academic administrators. A role title alone grants no exception.
    The assignment inventory still determines every authorized class.
    """
    tenant_id = request.scope["tenant_id"]
    denied = UserPermission.objects.filter(
        user=request.user, permission__code=LEADERSHIP_PERMISSION, permission__is_active=True,
        grant_type=UserPermission.GrantType.DENY,
    ).filter(Q(tenant_id=tenant_id) | Q(tenant__isnull=True)).filter(
        Q(campus_id=campus_id) | Q(campus__isnull=True)
    ).exists()
    return not denied and PermissionService.has_permission(
        request.user, LEADERSHIP_PERMISSION, tenant_id=tenant_id, campus_id=campus_id,
    )


def campus_allowed(request, campus_id):
    tenant_id = request.scope.get("tenant_id")
    permissions = ("admin_portal.access", "grading_analytics.read")
    denied = UserPermission.objects.filter(
        user=request.user, permission__code__in=permissions, permission__is_active=True,
        grant_type=UserPermission.GrantType.DENY,
    ).filter(Q(tenant_id=tenant_id) | Q(tenant__isnull=True)).filter(
        Q(campus_id=campus_id) | Q(campus__isnull=True)
    ).exists()
    return not denied and all(PermissionService.has_permission(
        request.user, code, tenant_id=tenant_id, campus_id=campus_id,
    ) for code in permissions)


class Selection:
    """One supervised assignment inventory; never use offering department ownership."""
    def __init__(self, request):
        self.request = request
        tenant_id = request.scope["tenant_id"]
        codes = {str(code).upper() for code in UserRole.objects.filter(
            user=request.user, is_active=True, role__is_active=True,
        ).filter(Q(tenant_id=tenant_id) | Q(tenant__isnull=True)).values_list("role__code", flat=True)}
        compare = request.user.is_superuser or "CAO" in codes or bool(codes & Official.DEAN_CODES)
        self.role = {"can_compare_campuses": compare}
        campus_source = AdminScopeService.active_scoped_campuses(request).filter(tenant_id=tenant_id)
        if not compare:
            campus_source = campus_source.filter(id=request.scope.get("campus_id"))
        self.campuses = [c for c in campus_source if campus_allowed(request, c.id)]
        campus_ids = {c.id for c in self.campuses}
        self.leadership_campuses = {c.id for c in self.campuses if leadership_access(request, c.id)}
        saved_scores = StudentActivityScore.objects.filter(
            activity__offering_id=OuterRef("offering_id"), activity__tenant_id=tenant_id,
            activity__campus_id=OuterRef("offering__campus_id"), activity__is_active=True,
            student__tenant_id=tenant_id, student__campus_id=OuterRef("offering__campus_id"), is_active=True,
        )
        saved_grades = StudentPeriodGrade.objects.filter(
            offering_id=OuterRef("offering_id"), tenant_id=tenant_id,
            campus_id=OuterRef("offering__campus_id"), student__tenant_id=tenant_id,
            student__campus_id=OuterRef("offering__campus_id"),
        )
        assignments = AdminScopeService.scoped_faculty_assignments(
            request, include_all_campuses=True,
        ).filter(
            tenant_id=tenant_id, offering__tenant_id=tenant_id,
            offering__academic_year__tenant_id=tenant_id, offering__term__tenant_id=tenant_id,
            offering__term__academic_year_id=F("offering__academic_year_id"),
            offering__course__tenant_id=tenant_id, offering__section__tenant_id=tenant_id,
            campus_id=F("offering__campus_id"),
            campus_id__in=campus_ids, offering__campus_id__in=campus_ids,
            is_active=True, response_status="ACCEPTED",
            faculty_user__is_active=True,
        ).alias(saved_scores=Exists(saved_scores), saved_grades=Exists(saved_grades)).filter(
            Q(offering__status="OPEN") | (Q(offering__status__in=("CLOSED", "ARCHIVED")) &
                                         (Q(saved_scores=True) | Q(saved_grades=True)))
        ).select_related("offering__section__program", "offering__course", "faculty_user")
        self.assignments = list(assignments)
        self.offerings = {a.offering_id: a.offering for a in self.assignments}
        self.faculty = {a.faculty_user_id: a.faculty_user for a in self.assignments}
        department_scope = Q(pk__in=[])
        for campus_id in campus_ids:
            department_scope |= Q(campus_id=campus_id, pk__in=ScopeService.get_accessible_department_ids(
                request.user, tenant_id=tenant_id, campus_id=campus_id,
            ))
        self.departments = list(Department.objects.filter(
            department_scope, tenant_id=tenant_id, is_active=True,
        ).select_related("campus").order_by("name", "id"))
        self.faculty_departments = defaultdict(set)
        for role in UserRole.objects.filter(
            user_id__in=self.faculty, is_active=True, role__code="FACULTY", role__is_active=True,
        ).filter(Q(tenant_id=tenant_id) | Q(tenant__isnull=True, user__default_tenant_id=tenant_id)).select_related("user"):
            campus_id = role.campus_id or role.user.default_campus_id
            department_id = role.department_id or role.user.default_department_id
            self.faculty_departments[(role.user_id, campus_id)].add(department_id)
        self._periods = {}

    def matching_assignments(self, filters):
        department_ids = None
        if filters.get("department_id"):
            department_ids = set(AdminScopeService.expand_department_filter_ids(
                filters["department_id"], campus_id=filters.get("campus_id"),
            ))
        matches = []
        for assignment in self.assignments:
            offering = assignment.offering
            if any(filters.get(key) and getattr(offering, key) != filters[key]
                   for key in ("academic_year_id", "term_id", "campus_id")):
                continue
            if filters.get("course_code") and offering.course.code != filters["course_code"]:
                continue
            if filters.get("faculty_id") and assignment.faculty_user_id != filters["faculty_id"]:
                continue
            if department_ids is not None and not department_ids.intersection(
                self.faculty_departments[(assignment.faculty_user_id, offering.campus_id)]
            ):
                continue
            matches.append(assignment)
        return matches

    def matching(self, filters):
        ids = {assignment.offering_id for assignment in self.matching_assignments(filters)}
        return sorted((self.offerings[pk] for pk in ids), key=lambda o: (
            o.campus.code, o.course.code, o.section.code, o.id,
        ))

    def allows_aggregates(self, offering):
        return offering.id in self.offerings and offering.campus_id in self.leadership_campuses

    def hidden_sections(self, sections):
        return {pk for pk in disclosure_mask(sections) if not self.allows_aggregates(sections[pk]["offering"])} | {
            pk for pk, section in sections.items() if section["invalid_scope"]
        }

    def periods(self, offering):
        key = (offering.course_id, offering.campus_id, offering.department_id,
               offering.program_id, offering.section.program_id, offering.term_id)
        if key not in self._periods:
            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
                self._periods[key] = list(FacultyGradingService.get_template_periods(template))
                for period in self._periods[key]:
                    period.template = template
            except ValidationError:
                self._periods[key] = []
        return self._periods[key]

    def options(self, selected):
        """Resolve a dependency chain without making downstream values restrict parents."""
        filters = dict(selected)
        options = {}
        def choose(key, values):
            options[key] = values
            allowed = {str(v["value"]) for v in values}
            if str(filters.get(key) or "") not in allowed:
                filters[key] = "" if key in {"course_code", "period_code"} else None
        offerings = list(self.offerings.values())
        years = {o.academic_year_id: o.academic_year.code for o in offerings}
        choose("academic_year_id", [{"value": pk, "label": label} for pk, label in sorted(years.items())])
        offerings = self.matching({"academic_year_id": filters["academic_year_id"]})
        terms = {o.term_id: f"{o.academic_year.code} / {o.term.code}" for o in offerings}
        choose("term_id", [{"value": pk, "label": label} for pk, label in sorted(terms.items())])
        choose("campus_id", [{"value": c.id, "label": f"{c.code} - {c.name}"} for c in self.campuses])
        if not filters["campus_id"] and not self.role["can_compare_campuses"] and self.campuses:
            filters["campus_id"] = self.campuses[0].id
        parent = {k: filters[k] for k in ("academic_year_id", "term_id", "campus_id")}
        relevant_faculty = {(a.faculty_user_id, a.offering.campus_id) for a in self.matching_assignments(parent)}
        departments = []
        for department in self.departments:
            if filters["campus_id"] and department.campus_id != filters["campus_id"]:
                continue
            expanded = set(AdminScopeService.expand_department_filter_ids(department.id, campus_id=department.campus_id))
            if any(expanded.intersection(self.faculty_departments[pair]) for pair in relevant_faculty
                   if pair[1] == department.campus_id):
                departments.append({"value": department.id, "label": department.name})
        choose("department_id", departments)
        parent["department_id"] = filters["department_id"]
        offerings = self.matching(parent)
        courses = {o.course.code: o.course.title for o in offerings}
        choose("course_code", [{"value": code, "label": f"{code} - {title}"} for code, title in sorted(courses.items())])
        parent["course_code"] = filters["course_code"]
        faculty_ids = {a.faculty_user_id for a in self.matching_assignments(parent)}
        choose("faculty_id", [{"value": pk, "label": faculty_label(self.faculty[pk])}
                              for pk in sorted(faculty_ids, key=lambda pk: (
                                  (self.faculty[pk].last_name or "").casefold(),
                                  (self.faculty[pk].first_name or "").casefold(),
                                  (self.faculty[pk].middle_name or "").casefold(), pk))])
        parent["faculty_id"] = filters["faculty_id"]
        # A selected teacher also narrows the course catalogue, using assignments.
        courses = {o.course.code: o.course.title for o in self.matching({
            **parent, "course_code": "",
        })}
        choose("course_code", [{"value": code, "label": f"{code} - {title}"} for code, title in sorted(courses.items())])
        periods = {}
        if filters["academic_year_id"] and filters["term_id"]:
            for offering in self.matching(parent):
                for period in self.periods(offering):
                    periods.setdefault(period.code, (period.sequence_no, period.name))
        choose("period_code", [{"value": code, "label": name} for code, (seq, name) in
                               sorted(periods.items(), key=lambda item: (item[1][0], item[0]))])
        return filters, options


def releasable_partition(count, total):
    return count in (0, total) or (count >= MIN_STUDENTS and total - count >= MIN_STUDENTS)


def disclosure_mask(sections):
    """Stable membership atoms across the whole authorized year/term, before UI filters.

    Totals use disclosed classes only. Suppressed classes never contribute a residual.
    A small atom withholds every participating class, independent of the selected filters.
    """
    memberships = defaultdict(set)
    for pk, section in sections.items():
        for row in section["coverage"]["rows"]:
            if row["usable_grade"]:
                memberships[row["student_id"]].add(pk)
    atoms = Counter(frozenset(ids) for ids in memberships.values())
    hidden = {pk for ids, count in atoms.items() if count < MIN_STUDENTS for pk in ids}
    hidden.update(pk for pk, s in sections.items() if s["coverage"]["computed_grade_count"] < MIN_STUDENTS)
    return hidden


def collect_sections(selection, filters):
    # Privacy basis deliberately excludes campus/department/course/faculty UI filters.
    key = tuple(filters[k] for k in ("academic_year_id", "term_id", "period_code"))
    if not hasattr(selection, "_sections"):
        selection._sections = {}
    if key in selection._sections:
        return selection._sections[key]
    universe = selection.matching({k: filters[k] for k in ("academic_year_id", "term_id")})
    supervised_by_offering = defaultdict(list)
    for assignment in selection.matching_assignments(filters):
        supervised_by_offering[assignment.offering_id].append(assignment)
    sections = {}
    for offering in universe:
        period = next((p for p in selection.periods(offering) if p.code == filters["period_code"]), None)
        if period:
            eligible = Enrollment.objects.filter(
                course_offering=offering, is_active=True, student__is_active=True,
                student__department__is_active=True,
            ).filter(Q(student__program__isnull=True) | Q(student__program__is_active=True)).exclude(
                enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES,
            )
            valid = eligible.filter(
                tenant_id=offering.tenant_id, campus_id=offering.campus_id,
                academic_year_id=offering.academic_year_id, term_id=offering.term_id,
                student__tenant_id=offering.tenant_id, student__campus_id=offering.campus_id,
            )
            valid_ids = set(valid.values_list("student_id", flat=True))
            # Fail before invoking the legacy calculator if an eligible row crosses record scope.
            invalid_scope = eligible.exclude(student_id__in=valid_ids).exists()
            section = withheld_section(offering, period) if invalid_scope else Official.get_section_performance_summary(offering, period)
            section["grade_signature"] = (*section["grading_context"]["signature"],
                                          str(FacultyGradingService.resolve_base_value(offering, period.template)))
            section["invalid_scope"] = invalid_scope or any(r["student_id"] not in valid_ids for r in section["coverage"]["rows"])
            supervised = supervised_by_offering[offering.id]
            if supervised:
                section["faculty_name"] = faculty_label(supervised[0].faculty_user)
                section["faculty_id"] = supervised[0].faculty_user_id
            sections[offering.id] = section
    selection._sections[key] = sections
    return sections


def withheld_section(offering, period):
    context = Official._grading_context(offering, period)
    threshold = context["passing_threshold"]
    return {"offering": offering, "period": period, "faculty_name": "", "class_average": None,
            "main_concern": SUPPRESSED, "grading_context": context,
            "coverage": {"rows": [], "computed_grade_count": 0, "active_enrollment_count": 0,
                "below_threshold_count": 0, "below_threshold_rate": 0, "passing_count": 0, "passing_rate": 0,
                "no_grade_count": 0, "coverage_rate": 0, "average": None, "median": None,
                "passing_threshold": threshold, "state": SUPPRESSED, "is_provisional": True,
                "distribution": Official._distribution([], threshold)}}


def report(selection, filters):
    sections = collect_sections(selection, filters)
    hidden = selection.hidden_sections(sections)
    below_memberships = defaultdict(set)
    for pk, section in sections.items():
        if pk in hidden:
            continue
        for row in section["coverage"]["rows"]:
            if row["usable_grade"] and Decimal(row["current_grade"]) < section["coverage"]["passing_threshold"]:
                below_memberships[row["student_id"]].add(pk)
    below_atoms = Counter(frozenset(ids) for ids in below_memberships.values())
    outcome_hidden = {pk for ids, count in below_atoms.items() if count < MIN_STUDENTS for pk in ids}
    selected = selection.matching(filters)
    raw = [sections[o.id] for o in selected if o.id in sections]
    rows = [public_section(s, s["offering"].id in hidden, s["offering"].id in outcome_hidden,
                           selection.allows_aggregates(s["offering"])) for s in raw]
    visible = [s for s in raw if s["offering"].id not in hidden]
    signatures = {s["grade_signature"] for s in visible}
    values = [Decimal(r["current_grade"]) for s in visible for r in s["coverage"]["rows"] if r["usable_grade"]]
    students = {r["student_id"] for s in visible for r in s["coverage"]["rows"] if r["usable_grade"]}
    below = {r["student_id"] for s in visible for r in s["coverage"]["rows"]
             if r["usable_grade"] and Decimal(r["current_grade"]) < s["coverage"]["passing_threshold"]}
    average = str(GradingGovernanceService._round(sum(values) / len(values))) if values and len(signatures) == 1 else UNAVAILABLE
    all_leadership = bool(visible) and all(selection.allows_aggregates(s["offering"]) for s in visible)
    outcomes_safe = not any(s["offering"].id in outcome_hidden and not selection.allows_aggregates(s["offering"]) for s in visible)
    below_count = len(below) if visible and (all_leadership or (outcomes_safe and releasable_partition(len(below), len(students)))) else SUPPRESSED
    groups = defaultdict(list)
    for section in visible:
        groups[(section["offering"].course.code, section["grade_signature"])].append(section)
    averages = []
    if len(signatures) > 1:
        for (course, signature), members in groups.items():
            grades = [Decimal(r["current_grade"]) for s in members for r in s["coverage"]["rows"] if r["usable_grade"]]
            if grades:
                averages.append({"course": course, "template": members[0]["grading_context"]["template_label"],
                                 "group": len(averages) + 1,
                                 "average": str(GradingGovernanceService._round(sum(grades) / len(grades)))})
    structures = {}
    trends = Counter(summary_trend(selection, section, structures) for section in visible)
    trend_text = ", ".join(f"{trends[label]} {label.lower()}" for label in ("Improving", "Stable", "Declining")) if any(trends[label] for label in ("Improving", "Stable", "Declining")) else UNAVAILABLE
    summary = {"average": average, "below": below_count,
               "attention": sum(r["attention"] for r in rows if not r["suppressed"]),
               "trend": trend_text, "trend_unavailable": trends[UNAVAILABLE], "classes": len(rows),
               "disclosed": len(visible), "suppressed": len(rows) - len(visible),
               "averages": averages,
               "average_note": "Different grading configurations; see compatible course groups below." if len(signatures) > 1 else "Usable grade records; repeated enrollments count separately."}
    return {"summary": summary, "rows": rows, "priority": [r for r in rows if r["attention"] and not r["suppressed"]][:5]}, raw


def public_section(section, hidden, outcome_hidden=False, leadership=False):
    offering = section["offering"]
    coverage = section["coverage"]
    n = coverage["computed_grade_count"]
    category_safe = leadership or (not outcome_hidden and releasable_partition(coverage["below_threshold_count"], n))
    distribution_safe = leadership or (not outcome_hidden and all(releasable_partition(row["count"], n) for row in coverage["distribution"]))
    coverage_safe = leadership or releasable_partition(coverage["no_grade_count"], coverage["active_enrollment_count"])
    return {
        "id": offering.id, "campus": offering.campus.code, "year": offering.academic_year.code,
        "term": offering.term.code, "course": offering.course.code, "title": offering.course.title,
        "section": offering.section.code, "faculty": section["faculty_name"],
        "period": section["period"].name, "period_code": section["period"].code,
        "suppressed": hidden, "average": SUPPRESSED if hidden else str(coverage["average"]) if coverage["average"] is not None else UNAVAILABLE,
        "withholding_reason": "Record scope could not be verified." if section.get("invalid_scope") else "Exact analytics require at least five usable graded students and safe overlapping cohorts.",
        "graded": SUPPRESSED if hidden else n,
        "below": SUPPRESSED if hidden or not category_safe else coverage["below_threshold_count"],
        "below_rate": SUPPRESSED if hidden or not category_safe else str(coverage["below_threshold_rate"]),
        "threshold": str(coverage["passing_threshold"]),
        "state": SUPPRESSED if hidden else coverage["state"],
        "concern": SUPPRESSED if hidden else section["main_concern"],
        "attention": not hidden and ((coverage["average"] is not None and coverage["average"] < coverage["passing_threshold"]) or bool(coverage["below_threshold_count"]) or coverage["no_grade_count"] > 0),
        "class_below": not hidden and coverage["average"] is not None and coverage["average"] < coverage["passing_threshold"],
        "distribution": [] if hidden or not distribution_safe else [
            {"label": r["label"], "lower": str(r["lower"]) if r["lower"] is not None else None,
             "upper": str(r["upper"]) if r["upper"] is not None else None,
             "count": r["count"], "percentage": str(r["percentage"])} for r in coverage["distribution"]
        ],
        "bands": [{"label": r["label"], "lower": str(r["lower"]) if r["lower"] is not None else None,
                   "upper": str(r["upper"]) if r["upper"] is not None else None} for r in coverage["distribution"]],
        "template": section["grading_context"]["template_label"],
        "formula": section["grading_context"]["formula_mode"],
        "profile": section["grading_context"]["profile_label"],
        "enrolled": SUPPRESSED if hidden or not coverage_safe else coverage["active_enrollment_count"],
        "no_grade": SUPPRESSED if hidden or not coverage_safe else coverage["no_grade_count"],
        "coverage_rate": SUPPRESSED if hidden or not coverage_safe else str(coverage["coverage_rate"]),
        "passing": SUPPRESSED if hidden or not category_safe else coverage["passing_count"],
        "passing_rate": SUPPRESSED if hidden or not category_safe else str(coverage["passing_rate"]),
        "median": SUPPRESSED if hidden else str(coverage["median"]) if coverage["median"] is not None else UNAVAILABLE,
    }


def activity_metrics(offerings, periods, cohorts, eligible, leadership_ids=frozenset()):
    """Two bulk reads. A score is evidence of recording, never proof of conduct."""
    activities = list(GradeActivity.objects.filter(
        offering_id__in=offerings, template_period_id__in=periods.values(), is_active=True,
        template_component__is_active=True,
    ).filter(Q(template_subcomponent__isnull=True) | Q(template_subcomponent__is_active=True)).filter(
        Q(template_detail__isnull=True) | Q(template_detail__is_active=True)
    ).select_related("template_component", "template_subcomponent", "template_detail"))
    valid = {a.id: a for a in activities if a.template_period_id == periods.get(a.offering_id)
             and a.tenant_id == offerings[a.offering_id].tenant_id
             and a.campus_id == offerings[a.offering_id].campus_id
             and a.template_component.template_period_id == a.template_period_id
             and (not a.template_subcomponent_id or a.template_subcomponent.template_component_id == a.template_component_id)
             and (not a.template_detail_id or a.template_detail.template_subcomponent_id == a.template_subcomponent_id)}
    scores = defaultdict(dict)
    for score in StudentActivityScore.objects.filter(activity_id__in=valid, is_active=True, is_excused=False):
        a = valid[score.activity_id]
        if score.student_id in eligible.get(a.offering_id, set()):
            mode = FacultyGradingService.resolve_score_input_mode(
                template_component=a.template_component, template_subcomponent=a.template_subcomponent,
                template_detail=a.template_detail,
            )
            value = Decimal(score.raw_score)
            if mode == "DIRECT_PERCENTAGE":
                value = value if 0 <= value <= 100 else None
            elif mode == "RAW_BASE50":
                value = value * 100 / a.total_score if a.total_score > 0 and 0 <= value <= a.total_score else None
            else:
                value = None
            scores[a.id][score.student_id] = value
    output = {}
    for pk in offerings:
        configured = [a for a in valid.values() if a.offering_id == pk]
        recorded = [a for a in configured if scores[a.id]]
        quizzes = [a for a in recorded if FacultyGradingService.is_quiz_activity(a)]
        cohort = cohorts.get(pk, set())
        comparable = bool(quizzes) and len(cohort) >= (1 if pk in leadership_ids else MIN_STUDENTS) and all(
            set(scores[a.id]) == cohort and all(v is not None for v in scores[a.id].values()) for a in quizzes
        )
        quiz_average = GradingGovernanceService._round(sum(sum(scores[a.id].values()) / len(cohort) for a in quizzes) / len(quizzes)) if comparable else None
        output[pk] = {"quizzes": len(quizzes) if recorded else UNAVAILABLE,
                      "other": len(recorded) - len(quizzes) if recorded else UNAVAILABLE,
                      "quiz_average": str(quiz_average) if quiz_average is not None else UNAVAILABLE,
                      "evidence": "Saved scores from eligible graded students; equally weighted activity percentages." if recorded else "No reliable recorded-activity evidence."}
    return output


def period_trends(selection, offering, current):
    current_ids = {r["student_id"] for r in current["coverage"]["rows"] if r["usable_grade"]}
    output = []
    previous = None
    for period in selection.periods(offering):
        basis = collect_sections(selection, {"academic_year_id": offering.academic_year_id,
            "term_id": offering.term_id, "period_code": period.code})
        section = current if period.id == current["period"].id else basis.get(offering.id)
        if section is None:
            continue
        coverage = section["coverage"]
        ids = {r["student_id"] for r in coverage["rows"] if r["usable_grade"]}
        usable = (offering.id not in selection.hidden_sections(basis) and not section.get("invalid_scope")
                  and ids == current_ids and len(ids) >= (1 if selection.allows_aggregates(offering) else MIN_STUDENTS) and not coverage["is_provisional"]
                  and coverage["computed_grade_count"] == coverage["active_enrollment_count"])
        average = coverage["average"] if usable else None
        # Period weights/components may differ; only identical component definitions are comparable.
        structure = component_signature(period)
        delta = None
        if average is not None and previous and previous[1] == structure:
            delta = average - previous[0]
        output.append({"period": period.name, "average": str(average) if average is not None else UNAVAILABLE,
                       "state": coverage["state"] if usable else "Withheld, incomplete, or different graded cohort",
                       "trend": "Improving" if delta is not None and delta >= 3 else "Declining" if delta is not None and delta <= -3 else "Stable" if delta is not None else UNAVAILABLE})
        previous = (average, structure) if average is not None else None
    canonical = (("PRELIM", "Prelim"), ("MIDTERM", "Midterm"), ("PREFINAL", "Pre-Final"), ("FINAL", "Final"))
    normalize = lambda value: "".join(c for c in value.upper() if c.isalnum())
    ordered = []
    remaining = list(output)
    for key, label in canonical:
        row = next((r for r in remaining if normalize(r["period"]) == key), None)
        if row:
            remaining.remove(row)
        ordered.append(row or {"period": label, "average": UNAVAILABLE, "state": "Period not configured", "trend": UNAVAILABLE})
    return ordered + remaining


def component_signature(period):
    """Include nested weights, attendance and overrides before calling periods comparable."""
    components = period.components.filter(is_active=True).prefetch_related("subcomponents__details").order_by("code")
    return tuple((c.code, str(c.weight_percentage), c.score_input_mode, c.is_exam_component,
                  tuple((s.code, str(s.weight_percentage), s.score_input_mode, s.detail_computation_mode,
                         s.is_attendance_component,
                         tuple(sorted((d.code, str(d.weight_percentage), d.score_input_mode)
                                      for d in s.details.all() if d.is_active)))
                        for s in sorted(c.subcomponents.all(), key=lambda s: s.code) if s.is_active)) for c in components)


def summary_trend(selection, section, structures):
    """Apply the full-scope disclosure plan to both periods of a comparison."""
    coverage = section["coverage"]
    if not coverage["computed_grade_count"] or coverage["is_provisional"] or coverage["computed_grade_count"] != coverage["active_enrollment_count"]:
        return UNAVAILABLE
    periods = selection.periods(section["offering"])
    previous = [p for p in periods if (p.sequence_no, p.id) < (section["period"].sequence_no, section["period"].id)]
    if not previous:
        return UNAVAILABLE
    previous = previous[-1]
    for period in (previous, section["period"]):
        if period.id not in structures:
            structures[period.id] = component_signature(period)
    if structures[previous.id] != structures[section["period"].id]:
        return UNAVAILABLE
    offering = section["offering"]
    basis = collect_sections(selection, {"academic_year_id": offering.academic_year_id,
        "term_id": offering.term_id, "period_code": previous.code})
    baseline = basis.get(offering.id)
    if not baseline or baseline["invalid_scope"] or offering.id in selection.hidden_sections(basis):
        return UNAVAILABLE
    prior = baseline["coverage"]
    current_ids = {r["student_id"] for r in coverage["rows"] if r["usable_grade"]}
    previous_ids = {r["student_id"] for r in prior["rows"] if r["usable_grade"]}
    if prior["is_provisional"] or current_ids != previous_ids or prior["computed_grade_count"] != prior["active_enrollment_count"]:
        return UNAVAILABLE
    delta = coverage["average"] - prior["average"]
    return "Improving" if delta >= 3 else "Declining" if delta <= -3 else "Stable"
