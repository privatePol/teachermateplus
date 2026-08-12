from django.db import migrations, models


def backfill_coverage_sources(apps, schema_editor):
    CourseExamConfiguration = apps.get_model(
        "departmental_exams", "CourseExamConfiguration"
    )
    CourseExamConfiguration.objects.filter(
        coverage_source__isnull=True,
    ).exclude(coverage="").update(coverage_source="OVERRIDE")


class Migration(migrations.Migration):

    dependencies = [
        ("departmental_exams", "0011_automatic_generation_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="examinationcycle",
            name="default_coverage",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="courseexamconfiguration",
            name="coverage_source",
            field=models.CharField(
                blank=True,
                choices=[("DEFAULT", "Cycle default"), ("OVERRIDE", "Course override")],
                max_length=8,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_coverage_sources, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="courseexamconfiguration",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(coverage_source__isnull=True)
                    | models.Q(coverage_source__in=["DEFAULT", "OVERRIDE"])
                ),
                name="ck_de_cfg_coverage_source",
            ),
        ),
    ]
