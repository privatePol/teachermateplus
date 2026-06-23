from django.db import migrations


def seed_user_role_assignment_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")

    permission, _ = Permission.objects.update_or_create(
        code="user_roles.update",
        defaults={
            "module": "user_roles",
            "action": "update",
            "description": "Allows assigning and deactivating scoped user role records.",
            "is_active": True,
        },
    )

    role_update_permission = Permission.objects.filter(code="roles.update").first()
    if not role_update_permission:
        return

    role_ids = RolePermission.objects.filter(permission=role_update_permission).values_list("role_id", flat=True).distinct()
    for role_id in role_ids:
        RolePermission.objects.get_or_create(role_id=role_id, permission_id=permission.id)

    user_permission_rows = UserPermission.objects.filter(permission=role_update_permission)
    for row in user_permission_rows:
        UserPermission.objects.get_or_create(
            user_id=row.user_id,
            permission_id=permission.id,
            grant_type=row.grant_type,
            tenant_id=row.tenant_id,
            campus_id=row.campus_id,
        )


def unseed_user_role_assignment_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    UserPermission = apps.get_model("rbac", "UserPermission")

    permission = Permission.objects.filter(code="user_roles.update").first()
    if not permission:
        return

    RolePermission.objects.filter(permission=permission).delete()
    UserPermission.objects.filter(permission=permission).delete()
    permission.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0020_seed_class_list_change_request_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_user_role_assignment_permission, unseed_user_role_assignment_permission),
    ]
