from django.db import migrations


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permission, _ = Permission.objects.update_or_create(
        code="students.import",
        defaults={
            "module": "students",
            "action": "import",
            "description": "Upload and confirm student master CSV imports.",
            "is_active": True,
        },
    )

    for role in Role.objects.filter(code__in=["SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR"]):
        RolePermission.objects.get_or_create(role_id=role.id, permission_id=permission.id)


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0008_seed_gradebook_student_identity_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permission, migrations.RunPython.noop),
    ]
