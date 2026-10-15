"""Synthetic output regressions; optional browser checks never access a server.

Set TMP_QUESTIONNAIRE_BROWSER to a Chrome/Edge executable and install Playwright
in the test environment to run the layout/PDF regression as well.
"""
import copy
import io
import json
import mimetypes
import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import patch

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .question_content import PLAIN_TEXT, RICH_HTML_V1, canonicalize_question_content, render_question_content
from .questionnaire_presentation import _compact_verified_html, render_questionnaire_content


STYLESHEET = "css/departmental_exam_questionnaire_presentation.css"
MIXED_INLINE_CONTENT = '<strong>First</strong> <em>Second</em><p>Third</p>'


def questionnaire_context(set_code="A"):
    rich_sources = (
        "<p>Single paragraph</p>",
        "<p>First paragraph</p><p>Second paragraph</p>",
        '<p class="tmp-preserve">Line 1<br><br><br>Line 2</p><p class="tmp-preserve"></p>',
        '<p class="MsoNormal"><span style="font-weight:bold">Word-pasted paragraph</span></p>',
        '<p>Table introduction</p><table><tbody><tr><td><p class="tmp-preserve">Cell</p></td></tr></tbody></table>',
        r"<p>Formula \(x^{2}\) and \[\frac{a}{b}\]</p>",
        MIXED_INLINE_CONTENT,
    )
    items = [{
        "position": 1, "question_text": "Plain line 1\nPlain line 2", "question_format": PLAIN_TEXT,
        "choices": ("Plain A", "Plain B", "Plain C", "Plain D"),
    }]
    for position, source in enumerate(rich_sources, 2):
        items.append({
            "position": position,
            "question_text": canonicalize_question_content(source, field="question_text").html,
            "question_format": RICH_HTML_V1,
            "choices": ("<p>Choice A</p>", "<p>Choice B</p>", "<p>Choice C</p>", "<p>Choice D</p>"),
        })
    for item in items:
        item.update(section_id=None, section_title="", section_instructions="", case_start=False,
                    case_title="", case_content="", case_format=PLAIN_TEXT)
    items[-1]["choices"] = (MIXED_INLINE_CONTENT,) * 4
    items[5].update(case_start=True, case_title="Synthetic caselet",
                    case_content="<p>Case paragraph</p>", case_format=RICH_HTML_V1)
    return {
        "course_code": "SYNTH", "course_title": "Synthetic questionnaire", "school_name": "Test school",
        "academic_year": "2026", "term": "First", "exam_period": "Final", "exam_heading": "Examinations",
        "set_code": set_code, "revision_number": 7, "printed_at": None, "paper_css_size": "Letter",
        "paper_sheet_width": "8.5in", "paper_sheet_height": "11in", "paper_options": (),
        "paper_size": "letter", "items": items, "MEDIA_URL": settings.MEDIA_URL,
    }


