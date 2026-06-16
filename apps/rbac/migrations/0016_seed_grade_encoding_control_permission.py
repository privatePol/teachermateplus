from django.db import migrations


ROLE_CODES = ["SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR", "AC", "DEAN", "COLLEGE_DEAN", "CAO"]


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permission, _ = Permission.objects.update_or_create(
        code="grading_encoding_control.manage",
        defaults={
            "module": "grading_encoding_control",
            "action": "manage",
            "description": "Manage grade encoding access control",
            "is_active": True,
        },
    )
    for role in Role.objects.filter(code__in=ROLE_CODES, is_active=True):
        RolePermission.objects.get_or_create(role_id=role.id, permission_id=permission.id)


def unseed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permission = Permission.objects.filter(code="grading_encoding_control.manage").first()
    if permission:
        RolePermission.objects.filter(permission_id=permission.id).delete()
        permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0015_grant_cao_grading_analytics_read"),
    ]

    operations = [
        migrations.RunPython(seed_permission, unseed_permission),
    ]
