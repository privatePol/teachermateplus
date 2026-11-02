from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import FacultyAssignment
from apps.admin_portal.correction_email_views import correction_email_history_view, correction_email_settings_view
from apps.core.services.features import FeatureSettingsService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.grading.correction_reminders import (
    MANILA, CorrectionReminderPolicy as Policy, CorrectionReminderService as Service,
    CorrectionReminderSettingsForm,
)
from apps.grading.models import (
    CorrectionEmailDelivery as Delivery, CorrectionStepNotificationIntent as Intent,
    CorrectionApprovalRouteStep, CorrectionPetitionWindowPolicy, GradeCorrectionRequest, GradeCorrectionRequestItem,
)
from apps.grading.services import GradingGovernanceService as Governance
from apps.grading import tests as existing_tests
from apps.rbac.models import Permission, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Tenant


class ReminderFixtures:
    def setUp(self):
        existing_tests.CorrectionWorkflowTests.setUp(self)
        for code in ("corrections.review", "admin_portal.access", "system_settings.update"):
            permission, _ = Permission.objects.get_or_create(code=code, defaults={"module": "test", "action": "review"})
            RolePermission.objects.get_or_create(role=self.reviewer_role, permission=permission)
        permission = Permission.objects.get(code="corrections.review")
        RolePermission.objects.get_or_create(role=self.cao_role, permission=permission)
        RolePermission.objects.get_or_create(role=self.cao_role, permission=Permission.objects.get(code="admin_portal.access"))
        SystemSettingService.set(FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            True, tenant_id=self.tenant.pk, value_type="BOOL")
        # The authoritative review-page selector requires an accepted faculty assignment.
        FacultyAssignment.objects.filter(offering=self.offering).update(response_status="ACCEPTED",
            accepted_at=timezone.now(), accepted_by=self.faculty_user, responded_at=timezone.now())
        for user in (self.reviewer_user, self.faculty_user):
            user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
            user.privacy_consent_at = timezone.now()
            user.save(update_fields=["privacy_consent_version", "privacy_consent_at"])
        for code in ("faculty_portal.access", "corrections.create"):
            permission, _ = Permission.objects.get_or_create(code=code, defaults={"module": "test", "action": "access"})
            RolePermission.objects.get_or_create(role=self.faculty_role, permission=permission)

    def create_request(self):
        return Governance.create_correction_request(user=self.faculty_user, offering=self.offering,
            template_period=self.period, justification="PRIVATE JUSTIFICATION NEVER LOGGED", items=[{
                "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                "student_id": self.student1.pk, "grade_activity_id": self.activity.pk, "new_value": "35"}])

    def pending(self, *, days=2, initial=True):
        with self.captureOnCommitCallbacks(execute=initial):
            request_obj = self.create_request()
        stamp = timezone.now() - timedelta(days=days)
        GradeCorrectionRequest.objects.filter(pk=request_obj.pk).update(created_at=stamp)
        Intent.objects.filter(step__correction_request=request_obj).update(activated_at=stamp)
        Delivery.objects.filter(correction_request=request_obj).update(
            scheduled_at=stamp, scheduled_local_date=stamp.astimezone(MANILA).date())
        Delivery.objects.filter(correction_request=request_obj, status=Delivery.Status.SENT).update(sent_at=stamp)
        return request_obj

    def tick(self, now):
        Service.discover(now=now)
        return Service.dispatch(now=now)

    def activate_at(self, stamp):
        Intent.objects.update(activated_at=stamp)
        Delivery.objects.filter(kind=Delivery.Kind.INITIAL).update(scheduled_at=stamp,
            scheduled_local_date=stamp.date(), sent_at=stamp)

    def manila(self, day=2, hour=8, minute=0):
        return datetime(2026, 11, day, hour, minute, tzinfo=MANILA)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CorrectionReminderTests(ReminderFixtures, TestCase):
    def test_initial_intent_before_commit_and_dispatch_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            request_obj = self.create_request()
            self.assertEqual(Intent.objects.count(), 1)
            self.assertEqual(Delivery.objects.get().status, Delivery.Status.PENDING)
            self.assertEqual(len(mail.outbox), 0)
        row = Delivery.objects.get()
        self.assertEqual(row.status, Delivery.Status.SENT)
        self.assertEqual(row.attempt_count, 1)
        self.assertEqual(row.recipient, self.reviewer_user)
        self.assertEqual(row.correction_request, request_obj)
        self.assertEqual(len(mail.outbox), 1)

    def test_rollback_removes_intent_and_never_sends(self):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    self.create_request()
                    raise RuntimeError("rollback")
        self.assertFalse(Intent.objects.exists())
        self.assertFalse(Delivery.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_intermediate_handoff_and_final_closure(self):
        for order, role in [(1, self.reviewer_role), (2, self.cao_role)]:
            CorrectionApprovalRouteStep.objects.create(route_rule=self.route_rule, step_order=order,
                approver_role=role, approver_label=role.name)
        with self.captureOnCommitCallbacks(execute=True):
            request_obj = self.create_request()
        self.assertEqual([message.to for message in mail.outbox], [[self.reviewer_user.email]])
        with self.captureOnCommitCallbacks(execute=True):
            request_obj = Governance.review_correction_request(request_obj=request_obj,
                reviewer=self.reviewer_user, approved=True)
        self.assertEqual([message.to for message in mail.outbox], [[self.reviewer_user.email], [self.cao_user.email]])
        self.assertEqual(Intent.objects.count(), 2)
        with self.captureOnCommitCallbacks(execute=True):
            request_obj = Governance.review_correction_request(request_obj=request_obj, reviewer=self.cao_user, approved=True)
        self.assertEqual(request_obj.status, GradeCorrectionRequest.Status.CLOSED)
        self.tick(timezone.now() + timedelta(days=2))
        self.assertEqual(len(mail.outbox), 2)

    def test_rejection_skips_queued_initial_and_reminders(self):
        request_obj = self.pending(initial=False)
        Governance.review_correction_request(request_obj=request_obj, reviewer=self.reviewer_user, approved=False)
        result = Service.dispatch()
        self.assertEqual(result["sent"], 0)
        self.assertEqual(Delivery.objects.get().outcome_code, "step_not_current")

    def test_existing_pending_backfill_and_repeat_polling(self):
        request_obj = self.pending(initial=False)
        Delivery.objects.all().delete()
        Intent.objects.all().delete()
        self.tick(timezone.now() + timedelta(days=1))
        self.assertEqual(Intent.objects.count(), 1)
        self.assertEqual(Delivery.objects.filter(kind=Delivery.Kind.INITIAL).count(), 1)
        count = len(mail.outbox)
        self.tick(timezone.now() + timedelta(days=1))
        self.assertEqual(len(mail.outbox), count)

    def test_calendar_day_weekend_timezone_frequency_and_no_catchup(self):
        request_obj = self.pending()
        # Nov 1 2026 is Sunday; activation Oct 31 (Saturday).
        self.activate_at(datetime(2026, 10, 31, 22, tzinfo=MANILA))
        now = self.manila(day=1, hour=8)
        self.assertEqual(now.weekday(), 6)
        before = len(mail.outbox)
        self.tick(now.astimezone(dt_timezone.utc))
        self.assertEqual(len(mail.outbox), before + 1)
        self.assertEqual(Delivery.objects.get(kind=Delivery.Kind.REMINDER).scheduled_local_date, now.date())
        self.tick(self.manila(day=10, hour=9))
        self.assertEqual(Delivery.objects.filter(kind=Delivery.Kind.REMINDER).count(), 2)

    def test_settings_default_and_dynamic_time_interval_disabled(self):
        self.assertEqual(Policy.get(self.tenant.pk), {"enabled": True, "interval_days": 1,
                         "send_time": datetime.strptime("08:00", "%H:%M").time()})
        self.pending()
        self.activate_at(self.manila(day=1, hour=22))
        form = CorrectionReminderSettingsForm({"enabled": "on", "interval_days": "2", "send_time": "09:30"})
        self.assertTrue(form.is_valid())
        Policy.save(self.tenant.pk, form.cleaned_data)
        before = len(mail.outbox)
        self.tick(self.manila(day=2, hour=12))
        self.tick(self.manila(day=3, hour=9, minute=29))
        self.assertEqual(len(mail.outbox), before)
        self.tick(self.manila(day=3, hour=9, minute=30))
        self.assertEqual(len(mail.outbox), before + 1)
        Policy.save(self.tenant.pk, dict(form.cleaned_data, enabled=False))
        self.tick(self.manila(day=5, hour=10))
        self.assertEqual(len(mail.outbox), before + 1)

    def test_initial_toggle_independent_of_reminders(self):
        SystemSettingService.set(FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            False, tenant_id=self.tenant.pk, value_type="BOOL")
        self.pending()
        self.assertFalse(Delivery.objects.exists())
        self.activate_at(self.manila(day=1))
        self.tick(self.manila(day=2, hour=10))
        self.assertEqual(Delivery.objects.get().kind, Delivery.Kind.REMINDER)
        self.assertEqual(len(mail.outbox), 1)

    def test_future_pending_steps_and_malformed_sequence_are_excluded(self):
        for order, role in [(1, self.reviewer_role), (2, self.cao_role)]:
            CorrectionApprovalRouteStep.objects.create(route_rule=self.route_rule, step_order=order,
                approver_role=role, approver_label=role.name)
        request_obj = self.pending(initial=False)
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(Delivery.objects.get().recipient_id, self.reviewer_user.pk)
        request_obj.approval_steps.filter(step_order=2).update(status="SKIPPED")
        self.assertEqual(Service.dispatch()["sent"], 0)

    def test_missing_inactive_account_assignment_and_role_recover(self):
        UserRole.objects.filter(user=self.reviewer_user).update(is_active=False)
        request_obj = self.pending()
        self.assertEqual(Intent.objects.count(), 1)
        self.assertFalse(Delivery.objects.exists())
        UserRole.objects.filter(user=self.reviewer_user).update(is_active=True)
        User.objects.filter(pk=self.reviewer_user.pk).update(is_active=False)
        Service.discover()
        self.assertFalse(Delivery.objects.exists())
        User.objects.filter(pk=self.reviewer_user.pk).update(is_active=True)
        Service.discover()
        Service.dispatch()
        self.assertEqual(Delivery.objects.get(kind=Delivery.Kind.INITIAL).status, Delivery.Status.SENT)

    def test_revalidate_direct_deny_including_null_scope_and_superuser(self):
        request_obj = self.pending(initial=False)
        self.reviewer_user.is_superuser = True
        self.reviewer_user.save(update_fields=["is_superuser"])
        UserPermission.objects.create(user=self.reviewer_user, permission=Permission.objects.get(code="corrections.review"),
            grant_type="DENY", tenant=None, campus=None)
        self.assertEqual(Service.dispatch()["sent"], 0)
        self.assertEqual(Delivery.objects.get().outcome_code, "recipient_ineligible")

    def test_permission_tenant_campus_department_and_override_boundaries(self):
        request_obj = self.pending(initial=False)
        step = Service.current_step(request_obj)
        self.assertFalse(Service.eligible(request_obj, step, self.super_admin_user))
        RolePermission.objects.filter(role=self.reviewer_role, permission__code="corrections.review").delete()
        self.assertFalse(Service.eligible(request_obj, step, self.reviewer_user))
        RolePermission.objects.create(role=self.reviewer_role, permission=Permission.objects.get(code="corrections.review"))
        assignment = UserRole.objects.get(user=self.reviewer_user)
        other_tenant = Tenant.objects.create(code="OTHER", name="Other")
        assignment.tenant = other_tenant
        assignment.save()
        self.assertFalse(Service.eligible(request_obj, step, self.reviewer_user))
        assignment.tenant = self.tenant
        assignment.campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other")
        assignment.save()
        self.assertFalse(Service.eligible(request_obj, step, self.reviewer_user))
        assignment.campus = self.campus
        assignment.department = Department.objects.create(tenant=self.tenant, campus=self.campus, code="OTHER", name="Other")
        assignment.save()
        self.assertFalse(Service.eligible(request_obj, step, self.reviewer_user))

    def test_on_behalf_initiator_restriction(self):
        request_obj = self.pending(initial=False)
        request_obj.request_source = "ADMIN_ON_BEHALF"
        request_obj.initiated_by_user = self.reviewer_user
        request_obj.save()
        self.assertEqual(Service.dispatch()["sent"], 0)

    def test_eligible_recipient_email_is_refreshed_and_missing_email_recovers(self):
        request_obj = self.pending(initial=False)
        User.objects.filter(pk=self.reviewer_user.pk).update(email="")
        self.assertEqual(Service.dispatch()["sent"], 0)
        self.assertEqual(Delivery.objects.get().outcome_code, "recipient_ineligible")
        User.objects.filter(pk=self.reviewer_user.pk).update(email="new@example.com")
        Service.discover()
        self.assertEqual(Service.dispatch()["sent"], 1)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])

    def test_inactive_approver_role_and_request_selector_are_rechecked(self):
        request_obj = self.pending(initial=False)
        self.reviewer_role.is_active = False
        self.reviewer_role.save()
        self.assertEqual(Service.dispatch()["sent"], 0)
        self.reviewer_role.is_active = True
        self.reviewer_role.save()
        # A valid role/permission cannot bypass the authoritative review selector.
        from apps.admin_portal.services import AdminScopeService
        with patch.object(AdminScopeService, "scoped_grade_correction_requests", return_value=GradeCorrectionRequest.objects.none()):
            self.assertFalse(Service.eligible(request_obj, Service.current_step(request_obj), self.reviewer_user))

    def test_other_campus_deny_does_not_block_current_campus(self):
        request_obj = self.pending(initial=False)
        other = Campus.objects.create(tenant=self.tenant, code="DENY_OTHER", name="Other")
        UserPermission.objects.create(user=self.reviewer_user, permission=Permission.objects.get(code="corrections.review"),
            tenant=self.tenant, campus=other, grant_type="DENY")
        self.assertEqual(Service.dispatch()["sent"], 1)

    def test_viewing_pending_request_does_not_acknowledge_or_stop_reminders(self):
        request_obj = self.pending()
        self.client.force_login(self.reviewer_user)
        self.assertEqual(self.client.get(reverse("admin_portal:grade_correction_request_review", args=[request_obj.pk])).status_code, 200)
        now = timezone.now() + timedelta(days=1)
        self.tick(now)
        self.assertEqual(Delivery.objects.filter(kind=Delivery.Kind.REMINDER, status=Delivery.Status.SENT).count(), 1)

    def test_rolled_back_handoff_never_sends_to_next_step(self):
        for order, role in [(1, self.reviewer_role), (2, self.cao_role)]:
            CorrectionApprovalRouteStep.objects.create(route_rule=self.route_rule, step_order=order,
                approver_role=role, approver_label=role.name)
        request_obj = self.pending()
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    Governance.review_correction_request(request_obj=request_obj, reviewer=self.reviewer_user, approved=True)
                    raise RuntimeError("rollback transition")
        self.assertEqual(Intent.objects.count(), 1)
        self.assertFalse(Delivery.objects.filter(recipient=self.cao_user).exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_history_hides_forged_cross_tenant_request(self):
        request_obj = self.pending()
        other = Tenant.objects.create(code="OTHER_HISTORY", name="Other")
        GradeCorrectionRequest.objects.filter(pk=request_obj.pk).update(tenant=other)
        self.client.force_login(self.reviewer_user)
        self.assertEqual(self.client.get(reverse("admin_portal:correction_email_history", args=[request_obj.pk])).status_code, 404)

    def test_legacy_initial_success_is_imported_per_recipient(self):
        request_obj = self.pending(initial=False)
        step = Service.current_step(request_obj)
        Delivery.objects.all().delete()
        Intent.objects.all().delete()
        from apps.auditlog.models import AuditLog
        AuditLog.objects.create(action="EMAIL_CORRECTION_STEP_APPROVER", portal="SYSTEM",
            entity_type="GradeCorrectionRequest", entity_id=str(request_obj.pk), tenant=self.tenant, campus=self.campus,
            metadata_json={"event_key": f"step:{step.pk}:pending", "recipients": [self.reviewer_user.email]})
        Service.discover()
        row = Delivery.objects.get(kind=Delivery.Kind.INITIAL)
        self.assertEqual(row.status, Delivery.Status.SENT)
        self.assertEqual(row.outcome_code, "legacy_backend_accepted")
        self.assertEqual(row.attempt_count, 0)

    def test_partial_failure_retries_individually_and_sanitizes_errors(self):
        other = User.objects.create_user(username="other-reviewer", email="other@example.com")
        UserRole.objects.create(user=other, role=self.reviewer_role, tenant=self.tenant, campus=self.campus)
        request_obj = self.pending(initial=False)
        from django.core.mail import EmailMultiAlternatives
        real_send = EmailMultiAlternatives.send
        def partial(message, *args, **kwargs):
            if message.to == [self.reviewer_user.email]:
                raise RuntimeError("SMTP PASSWORD secret; PRIVATE JUSTIFICATION NEVER LOGGED; grade=35")
            return real_send(message, *args, **kwargs)
        with patch.object(EmailMultiAlternatives, "send", partial), patch("logging.Logger._log") as log:
            result = Service.dispatch()
        self.assertNotIn("secret", str(log.call_args_list))
        self.assertNotIn("PRIVATE JUSTIFICATION", str(log.call_args_list))
        self.assertEqual(result, {"sent": 1, "failed": 1, "skipped": 0})
        failed = Delivery.objects.get(status=Delivery.Status.FAILED)
        self.assertEqual(failed.outcome_code, "email_or_preflight_failed")
        self.assertNotIn("secret", str(list(Delivery.objects.values())))
        self.assertEqual(Service.dispatch()["sent"], 0)
        result = Service.dispatch(now=timezone.now() + timedelta(minutes=6))
        self.assertEqual(result["sent"], 1)
        self.assertEqual(Delivery.objects.get(recipient=other).attempt_count, 1)
        self.assertEqual(Delivery.objects.get(recipient=self.reviewer_user).attempt_count, 2)

    def test_atomic_claim_retry_and_expired_lease(self):
        self.pending(initial=False)
        row = Delivery.objects.get()
        now = timezone.now()
        first = Service.claim(row.pk, now=now)
        self.assertIsNotNone(first)
        self.assertIsNone(Service.claim(row.pk, now=now))
        second = Service.claim(row.pk, now=now + timedelta(minutes=16))
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        self.assertEqual(Service.preflight(row.pk, first, now=now + timedelta(minutes=16))[1], "claim_lost")

    def test_expired_owned_claim_is_preserved_and_recovered_without_rediscovery(self):
        request_obj = self.pending(initial=False)
        row = Delivery.objects.get()
        now = timezone.now()
        original_preflight = Service.preflight
        observed = {}

        def expired_preflight(pk, token, *, now):
            observed["row"] = Delivery.objects.values().get(pk=pk)
            self.assertEqual(observed["row"]["claim_token"], token)
            return original_preflight(pk, token, now=now + Service.LEASE)

        with patch.object(Service, "preflight", side_effect=expired_preflight), patch(
                "django.core.mail.EmailMultiAlternatives.send") as send:
            result = Service.dispatch(request_id=request_obj.pk, now=now)
        send.assert_not_called()
        self.assertEqual(result, {"sent": 0, "failed": 0, "skipped": 0})
        self.assertEqual(Delivery.objects.values().get(pk=row.pk), observed["row"])
        self.assertEqual(observed["row"]["status"], Delivery.Status.PROCESSING)

        result = Service.dispatch(request_id=request_obj.pk, now=now + Service.LEASE)
        row.refresh_from_db()
        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(row.status, Delivery.Status.SENT)
        self.assertEqual(row.attempt_count, 2)
        self.assertEqual(row.first_attempt_at, now)
        self.assertEqual(row.last_attempt_at, now + Service.LEASE)
        self.assertIsNone(row.claim_token)

    def test_stale_preflight_cannot_overwrite_replacement_claim(self):
        request_obj = self.pending(initial=False)
        row = Delivery.objects.get()
        original_preflight = Service.preflight
        observed = {}

        def replaced_preflight(pk, token, *, now):
            replacement = Service.claim(pk, now=now + Service.LEASE)
            self.assertIsNotNone(replacement)
            self.assertNotEqual(token, replacement)
            observed["row"] = Delivery.objects.values().get(pk=pk)
            return original_preflight(pk, token, now=now + Service.LEASE)

        with patch.object(Service, "preflight", side_effect=replaced_preflight), patch(
                "django.core.mail.EmailMultiAlternatives.send") as send:
            result = Service.dispatch(request_id=request_obj.pk, now=timezone.now())
        send.assert_not_called()
        self.assertEqual(result, {"sent": 0, "failed": 0, "skipped": 0})
        self.assertEqual(Delivery.objects.values().get(pk=row.pk), observed["row"])
        self.assertEqual(observed["row"]["status"], Delivery.Status.PROCESSING)
        self.assertEqual(observed["row"]["attempt_count"], 2)

    def test_stale_sender_cannot_finalize_replacement_claim(self):
        request_obj = self.pending(initial=False)
        row = Delivery.objects.get()
        now = timezone.now()
        observed = {}

        def accepted_after_reclaim(*args, **kwargs):
            old_token = Delivery.objects.get(pk=row.pk).claim_token
            replacement = Service.claim(row.pk, now=now + Service.LEASE)
            self.assertIsNotNone(replacement)
            self.assertNotEqual(old_token, replacement)
            observed["row"] = Delivery.objects.values().get(pk=row.pk)
            return 1

        with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=accepted_after_reclaim) as send:
            result = Service.dispatch(request_id=request_obj.pk, now=now)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result, {"sent": 0, "failed": 0, "skipped": 0})
        self.assertEqual(Delivery.objects.values().get(pk=row.pk), observed["row"])
        self.assertIsNone(observed["row"]["sent_at"])

    def test_successful_delivery_remains_terminal_after_lease_recovery_window(self):
        request_obj = self.pending()
        row = Delivery.objects.get()
        snapshot = Delivery.objects.values().get(pk=row.pk)
        later = timezone.now() + Service.LEASE * 2
        self.assertIsNone(Service.claim(row.pk, now=later))
        Service._enqueue(request_obj, Service.current_step(request_obj), self.reviewer_user,
            Delivery.Kind.INITIAL, row.scheduled_at)
        with patch("django.core.mail.EmailMultiAlternatives.send") as send:
            result = Service.dispatch(request_id=request_obj.pk, now=later)
        send.assert_not_called()
        self.assertEqual(result, {"sent": 0, "failed": 0, "skipped": 0})
        self.assertEqual(Delivery.objects.values().get(pk=row.pk), snapshot)
        self.assertEqual(len(mail.outbox), 1)

    def test_rediscovery_recovers_previously_skipped_claim_lost_initial(self):
        request_obj = self.pending(initial=False)
        row = Delivery.objects.get()
        attempt_at = timezone.now() - Service.LEASE
        Delivery.objects.filter(pk=row.pk).update(status=Delivery.Status.SKIPPED, outcome_code="claim_lost",
            attempt_count=1, first_attempt_at=attempt_at, last_attempt_at=attempt_at)
        Service.activate(request_obj, dispatch=False)
        row.refresh_from_db()
        self.assertEqual(row.status, Delivery.Status.PENDING)
        self.assertEqual(row.attempt_count, 1)
        self.assertEqual(row.first_attempt_at, attempt_at)
        self.assertEqual(Service.dispatch(request_id=request_obj.pk)["sent"], 1)
        row.refresh_from_db()
        self.assertEqual(row.status, Delivery.Status.SENT)
        self.assertEqual(row.attempt_count, 2)
        self.assertEqual(len(mail.outbox), 1)

    def test_rediscovery_recovers_previously_skipped_claim_lost_due_reminder(self):
        request_obj = self.pending()
        now = timezone.now().astimezone(MANILA).replace(hour=12, minute=0, second=0, microsecond=0)
        Service.discover(now=now)
        row = Delivery.objects.get(kind=Delivery.Kind.REMINDER)
        Delivery.objects.filter(pk=row.pk).update(status=Delivery.Status.SKIPPED, outcome_code="claim_lost",
            attempt_count=1, first_attempt_at=now - Service.LEASE, last_attempt_at=now - Service.LEASE)
        Service.discover(now=now)
        row.refresh_from_db()
        self.assertEqual(row.status, Delivery.Status.PENDING)
        self.assertEqual(row.attempt_count, 1)
        self.assertEqual(Service.dispatch(request_id=request_obj.pk, now=now)["sent"], 1)
        row.refresh_from_db()
        self.assertEqual(row.status, Delivery.Status.SENT)
        self.assertEqual(row.attempt_count, 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_preflight_disabled_changed_time_and_expired_dates(self):
        self.pending()
        self.activate_at(self.manila(day=1))
        now = self.manila(day=2, hour=8)
        Service.discover(now=now)
        row = Delivery.objects.get(kind=Delivery.Kind.REMINDER)
        Policy.save(self.tenant.pk, {"enabled": True, "interval_days": 1,
            "send_time": datetime.strptime("09:00", "%H:%M").time()})
        self.assertEqual(Service.dispatch(now=now)["sent"], 0)
        self.assertEqual(Delivery.objects.get(pk=row.pk).outcome_code, "schedule_changed_or_expired")
        self.tick(self.manila(day=2, hour=9))
        self.assertEqual(Delivery.objects.get(pk=row.pk).status, Delivery.Status.SENT)

    def test_history_and_settings_authorized_scoped_rendering_and_validation(self):
        self.pending()
        self.client.force_login(self.reviewer_user)
        url = reverse("admin_portal:correction_email_settings")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Asia/Manila")
        response = self.client.post(url, {"enabled": "on", "interval_days": "3", "send_time": "10:15"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Policy.get(self.tenant.pk)["interval_days"], 3)
        response = self.client.post(url, {"interval_days": "0", "send_time": "25:99"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        history = reverse("admin_portal:correction_email_history", args=[Delivery.objects.first().correction_request_id])
        self.assertContains(self.client.get(history), "Accepted by email backend")
        permission = Permission.objects.get(code="corrections.review")
        UserPermission.objects.create(user=self.reviewer_user, permission=permission, tenant=self.tenant,
            campus=self.campus, grant_type="DENY")
        self.assertEqual(self.client.get(history).status_code, 403)

    def test_unauthorized_settings_and_history(self):
        request_obj = self.pending()
        self.client.force_login(self.faculty_user)
        self.assertEqual(self.client.get(reverse("admin_portal:correction_email_settings")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin_portal:correction_email_history", args=[request_obj.pk])).status_code, 403)

    def test_global_direct_deny_blocks_settings_and_history(self):
        request_obj = self.pending()
        self.client.force_login(self.reviewer_user)
        for code, url in [
                ("system_settings.update", reverse("admin_portal:correction_email_settings")),
                ("corrections.review", reverse("admin_portal:correction_email_history", args=[request_obj.pk]))]:
            UserPermission.objects.create(user=self.reviewer_user, permission=Permission.objects.get(code=code),
                tenant=None, campus=None, grant_type="DENY")
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_from_to_labels_rendered_in_faculty_review_and_print(self):
        request_obj = self.pending()
        self.client.force_login(self.reviewer_user)
        response = self.client.get(reverse("admin_portal:grade_correction_request_review", args=[request_obj.pk]))
        self.assertContains(response, "<th>From</th>", html=True)
        self.assertContains(response, "<th>To</th>", html=True)
        self.assertNotContains(response, "Original Value")
        CorrectionPetitionWindowPolicy.objects.create(tenant=self.tenant, campus=self.campus,
            academic_year=self.academic_year, term=self.term, grading_period=self.period,
            policy_mode="OPEN_ANYTIME")
        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:period_corrections", args=[self.offering.pk, self.period.pk]))
        self.assertEqual(response.status_code, 200, response.get("Location"))
        self.assertContains(response, ">From</th>")
        self.assertContains(response, ">To</th>")

    def test_command_output_has_only_counts(self):
        self.pending(initial=False)
        output = StringIO()
        call_command("process_correction_reminders", stdout=output)
        text = output.getvalue()
        self.assertIn("sent=1", text)
        self.assertNotIn(self.reviewer_user.email, text)
        self.assertNotIn("PRIVATE", text)


    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_console_backend_never_prints_email_body_to_cron_logs(self):
        self.pending(initial=False)
        output = StringIO()
        with patch("sys.stdout", output):
            result = Service.dispatch()
        self.assertEqual(result["sent"], 0)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(Delivery.objects.get(kind=Delivery.Kind.INITIAL).outcome_code, "unsafe_console_backend")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class CorrectionReminderTransactionTests(ReminderFixtures, TransactionTestCase):
    def test_real_commit_dispatches_and_real_rollback_does_not(self):
        with transaction.atomic():
            request_obj = self.create_request()
            self.assertFalse(mail.outbox)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(Delivery.objects.get().status, Delivery.Status.SENT)
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Governance.review_correction_request(request_obj=request_obj, reviewer=self.reviewer_user, approved=False)
                raise RuntimeError("rollback")
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, GradeCorrectionRequest.Status.PENDING)

    def test_simultaneous_claims_have_one_owner(self):
        with patch.object(Service, "dispatch_safely"):
            self.create_request()
        pk = Delivery.objects.get().pk
        barrier = Barrier(2)
        now = timezone.now()
        def claim():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return Service.claim(pk, now=now)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(token is not None for token in results), 1)
        self.assertEqual(Delivery.objects.get().attempt_count, 1)

    def test_simultaneous_dispatch_sends_one_recipient_once(self):
        with patch.object(Service, "dispatch_safely"):
            request_obj = self.create_request()
        barrier = Barrier(2)
        original_claim = Service.claim
        def synchronized_claim(*args, **kwargs):
            barrier.wait(timeout=10)
            return original_claim(*args, **kwargs)
        def worker():
            close_old_connections()
            try:
                return Service.dispatch(request_id=request_obj.pk)
            finally:
                close_old_connections()
        with patch("django.core.mail.EmailMultiAlternatives.send", return_value=1) as send:
            with patch.object(Service, "claim", side_effect=synchronized_claim):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: worker(), range(2)))
            self.assertLessEqual(send.call_count, 1)
            self.assertLessEqual(sum(result["sent"] for result in results), 1)
            # SQLite may reject a read-to-write lock upgrade during concurrent preflight.
            # Such a failure must be durable/retryable and must never duplicate SMTP.
            if Delivery.objects.get().status == Delivery.Status.FAILED:
                Service.dispatch(request_id=request_obj.pk, now=timezone.now() + timedelta(minutes=6))
            self.assertEqual(send.call_count, 1)
            self.assertEqual(Delivery.objects.get().status, Delivery.Status.SENT)
