from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("departmental_exams", "0025_faculty_case_rich_content")]
    operations = [
        migrations.AddField(
            model_name="cyclecourse", name="exam_classification",
            field=models.CharField(max_length=20, default="UNCLASSIFIED_LEGACY", choices=[
                ("UNCLASSIFIED_LEGACY", "Legacy - classification unconfirmed"),
                ("STANDARDIZED", "Standardized"), ("DEPARTMENTAL", "Departmental"),
            ]),
        ),
    ]
