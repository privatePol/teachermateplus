import re

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from apps.academics.models import AcademicYear, Course, Term
from apps.rbac.models import UserRole
from apps.tenants.models import Campus, Department, Tenant

from .contribution_services import ContributionRosterService
from .models import (
    CycleCourse,
    ExaminationCycle,
    FacultyContribution,
    Question,
)
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


class ContributorMonitoringPrintTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("print-faculty")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.assignment.assignment_note = "CONFIDENTIAL SOURCE IDENTITY"
        self.assignment.save(update_fields=["assignment_note", "updated_at"])
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(cycle_course=self.parent)
        self.url = reverse("departmental_exams:contributor_monitoring_print")

    def make_authorized_course(self, code):
        parent = self.make_course(cycle=self.parent.cycle, code=code)
        self.make_configuration(
            parent,
            workflow="OPEN",
            opened_at=timezone.now(),
            deadline=self.future_deadline(),
        )
        return parent

    def make_wrong_tenant_course(self):
        tenant = Tenant.objects.create(code="PRINT-OTHER", name="Print Other Tenant")
        year = AcademicYear.objects.create(
            tenant=tenant,
            code="PRINT-AY",
            name="Print AY",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        term = Term.objects.create(
            tenant=tenant,
            academic_year=year,
            code="PRINT-T1",
            name="Print Term",
        )
        course = Course.objects.create(
            tenant=tenant,
            code="WRONG-TENANT-COURSE",
            title="Wrong Tenant Course",
        )
        cycle = ExaminationCycle.objects.create(
            tenant=tenant,
            academic_year=year,
            term=term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            created_by=self.admin,
        )
        return CycleCourse.objects.create(cycle=cycle, course=course)

    def add_other_campus_role(self):
        existing = UserRole.objects.get(user=self.faculty, campus=self.campus)
        UserRole.objects.create(
            user=self.faculty,
            role=existing.role,
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
        )

    def add_question(self, position, text):
        return Question.objects.create(
            contribution=self.contribution,
            question_text=text,
            choice_a="CONFIDENTIAL CHOICE",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty="EASY",
            position=position,
        )

    def test_access_scope_and_current_filters_are_shared_with_monitoring(self):
        second = self.make_authorized_course("PRINT-SECOND")
        hidden = self.make_course(
            cycle=self.parent.cycle,
            department=self.other_department,
            code="UNAUTHORIZED-COURSE",
        )
        self.make_configuration(
            hidden,
            workflow="OPEN",
            opened_at=timezone.now(),
            deadline=self.future_deadline(),
        )
        wrong_tenant = self.make_wrong_tenant_course()

        self.client.force_login(self.configurer)
        filters = {
            "cycle": self.parent.cycle_id,
            "period": ExaminationCycle.ExamPeriod.MIDTERM,
            "course": second.course_id,
        }
        response = self.client.get(self.url, filters)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_cycle_id"], self.parent.cycle_id)
        self.assertEqual(response.context["selected_period"], "MIDTERM")
        self.assertEqual(response.context["selected_course_id"], second.course_id)
        self.assertEqual([course.id for course in response.context["courses"]], [second.id])
        self.assertNotContains(response, hidden.course.code)
        self.assertNotContains(response, wrong_tenant.course.code)
        self.assertEqual(response.context["total_authorized_courses"], 1)
        self.assertEqual(response.context["total_course_offerings"], 1)

        self.client.force_login(self.manager)
        denied = self.client.get(self.url)
        self.assertEqual(denied.status_code, 403)

    def test_interactive_print_link_preserves_only_valid_current_filters(self):
        self.client.force_login(self.configurer)
        filters = {
            "cycle": self.parent.cycle_id,
            "period": ExaminationCycle.ExamPeriod.MIDTERM,
            "course": self.parent.course_id,
        }
        response = self.client.get(
            reverse("departmental_exams:contributor_monitoring"), filters
        )
        expected = (
            f'{self.url}?cycle={self.parent.cycle_id}&amp;period=MIDTERM'
            f'&amp;course={self.parent.course_id}'
        )
        self.assertContains(response, expected)
        self.assertContains(response, "Print / Printer-Friendly View")
        self.assertContains(response, 'target="_blank"')

    def test_summary_totals_numbering_deadline_columns_and_course_totals(self):
        self.add_question(1, "CONFIDENTIAL QUESTION ONE")
        self.add_question(2, "CONFIDENTIAL QUESTION TWO")
        second = self.make_authorized_course("ZZZ-PRINT")
        self.add_grouped_offering(
            self.parent,
            campus=self.other_campus,
            department=self.other_department,
            slug="represented-north",
        )

        self.client.force_login(self.configurer)
        response = self.client.get(self.url)

        self.assertEqual(response.context["total_authorized_courses"], 2)
        self.assertEqual(response.context["total_course_offerings"], 3)
        self.assertEqual(
            [course.print_number for course in response.context["courses"]], [1, 2]
        )
        report_parent = next(
            course for course in response.context["courses"] if course.id == self.parent.id
        )
        self.assertEqual(report_parent.total_contributors, 1)
        self.assertEqual(report_parent.total_questions_saved, 2)
        self.assertEqual(report_parent.total_questions_required, 50)
        self.assertContains(response, "Contribution Deadline:")
        self.assertContains(response, "Course Totals: Contributors: 1")
        self.assertContains(response, "Questions: 2 / 50")

        html = response.content.decode()
        headers = [
            strip_tags(value).strip()
            for value in re.findall(r"<th[^>]*>(.*?)</th>", html, flags=re.DOTALL)
        ]
        self.assertEqual(
            headers,
            ["Contributor", "Campus", "Section", "Progress", "Sources"] * 2,
        )
        self.assertNotIn("<th>Workflow</th>", html)
        self.assertNotIn("<th>Roster</th>", html)
        self.assertNotIn("<th>Deadline</th>", html)
        self.assertNotIn("Blocked Draft Resolution", html)
        report_second = next(
            course for course in response.context["courses"] if course.id == second.id
        )
        self.assertEqual(report_second.represented_offering_count, 1)

    def test_campus_and_section_use_only_current_exact_assignment_sources(self):
        first_offering = self.parent.offering_snapshots.select_related(
            "offering__section"
        ).get().offering
        first_offering.section.code = "BSA-2A"
        first_offering.section.save(update_fields=["code", "updated_at"])
        self.add_other_campus_role()

        north_a = self.add_grouped_offering(
            self.parent,
            campus=self.other_campus,
            department=self.other_department,
            slug="north-a",
        )
        north_a.section.code = "BSA-2A"
        north_a.section.save(update_fields=["code", "updated_at"])
        self.make_assignment(
            self.parent,
            self.faculty,
            campus=self.other_campus,
            offering=north_a,
        )
        north_b = self.add_grouped_offering(
            self.parent,
            campus=self.other_campus,
            department=self.other_department,
            slug="north-b",
        )
        north_b.section.code = "BSA-2B"
        north_b.section.save(update_fields=["code", "updated_at"])
        self.make_assignment(
            self.parent,
            self.faculty,
            campus=self.other_campus,
            offering=north_b,
        )

        remote = Campus.objects.create(
            tenant=self.tenant, code="REMOTE", name="Remote"
        )
        remote_department = Department.objects.create(
            tenant=self.tenant,
            campus=remote,
            code="REMOTE",
            name="Remote Department",
        )
        unrelated = self.add_grouped_offering(
            self.parent,
            campus=remote,
            department=remote_department,
            slug="unrelated",
        )
        unrelated.section.code = "PRIVATE-9Z"
        unrelated.section.save(update_fields=["code", "updated_at"])
        invalid_assignment = self.make_assignment(
            self.parent,
            self.faculty,
            campus=remote,
            offering=unrelated,
        )
        invalid_assignment.is_active = False
        invalid_assignment.save(update_fields=["is_active", "updated_at"])
        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )

        self.client.force_login(self.configurer)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url)
        report_contribution = response.context["courses"][0].faculty_contributions.all()[0]
        self.assertEqual(report_contribution.print_campus_names, ["Main", "North"])
        self.assertEqual(report_contribution.print_section_codes, ["BSA-2A", "BSA-2B"])
        self.assertNotIn("Remote", report_contribution.print_campus_names)
        self.assertNotIn("PRIVATE-9Z", report_contribution.print_section_codes)
        self.assertEqual(report_contribution.valid_source_count, 3)
        self.assertEqual(report_contribution.invalid_source_count, 1)

        source_queries = [
            query
            for query in captured.captured_queries
            if "departmental_exam_contribution_sources" in query["sql"].lower()
        ]
        self.assertEqual(len(source_queries), 1)
        self.assertLessEqual(len(captured), 65)

    def test_explanations_confidentiality_timestamp_and_print_css(self):
        self.add_question(1, "CONFIDENTIAL QUESTION TEXT")
        self.client.force_login(self.configurer)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url)

        self.assertContains(response, "Progress shows the number of questions currently saved/credited")
        self.assertContains(response, "Progress alone does not necessarily mean Final Submission has been completed")
        self.assertContains(response, "Sources are the teaching-assignment records")
        self.assertContains(response, "qualifying source/assignment accepted by TMP")
        self.assertContains(response, "evaluated but rejected by the existing eligibility rules")
        self.assertContains(response, "Source counts are not question counts")
        self.assertNotContains(response, "CONFIDENTIAL QUESTION TEXT")
        self.assertNotContains(response, "CONFIDENTIAL CHOICE")
        self.assertNotContains(response, "CONFIDENTIAL SOURCE IDENTITY")
        self.assertNotContains(response, "assignment_id_snapshot")
        self.assertNotContains(response, "offering_id_snapshot")
        sql = "\n".join(query["sql"] for query in captured.captured_queries).lower()
        self.assertNotIn("question_text", sql)
        self.assertNotIn("correct_answer", sql)
        self.assertNotIn("assignment_note", sql)
        self.assertNotIn("faculty_response_note", sql)
        self.assertEqual(response.context["generated_at"].tzinfo.key, "Asia/Manila")
        self.assertContains(response, 'onclick="window.print()"')
        self.assertContains(response, "@media print")
        self.assertContains(response, ".print-controls { display: none !important; }")
        self.assertNotContains(response, "Synchronize roster")
        self.assertNotContains(response, "Initialize roster")
