from django.contrib import admin
from django import forms

from .models import ClassListChangeRequest, ClassListChangeRequestItem, Enrollment, EnrollmentAdjustmentLog
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


class ClassListChangeRequestItemInline(admin.TabularInline):
    model = ClassListChangeRequestItem
    extra = 0
    can_delete = False
    fields = ("student", "enrollment", "reference_student_no", "reference_student_name")
    readonly_fields = fields


@admin.register(ClassListChangeRequest)
class ClassListChangeRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "campus",
        "offering",
        "faculty_requester",
        "request_type",
        "status",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )
    search_fields = (
        "offering__course__code",
        "offering__course__title",
        "offering__section__code",
        "faculty_requester__username",
        "remarks",
        "review_remarks",
        "items__reference_student_no",
        "items__reference_student_name",
    )
    list_filter = ("campus", "request_type", "status")
    readonly_fields = (
        "tenant",
        "campus",
        "offering",
        "faculty_requester",
        "request_type",
        "status",
        "remarks",
        "reviewed_by",
        "reviewed_at",
        "review_remarks",
        "created_at",
        "updated_at",
    )
    inlines = [ClassListChangeRequestItemInline]


@admin.register(ClassListChangeRequestItem)
class ClassListChangeRequestItemAdmin(admin.ModelAdmin):
    list_display = ("request", "student", "enrollment", "reference_student_no", "reference_student_name")
    search_fields = ("reference_student_no", "reference_student_name", "student__student_no", "student__last_name")
