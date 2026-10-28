"""Thursday hotfix contracts. Synthetic records/disposable test databases only."""
import asyncio
import time
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from . import automation as auto, gameplay as game, realtime, tests_phase4 as fixtures
from .models import QuiTizzResponse
from .public_views import cookie_name
from .services import QuiTizzService


class ReadinessGameplayTests(TestCase):
    setUpTestData = classmethod(fixtures.Phase4Tests.setUpTestData.__func__)
    new_user = classmethod(fixtures.Phase4Tests.new_user.__func__)
    grant = classmethod(fixtures.Phase4Tests.grant.__func__)
    for _helper in ["enable", "args", "quiz", "question", "content", "mutate", "url", "command", "lobby", "player", "answer", "open", "reveal", "step"]:
        locals()[_helper] = getattr(fixtures.Phase4Tests, _helper)

    def setUp(self):
        fixtures.Phase4Tests.setUp(self)
        SystemSettingService.set(FeatureSettingsService.QUITIZZ_AUTOMATIC_ENABLED_KEY, True,
            tenant_id=self.tenant.pk, value_type="BOOL")

    def test_ack_and_recovery_return_only_own_accepted_choice_without_correctness(self):
        participant, token = self.player("First")
        _, other = self.player("Other")
        question = self.open(1)
        browser = Client(); browser.cookies[cookie_name(self.session.public_id)] = token
        ack = browser.post(self.url("answer"), urlencode({"question": question.public_id, "choice": "A"}), content_type="application/x-www-form-urlencoded")
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(ack.json(), {"accepted": True, "question": str(question.public_id), "selected_choice": "A"})
        saved = QuiTizzResponse.objects.filter(participant=participant).values().get()
        self.command("close_question")
        retry = browser.post(self.url("answer"), urlencode({"question": question.public_id, "choice": "A"}), content_type="application/x-www-form-urlencoded")
        self.assertEqual(retry.json(), ack.json())
        state = browser.get(self.url("state")).json()
        self.assertEqual(state["selected_choice"], "A"); self.assertTrue(state["accepted"])
        for forbidden in ["correct_choice", "is_correct", "awarded_points", "feedback", "fastest_five", "leaderboard"]:
            self.assertNotIn(forbidden, state); self.assertNotIn(forbidden, ack.json())
        self.assertNotIn("selected_choice", game.state(self.session.public_id, other))
        self.assertEqual(QuiTizzResponse.objects.filter(participant=participant).values().get(), saved)
        self.assertEqual(browser.post(self.url("answer"), urlencode({"question": question.public_id, "choice": "B"}), content_type="application/x-www-form-urlencoded").status_code, 400)

    def prepare_phase(self, mode, phase):
        self.session = QuiTizzService.launch(**self.mutate(self.session.source))
        self.participant, self.token = self.player("Keep me")
        self.q = self.open(1)
        self.answer(self.token, self.q, received_at=self.q.opened_at)
        if mode == "AUTOMATIC": self.command("set_mode", mode=mode)
        if phase in {"SUSPENSE", "RESULTS", "PREPARING"}: self.command("close_question")
        if phase in {"RESULTS", "PREPARING"}: self.command("reveal")
        if phase == "PREPARING":
            while self.session.show_phase != "PREPARING": self.step()
        if phase == "PAUSED": self.command("pause")
        self.session.refresh_from_db(); self.q.refresh_from_db()

    def test_cancel_every_active_phase_preserves_records_and_allows_new_room(self):
        for mode, phases in [("MANUAL", ["ANSWERING", "SUSPENSE", "RESULTS", "PAUSED"]),
                             ("AUTOMATIC", ["ANSWERING", "SUSPENSE", "RESULTS", "PREPARING", "PAUSED"])]:
            for phase in phases:
                with self.subTest(mode=mode, phase=phase):
                    self.prepare_phase(mode, phase)
                    before = list(self.q.responses.values())
                    self.participant.refresh_from_db(); points = self.participant.total_score
                    version, old_phase = self.session.state_version, self.session.show_phase
                    with self.assertRaises(ValidationError): self.command("cancel")
                    self.session.refresh_from_db(); self.assertEqual(self.session.state_version, version)
                    self.command("cancel", confirm_end=True)
                    self.session.refresh_from_db(); self.q.refresh_from_db()
                    self.assertEqual(self.session.status, "CANCELLED"); self.assertFalse(self.session.joining_open)
                    self.assertIsNone(self.session.next_transition_at); self.assertIsNone(self.session.paused_at)
                    self.assertIsNotNone(self.q.closed_at)
                    self.assertEqual(list(self.q.responses.values()), before)
                    self.participant.refresh_from_db(); self.assertEqual(self.participant.total_score, points)
                    with self.assertRaises(game.Unavailable): self.answer(self.token, self.q)
                    self.assertFalse(auto.advance(self.session.pk, version=version, phase=old_phase, dispatch=lambda *_: None))
                    self.assertEqual(game.state(self.session.public_id, self.token)["status"], "CANCELLED")
                    for name in ["host_state", "projector_state"]:
                        response = self.client.get(self.url(name)); self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json()["status"], "CANCELLED"); self.assertNotIn("question", response.json())
                    self.assertContains(self.client.get(self.url("host")), "players must rejoin using its new QR/link")
                    self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_GAME_CONTROL", entity_type="QuiTizzSession", entity_id=str(self.session.pk), metadata_json__command="cancel",
                        metadata_json__early_end_confirmed=True).exists())
                    fresh = QuiTizzService.launch(**self.mutate(self.session.source))
                    self.assertNotEqual(fresh.public_id, self.session.public_id); self.assertEqual(fresh.participants.count(), 0)
                    with self.assertRaises(game.Unavailable): game.state(fresh.public_id, self.token)

    def test_cancel_rejects_stale_host_version_and_json_explains_confirmation(self):
        self.prepare_phase("AUTOMATIC", "ANSWERING")
        url = self.url("host_command"); version = self.session.state_version
        self.assertTrue(self.step())
        stale = self.client.post(url, {"action": "cancel", "version": version, "confirm_end": "1"}, HTTP_ACCEPT="application/json")
        self.assertEqual(stale.status_code, 409); self.assertIn("Session changed", stale.json()["error"])
        missing = self.client.post(url, {"action": "cancel", "version": self.session.state_version}, HTTP_ACCEPT="application/json")
        self.assertEqual(missing.status_code, 400); self.assertIn("confirm", missing.json()["error"].lower())
        self.command("cancel", confirm_end=True)
        self.assertFalse(self.step(at=timezone.now()))

    def test_cancel_keeps_csrf_scope_owner_direct_deny_and_feature_checks(self):
        self.prepare_phase("MANUAL", "ANSWERING")
        url = self.url("host_command"); data = {"action": "cancel", "version": self.session.state_version, "confirm_end": "1"}
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.user)
        self.assertEqual(csrf.post(url, data).status_code, 403)
        for args in [self.args(user=self.other_user), self.args(campus=self.other_campus), self.args(tenant=self.other_tenant, campus=self.foreign_campus)]:
            from django.http import Http404
            with self.assertRaises((PermissionDenied, Http404)):
                game.command(**args, public_id=self.session.public_id, version=data["version"], action="cancel", confirm_end=True)
        self.enable(False)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.enable(True); self.grant(self.user, "quitizz.host", grant_type="DENY")
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.session.refresh_from_db(); self.assertEqual(self.session.status, "QUESTION_OPEN")

    def test_notifications_are_enqueued_only_after_commit_and_rollback_discards_them(self):
        from django.db import transaction
        with patch("apps.quitizz.automation.notifier.enqueue") as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    realtime.notify(self.session, "question_opened")
                    enqueue.assert_not_called()
            self.assertEqual(enqueue.call_count, 2)
            enqueue.reset_mock()
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        realtime.notify(self.session, "answer_received")
                        raise ValueError("Synthetic rollback")
                except ValueError: pass
            enqueue.assert_not_called()


