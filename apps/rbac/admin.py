from django.contrib import admin

from .models import Permission, Role, RolePermission, UserPermission, UserRole


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_system", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_system", "is_active")


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("code", "module", "action", "is_active")
    search_fields = ("code", "module", "action")
    list_filter = ("module", "action", "is_active")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "tenant", "campus", "department", "is_active", "assigned_at")
    search_fields = ("user__username", "role__code")
    list_filter = ("role", "tenant", "campus", "department", "is_active")


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display = ("role", "permission")
    search_fields = ("role__code", "permission__code")
    list_filter = ("role",)


@admin.register(UserPermission)
class UserPermissionAdmin(admin.ModelAdmin):
    list_display = ("user", "permission", "grant_type", "tenant", "campus", "created_at")
    search_fields = ("user__username", "permission__code")
    list_filter = ("grant_type", "tenant", "campus")
