from __future__ import annotations

from functools import wraps

from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.services.permissions import PermissionService


def permission_required(permission_code: str):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if request.path.startswith("/faculty/"):
                    return redirect(reverse("faculty_portal:public_index"))
                return redirect(reverse("accounts:admin_login"))
            if not PermissionService.has_permission(
                request.user,
                permission_code,
                tenant_id=getattr(request, "scope", {}).get("tenant_id"),
                campus_id=getattr(request, "scope", {}).get("campus_id"),
            ):
                return HttpResponseForbidden("You do not have permission to access this resource.")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


def portal_required(portal_code: str):
    permission_map = {"ADMIN": "admin_portal.access", "FACULTY": "faculty_portal.access"}
    permission_code = permission_map.get(portal_code.upper())

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if portal_code.upper() == "FACULTY":
                    return redirect(reverse("faculty_portal:public_index"))
                return redirect(reverse("accounts:admin_login"))
            if permission_code and not PermissionService.has_permission(
                request.user,
                permission_code,
                tenant_id=getattr(request, "scope", {}).get("tenant_id"),
                campus_id=getattr(request, "scope", {}).get("campus_id"),
            ):
                return HttpResponseForbidden("Portal access denied.")
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator
