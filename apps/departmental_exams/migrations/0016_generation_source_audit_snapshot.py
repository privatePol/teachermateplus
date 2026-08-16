import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0015_questionnaire_print_release"),
    ]

    operations = [
        migrations.CreateModel(
            name="GenerationSourceAuditSnapshot",
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
                ("schema_version", models.CharField(max_length=32)),
                ("logical_identity_version", models.CharField(max_length=32)),
                ("submitted_count", models.PositiveIntegerField()),
                ("eligible_count", models.PositiveIntegerField()),
                ("unique_logical_count", models.PositiveIntegerField()),
                ("redundant_copy_count", models.PositiveIntegerField()),
                (
                    "generation_revision",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="source_audit_snapshot",
                        to="departmental_exams.examgenerationrevision",
                    ),
                ),
            ],
            options={
                "db_table": "departmental_exam_generation_source_audits",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("eligible_count__gte", models.F("unique_logical_count")),
                            ("submitted_count__gte", models.F("eligible_count")),
                        ),
                        name="ck_de_source_audit_counts",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "redundant_copy_count",
                                models.F("eligible_count")
                                - models.F("unique_logical_count"),
                            )
                        ),
                        name="ck_de_source_audit_redundant",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="GenerationSourceQuestionSnapshot",
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
                ("source_question_id_snapshot", models.PositiveBigIntegerField()),
                ("source_question_revision", models.PositiveIntegerField()),
                ("source_question_digest", models.CharField(max_length=64)),
                ("contribution_id_snapshot", models.PositiveBigIntegerField()),
                ("contribution_revision_snapshot", models.PositiveIntegerField()),
                ("contribution_submitted_at_snapshot", models.DateTimeField()),
                ("contributor_id_snapshot", models.PositiveBigIntegerField()),
                ("contributor_name_snapshot", models.CharField(max_length=255)),
                ("campus_id_snapshot", models.PositiveBigIntegerField()),
                ("campus_code_snapshot", models.CharField(max_length=30)),
                ("campus_name_snapshot", models.CharField(max_length=120)),
                ("assignment_context_snapshot", models.JSONField(default=list)),
                ("question_text_snapshot", models.TextField(max_length=5000)),
                ("choices_snapshot", models.JSONField(default=list)),
                (
                    "difficulty_snapshot",
                    models.CharField(
                        choices=[
                            ("EASY", "Easy"),
                            ("MODERATE", "Moderate"),
                            ("DIFFICULT", "Difficult"),
                        ],
                        max_length=10,
                    ),
                ),
                (
                    "correct_answer_snapshot",
                    models.CharField(
                        choices=[("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")],
                        max_length=1,
                    ),
                ),
                ("normalized_fingerprint", models.CharField(max_length=64)),
                ("eligible_for_generation", models.BooleanField(default=True)),
                ("exclusion_code", models.CharField(blank=True, max_length=40)),
                (
                    "audit_snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="question_snapshots",
                        to="departmental_exams.generationsourceauditsnapshot",
                    ),
                ),
                (
                    "source_question",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="generation_source_audit_snapshots",
                        to="departmental_exams.question",
                    ),
                ),
            ],
            options={
                "db_table": "departmental_exam_generation_source_questions",
                "indexes": [
                    models.Index(
                        fields=["audit_snapshot", "normalized_fingerprint"],
                        name="idx_de_source_audit_fp",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("audit_snapshot", "source_question"),
                        name="uq_de_source_audit_question",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("contribution_revision_snapshot__gte", 1),
                            ("correct_answer_snapshot__in", ["A", "B", "C", "D"]),
                            (
                                "difficulty_snapshot__in",
                                ["EASY", "MODERATE", "DIFFICULT"],
                            ),
                            ("source_question_revision__gte", 1),
                        ),
                        name="ck_de_source_audit_question_values",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("eligible_for_generation", True),
                                ("exclusion_code", ""),
                            ),
                            models.Q(
                                ("eligible_for_generation", False),
                                models.Q(("exclusion_code", ""), _negated=True),
                            ),
                            _connector="OR",
                        ),
                        name="ck_de_source_audit_eligibility",
                    ),
                ],
            },
        ),
    ]
