"""Materialize the Stage 4.1 cycle default and course deadline provenance."""

from django.db import migrations, models


def backfill_deadline_sources(apps, schema_editor):
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    Configuration.objects.filter(contribution_deadline__isnull=False).update(
        contribution_deadline_source="OVERRIDE"
    )


def preserve_effective_deadlines(apps, schema_editor):
    # Reversing removes provenance and the cycle default only. The effective
    # contribution_deadline field already existed in 0003 and is untouched.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0003_cao_default_override_counts"),
    ]

    operations = [
        migrations.AddField(
            model_name="examinationcycle",
            name="default_contribution_deadline",
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="courseexamconfiguration",
            name="contribution_deadline_source",
            field=models.CharField(
                blank=True,
                choices=[("DEFAULT", "Cycle default"), ("OVERRIDE", "Course override")],
                max_length=8,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_deadline_sources, preserve_effective_deadlines),
        migrations.AddConstraint(
            model_name="courseexamconfiguration",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        contribution_deadline__isnull=True,
                        contribution_deadline_source__isnull=True,
                    )
                    | models.Q(
                        contribution_deadline__isnull=False,
                        contribution_deadline_source__isnull=False,
                        contribution_deadline_source__in=["DEFAULT", "OVERRIDE"],
                    )
                ),
                name="ck_de_cfg_deadline_source",
            ),
        ),
    ]
