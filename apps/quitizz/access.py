from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.rbac.models import UserPermission
from apps.tenants.models import Campus


MANAGE = "quitizz.manage"
HOST = "quitizz.host"
VIEW_HISTORY = "quitizz.view_history"


def assigned(user, code, tenant_id, campus_id):
    if not user or not user.is_authenticated or not user.is_active or not tenant_id or not campus_id:
        return False
    # An applicable direct DENY always wins, including a broader null scope.
    if UserPermission.objects.filter(user=user, permission__code=code, permission__is_active=True, grant_type="DENY").filter(
        Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
        Q(campus_id=campus_id) | Q(campus_id__isnull=True),
    ).exists():
        return False
    return PermissionService.has_assigned_permission(user, code, tenant_id=tenant_id, campus_id=campus_id, exact_scope=True)


def capabilities(user, tenant_id, campus_id):
    if not tenant_id or not campus_id or not FeatureSettingsService.is_quitizz_enabled(tenant_id=tenant_id):
        return {"manage": False, "host": False}
    if not Campus.objects.filter(pk=campus_id, tenant_id=tenant_id, is_active=True, tenant__is_active=True).exists():
        return {"manage": False, "host": False}
    if not assigned(user, "faculty_portal.access", tenant_id, campus_id):
        return {"manage": False, "host": False}
    return {"manage": assigned(user, MANAGE, tenant_id, campus_id), "host": assigned(user, HOST, tenant_id, campus_id)}


def require_access(user, tenant_id, campus_id, capability=None):
    caps = capabilities(user, tenant_id, campus_id)
    if not (caps.get(capability) if capability else any(caps.values())):
        raise PermissionDenied("QuiTizz is unavailable or permission has not been assigned in this campus.")
    return caps
