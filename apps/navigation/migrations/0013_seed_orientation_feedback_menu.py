from django.db import migrations


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")
    group, _ = MenuGroup.objects.get_or_create(
        portal="ADMIN",
        code="IMPORTS",
        defaults={"label": "Tools", "sort_order": 95, "is_active": True},
    )
    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="ORIENTATION_FEEDBACK",
        defaults={
            "menu_group": group,
            "label": "Orientation Feedback",
            "route_name": "orientation_feedback:session_list",
            "sort_order": 67,
            "is_active": True,
        },
    )
    permission = Permission.objects.filter(code="orientation_feedback.view").first()
    if permission:
        MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed_menu(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    items = MenuItem.objects.filter(portal="ADMIN", code="ORIENTATION_FEEDBACK")
    MenuItemPermission.objects.filter(menu_item__in=items).delete()
    items.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0012_seed_faculty_feedback_menu"),
        ("rbac", "0029_seed_orientation_feedback_permissions"),
    ]
    operations = [migrations.RunPython(seed_menu, unseed_menu)]
