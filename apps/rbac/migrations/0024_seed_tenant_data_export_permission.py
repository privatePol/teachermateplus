from django.db import migrations


PERMISSION = (
    "tenant_data_export.execute",
    "tenant_data_export",
    "execute",
    "Run secure tenant-scoped SQLite data exports after password and email OTP verification.",
)
ROLE_CODES = ["SUPER_ADMIN", "TENANT_ADMIN"]


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    code, module, action, description = PERMISSION
    permission, _ = Permission.objects.update_or_create(
        code=code,
        defaults={
            "module": module,
            "action": action,
            "description": description,
            "is_active": True,
        },
    )
    for role in Role.objects.filter(code__in=ROLE_CODES, is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=permission)


def unseed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permission = Permission.objects.filter(code=PERMISSION[0]).first()
    if permission:
        RolePermission.objects.filter(permission=permission).delete()
        permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0023_merge_20260623_1029"),
    ]

    operations = [
        migrations.RunPython(seed_permission, unseed_permission),
    ]
