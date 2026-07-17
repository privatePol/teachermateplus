from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape
from django.utils import timezone

from apps.accounts.models import User
from apps.imports.models import ImportBatch
from apps.imports.services import BulkImportService
from apps.rbac.models import Permission


class ImportSafetyGuidanceTests(TestCase):
    def setUp(self):
        permission_codes = {
            "admin_portal.access",
            "import_batches.read",
            *[
                BulkImportService.required_permission(import_type)
                for import_type in BulkImportService.list_import_types()
            ],
        }
        for code in permission_codes:
            module, action = code.split(".", 1)
            Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action},
            )

        self.admin = User.objects.create_superuser(
            username="import_safety_admin",
            email="import_safety_admin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def test_import_batch_list_explains_validation_and_duplicate_rules(self):
        response = self.client.get(reverse("admin_portal:import_batch_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Import Safety Measures")
        self.assertContains(response, "Uploading validates and stages records without changing operational data.")
        expected_importers = [
            (ImportBatch.ImportType.SECTIONS, "Sections"),
            (ImportBatch.ImportType.COURSES, "Courses"),
            (ImportBatch.ImportType.STUDENTS, "Students"),
            (ImportBatch.ImportType.COURSE_OFFERINGS, "Course Offerings"),
            (ImportBatch.ImportType.FACULTY_ASSIGNMENTS, "Faculty Assignments"),
            (ImportBatch.ImportType.FACULTY_USERS, "Faculty Users"),
            (ImportBatch.ImportType.ENROLLMENT, "Enrollment"),
        ]
        cards = response.context["import_cards"]
        self.assertEqual(
            [(card["import_type"], card["label"]) for card in cards],
            expected_importers,
        )
        self.assertContains(response, "Duplicate rule:", count=len(expected_importers))
        rendered = response.content.decode(response.charset)
        for card in cards:
            with self.subTest(import_type=card["import_type"]):
                duplicate_rule = card["safety"].get("duplicate_rule", "").strip()
                self.assertTrue(duplicate_rule)
                self.assertIn(escape(card["label"]), rendered)
                self.assertIn(escape(duplicate_rule), rendered)
        self.assertContains(response, "Existing sections and repeated section rows are skipped")
        self.assertContains(response, "UPSERT updates an existing student")
        self.assertContains(response, "course/section offering is rejected as a duplicate")

    def test_every_upload_page_shows_common_and_specific_safety_guidance(self):
        for import_type in BulkImportService.list_import_types():
            with self.subTest(import_type=import_type):
                slug = BulkImportService.import_type_to_slug(import_type)
                response = self.client.get(reverse("admin_portal:import_upload", args=[slug]))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Import Safety Measures")
                self.assertContains(
                    response,
                    "Uploading a CSV only validates and stages the file.",
                )
                self.assertContains(response, "Only rows marked VALID are eligible")
                self.assertContains(response, "Every successful imported row")
                self.assertContains(response, "Duplicate protection")

    def test_batch_detail_shows_matching_importer_safety_guidance(self):
        batch = ImportBatch.objects.create(
            import_type=ImportBatch.ImportType.COURSE_OFFERINGS,
            uploaded_by_user=self.admin,
            status=ImportBatch.Status.VALIDATED,
            total_rows=0,
            valid_rows=0,
            invalid_rows=0,
            expected_headers_json=[],
            actual_headers_json=[],
        )

        response = self.client.get(reverse("admin_portal:import_batch_detail", args=[batch.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Import Safety Measures")
        self.assertContains(response, "Re-uploading existing offerings does not update")
        self.assertContains(response, "A failed row is rolled back without undoing other successful rows.")

    def test_student_and_enrollment_pages_disclose_write_capabilities(self):
        student_response = self.client.get(
            reverse(
                "admin_portal:import_upload",
                args=[BulkImportService.import_type_to_slug(ImportBatch.ImportType.STUDENTS)],
            )
        )
        enrollment_response = self.client.get(
            reverse(
                "admin_portal:import_upload",
                args=[BulkImportService.import_type_to_slug(ImportBatch.ImportType.ENROLLMENT)],
            )
        )

        self.assertContains(student_response, "UPDATE and UPSERT can intentionally change existing student information.")
        self.assertContains(enrollment_response, "confirming the batch may create a missing student from the CSV")
