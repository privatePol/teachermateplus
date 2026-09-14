from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0028_case_generation_snapshots"),
    ]

    operations = [
        migrations.AddField(
            model_name="questionimportbatch",
            name="target_section",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="question_import_batches",
                to="departmental_exams.examsection",
            ),
        ),
    ]
