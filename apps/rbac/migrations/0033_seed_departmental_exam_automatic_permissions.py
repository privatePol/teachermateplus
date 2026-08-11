from django.db import migrations


PERMISSIONS = [
    (
        "departmental_exams.view_generated_exams",
        "view_generated_exams",
        "View confidential current automatic-mode generated examinations.",
    ),
    (
        "departmental_exams.print_generated_exams",
        "print_generated_exams",
        "Print current automatic-mode generated questionnaires when output support is available.",
    ),
    (
        "departmental_exams.manage_exam_generation",
        "manage_exam_generation",
        "Manage automatic examination generation, regeneration, history, and contribution reopen.",
    ),
]


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
        ("navigation", "0018_seed_departmental_exam_stage5_menus"),
        ("rbac", "0032_seed_departmental_exam_permissions"),
    ]
    operations = [migrations.RunPython(seed_permissions, unseed_permissions)]
