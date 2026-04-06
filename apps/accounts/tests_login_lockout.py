from datetime import timedelta

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PortalLoginLockoutState, User
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission


class LoginLockoutTests(TestCase):
    def setUp(self):
        Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
            is_active=True,
        )
        Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
            is_active=True,
        )
        Permission.objects.create(
            code="users.read",
            module="users",
            action="read",
            is_active=True,
        )
        Permission.objects.create(
            code="users.update",
            module="users",
            action="update",
            is_active=True,
        )
        self.password = "LockoutPass123!"
        self.user = User.objects.create_superuser(
            username="securityadmin",
            email="securityadmin@ncba.edu.ph",
            password=self.password,
        )
        self.user.must_change_password = False
        self.user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.user.privacy_consent_at = timezone.now()
        self.user.save(
            update_fields=[
                "must_change_password",
                "privacy_consent_version",
                "privacy_consent_at",
            ]
        )

    def _configure_lockout(self, *, enabled=True, max_attempts=2, window_minutes=15, duration_minutes=15):
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_LOCKOUT_ENABLED_KEY,
            enabled,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_LOCKOUT_MAX_ATTEMPTS_KEY,
            max_attempts,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_LOCKOUT_WINDOW_MINUTES_KEY,
            window_minutes,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_LOCKOUT_DURATION_MINUTES_KEY,
            duration_minutes,
            value_type="INT",
            is_active=True,
        )

    def test_admin_login_locks_after_reaching_threshold(self):
        self._configure_lockout(max_attempts=2)
        login_url = reverse("accounts:admin_login")

        response = self.client.post(login_url, {"username": self.user.username, "password": "wrong-pass"})
        self.assertContains(response, "Invalid username or password.", status_code=200)

        response = self.client.post(login_url, {"username": self.user.username, "password": "wrong-pass"})
        self.assertContains(response, "Too many failed login attempts.", status_code=200)

        state = PortalLoginLockoutState.objects.get(
            username=self.user.username,
            portal_code=PortalLoginLockoutState.PortalCode.ADMIN,
        )
        self.assertEqual(state.failed_attempt_count, 2)
        self.assertIsNotNone(state.locked_until)

    def test_successful_login_clears_previous_failure_count(self):
        self._configure_lockout(max_attempts=3)
        login_url = reverse("accounts:admin_login")

        self.client.post(login_url, {"username": self.user.username, "password": "wrong-pass"})
        state = PortalLoginLockoutState.objects.get(
            username=self.user.username,
            portal_code=PortalLoginLockoutState.PortalCode.ADMIN,
        )
        self.assertEqual(state.failed_attempt_count, 1)

        response = self.client.post(login_url, {"username": self.user.username, "password": self.password})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_portal:dashboard"))

        state.refresh_from_db()
        self.assertEqual(state.failed_attempt_count, 0)
        self.assertIsNone(state.locked_until)

    def test_lockout_is_portal_specific_and_expires_cleanly(self):
        self._configure_lockout(max_attempts=1)
        admin_login_url = reverse("accounts:admin_login")
        faculty_login_url = reverse("accounts:faculty_login")

        self.client.post(admin_login_url, {"username": self.user.username, "password": "wrong-pass"})
        admin_state = PortalLoginLockoutState.objects.get(
            username=self.user.username,
            portal_code=PortalLoginLockoutState.PortalCode.ADMIN,
        )
        self.assertIsNotNone(admin_state.locked_until)

        faculty_response = self.client.post(
            faculty_login_url,
            {"username": self.user.username, "password": self.password},
        )
        self.assertEqual(faculty_response.status_code, 302)
        self.assertEqual(faculty_response.url, reverse("faculty_portal:dashboard"))
        self.client.logout()

        admin_state.locked_until = timezone.now() - timedelta(minutes=1)
        admin_state.save(update_fields=["locked_until"])

        response = self.client.post(admin_login_url, {"username": self.user.username, "password": self.password})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("admin_portal:dashboard"))

    def test_admin_can_view_and_unlock_lockout_record(self):
        self._configure_lockout(max_attempts=1)
        self.client.post(reverse("accounts:admin_login"), {"username": self.user.username, "password": "wrong-pass"})
        state = PortalLoginLockoutState.objects.get(
            username=self.user.username,
            portal_code=PortalLoginLockoutState.PortalCode.ADMIN,
        )

        self.client.force_login(self.user)
        list_response = self.client.get(reverse("admin_portal:login_lockout_list"))
        self.assertContains(list_response, "Login Lockout Monitor", status_code=200)
        self.assertContains(list_response, self.user.username, status_code=200)

        unlock_response = self.client.post(
            reverse("admin_portal:login_lockout_unlock", args=[state.id]),
            {"next": reverse("admin_portal:login_lockout_list")},
        )
        self.assertEqual(unlock_response.status_code, 302)
        self.assertEqual(unlock_response.url, reverse("admin_portal:login_lockout_list"))

        state.refresh_from_db()
        self.assertEqual(state.failed_attempt_count, 0)
        self.assertIsNone(state.locked_until)
