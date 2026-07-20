from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from apps.accounts.services import ActivePortalSessionService


def _is_django_admin_request(request) -> bool:
    admin_path = str(getattr(settings, "DJANGO_ADMIN_PATH", "django-admin") or "django-admin").strip("/")
    return bool(request and request.path_info.startswith(f"/{admin_path}/"))


@receiver(user_logged_in, dispatch_uid="accounts.register_django_admin_active_session")
def register_django_admin_active_session(sender, request, user, **kwargs):
    if _is_django_admin_request(request):
        ActivePortalSessionService.register(
            request=request,
            user=user,
            enforce_single_session=ActivePortalSessionService.is_single_session_enforcement_enabled(user),
        )


@receiver(user_logged_out, dispatch_uid="accounts.unregister_django_admin_active_session")
def unregister_django_admin_active_session(sender, request, user, **kwargs):
    if _is_django_admin_request(request) and user is not None:
        ActivePortalSessionService.unregister(
            user=user,
            session_key=request.session.session_key,
        )
