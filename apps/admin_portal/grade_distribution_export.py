"""Bounded Excel export of already authorized, privacy-filtered aggregates."""
from io import BytesIO

from django.core.exceptions import ValidationError
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

MAX_WORKBOOK_ROWS = 50000


def validate_workbook_size(summaries, frequencies):
    if summaries > MAX_WORKBOOK_ROWS or frequencies > MAX_WORKBOOK_ROWS:
        raise ValidationError(f"Excel export limit is {MAX_WORKBOOK_ROWS:,} aggregate rows per sheet. Narrow your criteria; no records were truncated.")


def grade_distribution_workbook(rows):
    summaries = sum(1 + len(row["measures"]) for row in rows)
    frequencies = sum(len(measure["frequency"]) for row in rows
        for measure in [row, *row["measures"]])
    validate_workbook_size(summaries, frequencies)
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Grade summaries"
    frequency = workbook.create_sheet("Grade frequencies")
    identity = ["Faculty", "Campus", "Course", "Section", "Academic Year / Term", "Period", "Measure",
                "Dataset Reference", "Class Record", "Period Record", "Activity Record", "Gradebook Context", "Status Selection"]
    high_labels = ["Exactly 100", "Exactly 99", "Exactly 98", "Exactly 97", "Exactly 96", "Exactly 95", "Combined 95–99", "90 and above"]
    summary.append(identity + ["Grade Basis", "Saved Status", "Eligible Students", "Graded Students",
        "Average Grade", "Minimum", "Maximum"] +
        [f"{label} {column}" for label in high_labels for column in ("Count", "%")] +
        ["Same Grades As", "Same Grades As Dataset", "Analytics Availability", "Privacy Explanation", "Historical", "Data Notes"])
    frequency.append(identity + ["Exact Grade", "Student Count", "% of Graded Students"])
    for row in rows:
        common = [row["faculty_name"], row["campus"], f'{row["course_code"]} - {row["course_title"]}',
                  row["section"], f'{row["school_year"]} / {row["term"]}', row["period"]]
        measures = [{"label": "Period Grades", "unit": "Saved Gradebook period grade", **row}] + row["measures"]
        for measure in measures:
            same_as = measure.get("equivalent_to", "")
            dataset = common + [measure["label"], measure["dataset_id"], measure["offering_id"],
                measure["physical_period_id"], measure["activity_id"], measure["gradebook_context"],
                {"all": "All Statuses", "finalized": "Finalized", "unfinalized": "Unfinalized"}[measure["selection_status"]]]
            summary.append(dataset + [measure["unit"], measure["status"],
                measure["eligible_count"], measure["graded_count"], measure["average"], measure["minimum"], measure["maximum"]] +
                [value for item in measure["high_grade_breakdown"] for value in
                    (item["count"], item["percentage"] / 100 if item["percentage"] is not None else None)] +
                [same_as, measure.get("equivalent_dataset_id", ""), measure["analytics_state"], measure["privacy_message"], row["historical"], "; ".join(row["notes"])])
            # Every distinct record keeps its own frequencies, including equal
            # value maps whose charts can be reused. No grouping by titles.
            for item in measure["frequency"]:
                frequency.append(dataset + [item["grade"], item["count"], item["percentage"] / 100])
    for sheet in workbook:
        sheet.freeze_panes = "H2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        sheet.print_title_rows = "1:1"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="20613C")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            sheet.column_dimensions[cell.column_letter].width = (
                48 if cell.value in ("Grade Basis", "Privacy Explanation", "Data Notes") else
                48 if cell.value in ("Dataset Reference", "Same Grades As Dataset", "Gradebook Context") else
                32 if cell.value in ("Faculty", "Course", "Measure", "Same Grades As") else 20)
        sheet.row_dimensions[1].height = 45
        for cells in sheet.iter_rows(min_row=2):
            for cell in cells:
                header = sheet.cell(1, cell.column).value
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                if isinstance(cell.value, str):
                    # Force user-authored names/titles to literal Excel text,
                    # including strings beginning with =, +, - or @.
                    cell.data_type = "s"
                elif cell.value is not None and not isinstance(cell.value, bool):
                    cell.number_format = "0.00%" if "%" in header else (
                        "0.00" if header in ("Average Grade", "Minimum", "Maximum", "Exact Grade") else "0")
                if cell.row % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F0F7F2")
        sheet.auto_filter.ref = f"A1:{get_column_letter(sheet.max_column)}{sheet.max_row}"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
