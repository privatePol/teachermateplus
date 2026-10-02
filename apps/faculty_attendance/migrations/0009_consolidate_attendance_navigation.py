from django.db import migrations


def consolidate_navigation(apps, schema_editor):
    MenuItem = apps.get_model("navigation", "MenuItem")
    MenuItemPermission = apps.get_model("navigation", "MenuItemPermission")
    Permission = apps.get_model("rbac", "Permission")

    setup_item = MenuItem.objects.filter(
        portal="ADMIN",
        code="ATTENDANCE_SETUP",
        route_name="faculty_attendance:setup",
    ).first()
    if setup_item and setup_item.is_active:
        setup_item.is_active = False
        setup_item.save(update_fields=["is_active", "updated_at"])

    checklist_item = MenuItem.objects.filter(
        portal="ADMIN",
        code="ATTENDANCE_CHECKLIST",
        route_name="faculty_attendance:checklist",
    ).first()
    if checklist_item:
        update_fields = []
        if checklist_item.label == "Classroom Checklists":
            checklist_item.label = "Monthly Attendance Checklist"
            update_fields.append("label")
        if checklist_item.sort_order == 20:
            checklist_item.sort_order = 10
            update_fields.append("sort_order")
        if not checklist_item.is_active:
            checklist_item.is_active = True
            update_fields.append("is_active")
        if update_fields:
            checklist_item.save(update_fields=[*update_fields, "updated_at"])
        view_permission = Permission.objects.filter(code="faculty_attendance.view").first()
        if view_permission:
            MenuItemPermission.objects.get_or_create(
                menu_item=checklist_item,
                permission=view_permission,
            )

    review_item = MenuItem.objects.filter(
        portal="ADMIN",
        code="ATTENDANCE_RECONCILIATION",
        route_name="faculty_attendance:reconciliation",
        label="Attendance Reconciliation",
    ).first()
    if review_item:
        review_item.label = "Changes Needing Review"
        review_item.save(update_fields=["label", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("faculty_attendance", "0008_recurring_combined_classes"),
    ]

    operations = [
        migrations.RunPython(consolidate_navigation, migrations.RunPython.noop),
    ]
