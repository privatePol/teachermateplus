from django.db import migrations


PERMISSIONS = [
    ("faculty_replacement.view", "faculty_replacement", "view"),
    ("faculty_replacement.process", "faculty_replacement", "process"),
]
PROCESS_ROLE_CODES = ["SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR"]
VIEW_ONLY_ROLE_CODES = ["AC", "AREA_CHAIR", "DEAN", "COLLEGE_DEAN", "CAO"]


def seed_faculty_replacement_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permission_map = {}
    for code, module, action in PERMISSIONS:
        permission, _ = Permission.objects.get_or_create(
            code=code,
            defaults={
                "module": module,
                "action": action,
                "description": f"{action.title()} {module.replace('_', ' ')}",
                "is_active": True,
            },
        )
        permission_map[code] = permission

    for role in Role.objects.filter(code__in=PROCESS_ROLE_CODES, is_active=True):
        for permission in permission_map.values():
            RolePermission.objects.get_or_create(role=role, permission=permission)

    view_permission = permission_map["faculty_replacement.view"]
    process_permission = permission_map["faculty_replacement.process"]
    for role in Role.objects.filter(code__in=VIEW_ONLY_ROLE_CODES, is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=view_permission)
        RolePermission.objects.filter(role=role, permission=process_permission).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0018_narrow_enrollment_adjustment_process_roles"),
    ]

    operations = [
        migrations.RunPython(seed_faculty_replacement_permissions, migrations.RunPython.noop),
    ]
