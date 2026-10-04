from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.core.services.audit import AuditService

from .daily_encoding import _combined_groups, expected_daily_occurrences, resolve_occurrence_faculty
from .models import (
    AttendanceClosureDecision,
    AttendanceCutoffPublication,
    AttendanceCutoffPublicationEntry,
    AttendanceResult,
    AttendanceResultRevision,
    CoverageReconciliation,
    MeetingReconciliation,
    MeetingOffering,
    OfferingAttendanceSourceChange,
    TeachingMeeting,
)
from .observations import StaleAttendanceReview, require_confirmable_meeting_faculty, resolve_attendance_faculty, prime_meeting_readiness
from .permissions import PUBLISH_PERMISSION, can_faculty_view_own_attendance, require_attendance_permission


@dataclass(frozen=True)
class CutoffBlocker:
    code: str
    message: str
    occurrence_key: str = ""
    meeting_id: int | None = None
    offering_ids: tuple[int, ...] = ()
    meeting_date: date | None = None
    details: dict = field(default_factory=dict, compare=False)


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
    offerings: tuple = field(default=(), repr=False, compare=False)
    occurrences: tuple = field(default=(), repr=False, compare=False)
    meetings: tuple = field(default=(), repr=False, compare=False)

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
    return _review_cutoff(actor=actor, tenant_id=tenant_id, campus_id=campus_id,
        academic_year=academic_year, term=term, start_date=start_date, end_date=end_date, lock=lock)


