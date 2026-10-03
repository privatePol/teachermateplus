"""Audited College updates made inside the authoritative academic operation.

Only unrecorded meetings may change. Frozen checking manifests and saved
findings/publications remain evidence of the original state.
"""
from datetime import date, datetime, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.core.services.permissions import PermissionService
from apps.rbac.models import UserPermission

from .models import (FacultyCoverage, MeetingReconciliation, ScheduleSlot,
                     ScheduleVersion, TeachingMeeting)
from .permissions import _has_department_scope
from .schedule_parsing import parse_schedule_text
from .services import _audit, _meeting_snapshot


def require_academic_authority(actor, offering, permission):
    if not PermissionService.has_permission(actor, permission, tenant_id=offering.tenant_id,
                                           campus_id=offering.campus_id) or not _has_department_scope(
            user=actor, permission_code=permission, tenant_id=offering.tenant_id,
            campus_id=offering.campus_id, department_id=offering.department_id):
        raise PermissionDenied("You cannot change this class in the selected school scope.")
    # An integrated operation does not require a second attendance role. An
    # explicit DENY still vetoes its attendance effects, including wider DENYs.
    denied = PermissionService._scoped_user_permissions(actor, offering.tenant_id, offering.campus_id)
    if denied.filter(grant_type=UserPermission.GrantType.DENY,
                     permission__code__in=("faculty_attendance.manage_coverage",
                                          "faculty_attendance.manage_schedules",
                                          "faculty_attendance.reconcile")).exists():
        raise PermissionDenied("An explicit attendance restriction prevents this class update.")


def slot_key(slot):
    return slot.weekday, slot.start_time, slot.end_time


def map_schedule_slots(old_slots, new_slots):
    """One-to-one mapping: retain exact slots before pairing changed slots.

    A removed slot never claims an unchanged sibling's destination. A day
    change retires the old dated occurrence but can retain explicit recurrence.
    """
    old = sorted({slot_key(s) for s in old_slots})
    available = sorted({slot_key(s) for s in new_slots})
    result = {}
    for key in old:
        if key in available:
            result[key] = key
            available.remove(key)
    for key in old:
        if key in result:
            continue
        target = next((s for s in available if s[0] == key[0]), None)
        if target is not None:
            result[key] = target
            available.remove(target)
    # Finish all exact/same-weekday matches before considering a day move.
    # The remainder, not the original schedule size, determines ambiguity.
    unmatched = [key for key in old if key not in result]
    if len(unmatched) == 1 and len(available) == 1:
        result[unmatched[0]] = available.pop()
    elif unmatched and available:
        raise ValidationError(
            "The schedule change has multiple unmatched class slots. Their day changes cannot be matched safely; "
            "keep identifiable class slots in the Course Offering change before saving."
        )
    for key in unmatched:
        result.setdefault(key, None)
    return result


def combined_source_slot(group, offering, meeting_date):
    """Project an explicit combination through audited academic changes only."""
    key = (group.weekday, group.start_time, group.end_time)
    for change in offering.attendance_source_changes.filter(status="RESOLVED",
            source_reference__startswith="college-course-offering:",
            effective_from__lte=meeting_date).order_by("effective_from", "pk"):
        mapping = map_schedule_slots(parse_schedule_text(change.old_schedule_text).slots,
                                     parse_schedule_text(change.new_schedule_text).slots)
        if key in mapping:
            key = mapping[key]
        if key is None:
            break
    return key


def effective_combined_slot(group, meeting_date):
    slots = {combined_source_slot(group, link.offering, meeting_date)
             for link in group.offering_links.all()}
    if len(slots) == 1:
        return next(iter(slots))
    # Conflicting academic sources are still blocked, never inferred merged.
    return group.weekday, group.start_time, group.end_time


def validate_boundary(offering, effective_at, *, old_schedule_text=None, new_schedule_text=None, room_changed=False):
    date_only = isinstance(effective_at, date) and not isinstance(effective_at, datetime)
    if effective_at is None or (not date_only and timezone.is_naive(effective_at)):
        raise ValidationError("Enter Effective from in this operation; attendance dates cannot be guessed.")
    term = offering.term
    day = effective_at if date_only else timezone.localtime(effective_at).date()
    if not term.start_date or not term.end_date or not term.start_date <= day <= term.end_date:
        raise ValidationError("Effective from must be inside the class semester's dated range.")
    # Existing verified activation dates are a lower bound, never a backfill hint.
    first = FacultyCoverage.objects.filter(offering=offering).order_by("effective_from").first()
    if date_only:
        effective_at = timezone.make_aware(datetime.combine(day, time.min))
        if first and day == timezone.localtime(first.effective_from).date():
            old = {slot_key(s) for s in parse_schedule_text(old_schedule_text or "").slots}
            new = {slot_key(s) for s in parse_schedule_text(new_schedule_text or "").slots}
            affected = old | new if room_changed else old ^ new
            floor = timezone.localtime(first.effective_from)
            if any(weekday == day.weekday() and starts < floor.time() for weekday, starts, _ in affected):
                raise ValidationError("This change affects a class before the verified attendance start. Keep earlier history unchanged.")
            effective_at = first.effective_from
    if first and effective_at < first.effective_from:
        raise ValidationError("Effective from precedes the verified attendance start. Keep earlier history unchanged.")
    return effective_at


