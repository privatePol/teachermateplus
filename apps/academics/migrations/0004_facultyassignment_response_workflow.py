from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0003_facultyassignment_acceptance_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="facultyassignment",
            name="assignment_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facultyassignment",
            name="faculty_response_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facultyassignment",
            name="responded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facultyassignment",
            name="response_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("ACCEPTED", "Accepted"),
                    ("DECLINED", "Declined"),
                    ("CLARIFICATION_REQUESTED", "Clarification Requested"),
                ],
                default="PENDING",
                max_length=32,
            ),
        ),
    ]
