from django.db import migrations


PERMISSION_CODE = "departmental_exams.release_answer_keys"


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.update_or_create(
        code=PERMISSION_CODE,
        defaults={
            "module": "departmental_exams",
            "action": "release_answer_keys",
            "description": (
                "Release exact-revision confidential Answer Keys to currently "
                "assigned faculty within a bounded window."
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
        ("rbac", "0034_seed_departmental_exam_audit_permission"),
    ]

    operations = [migrations.RunPython(seed_permission, unseed_permission)]
