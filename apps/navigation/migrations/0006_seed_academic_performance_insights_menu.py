from django.db import migrations


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group = MenuGroup.objects.filter(portal="ADMIN", code="GRADING").first()
    permission = Permission.objects.filter(code="grading_analytics.read").first()
    if not group or not permission:
        return
    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="ACADEMIC_PERFORMANCE_INSIGHTS",
        defaults={
            "menu_group": group,
            "label": "Academic Performance Insights",
            "route_name": "admin_portal:academic_performance_insights",
            "sort_order": 76,
            "is_active": True,
        },
    )
    MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def remove_menu(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    items = MenuItem.objects.filter(
        portal="ADMIN",
        code="ACADEMIC_PERFORMANCE_INSIGHTS",
    )
    MenuItemPermission.objects.filter(menu_item__in=items).delete()
    items.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0005_move_student_enrollment_query_to_enrollment"),
        ("rbac", "0014_college_dean_read_only_baseline"),
    ]

    operations = [
        migrations.RunPython(seed_menu, remove_menu),
    ]
