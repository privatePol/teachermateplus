# Generated manually for the approved additive daily-encoding and publication gate.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("faculty_attendance", "0009_consolidate_attendance_navigation"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduleversion",
            name="source_kind",
            field=models.CharField(default="MANUAL", max_length=16),
        ),
        migrations.AddField(
            model_name="scheduleversion",
            name="source_fingerprint",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddConstraint(
            model_name="scheduleversion",
            constraint=models.UniqueConstraint(
                fields=("offering", "source_fingerprint", "effective_from"),
                name="uq_att_sched_source_fingerprint",
            ),
        ),
        migrations.AddField(
            model_name="teachingmeeting",
            name="source_kind",
            field=models.CharField(default="MANUAL", max_length=16),
        ),
        migrations.AddField(
            model_name="teachingmeeting",
            name="occurrence_key",
            field=models.CharField(blank=True, max_length=180, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="checkinground",
            name="academic_year",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attendance_checking_rounds",
                to="academics.academicyear",
            ),
        ),
        migrations.AddField(
            model_name="checkinground",
            name="term",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="attendance_checking_rounds",
                to="academics.term",
            ),
        ),
        migrations.AddField(
            model_name="checkinground",
            name="daily_occurrence_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="checkinground",
            index=models.Index(
                fields=["tenant", "campus", "academic_year", "term", "daily_occurrence_date"],
                name="idx_att_round_daily_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="checkinground",
            constraint=models.UniqueConstraint(
                fields=("tenant", "campus", "department", "academic_year", "term", "daily_occurrence_date"),
                name="uq_att_daily_round_scope",
            ),
        ),
        migrations.CreateModel(
            name="OfferingAttendanceSourceChange",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("old_schedule_text", models.CharField(blank=True, max_length=255)),
                ("new_schedule_text", models.CharField(blank=True, max_length=255)),
                ("old_room", models.CharField(blank=True, max_length=80)),
                ("new_room", models.CharField(blank=True, max_length=80)),
                ("effective_from", models.DateField(blank=True, null=True)),
                ("status", models.CharField(choices=[("PENDING", "Pending"), ("RESOLVED", "Resolved")], default="PENDING", max_length=12)),
                ("reason", models.TextField()),
                ("source_reference", models.CharField(max_length=96, unique=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("resolution_reason", models.TextField(blank=True)),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_source_changes", to="tenants.campus")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="created_attendance_source_changes", to=settings.AUTH_USER_MODEL)),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_source_changes", to="tenants.department")),
                ("offering", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_source_changes", to="academics.courseoffering")),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="resolved_attendance_source_changes", to=settings.AUTH_USER_MODEL)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_source_changes", to="tenants.tenant")),
            ],
            options={"db_table": "faculty_attendance_source_changes", "ordering": ["status", "offering_id", "created_at"]},
        ),
        migrations.AddIndex(
            model_name="offeringattendancesourcechange",
            index=models.Index(fields=["tenant", "campus", "department", "status"], name="idx_att_source_change_scope"),
        ),
        migrations.AddIndex(
            model_name="offeringattendancesourcechange",
            index=models.Index(fields=["offering", "status", "effective_from"], name="idx_att_source_change_offer"),
        ),
        migrations.CreateModel(
            name="AttendanceCutoffPublication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("lineage_key", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("version", models.PositiveIntegerField(default=1)),
                ("review_fingerprint", models.CharField(max_length=64)),
                ("submission_key", models.CharField(max_length=64, unique=True)),
                ("published_at", models.DateTimeField()),
                ("publication_reason", models.TextField()),
                ("academic_year", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_cutoff_publications", to="academics.academicyear")),
                ("campus", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_cutoff_publications", to="tenants.campus")),
                ("published_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="published_attendance_cutoffs", to=settings.AUTH_USER_MODEL)),
                ("supersedes", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="revisions", to="faculty_attendance.attendancecutoffpublication")),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_cutoff_publications", to="tenants.tenant")),
                ("term", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_cutoff_publications", to="academics.term")),
            ],
            options={"db_table": "faculty_attendance_cutoff_publications", "ordering": ["-published_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="attendancecutoffpublication",
            constraint=models.UniqueConstraint(fields=("tenant", "campus", "academic_year", "term", "start_date", "end_date", "version"), name="uq_att_cutoff_scope_version"),
        ),
        migrations.AddConstraint(
            model_name="attendancecutoffpublication",
            constraint=models.CheckConstraint(condition=Q(("end_date__gte", models.F("start_date"))), name="ck_att_cutoff_dates"),
        ),
        migrations.AddIndex(
            model_name="attendancecutoffpublication",
            index=models.Index(fields=["tenant", "campus", "academic_year", "term", "start_date", "end_date"], name="idx_att_cutoff_scope"),
        ),
        migrations.AddIndex(
            model_name="attendancecutoffpublication",
            index=models.Index(fields=["lineage_key", "version"], name="idx_att_cutoff_lineage"),
        ),
        migrations.CreateModel(
            name="AttendanceCutoffPublicationEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("occurrence_key", models.CharField(max_length=180)),
                ("meeting_date", models.DateField()),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField()),
                ("scheduled_minutes", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("UNVERIFIED", "Unverified"), ("PRESENT", "Present"), ("EXCEPTION", "Exception")], max_length=12)),
                ("late_flag", models.BooleanField(default=False)),
                ("late_minutes", models.PositiveIntegerField(default=0)),
                ("early_flag", models.BooleanField(default=False)),
                ("early_minutes", models.PositiveIntegerField(default=0)),
                ("absent_without_notice_hours", models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ("absent_with_notice_hours", models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ("missed_periods", models.DecimalField(decimal_places=2, default=0, max_digits=7)),
                ("meeting_snapshot", models.JSONField(default=dict)),
                ("findings_snapshot", models.JSONField(blank=True, default=list)),
                ("result_revision_number", models.PositiveIntegerField(default=0)),
                ("faculty_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="published_attendance_entries", to=settings.AUTH_USER_MODEL)),
                ("meeting", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cutoff_publication_entries", to="faculty_attendance.teachingmeeting")),
                ("publication", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="entries", to="faculty_attendance.attendancecutoffpublication")),
                ("result_revision", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cutoff_publication_entries", to="faculty_attendance.attendanceresultrevision")),
            ],
            options={"db_table": "faculty_attendance_cutoff_publication_entries", "ordering": ["meeting_date", "starts_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="attendancecutoffpublicationentry",
            constraint=models.UniqueConstraint(fields=("publication", "occurrence_key"), name="uq_att_cutoff_entry_occurrence"),
        ),
        migrations.AddIndex(
            model_name="attendancecutoffpublicationentry",
            index=models.Index(fields=["faculty_user", "meeting_date"], name="idx_att_cutoff_entry_faculty"),
        ),
        migrations.AddIndex(
            model_name="attendancecutoffpublicationentry",
            index=models.Index(fields=["publication", "meeting_date"], name="idx_att_cutoff_entry_date"),
        ),
    ]
