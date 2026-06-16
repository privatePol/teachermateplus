from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.grading.models import CourseTemplateAssignment, GradingTemplate
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


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
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIT",
            name="BS Information Technology",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1A",
            name="BSIT 1A",
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
        self.inactive_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A103",
            title="Inactive Assignment Course",
        )
        self.inactive_assignment = CourseTemplateAssignment.objects.create(
            course=self.inactive_course,
            grading_template=template,
            effective_from_term=self.term,
            is_active=False,
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
        self.assertEqual({row.course.id for row in rows}, {self.course_without_template.id, self.inactive_course.id})

    def test_can_filter_current_offerings_without_course_grading_template(self):
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course_without_template,
            section=self.section,
            status=CourseOffering.Status.OPEN,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("admin_portal:course_template_assignment_list"),
            {"offerings_without_template": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Offerings Without Course Template")
        self.assertContains(response, "Course Without Template")
        self.assertContains(response, "BSIT-1A")
        rows = list(response.context["offering_page_obj"].object_list)
        self.assertEqual([row.offering.id for row in rows], [offering.id])

    def test_assignment_list_separates_active_and_inactive_records(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("admin_portal:course_template_assignment_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Course Template Assignments")
        self.assertContains(response, "Inactive Course Template Assignments")
        active_rows = list(response.context["active_page_obj"].object_list)
        inactive_rows = list(response.context["inactive_page_obj"].object_list)
        self.assertEqual([row.course_id for row in active_rows], [self.course_with_template.id])
        self.assertEqual([row.course_id for row in inactive_rows], [self.inactive_course.id])

    def test_course_lists_are_sorted_by_title_then_code(self):
        course_z = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A099",
            title="Zulu Course",
        )
        course_a = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A200",
            title="Alpha Course",
        )
        template = GradingTemplate.objects.get(code="TMP1")
        CourseTemplateAssignment.objects.create(
            course=course_z,
            grading_template=template,
            effective_from_term=self.term,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=course_a,
            grading_template=template,
            effective_from_term=self.term,
            is_active=True,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin_portal:course_template_assignment_list"))

        active_titles = [
            row.course.title for row in response.context["active_page_obj"].object_list
        ]
        self.assertEqual(
            active_titles,
            ["Alpha Course", "Course With Template", "Zulu Course"],
        )
        filter_titles = [course.title for course in response.context["courses"]]
        self.assertEqual(filter_titles, sorted(filter_titles))
