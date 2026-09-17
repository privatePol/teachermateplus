from django.db import migrations, models
import django.db.models.deletion


def preserve_correction_history(apps, schema_editor):
    Contribution = apps.get_model("departmental_exams", "FacultyContribution")
    Configuration = apps.get_model("departmental_exams", "CourseExamConfiguration")
    Scenario = apps.get_model("departmental_exams", "ExamScenario")
    Member = apps.get_model("departmental_exams", "ExamScenarioMember")
    alias = schema_editor.connection.alias
    if (Contribution.objects.using(alias).filter(active_marker__isnull=True).exists()
            or Contribution.objects.using(alias).filter(supersedes__isnull=False).exists()
            or Configuration.objects.using(alias).filter(closed_cycle_correction_active=True).exists()
            or Scenario.objects.using(alias).filter(active_marker__isnull=True).exists()
            or Scenario.objects.using(alias).filter(supersedes__isnull=False).exists()
            or Member.objects.using(alias).filter(active_marker__isnull=True).exists()):
        raise RuntimeError("Correction history exists; reversing 0032 would lose its current-version boundary.")


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0031_question_rich_content"),
    ]

    operations = [
        migrations.AddField(
            model_name="courseexamconfiguration",
            name="closed_cycle_correction_active",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="facultycontribution",
            name="active_marker",
            field=models.PositiveSmallIntegerField(blank=True, default=1, null=True),
        ),
        migrations.AddField(
            model_name="facultycontribution",
            name="supersedes",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="correction_successor", to="departmental_exams.facultycontribution"),
        ),
        migrations.AddConstraint(
            model_name="facultycontribution",
            constraint=models.UniqueConstraint(fields=("cycle_course", "faculty_user", "active_marker"), name="uq_de_contribution_current"),
        ),
        migrations.RemoveConstraint(
            model_name="facultycontribution",
            name="uq_de_contribution_faculty_course",
        ),
        migrations.AddConstraint(
            model_name="facultycontribution",
            constraint=models.CheckConstraint(condition=models.Q(active_marker=1) | models.Q(active_marker__isnull=True), name="ck_de_contribution_active_marker"),
        ),
        migrations.AddIndex(
            model_name="facultycontribution",
            index=models.Index(fields=["cycle_course", "active_marker", "status"], name="idx_de_contrib_current"),
        ),
        migrations.AddField(
            model_name="examscenario", name="active_marker",
            field=models.PositiveSmallIntegerField(blank=True, default=1, null=True),
        ),
        migrations.AddField(
            model_name="examscenario", name="supersedes",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="correction_successor", to="departmental_exams.examscenario"),
        ),
        migrations.AddConstraint(
            model_name="examscenario",
            constraint=models.CheckConstraint(condition=models.Q(active_marker=1) | models.Q(active_marker__isnull=True), name="ck_de_scenario_active_marker"),
        ),
        migrations.AddField(
            model_name="examscenariomember", name="active_marker",
            field=models.PositiveSmallIntegerField(blank=True, default=1, null=True),
        ),
        migrations.AddConstraint(
            model_name="examscenariomember",
            constraint=models.UniqueConstraint(fields=("question", "active_marker"), name="uq_de_current_case_question"),
        ),
        migrations.AlterField(
            model_name="examscenariomember", name="question",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="exam_scenario_memberships", to="departmental_exams.question"),
        ),
        migrations.AddConstraint(
            model_name="examscenariomember",
            constraint=models.CheckConstraint(condition=models.Q(active_marker=1) | models.Q(active_marker__isnull=True), name="ck_de_member_active_marker"),
        ),
        migrations.RunPython(migrations.RunPython.noop, preserve_correction_history),
    ]
