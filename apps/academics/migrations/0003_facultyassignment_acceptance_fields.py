from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0002_facultyassignment_campus_facultyassignment_tenant"),
        ("accounts", "0003_user_default_department"),
    ]

    operations = [
        migrations.AddField(
            model_name="facultyassignment",
            name="accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facultyassignment",
            name="accepted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="accepted_faculty_assignments",
                to="accounts.user",
            ),
        ),
    ]
