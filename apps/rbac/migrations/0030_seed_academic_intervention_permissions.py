from django.db import migrations


PERMISSIONS = [
    ("academic_interventions.manage_own", "academic_interventions", "manage_own", "Manage faculty-owned academic intervention records."),
    ("academic_interventions.monitor", "academic_interventions", "monitor", "Read scoped academic intervention monitoring records."),
    ("academic_interventions.configure", "academic_interventions", "configure", "Configure Student Academic Intervention Tracking."),
    ("academic_interventions.view_disabled_archive", "academic_interventions", "view_disabled_archive", "View intervention archive while the feature is disabled."),
]


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")
    permissions = {}
    for code, module, action, description in PERMISSIONS:
        permissions[code], _ = Permission.objects.update_or_create(code=code, defaults={"module": module, "action": action, "description": description, "is_active": True})
    for role in Role.objects.filter(code="FACULTY", is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=permissions["academic_interventions.manage_own"])
    for role in Role.objects.filter(code__in=["AC", "AREA_CHAIR", "AREA_CHAIRPERSON", "DEAN", "COLLEGE_DEAN", "CAO", "SUPER_ADMIN"], is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=permissions["academic_interventions.monitor"])
    for role in Role.objects.filter(code="SUPER_ADMIN", is_active=True):
        RolePermission.objects.get_or_create(role=role, permission=permissions["academic_interventions.configure"])


class Migration(migrations.Migration):
    dependencies = [("rbac", "0029_seed_orientation_feedback_permissions")]
    operations = [migrations.RunPython(seed_permissions, migrations.RunPython.noop)]
