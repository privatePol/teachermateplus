from datetime import date

from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.admin_portal.views import (
    class_list_change_request_list_view,
    class_list_change_request_review_view,
)
from apps.admin_portal.services import AdminScopeService
from apps.core.services.scope import ScopeService
from apps.enrollment.models import ClassListChangeRequest, Enrollment
from apps.enrollment.services import ClassListChangeRequestService
from apps.faculty_portal.views import offering_enrollment_view
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class ClassListChangeRequestWorkflowTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.fairview = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
        self.cubao = Campus.objects.create(tenant=self.tenant, code="NCBA-CUBAO", name="Cubao")
        self.fairview_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
        )
        self.cubao_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            code="CUB_COLL_IS",
            name="Cubao Information Systems",
        )
        self.fairview_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_department,
            code="BSIT-FV",
            name="BSIT Fairview",
        )
        self.cubao_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            department=self.cubao_department,
            code="BSIT-CB",
            name="BSIT Cubao",
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
            code="A132-ITAPPS",
            title="IT Application Tools",
        )
        self.fairview_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_department,
            program=self.fairview_program,
            code="BSIT-FV-1A",
            name="BSIT Fairview 1A",
        )
        self.cubao_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            department=self.cubao_department,
            program=self.cubao_program,
            code="BSIT-CB-1A",
            name="BSIT Cubao 1A",
        )
        self.fairview_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_department,
            program=self.fairview_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.fairview_section,
        )
        self.cubao_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            department=self.cubao_department,
            program=self.cubao_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.cubao_section,
        )

        self.fairview_faculty = User.objects.create_user(
            username="faculty_fairview",
            email="faculty_fairview@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.fairview,
            default_department=self.fairview_department,
        )
        self.cubao_faculty = User.objects.create_user(
            username="faculty_cubao",
            email="faculty_cubao@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.cubao,
            default_department=self.cubao_department,
        )
        self.campus_admin = User.objects.create_user(
            username="campus_admin_fairview",
            email="campus_admin_fairview@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.fairview,
            default_department=self.fairview_department,
        )
        self.superadmin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
        )

        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        self.campus_admin_role = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin")
        self._grant_role_permissions(self.faculty_role, ["faculty_portal.access"])
        self._grant_role_permissions(self.campus_admin_role, [
            "admin_portal.access",
            "class_list_change_requests.view",
            "class_list_change_requests.review",
            "enrollment.create",
            "enrollment.update",
        ])

        UserRole.objects.create(
            user=self.fairview_faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_department,
        )
        UserRole.objects.create(
            user=self.cubao_faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.cubao,
            department=self.cubao_department,
        )
        UserRole.objects.create(
            user=self.campus_admin,
            role=self.campus_admin_role,
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_department,
        )

        self.fairview_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_department,
            program=self.fairview_program,
            student_no="2025-FV-001",
            last_name="Fairview",
            first_name="Student",
        )
        self.fairview_request_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_department,
            program=self.fairview_program,
            student_no="2025-FV-002",
            last_name="Fairview",
            first_name="Request",
        )
        self.cubao_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            department=self.cubao_department,
            program=self.cubao_program,
            student_no="2025-CB-001",
            last_name="Cubao",
            first_name="Student",
        )

        self.fairview_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            offering=self.fairview_offering,
            faculty_user=self.fairview_faculty,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.fairview_faculty,
        )
        self.cubao_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            offering=self.cubao_offering,
            faculty_user=self.cubao_faculty,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.cubao_faculty,
        )

        self.fairview_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            academic_year=self.academic_year,
            term=self.term,
            student=self.fairview_student,
            course_offering=self.fairview_offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.fairview_faculty,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        self.cubao_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            academic_year=self.academic_year,
            term=self.term,
            student=self.cubao_student,
            course_offering=self.cubao_offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.cubao_faculty,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )

        self.fairview_add_request = ClassListChangeRequestService.create_request(
            user=self.fairview_faculty,
            offering=self.fairview_offering,
            request_type=ClassListChangeRequest.RequestType.ADD,
            student=self.fairview_request_student,
            remarks="Please verify this addition.",
        )
        self.cubao_remove_request = ClassListChangeRequestService.create_request(
            user=self.cubao_faculty,
            offering=self.cubao_offering,
            request_type=ClassListChangeRequest.RequestType.REMOVE,
            enrollments=[self.cubao_enrollment],
            remarks="Please verify this removal.",
        )

    def _grant_role_permissions(self, role, permission_codes):
        for code in permission_codes:
            permission, _ = Permission.objects.get_or_create(
                code=code,
                defaults={"module": code.split(".")[0], "action": code.split(".")[1]},
            )
            RolePermission.objects.get_or_create(role=role, permission=permission)

    def _build_request(self, user, *, method="get", path="/", data=None):
        request = getattr(self.factory, method.lower())(path, data or {})
        request.user = user
        request.session = {}
        ScopeService.attach_scope_to_request(request)
        return request

    def test_campus_admin_sees_only_requests_for_assigned_campus(self):
        request = self._build_request(self.campus_admin, method="get", path=reverse("admin_portal:class_list_change_request_list"))
        response = class_list_change_request_list_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fairview")
        self.assertNotContains(response, "Cubao")

    def test_campus_admin_cannot_open_other_campus_review_page(self):
        request = self._build_request(
            self.campus_admin,
            method="get",
            path=reverse("admin_portal:class_list_change_request_review", kwargs={"request_id": self.cubao_remove_request.id}),
        )
        with self.assertRaises(Http404):
            class_list_change_request_review_view(request, request_id=self.cubao_remove_request.id)

    def test_superadmin_sees_all_campus_requests(self):
        request = self._build_request(self.superadmin, method="get", path=reverse("admin_portal:class_list_change_request_list"))
        response = class_list_change_request_list_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fairview")
        self.assertContains(response, "Cubao")

    def test_cancelled_class_list_change_requests_are_hidden_from_admin_queue(self):
        ClassListChangeRequestService.cancel_request(user=self.cubao_faculty, request_obj=self.cubao_remove_request)

        request = self._build_request(self.superadmin, method="get", path=reverse("admin_portal:class_list_change_request_list"))
        queryset = AdminScopeService.scoped_class_list_change_requests(request)

        self.assertEqual(queryset.count(), 1)
        self.assertEqual(queryset.first().id, self.fairview_add_request.id)
        self.assertFalse(queryset.filter(id=self.cubao_remove_request.id).exists())

    def test_approving_add_request_updates_cml_using_safe_enrollment_logic(self):
        request_obj = ClassListChangeRequestService.review_request(
            user=self.campus_admin,
            request_obj=self.fairview_add_request,
            approved=True,
            review_remarks="Verified against the student file.",
        )

        enrollment = Enrollment.objects.get(course_offering=self.fairview_offering, student=self.fairview_request_student)
        self.assertEqual(request_obj.status, ClassListChangeRequest.Status.APPROVED)
        self.assertTrue(enrollment.is_active)
        self.assertEqual(enrollment.enrollment_status, Enrollment.Status.ACTIVE)

    def test_approving_remove_request_updates_cml_using_safe_enrollment_adjustment_logic(self):
        request_obj = ClassListChangeRequestService.review_request(
            user=self.superadmin,
            request_obj=self.cubao_remove_request,
            approved=True,
            review_remarks="Verified against the registrar record.",
        )

        self.cubao_enrollment.refresh_from_db()
        self.assertEqual(request_obj.status, ClassListChangeRequest.Status.APPROVED)
        self.assertFalse(self.cubao_enrollment.is_active)

    def test_rejecting_request_does_not_change_cml_and_stores_reason(self):
        request_obj = ClassListChangeRequestService.review_request(
            user=self.superadmin,
            request_obj=self.cubao_remove_request,
            approved=False,
            review_remarks="Mismatch with AIMS roster.",
        )

        self.cubao_enrollment.refresh_from_db()
        self.assertEqual(request_obj.status, ClassListChangeRequest.Status.REJECTED)
        self.assertEqual(request_obj.review_remarks, "Mismatch with AIMS roster.")
        self.assertTrue(self.cubao_enrollment.is_active)

    def test_faculty_can_see_rejected_request_status_and_reason(self):
        ClassListChangeRequestService.review_request(
            user=self.superadmin,
            request_obj=self.cubao_remove_request,
            approved=False,
            review_remarks="Mismatch with AIMS roster.",
        )

        request = self._build_request(
            self.cubao_faculty,
            method="get",
            path=reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.cubao_offering.id}),
        )
        response = offering_enrollment_view(request, offering_id=self.cubao_offering.id)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "REJECTED")
        self.assertContains(response, "Mismatch with AIMS roster.")
