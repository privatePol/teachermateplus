from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.utils import timezone

from apps.core.services.audit import AuditService

from .contribution_authorization import ContributorEligibilityService
from .contribution_services import QuestionPayloadService, Stage5LockService
from .generation_algorithms import CAMPUS_WEIGHTS
from .final_lock import FinalExamLockPolicy
from .models import (
    BlockedContributionResolution,
    CourseExamConfiguration,
    CycleCourse,
    ExamBlueprint,
    ExamScenario,
    ExamScenarioMember,
    ExamSection,
    FacultyContribution,
    FacultyContributionEligibilitySource,
    Question,
    QuestionBlueprintPlacement,
    QuestionImportBatch,
)
from .services import DepartmentalExamAuthorizationService


class Stage6Conflict(ValidationError):
    """A stale Stage 6 revision, roster, or lifecycle conflict."""


STAGE6_CYCLE_NOT_OPEN_CODE = "CYCLE_NOT_OPEN"
STAGE6_CYCLE_NOT_OPEN_MESSAGE = "The examination cycle is not open for Stage 6 work."


def stage6_cycle_is_open(cycle):
    return cycle.status == cycle.Status.OPEN


def require_stage6_open_cycle(cycle, *, conflict_class=Stage6Conflict):
    if not stage6_cycle_is_open(cycle):
        raise conflict_class(STAGE6_CYCLE_NOT_OPEN_MESSAGE)


@dataclass(frozen=True)
class ContributorRosterReadiness:
    current: bool
    stale_reasons: tuple[str, ...]
    required_active_count: int
    submitted_required_count: int
    incomplete_active_count: int
    blocked_draft_count: int
    unresolved_blocked_count: int
    boundary_sha256: str = ""
    live_sha256: str = ""


def contribution_source_evidence(contribution) -> str:
    rows = sorted(
        (
            source.assignment_id_snapshot,
            source.offering_id_snapshot,
            source.tenant_id_snapshot,
            source.campus_id_snapshot,
            int(source.is_current),
        )
        for source in contribution.eligibility_sources.all()
    )
    material = "\n".join(":".join(str(value) for value in row) for row in rows)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _sha256_json(value) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def persisted_roster_boundary_evidence(*, configuration, contributions) -> str:
    """Hash the immutable close boundary without including confidential content."""
    rows = []
    for contribution in contributions:
        sources = sorted(
            (
                source.assignment_id_snapshot,
                source.offering_id_snapshot,
                source.tenant_id_snapshot,
                source.campus_id_snapshot,
                bool(source.is_current),
            )
            for source in contribution.eligibility_sources.all()
        )
        resolutions = sorted(
            (
                resolution.id,
                resolution.contribution_revision_snapshot,
                resolution.blocked_at_snapshot.isoformat(),
                resolution.source_evidence_sha256,
            )
            for resolution in contribution.blocked_resolution_events.all()
        )
        rows.append(
            (
                contribution.id,
                contribution.faculty_user_id,
                contribution.status,
                contribution.roster_status,
                contribution.revision,
                contribution.roster_blocked_at.isoformat()
                if contribution.roster_blocked_at
                else None,
                sources,
                resolutions,
            )
        )
    return _sha256_json(
        {
            "roster_revision": configuration.contributor_roster_revision,
            "contributions": rows,
        }
    )


def live_roster_evidence(inventory) -> str:
    eligible_ids = {assignment.id for assignment in inventory.eligible_sources}
    return _sha256_json(
        [
            (
                assignment.id,
                assignment.faculty_user_id,
                assignment.offering_id,
                assignment.tenant_id,
                assignment.campus_id,
                assignment.id in eligible_ids,
            )
            for assignment in inventory.all_sources
        ]
    )


def resolution_matches_episode(*, resolution, contribution, source_hash):
    return bool(
        resolution.blocked_at_snapshot == contribution.roster_blocked_at
        and resolution.contribution_revision_snapshot == contribution.revision
        and resolution.source_evidence_sha256 == source_hash
    )


