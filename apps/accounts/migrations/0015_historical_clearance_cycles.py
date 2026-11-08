from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def capture_legacy_cycles(apps, schema_editor):
    Report = apps.get_model("grading", "FacultyFinalClearanceReport")
    Cycle = apps.get_model("accounts", "HistoricalClearanceCycle")
    alias = schema_editor.connection.alias
    rows = Report.objects.using(alias).order_by().values_list(
        "faculty_user_id", "tenant_id", "campus_id", "term_id"
    ).distinct().iterator(chunk_size=500)
    # Re-scanning a drained legacy source is safe. Report rows are retained
    # verbatim; a marker expresses uncertainty, never proof of PDF success.
    for user_id, tenant_id, campus_id, term_id in rows:
        Cycle.objects.using(alias).get_or_create(
            user_id=user_id, tenant_id=tenant_id, campus_id=campus_id, term_id=term_id,
        )



class Migration(migrations.Migration):
    dependencies = [("accounts", "0014_protect_login_control_fields"), ("grading", "0036_correction_email_outbox")]
    operations = [
        migrations.CreateModel(
            name="HistoricalClearanceCycle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="tenants.tenant")),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="tenants.campus")),
                ("term", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="academics.term")),
            ],
            options={"db_table": "historical_clearance_cycles", "constraints": [models.UniqueConstraint(fields=["user", "tenant", "campus", "term"], name="uq_historical_clear_cycle")]},
        ),
        migrations.RunPython(capture_legacy_cycles, migrations.RunPython.noop),
    ]
