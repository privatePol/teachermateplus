from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService


class ScopeResolutionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ScopeService.attach_scope_to_request(request)
        return self.get_response(request)


class SessionTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _default_timeout_minutes() -> int:
        try:
            seconds = int(getattr(settings, "SESSION_COOKIE_AGE", 3600) or 3600)
        except (TypeError, ValueError):
            seconds = 3600
        return max(seconds // 60, 1)

    def __call__(self, request):
        user = getattr(request, "user", None)
        session = getattr(request, "session", None)
        if session is not None and getattr(user, "is_authenticated", False):
            tenant_id = getattr(request, "scope", {}).get("tenant_id")
            timeout_minutes = FeatureSettingsService.get_session_timeout_minutes(
                tenant_id=tenant_id,
                default=self._default_timeout_minutes(),
            )
            session.set_expiry(timeout_minutes * 60)
        return self.get_response(request)


class PortalAccessMiddleware:
    ADMIN_PREFIX = "/admin-portal/"
    FACULTY_PREFIX = "/faculty/"
    STUDENT_PREFIX = "/student/"
    ADMIN_LOGIN_PATH = "/admin-portal/login/"
    ADMIN_LOGIN_OTP_PATH = "/admin-portal/login/otp/"
    ADMIN_PUBLIC_PATHS = {
        "/admin-portal/login/",
        "/admin-portal/login/otp/",
        "/admin-portal/forgot-password/",
        "/admin-portal/forgot-password/sent/",
        "/admin-portal/reset/verify/",
        "/admin-portal/reset/confirm/",
        "/admin-portal/reset/done/",
    }
    FACULTY_LOGIN_PATH = "/faculty/login/"
    FACULTY_PUBLIC_PATHS = {
        "/faculty/",
        "/faculty/index/",
        "/faculty/login/otp/",
        "/faculty/forgot-password/",
        "/faculty/forgot-password/sent/",
        "/faculty/reset/done/",
    }
    FACULTY_PUBLIC_PREFIXES = (
        "/faculty/reset/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        scope = getattr(request, "scope", {})
        tenant_id = scope.get("tenant_id")
        campus_id = scope.get("campus_id")

        if path.startswith(self.ADMIN_PREFIX) and path not in self.ADMIN_PUBLIC_PATHS:
            if not request.user.is_authenticated:
                return redirect(reverse("accounts:admin_login"))
            if not PermissionService.has_permission(
                request.user, "admin_portal.access", tenant_id=tenant_id, campus_id=campus_id
            ):
                return HttpResponseForbidden("Admin portal access denied.")

        is_faculty_public_prefix = any(path.startswith(prefix) for prefix in self.FACULTY_PUBLIC_PREFIXES)
        if (
            path.startswith(self.FACULTY_PREFIX)
            and path != self.FACULTY_LOGIN_PATH
            and path not in self.FACULTY_PUBLIC_PATHS
            and not is_faculty_public_prefix
        ):
            if not request.user.is_authenticated:
                return redirect(reverse("faculty_portal:public_index"))
            if not PermissionService.has_permission(
                request.user, "faculty_portal.access", tenant_id=tenant_id, campus_id=campus_id
            ):
                return HttpResponseForbidden("Faculty portal access denied.")

        if path.startswith(self.STUDENT_PREFIX):
            if not request.user.is_authenticated:
                return redirect(reverse("accounts:admin_login"))

        return self.get_response(request)


class PostLoginSecurityMiddleware:
    ADMIN_PREFIX = "/admin-portal/"
    FACULTY_PREFIX = "/faculty/"
    STUDENT_PREFIX = "/student/"
    ADMIN_ALLOWED_PATHS = {
        "/admin-portal/login/",
        "/admin-portal/login/otp/",
        "/admin-portal/forgot-password/",
        "/admin-portal/forgot-password/sent/",
        "/admin-portal/reset/verify/",
        "/admin-portal/reset/confirm/",
        "/admin-portal/reset/done/",
        "/admin-portal/logout/",
        "/admin-portal/change-password/",
        "/admin-portal/privacy-consent/",
    }
    FACULTY_ALLOWED_PATHS = {
        "/faculty/",
        "/faculty/index/",
        "/faculty/login/",
        "/faculty/login/otp/",
        "/faculty/logout/",
        "/faculty/forgot-password/",
        "/faculty/forgot-password/sent/",
        "/faculty/reset/done/",
        "/faculty/change-password/",
        "/faculty/privacy-consent/",
    }
    FACULTY_ALLOWED_PREFIXES = ("/faculty/reset/",)

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _requires_privacy_consent(user):
        required_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        return (
            not getattr(user, "privacy_consent_at", None)
            or (getattr(user, "privacy_consent_version", None) or "") != required_version
        )

    def __call__(self, request):
        path = request.path
        user = request.user
        if not getattr(user, "is_authenticated", False):
            return self.get_response(request)

        if path.startswith(self.ADMIN_PREFIX) and path not in self.ADMIN_ALLOWED_PATHS:
            if getattr(user, "must_change_password", False):
                return redirect(reverse("accounts:admin_change_password"))
            if self._requires_privacy_consent(user):
                return redirect(reverse("accounts:admin_privacy_consent"))

        is_faculty_allowed_prefix = any(path.startswith(prefix) for prefix in self.FACULTY_ALLOWED_PREFIXES)
        if (
            path.startswith(self.FACULTY_PREFIX)
            and path not in self.FACULTY_ALLOWED_PATHS
            and not is_faculty_allowed_prefix
        ):
            if getattr(user, "must_change_password", False):
                return redirect(reverse("accounts:faculty_change_password"))
            if self._requires_privacy_consent(user):
                return redirect(reverse("accounts:faculty_privacy_consent"))

        return self.get_response(request)
