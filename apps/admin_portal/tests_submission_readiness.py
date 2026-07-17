from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import (
    AcademicYear,
    ActiveGradingPeriodSetting,
    Course,
    CourseOffering,
    FacultyAssignment,
    Section,
    TenantTermGradingPeriod,
    Term,
)
from apps.admin_portal.submission_readiness import GradeSubmissionReadinessService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class GradeSubmissionReadinessTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="READY", name="Readiness Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.college = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College of Computing",
            unit_type=Department.UnitType.DIVISION,
        )
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            parent=self.college,
            code="CS",
            name="Computer Science",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSCS",
            name="BS Computer Science",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2026-2027",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Semester",
            sequence_no=1,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 10, 31),
        )
        self.term_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=self.term_period,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="READY-TPL",
            name="Readiness Template",
            is_published=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        self.component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS101",
            title="Introduction to Computing",
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
        )
        self.faculty_role, _ = Role.objects.get_or_create(code="FACULTY", defaults={"name": "Faculty"})
        self.monitor_permission, _ = Permission.objects.get_or_create(
            code="faculty_activity_monitor.read",
            defaults={"module": "faculty_activity_monitor", "action": "read"},
        )
        self.admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={"module": "admin_portal", "action": "access"},
        )
        self.area_role = self._monitor_role("AREA_CHAIR", "Area Chair")
        self.area_chair = self._user("area-chair", self.campus, self.department)
        UserRole.objects.create(
            user=self.area_chair,
            role=self.area_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.faculty = self._faculty("faculty-one", self.campus, self.department)
        self.offering = self._offering("BSCS-1A", self.campus, self.department, self.program)
        self.assignment = self._assignment(self.offering, self.faculty)
        self.url = reverse("admin_portal:grade_submission_readiness")
        self.filters = {
            "academic_year_id": self.academic_year.id,
            "term_id": self.term.id,
            "period_code": "PRELIM",
        }

    def _monitor_role(self, code, name):
        role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
        RolePermission.objects.get_or_create(role=role, permission=self.admin_access)
        RolePermission.objects.get_or_create(role=role, permission=self.monitor_permission)
        return role

    def _user(self, username, campus, department):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=campus,
            default_department=department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def _faculty(self, username, campus, department):
        user = self._user(username, campus, department)
        UserRole.objects.create(
            user=user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=campus,
            department=department,
        )
        return user

    def _offering(self, section_code, campus, department, program, course=None):
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
            course=course or self.course,
            section=section,
        )

    def _assignment(self, offering, faculty):
        return FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            offering=offering,
            faculty_user=faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=faculty,
            is_primary=True,
        )

    def _student(self, number, offering):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            department=offering.department,
            program=offering.program,
            student_no=number,
            last_name=f"Student {number}",
            first_name="Test",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            academic_year=self.academic_year,
            term=self.term,
            course_offering=offering,
            student=student,
            enrollment_status=Enrollment.Status.ACTIVE,
        )
        return student

    def _activity(self, offering):
        return GradeActivity.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            offering=offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz 1",
            total_score=Decimal("100"),
        )

    def _score(self, activity, student):
        return StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=Decimal("80"),
            computed_score=Decimal("80"),
        )

    def _calculate(self, assignments=None, now=None):
        return GradeSubmissionReadinessService.calculate(
            assignments or [self.assignment],
            selected_period_code="PRELIM",
            now=now,
        )

    def test_readiness_calculations_cover_attention_nearly_ready_and_ready(self):
        students = [self._student(f"2026-{index:03d}", self.offering) for index in range(10)]
        activity = self._activity(self.offering)

        no_scores = self._calculate()[0]
        self.assertEqual(no_scores.status, GradeSubmissionReadinessService.NEEDS_ATTENTION)
        self.assertEqual(no_scores.progress_percent, Decimal("0.00"))

        for student in students[:9]:
            self._score(activity, student)
        nearly = self._calculate()[0]
        self.assertEqual(nearly.status, GradeSubmissionReadinessService.NEARLY_READY)
        self.assertEqual(nearly.progress_percent, Decimal("90.00"))

        self._score(activity, students[9])
        ready = self._calculate()[0]
        self.assertEqual(ready.status, GradeSubmissionReadinessService.READY)
        self.assertTrue(ready.submission_eligible)
        self.assertEqual(ready.progress_percent, Decimal("100.00"))
        self.assertIsNotNone(ready.last_activity_at)

    def test_submitted_and_overdue_statuses_and_priority_sorting(self):
        student = self._student("2026-001", self.offering)
        self._score(self._activity(self.offering), student)
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            deadline_at=timezone.now() - timedelta(hours=1),
        )
        overdue = self._calculate()[0]
        self.assertEqual(overdue.status, GradeSubmissionReadinessService.OVERDUE)

        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty,
            submitted_at=timezone.now(),
        )
        submitted = self._calculate()[0]
        self.assertEqual(submitted.status, GradeSubmissionReadinessService.SUBMITTED)
        self.assertEqual(submitted.progress_percent, Decimal("100.00"))
        self.assertLess(
            GradeSubmissionReadinessService.STATUS_PRIORITY[GradeSubmissionReadinessService.OVERDUE],
            GradeSubmissionReadinessService.STATUS_PRIORITY[GradeSubmissionReadinessService.SUBMITTED],
        )

    def test_page_groups_by_faculty_and_reports_summary_without_student_identity(self):
        second_offering = self._offering("BSCS-1B", self.campus, self.department, self.program)
        self._assignment(second_offering, self.faculty)
        student = self._student("PRIVATE-001", self.offering)
        self._score(self._activity(self.offering), student)
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url, self.filters)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["faculty_groups"]), 1)
        self.assertEqual(len(response.context["faculty_groups"][0]["rows"]), 2)
        self.assertEqual(response.context["summary"]["total_faculty"], 1)
        self.assertEqual(response.context["summary"]["ready"], 1)
        self.assertEqual(response.context["summary"]["needs_attention"], 1)
        self.assertNotContains(response, "PRIVATE-001")
        self.assertContains(response, 'class="card-header readiness-faculty-header py-3"')
        self.assertContains(response, 'class="readiness-faculty-name mb-0"')

    def test_filters_apply_to_course_section_and_status_without_faculty_filtering(self):
        other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS202",
            title="Algorithms",
        )
        CourseTemplateAssignment.objects.create(
            course=other_course,
            grading_template=self.template,
            effective_from_term=self.term,
        )
        other_faculty = self._faculty("faculty-two", self.campus, self.department)
        other_offering = self._offering("BSCS-2B", self.campus, self.department, self.program, other_course)
        self._assignment(other_offering, other_faculty)
        self.client.force_login(self.area_chair)

        response = self.client.get(
            self.url,
            {
                **self.filters,
                "course_code": "CS202",
                "section": "2B",
                "status": GradeSubmissionReadinessService.NEEDS_ATTENTION,
            },
        )

        self.assertEqual(response.status_code, 200)
        rows = [row for group in response.context["faculty_groups"] for row in group["rows"]]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].assignment.offering_id, other_offering.id)

    def test_forged_faculty_parameter_is_ignored_and_all_scoped_faculty_are_listed(self):
        other_faculty = self._faculty("faculty-two", self.campus, self.department)
        other_offering = self._offering("BSCS-1B", self.campus, self.department, self.program)
        self._assignment(other_offering, other_faculty)
        self.client.force_login(self.area_chair)

        response = self.client.get(
            self.url,
            {**self.filters, "faculty_user_id": self.faculty.id},
        )

        self.assertEqual(response.status_code, 200)
        faculty_ids = {group["faculty"].id for group in response.context["faculty_groups"]}
        self.assertEqual(faculty_ids, {self.faculty.id, other_faculty.id})
        self.assertNotContains(response, '<label class="form-label mb-1">Faculty</label>')
        self.assertNotContains(response, '<select class="form-select" name="faculty_user_id">')

    def test_area_chair_has_no_cross_campus_data_leak(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="BRANCH", name="Branch")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="BR-CS",
            name="Branch CS",
        )
        other_program = Program.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            code="BR-BSCS",
            name="Branch BSCS",
        )
        other_faculty = self._faculty("branch-faculty", other_campus, other_department)
        other_course = Course.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            code="SECRET101",
            title="Hidden Course",
        )
        CourseTemplateAssignment.objects.create(
            course=other_course,
            grading_template=self.template,
            effective_from_term=self.term,
        )
        other_offering = self._offering("SECRET-1A", other_campus, other_department, other_program, other_course)
        self._assignment(other_offering, other_faculty)
        self.client.force_login(self.area_chair)

        response = self.client.get(self.url, {**self.filters, "campus_id": "all"})

        self.assertEqual(response.status_code, 200)
        offering_ids = {
            row.assignment.offering_id
            for group in response.context["faculty_groups"]
            for row in group["rows"]
        }
        self.assertEqual(offering_ids, {self.offering.id})
        self.assertNotContains(response, "SECRET101")

    def test_college_dean_cao_campus_admin_and_superadmin_use_existing_scope_service(self):
        area_scope_role = Role.objects.create(code="AC", name="Area Scope")
        area_scope_user = self._user("area-scope", self.campus, self.department)
        UserRole.objects.create(
            user=area_scope_user,
            role=area_scope_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        users = []
        for code, department in [
            ("COLLEGE_DEAN", self.college),
            ("CAO", None),
            ("CAMPUS_ADMIN", None),
        ]:
            role = self._monitor_role(code, code.replace("_", " ").title())
            user = self._user(code.lower(), self.campus, department or self.department)
            UserRole.objects.create(
                user=user,
                role=role,
                tenant=self.tenant,
                campus=self.campus,
                department=department,
            )
            users.append(user)
        superadmin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        users.append(superadmin)

        for user in users:
            self.client.force_login(user)
            response = self.client.get(self.url, self.filters)
            self.assertEqual(response.status_code, 200, user.username)
            self.assertEqual(response.context["summary"]["total_faculty"], 1, user.username)

    def test_permission_enforcement_and_detail_scope(self):
        unauthorized_role = Role.objects.create(code="NO_MONITOR", name="No Monitor")
        RolePermission.objects.create(role=unauthorized_role, permission=self.admin_access)
        unauthorized = self._user("unauthorized", self.campus, self.department)
        UserRole.objects.create(
            user=unauthorized,
            role=unauthorized_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(unauthorized)
        self.assertEqual(self.client.get(self.url).status_code, 403)

        self.client.force_login(self.area_chair)
        detail = self.client.get(
            reverse("admin_portal:grade_submission_readiness_detail", args=[self.offering.id])
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Coming Soon")

    def test_summary_formula_counts_ready_and_submitted_as_operationally_ready(self):
        rows = []
        for status in [
            GradeSubmissionReadinessService.READY,
            GradeSubmissionReadinessService.SUBMITTED,
            GradeSubmissionReadinessService.NEARLY_READY,
            GradeSubmissionReadinessService.OVERDUE,
        ]:
            row = type("Result", (), {"status": status, "assignment": self.assignment})()
            rows.append(row)
        summary = GradeSubmissionReadinessService.summary(rows)
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["submitted"], 1)
        self.assertEqual(summary["needs_attention"], 1)
        self.assertEqual(summary["overdue"], 1)
        self.assertEqual(summary["readiness_percent"], 50.0)
