"""Bound, exact target review for multi-campus Faculty material releases."""

from datetime import datetime
from secrets import token_hex

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from apps.academics.models import CourseOffering
from apps.tenants.models import Campus

from .answer_key_release import AnswerKeyReleaseService
from .exam_units import resolve_examination_unit
from .models import (
    AnswerKeyRelease, CycleCourse, CycleCourseOffering, ExamCourseEquivalencyGroup,
    ExamCourseEquivalencyMembership,
    ExamGenerationRevision,
    ExaminationCycle, QuestionnaireLegacyCampusCoverage, QuestionnairePrintRelease,
)
from .questionnaire_printing import QuestionnairePrintReleaseService
from .services import DepartmentalExamAuthorizationService


SIGNING_SALT = "departmental-exam-release-target-review-v1"


def _request_campus_allowed(request, campus_id):
    scope = (getattr(request, "scope", {}) or {}).get("campus_ids")
    if scope is not None and campus_id not in scope:
        raise PermissionDenied("A release campus is outside the active request scope.")


def expand_targets(*, kind, bases, campus_id, tenant_id, actor, request):
    """Resolve selected primary/recipient identities to all exact active targets."""
    if kind not in {"questionnaire", "answer_key"} or campus_id < 0 or not bases:
        raise ValidationError("Release target selection is invalid.")
    targets = []
    for base in bases:
        primary_id, revision_id = base[:2]
        primary = CycleCourse.objects.select_related("cycle").filter(
            pk=primary_id, cycle__tenant_id=tenant_id,
            inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
        ).first()
        if primary is None:
            raise PermissionDenied("Release examination is outside the active tenant.")
        unit = resolve_examination_unit(primary)
        if unit.primary.id != primary.id:
            raise PermissionDenied("Only a primary-owned exact revision may be released.")
        revision = ExamGenerationRevision.objects.filter(
            pk=revision_id, cycle_course=primary,
        ).first()
        if revision is None:
            raise ValidationError("The reviewed revision is unavailable.")
        if kind == "questionnaire":
            DepartmentalExamAuthorizationService.require_generation_management(
                user=actor, cycle_course=primary,
            )
            if (revision.current_marker != 1
                    or revision.status != ExamGenerationRevision.Status.GENERATED):
                raise ValidationError("Only the current Generated Questionnaire revision may be released.")
            QuestionnairePrintReleaseService._require_valid_revision(
                revision=revision,
                require_current_generated=False,
            )
            participating_ids = {
                campus.id for campus in QuestionnairePrintReleaseService.participating_campuses(unit=unit)
            }
            selected_ids = participating_ids if campus_id == 0 else {campus_id}
            if not selected_ids or not selected_ids.issubset(participating_ids):
                raise PermissionDenied("A selected Questionnaire campus does not participate.")
            for target_id in sorted(selected_ids):
                QuestionnairePrintReleaseService.require_target(
                    course=primary, unit=unit, campus_id=target_id, request=request,
                )
                targets.append((primary_id, revision_id, target_id))
        else:
            if len(base) != 4 or base[3] != campus_id:
                raise ValidationError("Answer Key campus selection changed.")
            recipient_id = base[2]
            recipient = next((member for member in unit.members if member.id == recipient_id), None)
            if recipient is None or not AnswerKeyReleaseService.revision_is_eligible(revision):
                raise PermissionDenied("The Answer Key recipient or revision is unavailable.")
            participating_ids = set(recipient.offering_snapshots.filter(
                campus__is_active=True,
                campus__tenant_id=tenant_id,
                offering__tenant_id=tenant_id,
                offering__campus_id__isnull=False,
                offering__course_id=recipient.course_id,
                offering__academic_year_id=primary.cycle.academic_year_id,
                offering__term_id=primary.cycle.term_id,
            ).values_list("campus_id", flat=True))
            selected_ids = participating_ids if campus_id == 0 else {campus_id}
            if not selected_ids or not selected_ids.issubset(participating_ids):
                raise PermissionDenied("A selected Answer Key recipient campus does not participate.")
            for target_id in sorted(selected_ids):
                _request_campus_allowed(request, target_id)
                DepartmentalExamAuthorizationService.require_answer_key_target(
                    user=actor, cycle_course=primary, recipient_course=recipient,
                    target_campus_id=target_id,
                )
                targets.append((primary_id, revision_id, recipient_id, target_id))
    targets = tuple(sorted(targets))
    keys = [(row[0], row[-1]) if kind == "questionnaire" else (row[2], row[3])
            for row in targets]
    if len(keys) != len(set(keys)):
        raise ValidationError("A release target was selected more than once.")
    return targets


