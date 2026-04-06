from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import PortalLoginLockoutState, User


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
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
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
