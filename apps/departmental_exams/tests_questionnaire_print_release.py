from unittest.mock import patch
from inspect import unwrap
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.http import Http404
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.exceptions import IrreversibleError
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
from apps.rbac.models import Permission, UserPermission, UserRole
from apps.tenants.models import Program

from .automatic_workflow import AutomaticGenerationSummaryService
from .automatic_generation_audit import AutomaticGenerationAuditService
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
    QuestionnaireLegacyCampusCoverage,
)
from .questionnaire_printing import (
    QuestionnairePrintReleaseService,
    FacultyQuestionnairePrintService,
    _questionnaire_exam_heading,
)
from .release_review import SIGNING_SALT, make_review, confirm_review
from .stage6_views import questionnaire_print_release_view
from .stage4_test_support import Stage4TestCase, Stage4TransactionTestCase
from .setup_services import CourseSetupService


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
        CourseSetupService.classify(
            course_id=self.parent.pk,
            tenant_id=self.tenant.pk,
            actor=self.manager_user,
            classification=CycleCourse.ExamClassification.STANDARDIZED,
            expected_state=CourseSetupService.fingerprint(self.parent),
        )
        self.parent.refresh_from_db()
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
            target_campus_id=self.campus.id,
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
        review = client.post(
            reverse("departmental_exams:questionnaire_print_release"),
            {
                "action": "bulk_release",
                "review": "1",
                "target_campus_id": self.campus.id,
                "selections": [
                    f"{course.id}:{revision.id}"
                    for course, revision in selections
                ],
                "print_from": print_from.strftime("%Y-%m-%dT%H:%M"),
                "print_until": print_until.strftime("%Y-%m-%dT%H:%M"),
            },
        )
        if review.status_code != 200 or not review.context or not review.context.get("review_token"):
            return review
        return client.post(review.context["review_post_url"], {
            "action": "bulk_release", "target_campus_id": self.campus.id,
            "review_token": review.context["review_token"],
        })

    def _bulk_page(self):
        client = Client()
        client.force_login(self.manager_user)
        return client.get(reverse("departmental_exams:questionnaire_print_release"),
                          {"target_campus_id": self.campus.id})

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
        self.assertContains(page, "View details")
        self.assertNotContains(page, "Print Set A")
        details = client.get(
            reverse("departmental_exams:questionnaire_print_release_details", args=[self.parent.id])
        )
        self.assertContains(details, "Print Set A")
        self.assertContains(details, "Print Set B")

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
                    ".questionnaire-ending { break-inside: avoid-page; page-break-inside: avoid; }",
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
                body = response.content.decode()
                self.assertEqual(body.count('class="confidential-footer"'), 1)
                self.assertLess(
                    body.rfind('class="question"'),
                    body.find('class="confidential-footer"'),
                )

    def test_questionnaire_heading_uses_confirmed_classification_and_neutral_legacy(self):
        self.assertEqual(
            _questionnaire_exam_heading(CycleCourse.ExamClassification.STANDARDIZED),
            "STANDARDIZED EXAMINATIONS",
        )
        self.assertEqual(
            _questionnaire_exam_heading(CycleCourse.ExamClassification.DEPARTMENTAL),
            "DEPARTMENTAL EXAMINATIONS",
        )
        self.assertEqual(
            _questionnaire_exam_heading(CycleCourse.ExamClassification.UNCLASSIFIED_LEGACY),
            "EXAMINATIONS",
        )
        self.assertEqual(_questionnaire_exam_heading("UNKNOWN"), "EXAMINATIONS")

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
        details = client.get(
            reverse("departmental_exams:questionnaire_print_release_details", args=[self.parent.id])
        )
        self.assertEqual(details.status_code, 200)
        content = response.content.decode()
        answer_key_pane = content.split('id="answer-key-releases-pane"', 1)[1]
        campus_header = f"&middot; {self.campus.name}</div>"
        # The on-demand Questionnaire detail keeps one campus despite repeated offerings.
        self.assertEqual(details.content.decode().count(campus_header), 1)
        self.assertEqual(answer_key_pane.count(campus_header), 0)
        self.assertContains(response, "Target Campus (required)")

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
        self.assertContains(response, "View details")
        client = Client()
        client.force_login(self.manager_user)
        details = client.get(
            reverse("departmental_exams:questionnaire_print_release_details", args=[self.parent.id])
        )
        self.assertContains(details, "R2")
        self.assertContains(details, "Print Set A")

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
                selections=((secondary.id, secondary_revision.id, self.campus.id),),
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
                selections=((primary.id, revision.id, self.campus.id),),
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

    def test_review_is_read_only_and_final_post_revalidates_exact_targets(self):
        second_parent, second_revision = self._second_bulk_target()
        print_from, print_until = self._bulk_window()
        payload = {
            "action": "bulk_release",
            "review": "1",
            "target_campus_id": self.campus.id,
            "selections": [
                f"{self.parent.id}:{self.r2.id}",
                f"{second_parent.id}:{second_revision.id}",
            ],
            "print_from": print_from.strftime("%Y-%m-%dT%H:%M"),
            "print_until": print_until.strftime("%Y-%m-%dT%H:%M"),
        }
        client = Client()
        client.force_login(self.manager_user)
        url = reverse("departmental_exams:questionnaire_print_release")
        review = client.post(url, payload)
        self.assertEqual(review.status_code, 200)
        self.assertContains(review, "Review all 2 selected exact targets")
        self.assertEqual(len(review.context["review_rows"]), 2)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action="DE_QUESTIONNAIRE_PRINT_RELEASED").exists())

        payload.pop("review")
        payload["review_token"] = review.context["review_token"]
        self._newer_revision(second_parent, second_revision)
        stale = client.post(url, payload)
        self.assertEqual(stale.status_code, 400)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        payload["selections"][1] = f"{self.parent.id}:{second_revision.id}"
        tampered = client.post(url, payload)
        self.assertEqual(tampered.status_code, 400)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_closed_cycle_review_confirmation_returns_to_questionnaire_tab(self):
        cycle = self.parent.cycle
        cycle.status = ExaminationCycle.Status.CLOSED
        cycle.save(update_fields=["status", "updated_at"])
        client = Client()
        client.force_login(self.manager_user)
        center = reverse("departmental_exams:questionnaire_print_release")
        context_url = f"{center}?cycle_status=CLOSED&section=questionnaire-releases"
        page = client.get(context_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'action="{context_url.replace("&", "&amp;")}#questionnaire-releases-pane"', html=False)
        detail_url = reverse(
            "departmental_exams:questionnaire_print_release_details",
            args=[self.parent.id],
        )
        self.assertContains(page, f'{detail_url}?cycle_status=CLOSED&amp;section=questionnaire-releases', html=False)
        detail = client.get(detail_url + "?cycle_status=CLOSED")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "cycle_status=CLOSED")
        self.assertContains(
            detail,
            f'action="{context_url.replace("&", "&amp;")}#questionnaire-releases-pane"',
            html=False,
        )

        start, end = self._bulk_window()
        payload = {
            "action": "bulk_release", "review": "1",
            "target_campus_id": self.campus.id,
            "selections": [f"{self.parent.id}:{self.r2.id}"],
            "print_from": start.strftime("%Y-%m-%dT%H:%M"),
            "print_until": end.strftime("%Y-%m-%dT%H:%M"),
        }
        review = client.post(context_url, payload)
        self.assertEqual(review.status_code, 200)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        final_url = review.context["review_post_url"]
        self.assertIn("cycle_status=CLOSED", final_url)
        self.assertIn("section=questionnaire-releases", final_url)
        self.assertContains(review, final_url.replace("&", "&amp;"), html=False)
        payload.pop("review")
        payload["review_token"] = review.context["review_token"]
        confirmed = client.post(final_url, payload)
        self.assertEqual(confirmed.status_code, 302)
        self.assertIn("cycle_status=CLOSED", confirmed["Location"])
        self.assertIn("section=questionnaire-releases", confirmed["Location"])
        self.assertEqual(QuestionnairePrintRelease.objects.count(), 1)
        returned = client.get(confirmed["Location"])
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(returned.context["current_cycle_status"], "CLOSED")
        self.assertEqual(returned.context["initial_release_section"], "questionnaire-releases")

    def test_details_get_is_authorized_on_demand_and_main_get_skips_audit_history(self):
        client = Client()
        client.force_login(self.manager_user)
        url = reverse("departmental_exams:questionnaire_print_release")
        with patch.object(AutomaticGenerationAuditService, "run", side_effect=AssertionError("audit ran")):
            with CaptureQueriesContext(connection) as queries:
                main = client.get(url)
            detail = client.get(
                reverse("departmental_exams:questionnaire_print_release_details", args=[self.parent.id]),
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(main.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(main, "Print Set A")
        self.assertContains(detail, "Print Set A")
        self.assertIn("no-store", detail["Cache-Control"])
        self.assertFalse(any("automaticgenerationauditrun" in query["sql"].lower() for query in queries))
        client.force_login(self.faculty)
        denied = client.get(
            reverse("departmental_exams:questionnaire_print_release_details", args=[self.parent.id])
        )
        self.assertEqual(denied.status_code, 403)

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
                    (self.parent.id, self.r2.id, self.campus.id),
                    (second_parent.id, superseded.id, self.campus.id),
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

    def test_automatic_individual_release_rejects_historical_revision(self):
        superseded = self.r2
        self._newer_revision(self.parent, superseded)

        with self.assertRaises(ValidationError):
            self._release(revision=superseded)

        self.assertFalse(QuestionnairePrintRelease.objects.exists())

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
                    (self.parent.id, self.r2.id, self.campus.id),
                    (second_parent.id, self.r2.id, self.campus.id),
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
        client = Client()
        client.force_login(self.manager_user)
        foreign_details = client.get(
            reverse("departmental_exams:questionnaire_print_release_details", args=[foreign_parent.id])
        )
        self.assertEqual(foreign_details.status_code, 403)
        self.assertNotContains(foreign_details, "FOREIGN-101", status_code=403)
        forged_post = self._bulk_post(((self.parent, self.r2), (foreign_parent, foreign_revision)))
        self.assertEqual(forged_post.status_code, 400)

        with self.assertRaises(Http404):
            QuestionnairePrintReleaseService.bulk_release(
                selections=(
                    (self.parent.id, self.r2.id, self.campus.id),
                    (foreign_parent.id, foreign_revision.id, self.campus.id),
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
                selections=((self.parent.id, self.r2.id, self.campus.id),),
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
        self.assertContains(initial_page, "Release Exam for Printing")
        now = timezone.localtime().replace(second=0, microsecond=0)
        review = client.post(
            release_url,
            {
                "action": "release",
                "review": "1",
                "cycle_course_id": self.parent.id,
                "generation_revision": self.r2.id,
                "target_campus_id": self.campus.id,
                "print_from": now.strftime("%Y-%m-%dT%H:%M"),
                "print_until": (now + timezone.timedelta(hours=3)).strftime(
                    "%Y-%m-%dT%H:%M"
                ),
            },
        )
        self.assertEqual(review.status_code, 200)
        response = client.post(review.context["review_post_url"], {
            "action": "bulk_release",
            "target_campus_id": self.campus.id,
            "review_token": review.context["review_token"],
        })
        self.assertRedirects(
            response,
            f"{release_url}?cycle_status=OPEN&section=questionnaire-releases&target_campus_id={self.campus.id}#questionnaire-releases-pane",
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
                target_campus_id=self.campus.id,
                tenant_id=self.other_tenant.id,
                actor=self.manager_user,
                print_from=now,
                print_until=now + timezone.timedelta(hours=1),
            )
        with self.assertRaisesRegex(ValidationError, "does not belong"):
            QuestionnairePrintReleaseService.release(
                cycle_course_id=self.parent.id,
                revision_id=other_revision.id,
                target_campus_id=self.campus.id,
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
        detail_url = reverse("departmental_exams:questionnaire_print_release_details", args=[self.parent.id])
        admin_page = admin_client.get(detail_url, {"target_campus_id": self.campus.id})
        self.assertContains(admin_page, "A newer generated revision exists.")
        self.assertContains(
            admin_page,
            "It is not printable until it receives its own explicit release.",
        )
        self.assertContains(admin_page, "Release history (1)")
        self.assertContains(admin_page, "Revoke this campus")
        self.assertContains(admin_page, "Run Automatic Audit")
        self.assertContains(admin_page, "Print Set A")
        self.assertContains(admin_page, "Print Set B")

        r3_release = self._release(revision=r3)
        r2_release.refresh_from_db()
        self.assertEqual(r2_release.status, QuestionnairePrintRelease.Status.REVOKED)
        self.assertIsNone(r2_release.active_marker)
        self.assertEqual(r3_release.generation_revision, r3)
        refreshed_details = admin_client.get(detail_url, {"target_campus_id": self.campus.id})
        self.assertContains(refreshed_details, "Release history (2)")
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
        scheduled_page = client.get(reverse("departmental_exams:contribution_list"))
        self.assertNotContains(scheduled_page, "Print Set A")
        self.assertEqual(
            scheduled_page.context["contributions"][0].questionnaire_print["status"],
            "NOT_YET_AVAILABLE",
        )
        self.assertContains(scheduled_page, "Not yet available")
        self.assertEqual(client.get(self._print_url(scheduled)).status_code, 403)

        expired = self._release(
            print_from=now - timezone.timedelta(hours=2),
            print_until=now - timezone.timedelta(hours=1),
        )
        expired_page = client.get(reverse("departmental_exams:contribution_list"))
        self.assertNotContains(expired_page, "Print Set A")
        self.assertEqual(
            expired_page.context["contributions"][0].questionnaire_print["status"],
            "EXPIRED",
        )
        self.assertContains(expired_page, "Expired")
        self.assertEqual(client.get(self._print_url(expired)).status_code, 403)

        QuestionnairePrintReleaseService.revoke(
            release_id=expired.id,
            target_campus_id=self.campus.id,
            tenant_id=self.tenant.id,
            actor=self.manager_user,
        )
        revoked_page = client.get(reverse("departmental_exams:contribution_list"))
        self.assertNotContains(revoked_page, "Print Set A")
        self.assertEqual(
            revoked_page.context["contributions"][0].questionnaire_print["status"],
            "REVOKED",
        )
        self.assertContains(revoked_page, "Revoked")

    def test_print_until_boundary_is_inclusive_for_card_and_direct_access(self):
        boundary = timezone.now()
        release = self._release(
            print_from=boundary - timezone.timedelta(hours=1),
            print_until=boundary,
        )
        client = self._faculty_client()

        with patch("django.utils.timezone.now", return_value=boundary):
            page = client.get(reverse("departmental_exams:contribution_list"))
            self.assertEqual(
                page.context["contributions"][0].questionnaire_print["status"],
                "AVAILABLE",
            )
            self.assertContains(page, "Print Set A")
            self.assertEqual(client.get(self._print_url(release)).status_code, 200)

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
                self.assertContains(response, "STANDARDIZED EXAMINATIONS")
                self.assertNotContains(response, "DEPARTMENTAL EXAMINATIONS")
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
                    "difficulty": {"EASY": 1, "MODERATE": 1, "DIFFICULT": 0},
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
                    "difficulty": {"EASY": 1, "MODERATE": 1, "DIFFICULT": 0},
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

    def _add_other_campus_to_questionnaire(self):
        program = Program.objects.create(
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
            code=f"QP{self.parent.id}", name="Questionnaire program",
        )
        section = Section.objects.create(
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
            program=program, code=f"QS{self.parent.id}", name="Questionnaire section",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
            program=program, section=section, course=self.parent.course,
            academic_year=self.parent.cycle.academic_year, term=self.parent.cycle.term,
        )
        CycleCourseOffering.objects.create(
            cycle_course=self.parent, offering=offering, campus=self.other_campus,
        )
        UserRole.objects.create(
            user=self.manager_user, role=self.manager_user.user_roles.first().role,
            tenant=self.tenant, campus=self.other_campus, department=self.other_department,
        )
        return offering

    def test_campus_releases_are_independent_and_complete_unit_authority_remains(self):
        self._add_other_campus_to_questionnaire()
        now = timezone.now()
        fairview = self._release(print_from=now - timezone.timedelta(minutes=1),
                                 print_until=now + timezone.timedelta(hours=1))
        cubao = QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=self.r2.id,
            target_campus_id=self.other_campus.id, tenant_id=self.tenant.id,
            actor=self.manager_user, print_from=now - timezone.timedelta(minutes=1),
            print_until=now + timezone.timedelta(hours=3),
        )
        self.assertNotEqual(fairview.id, cubao.id)
        self.assertNotEqual(fairview.print_until, cubao.print_until)
        with self.assertRaises(PermissionDenied):
            FacultyQuestionnairePrintService._printable_release(
                contribution=self.contribution, release_id=cubao.id, set_code="A",
            )
        QuestionnairePrintReleaseService.revoke(
            release_id=fairview.id, target_campus_id=self.campus.id,
            tenant_id=self.tenant.id, actor=self.manager_user,
        )
        cubao.refresh_from_db()
        self.assertEqual(cubao.status, QuestionnairePrintRelease.Status.ACTIVE)
        deny = UserPermission.objects.create(
            user=self.manager_user,
            permission=Permission.objects.get(code="departmental_exams.manage_exam_generation"),
            tenant=self.tenant, campus=self.other_campus,
            grant_type=UserPermission.GrantType.DENY,
        )
        client = Client()
        client.force_login(self.manager_user)
        self.assertEqual(client.get(reverse(
            "departmental_exams:questionnaire_print_release_details",
            args=[self.parent.id],
        ), {"target_campus_id": self.campus.id}).status_code, 403)
        self.assertEqual(client.post(reverse(
            "departmental_exams:questionnaire_print_release"), {
                "action": "release", "review": "1", "cycle_course_id": self.parent.id,
                "generation_revision": self.r2.id,
                "target_campus_id": self.campus.id,
                "print_from": now.strftime("%Y-%m-%dT%H:%M"),
                "print_until": (now + timezone.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            }).status_code, 403)
        deny.delete()
        UserRole.objects.filter(
            user=self.manager_user, campus=self.other_campus,
        ).delete()
        with self.assertRaises(PermissionDenied):
            QuestionnairePrintReleaseService.release(
                cycle_course_id=self.parent.id, revision_id=self.r2.id,
                target_campus_id=self.campus.id, tenant_id=self.tenant.id,
                actor=self.manager_user, print_from=now,
                print_until=now + timezone.timedelta(hours=1),
            )

    def test_multi_campus_faculty_sees_both_independent_questionnaire_windows(self):
        other_offering = self._add_other_campus_to_questionnaire()
        UserRole.objects.create(
            user=self.faculty, role=self.faculty.user_roles.first().role,
            tenant=self.tenant, campus=self.other_campus,
            department=self.other_department,
        )
        other_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.other_campus, offering=other_offering,
            faculty_user=self.faculty, accepted_by=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            responded_at=timezone.now(), accepted_at=timezone.now(), is_primary=False,
        )
        FacultyContributionEligibilitySource.objects.create(
            contribution=self.contribution, assignment=other_assignment,
            assignment_id_snapshot=other_assignment.id,
            offering_id_snapshot=other_offering.id,
            tenant_id_snapshot=self.tenant.id,
            campus_id_snapshot=self.other_campus.id,
        )
        fairview = self._release()
        now = timezone.now()
        cubao = QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=self.r2.id,
            target_campus_id=self.other_campus.id, tenant_id=self.tenant.id,
            actor=self.manager_user, print_from=now - timezone.timedelta(minutes=1),
            print_until=now + timezone.timedelta(hours=1),
        )
        page = self._faculty_client().get(reverse("departmental_exams:contribution_list"))
        self.assertContains(page, self._print_url(fairview, "A"))
        self.assertContains(page, self._print_url(cubao, "A"))
        self.assertEqual(
            FacultyQuestionnairePrintService._printable_release(
                contribution=self.contribution, release_id=cubao.id, set_code="B",
            )[0].id, cubao.id,
        )
        QuestionnairePrintReleaseService.revoke(
            release_id=fairview.id, target_campus_id=self.campus.id,
            tenant_id=self.tenant.id, actor=self.manager_user,
        )
        page = self._faculty_client().get(reverse("departmental_exams:contribution_list"))
        self.assertNotContains(page, self._print_url(fairview, "A"))
        self.assertContains(page, self._print_url(cubao, "A"))

    def test_all_campuses_review_retry_and_revocation_staleness(self):
        self._add_other_campus_to_questionnaire()
        request = RequestFactory().post("/", {"target_campus_id": "0"})
        request.scope = {"campus_ids": {self.campus.id, self.other_campus.id}}
        start, end = self._bulk_window()
        token, payload = make_review(
            kind="questionnaire", bases=((self.parent.id, self.r2.id),),
            campus_id=0, tenant_id=self.tenant.id, actor=self.manager_user,
            request=request, window_from=start, window_until=end,
        )
        self.assertEqual({row[-1] for row in payload["targets"]},
                         {self.campus.id, self.other_campus.id})
        first = confirm_review(
            token=token, expected_kind="questionnaire", tenant_id=self.tenant.id,
            actor=self.manager_user, request=request,
        )
        audits_after = AuditLog.objects.count()
        retry = confirm_review(
            token=token, expected_kind="questionnaire", tenant_id=self.tenant.id,
            actor=self.manager_user, request=request,
        )
        self.assertEqual({row.id for row in first}, {row.id for row in retry})
        self.assertTrue(all(row.review_confirmation_id == payload["confirmation_id"] for row in first))
        self.assertEqual(AuditLog.objects.count(), audits_after)
        QuestionnairePrintReleaseService.revoke(
            release_id=first[0].id, target_campus_id=first[0].target_campus_id,
            tenant_id=self.tenant.id, actor=self.manager_user,
        )
        with self.assertRaises(ValidationError):
            confirm_review(token=token, expected_kind="questionnaire",
                           tenant_id=self.tenant.id, actor=self.manager_user,
                           request=request)

    def test_all_campuses_retry_preserves_identical_preexisting_campus(self):
        self._add_other_campus_to_questionnaire()
        start, end = self._bulk_window()
        existing = QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=self.r2.id,
            target_campus_id=self.campus.id, tenant_id=self.tenant.id,
            actor=self.manager_user, print_from=start, print_until=end,
        )
        request = RequestFactory().post("/", {"target_campus_id": "0"})
        request.scope = {"campus_ids": {self.campus.id, self.other_campus.id}}
        token, _payload = make_review(
            kind="questionnaire", bases=((self.parent.id, self.r2.id),),
            campus_id=0, tenant_id=self.tenant.id, actor=self.manager_user,
            request=request, window_from=start, window_until=end,
        )
        first = confirm_review(
            token=token, expected_kind="questionnaire", tenant_id=self.tenant.id,
            actor=self.manager_user, request=request,
        )
        self.assertIn(existing.id, {row.id for row in first})
        audits_after = AuditLog.objects.count()
        second = confirm_review(
            token=token, expected_kind="questionnaire", tenant_id=self.tenant.id,
            actor=self.manager_user, request=request,
        )
        self.assertEqual({row.id for row in first}, {row.id for row in second})
        self.assertEqual(AuditLog.objects.count(), audits_after)

    def test_matching_intervening_release_is_not_a_retry_for_same_or_other_actor(self):
        start, end = self._bulk_window()
        request = RequestFactory().post("/", {"target_campus_id": str(self.campus.id)})
        request.scope = {"campus_ids": {self.campus.id}}
        other_actor = self.make_user(
            "second-questionnaire-manager", self.department,
            ("admin_portal.access", "departmental_exams.manage_exam_generation"),
        )
        for actor in (self.manager_user, other_actor):
            with self.subTest(actor=actor.id):
                token, _ = make_review(
                    kind="questionnaire", bases=((self.parent.id, self.r2.id),),
                    campus_id=self.campus.id, tenant_id=self.tenant.id, actor=self.manager_user,
                    request=request, window_from=start, window_until=end,
                )
                intervening = QuestionnairePrintReleaseService.release(
                    cycle_course_id=self.parent.id, revision_id=self.r2.id,
                    target_campus_id=self.campus.id, tenant_id=self.tenant.id,
                    actor=actor, print_from=start, print_until=end,
                )
                audits_before = AuditLog.objects.count()
                with self.assertRaises(ValidationError):
                    confirm_review(token=token, expected_kind="questionnaire",
                                   tenant_id=self.tenant.id, actor=self.manager_user,
                                   request=request)
                self.assertEqual(AuditLog.objects.count(), audits_before)
                QuestionnairePrintReleaseService.revoke(
                    release_id=intervening.id, target_campus_id=self.campus.id,
                    tenant_id=self.tenant.id, actor=self.manager_user,
                )

    def test_separate_same_actor_confirmations_with_identical_values_are_distinct(self):
        start, end = self._bulk_window()
        request = RequestFactory().post("/", {"target_campus_id": str(self.campus.id)})
        request.scope = {"campus_ids": [self.campus.id]}
        reviews = [make_review(
            kind="questionnaire", bases=((self.parent.id, self.r2.id),),
            campus_id=self.campus.id, tenant_id=self.tenant.id, actor=self.manager_user,
            request=request, window_from=start, window_until=end,
        ) for _ in range(2)]
        self.assertNotEqual(reviews[0][1]["confirmation_id"], reviews[1][1]["confirmation_id"])
        confirmed = confirm_review(
            token=reviews[1][0], expected_kind="questionnaire",
            tenant_id=self.tenant.id, actor=self.manager_user, request=request,
        )[0]
        audit_count = AuditLog.objects.count()
        with self.assertRaises(ValidationError):
            confirm_review(
                token=reviews[0][0], expected_kind="questionnaire",
                tenant_id=self.tenant.id, actor=self.manager_user, request=request,
            )
        self.assertEqual(QuestionnairePrintRelease.objects.count(), 1)
        self.assertEqual(confirmed.review_confirmation_id, reviews[1][1]["confirmation_id"])
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_confirmed_release_replaced_later_cannot_be_revived_by_retry(self):
        start, end = self._bulk_window()
        request = RequestFactory().post("/", {"target_campus_id": str(self.campus.id)})
        request.scope = {"campus_ids": {self.campus.id}}
        token, _ = make_review(
            kind="questionnaire", bases=((self.parent.id, self.r2.id),),
            campus_id=self.campus.id, tenant_id=self.tenant.id, actor=self.manager_user,
            request=request, window_from=start, window_until=end,
        )
        first = confirm_review(token=token, expected_kind="questionnaire",
                               tenant_id=self.tenant.id, actor=self.manager_user, request=request)[0]
        replacement = QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=self.r2.id,
            target_campus_id=self.campus.id, tenant_id=self.tenant.id,
            actor=self.manager_user, print_from=start,
            print_until=end + timezone.timedelta(hours=1),
        )
        with self.assertRaises(ValidationError):
            confirm_review(token=token, expected_kind="questionnaire",
                           tenant_id=self.tenant.id, actor=self.manager_user, request=request)
        first.refresh_from_db()
        replacement.refresh_from_db()
        self.assertEqual(first.status, "REVOKED")
        self.assertEqual(replacement.status, "ACTIVE")

    def test_pre_confirmation_id_review_token_requires_a_new_review(self):
        start, end = self._bulk_window()
        request = RequestFactory().post("/", {"target_campus_id": str(self.campus.id)})
        request.scope = {"campus_ids": {self.campus.id}}
        _, payload = make_review(
            kind="questionnaire", bases=((self.parent.id, self.r2.id),),
            campus_id=self.campus.id, tenant_id=self.tenant.id, actor=self.manager_user,
            request=request, window_from=start, window_until=end,
        )
        payload.pop("confirmation_id")
        old_token = signing.dumps(payload, salt=SIGNING_SALT, compress=True)
        with self.assertRaises(ValidationError):
            confirm_review(token=old_token, expected_kind="questionnaire",
                           tenant_id=self.tenant.id, actor=self.manager_user,
                           request=request)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())

    def test_partial_request_scope_shows_only_its_legacy_campus_and_revoke(self):
        self._add_other_campus_to_questionnaire()
        now = timezone.now()
        legacy = QuestionnairePrintRelease.objects.create(
            cycle_course=self.parent, generation_revision=self.r2,
            print_from=now - timezone.timedelta(minutes=1),
            print_until=now + timezone.timedelta(hours=2),
            released_by=self.manager_user,
        )
        for campus in (self.campus, self.other_campus):
            QuestionnaireLegacyCampusCoverage.objects.create(release=legacy, campus=campus)
        for details in (False, True):
            with self.subTest(details=details):
                path = (reverse("departmental_exams:questionnaire_print_release_details",
                                args=[self.parent.id]) if details else
                        reverse("departmental_exams:questionnaire_print_release"))
                request = RequestFactory().get(path, {"target_campus_id": self.campus.id})
                request.user = self.manager_user
                request.scope = {"tenant_id": self.tenant.id, "campus_ids": [self.campus.id]}
                response = unwrap(questionnaire_print_release_view)(
                    request, detail_course_id=self.parent.id if details else None,
                )
                self.assertEqual(response.status_code, 200)
                content = response.content.decode()
                self.assertIn("Printable now", content)
                self.assertIn(self.campus.name, content)
                self.assertNotIn(self.other_campus.name, content)
                if details:
                    self.assertIn(f'value="{legacy.id}"', content)
                    self.assertIn("Coverage retained", content)
                    self.assertIn(f'name="target_campus_id" value="{self.campus.id}"', content)
        restricted_post = RequestFactory().post("/", {"target_campus_id": self.campus.id})
        restricted_post.scope = {"tenant_id": self.tenant.id, "campus_ids": [self.campus.id]}
        with self.assertRaises(PermissionDenied):
            QuestionnairePrintReleaseService.revoke(
                release_id=legacy.id, target_campus_id=self.other_campus.id,
                tenant_id=self.tenant.id, actor=self.manager_user,
                request=restricted_post,
            )
        QuestionnairePrintReleaseService.revoke(
            release_id=legacy.id, target_campus_id=self.campus.id,
            tenant_id=self.tenant.id, actor=self.manager_user,
            request=restricted_post,
        )
        self.assertIsNotNone(QuestionnaireLegacyCampusCoverage.objects.get(
            release=legacy, campus=self.campus,
        ).retired_at)
        self.assertIsNone(QuestionnaireLegacyCampusCoverage.objects.get(
            release=legacy, campus=self.other_campus,
        ).retired_at)
        restricted_get = RequestFactory().get(
            reverse("departmental_exams:questionnaire_print_release_details", args=[self.parent.id]),
            {"target_campus_id": self.campus.id},
        )
        restricted_get.user = self.manager_user
        restricted_get.scope = restricted_post.scope
        content = unwrap(questionnaire_print_release_view)(
            restricted_get, detail_course_id=self.parent.id,
        ).content.decode()
        self.assertIn("Coverage retired for visible campuses", content)
        self.assertIn("Not released", content)
        self.assertNotIn(self.other_campus.name, content)

    def test_legacy_coverage_identity_and_retirement_provenance_are_immutable(self):
        self._add_other_campus_to_questionnaire()
        now = timezone.now()
        legacy = QuestionnairePrintRelease.objects.create(
            cycle_course=self.parent, generation_revision=self.r2,
            print_from=now, print_until=now + timezone.timedelta(hours=2),
            released_by=self.manager_user,
        )
        coverage = QuestionnaireLegacyCampusCoverage.objects.create(
            release=legacy, campus=self.campus,
        )
        coverage.campus = self.other_campus
        with self.assertRaises(ValidationError):
            coverage.save(update_fields=["campus"])
        coverage.refresh_from_db()
        scoped = QuestionnairePrintRelease.objects.create(
            cycle_course=self.parent, generation_revision=self.r2,
            scope_kind="SCOPED", target_campus=self.campus, scope_key=self.campus.id,
            print_from=now, print_until=now + timezone.timedelta(hours=2),
            released_by=self.manager_user,
        )
        coverage.release = scoped
        with self.assertRaises(ValidationError):
            coverage.save(update_fields=["release"])
        coverage.refresh_from_db()
        coverage.retired_at = now
        coverage.retired_by = self.manager_user
        with self.assertRaises(ValidationError):
            coverage.save(update_fields=["retired_at"])
        coverage.refresh_from_db()
        retired = QuestionnairePrintReleaseService.retire_legacy_coverage(
            course=self.parent, campus_id=self.campus.id, actor=self.manager_user,
        )
        self.assertEqual(retired.id, coverage.id)
        coverage.refresh_from_db()
        retired_at = coverage.retired_at
        self.assertIsNotNone(retired_at)
        coverage.retired_at = None
        coverage.retired_by = None
        with self.assertRaises(ValidationError):
            coverage.save(update_fields=["retired_at", "retired_by"])
        coverage.refresh_from_db()
        coverage.retired_at = retired_at + timezone.timedelta(seconds=1)
        with self.assertRaises(ValidationError):
            coverage.save(update_fields=["retired_at"])
        coverage.refresh_from_db()
        coverage.retired_by = self.faculty
        with self.assertRaises(ValidationError):
            coverage.save(update_fields=["retired_by"])
        coverage.refresh_from_db()
        self.assertEqual(coverage.retired_at, retired_at)
        self.assertEqual(coverage.retired_by_id, self.manager_user.id)
        self.assertIsNone(QuestionnairePrintReleaseService.retire_legacy_coverage(
            course=self.parent, campus_id=self.campus.id, actor=self.manager_user,
        ))

    def test_individual_review_labels_retired_legacy_coverage_not_released(self):
        now = timezone.now()
        legacy = QuestionnairePrintRelease.objects.create(
            cycle_course=self.parent, generation_revision=self.r2,
            print_from=now, print_until=now + timezone.timedelta(hours=2),
            released_by=self.manager_user,
        )
        QuestionnaireLegacyCampusCoverage.objects.create(release=legacy, campus=self.campus)
        QuestionnairePrintReleaseService.revoke(
            release_id=legacy.id, target_campus_id=self.campus.id,
            tenant_id=self.tenant.id, actor=self.manager_user,
        )
        start, end = self._bulk_window()
        client = Client()
        client.force_login(self.manager_user)
        response = client.post(reverse("departmental_exams:questionnaire_print_release"), {
            "action": "release", "review": "1", "cycle_course_id": self.parent.id,
            "generation_revision": self.r2.id, "target_campus_id": self.campus.id,
            "print_from": start.strftime("%Y-%m-%dT%H:%M"),
            "print_until": end.strftime("%Y-%m-%dT%H:%M"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not currently released")
        self.assertNotContains(response, "Active R")

    def test_legacy_coverage_retirement_never_falls_back(self):
        self._add_other_campus_to_questionnaire()
        now = timezone.now()
        legacy = QuestionnairePrintRelease.objects.create(
            cycle_course=self.parent, generation_revision=self.r2,
            print_from=now - timezone.timedelta(minutes=1),
            print_until=now + timezone.timedelta(hours=2),
            released_by=self.manager_user,
        )
        for campus in (self.campus, self.other_campus):
            QuestionnaireLegacyCampusCoverage.objects.create(release=legacy, campus=campus)
        self.other_campus.is_active = False
        self.other_campus.save(update_fields=["is_active"])
        scoped = self._release()
        coverage = QuestionnaireLegacyCampusCoverage.objects.get(
            release=legacy, campus=self.campus,
        )
        self.assertIsNotNone(coverage.retired_at)
        self.assertIsNone(QuestionnaireLegacyCampusCoverage.objects.get(
            release=legacy, campus=self.other_campus,
        ).retired_at)
        QuestionnairePrintReleaseService.revoke(
            release_id=scoped.id, target_campus_id=self.campus.id,
            tenant_id=self.tenant.id, actor=self.manager_user,
        )
        with self.assertRaises(PermissionDenied):
            FacultyQuestionnairePrintService._printable_release(
                contribution=self.contribution, release_id=legacy.id, set_code="A",
            )
        self.other_campus.is_active = True
        self.other_campus.save(update_fields=["is_active"])
        coverage.refresh_from_db()
        self.assertIsNotNone(coverage.retired_at)

    def test_unretired_legacy_coverage_resumes_only_while_eligible_after_reactivation(self):
        now = timezone.now()
        legacy = QuestionnairePrintRelease.objects.create(
            cycle_course=self.parent, generation_revision=self.r2,
            print_from=now - timezone.timedelta(minutes=1),
            print_until=now + timezone.timedelta(hours=1),
            released_by=self.manager_user,
        )
        QuestionnaireLegacyCampusCoverage.objects.create(
            release=legacy, campus=self.campus,
        )
        self.campus.is_active = False
        self.campus.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            FacultyQuestionnairePrintService._printable_release(
                contribution=self.contribution, release_id=legacy.id, set_code="A",
            )
        self.campus.is_active = True
        self.campus.save(update_fields=["is_active"])
        self.assertEqual(
            FacultyQuestionnairePrintService._printable_release(
                contribution=self.contribution, release_id=legacy.id, set_code="A",
            )[0].id, legacy.id,
        )
        ExamGenerationRevision.objects.filter(pk=self.r2.id).update(
            status=ExamGenerationRevision.Status.SUPERSEDED, current_marker=None,
        )
        with self.assertRaises(PermissionDenied):
            FacultyQuestionnairePrintService._printable_release(
                contribution=self.contribution, release_id=legacy.id, set_code="A",
            )

    def test_all_campuses_rejects_a_new_participant_after_review(self):
        request = RequestFactory().post("/", {"target_campus_id": "0"})
        request.scope = {"campus_ids": {self.campus.id, self.other_campus.id}}
        start, end = self._bulk_window()
        token, original = make_review(
            kind="questionnaire", bases=((self.parent.id, self.r2.id),),
            campus_id=0, tenant_id=self.tenant.id, actor=self.manager_user,
            request=request, window_from=start, window_until=end,
        )
        self.assertEqual(len(original["targets"]), 1)
        self._add_other_campus_to_questionnaire()
        audits_before = AuditLog.objects.count()
        with self.assertRaises(ValidationError):
            confirm_review(token=token, expected_kind="questionnaire",
                           tenant_id=self.tenant.id, actor=self.manager_user,
                           request=request)
        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        self.assertEqual(AuditLog.objects.count(), audits_before)

    def test_campus_batch_rolls_back_release_and_audit_on_late_invalid_revision(self):
        self._add_other_campus_to_questionnaire()
        start, end = self._bulk_window()
        audits_before = AuditLog.objects.count()
        with self.assertRaises(ValidationError):
            QuestionnairePrintReleaseService.bulk_release(
                selections=((self.parent.id, self.r2.id, self.campus.id),
                            (self.parent.id, 999999, self.other_campus.id)),
                tenant_id=self.tenant.id, actor=self.manager_user,
                print_from=start, print_until=end,
            )
        self.assertFalse(QuestionnairePrintRelease.objects.exists())
        self.assertEqual(AuditLog.objects.count(), audits_before)

    def test_manual_review_does_not_gain_generation_management_release_authority(self):
        cycle = self.parent.cycle
        cycle.processing_mode = ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        cycle.save(update_fields=["processing_mode", "updated_at"])
        request = RequestFactory().post("/", {"target_campus_id": str(self.campus.id)})
        request.scope = {"campus_ids": {self.campus.id}}
        start, end = self._bulk_window()
        with self.assertRaises(PermissionDenied):
            make_review(
                kind="questionnaire", bases=((self.parent.id, self.r2.id),),
                campus_id=self.campus.id, tenant_id=self.tenant.id,
                actor=self.manager_user, request=request,
                window_from=start, window_until=end,
            )
        self.assertFalse(QuestionnairePrintRelease.objects.exists())


class QuestionnaireCampusMigrationTests(Stage4TransactionTestCase):
    def test_active_legacy_snapshot_includes_inactive_campus_and_reverse_is_guarded(self):
        previous = [("departmental_exams", "0032_contribution_correction_versions")]
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        try:
            historical = executor.loader.project_state(previous).apps
            OldRelease = historical.get_model("departmental_exams", "QuestionnairePrintRelease")
            course = self.make_course()
            program = Program.objects.create(
                tenant=self.tenant, campus=self.other_campus,
                department=self.other_department, code="MIG-P", name="Migration",
            )
            section = Section.objects.create(
                tenant=self.tenant, campus=self.other_campus,
                department=self.other_department, program=program,
                code="MIG-S", name="Migration",
            )
            offering = CourseOffering.objects.create(
                tenant=self.tenant, campus=self.other_campus,
                department=self.other_department, program=program, section=section,
                course=course.course, academic_year=course.cycle.academic_year,
                term=course.cycle.term,
            )
            CycleCourseOffering.objects.create(
                cycle_course=course, offering=offering, campus=self.other_campus,
            )
            self.other_campus.is_active = False
            self.other_campus.save(update_fields=["is_active"])
            revision = ExamGenerationRevision.objects.create(
                cycle_course=course, revision_number=1,
                source_input_fingerprint="f" * 64, algorithm_version="migration-test",
                generation_trigger="AUTOMATIC", configuration_revision_snapshot=1,
                blueprint_revision_snapshot=1, roster_boundary_snapshot="r" * 64,
                final_item_count_snapshot=2, request_token_digest="t" * 64,
                minimum_overlap=0, proportional_score=0,
                contributors_represented=1, squared_contributor_concentration=4,
            )
            now = timezone.now()
            active = OldRelease.objects.create(
                cycle_course_id=course.id, generation_revision_id=revision.id,
                print_from=now, print_until=now + timezone.timedelta(hours=2),
                released_by_id=self.admin.id, released_at=now,
            )
            historical_row = OldRelease.objects.create(
                cycle_course_id=course.id, generation_revision_id=revision.id,
                print_from=now, print_until=now + timezone.timedelta(hours=1),
                released_by_id=self.admin.id, released_at=now,
                status="REVOKED", active_marker=None,
                revoked_by_id=self.admin.id, revoked_at=now,
            )
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
            active_after = QuestionnairePrintRelease.objects.get(pk=active.id)
            self.assertEqual(active_after.scope_kind, "LEGACY_COURSE_WIDE")
            self.assertIsNone(active_after.review_confirmation_id)
            self.assertEqual(active_after.print_until, active.print_until)
            self.assertEqual(active_after.released_by_id, self.admin.id)
            self.assertEqual(set(QuestionnaireLegacyCampusCoverage.objects.filter(
                release_id=active.id,
            ).values_list("campus_id", flat=True)),
                             {self.campus.id, self.other_campus.id})
            self.assertFalse(QuestionnaireLegacyCampusCoverage.objects.filter(
                release_id=historical_row.id,
            ).exists())
            scoped = QuestionnairePrintRelease.objects.create(
                scope_kind="SCOPED", target_campus=self.campus,
                scope_key=self.campus.id, cycle_course=course,
                generation_revision=revision, print_from=now,
                print_until=now + timezone.timedelta(hours=2),
                released_by=self.admin,
            )
            self.assertIsNotNone(scoped.id)
            executor = MigrationExecutor(connection)
            with self.assertRaises(IrreversibleError):
                executor.migrate(previous)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
