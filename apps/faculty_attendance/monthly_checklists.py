from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.core.services.audit import AuditService

from .models import MonthlyChecklistArrangement, MonthlyChecklistArrangementEntry, RecurringCombinedClass, TeachingMeeting
from .permissions import MANAGE_ROUTES_PERMISSION, require_attendance_permission
from .schedule_parsing import parse_schedule_text


DAY_GROUPS = {
    "MW": ((0, 2), "Monday / Wednesday"),
    "TTH": ((1, 3), "Tuesday / Thursday"),
    "F": ((4,), "Friday"),
    "S": ((5,), "Saturday"),
}

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def day_group_for_weekdays(weekdays):
    days = tuple(sorted(set(int(day) for day in weekdays)))
    if not days or any(day < 0 or day > 6 for day in days):
        raise ValidationError("Select at least one valid weekday.")
    for key in ("MW", "TTH", "F", "S"):
        legacy_days = DAY_GROUPS[key][0]
        if days == legacy_days:
            return key
    return f"D{sum(1 << day for day in days):02X}"


# Preserve existing arrangement keys while accepting every nonempty weekday set.
for _mask in range(1, 128):
    _days = tuple(day for day in range(7) if _mask & (1 << day))
    DAY_GROUPS.setdefault(day_group_for_weekdays(_days), (_days, " / ".join(WEEKDAY_NAMES[day] for day in _days)))


@dataclass
class MonthlyChecklistRow:
    offering: CourseOffering
    pattern_key: str
    time_group_key: str
    weekdays: tuple[int, ...]
    start_time: object
    end_time: object
    faculty_label: str
    historical_attribution_note: str
    applicable_dates: frozenset[date]
    duration_hours: Decimal
    linked_offerings: tuple = ()
    room_label: str = ""
    is_combined: bool = False

    @property
    def token(self):
        return f"{self.offering_id}:{self.pattern_key}"

    @property
    def offering_id(self):
        return self.offering.pk


def month_dates(year: int, month: int, day_group: str) -> list[date]:
    weekdays = DAY_GROUPS[day_group][0]
    return [
        date(year, month, day)
        for day in range(1, calendar.monthrange(year, month)[1] + 1)
        if date(year, month, day).weekday() in weekdays
    ]


def _current_faculty_label(offering):
    assignments = [row for row in offering.faculty_assignments.all() if row.is_active]
    if not assignments:
        return "Unassigned"
    primary = [row for row in assignments if row.is_primary]
    rows = primary or assignments
    return ", ".join(row.faculty_user.full_name for row in rows)


def _faculty_attribution(offering, applicable_dates, start_time):
    coverages = list(offering.attendance_coverages.all())
    if not coverages:
        if applicable_dates and min(applicable_dates) < timezone.localdate():
            return "Historical faculty not verified", "No effective-dated coverage confirms the past dates."
        return _current_faculty_label(offering), ""
    by_faculty = defaultdict(list)
    gaps = []
    for meeting_date in sorted(applicable_dates):
        at = timezone.make_aware(datetime.combine(meeting_date, start_time), timezone.get_current_timezone())
        matching = [row for row in coverages if row.effective_from <= at and (row.effective_until is None or at < row.effective_until)]
        if len(matching) == 1:
            by_faculty[matching[0].faculty_user.full_name].append(meeting_date.day)
        else:
            gaps.append(meeting_date.day)
    labels = [f"{name} ({', '.join(map(str, days))})" for name, days in by_faculty.items()]
    if gaps:
        labels.append(f"Unassigned / review ({', '.join(map(str, gaps))})")
    return "; ".join(labels) or "Unassigned / review", "Coverage gaps require checker review." if gaps else ""


def _faculty_for_date(offering, meeting_date, start_time):
    at = timezone.make_aware(datetime.combine(meeting_date, start_time), timezone.get_current_timezone())
    coverages = [row for row in offering.attendance_coverages.all()
                 if row.effective_from <= at and (row.effective_until is None or at < row.effective_until)]
    if len(coverages) == 1:
        faculty = coverages[0].faculty_user
        return faculty.full_name, f"u{faculty.pk}", ""
    if meeting_date < timezone.localdate():
        return "Historical faculty not verified", "history-unverified", "No effective-dated coverage confirms these past dates."
    assignments = [row for row in offering.faculty_assignments.all() if row.is_active]
    primary = [row for row in assignments if row.is_primary]
    assignments = primary or assignments
    if len(assignments) == 1:
        faculty = assignments[0].faculty_user
        return faculty.full_name, f"u{faculty.pk}", ""
    return "Unassigned / review", "unresolved", "Faculty evidence is missing or conflicting."


