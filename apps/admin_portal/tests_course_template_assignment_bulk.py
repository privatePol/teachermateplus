from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, Term
from apps.grading.models import CourseTemplateAssignment, GradingTemplate
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Tenant


class BulkCourseTemplateAssignmentTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
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

        self.course_1 = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A101",
            title="Course 1",
        )
        self.course_2 = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A102",
            title="Course 2",
        )
        self.course_3 = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A103",
            title="Course 3",
        )

        self.template_target = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP1",
            name="Template 1",
            is_published=True,
            is_active=True,
        )
        self.template_other = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP2",
            name="Template 2",
            is_published=True,
            is_active=True,
        )

        CourseTemplateAssignment.objects.create(
            course=self.course_1,
            grading_template=self.template_other,
            effective_from_term=self.term,
            is_active=True,
        )
        self.inactive_assignment = CourseTemplateAssignment.objects.create(
            course=self.course_3,
            grading_template=self.template_target,
            effective_from_term=self.term,
            is_active=False,
        )

        self.user = User.objects.create_user(
            username="template_admin",
            email="template_admin@example.com",
            password="testpass123",
            first_name="Template",
            last_name="Admin",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin")
        admin_access = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
        )
        assignment_create = Permission.objects.create(
            code="course_template_assignments.create",
            module="course_template_assignments",
            action="create",
        )
        assignment_read = Permission.objects.create(
            code="course_template_assignments.read",
            module="course_template_assignments",
            action="read",
        )
        RolePermission.objects.create(role=role, permission=admin_access)
        RolePermission.objects.create(role=role, permission=assignment_create)
        RolePermission.objects.create(role=role, permission=assignment_read)
        UserRole.objects.create(
            user=self.user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

    def test_bulk_course_template_assignment_creates_reactivates_and_skips(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("admin_portal:course_template_assignment_create"),
            {
                "courses": [self.course_1.id, self.course_2.id, self.course_3.id],
                "grading_template": self.template_target.id,
                "effective_from_term": self.term.id,
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CourseTemplateAssignment.objects.filter(
                course=self.course_2,
                grading_template=self.template_target,
                effective_from_term=self.term,
                is_active=True,
            ).exists()
        )
        self.inactive_assignment.refresh_from_db()
        self.assertTrue(self.inactive_assignment.is_active)
        self.assertFalse(
            CourseTemplateAssignment.objects.filter(
                course=self.course_1,
                grading_template=self.template_target,
                effective_from_term=self.term,
            ).exists()
        )
