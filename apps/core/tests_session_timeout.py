from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from apps.accounts.models import User
from apps.core.middleware import SessionTimeoutMiddleware
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.tenants.models import Tenant


class SessionTimeoutMiddlewareTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.user = User.objects.create_user(
            username="session_timeout_user",
            email="session_timeout_user@example.com",
            password="testpass123",
            default_tenant=self.tenant,
        )
        self.factory = RequestFactory()

    def _request(self):
        request = self.factory.get("/admin-portal/")
        request.user = self.user
        request.session = SessionStore()
        request.scope = {"tenant_id": self.tenant.id}
        return request

    def test_session_timeout_uses_tenant_configuration(self):
        SystemSettingService.set(
            FeatureSettingsService.SESSION_TIMEOUT_MINUTES_KEY,
            45,
            tenant_id=self.tenant.id,
            value_type="INT",
            is_active=True,
        )
        request = self._request()

        SessionTimeoutMiddleware(lambda req: HttpResponse("ok"))(request)

        self.assertEqual(request.session.get_expiry_age(), 45 * 60)

    @override_settings(SESSION_COOKIE_AGE=1800)
    def test_session_timeout_falls_back_to_settings_cookie_age(self):
        request = self._request()

        SessionTimeoutMiddleware(lambda req: HttpResponse("ok"))(request)

        self.assertEqual(request.session.get_expiry_age(), 30 * 60)
