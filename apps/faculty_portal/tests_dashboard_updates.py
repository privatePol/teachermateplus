from datetime import date, timedelta

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment, EnrollmentAdjustmentLog
from apps.faculty_portal.services import FacultyDashboardUpdatesService
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeCorrectionRequest,
    GradeSubmission,
    GradingTemplate,
    GradingTemplatePeriod,
)
from apps.notifications.models import FacultyReminder, SubmissionNonComplianceNotice
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class FacultyDashboardUpdatesTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="DASH", name="Dashboard School")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIT",
            name="BSIT",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2026-2027",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 10, 31),
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS101",
            title="Introduction to Computing",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1A",
            name="BSIT 1A",
        )
        self.other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS102",
            title="Data Structures",
        )
        self.other_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1B",
            name="BSIT 1B",
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP-DASH",
            name="Dashboard Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.template_period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
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
        self.other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.other_course,
            section=self.other_section,
        )
        self.faculty = self._create_faculty("faculty.dashboard")
        self.other_faculty = self._create_faculty("faculty.other")
        self._create_active_assignment(timezone.now() - timedelta(days=3))
        self.student_1 = self._create_student("2026-001", "Ana", "Alvarez")
        self.student_2 = self._create_student("2026-002", "Ben", "Bautista")
        self.student_3 = self._create_student("2026-003", "Cara", "Castro")
        self.student_4 = self._create_student("2026-004", "Dani", "Dela Cruz")
        self.client.force_login(self.faculty)

    def _create_faculty(self, username):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role, _ = Role.objects.get_or_create(code="FACULTY", defaults={"name": "Faculty"})
        for code, module, action in [
            ("faculty_portal.access", "faculty_portal", "access"),
            ("dashboard.read", "dashboard", "read"),
        ]:
            permission, _ = Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action},
            )
            RolePermission.objects.get_or_create(role=role, permission=permission)
        UserRole.objects.create(user=user, role=role, tenant=self.tenant, campus=self.campus, department=self.department)
        return user

    def _create_student(self, student_no, first_name, last_name):
        return Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no=student_no,
            first_name=first_name,
            last_name=last_name,
        )

    def _set_timestamp(self, obj, field_name: str, when):
        obj.__class__.objects.filter(pk=obj.pk).update(**{field_name: when})
        obj.refresh_from_db()
        return obj

    def _create_login_success(self, when):
        log = AuditLog.objects.create(
            actor_user=self.faculty,
            portal=AuditLog.Portal.FACULTY,
            action="LOGIN_SUCCESS",
            entity_type="User",
            entity_id=str(self.faculty.id),
            tenant=self.tenant,
            campus=self.campus,
        )
        return self._set_timestamp(log, "created_at", when)

    def _create_active_assignment(self, when):
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=self.faculty,
            accepted_at=when,
            responded_at=when,
            is_primary=True,
        )
        return self._set_timestamp(assignment, "assigned_at", when)

    def _create_enrollment(self, student, offering, when):
        enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )
        return self._set_timestamp(enrollment, "created_at", when)

    def _create_enrollment_adjustment(self, student, when, *, source_offering=None, destination_offering=None, result=None):
        source_offering = source_offering or self.offering
        destination_offering = destination_offering or self.offering
        log = EnrollmentAdjustmentLog.objects.create(
            student=student,
            source_offering=source_offering,
            destination_offering=destination_offering,
            reason="Enrollment correction",
            processed_by=self.faculty,
            processed_at=when,
            result=result or EnrollmentAdjustmentLog.Result.COMPLETED,
            warning_flags=[],
            impact_snapshot={"classification": "SAFE"},
        )
        return log

    def _create_reopened_submission(self, when):
        submission = GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.template_period,
            status=GradeSubmission.Status.REOPENED,
            submitted_by_user=self.faculty,
            reopened_by_user=self.faculty,
            reopened_at=when,
        )
        return submission

    def _create_correction_request(self, status, when):
        return GradeCorrectionRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.template_period,
            requested_by_user=self.faculty,
            status=status,
            justification="Correction review",
            reviewed_by_user=self.faculty,
            reviewed_at=when,
        )

    def _create_faculty_reminder(self, when):
        reminder = FacultyReminder.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            faculty_user=self.faculty,
            offering=self.offering,
            reminder_type=FacultyReminder.ReminderType.GRADE_SUBMISSION,
            title="Submit the prelim gradebook",
            remind_at=when,
        )
        return self._set_timestamp(reminder, "created_at", when)

    def _create_notice(self, when):
        notice = SubmissionNonComplianceNotice.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.template_period,
            faculty_user=self.faculty,
            notice_level=SubmissionNonComplianceNotice.NoticeLevel.WARNING,
            sequence_no=1,
            title="Course Gradebook Not Submitted",
            message="The periodic grade submission is overdue.",
            deadline_at=when - timedelta(days=1),
            issued_at=when,
        )
        return notice

    def _updates(self, *, now=None):
        now = now or timezone.now()
        return FacultyDashboardUpdatesService.get_dashboard_updates(
            user=self.faculty,
            offerings=[self.offering],
            now=now,
        )

    def test_dashboard_renders_when_no_previous_login_exists(self):
        response = self.client.get(reverse("faculty_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Updates Since Your Last Visit")

    def test_updates_help_popover_markup_is_present(self):
        response = self.client.get(reverse("faculty_portal:dashboard"))
        self.assertContains(response, 'data-updates-help-toggle')
        self.assertContains(response, 'id="updates-help-callout"')
        self.assertContains(response, "What appears here?")
        self.assertContains(
            response,
            "This card shows important changes related to your assigned classes since your previous login.",
        )
        self.assertContains(response, "Only updates from your assigned classes are shown.")

    def test_empty_message_appears_when_no_previous_login_anchor_exists(self):
        response = self.client.get(reverse("faculty_portal:dashboard"))
        self.assertContains(
            response,
            "No recent updates yet. New changes related to your classes will appear here after your next visit.",
        )

    def test_dashboard_renders_empty_state_when_no_updates_exist(self):
        previous_login_at = timezone.now() - timedelta(days=2)
        current_login_at = previous_login_at + timedelta(days=1)
        self._create_login_success(previous_login_at)
        self._create_login_success(current_login_at)

        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertContains(response, "No recent updates yet.")
        self.assertContains(response, "New changes related to your classes will appear here after your next visit.")

    def test_previous_login_comes_from_prior_login_not_current_login(self):
        previous_login_at = timezone.now() - timedelta(days=2)
        event_at = previous_login_at + timedelta(hours=3)
        current_login_at = previous_login_at + timedelta(days=1)
        self._create_login_success(previous_login_at)
        self._create_login_success(current_login_at)
        self._create_enrollment(self.student_1, self.offering, event_at)

        updates = self._updates(now=current_login_at + timedelta(minutes=5))

        self.assertTrue(updates["has_previous_login"])
        self.assertEqual(updates["since_at"], previous_login_at)
        self.assertEqual(updates["current_login_at"], current_login_at)
        self.assertTrue(any("was added" in item["message"] for item in updates["items"]))

    def test_faculty_sees_updates_only_from_assigned_offerings(self):
        previous_login_at = timezone.now() - timedelta(days=2)
        current_login_at = previous_login_at + timedelta(days=1)
        self._create_login_success(previous_login_at)
        self._create_login_success(current_login_at)
        self._create_enrollment(self.student_1, self.offering, previous_login_at + timedelta(hours=2))
        self._create_enrollment(self.student_2, self.other_offering, previous_login_at + timedelta(hours=3))

        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertContains(response, "was added to CS101 / BSIT 1A")
        self.assertNotContains(response, "CS102 / BSIT 1B")

    def test_faculty_does_not_see_updates_from_unassigned_offerings(self):
        previous_login_at = timezone.now() - timedelta(days=2)
        current_login_at = previous_login_at + timedelta(days=1)
        self._create_login_success(previous_login_at)
        self._create_login_success(current_login_at)
        self._create_enrollment(self.student_1, self.other_offering, previous_login_at + timedelta(hours=2))

        updates = self._updates(now=current_login_at + timedelta(minutes=5))

        self.assertFalse(any(item["offering"] and item["offering"].id == self.other_offering.id for item in updates["items"]))

    def test_new_enrollment_after_previous_login_appears(self):
        previous_login_at = timezone.now() - timedelta(days=2)
        current_login_at = previous_login_at + timedelta(days=1)
        self._create_login_success(previous_login_at)
        self._create_login_success(current_login_at)
        enrollment_at = previous_login_at + timedelta(hours=2)
        self._create_enrollment(self.student_1, self.offering, enrollment_at)

        updates = self._updates(now=current_login_at + timedelta(minutes=5))

        self.assertTrue(any(item["when"] == enrollment_at for item in updates["items"]))
        self.assertTrue(any("Alvarez Ana was added" in item["message"] for item in updates["items"]))

    def test_new_enrollment_before_previous_login_does_not_appear(self):
        previous_login_at = timezone.now() - timedelta(days=2)
        current_login_at = previous_login_at + timedelta(days=1)
        self._create_login_success(previous_login_at)
        self._create_login_success(current_login_at)
        self._create_enrollment(self.student_1, self.offering, previous_login_at - timedelta(hours=1))

        updates = self._updates(now=current_login_at + timedelta(minutes=5))

        self.assertFalse(any("Alvarez Ana was added" in item["message"] for item in updates["items"]))

    def test_reopened_grade_submission_after_previous_login_appears(self):
        previous_login_at = timezone.now() - timedelta(days=2)
        current_login_at = previous_login_at + timedelta(days=1)
        self._create_login_success(previous_login_at)
        self._create_login_success(current_login_at)
        reopen_at = previous_login_at + timedelta(hours=4)
        self._create_reopened_submission(reopen_at)

        updates = self._updates(now=current_login_at + timedelta(minutes=5))

        self.assertTrue(any("gradebook was reopened" in item["message"] for item in updates["items"]))

    def test_correction_approval_and_rejection_after_previous_login_appear(self):
        previous_login_at = timezone.now() - timedelta(days=2)
        current_login_at = previous_login_at + timedelta(days=1)
        self._create_login_success(previous_login_at)
        self._create_login_success(current_login_at)
        approved_at = previous_login_at + timedelta(hours=2)
        rejected_at = previous_login_at + timedelta(hours=3)
        self._create_correction_request(GradeCorrectionRequest.Status.APPROVED, approved_at)
        self._create_correction_request(GradeCorrectionRequest.Status.REJECTED, rejected_at)

        updates = self._updates(now=current_login_at + timedelta(minutes=5))

        messages = [item["message"] for item in updates["items"]]
        self.assertTrue(any("correction request was approved" in message for message in messages))
        self.assertTrue(any("correction request was rejected" in message for message in messages))

    def test_dashboard_shows_only_the_newest_five_updates(self):
        previous_login_at = timezone.now() - timedelta(days=2)
        current_login_at = previous_login_at + timedelta(days=1)
        self._create_login_success(previous_login_at)
        self._create_login_success(current_login_at)
        for index in range(6):
            self._create_enrollment(
                self._create_student(f"2026-1{index:02d}", f"Student{index}", f"Alpha{index}"),
                self.offering,
                previous_login_at + timedelta(minutes=index + 1),
            )

        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertContains(response, "Showing the latest 5 updates.")
        self.assertEqual(response.content.decode().count('<div class="faculty-update-item">'), 5)
