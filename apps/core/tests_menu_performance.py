from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.core.services.menu import MenuService
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission


class MenuServicePerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="menu-query-user",
            email="menu-query-user@example.edu",
            password="MenuQueryPass123!",
        )
        cls.permission = Permission.objects.create(
            code="menu_query_test.read",
            module="menu_query_test",
            action="read",
            is_active=True,
        )

    def _add_group_with_items(self, *, portal, code, item_count):
        group = MenuGroup.objects.create(
            portal=portal,
            code=code,
            label=code,
            sort_order=900,
            is_active=True,
        )
        for number in range(item_count):
            item = MenuItem.objects.create(
                menu_group=group,
                portal=portal,
                code=f"{code}_ITEM_{number}",
                label=f"Item {number}",
                sort_order=number,
                is_active=True,
            )
            MenuItemPermission.objects.create(menu_item=item, permission=self.permission)
        return group

    def _query_count(self, portal):
        with CaptureQueriesContext(connection) as captured:
            MenuService.get_menu_tree(
                self.user,
                portal=portal,
                effective_codes={self.permission.code},
            )
        return len(captured)

    def test_admin_menu_query_count_does_not_grow_with_menu_items(self):
        self._add_group_with_items(portal="ADMIN", code="QUERY_SMALL", item_count=1)
        small_count = self._query_count("ADMIN")

        self._add_group_with_items(portal="ADMIN", code="QUERY_LARGE", item_count=30)
        large_count = self._query_count("ADMIN")

        self.assertLessEqual(large_count, 6)
        self.assertLessEqual(large_count, small_count + 1)

    def test_faculty_menu_visibility_and_order_are_unchanged(self):
        group = self._add_group_with_items(portal="FACULTY", code="FACULTY_QUERY", item_count=2)
        hidden_permission = Permission.objects.create(
            code="menu_query_test.hidden",
            module="menu_query_test",
            action="hidden",
            is_active=True,
        )
        hidden_item = MenuItem.objects.create(
            menu_group=group,
            portal="FACULTY",
            code="FACULTY_QUERY_HIDDEN",
            label="Hidden item",
            sort_order=3,
            is_active=True,
        )
        MenuItemPermission.objects.create(menu_item=hidden_item, permission=hidden_permission)

        tree = MenuService.get_menu_tree(
            self.user,
            portal="FACULTY",
            effective_codes={self.permission.code},
        )
        group_node = next(row for row in tree if row["group"].id == group.id)

        self.assertEqual(
            [node["item"].code for node in group_node["items"]],
            ["FACULTY_QUERY_ITEM_0", "FACULTY_QUERY_ITEM_1"],
        )
