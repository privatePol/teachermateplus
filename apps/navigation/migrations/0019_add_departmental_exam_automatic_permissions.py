from django.db import migrations


PERMISSION_CODES = (
    "departmental_exams.view_generated_exams",
    "departmental_exams.manage_exam_generation",
)


def add_permissions(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")
    item = MenuItem.objects.filter(
        portal="ADMIN", code="DE_EXAM_ASSIGNED_COURSES"
    ).first()
    if not item:
        return
    for code in PERMISSION_CODES:
        permission = Permission.objects.filter(code=code, is_active=True).first()
        if permission:
            MenuItemPermission.objects.get_or_create(
                menu_item=item, permission=permission
            )


def remove_permissions(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    item = MenuItem.objects.filter(
        portal="ADMIN", code="DE_EXAM_ASSIGNED_COURSES"
    ).first()
    if item:
        MenuItemPermission.objects.filter(
            menu_item=item,
            permission__code__in=PERMISSION_CODES,
        ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0018_seed_departmental_exam_stage5_menus"),
        ("rbac", "0033_seed_departmental_exam_automatic_permissions"),
    ]
    operations = [migrations.RunPython(add_permissions, remove_permissions)]