class QuestionnaireSpacingTests(SimpleTestCase):
    def test_repeated_set_rendering_preserves_content_and_context(self):
        for set_code in ("A", "B"):
            context = questionnaire_context(set_code)
            original = copy.deepcopy(context)
            first = render_to_string("departmental_exams/faculty/questionnaire_print.html", context)
            self.assertEqual(first, render_to_string("departmental_exams/faculty/questionnaire_print.html", context))
            self.assertEqual(context, original)
            self.assertIn(STYLESHEET, first)
            self.assertIn(MIXED_INLINE_CONTENT, first)
            previous = -1
            for item in context["items"]:
                text = str(render_questionnaire_content(item["question_text"], item["question_format"]))
                position = first.index(text)
                self.assertGreater(position, previous)
                previous = position
                for choice in item["choices"]:
                    self.assertIn(str(render_questionnaire_content(choice, item["question_format"], field="choice_a")), first)
            self.assertIn('class="tmp-case-content" data-scientific-content><p>Case paragraph</p>', first)

    def test_admin_preview_uses_same_spacing_without_rewriting_snapshots(self):
        rows = []
        for item in questionnaire_context()["items"]:
            rows.append(SimpleNamespace(
                position=item["position"], question_text_snapshot=item["question_text"],
                question_content_format_snapshot=item["question_format"], choices_snapshot=item["choices"],
                section_id_snapshot=None, scenario_id_snapshot=None, correct_answer_snapshot="D",
            ))
        before = copy.deepcopy([vars(row) for row in rows])
        context = {
            "request": SimpleNamespace(GET={}, user=SimpleNamespace(full_name="Test reviewer", username="reviewer")),
            "cycle_course": SimpleNamespace(id=1, course=SimpleNamespace(code="SYNTH", title="Synthetic")),
            "revision": SimpleNamespace(id=7, revision_number=7, status="GENERATED"),
            "generated_sets": [SimpleNamespace(set_code=code, ordered_items=rows, item_count=len(rows)) for code in ("A", "B")],
        }
        first = render_to_string("departmental_exams/admin/generated_revision_detail.html", context)
        self.assertIn(STYLESHEET, first)
        self.assertIn(MIXED_INLINE_CONTENT, first)
        self.assertIn('class="tmp-case-content tmp-mcq-output fw-semibold mb-2"', first)
        self.assertEqual(first, render_to_string("departmental_exams/admin/generated_revision_detail.html", context))
        self.assertEqual(before, [vars(row) for row in rows])

    def test_browser_spacing_and_pdf_preserve_content(self):
        executable = os.environ.get("TMP_QUESTIONNAIRE_BROWSER")
        if not executable:
            self.skipTest("Set TMP_QUESTIONNAIRE_BROWSER for isolated layout/PDF validation.")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is not installed in this test environment.")
        documents = {"/fixture": ""}
        static_root = Path(settings.BASE_DIR) / "static"

        def respond(route):
            url = urlparse(route.request.url)
            if url.hostname != "questionnaire.test":
                route.abort()
            elif url.path == "/fixture":
                route.fulfill(body=documents["/fixture"], content_type="text/html")
            elif url.path.startswith("/static/"):
                if url.path == "/static/" + STYLESHEET and documents.get("/presentation-css") is not None:
                    route.fulfill(body=documents["/presentation-css"], content_type="text/css")
                    return
                asset = (static_root / url.path[len("/static/"):]).resolve()
                if asset.is_relative_to(static_root.resolve()) and asset.is_file():
                    route.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset.name)[0] or "application/octet-stream")
                else:
                    route.fulfill(status=404, body="")
            else:
                route.fulfill(status=404, body="")

        measurements = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable, headless=True)
            page = browser.new_page()
            page.route("**/*", respond)
            for set_code in ("A", "B"):
                documents["/fixture"] = render_to_string("departmental_exams/faculty/questionnaire_print.html", questionnaire_context(set_code))
                for media in ("screen", "print"):
                    page.emulate_media(media=media)
                    page.goto("http://questionnaire.test/fixture")
                    page.wait_for_function("window.TMPScientificNotation && !document.querySelector('[data-scientific-print]').disabled")
                    before_content = page.locator(".questions").inner_html()
                    page.evaluate("document.querySelector('link[href*=questionnaire_presentation]').sheet.disabled = true")
                    before = page.evaluate("""() => {
                      const questions = document.querySelectorAll('.question');
                      const top = e => e.getBoundingClientRect().top;
                      const pitch = q => top(q.querySelectorAll('.choices li')[1]) - top(q.querySelector('.choices li'));
                      return {plain: pitch(questions[0]), rich: pitch(questions[1]),
                        tableSize: [document.querySelector('table').offsetWidth, document.querySelector('table').offsetHeight],
                        pageBreak: getComputedStyle(questions[1]).breakInside,
                        caseMargin: getComputedStyle(document.querySelector('.questionnaire-case p')).marginTop,
                        cellMargin: getComputedStyle(document.querySelector('td p')).marginTop};
                    }""")
                    page.evaluate("document.querySelector('link[href*=questionnaire_presentation]').sheet.disabled = false")
                    after = page.evaluate("""() => {
                      const questions = document.querySelectorAll('.question');
                      const top = e => e.getBoundingClientRect().top;
                      const pitch = q => top(q.querySelectorAll('.choices li')[1]) - top(q.querySelector('.choices li'));
                      return {plain: pitch(questions[0]), rich: pitch(questions[1]),
                        tableSize: [document.querySelector('table').offsetWidth, document.querySelector('table').offsetHeight],
                        pageBreak: getComputedStyle(questions[1]).breakInside,
                        aligned: top(questions[1].querySelector('.question-line p')) - top(questions[1].querySelector('.question-line > span')),
                        paragraphGap: parseFloat(getComputedStyle(questions[2].querySelector('.question-line p + p')).marginTop),
                        caseMargin: getComputedStyle(document.querySelector('.questionnaire-case p')).marginTop,
                        cellMargin: getComputedStyle(document.querySelector('td p')).marginTop,
                        preserved: getComputedStyle(questions[3].querySelector('.tmp-preserve')).whiteSpace,
                        blankHeight: questions[3].querySelector('.question-line .tmp-preserve:empty')?.getBoundingClientRect().height || 0,
                        katex: document.querySelectorAll('.katex').length};
                    }""")
                    self.assertEqual(before_content, page.locator(".questions").inner_html())
                    self.assertAlmostEqual(after["plain"], before["plain"], places=2)
                    self.assertLess(after["rich"], before["rich"] * .7)
                    self.assertAlmostEqual(after["aligned"], 0, places=2)
                    self.assertGreater(after["paragraphGap"], 0)
                    self.assertLess(after["paragraphGap"], 5)
                    self.assertEqual(after["caseMargin"], before["caseMargin"])
                    self.assertEqual(after["cellMargin"], before["cellMargin"])
                    self.assertEqual(after["tableSize"], before["tableSize"])
                    self.assertEqual(after["pageBreak"], before["pageBreak"])
                    self.assertEqual(after["preserved"], "break-spaces")
                    self.assertEqual(after["blankHeight"], 0)
                    self.assertGreater(after["katex"], 0)
                    # Exact mixed inline/block text, in both sets and media.
                    mixed = page.locator('.question').last
                    for content in (mixed.locator('.question-line .tmp-mcq-output'),
                                    *mixed.locator('.choices .tmp-mcq-output').all()):
                        self.assertEqual(content.inner_html(), MIXED_INLINE_CONTENT)
                        self.assertEqual(content.inner_text().splitlines()[0], "First Second")
                    measurements.append({"set": set_code, "media": media, "before": before, "after": after})
            # Measured PDF comparison uses 50 ordinary rich MCQs, not official data.
            context = questionnaire_context()
            context["items"] = [dict(context["items"][1], position=n, case_start=False) for n in range(1, 51)]
            documents["/fixture"] = render_to_string("departmental_exams/faculty/questionnaire_print.html", context)
            page.goto("http://questionnaire.test/fixture")
            page.wait_for_function("!document.querySelector('[data-scientific-print]').disabled")
            page.evaluate("document.querySelector('link[href*=questionnaire_presentation]').sheet.disabled = true")
            before_pdf = page.pdf(prefer_css_page_size=True)
            page.evaluate("document.querySelector('link[href*=questionnaire_presentation]').sheet.disabled = false")
            after_pdf = page.pdf(prefer_css_page_size=True)
            try:
                from pypdf import PdfReader
            except ImportError:
                pdf_counts = "PDFs generated; pypdf unavailable for page counts."
            else:
                before_reader, after_reader = PdfReader(io.BytesIO(before_pdf)), PdfReader(io.BytesIO(after_pdf))
                pdf_counts = {"before": len(before_reader.pages), "after": len(after_reader.pages)}
                self.assertLessEqual(pdf_counts["after"], pdf_counts["before"])
                for reader in (before_reader, after_reader):
                    text = "".join(p.extract_text() or "" for p in reader.pages)
                    self.assertEqual(text.count("Single paragraph"), 50)
                    for letter in "ABCD":
                        self.assertEqual(text.count("Choice " + letter), 50)
            # Gate 2B: compare the previous CSS-only correction and verified
            # renderer with the new display projection, using the same source.
            old_css = (static_root / STYLESHEET).read_text(encoding="utf-8").split("/* The display adapter", 1)[0]
            blank = '<p class="tmp-preserve"></p>'

            def spaced(context, fifty=False):
                context = copy.deepcopy(context)
                if fifty:
                    context["items"] = []
                    for number in range(1, 51):
                        item = dict(questionnaire_context()["items"][1], position=number,
                                    question_text=f'<p class="tmp-preserve">Stem {number}</p>')
                        if number % 10 == 0:
                            item["question_text"] += r'<p class="tmp-preserve">\[\begin{bmatrix}a & b \\ c & d\end{bmatrix}\]</p>'
                            item["question_text"] += '<table><tbody><tr><td>Cell A</td><td>Cell B</td></tr></tbody></table>'
                        context["items"].append(item)
                for item in context["items"]:
                    if item["question_format"] != RICH_HTML_V1:
                        continue
                    if not fifty and item["position"] == 3:
                        item["question_text"] = item["question_text"].replace('</p><p>', '</p>' + blank * 3 + '<p>')
                    item["question_text"] = canonicalize_question_content(blank * 3 + item["question_text"] + blank * 3, field="question_text").html
                    choices = []
                    for letter in "ABCD":
                        tail = "" if fifty else f'<br><br><br><strong></strong><br>Detail {letter}'
                        choice = blank * 2 + f'<p class="tmp-preserve"><br><br>Choice {letter}{tail}<br><br></p>' + blank * 2
                        choices.append(canonicalize_question_content(choice, field="choice_a").html)
                    item["choices"] = tuple(choices)
                return context

            def load_projection(context, before=False):
                if before:
                    with patch("apps.departmental_exams.templatetags.departmental_exam_content.render_questionnaire_content", side_effect=render_question_content):
                        documents["/fixture"] = render_to_string("departmental_exams/faculty/questionnaire_print.html", context)
                else:
                    documents["/fixture"] = render_to_string("departmental_exams/faculty/questionnaire_print.html", context)
                documents["/presentation-css"] = old_css if before else None
                page.goto("http://questionnaire.test/fixture")
                page.wait_for_function("window.TMPScientificNotation && !document.querySelector('[data-scientific-print]').disabled")

            smart_measurements = []
            metric_script = """() => {
              const questions = document.querySelectorAll('.question');
              const q = questions[1];
              const content = q.querySelector('.question-line [data-scientific-content]');
              const rangeFor = node => {const r = document.createRange(); r.selectNodeContents(node); return r.getBoundingClientRect();};
              const first = [...content.querySelectorAll('p')].find(p => p.textContent.replace(/\\u00a0/g,' ').trim());
              const lis = q.querySelectorAll('.choices li');
              const rect = rangeFor(first);
              const third = questions[2].querySelector('.question-line [data-scientific-content]');
              const thirdFirst = [...third.querySelectorAll('p')].find(p => p.textContent.replace(/\\u00a0/g,' ').trim());
              return {stemOffset: rect.top - rangeFor(q.querySelector('.question-line > span')).top,
                thirdStemOffset: rangeFor(thirdFirst).top - rangeFor(questions[2].querySelector('.question-line > span')).top,
                internalSpacers: [...third.querySelectorAll(':scope > p:empty')].map(p=>p.getBoundingClientRect().height),
                lineHeight: parseFloat(getComputedStyle(third).lineHeight),
                choicePitch: lis[1].getBoundingClientRect().top - lis[0].getBoundingClientRect().top,
                blankHeight: [...content.querySelectorAll(':scope > p')].filter(p => !p.textContent.replace(/\\u00a0/g,' ').trim()).reduce((sum,p)=>sum+p.getBoundingClientRect().height,0),
                tableHtml: document.querySelector('table').outerHTML,
                tableSize: [document.querySelector('table').offsetWidth, document.querySelector('table').offsetHeight],
                caseHtml: document.querySelector('.questionnaire-case').innerHTML,
                mathHtml: document.querySelector('.katex-display').innerHTML,
                mathSize: [document.querySelector('.katex-display').offsetWidth,document.querySelector('.katex-display').offsetHeight],
                clipped: rect.right > content.getBoundingClientRect().right + 1};
            }"""
            for set_code in ("A", "B"):
                context = spaced(questionnaire_context(set_code))
                original = copy.deepcopy(context)
                for media in ("screen", "print"):
                    page.emulate_media(media=media)
                    load_projection(context, before=True)
                    before = page.evaluate(metric_script)
                    load_projection(context)
                    after = page.evaluate(metric_script)
                    self.assertGreater(before["stemOffset"], 0)
                    self.assertAlmostEqual(after["stemOffset"], 0, places=2)
                    self.assertAlmostEqual(after["thirdStemOffset"], 0, places=2)
                    self.assertEqual(after["blankHeight"], 0)
                    self.assertEqual(len(after["internalSpacers"]), 1)
                    self.assertLessEqual(after["internalSpacers"][0], after["lineHeight"] + .1)
                    self.assertLess(after["choicePitch"], before["choicePitch"])
                    for key in ("tableHtml", "tableSize", "caseHtml", "mathHtml", "mathSize"):
                        self.assertEqual(after[key], before[key], key)
                    self.assertFalse(after["clipped"])
                    self.assertEqual(context, original)
                    # Reloading the same frozen source cannot accumulate edits.
                    normalized_html = page.locator('.questions').inner_html()
                    load_projection(context)
                    self.assertEqual(normalized_html, page.locator('.questions').inner_html())
                    smart_measurements.append({"set": set_code, "media": media,
                        "before": {k: before[k] for k in ("stemOffset","thirdStemOffset","choicePitch","blankHeight","internalSpacers")},
                        "after": {k: after[k] for k in ("stemOffset","thirdStemOffset","choicePitch","blankHeight","internalSpacers")}})

            smart_pdfs = []
            try:
                from pypdf import PdfReader
            except ImportError:
                pdf_reader_available = False
            else:
                pdf_reader_available = True
            for paper, width, height in (("Letter", "8.5in", "11in"), ("A4", "210mm", "297mm"), ("Legal", "8.5in", "14in")):
                context = spaced(questionnaire_context(), fifty=True)
                context.update(paper_css_size=paper, paper_sheet_width=width, paper_sheet_height=height)
                counts = {}
                for before in (True, False):
                    load_projection(context, before=before)
                    pdf = page.pdf(prefer_css_page_size=True)
                    if pdf_reader_available:
                        reader = PdfReader(io.BytesIO(pdf))
                        counts["before" if before else "after"] = len(reader.pages)
                        text = "".join(p.extract_text() or "" for p in reader.pages)
                        self.assertEqual(re.findall(r'\bStem (\d+)\b', text), [str(number) for number in range(1, 51)])
                        for letter in "ABCD":
                            self.assertEqual(text.count("Choice " + letter), 50)
                if pdf_reader_available:
                    self.assertLess(counts["after"], counts["before"])
                smart_pdfs.append({"paper":paper,"pages":counts,"page_counts_available":pdf_reader_available})
            print("Synthetic browser spacing: " + json.dumps({"browser": browser.version, "measurements": measurements,
                "pdf_pages": pdf_counts, "smart_measurements":smart_measurements,"smart_pdfs":smart_pdfs}))
            browser.close()


