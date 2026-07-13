from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PortalLoginLockoutState, User
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Tenant


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
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.user = User.objects.create_superuser(
            username="securityadmin",
            email="securityadmin@ncba.edu.ph",
            password=self.password,
        )
        self.user.default_tenant = self.tenant
        self.user.must_change_password = False
        self.user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.user.privacy_consent_at = timezone.now()
        self.user.save(
            update_fields=[
                "default_tenant",
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

    @override_settings(TRUSTED_PROXY_IPS=["10.0.0.0/8"])
    def test_admin_login_locks_after_reaching_threshold(self):
        self._configure_lockout(max_attempts=2)
        login_url = reverse("accounts:admin_login")

        response = self.client.post(
            login_url,
            {"username": self.user.username, "password": "wrong-pass"},
            REMOTE_ADDR="10.0.0.10",
            HTTP_X_FORWARDED_FOR="198.51.100.31",
        )
        self.assertContains(response, "Invalid username or password.", status_code=200)
        self.assertContains(response, 'class="login-attempt-message mt-4"')
        self.assertContains(response, "color: #b02a37;")
        self.assertContains(response, "Failed login attempt 1 of 2.")
        self.assertContains(response, "1 attempt remaining.")
        self.assertContains(response, "login will be temporarily locked for 15 minute(s).")

        response = self.client.post(
            login_url,
            {"username": self.user.username, "password": "wrong-pass"},
            REMOTE_ADDR="10.0.0.10",
            HTTP_X_FORWARDED_FOR="198.51.100.32",
        )
        self.assertContains(response, "Too many failed login attempts.", status_code=200)
        self.assertContains(response, "Failed login attempt 2 of 2.")
        self.assertContains(response, "No attempts remaining.")
        self.assertContains(response, "Login is temporarily locked for 15 minute(s).")

        state = PortalLoginLockoutState.objects.get(
            username=self.user.username,
            portal_code=PortalLoginLockoutState.PortalCode.ADMIN,
        )
        self.assertEqual(state.failed_attempt_count, 2)
        self.assertIsNotNone(state.locked_until)
        self.assertEqual(state.last_ip, "198.51.100.32")

        self.client.post(
            login_url,
            {"username": self.user.username, "password": "wrong-pass"},
            REMOTE_ADDR="10.0.0.10",
            HTTP_X_FORWARDED_FOR="198.51.100.33",
        )
        state.refresh_from_db()
        self.assertEqual(state.last_ip, "198.51.100.32")

    def test_faculty_login_renders_failed_attempt_details_in_red_message_box(self):
        self._configure_lockout(max_attempts=3, duration_minutes=20)

        response = self.client.post(
            reverse("accounts:faculty_login"),
            {"username": self.user.username, "password": "wrong-pass"},
        )

        self.assertContains(response, 'class="login-attempt-message mt-4"', status_code=200)
        self.assertContains(response, "color: #b02a37;")
        self.assertContains(response, "Failed login attempt 1 of 3.")
        self.assertContains(response, "2 attempts remaining.")
        self.assertContains(response, "temporarily locked for 20 minute(s).")

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

    def test_single_device_session_enforcement_signs_out_previous_browser_by_default(self):
        first_browser = Client()
        second_browser = Client()
        login_url = reverse("accounts:faculty_login")

        first_response = first_browser.post(login_url, {"username": self.user.username, "password": self.password})
        self.assertEqual(first_response.status_code, 302)
        first_session_key = first_browser.session.session_key
        self.assertTrue(Session.objects.filter(session_key=first_session_key).exists())

        second_response = second_browser.post(login_url, {"username": self.user.username, "password": self.password})
        self.assertEqual(second_response.status_code, 302)

        self.assertFalse(Session.objects.filter(session_key=first_session_key).exists())

    def test_single_device_session_enforcement_can_be_disabled_per_tenant(self):
        SystemSettingService.set(
            FeatureSettingsService.SINGLE_DEVICE_SESSION_ENFORCEMENT_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        first_browser = Client()
        second_browser = Client()
        login_url = reverse("accounts:faculty_login")

        first_response = first_browser.post(login_url, {"username": self.user.username, "password": self.password})
        self.assertEqual(first_response.status_code, 302)
        first_session_key = first_browser.session.session_key

        second_response = second_browser.post(login_url, {"username": self.user.username, "password": self.password})
        self.assertEqual(second_response.status_code, 302)

        self.assertTrue(Session.objects.filter(session_key=first_session_key).exists())

    def test_single_device_session_enforcement_uses_active_role_tenant_when_default_differs(self):
        other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        self.user.default_tenant = other_tenant
        self.user.save(update_fields=["default_tenant"])
        role = Role.objects.create(code="FACULTY", name="Faculty")
        UserRole.objects.create(user=self.user, role=role, tenant=self.tenant)
        SystemSettingService.set(
            FeatureSettingsService.SINGLE_DEVICE_SESSION_ENFORCEMENT_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        first_browser = Client()
        second_browser = Client()
        login_url = reverse("accounts:faculty_login")

        first_response = first_browser.post(login_url, {"username": self.user.username, "password": self.password})
        self.assertEqual(first_response.status_code, 302)
        first_session_key = first_browser.session.session_key

        second_response = second_browser.post(login_url, {"username": self.user.username, "password": self.password})
        self.assertEqual(second_response.status_code, 302)

        self.assertTrue(Session.objects.filter(session_key=first_session_key).exists())

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

    def test_lockout_monitor_displays_stored_ipv4_ipv6_and_null_ip_in_requested_column(self):
        now = timezone.now()
        PortalLoginLockoutState.objects.create(
            username="ipv4-user",
            portal_code=PortalLoginLockoutState.PortalCode.ADMIN,
            failed_attempt_count=1,
            last_failed_at=now,
            last_ip="198.51.100.41",
        )
        PortalLoginLockoutState.objects.create(
            username="ipv6-user",
            portal_code=PortalLoginLockoutState.PortalCode.FACULTY,
            failed_attempt_count=1,
            last_failed_at=now,
            last_ip="2001:0db8:0000:0000:0000:0000:0000:0042",
        )
        PortalLoginLockoutState.objects.create(
            username="null-ip-user",
            portal_code=PortalLoginLockoutState.PortalCode.ADMIN,
            failed_attempt_count=1,
            last_failed_at=now,
            last_ip=None,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("admin_portal:login_lockout_list"),
            HTTP_X_FORWARDED_FOR="203.0.113.70, 10.0.0.5",
            HTTP_X_REAL_IP="203.0.113.71",
        )
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertRegex(html, r"Last Failed</th>\s*<th>IP Address</th>\s*<th>Status</th>")
        self.assertContains(response, '<td data-column="ip-address">198.51.100.41</td>')
        self.assertContains(response, '<td data-column="ip-address">2001:db8::42</td>')
        self.assertContains(response, '<td data-column="ip-address">-</td>')
        self.assertNotContains(response, "203.0.113.70, 10.0.0.5")
        self.assertNotContains(response, "203.0.113.71")

        faculty_only = self.client.get(
            reverse("admin_portal:login_lockout_list"),
            {"portal": "FACULTY"},
        )
        self.assertContains(faculty_only, "ipv6-user", status_code=200)
        self.assertNotContains(faculty_only, "ipv4-user")
        self.assertNotContains(faculty_only, "null-ip-user")

    def test_lockout_monitor_ip_column_preserves_existing_scope(self):
        other_tenant = Tenant.objects.create(code="OTHER-IP", name="Other IP Tenant")
        scoped_admin = User.objects.create_user(
            username="scoped_ip_admin",
            email="scoped_ip_admin@example.com",
            password="ScopedIpAdmin123!",
            default_tenant=self.tenant,
            must_change_password=False,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        visible_user = User.objects.create_user(
            username="visible_ip_user",
            email="visible_ip_user@example.com",
            password="VisibleIpUser123!",
            default_tenant=self.tenant,
        )
        hidden_user = User.objects.create_user(
            username="hidden_ip_user",
            email="hidden_ip_user@example.com",
            password="HiddenIpUser123!",
            default_tenant=other_tenant,
        )
        role = Role.objects.create(code="IP_MONITOR", name="IP Monitor")
        for permission in Permission.objects.filter(code__in=["admin_portal.access", "users.read"]):
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(user=scoped_admin, role=role, tenant=self.tenant)
        PortalLoginLockoutState.objects.create(
            user=visible_user,
            username=visible_user.username,
            portal_code=PortalLoginLockoutState.PortalCode.ADMIN,
            failed_attempt_count=1,
            last_failed_at=timezone.now(),
            last_ip="198.51.100.51",
        )
        PortalLoginLockoutState.objects.create(
            user=hidden_user,
            username=hidden_user.username,
            portal_code=PortalLoginLockoutState.PortalCode.ADMIN,
            failed_attempt_count=1,
            last_failed_at=timezone.now(),
            last_ip="198.51.100.52",
        )
        self.client.force_login(scoped_admin)

        response = self.client.get(reverse("admin_portal:login_lockout_list"))

        self.assertContains(response, "198.51.100.51", status_code=200)
        self.assertNotContains(response, "198.51.100.52")
