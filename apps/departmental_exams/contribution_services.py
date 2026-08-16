from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter, defaultdict

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from apps.core.services.audit import AuditService

from .contribution_authorization import (
    ContributionAuthorizationService,
    ContributionConflict,
    ContributorEligibilityService,
)
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExaminationCycle,
    FacultyContribution,
    FacultyContributionEligibilitySource,
    Question,
)
from .services import DepartmentalExamAuthorizationService


class Stage5LockService:
    @staticmethod
    def lock_cycle_course(*, cycle_course_id, tenant_id):
        identity = CycleCourse.objects.filter(
            pk=cycle_course_id,
            cycle__tenant_id=tenant_id,
        ).values("cycle_id").first()
        if identity is None:
            raise Http404
        cycle = ExaminationCycle.objects.select_for_update().select_related("tenant").get(
            pk=identity["cycle_id"], tenant_id=tenant_id
        )
        cycle_course = (
            CycleCourse.objects.select_for_update()
            .select_related(
                "cycle",
                "cycle__tenant",
                "course",
                "responsible_department",
            )
            .get(pk=cycle_course_id, cycle=cycle)
        )
        configuration = (
            CourseExamConfiguration.objects.select_for_update()
            .filter(cycle_course=cycle_course)
            .first()
        )
        if configuration is not None:
            cycle_course.configuration = configuration
        return cycle, cycle_course, configuration

    @classmethod
    def lock_contribution(cls, *, contribution_id, user, tenant_id):
        identity = FacultyContribution.objects.filter(
            pk=contribution_id,
            faculty_user=user,
            cycle_course__cycle__tenant_id=tenant_id,
        ).values("cycle_course_id").first()
        if identity is None:
            raise Http404
        cycle, cycle_course, configuration = cls.lock_cycle_course(
            cycle_course_id=identity["cycle_course_id"],
            tenant_id=tenant_id,
        )
        contribution = (
            FacultyContribution.objects.select_for_update()
            .select_related("faculty_user", "source_campus")
            .get(pk=contribution_id, faculty_user=user, cycle_course=cycle_course)
        )
        contribution.cycle_course = cycle_course
        return cycle, cycle_course, configuration, contribution


