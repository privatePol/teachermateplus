from django.contrib import admin
from django import forms

from .models import Enrollment, EnrollmentAdjustmentLog
from apps.grading.services import EnrollmentSafetyService


class EnrollmentAdminForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        EnrollmentSafetyService.validate_changes_allowed(enrollment=self.instance, cleaned_data=cleaned)
        return cleaned


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    form = EnrollmentAdminForm
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


@admin.register(EnrollmentAdjustmentLog)
class EnrollmentAdjustmentLogAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "source_offering",
        "destination_offering",
        "result",
        "processed_by",
        "processed_at",
    )
    search_fields = (
        "student__student_no",
        "student__last_name",
        "student__first_name",
        "source_offering__course__code",
        "destination_offering__course__code",
    )
    list_filter = ("result", "source_offering__campus", "source_offering__academic_year", "source_offering__term")
    readonly_fields = (
        "student",
        "source_offering",
        "destination_offering",
        "source_enrollment_id",
        "destination_enrollment_id",
        "source_previous_is_active",
        "source_previous_status",
        "destination_is_active",
        "destination_status",
        "batch_reference",
        "reason",
        "processed_by",
        "processed_at",
        "result",
        "warning_flags",
        "impact_snapshot",
        "created_at",
        "updated_at",
    )
