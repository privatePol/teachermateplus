from __future__ import annotations

from collections import Counter, defaultdict

from django.core.exceptions import ValidationError
from django.db.models import Prefetch

from .blueprint_services import ContributorRosterReadinessService
from .contribution_services import QuestionPayloadService
from .generation_algorithms import (
    AllocationError,
    CAMPUS_WEIGHTS,
    allocate_campuses,
    allocate_difficulties,
    solve_two_set_feasibility,
)
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExamBlueprint,
    ExamScenario,
    ExamScenarioMember,
    ExamSection,
    FacultyContribution,
    Question,
    QuestionBlueprintPlacement,
    QuestionImportBatch,
)


def eligible_submitted_question_pool(*, cycle_course, participating_codes):
    """Return the content-valid Submitted pool without exposing it to aggregate callers."""
    submitted_questions = list(
        Question.objects.filter(
            contribution__cycle_course=cycle_course,
            contribution__status=FacultyContribution.Status.SUBMITTED,
        )
        .select_related("contribution__source_campus", "import_batch")
        .order_by("id")
    )
    participating_set = {
        (code or "").strip().upper() for code in participating_codes
    }
    if not participating_set or participating_set - set(CAMPUS_WEIGHTS):
        return [], len(submitted_questions)
    eligible_questions = []
    invalid_question_count = 0
    for question in submitted_questions:
        campus_code = (question.contribution.source_campus.code or "").strip().upper()
        try:
            QuestionPayloadService.validate(
                {
                    "question_text": question.question_text,
                    "choice_a": question.choice_a,
                    "choice_b": question.choice_b,
                    "choice_c": question.choice_c,
                    "choice_d": question.choice_d,
                    "correct_answer": question.correct_answer,
                    "difficulty": question.difficulty,
                }
            )
        except ValidationError:
            invalid_question_count += 1
            continue
        if campus_code not in participating_set:
            invalid_question_count += 1
            continue
        if (
            question.import_batch_id
            and question.import_batch.status != QuestionImportBatch.Status.CONFIRMED
        ):
            invalid_question_count += 1
            continue
        question.stage6_campus_code = campus_code
        eligible_questions.append(question)
    return eligible_questions, invalid_question_count


