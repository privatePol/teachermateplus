from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0010_facultyassignmentreplacementlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="course",
            name="exam_department",
            field=models.ForeignKey(
                blank=True,
                help_text="Departmental Exam Builder ownership only; does not change ordinary course visibility.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="exam_department_courses",
                to="tenants.department",
            ),
        ),
    ]
