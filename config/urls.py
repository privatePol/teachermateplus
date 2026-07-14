from django.conf import settings
from django.contrib import admin
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [
    path(f"{settings.DJANGO_ADMIN_PATH}/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.admin_portal.urls")),
    path("", include("apps.faculty_portal.urls")),
    path("", include("apps.exit_pulse.urls")),
    path("", include("apps.student_portal.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if getattr(settings, "DEBUG_TOOLBAR_ENABLED", False):
    urlpatterns.append(path("__debug__/", include("debug_toolbar.urls")))
