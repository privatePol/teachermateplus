from django.contrib import admin

from .models import Campus, Department, Program, SystemSetting, Tenant


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
    list_display = ("code", "name", "tenant", "campus", "is_active")
    search_fields = ("code", "name")
    list_filter = ("tenant", "campus", "is_active")


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
