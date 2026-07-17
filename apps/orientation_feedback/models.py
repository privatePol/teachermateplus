from __future__ import annotations

import secrets
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimeStampedModel


def generate_orientation_feedback_token() -> str:
    return secrets.token_urlsafe(32)


class OrientationSurveySession(TimeStampedModel):
    class SurveyType(models.TextChoices):
        FACULTY = "FACULTY", "Faculty Orientation Feedback"
        ACADEMIC_HEADS = "ACADEMIC_HEADS", "Academic Heads Orientation Feedback"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        OPEN = "OPEN", "Open"
        CLOSED = "CLOSED", "Closed"
        CANCELLED = "CANCELLED", "Cancelled"

    class ClosureReason(models.TextChoices):
        MANUAL = "MANUAL", "Ended by facilitator"
        AUTOMATIC = "AUTOMATIC", "Automatic closing time reached"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    survey_type = models.CharField(max_length=24, choices=SurveyType.choices)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="orientation_feedback_sessions",
    )
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="orientation_feedback_sessions",
    )
    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="orientation_feedback_sessions",
        blank=True,
        null=True,
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="orientation_feedback_sessions",
        blank=True,
        null=True,
    )
    orientation_date = models.DateField(blank=True, null=True)
    intended_start_time = models.TimeField(blank=True, null=True)
    intended_end_time = models.TimeField(blank=True, null=True)
    auto_close_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    public_token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_orientation_feedback_token,
        editable=False,
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_orientation_feedback_sessions",
    )
    started_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="started_orientation_feedback_sessions",
        blank=True,
        null=True,
    )
    started_at = models.DateTimeField(blank=True, null=True)
    closed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="closed_orientation_feedback_sessions",
        blank=True,
        null=True,
    )
    closed_at = models.DateTimeField(blank=True, null=True)
    closure_reason = models.CharField(max_length=16, choices=ClosureReason.choices, blank=True)
    cancelled_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="cancelled_orientation_feedback_sessions",
        blank=True,
        null=True,
    )
    cancelled_at = models.DateTimeField(blank=True, null=True)
    cancellation_reason = models.CharField(max_length=500, blank=True)
    eligible_count_snapshot = models.PositiveIntegerField(blank=True, null=True, editable=False)
    question_snapshot_version = models.PositiveIntegerField(default=0, editable=False)
    eligible_head_roles = models.ManyToManyField(
        "rbac.Role",
        through="OrientationSurveyEligibleRole",
        related_name="orientation_feedback_sessions",
        blank=True,
    )

    class Meta:
        db_table = "orientation_feedback_sessions"
        ordering = ["-orientation_date", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "campus", "status"], name="idx_orient_scope_status"),
            models.Index(fields=["survey_type", "orientation_date"], name="idx_orient_type_date"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="CANCELLED")
                    | (
                        models.Q(cancelled_at__isnull=False, cancelled_by__isnull=False)
                        & ~models.Q(cancellation_reason="")
                    )
                ),
                name="ck_orient_cancel_evidence",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="OPEN")
                    | models.Q(started_at__isnull=False, started_by__isnull=False)
                ),
                name="ck_orient_open_evidence",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.campus_id and self.tenant_id and self.campus.tenant_id != self.tenant_id:
            errors["campus"] = "Campus must belong to the selected tenant."
        if self.academic_year_id and self.academic_year.tenant_id != self.tenant_id:
            errors["academic_year"] = "Academic year must belong to the selected tenant."
        if self.term_id:
            if self.term.tenant_id != self.tenant_id:
                errors["term"] = "Term must belong to the selected tenant."
            if self.academic_year_id and self.term.academic_year_id != self.academic_year_id:
                errors["term"] = "Term must belong to the selected academic year."
        if (
            self.intended_start_time
            and self.intended_end_time
            and self.intended_end_time <= self.intended_start_time
        ):
            errors["intended_end_time"] = "Intended end time must be after the start time."
        if self.survey_type == self.SurveyType.FACULTY and self.pk and self.eligible_head_roles.exists():
            errors["eligible_head_roles"] = "Academic-head roles apply only to an Academic Heads survey."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.get_survey_type_display()}: {self.title} ({self.status})"


