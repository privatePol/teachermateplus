import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_active_portal_session_registry"),
        ("departmental_exams", "0016_generation_source_audit_snapshot"),
    ]

    operations = [
        migrations.CreateModel(
            name="AutomaticGenerationAuditRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PASS", "Pass"),
                            ("WARNING", "Warning"),
                            ("FAIL", "Fail"),
                        ],
                        max_length=10,
                    ),
                ),
                ("check_version", models.CharField(max_length=32)),
                ("run_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("findings_snapshot", models.JSONField(default=list)),
                ("summary_counts_snapshot", models.JSONField(default=dict)),
                (
                    "generation_revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="automatic_audit_runs",
                        to="departmental_exams.examgenerationrevision",
                    ),
                ),
                (
                    "run_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="departmental_exam_automatic_audit_runs",
                        to="accounts.user",
                    ),
                ),
            ],
            options={
                "db_table": "departmental_exam_automatic_audit_runs",
                "indexes": [
                    models.Index(
                        fields=["generation_revision", "-run_at"],
                        name="idx_de_auto_audit_revision",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(status__in=["PASS", "WARNING", "FAIL"]),
                        name="ck_de_auto_audit_status",
                    )
                ],
            },
        )
    ]
