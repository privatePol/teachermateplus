from django.db import migrations
from django.db.models.deletion import ProtectedError


PERMISSION_CODE = "academic_data_reconciliation.view"
ROLE_CODES = ["SUPER_ADMIN", "CAMPUS_ADMIN"]


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permission, _ = Permission.objects.update_or_create(
        code=PERMISSION_CODE,
        defaults={
            "module": "academic_data_reconciliation",
            "action": "view",
            "description": "View scoped academic data reconciliation reports.",
            "is_active": True,
        },
    )
    for role in Role.objects.filter(code__in=ROLE_CODES, is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=permission)


def unseed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")
    permission = Permission.objects.filter(code=PERMISSION_CODE).first()
    if not permission:
        return

    seeded_roles = Role.objects.filter(code__in=ROLE_CODES)
    RolePermission.objects.filter(permission=permission, role__in=seeded_roles).delete()
    if RolePermission.objects.filter(permission=permission).exists():
        return
    if UserPermission.objects.filter(permission=permission).exists():
        return
    try:
        permission.delete()
    except ProtectedError:
        # Preserve the permission when another app added a protected reference.
        return


class Migration(migrations.Migration):
    dependencies = [("rbac", "0030_seed_academic_intervention_permissions")]
    operations = [migrations.RunPython(seed_permission, unseed_permission)]
