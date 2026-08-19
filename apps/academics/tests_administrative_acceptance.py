from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.conf import settings
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant

from .administrative_acceptance import AdministrativeFacultyAssignmentAcceptanceService
from .models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term


DEFAULT_EXAM_DEPARTMENT = object()


class AdministrativeFacultyAssignmentAcceptanceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="T1", name="Tenant 1")
        self.campus = Campus.objects.create(tenant=self.tenant, code="C1", name="Campus 1")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="D1",
            name="Department 1",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY1",
            name="AY 1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="TERM1",
            name="Term 1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 6, 30),
        )
        self.actor = User.objects.create_superuser(
            username="admin-actor",
            email="admin@example.test",
            password="safe-test-password",
        )
        self.faculty = User.objects.create_user(
            username="faculty-one",
            email="faculty@example.test",
            password="safe-test-password",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        self.faculty_membership = UserRole.objects.create(
            user=self.faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.offering = self._make_offering("BASE")
        self.assignment = self._make_assignment(self.offering)

    def _make_offering(
        self,
        suffix,
        *,
        tenant=None,
        campus=None,
        department=None,
        academic_year=None,
        term=None,
        exam_department=DEFAULT_EXAM_DEPARTMENT,
        status=CourseOffering.Status.OPEN,
        is_active=True,
    ):
        tenant = tenant or self.tenant
        campus = campus or self.campus
        department = department or self.department
        academic_year = academic_year or self.academic_year
        term = term or self.term
        program = Program.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            code=f"P-{suffix}",
            name=f"Program {suffix}",
        )
        course = Course.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            exam_department=(
                department
                if exam_department is DEFAULT_EXAM_DEPARTMENT
                else exam_department
            ),
            code=f"COURSE-{suffix}",
            title=f"Course {suffix}",
        )
        section = Section.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            code=f"SEC-{suffix}",
            name=f"Section {suffix}",
        )
        return CourseOffering.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=academic_year,
            term=term,
            course=course,
            section=section,
            status=status,
            is_active=is_active,
        )

    def _make_assignment(self, offering, *, faculty=None, **overrides):
        values = {
            "tenant": offering.tenant,
            "campus": offering.campus,
            "offering": offering,
            "faculty_user": faculty or self.faculty,
            "is_active": True,
            "response_status": FacultyAssignment.ResponseStatus.PENDING,
        }
        values.update(overrides)
        return FacultyAssignment.objects.create(**values)

    def _preview(self, **overrides):
        values = {
            "tenant": self.tenant,
            "academic_year": self.academic_year,
            "term": self.term,
            "actor": self.actor,
            "reason": "Registrar-confirmed official assignment",
        }
        values.update(overrides)
        return AdministrativeFacultyAssignmentAcceptanceService.preview(**values)

    def _execute(self, report=None, **overrides):
        report = report or self._preview()
        values = {
            "tenant": self.tenant,
            "academic_year": self.academic_year,
            "term": self.term,
            "actor": self.actor,
            "reason": "Registrar-confirmed official assignment",
            "expected_candidate_hash": report.candidate_hash,
        }
        values.update(overrides)
        return AdministrativeFacultyAssignmentAcceptanceService.execute(**values)

    def _set_status(self, assignment, status):
        assignment.response_status = status
        if status == FacultyAssignment.ResponseStatus.ACCEPTED:
            now = timezone.now()
            assignment.responded_at = now
            assignment.accepted_at = now
            assignment.accepted_by = self.faculty
        elif status != FacultyAssignment.ResponseStatus.PENDING:
            assignment.responded_at = timezone.now()
        assignment.save()

    def _other_campus_scope(self, suffix="2"):
        campus = Campus.objects.create(tenant=self.tenant, code=f"C{suffix}", name=f"Campus {suffix}")
        department = Department.objects.create(
            tenant=self.tenant,
            campus=campus,
            code=f"D{suffix}",
            name=f"Department {suffix}",
        )
        return campus, department

    def _scoped_actor(self, *, campus=None, department=None):
        campus = campus or self.campus
        department = department or self.department
        permission = Permission.objects.create(
            code="faculty_assignments.update",
            module="faculty_assignments",
            action="update",
        )
        role = Role.objects.create(code="ASSIGNMENT_ADMIN", name="Assignment Admin")
        RolePermission.objects.create(role=role, permission=permission)
        actor = User.objects.create_user(
            username="scoped-actor",
            email="scoped-actor@example.test",
            password="safe-test-password",
        )
        UserRole.objects.create(
            user=actor,
            role=role,
            tenant=self.tenant,
            campus=campus,
            department=department,
        )
        return actor, permission

    def _add_role(self, actor, *, code, department, permission=None):
        role = Role.objects.create(code=code, name=code.replace("_", " ").title())
        if permission:
            RolePermission.objects.create(role=role, permission=permission)
        return UserRole.objects.create(
            user=actor,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=department,
        )

    def test_dry_run_causes_zero_writes(self):
        before = list(FacultyAssignment.objects.values())
        audit_count = AuditLog.objects.count()
        self._preview()
        self.assertEqual(before, list(FacultyAssignment.objects.values()))
        self.assertEqual(AuditLog.objects.count(), audit_count)

    def test_dry_run_candidate_count_is_correct(self):
        second = self._make_offering("SECOND")
        self._make_assignment(second)
        self.assertEqual(self._preview().candidate_count, 2)

    def test_candidate_hash_is_deterministic(self):
        self.assertEqual(self._preview().candidate_hash, self._preview().candidate_hash)

    def test_candidate_hash_changes_when_readiness_state_drifts(self):
        approved = self._preview().candidate_hash
        self.faculty.is_active = False
        self.faculty.save(update_fields=["is_active", "updated_at"])
        self.assertNotEqual(self._preview().candidate_hash, approved)

    def test_pending_assignment_is_included(self):
        self.assertEqual(self._preview().candidate_ids, (self.assignment.id,))

    def test_accepted_assignment_is_excluded(self):
        self._set_status(self.assignment, FacultyAssignment.ResponseStatus.ACCEPTED)
        self.assertEqual(self._preview().candidate_count, 0)

    def test_declined_assignment_is_excluded(self):
        self._set_status(self.assignment, FacultyAssignment.ResponseStatus.DECLINED)
        self.assertEqual(self._preview().candidate_count, 0)

    def test_clarification_requested_assignment_is_excluded(self):
        self._set_status(self.assignment, FacultyAssignment.ResponseStatus.CLARIFICATION_REQUESTED)
        self.assertEqual(self._preview().candidate_count, 0)

    def test_expired_assignment_is_excluded(self):
        self._set_status(self.assignment, FacultyAssignment.ResponseStatus.EXPIRED)
        self.assertEqual(self._preview().candidate_count, 0)

    def test_inactive_assignment_is_excluded(self):
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        report = self._preview()
        self.assertEqual(report.candidate_count, 0)
        self.assertEqual(report.inactive_assignments, 1)

    def test_wrong_tenant_is_excluded(self):
        other_tenant = Tenant.objects.create(code="T2", name="Tenant 2")
        other_campus = Campus.objects.create(tenant=other_tenant, code="C2", name="Campus 2")
        other_department = Department.objects.create(
            tenant=other_tenant, campus=other_campus, code="D2", name="Department 2"
        )
        other_year = AcademicYear.objects.create(
            tenant=other_tenant,
            code="AY2",
            name="AY 2",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        other_term = Term.objects.create(
            tenant=other_tenant, academic_year=other_year, code="TERM2", name="Term 2"
        )
        offering = self._make_offering(
            "OTHER-TENANT",
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            academic_year=other_year,
            term=other_term,
        )
        self._make_assignment(offering)
        self.assertEqual(self._preview().candidate_count, 1)

    def test_wrong_academic_year_is_excluded(self):
        year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2",
            name="AY 2",
            start_date=date(2027, 1, 1),
            end_date=date(2027, 12, 31),
        )
        term = Term.objects.create(tenant=self.tenant, academic_year=year, code="T2", name="Term 2")
        self._make_assignment(self._make_offering("OTHER-AY", academic_year=year, term=term))
        self.assertEqual(self._preview().candidate_count, 1)

    def test_wrong_term_is_excluded(self):
        term = Term.objects.create(
            tenant=self.tenant, academic_year=self.academic_year, code="T2", name="Term 2"
        )
        self._make_assignment(self._make_offering("OTHER-TERM", term=term))
        self.assertEqual(self._preview().candidate_count, 1)

    def test_closed_offering_is_excluded(self):
        self.offering.status = CourseOffering.Status.CLOSED
        self.offering.save(update_fields=["status", "updated_at"])
        self.assertEqual(self._preview().candidate_count, 0)

    def test_inactive_offering_is_excluded(self):
        self.offering.is_active = False
        self.offering.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(self._preview().candidate_count, 0)

    def test_campus_narrowing(self):
        campus, department = self._other_campus_scope()
        offering = self._make_offering("CAMPUS2", campus=campus, department=department)
        self._make_assignment(offering)
        self.assertEqual(self._preview(campus=self.campus).candidate_ids, (self.assignment.id,))

    def test_exam_department_narrowing(self):
        other_exam = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="EX2",
            name="Exam Department 2",
        )
        offering = self._make_offering("EXAM2", exam_department=other_exam)
        self._make_assignment(offering)
        self.assertEqual(
            self._preview(exam_department=self.department).candidate_ids,
            (self.assignment.id,),
        )

    def test_superuser_excludes_inactive_course_exam_department(self):
        self.offering.course.exam_department.is_active = False
        self.offering.course.exam_department.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(self._preview().candidate_ids, ())

    def test_scoped_actor_excludes_inactive_course_exam_department(self):
        actor, _permission = self._scoped_actor()
        self.offering.course.exam_department.is_active = False
        self.offering.course.exam_department.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(
            self._preview(actor=actor, campus=self.campus).candidate_ids,
            (),
        )

    def test_active_course_exam_department_remains_eligible(self):
        self.assertTrue(self.offering.course.exam_department.is_active)
        self.assertEqual(self._preview().candidate_ids, (self.assignment.id,))

    def test_exam_department_deactivation_after_preview_aborts_execution(self):
        report = self._preview()
        self.offering.course.exam_department.is_active = False
        self.offering.course.exam_department.save(update_fields=["is_active", "updated_at"])
        with self.assertRaises(ValidationError):
            self._execute(report=report)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertFalse(AuditLog.objects.exists())

    def test_null_course_exam_department_follows_existing_policy(self):
        self.offering.course.exam_department = None
        self.offering.course.save(update_fields=["exam_department", "updated_at"])
        self.assertEqual(self._preview().candidate_ids, (self.assignment.id,))

    def test_unauthorized_actor_is_rejected(self):
        actor = User.objects.create_user(
            username="unauthorized",
            email="unauthorized@example.test",
            password="safe-test-password",
        )
        with self.assertRaises(PermissionDenied):
            self._preview(actor=actor)

    def test_direct_deny_precedence(self):
        actor, permission = self._scoped_actor()
        UserPermission.objects.create(
            user=actor,
            permission=permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self._preview(actor=actor, campus=self.campus)

    def test_permission_does_not_compose_with_unrelated_role_department(self):
        actor, _permission = self._scoped_actor()
        other_department = Department.objects.create(
            tenant=self.tenant, campus=self.campus, code="D-CROSS", name="Cross Role"
        )
        self._add_role(actor, code="UNRELATED_SCOPE", department=other_department)
        other = self._make_assignment(
            self._make_offering(
                "CROSS-ROLE",
                department=other_department,
                exam_department=self.department,
            )
        )
        report = self._preview(actor=actor, campus=self.campus)
        self.assertEqual(report.candidate_ids, (self.assignment.id,))
        self.assertEqual(report.authorization_excluded_assignment_ids, (other.id,))

    def test_two_permission_bearing_roles_authorize_their_own_departments(self):
        actor, permission = self._scoped_actor()
        other_department = Department.objects.create(
            tenant=self.tenant, campus=self.campus, code="D-GRANT", name="Second Grant"
        )
        self._add_role(
            actor,
            code="SECOND_ASSIGNMENT_ADMIN",
            department=other_department,
            permission=permission,
        )
        other = self._make_assignment(
            self._make_offering("SECOND-GRANT", department=other_department)
        )
        report = self._preview(actor=actor, campus=self.campus)
        self.assertEqual(report.candidate_ids, (self.assignment.id, other.id))
        self.assertEqual(report.authorization_excluded_assignment_ids, ())

    def test_exam_department_does_not_compose_with_unrelated_role(self):
        actor, _permission = self._scoped_actor()
        other_department = Department.objects.create(
            tenant=self.tenant, campus=self.campus, code="D-EXAM-CROSS", name="Exam Cross"
        )
        self._add_role(actor, code="UNRELATED_EXAM_SCOPE", department=other_department)
        other = self._make_assignment(
            self._make_offering(
                "EXAM-CROSS",
                department=self.department,
                exam_department=other_department,
            )
        )
        report = self._preview(actor=actor, campus=self.campus)
        self.assertEqual(report.candidate_ids, (self.assignment.id,))
        self.assertEqual(report.authorization_excluded_assignment_ids, (other.id,))

    def test_null_department_on_permission_role_is_department_global(self):
        actor, _permission = self._scoped_actor()
        membership = UserRole.objects.get(user=actor, role__code="ASSIGNMENT_ADMIN")
        membership.department = None
        membership.save(update_fields=["department"])
        other_department = Department.objects.create(
            tenant=self.tenant, campus=self.campus, code="D-GLOBAL", name="Global Target"
        )
        other = self._make_assignment(
            self._make_offering("GLOBAL-DEPT", department=other_department)
        )
        self.assertEqual(
            self._preview(actor=actor, campus=self.campus).candidate_ids,
            (self.assignment.id, other.id),
        )

    def test_exact_direct_allow_is_global_within_its_campus(self):
        actor = User.objects.create_user(
            username="direct-actor",
            email="direct-actor@example.test",
            password="safe-test-password",
        )
        permission = Permission.objects.create(
            code="faculty_assignments.update",
            module="faculty_assignments",
            action="update",
        )
        self._add_role(actor, code="BASE_SCOPE_ONLY", department=self.department)
        UserPermission.objects.create(
            user=actor,
            permission=permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        other_department = Department.objects.create(
            tenant=self.tenant, campus=self.campus, code="D-DIRECT", name="Direct Target"
        )
        other = self._make_assignment(
            self._make_offering("DIRECT", department=other_department)
        )
        self.assertEqual(
            self._preview(actor=actor, campus=self.campus).candidate_ids,
            (self.assignment.id, other.id),
        )

    def test_exact_direct_deny_overrides_role_and_direct_allow(self):
        actor, permission = self._scoped_actor()
        UserPermission.objects.create(
            user=actor,
            permission=permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        UserPermission.objects.create(
            user=actor,
            permission=permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self._preview(actor=actor, campus=self.campus)

    def test_partial_actor_scope_reports_authorization_exclusion(self):
        actor, _permission = self._scoped_actor()
        campus, department = self._other_campus_scope("AUTH")
        unauthorized = self._make_assignment(
            self._make_offering("AUTH-OTHER", campus=campus, department=department)
        )
        report = self._preview(actor=actor)
        self.assertEqual(report.candidate_ids, (self.assignment.id,))
        self.assertEqual(report.authorization_excluded_assignment_ids, (unauthorized.id,))

    def test_authorization_exclusion_aborts_execution(self):
        actor, _permission = self._scoped_actor()
        campus, department = self._other_campus_scope("AUTHX")
        unauthorized = self._make_assignment(
            self._make_offering("AUTHX-OTHER", campus=campus, department=department)
        )
        report = self._preview(actor=actor)
        with self.assertRaises(PermissionDenied):
            self._execute(report=report, actor=actor)
        self.assignment.refresh_from_db()
        unauthorized.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertEqual(unauthorized.response_status, FacultyAssignment.ResponseStatus.PENDING)

    def test_admin_actor_is_truthfully_recorded(self):
        self._execute()
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.accepted_by, self.actor)

    def test_accepted_by_is_not_falsified_as_faculty(self):
        self._execute()
        self.assignment.refresh_from_db()
        self.assertNotEqual(self.assignment.accepted_by, self.assignment.faculty_user)

    def test_responded_at_is_populated(self):
        self._execute()
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.responded_at)

    def test_accepted_at_is_populated(self):
        self._execute()
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.accepted_at)

    def test_accepted_at_and_responded_at_are_consistent(self):
        self._execute()
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.accepted_at, self.assignment.responded_at)

    def test_response_due_at_is_cleared(self):
        self.assignment.response_due_at = timezone.now() + timedelta(days=1)
        self.assignment.save(update_fields=["response_due_at", "updated_at"])
        self._execute()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.response_due_at)

    def test_last_reminded_at_is_cleared(self):
        self.assignment.last_reminded_at = timezone.now()
        self.assignment.save(update_fields=["last_reminded_at", "updated_at"])
        self._execute()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.last_reminded_at)

    def test_reminder_count_is_reset(self):
        self.assignment.reminder_count = 4
        self.assignment.save(update_fields=["reminder_count", "updated_at"])
        self._execute()
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.reminder_count, 0)

    def test_faculty_response_note_is_cleared(self):
        self.assignment.faculty_response_note = "Old pending note"
        self.assignment.save(update_fields=["faculty_response_note", "updated_at"])
        self._execute()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.faculty_response_note)

    def test_exactly_one_audit_per_changed_assignment(self):
        report = self._execute()
        self.assertEqual(
            AuditLog.objects.filter(
                action=AdministrativeFacultyAssignmentAcceptanceService.AUDIT_ACTION
            ).count(),
            report.changed_count,
        )

    def test_audit_event_reason_and_batch_are_correct(self):
        report = self._execute()
        audit = AuditLog.objects.get(
            action=AdministrativeFacultyAssignmentAcceptanceService.AUDIT_ACTION
        )
        self.assertEqual(audit.metadata_json["event"], "administrative_faculty_assignment_acceptance")
        self.assertEqual(audit.metadata_json["reason"], "Registrar-confirmed official assignment")
        self.assertEqual(audit.metadata_json["batch_id"], report.batch_id)
        self.assertEqual(audit.metadata_json["source"], "management_command")
        self.assertIsNone(audit.http_method)
        self.assertIsNone(audit.ip_address)

    def test_expected_hash_mismatch_causes_zero_writes(self):
        with self.assertRaises(ValidationError):
            self._execute(expected_candidate_hash="0" * 64)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertFalse(AuditLog.objects.exists())

    def test_candidate_drift_causes_zero_writes(self):
        report = self._preview()
        offering = self._make_offering("DRIFT")
        self._make_assignment(offering)
        with self.assertRaises(ValidationError):
            self._execute(report=report)
        self.assertEqual(
            FacultyAssignment.objects.filter(response_status=FacultyAssignment.ResponseStatus.ACCEPTED).count(),
            0,
        )

    def test_readiness_anomaly_appears_in_dry_run(self):
        self.faculty.set_unusable_password()
        self.faculty.save(update_fields=["password", "updated_at"])
        report = self._preview()
        self.assertEqual(report.readiness_anomalies["unusable_password"], (self.assignment.id,))

    def test_anomaly_blocks_execution_without_override(self):
        self.faculty.is_active = False
        self.faculty.save(update_fields=["is_active", "updated_at"])
        report = self._preview()
        with self.assertRaises(ValidationError):
            self._execute(report=report)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)

    def test_anomaly_override_allows_otherwise_eligible_assignment(self):
        self.faculty.is_active = False
        self.faculty.save(update_fields=["is_active", "updated_at"])
        report = self._preview()
        result = self._execute(report=report, include_readiness_anomalies=True)
        self.assertEqual(result.changed_count, 1)

    def test_inactive_account_anomaly_is_distinct(self):
        self.faculty.is_active = False
        self.faculty.save(update_fields=["is_active", "updated_at"])
        report = self._preview()
        self.assertEqual(report.readiness_anomalies["inactive_user"], (self.assignment.id,))
        self.assertEqual(report.readiness_anomalies["unusable_password"], ())

    def test_unusable_password_anomaly_is_distinct(self):
        self.faculty.set_unusable_password()
        self.faculty.save(update_fields=["password", "updated_at"])
        report = self._preview()
        self.assertEqual(report.readiness_anomalies["unusable_password"], (self.assignment.id,))
        self.assertEqual(report.readiness_anomalies["inactive_user"], ())

    def test_missing_faculty_role_anomaly_is_distinct(self):
        self.faculty_membership.delete()
        report = self._preview()
        self.assertEqual(report.readiness_anomalies["faculty_membership_missing"], (self.assignment.id,))

    def test_inactive_faculty_role_anomaly_is_distinct(self):
        self.faculty_role.is_active = False
        self.faculty_role.save(update_fields=["is_active", "updated_at"])
        report = self._preview()
        self.assertEqual(report.readiness_anomalies["faculty_role_inactive"], (self.assignment.id,))

    def test_inactive_faculty_membership_anomaly_is_distinct(self):
        self.faculty_membership.is_active = False
        self.faculty_membership.save(update_fields=["is_active"])
        report = self._preview()
        self.assertEqual(
            report.readiness_anomalies["faculty_membership_inactive"],
            (self.assignment.id,),
        )
        self.assertEqual(report.readiness_anomalies["faculty_role_inactive"], ())
        self.assertEqual(report.readiness_anomalies["other"], ())

    def test_inactive_faculty_membership_and_role_are_both_reported(self):
        self.faculty_membership.is_active = False
        self.faculty_membership.save(update_fields=["is_active"])
        self.faculty_role.is_active = False
        self.faculty_role.save(update_fields=["is_active", "updated_at"])
        report = self._preview()
        self.assertEqual(
            report.readiness_anomalies["faculty_membership_inactive"],
            (self.assignment.id,),
        )
        self.assertEqual(
            report.readiness_anomalies["faculty_role_inactive"],
            (self.assignment.id,),
        )

    def test_inactive_membership_blocks_execution_without_override(self):
        self.faculty_membership.is_active = False
        self.faculty_membership.save(update_fields=["is_active"])
        report = self._preview()
        with self.assertRaises(ValidationError):
            self._execute(report=report)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)

    def test_readiness_override_does_not_bypass_assignment_authorization(self):
        actor, _permission = self._scoped_actor()
        other_department = Department.objects.create(
            tenant=self.tenant, campus=self.campus, code="D-NO-BYPASS", name="No Bypass"
        )
        unauthorized = self._make_assignment(
            self._make_offering("NO-BYPASS", department=other_department)
        )
        self.faculty.is_active = False
        self.faculty.save(update_fields=["is_active", "updated_at"])
        report = self._preview(actor=actor, campus=self.campus)
        self.assertIn(unauthorized.id, report.authorization_excluded_assignment_ids)
        with self.assertRaises(PermissionDenied):
            self._execute(
                report=report,
                actor=actor,
                campus=self.campus,
                include_readiness_anomalies=True,
            )

    def test_role_scope_mismatch_anomaly_is_distinct(self):
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="D-OTHER",
            name="Other Department",
        )
        self.faculty_membership.department = other_department
        self.faculty_membership.save(update_fields=["department"])
        report = self._preview()
        self.assertEqual(report.readiness_anomalies["department_mismatch"], (self.assignment.id,))

    def test_audit_failure_rolls_back_all_changes(self):
        with patch(
            "apps.academics.administrative_acceptance.AuditService.log_event",
            side_effect=RuntimeError("audit failed"),
        ):
            with self.assertRaises(RuntimeError):
                self._execute()
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertFalse(AuditLog.objects.exists())

    def test_second_run_is_idempotent(self):
        self._execute()
        second = self._preview()
        self.assertEqual(second.candidate_count, 0)
        result = self._execute(report=second)
        self.assertEqual(result.changed_count, 0)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AdministrativeFacultyAssignmentAcceptanceService.AUDIT_ACTION
            ).count(),
            1,
        )

    def test_no_nonpending_status_is_overwritten(self):
        declined = self._make_assignment(
            self._make_offering("DECLINED"),
            response_status=FacultyAssignment.ResponseStatus.DECLINED,
            responded_at=timezone.now(),
        )
        self._execute()
        declined.refresh_from_db()
        self.assertEqual(declined.response_status, FacultyAssignment.ResponseStatus.DECLINED)

    def test_cross_tenant_and_cross_campus_records_are_untouched(self):
        campus, department = self._other_campus_scope("3")
        other = self._make_assignment(
            self._make_offering("CAMPUS3", campus=campus, department=department)
        )
        report = self._preview(campus=self.campus)
        self._execute(report=report, campus=self.campus)
        other.refresh_from_db()
        self.assertEqual(other.response_status, FacultyAssignment.ResponseStatus.PENDING)

    def test_unrelated_overdue_assignments_do_not_expire(self):
        campus, department = self._other_campus_scope("4")
        overdue = self._make_assignment(
            self._make_offering("OVERDUE", campus=campus, department=department),
            response_due_at=timezone.now() - timedelta(days=1),
        )
        report = self._preview(campus=self.campus)
        self._execute(report=report, campus=self.campus)
        overdue.refresh_from_db()
        self.assertEqual(overdue.response_status, FacultyAssignment.ResponseStatus.PENDING)

    def test_malformed_state_fails_execution_closed(self):
        malformed = self._make_assignment(
            self._make_offering("MALFORMED"),
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=None,
        )
        report = self._preview()
        self.assertIn(malformed.id, report.malformed_acceptance_assignment_ids)
        with self.assertRaises(ValidationError):
            self._execute(report=report)

    def test_command_defaults_to_dry_run_and_prints_hash(self):
        output = StringIO()
        call_command(
            "administratively_accept_faculty_assignments",
            tenant=self.tenant.code,
            academic_year=self.academic_year.code,
            term=self.term.code,
            actor=str(self.actor.id),
            reason="Registrar-confirmed official assignment",
            stdout=output,
        )
        rendered = output.getvalue()
        self.assertIn("Candidate hash:", rendered)
        self.assertIn("NO DATABASE CHANGES MADE", rendered)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)

    def test_command_execute_requires_expected_hash(self):
        with self.assertRaises(CommandError):
            call_command(
                "administratively_accept_faculty_assignments",
                tenant=self.tenant.code,
                academic_year=self.academic_year.code,
                term=self.term.code,
                actor=str(self.actor.id),
                reason="Registrar-confirmed official assignment",
                execute=True,
            )

    def test_command_executes_with_approved_hash(self):
        report = self._preview()
        output = StringIO()
        call_command(
            "administratively_accept_faculty_assignments",
            tenant=self.tenant.code,
            academic_year=self.academic_year.code,
            term=self.term.code,
            actor=str(self.actor.id),
            reason="Registrar-confirmed official assignment",
            execute=True,
            expected_candidate_hash=report.candidate_hash,
            stdout=output,
        )
        self.assertIn("CHANGED: 1", output.getvalue())
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.ACCEPTED)

    def test_command_reports_inactive_membership_separately(self):
        self.faculty_membership.is_active = False
        self.faculty_membership.save(update_fields=["is_active"])
        output = StringIO()
        call_command(
            "administratively_accept_faculty_assignments",
            tenant=self.tenant.code,
            academic_year=self.academic_year.code,
            term=self.term.code,
            actor=str(self.actor.id),
            reason="Registrar-confirmed official assignment",
            stdout=output,
        )
        rendered = output.getvalue()
        self.assertIn("FACULTY membership inactive: 1", rendered)
        self.assertIn("FACULTY role inactive: 0", rendered)

    def test_user_facing_acceptance_wording_allows_administrative_confirmation(self):
        expected = {
            "apps/admin_portal/views.py": "accepted or administratively confirmed",
            "templates/admin_portal/dashboard.html": "authorized administrator confirms them",
            "apps/admin_portal/help_guide.py": "authorized administrative confirmation",
            "templates/admin_portal/guide.html": "administratively confirmed by an authorized administrator",
            "templates/faculty_portal/guide_manual.html": "authorized administrator confirms it",
        }
        for relative_path, phrase in expected.items():
            with self.subTest(path=relative_path):
                content = (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")
                self.assertIn(phrase, content)

        false_claims = {
            "apps/admin_portal/views.py": ("waiting for faculty acknowledgment", "% acknowledged"),
            "templates/admin_portal/dashboard.html": ("Faculty Must Accept Assigned Loads First",),
            "apps/admin_portal/help_guide.py": ("wait for the faculty member to accept it",),
            "templates/admin_portal/guide.html": ("New assignments stay pending until the faculty member responds",),
            "templates/faculty_portal/guide_manual.html": ("stays pending until you accept it.",),
        }
        for relative_path, phrases in false_claims.items():
            content = (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")
            for phrase in phrases:
                with self.subTest(path=relative_path, phrase=phrase):
                    self.assertNotIn(phrase, content)


class AdministrativeFacultyAssignmentAcceptanceTransactionTests(TransactionTestCase):
    """Locked-state revalidation coverage on Django's transaction-aware base."""

    reset_sequences = True
    setUp = AdministrativeFacultyAssignmentAcceptanceTests.setUp
    _make_offering = AdministrativeFacultyAssignmentAcceptanceTests._make_offering
    _make_assignment = AdministrativeFacultyAssignmentAcceptanceTests._make_assignment
    _preview = AdministrativeFacultyAssignmentAcceptanceTests._preview
    _execute = AdministrativeFacultyAssignmentAcceptanceTests._execute
    _scoped_actor = AdministrativeFacultyAssignmentAcceptanceTests._scoped_actor

    def test_offering_closed_between_preview_and_execution_aborts(self):
        report = self._preview()
        self.offering.status = CourseOffering.Status.CLOSED
        self.offering.save(update_fields=["status", "updated_at"])
        with self.assertRaises(ValidationError):
            self._execute(report=report)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertFalse(AuditLog.objects.exists())

    def test_actor_permission_revoked_before_locked_execution_aborts(self):
        actor, permission = self._scoped_actor()
        report = self._preview(actor=actor, campus=self.campus)
        RolePermission.objects.filter(permission=permission).delete()
        with self.assertRaises(PermissionDenied):
            self._execute(report=report, actor=actor, campus=self.campus)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertFalse(AuditLog.objects.exists())

    def test_faculty_readiness_change_before_locked_execution_aborts(self):
        report = self._preview()
        self.faculty.is_active = False
        self.faculty.save(update_fields=["is_active", "updated_at"])
        with self.assertRaises(ValidationError):
            self._execute(report=report, include_readiness_anomalies=True)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.PENDING)
        self.assertFalse(AuditLog.objects.exists())

    def test_two_executions_cannot_duplicate_mutation_or_audit(self):
        report = self._preview()
        first = self._execute(report=report)
        self.assertEqual(first.changed_count, 1)
        with self.assertRaises(ValidationError):
            self._execute(report=report)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.ACCEPTED)
        self.assertEqual(
            AuditLog.objects.filter(
                action=AdministrativeFacultyAssignmentAcceptanceService.AUDIT_ACTION
            ).count(),
            1,
        )
