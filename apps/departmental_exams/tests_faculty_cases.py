from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission

from .blueprint_services import BlueprintMutationService, ScenarioMutationService
from .contribution_services import QuestionMutationService
from .faculty_case_services import FacultyCaseMutationService, FacultyCasePolicy
from .generation_readiness import Stage6ReadinessService
from .models import (
    ExamBlueprint,
    ExamScenario,
    ExamScenarioMember,
    ExaminationCycle,
    FacultyContribution,
    Question,
    QuestionBlueprintPlacement,
)
from .scenario_content import (
    MAX_CANONICAL_CHARACTERS,
    MAX_DEPTH,
    MAX_NODES,
    MAX_RAW_CHARACTERS,
    MAX_TABLES,
    canonicalize_scenario_content,
    render_scenario_content,
)
from .services import CourseExamConfigurationService
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


class ScenarioContentTests(SimpleTestCase):
    def test_word_semantics_tables_unicode_alignment_and_idempotence(self):
        raw = """
        <!--[if gte mso 9]>metadata<![endif]-->
        <h2>Ignored heading wrapper</h2>
        <p class="MsoNormal" style="text-align:center;mso-margin-top-alt:auto">
        <b>Gross profit ₱1,250</b> and <i>x</i><sup>2</sup> H<sub>2</sub>O \\(x+1\\)
        </p>
        <ol start="3"><li>First</li><li>Second</li></ol>
        <table><thead><tr><th scope="col" colspan="2">Account</th></tr></thead>
        <tbody><tr><td rowspan="2">Cash</td><td>₱500</td></tr><tr><td>₱750</td></tr></tbody></table>
        <table><tr><td>Second table</td></tr></table>
        """
        result = canonicalize_scenario_content(raw)

        self.assertIn('class="tmp-align-center"', result.html)
        self.assertIn("<strong>Gross profit ₱1,250</strong>", result.html)
        self.assertIn("<em>x</em><sup>2</sup>", result.html)
        self.assertIn('colspan="2"', result.html)
        self.assertIn('rowspan="2"', result.html)
        self.assertEqual(result.html.count("<table>"), 2)
        self.assertEqual(canonicalize_scenario_content(result.html).html, result.html)
        self.assertTrue(result.warnings)

    def test_xss_and_office_metadata_are_removed(self):
        result = canonicalize_scenario_content(
            '<p id="x" class="bad" onclick="alert(1)" style="position:fixed">Safe'
            '<script>alert(1)</script><iframe src="https://bad.test">bad</iframe></p>'
            '<a href="javascript:alert(1)">link text</a>'
        )
        for forbidden in ("script", "iframe", "onclick", "javascript:", "position", "id=", "href", "class="):
            self.assertNotIn(forbidden, result.html.lower())
        self.assertIn("Safe", result.html)
        self.assertIn("link text", result.html)

    def test_images_and_native_word_equations_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "Images and diagrams"):
            canonicalize_scenario_content('<p>Diagram</p><img src="data:image/png;base64,abc">')
        with self.assertRaisesRegex(ValidationError, "native Word equation"):
            canonicalize_scenario_content("<m:oMath><m:r>x</m:r></m:oMath>")

    def test_svg_vml_and_comments_cannot_persist(self):
        for raw in (
            "<svg><text>diagram</text></svg>",
            '<v:shape><v:imagedata src="diagram.png"></v:imagedata></v:shape>',
        ):
            with self.subTest(raw=raw), self.assertRaisesRegex(
                ValidationError, "Images and diagrams"
            ):
                canonicalize_scenario_content(raw)
        result = canonicalize_scenario_content(
            "<p>before</p><!-- confidential comment --><p>after</p>"
        )
        self.assertNotIn("confidential comment", result.html)
        self.assertTrue(result.warnings)

    def test_pathological_size_and_table_span_are_rejected_without_truncation(self):
        with self.assertRaisesRegex(ValidationError, "request limit"):
            canonicalize_scenario_content("x" * (MAX_RAW_CHARACTERS + 1))
        with self.assertRaisesRegex(ValidationError, "colspan must be from 1 to 20"):
            canonicalize_scenario_content('<table><tr><td colspan="999">x</td></tr></table>')

    def test_canonical_node_depth_and_table_count_limits_remain_enforced(self):
        payloads = (
            (
                "<p>" + "x" * (MAX_CANONICAL_CHARACTERS + 1) + "</p>",
                "Canonical Case content",
            ),
            ("<p>x</p>" * (MAX_NODES + 1), "at most 2,000 HTML elements"),
            (
                "<strong>" * (MAX_DEPTH + 1) + "x" + "</strong>" * (MAX_DEPTH + 1),
                "at most 32 levels deep",
            ),
            (
                "<table><tr><td>x</td></tr></table>" * (MAX_TABLES + 1),
                "at most 10 tables",
            ),
        )
        for raw, message in payloads:
            with self.subTest(message=message), self.assertRaisesRegex(
                ValidationError, message
            ):
                canonicalize_scenario_content(raw)

    def test_nested_tables_cannot_bypass_outer_or_inner_row_limits(self):
        outer_overflow = (
            "<table><tr><td><table><tr><td>inner</td></tr></table></td></tr>"
            + "<tr><td>outer</td></tr>" * 100
            + "</table>"
        )
        inner_overflow = (
            "<table><tr><td><table>"
            + "<tr><td>inner</td></tr>" * 101
            + "</table></td></tr></table>"
        )
        for raw in (outer_overflow, inner_overflow):
            with self.subTest(raw_length=len(raw)), self.assertRaisesRegex(
                ValidationError, "at most 100 rows"
            ):
                canonicalize_scenario_content(raw)

    def test_nested_tables_cannot_bypass_outer_or_inner_column_limits(self):
        outer_overflow = (
            "<table><tr><td><table><tr><td>inner</td></tr></table></td>"
            + "<td>outer</td>" * 20
            + "</tr></table>"
        )
        inner_overflow = (
            "<table><tr><td><table><tr>"
            + "<td>inner</td>" * 21
            + "</tr></table></td></tr></table>"
        )
        for raw in (outer_overflow, inner_overflow):
            with self.subTest(raw_length=len(raw)), self.assertRaisesRegex(
                ValidationError, "at most 20 columns"
            ):
                canonicalize_scenario_content(raw)

    def test_nested_table_rowspan_and_colspan_remain_bounded(self):
        for attribute in ("rowspan", "colspan"):
            with self.subTest(attribute=attribute), self.assertRaisesRegex(
                ValidationError, rf"{attribute} must be from 1 to 20"
            ):
                canonicalize_scenario_content(
                    f'<table><tr><td><table><tr><td {attribute}="21">x</td>'
                    "</tr></table></td></tr></table>"
                )

    def test_html_void_elements_preserve_supported_trailing_content_and_idempotence(self):
        raw = (
            "<p>before<br>line</p><input type=text><meta charset=utf-8>"
            '<link rel="stylesheet"><embed src="x"><hr><source src="x">'
            "<track><wbr><p>after</p>"
        )
        result = canonicalize_scenario_content(raw)

        self.assertEqual(result.html, "<p>before<br>line</p><p>after</p>")
        self.assertEqual(canonicalize_scenario_content(result.html).html, result.html)
        for forbidden in ("input", "meta", "link", "embed", "hr", "source", "track", "wbr"):
            self.assertNotIn(forbidden, result.html)
        with self.assertRaisesRegex(ValidationError, "Images and diagrams"):
            canonicalize_scenario_content("<p>before</p><img><p>after</p>")

    def test_self_closing_html_void_elements_preserve_trailing_content_and_idempotence(self):
        raw = (
            "<p>before<br/>line</p><input/><meta/><link/><embed/><hr/>"
            "<source/><track/><wbr/><p>after</p>"
        )

        result = canonicalize_scenario_content(raw)

        self.assertEqual(result.html, "<p>before<br>line</p><p>after</p>")
        self.assertEqual(canonicalize_scenario_content(result.html).html, result.html)
        with self.assertRaisesRegex(ValidationError, "Images and diagrams"):
            canonicalize_scenario_content("<p>before</p><img/><p>after</p>")

    def test_self_closing_nonvoid_drop_with_content_is_rejected(self):
        for tag in (
            "form", "iframe", "object", "script", "style", "button",
            "select", "textarea", "option",
        ):
            with self.subTest(tag=tag), self.assertRaisesRegex(
                ValidationError, "malformed unsupported active markup"
            ):
                canonicalize_scenario_content(
                    f"<p>before</p><{tag}/><p>revealed?</p>"
                )

    def test_unclosed_unsupported_active_markup_rejects_instead_of_losing_later_content(self):
        with self.assertRaisesRegex(ValidationError, "unclosed unsupported active markup"):
            canonicalize_scenario_content("<p>before</p><form><p>after</p>")

    def test_mismatched_end_tag_cannot_escape_suppressed_form_boundary(self):
        raw = "<p>before<form><p>hidden</p></p><p>revealed?</p></form><p>after</p>"

        with self.assertRaisesRegex(ValidationError, "malformed unsupported active markup"):
            canonicalize_scenario_content(raw)

    def test_mismatched_nested_closing_tags_inside_suppression_are_rejected(self):
        with self.assertRaisesRegex(ValidationError, "malformed unsupported active markup"):
            canonicalize_scenario_content(
                "<p>before</p><form><div><span>hidden</div></span></form><p>after</p>"
            )

    def test_nested_suppressed_containers_remove_all_content_without_leaking(self):
        raw = (
            "<p>before</p><form><iframe><p>hidden</p></iframe>"
            "<object><p>also hidden</p></object></form><p>after</p>"
        )

        result = canonicalize_scenario_content(raw)

        self.assertEqual(result.html, "<p>before</p><p>after</p>")
        self.assertEqual(canonicalize_scenario_content(result.html).html, result.html)

    def test_matched_suppressed_container_preserves_trailing_valid_content(self):
        result = canonicalize_scenario_content(
            "<p>before</p><object><p>hidden</p></object><p>after</p>"
        )

        self.assertEqual(result.html, "<p>before</p><p>after</p>")

    def test_all_nonvoid_drop_with_content_boundaries_remain_structural(self):
        for tag in (
            "script", "style", "iframe", "object", "form", "button",
            "select", "textarea", "option",
        ):
            with self.subTest(tag=tag):
                result = canonicalize_scenario_content(
                    f"<p>before</p><{tag}><p>hidden</p></{tag}><p>after</p>"
                )
                self.assertEqual(result.html, "<p>before</p><p>after</p>")

    def test_rowspans_count_toward_effective_width_on_later_rows(self):
        raw = (
            "<table><tr>"
            + '<td rowspan="2">held</td>' * 20
            + "</tr><tr>"
            + "<td>new</td>" * 20
            + "</tr></table>"
        )

        with self.assertRaisesRegex(ValidationError, "at most 20 columns"):
            canonicalize_scenario_content(raw)

    def test_valid_rowspan_grid_within_effective_column_limit_is_accepted(self):
        raw = (
            "<table><tr>"
            + '<td rowspan="2">held</td>' * 10
            + "</tr><tr>"
            + "<td>new</td>" * 10
            + "</tr></table>"
        )

        result = canonicalize_scenario_content(raw)

        self.assertEqual(result.html.count('rowspan="2"'), 10)
        self.assertEqual(canonicalize_scenario_content(result.html).html, result.html)

    def test_colspan_combines_with_inherited_rowspan_occupancy(self):
        accepted = (
            '<table><tr><td rowspan="2">held</td></tr>'
            '<tr><td colspan="19">fits</td></tr></table>'
        )
        overflow = (
            '<table><tr><td rowspan="2">held</td></tr>'
            '<tr><td colspan="20">too wide</td></tr></table>'
        )
        overlap = (
            '<table><tr><td>first</td><td rowspan="2">held</td></tr>'
            '<tr><td colspan="2">overlap</td></tr></table>'
        )

        self.assertIn('colspan="19"', canonicalize_scenario_content(accepted).html)
        with self.assertRaisesRegex(ValidationError, "at most 20 columns"):
            canonicalize_scenario_content(overflow)
        with self.assertRaisesRegex(ValidationError, "overlapping table cells"):
            canonicalize_scenario_content(overlap)

    def test_rowspan_three_remains_occupied_across_both_later_rows(self):
        raw = (
            '<table><tr><td rowspan="3">held</td></tr>'
            '<tr><td colspan="19">second row fits</td></tr>'
            '<tr><td colspan="20">third row overflows</td></tr></table>'
        )

        with self.assertRaisesRegex(ValidationError, "at most 20 columns"):
            canonicalize_scenario_content(raw)

    def test_nested_table_grid_occupancy_is_independent(self):
        nested_row = "<tr>" + "<td>inner</td>" * 20 + "</tr>"
        raw = (
            '<table><tr><td rowspan="2"><table>'
            + nested_row
            + "</table></td></tr><tr>"
            + "<td>outer</td>" * 19
            + "</tr></table>"
        )

        result = canonicalize_scenario_content(raw)

        self.assertEqual(result.html.count("<table>"), 2)
        self.assertEqual(canonicalize_scenario_content(result.html).html, result.html)

    def test_typed_renderer_escapes_plain_text_and_accepts_only_canonical_rich_html(self):
        plain = str(render_scenario_content("<b>legacy</b>\nnext", "PLAIN_TEXT"))
        self.assertIn("&lt;b&gt;legacy&lt;/b&gt;<br>", plain)
        rich = str(render_scenario_content("<p><strong>Rich</strong></p>", "RICH_HTML_V1"))
        self.assertEqual(rich, "<p><strong>Rich</strong></p>")


