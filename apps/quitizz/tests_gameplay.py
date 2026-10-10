import json
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import gameplay as game, tests as foundation
from .models import QuiTizzParticipant, QuiTizzResponse, QuiTizzSessionQuestion, gameplay_write
from .public_views import cookie_name
from .services import StaleRevision


class GameplayTests(TestCase):
    # Reuse the accepted Phase 1 fixture without inheriting its test methods.
    setUpTestData = classmethod(foundation.QuiTizzFoundationTests.setUpTestData.__func__)
    new_user = classmethod(foundation.QuiTizzFoundationTests.new_user.__func__)
    grant = classmethod(foundation.QuiTizzFoundationTests.grant.__func__)
    enable = foundation.QuiTizzFoundationTests.enable
    args = foundation.QuiTizzFoundationTests.args
    quiz = foundation.QuiTizzFoundationTests.quiz
    content = foundation.QuiTizzFoundationTests.content
    question = foundation.QuiTizzFoundationTests.question
    mutate = foundation.QuiTizzFoundationTests.mutate
    launch = foundation.QuiTizzFoundationTests.launch

    def setUp(self):
        cache.clear()
        self.enable()
        self.client.force_login(self.user)
        quiz = self.quiz()
        self.question(quiz, prompt="Unique question content")
        self.question(quiz, prompt="Second question")
        self.session = self.launch(quiz)

    def url(self, name, session=None):
        return reverse(f"quitizz:{name}", kwargs={"public_id": (session or self.session).public_id})

    def command(self, action, **changes):
        self.session.refresh_from_db()
        return game.command(**{**self.args(), "public_id": self.session.public_id,
            "version": self.session.state_version, "action": action, **changes})

    def lobby(self):
        self.command("open_joining")
        self.session.refresh_from_db()

    def player(self, name="Player"):
        self.session.refresh_from_db()
        if self.session.status == "READY":
            self.lobby()
        grant = game.exchange(self.session.public_id, game.capability(self.session))
        return game.join(self.session.public_id, grant, name)

    def open_question(self):
        self.command("start")
        self.command("open_question")
        self.session.refresh_from_db()
        return self.session.questions.get(position=self.session.current_position)

    def answer(self, credential, question, choice="B", **extra):
        return game.submit(self.session.public_id, credential, question.public_id, choice, **extra)

    def test_valid_public_exchange_join_cookie_and_refresh(self):
        self.lobby()
        visitor = Client(enforce_csrf_checks=True)
        page = visitor.get(self.url("play"))
        self.assertEqual(page.status_code, 200)
        csrf = visitor.cookies[settings.CSRF_COOKIE_NAME].value
        headers = {"HTTP_X_CSRFTOKEN": csrf}
        from urllib.parse import urlencode
        exchanged = visitor.post(self.url("exchange"), urlencode({"capability": game.capability(self.session)}), content_type="application/x-www-form-urlencoded", **headers)
        self.assertEqual(exchanged.status_code, 200)
        response = visitor.post(self.url("join"), urlencode({"nickname": "  Alice  "}), content_type="application/x-www-form-urlencoded", **headers)
        self.assertEqual(response.status_code, 200)
        cookie = response.cookies[cookie_name(self.session.public_id)]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], self.url("play"))
        self.assertEqual(visitor.get(self.url("state")).json()["nickname"], "Alice")
        self.assertEqual(visitor.get(self.url("state")).json()["nickname"], "Alice")
        self.assertEqual(QuiTizzParticipant.objects.count(), 1)
        player = QuiTizzParticipant.objects.get()
        self.assertNotEqual(player.reconnect_digest, cookie.value)
        self.assertNotIn(cookie.value, response.content.decode())

    def test_secure_cookie_on_https(self):
        self.lobby()
        visitor = Client()
        from urllib.parse import urlencode
        visitor.post(self.url("exchange"), urlencode({"capability": game.capability(self.session)}), content_type="application/x-www-form-urlencoded", secure=True)
        response = visitor.post(self.url("join"), "nickname=HTTPS", content_type="application/x-www-form-urlencoded", secure=True)
        self.assertTrue(response.cookies[cookie_name(self.session.public_id)]["secure"])

    def test_invalid_capability_generic_response(self):
        self.lobby()
        response = Client().post(self.url("exchange"), "capability=bad", content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(str(self.tenant.pk), response.json()["error"])

    def test_missing_and_closed_join_rejected(self):
        with self.assertRaises(game.Unavailable):
            game.capability(self.session)
        self.lobby()
        token = game.capability(self.session)
        self.command("close_joining")
        with self.assertRaises(game.Unavailable):
            game.exchange(self.session.public_id, token)

    def test_expired_session_rejected(self):
        self.lobby()
        game.write(self.session, expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(game.Unavailable):
            game.exchange(self.session.public_id, "bad")

    def test_expired_signed_capability_rejected(self):
        self.lobby()
        with patch("django.core.signing.time.time", return_value=0):
            token = game.capability(self.session)
        with self.assertRaises(game.Unavailable):
            game.exchange(self.session.public_id, token)

    def test_expired_exchange_grant_rejected(self):
        self.lobby()
        with patch("django.core.signing.time.time", return_value=0):
            grant = game.exchange(self.session.public_id, game.capability(self.session))
        with self.assertRaises(game.Unavailable):
            game.join(self.session.public_id, grant, "Expired")

    def test_reopen_rotates_capability_generation(self):
        self.lobby()
        token = game.capability(self.session)
        self.command("close_joining")
        self.command("open_joining")
        with self.assertRaises(game.Unavailable):
            game.exchange(self.session.public_id, token)

    def test_duplicate_and_casefold_nickname(self):
        self.player("Straße")
        for name in ["Straße", " STRASSE ", "ＳＴＲＡＳＳＥ"]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.player(name)
        self.assertEqual(QuiTizzParticipant.objects.count(), 1)

    def test_canonical_normalization_is_deterministic_and_accent_sensitive(self):
        self.assertEqual(game.normalize_nickname("  Ａlice  "), game.normalize_nickname("Alice"))
        self.assertEqual(game.normalize_nickname("É")[1], game.normalize_nickname("E\u0301")[1])
        self.assertNotEqual(game.normalize_nickname("e")[1], game.normalize_nickname("é")[1])

    def test_nickname_validation(self):
        for value in ["", "   ", "x" * 33, "a\nB", "a\u202eB", None]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                game.normalize_nickname(value)
        self.assertEqual(len(game.normalize_nickname("x" * 32)[0]), 32)

    def test_join_integrity_error_is_translated_without_broken_transaction(self):
        self.lobby()
        with patch.object(QuiTizzParticipant.objects, "bulk_create", side_effect=IntegrityError("race")), self.assertRaises(ValidationError):
            self.player("Race")
        self.assertEqual(QuiTizzParticipant.objects.count(), 0)

    def test_database_unique_nickname(self):
        participant, _ = self.player()
        duplicate = QuiTizzParticipant(session=self.session, nickname="PLAYER", nickname_key=participant.nickname_key,
            reconnect_digest="0" * 64, reconnect_expires_at=participant.reconnect_expires_at)
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuiTizzParticipant.objects.bulk_create([duplicate])

    def test_hostile_nickname_escaped_in_host_html(self):
        self.player('<img src=x onerror="alert(1)">')
        response = self.client.get(self.url("host"))
        self.assertContains(response, "&lt;img")
        self.assertNotContains(response, '<img src=x onerror="alert(1)">')

    def test_cookie_identity_not_nickname_and_bad_secret_rejected(self):
        participant, credential = self.player()
        self.assertEqual(game.identity(self.session, credential).pk, participant.pk)
        for value in [participant.nickname, "bad", credential[:-1] + ("z" if credential[-1] != "z" else "x")]:
            with self.subTest(value=value), self.assertRaises(game.Unavailable):
                game.identity(self.session, value)

    def test_rejoin_valid_identity_restores_same_player(self):
        participant, credential = self.player()
        grant = game.exchange(self.session.public_id, game.capability(self.session))
        restored, same = game.join(self.session.public_id, grant, "Different", credential)
        self.assertEqual((restored.pk, same), (participant.pk, credential))
        self.assertEqual(QuiTizzParticipant.objects.count(), 1)

    def test_expired_and_removed_identity(self):
        participant, credential = self.player()
        QuiTizzParticipant.objects.filter(pk=participant.pk).update(reconnect_expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(game.Unavailable):
            game.state(self.session.public_id, credential)
        QuiTizzParticipant.objects.filter(pk=participant.pk).update(reconnect_expires_at=timezone.now() + timedelta(hours=1))
        self.command("remove", participant_id=participant.public_id)
        with self.assertRaises(game.Unavailable):
            game.state(self.session.public_id, credential)

    def test_session_isolation_token_identity_and_question(self):
        participant, credential = self.player()
        old = self.session
        quiz = self.quiz(); self.question(quiz)
        self.session = self.launch(quiz); self.lobby()
        with self.assertRaises(game.Unavailable):
            game.exchange(self.session.public_id, game.capability(old))
        with self.assertRaises(game.Unavailable):
            game.state(self.session.public_id, credential)
        _, second_credential = self.player("Second")
        self.open_question()
        with self.assertRaises(ValidationError):
            game.submit(self.session.public_id, second_credential, old.questions.first().public_id, "B")

    def test_wrong_public_id_generic(self):
        response = Client().get(reverse("quitizz:play", kwargs={"public_id": uuid.uuid4()}))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(set(response.json()), {"error"})

    def test_lifecycle_join_start_open_close_reveal_next_complete(self):
        _, credential = self.player()
        self.command("close_joining"); self.command("open_joining")
        question = self.open_question()
        self.assertIsNotNone(question.opened_at)
        self.assertEqual(question.deadline_at - question.opened_at, timedelta(seconds=30))
        self.assertEqual(self.session.scoring_policy_snapshot, game.POLICY)
        self.answer(credential, question)
        self.command("close_question"); self.command("reveal")
        self.assertEqual(game.state(self.session.public_id, credential)["status"], "ANSWER_REVEALED")
        self.command("next"); self.command("open_question")
        self.command("close_question"); self.command("reveal"); self.command("complete")
        state = game.state(self.session.public_id, credential)
        self.assertEqual(state["summary"]["rank"], 1)
        self.assertEqual(state["status"], "COMPLETED")
        self.assertEqual(QuiTizzResponse.objects.count(), 1)

    def test_stale_duplicate_host_command_rolls_back(self):
        version = self.session.state_version
        self.command("open_joining")
        with self.assertRaises(StaleRevision):
            self.command("start", version=version)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "LOBBY")

    def test_missing_and_invalid_host_versions(self):
        for value in [None, "bad", 0]:
            with self.subTest(value=value), self.assertRaises(StaleRevision):
                self.command("open_joining", version=value)

    def test_invalid_progression_and_reopen_rejected(self):
        self.lobby()
        for action in ["next", "reveal", "close_question", "complete", "unknown"]:
            with self.subTest(action=action), self.assertRaises(ValidationError):
                self.command(action)
        self.open_question()
        self.command("close_question")
        with self.assertRaises(ValidationError):
            self.command("open_question")

    def test_cancel_invalidates_join_answer_and_hides_question(self):
        _, credential = self.player(); question = self.open_question()
        self.command("cancel")
        self.assertNotIn("question", game.state(self.session.public_id, credential))
        with self.assertRaises(game.Unavailable):
            self.answer(credential, question)
        with self.assertRaises(game.Unavailable):
            game.capability(self.session)

    def test_completion_invalidates_join_and_mutations(self):
        self.player(); self.open_question()
        self.command("close_question"); self.command("reveal"); self.command("complete")
        self.session.refresh_from_db()
        with self.assertRaises(game.Unavailable):
            game.capability(self.session)
        with self.assertRaises(StaleRevision):
            self.command("open_joining")

    def test_single_answer_idempotent_retry_and_change_rejected(self):
        participant, credential = self.player(); question = self.open_question()
        first = self.answer(credential, question)
        self.assertEqual(self.answer(credential, question), first)
        with self.assertRaises(ValidationError):
            self.answer(credential, question, "A")
        participant.refresh_from_db()
        self.assertEqual(participant.correct_count, 1)
        self.assertEqual(QuiTizzResponse.objects.count(), 1)

    def test_duplicate_after_close_acknowledges_original_without_feedback(self):
        _, credential = self.player(); question = self.open_question()
        first = self.answer(credential, question)
        self.command("close_question")
        self.assertEqual(self.answer(credential, question), first)
        self.assertEqual(set(first), {"accepted", "question"})

    def test_wrong_and_stale_question_rejected(self):
        _, credential = self.player(); question = self.open_question()
        with self.assertRaises(ValidationError):
            self.answer(credential, self.session.questions.get(position=2))
        self.command("close_question"); self.command("reveal"); self.command("next"); self.command("open_question")
        with self.assertRaises(ValidationError):
            self.answer(credential, question)

    def test_closed_and_late_answer_rejected(self):
        _, credential = self.player(); question = self.open_question()
        with self.assertRaises(ValidationError):
            self.answer(credential, question, received_at=question.deadline_at + timedelta(microseconds=1))
        self.command("close_question")
        with self.assertRaises(ValidationError):
            self.answer(credential, question)
        self.assertFalse(QuiTizzResponse.objects.exists())

    def test_invalid_choice_rejected(self):
        _, credential = self.player(); question = self.open_question()
        for value in ["E", "b", "", "AB", None]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.answer(credential, question, value)

    def test_removed_participant_cannot_answer_or_retry(self):
        participant, credential = self.player(); question = self.open_question()
        self.answer(credential, question)
        self.command("remove", participant_id=participant.public_id)
        with self.assertRaises(game.Unavailable):
            self.answer(credential, question)

    def test_database_response_uniqueness_choice_score_and_relationship_validation(self):
        participant, credential = self.player(); question = self.open_question()
        self.answer(credential, question)
        response = QuiTizzResponse.objects.get()
        response.pk = None
        with self.assertRaises(IntegrityError), transaction.atomic():
            QuiTizzResponse.objects.bulk_create([response])
        for field, value in [("selected_choice", "E"), ("awarded_points", -1), ("elapsed_ms", -1), ("awarded_points", 1001)]:
            with self.subTest(field=field, value=value), self.assertRaises(IntegrityError), transaction.atomic():
                QuiTizzResponse.objects.update(**{field: value})
        quiz = self.quiz(); self.question(quiz); other = self.launch(quiz)
        response.session_question = other.questions.first()
        with self.assertRaises(ValidationError):
            response.clean()

    def test_score_formula_boundaries_and_clamping(self):
        self.assertEqual(game.score(False, 0, 30_000_000), 0)
        for elapsed, expected in [(-1, 1000), (0, 1000), (15_000_000, 850), (30_000_000, 700), (30_000_001, 700), (1, 999)]:
            with self.subTest(elapsed=elapsed):
                self.assertEqual(game.score(True, elapsed, 30_000_000), expected)

    def test_server_receipt_time_and_deadline_equality(self):
        _, credential = self.player(); question = self.open_question()
        self.answer(credential, question, received_at=question.deadline_at)
        response = QuiTizzResponse.objects.get()
        self.assertEqual((response.elapsed_ms, response.awarded_points), (30000, 700))

    def test_wrong_answer_zero_and_correct_midpoint(self):
        _, one = self.player("Wrong"); _, two = self.player("Correct")
        question = self.open_question()
        self.answer(one, question, "A", received_at=question.opened_at)
        self.answer(two, question, received_at=question.opened_at + timedelta(seconds=15))
        self.assertEqual(list(QuiTizzResponse.objects.order_by("awarded_points").values_list("awarded_points", flat=True)), [0, 850])

    def test_before_open_receipt_rejected(self):
        _, credential = self.player(); question = self.open_question()
        with self.assertRaises(ValidationError):
            self.answer(credential, question, received_at=question.opened_at - timedelta(microseconds=1))

    def test_client_score_and_elapsed_are_ignored(self):
        _, credential = self.player(); question = self.open_question()
        visitor = Client(); visitor.cookies[cookie_name(self.session.public_id)] = credential
        from urllib.parse import urlencode
        response = visitor.post(self.url("answer"), urlencode({"question": question.public_id, "choice": "A", "elapsed_ms": -999, "score": 100000, "received_at": question.opened_at.isoformat()}), content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(QuiTizzResponse.objects.get().awarded_points, 0)

    def test_deterministic_tie_sorting(self):
        players = [self.player(f"P{i}")[0] for i in range(5)]
        values = [(900, 1, 300), (900, 2, 500), (900, 2, 100), (1000, 1, 999), (900, 2, 100)]
        for player, (points, count, elapsed) in zip(players, values):
            QuiTizzParticipant.objects.filter(pk=player.pk).update(total_score=points, correct_count=count, cumulative_response_ms=elapsed)
        ordered = list(game.ranked_participants(self.session).values_list("pk", flat=True))
        self.assertEqual(ordered, [players[3].pk, players[2].pk, players[4].pk, players[1].pk, players[0].pk])

    def test_pre_reveal_html_json_and_ack_have_no_answer_key(self):
        _, credential = self.player(); question = self.open_question()
        self.answer(credential, question)
        visitor = Client(); visitor.cookies[cookie_name(self.session.public_id)] = credential
        for payload in [visitor.get(self.url("state")).content.decode(), visitor.get(self.url("play")).content.decode(), json.dumps(self.answer(credential, question))]:
            for forbidden in ["correct_choice", "is_correct", "awarded_points", "total_score", "scoring_policy", "Host answer", '"rank"']:
                self.assertNotIn(forbidden, payload)
        self.command("close_question")
        self.assertNotIn("feedback", game.state(self.session.public_id, credential))

    def test_post_reveal_feedback_only_after_host_reveal(self):
        _, credential = self.player(); question = self.open_question()
        self.answer(credential, question, received_at=question.opened_at)
        self.command("close_question"); self.command("reveal")
        feedback = game.state(self.session.public_id, credential)["feedback"]
        self.assertEqual(feedback, {"correct_choice": "B", "is_correct": True, "points": 1000, "answered": True})

    def test_feature_off_blocks_public_and_host_and_preserves_data(self):
        _, credential = self.player(); question = self.open_question()
        visitor = Client(); visitor.cookies[cookie_name(self.session.public_id)] = credential
        self.enable(False)
        for name in ["play", "state"]:
            response = visitor.get(self.url(name))
            self.assertEqual(response.status_code, 404)
            self.assertEqual(set(response.json()), {"error"})
        for name, body in [("join", "nickname=x"), ("exchange", "capability=bad"), ("answer", f"question={question.public_id}&choice=B")]:
            self.assertEqual(visitor.post(self.url(name), body, content_type="application/x-www-form-urlencoded").status_code, 404)
        with self.assertRaises(PermissionDenied):
            self.command("close_question")
        self.assertEqual(self.client.post(self.url("host_command"), {"version": self.session.state_version, "action": "cancel"}).status_code, 403)
        self.assertEqual(self.session.questions.count(), 2)
        self.assertEqual(self.session.participants.count(), 1)

    def test_on_does_not_grant_host_permission_and_direct_deny(self):
        with self.assertRaises(PermissionDenied):
            self.command("open_joining", user=self.other_user)
        self.grant(self.user, "quitizz.host", grant_type="DENY")
        with self.assertRaises(PermissionDenied):
            self.command("open_joining")

    def test_host_campus_tenant_and_owner_isolation(self):
        self.grant(self.user, "quitizz.host", campus=self.other_campus)
        self.grant(self.user, "faculty_portal.access", campus=self.other_campus)
        from django.http import Http404
        with self.assertRaises(Http404):
            self.command("open_joining", campus_id=self.other_campus.pk)
        with self.assertRaises(PermissionDenied):
            self.command("open_joining", tenant_id=self.other_tenant.pk, campus_id=self.foreign_campus.pk)
        self.grant(self.other_user, "quitizz.host")
        with self.assertRaises(Http404):
            self.command("open_joining", user=self.other_user)

    def test_cross_tenant_public_token_and_identity_fail(self):
        _, credential = self.player()
        quiz = self.quiz(); self.question(quiz); other = self.launch(quiz)
        # Synthetic second tenant session; create through accepted Phase 1 validation.
        from .models import QuiTizz, QuiTizzQuestion, QuiTizzSession
        from .services import QuiTizzService
        self.enable(tenant=self.other_tenant)
        self.grant(self.user, "faculty_portal.access", tenant=self.other_tenant, campus=self.foreign_campus)
        self.grant(self.user, "quitizz.host", tenant=self.other_tenant, campus=self.foreign_campus)
        source = QuiTizz.objects.create(tenant=self.other_tenant, campus=self.foreign_campus, owner=self.user, title="Foreign")
        QuiTizzQuestion.objects.create(quitizz=source, position=1, **self.content())
        foreign = QuiTizzService.launch(**self.args(tenant=self.other_tenant, campus=self.foreign_campus), public_id=source.public_id, revision=1)
        game.command(**self.args(tenant=self.other_tenant, campus=self.foreign_campus), public_id=foreign.public_id, version=1, action="open_joining")
        with self.assertRaises(game.Unavailable):
            game.exchange(foreign.public_id, game.capability(self.session))
        with self.assertRaises(game.Unavailable):
            game.state(foreign.public_id, credential)
        self.assertEqual(QuiTizzParticipant.objects.filter(session=foreign).count(), 0)

    def test_csrf_required_on_all_mutations(self):
        self.lobby()
        visitor = Client(enforce_csrf_checks=True)
        for name in ["exchange", "join", "answer"]:
            self.assertEqual(visitor.post(self.url(name), {}).status_code, 403)
        host = Client(enforce_csrf_checks=True); host.force_login(self.user)
        self.assertEqual(host.post(self.url("host_command"), {}).status_code, 403)

    def test_public_body_limit_and_wrong_content_type(self):
        self.lobby()
        visitor = Client()
        self.assertEqual(visitor.post(self.url("join"), "nickname=" + "x" * 5000, content_type="application/x-www-form-urlencoded").status_code, 413)
        self.assertEqual(visitor.post(self.url("join"), "{}", content_type="application/json").status_code, 413)

    def test_rate_limit_abstraction_and_response(self):
        for _ in range(3): game.throttle("test", self.session.public_id, "same", 3)
        with self.assertRaises(game.RateLimited): game.throttle("test", self.session.public_id, "same", 3)
        game.throttle("test", self.session.public_id, "other", 3)
        with patch("apps.quitizz.gameplay.throttle", side_effect=game.RateLimited()):
            response = Client().get(self.url("state"))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "60")

    def test_public_headers_no_storage_and_no_secrets(self):
        page = Client().get(self.url("play"))
        self.assertIn("no-store", page["Cache-Control"])
        self.assertEqual(page["Referrer-Policy"], "same-origin")
        self.assertNotContains(page, "Unique question content")
        self.assertNotContains(page, "tenant_id")

    def test_host_qr_and_controls_render(self):
        self.lobby()
        response = self.client.get(self.url("host_qr"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn("no-store", response["Cache-Control"])
        page = self.client.get(self.url("host"))
        self.assertContains(page, "Close joining")
        self.assertContains(page, "Start")
        self.assertContains(page, "Saved questions")
        self.assertEqual(self.client.get(self.url("host_command")).status_code, 405)

    def test_gameplay_writes_cannot_change_snapshot_content(self):
        question = self.session.questions.first()
        with gameplay_write(), self.assertRaises(ValidationError):
            QuiTizzSessionQuestion.objects.filter(pk=question.pk).update(correct_choice="A")

    def test_host_http_command_version_and_authorization(self):
        response = self.client.post(self.url("host_command"), {"action": "open_joining", "version": 1})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.post(self.url("host_command"), {"action": "start", "version": 1}).status_code, 409)
        self.assertEqual(self.client.get(self.url("host_state")).json()["status"], "LOBBY")
        self.client.force_login(self.other_user)
        self.assertEqual(self.client.post(self.url("host_command"), {"action": "cancel", "version": 2}).status_code, 403)

    def test_audit_failure_rolls_back_gameplay_question_and_session(self):
        self.player(); self.command("start")
        self.session.refresh_from_db()
        version = self.session.state_version
        with patch("apps.quitizz.services.AuditService.log_event", side_effect=RuntimeError("audit failure")), self.assertRaises(RuntimeError):
            self.command("open_question")
        self.session.refresh_from_db()
        self.assertEqual(self.session.state_version, version)
        self.assertIsNone(self.session.questions.get(position=1).opened_at)

    def test_remove_foreign_participant_does_not_mutate_session(self):
        self.lobby()
        with self.assertRaises(game.Unavailable):
            self.command("remove", participant_id=uuid.uuid4())
        self.session.refresh_from_db()
        self.assertEqual(self.session.state_version, 2)

    def test_closed_join_does_not_block_existing_identity(self):
        _, credential = self.player()
        self.command("close_joining")
        self.assertEqual(game.state(self.session.public_id, credential)["nickname"], "Player")

    def test_inactive_scope_blocks_public_paths(self):
        _, credential = self.player()
        from apps.tenants.models import Campus
        Campus.objects.filter(pk=self.campus.pk).update(is_active=False)
        with self.assertRaises(game.Unavailable):
            game.state(self.session.public_id, credential)

    def test_query_growth_and_100_synthetic_answers(self):
        credentials = []
        counts = {}
        for size in [1, 20, 100]:
            for i in range(len(credentials), size): credentials.append(self.player(f"P{i}")[1])
            with CaptureQueriesContext(connection) as host_queries: state = game.host_state(self.session)
            with CaptureQueriesContext(connection) as player_queries: game.state(self.session.public_id, credentials[0])
            counts[size] = (len(host_queries), len(player_queries))
            self.assertEqual(state["participant_count"], size)
        self.assertEqual(len(set(counts.values())), 1)
        question = self.open_question()
        answer_counts = {}
        for index, credential in enumerate(credentials, 1):
            with CaptureQueriesContext(connection) as queries:
                self.answer(credential, question, received_at=question.opened_at)
            if index in {1, 20, 100}: answer_counts[index] = len(queries)
        self.assertEqual(len(set(answer_counts.values())), 1)
        self.assertLess(answer_counts[100], 20)
        with CaptureQueriesContext(connection) as open_state_queries: game.state(self.session.public_id, credentials[0])
        self.assertEqual(QuiTizzResponse.objects.count(), 100)
        self.assertEqual(sum(self.session.participants.values_list("total_score", flat=True)), 100000)
        self.command("close_question"); self.command("reveal"); self.command("complete")
        self.assertEqual(sorted(self.session.participants.values_list("final_rank", flat=True)), list(range(1, 101)))
        print(f"QUITIZZ_QUERY_COUNTS lobby(host,state)={counts}; open_state={len(open_state_queries)}; accepted_answers={answer_counts}; synthetic=100 responses/100000 points/100 ranks")

    def test_no_server_ticks_and_modest_safe_client_transport(self):
        root = Path(settings.BASE_DIR)
        script = (root / "static/js/quitizz_play.js").read_text(encoding="utf-8")
        self.assertIn("}, 5000)", script)
        self.assertIn("window.history.replaceState", script)
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("WebSocket", script)


class GameplayMigrationTests(TransactionTestCase):
    def test_existing_phase1_questions_receive_distinct_public_ids_without_content_changes(self):
        from django.db.migrations.executor import MigrationExecutor
        before = [("quitizz", "0003_seed_faculty_navigation")]
        after = [("quitizz", "0004_quitizzparticipant_quitizzresponse_and_more")]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        try:
            apps = executor.loader.project_state(before).apps
            tenant = apps.get_model("tenants", "Tenant").objects.create(code="MIGQT", name="Migration test")
            campus = apps.get_model("tenants", "Campus").objects.create(tenant=tenant, code="M", name="Migration campus")
            # The live accounts schema includes later non-null fields. Insert
            # with the current model, then use its historical representation.
            from apps.accounts.models import User
            current_user = User.objects.create_user(username="qt-migration", email="migration@example.test", password="testpass")
            user = apps.get_model("accounts", "User").objects.get(pk=current_user.pk)
            source = apps.get_model("quitizz", "QuiTizz").objects.create(tenant=tenant, campus=campus, owner=user, title="Old snapshot")
            session = apps.get_model("quitizz", "QuiTizzSession").objects.create(source=source, tenant=tenant, campus=campus, host=user, title_snapshot=source.title, source_revision=1)
            questions = apps.get_model("quitizz", "QuiTizzSessionQuestion")
            for position in [1, 2]:
                questions.objects.create(session=session, position=position, prompt=f"Prompt {position}", choice_a="a", choice_b="b", choice_c="c", choice_d="d", correct_choice="B", timer_seconds=30)
            executor = MigrationExecutor(connection); executor.migrate(after)
            apps = executor.loader.project_state(after).apps
            rows = list(apps.get_model("quitizz", "QuiTizzSessionQuestion").objects.filter(session_id=session.pk).order_by("position"))
            self.assertEqual(len({row.public_id for row in rows}), 2)
            self.assertEqual([(row.prompt, row.correct_choice, row.timer_seconds) for row in rows], [("Prompt 1", "B", 30), ("Prompt 2", "B", 30)])
            saved = apps.get_model("quitizz", "QuiTizzSession").objects.get(pk=session.pk)
            self.assertEqual((saved.status, saved.scoring_policy_snapshot, saved.joining_open, saved.expires_at), ("READY", {}, False, None))
        finally:
            MigrationExecutor(connection).migrate(after)
