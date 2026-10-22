"""Period-wide Gradebook measures. No queries, conversion or record mutation.

Private contributor sets enter the shared disclosure plan before any optional
filter. Only released aggregates leave this module; Alignment pairs stay separate.
"""
from collections import Counter

from django.utils.crypto import salted_hmac

from apps.admin_portal.insights_generation import encoded
from apps.grading.services import FacultyGradingService


AVAILABLE = "Available"
MISSING = "Missing / incomplete"
PENDING = "Pending"
NA = "Not applicable"
HISTORICAL = "Unavailable / Unsupported Historical Data"
HISTORICAL_CHANGED = "Unavailable — Historical Definition Changed"
WITHHELD = "Privacy withheld"


def identity(code, period):
    from apps.admin_portal.learning_insights import key
    value = code.upper()
    for prefix in sorted({period.code.upper(), key(period), "PRE_FINAL", "PRE-FINAL"}, key=len, reverse=True):
        if value.startswith(prefix + "_"):
            return value[len(prefix) + 1:]
    return value


def historically_relevant(row, boundary):
    # An inactive definition already retired at submission is not evidence that
    # it participated. A later retirement/edit cannot establish its old state.
    return row.is_active or bool(boundary and row.updated_at > boundary)


def output_branches(period, components, boundary=None):
    """Explicit template identities only; never classify arbitrary non-quiz work.

    A combined configured branch stays combined; separate Participation/Output
    branches stay separate. Duplicate identities cannot establish a rollup.
    """
    branches = []
    for component in components:
        if component.is_exam_component or not historically_relevant(component, boundary):
            continue
        subs = [s for s in component.subcomponents.all() if historically_relevant(s, boundary)]
        component_tokens = set(identity(component.code, period).split("_"))
        whole = bool(component_tokens.intersection({"PARTICIPATION", "OUTPUT"}))
        nodes = [component] if whole else subs or [component]
        for node in nodes:
            if getattr(node, "is_attendance_component", False):
                continue
            code = identity(node.code, period)
            tokens = set(code.split("_"))
            if not tokens.intersection({"PARTICIPATION", "OUTPUT"}) or FacultyGradingService._is_quiz_template_code(code):
                continue
            label = ("Participation/Output" if {"PARTICIPATION", "OUTPUT"} <= tokens
                     else "Participation" if "PARTICIPATION" in tokens else "Output")
            branches.append({"key": code, "label": label, "component": component,
                             "sub": node if subs and not whole else None, "whole": whole})
    counts = Counter(b["key"] for b in branches)
    for branch in branches:
        branch["ambiguous"] = counts[branch["key"]] > 1
    return branches


def definition_path(component, sub, boundary):
    """The branch and ancestors used by the existing independent rollup."""
    rows = [component]
    for child in ([sub] if sub else component.subcomponents.all()):
        if historically_relevant(child, boundary):
            rows.append(child)
            rows.extend(d for d in child.details.all() if historically_relevant(d, boundary))
    return rows


def quiz_definition_path(period, components, boundary):
    rows = []
    for component in components:
        if component.is_exam_component or not historically_relevant(component, boundary):
            continue
        if FacultyGradingService._is_quiz_template_code(component.code):
            rows.extend(definition_path(component, None, boundary))
            continue
        for sub in component.subcomponents.all():
            if historically_relevant(sub, boundary) and any(
                FacultyGradingService._is_quiz_template_code(node.code) for node in
                [sub, *[d for d in sub.details.all() if historically_relevant(d, boundary)]]
            ):
                rows.extend(definition_path(component, sub, boundary))
    return rows


def historical_rollup_state(period, definitions, submission, context):
    """Do not use definitions known to postdate the finalized submission.

    Saved activity scores are already converted. Submission snapshots are not a
    complete formula archive; detect recorded definition drift rather than try
    to reconstruct a missing historical formula or reconvert saved scores.
    """
    if not submission or not submission.submitted_at or context["formula_mode"] != "WEIGHTED_COMPONENTS":
        return HISTORICAL
    snapshot = submission.template_snapshot_json or {}
    if snapshot.get("template_id") not in (None, context["template_id"]):
        return HISTORICAL
    # No complete immutable formula archive exists. A changed relevant row
    # cannot safely establish the submitted weights, mode or applicability.
    if any(row.updated_at > submission.submitted_at for row in [period, period.template, *definitions]):
        return HISTORICAL_CHANGED
    return None


