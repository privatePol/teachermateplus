"""Bounded presentation of existing, already-disclosed report aggregates."""
from collections import Counter, defaultdict

from django.core.paginator import Paginator

from apps.admin_portal import learning_insights as learning
from apps.admin_portal import assessment_table

AVAILABILITY = {
    "eligible": "Eligible exam configuration", "excluded": "Excluded by exam policy",
    "pending": "Pending completion", "unavailable": "Missing or unsupported data",
    "withheld": "Privacy withheld",
}


def exam_chart(entries, section=False):
    """Separate descriptive points from each independently matched segment.

    A segment repeats only the two averages already released by the existing
    matched comparison. It never connects full-period averages or fills gaps.
    """
    periods = [(code, label) for code, label in learning.PERIODS if code != "PREFINAL"
               or any(row["code"] == code for e in entries for row in e["exams"])]
    codes, labels = [p[0] for p in periods], [p[1] for p in periods]
    datasets = []
    groups = defaultdict(list)
    for entry in entries:
        for row in entry["exams"]:
            if row["average"] is not None:
                groups[(row["group"], row["type"])].append(row)
    for index, ((_, kind), rows) in enumerate(groups.items(), 1):
        datasets.append({"label": f"{kind} · period averages {index}", "connected": False,
            "values": [learning.average([r["average"] for r in rows if r["code"] == code]) for code in codes]})
    if section:
        for pair in entries[0]["exam_comparisons"] if entries else []:
            if pair["difference"] is not None:
                datasets.append({"label": f"Matched {pair['from']} – {pair['to']} ({pair['count']} students)",
                    "connected": True, "values": [pair["previous"] if code == pair["from_code"] else
                        pair["current"] if code == pair["to_code"] else None for code in codes]})
    return {"labels": labels, "datasets": datasets}


def pair_bars(entries):
    # Keys are generated from the backend's effective comparison signatures.
    # All three measures in each input pair already use a safe common cohort.
    groups = defaultdict(list)
    for entry in entries:
        for pair in entry["pairs"]:
            if all(pair[m] is not None for m in ("quiz", "participation", "exam")):
                groups[(entry["course"], pair["source"], pair["destination"], pair["type"], pair["group"])].append(pair)
    return [{"section": f"{course} · {source} → {destination} · {kind} · group {index}",
        "quiz_grade": learning.average([r["quiz"] for r in rows]),
        "standing": learning.average([r["participation"] for r in rows]),
        "exam": learning.average([r["exam"] for r in rows]), "sections": len(rows)}
        for index, ((course, source, destination, kind, _), rows) in enumerate(groups.items(), 1)]


