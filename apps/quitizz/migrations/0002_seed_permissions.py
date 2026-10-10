from django.db import migrations


def seed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    for action, description in (
        ("manage", "Create and manage own QuiTizzes in the assigned campus"),
        ("host", "Launch and view own QuiTizz session foundations in the assigned campus"),
        ("view_history", "Reserved for future QuiTizz history/results access"),
    ):
        Permission.objects.using(schema_editor.connection.alias).update_or_create(
            code=f"quitizz.{action}", defaults={"module": "quitizz", "action": action, "description": description, "is_active": True},
        )


def unseed(apps, schema_editor):
    Permission = apps.get_model("rbac", "Permission")
    Permission.objects.using(schema_editor.connection.alias).filter(
        code__in=["quitizz.manage", "quitizz.host", "quitizz.view_history"],
    ).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("quitizz", "0001_initial"), ("rbac", "0036_seed_planning_readiness_permissions")]
    # Reverse disables these permissions while preserving configured assignments.
    # No roles or users receive automatic grants.
    operations = [migrations.RunPython(seed, unseed)]
