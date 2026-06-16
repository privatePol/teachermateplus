from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.grading.models import GradeEncodingControl
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


class AdminGradeEncodingControlTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="TEN-GEC", name="Tenant GEC")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="CS",
            name="Computer Studies",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSCS",
            name="BSCS",
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
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS101",
            title="Intro to Computing",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSCS-1A",
            name="BSCS 1A",
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
        self.admin = User.objects.create_user(
            username="gec-admin",
            email="gec-admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.faculty = User.objects.create_user(
            username="gec-faculty",
            email="gec-faculty@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={"module": "admin_portal", "action": "access"},
        )
        manage, _ = Permission.objects.get_or_create(
            code="grading_encoding_control.manage",
            defaults={"module": "grading_encoding_control", "action": "manage"},
        )
        faculty_access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access"},
        )
        admin_role = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin")
        faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        RolePermission.objects.create(role=admin_role, permission=admin_access)
        RolePermission.objects.create(role=admin_role, permission=manage)
        RolePermission.objects.create(role=faculty_role, permission=faculty_access)
        UserRole.objects.create(user=self.admin, role=admin_role, tenant=self.tenant, campus=self.campus)
        UserRole.objects.create(user=self.faculty, role=faculty_role, tenant=self.tenant, campus=self.campus)

    def test_authorized_admin_can_create_closed_control_with_notice(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("admin_portal:grade_encoding_control_create"),
            {
                "tenant": self.tenant.id,
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "period_code": "",
                "campus": self.campus.id,
                "course_offering": "",
                "status": GradeEncodingControl.Status.CLOSED,
                "reason": "Enrollment cleanup",
                "notice_to_faculty": "Please wait for final class list.",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            GradeEncodingControl.objects.filter(
                tenant=self.tenant,
                campus=self.campus,
                status=GradeEncodingControl.Status.CLOSED,
            ).exists()
        )

    def test_closed_control_requires_reason_and_notice_in_admin_form(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("admin_portal:grade_encoding_control_create"),
            {
                "tenant": self.tenant.id,
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "campus": self.campus.id,
                "status": GradeEncodingControl.Status.CLOSED,
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter the reason when closing grade encoding.")
        self.assertContains(response, "Enter the faculty notice when closing grade encoding.")
        self.assertEqual(GradeEncodingControl.objects.count(), 0)

    def test_faculty_cannot_manage_encoding_controls(self):
        self.client.force_login(self.faculty)

        response = self.client.get(reverse("admin_portal:grade_encoding_control_list"))

        self.assertEqual(response.status_code, 403)
