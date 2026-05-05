from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.accounts.models import User
from apps.admin_portal.services import AdminScopeService
from apps.rbac.models import Permission
from apps.tenants.models import Campus, Department, Program, Tenant


class InactiveDepartmentVisibilityTests(TestCase):
    def setUp(self):
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
            ("programs.create", "programs", "create"),
            ("users.create", "users", "create"),
            ("courses.read", "courses", "read"),
            ("courses.create", "courses", "create"),
            ("offerings.read", "offerings", "read"),
        ]:
            Permission.objects.create(code=code, module=module, action=action)
        self.admin = User.objects.create_superuser(
            username="inactive_department_admin",
            email="inactive_department_admin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-03", name="Taytay")
        self.active_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="TAY_BASIC_ED",
            name="Taytay Basic Education",
        )
        self.inactive_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="TAY_BED_SHS_INACTIVE",
            name="Taytay Basic Ed SHS Inactive",
            is_active=False,
        )
        self.active_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            code="ACTPROG",
            name="Active Program",
        )
        self.inactive_department_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.inactive_department,
            code="INACTPROG",
            name="Inactive Department Program",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2526",
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
        )
        self.inactive_department_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.inactive_department,
            code="INACT101",
            title="Inactive Department Course",
        )
        self.inactive_department_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.inactive_department,
            program=self.inactive_department_program,
            code="INACT-1A",
            name="Inactive Department Section",
        )
        CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.inactive_department,
            program=self.inactive_department_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.inactive_department_course,
            section=self.inactive_department_section,
        )
        self.active_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            code="ACT101",
            title="Active Course",
        )
        self.active_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            program=self.active_program,
            code="ACT-1A",
            name="Active Section",
        )

    def assertInactiveDepartmentHidden(self, response):
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertNotIn(self.inactive_department.code, content)
        self.assertNotIn(self.inactive_department.name, content)

    def test_inactive_department_is_hidden_from_program_create_department_choices(self):
        response = self.client.get(reverse("admin_portal:program_create"))

        self.assertInactiveDepartmentHidden(response)

    def test_inactive_department_is_hidden_from_user_create_department_payload(self):
        response = self.client.get(reverse("admin_portal:user_create"))

        self.assertInactiveDepartmentHidden(response)

    def test_inactive_department_is_hidden_from_course_create_department_payload(self):
        response = self.client.get(reverse("admin_portal:course_create"))

        self.assertInactiveDepartmentHidden(response)

    def test_inactive_department_records_are_hidden_from_course_and_offering_lists(self):
        course_response = self.client.get(reverse("admin_portal:course_list"))
        offering_response = self.client.get(reverse("admin_portal:offering_list"))

        self.assertInactiveDepartmentHidden(course_response)
        self.assertInactiveDepartmentHidden(offering_response)
        self.assertNotContains(course_response, self.inactive_department_course.code)
        self.assertNotContains(offering_response, self.inactive_department_section.code)

    def test_own_inactive_records_remain_visible_on_their_maintenance_scopes(self):
        inactive_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            code="OWNINACT101",
            title="Own Inactive Course",
            is_active=False,
        )
        inactive_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            program=self.active_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.active_course,
            section=self.active_section,
            is_active=False,
        )

        scoped_courses = AdminScopeService.scoped_courses(self._request()).values_list("id", flat=True)
        scoped_offerings = AdminScopeService.scoped_course_offerings(self._request()).values_list("id", flat=True)

        self.assertIn(inactive_course.id, scoped_courses)
        self.assertIn(inactive_offering.id, scoped_offerings)

    def test_dependent_scopes_exclude_records_with_inactive_program_course_or_section(self):
        inactive_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            code="INACTIVE_PROGRAM",
            name="Inactive Program",
            is_active=False,
        )
        section_under_inactive_program = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            program=inactive_program,
            code="INACTIVE-PROG-SEC",
            name="Inactive Program Section",
        )
        inactive_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            code="INACTIVECOURSE",
            title="Inactive Course",
            is_active=False,
        )
        inactive_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            program=self.active_program,
            code="INACTIVE-SEC",
            name="Inactive Section",
            is_active=False,
        )
        offering_for_inactive_program = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            program=inactive_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.active_course,
            section=section_under_inactive_program,
        )
        offering_for_inactive_course = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            program=self.active_program,
            academic_year=self.academic_year,
            term=self.term,
            course=inactive_course,
            section=self.active_section,
        )
        offering_for_inactive_section = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.active_department,
            program=self.active_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.active_course,
            section=inactive_section,
        )

        request = self._request()
        scoped_section_ids = set(AdminScopeService.scoped_sections(request).values_list("id", flat=True))
        scoped_offering_ids = set(AdminScopeService.scoped_course_offerings(request).values_list("id", flat=True))

        self.assertNotIn(section_under_inactive_program.id, scoped_section_ids)
        self.assertIn(inactive_section.id, scoped_section_ids)
        self.assertNotIn(offering_for_inactive_program.id, scoped_offering_ids)
        self.assertNotIn(offering_for_inactive_course.id, scoped_offering_ids)
        self.assertNotIn(offering_for_inactive_section.id, scoped_offering_ids)

    def _request(self):
        request = type("Request", (), {})()
        request.user = self.admin
        request.scope = {}
        return request
