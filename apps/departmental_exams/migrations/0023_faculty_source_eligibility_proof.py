from django.db import migrations, models
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import F


SOURCE_HISTORY_BASELINE_MIGRATION = "0006_stage5_backfill_constraints"


def backfill_safe_legacy_eligibility_proof(apps, schema_editor):
    """Stamp only source rows with a persisted authoritative-current transition.

    Migration 0006 created some source rows as current using structural checks
    that predated the complete eligibility evaluator. Those rows and every
    create-already-invalid row are deliberately excluded. After 0006 completed,
    the sole application writer set a source current only from the authoritative
    eligible-source inventory. A later invalidation strictly after creation is
    therefore source-specific proof that the row passed that evaluator.
    """
    Source = apps.get_model(
        "departmental_exams",
        "FacultyContributionEligibilitySource",
    )
    recorder = MigrationRecorder(schema_editor.connection)
    baseline_applied_at = (
        recorder.migration_qs.filter(
            app="departmental_exams",
            name=SOURCE_HISTORY_BASELINE_MIGRATION,
        )
        .values_list("applied", flat=True)
        .first()
    )
    if baseline_applied_at is None:
        raise RuntimeError(
            "Cannot prove the Stage 5 source-history baseline migration time."
        )

    Source.objects.using(schema_editor.connection.alias).filter(
        eligibility_proven_at__isnull=True,
        is_current=False,
        created_at__gt=baseline_applied_at,
        invalidated_at__gt=F("created_at"),
    ).update(eligibility_proven_at=F("invalidated_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("departmental_exams", "0022_docx_question_import"),
    ]

    operations = [
        migrations.AddField(
            model_name="facultycontributioneligibilitysource",
            name="eligibility_proven_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_safe_legacy_eligibility_proof,
            migrations.RunPython.noop,
        ),
    ]
