from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("students", "0004_student_idx_students_scope_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="student",
            name="official_email",
            field=models.EmailField(blank=True, max_length=254, null=True),
        ),
        migrations.AddField(
            model_name="student",
            name="official_email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
