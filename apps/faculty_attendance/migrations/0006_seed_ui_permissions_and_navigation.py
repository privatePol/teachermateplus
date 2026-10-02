from django.db import migrations


PERMISSIONS = (
    ("faculty_attendance.manage_routes", "manage_routes", "Create and revise personal checker route ordering within authorized scope."),
    ("faculty_attendance.print", "print", "Print frozen faculty-attendance checking rounds within authorized scope."),
)

ITEMS = (
    ("ADMIN", "ATTENDANCE_SETUP", "Attendance Setup", "faculty_attendance:setup", 10, "faculty_attendance.manage_schedules"),
    ("ADMIN", "ATTENDANCE_CHECKLIST", "Classroom Checklists", "faculty_attendance:checklist", 20, "faculty_attendance.encode"),
    ("ADMIN", "ATTENDANCE_RECONCILIATION", "Attendance Reconciliation", "faculty_attendance:reconciliation", 30, "faculty_attendance.reconcile"),
    ("FACULTY", "MY_ATTENDANCE", "My Attendance", "faculty_attendance:my_attendance", 10, "faculty_attendance.view"),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    for code, action, description in PERMISSIONS:
        Permission.objects.update_or_create(code=code, defaults={"module": "faculty_attendance", "action": action, "description": description, "is_active": True})
    for portal in ("ADMIN", "FACULTY"):
        group, _ = MenuGroup.objects.get_or_create(portal=portal, code="FACULTY_ATTENDANCE", defaults={"label": "Faculty Attendance", "icon": "bi bi-calendar-check", "sort_order": 65, "is_active": True})
        for item_portal, code, label, route_name, sort_order, permission_code in ITEMS:
            if item_portal != portal:
                continue
            item, _ = MenuItem.objects.update_or_create(portal=portal, code=code, defaults={"menu_group": group, "label": label, "route_name": route_name, "icon": "bi bi-check2-square", "sort_order": sort_order, "is_active": True})
            permission = Permission.objects.get(code=permission_code)
            MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    for portal, code, _label, _route, _order, _permission in ITEMS:
        item = MenuItem.objects.filter(portal=portal, code=code).first()
        if item:
            MenuItemPermission.objects.filter(menu_item=item).delete()
            item.delete()
    for group in MenuGroup.objects.filter(code="FACULTY_ATTENDANCE"):
        if not group.items.exists():
            group.delete()
    for code, _action, _description in PERMISSIONS:
        permission = Permission.objects.filter(code=code).first()
        if permission and not RolePermission.objects.filter(permission=permission).exists() and not UserPermission.objects.filter(permission=permission).exists() and not MenuItemPermission.objects.filter(permission=permission).exists():
            permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("faculty_attendance", "0005_checkinground_checking_end_date_and_more"),
        ("navigation", "0026_exam_workflow_labels"),
        ("rbac", "0036_seed_planning_readiness_permissions"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
