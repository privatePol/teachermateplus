from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.imports.models import ImportBatch
from apps.imports.services import ImportTemplateService
from apps.rbac.models import Permission


class SectionImportTemplateTests(TestCase):
    def setUp(self):
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
            ("sections.read", "sections", "read"),
            ("sections.import", "sections", "import"),
        ]:
            Permission.objects.create(code=code, module=module, action=action)
        self.admin = User.objects.create_superuser(
            username="section_import_admin",
            email="section_import_admin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def test_sections_list_shows_template_download_and_upload_actions(self):
        response = self.client.get(reverse("admin_portal:section_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Download Template")
        self.assertContains(response, reverse("admin_portal:import_template_download", args=["sections"]))
        self.assertContains(response, "Upload CSV")
        self.assertContains(response, reverse("admin_portal:import_upload", args=["sections"]))

    def test_sections_template_download_uses_sections_import_headers(self):
        response = self.client.get(reverse("admin_portal:import_template_download", args=["sections"]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        expected_header = ",".join(ImportTemplateService.get_headers(ImportBatch.ImportType.SECTIONS))
        self.assertContains(response, expected_header)
        self.assertContains(response, "section_name")
        self.assertContains(response, "year_level")
        self.assertNotContains(response, "course_code")
        self.assertNotContains(response, "schedule_text")

    def test_sections_and_course_offerings_templates_are_distinct(self):
        section_headers = ImportTemplateService.get_headers(ImportBatch.ImportType.SECTIONS)
        offering_headers = ImportTemplateService.get_headers(ImportBatch.ImportType.COURSE_OFFERINGS)

        self.assertNotEqual(section_headers, offering_headers)
        self.assertIn("section_name", section_headers)
        self.assertIn("course_code", offering_headers)
