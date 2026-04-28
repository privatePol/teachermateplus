from apps.core.services.menu import MenuService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.rbac.models import UserRole


def portal_menu(request):
    portal = None
    if request.path.startswith("/admin-portal/"):
        portal = "ADMIN"
    elif request.path.startswith("/faculty/"):
        portal = "FACULTY"

    if not portal or not request.user.is_authenticated:
        return {"current_portal": portal, "portal_menu": [], "effective_permissions": set()}

    scope = getattr(request, "scope", {})
    permissions = PermissionService.get_effective_permission_codes(
        request.user,
        tenant_id=scope.get("tenant_id"),
        campus_id=scope.get("campus_id"),
    )
    menu = MenuService.get_menu_tree(
        request.user,
        portal=portal,
        tenant_id=scope.get("tenant_id"),
        campus_id=scope.get("campus_id"),
        effective_codes=permissions,
    )
    faculty_quick_tour_enabled = False
    faculty_portal_identity_warning = None
    if portal == "FACULTY":
        faculty_quick_tour_enabled = FeatureSettingsService.is_faculty_quick_tour_enabled(
            tenant_id=scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None),
            default=True,
        )
        has_faculty_role = UserRole.objects.filter(
            user=request.user,
            is_active=True,
            role__is_active=True,
            role__code="FACULTY",
        ).exists()
        if request.user.is_superuser or not has_faculty_role:
            faculty_portal_identity_warning = (
                "You are viewing the Faculty Portal using "
                f"{request.user.full_name or request.user.username}. "
                "Browser tabs share the same login session. Use an incognito window, separate browser, or separate "
                "browser profile when testing a different faculty account."
            )
    return {
        "current_portal": portal,
        "portal_menu": menu,
        "effective_permissions": permissions,
        "faculty_quick_tour_enabled": faculty_quick_tour_enabled,
        "faculty_portal_identity_warning": faculty_portal_identity_warning,
    }