class Stage6ReadinessService:
    """Read-only aggregate Stage 6A readiness and exact feasibility."""

    @staticmethod
    def _block(blockers, code, message, **details):
        if code not in {item["code"] for item in blockers}:
            blockers.append({"code": code, "message": message, **details})

    @classmethod
    def evaluate(cls, *, cycle_course):
        blockers = []
        shortages = []
        configuration = CourseExamConfiguration.objects.filter(
            cycle_course=cycle_course
        ).first()
        blueprint = ExamBlueprint.objects.filter(cycle_course=cycle_course).first()

        if cycle_course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED:
            cls._block(blockers, "NOT_INCLUDED", "The course examination is Exempt.")
        if configuration is None:
            cls._block(blockers, "CONFIGURATION_MISSING", "Course examination configuration is required.")
        else:
            if configuration.final_item_count is None or not 50 <= configuration.final_item_count <= 75:
                cls._block(blockers, "FINAL_COUNT_INVALID", "Final item count must be from 50 to 75.")
            if (
                configuration.easy_percent,
                configuration.moderate_percent,
                configuration.difficult_percent,
            ) != (30, 50, 20):
                cls._block(blockers, "DIFFICULTY_POLICY_INVALID", "Difficulty configuration must be exactly 30/50/20.")
            if configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.CLOSED:
                cls._block(blockers, "CONTRIBUTION_NOT_CLOSED", "Faculty contribution must be Closed.")

        roster = None
        if configuration is not None:
            roster = ContributorRosterReadinessService.evaluate(
                cycle_course=cycle_course, configuration=configuration
            )
            if not roster.current:
                cls._block(
                    blockers,
                    "ROSTER_STALE",
                    "The contributor roster is stale; synchronize it through the authorized workflow.",
                )
            if roster.incomplete_active_count:
                cls._block(
                    blockers,
                    "ACTIVE_CONTRIBUTORS_INCOMPLETE",
                    "Currently required Active contributors have not all Submitted.",
                    required=roster.required_active_count,
                    submitted=roster.submitted_required_count,
                )
            if roster.unresolved_blocked_count:
                cls._block(
                    blockers,
                    "BLOCKED_DRAFTS_UNRESOLVED",
                    "Every current Blocked Draft requires explicit resolution.",
                    unresolved=roster.unresolved_blocked_count,
                )

        participating_codes = tuple(
            dict.fromkeys(
                (code or "").strip().upper()
                for code in cycle_course.offering_snapshots.order_by(
                    "campus__code", "campus_id"
                ).values_list("campus__code", flat=True)
            )
        )
        campus_quotas = {}
        difficulty_quotas = {}
        final_count = configuration.final_item_count if configuration else None
        if final_count is not None and 50 <= final_count <= 75:
            try:
                campus_quotas = allocate_campuses(final_count, participating_codes)
            except AllocationError as exc:
                cls._block(blockers, "CAMPUS_CODE_INVALID", str(exc))
            difficulty_quotas = allocate_difficulties(final_count)

        section_rows = []
        section_quotas = {}
        if blueprint is None:
            cls._block(blockers, "BLUEPRINT_MISSING", "An examination blueprint is required.")
        elif blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS:
            if blueprint.sections.exists():
                cls._block(blockers, "BLUEPRINT_STRUCTURE_INVALID", "No Sections mode cannot retain explicit sections.")
            if final_count is not None:
                section_quotas = {0: final_count}
        else:
            section_rows = list(blueprint.sections.order_by("display_order", "id"))
            orders = [section.display_order for section in section_rows]
            if (
                not section_rows
                or any(section.item_quota <= 0 or section.display_order <= 0 for section in section_rows)
                or len(orders) != len(set(orders))
            ):
                cls._block(blockers, "BLUEPRINT_STRUCTURE_INVALID", "Use Sections requires positive, uniquely ordered sections.")
            section_quotas = {section.id: section.item_quota for section in section_rows}
            if final_count is not None and sum(section_quotas.values()) != final_count:
                cls._block(blockers, "SECTION_QUOTA_INVALID", "Section quotas must equal the final item count exactly.")

        eligible_questions, invalid_question_count = eligible_submitted_question_pool(
            cycle_course=cycle_course,
            participating_codes=participating_codes,
        )
        if invalid_question_count:
            cls._block(
                blockers,
                "ELIGIBLE_POOL_INVALID",
                "Submitted question rows with invalid payload or frozen campus evidence were excluded.",
                invalid_count=invalid_question_count,
            )

        eligible_ids = {question.id for question in eligible_questions}
        placement_by_question = {}
        if blueprint is not None and blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS:
            placements = list(
                QuestionBlueprintPlacement.objects.filter(
                    blueprint=blueprint, question_id__in=eligible_ids
                ).select_related("section")
            )
            placement_by_question = {
                placement.question_id: placement.section_id for placement in placements
            }
            invalid_placements = sum(
                1
                for placement in placements
                if placement.section.blueprint_id != blueprint.id
                or placement.question.contribution.cycle_course_id != cycle_course.id
            )
            missing = len(eligible_ids - set(placement_by_question))
            if missing or invalid_placements:
                cls._block(
                    blockers,
                    "QUESTION_PLACEMENTS_INCOMPLETE",
                    "Every eligible Submitted question requires one valid section placement.",
                    required=len(eligible_ids),
                    placed=len(eligible_ids) - missing,
                    invalid=invalid_placements,
                )

        scenario_vectors = []
        scenario_question_ids = set()
        invalid_scenario_count = 0
        scenarios = []
        if blueprint is not None:
            scenarios = list(
                ExamScenario.objects.filter(blueprint=blueprint)
                .select_related("section")
                .prefetch_related(
                    Prefetch(
                        "members",
                        queryset=ExamScenarioMember.objects.select_related(
                            "question__contribution__source_campus"
                        ).order_by("position", "id"),
                    )
                )
                .order_by("id")
            )
        valid_scenario_members = []
        for scenario in scenarios:
            members = list(scenario.members.all())
            member_ids = [member.question_id for member in members]
            valid = bool((scenario.stimulus or "").strip())
            valid = valid and len(members) >= 2 and len(member_ids) == len(set(member_ids))
            valid = valid and all(question_id in eligible_ids for question_id in member_ids)
            if blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS:
                valid = valid and scenario.section_id in section_quotas
                valid = valid and all(
                    placement_by_question.get(question_id) == scenario.section_id
                    for question_id in member_ids
                )
            else:
                valid = valid and scenario.section_id is None
            if scenario_question_ids.intersection(member_ids):
                valid = False
            if not valid:
                invalid_scenario_count += 1
                continue
            scenario_question_ids.update(member_ids)
            valid_scenario_members.append(members)
        if invalid_scenario_count:
            cls._block(
                blockers,
                "SCENARIOS_INVALID",
                "Incomplete or cross-scope scenarios block readiness.",
                invalid_count=invalid_scenario_count,
            )

        available_by_campus = Counter(
            question.stage6_campus_code for question in eligible_questions
        )
        available_by_difficulty = Counter(
            question.difficulty for question in eligible_questions
        )
        available_by_section = Counter()
        if blueprint is not None and blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS:
            available_by_section[0] = len(eligible_questions)
        else:
            available_by_section.update(placement_by_question.values())

        if final_count is not None and len(eligible_questions) < final_count:
            shortages.append(
                {"dimension": "total", "label": "Total", "required": final_count, "available": len(eligible_questions)}
            )
        for code, required in campus_quotas.items():
            available = available_by_campus[code]
            if available < required:
                shortages.append({"dimension": "campus", "label": code.title(), "required": required, "available": available})
        for code, required in difficulty_quotas.items():
            available = available_by_difficulty[code]
            if available < required:
                shortages.append({"dimension": "difficulty", "label": code.title(), "required": required, "available": available})
        section_labels = {section.id: section.title for section in section_rows}
        section_labels[0] = "Questionnaire"
        for section_id, required in section_quotas.items():
            available = available_by_section[section_id]
            if available < required:
                shortages.append({"dimension": "section", "label": section_labels.get(section_id, "Section"), "required": required, "available": available})
        if shortages:
            cls._block(blockers, "QUESTION_SHORTAGES", "The eligible Submitted pool has aggregate shortages.")

        solver_result = None
        structural_codes = {
            "NOT_INCLUDED",
            "CONFIGURATION_MISSING",
            "FINAL_COUNT_INVALID",
            "DIFFICULTY_POLICY_INVALID",
            "CONTRIBUTION_NOT_CLOSED",
            "ROSTER_STALE",
            "ACTIVE_CONTRIBUTORS_INCOMPLETE",
            "BLOCKED_DRAFTS_UNRESOLVED",
            "CAMPUS_CODE_INVALID",
            "BLUEPRINT_MISSING",
            "BLUEPRINT_STRUCTURE_INVALID",
            "SECTION_QUOTA_INVALID",
            "ELIGIBLE_POOL_INVALID",
            "QUESTION_PLACEMENTS_INCOMPLETE",
            "SCENARIOS_INVALID",
            "QUESTION_SHORTAGES",
        }
        if not ({item["code"] for item in blockers} & structural_codes):
            campus_order = tuple(campus_quotas)
            difficulty_order = ("EASY", "MODERATE", "DIFFICULT")
            section_order = tuple(section_quotas)

            def vector_for(question):
                section_key = (
                    0
                    if blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS
                    else placement_by_question[question.id]
                )
                return (
                    1,
                    *(1 if question.stage6_campus_code == key else 0 for key in campus_order),
                    *(1 if question.difficulty == key else 0 for key in difficulty_order),
                    *(1 if section_key == key else 0 for key in section_order),
                )

            question_vectors = {question.id: vector_for(question) for question in eligible_questions}
            for members in valid_scenario_members:
                scenario_vectors.append(
                    tuple(
                        sum(question_vectors[member.question_id][position] for member in members)
                        for position in range(len(next(iter(question_vectors.values()))))
                    )
                )
            singleton_capacities = Counter(
                question_vectors[question.id]
                for question in eligible_questions
                if question.id not in scenario_question_ids
            )
            margins = (
                final_count,
                *(campus_quotas[key] for key in campus_order),
                *(difficulty_quotas[key] for key in difficulty_order),
                *(section_quotas[key] for key in section_order),
            )
            solver_result = solve_two_set_feasibility(
                margins=margins,
                scenario_vectors=scenario_vectors,
                singleton_capacities=singleton_capacities,
            )
            if solver_result.limit_hit:
                cls._block(
                    blockers,
                    "FEASIBILITY_LIMIT",
                    "The deterministic feasibility state limit was reached.",
                )
            elif not solver_result.feasible:
                cls._block(
                    blockers,
                    "HARD_CONSTRAINTS_INFEASIBLE",
                    "Two equivalent sets cannot satisfy all hard margins and scenario bundles.",
                )

        return {
            "ready": not blockers,
            "status": "READY" if not blockers else "BLOCKED",
            "blockers": blockers,
            "shortages": shortages,
            "campus_quotas": campus_quotas,
            "difficulty_quotas": difficulty_quotas,
            "section_quotas": [
                {
                    "id": section_id,
                    "label": section_labels.get(section_id, "Section"),
                    "required": required,
                    "available": available_by_section[section_id],
                }
                for section_id, required in section_quotas.items()
            ],
            "eligible_question_count": len(eligible_questions),
            "invalid_question_count": invalid_question_count,
            "contributor_counts": {
                "required_active": roster.required_active_count if roster else 0,
                "submitted_required": roster.submitted_required_count if roster else 0,
                "blocked_drafts": roster.blocked_draft_count if roster else 0,
                "unresolved_blocked": roster.unresolved_blocked_count if roster else 0,
            },
            "scenario_count": len(scenarios),
            "minimum_overlap": solver_result.minimum_overlap if solver_result else None,
            "solver_states": solver_result.states_explored if solver_result else 0,
            "solver_limit_hit": solver_result.limit_hit if solver_result else False,
        }