class ContributorRosterReadinessService:
    @classmethod
    def evaluate(cls, *, cycle_course, configuration):
        stale_reasons = []
        if not configuration or configuration.contributor_roster_initialized_at is None:
            return ContributorRosterReadiness(
                current=False,
                stale_reasons=("Contributor roster is not initialized.",),
                required_active_count=0,
                submitted_required_count=0,
                incomplete_active_count=0,
                blocked_draft_count=0,
                unresolved_blocked_count=0,
            )

        contributions = list(
            FacultyContribution.objects.filter(cycle_course=cycle_course)
            .prefetch_related("eligibility_sources", "blocked_resolution_events")
            .order_by("id")
        )
        boundary_sha256 = persisted_roster_boundary_evidence(
            configuration=configuration,
            contributions=contributions,
        )

        # A successful Close freezes the persisted Stage 5 roster at its exact
        # roster revision. Ordinary later staffing changes are outside that
        # boundary and must not rewrite or invalidate Stage 6 readiness.
        if configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.CLOSED:
            required_active_count = 0
            submitted_required_count = 0
            incomplete_active_count = 0
            blocked_draft_count = 0
            unresolved_blocked_count = 0
            for contribution in contributions:
                current_sources = [
                    source
                    for source in contribution.eligibility_sources.all()
                    if source.is_current
                ]
                if contribution.status == FacultyContribution.Status.SUBMITTED:
                    if contribution.roster_status == FacultyContribution.RosterStatus.ACTIVE:
                        required_active_count += 1
                        submitted_required_count += 1
                    continue
                if contribution.roster_status == FacultyContribution.RosterStatus.ACTIVE:
                    required_active_count += 1
                    incomplete_active_count += 1
                    if not current_sources:
                        stale_reasons.append(
                            "Frozen Active contributor has no current source evidence."
                        )
                    continue
                blocked_draft_count += 1
                if current_sources:
                    stale_reasons.append(
                        "Frozen Blocked contributor retains current source evidence."
                    )
                source_hash = contribution_source_evidence(contribution)
                if not any(
                    resolution_matches_episode(
                        resolution=resolution,
                        contribution=contribution,
                        source_hash=source_hash,
                    )
                    for resolution in contribution.blocked_resolution_events.all()
                ):
                    unresolved_blocked_count += 1
            return ContributorRosterReadiness(
                current=not stale_reasons,
                stale_reasons=tuple(dict.fromkeys(stale_reasons)),
                required_active_count=required_active_count,
                submitted_required_count=submitted_required_count,
                incomplete_active_count=incomplete_active_count,
                blocked_draft_count=blocked_draft_count,
                unresolved_blocked_count=unresolved_blocked_count,
                boundary_sha256=boundary_sha256,
            )

        inventory = ContributorEligibilityService.source_inventory(
            cycle_course=cycle_course,
            allow_closed_contribution=True,
        )
        all_by_user = defaultdict(dict)
        eligible_by_user = defaultdict(set)
        for assignment in inventory.all_sources:
            key = (
                assignment.id,
                assignment.offering_id,
                assignment.tenant_id,
                assignment.campus_id,
            )
            all_by_user[assignment.faculty_user_id][key] = False
        for assignment in inventory.eligible_sources:
            key = (
                assignment.id,
                assignment.offering_id,
                assignment.tenant_id,
                assignment.campus_id,
            )
            all_by_user[assignment.faculty_user_id][key] = True
            eligible_by_user[assignment.faculty_user_id].add(key)

        by_user = {item.faculty_user_id: item for item in contributions}
        for user_id in eligible_by_user:
            if user_id not in by_user:
                stale_reasons.append("A currently eligible contributor is missing from the roster.")

        required_active_count = 0
        submitted_required_count = 0
        incomplete_active_count = 0
        blocked_draft_count = 0
        unresolved_blocked_count = 0

        for contribution in contributions:
            expected_eligible = eligible_by_user.get(contribution.faculty_user_id, set())
            if expected_eligible:
                required_active_count += 1
                if contribution.status == FacultyContribution.Status.SUBMITTED:
                    submitted_required_count += 1
                    # Submitted rows intentionally freeze their historical
                    # source attribution and are not rewritten by synchronize.
                    continue
                incomplete_active_count += 1
            elif contribution.status == FacultyContribution.Status.SUBMITTED:
                continue

            persisted = {
                (
                    source.assignment_id_snapshot,
                    source.offering_id_snapshot,
                    source.tenant_id_snapshot,
                    source.campus_id_snapshot,
                ): source.is_current
                for source in contribution.eligibility_sources.all()
            }
            expected = all_by_user.get(contribution.faculty_user_id, {})
            if persisted != expected:
                stale_reasons.append("Contributor source evidence differs from the live roster.")
            expected_status = (
                FacultyContribution.RosterStatus.ACTIVE
                if expected_eligible
                else FacultyContribution.RosterStatus.BLOCKED
            )
            if contribution.roster_status != expected_status:
                stale_reasons.append("Contributor roster status differs from live eligibility.")

            if not expected_eligible:
                blocked_draft_count += 1
                source_hash = contribution_source_evidence(contribution)
                valid_resolution = any(
                    resolution_matches_episode(
                        resolution=resolution,
                        contribution=contribution,
                        source_hash=source_hash,
                    )
                    for resolution in contribution.blocked_resolution_events.all()
                )
                if not valid_resolution:
                    unresolved_blocked_count += 1

        return ContributorRosterReadiness(
            current=not stale_reasons,
            stale_reasons=tuple(dict.fromkeys(stale_reasons)),
            required_active_count=required_active_count,
            submitted_required_count=submitted_required_count,
            incomplete_active_count=incomplete_active_count,
            blocked_draft_count=blocked_draft_count,
            unresolved_blocked_count=unresolved_blocked_count,
            boundary_sha256=boundary_sha256,
            live_sha256=live_roster_evidence(inventory),
        )


