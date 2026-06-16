from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import (
    AcademicYear,
    Course,
    CourseOffering,
    FacultyAssignment,
    FacultyAssignmentReplacementLog,
    Section,
    Term,
)
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradeCorrectionRequest,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class FacultyAssignmentReplacementTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-FVW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="INFO",
            name="Information Systems",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIS",
            name="BSIS",
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
            code="A132-ITAPPS",
            title="IT Application Tools",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIS-1A",
            name="BSIS 1A",
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
            code="TEST-REPL",
            name="Replacement Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )
        self.component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="PG_EXAM",
            name="Prelim Exam",
            weight_percentage=Decimal("100.00"),
            score_input_mode="RAW_BASE50",
            is_exam_component=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
        )
        self.current_faculty = self._user("current_faculty", "Current", "Faculty")
        self.replacement_faculty = self._user("replacement_faculty", "Replacement", "Faculty")
        self.third_faculty = self._user("third_faculty", "Third", "Faculty")
        self.admin_user = self._user("replacement_admin", "Campus", "Admin")
        self.view_user = self._user("view_only_dean", "View", "Only")
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-0001",
            last_name="Student",
            first_name="One",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.student,
            course_offering=self.offering,
        )
        self.assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.current_faculty,
            is_primary=True,
            is_active=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.current_faculty,
        )
        self._seed_roles()

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

    def _seed_roles(self):
        faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        admin_role = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin")
        dean_role = Role.objects.create(code="DEAN", name="Dean")
        permissions = {}
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
            ("faculty_portal.access", "faculty_portal", "access"),
            ("faculty_assignments.read", "faculty_assignments", "read"),
            ("faculty_assignments.update", "faculty_assignments", "update"),
            ("faculty_replacement.view", "faculty_replacement", "view"),
            ("faculty_replacement.process", "faculty_replacement", "process"),
        ]:
            permissions[code], _ = Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action},
            )
        RolePermission.objects.get_or_create(role=faculty_role, permission=permissions["faculty_portal.access"])
        for code in [
            "admin_portal.access",
            "faculty_assignments.read",
            "faculty_assignments.update",
            "faculty_replacement.view",
            "faculty_replacement.process",
        ]:
            RolePermission.objects.get_or_create(role=admin_role, permission=permissions[code])
        for code in ["admin_portal.access", "faculty_assignments.read", "faculty_replacement.view"]:
            RolePermission.objects.get_or_create(role=dean_role, permission=permissions[code])
        for faculty in [self.current_faculty, self.replacement_faculty, self.third_faculty]:
            UserRole.objects.create(
                user=faculty,
                role=faculty_role,
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
            )
        UserRole.objects.create(
            user=self.admin_user,
            role=admin_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        UserRole.objects.create(
            user=self.view_user,
            role=dean_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

    def _replacement_payload(self, replacement_type="PERMANENT", faculty=None):
        return {
            "assignment_ids": [str(self.assignment.id)],
            "replacement_faculty": str((faculty or self.replacement_faculty).id),
            "replacement_type": replacement_type,
            "reason_category": "ADMINISTRATIVE_REASSIGNMENT",
            "remarks": "Approved replacement for testing.",
            "confirm_impact": "on",
            "replacement_action": "confirm",
        }

    def _post_replacement(self, payload=None, user=None):
        self.client.force_login(user or self.admin_user)
        return self.client.post(
            reverse("admin_portal:faculty_assignment_replace"),
            payload or self._replacement_payload(),
        )

    def _create_score_records(self):
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Q1",
            total_score=Decimal("100.00"),
            created_by_user=self.current_faculty,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=self.student,
            raw_score=Decimal("0.00"),
            computed_score=Decimal("50.00"),
            encoded_by_user=self.current_faculty,
        )
        return activity

    def test_permanent_replacement_with_no_academic_records_deactivates_old_assignment(self):
        response = self._post_replacement()

        self.assertEqual(response.status_code, 302)
        self.assignment.refresh_from_db()
        new_assignment = FacultyAssignment.objects.get(offering=self.offering, faculty_user=self.replacement_faculty)
        self.assertFalse(self.assignment.is_active)
        self.assertTrue(new_assignment.is_active)
        self.assertTrue(new_assignment.is_primary)
        self.assertEqual(new_assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertEqual(FacultyAssignmentReplacementLog.objects.count(), 1)

    def test_replacement_when_activities_and_scores_exist_preserves_records_and_logs_impact(self):
        activity = self._create_score_records()

        response = self._post_replacement()

        self.assertEqual(response.status_code, 302)
        activity.refresh_from_db()
        self.assertEqual(activity.created_by_user, self.current_faculty)
        score = StudentActivityScore.objects.get(activity=activity, student=self.student)
        self.assertEqual(score.encoded_by_user, self.current_faculty)
        log = FacultyAssignmentReplacementLog.objects.get()
        self.assertEqual(log.impact_snapshot_json["activities"], 1)
        self.assertEqual(log.impact_snapshot_json["scores"], 1)

    def test_replacement_after_submitted_period_grades_logs_submissions_and_period_grades(self):
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.current_faculty,
            submitted_at=timezone.now(),
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            student=self.student,
            period_grade=Decimal("88.00"),
            computed_by_user=self.current_faculty,
        )

        response = self._post_replacement()

        self.assertEqual(response.status_code, 302)
        log = FacultyAssignmentReplacementLog.objects.get()
        self.assertEqual(log.impact_snapshot_json["submissions"], 1)
        self.assertEqual(log.impact_snapshot_json["period_grades"], 1)

    def test_replacement_when_grading_locks_exist_logs_lock_impact(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.offering,
            is_locked=True,
            is_active=True,
        )

        response = self._post_replacement()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(FacultyAssignmentReplacementLog.objects.get().impact_snapshot_json["grading_locks"], 1)

    def test_temporary_substitute_keeps_old_assignment_and_adds_secondary(self):
        response = self._post_replacement(self._replacement_payload(replacement_type="TEMPORARY"))

        self.assertEqual(response.status_code, 302)
        self.assignment.refresh_from_db()
        new_assignment = FacultyAssignment.objects.get(offering=self.offering, faculty_user=self.replacement_faculty)
        self.assertTrue(self.assignment.is_active)
        self.assertTrue(self.assignment.is_primary)
        self.assertTrue(new_assignment.is_active)
        self.assertFalse(new_assignment.is_primary)

    def test_secondary_cofaculty_assignment_keeps_current_primary(self):
        response = self._post_replacement(self._replacement_payload(replacement_type="SECONDARY"))

        self.assertEqual(response.status_code, 302)
        self.assignment.refresh_from_db()
        new_assignment = FacultyAssignment.objects.get(offering=self.offering, faculty_user=self.replacement_faculty)
        self.assertTrue(self.assignment.is_primary)
        self.assertFalse(new_assignment.is_primary)

    def test_wrong_faculty_assignment_deactivates_wrong_assignment(self):
        response = self._post_replacement(self._replacement_payload(replacement_type="WRONG_ASSIGNMENT"))

        self.assertEqual(response.status_code, 302)
        self.assignment.refresh_from_db()
        self.assertFalse(self.assignment.is_active)
        log = FacultyAssignmentReplacementLog.objects.get()
        self.assertEqual(log.replacement_type, "WRONG_ASSIGNMENT")

    def test_old_faculty_loses_access_after_permanent_replacement(self):
        self._post_replacement()

        self.client.force_login(self.current_faculty)
        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "A132-ITAPPS")

    def test_new_faculty_access_follows_acceptance_rules(self):
        self._post_replacement()

        self.client.force_login(self.replacement_faculty)
        response = self.client.get(reverse("faculty_portal:offering_periods", args=[self.offering.id]))

        self.assertEqual(response.status_code, 302)
        new_assignment = FacultyAssignment.objects.get(offering=self.offering, faculty_user=self.replacement_faculty)
        self.assertEqual(new_assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)

    def test_direct_faculty_edit_is_blocked_when_assignment_is_in_use(self):
        self._create_score_records()
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin_portal:faculty_assignment_update", args=[self.assignment.id]),
            {
                "offering": self.offering.id,
                "faculty_user": self.replacement_faculty.id,
                "assignment_note": "Unsafe direct replacement",
                "is_primary": "on",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use Replace Faculty")
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.faculty_user, self.current_faculty)

    def test_audit_log_stores_before_after_and_batch_reference(self):
        self._post_replacement()

        log = FacultyAssignmentReplacementLog.objects.get()
        self.assertTrue(log.batch_reference.startswith("FAR-"))
        self.assertEqual(log.old_assignment_before_json["is_active"], True)
        self.assertEqual(log.old_assignment_after_json["is_active"], False)
        self.assertEqual(log.new_assignment_after_json["faculty_user"], self.replacement_faculty.id)

    def test_view_only_user_can_open_but_cannot_process_direct_post(self):
        self.client.force_login(self.view_user)
        get_response = self.client.get(
            reverse("admin_portal:faculty_assignment_replace"),
            {"assignment_ids": [str(self.assignment.id)]},
        )
        self.assertEqual(get_response.status_code, 200)

        post_response = self.client.post(
            reverse("admin_portal:faculty_assignment_replace"),
            self._replacement_payload(),
        )
        self.assertEqual(post_response.status_code, 403)
