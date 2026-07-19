from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("academics", "0010_facultyassignmentreplacementlog"),
        ("grading", "0033_alter_coursetemplateassignment_grading_template"),
        ("students", "0004_student_idx_students_scope_status"),
        ("tenants", "0005_enable_existing_sis_api_feature"),
    ]

    operations = [
        migrations.CreateModel(
            name="AcademicInterventionCase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("identified_at", models.DateTimeField()),
                ("detection_source", models.CharField(choices=[("ANALYTICS", "Academic analytics"), ("MANUAL", "Faculty identified")], max_length=16)),
                ("detection_code", models.CharField(blank=True, max_length=64)),
                ("analytics_source_fingerprint", models.CharField(blank=True, max_length=64)),
                ("concern_snapshot_json", models.JSONField(blank=True, default=dict)),
                ("distinct_concern_summary", models.CharField(blank=True, max_length=500)),
                ("faculty_decision", models.CharField(blank=True, choices=[("CONDUCT", "Conduct Intervention"), ("MONITOR", "Continue Monitoring"), ("NO_INTERVENTION", "No Intervention Needed"), ("ALREADY_ADDRESSED", "Already Addressed"), ("INSUFFICIENT_DATA", "Insufficient Grading Data"), ("REFERRED", "Referred to Another Office")], max_length=24)),
                ("faculty_rationale", models.TextField(blank=True)),
                ("decision_at", models.DateTimeField(blank=True, null=True)),
                ("review_status", models.CharField(choices=[("PENDING_REVIEW", "Pending Review"), ("AWAITING_DATA", "Awaiting Data"), ("MONITORING", "Monitoring"), ("INTERVENTION_PLANNED", "Intervention Planned"), ("INTERVENTION_CONDUCTED", "Intervention Conducted"), ("NO_INTERVENTION", "No Intervention"), ("REFERRED", "Referred"), ("CLOSED", "Closed"), ("VOIDED", "Voided")], default="PENDING_REVIEW", max_length=32)),
                ("referral_destination", models.CharField(blank=True, max_length=120)),
                ("referral_date", models.DateField(blank=True, null=True)),
                ("referral_reason", models.CharField(blank=True, max_length=500)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("voided_at", models.DateTimeField(blank=True, null=True)),
                ("void_reason", models.TextField(blank=True)),
                ("academic_year", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="academic_intervention_cases", to="academics.academicyear")),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="academic_intervention_cases", to="tenants.campus")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_academic_intervention_cases", to=settings.AUTH_USER_MODEL)),
                ("faculty_owner", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="owned_academic_intervention_cases", to=settings.AUTH_USER_MODEL)),
                ("grading_period", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="academic_intervention_cases", to="grading.gradingtemplateperiod")),
                ("offering", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="academic_intervention_cases", to="academics.courseoffering")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="academic_intervention_cases", to="students.student")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="academic_intervention_cases", to="tenants.tenant")),
                ("term", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="academic_intervention_cases", to="academics.term")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_academic_intervention_cases", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academic_intervention_cases", "ordering": ["-identified_at", "-id"]},
        ),
        migrations.CreateModel(
            name="AcademicInterventionAction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("intervention_type", models.CharField(max_length=120)),
                ("status", models.CharField(choices=[("PLANNED", "Planned"), ("CONDUCTED", "Conducted"), ("CANCELLED", "Cancelled")], default="PLANNED", max_length=16)),
                ("planned_for", models.DateField(blank=True, null=True)),
                ("conducted_on", models.DateField(blank=True, null=True)),
                ("action_summary", models.TextField()),
                ("student_action_plan", models.TextField(blank=True)),
                ("cancellation_reason", models.TextField(blank=True)),
                ("case", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="actions", to="interventions.academicinterventioncase")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_academic_intervention_actions", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_academic_intervention_actions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academic_intervention_actions", "ordering": ["-conducted_on", "-planned_for", "-id"]},
        ),
        migrations.CreateModel(
            name="AcademicInterventionFollowUp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("due_on", models.DateField(blank=True, null=True)),
                ("status", models.CharField(choices=[("NOT_REQUIRED", "Not Required"), ("SCHEDULED", "Scheduled"), ("COMPLETED", "Completed"), ("STUDENT_UNRESPONSIVE", "Student Unresponsive"), ("FURTHER_SUPPORT_NEEDED", "Further Support Needed")], default="SCHEDULED", max_length=32)),
                ("result_summary", models.TextField(blank=True)),
                ("completed_on", models.DateField(blank=True, null=True)),
                ("action", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="follow_ups", to="interventions.academicinterventionaction")),
                ("case", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="follow_ups", to="interventions.academicinterventioncase")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_academic_intervention_followups", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_academic_intervention_followups", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "academic_intervention_followups", "ordering": ["due_on", "id"]},
        ),
        migrations.AddIndex(model_name="academicinterventioncase", index=models.Index(fields=["tenant", "campus", "academic_year", "term", "review_status"], name="idx_aic_scope_term_status")),
        migrations.AddIndex(model_name="academicinterventioncase", index=models.Index(fields=["faculty_owner", "review_status", "updated_at"], name="idx_aic_owner_status_upd")),
        migrations.AddIndex(model_name="academicinterventioncase", index=models.Index(fields=["offering", "student", "grading_period"], name="idx_aic_offer_student_period")),
        migrations.AddConstraint(model_name="academicinterventioncase", constraint=models.UniqueConstraint(condition=Q(("detection_source", "ANALYTICS"), ("voided_at__isnull", True)), fields=("faculty_owner", "offering", "student", "grading_period", "analytics_source_fingerprint"), name="uq_aic_owner_active_analytics")),
        migrations.AddIndex(model_name="academicinterventionaction", index=models.Index(fields=["case", "status", "conducted_on"], name="idx_aia_case_status_date")),
        migrations.AddIndex(model_name="academicinterventionfollowup", index=models.Index(fields=["case", "status", "due_on"], name="idx_aif_case_status_due")),
    ]
