from django.db import migrations


def seed_student_enrollment_query_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permission, _ = Permission.objects.update_or_create(
        code="student_enrollment_query.read",
        defaults={
            "module": "student_enrollment_query",
            "action": "read",
            "description": "Search one student and view enrollment and grade details for a selected academic year and term.",
            "is_active": True,
        },
    )

    for role in Role.objects.filter(code__in=["SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR"]):
        RolePermission.objects.get_or_create(role_id=role.id, permission_id=permission.id)


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0010_seed_faculty_final_clearance_permission"),
    ]

    operations = [
        migrations.RunPython(seed_student_enrollment_query_permission, migrations.RunPython.noop),
    ]
