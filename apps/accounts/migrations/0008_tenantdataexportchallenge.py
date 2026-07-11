import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_userdeactivationschedule"),
        ("tenants", "0005_enable_existing_sis_api_feature"),
    ]

    operations = [
        migrations.CreateModel(
            name="TenantDataExportChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("otp_hash", models.CharField(blank=True, max_length=128)),
                ("sent_to_email", models.EmailField(max_length=254)),
                ("password_verified_at", models.DateTimeField()),
                ("otp_sent_at", models.DateTimeField(blank=True, null=True)),
                ("otp_expires_at", models.DateTimeField(blank=True, null=True)),
                ("otp_verified_at", models.DateTimeField(blank=True, null=True)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("failed_attempt_count", models.PositiveIntegerField(default=0)),
                ("resend_count", models.PositiveIntegerField(default=0)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("OTP_SENT", "OTP Sent"),
                            ("OTP_VERIFIED", "OTP Verified"),
                            ("CONSUMED", "Consumed"),
                            ("EXPIRED", "Expired"),
                            ("LOCKED", "Locked"),
                            ("CANCELLED", "Cancelled"),
                        ],
                        default="OTP_SENT",
                        max_length=20,
                    ),
                ),
                ("request_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "requesting_user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tenant_data_export_challenges",
                        to="accounts.user",
                    ),
                ),
                (
                    "selected_tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tenant_data_export_challenges",
                        to="tenants.tenant",
                    ),
                ),
            ],
            options={
                "db_table": "tenant_data_export_challenges",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="tenantdataexportchallenge",
            index=models.Index(fields=["token"], name="idx_tenant_export_token"),
        ),
        migrations.AddIndex(
            model_name="tenantdataexportchallenge",
            index=models.Index(fields=["requesting_user", "status"], name="idx_tenant_export_user_status"),
        ),
        migrations.AddIndex(
            model_name="tenantdataexportchallenge",
            index=models.Index(fields=["selected_tenant", "created_at"], name="idx_tenant_export_tenant_time"),
        ),
    ]
