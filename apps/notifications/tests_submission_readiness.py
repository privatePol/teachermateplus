from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core import mail
from django.test import TestCase, override_settings
from unittest.mock import patch

from apps.admin_portal.tests_submission_readiness import GradeSubmissionReadinessTests
from apps.admin_portal.submission_readiness import GradeSubmissionReadinessService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.grading.models import GradingPeriodLock
from apps.notifications.models import SubmissionReadinessNotificationLog
from apps.notifications.submission_readiness import SubmissionReadinessEmailService
from apps.rbac.models import UserRole
from apps.tenants.models import Campus, Department


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="https://grades.example.edu")
class SubmissionReadinessEmailTests(TestCase):
    _monitor_role = GradeSubmissionReadinessTests._monitor_role
    _user = GradeSubmissionReadinessTests._user
    _faculty = GradeSubmissionReadinessTests._faculty
    _offering = GradeSubmissionReadinessTests._offering
    _assignment = GradeSubmissionReadinessTests._assignment
    _student = GradeSubmissionReadinessTests._student
    _activity = GradeSubmissionReadinessTests._activity
    _score = GradeSubmissionReadinessTests._score

    def setUp(self):
        GradeSubmissionReadinessTests.setUp(self)
        self.now = datetime(2026, 7, 20, 1, 0, tzinfo=ZoneInfo("Asia/Manila"))
        self.deadline = self.now + timedelta(days=5, hours=22, minutes=59)
        GradingPeriodLock.objects.create(
            tenant=self.tenant, campus=self.campus, academic_year=self.academic_year, term=self.term,
            period_code="PRELIM", scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=self.deadline, is_locked=False,
        )
        self._student("2026-001", self.offering)
        self._activity(self.offering)
        self._set_policy(enabled=True)

    def _set_policy(self, *, enabled, threshold=50, days=5, roles=None, repeat=False):
        values = {
            FeatureSettingsService.SUBMISSION_READINESS_EMAIL_ENABLED_KEY: (enabled, "BOOL"),
            FeatureSettingsService.SUBMISSION_READINESS_EMAIL_DAYS_BEFORE_KEY: (days, "INT"),
            FeatureSettingsService.SUBMISSION_READINESS_EMAIL_THRESHOLD_KEY: (threshold, "INT"),
            FeatureSettingsService.SUBMISSION_READINESS_EMAIL_ROLE_CODES_KEY: (roles or ["AREA_CHAIR"], "JSON"),
            FeatureSettingsService.SUBMISSION_READINESS_EMAIL_REPEAT_KEY: (repeat, "BOOL"),
        }
        for key, (value, value_type) in values.items():
            SystemSettingService.set(key, value, tenant_id=self.tenant.id, value_type=value_type)

    def test_exact_day_sends_scope_limited_html_and_text_then_deduplicates(self):
        first = SubmissionReadinessEmailService.run(now=self.now)
        self.assertEqual(first["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.area_chair.email])
        self.assertIn(self.faculty.full_name, message.body)
        self.assertIn("does not automatically indicate faculty non-compliance", message.body)
        self.assertIn("https://grades.example.edu/admin-portal/grading/submission-readiness/", message.body)
        self.assertTrue(message.alternatives)
        log = SubmissionReadinessNotificationLog.objects.get()
        self.assertEqual(log.assignment_count, 1)
        self.assertNotIn("student", str(log.metadata_json).lower())

        second = SubmissionReadinessEmailService.run(now=self.now)
        self.assertEqual(second["duplicates"], 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_wrong_day_disabled_and_exact_threshold_do_not_send(self):
        wrong_day = SubmissionReadinessEmailService.run(now=self.now - timedelta(days=1))
        self.assertEqual(wrong_day["sent"], 0)
        self._set_policy(enabled=False)
        disabled = SubmissionReadinessEmailService.run(now=self.now)
        self.assertEqual(disabled["sent"], 0)

        self._set_policy(enabled=True, threshold=0)
        threshold = SubmissionReadinessEmailService.run(now=self.now)
        self.assertEqual(threshold["sent"], 0)

    def test_dry_run_logs_without_sending(self):
        result = SubmissionReadinessEmailService.run(now=self.now, dry_run=True)
        self.assertEqual(result["dry_run"], 1)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(SubmissionReadinessNotificationLog.objects.get().status, "DRY_RUN")
        later = SubmissionReadinessEmailService.run(now=self.now + timedelta(seconds=1), dry_run=True)
        self.assertEqual(later["dry_run"], 1)
        self.assertEqual(SubmissionReadinessNotificationLog.objects.count(), 2)

    def test_dean_and_cao_use_authorized_scope(self):
        dean_role = self._monitor_role("COLLEGE_DEAN", "College Dean")
        dean = self._user("college-dean", self.campus, self.college)
        UserRole.objects.create(user=dean, role=dean_role, tenant=self.tenant, campus=self.campus, department=self.college)
        cao_role = self._monitor_role("CAO", "Chief Academic Officer")
        cao = self._user("cao", self.campus, self.department)
        UserRole.objects.create(user=cao, role=cao_role, tenant=self.tenant, campus=self.campus, department=None)
        self._set_policy(enabled=True, roles=["COLLEGE_DEAN", "CAO"])

        with patch.object(
            SubmissionReadinessEmailService,
            "_active_assignments",
            wraps=SubmissionReadinessEmailService._active_assignments,
        ) as assignments_call, patch(
            "apps.notifications.submission_readiness.GradeSubmissionReadinessService.calculate",
            wraps=GradeSubmissionReadinessService.calculate,
        ) as readiness_call:
            result = SubmissionReadinessEmailService.run(now=self.now)

        self.assertEqual(result["sent"], 2)
        self.assertEqual(assignments_call.call_count, 1)
        self.assertEqual(readiness_call.call_count, 1)
        self.assertEqual({message.to[0] for message in mail.outbox}, {dean.email, cao.email})

    def test_multiple_recipient_roles_consolidate_one_email(self):
        cao_role = self._monitor_role("CAO", "Chief Academic Officer")
        UserRole.objects.create(user=self.area_chair, role=cao_role, tenant=self.tenant, campus=self.campus)
        self._set_policy(enabled=True, roles=["AREA_CHAIR", "CAO"])

        result = SubmissionReadinessEmailService.run(now=self.now)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        log = SubmissionReadinessNotificationLog.objects.get()
        self.assertEqual(set(log.recipient_roles_json), {"AREA_CHAIR", "CAO"})

    def test_repeat_reminder_uses_actual_remaining_day(self):
        self._set_policy(enabled=True, repeat=True)
        result = SubmissionReadinessEmailService.run(now=self.now + timedelta(days=1))
        self.assertEqual(result["sent"], 1)
        self.assertIn("4 Days Before Deadline", mail.outbox[0].subject)

    def test_area_chair_does_not_receive_cross_campus_assignment(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other Campus")
        other_department = Department.objects.create(
            tenant=self.tenant, campus=other_campus, code="OTHER-CS", name="Other Computer Science"
        )
        other_chair = self._user("other-chair", other_campus, other_department)
        UserRole.objects.create(
            user=other_chair, role=self.area_role, tenant=self.tenant,
            campus=other_campus, department=other_department,
        )

        result = SubmissionReadinessEmailService.run(now=self.now)

        self.assertEqual(result["sent"], 1)
        self.assertEqual([message.to for message in mail.outbox], [[self.area_chair.email]])

    def test_failed_delivery_is_logged_and_retried_with_same_reserved_key(self):
        with patch("apps.notifications.submission_readiness.EmailMultiAlternatives.send", side_effect=RuntimeError("SMTP unavailable")):
            failed = SubmissionReadinessEmailService.run(now=self.now)
        self.assertEqual(failed["failed"], 1)
        log = SubmissionReadinessNotificationLog.objects.get()
        original_key = log.idempotency_key
        self.assertEqual(log.status, SubmissionReadinessNotificationLog.Status.FAILED)
        self.assertEqual(log.attempt_count, 1)

        retried = SubmissionReadinessEmailService.run(now=self.now + timedelta(minutes=1))

        self.assertEqual(retried["sent"], 1)
        log.refresh_from_db()
        self.assertEqual(log.idempotency_key, original_key)
        self.assertEqual(log.status, SubmissionReadinessNotificationLog.Status.SENT)
        self.assertEqual(log.attempt_count, 2)