class BlockedContributionResolutionService:
    @classmethod
    @transaction.atomic
    def resolve(
        cls,
        *,
        contribution_id,
        tenant_id,
        actor,
        expected_contribution_revision,
        expected_roster_revision,
        reason,
        request=None,
    ):
        identity = FacultyContribution.objects.filter(
            pk=contribution_id,
            cycle_course__cycle__tenant_id=tenant_id,
        ).values("cycle_course_id").first()
        if identity is None:
            raise Http404
        _cycle, cycle_course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=identity["cycle_course_id"], tenant_id=tenant_id
        )
        DepartmentalExamAuthorizationService.require_configure_cycle_course(
            user=actor, cycle_course=cycle_course
        )
        if (
            configuration is None
            or configuration.workflow_status
            != CourseExamConfiguration.WorkflowStatus.OPEN
        ):
            raise Stage6Conflict("Blocked Drafts may be resolved only while contribution is open.")
        contribution = (
            FacultyContribution.objects.select_for_update()
            .prefetch_related("eligibility_sources")
            .get(pk=contribution_id, cycle_course=cycle_course)
        )
        list(
            FacultyContributionEligibilitySource.objects.select_for_update()
            .filter(contribution=contribution)
            .order_by("id")
        )
        if contribution.status != FacultyContribution.Status.DRAFT:
            raise ValidationError("Only a Draft contribution can be resolved.")
        if contribution.roster_status != FacultyContribution.RosterStatus.BLOCKED:
            raise ValidationError("Only a currently Blocked contribution can be resolved.")
        if contribution.roster_blocked_at is None:
            raise Stage6Conflict("The Blocked contribution episode is incomplete.")
        if contribution.revision != expected_contribution_revision:
            raise Stage6Conflict("The contribution changed after the page was loaded.")
        if configuration.contributor_roster_revision != expected_roster_revision:
            raise Stage6Conflict("The contributor roster changed after the page was loaded.")
        roster = ContributorRosterReadinessService.evaluate(
            cycle_course=cycle_course, configuration=configuration
        )
        if not roster.current:
            raise Stage6Conflict(
                "The contributor roster is stale. Synchronize it explicitly before resolving Blocked Drafts."
            )
        reason = (reason or "").strip()
        if not 10 <= len(reason) <= 500:
            raise ValidationError("Reason must be from 10 to 500 characters.")
        resolution_identity = {
            "contribution": contribution,
            "blocked_at_snapshot": contribution.roster_blocked_at,
            "contribution_revision_snapshot": contribution.revision,
        }
        if BlockedContributionResolution.objects.filter(
            **resolution_identity
        ).exists():
            raise Stage6Conflict(
                "This exact Blocked contribution evidence state is already resolved."
            )
        resolution = BlockedContributionResolution(
            tenant_id=tenant_id,
            cycle_course=cycle_course,
            contribution=contribution,
            reason=reason,
            resolved_by=actor,
            resolved_at=timezone.now(),
            contribution_revision_snapshot=contribution.revision,
            roster_revision_snapshot=configuration.contributor_roster_revision,
            blocked_at_snapshot=contribution.roster_blocked_at,
            source_evidence_sha256=contribution_source_evidence(contribution),
        )
        resolution.full_clean()
        try:
            resolution.save()
        except IntegrityError as exc:
            raise Stage6Conflict(
                "This exact Blocked contribution evidence state is already resolved."
            ) from exc
        AuditService.log_event(
            action="DE_EXAM_BLOCKED_CONTRIBUTION_RESOLVED",
            portal="ADMIN",
            entity_type="BlockedContributionResolution",
            entity_id=resolution.id,
            actor=actor,
            tenant=tenant_id,
            campus=(
                cycle_course.responsible_department.campus_id
                if cycle_course.responsible_department_id
                else None
            ),
            metadata={
                "cycle_id": cycle_course.cycle_id,
                "cycle_course_id": cycle_course.id,
                "contribution_revision_snapshot": contribution.revision,
                "roster_revision_snapshot": configuration.contributor_roster_revision,
                "reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
                "reason_length": len(reason),
            },
            request=request,
        )
        return resolution


