import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("students", "0004_student_idx_students_scope_status"),
        ("tenants", "0004_tenantapikey"),
    ]

    operations = [
        migrations.CreateModel(
            name="StudentAccountLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                ("linked_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "campus",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="student_account_links",
                        to="tenants.campus",
                    ),
                ),
                (
                    "linked_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_student_account_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="account_links",
                        to="students.student",
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="student_account_links",
                        to="tenants.tenant",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="student_account_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "student_account_links",
                "ordering": ["tenant", "campus", "student__last_name", "student__first_name", "-linked_at"],
            },
        ),
        migrations.AddIndex(
            model_name="studentaccountlink",
            index=models.Index(fields=["user", "is_active"], name="idx_student_link_user_active"),
        ),
        migrations.AddIndex(
            model_name="studentaccountlink",
            index=models.Index(fields=["tenant", "campus", "student", "is_active"], name="idx_student_link_scope"),
        ),
        migrations.AddConstraint(
            model_name="studentaccountlink",
            constraint=models.UniqueConstraint(
                condition=Q(("is_active", True)),
                fields=("student",),
                name="uq_active_student_account_link_student",
            ),
        ),
        migrations.AddConstraint(
            model_name="studentaccountlink",
            constraint=models.UniqueConstraint(
                condition=Q(("is_active", True)),
                fields=("user",),
                name="uq_active_student_account_link_user",
            ),
        ),
    ]