def assessment_presentation(result, level, visible_ids, page_number):
    source = result.get("assessment_period_rows", [])
    if level != "all":
        source = [row for row in source if row["id"] in visible_ids]
    # A presentation alias joins labels across periods, never scores. Preserve
    # distinct branches when the same label occurs more than once in a period.
    multiplicity = Counter()
    preferred = {}
    for row in source:
        labels = Counter((m["label"], m["kind"]) for m in row["measures"])
        for label, count in labels.items():
            multiplicity[label] = max(multiplicity[label], count)
        for m in row["measures"]:
            preferred.setdefault((m["label"], m["kind"]), m["code"])
    source = [{**row, "measures": [{**m, "code": preferred[(m["label"], m["kind"])]
                if multiplicity[(m["label"], m["kind"])] == 1 else m["code"]}
                for m in row["measures"] if m["code"] not in {"standing", "periodic"}]} for row in source]
    codes = {}
    for row in source:
        for measure in row["measures"]:
            codes.setdefault(measure["code"], (measure["label"], measure["kind"]))

    def cell(measure=None):
        measure = measure or {"value": None, "state": assessment_table.NA}
        value = measure["value"]
        states = {assessment_table.AVAILABLE: "available", assessment_table.NA: "na",
            assessment_table.PENDING: "pending", assessment_table.MISSING: "missing",
            assessment_table.WITHHELD: "withheld", assessment_table.HISTORICAL: "historical",
            assessment_table.HISTORICAL_CHANGED: "historical"}
        return {"text": (str(int(value)) if measure.get("kind") == "count" else f"{value:.2f}") if value is not None else measure["state"],
                "state": measure["state"], "state_class": states[measure["state"]],
                "contributors": measure.get("contributors")}

    def local_codes(members):
        configured = {m["code"] for row in members for m in row["measures"]
                      if m["state"] != assessment_table.NA}
        return {code: definition for code, definition in codes.items()
                if code in configured or not code.startswith("output_") or
                not any(c.startswith("output_") for c in configured)}

    def header(code, label):
        return {"code": code, "label": label.replace("Average (Gradebook Points)", "Avg").replace(
            "Examination Average", "Exam Avg").replace("Activity Count", "Count")}

    def row_cells(row, definitions):
        return [cell(next((m for m in row["measures"] if m["code"] == code), None)) for code in definitions]

    if level == "section":
        periods = sorted(source, key=lambda row: (row["sequence"], row["period_id"]))
        definitions = local_codes(periods)
        return {"assessment_matrix": [{"label": label, "cells": [cell(next((m for m in row["measures"] if m["code"] == code), None))
                    for row in periods]} for code, (label, kind) in definitions.items()],
                "assessment_heading": f"{periods[0]['section']} / {periods[0]['campus']}" if periods else "",
                "assessment_periods": [row["period"] for row in periods]}
    headers = [header(code, label) for code, (label, kind) in codes.items()]
    if level == "all":
        groups = defaultdict(list)
        for row in source:
            groups[(row["course"], row["period_code"], row["group"])].append(row)
        rows = []
        for index, ((course, period, group), members) in enumerate(sorted(groups.items()), 1):
            measures = []
            for code, (label, kind) in codes.items():
                candidates = [m for member in members for m in member["measures"] if m["code"] == code]
                values = [m["value"] for m in candidates if m["value"] is not None]
                value = (sum(values) if kind == "count" else learning.average(values)) if values else None
                state = assessment_table.AVAILABLE if values else next((status for status in (
                    assessment_table.WITHHELD, assessment_table.HISTORICAL_CHANGED,
                    assessment_table.HISTORICAL, assessment_table.PENDING, assessment_table.MISSING)
                    if any(m["state"] == status for m in candidates)), assessment_table.NA)
                measures.append(cell({"value": value, "kind": kind, "state": state, "contributors": len(values) if values else None}))
            definitions = local_codes(members)
            rows.append({"label": course, "period": members[0]["period"], "group": index,
                         "sections": len({r["id"] for r in members}), "cells": measures})
            rows[-1]["local_codes"] = list(definitions)
        page = Paginator(rows, 20).get_page(page_number)
        blocks = {}
        for row in page.object_list:
            schema = tuple(row["local_codes"])
            block = blocks.setdefault(schema, {"headers": [h for h in headers if h["code"] in schema], "rows": []})
            block["rows"].append({**row, "cells": [c for h, c in zip(headers, row["cells"]) if h["code"] in schema]})
        return {"assessment_headers": headers, "assessment_rows": list(page.object_list),
                "assessment_blocks": list(blocks.values()), "assessment_page": page}
    rows = []
    for row in sorted(source, key=lambda r: (r["section"], r["campus"], r["sequence"])):
        rows.append({"label": f"{row['section']} / {row['campus']}", "period": row["period"],
            "cells": row_cells(row, codes)})
    sections = {}
    for row in sorted(source, key=lambda r: (r["section"], r["campus"], r["sequence"])):
        sections.setdefault(row["id"], []).append(row)
    return {"assessment_headers": headers, "assessment_rows": rows,
        "assessment_sections": [{"heading": f"{members[0]['section']} / {members[0]['campus']}",
            "headers": [header(code, label) for code, (label, kind) in local_codes(members).items()],
            "rows": [{"period": r["period"], "cells": row_cells(r, local_codes(members))} for r in members]}
            for members in sections.values()]}


def section_observations(bars, period, assessment_rows=()):
    """Describe only the already released, comparable plotted values.

    The user-approved 3-point cutoff selects concise presentation observations;
    it does not classify sections or establish statistical significance.
    """
    numeric = [b for b in bars if b.get("quiz_grade") is not None and b.get("exam") is not None]
    if not period or not numeric:
        return ["Comparable Quiz and Examination averages are not available for a section comparison."]
    # Reuse the table's already released historical/privacy decision. Chart
    # values alone do not establish authoritative component availability.
    period_code = next((code for code, label in learning.PERIODS if label == period), None)
    records = defaultdict(list)
    for row in assessment_rows:
        code = learning.GradingGovernanceService._normalize_period_key(row["period_code"])
        if code == period_code:
            records[f"{row['section']} / {row['campus']}"].append(row)

    def verified(bar):
        matches = records[bar["section"]]
        if len(matches) != 1:
            return False
        for code in ("quiz_grade", "exam"):
            measures = [m for m in matches[0]["measures"] if m["code"] == code]
            if (len(measures) != 1 or measures[0]["state"] != assessment_table.AVAILABLE
                    or measures[0]["value"] != bar[code]):
                return False
        return True

    candidates = [bar for bar in numeric if verified(bar)]
    if not candidates:
        return ["A reliable Quiz–Examination comparison is unavailable for this period because the required component grades cannot be verified."]
    notable = sorted((b for b in candidates if abs(b["quiz_grade"] - b["exam"]) >= learning.MEANINGFUL_CHANGE),
                     key=lambda b: abs(b["quiz_grade"] - b["exam"]), reverse=True)[:2]
    if not notable:
        return [f"{period}: Quiz and Examination averages with verified components differ by less than 3 points. No larger verified gap is highlighted."]
    return [f"{b['section']} — {period}: Quiz average {b['quiz_grade']:.2f}; Examination average {b['exam']:.2f}. "
            f"{'Quiz' if b['quiz_grade'] > b['exam'] else 'Examination'} is {abs(b['quiz_grade'] - b['exam']):.2f} points higher. "
            "This gap may be worth reviewing alongside assessment coverage."
            for b in notable]


