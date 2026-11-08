from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied
from asgiref.sync import sync_to_async

from apps.accounts.login_control import LoginAccessService


class LoginControlBackend(ModelBackend):
    def user_can_authenticate(self, user):
        # get_user() must not delete the session being loaded. Middleware owns
        # deadline processing and logout for authenticated requests.
        return super().user_can_authenticate(user) and not user.login_disabled

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        username = username if username is not None else kwargs.get(User.USERNAME_FIELD)
        if username is not None:
            user = User.objects.filter(**{User.USERNAME_FIELD: username}).first()
            if user is not None and (not self.user_can_authenticate(user) or not LoginAccessService.login_allowed(user)):
                # Stop backend traversal: the legacy backend remains configured
                # only to read pre-feature sessions, never to bypass this guard.
                raise PermissionDenied("Login is unavailable for this account.")
        return super().authenticate(request, username=username, password=password, **kwargs)

    async def aauthenticate(self, request, username=None, password=None, **kwargs):
        return await sync_to_async(self.authenticate)(request, username=username, password=password, **kwargs)

    async def aget_user(self, user_id):
        return await sync_to_async(self.get_user)(user_id)
