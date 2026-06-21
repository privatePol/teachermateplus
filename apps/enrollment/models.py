from django.db import models

from apps.core.models import ActivatableModel, TimeStampedModel


class Enrollment(TimeStampedModel, ActivatableModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        DRP = "DRP", "Dropped"
        W = "W", "Withdrawn"
        INC = "INC", "Incomplete"

    NON_ACTIVE_GRADING_STATUSES = {
        Status.DRP,
        Status.W,
        Status.INC,
    }

    class SourcePortal(models.TextChoices):
        ADMIN = "ADMIN", "Admin Portal"
        FACULTY = "FACULTY", "Faculty Portal"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="enrollments")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="enrollments")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="enrollments")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="enrollments")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="enrollments")
    course_offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.PROTECT, related_name="enrollments"
    )
    enrollment_status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    encoded_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="encoded_enrollments",
    )
    encoded_via_portal = models.CharField(
        max_length=16,
        choices=SourcePortal.choices,
        default=SourcePortal.ADMIN,
    )

    class Meta:
        db_table = "enrollments"
        ordering = ["student__last_name", "student__first_name", "student__student_no"]
        indexes = [
            models.Index(fields=["course_offering", "is_active", "enrollment_status"], name="idx_enroll_offering_status"),
            models.Index(fields=["tenant", "campus", "academic_year", "term", "is_active"], name="idx_enroll_scope_term"),
            models.Index(fields=["student", "is_active", "enrollment_status"], name="idx_enroll_student_status"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["course_offering", "student"], name="uq_enrollments_offering_student"),
        ]

    def __str__(self):
        return f"{self.course_offering_id}:{self.student.student_no}"


class EnrollmentAdjustmentLog(TimeStampedModel):
    class Result(models.TextChoices):
        COMPLETED = "completed", "Completed"
        COMPLETED_WITH_WARNING = "completed_with_warning", "Completed with Warning"
        BLOCKED = "blocked", "Blocked"
        FAILED = "failed", "Failed"

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="enrollment_adjustment_logs",
    )
    source_offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="source_enrollment_adjustment_logs",
    )
    destination_offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="destination_enrollment_adjustment_logs",
    )
    source_enrollment_id = models.PositiveBigIntegerField(blank=True, null=True)
    destination_enrollment_id = models.PositiveBigIntegerField(blank=True, null=True)
    source_previous_is_active = models.BooleanField(blank=True, null=True)
    source_previous_status = models.CharField(max_length=16, blank=True, null=True)
    destination_is_active = models.BooleanField(blank=True, null=True)
    destination_status = models.CharField(max_length=16, blank=True, null=True)
    batch_reference = models.CharField(max_length=40, blank=True, null=True)
    reason = models.TextField()
    processed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="processed_enrollment_adjustments",
    )
    processed_at = models.DateTimeField()
    result = models.CharField(max_length=32, choices=Result.choices)
    warning_flags = models.JSONField(default=list, blank=True)
    impact_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "enrollment_adjustment_logs"
        ordering = ["-processed_at", "-id"]
        indexes = [
            models.Index(fields=["source_offering", "processed_at"], name="idx_enradj_source_time"),
            models.Index(fields=["destination_offering", "processed_at"], name="idx_enradj_dest_time"),
            models.Index(fields=["student", "processed_at"], name="idx_enradj_student_time"),
            models.Index(fields=["result", "processed_at"], name="idx_enradj_result_time"),
            models.Index(fields=["batch_reference"], name="idx_enradj_batch_ref"),
        ]

    def __str__(self):
        return f"{self.student_id}:{self.source_offering_id}->{self.destination_offering_id}:{self.result}"


class ClassListChangeRequest(TimeStampedModel):
    class RequestType(models.TextChoices):
        ADD = "ADD", "Add Student"
        REMOVE = "REMOVE", "Remove Student"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="class_list_change_requests")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="class_list_change_requests")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="class_list_change_requests",
    )
    faculty_requester = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="requested_class_list_change_requests",
    )
    request_type = models.CharField(max_length=16, choices=RequestType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    remarks = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_class_list_change_requests",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    review_remarks = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "class_list_change_requests"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["campus", "status", "created_at"], name="idx_clsreq_campus_status"),
            models.Index(fields=["offering", "status", "created_at"], name="idx_clsreq_offering_status"),
            models.Index(fields=["faculty_requester", "created_at"], name="idx_clsreq_requester_time"),
        ]

    def __str__(self):
        return f"{self.offering_id}:{self.request_type}:{self.status}"


class ClassListChangeRequestItem(TimeStampedModel):
    request = models.ForeignKey(
        "enrollment.ClassListChangeRequest",
        on_delete=models.CASCADE,
        related_name="items",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="class_list_change_request_items",
    )
    enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="class_list_change_request_items",
    )
    reference_student_no = models.CharField(max_length=32, blank=True)
    reference_student_name = models.CharField(max_length=150, blank=True)

    class Meta:
        db_table = "class_list_change_request_items"
        ordering = ["id"]

    def __str__(self):
        return f"{self.request_id}:{self.student_id or self.enrollment_id or 'manual'}"
