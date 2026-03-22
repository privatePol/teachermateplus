from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "portal", "action", "entity_type", "entity_id", "actor_user", "tenant", "campus")
    search_fields = ("action", "entity_type", "entity_id", "actor_user__username", "route_name")
    list_filter = ("portal", "action", "tenant", "campus", "created_at")
    readonly_fields = (
        "created_at",
        "actor_user",
        "portal",
        "action",
        "entity_type",
        "entity_id",
        "tenant",
        "campus",
        "route_name",
        "http_method",
        "ip_address",
        "user_agent",
        "before_json",
        "after_json",
        "metadata_json",
    )