class ContributionRosterService:
    BATCH_SIZE = 200

    @classmethod
    def _synchronize_locked(
        cls,
        *,
        cycle_course,
        configuration,
        actor,
        request,
        initializing,
    ):
        if configuration is None or configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.OPEN:
            raise ValidationError("The course must be open for faculty contribution.")
        quota = configuration.questions_required_per_faculty
        if quota is None or not 50 <= quota <= 75:
            raise ValidationError("A valid materialized faculty quota is required.")
        if initializing and configuration.contributor_roster_initialized_at is not None:
            return {"changed": False, "created": 0, "activated": 0, "blocked": 0}
        if not initializing and configuration.contributor_roster_initialized_at is None:
            raise ValidationError("Initialize the contributor roster before synchronizing it.")

        inventory = ContributorEligibilityService.source_inventory(
            cycle_course=cycle_course
        )
        assignments_by_user = defaultdict(list)
        eligible_ids_by_user = defaultdict(set)
        for assignment in inventory.all_sources:
            assignments_by_user[assignment.faculty_user_id].append(assignment)
        for assignment in inventory.eligible_sources:
            eligible_ids_by_user[assignment.faculty_user_id].add(assignment.id)

        contributions = list(
            FacultyContribution.objects.select_for_update()
            .filter(cycle_course=cycle_course)
            .order_by("id")
        )
        preexisting_contribution_ids = {item.id for item in contributions}
        contributions_by_user = {item.faculty_user_id: item for item in contributions}
        creates = []
        for faculty_user_id, eligible_ids in eligible_ids_by_user.items():
            if faculty_user_id in contributions_by_user or not eligible_ids:
                continue
            primary = min(
                (
                    assignment
                    for assignment in assignments_by_user[faculty_user_id]
                    if assignment.id in eligible_ids
                ),
                key=lambda item: item.id,
            )
            creates.append(
                FacultyContribution(
                    cycle_course=cycle_course,
                    faculty_user_id=faculty_user_id,
                    source_assignment=primary,
                    source_campus_id=ContributorEligibilityService._effective_scope(primary)[1],
                    quota_snapshot=quota,
                    configuration_revision_snapshot=configuration.revision,
                    revision=1,
                    roster_status=FacultyContribution.RosterStatus.ACTIVE,
                    roster_blocked_at=None,
                    status=FacultyContribution.Status.DRAFT,
                )
            )
        if creates:
            FacultyContribution.objects.bulk_create(creates, batch_size=cls.BATCH_SIZE)
            contributions = list(
                FacultyContribution.objects.select_for_update()
                .filter(cycle_course=cycle_course)
                .order_by("id")
            )
            contributions_by_user = {item.faculty_user_id: item for item in contributions}

        existing_sources = list(
            FacultyContributionEligibilitySource.objects.select_for_update()
            .filter(contribution__cycle_course=cycle_course)
            .order_by("contribution_id", "assignment_id_snapshot")
        )
        source_map = {
            (item.contribution_id, item.assignment_id_snapshot): item
            for item in existing_sources
        }
        now = timezone.now()
        source_creates = []
        source_updates = []
        contribution_updates = []
        rebinds = []
        activated = 0
        blocked = 0

        for contribution in contributions:
            if contribution.status == FacultyContribution.Status.SUBMITTED:
                # Submission freezes roster attribution and source history for
                # this exact examination cycle. Live staffing changes may still
                # create a separate Draft for another independently eligible
                # faculty member, but must never rewrite this historical row.
                continue
            assignments = assignments_by_user.get(contribution.faculty_user_id, [])
            eligible_ids = eligible_ids_by_user.get(contribution.faculty_user_id, set())
            seen_ids = set()
            material_evidence_changed = False
            for assignment in assignments:
                seen_ids.add(assignment.id)
                current = assignment.id in eligible_ids
                source = source_map.get((contribution.id, assignment.id))
                if source is None:
                    source_creates.append(
                        FacultyContributionEligibilitySource(
                            contribution=contribution,
                            assignment=assignment,
                            assignment_id_snapshot=assignment.id,
                            offering_id_snapshot=assignment.offering_id,
                            tenant_id_snapshot=assignment.tenant_id or assignment.offering.tenant_id,
                            campus_id_snapshot=assignment.campus_id or assignment.offering.campus_id,
                            is_current=current,
                            invalidated_at=None if current else now,
                        )
                    )
                    material_evidence_changed = True
                    continue
                changed = False
                if source.assignment_id != assignment.id:
                    source.assignment = assignment
                    changed = True
                if source.is_current != current:
                    source.is_current = current
                    source.invalidated_at = None if current else now
                    changed = True
                    material_evidence_changed = True
                if changed:
                    source_updates.append(source)

            for source in existing_sources:
                if (
                    source.contribution_id == contribution.id
                    and source.assignment_id_snapshot not in seen_ids
                    and source.is_current
                ):
                    source.is_current = False
                    source.invalidated_at = now
                    source_updates.append(source)
                    material_evidence_changed = True

            eligible_assignments = sorted(
                (item for item in assignments if item.id in eligible_ids),
                key=lambda item: item.id,
            )
            desired_status = (
                FacultyContribution.RosterStatus.ACTIVE
                if eligible_assignments
                else FacultyContribution.RosterStatus.BLOCKED
            )
            changed_fields = []
            if eligible_assignments:
                primary = eligible_assignments[0]
                primary_scope = ContributorEligibilityService._effective_scope(primary)
                if contribution.source_assignment_id != primary.id:
                    rebinds.append((contribution.id, contribution.source_assignment_id, primary.id))
                    contribution.source_assignment = primary
                    changed_fields.append("source_assignment")
                    material_evidence_changed = True
                if contribution.source_campus_id != primary_scope[1]:
                    contribution.source_campus_id = primary_scope[1]
                    changed_fields.append("source_campus")
                    material_evidence_changed = True
            if contribution.roster_status != desired_status:
                contribution.roster_status = desired_status
                contribution.roster_blocked_at = None if eligible_assignments else now
                changed_fields.extend(["roster_status", "roster_blocked_at"])
                material_evidence_changed = True
                if desired_status == FacultyContribution.RosterStatus.ACTIVE:
                    activated += 1
                else:
                    blocked += 1
            elif desired_status == FacultyContribution.RosterStatus.ACTIVE and contribution.roster_blocked_at is not None:
                contribution.roster_blocked_at = None
                changed_fields.append("roster_blocked_at")
                material_evidence_changed = True
            elif desired_status == FacultyContribution.RosterStatus.BLOCKED and contribution.roster_blocked_at is None:
                contribution.roster_blocked_at = now
                changed_fields.append("roster_blocked_at")
                material_evidence_changed = True
            if (
                material_evidence_changed
                and contribution.id in preexisting_contribution_ids
                and desired_status == FacultyContribution.RosterStatus.BLOCKED
            ):
                contribution.revision += 1
                changed_fields.append("revision")
            if changed_fields:
                contribution_updates.append(contribution)

        if source_creates:
            FacultyContributionEligibilitySource.objects.bulk_create(
                source_creates, batch_size=cls.BATCH_SIZE
            )
        if source_updates:
            FacultyContributionEligibilitySource.objects.bulk_update(
                source_updates,
                ["assignment", "is_current", "invalidated_at", "updated_at"],
                batch_size=cls.BATCH_SIZE,
            )
        if contribution_updates:
            FacultyContribution.objects.bulk_update(
                contribution_updates,
                [
                    "source_assignment",
                    "source_campus",
                    "roster_status",
                    "roster_blocked_at",
                    "revision",
                    "updated_at",
                ],
                batch_size=cls.BATCH_SIZE,
            )

        changed = bool(creates or source_creates or source_updates or contribution_updates)
        if initializing:
            configuration.contributor_roster_initialized_at = now
            configuration.contributor_roster_initialized_by = actor
            configuration.contributor_roster_revision = 1
            configuration.save(update_fields=[
                "contributor_roster_initialized_at",
                "contributor_roster_initialized_by",
                "contributor_roster_revision",
                "updated_at",
            ])
            changed = True
        elif changed:
            configuration.contributor_roster_revision += 1
            configuration.save(update_fields=["contributor_roster_revision", "updated_at"])

        if changed:
            action = (
                "DE_EXAM_CONTRIBUTOR_ROSTER_INITIALIZED"
                if initializing
                else "DE_EXAM_CONTRIBUTOR_ROSTER_SYNCHRONIZED"
            )
            AuditService.log_event(
                action=action,
                portal="ADMIN",
                entity_type="CycleCourse",
                entity_id=cycle_course.id,
                actor=actor,
                tenant=cycle_course.cycle.tenant_id,
                campus=(
                    cycle_course.responsible_department.campus_id
                    if cycle_course.responsible_department_id
                    else None
                ),
                metadata={
                    "cycle_id": cycle_course.cycle_id,
                    "configuration_id": configuration.id,
                    "configuration_revision": configuration.revision,
                    "roster_revision": configuration.contributor_roster_revision,
                    "contributions_created": len(creates),
                    "contributions_activated": activated,
                    "contributions_blocked": blocked,
                    "valid_source_count": len(inventory.eligible_sources),
                    "observed_source_count": len(inventory.all_sources),
                    "batch_size": cls.BATCH_SIZE,
                },
                request=request,
            )
            for contribution_id, previous_id, resulting_id in rebinds:
                AuditService.log_event(
                    action="DE_EXAM_CONTRIBUTION_ASSIGNMENT_RESOLVED",
                    portal="ADMIN",
                    entity_type="FacultyContribution",
                    entity_id=contribution_id,
                    actor=actor,
                    tenant=cycle_course.cycle.tenant_id,
                    metadata={
                        "previous_assignment_snapshot_id": previous_id,
                        "resulting_assignment_snapshot_id": resulting_id,
                    },
                    request=request,
                )
        return {
            "changed": changed,
            "created": len(creates),
            "activated": activated,
            "blocked": blocked,
        }

    @classmethod
    @transaction.atomic
    def initialize(cls, *, cycle_course_id, tenant_id, actor, request=None):
        _cycle, cycle_course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        DepartmentalExamAuthorizationService.require_configure_cycle_course(
            user=actor, cycle_course=cycle_course
        )
        return cls._synchronize_locked(
            cycle_course=cycle_course,
            configuration=configuration,
            actor=actor,
            request=request,
            initializing=True,
        )

    @classmethod
    @transaction.atomic
    def synchronize(cls, *, cycle_course_id, tenant_id, actor, request=None):
        _cycle, cycle_course, configuration = Stage5LockService.lock_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        DepartmentalExamAuthorizationService.require_configure_cycle_course(
            user=actor, cycle_course=cycle_course
        )
        return cls._synchronize_locked(
            cycle_course=cycle_course,
            configuration=configuration,
            actor=actor,
            request=request,
            initializing=False,
        )

    @classmethod
    def initialize_for_open_locked(cls, *, cycle_course, configuration, actor, request=None):
        return cls._synchronize_locked(
            cycle_course=cycle_course,
            configuration=configuration,
            actor=actor,
            request=request,
            initializing=True,
        )


