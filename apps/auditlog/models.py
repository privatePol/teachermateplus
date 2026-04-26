from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    class Portal(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        FACULTY = "FACULTY", "Faculty"
        SYSTEM = "SYSTEM", "System"

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        blank=True,
        null=True,
    )
    portal = models.CharField(max_length=10, choices=Portal.choices, default=Portal.SYSTEM)
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=120)
    entity_id = models.CharField(max_length=64, blank=True, null=True)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.SET_NULL, related_name="audit_logs", blank=True, null=True
    )
    campus = models.ForeignKey(
        "tenants.Campus", on_delete=models.SET_NULL, related_name="audit_logs", blank=True, null=True
    )
    route_name = models.CharField(max_length=255, blank=True, null=True)
    http_method = models.CharField(max_length=16, blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=512, blank=True, null=True)
    before_json = models.JSONField(blank=True, null=True)
    after_json = models.JSONField(blank=True, null=True)
    metadata_json = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "campus", "created_at"], name="idx_audit_scope_created"),
            models.Index(fields=["actor_user", "portal", "created_at"], name="idx_audit_actor_portal"),
            models.Index(fields=["entity_type", "action", "created_at"], name="idx_audit_entity_action"),
        ]

    def __str__(self):
        return f"{self.portal}:{self.action}:{self.entity_type}:{self.entity_id or '-'}"
