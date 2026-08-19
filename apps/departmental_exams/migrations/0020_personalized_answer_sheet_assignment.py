import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0019_answer_key_release"),
        ("enrollment", "0006_classlistchangerequest_classlistchangerequestitem_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonalizedAnswerSheetAssignment",
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
                    "set_code",
                    models.CharField(
                        choices=[("A", "Set A"), ("B", "Set B")], max_length=1
                    ),
                ),
                (
                    "assignment_method",
                    models.CharField(
                        choices=[
                            ("INITIAL_BALANCED", "Initial balanced assignment"),
                            ("LATE_BALANCED", "Late balanced assignment"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "algorithm_version",
                    models.CharField(default="hmac-alternate-v1", max_length=64),
                ),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "assigned_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="assigned_personalized_answer_sheets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "course_offering",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="personalized_answer_sheet_assignments",
                        to="academics.courseoffering",
                    ),
                ),
                (
                    "enrollment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="personalized_answer_sheet_assignments",
                        to="enrollment.enrollment",
                    ),
                ),
                (
                    "generation_revision",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="personalized_answer_sheet_assignments",
                        to="departmental_exams.examgenerationrevision",
                    ),
                ),
            ],
            options={
                "db_table": "departmental_exam_personalized_sheet_assignments",
                "indexes": [
                    models.Index(
                        fields=[
                            "generation_revision",
                            "course_offering",
                            "set_code",
                        ],
                        name="idx_de_pers_rev_offer_set",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("generation_revision", "enrollment"),
                        name="uq_de_personalized_revision_enrollment",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("set_code__in", ["A", "B"])),
                        name="ck_de_personalized_set_code",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "assignment_method__in",
                                ["INITIAL_BALANCED", "LATE_BALANCED"],
                            )
                        ),
                        name="ck_de_personalized_method",
                    ),
                ],
            },
        )
    ]
