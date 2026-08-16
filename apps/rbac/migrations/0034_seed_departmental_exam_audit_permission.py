from django.db import migrations


PERMISSION_CODE = "departmental_exams.audit_generated_exams"


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.update_or_create(
        code=PERMISSION_CODE,
        defaults={
            "module": "departmental_exams",
            "action": "audit_generated_exams",
            "description": (
                "Run and view deterministic integrity audits for automatic-mode "
                "generated examinations."
            ),
            "is_active": True,
        },
    )


def unseed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    permission = Permission.objects.filter(code=PERMISSION_CODE).first()
    if (
        permission
        and not RolePermission.objects.filter(permission=permission).exists()
        and not UserPermission.objects.filter(permission=permission).exists()
        and not MenuItemPermission.objects.filter(permission=permission).exists()
    ):
        permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0033_seed_departmental_exam_automatic_permissions"),
    ]

    operations = [migrations.RunPython(seed_permission, unseed_permission)]
