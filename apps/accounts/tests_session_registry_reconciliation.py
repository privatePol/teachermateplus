from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY, update_session_auth_hash
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import close_old_connections, connection
from django.db.models.query import QuerySet
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import ActivePortalSession, User
from apps.accounts.services import ActivePortalSessionService
from apps.admin_portal import tests_dashboard as dashboard_fixtures
from apps.admin_portal.dashboard import logged_in_users, scope_token
from apps.core.middleware import SessionTimeoutMiddleware
from apps.core.services.scope import ScopeService


class RegistryFixture:
    make_user = classmethod(dashboard_fixtures.AdminDashboardTests.make_user.__func__)

    @classmethod
    def setUpTestData(cls):
        dashboard_fixtures.AdminDashboardTests.setUpTestData.__func__(cls)

    def request_for(self, client=None, user=None):
        client = client or self.client
        user = user or self.admin
        request = RequestFactory().get("/admin-portal/dashboard/")
        request.user = user
        request.session = SessionStore(session_key=client.session.session_key)
        dict(request.session.items())  # Load both concurrent snapshots before either reconciles.
        request.scope = ScopeService.build_scope(user)
        return request


class SessionRegistryReconciliationTests(RegistryFixture, TestCase):
    def setUp(self):
        self.client.force_login(self.admin)

    def dashboard(self, client=None):
        response = (client or self.client).get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        return response

    def test_missing_baseline_registration_is_counted_on_first_and_second_requests(self):
        key = self.client.session.session_key
        self.assertFalse(ActivePortalSession.objects.filter(session_key=key).exists())
        for _ in range(2):
            self.assertEqual(self.dashboard().context["active_user_count"], 1)
            self.assertTrue(ActivePortalSession.objects.filter(session_key=key, user=self.admin).exists())
        self.assertEqual(self.client.session.session_key, key)

    def test_premature_baseline_marker_does_not_hide_missing_registration(self):
        session = self.client.session
        session[ActivePortalSessionService.MONITOR_SESSION_KEY] = session.session_key
        session.save()
        for _ in range(2):
            self.assertEqual(self.dashboard().context["active_user_count"], 1)

    def test_inherited_password_change_key_rotation_is_reconciled_before_render(self):
        session = self.client.session
        old_key = session.session_key
        session[ActivePortalSessionService.MONITOR_SESSION_KEY] = old_key
        session.save()
        request = self.request_for()
        self.admin.set_password("InheritedPasswordChange123!")
        self.admin.save(update_fields=["password"])
        update_session_auth_hash(request, self.admin)
        request.session.save()
        new_key = request.session.session_key
        self.client.cookies[settings.SESSION_COOKIE_NAME] = new_key
        self.assertNotEqual(old_key, new_key)
        for _ in range(2):
            self.assertEqual(self.dashboard().context["active_user_count"], 1)
        self.assertFalse(Session.objects.filter(session_key=old_key).exists())
        self.assertTrue(ActivePortalSession.objects.filter(session_key=new_key, user=self.admin).exists())

    def test_healthy_repeat_request_does_not_update_registry_or_save_session_early(self):
        self.dashboard()
        row = ActivePortalSession.objects.get(user=self.admin)
        before = row.updated_at
        request = self.request_for()
        with patch.object(request.session, "save", side_effect=AssertionError("early session save")):
            with CaptureQueriesContext(connection) as queries:
                SessionTimeoutMiddleware(lambda req: HttpResponse("ok"))(request)
        writes = [q["sql"] for q in queries if "active_portal_sessions" in q["sql"] and
                  q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertEqual(writes, [])
        row.refresh_from_db()
        self.assertEqual(row.updated_at, before)
        reads = [q for q in queries if "active_portal_sessions" in q["sql"]]
        self.assertEqual(len(reads), 1)

    def test_background_gap_reconciliation_preserves_exact_idle_deadline(self):
        request = self.request_for()
        token = scope_token(request)
        key = self.client.session.session_key
        deadline = timezone.now() + timedelta(seconds=90)
        Session.objects.filter(session_key=key).update(expire_date=deadline)
        response = self.client.get(reverse("admin_portal:dashboard_reopen_card"), {"scope_token": token})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ActivePortalSession.objects.filter(user=self.admin, session_key=key).exists())
        self.assertEqual(Session.objects.get(session_key=key).expire_date, deadline)

    def test_two_missing_sessions_for_one_user_count_once_and_are_not_revoked(self):
        other = Client()
        other.force_login(self.admin)
        keys = {self.client.session.session_key, other.session.session_key}
        for client in (self.client, other, self.client, other):
            self.assertEqual(self.dashboard(client).context["active_user_count"], 1)
        self.assertEqual(set(ActivePortalSession.objects.filter(user=self.admin).values_list("session_key", flat=True)), keys)
        self.assertEqual(Session.objects.filter(session_key__in=keys).count(), 2)
        listing = self.client.get(reverse("admin_portal:dashboard_logged_in_users"))
        self.assertEqual(listing.context["page_obj"].paginator.count, 1)

    def test_expired_invalid_and_logged_out_sessions_are_excluded(self):
        expired = Client()
        expired.force_login(self.admin)
        expired_key = expired.session.session_key
        Session.objects.filter(session_key=expired_key).update(expire_date=timezone.now() - timedelta(seconds=1))
        invalid = Client()
        invalid.force_login(self.admin)
        invalid_session = invalid.session
        invalid_session[HASH_SESSION_KEY] = "invalid-authentication-hash"
        invalid_session.save()
        invalid_key = invalid_session.session_key
        for client, key in ((expired, expired_key), (invalid, invalid_key)):
            self.assertEqual(client.get(reverse("admin_portal:dashboard")).status_code, 302)
            self.assertFalse(ActivePortalSession.objects.filter(session_key=key).exists())
        self.assertEqual(self.dashboard().context["active_user_count"], 1)
        self.client.get(reverse("accounts:admin_logout"))
        self.assertEqual(logged_in_users(self.request_for(user=self.admin)).count(), 0)

    def test_other_user_session_identity_cannot_be_registered_for_request_user(self):
        other = self.make_user("different-owner")
        other_client = Client()
        other_client.force_login(other)
        request = self.request_for(other_client, self.admin)
        key = request.session.session_key
        SessionTimeoutMiddleware(lambda req: HttpResponse("ok"))(request)
        self.assertFalse(ActivePortalSession.objects.filter(session_key=key).exists())
        self.assertEqual(str(other.pk), request.session[SESSION_KEY])

    def test_invalid_hash_with_authenticated_request_object_is_not_reconciled(self):
        request = self.request_for()
        request.session[HASH_SESSION_KEY] = "invalid"
        SessionTimeoutMiddleware(lambda req: HttpResponse("ok"))(request)
        self.assertFalse(ActivePortalSession.objects.filter(session_key=request.session.session_key).exists())

    def test_reconciliation_does_not_save_the_session_or_resurrect_a_missing_backing_row(self):
        request = self.request_for()
        with patch.object(request.session, "save", side_effect=AssertionError("early session save")):
            self.assertTrue(ActivePortalSessionService.reconcile_authenticated_session(request=request, user=self.admin))
        ActivePortalSession.objects.filter(user=self.admin).delete()
        Session.objects.filter(session_key=request.session.session_key).delete()
        self.assertFalse(ActivePortalSessionService.reconcile_authenticated_session(request=request, user=self.admin))
        self.assertFalse(ActivePortalSession.objects.filter(user=self.admin).exists())

    def test_password_or_deactivation_race_cannot_create_a_registration(self):
        for change in ("password", "deactivate"):
            with self.subTest(change=change):
                user = self.make_user("raced-" + change)
                client = Client()
                client.force_login(user)
                request = self.request_for(client, user)
                persisted = User.objects.get(pk=user.pk)
                if change == "password":
                    persisted.set_password("ConcurrentPasswordChange123!")
                    persisted.save(update_fields=["password"])
                else:
                    persisted.is_active = False
                    persisted.save(update_fields=["is_active"])
                self.assertFalse(ActivePortalSessionService.reconcile_authenticated_session(request=request, user=user))
                self.assertFalse(ActivePortalSession.objects.filter(session_key=request.session.session_key).exists())

    def test_conflicting_registration_is_not_transferred_to_the_current_user(self):
        other = self.make_user("registered-owner")
        request = self.request_for()
        registration = ActivePortalSession.objects.create(session_key=request.session.session_key, user=other)
        self.assertFalse(ActivePortalSessionService.reconcile_authenticated_session(request=request, user=self.admin))
        registration.refresh_from_db()
        self.assertEqual(registration.user_id, other.pk)

    def test_two_interleaved_initializations_create_one_row_without_repeated_write(self):
        requests = [self.request_for(), self.request_for()]
        middleware = SessionTimeoutMiddleware(lambda req: HttpResponse("ok"))
        middleware(requests[0])
        row = ActivePortalSession.objects.get(user=self.admin)
        before = row.updated_at
        middleware(requests[1])
        row.refresh_from_db()
        self.assertEqual(ActivePortalSession.objects.filter(user=self.admin).count(), 1)
        self.assertEqual(row.updated_at, before)
        self.assertEqual(requests[0].session[ActivePortalSessionService.MONITOR_SESSION_KEY], requests[1].session[ActivePortalSessionService.MONITOR_SESSION_KEY])

    def test_scope_and_count_listing_parity_after_reconciling_other_tenant_and_campus(self):
        clients = []
        for name, values in [
            ("other-tenant-gap", {"default_tenant": self.other_tenant, "default_campus": None}),
            ("other-campus-gap", {"default_campus": self.other_campus}),
            ("other-department-gap", {"default_department": self.other_department}),
        ]:
            user = self.make_user(name, **values)
            client = Client()
            client.force_login(user)
            # A valid authenticated denied request still reconciles only its own identity.
            self.assertEqual(client.get(reverse("admin_portal:dashboard")).status_code, 403)
            self.assertTrue(ActivePortalSession.objects.filter(user=user, session_key=client.session.session_key).exists())
            clients.append(client)
        for _ in range(2):
            response = self.dashboard()
            listing = self.client.get(reverse("admin_portal:dashboard_logged_in_users"))
            self.assertEqual(response.context["active_user_count"], 1)
            self.assertEqual(listing.context["page_obj"].paginator.count, 1)
            self.assertEqual([row.pk for row in listing.context["page_obj"]], [self.admin.pk])


