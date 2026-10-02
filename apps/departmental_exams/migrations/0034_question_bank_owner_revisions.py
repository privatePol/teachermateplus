import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("academics", "0011_course_exam_department"),
        ("departmental_exams", "0033_questionnaire_campus_release"),
        ("tenants", "0005_enable_existing_sis_api_feature"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QuestionBankItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("kind", models.CharField(choices=[("QUESTION", "Standalone question"), ("CASE", "Whole Case")], max_length=12)),
                ("current_revision", models.PositiveIntegerField(default=1)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="question_bank_items", to="tenants.campus")),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="question_bank_items", to="academics.course")),
                ("origin_question", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="adopted_bank_item", to="departmental_exams.question")),
                ("origin_scenario", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="adopted_bank_item", to="departmental_exams.examscenario")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="owned_question_bank_items", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="question_bank_items", to="tenants.tenant")),
            ],
            options={"db_table": "departmental_exam_question_bank_items"},
        ),
        migrations.CreateModel(
            name="QuestionBankRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("revision", models.PositiveIntegerField()),
                ("title", models.CharField(blank=True, max_length=200)),
                ("stimulus", models.TextField(blank=True, max_length=50000)),
                ("scenario_content_format", models.CharField(choices=[("PLAIN_TEXT", "Plain text"), ("RICH_HTML_V1", "Rich HTML V1")], default="PLAIN_TEXT", max_length=16)),
                ("content_format", models.CharField(choices=[("PLAIN_TEXT", "Plain text"), ("RICH_HTML_V1", "Rich HTML V1")], default="PLAIN_TEXT", max_length=20)),
                ("question_text", models.TextField(blank=True, max_length=25000)),
                ("choice_a", models.TextField(blank=True, max_length=12000)),
                ("choice_b", models.TextField(blank=True, max_length=12000)),
                ("choice_c", models.TextField(blank=True, max_length=12000)),
                ("choice_d", models.TextField(blank=True, max_length=12000)),
                ("correct_answer", models.CharField(blank=True, choices=[("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")], max_length=1)),
                ("difficulty", models.CharField(blank=True, choices=[("EASY", "Easy"), ("MODERATE", "Moderate"), ("DIFFICULT", "Difficult")], max_length=10)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="question_bank_revisions", to=settings.AUTH_USER_MODEL)),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="revisions", to="departmental_exams.questionbankitem")),
            ],
            options={"db_table": "departmental_exam_question_bank_revisions"},
        ),
        migrations.CreateModel(
            name="QuestionBankCaseMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveSmallIntegerField()),
                ("content_format", models.CharField(choices=[("PLAIN_TEXT", "Plain text"), ("RICH_HTML_V1", "Rich HTML V1")], default="PLAIN_TEXT", max_length=20)),
                ("question_text", models.TextField(max_length=25000)),
                ("choice_a", models.TextField(max_length=12000)),
                ("choice_b", models.TextField(max_length=12000)),
                ("choice_c", models.TextField(max_length=12000)),
                ("choice_d", models.TextField(max_length=12000)),
                ("correct_answer", models.CharField(choices=[("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")], max_length=1)),
                ("difficulty", models.CharField(choices=[("EASY", "Easy"), ("MODERATE", "Moderate"), ("DIFFICULT", "Difficult")], max_length=10)),
                ("revision", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="members", to="departmental_exams.questionbankrevision")),
            ],
            options={"db_table": "departmental_exam_question_bank_case_members"},
        ),
        migrations.AddField(
            model_name="examscenario", name="source_bank_revision",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="draft_case_copies", to="departmental_exams.questionbankrevision"),
        ),
        migrations.AddField(
            model_name="question", name="source_bank_revision",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="draft_copies", to="departmental_exams.questionbankrevision"),
        ),
        migrations.AddIndex(model_name="questionbankitem", index=models.Index(fields=["owner", "tenant", "campus", "course", "kind"], name="idx_de_bank_owner_scope")),
        migrations.AddConstraint(model_name="questionbankitem", constraint=models.CheckConstraint(condition=models.Q(current_revision__gte=1), name="ck_de_bank_item_revision")),
        migrations.AddConstraint(model_name="questionbankitem", constraint=models.CheckConstraint(condition=models.Q(kind="QUESTION", origin_scenario__isnull=True) | models.Q(kind="CASE", origin_question__isnull=True), name="ck_de_bank_item_origin_kind")),
        migrations.AddIndex(model_name="questionbankrevision", index=models.Index(fields=["item", "revision"], name="idx_de_bank_revision")),
        migrations.AddConstraint(model_name="questionbankrevision", constraint=models.UniqueConstraint(fields=("item", "revision"), name="uq_de_bank_item_revision")),
        migrations.AddConstraint(model_name="questionbankrevision", constraint=models.CheckConstraint(condition=models.Q(revision__gte=1), name="ck_de_bank_revision_number")),
        migrations.AddIndex(model_name="questionbankcasemember", index=models.Index(fields=["revision", "position"], name="idx_de_bank_case_order")),
        migrations.AddConstraint(model_name="questionbankcasemember", constraint=models.UniqueConstraint(fields=("revision", "position"), name="uq_de_bank_case_position")),
        migrations.AddConstraint(model_name="questionbankcasemember", constraint=models.CheckConstraint(condition=models.Q(position__gte=1), name="ck_de_bank_case_position")),
    ]
