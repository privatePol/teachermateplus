from django.db import migrations


PERMISSIONS = (
    (
        "faculty_users.view_import",
        "faculty_users",
        "view_import",
        "View scoped Faculty user import batches and row details.",
    ),
    (
        "faculty_users.import",
        "faculty_users",
        "import",
        "Upload, preview, and confirm scoped Faculty user imports.",
    ),
    (
        "faculty_users.send_import_invitations",
        "faculty_users",
        "send_import_invitations",
        "Send Faculty invitations during a confirmed import.",
    ),
    (
        "faculty_users.resend_invitation",
        "faculty_users",
        "resend_invitation",
        "Send or resend invitations for imported Faculty users.",
    ),
)
ROLE_CODES = ("SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN")


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    for code, module, action, description in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": module,
                "action": action,
                "description": description,
                "is_active": True,
            },
        )
        for role in Role.objects.filter(code__in=ROLE_CODES, is_active=True):
            RolePermission.objects.get_or_create(role=role, permission=permission)


def unseed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permissions = Permission.objects.filter(code__in=[item[0] for item in PERMISSIONS])
    RolePermission.objects.filter(permission__in=permissions).delete()
    permissions.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0025_seed_faculty_feedback_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_permissions, unseed_permissions),
    ]