class QuestionPayloadService:
    TEXT_FIELDS = ("question_text", "choice_a", "choice_b", "choice_c", "choice_d")
    CHOICE_FIELDS = ("choice_a", "choice_b", "choice_c", "choice_d")
    BIDI_CONTROL_CODEPOINTS = frozenset(
        {
            0x061C,
            0x200E,
            0x200F,
            *range(0x202A, 0x202F),
            *range(0x2066, 0x206A),
        }
    )
    UNSUPPORTED_CHARACTER_MESSAGE = (
        "Control and bidirectional formatting characters are not allowed."
    )

    @staticmethod
    def normalize_text(value):
        return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    @classmethod
    def has_unsupported_characters(cls, value):
        normalized_newlines = (
            (value or "").replace("\r\n", "\n").replace("\r", "\n")
        )
        for character in normalized_newlines:
            codepoint = ord(character)
            if (
                (codepoint <= 0x001F and codepoint != 0x000A)
                or 0x007F <= codepoint <= 0x009F
                or 0xD800 <= codepoint <= 0xDFFF
                or codepoint == 0xFEFF
                or codepoint in cls.BIDI_CONTROL_CODEPOINTS
            ):
                return True
        return False

    @staticmethod
    def comparison_value(value):
        normalized = unicodedata.normalize("NFKC", value or "")
        return " ".join(normalized.split()).casefold()

    @classmethod
    def question_fingerprint(cls, value):
        return hashlib.sha256(cls.comparison_value(value).encode("utf-8")).hexdigest()

    @classmethod
    def validate(cls, payload):
        source_text = {
            field: payload.get(field) or ""
            for field in cls.TEXT_FIELDS
        }
        cleaned = {
            field: cls.normalize_text(source_text[field])
            for field in cls.TEXT_FIELDS
        }
        errors = defaultdict(list)
        for field, value in source_text.items():
            if cls.has_unsupported_characters(value):
                errors[field].append(cls.UNSUPPORTED_CHARACTER_MESSAGE)
        if not cleaned["question_text"]:
            errors["question_text"].append("Question text is required.")
        elif len(cleaned["question_text"]) > 5000:
            errors["question_text"].append("Question text may not exceed 5,000 characters.")
        for field in cls.CHOICE_FIELDS:
            if not cleaned[field]:
                errors[field].append("This choice is required.")
            elif len(cleaned[field]) > 1000:
                errors[field].append("Each choice may not exceed 1,000 characters.")
        comparisons = [cls.comparison_value(cleaned[field]) for field in cls.CHOICE_FIELDS]
        nonblank = [value for value in comparisons if value]
        if len(nonblank) != len(set(nonblank)):
            errors["choices"].append("Choices must be distinct after text normalization.")
        answer = (payload.get("correct_answer") or "").strip().upper()
        if answer not in {"A", "B", "C", "D"}:
            errors["correct_answer"].append("Correct answer must be A, B, C, or D.")
        difficulty_lookup = {item.lower(): item for item in Question.Difficulty.values}
        difficulty_lookup.update({label.lower(): value for value, label in Question.Difficulty.choices})
        difficulty = difficulty_lookup.get((payload.get("difficulty") or "").strip().lower())
        if difficulty is None:
            errors["difficulty"].append("Difficulty must be Easy, Moderate, or Difficult.")
        if errors:
            raise ValidationError(dict(errors))
        cleaned["correct_answer"] = answer
        cleaned["difficulty"] = difficulty
        return cleaned


