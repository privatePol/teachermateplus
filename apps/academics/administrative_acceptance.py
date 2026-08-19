from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant

from .models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term


class AdministrativeAcceptanceError(ValidationError):
    pass


@dataclass(frozen=True)
class AdministrativeAcceptanceReport:
    tenant: object
    academic_year: AcademicYear
    term: Term
    actor: User
    campus: object | None
    exam_department: object | None
    offerings_examined: int
    assignments_examined: int
    distinct_faculty: int
    status_counts: dict[str, int]
    inactive_assignments: int
    malformed_acceptance_assignment_ids: tuple[int, ...]
    inconsistent_scope_assignment_ids: tuple[int, ...]
    authorization_excluded_assignment_ids: tuple[int, ...]
    readiness_anomalies: dict[str, tuple[int, ...]]
    candidate_ids: tuple[int, ...]
    candidate_hash: str
    changed_count: int = 0
    batch_id: str | None = None

    @property
    def candidate_count(self):
        return len(self.candidate_ids)

    @property
    def would_change(self):
        return self.candidate_count

    @property
    def has_readiness_anomalies(self):
        return any(self.readiness_anomalies.values())


class AdministrativeFacultyAssignmentAcceptanceService:
    """Preview and apply truthful administrative assignment acceptance.

    The candidate hash is SHA-256 over compact, sorted JSON. Each candidate row
    includes assignment ID, assignment tenant/campus/faculty/status/acceptance
    fields, every offering/hierarchy field used by executable eligibility, and
    the faculty account/FACULTY-role readiness categories approved at preview.
    """

    PERMISSION_CODE = "faculty_assignments.update"
    AUDIT_ACTION = "ADMINISTRATIVE_FACULTY_ASSIGNMENT_ACCEPTANCE"
    AUDIT_EVENT = "administrative_faculty_assignment_acceptance"
    SOURCE = "management_command"
    ANOMALY_KEYS = (
        "inactive_user",
        "unusable_password",
        "faculty_membership_missing",
        "faculty_membership_inactive",
        "faculty_role_inactive",
        "tenant_mismatch",
        "campus_mismatch",
        "department_mismatch",
        "other",
    )

    @classmethod
    def preview(
        cls,
        *,
        tenant,
        academic_year,
        term,
        actor,
        reason,
        campus=None,
        exam_department=None,
    ):
        cls._validate_inputs(
            tenant=tenant,
            academic_year=academic_year,
            term=term,
            actor=actor,
            reason=reason,
            campus=campus,
            exam_department=exam_department,
        )
        return cls._build_report(
            tenant=tenant,
            academic_year=academic_year,
            term=term,
            actor=actor,
            campus=campus,
            exam_department=exam_department,
        )

    @classmethod
    def execute(
        cls,
        *,
        tenant,
        academic_year,
        term,
        actor,
        reason,
        expected_candidate_hash,
        campus=None,
        exam_department=None,
        include_readiness_anomalies=False,
    ):
        if not (expected_candidate_hash or "").strip():
            raise AdministrativeAcceptanceError("Execution requires an expected candidate hash.")

        with transaction.atomic():
            locked_scope = cls._lock_execution_state(
                tenant_id=tenant.id,
                academic_year_id=academic_year.id,
                term_id=term.id,
                actor_id=actor.id,
                campus_id=campus.id if campus else None,
                exam_department_id=exam_department.id if exam_department else None,
            )
            tenant = locked_scope["tenant"]
            academic_year = locked_scope["academic_year"]
            term = locked_scope["term"]
            actor = locked_scope["actor"]
            campus = locked_scope["campus"]
            exam_department = locked_scope["exam_department"]
            cls._validate_inputs(
                tenant=tenant,
                academic_year=academic_year,
                term=term,
                actor=actor,
                reason=reason,
                campus=campus,
                exam_department=exam_department,
            )
            report = cls._build_report(
                tenant=tenant,
                academic_year=academic_year,
                term=term,
                actor=actor,
                campus=campus,
                exam_department=exam_department,
            )
            if report.malformed_acceptance_assignment_ids:
                raise AdministrativeAcceptanceError("Malformed acceptance state detected; zero rows changed.")
            if report.inconsistent_scope_assignment_ids:
                raise AdministrativeAcceptanceError("Inconsistent assignment scope detected; zero rows changed.")
            if report.authorization_excluded_assignment_ids:
                raise PermissionDenied("One or more otherwise-eligible assignments are outside the actor's authority.")
            if report.candidate_hash != expected_candidate_hash.strip().lower():
                raise AdministrativeAcceptanceError("Candidate hash mismatch; zero rows changed.")
            if report.has_readiness_anomalies and not include_readiness_anomalies:
                raise AdministrativeAcceptanceError(
                    "Readiness anomalies require the explicit include-readiness-anomalies override."
                )

            now = timezone.now()
            batch_id = str(uuid.uuid4())
            changed = 0
            audit_rows = []
            locked_candidates = {
                row.id: row
                for row in FacultyAssignment.objects.select_for_update()
                .filter(id__in=report.candidate_ids)
                .select_related("offering", "offering__academic_year", "offering__term")
                .order_by("id")
            }
            if tuple(locked_candidates) != report.candidate_ids:
                raise AdministrativeAcceptanceError("Candidate set drift detected; zero rows changed.")

            for assignment_id in report.candidate_ids:
                assignment = locked_candidates[assignment_id]
                before = cls._acceptance_snapshot(assignment)
                assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
                assignment.responded_at = now
                assignment.accepted_at = now
                assignment.accepted_by = actor
                assignment.faculty_response_note = None
                assignment.response_due_at = None
                assignment.last_reminded_at = None
                assignment.reminder_count = 0
                assignment.save(
                    update_fields=[
                        "response_status",
                        "responded_at",
                        "accepted_at",
                        "accepted_by",
                        "faculty_response_note",
                        "response_due_at",
                        "last_reminded_at",
                        "reminder_count",
                        "updated_at",
                    ]
                )
                after = cls._acceptance_snapshot(assignment)
                audit_rows.append(
                    AuditService.log_event(
                        action=cls.AUDIT_ACTION,
                        portal="ADMIN",
                        entity_type="FacultyAssignment",
                        entity_id=assignment.id,
                        actor=actor,
                        tenant=tenant,
                        campus=assignment.offering.campus,
                        before_data=before,
                        after_data=after,
                        metadata={
                            "event": cls.AUDIT_EVENT,
                            "batch_id": batch_id,
                            "reason": reason.strip(),
                            "assignment_id": assignment.id,
                            "offering_id": assignment.offering_id,
                            "academic_year_id": academic_year.id,
                            "academic_year_code": academic_year.code,
                            "term_id": term.id,
                            "term_code": term.code,
                            "actor_id": actor.id,
                            "source": cls.SOURCE,
                        },
                        request=None,
                    )
                )
                changed += 1

            if changed != report.candidate_count or len(audit_rows) != changed:
                raise AdministrativeAcceptanceError("Assignment/audit count verification failed.")
            persisted_audits = AuditLog.objects.filter(
                id__in=[row.id for row in audit_rows],
                action=cls.AUDIT_ACTION,
            ).count()
            if persisted_audits != changed:
                raise AdministrativeAcceptanceError("Durable audit verification failed.")

            return AdministrativeAcceptanceReport(
                **{
                    **report.__dict__,
                    "changed_count": changed,
                    "batch_id": batch_id,
                }
            )

    @classmethod
    def _lock_execution_state(
        cls,
        *,
        tenant_id,
        academic_year_id,
        term_id,
        actor_id,
        campus_id,
        exam_department_id,
    ):
        """Lock and re-fetch every mutable row used by execution decisions.

        The offering/assignment lock is deliberately term-wide within the
        requested tenant. This prevents a mutable campus or Exam Department FK
        from moving a row into or out of the narrower command scope while the
        locked-state report is being rebuilt.
        """

        tenant = Tenant.objects.select_for_update().get(pk=tenant_id)
        academic_year = AcademicYear.objects.select_for_update().get(pk=academic_year_id)
        term = Term.objects.select_for_update().get(pk=term_id)
        actor = User.objects.select_for_update().get(pk=actor_id)
        campus = (
            Campus.objects.select_for_update().get(pk=campus_id)
            if campus_id is not None
            else None
        )
        exam_department = (
            Department.objects.select_for_update().get(pk=exam_department_id)
            if exam_department_id is not None
            else None
        )

        offerings = list(
            CourseOffering.objects.select_for_update()
            .filter(
                tenant_id=tenant_id,
                academic_year_id=academic_year_id,
                term_id=term_id,
            )
            .order_by("id")
        )
        offering_ids = [row.id for row in offerings]
        assignments = list(
            FacultyAssignment.objects.select_for_update()
            .filter(offering_id__in=offering_ids)
            .order_by("id")
        )

        course_ids = {row.course_id for row in offerings}
        section_ids = {row.section_id for row in offerings}
        program_ids = {row.program_id for row in offerings if row.program_id}
        courses = list(
            Course.objects.select_for_update().filter(id__in=course_ids).order_by("id")
        )
        sections = list(
            Section.objects.select_for_update().filter(id__in=section_ids).order_by("id")
        )
        section_program_ids = {row.program_id for row in sections}
        programs = list(
            Program.objects.select_for_update()
            .filter(id__in=(program_ids | section_program_ids))
            .order_by("id")
        )

        faculty_user_ids = {row.faculty_user_id for row in assignments}
        locked_user_ids = faculty_user_ids | {actor_id}
        list(
            User.objects.select_for_update()
            .filter(id__in=locked_user_ids)
            .order_by("id")
        )
        user_roles = list(
            UserRole.objects.select_for_update()
            .filter(user_id__in=locked_user_ids)
            .order_by("id")
        )
        role_ids = {row.role_id for row in user_roles}
        list(Role.objects.select_for_update().filter(id__in=role_ids).order_by("id"))

        permissions = list(
            Permission.objects.select_for_update()
            .filter(code__in=(cls.PERMISSION_CODE,))
            .order_by("id")
        )
        permission_ids = {row.id for row in permissions}
        list(
            RolePermission.objects.select_for_update()
            .filter(role_id__in=role_ids, permission_id__in=permission_ids)
            .order_by("id")
        )
        list(
            UserPermission.objects.select_for_update()
            .filter(user_id=actor_id, permission_id__in=permission_ids)
            .order_by("id")
        )

        campus_ids = {row.campus_id for row in offerings}
        if campus_id is not None:
            campus_ids.add(campus_id)
        list(Campus.objects.select_for_update().filter(id__in=campus_ids).order_by("id"))

        department_ids = {row.department_id for row in offerings}
        department_ids.update(row.department_id for row in courses if row.department_id)
        department_ids.update(row.exam_department_id for row in courses if row.exam_department_id)
        department_ids.update(row.department_id for row in sections)
        department_ids.update(row.department_id for row in programs)
        department_ids.update(row.department_id for row in user_roles if row.department_id)
        if exam_department_id is not None:
            department_ids.add(exam_department_id)
        list(
            Department.objects.select_for_update()
            .filter(
                Q(id__in=department_ids)
                | Q(tenant_id=tenant_id, campus_id__in=campus_ids)
            )
            .order_by("id")
        )

        return {
            "tenant": tenant,
            "academic_year": academic_year,
            "term": term,
            "actor": actor,
            "campus": campus,
            "exam_department": exam_department,
        }

    @classmethod
    def _validate_inputs(
        cls,
        *,
        tenant,
        academic_year,
        term,
        actor,
        reason,
        campus,
        exam_department,
    ):
        if not tenant or not tenant.is_active:
            raise AdministrativeAcceptanceError("An active tenant is required.")
        if not academic_year or academic_year.tenant_id != tenant.id:
            raise AdministrativeAcceptanceError("Academic year does not belong to the requested tenant.")
        if not term or term.tenant_id != tenant.id or term.academic_year_id != academic_year.id:
            raise AdministrativeAcceptanceError("Term does not belong to the requested tenant and academic year.")
        if not actor or not actor.is_active:
            raise PermissionDenied("An active administrative actor is required.")
        if not (reason or "").strip():
            raise AdministrativeAcceptanceError("A non-empty administrative reason is required.")
        if campus and (campus.tenant_id != tenant.id or not campus.is_active):
            raise AdministrativeAcceptanceError("Campus does not belong to the active requested tenant.")
        if exam_department and (
            exam_department.tenant_id != tenant.id
            or not exam_department.is_active
            or (campus and exam_department.campus_id != campus.id)
        ):
            raise AdministrativeAcceptanceError("Exam department is outside the requested active scope.")
        if actor.is_superuser:
            return
        if tenant.id not in ScopeService.get_accessible_tenant_ids(actor):
            raise PermissionDenied("Actor is not authorized for the requested tenant.")
        campus_ids = [campus.id] if campus else ScopeService.get_accessible_campus_ids(actor, tenant_id=tenant.id)
        if not campus_ids or not any(
            PermissionService.has_permission(
                actor,
                cls.PERMISSION_CODE,
                tenant_id=tenant.id,
                campus_id=campus_id,
            )
            for campus_id in campus_ids
        ):
            raise PermissionDenied("Actor lacks faculty assignment update authority in the requested scope.")

    @classmethod
    def _offering_queryset(cls, *, tenant, academic_year, term, campus, exam_department):
        queryset = CourseOffering.objects.filter(
            tenant=tenant,
            tenant__is_active=True,
            academic_year=academic_year,
            academic_year__is_active=True,
            term=term,
            term__is_active=True,
            term__academic_year=academic_year,
            term__academic_year__is_active=True,
            campus__is_active=True,
            department__is_active=True,
            course__is_active=True,
            section__is_active=True,
            section__department__is_active=True,
            section__program__is_active=True,
            section__program__department__is_active=True,
            is_active=True,
            status=CourseOffering.Status.OPEN,
        ).filter(
            Q(program__isnull=True)
            | Q(program__is_active=True, program__department__is_active=True),
            Q(course__department__isnull=True) | Q(course__department__is_active=True),
            Q(course__exam_department__isnull=True)
            | Q(
                course__exam_department__is_active=True,
                course__exam_department__tenant_id=F("tenant_id"),
            ),
        )
        if campus:
            queryset = queryset.filter(campus=campus)
        if exam_department:
            queryset = queryset.filter(course__exam_department=exam_department)
        return queryset

    @classmethod
    def _assignment_queryset(cls, *, tenant, academic_year, term, campus, exam_department):
        return (
            FacultyAssignment.objects.filter(
                offering__in=cls._offering_queryset(
                    tenant=tenant,
                    academic_year=academic_year,
                    term=term,
                    campus=campus,
                    exam_department=exam_department,
                )
            )
            .select_related(
                "tenant",
                "campus",
                "faculty_user",
                "offering",
                "offering__tenant",
                "offering__campus",
                "offering__department",
                "offering__academic_year",
                "offering__term",
                "offering__course",
                "offering__course__department",
                "offering__course__exam_department",
                "offering__section",
                "offering__section__department",
                "offering__section__program",
                "offering__section__program__department",
                "offering__program",
                "offering__program__department",
            )
            .order_by("id")
        )

    @classmethod
    def _build_report(cls, *, tenant, academic_year, term, actor, campus, exam_department):
        offering_queryset = cls._offering_queryset(
            tenant=tenant,
            academic_year=academic_year,
            term=term,
            campus=campus,
            exam_department=exam_department,
        )
        assignments = list(
            cls._assignment_queryset(
                tenant=tenant,
                academic_year=academic_year,
                term=term,
                campus=campus,
                exam_department=exam_department,
            )
        )
        status_counts = {
            choice: 0 for choice, _label in FacultyAssignment.ResponseStatus.choices
        }
        malformed = []
        inconsistent_scope = []
        authorization_excluded = []
        candidates = []

        for assignment in assignments:
            if assignment.is_active and assignment.response_status in status_counts:
                status_counts[assignment.response_status] += 1
            if assignment.is_active and cls._is_malformed_acceptance_state(assignment):
                malformed.append(assignment.id)
            if assignment.is_active and not cls._assignment_scope_is_consistent(assignment):
                inconsistent_scope.append(assignment.id)
            if not cls._is_executable_pending(assignment):
                continue
            if not cls._actor_allows_assignment(actor=actor, assignment=assignment):
                authorization_excluded.append(assignment.id)
                continue
            candidates.append(assignment)

        role_rows_by_user = cls._faculty_role_rows_by_user(candidates)
        anomaly_ids = defaultdict(set)
        for assignment in candidates:
            for key in cls._readiness_anomalies_for(
                assignment,
                role_rows=role_rows_by_user.get(assignment.faculty_user_id, ()),
            ):
                anomaly_ids[key].add(assignment.id)
        readiness_anomalies = {
            key: tuple(sorted(anomaly_ids[key])) for key in cls.ANOMALY_KEYS
        }
        candidate_ids = tuple(row.id for row in candidates)
        return AdministrativeAcceptanceReport(
            tenant=tenant,
            academic_year=academic_year,
            term=term,
            actor=actor,
            campus=campus,
            exam_department=exam_department,
            offerings_examined=offering_queryset.count(),
            assignments_examined=len(assignments),
            distinct_faculty=len({row.faculty_user_id for row in assignments}),
            status_counts=status_counts,
            inactive_assignments=sum(not row.is_active for row in assignments),
            malformed_acceptance_assignment_ids=tuple(sorted(malformed)),
            inconsistent_scope_assignment_ids=tuple(sorted(inconsistent_scope)),
            authorization_excluded_assignment_ids=tuple(sorted(authorization_excluded)),
            readiness_anomalies=readiness_anomalies,
            candidate_ids=candidate_ids,
            candidate_hash=cls._candidate_hash(
                candidates,
                role_rows_by_user=role_rows_by_user,
            ),
        )

    @staticmethod
    def _assignment_scope_is_consistent(assignment):
        offering = assignment.offering
        return bool(
            assignment.tenant_id == offering.tenant_id
            and assignment.campus_id == offering.campus_id
            and offering.campus.tenant_id == offering.tenant_id
            and offering.department.tenant_id == offering.tenant_id
            and offering.department.campus_id == offering.campus_id
            and offering.academic_year.tenant_id == offering.tenant_id
            and offering.term.tenant_id == offering.tenant_id
            and offering.term.academic_year_id == offering.academic_year_id
            and offering.course.tenant_id == offering.tenant_id
            and offering.section.tenant_id == offering.tenant_id
            and offering.section.campus_id == offering.campus_id
        )

    @staticmethod
    def _is_malformed_acceptance_state(assignment):
        if assignment.response_status == FacultyAssignment.ResponseStatus.ACCEPTED:
            return not (
                assignment.responded_at
                and assignment.accepted_at
                and assignment.accepted_by_id
                and assignment.responded_at == assignment.accepted_at
            )
        if assignment.accepted_at or assignment.accepted_by_id:
            return True
        if assignment.response_status == FacultyAssignment.ResponseStatus.PENDING:
            return assignment.responded_at is not None
        return assignment.responded_at is None

    @classmethod
    def _is_executable_pending(cls, assignment):
        return bool(
            assignment.is_active
            and assignment.response_status == FacultyAssignment.ResponseStatus.PENDING
            and assignment.responded_at is None
            and assignment.accepted_at is None
            and assignment.accepted_by_id is None
            and cls._assignment_scope_is_consistent(assignment)
            and not cls._is_malformed_acceptance_state(assignment)
        )

    @classmethod
    def _actor_allows_assignment(cls, *, actor, assignment):
        if actor.is_superuser:
            return True
        tenant_id = assignment.offering.tenant_id
        campus_id = assignment.offering.campus_id
        if tenant_id not in ScopeService.get_accessible_tenant_ids(actor):
            return False
        if campus_id not in ScopeService.get_accessible_campus_ids(actor, tenant_id=tenant_id):
            return False
        scoped_permissions = PermissionService._scoped_user_permissions(
            actor, tenant_id=tenant_id, campus_id=campus_id
        ).filter(permission__code=cls.PERMISSION_CODE)
        if scoped_permissions.filter(grant_type=UserPermission.GrantType.DENY).exists():
            return False
        if scoped_permissions.filter(grant_type=UserPermission.GrantType.ALLOW).exists():
            # UserPermission has tenant/campus scope but no department field.
            # An applicable direct ALLOW is therefore global to departments in
            # that exact campus, matching the existing report authorization contract.
            return True

        permission_roles = list(
            PermissionService._scoped_user_roles(
                actor, tenant_id=tenant_id, campus_id=campus_id
            )
            .filter(
                role__role_permissions__permission__code=cls.PERMISSION_CODE,
                role__role_permissions__permission__is_active=True,
            )
            .select_related("department")
            .distinct()
        )
        if not permission_roles:
            return False
        required_department_ids = {assignment.offering.department_id}
        if assignment.offering.course.exam_department_id:
            required_department_ids.add(assignment.offering.course.exam_department_id)
        return all(
            any(
                row.department_id is None
                or (
                    row.department.is_active
                    and ScopeService.department_scope_covers(
                        row.department_id, required_department_id
                    )
                )
                for row in permission_roles
            )
            for required_department_id in required_department_ids
        )

    @staticmethod
    def _faculty_role_rows_by_user(assignments):
        user_ids = {row.faculty_user_id for row in assignments}
        rows = UserRole.objects.filter(user_id__in=user_ids, role__code="FACULTY").select_related(
            "role", "tenant", "campus", "department"
        )
        result = defaultdict(list)
        for row in rows:
            result[row.user_id].append(row)
        return result

    @classmethod
    def _readiness_anomalies_for(cls, assignment, *, role_rows):
        anomalies = set()
        user = assignment.faculty_user
        offering = assignment.offering
        if not user.is_active:
            anomalies.add("inactive_user")
        if not user.has_usable_password():
            anomalies.add("unusable_password")
        if not role_rows:
            anomalies.add("faculty_membership_missing")
            return anomalies

        active_memberships = [row for row in role_rows if row.is_active]
        if not active_memberships:
            anomalies.add("faculty_membership_inactive")
            if any(not row.role.is_active for row in role_rows):
                anomalies.add("faculty_role_inactive")
            return anomalies
        active_roles = [row for row in active_memberships if row.role.is_active]
        if not active_roles:
            anomalies.add("faculty_role_inactive")
            return anomalies
        tenant_rows = [row for row in active_roles if row.tenant_id in (None, offering.tenant_id)]
        if not tenant_rows:
            anomalies.add("tenant_mismatch")
            return anomalies
        campus_rows = [row for row in tenant_rows if row.campus_id in (None, offering.campus_id)]
        if not campus_rows:
            anomalies.add("campus_mismatch")
            return anomalies
        # Match the existing Planning & Readiness Faculty Active contract:
        # a NULL department is global, otherwise the offering department must
        # match exactly. Do not introduce hierarchy expansion here.
        if not any(
            row.department_id in (None, offering.department_id)
            for row in campus_rows
        ):
            anomalies.add("department_mismatch")
        return anomalies

    @classmethod
    def _candidate_hash(cls, candidates, *, role_rows_by_user=None):
        candidates = tuple(candidates)
        role_rows_by_user = role_rows_by_user or cls._faculty_role_rows_by_user(candidates)
        canonical = [
            cls._candidate_state(
                row,
                readiness_anomalies=cls._readiness_anomalies_for(
                    row,
                    role_rows=role_rows_by_user.get(row.faculty_user_id, ()),
                ),
            )
            for row in sorted(candidates, key=lambda item: item.id)
        ]
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _candidate_state(assignment, *, readiness_anomalies=()):
        offering = assignment.offering
        course = offering.course
        section = offering.section
        program = offering.program
        return {
            "assignment": {
                "id": assignment.id,
                "tenant_id": assignment.tenant_id,
                "campus_id": assignment.campus_id,
                "faculty_user_id": assignment.faculty_user_id,
                "is_active": assignment.is_active,
                "response_status": assignment.response_status,
                "responded_at": assignment.responded_at.isoformat() if assignment.responded_at else None,
                "accepted_at": assignment.accepted_at.isoformat() if assignment.accepted_at else None,
                "accepted_by_id": assignment.accepted_by_id,
            },
            "faculty_readiness": {
                "user_active": assignment.faculty_user.is_active,
                "usable_password": assignment.faculty_user.has_usable_password(),
                "anomalies": sorted(readiness_anomalies),
            },
            "offering": {
                "id": offering.id,
                "tenant_id": offering.tenant_id,
                "campus_id": offering.campus_id,
                "department_id": offering.department_id,
                "academic_year_id": offering.academic_year_id,
                "term_id": offering.term_id,
                "course_id": offering.course_id,
                "section_id": offering.section_id,
                "program_id": offering.program_id,
                "is_active": offering.is_active,
                "status": offering.status,
            },
            "hierarchy": {
                "tenant_active": offering.tenant.is_active,
                "campus_tenant_id": offering.campus.tenant_id,
                "campus_active": offering.campus.is_active,
                "department_tenant_id": offering.department.tenant_id,
                "department_campus_id": offering.department.campus_id,
                "department_active": offering.department.is_active,
                "academic_year_tenant_id": offering.academic_year.tenant_id,
                "academic_year_active": offering.academic_year.is_active,
                "term_tenant_id": offering.term.tenant_id,
                "term_academic_year_id": offering.term.academic_year_id,
                "term_active": offering.term.is_active,
                "course_tenant_id": course.tenant_id,
                "course_active": course.is_active,
                "course_department_id": course.department_id,
                "course_department_active": (
                    course.department.is_active if course.department_id else None
                ),
                "course_exam_department_id": course.exam_department_id,
                "course_exam_department_tenant_id": (
                    course.exam_department.tenant_id if course.exam_department_id else None
                ),
                "course_exam_department_campus_id": (
                    course.exam_department.campus_id if course.exam_department_id else None
                ),
                "course_exam_department_active": (
                    course.exam_department.is_active if course.exam_department_id else None
                ),
                "section_tenant_id": section.tenant_id,
                "section_campus_id": section.campus_id,
                "section_department_id": section.department_id,
                "section_department_active": section.department.is_active,
                "section_active": section.is_active,
                "section_program_id": section.program_id,
                "section_program_active": section.program.is_active,
                "section_program_department_id": section.program.department_id,
                "section_program_department_active": section.program.department.is_active,
                "program_active": program.is_active if program else None,
                "program_department_id": program.department_id if program else None,
                "program_department_active": (
                    program.department.is_active if program else None
                ),
            },
        }

    @staticmethod
    def _acceptance_snapshot(assignment):
        return {
            "response_status": assignment.response_status,
            "faculty_response_note": assignment.faculty_response_note,
            "responded_at": assignment.responded_at,
            "accepted_at": assignment.accepted_at,
            "accepted_by_id": assignment.accepted_by_id,
            "response_due_at": assignment.response_due_at,
            "last_reminded_at": assignment.last_reminded_at,
            "reminder_count": assignment.reminder_count,
        }
