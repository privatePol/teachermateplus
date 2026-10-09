"""Portrait DTR matrices projected only from saved final/publication evidence.

Cells show the saved credited teaching basis. Deductions and applied leave are
displayed separately; printing never recalculates a payable amount or saves data.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .schedule_parsing import parse_schedule_text


DATE_COLUMNS = 16
ROWS_PER_PAGE = 12
ZERO = Decimal("0")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_SHORT = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")


def _amount(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else ZERO
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


def _display(amount):
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _exact(line, field):
    return _amount(line.get(f"exact_{field}", line.get(field, "0")))


def _pattern(days):
    days = tuple(sorted(days))
    return {(0, 2): "MW", (1, 3): "TTH", (4,): "FRI"}.get(
        days, "/".join(DAY_NAMES[day].upper() for day in days))


def _saved_metadata(line, sections):
    # Historical publications did not necessarily save Size or Units. Missing
    # values must not be replaced with today's roster/course configuration.
    size = line.get("size")
    if size is None and sections and all(row.get("student_count") is not None for row in sections):
        size = sum(_amount(row["student_count"]) for row in sections)
    units = line.get("units")
    if units is None and sections and all(row.get("units") is not None for row in sections):
        course_units = {(row.get("course_code"), str(row["units"])) for row in sections}
        units = sum((_amount(value) for _, value in course_units), ZERO)
    return str(size) if size is not None else "-", _display(_amount(units)) if units is not None else "-"


def _time_label(value):
    return value.replace("\u00e2\u20ac\u201c", " - ").replace("\u2013", " - ").replace("\ufffd", " - ")


def _exception_labels(line):
    labels = []
    for field, code in (("a", "A"), ("n", "N")):
        if _exact(line, field):
            if f"exact_{field}" in line:
                minutes = _exact(line, field) * 60
                labels.append(f"{code} {minutes.normalize():f}m")
            else:
                labels.append(f"{code} {line[field]}h")
    for field, code in (("late", "L"), ("early", "E")):
        minutes = line.get(f"{field}_minutes")
        if minutes:
            labels.append(f"{code} {minutes}m")
        elif minutes is None and _exact(line, field):
            labels.append(f"{code} {line[field]}h")
    if line.get("closure_revision"):
        labels.append("Closure")
    elif not labels and line.get("status") and not line["status"].lower().startswith("present"):
        labels.append(line["status"][:40])
    return labels


def _row_chunks(rows):
    """Keep long saved section labels readable instead of shrinking the font."""
    chunk, weight = [], 0
    for row in rows:
        row_weight = max(2, (len(row["subjects"]) + 13) // 14, (len(row["sections"]) + 12) // 13)
        if chunk and (len(chunk) == ROWS_PER_PAGE or weight + row_weight > 30):
            yield chunk
            chunk, weight = [], 0
        chunk.append(row)
        weight += row_weight
    yield chunk


def build_dtr_matrix(saved, *, published_entries=()):
    """Return print-only pages without querying mutable academic/attendance data.

    Optional entries must belong to the selected final's immutable publication.
    A legacy final without those entries uses its saved labels/sections/time.
    No scheduled occurrence is invented for an empty date cell.
    """
    start, end = date.fromisoformat(saved["start_date"]), date.fromisoformat(saved["end_date"])
    dates = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    evidence = {entry["meeting_id"]: entry["meeting_snapshot"] for entry in published_entries}
    rows = {}
    admin = defaultdict(lambda: ZERO)
    notes = []
    leave_by_type = defaultdict(lambda: ZERO)
    for line in saved.get("lines", []):
        day = date.fromisoformat(line["date"])
        if line.get("kind") == "ADMIN":
            admin[day] += _exact(line, "admin")
            continue
        if line.get("kind") != "CLASS":
            if _exact(line, "other") or _exact(line, "leave"):
                notes.append({"date": day, "label": line.get("label", line["kind"]),
                    "detail": (f"Other {_display(_exact(line, 'other'))}h" if _exact(line, "other") else
                        f"{line.get('leave_type', 'Leave')} {_display(_exact(line, 'leave'))}h applied"),
                    "reason": line.get("status", "")[:120]})
            if line.get("kind") == "LEAVE":
                leave_by_type[line.get("leave_type", "")] += _exact(line, "leave")
            continue
        published = evidence.get(line.get("meeting_id"), {})
        sections = line.get("sections") or published.get("sections") or []
        identity = tuple(sorted((str(row.get("offering_id", "")), row.get("course_code", ""),
            row.get("section_code", ""), row.get("section_name", "")) for row in sections))
        # Include the entire linked-section set and saved schedule version/time.
        # Same course code alone is never a grouping key. Linked classes stay one row.
        schedule = line.get("schedule") or published.get("schedule") or {}
        size, units = _saved_metadata(line, sections)
        key = (identity or line.get("label", "Recorded class"), line.get("time", ""),
            schedule.get("schedule_version_id"), schedule.get("original_text", ""),
            line.get("department_id"), size, units)
        if key not in rows:
            subjects = ", ".join(dict.fromkeys(row.get("course_code", "") for row in sections))
            section_names = ", ".join(dict.fromkeys(row.get("section_code", "") for row in sections))
            parsed = parse_schedule_text(schedule.get("original_text", ""))
            weekdays = {slot.weekday for slot in parsed.slots
                if slot.start_time.isoformat() == schedule.get("start_time")
                and slot.end_time.isoformat() == schedule.get("end_time")} if parsed.confirmed else set()
            rows[key] = {"subjects": subjects or line.get("label", "Recorded class"),
                "sections": section_names or "-", "time": _time_label(line.get("time", "-")),
                "size": size, "units": units, "identity": identity or line.get("label"),
                "days": weekdays, "dated": defaultdict(list)}
        row = rows[key]
        row["days"].add(day.weekday())
        labels = _exception_labels(line)
        row["dated"][day].append({"hours": _exact(line, "teaching"), "labels": labels})
        if labels:
            notes.append({"date": day, "label": line.get("label", "Recorded class"),
                "detail": "; ".join(labels), "reason": line.get("status", "")[:120]})
    ordered = sorted(rows.values(), key=lambda row: (tuple(sorted(row["days"])), row["time"], row["subjects"], row["sections"]))
    unit_rows = {}
    for row in ordered:
        unit_rows.setdefault(row["identity"], row["units"])
    total_units = _display(sum((_amount(value) for value in unit_rows.values()), ZERO))
    if any(value == "-" for value in unit_rows.values()):
        total_units = "Not saved"
    pages = []
    # Preserve legibility for longer decimal values, without changing hours or
    # reducing type size to squeeze the wider cells into a portrait page.
    date_columns = 12 if any(len(_display(item["hours"])) > 4
        for row in ordered for values in row["dated"].values() for item in values) else DATE_COLUMNS
    for offset in range(0, len(dates), date_columns):
        band = dates[offset:offset + date_columns]
        for chunk in _row_chunks(ordered):
            groups = []
            page_hours = ZERO
            for row in chunk:
                cells = []
                total = ZERO
                for day in band:
                    occurrences = row["dated"].get(day, [])
                    amount = sum((item["hours"] for item in occurrences), ZERO)
                    total += amount
                    cells.append({"hours": _display(amount) if occurrences else "",
                        "labels": [label for item in occurrences for label in item["labels"]]})
                page_hours += total
                label = _pattern(row["days"])
                if not groups or groups[-1]["label"] != label:
                    groups.append({"label": label, "rows": []})
                groups[-1]["rows"].append({**row, "cells": cells, "total": _display(total)})
            pages.append({"dates": [{"date": day, "day": day.day, "weekday": DAY_SHORT[day.weekday()],
                    "weekday_name": DAY_NAMES[day.weekday()]} for day in band], "groups": groups,
                "start": band[0], "end": band[-1], "teaching_total": _display(page_hours),
                "column_count": len(band) + 6,
                "admin_cells": [_display(admin[day]) if day in admin else "-" for day in band],
                "admin_total": _display(sum((admin.get(day, ZERO) for day in band), ZERO)), "show_admin": False})
        # Do not repeat payable admin hours when the same date band has several
        # vertical teaching pages; its matrix appears once at the band end.
        pages[-1]["show_admin"] = True
    # Only meaningful exceptions/credits are listed, never repeated all-zero rows.
    notes = sorted(notes, key=lambda note: (note["date"], note["label"]))
    inline = notes if len(notes) <= 2 else []
    if inline:
        pages[-1]["notes"] = inline
    elif notes:
        for offset in range(0, len(notes), 10):
            pages.append({"notes_only": True, "notes": notes[offset:offset + 10]})
    for number, page in enumerate(pages, 1):
        page["number"], page["count"] = number, len(pages)
        page["is_last"] = number == len(pages)
    pages[-1]["summary"] = True
    return {"pages": pages, "total_units": total_units, "class_rows": len(ordered),
        "metadata_missing": any(row["size"] == "-" or row["units"] == "-" for row in ordered),
        "leave_types": [{"code": code, "hours": _display(leave_by_type[code])} for code in ("VL", "SL", "EL")],
        "month_label": start.strftime("%B %Y") if (start.year, start.month) == (end.year, end.month)
            else f"{start:%b %Y} - {end:%b %Y}"}
