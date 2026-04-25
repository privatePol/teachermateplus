from apps.core.services.menu import MenuService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService


def portal_menu(request):
    portal = None
    if request.path.startswith("/admin-portal/"):
        portal = "ADMIN"
    elif request.path.startswith("/faculty/"):
        portal = "FACULTY"

    if not portal or not request.user.is_authenticated:
        return {"current_portal": portal, "portal_menu": [], "effective_permissions": set()}

    scope = getattr(request, "scope", {})
    menu = MenuService.get_menu_tree(
        request.user,
        portal=portal,
        tenant_id=scope.get("tenant_id"),
        campus_id=scope.get("campus_id"),
    )
    permissions = PermissionService.get_effective_permission_codes(
        request.user,
        tenant_id=scope.get("tenant_id"),
        campus_id=scope.get("campus_id"),
    )
    faculty_quick_tour_enabled = False
    if portal == "FACULTY":
        faculty_quick_tour_enabled = FeatureSettingsService.is_faculty_quick_tour_enabled(
            tenant_id=scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None),
            default=True,
        )
    return {
        "current_portal": portal,
        "portal_menu": menu,
        "effective_permissions": permissions,
        "faculty_quick_tour_enabled": faculty_quick_tour_enabled,
    }
