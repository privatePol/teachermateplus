from django.db import migrations


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.update_or_create(
        code="student_account_links.manage",
        defaults={
            "module": "student_account_links",
            "action": "manage",
            "description": "Allows admins to create and deactivate Student Portal account links.",
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("student_portal", "0002_seed_student_portal_permission"),
        ("rbac", "0008_seed_gradebook_student_identity_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permission, migrations.RunPython.noop),
    ]
