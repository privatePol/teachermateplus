from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.core.services.audit import AuditService

from .blueprint_services import Stage6Conflict, require_stage6_open_cycle
from .generation_readiness import GENERATION_ALGORITHM_VERSION, Stage6ReadinessService
from .generation_services import ExamGenerationService
from .models import (
    ExamGenerationRevision,
    ExaminationCycle,
    GeneratedExamItem,
    GeneratedExamSet,
)
from .services import DepartmentalExamAuthorizationService


class ApprovalConflict(Stage6Conflict):
    """A stale, drifted, corrupt, or lifecycle-conflicting approval request."""


@dataclass(frozen=True)
class ApprovalOutcome:
    revision: ExamGenerationRevision
    reused: bool = False


def _sha256_json(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class GeneratedExamIntegrityService:
    """Validate generated output against approval-time authoritative evidence."""

    SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

    @staticmethod
    def _quota_snapshot(value, *, label):
        if not isinstance(value, dict) or not value:
            raise ApprovalConflict(f"Generated {label} quota evidence is incomplete.")
        normalized = {}
        for key, amount in value.items():
            try:
                amount = int(amount)
            except (TypeError, ValueError) as exc:
                raise ApprovalConflict(
                    f"Generated {label} quota evidence is invalid."
                ) from exc
            if amount < 0:
                raise ApprovalConflict(f"Generated {label} quota evidence is invalid.")
            normalized[str(key)] = amount
        return normalized

    @classmethod
    def _verify_item_snapshot(cls, item, *, authoritative):
        choices = item.choices_snapshot
        if (
            item.source_question_revision < 1
            or not cls.SHA256_RE.fullmatch(item.source_question_digest or "")
            or not (item.question_text_snapshot or "").strip()
            or not isinstance(choices, list)
            or len(choices) != 4
            or any(not str(choice).strip() for choice in choices)
            or item.correct_answer_snapshot not in {"A", "B", "C", "D"}
            or item.source_contributor_id != item.source_contributor_id_snapshot
            or not (item.source_contributor_name_snapshot or "").strip()
            or not (item.campus_code_snapshot or "").strip()
            or not (item.campus_name_snapshot or "").strip()
            or item.source_section_id != item.section_id_snapshot
            or not (item.section_title_snapshot or "").strip()
        ):
            raise ApprovalConflict("Generated question snapshot evidence is incomplete.")
        expected_digest = _sha256_json(
            {
                "source_id": item.source_question_id,
                "revision": item.source_question_revision,
                "question_text": item.question_text_snapshot,
                "choices": choices,
                "correct_answer": item.correct_answer_snapshot,
                "difficulty": item.difficulty_snapshot,
            }
        )
        if expected_digest != item.source_question_digest:
            raise ApprovalConflict("Generated question snapshot evidence is corrupt.")
        scenario_fields = (
            item.source_scenario_id,
            item.scenario_id_snapshot,
            item.scenario_revision_snapshot,
            item.scenario_member_position_snapshot,
        )
        if item.scenario_id_snapshot is None:
            if any(value is not None for value in scenario_fields) or any(
                (value or "").strip()
                for value in (
                    item.scenario_title_snapshot,
                    item.scenario_stimulus_snapshot,
                )
            ):
                raise ApprovalConflict("Generated scenario snapshot evidence is corrupt.")
        elif (
            item.source_scenario_id != item.scenario_id_snapshot
            or not item.scenario_revision_snapshot
            or not item.scenario_member_position_snapshot
            or not (item.scenario_stimulus_snapshot or "").strip()
        ):
            raise ApprovalConflict("Generated scenario snapshot evidence is incomplete.")

        if authoritative is None:
            raise ApprovalConflict(
                "Generated item source is absent from authoritative generation evidence."
            )
        expected_section_id = authoritative.section_id or None
        expected_evidence = {
            "source_question_id": authoritative.source_id,
            "source_question_revision": authoritative.source_revision,
            "source_question_digest": authoritative.source_digest,
            "source_contributor_id": authoritative.contributor_id,
            "source_contributor_id_snapshot": authoritative.contributor_id,
            "source_contributor_name_snapshot": authoritative.contributor_name,
            "source_campus_id": authoritative.campus_id,
            "campus_code_snapshot": authoritative.campus_code,
            "campus_name_snapshot": authoritative.campus_name,
            "difficulty_snapshot": authoritative.difficulty,
            "source_section_id": expected_section_id,
            "section_id_snapshot": expected_section_id,
            "section_title_snapshot": authoritative.section_title,
            "section_instructions_snapshot": authoritative.section_instructions,
            "question_text_snapshot": authoritative.question_text,
            "choices_snapshot": list(authoritative.choices),
            "correct_answer_snapshot": authoritative.correct_answer,
            "source_scenario_id": authoritative.scenario_id,
            "scenario_id_snapshot": authoritative.scenario_id,
            "scenario_revision_snapshot": authoritative.scenario_revision,
            "scenario_title_snapshot": authoritative.scenario_title,
            "scenario_stimulus_snapshot": authoritative.scenario_stimulus,
            "scenario_member_position_snapshot": (
                authoritative.scenario_member_position
            ),
        }
        actual_evidence = {
            field: getattr(item, field) for field in expected_evidence
        }
        if actual_evidence != expected_evidence:
            raise ApprovalConflict(
                "Generated item question or scenario snapshot does not match "
                "authoritative generation evidence."
            )

    @classmethod
    def verify(cls, *, revision, generated_sets, generated_items, problem):
        if revision.final_item_count_snapshot < 1:
            raise ApprovalConflict("Generated final item count evidence is invalid.")
        if len(generated_sets) != 2 or [row.set_code for row in generated_sets] != ["A", "B"]:
            raise ApprovalConflict("Exactly one generated Set A and Set B are required.")

        items_by_set = defaultdict(list)
        for item in generated_items:
            items_by_set[item.generated_set_id].append(item)

        normalized_problem_campus = {
            str(key): int(value) for key, value in problem.campus_quotas.items()
        }
        normalized_problem_difficulty = {
            str(key): int(value) for key, value in problem.difficulty_quotas.items()
        }
        normalized_problem_section = {
            str(key): int(value) for key, value in problem.section_quotas.items()
        }
        automatic_mode = (
            revision.cycle_course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        )
        source_sets = []
        aggregate = None
        difficulty_by_set = {}
        for generated_set in generated_sets:
            items = items_by_set[generated_set.id]
            expected_count = revision.final_item_count_snapshot
            if generated_set.item_count != expected_count or len(items) != expected_count:
                raise ApprovalConflict("Generated set item count is incomplete.")
            positions = [item.position for item in items]
            if positions != list(range(1, expected_count + 1)):
                raise ApprovalConflict("Generated set ordering is incomplete.")
            source_ids = [item.source_question_id for item in items]
            if len(source_ids) != len(set(source_ids)):
                raise ApprovalConflict("A generated set contains duplicate source questions.")

            campus_snapshot = cls._quota_snapshot(
                generated_set.campus_quotas_snapshot, label="campus"
            )
            difficulty_snapshot = cls._quota_snapshot(
                generated_set.difficulty_quotas_snapshot, label="difficulty"
            )
            section_snapshot = cls._quota_snapshot(
                generated_set.section_quotas_snapshot, label="section"
            )
            if (
                campus_snapshot != normalized_problem_campus
                or (
                    not automatic_mode
                    and difficulty_snapshot != normalized_problem_difficulty
                )
                or section_snapshot != normalized_problem_section
            ):
                raise ApprovalConflict("Generated quota snapshots do not match current inputs.")

            campus_actual = Counter()
            difficulty_actual = Counter()
            section_actual = Counter()
            scenarios = defaultdict(list)
            for item in items:
                cls._verify_item_snapshot(
                    item,
                    authoritative=problem.questions.get(item.source_question_id),
                )
                campus_actual[str(item.campus_code_snapshot)] += 1
                difficulty_actual[str(item.difficulty_snapshot)] += 1
                section_actual[str(item.section_id_snapshot or 0)] += 1
                if item.scenario_id_snapshot is not None:
                    scenarios[item.scenario_id_snapshot].append(item)
            if (
                dict(campus_actual) != campus_snapshot
                or dict(difficulty_actual) != difficulty_snapshot
                or dict(section_actual) != section_snapshot
            ):
                raise ApprovalConflict("Generated quota evidence does not match persisted items.")

            for scenario_items in scenarios.values():
                member_positions = [
                    item.scenario_member_position_snapshot for item in scenario_items
                ]
                item_positions = [item.position for item in scenario_items]
                if (
                    len(scenario_items) < 2
                    or
                    member_positions != list(range(1, len(scenario_items) + 1))
                    or item_positions
                    != list(range(item_positions[0], item_positions[0] + len(scenario_items)))
                    or len(
                        {
                            (
                                item.scenario_revision_snapshot,
                                item.scenario_title_snapshot,
                                item.scenario_stimulus_snapshot,
                                item.section_id_snapshot,
                            )
                            for item in scenario_items
                        }
                    )
                    != 1
                ):
                    raise ApprovalConflict("Generated scenario snapshot evidence is not contiguous.")

            source_sets.append(set(source_ids))
            this_aggregate = {
                "campus_counts": campus_snapshot,
                "section_counts": section_snapshot,
            }
            if aggregate is not None and this_aggregate != aggregate:
                raise ApprovalConflict("Set A and Set B quota evidence is inconsistent.")
            aggregate = this_aggregate
            difficulty_by_set[generated_set.set_code] = difficulty_snapshot

        overlap = len(source_sets[0].intersection(source_sets[1]))
        if overlap != revision.minimum_overlap:
            raise ApprovalConflict("Generated overlap evidence is inconsistent.")
        difficulty_counts = (
            difficulty_by_set
            if automatic_mode
            else difficulty_by_set.get("A", {})
        )
        return {
            **aggregate,
            "difficulty_counts": difficulty_counts,
            "difficulty_target": normalized_problem_difficulty,
            "overlap_count": overlap,
        }


class ExamApprovalLockService:
    ATTESTATION_VERSION = "stage6c-v1"

    @staticmethod
    def _expected_fingerprint(value):
        value = str(value or "").strip().lower()
        if not GeneratedExamIntegrityService.SHA256_RE.fullmatch(value):
            raise ValidationError("Expected source-input fingerprint is invalid.")
        return value

    @classmethod
    def _audit(cls, *, revision, actor, request, locked_at, integrity):
        course = revision.cycle_course
        AuditService.log_event(
            action="DE_EXAM_APPROVED_LOCKED",
            portal="ADMIN",
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
                "tenant_id": course.cycle.tenant_id,
                "cycle_id": course.cycle_id,
                "course_id": course.course_id,
                "cycle_course_id": course.id,
                "generation_revision": revision.revision_number,
                "algorithm_version": revision.algorithm_version,
                "final_item_count": revision.final_item_count_snapshot,
                "overlap_count": integrity["overlap_count"],
                "source_input_fingerprint": revision.source_input_fingerprint,
                "campus_counts": integrity["campus_counts"],
                "difficulty_counts": integrity["difficulty_counts"],
                "section_counts": integrity["section_counts"],
                "approval_attestation_version": cls.ATTESTATION_VERSION,
                "locked_at": locked_at,
            },
            request=request,
        )

    @classmethod
    @transaction.atomic
    def approve_and_lock(
        cls,
        *,
        revision_id,
        tenant_id,
        actor,
        expected_revision_number,
        expected_source_input_fingerprint,
        request=None,
    ):
        identity = ExamGenerationRevision.objects.filter(
            pk=revision_id,
            cycle_course__cycle__tenant_id=tenant_id,
        ).values("cycle_course_id").first()
        if identity is None:
            raise Http404

        cycle, course, configuration, blueprint, revisions = (
            ExamGenerationService._lock_generation_inputs(
                cycle_course_id=identity["cycle_course_id"],
                tenant_id=tenant_id,
            )
        )
        if (
            cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            raise PermissionDenied(
                "Automatic-mode generations do not use Approve & Lock."
            )
        current = ExamGenerationService._current_revision(revisions)
        target = next((row for row in revisions if row.id == revision_id), None)
        if target is None:
            raise Http404
        generated_sets = list(
            GeneratedExamSet.objects.select_for_update()
            .filter(generation_revision=target)
            .order_by("set_code", "id")
        )
        generated_items = list(
            GeneratedExamItem.objects.select_for_update()
            .filter(generated_set__generation_revision=target)
            .order_by("generated_set__set_code", "position", "id")
        )

        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=actor,
            cycle_course=course,
        )
        if current is None or current.id != target.id:
            raise ApprovalConflict("The target generation is no longer current.")
        try:
            expected_revision = int(expected_revision_number)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Expected generation revision is invalid.") from exc
        expected_fingerprint = cls._expected_fingerprint(
            expected_source_input_fingerprint
        )

        if target.status == ExamGenerationRevision.Status.LOCKED:
            if (
                expected_revision == target.revision_number
                and expected_fingerprint == target.source_input_fingerprint
                and target.current_marker == 1
            ):
                return ApprovalOutcome(revision=target, reused=True)
            raise ApprovalConflict("The locked generation does not match this retry.")

        require_stage6_open_cycle(cycle, conflict_class=ApprovalConflict)
        if target.status != ExamGenerationRevision.Status.GENERATED or target.current_marker != 1:
            raise ApprovalConflict("This generation lifecycle cannot be approved and locked.")
        if expected_revision != target.revision_number:
            raise ApprovalConflict("The generation revision changed after the page was loaded.")
        if expected_fingerprint != target.source_input_fingerprint:
            raise ApprovalConflict("The generated input fingerprint changed after page load.")
        if configuration is None or blueprint is None:
            raise ApprovalConflict("Generation prerequisites changed. Regenerate before approval.")

        problem, readiness = Stage6ReadinessService.build_problem(cycle_course=course)
        if problem is None or not readiness["ready"]:
            raise ApprovalConflict("Generation inputs drifted. Regenerate before approval.")
        if not (
            expected_fingerprint
            == target.source_input_fingerprint
            == problem.input_fingerprint
        ):
            raise ApprovalConflict("Generation inputs drifted. Regenerate before approval.")
        if (
            target.configuration_revision_snapshot != problem.configuration_revision
            or target.blueprint_revision_snapshot != problem.blueprint_revision
            or target.roster_boundary_snapshot != problem.roster_boundary
            or target.final_item_count_snapshot != problem.final_count
            or target.algorithm_version != GENERATION_ALGORITHM_VERSION
        ):
            raise ApprovalConflict("Generation snapshots drifted. Regenerate before approval.")

        integrity = GeneratedExamIntegrityService.verify(
            revision=target,
            generated_sets=generated_sets,
            generated_items=generated_items,
            problem=problem,
        )
        locked_at = timezone.now()
        target.status = ExamGenerationRevision.Status.LOCKED
        target.locked_at = locked_at
        target.locked_by = actor
        target.approval_attestation_version = cls.ATTESTATION_VERSION
        target.save(
            update_fields=[
                "status",
                "locked_at",
                "locked_by",
                "approval_attestation_version",
                "updated_at",
            ]
        )
        cls._audit(
            revision=target,
            actor=actor,
            request=request,
            locked_at=locked_at,
            integrity=integrity,
        )
        return ApprovalOutcome(revision=target)
