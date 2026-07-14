from django.db import models

from apps.core.models import TimeStampedModel
from apps.core.upload_paths import import_source_upload_path


class ImportBatch(TimeStampedModel):
    class ImportType(models.TextChoices):
        SECTIONS = "sections", "Sections"
        COURSES = "courses", "Courses"
        STUDENTS = "students", "Students"
        COURSE_OFFERINGS = "course_offerings", "Course Offerings"
        FACULTY_ASSIGNMENTS = "faculty_assignments", "Faculty Assignments"
        FACULTY_USERS = "faculty_users", "Faculty Users"
        ENROLLMENT = "enrollment", "Enrollment"

    class Status(models.TextChoices):
        VALIDATED = "VALIDATED", "Validated"
        VALIDATION_FAILED = "VALIDATION_FAILED", "Validation Failed"
        CONFIRMED = "CONFIRMED", "Confirmed"
        CONFIRM_FAILED = "CONFIRM_FAILED", "Confirm Failed"

    import_type = models.CharField(max_length=40, choices=ImportType.choices)
    uploaded_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="import_batches_uploaded",
    )
    confirmed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="import_batches_confirmed",
        blank=True,
        null=True,
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="import_batches",
        blank=True,
        null=True,
    )
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="import_batches",
        blank=True,
        null=True,
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.VALIDATED)
    source_file = models.FileField(upload_to=import_source_upload_path, blank=True, null=True)
    original_filename = models.CharField(max_length=255, blank=True, null=True)
    expected_headers_json = models.JSONField(default=list)
    actual_headers_json = models.JSONField(default=list)
    total_rows = models.PositiveIntegerField(default=0)
    valid_rows = models.PositiveIntegerField(default=0)
    invalid_rows = models.PositiveIntegerField(default=0)
    imported_rows = models.PositiveIntegerField(default=0)
    error_summary_json = models.JSONField(blank=True, null=True)
    confirmed_at = models.DateTimeField(blank=True, null=True)
    metadata_json = models.JSONField(blank=True, null=True)
    email_system_enabled_snapshot = models.BooleanField(default=False)
    send_invitation_emails_requested = models.BooleanField(default=False)

    class Meta:
        db_table = "import_batches"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.import_type}:{self.id}:{self.status}"


class ImportBatchRow(TimeStampedModel):
    RESULT_LABELS = {
        "PREVIEW_CREATE": "New Faculty account — will be created",
        "PREVIEW_SKIP_EXISTING": "Existing matching Faculty account — will be skipped",
        "FAILED_VALIDATION": "Validation error",
        "FAILED_PROVISIONING": "Provisioning failed",
        "CREATED_EMAIL_DISABLED": "Email disabled by system",
        "CREATED_INVITATION_NOT_REQUESTED": "Invitation not requested",
        "CREATED_INVITATION_SENT": "Invitation sent",
        "CREATED_INVITATION_FAILED": "Invitation failed",
        "SKIPPED_EXISTING": "Skipped existing account",
    }
    class RowStatus(models.TextChoices):
        VALID = "VALID", "Valid"
        ERROR = "ERROR", "Error"
        IMPORTED = "IMPORTED", "Imported"

    batch = models.ForeignKey(
        "imports.ImportBatch",
        on_delete=models.PROTECT,
        related_name="rows",
    )
    row_number = models.PositiveIntegerField()
    row_status = models.CharField(max_length=16, choices=RowStatus.choices, default=RowStatus.VALID)
    raw_data_json = models.JSONField(default=dict)
    normalized_data_json = models.JSONField(blank=True, null=True)
    errors_json = models.JSONField(blank=True, null=True)
    imported_entity_type = models.CharField(max_length=120, blank=True, null=True)
    imported_entity_id = models.CharField(max_length=64, blank=True, null=True)
    result_code = models.CharField(max_length=64, blank=True, null=True)
    result_metadata_json = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = "import_batch_rows"
        ordering = ["row_number"]
        constraints = [
            models.UniqueConstraint(fields=["batch", "row_number"], name="uq_import_batch_rows_batch_row"),
        ]

    def __str__(self):
        return f"{self.batch_id}:{self.row_number}:{self.row_status}"

    @property
    def result_label(self):
        return self.RESULT_LABELS.get(self.result_code, self.result_code or "Pending preview")
