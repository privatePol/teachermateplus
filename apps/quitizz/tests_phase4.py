"""Phase 4 defaults, timing, privacy and navigation; disposable databases only."""
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import caches
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from . import gameplay as game, automation as auto, phase4, tests_gameplay as fixtures
from .models import QuiTizzResponse, QuiTizzSession
from .services import QuiTizzService, StaleRevision


class Phase4Tests(TestCase):
    setUpTestData = classmethod(fixtures.GameplayTests.setUpTestData.__func__)
    new_user = classmethod(fixtures.GameplayTests.new_user.__func__)
    grant = classmethod(fixtures.GameplayTests.grant.__func__)
    for _name in ["enable", "args", "quiz", "question", "content", "mutate", "url", "command", "lobby", "player", "answer"]:
        locals()[_name] = getattr(fixtures.GameplayTests, _name)

    def setUp(self):
        caches["quitizz"].clear()
        self.enable()
        self.client.force_login(self.user)
        quiz = self.quiz()
        for index in range(3):
            self.question(quiz, prompt=f"Saved question {index + 1}")
        self.session = QuiTizzService.launch(**self.mutate(quiz))

    def open(self, position=None):
        self.session.refresh_from_db()
        if self.session.status == "READY":
            self.lobby()
        changes = {"question_id": str(self.session.questions.get(position=position).public_id)} if position else {}
        self.command("open_selected", **changes)
        self.session.refresh_from_db()
        return self.session.questions.get(position=self.session.current_position)

    def reveal(self):
        self.command("close_question")
        self.command("reveal")
        self.session.refresh_from_db()

    def step(self, at=None):
        self.session.refresh_from_db()
        with patch("django.utils.timezone.now", return_value=at or self.session.next_transition_at):
            result = auto.advance(self.session.pk, version=self.session.state_version, phase=self.session.show_phase, dispatch=lambda *_: None)
        self.session.refresh_from_db()
        return result

    def options(self, **changes):
        # Build a separate new launch, never rewrite the immutable policy.
        self.session = QuiTizzService.launch(**self.mutate(self.session.source), presentation=changes)

    def test_launch_defaults_and_immutable_policy(self):
        self.assertEqual(self.session.automation_policy_snapshot["version"], 2)
        self.assertEqual(phase4.policy(self.session)["presentation"], phase4.DEFAULTS)
        with self.assertRaises(ValidationError):
            game.write(self.session, automation_policy_snapshot={})
        for options in [{"answer_seconds": 0}, {"fastest_seconds": 61}, {"scores_seconds": True}, {"show_fastest": "true"}, {"unknown": 1}]:
            with self.subTest(options=options), self.assertRaises(ValidationError):
                QuiTizzService.launch(**self.mutate(self.session.source), presentation=options)

    def test_launch_form_defaults_and_invalid_duration(self):
        url = reverse("quitizz:launch", kwargs={"public_id": self.session.source.public_id})
        response = self.client.get(url)
        form = response.context["presentation_form"]
        self.assertTrue(form["show_fastest"].value())
        self.assertTrue(form["show_scores"].value())
        self.assertFalse(form["final_fastest"].value())
        response = self.client.post(url, {"revision": self.session.source.revision, "presentation_options": "1", "answer_seconds": 100, "fastest_seconds": 5, "scores_seconds": 5})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "less than or equal", status_code=400)

    def test_fastest_sizes_correct_removed_ties_and_exact_stored_ms(self):
        for count in [0, 1, 4, 5, 8]:
            with self.subTest(count=count):
                if count:
                    self.session = QuiTizzService.launch(**self.mutate(self.session.source))
                players = [self.player(f"Player {index}") for index in range(count + 2)]
                question = self.open()
                for index, (_, credential) in enumerate(players):
                    elapsed = 1234 if index < count else 500
                    # Reverse receipt order relative to PK; two rows share the
                    # exact receipt time so the last tie-break is exercised too.
                    receipt_us = 3 if index in [1, 2] else count + 2 - index
                    self.answer(credential, question, "B" if index < count else "A", received_at=question.opened_at + timedelta(microseconds=elapsed * 1000 + receipt_us))
                if count > 5:
                    self.command("remove", participant_id=players[0][0].public_id)
                self.assertEqual(phase4.fastest(question), [])
                self.reveal(); question.refresh_from_db()
                expected = list(question.responses.filter(is_correct=True, participant__removed_at__isnull=True).order_by("elapsed_ms", "received_at", "pk")[:5])
                rows = game.presentation_state(self.session)["fastest_five"]
                self.assertEqual([row["nickname"] for row in rows], [row.participant.nickname for row in expected])
                self.assertEqual([row["elapsed_ms"] for row in rows], [row.elapsed_ms for row in expected])
                self.assertEqual(len(rows), min(count, 5))

    def test_pause_deadline_retry_and_scoring_unchanged(self):
        player, credential = self.player()
        other, other_token = self.player("Boundary")
        third, late_token = self.player("Too late")
        question = self.open(); start = question.opened_at
        for seconds, action in [(4, "pause"), (40, "resume"), (44, "pause"), (80, "resume")]:
            with patch("django.utils.timezone.now", return_value=start + timedelta(seconds=seconds)):
                self.command(action)
        self.answer(credential, question, received_at=start + timedelta(seconds=82, microseconds=987))
        response = QuiTizzResponse.objects.get(participant=player)
        self.assertEqual(response.elapsed_ms, 10000)
        self.assertEqual(response.awarded_points, game.score(True, 10_000_987, 30_000_000))
        self.answer(credential, question, received_at=start + timedelta(seconds=83))
        self.assertEqual(question.responses.count(), 1)
        question.refresh_from_db()
        self.answer(other_token, question, received_at=question.deadline_at)
        self.assertEqual(question.responses.get(participant=other).elapsed_ms, 30000)
        with self.assertRaises(ValidationError):
            self.answer(late_token, question, received_at=question.deadline_at + timedelta(microseconds=1))

    def test_privacy_screens_manual_hold_and_reconnect(self):
        _, credential = self.player("Private")
        question = self.open(); self.answer(credential, question, received_at=question.opened_at)
        for action in [None, "close_question"]:
            if action: self.command(action)
            payload = json.dumps(game.presentation_state(self.session))
            for forbidden in ["fastest_five", "correct_choice", "Private", "fastest_correct_award"]:
                self.assertNotIn(forbidden, payload)
            with self.assertRaises(ValidationError): self.command("show_screen", screen="FASTEST_FIVE")
        self.command("reveal")
        for screen in phase4.SCREENS:
            self.command("show_screen", screen=screen)
            self.session.refresh_from_db()
            self.assertEqual(self.session.results_screen, screen)
            self.assertIsNone(self.session.next_transition_at)
            with patch("django.utils.timezone.now", return_value=question.opened_at + timedelta(minutes=20)):
                self.assertFalse(auto.advance(self.session.pk, version=self.session.state_version, phase="RESULTS"))
            self.assertEqual(self.client.get(self.url("host_state")).json()["results_screen"], screen)
        payload = json.dumps(game.state(self.session.public_id, credential))
        self.assertNotIn("fastest_five", payload)
        self.assertNotIn("navigator", payload)

    def test_selection_does_not_change_awarding_or_paused_live_question(self):
        self.open(3); self.reveal(); self.command("show_screen", screen="FASTEST_FIVE")
        selected = self.session.questions.get(position=2)
        self.command("select_question", question_id=str(selected.public_id))
        self.session.refresh_from_db()
        self.assertEqual((self.session.current_position, self.session.results_screen), (3, "FASTEST_FIVE"))
        self.assertEqual(self.open().position, 2)
        self.session.refresh_from_db(); self.assertIsNone(self.session.upcoming_question_id)
        now = self.session.questions.get(position=2).opened_at + timedelta(seconds=2)
        with patch("django.utils.timezone.now", return_value=now): self.command("pause")
        self.session.refresh_from_db(); remaining = self.session.pause_remaining_us
        self.command("select_question", question_id=str(self.session.questions.get(position=1).public_id))
        self.session.refresh_from_db()
        self.assertTrue(self.session.paused_at)
        self.assertEqual((self.session.current_position, self.session.pause_remaining_us), (2, remaining))
        with self.assertRaises(ValidationError): self.command("open_selected")

    def test_pending_resolver_out_of_order_no_replay_and_completion(self):
        for position in [3, 1, 2]:
            self.assertEqual(self.open(position).position, position)
            self.reveal()
            if position != 2:
                with self.assertRaises(ValidationError): self.command("complete")
            with self.assertRaises(ValidationError): self.command("select_question", question_id=str(self.session.questions.get(position=position).public_id))
        self.command("complete"); self.session.refresh_from_db()
        self.assertEqual(self.session.status, "COMPLETED")
        with self.assertRaises(StaleRevision): self.command("open_selected")
        self.assertEqual(list(self.session.questions.values_list("position", flat=True)), [1, 2, 3])

    def test_early_end_confirmation_audit_no_phantom_results(self):
        self.open(); self.reveal()
        for action in ["complete", "end_challenge", "cancel"]:
            with self.assertRaises(ValidationError): self.command(action)
        self.command("end_challenge", confirm_end=True)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "COMPLETED")
        self.assertEqual(self.session.questions.filter(opened_at__isnull=True).count(), 2)
        self.assertFalse(QuiTizzResponse.objects.exists())
        from apps.auditlog.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="QUITIZZ_GAME_CONTROL", metadata_json__early_end_confirmed=True).exists())

    def test_cross_session_stale_commands_and_scope_rejected(self):
        foreign = QuiTizzService.launch(**self.mutate(self.session.source)).questions.first()
        self.lobby()
        with self.assertRaises(ValidationError): self.command("select_question", question_id=str(foreign.public_id))
        self.session.refresh_from_db(); version = self.session.state_version
        selected = self.session.questions.last()
        self.command("select_question", question_id=str(selected.public_id))
        with self.assertRaises(StaleRevision):
            game.command(**self.args(), public_id=self.session.public_id, version=version, action="select_question", question_id=str(selected.public_id))
        for arguments in [self.args(user=self.other_user), self.args(campus=self.other_campus), self.args(tenant=self.other_tenant, campus=self.foreign_campus)]:
            from django.http import Http404
            with self.assertRaises((PermissionDenied, Http404)):
                game.command(**arguments, public_id=self.session.public_id, version=self.session.state_version, action="select_question", question_id=str(selected.public_id))
        self.grant(self.user, "quitizz.host", grant_type="DENY")
        with self.assertRaises(PermissionDenied): self.command("select_question", question_id=str(selected.public_id))

    def test_review_is_read_only_host_only_and_revealed_only(self):
        question = self.open()
        url = reverse("quitizz:host_review", kwargs={"public_id": self.session.public_id, "question_id": question.public_id})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.reveal(); before = QuiTizzSession.objects.filter(pk=self.session.pk).values().get()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200); self.assertTrue(response.json()["historical"])
        self.assertEqual(QuiTizzSession.objects.filter(pk=self.session.pk).values().get(), before)
        self.open(3)
        self.assertEqual(self.client.get(url).json()["question"]["position"], 1)
        self.assertEqual(self.session.current_position, 3)
        self.grant(self.user, "quitizz.host", grant_type="DENY")
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_csrf_and_expiry_apply_to_new_controls(self):
        self.lobby(); self.session.refresh_from_db()
        visitor = Client(enforce_csrf_checks=True); visitor.force_login(self.user)
        url = self.url("host_command")
        self.assertEqual(visitor.post(url, {"action": "select_question", "question": self.session.questions.first().public_id, "version": self.session.state_version}).status_code, 403)
        game.write(self.session, expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(self.url("host_state")).status_code, 404)

    def test_automatic_sequences_options_and_legacy_reader(self):
        from apps.core.services.features import FeatureSettingsService
        from apps.core.services.settings import SystemSettingService
        SystemSettingService.set(FeatureSettingsService.QUITIZZ_AUTOMATIC_ENABLED_KEY, True, tenant_id=self.tenant.pk, value_type="BOOL")
        for fastest, scores in [(True, True), (False, True), (True, False), (False, False)]:
            with self.subTest(fastest=fastest, scores=scores):
                self.options(show_fastest=fastest, show_scores=scores)
                self.open(); self.command("set_mode", mode="AUTOMATIC")
                self.assertTrue(self.step()); self.assertEqual(self.session.show_phase, "SUSPENSE")
                self.assertTrue(self.step()); self.assertEqual(self.session.results_screen, "ANSWER")
                self.assertEqual(self.session.next_transition_at - self.session.phase_started_at, timedelta(seconds=2))
                for screen in (["FASTEST_FIVE"] if fastest else []) + (["HIGHEST_SCORES"] if scores else []):
                    self.assertTrue(self.step()); self.assertEqual(self.session.results_screen, screen)
                    self.assertEqual(self.session.next_transition_at - self.session.phase_started_at, timedelta(seconds=5))
                self.assertTrue(self.step()); self.assertEqual(self.session.show_phase, "PREPARING")
                target = self.session.questions.last()
                old_version = self.session.state_version
                self.command("select_question", question_id=str(target.public_id))
                self.assertFalse(auto.advance(self.session.pk, version=old_version, phase="PREPARING"))
                self.assertTrue(self.step()); self.assertEqual(self.session.current_position, 3)
        legacy = self.session
        legacy.automation_policy_snapshot = {}
        self.assertEqual(phase4.policy(legacy)["results_seconds"], 7)
        self.assertFalse(phase4.modern(legacy))

    def test_auto_final_question_does_not_prepare_and_restart_full_screen(self):
        from apps.core.services.features import FeatureSettingsService
        from apps.core.services.settings import SystemSettingService
        SystemSettingService.set(FeatureSettingsService.QUITIZZ_AUTOMATIC_ENABLED_KEY, True, tenant_id=self.tenant.pk, value_type="BOOL")
        self.open(3); self.reveal(); self.open(1); self.reveal(); question = self.open(2)
        self.command("set_mode", mode="AUTOMATIC")
        self.step(); self.step()
        self.step(self.session.next_transition_at + timedelta(minutes=5))
        self.assertEqual(self.session.results_screen, "FASTEST_FIVE")
        self.assertEqual(self.session.next_transition_at - self.session.phase_started_at, timedelta(seconds=5))
        self.step(); self.step()
        self.assertEqual((self.session.status, self.session.show_phase), ("COMPLETED", "FINISHED"))

    def test_final_award_eligibility_exact_average_and_overall_unchanged(self):
        self.options(final_fastest=True)
        players = [self.player(name) for name in ["Below half", "Fast", "Champion"]]
        for position in [1, 2, 3]:
            question = self.open(position)
            for index, (_, credential) in enumerate(players):
                correct = position == 1 or (index > 0 and position == 2) or (index == 2 and position == 3)
                self.answer(credential, question, "B" if correct else "A", received_at=question.opened_at + timedelta(milliseconds=[1, 10, 20][index]))
            self.reveal()
        expected = list(game.ranked_participants(self.session).values_list("pk", flat=True))
        self.command("complete"); self.session.refresh_from_db()
        award = game.presentation_state(self.session)["fastest_correct_award"]
        self.assertEqual((award["nickname"], award["average_ms"], award["correct_count"]), ("Fast", "10.00", 2))
        self.assertEqual(list(game.ranked_participants(self.session).values_list("pk", flat=True)), expected)
        self.assertEqual(game.presentation_state(self.session)["leaderboard"][0]["nickname"], "Champion")
        self.assertNotIn("fastest_correct_award", game.state(self.session.public_id, players[0][1]))

    def test_award_no_eligible_removed_and_dual_winner(self):
        self.options(final_fastest=True)
        champion, credential = self.player("Dual winner")
        for position in [1, 2, 3]:
            question = self.open(position)
            self.answer(credential, question, received_at=question.opened_at)
            self.reveal()
        self.command("complete"); self.session.refresh_from_db()
        state = game.presentation_state(self.session)
        self.assertEqual(state["fastest_correct_award"]["nickname"], state["leaderboard"][0]["nickname"])
        self.session.participants.filter(pk=champion.pk).update(removed_at=timezone.now())
        self.assertIsNone(phase4.final_award(self.session))

    def test_award_fraction_order_and_tie_breaks(self):
        # Exact rational keys, not rounded display averages, decide the winner.
        self.options(final_fastest=True)
        players = [self.player(name) for name in ["Average A", "Average B"]]
        for position in [1, 2, 3]:
            question = self.open(position)
            for index, (_, token) in enumerate(players):
                elapsed = [10, 10, 11][position-1] if index == 0 else [10, 11, 10][position-1]
                self.answer(token, question, received_at=question.opened_at + timedelta(milliseconds=elapsed))
            self.reveal()
        self.command("complete"); self.session.refresh_from_db()
        winner = min(players, key=lambda pair: str(pair[0].public_id))[0]
        self.assertEqual(phase4.final_award(self.session)["nickname"], winner.nickname)
        self.session.participants.filter(pk=players[0][0].pk).update(total_score=99999)
        self.assertEqual(phase4.final_award(self.session)["nickname"], players[0][0].nickname)

    def test_award_count_tie_and_even_half_eligibility(self):
        quiz = self.session.source
        self.question(quiz, prompt="Fourth saved question")
        self.session = QuiTizzService.launch(**self.mutate(quiz), presentation={"final_fastest": True})
        players = [self.player(name) for name in ["Below", "Exactly half", "More correct"]]
        for position in [1, 2, 3, 4]:
            question = self.open(position)
            for index, (_, token) in enumerate(players):
                correct = position <= index + 1
                self.answer(token, question, "B" if correct else "A", received_at=question.opened_at + timedelta(milliseconds=10 if index else 0))
            self.reveal()
        self.command("complete"); self.session.refresh_from_db()
        self.assertEqual(phase4.final_award(self.session)["nickname"], "More correct")
        self.session.participants.filter(pk=players[2][0].pk).update(removed_at=timezone.now())
        self.assertEqual(phase4.final_award(self.session)["nickname"], "Exactly half")

    def test_award_orders_unequal_fraction_before_count_or_points(self):
        self.options(final_fastest=True)
        players = [self.player(name) for name in ["Two answers", "Three answers"]]
        for position in [1, 2, 3]:
            question = self.open(position)
            for index, (_, token) in enumerate(players):
                correct = index == 1 or position < 3
                elapsed = [10, 11, 0][position-1] if index == 0 else [10, 11, 11][position-1]
                self.answer(token, question, "B" if correct else "A", received_at=question.opened_at + timedelta(milliseconds=elapsed))
            self.reveal()
        self.command("complete"); self.session.refresh_from_db()
        # 21/2 < 32/3 even though the second contestant has more correct answers/points.
        self.assertEqual(phase4.final_award(self.session)["nickname"], "Two answers")

    def test_disabled_screens_and_final_award_stay_disabled(self):
        self.options(show_fastest=False, show_scores=False)
        for position in [1, 2, 3]:
            self.open(position); self.reveal()
            for screen in ["FASTEST_FIVE", "HIGHEST_SCORES"]:
                with self.assertRaises(ValidationError): self.command("show_screen", screen=screen)
            state = game.presentation_state(self.session)
            self.assertNotIn("fastest_five", state); self.assertNotIn("leaderboard", state)
        self.command("complete"); self.session.refresh_from_db()
        self.assertIsNone(phase4.final_award(self.session))
        self.assertIn("leaderboard", game.presentation_state(self.session))

    def test_selection_does_not_interrupt_live_question_and_stales_override(self):
        question = self.open()
        before = (question.opened_at, question.deadline_at)
        self.session.refresh_from_db(); version = self.session.state_version
        self.command("select_question", question_id=str(self.session.questions.last().public_id))
        question.refresh_from_db(); self.session.refresh_from_db()
        self.assertEqual((question.opened_at, question.deadline_at), before)
        self.assertEqual(self.session.current_position, 1)
        with self.assertRaises(StaleRevision):
            game.command(**self.args(), public_id=self.session.public_id, version=version, action="reveal_now")
        with self.assertRaises(ValidationError): self.command("open_selected")
