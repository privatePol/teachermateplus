from __future__ import annotations

from dataclasses import dataclass

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission


@dataclass(frozen=True)
class SeededNavigationRepair:
    permission_code: str
    permission_module: str
    permission_action: str
    permission_description: str
    menu_item_code: str
    menu_item_label: str
    route_name: str
    preferred_menu_group_code: str
    sort_order: int
    role_codes: tuple[str, ...]
    fallback_menu_group_code: str | None = None
    extra_role_permission_codes: tuple[str, ...] = ()


class Command(BaseCommand):
    help = "Repair known seeded RBAC permissions and Admin Portal navigation rows without changing migrations."

    REPAIRS = (
        SeededNavigationRepair(
            permission_code="corrections.create_on_behalf",
            permission_module="corrections",
            permission_action="create_on_behalf",
            permission_description="Create grade correction petitions on behalf of the original faculty member.",
            menu_item_code="GRADE_CORRECTION_ON_BEHALF",
            menu_item_label="Create Correction On Behalf",
            route_name="admin_portal:grade_correction_request_create_on_behalf",
            preferred_menu_group_code="GRADING",
            sort_order=91,
            role_codes=("AC", "DEAN", "CAMPUS_ADMIN", "TENANT_ADMIN"),
            extra_role_permission_codes=("corrections.read",),
        ),
        SeededNavigationRepair(
            permission_code="student_enrollment_query.read",
            permission_module="student_enrollment_query",
            permission_action="read",
            permission_description="Read consolidated student enrollment and grade records.",
            menu_item_code="STUDENT_ENROLLMENT_QUERY",
            menu_item_label="Student Enrollment Query",
            route_name="admin_portal:student_enrollment_query",
            preferred_menu_group_code="ENROLLMENT",
            fallback_menu_group_code="STUDENTS",
            sort_order=20,
            role_codes=("SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR"),
        ),
    )

    def handle(self, *args, **options):
        self.stdout.write("Repairing seeded RBAC and navigation rows...")
        summary = {
            "permissions_created": 0,
            "permissions_updated": 0,
            "permissions_ok": 0,
            "menu_items_created": 0,
            "menu_items_updated": 0,
            "menu_items_ok": 0,
            "role_permissions_created": 0,
            "role_permissions_existing": 0,
            "menu_permissions_created": 0,
            "menu_permissions_existing": 0,
            "warnings": 0,
        }

        with transaction.atomic():
            for repair in self.REPAIRS:
                self.stdout.write(f"Checking {repair.permission_code} / {repair.menu_item_code}")
                permission = self._repair_permission(repair, summary)
                menu_item = self._repair_menu_item(repair, summary)
                self._repair_menu_permission(menu_item, permission, summary)
                self._repair_role_permissions(repair.role_codes, permission, repair.permission_code, summary)
                for extra_permission_code in repair.extra_role_permission_codes:
                    extra_permission = Permission.objects.filter(code=extra_permission_code, is_active=True).first()
                    if extra_permission:
                        self._repair_role_permissions(repair.role_codes, extra_permission, extra_permission_code, summary)
                    else:
                        self._warn(
                            summary,
                            f"Optional permission {extra_permission_code} is missing; role links were skipped.",
                        )

        self.stdout.write(
            self.style.SUCCESS(
                "Repair complete: "
                f"permissions created={summary['permissions_created']}, "
                f"updated={summary['permissions_updated']}, ok={summary['permissions_ok']}; "
                f"menu items created={summary['menu_items_created']}, "
                f"updated={summary['menu_items_updated']}, ok={summary['menu_items_ok']}; "
                f"role-permission links created={summary['role_permissions_created']}, "
                f"existing={summary['role_permissions_existing']}; "
                f"menu-permission links created={summary['menu_permissions_created']}, "
                f"existing={summary['menu_permissions_existing']}; "
                f"warnings={summary['warnings']}."
            )
        )

    def _repair_permission(self, repair: SeededNavigationRepair, summary: dict[str, int]) -> Permission:
        defaults = {
            "module": repair.permission_module,
            "action": repair.permission_action,
            "description": repair.permission_description,
            "is_active": True,
        }
        existing = Permission.objects.filter(code=repair.permission_code).first()
        changed = bool(
            existing
            and any(getattr(existing, field) != value for field, value in defaults.items())
        )
        permission, created = Permission.objects.update_or_create(
            code=repair.permission_code,
            defaults=defaults,
        )
        if created:
            summary["permissions_created"] += 1
            self.stdout.write(f"  Created permission {permission.code}.")
        elif changed:
            summary["permissions_updated"] += 1
            self.stdout.write(f"  Updated permission {permission.code}.")
        else:
            summary["permissions_ok"] += 1
            self.stdout.write(f"  Permission {permission.code} already ok.")
        return permission

    def _repair_menu_item(self, repair: SeededNavigationRepair, summary: dict[str, int]) -> MenuItem | None:
        menu_group = self._resolve_menu_group(repair, summary)
        if not menu_group:
            self._warn(
                summary,
                f"Menu item {repair.menu_item_code} skipped because no usable ADMIN menu group exists.",
            )
            return None

        defaults = {
            "menu_group": menu_group,
            "label": repair.menu_item_label,
            "route_name": repair.route_name,
            "sort_order": repair.sort_order,
            "is_active": True,
        }
        existing = MenuItem.objects.filter(portal=MenuGroup.Portal.ADMIN, code=repair.menu_item_code).first()
        changed = bool(
            existing
            and any(getattr(existing, field) != value for field, value in defaults.items())
        )
        menu_item, created = MenuItem.objects.update_or_create(
            portal=MenuGroup.Portal.ADMIN,
            code=repair.menu_item_code,
            defaults=defaults,
        )
        if created:
            summary["menu_items_created"] += 1
            self.stdout.write(f"  Created menu item {menu_item.code}.")
        elif changed:
            summary["menu_items_updated"] += 1
            self.stdout.write(f"  Updated menu item {menu_item.code}.")
        else:
            summary["menu_items_ok"] += 1
            self.stdout.write(f"  Menu item {menu_item.code} already ok.")
        return menu_item

    def _resolve_menu_group(self, repair: SeededNavigationRepair, summary: dict[str, int]) -> MenuGroup | None:
        preferred = MenuGroup.objects.filter(
            portal=MenuGroup.Portal.ADMIN,
            code=repair.preferred_menu_group_code,
            is_active=True,
        ).first()
        if preferred:
            return preferred
        self._warn(summary, f"ADMIN menu group {repair.preferred_menu_group_code} is missing.")
        if not repair.fallback_menu_group_code:
            return None
        fallback = MenuGroup.objects.filter(
            portal=MenuGroup.Portal.ADMIN,
            code=repair.fallback_menu_group_code,
            is_active=True,
        ).first()
        if fallback:
            self.stdout.write(f"  Using fallback ADMIN menu group {repair.fallback_menu_group_code}.")
            return fallback
        self._warn(summary, f"Fallback ADMIN menu group {repair.fallback_menu_group_code} is missing.")
        return None

    def _repair_menu_permission(
        self,
        menu_item: MenuItem | None,
        permission: Permission,
        summary: dict[str, int],
    ) -> None:
        if not menu_item:
            return
        _, created = MenuItemPermission.objects.get_or_create(menu_item=menu_item, permission=permission)
        if created:
            summary["menu_permissions_created"] += 1
            self.stdout.write(f"  Created menu-permission link {menu_item.code} -> {permission.code}.")
        else:
            summary["menu_permissions_existing"] += 1
            self.stdout.write(f"  Menu-permission link {menu_item.code} -> {permission.code} already exists.")

    def _repair_role_permissions(
        self,
        role_codes: tuple[str, ...],
        permission: Permission,
        permission_code: str,
        summary: dict[str, int],
    ) -> None:
        for role_code in role_codes:
            role = Role.objects.filter(code=role_code, is_active=True).first()
            if not role:
                self._warn(summary, f"Role {role_code} is missing; {permission_code} was not linked to it.")
                continue
            _, created = RolePermission.objects.get_or_create(role=role, permission=permission)
            if created:
                summary["role_permissions_created"] += 1
                self.stdout.write(f"  Created role-permission link {role.code} -> {permission.code}.")
            else:
                summary["role_permissions_existing"] += 1
                self.stdout.write(f"  Role-permission link {role.code} -> {permission.code} already exists.")

    def _warn(self, summary: dict[str, int], message: str) -> None:
        summary["warnings"] += 1
        self.stdout.write(self.style.WARNING(f"  WARNING: {message}"))
