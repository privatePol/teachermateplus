from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.notifications.models import FacultyReminder, FacultyReminderEmailQueue
from apps.notifications.services import FacultyReminderService
from apps.tenants.models import Campus, Department, Program, Tenant

User = get_user_model()


class FacultyReminderServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="National College of Business and Arts", is_active=True)
        self.campus = Campus.objects.create(tenant=self.tenant, code="FAIRV", name="Fairview", is_active=True)
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="IT",
            name="Information Technology",
            is_active=True,
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIT",
            name="BS Information Technology",
            level="COLLEGE",
            is_active=True,
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2025",
            name="Academic Year 2025-2026",
            start_date="2025-06-01",
            end_date="2026-05-31",
            is_active=True,
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            is_active=True,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A132-ITAPPS",
            title="IT Application Tools",
            is_active=True,
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSA_1A",
            name="BSA 1A",
            is_active=True,
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
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="faculty1",
            email="faculty1@ncba.edu.ph",
            password="testpassword123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            is_active=True,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.user,
            is_primary=True,
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_REMINDER_CENTER_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_REMINDER_EMAIL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="no-reply@edugradespro.local")
    def test_queue_and_process_faculty_reminder_email(self):
        reminder = FacultyReminder.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            faculty_user=self.user,
            offering=self.offering,
            reminder_type=FacultyReminder.ReminderType.ACTIVITY_PREPARATION,
            title="Prepare Quiz 1",
            period_label="PRELIM",
            notes="Prepare the quiz and align the encoded scores before the deadline.",
            remind_at=timezone.now() - timedelta(minutes=1),
            due_at=timezone.now() + timedelta(days=1),
            send_email=True,
            created_by=self.user,
            is_active=True,
        )

        queued = FacultyReminderService.queue_due_email_notifications(now=timezone.now(), tenant_id=self.tenant.id)
        self.assertEqual(queued, 1)
        self.assertEqual(FacultyReminderEmailQueue.objects.count(), 1)
        queue_entry = FacultyReminderEmailQueue.objects.get()
        self.assertEqual(queue_entry.status, FacultyReminderEmailQueue.Status.PENDING)

        processed = FacultyReminderService.process_email_queue(now=timezone.now(), batch_size=10)
        self.assertEqual(processed, 1)
        queue_entry.refresh_from_db()
        reminder.refresh_from_db()
        self.assertEqual(queue_entry.status, FacultyReminderEmailQueue.Status.SENT)
        self.assertIsNotNone(queue_entry.sent_at)
        self.assertIsNotNone(reminder.email_last_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Prepare Quiz 1", mail.outbox[0].subject)
