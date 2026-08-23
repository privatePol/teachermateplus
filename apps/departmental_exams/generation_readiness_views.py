from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils.cache import patch_cache_control
from django.views.decorators.http import require_GET

from apps.core.decorators import portal_required
from apps.tenants.models import Tenant

from .automatic_generation_readiness import AutomaticGenerationReadinessReport


def _current_tenant(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id") or getattr(
        request.user, "default_tenant_id", None
    )
    tenant = Tenant.objects.filter(id=tenant_id, is_active=True).first()
    if tenant is None:
        raise PermissionDenied("An active current tenant is required.")
    return tenant


def _report_response(request, *, template_name):
    tenant = _current_tenant(request)
    context = AutomaticGenerationReadinessReport(
        tenant_id=tenant.id,
        user=request.user,
        params=request.GET,
    ).build()
    context["tenant"] = tenant
    response = render(request, template_name, context)
    patch_cache_control(response, private=True, no_store=True)
    return response


@require_GET
@portal_required("ADMIN")
def automatic_generation_readiness_view(request):
    return _report_response(
        request,
        template_name="departmental_exams/admin/automatic_generation_readiness.html",
    )


@require_GET
@portal_required("ADMIN")
def automatic_generation_readiness_print_view(request):
    return _report_response(
        request,
        template_name=(
            "departmental_exams/admin/automatic_generation_readiness_print.html"
        ),
    )
