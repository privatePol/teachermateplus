from django.db import migrations


PERMISSION = (
    "faculty_attendance.publish_cutoff",
    "publish_cutoff",
    "Review and publish a complete-campus immutable faculty-attendance cutoff within authorized scope.",
)


def seed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    code, action, description = PERMISSION
    permission, _created = Permission.objects.update_or_create(
        code=code,
        defaults={"module": "faculty_attendance", "action": action, "description": description, "is_active": True},
    )
    group, _created = MenuGroup.objects.get_or_create(
        portal="ADMIN",
        code="FACULTY_ATTENDANCE",
        defaults={"label": "Faculty Attendance", "icon": "bi bi-calendar-check", "sort_order": 65, "is_active": True},
    )
    item, _created = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="ATTENDANCE_CUTOFF_REVIEW",
        defaults={
            "menu_group": group,
            "label": "Campus Attendance Cutoff",
            "route_name": "faculty_attendance:cutoff_review",
            "icon": "bi bi-clipboard2-check",
            "sort_order": 40,
            "is_active": True,
        },
    )
    MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    item = MenuItem.objects.filter(portal="ADMIN", code="ATTENDANCE_CUTOFF_REVIEW").first()
    if item:
        MenuItemPermission.objects.filter(menu_item=item).delete()
        item.delete()
    permission = Permission.objects.filter(code=PERMISSION[0]).first()
    if permission and not RolePermission.objects.filter(permission=permission).exists() and not UserPermission.objects.filter(permission=permission).exists() and not MenuItemPermission.objects.filter(permission=permission).exists():
        permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("faculty_attendance", "0010_daily_encoding_cutoff_publication"),
        ("navigation", "0026_exam_workflow_labels"),
        ("rbac", "0036_seed_planning_readiness_permissions"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
