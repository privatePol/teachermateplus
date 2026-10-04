"""Complete faculty slices built from one campus evidence scan.

Assignments identify possible reviewers, never dated attendance attribution.
Missing dated evidence is campus-wide unless a finite candidate set is proved.
"""
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, FacultyAssignment, Term
from apps.core.services.audit import AuditService
from apps.rbac.models import UserRole
from apps.tenants.models import Campus, Department

from .cutoffs import _review_cutoff, _fingerprint, _publication_entry_values
from .models import AttendanceCutoffPublication, AttendanceCutoffPublicationEntry, DTRAdjustment, FacultyCoverage
from .notice_locking import lock_notice_campus
from .observations import StaleAttendanceReview, resolve_attendance_faculty
from .permissions import PUBLISH_FACULTY_PERMISSION, require_attendance_permission


@dataclass(frozen=True)
class FacultyCutoffSlice:
    faculty: object
    records: tuple
    blockers: tuple
    departments: frozenset
    fingerprint: str
    scope_snapshot: dict
    requires_dtr: bool

    @property
    def ready(self):
        return self.requires_dtr and not self.blockers


@dataclass(frozen=True)
class FacultyCutoffReview:
    slices: tuple
    unattributed: tuple
    scope: dict


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _departments(actor, tenant_id, campus_id, permission_code):
    if not Campus.objects.filter(pk=campus_id, tenant_id=tenant_id).exists():
        raise PermissionDenied("Select a campus in this tenant.")
    allowed = set()
    for department_id in Department.objects.filter(tenant_id=tenant_id, campus_id=campus_id).values_list("pk", flat=True):
        try:
            require_attendance_permission(user=actor, permission_code=permission_code,
                tenant_id=tenant_id, campus_id=campus_id, department_id=department_id)
        except PermissionDenied:
            continue
        allowed.add(department_id)
    if not allowed:
        raise PermissionDenied("Faculty cutoff authority is required in this campus.")
    return allowed


