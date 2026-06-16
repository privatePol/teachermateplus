from django.db import migrations


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group, _ = MenuGroup.objects.update_or_create(
        portal="ADMIN",
        code="GRADING",
        defaults={"label": "Grading", "sort_order": 80, "is_active": True},
    )
    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="GRADE_ENCODING_CONTROL",
        defaults={
            "menu_group": group,
            "label": "Grade Encoding Access Control",
            "route_name": "admin_portal:grade_encoding_control_list",
            "sort_order": 72,
            "is_active": True,
        },
    )
    permission = Permission.objects.filter(code="grading_encoding_control.manage").first()
    if permission:
        MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed_menu(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    items = MenuItem.objects.filter(portal="ADMIN", code="GRADE_ENCODING_CONTROL")
    MenuItemPermission.objects.filter(menu_item__in=items).delete()
    items.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0006_seed_academic_performance_insights_menu"),
        ("rbac", "0016_seed_grade_encoding_control_permission"),
    ]

    operations = [
        migrations.RunPython(seed_menu, unseed_menu),
    ]