def retired_meeting_ids():
    return MeetingReconciliation.objects.filter(status="RESOLVED",
        proposed_snapshot__college_retired=True).values_list("meeting_id", flat=True)


def _protected(meeting):
    return (hasattr(meeting, "substitution") or meeting.checking_round_rows.filter(observations__isnull=False).exists() or meeting.cutoff_publication_entries.exists()
            or meeting.closure_decisions.exists()
            or getattr(getattr(meeting, "attendance_result", None), "revision", 0) > 0)


def _invalidate_reviews(meeting, actor):
    # Old snapshots/hash remain untouched; an open browser must re-review the
    # changed current class before its next POST.
    from .models import CheckingRound
    for round_ in CheckingRound.objects.select_for_update().filter(manifest_rows__meeting=meeting):
        before = {"manifest_revision": round_.manifest_revision, "manifest_hash": round_.manifest_hash}
        round_.manifest_revision += 1
        round_.save(update_fields=["manifest_revision", "updated_at"])
        _audit(action="FACULTY_ATTENDANCE_ACADEMIC_REVIEW_INVALIDATED", entity=round_, actor=actor,
               before=before, after={"manifest_revision": round_.manifest_revision})


def _resolve(item, actor, decision, reason):
    if item.status == "RESOLVED":
        return
    item.status, item.decision = "RESOLVED", decision
    item.resolved_by, item.resolved_at = actor, timezone.now()
    item.resolution_reason = (reason or "").strip()
    item.full_clean()
    item.save()
    _audit(action="FACULTY_ATTENDANCE_ACADEMIC_SYNC_REVIEWED", entity=item, actor=actor,
           after={"decision": decision, "automatic_academic_operation": True})


def _refresh_faculty(meeting):
    links = list(meeting.offering_links.select_related("offering"))
    matches = []
    for link in links:
        rows = list(FacultyCoverage.objects.filter(offering=link.offering,
            effective_from__lte=meeting.starts_at).filter(
                Q(effective_until__isnull=True) | Q(effective_until__gt=meeting.starts_at)))
        if len(rows) != 1:
            matches = []
            break
        matches.extend(rows)
    primary = next((c for c in matches if any(l.is_primary and l.offering_id == c.offering_id for l in links)), None)
    resolved = bool(primary and len({c.faculty_user_id for c in matches}) == 1)
    meeting.coverage = primary if resolved else None
    meeting.faculty_user = primary.faculty_user if resolved else None
    meeting.unresolved_coverage = not resolved
    meeting.faculty_snapshot = ({"coverage_id": primary.pk, "faculty_user_id": primary.faculty_user_id,
        "faculty_name": primary.faculty_user.full_name, "college_academic_sync": True} if resolved else {"college_academic_sync": True})


