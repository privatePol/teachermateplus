from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradeEncodingControl,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class FacultyGradeEncodingControlNoticeTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="TEN-FGEC", name="Tenant Faculty GEC")
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
        self.faculty = User.objects.create_user(
            username="faculty-gec-notice",
            email="faculty-gec-notice@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        faculty_access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access"},
        )
        dashboard_read, _ = Permission.objects.get_or_create(
            code="dashboard.read",
            defaults={"module": "dashboard", "action": "read"},
        )
        role = Role.objects.create(code="FACULTY", name="Faculty")
        RolePermission.objects.create(role=role, permission=faculty_access)
        RolePermission.objects.create(role=role, permission=dashboard_read)
        UserRole.objects.create(user=self.faculty, role=role, tenant=self.tenant, campus=self.campus)
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=self.faculty,
            accepted_at=timezone.now(),
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-10001",
            last_name="Student",
            first_name="One",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            course_offering=self.offering,
            student=self.student,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TPL-FGEC",
            name="Faculty GEC Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
            is_active=True,
        )
        self.component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )
        self.activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Q1",
            total_score=Decimal("20.00"),
            activity_date=date(2026, 1, 10),
            created_by_user=self.faculty,
            is_active=True,
        )
        GradeEncodingControl.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            campus=self.campus,
            course_offering=self.offering,
            status=GradeEncodingControl.Status.CLOSED,
            reason="Enrollment cleanup",
            notice_to_faculty="Please wait for the final class list.",
            is_active=True,
        )
        self.client.force_login(self.faculty)

    def test_dashboard_shows_compact_encoding_closed_status_only(self):
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Encoding Closed")
        self.assertContains(response, "View Class")
        self.assertNotContains(response, "Grade encoding is temporarily disabled")
        self.assertNotContains(response, "Enrollment cleanup")
        self.assertNotContains(response, "Please wait for the final class list.")

    def test_gradebook_pages_show_encoding_closed_notice(self):
        expected = "Grade encoding is temporarily disabled"
        urls = [
            reverse("faculty_portal:period_activities", args=[self.offering.id, self.period.id]),
            reverse("faculty_portal:activity_scores", args=[self.offering.id, self.period.id, self.activity.id]),
            reverse("faculty_portal:period_attendance", args=[self.offering.id, self.period.id]),
            reverse("faculty_portal:period_summary", args=[self.offering.id, self.period.id]),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected)
                self.assertContains(response, "Enrollment cleanup")
                self.assertContains(response, "Please wait for the final class list.")

    def test_direct_activity_post_is_blocked_when_encoding_closed(self):
        response = self.client.post(
            reverse("faculty_portal:period_activities", args=[self.offering.id, self.period.id]),
            {
                "template_component": self.component.id,
                "template_subcomponent": "",
                "template_detail": "",
                "title": "Q2",
                "total_score": "20.00",
                "activity_date": "2026-01-11",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grade encoding is temporarily disabled")
        self.assertContains(response, "Enrollment cleanup")
        self.assertFalse(GradeActivity.objects.filter(offering=self.offering, title="Q2").exists())
