from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import ActivatableModel, TimeStampedModel


class AcademicYear(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="academic_years")
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        db_table = "academic_years"
        ordering = ["-start_date", "name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uq_academic_years_tenant_code"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def identifiers_are_in_use(self):
        if not self.pk:
            return False
        return (
            self.terms.exists()
            or self.course_offerings.exists()
            or self.enrollments.exists()
            or self.faculty_final_clearance_reports.exists()
            or self.grading_period_locks.exists()
        )

    def _validate_identifier_immutability(self):
        if not self.pk:
            return
        original = AcademicYear.objects.filter(pk=self.pk).only("tenant_id", "code").first()
        if not original or not self.identifiers_are_in_use():
            return

        errors = {}
        if self.tenant_id != original.tenant_id:
            errors["tenant"] = (
                "Tenant cannot be changed because this academic year is already used by academic records."
            )
        if self.code != original.code:
            errors["code"] = (
                "Code cannot be changed because this academic year is already used by terms, "
                "course offerings, enrollments, grading periods, or reports."
            )
        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        self._validate_identifier_immutability()

    def save(self, *args, **kwargs):
        self._validate_identifier_immutability()
        return super().save(*args, **kwargs)


class Term(TimeStampedModel, ActivatableModel):
    class TermType(models.TextChoices):
        REGULAR = "REGULAR", "Regular"
        SUMMER = "SUMMER", "Summer"
        SPECIAL = "SPECIAL", "Special"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="terms")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="terms")
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    term_type = models.CharField(max_length=20, choices=TermType.choices, default=TermType.REGULAR)
    sequence_no = models.PositiveIntegerField(default=1)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)

    class Meta:
        db_table = "terms"
        ordering = ["tenant", "academic_year", "sequence_no", "name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "academic_year", "code"], name="uq_terms_tenant_ay_code"),
        ]

    def __str__(self):
        return f"{self.academic_year.code}:{self.code}"


class TenantTermGradingPeriod(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="term_grading_periods",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="grading_periods",
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=120)
    sequence_no = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "tenant_term_grading_periods"
        ordering = ["tenant", "term", "sequence_no", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "term", "code"],
                name="uq_term_grading_periods_term_code",
            ),
        ]

    def __str__(self):
        return f"{self.term.code}:{self.code}"


class ActiveGradingPeriodSetting(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="active_grading_period_settings",
    )
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="active_grading_period_settings",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="active_grading_period_settings",
    )
    period = models.ForeignKey(
        "academics.TenantTermGradingPeriod",
        on_delete=models.PROTECT,
        related_name="active_settings",
    )
    set_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="set_active_grading_periods",
        blank=True,
        null=True,
    )
    set_at = models.DateTimeField(auto_now=True)
    auto_advanced_from_deadline = models.BooleanField(default=False)
    remarks = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "active_grading_period_settings"
        ordering = ["tenant", "campus", "term"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "campus", "term"],
                name="uq_active_grading_period_settings_scope",
            ),
        ]

    def __str__(self):
        return f"{self.campus.code}:{self.term.code}:{self.period.code}"


class Course(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="courses")
    campus = models.ForeignKey(
        "tenants.Campus", on_delete=models.PROTECT, related_name="courses", blank=True, null=True
    )
    department = models.ForeignKey(
        "tenants.Department", on_delete=models.PROTECT, related_name="courses", blank=True, null=True
    )
    code = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    units = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    course_type = models.CharField(max_length=50, blank=True, null=True)
    default_base_value = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    syllabus_url = models.URLField(
        max_length=1000,
        blank=True,
        null=True,
        help_text="Optional Google Drive or external syllabus link for this course.",
    )

    class Meta:
        db_table = "courses"
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uq_courses_tenant_code"),
        ]

    def __str__(self):
        return f"{self.code} - {self.title}"


class Section(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="sections")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="sections")
    department = models.ForeignKey("tenants.Department", on_delete=models.PROTECT, related_name="sections")
    program = models.ForeignKey("tenants.Program", on_delete=models.PROTECT, related_name="sections")
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    year_level = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = "sections"
        ordering = ["campus", "program", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "campus", "department", "program", "code"],
                name="uq_sections_scope_code",
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class CourseOffering(TimeStampedModel, ActivatableModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"
        ARCHIVED = "ARCHIVED", "Archived"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="course_offerings")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="course_offerings")
    department = models.ForeignKey("tenants.Department", on_delete=models.PROTECT, related_name="course_offerings")
    program = models.ForeignKey(
        "tenants.Program", on_delete=models.PROTECT, related_name="course_offerings", blank=True, null=True
    )
    academic_year = models.ForeignKey(
        "academics.AcademicYear", on_delete=models.PROTECT, related_name="course_offerings"
    )
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="course_offerings")
    course = models.ForeignKey("academics.Course", on_delete=models.PROTECT, related_name="course_offerings")
    section = models.ForeignKey("academics.Section", on_delete=models.PROTECT, related_name="course_offerings")
    room = models.CharField(max_length=80, blank=True, null=True)
    schedule_text = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    class Meta:
        db_table = "course_offerings"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "campus", "term", "status", "is_active"], name="idx_offer_scope_status"),
            models.Index(fields=["tenant", "campus", "department", "term"], name="idx_offer_dept_term"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "campus", "department", "term", "course", "section"],
                name="uq_offerings_scope_term_course_section",
            ),
        ]

    def __str__(self):
        return f"{self.term.code} {self.course.code} {self.section.code}"