class SessionRegistryConcurrentReconciliationTests(TransactionTestCase):
    def test_simultaneous_initialization_uses_one_owner_and_one_row(self):
        from apps.tenants.models import Tenant
        tenant = Tenant.objects.create(code="RECONCILE", name="Concurrent reconciliation")
        user = User.objects.create_user(username="concurrent-gap", email="concurrent-gap@example.test", password="testpass123", default_tenant=tenant)
        client = Client()
        client.force_login(user)
        key = client.session.session_key
        barrier = Barrier(2)
        original_exists = QuerySet.exists

        def both_observe_gap(queryset):
            result = original_exists(queryset)
            if queryset.model is ActivePortalSession:
                self.assertFalse(result)
                barrier.wait(timeout=10)
            return result

        def reconcile():
            close_old_connections()
            try:
                current = User.objects.get(pk=user.pk)
                request = RequestFactory().get("/admin-portal/dashboard/")
                request.user = current
                request.session = SessionStore(session_key=key)
                dict(request.session.items())
                request.scope = {"tenant_id": tenant.pk}
                SessionTimeoutMiddleware(lambda req: HttpResponse("ok"))(request)
                return request.session.get(ActivePortalSessionService.MONITOR_SESSION_KEY)
            finally:
                close_old_connections()

        with patch.object(QuerySet, "exists", both_observe_gap):
            with ThreadPoolExecutor(max_workers=2) as executor:
                self.assertEqual(list(executor.map(lambda _: reconcile(), range(2))), [key, key])
        self.assertEqual(list(ActivePortalSession.objects.filter(session_key=key).values_list("user_id", flat=True)), [user.pk])
