"""Independent-review regressions; use only disposable test databases."""
from datetime import timedelta
from importlib import import_module
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from threading import Event, current_thread
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import authenticate
from django.contrib.auth.forms import SetPasswordForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.login_control import LoginAccessService as Access
from apps.accounts.login_obligations import LoginObligationQuerySet
from apps.accounts.models import User
from apps.accounts.faculty_provisioning import ScopedUserRoleAssignmentService
from apps.accounts.forms import FacultySelfChangePasswordForm, AdminSelfChangePasswordForm
from apps.accounts.tests_login_control import LoginFixture
from apps.academics.models import CourseOffering, FacultyAssignment, Term
from apps.admin_portal.forms import UserUpdateForm
from apps.rbac.models import Permission, Role, RolePermission, UserRole, UserPermission
from apps.tenants.models import Tenant, Campus


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ReviewRegressionTests(LoginFixture, TestCase):
    def next_loading(self, *, active=True):
        term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year,
            code="NEXT-REVIEW", name="Next term", sequence_no=self.term.sequence_no + 1)
        offering = CourseOffering.objects.create(tenant=self.tenant, campus=self.campus,
            department=self.department, program=self.program, academic_year=self.academic_year,
            term=term, course=self.course, section=self.section)
        return FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus,
            offering=offering, faculty_user=self.user, is_active=active,
            response_status="ACCEPTED", accepted_at=timezone.now())

    def test_stale_account_form_cannot_restore_disabled_access(self):
        stale = User.objects.get(pk=self.user.pk)
        data = {field: getattr(stale, field) for field in ("email", "first_name", "middle_name", "last_name", "is_active", "is_staff")}
        data.update(default_tenant=self.tenant.pk, default_campus=self.campus.pk,
            default_department=self.department.pk, first_name="Edited after disable")
        form = UserUpdateForm(data=data, instance=stale)
        self.assertTrue(form.is_valid(), form.errors)
        self.manual(True)
        form.save()
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 1)
        self.assertEqual(self.user.first_name, "Edited after disable")

    def test_stale_inherited_password_form_preserves_revocation(self):
        stale = User.objects.get(pk=self.user.pk)
        form = SetPasswordForm(stale, {"new_password1": "ChangedPass789!", "new_password2": "ChangedPass789!"})
        self.assertTrue(form.is_valid(), form.errors)
        self.manual(True)
        form.save()
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 1)
        self.assertTrue(self.user.check_password("ChangedPass789!"))
        self.assertIsNone(authenticate(username=self.user.username, password="ChangedPass789!"))

    def test_later_same_campus_obligation_exempts_scheduling(self):
        self.next_loading()
        event = self.event()
        self.assertEqual(event.state, "EXEMPT")
        self.assertIn("term", event.reason)

    def test_later_same_campus_obligation_exempts_application(self):
        event = self.event(at=timezone.now() - timedelta(days=3))
        self.next_loading()
        Access.process_due()
        event.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(event.state, "EXEMPT")
        self.assertFalse(self.user.login_disabled)

    def test_seed_grants_established_super_admin_role(self):
        role, _ = Role.objects.get_or_create(code="SUPER_ADMIN", defaults={"name": "Super Admin"})
        permission = Permission.objects.get(code=Access.PERMISSION)
        RolePermission.objects.filter(role=role, permission=permission).delete()
        seed = import_module("apps.rbac.migrations.0037_seed_login_access_permission")
        seed.seed_permission(apps, None)
        self.assertTrue(RolePermission.objects.filter(role=role, permission=permission).exists())

    def test_stale_save_cannot_resurrect_unregistered_legacy_session(self):
        stale = User.objects.get(pk=self.user.pk)
        self.client.force_login(stale, backend="django.contrib.auth.backends.ModelBackend")
        cookie = self.client.cookies["sessionid"].value
        self.manual(True)
        self.manual(False)
        stale.first_name = "Ordinary edit"
        stale.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.login_access_version, 2)
        self.assertFalse(self.user.login_disabled)
        self.client.cookies["sessionid"] = cookie
        self.assertEqual(self.client.get(self.pdf_url).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(stale.login_access_version, 2)

    def test_stale_disabled_instance_cannot_undo_manual_enable(self):
        self.manual(True)
        stale = User.objects.get(pk=self.user.pk)
        self.manual(False)
        stale.last_name = "Still editable"
        stale.save()
        self.user.refresh_from_db()
        self.assertFalse(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 2)
        self.assertEqual(self.user.last_name, "Still editable")

    def test_explicit_protected_save_cannot_decrease_version(self):
        self.manual(True)
        stale = User.objects.get(pk=self.user.pk)
        stale.login_disabled, stale.login_access_version = False, 0
        stale.save(update_fields=["login_disabled", "login_access_version"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 1)
        self.assertFalse(User._meta.get_field("login_disabled").editable)
        self.assertFalse(User._meta.get_field("login_access_version").editable)

    def test_both_self_password_forms_keep_monotonic_versions(self):
        password = "testpass123"
        for index, cls in enumerate((FacultySelfChangePasswordForm, AdminSelfChangePasswordForm), 1):
            stale = User.objects.get(pk=self.user.pk)
            new_password = f"ReviewChangedPass{index}!"
            form = cls(stale, {"old_password": password, "new_password1": new_password, "new_password2": new_password})
            self.assertTrue(form.is_valid(), form.errors)
            self.manual(True)
            self.manual(False)
            form.save()
            self.user.refresh_from_db()
            self.assertEqual(self.user.login_access_version, index * 2)
            self.assertFalse(self.user.login_disabled)
            self.assertTrue(self.user.check_password(new_password))
            self.assertIsNotNone(authenticate(username=self.user.username, password=new_password))
            password = new_password

    def test_deferred_user_save_keeps_password_and_independent_state(self):
        stale = User.objects.only("pk", "first_name").get(pk=self.user.pk)
        self.manual(True)
        stale.first_name = "Deferred edit"
        stale.save()
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 1)
        self.assertTrue(self.user.check_password("testpass123"))

    def test_reconstructed_existing_pk_cannot_overwrite_independent_state(self):
        stale = User(**{field.attname: getattr(self.user, field.attname) for field in User._meta.concrete_fields})
        self.assertTrue(stale._state.adding)
        self.manual(True)
        stale.first_name = "Reconstructed account edit"
        stale.save()
        self.user.refresh_from_db()
        self.assertTrue(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 1)
        self.assertEqual(stale.login_access_version, 1)
        self.assertEqual(self.user.first_name, "Reconstructed account edit")

    def test_normal_user_insert_and_clone_semantics_remain_available(self):
        clone = User.objects.get(pk=self.user.pk)
        clone.pk = None
        clone.username = "cloned-review-user"
        clone.email = "cloned-review-user@example.edu"
        clone.save()
        self.assertNotEqual(clone.pk, self.user.pk)
        self.assertTrue(clone.check_password("testpass123"))
        self.assertFalse(clone.login_disabled)
        self.assertEqual(clone.login_access_version, 0)

    def test_only_cleared_cycle_loading_does_not_create_exception(self):
        self.assertEqual(self.event().state, "PENDING")

    def test_inactive_or_closed_next_term_loading_is_not_an_obligation(self):
        assignment = self.next_loading(active=False)
        self.assertEqual(self.event().state, "PENDING")
        assignment.is_active = True
        assignment.save(update_fields=["is_active"])
        assignment.offering.status = "CLOSED"
        assignment.offering.save(update_fields=["status"])
        self.assertEqual(Access.exception_reason(self.user, self.campus.pk, self.term.pk), "")

    def test_bulk_and_update_or_create_activation_use_the_same_account_guard(self):
        assignment = self.next_loading(active=False)
        event = self.event(at=timezone.now() - timedelta(days=3))
        assignment.is_active = True
        FacultyAssignment.objects.bulk_update([assignment], ["is_active"])
        Access.process_due()
        event.refresh_from_db()
        self.assertEqual(event.state, "EXEMPT")
        role = Role.objects.create(code="REVIEW-ROLE", name="Review role")
        grant = UserRole.objects.create(user=self.user, role=role, tenant=self.tenant, campus=self.campus, is_active=False)
        UserRole.objects.update_or_create(pk=grant.pk, defaults={"is_active": True})
        self.assertIn("role", Access.exception_reason(self.user, self.campus.pk, self.term.pk))

    def test_manual_override_locks_tenant_before_updating_deadlines(self):
        self.event()
        self.manual(True)
        with CaptureQueriesContext(connection) as queries:
            self.manual(False)
        sql = [row["sql"].upper() for row in queries.captured_queries]
        tenant_reads = [i for i, query in enumerate(sql) if query.startswith("SELECT") and 'FROM "TENANTS"' in query]
        deadline_updates = [i for i, query in enumerate(sql) if query.startswith('UPDATE "CLEARANCE_LOGIN_DEADLINES"')]
        self.assertTrue(tenant_reads, "Manual transitions must acquire tenant locks before deadline locks")
        self.assertTrue(deadline_updates)
        self.assertLess(min(tenant_reads), min(deadline_updates))

    def test_ordinary_super_admin_grant_scope_and_direct_deny(self):
        role = Role.objects.create(code="SUPER_ADMIN", name="Super Admin")
        seed = import_module("apps.rbac.migrations.0037_seed_login_access_permission")
        seed.seed_permission(apps, None)
        seed.seed_permission(apps, None)
        RolePermission.objects.get_or_create(role=role, permission=Permission.objects.get(code="admin_portal.access"))
        actor = User.objects.create_user(username="ordinary-superadmin", email="ordinary-superadmin@example.edu",
            password="ReviewPassword!", default_tenant=self.tenant, default_campus=self.campus,
            privacy_consent_at=timezone.now(), privacy_consent_version=self.admin.privacy_consent_version)
        self.assertFalse(actor.is_superuser)
        UserRole.objects.create(user=actor, role=role, tenant=self.tenant, campus=self.campus)
        self.manual(True, actor=actor)
        self.manual(False, actor=actor)
        tenant = Tenant.objects.create(code="OTHER-REVIEW", name="Other")
        campus = Campus.objects.create(tenant=tenant, code="OTHER", name="Other")
        self.assertFalse(Access.has_management_permission(actor, tenant.pk, campus.pk))
        UserPermission.objects.create(user=actor, permission=Permission.objects.get(code=Access.PERMISSION),
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        seed.seed_permission(apps, None)
        with self.assertRaises(PermissionDenied):
            self.manual(True, actor=actor)

    def test_seed_does_not_grant_unsupported_alias_or_inactive_roles(self):
        alias = Role.objects.create(code="SUPERADMIN", name="Unsupported alias")
        inactive = Role.objects.create(code="SUPER_ADMIN", name="Inactive Super Admin", is_active=False)
        seed = import_module("apps.rbac.migrations.0037_seed_login_access_permission")
        seed.seed_permission(apps, None)
        self.assertFalse(RolePermission.objects.filter(role__in=[alias, inactive], permission__code=Access.PERMISSION).exists())


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class FreshLoginPermissionTests(TestCase):
    def test_fresh_seed_after_migrations_grants_both_real_roles(self):
        for code in ("SUPER_ADMIN", "CAMPUS_ADMIN"):
            role = Role.objects.filter(code=code).first()
            if role:
                RolePermission.objects.filter(role=role).delete()
                role.delete()
        call_command("seed_stage_0_1", stdout=StringIO())
        for code in ("SUPER_ADMIN", "CAMPUS_ADMIN"):
            self.assertTrue(RolePermission.objects.filter(role__code=code, permission__code=Access.PERMISSION).exists())
        self.assertFalse(Role.objects.filter(code="SUPERADMIN").exists())


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ReviewConcurrencyTests(LoginFixture, TransactionTestCase):
    next_loading = ReviewRegressionTests.next_loading

    @staticmethod
    def in_connection(fn):
        close_old_connections()
        try:
            if connection.vendor == "sqlite":
                # Make a pre-fix read/check/write race observable. The fixed
                # explicit account writer fence still blocks the competing write.
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA read_uncommitted = 1")
            return fn()
        finally:
            connection.close()

    def run_stale_writer(self, prepare, transition):
        loaded, release = Event(), Event()
        def write():
            save = prepare(User.objects.get(pk=self.user.pk))
            loaded.set()
            if not release.wait(10):
                raise AssertionError("Stale writer was not released")
            save()
        with ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(self.in_connection, write)
            try:
                self.assertTrue(loaded.wait(10))
                transition()
            finally:
                release.set()
            writer.result(timeout=15)
        self.user.refresh_from_db()

    def test_concurrent_account_form_preserves_manual_disable(self):
        def prepare(stale):
            data = {field: getattr(stale, field) for field in ("email", "first_name", "middle_name", "last_name", "is_active", "is_staff")}
            data.update(default_tenant=self.tenant.pk, default_campus=self.campus.pk,
                default_department=self.department.pk, first_name="Concurrent account edit")
            form = UserUpdateForm(data=data, instance=stale)
            if not form.is_valid():
                raise AssertionError(form.errors)
            return form.save
        self.run_stale_writer(prepare, lambda: self.manual(True))
        self.assertTrue(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 1)
        self.assertEqual(self.user.first_name, "Concurrent account edit")

    def test_concurrent_password_reset_preserves_disable_enable_epoch(self):
        def prepare(stale):
            form = SetPasswordForm(stale, {"new_password1": "ThreadPassword789!", "new_password2": "ThreadPassword789!"})
            if not form.is_valid():
                raise AssertionError(form.errors)
            return form.save
        self.run_stale_writer(prepare, lambda: (self.manual(True), self.manual(False)))
        self.assertFalse(self.user.login_disabled)
        self.assertEqual(self.user.login_access_version, 2)
        self.assertTrue(self.user.check_password("ThreadPassword789!"))

    def test_concurrent_profile_save_never_revives_legacy_cookie(self):
        self.client.force_login(self.user, backend="django.contrib.auth.backends.ModelBackend")
        cookie = self.client.cookies["sessionid"].value
        def prepare(stale):
            stale.first_name = "Concurrent legacy edit"
            return stale.save
        self.run_stale_writer(prepare, lambda: (self.manual(True), self.manual(False)))
        self.assertEqual(self.user.login_access_version, 2)
        self.client.cookies["sessionid"] = cookie
        self.assertEqual(self.client.get(self.pdf_url).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_assignment_transfer_while_waiting_rejects_unlocked_new_owner(self):
        assignment = self.next_loading(active=False)
        replacement = User.objects.create_user(username="transferred-review", email="transferred-review@example.edu")
        snapshot, release = Event(), Event()
        original = LoginObligationQuerySet._subject_ids
        def subjects(queryset, **kwargs):
            ids = original(queryset, **kwargs)
            if (queryset.model is FacultyAssignment and current_thread().name.startswith("stale-activation")
                    and not snapshot.is_set()):
                snapshot.set()
                if not release.wait(10):
                    raise AssertionError("Activation was not released")
            return ids
        def activate():
            assignment.is_active = True
            assignment.save(update_fields=["is_active"])
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale-activation") as pool, patch.object(
                LoginObligationQuerySet, "_subject_ids", subjects):
            writer = pool.submit(self.in_connection, activate)
            try:
                self.assertTrue(snapshot.wait(10))
                moved = FacultyAssignment.objects.get(pk=assignment.pk)
                moved.faculty_user = replacement
                moved.save(update_fields=["faculty_user"])
            finally:
                release.set()
            with self.assertRaisesMessage(ValidationError, "assigned account changed concurrently"):
                writer.result(timeout=15)
        assignment.refresh_from_db()
        self.assertEqual(assignment.faculty_user_id, replacement.pk)
        self.assertFalse(assignment.is_active)

    def assert_activation_cannot_commit_between_check_and_disable(self, activate):
        event = self.event(at=timezone.now() - timedelta(days=3))
        checked, release, attempted, committed = Event(), Event(), Event(), Event()
        original = Access.exception_reason
        def reason(*args, **kwargs):
            result = original(*args, **kwargs)
            checked.set()
            if not release.wait(10):
                raise AssertionError("Worker was not released")
            return result
        def write():
            attempted.set()
            activate()
            committed.set()
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(Access, "exception_reason", side_effect=reason):
            worker = pool.submit(self.in_connection, lambda: Access.process_user(self.user.pk))
            try:
                self.assertTrue(checked.wait(10))
                writer = pool.submit(self.in_connection, write)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(committed.wait(0.2), "Activation committed after the worker's exception read")
            finally:
                release.set()
            worker.result(timeout=15)
            writer.result(timeout=15)
        event.refresh_from_db()
        self.user.refresh_from_db()
        # The worker committed first. Later provisioning never silently enables
        # a disabled login; MIS's independent manual control remains necessary.
        self.assertEqual(event.state, "APPLIED")
        self.assertTrue(self.user.login_disabled)
        self.assertTrue(committed.is_set())

    def test_role_reactivation_cannot_commit_between_exception_read_and_update(self):
        role = Role.objects.create(code="REACTIVATE-REVIEW", name="Other role")
        UserRole.objects.create(user=self.user, role=role, tenant=self.tenant, campus=self.campus, is_active=False)
        Permission.objects.get_or_create(code="user_roles.update", defaults={"module": "user_roles", "action": "update"})
        self.assert_activation_cannot_commit_between_check_and_disable(lambda: ScopedUserRoleAssignmentService.assign(
            actor=self.admin, user=self.user, role=role, tenant=self.tenant, campus=self.campus,
            department=None, permission_code="user_roles.update"))

    def test_assignment_reactivation_cannot_commit_between_check_and_update(self):
        assignment = self.next_loading(active=False)
        def activate():
            assignment.is_active = True
            assignment.save(update_fields=["is_active"])
        self.assert_activation_cannot_commit_between_check_and_disable(activate)

    def test_queryset_role_activation_cannot_bypass_serialization(self):
        role = Role.objects.create(code="QUERYSET-REVIEW", name="Other role")
        grant = UserRole.objects.create(user=self.user, role=role, tenant=self.tenant, campus=self.campus, is_active=False)
        self.assert_activation_cannot_commit_between_check_and_disable(lambda: UserRole.objects.filter(pk=grant.pk).update(is_active=True))

    def test_global_role_activation_cannot_bypass_serialization(self):
        role = Role.objects.create(code="GLOBAL-REVIEW", name="Other role", is_active=False)
        UserRole.objects.create(user=self.user, role=role, tenant=self.tenant, campus=self.campus)
        def activate():
            role.is_active = True
            role.save(update_fields=["is_active"])
        self.assert_activation_cannot_commit_between_check_and_disable(activate)

    def test_offering_reopening_cannot_bypass_serialization(self):
        assignment = self.next_loading()
        assignment.offering.status = "CLOSED"
        assignment.offering.save(update_fields=["status"])
        def activate():
            assignment.offering.status = "OPEN"
            assignment.offering.save(update_fields=["status"])
        self.assert_activation_cannot_commit_between_check_and_disable(activate)

    def test_worker_waits_for_role_activation_then_observes_exception(self):
        event = self.event(at=timezone.now() - timedelta(days=3))
        role = Role.objects.create(code="WRITER-FIRST", name="Other role")
        grant = UserRole.objects.create(user=self.user, role=role, tenant=self.tenant, campus=self.campus, is_active=False)
        changed, release, checked = Event(), Event(), Event()
        original = Access.exception_reason
        def activate():
            with transaction.atomic():
                grant.is_active = True
                grant.save(update_fields=["is_active"])
                changed.set()
                if not release.wait(10):
                    raise AssertionError("Writer was not released")
        def reason(*args, **kwargs):
            checked.set()
            return original(*args, **kwargs)
        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(Access, "exception_reason", side_effect=reason):
            writer = pool.submit(self.in_connection, activate)
            try:
                self.assertTrue(changed.wait(10))
                worker = pool.submit(self.in_connection, lambda: Access.process_user(self.user.pk))
                self.assertFalse(checked.wait(0.2))
            finally:
                release.set()
            writer.result(timeout=15)
            worker.result(timeout=15)
        event.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(event.state, "EXEMPT")
        self.assertFalse(self.user.login_disabled)


class LoginPermissionMigrationTests(TransactionTestCase):
    def test_real_migration_grants_existing_roles_and_preserves_deny(self):
        executor = MigrationExecutor(connection)
        executor.migrate([("rbac", "0036_seed_planning_readiness_permissions")])
        role, _ = Role.objects.get_or_create(code="SUPER_ADMIN", defaults={"name": "Super Admin"})
        permission, _ = Permission.objects.get_or_create(code=Access.PERMISSION,
            defaults={"module": "users", "action": "login_access.update"})
        RolePermission.objects.filter(role=role, permission=permission).delete()
        user = User.objects.create_user(username="migration-admin", email="migration-admin@example.edu", password="TestPass123!")
        deny = UserPermission.objects.create(user=user, permission=permission, grant_type="DENY")
        try:
            MigrationExecutor(connection).migrate([("rbac", "0037_seed_login_access_permission")])
            self.assertTrue(RolePermission.objects.filter(role=role, permission=permission).exists())
            self.assertTrue(UserPermission.objects.filter(pk=deny.pk, grant_type="DENY").exists())
        finally:
            MigrationExecutor(connection).migrate([("rbac", "0037_seed_login_access_permission")])


class HistoricalCycleUpgradeTests(LoginFixture, TransactionTestCase):
    def test_upgrade_captures_old_report_cycles_without_asserting_pdf_success(self):
        from django.db.migrations.executor import MigrationExecutor
        from apps.accounts.models import HistoricalClearanceCycle, ClearanceLoginDeadline as Deadline
        from apps.grading.reporting import FacultyFinalClearanceReportService
        old = [("accounts", "0014_protect_login_control_fields")]
        new = [("accounts", "0015_historical_clearance_cycles")]
        MigrationExecutor(connection).migrate(old)
        try:
            FacultyFinalClearanceReportService.generate_report_record(
                faculty_user=self.user, campus=self.campus, term=self.term, generated_by_user=self.user)
            MigrationExecutor(connection).migrate(new)
            self.assertTrue(HistoricalClearanceCycle.objects.filter(
                user=self.user, tenant=self.tenant, campus=self.campus, term=self.term).exists())
            self.assertFalse(Deadline.objects.filter(user=self.user).exists())
        finally:
            MigrationExecutor(connection).migrate(new)

    def test_legacy_generation_drained_before_idempotent_snapshot(self):
        from types import SimpleNamespace
        from apps.accounts.models import HistoricalClearanceCycle
        from apps.grading.models import FacultyFinalClearanceReport
        from apps.grading.reporting import FacultyFinalClearanceReportService
        from apps.accounts.login_control import LoginAccessService
        from apps.accounts.models import ClearanceLoginDeadline
        self.feature(False)
        ready, drain = Event(), Event()
        def legacy_generation():
            close_old_connections()
            try:
                report = FacultyFinalClearanceReportService.generate_report_record(
                    faculty_user=self.user, campus=self.campus, term=self.term, generated_by_user=self.user)
                ready.set()
                if not drain.wait(10):
                    raise AssertionError("legacy request was not drained")
                return report.pk
            finally:
                connection.close()
        capture = import_module("apps.accounts.migrations.0015_historical_clearance_cycles").capture_legacy_cycles
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(legacy_generation)
            try:
                self.assertTrue(ready.wait(10))
                self.assertFalse(HistoricalClearanceCycle.objects.exists())
            finally:
                drain.set()
            report_id = future.result(timeout=15)
        # Operations must close admission and drain old writers BEFORE taking
        # the snapshot. This test models that boundary, not a deployed gate.
        capture(apps, SimpleNamespace(connection=connection))
        capture(apps, SimpleNamespace(connection=connection))
        self.assertEqual(HistoricalClearanceCycle.objects.filter(user=self.user, term=self.term).count(), 1)
        self.feature(True)
        report = FacultyFinalClearanceReport.objects.get(pk=report_id)
        marker = LoginAccessService.record_generation(report=report)
        self.assertEqual(marker.state, "EXEMPT")
        self.assertFalse(ClearanceLoginDeadline.objects.filter(state="PENDING").exists())
