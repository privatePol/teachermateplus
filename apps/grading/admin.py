from django.contrib import admin

from .models import (
    CorrectionApprovalRouteRule,
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    GradeCorrectionApprovalStep,
    GradeCorrectionAttachment,
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeCorrectionUnlockWindow,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradeActivity,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    TemplateHotfixRequest,
    TenantGradingProfile,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)


@admin.register(GradingTemplate)
class GradingTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "code",
        "name",
        "default_base_value",
        "approval_status",
        "is_published",
        "is_active",
    )
    search_fields = ("code", "name")
    list_filter = ("tenant", "approval_status", "is_published", "is_active")


@admin.register(GradingTemplatePeriod)
class GradingTemplatePeriodAdmin(admin.ModelAdmin):
    list_display = ("template", "code", "name", "sequence_no", "weight_percentage", "is_active")
    search_fields = ("code", "name", "template__code")
    list_filter = ("template", "is_active")


@admin.register(GradingTemplateComponent)
class GradingTemplateComponentAdmin(admin.ModelAdmin):
    list_display = ("template_period", "code", "name", "weight_percentage", "sort_order", "is_active")
    search_fields = ("code", "name", "template_period__template__code")
    list_filter = ("is_active",)


@admin.register(GradingTemplateSubcomponent)
class GradingTemplateSubcomponentAdmin(admin.ModelAdmin):
    list_display = (
        "template_component",
        "code",
        "name",
        "weight_percentage",
        "is_attendance_component",
        "admin_locked",
        "is_active",
    )
    search_fields = ("code", "name", "template_component__code")
    list_filter = ("is_attendance_component", "admin_locked", "is_active")


@admin.register(GradingTemplateDetail)
class GradingTemplateDetailAdmin(admin.ModelAdmin):
    list_display = ("template_subcomponent", "code", "name", "weight_percentage", "admin_locked", "is_active")
    search_fields = ("code", "name", "template_subcomponent__code", "template_subcomponent__template_component__code")
    list_filter = ("admin_locked", "is_active")


@admin.register(CourseTemplateAssignment)
class CourseTemplateAssignmentAdmin(admin.ModelAdmin):
    list_display = ("course", "grading_template", "effective_from_term", "is_active")
    search_fields = ("course__code", "grading_template__code")
    list_filter = ("is_active",)


@admin.register(TenantGradingProfile)
class TenantGradingProfileAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "profile_code",
        "profile_name",
        "campus",
        "department",
        "program",
        "course",
        "course_type",
        "term_type",
        "grading_template",
        "priority",
        "passing_grade_threshold",
        "period_grade_formula_mode",
        "is_default",
        "is_active",
    )
    search_fields = ("profile_code", "profile_name", "course__code", "course_type", "grading_template__code")
    list_filter = ("tenant", "campus", "term_type", "period_grade_formula_mode", "is_default", "is_active")


@admin.register(CourseBaseValueOverride)
class CourseBaseValueOverrideAdmin(admin.ModelAdmin):
    list_display = ("course", "base_value", "effective_from_term", "is_active")
    search_fields = ("course__code",)
    list_filter = ("is_active",)


@admin.register(GradeActivity)
class GradeActivityAdmin(admin.ModelAdmin):
    list_display = (
        "offering",
        "template_period",
        "template_component",
        "template_subcomponent",
        "template_detail",
        "title",
        "total_score",
        "activity_date",
        "is_active",
    )
    search_fields = ("title", "offering__course__code", "offering__section__code")
    list_filter = ("template_period", "template_component", "is_active")


@admin.register(StudentActivityScore)
class StudentActivityScoreAdmin(admin.ModelAdmin):
    list_display = ("activity", "student", "raw_score", "computed_score", "is_excused", "is_active")
    search_fields = ("student__student_no", "student__last_name", "activity__title")
    list_filter = ("is_excused", "is_active")


@admin.register(StudentPeriodGrade)
class StudentPeriodGradeAdmin(admin.ModelAdmin):
    list_display = (
        "offering",
        "template_period",
        "student",
        "class_standing_grade",
        "exam_grade",
        "period_grade",
        "is_finalized",
    )
    search_fields = ("student__student_no", "student__last_name", "offering__course__code")
    list_filter = ("template_period", "is_finalized")


