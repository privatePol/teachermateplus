from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.core.services.audit import AuditService

from .blueprint_services import Stage6Conflict, require_stage6_open_cycle
from .contribution_services import Stage5LockService
from .generation_algorithms import (
    confidential_hmac_rank,
    order_selected_blocks,
    proportional_campus_difficulty_score,
    solve_automatic_identity_aware_two_sets,
    solve_identity_aware_two_sets,
)
from .generation_readiness import (
    AUTOMATIC_LOGICAL_IDENTITY_VERSION,
    AUTOMATIC_GENERATION_DEFAULT_MAX_STATES,
    GENERATION_ALGORITHM_VERSION,
    SOURCE_AUDIT_SCHEMA_VERSION,
    Stage6ReadinessService,
    resolve_automatic_generation_max_states,
)
from .models import (
    BlockedContributionResolution,
    CycleCourse,
    ExamBlueprint,
    ExamGenerationRevision,
    ExaminationCycle,
    ExamScenario,
    ExamScenarioMember,
    ExamSection,
    FacultyContribution,
    FacultyContributionEligibilitySource,
    GeneratedExamItem,
    GeneratedExamSet,
    GenerationSourceAuditSnapshot,
    GenerationSourceQuestionSnapshot,
    Question,
    QuestionBlueprintPlacement,
)
from .services import DepartmentalExamAuthorizationService


class GenerationConflict(Stage6Conflict):
    """A stale or lifecycle-conflicting generation request."""


class GenerationLimitExceeded(Stage6Conflict):
    """The identity-aware optimum could not be proved inside the state bound."""


@dataclass(frozen=True)
class GenerationOutcome:
    revision: ExamGenerationRevision
    reused: bool = False


