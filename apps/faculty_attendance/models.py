import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel


def _validate_scope(*, tenant_id, campus, department=None):
    if campus and campus.tenant_id != tenant_id:
        raise ValidationError("Campus must belong to the selected tenant.")
    if department:
        if department.tenant_id != tenant_id:
            raise ValidationError("Department must belong to the selected tenant.")
        if campus and department.campus_id != campus.id:
            raise ValidationError("Department must belong to the selected campus.")


class ScheduleVersion(TimeStampedModel):
    class InterpretationStatus(models.TextChoices):
        CORRECTION_REQUIRED = "CORRECTION_REQUIRED", "Correction required"
        CONFIRMED = "CONFIRMED", "Confirmed"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_schedules")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="attendance_schedules")
    department = models.ForeignKey(
        "tenants.Department", on_delete=models.PROTECT, related_name="attendance_schedules"
    )
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.PROTECT, related_name="attendance_schedule_versions"
    )
    version_number = models.PositiveIntegerField()
    original_text = models.CharField(max_length=255, blank=True)
    source_kind = models.CharField(max_length=16, default="MANUAL")
    source_fingerprint = models.CharField(max_length=64, blank=True, null=True)
    interpretation_status = models.CharField(max_length=24, choices=InterpretationStatus.choices)
    effective_from = models.DateField()
    effective_until = models.DateField(blank=True, null=True)
    correction_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_attendance_schedules"
    )

    class Meta:
        db_table = "faculty_attendance_schedule_versions"
        ordering = ["offering_id", "version_number"]
        constraints = [
            models.UniqueConstraint(fields=["offering", "version_number"], name="uq_att_sched_offer_ver"),
            models.UniqueConstraint(
                fields=["offering", "source_fingerprint", "effective_from"],
                name="uq_att_sched_source_fingerprint",
            ),
            models.CheckConstraint(
                condition=Q(effective_until__isnull=True) | Q(effective_until__gte=models.F("effective_from")),
                name="ck_att_sched_date_range",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "campus", "department", "effective_from"], name="idx_att_sched_scope"),
            models.Index(fields=["offering", "effective_from", "effective_until"], name="idx_att_sched_effective"),
        ]

    def clean(self):
        super().clean()
        _validate_scope(tenant_id=self.tenant_id, campus=self.campus, department=self.department)
        if self.offering_id:
            if (
                self.offering.tenant_id != self.tenant_id
                or self.offering.campus_id != self.campus_id
                or self.offering.department_id != self.department_id
            ):
                raise ValidationError("Offering must match the schedule tenant, campus, and department.")
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValidationError("Schedule effective-until date cannot precede effective-from date.")


class ScheduleSlot(TimeStampedModel):
    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    schedule_version = models.ForeignKey(ScheduleVersion, on_delete=models.PROTECT, related_name="slots")
    sequence = models.PositiveSmallIntegerField()
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    room_text = models.CharField(max_length=80, blank=True)
    building = models.CharField(max_length=120, blank=True)
    floor = models.CharField(max_length=80, blank=True)
    room = models.CharField(max_length=80, blank=True)

    class Meta:
        db_table = "faculty_attendance_schedule_slots"
        ordering = ["schedule_version_id", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["schedule_version", "sequence"], name="uq_att_sched_slot_seq"),
            models.UniqueConstraint(
                fields=["schedule_version", "weekday", "start_time", "end_time"],
                name="uq_att_sched_slot_time",
            ),
            models.CheckConstraint(condition=Q(end_time__gt=models.F("start_time")), name="ck_att_slot_time_order"),
        ]

    def clean(self):
        super().clean()
        if self.end_time <= self.start_time:
            raise ValidationError("Schedule slot end time must be later than start time.")


class FacultyCoverage(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_coverages")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="attendance_coverages")
    department = models.ForeignKey(
        "tenants.Department", on_delete=models.PROTECT, related_name="attendance_coverages"
    )
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.PROTECT, related_name="attendance_coverages"
    )
    faculty_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_coverages"
    )
    source_assignment = models.ForeignKey(
        "academics.FacultyAssignment",
        on_delete=models.PROTECT,
        related_name="attendance_coverages",
        blank=True,
        null=True,
    )
    effective_from = models.DateTimeField()
    effective_until = models.DateTimeField(blank=True, null=True)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_attendance_coverages"
    )

    class Meta:
        db_table = "faculty_attendance_coverages"
        ordering = ["offering_id", "effective_from", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "faculty_user", "effective_from"], name="uq_att_cov_offer_fac_start"
            ),
            models.CheckConstraint(
                condition=Q(effective_until__isnull=True) | Q(effective_until__gt=models.F("effective_from")),
                name="ck_att_cov_time_range",
            ),
        ]
        indexes = [
            models.Index(fields=["offering", "effective_from", "effective_until"], name="idx_att_cov_effective"),
            models.Index(fields=["faculty_user", "effective_from"], name="idx_att_cov_faculty"),
        ]

    def clean(self):
        super().clean()
        _validate_scope(tenant_id=self.tenant_id, campus=self.campus, department=self.department)
        if self.offering_id and (
            self.offering.tenant_id != self.tenant_id
            or self.offering.campus_id != self.campus_id
            or self.offering.department_id != self.department_id
        ):
            raise ValidationError("Offering must match the coverage tenant, campus, and department.")
        if self.source_assignment_id:
            if self.source_assignment.offering_id != self.offering_id:
                raise ValidationError("Source assignment must belong to the covered offering.")
            if self.source_assignment.faculty_user_id != self.faculty_user_id:
                raise ValidationError("Source assignment must belong to the covered faculty member.")
            if (self.source_assignment.tenant_id not in (None, self.tenant_id)
                    or self.source_assignment.campus_id not in (None, self.campus_id)):
                raise ValidationError("Source assignment scope conflicts with its owning offering.")
        if self.effective_until and self.effective_until <= self.effective_from:
            raise ValidationError("Coverage uses a half-open interval and must end after it starts.")


