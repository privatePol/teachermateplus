"""Audited, per-meeting no-class decisions; no attendance result is invented."""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.services.audit import AuditService

from .models import AttendanceClosureDecision, AttendanceResult, TeachingMeeting
from .observations import require_confirmable_meeting_faculty
from .permissions import CORRECT_PERMISSION, require_attendance_permission
from .notice_locking import lock_notice_campus


def latest_closure(meeting):
    return AttendanceClosureDecision.objects.filter(meeting=meeting).order_by("-revision", "-pk").first()


@transaction.atomic
def save_closure(*, actor, meeting, status, kind, pay_basis, reason, expected_revision):
    lock_notice_campus(meeting.campus_id)
    meeting = TeachingMeeting.objects.select_for_update().select_related("substitution").get(pk=meeting.pk)
    require_attendance_permission(
        user=actor, permission_code=CORRECT_PERMISSION,
        tenant_id=meeting.tenant_id, campus_id=meeting.campus_id, department_id=meeting.department_id,
    )
    if status not in AttendanceClosureDecision.Status.values:
        raise ValidationError("Choose class closed or revoke the closure.")
    if kind not in AttendanceClosureDecision.Kind.values:
        raise ValidationError("Choose a holiday or class suspension.")
    if pay_basis not in AttendanceClosureDecision.PayBasis.values:
        raise ValidationError("Verify regular or part-time pay basis for this dated closure.")
    reason = (reason or "").strip()
    faculty = require_confirmable_meeting_faculty(meeting)
    previous = AttendanceClosureDecision.objects.select_for_update().filter(meeting=meeting).order_by("-revision", "-pk").first()
    if (previous.revision if previous else 0) != expected_revision:
        raise ValidationError("Closure decision changed; reload before saving a correction.")
    if status == AttendanceClosureDecision.Status.REVOKED and previous is None:
        raise ValidationError("There is no closure to revoke.")
    result = AttendanceResult.objects.select_for_update().filter(meeting=meeting).first()
    item = AttendanceClosureDecision(
        meeting=meeting, revision=expected_revision + 1, supersedes=previous,
        status=status, kind=kind, pay_basis=pay_basis, faculty_user=faculty,
        result_revision_at_decision=result.revision if result else 0,
        reason=reason.strip(), decided_by=actor,
    )
    item.full_clean()
    item.save()
    from .staff_notices import refresh_for_meeting
    refresh_for_meeting(meeting)
    AuditService.log_event(
        action="FACULTY_ATTENDANCE_CLOSURE_REVISED" if previous else "FACULTY_ATTENDANCE_CLOSURE_RECORDED",
        portal="ADMIN", entity_type="AttendanceClosureDecision", entity_id=item.pk,
        actor=actor, tenant=meeting.tenant_id, campus=meeting.campus_id,
        before_data={"revision": previous.revision, "status": previous.status} if previous else None,
        after_data={
            "meeting_id": meeting.pk, "revision": item.revision, "status": status,
            "kind": kind, "pay_basis": pay_basis, "faculty_user_id": faculty.pk,
            "result_revision_at_decision": item.result_revision_at_decision,
        },
    )
    return item
