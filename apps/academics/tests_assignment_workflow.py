from datetime import date, timedelta

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.academics.services import FacultyAssignmentWorkflowService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.notifications.models import NotificationQueue
from apps.tenants.models import Campus, Department, Program, Tenant


class FacultyAssignmentWorkflowServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
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
            code="BSIT-1A",
            name="BSIT 1A",
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
        self.faculty_user = User.objects.create_user(
            username="faculty_workflow",
            email="faculty_workflow@example.com",
            password="testpass123",
            first_name="Faculty",
            last_name="Workflow",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            is_primary=False,
        )

    def test_reset_response_window_sets_due_date_and_clears_prior_state(self):
        self.assignment.response_status = FacultyAssignment.ResponseStatus.DECLINED
        self.assignment.faculty_response_note = "Need clarification."
        self.assignment.responded_at = timezone.now()
        self.assignment.reminder_count = 2
        self.assignment.last_reminded_at = timezone.now()

        FacultyAssignmentWorkflowService.reset_response_window(self.assignment, note="Updated load instructions.")

        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertEqual(self.assignment.assignment_note, "Updated load instructions.")
        self.assertIsNotNone(self.assignment.response_due_at)
        self.assertIsNone(self.assignment.accepted_at)
        self.assertIsNone(self.assignment.accepted_by)
        self.assertIsNone(self.assignment.faculty_response_note)
        self.assertIsNone(self.assignment.responded_at)
        self.assertIsNone(self.assignment.last_reminded_at)
        self.assertEqual(self.assignment.reminder_count, 0)

    def test_queue_pending_assignment_reminders_creates_notification_and_updates_counters(self):
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_ASSIGNMENT_FIRST_REMINDER_DAYS_KEY,
            0,
            tenant_id=self.tenant.id,
            value_type="INT",
        )
        FacultyAssignmentWorkflowService.reset_response_window(self.assignment)
        self.assignment.assigned_at = timezone.now() - timedelta(hours=2)
        self.assignment.save(
            update_fields=[
                "assignment_note",
                "accepted_at",
                "accepted_by",
                "response_status",
                "faculty_response_note",
                "responded_at",
                "response_due_at",
                "last_reminded_at",
                "reminder_count",
                "assigned_at",
                "updated_at",
            ]
        )

        created = FacultyAssignmentWorkflowService.queue_pending_assignment_reminders(now=timezone.now())

        self.assignment.refresh_from_db()
        self.assertEqual(created, 1)
        self.assertEqual(self.assignment.reminder_count, 1)
        self.assertIsNotNone(self.assignment.last_reminded_at)
        self.assertTrue(
            NotificationQueue.objects.filter(
                recipient_user=self.faculty_user,
                reference_type=FacultyAssignmentWorkflowService.REMINDER_REFERENCE_TYPE,
            ).exists()
        )

    def test_expire_overdue_assignments_marks_pending_assignment_as_expired(self):
        FacultyAssignmentWorkflowService.reset_response_window(self.assignment)
        self.assignment.response_due_at = timezone.now() - timedelta(minutes=5)
        self.assignment.save(
            update_fields=[
                "assignment_note",
                "accepted_at",
                "accepted_by",
                "response_status",
                "faculty_response_note",
                "responded_at",
                "response_due_at",
                "last_reminded_at",
                "reminder_count",
                "updated_at",
            ]
        )

        expired_count = FacultyAssignmentWorkflowService.expire_overdue_assignments(now=timezone.now())

        self.assignment.refresh_from_db()
        self.assertEqual(expired_count, 1)
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.EXPIRED)
        self.assertIsNone(self.assignment.response_due_at)
        self.assertIsNotNone(self.assignment.responded_at)
