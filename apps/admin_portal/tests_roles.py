from importlib import import_module

from django.apps import apps as django_apps
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
        self.assertContains(response, "Compare Permissions")
        self.assertContains(response, 'name="role_ids"', html=False)
        self.assertContains(response, reverse("admin_portal:role_permission_compare"))

    def test_role_permission_compare_page_shows_side_by_side_grants(self):
        campus_admin = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin", is_active=True)
        dean = Role.objects.create(code="DEAN", name="Dean", is_active=True)
        shared = Permission.objects.create(
            code="compare.shared",
            module="compare",
            action="read",
            description="Shared permission",
        )
        campus_only = Permission.objects.create(
            code="compare.campus_only",
            module="compare",
            action="update",
            description="Campus-only permission",
        )
        dean_only = Permission.objects.create(
            code="compare.dean_only",
            module="compare",
            action="approve",
            description="Dean-only permission",
        )
        RolePermission.objects.create(role=campus_admin, permission=shared)
        RolePermission.objects.create(role=campus_admin, permission=campus_only)
        RolePermission.objects.create(role=dean, permission=shared)
        RolePermission.objects.create(role=dean, permission=dean_only)

        response = self.client.get(
            reverse("admin_portal:role_permission_compare"),
            {"role_ids": [campus_admin.id, dean.id]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["selected_roles"]), 2)
        self.assertGreaterEqual(response.context["difference_count"], 2)
        self.assertContains(response, "Compare Role Permissions")
        self.assertContains(response, "Campus Admin")
        self.assertContains(response, "Dean")
        self.assertContains(response, "Shared permission")
        self.assertContains(response, "Campus-only permission")
        self.assertContains(response, "Dean-only permission")
        self.assertContains(response, "Granted")
        self.assertContains(response, "Not granted")
        self.assertContains(response, "Show Differences Only")
        self.assertContains(response, 'data-different="1"', html=False)
        self.assertNotContains(response, 'name="permissions"', html=False)

    def test_role_permission_compare_requires_at_least_two_roles(self):
        role = Role.objects.create(code="ONE_ROLE", name="One Role", is_active=True)

        response = self.client.get(
            reverse("admin_portal:role_permission_compare"),
            {"role_ids": [role.id]},
        )

        self.assertRedirects(response, reverse("admin_portal:role_list"))

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

    def test_role_permissions_page_shows_plain_language_module_descriptions(self):
        role = Role.objects.create(code="DESCRIBED_ROLE", name="Described Role", is_active=True)
        Permission.objects.create(code="academic_years.read", module="academic_years", action="read")
        Permission.objects.create(code="actual_data_reset.run", module="actual_data_reset", action="run")
        Permission.objects.create(code="custom_module.read", module="custom_module", action="read")

        response = self.client.get(reverse("admin_portal:role_permissions", args=[role.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Academic Year Setup")
        self.assertContains(response, "Controls who can view, create, or edit the official academic year records")
        self.assertContains(response, "Actual Data Reset")
        self.assertContains(response, "Grant only to trusted users because this can remove transactions")
        self.assertContains(response, "Custom Module")
        self.assertContains(response, "Controls access to Custom Module pages and actions")
        self.assertContains(response, "academic_years.read")
        self.assertContains(
            response,
            'id="critical-access-safeguard" class="alert alert-warning border small mb-0 mt-3 d-none"',
            html=False,
        )

    def test_role_permissions_page_lists_only_current_departmental_exam_permissions(self):
        role = Role.objects.create(code="DE_FOUNDATION_ROLE", name="Departmental Exam Foundation")
        migration = import_module("apps.rbac.migrations.0032_seed_departmental_exam_permissions")
        migration.seed_permissions(django_apps, None)

        response = self.client.get(reverse("admin_portal:role_permissions", args=[role.id]))

        self.assertEqual(response.status_code, 200)
        departmental_exams = next(
            module
            for module in response.context["permissions_by_module"]
            if module["key"] == "departmental_exams"
        )
        self.assertEqual(
            {row["code"] for row in departmental_exams["permissions"]},
            {
                "departmental_exams.manage_cycles",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            },
        )
        self.assertContains(response, "Manage examination cycles.")
        self.assertContains(
            response, "Configure authorized grouped course examinations."
        )
        self.assertContains(response, "Review assigned grouped course examinations.")
        self.assertNotContains(response, "Contribute departmental examination questions.")
        self.assertNotContains(response, "Encode departmental examination questions.")
        self.assertNotContains(response, "Generate departmental examination questionnaires.")
        self.assertNotContains(response, "Approve and lock departmental examinations.")
        self.assertNotContains(response, "Print approved departmental questionnaires.")
        self.assertNotContains(response, "View departmental examination answer keys.")
        self.assertNotContains(response, "Download departmental examination PDFs.")
        self.assertNotContains(response, "Pair Code")
        self.assertNotContains(response, "QR")

    def test_role_permissions_page_shows_comparison_summary_index_and_numbering(self):
        role = Role.objects.create(code="COMPARE_ROLE", name="Compare Role", is_active=True)
        roles_read = Permission.objects.get(code="roles.read")
        RolePermission.objects.create(role=role, permission=roles_read)

        response = self.client.get(reverse("admin_portal:role_permissions", args=[role.id]))

        self.assertEqual(response.status_code, 200)
        roles_module = next(module for module in response.context["permissions_by_module"] if module["key"] == "roles")
        roles_read_row = next(item for item in roles_module["permissions"] if item["code"] == "roles.read")
        roles_update_row = next(item for item in roles_module["permissions"] if item["code"] == "roles.update")
        self.assertEqual(response.context["role_assigned_permission_count"], 1)
        self.assertGreaterEqual(response.context["total_permission_count"], 4)
        self.assertGreaterEqual(response.context["permission_group_count"], 3)
        self.assertContains(response, "Module Index")
        self.assertContains(response, 'id="permission-module-jump"', html=False)
        self.assertContains(response, 'value="card_module_admin_portal"', html=False)
        self.assertContains(response, 'value="card_module_roles"', html=False)
        self.assertContains(response, "permission-card-header-alt-a")
        self.assertContains(response, "permission-card-header-alt-b")
        self.assertContains(response, f'{roles_module["number"]}. Security Roles')
        self.assertContains(response, f'1 of {response.context["total_permission_count"]}')
        self.assertContains(response, f'{response.context["permission_group_count"]}')
        self.assertEqual(roles_module["assigned_count"], 1)
        self.assertGreaterEqual(roles_module["total_count"], 2)
        self.assertContains(response, "assigned")
        self.assertContains(response, roles_read_row["number"])
        self.assertContains(response, roles_update_row["number"])
        self.assertContains(response, "Assigned")
        self.assertContains(response, "Not assigned")

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
        self.assertIn("saved_module=alpha", response["Location"])
        self.assertIn("#card_module_alpha", response["Location"])
        role_permission_ids = set(role.role_permissions.values_list("permission_id", flat=True))
        self.assertNotIn(alpha_read.id, role_permission_ids)
        self.assertIn(alpha_update.id, role_permission_ids)
        self.assertIn(beta_read.id, role_permission_ids)
        self.assertNotIn(beta_update.id, role_permission_ids)

        response = self.client.get(response["Location"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="card_module_alpha"', html=False)
        self.assertContains(response, "Changes saved")

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
        self.assertContains(
            response,
            'id="critical-access-safeguard" class="alert alert-warning border small mb-0 mt-3 "',
            html=False,
        )

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


class RolePermissionBoundaryTests(TestCase):
    def setUp(self):
        self.admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access", defaults={"module": "admin_portal", "action": "access"}
        )
        self.roles_read, _ = Permission.objects.get_or_create(
            code="roles.read", defaults={"module": "roles", "action": "read"}
        )
        self.user_roles_update, _ = Permission.objects.get_or_create(
            code="user_roles.update", defaults={"module": "user_roles", "action": "update"}
        )
        self.actor_role = Role.objects.create(code="BOUNDARY_ROLE", name="Boundary Role")
        self.target_role = Role.objects.create(code="TARGET_ROLE", name="Target Role", is_active=True)

        self.actor = User.objects.create_user(
            username="boundary_admin",
            email="boundary_admin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(user=self.actor, role=self.actor_role)
        RolePermission.objects.create(role=self.actor_role, permission=self.admin_access)
        RolePermission.objects.create(role=self.actor_role, permission=self.roles_read)
        RolePermission.objects.create(role=self.actor_role, permission=self.user_roles_update)
        self.client.force_login(self.actor)

    def test_user_role_assignment_permission_does_not_open_role_permissions_page(self):
        response = self.client.get(reverse("admin_portal:role_permissions", args=[self.target_role.id]))

        self.assertEqual(response.status_code, 403)