class BlueprintMutationService:
    @staticmethod
    def _require_prelock(*, cycle_course, configuration):
        FinalExamLockPolicy.require_not_locked(cycle_course)
        if cycle_course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED:
            raise ValidationError("Only Included course examinations may use a blueprint.")
        require_stage6_open_cycle(cycle_course.cycle)
        if (
            configuration is None
            or configuration.workflow_status
            != CourseExamConfiguration.WorkflowStatus.CLOSED
        ):
            raise Stage6Conflict("Close faculty contribution before Stage 6 blueprint work.")

    @staticmethod
    def _audit(*, action, blueprint, actor, request, metadata=None):
        course = blueprint.cycle_course
        AuditService.log_event(
            action=action,
            portal="ADMIN",
            entity_type="ExamBlueprint",
            entity_id=blueprint.id,
            actor=actor,
            tenant=course.cycle.tenant_id,
            campus=(course.responsible_department.campus_id if course.responsible_department_id else None),
            metadata={
                "cycle_id": course.cycle_id,
                "cycle_course_id": course.id,
                "blueprint_revision": blueprint.revision,
                **(metadata or {}),
            },
            request=request,
        )

    @classmethod
    @transaction.atomic
    def save_structure(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        actor,
        expected_revision,
        mode,
        sections,
        request=None,
    ):
        _cycle, course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=cycle_course_id, tenant_id=tenant_id
        )
        DepartmentalExamAuthorizationService.require_configure_cycle_course(
            user=actor, cycle_course=course
        )
        cls._require_prelock(cycle_course=course, configuration=configuration)
        blueprint = (
            ExamBlueprint.objects.select_for_update()
            .filter(cycle_course=course)
            .first()
        )
        if blueprint is None:
            if expected_revision not in (None, 0):
                raise Stage6Conflict("The blueprint was created after the page was loaded.")
            blueprint = ExamBlueprint(
                cycle_course=course,
                mode=mode,
                revision=1,
                created_by=actor,
                updated_by=actor,
            )
            creating = True
        else:
            if blueprint.revision != expected_revision:
                raise Stage6Conflict("The blueprint changed after the page was loaded.")
            creating = False
        if mode not in ExamBlueprint.Mode.values:
            raise ValidationError("Unsupported blueprint mode.")

        normalized = []
        seen_orders = set()
        seen_ids = set()
        for row in sections or ():
            section_id = row.get("id")
            title = (row.get("title") or "").strip()
            instructions = (row.get("instructions") or "").strip()
            try:
                display_order = int(row.get("display_order"))
                item_quota = int(row.get("item_quota"))
            except (TypeError, ValueError) as exc:
                raise ValidationError("Section order and quota must be integers.") from exc
            if not title or len(title) > 200:
                raise ValidationError("Each section requires a title of at most 200 characters.")
            if len(instructions) > 2000:
                raise ValidationError("Section instructions may not exceed 2,000 characters.")
            if display_order <= 0 or item_quota <= 0:
                raise ValidationError("Section order and quota must be positive.")
            if display_order in seen_orders:
                raise ValidationError("Section display order must be unique.")
            seen_orders.add(display_order)
            if section_id:
                section_id = int(section_id)
                if section_id in seen_ids:
                    raise ValidationError("A section may appear only once.")
                seen_ids.add(section_id)
            normalized.append(
                {
                    "id": section_id,
                    "title": title,
                    "instructions": instructions,
                    "display_order": display_order,
                    "item_quota": item_quota,
                }
            )

        if mode == ExamBlueprint.Mode.NO_SECTIONS:
            if normalized:
                raise ValidationError("No Sections mode cannot retain explicit sections.")
        else:
            if not normalized:
                raise ValidationError("Use Sections mode requires at least one section.")
            if sum(row["item_quota"] for row in normalized) != configuration.final_item_count:
                raise ValidationError("Section quotas must equal the configured final item count exactly.")

        existing = {
            item.id: item
            for item in ExamSection.objects.select_for_update()
            .filter(blueprint=blueprint)
            .order_by("id")
        } if not creating else {}
        unknown_ids = seen_ids - set(existing)
        if unknown_ids:
            raise Http404
        removed = [item for item_id, item in existing.items() if item_id not in seen_ids]
        if any(item.question_placements.exists() or item.scenarios.exists() for item in removed):
            raise Stage6Conflict("Remove confidential placements and scenarios before deleting their section.")
        if mode != blueprint.mode and (
            blueprint.question_placements.exists() or blueprint.scenarios.exists()
        ):
            raise Stage6Conflict("Remove confidential placements and scenarios before changing blueprint mode.")

        before = (
            blueprint.mode,
            tuple(
                (item.id, item.title, item.instructions, item.display_order, item.item_quota)
                for item in existing.values()
            ),
        )
        after = (
            mode,
            tuple(
                (row["id"], row["title"], row["instructions"], row["display_order"], row["item_quota"])
                for row in normalized
            ),
        )
        if not creating and before == after:
            return blueprint, False
        blueprint.mode = mode
        blueprint.updated_by = actor
        if not creating:
            blueprint.revision += 1
        blueprint.full_clean()
        blueprint.save()

        for offset, section in enumerate(existing.values(), start=1):
            section.display_order = 10_000 + offset
            section.save(update_fields=["display_order", "updated_at"])
        for item in removed:
            item.delete()
        for row in normalized:
            section = existing.get(row["id"]) if row["id"] else None
            if section is None:
                section = ExamSection(blueprint=blueprint)
            section.title = row["title"]
            section.instructions = row["instructions"]
            section.display_order = row["display_order"]
            section.item_quota = row["item_quota"]
            section.full_clean()
            section.save()
        cls._audit(
            action="DE_EXAM_BLUEPRINT_CREATED" if creating else "DE_EXAM_BLUEPRINT_UPDATED",
            blueprint=blueprint,
            actor=actor,
            request=request,
            metadata={"mode": mode, "section_count": len(normalized)},
        )
        return blueprint, True


