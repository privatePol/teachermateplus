from django.contrib import admin

from .models import (
    FacultyMemo,
    FacultyReminder,
    FacultyReminderEmailQueue,
    NotificationQueue,
    SubmissionNonComplianceNotice,
)


@admin.register(FacultyReminder)
class FacultyReminderAdmin(admin.ModelAdmin):
    list_display = ("faculty_user", "title", "reminder_type", "remind_at", "due_at", "completed_at", "send_email")
    list_filter = ("reminder_type", "send_email", "is_active")
    search_fields = ("faculty_user__username", "faculty_user__email", "title", "notes", "period_label")


@admin.register(FacultyMemo)
class FacultyMemoAdmin(admin.ModelAdmin):
    list_display = ("title", "faculty_user", "memo_type", "offering", "student", "is_pinned", "is_active", "updated_at")
    list_filter = ("memo_type", "is_pinned", "is_active")
    search_fields = ("faculty_user__username", "faculty_user__email", "title", "body")


@admin.register(FacultyReminderEmailQueue)
class FacultyReminderEmailQueueAdmin(admin.ModelAdmin):
    list_display = ("recipient_user", "status", "scheduled_at", "sent_at", "subject", "dedupe_key")
    list_filter = ("status",)
    search_fields = ("recipient_user__username", "recipient_user__email", "subject", "dedupe_key")


@admin.register(NotificationQueue)
class NotificationQueueAdmin(admin.ModelAdmin):
    list_display = ("recipient_user", "channel", "status", "scheduled_at", "sent_at", "reference_type", "reference_id")
    list_filter = ("channel", "status")
    search_fields = ("recipient_user__username", "recipient_user__email", "subject", "reference_type", "reference_id")


@admin.register(SubmissionNonComplianceNotice)
class SubmissionNonComplianceNoticeAdmin(admin.ModelAdmin):
    list_display = (
        "faculty_user",
        "offering",
        "template_period",
        "notice_level",
        "sequence_no",
        "status",
        "issued_at",
        "resolved_at",
    )
    list_filter = ("notice_level", "status", "tenant", "campus", "department")
    search_fields = (
        "faculty_user__username",
        "faculty_user__email",
        "offering__course__code",
        "offering__section__code",
        "title",
    )
