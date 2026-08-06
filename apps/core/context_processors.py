from apps.core.services.menu import MenuService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.academics.services import AcademicGovernanceService
from apps.rbac.models import UserRole


_DEPARTMENTAL_EXAM_ACTIVE_ROUTES = {
    "FACULTY": {
        "DE_EXAM_FACULTY_CONTRIBUTIONS": {
            "departmental_exams:contribution_list",
            "departmental_exams:contribution_workspace",
            "departmental_exams:question_create",
            "departmental_exams:question_edit",
            "departmental_exams:question_delete",
            "departmental_exams:question_reorder",
            "departmental_exams:csv_template",
            "departmental_exams:csv_upload",
            "departmental_exams:csv_preview",
            "departmental_exams:csv_error_report",
            "departmental_exams:csv_confirm",
            "departmental_exams:contribution_submit",
        },
    },
    "ADMIN": {
        "DE_EXAM_CYCLES": {
            "departmental_exams:cycle_list",
            "departmental_exams:cycle_create",
            "departmental_exams:cycle_configuration",
            "departmental_exams:cycle_apply_defaults",
            "departmental_exams:cycle_open",
            "departmental_exams:cycle_close",
            "departmental_exams:cycle_course_list",
        },
        "DE_EXAM_ASSIGNED_COURSES": {
            "departmental_exams:assigned_course_examinations",
            "departmental_exams:cycle_course_administration",
            "departmental_exams:cycle_course_exempt",
            "departmental_exams:cycle_course_restore",
            "departmental_exams:course_configuration",
            "departmental_exams:course_remove_overrides",
            "departmental_exams:course_contribution_open",
            "departmental_exams:course_contribution_close",
            "departmental_exams:course_contribution_reopen",
            "departmental_exams:course_configuration_revert",
        },
        "DE_EXAM_CONTRIBUTOR_MONITORING": {
            "departmental_exams:contributor_monitoring",
            "departmental_exams:roster_action",
        },
    },
}


def _mark_departmental_exam_active_menu(request, menu, portal):
    resolver_match = getattr(request, "resolver_match", None)
    current_route = getattr(resolver_match, "view_name", None)
    active_code = next(
        (
            code
            for code, route_names in _DEPARTMENTAL_EXAM_ACTIVE_ROUTES.get(
                portal, {}
            ).items()
            if current_route in route_names
        ),
        None,
    )
    departmental_codes = set(
        _DEPARTMENTAL_EXAM_ACTIVE_ROUTES.get(portal, {})
    )
    for group in menu:
        for node in group["items"]:
            if node["item"].code in departmental_codes:
                node["is_active"] = node["item"].code == active_code


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
    if portal in {"ADMIN", "FACULTY"}:
        # Departmental Exam Stage 5 menu visibility has owner/source and
        # exact-responsibility requirements beyond a generic permission row.
        # This read-only filter keeps navigation aligned with direct routes.
        from apps.departmental_exams.contribution_selectors import (
            ContributionMonitoringSelector,
            ContributionSelector,
        )

        if portal == "FACULTY":
            stage5_visible = ContributionSelector.faculty_navigation_visible(
                user=request.user,
                tenant_id=scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None),
                campus_id=scope.get("campus_id"),
            )
            stage5_code = "DE_EXAM_FACULTY_CONTRIBUTIONS"
        else:
            stage5_visible = ContributionMonitoringSelector.navigation_visible(
                user=request.user,
                tenant_id=scope.get("tenant_id"),
            )
            stage5_code = "DE_EXAM_CONTRIBUTOR_MONITORING"
        if not stage5_visible:
            for group in menu:
                group["items"] = [
                    node for node in group["items"] if node["item"].code != stage5_code
                ]
            menu = [group for group in menu if group["items"]]
    _mark_departmental_exam_active_menu(request, menu, portal)
    admin_academic_performance_insights_enabled = False
    faculty_quick_tour_enabled = False
    faculty_grade_prediction_enabled = False
    faculty_at_risk_monitor_enabled = False
    faculty_academic_interventions_enabled = False
    exit_pulse_enabled = False
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
        faculty_academic_interventions_enabled = (
            "academic_interventions.manage_own" in permissions
            and FeatureSettingsService.is_student_academic_intervention_tracking_enabled(
                tenant_id=faculty_tenant_id,
                default=False,
            )
        )
        exit_pulse_enabled = (
            "exit_pulse.use" in permissions
            and FeatureSettingsService.is_exit_pulse_enabled(
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
        admin_academic_performance_insights_enabled = (
            FeatureSettingsService.is_academic_performance_insights_enabled(
                tenant_id=scope.get("tenant_id"),
                default=False,
            )
        )
        if not admin_academic_performance_insights_enabled:
            for group in menu:
                group["items"] = [
                    node
                    for node in group["items"]
                    if node["item"].code != "ACADEMIC_PERFORMANCE_INSIGHTS"
                ]
            menu = [group for group in menu if group["items"]]
        if not FeatureSettingsService.is_student_academic_intervention_tracking_enabled(
            tenant_id=scope.get("tenant_id"), default=False
        ):
            for group in menu:
                group["items"] = [
                    node for node in group["items"] if node["item"].code != "ACADEMIC_INTERVENTION_TRACKING"
                ]
            menu = [group for group in menu if group["items"]]
        admin_orientation_feedback_enabled = FeatureSettingsService.is_orientation_feedback_enabled(
            tenant_id=scope.get("tenant_id"),
            default=True,
        )
        if not admin_orientation_feedback_enabled:
            for group in menu:
                group["items"] = [
                    node
                    for node in group["items"]
                    if node["item"].code != "ORIENTATION_FEEDBACK"
                ]
            menu = [group for group in menu if group["items"]]
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
        "faculty_academic_interventions_enabled": faculty_academic_interventions_enabled,
        "exit_pulse_enabled": exit_pulse_enabled,
        "faculty_portal_identity_warning": faculty_portal_identity_warning,
        "admin_active_academic_year": admin_active_academic_year,
        "admin_active_term": admin_active_term,
        "admin_user_role_label": admin_user_role_label,
        "admin_academic_performance_insights_enabled": admin_academic_performance_insights_enabled,
        "faculty_active_academic_year": faculty_active_academic_year,
        "faculty_active_term": faculty_active_term,
    }
