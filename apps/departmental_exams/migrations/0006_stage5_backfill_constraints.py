import unicodedata

from django.db import migrations, models
from django.utils import timezone


VALID_DIFFICULTIES = {"EASY", "MODERATE", "DIFFICULT"}
VALID_ANSWERS = {"A", "B", "C", "D"}


def _comparison_value(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


def backfill_stage5(apps, schema_editor):
    Contribution = apps.get_model("departmental_exams", "FacultyContribution")
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    Source = apps.get_model("departmental_exams", "FacultyContributionEligibilitySource")
    Question = apps.get_model("departmental_exams", "Question")
    now = timezone.now()

    for contribution in Contribution.objects.select_related(
        "cycle_course", "source_assignment", "source_assignment__offering"
    ).order_by("id"):
        configuration = Configuration.objects.filter(cycle_course_id=contribution.cycle_course_id).first()
        if configuration is None or configuration.questions_required_per_faculty is None:
            raise RuntimeError(f"Contribution {contribution.pk} has no deterministic course quota.")
        quota = configuration.questions_required_per_faculty
        if not 50 <= quota <= 75:
            raise RuntimeError(f"Contribution {contribution.pk} has an invalid course quota.")
        if contribution.status == "SUBMITTED" and contribution.submitted_at is None:
            raise RuntimeError(f"Submitted contribution {contribution.pk} has no submitted_at value.")
        if contribution.status == "DRAFT" and contribution.submitted_at is not None:
            raise RuntimeError(f"Draft contribution {contribution.pk} has a submitted_at value.")
        assignment = contribution.source_assignment
        if assignment is None:
            raise RuntimeError(f"Contribution {contribution.pk} has no representable legacy assignment.")
        offering = assignment.offering
        structurally_current = bool(
            assignment.is_active
            and assignment.response_status == "ACCEPTED"
            and assignment.accepted_at is not None
            and assignment.tenant_id
            and assignment.campus_id
            and assignment.tenant_id == offering.tenant_id
            and assignment.campus_id == offering.campus_id
            and offering.is_active
            and offering.status == "OPEN"
        )
        Source.objects.get_or_create(
            contribution_id=contribution.pk,
            assignment_id_snapshot=assignment.pk,
            defaults={
                "assignment_id": assignment.pk,
                "offering_id_snapshot": offering.pk,
                "tenant_id_snapshot": assignment.tenant_id or offering.tenant_id,
                "campus_id_snapshot": assignment.campus_id or offering.campus_id,
                "is_current": structurally_current,
                "invalidated_at": None if structurally_current else now,
            },
        )
        contribution.quota_snapshot = quota
        contribution.configuration_revision_snapshot = configuration.revision
        contribution.revision = contribution.revision or 1
        contribution.roster_status = "ACTIVE" if structurally_current else "BLOCKED"
        contribution.roster_blocked_at = None if structurally_current else now
        contribution.save(update_fields=[
            "quota_snapshot",
            "configuration_revision_snapshot",
            "revision",
            "roster_status",
            "roster_blocked_at",
        ])

    for contribution_id in Contribution.objects.order_by("id").values_list("id", flat=True):
        questions = list(Question.objects.filter(contribution_id=contribution_id).order_by("created_at", "id"))
        for position, question in enumerate(questions, start=1):
            values = [question.choice_a, question.choice_b, question.choice_c, question.choice_d]
            if not (question.question_text or "").strip() or len(question.question_text or "") > 5000:
                raise RuntimeError(f"Question row {question.pk} has invalid question text.")
            if any(not (value or "").strip() or len(value) > 1000 for value in values):
                raise RuntimeError(f"Question row {question.pk} has invalid choices.")
            if len({_comparison_value(value) for value in values}) != 4:
                raise RuntimeError(f"Question row {question.pk} has duplicate choices.")
            if question.correct_answer not in VALID_ANSWERS or question.difficulty not in VALID_DIFFICULTIES:
                raise RuntimeError(f"Question row {question.pk} has invalid answer or difficulty.")
            question.position = position
            question.revision = question.revision or 1
            question.entry_method = "MANUAL"
            question.save(update_fields=["position", "revision", "entry_method"])


def refuse_unsafe_reverse(apps, schema_editor):
    Contribution = apps.get_model("departmental_exams", "FacultyContribution")
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    Source = apps.get_model("departmental_exams", "FacultyContributionEligibilitySource")
    Question = apps.get_model("departmental_exams", "Question")
    Batch = apps.get_model("departmental_exams", "QuestionImportBatch")

    if Batch.objects.exists():
        raise RuntimeError("Stage 5 import activity prevents safe reversal.")
    if Configuration.objects.filter(contributor_roster_initialized_at__isnull=False).exists():
        raise RuntimeError("Initialized Stage 5 rosters prevent safe reversal.")
    if Contribution.objects.exclude(revision=1).exists() or Question.objects.exclude(revision=1).exists():
        raise RuntimeError("Stage 5 contribution or question mutations prevent safe reversal.")
    if Question.objects.filter(entry_method="CSV").exists():
        raise RuntimeError("CSV-created questions prevent safe reversal.")
    for contribution in Contribution.objects.order_by("id"):
        sources = list(Source.objects.filter(contribution_id=contribution.pk))
        if (
            len(sources) != 1
            or sources[0].assignment_id is None
            or sources[0].assignment_id != contribution.source_assignment_id
        ):
            raise RuntimeError(
                f"Contribution {contribution.pk} cannot be represented by the legacy single-source schema."
            )


class Migration(migrations.Migration):
    dependencies = [("departmental_exams", "0005_stage5_nullable_schema")]

    operations = [
        migrations.RunPython(backfill_stage5, refuse_unsafe_reverse),
        migrations.AddConstraint(
            model_name="courseexamconfiguration",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("contributor_roster_initialized_at__isnull", True), ("contributor_roster_initialized_by__isnull", True), ("contributor_roster_revision", 0)), models.Q(("contributor_roster_initialized_at__isnull", False), ("contributor_roster_initialized_by__isnull", False), ("contributor_roster_revision__gte", 1)), _connector="OR"), name="ck_de_cfg_roster_state"),
        ),
        migrations.AlterField(
            model_name="facultycontribution",
            name="quota_snapshot",
            field=models.PositiveSmallIntegerField(),
        ),
        migrations.AlterField(
            model_name="facultycontribution",
            name="configuration_revision_snapshot",
            field=models.PositiveIntegerField(),
        ),
        migrations.AlterField(
            model_name="facultycontribution",
            name="roster_status",
            field=models.CharField(choices=[("ACTIVE", "Active"), ("BLOCKED", "Blocked")], default="ACTIVE", max_length=10),
        ),
        migrations.AlterField(
            model_name="question",
            name="position",
            field=models.PositiveIntegerField(),
        ),
        migrations.AddConstraint(
            model_name="facultycontribution",
            constraint=models.CheckConstraint(condition=models.Q(("quota_snapshot__gte", 50), ("quota_snapshot__lte", 75)), name="ck_de_contrib_quota_50_75"),
        ),
        migrations.AddConstraint(
            model_name="facultycontribution",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("status", "DRAFT"), ("submitted_at__isnull", True)), models.Q(("status", "SUBMITTED"), ("submitted_at__isnull", False)), _connector="OR"), name="ck_de_contrib_submit_time"),
        ),
        migrations.AddConstraint(
            model_name="facultycontribution",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("roster_blocked_at__isnull", True), ("roster_status", "ACTIVE")), models.Q(("roster_blocked_at__isnull", False), ("roster_status", "BLOCKED")), _connector="OR"), name="ck_de_contrib_block_time"),
        ),
        migrations.AddConstraint(
            model_name="facultycontribution",
            constraint=models.CheckConstraint(condition=models.Q(("configuration_revision_snapshot__gte", 1), ("revision__gte", 1)), name="ck_de_contrib_revisions"),
        ),
        migrations.AddIndex(
            model_name="facultycontribution",
            index=models.Index(fields=["cycle_course", "status", "roster_status"], name="idx_de_contrib_monitor"),
        ),
        migrations.AddConstraint(
            model_name="facultycontributioneligibilitysource",
            constraint=models.UniqueConstraint(fields=("contribution", "assignment_id_snapshot"), name="uq_de_contrib_source_assignment"),
        ),
        migrations.AddConstraint(
            model_name="facultycontributioneligibilitysource",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("invalidated_at__isnull", True), ("is_current", True)), models.Q(("invalidated_at__isnull", False), ("is_current", False)), _connector="OR"), name="ck_de_source_invalidated"),
        ),
        migrations.AddIndex(
            model_name="facultycontributioneligibilitysource",
            index=models.Index(fields=["contribution", "is_current", "assignment_id_snapshot"], name="idx_de_source_current"),
        ),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.UniqueConstraint(fields=("contribution", "position"), name="uq_de_question_position"),
        ),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.CheckConstraint(condition=models.Q(("position__gte", 1)), name="ck_de_question_position"),
        ),
        migrations.AddConstraint(
            model_name="question",
            constraint=models.CheckConstraint(condition=models.Q(("correct_answer__in", ["A", "B", "C", "D"]), ("difficulty__in", ["EASY", "MODERATE", "DIFFICULT"]), ("entry_method__in", ["MANUAL", "CSV"]), ("revision__gte", 1)), name="ck_de_question_codes"),
        ),
        migrations.AddConstraint(
            model_name="questionimportbatch",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("confirmed_at__isnull", False), ("payload_purged_at__isnull", False), ("status", "CONFIRMED")), models.Q(("confirmed_at__isnull", True), ("payload_purged_at__isnull", False), ("status", "EXPIRED")), models.Q(("confirmed_at__isnull", True), ("status__in", ["INVALID", "READY"])), _connector="OR"), name="ck_de_batch_status_times"),
        ),
        migrations.AddConstraint(
            model_name="questionimportbatch",
            constraint=models.CheckConstraint(condition=models.Q(("valid_rows__lte", models.F("total_rows"))), name="ck_de_batch_counts"),
        ),
        migrations.AddConstraint(
            model_name="questionimportbatch",
            constraint=models.CheckConstraint(condition=models.Q(models.Q(("error_count", 0), ("status__in", ["READY", "CONFIRMED"]), ("valid_rows__gte", 1)), models.Q(("error_count__gte", 1), ("status", "INVALID")), ("status", "EXPIRED"), _connector="OR"), name="ck_de_batch_validity"),
        ),
        migrations.AddIndex(
            model_name="questionimportbatch",
            index=models.Index(fields=["uploading_user", "status", "expires_at"], name="idx_de_batch_owner_status"),
        ),
        migrations.AddIndex(
            model_name="questionimportbatch",
            index=models.Index(fields=["contribution", "status", "expires_at"], name="idx_de_batch_contrib_status"),
        ),
        migrations.AddConstraint(
            model_name="questionimportrow",
            constraint=models.UniqueConstraint(fields=("batch", "row_number"), name="uq_de_batch_row_number"),
        ),
        migrations.AddIndex(
            model_name="questionimportrow",
            index=models.Index(fields=["batch", "row_number"], name="idx_de_batch_row"),
        ),
    ]
