from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orientation_feedback", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orientationsurveyparticipation",
            name="validation_method",
            field=models.CharField(
                choices=[
                    ("EMAIL", "Registered email (legacy)"),
                    ("EMAIL_OTP", "Registered email with one-time code"),
                ],
                default="EMAIL",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="orientationsurveyparticipation",
            name="email_otp_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="orientationsurveyparticipation",
            name="email_otp_failed_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="orientationsurveyparticipation",
            name="email_otp_hash",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="orientationsurveyparticipation",
            name="email_otp_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="orientationsurveyparticipation",
            name="email_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
