"""Aggregate facts from current saved grades; no gradebook writes or recomputation."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db.models import F, Q

from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.core.services.settings import SystemSettingService
from apps.grading.models import GradeActivity, StudentActivityScore
from apps.grading.services import FacultyGradingService


def rounded(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if value is not None else None


def percentage(count, total):
    return rounded(Decimal(count) * 100 / total) if total else Decimal("0.00")


def high_grade_breakdown(values, total, *, disclosed=True):
    """Unrounded saved/configured values; reuse the admitted measure population.

    These are projections of its already releasable exact frequencies, not new
    cohorts. Combined categories overlap and must never be added together.
    """
    categories = [(f"Exactly {grade}", sum(v == grade for v in values)) for grade in range(100, 94, -1)]
    categories.extend([
        ("Combined 95–99", sum(Decimal("95") <= v < Decimal("100") for v in values)),
        ("90 and above", sum(v >= Decimal("90") for v in values)),
    ])
    return [{"grade": label, "count": count if disclosed else None,
             "total_graded_students": total,
             "percentage": percentage(count, total) if disclosed else None}
            for label, count in categories]


def distribution(values, threshold):
    t = Decimal(threshold)
    limits = (t + 15, t, t - 5, t - 15)
    counts = [0] * 5
    for value in values:
        index = next((i for i, lower in enumerate(limits) if value >= lower), 4)
        counts[index] += 1
    fmt = lambda n: format(n.normalize(), "f")
    labels = ("Strongly Above Threshold", "Above Threshold", "Near Threshold", "Below Threshold", "Well Below Threshold")
    ranges = (f"{fmt(t + 15)} and above", f"{fmt(t)} to below {fmt(t + 15)}",
              f"{fmt(t - 5)} to below {fmt(t)}", f"{fmt(t - 15)} to below {fmt(t - 5)}", f"below {fmt(t - 15)}")
    return [{"label": label, "range": interval, "count": count, "percentage": percentage(count, len(values))}
            for label, interval, count in zip(labels, ranges, counts)]


def metrics(values, *, threshold, eligible_count, finalized_count=0, unfinalized_count=0):
    values = [Decimal(v) for v in values if v is not None]
    frequencies = defaultdict(int)
    for value in values:
        frequencies[value] += 1
    count = len(values)
    status = "Mixed" if finalized_count and unfinalized_count else ("Finalized" if finalized_count else ("Unfinalized" if unfinalized_count else "No saved values"))
    return {
        "graded_count": count, "eligible_count": eligible_count,
        "average": rounded(sum(values) / count) if count else None,
        "minimum": min(values) if count else None, "maximum": max(values) if count else None,
        "exact_100_count": frequencies[Decimal("100")], "exact_100_percentage": percentage(frequencies[Decimal("100")], count),
        "all_100": bool(count and frequencies[Decimal("100")] == count),
        "high_grade_breakdown": high_grade_breakdown(values, count),
        "finalized_count": finalized_count, "unfinalized_count": unfinalized_count, "status": status,
        "distribution": distribution(values, threshold),
        "frequency": [{"grade": grade, "count": n, "percentage": percentage(n, count)} for grade, n in sorted(frequencies.items()) if n],
    }


MIN_ANALYTICS_STUDENTS = 5
PRIVACY_MESSAGE = "Detailed grade analytics are unavailable because fewer than five students have saved grades for this selection."
COMPLEMENTARY_MESSAGE = "Detailed grade analytics are withheld to prevent comparison with another selection from revealing grades for fewer than five students."
FORMULA_MESSAGE = "Related grade analytics are withheld because grading formulas could reveal grades for fewer than five students."


class PrivacyFamily:
    """One physical class/period; server-only student sets never enter payloads.

    Decisions use all saved sources before status filtering. Period, saved exam,
    saved standing, then component/activity measures have a fixed priority. A new
    measure is withheld if it would split any already disclosed membership/status
    cell below five; previous decisions never change when detail is requested.
    """
    def __init__(self, status_by_student, status, threshold):
        self.status_by_student = status_by_student
        self.status = status
        self.threshold = threshold
        self._cells = []
        self._covered = set()
        self._populations = set()

    def _admit(self, population):
        population = frozenset(population)
        if population in self._populations:
            return True
        cells = []
        for previous in self._cells:
            cells.extend(part for part in (previous & population, previous - population) if part)
        uncovered = population - self._covered
        for status in (True, False, None):
            part = {sid for sid in uncovered if self.status_by_student.get(sid) is status}
            if part:
                cells.append(part)
        if any(len(cell) < MIN_ANALYTICS_STUDENTS for cell in cells):
            return False
        self._cells = cells
        self._covered.update(population)
        self._populations.add(population)
        return True

    def measure(self, samples, *, eligible_count, forced_reason=""):
        # Dictionaries count distinct students, never repeated activities/revisions.
        samples = {sid: value for sid, value in samples.items() if value is not None}
        chosen = {sid: value for sid, value in samples.items() if self.status == "all"
                  or self.status_by_student.get(sid) is (self.status == "finalized")}
        admitted = not forced_reason and len(samples) >= MIN_ANALYTICS_STUDENTS and self._admit(samples)
        reason = ("minimum_population" if len(chosen) < MIN_ANALYTICS_STUDENTS else "complementary_population")
        if forced_reason and len(chosen) >= MIN_ANALYTICS_STUDENTS:
            reason = forced_reason
        state = "unavailable" if not chosen else ("disclosed" if admitted else "privacy_suppressed")
        finalized = sum(self.status_by_student.get(sid) is True for sid in chosen)
        unfinalized = sum(self.status_by_student.get(sid) is False for sid in chosen)
        if state == "disclosed":
            result = metrics(chosen.values(), threshold=self.threshold, eligible_count=eligible_count,
                             finalized_count=finalized, unfinalized_count=unfinalized)
        else:
            bands = distribution([], self.threshold)
            for band in bands:
                band["count"] = band["percentage"] = None
            result = {"graded_count": len(chosen), "eligible_count": eligible_count,
                      "average": None, "minimum": None, "maximum": None,
                      "exact_100_count": None, "exact_100_percentage": None, "all_100": None,
                      "high_grade_breakdown": high_grade_breakdown([], len(chosen), disclosed=False),
                      "frequency": [], "distribution": bands, "finalized_count": finalized,
                      "unfinalized_count": unfinalized,
                      "status": "Mixed" if finalized and unfinalized else
                          ("Finalized" if finalized else ("Unfinalized" if unfinalized else "No saved values"))}
        if chosen and not finalized and not unfinalized:
            result["status"] = "No saved period status"
        result.update(analytics_state=state, privacy_suppressed=state == "privacy_suppressed",
                      privacy_reason=reason if state == "privacy_suppressed" else "",
                      privacy_message=(PRIVACY_MESSAGE if reason == "minimum_population" else
                                       FORMULA_MESSAGE if reason == "formula_dependency" else COMPLEMENTARY_MESSAGE)
                          if state == "privacy_suppressed" else ("No eligible saved values are available for this measure." if state == "unavailable" else ""))
        return result


class FormulaPrivacyPlan:
    """Plan the entire class/period before releasing any statistic.

    Edges mean potential formula dependence, not equality or additivity. Saved
    bucket sums, weighted period totals, activity averages, raw/configured scores,
    detail weights, rounding and transmutation can all join the same family.
    Exact frequencies make a sum-only linear-rank check insufficient. Withhold
    every exposed ancestor of a protected source, plus ambiguous/invertible
    equivalents. Safe sibling branches remain available once their shared total
    is withheld. Cutting all ancestor paths also prevents systems of equations
    involving several totals at different weights, without suppressing siblings.
    """
    def __init__(self, privacy):
        self.privacy = privacy
        self.samples = {}
        self.edges = defaultdict(set)
        self.blocked = set()
        self.population_protected = set()

    def add(self, key, samples):
        self.samples[key] = {sid: value for sid, value in samples.items() if value is not None}

    def link(self, first, second):
        # Bidirectional ambiguity or equivalence: neither can mask the other.
        self.edges[first].add(second)
        self.edges[second].add(first)

    def depends_on(self, total, source):
        self.edges[source].add(total)

    def finalize(self, *, unresolved=False):
        probe = PrivacyFamily(self.privacy.status_by_student, "all", self.privacy.threshold)
        # Full-source, fixed-order check, independent of requested status/route.
        protected = set(self.samples) if unresolved else set()
        for key, samples in self.samples.items():
            if samples and len(samples) < MIN_ANALYTICS_STUDENTS:
                protected.add(key)
            elif samples and not probe._admit(samples):
                protected.add(key)
                self.population_protected.add(key)
        pending = list(protected)
        self.blocked = set(protected)
        while pending:
            for neighbor in self.edges[pending.pop()]:
                if neighbor not in self.blocked:
                    self.blocked.add(neighbor)
                    pending.append(neighbor)

    def measure(self, key, *, eligible_count):
        return self.privacy.measure(self.samples[key], eligible_count=eligible_count,
            forced_reason=("complementary_population" if key in self.population_protected else "formula_dependency")
                if key in self.blocked else "")


def threshold_for(offering):
    try:
        trace = FacultyGradingService.resolve_passing_threshold_trace(offering)
        return trace["value"], trace["source_label"]
    except ValidationError:
        raw = SystemSettingService.get("PASSING_GRADE_THRESHOLD", tenant_id=offering.tenant_id, default=None)
        try:
            value = Decimal(str(raw))
            if not value.is_finite():
                raise ValueError
            return rounded(value), "Tenant threshold setting (template unavailable)"
        except (ValueError, ArithmeticError):
            return Decimal("75.00"), "System default (template unavailable)"


def dataset_metadata(offering, period, key, status, *, activity=None):
    """Source records and status selection identify a dataset, never its label."""
    source = f"activity:{activity.id}" if activity else key
    context = f"{period.name}; Period #{period.id}"
    if activity:
        route = [activity.template_component.name]
        if activity.template_subcomponent:
            route.append(activity.template_subcomponent.name)
        if activity.template_detail:
            route.append(activity.template_detail.name)
        context += f"; {' / '.join(route)}; Activity #{activity.id}"
    return {
        "dataset_id": f"offering:{offering.id}/period:{period.id}/{source}/status:{status}",
        "offering_id": offering.id, "physical_period_id": period.id,
        "activity_id": activity.id if activity else None,
        "selection_status": status, "gradebook_context": context,
    }


def component_measures(offering, period, saved_rows, student_ids, threshold, historical, privacy):
    """Keep saved totals distinct from current engine interpretations and raw scores."""
    measures, notes = [], []
    activity_map = {}
    plan = FormulaPrivacyPlan(privacy)
    plan.add("period_grade", {r["student_id"]: r["period_grade"] for r in saved_rows})
    selected_ids = student_ids if privacy.status == "all" else {r["student_id"] for r in saved_rows if r["is_finalized"] == (privacy.status == "finalized")}

    def add_measure(key, values, label, unit):
        plan.add(key, values)
        measures.append((key, label, unit))

    def finish(*, unresolved=False, unknown_links=False, incomplete=None):
        if unknown_links:
            # Original/current lineage cannot prove independence of the buckets.
            plan.link("period_grade", "exam_grade")
            plan.link("period_grade", "class_standing_grade")
        plan.finalize(unresolved=unresolved)
        period_metadata = dataset_metadata(offering, period, "period_grade", privacy.status)
        period_metrics = {**plan.measure("period_grade", eligible_count=len(student_ids)), **period_metadata}
        released = []
        disclosed_sources = [("period_grade", "Period Grades", period_metadata)] if period_metrics["analytics_state"] == "disclosed" else []
        for key, label, unit in measures:
            # Intermediate engine components protect formulas but duplicate the
            # gradebook's saved totals/activity columns in the public report.
            if isinstance(key, tuple) and key[0] == "component":
                continue
            metadata = dataset_metadata(offering, period, key, privacy.status,
                activity=activity_map[key[1]] if isinstance(key, tuple) else None)
            measure = {"label": label, "unit": unit, **plan.measure(key, eligible_count=len(selected_ids)), **metadata}
            # Compare full student/value maps, never rounded aggregate averages.
            # Suppressed sources cannot disclose an equivalence relationship.
            if measure["analytics_state"] == "disclosed":
                equivalent = next(((prior_label, prior_metadata) for prior_key, prior_label, prior_metadata in disclosed_sources
                    if plan.samples[key] == plan.samples[prior_key]), None)
                measure["equivalent_to"] = equivalent[0] if equivalent else ""
                measure["equivalent_dataset_id"] = equivalent[1]["dataset_id"] if equivalent else ""
                measure["equivalent_context"] = equivalent[1]["gradebook_context"] if equivalent else ""
                if not equivalent:
                    disclosed_sources.append((key, label, metadata))
            released.append(measure)
        return period_metrics, released, notes, incomplete

    for field, label in (("exam_grade", "Examination Grades"), ("class_standing_grade", "Class Standing Grades")):
        values = {r["student_id"]: r[field] for r in saved_rows if r[field] is not None}
        add_measure(field, values, label, "Stored grade total; may exceed 100 when major components are summed.")
    # Gradebook activity columns read computed_score, including configured Base
    # 50. Raw inputs remain internal sources in the formula privacy plan.
    activities = list(GradeActivity.objects.filter(offering=offering, template_period=period, is_active=True,
                    template_component__template_period=period,
                    tenant_id=offering.tenant_id, campus_id=offering.campus_id).filter(
                        Q(template_subcomponent__isnull=True) | Q(
                          template_subcomponent__template_component_id=F("template_component_id"))
                    ).filter(Q(template_detail__isnull=True) | Q(
                          template_detail__template_subcomponent_id=F("template_subcomponent_id"))).select_related(
                        "template_component", "template_subcomponent", "template_detail").order_by("id")[:201])
    if len(activities) > 200:
        notes.append("Activity analysis limit: 200 activities per class/period. Formula safety cannot be established; related analytics are withheld.")
        return finish(unresolved=True, unknown_links=True)
    activity_map = {a.id: a for a in activities}
    scores_qs = StudentActivityScore.objects.filter(activity_id__in=[a.id for a in activities], student_id__in=student_ids, is_active=True).order_by()
    if scores_qs.count() > 20000:
        notes.append("Activity analysis limit: 20,000 saved assessment scores per class/period. Formula safety cannot be established; related analytics are withheld.")
        return finish(unresolved=True, unknown_links=True)
    scores = list(scores_qs.values("student_id", "activity_id", "raw_score", "computed_score"))
    inactive_routes = any(not a.template_component.is_active or
        (a.template_subcomponent and not a.template_subcomponent.is_active) or
        (a.template_detail and not a.template_detail.is_active) for a in activities)
    if inactive_routes:
        notes.append("An active activity has an inactive grading route. Its formula lineage cannot be verified; related analytics are withheld.")
    if historical:
        for activity in activities:
            configured_key, input_key = ("configured", activity.id), ("raw_input", activity.id)
            add_measure(configured_key, {r["student_id"]: r["computed_score"] for r in scores if r["activity_id"] == activity.id},
                        activity.title, "Saved Gradebook activity grade (100-point scale).")
            plan.add(input_key, {r["student_id"]: r["raw_score"] for r in scores if r["activity_id"] == activity.id})
            plan.link(configured_key, input_key)
            # Original formulas are unavailable. Every retained activity may
            # feed any saved bucket; do not assert historical independence.
            for total in ("period_grade", "exam_grade", "class_standing_grade"):
                plan.depends_on(total, configured_key)
        notes.append("Historical analytics use retained saved Gradebook totals and activity grades. Original formulas, activity maxima and score revisions are not snapshotted; current saved activity entries may reflect later edits. No historical calculation is invented.")
        return finish(unresolved=inactive_routes, unknown_links=True)
    try:
        template = FacultyGradingService.resolve_template_for_offering(offering)
        if template.id != period.template_id:
            raise ValidationError("The saved physical period differs from the current template.")
        base = FacultyGradingService.resolve_base_value(offering, template)
        strategy = FacultyGradingService.resolve_period_grade_strategy(offering, template=template)
    except ValidationError:
        notes.append("Current component configuration cannot reliably interpret this saved period. Formula independence cannot be established; related analytics are withheld and underlying source completeness is unknown.")
        return finish(unresolved=True, unknown_links=True)
    components = list(period.components.filter(is_active=True).prefetch_related("subcomponents", "subcomponents__details").order_by("sort_order", "id"))
    lookup, raw_lookup, present = defaultdict(list), defaultdict(list), defaultdict(set)
    component_present = defaultdict(set)
    activity_values, actual_values, raw_input_values = defaultdict(dict), defaultdict(dict), defaultdict(dict)
    for score in scores:
        activity = activity_map[score["activity_id"]]
        if score["computed_score"] is not None:
            lookup[(score["student_id"], activity.template_component_id, activity.template_subcomponent_id, activity.template_detail_id)].append(score["computed_score"])
            present[score["student_id"]].add(activity.id)
            component_present[score["student_id"]].add(activity.template_component_id)
            activity_values[activity.id][score["student_id"]] = score["computed_score"]
        mode = FacultyGradingService.resolve_score_input_mode(template_component=activity.template_component,
                    template_subcomponent=activity.template_subcomponent, template_detail=activity.template_detail)
        if score["raw_score"] is not None:
            raw_input_values[activity.id][score["student_id"]] = score["raw_score"]
            raw_lookup[(score["student_id"], activity.template_component_id)].append((score["raw_score"], activity.total_score))
            if mode == "DIRECT_PERCENTAGE":
                actual_values[activity.id][score["student_id"]] = score["raw_score"]
            elif activity.total_score > 0:
                # Preserve unrounded boundaries and exact-100 facts. Never clamp.
                actual_values[activity.id][score["student_id"]] = score["raw_score"] * 100 / activity.total_score
    has_attendance = any(sub.is_attendance_component for c in components for sub in c.subcomponents.all() if sub.is_active)
    attendance_scores, attendance_present, session_ids = defaultdict(list), defaultdict(set), set()
    attendance_values = defaultdict(dict)
    if has_attendance:
        session_ids = set(AttendanceSession.objects.filter(offering=offering, template_period=period, is_active=True,
                          tenant_id=offering.tenant_id, campus_id=offering.campus_id).values_list("id", flat=True)[:201])
        records = AttendanceRecord.objects.filter(session_id__in=session_ids, student_id__in=student_ids, is_active=True).order_by()
        if len(session_ids) > 200 or records.count() > 20000:
            notes.append("Component analysis limit: 200 attendance sessions / 20,000 records. Formula safety cannot be established; related analytics are withheld.")
            return finish(unresolved=True, unknown_links=True)
        for record in records.values("student_id", "session_id", "status_code"):
            attendance_scores[record["student_id"]].append(FacultyGradingService.compute_activity_score(
                raw_score=FacultyGradingService.ATTENDANCE_SCORE_MAP.get(record["status_code"], Decimal("0")),
                total_score=Decimal("100"), base_value=base, score_input_mode="DIRECT_PERCENTAGE"))
            attendance_present[record["student_id"]].add(record["session_id"])
            attendance_values[record["session_id"]][record["student_id"]] = attendance_scores[record["student_id"]][-1]
        for component in components:
            if any(sub.is_active and sub.is_attendance_component for sub in component.subcomponents.all()):
                for sid in attendance_scores:
                    component_present[sid].add(component.id)

    class BatchedReadEngine(FacultyGradingService):
        # Adapter for the engine's only per-student DB source. The authoritative
        # status mapping, activity-score function, mean and rounding are unchanged.
        @classmethod
        def _attendance_subcomponent_score(cls, *, student_id, **kwargs):
            values = attendance_scores.get(student_id, [])
            return cls._round(sum(values) / len(values)) if values else None

    expected = {a.id for a in activities}
    required_routes = set()
    for component in components:
        subs = [s for s in component.subcomponents.all() if s.is_active]
        if not subs:
            required_routes.add((component.id, None, None))
        for sub in subs:
            if sub.is_attendance_component:
                continue
            details = [d for d in sub.details.all() if d.is_active]
            required_routes.update((component.id, sub.id, d.id) for d in details)
            if not details:
                required_routes.add((component.id, sub.id, None))
    present_routes = defaultdict(set)
    for sid, component_id, sub_id, detail_id in lookup:
        present_routes[sid].add((component_id, sub_id, detail_id))
    incomplete = sum(
        (not expected and not session_ids) or not expected.issubset(present[sid])
        or not required_routes.issubset(present_routes[sid])
        or (has_attendance and (not session_ids or not session_ids.issubset(attendance_present[sid])))
        for sid in selected_ids
    )
    component_values = defaultdict(dict)
    engine_totals = {}
    for sid in student_ids:
        detail = BatchedReadEngine.build_period_grade_detail_for_student(
            offering=offering, template_period=period, student_id=sid, template=template, base_value=base,
            components=components, score_lookup=lookup, raw_score_lookup=raw_lookup, period_grade_strategy=strategy,
        )
        engine_totals[sid] = {"period_grade": detail["period_grade"], "exam_grade": detail["exam_grade"],
                              "class_standing_grade": detail["class_standing"]}
        for component in detail["component_breakdown"]:
            if component["score"] is not None and component["id"] in component_present[sid]:
                component_values[component["id"]][sid] = component["score"]
    for component in components:
        category = "Examination" if FacultyGradingService.is_exam_component(component) else "Class standing"
        key = ("component", component.id)
        add_measure(key, component_values[component.id], f"{category} - {component.name}",
                    f"Current engine component grade, weight {component.weight_percentage}%; not a saved historical snapshot or weighted contribution.")
        plan.depends_on("exam_grade" if FacultyGradingService.is_exam_component(component) else "class_standing_grade", key)
        if component.weight_percentage:
            plan.depends_on("period_grade", key)
        if any(sub.is_active and sub.is_attendance_component for sub in component.subcomponents.all()):
            for session_id, values in attendance_values.items():
                source = ("attendance", session_id)
                plan.add(source, values)
                plan.depends_on(key, source)
    for activity in activities:
        # Check both raw and configured sources, including non-additive formulas.
        raw_key, configured_key = ("raw", activity.id), ("configured", activity.id)
        input_key = ("raw_input", activity.id)
        plan.add(raw_key, actual_values[activity.id])
        plan.add(configured_key, activity_values[activity.id])
        plan.add(input_key, raw_input_values[activity.id])
        plan.depends_on(("component", activity.template_component_id), raw_key)
        plan.depends_on(("component", activity.template_component_id), configured_key)
        plan.depends_on(("component", activity.template_component_id), input_key)
        plan.link(raw_key, configured_key)
        plan.link(raw_key, input_key)
        measures.append((configured_key, activity.title,
                         "Saved Gradebook activity grade (100-point scale), including the configured base. A grade of 100 need not mean a perfect raw score."))
    lineage_matches = all(all(r[field] == engine_totals[r["student_id"]][field]
                              for field in ("period_grade", "exam_grade", "class_standing_grade")) for r in saved_rows)
    notes.append("Activity analytics use saved Gradebook grades, including the configured base. The read-only grading engine checks formula dependencies internally; analytics do not recompute or overwrite saved grades. Exactly 100 need not mean a perfect raw score.")
    if not lineage_matches:
        notes.append("Saved totals differ from current assessment interpretations. Their formula independence cannot be verified; related saved buckets are treated as potentially dependent.")
    return finish(unresolved=inactive_routes, unknown_links=not lineage_matches, incomplete=incomplete)
