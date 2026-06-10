from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.models import User
from apps.rbac.models import Permission, Role, RolePermission, UserRole


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class FacultyPasswordResetEligibilityTests(TestCase):
    def setUp(self):
        self.faculty_permission = Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
            is_active=True,
        )
        self.admin_permission = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
            is_active=True,
        )
        self.faculty_role = Role.objects.create(code="FACULTY_RESET", name="Faculty Reset")
        self.admin_role = Role.objects.create(code="ADMIN_RESET", name="Admin Reset")
        RolePermission.objects.create(role=self.faculty_role, permission=self.faculty_permission)
        RolePermission.objects.create(role=self.admin_role, permission=self.admin_permission)

    def _user(self, username, roles):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@ncba.edu.ph",
            password="CurrentPass123!",
            is_active=True,
            must_change_password=False,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        for role in roles:
            UserRole.objects.create(user=user, role=role)
        return user

    def _reset_url(self, user):
        return reverse(
            "accounts:faculty_password_reset_confirm",
            kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
                "token": default_token_generator.make_token(user),
            },
        )

    def test_faculty_only_user_can_request_and_open_reset_link(self):
        user = self._user("faculty_reset_only", [self.faculty_role])

        response = self.client.post(
            reverse("accounts:faculty_forgot_password"),
            {"identifier": user.username},
        )

        self.assertRedirects(response, reverse("accounts:faculty_forgot_password_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.client.get(self._reset_url(user)).status_code, 200)

    def test_admin_only_user_cannot_receive_or_open_faculty_reset_link(self):
        user = self._user("admin_reset_only", [self.admin_role])

        response = self.client.post(
            reverse("accounts:faculty_forgot_password"),
            {"identifier": user.username},
        )

        self.assertRedirects(response, reverse("accounts:faculty_forgot_password_done"))
        self.assertEqual(len(mail.outbox), 0)
        confirm_response = self.client.get(self._reset_url(user))
        self.assertRedirects(confirm_response, reverse("accounts:faculty_forgot_password"))

    def test_dual_access_user_can_use_faculty_reset(self):
        user = self._user("dual_reset_user", [self.faculty_role, self.admin_role])

        response = self.client.post(
            reverse("accounts:faculty_forgot_password"),
            {"identifier": user.email},
        )

        self.assertRedirects(response, reverse("accounts:faculty_forgot_password_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.client.get(self._reset_url(user)).status_code, 200)
