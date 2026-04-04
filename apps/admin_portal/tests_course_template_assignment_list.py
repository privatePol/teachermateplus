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


class CourseTemplateAssignmentListTests(TestCase):
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
        self.course_with_template = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A101",
            title="Course With Template",
        )
        self.course_without_template = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A102",
            title="Course Without Template",
        )
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP1",
            name="Template 1",
            is_published=True,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course_with_template,
            grading_template=template,
            effective_from_term=self.term,
            is_active=True,
        )

        self.user = User.objects.create_user(
            username="assignment_reader",
            email="assignment_reader@example.com",
            password="testpass123",
            first_name="Reader",
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
        assignment_read = Permission.objects.create(
            code="course_template_assignments.read",
            module="course_template_assignments",
            action="read",
        )
        assignment_create = Permission.objects.create(
            code="course_template_assignments.create",
            module="course_template_assignments",
            action="create",
        )
        RolePermission.objects.create(role=role, permission=admin_access)
        RolePermission.objects.create(role=role, permission=assignment_read)
        RolePermission.objects.create(role=role, permission=assignment_create)
        UserRole.objects.create(
            user=self.user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

    def test_can_filter_courses_without_grading_template(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("admin_portal:course_template_assignment_list"),
            {"without_template": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Courses Without Grading Template")
        self.assertContains(response, "Course Without Template")
        self.assertContains(response, "No grading template assigned")
        rows = list(response.context["page_obj"].object_list)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].course.id, self.course_without_template.id)
