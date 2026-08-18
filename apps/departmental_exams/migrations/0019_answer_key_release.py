import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0018_resumable_question_csv_import"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AnswerKeyRelease",
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
                ("available_from", models.DateTimeField()),
                ("available_until", models.DateTimeField()),
                ("released_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("attestation_version", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[("ACTIVE", "Active"), ("REVOKED", "Revoked")],
                        default="ACTIVE",
                        max_length=8,
                    ),
                ),
                (
                    "active_marker",
                    models.PositiveSmallIntegerField(blank=True, default=1, null=True),
                ),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "cycle_course",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="answer_key_releases",
                        to="departmental_exams.cyclecourse",
                    ),
                ),
                (
                    "generation_revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="answer_key_releases",
                        to="departmental_exams.examgenerationrevision",
                    ),
                ),
                (
                    "released_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="released_departmental_exam_answer_keys",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "revoked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="revoked_departmental_exam_answer_key_releases",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "departmental_exam_answer_key_releases",
                "indexes": [
                    models.Index(
                        fields=[
                            "cycle_course",
                            "status",
                            "available_from",
                            "available_until",
                        ],
                        name="idx_de_key_release_window",
                    ),
                    models.Index(
                        fields=["generation_revision", "status"],
                        name="idx_de_key_release_rev",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("cycle_course", "active_marker"),
                        name="uq_de_key_release_active",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("available_until__gt", models.F("available_from"))
                        ),
                        name="ck_de_key_release_window",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("attestation_version", ""),
                            _negated=True,
                        ),
                        name="ck_de_key_attestation",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("active_marker", 1),
                                ("revoked_at__isnull", True),
                                ("revoked_by__isnull", True),
                                ("status", "ACTIVE"),
                            ),
                            models.Q(
                                ("active_marker__isnull", True),
                                ("revoked_at__isnull", False),
                                ("revoked_by__isnull", False),
                                ("status", "REVOKED"),
                            ),
                            _connector="OR",
                        ),
                        name="ck_de_key_release_status",
                    ),
                ],
            },
        )
    ]