def _review_cutoff(*, actor, tenant_id, campus_id, academic_year, term, start_date, end_date,
                   lock=False, faculty_review=False):
    # Internal evidence builder. Faculty callers authorize/filter each complete
    # slice before exposing it; the public campus path keeps its original gate.
    if end_date < start_date:
        raise ValidationError("Cutoff end date cannot precede start date.")
    if academic_year.tenant_id != tenant_id or term.tenant_id != tenant_id or term.academic_year_id != academic_year.pk:
        raise ValidationError("Select a semester in this tenant and academic year.")
    offering_queryset = CourseOffering.objects.filter(
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year=academic_year,
        term=term,
    ).select_related("course", "section", "department")
    if lock:
        offering_queryset = offering_queryset.select_for_update()
    source_prefetch = Prefetch("attendance_source_changes", queryset=OfferingAttendanceSourceChange.objects.select_for_update()) if faculty_review and lock else "attendance_source_changes"
    offerings = list(offering_queryset.prefetch_related(source_prefetch))
    if not faculty_review:
        _complete_campus_permission(actor=actor, offerings=offerings)
    combined = _combined_groups(
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year_id=academic_year.pk,
        term_id=term.pk,
        offering_ids={item.pk for item in offerings},
        lock=faculty_review and lock,
    )
    occurrences, occurrence_issues = expected_daily_occurrences(
        offerings=offerings,
        term=term,
        start_date=start_date,
        end_date=end_date,
        combined_classes=combined,
        lock=faculty_review and lock,
    )
    blockers = [CutoffBlocker(code=item.code, message=item.message, occurrence_key=item.occurrence_key,
        offering_ids=tuple(o.pk for o in item.affected_offerings), meeting_date=item.meeting_date) for item in occurrence_issues]

    from .college_sync import retired_meeting_ids
    meeting_queryset = TeachingMeeting.objects.exclude(pk__in=retired_meeting_ids(
        lock=faculty_review and lock, tenant_id=tenant_id, campus_id=campus_id)).filter(
        tenant_id=tenant_id,
        campus_id=campus_id,
        meeting_date__range=(start_date, end_date),
        offering_links__offering__academic_year=academic_year,
        offering_links__offering__term=term,
    ).distinct().select_related("attendance_result", "substitution")
    if lock:
        meeting_queryset = meeting_queryset.select_for_update()
    if faculty_review and lock:
        meeting_queryset = meeting_queryset.select_related("coverage_adoption__faculty_user", "faculty_user",
            "attendance_result__faculty_user", "substitution__substitute_faculty").prefetch_related(
            Prefetch("offering_links", queryset=MeetingOffering.objects.select_for_update()),
            Prefetch("reconciliations", queryset=MeetingReconciliation.objects.select_for_update()),
            Prefetch("closure_decisions", queryset=AttendanceClosureDecision.objects.select_for_update()))
    else:
        meeting_queryset = meeting_queryset.prefetch_related("offering_links", "reconciliations", "closure_decisions")
    if faculty_review:
        meeting_queryset = meeting_queryset.prefetch_related("faculty_user", "attendance_result__faculty_user",
            "substitution__substitute_faculty", "coverage_adoption__faculty_user")
    meetings = list(meeting_queryset)
    if not (faculty_review and lock):
        prime_meeting_readiness(meetings)
    if faculty_review and lock:
        # Current locking reads after the campus mutex, also under InnoDB
        # REPEATABLE READ. A waiting writer cannot validate an older snapshot.
        pending_meetings = set(MeetingReconciliation.objects.select_for_update().filter(
            meeting_id__in=[m.pk for m in meetings], status="PENDING").values_list("meeting_id", flat=True))
        pending_sources = list(CoverageReconciliation.objects.select_for_update().filter(
            offering_id__in=[o.pk for o in offerings], status="PENDING").values_list("offering_id", "effective_at"))
        closures = defaultdict(list)
        for closure in AttendanceClosureDecision.objects.select_for_update().filter(meeting_id__in=[m.pk for m in meetings]):
            closures[closure.meeting_id].append(closure)
        for meeting in meetings:
            # Current retirement evidence already excluded retired rows above.
            meeting._attendance_retired = False
            linked_ids = {l.offering_id for l in meeting.offering_links.all()}
            meeting._attendance_pending_review = meeting.pk in pending_meetings or any(
                oid in linked_ids and (effective is None or effective <= meeting.starts_at) for oid, effective in pending_sources)
            meeting._prefetched_objects_cache["closure_decisions"] = closures[meeting.pk]
    meetings_by_key = {item.occurrence_key: item for item in meetings if item.occurrence_key}
    meetings_by_signature = {_meeting_signature(item): item for item in meetings}
    revisions = {
        (item.result_id, item.revision): item
        for item in (AttendanceResultRevision.objects.select_for_update() if faculty_review and lock else AttendanceResultRevision.objects).filter(
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
        if (faculty_review or meeting.unresolved_coverage) and result.faculty_user_id != dated_faculty.pk:
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
    pending_coverage = CoverageReconciliation.objects.select_related("offering__course", "offering__section").filter(
        tenant_id=tenant_id,
        campus_id=campus_id,
        offering_id__in=[item.pk for item in offerings],
        status=CoverageReconciliation.Status.PENDING,
    ).filter(Q(effective_at__isnull=True) | Q(effective_at__lt=timezone.make_aware(
        datetime.combine(end_date + timedelta(days=1), time.min))))
    if faculty_review and lock:
        pending_coverage = pending_coverage.select_for_update()
    for item in pending_coverage:
        blockers.append(
            CutoffBlocker(
                code="COVERAGE_CHANGE_PENDING",
                offering_ids=(item.offering_id,),
                message=f"{item.offering.course.code} / {item.offering.section.code} has an unresolved faculty coverage change.",
            )
        )
    records = tuple(sorted(records, key=lambda item: (item.meeting.meeting_date, item.meeting.starts_at, item.occurrence_key)))
    # Presentation metadata is scoped to the authorized review. It is excluded
    # from the readiness fingerprint and never changes saved source evidence.
    meeting_map = {m.pk: m for m in meetings}
    occurrence_map = {o.occurrence_key: o for o in occurrences}
    offering_map = {o.pk: o for o in offerings}
    decorated = []
    for blocker in blockers:
        meeting = meeting_map.get(blocker.meeting_id)
        occurrence = occurrence_map.get(blocker.occurrence_key)
        details = {}
        if faculty_review:
            decorated.append(blocker)
            continue
        if meeting:
            result = getattr(meeting, "attendance_result", None)
            faculty, _source = resolve_attendance_faculty(meeting, result)
            if not result:
                try:
                    faculty = require_confirmable_meeting_faculty(meeting)
                except ValidationError:
                    faculty = None
            details = {"date": meeting.meeting_date, "start": timezone.localtime(meeting.starts_at).time(),
                "end": timezone.localtime(meeting.ends_at).time(), "faculty": faculty,
                "sections": meeting.sections_snapshot, "room": meeting.location_snapshot.get("room") or meeting.location_snapshot.get("room_text"),
                "department_id": meeting.department_id}
        elif occurrence:
            details = {"date": occurrence.meeting_date, "start": occurrence.start_time, "end": occurrence.end_time,
                "faculty": resolve_occurrence_faculty(occurrence), "room": occurrence.source_room,
                "sections": [{"course_code": o.course.code, "course_title": o.course.title, "section_code": o.section.code}
                             for o in occurrence.linked_offerings], "department_id": occurrence.primary_offering.department_id}
        elif blocker.offering_ids:
            sources = [offering_map[oid] for oid in blocker.offering_ids if oid in offering_map]
            details = {"date": blocker.meeting_date, "sections": [{"course_code": o.course.code,
                "course_title": o.course.title, "section_code": o.section.code} for o in sources],
                "source_schedules": [o.schedule_text for o in sources],
                "department_id": sources[0].department_id if sources else None}
        decorated.append(replace(blocker, details=details))
    blockers = tuple(sorted(decorated, key=lambda item: (item.code, item.message, item.occurrence_key)))
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
        offerings=tuple(offerings), occurrences=tuple(occurrences), meetings=tuple(meetings),
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
            faculty_scope__isnull=True,
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


def _latest_publications(*, tenant_id, campus_id, faculty_user=None):
    from django.db.models import Q
    queryset = AttendanceCutoffPublication.objects.filter(tenant_id=tenant_id, campus_id=campus_id)
    if faculty_user is not None:
        queryset = queryset.filter(Q(faculty_scope__isnull=True) | Q(faculty_scope=faculty_user))
    publications = list(
        queryset.order_by(
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
    publications = _latest_publications(tenant_id=tenant_id, campus_id=campus_id, faculty_user=faculty_user)
    publication_ids = [item.pk for item in publications]
    entries = list(
        AttendanceCutoffPublicationEntry.objects.filter(
            publication_id__in=publication_ids,
            meeting_date__range=(start_date, end_date),
        ).select_related("publication")
    )
    deduped = {}
    latest_by_day = {}
    ordered_publications = sorted(publications, key=lambda item: (item.published_at, item.pk), reverse=True)
    for entry in entries:
        # A complete newer publication replaces membership for its date scope,
        # including an explicitly empty faculty slice. Otherwise old campus
        # entries could reappear after a dated attribution correction.
        day_key = (entry.publication.academic_year_id, entry.publication.term_id, entry.meeting_date)
        if day_key not in latest_by_day:
            latest_by_day[day_key] = next((item.pk for item in ordered_publications
                if (item.academic_year_id, item.term_id) == day_key[:2]
                and item.start_date <= entry.meeting_date <= item.end_date), None)
        if entry.publication_id != latest_by_day[day_key]:
            continue
        current = deduped.get(entry.occurrence_key)
        if current is None or (entry.publication.published_at, entry.publication_id, entry.pk) > (
            current.publication.published_at,
            current.publication_id,
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
