from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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
        Permission.objects.create(code="admin_portal.access", module="admin_portal", action="access")
        Permission.objects.get(code="faculty_portal.access")
        self.user = User.objects.create_superuser(
            username="facultytester",
            email="facultytester@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)

    def test_admin_privacy_consent_locks_left_navigation(self):
        response = self.client.get(reverse("accounts:admin_privacy_consent"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-privacy-consent-lock="1"')
        self.assertContains(response, "Navigation is locked until privacy consent is accepted.")
        self.assertNotContains(response, 'href="/admin-portal/dashboard/')
        self.assertNotContains(response, reverse("accounts:admin_change_password"))

    def test_faculty_privacy_consent_locks_left_navigation(self):
        response = self.client.get(reverse("accounts:faculty_privacy_consent"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-navigation-lock="1"')
        self.assertContains(response, "Navigation is locked until privacy consent is accepted.")
        self.assertNotContains(response, 'href="/faculty/courses/')
        self.assertNotContains(response, reverse("accounts:faculty_change_password"))

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


class FacultyRequiredPasswordChangeViewTests(TestCase):
    def setUp(self):
        Permission.objects.get(code="faculty_portal.access")
        self.current_password = "IssuedPass123!"
        self.user = User.objects.create_superuser(
            username="requiredchange",
            email="requiredchange@example.com",
            password=self.current_password,
        )
        self.user.must_change_password = True
        self.user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.user.privacy_consent_at = timezone.now()
        self.user.save(
            update_fields=[
                "must_change_password",
                "privacy_consent_version",
                "privacy_consent_at",
            ]
        )
        self.client.force_login(self.user)

    def test_required_password_change_collapses_and_locks_faculty_navigation(self):
        response = self.client.get(reverse("accounts:faculty_change_password"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-navigation-lock="1"')
        self.assertContains(response, "sidebar-collapsed navigation-lock")
        self.assertContains(response, "Navigation is locked until your password is changed.")
        self.assertContains(response, "Navigation will become available after the password is updated successfully.")
        self.assertContains(response, 'id="faculty-sidebar-toggle"', html=False)
        self.assertContains(response, 'disabled aria-disabled="true"', html=False)
        self.assertNotContains(response, 'href="/faculty/courses/')
        self.assertNotContains(response, ">Back</a>", html=False)

    def test_invalid_required_password_change_keeps_navigation_locked(self):
        response = self.client.post(
            reverse("accounts:faculty_change_password"),
            {
                "old_password": "wrong-password",
                "new_password1": "ChangedPass456!",
                "new_password2": "ChangedPass456!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)
        self.assertContains(response, 'data-navigation-lock="1"')
        self.assertContains(response, "Navigation is locked until your password is changed.")

    def test_successful_required_password_change_unlocks_faculty_navigation(self):
        response = self.client.post(
            reverse("accounts:faculty_change_password"),
            {
                "old_password": self.current_password,
                "new_password1": "ChangedPass456!",
                "new_password2": "ChangedPass456!",
            },
        )

        self.assertRedirects(response, reverse("faculty_portal:dashboard"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password("ChangedPass456!"))

        unlocked_response = self.client.get(reverse("accounts:faculty_change_password"))
        self.assertEqual(unlocked_response.status_code, 200)
        self.assertNotContains(unlocked_response, 'data-navigation-lock="1"')
        self.assertNotContains(unlocked_response, "Navigation is locked until your password is changed.")
        self.assertContains(unlocked_response, ">Back</a>", html=False)