class QuestionnaireDisplayNormalizationTests(SimpleTestCase):
    def test_mixed_root_inline_separator_is_exact_for_every_mcq_field(self):
        from .templatetags.departmental_exam_content import questionnaire_question_content, questionnaire_choice_content

        for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d"):
            with self.subTest(field=field):
                canonical = canonicalize_question_content(MIXED_INLINE_CONTENT, field=field)
                self.assertEqual(canonical.html, MIXED_INLINE_CONTENT)
                projected = str(render_questionnaire_content(canonical.html, RICH_HTML_V1, field=field))
                self.assertEqual(projected, MIXED_INLINE_CONTENT)
                self.assertEqual(canonicalize_question_content(projected, field=field).visible_text, "First Second Third")
                self.assertEqual(projected, str(render_questionnaire_content(projected, RICH_HTML_V1, field=field)))
                self.assertEqual(canonical.html, MIXED_INLINE_CONTENT)
        self.assertEqual(str(questionnaire_question_content(MIXED_INLINE_CONTENT, RICH_HTML_V1)), MIXED_INLINE_CONTENT)
        self.assertEqual(str(questionnaire_choice_content(MIXED_INLINE_CONTENT, RICH_HTML_V1)), MIXED_INLINE_CONTENT)

    def test_nested_inline_and_entity_separators_preserve_exact_text(self):
        for separator in (" ", "\n", "&nbsp;", "&#160;"):
            source = '<strong><em>First</em></strong>' + separator + '<u>Second<sup>2</sup></u><p>Third</p>'
            canonical = self.rich(source)
            self.assertEqual(str(render_questionnaire_content(canonical, RICH_HTML_V1)), canonical)
            self.assertEqual(canonicalize_question_content(canonical, field="question_text").visible_text, "First Second2 Third")
        source = '<p class="tmp-preserve"><strong>First</strong> <em><u>Second</u></em></p><p>Third</p>'
        self.assertEqual(self.display(source), source)

    def test_mixed_root_text_and_blocks_keep_inline_word_separators(self):
        for source, visible in (
            ('<p>Before</p><strong>First</strong> <em>Second</em><p>After</p>', 'Before First Second After'),
            ('First <em>Second</em> <strong>Third</strong><p>Fourth</p>', 'First Second Third Fourth'),
            ('<p>First</p><strong>Second</strong> Third <em>Fourth</em>', 'First Second Third Fourth'),
        ):
            canonical = self.rich(source)
            projected = str(render_questionnaire_content(canonical, RICH_HTML_V1))
            self.assertEqual(projected, canonical)
            self.assertEqual(canonicalize_question_content(projected, field="question_text").visible_text, visible)

    def test_inline_word_separators_survive_surrounding_spacer_compaction(self):
        blank = '<p class="tmp-preserve"></p>'
        source = blank * 3 + MIXED_INLINE_CONTENT + blank * 4 + '<p>Fourth</p>' + blank * 3
        expected = MIXED_INLINE_CONTENT + blank + '<p>Fourth</p>'
        for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d"):
            canonical = self.rich(source, field=field)
            projected = str(render_questionnaire_content(canonical, RICH_HTML_V1, field=field))
            self.assertEqual(projected, expected)
            self.assertEqual(canonicalize_question_content(projected, field=field).visible_text, 'First Second Third Fourth')
            self.assertEqual(str(render_questionnaire_content(projected, RICH_HTML_V1, field=field)), expected)
        # Block indentation still contributes no extra blank line.
        self.assertEqual(self.display('<p>First</p>\n\n <p>Second</p>'), '<p>First</p><p>Second</p>')

    def test_links_and_spans_remain_opaque_or_escaped_without_trust_promotion(self):
        for source in (
            '<a href="/local"><strong>First</strong></a> <span><em>Second</em></span><p>Third</p>',
            '<span><strong>First</strong> <em>Second</em></span> <span>Third</span><p>Fourth</p>',
            '<p><a href="/local">First</a> <span>Second</span></p><p>Third</p>',
        ):
            self.assertEqual(_compact_verified_html(source), source)
            for field in ("question_text", "choice_a", "choice_b", "choice_c", "choice_d"):
                self.assertEqual(str(render_questionnaire_content(source, RICH_HTML_V1, field=field)),
                                 str(render_question_content(source, RICH_HTML_V1, field=field)))
            self.assertIn('&lt;', str(render_questionnaire_content(source, RICH_HTML_V1)))

    def test_mixed_historical_content_safety_is_unchanged(self):
        for source in (MIXED_INLINE_CONTENT + '<p></p>',
                       '<strong onclick="bad()">First</strong> <em>Second</em><p>Third</p>',
                       '<script>bad()</script>' + MIXED_INLINE_CONTENT):
            self.assertEqual(str(render_questionnaire_content(source, RICH_HTML_V1)), str(render_question_content(source, RICH_HTML_V1)))
        self.assertEqual(str(render_questionnaire_content(MIXED_INLINE_CONTENT, PLAIN_TEXT)),
                         str(render_question_content(MIXED_INLINE_CONTENT, PLAIN_TEXT)))

    def rich(self, raw, field="question_text"):
        return canonicalize_question_content(raw, field=field).html

    def display(self, raw, field="question_text"):
        return str(render_questionnaire_content(self.rich(raw, field), RICH_HTML_V1, field=field))

    def test_enter_at_start_middle_end_leaves_one_internal_spacer(self):
        empty = '<p class="tmp-preserve"></p>'
        first, second = '<p class="tmp-preserve">First</p>', '<p class="tmp-preserve">Second</p>'
        self.assertEqual(self.display(empty * 3 + first + empty * 4 + second + empty * 3), first + empty + second)

    def test_root_breaks_and_empty_headings_project_idempotently(self):
        value = self.rich('<br><p>A</p><h3></h3><h4></h4><p>B</p><br>')
        expected = '<p>A</p><p class="tmp-preserve"></p><p>B</p>'
        self.assertEqual(str(render_questionnaire_content(value, RICH_HTML_V1)), expected)
        self.assertEqual(str(render_questionnaire_content(expected, RICH_HTML_V1)), expected)

    def test_each_choice_trims_empty_nbsp_and_nested_formatting_blocks(self):
        blanks = '<p class="tmp-preserve">&nbsp;</p><p class="tmp-preserve"><strong><em></em></strong></p><p class="tmp-preserve"><br></p>'
        for letter in "ABCD":
            meaningful = f'<p class="tmp-preserve"><strong>Choice {letter}</strong></p>'
            self.assertEqual(self.display(blanks + meaningful + blanks, field="choice_" + letter.lower()), meaningful)

    def test_shift_enter_and_empty_marks_compact_across_inline_boundaries(self):
        source = '<p class="tmp-preserve"><br><br><strong>First<br></strong><br><em><br><br>Second</em><br><br></p>'
        self.assertEqual(self.display(source), '<p class="tmp-preserve"><strong>First<br></strong><br><em>Second</em></p>')
        self.assertEqual(self.display('<p class="tmp-preserve">First<br><strong></strong><br><br>Second</p>'),
                         '<p class="tmp-preserve">First<br><br>Second</p>')

    def test_word_pasted_marked_blanks_compact_without_losing_text_formatting(self):
        source = '<p class="MsoNormal tmp-preserve">&nbsp;</p><p class="tmp-preserve"><u>Word</u> text</p><p class="MsoNormal tmp-preserve"><br></p>'
        self.assertEqual(self.display(source), '<p class="tmp-preserve"><u>Word</u> text</p>')

    def test_literal_newline_runs_compact_but_horizontal_whitespace_survives(self):
        self.assertEqual(self.display('<p class="tmp-preserve">\n\n First\n\n\n Second\n\n</p>'),
                         '<p class="tmp-preserve"> First\n\n Second</p>')
        self.assertEqual(str(render_questionnaire_content('\n\nFirst\n\n\nSecond\n\n', PLAIN_TEXT)), 'First\n\nSecond')
        self.assertEqual(str(render_questionnaire_content('First\nSecond', PLAIN_TEXT)), 'First\nSecond')
        self.assertEqual(str(render_questionnaire_content('\n&nbsp;\n', PLAIN_TEXT)), '&amp;nbsp;')

    def test_meaningful_preformatted_whitespace_is_opaque(self):
        for source in ('<p class="tmp-preserve"> A  B\n\n\n C  D</p>', '<p class="tmp-preserve">A\tB\n\n\nC\tD</p>'):
            canonical = self.rich(source)
            self.assertEqual(str(render_questionnaire_content(canonical, RICH_HTML_V1)), canonical)
        plain = 'A  B\n\n\nC  D'
        self.assertEqual(str(render_questionnaire_content(plain, PLAIN_TEXT)), plain)

    def test_table_and_matrix_nested_structure_remains_byte_identical(self):
        table = self.rich('<table><tbody><tr><td rowspan="2"><p class="tmp-preserve"></p><p class="tmp-preserve">Cell<br><br><br>next</p></td><td>B</td></tr><tr><td>C</td></tr></tbody></table><p>After table</p>')
        matrix = self.rich(r'<p class="tmp-preserve">\[\begin{bmatrix}a & b \\ c & d\end{bmatrix}\]</p>')
        empty = '<p class="tmp-preserve"></p>'
        self.assertEqual(str(render_questionnaire_content(empty + table + empty, RICH_HTML_V1)), table)
        self.assertEqual(str(render_questionnaire_content(empty + matrix + empty, RICH_HTML_V1)), matrix)

    def test_spacers_outside_math_compact_without_touching_matrix_whitespace(self):
        expression = '\\[\\begin{bmatrix}a & b\\\\\n\n\nc & d\\end{bmatrix}\\]'
        source = '<p class="tmp-preserve"><br><br>\n\n' + expression + '\n\n<br><br></p>'
        self.assertEqual(self.display(source), self.rich('<p class="tmp-preserve">' + expression + '</p>'))
        self.assertEqual(str(render_questionnaire_content('\n\n' + expression + '\n\n', PLAIN_TEXT)), str(render_questionnaire_content(expression, PLAIN_TEXT)))

    def test_fragmented_math_fails_closed_without_compacting_its_breaks(self):
        canonical = self.rich('<p class="tmp-preserve">\\[<strong>a<br><br><br>b</strong>\\]</p>')
        self.assertEqual(str(render_questionnaire_content(canonical, RICH_HTML_V1)), canonical)

    def test_image_page_break_and_preformatted_protected_subtrees_are_not_edited(self):
        # These structures are opaque to the structural projection. The public
        # renderer still escapes them if the existing sanitizer cannot verify them.
        for source in ('<p><img src="local.png"></p>', '<p style="break-before:page"><br></p>',
                       '<p class="page-break"></p>', '<pre> A\n\n\n B</pre>', '<p><code>A\n\n\nB</code></p>'):
            self.assertEqual(_compact_verified_html(source), source)
            self.assertEqual(str(render_questionnaire_content(source, RICH_HTML_V1)), str(render_question_content(source, RICH_HTML_V1)))

    def test_noncanonical_historical_unknown_and_active_html_remain_escaped(self):
        for value, content_format in (('<p>Text</p><p></p>', RICH_HTML_V1),
                                     ('<script>alert(1)</script><p>Text</p>', RICH_HTML_V1),
                                     ('<p onclick="bad()">Text</p>', RICH_HTML_V1), ('<p>Text</p>', 'UNKNOWN')):
            self.assertEqual(str(render_questionnaire_content(value, content_format)), str(render_question_content(value, content_format)))
        self.assertEqual(str(render_questionnaire_content('<table>literal</table>', PLAIN_TEXT)), '&lt;table&gt;literal&lt;/table&gt;')

    def test_projection_is_idempotent_and_does_not_change_semantic_text(self):
        empty = '<p class="tmp-preserve"><br></p>'
        for count in range(1, 6):
            value = self.rich(empty * count + '<p class="tmp-preserve">A &amp; B<br><br><br>C</p>' + empty * count + '<p class="tmp-preserve">D</p>' + empty)
            before = canonicalize_question_content(value, field='question_text').visible_text
            projected = str(render_questionnaire_content(value, RICH_HTML_V1))
            self.assertEqual(projected, _compact_verified_html(projected))
            self.assertEqual(projected, str(render_questionnaire_content(projected, RICH_HTML_V1)))
            self.assertEqual(before, canonicalize_question_content(projected, field='question_text').visible_text)
            self.assertIn('class="tmp-preserve"', projected)
