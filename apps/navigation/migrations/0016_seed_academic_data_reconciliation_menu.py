from django.db import migrations


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group = MenuGroup.objects.filter(portal="ADMIN", code="ACADEMICS").first()
    permission = Permission.objects.filter(code="academic_data_reconciliation.view").first()
    if not group or not permission:
        return
    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="ACADEMIC_DATA_RECONCILIATION",
        defaults={
            "menu_group": group,
            "label": "Data Reconciliation",
            "route_name": "admin_portal:academic_data_reconciliation",
            "sort_order": 72,
            "is_active": True,
        },
    )
    MenuItemPermission.objects.filter(menu_item=item).exclude(permission=permission).delete()
    MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed_menu(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    items = MenuItem.objects.filter(portal="ADMIN", code="ACADEMIC_DATA_RECONCILIATION")
    MenuItemPermission.objects.filter(menu_item__in=items).delete()
    items.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0015_seed_academic_intervention_menu"),
        ("rbac", "0031_seed_academic_data_reconciliation_permission"),
    ]
    operations = [migrations.RunPython(seed_menu, unseed_menu)]
