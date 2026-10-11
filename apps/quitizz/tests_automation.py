import json
from datetime import timedelta
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction, IntegrityError
from django.test import TestCase, Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from apps.core.services.settings import SystemSettingService
from apps.core.services.features import FeatureSettingsService
from apps.auditlog.models import AuditLog
from . import tests_gameplay as fixtures, gameplay as game, automation as auto
from .models import QuiTizzSession, QuiTizzResponse
from .services import QuiTizzService, StaleRevision


class AutomationTests(TestCase):
    setUpTestData = classmethod(fixtures.GameplayTests.setUpTestData.__func__)
    new_user = classmethod(fixtures.GameplayTests.new_user.__func__)
    grant = classmethod(fixtures.GameplayTests.grant.__func__)
    enable = fixtures.GameplayTests.enable
    args = fixtures.GameplayTests.args
    quiz = fixtures.GameplayTests.quiz
    question = fixtures.GameplayTests.question
    content = fixtures.GameplayTests.content
    mutate = fixtures.GameplayTests.mutate
    launch = fixtures.GameplayTests.launch
    url = fixtures.GameplayTests.url
    command = fixtures.GameplayTests.command
    lobby = fixtures.GameplayTests.lobby
    player = fixtures.GameplayTests.player
    open_question = fixtures.GameplayTests.open_question
    answer = fixtures.GameplayTests.answer

    def setUp(self):
        fixtures.GameplayTests.setUp(self)
        self.automatic()
        self.participant, self.credential = self.player()
        self.command("set_mode", mode="AUTOMATIC")
        self.q = self.open_question()
        self.now = self.q.opened_at

    def automatic(self, enabled=True):
        SystemSettingService.set(FeatureSettingsService.QUITIZZ_AUTOMATIC_ENABLED_KEY, enabled,
            tenant_id=self.tenant.pk, value_type="BOOL")

    def step(self, at=None, **kwargs):
        self.session.refresh_from_db()
        with patch("django.utils.timezone.now", return_value=at or self.session.next_transition_at):
            result = auto.advance(self.session.pk, version=self.session.state_version, phase=self.session.show_phase, **kwargs)
        self.session.refresh_from_db(); self.q.refresh_from_db()
        return result

    def at_command(self, at, action, **kwargs):
        with patch("django.utils.timezone.now", return_value=at):
            return self.command(action, **kwargs)

    def test_full_flow_and_final_no_preparation(self):
        self.assertTrue(self.step()); self.assertEqual(self.session.show_phase, "SUSPENSE")
        self.assertEqual(self.q.closed_at, self.q.deadline_at)
        close = self.session.phase_started_at
        self.assertEqual(self.session.next_transition_at, close + timedelta(seconds=5))
        self.assertFalse(self.step(close + timedelta(seconds=5, microseconds=-1)))
        self.assertTrue(self.step()); self.assertEqual(self.session.show_phase, "RESULTS")
        self.assertEqual(self.session.next_transition_at, self.session.phase_started_at + timedelta(seconds=7))
        self.assertTrue(self.step()); self.assertEqual(self.session.show_phase, "PREPARING")
        self.assertEqual(self.session.next_transition_at, self.session.phase_started_at + timedelta(seconds=5))
        self.assertTrue(self.step()); self.assertEqual((self.session.current_position, self.session.show_phase), (2, "ANSWERING"))
        for _ in range(3): self.assertTrue(self.step())
        self.assertEqual((self.session.status, self.session.show_phase, self.session.next_transition_at), ("COMPLETED", "FINISHED", None))
        self.participant.refresh_from_db(); self.assertEqual(self.participant.final_rank, 1)

    def test_restart_closes_old_deadline_but_gives_full_suspense(self):
        later = self.q.deadline_at + timedelta(minutes=10)
        self.step(later)
        self.assertEqual(self.q.closed_at, self.q.deadline_at)
        self.assertEqual(self.session.next_transition_at, later + timedelta(seconds=5))
        self.assertEqual(self.session.current_position, 1)

    def test_exact_deadline_and_late_answer(self):
        self.answer(self.credential, self.q, received_at=self.q.deadline_at)
        self.assertTrue(self.step(self.q.deadline_at))
        with self.assertRaises(ValidationError):
            self.answer(self.credential, self.q, choice="A", received_at=self.q.deadline_at + timedelta(microseconds=1))

    def test_duplicate_scheduler_and_stale_version_do_not_audit_twice(self):
        version = self.session.state_version
        self.step(); count = AuditLog.objects.count()
        with patch("django.utils.timezone.now", return_value=self.session.next_transition_at):
            self.assertFalse(auto.advance(self.session.pk, version=version, phase="ANSWERING"))
        self.assertEqual(AuditLog.objects.count(), count)

    def test_host_override_invalidates_captured_scheduler_work(self):
        version = self.session.state_version
        self.command("reveal_now")
        self.assertFalse(auto.advance(self.session.pk, version=version, phase="ANSWERING"))
        self.session.refresh_from_db(); self.assertEqual(self.session.status, "ANSWER_REVEALED")

    def test_pause_answering_excludes_paused_time(self):
        self.at_command(self.now + timedelta(seconds=10), "pause")
        self.session.refresh_from_db(); self.assertEqual(self.session.pause_remaining_us, 20_000_000)
        with self.assertRaises(ValidationError):
            self.answer(self.credential, self.q, received_at=self.now + timedelta(seconds=11))
        self.at_command(self.now + timedelta(seconds=110), "resume")
        self.q.refresh_from_db(); self.assertEqual(self.q.opened_at, self.now)
        self.answer(self.credential, self.q, received_at=self.now + timedelta(seconds=115))
        response = QuiTizzResponse.objects.get(participant=self.participant)
        self.assertEqual((response.elapsed_ms, response.awarded_points), (15000, 850))

    def test_multiple_pauses_and_stale_receipt_segment(self):
        self.at_command(self.now + timedelta(seconds=4), "pause")
        self.at_command(self.now + timedelta(seconds=40), "resume")
        self.at_command(self.now + timedelta(seconds=44), "pause")
        self.at_command(self.now + timedelta(seconds=80), "resume")
        with self.assertRaises(ValidationError): self.answer(self.credential, self.q, received_at=self.now + timedelta(seconds=43))
        self.answer(self.credential, self.q, received_at=self.now + timedelta(seconds=82))
        self.assertEqual(QuiTizzResponse.objects.get(participant=self.participant).elapsed_ms, 10000)

    def test_pause_each_presentation_phase_restores_remaining(self):
        for phase in ["SUSPENSE", "RESULTS", "PREPARING"]:
            self.step(); self.assertEqual(self.session.show_phase, phase)
            start = self.session.phase_started_at
            remaining = self.session.next_transition_at - start - timedelta(seconds=1)
            self.at_command(start + timedelta(seconds=1), "pause")
            self.session.refresh_from_db(); self.assertIsNone(self.session.next_transition_at)
            state = game.state(self.session.public_id, self.credential)
            self.assertTrue(state["paused"]); self.assertFalse(state["can_answer"])
            self.at_command(start + timedelta(seconds=100), "resume")
            self.session.refresh_from_db()
            self.assertEqual(self.session.next_transition_at, start + timedelta(seconds=100) + remaining)

    def test_pause_after_timeout_resume_does_not_add_answer_time(self):
        self.at_command(self.q.deadline_at + timedelta(seconds=9), "pause")
        self.session.refresh_from_db(); self.assertEqual(self.session.pause_remaining_us, 0)
        self.at_command(self.q.deadline_at + timedelta(seconds=100), "resume")
        original_cutoff = self.q.deadline_at
        self.q.refresh_from_db(); self.assertEqual(self.q.deadline_at, original_cutoff)
        with self.assertRaises(ValidationError):
            self.answer(self.credential, self.q, received_at=original_cutoff + timedelta(seconds=100))
        self.assertTrue(self.step()); self.assertEqual(self.session.show_phase, "SUSPENSE")

    def test_feature_off_suspends_without_reveal(self):
        self.enable(False); self.step()
        self.assertTrue(self.session.paused_at); self.assertIsNone(self.q.revealed_at)

    def test_automatic_off_suspends_and_manual_recovery(self):
        self.automatic(False); auto.suspend_tenant(self.tenant.pk)
        self.session.refresh_from_db(); self.q.refresh_from_db()
        self.assertEqual(self.session.pause_reason, "ACCESS_UNAVAILABLE")
        with self.assertRaises(PermissionDenied): self.command("resume")
        self.command("set_mode", mode="MANUAL"); self.command("resume")
        self.session.refresh_from_db(); self.assertIsNone(self.session.next_transition_at)

    def test_direct_deny_suspends(self):
        self.grant(self.user, "quitizz.host", grant_type="DENY")
        self.step(); self.assertTrue(self.session.paused_at)

    def test_inactive_host_and_scope_suspend(self):
        self.user.is_active = False; self.user.save(update_fields=["is_active"])
        auto.safety_check(self.session.pk, healthy=True, dispatch=lambda *_: None)
        self.session.refresh_from_db(); self.assertTrue(self.session.paused_at)

    def test_inactive_campus_suspend(self):
        self.campus.is_active = False; self.campus.save(update_fields=["is_active"])
        self.step(); self.assertTrue(self.session.paused_at)

    def test_confirmed_throttle_failure_and_recovery_require_resume(self):
        with patch.object(auto.caches["quitizz"], "get", side_effect=ConnectionError("private connection")):
            self.assertFalse(auto.throttle_healthy())
            with self.assertRaises(ValidationError): self.command("reveal_now")
            auto.safety_check(self.session.pk, healthy=False, dispatch=lambda *_: None)
            with self.assertRaises(ValidationError): self.command("resume")
        self.session.refresh_from_db(); self.assertEqual(self.session.pause_reason, "THROTTLE_UNAVAILABLE")
        with self.assertRaises(ValidationError): self.command("reveal_now")
        with self.assertRaises(ValidationError): self.command("next_now")
        self.command("resume"); self.session.refresh_from_db(); self.assertFalse(self.session.paused_at)

    def test_notification_loss_does_not_rollback_or_stall(self):
        notifications = []
        with self.captureOnCommitCallbacks(execute=True):
            self.step(dispatch=lambda *args: notifications.append(args))
        self.assertEqual(len(notifications), 2)
        self.assertTrue(all(event == {"type": "quitizz.event", "event": "sync_required"} for _, event in notifications))

    def test_audit_failure_rolls_back_session_and_question(self):
        with patch.object(QuiTizzService, "audit", side_effect=RuntimeError("audit fail")):
            with self.assertRaises(RuntimeError): self.step()
        self.session.refresh_from_db(); self.q.refresh_from_db()
        self.assertEqual(self.session.show_phase, "ANSWERING"); self.assertIsNone(self.q.closed_at)

    def test_no_key_or_distribution_before_reveal(self):
        self.step()
        for state in [game.presentation_state(self.session), game.state(self.session.public_id, self.credential)]:
            encoded = json.dumps(state)
            for forbidden in ["correct_choice", "distribution", "awarded_points", '"leaderboard"']:
                self.assertNotIn(forbidden, encoded)

    def test_next_override_advances_one_and_final_invalid_next(self):
        self.command("reveal_now"); self.command("next_now")
        self.session.refresh_from_db(); self.assertEqual((self.session.current_position, self.session.status), (2, "QUESTION_OPEN"))
        self.command("reveal_now")
        with self.assertRaises(ValidationError): self.command("next_now")

    def test_early_end_cancels_without_key_or_champion(self):
        self.command("end_challenge"); self.session.refresh_from_db()
        state = game.presentation_state(self.session)
        self.assertEqual(state["status"], "CANCELLED"); self.assertNotIn("leaderboard", state); self.assertNotIn("reveal", state)

    def test_mode_flips_do_not_reset_deadline_or_score(self):
        self.answer(self.credential, self.q, received_at=self.now)
        deadline = self.q.deadline_at
        self.command("set_mode", mode="MANUAL"); self.command("set_mode", mode="AUTOMATIC")
        self.q.refresh_from_db(); self.assertEqual(self.q.deadline_at, deadline)
        self.participant.refresh_from_db(); self.assertEqual(self.participant.total_score, 1000)
        self.assertEqual(QuiTizzResponse.objects.count(), 1)

    def test_new_sessions_manual_by_default_and_auto_gated(self):
        source = self.session.source
        manual = self.launch(source); self.assertEqual(manual.playback_mode, "MANUAL")
        self.automatic(False)
        with self.assertRaises(ValidationError): QuiTizzService.launch(**self.mutate(source), playback_mode="AUTOMATIC")
        self.automatic(); launched = QuiTizzService.launch(**self.mutate(source), playback_mode="AUTOMATIC")
        self.assertEqual(launched.automation_policy_snapshot, auto.POLICY)

    def test_policy_snapshot_is_immutable(self):
        with self.assertRaises(ValidationError): game.write(self.session, automation_policy_snapshot={})

    def test_due_query_is_one_and_bounded(self):
        with CaptureQueriesContext(connection) as queries:
            candidates = auto.due_candidates(self.q.deadline_at, batch_size=1)
        self.assertEqual(len(queries), 1); self.assertEqual(len(candidates), 1)
        self.assertIn("qts_auto_due_idx", {index.name for index in QuiTizzSession._meta.indexes})

    def test_independent_sessions_are_not_advanced(self):
        second = self.launch(self.session.source)
        for action in ["set_mode", "open_joining", "start", "open_question"]:
            game.command(**self.args(), public_id=second.public_id, version=second.state_version,
                action=action, mode="AUTOMATIC" if action == "set_mode" else None)
            second.refresh_from_db()
        second_version = second.state_version
        self.step(); second.refresh_from_db()
        self.assertEqual((second.status, second.state_version), ("QUESTION_OPEN", second_version))
        with patch("django.utils.timezone.now", return_value=second.next_transition_at):
            self.assertTrue(auto.advance(second.pk, version=second_version, phase="ANSWERING", dispatch=lambda *_: None))
        second.refresh_from_db(); self.session.refresh_from_db()
        self.assertEqual((second.show_phase, self.session.show_phase), ("SUSPENSE", "SUSPENSE"))

    def test_snapshot_retries_changed_version(self):
        calls = []
        def build(session):
            calls.append(1); result = game._presentation_state(session)
            if len(calls) == 1: self.command("pause")
            return result
        result = game.consistent_snapshot(self.session, build)
        self.assertEqual(len(calls), 2); self.assertTrue(result["paused"])

    def test_full_join_link_fragment_rotation_and_no_public_leak(self):
        self.command("open_joining")
        first = self.client.get(self.url("host_join_link"))
        self.assertEqual(first.status_code, 200); self.assertIn("no-store", first["Cache-Control"])
        url = first.json()["join_url"]; self.assertIn("#", url); self.assertNotIn("?", url)
        self.command("close_joining"); self.assertNotEqual(self.client.get(self.url("host_join_link")).status_code, 200)
        self.command("open_joining"); self.assertNotEqual(url, self.client.get(self.url("host_join_link")).json()["join_url"])
        self.assertNotIn("join_url", game.state(self.session.public_id, self.credential))

    def test_join_link_requires_host_owner_and_live_session(self):
        self.command("open_joining")
        self.assertNotEqual(Client().get(self.url("host_join_link")).status_code, 200)
        self.grant(self.user, "quitizz.host", grant_type="DENY")
        self.assertEqual(self.client.get(self.url("host_join_link")).status_code, 403)

    def test_pause_constraints_reject_invalid_schedule(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            game.write(self.session, paused_at=timezone.now())
