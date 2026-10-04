"""Audited actual missed intervals for mixed A/N and L/E findings."""

import re
from collections import defaultdict
from datetime import time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.services.audit import AuditService

from .models import AttendanceCutoffPublication, AttendanceCutoffPublicationEntry, AttendanceResult, DTRMixedFindingDecision
from .permissions import DTR_EDIT_PERMISSION, require_attendance_permission


INTERVAL = re.compile(r"^(A|N|L|E)\s+(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})$")


def needs_interval_reconciliation(entry):
    amounts = (
        Decimal(entry.absent_without_notice_hours), Decimal(entry.absent_with_notice_hours),
        Decimal(entry.late_minutes) / 60, Decimal(entry.early_minutes) / 60,
    )
    return (amounts[0] > 0 or amounts[1] > 0) and sum(value > 0 for value in amounts) > 1


def parse_missed_intervals(entry, text):
    """Accept one `A 08:00-08:30` line per actual, non-overlapping missed span."""
    meeting_start = timezone.localtime(entry.starts_at)
    meeting_end = timezone.localtime(entry.ends_at)
    if meeting_start.date() != meeting_end.date():
        raise ValidationError("Overnight classes need a separately reviewed interval correction.")
    start_bound = meeting_start.hour * 60 + meeting_start.minute
    end_bound = meeting_end.hour * 60 + meeting_end.minute
    limits = {
        "A": Decimal(entry.absent_without_notice_hours) * 60,
        "N": Decimal(entry.absent_with_notice_hours) * 60,
        "L": Decimal(entry.late_minutes), "E": Decimal(entry.early_minutes),
    }
    intervals = []
    totals = defaultdict(int)
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        match = INTERVAL.fullmatch(raw.strip())
        if not match:
            raise ValidationError("Enter each actual missed span as A/N/L/E HH:MM-HH:MM, one per line.")
        kind, start_text, end_text = match.groups()
        try:
            start_clock, end_clock = time.fromisoformat(start_text), time.fromisoformat(end_text)
        except ValueError as exc:
            raise ValidationError("Use valid 24-hour clock times for missed intervals.") from exc
        start = start_clock.hour * 60 + start_clock.minute
        end = end_clock.hour * 60 + end_clock.minute
        if not start_bound <= start < end <= end_bound:
            raise ValidationError("Missed intervals must be positive and within this dated class time.")
        if not limits[kind]:
            raise ValidationError("An interval category must exist in the published attendance finding.")
        totals[kind] += end - start
        if Decimal(totals[kind]) > limits[kind]:
            raise ValidationError("An actual missed interval exceeds its recorded A/N hours or L/E minutes.")
        intervals.append({"kind": kind, "start": start_text, "end": end_text, "minutes": end - start})
    if not intervals:
        raise ValidationError("Record the actual missed intervals before finalizing this mixed finding.")
    intervals.sort(key=lambda row: row["start"])
    for previous, following in zip(intervals, intervals[1:]):
        if previous["end"] > following["start"]:
            raise ValidationError("Actual missed intervals overlap; reconcile the class without double deduction.")
    return intervals


def current_mixed_decisions(*, publication, faculty):
    rows = DTRMixedFindingDecision.objects.filter(
        publication=publication, faculty_user=faculty,
    ).order_by("meeting_id", "-revision", "-pk")
    latest = {}
    for row in rows:
        latest.setdefault(row.meeting_id, row)
    return latest


@transaction.atomic
def save_mixed_decision(*, actor, publication, faculty, meeting, intervals_text, reason, expected_revision):
    from .notice_locking import lock_notice_campus
    lock_notice_campus(publication.campus_id)
    publication = AttendanceCutoffPublication.objects.select_for_update().get(pk=publication.pk)
    entry = AttendanceCutoffPublicationEntry.objects.select_for_update().select_related(
        "meeting", "result_revision",
    ).filter(publication=publication, meeting=meeting, faculty_user=faculty).first()
    if entry is None or not needs_interval_reconciliation(entry) or not entry.result_revision_id:
        raise ValidationError("Select a published mixed-finding class attributed to this faculty.")
    require_attendance_permission(
        user=actor, permission_code=DTR_EDIT_PERMISSION,
        tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        department_id=entry.meeting.department_id,
    )
    current = AttendanceResult.objects.select_for_update().filter(meeting=meeting).first()
    if not current or current.revision != entry.result_revision_number or current.faculty_user_id != faculty.pk:
        raise ValidationError("Attendance changed; republish before reconciling DTR missed intervals.")
    intervals = parse_missed_intervals(entry, intervals_text)
    previous = DTRMixedFindingDecision.objects.select_for_update().filter(
        publication=publication, meeting=meeting,
    ).order_by("-revision", "-pk").first()
    if (previous.revision if previous else 0) != expected_revision:
        raise ValidationError("Missed-interval decision changed; reload before correcting it.")
    item = DTRMixedFindingDecision(
        publication=publication, meeting=meeting, result_revision=entry.result_revision,
        faculty_user=faculty, revision=expected_revision + 1, supersedes=previous,
        intervals=intervals, reason=(reason or "").strip(), decided_by=actor,
    )
    item.full_clean()
    item.save()
    AuditService.log_event(
        action="FACULTY_ATTENDANCE_DTR_INTERVALS_REVISED" if previous else "FACULTY_ATTENDANCE_DTR_INTERVALS_RECONCILED",
        portal="ADMIN", entity_type="DTRMixedFindingDecision", entity_id=item.pk,
        actor=actor, tenant=publication.tenant_id, campus=publication.campus_id,
        before_data={"revision": previous.revision} if previous else None,
        after_data={
            "publication_id": publication.pk, "meeting_id": meeting.pk, "revision": item.revision,
            "result_revision_id": entry.result_revision_id, "intervals": intervals,
        },
    )
    return item
