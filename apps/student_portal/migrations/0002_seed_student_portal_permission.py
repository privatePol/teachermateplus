from django.db import migrations


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    permissions = [
        (
            "student_portal.access",
            "student_portal",
            "access",
            "Allows a linked student user to access the Student Portal.",
        ),
        (
            "student_account_links.manage",
            "student_account_links",
            "manage",
            "Allows admins to create and deactivate Student Portal account links.",
        ),
    ]
    for code, module, action, description in permissions:
        Permission.objects.update_or_create(
            code=code,
            defaults={
                "module": module,
                "action": action,
                "description": description,
                "is_active": True,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("student_portal", "0001_initial"),
        ("rbac", "0008_seed_gradebook_student_identity_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permission, migrations.RunPython.noop),
    ]
