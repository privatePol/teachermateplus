from django.db import migrations


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group, _ = MenuGroup.objects.update_or_create(
        portal="ADMIN",
        code="ENROLLMENT",
        defaults={"label": "Enrollment", "sort_order": 60, "is_active": True},
    )
    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="ENROLLMENT_ADJUSTMENTS",
        defaults={
            "menu_group": group,
            "label": "Enrollment Adjustments",
            "route_name": "admin_portal:enrollment_adjustments",
            "sort_order": 25,
            "is_active": True,
        },
    )
    permission = Permission.objects.filter(code="enrollment_adjustment.view").first()
    if permission:
        MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed_menu(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    items = MenuItem.objects.filter(portal="ADMIN", code="ENROLLMENT_ADJUSTMENTS")
    MenuItemPermission.objects.filter(menu_item__in=items).delete()
    items.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0007_seed_grade_encoding_control_menu"),
        ("rbac", "0017_seed_enrollment_adjustment_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_menu, unseed_menu),
    ]
