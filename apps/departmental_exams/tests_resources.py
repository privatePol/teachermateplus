import re

from django.urls import reverse

from apps.core.services.settings import SystemSettingService

from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


class DepartmentalExamResourcesTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("resources-faculty")
        self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.client.force_login(self.faculty)

    def test_authorized_faculty_can_access_resources_and_answer_sheet(self):
        resources_response = self.client.get(
            reverse("departmental_exams:resources")
        )
        answer_sheet_response = self.client.get(
            reverse("departmental_exams:answer_sheet")
        )

        self.assertEqual(resources_response.status_code, 200)
        self.assertTemplateUsed(
            resources_response, "departmental_exams/faculty/resources.html"
        )
        self.assertEqual(answer_sheet_response.status_code, 200)
        self.assertTemplateUsed(
            answer_sheet_response,
            "departmental_exams/faculty/answer_sheet.html",
        )

    def test_faculty_without_builder_eligibility_is_denied(self):
        outsider = self.make_faculty("resources-outsider")
        self.client.force_login(outsider)

        for route_name in (
            "departmental_exams:resources",
            "departmental_exams:answer_sheet",
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 403)
                self.assertTemplateUsed(
                    response, "departmental_exams/faculty/error.html"
                )

    def test_navigation_includes_resources_and_marks_resource_routes_active(self):
        resources_response = self.client.get(
            reverse("departmental_exams:resources")
        )
        answer_sheet_response = self.client.get(
            reverse("departmental_exams:answer_sheet")
        )

        for response in (resources_response, answer_sheet_response):
            nodes = [
                node
                for group in response.context["portal_menu"]
                for node in group["items"]
            ]
            codes = [node["item"].code for node in nodes]
            self.assertIn("DE_EXAM_FACULTY_CONTRIBUTIONS", codes)
            self.assertIn("DE_EXAM_FACULTY_RESOURCES", codes)
            resources_node = next(
                node
                for node in nodes
                if node["item"].code == "DE_EXAM_FACULTY_RESOURCES"
            )
            self.assertEqual(resources_node["item"].label, "Resources")
            self.assertTrue(resources_node["is_active"])
            self.assertContains(
                response, 'data-menu-code="DE_EXAM_FACULTY_RESOURCES"'
            )

    def test_resources_page_lists_the_built_in_answer_sheet(self):
        response = self.client.get(reverse("departmental_exams:resources"))

        self.assertContains(response, "Departmental Exam Builder Resources")
        self.assertContains(response, "NCBA 75-Item Answer Sheet")
        self.assertContains(
            response,
            f'href="{reverse("departmental_exams:answer_sheet")}"',
            html=False,
        )
        self.assertContains(response, "Open / View")
        self.assertContains(response, "Letter, A4, or Legal paper options")

    def test_answer_sheet_renders_required_fields_columns_items_and_print_control(self):
        response = self.client.get(reverse("departmental_exams:answer_sheet"))
        content = response.content.decode()
        sheet_content = content.split(
            '<article class="answer-sheet-page"', 1
        )[1].split("</article>", 1)[0]

        for required_text in (
            "NATIONAL COLLEGE OF BUSINESS AND ARTS",
            "Cubao",
            "Fairview",
            "Taytay",
            "Prelim",
            "Midterm",
            "Final",
            "BSA",
            "BSBA",
            "HM",
            "IS/CS",
            "EDUC",
            "Stud Number:",
            "Date:",
            "Student Name:",
            "Course/Subject:",
            "REMINDERS:",
            "EDUCATING GLOBALLY",
            "COMPETITIVE FILIPINOS",
            "Set:",
            "Revision:",
            "Pair Code:",
            "Print Answer Sheet",
        ):
            with self.subTest(required_text=required_text):
                self.assertContains(response, required_text)

        for removed_label in (
            "Campus:",
            "Period:",
            "Program:",
            "Student Number:",
            "Name of Student:",
            "Course:",
            "Year:",
        ):
            with self.subTest(removed_label=removed_label):
                self.assertNotIn(removed_label, sheet_content)

        self.assertEqual(content.count('class="answer-column"'), 3)
        self.assertEqual(content.count('class="answer-item"'), 75)
        self.assertEqual(content.count('class="answer-number"'), 75)
        self.assertEqual(content.count('class="answer-bubble"'), 300)
        self.assertEqual(
            [
                int(number)
                for number in re.findall(
                    r'<span class="answer-number">(\d+)</span>', content
                )
            ],
            list(range(1, 76)),
        )
        self.assertEqual(
            tuple(tuple(column) for column in response.context["answer_columns"]),
            (
                tuple(range(1, 26)),
                tuple(range(26, 51)),
                tuple(range(51, 76)),
            ),
        )
        self.assertEqual(
            re.findall(r'data-option="([A-Z])"', content),
            list("ABCD") * 3,
        )
        self.assertEqual(
            set(re.findall(r'option ([A-Z])"', content)),
            set("ABCD"),
        )
        self.assertContains(response, "logos/ncba-logo.png")
        self.assertContains(response, 'class="answer-sheet-divider"', html=False)
        self.assertContains(response, 'class="answer-watermark"', html=False)
        self.assertEqual(
            sheet_content.count("<span>NCBA</span>"),
            75,
        )
        self.assertIn("width: 0.19in", content)
        self.assertIn("@page", content)
        self.assertIn("size: Letter portrait", content)
        self.assertIn("window.print()", content)

    def test_answer_sheet_supports_only_allowlisted_paper_sizes(self):
        route = reverse("departmental_exams:answer_sheet")
        paper_sizes = (
            ("letter", "Letter", "8.5in", "11in"),
            ("a4", "A4", "210mm", "297mm"),
            ("legal", "Legal", "8.5in", "14in"),
        )

        for paper_value, css_size, sheet_width, sheet_height in paper_sizes:
            with self.subTest(paper_value=paper_value):
                response = self.client.get(route, {"paper": paper_value})
                content = response.content.decode()

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["paper_size"], paper_value)
                self.assertEqual(response.context["paper_css_size"], css_size)
                self.assertEqual(response.context["paper_sheet_width"], sheet_width)
                self.assertEqual(response.context["paper_sheet_height"], sheet_height)
                self.assertIn(f"size: {css_size} portrait", content)
                self.assertContains(
                    response,
                    f'data-paper-size="{paper_value}"',
                    html=False,
                )

        option_values = tuple(
            option["value"] for option in response.context["paper_options"]
        )
        self.assertEqual(option_values, ("letter", "a4", "legal"))

    def test_answer_sheet_missing_or_invalid_paper_falls_back_to_letter(self):
        route = reverse("departmental_exams:answer_sheet")
        invalid_paper = "tabloid};body{display:none"

        for query in ({}, {"paper": invalid_paper}):
            with self.subTest(query=query):
                response = self.client.get(route, query)
                content = response.content.decode()

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["paper_size"], "letter")
                self.assertEqual(response.context["paper_css_size"], "Letter")
                self.assertIn("size: Letter portrait", content)
                self.assertContains(
                    response,
                    'data-paper-size="letter"',
                    html=False,
                )
                self.assertNotIn(invalid_paper, content)

    def test_print_css_hides_faculty_banner_and_shared_notification_chrome(self):
        response = self.client.get(reverse("departmental_exams:answer_sheet"))
        content = response.content.decode()
        print_css = content.rsplit("@media print {", 1)[1].split("</style>", 1)[0]
        hide_rule = print_css.split("display: none !important;", 1)[0].rsplit("}", 1)[-1]

        for selector in (
            ".faculty-identity-warning",
            "#inline-message-container",
            "#system-error-modal",
            "body > .modal-backdrop",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, hide_rule)
        self.assertIn("display: none !important;", print_css)

    def test_disabled_feature_hides_resources_and_denies_direct_routes(self):
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )

        for route_name in (
            "departmental_exams:resources",
            "departmental_exams:answer_sheet",
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 403)
                menu_codes = [
                    node["item"].code
                    for group in response.context["portal_menu"]
                    for node in group["items"]
                ]
                self.assertNotIn("DE_EXAM_FACULTY_RESOURCES", menu_codes)
