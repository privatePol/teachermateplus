from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.academics.models import CourseOffering

from .models import (
    CheckingRound,
    OfferingAttendanceSourceChange,
    RecurringCombinedClass,
    ScheduleSlot,
    ScheduleVersion,
    TeachingMeeting,
)
from .observations import CheckingRoundService
from .permissions import ENCODE_PERMISSION, require_attendance_permission
from .schedule_parsing import parse_schedule_text
from .services import MeetingService


@dataclass(frozen=True)
class DailyOccurrence:
    occurrence_key: str
    primary_offering: CourseOffering
    linked_offerings: tuple[CourseOffering, ...]
    meeting_date: date
    start_time: object
    end_time: object
    source_schedule_text: str
    source_room: str
    source_effective_from: date
    source_effective_until: date | None
    combined_definition_id: int | None = None
    dated_meeting_id: int | None = None
    historical_meeting_id: int | None = None

    @property
    def is_combined(self):
        return (self.combined_definition_id is not None or self.dated_meeting_id is not None
                or (self.historical_meeting_id is not None and len(self.linked_offerings) > 1))


@dataclass(frozen=True)
class DailyIssue:
    code: str
    message: str
    offering: CourseOffering | None = None
    linked_offerings: tuple[CourseOffering, ...] = ()
    meeting_date: date | None = None
    occurrence_key: str = ""
    action: str = ""

    @property
    def affected_offerings(self):
        if self.linked_offerings:
            return self.linked_offerings
        return (self.offering,) if self.offering is not None else ()

    @property
    def class_label(self):
        return " / ".join(
            f"{item.course.code} · {item.section.code}"
            for item in self.affected_offerings
        ) or "Selected attendance scope"

    @property
    def contextual_message(self):
        parts = [self.class_label]
        if self.meeting_date:
            parts.append(self.meeting_date.isoformat())
        parts.append(self.message)
        return ": ".join(parts)


class DailyMaterializationBlock(ValidationError):
    def __init__(self, message, *, code="MATERIALIZATION_BLOCKED", action="corrections"):
        self.daily_code = code
        self.daily_action = action
        super().__init__(message)


def _normalized_text(value):
    return " ".join(str(value or "").split()).casefold()


def readable_validation_message(exc):
    if isinstance(exc, ValidationError):
        values = exc.messages
    else:
        values = [str(exc)]
    readable = []
    for value in values:
        normalized = " ".join(str(value).split()).strip()
        if normalized and normalized not in readable:
            readable.append(normalized)
    return "; ".join(readable) or "This record needs review before daily encoding."


def _issue_from_exception(*, exc, occurrence):
    return DailyIssue(
        code=getattr(exc, "daily_code", "MATERIALIZATION_BLOCKED"),
        offering=occurrence.primary_offering,
        linked_offerings=occurrence.linked_offerings,
        meeting_date=occurrence.meeting_date,
        occurrence_key=occurrence.occurrence_key,
        message=readable_validation_message(exc),
        action=getattr(exc, "daily_action", "corrections"),
    )


