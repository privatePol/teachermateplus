from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from apps.academics.models import (
    AcademicYear,
    Course,
    CourseOffering,
    FacultyAssignment,
    Section,
    Term,
)
from apps.auditlog.models import AuditLog
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission
from apps.tenants.models import Program

from .automatic_workflow import AutomaticGenerationSummaryService
from .exam_units import ExamCourseEquivalencyService
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExamGenerationRevision,
    ExaminationCycle,
    FacultyContribution,
    FacultyContributionEligibilitySource,
    GeneratedExamItem,
    GeneratedExamSet,
    Question,
    QuestionnairePrintRelease,
)
from .questionnaire_printing import QuestionnairePrintReleaseService
from .stage4_test_support import Stage4TestCase


MANILA = ZoneInfo("Asia/Manila")


class QuestionnairePrintReleaseTests(Stage4TestCase):
    PRINT_SCHOOL_NAME = "National College of Business and Arts"
    PRINT_CAMPUS_LINE = "Cubao-Fairview-Taytay"

    def setUp(self):
        super().setUp()
        SystemSettingService.set(
            "PRINT_HEADER_SCHOOL_NAME",
            self.PRINT_SCHOOL_NAME,
            tenant_id=self.tenant.id,
        )
        SystemSettingService.set(
            "PRINT_HEADER_SCHOOL_ADDRESS",
            self.PRINT_CAMPUS_LINE,
            tenant_id=self.tenant.id,
        )
        self.manager_user = self.make_user(
            "questionnaire-release-manager",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.manage_exam_generation",
            ),
        )
        self.faculty = self.make_user(
            "questionnaire-print-faculty",
            self.department,
            ("faculty_portal.access",),
        )
        cycle = self.make_cycle(status=ExaminationCycle.Status.OPEN)
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        self.parent = self.make_course(cycle=cycle, department=None, code="PRINT-101")
        self.configuration = self.make_configuration(
            self.parent,
            workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
            opened_at=timezone.now() - timezone.timedelta(days=2),
        )
        self.assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.parent.offering_snapshots.get().offering,
            faculty_user=self.faculty,
            accepted_by=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            responded_at=timezone.now(),
            accepted_at=timezone.now(),
            is_primary=True,
        )
        self.contribution = FacultyContribution.objects.create(
            cycle_course=self.parent,
            faculty_user=self.faculty,
            source_assignment=self.assignment,
            source_campus=self.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=self.configuration.revision,
            status=FacultyContribution.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        FacultyContributionEligibilitySource.objects.create(
            contribution=self.contribution,
            assignment=self.assignment,
            assignment_id_snapshot=self.assignment.id,
            offering_id_snapshot=self.assignment.offering_id,
            tenant_id_snapshot=self.tenant.id,
            campus_id_snapshot=self.campus.id,
        )
        self.questions = [
            Question.objects.create(
                contribution=self.contribution,
                question_text=f"Safe question {position}",
                choice_a=f"Choice A{position}",
                choice_b=f"Choice B{position}",
                choice_c=f"Choice C{position}",
                choice_d=f"Choice D{position}",
                correct_answer="D",
                difficulty=("EASY" if position == 1 else "MODERATE"),
                position=position,
            )
            for position in (1, 2)
        ]
        self.r2 = self._make_revision(self.parent, revision_number=2)

    def _make_revision(
        self,
        parent,
        *,
        revision_number,
        supersedes=None,
        with_sets=True,
    ):
        revision = ExamGenerationRevision.objects.create(
            cycle_course=parent,
            revision_number=revision_number,
            source_input_fingerprint=str(revision_number) * 64,
            algorithm_version="automatic-print-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="r" * 64,
            final_item_count_snapshot=2,
            request_token_digest=(str(revision_number + 3) * 64)[:64],
            supersedes=supersedes,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=1,
            squared_contributor_concentration=4,
        )
        if not with_sets:
            return revision
        for set_code in (GeneratedExamSet.SetCode.A, GeneratedExamSet.SetCode.B):
            generated_set = GeneratedExamSet.objects.create(
                generation_revision=revision,
                set_code=set_code,
                campus_quotas_snapshot={"PRIVATE-CAMPUS": 2},
                difficulty_quotas_snapshot={
                    "EASY": 1,
                    "MODERATE": 1,
                    "DIFFICULT": 0,
                },
                section_quotas_snapshot={"0": 2},
                item_count=2,
            )
            for position, question in enumerate(self.questions, start=1):
                GeneratedExamItem.objects.create(
                    generated_set=generated_set,
                    position=position,
                    source_question=question,
                    source_question_revision=question.revision,
                    source_question_digest="SECRET-DIGEST-" + "x" * 50,
                    source_contributor=self.faculty,
                    source_contributor_id_snapshot=self.faculty.id,
                    source_contributor_name_snapshot="CONFIDENTIAL CONTRIBUTOR",
                    source_campus=self.campus,
                    campus_code_snapshot="PRIVATE-CAMPUS",
                    campus_name_snapshot="Private provenance campus",
                    difficulty_snapshot=("EASY" if position == 1 else "MODERATE"),
                    section_title_snapshot="Internal section",
                    question_text_snapshot=f"Released {set_code} question {position}",
                    choices_snapshot=[
                        f"{set_code} choice A{position}",
                        f"{set_code} choice B{position}",
                        f"{set_code} choice C{position}",
                        f"{set_code} choice D{position}",
                    ],
                    correct_answer_snapshot="D",
                )
        return revision

    def _release(self, *, revision=None, print_from=None, print_until=None):
        now = timezone.now()
        return QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id,
            revision_id=(revision or self.r2).id,
            tenant_id=self.tenant.id,
            actor=self.manager_user,
            print_from=print_from or now - timezone.timedelta(minutes=5),
            print_until=print_until or now + timezone.timedelta(hours=2),
        )

    def _second_bulk_target(self):
        parent = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="PRINT-BULK",
        )
        return parent, self._make_revision(parent, revision_number=2)

    @staticmethod
    def _bulk_window():
        print_from = timezone.localtime(timezone.now()).replace(
            second=0,
            microsecond=0,
        ) + timezone.timedelta(hours=1)
        return print_from, print_from + timezone.timedelta(days=1)

    def _bulk_post(self, selections, *, print_from=None, print_until=None, user=None):
        default_from, default_until = self._bulk_window()
        print_from = print_from or default_from
        print_until = print_until or default_until
        client = Client()
        client.force_login(user or self.manager_user)
        return client.post(
            reverse("departmental_exams:questionnaire_print_release"),
            {
                "action": "bulk_release",
                "selections": [
                    f"{course.id}:{revision.id}"
                    for course, revision in selections
                ],
                "print_from": print_from.strftime("%Y-%m-%dT%H:%M"),
                "print_until": print_until.strftime("%Y-%m-%dT%H:%M"),
            },
        )

    def _bulk_page(self):
        client = Client()
        client.force_login(self.manager_user)
        return client.get(reverse("departmental_exams:questionnaire_print_release"))

    def _newer_revision(self, parent, revision):
        ExamGenerationRevision.objects.filter(pk=revision.pk).update(
            status=ExamGenerationRevision.Status.SUPERSEDED,
            current_marker=None,
        )
        revision.refresh_from_db()
        return self._make_revision(
            parent,
            revision_number=revision.revision_number + 1,
            supersedes=revision,
        )

    def _faculty_client(self, user=None):
        client = Client()
        client.force_login(user or self.faculty)
        return client

    def _print_url(self, release, set_code="A", contribution=None):
        return reverse(
            "departmental_exams:questionnaire_print",
            args=[
                (contribution or self.contribution).id,
                release.id,
                set_code,
            ],
        )

    def _admin_print_url(self, revision=None, set_code="A"):
        return reverse(
            "departmental_exams:admin_questionnaire_print",
            args=[(revision or self.r2).id, set_code],
        )

    def test_admin_direct_prints_exact_set_a_and_b_without_faculty_release(self):
        client = Client()
        client.force_login(self.manager_user)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        page = client.get(reverse("departmental_exams:questionnaire_print_release"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Print Set A")
        self.assertContains(page, "Print Set B")

        for set_code in ("A", "B"):
            with self.subTest(set_code=set_code):
                response = client.get(self._admin_print_url(set_code=set_code))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["revision_number"], 2)
                self.assertEqual(response.context["set_code"], set_code)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertIn("private", response["Cache-Control"])
                body = response.content.decode()
                self.assertIn(f"Released {set_code} question 1", body)
                self.assertNotIn("CONFIDENTIAL CONTRIBUTOR", body)
                self.assertNotIn("PRIVATE-CAMPUS", body)
                self.assertNotIn("Private provenance campus", body)
                self.assertNotIn("SECRET-DIGEST", body)
                self.assertNotIn("MODERATE", body)

        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        audits = AuditLog.objects.filter(
            action="DE_ADMIN_QUESTIONNAIRE_PRINT_SET_ACCESSED"
        )
        self.assertEqual(audits.count(), 2)
        for audit in audits:
            metadata = str(audit.metadata_json).lower()
            self.assertNotIn("answer", metadata)
            self.assertNotIn("question_text", metadata)
            self.assertNotIn("fingerprint", metadata)

    def test_faculty_and_admin_questionnaires_support_all_paper_sizes_for_both_sets(self):
        release = self._release()
        admin_client = Client()
        admin_client.force_login(self.manager_user)
        portals = (
            (
                "faculty",
                self._faculty_client(),
                lambda set_code: self._print_url(release, set_code),
            ),
            (
                "admin",
                admin_client,
                lambda set_code: self._admin_print_url(set_code=set_code),
            ),
        )
        paper_sizes = (
            ("letter", "Letter", "8.5in", "11in"),
            ("a4", "A4", "210mm", "297mm"),
            ("legal", "Legal", "8.5in", "14in"),
        )

        for portal, client, url_for_set in portals:
            for set_code in ("A", "B"):
                for paper_value, css_size, sheet_width, sheet_height in paper_sizes:
                    with self.subTest(
                        portal=portal,
                        set_code=set_code,
                        paper=paper_value,
                    ):
                        response = client.get(
                            url_for_set(set_code),
                            {"paper": paper_value},
                        )
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.context["set_code"], set_code)
                        self.assertEqual(response.context["paper_size"], paper_value)
                        self.assertContains(
                            response,
                            f"@page {{ size: {css_size} portrait; margin: 0.55in 0.6in 0.85in; }}",
                        )
                        self.assertContains(
                            response,
                            (
                                f".questionnaire {{ width: {sheet_width}; "
                                f"min-height: {sheet_height};"
                            ),
                        )
                        self.assertContains(
                            response,
                            f'<option value="{paper_value}" selected>{css_size}</option>',
                            html=True,
                        )
                        self.assertContains(response, "vendor/katex/0.18.4/katex.min.css")
                        self.assertContains(response, "vendor/katex/0.18.4/katex.min.js")
                        self.assertContains(response, "departmental_exam_scientific_notation.js")
                        self.assertContains(response, "data-scientific-print", html=False)
                        self.assertContains(response, "data-scientific-content", html=False)

        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_QUESTIONNAIRE_PRINT_SET_ACCESSED"
            ).count(),
            6,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="DE_ADMIN_QUESTIONNAIRE_PRINT_SET_ACCESSED"
            ).count(),
            6,
        )
        for audit in AuditLog.objects.filter(
            action__in=(
                "DE_QUESTIONNAIRE_PRINT_SET_ACCESSED",
                "DE_ADMIN_QUESTIONNAIRE_PRINT_SET_ACCESSED",
            )
        ):
            self.assertNotIn("paper", audit.metadata_json)

    def test_questionnaire_layout_keeps_school_name_and_footer_print_safe(self):
        release = self._release()
        admin_client = Client()
        admin_client.force_login(self.manager_user)
        responses = (
            self._faculty_client().get(self._print_url(release, "A")),
            admin_client.get(self._admin_print_url(set_code="A")),
        )

        for response in responses:
            with self.subTest(portal=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["paper_size"], "letter")
                self.assertContains(response, "@page { size: Letter portrait;")
                self.assertContains(
                    response,
                    "font-size: 14pt; line-height: 1.05;",
                )
                self.assertContains(response, "white-space: nowrap;")
                self.assertContains(
                    response,
                    ".question { margin: 0 0 12px; break-inside: avoid-page; page-break-inside: avoid; }",
                )
                self.assertContains(
                    response,
                    ".confidential-footer { position: static; margin-top: 0.25in;",
                )
                self.assertNotContains(
                    response,
                    ".confidential-footer { position: fixed;",
                )
                self.assertContains(
                    response,
                    '<section class="questions" aria-label="Multiple-choice questions">',
                    html=False,
                )

    def test_unknown_paper_size_falls_back_to_letter_without_reflection(self):
        release = self._release()
        admin_client = Client()
        admin_client.force_login(self.manager_user)
        unsafe_value = "a4; } body { display: none"

        responses = (
            self._faculty_client().get(
                self._print_url(release, "A"),
                {"paper": unsafe_value},
            ),
            admin_client.get(
                self._admin_print_url(set_code="A"),
                {"paper": unsafe_value},
            ),
        )
        for response in responses:
            with self.subTest(portal=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["paper_size"], "letter")
                self.assertContains(response, "@page { size: Letter portrait;")
                self.assertNotContains(response, unsafe_value)

    def test_html_and_link_shaped_snapshots_remain_escaped_across_confidential_outputs(self):
        html_fragments = (
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "<svg onload=alert(1)>",
            '<a href="javascript:alert(1)">click</a>',
        )
        question_text = "\n".join(html_fragments)
        choices = [
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "https://example.invalid/exam",
        ]
        GeneratedExamItem.objects.filter(
            generated_set__generation_revision=self.r2,
            generated_set__set_code=GeneratedExamSet.SetCode.A,
            position=1,
        ).update(
            question_text_snapshot=question_text,
            choices_snapshot=choices,
        )

        admin_client = Client()
        admin_client.force_login(self.manager_user)
        release = self._release()
        responses = (
            (
                "generated revision",
                admin_client.get(
                    reverse(
                        "departmental_exams:generated_revision_detail",
                        args=[self.r2.id],
                    )
                ),
                True,
            ),
            (
                "selection audit",
                admin_client.get(
                    reverse(
                        "departmental_exams:generation_selection_audit",
                        args=[self.r2.id],
                    )
                ),
                False,
            ),
            (
                "admin questionnaire print",
                admin_client.get(self._admin_print_url(set_code="A")),
                True,
            ),
            (
                "faculty questionnaire print",
                self._faculty_client().get(self._print_url(release, "A")),
                True,
            ),
        )
        for label, response, exposes_choices in responses:
            with self.subTest(output=label):
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                for fragment in html_fragments:
                    self.assertIn(escape(fragment), body)
                    self.assertNotIn(fragment, body)
                if exposes_choices:
                    for choice in choices:
                        self.assertIn(escape(choice), body)
                lowered = body.lower()
                self.assertNotIn('href="javascript:', lowered)
                self.assertNotIn('href="data:', lowered)
                self.assertNotIn('href="vbscript:', lowered)
                self.assertNotIn('<a href="https://example.invalid/exam', lowered)

    def test_questionnaire_outputs_preserve_scientific_notation_for_question_and_choices(self):
        notation_question = (
            r"\(\frac{x}{y}\) \(\sqrt{x}\) \(x^2\) \(x_n\) "
            r"\(\alpha + \theta\) \(\sum_i^n i\) \(\int_0^1 x\,dx\)"
        )
        notation_choices = [
            r"\(\ce{2H2 + O2 -> 2H2O}\)",
            r"\[\begin{bmatrix}a & b \\ c & d\end{bmatrix}\]",
            r"\(\pi \le \infty\)",
            r"ordinary text",
        ]
        GeneratedExamItem.objects.filter(
            generated_set__generation_revision=self.r2,
            generated_set__set_code=GeneratedExamSet.SetCode.A,
            position=1,
        ).update(
            question_text_snapshot=notation_question,
            choices_snapshot=notation_choices,
        )
        release = self._release()
        admin_client = Client()
        admin_client.force_login(self.manager_user)
        responses = (
            admin_client.get(self._admin_print_url(set_code="A")),
            self._faculty_client().get(self._print_url(release, "A")),
        )
        for response in responses:
            with self.subTest(portal=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, notation_question)
                for choice in notation_choices:
                    self.assertContains(response, escape(choice))
                self.assertContains(response, "data-scientific-content", html=False)
                self.assertContains(response, "vendor/katex/0.18.4/contrib/mhchem.min.js")

    def test_release_page_renders_each_campus_once_for_repeated_offerings(self):
        original_offering = self.parent.offering_snapshots.get().offering
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=original_offering.program,
            code="PRINT-DUPLICATE-CAMPUS",
            name="Print Duplicate Campus Section",
        )
        repeated_campus_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=original_offering.program,
            academic_year=self.parent.cycle.academic_year,
            term=self.parent.cycle.term,
            course=self.parent.course,
            section=section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=self.parent,
            offering=repeated_campus_offering,
            campus=self.campus,
        )
        client = Client()
        client.force_login(self.manager_user)

        response = client.get(
            reverse("departmental_exams:questionnaire_print_release")
        )

        self.assertEqual(response.status_code, 200)
        course = next(
            item for item in response.context["courses"] if item.id == self.parent.id
        )
        self.assertEqual(
            tuple(campus.id for campus in course.print_release_campuses),
            (self.campus.id,),
        )
        content = response.content.decode()
        questionnaire_pane, answer_key_pane = content.split(
            'id="questionnaire-releases-pane"', 1
        )[1].split('id="answer-key-releases-pane"', 1)
        campus_header = f"&middot; {self.campus.name}</div>"
        # The deduplicated campus appears once in each Release Center tab.
        self.assertEqual(questionnaire_pane.count(campus_header), 1)
        self.assertEqual(answer_key_pane.count(campus_header), 1)

    def test_bulk_list_shows_one_current_r1_and_server_derived_badge(self):
        ExamGenerationRevision.objects.filter(pk=self.r2.pk).update(
            status=ExamGenerationRevision.Status.SUPERSEDED,
            current_marker=None,
        )
        r1_course = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="PRINT-R1-CURRENT",
        )
        r1 = self._make_revision(r1_course, revision_number=1)

        response = self._bulk_page()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["bulk_selection_row_count"], 1)
        self.assertEqual(
            [row["revision"].id for row in response.context["bulk_selection_rows"]],
            [r1.id],
        )
        self.assertContains(
            response,
            'aria-label="1 bulk print release record">1</span>',
            html=False,
        )

    def test_bulk_list_shows_only_r2_when_r1_is_superseded(self):
        ExamGenerationRevision.objects.filter(pk=self.r2.pk).update(
            status=ExamGenerationRevision.Status.SUPERSEDED,
            current_marker=None,
        )
        course = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="PRINT-HISTORY",
        )
        r1 = self._make_revision(course, revision_number=1)
        r2 = self._newer_revision(course, r1)

        response = self._bulk_page()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["bulk_selection_row_count"], 1)
        self.assertEqual(
            [row["revision"].id for row in response.context["bulk_selection_rows"]],
            [r2.id],
        )
        self.assertTrue(
            ExamGenerationRevision.objects.filter(
                pk=r1.id,
                status=ExamGenerationRevision.Status.SUPERSEDED,
                current_marker__isnull=True,
            ).exists()
        )

    def test_bulk_list_has_exactly_one_current_revision_per_course(self):
        second_course, second_r1 = self._second_bulk_target()
        second_current = self._newer_revision(second_course, second_r1)
        third_course = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="PRINT-THIRD",
        )
        third_current = self._make_revision(third_course, revision_number=1)

        response = self._bulk_page()

        rows = response.context["bulk_selection_rows"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["bulk_selection_row_count"], 3)
        self.assertEqual(
            {row["course"].id: row["revision"].id for row in rows},
            {
                self.parent.id: self.r2.id,
                second_course.id: second_current.id,
                third_course.id: third_current.id,
            },
        )

    def test_bulk_list_does_not_fall_back_when_no_current_revision_exists(self):
        ExamGenerationRevision.objects.filter(pk=self.r2.pk).update(
            status=ExamGenerationRevision.Status.SUPERSEDED,
            current_marker=None,
        )

        response = self._bulk_page()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["bulk_selection_rows"], [])
        self.assertEqual(response.context["bulk_selection_row_count"], 0)
        self.assertContains(
            response,
            'aria-label="0 bulk print release records">0</span>',
            html=False,
        )
        self.assertContains(
            response,
            "No current Generated revisions are available for bulk release.",
        )

    def test_bulk_select_all_and_selected_count_dom_contract(self):
        response = self._bulk_page()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="bulk-print-release-form"', html=False)
        self.assertContains(response, 'id="bulk-select-all"', html=False)
        self.assertContains(
            response,
            'name="selections" value="'
            f"{self.parent.id}:{self.r2.id}"
            '"',
            html=False,
        )
        self.assertContains(
            response,
            'class="form-check-input bulk-release-selection"',
            html=False,
        )
        self.assertContains(response, 'id="bulk-selected-count"', html=False)
        self.assertContains(
            response,
            'data-release-ajax="true"',
            html=False,
        )
        self.assertContains(
            response,
            'data-release-section="questionnaire-releases"',
            html=False,
        )
        self.assertContains(
            response,
            'data-release-action="bulk_release"',
            html=False,
        )
        self.assertContains(
            response,
            "js/departmental_exam_release_center.js",
            html=False,
        )
        self.assertNotContains(
            response,
            "The batch is all-or-nothing. Any invalid, unauthorized, cross-tenant, or incomplete selection prevents every release in this submission.",
        )

    def test_bulk_list_equivalency_row_is_primary_owned_and_requires_all_campuses(self):
        cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            scope_suffix="BULK-EQ",
        )
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        primary = self.make_course(cycle=cycle, code="PRINT-EQ-P")
        secondary = self.make_course(cycle=cycle, code="PRINT-EQ-S")
        deadline = self.future_deadline()
        opened_at = timezone.now() - timezone.timedelta(days=2)
        for member in (primary, secondary):
            self.make_configuration(
                member,
                workflow=CourseExamConfiguration.WorkflowStatus.CLOSED,
                opened_at=opened_at,
                deadline=deadline,
            )
        program = Program.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            code="PRINT-EQ-NORTH-P",
            name="Print Equivalency North Program",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=program,
            code="PRINT-EQ-NORTH-S",
            name="Print Equivalency North Section",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=program,
            academic_year=cycle.academic_year,
            term=cycle.term,
            course=secondary.course,
            section=section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=secondary,
            offering=offering,
            campus=self.other_campus,
        )
        ExamCourseEquivalencyService.create_group(
            cycle_id=cycle.id,
            name="Bulk Print Equivalency",
            primary_cycle_course_id=primary.id,
            member_ids=(primary.id, secondary.id),
            actor=self.admin,
        )
        revision = self._make_revision(primary, revision_number=1)
        secondary_revision = self._make_revision(secondary, revision_number=1)
        permission = Permission.objects.get(
            code="departmental_exams.manage_exam_generation"
        )
        north_allow = UserPermission.objects.create(
            user=self.manager_user,
            permission=permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.other_campus,
        )

        response = self._bulk_page()

        group_rows = [
            row
            for row in response.context["bulk_selection_rows"]
            if row["course"].id in (primary.id, secondary.id)
        ]
        self.assertEqual(
            [(row["course"].id, row["revision"].id) for row in group_rows],
            [(primary.id, revision.id)],
        )

        print_from, print_until = self._bulk_window()
        with self.assertRaisesRegex(
            ValidationError,
            "primary-owned revision for an examination unit",
        ):
            QuestionnairePrintReleaseService.bulk_release(
                selections=((secondary.id, secondary_revision.id),),
                tenant_id=self.tenant.id,
                actor=self.manager_user,
                print_from=print_from,
                print_until=print_until,
            )

        north_allow.delete()
        UserPermission.objects.create(
            user=self.manager_user,
            permission=permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.other_campus,
        )
        with self.assertRaises(PermissionDenied):
            QuestionnairePrintReleaseService.bulk_release(
                selections=((primary.id, revision.id),),
                tenant_id=self.tenant.id,
                actor=self.manager_user,
                print_from=print_from,
                print_until=print_until,
            )
        self.assertFalse(
            QuestionnairePrintRelease.objects.filter(cycle_course=primary).exists()
        )

    def test_bulk_release_authorized_multiple_revisions(self):
        second_parent, second_revision = self._second_bulk_target()

        response = self._bulk_post(
            ((self.parent, self.r2), (second_parent, second_revision))
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            QuestionnairePrintRelease.objects.filter(
                status=QuestionnairePrintRelease.Status.ACTIVE,
                active_marker=1,
            ).count(),
            2,
        )

    def test_forged_bulk_post_rejects_superseded_revision(self):
        superseded = self.r2
        self._newer_revision(self.parent, superseded)

        response = self._bulk_post(((self.parent, superseded),))

        self.assertEqual(response.status_code, 400)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_direct_bulk_release_rejects_superseded_revision_and_rolls_back(self):
        second_parent, superseded = self._second_bulk_target()
        self._newer_revision(second_parent, superseded)
        print_from, print_until = self._bulk_window()

        with self.assertRaisesRegex(
            ValidationError,
            "Bulk print release accepts only the current Generated revision.",
        ):
            QuestionnairePrintReleaseService.bulk_release(
                selections=(
                    (self.parent.id, self.r2.id),
                    (second_parent.id, superseded.id),
                ),
                tenant_id=self.tenant.id,
                actor=self.manager_user,
                print_from=print_from,
                print_until=print_until,
            )

        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        self.assertFalse(
            AuditLog.objects.filter(
                action="DE_QUESTIONNAIRE_PRINT_RELEASED"
            ).exists()
        )

    def test_bulk_current_only_does_not_change_individual_historical_release(self):
        superseded = self.r2
        self._newer_revision(self.parent, superseded)

        release = self._release(revision=superseded)

        self.assertEqual(release.generation_revision_id, superseded.id)
        self.assertEqual(release.status, QuestionnairePrintRelease.Status.ACTIVE)

    def test_bulk_release_applies_same_window_to_each_record(self):
        second_parent, second_revision = self._second_bulk_target()
        print_from, print_until = self._bulk_window()

        response = self._bulk_post(
            ((self.parent, self.r2), (second_parent, second_revision)),
            print_from=print_from,
            print_until=print_until,
        )

        self.assertEqual(response.status_code, 302)
        releases = list(
            QuestionnairePrintRelease.objects.filter(
                status=QuestionnairePrintRelease.Status.ACTIVE,
                active_marker=1,
            ).order_by("cycle_course_id")
        )
        self.assertEqual({release.print_from for release in releases}, {print_from})
        self.assertEqual({release.print_until for release in releases}, {print_until})

    def test_bulk_release_records_remain_independently_revision_bound(self):
        second_parent, second_revision = self._second_bulk_target()

        self._bulk_post(
            ((self.parent, self.r2), (second_parent, second_revision))
        )

        self.assertEqual(
            QuestionnairePrintRelease.objects.get(
                cycle_course=self.parent,
                status=QuestionnairePrintRelease.Status.ACTIVE,
            ).generation_revision_id,
            self.r2.id,
        )
        self.assertEqual(
            QuestionnairePrintRelease.objects.get(
                cycle_course=second_parent,
                status=QuestionnairePrintRelease.Status.ACTIVE,
            ).generation_revision_id,
            second_revision.id,
        )

    def test_bulk_release_replaces_active_release_and_preserves_history(self):
        previous = self._release()
        newer = self._newer_revision(self.parent, self.r2)

        response = self._bulk_post(((self.parent, newer),))

        self.assertEqual(response.status_code, 302)
        previous.refresh_from_db()
        self.assertEqual(previous.status, QuestionnairePrintRelease.Status.REVOKED)
        self.assertIsNone(previous.active_marker)
        active = QuestionnairePrintRelease.objects.get(
            cycle_course=self.parent,
            status=QuestionnairePrintRelease.Status.ACTIVE,
            active_marker=1,
        )
        self.assertEqual(active.generation_revision_id, newer.id)
        self.assertEqual(
            QuestionnairePrintRelease.objects.filter(cycle_course=self.parent).count(),
            2,
        )

    def test_bulk_release_invalid_item_rolls_back_entire_batch(self):
        second_parent, _second_revision = self._second_bulk_target()
        print_from, print_until = self._bulk_window()

        with self.assertRaises(ValidationError):
            QuestionnairePrintReleaseService.bulk_release(
                selections=(
                    (self.parent.id, self.r2.id),
                    (second_parent.id, self.r2.id),
                ),
                tenant_id=self.tenant.id,
                actor=self.manager_user,
                print_from=print_from,
                print_until=print_until,
            )

        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_bulk_release_invalid_window_is_rejected_without_writes(self):
        print_from, _print_until = self._bulk_window()

        response = self._bulk_post(
            ((self.parent, self.r2),),
            print_from=print_from,
            print_until=print_from,
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Print Until must be later than Print From.",
            status_code=400,
        )
        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_bulk_release_cross_tenant_selection_rolls_back_entire_batch(self):
        foreign_year = AcademicYear.objects.create(
            tenant=self.other_tenant,
            code="FOREIGN-AY",
            name="Foreign AY",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        foreign_term = Term.objects.create(
            tenant=self.other_tenant,
            academic_year=foreign_year,
            code="FOREIGN-T1",
            name="Foreign Term",
        )
        foreign_cycle = ExaminationCycle.objects.create(
            tenant=self.other_tenant,
            academic_year=foreign_year,
            term=foreign_term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
            created_by=self.admin,
        )
        foreign_course = Course.objects.create(
            tenant=self.other_tenant,
            code="FOREIGN-101",
            title="Foreign Course",
        )
        foreign_parent = CycleCourse.objects.create(
            cycle=foreign_cycle,
            course=foreign_course,
        )
        foreign_revision = ExamGenerationRevision.objects.create(
            cycle_course=foreign_parent,
            revision_number=1,
            source_input_fingerprint="f" * 64,
            algorithm_version="foreign-test-v1",
            generation_trigger=ExamGenerationRevision.GenerationTrigger.AUTOMATIC,
            configuration_revision_snapshot=1,
            blueprint_revision_snapshot=1,
            roster_boundary_snapshot="r" * 64,
            final_item_count_snapshot=2,
            request_token_digest="d" * 64,
            minimum_overlap=0,
            proportional_score=0,
            contributors_represented=0,
            squared_contributor_concentration=0,
        )
        print_from, print_until = self._bulk_window()

        with self.assertRaises(Http404):
            QuestionnairePrintReleaseService.bulk_release(
                selections=(
                    (self.parent.id, self.r2.id),
                    (foreign_parent.id, foreign_revision.id),
                ),
                tenant_id=self.tenant.id,
                actor=self.manager_user,
                print_from=print_from,
                print_until=print_until,
            )

        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_bulk_release_direct_deny_and_unauthorized_users_are_blocked(self):
        print_from, print_until = self._bulk_window()
        UserPermission.objects.create(
            user=self.manager_user,
            permission=Permission.objects.get(
                code="departmental_exams.manage_exam_generation"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )

        with self.assertRaises(PermissionDenied):
            QuestionnairePrintReleaseService.bulk_release(
                selections=((self.parent.id, self.r2.id),),
                tenant_id=self.tenant.id,
                actor=self.manager_user,
                print_from=print_from,
                print_until=print_until,
            )
        self.assertEqual(
            self._bulk_post(
                ((self.parent, self.r2),),
                print_from=print_from,
                print_until=print_until,
                user=self.configurer,
            ).status_code,
            403,
        )
        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_bulk_release_does_not_auto_release_regenerated_revision(self):
        self._bulk_post(((self.parent, self.r2),))
        newer = self._newer_revision(self.parent, self.r2)

        active = QuestionnairePrintRelease.objects.get(
            cycle_course=self.parent,
            status=QuestionnairePrintRelease.Status.ACTIVE,
            active_marker=1,
        )

        self.assertEqual(active.generation_revision_id, self.r2.id)
        self.assertFalse(
            QuestionnairePrintRelease.objects.filter(
                cycle_course=self.parent,
                generation_revision=newer,
            ).exists()
        )

    def test_admin_direct_print_preserves_requested_historical_revision(self):
        ExamGenerationRevision.objects.filter(pk=self.r2.pk).update(
            status=ExamGenerationRevision.Status.SUPERSEDED,
            current_marker=None,
        )
        r3 = self._make_revision(
            self.parent,
            revision_number=3,
            supersedes=self.r2,
        )
        client = Client()
        client.force_login(self.manager_user)

        response = client.get(self._admin_print_url(revision=self.r2, set_code="A"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["revision_number"], 2)
        self.assertContains(response, "Revision R2")
        self.assertNotEqual(response.context["revision_number"], r3.revision_number)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_admin_direct_print_permission_and_direct_deny_fail_closed(self):
        print_user = self.make_user(
            "questionnaire-admin-printer",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.print_generated_exams",
            ),
        )
        client = Client()
        client.force_login(print_user)
        self.assertEqual(client.get(self._admin_print_url()).status_code, 200)
        self.assertEqual(
            client.get(reverse("departmental_exams:questionnaire_print_release")).status_code,
            200,
        )
        UserPermission.objects.create(
            user=print_user,
            permission=Permission.objects.get(
                code="departmental_exams.print_generated_exams"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(client.get(self._admin_print_url()).status_code, 403)
        self.assertEqual(
            client.get(reverse("departmental_exams:questionnaire_print_release")).status_code,
            403,
        )
        unauthorized = Client()
        unauthorized.force_login(self.configurer)
        self.assertEqual(unauthorized.get(self._admin_print_url()).status_code, 403)

    def test_authorized_admin_releases_exact_revision_and_records_safe_audit(self):
        client = Client()
        client.force_login(self.manager_user)
        release_url = reverse("departmental_exams:questionnaire_print_release")
        initial_page = client.get(release_url)
        self.assertEqual(initial_page.status_code, 200)
        self.assertContains(initial_page, "Questionnaire Print Release")
        now = timezone.localtime().replace(second=0, microsecond=0)
        response = client.post(
            release_url,
            {
                "action": "release",
                "cycle_course_id": self.parent.id,
                "generation_revision": self.r2.id,
                "print_from": now.strftime("%Y-%m-%dT%H:%M"),
                "print_until": (now + timezone.timedelta(hours=3)).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
            },
        )

        self.assertRedirects(
            response,
            release_url,
        )
        release = QuestionnairePrintRelease.objects.get()
        self.assertEqual(release.generation_revision, self.r2)
        self.assertEqual(release.cycle_course, self.parent)
        audit = AuditLog.objects.get(action="DE_QUESTIONNAIRE_PRINT_RELEASED")
        self.assertEqual(audit.metadata_json["revision_id"], self.r2.id)
        self.assertNotIn("question", str(audit.metadata_json).lower())
        self.assertNotIn("choice", str(audit.metadata_json).lower())
        self.assertNotIn("answer", str(audit.metadata_json).lower())

    def test_wrong_tenant_course_revision_and_invalid_window_are_rejected(self):
        other_parent = self.make_course(cycle=self.parent.cycle, department=None, code="PRINT-OTHER")
        other_revision = self._make_revision(
            other_parent,
            revision_number=1,
            with_sets=False,
        )
        now = timezone.now()
        with self.assertRaises(Http404):
            QuestionnairePrintReleaseService.release(
                cycle_course_id=self.parent.id,
                revision_id=self.r2.id,
                tenant_id=self.other_tenant.id,
                actor=self.manager_user,
                print_from=now,
                print_until=now + timezone.timedelta(hours=1),
            )
        with self.assertRaisesRegex(ValidationError, "does not belong"):
            QuestionnairePrintReleaseService.release(
                cycle_course_id=self.parent.id,
                revision_id=other_revision.id,
                tenant_id=self.tenant.id,
                actor=self.manager_user,
                print_from=now,
                print_until=now + timezone.timedelta(hours=1),
            )
        with self.assertRaisesRegex(ValidationError, "later than"):
            self._release(print_from=now, print_until=now)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_regenerated_r3_is_not_substituted_and_explicit_release_replaces_r2(self):
        r2_release = self._release()
        self.r2.status = ExamGenerationRevision.Status.SUPERSEDED
        self.r2.current_marker = None
        self.r2.save(update_fields=["status", "current_marker", "updated_at"])
        r3 = self._make_revision(
            self.parent,
            revision_number=3,
            supersedes=self.r2,
        )

        list_response = self._faculty_client().get(
            reverse("departmental_exams:contribution_list")
        )
        self.assertContains(list_response, "Released R2")
        self.assertNotContains(list_response, "Released R3")
        self.assertEqual(
            QuestionnairePrintRelease.objects.get(status="ACTIVE").generation_revision,
            self.r2,
        )
        admin_client = Client()
        admin_client.force_login(self.manager_user)
        admin_page = admin_client.get(
            reverse("departmental_exams:questionnaire_print_release")
        )
        self.assertContains(admin_page, "A newer generated revision exists.")
        self.assertContains(
            admin_page,
            "It is not printable until it receives its own explicit release.",
        )

        r3_release = self._release(revision=r3)
        r2_release.refresh_from_db()
        self.assertEqual(r2_release.status, QuestionnairePrintRelease.Status.REVOKED)
        self.assertIsNone(r2_release.active_marker)
        self.assertEqual(r3_release.generation_revision, r3)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DE_QUESTIONNAIRE_PRINT_RELEASE_REVOKED",
                entity_id=str(r2_release.id),
            ).exists()
        )
        self.assertContains(
            self._faculty_client().get(reverse("departmental_exams:contribution_list")),
            "Released R3",
        )

    def test_assigned_faculty_sees_both_print_actions_inside_active_window(self):
        release = self._release()
        response = self._faculty_client().get(
            reverse("departmental_exams:contribution_list")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self._print_url(release, "A"))
        self.assertContains(response, self._print_url(release, "B"))
        self.assertContains(response, "Questionnaire")
        self.assertContains(response, "Personalized Answer Sheets")

    def test_unrelated_faculty_cannot_see_or_access_print_output(self):
        release = self._release()
        unrelated = self.make_user(
            "unrelated-questionnaire-faculty",
            self.department,
            ("faculty_portal.access",),
        )
        client = self._faculty_client(unrelated)
        list_response = client.get(reverse("departmental_exams:contribution_list"))
        self.assertNotContains(list_response, "Print Set A", status_code=403)
        self.assertEqual(client.get(self._print_url(release)).status_code, 404)

    def test_before_and_after_window_hide_buttons_and_direct_url_denies(self):
        now = timezone.now()
        scheduled = self._release(
            print_from=now + timezone.timedelta(hours=1),
            print_until=now + timezone.timedelta(hours=2),
        )
        client = self._faculty_client()
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "Print Set A",
        )
        self.assertEqual(client.get(self._print_url(scheduled)).status_code, 403)

        expired = self._release(
            print_from=now - timezone.timedelta(hours=2),
            print_until=now - timezone.timedelta(hours=1),
        )
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "Print Set A",
        )
        self.assertEqual(client.get(self._print_url(expired)).status_code, 403)

    def test_set_a_and_b_are_sanitized_no_store_and_audited(self):
        release = self._release()
        client = self._faculty_client()
        for set_code in ("A", "B"):
            with self.subTest(set_code=set_code):
                response = client.get(self._print_url(release, set_code))
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-store", response["Cache-Control"])
                self.assertContains(
                    response,
                    (
                        '<span class="running-course-code">'
                        f"{self.parent.course.code}</span>"
                    ),
                    html=True,
                )
                self.assertContains(
                    response,
                    (
                        '<span class="running-course-title">'
                        f"{self.parent.course.title}</span>"
                    ),
                    html=True,
                )
                self.assertContains(response, self.PRINT_SCHOOL_NAME)
                self.assertContains(response, self.PRINT_CAMPUS_LINE)
                self.assertContains(response, self.parent.cycle.term.name)
                self.assertContains(response, self.parent.cycle.academic_year.name)
                self.assertContains(
                    response,
                    self.parent.cycle.get_exam_period_display(),
                )
                self.assertContains(response, "DEPARTMENTAL EXAMINATIONS")
                self.assertContains(response, self.parent.course.title)
                self.assertContains(response, self.parent.course.code)
                self.assertContains(response, f"SET {set_code}")
                self.assertContains(response, "shade the circle on the answer sheet")
                self.assertContains(response, "STRICTLY NO ERASURES ALLOWED")
                self.assertContains(response, "Pencil No. 2")
                self.assertContains(response, f"Released {set_code} question 1")
                self.assertContains(response, f"{set_code} choice A1")
                self.assertContains(
                    response,
                    (
                        '<div class="question-line"><span>1.</span><span data-scientific-content>'
                        f"Released {set_code} question 1</span></div>"
                    ),
                    html=True,
                )
                body = response.content.decode()
                for forbidden in (
                    "correct_answer_snapshot",
                    "difficulty_snapshot",
                    "Private provenance campus",
                    "PRIVATE-CAMPUS",
                    "CONFIDENTIAL CONTRIBUTOR",
                    "SECRET-DIGEST",
                    "source_question",
                    "source_contributor",
                    "source_campus",
                    "contribution_id",
                    "campus_quotas_snapshot",
                    "fingerprint",
                    "HMAC",
                    "automatic-print-test-v1",
                    "Correct answer:",
                    "answer key",
                    "revision history",
                ):
                    self.assertNotIn(forbidden, body)
        audits = AuditLog.objects.filter(
            action="DE_QUESTIONNAIRE_PRINT_SET_ACCESSED"
        ).order_by("id")
        self.assertEqual(audits.count(), 2)
        self.assertEqual(
            [audit.metadata_json["set_code"] for audit in audits],
            ["A", "B"],
        )
        for audit in audits:
            metadata = str(audit.metadata_json).lower()
            self.assertNotIn("question", metadata)
            self.assertNotIn("choice", metadata)
            self.assertNotIn("answer", metadata)

    def test_lost_current_assignment_or_direct_deny_fails_closed(self):
        release = self._release()
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        client = self._faculty_client()
        self.assertNotContains(
            client.get(reverse("departmental_exams:contribution_list")),
            "Print Set A",
        )
        self.assertEqual(client.get(self._print_url(release)).status_code, 403)

        self.assignment.is_active = True
        self.assignment.save(update_fields=["is_active", "updated_at"])
        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code="faculty_portal.access"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(client.get(self._print_url(release)).status_code, 403)

    def test_summary_displays_actual_persisted_set_and_difficulty_counts(self):
        summary = AutomaticGenerationSummaryService.build(cycle=self.parent.cycle)
        generated = summary["generated"][0]
        self.assertEqual(
            generated["actual_set_counts"],
            (
                {
                    "set_code": "A",
                    "total": 2,
                    "campuses": (
                        {
                            "campus_code": "PRIVATE-CAMPUS",
                            "campus_name": "Private provenance campus",
                            "total": 2,
                            "easy": 1,
                            "moderate": 1,
                            "difficult": 0,
                        },
                    ),
                },
                {
                    "set_code": "B",
                    "total": 2,
                    "campuses": (
                        {
                            "campus_code": "PRIVATE-CAMPUS",
                            "campus_name": "Private provenance campus",
                            "total": 2,
                            "easy": 1,
                            "moderate": 1,
                            "difficult": 0,
                        },
                    ),
                },
            ),
        )
        response = Client()
        response.force_login(self.manager_user)
        page = response.get(
            reverse(
                "departmental_exams:automatic_generation_summary",
                args=[self.parent.cycle_id],
            )
        )
        self.assertContains(page, "Set A — 2 actual items")
        self.assertContains(page, "Set B — 2 actual items")
        self.assertContains(
            page,
            "Easy 1 &middot; Moderate 1 &middot; Difficult 0",
            html=False,
        )

    def test_summary_waiting_deadline_and_draft_wording(self):
        waiting_parent = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="PRINT-WAIT",
        )
        deadline = timezone.now().astimezone(MANILA).replace(
            hour=9,
            minute=30,
            second=0,
            microsecond=0,
        ) + timezone.timedelta(days=3)
        self.make_configuration(
            waiting_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.OPEN,
            opened_at=timezone.now(),
            deadline=deadline,
        )
        draft_parent = self.make_course(
            cycle=self.parent.cycle,
            department=None,
            code="PRINT-DRAFT",
        )
        self.make_configuration(
            draft_parent,
            workflow=CourseExamConfiguration.WorkflowStatus.DRAFT,
        )
        client = Client()
        client.force_login(self.manager_user)
        response = client.get(
            reverse(
                "departmental_exams:automatic_generation_summary",
                args=[self.parent.cycle_id],
            )
        )
        self.assertContains(response, "Contribution deadline has not arrived yet.")
        self.assertContains(
            response,
            f"Deadline:</strong> {deadline.strftime('%b')} {deadline.day}, {deadline.year} 9:30 AM",
            html=False,
        )
        self.assertContains(
            response,
            "Automatic generation will run after the deadline.",
        )
        self.assertContains(response, "Course setup is not yet complete.")
        self.assertContains(
            response,
            "Complete the course configuration and open contributions before automatic generation can proceed.",
        )
