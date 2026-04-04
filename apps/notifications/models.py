from django.db import models

from apps.core.models import TimeStampedModel


class FacultyReminder(TimeStampedModel):
    class ReminderType(models.TextChoices):
        ACTIVITY_PREPARATION = "ACTIVITY_PREPARATION", "Activity Preparation"
        SCORE_ENCODING = "SCORE_ENCODING", "Score Encoding"
        ASSIGNMENT_ACCEPTANCE = "ASSIGNMENT_ACCEPTANCE", "Assignment Acceptance"
        GRADE_SUBMISSION = "GRADE_SUBMISSION", "Grade Submission"
        CORRECTION_WINDOW = "CORRECTION_WINDOW", "Correction Window"
        AT_RISK_FOLLOWUP = "AT_RISK_FOLLOWUP", "At-Risk Follow-up"
        CUSTOM = "CUSTOM", "Custom"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="faculty_reminders")
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="faculty_reminders",
        blank=True,
        null=True,
    )
    faculty_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="faculty_reminders",
    )
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="faculty_reminders",
        blank=True,
        null=True,
    )
    reminder_type = models.CharField(
        max_length=40,
        choices=ReminderType.choices,
        default=ReminderType.CUSTOM,
    )
    title = models.CharField(max_length=160)
    period_label = models.CharField(max_length=120, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    remind_at = models.DateTimeField()
    due_at = models.DateTimeField(blank=True, null=True)
    snoozed_until = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    send_email = models.BooleanField(default=True)
    email_last_queued_at = models.DateTimeField(blank=True, null=True)
    email_last_sent_at = models.DateTimeField(blank=True, null=True)
    email_attempt_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_faculty_reminders",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "faculty_reminders"
        ordering = ["completed_at", "snoozed_until", "remind_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "faculty_user", "remind_at"], name="idx_faculty_reminder_scope"),
            models.Index(fields=["tenant", "faculty_user", "completed_at"], name="idx_faculty_reminder_done"),
        ]

    def __str__(self):
        return f"{self.faculty_user_id}:{self.title}"

    @property
    def is_completed(self):
        return self.completed_at is not None


class FacultyMemo(TimeStampedModel):
    class MemoType(models.TextChoices):
        GENERAL = "GENERAL", "General"
        CLASS = "CLASS", "Class Memo"
        STUDENT = "STUDENT", "Student Memo"
        CUSTOM = "CUSTOM", "Custom"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="faculty_memos")
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="faculty_memos",
        blank=True,
        null=True,
    )
    faculty_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="faculty_memos",
    )
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="faculty_memos",
        blank=True,
        null=True,
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="faculty_memos",
        blank=True,
        null=True,
    )
    memo_type = models.CharField(max_length=20, choices=MemoType.choices, default=MemoType.GENERAL)
    title = models.CharField(max_length=160)
    body = models.TextField()
    is_pinned = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_faculty_memos",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "faculty_memos"
        ordering = ["-is_pinned", "-updated_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "faculty_user", "is_pinned"], name="idx_faculty_memo_scope"),
            models.Index(fields=["tenant", "faculty_user", "memo_type"], name="idx_faculty_memo_type"),
        ]

    def __str__(self):
        return f"{self.faculty_user_id}:{self.title}"


class FacultyReminderEmailQueue(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="faculty_reminder_email_queue")
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="faculty_reminder_email_queue",
        blank=True,
        null=True,
    )
    reminder = models.ForeignKey(
        "notifications.FacultyReminder",
        on_delete=models.CASCADE,
        related_name="email_queue",
    )
    recipient_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="faculty_reminder_email_queue",
    )
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=255)
    text_body = models.TextField()
    html_body = models.TextField()
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)
    dedupe_key = models.CharField(max_length=180)
    priority = models.PositiveIntegerField(default=50)
    metadata_json = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = "faculty_reminder_email_queue"
        ordering = ["status", "scheduled_at", "priority", "id"]
        constraints = [
            models.UniqueConstraint(fields=["dedupe_key"], name="uq_faculty_reminder_email_queue_dedupe"),
        ]
        indexes = [
            models.Index(fields=["status", "scheduled_at"], name="idx_fremail_status_schedule"),
            models.Index(fields=["tenant", "recipient_user", "scheduled_at"], name="idx_fremail_scope"),
        ]

    def __str__(self):
        return f"{self.recipient_user_id}:{self.status}:{self.dedupe_key}"


class NotificationQueue(TimeStampedModel):
    class Channel(models.TextChoices):
        EMAIL = "EMAIL", "Email"
        SYSTEM = "SYSTEM", "System"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="notification_queue")
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="notification_queue",
        blank=True,
        null=True,
    )
    recipient_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="notification_queue",
    )
    channel = models.CharField(max_length=12, choices=Channel.choices, default=Channel.EMAIL)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    reference_type = models.CharField(max_length=120, blank=True, null=True)
    reference_id = models.CharField(max_length=64, blank=True, null=True)
    metadata_json = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = "notification_queue"
        ordering = ["status", "scheduled_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient_user", "channel", "reference_type", "reference_id", "scheduled_at"],
                name="uq_notification_queue_reference",
            ),
        ]

    def __str__(self):
        return f"{self.channel}:{self.recipient_user_id}:{self.status}"
