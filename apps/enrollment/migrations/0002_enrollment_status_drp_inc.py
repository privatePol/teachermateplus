from django.db import migrations, models


def forward_rename_dr_to_drp(apps, schema_editor):
    Enrollment = apps.get_model("enrollment", "Enrollment")
    Enrollment.objects.filter(enrollment_status="DR").update(enrollment_status="DRP")


def reverse_rename_drp_to_dr(apps, schema_editor):
    Enrollment = apps.get_model("enrollment", "Enrollment")
    Enrollment.objects.filter(enrollment_status="DRP").update(enrollment_status="DR")


class Migration(migrations.Migration):
    dependencies = [
        ("enrollment", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forward_rename_dr_to_drp, reverse_rename_drp_to_dr),
        migrations.AlterField(
            model_name="enrollment",
            name="enrollment_status",
            field=models.CharField(
                choices=[
                    ("ACTIVE", "Active"),
                    ("DRP", "Dropped"),
                    ("W", "Withdrawn"),
                    ("INC", "Incomplete"),
                ],
                default="ACTIVE",
                max_length=16,
            ),
        ),
    ]
