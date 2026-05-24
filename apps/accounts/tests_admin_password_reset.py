import re

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import LoginOtpChallenge, User
from apps.rbac.models import Permission, Role, RolePermission, UserRole


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AdminPasswordResetTests(TestCase):
    def setUp(self):
        self.admin_permission = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
            is_active=True,
        )
        self.faculty_permission = Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
            is_active=True,
        )
        admin_role = Role.objects.create(code="ADMIN_TEST", name="Admin Test")
        RolePermission.objects.create(role=admin_role, permission=self.admin_permission)
        faculty_role = Role.objects.create(code="FACULTY_TEST", name="Faculty Test")
        RolePermission.objects.create(role=faculty_role, permission=self.faculty_permission)

        self.admin_user = User.objects.create_user(
            username="resetadmin",
            email="resetadmin@ncba.edu.ph",
            password="OldAdminPass123!",
            is_active=True,
        )
        self.admin_user.must_change_password = False
        self.admin_user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.admin_user.privacy_consent_at = timezone.now()
        self.admin_user.save(update_fields=["must_change_password", "privacy_consent_version", "privacy_consent_at"])
        UserRole.objects.create(user=self.admin_user, role=admin_role)

        self.faculty_user = User.objects.create_user(
            username="facultyonly",
            email="facultyonly@ncba.edu.ph",
            password="FacultyPass123!",
            is_active=True,
        )
        UserRole.objects.create(user=self.faculty_user, role=faculty_role)

    def _code_from_email(self):
        self.assertEqual(len(mail.outbox), 1)
        code_match = re.search(r"\b(\d{6})\b", mail.outbox[0].body)
        self.assertIsNotNone(code_match)
        return code_match.group(1)

    def test_admin_forgot_password_sends_otp_for_admin_portal_user(self):
        response = self.client.post(
            reverse("accounts:admin_forgot_password"),
            {"identifier": self.admin_user.username},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:admin_password_reset_otp"))
        self.assertEqual(LoginOtpChallenge.objects.count(), 1)
        self.assertEqual(mail.outbox[0].subject, "NCBA | EduGradePlus: Admin Password Reset Code")

    def test_admin_forgot_password_does_not_send_otp_for_faculty_only_user(self):
        response = self.client.post(
            reverse("accounts:admin_forgot_password"),
            {"identifier": self.faculty_user.username},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:admin_forgot_password_done"))
        self.assertEqual(LoginOtpChallenge.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_password_reset_requires_otp_before_setting_new_password(self):
        blocked_response = self.client.get(reverse("accounts:admin_password_reset_confirm"))
        self.assertEqual(blocked_response.status_code, 302)
        self.assertEqual(blocked_response.url, reverse("accounts:admin_forgot_password"))

        self.client.post(
            reverse("accounts:admin_forgot_password"),
            {"identifier": self.admin_user.email},
        )
        code = self._code_from_email()

        verify_response = self.client.post(reverse("accounts:admin_password_reset_otp"), {"otp_code": code})
        self.assertEqual(verify_response.status_code, 302)
        self.assertEqual(verify_response.url, reverse("accounts:admin_password_reset_confirm"))

        reset_response = self.client.post(
            reverse("accounts:admin_password_reset_confirm"),
            {
                "new_password1": "NewAdminPass123!",
                "new_password2": "NewAdminPass123!",
            },
        )
        self.assertEqual(reset_response.status_code, 302)
        self.assertEqual(reset_response.url, reverse("accounts:admin_password_reset_complete"))

        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.check_password("NewAdminPass123!"))
        self.assertFalse(self.admin_user.must_change_password)
        challenge = LoginOtpChallenge.objects.get()
        self.assertIsNotNone(challenge.consumed_at)

    def test_admin_password_reset_verifies_original_reset_challenge_only(self):
        self.client.post(
            reverse("accounts:admin_forgot_password"),
            {"identifier": self.admin_user.email},
        )
        original_code = self._code_from_email()
        original_challenge = LoginOtpChallenge.objects.get()
        LoginOtpChallenge.objects.create(
            user=self.admin_user,
            portal_code=LoginOtpChallenge.PortalCode.ADMIN,
            code_hash=make_password("222222"),
            sent_to_email=self.admin_user.email,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        wrong_challenge_response = self.client.post(
            reverse("accounts:admin_password_reset_otp"),
            {"otp_code": "222222"},
        )
        self.assertContains(wrong_challenge_response, "The reset verification code is incorrect.", status_code=200)

        verify_response = self.client.post(reverse("accounts:admin_password_reset_otp"), {"otp_code": original_code})
        self.assertEqual(verify_response.status_code, 302)
        self.assertEqual(verify_response.url, reverse("accounts:admin_password_reset_confirm"))
        original_challenge.refresh_from_db()
        self.assertIsNotNone(original_challenge.consumed_at)