def target_state(*, kind, target, lock=False):
    rows = (QuestionnairePrintRelease.objects if kind == "questionnaire"
            else AnswerKeyRelease.objects)
    if kind == "questionnaire":
        rows = rows.filter(
            cycle_course_id=target[0], target_campus_id=target[2],
            scope_kind=QuestionnairePrintRelease.ScopeKind.SCOPED,
        )
    else:
        rows = rows.filter(
            cycle_course_id=target[0], recipient_course_id=target[2],
            target_campus_id=target[3], scope_kind=AnswerKeyRelease.ScopeKind.SCOPED,
        )
    if lock:
        rows = rows.select_for_update()
    history = list(rows.order_by("-id").values("id", "status", "active_marker")[:2])
    state = {
        "latest_id": history[0]["id"] if history else None,
        "active_id": next((row["id"] for row in history
                           if row["status"] == "ACTIVE" and row["active_marker"] == 1), None),
    }
    if kind == "questionnaire":
        coverage = QuestionnaireLegacyCampusCoverage.objects.filter(
            release__cycle_course_id=target[0], campus_id=target[2],
            release__status="ACTIVE", release__active_marker=1,
        )
        if lock:
            coverage = coverage.select_for_update()
        coverage = coverage.values("id", "retired_at").first()
        state["coverage_id"] = coverage["id"] if coverage else None
        state["coverage_retired"] = bool(coverage and coverage["retired_at"])
    return state


def make_review(*, kind, bases, campus_id, tenant_id, actor, request,
                window_from, window_until, attestation=False):
    targets = expand_targets(
        kind=kind, bases=bases, campus_id=campus_id, tenant_id=tenant_id,
        actor=actor, request=request,
    )
    payload = {
        "kind": kind, "bases": [list(row) for row in bases],
        "campus_id": campus_id, "targets": [list(row) for row in targets],
        "states": [target_state(kind=kind, target=row) for row in targets],
        "tenant_id": tenant_id, "actor_id": actor.id,
        "confirmation_id": token_hex(16),
        "window_from": window_from.isoformat(), "window_until": window_until.isoformat(),
        "attestation": bool(attestation),
    }
    return signing.dumps(payload, salt=SIGNING_SALT, compress=True), payload


def _is_exact_retry(*, kind, target, expected, window_from, window_until,
                    confirmation_id, actor_id):
    model = QuestionnairePrintRelease if kind == "questionnaire" else AnswerKeyRelease
    rows = model.objects.filter(
        cycle_course_id=target[0], target_campus_id=target[-1],
        scope_kind=model.ScopeKind.SCOPED, id__gt=expected["latest_id"] or 0,
    )
    if kind == "answer_key":
        rows = rows.filter(recipient_course_id=target[2])
    rows = list(rows.order_by("id"))
    if len(rows) != 1:
        return None
    row = rows[0]
    if (row.status != "ACTIVE" or row.active_marker != 1
            or row.generation_revision_id != target[1]
            or row.review_confirmation_id != confirmation_id
            or row.released_by_id != actor_id):
        return None
    if kind == "questionnaire":
        return row if row.print_from == window_from and row.print_until == window_until else None
    return row if row.available_from == window_from and row.available_until == window_until else None


