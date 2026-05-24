from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("grading", "0025_alter_templatehotfixrequest_apply_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantgradingprofile",
            name="period_grade_formula_json",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenantgradingprofile",
            name="period_grade_formula_mode",
            field=models.CharField(
                choices=[
                    ("WEIGHTED_COMPONENTS", "Weighted Components"),
                    ("DEPED_TRANSMUTATION", "DepEd Transmutation Table"),
                ],
                default="WEIGHTED_COMPONENTS",
                max_length=40,
            ),
        ),
    ]
