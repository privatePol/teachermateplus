from django.contrib import admin

from .models import NotificationQueue


@admin.register(NotificationQueue)
class NotificationQueueAdmin(admin.ModelAdmin):
    list_display = ("recipient_user", "channel", "status", "scheduled_at", "sent_at", "reference_type", "reference_id")
    list_filter = ("channel", "status")
    search_fields = ("recipient_user__username", "recipient_user__email", "subject", "reference_type", "reference_id")
