from django.db import migrations


ROLE_CODES = ["SUPER_ADMIN", "CAMPUS_ADMIN"]


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permissions = [
        ("class_list_change_requests.view", "class_list_change_requests", "view", "View class list change requests"),
        (
            "class_list_change_requests.review",
            "class_list_change_requests",
            "review",
            "Review class list change requests",
        ),
    ]
    created_permissions = []
    for code, module, action, description in permissions:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": module,
                "action": action,
                "description": description,
                "is_active": True,
            },
        )
        created_permissions.append(permission)

    for role in Role.objects.filter(code__in=ROLE_CODES, is_active=True):
        for permission in created_permissions:
            RolePermission.objects.get_or_create(role_id=role.id, permission_id=permission.id)


def unseed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permissions = Permission.objects.filter(
        code__in=["class_list_change_requests.view", "class_list_change_requests.review"]
    )
    RolePermission.objects.filter(permission__in=permissions).delete()
    permissions.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0019_seed_faculty_replacement_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_permissions, unseed_permissions),
    ]
