from django.db import migrations


def move_student_enrollment_query_to_enrollment(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")

    enrollment_group = MenuGroup.objects.filter(portal="ADMIN", code="ENROLLMENT").first()
    if not enrollment_group:
        return

    MenuItem.objects.filter(portal="ADMIN", code="STUDENT_ENROLLMENT_QUERY").update(
        menu_group_id=enrollment_group.id,
        sort_order=20,
        is_active=True,
    )


def move_student_enrollment_query_to_students(apps, schema_editor):
    MenuGroup = apps.get_model("navigation", "MenuGroup")
    MenuItem = apps.get_model("navigation", "MenuItem")

    students_group = MenuGroup.objects.filter(portal="ADMIN", code="STUDENTS").first()
    if not students_group:
        return

    MenuItem.objects.filter(portal="ADMIN", code="STUDENT_ENROLLMENT_QUERY").update(
        menu_group_id=students_group.id,
        sort_order=20,
        is_active=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("navigation", "0004_seed_student_enrollment_query_menu"),
    ]

    operations = [
        migrations.RunPython(
            move_student_enrollment_query_to_enrollment,
            move_student_enrollment_query_to_students,
        ),
    ]
