from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("departmental_exams", "0013_correct_coverage_source_invariant"),
    ]

    operations = [
        migrations.AddField(
            model_name="examinationcycle",
            name="automatic_campus_contribution_policy",
            field=models.CharField(
                choices=[
                    ("STRICT", "Require every participating campus"),
                    (
                        "AVAILABLE_WITH_WARNING",
                        "Use represented campuses and show a warning",
                    ),
                ],
                default="AVAILABLE_WITH_WARNING",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="examinationcycle",
            name="automatic_contributor_completion_policy",
            field=models.CharField(
                choices=[
                    ("REQUIRE_ALL", "Require every active contributor"),
                    (
                        "SUFFICIENT_POOL",
                        "Use the sufficient Submitted pool and show a warning",
                    ),
                ],
                default="SUFFICIENT_POOL",
                max_length=16,
            ),
        ),
    ]
