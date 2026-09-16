from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from .models import GeneratedExamItem, GeneratedExamSet, Question, QuestionIdentityReservation
from .stage4_test_support import Stage4TransactionTestCase
from .tests_faculty_cases import FacultyCaseFixtureMixin
from . import tests_questionnaire_print_release as print_fixtures


class CaseSnapshotMigrationTests(FacultyCaseFixtureMixin, Stage4TransactionTestCase):
    def test_historical_plain_text_survives_forward_and_rich_reverse_is_guarded(self):
        scenario = self.save_case()
        self.questions = [self.add_question(scenario=scenario, text=f"Legacy item {i}") for i in range(2)]
        revision = print_fixtures.QuestionnairePrintReleaseTests._make_revision(self, self.parent, revision_number=1)
        items = list(GeneratedExamItem.objects.filter(generated_set__generation_revision=revision).order_by("pk"))
        literal = "<b>Historical plain narrative</b>\nNot rich HTML"
        for item in items:
            GeneratedExamItem.objects.filter(pk=item.pk).update(
                source_scenario_id=scenario.id, scenario_id_snapshot=scenario.id,
                scenario_revision_snapshot=1, scenario_title_snapshot="Legacy Case",
                scenario_stimulus_snapshot=literal, scenario_member_position_snapshot=item.position)
        original_ids = [item.id for item in items]
        previous = [("departmental_exams", "0027_answer_key_release_target_scope")]
        latest = [("departmental_exams", "0031_question_rich_content")]
        try:
            # Model a pre-0031 plain-text database.  New fixture writes use the
            # current v4 service, but a genuine v4 reservation is precisely the
            # irreversible evidence covered by the separate reverse-guard test.
            QuestionIdentityReservation.objects.update(version="course-question-v3")
            MigrationExecutor(connection).migrate(previous)
            old_apps = MigrationExecutor(connection).loader.project_state(previous).apps
            historical = old_apps.get_model("departmental_exams", "GeneratedExamItem")
            self.assertEqual(list(historical.objects.filter(id__in=original_ids).order_by("id")
                                  .values_list("scenario_stimulus_snapshot", flat=True)), [literal] * 4)
            MigrationExecutor(connection).migrate(latest)
            migrated = list(GeneratedExamItem.objects.filter(id__in=original_ids).order_by("id"))
            self.assertEqual([item.id for item in migrated], original_ids)
            self.assertEqual([item.scenario_stimulus_snapshot for item in migrated], [literal] * 4)
            self.assertEqual({item.scenario_content_format_snapshot for item in migrated}, {"PLAIN_TEXT"})
            self.assertEqual(set(revision.generated_sets.values_list("structured_content_digest", flat=True)), {""})
            from .questionnaire_printing import _sanitized_questionnaire_context
            from django.template.loader import render_to_string
            html = render_to_string("departmental_exams/faculty/questionnaire_print.html",
                _sanitized_questionnaire_context(revision=revision, generated_set=revision.generated_sets.first()))
            self.assertIn("&lt;b&gt;Historical plain narrative&lt;/b&gt;", html)
            GeneratedExamSet.objects.filter(generation_revision=revision).update(structured_content_digest="a" * 64)
            with self.assertRaisesMessage(RuntimeError, "cannot be removed"):
                MigrationExecutor(connection).migrate(previous)
        finally:
            GeneratedExamSet.objects.filter(generation_revision=revision).update(structured_content_digest="")
            MigrationExecutor(connection).migrate(latest)

    def test_question_v4_reverse_guard_preserves_rich_and_identity_evidence(self):
        question = self.add_question(text="Rich question")
        Question.objects.filter(pk=question.pk).update(content_format="RICH_HTML_V1")
        previous = [("departmental_exams", "0030_course_question_identity")]
        latest = [("departmental_exams", "0031_question_rich_content")]
        try:
            with self.assertRaisesMessage(RuntimeError, "cannot be reversed safely"):
                MigrationExecutor(connection).migrate(previous)
            question.refresh_from_db()
            self.assertEqual(question.content_format, "RICH_HTML_V1")
        finally:
            Question.objects.filter(pk=question.pk).update(content_format="PLAIN_TEXT")
            MigrationExecutor(connection).migrate(latest)
