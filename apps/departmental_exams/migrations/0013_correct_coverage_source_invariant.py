from django.db import migrations, models


VALID_COVERAGE_SOURCES = ("DEFAULT", "OVERRIDE")


def normalize_coverage_sources(apps, schema_editor):
    CourseExamConfiguration = apps.get_model(
        "departmental_exams", "CourseExamConfiguration"
    )
    CourseExamConfiguration.objects.filter(coverage="").exclude(
        coverage_source__isnull=True
    ).update(coverage_source=None)
    CourseExamConfiguration.objects.exclude(coverage="").filter(
        coverage_source__isnull=True
    ).update(coverage_source="OVERRIDE")
    CourseExamConfiguration.objects.exclude(coverage="").exclude(
        coverage_source__isnull=True
    ).exclude(coverage_source__in=VALID_COVERAGE_SOURCES).update(
        coverage_source="OVERRIDE"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("departmental_exams", "0012_cycle_default_coverage"),
    ]

    operations = [
        migrations.RunPython(
            normalize_coverage_sources,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="courseexamconfiguration",
            name="ck_de_cfg_coverage_source",
        ),
        migrations.AddConstraint(
            model_name="courseexamconfiguration",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(coverage="", coverage_source__isnull=True)
                    | (
                        ~models.Q(coverage="")
                        & models.Q(coverage_source__isnull=False)
                        & models.Q(
                            coverage_source__in=VALID_COVERAGE_SOURCES
                        )
                    )
                ),
                name="ck_de_cfg_coverage_source",
            ),
        ),
    ]
