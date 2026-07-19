from django.db import migrations


def seed_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")
    group = MenuGroup.objects.filter(portal="ADMIN", code="GRADING").first()
    permission = Permission.objects.filter(code="academic_interventions.monitor").first()
    if not group or not permission:
        return
    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN", code="ACADEMIC_INTERVENTION_TRACKING",
        defaults={"menu_group": group, "label": "Academic Intervention Tracking", "route_name": "admin_portal:academic_intervention_monitor", "sort_order": 77, "is_active": True},
    )
    MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)


class Migration(migrations.Migration):
    dependencies = [("navigation", "0014_seed_grade_submission_readiness_menu"), ("rbac", "0030_seed_academic_intervention_permissions")]
    operations = [migrations.RunPython(seed_menu, migrations.RunPython.noop)]
