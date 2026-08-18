from django.db import migrations


MENU_CODE = "DE_EXAM_PLANNING_READINESS"
PERMISSION_CODE = "departmental_exams.view_planning_readiness"


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group = MenuGroup.objects.filter(
        portal="ADMIN",
        code="DEPARTMENTAL_EXAMS",
    ).first()
    permission = Permission.objects.filter(
        code=PERMISSION_CODE,
        is_active=True,
    ).first()
    if not group or not permission:
        return
    item, created = MenuItem.objects.get_or_create(
        portal="ADMIN",
        code=MENU_CODE,
        defaults={
            "menu_group": group,
            "label": "Planning & Readiness",
            "route_name": "departmental_exams:planning_readiness",
            "sort_order": 20,
            "is_active": True,
        },
    )
    if not created and item.menu_group_id != group.id:
        return
    MenuItemPermission.objects.get_or_create(
        menu_item=item,
        permission=permission,
    )


def unseed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    group = MenuGroup.objects.filter(
        portal="ADMIN",
        code="DEPARTMENTAL_EXAMS",
    ).first()
    if not group:
        return
    item = MenuItem.objects.filter(
        menu_group=group,
        portal="ADMIN",
        code=MENU_CODE,
    ).first()
    if not item:
        return
    MenuItemPermission.objects.filter(
        menu_item=item,
        permission__code=PERMISSION_CODE,
    ).delete()
    if not MenuItemPermission.objects.filter(menu_item=item).exists():
        item.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0023_add_answer_key_release_permission"),
        ("rbac", "0036_seed_planning_readiness_permissions"),
    ]

    operations = [migrations.RunPython(seed_menu, unseed_menu)]
