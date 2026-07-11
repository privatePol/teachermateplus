from django.db import models

from apps.core.models import TimeStampedModel


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
