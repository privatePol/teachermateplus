from django.db import migrations


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group, _ = MenuGroup.objects.update_or_create(
        portal="ADMIN",
        code="IMPORTS",
        defaults={"label": "Tools", "sort_order": 95, "is_active": True},
    )
    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="FACULTY_FEEDBACK",
        defaults={
            "menu_group": group,
            "label": "Faculty Feedback",
            "route_name": "admin_portal:faculty_feedback",
            "sort_order": 66,
            "is_active": True,
        },
    )
    permission = Permission.objects.filter(code="faculty_feedback.read").first()
    if permission:
        MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed_menu(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    items = MenuItem.objects.filter(portal="ADMIN", code="FACULTY_FEEDBACK")
    MenuItemPermission.objects.filter(menu_item__in=items).delete()
    items.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0011_seed_tenant_data_export_menu"),
        ("rbac", "0025_seed_faculty_feedback_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_menu, unseed_menu),
    ]
