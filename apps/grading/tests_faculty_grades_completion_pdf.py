"""PDF content and geometry regressions using sanitized, unsaved report records."""

from copy import deepcopy
from datetime import datetime
from io import BytesIO
from unittest.mock import patch
from uuid import UUID

from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from pypdf import PdfReader
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

from apps.accounts.models import User
from apps.accounts.services import UserSignatureService
from apps.academics.models import AcademicYear, Term
from apps.core.services.settings import SystemSettingService
from apps.grading.models import FacultyFinalClearanceReport
from apps.grading.reporting import FacultyFinalClearanceReportService
from apps.tenants.models import Campus, Tenant


class FacultyGradesCompletionPdfTests(SimpleTestCase):
    def setUp(self):
        tenant = Tenant(id=101, code="PDF-TEST", name="Example College")
        campus = Campus(id=102, tenant=tenant, code="EXAMPLE", name="Example Campus")
        year = AcademicYear(id=103, tenant=tenant, code="AY2627")
        term = Term(id=104, tenant=tenant, academic_year=year, code="1ST-SEM", name="First Semester")
        faculty = User(id=105, username="example_faculty", first_name="Example", last_name="Faculty")
        self.report = FacultyFinalClearanceReport(
            id=106, tenant=tenant, campus=campus, academic_year=year, term=term,
            faculty_user=faculty, generated_by_user=faculty,
            report_uuid=UUID("12345678-1234-4234-8234-123456789012"),
            reference_no="FCR-1ST-SEM-105-20261025181117",
            verification_code="ABCDEF1234567890", clearance_status="CLEARED",
            total_assigned_courses=5, complete_courses=5, incomplete_courses=0,
            created_at=timezone.make_aware(datetime(2026, 10, 25, 18, 11)),
            snapshot_json={"clearance_status": "CLEARED", "rows": self.sample_rows()},
        )

    @staticmethod
    def sample_rows():
        # Same five-course label lengths as the supplied sample; no student data.
        labels = [
            ("A132-ITAPPS", "IT Application Tools in Business", "BSBA-FM 1-BA1_IRREG_TRANSFEREE"),
            ("AE227-SASA", "Statistical Analysis with Software Application", "BSBA HRM 2-BA2_HRM"),
            ("IS313-MIS", "Management Information System", "BSIS 3-3_BSIS"),
            ("IS322-ITAUDIT", "Elective 2 (IT Audit Controls)", "BSIS 3-3_BSIS"),
            ("IS323-ITINFRA", "IT Infrastructure and Network Technologies", "BSIS 3-3_BSIS"),
        ]
        return [dict(
            offering_id=i, course_code=code, course_title=title, section_code=section,
            campus_code="EXAMPLE", eligible_student_count=16, period_labels=["PG", "MG", "PFG", "FG"],
            final_submission_status="Submitted", encoding_status="COMPLETE", final_grade_missing_count=0,
            notes=["SNAPSHOT-ONLY-NOTE"],
        ) for i, (code, title, section) in enumerate(labels, 1)]

    def build_pdf(self):
        def print_setting(key, *, default=None, **kwargs):
            return {"PRINT_HEADER_SCHOOL_NAME": "National College of Business and Arts",
                    "PRINT_HEADER_SCHOOL_ADDRESS": "Example Campus | Test Address"}.get(key, default)

        with patch.object(SystemSettingService, "get", side_effect=print_setting):
            return FacultyFinalClearanceReportService.build_pdf_bytes(report_obj=self.report)

    @staticmethod
    def text(reader):
        return " ".join(" ".join(page.extract_text().split()) for page in reader.pages)

    @staticmethod
    def text_runs(page):
        runs = []
        font_name, font_size = "Helvetica", 0
        text_matrix = [1, 0, 0, 1, 0, 0]
        leading = 0

        def visitor(operator, args, cm, tm):
            nonlocal font_name, font_size, text_matrix, leading
            if operator == b"BT":
                text_matrix = [1, 0, 0, 1, 0, 0]
            elif operator == b"Tm":
                text_matrix = [float(value) for value in args]
            elif operator == b"TL":
                leading = float(args[0])
            elif operator == b"Td":
                dx, dy = map(float, args)
                text_matrix[4] += dx * text_matrix[0] + dy * text_matrix[2]
                text_matrix[5] += dx * text_matrix[1] + dy * text_matrix[3]
            elif operator == b"T*":
                # PDF TL is in user units. Some pypdf versions scale this by
                # font size in their extracted matrix, overstating line gaps.
                text_matrix[4] -= leading * text_matrix[2]
                text_matrix[5] -= leading * text_matrix[3]
            if operator == b"Tf":
                font = page["/Resources"]["/Font"][args[0]]
                font_name = str(font["/BaseFont"]).lstrip("/")
                font_size = float(args[1])
            if operator == b"Tj":
                value = args[0]
                text = value.decode("cp1252") if isinstance(value, bytes) else str(value)
                if not text.strip() or text.strip() == "NCBA":
                    return
                # Actual PDF text origin after centering and line advances,
                # transformed by pypdf's graphics matrix into page user space.
                x = text_matrix[4] * cm[0] + text_matrix[5] * cm[2] + cm[4]
                y = text_matrix[4] * cm[1] + text_matrix[5] * cm[3] + cm[5]
                runs.append((text.strip(), x, y, font_size, font_name))

        page.extract_text(visitor_operand_before=visitor)
        return runs

    def assert_content_bounds(self, reader):
        for page in reader.pages:
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            for text, x, y, size, font in self.text_runs(page):
                if text.startswith("Page "):
                    continue
                self.assertGreaterEqual(size, 7.5, text)
                self.assertGreaterEqual(x, 15 * mm - 0.1, text)
                self.assertLessEqual(y, height - 15 * mm + 0.1, text)
                self.assertGreaterEqual(y, 15 * mm, text)
                for line in text.splitlines():
                    self.assertLessEqual(x + stringWidth(line, font, size), width - 15 * mm + 0.1, line)

    def test_exact_report_text_and_removed_printed_columns(self):
        reader = PdfReader(BytesIO(self.build_pdf()))
        self.assertEqual(reader.metadata.title, "Faculty Grades Completion Report")
        text = self.text(reader)
        for required in (
            "Faculty Grades Completion Report", "A. FACULTY OVERVIEW",
            "B. COURSE GRADE COMPLETION STATUS", "C. CONTROL AND VERIFICATION",
            "Final Submission", "Completion Status", "Name and Signature",
            "A course is marked COMPLETE when all required student grades are recorded, period submissions are completed, and final grades are available.",
            "This report summarizes the faculty member's grade completion status for the selected academic period. It serves as a supporting attachment to the official Faculty Clearance Form and does not constitute clearance approval. Authorized personnel may verify its authenticity using the Reference No. and Verification Code.",
        ):
            self.assertIn(required, text)
        for removed in (
            "Field", "Eligible", "Notes", "SNAPSHOT-ONLY-NOTE", "Overall Status", "CLEARED",
            "FACULTY CLEARANCE OVERVIEW", "B. COURSE CLEARANCE STATUS", "D. FACULTY SIGNATURE",
            "No stored signature on file", "Printed by Faculty", "official NCBA faculty clearance record",
        ):
            self.assertNotIn(removed, text)

    def test_overall_display_does_not_modify_status_identifiers_or_snapshot(self):
        for status, display in (("CLEARED", "COMPLETE"), ("NOT_CLEARED", "INCOMPLETE")):
            with self.subTest(status=status):
                self.report.clearance_status = status
                before = deepcopy({key: value for key, value in self.report.__dict__.items() if key != "_state"})
                text = self.text(PdfReader(BytesIO(self.build_pdf())))
                self.assertIn("Overall Completion " + display, text)
                self.assertEqual({key: value for key, value in self.report.__dict__.items() if key != "_state"}, before)
                self.assertIn(self.report.reference_no, text)
                self.assertIn(self.report.verification_code, text)
                self.assertIn(str(self.report.report_uuid), text)

    def test_five_course_sample_is_one_readable_a4_portrait_page(self):
        reader = PdfReader(BytesIO(self.build_pdf()))
        self.assertEqual(len(reader.pages), 1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 210 * mm, places=3)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.height), 297 * mm, places=3)
        self.assert_content_bounds(reader)
        runs = self.text_runs(reader.pages[0])
        self.assertEqual(sum(text == "COMPLETE" for text, *_ in runs), 6)
        self.assertEqual(sum(text == "Name and Signature" for text, *_ in runs), 1)

    def test_generated_timestamp_is_centered_on_the_actual_pdf_page(self):
        reader = PdfReader(BytesIO(self.build_pdf()))
        text, x, y, size, font = next(run for run in self.text_runs(reader.pages[0]) if run[0].startswith("Generated:"))
        self.assertEqual(text, "Generated: 2026-10-25 18:11")
        self.assertAlmostEqual(x + stringWidth(text, font, size) / 2, float(reader.pages[0].mediabox.width) / 2, places=2)

    def test_long_labels_wrap_within_page_bounds(self):
        self.report.faculty_user.first_name = "Example Faculty " * 5
        self.report.campus.name = "Example Campus With A Long Name " * 3
        self.report.snapshot_json["rows"][0].update(
            course_title="Research <Methods> & Applied Analysis " * 6,
            section_code="LONG_SECTION_" * 8,
        )
        reader = PdfReader(BytesIO(self.build_pdf()))
        text = self.text(reader)
        self.assertIn("Research <Methods> & Applied Analysis", text)
        self.assertEqual("".join(text.split()).count("LONG_SECTION_" * 8), 1)
        self.assert_content_bounds(reader)

    def test_large_course_list_paginates_with_repeated_headers_and_complete_rows(self):
        rows = []
        for index in range(60):
            row = deepcopy(self.sample_rows()[index % 5])
            row["course_code"] = f"COURSE-{index:03d}"
            rows.append(row)
        self.report.snapshot_json["rows"] = rows
        self.report.total_assigned_courses = self.report.complete_courses = len(rows)
        reader = PdfReader(BytesIO(self.build_pdf()))
        self.assertGreater(len(reader.pages), 1)
        full_text = self.text(reader)
        for index in range(60):
            self.assertIn(f"COURSE-{index:03d}", full_text)
        for page in reader.pages:
            text = " ".join(page.extract_text().split())
            if "COURSE-" in text:
                self.assertIn("Course Section Final Submission Completion Status", text)
        self.assertIn("Name and Signature", reader.pages[-1].extract_text())
        self.assert_content_bounds(reader)

    def test_manual_line_never_reads_or_logs_a_stored_signature(self):
        with patch.object(UserSignatureService, "get_active_credential") as credential, \
             patch.object(UserSignatureService, "log_signature_usage") as usage:
            text = self.text(PdfReader(BytesIO(self.build_pdf())))
        credential.assert_not_called()
        usage.assert_not_called()
        self.assertEqual(text.count("Name and Signature"), 1)

    def test_signature_line_stays_with_its_label_at_a_page_boundary(self):
        for count in range(5, 16):
            with self.subTest(courses=count):
                self.report.snapshot_json["rows"] = [deepcopy(self.sample_rows()[i % 5]) for i in range(count)]
                reader = PdfReader(BytesIO(self.build_pdf()))
                page = reader.pages[-1]
                self.assertIn("C. CONTROL AND VERIFICATION", page.extract_text())
                self.assertIn(self.report.reference_no, page.extract_text())
                label = next(run for run in self.text_runs(page) if run[0] == "Name and Signature")
                lines = []

                def operand(operator, args, cm, tm):
                    if operator in (b"m", b"l"):
                        x, y = float(args[0]), float(args[1])
                        lines.append((operator, x * cm[0] + y * cm[2] + cm[4], x * cm[1] + y * cm[3] + cm[5]))

                page.extract_text(visitor_operand_before=operand)
                signature_lines = [(a, b) for a, b in zip(lines, lines[1:]) if a[0] == b"m" and b[0] == b"l"
                                   and abs(abs(a[1] - b[1]) - 65 * mm) < 0.1 and abs(a[2] - b[2]) < 0.1]
                self.assertEqual(len(signature_lines), 1)
                self.assertGreater(signature_lines[0][0][2], label[2])
                self.assertLess(signature_lines[0][0][2] - label[2], 20)

    def test_qr_payload_and_printed_identifiers_preserve_both_verification_modes(self):
        for site_url in ("", "https://grades.example.test"):
            with self.subTest(site_url=site_url), override_settings(SITE_URL=site_url):
                expected = FacultyFinalClearanceReportService.verification_lookup_value(report_obj=self.report)
                drawing = FacultyFinalClearanceReportService.verification_qr_drawing(report_obj=self.report)
                self.assertEqual(drawing.contents[0].value, expected)
                self.assertAlmostEqual(drawing.width, 28 * mm)
                with patch.object(FacultyFinalClearanceReportService, "verification_qr_drawing", wraps=FacultyFinalClearanceReportService.verification_qr_drawing) as qr:
                    text = self.text(PdfReader(BytesIO(self.build_pdf())))
                qr.assert_called_once_with(report_obj=self.report)
                self.assertIn("Scan to verify this report.", text)
                self.assertIn(self.report.reference_no, expected)
                self.assertIn(self.report.verification_code, expected)
                if not site_url:
                    self.assertIn(str(self.report.report_uuid), expected)
