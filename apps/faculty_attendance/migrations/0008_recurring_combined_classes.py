from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0011_course_exam_department"),
        ("faculty_attendance", "0007_monthly_checklist_arrangements"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RecurringCombinedClass",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("weekday", models.PositiveSmallIntegerField(choices=[(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")])), ("start_time", models.TimeField()), ("end_time", models.TimeField()),
                ("effective_from", models.DateField()), ("effective_until", models.DateField(blank=True, null=True)), ("reason", models.TextField()),
                ("academic_year", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_combined_classes", to="academics.academicyear")),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_combined_classes", to="tenants.campus")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_attendance_combined_classes", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_combined_classes", to="tenants.tenant")),
                ("term", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_combined_classes", to="academics.term")),
            ],
            options={"db_table": "faculty_attendance_recurring_combined_classes", "ordering": ["academic_year_id", "term_id", "weekday", "start_time", "effective_from", "id"]},
        ),
        migrations.CreateModel(
            name="RecurringCombinedClassOffering",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_primary", models.BooleanField(default=False)),
                ("combined_class", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="offering_links", to="faculty_attendance.recurringcombinedclass")),
                ("offering", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_combined_class_links", to="academics.courseoffering")),
            ], options={"db_table": "faculty_attendance_recurring_combined_offerings"},
        ),
        migrations.AddConstraint(model_name="recurringcombinedclass", constraint=models.CheckConstraint(condition=models.Q(("weekday__gte", 0), ("weekday__lte", 6)), name="ck_att_combined_weekday")),
        migrations.AddConstraint(model_name="recurringcombinedclass", constraint=models.CheckConstraint(condition=models.Q(("end_time__gt", models.F("start_time"))), name="ck_att_combined_time")),
        migrations.AddConstraint(model_name="recurringcombinedclass", constraint=models.CheckConstraint(condition=models.Q(("effective_until__isnull", True), ("effective_until__gte", models.F("effective_from")), _connector="OR"), name="ck_att_combined_dates")),
        migrations.AddIndex(model_name="recurringcombinedclass", index=models.Index(fields=["tenant", "campus", "academic_year", "term", "weekday"], name="idx_att_combined_scope")),
        migrations.AddConstraint(model_name="recurringcombinedclassoffering", constraint=models.UniqueConstraint(fields=("combined_class", "offering"), name="uq_att_combined_offering")),
        migrations.AddConstraint(model_name="recurringcombinedclassoffering", constraint=models.UniqueConstraint(condition=models.Q(("is_primary", True)), fields=("combined_class",), name="uq_att_combined_primary")),
    ]
