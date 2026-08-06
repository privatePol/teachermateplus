from io import BytesIO
import base64

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User, UserSignatureCredential
from apps.accounts.services import UserSignatureService
from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission
from apps.tenants.models import Campus, Tenant


class UserSignatureTests(TestCase):
    def setUp(self):
        Permission.objects.get(code="faculty_portal.access")
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

    def _drawn_signature_data_url(self, *, empty=False, image_format="PNG"):
        buffer = BytesIO()
        image = Image.new("RGBA", (900, 300), (0, 0, 0, 0))
        if not empty:
            for x in range(100, 500):
                y = 140 + ((x // 20) % 3)
                image.putpixel((x, y), (20, 20, 20, 255))
                image.putpixel((x, y + 1), (20, 20, 20, 255))
        image.save(buffer, format=image_format)
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    def _post_drawn_signature(self, data_url, **extra):
        payload = {
            "action": "draw",
            "draw-signature_data": data_url,
            "draw-current_password": self.password,
            "draw-ownership_confirmation": "on",
        }
        payload.update(extra)
        return self.client.post(reverse("accounts:faculty_signature"), payload)

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

    def test_faculty_can_save_drawn_signature_and_transparent_margins_are_trimmed(self):
        self._enable_signature_feature()
        self.client.force_login(self.user)

        response = self._post_drawn_signature(self._drawn_signature_data_url())

        self.assertEqual(response.status_code, 302)
        credential = self.user.signature_credential
        self.assertTrue(credential.original_filename.startswith(f"drawn-signature-{self.user.id}-"))
        self.assertEqual(credential.image_format, "PNG")
        self.assertLess(credential.image_width, 900)
        self.assertLess(credential.image_height, 300)
        self.assertTrue(
            AuditLog.objects.filter(
                actor_user=self.user,
                action="CREATE_SIGNATURE",
                metadata_json__source="DRAW",
            ).exists()
        )

    def test_draw_signature_page_exposes_touch_canvas_preview_and_confirmation(self):
        self._enable_signature_feature()
        self.client.force_login(self.user)

        response = self.client.get(reverse("accounts:faculty_signature"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="signature-canvas"')
        self.assertContains(response, 'id="signature-undo"')
        self.assertContains(response, 'id="signature-clear"')
        self.assertContains(response, 'id="signature-preview-button"')
        self.assertContains(response, 'id="signature-cancel"')
        self.assertContains(response, "pointerdown")
        self.assertContains(response, "authorize TeacherMate+ to use it")

    def test_drawn_signature_rejects_empty_canvas(self):
        self._enable_signature_feature()
        self.client.force_login(self.user)

        response = self._post_drawn_signature(self._drawn_signature_data_url(empty=True))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draw a signature before saving.")
        self.assertFalse(hasattr(self.user, "signature_credential"))

    def test_drawn_signature_rejects_invalid_base64(self):
        self._enable_signature_feature()
        self.client.force_login(self.user)

        response = self._post_drawn_signature("data:image/png;base64,not-valid-%%%")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Drawn signature data is invalid.")

    def test_drawn_signature_rejects_oversized_payload(self):
        self._enable_signature_feature()
        self.client.force_login(self.user)

        response = self._post_drawn_signature("data:image/png;base64," + ("A" * (3 * 1024 * 1024)))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserSignatureCredential.objects.filter(user=self.user).exists())

    def test_drawn_signature_rejects_invalid_image_bytes(self):
        self._enable_signature_feature()
        self.client.force_login(self.user)
        invalid_data = "data:image/png;base64," + base64.b64encode(b"not-an-image").decode("ascii")

        response = self._post_drawn_signature(invalid_data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Uploaded file is not a valid image.")

    def test_upload_does_not_trust_spoofed_mime_type(self):
        self._enable_signature_feature()
        self.client.force_login(self.user)
        buffer = BytesIO()
        Image.new("RGB", (100, 40), "white").save(buffer, format="GIF")
        spoofed = SimpleUploadedFile("signature.png", buffer.getvalue(), content_type="image/png")

        response = self.client.post(
            reverse("accounts:faculty_signature"),
            {
                "action": "upload",
                "upload-current_password": self.password,
                "upload-signature_file": spoofed,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use PNG or JPG/JPEG")

    def test_draw_endpoint_ignores_forged_faculty_and_scope_ids(self):
        self._enable_signature_feature()
        other = User.objects.create_user(
            username="otherfaculty",
            email="otherfaculty@ncba.edu.ph",
            password=self.password,
        )
        self.client.force_login(self.user)

        response = self._post_drawn_signature(
            self._drawn_signature_data_url(),
            faculty_id=str(other.id),
            tenant_id="999999",
            campus_id="999999",
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.user.signature_credential.has_signature)
        self.assertFalse(hasattr(other, "signature_credential"))

    def test_faculty_cannot_preview_another_tenant_users_signature(self):
        self._enable_signature_feature()
        UserSignatureService.store_signature(
            user=self.user,
            uploaded_file=self._signature_upload(),
            actor=self.user,
        )
        other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER", name="Other Campus")
        other = User.objects.create_superuser(
            username="otherowner",
            email="otherowner@example.edu",
            password=self.password,
        )
        other.default_tenant = other_tenant
        other.default_campus = other_campus
        other.must_change_password = False
        other.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        other.privacy_consent_at = timezone.now()
        other.save()
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY,
            True,
            tenant_id=other_tenant.id,
            value_type="BOOL",
        )
        self.client.force_login(other)

        response = self.client.get(reverse("accounts:faculty_signature_preview"))

        self.assertEqual(response.status_code, 404)

    def test_anonymous_user_cannot_submit_drawn_signature(self):
        self._enable_signature_feature()

        response = self.client.post(
            reverse("accounts:faculty_signature"),
            {"action": "draw", "draw-signature_data": self._drawn_signature_data_url()},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(UserSignatureCredential.objects.filter(user=self.user).exists())

    def test_failed_drawn_replacement_preserves_previous_signature(self):
        self._enable_signature_feature()
        original = UserSignatureService.store_signature(
            user=self.user,
            uploaded_file=self._signature_upload(color=(10, 20, 30, 255)),
            actor=self.user,
        )
        original_hash = original.content_sha256
        self.client.force_login(self.user)

        response = self._post_drawn_signature("data:image/png;base64," + base64.b64encode(b"broken").decode("ascii"))

        self.assertEqual(response.status_code, 200)
        original.refresh_from_db()
        self.assertTrue(original.has_signature)
        self.assertEqual(original.content_sha256, original_hash)

    def test_drawn_signature_replacement_and_deletion_are_audited(self):
        self._enable_signature_feature()
        UserSignatureService.store_signature(
            user=self.user,
            uploaded_file=self._signature_upload(),
            actor=self.user,
        )
        self.client.force_login(self.user)

        replace_response = self._post_drawn_signature(self._drawn_signature_data_url())
        delete_response = self.client.post(
            reverse("accounts:faculty_signature"),
            {"action": "delete", "delete-current_password": self.password},
        )

        self.assertEqual(replace_response.status_code, 302)
        self.assertEqual(delete_response.status_code, 302)
        self.assertTrue(AuditLog.objects.filter(actor_user=self.user, action="REPLACE_SIGNATURE").exists())
        self.assertTrue(AuditLog.objects.filter(actor_user=self.user, action="REMOVE_SIGNATURE").exists())
        self.user.signature_credential.refresh_from_db()
        self.assertFalse(self.user.signature_credential.has_signature)
