from django.db import migrations


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    on_behalf_permission, _ = Permission.objects.update_or_create(
        code="corrections.create_on_behalf",
        defaults={
            "module": "corrections",
            "action": "create_on_behalf",
            "description": "Create grade correction petitions on behalf of the original faculty member.",
            "is_active": True,
        },
    )
    read_permission = Permission.objects.filter(code="corrections.read").first()
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    for role in Role.objects.filter(code__in=["AC", "DEAN", "CAMPUS_ADMIN", "TENANT_ADMIN"]):
        RolePermission.objects.get_or_create(role_id=role.id, permission_id=on_behalf_permission.id)
        if read_permission:
            RolePermission.objects.get_or_create(role_id=role.id, permission_id=read_permission.id)


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0005_seed_grade_distribution_monitor_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permission, migrations.RunPython.noop),
    ]
