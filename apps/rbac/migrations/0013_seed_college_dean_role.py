from django.db import migrations


COLLEGE_DEAN_PERMISSIONS = [
    "admin_portal.access",
    "dashboard.read",
    "courses.read",
    "sections.read",
    "offerings.read",
    "faculty_assignments.read",
    "faculty_final_clearance.read",
    "grading_analytics.read",
    "grade_distribution_monitor.read",
    "grading_templates.read",
    "template_components.read",
    "template_subcomponents.read",
    "template_details.read",
    "template_hotfixes.read",
    "course_template_assignments.read",
    "course_base_overrides.read",
    "grade_submissions.read",
    "corrections.read",
    "reopen_requests.read",
]


def seed_college_dean_role(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    role, _created = Role.objects.get_or_create(
        code="COLLEGE_DEAN",
        defaults={
            "name": "College Dean",
            "description": (
                "Scoped academic monitoring role for assigned campuses and departments. "
                "Additional permissions remain configurable by the Superadmin."
            ),
            "is_system": True,
            "is_active": True,
        },
    )
    for permission in Permission.objects.filter(code__in=COLLEGE_DEAN_PERMISSIONS, is_active=True):
        RolePermission.objects.get_or_create(role_id=role.id, permission_id=permission.id)


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0012_limit_reopen_review_to_campus_admin"),
    ]

    operations = [
        migrations.RunPython(seed_college_dean_role, migrations.RunPython.noop),
    ]
