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