def deduplicate_daily_issues(issues):
    unique = []
    seen = set()
    for issue in issues:
        offering_ids = tuple(sorted(item.pk for item in issue.affected_offerings))
        occurrence_identity = issue.occurrence_key or (offering_ids, issue.meeting_date)
        key = (issue.code, occurrence_identity, _normalized_text(issue.message))
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def _date_range(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _source_fingerprint(*, schedule_text, room, effective_from, effective_until):
    value = {
        "schedule_text": schedule_text or "",
        "room": room or "",
        "effective_from": effective_from.isoformat(),
        "effective_until": effective_until.isoformat() if effective_until else None,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _source_for_date(*, offering, term, meeting_date, changes):
    """Return the recorded Course Offering source snapshot applicable on one date."""
    resolved = [item for item in changes if item.status == OfferingAttendanceSourceChange.Status.RESOLVED]
    if not resolved:
        return offering.schedule_text or "", offering.room or "", term.start_date, term.end_date
    resolved.sort(key=lambda item: (item.effective_from, item.pk))
    first = resolved[0]
    if meeting_date < first.effective_from:
        return first.old_schedule_text, first.old_room, term.start_date, first.effective_from - timedelta(days=1)
    applicable = [item for item in resolved if item.effective_from <= meeting_date][-1]
    later = next((item for item in resolved if item.effective_from > applicable.effective_from), None)
    return (
        applicable.new_schedule_text,
        applicable.new_room,
        applicable.effective_from,
        later.effective_from - timedelta(days=1) if later else term.end_date,
    )


def _combined_groups(*, tenant_id, campus_id, academic_year_id, term_id, offering_ids):
    groups = RecurringCombinedClass.objects.filter(
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year_id=academic_year_id,
        term_id=term_id,
    ).prefetch_related("offering_links__offering__course", "offering_links__offering__section")
    return [
        group
        for group in groups
        if {link.offering_id for link in group.offering_links.all()} <= offering_ids
    ]


def _dated_combined_meetings(*, offerings, start_date, end_date):
    """Return candidate dated shared meetings without widening the selected offering scope."""
    if not offerings:
        return []
    first = offerings[0]
    offering_ids = {item.pk for item in offerings}
    from .college_sync import retired_meeting_ids
    return list(
        TeachingMeeting.objects.filter(
            tenant_id=first.tenant_id,
            campus_id=first.campus_id,
            meeting_date__range=(start_date, end_date),
            offering_links__offering_id__in=offering_ids,
        )
        .exclude(pk__in=retired_meeting_ids()).distinct()
        .select_related("schedule_slot")
        .prefetch_related("offering_links")
        .order_by("meeting_date", "starts_at", "pk")
    )


def _dated_combination_issue(*, meeting, primary, linked, code, message):
    return DailyIssue(
        code=code,
        offering=primary,
        linked_offerings=linked,
        meeting_date=meeting.meeting_date,
        occurrence_key=f"dated-meeting:{meeting.pk}",
        message=message,
        action="combined",
    )


def _recurring_definition_conflicts_with_dated(*, group, meeting_date, linked_ids, start_time, end_time):
    from .college_sync import effective_combined_slot
    slot = effective_combined_slot(group, meeting_date)
    if (
        slot is None or meeting_date.weekday() != slot[0]
        or meeting_date < group.effective_from
        or (group.effective_until and meeting_date > group.effective_until)
    ):
        return False
    group_ids = {link.offering_id for link in group.offering_links.all()}
    if not group_ids.intersection(linked_ids):
        return False
    return (
        group_ids != linked_ids
        or slot[1] != start_time
        or slot[2] != end_time
    )


def expected_daily_occurrences(*, offerings, term, start_date, end_date, combined_classes=()):
    """Read-only expected-occurrence builder shared by daily encoding and cutoff review."""
    start_date = max(start_date, term.start_date)
    end_date = min(end_date, term.end_date)
    if end_date < start_date:
        return [], []
    offerings = list(offerings)
    issues = []
    ordinary = {}
    for offering in offerings:
        changes = list(
            offering.attendance_source_changes.all().order_by("effective_from", "pk")
        )
        pending = [item for item in changes if item.status == OfferingAttendanceSourceChange.Status.PENDING]
        if pending:
            issues.append(
                DailyIssue(
                    code="SOURCE_CHANGE_PENDING",
                    offering=offering,
                    linked_offerings=(offering,),
                    meeting_date=start_date if start_date == end_date else None,
                    message="A Course Offering schedule or room change still needs attendance reconciliation.",
                    action="reconciliation",
                )
            )
            continue
        for meeting_date in _date_range(start_date, end_date):
            source_text, source_room, effective_from, effective_until = _source_for_date(
                offering=offering,
                term=term,
                meeting_date=meeting_date,
                changes=changes,
            )
            parsed = parse_schedule_text(source_text)
            if not parsed.confirmed:
                issues.append(
                    DailyIssue(
                        code="SCHEDULE_AMBIGUOUS",
                        offering=offering,
                        linked_offerings=(offering,),
                        meeting_date=meeting_date,
                        message=f"{parsed.reason} Correct the Course Offering schedule before encoding.",
                        action="corrections",
                    )
                )
                continue
            for slot in parsed.slots:
                if slot.weekday != meeting_date.weekday():
                    continue
                key = f"offering:{offering.pk}:{meeting_date.isoformat()}:{slot.start_time:%H%M}-{slot.end_time:%H%M}"
                ordinary[(offering.pk, meeting_date, slot.start_time, slot.end_time)] = DailyOccurrence(
                    occurrence_key=key,
                    primary_offering=offering,
                    linked_offerings=(offering,),
                    meeting_date=meeting_date,
                    start_time=slot.start_time,
                    end_time=slot.end_time,
                    source_schedule_text=source_text,
                    source_room=source_room,
                    source_effective_from=effective_from,
                    source_effective_until=effective_until,
                )

    # A recorded class keeps its saved time/room on a later academic edit.
    # Replace only that changed source slot, never another legitimate same-day slot.
    preserved = TeachingMeeting.objects.filter(offering_links__offering__in=offerings,
        meeting_date__range=(start_date, end_date), reconciliations__status="RESOLVED",
        reconciliations__proposed_snapshot__college_preserved=True).distinct()
    historical_consumed = {}
    for meeting in preserved.select_related("schedule_slot__schedule_version").prefetch_related("offering_links__offering"):
        links = list(meeting.offering_links.all())
        if not {l.offering_id for l in links} <= {o.pk for o in offerings}:
            continue
        primary = next(l.offering for l in links if l.is_primary)
        for review in meeting.reconciliations.filter(proposed_snapshot__college_preserved=True):
            old = parse_schedule_text(review.before_snapshot.get("schedule_text", ""))
            new = parse_schedule_text(review.proposed_snapshot.get("schedule_text", ""))
            from .college_sync import map_schedule_slots
            old_key = (meeting.meeting_date.weekday(), _local_meeting_time(meeting.starts_at),
                       _local_meeting_time(meeting.ends_at))
            mapping = map_schedule_slots(old.slots, new.slots)
            if old_key not in mapping:
                saved_source = parse_schedule_text(review.before_snapshot.get("meeting_schedule", {}).get("original_text", ""))
                mapping = map_schedule_slots(saved_source.slots, new.slots)
            target = mapping.get(old_key)
            if target and target[0] == meeting.meeting_date.weekday():
                for link in links:
                    key = (link.offering_id, meeting.meeting_date, target[1], target[2])
                    ordinary.pop(key, None)
                    if len(links) > 1:
                        historical_consumed[key] = meeting.pk
        ordinary[("historical", meeting.pk)] = DailyOccurrence(
            occurrence_key=meeting.occurrence_key or f"historical:{meeting.pk}", primary_offering=primary,
            linked_offerings=tuple(l.offering for l in links), meeting_date=meeting.meeting_date,
            start_time=_local_meeting_time(meeting.starts_at), end_time=_local_meeting_time(meeting.ends_at),
            source_schedule_text=meeting.schedule_snapshot.get("original_text", ""),
            source_room=meeting.location_snapshot.get("room_text", ""),
            source_effective_from=meeting.schedule_slot.schedule_version.effective_from,
            source_effective_until=meeting.schedule_slot.schedule_version.effective_until,
            historical_meeting_id=meeting.pk)

    offering_map = {item.pk: item for item in offerings}
    dated_candidates = []
    blocked_dated_keys = set()
    for meeting in _dated_combined_meetings(
        offerings=offerings,
        start_date=start_date,
        end_date=end_date,
    ):
        if any(o.historical_meeting_id == meeting.pk for o in ordinary.values()):
            continue
        links = list(meeting.offering_links.all())
        linked_ids = {link.offering_id for link in links}
        if len(links) < 2 or len(linked_ids) != len(links) or not linked_ids <= set(offering_map):
            # A partial selected scope must never expose or fold another offering's dated meeting.
            continue
        linked = tuple(offering_map[link.offering_id] for link in links)
        primary_link = next((link for link in links if link.is_primary), links[0])
        primary = offering_map[primary_link.offering_id]
        start_time = _local_meeting_time(meeting.starts_at)
        end_time = _local_meeting_time(meeting.ends_at)
        keys = [(item.pk, meeting.meeting_date, start_time, end_time) for item in linked]
        source_rows = [ordinary.get(key) for key in keys]
        if any(item is None for item in source_rows):
            blocked_dated_keys.update(keys)
            issues.append(
                _dated_combination_issue(
                    meeting=meeting,
                    primary=primary,
                    linked=linked,
                    code="DATED_COMBINED_SOURCE_CONFLICT",
                    message=(
                        "This explicit dated combined meeting does not match every linked Course Offering "
                        "date/time source. Review the recorded schedule evidence before encoding."
                    ),
                )
            )
            continue
        primary_source = next(item for item in source_rows if item.primary_offering.pk == primary.pk)
        if len({_normalized_text(item.source_room) for item in source_rows}) != 1:
            blocked_dated_keys.update(keys)
            issues.append(
                _dated_combination_issue(
                    meeting=meeting,
                    primary=primary,
                    linked=linked,
                    code="DATED_COMBINED_ROOM_CONFLICT",
                    message=(
                        "This explicit dated combined meeting has conflicting linked Course Offering room evidence. "
                        "Review the source before encoding."
                    ),
                )
            )
            continue
        occurrence = DailyOccurrence(
            occurrence_key=f"dated-meeting:{meeting.pk}",
            primary_offering=primary,
            linked_offerings=linked,
            meeting_date=meeting.meeting_date,
            start_time=start_time,
            end_time=end_time,
            source_schedule_text=primary_source.source_schedule_text,
            source_room=primary_source.source_room,
            source_effective_from=primary_source.source_effective_from,
            source_effective_until=primary_source.source_effective_until,
            dated_meeting_id=meeting.pk,
        )
        try:
            slot = _existing_source_slot_for_occurrence(occurrence=occurrence)
        except (ValidationError, PermissionDenied) as exc:
            blocked_dated_keys.update(keys)
            issues.append(
                _dated_combination_issue(
                    meeting=meeting,
                    primary=primary,
                    linked=linked,
                    code="DATED_COMBINED_SOURCE_CONFLICT",
                    message=readable_validation_message(exc),
                )
            )
            continue
        if (
            slot is None
            or not _meeting_matches_occurrence(meeting, occurrence, schedule_slot_id=slot.pk)
            or not _meeting_location_matches_occurrence(meeting, occurrence)
        ):
            blocked_dated_keys.update(keys)
            issues.append(
                _dated_combination_issue(
                    meeting=meeting,
                    primary=primary,
                    linked=linked,
                    code="DATED_COMBINED_SOURCE_CONFLICT",
                    message=(
                        "This explicit dated combined meeting conflicts with the recorded Course Offering "
                        "schedule or room source. Resolve the history before encoding."
                    ),
                )
            )
            continue
        dated_candidates.append(
            {
                "meeting": meeting,
                "primary": primary,
                "linked": linked,
                "linked_ids": linked_ids,
                "keys": keys,
                "occurrence": occurrence,
            }
        )

    claims_by_key = {}
    for candidate in dated_candidates:
        for key in candidate["keys"]:
            claims_by_key.setdefault(key, []).append(candidate)
    conflicting_dated_ids = {
        candidate["meeting"].pk
        for candidates in claims_by_key.values()
        if len(candidates) > 1
        for candidate in candidates
    }
    for candidate in dated_candidates:
        for group in combined_classes:
            if not _recurring_definition_conflicts_with_dated(
                group=group,
                meeting_date=candidate["meeting"].meeting_date,
                linked_ids=candidate["linked_ids"],
                start_time=candidate["occurrence"].start_time,
                end_time=candidate["occurrence"].end_time,
            ):
                continue
            conflicting_dated_ids.add(candidate["meeting"].pk)
            from .college_sync import effective_combined_slot
            slot = effective_combined_slot(group, candidate["meeting"].meeting_date)
            blocked_dated_keys.update(
                (link.offering_id, candidate["meeting"].meeting_date, slot[1], slot[2])
                for link in group.offering_links.all()
            )
    dated_consumed = {}
    for candidate in dated_candidates:
        meeting = candidate["meeting"]
        if meeting.pk in conflicting_dated_ids:
            blocked_dated_keys.update(candidate["keys"])
            issues.append(
                _dated_combination_issue(
                    meeting=meeting,
                    primary=candidate["primary"],
                    linked=candidate["linked"],
                    code="DATED_COMBINED_CONFLICT",
                    message=(
                        "Competing dated or recurring combined-meeting evidence overlaps this exact class occurrence. "
                        "Review the explicit combination before encoding."
                    ),
                )
            )
            continue
        for key in candidate["keys"]:
            ordinary.pop(key, None)
            dated_consumed[key] = candidate
        ordinary[("dated-meeting", meeting.pk)] = candidate["occurrence"]

    for key in blocked_dated_keys:
        ordinary.pop(key, None)

    for group in combined_classes:
        links = list(group.offering_links.all())
        if len(links) < 2:
            continue
        linked = tuple(link.offering for link in links)
        primary = next((link.offering for link in links if link.is_primary), linked[0])
        for meeting_date in _date_range(start_date, end_date):
            from .college_sync import effective_combined_slot, combined_source_slot
            slot = effective_combined_slot(group, meeting_date)
            if slot is None or meeting_date.weekday() != slot[0]:
                continue
            if meeting_date < group.effective_from or (
                group.effective_until and meeting_date > group.effective_until
            ):
                continue
            _, start_time, end_time = slot
            keys = [(item.pk, meeting_date, start_time, end_time) for item in linked]
            if all(key in historical_consumed for key in keys) and len({historical_consumed[key] for key in keys}) == 1:
                continue
            consumed = [dated_consumed.get(key) for key in keys]
            if all(item is not None for item in consumed):
                dated = consumed[0]
                if all(item is dated for item in consumed):
                    # Exact dated evidence is authoritative for this one date, not a new recurrence.
                    continue
            if any(item is not None for item in consumed) or any(key in blocked_dated_keys for key in keys):
                issues.append(
                    DailyIssue(
                        code="DATED_COMBINED_CONFLICT",
                        offering=primary,
                        linked_offerings=linked,
                        meeting_date=meeting_date,
                        occurrence_key=f"combined:{group.pk}:{meeting_date.isoformat()}:{group.start_time:%H%M}-{group.end_time:%H%M}",
                        message=(
                            "Recurring and dated combined-meeting evidence conflicts for this exact class occurrence. "
                            "Review the explicit combination before encoding."
                        ),
                        action="combined",
                    )
                )
                continue
            source_rows = [ordinary.get(key) for key in keys]
            if any(item is None for item in source_rows):
                # Do not split an explicit combination into independent classes
                # while linked academic edits are awaiting agreement.
                for item in linked:
                    source_slot = combined_source_slot(group, item, meeting_date)
                    if source_slot and source_slot[0] == meeting_date.weekday():
                        ordinary.pop((item.pk, meeting_date, source_slot[1], source_slot[2]), None)
                issues.append(
                    DailyIssue(
                        code="COMBINED_SOURCE_CONFLICT",
                        offering=primary,
                        linked_offerings=linked,
                        meeting_date=meeting_date,
                        message=(
                            f"Sections taught together on {meeting_date} do not all match the recorded Course Offering "
                            "schedule evidence. Save matching schedules for the linked Course Offerings."
                        ),
                        action="combined",
                    )
                )
                continue
            primary_source = ordinary[keys[linked.index(primary)]]
            if any(item.source_room != primary_source.source_room for item in source_rows):
                for key in keys:
                    ordinary.pop(key, None)
                issues.append(
                    DailyIssue(
                        code="COMBINED_ROOM_CONFLICT",
                        offering=primary,
                        linked_offerings=linked,
                        meeting_date=meeting_date,
                        message="Sections taught together have conflicting room evidence and cannot be encoded as one meeting.",
                        action="combined",
                    )
                )
                continue
            for key in keys:
                ordinary.pop(key, None)
            combined_key = f"combined:{group.pk}:{meeting_date.isoformat()}:{start_time:%H%M}-{end_time:%H%M}"
            ordinary[(primary.pk, meeting_date, start_time, end_time, group.pk)] = DailyOccurrence(
                occurrence_key=combined_key,
                primary_offering=primary,
                linked_offerings=linked,
                meeting_date=meeting_date,
                start_time=start_time,
                end_time=end_time,
                source_schedule_text=primary_source.source_schedule_text,
                source_room=primary_source.source_room,
                source_effective_from=primary_source.source_effective_from,
                source_effective_until=primary_source.source_effective_until,
                combined_definition_id=group.pk,
            )
    occurrences = sorted(
        ordinary.values(),
        key=lambda item: (item.meeting_date, item.start_time, item.source_room, item.occurrence_key),
    )
    return occurrences, deduplicate_daily_issues(issues)


def _is_effective_on(version, meeting_date):
    return version.effective_from <= meeting_date and (
        version.effective_until is None or meeting_date <= version.effective_until
    )


def _compatible_slot(version, occurrence):
    if version.interpretation_status != ScheduleVersion.InterpretationStatus.CONFIRMED:
        return None
    if _normalized_text(version.original_text) != _normalized_text(occurrence.source_schedule_text):
        return None
    parsed = parse_schedule_text(occurrence.source_schedule_text)
    if not parsed.confirmed:
        return None
    version_slots = list(version.slots.all())
    expected_slots = sorted((item.weekday, item.start_time, item.end_time) for item in parsed.slots)
    actual_slots = sorted((item.weekday, item.start_time, item.end_time) for item in version_slots)
    if actual_slots != expected_slots:
        return None
    expected_room = _normalized_text(occurrence.source_room)
    for slot in version_slots:
        recorded_rooms = {
            _normalized_text(value)
            for value in (slot.room_text, slot.room)
            if _normalized_text(value)
        }
        if expected_room and recorded_rooms != {expected_room}:
            return None
        if not expected_room and recorded_rooms:
            return None
    return next(
        (
            slot
            for slot in version_slots
            if slot.weekday == occurrence.meeting_date.weekday()
            and slot.start_time == occurrence.start_time
            and slot.end_time == occurrence.end_time
        ),
        None,
    )


def _existing_source_slot_for_occurrence(*, occurrence, lock=False):
    versions = ScheduleVersion.objects.filter(
        offering_id=occurrence.primary_offering.pk,
        effective_from__lte=occurrence.source_effective_until or date.max,
    ).filter(Q(effective_until__isnull=True) | Q(effective_until__gte=occurrence.source_effective_from))
    if lock:
        versions = versions.select_for_update()
    versions = list(versions.prefetch_related("slots").order_by("effective_from", "version_number", "pk"))
    if not versions:
        return None
    effective = [item for item in versions if _is_effective_on(item, occurrence.meeting_date)]
    if len(effective) > 1:
        # Same-day academic edits append versions rather than overwrite them.
        # Only an explicitly resolved automatic source update may select its
        # exact fingerprint; unrelated manual interpretation conflicts still block.
        fingerprint = _source_fingerprint(schedule_text=occurrence.source_schedule_text,
            room=occurrence.source_room, effective_from=occurrence.source_effective_from,
            effective_until=occurrence.source_effective_until)
        automatic = OfferingAttendanceSourceChange.objects.filter(offering_id=occurrence.primary_offering.pk,
            status="RESOLVED", effective_from=occurrence.source_effective_from,
            source_reference__startswith="college-course-offering:",
            new_schedule_text=occurrence.source_schedule_text, new_room=occurrence.source_room).exists()
        exact = [v for v in effective if v.source_kind == "COURSE_OFFERING" and v.source_fingerprint == fingerprint]
        if automatic and exact:
            effective = [max(exact, key=lambda v: (v.version_number, v.pk))]
    if len(effective) > 1:
        raise DailyMaterializationBlock(
            "Multiple recorded schedule interpretations are effective on this date. Resolve which source applies before encoding.",
            code="SCHEDULE_INTERPRETATION_AMBIGUOUS",
        )
    if not effective:
        raise DailyMaterializationBlock(
            "A recorded schedule interpretation overlaps the Course Offering source period but does not cover this date. Review the effective dates before encoding.",
            code="SCHEDULE_EFFECTIVE_DATE_CONFLICT",
        )
    version = effective[0]
    slot = _compatible_slot(version, occurrence)
    if slot is None:
        raise DailyMaterializationBlock(
            "The recorded attendance schedule, time, or room conflicts with the Course Offering source for this date. Resolve the schedule evidence before encoding.",
            code="SCHEDULE_SOURCE_CONFLICT",
        )
    return slot


def _local_meeting_time(value):
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.time().replace(tzinfo=None)


def _meeting_matches_occurrence(meeting, occurrence, *, schedule_slot_id=None):
    offering_ids = {item.pk for item in occurrence.linked_offerings}
    linked_ids = {item.offering_id for item in meeting.offering_links.all()}
    return (
        (schedule_slot_id is None or meeting.schedule_slot_id == schedule_slot_id)
        and linked_ids == offering_ids
        and _local_meeting_time(meeting.starts_at) == occurrence.start_time
        and _local_meeting_time(meeting.ends_at) == occurrence.end_time
    )


def _meeting_location_matches_occurrence(meeting, occurrence):
    expected_room = _normalized_text(occurrence.source_room)
    snapshot = meeting.location_snapshot or {}
    recorded_rooms = {
        _normalized_text(snapshot.get(field))
        for field in ("room", "room_text")
        if _normalized_text(snapshot.get(field))
    }
    return recorded_rooms == ({expected_room} if expected_room else set())


def _recorded_meetings_for_occurrence(occurrence, *, lock=False):
    offering_ids = [item.pk for item in occurrence.linked_offerings]
    from .college_sync import retired_meeting_ids
    queryset = TeachingMeeting.objects.exclude(pk__in=retired_meeting_ids()).filter(
        meeting_date=occurrence.meeting_date,
        offering_links__offering_id__in=offering_ids,
    ).distinct().select_related("schedule_slot")
    if lock:
        queryset = queryset.select_for_update()
    candidates = list(queryset.prefetch_related("offering_links", "reconciliations").order_by("pk"))
    return [m for m in candidates if m.occurrence_key == occurrence.occurrence_key or (
        _local_meeting_time(m.starts_at) < occurrence.end_time and
        _local_meeting_time(m.ends_at) > occurrence.start_time)]


def _existing_meeting_for_occurrence(*, schedule_slot, occurrence, lock=False):
    candidates = _recorded_meetings_for_occurrence(occurrence, lock=lock)
    matching = [
        item
        for item in candidates
        if _meeting_matches_occurrence(item, occurrence, schedule_slot_id=schedule_slot.pk)
    ]
    if len(matching) == 1 and len(candidates) == 1:
        return matching[0]
    if candidates:
        raise DailyMaterializationBlock(
            "An existing dated meeting uses different linked sections, time, or schedule evidence. Review the explicit combined-class or historical meeting record before encoding.",
            code="DATED_MEETING_CONFLICT",
            action="combined",
        )
    return None


def inspect_daily_occurrences(occurrences):
    """Return read-only blockers that materialization would encounter."""
    issues = []
    for occurrence in occurrences:
        if occurrence.historical_meeting_id:
            continue
        recorded = _recorded_meetings_for_occurrence(occurrence)
        matching_recorded = [item for item in recorded if _meeting_matches_occurrence(item, occurrence)]
        if len(matching_recorded) > 1:
            issues.append(
                DailyIssue(
                    code="DATED_MEETING_CONFLICT",
                    message="Multiple dated meetings match this Course Offering occurrence. Review the historical meeting records before encoding.",
                    offering=occurrence.primary_offering,
                    linked_offerings=occurrence.linked_offerings,
                    meeting_date=occurrence.meeting_date,
                    occurrence_key=occurrence.occurrence_key,
                    action="combined",
                )
            )
        for meeting in matching_recorded:
            if meeting.unresolved_coverage and not hasattr(meeting, "coverage_adoption") and not hasattr(meeting, "substitution"):
                issues.append(
                    DailyIssue(
                        code="UNRESOLVED_COVERAGE",
                        message="Save the assigned faculty and Effective from in Faculty Assignments. Linked sections must have the same dated faculty before encoding.",
                        offering=occurrence.primary_offering,
                        linked_offerings=occurrence.linked_offerings,
                        meeting_date=occurrence.meeting_date,
                        occurrence_key=occurrence.occurrence_key,
                        action="coverage",
                    )
                )
            if any(item.status == "PENDING" for item in meeting.reconciliations.all()):
                issues.append(
                    DailyIssue(
                        code="MEETING_RECONCILIATION_PENDING",
                        message="This dated meeting has a pending attendance-history reconciliation.",
                        offering=occurrence.primary_offering,
                        linked_offerings=occurrence.linked_offerings,
                        meeting_date=occurrence.meeting_date,
                        occurrence_key=occurrence.occurrence_key,
                        action="reconciliation",
                    )
                )
        try:
            slot = _existing_source_slot_for_occurrence(occurrence=occurrence)
            if slot is not None:
                _existing_meeting_for_occurrence(schedule_slot=slot, occurrence=occurrence)
        except (ValidationError, PermissionDenied) as exc:
            issues.append(_issue_from_exception(exc=exc, occurrence=occurrence))
    return deduplicate_daily_issues(issues)


def _source_slot_for_occurrence(*, actor, occurrence):
    offering = CourseOffering.objects.select_for_update().select_related("tenant", "campus", "department").get(
        pk=occurrence.primary_offering.pk
    )
    require_attendance_permission(
        user=actor,
        permission_code=ENCODE_PERMISSION,
        tenant_id=offering.tenant_id,
        campus_id=offering.campus_id,
        department_id=offering.department_id,
    )
    fingerprint = _source_fingerprint(
        schedule_text=occurrence.source_schedule_text,
        room=occurrence.source_room,
        effective_from=occurrence.source_effective_from,
        effective_until=occurrence.source_effective_until,
    )
    slot = _existing_source_slot_for_occurrence(occurrence=occurrence, lock=True)
    if slot is None:
        parsed = parse_schedule_text(occurrence.source_schedule_text)
        if not parsed.confirmed:
            raise ValidationError("Course Offering schedule must be corrected before daily encoding.")
        version_number = (
            ScheduleVersion.objects.filter(offering=offering).aggregate(value=Max("version_number"))["value"] or 0
        ) + 1
        version = ScheduleVersion(
            tenant=offering.tenant,
            campus=offering.campus,
            department=offering.department,
            offering=offering,
            version_number=version_number,
            original_text=occurrence.source_schedule_text,
            source_kind="COURSE_OFFERING",
            source_fingerprint=fingerprint,
            interpretation_status=ScheduleVersion.InterpretationStatus.CONFIRMED,
            effective_from=occurrence.source_effective_from,
            effective_until=occurrence.source_effective_until,
            correction_reason="Recognizable Course Offering source materialized for daily attendance encoding.",
            created_by=actor,
        )
        version.full_clean()
        version.save()
        for sequence, parsed_slot in enumerate(parsed.slots, start=1):
            slot = ScheduleSlot(
                schedule_version=version,
                sequence=sequence,
                weekday=parsed_slot.weekday,
                start_time=parsed_slot.start_time,
                end_time=parsed_slot.end_time,
                room_text=occurrence.source_room,
                room=occurrence.source_room,
            )
            slot.full_clean()
            slot.save()
        slot = version.slots.filter(
            weekday=occurrence.meeting_date.weekday(),
            start_time=occurrence.start_time,
            end_time=occurrence.end_time,
        ).first()
    if slot is None:
        raise ValidationError("The recorded Course Offering source does not contain this expected meeting time.")
    return slot


@transaction.atomic
def materialize_daily_occurrence(*, actor, occurrence):
    """Create or reuse exactly one dated meeting from a recognizable academic source."""
    for offering in occurrence.linked_offerings:
        require_attendance_permission(
            user=actor,
            permission_code=ENCODE_PERMISSION,
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            department_id=offering.department_id,
        )
    if occurrence.historical_meeting_id:
        return TeachingMeeting.objects.select_for_update().get(pk=occurrence.historical_meeting_id)
    slot = _source_slot_for_occurrence(actor=actor, occurrence=occurrence)
    existing = _existing_meeting_for_occurrence(schedule_slot=slot, occurrence=occurrence, lock=True)
    if existing is not None:
        return existing
    return MeetingService.generate(
        actor=actor,
        schedule_slot=slot,
        meeting_date=occurrence.meeting_date,
        offerings=[item for item in occurrence.linked_offerings if item.pk != occurrence.primary_offering.pk],
        permission_code=ENCODE_PERMISSION,
        occurrence_key=occurrence.occurrence_key,
        source_kind="COURSE_OFFERING",
    )


def _audited_academic_occurrence(occurrence):
    """Proof for each source of a new row, not permission to alter frozen rows.

    Creation proves the class exists, not who taught it: missing dated coverage
    remains unresolved. A retired sibling alone is never evidence for additions.
    """
    from apps.auditlog.models import AuditLog
    from .models import CoverageReconciliation, FacultyCoverage
    starts_at = timezone.make_aware(datetime.combine(occurrence.meeting_date, occurrence.start_time))
    for offering in occurrence.linked_offerings:
        scope = {"tenant_id": offering.tenant_id, "campus_id": offering.campus_id,
                 "department_id": offering.department_id}
        if offering.attendance_source_changes.filter(**scope, status="RESOLVED",
                source_reference__startswith="college-course-offering:",
                effective_from__lte=occurrence.meeting_date,
                new_schedule_text=occurrence.source_schedule_text, new_room=occurrence.source_room).exists():
            continue
        if AuditLog.objects.filter(action="CREATE", portal="ADMIN", entity_type="CourseOffering",
                entity_id=str(offering.pk), tenant_id=offering.tenant_id, campus_id=offering.campus_id,
                after_json__schedule_text=occurrence.source_schedule_text,
                after_json__room=occurrence.source_room).exists():
            continue
        reconciliations = CoverageReconciliation.objects.filter(**scope, offering=offering,
            status="RESOLVED", proposed_faculty__isnull=False, source_assignment__isnull=False)
        authorized = False
        for coverage in FacultyCoverage.objects.filter(**scope, offering=offering,
                effective_from__lte=starts_at).filter(Q(effective_until__isnull=True) | Q(effective_until__gt=starts_at)):
            ids = reconciliations.filter(source_assignment_id=coverage.source_assignment_id,
                proposed_faculty_id=coverage.faculty_user_id, effective_at=coverage.effective_from).values_list("pk", flat=True)
            if AuditLog.objects.filter(action="FACULTY_ATTENDANCE_ASSIGNMENT_SYNC_RESOLVED",
                    entity_type="CoverageReconciliation", entity_id__in=[str(pk) for pk in ids],
                    tenant_id=offering.tenant_id, campus_id=offering.campus_id).exists():
                authorized = True
                break
        if not authorized:
            return False
    return True


@transaction.atomic
def prepare_daily_encoding(*, actor, offerings, academic_year, term, meeting_date, saved_route=None):
    """Materialize a date's recognizable occurrences and create/reuse one round per department."""
    offerings = list(offerings)
    if not offerings:
        raise ValidationError("No Course Offerings are available for the selected attendance scope.")
    if meeting_date < term.start_date or meeting_date > term.end_date:
        raise ValidationError("Choose a date within the selected semester.")
    if saved_route and (saved_route.owner_id != actor.pk or saved_route.tenant_id != offerings[0].tenant_id):
        raise ValidationError("Saved route is not available for this checker scope.")
    groups = _combined_groups(
        tenant_id=offerings[0].tenant_id if offerings else None,
        campus_id=offerings[0].campus_id if offerings else None,
        academic_year_id=academic_year.pk,
        term_id=term.pk,
        offering_ids={item.pk for item in offerings},
    ) if offerings else []
    occurrences, issues = expected_daily_occurrences(
        offerings=offerings,
        term=term,
        start_date=meeting_date,
        end_date=meeting_date,
        combined_classes=groups,
    )
    meetings_by_department = {}
    for occurrence in occurrences:
        try:
            meeting = materialize_daily_occurrence(actor=actor, occurrence=occurrence)
        except (ValidationError, PermissionDenied) as exc:
            issues.append(_issue_from_exception(exc=exc, occurrence=occurrence))
            continue
        meetings_by_department.setdefault(meeting.department_id, []).append((meeting, occurrence))

    rounds = []
    for department_id, meeting_rows in meetings_by_department.items():
        meetings = [item[0] for item in meeting_rows]
        linked_offerings = tuple(
            {offering.pk: offering for _, occurrence in meeting_rows for offering in occurrence.linked_offerings}.values()
        )
        existing = CheckingRound.objects.select_for_update().filter(
            tenant_id=meetings[0].tenant_id,
            campus_id=meetings[0].campus_id,
            department_id=department_id,
            academic_year=academic_year,
            term=term,
            daily_occurrence_date=meeting_date,
        ).first()
        if existing:
            frozen_ids = set(existing.manifest_rows.values_list("meeting_id", flat=True))
            missing = [item for item in meetings if item.pk not in frozen_ids]
            # A verified academic edit may add a legitimate second daily slot.
            # Only missing rows backed by the audited automatic source operation
            # can extend a prepared list; unrelated manual changes still block.
            automatically_added = all(_audited_academic_occurrence(occurrence)
                for meeting, occurrence in meeting_rows if meeting.pk not in frozen_ids)
            if missing and automatically_added:
                from .models import CheckingRoundMeeting
                from .observations import _meeting_manifest_snapshot
                from .services import _audit
                sequence = existing.manifest_rows.aggregate(n=Max("sequence"))["n"] or 0
                for meeting in missing:
                    sequence += 1
                    row = CheckingRoundMeeting(checking_round=existing, meeting=meeting, sequence=sequence,
                        reviewed_result_revision=0, meeting_snapshot=_meeting_manifest_snapshot(meeting))
                    row.full_clean()
                    row.save()
                existing.manifest_revision += 1
                existing.save(update_fields=["manifest_revision", "updated_at"])
                _audit(action="FACULTY_ATTENDANCE_RESCHEDULED_LIST_RESUMED", entity=existing, actor=actor,
                       after={"added_meeting_ids": [m.pk for m in missing], "manifest_revision": existing.manifest_revision})
            elif missing:
                issues.append(
                    DailyIssue(
                        code="DAILY_ROUND_STALE",
                        offering=linked_offerings[0] if linked_offerings else None,
                        linked_offerings=linked_offerings,
                        meeting_date=meeting_date,
                        message=(
                            "This department already has a frozen daily encoding list with different meetings. "
                            "Review source changes instead of silently extending it."
                        ),
                        action="reconciliation",
                    )
                )
            rounds.append(existing)
            continue
        try:
            rounds.append(
                CheckingRoundService.create(
                    actor=actor,
                    meetings=meetings,
                    checking_date=meeting_date,
                    checking_end_date=meeting_date,
                    label=f"Daily encoding {meeting_date.isoformat()}",
                    saved_route=saved_route,
                    academic_year=academic_year,
                    term=term,
                    daily_occurrence_date=meeting_date,
                )
            )
        except IntegrityError:
            existing = CheckingRound.objects.get(
                tenant_id=meetings[0].tenant_id,
                campus_id=meetings[0].campus_id,
                department_id=department_id,
                academic_year=academic_year,
                term=term,
                daily_occurrence_date=meeting_date,
            )
            rounds.append(existing)
    return rounds, deduplicate_daily_issues(issues)
