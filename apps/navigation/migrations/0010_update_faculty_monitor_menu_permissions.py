from django.db import migrations


MENU_PERMISSION_MAP = {
    "FACULTY_ACTIVITY_MONITOR": "faculty_activity_monitor.read",
    "FACULTY_FINAL_CLEARANCE": "faculty_final_clearance.read",
}


def update_menu_permissions(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    for menu_code, permission_code in MENU_PERMISSION_MAP.items():
        menu_item = MenuItem.objects.filter(portal="ADMIN", code=menu_code).first()
        permission = Permission.objects.filter(code=permission_code).first()
        if not menu_item or not permission:
            continue
        MenuItemPermission.objects.filter(menu_item=menu_item).exclude(permission=permission).delete()
        MenuItemPermission.objects.get_or_create(menu_item=menu_item, permission=permission)


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0009_seed_class_list_change_request_menu"),
        ("rbac", "0021_seed_faculty_monitor_permissions"),
    ]

    operations = [
        migrations.RunPython(update_menu_permissions, migrations.RunPython.noop),
    ]
