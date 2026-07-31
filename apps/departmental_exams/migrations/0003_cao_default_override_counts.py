"""CAO default-and-override counts.

Reverse deliberately restores PER_COURSE semantics.  Course effective values
survive, but DEFAULT/OVERRIDE provenance and defaults-revision history cannot
be represented by 0002 and are intentionally lost.
"""

from django.db import migrations, models


def _valid(value):
    return value is not None and 50 <= value <= 75


def preflight_immutable_counts(apps, schema_editor):
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    immutable = Configuration.objects.filter(
        models.Q(opened_at__isnull=False)
        | models.Q(workflow_status__in=["OPEN", "CLOSED"])
        | models.Q(cycle_course__faculty_contributions__isnull=False)
        | models.Q(cycle_course__faculty_contributions__questions__isnull=False)
    ).distinct()
    invalid = immutable.exclude(
        questions_required_per_faculty__gte=50,
        questions_required_per_faculty__lte=75,
        final_item_count__gte=50,
        final_item_count__lte=75,
    )
    if invalid.exists():
        raise RuntimeError(
            "Cannot apply CAO count migration: immutable configuration rows must have both effective counts from 50 to 75."
        )


def noop_reverse_preflight(apps, schema_editor):
    pass


def backfill_cao_defaults(apps, schema_editor):
    Cycle = apps.get_model("departmental_exams", "ExaminationCycle")
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    for cycle in Cycle.objects.all().iterator():
        # A legacy fixed value becomes only the final-count default.  There was
        # no trustworthy historical cycle faculty quota.
        cycle.default_questions_required_per_faculty = None
        cycle.defaults_revision = 0
        cycle.save(update_fields=["default_questions_required_per_faculty", "defaults_revision"])
        configurations = Configuration.objects.filter(cycle_course__cycle_id=cycle.id)
        for configuration in configurations.iterator():
            immutable = (
                configuration.opened_at is not None
                or configuration.workflow_status in ("OPEN", "CLOSED")
                or Configuration.objects.filter(pk=configuration.pk, cycle_course__faculty_contributions__isnull=False).exists()
                or Configuration.objects.filter(pk=configuration.pk, cycle_course__faculty_contributions__questions__isnull=False).exists()
            )
            invalid_q = not _valid(configuration.questions_required_per_faculty)
            invalid_final = not _valid(configuration.final_item_count)
            if immutable and (invalid_q or invalid_final):
                raise RuntimeError(
                    "Cannot apply CAO count migration: immutable configuration rows must have both effective counts from 50 to 75."
                )
            changed = False
            if invalid_q:
                configuration.questions_required_per_faculty = None
                configuration.questions_required_per_faculty_source = None
                changed = True
            else:
                configuration.questions_required_per_faculty_source = "OVERRIDE"
                changed = True
            if invalid_final:
                configuration.final_item_count = None
                configuration.final_item_count_source = None
                changed = True
            elif (
                cycle.legacy_item_count_mode == "FIXED_ALL"
                and configuration.final_item_count == cycle.default_final_item_count
            ):
                configuration.final_item_count_source = "DEFAULT"
                changed = True
            else:
                configuration.final_item_count_source = "OVERRIDE"
                changed = True
            configuration.cycle_defaults_revision_snapshot = cycle.defaults_revision
            if changed and not immutable:
                configuration.revision += 1
            configuration.save(update_fields=[
                "questions_required_per_faculty", "questions_required_per_faculty_source",
                "final_item_count", "final_item_count_source",
                "cycle_defaults_revision_snapshot", "revision",
            ])


def reverse_cao_defaults(apps, schema_editor):
    Cycle = apps.get_model("departmental_exams", "ExaminationCycle")
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    # Preserve effective values while intentionally collapsing hybrid
    # provenance into legacy per-course semantics.
    Cycle.objects.all().update(legacy_item_count_mode="PER_COURSE", default_final_item_count=None)
    Configuration.objects.all().update(legacy_item_count_mode_snapshot="PER_COURSE")


class Migration(migrations.Migration):
    dependencies = [("departmental_exams", "0002_stage4_course_configuration")]

    operations = [
        migrations.RunPython(preflight_immutable_counts, noop_reverse_preflight),
        migrations.RemoveConstraint(model_name="examinationcycle", name="ck_de_cycle_item_count_mode"),
        migrations.RenameField(model_name="examinationcycle", old_name="fixed_final_item_count", new_name="default_final_item_count"),
        migrations.RenameField(model_name="examinationcycle", old_name="item_count_mode", new_name="legacy_item_count_mode"),
        migrations.AddField(model_name="examinationcycle", name="default_questions_required_per_faculty", field=models.PositiveSmallIntegerField(blank=True, default=None, null=True)),
        migrations.AddField(model_name="examinationcycle", name="defaults_revision", field=models.PositiveIntegerField(default=0)),
        migrations.RenameField(model_name="courseexamconfiguration", old_name="item_count_mode_snapshot", new_name="legacy_item_count_mode_snapshot"),
        migrations.AddField(model_name="courseexamconfiguration", name="questions_required_per_faculty_source", field=models.CharField(blank=True, choices=[("DEFAULT", "Cycle default"), ("OVERRIDE", "Course override")], max_length=8, null=True)),
        migrations.AddField(model_name="courseexamconfiguration", name="final_item_count_source", field=models.CharField(blank=True, choices=[("DEFAULT", "Cycle default"), ("OVERRIDE", "Course override")], max_length=8, null=True)),
        migrations.AddField(model_name="courseexamconfiguration", name="cycle_defaults_revision_snapshot", field=models.PositiveIntegerField(blank=True, null=True)),
        migrations.RunPython(backfill_cao_defaults, reverse_cao_defaults),
        migrations.AddConstraint(model_name="examinationcycle", constraint=models.CheckConstraint(condition=models.Q(default_questions_required_per_faculty__isnull=True) | models.Q(default_questions_required_per_faculty__gte=50, default_questions_required_per_faculty__lte=75), name="ck_de_cycle_default_q_50_75")),
        migrations.AddConstraint(model_name="examinationcycle", constraint=models.CheckConstraint(condition=models.Q(default_final_item_count__isnull=True) | models.Q(default_final_item_count__gte=50, default_final_item_count__lte=75), name="ck_de_cycle_default_final_50_75")),
        migrations.AddConstraint(model_name="courseexamconfiguration", constraint=models.CheckConstraint(condition=models.Q(questions_required_per_faculty__isnull=True, questions_required_per_faculty_source__isnull=True) | models.Q(questions_required_per_faculty__isnull=False, questions_required_per_faculty_source__isnull=False, questions_required_per_faculty__gte=50, questions_required_per_faculty__lte=75, questions_required_per_faculty_source__in=["DEFAULT", "OVERRIDE"]), name="ck_de_cfg_q_value_source")),
        migrations.AddConstraint(model_name="courseexamconfiguration", constraint=models.CheckConstraint(condition=models.Q(final_item_count__isnull=True, final_item_count_source__isnull=True) | models.Q(final_item_count__isnull=False, final_item_count_source__isnull=False, final_item_count__gte=50, final_item_count__lte=75, final_item_count_source__in=["DEFAULT", "OVERRIDE"]), name="ck_de_cfg_final_value_source")),
    ]