@transaction.atomic
def confirm_review(*, token, expected_kind, tenant_id, actor, request):
    try:
        payload = signing.loads(token, salt=SIGNING_SALT, max_age=3600)
    except signing.BadSignature as exc:
        raise ValidationError("Release review has expired or changed. Review targets again.") from exc
    confirmation_id = payload.get("confirmation_id") if isinstance(payload, dict) else None
    if (not isinstance(confirmation_id, str) or len(confirmation_id) != 32
            or any(character not in "0123456789abcdef" for character in confirmation_id)):
        raise ValidationError("Release review changed. Review targets again.")
    if payload["tenant_id"] != tenant_id or payload["actor_id"] != actor.id:
        raise PermissionDenied("Release review belongs to another operator or tenant.")
    if str(payload["campus_id"]) != str(request.POST.get("target_campus_id", "")):
        raise ValidationError("Release campus selection changed after review.")
    kind = payload["kind"]
    if kind != expected_kind:
        raise ValidationError("Release review action changed. Review targets again.")
    bases = tuple(tuple(row) for row in payload["bases"])
    expected_targets = tuple(tuple(row) for row in payload["targets"])
    cycle_ids = sorted(set(CycleCourse.objects.filter(
        pk__in=[row[0] for row in bases], cycle__tenant_id=tenant_id,
    ).values_list("cycle_id", flat=True)))
    list(ExaminationCycle.objects.select_for_update().filter(
        pk__in=cycle_ids, tenant_id=tenant_id,
    ).order_by("id"))
    list(CycleCourse.objects.select_for_update().filter(
        cycle_id__in=cycle_ids,
    ).order_by("id"))
    list(ExamCourseEquivalencyGroup.objects.select_for_update().filter(
        cycle_id__in=cycle_ids,
    ).order_by("id"))
    list(ExamCourseEquivalencyMembership.objects.select_for_update().filter(
        cycle_course__cycle_id__in=cycle_ids,
    ).order_by("id"))
    snapshots = list(CycleCourseOffering.objects.select_for_update().filter(
        cycle_course__cycle_id__in=cycle_ids,
    ).order_by("id"))
    list(CourseOffering.objects.select_for_update().filter(
        pk__in={snapshot.offering_id for snapshot in snapshots},
    ).order_by("id"))
    list(Campus.objects.select_for_update().filter(
        tenant_id=tenant_id,
    ).order_by("id"))
    list(ExamGenerationRevision.objects.select_for_update().filter(
        pk__in=[row[1] for row in bases],
    ).order_by("id"))
    targets = expand_targets(
        kind=kind, bases=bases, campus_id=payload["campus_id"],
        tenant_id=tenant_id, actor=actor, request=request,
    )
    if targets != expected_targets:
        raise ValidationError("Participating release targets changed. Review the full selection again.")
    window_from = datetime.fromisoformat(payload["window_from"])
    window_until = datetime.fromisoformat(payload["window_until"])
    states = [target_state(kind=kind, target=row, lock=True) for row in targets]
    if states != payload["states"]:
        retries = []
        for row, expected, current in zip(targets, payload["states"], states):
            if current == expected and expected["active_id"]:
                model = QuestionnairePrintRelease if kind == "questionnaire" else AnswerKeyRelease
                prior = model.objects.filter(pk=expected["active_id"]).first()
                from_value = prior.print_from if kind == "questionnaire" else prior.available_from
                until_value = prior.print_until if kind == "questionnaire" else prior.available_until
                retries.append(prior if (
                    prior.generation_revision_id == row[1]
                    and from_value == window_from and until_value == window_until
                ) else None)
            else:
                retries.append(_is_exact_retry(
                    kind=kind, target=row, expected=expected,
                    window_from=window_from, window_until=window_until,
                    confirmation_id=confirmation_id, actor_id=actor.id,
                ))
        if all(retries):
            return tuple(retries)
        raise ValidationError("Release state changed after review. Review all targets again.")
    if kind == "questionnaire":
        return QuestionnairePrintReleaseService.bulk_release(
            selections=targets, tenant_id=tenant_id, actor=actor,
            print_from=window_from, print_until=window_until, request=request,
            review_confirmation_id=confirmation_id,
        )
    return AnswerKeyReleaseService.bulk_release(
        selections=targets, tenant_id=tenant_id, actor=actor,
        available_from=window_from, available_until=window_until,
        attestation_confirmed=payload["attestation"], request=request,
        allow_multiple_campuses=payload["campus_id"] == 0,
        review_confirmation_id=confirmation_id,
    )
