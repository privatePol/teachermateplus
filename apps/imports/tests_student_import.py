from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.imports.models import ImportBatch
from apps.imports.services import BulkImportService, ImportTemplateService
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


User = get_user_model()


class StudentImportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="student_import_admin",
            email="student_import_admin@example.edu",
            password="testpass123",
        )
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-02", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIT",
            name="BSIT",
        )
        self.second_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSBA",
            name="BSBA",
        )
        self.request = SimpleNamespace(
            scope={
                "tenant_id": self.tenant.id,
                "campus_id": self.campus.id,
                "tenant_ids": [self.tenant.id],
                "campus_ids": [self.campus.id],
            }
        )

    def _upload_csv(self, rows):
        content = "\n".join([",".join(ImportTemplateService.get_headers(ImportBatch.ImportType.STUDENTS)), *rows])
        return SimpleUploadedFile("students.csv", content.encode("utf-8"), content_type="text/csv")

    def test_students_template_supports_create_update_and_upsert(self):
        headers = ImportTemplateService.get_headers(ImportBatch.ImportType.STUDENTS)

        self.assertEqual(headers[0], "row_action")
        self.assertIn("student_no", headers)
        self.assertIn("program_code", headers)
        self.assertIn("year_level", headers)
        self.assertIn("official_email_verified", headers)
        self.assertEqual(BulkImportService.required_permission(ImportBatch.ImportType.STUDENTS), "students.import")

    def test_student_import_upsert_creates_missing_student(self):
        batch = BulkImportService.validate_and_stage_upload(
            import_type=ImportBatch.ImportType.STUDENTS,
            uploaded_file=self._upload_csv(
                [
                    "UPSERT,NCBA,NCBA-02,COLLEGE,BSIT,2025-10102,DELA CRUZ,JUAN,SANTOS,juan@example.edu,TRUE,M,1,ACTIVE,TRUE",
                ]
            ),
            user=self.user,
            request=self.request,
        )

        self.assertEqual(batch.valid_rows, 1)
        self.assertEqual(batch.invalid_rows, 0)

        BulkImportService.confirm_batch(batch=batch, actor=self.user)

        student = Student.objects.get(tenant=self.tenant, campus=self.campus, student_no="2025-10102")
        self.assertEqual(student.department, self.department)
        self.assertEqual(student.program, self.program)
        self.assertEqual(student.year_level, "1")
        self.assertEqual(student.official_email, "juan@example.edu")
        self.assertIsNotNone(student.official_email_verified_at)

    def test_student_import_update_can_change_year_level_and_program_without_repeating_names(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-10102",
            last_name="DELA CRUZ",
            first_name="JUAN",
            year_level="1",
            status=Student.Status.ACTIVE,
        )
        batch = BulkImportService.validate_and_stage_upload(
            import_type=ImportBatch.ImportType.STUDENTS,
            uploaded_file=self._upload_csv(
                [
                    "UPDATE,NCBA,NCBA-02,,BSBA,2025-10102,,,,,,,2,ACTIVE,TRUE",
                ]
            ),
            user=self.user,
            request=self.request,
        )

        self.assertEqual(batch.valid_rows, 1)
        self.assertEqual(batch.invalid_rows, 0)

        BulkImportService.confirm_batch(batch=batch, actor=self.user)

        student.refresh_from_db()
        self.assertEqual(student.last_name, "DELA CRUZ")
        self.assertEqual(student.first_name, "JUAN")
        self.assertEqual(student.program, self.second_program)
        self.assertEqual(student.year_level, "2")
