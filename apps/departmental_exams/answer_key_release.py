from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from apps.core.services.settings import SystemSettingService

from .contribution_authorization import ContributionAuthorizationService
from .exam_units import resolve_examination_unit
from .models import (
    AnswerKeyRelease,
    CycleCourse,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    GeneratedExamItem,
    GeneratedExamSet,
)
from .services import DepartmentalExamAuthorizationService


MANILA_TIMEZONE = ZoneInfo("Asia/Manila")
ANSWER_KEY_RELEASE_ATTESTATION_VERSION = "all-sessions-concluded-v1"


@dataclass(frozen=True)
class AnswerKeyAccessAuditContext:
    release_id: int
    tenant_id: int
    cycle_id: int
    cycle_course_id: int
    revision_id: int
    revision_number: int
    set_code: str
    faculty_user_id: int
    available_from: datetime
    available_until: datetime
    accessed_at: datetime


def _revision_is_current_final(revision):
    if revision.current_marker != 1:
        return False
    if (
        revision.cycle_course.cycle.processing_mode
        == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
    ):
        return revision.status == ExamGenerationRevision.Status.GENERATED
    return revision.status == ExamGenerationRevision.Status.LOCKED


def _complete_sets(revision):
    rows = list(
        GeneratedExamSet.objects.filter(generation_revision=revision).values(
            "id",
            "set_code",
            "item_count",
        )
    )
    if len(rows) != 2 or {row["set_code"] for row in rows} != {
        GeneratedExamSet.SetCode.A,
        GeneratedExamSet.SetCode.B,
    }:
        return False
    if not 1 <= revision.final_item_count_snapshot <= 75:
        return False
    positions_by_set = {row["id"]: [] for row in rows}
    for generated_set_id, position in GeneratedExamItem.objects.filter(
        generated_set_id__in=positions_by_set
    ).order_by("generated_set_id", "position").values_list(
        "generated_set_id",
        "position",
    ):
        positions_by_set[generated_set_id].append(position)
    return all(
        row["item_count"] == revision.final_item_count_snapshot
        and positions_by_set[row["id"]] == list(range(1, row["item_count"] + 1))
        for row in rows
    )