class MeetingCoverageAdoption(TimeStampedModel):
    """One explicit initial attribution; the original meeting/manifest stays frozen."""

    meeting = models.OneToOneField("TeachingMeeting", on_delete=models.PROTECT, related_name="coverage_adoption")
    coverage = models.ForeignKey(FacultyCoverage, on_delete=models.PROTECT, related_name="adoptions")
    faculty_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_coverage_adoptions")
    adopted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="decided_attendance_coverage_adoptions")
    reason = models.TextField(blank=True)
    decision_snapshot = models.JSONField(default=dict)

    class Meta:
        db_table = "faculty_attendance_coverage_adoptions"

    def clean(self):
        super().clean()
        if self.coverage_id and self.meeting_id:
            if (self.coverage.tenant_id != self.meeting.tenant_id
                    or self.coverage.campus_id != self.meeting.campus_id
                    or self.coverage.department_id != self.meeting.department_id
                    or self.coverage.faculty_user_id != self.faculty_user_id
                    or self.coverage.effective_from > self.meeting.starts_at
                    or (self.coverage.effective_until is not None and self.meeting.starts_at >= self.coverage.effective_until)
                    or not self.meeting.offering_links.filter(offering_id=self.coverage.offering_id).exists()):
                raise ValidationError("Adopted coverage must match the meeting and faculty scope.")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Coverage adoption is immutable; use attendance correction revisions.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Coverage adoption history cannot be deleted.")


class TeachingMeeting(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_meetings")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="attendance_meetings")
    department = models.ForeignKey(
        "tenants.Department", on_delete=models.PROTECT, related_name="attendance_meetings"
    )
    schedule_slot = models.ForeignKey(ScheduleSlot, on_delete=models.PROTECT, related_name="meetings")
    source_kind = models.CharField(max_length=16, default="MANUAL")
    occurrence_key = models.CharField(max_length=180, blank=True, null=True, unique=True)
    meeting_date = models.DateField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    scheduled_minutes = models.PositiveIntegerField()
    coverage = models.ForeignKey(
        FacultyCoverage, on_delete=models.PROTECT, related_name="meetings", blank=True, null=True
    )
    faculty_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_meetings",
        blank=True,
        null=True,
    )
    schedule_snapshot = models.JSONField(default=dict)
    faculty_snapshot = models.JSONField(default=dict, blank=True)
    location_snapshot = models.JSONField(default=dict)
    sections_snapshot = models.JSONField(default=list)
    unresolved_coverage = models.BooleanField(default=False)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="generated_attendance_meetings"
    )

    offerings = models.ManyToManyField(
        "academics.CourseOffering", through="MeetingOffering", related_name="attendance_meetings"
    )

    class Meta:
        db_table = "faculty_attendance_meetings"
        ordering = ["meeting_date", "starts_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["schedule_slot", "meeting_date"], name="uq_att_meeting_slot_date"),
            models.CheckConstraint(condition=Q(ends_at__gt=models.F("starts_at")), name="ck_att_meeting_time_order"),
            models.CheckConstraint(condition=Q(scheduled_minutes__gt=0), name="ck_att_meeting_minutes"),
        ]
        indexes = [
            models.Index(fields=["tenant", "campus", "department", "meeting_date"], name="idx_att_meeting_scope"),
            models.Index(fields=["faculty_user", "meeting_date"], name="idx_att_meeting_faculty"),
        ]

    def clean(self):
        super().clean()
        _validate_scope(tenant_id=self.tenant_id, campus=self.campus, department=self.department)
        version = self.schedule_slot.schedule_version if self.schedule_slot_id else None
        if version and (
            version.tenant_id != self.tenant_id
            or version.campus_id != self.campus_id
            or version.department_id != self.department_id
        ):
            raise ValidationError("Schedule slot must match the meeting scope.")
        if self.coverage_id:
            if (
                self.coverage.tenant_id != self.tenant_id
                or self.coverage.campus_id != self.campus_id
                or self.coverage.department_id != self.department_id
            ):
                raise ValidationError("Coverage must match the meeting scope.")
            if self.faculty_user_id != self.coverage.faculty_user_id:
                raise ValidationError("Meeting faculty must match its permanent coverage snapshot.")
        elif self.faculty_user_id:
            raise ValidationError("A permanent faculty snapshot requires a coverage record.")
        if self.unresolved_coverage == bool(self.coverage_id):
            raise ValidationError("Meeting coverage must be either resolved or explicitly unresolved.")
        if self.ends_at <= self.starts_at:
            raise ValidationError("Meeting end must be later than its start.")


class MeetingOffering(TimeStampedModel):
    meeting = models.ForeignKey(TeachingMeeting, on_delete=models.PROTECT, related_name="offering_links")
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.PROTECT, related_name="attendance_meeting_links"
    )
    is_primary = models.BooleanField(default=False)
    course_code_snapshot = models.CharField(max_length=50)
    course_title_snapshot = models.CharField(max_length=200)
    section_code_snapshot = models.CharField(max_length=50)
    section_name_snapshot = models.CharField(max_length=150)

    class Meta:
        db_table = "faculty_attendance_meeting_offerings"
        ordering = ["meeting_id", "-is_primary", "offering_id"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "offering"], name="uq_att_meeting_offering"),
        ]

    def clean(self):
        super().clean()
        if self.meeting_id and self.offering_id and (
            self.offering.tenant_id != self.meeting.tenant_id
            or self.offering.campus_id != self.meeting.campus_id
            or self.offering.department_id != self.meeting.department_id
        ):
            raise ValidationError("Linked offering must match the meeting scope.")


class MeetingSubstitution(TimeStampedModel):
    meeting = models.OneToOneField(TeachingMeeting, on_delete=models.PROTECT, related_name="substitution")
    original_faculty = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_substitutions_as_original",
        blank=True,
        null=True,
    )
    substitute_faculty = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_substitutions"
    )
    reason = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="decided_attendance_substitutions"
    )
    decision_snapshot = models.JSONField(default=dict)

    class Meta:
        db_table = "faculty_attendance_substitutions"

    def clean(self):
        super().clean()
        if self.meeting_id and self.original_faculty_id != self.meeting.faculty_user_id:
            raise ValidationError("Original faculty must match the meeting's permanent faculty snapshot.")
        if self.original_faculty_id and self.original_faculty_id == self.substitute_faculty_id:
            raise ValidationError("Substitute faculty must differ from the original faculty.")


class MeetingReconciliation(TimeStampedModel):
    class SourceType(models.TextChoices):
        SCHEDULE = "SCHEDULE", "Schedule"
        COVERAGE = "COVERAGE", "Coverage"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RESOLVED = "RESOLVED", "Resolved"

    class Decision(models.TextChoices):
        KEEP_SNAPSHOT = "KEEP_SNAPSHOT", "Keep historical snapshot"
        REVISE_FUTURE = "REVISE_FUTURE", "Use change for future processing"
        MANUAL_CORRECTION = "MANUAL_CORRECTION", "Manual correction required"

    meeting = models.ForeignKey(TeachingMeeting, on_delete=models.PROTECT, related_name="reconciliations")
    source_type = models.CharField(max_length=16, choices=SourceType.choices)
    source_reference = models.CharField(max_length=80)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField(blank=True)
    before_snapshot = models.JSONField(default=dict)
    proposed_snapshot = models.JSONField(default=dict)
    detected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="detected_attendance_reconciliations"
    )
    decision = models.CharField(max_length=24, choices=Decision.choices, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resolved_attendance_reconciliations",
        blank=True,
        null=True,
    )
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolution_reason = models.TextField(blank=True)

    class Meta:
        db_table = "faculty_attendance_reconciliations"
        ordering = ["status", "meeting_id", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["meeting", "source_type", "source_reference"], name="uq_att_reconcile_source"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "meeting"], name="idx_att_reconcile_status"),
        ]

    def clean(self):
        super().clean()
        if self.status == self.Status.RESOLVED:
            if not self.decision or not self.resolved_by_id:
                raise ValidationError("Resolved reconciliation requires a decision and actor.")
        elif self.decision or self.resolved_by_id or self.resolved_at or self.resolution_reason:
            raise ValidationError("Pending reconciliation cannot contain resolution details.")