class QuestionMutationService:
    @staticmethod
    def _audit(*, action, contribution, actor, question_id=None, metadata=None, request=None):
        AuditService.log_event(
            action=action,
            portal="FACULTY",
            entity_type="Question" if question_id else "FacultyContribution",
            entity_id=question_id or contribution.id,
            actor=actor,
            tenant=contribution.cycle_course.cycle.tenant_id,
            campus=contribution.source_campus_id,
            metadata={
                "cycle_id": contribution.cycle_course.cycle_id,
                "cycle_course_id": contribution.cycle_course_id,
                "contribution_id": contribution.id,
                **(metadata or {}),
            },
            request=request,
        )

    @classmethod
    def _lock_mutable(
        cls,
        *,
        contribution_id,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
    ):
        _cycle, _course, configuration, contribution = Stage5LockService.lock_contribution(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
        )
        ContributionAuthorizationService.require_mutable_locked(
            contribution=contribution,
            configuration=configuration,
            request_tenant_id=tenant_id,
            request_campus_id=campus_id,
        )
        ContributionAuthorizationService.require_revision(
            contribution=contribution,
            expected_revision=expected_contribution_revision,
        )
        ContributionAuthorizationService.require_no_active_import(
            contribution=contribution,
        )
        questions = list(
            Question.objects.select_for_update()
            .filter(contribution=contribution)
            .order_by("pk")
        )
        return configuration, contribution, questions

    @staticmethod
    def _increment_contribution(contribution):
        contribution.revision += 1
        contribution.save(update_fields=["revision", "updated_at"])

    @classmethod
    @transaction.atomic
    def create(
        cls,
        *,
        contribution_id,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
        payload,
        request=None,
    ):
        _configuration, contribution, questions = cls._lock_mutable(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            expected_contribution_revision=expected_contribution_revision,
        )
        ContributionAuthorizationService.require_add_capacity(
            contribution=contribution,
            question_count=len(questions),
        )
        cleaned = QuestionPayloadService.validate(payload)
        duplicate_warning = Question.objects.filter(
            contribution__faculty_user=user
        ).values_list("question_text", flat=True)
        duplicate_warning = QuestionPayloadService.question_fingerprint(
            cleaned["question_text"]
        ) in {
            QuestionPayloadService.question_fingerprint(value)
            for value in duplicate_warning
        }
        before_revision = contribution.revision
        question = Question.objects.create(
            contribution=contribution,
            position=len(questions) + 1,
            revision=1,
            entry_method=Question.EntryMethod.MANUAL,
            **cleaned,
        )
        cls._increment_contribution(contribution)
        cls._audit(
            action="DE_EXAM_QUESTION_CREATED",
            contribution=contribution,
            actor=user,
            question_id=question.id,
            metadata={
                "position": question.position,
                "entry_method": question.entry_method,
                "revision_before": before_revision,
                "revision_after": contribution.revision,
                "difficulty": question.difficulty,
            },
            request=request,
        )
        question.duplicate_warning = duplicate_warning
        return question

    @classmethod
    @transaction.atomic
    def update(
        cls,
        *,
        contribution_id,
        question_id,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
        expected_question_revision,
        payload,
        request=None,
    ):
        _configuration, contribution, questions = cls._lock_mutable(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            expected_contribution_revision=expected_contribution_revision,
        )
        question = next((item for item in questions if item.id == question_id), None)
        if question is None:
            raise Http404
        if question.revision != expected_question_revision:
            raise ContributionConflict("This question changed after the page was loaded.")
        cleaned = QuestionPayloadService.validate(payload)
        duplicate_warning = QuestionPayloadService.question_fingerprint(
            cleaned["question_text"]
        ) in {
            QuestionPayloadService.question_fingerprint(value)
            for value in Question.objects.filter(
                contribution__faculty_user=user
            ).exclude(pk=question.pk).values_list("question_text", flat=True)
        }
        changed_fields = [
            field for field, value in cleaned.items() if getattr(question, field) != value
        ]
        if not changed_fields:
            question.duplicate_warning = duplicate_warning
            return question, False
        before_revision = contribution.revision
        for field, value in cleaned.items():
            setattr(question, field, value)
        question.revision += 1
        question.save(update_fields=[*changed_fields, "revision", "updated_at"])
        cls._increment_contribution(contribution)
        cls._audit(
            action="DE_EXAM_QUESTION_UPDATED",
            contribution=contribution,
            actor=user,
            question_id=question.id,
            metadata={
                "changed_fields": changed_fields,
                "question_revision": question.revision,
                "revision_before": before_revision,
                "revision_after": contribution.revision,
                "difficulty": question.difficulty,
            },
            request=request,
        )
        question.duplicate_warning = duplicate_warning
        return question, True

    @staticmethod
    def _rewrite_positions(questions):
        if not questions:
            return
        high = max(item.position for item in questions) + len(questions) + 1
        for offset, question in enumerate(questions):
            question.position = high + offset
            question.save(update_fields=["position", "updated_at"])
        for position, question in enumerate(questions, start=1):
            question.position = position
            question.save(update_fields=["position", "updated_at"])

    @classmethod
    @transaction.atomic
    def delete(
        cls,
        *,
        contribution_id,
        question_id,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
        expected_question_revision,
        request=None,
    ):
        _configuration, contribution, questions = cls._lock_mutable(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            expected_contribution_revision=expected_contribution_revision,
        )
        question = next((item for item in questions if item.id == question_id), None)
        if question is None:
            raise Http404
        if question.revision != expected_question_revision:
            raise ContributionConflict("This question changed after the page was loaded.")
        deleted_id = question.id
        deleted_position = question.position
        before_revision = contribution.revision
        remaining = sorted(
            (item for item in questions if item.id != deleted_id),
            key=lambda item: item.position,
        )
        question.delete()
        cls._rewrite_positions(remaining)
        cls._increment_contribution(contribution)
        cls._audit(
            action="DE_EXAM_QUESTION_DELETED",
            contribution=contribution,
            actor=user,
            question_id=deleted_id,
            metadata={
                "deleted_position": deleted_position,
                "resulting_count": len(remaining),
                "revision_before": before_revision,
                "revision_after": contribution.revision,
            },
            request=request,
        )

    @classmethod
    @transaction.atomic
    def reorder(
        cls,
        *,
        contribution_id,
        ordered_question_ids,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
        request=None,
    ):
        _configuration, contribution, questions = cls._lock_mutable(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            expected_contribution_revision=expected_contribution_revision,
        )
        expected_ids = {item.id for item in questions}
        submitted_ids = list(ordered_question_ids)
        if len(submitted_ids) != len(set(submitted_ids)) or set(submitted_ids) != expected_ids:
            raise ValidationError("The reorder request must include every owned question exactly once.")
        current_ids = [item.id for item in sorted(questions, key=lambda item: item.position)]
        if submitted_ids == current_ids:
            return False
        by_id = {item.id: item for item in questions}
        ordered = [by_id[item_id] for item_id in submitted_ids]
        before_revision = contribution.revision
        cls._rewrite_positions(ordered)
        cls._increment_contribution(contribution)
        cls._audit(
            action="DE_EXAM_QUESTIONS_REORDERED",
            contribution=contribution,
            actor=user,
            metadata={
                "question_ids": submitted_ids,
                "revision_before": before_revision,
                "revision_after": contribution.revision,
            },
            request=request,
        )
        return True

    @classmethod
    @transaction.atomic
    def submit(
        cls,
        *,
        contribution_id,
        user,
        tenant_id,
        campus_id,
        expected_contribution_revision,
        request=None,
    ):
        _cycle, _course, configuration, contribution = Stage5LockService.lock_contribution(
            contribution_id=contribution_id,
            user=user,
            tenant_id=tenant_id,
        )
        ContributionAuthorizationService.require_common_read_access(
            user=user,
            tenant=contribution.cycle_course.cycle.tenant,
            request_tenant_id=tenant_id,
            request_campus_id=campus_id,
        )
        if contribution.status == FacultyContribution.Status.SUBMITTED:
            return contribution, False
        ContributionAuthorizationService.require_mutable_locked(
            contribution=contribution,
            configuration=configuration,
            request_tenant_id=tenant_id,
            request_campus_id=campus_id,
        )
        ContributionAuthorizationService.require_revision(
            contribution=contribution,
            expected_revision=expected_contribution_revision,
        )
        ContributionAuthorizationService.require_no_active_import(
            contribution=contribution,
        )
        questions = list(
            Question.objects.select_for_update()
            .filter(contribution=contribution)
            .order_by("pk")
        )
        if len(questions) != contribution.quota_snapshot:
            raise ValidationError(
                f"Exactly {contribution.quota_snapshot} valid questions are required before submission."
            )
        for question in questions:
            QuestionPayloadService.validate(
                {
                    field: getattr(question, field)
                    for field in (*QuestionPayloadService.TEXT_FIELDS, "correct_answer", "difficulty")
                }
            )
        before_revision = contribution.revision
        contribution.status = FacultyContribution.Status.SUBMITTED
        contribution.submitted_at = timezone.now()
        contribution.revision += 1
        contribution.save(update_fields=["status", "submitted_at", "revision", "updated_at"])
        difficulty_counts = Counter(item.difficulty for item in questions)
        cls._audit(
            action="DE_EXAM_CONTRIBUTION_SUBMITTED",
            contribution=contribution,
            actor=user,
            metadata={
                "quota": contribution.quota_snapshot,
                "question_count": len(questions),
                "difficulty_counts": dict(difficulty_counts),
                "revision_before": before_revision,
                "revision_after": contribution.revision,
            },
            request=request,
        )
        return contribution, True
