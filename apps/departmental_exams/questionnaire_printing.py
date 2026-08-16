from __future__ import annotations

from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F
from django.http import Http404
from django.utils import timezone

from apps.core.services.audit import AuditService
from apps.core.services.settings import SystemSettingService

from .contribution_authorization import ContributionAuthorizationService
from .models import (
    CycleCourse,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    GeneratedExamSet,
    QuestionnairePrintRelease,
)
from .services import DepartmentalExamAuthorizationService


MANILA_TIMEZONE = ZoneInfo("Asia/Manila")


def _sanitized_questionnaire_context(*, revision, generated_set):
    if generated_set.generation_revision_id != revision.id:
        raise PermissionDenied("The generated questionnaire set is unavailable.")
    item_rows = list(
        generated_set.items.order_by("position").values(
            "position",
            "question_text_snapshot",
            "choices_snapshot",
        )
    )
    if (
        len(item_rows) != generated_set.item_count
        or [row["position"] for row in item_rows]
        != list(range(1, generated_set.item_count + 1))
    ):
        raise PermissionDenied("The generated questionnaire set is incomplete.")
    cycle = revision.cycle_course.cycle
    tenant = cycle.tenant
    return {
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
        "course_code": revision.cycle_course.course.code,
        "course_title": revision.cycle_course.course.title,
        "set_code": generated_set.set_code,
        "revision_number": revision.revision_number,
        "printed_at": timezone.now().astimezone(MANILA_TIMEZONE),
        "items": tuple(
            {
                "position": row["position"],
                "question_text": row["question_text_snapshot"],
                "choices": tuple(row["choices_snapshot"] or ()),
            }
            for row in item_rows
        ),
    }


