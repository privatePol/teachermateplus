from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import GradingTemplate, GradingTemplatePeriod, StudentPeriodGrade
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class AdminGradingAnalyticsTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="FV", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="IS",
            name="Information Systems",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIT",
            name="BS Information Technology",
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
            name="First Term",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="IT101",
            title="Information Technology 101",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT1A",
            name="BSIT 1A",
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
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED_DRAFT",
            name="Draft Template",
            is_published=False,
            is_active=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )

        self.admin_user = self._user("analytics_admin", "Analytics", "Admin")
        self.faculty_user = self._user("analytics_faculty", "Analytics", "Faculty")
        role = Role.objects.create(code="ANALYTICS_ADMIN", name="Analytics Admin")
        faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        for code, module, action in (
            ("admin_portal.access", "admin_portal", "access"),
            ("grading_analytics.read", "grading_analytics", "read"),
        ):
            permission, _ = Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action},
            )
            RolePermission.objects.get_or_create(role=role, permission=permission)
        UserRole.objects.create(
            user=self.admin_user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        UserRole.objects.create(
            user=self.faculty_user,
            role=faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )
        self.url = reverse("admin_portal:grading_analytics")

    def test_grading_analytics_loads_when_offering_has_no_published_template(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no published grading template assigned")
        self.assertContains(response, "No Published Template: 1")
        self.assertEqual(response.context["summary"]["missing_template_offerings"], 1)

    def test_grading_analytics_follows_supervised_faculty_when_offering_department_differs(self):
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
        )
        self.offering.department = other_department
        self.offering.save(update_fields=["department", "updated_at"])
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["offerings"], 1)
        self.assertContains(response, "Results follow the faculty members you supervise")

    def test_grading_analytics_defaults_to_current_topbar_campus(self):
        other_campus = Campus.objects.create(
            tenant=self.tenant,
            code="OTHER",
            name="Other Campus",
        )
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="IS",
            name="Information Systems",
        )
        other_program = Program.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            code="BSIS",
            name="BS Information Systems",
        )
        other_section = Section.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            code="BSIS-1B",
            name="BSIS 1B",
        )
        other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=other_section,
        )
        UserRole.objects.create(
            user=self.admin_user,
            role=self.admin_user.user_roles.get().role,
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
        )
        UserRole.objects.create(
            user=self.faculty_user,
            role=Role.objects.get(code="FACULTY"),
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            offering=other_offering,
            faculty_user=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
        )
        session = self.client.session
        session[ScopeService.SESSION_TENANT_KEY] = self.tenant.id
        session[ScopeService.SESSION_CAMPUS_KEY] = self.campus.id
        session.save()
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_campus_id"], self.campus.id)
        self.assertEqual(response.context["summary"]["offerings"], 1)

        all_campuses_response = self.client.get(self.url, {"campus_id": "all"})

        self.assertEqual(all_campuses_response.status_code, 200)
        self.assertTrue(all_campuses_response.context["all_campuses_selected"])
        self.assertEqual(all_campuses_response.context["summary"]["offerings"], 2)

    def test_grading_analytics_requires_specific_permission(self):
        unauthorized_user = self._user("analytics_without_permission", "No", "Analytics")
        role = Role.objects.create(code="ANALYTICS_NO_READ", name="Analytics No Read")
        admin_access = Permission.objects.get(code="admin_portal.access")
        RolePermission.objects.create(role=role, permission=admin_access)
        UserRole.objects.create(
            user=unauthorized_user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(unauthorized_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_missing_template_analytics_uses_tenant_passing_threshold(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-ANA-001",
            last_name="Missing",
            first_name="Template",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            is_active=True,
            encoded_by_user=self.admin_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            student=student,
            period_grade=Decimal("77.00"),
        )
        SystemSettingService.set(
            "PASSING_GRADE_THRESHOLD",
            "80",
            tenant_id=self.tenant.id,
            value_type="STRING",
            is_active=True,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["failed_rows"], 1)
        self.assertContains(response, "No template")

    def test_no_filter_lists_each_qualifying_offering_separately(self):
        second = self._add_offering(section_code="BSIT1B")
        third = self._add_offering(section_code="BSIT1C")
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["offerings"], 3)
        self.assertEqual(
            {row["id"] for row in response.context["offering_rows"]},
            {self.offering.id, second.id, third.id},
        )

    def test_course_filter_returns_all_matching_sections_without_aggregation(self):
        second = self._add_offering(section_code="BSIT1B")
        other_course = self._add_offering(
            course_code="ECO101",
            course_title="Economics",
            section_code="BSIT1C",
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url, {"course_code": self.course.code})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["offerings"], 2)
        self.assertEqual(
            [row["id"] for row in response.context["offering_rows"]],
            [self.offering.id, second.id],
        )
        self.assertNotIn(other_course.id, [row["id"] for row in response.context["offering_rows"]])
        self.assertContains(response, "Course filtering does not combine sections")

    def test_course_dropdown_has_distinct_authorized_codes_only(self):
        self._add_offering(section_code="BSIT1B")
        self._add_offering(course_code="ECO101", course_title="Economics", section_code="BSIT1C")
        unauthorized = self._add_out_of_scope_offering(course_code="SECRET101")
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url)

        self.assertEqual(
            [option["code"] for option in response.context["course_options"]],
            ["ECO101", "IT101"],
        )
        self.assertNotIn(unauthorized.course.code, response.content.decode())

    def test_forged_unauthorized_course_code_returns_no_unauthorized_data(self):
        unauthorized = self._add_out_of_scope_offering(course_code="SECRET101")
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url, {"course_code": unauthorized.course.code})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["offerings"], 0)
        self.assertEqual(response.context["offering_rows"], [])
        self.assertContains(
            response,
            "No course offerings match the selected filters within your authorized scope.",
        )

    def test_search_matches_course_code_title_section_and_full_faculty_name(self):
        search_faculty = self._user("maria_santos", "Maria", "Santos")
        UserRole.objects.create(
            user=search_faculty,
            role=Role.objects.get(code="FACULTY"),
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        searchable = self._add_offering(
            course_code="BA131-ECO",
            course_title="Applied Economics",
            section_code="BSA-SEARCH",
            faculty_user=search_faculty,
        )
        self.client.force_login(self.admin_user)

        for query in ("BA131", "Applied Economics", "BSA-SEARCH", "Maria Santos"):
            with self.subTest(query=query):
                response = self.client.get(self.url, {"q": query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["summary"]["offerings"], 1)
                self.assertEqual(response.context["offering_rows"][0]["id"], searchable.id)

    def test_search_and_course_filters_work_together(self):
        matching = self._add_offering(section_code="TARGET-SECTION")
        self._add_offering(section_code="OTHER-SECTION")
        self._add_offering(
            course_code="ECO101",
            course_title="Economics",
            section_code="TARGET-SECTION-ECO",
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            self.url,
            {"course_code": self.course.code, "q": "TARGET"},
        )

        self.assertEqual(response.context["summary"]["offerings"], 1)
        self.assertEqual(response.context["offering_rows"][0]["id"], matching.id)

    def test_academic_year_term_and_course_filters_work_together(self):
        second_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
        )
        second_term_offering = self._add_offering(
            section_code="BSIT2A",
            term=second_term,
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(
            self.url,
            {
                "academic_year_id": self.academic_year.id,
                "term_id": second_term.id,
                "course_code": self.course.code,
            },
        )

        self.assertEqual(response.context["summary"]["offerings"], 1)
        self.assertEqual(response.context["offering_rows"][0]["id"], second_term_offering.id)

    def test_summary_cards_use_the_filtered_offering_set(self):
        other = self._add_offering(
            course_code="ECO101",
            course_title="Economics",
            section_code="BSIT1B",
        )
        self._enroll_student(self.offering, "2025-ANA-101")
        self._enroll_student(other, "2025-ANA-102")
        self._enroll_student(other, "2025-ANA-103")
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url, {"course_code": "ECO101"})

        self.assertEqual(response.context["summary"]["offerings"], 1)
        self.assertEqual(response.context["summary"]["active_students"], 2)
        self.assertContains(response, "Showing 1 offering for")
        self.assertContains(response, "<strong>ECO101</strong>")

    def test_clear_action_returns_to_unfiltered_analytics_url(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(self.url, {"course_code": self.course.code, "q": "IT"})

        self.assertContains(response, f'href="{self.url}">Clear</a>')

    def test_area_chair_course_filter_cannot_cross_department_or_campus_scope(self):
        area_chair = self._user("analytics_ac", "Area", "Chair")
        self._grant_analytics_role(area_chair, "AC", department=self.department)
        unauthorized = self._add_out_of_scope_offering(course_code="SECRET101")
        self.client.force_login(area_chair)

        response = self.client.get(
            self.url,
            {"campus_id": "all", "course_code": unauthorized.course.code},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["offerings"], 0)
        self.assertEqual(
            [option["code"] for option in response.context["course_options"]],
            [self.course.code],
        )

    def test_college_dean_filter_uses_only_area_chair_supervision_chain(self):
        college = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
        )
        self.department.parent = college
        self.department.save(update_fields=["parent", "updated_at"])
        area_chair = self._user("dean_chain_ac", "Dean", "Chain")
        self._grant_role(area_chair, "AC", department=self.department)
        dean = self._user("analytics_dean", "College", "Dean")
        self._grant_analytics_role(dean, "COLLEGE_DEAN", department=college)
        no_chair_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            parent=college,
            code="NOCHAIR",
            name="No Chair Department",
        )
        hidden = self._add_out_of_scope_offering(
            course_code="NOCHAIR101",
            campus=self.campus,
            department=no_chair_department,
        )
        self.client.force_login(dean)

        response = self.client.get(self.url, {"campus_id": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [option["code"] for option in response.context["course_options"]],
            [self.course.code],
        )
        self.assertNotIn(hidden.id, [row["id"] for row in response.context["offering_rows"]])

    def test_cao_admin_and_superadmin_keep_their_existing_analytics_scope(self):
        users = []
        for username, role_code in (("analytics_cao", "CAO"), ("analytics_tenant_admin", "TENANT_ADMIN")):
            user = self._user(username, "Scope", role_code)
            self._grant_analytics_role(user, role_code, department=None)
            users.append(user)
        superadmin = User.objects.create_superuser(
            username="analytics_superadmin",
            email="analytics_superadmin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        users.append(superadmin)

        for user in users:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self.url, {"course_code": self.course.code})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["summary"]["offerings"], 1)

    def _add_offering(
        self,
        *,
        course_code=None,
        course_title=None,
        section_code,
        faculty_user=None,
        campus=None,
        department=None,
        program=None,
        academic_year=None,
        term=None,
    ):
        campus = campus or self.campus
        department = department or self.department
        program = program or self.program
        academic_year = academic_year or self.academic_year
        term = term or self.term
        if course_code and course_code != self.course.code:
            course = Course.objects.create(
                tenant=self.tenant,
                campus=campus,
                department=department,
                code=course_code,
                title=course_title or course_code,
            )
        else:
            course = self.course
        section = Section.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            code=section_code,
            name=section_code,
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=academic_year,
            term=term,
            course=course,
            section=section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=campus,
            offering=offering,
            faculty_user=faculty_user or self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )
        return offering

    def _add_out_of_scope_offering(self, *, course_code, campus=None, department=None):
        campus = campus or Campus.objects.create(
            tenant=self.tenant,
            code=f"OUT{Campus.objects.count()}",
            name="Out of Scope Campus",
        )
        department = department or Department.objects.create(
            tenant=self.tenant,
            campus=campus,
            code=f"OUT{Department.objects.count()}",
            name="Out of Scope Department",
        )
        program = Program.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            code=f"OUT{Program.objects.count()}",
            name="Out of Scope Program",
        )
        faculty = User.objects.create_user(
            username=f"outside_faculty_{User.objects.count()}",
            email=f"outside_faculty_{User.objects.count()}@example.com",
            password="testpass123",
            first_name="Outside",
            last_name="Faculty",
            default_tenant=self.tenant,
            default_campus=campus,
            default_department=department,
        )
        UserRole.objects.create(
            user=faculty,
            role=Role.objects.get(code="FACULTY"),
            tenant=self.tenant,
            campus=campus,
            department=department,
        )
        return self._add_offering(
            course_code=course_code,
            course_title="Restricted Course",
            section_code=f"OUT-SECTION-{Section.objects.count()}",
            faculty_user=faculty,
            campus=campus,
            department=department,
            program=program,
        )

    def _enroll_student(self, offering, student_no):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            department=offering.department,
            program=offering.program,
            student_no=student_no,
            last_name="Analytics",
            first_name=student_no,
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            academic_year=offering.academic_year,
            term=offering.term,
            student=student,
            course_offering=offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            is_active=True,
            encoded_by_user=self.admin_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )

    def _grant_role(self, user, role_code, *, department):
        role, _ = Role.objects.get_or_create(code=role_code, defaults={"name": role_code})
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=department,
        )
        return role

    def _grant_analytics_role(self, user, role_code, *, department):
        role = self._grant_role(user, role_code, department=department)
        for permission_code in ("admin_portal.access", "grading_analytics.read"):
            RolePermission.objects.get_or_create(
                role=role,
                permission=Permission.objects.get(code=permission_code),
            )

    def _user(self, username, first_name, last_name):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            first_name=first_name,
            last_name=last_name,
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
