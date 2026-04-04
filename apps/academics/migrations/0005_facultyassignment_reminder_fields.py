from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0004_facultyassignment_response_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="facultyassignment",
            name="last_reminded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facultyassignment",
            name="reminder_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="facultyassignment",
            name="response_due_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="facultyassignment",
            name="response_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("ACCEPTED", "Accepted"),
                    ("DECLINED", "Declined"),
                    ("CLARIFICATION_REQUESTED", "Clarification Requested"),
                    ("EXPIRED", "Expired"),
                ],
                default="PENDING",
                max_length=32,
            ),
        ),
    ]
