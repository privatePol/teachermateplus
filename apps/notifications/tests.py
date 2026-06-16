from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
)
from apps.notifications.models import FacultyReminder, FacultyReminderEmailQueue, SubmissionNonComplianceNotice
from apps.notifications.services import FacultyReminderService, SubmissionNonComplianceNoticeService
from apps.rbac.models import Role, UserRole
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
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )

    def _enable_non_compliance_notices(
        self,
        *,
        first_notice_after_days: int = 1,
        notice_interval_days: int = 1,
        max_notice_count: int = 3,
    ):
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_NOTICE_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_FIRST_NOTICE_AFTER_DAYS_KEY,
            first_notice_after_days,
            tenant_id=self.tenant.id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_LEVEL_INTERVAL_DAYS_KEY,
            notice_interval_days,
            tenant_id=self.tenant.id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_MAX_NOTICE_COUNT_KEY,
            max_notice_count,
            tenant_id=self.tenant.id,
            value_type="INT",
            is_active=True,
        )

    def _create_period_lock(self, *, deadline_at):
        return GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=deadline_at,
            is_locked=False,
            is_active=True,
        )

    def _create_area_chair_and_cao(self):
        area_chair_user = User.objects.create_user(
            username="area_chair1",
            email="area_chair1@ncba.edu.ph",
            password="testpassword123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            is_active=True,
        )
        area_chair_role = Role.objects.create(code="AREA_CHAIR", name="Area Chairperson", is_active=True)
        UserRole.objects.create(
            user=area_chair_user,
            role=area_chair_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            is_active=True,
        )
        cao_user = User.objects.create_user(
            username="cao1",
            email="cao1@ncba.edu.ph",
            password="testpassword123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            is_active=True,
        )
        cao_role = Role.objects.create(code="CAO", name="Chief Academic Officer", is_active=True)
        UserRole.objects.create(
            user=cao_user,
            role=cao_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            is_active=True,
        )
        return area_chair_user, cao_user

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="no-reply@teachermateplus.local")
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

    def test_sync_activity_reminder_dedupes_existing_activity_reminders(self):
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz Duplicate",
            total_score="50.00",
            activity_date=timezone.localdate() + timedelta(days=2),
            created_by_user=self.user,
            is_active=True,
        )
        FacultyReminder.objects.bulk_create(
            [
                FacultyReminder(
                    tenant=self.tenant,
                    campus=self.campus,
                    faculty_user=self.user,
                    offering=self.offering,
                    grade_activity=activity,
                    reminder_type=FacultyReminder.ReminderType.ACTIVITY_PREPARATION,
                    title="Duplicate Reminder 1",
                    remind_at=timezone.now() + timedelta(hours=1),
                    send_email=True,
                    created_by=self.user,
                ),
                FacultyReminder(
                    tenant=self.tenant,
                    campus=self.campus,
                    faculty_user=self.user,
                    offering=self.offering,
                    grade_activity=activity,
                    reminder_type=FacultyReminder.ReminderType.ACTIVITY_PREPARATION,
                    title="Duplicate Reminder 2",
                    remind_at=timezone.now() + timedelta(hours=2),
                    send_email=True,
                    created_by=self.user,
                ),
            ]
        )

        reminder = FacultyReminderService.sync_activity_reminder(
            activity=activity,
            faculty_user=self.user,
            created_by=self.user,
        )

        self.assertIsNotNone(reminder)
        self.assertEqual(FacultyReminder.objects.filter(grade_activity=activity).count(), 1)
        self.assertEqual(FacultyReminder.objects.filter(grade_activity__isnull=True, is_active=False).count(), 1)

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

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="no-reply@teachermateplus.local")
    def test_submission_non_compliance_notice_progression_and_resolution(self):
        self._enable_non_compliance_notices()
        area_chair_user, cao_user = self._create_area_chair_and_cao()
        unaccepted_user = User.objects.create_user(
            username="unaccepted_faculty",
            email="unaccepted_faculty@ncba.edu.ph",
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
            faculty_user=unaccepted_user,
            response_status=FacultyAssignment.ResponseStatus.PENDING,
            is_primary=False,
            is_active=True,
        )
        first_run = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        self._create_period_lock(deadline_at=first_run - timedelta(days=1, minutes=1))

        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=first_run, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 1)
        first_notice = SubmissionNonComplianceNotice.objects.get()
        self.assertEqual(first_notice.notice_level, SubmissionNonComplianceNotice.NoticeLevel.NOTICE)
        self.assertEqual(first_notice.recipient_emails_json, [self.user.email])
        self.assertNotIn(unaccepted_user.email, first_notice.recipient_emails_json)
        self.assertIn(self.offering.course.code, first_notice.message)
        self.assertIn(self.offering.section.name, first_notice.message)
        self.assertIn(self.period.name, first_notice.message)
        self.assertIn("Course Gradebook Not Submitted", mail.outbox[0].subject)

        result = SubmissionNonComplianceNoticeService.issue_due_notices(
            now=first_run,
            tenant_id=self.tenant.id,
        )
        self.assertEqual(result["issued"], 0)

        second_run = first_run + timedelta(days=1, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=second_run, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 1)
        second_notice = SubmissionNonComplianceNotice.objects.order_by("issued_at", "id")[1]
        self.assertEqual(second_notice.notice_level, SubmissionNonComplianceNotice.NoticeLevel.WARNING)
        self.assertIn(self.user.email, second_notice.recipient_emails_json or [])
        self.assertIn(area_chair_user.email, second_notice.recipient_emails_json or [])
        self.assertNotIn(cao_user.email, second_notice.recipient_emails_json or [])
        self.assertEqual(second_notice.recipient_roles_json, ["FACULTY", "AREA_CHAIR"])

        third_run = second_run + timedelta(days=1, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=third_run, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 1)
        third_notice = SubmissionNonComplianceNotice.objects.order_by("issued_at", "id")[2]
        self.assertEqual(third_notice.notice_level, SubmissionNonComplianceNotice.NoticeLevel.ESCALATION)
        self.assertIn(self.user.email, third_notice.recipient_emails_json or [])
        self.assertIn(area_chair_user.email, third_notice.recipient_emails_json or [])
        self.assertIn(cao_user.email, third_notice.recipient_emails_json or [])
        self.assertEqual(third_notice.recipient_roles_json, ["FACULTY", "AREA_CHAIR", "CAO"])
        self.assertEqual(len(mail.outbox), 3)

        fourth_run = third_run + timedelta(days=1, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=fourth_run, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 0)
        self.assertEqual(SubmissionNonComplianceNotice.objects.count(), 3)
        self.assertEqual(len(mail.outbox), 3)

        submission = GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.user,
            submitted_at=third_run,
        )
        resolved = SubmissionNonComplianceNoticeService.resolve_submitted_notices(
            tenant_id=self.tenant.id,
            now=third_run + timedelta(minutes=5),
        )
        self.assertEqual(resolved, 3)
        self.assertEqual(
            SubmissionNonComplianceNotice.objects.filter(status=SubmissionNonComplianceNotice.Status.RESOLVED).count(),
            3,
        )
        self.assertEqual(
            SubmissionNonComplianceNotice.objects.filter(submission=submission).count(),
            3,
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="no-reply@teachermateplus.local")
    def test_submission_non_compliance_custom_notice_interval_schedule(self):
        self._enable_non_compliance_notices(
            first_notice_after_days=2,
            notice_interval_days=2,
            max_notice_count=3,
        )
        area_chair_user, cao_user = self._create_area_chair_and_cao()
        base_run = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        self._create_period_lock(deadline_at=base_run)

        day_1 = base_run + timedelta(days=1, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=day_1, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 0)

        day_2 = base_run + timedelta(days=2, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=day_2, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 1)
        first_notice = SubmissionNonComplianceNotice.objects.order_by("issued_at", "id")[0]
        self.assertEqual(first_notice.sequence_no, 1)
        self.assertEqual(first_notice.notice_level, SubmissionNonComplianceNotice.NoticeLevel.NOTICE)
        self.assertEqual(first_notice.recipient_emails_json, [self.user.email])

        day_3 = base_run + timedelta(days=3, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=day_3, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 0)

        day_4 = base_run + timedelta(days=4, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=day_4, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 1)
        second_notice = SubmissionNonComplianceNotice.objects.order_by("issued_at", "id")[1]
        self.assertEqual(second_notice.sequence_no, 2)
        self.assertEqual(second_notice.notice_level, SubmissionNonComplianceNotice.NoticeLevel.WARNING)
        self.assertIn(area_chair_user.email, second_notice.recipient_emails_json or [])
        self.assertNotIn(cao_user.email, second_notice.recipient_emails_json or [])

        day_5 = base_run + timedelta(days=5, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=day_5, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 0)

        day_6 = base_run + timedelta(days=6, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=day_6, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 1)
        third_notice = SubmissionNonComplianceNotice.objects.order_by("issued_at", "id")[2]
        self.assertEqual(third_notice.sequence_no, 3)
        self.assertEqual(third_notice.notice_level, SubmissionNonComplianceNotice.NoticeLevel.ESCALATION)
        self.assertIn(cao_user.email, third_notice.recipient_emails_json or [])

        day_8 = base_run + timedelta(days=8, minutes=1)
        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=day_8, tenant_id=self.tenant.id)
        self.assertEqual(result["issued"], 0)
        self.assertEqual(SubmissionNonComplianceNotice.objects.count(), 3)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="no-reply@teachermateplus.local")
    def test_submission_non_compliance_notice_feature_off_prevents_notices(self):
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_NOTICE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        first_run = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        self._create_period_lock(deadline_at=first_run - timedelta(days=3, minutes=1))

        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=first_run, tenant_id=self.tenant.id)

        self.assertEqual(result["issued"], 0)
        self.assertEqual(SubmissionNonComplianceNotice.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="no-reply@teachermateplus.local")
    def test_submission_non_compliance_missing_area_chair_cao_and_hr_recipients_do_not_crash(self):
        self._enable_non_compliance_notices()
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_HR_RECIPIENTS_KEY,
            ["hr@ncba.edu.ph"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )
        first_run = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        self._create_period_lock(deadline_at=first_run - timedelta(days=1, minutes=1))

        for offset in (0, 1, 2):
            result = SubmissionNonComplianceNoticeService.issue_due_notices(
                now=first_run + timedelta(days=offset, minutes=offset),
                tenant_id=self.tenant.id,
            )
            self.assertEqual(result["issued"], 1)

        notices = list(SubmissionNonComplianceNotice.objects.order_by("sequence_no"))
        self.assertEqual([notice.sequence_no for notice in notices], [1, 2, 3])
        for notice in notices:
            self.assertEqual(notice.recipient_emails_json, [self.user.email])
            self.assertNotIn("hr@ncba.edu.ph", notice.recipient_emails_json or [])
        self.assertEqual(notices[0].recipient_roles_json, ["FACULTY"])
        self.assertEqual(notices[1].recipient_roles_json, ["FACULTY"])
        self.assertEqual(notices[2].recipient_roles_json, ["FACULTY"])
        self.assertEqual(len(mail.outbox), 3)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", DEFAULT_FROM_EMAIL="no-reply@teachermateplus.local")
    def test_submission_non_compliance_missing_faculty_email_fails_safely(self):
        self._enable_non_compliance_notices()
        self.user.email = ""
        self.user.save(update_fields=["email"])
        first_run = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)
        self._create_period_lock(deadline_at=first_run - timedelta(days=1, minutes=1))

        result = SubmissionNonComplianceNoticeService.issue_due_notices(now=first_run, tenant_id=self.tenant.id)

        self.assertEqual(result["issued"], 1)
        notice = SubmissionNonComplianceNotice.objects.get()
        self.assertEqual(notice.recipient_emails_json, [])
        self.assertEqual(notice.email_status, SubmissionNonComplianceNotice.Status.FAILED)
        self.assertIn("No recipient emails", notice.email_error_message)
        self.assertEqual(len(mail.outbox), 0)

    def test_submission_non_compliance_area_chairs_include_parent_department_roles(self):
        parent_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
            is_active=True,
        )
        self.department.parent = parent_department
        self.department.save(update_fields=["parent", "updated_at"])
        area_chair_user = User.objects.create_user(
            username="parent_area_chair",
            email="parent_area_chair@ncba.edu.ph",
            password="testpassword123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=parent_department,
            is_active=True,
        )
        area_chair_role = Role.objects.create(code="AREA_CHAIR", name="Area Chairperson", is_active=True)
        UserRole.objects.create(
            user=area_chair_user,
            role=area_chair_role,
            tenant=self.tenant,
            campus=self.campus,
            department=parent_department,
            is_active=True,
        )

        head_users = SubmissionNonComplianceNoticeService._resolve_area_chair_users(offering=self.offering)

        self.assertIn(area_chair_user, head_users)