class FacultyCaseFixtureMixin(Stage5FixtureMixin):
    def setUp(self):
        super().setUp()
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage="Structured Case outcomes",
        )
        self.parent = self.make_course(cycle=self.cycle, code="CASE")
        self.configuration = self.make_configuration(self.parent)
        self.faculty = self.make_faculty("case-owner")
        self.other_faculty = self.make_faculty("case-other")
        self.make_assignment(self.parent, self.faculty)
        self.make_assignment(self.parent, self.other_faculty)
        self.blueprint = BlueprintMutationService.save_structure(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_revision=0,
            mode=ExamBlueprint.Mode.USE_SECTIONS,
            sections=(
                {"title": "Section A", "instructions": "", "display_order": 1, "item_quota": 30},
                {"title": "Section B", "instructions": "", "display_order": 2, "item_quota": 20},
            ),
        )[0]
        self.configuration = CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=self.configuration.revision,
        )[0]
        self.contribution = FacultyContribution.objects.get(faculty_user=self.faculty)
        self.other_contribution = FacultyContribution.objects.get(faculty_user=self.other_faculty)
        self.section_a, self.section_b = self.blueprint.sections.order_by("display_order")
        self.client.force_login(self.faculty)

    def save_case(self, *, contribution=None, title="Audit Case", html=None, section=None):
        contribution = contribution or self.contribution
        scenario, _creating = FacultyCaseMutationService.save(
            contribution_id=contribution.id,
            user=contribution.faculty_user,
            tenant_id=self.tenant.id,
            campus_id=contribution.source_campus_id,
            expected_contribution_revision=contribution.revision,
            expected_scenario_revision=0,
            title=title,
            raw_content=html or "<p><strong>Confidential narrative ₱500</strong></p>",
            section_id=(section or self.section_a).id,
        )
        contribution.refresh_from_db()
        return scenario

    def add_question(self, *, scenario=None, section=None, text="Linked question"):
        self.contribution.refresh_from_db()
        return QuestionMutationService.create(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            payload=self.payload(text),
            section_id=(section or self.section_a).id,
            scenario_id=scenario.id if scenario else None,
        )


