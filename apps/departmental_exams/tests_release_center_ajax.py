import re
from pathlib import Path

from django.test import SimpleTestCase


class ReleaseCenterAjaxSourceContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        repository_root = Path(__file__).resolve().parents[2]
        cls.script = (
            repository_root / "static" / "js" / "departmental_exam_release_center.js"
        ).read_text(encoding="utf-8")
        cls.questionnaire_template = (
            repository_root
            / "templates"
            / "departmental_exams"
            / "admin"
            / "_questionnaire_release_pane.html"
        ).read_text(encoding="utf-8")
        cls.answer_key_template = (
            repository_root
            / "templates"
            / "departmental_exams"
            / "admin"
            / "_answer_key_release_pane.html"
        ).read_text(encoding="utf-8")

    def test_named_action_control_cannot_shadow_ajax_request_url(self):
        self.assertIn(
            'const requestUrl = form.getAttribute("action") || window.location.href;',
            self.script,
        )
        self.assertIn("window.fetch(requestUrl, {", self.script)
        self.assertNotIn("form.action || window.location.href", self.script)
        self.assertNotIn("window.fetch(form.action", self.script)

    def test_all_release_ajax_forms_keep_hidden_action_contract(self):
        expected_actions = (
            (
                self.questionnaire_template,
                {"bulk_release", "release", "revoke"},
            ),
            (
                self.answer_key_template,
                {
                    "bulk_answer_key_release",
                    "answer_key_release",
                    "answer_key_revoke",
                },
            ),
        )

        for template_source, expected_values in expected_actions:
            ajax_forms = re.findall(
                r'<form\b(?=[^>]*data-release-ajax="true")[^>]*>.*?</form>',
                template_source,
                flags=re.DOTALL,
            )
            self.assertTrue(ajax_forms)
            action_values = []
            for form_source in ajax_forms:
                action_control = re.search(
                    r'<input\b(?=[^>]*type="hidden")(?=[^>]*name="action")'
                    r'(?=[^>]*value="([^"]+)")[^>]*>',
                    form_source,
                )
                self.assertIsNotNone(action_control)
                action_values.append(action_control.group(1))

            self.assertEqual(set(action_values), expected_values)
