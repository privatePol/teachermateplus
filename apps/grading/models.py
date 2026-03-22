from django.db import models
from django.core.validators import MinValueValidator

from apps.core.models import ActivatableModel, TimeStampedModel


class ScoreInputMode(models.TextChoices):
    RAW_BASE50 = "RAW_BASE50", "Raw Score (Base-50)"
    DIRECT_PERCENTAGE = "DIRECT_PERCENTAGE", "Direct Percentage"


class ScoreInputModeOverride(models.TextChoices):
    INHERIT = "INHERIT", "Inherit Parent Rule"
    RAW_BASE50 = ScoreInputMode.RAW_BASE50, "Raw Score (Base-50)"
    DIRECT_PERCENTAGE = ScoreInputMode.DIRECT_PERCENTAGE, "Direct Percentage"


class GradingTemplate(TimeStampedModel, ActivatableModel):
    class ApprovalStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        FOR_APPROVAL = "FOR_APPROVAL", "For Approval"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="grading_templates")
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, null=True)
    default_base_value = models.DecimalField(max_digits=6, decimal_places=2, default=50)
    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.DRAFT,
    )
    approval_requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="requested_grading_template_approvals",
    )
    approval_requested_at = models.DateTimeField(blank=True, null=True)
    approval_reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_grading_template_approvals",
    )
    approval_reviewed_at = models.DateTimeField(blank=True, null=True)
    approval_remarks = models.TextField(blank=True, null=True)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(blank=True, null=True)
    published_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="published_grading_templates",
    )

    class Meta:
        db_table = "grading_templates"
        ordering = ["tenant", "name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uq_grading_templates_tenant_code"),
        ]

    def __str__(self):
        return f"{self.tenant.code}:{self.code}"


class TemplateHotfixRequest(TimeStampedModel):
    class ApplyMode(models.TextChoices):
        FUTURE_ONLY = "FUTURE_ONLY", "Future Only"
        ACTIVE_NOT_SUBMITTED = "ACTIVE_NOT_SUBMITTED", "Active Not Submitted"
        SELECTED_OFFERINGS = "SELECTED_OFFERINGS", "Selected Offerings"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        APPLIED = "APPLIED", "Applied"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="template_hotfix_requests")
    template = models.ForeignKey(
        "grading.GradingTemplate",
        on_delete=models.PROTECT,
        related_name="hotfix_requests",
    )
    apply_mode = models.CharField(max_length=24, choices=ApplyMode.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    justification = models.TextField()
    selected_offering_ids_json = models.JSONField(blank=True, null=True)
    requested_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="requested_template_hotfixes",
    )
    reviewed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_template_hotfixes",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    review_remarks = models.TextField(blank=True, null=True)
    applied_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="applied_template_hotfixes",
    )
    applied_at = models.DateTimeField(blank=True, null=True)
    affected_offering_count = models.PositiveIntegerField(default=0)
    recomputed_offering_count = models.PositiveIntegerField(default=0)
    impact_snapshot_json = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = "template_hotfix_requests"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template.code}:{self.apply_mode}:{self.status}"


class GradingTemplatePeriod(TimeStampedModel, ActivatableModel):
    template = models.ForeignKey(
        "grading.GradingTemplate",
        on_delete=models.PROTECT,
        related_name="periods",
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=120)
    sequence_no = models.PositiveIntegerField(default=1)
    weight_percentage = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)

    class Meta:
        db_table = "grading_template_periods"
        ordering = ["template", "sequence_no", "name"]
        constraints = [
            models.UniqueConstraint(fields=["template", "code"], name="uq_template_periods_template_code"),
        ]

    def __str__(self):
        return f"{self.template.code}:{self.code}"


class GradingTemplateComponent(TimeStampedModel, ActivatableModel):
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="components",
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=120)
    weight_percentage = models.DecimalField(max_digits=6, decimal_places=2)
    sort_order = models.PositiveIntegerField(default=1)
    score_input_mode = models.CharField(
        max_length=24,
        choices=ScoreInputMode.choices,
        default=ScoreInputMode.RAW_BASE50,
    )

    class Meta:
        db_table = "grading_template_components"
        ordering = ["template_period", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["template_period", "code"], name="uq_template_components_period_code"
            ),
        ]

    def __str__(self):
        return f"{self.template_period.code}:{self.code}"


