from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import TimeStampedModel


class AcademicInterventionCase(TimeStampedModel):
    class DetectionSource(models.TextChoices):
        ANALYTICS = "ANALYTICS", "Academic analytics"
        MANUAL = "MANUAL", "Faculty identified"

    class Decision(models.TextChoices):
        CONDUCT = "CONDUCT", "Conduct Intervention"
        MONITOR = "MONITOR", "Continue Monitoring"
        NO_INTERVENTION = "NO_INTERVENTION", "No Intervention Needed"
        ALREADY_ADDRESSED = "ALREADY_ADDRESSED", "Already Addressed"
        INSUFFICIENT_DATA = "INSUFFICIENT_DATA", "Insufficient Grading Data"
        REFERRED = "REFERRED", "Referred to Another Office"

    class ReviewStatus(models.TextChoices):
        PENDING_REVIEW = "PENDING_REVIEW", "Pending Review"
        AWAITING_DATA = "AWAITING_DATA", "Awaiting Data"
        MONITORING = "MONITORING", "Monitoring"
        INTERVENTION_PLANNED = "INTERVENTION_PLANNED", "Intervention Planned"
        INTERVENTION_CONDUCTED = "INTERVENTION_CONDUCTED", "Intervention Conducted"
        NO_INTERVENTION = "NO_INTERVENTION", "No Intervention"
        REFERRED = "REFERRED", "Referred"
        CLOSED = "CLOSED", "Closed"
        VOIDED = "VOIDED", "Voided"

    class ReferralDestination(models.TextChoices):
        GUIDANCE = "GUIDANCE", "Guidance Office"
        STUDENT_SERVICES = "STUDENT_SERVICES", "Student Services"
        ACADEMIC_HEAD = "ACADEMIC_HEAD", "Academic Head"
        OTHER_APPROVED = "OTHER_APPROVED", "Other Approved Office"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="academic_intervention_cases")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="academic_intervention_cases")
    offering = models.ForeignKey("academics.CourseOffering", on_delete=models.PROTECT, related_name="academic_intervention_cases")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="academic_intervention_cases")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="academic_intervention_cases")
    grading_period = models.ForeignKey("grading.GradingTemplatePeriod", on_delete=models.PROTECT, related_name="academic_intervention_cases")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="academic_intervention_cases")
    faculty_owner = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="owned_academic_intervention_cases")
    identified_at = models.DateTimeField()
    detection_source = models.CharField(max_length=16, choices=DetectionSource.choices)
    detection_code = models.CharField(max_length=64, blank=True)
    analytics_source_fingerprint = models.CharField(max_length=64, blank=True)
    concern_snapshot_json = models.JSONField(default=dict, blank=True)
    distinct_concern_summary = models.CharField(max_length=500, blank=True)
    faculty_decision = models.CharField(max_length=24, choices=Decision.choices, blank=True)
    faculty_rationale = models.TextField(blank=True)
    decision_at = models.DateTimeField(blank=True, null=True)
    review_status = models.CharField(max_length=32, choices=ReviewStatus.choices, default=ReviewStatus.PENDING_REVIEW)
    referral_destination = models.CharField(max_length=32, choices=ReferralDestination.choices, blank=True)
    referral_destination_label = models.CharField(max_length=120, blank=True)
    referral_date = models.DateField(blank=True, null=True)
    referral_reason = models.CharField(max_length=500, blank=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    voided_at = models.DateTimeField(blank=True, null=True)
    void_reason = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="created_academic_intervention_cases")
    updated_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="updated_academic_intervention_cases")

    class Meta:
        db_table = "academic_intervention_cases"
        ordering = ["-identified_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "campus", "academic_year", "term", "review_status"], name="idx_aic_scope_term_status"),
            models.Index(fields=["faculty_owner", "review_status", "updated_at"], name="idx_aic_owner_status_upd"),
            models.Index(fields=["offering", "student", "grading_period"], name="idx_aic_offer_student_period"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["faculty_owner", "offering", "student", "grading_period", "analytics_source_fingerprint"],
                condition=Q(detection_source="ANALYTICS", voided_at__isnull=True),
                name="uq_aic_owner_active_analytics",
            ),
        ]

    def clean(self):
        errors = {}
        if self.campus_id and self.tenant_id and self.campus.tenant_id != self.tenant_id:
            errors["campus"] = "Campus must belong to the tenant."
        if self.offering_id:
            offering = self.offering
            for field_name, actual in (("tenant", offering.tenant_id), ("campus", offering.campus_id), ("academic_year", offering.academic_year_id), ("term", offering.term_id)):
                if getattr(self, f"{field_name}_id") and getattr(self, f"{field_name}_id") != actual:
                    errors[field_name] = "Case scope must match the course offering."
        if self.student_id and self.tenant_id and self.student.tenant_id != self.tenant_id:
            errors["student"] = "Student must belong to the tenant."
        if self.detection_source == self.DetectionSource.ANALYTICS and not self.analytics_source_fingerprint:
            errors["analytics_source_fingerprint"] = "Analytics cases require a source fingerprint."
        if self.detection_source == self.DetectionSource.MANUAL and len((self.distinct_concern_summary or "").strip()) < 10:
            errors["distinct_concern_summary"] = "Enter a concise distinct-concern summary."
        if self.pk:
            original_owner_id = type(self).objects.filter(pk=self.pk).values_list("faculty_owner_id", flat=True).first()
            if original_owner_id is not None and original_owner_id != self.faculty_owner_id:
                errors["faculty_owner"] = "Faculty ownership is immutable."
        if self.faculty_decision == self.Decision.REFERRED:
            if not self.referral_destination:
                errors["referral_destination"] = "Select an approved referral destination."
            if not self.referral_date:
                errors["referral_date"] = "Enter the referral date."
            if not (self.referral_reason or "").strip():
                errors["referral_reason"] = "Enter a brief academic referral reason."
            if self.referral_destination == self.ReferralDestination.OTHER_APPROVED and not (
                self.referral_destination_label or ""
            ).strip():
                errors["referral_destination_label"] = "Name the approved referral office."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original_owner_id = type(self).objects.filter(pk=self.pk).values_list("faculty_owner_id", flat=True).first()
            if original_owner_id is not None and original_owner_id != self.faculty_owner_id:
                raise ValidationError({"faculty_owner": "Faculty ownership is immutable."})
        return super().save(*args, **kwargs)


