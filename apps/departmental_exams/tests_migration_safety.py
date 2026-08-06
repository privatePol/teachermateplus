from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserPermission


class DepartmentalExamSeedMigrationSafetyTests(TestCase):
    def setUp(self):
        self.rbac_migration = import_module(
            "apps.rbac.migrations.0032_seed_departmental_exam_permissions"
        )
        self.navigation_migration = import_module(
            "apps.navigation.migrations.0017_seed_departmental_exam_menus"
        )
        self.stage5_navigation_migration = import_module(
            "apps.navigation.migrations.0018_seed_departmental_exam_stage5_menus"
        )
        self.rbac_migration.seed_permissions(django_apps, None)

    def test_rbac_reverse_preserves_permission_referenced_by_custom_menu_item(self):
        permission = Permission.objects.get(code="departmental_exams.review_generate")
        custom_group = MenuGroup.objects.create(
            portal="ADMIN",
            code="CUSTOM_DE_RBAC",
            label="Custom Departmental RBAC",
        )
        custom_item = MenuItem.objects.create(
            menu_group=custom_group,
            portal="ADMIN",
            code="CUSTOM_DE_RBAC_AUDIT",
            label="Custom Audit",
        )
        custom_link = MenuItemPermission.objects.create(menu_item=custom_item, permission=permission)

        self.rbac_migration.unseed_permissions(django_apps, None)

        self.assertTrue(Permission.objects.filter(pk=permission.pk).exists())
        self.assertTrue(MenuItemPermission.objects.filter(pk=custom_link.pk).exists())

    def test_rbac_seed_contains_only_current_foundation_permissions(self):
        permissions = {
            permission.code: permission.description
            for permission in Permission.objects.filter(module="departmental_exams")
        }
        self.assertSetEqual(
            set(permissions),
            {
                "departmental_exams.manage_cycles",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            },
        )
        self.assertEqual(
            permissions,
            {
                "departmental_exams.manage_cycles": "Manage examination cycles.",
                "departmental_exams.configure": "Configure authorized grouped course examinations.",
                "departmental_exams.review_generate": "Review assigned grouped course examinations.",
            },
        )

    def test_rbac_reverse_preserves_permission_referenced_by_custom_role_and_user(self):
        permission = Permission.objects.get(code="departmental_exams.configure")
        role = Role.objects.create(code="CUSTOM_DE_CONFIG", name="Custom DE Config")
        role_link = RolePermission.objects.create(role=role, permission=permission)
        user = get_user_model().objects.create_user(
            username="custom-de-config-user",
            email="custom-de-config@example.edu",
            password="TestPass123!",
        )
        user_link = UserPermission.objects.create(
            user=user,
            permission=permission,
            grant_type=UserPermission.GrantType.ALLOW,
        )

        self.rbac_migration.unseed_permissions(django_apps, None)

        self.assertTrue(Permission.objects.filter(pk=permission.pk).exists())
        self.assertTrue(RolePermission.objects.filter(pk=role_link.pk).exists())
        self.assertTrue(UserPermission.objects.filter(pk=user_link.pk).exists())

    def test_navigation_seed_is_idempotent_and_reverse_is_exactly_scoped(self):
        self.stage5_navigation_migration.unseed(django_apps, None)
        self.navigation_migration.unseed_menu(django_apps, None)
        self.navigation_migration.seed_menu(django_apps, None)
        self.navigation_migration.seed_menu(django_apps, None)
        self.stage5_navigation_migration.seed(django_apps, None)
        self.stage5_navigation_migration.seed(django_apps, None)

        admin_group = MenuGroup.objects.get(portal="ADMIN", code="DEPARTMENTAL_EXAMS")
        admin_cycles = MenuItem.objects.get(
            menu_group=admin_group,
            portal="ADMIN",
            code="DE_EXAM_CYCLES",
        )
        admin_assigned_courses = MenuItem.objects.get(
            menu_group=admin_group,
            portal="ADMIN",
            code="DE_EXAM_ASSIGNED_COURSES",
        )
        admin_contributor_monitoring = MenuItem.objects.get(
            menu_group=admin_group,
            portal="ADMIN",
            code="DE_EXAM_CONTRIBUTOR_MONITORING",
        )
        faculty_group = MenuGroup.objects.get(
            portal="FACULTY",
            code="DEPARTMENTAL_EXAMS",
        )
        faculty_contributions = MenuItem.objects.get(
            menu_group=faculty_group,
            portal="FACULTY",
            code="DE_EXAM_FACULTY_CONTRIBUTIONS",
        )
        self.assertEqual(
            MenuItem.objects.filter(portal="ADMIN", code="DE_EXAM_CYCLES").count(),
            1,
        )
        self.assertEqual(
            MenuItemPermission.objects.filter(
                menu_item=admin_cycles,
                permission__code="departmental_exams.manage_cycles",
            ).count(),
            1,
        )
        self.assertSetEqual(
            set(
                MenuItemPermission.objects.filter(
                    menu_item=admin_assigned_courses
                ).values_list("permission__code", flat=True)
            ),
            {
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            },
        )
        self.assertEqual(
            MenuItem.objects.filter(
                portal="ADMIN",
                code="DE_EXAM_CONTRIBUTOR_MONITORING",
            ).count(),
            1,
        )
        self.assertSetEqual(
            set(
                MenuItemPermission.objects.filter(
                    menu_item=admin_contributor_monitoring
                ).values_list("permission__code", flat=True)
            ),
            {
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            },
        )
        self.assertEqual(
            MenuItem.objects.filter(
                portal="FACULTY",
                code="DE_EXAM_FACULTY_CONTRIBUTIONS",
            ).count(),
            1,
        )
        self.assertSetEqual(
            set(
                MenuItemPermission.objects.filter(
                    menu_item=faculty_contributions
                ).values_list("permission__code", flat=True)
            ),
            {"faculty_portal.access"},
        )
        self.assertEqual(
            admin_cycles.route_name,
            "departmental_exams:cycle_list",
        )
        self.assertEqual(
            admin_assigned_courses.route_name,
            "departmental_exams:assigned_course_examinations",
        )
        self.assertEqual(
            admin_contributor_monitoring.route_name,
            "departmental_exams:contributor_monitoring",
        )
        self.assertEqual(
            faculty_contributions.route_name,
            "departmental_exams:contribution_list",
        )
        self.assertNotEqual(reverse(admin_cycles.route_name), "#")
        self.assertNotEqual(reverse(admin_assigned_courses.route_name), "#")
        self.assertNotEqual(reverse(admin_contributor_monitoring.route_name), "#")
        self.assertNotEqual(reverse(faculty_contributions.route_name), "#")
        self.assertFalse(
            MenuItem.objects.filter(
                code__in=["DE_EXAM_COURSE_SETUP", "DE_EXAM_CONTRIBUTIONS"]
            ).exists()
        )

        custom_permission = Permission.objects.create(
            code="custom_departmental_menu.read",
            module="custom_departmental_menu",
            action="read",
        )
        custom_group = MenuGroup.objects.create(
            portal="FACULTY",
            code="CUSTOM_DEPARTMENTAL_EXAMS",
            label="Custom Departmental Exams",
        )
        custom_item = MenuItem.objects.create(
            menu_group=custom_group,
            portal="FACULTY",
            code="DE_EXAM_CYCLES",
            label="Custom Faculty Cycles",
        )
        custom_link = MenuItemPermission.objects.create(
            menu_item=custom_item,
            permission=custom_permission,
        )

        self.stage5_navigation_migration.unseed(django_apps, None)

        self.assertTrue(MenuItem.objects.filter(pk=admin_cycles.pk).exists())
        self.assertTrue(
            MenuItem.objects.filter(pk=admin_assigned_courses.pk).exists()
        )
        self.assertFalse(
            MenuItem.objects.filter(pk=admin_contributor_monitoring.pk).exists()
        )
        self.assertFalse(
            MenuItem.objects.filter(pk=faculty_contributions.pk).exists()
        )
        self.assertTrue(
            MenuGroup.objects.filter(
                portal="ADMIN", code="DEPARTMENTAL_EXAMS"
            ).exists()
        )
        self.assertFalse(
            MenuGroup.objects.filter(
                portal="FACULTY", code="DEPARTMENTAL_EXAMS"
            ).exists()
        )
        self.assertTrue(MenuItem.objects.filter(pk=custom_item.pk).exists())
        self.assertTrue(MenuItemPermission.objects.filter(pk=custom_link.pk).exists())

        self.navigation_migration.unseed_menu(django_apps, None)

        self.assertFalse(MenuItem.objects.filter(pk=admin_cycles.pk).exists())
        self.assertFalse(
            MenuItem.objects.filter(pk=admin_assigned_courses.pk).exists()
        )
        self.assertFalse(MenuItemPermission.objects.filter(menu_item_id=admin_cycles.id).exists())
        self.assertFalse(MenuGroup.objects.filter(portal="ADMIN", code="DEPARTMENTAL_EXAMS").exists())
        self.assertTrue(MenuItem.objects.filter(pk=custom_item.pk).exists())
        self.assertTrue(MenuItemPermission.objects.filter(pk=custom_link.pk).exists())