class FacultyAssignment(TimeStampedModel, ActivatableModel):
    class ResponseStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        DECLINED = "DECLINED", "Declined"
        CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED", "Clarification Requested"
        EXPIRED = "EXPIRED", "Expired"

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="faculty_assignments",
        blank=True,
        null=True,
    )
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="faculty_assignments",
        blank=True,
        null=True,
    )
    offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.PROTECT, related_name="faculty_assignments"
    )
    faculty_user = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="faculty_assignments"
    )
    assignment_note = models.TextField(blank=True, null=True)
    accepted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="accepted_faculty_assignments",
        blank=True,
        null=True,
    )
    response_status = models.CharField(
        max_length=32,
        choices=ResponseStatus.choices,
        default=ResponseStatus.PENDING,
    )
    faculty_response_note = models.TextField(blank=True, null=True)
    responded_at = models.DateTimeField(blank=True, null=True)
    accepted_at = models.DateTimeField(blank=True, null=True)
    response_due_at = models.DateTimeField(blank=True, null=True)
    last_reminded_at = models.DateTimeField(blank=True, null=True)
    reminder_count = models.PositiveIntegerField(default=0)
    is_primary = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "faculty_assignments"
        ordering = ["-assigned_at"]
        indexes = [
            models.Index(fields=["faculty_user", "is_active", "response_status"], name="idx_fac_assign_user_status"),
            models.Index(fields=["tenant", "campus", "is_active"], name="idx_fac_assign_scope"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["offering", "faculty_user"], name="uq_faculty_assignments_offering_user"),
        ]

    def __str__(self):
        return f"{self.offering_id}:{self.faculty_user.username}"

    @property
    def is_accepted(self):
        return self.response_status == self.ResponseStatus.ACCEPTED and self.accepted_at is not None


class FacultyAssignmentReplacementLog(TimeStampedModel):
    class ReplacementType(models.TextChoices):
        PERMANENT = "PERMANENT", "Permanent Replacement"
        TEMPORARY = "TEMPORARY", "Temporary Substitute"
        SECONDARY = "SECONDARY", "Secondary / Co-Faculty"
        ADMINISTRATIVE = "ADMINISTRATIVE", "Administrative Reassignment"
        WRONG_ASSIGNMENT = "WRONG_ASSIGNMENT", "Wrong Faculty Assignment"

    class ReasonCategory(models.TextChoices):
        RESIGNATION = "RESIGNATION", "Resignation"
        MEDICAL_LEAVE = "MEDICAL_LEAVE", "Medical Leave"
        MATERNITY_LEAVE = "MATERNITY_LEAVE", "Maternity Leave"
        SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT", "Schedule Conflict"
        WRONG_ASSIGNMENT = "WRONG_ASSIGNMENT", "Wrong Assignment"
        ADMINISTRATIVE_REASSIGNMENT = "ADMINISTRATIVE_REASSIGNMENT", "Administrative Reassignment"
        SUBSTITUTE_FACULTY = "SUBSTITUTE_FACULTY", "Substitute Faculty"
        CO_FACULTY_ASSIGNMENT = "CO_FACULTY_ASSIGNMENT", "Co-Faculty Assignment"
        OTHER = "OTHER", "Other"

    batch_reference = models.CharField(max_length=40, db_index=True)
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="faculty_assignment_replacement_logs",
    )
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="faculty_assignment_replacement_logs",
    )
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="faculty_assignment_replacement_logs",
    )
    source_faculty = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="source_faculty_replacement_logs",
    )
    replacement_faculty = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="replacement_faculty_replacement_logs",
    )
    old_assignment = models.ForeignKey(
        "academics.FacultyAssignment",
        on_delete=models.PROTECT,
        related_name="replacement_logs_as_old_assignment",
    )
    new_assignment = models.ForeignKey(
        "academics.FacultyAssignment",
        on_delete=models.PROTECT,
        related_name="replacement_logs_as_new_assignment",
    )
    replacement_type = models.CharField(max_length=24, choices=ReplacementType.choices)
    reason_category = models.CharField(max_length=40, choices=ReasonCategory.choices)
    remarks = models.TextField()
    processed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="processed_faculty_assignment_replacements",
    )
    processed_at = models.DateTimeField()
    old_assignment_before_json = models.JSONField(default=dict)
    old_assignment_after_json = models.JSONField(default=dict)
    new_assignment_before_json = models.JSONField(blank=True, null=True)
    new_assignment_after_json = models.JSONField(default=dict)
    impact_snapshot_json = models.JSONField(default=dict)

    class Meta:
        db_table = "faculty_assignment_replacement_logs"
        ordering = ["-processed_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "campus", "processed_at"], name="idx_fac_repl_scope_time"),
            models.Index(fields=["offering", "processed_at"], name="idx_fac_repl_offering_time"),
            models.Index(fields=["batch_reference"], name="idx_fac_repl_batch"),
        ]

    def __str__(self):
        return f"{self.batch_reference}:{self.offering_id}"
