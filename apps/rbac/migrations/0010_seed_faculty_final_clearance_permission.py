from django.db import migrations


def seed_faculty_final_clearance_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")

    permission, _ = Permission.objects.update_or_create(
        code="faculty_final_clearance.read",
        defaults={
            "module": "faculty_final_clearance",
            "action": "read",
            "description": "Allows previewing and verifying Faculty Final Clearance reports.",
            "is_active": True,
        },
    )

    old_permission = Permission.objects.filter(code="faculty_assignments.read").first()
    if old_permission:
        role_ids = RolePermission.objects.filter(permission=old_permission).values_list("role_id", flat=True)
        for role_id in role_ids:
            RolePermission.objects.get_or_create(role_id=role_id, permission=permission)

    menu_item = MenuItem.objects.filter(code="FACULTY_FINAL_CLEARANCE").first()
    if menu_item:
        if old_permission:
            MenuItemPermission.objects.filter(menu_item=menu_item, permission=old_permission).delete()
        MenuItemPermission.objects.get_or_create(menu_item=menu_item, permission=permission)


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0003_seed_correction_on_behalf_menu"),
        ("rbac", "0009_seed_students_import_permission"),
    ]

    operations = [
        migrations.RunPython(seed_faculty_final_clearance_permission, migrations.RunPython.noop),
    ]
