from django.db import models

from apps.core.models import TimeStampedModel


class PredictionAssumptionMode(models.TextChoices):
    IGNORE_MISSING = "IGNORE_MISSING", "Ignore Missing"
    RAW_ZERO = "RAW_ZERO", "Assume Zero Raw Score"
    FULL_SCORE = "FULL_SCORE", "Assume Full Score"


class PredictionSettingSnapshot(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="prediction_setting_snapshots")
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="prediction_setting_snapshots",
        blank=True,
        null=True,
    )
    assumption_mode = models.CharField(
        max_length=24,
        choices=PredictionAssumptionMode.choices,
        default=PredictionAssumptionMode.IGNORE_MISSING,
    )
    show_best_case = models.BooleanField(default=True)
    show_worst_case = models.BooleanField(default=True)
    show_target_needed = models.BooleanField(default=True)
    generated_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="generated_prediction_setting_snapshots",
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "prediction_setting_snapshots"
        ordering = ["-created_at"]

    def __str__(self):
        campus_code = self.campus.code if self.campus_id else "ALL"
        return f"{self.tenant.code}:{campus_code}:{self.assumption_mode}"


class PredictionSnapshot(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="prediction_snapshots")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="prediction_snapshots")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="prediction_snapshots",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="prediction_snapshots",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="prediction_snapshots",
    )
    setting_snapshot = models.ForeignKey(
        "predictions.PredictionSettingSnapshot",
        on_delete=models.PROTECT,
        related_name="prediction_snapshots",
    )
    current_projected_period_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    best_case_period_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    worst_case_period_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    current_projected_final_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    best_case_final_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    worst_case_final_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    target_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    target_needed_percent = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    at_risk_flag = models.BooleanField(default=False)
    encoded_item_count = models.PositiveIntegerField(default=0)
    expected_item_count = models.PositiveIntegerField(default=0)
    remaining_item_count = models.PositiveIntegerField(default=0)
    coverage_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    source_version = models.CharField(max_length=64, blank=True, null=True)
    is_stale = models.BooleanField(default=False)
    computed_at = models.DateTimeField()

    class Meta:
        db_table = "prediction_snapshots"
        ordering = ["student__last_name", "student__first_name", "student__student_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "template_period", "student", "setting_snapshot"],
                name="uq_prediction_snapshots_scope_student_setting",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "campus", "offering", "template_period"], name="idx_pred_snapshot_scope"),
            models.Index(fields=["offering", "template_period", "student"], name="idx_pred_snapshot_student"),
            models.Index(fields=["is_stale", "computed_at"], name="idx_pred_snapshot_stale"),
        ]

    def __str__(self):
        return f"{self.offering_id}:{self.template_period_id}:{self.student_id}"


class PredictionSummarySnapshot(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="prediction_summary_snapshots")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="prediction_summary_snapshots")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="prediction_summary_snapshots",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="prediction_summary_snapshots",
    )
    setting_snapshot = models.ForeignKey(
        "predictions.PredictionSettingSnapshot",
        on_delete=models.PROTECT,
        related_name="prediction_summary_snapshots",
    )
    student_count = models.PositiveIntegerField(default=0)
    students_with_projection = models.PositiveIntegerField(default=0)
    at_risk_count = models.PositiveIntegerField(default=0)
    passing_count = models.PositiveIntegerField(default=0)
    failing_count = models.PositiveIntegerField(default=0)
    avg_projected_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    avg_best_case_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    avg_worst_case_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    avg_coverage_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    source_version = models.CharField(max_length=64, blank=True, null=True)
    is_stale = models.BooleanField(default=False)
    computed_at = models.DateTimeField()

    class Meta:
        db_table = "prediction_summary_snapshots"
        ordering = ["-computed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "template_period", "setting_snapshot"],
                name="uq_prediction_summary_scope_setting",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "campus", "offering", "template_period"], name="idx_pred_summary_scope"),
        ]

    def __str__(self):
        return f"{self.offering_id}:{self.template_period_id}"


class PredictionDirtyQueue(TimeStampedModel):
    class Reason(models.TextChoices):
        SCORE_CHANGE = "SCORE_CHANGE", "Score Change"
        ACTIVITY_CHANGE = "ACTIVITY_CHANGE", "Activity Change"
        ATTENDANCE_CHANGE = "ATTENDANCE_CHANGE", "Attendance Change"
        CORRECTION_APPROVED = "CORRECTION_APPROVED", "Correction Approved"
        REOPEN_EDIT = "REOPEN_EDIT", "Reopen Edit"
        TEMPLATE_CHANGE = "TEMPLATE_CHANGE", "Template Change"
        MANUAL_REFRESH = "MANUAL_REFRESH", "Manual Refresh"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        DONE = "DONE", "Done"
        FAILED = "FAILED", "Failed"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="prediction_dirty_rows")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="prediction_dirty_rows")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="prediction_dirty_rows",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="prediction_dirty_rows",
        blank=True,
        null=True,
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="prediction_dirty_rows",
        blank=True,
        null=True,
    )
    reason = models.CharField(max_length=24, choices=Reason.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    processed_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "prediction_dirty_queue"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="idx_pred_queue_status"),
            models.Index(fields=["offering", "template_period", "student"], name="idx_pred_queue_scope"),
        ]

    def __str__(self):
        return f"{self.offering_id}:{self.template_period_id or 'ALL'}:{self.reason}"


class PredictionWhatIfDraft(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="prediction_what_if_drafts")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="prediction_what_if_drafts")
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="prediction_what_if_drafts",
    )
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="prediction_what_if_drafts",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="prediction_what_if_drafts",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="prediction_what_if_drafts",
        blank=True,
        null=True,
    )
    scenario_name = models.CharField(max_length=120)
    assumed_remaining_score = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    target_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    assumptions_json = models.JSONField(blank=True, null=True)
    results_json = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = "prediction_what_if_drafts"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user.username}:{self.scenario_name}"


class PredictionViewLog(TimeStampedModel):
    class ViewMode(models.TextChoices):
        CLASS_SUMMARY = "CLASS_SUMMARY", "Class Summary"
        STUDENT_DETAIL = "STUDENT_DETAIL", "Student Detail"
        WHAT_IF = "WHAT_IF", "What If"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="prediction_view_logs")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="prediction_view_logs")
    viewer = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="prediction_view_logs",
    )
    viewer_role_code = models.CharField(max_length=64, blank=True, null=True)
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="prediction_view_logs",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="prediction_view_logs",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="prediction_view_logs",
        blank=True,
        null=True,
    )
    view_mode = models.CharField(max_length=16, choices=ViewMode.choices, default=ViewMode.CLASS_SUMMARY)

    class Meta:
        db_table = "prediction_view_logs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.viewer_id}:{self.offering_id}:{self.view_mode}"
