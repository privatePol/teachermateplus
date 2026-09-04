import re

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from apps.academics.models import AcademicYear, Term
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission

from .contribution_services import ContributionRosterService
from .models import CycleCourse, ExaminationCycle, FacultyContribution, Question
from .stage4_test_support import Stage4TestCase
from .tests_stage5_contributions import Stage5FixtureMixin


class ContributorMonitoringFacultySubmissionPrintTests(
    Stage5FixtureMixin, Stage4TestCase
):
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
        self.faculty = self.make_faculty("faculty-summary-primary")
        self._set_name(self.faculty, last_name="Zulu", first_name="Zoe")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(
            cycle_course=self.parent,
            faculty_user=self.faculty,
        )
        self.url = reverse(
            "departmental_exams:contributor_monitoring_faculty_submission_print",
            args=[self.parent.cycle_id],
        )

    @staticmethod
    def _set_name(user, *, last_name, first_name):
        user.last_name = last_name
        user.first_name = first_name
        user.save(update_fields=["last_name", "first_name", "updated_at"])

    def make_summary_contribution(
        self,
        code,
        *,
        faculty=None,
        username=None,
        status=FacultyContribution.Status.DRAFT,
        cycle=None,
        campus=None,
        responsible_department=None,
        actor=None,
    ):
        parent = self.make_course(
            cycle=cycle or self.parent.cycle,
            department=responsible_department or self.department,
            code=code,
        )
        self.make_configuration(
            parent,
            workflow="OPEN",
            opened_at=timezone.now(),
            deadline=self.future_deadline(),
        )
        offering = None
        if campus is not None and campus.id != self.campus.id:
            offering = self.add_grouped_offering(
                parent,
                campus=campus,
                department=self.other_department,
                slug=f"{code}-CAMPUS",
            )
        faculty = faculty or self.make_faculty(
            username or f"faculty-{parent.id}",
            campus=campus,
            department=(
                self.other_department
                if campus is not None and campus.id != self.campus.id
                else self.department
            ),
        )
        self.make_assignment(parent, faculty, campus=campus, offering=offering)
        ContributionRosterService.initialize(
            cycle_course_id=parent.id,
            tenant_id=self.tenant.id,
            actor=actor or self.configurer,
        )
        contribution = FacultyContribution.objects.get(
            cycle_course=parent,
            faculty_user=faculty,
        )
        if status == FacultyContribution.Status.SUBMITTED:
            contribution.status = status
            contribution.submitted_at = timezone.now()
            contribution.save(update_fields=["status", "submitted_at", "updated_at"])
        return parent, contribution

    def test_authorized_button_targets_selected_cycle(self):
        self.client.force_login(self.configurer)

        response = self.client.get(
            reverse("departmental_exams:contributor_monitoring"),
            {"cycle": self.parent.cycle_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Print Faculty Submission Summary")
        self.assertContains(response, f'href="{self.url}"')
        self.assertContains(response, 'target="_blank"')

    def test_campus_scoped_viewer_counts_distinctly_and_numbers_sorted_contributors(
        self,
    ):
        _second_draft_parent, _second_draft = self.make_summary_contribution(
            "SECOND-DRAFT", faculty=self.faculty
        )
        _submitted_parent, _submitted = self.make_summary_contribution(
            "SUBMITTED",
            faculty=self.faculty,
            status=FacultyContribution.Status.SUBMITTED,
        )
        alpha = self.make_faculty("faculty-summary-alpha")
        self._set_name(alpha, last_name="adams", first_name="Amy")
        _alpha_parent, _alpha_submitted = self.make_summary_contribution(
            "ALPHA-SUBMITTED",
            faculty=alpha,
            status=FacultyContribution.Status.SUBMITTED,
        )

        duplicate_offering = self.add_grouped_offering(
            self.parent,
            campus=self.campus,
            department=self.department,
            slug="FACULTY-SUMMARY-DUPLICATE",
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

        north = self.make_faculty(
            "faculty-summary-north",
            campus=self.other_campus,
            department=self.other_department,
        )
        self._set_name(north, last_name="North", first_name="Nora")
        self.make_summary_contribution(
            "OTHER-CAMPUS",
            faculty=north,
            campus=self.other_campus,
        )
        exempt_parent, _exempt = self.make_summary_contribution(
            "EXEMPT", faculty=self.faculty
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
            scope_suffix="FACULTY-SUMMARY-OTHER-CYCLE",
        )
        self.make_summary_contribution(
            "OTHER-CYCLE",
            faculty=self.faculty,
            cycle=other_cycle,
        )
        hidden = self.make_faculty("faculty-summary-hidden")
        self._set_name(hidden, last_name="Hidden", first_name="Hana")
        self.make_summary_contribution(
            "HIDDEN-DEPARTMENT",
            faculty=hidden,
            responsible_department=self.other_department,
            actor=self.admin,
        )
        Question.objects.create(
            contribution=self.contribution,
            question_text="CONFIDENTIAL FACULTY SUMMARY QUESTION",
            choice_a="CONFIDENTIAL FACULTY SUMMARY ANSWER",
            choice_b="B",
            choice_c="C",
            choice_d="D",
            correct_answer="A",
            difficulty=Question.Difficulty.EASY,
            position=1,
        )

        self.client.force_login(self.configurer)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [
                (
                    row["display_name"],
                    row["default_campus_name"],
                    row["draft_count"],
                    row["submitted_count"],
                    row["course_count"],
                )
                for row in response.context["summary_rows"]
            ],
            [
                ("adams, Amy", self.campus.name, 0, 1, 1),
                ("Zulu, Zoe", self.campus.name, 2, 1, 3),
            ],
        )
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
            [
                "No.",
                "Contributor",
                "Default Campus",
                "Draft",
                "Submitted",
                "Courses",
            ],
        )
        self.assertContains(
            response,
            f"<tr><td>1</td><td>adams, Amy</td><td>{self.campus.name}</td><td>0</td><td>1</td><td>1</td></tr>",
            html=True,
        )
        self.assertContains(
            response,
            f"<tr><td>2</td><td>Zulu, Zoe</td><td>{self.campus.name}</td><td>2</td><td>1</td><td>3</td></tr>",
            html=True,
        )
        self.assertNotContains(response, "Campus (Default Campus):")
        self.assertContains(response, "Default Campus")
        self.assertContains(response, self.campus.name)
        self.assertContains(response, "NATIONAL COLLEGE OF BUSINESS AND ARTS")
        self.assertContains(response, "994 Aurora Blvd., Cubao, Quezon City")
        self.assertContains(response, "/media/logos/ncba-logo.png")
        self.assertContains(response, "(Asia/Manila)")
        self.assertEqual(response.context["generated_at"].tzinfo.key, "Asia/Manila")
        self.assertNotContains(response, "North, Nora")
        self.assertNotContains(response, "Hidden, Hana")
        self.assertNotContains(response, "CONFIDENTIAL FACULTY SUMMARY QUESTION")
        self.assertNotContains(response, "CONFIDENTIAL FACULTY SUMMARY ANSWER")
        sql = "\n".join(query["sql"] for query in captured.captured_queries).lower()
        self.assertNotIn("question_text", sql)
        self.assertNotIn("correct_answer", sql)

    def test_globally_authorized_viewer_receives_authorized_multi_campus_rows(self):
        north = self.make_faculty(
            "faculty-summary-global-north",
            campus=self.other_campus,
            department=self.other_department,
        )
        self._set_name(north, last_name="North", first_name="Nora")
        self.make_summary_contribution(
            "GLOBAL-OTHER-CAMPUS",
            faculty=north,
            campus=self.other_campus,
        )
        self.client.force_login(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [
                (row["display_name"], row["default_campus_name"])
                for row in response.context["summary_rows"]
            ],
            [
                ("North, Nora", self.other_campus.name),
                ("Zulu, Zoe", self.campus.name),
            ],
        )
        self.assertContains(response, self.other_campus.name)

    def test_report_is_get_only_and_direct_access_remains_protected(self):
        self.client.force_login(self.configurer)
        self.assertEqual(self.client.post(self.url).status_code, 405)

        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url).status_code, 403)

        self.client.force_login(self.configurer)
        wrong_year = AcademicYear.objects.create(
            tenant=self.other_tenant,
            code="FACULTY-SUMMARY-OTHER-AY",
            name="Faculty Summary Other AY",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        wrong_term = Term.objects.create(
            tenant=self.other_tenant,
            academic_year=wrong_year,
            code="FACULTY-SUMMARY-OTHER-T1",
            name="Faculty Summary Other Term",
        )
        wrong_cycle = ExaminationCycle.objects.create(
            tenant=self.other_tenant,
            academic_year=wrong_year,
            term=wrong_term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            created_by=self.admin,
        )
        wrong_tenant_url = reverse(
            "departmental_exams:contributor_monitoring_faculty_submission_print",
            args=[wrong_cycle.id],
        )
        self.assertEqual(self.client.get(wrong_tenant_url).status_code, 403)

        UserPermission.objects.create(
            user=self.configurer,
            permission=Permission.objects.get(code="departmental_exams.configure"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_empty_result_is_clear_when_only_exempt_courses_exist(self):
        CycleCourse.objects.filter(pk=self.parent.pk).update(
            inclusion_status=CycleCourse.InclusionStatus.EXEMPT,
            exemption_category=CycleCourse.ExemptionCategory.OTHER_OUTPUT_BASED,
            exemption_reason="Approved non-examination output requirement.",
            exemption_changed_by=self.configurer,
            exemption_changed_at=timezone.now(),
        )
        self.client.force_login(self.configurer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary_rows"], [])
        self.assertContains(
            response,
            "No qualifying faculty contributions found.",
        )

    def test_missing_contributor_default_campus_displays_not_set(self):
        self.faculty.default_campus = None
        self.faculty.save(update_fields=["default_campus", "updated_at"])
        self.client.force_login(self.configurer)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [
                (row["display_name"], row["default_campus_name"])
                for row in response.context["summary_rows"]
            ],
            [("Zulu, Zoe", "Not set")],
        )
        self.assertContains(response, "<td>Not set</td>", html=True)
