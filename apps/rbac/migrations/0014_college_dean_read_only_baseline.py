from django.db import migrations


COLLEGE_DEAN_ACTION_PERMISSIONS = [
    "faculty_assignments.create",
    "faculty_assignments.update",
    "grading_templates.approve",
    "template_hotfixes.review",
    "grade_submissions.revert_before_deadline",
    "corrections.create_on_behalf",
    "corrections.review",
    "reopen_requests.review",
]


def set_college_dean_read_only_baseline(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Role = apps.get_model("rbac", "Role")
    RolePermission = apps.get_model("rbac", "RolePermission")

    role = Role.objects.filter(code="COLLEGE_DEAN").first()
    if role is None:
        return
    permission_ids = Permission.objects.filter(
        code__in=COLLEGE_DEAN_ACTION_PERMISSIONS
    ).values_list("id", flat=True)
    RolePermission.objects.filter(
        role_id=role.id,
        permission_id__in=permission_ids,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0013_seed_college_dean_role"),
    ]

    operations = [
        migrations.RunPython(
            set_college_dean_read_only_baseline,
            migrations.RunPython.noop,
        ),
    ]
