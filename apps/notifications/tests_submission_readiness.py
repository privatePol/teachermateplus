from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from unittest.mock import patch

from apps.academics.models import ActiveGradingPeriodSetting
from apps.admin_portal.tests_submission_readiness import GradeSubmissionReadinessTests
from apps.admin_portal.submission_readiness import GradeSubmissionReadinessService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.grading.models import GradingPeriodLock
from apps.notifications.models import SubmissionReadinessNotificationLog
from apps.notifications.submission_readiness import SubmissionReadinessEmailService
from apps.rbac.models import UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


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

    def _add_qualifying_assignment(
        self,
        *,
        username,
        campus,
        faculty_department,
        offering_department,
        code,
    ):
        program = Program.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=faculty_department,
            code=f"{code}-PROGRAM",
            name=f"{code} Program",
        )
        faculty = self._faculty(username, campus, faculty_department)
        offering = self._offering(
            f"{code}-SECTION",
            campus,
            offering_department,
            program,
        )
        assignment = self._assignment(offering, faculty)
        self._student(f"{code}-STUDENT", offering)
        self._activity(offering)
        return faculty, offering, assignment

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

    def test_area_chair_uses_faculty_role_department_when_offering_department_is_college(self):
        self.offering.department = self.college
        self.offering.save(update_fields=["department", "updated_at"])

        result = SubmissionReadinessEmailService.run(now=self.now)

        self.assertEqual(result["sent"], 1)
        self.assertEqual([message.to for message in mail.outbox], [[self.area_chair.email]])
        log = SubmissionReadinessNotificationLog.objects.get()
        self.assertEqual(log.scope_context_json["report_department_ids"], [self.department.id])
        self.assertNotIn(self.college.id, log.scope_context_json["report_department_ids"])

    def test_area_chair_eligibility_is_independent_of_offering_department(self):
        first = SubmissionReadinessEmailService.run(now=self.now)
        self.assertEqual(first["sent"], 1)
        SubmissionReadinessNotificationLog.objects.all().delete()
        mail.outbox.clear()
        self.offering.department = self.college
        self.offering.save(update_fields=["department", "updated_at"])

        second = SubmissionReadinessEmailService.run(now=self.now + timedelta(seconds=1))

        self.assertEqual(second["sent"], 1)
        self.assertEqual([message.to for message in mail.outbox], [[self.area_chair.email]])

    def test_missing_faculty_role_department_fails_closed_for_area_chair_and_dean(self):
        UserRole.objects.filter(user=self.faculty, role=self.faculty_role).update(department=None)
        dean_role = self._monitor_role("COLLEGE_DEAN", "College Dean")
        dean = self._user("missing-home-dean", self.campus, self.college)
        UserRole.objects.create(
            user=dean,
            role=dean_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.college,
        )
        self._set_policy(enabled=True, roles=["AREA_CHAIR", "COLLEGE_DEAN"])

        result = SubmissionReadinessEmailService.run(now=self.now)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_ambiguous_faculty_role_departments_fail_closed(self):
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            parent=self.college,
            code="SECOND-HOME",
            name="Second Home Department",
        )
        UserRole.objects.create(
            user=self.faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=other_department,
        )

        result = SubmissionReadinessEmailService.run(now=self.now)

        self.assertEqual(result["sent"], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_college_dean_uses_area_chair_supervision_of_faculty_role_departments(self):
        unrelated_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            parent=self.college,
            code="NO-AC",
            name="Department Without Area Chair",
        )
        unrelated_faculty, unrelated_offering, _ = self._add_qualifying_assignment(
            username="unrelated-faculty",
            campus=self.campus,
            faculty_department=unrelated_department,
            offering_department=self.college,
            code="NO-AC",
        )
        self.offering.department = self.college
        self.offering.save(update_fields=["department", "updated_at"])
        dean_role = self._monitor_role("COLLEGE_DEAN", "College Dean")
        dean = self._user("scoped-dean", self.campus, self.college)
        UserRole.objects.create(
            user=dean,
            role=dean_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.college,
        )
        self._set_policy(enabled=True, roles=["COLLEGE_DEAN"])

        result = SubmissionReadinessEmailService.run(now=self.now)

        self.assertEqual(result["sent"], 1)
        self.assertEqual([message.to for message in mail.outbox], [[dean.email]])
        self.assertIn(self.faculty.full_name, mail.outbox[0].body)
        self.assertNotIn(unrelated_faculty.full_name, mail.outbox[0].body)
        log = SubmissionReadinessNotificationLog.objects.get()
        self.assertEqual(log.assignment_count, 1)
        self.assertEqual(log.scope_context_json["report_department_ids"], [self.department.id])
        self.assertNotEqual(unrelated_offering.department_id, unrelated_department.id)

    def test_cao_with_fairview_scope_receives_other_campus_rows(self):
        self.campus.code = "FAIRVIEW"
        self.campus.name = "Fairview"
        self.campus.save(update_fields=["code", "name", "updated_at"])
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        other_campus = Campus.objects.create(tenant=self.tenant, code="CUBAO", name="Cubao")
        other_college = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="CUBAO-COLLEGE",
            name="Cubao College",
        )
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            parent=other_college,
            code="CUBAO-CS",
            name="Cubao Computer Science",
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            term=self.term,
            period=self.term_period,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=self.deadline,
            is_locked=False,
        )
        other_faculty, _, _ = self._add_qualifying_assignment(
            username="cubao-faculty",
            campus=other_campus,
            faculty_department=other_department,
            offering_department=other_college,
            code="CUBAO",
        )
        cao_role = self._monitor_role("CAO", "Chief Academic Officer")
        cao = self._user("fairview-cao", self.campus, self.department)
        UserRole.objects.create(
            user=cao,
            role=cao_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self._set_policy(enabled=True, roles=["CAO"])

        result = SubmissionReadinessEmailService.run(now=self.now)

        self.assertEqual(result["sent"], 1)
        self.assertEqual([message.to for message in mail.outbox], [[cao.email]])
        self.assertIn(other_faculty.full_name, mail.outbox[0].body)
        log = SubmissionReadinessNotificationLog.objects.get()
        self.assertEqual(log.scope_context_json["report_campus_ids"], [other_campus.id])
        self.assertEqual(log.scope_context_json["report_department_ids"], [other_department.id])

    def test_cao_tenant_scope_excludes_another_tenant(self):
        cao_role = self._monitor_role("CAO", "Chief Academic Officer")
        cao = self._user("tenant-cao", self.campus, self.department)
        cao_row = UserRole.objects.create(
            user=cao,
            role=cao_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        other_tenant_result = SimpleNamespace(
            assignment=SimpleNamespace(
                offering=SimpleNamespace(tenant_id=other_tenant.id, campus_id=self.campus.id)
            )
        )

        self.assertFalse(
            SubmissionReadinessEmailService._role_covers(
                cao_row,
                other_tenant_result,
                department_ids=None,
                faculty_department_id=self.department.id,
            )
        )
        self.assertNotIn(
            cao_row,
            SubmissionReadinessEmailService._recipient_rows(
                tenant_id=other_tenant.id,
                configured_roles=["CAO"],
            ),
        )

    def test_faculty_department_bulk_resolution_has_bounded_queries(self):
        def result_for(user):
            return SimpleNamespace(
                assignment=SimpleNamespace(
                    faculty_user_id=user.id,
                    offering=SimpleNamespace(tenant_id=self.tenant.id, campus_id=self.campus.id),
                )
            )

        larger_faculty_set = [self.faculty]
        for index in range(5):
            larger_faculty_set.append(self._faculty(f"bulk-faculty-{index}", self.campus, self.department))

        with CaptureQueriesContext(connection) as small_queries:
            small_result = SubmissionReadinessEmailService._faculty_department_ids_by_scope(
                tenant_id=self.tenant.id,
                results=[result_for(self.faculty)],
            )
        with CaptureQueriesContext(connection) as large_queries:
            large_result = SubmissionReadinessEmailService._faculty_department_ids_by_scope(
                tenant_id=self.tenant.id,
                results=[result_for(faculty) for faculty in larger_faculty_set],
            )

        self.assertEqual(len(small_queries), 1)
        self.assertEqual(len(large_queries), len(small_queries))
        self.assertEqual(len(large_result), len(larger_faculty_set))
        self.assertEqual(set(small_result.values()), {self.department.id})
        self.assertEqual(set(large_result.values()), {self.department.id})

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
