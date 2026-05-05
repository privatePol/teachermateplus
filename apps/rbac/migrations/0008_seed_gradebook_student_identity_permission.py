from django.db import migrations
from django.db.models import Q


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permission, _ = Permission.objects.update_or_create(
        code="gradebook.view_student_identity",
        defaults={
            "module": "gradebook",
            "action": "view_student_identity",
            "description": "View unmasked student numbers and names in authorized gradebook review pages.",
            "is_active": True,
        },
    )

    reviewer_roles = Role.objects.filter(
        Q(code__in=["AC", "DEAN", "CAO", "SUPER_ADMIN"])
        | Q(code__endswith="_AC")
        | Q(code__endswith="_DEAN")
        | Q(code__endswith="_CAO")
    )
    for role in reviewer_roles:
        RolePermission.objects.get_or_create(role_id=role.id, permission_id=permission.id)


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0007_assign_correction_on_behalf_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permission, migrations.RunPython.noop),
    ]
