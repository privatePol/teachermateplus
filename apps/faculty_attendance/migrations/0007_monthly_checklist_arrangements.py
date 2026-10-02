from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0011_course_exam_department"),
        ("faculty_attendance", "0006_seed_ui_permissions_and_navigation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MonthlyChecklistArrangement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("day_group", models.CharField(max_length=12)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("academic_year", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_monthly_arrangements", to="academics.academicyear")),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_monthly_arrangements", to="tenants.campus")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_monthly_arrangements", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_monthly_arrangements", to="tenants.tenant")),
                ("term", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_monthly_arrangements", to="academics.term")),
            ],
            options={"db_table": "faculty_attendance_monthly_arrangements"},
        ),
        migrations.CreateModel(
            name="MonthlyChecklistArrangementEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("pattern_key", models.CharField(max_length=64)),
                ("time_group_key", models.CharField(max_length=32)),
                ("position", models.PositiveIntegerField()),
                ("arrangement", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="entries", to="faculty_attendance.monthlychecklistarrangement")),
                ("offering", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_monthly_arrangement_entries", to="academics.courseoffering")),
            ],
            options={"db_table": "faculty_attendance_monthly_arrangement_entries", "ordering": ["time_group_key", "position", "id"]},
        ),
        migrations.AddConstraint(model_name="monthlychecklistarrangement", constraint=models.UniqueConstraint(fields=("tenant", "campus", "academic_year", "term", "owner", "day_group"), name="uq_att_monthly_arr_scope")),
        migrations.AddIndex(model_name="monthlychecklistarrangement", index=models.Index(fields=["tenant", "campus", "academic_year", "term", "day_group"], name="idx_att_monthly_scope")),
        migrations.AddConstraint(model_name="monthlychecklistarrangemententry", constraint=models.UniqueConstraint(fields=("arrangement", "offering", "pattern_key"), name="uq_att_monthly_arr_entry")),
        migrations.AddConstraint(model_name="monthlychecklistarrangemententry", constraint=models.UniqueConstraint(fields=("arrangement", "time_group_key", "position"), name="uq_att_monthly_arr_pos")),
    ]
