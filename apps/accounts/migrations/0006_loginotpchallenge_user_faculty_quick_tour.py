from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_usersignaturecredential_usersignatureusagelog"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="faculty_quick_tour_disabled",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="LoginOtpChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "portal_code",
                    models.CharField(
                        choices=[("ADMIN", "Admin Portal"), ("FACULTY", "Faculty Portal")],
                        max_length=20,
                    ),
                ),
                ("code_hash", models.CharField(max_length=128)),
                ("sent_to_email", models.EmailField(max_length=254)),
                ("expires_at", models.DateTimeField()),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="login_otp_challenges",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "login_otp_challenges",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["user", "portal_code", "created_at"], name="login_otp_c_user_id_c278bb_idx"),
                    models.Index(fields=["portal_code", "expires_at"], name="login_otp_c_portal__fd276d_idx"),
                ],
            },
        ),
    ]
