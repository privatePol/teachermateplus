import csv
import io
from html.parser import HTMLParser

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import escape
from apps.academics.models import CourseOffering, Section
from apps.core.services.settings import SystemSettingService
from apps.core.context_processors import portal_menu
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, UserPermission, UserRole
from apps.tenants.models import Program

from .contribution_services import ContributionRosterService, QuestionMutationService
from .csv_import import QuestionCSVImportService
from .models import (
    CycleCourseOffering,
    FacultyContribution,
    Question,
    QuestionImportBatch,
    QuestionImportRow,
)
from .tests_stage5_contributions import Stage5FixtureMixin
from .stage4_test_support import Stage4TestCase


class _ActiveMenuParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active_codes = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        code = attributes.get("data-menu-code")
        if code and "active" in classes:
            self.active_codes.append(code)


class _HrefParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def _active_menu_codes(response):
    parser = _ActiveMenuParser()
    parser.feed(response.content.decode())
    return parser.active_codes


def _response_hrefs(response):
    parser = _HrefParser()
    parser.feed(response.content.decode())
    return parser.hrefs


class Stage5FacultyViewTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("view-faculty")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get()
        self.client.force_login(self.faculty)

    def fill_questions(self, count):
        return Question.objects.bulk_create(
            [
                Question(
                    contribution=self.contribution,
                    question_text=f"Question {position}",
                    choice_a="A",
                    choice_b="B",
                    choice_c="C",
                    choice_d="D",
                    correct_answer="A",
                    difficulty="EASY",
                    position=position,
                )
                for position in range(1, count + 1)
            ]
        )

    @staticmethod
    def valid_question_post(revision):
        return {
            "expected_contribution_revision": revision,
            "question_text": "Forbidden question",
            "choice_a": "A",
            "choice_b": "B",
            "choice_c": "C",
            "choice_d": "D",
            "correct_answer": "A",
            "difficulty": "EASY",
        }

    @staticmethod
    def valid_csv_upload(row=None):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(
            (
                "question_text",
                "choice_a",
                "choice_b",
                "choice_c",
                "choice_d",
                "correct_answer",
                "difficulty",
            )
        )
        writer.writerow(
            row
            or ("Forbidden CSV question", "A", "B", "C", "D", "A", "EASY")
        )
        return SimpleUploadedFile(
            "questions.csv",
            stream.getvalue().encode("utf-8"),
            content_type="text/csv",
        )

    def assert_workspace_has_no_mutation_urls(self, response, question):
        mutation_urls = (
            reverse(
                "departmental_exams:question_create",
                args=[self.contribution.id],
            ),
            reverse(
                "departmental_exams:question_edit",
                args=[self.contribution.id, question.id],
            ),
            reverse(
                "departmental_exams:question_delete",
                args=[self.contribution.id, question.id],
            ),
            reverse(
                "departmental_exams:question_reorder",
                args=[self.contribution.id],
            ),
            reverse(
                "departmental_exams:csv_upload",
                args=[self.contribution.id],
            ),
            reverse(
                "departmental_exams:contribution_submit",
                args=[self.contribution.id],
            ),
        )
        for url in mutation_urls:
            with self.subTest(url=url):
                self.assertNotContains(response, url)

    def assert_direct_mutation_routes_denied(self, question, batch):
        responses = (
            self.client.post(
                reverse(
                    "departmental_exams:question_create",
                    args=[self.contribution.id],
                ),
                self.valid_question_post(self.contribution.revision),
            ),
            self.client.post(
                reverse(
                    "departmental_exams:question_edit",
                    args=[self.contribution.id, question.id],
                ),
                {
                    **self.valid_question_post(self.contribution.revision),
                    "expected_question_revision": question.revision,
                },
            ),
            self.client.post(
                reverse(
                    "departmental_exams:question_delete",
                    args=[self.contribution.id, question.id],
                ),
                {
                    "expected_contribution_revision": self.contribution.revision,
                    "expected_question_revision": question.revision,
                },
            ),
            self.client.post(
                reverse(
                    "departmental_exams:question_reorder",
                    args=[self.contribution.id],
                ),
                {
                    "expected_contribution_revision": self.contribution.revision,
                    "ordered_question_ids": str(question.id),
                },
            ),
            self.client.post(
                reverse(
                    "departmental_exams:csv_upload",
                    args=[self.contribution.id],
                ),
                {
                    "expected_contribution_revision": self.contribution.revision,
                    "csv_file": self.valid_csv_upload(),
                },
            ),
            self.client.post(
                reverse("departmental_exams:csv_confirm", args=[batch.token]),
                {"file_sha256": batch.file_sha256},
            ),
            self.client.post(
                reverse(
                    "departmental_exams:contribution_submit",
                    args=[self.contribution.id],
                ),
                {
                    "expected_contribution_revision": self.contribution.revision,
                    "confirm_exact_quota": "on",
                },
            ),
        )
        for response in responses:
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 403)
                self.assertTemplateUsed(
                    response, "departmental_exams/faculty/error.html"
                )

    def roster_state(self):
        self.configuration.refresh_from_db()
        self.contribution.refresh_from_db()
        return {
            "contribution_status": self.contribution.status,
            "roster_status": self.contribution.roster_status,
            "roster_revision": self.configuration.contributor_roster_revision,
            "source_rows": list(
                self.contribution.eligibility_sources.order_by("id").values(
                    "id",
                    "assignment_id",
                    "assignment_id_snapshot",
                    "offering_id_snapshot",
                    "tenant_id_snapshot",
                    "campus_id_snapshot",
                    "is_current",
                    "invalidated_at",
                )
            ),
            "audit_count": AuditLog.objects.count(),
        }

    def add_grouped_offering(self, slug):
        program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code=f"VIEW-{slug}",
            name=f"View {slug}",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=program,
            code=f"VIEW-{slug}",
            name=f"View {slug}",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=program,
            academic_year=self.parent.cycle.academic_year,
            term=self.parent.cycle.term,
            course=self.parent.course,
            section=section,
        )
        CycleCourseOffering.objects.create(
            cycle_course=self.parent,
            offering=offering,
            campus=self.campus,
        )
        return offering

    def test_workspace_hides_offerings_and_keeps_contribution_details_and_controls(self):
        grouped_offering = self.add_grouped_offering("HIDDEN-OFFERING")
        type(self.configuration).objects.filter(pk=self.configuration.pk).update(
            contributor_instructions_snapshot="Write plain-text questions."
        )

        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )

        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(workspace.context["offering_snapshots"].count(), 2)
        self.assertNotContains(workspace, "Offerings / campuses:")
        self.assertNotContains(workspace, str(grouped_offering))
        self.assertNotContains(workspace, grouped_offering.section.code)
        self.assertNotContains(workspace, grouped_offering.section.name)
        self.assertContains(
            workspace,
            f"{self.parent.course.code} — {self.parent.course.title}",
        )
        self.assertContains(workspace, "Coverage:")
        self.assertContains(workspace, "Core outcomes")
        self.assertContains(workspace, "0 / 50")
        self.assertContains(
            workspace,
            date_format(
                timezone.localtime(self.configuration.contribution_deadline),
                "M j, Y g:i A",
            ),
        )
        self.assertContains(workspace, "Write plain-text questions.")
        self.assertContains(workspace, ">Add question<")
        self.assertContains(workspace, ">Upload CSV<")
        self.assertContains(workspace, ">Download CSV template<")

    def test_unsynchronized_assignment_loss_at_49_is_rendered_read_only(self):
        questions = self.fill_questions(49)
        batch = QuestionCSVImportService.create_preview(
            contribution_id=self.contribution.id,
            uploaded_file=self.valid_csv_upload(),
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        before = self.roster_state()

        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )

        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "49 / 50")
        self.assertContains(workspace, questions[0].question_text)
        self.assertContains(
            workspace, "Your current contributor eligibility could not be verified."
        )
        self.assertContains(
            workspace,
            "temporarily read-only until the roster is synchronized",
        )
        self.assertNotContains(workspace, ">Add question<")
        self.assertNotContains(workspace, ">Upload CSV<")
        self.assertNotContains(workspace, ">Edit<")
        self.assertNotContains(workspace, ">Delete<")
        self.assertNotContains(workspace, "Quota reached.")
        self.assertNotContains(workspace, "Final submission")
        self.assertNotContains(workspace, "Save displayed order")
        self.assertNotContains(workspace, "Move up")
        self.assertNotContains(workspace, "Move down")
        self.assert_workspace_has_no_mutation_urls(workspace, questions[0])
        self.assertEqual(self.roster_state(), before)

        self.assert_direct_mutation_routes_denied(questions[0], batch)
        self.assertEqual(self.roster_state(), before)

    def test_unsynchronized_exact_permission_deny_at_50_is_rendered_read_only(self):
        questions = self.fill_questions(49)
        batch = QuestionCSVImportService.create_preview(
            contribution_id=self.contribution.id,
            uploaded_file=self.valid_csv_upload(),
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        Question.objects.create(
            contribution=self.contribution,
            question_text="Question 50",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=50,
        )
        permission = Permission.objects.get(code="faculty_portal.access")
        UserPermission.objects.create(
            user=self.faculty,
            permission=permission,
            tenant=self.tenant,
            campus=self.campus,
            grant_type=UserPermission.GrantType.ALLOW,
        )
        UserRole.objects.filter(user=self.faculty).update(
            campus=self.other_campus,
            department=self.other_department,
        )
        self.faculty.default_campus = self.other_campus
        self.faculty.default_department = self.other_department
        self.faculty.save(
            update_fields=["default_campus", "default_department", "updated_at"]
        )
        UserPermission.objects.create(
            user=self.faculty,
            permission=permission,
            tenant=self.tenant,
            campus=self.campus,
            grant_type=UserPermission.GrantType.DENY,
        )
        before = self.roster_state()

        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )

        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "50 / 50")
        self.assertContains(workspace, questions[0].question_text)
        self.assertContains(
            workspace, "Your current contributor eligibility could not be verified."
        )
        self.assertNotContains(workspace, ">Add question<")
        self.assertNotContains(workspace, ">Upload CSV<")
        self.assertNotContains(workspace, ">Edit<")
        self.assertNotContains(workspace, ">Delete<")
        self.assertNotContains(workspace, "Quota reached.")
        self.assertNotContains(workspace, "required quota of 50 questions")
        self.assertNotContains(workspace, "Final submission")
        self.assertNotContains(workspace, "Save displayed order")
        self.assertNotContains(workspace, "Move up")
        self.assertNotContains(workspace, "Move down")
        self.assert_workspace_has_no_mutation_urls(workspace, questions[0])
        self.assertEqual(self.roster_state(), before)

        self.assert_direct_mutation_routes_denied(questions[0], batch)
        self.assertEqual(self.roster_state(), before)

    def test_unsynchronized_unretained_new_assignment_does_not_reauthorize(self):
        questions = self.fill_questions(49)
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        new_offering = self.add_grouped_offering("UNRETAINED")
        new_assignment = self.make_assignment(
            self.parent,
            self.faculty,
            offering=new_offering,
        )
        self.assertFalse(
            self.contribution.eligibility_sources.filter(
                assignment_id_snapshot=new_assignment.id
            ).exists()
        )
        before = self.roster_state()

        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )

        self.assertEqual(workspace.status_code, 200)
        self.assertContains(
            workspace, "Your current contributor eligibility could not be verified."
        )
        self.assert_workspace_has_no_mutation_urls(workspace, questions[0])
        self.assertEqual(self.roster_state(), before)

    def test_workspace_actions_track_capacity_and_reappear_after_delete(self):
        questions = self.fill_questions(49)
        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )
        create_url = reverse(
            "departmental_exams:question_create", args=[self.contribution.id]
        )
        upload_url = reverse(
            "departmental_exams:csv_upload", args=[self.contribution.id]
        )
        submit_url = reverse(
            "departmental_exams:contribution_submit", args=[self.contribution.id]
        )
        self.assertContains(workspace, "49 / 50")
        self.assertContains(workspace, f'href="{create_url}"')
        self.assertContains(workspace, f'href="{upload_url}"')
        self.assertNotContains(workspace, "Quota reached.")
        self.assertNotContains(workspace, f'href="{submit_url}"')

        last_question = Question.objects.create(
            contribution=self.contribution,
            question_text="Question 50",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=50,
        )
        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )
        self.assertContains(workspace, "50 / 50")
        self.assertContains(workspace, "100% complete")
        self.assertContains(workspace, "Quota reached.")
        self.assertContains(workspace, "required quota of 50 questions")
        self.assertNotContains(workspace, f'href="{create_url}"')
        self.assertNotContains(workspace, f'href="{upload_url}"')
        self.assertContains(workspace, f'href="{submit_url}"')
        self.assertContains(workspace, "Download CSV template")
        self.assertContains(workspace, "Save displayed order")
        self.assertContains(workspace, "Edit", count=50)
        self.assertContains(workspace, "Delete", count=50)

        deleted = self.client.post(
            reverse(
                "departmental_exams:question_delete",
                args=[self.contribution.id, last_question.id],
            ),
            {
                "expected_contribution_revision": self.contribution.revision,
                "expected_question_revision": last_question.revision,
            },
        )
        self.assertEqual(deleted.status_code, 302)
        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )
        self.assertContains(workspace, "49 / 50")
        self.assertContains(workspace, f'href="{create_url}"')
        self.assertContains(workspace, f'href="{upload_url}"')
        self.assertNotContains(workspace, "Quota reached.")
        self.assertNotContains(workspace, f'href="{submit_url}"')
        self.assertEqual(questions[0].position, 1)

    def test_full_quota_manual_routes_return_safe_conflict_without_side_effects(self):
        self.fill_questions(50)
        create_url = reverse(
            "departmental_exams:question_create", args=[self.contribution.id]
        )
        revision_before = self.contribution.revision
        audit_count = AuditLog.objects.filter(action="DE_EXAM_QUESTION_CREATED").count()

        for response in (
            self.client.get(create_url),
            self.client.post(
                create_url,
                self.valid_question_post(self.contribution.revision),
            ),
        ):
            self.assertEqual(response.status_code, 409)
            self.assertTemplateUsed(response, "departmental_exams/faculty/error.html")
            self.assertTemplateUsed(response, "faculty_portal/base.html")
            self.assertContains(response, "Contribution quota reached", status_code=409)
            self.assertContains(response, "required quota of 50 questions", status_code=409)
            self.assertContains(response, "Return to Question Bank", status_code=409)
            self.assertNotContains(response, 'name="question_text"', status_code=409)
            self.assertNotContains(
                response,
                "The submitted page state is missing or invalid",
                status_code=409,
            )

        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.questions.count(), 50)
        self.assertEqual(self.contribution.revision, revision_before)
        self.assertEqual(
            AuditLog.objects.filter(action="DE_EXAM_QUESTION_CREATED").count(),
            audit_count,
        )

    def test_full_quota_csv_routes_return_safe_conflict_without_side_effects(self):
        self.fill_questions(50)
        upload_url = reverse(
            "departmental_exams:csv_upload", args=[self.contribution.id]
        )
        revision_before = self.contribution.revision
        audit_count = AuditLog.objects.filter(
            action="DE_EXAM_QUESTION_CSV_IMPORTED"
        ).count()

        responses = (
            self.client.get(upload_url),
            self.client.post(
                upload_url,
                {
                    "expected_contribution_revision": self.contribution.revision,
                    "csv_file": self.valid_csv_upload(),
                },
            ),
        )
        for response in responses:
            self.assertEqual(response.status_code, 409)
            self.assertTemplateUsed(response, "departmental_exams/faculty/error.html")
            self.assertContains(response, "Contribution quota reached", status_code=409)
            self.assertContains(response, "required quota of 50 questions", status_code=409)
            self.assertNotContains(response, 'name="csv_file"', status_code=409)

        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.questions.count(), 50)
        self.assertEqual(self.contribution.revision, revision_before)
        self.assertFalse(QuestionImportBatch.objects.exists())
        self.assertEqual(
            AuditLog.objects.filter(action="DE_EXAM_QUESTION_CSV_IMPORTED").count(),
            audit_count,
        )

    def test_newly_full_csv_preview_and_confirm_are_quota_conflicts_without_replay(self):
        self.fill_questions(49)
        batch = QuestionCSVImportService.create_preview(
            contribution_id=self.contribution.id,
            uploaded_file=self.valid_csv_upload(),
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        QuestionMutationService.create(
            contribution_id=self.contribution.id,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
            payload=self.payload("Question that fills the quota"),
        )
        self.contribution.refresh_from_db()
        revision_after_fill = self.contribution.revision
        audit_count = AuditLog.objects.filter(
            action="DE_EXAM_QUESTION_CSV_IMPORTED"
        ).count()
        preview_url = reverse("departmental_exams:csv_preview", args=[batch.token])
        confirm_url = reverse("departmental_exams:csv_confirm", args=[batch.token])

        responses = (
            self.client.get(preview_url),
            self.client.post(confirm_url, {"file_sha256": batch.file_sha256}),
            self.client.post(confirm_url, {"file_sha256": batch.file_sha256}),
        )
        for response in responses:
            self.assertEqual(response.status_code, 409)
            self.assertTemplateUsed(response, "departmental_exams/faculty/error.html")
            self.assertContains(response, "Contribution quota reached", status_code=409)
            self.assertNotContains(response, "Confirm atomic import", status_code=409)
            self.assertNotContains(response, str(batch.token), status_code=409)
            self.assertNotContains(response, "Forbidden CSV question", status_code=409)

        self.contribution.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(self.contribution.questions.count(), 50)
        self.assertEqual(self.contribution.revision, revision_after_fill)
        self.assertEqual(batch.status, QuestionImportBatch.Status.FAILED)
        self.assertEqual(batch.failure_code, "QUOTA_CHANGED")
        self.assertFalse(batch.rows.exists())
        self.assertEqual(
            AuditLog.objects.filter(action="DE_EXAM_QUESTION_CSV_IMPORTED").count(),
            audit_count,
        )

    def test_dashboard_and_workspace_gets_never_create_or_synchronize(self):
        self.configuration.refresh_from_db()
        before = (
            FacultyContribution.objects.count(),
            self.contribution.eligibility_sources.count(),
            self.configuration.contributor_roster_revision,
        )
        list_response = self.client.get(reverse("departmental_exams:contribution_list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "<title>Question Bank | TeacherMate+</title>", html=True)
        self.assertContains(list_response, '<h1 class="h3 mb-1">Question Bank</h1>', html=True)
        menu_item = next(
            node["item"]
            for group in list_response.context["portal_menu"]
            for node in group["items"]
            if node["item"].code == "DE_EXAM_FACULTY_CONTRIBUTIONS"
        )
        self.assertEqual(menu_item.label, "Question Bank")
        workspace_response = self.client.get(
            reverse("departmental_exams:contribution_workspace", args=[self.contribution.id])
        )
        self.assertEqual(workspace_response.status_code, 200)
        self.assertContains(
            workspace_response,
            f'<li class="breadcrumb-item"><a href="{reverse("departmental_exams:contribution_list")}">Question Bank</a></li>',
            html=True,
        )
        self.assertContains(workspace_response, "<strong>Deadline:</strong>", html=True)
        self.configuration.refresh_from_db()
        after = (
            FacultyContribution.objects.count(),
            self.contribution.eligibility_sources.count(),
            self.configuration.contributor_roster_revision,
        )
        self.assertEqual(after, before)

    def test_blocked_owner_reads_own_escaped_content_but_mutation_route_is_403(self):
        Question.objects.create(
            contribution=self.contribution,
            question_text="<script>alert(1)</script>",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=1,
        )
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        from .contribution_services import ContributionRosterService

        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        workspace = self.client.get(
            reverse("departmental_exams:contribution_workspace", args=[self.contribution.id])
        )
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "blocked and read-only")
        self.assertContains(workspace, "&lt;script&gt;alert(1)&lt;/script&gt;", html=False)
        self.assertNotContains(workspace, "Add question")
        denied = self.client.get(
            reverse("departmental_exams:question_create", args=[self.contribution.id])
        )
        self.assertEqual(denied.status_code, 403)
        self.assertTemplateUsed(denied, "departmental_exams/faculty/error.html")
        self.assertTemplateUsed(denied, "faculty_portal/base.html")
        self.assertContains(denied, 'class="alert alert-danger', status_code=403)
        self.assertNotContains(denied, "<script>alert(1)</script>", status_code=403)

    def test_csv_html_and_link_shaped_content_is_literal_escaped_and_nonclickable(self):
        html_fragments = (
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "<svg onload=alert(1)>",
            '<a href="javascript:alert(1)">click</a>',
        )
        question_text = "\n".join(html_fragments)
        choices = (
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "https://example.invalid/exam",
        )
        batch = QuestionCSVImportService.create_preview(
            contribution_id=self.contribution.id,
            uploaded_file=self.valid_csv_upload(
                (question_text, *choices, "A", "EASY")
            ),
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision,
        )
        preview = self.client.get(
            reverse("departmental_exams:csv_preview", args=[batch.token])
        )
        self.assertEqual(preview.status_code, 200)

        confirmed, changed = QuestionCSVImportService.confirm(
            token=batch.token,
            expected_file_sha256=batch.file_sha256,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        self.assertTrue(changed)
        workspace = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[confirmed.contribution_id],
            )
        )
        self.assertEqual(workspace.status_code, 200)
        stored = Question.objects.get(import_batch=batch)
        self.assertEqual(stored.question_text, question_text)
        self.assertEqual(
            (stored.choice_a, stored.choice_b, stored.choice_c, stored.choice_d),
            choices,
        )

        for response in (preview, workspace):
            with self.subTest(template=response.templates[0].name):
                body = response.content.decode()
                for fragment in html_fragments:
                    self.assertIn(escape(fragment), body)
                    self.assertNotIn(fragment, body)
                for choice in choices:
                    self.assertIn(escape(choice), body)
                unsafe_schemes = ("javascript:", "data:", "vbscript:")
                self.assertFalse(
                    any(
                        href.lower().startswith(unsafe_schemes)
                        or href == "https://example.invalid/exam"
                        for href in _response_hrefs(response)
                    )
                )

    def test_deadline_crossing_hides_controls_and_denies_upload(self):
        type(self.configuration).objects.filter(pk=self.configuration.pk).update(
            contribution_deadline=timezone.now() - timezone.timedelta(minutes=1)
        )
        workspace = self.client.get(
            reverse("departmental_exams:contribution_workspace", args=[self.contribution.id])
        )
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "currently read-only")
        self.assertNotContains(workspace, "Add question")
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:csv_upload", args=[self.contribution.id])
            ).status_code,
            403,
        )

    def test_non_owner_contribution_and_question_return_404(self):
        question = Question.objects.create(
            contribution=self.contribution,
            question_text="Private",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=1,
        )
        outsider = self.make_faculty("outsider")
        self.client.force_login(outsider)
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:contribution_workspace", args=[self.contribution.id])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "departmental_exams:question_edit",
                    args=[self.contribution.id, question.id],
                )
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse(
                    "departmental_exams:question_delete",
                    args=[self.contribution.id, question.id],
                ),
                {
                    "expected_contribution_revision": self.contribution.revision,
                    "expected_question_revision": question.revision,
                },
            ).status_code,
            404,
        )

    def test_delete_first_of_two_posts_without_500_and_repeated_delete_is_404(self):
        first = Question.objects.create(
            contribution=self.contribution,
            question_text="First delete regression question",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=1,
        )
        second = Question.objects.create(
            contribution=self.contribution,
            question_text="Second delete regression question",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=2,
        )
        delete_url = reverse(
            "departmental_exams:question_delete",
            args=[self.contribution.id, first.id],
        )
        payload = {
            "expected_contribution_revision": self.contribution.revision,
            "expected_question_revision": first.revision,
        }

        response = self.client.post(delete_url, payload)

        self.assertRedirects(
            response,
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            ),
            fetch_redirect_response=False,
        )
        self.contribution.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(Question.objects.filter(pk=first.id).exists())
        self.assertEqual(second.position, 1)
        self.assertEqual(self.contribution.revision, payload["expected_contribution_revision"] + 1)

        repeated = self.client.post(
            delete_url,
            {
                "expected_contribution_revision": self.contribution.revision,
                "expected_question_revision": first.revision,
            },
        )
        self.assertEqual(repeated.status_code, 404)

    def test_template_download_content_type_disposition_and_exact_header(self):
        response = self.client.get(
            reverse("departmental_exams:csv_template", args=[self.contribution.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("TeacherMatePlus_Departmental_Exam_Questions.csv", response["Content-Disposition"])
        self.assertEqual(
            tuple(next(csv.reader(io.StringIO(response.content.decode("utf-8"))))),
            (
                "question_text",
                "choice_a",
                "choice_b",
                "choice_c",
                "choice_d",
                "correct_answer",
                "difficulty",
            ),
        )

    def test_faculty_menu_visible_for_blocked_contribution(self):
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        from .contribution_services import ContributionRosterService

        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        response = self.client.get(reverse("departmental_exams:contribution_list"))
        codes = [
            node["item"].code
            for group in response.context["portal_menu"]
            for node in group["items"]
        ]
        self.assertIn("DE_EXAM_FACULTY_CONTRIBUTIONS", codes)

    def test_legacy_null_scoped_assignment_exposes_navigation_and_synchronized_list(self):
        self.contribution.delete()
        self.assignment.tenant = None
        self.assignment.campus = None
        self.assignment.save(update_fields=["tenant", "campus", "updated_at"])

        eligible_without_roster = self.client.get(
            reverse("departmental_exams:contribution_list")
        )
        self.assertEqual(eligible_without_roster.status_code, 200)
        self.assertContains(eligible_without_roster, "No contribution roster record is available yet")
        self.assertIn(
            "DE_EXAM_FACULTY_CONTRIBUTIONS",
            [
                node["item"].code
                for group in eligible_without_roster.context["portal_menu"]
                for node in group["items"]
            ],
        )

        result = ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.assertEqual(result["created"], 1)
        contribution = FacultyContribution.objects.get(faculty_user=self.faculty)

        synchronized = self.client.get(reverse("departmental_exams:contribution_list"))
        self.assertEqual(synchronized.status_code, 200)
        self.assertContains(synchronized, self.parent.course.code)
        self.assertContains(synchronized, f"0 / {contribution.quota_snapshot}")

    def test_bootstrap_forms_render_bound_errors_checkboxes_and_csrf(self):
        create_url = reverse(
            "departmental_exams:question_create", args=[self.contribution.id]
        )
        question_get = self.client.get(create_url)
        self.assertEqual(question_get.status_code, 200)
        question_form = question_get.context["form"]
        self.assertIn("form-control", question_form.fields["question_text"].widget.attrs["class"])
        self.assertIn("form-select", question_form.fields["correct_answer"].widget.attrs["class"])
        self.assertContains(question_get, 'name="csrfmiddlewaretoken"')
        self.assertEqual(
            question_get.content.decode().count("data-scientific-field"),
            5,
        )
        self.assertEqual(
            question_get.content.decode().count("data-scientific-preview"),
            5,
        )
        self.assertContains(question_get, "vendor/katex/0.18.4/katex.min.css")
        self.assertContains(question_get, "vendor/katex/0.18.4/katex.min.js")
        self.assertContains(question_get, "vendor/katex/0.18.4/contrib/mhchem.min.js")
        self.assertContains(question_get, "departmental_exam_scientific_notation.js")

        question_invalid = self.client.post(
            create_url,
            {"expected_contribution_revision": self.contribution.revision},
        )
        self.assertEqual(question_invalid.status_code, 400)
        invalid_form = question_invalid.context["form"]
        self.assertIn("is-invalid", invalid_form.fields["question_text"].widget.attrs["class"])
        self.assertEqual(
            invalid_form.fields["question_text"].widget.attrs["aria-invalid"], "true"
        )
        self.assertContains(question_invalid, "invalid-feedback d-block", status_code=400)

        upload = self.client.get(
            reverse("departmental_exams:csv_upload", args=[self.contribution.id])
        )
        self.assertIn("form-control", upload.context["form"].fields["csv_file"].widget.attrs["class"])
        self.assertContains(upload, 'name="csrfmiddlewaretoken"')

        submit = self.client.get(
            reverse("departmental_exams:contribution_submit", args=[self.contribution.id])
        )
        self.assertContains(submit, 'class="form-check mb-3"')
        self.assertIn(
            "form-check-input",
            submit.context["form"].fields["confirm_exact_quota"].widget.attrs["class"],
        )
        self.assertContains(submit, 'name="csrfmiddlewaretoken"')

        self.client.force_login(self.configurer)
        roster = self.client.get(
            reverse(
                "departmental_exams:roster_action",
                args=[self.parent.id, "synchronize"],
            )
        )
        self.assertEqual(roster.status_code, 200)
        self.assertContains(roster, 'class="form-check mb-3"')
        self.assertIn(
            "form-check-input", roster.context["form"].fields["confirm"].widget.attrs["class"]
        )
        self.assertContains(roster, 'name="csrfmiddlewaretoken"')

    def test_manual_html_and_link_shaped_content_stays_literal_in_workspace_and_delete(self):
        payload = self.valid_question_post(self.contribution.revision)
        payload.update(
            {
                "question_text": r'<script>alert(1)</script> \(\frac{x}{y}\)',
                "choice_a": '<a href="javascript:alert(1)">A</a>',
                "choice_b": "data:text/html,<img src=x onerror=alert(1)>",
                "choice_c": "vbscript:msgbox(1)",
                "choice_d": r"\(\ce{H2O}\)",
            }
        )
        created = self.client.post(
            reverse("departmental_exams:question_create", args=[self.contribution.id]),
            payload,
        )
        self.assertEqual(created.status_code, 302)
        question = self.contribution.questions.get()
        responses = (
            self.client.get(
                reverse(
                    "departmental_exams:contribution_workspace",
                    args=[self.contribution.id],
                )
            ),
            self.client.get(
                reverse(
                    "departmental_exams:question_delete",
                    args=[self.contribution.id, question.id],
                )
            ),
        )
        for response in responses:
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertIn(escape(payload["question_text"]), body)
                self.assertNotIn("<script>alert(1)</script>", body)
                self.assertNotIn('<a href="javascript:alert(1)">', body.lower())
                self.assertNotIn('href="data:', body.lower())
                self.assertNotIn('href="vbscript:', body.lower())
                self.assertIn("data-scientific-content", body)
                self.assertIn("departmental_exam_scientific_notation.js", body)

    def test_conflict_expiry_and_malformed_responses_use_safe_faculty_shell(self):
        question = Question.objects.create(
            contribution=self.contribution,
            question_text="PRIVATE STALE QUESTION",
            choice_a="A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=1,
        )
        stale = self.client.post(
            reverse(
                "departmental_exams:question_edit",
                args=[self.contribution.id, question.id],
            ),
            {
                "expected_contribution_revision": self.contribution.revision + 10,
                "expected_question_revision": question.revision,
                "question_text": "Replacement",
                "choice_a": "A",
                "choice_b": "B",
                "choice_c": "C",
                "choice_d": "D",
                "correct_answer": "A",
                "difficulty": "EASY",
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertTemplateUsed(stale, "departmental_exams/faculty/error.html")
        self.assertTemplateUsed(stale, "faculty_portal/base.html")
        self.assertContains(stale, 'class="alert alert-danger', status_code=409)
        self.assertNotContains(stale, "PRIVATE STALE QUESTION", status_code=409)

        malformed = self.client.post(
            reverse(
                "departmental_exams:question_reorder", args=[self.contribution.id]
            ),
            {},
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertTemplateUsed(malformed, "faculty_portal/base.html")
        self.assertContains(malformed, 'class="alert alert-danger', status_code=400)

        batch = QuestionImportBatch.objects.create(
            tenant=self.tenant,
            contribution=self.contribution,
            uploading_user=self.faculty,
            status="READY",
            contribution_revision_snapshot=self.contribution.revision,
            file_sha256="a" * 64,
            filename_sha256="b" * 64,
            total_rows=1,
            valid_rows=1,
            resulting_question_count=1,
            expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        QuestionImportRow.objects.create(
            batch=batch,
            row_number=2,
            payload={"question_text": "PRIVATE EXPIRED PAYLOAD"},
        )
        expired = self.client.get(
            reverse("departmental_exams:csv_preview", args=[batch.token])
        )
        self.assertEqual(expired.status_code, 410)
        self.assertTemplateUsed(expired, "faculty_portal/base.html")
        self.assertContains(expired, 'class="alert alert-danger', status_code=410)
        self.assertNotContains(expired, str(batch.token), status_code=410)
        self.assertNotContains(expired, "PRIVATE EXPIRED PAYLOAD", status_code=410)

    def test_csv_preview_uses_responsive_summary_table_and_actions(self):
        batch = QuestionImportBatch.objects.create(
            tenant=self.tenant,
            contribution=self.contribution,
            uploading_user=self.faculty,
            status="READY",
            contribution_revision_snapshot=self.contribution.revision,
            file_sha256="c" * 64,
            filename_sha256="d" * 64,
            total_rows=1,
            valid_rows=1,
            warning_count=1,
            resulting_question_count=1,
            expires_at=timezone.now() + timezone.timedelta(minutes=30),
        )
        QuestionImportRow.objects.create(
            batch=batch,
            row_number=2,
            payload={
                "question_text": "Responsive question",
                "choice_a": "A",
                "choice_b": "B",
                "choice_c": "C",
                "choice_d": "D",
                "correct_answer": "A",
                "difficulty": "EASY",
            },
            warnings=[{"message": "Review this row."}],
        )
        response = self.client.get(
            reverse("departmental_exams:csv_preview", args=[batch.token])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "col-6 col-md-3 col-xl", count=8)
        self.assertContains(response, 'class="table-responsive"')
        self.assertContains(response, "d-flex flex-wrap gap-2")
        self.assertContains(response, "alert alert-warning")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertEqual(
            _active_menu_codes(response), ["DE_EXAM_FACULTY_CONTRIBUTIONS"]
        )

    def test_faculty_workflow_routes_keep_only_contribution_menu_active(self):
        routes = (
            reverse("departmental_exams:contribution_list"),
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            ),
            reverse(
                "departmental_exams:question_create", args=[self.contribution.id]
            ),
            reverse("departmental_exams:csv_upload", args=[self.contribution.id]),
            reverse(
                "departmental_exams:contribution_submit",
                args=[self.contribution.id],
            ),
        )
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    _active_menu_codes(response),
                    ["DE_EXAM_FACULTY_CONTRIBUTIONS"],
                )

        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        from .contribution_services import ContributionRosterService

        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        blocked = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )
        self.assertEqual(
            _active_menu_codes(blocked), ["DE_EXAM_FACULTY_CONTRIBUTIONS"]
        )

        FacultyContribution.objects.filter(pk=self.contribution.pk).update(
            roster_status="ACTIVE",
            roster_blocked_at=None,
            status="SUBMITTED",
            submitted_at=timezone.now(),
        )
        submitted = self.client.get(
            reverse(
                "departmental_exams:contribution_workspace",
                args=[self.contribution.id],
            )
        )
        self.assertEqual(
            _active_menu_codes(submitted), ["DE_EXAM_FACULTY_CONTRIBUTIONS"]
        )

    def test_feature_off_denies_direct_route_and_hides_menu(self):
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        request = RequestFactory().get("/faculty/")
        request.user = self.faculty
        request.scope = {"tenant_id": self.tenant.id, "campus_id": self.campus.id}
        context = portal_menu(request)
        codes = [
            node["item"].code
            for group in context["portal_menu"]
            for node in group["items"]
        ]
        self.assertNotIn("DE_EXAM_FACULTY_CONTRIBUTIONS", codes)
        self.assertEqual(
            self.client.get(reverse("departmental_exams:contribution_list")).status_code,
            403,
        )


class Stage5MonitoringAndCleanupTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("monitor-faculty")
        self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get()

    def test_monitoring_is_exact_scoped_aggregate_only(self):
        Question.objects.create(
            contribution=self.contribution,
            question_text="MONITOR MUST NOT FETCH THIS SECRET",
            choice_a="SECRET A",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=1,
        )
        self.client.force_login(self.configurer)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("departmental_exams:contributor_monitoring"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.faculty.full_name or self.faculty.username)
        self.assertNotContains(response, "MONITOR MUST NOT FETCH THIS SECRET")
        self.assertNotContains(response, "SECRET A")
        sql = "\n".join(query["sql"] for query in captured.captured_queries).lower()
        self.assertNotIn("question_text", sql)
        self.assertNotIn("correct_answer", sql)

    def test_manage_cycles_only_user_is_denied_monitoring(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("departmental_exams:contributor_monitoring"))
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "departmental_exams/admin/error.html")
        self.assertTemplateUsed(response, "admin_portal/base.html")
        self.assertContains(response, 'class="alert alert-danger', status_code=403)

    def test_admin_departmental_exam_routes_have_one_intended_active_item(self):
        cases = (
            (
                self.configurer,
                reverse("departmental_exams:contributor_monitoring"),
                "DE_EXAM_CONTRIBUTOR_MONITORING",
            ),
            (
                self.configurer,
                reverse(
                    "departmental_exams:roster_action",
                    args=[self.parent.id, "synchronize"],
                ),
                "DE_EXAM_CONTRIBUTOR_MONITORING",
            ),
            (
                self.manager,
                reverse("departmental_exams:cycle_list"),
                "DE_EXAM_CYCLES",
            ),
            (
                self.configurer,
                reverse("departmental_exams:assigned_course_examinations"),
                "DE_EXAM_ASSIGNED_COURSES",
            ),
        )
        departmental_codes = {
            "DE_EXAM_CYCLES",
            "DE_EXAM_ASSIGNED_COURSES",
            "DE_EXAM_CONTRIBUTOR_MONITORING",
        }
        for user, route, expected in cases:
            with self.subTest(route=route):
                self.client.force_login(user)
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                active = [
                    code
                    for code in _active_menu_codes(response)
                    if code in departmental_codes
                ]
                self.assertEqual(active, [expected])

    def test_assigned_reviewer_can_monitor_but_cannot_run_roster_action(self):
        self.parent.reviewer = self.reviewer
        self.parent.save(update_fields=["reviewer", "updated_at"])
        self.client.force_login(self.reviewer)
        self.assertEqual(
            self.client.get(reverse("departmental_exams:contributor_monitoring")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:roster_action", args=[self.parent.id, "synchronize"])
            ).status_code,
            403,
        )

    def test_cleanup_expires_preview_purges_payload_and_reports_only_counts(self):
        batch = QuestionImportBatch.objects.create(
            tenant=self.tenant,
            contribution=self.contribution,
            uploading_user=self.faculty,
            status="READY",
            contribution_revision_snapshot=self.contribution.revision,
            file_sha256="a" * 64,
            filename_sha256="b" * 64,
            total_rows=1,
            valid_rows=1,
            resulting_question_count=1,
            expires_at=timezone.now() - timezone.timedelta(minutes=1),
        )
        QuestionImportRow.objects.create(
            batch=batch,
            row_number=2,
            payload={"question_text": "CLEANUP SECRET"},
        )
        output = io.StringIO()
        call_command(
            "purge_expired_question_import_previews",
            batch_size=10,
            stdout=output,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.status, "EXPIRED")
        self.assertFalse(batch.rows.exists())
        self.assertNotIn("CLEANUP SECRET", output.getvalue())
        self.assertIn("Expired batches: 1", output.getvalue())
