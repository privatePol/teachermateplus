from django.db import migrations


PERMISSIONS = (
    ("faculty_attendance.view", "view", "View Faculty Attendance records within authorized scope."),
    (
        "faculty_attendance.manage_schedules",
        "manage_schedules",
        "Create corrected and effective-dated Faculty Attendance schedule interpretations.",
    ),
    (
        "faculty_attendance.manage_coverage",
        "manage_coverage",
        "Create effective-dated Faculty Attendance faculty coverage.",
    ),
    (
        "faculty_attendance.manage_meetings",
        "manage_meetings",
        "Generate stable Faculty Attendance meeting snapshots.",
    ),
    (
        "faculty_attendance.manage_substitutions",
        "manage_substitutions",
        "Assign meeting-specific Faculty Attendance substitutes.",
    ),
    (
        "faculty_attendance.reconcile",
        "reconcile",
        "Resolve Faculty Attendance schedule and coverage reconciliation items.",
    ),
)


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    for code, action, description in PERMISSIONS:
        Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": "faculty_attendance",
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
        ("faculty_attendance", "0001_initial"),
        ("rbac", "0036_seed_planning_readiness_permissions"),
    ]

    operations = [migrations.RunPython(seed_permissions, unseed_permissions)]
