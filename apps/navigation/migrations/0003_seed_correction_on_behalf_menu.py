from django.db import migrations


def seed_menu_item(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group = MenuGroup.objects.filter(portal="ADMIN", code="GRADING").first()
    permission = Permission.objects.filter(code="corrections.create_on_behalf", is_active=True).first()
    if not group or not permission:
        return

    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="GRADE_CORRECTION_ON_BEHALF",
        defaults={
            "menu_group_id": group.id,
            "label": "Create Correction On Behalf",
            "route_name": "admin_portal:grade_correction_request_create_on_behalf",
            "sort_order": 91,
            "is_active": True,
        },
    )
    MenuItemPermission.objects.get_or_create(menu_item_id=item.id, permission_id=permission.id)


class Migration(migrations.Migration):

    dependencies = [
        ("navigation", "0002_alter_menuitem_menu_group_alter_menuitem_parent_and_more"),
        ("rbac", "0007_assign_correction_on_behalf_permission"),
    ]

    operations = [
        migrations.RunPython(seed_menu_item, migrations.RunPython.noop),
    ]
