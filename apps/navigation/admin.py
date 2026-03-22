from django.contrib import admin

from .models import MenuGroup, MenuItem, MenuItemPermission


@admin.register(MenuGroup)
class MenuGroupAdmin(admin.ModelAdmin):
    list_display = ("portal", "code", "label", "sort_order", "is_active")
    search_fields = ("portal", "code", "label")
    list_filter = ("portal", "is_active")


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("portal", "code", "label", "menu_group", "parent", "sort_order", "is_active")
    search_fields = ("portal", "code", "label", "route_name")
    list_filter = ("portal", "menu_group", "is_active")


@admin.register(MenuItemPermission)
class MenuItemPermissionAdmin(admin.ModelAdmin):
    list_display = ("menu_item", "permission")
    search_fields = ("menu_item__code", "permission__code")
    list_filter = ("permission",)
