from django.db import migrations


MENU_CODE = "DE_EXAM_QUESTIONNAIRE_PRINT_RELEASE"
PERMISSION_CODES = (
    "departmental_exams.print_generated_exams",
    "departmental_exams.audit_generated_exams",
)


def add_permissions(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")
    item = MenuItem.objects.filter(portal="ADMIN", code=MENU_CODE).first()
    if not item:
        return
    for code in PERMISSION_CODES:
        permission = Permission.objects.filter(code=code, is_active=True).first()
        if permission:
            MenuItemPermission.objects.get_or_create(
                menu_item=item,
                permission=permission,
            )


def remove_permissions(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    item = MenuItem.objects.filter(portal="ADMIN", code=MENU_CODE).first()
    if item:
        MenuItemPermission.objects.filter(
            menu_item=item,
            permission__code__in=PERMISSION_CODES,
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0020_seed_questionnaire_print_release_menu"),
        ("rbac", "0034_seed_departmental_exam_audit_permission"),
    ]

    operations = [migrations.RunPython(add_permissions, remove_permissions)]