class NotificationHotfixTests(SimpleTestCase):
    def test_stalled_channel_does_not_hold_committed_callback_and_recovers(self):
        entered, release, delivered, failed = Event(), Event(), Event(), Event()
        messages = []
        class Layer:
            async def group_send(self, name, event):
                if not release.is_set():
                    entered.set()
                    while not release.is_set(): await asyncio.sleep(.005)
                    failed.set(); raise ConnectionError("Synthetic unavailable channel")
                messages.append((name, event)); delivered.set()
        queue = auto.NotificationQueue()
        try:
            with patch("apps.quitizz.automation.notifier", queue), \
                 patch("apps.quitizz.realtime.transaction.on_commit", side_effect=lambda callback: callback()), \
                 patch("apps.quitizz.realtime.FeatureSettingsService.is_quitizz_enabled", return_value=True), \
                 patch("apps.quitizz.realtime.get_channel_layer", return_value=Layer()), \
                 self.assertLogs("apps.quitizz.realtime", level="WARNING"):
                start = time.perf_counter()
                realtime.notify(SimpleNamespace(pk=1, tenant_id=1), "question_opened")
                elapsed = time.perf_counter() - start
                self.assertTrue(entered.wait(2)); self.assertFalse(release.is_set())
                thread = queue.thread
                release.set(); self.assertTrue(failed.wait(2)); self.assertTrue(delivered.wait(2))
                delivered.clear(); realtime.notify(SimpleNamespace(pk=1, tenant_id=1), "answer_received")
                self.assertTrue(delivered.wait(2)); self.assertIs(queue.thread, thread)
                queue.stop()
                self.assertTrue(all(event == {"type":"quitizz.event","event":"sync_required"} for _, event in messages))
                print(f"QUITIZZ_QUEUED_COMMIT_CALLBACK {elapsed * 1000:.3f} ms; returned while synthetic channel blocked; subsequent publication recovered")
        finally:
            release.set(); queue.stop()

    def test_queue_retains_capacity_coalescing_payload_filter_and_single_worker(self):
        queue = auto.NotificationQueue(); self.assertEqual(queue.capacity, 256)
        # Hold the worker unscheduled to inspect the full pending capacity.
        queue.thread = Mock()
        event = {"type":"quitizz.event","event":"sync_required"}
        for _ in range(1000): queue.enqueue("same", event)
        self.assertEqual(len(queue.pending), 1)
        for index in range(300): queue.enqueue(f"group{index}", event)
        self.assertEqual(len(queue.pending), 256)
        with self.assertRaises(ValueError): queue.enqueue("unsafe", {**event, "choice":"B"})
        queue.stop(); queue.enqueue("late", event); self.assertEqual(len(queue.pending), 0)
