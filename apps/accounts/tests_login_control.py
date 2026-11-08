from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from io import StringIO
import re
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import mail
from django.contrib.auth import authenticate
from django.contrib.auth import aauthenticate
from asgiref.sync import async_to_sync
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction, IntegrityError
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.login_control import LoginAccessService as Access, StaleLoginControl
from apps.accounts.models import ClearanceLoginDeadline as Deadline, User, ActivePortalSession
from apps.accounts.services import ActivePortalSessionService
from apps.academics.models import FacultyAssignment, CourseOffering, Course, Section, Term
from apps.admin_portal.services import AdminScopeService
from apps.auditlog.models import AuditLog
from apps.attendance.models import AttendanceSession, AttendanceRecord
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.grading.models import FacultyFinalClearanceReport, StudentFinalGrade, StudentPeriodGrade, GradeSubmission
from apps.grading.reporting import FacultyFinalClearanceReportService as Reports
from apps.departmental_exams.models import ExaminationCycle, CycleCourse, FacultyContribution
from apps.rbac.models import Permission, Role, RolePermission, UserRole, UserPermission
from apps.tenants.models import Campus, Department, Program
from apps.tenants.models import SystemSetting, Tenant
from apps.faculty_portal import tests_assignment_acceptance as fixtures


class LoginFixture:
    # Reuse established grading fixtures without inheriting/discovering their
    # unrelated test methods. Requests still run the real middleware and views.
    _accept_assignment = fixtures.FacultyAssignmentAcceptanceTests._accept_assignment
    _create_active_student = fixtures.FacultyAssignmentAcceptanceTests._create_active_student
    _complete_final_clearance_for_offering = fixtures.FacultyAssignmentAcceptanceTests._complete_final_clearance_for_offering

    def setUp(self):
        Permission.objects.get_or_create(code="faculty_portal.access", defaults={"module": "faculty_portal", "action": "access"})
        fixtures.FacultyAssignmentAcceptanceTests.setUp(self)
        self._accept_assignment()
        self._complete_final_clearance_for_offering()
        self.user = self.faculty_user
        self.admin = User.objects.create_superuser(
            username="login-control-admin", email="login-control-admin@example.edu", password="AdminPass123!",
            default_tenant=self.tenant, default_campus=self.campus,
            privacy_consent_at=timezone.now(), privacy_consent_version=settings.PRIVACY_CONSENT_VERSION,
        )
        for code in (Access.PERMISSION, "users.read", "admin_portal.access", "system_settings.update", "faculty_final_clearance.read"):
            Permission.objects.get_or_create(code=code, defaults={"module": code.split(".")[0], "action": code.split(".")[-1]})
        self.feature(True)
        self.url = reverse("admin_portal:user_login_control", args=[self.user.pk])
        self.pdf_url = reverse("faculty_portal:period_final_clearance", args=[self.offering.pk, self.final.pk])

    def feature(self, enabled):
        Access.save_automatic_policy(tenant_id=self.tenant.pk, enabled=enabled, delay_days=Access.automatic_delay_days(self.tenant.pk), actor=self.admin)

    def event(self, *, at=None, term=None):
        report = Reports.generate_report_record(faculty_user=self.user, campus=self.campus, term=term or self.term, generated_by_user=self.user)
        if term:
            # A new cycle's eligibility fixture is independent of the old term.
            report.clearance_status = "CLEARED"
            report.save(update_fields=["clearance_status"])
        return Access.record_generation(report=report, generated_at=at or timezone.now())

    def manual(self, disabled, version=None, actor=None):
        self.user.refresh_from_db()
        return Access.set_manual(
            actor=actor or self.admin, target_id=self.user.pk, disabled=disabled,
            expected_version=self.user.login_access_version if version is None else version,
            tenant_id=self.tenant.pk, campus_id=self.campus.pk,
        )

    def other_loading(self):
        campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other campus")
        department = Department.objects.create(tenant=self.tenant, campus=campus, code="OTHER", name="Other department")
        program = Program.objects.create(tenant=self.tenant, campus=campus, department=department, code="OTHER", name="Other")
        course = Course.objects.create(tenant=self.tenant, campus=campus, department=department, code="OTHER", title="Other")
        section = Section.objects.create(tenant=self.tenant, campus=campus, department=department, program=program, code="OTHER", name="Other")
        offering = CourseOffering.objects.create(tenant=self.tenant, campus=campus, department=department, program=program, course=course, section=section, term=self.term, academic_year=self.academic_year)
        return FacultyAssignment.objects.create(tenant=self.tenant, campus=campus, offering=offering, faculty_user=self.user, response_status="ACCEPTED", accepted_at=timezone.now())


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"], EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class LoginControlTests(LoginFixture, TestCase):
    def test_pdf_preview_does_not_schedule(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.pdf_url).status_code, 200)
        self.assertFalse(Deadline.objects.exists())

    def test_successful_pdf_and_repeated_generation_keep_first_deadline(self):
        self.client.force_login(self.user)
        for _ in range(2):
            response = self.client.post(self.pdf_url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))
        self.assertEqual(FacultyFinalClearanceReport.objects.count(), 2)
        event = Deadline.objects.get()
        self.assertEqual(event.disable_at - event.first_generated_at, timedelta(hours=48))
        self.assertEqual(event.source_report_id, FacultyFinalClearanceReport.objects.order_by("pk").first().pk)
        response = self.client.get(self.pdf_url)
        self.assertContains(response, "Asia/Manila")
        self.assertEqual(AuditLog.objects.filter(action="CLEARANCE_LOGIN_SCHEDULED").count(), 1)

    def test_failed_pdf_does_not_schedule(self):
        self.client.force_login(self.user)
        with patch.object(Reports, "build_pdf_bytes", side_effect=RuntimeError("render failed")):
            with self.assertRaises(RuntimeError):
                self.client.post(self.pdf_url)
        self.assertFalse(Deadline.objects.exists())

    def test_incomplete_clearance_does_not_schedule(self):
        StudentFinalGrade.objects.all().delete()
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.pdf_url).status_code, 302)
        self.assertFalse(Deadline.objects.exists())

    def test_uncleared_report_is_never_scheduled(self):
        StudentFinalGrade.objects.all().delete()
        self.assertIsNone(self.event())

    def test_exact_cutoff_and_idempotent_repeated_worker(self):
        now = timezone.now()
        event = self.event(at=now)
        self.assertEqual(Access.process_due(at=event.disable_at - timedelta(microseconds=1)), [])
        self.assertEqual(len(Access.process_due(at=event.disable_at)), 1)
        self.assertEqual(Access.process_due(at=event.disable_at + timedelta(hours=1)), [])
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_disabled)
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.login_access_version, 1)
        self.assertEqual(AuditLog.objects.filter(action="AUTO_DISABLE_LOGIN").count(), 1)

    def test_authentication_enforces_due_cutoff_without_cron(self):
        event = self.event(at=timezone.now() - timedelta(hours=48))
        self.assertIsNone(authenticate(username=self.user.username, password="testpass123"))
        event.refresh_from_db()
        self.assertEqual(event.state, "APPLIED")

    def test_registered_session_is_revoked_at_due_cutoff(self):
        self.client.force_login(self.user)
        ActivePortalSessionService.register(request=self.request_for(self.client), user=self.user, enforce_single_session=False)
        key = self.client.session.session_key
        self.event(at=timezone.now() - timedelta(hours=48))
        self.assertEqual(self.client.get(self.pdf_url).status_code, 302)
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertFalse(ActivePortalSession.objects.filter(user=self.user).exists())

    @staticmethod
    def request_for(client):
        request = RequestFactory().get("/faculty/")
        request.session = client.session
        return request

    def test_manual_enable_overrides_applied_event_and_reprint_forever(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        Access.process_due()
        self.manual(False)
        repeat = self.event()
        self.assertEqual(repeat.pk, event.pk)
        self.assertEqual(repeat.state, "OVERRIDDEN")
        self.assertEqual(Access.process_due(), [])
        self.user.refresh_from_db()
        self.assertFalse(self.user.login_disabled)

    def test_manual_enable_overrides_pending_events_across_cycles(self):
        event = self.event()
        self.manual(True)
        self.manual(False)
        event.refresh_from_db()
        self.assertEqual(event.state, "OVERRIDDEN")
        self.assertEqual(Access.process_due(at=event.disable_at), [])

    def test_new_clearance_cycle_can_schedule_after_override(self):
        self.event()
        self.manual(True)
        self.manual(False)
        self.offering.status = "CLOSED"
        self.offering.save(update_fields=["status"])
        term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year, code="NEXT", name="Next")
        event = self.event(term=term)
        self.assertEqual(event.state, "PENDING")
        self.assertEqual(event.access_version, 2)
        Access.process_due(at=event.disable_at)
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_disabled)

    def test_generation_in_flight_cannot_undo_manual_override(self):
        self.manual(True)
        self.manual(False)
        report = Reports.generate_report_record(faculty_user=self.user, campus=self.campus, term=self.term, generated_by_user=self.user)
        event = Access.record_generation(report=report, access_version=0)
        self.assertEqual(event.state, "OVERRIDDEN")
        self.assertEqual(Access.process_due(at=event.disable_at), [])

    def test_feature_off_cancels_pending_and_marks_off_generations_terminal(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        self.feature(False)
        self.assertEqual(Access.process_due(), [])
        self.assertTrue(Access.login_allowed(self.user))
        self.assertEqual(self.event().pk, event.pk)
        event.refresh_from_db()
        self.assertEqual(event.state, "CANCELED")
        self.assertEqual(event.resolved_by_id, self.admin.pk)
        self.assertTrue(AuditLog.objects.filter(action="CLEARANCE_LOGIN_CANCELED", entity_id=str(event.pk)).exists())
        self.feature(True)
        self.assertEqual(self.event().state, "CANCELED")
        self.assertEqual(Access.process_due(), [])

    def test_feature_off_does_not_restore_disabled_login(self):
        self.event(at=timezone.now() - timedelta(hours=49))
        Access.process_due()
        self.feature(False)
        self.user.refresh_from_db()
        self.assertFalse(Access.login_allowed(self.user))

    def test_historical_report_does_not_backfill_a_deadline(self):
        Reports.generate_report_record(faculty_user=self.user, campus=self.campus, term=self.term, generated_by_user=self.user)
        self.assertEqual(Access.process_due(), [])
        self.assertFalse(Deadline.objects.exists())

    def test_feature_defaults_off_without_a_setting(self):
        SystemSetting.objects.filter(setting_key=Access.FEATURE_KEY).delete()
        self.assertFalse(Access.automatic_enabled(self.tenant.pk))
        self.assertEqual(self.event().state, "CANCELED")

    def test_malformed_string_feature_setting_cannot_enable_automation(self):
        SystemSettingService.set(Access.FEATURE_KEY, "false", tenant_id=self.tenant.pk, value_type="STRING", is_active=True)
        self.assertFalse(Access.automatic_enabled(self.tenant.pk))
        self.assertEqual(self.event().state, "CANCELED")

    def test_other_campus_loading_added_after_generation_is_rechecked(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        self.other_loading()
        Access.process_due()
        event.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(event.state, "EXEMPT")
        self.assertFalse(self.user.login_disabled)

    def test_async_authentication_cannot_fall_through_to_legacy_backend(self):
        self.manual(True)
        self.assertIsNone(async_to_sync(aauthenticate)(username=self.user.username, password="testpass123"))

    def test_policy_post_is_tenant_scoped_and_audited(self):
        AuditLog.objects.filter(action="CLEARANCE_LOGIN_POLICY").delete()
        self.client.force_login(self.admin)
        url = reverse("admin_portal:clearance_login_policy")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"delay_days": 2}).status_code, 302)
        self.assertFalse(Access.automatic_enabled(self.tenant.pk))
        self.assertEqual(AuditLog.objects.get(action="CLEARANCE_LOGIN_POLICY").tenant_id, self.tenant.pk)
        self.assertEqual(self.client.post(url, {"enabled": "on", "delay_days": 3}).status_code, 302)
        self.assertTrue(Access.automatic_enabled(self.tenant.pk))
        self.assertEqual(Access.automatic_delay_days(self.tenant.pk), 3)

    def test_policy_controls_render_persisted_days_and_default_two(self):
        self.client.force_login(self.admin)
        url = reverse("admin_portal:configurable_features_settings")
        response = self.client.get(url)
        self.assertContains(response, "Automatic Faculty Login Disabling")
        self.assertContains(response, 'name="delay_days"')
        self.assertEqual(response.context["clearance_login_policy_form"].initial["delay_days"], 2)
        self.client.post(reverse("admin_portal:clearance_login_policy"), {"enabled": "on", "delay_days": 5})
        self.assertEqual(self.client.get(url).context["clearance_login_policy_form"].initial["delay_days"], 5)
        setting = SystemSetting.objects.get(tenant=self.tenant, setting_key=Access.DELAY_KEY)
        self.assertEqual((setting.value_type, setting.setting_value), ("INT", "5"))

    def test_policy_rejects_invalid_days_without_mutation_or_canceling(self):
        self.client.force_login(self.admin)
        event = self.event()
        before = list(SystemSetting.objects.values())
        audit_count = AuditLog.objects.count()
        for value in (None, "", "0", "-1", "1.5", "abc", "999999999999999999999999"):
            with self.subTest(days=value):
                data = {} if value is None else {"delay_days": value}
                self.assertEqual(self.client.post(reverse("admin_portal:clearance_login_policy"), data).status_code, 400)
        self.assertEqual(list(SystemSetting.objects.values()), before)
        self.assertEqual(AuditLog.objects.count(), audit_count)
        event.refresh_from_db()
        self.assertEqual(event.state, "PENDING")

    def test_service_rejects_non_whole_or_subminimum_days(self):
        for days in (0, -1, 1.5, True, "2"):
            with self.subTest(days=days), self.assertRaises(ValidationError):
                Access.save_automatic_policy(tenant_id=self.tenant.pk, enabled=True, delay_days=days, actor=self.admin)

    def test_minimum_one_day_and_elapsed_hours_across_dst(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("admin_portal:clearance_login_policy"), {"enabled": "on", "delay_days": 1}).status_code, 302)
        at = datetime(2026, 3, 7, 12, tzinfo=ZoneInfo("America/New_York"))
        event = self.event(at=at)
        self.assertEqual(event.delay_days, 1)
        self.assertEqual(event.disable_at - at.astimezone(datetime_timezone.utc), timedelta(hours=24))
        self.assertEqual(Access.process_due(at=event.disable_at - timedelta(microseconds=1)), [])
        self.assertEqual(len(Access.process_due(at=event.disable_at)), 1)

    def test_day_changes_only_affect_new_cycles_and_keep_first_snapshot(self):
        first = self.event()
        Access.save_automatic_policy(tenant_id=self.tenant.pk, enabled=True, delay_days=5, actor=self.admin)
        repeat = self.event(at=timezone.now() + timedelta(hours=1))
        self.assertEqual((repeat.pk, repeat.first_generated_at, repeat.disable_at, repeat.delay_days),
                         (first.pk, first.first_generated_at, first.disable_at, 2))
        term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year, code="DELAY-NEXT", name="Next")
        self.offering.status = "CLOSED"
        self.offering.save(update_fields=["status"])
        new = self.event(term=term)
        self.assertEqual(new.delay_days, 5)
        self.assertEqual(new.disable_at - new.first_generated_at, timedelta(hours=120))
        self.client.force_login(self.user)
        response = self.client.get(self.pdf_url)
        self.assertContains(response, "2-day login deadline")

    def test_global_policy_cannot_enable_or_change_tenant_defaults(self):
        SystemSetting.objects.filter(tenant=self.tenant, setting_key__in=[Access.FEATURE_KEY, Access.DELAY_KEY]).delete()
        SystemSettingService.set(Access.FEATURE_KEY, True, value_type="BOOL", is_active=True)
        SystemSettingService.set(Access.DELAY_KEY, 8, value_type="INT", is_active=True)
        self.assertFalse(Access.automatic_enabled(self.tenant.pk))
        self.assertFalse(Access.automatic_enabled(None))
        self.assertEqual(Access.automatic_delay_days(self.tenant.pk), 2)
        self.assertEqual(self.event().state, "CANCELED")

    def test_upgrade_protects_historical_clearance_reprints_without_assuming_success(self):
        from importlib import import_module
        from django.apps import apps
        from apps.accounts.models import HistoricalClearanceCycle
        self.feature(False)
        historical = Reports.generate_report_record(faculty_user=self.user, campus=self.campus, term=self.term, generated_by_user=self.user)
        from apps.core.services.audit import AuditService
        AuditService.log_event(action="DOWNLOAD", portal="FACULTY", entity_type="FacultyFinalClearanceReport", entity_id=historical.pk, actor=self.user, tenant=self.tenant, campus=self.campus)
        migration = import_module("apps.accounts.migrations.0015_historical_clearance_cycles")
        migration.capture_legacy_cycles(apps, SimpleNamespace(connection=connection))
        self.assertTrue(HistoricalClearanceCycle.objects.filter(user=self.user, tenant=self.tenant, campus=self.campus, term=self.term).exists())
        self.feature(True)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.pdf_url).status_code, 200)
        marker = Deadline.objects.get(user=self.user, term=self.term)
        self.assertEqual(marker.state, "EXEMPT")
        self.assertTrue(AuditLog.objects.filter(action="CLEARANCE_LOGIN_EXCEPTION", entity_id=marker.pk).exists())
        self.assertEqual(self.client.post(self.pdf_url).status_code, 200)
        self.assertEqual(Deadline.objects.filter(user=self.user, term=self.term).count(), 1)
        self.user.refresh_from_db()
        self.assertFalse(self.user.login_disabled)

    def test_unproven_legacy_record_is_conservatively_exempt(self):
        from importlib import import_module
        from django.apps import apps
        Reports.generate_report_record(faculty_user=self.user, campus=self.campus, term=self.term, generated_by_user=self.user)
        import_module("apps.accounts.migrations.0015_historical_clearance_cycles").capture_legacy_cycles(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.event().state, "EXEMPT")

    def test_historical_marker_does_not_exempt_a_new_term(self):
        from apps.accounts.models import HistoricalClearanceCycle
        HistoricalClearanceCycle.objects.create(user=self.user, tenant=self.tenant, campus=self.campus, term=self.term)
        self.offering.status = "CLOSED"
        self.offering.save(update_fields=["status"])
        later = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year, code="NEW-CLEAR", name="New term")
        self.assertEqual(self.event(term=later).state, "PENDING")

    def test_off_pdf_cannot_be_scheduled_by_reenable_or_reprint(self):
        self.feature(False)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.pdf_url).status_code, 200)
        marker = Deadline.objects.get()
        self.assertEqual(marker.state, "CANCELED")
        self.feature(True)
        self.assertEqual(Access.process_due(at=marker.disable_at + timedelta(days=1)), [])
        self.assertEqual(self.client.post(self.pdf_url).status_code, 200)
        marker.refresh_from_db()
        self.assertEqual(marker.state, "CANCELED")
        self.assertEqual(Deadline.objects.count(), 1)
        term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year, code="AFTER-OFF", name="Next")
        self.offering.status = "CLOSED"
        self.offering.save(update_fields=["status"])
        self.assertEqual(self.event(term=term).state, "PENDING")

    def test_pdf_uses_configured_days_and_success_timestamp_before_download_audit(self):
        Access.save_automatic_policy(tenant_id=self.tenant.pk, enabled=True, delay_days=3, actor=self.admin)
        self.client.force_login(self.user)
        original_audit = Access.__module__
        from apps.core.services.audit import AuditService
        log_event = AuditService.log_event
        download_times = []
        def log(**kwargs):
            if kwargs["action"] == "DOWNLOAD":
                download_times.append(timezone.now())
            return log_event(**kwargs)
        with patch(f"{original_audit}.AuditService.log_event", side_effect=log):
            self.assertEqual(self.client.post(self.pdf_url).status_code, 200)
        event = Deadline.objects.get()
        self.assertLessEqual(event.first_generated_at, download_times[0])
        self.assertEqual(event.delay_days, 3)
        self.assertEqual(event.disable_at - event.first_generated_at, timedelta(hours=72))
        self.assertContains(self.client.get(self.pdf_url), "3-day login deadline")

    def test_policy_requires_permission_csrf_and_authorized_tenant(self):
        url = reverse("admin_portal:clearance_login_policy")
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url, {"delay_days": 2}).status_code, 403)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(url, {"delay_days": 2}).status_code, 403)
        from apps.admin_portal.login_control_views import clearance_login_policy_view
        request = RequestFactory().post(url, {"delay_days": 2})
        request.user = self.admin
        for scope in ({"tenant_id": None}, {"tenant_id": self.tenant.pk, "tenant_ids": []}):
            request.scope = scope
            with self.subTest(scope=scope), self.assertRaises(PermissionDenied):
                clearance_login_policy_view(request)

    def test_manual_controls_work_with_policy_on_and_off(self):
        for enabled in (False, True):
            self.feature(enabled)
            self.manual(True)
            self.user.refresh_from_db()
            self.assertTrue(self.user.login_disabled)
            self.manual(False)
            self.user.refresh_from_db()
            self.assertFalse(self.user.login_disabled)

    def test_policy_off_audit_failure_rolls_back_setting_and_cancellation(self):
        event = self.event()
        with patch("apps.accounts.login_control.AuditService.log_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.feature(False)
        self.assertTrue(Access.automatic_enabled(self.tenant.pk))
        event.refresh_from_db()
        self.assertEqual(event.state, "PENDING")

    def test_off_cancels_all_tenant_campuses_but_preserves_other_tenants(self):
        event = self.event()
        loading = self.other_loading()
        report = Reports.generate_report_record(faculty_user=self.user, campus=loading.offering.campus, term=self.term, generated_by_user=self.user)
        second = Deadline.objects.create(user=self.user, tenant=self.tenant, campus=loading.offering.campus, term=self.term, source_report=report,
            first_generated_at=event.first_generated_at, disable_at=event.disable_at, access_version=0)
        other_tenant = Tenant.objects.create(code="OTHER-POLICY", name="Other")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER", name="Other")
        other = Deadline.objects.create(user=self.user, tenant=other_tenant, campus=other_campus, term=self.term, source_report=report,
            first_generated_at=event.first_generated_at, disable_at=event.disable_at, access_version=0)
        self.feature(False)
        event.refresh_from_db()
        second.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((event.state, second.state, other.state), ("CANCELED", "CANCELED", "PENDING"))

    def test_deadline_snapshot_rejects_zero_days_in_database(self):
        event = self.event()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Deadline.objects.filter(pk=event.pk).update(delay_days=0)

    def test_policy_direct_deny_overrides_superuser(self):
        UserPermission.objects.create(user=self.admin, permission=Permission.objects.get(code="system_settings.update"), grant_type="DENY", tenant=self.tenant, campus=self.campus)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse("admin_portal:clearance_login_policy"), {}).status_code, 403)
        self.assertTrue(Access.automatic_enabled(self.tenant.pk))

    def test_tenant_feature_does_not_enable_another_tenant(self):
        tenant = Tenant.objects.create(code="LOGIN-ISOLATED", name="Isolated")
        self.assertFalse(Access.automatic_enabled(tenant.pk))

    def test_scoped_operator_cannot_mutate_another_tenant_through_direct_post(self):
        tenant = Tenant.objects.create(code="OTHER-TENANT", name="Other tenant")
        campus = Campus.objects.create(tenant=tenant, code="OTHER", name="Other")
        role = Role.objects.create(code="OUTSIDE-OP", name="Outside operator")
        operator = User.objects.create_user(username="outside-op", email="outside-op@example.edu", password="Pass123!", privacy_consent_at=timezone.now(), privacy_consent_version=settings.PRIVACY_CONSENT_VERSION)
        UserRole.objects.create(user=operator, role=role, tenant=tenant, campus=campus)
        for code in (Access.PERMISSION, "admin_portal.access"):
            RolePermission.objects.create(role=role, permission=Permission.objects.get(code=code))
        self.client.force_login(operator)
        response = self.client.post(self.url, {"operation": "disable", "version": 0, "confirmed": "yes"})
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertFalse(self.user.login_disabled)

    def test_dedicated_permission_and_direct_deny_for_scoped_operator(self):
        role = Role.objects.create(code="SCOPED-OP", name="Scoped operator")
        operator = User.objects.create_user(username="scoped-op", email="scoped-op@example.edu", password="Pass123!")
        UserRole.objects.create(user=operator, role=role, tenant=self.tenant, campus=self.campus)
        with self.assertRaises(PermissionDenied):
            self.manual(True, actor=operator)
        permission = Permission.objects.get(code=Access.PERMISSION)
        RolePermission.objects.create(role=role, permission=permission)
        UserPermission.objects.create(user=operator, permission=permission, grant_type="DENY", tenant=self.tenant, campus=self.campus)
        with self.assertRaises(PermissionDenied):
            self.manual(True, actor=operator)

    def test_stale_worker_candidate_rechecks_after_manual_enable(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        self.manual(True)
        original = Access.process_user
        def after_candidates(user_id, **kwargs):
            self.manual(False)
            return original(user_id, **kwargs)
        with patch.object(Access, "process_user", side_effect=after_candidates):
            self.assertEqual(Access.process_due(), [])
        event.refresh_from_db()
        self.assertEqual(event.state, "OVERRIDDEN")

    def test_unrelated_sessions_survive_manual_disable(self):
        other = Client()
        other.force_login(self.admin)
        key = other.session.session_key
        self.manual(True)
        self.assertTrue(Session.objects.filter(session_key=key).exists())

    def test_reference_fits_existing_schema_and_rapid_requests_differ(self):
        kwargs = {"faculty_user": SimpleNamespace(id=9223372036854775807), "term": SimpleNamespace(code="X" * 50)}
        first = Reports._reference_no(**kwargs)
        self.assertLessEqual(len(first), 64)
        self.assertNotEqual(first, Reports._reference_no(**kwargs))

    def test_other_campus_loading_is_recorded_for_mis(self):
        self.other_loading()
        event = self.event()
        self.assertEqual(event.state, "EXEMPT")
        self.assertIn("another campus", event.reason)
        self.assertEqual(Access.process_due(at=event.disable_at), [])
        self.assertTrue(AuditLog.objects.filter(action="CLEARANCE_LOGIN_EXCEPTION").exists())

    def test_added_role_is_rechecked_at_deadline(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        role = Role.objects.create(code="SECOND-ROLE", name="Second role")
        UserRole.objects.create(user=self.user, role=role, tenant=self.tenant, campus=self.campus)
        Access.process_due()
        event.refresh_from_db()
        self.assertEqual(event.state, "EXEMPT")
        self.user.refresh_from_db()
        self.assertFalse(self.user.login_disabled)

    def test_dry_run_and_command_do_not_change_state(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        out = StringIO()
        call_command("process_clearance_login_deadlines", dry_run=True, stdout=out)
        self.assertIn("Would process 1", out.getvalue())
        event.refresh_from_db()
        self.assertEqual(event.state, "PENDING")

    def test_worker_audit_failure_rolls_back_and_retry_applies_once(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        with patch("apps.accounts.login_control.AuditService.log_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                Access.process_due()
        self.user.refresh_from_db()
        event.refresh_from_db()
        self.assertFalse(self.user.login_disabled)
        self.assertEqual(event.state, "PENDING")
        Access.process_due()
        self.assertEqual(AuditLog.objects.filter(action="AUTO_DISABLE_LOGIN").count(), 1)

    def test_duplicate_cycle_constraint(self):
        event = self.event()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Deadline.objects.create(user=self.user, tenant=self.tenant, campus=self.campus, term=self.term, source_report=event.source_report, first_generated_at=event.first_generated_at, disable_at=event.disable_at, access_version=0)

    def test_manual_post_confirmation_stale_conflict_and_audit(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url, {"operation": "disable", "version": 0}).status_code, 400)
        data = {"operation": "disable", "version": 0, "confirmed": "yes"}
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(self.client.post(self.url, data).status_code, 409)
        log = AuditLog.objects.get(action="DISABLE_LOGIN")
        self.assertEqual(log.actor_user_id, self.admin.pk)
        self.assertEqual(log.entity_id, str(self.user.pk))
        self.assertFalse(log.before_json["login_disabled"])
        self.assertTrue(log.after_json["login_disabled"])
        self.assertIsNotNone(log.created_at)

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(self.url, {"operation": "disable", "version": 0, "confirmed": "yes"}).status_code, 403)

    def test_users_row_renders_confirmation_and_independent_status(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_portal:user_list"))
        self.assertContains(response, f'aria-label="Disable Login for {self.user.username}"')
        self.assertContains(response, 'name="confirmed"')
        self.manual(True)
        response = self.client.get(reverse("admin_portal:user_list"))
        self.assertContains(response, f'aria-label="Enable Login for {self.user.username}"')

    def test_password_login_is_denied_in_both_portals(self):
        self.manual(True)
        self.assertIsNone(authenticate(username=self.user.username, password="testpass123"))
        for url in (reverse("accounts:admin_login"), reverse("faculty_portal:public_index")):
            self.client.post(url, {"username": self.user.username, "password": "testpass123"})
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_django_admin_login_is_denied(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.manual(True)
        self.client.post(reverse("admin:login"), {"username": self.user.username, "password": "testpass123", "next": "/django-admin/"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_pending_otp_cannot_complete_after_disable_and_enable(self):
        SystemSettingService.set("FEATURE_LOGIN_EMAIL_OTP_ENABLED", True, tenant_id=self.tenant.pk, value_type="BOOL", is_active=True)
        self.client.post(reverse("faculty_portal:public_index"), {"username": self.user.username, "password": "testpass123"})
        self.assertIn("pending_login_otp_user_id", self.client.session)
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        self.manual(True)
        self.manual(False)
        self.client.post(reverse("accounts:faculty_login_otp"), {"otp_code": code})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unregistered_legacy_session_never_returns_after_enable(self):
        self.client.force_login(self.user, backend="django.contrib.auth.backends.ModelBackend")
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        self.assertFalse(ActivePortalSession.objects.filter(user=self.user).exists())
        self.manual(True)
        self.manual(False)
        self.client.cookies[settings.SESSION_COOKIE_NAME] = cookie
        self.assertEqual(self.client.get(self.pdf_url).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_legacy_session_is_rejected_while_disabled(self):
        self.client.force_login(self.user, backend="django.contrib.auth.backends.ModelBackend")
        self.manual(True)
        self.assertEqual(self.client.get(self.pdf_url).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_stale_authenticated_object_cannot_register_a_session(self):
        stale = User.objects.get(pk=self.user.pk)
        self.client.force_login(stale)
        self.manual(True)
        self.manual(False)
        with self.assertRaises(PermissionDenied):
            ActivePortalSessionService.register(request=self.request_for(self.client), user=stale, enforce_single_session=False)

    def test_enable_does_not_reactivate_inactive_account(self):
        self.manual(True)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.manual(False)
        self.assertIsNone(authenticate(username=self.user.username, password="testpass123"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_scoped_permission_denies_wrong_scope_and_multicampus_account(self):
        role = Role.objects.create(code="LOGIN-OP", name="Login operator")
        RolePermission.objects.create(role=role, permission=Permission.objects.get(code=Access.PERMISSION))
        operator = User.objects.create_user(username="operator", email="operator@example.edu", password="Pass123!")
        UserRole.objects.create(user=operator, role=role, tenant=self.tenant, campus=self.campus)
        self.manual(True, actor=operator)
        self.manual(False, actor=operator)
        self.other_loading()
        with self.assertRaises(PermissionDenied):
            self.manual(True, actor=operator)

    def test_no_permission_direct_deny_self_and_superuser_protection(self):
        with self.assertRaises(PermissionDenied):
            self.manual(True, actor=self.user)
        UserPermission.objects.create(user=self.admin, permission=Permission.objects.get(code=Access.PERMISSION), grant_type="DENY", tenant=self.tenant, campus=self.campus)
        with self.assertRaises(PermissionDenied):
            self.manual(True)
        with self.assertRaises(PermissionDenied):
            Access.set_manual(actor=self.admin, target_id=self.admin.pk, disabled=True, expected_version=0, tenant_id=self.tenant.pk, campus_id=self.campus.pk)

    def test_manual_audit_failure_rolls_back_flag_and_session_revocation(self):
        self.client.force_login(self.user)
        ActivePortalSessionService.register(request=self.request_for(self.client), user=self.user, enforce_single_session=False)
        key = self.client.session.session_key
        with patch("apps.accounts.login_control.AuditService.log_event", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                self.manual(True)
        self.user.refresh_from_db()
        self.assertFalse(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 0)
        self.assertTrue(Session.objects.filter(session_key=key).exists())

    def test_grades_loading_and_admin_history_remain_visible(self):
        cycle = ExaminationCycle.objects.create(tenant=self.tenant, academic_year=self.academic_year, term=self.term, exam_period="FINAL", status="OPEN", created_by=self.admin)
        cycle_course = CycleCourse.objects.create(cycle=cycle, course=self.course, responsible_department=self.department)
        FacultyContribution.objects.create(cycle_course=cycle_course, faculty_user=self.user, source_assignment=self.assignment, source_campus=self.campus, quota_snapshot=50, configuration_revision_snapshot=1, status="SUBMITTED", submitted_at=timezone.now())
        contributions_before = list(FacultyContribution.objects.values())
        attendance = AttendanceSession.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.final, session_date=timezone.localdate(), created_by_user=self.user)
        AttendanceRecord.objects.create(tenant=self.tenant, campus=self.campus, session=attendance, student_id=StudentFinalGrade.objects.get().student_id, status_code="P", recorded_by_user=self.user)
        attendance_before = (list(AttendanceSession.objects.values()), list(AttendanceRecord.objects.values()))
        AuditLog.objects.create(actor_user=self.admin, action="EXISTING_HISTORY", entity_type="User", entity_id=str(self.user.pk), tenant=self.tenant, campus=self.campus)
        audit_ids = set(AuditLog.objects.values_list("pk", flat=True))
        before = (list(StudentFinalGrade.objects.values()), list(StudentPeriodGrade.objects.values()), list(GradeSubmission.objects.values()), list(FacultyAssignment.objects.values()))
        self.manual(True)
        after = (list(StudentFinalGrade.objects.values()), list(StudentPeriodGrade.objects.values()), list(GradeSubmission.objects.values()), list(FacultyAssignment.objects.values()))
        self.assertEqual(before, after)
        self.assertEqual(contributions_before, list(FacultyContribution.objects.values()))
        self.assertEqual(attendance_before, (list(AttendanceSession.objects.values()), list(AttendanceRecord.objects.values())))
        self.assertTrue(audit_ids.issubset(set(AuditLog.objects.values_list("pk", flat=True))))
        request = RequestFactory().get("/admin-portal/")
        request.user = self.admin
        request.session = {}
        ScopeService.attach_scope_to_request(request)
        self.assertIn(self.user.pk, AdminScopeService.scoped_faculty_users(request))
        self.assertIn(self.assignment.pk, AdminScopeService.scoped_faculty_assignments(request).values_list("pk", flat=True))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class LoginConcurrencyTests(LoginFixture, TransactionTestCase):
    def race(self, *operations):
        barrier = Barrier(len(operations))
        def run(operation):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(run, operations))

    def test_concurrent_generation_creates_one_fixed_deadline(self):
        report = Reports.generate_report_record(faculty_user=self.user, campus=self.campus, term=self.term, generated_by_user=self.user)
        events = self.race(lambda: Access.record_generation(report=report), lambda: Access.record_generation(report=report))
        self.assertEqual(events[0].pk, events[1].pk)
        self.assertEqual(Deadline.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="CLEARANCE_LOGIN_SCHEDULED").count(), 1)

    def test_concurrent_manual_requests_reject_stale_version(self):
        def change():
            try:
                Access.set_manual(actor=self.admin, target_id=self.user.pk, disabled=True, expected_version=0, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
                return "changed"
            except StaleLoginControl:
                return "stale"
        self.assertCountEqual(self.race(change, change), ["changed", "stale"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.login_access_version, 1)
        self.assertEqual(AuditLog.objects.filter(action="DISABLE_LOGIN").count(), 1)

    def test_manual_enable_and_due_worker_do_not_rearm_old_event(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        self.manual(True)
        self.race(
            lambda: Access.process_user(self.user.pk),
            lambda: Access.set_manual(actor=self.admin, target_id=self.user.pk, disabled=False, expected_version=1, tenant_id=self.tenant.pk, campus_id=self.campus.pk),
        )
        self.user.refresh_from_db()
        event.refresh_from_db()
        self.assertFalse(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 2)
        self.assertEqual(event.state, "OVERRIDDEN")
        self.assertEqual(Access.process_due(), [])

    def test_two_workers_apply_once(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        barrier = Barrier(2)
        user_id = self.user.pk

        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return Access.process_user(user_id)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sum(len(rows) for rows in results), 1)
        event.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(event.state, "APPLIED")
        self.assertEqual(self.user.login_access_version, 1)
        self.assertEqual(AuditLog.objects.filter(action="AUTO_DISABLE_LOGIN").count(), 1)

    def test_policy_off_and_generation_leave_no_revivable_deadline(self):
        report = Reports.generate_report_record(faculty_user=self.user, campus=self.campus, term=self.term, generated_by_user=self.user)
        self.race(
            lambda: Access.record_generation(report=report),
            lambda: Access.save_automatic_policy(tenant_id=self.tenant.pk, enabled=False, delay_days=2, actor=self.admin),
        )
        event = Deadline.objects.get()
        self.assertEqual(event.state, "CANCELED")
        self.feature(True)
        self.assertEqual(Access.process_due(at=event.disable_at), [])

    def test_policy_off_and_due_worker_have_terminal_commit_order(self):
        event = self.event(at=timezone.now() - timedelta(hours=49))
        self.race(
            lambda: Access.process_user(self.user.pk),
            lambda: Access.save_automatic_policy(tenant_id=self.tenant.pk, enabled=False, delay_days=2, actor=self.admin),
        )
        event.refresh_from_db()
        self.user.refresh_from_db()
        self.assertIn(event.state, ("CANCELED", "APPLIED"))
        self.assertEqual(self.user.login_disabled, event.state == "APPLIED")
        self.feature(True)
        self.assertEqual(Access.process_due(), [])
