import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("departmental_exams", "0008_stage6_blueprint_constraints"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExamGenerationRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("revision_number", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("GENERATED", "Generated"), ("SUPERSEDED", "Superseded")], default="GENERATED", max_length=12)),
                ("current_marker", models.PositiveSmallIntegerField(blank=True, default=1, null=True)),
                ("source_input_fingerprint", models.CharField(max_length=64)),
                ("algorithm_version", models.CharField(max_length=64)),
                ("generated_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("configuration_revision_snapshot", models.PositiveIntegerField()),
                ("blueprint_revision_snapshot", models.PositiveIntegerField()),
                ("roster_boundary_snapshot", models.CharField(max_length=64)),
                ("final_item_count_snapshot", models.PositiveSmallIntegerField()),
                ("request_token_digest", models.CharField(max_length=64)),
                ("regeneration_reason", models.TextField(blank=True, max_length=500)),
                ("minimum_overlap", models.PositiveSmallIntegerField()),
                ("proportional_score", models.PositiveBigIntegerField()),
                ("contributors_represented", models.PositiveSmallIntegerField()),
                ("squared_contributor_concentration", models.PositiveIntegerField()),
                ("cycle_course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="generation_revisions", to="departmental_exams.cyclecourse")),
                ("generated_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="generated_departmental_exams", to=settings.AUTH_USER_MODEL)),
                ("supersedes", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="superseded_by", to="departmental_exams.examgenerationrevision")),
            ],
            options={
                "db_table": "departmental_exam_generation_revisions",
                "indexes": [
                    models.Index(fields=["cycle_course", "-revision_number"], name="idx_de_gen_course_revision"),
                    models.Index(fields=["source_input_fingerprint"], name="idx_de_gen_fingerprint"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("cycle_course", "revision_number"), name="uq_de_gen_course_revision"),
                    models.UniqueConstraint(fields=("cycle_course", "current_marker"), name="uq_de_gen_course_current"),
                    models.UniqueConstraint(fields=("cycle_course", "request_token_digest"), name="uq_de_gen_course_token"),
                    models.CheckConstraint(condition=models.Q(("blueprint_revision_snapshot__gte", 1), ("configuration_revision_snapshot__gte", 1), ("final_item_count_snapshot__gte", 1), ("revision_number__gte", 1)), name="ck_de_gen_positive_values"),
                    models.CheckConstraint(condition=models.Q(models.Q(("current_marker", 1), ("status", "GENERATED")), models.Q(("current_marker__isnull", True), ("status", "SUPERSEDED")), _connector="OR"), name="ck_de_gen_current_status"),
                ],
            },
        ),
        migrations.CreateModel(
            name="GeneratedExamSet",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("set_code", models.CharField(choices=[("A", "Set A"), ("B", "Set B")], max_length=1)),
                ("campus_quotas_snapshot", models.JSONField(default=dict)),
                ("difficulty_quotas_snapshot", models.JSONField(default=dict)),
                ("section_quotas_snapshot", models.JSONField(default=dict)),
                ("item_count", models.PositiveSmallIntegerField()),
                ("generation_revision", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="generated_sets", to="departmental_exams.examgenerationrevision")),
            ],
            options={
                "db_table": "departmental_exam_generated_sets",
                "indexes": [models.Index(fields=["generation_revision", "set_code"], name="idx_de_gen_set_code")],
                "constraints": [
                    models.UniqueConstraint(fields=("generation_revision", "set_code"), name="uq_de_gen_set_code"),
                    models.CheckConstraint(condition=models.Q(("item_count__gte", 1), ("set_code__in", ["A", "B"])), name="ck_de_gen_set_values"),
                ],
            },
        ),
        migrations.CreateModel(
            name="GeneratedExamItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveSmallIntegerField()),
                ("source_question_revision", models.PositiveIntegerField()),
                ("source_question_digest", models.CharField(max_length=64)),
                ("source_contributor_id_snapshot", models.PositiveBigIntegerField()),
                ("source_contributor_name_snapshot", models.CharField(max_length=255)),
                ("campus_code_snapshot", models.CharField(max_length=30)),
                ("campus_name_snapshot", models.CharField(max_length=120)),
                ("difficulty_snapshot", models.CharField(choices=[("EASY", "Easy"), ("MODERATE", "Moderate"), ("DIFFICULT", "Difficult")], max_length=10)),
                ("section_id_snapshot", models.PositiveBigIntegerField(blank=True, null=True)),
                ("section_title_snapshot", models.CharField(max_length=200)),
                ("section_instructions_snapshot", models.TextField(blank=True, max_length=2000)),
                ("question_text_snapshot", models.TextField(max_length=5000)),
                ("choices_snapshot", models.JSONField(default=list)),
                ("correct_answer_snapshot", models.CharField(choices=[("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")], max_length=1)),
                ("scenario_id_snapshot", models.PositiveBigIntegerField(blank=True, null=True)),
                ("scenario_revision_snapshot", models.PositiveIntegerField(blank=True, null=True)),
                ("scenario_title_snapshot", models.CharField(blank=True, max_length=200)),
                ("scenario_stimulus_snapshot", models.TextField(blank=True, max_length=5000)),
                ("scenario_member_position_snapshot", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("generated_set", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="items", to="departmental_exams.generatedexamset")),
                ("source_campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="generated_exam_item_snapshots", to="tenants.campus")),
                ("source_contributor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="generated_exam_item_snapshots", to=settings.AUTH_USER_MODEL)),
                ("source_question", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="generated_exam_items", to="departmental_exams.question")),
                ("source_scenario", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="generated_exam_item_snapshots", to="departmental_exams.examscenario")),
                ("source_section", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="generated_exam_item_snapshots", to="departmental_exams.examsection")),
            ],
            options={
                "db_table": "departmental_exam_generated_items",
                "indexes": [
                    models.Index(fields=["generated_set", "position"], name="idx_de_gen_item_position"),
                    models.Index(fields=["source_question"], name="idx_de_gen_item_source"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("generated_set", "position"), name="uq_de_gen_item_position"),
                    models.UniqueConstraint(fields=("generated_set", "source_question"), name="uq_de_gen_item_source"),
                    models.CheckConstraint(condition=models.Q(("correct_answer_snapshot__in", ["A", "B", "C", "D"]), ("difficulty_snapshot__in", ["EASY", "MODERATE", "DIFFICULT"]), ("position__gte", 1), ("source_question_revision__gte", 1)), name="ck_de_gen_item_values"),
                    models.CheckConstraint(condition=models.Q(models.Q(("scenario_id_snapshot__isnull", True), ("scenario_member_position_snapshot__isnull", True), ("scenario_revision_snapshot__isnull", True), ("scenario_stimulus_snapshot", ""), ("scenario_title_snapshot", ""), ("source_scenario__isnull", True)), models.Q(("scenario_id_snapshot__isnull", False), ("scenario_member_position_snapshot__gte", 1), ("scenario_revision_snapshot__gte", 1), ("source_scenario__isnull", False)), _connector="OR"), name="ck_de_gen_item_scenario"),
                ],
            },
        ),
    ]