def _stage6_question_identity(*, question_id, tenant_id):
    identity = Question.objects.filter(
        pk=question_id,
        contribution__cycle_course__cycle__tenant_id=tenant_id,
    ).values("contribution__cycle_course_id").first()
    if identity is None:
        raise Http404
    return identity


# Canonical confidential-overlay order after the Stage 5 parent locks:
# blueprint, Questions by PK, required Section, placements by PK, scenario,
# then scenario members by PK. Blueprint ownership serializes structure writes.
def _lock_stage6_blueprint(*, cycle_course):
    return (
        ExamBlueprint.objects.select_for_update()
        .filter(cycle_course=cycle_course)
        .first()
    )


def _lock_stage6_questions(*, cycle_course, question_ids):
    return list(
        Question.objects.select_for_update()
        .select_related("contribution__source_campus", "import_batch")
        .filter(
            pk__in=question_ids,
            contribution__cycle_course=cycle_course,
            contribution__status=FacultyContribution.Status.SUBMITTED,
        )
        .order_by("id")
    )


class QuestionPlacementService:
    @classmethod
    @transaction.atomic
    def place(
        cls,
        *,
        question_id,
        section_id,
        tenant_id,
        actor,
        expected_placement_revision=None,
        request=None,
    ):
        identity = _stage6_question_identity(
            question_id=question_id, tenant_id=tenant_id
        )
        _cycle, course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=identity["contribution__cycle_course_id"],
            tenant_id=tenant_id,
        )
        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=actor, cycle_course=course
        )
        BlueprintMutationService._require_prelock(cycle_course=course, configuration=configuration)
        blueprint = _lock_stage6_blueprint(cycle_course=course)
        if blueprint is None or blueprint.mode != ExamBlueprint.Mode.USE_SECTIONS:
            raise Stage6Conflict("Use Sections blueprint configuration is required before classification.")
        question = get_stage6_question(
            question_id=question_id, tenant_id=tenant_id, for_update=True
        )
        if question.contribution.cycle_course_id != course.id:
            raise Http404
        require_eligible_stage6_question(question=question, cycle_course=course)
        section = ExamSection.objects.select_for_update().filter(
            pk=section_id, blueprint=blueprint
        ).first()
        if section is None:
            raise Http404
        placement = QuestionBlueprintPlacement.objects.select_for_update().filter(
            question=question
        ).order_by("id").first()
        if placement is not None and placement.blueprint_id != blueprint.id:
            raise Stage6Conflict("Question already belongs to a different blueprint placement.")
        if placement is not None and placement.revision != expected_placement_revision:
            raise Stage6Conflict("The question placement changed after the page was loaded.")
        if placement is None:
            if expected_placement_revision not in (None, 0):
                raise Stage6Conflict("The question was classified after the page was loaded.")
            placement = QuestionBlueprintPlacement(
                blueprint=blueprint,
                question=question,
                section=section,
                placed_by=actor,
                revision=1,
            )
            changed = True
        else:
            changed = placement.section_id != section.id
            if changed:
                placement.section = section
                placement.placed_by = actor
                placement.revision += 1
        if changed:
            placement.full_clean()
            placement.save()
            AuditService.log_event(
                action="DE_EXAM_QUESTION_PLACED",
                portal="ADMIN",
                entity_type="QuestionBlueprintPlacement",
                entity_id=placement.id,
                actor=actor,
                tenant=tenant_id,
                campus=course.responsible_department.campus_id,
                metadata={
                    "cycle_id": course.cycle_id,
                    "cycle_course_id": course.id,
                    "blueprint_revision": blueprint.revision,
                    "placement_revision": placement.revision,
                },
                request=request,
            )
        return placement, changed


