import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0004_stage41_default_contribution_deadline"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="courseexamconfiguration",
            name="contributor_roster_initialized_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="courseexamconfiguration",
            name="contributor_roster_initialized_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="initialized_exam_contributor_rosters",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="courseexamconfiguration",
            name="contributor_roster_revision",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="facultycontribution",
            name="source_assignment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="exam_contributions",
                to="academics.facultyassignment",
            ),
        ),
        migrations.AddField(
            model_name="facultycontribution",
            name="quota_snapshot",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facultycontribution",
            name="configuration_revision_snapshot",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facultycontribution",
            name="revision",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="facultycontribution",
            name="roster_status",
            field=models.CharField(
                blank=True,
                choices=[("ACTIVE", "Active"), ("BLOCKED", "Blocked")],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="facultycontribution",
            name="roster_blocked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="QuestionImportBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("status", models.CharField(choices=[("INVALID", "Invalid"), ("READY", "Ready"), ("CONFIRMED", "Confirmed"), ("EXPIRED", "Expired")], max_length=10)),
                ("contribution_revision_snapshot", models.PositiveIntegerField()),
                ("file_sha256", models.CharField(max_length=64)),
                ("filename_sha256", models.CharField(max_length=64)),
                ("total_rows", models.PositiveSmallIntegerField(default=0)),
                ("valid_rows", models.PositiveSmallIntegerField(default=0)),
                ("error_count", models.PositiveSmallIntegerField(default=0)),
                ("warning_count", models.PositiveSmallIntegerField(default=0)),
                ("resulting_question_count", models.PositiveSmallIntegerField(default=0)),
                ("expires_at", models.DateTimeField()),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("payload_purged_at", models.DateTimeField(blank=True, null=True)),
                ("confirming_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="confirmed_exam_question_batches", to=settings.AUTH_USER_MODEL)),
                ("contribution", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="question_import_batches", to="departmental_exams.facultycontribution")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="exam_question_import_batches", to="tenants.tenant")),
                ("uploading_user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="uploaded_exam_question_batches", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "departmental_exam_question_import_batches"},
        ),
        migrations.AddField(
            model_name="question",
            name="position",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="question",
            name="revision",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="question",
            name="entry_method",
            field=models.CharField(choices=[("MANUAL", "Manual"), ("CSV", "CSV")], default="MANUAL", max_length=10),
        ),
        migrations.AddField(
            model_name="question",
            name="import_batch",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="imported_questions", to="departmental_exams.questionimportbatch"),
        ),
        migrations.AlterField(
            model_name="question",
            name="question_text",
            field=models.TextField(max_length=5000),
        ),
        migrations.CreateModel(
            name="FacultyContributionEligibilitySource",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("assignment_id_snapshot", models.PositiveBigIntegerField()),
                ("offering_id_snapshot", models.PositiveBigIntegerField()),
                ("tenant_id_snapshot", models.PositiveBigIntegerField()),
                ("campus_id_snapshot", models.PositiveBigIntegerField()),
                ("is_current", models.BooleanField(default=True)),
                ("invalidated_at", models.DateTimeField(blank=True, null=True)),
                ("assignment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="exam_contribution_sources", to="academics.facultyassignment")),
                ("contribution", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="eligibility_sources", to="departmental_exams.facultycontribution")),
            ],
            options={"db_table": "departmental_exam_contribution_sources"},
        ),
        migrations.CreateModel(
            name="QuestionImportRow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("row_number", models.PositiveSmallIntegerField()),
                ("payload", models.JSONField(default=dict)),
                ("errors", models.JSONField(default=list)),
                ("warnings", models.JSONField(default=list)),
                ("fingerprint", models.CharField(blank=True, max_length=64)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="rows", to="departmental_exams.questionimportbatch")),
            ],
            options={"db_table": "departmental_exam_question_import_rows"},
        ),
    ]
