from django.db import migrations


PERMISSIONS = (
    (
        "faculty_feedback.read",
        "faculty_feedback",
        "read",
        "Read scoped Faculty Portal feedback submissions.",
        ("SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN"),
    ),
    (
        "faculty_feedback.export",
        "faculty_feedback",
        "export",
        "Export scoped Faculty Portal feedback submissions to CSV.",
        ("SUPER_ADMIN", "TENANT_ADMIN"),
    ),
)


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    for code, module, action, description, role_codes in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": module,
                "action": action,
                "description": description,
                "is_active": True,
            },
        )
        for role in Role.objects.filter(code__in=role_codes, is_active=True):
            RolePermission.objects.get_or_create(role=role, permission=permission)


def unseed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permissions = Permission.objects.filter(code__in=[permission[0] for permission in PERMISSIONS])
    RolePermission.objects.filter(permission__in=permissions).delete()
    permissions.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0024_seed_tenant_data_export_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permissions, unseed_permissions),
    ]
