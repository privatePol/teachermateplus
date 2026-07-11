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
        code="TENANT_DATA_EXPORT",
        defaults={
            "menu_group": group,
            "label": "Secure Tenant Data Export",
            "route_name": "admin_portal:tenant_data_export",
            "sort_order": 65,
            "is_active": True,
        },
    )
    permission = Permission.objects.filter(code="tenant_data_export.execute").first()
    if permission:
        MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed_menu(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    items = MenuItem.objects.filter(portal="ADMIN", code="TENANT_DATA_EXPORT")
    MenuItemPermission.objects.filter(menu_item__in=items).delete()
    items.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0010_update_faculty_monitor_menu_permissions"),
        ("rbac", "0024_seed_tenant_data_export_permission"),
    ]

    operations = [
        migrations.RunPython(seed_menu, unseed_menu),
    ]
