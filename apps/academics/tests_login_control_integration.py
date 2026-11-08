"""Integration boundaries between staging acceptance and login control."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext

from apps.accounts import login_obligations
from apps.accounts.login_control import LoginAccessService
from apps.academics import tests_administrative_acceptance as fixtures
from apps.academics.models import FacultyAssignment
from apps.auditlog.models import AuditLog
from apps.grading.models import FacultyFinalClearanceReport
from apps.grading.reporting import FacultyFinalClearanceReportService
from apps.rbac.models import Permission, Role, UserRole


class AcceptanceLoginControlConcurrencyTests(TransactionTestCase):
    def setUp(self):
        fixtures.AdministrativeFacultyAssignmentAcceptanceTests.setUp(self)
        from apps.faculty_attendance.models import AttendanceNoticeMutex
        AttendanceNoticeMutex.objects.get_or_create(campus=self.campus)
    _make_offering = fixtures.AdministrativeFacultyAssignmentAcceptanceTests._make_offering
    _make_assignment = fixtures.AdministrativeFacultyAssignmentAcceptanceTests._make_assignment
    _preview = fixtures.AdministrativeFacultyAssignmentAcceptanceTests._preview
    _execute = fixtures.AdministrativeFacultyAssignmentAcceptanceTests._execute

    @staticmethod
    def connection_call(fn):
        close_old_connections()
        try:
            if connection.vendor == "sqlite":
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA read_uncommitted = 1")
            return fn()
        finally:
            connection.close()

    def policy(self):
        LoginAccessService.save_automatic_policy(
            tenant_id=self.tenant.pk, enabled=False, delay_days=2, actor=self.actor,
        )

    def test_acceptance_locks_accounts_before_tenant_even_when_policy_is_off(self):
        report = self._preview()
        with CaptureQueriesContext(connection) as queries:
            self._execute(report=report)
        sql = [row["sql"].upper() for row in queries]
        if connection.vendor == "sqlite":
            account_lock = next(i for i, row in enumerate(sql) if row.startswith("UPDATE") and "LOGIN_ACCESS_VERSION" in row)
        else:
            account_lock = next(i for i, row in enumerate(sql) if "FOR UPDATE" in row and 'USERS' in row)
        tenant_read = next(i for i, row in enumerate(sql) if row.startswith("SELECT") and 'TENANTS' in row)
        self.assertLess(account_lock, tenant_read)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.ACCEPTED)

    def run_competing(self, first_name, kind="policy"):
        report = self._preview()
        held, release, attempted, completed = Event(), Event(), Event(), Event()
        original = login_obligations.lock_login_users

        if kind == "role":
            role = Role.objects.create(code="CONTINUING", name="Continuing role")
            grant = UserRole.objects.create(user=self.faculty, role=role, tenant=self.tenant,
                                            campus=self.campus, is_active=False)
        if kind == "disable":
            Permission.objects.get_or_create(code=LoginAccessService.PERMISSION,
                defaults={"module": "users", "action": "login_access.update", "is_active": True})
        if kind == "pdf":
            pdf_report = FacultyFinalClearanceReport.objects.create(
                tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
                term=self.term, faculty_user=self.faculty, generated_by_user=self.faculty,
                reference_no="FCR-INTEGRATION", verification_code="VERIFY-INTEGRATION",
                clearance_status="CLEARED", snapshot_json={"rows": []},
            )

        def guarded(ids, **kwargs):
            first = current_thread().name.startswith(first_name)
            if not first:
                attempted.set()
            result = original(ids, **kwargs)
            if first and not held.is_set():
                held.set()
                if not release.wait(10):
                    raise AssertionError("First transaction did not release its account lock")
            return result

        def acceptance():
            return self._execute(report=report)

        def competing_write():
            if kind == "policy":
                self.policy()
            elif kind == "role":
                grant.is_active = True
                grant.save(update_fields=["is_active"])
            elif kind == "disable":
                LoginAccessService.set_manual(actor=self.actor, target_id=self.faculty.pk, disabled=True,
                    expected_version=0, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
            else:
                from django.test import RequestFactory
                request = RequestFactory().post("/faculty/clearance/")
                request.user = self.faculty
                LoginAccessService.generate_clearance_pdf(report=pdf_report, access_version=0,
                    filename="report.pdf", request=request)
            return True

        first_fn, second_fn = (acceptance, competing_write) if first_name == "acceptance" else (competing_write, acceptance)
        second_name = kind if first_name == "acceptance" else "acceptance"
        def second():
            result = second_fn()
            completed.set()
            return result
        with patch("apps.accounts.login_obligations.lock_login_users", side_effect=guarded), \
                patch("apps.accounts.login_control.lock_login_users", side_effect=guarded), \
                patch.object(FacultyFinalClearanceReportService, "build_pdf_bytes", return_value=b"pdf"), \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix=first_name) as first_pool, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix=second_name) as second_pool:
            first = first_pool.submit(self.connection_call, first_fn)
            try:
                self.assertTrue(held.wait(10))
                other = second_pool.submit(self.connection_call, second)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(completed.wait(0.15))
            finally:
                release.set()
            first.result(timeout=15)
            other.result(timeout=15)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.ACCEPTED)
        self.assertFalse(LoginAccessService.automatic_enabled(self.tenant.pk))
        if kind == "policy":
            self.assertEqual(AuditLog.objects.filter(action="CLEARANCE_LOGIN_POLICY").count(), 1)
        elif kind == "role":
            grant.refresh_from_db()
            self.assertTrue(grant.is_active)
        elif kind == "disable":
            self.faculty.refresh_from_db()
            self.assertTrue(self.faculty.login_disabled)
        else:
            self.assertEqual(pdf_report.faculty_user.clearance_login_deadlines.get().state, "CANCELED")
        self.assertEqual(AuditLog.objects.filter(action="ADMINISTRATIVE_FACULTY_ASSIGNMENT_ACCEPTANCE").count(), 1)

    def test_nowait_retry_rolls_back_complete_root_unit(self):
        from django.db import OperationalError, transaction
        from apps.academics.administrative_acceptance import retry_acceptance_write
        from apps.accounts.models import User
        attempts = []
        before = self.faculty.first_name
        @retry_acceptance_write
        def operation():
            with transaction.atomic():
                self.assertEqual(User.objects.get(pk=self.faculty.pk).first_name, before)
                User.objects.filter(pk=self.faculty.pk).update(first_name="committed-once")
                attempts.append(1)
                if len(attempts) == 1:
                    raise OperationalError(3572, "NOWAIT row busy")
        with patch.object(connection, "vendor", "mysql"), \
                patch("apps.academics.administrative_acceptance.time.sleep") as sleep:
            operation()
        self.faculty.refresh_from_db()
        self.assertEqual(self.faculty.first_name, "committed-once")
        self.assertEqual(len(attempts), 2)
        sleep.assert_called_once()

    def test_nowait_failure_never_retries_inside_outer_transaction(self):
        from django.db import OperationalError, transaction
        from apps.academics.administrative_acceptance import retry_acceptance_write, AdministrativeAcceptanceError
        from apps.accounts.models import User
        attempts = []
        before = self.faculty.first_name
        @retry_acceptance_write
        def operation():
            with transaction.atomic():
                User.objects.filter(pk=self.faculty.pk).update(first_name="must-rollback")
                attempts.append(1)
                raise OperationalError(3572, "NOWAIT row busy")
        with transaction.atomic(), patch.object(connection, "vendor", "mysql"), \
                patch("apps.academics.administrative_acceptance.time.sleep") as sleep:
            with self.assertRaises(AdministrativeAcceptanceError):
                operation()
            self.assertEqual(User.objects.get(pk=self.faculty.pk).first_name, before)
        self.assertEqual(len(attempts), 1)
        sleep.assert_not_called()

    def test_database_without_nowait_fails_closed_before_row_locks(self):
        from apps.academics.administrative_acceptance import acceptance_lock_options, AdministrativeAcceptanceError
        with patch.object(connection.features, "has_select_for_update", True), \
                patch.object(connection.features, "has_select_for_update_nowait", False):
            with self.assertRaises(AdministrativeAcceptanceError):
                acceptance_lock_options()

    def test_repeatable_read_configuration_fails_closed(self):
        from apps.academics.administrative_acceptance import acceptance_lock_options, AdministrativeAcceptanceError
        with patch.object(connection, "vendor", "mysql"), \
                patch.object(connection, "isolation_level", "repeatable read", create=True):
            with self.assertRaises(AdministrativeAcceptanceError):
                acceptance_lock_options()

    def test_acceptance_locks_only_actor_and_term_participants(self):
        from apps.accounts.models import User
        unrelated = User.objects.create_user(username="unrelated-lock", email="unrelated-lock@example.test")
        report = self._preview()
        with patch("apps.accounts.login_obligations.lock_login_users", wraps=login_obligations.lock_login_users) as locks:
            self._execute(report=report)
        self.assertEqual(set(locks.call_args.args[0]), {self.actor.pk, self.faculty.pk})
        self.assertNotIn(unrelated.pk, locks.call_args.args[0])

    def test_all_acceptance_lock_reads_request_nowait_when_supported(self):
        from django.db.models.query import QuerySet
        original = QuerySet.select_for_update
        calls = []
        def record(qs, *args, **kwargs):
            calls.append(kwargs)
            return original(qs, *args, **kwargs)
        report = self._preview()
        with patch.object(connection.features, "has_select_for_update_nowait", True), \
                patch.object(QuerySet, "select_for_update", record):
            self._execute(report=report)
        self.assertTrue(calls)
        self.assertTrue(all(row.get("nowait") is True for row in calls), calls)

    def run_cutoff_competition(self, first_name, cross_tenant=False, sequential=False):
        from datetime import date
        from apps.accounts.models import User
        from apps.academics.models import AcademicYear, Term
        from apps.faculty_attendance import faculty_cutoffs
        from apps.faculty_attendance.models import AttendanceCutoffPublication
        from apps.faculty_attendance.permissions import PUBLISH_FACULTY_PERMISSION
        from apps.tenants.models import Tenant, Campus, Department
        # Two shared users with opposite ID/display ordering reproduce the old
        # cross-tenant account-order cycle. Publication performs its real review
        # and commits real publication/audit rows, rather than a stubbed review.
        second_faculty = User.objects.create_user(username="shared-second", email="shared-second@example.test",
            password="safe-test-password", first_name="A", default_tenant=self.tenant,
            default_campus=self.campus, default_department=self.department)
        self.faculty.first_name = "Z"
        self.faculty.save(update_fields=["first_name"])
        UserRole.objects.create(user=second_faculty, role=self.faculty_role, tenant=self.tenant, campus=self.campus, department=self.department)
        second_assignment = self._make_assignment(self._make_offering("SHARED"), faculty=second_faculty)
        if cross_tenant:
            tenant = Tenant.objects.create(code="CUT-OTHER", name="Other")
            campus = Campus.objects.create(tenant=tenant, code="CUT-OTHER", name="Other")
            department = Department.objects.create(tenant=tenant, campus=campus, code="CUT-OTHER", name="Other")
            year = AcademicYear.objects.create(tenant=tenant, code="CUT-OTHER", name="Other", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
            term = Term.objects.create(tenant=tenant, academic_year=year, code="CUT-OTHER", name="Other", start_date=date(2026, 1, 1), end_date=date(2026, 6, 30))
        else:
            tenant, campus, department, year, term = self.tenant, self.campus, self.department, self.academic_year, self.term
            # A valid Tuesday schedule creates no Monday teaching occurrence.
            # Retain dated coverage so acceptance does not create a new setup
            # reconciliation that would correctly invalidate the cutoff review.
            for offering in (self.offering, second_assignment.offering):
                offering.schedule_text = "T 08:00-09:00"
                offering.save(update_fields=["schedule_text"])
            from datetime import datetime
            from django.utils import timezone
            from apps.faculty_attendance.models import FacultyCoverage
            for assignment in (self.assignment, second_assignment):
                FacultyCoverage.objects.create(tenant=self.tenant, campus=self.campus, department=self.department,
                    offering=assignment.offering, faculty_user=assignment.faculty_user, source_assignment=assignment,
                    effective_from=timezone.make_aware(datetime(2026, 1, 1)), created_by=self.actor)
        ac = Role.objects.create(code="AC", name="Area chair")
        for user in (self.faculty, second_faculty):
            UserRole.objects.create(user=user, role=ac, tenant=tenant, campus=campus, department=department)
        Permission.objects.get_or_create(code=PUBLISH_FACULTY_PERMISSION, defaults={"module":"attendance", "action":"publish"})
        from apps.faculty_attendance.models import AttendanceNoticeMutex
        AttendanceNoticeMutex.objects.get_or_create(campus=campus)
        from apps.core.services.features import FeatureSettingsService
        from apps.core.services.settings import SystemSettingService
        SystemSettingService.set(FeatureSettingsService.FACULTY_ATTENDANCE_ENABLED_KEY, True,
                                 tenant_id=tenant.pk, value_type="BOOL")
        scope = dict(tenant_id=tenant.pk, campus_id=campus.pk, academic_year=year, term=term,
                     start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        preview = faculty_cutoffs.review_faculty_cutoff(actor=self.actor, **scope)
        fingerprints = {str(row.faculty.pk):row.fingerprint for row in preview.slices}
        report = self._preview()
        held, release, attempted = Event(), Event(), Event()
        original_lock = login_obligations.lock_login_users
        original_review = faculty_cutoffs.review_faculty_cutoff

        def accounts(ids, **kwargs):
            attempted.set()
            result = original_lock(ids, **kwargs)
            if first_name == "acceptance" and not held.is_set():
                held.set()
                if not release.wait(10):
                    raise AssertionError("acceptance lock not released")
            return result

        def review(**kwargs):
            if kwargs.get("lock"):
                attempted.set()
                if first_name == "publication" and not held.is_set():
                    # Emulate the existing joined user-lock acquisition at the
                    # worst point, inside the actual publication transaction.
                    User.objects.select_for_update().get(pk=second_faculty.pk)
                    held.set()
                    if not release.wait(10):
                        raise AssertionError("publication lock not released")
            return original_review(**kwargs)

        def publication():
            return faculty_cutoffs.publish_faculty_cutoffs(actor=self.actor, **scope,
                faculty_ids=[self.faculty.pk, second_faculty.pk], expected_fingerprints=fingerprints,
                submission_key="real-cutoff-lock")
        def acceptance():
            return self._execute(report=report)
        first_fn, second_fn = (acceptance, publication) if first_name == "acceptance" else (publication, acceptance)
        if sequential:
            first_fn()
            second_fn()
            self.assertEqual(AttendanceCutoffPublication.objects.filter(submission_key__startswith="real-cutoff-lock:").count(), 2)
            return
        with patch("apps.accounts.login_obligations.lock_login_users", side_effect=accounts), \
                patch.object(faculty_cutoffs, "review_faculty_cutoff", side_effect=review), \
                ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.connection_call, first_fn)
            try:
                self.assertTrue(held.wait(10))
                attempted.clear()
                other = pool.submit(self.connection_call, second_fn)
                self.assertTrue(attempted.wait(10))
                # Let acceptance encounter NOWAIT and restart its whole unit.
                release.wait(0.15)
            finally:
                release.set()
            first.result(timeout=20)
            other.result(timeout=20)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.ACCEPTED)
        self.assertEqual(AttendanceCutoffPublication.objects.filter(submission_key__startswith="real-cutoff-lock:").count(), 2)
        self.assertEqual(AuditLog.objects.filter(action="ADMINISTRATIVE_FACULTY_ASSIGNMENT_ACCEPTANCE").count(), 2)
        self.assertFalse(LoginAccessService.automatic_enabled(self.tenant.pk))

    def test_same_tenant_real_publication_and_acceptance_fixture(self):
        self.run_cutoff_competition("publication", sequential=True)

    def test_cross_tenant_shared_user_publication_and_acceptance_fixture(self):
        self.run_cutoff_competition("publication", cross_tenant=True, sequential=True)

    def test_same_tenant_acceptance_then_real_publication_fixture(self):
        self.run_cutoff_competition("acceptance", sequential=True)

    def test_cross_tenant_acceptance_then_real_publication_fixture(self):
        self.run_cutoff_competition("acceptance", cross_tenant=True, sequential=True)

    @skipUnlessDBFeature("has_select_for_update_nowait")
    def test_acceptance_before_cutoff_publication_serializes(self):
        self.run_cutoff_competition("acceptance")

    @skipUnlessDBFeature("has_select_for_update_nowait")
    def test_cutoff_publication_before_acceptance_serializes(self):
        self.run_cutoff_competition("publication")

    @skipUnlessDBFeature("has_select_for_update_nowait")
    def test_acceptance_before_other_tenant_cutoff_with_shared_users(self):
        self.run_cutoff_competition("acceptance", cross_tenant=True)

    @skipUnlessDBFeature("has_select_for_update_nowait")
    def test_other_tenant_cutoff_before_acceptance_with_shared_users(self):
        self.run_cutoff_competition("publication", cross_tenant=True)

    def test_acceptance_before_policy_serializes_without_deadlock(self):
        self.run_competing("acceptance")

    def test_policy_before_acceptance_serializes_without_deadlock(self):
        self.run_competing("policy")

    def test_acceptance_before_clearance_pdf_serializes_while_off(self):
        self.run_competing("acceptance", "pdf")

    def test_clearance_pdf_before_acceptance_serializes_while_off(self):
        self.run_competing("pdf", "pdf")

    def test_acceptance_before_role_activation_serializes(self):
        self.run_competing("acceptance", "role")

    def test_role_activation_before_acceptance_serializes(self):
        self.run_competing("role", "role")

    def test_acceptance_before_manual_disable_serializes(self):
        self.run_competing("acceptance", "disable")

    def test_manual_disable_before_acceptance_serializes(self):
        self.run_competing("disable", "disable")
