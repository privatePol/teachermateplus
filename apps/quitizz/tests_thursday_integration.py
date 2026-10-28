"""Production-based Thursday integration contracts on disposable data."""
from datetime import timedelta

from django.test import TestCase

from . import gameplay as game, tests_phase4 as fixtures


class ThursdayIntegrationTests(TestCase):
    setUpTestData = classmethod(fixtures.Phase4Tests.setUpTestData.__func__)
    new_user = classmethod(fixtures.Phase4Tests.new_user.__func__)
    grant = classmethod(fixtures.Phase4Tests.grant.__func__)
    for _helper in ["enable", "args", "quiz", "question", "content", "mutate", "url", "command", "lobby", "player", "answer", "open", "reveal"]:
        locals()[_helper] = getattr(fixtures.Phase4Tests, _helper)

    def setUp(self):
        fixtures.Phase4Tests.setUp(self)

    def test_same_player_can_win_each_question_without_exposing_the_ranking_to_players(self):
        winner, winner_token = self.player("Repeat winner")
        other, other_token = self.player("Another player")
        for position in (1, 2):
            question = self.open(position)
            self.answer(winner_token, question, "B", received_at=question.opened_at + timedelta(seconds=1))
            self.answer(other_token, question, "B", received_at=question.opened_at + timedelta(seconds=2))
            self.reveal()
            self.command("show_screen", screen="FASTEST_FIVE")
            self.session.refresh_from_db()
            presentation = game.presentation_state(self.session)
            self.assertEqual([(row["rank"], row["nickname"]) for row in presentation["fastest_five"]],
                             [(1, winner.nickname), (2, other.nickname)])
            for token in (winner_token, other_token):
                private_state = game.state(self.session.public_id, token)
                self.assertNotIn("fastest_five", private_state)
                self.assertNotIn("leaderboard", private_state)