class CoverageReconciliation(TimeStampedModel):
    class EventType(models.TextChoices):
        ASSIGNMENT_CREATED = "ASSIGNMENT_CREATED", "Assignment created"
        ASSIGNMENT_REACTIVATED = "ASSIGNMENT_REACTIVATED", "Assignment reactivated"
        ASSIGNMENT_ACCEPTED = "ASSIGNMENT_ACCEPTED", "Assignment accepted"
        ASSIGNMENT_IMPORTED = "ASSIGNMENT_IMPORTED", "Assignment imported"
        PERMANENT_REPLACEMENT = "PERMANENT_REPLACEMENT", "Permanent replacement"
        UNASSIGNMENT = "UNASSIGNMENT", "Unassignment"
        DIRECT_UPDATE = "DIRECT_UPDATE", "Direct assignment update"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RESOLVED = "RESOLVED", "Resolved"

    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_coverage_reconciliations"
    )
    campus = models.ForeignKey(
        "tenants.Campus", on_delete=models.PROTECT, related_name="attendance_coverage_reconciliations"
    )
    department = models.ForeignKey(
        "tenants.Department", on_delete=models.PROTECT, related_name="attendance_coverage_reconciliations"
    )
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.PROTECT, related_name="attendance_coverage_reconciliations"
    )
    source_assignment = models.ForeignKey(
        "academics.FacultyAssignment",
        on_delete=models.PROTECT,
        related_name="attendance_coverage_reconciliations",
        blank=True,
        null=True,
    )
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    source_reference = models.CharField(max_length=96, unique=True)
    prior_faculty = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_coverage_changes_as_prior",
        blank=True,
        null=True,
    )
    proposed_faculty = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_coverage_changes_as_proposed",
        blank=True,
        null=True,
    )
    effective_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_attendance_coverage_reconciliations",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resolved_attendance_coverage_reconciliations",
        blank=True,
        null=True,
    )
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolution_reason = models.TextField(blank=True)

    class Meta:
        db_table = "faculty_attendance_coverage_reconciliations"
        ordering = ["status", "offering_id", "created_at"]
        indexes = [
            models.Index(fields=["offering", "status", "effective_at"], name="idx_att_covrec_offer"),
            models.Index(fields=["tenant", "campus", "department", "status"], name="idx_att_covrec_scope"),
        ]

    def clean(self):
        super().clean()
        _validate_scope(tenant_id=self.tenant_id, campus=self.campus, department=self.department)
        if self.offering_id and (
            self.offering.tenant_id != self.tenant_id
            or self.offering.campus_id != self.campus_id
            or self.offering.department_id != self.department_id
        ):
            raise ValidationError("Coverage reconciliation offering must match its scope.")
        if self.status == self.Status.RESOLVED:
            if not self.resolved_by_id or not self.resolved_at:
                raise ValidationError("Resolved coverage reconciliation requires actor and time.")
        elif self.resolved_by_id or self.resolved_at or self.resolution_reason:
            raise ValidationError("Pending coverage reconciliation cannot contain resolution details.")


class SavedCheckerRoute(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_routes")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="attendance_routes")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_routes"
    )
    name = models.CharField(max_length=120)
    revision = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "faculty_attendance_saved_routes"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "campus", "owner", "name"], name="uq_att_route_owner_name"
            ),
        ]
        indexes = [models.Index(fields=["tenant", "campus", "owner", "is_active"], name="idx_att_route_scope")]

    def clean(self):
        super().clean()
        if self.campus_id and self.campus.tenant_id != self.tenant_id:
            raise ValidationError("Saved route campus must belong to its tenant.")


class SavedCheckerRouteEntry(TimeStampedModel):
    route = models.ForeignKey(SavedCheckerRoute, on_delete=models.CASCADE, related_name="entries")
    schedule_slot = models.ForeignKey(ScheduleSlot, on_delete=models.PROTECT, related_name="route_entries")
    position = models.PositiveIntegerField()

    class Meta:
        db_table = "faculty_attendance_saved_route_entries"
        ordering = ["route_id", "position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["route", "schedule_slot"], name="uq_att_route_slot"),
            models.UniqueConstraint(fields=["route", "position"], name="uq_att_route_position"),
        ]

    def clean(self):
        super().clean()
        if self.route_id and self.schedule_slot_id:
            offering = self.schedule_slot.schedule_version.offering
            if offering.tenant_id != self.route.tenant_id or offering.campus_id != self.route.campus_id:
                raise ValidationError("Route entry schedule slot must match the route tenant and campus.")


class MonthlyChecklistArrangement(TimeStampedModel):
    """A checker's saved order for one academic scope and day group."""

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_monthly_arrangements")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="attendance_monthly_arrangements")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="attendance_monthly_arrangements")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="attendance_monthly_arrangements")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_monthly_arrangements")
    day_group = models.CharField(max_length=12)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "faculty_attendance_monthly_arrangements"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "campus", "academic_year", "term", "owner", "day_group"],
                name="uq_att_monthly_arr_scope",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "campus", "academic_year", "term", "day_group"], name="idx_att_monthly_scope"),
        ]

    def clean(self):
        super().clean()
        if self.campus_id and self.campus.tenant_id != self.tenant_id:
            raise ValidationError("Arrangement campus must belong to its tenant.")
        if self.academic_year_id and self.academic_year.tenant_id != self.tenant_id:
            raise ValidationError("Arrangement academic year must belong to its tenant.")
        if self.term_id and (
            self.term.tenant_id != self.tenant_id or self.term.academic_year_id != self.academic_year_id
        ):
            raise ValidationError("Arrangement term must belong to its academic year and tenant.")


