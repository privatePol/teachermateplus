from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import GradingTemplate, GradingTemplatePeriod, StudentPeriodGrade
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class GradeDistributionMonitorTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="FV", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BSA",
            name="Accountancy",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSA",
            name="BS Accountancy",
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
            code="GENED",
            name="General Education",
            is_published=True,
            is_active=True,
            passing_grade_threshold=Decimal("75"),
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )

        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="ACC101",
            title="Accounting 101",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSA1A",
            name="BSA 1A",
        )
        self.offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )

        self.admin_user = self._user("dean", "Dean", "User", self.tenant, self.campus, self.department)
        self.faculty_user = self._user("faculty", "Faculty", "One", self.tenant, self.campus, self.department)
        self._assign_permissions(self.admin_user, include_monitor=True)
        self._assign_faculty_role(self.faculty_user)
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )

        self._seed_grades(self.offering, [95, 94, 93, 92, 91, 90, 96, 97, 80, 76])
        self.url = reverse("admin_portal:grade_distribution_monitor")

    def test_monitor_requires_specific_permission(self):
        user = self._user("no_monitor", "No", "Monitor", self.tenant, self.campus, self.department)
        self._assign_permissions(user, include_monitor=False)
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_period_grade_distribution_flags_high_concentration(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accounting 101")
        self.assertContains(response, "80.0%")
        self.assertContains(response, 'id="gradeDistributionLoadingOverlay"')
        self.assertContains(response, 'id="gradeDistributionFilterForm"')
        self.assertNotContains(response, "Course / Subject")
        self.assertNotContains(response, "Class / Offering")
        self.assertNotContains(response, "All Components")
        self.assertNotContains(response, "All Sub-components")
        self.assertContains(response, 'data-bs-target="#gradeDistributionDetailModal1"')
        self.assertContains(response, "Masked Student No.")
        self.assertContains(response, "*******001")
        self.assertContains(response, "T*** S***")
        self.assertNotContains(response, "ACC101-001")
        self.assertNotContains(response, "Student1")
        self.assertNotContains(response, "Classes in Scope")
        self.assertNotContains(response, "Rows Reviewed")
        self.assertNotContains(response, "For Review")
        self.assertNotContains(response, "High Grade Concentration")
        self.assertNotContains(response, "High Perfect Score Rate")
        self.assertNotContains(response, "<th>Spread</th>", html=True)
        self.assertNotContains(response, "<th>Comparison</th>", html=True)
        self.assertNotContains(response, "<th>Status</th>", html=True)

    def test_period_filter_uses_templates_resolved_for_monitored_classes(self):
        unrelated_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="OTHER-DEPT",
            name="Other Department",
        )
        self.template.department_visibility = GradingTemplate.DepartmentVisibility.SELECTED
        self.template.save(update_fields=["department_visibility", "updated_at"])
        self.template.visible_departments.set([unrelated_department])
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{self.period.id}"')
        self.assertContains(response, "GENED / Prelim")

    def test_scope_does_not_leak_other_tenant_data(self):
        other_tenant = Tenant.objects.create(code="OTHER", name="Other School")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OC", name="Other Campus")
        other_department = Department.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            code="OTH",
            name="Other Department",
        )
        other_program = Program.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            code="OTH",
            name="Other Program",
        )
        other_ay = AcademicYear.objects.create(
            tenant=other_tenant,
            code="2025-2026",
            name="Other AY",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        other_term = Term.objects.create(
            tenant=other_tenant,
            academic_year=other_ay,
            code="1ST",
            name="First Semester",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )
        other_course = Course.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            code="OTHER101",
            title="Other Course",
        )
        other_section = Section.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            code="OTH1",
            name="Other 1",
        )
        other_offering = CourseOffering.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            academic_year=other_ay,
            term=other_term,
            course=other_course,
            section=other_section,
        )
        other_template = GradingTemplate.objects.create(
            tenant=other_tenant,
            code="OTHER",
            name="Other Template",
            is_published=True,
            is_active=True,
            passing_grade_threshold=Decimal("75"),
        )
        other_period = GradingTemplatePeriod.objects.create(
            template=other_template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        self._seed_grades(other_offering, [100] * 10, period=other_period)

        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "OTHER101")
        self.assertNotContains(response, "Other Course")

    def test_small_sample_warning_suppresses_review_flag(self):
        offering = self._create_extra_offering("SMALL101", "Small Sample Course", "BSA1B")
        self._seed_grades(offering, [98, 97, 96, 95, 94])

        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"offering_id": offering.id})

        self.assertEqual(response.status_code, 200)
        target_row = next(row for row in response.context["rows"] if row["course_code"] == "SMALL101")
        row_flags = [flag["label"] for flag in target_row["flags"]]
        self.assertIn("Small Sample", row_flags)
        self.assertNotIn("High Grade Concentration", row_flags)

    def test_incomplete_data_is_shown_when_not_all_active_students_have_grades(self):
        offering = self._create_extra_offering("INC101", "Incomplete Data Course", "BSA1C")
        self._seed_grades(offering, [95] * 8, extra_enrolled_without_grade=2)

        self.client.force_login(self.admin_user)
        response = self.client.get(self.url, {"offering_id": offering.id})

        self.assertEqual(response.status_code, 200)
        target_row = next(row for row in response.context["rows"] if row["course_code"] == "INC101")
        row_flags = [flag["label"] for flag in target_row["flags"]]
        self.assertIn("Incomplete Data", row_flags)
        self.assertNotIn("High Grade Concentration", row_flags)

    def test_monitor_loads_when_offering_has_no_published_template(self):
        self.template.is_published = False
        self.template.save(update_fields=["is_published", "updated_at"])

        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no published grading template assigned")
        self.assertContains(response, "No template")
        self.assertEqual(response.context["summary"]["missing_template_offerings"], 1)

    def test_missing_template_monitor_uses_tenant_passing_threshold(self):
        SystemSettingService.set(
            "PASSING_GRADE_THRESHOLD",
            "80",
            tenant_id=self.tenant.id,
            value_type="STRING",
            is_active=True,
        )
        self.template.is_published = False
        self.template.save(update_fields=["is_published", "updated_at"])

        self.client.force_login(self.admin_user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        target_row = next(row for row in response.context["rows"] if row["course_code"] == "ACC101")
        self.assertEqual(target_row["below_passing_pct"], Decimal("10.0"))

    def test_monitor_scope_follows_faculty_assignment_scope_not_offering_department(self):
        reviewer_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
        )
        scoped_reviewer = self._user(
            "ac_scope",
            "Area",
            "Chair",
            self.tenant,
            self.campus,
            reviewer_department,
        )
        scoped_faculty = self._user(
            "faculty_scope",
            "Scoped",
            "Faculty",
            self.tenant,
            self.campus,
            reviewer_department,
        )
        self._assign_permissions(scoped_reviewer, include_monitor=True)
        self._assign_faculty_role(scoped_faculty)
        offering = self._create_extra_offering("MISMATCH101", "Offering Department Mismatch", "BSA1D")
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            faculty_user=scoped_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )
        self._seed_grades(offering, [91, 92, 93, 94, 95, 96, 97, 98, 81, 76])

        self.client.force_login(scoped_reviewer)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MISMATCH101")
        self.assertContains(response, "Offering Department Mismatch")

    def test_monitor_defaults_to_topbar_campus_and_supports_explicit_all_campuses(self):
        second_campus = Campus.objects.create(
            tenant=self.tenant,
            code="CUB",
            name="Cubao",
        )
        second_department = Department.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            code="CUB-BSA",
            name="Cubao Accountancy",
        )
        second_program = Program.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
            code="BSA-CUB",
            name="BS Accountancy Cubao",
        )
        second_course = Course.objects.create(
            tenant=self.tenant,
            code="CUB101",
            title="Cubao Accounting",
        )
        second_section = Section.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
            program=second_program,
            code="CUB-BSA1A",
            name="Cubao BSA 1A",
        )
        second_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
            program=second_program,
            academic_year=self.academic_year,
            term=self.term,
            course=second_course,
            section=second_section,
        )
        admin_role = self.admin_user.user_roles.exclude(role__code="FACULTY").get().role
        UserRole.objects.create(
            user=self.admin_user,
            role=admin_role,
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
        )
        UserRole.objects.create(
            user=self.faculty_user,
            role=Role.objects.get(code="FACULTY"),
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            offering=second_offering,
            faculty_user=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
            is_primary=True,
        )
        self._seed_grades(second_offering, [88, 89], period=self.period)
        self.client.force_login(self.admin_user)

        default_response = self.client.get(self.url)
        all_campuses_response = self.client.get(self.url, {"campus_id": "all"})

        self.assertEqual(default_response.status_code, 200)
        self.assertEqual(default_response.context["summary"]["offerings"], 1)
        self.assertNotContains(default_response, "Cubao Accounting")
        self.assertEqual(all_campuses_response.status_code, 200)
        self.assertTrue(all_campuses_response.context["selected"]["all_campuses"])
        self.assertEqual(all_campuses_response.context["summary"]["offerings"], 2)
        self.assertContains(all_campuses_response, "Cubao Accounting")

    def test_export_csv_uses_same_permission_and_scope(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url, {"export": "csv"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertContains(response, "Accounting 101")
        header = response.content.decode("utf-8").splitlines()[0]
        self.assertNotIn("Spread", header)
        self.assertNotIn("Department Average", header)
        self.assertNotIn("Subject Average", header)
        self.assertNotIn("Flags", header)

    def _user(self, username, first_name, last_name, tenant, campus, department):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            first_name=first_name,
            last_name=last_name,
            default_tenant=tenant,
            default_campus=campus,
            default_department=department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def _assign_permissions(self, user, *, include_monitor):
        role, _ = Role.objects.get_or_create(code=f"ROLE_{user.username}", defaults={"name": f"Role {user.username}"})
        admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={"module": "admin_portal", "action": "access"},
        )
        RolePermission.objects.get_or_create(role=role, permission=admin_access)
        if include_monitor:
            monitor, _ = Permission.objects.get_or_create(
                code="grade_distribution_monitor.read",
                defaults={"module": "grade_distribution_monitor", "action": "read"},
            )
            RolePermission.objects.get_or_create(role=role, permission=monitor)
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=user.default_tenant,
            campus=user.default_campus,
            department=user.default_department,
        )

    def _assign_faculty_role(self, user):
        role, _ = Role.objects.get_or_create(code="FACULTY", defaults={"name": "Faculty"})
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=user.default_tenant,
            campus=user.default_campus,
            department=user.default_department,
        )

    def _seed_grades(self, offering, values, period=None, extra_enrolled_without_grade=0):
        period = period or self.period
        for index, value in enumerate(values, start=1):
            student = Student.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                department=offering.department,
                program=offering.program,
                student_no=f"{offering.course.code}-{index:03d}",
                last_name=f"Student{index}",
                first_name="Test",
            )
            Enrollment.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                academic_year=offering.academic_year,
                term=offering.term,
                student=student,
                course_offering=offering,
            )
            StudentPeriodGrade.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                offering=offering,
                template_period=period,
                student=student,
                period_grade=Decimal(str(value)),
            )
        for index in range(len(values) + 1, len(values) + extra_enrolled_without_grade + 1):
            student = Student.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                department=offering.department,
                program=offering.program,
                student_no=f"{offering.course.code}-{index:03d}",
                last_name=f"Student{index}",
                first_name="NoGrade",
            )
            Enrollment.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                academic_year=offering.academic_year,
                term=offering.term,
                student=student,
                course_offering=offering,
            )

    def _create_extra_offering(self, course_code, course_title, section_code):
        course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code=course_code,
            title=course_title,
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code=section_code,
            name=section_code,
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=course,
            section=section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            faculty_user=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )
        return offering
