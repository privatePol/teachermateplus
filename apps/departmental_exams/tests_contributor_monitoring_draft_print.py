import re

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import strip_tags

from apps.academics.models import AcademicYear, Course, Term
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission
from apps.tenants.models import Campus

from .contribution_services import ContributionRosterService
from .models import CycleCourse, ExaminationCycle, FacultyContribution, Question
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


class ContributorMonitoringDraftPrintTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        SystemSettingService.set(
            "PRINT_HEADER_SCHOOL_NAME",
            "NATIONAL COLLEGE OF BUSINESS AND ARTS",
            tenant_id=self.tenant.id,
            value_type="STRING",
        )
        SystemSettingService.set(
            "PRINT_HEADER_SCHOOL_ADDRESS",
            "994 Aurora Blvd., Cubao, Quezon City",
            tenant_id=self.tenant.id,
            value_type="STRING",
        )
        self.parent, self.configuration = self.make_stage5_course()
        self.faculty = self.make_faculty("draft-report-faculty")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(
            cycle_course=self.parent,
            faculty_user=self.faculty,
        )
        self.url = reverse(
            "departmental_exams:contributor_monitoring_draft_print",
            args=[self.parent.cycle_id],
        )

    def make_visible_contribution(
        self, code, *, cycle=None, username=None, faculty=None
    ):
        parent = self.make_course(cycle=cycle or self.parent.cycle, code=code)
        configuration = self.make_configuration(
            parent,
            workflow="OPEN",
            opened_at=timezone.now(),
            deadline=self.future_deadline(),
        )
        faculty = faculty or self.make_faculty(username or f"faculty-{parent.id}")
        self.make_assignment(parent, faculty)
        ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        return (
            parent,
            configuration,
            FacultyContribution.objects.get(cycle_course=parent),
        )

    def test_authorized_monitoring_button_targets_the_selected_cycle(self):
        self.client.force_login(self.configurer)

        response = self.client.get(
            reverse("departmental_exams:contributor_monitoring"),
            {"cycle": self.parent.cycle_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Print All Draft Contributions")
        self.assertContains(response, f'href="{self.url}"')
        self.assertContains(response, 'target="_blank"')

    def test_report_is_cycle_bound_draft_only_included_and_exact_scope(self):
        self.faculty.last_name = "Dela Cruz"
        self.faculty.first_name = "Juan"
        self.faculty.save(update_fields=["last_name", "first_name", "updated_at"])
        Question.objects.create(
            contribution=self.contribution,
            question_text="CONFIDENTIAL DRAFT QUESTION",
            choice_a="CONFIDENTIAL ANSWER",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty=Question.Difficulty.EASY,
            position=1,
        )
        submitted_parent, _submitted_configuration, submitted = (
            self.make_visible_contribution(
                "SUBMITTED-REPORT", username="submitted-report-faculty"
            )
        )
        submitted.status = FacultyContribution.Status.SUBMITTED
        submitted.submitted_at = timezone.now()
        submitted.save(update_fields=["status", "submitted_at", "updated_at"])

        exempt_parent, _exempt_configuration, _exempt = self.make_visible_contribution(
            "EXEMPT-REPORT", username="exempt-report-faculty"
        )
        CycleCourse.objects.filter(pk=exempt_parent.pk).update(
            inclusion_status=CycleCourse.InclusionStatus.EXEMPT,
            exemption_category=CycleCourse.ExemptionCategory.OTHER_OUTPUT_BASED,
            exemption_reason="Approved non-examination output requirement.",
            exemption_changed_by=self.configurer,
            exemption_changed_at=timezone.now(),
        )

        other_cycle = self.make_cycle(
            status=ExaminationCycle.Status.OPEN,
            default_questions_required_per_faculty=50,
            default_final_item_count=50,
            default_contribution_deadline=self.future_deadline(),
            scope_suffix="DRAFT-PRINT-OTHER",
        )
        other_cycle_parent, _other_configuration, _other_contribution = (
            self.make_visible_contribution(
                "OTHER-CYCLE",
                cycle=other_cycle,
                username="other-cycle-report-faculty",
            )
        )

        hidden_parent = self.make_course(
            cycle=self.parent.cycle,
            department=self.other_department,
            code="HIDDEN-DEPARTMENT",
        )
        hidden_configuration = self.make_configuration(
            hidden_parent,
            workflow="OPEN",
            opened_at=timezone.now(),
            deadline=self.future_deadline(),
        )
        hidden_faculty = self.make_faculty("hidden-report-faculty")
        hidden_assignment = self.make_assignment(hidden_parent, hidden_faculty)
        FacultyContribution.objects.create(
            cycle_course=hidden_parent,
            faculty_user=hidden_faculty,
            source_assignment=hidden_assignment,
            source_campus=hidden_assignment.campus,
            quota_snapshot=50,
            configuration_revision_snapshot=hidden_configuration.revision,
        )

        other_tenant_campus = Campus.objects.create(
            tenant=self.other_tenant,
            code="DRAFT-PRINT-OTHER-CAMPUS",
            name="Draft Print Other Campus",
        )
        other_tenant_year = AcademicYear.objects.create(
            tenant=self.other_tenant,
            code="DRAFT-PRINT-OTHER-TENANT-AY",
            name="Draft Print Other Tenant AY",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        other_tenant_term = Term.objects.create(
            tenant=self.other_tenant,
            academic_year=other_tenant_year,
            code="DRAFT-PRINT-OTHER-TENANT-T1",
            name="Draft Print Other Tenant Term",
        )
        other_tenant_cycle = ExaminationCycle.objects.create(
            tenant=self.other_tenant,
            academic_year=other_tenant_year,
            term=other_tenant_term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            created_by=self.admin,
        )
        other_tenant_course = Course.objects.create(
            tenant=self.other_tenant,
            code="OTHER-TENANT-COURSE",
            title="Other Tenant Course",
        )
        other_tenant_parent = CycleCourse.objects.create(
            cycle=other_tenant_cycle,
            course=other_tenant_course,
        )
        other_tenant_faculty = get_user_model().objects.create_user(
            username="other-tenant-draft-faculty",
            email="other-tenant-draft-faculty@example.edu",
            password="Pass123!",
            default_tenant=self.other_tenant,
            default_campus=other_tenant_campus,
            first_name="Olivia",
            last_name="OtherTenant",
        )
        FacultyContribution.objects.create(
            cycle_course=other_tenant_parent,
            faculty_user=other_tenant_faculty,
            source_campus=other_tenant_campus,
            quota_snapshot=50,
            configuration_revision_snapshot=1,
        )

        self.client.force_login(self.configurer)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [course.id for course in response.context["courses"]],
            [self.parent.id],
        )
        self.assertEqual(
            [
                (row["display_name"], row["draft_count"])
                for row in response.context["faculty_summary"]
            ],
            [("Dela Cruz, Juan", 1)],
        )
        self.assertContains(response, self.parent.course.code)
        self.assertContains(response, self.faculty.full_name)
        self.assertContains(response, "1 / 50 (2%)")
        self.assertContains(response, "<td>DRAFT</td>", html=True)
        self.assertContains(response, self.campus.name)
        expected_deadline = date_format(
            timezone.localtime(self.configuration.active_contribution_deadline),
            "M j, Y g:i A",
        )
        self.assertContains(response, expected_deadline)
        self.assertNotContains(response, submitted_parent.course.code)
        self.assertNotContains(response, "submitted-report-faculty")
        self.assertNotContains(response, exempt_parent.course.code)
        self.assertNotContains(response, "exempt-report-faculty")
        self.assertNotContains(response, other_cycle_parent.course.code)
        self.assertNotContains(response, "other-cycle-report-faculty")
        self.assertNotContains(response, hidden_parent.course.code)
        self.assertNotContains(response, "hidden-report-faculty")
        self.assertNotContains(response, "OtherTenant, Olivia")
        self.assertNotContains(response, "CONFIDENTIAL DRAFT QUESTION")
        self.assertNotContains(response, "CONFIDENTIAL ANSWER")
        sql = "\n".join(query["sql"] for query in captured.captured_queries).lower()
        self.assertNotIn("question_text", sql)
        self.assertNotIn("correct_answer", sql)

    def test_course_and_faculty_summary_numbering_follow_stable_sorted_order(self):
        self.faculty.last_name = "Zulu"
        self.faculty.first_name = "Zoe"
        self.faculty.save(update_fields=["last_name", "first_name", "updated_at"])
        first_parent, _configuration, first = self.make_visible_contribution(
            "AAA-REPORT", username="first-numbered-faculty"
        )
        first.faculty_user.last_name = "adams"
        first.faculty_user.first_name = "Amy"
        first.faculty_user.save(
            update_fields=["last_name", "first_name", "updated_at"]
        )
        last_parent, _configuration, _last = self.make_visible_contribution(
            "ZZZ-REPORT", faculty=self.faculty
        )
        duplicate_offering = self.add_grouped_offering(
            self.parent,
            campus=self.campus,
            department=self.department,
            slug="DRAFT-NUMBER-DUPLICATE",
        )
        self.make_assignment(
            self.parent,
            self.faculty,
            offering=duplicate_offering,
        )
        ContributionRosterService.synchronize(
            cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id,
            actor=self.configurer,
        )
        self.assertEqual(self.contribution.eligibility_sources.count(), 2)
        self.client.force_login(self.configurer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [course.id for course in response.context["courses"]],
            [first_parent.id, self.parent.id, last_parent.id],
        )
        content = response.content.decode()
        course_headings = [
            f"{number}. {course.course.code} &mdash; {course.course.title}"
            for number, course in enumerate(response.context["courses"], start=1)
        ]
        self.assertEqual(
            [content.index(heading) for heading in course_headings],
            sorted(content.index(heading) for heading in course_headings),
        )
        self.assertEqual(
            [
                (row["display_name"], row["draft_count"])
                for row in response.context["faculty_summary"]
            ],
            [("adams, Amy", 1), ("Zulu, Zoe", 2)],
        )
        self.assertContains(response, "Draft Summary by Faculty")
        self.assertContains(response, '<ol class="faculty-summary-list">')
        self.assertContains(response, '<li class="faculty-summary-entry">')
        self.assertContains(response, "1. adams, Amy &mdash; 1 Draft", html=False)
        self.assertContains(response, "2. Zulu, Zoe &mdash; 2 Draft", html=False)

    def test_required_columns_brand_cycle_timestamp_and_print_css_render(self):
        self.client.force_login(self.configurer)

        response = self.client.get(self.url)

        headers = [
            strip_tags(value).strip()
            for value in re.findall(
                r"<th[^>]*>(.*?)</th>",
                response.content.decode(),
                flags=re.DOTALL,
            )
        ]
        self.assertEqual(
            headers,
            ["Contributor", "Campus", "Progress", "Workflow", "Deadline"],
        )
        self.assertNotIn("Course", headers)
        self.assertNotIn("Section", headers)
        self.assertContains(response, "NATIONAL COLLEGE OF BUSINESS AND ARTS")
        self.assertContains(response, "994 Aurora Blvd., Cubao, Quezon City")
        self.assertContains(response, "/media/logos/ncba-logo.png")
        self.assertContains(response, "Examination Cycle:")
        self.assertContains(response, str(self.parent.cycle.academic_year))
        self.assertContains(response, str(self.parent.cycle.term))
        self.assertContains(response, self.parent.cycle.get_exam_period_display())
        self.assertContains(response, "Date and time printed:")
        self.assertContains(response, "(Asia/Manila)")
        self.assertEqual(response.context["generated_at"].tzinfo.key, "Asia/Manila")
        self.assertContains(response, "@media print")
        self.assertContains(response, "thead { display: table-header-group; }")
        self.assertContains(response, "page-break-inside: avoid")
        self.assertContains(response, "column-count: 3")
        self.assertContains(response, "column-count: 2")
        self.assertContains(response, "column-count: 1")
        self.assertContains(response, "column-fill: balance")
        self.assertContains(response, "font-size: 9pt")
        self.assertContains(response, ".print-controls { display: none !important; }")

    def test_empty_result_has_clear_message(self):
        self.contribution.status = FacultyContribution.Status.SUBMITTED
        self.contribution.submitted_at = timezone.now()
        self.contribution.save(update_fields=["status", "submitted_at", "updated_at"])
        self.client.force_login(self.configurer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["courses"], [])
        self.assertContains(response, "No draft contributions found")
        self.assertNotContains(response, "Draft Summary by Faculty")

    def test_unauthorized_wrong_tenant_and_direct_deny_access_fail_closed(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 403)

        wrong_year = AcademicYear.objects.create(
            tenant=self.other_tenant,
            code="DRAFT-PRINT-OTHER-AY",
            name="Draft Print Other AY",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        wrong_term = Term.objects.create(
            tenant=self.other_tenant,
            academic_year=wrong_year,
            code="DRAFT-PRINT-OTHER-T1",
            name="Draft Print Other Term",
        )
        wrong_cycle = ExaminationCycle.objects.create(
            tenant=self.other_tenant,
            academic_year=wrong_year,
            term=wrong_term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            created_by=self.admin,
        )
        self.client.force_login(self.configurer)
        wrong_tenant_url = reverse(
            "departmental_exams:contributor_monitoring_draft_print",
            args=[wrong_cycle.id],
        )
        self.assertEqual(self.client.get(wrong_tenant_url).status_code, 403)

        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.assertEqual(self.client.get(self.url).status_code, 403)
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )

        UserPermission.objects.create(
            user=self.configurer,
            permission=Permission.objects.get(code="departmental_exams.configure"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(self.client.get(self.url).status_code, 403)
