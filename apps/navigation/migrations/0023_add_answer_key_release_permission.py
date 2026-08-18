from django.db import migrations


MENU_CODE = "DE_EXAM_QUESTIONNAIRE_PRINT_RELEASE"
PERMISSION_CODE = "departmental_exams.release_answer_keys"


def add_permission(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")
    item = MenuItem.objects.filter(portal="ADMIN", code=MENU_CODE).first()
    permission = Permission.objects.filter(
        code=PERMISSION_CODE,
        is_active=True,
    ).first()
    if item and permission:
        MenuItemPermission.objects.get_or_create(
            menu_item=item,
            permission=permission,
        )


def remove_permission(apps, schema_editor):
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    MenuItemPermission.objects.filter(
        menu_item__portal="ADMIN",
        menu_item__code=MENU_CODE,
        permission__code=PERMISSION_CODE,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0022_seed_automatic_generation_summary_menu"),
        ("rbac", "0035_seed_answer_key_release_permission"),
    ]

    operations = [migrations.RunPython(add_permission, remove_permission)]
