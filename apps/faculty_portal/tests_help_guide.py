from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Tenant


class FacultyHelpGuideTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="FGUIDE", name="Faculty Guide School")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.user = User.objects.create_user(
            username="faculty_guide",
            password="testpass123",
            email="faculty_guide@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        permission = Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
        )
        role = Role.objects.create(code="FACULTY", name="Faculty")
        RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(
            user=self.user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            is_active=True,
        )
        self.client.force_login(self.user)

    def test_revised_faculty_guide_explains_zero_blank_and_base_50(self):
        response = self.client.get(reverse("faculty_portal:guide"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "faculty_portal/guide_role_based.html")
        self.assertContains(response, "A saved 0 is complete and counts in computation.")
        self.assertContains(response, "A blank score is missing and can block submission.")
        self.assertContains(response, "Under Raw Score Base-50, raw 0 is transmuted to 50.")
        self.assertContains(response, 'id="guide-assignments"', html=False)
        self.assertContains(response, 'id="guide-submission"', html=False)
        self.assertContains(response, 'id="guide-notes"', html=False)
        self.assertContains(response, 'id="guide-classlist"', html=False)
        self.assertContains(response, "Back to Dashboard")
        self.assertContains(response, reverse("faculty_portal:dashboard"))

    def test_faculty_guide_can_restore_legacy_template(self):
        SystemSettingService.set(
            FeatureSettingsService.ROLE_BASED_HELP_GUIDE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

        response = self.client.get(reverse("faculty_portal:guide"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "faculty_portal/guide.html")
