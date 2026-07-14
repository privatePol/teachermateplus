from django.db import migrations


PERMISSION_CODE = "exit_pulse.use"
ROLE_CODES = ("FACULTY", "SUPER_ADMIN")


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permission, _ = Permission.objects.update_or_create(
        code=PERMISSION_CODE,
        defaults={
            "module": "exit_pulse",
            "action": "use",
            "description": "Create and manage Exit Pulse sessions for accepted faculty assignments.",
            "is_active": True,
        },
    )
    for role in Role.objects.filter(code__in=ROLE_CODES, is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=permission)


def unseed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permission = Permission.objects.filter(code=PERMISSION_CODE).first()
    if permission:
        RolePermission.objects.filter(permission=permission).delete()
        permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0026_seed_faculty_user_import_permissions"),
    ]

    operations = [migrations.RunPython(seed_permission, unseed_permission)]
