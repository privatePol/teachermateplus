from django.test import TestCase
from django.urls import reverse
from django.conf import settings
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, Role, RolePermission, UserRole


class RoleManagementTests(TestCase):
    def setUp(self):
        Permission.objects.create(code="admin_portal.access", module="admin_portal", action="access")
        Permission.objects.create(code="audit_logs.read", module="audit_logs", action="read")
        Permission.objects.create(code="roles.read", module="roles", action="read")
        Permission.objects.create(code="roles.update", module="roles", action="update")
        self.admin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def test_role_list_separates_active_and_inactive_records(self):
        Role.objects.create(code="ACTIVE_ROLE", name="Active Role", is_active=True)
        Role.objects.create(code="INACTIVE_ROLE", name="Inactive Role", is_active=False)

        response = self.client.get(reverse("admin_portal:role_list"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Active Records", content)
        self.assertIn("Inactive Records", content)
        self.assertIn("ACTIVE_ROLE", content)
        self.assertIn("INACTIVE_ROLE", content)
        self.assertIn("Type INACTIVE_ROLE", content)

    def test_inactive_role_hard_delete_requires_exact_role_code(self):
        role = Role.objects.create(code="OLD_ROLE", name="Old Role", is_active=False)
        permission = Permission.objects.create(code="old.read", module="old", action="read")
        RolePermission.objects.create(role=role, permission=permission)
        user = User.objects.create_user(username="role_user", email="role_user@example.com", password="testpass123")
        UserRole.objects.create(user=user, role=role)

        response = self.client.post(
            reverse("admin_portal:role_delete", args=[role.id]),
            {"confirmation_code": "WRONG"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Role.objects.filter(id=role.id).exists())

        response = self.client.post(
            reverse("admin_portal:role_delete", args=[role.id]),
            {"confirmation_code": "OLD_ROLE"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Role.objects.filter(id=role.id).exists())
        self.assertFalse(RolePermission.objects.filter(role_id=role.id).exists())
        self.assertFalse(UserRole.objects.filter(role_id=role.id).exists())

    def test_active_role_cannot_be_hard_deleted(self):
        role = Role.objects.create(code="ACTIVE_KEEP", name="Active Keep", is_active=True)

        response = self.client.post(
            reverse("admin_portal:role_delete", args=[role.id]),
            {"confirmation_code": "ACTIVE_KEEP"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Role.objects.filter(id=role.id).exists())

    def test_role_permissions_page_shows_section_save_buttons(self):
        role = Role.objects.create(code="SECTION_SAVE", name="Section Save", is_active=True)
        Permission.objects.create(code="alpha.read", module="alpha", action="read")

        response = self.client.get(reverse("admin_portal:role_permissions", args=[role.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Save Section")
        self.assertContains(response, 'name="save_module" value="alpha"', html=False)

    def test_role_permissions_section_save_updates_only_selected_module(self):
        role = Role.objects.create(code="PARTIAL_SAVE", name="Partial Save", is_active=True)
        alpha_read = Permission.objects.create(code="alpha.read", module="alpha", action="read")
        alpha_update = Permission.objects.create(code="alpha.update", module="alpha", action="update")
        beta_read = Permission.objects.create(code="beta.read", module="beta", action="read")
        beta_update = Permission.objects.create(code="beta.update", module="beta", action="update")
        RolePermission.objects.create(role=role, permission=alpha_read)
        RolePermission.objects.create(role=role, permission=beta_read)

        response = self.client.post(
            reverse("admin_portal:role_permissions", args=[role.id]),
            {
                "save_module": "alpha",
                "permissions": [str(alpha_update.id), str(beta_update.id)],
            },
        )

        self.assertEqual(response.status_code, 302)
        role_permission_ids = set(role.role_permissions.values_list("permission_id", flat=True))
        self.assertNotIn(alpha_read.id, role_permission_ids)
        self.assertIn(alpha_update.id, role_permission_ids)
        self.assertIn(beta_read.id, role_permission_ids)
        self.assertNotIn(beta_update.id, role_permission_ids)

    def test_critical_role_permission_change_requires_reason_and_confirmation(self):
        role = Role.objects.create(code="CRITICAL_ROLE", name="Critical Role", is_active=True)
        UserRole.objects.create(user=self.admin, role=role)
        admin_access = Permission.objects.get(code="admin_portal.access")

        response = self.client.post(
            reverse("admin_portal:role_permissions", args=[role.id]),
            {"permissions": [str(admin_access.id)]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(RolePermission.objects.filter(role=role, permission=admin_access).exists())
        self.assertContains(response, "Enter the reason for changing critical role access.")

        response = self.client.post(
            reverse("admin_portal:role_permissions", args=[role.id]),
            {
                "permissions": [str(admin_access.id)],
                "change_reason": "Granting portal access for assigned operations.",
                "confirmation_phrase": "CHANGE PERMISSIONS",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(RolePermission.objects.filter(role=role, permission=admin_access).exists())
        log = AuditLog.objects.filter(entity_type="RolePermission", entity_id=str(role.id)).latest("created_at")
        self.assertTrue(log.metadata_json["critical_action"])
        self.assertEqual(log.metadata_json["reason"], "Granting portal access for assigned operations.")

    def test_recent_critical_actions_report_shows_safe_audit_summary(self):
        AuditLog.objects.create(
            actor_user=self.admin,
            portal="ADMIN",
            action="UPDATE",
            entity_type="RolePermission",
            entity_id="77",
            before_json={"permission_ids": [1]},
            after_json={"permission_ids": [1, 2]},
            metadata_json={
                "critical_action": True,
                "reason": "Temporary access for setup.",
                "confirmation_required": True,
                "impact_summary": {"affected_active_user_count": 3},
            },
        )

        response = self.client.get(reverse("admin_portal:recent_critical_actions"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recent Critical Actions")
        self.assertContains(response, "Temporary access for setup.")
        self.assertContains(response, "3 active user")
        self.assertNotContains(response, "permission_ids")
