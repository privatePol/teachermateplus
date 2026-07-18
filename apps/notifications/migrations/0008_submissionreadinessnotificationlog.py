import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0010_facultyassignmentreplacementlog"),
        ("grading", "0033_alter_coursetemplateassignment_grading_template"),
        ("notifications", "0007_facultyreminder_app_level_activity_dedupe"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubmissionReadinessNotificationLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("recipient_email", models.EmailField(blank=True, max_length=254)),
                ("recipient_roles_json", models.JSONField(default=list)),
                ("scope_context_json", models.JSONField(default=dict)),
                ("deadline_at", models.DateTimeField()),
                ("threshold", models.DecimalField(decimal_places=2, max_digits=5)),
                ("days_before", models.PositiveSmallIntegerField()),
                ("policy_version", models.CharField(default="v1", max_length=32)),
                ("generated_at", models.DateTimeField()),
                ("faculty_count", models.PositiveIntegerField(default=0)),
                ("assignment_count", models.PositiveIntegerField(default=0)),
                ("status", models.CharField(choices=[("DRY_RUN", "Dry run"), ("SENT", "Sent"), ("FAILED", "Failed"), ("SKIPPED", "Skipped")], max_length=12)),
                ("failure_reason", models.CharField(blank=True, max_length=255)),
                ("idempotency_key", models.CharField(max_length=64, unique=True)),
                ("metadata_json", models.JSONField(blank=True, default=dict)),
                ("academic_year", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="academics.academicyear")),
                ("campus", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="readiness_notification_logs", to="tenants.campus")),
                ("recipient", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="submission_readiness_notification_logs", to=settings.AUTH_USER_MODEL)),
                ("template_period", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="grading.gradingtemplateperiod")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="readiness_notification_logs", to="tenants.tenant")),
                ("term", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="academics.term")),
            ],
            options={"db_table": "submission_readiness_notification_logs", "ordering": ["-generated_at", "-id"]},
        ),
        migrations.AddIndex(model_name="submissionreadinessnotificationlog", index=models.Index(fields=["tenant", "recipient", "generated_at"], name="idx_srnotif_recipient")),
        migrations.AddIndex(model_name="submissionreadinessnotificationlog", index=models.Index(fields=["status", "generated_at"], name="idx_srnotif_status")),
    ]