class MonthlyChecklistArrangementEntry(TimeStampedModel):
    arrangement = models.ForeignKey(MonthlyChecklistArrangement, on_delete=models.CASCADE, related_name="entries")
    offering = models.ForeignKey("academics.CourseOffering", on_delete=models.PROTECT, related_name="attendance_monthly_arrangement_entries")
    pattern_key = models.CharField(max_length=64)
    time_group_key = models.CharField(max_length=32)
    position = models.PositiveIntegerField()

    class Meta:
        db_table = "faculty_attendance_monthly_arrangement_entries"
        ordering = ["time_group_key", "position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["arrangement", "offering", "pattern_key"], name="uq_att_monthly_arr_entry"),
            models.UniqueConstraint(fields=["arrangement", "time_group_key", "position"], name="uq_att_monthly_arr_pos"),
        ]

    def clean(self):
        super().clean()
        if self.arrangement_id and self.offering_id:
            arrangement = self.arrangement
            offering = self.offering
            if (
                offering.tenant_id != arrangement.tenant_id
                or offering.campus_id != arrangement.campus_id
                or offering.academic_year_id != arrangement.academic_year_id
                or offering.term_id != arrangement.term_id
            ):
                raise ValidationError("Arrangement entry must match the saved academic scope.")


class RecurringCombinedClass(TimeStampedModel):
    """Explicit effective-dated membership for one recurring shared meeting slot."""

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_combined_classes")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="attendance_combined_classes")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="attendance_combined_classes")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="attendance_combined_classes")
    weekday = models.PositiveSmallIntegerField(choices=[(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")])
    start_time = models.TimeField()
    end_time = models.TimeField()
    effective_from = models.DateField()
    effective_until = models.DateField(blank=True, null=True)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_attendance_combined_classes")

    @property
    def weekday_label(self):
        return ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[self.weekday]

    class Meta:
        db_table = "faculty_attendance_recurring_combined_classes"
        ordering = ["academic_year_id", "term_id", "weekday", "start_time", "effective_from", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(weekday__gte=0) & Q(weekday__lte=6), name="ck_att_combined_weekday"),
            models.CheckConstraint(condition=Q(end_time__gt=models.F("start_time")), name="ck_att_combined_time"),
            models.CheckConstraint(condition=Q(effective_until__isnull=True) | Q(effective_until__gte=models.F("effective_from")), name="ck_att_combined_dates"),
        ]
        indexes = [models.Index(fields=["tenant", "campus", "academic_year", "term", "weekday"], name="idx_att_combined_scope")]

    def clean(self):
        super().clean()
        if self.campus_id and self.campus.tenant_id != self.tenant_id:
            raise ValidationError("Combined class campus must belong to its tenant.")
        if self.academic_year_id and self.academic_year.tenant_id != self.tenant_id:
            raise ValidationError("Combined class academic year must belong to its tenant.")
        if self.term_id and (self.term.tenant_id != self.tenant_id or self.term.academic_year_id != self.academic_year_id):
            raise ValidationError("Combined class semester must belong to its academic year and tenant.")
        if self.end_time and self.start_time and self.end_time <= self.start_time:
            raise ValidationError("Combined class end time must be later than start time.")
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValidationError("Combined class end date cannot precede its start date.")


class RecurringCombinedClassOffering(TimeStampedModel):
    combined_class = models.ForeignKey(RecurringCombinedClass, on_delete=models.PROTECT, related_name="offering_links")
    offering = models.ForeignKey("academics.CourseOffering", on_delete=models.PROTECT, related_name="attendance_combined_class_links")
    is_primary = models.BooleanField(default=False)

    class Meta:
        db_table = "faculty_attendance_recurring_combined_offerings"
        constraints = [
            models.UniqueConstraint(fields=["combined_class", "offering"], name="uq_att_combined_offering"),
            models.UniqueConstraint(fields=["combined_class"], condition=Q(is_primary=True), name="uq_att_combined_primary"),
        ]

    def clean(self):
        super().clean()
        if self.combined_class_id and self.offering_id:
            group = self.combined_class
            offering = self.offering
            if (offering.tenant_id != group.tenant_id or offering.campus_id != group.campus_id or
                    offering.academic_year_id != group.academic_year_id or offering.term_id != group.term_id):
                raise ValidationError("Every linked offering must match the combined class tenant, campus, academic year, and semester.")


class CheckingRound(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_checking_rounds")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="attendance_checking_rounds")
    department = models.ForeignKey(
        "tenants.Department", on_delete=models.PROTECT, related_name="attendance_checking_rounds"
    )
    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="attendance_checking_rounds",
        blank=True,
        null=True,
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="attendance_checking_rounds",
        blank=True,
        null=True,
    )
    daily_occurrence_date = models.DateField(blank=True, null=True)
    checking_date = models.DateField()
    checking_end_date = models.DateField(blank=True, null=True)
    label = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    manifest_revision = models.PositiveIntegerField(default=1)
    manifest_hash = models.CharField(max_length=64)
    manifest_frozen_at = models.DateTimeField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_attendance_checking_rounds"
    )
    saved_route = models.ForeignKey(
        SavedCheckerRoute,
        on_delete=models.PROTECT,
        related_name="checking_rounds",
        blank=True,
        null=True,
    )
    route_name_snapshot = models.CharField(max_length=120, blank=True)
    route_revision_snapshot = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        db_table = "faculty_attendance_checking_rounds"
        ordering = ["-checking_date", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "campus", "department", "checking_date"], name="idx_att_round_scope"),
            models.Index(
                fields=["tenant", "campus", "academic_year", "term", "daily_occurrence_date"],
                name="idx_att_round_daily_scope",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "campus", "department", "academic_year", "term", "daily_occurrence_date"],
                name="uq_att_daily_round_scope",
            ),
        ]

    def clean(self):
        super().clean()
        _validate_scope(tenant_id=self.tenant_id, campus=self.campus, department=self.department)
        if self.checking_end_date and self.checking_end_date < self.checking_date:
            raise ValidationError("Checking end date cannot precede its start date.")
        if self.saved_route_id and (
            self.saved_route.tenant_id != self.tenant_id or self.saved_route.campus_id != self.campus_id
        ):
            raise ValidationError("Saved route must match the checking-round tenant and campus.")
        if self.academic_year_id and self.academic_year.tenant_id != self.tenant_id:
            raise ValidationError("Checking-round academic year must match its tenant.")
        if self.term_id and (
            self.term.tenant_id != self.tenant_id
            or (self.academic_year_id and self.term.academic_year_id != self.academic_year_id)
        ):
            raise ValidationError("Checking-round semester must match its academic year and tenant.")