class QuestionnairePrintReleaseService:
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
            )
            .prefetch_related("offering_snapshots")
            .get(pk=cycle_course_id, cycle_id=cycle_id)
        )

    @staticmethod
    def _require_valid_revision(*, revision):
        set_rows = list(
            GeneratedExamSet.objects.filter(generation_revision=revision)
            .annotate(actual_item_count=Count("items"))
            .values("set_code", "item_count", "actual_item_count")
        )
        set_codes = {row["set_code"] for row in set_rows}
        if set_codes != {
            GeneratedExamSet.SetCode.A,
            GeneratedExamSet.SetCode.B,
        } or any(
            row["item_count"] < 1
            or row["actual_item_count"] != row["item_count"]
            for row in set_rows
        ):
            raise ValidationError(
                "Only a complete generated revision with exact Set A and Set B snapshots may be released."
            )

    @staticmethod
    def _validate_window(*, print_from, print_until):
        if (
            print_from is None
            or print_until is None
            or timezone.is_naive(print_from)
            or timezone.is_naive(print_until)
        ):
            raise ValidationError("Print From and Print Until must include timezone information.")
        if print_until <= print_from:
            raise ValidationError(
                {"print_until": "Print Until must be later than Print From."}
            )

    @staticmethod
    def _audit_release(*, action, release, actor, request=None, metadata=None):
        AuditService.log_event(
            action=action,
            portal="ADMIN",
            entity_type="QuestionnairePrintRelease",
            entity_id=release.id,
            actor=actor,
            tenant=release.cycle_course.cycle.tenant_id,
            metadata={
                "cycle_id": release.cycle_course.cycle_id,
                "cycle_course_id": release.cycle_course_id,
                "revision_id": release.generation_revision_id,
                "revision_number": release.generation_revision.revision_number,
                "print_from": release.print_from,
                "print_until": release.print_until,
                "released_at": release.released_at,
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
        print_from,
        print_until,
        request=None,
    ):
        cls._validate_window(print_from=print_from, print_until=print_until)
        course = cls._lock_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        DepartmentalExamAuthorizationService.require_generation_management(
            user=actor,
            cycle_course=course,
        )
        try:
            revision = (
                ExamGenerationRevision.objects.select_for_update()
                .select_related("cycle_course")
                .get(pk=revision_id, cycle_course_id=course.id)
            )
        except ExamGenerationRevision.DoesNotExist as exc:
            raise ValidationError(
                {"generation_revision": "Selected revision does not belong to this course examination."}
            ) from exc
        cls._require_valid_revision(revision=revision)

        now = timezone.now()
        previous = (
            QuestionnairePrintRelease.objects.select_for_update()
            .select_related("cycle_course__cycle", "generation_revision")
            .filter(
                cycle_course=course,
                status=QuestionnairePrintRelease.Status.ACTIVE,
                active_marker=1,
            )
            .first()
        )
        if previous:
            previous.status = QuestionnairePrintRelease.Status.REVOKED
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
            cls._audit_release(
                action="DE_QUESTIONNAIRE_PRINT_RELEASE_REVOKED",
                release=previous,
                actor=actor,
                request=request,
                metadata={"replacement_revision_id": revision.id},
            )

        release = QuestionnairePrintRelease(
            cycle_course=course,
            generation_revision=revision,
            print_from=print_from,
            print_until=print_until,
            released_by=actor,
            released_at=now,
        )
        release.full_clean()
        release.save()
        cls._audit_release(
            action="DE_QUESTIONNAIRE_PRINT_RELEASED",
            release=release,
            actor=actor,
            request=request,
            metadata={
                "replaced_release_id": previous.id if previous else None,
            },
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
        print_from,
        print_until,
        request=None,
    ):
        cls._validate_window(print_from=print_from, print_until=print_until)
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
                {"selections": "Select at least one generated course revision."}
            )

        releases = []
        for cycle_course_id, revision_id in sorted(normalized):
            releases.append(
                cls.release(
                    cycle_course_id=cycle_course_id,
                    revision_id=revision_id,
                    tenant_id=tenant_id,
                    actor=actor,
                    print_from=print_from,
                    print_until=print_until,
                    request=request,
                )
            )
        return tuple(releases)

    @classmethod
    @transaction.atomic
    def revoke(cls, *, release_id, tenant_id, actor, request=None):
        course_id = (
            QuestionnairePrintRelease.objects.filter(
                pk=release_id,
                cycle_course__cycle__tenant_id=tenant_id,
            )
            .values_list("cycle_course_id", flat=True)
            .first()
        )
        if course_id is None:
            raise Http404("Questionnaire print release does not exist in the active tenant.")
        course = cls._lock_course(cycle_course_id=course_id, tenant_id=tenant_id)
        DepartmentalExamAuthorizationService.require_generation_management(
            user=actor,
            cycle_course=course,
        )
        release = (
            QuestionnairePrintRelease.objects.select_for_update()
            .select_related("cycle_course__cycle", "generation_revision")
            .get(pk=release_id, cycle_course=course)
        )
        if release.status != QuestionnairePrintRelease.Status.ACTIVE:
            raise ValidationError("Only the active questionnaire print release may be revoked.")
        release.status = QuestionnairePrintRelease.Status.REVOKED
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
        cls._audit_release(
            action="DE_QUESTIONNAIRE_PRINT_RELEASE_REVOKED",
            release=release,
            actor=actor,
            request=request,
        )
        return release


