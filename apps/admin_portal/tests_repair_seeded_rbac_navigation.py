from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission


class RepairSeededRbacNavigationCommandTests(TestCase):
    CORRECTION_PERMISSION = "corrections.create_on_behalf"
    CORRECTION_MENU_ITEM = "GRADE_CORRECTION_ON_BEHALF"
    CORRECTION_ROLES = ("AC", "DEAN", "COLLEGE_DEAN", "CAMPUS_ADMIN", "TENANT_ADMIN")
    STUDENT_QUERY_PERMISSION = "student_enrollment_query.read"
    STUDENT_QUERY_MENU_ITEM = "STUDENT_ENROLLMENT_QUERY"
    STUDENT_QUERY_ROLES = ("SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR")

    def setUp(self):
        for code in {
            *self.CORRECTION_ROLES,
            *self.STUDENT_QUERY_ROLES,
        }:
            Role.objects.update_or_create(
                code=code,
                defaults={"name": code.replace("_", " ").title(), "is_active": True},
            )
        for code, label, sort_order in [
            ("GRADING", "Grading", 50),
            ("ENROLLMENT", "Enrollment", 40),
            ("STUDENTS", "Students", 30),
        ]:
            MenuGroup.objects.update_or_create(
                portal=MenuGroup.Portal.ADMIN,
                code=code,
                defaults={"label": label, "sort_order": sort_order, "is_active": True},
            )
        Permission.objects.update_or_create(
            code="corrections.read",
            defaults={
                "module": "corrections",
                "action": "read",
                "description": "Read grade correction petitions.",
                "is_active": True,
            },
        )

    def _run_command(self):
        output = StringIO()
        call_command("repair_seeded_rbac_navigation", stdout=output)
        return output.getvalue()

    def _remove_seeded_targets(self):
        MenuItemPermission.objects.filter(
            menu_item__code__in=[self.CORRECTION_MENU_ITEM, self.STUDENT_QUERY_MENU_ITEM]
        ).delete()
        MenuItemPermission.objects.filter(
            permission__code__in=[self.CORRECTION_PERMISSION, self.STUDENT_QUERY_PERMISSION]
        ).delete()
        RolePermission.objects.filter(
            permission__code__in=[self.CORRECTION_PERMISSION, self.STUDENT_QUERY_PERMISSION]
        ).delete()
        MenuItem.objects.filter(
            portal=MenuGroup.Portal.ADMIN,
            code__in=[self.CORRECTION_MENU_ITEM, self.STUDENT_QUERY_MENU_ITEM],
        ).delete()
        Permission.objects.filter(
            code__in=[self.CORRECTION_PERMISSION, self.STUDENT_QUERY_PERMISSION]
        ).delete()

    def test_creates_correction_on_behalf_permission_menu_and_links(self):
        self._remove_seeded_targets()

        output = self._run_command()

        permission = Permission.objects.get(code=self.CORRECTION_PERMISSION)
        self.assertEqual(permission.module, "corrections")
        self.assertEqual(permission.action, "create_on_behalf")
        menu_item = MenuItem.objects.get(portal=MenuGroup.Portal.ADMIN, code=self.CORRECTION_MENU_ITEM)
        self.assertEqual(menu_item.label, "Create Correction On Behalf")
        self.assertEqual(menu_item.route_name, "admin_portal:grade_correction_request_create_on_behalf")
        self.assertEqual(menu_item.menu_group.code, "GRADING")
        self.assertEqual(menu_item.sort_order, 91)
        self.assertTrue(MenuItemPermission.objects.filter(menu_item=menu_item, permission=permission).exists())
        self.assertIn("Created permission corrections.create_on_behalf", output)

    def test_links_correction_on_behalf_permission_to_target_roles(self):
        self._remove_seeded_targets()

        self._run_command()

        permission = Permission.objects.get(code=self.CORRECTION_PERMISSION)
        read_permission = Permission.objects.get(code="corrections.read")
        for role_code in self.CORRECTION_ROLES:
            role = Role.objects.get(code=role_code)
            self.assertTrue(RolePermission.objects.filter(role=role, permission=permission).exists())
            self.assertTrue(RolePermission.objects.filter(role=role, permission=read_permission).exists())

    def test_college_dean_receives_correction_permissions_when_dean_is_missing(self):
        self._remove_seeded_targets()
        Role.objects.filter(code="DEAN").update(is_active=False)

        output = self._run_command()

        college_dean = Role.objects.get(code="COLLEGE_DEAN")
        correction_permission = Permission.objects.get(code=self.CORRECTION_PERMISSION)
        read_permission = Permission.objects.get(code="corrections.read")
        self.assertTrue(
            RolePermission.objects.filter(role=college_dean, permission=correction_permission).exists()
        )
        self.assertTrue(RolePermission.objects.filter(role=college_dean, permission=read_permission).exists())
        self.assertIn("WARNING: Role DEAN is missing", output)
        self.assertIn("Repair complete", output)

    def test_creates_student_enrollment_query_permission_menu_and_links(self):
        self._remove_seeded_targets()

        output = self._run_command()

        permission = Permission.objects.get(code=self.STUDENT_QUERY_PERMISSION)
        self.assertEqual(permission.module, "student_enrollment_query")
        self.assertEqual(permission.action, "read")
        menu_item = MenuItem.objects.get(portal=MenuGroup.Portal.ADMIN, code=self.STUDENT_QUERY_MENU_ITEM)
        self.assertEqual(menu_item.label, "Student Enrollment Query")
        self.assertEqual(menu_item.route_name, "admin_portal:student_enrollment_query")
        self.assertEqual(menu_item.menu_group.code, "ENROLLMENT")
        self.assertEqual(menu_item.sort_order, 20)
        self.assertTrue(MenuItemPermission.objects.filter(menu_item=menu_item, permission=permission).exists())
        for role_code in self.STUDENT_QUERY_ROLES:
            role = Role.objects.get(code=role_code)
            self.assertTrue(RolePermission.objects.filter(role=role, permission=permission).exists())
        self.assertIn("Created permission student_enrollment_query.read", output)

    def test_command_is_idempotent_when_run_twice(self):
        self._remove_seeded_targets()

        self._run_command()
        self._run_command()

        self.assertEqual(Permission.objects.filter(code=self.CORRECTION_PERMISSION).count(), 1)
        self.assertEqual(Permission.objects.filter(code=self.STUDENT_QUERY_PERMISSION).count(), 1)
        self.assertEqual(MenuItem.objects.filter(code=self.CORRECTION_MENU_ITEM).count(), 1)
        self.assertEqual(MenuItem.objects.filter(code=self.STUDENT_QUERY_MENU_ITEM).count(), 1)
        self.assertEqual(
            MenuItemPermission.objects.filter(menu_item__code=self.CORRECTION_MENU_ITEM).count(),
            1,
        )
        self.assertEqual(
            MenuItemPermission.objects.filter(menu_item__code=self.STUDENT_QUERY_MENU_ITEM).count(),
            1,
        )
        for permission_code, role_codes in [
            (self.CORRECTION_PERMISSION, self.CORRECTION_ROLES),
            (self.STUDENT_QUERY_PERMISSION, self.STUDENT_QUERY_ROLES),
        ]:
            for role_code in role_codes:
                self.assertEqual(
                    RolePermission.objects.filter(
                        role__code=role_code,
                        permission__code=permission_code,
                    ).count(),
                    1,
                )

    def test_uses_students_group_when_enrollment_group_is_missing(self):
        self._remove_seeded_targets()
        MenuGroup.objects.filter(portal=MenuGroup.Portal.ADMIN, code="ENROLLMENT").update(is_active=False)

        output = self._run_command()

        menu_item = MenuItem.objects.get(portal=MenuGroup.Portal.ADMIN, code=self.STUDENT_QUERY_MENU_ITEM)
        self.assertEqual(menu_item.menu_group.code, "STUDENTS")
        self.assertIn("WARNING: ADMIN menu group ENROLLMENT is missing.", output)
        self.assertIn("Using fallback ADMIN menu group STUDENTS.", output)

    def test_handles_missing_menu_groups_and_roles_gracefully(self):
        self._remove_seeded_targets()
        MenuGroup.objects.filter(portal=MenuGroup.Portal.ADMIN, code__in=["GRADING", "ENROLLMENT", "STUDENTS"]).update(
            is_active=False
        )
        Role.objects.filter(code="REGISTRAR").update(is_active=False)

        output = self._run_command()

        self.assertTrue(Permission.objects.filter(code=self.CORRECTION_PERMISSION).exists())
        self.assertTrue(Permission.objects.filter(code=self.STUDENT_QUERY_PERMISSION).exists())
        self.assertFalse(MenuItem.objects.filter(code=self.CORRECTION_MENU_ITEM).exists())
        self.assertFalse(MenuItem.objects.filter(code=self.STUDENT_QUERY_MENU_ITEM).exists())
        self.assertIn("WARNING: ADMIN menu group GRADING is missing.", output)
        self.assertIn("WARNING: Fallback ADMIN menu group STUDENTS is missing.", output)
        self.assertIn("WARNING: Role REGISTRAR is missing", output)
