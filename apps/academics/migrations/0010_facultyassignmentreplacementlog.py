from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("academics", "0009_course_syllabus_url"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FacultyAssignmentReplacementLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("batch_reference", models.CharField(db_index=True, max_length=40)),
                (
                    "replacement_type",
                    models.CharField(
                        choices=[
                            ("PERMANENT", "Permanent Replacement"),
                            ("TEMPORARY", "Temporary Substitute"),
                            ("SECONDARY", "Secondary / Co-Faculty"),
                            ("ADMINISTRATIVE", "Administrative Reassignment"),
                            ("WRONG_ASSIGNMENT", "Wrong Faculty Assignment"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "reason_category",
                    models.CharField(
                        choices=[
                            ("RESIGNATION", "Resignation"),
                            ("MEDICAL_LEAVE", "Medical Leave"),
                            ("MATERNITY_LEAVE", "Maternity Leave"),
                            ("SCHEDULE_CONFLICT", "Schedule Conflict"),
                            ("WRONG_ASSIGNMENT", "Wrong Assignment"),
                            ("ADMINISTRATIVE_REASSIGNMENT", "Administrative Reassignment"),
                            ("SUBSTITUTE_FACULTY", "Substitute Faculty"),
                            ("CO_FACULTY_ASSIGNMENT", "Co-Faculty Assignment"),
                            ("OTHER", "Other"),
                        ],
                        max_length=40,
                    ),
                ),
                ("remarks", models.TextField()),
                ("processed_at", models.DateTimeField()),
                ("old_assignment_before_json", models.JSONField(default=dict)),
                ("old_assignment_after_json", models.JSONField(default=dict)),
                ("new_assignment_before_json", models.JSONField(blank=True, null=True)),
                ("new_assignment_after_json", models.JSONField(default=dict)),
                ("impact_snapshot_json", models.JSONField(default=dict)),
                (
                    "campus",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="faculty_assignment_replacement_logs",
                        to="tenants.campus",
                    ),
                ),
                (
                    "new_assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="replacement_logs_as_new_assignment",
                        to="academics.facultyassignment",
                    ),
                ),
                (
                    "offering",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="faculty_assignment_replacement_logs",
                        to="academics.courseoffering",
                    ),
                ),
                (
                    "old_assignment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="replacement_logs_as_old_assignment",
                        to="academics.facultyassignment",
                    ),
                ),
                (
                    "processed_by_user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="processed_faculty_assignment_replacements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "replacement_faculty",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="replacement_faculty_replacement_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "source_faculty",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_faculty_replacement_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="faculty_assignment_replacement_logs",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "faculty_assignment_replacement_logs",
                "ordering": ["-processed_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="facultyassignmentreplacementlog",
            index=models.Index(fields=["tenant", "campus", "processed_at"], name="idx_fac_repl_scope_time"),
        ),
        migrations.AddIndex(
            model_name="facultyassignmentreplacementlog",
            index=models.Index(fields=["offering", "processed_at"], name="idx_fac_repl_offering_time"),
        ),
        migrations.AddIndex(
            model_name="facultyassignmentreplacementlog",
            index=models.Index(fields=["batch_reference"], name="idx_fac_repl_batch"),
        ),
    ]