def build_monthly_rows(*, offerings, year: int, month: int, day_group: str, arrangement=None, combined_classes=(), dated_meetings=()):
    wanted_days = set(DAY_GROUPS[day_group][0])
    dates = month_dates(year, month, day_group)
    rows = []
    corrections = []
    offering_map = {offering.pk: offering for offering in offerings}
    occurrences = {}
    for offering in offerings:
        parsed = parse_schedule_text(offering.schedule_text)
        if not parsed.confirmed:
            corrections.append((offering, parsed.reason))
            continue
        by_time = defaultdict(list)
        for slot in parsed.slots:
            if slot.weekday in wanted_days:
                by_time[(slot.start_time, slot.end_time)].append(slot.weekday)
        for (start, end), weekdays in by_time.items():
            weekdays = tuple(sorted(set(weekdays)))
            source_pattern = f"d{','.join(str(day) for day in weekdays)}@{start:%H%M}-{end:%H%M}"
            for meeting_date in dates:
                if meeting_date.weekday() not in weekdays:
                    continue
                label, faculty_key, note = _faculty_for_date(offering, meeting_date, start)
                occurrences[(offering.pk, meeting_date, start, end)] = {
                    "offering": offering, "linked": (offering,), "date": meeting_date, "start": start, "end": end,
                    "faculty_label": label, "faculty_key": faculty_key, "note": note,
                    "room": (offering.room or "").strip(), "pattern": source_pattern, "combined": False,
                }
    unresolved_seen = set()
    exact_consumed = set()
    for meeting in dated_meetings:
        links = list(meeting.offering_links.all())
        if len(links) < 2 or meeting.meeting_date not in dates:
            continue
        start = timezone.localtime(meeting.starts_at).time().replace(tzinfo=None)
        end = timezone.localtime(meeting.ends_at).time().replace(tzinfo=None)
        linked = tuple(offering_map[link.offering_id] for link in links if link.offering_id in offering_map)
        if len(linked) != len(links):
            continue
        primary_link = next((link for link in links if link.is_primary), links[0])
        keys = [(offering.pk, meeting.meeting_date, start, end) for offering in linked]
        evidence = [occurrences.get(key) for key in keys]
        for key in keys:
            occurrences.pop(key, None)
            exact_consumed.add(key)
        faculty = getattr(getattr(meeting, "substitution", None), "substitute_faculty", None) or meeting.faculty_user
        label = faculty.full_name if faculty else "Unassigned / review"
        fallback = next((item for item in evidence if item is not None), None)
        room = meeting.location_snapshot.get("room") or meeting.location_snapshot.get("room_text") or (fallback["room"] if fallback else "")
        occurrences[("meeting", meeting.pk)] = {
            "offering": offering_map[primary_link.offering_id], "linked": linked, "date": meeting.meeting_date,
            "start": start, "end": end,
            "faculty_label": label, "faculty_key": f"dated{getattr(faculty, 'pk', 0)}", "note": "",
            "room": room, "pattern": f"m{meeting.pk}@{start:%H%M}-{end:%H%M}", "combined": True,
        }
    for group in combined_classes:
        links = list(group.offering_links.all())
        linked = tuple(offering_map[link.offering_id] for link in links if link.offering_id in offering_map)
        if len(linked) != len(links) or len(linked) < 2:
            continue
        primary_link = next((link for link in links if link.is_primary), links[0])
        primary = offering_map[primary_link.offering_id]
        for meeting_date in dates:
            if meeting_date.weekday() != group.weekday or meeting_date < group.effective_from or (group.effective_until and meeting_date > group.effective_until):
                continue
            keys = [(offering.pk, meeting_date, group.start_time, group.end_time) for offering in linked]
            if all(key in exact_consumed for key in keys):
                continue
            evidence = [occurrences.get(key) for key in keys]
            reason = ""
            if any(item is None for item in evidence):
                reason = "A linked offering no longer has the exact recurring day/time; the sections remain separate for this date."
            elif len({item["room"].casefold() for item in evidence}) != 1 or not evidence[0]["room"]:
                reason = "Linked offerings have missing or conflicting room evidence; no combined row was inferred."
            elif len({item["faculty_key"] for item in evidence}) != 1:
                reason = "Linked offerings have conflicting faculty evidence for this date; no combined row was inferred."
            if reason:
                marker = (group.pk, reason)
                if marker not in unresolved_seen:
                    corrections.append((primary, f"Sections taught together: {reason}"))
                    unresolved_seen.add(marker)
                continue
            for key in keys:
                occurrences.pop(key, None)
            first = evidence[0]
            occurrences[("combined", group.pk, meeting_date)] = {
                **first, "offering": primary, "linked": linked, "date": meeting_date,
                "pattern": f"c{group.pk}@{group.weekday}-{group.start_time:%H%M}-{group.end_time:%H%M}",
                "combined": True,
                "note": first["note"],
            }
    grouped = defaultdict(list)
    for item in occurrences.values():
        identity = (item["offering"].pk, item["pattern"], item["faculty_key"], item["room"].casefold(),
                    tuple(row.pk for row in item["linked"]), item["note"])
        grouped[identity].append(item)
    for items in grouped.values():
        first = items[0]
        applicable = frozenset(item["date"] for item in items)
        weekdays = tuple(sorted({item.weekday() for item in applicable}))
        segment = first["faculty_key"].replace("-", "")[:16]
        pattern_key = f"{first['pattern']}~{segment}"
        minutes = (datetime.combine(date.min, first["end"]) - datetime.combine(date.min, first["start"])).seconds // 60
        rows.append(MonthlyChecklistRow(
            offering=first["offering"], pattern_key=pattern_key,
            time_group_key=f"{first['start']:%H%M}-{first['end']:%H%M}", weekdays=weekdays,
            start_time=first["start"], end_time=first["end"], faculty_label=first["faculty_label"],
            historical_attribution_note=first["note"], applicable_dates=applicable,
            duration_hours=(Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01")),
            linked_offerings=first["linked"], room_label=first["room"], is_combined=first["combined"],
        ))
    positions = {}
    if arrangement:
        positions = {(entry.offering_id, entry.pattern_key): entry.position for entry in arrangement.entries.all()}
    rows.sort(key=lambda row: (
        row.time_group_key,
        positions.get((row.offering_id, row.pattern_key), 10**9),
        (row.offering.room or "").casefold(),
        row.offering.course.code.casefold(),
        row.offering.section.code.casefold(),
        row.offering_id,
    ))
    return rows, corrections, dates


class MonthlyArrangementService:
    @classmethod
    @transaction.atomic
    def save(cls, *, actor, tenant_id, campus_id, academic_year_id, term_id, day_group, tokens, expected_revision):
        if day_group not in DAY_GROUPS:
            raise ValidationError("Select a supported day group.")
        if len(tokens) != len(set(tokens)):
            raise ValidationError("Arrangement contains duplicate classes.")
        arrangement = MonthlyChecklistArrangement.objects.select_for_update().filter(
            tenant_id=tenant_id, campus_id=campus_id, academic_year_id=academic_year_id,
            term_id=term_id, owner=actor, day_group=day_group,
        ).first()
        if arrangement and expected_revision != arrangement.revision:
            raise ValidationError("This arrangement changed in another page. Reload before saving again.")
        parsed_tokens = []
        for token in tokens:
            try:
                offering_id, pattern_key = token.split(":", 1)
                parsed_tokens.append((int(offering_id), pattern_key))
            except (TypeError, ValueError):
                raise ValidationError("Arrangement contains an invalid class row.")
        offerings = {
            row.pk: row for row in CourseOffering.objects.select_for_update().filter(
                pk__in=[item[0] for item in parsed_tokens], tenant_id=tenant_id, campus_id=campus_id,
                academic_year_id=academic_year_id, term_id=term_id,
            )
        }
        if len(offerings) != len({item[0] for item in parsed_tokens}):
            raise ValidationError("One or more classes are stale or outside the selected scope.")
        for department_id in {row.department_id for row in offerings.values()}:
            require_attendance_permission(
                user=actor, permission_code=MANAGE_ROUTES_PERMISSION, tenant_id=tenant_id,
                campus_id=campus_id, department_id=department_id,
            )
        submitted = []
        for offering_id, pattern_key in parsed_tokens:
            base_pattern = pattern_key.split("~", 1)[0]
            if base_pattern.startswith("c"):
                try:
                    group_id = int(base_pattern[1:].split("@", 1)[0])
                except ValueError as exc:
                    raise ValidationError("Combined class row identity is invalid.") from exc
                group = RecurringCombinedClass.objects.filter(
                    pk=group_id, tenant_id=tenant_id, campus_id=campus_id,
                    academic_year_id=academic_year_id, term_id=term_id,
                    offering_links__offering_id=offering_id, offering_links__is_primary=True,
                ).first()
                if group is None or group.weekday not in DAY_GROUPS[day_group][0]:
                    raise ValidationError("A combined class definition changed. Reload before saving the arrangement.")
                time_key = f"{group.start_time:%H%M}-{group.end_time:%H%M}"
                submitted.append((offering_id, pattern_key, time_key))
                continue
            if base_pattern.startswith("m"):
                try:
                    meeting_id = int(base_pattern[1:].split("@", 1)[0])
                except ValueError as exc:
                    raise ValidationError("Dated combined row identity is invalid.") from exc
                meeting = TeachingMeeting.objects.filter(
                    pk=meeting_id, tenant_id=tenant_id, campus_id=campus_id,
                    offering_links__offering_id=offering_id, offering_links__is_primary=True,
                ).first()
                if meeting is None or meeting.meeting_date.weekday() not in DAY_GROUPS[day_group][0]:
                    raise ValidationError("A dated combined meeting changed. Reload before saving the arrangement.")
                time_key = f"{timezone.localtime(meeting.starts_at):%H%M}-{timezone.localtime(meeting.ends_at):%H%M}"
                submitted.append((offering_id, pattern_key, time_key))
                continue
            parsed = parse_schedule_text(offerings[offering_id].schedule_text)
            valid_keys = set()
            grouped = defaultdict(list)
            for slot in parsed.slots if parsed.confirmed else ():
                if slot.weekday in DAY_GROUPS[day_group][0]:
                    grouped[(slot.start_time, slot.end_time)].append(slot.weekday)
            for (start, end), weekdays in grouped.items():
                valid_keys.add(f"d{','.join(str(day) for day in sorted(set(weekdays)))}@{start:%H%M}-{end:%H%M}")
            if base_pattern not in valid_keys:
                raise ValidationError("A class schedule changed. Reload and review the corrected rows.")
            time_key = base_pattern.rsplit("@", 1)[1]
            submitted.append((offering_id, pattern_key, time_key))
        if arrangement:
            retained = [
                (entry.offering_id, entry.pattern_key, entry.time_group_key)
                for entry in arrangement.entries.order_by("time_group_key", "position")
                if (entry.offering_id, entry.pattern_key) not in {(row[0], row[1]) for row in submitted}
            ]
            arrangement.revision += 1
        else:
            arrangement = MonthlyChecklistArrangement(
                tenant_id=tenant_id, campus_id=campus_id, academic_year_id=academic_year_id,
                term_id=term_id, owner=actor, day_group=day_group,
            )
            retained = []
        arrangement.full_clean()
        arrangement.save()
        arrangement.entries.all().delete()
        grouped_rows = defaultdict(list)
        for item in submitted + retained:
            grouped_rows[item[2]].append(item)
        for time_key, items in grouped_rows.items():
            for position, (offering_id, pattern_key, _) in enumerate(items, 1):
                MonthlyChecklistArrangementEntry.objects.create(
                    arrangement=arrangement, offering_id=offering_id, pattern_key=pattern_key,
                    time_group_key=time_key, position=position,
                )
        AuditService.log_event(
            action="FACULTY_ATTENDANCE_MONTHLY_ARRANGEMENT_SAVED", portal="ADMIN",
            entity_type="MonthlyChecklistArrangement", entity_id=arrangement.pk, actor=actor,
            tenant=tenant_id, campus=campus_id,
            after_data={"revision": arrangement.revision, "day_group": day_group, "entry_count": len(submitted)},
        )
        return arrangement
