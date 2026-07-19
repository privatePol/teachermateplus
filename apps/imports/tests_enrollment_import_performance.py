from datetime import date
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.enrollment.models import Enrollment
from apps.imports.models import ImportBatch, ImportBatchRow
from apps.imports.services import BulkImportService, ImportTemplateService
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


User = get_user_model()


class EnrollmentImportPerformanceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="ENRPERF", name="Enrollment Performance Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="IT",
            name="Information Technology",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIT",
            name="BSIT",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2026-2027",
            name="Academic Year 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Semester",
            sequence_no=1,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="IT101",
            title="Introduction to IT",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1A",
            name="BSIT 1A",
        )
        self.offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )
        self.actor = User.objects.create_user(
            username="enrollment-performance-importer",
            email="enrollment-performance@example.edu",
            password="AdminPass!123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        self.request = SimpleNamespace(
            scope={
                "tenant_id": self.tenant.id,
                "campus_id": self.campus.id,
                "tenant_ids": [self.tenant.id],
                "campus_ids": [self.campus.id],
                "department_ids": [self.department.id],
            }
        )

    def _student(self, index):
        return Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no=f"2026-{index:05d}",
            last_name=f"Student {index}",
            first_name="Test",
        )

    def _row(self, student):
        values = {
            "tenant_code": self.tenant.code,
            "campus_code": self.campus.code,
            "academic_year_code": self.academic_year.code,
            "term_code": self.term.code,
            "student_no": student.student_no,
            "student_last_name": student.last_name,
            "student_first_name": student.first_name,
            "student_middle_name": "",
            "student_sex": "",
            "student_year_level": "1",
            "course_code": self.course.code,
            "section_code": self.section.code,
            "enrollment_status": "ACTIVE",
        }
        headers = ImportTemplateService.get_headers(ImportBatch.ImportType.ENROLLMENT)
        return ",".join(str(values[header]) for header in headers)

    def _upload(self, students):
        headers = ImportTemplateService.get_headers(ImportBatch.ImportType.ENROLLMENT)
        content = "\n".join([",".join(headers), *[self._row(student) for student in students]]).encode("utf-8")
        uploaded = SimpleUploadedFile("enrollment.csv", content, content_type="text/csv")
        return BulkImportService.validate_and_stage_upload(
            import_type=ImportBatch.ImportType.ENROLLMENT,
            uploaded_file=uploaded,
            user=self.actor,
            request=self.request,
        )

    @staticmethod
    def _course_offering_selects(captured):
        return [
            query["sql"]
            for query in captured.captured_queries
            if query["sql"].lstrip().upper().startswith("SELECT")
            and "course_offerings" in query["sql"].lower()
        ]

    def test_preview_preloads_offerings_once_as_row_count_increases(self):
        one_student = [self._student(1)]
        ten_students = [self._student(index) for index in range(2, 12)]

        with CaptureQueriesContext(connection) as one_queries:
            one_batch = self._upload(one_student)
        with CaptureQueriesContext(connection) as ten_queries:
            ten_batch = self._upload(ten_students)

        self.assertEqual((one_batch.valid_rows, one_batch.invalid_rows), (1, 0))
        self.assertEqual((ten_batch.valid_rows, ten_batch.invalid_rows), (10, 0))
        self.assertEqual(len(self._course_offering_selects(one_queries)), 1)
        self.assertEqual(len(self._course_offering_selects(ten_queries)), 1)

    def test_confirmation_checks_allowed_offerings_once_as_row_count_increases(self):
        batches = [
            self._upload([self._student(20)]),
            self._upload([self._student(index) for index in range(21, 31)]),
        ]

        offering_query_counts = []
        for expected_rows, batch in zip((1, 10), batches):
            with CaptureQueriesContext(connection) as captured:
                BulkImportService.confirm_batch(
                    batch=batch,
                    actor=self.actor,
                    request=self.request,
                )
            offering_query_counts.append(len(self._course_offering_selects(captured)))
            batch.refresh_from_db()
            self.assertEqual(batch.status, ImportBatch.Status.CONFIRMED)
            self.assertEqual(batch.imported_rows, expected_rows)
            self.assertEqual(
                batch.rows.filter(row_status=ImportBatchRow.RowStatus.IMPORTED).count(),
                expected_rows,
            )

        self.assertEqual(offering_query_counts, [1, 1])
        self.assertEqual(Enrollment.objects.filter(course_offering=self.offering).count(), 11)

    def test_confirmation_rejects_student_moved_outside_offering_scope_after_preview(self):
        student = self._student(35)
        batch = self._upload([student])
        other_campus = Campus.objects.create(tenant=self.tenant, code="MOVED", name="Moved Campus")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="MOVED-IT",
            name="Moved Information Technology",
        )
        other_program = Program.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            code="MOVED-BSIT",
            name="Moved BSIT",
        )
        student.campus = other_campus
        student.department = other_department
        student.program = other_program
        student.save(update_fields=["campus", "department", "program", "updated_at"])

        BulkImportService.confirm_batch(batch=batch, actor=self.actor, request=self.request)

        batch.refresh_from_db()
        row = batch.rows.get()
        self.assertEqual(batch.status, ImportBatch.Status.CONFIRM_FAILED)
        self.assertEqual(row.row_status, ImportBatchRow.RowStatus.ERROR)
        self.assertIn("campus no longer match", " ".join(row.errors_json or []))
        self.assertFalse(Enrollment.objects.filter(course_offering=self.offering, student=student).exists())

    def test_confirmation_rejects_offering_term_changed_after_preview(self):
        student = self._student(36)
        batch = self._upload([student])
        replacement_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="2ND",
            name="Second Semester",
            sequence_no=2,
        )
        self.offering.term = replacement_term
        self.offering.save(update_fields=["term", "updated_at"])

        BulkImportService.confirm_batch(batch=batch, actor=self.actor, request=self.request)

        batch.refresh_from_db()
        row = batch.rows.get()
        self.assertEqual(batch.status, ImportBatch.Status.CONFIRM_FAILED)
        self.assertEqual(row.row_status, ImportBatchRow.RowStatus.ERROR)
        self.assertIn("Offering scope changed after preview", " ".join(row.errors_json or []))
        self.assertFalse(Enrollment.objects.filter(course_offering=self.offering, student=student).exists())

    def test_preview_rejects_offering_outside_campus_scope(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="SIDE", name="Side")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="SIDE-IT",
            name="Side Information Technology",
        )
        other_program = Program.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            code="SIDE-BSIT",
            name="Side BSIT",
        )
        other_section = Section.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            code="SIDE-1A",
            name="Side 1A",
        )
        CourseOffering.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=other_section,
        )
        student = self._student(40)
        values = self._row(student).split(",")
        headers = ImportTemplateService.get_headers(ImportBatch.ImportType.ENROLLMENT)
        values[headers.index("campus_code")] = other_campus.code
        values[headers.index("section_code")] = other_section.code
        content = "\n".join([",".join(headers), ",".join(values)]).encode("utf-8")

        batch = BulkImportService.validate_and_stage_upload(
            import_type=ImportBatch.ImportType.ENROLLMENT,
            uploaded_file=SimpleUploadedFile("outside.csv", content, content_type="text/csv"),
            user=self.actor,
            request=self.request,
        )

        self.assertEqual((batch.valid_rows, batch.invalid_rows), (0, 1))
        row = batch.rows.get()
        self.assertEqual(row.row_status, ImportBatchRow.RowStatus.ERROR)
        self.assertIn("outside your scope", " ".join(row.errors_json or []))
