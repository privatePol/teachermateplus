from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from django.template.loader import render_to_string

from .contribution_services import QuestionPayloadService
from .duplicate_contract import VERSION, standalone_identity
from .question_content import (
    MAX_COLUMNS_PER_ROW,
    MAX_ROWS_PER_TABLE,
    PLAIN_TEXT,
    RICH_HTML_V1,
    canonicalize_question_content,
    canonicalize_question_fields,
    plain_text_to_rich_html,
    render_question_content,
)
from .scenario_content import canonicalize_scenario_content
from .generation_readiness import (
    LEGACY_QUESTION_CONTENT_DIGEST_VERSION,
    QUESTION_CONTENT_DIGEST_VERSION,
    generation_question_source_digest,
)


def payload(**changes):
    value = {
        "question_text": "Record the entry",
        "choice_a": "Debit Cash",
        "choice_b": "Credit Revenue",
        "choice_c": "Debit Expense",
        "choice_d": "Credit Payable",
        "correct_answer": "A",
        "difficulty": "EASY",
        "content_format": PLAIN_TEXT,
    }
    value.update(changes)
    return value


class QuestionContentTests(SimpleTestCase):
    def test_plain_projection_escapes_literal_html(self):
        self.assertEqual(
            plain_text_to_rich_html("Show <table> literally\nnext"),
            "<p class=\"tmp-preserve\">Show &lt;table&gt; literally<br>next</p>",
        )

    def test_case_canonicalizer_remains_its_own_profile(self):
        case = canonicalize_scenario_content('<p class="tmp-indent-8">Case</p>').html
        question = canonicalize_question_content('<p class="tmp-indent-8">Case</p>', field="question_text").html
        self.assertEqual(case, question)
        self.assertEqual(canonicalize_scenario_content(case).html, case)

    def test_question_table_geometry_is_bounded_below_case_profile(self):
        rows = "".join("<tr><td>A</td></tr>" for _ in range(MAX_ROWS_PER_TABLE + 1))
        with self.assertRaises(ValidationError):
            canonicalize_question_content(f"<table><tbody>{rows}</tbody></table>", field="choice_a")
        cells = "".join("<td>A</td>" for _ in range(MAX_COLUMNS_PER_ROW + 1))
        with self.assertRaises(ValidationError):
            canonicalize_question_content(f"<table><tbody><tr>{cells}</tr></tbody></table>", field="choice_a")

    def test_rich_validation_uses_visible_text_not_raw_html(self):
        rich = payload(
            content_format=RICH_HTML_V1,
            question_text="<p>Record <strong>the</strong> entry</p>",
            choice_a="<p>Debit Cash</p>",
            choice_b="<p>Credit Revenue</p>",
            choice_c="<p>Debit Expense</p>",
            choice_d="<p>Credit Payable</p>",
        )
        cleaned = QuestionPayloadService.validate(rich)
        self.assertEqual(cleaned["content_format"], RICH_HTML_V1)
        self.assertEqual(cleaned["question_text"], "<p>Record <strong>the</strong> entry</p>")
        with self.assertRaises(ValidationError):
            QuestionPayloadService.validate(payload(
                content_format=RICH_HTML_V1,
                question_text="<p>Record</p>",
                choice_a="<p>Same</p>", choice_b="<p><strong>Same</strong></p>",
                choice_c="<p>Third</p>", choice_d="<p>Fourth</p>",
            ))

    def test_neutral_rich_markup_is_plain_identity_equivalent(self):
        plain = payload(question_text="Record\nentry")
        rich = payload(
            content_format=RICH_HTML_V1,
            question_text="<h3>Record</h3><p>entry</p>",
            choice_a="<p>Debit Cash</p>", choice_b="<p>Credit Revenue</p>",
            choice_c="<p>Debit Expense</p>", choice_d="<p>Credit Payable</p>",
        )
        self.assertEqual(VERSION, "course-question-v4")
        self.assertEqual(standalone_identity(plain), standalone_identity(rich))

    def test_table_and_scientific_structure_remain_identity_significant(self):
        base = payload(content_format=RICH_HTML_V1,
            question_text="<p>x<sup>2</sup></p>", choice_a="<p>A</p>", choice_b="<p>B</p>",
            choice_c="<p>C</p>", choice_d="<p>D</p>")
        flattened = {**base, "question_text": "<p>x2</p>"}
        table = {**base, "question_text": "<table><tbody><tr><td>x</td><td>2</td></tr></tbody></table>"}
        self.assertNotEqual(standalone_identity(base), standalone_identity(flattened))
        self.assertNotEqual(standalone_identity(base), standalone_identity(table))

    def test_aggregate_limit_is_field_aware(self):
        values, visible = canonicalize_question_fields(payload(), content_format=PLAIN_TEXT)
        self.assertEqual(values["question_text"], visible["question_text"])

    def test_source_digest_version_freezes_historical_payload(self):
        kwargs = {
            "source_id": 9, "revision": 3, "question_text": "x", "choices": ("A", "B", "C", "D"),
            "correct_answer": "B", "difficulty": "EASY", "content_format": PLAIN_TEXT,
        }
        legacy = generation_question_source_digest(
            **kwargs, digest_version=LEGACY_QUESTION_CONTENT_DIGEST_VERSION,
        )
        current = generation_question_source_digest(
            **kwargs, digest_version=QUESTION_CONTENT_DIGEST_VERSION,
        )
        self.assertNotEqual(legacy, current)
        self.assertEqual(legacy, generation_question_source_digest(
            **kwargs, digest_version=LEGACY_QUESTION_CONTENT_DIGEST_VERSION,
        ))

    def test_question_renderer_escapes_plain_unknown_and_noncanonical_rich_values(self):
        self.assertIn(
            "&lt;table&gt;literal&lt;/table&gt;",
            str(render_question_content("<table>literal</table>", PLAIN_TEXT)),
        )
        self.assertEqual(
            str(render_question_content("<p><strong>Safe</strong></p>", RICH_HTML_V1)),
            "<p><strong>Safe</strong></p>",
        )
        for content_format in ("UNKNOWN", RICH_HTML_V1):
            with self.subTest(content_format=content_format):
                rendered = str(render_question_content("<script>private()</script>", content_format))
                self.assertIn("&lt;script&gt;private()&lt;/script&gt;", rendered)
                self.assertNotIn("<script>", rendered)

    def test_questionnaire_uses_single_column_only_for_rich_question_snapshots(self):
        context = {
            "course_code": "ACCT", "course_title": "Accounting", "school_name": "School",
            "school_address": "", "academic_year": "2026", "term": "First",
            "exam_period": "Midterm", "exam_heading": "Examinations", "set_code": "A",
            "revision_number": 1, "printed_at": None, "paper_css_size": "Letter",
            "paper_sheet_width": "8.5in", "paper_sheet_height": "11in", "paper_options": (),
            "paper_size": "letter",
            "items": (
                {
                    "position": 1, "section_id": None, "section_title": "", "section_instructions": "",
                    "case_start": False, "case_title": "", "case_content": "", "case_format": PLAIN_TEXT,
                    "question_text": "Plain", "question_format": PLAIN_TEXT,
                    "choices": ("A", "B", "C", "D"),
                },
                {
                    "position": 2, "section_id": None, "section_title": "", "section_instructions": "",
                    "case_start": False, "case_title": "", "case_content": "", "case_format": PLAIN_TEXT,
                    "question_text": "<p>Rich</p>", "question_format": RICH_HTML_V1,
                    "choices": ("<p>A</p>", "<p>B</p>", "<p>C</p>", "<p>D</p>"),
                },
            ),
        }
        rendered = render_to_string("departmental_exams/faculty/questionnaire_print.html", context)
        self.assertEqual(rendered.count('class="choices choices-rich"'), 1)
        self.assertEqual(rendered.count('class="choices" type="A"'), 1)
        self.assertIn("<p>Rich</p>", rendered)
