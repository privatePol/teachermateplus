from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.rbac.models import UserPermission, UserRole


VIEW_PERMISSION = "faculty_attendance.view"
MANAGE_SCHEDULES_PERMISSION = "faculty_attendance.manage_schedules"
MANAGE_COVERAGE_PERMISSION = "faculty_attendance.manage_coverage"
MANAGE_MEETINGS_PERMISSION = "faculty_attendance.manage_meetings"
MANAGE_SUBSTITUTIONS_PERMISSION = "faculty_attendance.manage_substitutions"
RECONCILE_PERMISSION = "faculty_attendance.reconcile"
ENCODE_PERMISSION = "faculty_attendance.encode"
CORRECT_PERMISSION = "faculty_attendance.correct"
MANAGE_ROUTES_PERMISSION = "faculty_attendance.manage_routes"
PRINT_PERMISSION = "faculty_attendance.print"
PUBLISH_PERMISSION = "faculty_attendance.publish_cutoff"
PUBLISH_FACULTY_PERMISSION = "faculty_attendance.publish_faculty_cutoff"
DTR_VIEW_PERMISSION = "faculty_attendance.dtr_view"
DTR_EDIT_PERMISSION = "faculty_attendance.dtr_edit"
DTR_FINALIZE_PERMISSION = "faculty_attendance.dtr_finalize"
DTR_PRINT_PERMISSION = "faculty_attendance.dtr_print"
DTR_AC_SUMMARY_PERMISSION = "faculty_attendance.dtr_ac_summary"


def _has_department_scope(*, user, permission_code, tenant_id, campus_id, department_id):
    if user.is_superuser:
        return True
    direct_allow = UserPermission.objects.filter(
        user=user,
        permission__code=permission_code,
        permission__is_active=True,
        grant_type=UserPermission.GrantType.ALLOW,
    ).filter(Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
             Q(campus_id=campus_id) | Q(campus_id__isnull=True)).exists()
    if direct_allow:
        return True
    return UserRole.objects.filter(
        user=user,
        is_active=True,
        role__is_active=True,
        role__role_permissions__permission__code=permission_code,
        role__role_permissions__permission__is_active=True,
    ).filter(Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
             Q(campus_id=campus_id) | Q(campus_id__isnull=True),
             Q(department_id=department_id) | Q(department_id__isnull=True)).exists()


def require_attendance_permission(*, user, permission_code, tenant_id, campus_id, department_id):
    if not FeatureSettingsService.is_faculty_attendance_enabled(tenant_id=tenant_id):
        raise PermissionDenied("Faculty Attendance is disabled for this tenant.")
    if not PermissionService.has_permission(
        user,
        permission_code,
        tenant_id=tenant_id,
        campus_id=campus_id,
    ):
        raise PermissionDenied("Faculty Attendance permission is required.")
    if not _has_department_scope(
        user=user,
        permission_code=permission_code,
        tenant_id=tenant_id,
        campus_id=campus_id,
        department_id=department_id,
    ):
        raise PermissionDenied("Faculty Attendance department scope is required.")


def can_faculty_view_own_attendance(*, user, tenant_id, campus_id):
    return (
        FeatureSettingsService.is_faculty_attendance_faculty_visibility_enabled(tenant_id=tenant_id)
        and PermissionService.has_permission(
            user,
            VIEW_PERMISSION,
            tenant_id=tenant_id,
            campus_id=campus_id,
        )
    )
