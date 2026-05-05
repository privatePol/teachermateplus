from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.accounts.models import User
from apps.rbac.models import Permission
from apps.tenants.models import Campus, Department, Program, Tenant


class DepartmentDropdownLabelTests(TestCase):
    def setUp(self):
        Permission.objects.create(code="admin_portal.access", module="admin_portal", action="access")
        Permission.objects.create(code="courses.read", module="courses", action="read")
        Permission.objects.create(code="offerings.read", module="offerings", action="read")
        self.admin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.cubao = Campus.objects.create(tenant=self.tenant, code="NCBA-01", name="Cubao")
        self.fairview = Campus.objects.create(tenant=self.tenant, code="NCBA-02", name="Fairview")
        self.cubao_college = Department.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.fairview_college = Department.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            code="BSIS",
            name="BSIS",
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
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            code="IT101",
            title="Intro to IT",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            program=self.program,
            code="BSIS-1A",
            name="BSIS 1A",
        )
        CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )

    def test_course_list_department_filter_prefixes_campus_when_all_campuses_are_visible(self):
        response = self.client.get(reverse("admin_portal:course_list"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("NCBA-01 / COLLEGE", content)
        self.assertIn("NCBA-02 / COLLEGE", content)

    def test_offering_list_department_filter_prefixes_campus_when_all_campuses_are_visible(self):
        response = self.client.get(reverse("admin_portal:offering_list"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("NCBA-01 / COLLEGE", content)
        self.assertIn("NCBA-02 / COLLEGE", content)
