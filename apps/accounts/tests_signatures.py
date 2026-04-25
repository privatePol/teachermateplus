from io import BytesIO

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.accounts.services import UserSignatureService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission
from apps.tenants.models import Campus, Tenant


class UserSignatureTests(TestCase):
    def setUp(self):
        Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
            is_active=True,
        )
        Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
            is_active=True,
        )
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.password = "SignaturePass123!"
        self.user = User.objects.create_superuser(
            username="signatureowner",
            email="signatureowner@ncba.edu.ph",
            password=self.password,
        )
        self.user.default_tenant = self.tenant
        self.user.default_campus = self.campus
        self.user.must_change_password = False
        self.user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.user.privacy_consent_at = timezone.now()
        self.user.save(
            update_fields=[
                "default_tenant",
                "default_campus",
                "must_change_password",
                "privacy_consent_version",
                "privacy_consent_at",
            ]
        )

    def _enable_signature_feature(self):
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )

    def _signature_upload(self, *, name="signature.png", color=(0, 0, 0, 255)):
        buffer = BytesIO()
        Image.new("RGBA", (220, 80), color).save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_store_signature_encrypts_bytes_and_decrypts_to_png(self):
        uploaded_file = self._signature_upload(color=(20, 80, 120, 255))
        credential = UserSignatureService.store_signature(
            user=self.user,
            uploaded_file=uploaded_file,
            actor=self.user,
        )

        self.assertTrue(credential.has_signature)
        self.assertNotEqual(bytes(credential.encrypted_blob), b"")
        decrypted = UserSignatureService.decrypt_signature_bytes(credential=credential)
        self.assertTrue(decrypted.startswith(b"\x89PNG"))
        self.assertNotEqual(decrypted, bytes(credential.encrypted_blob))

    def test_faculty_signature_upload_and_preview(self):
        self._enable_signature_feature()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:faculty_signature"),
            {
                "action": "upload",
                "upload-current_password": self.password,
                "upload-signature_file": self._signature_upload(color=(80, 20, 120, 255)),
            },
        )

        self.assertEqual(response.status_code, 302)
        preview_response = self.client.get(reverse("accounts:faculty_signature_preview"))
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(preview_response["Content-Type"], "image/png")
        self.assertTrue(preview_response.content.startswith(b"\x89PNG"))