class GradingTemplateSubcomponent(TimeStampedModel, ActivatableModel):
    template_component = models.ForeignKey(
        "grading.GradingTemplateComponent",
        on_delete=models.PROTECT,
        related_name="subcomponents",
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=120)
    weight_percentage = models.DecimalField(max_digits=6, decimal_places=2)
    sort_order = models.PositiveIntegerField(default=1)
    score_input_mode = models.CharField(
        max_length=24,
        choices=ScoreInputModeOverride.choices,
        default=ScoreInputModeOverride.INHERIT,
    )
    is_attendance_component = models.BooleanField(default=False)
    admin_locked = models.BooleanField(default=True)

    class Meta:
        db_table = "grading_template_subcomponents"
        ordering = ["template_component", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["template_component", "code"], name="uq_template_subcomponents_component_code"
            ),
        ]

    def __str__(self):
        return f"{self.template_component.code}:{self.code}"


class GradingTemplateDetail(TimeStampedModel, ActivatableModel):
    template_subcomponent = models.ForeignKey(
        "grading.GradingTemplateSubcomponent",
        on_delete=models.PROTECT,
        related_name="details",
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=120)
    weight_percentage = models.DecimalField(max_digits=6, decimal_places=2)
    sort_order = models.PositiveIntegerField(default=1)
    score_input_mode = models.CharField(
        max_length=24,
        choices=ScoreInputModeOverride.choices,
        default=ScoreInputModeOverride.INHERIT,
    )
    admin_locked = models.BooleanField(default=True)

    class Meta:
        db_table = "grading_template_details"
        ordering = ["template_subcomponent", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["template_subcomponent", "code"],
                name="uq_template_details_subcomponent_code",
            ),
        ]

    def __str__(self):
        return f"{self.template_subcomponent.code}:{self.code}"


class TenantGradingProfile(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="grading_profiles")
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="grading_profiles",
        blank=True,
        null=True,
    )
    department = models.ForeignKey(
        "tenants.Department",
        on_delete=models.PROTECT,
        related_name="grading_profiles",
        blank=True,
        null=True,
    )
    program = models.ForeignKey(
        "tenants.Program",
        on_delete=models.PROTECT,
        related_name="grading_profiles",
        blank=True,
        null=True,
    )
    course = models.ForeignKey(
        "academics.Course",
        on_delete=models.PROTECT,
        related_name="grading_profiles",
        blank=True,
        null=True,
    )
    course_type = models.CharField(max_length=50, blank=True, null=True)
    profile_code = models.CharField(max_length=64)
    profile_name = models.CharField(max_length=150)
    grading_template = models.ForeignKey(
        "grading.GradingTemplate",
        on_delete=models.PROTECT,
        related_name="tenant_grading_profiles",
    )
    default_base_value = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    priority = models.PositiveIntegerField(default=100)
    effective_from_term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="tenant_grading_profiles",
        blank=True,
        null=True,
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = "tenant_grading_profiles"
        ordering = ["tenant", "priority", "profile_code"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "profile_code"], name="uq_tenant_grading_profiles_code"),
        ]

    def __str__(self):
        return f"{self.tenant.code}:{self.profile_code}"


class CourseTemplateAssignment(TimeStampedModel, ActivatableModel):
    course = models.ForeignKey(
        "academics.Course", on_delete=models.PROTECT, related_name="template_assignments"
    )
    grading_template = models.ForeignKey(
        "grading.GradingTemplate", on_delete=models.PROTECT, related_name="course_assignments"
    )
    effective_from_term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="template_assignments",
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "course_template_assignments"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "grading_template", "effective_from_term"],
                name="uq_course_template_assignments",
            ),
        ]

    def __str__(self):
        return f"{self.course.code}->{self.grading_template.code}"


class CourseBaseValueOverride(TimeStampedModel, ActivatableModel):
    course = models.ForeignKey(
        "academics.Course", on_delete=models.PROTECT, related_name="base_value_overrides"
    )
    base_value = models.DecimalField(max_digits=6, decimal_places=2)
    effective_from_term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="base_value_overrides",
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "course_base_value_overrides"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "effective_from_term"],
                name="uq_course_base_value_overrides",
            ),
        ]

    def __str__(self):
        return f"{self.course.code}:{self.base_value}"


