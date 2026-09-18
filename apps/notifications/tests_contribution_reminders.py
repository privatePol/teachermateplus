from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core import mail
from django.core.management import call_command
from django.test import override_settings, skipUnlessDBFeature
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.departmental_exams.models import FacultyContribution
from apps.departmental_exams.models import FacultyContributionEligibilitySource, ExaminationCycle
from apps.departmental_exams.exam_units import ExamCourseEquivalencyService
from apps.departmental_exams.contribution_services import ContributionRosterService
from apps.departmental_exams.services import CourseExamConfigurationService
from apps.departmental_exams.stage4_test_support import Stage4TestCase, Stage4TransactionTestCase
from apps.departmental_exams.tests_stage5_contributions import Stage5FixtureMixin
from apps.notifications.contribution_reminders import ContributionDeadlineReminderService as Service
from apps.notifications.models import ContributionDeadlineReminderDelivery as Delivery


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="noreply@example.edu",
    SITE_URL="https://grades.example.edu",
)
class ContributionDeadlineReminderTests(Stage5FixtureMixin, Stage4TestCase):
    def setUp(self):
        super().setUp()
        local_today = timezone.now().astimezone(ZoneInfo("Asia/Manila")).date()
        self.now = datetime.combine(local_today, datetime.min.time(), ZoneInfo("Asia/Manila")).replace(hour=9)
        self.deadline = self.now + timedelta(days=1, hours=8)
        self.cycle = self.make_cycle(status="OPEN", default_questions_required_per_faculty=50,
                                     default_final_item_count=50, default_contribution_deadline=self.deadline)
        self.parent = self.make_course(cycle=self.cycle, code="REMINDER")
        self.configuration = self.make_configuration(self.parent, workflow="OPEN",
                                                     opened_at=timezone.now(), deadline=self.deadline)
        self.faculty = self.make_faculty("reminder-faculty")
        self.assignment = self.make_assignment(self.parent, self.faculty)
        self.initialize(self.parent)
        self.contribution = FacultyContribution.objects.get(faculty_user=self.faculty)

    def enable(self):
        SystemSettingService.set(FeatureSettingsService.CONTRIBUTION_DEADLINE_REMINDER_ENABLED_KEY,
                                 True, tenant_id=self.tenant.id, value_type="BOOL", is_active=True)

    def run_reminders(self, *, now=None, dry_run=False):
        return Service.run(now=now or self.now, tenant_id=self.tenant.id, dry_run=dry_run)

    def test_feature_defaults_off(self):
        self.assertEqual(self.run_reminders()["eligible"], 0)
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_global_setting_cannot_enable_an_unconfigured_tenant(self):
        SystemSettingService.set(
            FeatureSettingsService.CONTRIBUTION_DEADLINE_REMINDER_ENABLED_KEY,
            True, tenant_id=None, value_type="BOOL", is_active=True,
        )
        self.assertFalse(FeatureSettingsService.is_contribution_deadline_reminder_enabled(tenant_id=self.tenant.id))
        self.assertEqual(self.run_reminders()["eligible"], 0)
        self.assertEqual(Delivery.objects.count(), 0)

    def test_mistyped_tenant_setting_fails_closed(self):
        SystemSettingService.set(
            FeatureSettingsService.CONTRIBUTION_DEADLINE_REMINDER_ENABLED_KEY,
            "true", tenant_id=self.tenant.id, value_type="STRING", is_active=True,
        )
        self.assertFalse(FeatureSettingsService.is_contribution_deadline_reminder_enabled(tenant_id=self.tenant.id))
        self.assertEqual(self.run_reminders()["eligible"], 0)

    def test_manila_window_catch_up_and_no_early_or_deadline_day(self):
        self.enable()
        self.assertEqual(self.run_reminders(now=self.now - timedelta(minutes=1))["eligible"], 0)
        self.assertEqual(self.run_reminders(now=self.now + timedelta(hours=3))["sent"], 1)
        self.assertEqual(self.run_reminders(now=self.now + timedelta(days=1))["eligible"], 0)
        self.assertEqual(self.run_reminders(now=self.deadline + timedelta(seconds=1))["eligible"], 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_one_private_email_and_repeat_is_terminal(self):
        self.enable()
        second = self.make_course(cycle=self.cycle, code="REMINDER-SECOND")
        self.make_configuration(second, workflow="OPEN", opened_at=timezone.now(), deadline=self.deadline)
        self.make_assignment(second, self.faculty)
        self.initialize(second)
        self.assertEqual(self.run_reminders()["sent"], 1)
        self.assertEqual(self.run_reminders()["duplicates"], 1)
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        text = mail.outbox[0].body
        self.assertIn("due tomorrow", text)
        self.assertIn("https://grades.example.edu/faculty/departmental-exams/contributions/", text)
        self.assertNotIn(self.parent.course.code, text)
        self.assertNotIn(second.course.code, text)
        self.assertNotIn(self.campus.name, text)
        self.assertNotIn("question", text.lower().replace("question bank", ""))
        self.assertNotIn(self.parent.course.code, mail.outbox[0].alternatives[0][0])

    def test_submitted_and_historical_submitted_do_not_replace_current_draft(self):
        self.enable()
        self.contribution.status = FacultyContribution.Status.SUBMITTED
        self.contribution.submitted_at = timezone.now()
        self.contribution.save(update_fields=["status", "submitted_at", "updated_at"])
        self.assertEqual(self.run_reminders()["eligible"], 0)
        self.contribution.active_marker = None
        self.contribution.save(update_fields=["active_marker", "updated_at"])
        successor = FacultyContribution.objects.create(
            cycle_course=self.parent, faculty_user=self.faculty,
            source_assignment=self.assignment, source_campus=self.campus,
            quota_snapshot=50, configuration_revision_snapshot=self.configuration.revision,
            status=FacultyContribution.Status.DRAFT, supersedes=self.contribution,
        )
        for source in self.contribution.eligibility_sources.all():
            FacultyContributionEligibilitySource.objects.create(
                contribution=successor, assignment=source.assignment,
                assignment_id_snapshot=source.assignment_id_snapshot,
                offering_id_snapshot=source.offering_id_snapshot,
                tenant_id_snapshot=source.tenant_id_snapshot,
                campus_id_snapshot=source.campus_id_snapshot,
                eligibility_proven_at=source.eligibility_proven_at,
                is_current=source.is_current,
            )
        self.assertEqual(self.run_reminders()["sent"], 1)

    def test_reopened_blocked_draft_uses_specific_historical_authority(self):
        self.enable()
        self.cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        self.cycle.status = ExaminationCycle.Status.CLOSED
        self.cycle.save(update_fields=["processing_mode", "status", "updated_at"])
        self.configuration.reopened_contribution_deadline = self.deadline
        self.configuration.closed_cycle_correction_active = True
        self.configuration.save(update_fields=["reopened_contribution_deadline", "closed_cycle_correction_active", "updated_at"])
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        self.contribution.roster_status = FacultyContribution.RosterStatus.BLOCKED
        self.contribution.roster_blocked_at = timezone.now()
        self.contribution.save(update_fields=["roster_status", "roster_blocked_at", "updated_at"])
        self.assertEqual(self.run_reminders()["sent"], 1)

    def test_grouped_members_consolidate_without_duplicate_email(self):
        self.enable()
        self.cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        self.cycle.save(update_fields=["processing_mode", "updated_at"])
        second = self.make_course(cycle=self.cycle, code="GROUPED")
        self.make_configuration(second, workflow="OPEN", opened_at=timezone.now(), deadline=self.deadline)
        self.make_assignment(second, self.faculty)
        actor = self.make_user("reminder-manager", self.department,
                               ("departmental_exams.manage_exam_generation",))
        ContributionRosterService.initialize(
            cycle_course_id=second.id, tenant_id=self.tenant.id, actor=actor,
        )
        ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Reminder group",
            primary_cycle_course_id=self.parent.id,
            member_ids=[self.parent.id, second.id], actor=actor,
        )
        self.assertEqual(self.run_reminders()["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_mixed_deadline_dates_get_separate_consolidated_emails(self):
        self.enable()
        second = self.make_course(cycle=self.cycle, code="LATER-DATE")
        later_deadline = self.deadline + timedelta(days=2)
        self.make_configuration(second, workflow="OPEN", opened_at=timezone.now(), deadline=later_deadline)
        self.make_assignment(second, self.faculty)
        self.initialize(second)
        self.assertEqual(self.run_reminders()["sent"], 1)
        self.assertEqual(self.run_reminders(now=self.now + timedelta(days=2))["sent"], 1)
        self.assertEqual(Delivery.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_open_creates_draft_for_eligible_faculty(self):
        later = self.make_course(cycle=self.cycle, code="NOT-LAZY")
        configuration = self.make_configuration(later, workflow="DRAFT", deadline=self.deadline)
        self.make_assignment(later, self.faculty)
        self.assertFalse(FacultyContribution.objects.filter(cycle_course=later).exists())
        CourseExamConfigurationService.open_for_contribution(
            cycle_course_id=later.id, tenant_id=self.tenant.id,
            user=self.configurer, expected_revision=configuration.revision,
        )
        self.assertTrue(FacultyContribution.objects.filter(
            cycle_course=later, faculty_user=self.faculty,
            status=FacultyContribution.Status.DRAFT, active_marker=1,
        ).exists())

    def test_blocked_draft_and_direct_deny_are_excluded(self):
        from apps.rbac.models import Permission, UserPermission

        self.enable()
        UserPermission.objects.create(
            user=self.faculty, tenant=self.tenant, campus=self.campus,
            permission=Permission.objects.get(code="faculty_portal.access"),
            grant_type=UserPermission.GrantType.DENY,
        )
        self.assertEqual(self.run_reminders()["eligible"], 0)
        self.assertEqual(Delivery.objects.count(), 0)

    def test_closure_exemption_and_permission_loss_before_dispatch_skip(self):
        self.enable()
        original = Service._eligible_groups

        def close_before_recheck(*, tenant, now, faculty_user_id=None):
            if Delivery.objects.exists():
                self.configuration.workflow_status = self.configuration.WorkflowStatus.CLOSED
                self.configuration.save(update_fields=["workflow_status", "updated_at"])
            return original(tenant=tenant, now=now, faculty_user_id=faculty_user_id)

        with patch.object(Service, "_eligible_groups", side_effect=close_before_recheck):
            self.assertEqual(self.run_reminders()["skipped"], 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_final_submission_before_dispatch_suppresses_email(self):
        self.enable()
        original = Service._eligible_groups

        def submit_before_recheck(*, tenant, now, faculty_user_id=None):
            if Delivery.objects.exists():
                self.contribution.status = FacultyContribution.Status.SUBMITTED
                self.contribution.submitted_at = timezone.now()
                self.contribution.save(update_fields=["status", "submitted_at", "updated_at"])
            return original(tenant=tenant, now=now, faculty_user_id=faculty_user_id)

        with patch.object(Service, "_eligible_groups", side_effect=submit_before_recheck):
            self.assertEqual(self.run_reminders()["skipped"], 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_feature_disabled_before_dispatch_suppresses_email(self):
        self.enable()
        original = Service._eligible_groups

        def disable_before_recheck(*, tenant, now, faculty_user_id=None):
            if Delivery.objects.exists():
                SystemSettingService.set(
                    FeatureSettingsService.CONTRIBUTION_DEADLINE_REMINDER_ENABLED_KEY,
                    False, tenant_id=self.tenant.id, value_type="BOOL", is_active=True,
                )
            return original(tenant=tenant, now=now, faculty_user_id=faculty_user_id)

        with patch.object(Service, "_eligible_groups", side_effect=disable_before_recheck):
            self.assertEqual(self.run_reminders()["skipped"], 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_deadline_extension_before_dispatch_suppresses_old_date(self):
        self.enable()
        original = Service._eligible_groups

        def extend_before_recheck(*, tenant, now, faculty_user_id=None):
            if Delivery.objects.exists():
                self.configuration.reopened_contribution_deadline = self.deadline + timedelta(days=2)
                self.configuration.save(update_fields=["reopened_contribution_deadline", "updated_at"])
            return original(tenant=tenant, now=now, faculty_user_id=faculty_user_id)

        with patch.object(Service, "_eligible_groups", side_effect=extend_before_recheck):
            self.assertEqual(self.run_reminders()["skipped"], 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_permission_loss_before_dispatch_suppresses_email(self):
        from apps.rbac.models import Permission, UserPermission

        self.enable()
        original = Service._eligible_groups

        def deny_before_recheck(*, tenant, now, faculty_user_id=None):
            if Delivery.objects.exists() and not UserPermission.objects.filter(user=self.faculty).exists():
                UserPermission.objects.create(
                    user=self.faculty, tenant=self.tenant, campus=self.campus,
                    permission=Permission.objects.get(code="faculty_portal.access"),
                    grant_type=UserPermission.GrantType.DENY,
                )
            return original(tenant=tenant, now=now, faculty_user_id=faculty_user_id)

        with patch.object(Service, "_eligible_groups", side_effect=deny_before_recheck):
            self.assertEqual(self.run_reminders()["skipped"], 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_new_deadline_date_can_send_again_but_same_date_cannot(self):
        self.enable()
        self.assertEqual(self.run_reminders()["sent"], 1)
        self.configuration.reopened_contribution_deadline = self.deadline + timedelta(hours=1)
        self.configuration.save(update_fields=["reopened_contribution_deadline", "updated_at"])
        self.assertEqual(self.run_reminders()["duplicates"], 1)
        self.configuration.reopened_contribution_deadline = self.deadline + timedelta(days=2)
        self.configuration.save(update_fields=["reopened_contribution_deadline", "updated_at"])
        self.assertEqual(self.run_reminders(now=self.now + timedelta(days=2))["sent"], 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_tenant_filter_and_exemption_exclude(self):
        self.enable()
        self.assertEqual(Service.run(now=self.now, tenant_id=self.other_tenant.id)["eligible"], 0)
        self.parent.inclusion_status = self.parent.InclusionStatus.EXEMPT
        self.parent.exemption_category = self.parent.ExemptionCategory.PRACTICUM_OJT
        self.parent.exemption_reason = "Approved practicum alternative."
        self.parent.exemption_changed_by = self.admin
        self.parent.exemption_changed_at = timezone.now()
        self.parent.save()
        self.assertEqual(self.run_reminders()["eligible"], 0)

    def test_missing_and_invalid_email_are_recorded_and_correctable_same_day(self):
        self.enable()
        self.faculty.email = ""
        self.faculty.save(update_fields=["email", "updated_at"])
        self.assertEqual(self.run_reminders()["skipped"], 1)
        row = Delivery.objects.get()
        self.assertEqual(row.failure_code, "MISSING_EMAIL")
        self.faculty.email = "bad-address"
        self.faculty.save(update_fields=["email", "updated_at"])
        self.assertEqual(self.run_reminders()["skipped"], 1)
        self.assertEqual(Delivery.objects.get().failure_code, "INVALID_EMAIL")
        self.faculty.email = "corrected@example.edu"
        self.faculty.save(update_fields=["email", "updated_at"])
        self.assertEqual(self.run_reminders()["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["corrected@example.edu"])

    def test_email_address_is_refreshed_before_dispatch(self):
        self.enable()
        original = Service._eligible_groups

        def change_address_before_recheck(*, tenant, now, faculty_user_id=None):
            if Delivery.objects.exists():
                self.faculty.email = "fresh@example.edu"
                self.faculty.save(update_fields=["email", "updated_at"])
            return original(tenant=tenant, now=now, faculty_user_id=faculty_user_id)

        with patch.object(Service, "_eligible_groups", side_effect=change_address_before_recheck):
            self.assertEqual(self.run_reminders()["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["fresh@example.edu"])

    def test_pre_send_failure_bounded_retry_and_uncertain_smtp_no_retry(self):
        self.enable()
        with override_settings(SITE_URL="https://localhost"):
            self.assertEqual(self.run_reminders()["failed_pre_send"], 1)
        with override_settings(SITE_URL=""):
            for _ in range(Service.MAX_PRE_SEND_ATTEMPTS - 1):
                self.assertEqual(self.run_reminders()["failed_pre_send"], 1)
            self.assertEqual(self.run_reminders()["duplicates"], 1)
        self.assertEqual(Delivery.objects.get().attempt_count, Service.MAX_PRE_SEND_ATTEMPTS)
        self.assertEqual(len(mail.outbox), 0)

    def test_known_pre_send_failure_retries_after_configuration_correction(self):
        self.enable()
        with override_settings(SITE_URL=""):
            self.assertEqual(self.run_reminders()["failed_pre_send"], 1)
        self.assertEqual(self.run_reminders()["sent"], 1)
        row = Delivery.objects.get()
        self.assertEqual(row.attempt_count, 2)
        self.assertEqual(row.status, Delivery.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)

    def test_smtp_exception_is_uncertain_and_not_resent(self):
        self.enable()
        with patch("apps.notifications.contribution_reminders.EmailMultiAlternatives.send", side_effect=TimeoutError):
            self.assertEqual(self.run_reminders()["uncertain"], 1)
        self.assertEqual(Delivery.objects.get().status, Delivery.Status.UNCERTAIN)
        self.assertEqual(self.run_reminders()["duplicates"], 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_stale_preparing_reclaims_and_stale_dispatching_becomes_uncertain(self):
        self.enable()
        row = Delivery.objects.create(
            tenant=self.tenant, faculty_user=self.faculty,
            deadline_local_date=self.deadline.astimezone(Service.MANILA).date(),
            status=Delivery.Status.PREPARING, claimed_at=self.now - timedelta(minutes=16),
            attempt_count=1,
        )
        self.assertEqual(self.run_reminders()["sent"], 1)
        self.assertEqual(Delivery.objects.get(pk=row.pk).attempt_count, 2)
        row.status = Delivery.Status.DISPATCHING
        row.claimed_at = self.now - timedelta(minutes=16)
        row.sent_at = None
        row.save(update_fields=["status", "claimed_at", "sent_at", "updated_at"])
        self.assertEqual(self.run_reminders()["duplicates"], 1)
        self.assertEqual(Delivery.objects.get(pk=row.pk).status, Delivery.Status.UNCERTAIN)
        self.assertEqual(len(mail.outbox), 1)

    def test_dry_run_command_never_writes_or_sends(self):
        self.enable()
        with patch("apps.notifications.contribution_reminders.timezone.now", return_value=self.now):
            call_command("send_contribution_deadline_reminders", "--dry-run", tenant_id=self.tenant.id)
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)


class ContributionReminderClaimConcurrencyTests(Stage4TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_two_database_connections_claim_one_key_once(self):
        barrier = Barrier(2)
        now = timezone.now()
        target_date = now.astimezone(Service.MANILA).date() + timedelta(days=1)

        def claim():
            from django.db import close_old_connections

            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return Service._claim(tenant=self.tenant, user=self.admin,
                                      deadline_date=target_date, now=now)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(Delivery.objects.count(), 1)