class CheckingRoundMeeting(TimeStampedModel):
    checking_round = models.ForeignKey(CheckingRound, on_delete=models.PROTECT, related_name="manifest_rows")
    meeting = models.ForeignKey(TeachingMeeting, on_delete=models.PROTECT, related_name="checking_round_rows")
    sequence = models.PositiveIntegerField()
    reviewed_result_revision = models.PositiveIntegerField(default=0)
    meeting_snapshot = models.JSONField(default=dict)

    class Meta:
        db_table = "faculty_attendance_checking_round_meetings"
        ordering = ["checking_round_id", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["checking_round", "meeting"], name="uq_att_round_meeting"),
            models.UniqueConstraint(fields=["checking_round", "sequence"], name="uq_att_round_sequence"),
        ]

    def clean(self):
        super().clean()
        if self.checking_round_id and self.meeting_id and (
            self.meeting.tenant_id != self.checking_round.tenant_id
            or self.meeting.campus_id != self.checking_round.campus_id
            or self.meeting.department_id != self.checking_round.department_id
        ):
            raise ValidationError("Checking-round meeting must match the round scope.")


class AttendanceObservation(TimeStampedModel):
    round_meeting = models.ForeignKey(
        CheckingRoundMeeting, on_delete=models.PROTECT, related_name="observations"
    )
    submission_key = models.CharField(max_length=64)
    observed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_observations"
    )
    note = models.TextField(blank=True)
    payload_hash = models.CharField(max_length=64)

    class Meta:
        db_table = "faculty_attendance_observations"
        ordering = ["round_meeting_id", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["round_meeting", "submission_key"], name="uq_att_observation_request"),
        ]


class AttendanceObservationFinding(TimeStampedModel):
    class FindingType(models.TextChoices):
        ABSENCE = "ABSENCE", "Absence"
        LATE = "LATE", "Late"
        EARLY = "EARLY", "Early dismissal"

    class NoticeStatus(models.TextChoices):
        WITHOUT_NOTICE = "A", "Absent without notice"
        WITH_NOTICE = "N", "Absent with notice"

    observation = models.ForeignKey(
        AttendanceObservation, on_delete=models.PROTECT, related_name="findings"
    )
    finding_type = models.CharField(max_length=12, choices=FindingType.choices)
    segment_key = models.CharField(max_length=40)
    notice_status = models.CharField(max_length=1, choices=NoticeStatus.choices, blank=True)
    missed_periods = models.DecimalField(max_digits=7, decimal_places=2, blank=True, null=True)
    missed_hours = models.DecimalField(max_digits=7, decimal_places=2, blank=True, null=True)
    minutes = models.PositiveIntegerField(blank=True, null=True)

    class Meta:
        db_table = "faculty_attendance_observation_findings"
        ordering = ["observation_id", "finding_type", "segment_key"]
        constraints = [
            models.UniqueConstraint(
                fields=["observation", "finding_type", "segment_key"], name="uq_att_finding_segment"
            ),
            models.CheckConstraint(
                condition=Q(missed_periods__isnull=True) | Q(missed_periods__gte=0),
                name="ck_att_finding_periods",
            ),
            models.CheckConstraint(
                condition=Q(missed_hours__isnull=True) | Q(missed_hours__gte=0),
                name="ck_att_finding_hours",
            ),
        ]

    def clean(self):
        super().clean()
        if self.finding_type == self.FindingType.ABSENCE:
            if self.notice_status not in self.NoticeStatus.values:
                raise ValidationError("Each absence segment requires exactly one A or N notice status.")
            if self.minutes is not None:
                raise ValidationError("Absence segments use decimal periods/hours, not minutes.")
            if self.missed_periods is None and self.missed_hours is None:
                raise ValidationError("Absence segment requires missed periods or decimal hours.")
        else:
            if self.notice_status or self.missed_periods is not None or self.missed_hours is not None:
                raise ValidationError("Late and early-dismissal findings use minutes only.")
            if self.minutes is None:
                raise ValidationError("Late and early-dismissal findings require explicit minutes, including zero.")


class AttendanceResult(TimeStampedModel):
    class Status(models.TextChoices):
        UNVERIFIED = "UNVERIFIED", "Unverified"
        PRESENT = "PRESENT", "Present"
        EXCEPTION = "EXCEPTION", "Exception"

    meeting = models.OneToOneField(TeachingMeeting, on_delete=models.PROTECT, related_name="attendance_result")
    revision = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UNVERIFIED)
    faculty_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="current_attendance_results",
        blank=True,
        null=True,
    )
    source_observation = models.ForeignKey(
        AttendanceObservation,
        on_delete=models.PROTECT,
        related_name="selected_for_results",
        blank=True,
        null=True,
    )
    absent_without_notice_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    absent_with_notice_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    missed_periods = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    late_flag = models.BooleanField(default=False)
    late_minutes = models.PositiveIntegerField(default=0)
    early_flag = models.BooleanField(default=False)
    early_minutes = models.PositiveIntegerField(default=0)
    findings_snapshot = models.JSONField(default=list, blank=True)
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="corrected_attendance_results",
        blank=True,
        null=True,
    )
    correction_reason = models.TextField(blank=True)

    class Meta:
        db_table = "faculty_attendance_results"
        indexes = [
            models.Index(fields=["faculty_user", "status", "meeting"], name="idx_att_result_faculty"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(absent_without_notice_hours__gte=0), name="ck_att_result_a_hours"),
            models.CheckConstraint(condition=Q(absent_with_notice_hours__gte=0), name="ck_att_result_n_hours"),
            models.CheckConstraint(condition=Q(missed_periods__gte=0), name="ck_att_result_periods"),
        ]


class AttendanceResultRevision(TimeStampedModel):
    result = models.ForeignKey(AttendanceResult, on_delete=models.PROTECT, related_name="history")
    revision = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=AttendanceResult.Status.choices)
    faculty_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="attendance_result_revisions",
        blank=True,
        null=True,
    )
    source_observation = models.ForeignKey(
        AttendanceObservation,
        on_delete=models.PROTECT,
        related_name="result_revisions",
        blank=True,
        null=True,
    )
    findings_snapshot = models.JSONField(default=list, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_result_changes"
    )
    change_reason = models.TextField(blank=True)

    class Meta:
        db_table = "faculty_attendance_result_revisions"
        ordering = ["result_id", "revision"]
        constraints = [
            models.UniqueConstraint(fields=["result", "revision"], name="uq_att_result_revision"),
        ]


class AttendanceNoticeMutex(models.Model):
    """Stable campus-wide mutex, acquired before attendance/closure source locks."""

    campus = models.OneToOneField('tenants.Campus', on_delete=models.PROTECT, primary_key=True)

    class Meta:
        db_table = 'faculty_attendance_notice_mutexes'