@transaction.atomic
def sync_assignment(*, actor, assignment, effective_at, permission, reconciliation):
    offering = CourseOffering.objects.select_for_update().get(pk=assignment.offering_id)
    require_academic_authority(actor, offering, permission)
    effective_at = validate_boundary(offering, effective_at)
    if assignment.tenant_id not in (None, offering.tenant_id) or assignment.campus_id not in (None, offering.campus_id):
        raise ValidationError("Faculty assignment conflicts with its class school scope.")
    faculty = reconciliation.proposed_faculty if reconciliation.proposed_faculty_id else None
    coverages = list(FacultyCoverage.objects.select_for_update().filter(offering=offering).order_by("effective_from"))
    current = [c for c in coverages if c.effective_from <= effective_at and (c.effective_until is None or effective_at < c.effective_until)]
    if len(current) > 1:
        raise ValidationError("Overlapping faculty assignments need administrative review before saving.")
    coverage = current[0] if current else None
    if coverage and (faculty is None or coverage.faculty_user_id != faculty.pk):
        if coverage.effective_from == effective_at:
            raise ValidationError("Another assignment already begins at this exact time; choose the correct effective boundary.")
        coverage.effective_until = effective_at
        coverage.full_clean()
        coverage.save(update_fields=["effective_until", "updated_at"])
        coverage = None
    if faculty and coverage is None:
        later = next((c for c in coverages if c.effective_from > effective_at), None)
        until = later.effective_from if later else timezone.make_aware(datetime.combine(offering.term.end_date + timedelta(days=1), time.min))
        coverage = FacultyCoverage(tenant_id=offering.tenant_id, campus_id=offering.campus_id,
            department_id=offering.department_id, offering=offering, faculty_user=faculty,
            source_assignment=assignment, effective_from=effective_at, effective_until=until,
            reason=reconciliation.reason, created_by=actor)
        coverage.full_clean()
        coverage.save()
        _audit(action="FACULTY_ATTENDANCE_ACADEMIC_COVERAGE_SYNCED", entity=coverage, actor=actor,
               after={"faculty_user_id": faculty.pk, "effective_from": effective_at, "effective_until": until})
    for meeting in TeachingMeeting.objects.select_for_update().filter(
            offering_links__offering=offering, starts_at__gte=effective_at).exclude(
            pk__in=retired_meeting_ids()).distinct().order_by("pk"):
        before = _meeting_snapshot(meeting)
        protected = _protected(meeting)
        if not protected:
            _refresh_faculty(meeting)
            meeting.full_clean()
            meeting.save()
            if before != _meeting_snapshot(meeting):
                _invalidate_reviews(meeting, actor)
        item, _ = MeetingReconciliation.objects.get_or_create(meeting=meeting, source_type="COVERAGE",
            source_reference=f"college-assignment:{reconciliation.pk}", defaults={"detected_by": actor,
                "reason": reconciliation.reason, "before_snapshot": before,
                "proposed_snapshot": {"faculty_user_id": faculty.pk if faculty else None,
                                      "recorded_attendance_preserved": protected}})
        _resolve(item, actor, "KEEP_SNAPSHOT" if protected else "REVISE_FUTURE", reconciliation.reason)
        if not protected:
            # Earlier automatic/setup entries are satisfied by the same verified
            # boundary. Unrelated manual history decisions remain explicit.
            for pending in meeting.reconciliations.filter(status="PENDING", source_type="COVERAGE"):
                _resolve(pending, actor, "REVISE_FUTURE", reconciliation.reason)
    for pending in offering.attendance_coverage_reconciliations.filter(status="PENDING"):
        if pending.pk == reconciliation.pk or (not pending.prior_faculty_id
                and pending.proposed_faculty_id == reconciliation.proposed_faculty_id):
            pending.effective_at = effective_at
            pending.status, pending.resolved_by, pending.resolved_at = "RESOLVED", actor, timezone.now()
            pending.resolution_reason = reconciliation.reason
            pending.full_clean()
            pending.save()
            _audit(action="FACULTY_ATTENDANCE_ASSIGNMENT_SYNC_RESOLVED", entity=pending, actor=actor,
                   after={"effective_at": effective_at, "coverage_id": coverage.pk if coverage else None})
    return reconciliation


