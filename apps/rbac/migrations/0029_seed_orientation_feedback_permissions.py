from django.db import migrations


PERMISSIONS = (
    ("orientation_feedback.view", "view", "View scoped orientation feedback survey sessions."),
    ("orientation_feedback.manage", "manage", "Create and edit draft orientation feedback surveys."),
    ("orientation_feedback.start", "start", "Start orientation feedback survey sessions."),
    ("orientation_feedback.close", "close", "End open orientation feedback survey sessions."),
    ("orientation_feedback.cancel", "cancel", "Cancel draft or open orientation feedback surveys."),
    ("orientation_feedback.view_analytics", "view_analytics", "View aggregate orientation feedback analytics."),
    ("orientation_feedback.export", "export", "Export aggregate orientation feedback results."),
)


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    super_admin_roles = Role.objects.filter(code="SUPER_ADMIN", is_active=True)
    for code, action, description in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": "orientation_feedback",
                "action": action,
                "description": description,
                "is_active": True,
            },
        )
        for role in super_admin_roles:
            RolePermission.objects.get_or_create(role=role, permission=permission)


def unseed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permissions = Permission.objects.filter(code__in=[row[0] for row in PERMISSIONS])
    RolePermission.objects.filter(permission__in=permissions).delete()
    permissions.delete()


class Migration(migrations.Migration):
    dependencies = [("rbac", "0028_seed_exit_pulse_identity_investigation_permission")]
    operations = [migrations.RunPython(seed_permissions, unseed_permissions)]