class FacultyQuestionnairePrintService:
    @staticmethod
    def available_options(*, contributions, now=None):
        contributions = tuple(contributions)
        now = now or timezone.now()
        course_ids = {row.cycle_course_id for row in contributions}
        releases = {
            row.cycle_course_id: row
            for row in QuestionnairePrintRelease.objects.filter(
                cycle_course_id__in=course_ids,
                status=QuestionnairePrintRelease.Status.ACTIVE,
                active_marker=1,
                print_from__lte=now,
                print_until__gte=now,
                generation_revision__cycle_course_id=F("cycle_course_id"),
            ).select_related("generation_revision")
        }
        options = {}
        for contribution in contributions:
            release = releases.get(contribution.cycle_course_id)
            if not release or not ContributionAuthorizationService.has_retained_current_print_eligibility(
                contribution=contribution
            ):
                continue
            set_rows = GeneratedExamSet.objects.filter(
                generation_revision=release.generation_revision,
                set_code__in=(GeneratedExamSet.SetCode.A, GeneratedExamSet.SetCode.B),
            ).annotate(actual_item_count=Count("items"))
            if set(set_rows.values_list("set_code", flat=True)) != {"A", "B"} or any(
                row.actual_item_count != row.item_count for row in set_rows
            ):
                continue
            options[contribution.id] = {
                "release_id": release.id,
                "revision_number": release.generation_revision.revision_number,
            }
        return options

    @staticmethod
    def _printable_release(*, contribution, release_id, set_code, now=None):
        normalized_set = (set_code or "").upper()
        if normalized_set not in GeneratedExamSet.SetCode.values:
            raise Http404("Questionnaire set does not exist.")
        try:
            release = QuestionnairePrintRelease.objects.select_related(
                "cycle_course__cycle__tenant",
                "cycle_course__cycle__academic_year",
                "cycle_course__cycle__term",
                "cycle_course__course",
                "generation_revision",
            ).get(
                pk=release_id,
                cycle_course=contribution.cycle_course,
                cycle_course__cycle__tenant_id=contribution.cycle_course.cycle.tenant_id,
                generation_revision__cycle_course=contribution.cycle_course,
            )
        except QuestionnairePrintRelease.DoesNotExist as exc:
            raise PermissionDenied("Questionnaire print release is unavailable.") from exc
        now = now or timezone.now()
        if (
            release.status != QuestionnairePrintRelease.Status.ACTIVE
            or release.active_marker != 1
            or now < release.print_from
            or now > release.print_until
        ):
            raise PermissionDenied("Questionnaire printing is outside the authorized release window.")
        if not ContributionAuthorizationService.has_retained_current_print_eligibility(
            contribution=contribution
        ):
            raise PermissionDenied("No current qualifying teaching assignment remains.")
        try:
            generated_set = GeneratedExamSet.objects.get(
                generation_revision=release.generation_revision,
                set_code=normalized_set,
            )
        except GeneratedExamSet.DoesNotExist as exc:
            raise PermissionDenied("The released questionnaire set is incomplete.") from exc
        return release, generated_set, normalized_set

    @classmethod
    def build_safe_context(
        cls,
        *,
        contribution,
        release_id,
        set_code,
        now=None,
        actor,
        request=None,
    ):
        release, generated_set, normalized_set = cls._printable_release(
            contribution=contribution,
            release_id=release_id,
            set_code=set_code,
            now=now,
        )
        cycle = release.cycle_course.cycle
        tenant = cycle.tenant
        printed_at = timezone.now().astimezone(MANILA_TIMEZONE)
        AuditService.log_event(
            action="DE_QUESTIONNAIRE_PRINT_SET_ACCESSED",
            portal="FACULTY",
            entity_type="QuestionnairePrintRelease",
            entity_id=release.id,
            actor=actor,
            tenant=tenant.id,
            metadata={
                "cycle_id": cycle.id,
                "cycle_course_id": release.cycle_course_id,
                "revision_id": release.generation_revision_id,
                "revision_number": release.generation_revision.revision_number,
                "set_code": normalized_set,
                "print_from": release.print_from,
                "print_until": release.print_until,
                "printed_at": printed_at,
            },
            request=request,
        )
        context = _sanitized_questionnaire_context(
            revision=release.generation_revision,
            generated_set=generated_set,
        )
        context["printed_at"] = printed_at
        return context


class AdminQuestionnairePrintService:
    @staticmethod
    def build_safe_context(
        *, revision, set_code, actor, request=None
    ):
        from .generation_reporting import GenerationReportingAuthorizationService

        GenerationReportingAuthorizationService.require_print(
            user=actor,
            revision=revision,
        )
        normalized_set = (set_code or "").strip().upper()
        if normalized_set not in GeneratedExamSet.SetCode.values:
            raise Http404("Questionnaire set does not exist.")
        try:
            generated_set = GeneratedExamSet.objects.get(
                generation_revision=revision,
                set_code=normalized_set,
            )
        except GeneratedExamSet.DoesNotExist as exc:
            raise Http404("Questionnaire set does not exist.") from exc
        context = _sanitized_questionnaire_context(
            revision=revision,
            generated_set=generated_set,
        )
        AuditService.log_event(
            action="DE_ADMIN_QUESTIONNAIRE_PRINT_SET_ACCESSED",
            portal="ADMIN",
            entity_type="ExamGenerationRevision",
            entity_id=revision.id,
            actor=actor,
            tenant=revision.cycle_course.cycle.tenant_id,
            metadata={
                "cycle_id": revision.cycle_course.cycle_id,
                "cycle_course_id": revision.cycle_course_id,
                "revision_id": revision.id,
                "revision_number": revision.revision_number,
                "set_code": normalized_set,
                "item_count": len(context["items"]),
                "printed_at": context["printed_at"],
            },
            request=request,
        )
        return context