class AttendanceStaffNotice(TimeStampedModel):
    """Current in-app delivery state; source attendance revisions remain append-only."""

    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.PROTECT)
    campus = models.ForeignKey('tenants.Campus', on_delete=models.PROTECT)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='attendance_staff_notices')
    faculty_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='attendance_followup_notices')
    meeting = models.ForeignKey(TeachingMeeting, on_delete=models.PROTECT, null=True, blank=True)
    academic_year = models.ForeignKey('academics.AcademicYear', on_delete=models.PROTECT)
    term = models.ForeignKey('academics.Term', on_delete=models.PROTECT)
    event_key = models.CharField(max_length=100)
    kind = models.CharField(max_length=12, choices=[('ABSENCE', 'Recorded absence'), ('TARDINESS', 'Monthly late follow-up')])
    month = models.DateField(null=True, blank=True)
    source_fingerprint = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'faculty_attendance_staff_notices'
        constraints = [models.UniqueConstraint(fields=['tenant', 'campus', 'recipient', 'event_key'], name='uq_att_staff_notice_event')]
        indexes = [models.Index(fields=['recipient', 'campus', 'is_active'], name='idx_att_staff_notice_inbox')]


class OfferingAttendanceSourceChange(TimeStampedModel):
    """Evidence of a Course Offering schedule/room change awaiting attendance review."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        RESOLVED = "RESOLVED", "Resolved"

    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_source_changes"
    )
    campus = models.ForeignKey(
        "tenants.Campus", on_delete=models.PROTECT, related_name="attendance_source_changes"
    )
    department = models.ForeignKey(
        "tenants.Department", on_delete=models.PROTECT, related_name="attendance_source_changes"
    )
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.PROTECT, related_name="attendance_source_changes"
    )
    old_schedule_text = models.CharField(max_length=255, blank=True)
    new_schedule_text = models.CharField(max_length=255, blank=True)
    old_room = models.CharField(max_length=80, blank=True)
    new_room = models.CharField(max_length=80, blank=True)
    effective_from = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField(blank=True)
    source_reference = models.CharField(max_length=96, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_attendance_source_changes"
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resolved_attendance_source_changes",
        blank=True,
        null=True,
    )
    resolved_at = models.DateTimeField(blank=True, null=True)
    resolution_reason = models.TextField(blank=True)

    class Meta:
        db_table = "faculty_attendance_source_changes"
        ordering = ["status", "offering_id", "created_at"]
        indexes = [
            models.Index(fields=["tenant", "campus", "department", "status"], name="idx_att_source_change_scope"),
            models.Index(fields=["offering", "status", "effective_from"], name="idx_att_source_change_offer"),
        ]

    def clean(self):
        super().clean()
        _validate_scope(tenant_id=self.tenant_id, campus=self.campus, department=self.department)
        if self.offering_id and (
            self.offering.tenant_id != self.tenant_id
            or self.offering.campus_id != self.campus_id
            or self.offering.department_id != self.department_id
        ):
            raise ValidationError("Course Offering source change must match its scope.")
        if self.status == self.Status.RESOLVED:
            if not self.effective_from or not self.resolved_by_id or not self.resolved_at:
                raise ValidationError("Resolved source change requires an effective date, actor, and time.")
        elif self.resolved_by_id or self.resolved_at or self.resolution_reason:
            raise ValidationError("Pending source change cannot contain resolution details.")


class AttendanceCutoffPublication(TimeStampedModel):
    """A campus-complete or explicitly faculty-scoped publication version."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    lineage_key = models.UUIDField(default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_cutoff_publications")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="attendance_cutoff_publications")
    academic_year = models.ForeignKey(
        "academics.AcademicYear", on_delete=models.PROTECT, related_name="attendance_cutoff_publications"
    )
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="attendance_cutoff_publications")
    start_date = models.DateField()
    end_date = models.DateField()
    faculty_scope = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True,
        related_name="faculty_cutoff_publications",
    )
    scope_key = models.CharField(max_length=40, default="CAMPUS", editable=False)
    scope_snapshot = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    review_fingerprint = models.CharField(max_length=64)
    submission_key = models.CharField(max_length=64, unique=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="published_attendance_cutoffs"
    )
    published_at = models.DateTimeField()
    publication_reason = models.TextField(blank=True)
    supersedes = models.ForeignKey(
        "self", on_delete=models.PROTECT, related_name="revisions", blank=True, null=True
    )

    class Meta:
        db_table = "faculty_attendance_cutoff_publications"
        ordering = ["-published_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "campus", "academic_year", "term", "start_date", "end_date", "scope_key", "version"],
                name="uq_att_cutoff_scope_version",
            ),
            models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="ck_att_cutoff_dates"),
            models.CheckConstraint(
                condition=Q(faculty_scope__isnull=True, scope_key="CAMPUS")
                | (Q(faculty_scope__isnull=False) & ~Q(scope_key="CAMPUS")),
                name="ck_att_cutoff_scope_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "campus", "academic_year", "term", "start_date", "end_date"], name="idx_att_cutoff_scope"),
            models.Index(fields=["lineage_key", "version"], name="idx_att_cutoff_lineage"),
        ]

    def clean(self):
        super().clean()
        _validate_scope(tenant_id=self.tenant_id, campus=self.campus)
        if self.scope_key != (f"FACULTY:{self.faculty_scope_id}" if self.faculty_scope_id else "CAMPUS"):
            raise ValidationError("Publication scope must identify exactly its faculty or the complete campus.")
        if self.academic_year_id and self.academic_year.tenant_id != self.tenant_id:
            raise ValidationError("Cutoff academic year must match its tenant.")
        if self.term_id and (
            self.term.tenant_id != self.tenant_id or self.term.academic_year_id != self.academic_year_id
        ):
            raise ValidationError("Cutoff semester must match its academic year and tenant.")
        if self.end_date < self.start_date:
            raise ValidationError("Cutoff end date cannot precede start date.")
        if self.supersedes_id and self.supersedes.lineage_key != self.lineage_key:
            raise ValidationError("A cutoff publication may supersede only its own lineage.")
        if self.supersedes_id and (
            self.supersedes.faculty_scope_id != self.faculty_scope_id
            or self.supersedes.tenant_id != self.tenant_id or self.supersedes.campus_id != self.campus_id
            or self.supersedes.academic_year_id != self.academic_year_id or self.supersedes.term_id != self.term_id
            or self.supersedes.start_date != self.start_date or self.supersedes.end_date != self.end_date
            or self.version != self.supersedes.version + 1
        ):
            raise ValidationError("Publication revisions must follow the same faculty/campus cutoff.")

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, faculty_scope__isnull=False).exists():
            raise ValidationError("Faculty publications are immutable; publish a new revision.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.faculty_scope_id:
            raise ValidationError("Faculty publication history cannot be deleted.")
        return super().delete(*args, **kwargs)


class AttendanceCutoffPublicationEntry(TimeStampedModel):
    publication = models.ForeignKey(
        AttendanceCutoffPublication, on_delete=models.PROTECT, related_name="entries"
    )
    occurrence_key = models.CharField(max_length=180)
    meeting = models.ForeignKey(
        TeachingMeeting,
        on_delete=models.PROTECT,
        related_name="cutoff_publication_entries",
        blank=True,
        null=True,
    )
    result_revision = models.ForeignKey(
        AttendanceResultRevision,
        on_delete=models.PROTECT,
        related_name="cutoff_publication_entries",
        blank=True,
        null=True,
    )
    faculty_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="published_attendance_entries",
        blank=True,
        null=True,
    )
    meeting_date = models.DateField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    scheduled_minutes = models.PositiveIntegerField()
    status = models.CharField(max_length=12, choices=[*AttendanceResult.Status.choices, ("CLOSED", "Class closed")])
    closure_decision = models.ForeignKey(
        "AttendanceClosureDecision", on_delete=models.PROTECT, related_name="published_entries", blank=True, null=True,
    )
    late_flag = models.BooleanField(default=False)
    late_minutes = models.PositiveIntegerField(default=0)
    early_flag = models.BooleanField(default=False)
    early_minutes = models.PositiveIntegerField(default=0)
    absent_without_notice_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    absent_with_notice_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    missed_periods = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    meeting_snapshot = models.JSONField(default=dict)
    findings_snapshot = models.JSONField(default=list, blank=True)
    result_revision_number = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "faculty_attendance_cutoff_publication_entries"
        ordering = ["meeting_date", "starts_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["publication", "occurrence_key"], name="uq_att_cutoff_entry_occurrence"),
        ]
        indexes = [
            models.Index(fields=["faculty_user", "meeting_date"], name="idx_att_cutoff_entry_faculty"),
            models.Index(fields=["publication", "meeting_date"], name="idx_att_cutoff_entry_date"),
        ]

    def clean(self):
        super().clean()
        if self.publication_id and self.publication.faculty_scope_id and self.faculty_user_id != self.publication.faculty_scope_id:
            raise ValidationError("A faculty publication may contain only its owner's occurrences.")
        if self.ends_at <= self.starts_at:
            raise ValidationError("Published meeting end must be later than its start.")
        if self.scheduled_minutes <= 0:
            raise ValidationError("Published meeting requires positive scheduled minutes.")
        if self.meeting_id and (
            self.meeting.tenant_id != self.publication.tenant_id
            or self.meeting.campus_id != self.publication.campus_id
        ):
            raise ValidationError("Published meeting must match publication tenant and campus.")
        if self.status == "CLOSED":
            if not self.closure_decision_id or self.result_revision_id:
                raise ValidationError("A closed class needs its checker decision and no attendance result revision.")
            if self.closure_decision.meeting_id != self.meeting_id:
                raise ValidationError("Published closure must belong to its dated meeting.")
        elif self.closure_decision_id:
            raise ValidationError("Only a closed class may reference a closure decision.")

    def save(self, *args, **kwargs):
        if self.pk and self.publication.faculty_scope_id:
            raise ValidationError("Faculty publication membership is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.publication.faculty_scope_id:
            raise ValidationError("Faculty publication membership cannot be deleted.")
        return super().delete(*args, **kwargs)


class AttendanceClosureDecision(TimeStampedModel):
    """Append-only, dated no-class decision and explicit checker-verified pay basis."""

    class Kind(models.TextChoices):
        HOLIDAY = "HOLIDAY", "Holiday"
        SUSPENSION = "SUSPENSION", "Class suspension"

    class PayBasis(models.TextChoices):
        REGULAR = "REGULAR", "Regular faculty: scheduled hours paid"
        PART_TIME = "PART_TIME", "Part-time faculty: no-work hours unpaid"

    class Status(models.TextChoices):
        CLOSED = "CLOSED", "Class closed"
        REVOKED = "REVOKED", "Closure revoked"

    meeting = models.ForeignKey(TeachingMeeting, on_delete=models.PROTECT, related_name="closure_decisions")
    revision = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey("self", on_delete=models.PROTECT, blank=True, null=True, related_name="later_revisions")
    status = models.CharField(max_length=8, choices=Status.choices)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    pay_basis = models.CharField(max_length=10, choices=PayBasis.choices)
    faculty_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    result_revision_at_decision = models.PositiveIntegerField(default=0)
    reason = models.TextField(blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_closure_decisions")

    class Meta:
        db_table = "faculty_attendance_closure_decisions"
        constraints = [models.UniqueConstraint(fields=["meeting", "revision"], name="uq_att_closure_meeting_rev")]
        indexes = [models.Index(fields=["meeting", "revision"], name="idx_att_closure_meeting_rev")]

    def clean(self):
        super().clean()
        if self.meeting_id and self.faculty_user_id and self.faculty_user_id not in {
            self.meeting.faculty_user_id,
            getattr(getattr(self.meeting, "substitution", None), "substitute_faculty_id", None),
        }:
            raise ValidationError("Closure faculty must match the dated meeting or explicit substitute.")
        if self.supersedes_id and (
            self.supersedes.meeting_id != self.meeting_id or self.revision != self.supersedes.revision + 1
        ):
            raise ValidationError("Closure correction must follow this meeting's preceding revision.")
        if not self.supersedes_id and self.revision != 1:
            raise ValidationError("The first closure decision must be revision one.")


class DTRAdjustment(TimeStampedModel):
    """An immutable checker decision; later values use the same entry key and a new revision."""

    class Kind(models.TextChoices):
        ADMIN = "ADMIN", "Scheduled admin office hours"
        LEAVE = "LEAVE", "Leave credit"
        OTHER = "OTHER", "Other deduction"

    class LeaveType(models.TextChoices):
        VL = "VL", "Vacation leave"
        SL = "SL", "Sick leave"
        EL = "EL", "Emergency leave"

    class Offset(models.TextChoices):
        A = "A", "Absent without notice"
        N = "N", "Absent with notice"
        L = "L", "Late"
        E = "E", "Early dismissal"
        OTHER = "OTHER", "Other deduction"

    entry_key = models.UUIDField(default=uuid.uuid4, editable=False)
    revision = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey("self", on_delete=models.PROTECT, blank=True, null=True, related_name="later_revisions")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT)
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT)
    department = models.ForeignKey("tenants.Department", on_delete=models.PROTECT)
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT)
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT)
    start_date = models.DateField()
    end_date = models.DateField()
    faculty_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    entry_date = models.DateField()
    kind = models.CharField(max_length=8, choices=Kind.choices)
    leave_type = models.CharField(max_length=2, choices=LeaveType.choices, blank=True)
    offset_kind = models.CharField(max_length=8, choices=Offset.choices, blank=True)
    hours = models.DecimalField(max_digits=8, decimal_places=2)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_dtr_adjustments")

    class Meta:
        db_table = "faculty_attendance_dtr_adjustments"
        constraints = [
            models.UniqueConstraint(fields=["entry_key", "revision"], name="uq_att_dtr_adjustment_rev"),
            models.CheckConstraint(condition=Q(hours__gte=0), name="ck_att_dtr_adjustment_hours"),
            models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="ck_att_dtr_adjustment_dates"),
        ]
        indexes = [models.Index(fields=["tenant", "campus", "faculty_user", "start_date", "end_date"], name="idx_att_dtr_adj_scope")]

    def clean(self):
        super().clean()
        _validate_scope(tenant_id=self.tenant_id, campus=self.campus, department=self.department)
        if self.academic_year_id and self.academic_year.tenant_id != self.tenant_id:
            raise ValidationError("DTR academic year must match the tenant.")
        if self.term_id and (self.term.tenant_id != self.tenant_id or self.term.academic_year_id != self.academic_year_id):
            raise ValidationError("DTR semester must match the academic year.")
        if self.end_date < self.start_date or not self.start_date <= self.entry_date <= self.end_date:
            raise ValidationError("Adjustment date must fall within its cutoff.")
        if self.hours < 0:
            raise ValidationError("Adjustment hours cannot be negative.")
        if self.kind == self.Kind.LEAVE:
            if not self.leave_type or not self.offset_kind:
                raise ValidationError("Leave needs a VL/SL/EL type and an exact deduction to offset.")
        elif self.leave_type or self.offset_kind:
            raise ValidationError("Only leave credit may specify a leave type or deduction offset.")
        if self.supersedes_id and (
            self.supersedes.entry_key != self.entry_key or self.revision != self.supersedes.revision + 1
        ):
            raise ValidationError("Adjustment correction must follow the previous revision of the same entry.")
        if not self.supersedes_id and self.revision != 1:
            raise ValidationError("A new DTR entry starts at revision one.")


