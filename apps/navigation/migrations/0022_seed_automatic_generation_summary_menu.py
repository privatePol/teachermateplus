from django.db import migrations


MENU_CODE = "DE_EXAM_AUTOMATIC_GENERATION_SUMMARY"
PERMISSION_CODES = (
    "departmental_exams.view_generated_exams",
    "departmental_exams.manage_exam_generation",
)


def seed(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group = MenuGroup.objects.filter(
        portal="ADMIN",
        code="DEPARTMENTAL_EXAMS",
    ).first()
    if not group:
        return
    item, created = MenuItem.objects.get_or_create(
        portal="ADMIN",
        code=MENU_CODE,
        defaults={
            "menu_group": group,
            "label": "Automatic Generation Summary",
            "route_name": "departmental_exams:automatic_generation_summary_entry",
            "sort_order": 20,
            "is_active": True,
        },
    )
    if not created and item.menu_group_id != group.id:
        return
    permissions = Permission.objects.filter(
        code__in=PERMISSION_CODES,
        is_active=True,
    )
    for permission in permissions:
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
        permission__code__in=PERMISSION_CODES,
    ).delete()
    if not MenuItemPermission.objects.filter(menu_item=item).exists():
        item.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0021_add_questionnaire_output_permissions"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
