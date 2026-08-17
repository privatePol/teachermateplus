import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ScientificNotationStaticContractTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_dir = Path(settings.BASE_DIR)
        cls.script_path = (
            cls.base_dir / "static" / "js" / "departmental_exam_scientific_notation.js"
        )
        cls.script = cls.script_path.read_text(encoding="utf-8")
        cls.vendor_dir = cls.base_dir / "static" / "vendor" / "katex" / "0.18.4"

    def test_official_version_pinned_assets_fonts_and_license_are_self_hosted(self):
        metadata = json.loads(
            (self.vendor_dir / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["name"], "katex")
        self.assertEqual(metadata["version"], "0.18.4")
        self.assertEqual(metadata["license"], "MIT")
        for relative_path in (
            "katex.min.css",
            "katex.min.js",
            "contrib/auto-render.min.js",
            "contrib/mhchem.min.js",
            "LICENSE",
        ):
            asset = self.vendor_dir / relative_path
            self.assertTrue(asset.is_file(), relative_path)
            self.assertGreater(asset.stat().st_size, 0, relative_path)
        self.assertEqual(len(list((self.vendor_dir / "fonts").glob("*.woff2"))), 20)
        self.assertNotIn("cdn", self.script.lower())

    def test_renderer_is_restricted_bounded_and_fail_safe(self):
        required_options = (
            'left: "\\\\(", right: "\\\\)", display: false',
            'left: "\\\\[", right: "\\\\]", display: true',
            "trust: false",
            "throwOnError: false",
            'strict: "error"',
            "maxSize: 10",
            "maxExpand: 1000",
            'output: "htmlAndMathml"',
        )
        for option in required_options:
            self.assertIn(option, self.script)
        self.assertEqual(self.script.count("left:"), 2)
        self.assertNotIn('left: "$"', self.script)
        self.assertIn("try {", self.script)
        self.assertIn("catch (_error)", self.script)

    def test_editor_uses_text_content_and_has_no_raw_html_input_path(self):
        self.assertIn("preview.textContent = value", self.script)
        self.assertIn('querySelectorAll("[data-scientific-content]")', self.script)
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(forbidden, self.script)
        self.assertIn("trust: false", self.script)

    def test_toolbar_covers_phase_one_math_chemistry_and_matrix_constructs(self):
        required_fragments = (
            "Insert Equation",
            "Chemical Formula",
            "\\\\frac",
            "\\\\sqrt",
            "^{",
            "_{",
            "\\\\pm",
            "\\\\pi",
            "\\\\theta",
            "\\\\sum",
            "\\\\int",
            "\\\\infty",
            "\\\\le",
            "\\\\ge",
            "\\\\ne",
            "Greek symbols",
            "\\\\begin{bmatrix}",
            "\\\\ce{",
        )
        for fragment in required_fragments:
            self.assertIn(fragment, self.script)

    def test_print_waits_for_initial_render_and_fonts(self):
        self.assertLess(
            self.script.index("renderAll(document)"),
            self.script.index("const initialReady = fontsReady()"),
        )
        self.assertIn("document.fonts.ready", self.script)
        self.assertIn("initialReady.then", self.script)
        self.assertIn("window.print()", self.script)

    def test_all_authorized_rendering_templates_load_shared_assets(self):
        templates = (
            "faculty/question_form.html",
            "faculty/contribution_workspace.html",
            "faculty/question_delete.html",
            "admin/blueprint_review.html",
            "admin/generated_revision_detail.html",
            "admin/generation_selection_audit.html",
            "admin/generation_selection_audit_print.html",
            "faculty/questionnaire_print.html",
        )
        template_root = self.base_dir / "templates" / "departmental_exams"
        for relative_path in templates:
            with self.subTest(template=relative_path):
                source = (template_root / relative_path).read_text(encoding="utf-8")
                self.assertIn("_scientific_notation_assets.html", source)
                self.assertIn("data-scientific-content", source)
                self.assertNotIn("|safe", source)
                for field_name in (
                    "question_text",
                    "choice_a",
                    "choice_b",
                    "choice_c",
                    "choice_d",
                    "question",
                ):
                    self.assertNotIn(f"{field_name}|linebreaksbr", source)

    def test_shared_assets_load_katex_mhchem_auto_render_and_application_script(self):
        source = (
            self.base_dir
            / "templates"
            / "departmental_exams"
            / "_scientific_notation_assets.html"
        ).read_text(encoding="utf-8")
        expected_order = (
            "katex.min.js",
            "mhchem.min.js",
            "auto-render.min.js",
            "departmental_exam_scientific_notation.js",
        )
        positions = [source.index(asset) for asset in expected_order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("http://", source)
        self.assertNotIn("https://", source)
