import uuid

from django.db import models

from apps.core.models import TimeStampedModel


class FacultyGradebookModification(models.Model):
    """Latest observed faculty data edit; never inferred from computed grades."""

    faculty_user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    offering = models.ForeignKey("academics.CourseOffering", on_delete=models.CASCADE)
    template_period = models.ForeignKey("grading.GradingTemplatePeriod", on_delete=models.CASCADE)
    modified_at = models.DateTimeField()

    class Meta:
        db_table = "faculty_gradebook_modifications"
        constraints = [
            models.UniqueConstraint(
                fields=["faculty_user", "offering", "template_period"],
                name="uq_faculty_gradebook_mod",
            ),
        ]
        indexes = [models.Index(fields=["faculty_user", "modified_at"], name="idx_faculty_gradebook_mod")]


class FacultyDashboardVisitState(models.Model):
    """One locked coalescing state per browser session, owner and scope."""

    identity_key = models.CharField(max_length=64, unique=True)
    faculty_user = models.ForeignKey("accounts.User", on_delete=models.CASCADE)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, null=True)
    campus = models.ForeignKey("tenants.Campus", on_delete=models.CASCADE, null=True)
    window_token = models.UUIDField(default=uuid.uuid4)
    window_started_at = models.DateTimeField()
    previous_visit_at = models.DateTimeField(null=True)
    recorded_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "faculty_dashboard_visit_states"


class FacultyFeedback(TimeStampedModel):
    class Rating(models.TextChoices):
        HAPPY = "HAPPY", "Happy"
        NEUTRAL = "NEUTRAL", "Neutral"
        SAD = "SAD", "Sad"

    faculty_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="faculty_feedback_submissions",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="faculty_feedback",
    )
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="faculty_feedback",
        blank=True,
        null=True,
    )
    rating = models.CharField(max_length=20, choices=Rating.choices)
    suggestion = models.TextField(blank=True)
    page_path = models.CharField(max_length=255, blank=True)
    route_name = models.CharField(max_length=128, blank=True)
    feature_code = models.CharField(max_length=80, blank=True)
    referrer_path = models.CharField(max_length=255, blank=True)
    app_version = models.CharField(max_length=64, blank=True)
    user_agent_summary = models.CharField(max_length=160, blank=True)

    class Meta:
        db_table = "faculty_feedback"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "created_at"], name="idx_facfb_tenant_time"),
            models.Index(fields=["campus", "created_at"], name="idx_facfb_campus_time"),
            models.Index(fields=["rating", "created_at"], name="idx_facfb_rating_time"),
            models.Index(fields=["faculty_user", "created_at"], name="idx_facfb_user_time"),
            models.Index(fields=["route_name", "created_at"], name="idx_facfb_route_time"),
        ]

    def __str__(self):
        return f"{self.faculty_user_id}:{self.rating}:{self.created_at:%Y-%m-%d %H:%M}"