class FacultyCaseWorkflowTests(FacultyCaseFixtureMixin, Stage4TestCase):
    def _prepare_legacy_stage6_boundary(self):
        self.campus.code = "CUBAO"
        self.campus.save(update_fields=["code", "updated_at"])
        faculty_case = self.save_case(
            title="FACULTY-OWNED-CASE-MARKER",
            html="<p>FACULTY-OWNED-STIMULUS-MARKER</p>",
        )
        faculty_question = self.add_question(
            scenario=faculty_case, text="Faculty-owned linked question"
        )
        faculty_case.refresh_from_db()
        other_questions = []
        for text in ("Other contributor first", "Other contributor second"):
            self.other_contribution.refresh_from_db()
            other_questions.append(
                QuestionMutationService.create(
                    contribution_id=self.other_contribution.id,
                    user=self.other_faculty,
                    tenant_id=self.tenant.id,
                    campus_id=self.other_contribution.source_campus_id,
                    expected_contribution_revision=self.other_contribution.revision,
                    payload=self.payload(text),
                    section_id=self.section_a.id,
                )
            )
        now = timezone.now()
        for contribution in (self.contribution, self.other_contribution):
            contribution.status = FacultyContribution.Status.SUBMITTED
            contribution.submitted_at = now
            contribution.save(update_fields=["status", "submitted_at", "updated_at"])
        self.configuration.workflow_status = self.configuration.WorkflowStatus.CLOSED
        self.configuration.closed_at = now
        self.configuration.save(
            update_fields=["workflow_status", "closed_at", "updated_at"]
        )
        self.parent.reviewer = self.reviewer
        self.parent.save(update_fields=["reviewer", "updated_at"])
        self.client.force_login(self.reviewer)
        return faculty_case, faculty_question, other_questions

    def test_direct_deny_blocks_preview_save_and_guessed_case_routes(self):
        scenario = self.save_case()
        permission = Permission.objects.get(code="faculty_portal.access")
        UserPermission.objects.create(
            user=self.faculty,
            permission=permission,
            tenant=self.tenant,
            campus=self.campus,
            grant_type=UserPermission.GrantType.DENY,
        )
        for method, url, data in (
            (self.client.get, reverse("departmental_exams:faculty_case_detail", args=[self.contribution.id, scenario.id]), None),
            (self.client.post, reverse("departmental_exams:faculty_case_preview", args=[self.contribution.id]), {"stimulus": "<p>x</p>"}),
            (self.client.post, reverse("departmental_exams:faculty_case_edit", args=[self.contribution.id, scenario.id]), {}),
        ):
            with self.subTest(url=url):
                response = method(url, data) if data is not None else method(url)
                self.assertEqual(response.status_code, 403)

    def test_workspace_exposes_two_clear_paths_only_for_structured_manual(self):
        response = self.client.get(reverse("departmental_exams:contribution_workspace", args=[self.contribution.id]))
        self.assertContains(response, "Add Standalone MCQ")
        self.assertContains(response, "Create Case-Based Question Group")
        self.cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        self.cycle.save(update_fields=["processing_mode", "updated_at"])
        response = self.client.get(reverse("departmental_exams:contribution_workspace", args=[self.contribution.id]))
        self.assertNotContains(response, "Create Case-Based Question Group")
        denied = self.client.get(reverse("departmental_exams:faculty_case_create", args=[self.contribution.id]))
        self.assertEqual(denied.status_code, 403)

    def test_rendered_case_form_has_one_resolvable_hidden_source_control(self):
        response = self.client.get(
            reverse("departmental_exams:faculty_case_create", args=[self.contribution.id])
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertEqual(body.count('name="stimulus"'), 1)
        self.assertRegex(
            body,
            r'<input[^>]+type="hidden"[^>]+name="stimulus"[^>]+data-case-source',
        )
        self.assertIn("data-case-rich-editor", body)
        self.assertIn("data-case-preview-button", body)
        self.assertNotIn("<textarea name=\"stimulus\"", body)

    def test_feature_off_preserves_existing_question_ui_and_case_route_denies(self):
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        response = self.client.get(reverse("departmental_exams:contribution_workspace", args=[self.contribution.id]))
        self.assertContains(response, ">Add question<")
        self.assertNotContains(response, "Create Case-Based Question Group")
        self.assertEqual(
            self.client.get(reverse("departmental_exams:faculty_case_create", args=[self.contribution.id])).status_code,
            403,
        )

    def test_preview_and_save_share_canonical_authority_and_audit_is_confidential(self):
        raw = '<p class="MsoNormal" onclick="bad()"><b>Confidential ₱700</b></p>'
        preview = self.client.post(
            reverse("departmental_exams:faculty_case_preview", args=[self.contribution.id]),
            {"stimulus": raw, "input_format": "html"},
        )
        self.assertEqual(preview.status_code, 200)
        canonical = preview.json()["html"]
        response = self.client.post(
            reverse("departmental_exams:faculty_case_create", args=[self.contribution.id]),
            {
                "expected_contribution_revision": self.contribution.revision,
                "expected_scenario_revision": 0,
                "title": "Private Case",
                "stimulus": raw,
                "section_id": self.section_a.id,
            },
        )
        self.assertEqual(
            response.status_code,
            302,
            getattr(response.context.get("form"), "errors", None) if response.context else None,
        )
        scenario = ExamScenario.objects.get(contribution=self.contribution)
        self.assertEqual(scenario.stimulus, canonical)
        self.assertEqual(scenario.content_format, ExamScenario.ContentFormat.RICH_HTML_V1)
        audit = AuditLog.objects.get(action="DE_EXAM_FACULTY_CASE_CREATED")
        serialized = str(audit.metadata_json)
        self.assertIn("content_digest", serialized)
        self.assertNotIn("Confidential", serialized)
        self.assertNotIn("₱700", serialized)

    def test_preview_rejects_images_and_word_equations_without_persistence(self):
        for raw, message in (
            ('<p>x</p><img src="file:///x.png">', "Images and diagrams"),
            ("<m:oMath>x</m:oMath>", "native Word equation"),
        ):
            with self.subTest(raw=raw):
                response = self.client.post(
                    reverse("departmental_exams:faculty_case_preview", args=[self.contribution.id]),
                    {"stimulus": raw},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, str(response.json()))
        self.assertFalse(ExamScenario.objects.filter(contribution=self.contribution).exists())

    def test_owner_crud_guessed_cross_faculty_urls_and_nonempty_delete(self):
        scenario = self.save_case()
        question = self.add_question(scenario=scenario)
        detail = self.client.get(reverse("departmental_exams:faculty_case_detail", args=[self.contribution.id, scenario.id]))
        self.assertContains(detail, "Confidential narrative")
        self.assertContains(detail, "Linked question")
        self.assertEqual(
            self.client.get(reverse("departmental_exams:faculty_case_detail", args=[self.other_contribution.id, scenario.id])).status_code,
            404,
        )
        self.contribution.refresh_from_db()
        scenario.refresh_from_db()
        blocked = self.client.post(
            reverse("departmental_exams:faculty_case_delete", args=[self.contribution.id, scenario.id]),
            {
                "expected_contribution_revision": self.contribution.revision,
                "expected_scenario_revision": scenario.revision,
            },
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertTrue(ExamScenario.objects.filter(pk=scenario.id).exists())
        self.assertTrue(Question.objects.filter(pk=question.id).exists())

    def test_linked_and_standalone_questions_share_quota_and_section_rules(self):
        scenario = self.save_case()
        linked = self.add_question(scenario=scenario)
        standalone = self.add_question(section=self.section_b, text="Standalone")
        self.assertEqual(self.contribution.questions.count(), 2)
        self.assertEqual(linked.exam_scenario_membership.scenario_id, scenario.id)
        self.assertEqual(linked.blueprint_placement.section_id, self.section_a.id)
        self.assertEqual(standalone.blueprint_placement.section_id, self.section_b.id)
        with self.assertRaisesRegex(ValidationError, "Case Exam Section"):
            self.add_question(scenario=scenario, section=self.section_b, text="Mismatch")

    def test_member_order_is_explicit_and_question_delete_preserves_case(self):
        scenario = self.save_case()
        first = self.add_question(scenario=scenario, text="First")
        second = self.add_question(scenario=scenario, text="Second")
        self.contribution.refresh_from_db()
        scenario.refresh_from_db()
        changed = FacultyCaseMutationService.reorder_members(
            contribution_id=self.contribution.id,
            scenario_id=scenario.id,
            ordered_question_ids=[second.id, first.id],
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            expected_scenario_revision=scenario.revision,
        )
        self.assertTrue(changed)
        self.assertEqual(
            list(scenario.members.order_by("position").values_list("question_id", flat=True)),
            [second.id, first.id],
        )
        self.contribution.refresh_from_db()
        second.refresh_from_db()
        QuestionMutationService.delete(
            contribution_id=self.contribution.id,
            question_id=second.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            expected_question_revision=second.revision,
        )
        self.assertTrue(ExamScenario.objects.filter(pk=scenario.id).exists())
        self.assertEqual(list(scenario.members.values_list("position", flat=True)), [1])

    def test_submission_rejects_empty_case_unplaced_and_section_mismatch(self):
        scenario = self.save_case()
        with self.assertRaisesRegex(ValidationError, "at least one Linked Question"):
            FacultyCasePolicy.validate_submission(
                contribution=self.contribution, questions=[], tenant_id=self.tenant.id
            )
        question = self.add_question(scenario=scenario)
        placement = question.blueprint_placement
        placement.section = self.section_b
        placement.save(update_fields=["section", "updated_at"])
        with self.assertRaisesRegex(ValidationError, "match its Case Exam Section"):
            FacultyCasePolicy.validate_submission(
                contribution=self.contribution,
                questions=[question],
                tenant_id=self.tenant.id,
            )

    def test_submission_rejects_reverse_membership_outside_owned_case_set(self):
        question = self.add_question(text="Reverse membership probe")
        foreign_case = ExamScenario.objects.create(
            blueprint=self.blueprint,
            section=self.section_a,
            contribution=self.other_contribution,
            title="Foreign Case",
            stimulus="<p>Foreign canonical Case</p>",
            content_format=ExamScenario.ContentFormat.RICH_HTML_V1,
            created_by=self.other_faculty,
            updated_by=self.other_faculty,
        )
        ExamScenarioMember.objects.create(
            scenario=foreign_case, question=question, position=1
        )

        with self.assertRaisesRegex(
            ValidationError, "belongs to a Case outside this contribution"
        ):
            FacultyCasePolicy.validate_submission(
                contribution=self.contribution,
                questions=[question],
                tenant_id=self.tenant.id,
            )

    def test_legacy_stage6_cannot_list_update_delete_or_replace_faculty_case(self):
        faculty_case, faculty_question, other_questions = (
            self._prepare_legacy_stage6_boundary()
        )
        before = (
            faculty_case.title,
            faculty_case.stimulus,
            faculty_case.revision,
            list(faculty_case.members.values_list("question_id", "position")),
        )

        with self.assertRaises(Http404):
            ScenarioMutationService.save(
                cycle_course_id=self.parent.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                title="Reviewer replacement",
                stimulus="Reviewer replacement stimulus",
                question_ids=[question.id for question in other_questions],
                section_id=self.section_a.id,
                scenario_id=faculty_case.id,
                expected_revision=faculty_case.revision,
            )
        with self.assertRaises(Http404):
            ScenarioMutationService.delete(
                scenario_id=faculty_case.id,
                tenant_id=self.tenant.id,
                actor=self.reviewer,
                expected_revision=faculty_case.revision,
            )

        faculty_case.refresh_from_db()
        self.assertEqual(
            before,
            (
                faculty_case.title,
                faculty_case.stimulus,
                faculty_case.revision,
                list(faculty_case.members.values_list("question_id", "position")),
            ),
        )
        self.assertEqual(faculty_case.members.get().question_id, faculty_question.id)
        self.assertFalse(
            ExamScenarioMember.objects.filter(question__in=other_questions).exists()
        )
        review = self.client.get(
            reverse("departmental_exams:blueprint_review", args=[self.parent.id])
        )
        self.assertEqual(review.status_code, 200)
        self.assertNotContains(review, "FACULTY-OWNED-CASE-MARKER")
        self.assertNotContains(review, "FACULTY-OWNED-STIMULUS-MARKER")

        readiness = Stage6ReadinessService.evaluate(cycle_course=self.parent)
        self.assertEqual(readiness["scenario_count"], 0)
        self.assertNotIn(
            "SCENARIOS_INVALID", {row["code"] for row in readiness["blockers"]}
        )

    def test_legacy_null_contribution_scenario_crud_still_works(self):
        faculty_case, _faculty_question, other_questions = (
            self._prepare_legacy_stage6_boundary()
        )
        faculty_before = (
            faculty_case.title,
            faculty_case.stimulus,
            faculty_case.revision,
            list(faculty_case.members.values_list("question_id", "position")),
        )
        legacy, changed = ScenarioMutationService.save(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            title="Legacy reviewer scenario",
            stimulus="Legacy reviewer stimulus",
            question_ids=[question.id for question in other_questions],
            section_id=self.section_a.id,
            expected_revision=0,
        )
        self.assertTrue(changed)
        self.assertIsNone(legacy.contribution_id)
        legacy, changed = ScenarioMutationService.save(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            title="Updated legacy reviewer scenario",
            stimulus="Updated legacy reviewer stimulus",
            question_ids=[question.id for question in reversed(other_questions)],
            section_id=self.section_a.id,
            scenario_id=legacy.id,
            expected_revision=legacy.revision,
        )
        self.assertTrue(changed)
        self.assertEqual(
            list(legacy.members.order_by("position").values_list("question_id", flat=True)),
            [question.id for question in reversed(other_questions)],
        )
        deleted_course_id = ScenarioMutationService.delete(
            scenario_id=legacy.id,
            tenant_id=self.tenant.id,
            actor=self.reviewer,
            expected_revision=legacy.revision,
        )
        self.assertEqual(deleted_course_id, self.parent.id)
        self.assertFalse(ExamScenario.objects.filter(pk=legacy.id).exists())
        faculty_case.refresh_from_db()
        self.assertEqual(
            faculty_before,
            (
                faculty_case.title,
                faculty_case.stimulus,
                faculty_case.revision,
                list(faculty_case.members.values_list("question_id", "position")),
            ),
        )

    def test_submitted_contribution_is_immutable_and_reopen_does_not_unfreeze_structure(self):
        scenario = self.save_case()
        self.blueprint.refresh_from_db()
        frozen_at = self.blueprint.structure_frozen_at
        self.contribution.status = FacultyContribution.Status.SUBMITTED
        self.contribution.submitted_at = self.configuration.opened_at
        self.contribution.save(update_fields=["status", "submitted_at", "updated_at"])
        response = self.client.get(reverse("departmental_exams:faculty_case_edit", args=[self.contribution.id, scenario.id]))
        self.assertEqual(response.status_code, 403)
        self.contribution.status = FacultyContribution.Status.DRAFT
        self.contribution.submitted_at = None
        self.contribution.save(update_fields=["status", "submitted_at", "updated_at"])
        self.blueprint.refresh_from_db()
        self.assertEqual(self.blueprint.structure_frozen_at, frozen_at)

    def test_plain_standalone_question_regression_remains_available_when_feature_off(self):
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        question = QuestionMutationService.create(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            payload=self.payload("Legacy standalone"),
        )
        self.assertEqual(question.question_text, "Legacy standalone")
        self.assertFalse(QuestionBlueprintPlacement.objects.filter(question=question).exists())
        self.assertFalse(ExamScenarioMember.objects.filter(question=question).exists())

    def test_no_sections_case_and_linked_question_use_implicit_structure(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            default_coverage="No Sections outcomes",
            scope_suffix="NOSECTIONS",
        )
        parent = self.make_course(cycle=cycle, code="NOSEC")
        configuration = self.make_configuration(parent)
        faculty = self.make_faculty("no-sections-owner")
        self.make_assignment(parent, faculty)
        blueprint = BlueprintMutationService.save_structure(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
            expected_revision=0,
            mode=ExamBlueprint.Mode.NO_SECTIONS,
            sections=(),
        )[0]
        CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            user=self.configurer,
            expected_revision=configuration.revision,
        )
        contribution = FacultyContribution.objects.get(cycle_course=parent, faculty_user=faculty)
        scenario, _created = FacultyCaseMutationService.save(
            contribution_id=contribution.id,
            user=faculty,
            tenant_id=self.tenant.id,
            campus_id=faculty.default_campus_id,
            expected_contribution_revision=contribution.revision,
            title="Implicit section Case",
            raw_content="<p>Case narrative</p>",
        )
        contribution.refresh_from_db()
        question = QuestionMutationService.create(
            contribution_id=contribution.id,
            user=faculty,
            tenant_id=self.tenant.id,
            campus_id=faculty.default_campus_id,
            expected_contribution_revision=contribution.revision,
            payload=self.payload("Implicit linked question"),
            scenario_id=scenario.id,
        )
        self.assertEqual(scenario.blueprint_id, blueprint.id)
        self.assertIsNone(scenario.section_id)
        self.assertFalse(QuestionBlueprintPlacement.objects.filter(question=question).exists())
        self.assertEqual(question.exam_scenario_membership.scenario_id, scenario.id)
