from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class ParsedScheduleSlot:
    weekday: int
    start_time: time
    end_time: time


@dataclass(frozen=True)
class ScheduleParseResult:
    confirmed: bool
    slots: tuple[ParsedScheduleSlot, ...]
    reason: str = ""


_SEGMENT_RE = re.compile(
    r"^\s*(?P<days>[A-Za-z/,\s]+?)\s+"
    r"(?P<start>\d{1,2}:\d{2})\s*(?P<start_meridiem>AM|PM)?\s*[-–]\s*"
    r"(?P<end>\d{1,2}:\d{2})\s*(?P<end_meridiem>AM|PM)?\s*$",
    re.IGNORECASE,
)
_TIME_RANGE_RE = re.compile(
    r"^\s*(?P<start>\d{1,2}:\d{2})\s*(?P<start_meridiem>AM|PM)?\s*[-â€“–]\s*"
    r"(?P<end>\d{1,2}:\d{2})\s*(?P<end_meridiem>AM|PM)?\s*$",
    re.IGNORECASE,
)

_NAMED_DAYS = {
    "M": 0,
    "MON": 0,
    "MONDAY": 0,
    "TUE": 1,
    "T": 1,
    "TUES": 1,
    "TUESDAY": 1,
    "WED": 2,
    "W": 2,
    "WEDNESDAY": 2,
    "THU": 3,
    "TH": 3,
    "THUR": 3,
    "THURS": 3,
    "THURSDAY": 3,
    "FRI": 4,
    "F": 4,
    "FRIDAY": 4,
    "SAT": 5,
    "S": 5,
    "SATURDAY": 5,
    "SUN": 6,
    "SUNDAY": 6,
}
_COMPACT_TOKENS = (("TH", 3), ("SA", 5), ("SU", 6), ("M", 0), ("T", 1), ("W", 2), ("F", 4), ("S", 5))


def _parse_days(raw: str) -> tuple[int, ...] | None:
    normalized = raw.strip().upper()
    if not normalized:
        return None
    if normalized in _NAMED_DAYS:
        days = [_NAMED_DAYS[normalized]]
    elif re.search(r"[/,\s]", normalized):
        tokens = [part for part in re.split(r"[/,\s]+", normalized) if part]
        days = [_NAMED_DAYS.get(token) for token in tokens]
        if not days or any(day is None for day in days):
            return None
    else:
        days = []
        remaining = normalized
        while remaining:
            matched = False
            for token, day in _COMPACT_TOKENS:
                if remaining.startswith(token):
                    days.append(day)
                    remaining = remaining[len(token) :]
                    matched = True
                    break
            if not matched:
                return None
    if len(days) != len(set(days)):
        return None
    return tuple(days)


def _parse_time(raw: str, meridiem: str | None) -> time | None:
    try:
        if meridiem:
            return datetime.strptime(f"{raw} {meridiem.upper()}", "%I:%M %p").time()
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


def parse_schedule_text(value: str | None) -> ScheduleParseResult:
    """Parse only explicit supported day/time expressions; otherwise fail closed."""
    if not value or not value.strip():
        return ScheduleParseResult(False, (), "Schedule text is blank.")

    parsed: list[ParsedScheduleSlot] = []
    normalized_value = re.sub(r"\s+", " ", value.strip())
    for segment in normalized_value.split(";"):
        # Imported TMP schedules may pair slash-separated days with positional
        # slash-separated ranges (for example M/W 10:30AM-12:00PM/...).
        positional = re.fullmatch(r"\s*(?P<days>[A-Za-z/,]+)\s+(?P<ranges>.+?)\s*", segment)
        if positional and "/" in positional.group("ranges"):
            days = _parse_days(positional.group("days"))
            ranges = positional.group("ranges").split("/")
            if not days or len(ranges) != len(days):
                return ScheduleParseResult(False, (), "Day and time counts do not match; correct the schedule text.")
            for day, raw_range in zip(days, ranges):
                range_match = _TIME_RANGE_RE.fullmatch(raw_range)
                if not range_match:
                    return ScheduleParseResult(False, (), "Unsupported or ambiguous schedule format.")
                start_meridiem = range_match.group("start_meridiem")
                end_meridiem = range_match.group("end_meridiem")
                if bool(start_meridiem) != bool(end_meridiem):
                    return ScheduleParseResult(False, (), "Both times must explicitly use AM/PM, or both must use 24-hour time.")
                start = _parse_time(range_match.group("start"), start_meridiem)
                end = _parse_time(range_match.group("end"), end_meridiem)
                if start is None or end is None or end <= start:
                    return ScheduleParseResult(False, (), "Schedule days or time range are invalid or ambiguous.")
                parsed.append(ParsedScheduleSlot(day, start, end))
            continue
        match = _SEGMENT_RE.fullmatch(segment)
        if not match:
            return ScheduleParseResult(False, (), "Unsupported or ambiguous schedule format.")
        start_meridiem = match.group("start_meridiem")
        end_meridiem = match.group("end_meridiem")
        if bool(start_meridiem) != bool(end_meridiem):
            return ScheduleParseResult(False, (), "Both times must explicitly use AM/PM, or both must use 24-hour time.")
        days = _parse_days(match.group("days"))
        start = _parse_time(match.group("start"), start_meridiem)
        end = _parse_time(match.group("end"), end_meridiem)
        if not days or start is None or end is None or end <= start:
            return ScheduleParseResult(False, (), "Schedule days or time range are invalid or ambiguous.")
        parsed.extend(ParsedScheduleSlot(day, start, end) for day in days)

    unique = {(slot.weekday, slot.start_time, slot.end_time) for slot in parsed}
    if len(unique) != len(parsed):
        return ScheduleParseResult(False, (), "Schedule contains duplicate meeting slots.")
    return ScheduleParseResult(True, tuple(sorted(parsed, key=lambda item: (item.weekday, item.start_time))), "")
