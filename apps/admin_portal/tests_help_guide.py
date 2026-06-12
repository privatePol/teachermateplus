from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.admin_portal.help_guide import build_admin_help_sections
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Tenant


class AdminHelpGuideTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="GUIDE", name="Guide School")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.portal_permission = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
        )
        self.dashboard_permission = Permission.objects.create(
            code="dashboard.read",
            module="dashboard",
            action="read",
        )
        self.course_permission = Permission.objects.create(
            code="courses.read",
            module="courses",
            action="read",
        )
        self.reset_permission = Permission.objects.create(
            code="actual_data_reset.run",
            module="actual_data_reset",
            action="run",
        )
        self.grading_template_permission = Permission.objects.create(
            code="grading_templates.read",
            module="grading_templates",
            action="read",
        )
        self.hotfix_permission = Permission.objects.create(
            code="template_hotfixes.read",
            module="template_hotfixes",
            action="read",
        )

    def _make_user(self, *, username, role_code, permissions):
        user = User.objects.create_user(
            username=username,
            password="testpass123",
            email=f"{username}@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code=role_code, name=role_code.replace("_", " ").title())
        for permission in permissions:
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            is_active=True,
        )
        return user

    def test_campus_admin_does_not_receive_superadmin_help(self):
        user = self._make_user(
            username="campus_admin_guide",
            role_code="CAMPUS_ADMIN",
            permissions=[
                self.portal_permission,
                self.dashboard_permission,
                self.course_permission,
                self.reset_permission,
            ],
        )

        sections = build_admin_help_sections(
            user=user,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        section_codes = {section["code"] for section in sections}
        rendered_text = " ".join(
            topic["title"] for section in sections for topic in section["topics"]
        )

        self.assertIn("academic-setup", section_codes)
        self.assertNotIn("superadmin", section_codes)
        self.assertNotIn("Tenants, Roles, Permissions, Menus, and High-Risk Tools", rendered_text)

    def test_superadmin_role_receives_sensitive_help(self):
        user = self._make_user(
            username="superadmin_guide",
            role_code="SUPER_ADMIN",
            permissions=[
                self.portal_permission,
                self.dashboard_permission,
                self.reset_permission,
            ],
        )

        sections = build_admin_help_sections(
            user=user,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )

        self.assertIn("superadmin", {section["code"] for section in sections})

    def test_admin_guide_can_restore_legacy_template(self):
        user = User.objects.create_superuser(
            username="guide_root",
            password="testpass123",
            email="guide_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)

        revised_response = self.client.get(reverse("admin_portal:guide"))
        self.assertEqual(revised_response.status_code, 200)
        self.assertTemplateUsed(revised_response, "admin_portal/guide_role_based.html")

        SystemSettingService.set(
            FeatureSettingsService.ROLE_BASED_HELP_GUIDE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        legacy_response = self.client.get(reverse("admin_portal:guide"))
        self.assertEqual(legacy_response.status_code, 200)
        self.assertTemplateUsed(legacy_response, "admin_portal/guide.html")

    def test_practical_guide_links_to_full_guide_and_back(self):
        user = User.objects.create_superuser(
            username="guide_link_root",
            password="testpass123",
            email="guide_link_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)

        practical_response = self.client.get(reverse("admin_portal:guide"))
        self.assertTemplateUsed(practical_response, "admin_portal/guide_role_based.html")
        self.assertContains(practical_response, "Open Full Admin Guide")
        self.assertContains(practical_response, "?view=full")

        full_response = self.client.get(reverse("admin_portal:guide"), {"view": "full"})
        self.assertTemplateUsed(full_response, "admin_portal/guide.html")
        self.assertContains(full_response, "Back to Practical Guide")
        self.assertContains(full_response, "?view=practical")

    def test_explicit_practical_view_works_when_legacy_is_tenant_default(self):
        user = User.objects.create_superuser(
            username="guide_practical_override_root",
            password="testpass123",
            email="guide_practical_override_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)
        SystemSettingService.set(
            FeatureSettingsService.ROLE_BASED_HELP_GUIDE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

        response = self.client.get(reverse("admin_portal:guide"), {"view": "practical"})

        self.assertTemplateUsed(response, "admin_portal/guide_role_based.html")
        self.assertContains(response, "Open Full Admin Guide")

    def test_full_guide_keeps_superadmin_incident_section_hidden_from_campus_admin(self):
        user = self._make_user(
            username="campus_admin_full_guide",
            role_code="CAMPUS_ADMIN",
            permissions=[
                self.portal_permission,
                self.dashboard_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"), {"view": "full"})

        self.assertTemplateUsed(response, "admin_portal/guide.html")
        self.assertNotContains(response, "14. Production Incident Response")
        self.assertNotContains(response, 'href="#incident-response"', html=False)

    def test_revised_admin_guide_preserves_existing_deep_link_anchors(self):
        user = User.objects.create_superuser(
            username="guide_anchor_root",
            password="testpass123",
            email="guide_anchor_root@example.com",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"))

        self.assertContains(response, 'id="grading-template-calculator"', html=False)
        self.assertContains(response, 'id="assignment-acceptance"', html=False)

    def test_grading_template_help_names_exact_menu_and_builder_steps(self):
        user = self._make_user(
            username="grading_guide_admin",
            role_code="GRADING_ADMIN",
            permissions=[
                self.portal_permission,
                self.grading_template_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"))

        self.assertContains(response, "Admin Portal -&gt; Grading -&gt; Grading Templates")
        self.assertContains(response, "Click the Builder icon on the template row.")
        self.assertContains(response, "Detail Computation to Average Activities")
        self.assertNotContains(response, "Do not confuse Direct Percentage")

    def test_hotfix_help_is_visible_with_hotfix_permission(self):
        user = self._make_user(
            username="hotfix_guide_admin",
            role_code="HOTFIX_REVIEWER",
            permissions=[
                self.portal_permission,
                self.hotfix_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"))

        self.assertContains(response, "Change a Published Template Using a Hotfix")
        self.assertContains(response, "Admin Portal -&gt; Grading -&gt; Template Hotfix Requests")
        self.assertContains(response, "type APPLY HOTFIX")
        self.assertContains(response, "submitted offerings in restricted modes are skipped")

    def test_hotfix_help_is_hidden_without_hotfix_permission(self):
        user = self._make_user(
            username="non_hotfix_guide_admin",
            role_code="BASIC_ADMIN",
            permissions=[
                self.portal_permission,
                self.dashboard_permission,
            ],
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin_portal:guide"))

        self.assertNotContains(response, "Change a Published Template Using a Hotfix")
