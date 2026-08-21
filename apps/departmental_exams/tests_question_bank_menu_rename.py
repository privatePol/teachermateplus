from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission


class ExamQuestionBankNavigationMigrationTests(TestCase):
    def setUp(self):
        self.migration = import_module(
            "apps.navigation.migrations.0025_rename_faculty_question_bank_menu"
        )
        self.faculty_group = MenuGroup.objects.get(
            portal="FACULTY",
            code="DEPARTMENTAL_EXAMS",
        )
        self.faculty_item = MenuItem.objects.get(
            menu_group=self.faculty_group,
            portal="FACULTY",
            code="DE_EXAM_FACULTY_CONTRIBUTIONS",
        )
        self.admin_item = MenuItem.objects.get(
            portal="ADMIN",
            code="DE_EXAM_CONTRIBUTOR_MONITORING",
        )

    def _faculty_item_state(self):
        self.faculty_item.refresh_from_db()
        return {
            "menu_group_id": self.faculty_item.menu_group_id,
            "portal": self.faculty_item.portal,
            "code": self.faculty_item.code,
            "route_name": self.faculty_item.route_name,
            "sort_order": self.faculty_item.sort_order,
            "is_active": self.faculty_item.is_active,
            "icon": self.faculty_item.icon,
            "parent_id": self.faculty_item.parent_id,
        }

    def _permission_ids(self):
        return list(
            MenuItemPermission.objects.filter(menu_item=self.faculty_item)
            .order_by("permission_id")
            .values_list("permission_id", flat=True)
        )

    def test_forward_changes_only_exact_owned_faculty_label(self):
        self.migration.rename_to_question_contributions(django_apps, None)
        self.faculty_item.refresh_from_db()
        self.assertEqual(self.faculty_item.label, "Question Contributions")

        unrelated = MenuItem.objects.create(
            menu_group=self.faculty_group,
            portal="FACULTY",
            code="CUSTOM_QUESTION_CONTRIBUTIONS",
            label="Question Contributions",
            route_name="",
            sort_order=99,
            is_active=True,
        )
        item_state = self._faculty_item_state()
        permission_ids = self._permission_ids()
        admin_state = (
            self.admin_item.label,
            self.admin_item.menu_group_id,
            self.admin_item.route_name,
            self.admin_item.sort_order,
            self.admin_item.is_active,
        )

        self.migration.rename_to_question_bank(django_apps, None)

        self.faculty_item.refresh_from_db()
        unrelated.refresh_from_db()
        self.admin_item.refresh_from_db()
        self.assertEqual(self.faculty_item.label, "Question Bank")
        self.assertEqual(self._faculty_item_state(), item_state)
        self.assertEqual(self._permission_ids(), permission_ids)
        self.assertEqual(unrelated.label, "Question Contributions")
        self.assertEqual(
            (
                self.admin_item.label,
                self.admin_item.menu_group_id,
                self.admin_item.route_name,
                self.admin_item.sort_order,
                self.admin_item.is_active,
            ),
            admin_state,
        )
        self.assertEqual(self.admin_item.label, "Contributor Completion")
        self.assertEqual(self.faculty_item.code, "DE_EXAM_FACULTY_CONTRIBUTIONS")
        self.assertEqual(
            self.faculty_item.route_name,
            "departmental_exams:contribution_list",
        )

    def test_reverse_changes_only_exact_owned_faculty_label(self):
        item_state = self._faculty_item_state()
        permission_ids = self._permission_ids()
        admin_label = self.admin_item.label

        self.migration.rename_to_question_contributions(django_apps, None)

        self.faculty_item.refresh_from_db()
        self.admin_item.refresh_from_db()
        self.assertEqual(self.faculty_item.label, "Question Contributions")
        self.assertEqual(self._faculty_item_state(), item_state)
        self.assertEqual(self._permission_ids(), permission_ids)
        self.assertEqual(self.admin_item.label, admin_label)

    def test_forward_ignores_same_code_outside_owned_group(self):
        custom_group = MenuGroup.objects.create(
            portal="FACULTY",
            code="CUSTOM_DEPARTMENTAL_EXAMS",
            label="Custom Departmental Exams",
        )
        self.migration.rename_to_question_contributions(django_apps, None)
        self.faculty_item.menu_group = custom_group
        self.faculty_item.save(update_fields=["menu_group", "updated_at"])

        self.migration.rename_to_question_bank(django_apps, None)

        self.faculty_item.refresh_from_db()
        self.assertEqual(self.faculty_item.label, "Question Contributions")
        self.assertEqual(self.faculty_item.menu_group_id, custom_group.id)
