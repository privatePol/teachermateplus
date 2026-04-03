from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0002_alter_student_program"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="student",
            name="uq_students_tenant_student_no",
        ),
        migrations.AddConstraint(
            model_name="student",
            constraint=models.UniqueConstraint(
                fields=("tenant", "campus", "student_no"),
                name="uq_students_tenant_campus_student_no",
            ),
        ),
    ]
