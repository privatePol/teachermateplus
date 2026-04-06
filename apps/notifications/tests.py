from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.grading.models import GradeActivity, GradingTemplate, GradingTemplateComponent, GradingTemplatePeriod
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
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED",
            name="General Education Template",
            is_active=True,
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        self.component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="QUIZ",
            name="Quizzes",
            weight_percentage=100,
            sort_order=1,
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

    def test_sync_activity_reminder_creates_future_activity_reminder(self):
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz 2",
            total_score="50.00",
            activity_date=timezone.localdate() + timedelta(days=2),
            created_by_user=self.user,
            is_active=True,
        )

        reminder = FacultyReminderService.sync_activity_reminder(
            activity=activity,
            faculty_user=self.user,
            created_by=self.user,
        )

        self.assertIsNotNone(reminder)
        reminder.refresh_from_db()
        self.assertEqual(reminder.grade_activity_id, activity.id)
        self.assertEqual(reminder.title, "Prepare Activity: Quiz 2")
        self.assertEqual(reminder.reminder_type, FacultyReminder.ReminderType.ACTIVITY_PREPARATION)
        self.assertEqual(reminder.period_label, "Prelim")
        self.assertTrue(reminder.send_email)

    def test_sync_activity_reminder_cancels_when_activity_is_no_longer_future(self):
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz 3",
            total_score="50.00",
            activity_date=timezone.localdate() + timedelta(days=3),
            created_by_user=self.user,
            is_active=True,
        )
        reminder = FacultyReminderService.sync_activity_reminder(
            activity=activity,
            faculty_user=self.user,
            created_by=self.user,
        )
        self.assertIsNotNone(reminder)

        activity.activity_date = timezone.localdate()
        activity.save(update_fields=["activity_date", "updated_at"])

        cancelled = FacultyReminderService.sync_activity_reminder(
            activity=activity,
            faculty_user=self.user,
            created_by=self.user,
        )

        self.assertIsNone(cancelled)
        reminder.refresh_from_db()
        self.assertFalse(reminder.is_active)
        self.assertIsNotNone(reminder.cancelled_at)

    def test_sync_activity_reminder_respects_optional_email_setting(self):
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_REMINDER_EMAIL_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz 4",
            total_score="50.00",
            activity_date=timezone.localdate() + timedelta(days=4),
            created_by_user=self.user,
            is_active=True,
        )

        reminder = FacultyReminderService.sync_activity_reminder(
            activity=activity,
            faculty_user=self.user,
            created_by=self.user,
        )

        self.assertIsNotNone(reminder)
        self.assertFalse(reminder.send_email)
