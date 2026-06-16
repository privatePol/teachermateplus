from django.db import migrations


def grant_permission(apps, schema_editor):
    Role = apps.get_model("rbac", "Role")
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")

    role = Role.objects.filter(code="CAO").first()
    permission = Permission.objects.filter(code="grading_analytics.read", is_active=True).first()
    if role and permission:
        RolePermission.objects.get_or_create(role=role, permission=permission)


def remove_permission(apps, schema_editor):
    RolePermission = apps.get_model("rbac", "RolePermission")
    RolePermission.objects.filter(
        role__code="CAO",
        permission__code="grading_analytics.read",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0014_college_dean_read_only_baseline"),
    ]

    operations = [
        migrations.RunPython(grant_permission, remove_permission),
    ]