@transaction.atomic
def sync_schedule(*, actor, change):
    offering = CourseOffering.objects.select_for_update().get(pk=change.offering_id)
    require_academic_authority(actor, offering, "offerings.update")
    at = validate_boundary(offering, change.effective_from, old_schedule_text=change.old_schedule_text,
        new_schedule_text=change.new_schedule_text, room_changed=change.old_room != change.new_room)
    parsed = parse_schedule_text(change.new_schedule_text)
    if not parsed.confirmed:
        raise ValidationError("Enter an unambiguous class schedule in Course Offerings before saving this change.")
    versions = ScheduleVersion.objects.select_for_update().filter(offering=offering)
    for old in versions.filter(effective_from__lt=change.effective_from).filter(
            Q(effective_until__isnull=True) | Q(effective_until__gte=change.effective_from)):
        old.effective_until = change.effective_from - timedelta(days=1)
        old.save(update_fields=["effective_until", "updated_at"])
    if versions.filter(effective_from__gt=change.effective_from).exists():
        raise ValidationError("A later class schedule already exists. Keep its effective dates or correct the academic change date.")
    from .daily_encoding import _source_fingerprint
    version = ScheduleVersion(tenant_id=offering.tenant_id, campus_id=offering.campus_id,
        department_id=offering.department_id, offering=offering,
        version_number=(versions.aggregate(n=Max("version_number"))["n"] or 0) + 1,
        original_text=change.new_schedule_text, source_kind="COURSE_OFFERING",
        source_fingerprint=_source_fingerprint(schedule_text=change.new_schedule_text, room=change.new_room,
            effective_from=change.effective_from, effective_until=offering.term.end_date),
        interpretation_status="CONFIRMED", effective_from=change.effective_from,
        effective_until=offering.term.end_date, correction_reason=change.reason, created_by=actor)
    version.full_clean()
    version.save()
    for sequence, slot in enumerate(parsed.slots, 1):
        row = ScheduleSlot(schedule_version=version, sequence=sequence, weekday=slot.weekday,
            start_time=slot.start_time, end_time=slot.end_time, room=change.new_room, room_text=change.new_room)
        row.full_clean()
        row.save()
    for meeting in TeachingMeeting.objects.select_for_update().filter(
            offering_links__offering=offering, meeting_date__gte=change.effective_from).exclude(
            pk__in=retired_meeting_ids()).distinct().order_by("pk"):
        item = meeting.reconciliations.get(source_reference=change.source_reference)
        if _protected(meeting) or meeting.starts_at < at:
            item.proposed_snapshot = {**item.proposed_snapshot, "college_preserved": True}
            item.save(update_fields=["proposed_snapshot", "updated_at"])
            _resolve(item, actor, "KEEP_SNAPSHOT", change.reason)
            continue
        linked = list(meeting.offering_links.select_related("offering"))
        if len(linked) > 1:
            # A single academic edit cannot silently split an explicit combination.
            if any(l.offering_id != offering.pk and
                   (l.offering.schedule_text != change.new_schedule_text or (l.offering.room or "") != change.new_room)
                   for l in linked):
                # Source edits can be saved sequentially. Do not guess a split
                # or force an attendance recovery step between academic saves.
                meeting.schedule_snapshot = {**meeting.schedule_snapshot, "college_source_waiting": True}
                meeting.save(update_fields=["schedule_snapshot", "updated_at"])
                _invalidate_reviews(meeting, actor)
                _resolve(item, actor, "KEEP_SNAPSHOT", change.reason)
                continue
        meeting_version = version
        primary = next((link.offering for link in linked if link.is_primary), offering)
        if primary.pk != offering.pk:
            meeting_version = ScheduleVersion.objects.filter(offering=primary, interpretation_status="CONFIRMED",
                original_text=change.new_schedule_text, effective_from__lte=meeting.meeting_date).filter(
                Q(effective_until__isnull=True) | Q(effective_until__gte=meeting.meeting_date)
            ).order_by("-version_number", "-pk").first()
            if meeting_version is None:
                raise ValidationError("Save the shared schedule on the primary section before encoding this combined class.")
        slots = list(meeting_version.slots.all())
        prior = parse_schedule_text(change.old_schedule_text)
        old_key = (meeting.meeting_date.weekday(), timezone.localtime(meeting.starts_at).time(),
                   timezone.localtime(meeting.ends_at).time())
        target = map_schedule_slots(prior.slots, slots).get(old_key)
        new = next((s for s in slots if slot_key(s) == target and s.weekday == meeting.meeting_date.weekday()), None)
        if new is None:
            item.proposed_snapshot = {**item.proposed_snapshot, "college_retired": True}
            item.save(update_fields=["proposed_snapshot", "updated_at"])
        else:
            meeting.schedule_slot = new
            meeting.starts_at = timezone.make_aware(datetime.combine(meeting.meeting_date, new.start_time))
            meeting.ends_at = timezone.make_aware(datetime.combine(meeting.meeting_date, new.end_time))
            meeting.scheduled_minutes = int((meeting.ends_at - meeting.starts_at).total_seconds() // 60)
            meeting.schedule_snapshot = {"schedule_version_id": meeting_version.pk, "version_number": meeting_version.version_number,
                "original_text": meeting_version.original_text, "weekday": new.weekday,
                "start_time": new.start_time.isoformat(), "end_time": new.end_time.isoformat()}
            meeting.location_snapshot = {"room_text": new.room_text, "room": new.room, "building": "", "floor": ""}
            _refresh_faculty(meeting)
            meeting.full_clean()
            meeting.save()
        _invalidate_reviews(meeting, actor)
        _resolve(item, actor, "REVISE_FUTURE", change.reason)
    change.status, change.resolved_by, change.resolved_at = "RESOLVED", actor, timezone.now()
    change.resolution_reason = change.reason
    change.full_clean()
    change.save()
    _audit(action="FACULTY_ATTENDANCE_ACADEMIC_SCHEDULE_SYNCED", entity=change, actor=actor,
           after={"effective_from": change.effective_from, "schedule_version_id": version.pk})
    from .models import RecurringCombinedClass
    for group in RecurringCombinedClass.objects.filter(offering_links__offering=offering,
            tenant_id=offering.tenant_id, campus_id=offering.campus_id,
            academic_year_id=offering.academic_year_id, term_id=offering.term_id).distinct():
        keys = {combined_source_slot(group, link.offering, change.effective_from)
                for link in group.offering_links.select_related("offering")}
        if len(keys) == 1:
            _audit(action="FACULTY_ATTENDANCE_COLLEGE_COMBINED_SYNC", entity=group, actor=actor,
                after={"effective_from": change.effective_from, "source_change_id": change.pk,
                       "effective_slot": next(iter(keys)), "original_definition_preserved": True})
    return change
