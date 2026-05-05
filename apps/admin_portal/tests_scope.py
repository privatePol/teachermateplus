from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.admin_portal.views import (
    _active_user_activity_rows,
    _mask_student_name,
    _mask_student_number,
    faculty_activity_monitor_view,
    faculty_gradebook_explanation_view,
    faculty_gradebook_monitor_view,
)
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.auditlog.models import AuditLog
from apps.admin_portal.services import AdminScopeService
from apps.core.services.scope import ScopeService
from apps.enrollment.models import Enrollment
from apps.enrollment.services import EnrollmentService
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
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

    def _seed_gradebook_explanation_fixture(self):
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED_EXPLAIN",
            name="General Education Explain",
            is_published=True,
        )
        period = GradingTemplatePeriod.objects.create(
            template=template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        CourseTemplateAssignment.objects.create(course=self.course, grading_template=template)
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_is,
            program=self.program,
            student_no="2025-10777",
            last_name="SANTOS",
            first_name="ANA",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            template_component=component,
            title="Quiz",
            total_score=Decimal("100.00"),
            created_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=Decimal("87.00"),
            computed_score=Decimal("87.00"),
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            student=student,
            class_standing_grade=Decimal("87.00"),
            period_grade=Decimal("87.00"),
            is_finalized=True,
        )
        return period, student

    def test_scoped_faculty_users_uses_faculty_default_department_when_role_department_is_blank(self):
        request = self._build_request()

        faculty_ids = list(AdminScopeService.scoped_faculty_users(request))

        self.assertIn(self.faculty_user.id, faculty_ids)

    def test_scoped_faculty_assignments_follow_faculty_scope_not_offering_department(self):
        request = self._build_request()

        assignment_ids = list(AdminScopeService.scoped_faculty_assignments(request).values_list("id", flat=True))

        self.assertIn(self.assignment.id, assignment_ids)

    def test_parent_department_role_scope_includes_child_departments(self):
        self.department_is.parent = self.department_college
        self.department_is.save(update_fields=["parent", "updated_at"])
        dean_user = User.objects.create_user(
            username="college_dean_scope",
            email="college_dean_scope@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department_college,
        )
        UserRole.objects.create(
            user=dean_user,
            role=self.ac_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_college,
        )

        request = self.factory.get("/")
        request.user = dean_user
        request.session = {}
        ScopeService.attach_scope_to_request(request)

        self.assertIn(self.department_college.id, request.scope["department_ids"])
        self.assertIn(self.department_is.id, request.scope["department_ids"])
        faculty_ids = list(AdminScopeService.scoped_faculty_users(request))
        self.assertIn(self.faculty_user.id, faculty_ids)

    def test_child_department_role_scope_does_not_include_parent_or_sibling_departments(self):
        sibling_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_BA",
            name="Fairview Business Administration",
            parent=self.department_college,
        )
        self.department_is.parent = self.department_college
        self.department_is.save(update_fields=["parent", "updated_at"])

        request = self._build_request()

        self.assertIn(self.department_is.id, request.scope["department_ids"])
        self.assertNotIn(self.department_college.id, request.scope["department_ids"])
        self.assertNotIn(sibling_department.id, request.scope["department_ids"])

    def test_parent_department_role_scope_excludes_inactive_child_departments(self):
        self.department_is.parent = self.department_college
        self.department_is.is_active = False
        self.department_is.save(update_fields=["parent", "is_active", "updated_at"])
        dean_user = User.objects.create_user(
            username="college_dean_inactive_child",
            email="college_dean_inactive_child@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department_college,
        )
        UserRole.objects.create(
            user=dean_user,
            role=self.ac_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_college,
        )

        request = self.factory.get("/")
        request.user = dean_user
        request.session = {}
        ScopeService.attach_scope_to_request(request)

        self.assertIn(self.department_college.id, request.scope["department_ids"])
        self.assertNotIn(self.department_is.id, request.scope["department_ids"])

    def test_admin_scope_campus_ignores_faculty_role_when_admin_role_exists(self):
        second_campus = Campus.objects.create(tenant=self.tenant, code="NCBA-CUBAO", name="Cubao")
        UserRole.objects.create(
            user=self.ac_user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=second_campus,
            department=None,
        )

        scope = ScopeService.build_scope(self.ac_user, tenant_id=self.tenant.id, campus_id=second_campus.id)

        self.assertIn(self.campus.id, scope["campus_ids"])
        self.assertNotIn(second_campus.id, scope["campus_ids"])
        self.assertEqual(scope["campus_id"], self.campus.id)

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

    def test_scoped_students_does_not_leak_programless_students_from_other_campus(self):
        visible_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_is,
            program=None,
            student_no="2025-PROGLESS-001",
            last_name="Visible",
            first_name="Student",
        )
        second_campus = Campus.objects.create(tenant=self.tenant, code="NCBA-CUBAO", name="Cubao")
        second_department = Department.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            code="CUB_COLL_IS",
            name="Cubao Information Systems",
        )
        hidden_student = Student.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
            program=None,
            student_no="2025-PROGLESS-999",
            last_name="Hidden",
            first_name="Student",
        )

        request = self._build_request()
        student_ids = set(AdminScopeService.scoped_students(request).values_list("id", flat=True))

        self.assertIn(visible_student.id, student_ids)
        self.assertNotIn(hidden_student.id, student_ids)

    def test_enrollment_update_rejects_cross_campus_student_mismatch(self):
        enrollment_update = Permission.objects.create(
            code="enrollment.update",
            module="enrollment",
            action="update",
        )
        RolePermission.objects.create(role=self.ac_role, permission=enrollment_update)
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_is,
            program=None,
            student_no="2025-VALID-001",
            last_name="Valid",
            first_name="Student",
        )
        enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.ac_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )
        second_campus = Campus.objects.create(tenant=self.tenant, code="NCBA-CUBAO", name="Cubao")
        second_department = Department.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            code="CUB_COLL_BUS",
            name="Cubao Business",
        )
        other_student = Student.objects.create(
            tenant=self.tenant,
            campus=second_campus,
            department=second_department,
            program=None,
            student_no="2025-OTHER-001",
            last_name="Other",
            first_name="Campus",
        )
        enrollment.student = other_student

        with self.assertRaisesMessage(ValidationError, "Student and offering campus mismatch."):
            EnrollmentService.update_enrollment(
                user=self.ac_user,
                enrollment=enrollment,
                enrollment_status=Enrollment.Status.ACTIVE,
                is_active=True,
                portal=Enrollment.SourcePortal.ADMIN,
            )

    def test_active_user_activity_rows_respects_current_session_timeout_policy(self):
        recent_user = User.objects.create_user(
            username="recent_admin_scope",
            email="recent_admin_scope@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department_is,
        )
        stale_user = User.objects.create_user(
            username="stale_admin_scope",
            email="stale_admin_scope@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department_is,
        )
        UserRole.objects.create(
            user=recent_user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_is,
        )
        UserRole.objects.create(
            user=stale_user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_is,
        )

        def _create_session_for(user, session_key):
            store = SessionStore(session_key=session_key)
            store["_auth_user_id"] = str(user.id)
            store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
            store["_auth_user_hash"] = user.get_session_auth_hash()
            store.save(must_create=True)
            Session.objects.filter(session_key=store.session_key).update(
                expire_date=timezone.now() + timedelta(days=7)
            )

        _create_session_for(recent_user, "recent_scope_session")
        _create_session_for(stale_user, "stale_scope_session")

        recent_log = AuditLog.objects.create(
            actor_user=recent_user,
            portal=AuditLog.Portal.ADMIN,
            action="LOGIN_SUCCESS",
            entity_type="User",
            entity_id=recent_user.id,
            tenant=self.tenant,
            campus=self.campus,
            route_name="accounts:admin_login",
        )
        stale_log = AuditLog.objects.create(
            actor_user=stale_user,
            portal=AuditLog.Portal.ADMIN,
            action="LOGIN_SUCCESS",
            entity_type="User",
            entity_id=stale_user.id,
            tenant=self.tenant,
            campus=self.campus,
            route_name="accounts:admin_login",
        )
        AuditLog.objects.filter(id=recent_log.id).update(created_at=timezone.now() - timedelta(minutes=10))
        AuditLog.objects.filter(id=stale_log.id).update(
            created_at=timezone.now() - timedelta(seconds=(getattr(settings, "SESSION_COOKIE_AGE", 3600) + 300))
        )

        request = self._build_request()
        rows = _active_user_activity_rows(request, limit=25)
        usernames = [row["user"].username for row in rows]

        self.assertIn(recent_user.username, usernames)
        self.assertNotIn(stale_user.username, usernames)

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

    def test_gradebook_monitor_unmasks_student_identity_for_authorized_reviewer(self):
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED_UNMASK",
            name="General Education Unmask",
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
        permission, _ = Permission.objects.get_or_create(
            code="gradebook.view_student_identity",
            defaults={
                "module": "gradebook",
                "action": "view_student_identity",
            },
        )
        RolePermission.objects.get_or_create(role=self.ac_role, permission=permission)

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
        self.assertIn("Authorized Student Identity View", content)
        self.assertIn(student.student_no, content)
        self.assertIn(f"{student.last_name}, {student.first_name}", content)
        self.assertNotIn(_mask_student_number(student.student_no), content)
        self.assertNotIn(_mask_student_name(student), content)

        audit_log = AuditLog.objects.filter(entity_type="FacultyGradebookMonitor").latest("id")
        self.assertEqual(audit_log.actor_user, self.ac_user)
        self.assertEqual(audit_log.metadata_json.get("masked_student_identity"), False)
        self.assertEqual(audit_log.metadata_json.get("student_identity_visible"), True)

    def test_gradebook_explanation_masks_identity_and_logs_safe_audit(self):
        period, student = self._seed_gradebook_explanation_fixture()
        request = self.factory.get(
            f"/admin-portal/academics/faculty-gradebook/explain/{self.offering.id}/{period.id}/{student.id}/PERIOD/"
        )
        request.user = self.ac_user
        request.session = {}
        ScopeService.attach_scope_to_request(request)

        response = faculty_gradebook_explanation_view.__wrapped__.__wrapped__(
            request,
            offering_id=self.offering.id,
            period_id=period.id,
            student_id=student.id,
            grade_type="PERIOD",
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Masked Student", content)
        self.assertIn("identity masked", content)
        self.assertNotIn(f"{student.last_name}, {student.first_name}", content)
        self.assertNotIn(student.student_no, content)
        audit_log = AuditLog.objects.filter(entity_type="GradeExplanation").latest("id")
        self.assertEqual(audit_log.metadata_json.get("masked_student_identity"), True)
        self.assertNotIn("raw_value", audit_log.metadata_json)
        self.assertIsNone(audit_log.before_json)
        self.assertIsNone(audit_log.after_json)

    def test_gradebook_explanation_denies_cross_scope_offering(self):
        period, student = self._seed_gradebook_explanation_fixture()
        other_tenant = Tenant.objects.create(code="OTHER", name="Other")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER-MAIN", name="Other Main")
        other_department = Department.objects.create(tenant=other_tenant, campus=other_campus, code="OTHER-CS", name="Other CS")
        other_program = Program.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            code="OBSCS",
            name="Other BSCS",
        )
        other_year = AcademicYear.objects.create(
            tenant=other_tenant,
            code="2025-2026-O",
            name="Other AY",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        other_term = Term.objects.create(
            tenant=other_tenant,
            academic_year=other_year,
            code="1ST-O",
            name="Other First Term",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )
        other_course = Course.objects.create(tenant=other_tenant, code="OTH101", title="Other Course")
        other_section = Section.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            code="OTH-1A",
            name="OTH 1A",
        )
        other_offering = CourseOffering.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            academic_year=other_year,
            term=other_term,
            course=other_course,
            section=other_section,
        )
        request = self.factory.get(
            f"/admin-portal/academics/faculty-gradebook/explain/{other_offering.id}/{period.id}/{student.id}/PERIOD/"
        )
        request.user = self.ac_user
        request.session = {}
        ScopeService.attach_scope_to_request(request)

        with self.assertRaises(Http404):
            faculty_gradebook_explanation_view.__wrapped__.__wrapped__(
                request,
                offering_id=other_offering.id,
                period_id=period.id,
                student_id=student.id,
                grade_type="PERIOD",
            )

    def test_faculty_activity_monitor_surfaces_login_activity_and_gradebook_work(self):
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.accepted_at = timezone.now()
        self.assignment.save(update_fields=["response_status", "accepted_at"])

        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED_MONITOR",
            name="General Education Monitor",
            is_published=True,
        )
        period = GradingTemplatePeriod.objects.create(
            template=template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        CourseTemplateAssignment.objects.create(course=self.course, grading_template=template)

        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_is,
            program=self.program,
            student_no="2025-20888",
            last_name="SANTOS",
            first_name="MARIA",
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
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            template_component=component,
            title="Quiz 1",
            total_score=Decimal("20.00"),
            created_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=Decimal("18.00"),
            computed_score=Decimal("90.00"),
        )

        self.faculty_user.last_login = timezone.now()
        self.faculty_user.save(update_fields=["last_login"])

        AuditLog.objects.create(
            actor_user=self.faculty_user,
            portal=AuditLog.Portal.FACULTY,
            action="LOGIN_SUCCESS",
            entity_type="User",
            entity_id=str(self.faculty_user.id),
            tenant=self.tenant,
            campus=self.campus,
            metadata_json={"username": self.faculty_user.username},
        )
        AuditLog.objects.create(
            actor_user=self.faculty_user,
            portal=AuditLog.Portal.FACULTY,
            action="CREATE",
            entity_type="GradeActivity",
            entity_id=str(activity.id),
            tenant=self.tenant,
            campus=self.campus,
            after_json={"offering_id": self.offering.id, "period_id": period.id, "title": activity.title},
        )
        AuditLog.objects.create(
            actor_user=self.faculty_user,
            portal=AuditLog.Portal.FACULTY,
            action="UPDATE",
            entity_type="StudentActivityScore",
            entity_id=str(activity.id),
            tenant=self.tenant,
            campus=self.campus,
            metadata_json={"saved_count": 1, "activity_id": activity.id},
        )

        request = self.factory.get(
            "/admin-portal/academics/faculty-activity-monitor/",
            {
                "term_id": self.term.id,
                "faculty_user_id": self.faculty_user.id,
                "window": "7d",
            },
        )
        request.user = self.ac_user
        request.session = {}
        ScopeService.attach_scope_to_request(request)

        response = faculty_activity_monitor_view.__wrapped__.__wrapped__(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Faculty Activity Monitor", content)
        self.assertIn("Active in Window", content)
        self.assertIn("Window Activity Trend", content)
        self.assertIn("Week-over-Week Comparison", content)
        self.assertIn("Flagged Classes", content)
        self.assertIn("Login Activity", content)
        self.assertIn("StudentActivityScore", content)
        self.assertIn("Open Grade Book", content)
        self.assertIn("Active", content)
        self.assertIn("No Grade Encoding", content)

    def test_faculty_activity_monitor_department_filter_follows_faculty_scope_not_offering_department(self):
        self.department_is.parent = self.department_college
        self.department_is.save(update_fields=["parent", "updated_at"])
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.accepted_at = timezone.now()
        self.assignment.save(update_fields=["response_status", "accepted_at"])

        request = self.factory.get(
            "/admin-portal/academics/faculty-activity-monitor/",
            {
                "term_id": self.term.id,
                "department_id": self.department_is.id,
                "window": "7d",
            },
        )
        request.user = self.ac_user
        request.session = {}
        ScopeService.attach_scope_to_request(request)

        response = faculty_activity_monitor_view.__wrapped__.__wrapped__(request)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("faculty_monitor", content)
        self.assertIn("Faculty Activity Monitor", content)

    def test_admin_portal_root_redirects_to_login_for_anonymous_users(self):
        response = self.client.get("/admin-portal", follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin-portal/login/", response["Location"])

    def test_admin_portal_root_slash_redirects_to_login_for_anonymous_users(self):
        response = self.client.get("/admin-portal/", follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin-portal/login/", response["Location"])
