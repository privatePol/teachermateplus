from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.core.services.audit import AuditService

from .daily_encoding import _combined_groups, expected_daily_occurrences
from .models import (
    AttendanceClosureDecision,
    AttendanceCutoffPublication,
    AttendanceCutoffPublicationEntry,
    AttendanceResult,
    AttendanceResultRevision,
    CoverageReconciliation,
    TeachingMeeting,
)
from .observations import StaleAttendanceReview, require_confirmable_meeting_faculty
from .permissions import PUBLISH_PERMISSION, can_faculty_view_own_attendance, require_attendance_permission


@dataclass(frozen=True)
class CutoffBlocker:
    code: str
    message: str
    occurrence_key: str = ""
    meeting_id: int | None = None


@dataclass(frozen=True)
class CutoffRecord:
    occurrence_key: str
    meeting: TeachingMeeting
    result: AttendanceResult | None
    result_revision: AttendanceResultRevision | None
    closure: AttendanceClosureDecision | None = None


@dataclass(frozen=True)
class CutoffReview:
    tenant_id: int
    campus_id: int
    academic_year_id: int
    term_id: int
    start_date: date
    end_date: date
    records: tuple[CutoffRecord, ...]
    blockers: tuple[CutoffBlocker, ...]
    fingerprint: str

    @property
    def ready(self):
        return bool(self.records) and not self.blockers


