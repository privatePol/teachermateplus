from django.db import migrations


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    Role = apps.get_model("rbac", "Role")
    permission, _ = Permission.objects.get_or_create(
        code="users.login_access.update",
        defaults={"module": "users", "action": "login_access.update", "description": "Enable or disable login independently of activation, subject to target scope and lockout protections.", "is_active": True},
    )
    for role_id in Role.objects.filter(code__in=["SUPER_ADMIN", "CAMPUS_ADMIN"], is_active=True).values_list("pk", flat=True):
        RolePermission.objects.get_or_create(role_id=role_id, permission=permission)


class Migration(migrations.Migration):
    dependencies = [("rbac", "0036_seed_planning_readiness_permissions")]
    operations = [migrations.RunPython(seed_permission, migrations.RunPython.noop)]
