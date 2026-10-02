from django.db import migrations


PERMISSIONS = (
    ("faculty_attendance.dtr_view", "dtr_view", "Review scoped faculty DTR hours and complete-campus cutoff summary."),
    ("faculty_attendance.dtr_edit", "dtr_edit", "Record or correct dated admin hours, leave credits and other deductions."),
    ("faculty_attendance.dtr_finalize", "dtr_finalize", "Finalize and revise faculty DTR snapshots after published attendance review."),
    ("faculty_attendance.dtr_print", "dtr_print", "Print authorized final DTRs and cutoff hour summaries."),
    ("faculty_attendance.dtr_ac_summary", "dtr_ac_summary", "View the restricted AC department DTR summary."),
)

ITEMS = (
    ("ADMIN", "ATTENDANCE_DTR", "Faculty DTR", "faculty_attendance:dtr_review", 50, "faculty_attendance.dtr_view"),
    ("FACULTY", "MY_DTR", "My DTR", "faculty_attendance:my_dtr", 20, "faculty_attendance.view"),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    for code, action, description in PERMISSIONS:
        Permission.objects.update_or_create(
            code=code, defaults={
                "module": "faculty_attendance", "action": action,
                "description": description, "is_active": True,
            },
        )
    for portal, code, label, route, order, permission_code in ITEMS:
        group, _ = MenuGroup.objects.get_or_create(
            portal=portal, code="FACULTY_ATTENDANCE",
            defaults={"label": "Faculty Attendance", "icon": "bi bi-calendar-check", "sort_order": 65, "is_active": True},
        )
        item, _ = MenuItem.objects.update_or_create(
            portal=portal, code=code,
            defaults={
                "menu_group": group, "label": label, "route_name": route,
                "icon": "bi bi-file-earmark-spreadsheet", "sort_order": order, "is_active": True,
            },
        )
        permission = Permission.objects.get(code=permission_code)
        MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    for portal, code, _label, _route, _order, _permission in ITEMS:
        item = MenuItem.objects.filter(portal=portal, code=code).first()
        if item:
            MenuItemPermission.objects.filter(menu_item=item).delete()
            item.delete()
    for code, _action, _description in PERMISSIONS:
        permission = Permission.objects.filter(code=code).first()
        if permission and not RolePermission.objects.filter(permission=permission).exists() and not UserPermission.objects.filter(permission=permission).exists() and not MenuItemPermission.objects.filter(permission=permission).exists():
            permission.delete()


class Migration(migrations.Migration):
    dependencies = [("faculty_attendance", "0012_dtradjustment_facultydtr")]
    operations = [migrations.RunPython(seed, unseed)]
