from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole


class FacultyQuickTourTests(TestCase):
    def setUp(self):
        faculty_access = Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
            is_active=True,
        )
        dashboard_read = Permission.objects.create(
            code="dashboard.read",
            module="dashboard",
            action="read",
            is_active=True,
        )
        role = Role.objects.create(code="FACULTY", name="Faculty", is_active=True)
        RolePermission.objects.create(role=role, permission=faculty_access)
        RolePermission.objects.create(role=role, permission=dashboard_read)
        self.faculty_user = User.objects.create_user(
            username="tourfaculty",
            email="tourfaculty@ncba.edu.ph",
            password="TourPass123!",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(user=self.faculty_user, role=role, is_active=True)
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_QUICK_TOUR_ENABLED_KEY,
            True,
            value_type="BOOL",
            is_active=True,
        )

    def test_faculty_dashboard_renders_quick_tour_when_enabled(self):
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertContains(response, "Faculty Portal quick tour", status_code=200)
        self.assertContains(response, "Disable on next logon")
        self.assertContains(response, 'data-tour-id="my-classes"')

    def test_faculty_dashboard_hides_quick_tour_when_user_disabled_it(self):
        self.faculty_user.faculty_quick_tour_disabled = True
        self.faculty_user.save(update_fields=["faculty_quick_tour_disabled"])
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertNotContains(response, "Faculty Portal quick tour", status_code=200)

    def test_disable_quick_tour_endpoint_updates_user_preference(self):
        self.client.force_login(self.faculty_user)

        response = self.client.post(reverse("faculty_portal:quick_tour_disable"))

        self.assertEqual(response.status_code, 200)
        self.faculty_user.refresh_from_db()
        self.assertTrue(self.faculty_user.faculty_quick_tour_disabled)
