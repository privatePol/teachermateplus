from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.faculty_portal.services import FacultyPerformanceService
from apps.grading.models import (
    GradeActivity,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class AcademicPerformanceInsightsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="FV", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="INFOSYS",
            name="Information Systems",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIS",
            name="BS Information Systems",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="AY 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Semester",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="BSIS",
            name="BSIS Template",
            is_active=True,
            is_published=True,
            passing_grade_threshold=Decimal("75"),
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        self.component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="QUIZZES",
            name="Quizzes",
            weight_percentage=Decimal("100"),
            sort_order=1,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            code="ITAPPS",
            title="IT Application Tools",
        )
        self.area_chair = self._user("area-chair", self.campus, self.department)
        self.faculty = self._user("faculty-one", self.campus, self.department)
        self.area_role = self._role_with_access("AREA_CHAIR", "Area Chairperson")
        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        UserRole.objects.create(
            user=self.area_chair,
            role=self.area_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        UserRole.objects.create(
            user=self.faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.offering_a = self._offering("BSIS-1A", self.campus, self.department, self.program)
        self.offering_b = self._offering("BSIS-1B", self.campus, self.department, self.program)
        self._assign(self.offering_a, self.faculty)
        self._assign(self.offering_b, self.faculty)
        self.student_a1 = self._student(self.offering_a, "2025-001", "Alpha")
        self.student_a2 = self._student(self.offering_a, "2025-002", "Beta")
        self.student_b1 = self._student(self.offering_b, "2025-003", "Gamma")
        self.student_b2 = self._student(self.offering_b, "2025-004", "Delta")
        self.activity_a = self._activity(self.offering_a, "Q1", 100)
        self.activity_b = self._activity(self.offering_b, "Q1", 100)
        self._score(self.activity_a, self.student_a1, 80, 90)
        self._score(self.activity_a, self.student_a2, 0, 50)
        self._score(self.activity_b, self.student_b1, 90, 95)
        self._score(self.activity_b, self.student_b2, 85, Decimal("92.50"))
        self._enable_feature()
        self.url = reverse("admin_portal:academic_performance_insights")
        self.filters = {
            "academic_year_id": self.academic_year.id,
            "term_id": self.term.id,
            "period_code": self.period.code,
        }

    def test_disabled_feature_returns_not_found(self):
        SystemSettingService.set(
            FeatureSettingsService.ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url, self.filters)

        self.assertEqual(response.status_code, 404)

    def test_feature_toggle_hides_and_shows_navigation_entry(self):
        self.client.force_login(self.area_chair)
        enabled_response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertContains(enabled_response, "Academic Performance Insights")

        SystemSettingService.set(
            FeatureSettingsService.ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        disabled_response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertNotContains(disabled_response, "Academic Performance Insights")

    def test_required_filters_show_friendly_message(self):
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select an Academic Year, Term, and Grading Period")
        self.assertNotContains(response, self.student_a1.last_name)

    def test_user_without_grading_analytics_permission_is_denied(self):
        unauthorized = self._user("unauthorized", self.campus, self.department)
        role = Role.objects.create(code="UNAUTHORIZED_HEAD", name="Unauthorized Head")
        access, _ = Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={"module": "admin_portal", "action": "access"},
        )
        RolePermission.objects.create(role=role, permission=access)
        UserRole.objects.create(
            user=unauthorized,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(unauthorized)

        response = self.client.get(self.url, self.filters)

        self.assertEqual(response.status_code, 403)

    def test_course_report_reuses_official_faculty_performance_service(self):
        self.client.force_login(self.area_chair)
        original = FacultyPerformanceService.get_class_performance_snapshot
        with patch.object(
            FacultyPerformanceService,
            "get_class_performance_snapshot",
            wraps=original,
        ) as official_snapshot:
            response = self.client.get(self.url, self.filters)

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(official_snapshot.call_count, 2)
        self.assertContains(response, "ITAPPS")
        self.assertContains(response, "BSIS-1A")
        self.assertContains(response, "BSIS-1B")
        self.assertEqual(
            response.content.decode("utf-8").count('class="card shadow-sm h-100 insight-card"'),
            4,
        )
        self.assertNotContains(response, "Student Ranking")
        self.assertNotContains(response, "Class Ranking")

    def test_main_report_shows_attention_panel_and_css_bar_legend(self):
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url, self.filters)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Needs Attention")
        self.assertContains(response, "Main Issue")
        self.assertContains(response, "Suggested Check")
        self.assertContains(response, "means a higher class average")
        self.assertContains(response, "means more at-risk students")
        self.assertContains(response, "means more missing outputs")

    def test_main_report_shows_normal_attention_message_when_all_sections_are_normal(self):
        StudentActivityScore.objects.update(
            raw_score=Decimal("90"),
            computed_score=Decimal("95"),
        )
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url, self.filters)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All compared sections are within normal range.")

    def test_main_report_shows_incomplete_attention_message(self):
        GradeActivity.objects.update(is_active=False)
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url, self.filters)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Some sections have incomplete data. Review grade encoding or activity setup first.",
        )

    def test_course_filter_excludes_other_course_codes(self):
        other_course = Course.objects.create(
            tenant=self.tenant,
            code="OTHER101",
            title="Other Course",
        )
        self.offering_b.course = other_course
        self.offering_b.save(update_fields=["course", "updated_at"])
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url, {**self.filters, "course_code": "ITAPPS"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BSIS-1A")
        self.assertNotContains(response, "BSIS-1B")

    def test_encoded_zero_is_not_missing_and_student_identity_is_not_exposed(self):
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url, self.filters)

        self.assertEqual(response.status_code, 200)
        target = next(row for row in response.context["rows"] if row["offering"].id == self.offering_a.id)
        self.assertEqual(target["missing_output_count"], 0)
        self.assertNotContains(response, self.student_a1.student_no)
        self.assertNotContains(response, self.student_a1.last_name)
        self.assertNotContains(response, self.student_a2.last_name)

    def test_area_chair_cannot_see_other_campus(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="CUB", name="Cubao")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="INFOSYS-CUB",
            name="Information Systems Cubao",
        )
        other_program = Program.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            code="BSIS-CUB",
            name="BSIS Cubao",
        )
        other_faculty = self._user("faculty-cub", other_campus, other_department)
        UserRole.objects.create(
            user=other_faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
        )
        UserRole.objects.create(
            user=self.area_chair,
            role=self.area_role,
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
        )
        other_offering = self._offering("BSIS-CUB-1A", other_campus, other_department, other_program)
        self._assign(other_offering, other_faculty)
        other_student = self._student(other_offering, "CUB-001", "Hidden")
        other_activity = self._activity(other_offering, "Q1", 100)
        self._score(other_activity, other_student, 90, 95)
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url, {**self.filters, "campus_id": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "BSIS-CUB-1A")
        self.assertNotContains(response, "Cubao")
        visible_campus_ids = {
            campus.id for campus in response.context["filter_options"]["role_scope"]["campuses"]
        }
        self.assertEqual(visible_campus_ids, {self.campus.id})

    def test_college_dean_can_compare_authorized_campuses_through_area_chairs(self):
        second_campus = Campus.objects.create(tenant=self.tenant, code="CUB", name="Cubao")
        second_department = Department.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            code="INFOSYS-CUB",
            name="Information Systems Cubao",
        )
        second_program = Program.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
            code="BSIS-CUB",
            name="BSIS Cubao",
        )
        second_chair = self._user("area-chair-cub", second_campus, second_department)
        second_faculty = self._user("faculty-cub", second_campus, second_department)
        UserRole.objects.create(
            user=second_chair,
            role=self.area_role,
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
        )
        UserRole.objects.create(
            user=second_faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
        )
        offering = self._offering("BSIS-CUB-1A", second_campus, second_department, second_program)
        self._assign(offering, second_faculty)
        student = self._student(offering, "CUB-001", "CrossCampus")
        activity = self._activity(offering, "Q1", 100)
        self._score(activity, student, 90, 95)
        dean = self._user("college-dean", self.campus, self.department)
        dean_role = self._role_with_access("COLLEGE_DEAN", "College Dean")
        for campus, department in [
            (self.campus, self.department),
            (second_campus, second_department),
        ]:
            UserRole.objects.create(
                user=dean,
                role=dean_role,
                tenant=self.tenant,
                campus=campus,
                department=department,
            )
        self.client.force_login(dean)

        response = self.client.get(self.url, {**self.filters, "campus_id": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BSIS-1A")
        self.assertContains(response, "BSIS-CUB-1A")
        self.assertContains(response, "Authorized Campus Summary")

    def test_activity_consistency_labels_minor_difference_and_incomplete_setup(self):
        self._activity(self.offering_b, "Q2", 50)
        activity_url = reverse("admin_portal:academic_activity_consistency")
        self.client.force_login(self.area_chair)

        response = self.client.get(activity_url, self.filters)

        self.assertEqual(response.status_code, 200)
        statuses = {row["section"]: row["consistency_status"] for row in response.context["rows"]}
        self.assertEqual(statuses["BSIS-1A"], "Minor Difference")
        self.assertEqual(statuses["BSIS-1B"], "Minor Difference")

        self.activity_a.is_active = False
        self.activity_a.save(update_fields=["is_active", "updated_at"])
        response = self.client.get(activity_url, self.filters)
        statuses = {row["section"]: row["consistency_status"] for row in response.context["rows"]}
        self.assertEqual(statuses["BSIS-1A"], "Incomplete Setup")

    def test_activity_consistency_uses_counts_not_exact_titles_and_flags_major_difference(self):
        self.activity_b.title = "Different Faculty Title"
        self.activity_b.save(update_fields=["title", "updated_at"])
        self._activity(self.offering_b, "Second Output", 40)
        self._activity(self.offering_b, "Third Output", 75)
        activity_url = reverse("admin_portal:academic_activity_consistency")
        self.client.force_login(self.area_chair)

        response = self.client.get(activity_url, self.filters)

        statuses = {row["section"]: row["consistency_status"] for row in response.context["rows"]}
        self.assertEqual(statuses["BSIS-1A"], "Needs Review")
        self.assertEqual(statuses["BSIS-1B"], "Needs Review")
        target = next(row for row in response.context["rows"] if row["section"] == "BSIS-1B")
        self.assertEqual(target["max_score_difference"], Decimal("60"))

    def test_activity_consistency_requires_two_same_course_sections(self):
        other_course = Course.objects.create(
            tenant=self.tenant,
            code="OTHER101",
            title="Other Course",
        )
        self.offering_b.course = other_course
        self.offering_b.save(update_fields=["course", "updated_at"])
        self.client.force_login(self.area_chair)

        response = self.client.get(
            reverse("admin_portal:academic_activity_consistency"),
            self.filters,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comparison requires at least two sections with the same course code.")
        self.assertEqual(list(response.context["rows"]), [])

    def test_section_detail_shows_no_student_data(self):
        self.client.force_login(self.area_chair)
        url = reverse(
            "admin_portal:academic_performance_section_detail",
            args=[self.offering_a.id, self.period.code],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Component Breakdown")
        self.assertContains(response, "Activity Setup")
        self.assertContains(response, "What to Review")
        self.assertContains(response, "Comparison Context")
        self.assertContains(response, "Ready for Comparison")
        self.assertContains(response, "Activity Setup Status")
        self.assertContains(response, "View Activity Consistency")
        self.assertNotContains(response, self.student_a1.student_no)
        self.assertNotContains(response, self.student_a1.last_name)
        self.assertNotContains(response, "Student Ranking")
        self.assertNotContains(response, "Faculty Ranking")

    def test_section_detail_sorts_activities_and_preserves_report_return_state(self):
        self._activity(self.offering_a, "Z Activity", 20)
        self._activity(self.offering_a, "A Activity", 20)
        self.client.force_login(self.area_chair)
        report_url = f"{self.url}?academic_year_id={self.academic_year.id}&term_id={self.term.id}&period_code=PRELIM&course_code=ITAPPS&page=2"
        detail_url = reverse(
            "admin_portal:academic_performance_section_detail",
            args=[self.offering_a.id, self.period.code],
        )

        report_response = self.client.get(report_url)
        self.assertContains(report_response, "next=")

        response = self.client.get(detail_url, {"next": report_url})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["back_url"], report_url)
        content = response.content.decode("utf-8")
        self.assertLess(content.index("A Activity"), content.index("Q1"))
        self.assertLess(content.index("Q1"), content.index("Z Activity"))

    def test_section_detail_rejects_external_return_url(self):
        self.client.force_login(self.area_chair)
        detail_url = reverse(
            "admin_portal:academic_performance_section_detail",
            args=[self.offering_a.id, self.period.code],
        )

        response = self.client.get(detail_url, {"next": "https://example.com/unsafe"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["back_url"],
            reverse("admin_portal:academic_performance_insights"),
        )

    def test_reports_do_not_create_or_update_grade_records(self):
        before_scores = list(
            StudentActivityScore.objects.order_by("id").values_list(
                "id",
                "raw_score",
                "computed_score",
                "updated_at",
            )
        )
        before_period_grades = StudentPeriodGrade.objects.count()
        self.client.force_login(self.area_chair)

        self.client.get(self.url, self.filters)
        self.client.get(reverse("admin_portal:academic_activity_consistency"), self.filters)
        self.client.get(
            reverse(
                "admin_portal:academic_performance_section_detail",
                args=[self.offering_a.id, self.period.code],
            )
        )

        after_scores = list(
            StudentActivityScore.objects.order_by("id").values_list(
                "id",
                "raw_score",
                "computed_score",
                "updated_at",
            )
        )
        self.assertEqual(after_scores, before_scores)
        self.assertEqual(StudentPeriodGrade.objects.count(), before_period_grades)

    def _enable_feature(self):
        SystemSettingService.set(
            FeatureSettingsService.ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

    def _role_with_access(self, code, name):
        role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
        for permission_code in ["admin_portal.access", "dashboard.read", "grading_analytics.read"]:
            permission, _ = Permission.objects.get_or_create(
                code=permission_code,
                defaults={"module": permission_code.split(".")[0], "action": permission_code.split(".")[-1]},
            )
            RolePermission.objects.get_or_create(role=role, permission=permission)
        return role

    def _user(self, username, campus, department):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            first_name=username,
            last_name="User",
            default_tenant=self.tenant,
            default_campus=campus,
            default_department=department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def _offering(self, section_code, campus, department, program):
        section = Section.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            code=section_code,
            name=section_code,
        )
        return CourseOffering.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=section,
        )

    def _assign(self, offering, faculty):
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            offering=offering,
            faculty_user=faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )

    def _student(self, offering, student_no, last_name):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            department=offering.department,
            program=offering.program,
            student_no=student_no,
            first_name="Test",
            last_name=last_name,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=offering,
        )
        return student

    def _activity(self, offering, title, total_score):
        return GradeActivity.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            offering=offering,
            template_period=self.period,
            template_component=self.component,
            title=title,
            total_score=Decimal(str(total_score)),
            created_by_user=self.faculty,
        )

    @staticmethod
    def _score(activity, student, raw, computed):
        return StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=Decimal(str(raw)),
            computed_score=Decimal(str(computed)),
        )
