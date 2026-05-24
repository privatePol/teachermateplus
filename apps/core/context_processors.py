from apps.core.services.menu import MenuService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.academics.services import AcademicGovernanceService
from apps.rbac.models import UserRole


def _admin_role_label(user, *, tenant_id=None, campus_id=None):
    if user.is_superuser:
        return "Superadmin"

    roles_qs = (
        UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            role__role_permissions__permission__code="admin_portal.access",
            role__role_permissions__permission__is_active=True,
        )
        .select_related("role")
        .order_by("role__name", "role__code")
    )
    if tenant_id is not None:
        roles_qs = roles_qs.filter(tenant_id__in=[tenant_id, None])
    if campus_id is not None:
        roles_qs = roles_qs.filter(campus_id__in=[campus_id, None])

    role_names = []
    for user_role in roles_qs:
        label = user_role.role.name or user_role.role.code
        if label not in role_names:
            role_names.append(label)

    if not role_names:
        return ""
    if len(role_names) > 3:
        return ", ".join(role_names[:3]) + f" +{len(role_names) - 3}"
    return ", ".join(role_names)


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
    faculty_grade_prediction_enabled = False
    faculty_at_risk_monitor_enabled = False
    faculty_portal_identity_warning = None
    if portal == "FACULTY":
        faculty_tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
        faculty_quick_tour_enabled = FeatureSettingsService.is_faculty_quick_tour_enabled(
            tenant_id=faculty_tenant_id,
            default=True,
        )
        faculty_grade_prediction_enabled = FeatureSettingsService.can_user_access_grade_prediction(
            user=request.user,
            tenant_id=faculty_tenant_id,
        )
        faculty_at_risk_monitor_enabled = (
            faculty_grade_prediction_enabled
            and FeatureSettingsService.is_grade_prediction_at_risk_enabled(
                tenant_id=faculty_tenant_id,
                default=True,
            )
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
    admin_active_academic_year = None
    admin_active_term = None
    admin_user_role_label = ""
    faculty_active_academic_year = None
    faculty_active_term = None
    if portal == "ADMIN" and scope.get("tenant_id"):
        admin_active_academic_year, admin_active_term = AcademicGovernanceService.resolve_active_scope(
            tenant_id=scope.get("tenant_id")
        )
    if portal == "ADMIN":
        admin_user_role_label = _admin_role_label(
            request.user,
            tenant_id=scope.get("tenant_id"),
            campus_id=scope.get("campus_id"),
        )
    if portal == "FACULTY":
        faculty_tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
        if faculty_tenant_id:
            faculty_active_academic_year, faculty_active_term = AcademicGovernanceService.resolve_active_scope(
                tenant_id=faculty_tenant_id
            )

    return {
        "current_portal": portal,
        "portal_menu": menu,
        "effective_permissions": permissions,
        "faculty_quick_tour_enabled": faculty_quick_tour_enabled,
        "faculty_grade_prediction_enabled": faculty_grade_prediction_enabled,
        "faculty_at_risk_monitor_enabled": faculty_at_risk_monitor_enabled,
        "faculty_portal_identity_warning": faculty_portal_identity_warning,
        "admin_active_academic_year": admin_active_academic_year,
        "admin_active_term": admin_active_term,
        "admin_user_role_label": admin_user_role_label,
        "faculty_active_academic_year": faculty_active_academic_year,
        "faculty_active_term": faculty_active_term,
    }
