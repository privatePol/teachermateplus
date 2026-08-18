from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET

from apps.core.decorators import portal_required
from apps.tenants.models import Tenant

from .planning_readiness import (
    PlanningReadinessAuthorizationService,
    PlanningReadinessReport,
)


def _current_tenant(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    tenant = Tenant.objects.filter(id=tenant_id, is_active=True).first()
    if not tenant:
        raise PermissionDenied("An active current tenant is required.")
    return tenant


def _report_context(request, *, scope):
    tenant = _current_tenant(request)
    context = PlanningReadinessReport(
        tenant=tenant,
        scope=scope,
        params=request.GET,
    ).build()
    context["generated_at"] = timezone.localtime(
        timezone.now(), timezone=ZoneInfo("Asia/Manila")
    )
    return context


@require_GET
@portal_required("ADMIN")
def planning_readiness_view(request):
    tenant = _current_tenant(request)
    view_scope = PlanningReadinessAuthorizationService.require_scope(
        user=request.user,
        tenant_id=tenant.id,
        permission_code=PlanningReadinessAuthorizationService.VIEW_PERMISSION,
    )
    print_scope = PlanningReadinessAuthorizationService.scope_for_permission(
        user=request.user,
        tenant_id=tenant.id,
        permission_code=PlanningReadinessAuthorizationService.PRINT_PERMISSION,
    )
    context = _report_context(request, scope=view_scope)
    context["can_print"] = view_scope.intersection(print_scope).allows_anything()
    response = render(
        request,
        "departmental_exams/admin/planning_readiness.html",
        context,
    )
    patch_cache_control(response, private=True, no_store=True)
    return response


@require_GET
@portal_required("ADMIN")
def planning_readiness_print_view(request):
    tenant = _current_tenant(request)
    view_scope = PlanningReadinessAuthorizationService.require_scope(
        user=request.user,
        tenant_id=tenant.id,
        permission_code=PlanningReadinessAuthorizationService.VIEW_PERMISSION,
    )
    print_scope = PlanningReadinessAuthorizationService.require_scope(
        user=request.user,
        tenant_id=tenant.id,
        permission_code=PlanningReadinessAuthorizationService.PRINT_PERMISSION,
    )
    combined_scope = view_scope.intersection(print_scope)
    if not combined_scope.allows_anything():
        raise PermissionDenied("Planning & Readiness print access is unavailable in your shared exact scope.")
    response = render(
        request,
        "departmental_exams/admin/planning_readiness_print.html",
        _report_context(request, scope=combined_scope),
    )
    patch_cache_control(response, private=True, no_store=True)
    return response
