"""Real row-lock races; SQLite deliberately does not execute these bodies."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection, connections, close_old_connections
from django.test import TransactionTestCase

from . import automation as auto, gameplay as game
from .tests_automation_concurrency import _ensure_fixture_permissions
from . import tests_phase4 as fixtures
from .services import StaleRevision


@skipUnless(connection.vendor == "mysql" and connection.features.has_select_for_update,
            "Phase 4 races require an authorized disposable MariaDB/InnoDB database")
class Phase4InnoDBTests(TransactionTestCase):
    new_user = classmethod(fixtures.Phase4Tests.new_user.__func__)
    grant = classmethod(fixtures.Phase4Tests.grant.__func__)
    for _name in ["enable", "args", "quiz", "question", "content", "mutate", "url", "command", "lobby", "player", "answer", "open", "reveal", "step"]:
        locals()[_name] = getattr(fixtures.Phase4Tests, _name)

    def setUp(self):
        _ensure_fixture_permissions()
        fixtures.Phase4Tests.setUpTestData.__func__(type(self))
        fixtures.Phase4Tests.setUp(self)

    def race(self, first, second):
        barrier = Barrier(2)
        def run(work):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    return bool(work())
                except StaleRevision:
                    return False
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as workers:
            result = list(workers.map(run, [first, second]))
        self.assertEqual(sorted(result), [False, True])

    def test_selection_vs_scheduler_consumes_one_version(self):
        from apps.core.services.features import FeatureSettingsService
        from apps.core.services.settings import SystemSettingService
        SystemSettingService.set(FeatureSettingsService.QUITIZZ_AUTOMATIC_ENABLED_KEY, True, tenant_id=self.tenant.pk, value_type="BOOL")
        self.open(); self.command("set_mode", mode="AUTOMATIC")
        for _ in range(5): self.step()
        self.assertEqual(self.session.show_phase, "PREPARING")
        version, deadline = self.session.state_version, self.session.next_transition_at
        target = self.session.questions.get(position=3)
        with patch("django.utils.timezone.now", return_value=deadline):
            self.race(lambda: auto.advance(self.session.pk, version=version, phase="PREPARING", dispatch=lambda *_: None),
                lambda: game.command(**self.args(), public_id=self.session.public_id, version=version, action="select_question", question_id=str(target.public_id)))
        self.session.refresh_from_db()
        self.assertEqual(self.session.state_version, version + 1)
        self.assertIn(self.session.current_position, [1, 2])
        self.assertEqual(self.session.questions.filter(opened_at__isnull=False).count(), 2 if self.session.current_position == 2 else 1)

    def test_selection_vs_resume_consumes_one_version(self):
        self.open(); self.command("pause"); self.session.refresh_from_db()
        version = self.session.state_version
        target = self.session.questions.get(position=3)
        self.race(lambda: game.command(**self.args(), public_id=self.session.public_id, version=version, action="resume"),
            lambda: game.command(**self.args(), public_id=self.session.public_id, version=version, action="select_question", question_id=str(target.public_id)))
        self.session.refresh_from_db()
        self.assertEqual(self.session.state_version, version + 1)
        self.assertEqual(self.session.current_position, 1)
