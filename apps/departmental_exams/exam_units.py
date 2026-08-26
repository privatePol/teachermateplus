from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.core.services.audit import AuditService

from .models import (
    AnswerKeyRelease,
    CourseExamConfiguration,
    CycleCourse,
    ExamCourseEquivalencyGroup,
    ExamCourseEquivalencyMembership,
    ExamGenerationRevision,
    ExaminationCycle,
    QuestionnairePrintRelease,
    _equivalency_lifecycle_service_scope,
)


def _sha256_json(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExaminationUnit:
    primary: CycleCourse
    members: tuple[CycleCourse, ...]
    group: ExamCourseEquivalencyGroup | None = None
    memberships: tuple[ExamCourseEquivalencyMembership, ...] = ()

    @property
    def member_ids(self):
        return tuple(member.id for member in self.members)

    @property
    def grouped(self):
        return self.group is not None

    def fingerprint_metadata(self):
        if self.group is None:
            return {
                "group_id": None,
                "primary_cycle_course_id": self.primary.id,
                "member_cycle_course_ids": self.member_ids,
            }
        return {
            "group_id": self.group.id,
            "group_updated_at": self.group.updated_at,
            "group_name": self.group.name,
            "primary_cycle_course_id": self.primary.id,
            "members": [
                {
                    "membership_id": membership.id,
                    "membership_updated_at": membership.updated_at,
                    "cycle_course_id": membership.cycle_course_id,
                }
                for membership in self.memberships
            ],
        }


def configuration_compatibility_key(configuration):
    if configuration is None:
        return None
    return {
        "final_item_count": configuration.final_item_count,
        "questions_required_per_faculty": configuration.questions_required_per_faculty,
        "active_contribution_deadline": configuration.active_contribution_deadline,
        "workflow_status": configuration.workflow_status,
        "difficulty_percentages": (
            configuration.easy_percent,
            configuration.moderate_percent,
            configuration.difficult_percent,
        ),
        "coverage": (configuration.coverage or "").strip(),
        "general_instructions": (configuration.general_instructions or "").strip(),
        "additional_instructions": (configuration.additional_instructions or "").strip(),
        "contributor_instructions_snapshot": (
            configuration.contributor_instructions_snapshot or ""
        ).strip(),
    }


def _compatibility_errors(*, members, configurations):
    errors = []
    if len(members) < 2:
        errors.append("An equivalency group requires at least two active members.")
    cycles = {member.cycle_id for member in members}
    if len(cycles) != 1:
        errors.append("All equivalency members must belong to the same examination cycle.")
    if any(
        member.inclusion_status != CycleCourse.InclusionStatus.INCLUDED
        for member in members
    ):
        errors.append("All equivalency members must be Included.")
    missing = [member.id for member in members if configurations.get(member.id) is None]
    if missing:
        errors.append("Every equivalency member requires a course examination configuration.")
    keys = {
        _sha256_json(configuration_compatibility_key(configurations.get(member.id)))
        for member in members
        if configurations.get(member.id) is not None
    }
    if len(keys) > 1:
        errors.append(
            "Equivalency members must have compatible effective item count, quota, "
            "deadline, workflow, difficulty, coverage, and instruction settings."
        )
    return errors


def validate_examination_unit(unit):
    if not unit.grouped:
        return unit
    if unit.group.cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
        raise ValidationError(
            "Course equivalency is supported only for Automatic Generation cycles."
        )
    if unit.primary.id not in unit.member_ids:
        raise ValidationError("The primary course must be an active equivalency member.")
    if any(member.cycle_id != unit.group.cycle_id for member in unit.members):
        raise ValidationError("Equivalency member cycle scope is inconsistent.")
    configurations = {
        row.cycle_course_id: row
        for row in CourseExamConfiguration.objects.filter(cycle_course_id__in=unit.member_ids)
    }
    errors = _compatibility_errors(members=unit.members, configurations=configurations)
    if errors:
        raise ValidationError({"members": errors})
    return unit


def resolve_examination_unit(cycle_course, *, for_update=False, validate=True):
    membership_queryset = ExamCourseEquivalencyMembership.objects.filter(
        cycle_course_id=cycle_course.id,
        active_marker=1,
        group__is_active=True,
    ).select_related("group__cycle", "group__primary_cycle_course")
    memberships = list(membership_queryset.order_by("group_id", "id")[:2])
    if not memberships:
        return ExaminationUnit(primary=cycle_course, members=(cycle_course,))
    if len(memberships) != 1:
        raise ValidationError("Course equivalency membership is ambiguous.")
    group = memberships[0].group
    if for_update:
        group = (
            ExamCourseEquivalencyGroup.objects.select_for_update()
            .select_related("cycle", "primary_cycle_course")
            .get(pk=group.id)
        )
        if not group.is_active:
            raise ValidationError("Course equivalency group is no longer active.")
    active_memberships = ExamCourseEquivalencyMembership.objects.filter(
        group=group,
        active_marker=1,
    ).select_related("cycle_course__course", "cycle_course__cycle")
    if for_update:
        active_memberships = active_memberships.select_for_update()
    active_memberships = tuple(
        active_memberships.order_by(
            "cycle_course__course__code", "cycle_course__course_id", "cycle_course_id"
        )
    )
    members = tuple(row.cycle_course for row in active_memberships)
    primary = next(
        (member for member in members if member.id == group.primary_cycle_course_id),
        group.primary_cycle_course,
    )
    unit = ExaminationUnit(
        primary=primary,
        members=members,
        group=group,
        memberships=active_memberships,
    )
    return validate_examination_unit(unit) if validate else unit


class ExamCourseEquivalencyService:
    @staticmethod
    def _require_authority(*, cycle, members, actor):
        from .services import DepartmentalExamAuthorizationService

        DepartmentalExamAuthorizationService.require_automatic_courses_permission(
            user=actor,
            cycle=cycle,
            courses=members,
            permissions=(
                DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
            ),
            require_included=False,
        )

    @staticmethod
    def _require_mutable(*, cycle, members):
        if cycle.status != ExaminationCycle.Status.OPEN:
            raise ValidationError("Equivalency membership may change only in an open cycle.")
        if cycle.processing_mode != ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION:
            raise ValidationError("Equivalency is available only for Automatic Generation.")
        member_ids = tuple(member.id for member in members)
        if QuestionnairePrintRelease.objects.filter(cycle_course_id__in=member_ids).exists():
            raise ValidationError(
                "Equivalency membership cannot change after questionnaire release."
            )
        if AnswerKeyRelease.objects.filter(cycle_course_id__in=member_ids).exists():
            raise ValidationError("Equivalency membership cannot change after Answer Key release.")
        if ExamGenerationRevision.objects.filter(cycle_course_id__in=member_ids).exists():
            raise ValidationError(
                "Equivalency membership cannot change after generation processing has begun."
            )
        if CourseExamConfiguration.objects.filter(
            cycle_course_id__in=member_ids
        ).filter(
            models.Q(automatic_processed_at__isnull=False)
            | ~models.Q(automatic_processing_status="")
        ).exists():
            raise ValidationError(
                "Equivalency membership cannot change after automatic processing has begun."
            )

    @staticmethod
    def _locked_members(*, cycle, member_ids):
        normalized_ids = tuple(sorted({int(value) for value in member_ids}))
        if len(normalized_ids) < 2:
            raise ValidationError("An equivalency group requires at least two members.")
        members = tuple(
            CycleCourse.objects.select_for_update()
            .select_related("cycle", "course")
            .filter(id__in=normalized_ids, cycle=cycle)
            .order_by("course__code", "course_id", "id")
        )
        if len(members) != len(normalized_ids):
            raise ValidationError("Every equivalency member must belong to the selected cycle.")
        return members

    @classmethod
    @transaction.atomic
    def create_group(cls, *, cycle_id, name, primary_cycle_course_id, member_ids, actor):
        if actor is None:
            raise ValidationError("An acting user is required.")
        cycle = ExaminationCycle.objects.select_for_update().get(pk=cycle_id)
        members = cls._locked_members(cycle=cycle, member_ids=member_ids)
        if primary_cycle_course_id not in {member.id for member in members}:
            raise ValidationError("The primary course must be an equivalency member.")
        cls._require_authority(cycle=cycle, members=members, actor=actor)
        cls._require_mutable(cycle=cycle, members=members)
        if ExamCourseEquivalencyMembership.objects.select_for_update().filter(
            cycle_course__in=members,
            active_marker=1,
            group__is_active=True,
        ).exists():
            raise ValidationError("A member already belongs to an active equivalency group.")
        configurations = {
            row.cycle_course_id: row
            for row in CourseExamConfiguration.objects.select_for_update().filter(
                cycle_course__in=members
            )
        }
        errors = _compatibility_errors(members=members, configurations=configurations)
        if errors:
            raise ValidationError({"members": errors})
        group = ExamCourseEquivalencyGroup(
            cycle=cycle,
            name=name,
            primary_cycle_course_id=primary_cycle_course_id,
            created_by=actor,
            updated_by=actor,
        )
        group.full_clean()
        with _equivalency_lifecycle_service_scope():
            group.save()
            for member in members:
                membership = ExamCourseEquivalencyMembership(
                    group=group,
                    cycle_course=member,
                    added_by=actor,
                )
                membership.full_clean()
                membership.save()
        unit = resolve_examination_unit(group.primary_cycle_course, for_update=True)
        AuditService.log_event(
            action="DE_EXAM_COURSE_EQUIVALENCY_CREATED",
            portal="SYSTEM",
            entity_type="ExamCourseEquivalencyGroup",
            entity_id=group.id,
            actor=actor,
            tenant=cycle.tenant_id,
            metadata={
                "cycle_id": cycle.id,
                "primary_cycle_course_id": unit.primary.id,
                "member_cycle_course_ids": list(unit.member_ids),
            },
        )
        return group

    @classmethod
    @transaction.atomic
    def replace_members(
        cls, *, group_id, primary_cycle_course_id, member_ids, actor
    ):
        if actor is None:
            raise ValidationError("An acting user is required.")
        group_identity = ExamCourseEquivalencyGroup.objects.filter(
            pk=group_id,
            is_active=True,
        ).values("cycle_id").first()
        if group_identity is None:
            raise ValidationError("Active equivalency group does not exist.")
        cycle = ExaminationCycle.objects.select_for_update().get(
            pk=group_identity["cycle_id"]
        )
        group = (
            ExamCourseEquivalencyGroup.objects.select_for_update()
            .select_related("cycle")
            .get(pk=group_id, is_active=True)
        )
        members = cls._locked_members(cycle=cycle, member_ids=member_ids)
        if primary_cycle_course_id not in {member.id for member in members}:
            raise ValidationError("The primary course must be an equivalency member.")
        current_memberships = list(
            ExamCourseEquivalencyMembership.objects.select_for_update()
            .filter(group=group)
            .order_by("id")
        )
        current_member_ids = {
            row.cycle_course_id for row in current_memberships if row.active_marker == 1
        }
        affected_ids = current_member_ids | {member.id for member in members}
        affected_members = tuple(
            CycleCourse.objects.select_for_update()
            .select_related("cycle")
            .prefetch_related("offering_snapshots")
            .filter(id__in=affected_ids)
            .order_by("id")
        )
        cls._require_authority(cycle=cycle, members=affected_members, actor=actor)
        cls._require_mutable(cycle=cycle, members=affected_members)
        if ExamCourseEquivalencyMembership.objects.select_for_update().filter(
            cycle_course__in=members,
            active_marker=1,
            group__is_active=True,
        ).exclude(group=group).exists():
            raise ValidationError("A member already belongs to another active equivalency group.")
        configurations = {
            row.cycle_course_id: row
            for row in CourseExamConfiguration.objects.select_for_update().filter(
                cycle_course__in=members
            )
        }
        errors = _compatibility_errors(members=members, configurations=configurations)
        if errors:
            raise ValidationError({"members": errors})
        now = timezone.now()
        by_course = {row.cycle_course_id: row for row in current_memberships}
        desired_ids = {member.id for member in members}
        with _equivalency_lifecycle_service_scope():
            for membership in current_memberships:
                if (
                    membership.active_marker == 1
                    and membership.cycle_course_id not in desired_ids
                ):
                    membership.active_marker = None
                    membership.removed_by = actor
                    membership.removed_at = now
                    membership.full_clean()
                    membership.save(
                        update_fields=[
                            "active_marker",
                            "removed_by",
                            "removed_at",
                            "updated_at",
                        ]
                    )
            for member in members:
                membership = by_course.get(member.id)
                if membership is None:
                    membership = ExamCourseEquivalencyMembership(
                        group=group,
                        cycle_course=member,
                        added_by=actor,
                    )
                elif membership.active_marker is None:
                    membership.active_marker = 1
                    membership.removed_by = None
                    membership.removed_at = None
                membership.full_clean()
                membership.save()
            group.primary_cycle_course_id = primary_cycle_course_id
            group.updated_by = actor
            group.full_clean()
            group.save(update_fields=["primary_cycle_course", "updated_by", "updated_at"])
        unit = resolve_examination_unit(group.primary_cycle_course, for_update=True)
        AuditService.log_event(
            action="DE_EXAM_COURSE_EQUIVALENCY_MEMBERS_REPLACED",
            portal="SYSTEM",
            entity_type="ExamCourseEquivalencyGroup",
            entity_id=group.id,
            actor=actor,
            tenant=cycle.tenant_id,
            metadata={
                "cycle_id": cycle.id,
                "previous_member_cycle_course_ids": sorted(current_member_ids),
                "primary_cycle_course_id": unit.primary.id,
                "member_cycle_course_ids": list(unit.member_ids),
            },
        )
        return unit.group

    @classmethod
    @transaction.atomic
    def retire_group(cls, *, group_id, actor, reason):
        if actor is None:
            raise ValidationError("An acting user is required.")
        normalized_reason = " ".join((reason or "").split())
        if not 10 <= len(normalized_reason) <= 500:
            raise ValidationError(
                "A retirement reason of 10 to 500 characters is required."
            )
        group_identity = ExamCourseEquivalencyGroup.objects.filter(
            pk=group_id,
            is_active=True,
        ).values("cycle_id").first()
        if group_identity is None:
            raise ValidationError("Active equivalency group does not exist.")
        cycle = ExaminationCycle.objects.select_for_update().get(
            pk=group_identity["cycle_id"]
        )
        group = (
            ExamCourseEquivalencyGroup.objects.select_for_update()
            .select_related("cycle", "primary_cycle_course")
            .get(pk=group_id, is_active=True)
        )
        memberships = list(
            ExamCourseEquivalencyMembership.objects.select_for_update()
            .select_related("cycle_course__cycle")
            .prefetch_related("cycle_course__offering_snapshots")
            .filter(group=group, active_marker=1)
            .order_by("cycle_course_id")
        )
        members = tuple(row.cycle_course for row in memberships)
        if len(members) < 2 or group.primary_cycle_course_id not in {
            member.id for member in members
        }:
            raise ValidationError("The active equivalency group membership is inconsistent.")
        cls._require_authority(cycle=cycle, members=members, actor=actor)
        cls._require_mutable(cycle=cycle, members=members)

        now = timezone.now()
        member_ids = tuple(member.id for member in members)
        with _equivalency_lifecycle_service_scope():
            for membership in memberships:
                membership.active_marker = None
                membership.removed_by = actor
                membership.removed_at = now
                membership.full_clean()
                membership.save(
                    update_fields=[
                        "active_marker",
                        "removed_by",
                        "removed_at",
                        "updated_at",
                    ]
                )
            group.is_active = False
            group.retired_by = actor
            group.retired_at = now
            group.retirement_reason = normalized_reason
            group.updated_by = actor
            group.full_clean()
            group.save(
                update_fields=[
                    "is_active",
                    "retired_by",
                    "retired_at",
                    "retirement_reason",
                    "updated_by",
                    "updated_at",
                ]
            )
        AuditService.log_event(
            action="DE_EXAM_COURSE_EQUIVALENCY_RETIRED",
            portal="SYSTEM",
            entity_type="ExamCourseEquivalencyGroup",
            entity_id=group.id,
            actor=actor,
            tenant=cycle.tenant_id,
            metadata={
                "cycle_id": cycle.id,
                "primary_cycle_course_id": group.primary_cycle_course_id,
                "member_cycle_course_ids": list(member_ids),
                "reason": normalized_reason,
            },
        )
        return group
