"""Session registry repair must serialize with independent login revocation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import SimpleTestCase, TransactionTestCase

from apps.accounts.login_obligations import lock_login_users
from apps.accounts.models import ActivePortalSession, User
from apps.accounts.services import ActivePortalSessionService
from apps.accounts.tests_login_control import LoginFixture


class UserNoopSaveTests(SimpleTestCase):
    def test_unsaved_user_noop_update_does_not_force_a_refresh_or_insert(self):
        user = User(username="unsaved-noop", email="unsaved-noop@example.test")
        user.save(update_fields=[])
        self.assertIsNone(user.pk)
        self.assertTrue(user._state.adding)


class LoginRegistryIntegrationTests(LoginFixture, TransactionTestCase):

    @staticmethod
    def connection_call(fn):
        close_old_connections()
        try:
            if connection.vendor == "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA read_uncommitted = 1")
            return fn()
        finally:
            connection.close()

    def legacy_session(self):
        self.client.force_login(self.user)
        key = self.client.session.session_key
        ActivePortalSession.objects.filter(session_key=key).delete()
        return key, User.objects.get(pk=self.user.pk)

    def test_stale_reconciliation_cannot_revive_after_disable_and_enable(self):
        key, stale = self.legacy_session()
        ready, release = Event(), Event()
        def reconcile():
            ready.set()
            if not release.wait(10):
                raise AssertionError("Reconciliation was not released")
            return ActivePortalSessionService._insert_authenticated_registration(key=key, user=stale)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.connection_call, reconcile)
            try:
                self.assertTrue(ready.wait(10))
                self.manual(True)
                self.manual(False)
            finally:
                release.set()
            self.assertFalse(future.result(timeout=15))
        self.assertFalse(ActivePortalSession.objects.filter(session_key=key).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.login_access_version, 2)

    def test_reconciliation_first_is_removed_by_subsequent_disable(self):
        key, stale = self.legacy_session()
        held, attempted, release, finished = Event(), Event(), Event(), Event()
        def lock(ids, **kwargs):
            result = lock_login_users(ids, **kwargs)
            held.set()
            if not release.wait(10):
                raise AssertionError("Registry repair was not released")
            return result
        def disable():
            attempted.set()
            self.manual(True)
            finished.set()
        with patch("apps.accounts.login_obligations.lock_login_users", side_effect=lock), \
                ThreadPoolExecutor(max_workers=2) as pool:
            repair = pool.submit(self.connection_call, lambda: ActivePortalSessionService._insert_authenticated_registration(key=key, user=stale))
            try:
                self.assertTrue(held.wait(10))
                change = pool.submit(self.connection_call, disable)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(finished.wait(0.15))
            finally:
                release.set()
            self.assertTrue(repair.result(timeout=15))
            change.result(timeout=15)
        self.assertFalse(ActivePortalSession.objects.filter(session_key=key).exists())
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_disabled)