class OrientationSurveyEligibleRole(models.Model):
    session = models.ForeignKey(
        OrientationSurveySession,
        on_delete=models.CASCADE,
        related_name="eligible_role_links",
    )
    role = models.ForeignKey(
        "rbac.Role",
        on_delete=models.PROTECT,
        related_name="orientation_feedback_eligibility_links",
    )

    class Meta:
        db_table = "orientation_feedback_eligible_roles"
        constraints = [
            models.UniqueConstraint(fields=["session", "role"], name="uq_orient_session_role"),
        ]


class OrientationSurveyQuestion(TimeStampedModel):
    class QuestionType(models.TextChoices):
        SCALE = "SCALE", "Five-point scale"
        MULTI_SELECT = "MULTI_SELECT", "Multiple selection"
        TEXT = "TEXT", "Open feedback"

    class ScaleKind(models.TextChoices):
        QUALITY = "QUALITY", "Quality"
        EASE = "EASE", "Ease"
        CLARITY = "CLARITY", "Clarity"
        PACE = "PACE", "Pace"
        CONFIDENCE = "CONFIDENCE", "Confidence"
        READINESS = "READINESS", "Readiness"
        AGREEMENT = "AGREEMENT", "Agreement"

    session = models.ForeignKey(
        OrientationSurveySession,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    code = models.CharField(max_length=64)
    section_code = models.CharField(max_length=16)
    section_title = models.CharField(max_length=120)
    text = models.CharField(max_length=500)
    question_type = models.CharField(max_length=16, choices=QuestionType.choices)
    scale_kind = models.CharField(max_length=16, choices=ScaleKind.choices, blank=True)
    is_required = models.BooleanField(default=False)
    reverse_scored = models.BooleanField(default=False)
    composite_index_code = models.CharField(max_length=64, blank=True)
    display_order = models.PositiveIntegerField()

    class Meta:
        db_table = "orientation_feedback_questions"
        ordering = ["display_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["session", "code"], name="uq_orient_session_question"),
            models.UniqueConstraint(
                fields=["session", "display_order"],
                name="uq_orient_session_question_order",
            ),
        ]

    def clean(self):
        super().clean()
        if self.question_type == self.QuestionType.SCALE and not self.scale_kind:
            raise ValidationError({"scale_kind": "Scale questions require a scale kind."})
        if self.question_type != self.QuestionType.SCALE and self.scale_kind:
            raise ValidationError({"scale_kind": "Only scale questions may define a scale kind."})
        if self.reverse_scored and self.question_type != self.QuestionType.SCALE:
            raise ValidationError({"reverse_scored": "Only scale questions may be reverse scored."})

    def __str__(self):
        return f"{self.session_id}:{self.code}"

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).first()
            if original and original.session.status != OrientationSurveySession.Status.DRAFT:
                immutable_fields = (
                    "code",
                    "section_code",
                    "section_title",
                    "text",
                    "question_type",
                    "scale_kind",
                    "is_required",
                    "reverse_scored",
                    "composite_index_code",
                    "display_order",
                )
                if any(getattr(original, field) != getattr(self, field) for field in immutable_fields):
                    raise ValidationError("Published orientation survey questions are immutable.")
        return super().save(*args, **kwargs)


