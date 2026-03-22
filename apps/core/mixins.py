from __future__ import annotations

from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic.base import ContextMixin

from apps.core.services.permissions import PermissionService


class ScopeContextMixin(ContextMixin):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current_scope"] = getattr(self.request, "scope", {})
        return context


class PortalAccessMixin:
    portal_permission_code: str | None = None
    login_route_name: str = "accounts:admin_login"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            route_name = self.login_route_name
            if request.path.startswith("/faculty/"):
                route_name = "faculty_portal:public_index"
            return redirect(reverse(route_name))
        if self.portal_permission_code and not PermissionService.has_permission(
            request.user,
            self.portal_permission_code,
            tenant_id=getattr(request, "scope", {}).get("tenant_id"),
            campus_id=getattr(request, "scope", {}).get("campus_id"),
        ):
            return HttpResponseForbidden("Portal access denied.")
        return super().dispatch(request, *args, **kwargs)


class PermissionRequiredMixin:
    permission_required_code: str | None = None

    def dispatch(self, request, *args, **kwargs):
        if self.permission_required_code and not PermissionService.has_permission(
            request.user,
            self.permission_required_code,
            tenant_id=getattr(request, "scope", {}).get("tenant_id"),
            campus_id=getattr(request, "scope", {}).get("campus_id"),
        ):
            return HttpResponseForbidden("Permission denied.")
        return super().dispatch(request, *args, **kwargs)
