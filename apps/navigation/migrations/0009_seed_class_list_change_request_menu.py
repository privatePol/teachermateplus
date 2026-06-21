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
        code="CLASS_LIST_CHANGE_REQUESTS",
        defaults={
            "menu_group": group,
            "label": "Class List Change Requests",
            "route_name": "admin_portal:class_list_change_request_list",
            "sort_order": 26,
            "is_active": True,
        },
    )
    permission = Permission.objects.filter(code="class_list_change_requests.view").first()
    if permission:
        MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed_menu(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    items = MenuItem.objects.filter(portal="ADMIN", code="CLASS_LIST_CHANGE_REQUESTS")
    MenuItemPermission.objects.filter(menu_item__in=items).delete()
    items.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0008_seed_enrollment_adjustment_menu"),
        ("rbac", "0020_seed_class_list_change_request_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_menu, unseed_menu),
    ]
