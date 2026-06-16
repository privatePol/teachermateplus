from django.db import migrations


ROLE_CODES = ["SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR", "AC", "AREA_CHAIR", "DEAN", "COLLEGE_DEAN", "CAO"]


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permissions = [
        ("enrollment_adjustment.view", "view", "View enrollment adjustment tool and history"),
        ("enrollment_adjustment.process", "process", "Process post-enrollment academic adjustments"),
    ]
    created_permissions = []
    for code, action, description in permissions:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": "enrollment_adjustment",
                "action": action,
                "description": description,
                "is_active": True,
            },
        )
        created_permissions.append(permission)

    for role in Role.objects.filter(code__in=ROLE_CODES, is_active=True):
        for permission in created_permissions:
            RolePermission.objects.get_or_create(role_id=role.id, permission_id=permission.id)


def unseed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permissions = Permission.objects.filter(
        code__in=["enrollment_adjustment.view", "enrollment_adjustment.process"]
    )
    RolePermission.objects.filter(permission__in=permissions).delete()
    permissions.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0016_seed_grade_encoding_control_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permissions, unseed_permissions),
    ]