def period_measures(offering, period, context, signature, components, submission,
                    authorized, reason, finalized, active_roster, activities, raw_scores, grade_scores,
                    eligible_roster=None, historical_components=None):
    from apps.admin_portal import learning_insights as learning
    measures = []
    pending = reason in {"Ongoing or future period; not concluded.",
        "Concluded-period submission is not finalized.", "Reopen or correction window is active."}
    blocked = PENDING if pending else MISSING

    def add(code, label, kind, value=None, population=(), applicable=True, state=None):
        state = NA if not applicable else blocked if not authorized else state or (AVAILABLE if value is not None else MISSING)
        measures.append({"code": code, "label": label, "kind": kind,
            "value": value if state == AVAILABLE else None, "state": state,
            "population": set(population) if value is not None and state == AVAILABLE and kind == "grade" else set()})

    eligible_roster = active_roster if eligible_roster is None else eligible_roster
    recorded, quizzes, quiz_cohort = learning.activity_population(activities, raw_scores, eligible_roster)
    historical_components = components if historical_components is None else historical_components
    boundary = submission.submitted_at if submission else None
    quiz_definitions = quiz_definition_path(period, historical_components, boundary)
    quiz_applicable = bool(quiz_definitions)
    quiz_state = historical_rollup_state(period, quiz_definitions, submission, context)
    quiz_values = learning.gradebook_quiz_values(quizzes, grade_scores, quiz_cohort, components) if not quiz_state and authorized else {}
    quiz_population = set(active_roster) if active_roster and set(active_roster) <= quiz_values.keys() else set()
    quiz_mean = learning.average([quiz_values[sid] for sid in quiz_population]) if quiz_population else None
    add("quizzes", "Quiz Count", "count", len({a.pk for a in quizzes}) if quizzes else None, applicable=quiz_applicable)
    add("quiz_grade", "Quiz Average (Gradebook Points)", "grade", quiz_mean, quiz_population,
        applicable=quiz_applicable, state=quiz_state)

    branches = output_branches(period, historical_components, boundary)
    supported = not quiz_state
    if not branches:
        add("output_count", "Participation/Output Activity Count", "count", applicable=False)
        add("output_grade", "Participation/Output Average (Gradebook Points)", "grade", applicable=False)
    for index, branch in enumerate(branches):
        c, sub = branch["component"], branch["sub"]
        branch_state = historical_rollup_state(period, definition_path(c, sub, boundary), submission, context)
        supported = supported and not branch_state
        selected = [a for a in activities if a.template_component_id == c.pk and
                    (branch["whole"] or a.template_subcomponent_id == (sub.pk if sub else None))]
        evidenced = [a for a in selected if not FacultyGradingService.is_quiz_activity(a)
                     and any(raw_scores[a.pk].get(sid) is not None for sid in eligible_roster)]
        # This is the period's complete component contributors, not a matched
        # Alignment pair. An entirely unscored configured activity stays missing.
        population = {sid for sid in finalized if selected and all(raw_scores[a.pk].get(sid) is not None for a in selected)}
        mixed = any(FacultyGradingService.is_quiz_activity(a) for a in selected)
        values = learning.gradebook_quiz_values(selected, grade_scores, population, components) if not branch_state and not branch["ambiguous"] and not mixed and authorized else {}
        population &= values.keys()
        value = learning.average([values[sid] for sid in population]) if population else None
        code = "output_" + branch["key"] + ("_" + str(index) if branch["ambiguous"] else "")
        add(code + "_count", branch["label"] + " Activity Count", "count", len({a.pk for a in evidenced}) if evidenced else None)
        add(code + "_grade", branch["label"] + " Average (Gradebook Points)", "grade", value, population,
            state=branch_state or (MISSING if branch["ambiguous"] else None))

    applicable_components = [c for c in historical_components if historically_relevant(c, boundary)]
    for metric, label, applicable in (
        ("exam", "Examination Average", any(c.is_exam_component for c in applicable_components)),
        ("standing", "Class Standing Average", any(not c.is_exam_component for c in applicable_components)),
        ("periodic", "Periodic Grade Average", bool(applicable_components)),
    ):
        population = {sid for sid, values in finalized.items() if metric in values}
        add(metric, label, "grade", learning.average([finalized[sid][metric] for sid in population]) if population else None,
            population, applicable)
    return {"id": offering.id, "course": offering.course.code, "section": offering.section.code,
        "campus": offering.campus.code, "period": period.name, "period_code": period.code,
        "period_id": period.id, "sequence": period.sequence_no, "measures": measures,
        # An incomplete historical formula archive cannot establish a shared
        # scale across sections, even though their saved official grades remain
        # individually describable. Keep such section groups independent.
        "group": salted_hmac("assessment-table-v1", encoded([
            signature, period.code, None if supported else offering.id]), algorithm="sha256").hexdigest()}


def populations(periods):
    return {oid: [m["population"] for row in rows for m in row["measures"] if m["population"]]
            for oid, rows in periods.items()}


def released(periods, chosen, disclosed, leadership_ids):
    """Strip every contributor set before results reach caches/JSON/templates."""
    output = []
    for offering in chosen:
        for row in periods.get(offering.id, []):
            public = {k: v for k, v in row.items() if k != "measures"}
            public["measures"] = []
            for measure in row["measures"]:
                item = {k: v for k, v in measure.items() if k != "population"}
                allowed = offering.id in leadership_ids or frozenset(measure["population"]) in disclosed
                if measure["population"] and not allowed:
                    item.update(value=None, state=WITHHELD)
                item["contributors"] = len(measure["population"]) if measure["population"] and allowed else None
                public["measures"].append(item)
            output.append(public)
    return output
