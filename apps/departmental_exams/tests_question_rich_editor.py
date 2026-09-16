"""Focused Step B contracts for rich MCQ authoring and rendering.

Browser clipboard/caret behavior is intentionally not simulated here.  These
tests exercise the server boundary and the rendered editor contract using only
the disposable Django test database.
"""
from pathlib import Path

from django.test import Client
from django.urls import reverse

from .contribution_services import QuestionMutationService
from .models import ExamScenarioMember, Question
from .question_content import canonicalize_question_content, plain_text_to_rich_html
from .stage4_test_support import Stage4TestCase
from .tests_faculty_cases import FacultyCaseFixtureMixin
from .tests_stage5_contributions import Stage5FixtureMixin


def rich_payload(*, question_text="<p>Record the journal entry.</p>"):
    return {
        "question_text": question_text,
        "choice_a": (
            '<table><tbody><tr><td>Cash</td><td class="tmp-align-right">1,000</td></tr>'
            '<tr><td class="tmp-rule-single">Total</td><td class="tmp-align-right tmp-rule-single">1,000</td></tr>'
            "</tbody></table>"
        ),
        "choice_b": '<p class="tmp-indent-1">Debit expense</p><p>Credit cash</p>',
        "choice_c": "<p>Debit receivable</p>",
        "choice_d": "<p>Credit revenue <sup>2</sup> \\(\\alpha\\)</p>",
        "correct_answer": "B",
        "difficulty": "MODERATE",
        "content_format": Question.ContentFormat.RICH_HTML_V1,
    }


class RichQuestionEditorViewTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("rich-question-owner")
        self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        from .models import FacultyContribution

        self.contribution = FacultyContribution.objects.get(faculty_user=self.faculty)
        self.client.force_login(self.faculty)

    def _create_payload(self, **changes):
        self.contribution.refresh_from_db()
        return {
            "expected_contribution_revision": self.contribution.revision,
            **rich_payload(),
            **changes,
        }

    def test_standalone_editor_preview_save_edit_and_safe_render_contract(self):
        create_url = reverse("departmental_exams:question_create", args=[self.contribution.id])
        page = self.client.get(create_url)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertEqual(body.count("data-question-rich-field="), 5)
        for name, label in (
            ("question_text", "Question stem"), ("choice_a", "Choice A"),
            ("choice_b", "Choice B"), ("choice_c", "Choice C"), ("choice_d", "Choice D"),
        ):
            self.assertIn(f'data-question-rich-field="{name}"', body)
            self.assertIn(label, body)
            self.assertIn(f'data-question-preview-field="{name}"', body)
        self.assertIn("data-question-preview-button disabled", body)
        self.assertIn("data-question-save disabled", body)
        self.assertIn("data-question-paste-recovery", body)
        self.assertIn("The answer letter remains fixed to Choice A, B, C, or D", body)

        preview_url = reverse("departmental_exams:question_preview", args=[self.contribution.id])
        payload = self._create_payload()
        before = Question.objects.count()
        preview = self.client.post(preview_url, payload)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("no-store", preview["Cache-Control"])
        self.assertIn("private", preview["Cache-Control"])
        self.assertEqual(Question.objects.count(), before)
        fields = preview.json()["fields"]
        self.assertIn("tmp-rule-single", fields["choice_a"])
        self.assertIn("tmp-indent-1", fields["choice_b"])
        self.assertIn("<sup>2</sup>", fields["choice_d"])

        saved = self.client.post(create_url, {**payload, **fields})
        self.assertEqual(saved.status_code, 302)
        question = Question.objects.get(contribution=self.contribution)
        self.assertEqual(question.content_format, Question.ContentFormat.RICH_HTML_V1)
        self.assertEqual(question.correct_answer, "B")
        self.assertEqual(question.choice_a, fields["choice_a"])

        workspace = self.client.get(reverse("departmental_exams:contribution_workspace", args=[self.contribution.id]))
        self.assertContains(workspace, "<table>", html=False)
        self.assertContains(workspace, "tmp-rule-single", html=False)
        self.assertNotContains(workspace, "&lt;table&gt;", html=False)

        # A non-content edit keeps exact canonical content and the fixed B mapping.
        self.contribution.refresh_from_db()
        edited = self.client.post(
            reverse("departmental_exams:question_edit", args=[self.contribution.id, question.id]),
            {
                "expected_contribution_revision": self.contribution.revision,
                "expected_question_revision": question.revision,
                **{field: getattr(question, field) for field in (
                    "question_text", "choice_a", "choice_b", "choice_c", "choice_d",
                )},
                "content_format": question.content_format,
                "correct_answer": question.correct_answer,
                "difficulty": "DIFFICULT",
            },
        )
        self.assertEqual(edited.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.choice_a, fields["choice_a"])
        self.assertEqual(question.correct_answer, "B")
        self.assertEqual(question.difficulty, "DIFFICULT")

        self.contribution.refresh_from_db()
        answer_only = self.client.post(
            reverse("departmental_exams:question_edit", args=[self.contribution.id, question.id]),
            {
                "expected_contribution_revision": self.contribution.revision,
                "expected_question_revision": question.revision,
                **{field: getattr(question, field) for field in (
                    "question_text", "choice_a", "choice_b", "choice_c", "choice_d",
                )},
                "content_format": question.content_format,
                "correct_answer": "C",
                "difficulty": question.difficulty,
            },
        )
        self.assertEqual(answer_only.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.choice_a, fields["choice_a"])
        self.assertEqual(question.correct_answer, "C")

    def test_preview_rejects_oversized_table_without_persistence_or_content_echo(self):
        rows = "".join("<tr><td>private row</td></tr>" for _ in range(41))
        response = self.client.post(
            reverse("departmental_exams:question_preview", args=[self.contribution.id]),
            self._create_payload(question_text=f"<table><tbody>{rows}</tbody></table>"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("question_text", response.json()["errors"])
        self.assertNotIn("private row", response.content.decode())
        self.assertFalse(Question.objects.filter(contribution=self.contribution).exists())

    def test_preview_does_not_grant_another_faculty_owner_access(self):
        other = self.make_faculty("rich-question-other")
        self.make_assignment(self.parent, other)
        other_client = Client()
        other_client.force_login(other)
        response = other_client.post(
            reverse("departmental_exams:question_preview", args=[self.contribution.id]),
            self._create_payload(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Question.objects.filter(contribution=self.contribution).exists())

    def test_plain_literal_markup_remains_escaped_in_editor_and_display(self):
        question = QuestionMutationService.create(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            payload={
                "question_text": "Show <table> literally",
                "choice_a": "A", "choice_b": "B", "choice_c": "C", "choice_d": "D",
                "correct_answer": "A", "difficulty": "EASY",
            },
        )
        self.contribution.refresh_from_db()
        projected = {
            field: canonicalize_question_content(
                plain_text_to_rich_html(getattr(question, field)), field=field
            ).html
            for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d")
        }
        no_op = self.client.post(
            reverse("departmental_exams:question_edit", args=[self.contribution.id, question.id]),
            {
                "expected_contribution_revision": self.contribution.revision,
                "expected_question_revision": question.revision,
                **projected,
                "content_format": Question.ContentFormat.RICH_HTML_V1,
                "correct_answer": question.correct_answer,
                "difficulty": question.difficulty,
            },
        )
        self.assertEqual(no_op.status_code, 302)
        question.refresh_from_db()
        self.assertEqual(question.content_format, Question.ContentFormat.PLAIN_TEXT)
        self.assertEqual(question.question_text, "Show <table> literally")
        self.assertEqual(question.revision, 1)
        edit = self.client.get(reverse("departmental_exams:question_edit", args=[self.contribution.id, question.id]))
        self.assertContains(edit, "Show &lt;table&gt; literally", html=False)
        workspace = self.client.get(reverse("departmental_exams:contribution_workspace", args=[self.contribution.id]))
        self.assertContains(workspace, "Show &lt;table&gt; literally", html=False)

    def test_legacy_plain_post_without_content_format_remains_supported(self):
        self.contribution.refresh_from_db()
        response = self.client.post(
            reverse("departmental_exams:question_create", args=[self.contribution.id]),
            {
                "expected_contribution_revision": self.contribution.revision,
                "question_text": "Legacy plain route", "choice_a": "A", "choice_b": "B",
                "choice_c": "C", "choice_d": "D", "correct_answer": "A", "difficulty": "EASY",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Question.objects.get(contribution=self.contribution).content_format,
            Question.ContentFormat.PLAIN_TEXT,
        )


class RichLinkedQuestionEditorTests(FacultyCaseFixtureMixin, Stage4TestCase):
    def test_linked_editor_uses_the_same_preview_save_boundary_and_fixed_mapping(self):
        scenario = self.save_case()
        create_url = reverse(
            "departmental_exams:faculty_case_question_create",
            args=[self.contribution.id, scenario.id],
        )
        page = self.client.get(create_url)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.content.decode().count("data-question-rich-field="), 5)
        payload = {
            "expected_contribution_revision": self.contribution.revision,
            "scenario_id": scenario.id,
            "section_id": scenario.section_id,
            **rich_payload(),
        }
        preview = self.client.post(reverse("departmental_exams:question_preview", args=[self.contribution.id]), payload)
        self.assertEqual(preview.status_code, 200)
        saved = self.client.post(create_url, {**payload, **preview.json()["fields"]})
        self.assertEqual(saved.status_code, 302)
        question = self.contribution.questions.get()
        self.assertEqual(question.content_format, Question.ContentFormat.RICH_HTML_V1)
        self.assertEqual(question.correct_answer, "B")
        self.assertEqual(ExamScenarioMember.objects.get(question=question).scenario_id, scenario.id)

    def test_editor_source_contract_reuses_case_word_and_active_scientific_mechanics(self):
        source = Path(__file__).resolve().parents[2] / "static/js/departmental_exam_case_editor.js"
        editor_source = source.read_text(encoding="utf-8")
        scientific = (source.parent / "departmental_exam_scientific_notation.js").read_text(encoding="utf-8")
        for token in (
            "mountQuestionEditors", "normalizeClipboard", "createCaseEditor",
            "setAccountingRule", "tmp:scientific-rich-editor-ready", "previewSequence",
            "previewPending", "data-question-paste-recovery",
        ):
            self.assertIn(token, editor_source)
        self.assertIn("TMPScientificEditor", scientific)
        self.assertIn("data-scientific-rich-editor", scientific)