def review_faculty_cutoff(*, actor, tenant_id, campus_id, academic_year, term, start_date, end_date,
                          lock=False, permission_code=PUBLISH_FACULTY_PERMISSION, department_ids=None):
    if lock:
        academic_year = AcademicYear.objects.select_for_update().get(pk=academic_year.pk, tenant_id=tenant_id)
        term = Term.objects.select_for_update().get(pk=term.pk, tenant_id=tenant_id, academic_year=academic_year)
    allowed = _departments(actor, tenant_id, campus_id, permission_code)
    if department_ids is not None:
        allowed.intersection_update(department_ids)
        if not allowed:
            raise PermissionDenied("No authorized departments remain in the selected scope.")
    if (not term.start_date or not term.end_date or not term.start_date <= start_date <= end_date <= term.end_date):
        raise ValidationError("Choose cutoff dates inside the selected semester.")
    scope = dict(tenant_id=tenant_id, campus_id=campus_id, academic_year=academic_year,
                 term=term, start_date=start_date, end_date=end_date)
    evidence = _review_cutoff(actor=actor, **scope, lock=lock, faculty_review=True)
    offering_map = {o.pk: o for o in evidence.offerings}
    meeting_map = {m.pk: m for m in evidence.meetings}
    occurrence_map = {o.occurrence_key: o for o in evidence.occurrences}
    by_offering = defaultdict(list)
    roster = defaultdict(set)
    covers = FacultyCoverage.objects.filter(offering_id__in=offering_map).select_related("faculty_user")
    if lock:
        covers = covers.select_for_update()
    for coverage in covers:
        by_offering[coverage.offering_id].append(coverage)
        roster[coverage.faculty_user_id].add(offering_map[coverage.offering_id].department_id)
    assignments = FacultyAssignment.objects.filter(offering_id__in=offering_map, is_active=True)
    for assignment in assignments.select_for_update() if lock else assignments:
        roster[assignment.faculty_user_id].add(offering_map[assignment.offering_id].department_id)
    adjustments = DTRAdjustment.objects.filter(tenant_id=tenant_id, campus_id=campus_id,
        academic_year=academic_year, term=term, start_date=start_date, end_date=end_date).order_by("entry_key", "-revision", "-pk")
    adjustments = list(adjustments.select_for_update() if lock else adjustments)
    current_adjustments = {}
    for adjustment in adjustments:
        current_adjustments.setdefault(adjustment.entry_key, adjustment)
        roster[adjustment.faculty_user_id].add(adjustment.department_id)
    # AC faculty can publish an explicitly empty teaching slice before office
    # hours are recorded. A faculty with neither teaching nor AC/admin is empty.
    ac_ids = set()
    roles = UserRole.objects.filter(is_active=True, role__is_active=True,
        role__code__in=("AC", "AREA_CHAIR", "AREA_CHAIRPERSON"))
    roles = roles.filter(Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
        Q(campus_id=campus_id) | Q(campus_id__isnull=True)).select_related("user")
    for role in roles.select_for_update() if lock else roles:
        departments = {role.department_id or role.user.default_department_id} - {None}
        if not departments:
            department_rows = Department.objects.filter(tenant_id=tenant_id, campus_id=campus_id)
            departments = set((department_rows.select_for_update() if lock else department_rows).values_list("pk", flat=True))
        ac_ids.add(role.user_id)
        roster[role.user_id].update(departments)
    for record in evidence.records:
        faculty_id = record.result.faculty_user_id if record.result else record.closure.faculty_user_id
        roster[faculty_id].update(offering_map[l.offering_id].department_id
            for l in record.meeting.offering_links.all() if l.offering_id in offering_map)
    for meeting in evidence.meetings:
        faculty, _ = resolve_attendance_faculty(meeting, getattr(meeting, "attendance_result", None))
        if faculty:
            roster[faculty.pk].add(meeting.department_id)
    historical_teachers = set()
    historical_entries = AttendanceCutoffPublicationEntry.objects.filter(
        publication__tenant_id=tenant_id, publication__campus_id=campus_id,
        publication__academic_year=academic_year, publication__term=term,
        publication__start_date=start_date, publication__end_date=end_date).select_related("meeting")
    for entry in historical_entries.select_for_update() if lock else historical_entries:
        if entry.faculty_user_id:
            historical_teachers.add(entry.faculty_user_id)
            roster[entry.faculty_user_id].add(entry.meeting.department_id)

    def at_start(offering_ids, at):
        ids = set()
        complete = bool(offering_ids)
        for offering_id in offering_ids:
            rows = [c for c in by_offering[offering_id] if c.effective_from <= at
                    and (c.effective_until is None or at < c.effective_until)]
            complete = complete and bool(rows)
            ids.update(c.faculty_user_id for c in rows)
        return ids, complete

    def throughout(offering_ids):
        begin = timezone.make_aware(datetime.combine(start_date, time.min))
        finish = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
        ids, complete = set(), bool(offering_ids)
        for offering_id in offering_ids:
            cursor = begin
            for c in sorted(by_offering[offering_id], key=lambda row: row.effective_from):
                until = c.effective_until or finish
                if until <= begin or c.effective_from >= finish:
                    continue
                ids.add(c.faculty_user_id)
                if c.effective_from > cursor:
                    complete = False
                cursor = max(cursor, until)
            complete = complete and cursor >= finish
        return ids, complete

    per_faculty = defaultdict(list)
    unattributed = []
    global_blockers = []
    for blocker in evidence.blockers:
        meeting = meeting_map.get(blocker.meeting_id)
        occurrence = occurrence_map.get(blocker.occurrence_key)
        offering_ids = tuple(l.offering_id for l in meeting.offering_links.all()) if meeting else (
            tuple(o.pk for o in occurrence.linked_offerings) if occurrence else blocker.offering_ids)
        sources = [offering_map[pk] for pk in offering_ids if pk in offering_map]
        departments = {o.department_id for o in sources}
        if meeting:
            ids, complete = at_start(offering_ids, meeting.starts_at)
            faculty, _ = resolve_attendance_faculty(meeting, getattr(meeting, "attendance_result", None))
            dated, _ = resolve_attendance_faculty(meeting)
            if faculty:
                ids.add(faculty.pk)
            if dated:
                ids.add(dated.pk)
            # Saved/explicit dated attribution proves a known-faculty blocker;
            # include conflicting coverage candidates rather than guessing one.
            complete = complete or faculty is not None or dated is not None
            when, start, end = meeting.meeting_date, timezone.localtime(meeting.starts_at).time(), timezone.localtime(meeting.ends_at).time()
        elif occurrence:
            ids, complete = at_start(offering_ids, timezone.make_aware(datetime.combine(occurrence.meeting_date, occurrence.start_time)))
            when, start, end = occurrence.meeting_date, occurrence.start_time, occurrence.end_time
        else:
            ids, complete = throughout(offering_ids)
            when, start, end = blocker.meeting_date or start_date, None, None
        details = {"date": when, "start": start, "end": end, "departments": sorted(departments),
            "sections": [{"course_code": o.course.code, "course_title": o.course.title, "section_code": o.section.code} for o in sources],
            "candidate_ids": sorted(ids), "scope_unproven": not complete,
            "source_schedules": [o.schedule_text for o in sources]}
        decorated = replace(blocker, details=details)
        for faculty_id in ids:
            roster[faculty_id].update(departments)
        if not complete:
            global_blockers.append(decorated)
        else:
            for faculty_id in ids:
                per_faculty[faculty_id].append(decorated)
        if not complete or len(ids) != 1:
            if departments and departments <= allowed:
                unattributed.append(decorated)
    records = defaultdict(list)
    for record in evidence.records:
        faculty_id = record.result.faculty_user_id if record.result else record.closure.faculty_user_id
        records[faculty_id].append(record)
    slices = []
    faculty_rows = User.objects.filter(pk__in=roster).order_by("last_name", "first_name", "pk")
    for faculty in faculty_rows.select_for_update() if lock else faculty_rows:
        departments = roster[faculty.pk]
        if not departments or not departments <= allowed:
            continue  # A partial department grant cannot publish a partial faculty.
        blockers = tuple(per_faculty[faculty.pk] + global_blockers)
        snapshot = {"faculty_user_id": faculty.pk, "departments": sorted(departments),
            "occurrences": [{"key": r.occurrence_key, "meeting_id": r.meeting.pk,
                "result_revision": r.result.revision if r.result else None,
                "closure_revision": r.closure.revision if r.closure else None} for r in records[faculty.pk]],
            "teaching_complete": not blockers,
            "revises_teaching_to_empty": faculty.pk in historical_teachers and not records[faculty.pk],
            "ac_role": faculty.pk in ac_ids}
        fingerprint = _digest({"scope": (tenant_id, campus_id, academic_year.pk, term.pk, start_date, end_date),
            "snapshot": snapshot, "findings": _fingerprint(records=records[faculty.pk], blockers=blockers),
            "blocker_scope": [b.details for b in blockers]})
        requires_dtr = bool(records[faculty.pk] or blockers or faculty.pk in ac_ids or faculty.pk in historical_teachers or any(
            a.faculty_user_id == faculty.pk and a.kind == "ADMIN" and a.hours > 0 for a in current_adjustments.values()))
        # Never expose another department's unresolved metadata through a row.
        visible_blockers = tuple(b if set(b.details["departments"]) <= allowed else replace(b,
            message="Unattributed evidence outside your department scope prevents proving this faculty's complete cutoff.",
            offering_ids=(), meeting_id=None, occurrence_key="", details={"scope_unproven": True}) for b in blockers)
        slices.append(FacultyCutoffSlice(faculty, tuple(records[faculty.pk]), visible_blockers,
            frozenset(departments), fingerprint, snapshot, requires_dtr))
    return FacultyCutoffReview(tuple(slices), tuple(unattributed), scope)


