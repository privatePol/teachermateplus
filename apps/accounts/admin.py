from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import LoginOtpChallenge, PortalLoginLockoutState, User, UserDeactivationSchedule


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("username",)
    list_display = (
        "username",
        "email",
        "is_active",
        "is_staff",
        "is_superuser",
        "default_tenant",
        "default_campus",
        "default_department",
    )
    list_filter = ("is_active", "is_staff", "is_superuser", "default_tenant", "default_campus", "default_department")
    search_fields = ("username", "email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "middle_name", "last_name", "email")}),
        ("Scope", {"fields": ("default_tenant", "default_campus", "default_department")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "faculty_quick_tour_disabled", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "email", "password1", "password2", "is_staff", "is_superuser"),
            },
        ),
    )


@admin.register(PortalLoginLockoutState)
class PortalLoginLockoutStateAdmin(admin.ModelAdmin):
    list_display = (
        "username",
        "portal_code",
        "failed_attempt_count",
        "locked_until",
        "last_failed_at",
        "user",
    )
    list_filter = ("portal_code",)
    search_fields = ("username", "user__username", "user__email")
    autocomplete_fields = ("user",)


@admin.register(LoginOtpChallenge)
class LoginOtpChallengeAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "portal_code",
        "sent_to_email",
        "expires_at",
        "consumed_at",
        "attempt_count",
        "created_at",
    )
    list_filter = ("portal_code", "consumed_at")
    search_fields = ("user__username", "user__email", "sent_to_email")
    autocomplete_fields = ("user",)
    readonly_fields = ("code_hash", "created_at")


@admin.register(UserDeactivationSchedule)
class UserDeactivationScheduleAdmin(admin.ModelAdmin):
    list_display = ("user", "scheduled_for", "status", "scheduled_by_user", "applied_at", "cancelled_at")
    list_filter = ("status", "scheduled_for")
    search_fields = ("user__username", "user__email", "user__first_name", "user__last_name")
    autocomplete_fields = ("user", "scheduled_by_user", "cancelled_by_user", "applied_by_user")
    readonly_fields = ("created_at", "updated_at", "applied_at", "cancelled_at")
