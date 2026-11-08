"""Full provisioning must own its audit actor before inserting any rows."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, close_old_connections, connection, transaction
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.faculty_provisioning import (
    FacultyAccountProvisioningService as Provisioning,
    ScopedUserRoleAssignmentService as Assignment,
)
from apps.accounts.login_obligations import lock_login_users
from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.core.services.permissions import PermissionService
from apps.rbac.models import Permission, Role, UserRole
from apps.tenants.models import Campus, Department, Tenant


class FacultyProvisioningLockTests(TransactionTestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(
            username="provision-lock-admin", email="admin@ncba.edu.ph", password=None,
        )
        self.tenant = Tenant.objects.create(code="PROVISION", name="Provisioning")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.department = Department.objects.create(
            tenant=self.tenant, campus=self.campus, code="COLLEGE", name="College",
        )
        self.role, _ = Role.objects.update_or_create(
            code="FACULTY", defaults={"name": "Faculty", "is_active": True},
        )
        Permission.objects.get_or_create(
            code=Provisioning.IMPORT_PERMISSION, defaults={"module": "faculty_users", "action": "import"},
        )

    def provision(self, username="new-faculty"):
        return Provisioning.provision(
            actor=self.actor, tenant=self.tenant, campus=self.campus, department=self.department,
            first_name=" New ", middle_name=" Faculty ", last_name=" User ",
            email=f"{username}@ncba.edu.ph", username=username,
            import_batch_id=71, import_row_number=2,
        )

    def assert_provisioned(self, result):
        user = User.objects.get(pk=result.user.pk)
        self.assertTrue(result.user_created)
        self.assertTrue(result.role_created)
        self.assertFalse(user.is_active)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.login_disabled)
        self.assertEqual(user.login_access_version, 0)
        self.assertEqual((user.first_name, user.middle_name, user.last_name), ("New", "Faculty", "User"))
        self.assertEqual(
            (user.default_tenant_id, user.default_campus_id, user.default_department_id),
            (self.tenant.pk, self.campus.pk, self.department.pk),
        )
        grant = UserRole.objects.get(user=user)
        self.assertEqual(result.role_assignment.pk, grant.pk)
        self.assertTrue(grant.is_active)
        self.assertEqual(
            (grant.role_id, grant.tenant_id, grant.campus_id, grant.department_id),
            (self.role.pk, self.tenant.pk, self.campus.pk, self.department.pk),
        )
        for action, entity, entity_id in (
            ("FACULTY_USER_CREATED", "User", user.pk),
            ("FACULTY_ROLE_ASSIGNED", "UserRole", grant.pk),
        ):
            audit = AuditLog.objects.get(action=action, entity_type=entity, entity_id=str(entity_id))
            self.assertEqual((audit.actor_user_id, audit.tenant_id, audit.campus_id, audit.portal),
                (self.actor.pk, self.tenant.pk, self.campus.pk, "ADMIN"))
            self.assertEqual(audit.metadata_json["import_batch_id"], 71)
            self.assertEqual(audit.metadata_json["import_row_number"], 2)

    def test_actor_lock_precedes_account_and_audit_inserts_without_relocking_actor(self):
        with CaptureQueriesContext(connection) as queries, patch(
            "apps.accounts.faculty_provisioning.lock_login_users", wraps=lock_login_users,
        ) as locks:
            result = self.provision()
        sql = [query["sql"].upper() for query in queries]
        account_insert = next(i for i, statement in enumerate(sql)
            if statement.startswith("INSERT INTO") and User._meta.db_table.upper() in statement)
        audit_insert = next(i for i, statement in enumerate(sql)
            if statement.startswith("INSERT INTO") and AuditLog._meta.db_table.upper() in statement)
        if connection.vendor == "sqlite":
            actor_lock = next(i for i, statement in enumerate(sql)
                if statement.startswith("UPDATE") and "LOGIN_ACCESS_VERSION" in statement)
        else:
            actor_lock = next(i for i, statement in enumerate(sql) if "FOR UPDATE" in statement)
        self.assertLess(actor_lock, account_insert)
        self.assertLess(actor_lock, audit_insert)
        locks.assert_called_once_with([self.actor.pk])
        self.assert_provisioned(result)
        self.assertEqual(AuditLog.objects.count(), 2)

    @staticmethod
    def in_connection(fn):
        close_old_connections()
        try:
            if connection.vendor == "sqlite":
                # Allow competing validation reads; the actual writer fence
                # must still serialize insertion, even with dirty reads allowed.
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA read_uncommitted = 1")
            return fn()
        finally:
            connection.close()

    def competing_calls(self, *, duplicate=False):
        held, release, attempted, second_insert = Event(), Event(), Event(), Event()

        def lock(ids, **kwargs):
            second = current_thread().name.startswith("second-provision")
            if second:
                attempted.set()
            rows = lock_login_users(ids, **kwargs)
            if not second and not held.is_set():
                held.set()
                if not release.wait(10):
                    raise AssertionError("First provisioning transaction was not released")
            return rows

        def second():
            def observe(execute, sql, params, many, context):
                if sql.upper().startswith("INSERT INTO"):
                    second_insert.set()
                return execute(sql, params, many, context)
            with connection.execute_wrapper(observe):
                return self.provision("first-faculty" if duplicate else "second-faculty")

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="first-provision") as first_pool, \
                ThreadPoolExecutor(max_workers=1, thread_name_prefix="second-provision") as second_pool, \
                patch("apps.accounts.faculty_provisioning.lock_login_users", side_effect=lock):
            first = first_pool.submit(self.in_connection, lambda: self.provision("first-faculty"))
            try:
                self.assertTrue(held.wait(10), "First provisioning never reached its actor lock")
                other = second_pool.submit(self.in_connection, second)
                self.assertTrue(attempted.wait(10), "Competing provisioning never attempted the actor lock")
                self.assertFalse(second_insert.wait(0.15), "Competing insertion passed the held actor lock")
            finally:
                release.set()
            first_result = first.result(timeout=15)
            if duplicate:
                with self.assertRaisesMessage(ValidationError, "already assigned"):
                    other.result(timeout=15)
                return [first_result]
            return [first_result, other.result(timeout=15)]

    def test_competing_provisioning_same_actor_serializes_complete_accounts_and_audits(self):
        results = self.competing_calls()
        self.assertEqual(len({result.user.pk for result in results}), 2)
        for result in results:
            self.assert_provisioned(result)
        self.assertEqual(User.objects.count(), 3)
        self.assertEqual(UserRole.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), 4)
        self.actor.refresh_from_db()
        self.assertFalse(self.actor.login_disabled)
        self.assertEqual(self.actor.login_access_version, 0)

    def test_competing_duplicate_provisioning_leaves_one_complete_account(self):
        result, = self.competing_calls(duplicate=True)
        self.assert_provisioned(result)
        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(UserRole.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_assignment_authorization_failure_rolls_back_account_and_creation_audit(self):
        with patch.object(PermissionService, "has_permission", side_effect=[True, False]):
            with self.assertRaises(PermissionDenied):
                self.provision()
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(UserRole.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_outer_transaction_rollback_owns_complete_provisioning_unit(self):
        with self.assertRaisesMessage(RuntimeError, "outer rollback"):
            with transaction.atomic():
                self.assert_provisioned(self.provision())
                raise RuntimeError("outer rollback")
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(UserRole.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_standalone_role_assignment_still_locks_actor_and_existing_target(self):
        target = User.objects.create_user(username="existing-faculty", email="existing@ncba.edu.ph")
        with patch("apps.accounts.faculty_provisioning.lock_login_users", wraps=lock_login_users) as locks:
            result = Assignment.assign(
                actor=self.actor, user=target, role=self.role, tenant=self.tenant,
                campus=self.campus, department=self.department,
                permission_code=Provisioning.IMPORT_PERMISSION,
            )
        locks.assert_called_once_with([target.pk, self.actor.pk])
        self.assertTrue(result.created)
        self.assertEqual(result.assignment.user_id, target.pk)
        self.assertEqual(AuditLog.objects.get().action, "FACULTY_ROLE_ASSIGNED")

    def test_sqlite_retry_after_creation_audit_retries_whole_unit_without_duplicates(self):
        if connection.vendor != "sqlite":
            self.skipTest("Bounded writer-lock retries are specific to SQLite")
        assign = Assignment._assign_locked
        calls = []

        def assign_after_failure(**kwargs):
            calls.append(kwargs["user"].pk)
            self.assertEqual(AuditLog.objects.filter(action="FACULTY_USER_CREATED").count(), 1)
            if len(calls) == 1:
                raise OperationalError("database is locked")
            return assign(**kwargs)

        with patch.object(Assignment, "_assign_locked", side_effect=assign_after_failure):
            result = self.provision()
        self.assertEqual(len(calls), 2)
        self.assert_provisioned(result)
        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(UserRole.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_nested_lock_failure_propagates_without_retry_or_partial_creation(self):
        with patch.object(Assignment, "_assign_locked", side_effect=OperationalError("database is locked")) as assign:
            with self.assertRaises(OperationalError):
                with transaction.atomic():
                    self.provision()
        self.assertEqual(assign.call_count, 1)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(UserRole.objects.exists())
        self.assertFalse(AuditLog.objects.exists())
