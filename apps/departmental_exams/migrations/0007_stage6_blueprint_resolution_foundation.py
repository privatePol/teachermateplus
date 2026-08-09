import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0006_stage5_backfill_constraints"),
        ("tenants", "0005_enable_existing_sis_api_feature"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExamBlueprint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mode", models.CharField(choices=[("NO_SECTIONS", "No Sections"), ("USE_SECTIONS", "Use Sections")], default="NO_SECTIONS", max_length=12)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_exam_blueprints", to=settings.AUTH_USER_MODEL)),
                ("cycle_course", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="exam_blueprint", to="departmental_exams.cyclecourse")),
                ("updated_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="updated_exam_blueprints", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "departmental_exam_blueprints"},
        ),
        migrations.CreateModel(
            name="ExamScenario",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(blank=True, max_length=200)),
                ("stimulus", models.TextField(max_length=5000)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("blueprint", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="scenarios", to="departmental_exams.examblueprint")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_exam_scenarios", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="updated_exam_scenarios", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "departmental_exam_scenarios"},
        ),
        migrations.CreateModel(
            name="ExamScenarioMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveSmallIntegerField()),
                ("question", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="exam_scenario_membership", to="departmental_exams.question")),
                ("scenario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="members", to="departmental_exams.examscenario")),
            ],
            options={"db_table": "departmental_exam_scenario_members"},
        ),
        migrations.CreateModel(
            name="ExamSection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=200)),
                ("instructions", models.TextField(blank=True, max_length=2000)),
                ("display_order", models.PositiveSmallIntegerField()),
                ("item_quota", models.PositiveSmallIntegerField()),
                ("blueprint", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sections", to="departmental_exams.examblueprint")),
            ],
            options={"db_table": "departmental_exam_sections"},
        ),
        migrations.AddField(
            model_name="examscenario",
            name="section",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="scenarios", to="departmental_exams.examsection"),
        ),
        migrations.CreateModel(
            name="QuestionBlueprintPlacement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("blueprint", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="question_placements", to="departmental_exams.examblueprint")),
                ("placed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="exam_question_placements", to=settings.AUTH_USER_MODEL)),
                ("question", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="blueprint_placement", to="departmental_exams.question")),
                ("section", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="question_placements", to="departmental_exams.examsection")),
            ],
            options={"db_table": "departmental_exam_question_placements"},
        ),
        migrations.CreateModel(
            name="BlockedContributionResolution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reason", models.TextField(max_length=500)),
                ("resolved_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("contribution_revision_snapshot", models.PositiveIntegerField()),
                ("roster_revision_snapshot", models.PositiveIntegerField()),
                ("blocked_at_snapshot", models.DateTimeField()),
                ("source_evidence_sha256", models.CharField(max_length=64)),
                ("contribution", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="blocked_resolution_events", to="departmental_exams.facultycontribution")),
                ("cycle_course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="blocked_contribution_resolutions", to="departmental_exams.cyclecourse")),
                ("resolved_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="resolved_blocked_exam_contributions", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="blocked_exam_contribution_resolutions", to="tenants.tenant")),
            ],
            options={"db_table": "departmental_exam_blocked_resolutions"},
        ),
    ]
