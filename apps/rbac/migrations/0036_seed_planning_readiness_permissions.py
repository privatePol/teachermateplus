from django.db import migrations


PERMISSIONS = (
    (
        "departmental_exams.view_planning_readiness",
        "view_planning_readiness",
        "View the read-only Departmental Exam Planning & Readiness report within exact authorized scope.",
    ),
    (
        "departmental_exams.print_planning_readiness",
        "print_planning_readiness",
        "Print the Departmental Exam Planning & Readiness report when view access is also authorized.",
    ),
)


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    for code, action, description in PERMISSIONS:
        Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": "departmental_exams",
                "action": action,
                "description": description,
                "is_active": True,
            },
        )


def unseed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    for code, _action, _description in PERMISSIONS:
        permission = Permission.objects.filter(code=code).first()
        if (
            permission
            and not RolePermission.objects.filter(permission=permission).exists()
            and not UserPermission.objects.filter(permission=permission).exists()
            and not MenuItemPermission.objects.filter(permission=permission).exists()
        ):
            permission.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0035_seed_answer_key_release_permission"),
    ]

    operations = [migrations.RunPython(seed_permissions, unseed_permissions)]