class ExamGenerationService:
    DEFAULT_MAX_STATES = 500_000
    AUTOMATIC_DEFAULT_MAX_STATES = AUTOMATIC_GENERATION_DEFAULT_MAX_STATES

    @staticmethod
    def request_token_digest(raw_token):
        token = str(raw_token or "").strip()
        if len(token) < 32 or len(token) > 200:
            raise ValidationError("The generation request token is invalid.")
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @classmethod
    def _lock_generation_inputs(cls, *, cycle_course_id, tenant_id):
        cycle, course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        blueprint = None
        if (
            cycle.processing_mode
            == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        ):
            blueprint = (
                ExamBlueprint.objects.select_for_update()
                .filter(cycle_course=course)
                .first()
            )
        if blueprint is not None:
            course.exam_blueprint = blueprint
            list(
                ExamSection.objects.select_for_update()
                .filter(blueprint=blueprint)
                .order_by("id")
            )
            list(
                QuestionBlueprintPlacement.objects.select_for_update()
                .filter(blueprint=blueprint)
                .order_by("id")
            )
            scenarios = list(
                ExamScenario.objects.select_for_update()
                .filter(blueprint=blueprint)
                .order_by("id")
            )
            list(
                ExamScenarioMember.objects.select_for_update()
                .filter(scenario_id__in=[scenario.id for scenario in scenarios])
                .order_by("id")
            )
        contributions = list(
            FacultyContribution.objects.select_for_update()
            .filter(cycle_course=course)
            .order_by("id")
        )
        list(
            FacultyContributionEligibilitySource.objects.select_for_update()
            .filter(contribution_id__in=[row.id for row in contributions])
            .order_by("id")
        )
        list(
            BlockedContributionResolution.objects.select_for_update()
            .filter(cycle_course=course)
            .order_by("id")
        )
        list(
            Question.objects.select_for_update()
            .filter(contribution__cycle_course=course)
            .order_by("id")
        )
        revisions = list(
            ExamGenerationRevision.objects.select_for_update()
            .filter(cycle_course=course)
            .order_by("id")
        )
        return cycle, course, configuration, blueprint, revisions

    @staticmethod
    def _current_revision(revisions):
        rows = [row for row in revisions if row.current_marker == 1]
        if len(rows) > 1:
            raise GenerationConflict("Generation revision state is inconsistent.")
        return rows[0] if rows else None

    @classmethod
    def _audit(cls, *, action, revision, actor, request, metadata=None):
        course = revision.cycle_course
        AuditService.log_event(
            action=action,
            portal=(
                "SYSTEM"
                if actor is None
                and revision.generation_trigger
                == ExamGenerationRevision.GenerationTrigger.AUTOMATIC
                else "ADMIN"
            ),
            entity_type="ExamGenerationRevision",
            entity_id=revision.id,
            actor=actor,
            tenant=course.cycle.tenant_id,
            campus=(
                course.responsible_department.campus_id
                if course.responsible_department_id
                else None
            ),
            metadata={
                "cycle_id": course.cycle_id,
                "cycle_course_id": course.id,
                "generation_revision": revision.revision_number,
                "algorithm_version": revision.algorithm_version,
                "item_count_per_set": revision.final_item_count_snapshot,
                "minimum_overlap": revision.minimum_overlap,
                "proportional_score": revision.proportional_score,
                "contributors_represented": revision.contributors_represented,
                "squared_contributor_concentration": (
                    revision.squared_contributor_concentration
                ),
                "source_input_fingerprint": revision.source_input_fingerprint,
                "generation_trigger": revision.generation_trigger,
                **(metadata or {}),
            },
            request=request,
        )

    @classmethod
    @transaction.atomic
    def generate(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        actor,
        expected_current_revision,
        expected_input_fingerprint,
        request_token,
        regeneration=False,
        regeneration_reason="",
        generation_trigger=ExamGenerationRevision.GenerationTrigger.MANUAL,
        request=None,
        max_states=None,
    ):
        token_digest = cls.request_token_digest(request_token)
        reason = " ".join((regeneration_reason or "").split())
        if len(reason) > 500:
            raise ValidationError("A regeneration note cannot exceed 500 characters.")
        if generation_trigger not in ExamGenerationRevision.GenerationTrigger.values:
            raise ValidationError("The generation trigger is invalid.")

        cycle, course, configuration, blueprint, revisions = cls._lock_generation_inputs(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        automatic_system_run = (
            generation_trigger
            == ExamGenerationRevision.GenerationTrigger.AUTOMATIC
        )
        automatic_mode = (
            cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        if automatic_system_run:
            if actor is not None or regeneration or not automatic_mode or reason:
                raise GenerationConflict("Automatic generation attribution is invalid.")
        elif automatic_mode:
            DepartmentalExamAuthorizationService.require_generation_management(
                user=actor,
                cycle_course=course,
            )
            if not regeneration:
                raise GenerationConflict(
                    "Automatic-mode first generation is performed by deadline processing."
                )
        else:
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=actor,
                cycle_course=course,
            )
            if regeneration and not 10 <= len(reason) <= 500:
                raise ValidationError(
                    "A regeneration reason of 10 to 500 characters is required."
                )
        if not regeneration and reason:
            raise ValidationError("A note is accepted only for regeneration.")
        from .final_lock import FinalExamLockPolicy

        # Final lock rejection precedes request-token reuse so no old browser
        # token can make Generate or Regenerate appear successful after lock.
        FinalExamLockPolicy.require_not_locked(
            course,
            conflict_class=GenerationConflict,
        )
        require_stage6_open_cycle(cycle, conflict_class=GenerationConflict)
        duplicate = next(
            (row for row in revisions if row.request_token_digest == token_digest),
            None,
        )
        if duplicate is not None:
            return GenerationOutcome(revision=duplicate, reused=True)

        if configuration is None or (not automatic_mode and blueprint is None):
            raise GenerationConflict("Generation prerequisites changed. Refresh the workspace.")
        automatic_state_budget = (
            resolve_automatic_generation_max_states(max_states)
            if automatic_mode
            else None
        )
        problem, readiness = Stage6ReadinessService.build_problem(
            cycle_course=course,
            automatic_max_states=automatic_state_budget,
        )
        if problem is None or not readiness["ready"]:
            raise GenerationConflict("Generation readiness changed. Refresh the workspace.")
        expected_fingerprint = str(expected_input_fingerprint or "").strip().lower()
        if expected_fingerprint != problem.input_fingerprint:
            raise GenerationConflict("Generation inputs changed. Refresh the workspace.")

        current = cls._current_revision(revisions)
        current_number = current.revision_number if current else 0
        try:
            expected_revision = int(expected_current_revision)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Expected generation revision is invalid.") from exc
        if expected_revision != current_number:
            raise GenerationConflict("The current generation revision changed. Refresh the workspace.")
        if regeneration and current is None:
            raise GenerationConflict("There is no current generation to regenerate.")
        if not regeneration and current is not None:
            raise GenerationConflict("A current generation already exists. Use Regenerate.")
        if current is not None and current.status != ExamGenerationRevision.Status.GENERATED:
            raise GenerationConflict("This generation lifecycle no longer permits regeneration.")

        next_revision = max((row.revision_number for row in revisions), default=0) + 1
        hmac_context = {
            "algorithm_version": GENERATION_ALGORITHM_VERSION,
            "tenant_id": course.cycle.tenant_id,
            "cycle_id": course.cycle_id,
            "cycle_course_id": course.id,
            "configuration_revision": problem.configuration_revision,
            "blueprint_revision": problem.blueprint_revision,
            "roster_boundary": problem.roster_boundary,
            "input_fingerprint": problem.input_fingerprint,
            "generation_revision": next_revision,
        }
        if automatic_mode:
            configured_limit = automatic_state_budget
            selection = solve_automatic_identity_aware_two_sets(
                margins=problem.margins,
                blocks=problem.blocks,
                campus_quotas=problem.campus_quotas,
                difficulty_quotas=problem.difficulty_quotas,
                secret=settings.SECRET_KEY,
                hmac_context=hmac_context,
                max_states=configured_limit,
                optimize_soft=True,
            )
        else:
            configured_limit = int(
                max_states
                if max_states is not None
                else getattr(
                    settings,
                    "DEPARTMENTAL_EXAM_GENERATION_MAX_STATES",
                    cls.DEFAULT_MAX_STATES,
                )
            )
            selection = solve_identity_aware_two_sets(
                margins=problem.margins,
                blocks=problem.blocks,
                minimum_overlap=problem.minimum_overlap,
                campus_quotas=problem.campus_quotas,
                difficulty_quotas=problem.difficulty_quotas,
                secret=settings.SECRET_KEY,
                hmac_context=hmac_context,
                max_states=configured_limit,
            )
        if selection.limit_hit:
            raise GenerationLimitExceeded(
                "The generation optimum could not be proved within the configured state limit."
            )
        if not selection.feasible:
            raise GenerationConflict(
                "The identity-aware generation problem is no longer feasible."
            )
        if selection.overlap != problem.minimum_overlap:
            raise GenerationConflict("The authoritative minimum overlap was not preserved.")
        cls._validate_selection(problem=problem, selection=selection)

        predecessor = current
        if predecessor is None and automatic_mode and revisions:
            predecessor = max(revisions, key=lambda row: row.revision_number)
        if current is not None:
            current.status = ExamGenerationRevision.Status.SUPERSEDED
            current.current_marker = None
            current.save(update_fields=["status", "current_marker", "updated_at"])
        revision = ExamGenerationRevision.objects.create(
            cycle_course=course,
            revision_number=next_revision,
            status=ExamGenerationRevision.Status.GENERATED,
            current_marker=1,
            source_input_fingerprint=problem.input_fingerprint,
            algorithm_version=GENERATION_ALGORITHM_VERSION,
            generated_at=timezone.now(),
            generated_by=actor,
            generation_trigger=generation_trigger,
            configuration_revision_snapshot=problem.configuration_revision,
            blueprint_revision_snapshot=problem.blueprint_revision,
            roster_boundary_snapshot=problem.roster_boundary,
            final_item_count_snapshot=problem.final_count,
            request_token_digest=token_digest,
            supersedes=predecessor,
            regeneration_reason=reason,
            minimum_overlap=selection.overlap,
            proportional_score=selection.proportional_score,
            contributors_represented=selection.contributors_represented,
            squared_contributor_concentration=(
                selection.squared_contributor_concentration
            ),
        )
        cls._create_source_audit_snapshot(revision=revision, problem=problem)

        selected_block_ids_by_set = {}
        ordered_members_by_set = {}
        for set_code, selected_ids in (
            (GeneratedExamSet.SetCode.A, selection.set_a_block_ids),
            (GeneratedExamSet.SetCode.B, selection.set_b_block_ids),
        ):
            generated_set = GeneratedExamSet.objects.create(
                generation_revision=revision,
                set_code=set_code,
                campus_quotas_snapshot=problem.campus_quotas,
                difficulty_quotas_snapshot=problem.difficulty_quotas,
                section_quotas_snapshot={
                    str(section_id): quota
                    for section_id, quota in problem.section_quotas.items()
                },
                item_count=problem.final_count,
            )
            ordered_members = order_selected_blocks(
                blocks=problem.blocks,
                selected_block_ids=selected_ids,
                set_code=set_code,
                secret=settings.SECRET_KEY,
                hmac_context=hmac_context,
                section_order=problem.section_order,
            )
            set_a_selected_ids = selected_block_ids_by_set.get(
                GeneratedExamSet.SetCode.A
            )
            set_a_members = ordered_members_by_set.get(GeneratedExamSet.SetCode.A)
            if (
                automatic_mode
                and set_code == GeneratedExamSet.SetCode.B
                and set_a_selected_ids is not None
                and set_a_members is not None
                and len(ordered_members) > 1
                and tuple(str(block_id) for block_id in selected_ids)
                == set_a_selected_ids
            ):
                rotation_seed = confidential_hmac_rank(
                    secret=settings.SECRET_KEY,
                    domain="departmental-exams.automatic.order.set-b.rotation",
                    context={
                        **hmac_context,
                        "selected_source_ids": [
                            member.source_id for member in ordered_members
                        ],
                    },
                )
                set_a_source_ids = tuple(member.source_id for member in set_a_members)
                for offset in range(len(ordered_members)):
                    rotation = (rotation_seed + offset) % len(ordered_members)
                    candidate = (
                        ordered_members[rotation:] + ordered_members[:rotation]
                    )
                    if tuple(member.source_id for member in candidate) != set_a_source_ids:
                        ordered_members = candidate
                        break
            if len(ordered_members) != problem.final_count:
                raise GenerationConflict("Generated set item count does not match its snapshot.")
            selected_block_ids_by_set[set_code] = tuple(
                str(block_id) for block_id in selected_ids
            )
            ordered_members_by_set[set_code] = ordered_members
            GeneratedExamItem.objects.bulk_create(
                [
                    cls._item_snapshot(
                        generated_set=generated_set,
                        position=position,
                        data=problem.questions[member.source_id],
                    )
                    for position, member in enumerate(ordered_members, start=1)
                ]
            )

        reason_metadata = {}
        if reason:
            reason_metadata = {
                "regeneration_reason_sha256": hashlib.sha256(
                    reason.encode("utf-8")
                ).hexdigest(),
                "regeneration_reason_length": len(reason),
            }
        if current is not None:
            cls._audit(
                action="DE_EXAM_GENERATION_SUPERSEDED",
                revision=current,
                actor=actor,
                request=request,
                metadata={
                    "superseded_by_generation_revision": revision.revision_number,
                    **reason_metadata,
                },
            )
        cls._audit(
            action=("DE_EXAM_REGENERATED" if current is not None else "DE_EXAM_GENERATED"),
            revision=revision,
            actor=actor,
            request=request,
            metadata=reason_metadata,
        )
        return GenerationOutcome(revision=revision)

    @staticmethod
    def _create_source_audit_snapshot(*, revision, problem):
        eligible_rows = tuple(
            row for row in problem.source_audit_questions if row.eligible_for_generation
        )
        if problem.logical_identity_version == AUTOMATIC_LOGICAL_IDENTITY_VERSION:
            unique_logical_count = len(
                {row.normalized_fingerprint for row in eligible_rows}
            )
        else:
            unique_logical_count = len(eligible_rows)
        audit_snapshot = GenerationSourceAuditSnapshot.objects.create(
            generation_revision=revision,
            schema_version=SOURCE_AUDIT_SCHEMA_VERSION,
            logical_identity_version=problem.logical_identity_version,
            submitted_count=len(problem.source_audit_questions),
            eligible_count=len(eligible_rows),
            unique_logical_count=unique_logical_count,
            redundant_copy_count=len(eligible_rows) - unique_logical_count,
        )
        GenerationSourceQuestionSnapshot.objects.bulk_create(
            [
                GenerationSourceQuestionSnapshot(
                    audit_snapshot=audit_snapshot,
                    source_question_id=row.source_id,
                    source_question_id_snapshot=row.source_id,
                    source_question_revision=row.source_revision,
                    source_question_digest=row.source_digest,
                    contribution_id_snapshot=row.contribution_id,
                    contribution_revision_snapshot=row.contribution_revision,
                    contribution_submitted_at_snapshot=(
                        row.contribution_submitted_at
                    ),
                    contributor_id_snapshot=row.contributor_id,
                    contributor_name_snapshot=row.contributor_name,
                    campus_id_snapshot=row.campus_id,
                    campus_code_snapshot=row.campus_code,
                    campus_name_snapshot=row.campus_name,
                    assignment_context_snapshot=list(row.assignment_context),
                    question_text_snapshot=row.question_text,
                    choices_snapshot=list(row.choices),
                    difficulty_snapshot=row.difficulty,
                    correct_answer_snapshot=row.correct_answer,
                    normalized_fingerprint=row.normalized_fingerprint,
                    eligible_for_generation=row.eligible_for_generation,
                    exclusion_code=row.exclusion_code,
                )
                for row in problem.source_audit_questions
            ],
            batch_size=500,
        )
        return audit_snapshot

    @staticmethod
    def _validate_selection(*, problem, selection):
        by_id = {str(block.block_id): block for block in problem.blocks}
        selected_blocks = []
        selected_source_sets = []
        all_members = []
        proportional = 0
        for selected_ids in (selection.set_a_block_ids, selection.set_b_block_ids):
            normalized_ids = tuple(str(value) for value in selected_ids)
            if len(normalized_ids) != len(set(normalized_ids)) or any(
                block_id not in by_id for block_id in normalized_ids
            ):
                raise GenerationConflict("The selector returned an invalid block identity.")
            blocks = [by_id[block_id] for block_id in normalized_ids]
            vector = tuple(
                sum(block.vector[position] for block in blocks)
                for position in range(len(problem.margins))
            )
            if vector != problem.margins:
                raise GenerationConflict("The selector did not preserve every hard margin.")
            members = [member for block in blocks for member in block.members]
            source_ids = {member.source_id for member in members}
            if len(source_ids) != len(members):
                raise GenerationConflict("A set contains a duplicate source question.")
            if (
                problem.cycle_course.cycle.processing_mode
                == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            ):
                fingerprints = [
                    problem.questions[member.source_id].normalized_fingerprint
                    for member in members
                ]
                if len(fingerprints) != len(set(fingerprints)):
                    raise GenerationConflict(
                        "An automatic set contains duplicate normalized questions."
                    )
            cells = {}
            for member in members:
                key = (member.campus, member.difficulty)
                cells[key] = cells.get(key, 0) + 1
            proportional += proportional_campus_difficulty_score(
                total=problem.final_count,
                campus_quotas=problem.campus_quotas,
                difficulty_quotas=problem.difficulty_quotas,
                cell_counts=cells,
            )
            selected_blocks.append(blocks)
            selected_source_sets.append(source_ids)
            all_members.extend(members)
        overlap = len(selected_source_sets[0].intersection(selected_source_sets[1]))
        if overlap != selection.overlap:
            raise GenerationConflict("The selector overlap evidence is inconsistent.")
        contributor_counts = {}
        for member in all_members:
            contributor_counts[member.contributor_id] = (
                contributor_counts.get(member.contributor_id, 0) + 1
            )
        if proportional != selection.proportional_score:
            raise GenerationConflict("The selector proportional score is inconsistent.")
        if len(contributor_counts) != selection.contributors_represented:
            raise GenerationConflict("The selector contributor representation is inconsistent.")
        if (
            sum(count * count for count in contributor_counts.values())
            != selection.squared_contributor_concentration
        ):
            raise GenerationConflict("The selector contributor concentration is inconsistent.")

    @staticmethod
    def _item_snapshot(*, generated_set, position, data):
        return GeneratedExamItem(
            generated_set=generated_set,
            position=position,
            source_question_id=data.source_id,
            source_question_revision=data.source_revision,
            source_question_digest=data.source_digest,
            source_contributor_id=data.contributor_id,
            source_contributor_id_snapshot=data.contributor_id,
            source_contributor_name_snapshot=data.contributor_name,
            source_campus_id=data.campus_id,
            campus_code_snapshot=data.campus_code,
            campus_name_snapshot=data.campus_name,
            difficulty_snapshot=data.difficulty,
            source_section_id=(data.section_id or None),
            section_id_snapshot=(data.section_id or None),
            section_title_snapshot=data.section_title,
            section_instructions_snapshot=data.section_instructions,
            question_text_snapshot=data.question_text,
            choices_snapshot=list(data.choices),
            correct_answer_snapshot=data.correct_answer,
            source_scenario_id=data.scenario_id,
            scenario_id_snapshot=data.scenario_id,
            scenario_revision_snapshot=data.scenario_revision,
            scenario_title_snapshot=data.scenario_title,
            scenario_stimulus_snapshot=data.scenario_stimulus,
            scenario_member_position_snapshot=data.scenario_member_position,
        )

    @staticmethod
    def current_for_course(*, cycle_course):
        return (
            ExamGenerationRevision.objects.filter(
                cycle_course=cycle_course,
                current_marker=1,
            )
            .select_related("generated_by", "locked_by")
            .first()
        )

    @staticmethod
    def revision_for_tenant(*, revision_id, tenant_id):
        revision = (
            ExamGenerationRevision.objects.select_related(
                "cycle_course__cycle",
                "cycle_course__course",
                "cycle_course__responsible_department",
                "generated_by",
                "locked_by",
            )
            .filter(pk=revision_id, cycle_course__cycle__tenant_id=tenant_id)
            .first()
        )
        if revision is None:
            raise Http404
        return revision
