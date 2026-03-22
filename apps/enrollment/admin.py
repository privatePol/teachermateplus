from django.contrib import admin

from .models import Enrollment


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "course_offering",
        "enrollment_status",
        "tenant",
        "campus",
        "encoded_via_portal",
        "is_active",
    )
    search_fields = ("student__student_no", "student__last_name", "course_offering__course__code")
    list_filter = ("tenant", "campus", "enrollment_status", "encoded_via_portal", "is_active")
