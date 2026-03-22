from django.db import models

from apps.core.models import TimeStampedModel


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
