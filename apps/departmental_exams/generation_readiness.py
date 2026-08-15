from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db.models import Prefetch

from .blueprint_services import (
    STAGE6_CYCLE_NOT_OPEN_CODE,
    STAGE6_CYCLE_NOT_OPEN_MESSAGE,
    ContributorRosterReadinessService,
    stage6_cycle_is_open,
)
from .contribution_services import QuestionPayloadService
from .generation_algorithms import (
    AllocationError,
    CAMPUS_WEIGHTS,
    IdentityBlock,
    IdentityMember,
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
    ExaminationCycle,
    FacultyContribution,
    Question,
    QuestionBlueprintPlacement,
    QuestionImportBatch,
)
from .stage6_campus_codes import (
    Stage6CampusCodeAmbiguity,
    canonicalize_participating_campus_rows,
    canonicalize_stage6_campus_code,
)


GENERATION_ALGORITHM_VERSION = "stage6b-v1"
# Existing immutable revision rows require a positive structure snapshot.
# This marks the non-persistent Automatic flat contract; it is not a Blueprint PK.
AUTOMATIC_FLAT_STRUCTURE_REVISION = 1


def _sha256_json(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GenerationQuestion:
    source_id: int
    source_revision: int
    source_digest: str
    contributor_id: int
    contributor_name: str
    campus_id: int
    campus_code: str
    campus_name: str
    difficulty: str
    section_id: int
    section_title: str
    section_instructions: str
    normalized_fingerprint: str
    question_text: str
    choices: tuple[str, str, str, str]
    correct_answer: str
    scenario_id: int | None = None
    scenario_revision: int | None = None
    scenario_title: str = ""
    scenario_stimulus: str = ""
    scenario_member_position: int | None = None


@dataclass(frozen=True)
class GenerationProblem:
    cycle_course: object
    configuration: object
    blueprint: object
    final_count: int
    margins: tuple[int, ...]
    campus_quotas: dict
    difficulty_quotas: dict
    section_quotas: dict
    section_order: tuple[int, ...]
    questions: dict[int, GenerationQuestion]
    blocks: tuple[IdentityBlock, ...]
    input_fingerprint: str
    configuration_revision: int
    blueprint_revision: int
    roster_boundary: str
    minimum_overlap: int


def eligible_submitted_question_pool(
    *, cycle_course, participating_codes=None, participating_campus_ids=None
):
    """Return the content-valid Submitted pool without exposing it to aggregate callers."""
    if (participating_codes is None) == (participating_campus_ids is None):
        raise ValueError(
            "Provide exactly one participating-campus identity representation."
        )
    submitted_questions = list(
        Question.objects.filter(
            contribution__cycle_course=cycle_course,
            contribution__status=FacultyContribution.Status.SUBMITTED,
        )
        .select_related(
            "contribution__source_campus",
            "contribution__faculty_user",
            "import_batch",
        )
        .order_by("id")
    )
    if participating_campus_ids is not None:
        participating_set = {int(campus_id) for campus_id in participating_campus_ids}
        if not participating_set:
            return [], len(submitted_questions)
        campus_is_participating = (
            lambda question: question.contribution.source_campus_id in participating_set
        )
    else:
        participating_set = {
            canonicalize_stage6_campus_code(code) for code in participating_codes
        }
        if not participating_set or participating_set - set(CAMPUS_WEIGHTS):
            return [], len(submitted_questions)
        campus_is_participating = (
            lambda question: canonicalize_stage6_campus_code(
                question.contribution.source_campus.code
            )
            in participating_set
        )
    eligible_questions = []
    invalid_question_count = 0
    for question in submitted_questions:
        campus_code = canonicalize_stage6_campus_code(
            question.contribution.source_campus.code
        )
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
        if not campus_is_participating(question):
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


def automatic_campus_allocate(*, total, campus_ids):
    """Evenly allocate an Automatic questionnaire across actual Campus records."""
    ordered_ids = tuple(sorted({int(campus_id) for campus_id in campus_ids}))
    if not ordered_ids:
        return {}
    base, remainder = divmod(total, len(ordered_ids))
    return {
        campus_id: base + (1 if index < remainder else 0)
        for index, campus_id in enumerate(ordered_ids)
    }


def automatic_logical_question_groups(questions):
    """Return every eligible row grouped by its Automatic logical identity."""
    by_fingerprint = defaultdict(list)
    for question in questions:
        by_fingerprint[
            QuestionPayloadService.question_fingerprint(question.question_text)
        ].append(question)
    return {
        fingerprint: tuple(sorted(rows, key=lambda question: question.id))
        for fingerprint, rows in sorted(by_fingerprint.items())
    }


class Stage6ReadinessService:
    """Read-only aggregate Stage 6A readiness and exact feasibility."""

    @staticmethod
    def _block(blockers, code, message, **details):
        if code not in {item["code"] for item in blockers}:
            blockers.append({"code": code, "message": message, **details})

    @staticmethod
    def _warn(warnings, code, message, **details):
        if code not in {item["code"] for item in warnings}:
            warnings.append({"code": code, "message": message, **details})

    @classmethod
    def evaluate(cls, *, cycle_course):
        _problem, report = cls.build_problem(cycle_course=cycle_course)
        return report

    @classmethod
    def build_problem(cls, *, cycle_course):
        blockers = []
        warnings = []
        shortages = []
        automatic_flat_mode = (
            cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        configuration = CourseExamConfiguration.objects.filter(
            cycle_course=cycle_course
        ).first()
        blueprint = (
            None
            if automatic_flat_mode
            else ExamBlueprint.objects.filter(cycle_course=cycle_course).first()
        )

        if not stage6_cycle_is_open(cycle_course.cycle):
            cls._block(
                blockers,
                STAGE6_CYCLE_NOT_OPEN_CODE,
                STAGE6_CYCLE_NOT_OPEN_MESSAGE,
            )
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
                details = {
                    "required": roster.required_active_count,
                    "submitted": roster.submitted_required_count,
                }
                if (
                    automatic_flat_mode
                    and cycle_course.cycle.automatic_contributor_completion_policy
                    == ExaminationCycle.AutomaticContributorCompletionPolicy.SUFFICIENT_POOL
                ):
                    cls._warn(
                        warnings,
                        "ACTIVE_CONTRIBUTORS_INCOMPLETE",
                        (
                            f"{roster.submitted_required_count} / "
                            f"{roster.required_active_count} active contributors "
                            "Final Submitted; generation uses the sufficient Submitted pool."
                        ),
                        **details,
                    )
                else:
                    cls._block(
                        blockers,
                        "ACTIVE_CONTRIBUTORS_INCOMPLETE",
                        "Currently required Active contributors have not all Submitted.",
                        **details,
                    )
            if roster.unresolved_blocked_count:
                cls._block(
                    blockers,
                    "BLOCKED_DRAFTS_UNRESOLVED",
                    "Every current Blocked Draft requires explicit resolution.",
                    unresolved=roster.unresolved_blocked_count,
                )

        automatic_campus_labels = {}
        automatic_participating_ids = ()
        participating_codes = ()
        if automatic_flat_mode:
            for campus_id, campus_name in cycle_course.offering_snapshots.order_by(
                "campus_id", "id"
            ).values_list("campus_id", "campus__name"):
                automatic_campus_labels.setdefault(campus_id, campus_name)
            automatic_participating_ids = tuple(automatic_campus_labels)
            if not automatic_participating_ids:
                cls._block(
                    blockers,
                    "CAMPUS_PARTICIPATION_MISSING",
                    "At least one participating campus snapshot is required.",
                )
        else:
            try:
                participating_codes = canonicalize_participating_campus_rows(
                    cycle_course.offering_snapshots.order_by(
                        "campus__code", "campus_id"
                    ).values_list("campus_id", "campus__code")
                )
            except Stage6CampusCodeAmbiguity as exc:
                cls._block(blockers, "CAMPUS_CODE_INVALID", str(exc))
        campus_quotas = {}
        difficulty_quotas = {}
        final_count = configuration.final_item_count if configuration else None
        if (
            not automatic_flat_mode
            and final_count is not None
            and 50 <= final_count <= 75
        ):
            try:
                campus_quotas = allocate_campuses(final_count, participating_codes)
            except AllocationError as exc:
                cls._block(blockers, "CAMPUS_CODE_INVALID", str(exc))
            difficulty_quotas = allocate_difficulties(final_count)

        section_rows = []
        section_quotas = {}
        if automatic_flat_mode:
            if final_count is not None:
                section_quotas = {0: final_count}
        elif blueprint is None:
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

        pool_kwargs = (
            {"participating_campus_ids": automatic_participating_ids}
            if automatic_flat_mode
            else {"participating_codes": participating_codes}
        )
        eligible_questions, invalid_question_count = eligible_submitted_question_pool(
            cycle_course=cycle_course,
            **pool_kwargs,
        )
        submitted_question_count = len(eligible_questions)
        duplicate_question_count = 0
        automatic_question_groups = {}
        if invalid_question_count:
            cls._block(
                blockers,
                "ELIGIBLE_POOL_INVALID",
                "Submitted question rows with invalid payload or frozen campus evidence were excluded.",
                invalid_count=invalid_question_count,
            )

        if automatic_flat_mode:
            represented_campus_ids = {
                question.contribution.source_campus_id for question in eligible_questions
            }
            missing_campus_ids = tuple(
                campus_id
                for campus_id in automatic_participating_ids
                if campus_id not in represented_campus_ids
            )
            missing_campus_names = tuple(
                automatic_campus_labels[campus_id]
                for campus_id in missing_campus_ids
            )
            if missing_campus_ids:
                details = {
                    "campus_names": missing_campus_names,
                    "missing_campus_count": len(missing_campus_ids),
                }
                message = (
                    "No usable unique Submitted questions represent: "
                    + ", ".join(missing_campus_names)
                    + "."
                )
                if (
                    cycle_course.cycle.automatic_campus_contribution_policy
                    == ExaminationCycle.AutomaticCampusContributionPolicy.AVAILABLE_WITH_WARNING
                ):
                    cls._warn(
                        warnings,
                        "MISSING_CAMPUS_REPRESENTATION",
                        message
                        + " Feasible allocation will use represented campuses only.",
                        **details,
                    )
                    allocation_campus_ids = represented_campus_ids
                else:
                    cls._block(
                        blockers,
                        "MISSING_CAMPUS_REPRESENTATION",
                        message,
                        **details,
                    )
                    allocation_campus_ids = automatic_participating_ids
            else:
                allocation_campus_ids = automatic_participating_ids
            if final_count is not None and 50 <= final_count <= 75:
                campus_quotas = automatic_campus_allocate(
                    total=final_count,
                    campus_ids=allocation_campus_ids,
                )
                difficulty_quotas = allocate_difficulties(final_count)
            automatic_question_groups = automatic_logical_question_groups(
                eligible_questions
            )
            duplicate_question_count = (
                submitted_question_count - len(automatic_question_groups)
            )
            if duplicate_question_count:
                cls._warn(
                    warnings,
                    "REDUNDANT_DUPLICATE_QUESTIONS",
                    (
                        f"{submitted_question_count} submitted \u2022 {len(automatic_question_groups)} unique "
                        f"\u2022 {duplicate_question_count} duplicate copies automatically ignored."
                    ),
                    submitted=submitted_question_count,
                    unique=len(automatic_question_groups),
                    redundant=duplicate_question_count,
                )

        eligible_ids = {question.id for question in eligible_questions}
        placement_by_question = {}
        if (
            not automatic_flat_mode
            and blueprint is not None
            and blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS
        ):
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
        if not automatic_flat_mode and blueprint is not None:
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
            valid_scenario_members.append((scenario, members))
        if invalid_scenario_count:
            cls._block(
                blockers,
                "SCENARIOS_INVALID",
                "Incomplete or cross-scope scenarios block readiness.",
                invalid_count=invalid_scenario_count,
            )

        if automatic_flat_mode:
            available_by_campus = Counter(
                {
                    campus_id: sum(
                        any(
                            question.contribution.source_campus_id == campus_id
                            for question in rows
                        )
                        for rows in automatic_question_groups.values()
                    )
                    for campus_id in campus_quotas
                }
            )
            available_by_difficulty = Counter(
                {
                    difficulty: sum(
                        any(question.difficulty == difficulty for question in rows)
                        for rows in automatic_question_groups.values()
                    )
                    for difficulty in difficulty_quotas
                }
            )
        else:
            available_by_campus = Counter(
                question.stage6_campus_code for question in eligible_questions
            )
            available_by_difficulty = Counter(
                question.difficulty for question in eligible_questions
            )
        available_by_section = Counter()
        if automatic_flat_mode or (
            blueprint is not None and blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS
        ):
            available_by_section[0] = (
                len(automatic_question_groups)
                if automatic_flat_mode
                else len(eligible_questions)
            )
        else:
            available_by_section.update(placement_by_question.values())

        logical_question_count = (
            len(automatic_question_groups)
            if automatic_flat_mode
            else len(eligible_questions)
        )
        if final_count is not None and logical_question_count < final_count:
            shortages.append(
                {"dimension": "total", "label": "Total", "required": final_count, "available": logical_question_count}
            )
        for campus_key, required in campus_quotas.items():
            available = available_by_campus[campus_key]
            if available < required:
                label = (
                    automatic_campus_labels[campus_key]
                    if automatic_flat_mode
                    else campus_key.title()
                )
                shortages.append({"dimension": "campus", "label": label, "required": required, "available": available})
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
            if automatic_flat_mode and duplicate_question_count:
                cls._block(
                    blockers,
                    "UNIQUE_QUESTION_SHORTAGES",
                    "The deduplicated Submitted pool has aggregate shortages.",
                )
            else:
                cls._block(blockers, "QUESTION_SHORTAGES", "The eligible Submitted pool has aggregate shortages.")

        solver_result = None
        margins = ()
        question_vectors = {}
        identity_blocks = []
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
            "CAMPUS_PARTICIPATION_MISSING",
            "MISSING_CAMPUS_REPRESENTATION",
            "BLUEPRINT_MISSING",
            "BLUEPRINT_STRUCTURE_INVALID",
            "SECTION_QUOTA_INVALID",
            "ELIGIBLE_POOL_INVALID",
            "QUESTION_PLACEMENTS_INCOMPLETE",
            "SCENARIOS_INVALID",
            "QUESTION_SHORTAGES",
            "UNIQUE_QUESTION_SHORTAGES",
        }
        if not ({item["code"] for item in blockers} & structural_codes):
            campus_order = tuple(campus_quotas)
            difficulty_order = ("EASY", "MODERATE", "DIFFICULT")
            section_order = tuple(section_quotas)

            def vector_for(question):
                section_key = (
                    0
                    if automatic_flat_mode
                    or blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS
                    else placement_by_question[question.id]
                )
                campus_key = (
                    question.contribution.source_campus_id
                    if automatic_flat_mode
                    else question.stage6_campus_code
                )
                return (
                    1,
                    *(1 if campus_key == key else 0 for key in campus_order),
                    *(1 if question.difficulty == key else 0 for key in difficulty_order),
                    *(1 if section_key == key else 0 for key in section_order),
                )

            question_vectors = {question.id: vector_for(question) for question in eligible_questions}
            for _scenario, members in valid_scenario_members:
                scenario_vectors.append(
                    tuple(
                        sum(question_vectors[member.question_id][position] for member in members)
                        for position in range(len(next(iter(question_vectors.values()))))
                    )
                )
            alternative_vector_groups = []
            if automatic_flat_mode:
                singleton_capacities = Counter()
                for rows in automatic_question_groups.values():
                    vectors = tuple(
                        sorted({question_vectors[question.id] for question in rows})
                    )
                    if len(vectors) == 1:
                        singleton_capacities[vectors[0]] += 1
                    else:
                        alternative_vector_groups.append(vectors)
            else:
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
                alternative_vector_groups=alternative_vector_groups,
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
                    (
                        "Two equivalent sets cannot satisfy the required questionnaire allocation across campus, difficulty, and item-count constraints."
                        if automatic_flat_mode
                        else "Two equivalent sets cannot satisfy all hard margins and scenario bundles."
                    ),
                )

        report = {
            "ready": not blockers,
            "status": "READY" if not blockers else "BLOCKED",
            "blockers": blockers,
            "warnings": warnings,
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
            "eligible_question_count": logical_question_count,
            "submitted_question_count": submitted_question_count,
            "unique_question_count": logical_question_count,
            "invalid_question_count": invalid_question_count,
            "duplicate_question_count": duplicate_question_count,
            "automatic_pool": automatic_flat_mode,
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
        problem = None
        if not blockers and solver_result and solver_result.feasible:
            section_labels = {section.id: section.title for section in section_rows}
            section_instructions = {
                section.id: section.instructions for section in section_rows
            }
            section_labels[0] = "Questionnaire"
            section_instructions[0] = ""
            scenario_by_question = {}
            for scenario, members in valid_scenario_members:
                for member in members:
                    scenario_by_question[member.question_id] = (scenario, member.position)
            generation_questions = {}
            for question in eligible_questions:
                section_id = (
                    0
                    if automatic_flat_mode
                    or blueprint.mode == ExamBlueprint.Mode.NO_SECTIONS
                    else placement_by_question[question.id]
                )
                scenario_data = scenario_by_question.get(question.id)
                scenario = scenario_data[0] if scenario_data else None
                source_digest = _sha256_json(
                    {
                        "source_id": question.id,
                        "revision": question.revision,
                        "question_text": question.question_text,
                        "choices": [
                            question.choice_a,
                            question.choice_b,
                            question.choice_c,
                            question.choice_d,
                        ],
                        "correct_answer": question.correct_answer,
                        "difficulty": question.difficulty,
                    }
                )
                contributor = question.contribution.faculty_user
                generation_questions[question.id] = GenerationQuestion(
                    source_id=question.id,
                    source_revision=question.revision,
                    source_digest=source_digest,
                    contributor_id=contributor.id,
                    contributor_name=contributor.full_name,
                    campus_id=question.contribution.source_campus_id,
                    campus_code=question.stage6_campus_code,
                    campus_name=question.contribution.source_campus.name,
                    difficulty=question.difficulty,
                    section_id=section_id,
                    section_title=section_labels[section_id],
                    section_instructions=section_instructions[section_id],
                    normalized_fingerprint=QuestionPayloadService.question_fingerprint(
                        question.question_text
                    ),
                    question_text=question.question_text,
                    choices=(
                        question.choice_a,
                        question.choice_b,
                        question.choice_c,
                        question.choice_d,
                    ),
                    correct_answer=question.correct_answer,
                    scenario_id=scenario.id if scenario else None,
                    scenario_revision=scenario.revision if scenario else None,
                    scenario_title=scenario.title if scenario else "",
                    scenario_stimulus=scenario.stimulus if scenario else "",
                    scenario_member_position=scenario_data[1] if scenario_data else None,
                )

            for scenario, members in valid_scenario_members:
                identity_blocks.append(
                    IdentityBlock(
                        block_id=f"scenario:{scenario.id}",
                        vector=tuple(
                            sum(
                                question_vectors[member.question_id][position]
                                for member in members
                            )
                            for position in range(len(margins))
                        ),
                        members=tuple(
                            IdentityMember(
                                source_id=member.question_id,
                                contributor_id=generation_questions[
                                    member.question_id
                                ].contributor_id,
                                campus=(
                                    generation_questions[member.question_id].campus_id
                                    if automatic_flat_mode
                                    else generation_questions[
                                        member.question_id
                                    ].campus_code
                                ),
                                difficulty=generation_questions[
                                    member.question_id
                                ].difficulty,
                                section_id=generation_questions[
                                    member.question_id
                                ].section_id,
                                member_order=member.position,
                            )
                            for member in members
                        ),
                    )
                )
            for question in eligible_questions:
                if question.id in scenario_question_ids:
                    continue
                data = generation_questions[question.id]
                identity_blocks.append(
                    IdentityBlock(
                        block_id=f"question:{question.id}",
                        vector=question_vectors[question.id],
                        members=(
                            IdentityMember(
                                source_id=question.id,
                                contributor_id=data.contributor_id,
                                campus=(
                                    data.campus_id
                                    if automatic_flat_mode
                                    else data.campus_code
                                ),
                                difficulty=data.difficulty,
                                section_id=data.section_id,
                            ),
                        ),
                        logical_group_id=(
                            data.normalized_fingerprint
                            if automatic_flat_mode
                            else None
                        ),
                    )
                )
            roster_boundary = roster.boundary_sha256 if roster else ""
            structure_revision = (
                AUTOMATIC_FLAT_STRUCTURE_REVISION
                if automatic_flat_mode
                else blueprint.revision
            )
            fingerprint_payload = {
                "algorithm_version": GENERATION_ALGORITHM_VERSION,
                "tenant_id": cycle_course.cycle.tenant_id,
                "cycle_id": cycle_course.cycle_id,
                "cycle_course_id": cycle_course.id,
                "configuration_revision": configuration.revision,
                "blueprint_revision": structure_revision,
                "roster_boundary": roster_boundary,
                "final_count": final_count,
                "campus_quotas": campus_quotas,
                "difficulty_quotas": difficulty_quotas,
                "section_quotas": section_quotas,
                "questions": [
                    {
                        "id": item.source_id,
                        "revision": item.source_revision,
                        "digest": item.source_digest,
                        "contributor_id": item.contributor_id,
                        "campus": item.campus_code,
                        "difficulty": item.difficulty,
                        "section_id": item.section_id,
                        "scenario_id": item.scenario_id,
                        "scenario_revision": item.scenario_revision,
                        "scenario_member_position": item.scenario_member_position,
                        "scenario_title": item.scenario_title,
                        "scenario_stimulus": item.scenario_stimulus,
                    }
                    for item in sorted(
                        generation_questions.values(), key=lambda row: row.source_id
                    )
                ],
            }
            if automatic_flat_mode:
                fingerprint_payload["structure_mode"] = "AUTOMATIC_FLAT"
                fingerprint_payload["automatic_dedupe_policy"] = "normalized-text-v3"
                fingerprint_payload["automatic_policies"] = {
                    "campus_contribution": (
                        cycle_course.cycle.automatic_campus_contribution_policy
                    ),
                    "contributor_completion": (
                        cycle_course.cycle.automatic_contributor_completion_policy
                    ),
                }
            problem = GenerationProblem(
                cycle_course=cycle_course,
                configuration=configuration,
                blueprint=blueprint,
                final_count=final_count,
                margins=margins,
                campus_quotas=dict(campus_quotas),
                difficulty_quotas=dict(difficulty_quotas),
                section_quotas=dict(section_quotas),
                section_order=tuple(section_quotas),
                questions=generation_questions,
                blocks=tuple(identity_blocks),
                input_fingerprint=_sha256_json(fingerprint_payload),
                configuration_revision=configuration.revision,
                blueprint_revision=structure_revision,
                roster_boundary=roster_boundary,
                minimum_overlap=solver_result.minimum_overlap,
            )
        return problem, report