def _publication_scope(scope):
    return dict(tenant_id=scope["tenant_id"], campus_id=scope["campus_id"],
        academic_year=scope["academic_year"], term=scope["term"],
        start_date=scope["start_date"], end_date=scope["end_date"])


@transaction.atomic
def publish_faculty_cutoffs(*, actor, faculty_ids, expected_fingerprints, submission_key, publication_reason="", **scope):
    if not isinstance(submission_key, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,40}", submission_key):
        raise ValidationError("A faculty publication request identity of at most 40 characters is required.")
    selected = sorted(set(int(pk) for pk in faculty_ids))
    if not selected:
        raise ValidationError("Select ready faculty to publish.")
    _departments(actor, scope["tenant_id"], scope["campus_id"], PUBLISH_FACULTY_PERMISSION)
    lock_notice_campus(scope["campus_id"])
    review = review_faculty_cutoff(actor=actor, lock=True, **scope)
    slices = {row.faculty.pk: row for row in review.slices}
    reason = (publication_reason or "").strip()
    request_digest = _digest({"actor": actor.pk, "faculty_ids": selected,
        "fingerprints": {str(pk): expected_fingerprints.get(str(pk)) for pk in selected},
        "scope": (scope["tenant_id"], scope["campus_id"], scope["academic_year"].pk,
                  scope["term"].pk, scope["start_date"], scope["end_date"]), "reason": reason})
    prior_requests = list(AttendanceCutoffPublication.objects.select_for_update().filter(
        submission_key__startswith=f"{submission_key}:"))
    if any(p.scope_snapshot.get("request_digest") != request_digest for p in prior_requests):
        raise StaleAttendanceReview("This retry identity belongs to a different selection, note or actor.")
    # Validate the entire selection before any writes. The request is atomic.
    requests = []
    for faculty_id in selected:
        row = slices.get(faculty_id)
        if row is None:
            raise PermissionDenied("Selected faculty is outside your complete authorized cutoff scope.")
        expected = expected_fingerprints.get(str(faculty_id))
        key = f"{submission_key}:{faculty_id}"
        existing = AttendanceCutoffPublication.objects.select_for_update().filter(submission_key=key).first()
        if existing:
            if (existing.faculty_scope_id != faculty_id or any(getattr(existing, k + "_id", None) != v.pk
                for k, v in (("academic_year", scope["academic_year"]), ("term", scope["term"])))
                or existing.tenant_id != scope["tenant_id"] or existing.campus_id != scope["campus_id"]
                or existing.start_date != scope["start_date"] or existing.end_date != scope["end_date"]
                or existing.review_fingerprint != expected):
                raise StaleAttendanceReview("This retry identity belongs to a different faculty review.")
        if row.fingerprint != expected or not row.ready:
            raise StaleAttendanceReview("Selected faculty evidence changed or is blocked. Reload before publishing.")
        requests.append((row, key, existing))
    publications = []
    for row, key, existing in requests:
        previous = AttendanceCutoffPublication.objects.select_for_update().filter(
            **_publication_scope(scope), faculty_scope=row.faculty).order_by("-version", "-pk").first()
        if existing:
            if previous.pk != existing.pk:
                raise StaleAttendanceReview("A later faculty publication exists. Reload instead of resending this request.")
            publications.append(existing)
            continue
        if previous and previous.review_fingerprint == row.fingerprint:
            publications.append(previous)  # Resume without duplicating a completed slice.
            continue
        publication = AttendanceCutoffPublication(**_publication_scope(scope), faculty_scope=row.faculty,
            scope_key=f"FACULTY:{row.faculty.pk}", scope_snapshot={**row.scope_snapshot, "request_digest": request_digest},
            lineage_key=previous.lineage_key if previous else AttendanceCutoffPublication._meta.get_field("lineage_key").get_default(),
            version=previous.version + 1 if previous else 1, supersedes=previous,
            review_fingerprint=row.fingerprint, submission_key=key, publication_reason=reason,
            published_by=actor, published_at=timezone.now())
        publication.full_clean()
        publication.save()
        entries = [AttendanceCutoffPublicationEntry(publication=publication, **_publication_entry_values(r)) for r in row.records]
        for entry in entries:
            entry.full_clean()
        AttendanceCutoffPublicationEntry.objects.bulk_create(entries)
        AuditService.log_event(action="FACULTY_ATTENDANCE_FACULTY_CUTOFF_PUBLISHED", portal="ADMIN",
            entity_type="AttendanceCutoffPublication", entity_id=publication.pk, actor=actor,
            tenant=publication.tenant_id, campus=publication.campus_id,
            after_data={"faculty_user_id": row.faculty.pk, "version": publication.version,
                "entry_count": len(entries), "request_identity": submission_key, "scope": row.scope_snapshot})
        publications.append(publication)
    return publications
