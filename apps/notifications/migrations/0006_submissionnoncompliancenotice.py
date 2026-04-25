from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0005_facultyassignment_reminder_fields"),
        ("grading", "0020_gradingtemplatecomponent_is_exam_component"),
        ("notifications", "0005_facultyreminder_grade_activity_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubmissionNonComplianceNotice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("notice_level", models.CharField(choices=[("NOTICE", "Notice"), ("WARNING", "Warning"), ("ESCALATION", "Escalation")], max_length=20)),
                ("sequence_no", models.PositiveIntegerField(default=1)),
                ("title", models.CharField(max_length=180)),
                ("message", models.TextField()),
                ("deadline_at", models.DateTimeField()),
                ("issued_at", models.DateTimeField()),
                ("status", models.CharField(choices=[("OPEN", "Open"), ("RESOLVED", "Resolved"), ("FAILED", "Failed")], default="OPEN", max_length=12)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("resolution_note", models.CharField(blank=True, max_length=255, null=True)),
                ("recipient_emails_json", models.JSONField(blank=True, null=True)),
                ("recipient_roles_json", models.JSONField(blank=True, null=True)),
                ("email_status", models.CharField(choices=[("OPEN", "Open"), ("RESOLVED", "Resolved"), ("FAILED", "Failed")], default="OPEN", max_length=12)),
                ("email_sent_at", models.DateTimeField(blank=True, null=True)),
                ("email_attempt_count", models.PositiveIntegerField(default=0)),
                ("email_error_message", models.TextField(blank=True, null=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="submission_non_compliance_notices", to="tenants.campus")),
                ("department", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="submission_non_compliance_notices", to="tenants.department")),
                ("faculty_user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="submission_non_compliance_notices", to="accounts.user")),
                ("offering", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="submission_non_compliance_notices", to="academics.courseoffering")),
                ("submission", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="resolved_non_compliance_notices", to="grading.gradesubmission")),
                ("template_period", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="submission_non_compliance_notices", to="grading.gradingtemplateperiod")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="submission_non_compliance_notices", to="tenants.tenant")),
            ],
            options={
                "db_table": "submission_non_compliance_notices",
                "ordering": ["-issued_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="submissionnoncompliancenotice",
            index=models.Index(fields=["tenant", "faculty_user", "status"], name="idx_nc_notice_faculty_status"),
        ),
        migrations.AddIndex(
            model_name="submissionnoncompliancenotice",
            index=models.Index(fields=["offering", "template_period", "issued_at"], name="idx_nc_notice_scope_issued"),
        ),
        migrations.AddIndex(
            model_name="submissionnoncompliancenotice",
            index=models.Index(fields=["notice_level", "issued_at"], name="idx_nc_notice_level_issued"),
        ),
    ]
