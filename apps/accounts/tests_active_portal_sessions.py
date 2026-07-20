from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ActivePortalSession, User
from apps.accounts.services import UserDeactivationService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission
from apps.tenants.models import Tenant


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ActivePortalSessionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for code in ("admin_portal.access", "faculty_portal.access"):
            module, action = code.split(".", 1)
            Permission.objects.create(code=code, module=module, action=action, is_active=True)

        cls.tenant = Tenant.objects.create(code="SESSION-A", name="Session Tenant A")
        cls.other_tenant = Tenant.objects.create(code="SESSION-B", name="Session Tenant B")
        cls.password = "BoundedSessionPass123!"
        cls.user = cls._create_user("session-user", cls.tenant)
        cls.other_user = cls._create_user("other-session-user", cls.other_tenant)

    @classmethod
    def _create_user(cls, username, tenant):
        user = User.objects.create_superuser(
            username=username,
            email=f"{username}@example.edu",
            password=cls.password,
        )
        user.default_tenant = tenant
        user.must_change_password = False
        user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        user.privacy_consent_at = timezone.now()
        user.save(
            update_fields=[
                "default_tenant",
                "must_change_password",
                "privacy_consent_version",
                "privacy_consent_at",
            ]
        )
        return user

    def _login(self, client, *, user=None, portal="FACULTY"):
        user = user or self.user
        url = reverse("accounts:admin_login") if portal == "ADMIN" else reverse("faculty_portal:public_index")
        return client.post(url, {"username": user.username, "password": self.password})

    def test_first_admin_and_faculty_logins_register_the_active_session(self):
        admin_browser = Client()
        admin_response = self._login(admin_browser, portal="ADMIN")
        self.assertEqual(admin_response.status_code, 302)
        admin_key = admin_browser.session.session_key
        self.assertTrue(ActivePortalSession.objects.filter(user=self.user, session_key=admin_key).exists())

        faculty_browser = Client()
        faculty_response = self._login(faculty_browser, portal="FACULTY")
        self.assertEqual(faculty_response.status_code, 302)
        faculty_key = faculty_browser.session.session_key
        self.assertTrue(ActivePortalSession.objects.filter(user=self.user, session_key=faculty_key).exists())
        self.assertFalse(Session.objects.filter(session_key=admin_key).exists())

    def test_later_login_replaces_prior_registered_session(self):
        first_browser = Client()
        second_browser = Client()
        self._login(first_browser)
        first_key = first_browser.session.session_key

        self._login(second_browser)
        second_key = second_browser.session.session_key

        self.assertFalse(Session.objects.filter(session_key=first_key).exists())
        self.assertTrue(Session.objects.filter(session_key=second_key).exists())
        self.assertEqual(
            list(ActivePortalSession.objects.filter(user=self.user).values_list("session_key", flat=True)),
            [second_key],
        )

    def test_unrelated_tenant_session_is_not_decoded_inspected_or_revoked(self):
        other_browser = Client()
        self._login(other_browser, user=self.other_user)
        other_key = other_browser.session.session_key

        for number in range(75):
            Session.objects.create(
                session_key=f"unrelated{number:031d}",
                session_data="not-decoded-by-login",
                expire_date=timezone.now() + timedelta(hours=1),
            )

        target_browser = Client()
        with patch.object(Session, "get_decoded", side_effect=AssertionError("unrelated session decoded")):
            response = self._login(target_browser)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Session.objects.filter(session_key=other_key).exists())
        self.assertTrue(
            ActivePortalSession.objects.filter(user=self.other_user, session_key=other_key).exists()
        )

    def test_logout_removes_only_the_matching_registry_record(self):
        browser = Client()
        self._login(browser, portal="ADMIN")
        session_key = browser.session.session_key

        response = browser.get(reverse("accounts:admin_logout"))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ActivePortalSession.objects.filter(session_key=session_key).exists())
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())

    def test_faculty_logout_removes_the_matching_registry_record(self):
        browser = Client()
        self._login(browser, portal="FACULTY")
        session_key = browser.session.session_key

        response = browser.get(reverse("accounts:faculty_logout"))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ActivePortalSession.objects.filter(session_key=session_key).exists())
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())

    def test_django_admin_login_and_logout_keep_registry_consistent(self):
        browser = Client()
        login_response = browser.post(
            reverse("admin:login"),
            {
                "username": self.user.username,
                "password": self.password,
                "next": reverse("admin:index"),
            },
        )
        self.assertEqual(login_response.status_code, 302)
        session_key = browser.session.session_key
        self.assertTrue(ActivePortalSession.objects.filter(user=self.user, session_key=session_key).exists())

        logout_response = browser.post(reverse("admin:logout"))

        self.assertEqual(logout_response.status_code, 302)
        self.assertFalse(ActivePortalSession.objects.filter(session_key=session_key).exists())

    def test_portal_login_can_revoke_a_registered_django_admin_session(self):
        admin_browser = Client()
        admin_browser.post(
            reverse("admin:login"),
            {
                "username": self.user.username,
                "password": self.password,
                "next": reverse("admin:index"),
            },
        )
        admin_session_key = admin_browser.session.session_key

        portal_browser = Client()
        response = self._login(portal_browser, portal="ADMIN")

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Session.objects.filter(session_key=admin_session_key).exists())
        self.assertFalse(ActivePortalSession.objects.filter(session_key=admin_session_key).exists())

    def test_django_admin_login_enforces_the_active_portal_session_policy(self):
        portal_browser = Client()
        self._login(portal_browser, portal="FACULTY")
        portal_session_key = portal_browser.session.session_key

        admin_browser = Client()
        response = admin_browser.post(
            reverse("admin:login"),
            {
                "username": self.user.username,
                "password": self.password,
                "next": reverse("admin:index"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Session.objects.filter(session_key=portal_session_key).exists())
        self.assertFalse(ActivePortalSession.objects.filter(session_key=portal_session_key).exists())
        self.assertEqual(ActivePortalSession.objects.filter(user=self.user).count(), 1)

    def test_stale_registry_record_is_removed_without_decoding_sessions(self):
        stale_key = "s" * 40
        ActivePortalSession.objects.create(user=self.user, session_key=stale_key)

        browser = Client()
        with patch.object(Session, "get_decoded", side_effect=AssertionError("session decoded")):
            response = self._login(browser)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ActivePortalSession.objects.filter(session_key=stale_key).exists())
        self.assertEqual(ActivePortalSession.objects.filter(user=self.user).count(), 1)

    def test_admin_password_change_updates_the_registered_session_key(self):
        browser = Client()
        self._login(browser, portal="ADMIN")
        old_key = browser.session.session_key

        response = browser.post(
            reverse("accounts:admin_change_password"),
            {
                "old_password": self.password,
                "new_password1": "UpdatedSessionPass456!",
                "new_password2": "UpdatedSessionPass456!",
            },
        )

        self.assertEqual(response.status_code, 302)
        new_key = browser.session.session_key
        self.assertNotEqual(new_key, old_key)
        self.assertFalse(ActivePortalSession.objects.filter(session_key=old_key).exists())
        self.assertTrue(ActivePortalSession.objects.filter(user=self.user, session_key=new_key).exists())

    def test_faculty_password_change_updates_the_registered_session_key(self):
        browser = Client()
        self._login(browser, portal="FACULTY")
        old_key = browser.session.session_key

        response = browser.post(
            reverse("accounts:faculty_change_password"),
            {
                "old_password": self.password,
                "new_password1": "UpdatedFacultySession456!",
                "new_password2": "UpdatedFacultySession456!",
            },
        )

        self.assertEqual(response.status_code, 302)
        new_key = browser.session.session_key
        self.assertNotEqual(new_key, old_key)
        self.assertFalse(ActivePortalSession.objects.filter(session_key=old_key).exists())
        self.assertTrue(ActivePortalSession.objects.filter(user=self.user, session_key=new_key).exists())

    def test_repeated_logins_leave_one_authorized_active_session(self):
        session_keys = []
        for _ in range(3):
            browser = Client()
            self._login(browser)
            session_keys.append(browser.session.session_key)

        self.assertEqual(ActivePortalSession.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Session.objects.filter(session_key__in=session_keys).count(), 1)
        self.assertTrue(Session.objects.filter(session_key=session_keys[-1]).exists())

    def test_disabled_tenant_policy_tracks_without_revoking_multiple_sessions(self):
        SystemSettingService.set(
            FeatureSettingsService.SINGLE_DEVICE_SESSION_ENFORCEMENT_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        first_browser = Client()
        second_browser = Client()
        self._login(first_browser)
        self._login(second_browser)

        registered_keys = set(
            ActivePortalSession.objects.filter(user=self.user).values_list("session_key", flat=True)
        )
        self.assertEqual(registered_keys, {first_browser.session.session_key, second_browser.session.session_key})
        self.assertEqual(Session.objects.filter(session_key__in=registered_keys).count(), 2)

    def test_scheduled_deactivation_session_clear_is_bounded_to_registered_user(self):
        SystemSettingService.set(
            FeatureSettingsService.SINGLE_DEVICE_SESSION_ENFORCEMENT_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        first_browser = Client()
        second_browser = Client()
        other_browser = Client()
        self._login(first_browser)
        self._login(second_browser)
        self._login(other_browser, user=self.other_user)
        other_key = other_browser.session.session_key

        with patch.object(Session, "get_decoded", side_effect=AssertionError("session decoded")):
            deleted = UserDeactivationService._clear_user_sessions(user=self.user)

        self.assertEqual(deleted, 2)
        self.assertFalse(ActivePortalSession.objects.filter(user=self.user).exists())
        self.assertTrue(Session.objects.filter(session_key=other_key).exists())