def get_stage6_question(*, question_id, tenant_id, for_update=False):
    queryset = Question.objects.select_related(
        "contribution__cycle_course__cycle",
        "contribution__cycle_course__responsible_department",
        "contribution__source_campus",
        "import_batch",
    )
    if for_update:
        queryset = queryset.select_for_update()
    question = queryset.filter(
        pk=question_id,
        contribution__cycle_course__cycle__tenant_id=tenant_id,
        contribution__status=FacultyContribution.Status.SUBMITTED,
        contribution__cycle_course__inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
    ).first()
    if question is None:
        raise Http404
    return question


def require_eligible_stage6_question(*, question, cycle_course):
    participating_codes = {
        (code or "").strip().upper()
        for code in cycle_course.offering_snapshots.values_list(
            "campus__code", flat=True
        )
    }
    source_code = (question.contribution.source_campus.code or "").strip().upper()
    if (
        source_code not in participating_codes
        or source_code not in CAMPUS_WEIGHTS
    ):
        raise ValidationError("Question frozen campus is not a recognized participating campus.")
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
    if (
        question.import_batch_id
        and question.import_batch.status != QuestionImportBatch.Status.CONFIRMED
    ):
        raise ValidationError("Only confirmed persisted questions may be classified.")


class ScenarioMutationService:
    @classmethod
    @transaction.atomic
    def save(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        actor,
        title,
        stimulus,
        question_ids,
        section_id=None,
        scenario_id=None,
        expected_revision=0,
        request=None,
    ):
        _cycle, course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=cycle_course_id, tenant_id=tenant_id
        )
        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=actor, cycle_course=course
        )
        BlueprintMutationService._require_prelock(cycle_course=course, configuration=configuration)
        blueprint = _lock_stage6_blueprint(cycle_course=course)
        if blueprint is None:
            raise Stage6Conflict("Configure the examination blueprint first.")
        title = (title or "").strip()
        stimulus = (stimulus or "").strip()
        if len(title) > 200:
            raise ValidationError("Scenario title may not exceed 200 characters.")
        if not stimulus or len(stimulus) > 5000:
            raise ValidationError("Scenario text is required and may not exceed 5,000 characters.")
        try:
            ordered_ids = [int(value) for value in question_ids]
        except (TypeError, ValueError) as exc:
            raise ValidationError("Scenario question numbers must be integers.") from exc
        if len(ordered_ids) < 2 or len(ordered_ids) != len(set(ordered_ids)):
            raise ValidationError("A scenario requires at least two distinct ordered questions.")
        questions = _lock_stage6_questions(
            cycle_course=course,
            question_ids=ordered_ids,
        )
        if len(questions) != len(ordered_ids):
            raise Http404
        by_id = {question.id: question for question in questions}
        for question in questions:
            require_eligible_stage6_question(question=question, cycle_course=course)
        section = None
        if blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS:
            section = ExamSection.objects.select_for_update().filter(
                pk=section_id, blueprint=blueprint
            ).first()
            if section is None:
                raise Http404
            locked_placements = list(
                QuestionBlueprintPlacement.objects.select_for_update().filter(
                    question_id__in=ordered_ids, blueprint=blueprint
                ).order_by("id")
            )
            placement_sections = {
                placement.question_id: placement.section_id
                for placement in locked_placements
            }
            if any(placement_sections.get(question_id) != section.id for question_id in ordered_ids):
                raise ValidationError("Every scenario member must be classified in the scenario section.")
        elif section_id not in (None, "", 0, "0"):
            raise ValidationError("No Sections scenarios use the implicit section.")

        scenario = None
        if scenario_id is not None:
            scenario = ExamScenario.objects.select_for_update().filter(
                pk=scenario_id, blueprint=blueprint
            ).first()
            if scenario is None:
                raise Http404
            if scenario.revision != expected_revision:
                raise Stage6Conflict("The scenario changed after the page was loaded.")
        elif expected_revision not in (None, 0):
            raise Stage6Conflict("The scenario was created after the page was loaded.")
        member_filter = Q(question_id__in=ordered_ids)
        if scenario is not None:
            member_filter |= Q(scenario=scenario)
        locked_members = list(
            ExamScenarioMember.objects.select_for_update()
            .filter(member_filter)
            .order_by("id")
        )
        if any(
            scenario is None or member.scenario_id != scenario.id
            for member in locked_members
            if member.question_id in ordered_ids
        ):
            raise ValidationError("A question may belong to at most one scenario.")

        creating = scenario is None
        if creating:
            scenario = ExamScenario(
                blueprint=blueprint,
                created_by=actor,
                updated_by=actor,
                revision=1,
            )
        else:
            scenario.revision += 1
            scenario.updated_by = actor
        scenario.section = section
        scenario.title = title
        scenario.stimulus = stimulus
        scenario.full_clean()
        scenario.save()
        if not creating:
            scenario.members.all().delete()
        ExamScenarioMember.objects.bulk_create(
            [
                ExamScenarioMember(
                    scenario=scenario,
                    question=by_id[question_id],
                    position=position,
                )
                for position, question_id in enumerate(ordered_ids, start=1)
            ]
        )
        AuditService.log_event(
            action="DE_EXAM_SCENARIO_CREATED" if creating else "DE_EXAM_SCENARIO_UPDATED",
            portal="ADMIN",
            entity_type="ExamScenario",
            entity_id=scenario.id,
            actor=actor,
            tenant=tenant_id,
            campus=course.responsible_department.campus_id,
            metadata={
                "cycle_id": course.cycle_id,
                "cycle_course_id": course.id,
                "blueprint_revision": blueprint.revision,
                "scenario_revision": scenario.revision,
                "member_count": len(ordered_ids),
            },
            request=request,
        )
        return scenario, True

    @classmethod
    @transaction.atomic
    def delete(
        cls,
        *,
        scenario_id,
        tenant_id,
        actor,
        expected_revision,
        request=None,
    ):
        identity = ExamScenario.objects.filter(
            pk=scenario_id,
            blueprint__cycle_course__cycle__tenant_id=tenant_id,
        ).values("blueprint__cycle_course_id").first()
        if identity is None:
            raise Http404
        _cycle, course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=identity["blueprint__cycle_course_id"], tenant_id=tenant_id
        )
        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=actor, cycle_course=course
        )
        BlueprintMutationService._require_prelock(cycle_course=course, configuration=configuration)
        blueprint = _lock_stage6_blueprint(cycle_course=course)
        if blueprint is None:
            raise Http404
        scenario = ExamScenario.objects.select_for_update().get(
            pk=scenario_id, blueprint=blueprint
        )
        if scenario.revision != expected_revision:
            raise Stage6Conflict("The scenario changed after the page was loaded.")
        locked_members = list(
            ExamScenarioMember.objects.select_for_update()
            .filter(scenario=scenario)
            .order_by("id")
        )
        member_count = len(locked_members)
        AuditService.log_event(
            action="DE_EXAM_SCENARIO_DELETED",
            portal="ADMIN",
            entity_type="ExamScenario",
            entity_id=scenario.id,
            actor=actor,
            tenant=tenant_id,
            campus=course.responsible_department.campus_id,
            metadata={
                "cycle_id": course.cycle_id,
                "cycle_course_id": course.id,
                "scenario_revision": scenario.revision,
                "member_count": member_count,
            },
            request=request,
        )
        scenario.delete()
        return course.id
