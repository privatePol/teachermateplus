from django.db import migrations


def seed_student_enrollment_query_menu(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    group = MenuGroup.objects.filter(portal="ADMIN", code="STUDENTS").first()
    permission = Permission.objects.filter(code="student_enrollment_query.read", is_active=True).first()
    if not group or not permission:
        return

    item, _ = MenuItem.objects.update_or_create(
        portal="ADMIN",
        code="STUDENT_ENROLLMENT_QUERY",
        defaults={
            "menu_group_id": group.id,
            "label": "Student Enrollment Query",
            "route_name": "admin_portal:student_enrollment_query",
            "sort_order": 20,
            "is_active": True,
        },
    )
    MenuItemPermission.objects.get_or_create(menu_item_id=item.id, permission_id=permission.id)


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0003_seed_correction_on_behalf_menu"),
        ("rbac", "0011_seed_student_enrollment_query_permission"),
    ]

    operations = [
        migrations.RunPython(seed_student_enrollment_query_menu, migrations.RunPython.noop),
    ]
