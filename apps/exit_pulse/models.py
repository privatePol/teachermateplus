from __future__ import annotations

import secrets
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimeStampedModel


def generate_exit_pulse_token() -> str:
    return secrets.token_urlsafe(32)


class ExitPulseSession(TimeStampedModel):
    class QuestionCode(models.TextChoices):
        UNDERSTANDING = "UNDERSTANDING", "How well do you understand today’s topic?"
        APPLICATION_CONFIDENCE = (
            "APPLICATION_CONFIDENCE",
            "How confident are you that you can apply today’s lesson?",
        )
        NEEDS_EXPLANATION = (
            "NEEDS_EXPLANATION",
            "Does today’s topic need further explanation or examples?",
        )
        CUSTOM = "CUSTOM", "Custom Question"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        LIVE = "LIVE", "Live"
        CLOSED = "CLOSED", "Closed"
        EXPIRED = "EXPIRED", "Expired"
        CANCELLED = "CANCELLED", "Cancelled"

    FEEDBACK_REVIEW_PROMPT = "Which part of today’s topic should be reviewed?"
    FEEDBACK_LEARNED_PROMPT = "What is one thing you learned today?"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="exit_pulse_sessions")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="exit_pulse_sessions")
    faculty_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="exit_pulse_sessions",
    )
    faculty_assignment = models.ForeignKey(
        "academics.FacultyAssignment",
        on_delete=models.PROTECT,
        related_name="exit_pulse_sessions",
    )
    course_offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="exit_pulse_sessions",
    )
    academic_year = models.ForeignKey(
        "academics.AcademicYear",
        on_delete=models.PROTECT,
        related_name="exit_pulse_sessions",
    )
    term = models.ForeignKey(
        "academics.Term",
        on_delete=models.PROTECT,
        related_name="exit_pulse_sessions",
    )
    course = models.ForeignKey(
        "academics.Course",
        on_delete=models.PROTECT,
        related_name="exit_pulse_sessions",
    )
    section = models.ForeignKey(
        "academics.Section",
        on_delete=models.PROTECT,
        related_name="exit_pulse_sessions",
    )
    topic = models.CharField(max_length=200)
    question_code = models.CharField(max_length=32, choices=QuestionCode.choices)
    question_text_snapshot = models.CharField(max_length=250)
    custom_question = models.CharField(max_length=250, blank=True)
    allow_written_feedback = models.BooleanField(default=False)
    feedback_review_enabled = models.BooleanField(default=False)
    feedback_review_prompt_snapshot = models.CharField(max_length=200, blank=True)
    feedback_learned_enabled = models.BooleanField(default=False)
    feedback_learned_prompt_snapshot = models.CharField(max_length=200, blank=True)
    public_token = models.CharField(max_length=64, unique=True, default=generate_exit_pulse_token, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    started_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    extended_at = models.DateTimeField(blank=True, null=True)
    extension_count = models.PositiveSmallIntegerField(default=0)
    enrollment_count_snapshot = models.PositiveIntegerField(
        blank=True,
        null=True,
        editable=False,
        help_text="Eligible enrollment count captured when the session first starts; null for legacy sessions.",
    )
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_exit_pulse_sessions",
    )

    class Meta:
        db_table = "exit_pulse_sessions"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["faculty_user", "status", "created_at"], name="idx_pulse_fac_status"),
            models.Index(fields=["faculty_assignment", "created_at"], name="idx_pulse_assignment"),
            models.Index(fields=["status", "expires_at"], name="idx_pulse_status_exp"),
            models.Index(fields=["tenant", "campus", "created_at"], name="idx_pulse_scope_time"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(extension_count__gte=0, extension_count__lte=1),
                name="ck_pulse_extension_once",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        assignment = self.faculty_assignment
        offering = self.course_offering
        if assignment.faculty_user_id != self.faculty_user_id:
            errors["faculty_assignment"] = "The assignment does not belong to the selected faculty member."
        if assignment.offering_id != self.course_offering_id:
            errors["faculty_assignment"] = "The assignment does not belong to the selected course offering."
        expected = {
            "tenant": offering.tenant_id,
            "campus": offering.campus_id,
            "academic_year": offering.academic_year_id,
            "term": offering.term_id,
            "course": offering.course_id,
            "section": offering.section_id,
        }
        for field_name, expected_id in expected.items():
            if getattr(self, f"{field_name}_id") != expected_id:
                errors[field_name] = "Exit Pulse scope must match the assigned course offering."
        if not self.allow_written_feedback and (
            self.feedback_review_enabled or self.feedback_learned_enabled
        ):
            errors["allow_written_feedback"] = "Written prompts require written feedback to be enabled."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.course_offering_id}:{self.topic}:{self.status}"


class ExitPulseResponse(TimeStampedModel):
    IDENTITY_FIELDS = (
        "student_enrollment_id",
        "privacy_notice_version",
        "privacy_notice_acknowledged_at",
    )

    class ResponseCode(models.TextChoices):
        CONFIDENT = "CONFIDENT", "I understand it well and feel confident"
        MOSTLY_UNDERSTOOD = "MOSTLY_UNDERSTOOD", "I understand most of it"
        NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION", "I need a little clarification"
        NEEDS_PRACTICE = "NEEDS_PRACTICE", "I need more examples or practice"

    session = models.ForeignKey(
        "exit_pulse.ExitPulseSession",
        on_delete=models.CASCADE,
        related_name="responses",
    )
    student_enrollment = models.ForeignKey(
        "enrollment.Enrollment",
        on_delete=models.PROTECT,
        related_name="exit_pulse_responses",
        blank=True,
        null=True,
        editable=False,
        help_text="Validated class enrollment; null only for responses created before identity validation.",
    )
    privacy_notice_version = models.CharField(
        max_length=32,
        blank=True,
        default="",
        editable=False,
    )
    privacy_notice_acknowledged_at = models.DateTimeField(
        blank=True,
        null=True,
        editable=False,
    )
    response_code = models.CharField(max_length=32, choices=ResponseCode.choices)
    feedback_review = models.CharField(max_length=200, blank=True)
    feedback_learned = models.CharField(max_length=200, blank=True)
    anonymous_token_hash = models.CharField(max_length=64, blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    technical_identifier_expires_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "exit_pulse_responses"
        ordering = ["submitted_at", "id"]
        indexes = [
            models.Index(fields=["session", "response_code"], name="idx_pulse_resp_code"),
            models.Index(fields=["technical_identifier_expires_at"], name="idx_pulse_ident_exp"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "student_enrollment"],
                name="uq_pulse_response_enrollment",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        student_enrollment__isnull=True,
                        privacy_notice_version="",
                        privacy_notice_acknowledged_at__isnull=True,
                    )
                    | (
                        models.Q(
                            student_enrollment__isnull=False,
                            privacy_notice_acknowledged_at__isnull=False,
                        )
                        & ~models.Q(privacy_notice_version="")
                    )
                ),
                name="ck_pulse_response_identity_notice",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    response_code__in=[
                        "CONFIDENT",
                        "MOSTLY_UNDERSTOOD",
                        "NEEDS_CLARIFICATION",
                        "NEEDS_PRACTICE",
                    ]
                ),
                name="ck_pulse_response_code",
            ),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.response_code}:{self.submitted_at:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(*self.IDENTITY_FIELDS).first()
            if original and any(
                original[field_name] != getattr(self, field_name)
                for field_name in self.IDENTITY_FIELDS
            ):
                raise ValidationError("Exit Pulse response identity and privacy evidence are immutable.")
        return super().save(*args, **kwargs)