def _fingerprint(*, records, blockers):
    payload = {
        "records": [
            {
                "occurrence_key": item.occurrence_key,
                "meeting_id": item.meeting.pk,
                "result_id": item.result.pk if item.result else None,
                "result_revision": item.result.revision if item.result else None,
                "faculty_user_id": item.result.faculty_user_id if item.result else item.closure.faculty_user_id,
                "closure_id": item.closure.pk if item.closure else None,
                "closure_revision": item.closure.revision if item.closure else None,
            }
            for item in records
        ],
        "blockers": [
            {
                "code": item.code,
                "occurrence_key": item.occurrence_key,
                "meeting_id": item.meeting_id,
                "message": item.message,
            }
            for item in blockers
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _complete_campus_permission(*, actor, offerings):
    department_ids = sorted({item.department_id for item in offerings})
    if not department_ids:
        raise ValidationError("No Course Offerings exist for the selected campus, academic year, and semester.")
    for department_id in department_ids:
        try:
            require_attendance_permission(
                user=actor,
                permission_code=PUBLISH_PERMISSION,
                tenant_id=offerings[0].tenant_id,
                campus_id=offerings[0].campus_id,
                department_id=department_id,
            )
        except PermissionDenied as exc:
            raise PermissionDenied("Complete-campus cutoff publication authority is required.") from exc


def _meeting_signature(meeting):
    local_start = timezone.localtime(meeting.starts_at).time().replace(tzinfo=None)
    local_end = timezone.localtime(meeting.ends_at).time().replace(tzinfo=None)
    offering_ids = tuple(sorted(link.offering_id for link in meeting.offering_links.all()))
    return offering_ids, meeting.meeting_date, local_start, local_end


def _occurrence_signature(occurrence):
    return (
        tuple(sorted(item.pk for item in occurrence.linked_offerings)),
        occurrence.meeting_date,
        occurrence.start_time,
        occurrence.end_time,
    )


def review_cutoff(*, actor, tenant_id, campus_id, academic_year, term, start_date, end_date, lock=False):
    if end_date < start_date:
        raise ValidationError("Cutoff end date cannot precede start date.")
    offering_queryset = CourseOffering.objects.filter(
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year=academic_year,
        term=term,
    ).select_related("course", "section", "department")
    if lock:
        offering_queryset = offering_queryset.select_for_update()
    offerings = list(offering_queryset.prefetch_related("attendance_source_changes"))
    _complete_campus_permission(actor=actor, offerings=offerings)
    combined = _combined_groups(
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year_id=academic_year.pk,
        term_id=term.pk,
        offering_ids={item.pk for item in offerings},
    )
    occurrences, occurrence_issues = expected_daily_occurrences(
        offerings=offerings,
        term=term,
        start_date=start_date,
        end_date=end_date,
        combined_classes=combined,
    )
    blockers = [CutoffBlocker(code=item.code, message=item.message, occurrence_key=item.occurrence_key) for item in occurrence_issues]

    meeting_queryset = TeachingMeeting.objects.filter(
        tenant_id=tenant_id,
        campus_id=campus_id,
        meeting_date__range=(start_date, end_date),
        offering_links__offering__academic_year=academic_year,
        offering_links__offering__term=term,
    ).distinct().select_related("attendance_result", "substitution")
    if lock:
        meeting_queryset = meeting_queryset.select_for_update()
    meetings = list(
        meeting_queryset.prefetch_related("offering_links", "reconciliations", "closure_decisions")
    )
    meetings_by_key = {item.occurrence_key: item for item in meetings if item.occurrence_key}
    meetings_by_signature = {_meeting_signature(item): item for item in meetings}
    revisions = {
        (item.result_id, item.revision): item
        for item in AttendanceResultRevision.objects.filter(
            result_id__in=[item.attendance_result.pk for item in meetings if hasattr(item, "attendance_result")],
        )
    }
    matched_meeting_ids = set()
    records = []
    for occurrence in occurrences:
        meeting = meetings_by_key.get(occurrence.occurrence_key) or meetings_by_signature.get(
            _occurrence_signature(occurrence)
        )
        if meeting is None:
            blockers.append(
                CutoffBlocker(
                    code="UNMATERIALIZED_OCCURRENCE",
                    occurrence_key=occurrence.occurrence_key,
                    message=(
                        f"{occurrence.meeting_date} {occurrence.primary_offering.course.code} / "
                        f"{occurrence.primary_offering.section.code} has not been encoded yet."
                    ),
                )
            )
            continue
        matched_meeting_ids.add(meeting.pk)
        try:
            dated_faculty = require_confirmable_meeting_faculty(meeting)
        except ValidationError:
            blockers.append(
                CutoffBlocker(
                    code="MEETING_RECONCILIATION_PENDING",
                    meeting_id=meeting.pk,
                    occurrence_key=occurrence.occurrence_key,
                    message=f"{meeting.meeting_date} has unresolved faculty or attendance-history review.",
                )
            )
            continue
        result = getattr(meeting, "attendance_result", None)
        closure = max(meeting.closure_decisions.all(), key=lambda item: (item.revision, item.pk), default=None)
        if closure and closure.status == AttendanceClosureDecision.Status.CLOSED:
            if closure.faculty_user_id != dated_faculty.pk or closure.result_revision_at_decision != (result.revision if result else 0):
                blockers.append(CutoffBlocker(
                    code="CLOSURE_REVIEW_PENDING", meeting_id=meeting.pk,
                    occurrence_key=occurrence.occurrence_key,
                    message=f"{meeting.meeting_date} closure faculty or attendance changed; revise the dated checker decision before publication.",
                ))
                continue
            records.append(CutoffRecord(occurrence.occurrence_key, meeting, None, None, closure))
            continue
        if result is None or result.status == AttendanceResult.Status.UNVERIFIED:
            blockers.append(
                CutoffBlocker(
                    code="UNVERIFIED_ATTENDANCE",
                    meeting_id=meeting.pk,
                    occurrence_key=occurrence.occurrence_key,
                    message=f"{meeting.meeting_date} is still unverified; explicitly record an exception or confirm present.",
                )
            )
            continue
        if result.faculty_user_id is None:
            blockers.append(
                CutoffBlocker(
                    code="FACULTY_ATTRIBUTION_MISSING",
                    meeting_id=meeting.pk,
                    occurrence_key=occurrence.occurrence_key,
                    message=f"{meeting.meeting_date} has no confirmed faculty attribution.",
                )
            )
            continue
        if meeting.unresolved_coverage and result.faculty_user_id != dated_faculty.pk:
            blockers.append(
                CutoffBlocker(
                    code="FACULTY_ATTRIBUTION_CONFLICT",
                    meeting_id=meeting.pk,
                    occurrence_key=occurrence.occurrence_key,
                    message=(
                        f"{meeting.meeting_date} attendance faculty does not match its verified dated attribution. "
                        "Review and correct the attribution before publication."
                    ),
                )
            )
            continue
        revision = revisions.get((result.pk, result.revision))
        if revision is None:
            blockers.append(
                CutoffBlocker(
                    code="RESULT_REVISION_MISSING",
                    meeting_id=meeting.pk,
                    occurrence_key=occurrence.occurrence_key,
                    message=f"{meeting.meeting_date} has no immutable revision matching its current attendance result.",
                )
            )
            continue
        records.append(CutoffRecord(occurrence.occurrence_key, meeting, result, revision))

    for meeting in meetings:
        if meeting.pk not in matched_meeting_ids:
            blockers.append(
                CutoffBlocker(
                    code="RECORDED_MEETING_SOURCE_MISMATCH",
                    meeting_id=meeting.pk,
                    message=(
                        f"Recorded meeting on {meeting.meeting_date} no longer matches the expected Course Offering source. "
                        "Resolve the source history before publication."
                    ),
                )
            )
    pending_coverage = CoverageReconciliation.objects.filter(
        tenant_id=tenant_id,
        campus_id=campus_id,
        offering_id__in=[item.pk for item in offerings],
        status=CoverageReconciliation.Status.PENDING,
    )
    for item in pending_coverage:
        blockers.append(
            CutoffBlocker(
                code="COVERAGE_CHANGE_PENDING",
                message=f"{item.offering.course.code} / {item.offering.section.code} has an unresolved faculty coverage change.",
            )
        )
    records = tuple(sorted(records, key=lambda item: (item.meeting.meeting_date, item.meeting.starts_at, item.occurrence_key)))
    blockers = tuple(sorted(blockers, key=lambda item: (item.code, item.message, item.occurrence_key)))
    return CutoffReview(
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year_id=academic_year.pk,
        term_id=term.pk,
        start_date=start_date,
        end_date=end_date,
        records=records,
        blockers=blockers,
        fingerprint=_fingerprint(records=records, blockers=blockers),
    )


def _publication_entry_values(record):
    meeting = record.meeting
    result = record.result
    meeting_snapshot = {
        "schedule": meeting.schedule_snapshot,
        "location": meeting.location_snapshot,
        "sections": meeting.sections_snapshot,
        "faculty": meeting.faculty_snapshot,
    }
    adoption = getattr(meeting, "coverage_adoption", None)
    if adoption:
        meeting_snapshot["coverage_adoption"] = {"id": adoption.pk, "decision": adoption.decision_snapshot}
    if record.closure:
        closure = record.closure
        meeting_snapshot["closure"] = {
            "kind": closure.kind, "pay_basis": closure.pay_basis, "reason": closure.reason,
            "revision": closure.revision, "faculty_user_id": closure.faculty_user_id,
        }
        return {
            "occurrence_key": record.occurrence_key, "meeting": meeting,
            "closure_decision": closure, "result_revision": None,
            "faculty_user": closure.faculty_user, "meeting_date": meeting.meeting_date,
            "starts_at": meeting.starts_at, "ends_at": meeting.ends_at,
            "scheduled_minutes": meeting.scheduled_minutes, "status": "CLOSED",
            "late_flag": False, "late_minutes": 0, "early_flag": False, "early_minutes": 0,
            "absent_without_notice_hours": 0, "absent_with_notice_hours": 0,
            "missed_periods": 0, "meeting_snapshot": meeting_snapshot,
            "findings_snapshot": [], "result_revision_number": 0,
        }
    return {
        "occurrence_key": record.occurrence_key,
        "meeting": meeting,
        "result_revision": record.result_revision,
        "faculty_user": result.faculty_user,
        "meeting_date": meeting.meeting_date,
        "starts_at": meeting.starts_at,
        "ends_at": meeting.ends_at,
        "scheduled_minutes": meeting.scheduled_minutes,
        "status": result.status,
        "late_flag": result.late_flag,
        "late_minutes": result.late_minutes,
        "early_flag": result.early_flag,
        "early_minutes": result.early_minutes,
        "absent_without_notice_hours": result.absent_without_notice_hours,
        "absent_with_notice_hours": result.absent_with_notice_hours,
        "missed_periods": result.missed_periods,
        "meeting_snapshot": meeting_snapshot,
        "findings_snapshot": result.findings_snapshot,
        "result_revision_number": result.revision,
    }


@transaction.atomic
def publish_cutoff(
    *,
    actor,
    tenant_id,
    campus_id,
    academic_year,
    term,
    start_date,
    end_date,
    expected_fingerprint,
    submission_key,
    publication_reason,
):
    if not submission_key:
        raise ValidationError("Publication submission key is required.")
    publication_reason = (publication_reason or "").strip()
    existing = AttendanceCutoffPublication.objects.select_for_update().filter(submission_key=submission_key).first()
    if existing:
        if existing.review_fingerprint != expected_fingerprint:
            raise StaleAttendanceReview("This publication request belongs to a different review state.")
        return existing
    review = review_cutoff(
        actor=actor,
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year=academic_year,
        term=term,
        start_date=start_date,
        end_date=end_date,
        lock=True,
    )
    if review.fingerprint != expected_fingerprint:
        raise StaleAttendanceReview("Cutoff data changed after review. Reload before publishing.")
    if not review.ready:
        raise ValidationError("Complete every blocking attendance record before publishing this campus cutoff.")
    previous = (
        AttendanceCutoffPublication.objects.select_for_update()
        .filter(
            tenant_id=tenant_id,
            campus_id=campus_id,
            academic_year=academic_year,
            term=term,
            start_date=start_date,
            end_date=end_date,
        )
        .order_by("-version", "-pk")
        .first()
    )
    publication = AttendanceCutoffPublication(
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year=academic_year,
        term=term,
        start_date=start_date,
        end_date=end_date,
        lineage_key=previous.lineage_key if previous else None,
        version=(previous.version + 1) if previous else 1,
        review_fingerprint=review.fingerprint,
        submission_key=submission_key,
        published_by=actor,
        published_at=timezone.now(),
        publication_reason=publication_reason.strip(),
        supersedes=previous,
    )
    if publication.lineage_key is None:
        publication.lineage_key = AttendanceCutoffPublication._meta.get_field("lineage_key").get_default()
    publication.full_clean()
    publication.save()
    entries = []
    for record in review.records:
        entry = AttendanceCutoffPublicationEntry(publication=publication, **_publication_entry_values(record))
        entry.full_clean()
        entries.append(entry)
    AttendanceCutoffPublicationEntry.objects.bulk_create(entries)
    AuditService.log_event(
        action="FACULTY_ATTENDANCE_CUTOFF_PUBLISHED",
        portal="ADMIN",
        entity_type="AttendanceCutoffPublication",
        entity_id=publication.pk,
        actor=actor,
        tenant=publication.tenant,
        campus=publication.campus,
        after_data={
            "start_date": start_date,
            "end_date": end_date,
            "version": publication.version,
            "entry_count": len(entries),
        },
    )
    return publication


def _latest_publications(*, tenant_id, campus_id):
    publications = list(
        AttendanceCutoffPublication.objects.filter(tenant_id=tenant_id, campus_id=campus_id).order_by(
            "lineage_key", "-version", "-published_at"
        )
    )
    latest = {}
    for publication in publications:
        latest.setdefault(publication.lineage_key, publication)
    return list(latest.values())


def faculty_published_entries(*, faculty_user, tenant_id, campus_id, start_date, end_date, include_history=False):
    if not can_faculty_view_own_attendance(user=faculty_user, tenant_id=tenant_id, campus_id=campus_id):
        raise PermissionDenied("My Attendance is disabled or unavailable for this account.")
    publications = _latest_publications(tenant_id=tenant_id, campus_id=campus_id)
    publication_ids = [item.pk for item in publications]
    entries = list(
        AttendanceCutoffPublicationEntry.objects.filter(
            publication_id__in=publication_ids,
            meeting_date__range=(start_date, end_date),
        ).select_related("publication")
    )
    deduped = {}
    for entry in entries:
        current = deduped.get(entry.occurrence_key)
        if current is None or (entry.publication.published_at, entry.publication.version, entry.pk) > (
            current.publication.published_at,
            current.publication.version,
            current.pk,
        ):
            deduped[entry.occurrence_key] = entry
    history = []
    if include_history:
        history = list(
            AttendanceCutoffPublicationEntry.objects.filter(
                publication__tenant_id=tenant_id,
                publication__campus_id=campus_id,
                faculty_user=faculty_user,
                meeting_date__range=(start_date, end_date),
            ).select_related("publication").order_by("-publication__published_at", "meeting_date", "starts_at")
        )
    return (
        sorted((entry for entry in deduped.values() if entry.faculty_user_id == faculty_user.pk),
               key=lambda item: (item.meeting_date, item.starts_at, item.occurrence_key)),
        publications,
        history,
    )


def published_tardiness_summary(*, faculty_user, tenant_id, campus_id, year, month):
    start_date = date(year, month, 1)
    next_month = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    end_date = next_month - timedelta(days=1)
    entries, publications, _history = faculty_published_entries(
        faculty_user=faculty_user,
        tenant_id=tenant_id,
        campus_id=campus_id,
        start_date=start_date,
        end_date=end_date,
    )
    covered_days = set()
    for publication in publications:
        current = max(start_date, publication.start_date)
        final = min(end_date, publication.end_date)
        while current <= final:
            covered_days.add(current)
            current += timedelta(days=1)
    complete = len(covered_days) == (end_date - start_date).days + 1
    count = sum(1 for entry in entries if entry.late_flag)
    threshold_state = (
        "exceeded" if count >= 5 else "reached" if count == 4 else "nearing" if count == 3 else "within_limit"
    )
    return {
        "count": count,
        "threshold_state": threshold_state,
        "is_complete": complete,
        "start_date": start_date,
        "end_date": end_date,
    }
