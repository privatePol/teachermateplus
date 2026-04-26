from django.db import migrations


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.update_or_create(
        code="grade_distribution_monitor.read",
        defaults={
            "module": "grade_distribution_monitor",
            "action": "read",
            "description": "Read faculty grade distribution monitoring dashboard",
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("rbac", "0004_remove_userrole_uq_user_roles_scoped_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_permission, migrations.RunPython.noop),
    ]
