from django.db import migrations


PERMISSIONS = [
    (
        "departmental_exams.manage_cycles",
        "manage_cycles",
        "Manage examination cycles.",
    ),
    (
        "departmental_exams.configure",
        "configure",
        "Configure authorized grouped course examinations.",
    ),
    (
        "departmental_exams.review_generate",
        "review_generate",
        "Review assigned grouped course examinations.",
    ),
]


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    for code, action, description in PERMISSIONS:
        Permission.objects.update_or_create(code=code, defaults={"module": "departmental_exams", "action": action, "description": description, "is_active": True})


def unseed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    for code, _action, _description in PERMISSIONS:
        permission = Permission.objects.filter(code=code).first()
        if (
            permission
            and not RolePermission.objects.filter(permission=permission).exists()
            and not UserPermission.objects.filter(permission=permission).exists()
            and not MenuItemPermission.objects.filter(permission=permission).exists()
        ):
            permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0016_seed_academic_data_reconciliation_menu"),
        ("rbac", "0031_seed_academic_data_reconciliation_permission"),
    ]
    operations = [migrations.RunPython(seed_permissions, unseed_permissions)]
