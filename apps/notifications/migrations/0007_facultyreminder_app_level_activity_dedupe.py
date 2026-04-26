from django.db import migrations, models


def dedupe_activity_reminders(apps, schema_editor):
    FacultyReminder = apps.get_model("notifications", "FacultyReminder")
    duplicate_activity_ids = (
        FacultyReminder.objects.exclude(grade_activity__isnull=True)
        .values("grade_activity_id")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
        .values_list("grade_activity_id", flat=True)
    )
    for activity_id in duplicate_activity_ids:
        reminders = list(FacultyReminder.objects.filter(grade_activity_id=activity_id).order_by("id"))
        keeper = reminders[0]
        FacultyReminder.objects.filter(id__in=[reminder.id for reminder in reminders[1:]]).update(
            grade_activity=None,
            is_active=False,
            notes="Duplicate activity reminder cancelled during migration cleanup.",
        )
        if not keeper.is_active:
            keeper.is_active = True
            keeper.cancelled_at = None
            keeper.save(update_fields=["is_active", "cancelled_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0006_submissionnoncompliancenotice"),
    ]

    operations = [
        migrations.RunPython(dedupe_activity_reminders, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="facultyreminder",
            name="uq_faculty_reminder_grade_activity",
        ),
    ]