class AnswerKeyReleaseService:
    @staticmethod
    def revision_is_eligible(revision):
        return _revision_is_current_final(revision) and _complete_sets(revision)

    @staticmethod
    def _validate_window(*, available_from, available_until):
        if (
            available_from is None
            or available_until is None
            or timezone.is_naive(available_from)
            or timezone.is_naive(available_until)
        ):
            raise ValidationError(
                "Available From and Available Until must include timezone information."
            )
        if available_until <= available_from:
            raise ValidationError(
                {"available_until": "Available Until must be later than Available From."}
            )

    @staticmethod
    def _lock_course(*, cycle_course_id, tenant_id):
        cycle_id = (
            CycleCourse.objects.filter(
                pk=cycle_course_id,
                cycle__tenant_id=tenant_id,
            )
            .values_list("cycle_id", flat=True)
            .first()
        )
        if cycle_id is None:
            raise Http404("Course examination does not exist in the active tenant.")
        ExaminationCycle.objects.select_for_update().get(
            pk=cycle_id,
            tenant_id=tenant_id,
        )
        CycleCourse.objects.select_for_update().get(
            pk=cycle_course_id,
            cycle_id=cycle_id,
        )
        return (
            CycleCourse.objects.select_related(
                "cycle",
                "cycle__tenant",
                "course",
                "responsible_department",
                "reviewer",
            )
            .prefetch_related("offering_snapshots")
            .get(pk=cycle_course_id, cycle_id=cycle_id)
        )

    @staticmethod
    def _audit(*, action, release, actor, request=None, metadata=None):
        AuditService.log_event(
            action=action,
            portal="ADMIN",
            entity_type="AnswerKeyRelease",
            entity_id=release.id,
            actor=actor,
            tenant=release.cycle_course.cycle.tenant_id,
            metadata={
                "release_id": release.id,
                "cycle_id": release.cycle_course.cycle_id,
                "cycle_course_id": release.cycle_course_id,
                "revision_id": release.generation_revision_id,
                "revision_number": release.generation_revision.revision_number,
                "available_from": release.available_from,
                "available_until": release.available_until,
                "released_at": release.released_at,
                "attestation_version": release.attestation_version,
                **(metadata or {}),
            },
            request=request,
        )

    @classmethod
    @transaction.atomic
    def release(
        cls,
        *,
        cycle_course_id,
        revision_id,
        tenant_id,
        actor,
        available_from,
        available_until,
        attestation_confirmed,
        request=None,
        require_primary=False,
    ):
        cls._validate_window(
            available_from=available_from,
            available_until=available_until,
        )
        if attestation_confirmed is not True:
            raise ValidationError(
                "Confirm that all examination sessions have concluded."
            )
        course = cls._lock_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        DepartmentalExamAuthorizationService.require_answer_key_release(
            user=actor,
            cycle_course=course,
        )
        if require_primary and resolve_examination_unit(course).primary.id != course.id:
            raise ValidationError(
                "Bulk Answer Key release accepts only the primary-owned revision for an examination unit."
            )
        try:
            revision = (
                ExamGenerationRevision.objects.select_for_update()
                .select_related("cycle_course__cycle")
                .get(pk=revision_id, cycle_course=course)
            )
        except ExamGenerationRevision.DoesNotExist as exc:
            raise ValidationError(
                {"generation_revision": "Selected revision does not belong to this course examination."}
            ) from exc
        if not _revision_is_current_final(revision):
            raise ValidationError(
                {"generation_revision": "Only the current final revision may be released."}
            )
        if not _complete_sets(revision):
            raise ValidationError(
                "Only a complete revision with exact Set A and Set B snapshots may be released."
            )

        now = timezone.now()
        previous = (
            AnswerKeyRelease.objects.select_for_update()
            .select_related("cycle_course__cycle", "generation_revision")
            .filter(
                cycle_course=course,
                status=AnswerKeyRelease.Status.ACTIVE,
                active_marker=1,
            )
            .first()
        )
        if (
            previous
            and previous.generation_revision_id == revision.id
            and previous.available_from == available_from
            and previous.available_until == available_until
            and previous.attestation_version
            == ANSWER_KEY_RELEASE_ATTESTATION_VERSION
        ):
            return previous
        if previous:
            previous.status = AnswerKeyRelease.Status.REVOKED
            previous.active_marker = None
            previous.revoked_by = actor
            previous.revoked_at = now
            previous.full_clean()
            previous.save(
                update_fields=[
                    "status",
                    "active_marker",
                    "revoked_by",
                    "revoked_at",
                    "updated_at",
                ]
            )
            cls._audit(
                action="DE_ANSWER_KEY_RELEASE_REVOKED",
                release=previous,
                actor=actor,
                request=request,
                metadata={
                    "revocation_reason": "REPLACED",
                    "replacement_revision_id": revision.id,
                },
            )

        release = AnswerKeyRelease(
            cycle_course=course,
            generation_revision=revision,
            available_from=available_from,
            available_until=available_until,
            released_by=actor,
            released_at=now,
            attestation_version=ANSWER_KEY_RELEASE_ATTESTATION_VERSION,
        )
        release.full_clean()
        release.save()
        cls._audit(
            action="DE_ANSWER_KEY_RELEASED",
            release=release,
            actor=actor,
            request=request,
            metadata={"replaced_release_id": previous.id if previous else None},
        )
        return release

    @classmethod
    @transaction.atomic
    def bulk_release(
        cls,
        *,
        selections,
        tenant_id,
        actor,
        available_from,
        available_until,
        attestation_confirmed,
        request=None,
    ):
        cls._validate_window(
            available_from=available_from,
            available_until=available_until,
        )
        if attestation_confirmed is not True:
            raise ValidationError(
                "Confirm that all examination sessions for all selected courses have concluded."
            )
        normalized = []
        course_ids = set()
        for selection in selections:
            try:
                cycle_course_id, revision_id = (int(value) for value in selection)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    {"selections": "One or more selected revisions are invalid."}
                ) from exc
            if cycle_course_id < 1 or revision_id < 1:
                raise ValidationError(
                    {"selections": "One or more selected revisions are invalid."}
                )
            if cycle_course_id in course_ids:
                raise ValidationError(
                    {"selections": "Select only one revision for each course examination."}
                )
            course_ids.add(cycle_course_id)
            normalized.append((cycle_course_id, revision_id))
        if not normalized:
            raise ValidationError(
                {"selections": "Select at least one current final course revision."}
            )

        cycle_by_course = dict(
            CycleCourse.objects.filter(
                id__in=course_ids,
                cycle__tenant_id=tenant_id,
            ).values_list("id", "cycle_id")
        )
        ordered = sorted(
            normalized,
            key=lambda selection: (
                cycle_by_course.get(selection[0], 2**63),
                selection[0],
            ),
        )
        releases = []
        for cycle_course_id, revision_id in ordered:
            releases.append(
                cls.release(
                    cycle_course_id=cycle_course_id,
                    revision_id=revision_id,
                    tenant_id=tenant_id,
                    actor=actor,
                    available_from=available_from,
                    available_until=available_until,
                    attestation_confirmed=True,
                    request=request,
                    require_primary=True,
                )
            )
        return tuple(releases)

    @staticmethod
    def operational_status(*, release, expected_revision=None, now=None):
        if release is None:
            return "Not released"
        if (
            expected_revision is None
            or release.generation_revision_id != expected_revision.id
            or not _revision_is_current_final(release.generation_revision)
            or not _complete_sets(release.generation_revision)
        ):
            return "Superseded / No Longer Faculty Accessible"
        if release.status == AnswerKeyRelease.Status.REVOKED:
            return "Revoked"
        now = now or timezone.now()
        if now < release.available_from:
            return "Scheduled"
        if now > release.available_until:
            return "Ended"
        return "Currently Released / Available"

    @classmethod
    @transaction.atomic
    def revoke(cls, *, release_id, tenant_id, actor, request=None):
        course_id = (
            AnswerKeyRelease.objects.filter(
                pk=release_id,
                cycle_course__cycle__tenant_id=tenant_id,
            )
            .values_list("cycle_course_id", flat=True)
            .first()
        )
        if course_id is None:
            raise Http404("Answer Key release does not exist in the active tenant.")
        course = cls._lock_course(cycle_course_id=course_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_answer_key_release(
            user=actor,
            cycle_course=course,
        )
        release = (
            AnswerKeyRelease.objects.select_for_update()
            .select_related("cycle_course__cycle", "generation_revision")
            .get(pk=release_id, cycle_course=course)
        )
        if release.status != AnswerKeyRelease.Status.ACTIVE:
            raise ValidationError("Only the active Answer Key release may be revoked.")
        release.status = AnswerKeyRelease.Status.REVOKED
        release.active_marker = None
        release.revoked_by = actor
        release.revoked_at = timezone.now()
        release.full_clean()
        release.save(
            update_fields=[
                "status",
                "active_marker",
                "revoked_by",
                "revoked_at",
                "updated_at",
            ]
        )
        cls._audit(
            action="DE_ANSWER_KEY_RELEASE_REVOKED",
            release=release,
            actor=actor,
            request=request,
            metadata={"revocation_reason": "REVOKED"},
        )
        return release


class FacultyAnswerKeyReleaseService:
    @staticmethod
    def available_options(*, contributions, now=None):
        contributions = tuple(contributions)
        now = now or timezone.now()
        primary_by_contribution = {
            row.id: resolve_examination_unit(row.cycle_course).primary.id
            for row in contributions
        }
        course_ids = set(primary_by_contribution.values())
        releases = {
            row.cycle_course_id: row
            for row in AnswerKeyRelease.objects.filter(
                cycle_course_id__in=course_ids,
                status=AnswerKeyRelease.Status.ACTIVE,
                active_marker=1,
                available_from__lte=now,
                available_until__gte=now,
                generation_revision__cycle_course_id=F("cycle_course_id"),
                generation_revision__current_marker=1,
            ).select_related(
                "generation_revision",
                "generation_revision__cycle_course__cycle",
            )
            if _revision_is_current_final(row.generation_revision)
        }
        options = {}
        for contribution in contributions:
            release = releases.get(primary_by_contribution[contribution.id])
            if (
                not release
                or not ContributionAuthorizationService.has_retained_current_print_eligibility(
                    contribution=contribution
                )
                or not _complete_sets(release.generation_revision)
            ):
                continue
            options[contribution.id] = {
                "release_id": release.id,
                "revision_number": release.generation_revision.revision_number,
            }
        return options

    @staticmethod
    def _authorized_release(*, contribution, release_id, set_code, actor, now=None):
        if (
            not actor
            or not actor.is_authenticated
            or not actor.is_active
            or actor.id != contribution.faculty_user_id
        ):
            raise PermissionDenied("Answer Key access is unavailable.")
        normalized_set = (set_code or "").strip().upper()
        if normalized_set not in GeneratedExamSet.SetCode.values:
            raise Http404("Answer Key set does not exist.")
        primary_course = resolve_examination_unit(contribution.cycle_course).primary
        try:
            release = AnswerKeyRelease.objects.select_related(
                "cycle_course__cycle__tenant",
                "cycle_course__cycle__academic_year",
                "cycle_course__cycle__term",
                "cycle_course__course",
                "generation_revision__cycle_course__cycle",
            ).get(
                pk=release_id,
                cycle_course=primary_course,
                cycle_course__cycle__tenant_id=contribution.cycle_course.cycle.tenant_id,
                generation_revision__cycle_course=primary_course,
            )
        except AnswerKeyRelease.DoesNotExist as exc:
            raise PermissionDenied("Answer Key release is unavailable.") from exc
        now = now or timezone.now()
        if (
            release.status != AnswerKeyRelease.Status.ACTIVE
            or release.active_marker != 1
            or now < release.available_from
            or now > release.available_until
            or not _revision_is_current_final(release.generation_revision)
        ):
            raise PermissionDenied("Answer Key access is outside the authorized release.")
        if not ContributionAuthorizationService.has_retained_current_print_eligibility(
            contribution=contribution
        ):
            raise PermissionDenied("No current qualifying teaching assignment remains.")
        try:
            generated_set = GeneratedExamSet.objects.only(
                "id",
                "set_code",
                "item_count",
                "generation_revision_id",
            ).get(
                generation_revision=release.generation_revision,
                set_code=normalized_set,
            )
        except GeneratedExamSet.DoesNotExist as exc:
            raise PermissionDenied("The released Answer Key set is incomplete.") from exc
        items = list(
            generated_set.items.order_by("position").values(
                "position",
                "correct_answer_snapshot",
            )
        )
        if (
            len(items) != generated_set.item_count
            or [row["position"] for row in items]
            != list(range(1, generated_set.item_count + 1))
        ):
            raise PermissionDenied("The released Answer Key set is incomplete.")
        return release, normalized_set, items

    @classmethod
    def build_safe_context(
        cls,
        *,
        contribution,
        release_id,
        set_code,
        actor,
        printable,
        request=None,
        now=None,
        include_audit_context=False,
    ):
        release, normalized_set, item_rows = cls._authorized_release(
            contribution=contribution,
            release_id=release_id,
            set_code=set_code,
            actor=actor,
            now=now,
        )
        cycle = release.cycle_course.cycle
        tenant = cycle.tenant
        accessed_at = timezone.now().astimezone(MANILA_TIMEZONE)
        context = {
            "school_name": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_NAME",
                tenant_id=tenant.id,
                default=tenant.name,
            ),
            "school_address": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_ADDRESS",
                tenant_id=tenant.id,
                default="",
            ),
            "academic_year": cycle.academic_year.name,
            "term": cycle.term.name,
            "exam_period": cycle.get_exam_period_display(),
            "course_code": release.cycle_course.course.code,
            "course_title": release.cycle_course.course.title,
            "set_code": normalized_set,
            "revision_number": release.generation_revision.revision_number,
            "accessed_at": accessed_at,
            "items": tuple(
                {
                    "position": row["position"],
                    "correct_answer": row["correct_answer_snapshot"],
                }
                for row in item_rows
            ),
            "release_id": release.id,
            "contribution_id": contribution.id,
        }
        audit_context = AnswerKeyAccessAuditContext(
            release_id=release.id,
            tenant_id=tenant.id,
            cycle_id=cycle.id,
            cycle_course_id=release.cycle_course_id,
            revision_id=release.generation_revision_id,
            revision_number=release.generation_revision.revision_number,
            set_code=normalized_set,
            faculty_user_id=actor.id,
            available_from=release.available_from,
            available_until=release.available_until,
            accessed_at=accessed_at,
        )
        if include_audit_context:
            return context, audit_context
        return context

    @staticmethod
    def record_access(*, audit_context, actor, printable, request=None):
        if actor.id != audit_context.faculty_user_id:
            raise PermissionDenied("Answer Key access is unavailable.")
        return AuditService.log_event(
            action=(
                "DE_FACULTY_ANSWER_KEY_SET_PRINTED"
                if printable
                else "DE_FACULTY_ANSWER_KEY_SET_VIEWED"
            ),
            portal="FACULTY",
            entity_type="AnswerKeyRelease",
            entity_id=audit_context.release_id,
            actor=actor,
            tenant=audit_context.tenant_id,
            metadata={
                "release_id": audit_context.release_id,
                "cycle_id": audit_context.cycle_id,
                "cycle_course_id": audit_context.cycle_course_id,
                "revision_id": audit_context.revision_id,
                "revision_number": audit_context.revision_number,
                "set_code": audit_context.set_code,
                "faculty_user_id": audit_context.faculty_user_id,
                "available_from": audit_context.available_from,
                "available_until": audit_context.available_until,
                "accessed_at": audit_context.accessed_at,
            },
            request=request,
        )

    @classmethod
    def build_checking_master_context(
        cls,
        *,
        contribution,
        release_id,
        set_code,
        actor,
        request=None,
        now=None,
    ):
        release, normalized_set, item_rows = cls._authorized_release(
            contribution=contribution,
            release_id=release_id,
            set_code=set_code,
            actor=actor,
            now=now,
        )
        final_item_count = release.generation_revision.final_item_count_snapshot
        if (
            final_item_count < 1
            or final_item_count > 75
            or len(item_rows) != final_item_count
        ):
            raise PermissionDenied("The released Checking Master item count is invalid.")

        normalized_answers = {}
        for row in item_rows:
            answer = (row["correct_answer_snapshot"] or "").strip().upper()
            if answer not in {"A", "B", "C", "D"}:
                raise PermissionDenied(
                    "The released Checking Master answer snapshot is invalid."
                )
            normalized_answers[row["position"]] = answer

        display_rows = []
        for position in range(1, 76):
            answer = normalized_answers.get(position)
            is_unused = position > final_item_count
            if not is_unused and answer is None:
                raise PermissionDenied(
                    "The released Checking Master item sequence is incomplete."
                )
            display_rows.append(
                {
                    "position": position,
                    "is_unused": is_unused,
                    "bubbles": tuple(
                        {
                            "code": code,
                            "is_shaded": bool(not is_unused and code == answer),
                        }
                        for code in "ABCD"
                    ),
                }
            )

        cycle = release.cycle_course.cycle
        tenant = cycle.tenant
        accessed_at = timezone.now().astimezone(MANILA_TIMEZONE)
        context = {
            "school_name": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_NAME",
                tenant_id=tenant.id,
                default=tenant.name,
            ),
            "school_address": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_ADDRESS",
                tenant_id=tenant.id,
                default="",
            ),
            "academic_year": cycle.academic_year.name,
            "term": cycle.term.name,
            "exam_period": cycle.get_exam_period_display(),
            "course_code": release.cycle_course.course.code,
            "course_title": release.cycle_course.course.title,
            "set_code": normalized_set,
            "revision_number": release.generation_revision.revision_number,
            "final_item_count": final_item_count,
            "accessed_at": accessed_at,
            "answer_columns": (
                tuple(display_rows[0:25]),
                tuple(display_rows[25:50]),
                tuple(display_rows[50:75]),
            ),
            "release_id": release.id,
            "contribution_id": contribution.id,
        }
        AuditService.log_event(
            action="DE_FACULTY_CHECKING_MASTER_PRINTED",
            portal="FACULTY",
            entity_type="AnswerKeyRelease",
            entity_id=release.id,
            actor=actor,
            tenant=tenant.id,
            metadata={
                "release_id": release.id,
                "cycle_id": cycle.id,
                "cycle_course_id": release.cycle_course_id,
                "revision_id": release.generation_revision_id,
                "revision_number": release.generation_revision.revision_number,
                "set_code": normalized_set,
                "faculty_user_id": actor.id,
                "available_from": release.available_from,
                "available_until": release.available_until,
                "accessed_at": accessed_at,
            },
            request=request,
        )
        return context