@admin.register(StudentFinalGrade)
class StudentFinalGradeAdmin(admin.ModelAdmin):
    list_display = ("offering", "student", "final_grade", "is_submitted")
    search_fields = ("student__student_no", "student__last_name", "offering__course__code")
    list_filter = ("is_submitted",)


@admin.register(GradingPeriodLock)
class GradingPeriodLockAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "campus",
        "term",
        "period_code",
        "scope_type",
        "course_offering",
        "is_locked",
        "deadline_at",
    )
    search_fields = ("period_code", "campus__code", "term__code", "course_offering__course__code")
    list_filter = ("scope_type", "is_locked", "tenant", "campus")


@admin.register(GradeSubmission)
class GradeSubmissionAdmin(admin.ModelAdmin):
    list_display = ("offering", "template_period", "status", "submitted_by_user", "submitted_at", "reopened_at")
    search_fields = ("offering__course__code", "offering__section__code", "template_period__code")
    list_filter = ("status", "offering__tenant", "offering__campus")


@admin.register(GradeSubmissionReopenRequest)
class GradeSubmissionReopenRequestAdmin(admin.ModelAdmin):
    list_display = ("submission", "requested_by_user", "status", "created_at", "reviewed_at")
    search_fields = ("submission__offering__course__code", "submission__offering__section__code", "requested_by_user__username")
    list_filter = ("status", "tenant", "campus")


@admin.register(GradeCorrectionRequest)
class GradeCorrectionRequestAdmin(admin.ModelAdmin):
    list_display = (
        "offering",
        "template_period",
        "requested_by_user",
        "faculty_department",
        "approval_route",
        "status",
        "created_at",
        "reviewed_at",
    )
    search_fields = ("offering__course__code", "offering__section__code", "requested_by_user__username")
    list_filter = ("status", "offering__tenant", "offering__campus")


@admin.register(GradeCorrectionApprovalStep)
class GradeCorrectionApprovalStepAdmin(admin.ModelAdmin):
    list_display = (
        "correction_request",
        "step_order",
        "approver_role",
        "requires_same_department",
        "status",
        "reviewed_by_user",
        "reviewed_at",
    )
    search_fields = (
        "correction_request__offering__course__code",
        "correction_request__offering__section__code",
        "approver_role__code",
    )
    list_filter = ("status", "approver_role", "requires_same_department")


@admin.register(GradeCorrectionRequestItem)
class GradeCorrectionRequestItemAdmin(admin.ModelAdmin):
    list_display = ("correction_request", "requested_action", "student", "grade_activity", "is_active")
    search_fields = ("correction_request__offering__course__code", "student__student_no", "grade_activity__title")
    list_filter = ("requested_action", "is_active")


@admin.register(GradeCorrectionAttachment)
class GradeCorrectionAttachmentAdmin(admin.ModelAdmin):
    list_display = ("correction_request", "original_filename", "content_type", "file_size_bytes", "uploaded_by_user", "created_at")
    search_fields = (
        "correction_request__offering__course__code",
        "original_filename",
        "uploaded_by_user__username",
    )


@admin.register(GradeCorrectionUnlockWindow)
class GradeCorrectionUnlockWindowAdmin(admin.ModelAdmin):
    list_display = ("correction_request", "offering", "template_period", "start_at", "end_at", "is_active", "is_consumed")
    search_fields = ("offering__course__code", "offering__section__code")
    list_filter = ("is_active", "is_consumed")


@admin.register(TemplateHotfixRequest)
class TemplateHotfixRequestAdmin(admin.ModelAdmin):
    list_display = (
        "template",
        "apply_mode",
        "status",
        "requested_by_user",
        "reviewed_by_user",
        "affected_offering_count",
        "recomputed_offering_count",
        "created_at",
    )
    search_fields = ("template__code", "requested_by_user__username")
    list_filter = ("status", "apply_mode", "tenant")


@admin.register(CorrectionApprovalRouteRule)
class CorrectionApprovalRouteRuleAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "faculty_department",
        "route_mode",
        "step1_role",
        "final_role",
        "is_active",
    )
    search_fields = (
        "tenant__code",
        "faculty_department__code",
        "faculty_department__name",
        "step1_role__code",
        "final_role__code",
    )
    list_filter = ("tenant", "route_mode", "is_active")
