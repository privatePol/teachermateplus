from django.contrib import admin

from .models import Campus, Department, Program, SystemSetting, Tenant, TenantApiKey


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "created_at")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "tenant", "is_active")
    search_fields = ("code", "name")
    list_filter = ("tenant", "is_active")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "tenant", "campus", "parent", "operation_branch", "unit_type", "is_active")
    search_fields = ("code", "name")
    list_filter = ("tenant", "campus", "operation_branch", "unit_type", "is_active")


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "level", "tenant", "campus", "department", "is_active")
    search_fields = ("code", "name")
    list_filter = ("tenant", "campus", "department", "is_active")


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("setting_key", "setting_value", "value_type", "tenant", "is_active", "updated_at")
    search_fields = ("setting_key", "setting_value")
    list_filter = ("tenant", "value_type", "is_active")


@admin.register(TenantApiKey)
class TenantApiKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "purpose", "key_prefix", "is_active", "expires_at", "revoked_at", "last_used_at")
    search_fields = ("name", "key_prefix", "tenant__code", "tenant__name")
    list_filter = ("tenant", "purpose", "is_active")
    readonly_fields = ("key_prefix", "key_hash", "last_used_at", "created_at", "updated_at")