class AnswerKeyViewerReportService:
    @staticmethod
    def report(*, release_id, tenant_id, actor):
        try:
            release = AnswerKeyRelease.objects.select_related(
                "cycle_course__cycle__tenant",
                "cycle_course__cycle__academic_year",
                "cycle_course__cycle__term",
                "cycle_course__course",
                "generation_revision",
            ).get(
                pk=release_id,
                cycle_course__cycle__tenant_id=tenant_id,
                generation_revision__cycle_course_id=F("cycle_course_id"),
            )
        except AnswerKeyRelease.DoesNotExist as exc:
            raise Http404(
                "Answer Key release does not exist in the active tenant."
            ) from exc
        DepartmentalExamAuthorizationService.require_answer_key_release(
            user=actor,
            cycle_course=release.cycle_course,
        )
        events = AuditLog.objects.filter(
            tenant_id=tenant_id,
            entity_type="AnswerKeyRelease",
            entity_id=str(release.id),
            action="DE_FACULTY_ANSWER_KEY_SET_VIEWED",
            actor_user__isnull=False,
        ).select_related("actor_user").order_by("created_at", "id")
        grouped = {}
        for event in events:
            row = grouped.setdefault(
                event.actor_user_id,
                {
                    "faculty": event.actor_user,
                    "sets_viewed": set(),
                    "first_viewed": event.created_at,
                    "last_viewed": event.created_at,
                },
            )
            set_code = (event.metadata_json or {}).get("set_code")
            if set_code in GeneratedExamSet.SetCode.values:
                row["sets_viewed"].add(set_code)
            row["last_viewed"] = event.created_at
        rows = tuple(
            {
                **row,
                "sets_viewed": tuple(sorted(row["sets_viewed"])),
            }
            for row in grouped.values()
        )
        return release, rows
