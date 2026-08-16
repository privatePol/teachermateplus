import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


def backfill_confirmed_progress(apps, schema_editor):
    Batch = apps.get_model("departmental_exams", "QuestionImportBatch")
    Batch.objects.using(schema_editor.connection.alias).filter(status="CONFIRMED").update(
        committed_rows=models.F("total_rows"),
    )


def reverse_resumable_imports(apps, schema_editor):
    """Discard incomplete imports before the pre-0018 constraints return.

    IMPORTING, PAUSED, and FAILED have no lossless pre-0018 representation.
    They become EXPIRED without fabricating confirmation; partial Question rows
    and confidential staged rows are deleted, so resumable progress is lost.
    """
    Batch = apps.get_model("departmental_exams", "QuestionImportBatch")
    Question = apps.get_model("departmental_exams", "Question")
    Row = apps.get_model("departmental_exams", "QuestionImportRow")
    database = schema_editor.connection.alias
    incomplete_statuses = ["IMPORTING", "PAUSED", "FAILED"]
    batch_ids = list(
        Batch.objects.using(database)
        .filter(status__in=incomplete_statuses)
        .values_list("pk", flat=True)
    )
    if not batch_ids:
        return
    placeholders = ", ".join(["%s"] * len(batch_ids))
    question_table = schema_editor.quote_name(Question._meta.db_table)
    row_table = schema_editor.quote_name(Row._meta.db_table)
    # Partial import questions cannot have valid downstream Stage 6 rows. Raw,
    # parameterized deletes avoid historical-model collector mismatches while
    # database foreign keys still fail closed if that invariant is violated.
    schema_editor.execute(
        f"DELETE FROM {question_table} WHERE import_batch_id IN ({placeholders})",
        batch_ids,
    )
    schema_editor.execute(
        f"DELETE FROM {row_table} WHERE batch_id IN ({placeholders})",
        batch_ids,
    )
    Batch.objects.using(database).filter(pk__in=batch_ids).update(
        status="EXPIRED",
        active_contribution=None,
        committed_rows=0,
        next_row_number=None,
        started_at=None,
        progress_updated_at=None,
        confirmed_at=None,
        payload_purged_at=timezone.now(),
        failure_code="",
        failure_message="",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0017_automatic_generation_audit_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="import_row_number",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="questionimportbatch",
            name="active_contribution",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="active_question_import_batch",
                to="departmental_exams.facultycontribution",
            ),
        ),
        migrations.AddField(
            model_name="questionimportbatch",
            name="committed_rows",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="questionimportbatch",
            name="failure_code",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="questionimportbatch",
            name="failure_message",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="questionimportbatch",
            name="next_row_number",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="questionimportbatch",
            name="progress_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="questionimportbatch",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="questionimportbatch",
            name="status",
            field=models.CharField(
                choices=[
                    ("INVALID", "Invalid"),
                    ("READY", "Ready"),
                    ("IMPORTING", "Importing"),
                    ("PAUSED", "Paused"),
                    ("FAILED", "Failed"),
                    ("CONFIRMED", "Confirmed"),
                    ("EXPIRED", "Expired"),
                ],
                max_length=10,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="questionimportbatch",
            name="ck_de_batch_status_times",
        ),
        migrations.RemoveConstraint(
            model_name="questionimportbatch",
            name="ck_de_batch_validity",
        ),
        migrations.RunPython(backfill_confirmed_progress, reverse_resumable_imports),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.UniqueConstraint(
                fields=("import_batch", "import_row_number"),
                name="uq_de_question_import_row",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionimportbatch",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status="CONFIRMED",
                        confirmed_at__isnull=False,
                        payload_purged_at__isnull=False,
                    )
                    | models.Q(
                        status="EXPIRED",
                        confirmed_at__isnull=True,
                        payload_purged_at__isnull=False,
                    )
                    | models.Q(
                        status__in=["INVALID", "READY"],
                        confirmed_at__isnull=True,
                        payload_purged_at__isnull=True,
                    )
                    | models.Q(
                        status="FAILED",
                        confirmed_at__isnull=True,
                        payload_purged_at__isnull=False,
                    )
                    | models.Q(
                        status__in=["IMPORTING", "PAUSED"],
                        confirmed_at__isnull=True,
                        payload_purged_at__isnull=True,
                        started_at__isnull=False,
                        progress_updated_at__isnull=False,
                    )
                ),
                name="ck_de_batch_status_times",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionimportbatch",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=[
                            "READY",
                            "IMPORTING",
                            "PAUSED",
                            "CONFIRMED",
                        ],
                        error_count=0,
                        valid_rows__gte=1,
                    )
                    | models.Q(status="INVALID", error_count__gte=1)
                    | models.Q(status__in=["FAILED", "EXPIRED"])
                ),
                name="ck_de_batch_validity",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionimportbatch",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["INVALID", "READY", "FAILED", "EXPIRED"],
                        committed_rows=0,
                        next_row_number__isnull=True,
                    )
                    | models.Q(
                        status="CONFIRMED",
                        committed_rows=models.F("total_rows"),
                        next_row_number__isnull=True,
                    )
                    | models.Q(
                        status__in=["IMPORTING", "PAUSED"],
                        committed_rows__lt=models.F("total_rows"),
                        next_row_number__isnull=False,
                    )
                ),
                name="ck_de_batch_progress",
            ),
        ),
        migrations.AddConstraint(
            model_name="questionimportbatch",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        status__in=["IMPORTING", "PAUSED"],
                        active_contribution=models.F("contribution"),
                    )
                    | models.Q(
                        status__in=["INVALID", "READY", "FAILED", "CONFIRMED", "EXPIRED"],
                        active_contribution__isnull=True,
                    )
                ),
                name="ck_de_batch_active_contrib",
            ),
        ),
    ]
