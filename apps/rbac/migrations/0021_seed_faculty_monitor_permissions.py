from django.db import migrations


PERMISSIONS = [
    (
        "faculty_activity_monitor.read",
        "faculty_activity_monitor",
        "read",
        "Allows viewing faculty activity monitoring reports for supervised faculty.",
    ),
    (
        "faculty_gradebook_monitor.read",
        "faculty_gradebook_monitor",
        "read",
        "Allows viewing read-only faculty gradebook monitoring pages for supervised faculty.",
    ),
    (
        "grade_prediction_monitor.read",
        "grade_prediction_monitor",
        "read",
        "Allows viewing admin grade prediction monitoring pages for supervised faculty.",
    ),
]

ACADEMIC_MONITOR_ROLE_CODES = ["AC", "AREA_CHAIR", "DEAN", "COLLEGE_DEAN", "CAO", "SUPER_ADMIN"]
NON_MONITORING_ADMIN_ROLE_CODES = ["CAMPUS_ADMIN", "TENANT_ADMIN", "REGISTRAR"]


def seed_faculty_monitor_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    permission_map = {}
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
        permission_map[code] = permission

    final_clearance_permission = Permission.objects.filter(code="faculty_final_clearance.read").first()
    monitoring_permissions = list(permission_map.values())
    if final_clearance_permission:
        monitoring_permissions.append(final_clearance_permission)

    for role in Role.objects.filter(code__in=ACADEMIC_MONITOR_ROLE_CODES, is_active=True):
        for permission in monitoring_permissions:
            RolePermission.objects.get_or_create(role=role, permission=permission)

    for role in Role.objects.filter(code__in=NON_MONITORING_ADMIN_ROLE_CODES, is_active=True):
        for permission in monitoring_permissions:
            RolePermission.objects.filter(role=role, permission=permission).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0020_seed_class_list_change_request_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_faculty_monitor_permissions, migrations.RunPython.noop),
    ]