class AcademicInterventionDecisionRevision(TimeStampedModel):
    case = models.ForeignKey(
        AcademicInterventionCase,
        on_delete=models.PROTECT,
        related_name="decision_revisions",
    )
    revision_no = models.PositiveIntegerField()
    supersedes = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="superseded_by",
        blank=True,
        null=True,
    )
    decision = models.CharField(max_length=24, choices=AcademicInterventionCase.Decision.choices)
    rationale = models.TextField(blank=True)
    decided_at = models.DateTimeField()
    decided_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="academic_intervention_decision_revisions",
    )
    correction_reason = models.CharField(max_length=500, blank=True)
    referral_destination = models.CharField(
        max_length=32,
        choices=AcademicInterventionCase.ReferralDestination.choices,
        blank=True,
    )
    referral_destination_label = models.CharField(max_length=120, blank=True)
    referral_date = models.DateField(blank=True, null=True)
    referral_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "academic_intervention_decision_revisions"
        ordering = ["revision_no", "id"]
        constraints = [
            models.UniqueConstraint(fields=["case", "revision_no"], name="uq_aidr_case_revision"),
        ]
        indexes = [models.Index(fields=["case", "decided_at"], name="idx_aidr_case_decided")]


class AcademicInterventionAction(TimeStampedModel):
    class Status(models.TextChoices):
        PLANNED = "PLANNED", "Planned"
        CONDUCTED = "CONDUCTED", "Conducted"
        CANCELLED = "CANCELLED", "Cancelled"

    case = models.ForeignKey(AcademicInterventionCase, on_delete=models.PROTECT, related_name="actions")
    intervention_type = models.CharField(max_length=120)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    planned_for = models.DateField(blank=True, null=True)
    conducted_on = models.DateField(blank=True, null=True)
    action_summary = models.TextField()
    student_action_plan = models.TextField(blank=True)
    cancellation_reason = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="created_academic_intervention_actions")
    updated_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="updated_academic_intervention_actions")

    class Meta:
        db_table = "academic_intervention_actions"
        ordering = ["-conducted_on", "-planned_for", "-id"]
        indexes = [models.Index(fields=["case", "status", "conducted_on"], name="idx_aia_case_status_date")]
        constraints = [
            models.UniqueConstraint(
                fields=["case"],
                condition=Q(status="PLANNED"),
                name="uq_aia_case_active_planned",
            ),
        ]

    def clean(self):
        errors = {}
        if self.status == self.Status.PLANNED:
            if not self.planned_for:
                errors["planned_for"] = "Enter the planned intervention date."
            if self.conducted_on:
                errors["conducted_on"] = "A planned action cannot already have a conducted date."
        elif self.status == self.Status.CONDUCTED and not self.conducted_on:
            errors["conducted_on"] = "Enter the intervention date."
        elif self.status == self.Status.CANCELLED and not (self.cancellation_reason or "").strip():
            errors["cancellation_reason"] = "Enter a cancellation reason."
        if errors:
            raise ValidationError(errors)


class AcademicInterventionFollowUp(TimeStampedModel):
    class Status(models.TextChoices):
        NOT_REQUIRED = "NOT_REQUIRED", "Not Required"
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"
        STUDENT_UNRESPONSIVE = "STUDENT_UNRESPONSIVE", "Student Unresponsive"
        FURTHER_SUPPORT_NEEDED = "FURTHER_SUPPORT_NEEDED", "Further Support Needed"

    case = models.ForeignKey(AcademicInterventionCase, on_delete=models.PROTECT, related_name="follow_ups")
    action = models.ForeignKey(AcademicInterventionAction, on_delete=models.PROTECT, related_name="follow_ups", blank=True, null=True)
    due_on = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.SCHEDULED)
    result_summary = models.TextField(blank=True)
    completed_on = models.DateField(blank=True, null=True)
    created_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="created_academic_intervention_followups")
    updated_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, blank=True, null=True, related_name="updated_academic_intervention_followups")

    class Meta:
        db_table = "academic_intervention_followups"
        ordering = ["due_on", "id"]
        indexes = [models.Index(fields=["case", "status", "due_on"], name="idx_aif_case_status_due")]

    @property
    def is_due(self):
        return bool(self.status == self.Status.SCHEDULED and self.due_on and self.due_on < timezone.localdate())

    @property
    def effective_status_display(self):
        return "Due" if self.is_due else self.get_status_display()

    def clean(self):
        errors = {}
        if self.action_id and self.case_id and self.action.case_id != self.case_id:
            errors["action"] = "Follow-up action must belong to the same intervention record."
        if self.status == self.Status.COMPLETED and not self.completed_on:
            errors["completed_on"] = "Enter the follow-up completion date."
        if self.status != self.Status.COMPLETED and self.completed_on:
            errors["completed_on"] = "Only completed follow-ups may have a completion date."
        if errors:
            raise ValidationError(errors)