class GradeActivity(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="grade_activities")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="grade_activities")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="grade_activities",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="grade_activities",
    )
    template_component = models.ForeignKey(
        "grading.GradingTemplateComponent",
        on_delete=models.PROTECT,
        related_name="grade_activities",
    )
    template_subcomponent = models.ForeignKey(
        "grading.GradingTemplateSubcomponent",
        on_delete=models.PROTECT,
        related_name="grade_activities",
        blank=True,
        null=True,
    )
    template_detail = models.ForeignKey(
        "grading.GradingTemplateDetail",
        on_delete=models.PROTECT,
        related_name="grade_activities",
        blank=True,
        null=True,
    )
    title = models.CharField(max_length=150)
    total_score = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0.01)])
    activity_date = models.DateField(blank=True, null=True)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_grade_activities",
    )

    class Meta:
        db_table = "grade_activities"
        ordering = ["-activity_date", "-created_at"]

    def __str__(self):
        return f"{self.offering_id}:{self.title}"


class StudentActivityScore(TimeStampedModel, ActivatableModel):
    activity = models.ForeignKey(
        "grading.GradeActivity",
        on_delete=models.PROTECT,
        related_name="student_scores",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="activity_scores",
    )
    raw_score = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    computed_score = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    encoded_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="encoded_activity_scores",
    )
    remarks = models.CharField(max_length=255, blank=True, null=True)
    is_excused = models.BooleanField(default=False)

    class Meta:
        db_table = "student_activity_scores"
        ordering = ["student__last_name", "student__first_name", "student__student_no"]
        constraints = [
            models.UniqueConstraint(fields=["activity", "student"], name="uq_student_activity_scores_activity_student"),
        ]

    def __str__(self):
        return f"{self.activity_id}:{self.student_id}"


class StudentPeriodGrade(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="student_period_grades")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="student_period_grades")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="student_period_grades",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="student_period_grades",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="period_grades",
    )
    class_standing_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    exam_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    period_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    computed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="computed_period_grades",
    )
    computed_at = models.DateTimeField(auto_now=True)
    is_finalized = models.BooleanField(default=False)

    class Meta:
        db_table = "student_period_grades"
        ordering = ["student__last_name", "student__first_name", "student__student_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "template_period", "student"],
                name="uq_student_period_grades_offering_period_student",
            ),
        ]

    def __str__(self):
        return f"{self.offering_id}:{self.template_period.code}:{self.student_id}"


class StudentFinalGrade(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="student_final_grades")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="student_final_grades")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="student_final_grades",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="final_grades",
    )
    final_grade = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    remarks = models.CharField(max_length=255, blank=True, null=True)
    computed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="computed_final_grades",
    )
    computed_at = models.DateTimeField(auto_now=True)
    is_submitted = models.BooleanField(default=False)

    class Meta:
        db_table = "student_final_grades"
        ordering = ["student__last_name", "student__first_name", "student__student_no"]
        constraints = [
            models.UniqueConstraint(fields=["offering", "student"], name="uq_student_final_grades_offering_student"),
        ]

    def __str__(self):
        return f"{self.offering_id}:{self.student_id}"


class GradingPeriodLock(TimeStampedModel, ActivatableModel):
    class ScopeType(models.TextChoices):
        CAMPUS = "CAMPUS", "Campus"
        COURSE = "COURSE", "Course Offering"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="grading_period_locks")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="grading_period_locks")
    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="grading_period_locks",
    )
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="grading_period_locks")
    period_code = models.CharField(max_length=50)
    scope_type = models.CharField(max_length=12, choices=ScopeType.choices, default=ScopeType.CAMPUS)
    course_offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="grading_period_locks",
        blank=True,
        null=True,
    )
    is_locked = models.BooleanField(default=False)
    deadline_at = models.DateTimeField(blank=True, null=True)
    locked_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="period_locks_set",
    )
    locked_at = models.DateTimeField(blank=True, null=True)
    reopened_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="period_locks_reopened",
    )
    reopened_at = models.DateTimeField(blank=True, null=True)
    remarks = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "grading_period_locks"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "campus", "academic_year", "term", "period_code", "scope_type", "course_offering"],
                name="uq_grading_period_locks_scope",
            ),
        ]

    def __str__(self):
        return f"{self.campus.code}:{self.term.code}:{self.period_code}:{self.scope_type}"


class GradeSubmission(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        REOPENED = "REOPENED", "Reopened"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="grade_submissions")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="grade_submissions")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="grade_submissions",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="grade_submissions",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    submitted_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="submitted_grade_submissions",
    )
    submitted_at = models.DateTimeField(blank=True, null=True)
    reopened_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reopened_grade_submissions",
    )
    reopened_at = models.DateTimeField(blank=True, null=True)
    submission_snapshot_json = models.JSONField(blank=True, null=True)
    template_snapshot_json = models.JSONField(blank=True, null=True)
    remarks = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "grade_submissions"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "template_period"],
                name="uq_grade_submissions_offering_period",
            ),
        ]

    def __str__(self):
        return f"{self.offering_id}:{self.template_period.code}:{self.status}"


class GradeSubmissionReopenRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="grade_submission_reopen_requests")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="grade_submission_reopen_requests")
    submission = models.ForeignKey(
        "grading.GradeSubmission",
        on_delete=models.PROTECT,
        related_name="reopen_requests",
    )
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="grade_submission_reopen_requests",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="grade_submission_reopen_requests",
    )
    requested_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="grade_submission_reopen_requests",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    justification = models.TextField()
    reviewed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_grade_submission_reopen_requests",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    review_remarks = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "grade_submission_reopen_requests"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.submission_id}:{self.status}"


class GradeCorrectionRequest(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        CLOSED = "CLOSED", "Closed"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="grade_correction_requests")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="grade_correction_requests")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="grade_correction_requests",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="grade_correction_requests",
    )
    requested_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="grade_correction_requests",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    justification = models.TextField()
    reviewed_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_grade_correction_requests",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    review_remarks = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "grade_correction_requests"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.offering_id}:{self.template_period.code}:{self.status}"


class GradeCorrectionRequestItem(TimeStampedModel, ActivatableModel):
    class RequestedAction(models.TextChoices):
        UPDATE_SCORE = "UPDATE_SCORE", "Update Score"
        UPDATE_ATTENDANCE = "UPDATE_ATTENDANCE", "Update Attendance"
        UPDATE_STATUS = "UPDATE_STATUS", "Update Status"

    correction_request = models.ForeignKey(
        "grading.GradeCorrectionRequest",
        on_delete=models.PROTECT,
        related_name="items",
    )
    requested_action = models.CharField(max_length=24, choices=RequestedAction.choices, default=RequestedAction.UPDATE_SCORE)
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="grade_correction_items",
        blank=True,
        null=True,
    )
    grade_activity = models.ForeignKey(
        "grading.GradeActivity",
        on_delete=models.PROTECT,
        related_name="grade_correction_items",
        blank=True,
        null=True,
    )
    old_value = models.CharField(max_length=255, blank=True, null=True)
    new_value = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "grade_correction_request_items"
        ordering = ["id"]

    def __str__(self):
        return f"{self.correction_request_id}:{self.requested_action}"


class GradeCorrectionAttachment(TimeStampedModel):
    correction_request = models.ForeignKey(
        "grading.GradeCorrectionRequest",
        on_delete=models.PROTECT,
        related_name="attachments",
    )
    file = models.FileField(upload_to="correction_attachments/%Y/%m/")
    uploaded_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="uploaded_grade_correction_attachments",
    )

    class Meta:
        db_table = "grade_correction_attachments"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.correction_request_id}:{self.file.name}"


class GradeCorrectionUnlockWindow(TimeStampedModel, ActivatableModel):
    correction_request = models.OneToOneField(
        "grading.GradeCorrectionRequest",
        on_delete=models.PROTECT,
        related_name="unlock_window",
    )
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="grade_correction_unlock_windows",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="grade_correction_unlock_windows",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    is_consumed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "grade_correction_unlock_windows"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.offering_id}:{self.template_period.code}:{self.start_at}->{self.end_at}"
