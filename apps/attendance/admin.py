from django.contrib import admin

from .models import AttendanceRecord, AttendanceSession


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ("offering", "template_period", "session_date", "title", "is_active")
    search_fields = ("offering__course__code", "offering__section__code", "title")
    list_filter = ("template_period", "is_active")


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("session", "student", "status_code", "is_active")
    search_fields = ("student__student_no", "student__last_name")
    list_filter = ("status_code", "is_active")
