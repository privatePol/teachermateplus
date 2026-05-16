from __future__ import annotations

from functools import wraps

from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService

from .services import StudentPortalAccessError, StudentPortalService


def student_portal_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(reverse("accounts:admin_login"))
        try:
            link = StudentPortalService.get_active_link_for_user(request.user)
        except StudentPortalAccessError as exc:
            return HttpResponseForbidden(str(exc))
        if not FeatureSettingsService.is_student_portal_enabled(tenant_id=link.tenant_id, default=False):
            return HttpResponseForbidden("Student Portal is not enabled for this tenant.")
        if not PermissionService.has_permission(
            request.user,
            "student_portal.access",
            tenant_id=link.tenant_id,
            campus_id=link.campus_id,
        ):
            return HttpResponseForbidden("Student Portal access denied.")
        request.student_link = link
        return view_func(request, *args, **kwargs)

    return _wrapped

