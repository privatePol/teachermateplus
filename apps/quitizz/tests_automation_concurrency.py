"""Real locking acceptance: deliberately skipped outside MySQL/MariaDB.

Run only against a separately authorized disposable InnoDB test database.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch, MagicMock
from django.db import connection, connections, close_old_connections
from django.test import TransactionTestCase, SimpleTestCase, override_settings
from . import tests_automation as fixtures, automation as auto, gameplay as game
from .models import QuiTizzSession
from .services import StaleRevision


@skipUnless(connection.vendor == "mysql" and connection.features.has_select_for_update,
            "Real MariaDB/MySQL InnoDB acceptance requires an authorized disposable database")
class AutomationInnoDBTests(TransactionTestCase):
    new_user = classmethod(fixtures.AutomationTests.new_user.__func__)
    grant = classmethod(fixtures.AutomationTests.grant.__func__)
    for _name in ["enable", "args", "quiz", "question", "content", "mutate", "launch", "url", "command", "lobby", "player", "open_question", "answer", "automatic"]:
        locals()[_name] = getattr(fixtures.AutomationTests, _name)

    def setUp(self):
        fixtures.AutomationTests.setUpTestData.__func__(type(self))
        fixtures.AutomationTests.setUp(self)

    def test_two_workers_commit_exactly_one_transition(self):
        pk, version, deadline = self.session.pk, self.session.state_version, self.q.deadline_at
        barrier = Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return auto.advance(pk, version=version, phase="ANSWERING", dispatch=lambda *_: None)
            finally:
                connections.close_all()
        with patch("django.utils.timezone.now", return_value=deadline), ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.session.refresh_from_db(); self.assertEqual(self.session.state_version, version + 1)

    def test_scheduler_and_host_command_share_one_lock_and_version(self):
        pk, version = self.session.pk, self.session.state_version
        barrier = Barrier(2)
        def run(host):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if host:
                    try:
                        game.command(**self.args(), public_id=self.session.public_id, version=version, action="end_challenge")
                        return True
                    except StaleRevision:
                        return False
                return auto.advance(pk, version=version, phase="ANSWERING", dispatch=lambda *_: None)
            finally:
                connections.close_all()
        with patch("django.utils.timezone.now", return_value=self.q.deadline_at), ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, [True, False]))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(QuiTizzSession.objects.get(pk=pk).state_version, version + 1)


class NotificationQueueTests(SimpleTestCase):
    def test_queue_caps_and_coalesces_without_starting_extra_workers(self):
        queue = auto.NotificationQueue(capacity=2)
        event = {"type": "quitizz.event", "event": "sync_required"}
        with patch("apps.quitizz.automation.Thread.start") as start:
            for _ in range(100): queue.enqueue("host", event)
            queue.enqueue("players", event); queue.enqueue("overflow", event)
        self.assertEqual(list(queue.pending), ["host", "players"])
        self.assertEqual(start.call_count, 1)
        with self.assertRaises(ValueError): queue.enqueue("host", {"event": "sync_required", "answer": "B"})

    def test_notification_is_registered_only_after_commit(self):
        from types import SimpleNamespace
        from . import realtime
        callbacks = []
        session = SimpleNamespace(pk=1, tenant_id=1)
        with patch.object(realtime.transaction, "on_commit", side_effect=callbacks.append), patch.object(realtime.FeatureSettingsService, "is_quitizz_enabled", return_value=True):
            publish = []
            realtime.notify(session, "phase_changed", dispatch=lambda *args: publish.append(args))
            self.assertEqual(publish, [])
            callbacks[0]()
        self.assertEqual(len(publish), 2)


class SchedulerCommandTests(SimpleTestCase):
    def test_production_like_automation_rejects_process_local_throttling(self):
        for environment in ["staging", "production"]:
            with override_settings(DJANGO_ENV=environment, QUITIZZ_DEPLOYMENT=environment,
                    CACHES={"quitizz": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}):
                self.assertFalse(auto.throttle_healthy())

    @override_settings(DJANGO_ENV="local", QUITIZZ_DEPLOYMENT="local")
    def test_disposable_local_protection_probe_remains_supported(self):
        self.assertTrue(auto.throttle_healthy())

    @override_settings(QUITIZZ_SCHEDULER_ACTIVE_SECONDS=0.25, QUITIZZ_SCHEDULER_IDLE_SECONDS=2, QUITIZZ_SCHEDULER_BATCH_SIZE=100)
    def test_one_pass_is_bounded_cleans_connections_and_stops_publisher(self):
        from .management.commands.run_quitizz_scheduler import Command
        manager = MagicMock()
        manager.filter.return_value.exclude.return_value.order_by.return_value.values_list.return_value.__getitem__.return_value = [7]
        with patch("apps.quitizz.management.commands.run_quitizz_scheduler.QuiTizzSession.objects", manager), \
             patch.object(auto, "due_candidates", return_value=[(7, 2, "ANSWERING")]) as due, \
             patch.object(auto, "safety_check") as safety, patch.object(auto, "advance") as advance, \
             patch.object(auto, "throttle_healthy", return_value=True), patch.object(auto, "NotificationQueue") as queue, \
             patch("apps.quitizz.management.commands.run_quitizz_scheduler.close_old_connections") as close, \
             patch("apps.quitizz.management.commands.run_quitizz_scheduler.connections.close_all") as close_all:
            Command().handle(once=True)
        due.assert_called_once_with(batch_size=100)
        safety.assert_called_once_with(7, healthy=True, dispatch=queue.return_value.enqueue)
        advance.assert_called_once_with(7, version=2, phase="ANSWERING", dispatch=queue.return_value.enqueue)
        close.assert_called_once(); close_all.assert_called_once(); queue.return_value.stop.assert_called_once()

    @override_settings(QUITIZZ_SCHEDULER_ACTIVE_SECONDS=0, QUITIZZ_SCHEDULER_IDLE_SECONDS=2, QUITIZZ_SCHEDULER_BATCH_SIZE=100)
    def test_invalid_poll_interval_fails_before_any_database_query(self):
        from django.core.management.base import CommandError
        from .management.commands.run_quitizz_scheduler import Command
        with self.assertRaises(CommandError): Command().handle(once=True)
