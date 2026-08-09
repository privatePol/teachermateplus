import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0009_stage6b_generation_output"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="examgenerationrevision",
            name="approval_attestation_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="examgenerationrevision",
            name="locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="examgenerationrevision",
            name="locked_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="locked_departmental_exams",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="examgenerationrevision",
            name="status",
            field=models.CharField(
                choices=[
                    ("GENERATED", "Generated"),
                    ("SUPERSEDED", "Superseded"),
                    ("LOCKED", "Locked"),
                ],
                default="GENERATED",
                max_length=12,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="examgenerationrevision",
            name="ck_de_gen_current_status",
        ),
        migrations.AddConstraint(
            model_name="examgenerationrevision",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="GENERATED",
                        current_marker=1,
                        current_marker__isnull=False,
                        locked_at__isnull=True,
                        locked_by__isnull=True,
                        approval_attestation_version="",
                    )
                    | models.Q(
                        status="SUPERSEDED",
                        current_marker__isnull=True,
                        locked_at__isnull=True,
                        locked_by__isnull=True,
                        approval_attestation_version="",
                    )
                    | (
                        models.Q(
                            status="LOCKED",
                            current_marker=1,
                            current_marker__isnull=False,
                            locked_at__isnull=False,
                            locked_by__isnull=False,
                        )
                        & ~models.Q(approval_attestation_version="")
                    )
                ),
                name="ck_de_gen_current_status",
            ),
        ),
    ]
