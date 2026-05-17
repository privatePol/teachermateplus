from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.accounts.forms import PrivacyConsentForm
from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission


class PrivacyConsentFormTests(TestCase):
    def test_confirmation_phrase_must_match(self):
        form = PrivacyConsentForm(data={"consent": "on", "confirmation_phrase": "I AGREE"})

        self.assertFalse(form.is_valid())
        self.assertIn("confirmation_phrase", form.errors)

    def test_confirmation_phrase_accepts_exact_phrase(self):
        form = PrivacyConsentForm(data={"consent": "on", "confirmation_phrase": "I CONSENT"})

        self.assertTrue(form.is_valid())


class PrivacyConsentViewTests(TestCase):
    def setUp(self):
        Permission.objects.create(code="faculty_portal.access", module="faculty_portal", action="access")
        self.user = User.objects.create_superuser(
            username="facultytester",
            email="facultytester@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)

    def test_faculty_privacy_consent_requires_typed_confirmation(self):
        response = self.client.post(
            reverse("accounts:faculty_privacy_consent"),
            {"consent": "on", "confirmation_phrase": "I AGREE"},
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.privacy_consent_at)
        self.assertContains(response, "Type &quot;I CONSENT&quot; exactly to continue.")

    def test_faculty_privacy_consent_records_when_confirmation_matches(self):
        response = self.client.post(
            reverse("accounts:faculty_privacy_consent"),
            {"consent": "on", "confirmation_phrase": "I CONSENT"},
        )

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.privacy_consent_version,
            getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
        )
        self.assertIsNotNone(self.user.privacy_consent_at)
        self.assertTrue(
            AuditLog.objects.filter(
                action="PRIVACY_CONSENT_ACCEPTED",
                entity_type="User",
                entity_id=str(self.user.id),
            ).exists()
        )
