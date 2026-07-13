from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from apps.core.services.client_ip import resolve_client_ip


class ClientIpResolverTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, *, remote_addr=None, forwarded_for=None, real_ip=None):
        extra = {}
        if remote_addr is not None:
            extra["REMOTE_ADDR"] = remote_addr
        if forwarded_for is not None:
            extra["HTTP_X_FORWARDED_FOR"] = forwarded_for
        if real_ip is not None:
            extra["HTTP_X_REAL_IP"] = real_ip
        request = self.factory.get("/", **extra)
        if remote_addr is None:
            request.META.pop("REMOTE_ADDR", None)
        return request

    @override_settings(TRUST_UNIX_SOCKET_PROXY=True)
    def test_direct_peer_is_used_and_untrusted_forwarded_headers_are_ignored(self):
        request = self._request(
            remote_addr="198.51.100.20",
            forwarded_for="203.0.113.10, 10.0.0.8",
            real_ip="203.0.113.11",
        )

        self.assertEqual(resolve_client_ip(request), "198.51.100.20")

    @override_settings(TRUSTED_PROXY_IPS=["10.0.0.0/8"], TRUST_UNIX_SOCKET_PROXY=True)
    def test_trusted_proxy_chain_returns_nearest_untrusted_ipv4_client(self):
        request = self._request(
            remote_addr="10.0.0.9",
            forwarded_for="192.0.2.99, 203.0.113.25, 10.0.0.8",
        )

        self.assertEqual(resolve_client_ip(request), "203.0.113.25")

    @override_settings(TRUSTED_PROXY_IPS=["2001:db8:ffff::/48"])
    def test_trusted_proxy_normalizes_ipv6_client(self):
        request = self._request(
            remote_addr="2001:db8:ffff::10",
            forwarded_for="2001:0db8:0001:0000:0000:0000:0000:0007",
        )

        self.assertEqual(resolve_client_ip(request), "2001:db8:1::7")

    @override_settings(TRUSTED_PROXY_IPS=["10.0.0.0/8"])
    def test_malformed_forwarded_chain_fails_closed_to_trusted_peer(self):
        request = self._request(
            remote_addr="10.0.0.9",
            forwarded_for="203.0.113.25, not-an-ip",
        )

        self.assertEqual(resolve_client_ip(request), "10.0.0.9")

    @override_settings(TRUSTED_PROXY_IPS=["10.0.0.0/8"])
    def test_trusted_proxy_can_use_single_normalized_x_real_ip_fallback(self):
        request = self._request(
            remote_addr="10.0.0.9",
            real_ip="2001:0db8:0000:0000:0000:0000:0000:0009",
        )

        self.assertEqual(resolve_client_ip(request), "2001:db8::9")

    @override_settings(TRUST_UNIX_SOCKET_PROXY=False)
    def test_missing_remote_addr_does_not_trust_proxy_headers_by_default(self):
        request = self._request(real_ip="198.51.100.80", forwarded_for="198.51.100.81")

        self.assertIsNone(resolve_client_ip(request))

    @override_settings(TRUST_UNIX_SOCKET_PROXY=True)
    def test_unix_socket_proxy_prefers_and_normalizes_valid_x_real_ip(self):
        ipv4_request = self._request(real_ip="198.51.100.80", forwarded_for="198.51.100.81")
        ipv6_request = self._request(real_ip="2001:0db8:0000:0000:0000:0000:0000:0080")
        non_ip_peer_request = self._request(remote_addr="unix-socket", real_ip="198.51.100.82")

        self.assertEqual(resolve_client_ip(ipv4_request), "198.51.100.80")
        self.assertEqual(resolve_client_ip(ipv6_request), "2001:db8::80")
        self.assertEqual(resolve_client_ip(non_ip_peer_request), "198.51.100.82")

    @override_settings(TRUST_UNIX_SOCKET_PROXY=True)
    def test_unix_socket_proxy_safely_uses_final_valid_x_forwarded_for_address(self):
        request = self._request(
            forwarded_for="192.0.2.10, 2001:0db8:0000:0000:0000:0000:0000:0081",
        )

        self.assertEqual(resolve_client_ip(request), "2001:db8::81")

    @override_settings(TRUST_UNIX_SOCKET_PROXY=True)
    def test_unix_socket_proxy_rejects_malformed_headers(self):
        malformed_real_ip = self._request(
            real_ip="not-an-ip",
            forwarded_for="198.51.100.82",
        )
        malformed_forwarded_for = self._request(
            forwarded_for="198.51.100.82, not-an-ip",
        )
        invalid_remote_addr = self._request(
            remote_addr="unix-socket",
            real_ip="also-not-an-ip",
        )

        self.assertIsNone(resolve_client_ip(malformed_real_ip))
        self.assertIsNone(resolve_client_ip(malformed_forwarded_for))
        self.assertIsNone(resolve_client_ip(invalid_remote_addr))


class ClientIpAuditTests(TestCase):
    @override_settings(TRUSTED_PROXY_IPS=["10.0.0.0/8"])
    def test_audit_service_stores_only_resolved_client_ip(self):
        request = RequestFactory().post(
            "/admin-portal/login/",
            REMOTE_ADDR="10.0.0.9",
            HTTP_X_FORWARDED_FOR="198.51.100.70, 10.0.0.8",
            HTTP_X_REAL_IP="198.51.100.71",
        )

        AuditService.log_event(
            action="LOGIN_FAILURE",
            portal="ADMIN",
            entity_type="User",
            metadata={"username": "client-ip-test"},
            request=request,
        )

        audit = AuditLog.objects.get(action="LOGIN_FAILURE")
        self.assertEqual(audit.ip_address, "198.51.100.70")
        self.assertNotIn("10.0.0.8", str(audit.metadata_json))

    @override_settings(TRUST_UNIX_SOCKET_PROXY=True)
    def test_unix_socket_proxy_ip_is_stored_in_audit_and_login_lockout_state(self):
        from apps.accounts.models import PortalLoginLockoutState
        from apps.accounts.services import LoginLockoutService

        request = RequestFactory().post(
            "/admin-portal/login/",
            REMOTE_ADDR="",
            HTTP_X_REAL_IP="2001:0db8:0000:0000:0000:0000:0000:0090",
            HTTP_X_FORWARDED_FOR="192.0.2.90, 2001:db8::90",
        )

        AuditService.log_event(
            action="LOGIN_FAILURE",
            portal="ADMIN",
            entity_type="User",
            metadata={"username": "unix-client-ip-test"},
            request=request,
        )
        LoginLockoutService.register_failure(
            username="unix-client-ip-test",
            portal_code="ADMIN",
            request=request,
        )

        audit = AuditLog.objects.get(action="LOGIN_FAILURE")
        lockout = PortalLoginLockoutState.objects.get(username="unix-client-ip-test", portal_code="ADMIN")
        self.assertEqual(audit.ip_address, "2001:db8::90")
        self.assertEqual(lockout.last_ip, "2001:db8::90")
