from django.contrib.auth import logout, SESSION_KEY
from django.shortcuts import redirect
from django.urls import reverse

from apps.accounts.login_control import LoginAccessService


class LoginAccessMiddleware:
    """Also guard existing sessions using the legacy ModelBackend path."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and SESSION_KEY in request.session:
            # Authentication may have rejected a disabled/inactive legacy user.
            # Flush any already-loaded session before later middleware saves it.
            logout(request)
        if request.user.is_authenticated and not LoginAccessService.login_allowed(request.user):
            logout(request)
            route = "faculty_portal:public_index" if request.path.startswith("/faculty/") else "accounts:admin_login"
            return redirect(reverse(route))
        return self.get_response(request)
