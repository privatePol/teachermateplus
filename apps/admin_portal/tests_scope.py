from datetime import date
from decimal import Decimal

from django.test import RequestFactory, TestCase

from apps.accounts.models import User
from apps.admin_portal.views import (
    _mask_student_name,
    _mask_student_number,
    faculty_gradebook_monitor_view,
)
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.auditlog.models import AuditLog
from apps.admin_portal.services import AdminScopeService
from apps.core.services.scope import ScopeService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.rbac.models import Role, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class FacultyMonitoringScopeTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
        self.department_is = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
        )
        self.department_college = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="NCBA-02-COLLEGE",
            name="Fairview College",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_college,
            code="BSIT",
            name="BSIT",
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
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_college,
            program=self.program,
            code="BSIT-1A",
            name="BSIT-1A",
        )
        self.offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_college,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )

        self.faculty_user = User.objects.create_user(
            username="faculty_monitor",
            email="faculty_monitor@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department_is,
        )
        self.ac_user = User.objects.create_user(
            username="ac_monitor",
            email="ac_monitor@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department_is,
        )

        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        self.ac_role = Role.objects.create(code="NCBA_FAIRVIEW_AC", name="Fairview AC")

        UserRole.objects.create(
            user=self.faculty_user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=None,
        )
        UserRole.objects.create(
            user=self.ac_user,
            role=self.ac_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_is,
        )

        self.assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            is_primary=True,
        )

    def _build_request(self):
        request = self.factory.get("/")
        request.user = self.ac_user
        request.session = {}
        ScopeService.attach_scope_to_request(request)
        return request

    def test_scoped_faculty_users_uses_faculty_default_department_when_role_department_is_blank(self):
        request = self._build_request()

        faculty_ids = list(AdminScopeService.scoped_faculty_users(request))

        self.assertIn(self.faculty_user.id, faculty_ids)

    def test_scoped_faculty_assignments_follow_faculty_scope_not_offering_department(self):
        request = self._build_request()

        assignment_ids = list(AdminScopeService.scoped_faculty_assignments(request).values_list("id", flat=True))

        self.assertIn(self.assignment.id, assignment_ids)

    def test_campus_wide_reviewer_can_see_faculty_with_blank_department_role_in_same_campus(self):
        second_campus = Campus.objects.create(tenant=self.tenant, code="NCBA-CUBAO", name="Cubao")
        second_department = Department.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            code="CUB_COLL_IS",
            name="Cubao Information Systems",
        )
        second_program = Program.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
            code="BSIT-C",
            name="BSIT Cubao",
        )
        second_section = Section.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
            program=second_program,
            code="BSIT-C-1A",
            name="BSIT Cubao 1A",
        )
        second_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
            program=second_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=second_section,
        )

        faculty_other_default = User.objects.create_user(
            username="faculty_campus_wide",
            email="faculty_campus_wide@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department_is,
        )
        cao_user = User.objects.create_user(
            username="cao_monitor",
            email="cao_monitor@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=second_campus,
            default_department=second_department,
        )
        cao_role = Role.objects.create(code="NCBA_CAO", name="Chief Academic Officer")

        UserRole.objects.create(
            user=faculty_other_default,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=second_campus,
            department=None,
        )
        UserRole.objects.create(
            user=cao_user,
            role=cao_role,
            tenant=self.tenant,
            campus=second_campus,
            department=None,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            offering=second_offering,
            faculty_user=faculty_other_default,
            is_primary=True,
        )

        request = self.factory.get("/")
        request.user = cao_user
        request.session = {}
        ScopeService.attach_scope_to_request(request)

        faculty_ids = list(AdminScopeService.scoped_faculty_users(request))

        self.assertIn(faculty_other_default.id, faculty_ids)

    def test_gradebook_monitor_masks_student_identity_and_logs_view(self):
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED_V1",
            name="General Education",
            is_published=True,
        )
        period = GradingTemplatePeriod.objects.create(
            template=template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )
        class_standing = GradingTemplateComponent.objects.create(
            template_period=period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("60.00"),
            sort_order=1,
        )
        exam = GradingTemplateComponent.objects.create(
            template_period=period,
            code="EXAM",
            name="Prelim Exam",
            weight_percentage=Decimal("40.00"),
            sort_order=2,
        )
        CourseTemplateAssignment.objects.create(course=self.course, grading_template=template)

        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_is,
            program=self.program,
            student_no="2025-10606",
            last_name="BAUTISTA",
            first_name="KENJIE",
            middle_name="ALFONSO",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )

        class_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            template_component=class_standing,
            title="Q1",
            total_score=Decimal("30.00"),
            created_by_user=self.faculty_user,
        )
        exam_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            template_component=exam,
            title="Prelim Exam",
            total_score=Decimal("100.00"),
            created_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=class_activity,
            student=student,
            raw_score=Decimal("27.00"),
            computed_score=Decimal("90.00"),
        )
        StudentActivityScore.objects.create(
            activity=exam_activity,
            student=student,
            raw_score=Decimal("88.00"),
            computed_score=Decimal("88.00"),
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            student=student,
            class_standing_grade=Decimal("90.00"),
            exam_grade=Decimal("88.00"),
            period_grade=Decimal("89.20"),
            is_finalized=True,
        )

        request = self.factory.get(
            "/admin-portal/academics/faculty-gradebook/",
            {
                "faculty_user_id": self.faculty_user.id,
                "offering_id": self.offering.id,
                "period_id": period.id,
            },
        )
        request.user = self.ac_user
        request.session = {}
        ScopeService.attach_scope_to_request(request)

        response = faculty_gradebook_monitor_view.__wrapped__.__wrapped__(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Faculty Grade Book Monitor", content)
        self.assertIn("Masked Student Identity", content)
        self.assertIn("Active Students", content)
        self.assertIn("Pass Rate", content)
        self.assertIn(_mask_student_number(student.student_no), content)
        self.assertIn(_mask_student_name(student), content)
        self.assertNotIn(student.student_no, content)
        self.assertNotIn(f"{student.last_name}, {student.first_name}", content)

        audit_log = AuditLog.objects.filter(entity_type="FacultyGradebookMonitor").latest("id")
        self.assertEqual(audit_log.actor_user, self.ac_user)
        self.assertEqual(audit_log.portal, AuditLog.Portal.ADMIN)
        self.assertEqual(audit_log.metadata_json.get("masked_student_identity"), True)
