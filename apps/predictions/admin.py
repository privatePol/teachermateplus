from django.contrib import admin

from apps.predictions.models import (
    PredictionDirtyQueue,
    PredictionSettingSnapshot,
    PredictionSnapshot,
    PredictionSummarySnapshot,
    PredictionViewLog,
    PredictionWhatIfDraft,
)


@admin.register(PredictionSettingSnapshot)
class PredictionSettingSnapshotAdmin(admin.ModelAdmin):
    list_display = ("tenant", "campus", "assumption_mode", "show_best_case", "show_worst_case", "created_at")
    list_filter = ("tenant", "campus", "assumption_mode")
    search_fields = ("tenant__code", "campus__code")


@admin.register(PredictionSnapshot)
class PredictionSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "offering",
        "template_period",
        "student",
        "current_projected_period_grade",
        "best_case_period_grade",
        "worst_case_period_grade",
        "at_risk_flag",
        "computed_at",
    )
    list_filter = ("tenant", "campus", "template_period", "at_risk_flag", "is_stale")
    search_fields = ("student__student_no", "student__last_name", "offering__course__code")


@admin.register(PredictionSummarySnapshot)
class PredictionSummarySnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "offering",
        "template_period",
        "student_count",
        "at_risk_count",
        "avg_projected_grade",
        "avg_coverage_percent",
        "computed_at",
    )
    list_filter = ("tenant", "campus", "template_period", "is_stale")
    search_fields = ("offering__course__code", "offering__section__code")


@admin.register(PredictionDirtyQueue)
class PredictionDirtyQueueAdmin(admin.ModelAdmin):
    list_display = ("offering", "template_period", "student", "reason", "status", "created_at", "processed_at")
    list_filter = ("tenant", "campus", "reason", "status")
    search_fields = ("offering__course__code", "student__student_no")


@admin.register(PredictionWhatIfDraft)
class PredictionWhatIfDraftAdmin(admin.ModelAdmin):
    list_display = ("scenario_name", "user", "offering", "template_period", "student", "updated_at")
    list_filter = ("tenant", "campus", "template_period")
    search_fields = ("scenario_name", "user__username", "student__student_no")


@admin.register(PredictionViewLog)
class PredictionViewLogAdmin(admin.ModelAdmin):
    list_display = ("viewer", "viewer_role_code", "offering", "template_period", "view_mode", "created_at")
    list_filter = ("tenant", "campus", "view_mode", "viewer_role_code")
    search_fields = ("viewer__username", "offering__course__code", "student__student_no")

