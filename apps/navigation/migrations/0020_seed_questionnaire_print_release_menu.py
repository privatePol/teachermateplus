from django.db import migrations


MENU_CODE = "DE_EXAM_QUESTIONNAIRE_PRINT_RELEASE"
PERMISSION_CODE = "departmental_exams.manage_exam_generation"


def seed(apps, schema_editor):
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
            "label": "Questionnaire Print Release",
            "route_name": "departmental_exams:questionnaire_print_release",
            "sort_order": 40,
            "is_active": True,
        },
    )
    if not created and item.menu_group_id != group.id:
        return
    MenuItemPermission.objects.get_or_create(
        menu_item=item,
        permission=permission,
    )


def unseed(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    item = MenuItem.objects.filter(portal="ADMIN", code=MENU_CODE).first()
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
        ("navigation", "0019_add_departmental_exam_automatic_permissions"),
    ]
    operations = [migrations.RunPython(seed, unseed)]
