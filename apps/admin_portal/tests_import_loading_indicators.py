from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.imports.models import ImportBatch, ImportBatchRow
from apps.rbac.models import Permission


class ImportLoadingIndicatorTests(TestCase):
    def setUp(self):
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
            ("import_batches.read", "import_batches", "read"),
            ("sections.import", "sections", "import"),
        ]:
            Permission.objects.create(code=code, module=module, action=action)
        self.admin = User.objects.create_superuser(
            username="import_loading_admin",
            email="import_loading_admin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def test_upload_validation_page_has_progress_indicator(self):
        response = self.client.get(reverse("admin_portal:import_upload", args=["sections"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Validating CSV records")
        self.assertContains(response, "progress-bar-striped progress-bar-animated")
        self.assertContains(response, "csv-upload-overlay")

    def test_confirm_import_page_has_progress_indicator(self):
        batch = ImportBatch.objects.create(
            import_type=ImportBatch.ImportType.SECTIONS,
            uploaded_by_user=self.admin,
            status=ImportBatch.Status.VALIDATED,
            total_rows=1,
            valid_rows=1,
            invalid_rows=0,
            expected_headers_json=["tenant_code"],
            actual_headers_json=["tenant_code"],
            original_filename="sections.csv",
        )
        ImportBatchRow.objects.create(
            batch=batch,
            row_number=2,
            row_status=ImportBatchRow.RowStatus.VALID,
            raw_data_json={"tenant_code": "NCBA"},
            normalized_data_json={"tenant_id": 1},
        )

        response = self.client.get(reverse("admin_portal:import_batch_detail", args=[batch.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Importing validated records")
        self.assertContains(response, "progress-bar-striped progress-bar-animated")
        self.assertContains(response, "import-confirm-overlay")