def present(result, selection, filters, tab, page_number):
    entries = result["classifications"]
    section = bool(filters.get("offering_id"))
    course = bool(filters.get("course_code"))
    level = "section" if section else "course" if course else "all"
    availability_periods = []
    for code, label in (("MIDTERM", "Midterm"), ("FINAL", "Final")):
        counts = Counter(e.get("period_availability", {}).get(code, "unavailable") for e in entries)
        availability_periods.append({"code": code, "label": label, "total": len(entries),
            "statuses": [{"code": key, "label": text, "count": counts[key]} for key, text in AVAILABILITY.items()]})
    context = {"tab": tab, "level": level, "unified_presentation": True,
        "sections": result["sections"], "analyzed": result["analyzed"],
        "insufficient": result["insufficient"], "patterns": result["patterns"],
        "availability_periods": availability_periods,
        "heading": result.get("class_label") or filters.get("course_code") or "All authorized courses",
        "details": [], "summary_rows": [], "overview": None, "bars_table": [], "exam_table": [],
        "period_rows": result.get("rows", []) if course else []}
    charts = {"line": [], "line_mode": "descriptive", "bars": [], "bar_period": "", "patterns": result["patterns"],
              "threshold": "3.00", "exams": {"labels": [], "datasets": []}}
    if level == "all":
        by_course = defaultdict(list)
        for row in entries:
            by_course[row["course"]].append(row)
        rows = []
        for code, group in sorted(by_course.items()):
            rows.append({"course": code, "label": code, "sections": len(group),
                "classified": sum(e["category"] in learning.PATTERNS for e in group),
                "review": sum(e["category"] in {learning.PATTERNS[1], learning.PATTERNS[2]} for e in group),
                "available_exams": sum(any(x["average"] is not None for x in e.get("exams", [])) for e in group)})
    elif level == "course":
        rows = [{"label": f"{e['section']} / {e['campus']}", "id": e["id"], "category": e["category"],
            "availability_label": "See performance results",
            "period_availability": [{"label": label, "status": AVAILABILITY[
                e.get("period_availability", {}).get(code, "unavailable")]}
                for code, label in (("MIDTERM", "Midterm"), ("FINAL", "Final"))],
            "available_exams": sum(x["average"] is not None for x in e.get("exams", []))} for e in entries]
        if tab == "overview":
            comparisons = {r["section"]: r for r in result.get("comparison_rows", [])}
            for row in rows:
                values = comparisons.get(row["label"], {})
                row.update({key: values.get(key) for key in ("quiz_grade", "standing", "exam")})
                row["availability_label"] = values.get("status", row["availability_label"])
    else:
        rows = []
    page = Paginator(rows, 20).get_page(page_number)
    context.update(page=page, summary_rows=list(page.object_list))
    visible = entries if section else [e for e in entries if e["id"] in {r.get("id") for r in page.object_list}]
    if tab == "overview":
        if section:
            # The proven detailed results template receives only the selected
            # section; it retains supporting period tables and explanations.
            context.update(result)
            context["overview"] = True
        elif course:
            allowed_labels = {r["label"] for r in page.object_list}
            charts.update(line=result["charts"]["line"], line_mode=result["charts"]["line_mode"],
                bar_period=result["charts"].get("bar_period", ""),
                bars=[b for b in result["charts"]["bars"] if b["section"] in allowed_labels])
            context["line_note"] = result["line_note"]
            context["comparison_rows"] = [r for r in result.get("comparison_rows", []) if r["section"] in allowed_labels]
        if section:
            selected_label = f"{entries[0]['section']} / {entries[0]['campus']}" if entries else ""
            bars = [b for b in result["charts"]["bars"] if b["section"] == selected_label]
            charts.update({**result["charts"], "bars": bars})
            context["bars"] = bars
            context["comparison_rows"] = [r for r in result.get("comparison_rows", []) if r["section"] == selected_label]
        context["bars_table"] = charts["bars"]
        context["section_observations"] = section_observations(
            charts["bars"], charts["bar_period"], result.get("assessment_period_rows", []))
        context.update(assessment_presentation(result, level,
            {e["id"] for e in entries} if section else {row.get("id") for row in page.object_list}, page_number))
    elif tab == "exams":
        # Broad view offers course drilldown, without averaging unrelated exams.
        if course:
            charts["exams"] = exam_chart(visible, section)
            charts["exams"]["datasets"] = charts["exams"]["datasets"][:12]
            context["exam_plot"] = charts["exams"]
            context["exam_table"] = [{**row, "section": e["section"]} for e in visible for row in e["exams"]]
        if section:
            context["details"] = visible
    else:
        grouped_entries = entries if section else visible if course else [e for e in entries
            if e["course"] in {r["course"] for r in page.object_list}]
        # A broad page shows at most twelve compatible groups; course/section
        # drilldown exposes the remaining groups without mass-rendering cards.
        grouped = pair_bars(grouped_entries)
        charts["bars"] = grouped[:12]
        context["bars_table"] = charts["bars"]
        context["group_limit"] = len(grouped) > 12
        if section:
            context["details"] = visible
    context["charts"] = charts
    return context, charts
