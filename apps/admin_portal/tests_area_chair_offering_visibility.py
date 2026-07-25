from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.admin_portal.services import AdminScopeService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


class AreaChairCourseOfferingVisibilityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.fairview = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
        self.cubao = Campus.objects.create(tenant=self.tenant, code="NCBA-CUBAO", name="Cubao")
        self.college = Department.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.ba = Department.objects.create(
            tenant=self.tenant, campus=self.fairview, parent=self.college, code="BA", name="Business Administration"
        )
        self.is_department = Department.objects.create(
            tenant=self.tenant, campus=self.fairview, parent=self.college, code="IS", name="Information Systems"
        )
        self.basic_ed = Department.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            code="BED",
            name="Basic Education",
            unit_type=Department.UnitType.DIVISION,
        )
        self.jhs = Department.objects.create(
            tenant=self.tenant, campus=self.fairview, parent=self.basic_ed, code="JHS", name="Junior High School"
        )
        self.cubao_college = Department.objects.create(
            tenant=self.tenant, campus=self.cubao, code="COLLEGE", name="Cubao College"
        )
        self.cubao_is = Department.objects.create(
            tenant=self.tenant, campus=self.cubao, parent=self.cubao_college, code="IS", name="Cubao IS"
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2627",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST-SEM",
            name="First Semester",
            sequence_no=1,
        )
        self.course = Course.objects.create(tenant=self.tenant, code="GEN101", title="General Course")
        self.ba_offering = self._offering(self.fairview, self.ba, "BSBA", "BA-1A")
        self.is_offering = self._offering(self.fairview, self.is_department, "BSIS", "IS-1A")
        self.jhs_offering = self._offering(self.fairview, self.jhs, "JHS", "JHS-1")
        self.cubao_offering = self._offering(self.cubao, self.cubao_is, "BSIS-C", "IS-C-1A")

        self.user = User.objects.create_user(
            username="area_chair_ba",
            email="area_chair_ba@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.fairview,
            default_department=self.ba,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.role = Role.objects.create(code="AC", name="Area Chairman")
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
            ("offerings.read", "offerings", "read"),
        ]:
            permission = Permission.objects.create(code=code, module=module, action=action)
            RolePermission.objects.create(role=self.role, permission=permission)
        UserRole.objects.create(
            user=self.user,
            role=self.role,
            tenant=self.tenant,
            campus=self.fairview,
            department=self.ba,
        )
        self.client.force_login(self.user)

    def _offering(self, campus, department, program_code, section_code):
        program = Program.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            code=program_code,
            name=program_code,
        )
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

    def test_area_chair_offering_list_includes_same_campus_college_siblings_only(self):
        response = self.client.get(reverse("admin_portal:offering_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BA-1A")
        self.assertContains(response, "IS-1A")
        self.assertNotContains(response, "JHS-1")
        self.assertNotContains(response, "IS-C-1A")
        self.assertContains(response, f'<option value="{self.ba.id}" >NCBA-FAIRVIEW / BA</option>', html=False)
        self.assertContains(response, f'<option value="{self.is_department.id}" >NCBA-FAIRVIEW / IS</option>', html=False)
        self.assertNotContains(response, f'<option value="{self.jhs.id}"', html=False)

    def test_standard_scope_service_remains_department_limited(self):
        response = self.client.get(reverse("admin_portal:offering_list"))
        request = response.wsgi_request

        standard_ids = set(AdminScopeService.scoped_course_offerings(request).values_list("id", flat=True))
        list_ids = set(AdminScopeService.scoped_course_offerings_for_list(request).values_list("id", flat=True))

        self.assertEqual(standard_ids, {self.ba_offering.id})
        self.assertEqual(list_ids, {self.ba_offering.id, self.is_offering.id})

    def test_area_chair_unassigned_print_uses_same_college_list_scope(self):
        response = self.client.get(reverse("admin_portal:offering_unassigned_print"))

        self.assertEqual(response.status_code, 200)
        row_ids = {row.id for row in response.context["report_rows"]}
        self.assertSetEqual(row_ids, {self.ba_offering.id, self.is_offering.id})
        self.assertContains(response, "BA-1A")
        self.assertContains(response, "IS-1A")
        self.assertNotContains(response, "JHS-1")
        self.assertNotContains(response, "IS-C-1A")
