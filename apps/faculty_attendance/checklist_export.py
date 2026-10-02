"""Validated paper settings shared by HTML print and an ordered XLSX export."""
from io import BytesIO
from math import floor

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

PAPERS = {"A4": (210, 297, "9"), "Letter": (215.9, 279.4, "1"),
          "Legal": (215.9, 355.6, "5"), "Long Bond": (215.9, 330.2, "14")}
TEXT_SIZES = ("11", "12", "14")


def print_settings(values):
    paper = values.get("paper") or "A4"
    orientation = values.get("orientation") or "landscape"
    text_size = int(values.get("text_size") or "11")
    width, height, excel_size = PAPERS[paper]
    if orientation == "landscape":
        width, height = height, width
    dates_per_sheet = max(4, min(12, floor((width - 16) * .47 / (14 * text_size / 11))))
    return dict(paper=paper, orientation=orientation, text_size=text_size,
                width_mm=width, height_mm=height, excel_size=excel_size,
                dates_per_sheet=dates_per_sheet)


def checklist_xlsx(context):
    settings = context["print_settings"]
    book = Workbook()
    book.remove(book.active)
    line = Side(style="thin", color="666666")
    for number, dates in enumerate(context["date_batches"], 1):
        sheet = book.create_sheet(f"Checklist {number}")
        count = 5 + len(dates)
        def text(row, column, value):
            cell = sheet.cell(row, column, str(value))
            cell.data_type = "s"  # Literal strings, even when starting with =/+/-/@.
            return cell
        text(1, 1, f'{context["tenant_name"]} — {context["campus_name"]}')
        text(2, 1, f'{context["selected_academic_year"].code} / {context["selected_term"].name} — '
             f'{context["selected_month"]:%B %Y} — {context["selected_day_group_label"]}')
        text(3, 1, "A/N: absent; L: late minutes; E: early minutes. Blank applicable cells remain unverified.")
        for row in (1, 2, 3):
            sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=count)
            sheet.row_dimensions[row].height = 30
        for column, label in enumerate(("Course", "Section", "Faculty", "Room", "Scheduled hours"), 1):
            text(4, column, label)
        for column, day in enumerate(dates, 6):
            sheet.cell(4, column, day).number_format = "ddd\nmmm d"
        current = 5
        previous_group = None
        for row in context["rows"]:
            if row.time_group_key != previous_group:
                text(current, 1, f'{row.start_time:%H:%M}–{row.end_time:%H:%M}')
                sheet.merge_cells(start_row=current, start_column=1, end_row=current, end_column=count)
                sheet.cell(current, 1).fill = PatternFill("solid", fgColor="E8EEE8")
                current += 1
                previous_group = row.time_group_key
            values = ("\n".join(f'{o.course.code} — {o.course.title}' for o in row.linked_offerings),
                      "\n".join(o.section.code for o in row.linked_offerings), row.faculty_label, row.room_label)
            for column, value in enumerate(values, 1):
                text(current, column, value)
            sheet.cell(current, 5, float(row.duration_hours)).number_format = "0.00"
            for column, day in enumerate(dates, 6):
                cell = sheet.cell(current, column)
                if day not in row.applicable_dates:
                    cell.value = "N/A"
                    cell.fill = PatternFill("solid", fgColor="EEEEEE")
            sheet.row_dimensions[current].height = max(44, settings["text_size"] * (2 + len(row.linked_offerings)))
            current += 1
        for cells in sheet.iter_rows():
            for cell in cells:
                cell.font = Font(name="Arial", size=settings["text_size"], bold=cell.row <= 4)
                cell.alignment = Alignment(wrap_text=True, vertical="center")
                if cell.row >= 4:
                    cell.border = Border(left=line, right=line, top=line, bottom=line)
        usable_width = settings["width_mm"] - 24
        # Size columns to the chosen paper rather than scaling all text down.
        char_mm = 2.2 * settings["text_size"] / 11
        for column, fraction in zip("ABCDE", (.17, .11, .14, .07, .08)):
            sheet.column_dimensions[column].width = usable_width * fraction / char_mm
        from openpyxl.utils import get_column_letter
        for column in range(6, count + 1):
            sheet.column_dimensions[get_column_letter(column)].width = usable_width * .43 / len(dates) / char_mm
        sheet.freeze_panes = "F5"
        sheet.print_title_rows = "1:4"
        sheet.print_options.horizontalCentered = True
        sheet.page_setup.orientation = settings["orientation"]
        sheet.page_setup.paperSize = settings["excel_size"]
        sheet.page_setup.scale = 100
        sheet.sheet_properties.pageSetUpPr.fitToPage = False
        sheet.print_area = f"A1:{get_column_letter(count)}{max(4, current - 1)}"
    output = BytesIO()
    book.save(output)
    return output.getvalue()
