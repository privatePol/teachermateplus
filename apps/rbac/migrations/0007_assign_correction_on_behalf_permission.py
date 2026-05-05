from django.db import migrations


def assign_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    on_behalf_permission = Permission.objects.filter(code="corrections.create_on_behalf").first()
    read_permission = Permission.objects.filter(code="corrections.read").first()
    if not on_behalf_permission:
        return
    for role in Role.objects.filter(code__in=["AC", "DEAN", "CAMPUS_ADMIN", "TENANT_ADMIN"]):
        RolePermission.objects.get_or_create(role_id=role.id, permission_id=on_behalf_permission.id)
        if read_permission:
            RolePermission.objects.get_or_create(role_id=role.id, permission_id=read_permission.id)


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0006_seed_correction_on_behalf_permission"),
    ]

    operations = [
        migrations.RunPython(assign_permission, migrations.RunPython.noop),
    ]
