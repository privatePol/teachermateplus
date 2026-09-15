"""Authoritative structured Automatic pool assessment, without mutations."""
from dataclasses import replace

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q

from .contribution_services import QuestionPayloadService
from .models import ExamScenario, ExamScenarioMember, FacultyContribution, QuestionBlueprintPlacement
from .scenario_content import canonicalize_scenario_content
from .duplicate_contract import question_identity


def assess_whole_units(*, blueprint, unit, questions, audit_questions, sections, narrative_cache=None):
    narrative_cache = narrative_cache if narrative_cache is not None else {}
    by_id = {q.id: q for q in questions}
    placements = {p.question_id: p for p in QuestionBlueprintPlacement.objects.filter(
        question_id__in=by_id).select_related("section")}
    section_ids = {s.id for s in sections}
    explicit = blueprint.mode == "USE_SECTIONS"

    def placed(question_id):
        placement = placements.get(question_id)
        if not explicit:
            return placement is None
        return (placement is not None and placement.blueprint_id == blueprint.id
                and placement.section_id in section_ids)

    scenarios = list(ExamScenario.objects.filter(blueprint=blueprint).filter(
        Q(members__question_id__in=by_id) | Q(contribution__status="SUBMITTED")
    ).distinct().select_related("contribution").order_by("id"))
    members = list(ExamScenarioMember.objects.filter(
        Q(scenario_id__in=[scenario.id for scenario in scenarios]) | Q(question_id__in=by_id)
    ).select_related("question__contribution", "question__exam_scenario_membership__scenario", "scenario").order_by("scenario_id", "position", "id"))
    by_scenario = {}
    for member in members:
        # Cross-unit links are an authorization/integrity failure, not a pool
        # shortage that may be bypassed by choosing other questions.
        if (member.scenario.blueprint_id != blueprint.id
                or member.question.contribution.cycle_course_id not in unit.member_ids):
            raise PermissionDenied("Case membership crosses the authorized examination unit.")
        by_scenario.setdefault(member.scenario_id, []).append(member)
    excluded = {}
    warnings = []
    valid = []
    for scenario in scenarios:
        if scenario.contribution_id and scenario.contribution.cycle_course_id not in unit.member_ids:
            raise PermissionDenied("Case ownership crosses the authorized examination unit.")
        rows = by_scenario.get(scenario.id, [])
        ids = [row.question_id for row in rows]
        # Unsubmitted Cases cannot supply questions. Do not expose irrelevant
        # Draft details in readiness; Submitted/partially eligible units warn.
        relevant = bool(set(ids) & set(by_id)) or (
            scenario.contribution_id and scenario.contribution.status == "SUBMITTED")
        reasons = []
        if not ids:
            reasons.append("no linked MCQs")
        if any(qid not in by_id for qid in ids):
            reasons.append("one or more linked MCQs are unusable or not Final Submitted")
        if scenario.contribution_id and (
            scenario.contribution.status != FacultyContribution.Status.SUBMITTED
            or any(row.question.contribution_id != scenario.contribution_id for row in rows)
        ):
            reasons.append("inconsistent contribution ownership or submission")
        if ([row.position for row in rows] != list(range(1, len(rows) + 1))
                or len(ids) != len(set(ids))):
            reasons.append("invalid linked-question order")
        if any(not placed(qid) for qid in ids) or (
            explicit and (scenario.section_id not in section_ids or any(
                placements.get(qid) is None or placements[qid].section_id != scenario.section_id
                for qid in ids))
        ) or (not explicit and scenario.section_id is not None):
            reasons.append("missing or mismatched section placement")
        try:
            fingerprints = [question_identity(row.question, narrative_cache=narrative_cache) for row in rows]
            if len(fingerprints) != len(set(fingerprints)):
                reasons.append("duplicate logical MCQs within the Case")
        except ValidationError:
            reasons.append("invalid Case identity")
        if scenario.content_format == "RICH_HTML_V1":
            try:
                if canonicalize_scenario_content(scenario.stimulus).html != scenario.stimulus:
                    reasons.append("noncanonical rich narrative")
            except ValidationError:
                reasons.append("invalid rich narrative")
        elif scenario.content_format != "PLAIN_TEXT" or not scenario.stimulus.strip():
            reasons.append("invalid narrative format or empty narrative")
        if reasons:
            excluded.update({qid: "UNUSABLE_CASE" for qid in ids})
            if relevant:
                warnings.append({"code": "UNUSABLE_CASE_EXCLUDED",
                                 "message": "An entire Case was excluded: " + "; ".join(reasons) + ".",
                                 "excluded_member_count": len(ids)})
        else:
            valid.append((scenario, rows))
    member_ids = {row.question_id for row in members}
    for qid in by_id.keys() - member_ids:
        if not placed(qid):
            excluded[qid] = "INVALID_SECTION_PLACEMENT"
    standalone_invalid = sum(code == "INVALID_SECTION_PLACEMENT" for code in excluded.values())
    if standalone_invalid:
        warnings.append({"code": "UNPLACED_SINGLETONS_EXCLUDED",
                         "message": f"{standalone_invalid} standalone MCQs with invalid section placement were excluded."})
    audit_questions = [replace(row, eligible_for_generation=False, exclusion_code=excluded[row.source_id])
                       if row.source_id in excluded else row for row in audit_questions]
    return ([q for q in questions if q.id not in excluded], audit_questions,
            {qid: p.section_id for qid, p in placements.items() if qid not in excluded},
            valid, warnings, len(set(by_id) & set(excluded)))
