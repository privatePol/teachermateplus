import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("academics", "0011_course_exam_department"),
        ("tenants", "0005_enable_existing_sis_api_feature"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExaminationCycle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                ("exam_period", models.CharField(choices=[("MIDTERM", "Midterm"), ("FINAL", "Final")], max_length=10)),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("OPEN", "Open"), ("CLOSED", "Closed")], default="DRAFT", max_length=10)),
                ("academic_year", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="examination_cycles", to="academics.academicyear")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_examination_cycles", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="examination_cycles", to="tenants.tenant")),
                ("term", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="examination_cycles", to="academics.term")),
            ],
            options={
                "db_table": "departmental_exam_cycles",
                "constraints": [models.UniqueConstraint(fields=("tenant", "academic_year", "term", "exam_period"), name="uq_de_cycle_scope_period")],
                "indexes": [models.Index(fields=["tenant", "term", "status"], name="idx_de_cycle_scope_status")],
            },
        ),
        migrations.CreateModel(
            name="CycleCourse",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("inclusion_status", models.CharField(choices=[("INCLUDED", "Included"), ("EXEMPT", "Exempt")], default="INCLUDED", max_length=10)),
                ("exemption_category", models.CharField(blank=True, choices=[("PRACTICUM_OJT", "Practicum / OJT"), ("INTERNSHIP", "Internship"), ("THESIS_RESEARCH", "Thesis and Research Writing"), ("CAPSTONE", "Capstone Project"), ("LABORATORY_PRACTICAL", "Laboratory / Practical"), ("PORTFOLIO_BASED", "Portfolio-based"), ("PERFORMANCE_BASED", "Performance-based"), ("OTHER_OUTPUT_BASED", "Other output-based")], max_length=30)),
                ("exemption_reason", models.TextField(blank=True)),
                ("exemption_changed_at", models.DateTimeField(blank=True, null=True)),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="exam_cycle_courses", to="academics.course")),
                ("cycle", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cycle_courses", to="departmental_exams.examinationcycle")),
                ("responsible_department", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="responsible_exam_cycle_courses", to="tenants.department")),
                ("exemption_changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="changed_exam_exemptions", to=settings.AUTH_USER_MODEL)),
                ("reviewer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reviewer_cycle_courses", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "departmental_exam_cycle_courses",
                "constraints": [models.UniqueConstraint(fields=("cycle", "course"), name="uq_de_cycle_course")],
                "indexes": [models.Index(fields=["cycle", "responsible_department", "inclusion_status"], name="idx_de_cycle_course_status")],
            },
        ),
        migrations.CreateModel(
            name="CourseExamConfiguration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("final_item_count", models.PositiveSmallIntegerField(default=50)),
                ("required_questions_per_faculty", models.PositiveSmallIntegerField(default=1)),
                ("general_instructions", models.TextField(blank=True)),
                ("submission_deadline", models.DateTimeField(blank=True, null=True)),
                ("easy_percent", models.PositiveSmallIntegerField(default=30)),
                ("moderate_percent", models.PositiveSmallIntegerField(default=50)),
                ("difficult_percent", models.PositiveSmallIntegerField(default=20)),
                ("is_published", models.BooleanField(default=False)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("published_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="published_exam_configurations", to=settings.AUTH_USER_MODEL)),
                ("cycle_course", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="configuration", to="departmental_exams.cyclecourse")),
            ],
            options={"db_table": "departmental_exam_configurations"},
        ),
        migrations.CreateModel(
            name="FacultyContribution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("SUBMITTED", "Submitted")], default="DRAFT", max_length=10)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("cycle_course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="faculty_contributions", to="departmental_exams.cyclecourse")),
                ("faculty_user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="exam_contributions", to=settings.AUTH_USER_MODEL)),
                ("source_assignment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="exam_contributions", to="academics.facultyassignment")),
                ("source_campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="exam_contributions", to="tenants.campus")),
            ],
            options={
                "db_table": "departmental_exam_faculty_contributions",
                "constraints": [models.UniqueConstraint(fields=("cycle_course", "faculty_user"), name="uq_de_contribution_faculty_course")],
                "indexes": [models.Index(fields=["faculty_user", "status"], name="idx_de_contrib_user_status")],
            },
        ),
        migrations.CreateModel(
            name="Question",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("question_text", models.TextField()),
                ("choice_a", models.CharField(max_length=1000)),
                ("choice_b", models.CharField(max_length=1000)),
                ("choice_c", models.CharField(max_length=1000)),
                ("choice_d", models.CharField(max_length=1000)),
                ("correct_answer", models.CharField(choices=[("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")], max_length=1)),
                ("difficulty", models.CharField(choices=[("EASY", "Easy"), ("MODERATE", "Moderate"), ("DIFFICULT", "Difficult")], max_length=10)),
                ("contribution", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="questions", to="departmental_exams.facultycontribution")),
            ],
            options={
                "db_table": "departmental_exam_questions",
                "indexes": [models.Index(fields=["contribution", "difficulty"], name="idx_de_q_contrib_difficulty")],
            },
        ),
        migrations.CreateModel(
            name="CycleCourseOffering",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="exam_cycle_offering_snapshots", to="tenants.campus")),
                ("cycle_course", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="offering_snapshots", to="departmental_exams.cyclecourse")),
                ("offering", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="exam_cycle_snapshots", to="academics.courseoffering")),
            ],
            options={
                "db_table": "departmental_exam_cycle_course_offerings",
                "constraints": [models.UniqueConstraint(fields=("cycle_course", "offering"), name="uq_de_cycle_course_offering")],
                "indexes": [models.Index(fields=["cycle_course", "campus"], name="idx_de_cycle_course_campus")],
            },
        ),
    ]
