from django.db import migrations


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group = MenuGroup.objects.filter(portal="ADMIN", code="GRADING").first()
    permission = Permission.objects.filter(code="faculty_activity_monitor.read").first()
    if not group or not permission:
        return
    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="GRADE_SUBMISSION_READINESS",
        defaults={
            "menu_group": group,
            "label": "Submission Readiness",
            "route_name": "admin_portal:grade_submission_readiness",
            "sort_order": 74,
            "is_active": True,
        },
    )
    MenuItemPermission.objects.filter(menu_item=item).exclude(permission=permission).delete()
    MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


def unseed_menu(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    items = MenuItem.objects.filter(portal="ADMIN", code="GRADE_SUBMISSION_READINESS")
    MenuItemPermission.objects.filter(menu_item__in=items).delete()
    items.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0013_seed_orientation_feedback_menu"),
        ("rbac", "0029_seed_orientation_feedback_permissions"),
    ]
    operations = [migrations.RunPython(seed_menu, unseed_menu)]