class FacultyDTR(TimeStampedModel):
    """Immutable finalized hours and daily evidence for one faculty/campus/cutoff."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT)
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT)
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT)
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT)
    start_date = models.DateField()
    end_date = models.DateField()
    faculty_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    publication = models.ForeignKey(AttendanceCutoffPublication, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey("self", on_delete=models.PROTECT, blank=True, null=True, related_name="later_revisions")
    review_fingerprint = models.CharField(max_length=64)
    snapshot = models.JSONField()
    finalization_reason = models.TextField(blank=True)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="finalized_faculty_dtrs")
    finalized_at = models.DateTimeField()

    class Meta:
        db_table = "faculty_attendance_final_dtrs"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "campus", "academic_year", "term", "start_date", "end_date", "faculty_user", "revision"], name="uq_att_dtr_scope_rev"),
            models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="ck_att_dtr_dates"),
        ]
        indexes = [models.Index(fields=["tenant", "campus", "start_date", "end_date"], name="idx_att_dtr_scope")]

    def clean(self):
        super().clean()
        _validate_scope(tenant_id=self.tenant_id, campus=self.campus)
        if self.publication_id and (
            self.publication.tenant_id != self.tenant_id or self.publication.campus_id != self.campus_id
            or self.publication.academic_year_id != self.academic_year_id or self.publication.term_id != self.term_id
            or self.publication.start_date != self.start_date or self.publication.end_date != self.end_date
        ):
            raise ValidationError("DTR must use a publication for its exact campus cutoff.")
        if self.publication_id and self.publication.faculty_scope_id and self.publication.faculty_scope_id != self.faculty_user_id:
            raise ValidationError("DTR must belong to its faculty publication's owner.")
        if self.supersedes_id and (
            self.supersedes.faculty_user_id != self.faculty_user_id
            or self.supersedes.campus_id != self.campus_id
            or self.supersedes.revision + 1 != self.revision
        ):
            raise ValidationError("DTR revision must follow this faculty's prior cutoff snapshot.")
        if not self.supersedes_id and self.revision != 1:
            raise ValidationError("A new DTR starts at revision one.")

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, publication__faculty_scope__isnull=False).exists():
            raise ValidationError("Faculty-scoped final DTR snapshots are immutable; finalize a new revision.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.publication.faculty_scope_id:
            raise ValidationError("Faculty-scoped final DTR history cannot be deleted.")
        return super().delete(*args, **kwargs)


class DTRMixedFindingDecision(TimeStampedModel):
    """Checker-verified non-overlapping missed intervals for a published mixed finding."""

    publication = models.ForeignKey(AttendanceCutoffPublication, on_delete=models.PROTECT)
    meeting = models.ForeignKey(TeachingMeeting, on_delete=models.PROTECT)
    result_revision = models.ForeignKey(AttendanceResultRevision, on_delete=models.PROTECT)
    faculty_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(default=1)
    supersedes = models.ForeignKey("self", on_delete=models.PROTECT, blank=True, null=True, related_name="later_revisions")
    intervals = models.JSONField(default=list)
    reason = models.TextField(blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dtr_mixed_finding_decisions")

    class Meta:
        db_table = "faculty_attendance_dtr_mixed_decisions"
        constraints = [models.UniqueConstraint(fields=["publication", "meeting", "revision"], name="uq_att_dtr_mixed_rev")]
        indexes = [models.Index(fields=["publication", "faculty_user"], name="idx_att_dtr_mixed_scope")]

    def clean(self):
        super().clean()
        if self.meeting_id and self.publication_id and (
            self.meeting.tenant_id != self.publication.tenant_id or self.meeting.campus_id != self.publication.campus_id
        ):
            raise ValidationError("Mixed-finding decision must match its published campus.")
        if self.result_revision_id and self.result_revision.result.meeting_id != self.meeting_id:
            raise ValidationError("Mixed-finding decision must reference the meeting's result revision.")
        if not self.intervals:
            raise ValidationError("Actual missed intervals are required.")
        if self.supersedes_id and (
            self.supersedes.publication_id != self.publication_id
            or self.supersedes.meeting_id != self.meeting_id
            or self.revision != self.supersedes.revision + 1
        ):
            raise ValidationError("Mixed-finding correction must follow the preceding revision.")
        if not self.supersedes_id and self.revision != 1:
            raise ValidationError("The first interval decision must be revision one.")
