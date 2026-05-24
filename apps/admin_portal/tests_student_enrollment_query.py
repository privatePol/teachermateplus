from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeActivity,
    GradeSubmission,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class StudentEnrollmentQueryViewTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="CUB", name="NCBA-Cubao")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLL",
            name="College",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSA",
            name="Bachelor of Science in Accountancy",
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
            name="Second Semester",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A132-ITAPPS",
            title="IT Application Tools in Business",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSA 1-BSA_1A",
            name="BSA 1-BSA_1A",
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
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-10102",
            last_name="ARCILLA",
            first_name="JANICA",
            middle_name="MARGOE",
            year_level="1",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.student,
            course_offering=self.offering,
        )
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="BSA-GT",
            name="BSA Grading Template",
            is_published=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("25.00"),
        )
        component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("60.00"),
            sort_order=1,
        )
        subcomponent = GradingTemplateSubcomponent.objects.create(
            template_component=component,
            code="QUIZ",
            name="Quizzes",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=component,
            template_subcomponent=subcomponent,
            title="Quiz 1",
            total_score=Decimal("20.00"),
            activity_date=date(2026, 1, 20),
        )
        self.admin_user = User.objects.create_user(
            username="registrar",
            email="registrar@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        self.role, _ = Role.objects.get_or_create(code="REGISTRAR", defaults={"name": "Registrar"})
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
            ("student_enrollment_query.read", "student_enrollment_query", "read"),
        ]:
            permission, _ = Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action},
            )
            RolePermission.objects.get_or_create(role=self.role, permission=permission)
        UserRole.objects.create(
            user=self.admin_user,
            role=self.role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=self.student,
            raw_score=Decimal("18.00"),
            computed_score=Decimal("90.00"),
            encoded_by_user=self.admin_user,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            student=self.student,
            class_standing_grade=Decimal("90.00"),
            exam_grade=Decimal("88.00"),
            period_grade=Decimal("89.00"),
            computed_by_user=self.admin_user,
            is_finalized=True,
        )
        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            student=self.student,
            final_grade=Decimal("89.00"),
            remarks="PASSED",
            computed_by_user=self.admin_user,
            is_submitted=True,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.admin_user,
            submitted_at=timezone.now(),
        )

    def test_student_enrollment_query_displays_consolidated_grade_records(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(
            reverse("admin_portal:student_enrollment_query"),
            {
                "q": "ARCILLA",
                "student_id": self.student.id,
                "academic_year_id": self.academic_year.id,
                "term_id": self.term.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2025-10102")
        self.assertContains(response, "A132-ITAPPS")
        self.assertContains(response, "Prelim")
        self.assertContains(response, "Period Grade")
        self.assertContains(response, "89.00")
        self.assertContains(response, "Quiz 1")
        self.assertContains(response, "18.00")
        self.assertTrue(
            AuditLog.objects.filter(
                action="VIEW_STUDENT_ENROLLMENT_QUERY",
                entity_type="Student",
                entity_id=str(self.student.id),
            ).exists()
        )

    def test_student_enrollment_query_requires_permission(self):
        restricted_role, _ = Role.objects.get_or_create(code="LIMITED_ADMIN", defaults={"name": "Limited Admin"})
        portal_permission = Permission.objects.get(code="admin_portal.access")
        RolePermission.objects.get_or_create(role=restricted_role, permission=portal_permission)
        limited_user = User.objects.create_user(
            username="limited",
            email="limited@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=limited_user,
            role=restricted_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

        self.client.force_login(limited_user)
        response = self.client.get(reverse("admin_portal:student_enrollment_query"))

        self.assertEqual(response.status_code, 403)
