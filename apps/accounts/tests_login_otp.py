import re

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import LoginOtpChallenge, User
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LoginOtpTests(TestCase):
    def setUp(self):
        Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
            is_active=True,
        )
        self.password = "OtpPass123!"
        self.user = User.objects.create_superuser(
            username="otpadmin",
            email="otpadmin@ncba.edu.ph",
            password=self.password,
        )
        self.user.must_change_password = False
        self.user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.user.privacy_consent_at = timezone.now()
        self.user.save(update_fields=["must_change_password", "privacy_consent_version", "privacy_consent_at"])

    def _set_otp_enabled(self, enabled=True):
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_EMAIL_OTP_ENABLED_KEY,
            enabled,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_EMAIL_OTP_EXPIRY_MINUTES_KEY,
            10,
            value_type="INT",
            is_active=True,
        )

    def test_login_continues_normally_when_email_otp_is_disabled(self):
        self._set_otp_enabled(False)

        response = self.client.post(
            reverse("accounts:admin_login"),
            {"username": self.user.username, "password": self.password},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_portal:dashboard"))
        self.assertEqual(LoginOtpChallenge.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_email_otp_enabled_sends_code_before_login_completion(self):
        self._set_otp_enabled(True)

        response = self.client.post(
            reverse("accounts:admin_login"),
            {"username": self.user.username, "password": self.password},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:admin_login_otp"))
        self.assertEqual(LoginOtpChallenge.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("NCBA EduGrade+ Login Verification", mail.outbox[0].subject)

        code_match = re.search(r"\b(\d{6})\b", mail.outbox[0].body)
        self.assertIsNotNone(code_match)
        code = code_match.group(1)

        verify_response = self.client.post(reverse("accounts:admin_login_otp"), {"otp_code": code})
        self.assertEqual(verify_response.status_code, 302)
        self.assertEqual(verify_response.url, reverse("admin_portal:dashboard"))

        challenge = LoginOtpChallenge.objects.get()
        self.assertIsNotNone(challenge.consumed_at)

    def test_invalid_email_otp_does_not_complete_login(self):
        self._set_otp_enabled(True)
        self.client.post(
            reverse("accounts:admin_login"),
            {"username": self.user.username, "password": self.password},
        )

        response = self.client.post(reverse("accounts:admin_login_otp"), {"otp_code": "000000"})

        self.assertContains(response, "The verification code is incorrect.", status_code=200)
        challenge = LoginOtpChallenge.objects.get()
        self.assertEqual(challenge.attempt_count, 1)
        self.assertIsNone(challenge.consumed_at)