class OrientationSurveyChoice(models.Model):
    question = models.ForeignKey(
        OrientationSurveyQuestion,
        on_delete=models.CASCADE,
        related_name="choices",
    )
    code = models.CharField(max_length=64)
    label = models.CharField(max_length=200)
    emoji = models.CharField(max_length=16, blank=True)
    score = models.PositiveSmallIntegerField(blank=True, null=True)
    display_order = models.PositiveIntegerField()
    allows_other_text = models.BooleanField(default=False)

    class Meta:
        db_table = "orientation_feedback_choices"
        ordering = ["display_order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["question", "code"], name="uq_orient_question_choice"),
            models.UniqueConstraint(
                fields=["question", "display_order"],
                name="uq_orient_question_choice_order",
            ),
            models.CheckConstraint(
                condition=models.Q(score__isnull=True) | models.Q(score__gte=1, score__lte=5),
                name="ck_orient_choice_score",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.select_related("question__session").filter(pk=self.pk).first()
            if original and original.question.session.status != OrientationSurveySession.Status.DRAFT:
                immutable_fields = (
                    "question_id",
                    "code",
                    "label",
                    "emoji",
                    "score",
                    "display_order",
                    "allows_other_text",
                )
                if any(getattr(original, field) != getattr(self, field) for field in immutable_fields):
                    raise ValidationError("Published orientation survey response choices are immutable.")
        return super().save(*args, **kwargs)


class OrientationSurveyParticipation(TimeStampedModel):
    class ValidationMethod(models.TextChoices):
        EMAIL = "EMAIL", "Registered email (legacy)"
        EMAIL_OTP = "EMAIL_OTP", "Registered email with one-time code"

    session = models.ForeignKey(
        OrientationSurveySession,
        on_delete=models.PROTECT,
        related_name="participations",
    )
    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="orientation_feedback_participations",
    )
    eligible_role_codes_snapshot = models.JSONField(default=list, blank=True)
    validation_method = models.CharField(
        max_length=16,
        choices=ValidationMethod.choices,
        default=ValidationMethod.EMAIL,
    )
    validated_at = models.DateTimeField(blank=True, null=True)
    email_otp_hash = models.CharField(max_length=128, blank=True)
    email_otp_sent_at = models.DateTimeField(blank=True, null=True)
    email_otp_expires_at = models.DateTimeField(blank=True, null=True)
    email_otp_failed_attempts = models.PositiveSmallIntegerField(default=0)
    email_verified_at = models.DateTimeField(blank=True, null=True)
    submitted_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "orientation_feedback_participations"
        constraints = [
            models.UniqueConstraint(fields=["session", "user"], name="uq_orient_session_user"),
        ]
        indexes = [
            models.Index(fields=["session", "submitted_at"], name="idx_orient_part_submit"),
        ]


class OrientationSurveyResponse(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    session = models.ForeignKey(
        OrientationSurveySession,
        on_delete=models.PROTECT,
        related_name="responses",
    )
    participation = models.OneToOneField(
        OrientationSurveyParticipation,
        on_delete=models.PROTECT,
        related_name="response",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "orientation_feedback_responses"
        ordering = ["submitted_at", "id"]

    def clean(self):
        super().clean()
        if self.participation_id and self.session_id and self.participation.session_id != self.session_id:
            raise ValidationError("Participation and response must belong to the same survey session.")


class OrientationSurveyAnswer(models.Model):
    response = models.ForeignKey(
        OrientationSurveyResponse,
        on_delete=models.CASCADE,
        related_name="answers",
    )
    question = models.ForeignKey(
        OrientationSurveyQuestion,
        on_delete=models.PROTECT,
        related_name="answers",
    )
    text_value = models.TextField(blank=True)

    class Meta:
        db_table = "orientation_feedback_answers"
        constraints = [
            models.UniqueConstraint(fields=["response", "question"], name="uq_orient_response_question"),
        ]

    def clean(self):
        super().clean()
        if self.response_id and self.question_id and self.response.session_id != self.question.session_id:
            raise ValidationError("Answer question must belong to the response survey session.")


class OrientationSurveyAnswerChoice(models.Model):
    answer = models.ForeignKey(
        OrientationSurveyAnswer,
        on_delete=models.CASCADE,
        related_name="selected_choices",
    )
    choice = models.ForeignKey(
        OrientationSurveyChoice,
        on_delete=models.PROTECT,
        related_name="answer_selections",
    )

    class Meta:
        db_table = "orientation_feedback_answer_choices"
        constraints = [
            models.UniqueConstraint(fields=["answer", "choice"], name="uq_orient_answer_choice"),
        ]

    def clean(self):
        super().clean()
        if self.answer_id and self.choice_id and self.answer.question_id != self.choice.question_id:
            raise ValidationError("Selected choice must belong to the answered question.")
