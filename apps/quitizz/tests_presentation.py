import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from apps.rbac.models import UserRole
from apps.core.services.scope import ScopeService
from . import gameplay as game
from . import tests_gameplay as phase2a


class PresentationTests(TestCase):
    setUpTestData = classmethod(phase2a.GameplayTests.setUpTestData.__func__)
    new_user = classmethod(phase2a.GameplayTests.new_user.__func__)
    grant = classmethod(phase2a.GameplayTests.grant.__func__)
    enable = phase2a.GameplayTests.enable
    args = phase2a.GameplayTests.args
    quiz = phase2a.GameplayTests.quiz
    content = phase2a.GameplayTests.content
    question = phase2a.GameplayTests.question
    mutate = phase2a.GameplayTests.mutate
    launch = phase2a.GameplayTests.launch
    setUp = phase2a.GameplayTests.setUp
    url = phase2a.GameplayTests.url
    command = phase2a.GameplayTests.command
    lobby = phase2a.GameplayTests.lobby
    player = phase2a.GameplayTests.player
    open_question = phase2a.GameplayTests.open_question
    answer = phase2a.GameplayTests.answer

    def projection(self):
        return self.client.get(self.url("projector_state")).json()

    def reveal(self):
        self.command("close_question")
        self.command("reveal")
        self.session.refresh_from_db()

    def test_projector_branding_no_saved_keys_or_mutation_controls(self):
        response = self.client.get(self.url("projector"))
        for text in ["QuiTizz", "Powered by TeacherMate+", "Scan. Play. Spark. Win."]:
            self.assertContains(response, text)
        for text in ["NCBA", "Unique question content", "correct_choice", "Host answer", 'name="action"']:
            self.assertNotContains(response, text)
        self.assertContains(self.client.get(self.url("host")), "Open Projector")

    def test_ready_lobby_and_prepared_do_not_expose_question_or_results(self):
        for action in [None, "open_joining", "start"]:
            if action:
                self.command(action)
            state = self.projection()
            for key in ["question", "reveal", "leaderboard", "participants"]:
                self.assertNotIn(key, state)
            self.assertEqual(state["question_count"], 2)
            self.assertEqual(state["participant_count"], 0)

    def test_open_and_locked_question_never_leak_key_scores_or_distribution(self):
        _, credential = self.player()
        question = self.open_question()
        self.answer(credential, question, received_at=question.opened_at)
        for action in [None, "close_question"]:
            if action:
                self.command(action)
            state = self.projection()
            self.assertEqual(state["question"]["prompt"], question.prompt)
            self.assertEqual(state["answered_count"], 1)
            for forbidden in ["correct_choice", "is_correct", "distribution", "score", "rank", "reconnect", "public_id", "selected_choice"]:
                self.assertNotIn(forbidden, json.dumps(state))

    def test_reveal_distribution_is_aggregate_with_all_four_choices(self):
        players = [self.player(name)[1] for name in ["Alpha", "Beta", "Gamma"]]
        question = self.open_question()
        for credential, choice in zip(players, "BBA"):
            self.answer(credential, question, choice=choice, received_at=question.opened_at)
        self.reveal()
        state = self.projection()
        self.assertEqual(state["reveal"], {"correct_choice": "B", "distribution": {"A": 1, "B": 2, "C": 0, "D": 0}})
        self.assertEqual([row["score"] for row in state["leaderboard"]], [1000, 1000, 0])
        self.assertTrue(all(set(row) == {"rank", "nickname", "score"} for row in state["leaderboard"]))
        self.assertNotIn("selected_choice", json.dumps(state))

    def test_zero_responses_and_no_participants(self):
        self.lobby()
        self.open_question()
        self.reveal()
        self.command("complete")
        state = self.projection()
        self.assertEqual(state["leaderboard"], [])
        self.assertEqual(state["reveal"]["distribution"], dict.fromkeys("ABCD", 0))
        self.assertEqual(state["answered_count"], 0)

    def test_fewer_than_three_zero_score_participants(self):
        self.player("First")
        self.player("Second")
        self.open_question()
        self.reveal()
        self.command("complete")
        state = self.projection()
        self.assertEqual(state["leaderboard"], [{"rank": 1, "nickname": "First", "score": 0}, {"rank": 2, "nickname": "Second", "score": 0}])

    def test_top_five_uses_existing_canonical_ranking_and_excludes_removed(self):
        for index in range(7):
            self.player(f"Player {index}")
        removed = self.session.participants.get(nickname="Player 0")
        self.command("remove", participant_id=removed.public_id)
        question = self.open_question()
        # Fixture scores deliberately exercise every ordering key; no JS scoring.
        for index, player in enumerate(game.ranked_participants(self.session)):
            game.write(player, total_score=100 if index < 4 else 0,
                correct_count=2 if index < 3 else 1, cumulative_response_ms=30 - index)
        self.reveal()
        expected = list(game.ranked_participants(self.session)[:5])
        self.assertEqual([row["nickname"] for row in self.projection()["leaderboard"]], [p.nickname for p in expected])
        self.command("complete")
        for row, player in zip(self.projection()["leaderboard"], expected):
            player.refresh_from_db()
            self.assertEqual(row["rank"], player.final_rank)
        self.assertNotIn(removed.nickname, json.dumps(self.projection()))

    def test_next_question_clears_reveal_and_leaderboard(self):
        self.player()
        self.open_question()
        self.reveal()
        self.command("next")
        state = self.projection()
        self.assertEqual(state["position"], 2)
        self.assertEqual(state["answered_count"], 0)
        self.assertNotIn("question", state)
        self.assertNotIn("reveal", state)
        self.assertNotIn("leaderboard", state)

    def test_cancelled_projection_hides_question_and_results(self):
        self.player()
        self.open_question()
        self.command("cancel")
        state = self.projection()
        self.assertNotIn("question", state)
        self.assertNotIn("reveal", state)
        self.assertNotIn("leaderboard", state)

    def test_projector_requires_login_permission_and_exact_owner(self):
        for name in ["projector", "projector_state"]:
            self.assertEqual(Client().get(self.url(name)).status_code, 302)
        self.client.force_login(self.other_user)
        for name in ["projector", "projector_state"]:
            self.assertEqual(self.client.get(self.url(name)).status_code, 403)
        self.grant(self.other_user, "quitizz.host")
        for name in ["projector", "projector_state"]:
            self.assertEqual(self.client.get(self.url(name)).status_code, 404)

    def test_projector_feature_off_and_direct_deny(self):
        self.enable(False)
        for name in ["projector", "projector_state"]:
            self.assertEqual(self.client.get(self.url(name)).status_code, 403)
        self.enable()
        self.grant(self.user, "quitizz.host", grant_type="DENY")
        for name in ["projector", "projector_state"]:
            self.assertEqual(self.client.get(self.url(name)).status_code, 403)

    def test_projector_rejects_genuinely_selectable_other_scope(self):
        for tenant, campus in [(self.tenant, self.other_campus), (self.other_tenant, self.foreign_campus)]:
            self.enable(tenant=tenant)
            UserRole.objects.create(user=self.user, role=self.role, tenant=tenant, campus=campus)
            for permission in ["faculty_portal.access", "quitizz.host"]:
                self.grant(self.user, permission, tenant=tenant, campus=campus)
            browser_session = self.client.session
            browser_session[ScopeService.SESSION_TENANT_KEY] = tenant.pk
            browser_session[ScopeService.SESSION_CAMPUS_KEY] = campus.pk
            browser_session.save()
            for name in ["projector", "projector_state"]:
                self.assertEqual(self.client.get(self.url(name)).status_code, 404)

    def test_projector_expiry_and_cache_headers(self):
        response = self.client.get(self.url("projector_state"))
        self.assertIn("no-store", response["Cache-Control"])
        game.write(self.session, expires_at=timezone.now() - timedelta(seconds=1))
        response = self.client.get(self.url("projector_state"))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(set(response.json()), {"error"})

    def test_players_receive_only_own_feedback_and_authorized_final_rank(self):
        _, correct = self.player("Correct player")
        _, incorrect = self.player("Incorrect player")
        question = self.open_question()
        self.answer(correct, question, received_at=question.opened_at)
        self.answer(incorrect, question, choice="A", received_at=question.opened_at)
        for credential in [correct, incorrect]:
            state = game.state(self.session.public_id, credential)
            self.assertTrue(state["accepted"])
            self.assertFalse(state["can_answer"])
            self.assertNotIn("feedback", state)
            self.assertNotIn("summary", state)
        self.reveal()
        self.assertTrue(game.state(self.session.public_id, correct)["feedback"]["is_correct"])
        self.assertFalse(game.state(self.session.public_id, incorrect)["feedback"]["is_correct"])
        self.command("complete")
        for credential, other, rank in [(correct, "Incorrect player", 1), (incorrect, "Correct player", 2)]:
            state = game.state(self.session.public_id, credential)
            self.assertEqual(state["summary"]["rank"], rank)
            self.assertNotIn(other, json.dumps(state))
