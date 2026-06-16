from django.db import migrations


PROCESS_ROLE_CODES = ["SUPER_ADMIN", "TENANT_ADMIN", "CAMPUS_ADMIN", "REGISTRAR"]
VIEW_ONLY_ROLE_CODES = ["AC", "AREA_CHAIR", "DEAN", "COLLEGE_DEAN", "CAO"]


def narrow_process_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    process_permission = Permission.objects.filter(code="enrollment_adjustment.process").first()
    if not process_permission:
        return
    view_only_roles = Role.objects.filter(code__in=VIEW_ONLY_ROLE_CODES)
    RolePermission.objects.filter(role__in=view_only_roles, permission=process_permission).delete()
    for role in Role.objects.filter(code__in=PROCESS_ROLE_CODES, is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=process_permission)


def restore_previous_process_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    process_permission = Permission.objects.filter(code="enrollment_adjustment.process").first()
    if not process_permission:
        return
    for role in Role.objects.filter(code__in=PROCESS_ROLE_CODES + VIEW_ONLY_ROLE_CODES, is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=process_permission)


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0017_seed_enrollment_adjustment_permissions"),
    ]

    operations = [
        migrations.RunPython(narrow_process_permission, restore_previous_process_permission),
    ]
