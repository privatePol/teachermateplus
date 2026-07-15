from django.db import migrations


PERMISSION_CODE = "exit_pulse.response_identity_investigate"


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.update_or_create(
        code=PERMISSION_CODE,
        defaults={
            "module": "exit_pulse",
            "action": "response_identity_investigate",
            "description": (
                "Allows a separately authorized, scoped investigation workflow to review "
                "one Exit Pulse response identity with a recorded reason and audit event."
            ),
            "is_active": True,
        },
    )


def unseed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")
    permission = Permission.objects.filter(code=PERMISSION_CODE).first()
    if permission:
        RolePermission.objects.filter(permission=permission).delete()
        UserPermission.objects.filter(permission=permission).delete()
        permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0027_seed_exit_pulse_permission"),
    ]

    operations = [migrations.RunPython(seed_permission, unseed_permission)]
