from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.rbac.models import UserPermission, UserRole

from .models import (
    CourseExamConfiguration,
    CycleCourse,
    FacultyContribution,
    QuestionImportBatch,
)


class ContributionConflict(ValidationError):
    """A stale Stage 5 write that callers should expose as HTTP 409."""


class ContributionQuotaReached(ContributionConflict):
    """An add operation denied because the exact contribution quota is full."""

    def __init__(self, quota):
        self.quota = quota
        super().__init__(
            f"The required quota of {quota} questions has already been reached."
        )


class ContributionExpired(ValidationError):
    """An expired confidential preview that callers should expose as HTTP 410."""


@dataclass(frozen=True)
class SourceInventory:
    all_sources: tuple
    eligible_sources: tuple


class ContributorEligibilityService:
    PORTAL_PERMISSION = "faculty_portal.access"

    @staticmethod
    def _effective_scope(assignment):
        """Return the exact assignment scope, including the approved legacy fallback."""
        assignment_scope = (assignment.tenant_id, assignment.campus_id)
        offering_scope = (assignment.offering.tenant_id, assignment.offering.campus_id)
        if assignment_scope == (None, None):
            return offering_scope
        if None in assignment_scope or assignment_scope != offering_scope:
            return None
        return assignment_scope

    @staticmethod
    def _linked_assignments(*, cycle_course, faculty_user_id=None):
        queryset = (
            FacultyAssignment.objects.filter(
                offering__exam_cycle_snapshots__cycle_course_id=cycle_course.pk,
                offering__exam_cycle_snapshots__campus_id=F("offering__campus_id"),
            )
            .select_related(
                "faculty_user",
                "tenant",
                "campus",
                "offering",
                "offering__tenant",
                "offering__campus",
                "offering__academic_year",
                "offering__term",
                "offering__course",
            )
            .order_by("faculty_user_id", "id")
            .distinct()
        )
        if faculty_user_id is not None:
            queryset = queryset.filter(faculty_user_id=faculty_user_id)
        return queryset

    @staticmethod
    def _structurally_valid(
        *,
        assignment,
        cycle_course,
        configuration,
        allow_closed_contribution=False,
        allow_draft_configuration=False,
    ):
        cycle = cycle_course.cycle
        offering = assignment.offering
        effective_scope = ContributorEligibilityService._effective_scope(assignment)
        return bool(
            assignment.faculty_user.is_active
            and cycle.tenant.is_active
            and effective_scope is not None
            and effective_scope[0] == cycle.tenant_id
            and offering.tenant.is_active
            and offering.campus.is_active
            and offering.is_active
            and offering.status == CourseOffering.Status.OPEN
            and assignment.is_active
            and assignment.response_status == FacultyAssignment.ResponseStatus.ACCEPTED
            and assignment.accepted_at is not None
            and offering.course_id == cycle_course.course_id
            and offering.academic_year_id == cycle.academic_year_id
            and offering.term_id == cycle.term_id
            and cycle_course.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
            and cycle.status == cycle.Status.OPEN
            and configuration is not None
            and configuration.workflow_status
            in (
                (
                    CourseExamConfiguration.WorkflowStatus.OPEN,
                    CourseExamConfiguration.WorkflowStatus.CLOSED,
                )
                if allow_closed_contribution
                else (
                    (
                        CourseExamConfiguration.WorkflowStatus.DRAFT,
                        CourseExamConfiguration.WorkflowStatus.OPEN,
                    )
                    if allow_draft_configuration
                    else (CourseExamConfiguration.WorkflowStatus.OPEN,)
                )
            )
            and FeatureSettingsService.is_departmental_exam_builder_enabled(
                tenant_id=cycle.tenant_id
            )
        )

    @classmethod
    def _bulk_allowed_keys(cls, assignments):
        assignments = tuple(assignments)
        scoped_assignments = []
        for item in assignments:
            effective_scope = cls._effective_scope(item)
            if effective_scope is not None:
                scoped_assignments.append((item, effective_scope))
        if not scoped_assignments:
            return set()
        user_ids = {item.faculty_user_id for item, _scope in scoped_assignments}
        tenant_ids = {scope[0] for _item, scope in scoped_assignments}
        campus_ids = {scope[1] for _item, scope in scoped_assignments}
        role_keys = set(
            UserRole.objects.filter(
                user_id__in=user_ids,
                tenant_id__in=tenant_ids,
                campus_id__in=campus_ids,
                is_active=True,
                role__is_active=True,
                role__role_permissions__permission__code=cls.PORTAL_PERMISSION,
                role__role_permissions__permission__is_active=True,
            ).values_list("user_id", "tenant_id", "campus_id")
        )
        direct = UserPermission.objects.filter(
            user_id__in=user_ids,
            tenant_id__in=tenant_ids,
            campus_id__in=campus_ids,
            permission__code=cls.PORTAL_PERMISSION,
            permission__is_active=True,
        ).values_list("user_id", "tenant_id", "campus_id", "grant_type")
        allows = set()
        denies = set()
        for user_id, tenant_id, campus_id, grant_type in direct:
            key = (user_id, tenant_id, campus_id)
            if grant_type == UserPermission.GrantType.DENY:
                denies.add(key)
            else:
                allows.add(key)
        return (role_keys | allows) - denies

    @classmethod
    def source_inventory(
        cls,
        *,
        cycle_course,
        faculty_user_id=None,
        allow_closed_contribution=False,
        allow_draft_configuration=False,
    ):
        configuration = getattr(cycle_course, "configuration", None)
        assignments = tuple(
            cls._linked_assignments(
                cycle_course=cycle_course,
                faculty_user_id=faculty_user_id,
            )
        )
        allowed_keys = cls._bulk_allowed_keys(assignments)
        eligible = []
        for assignment in assignments:
            effective_scope = cls._effective_scope(assignment)
            if (
                effective_scope is not None
                and cls._structurally_valid(
                    assignment=assignment,
                    cycle_course=cycle_course,
                    configuration=configuration,
                    allow_closed_contribution=allow_closed_contribution,
                    allow_draft_configuration=allow_draft_configuration,
                )
                and (assignment.faculty_user_id, *effective_scope) in allowed_keys
            ):
                eligible.append(assignment)
        return SourceInventory(all_sources=assignments, eligible_sources=tuple(eligible))

    @classmethod
    def print_source_inventory(cls, *, cycle_course, faculty_user_id=None):
        """Return current teaching assignments without reopening contribution lifecycle.

        Questionnaire printing is governed by current assignment ownership and
        its own release window. A closed contribution intake or examination
        cycle therefore must not make a still-current teaching assignment
        historical, while tenant/course/offering scope and direct-deny portal
        authority remain fail-closed.
        """
        assignments = tuple(
            cls._linked_assignments(
                cycle_course=cycle_course,
                faculty_user_id=faculty_user_id,
            )
        )
        allowed_keys = cls._bulk_allowed_keys(assignments)
        eligible = []
        cycle = cycle_course.cycle
        for assignment in assignments:
            effective_scope = cls._effective_scope(assignment)
            offering = assignment.offering
            if (
                effective_scope is not None
                and assignment.faculty_user.is_active
                and cycle.tenant.is_active
                and effective_scope[0] == cycle.tenant_id
                and offering.tenant.is_active
                and offering.campus.is_active
                and offering.is_active
                and offering.status == CourseOffering.Status.OPEN
                and assignment.is_active
                and assignment.response_status
                == FacultyAssignment.ResponseStatus.ACCEPTED
                and assignment.accepted_at is not None
                and offering.course_id == cycle_course.course_id
                and offering.academic_year_id == cycle.academic_year_id
                and offering.term_id == cycle.term_id
                and cycle_course.inclusion_status
                == CycleCourse.InclusionStatus.INCLUDED
                and FeatureSettingsService.is_departmental_exam_builder_enabled(
                    tenant_id=cycle.tenant_id
                )
                and (assignment.faculty_user_id, *effective_scope) in allowed_keys
            ):
                eligible.append(assignment)
        return SourceInventory(
            all_sources=assignments,
            eligible_sources=tuple(eligible),
        )

    @classmethod
    def preparation_source_inventory(cls, *, cycle_course):
        """Evaluate faculty eligibility for a Draft row as if it were opened now."""
        return cls.source_inventory(
            cycle_course=cycle_course,
            allow_draft_configuration=True,
        )

    @classmethod
    def source_is_eligible(cls, *, assignment, cycle_course):
        configuration = getattr(cycle_course, "configuration", None)
        if not cls._structurally_valid(
            assignment=assignment,
            cycle_course=cycle_course,
            configuration=configuration,
        ):
            return False
        effective_scope = cls._effective_scope(assignment)
        if effective_scope is None:
            return False
        return PermissionService.has_assigned_permission(
            assignment.faculty_user,
            cls.PORTAL_PERMISSION,
            tenant_id=effective_scope[0],
            campus_id=effective_scope[1],
            exact_scope=True,
        )

    @classmethod
    def has_any_eligible_source(cls, *, user, tenant_id):
        """Bounded set-based counterpart for Faculty navigation visibility."""
        if not user or not user.is_authenticated or not user.is_active or not tenant_id:
            return False
        assignments = tuple(
            FacultyAssignment.objects.filter(
                faculty_user=user,
                faculty_user__is_active=True,
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                accepted_at__isnull=False,
                offering__tenant_id=tenant_id,
                offering__tenant__is_active=True,
                offering__campus__is_active=True,
                offering__is_active=True,
                offering__status=CourseOffering.Status.OPEN,
                offering__exam_cycle_snapshots__campus_id=F("offering__campus_id"),
                offering__exam_cycle_snapshots__cycle_course__course_id=F("offering__course_id"),
                offering__exam_cycle_snapshots__cycle_course__cycle__tenant_id=F("offering__tenant_id"),
                offering__exam_cycle_snapshots__cycle_course__cycle__academic_year_id=F("offering__academic_year_id"),
                offering__exam_cycle_snapshots__cycle_course__cycle__term_id=F("offering__term_id"),
                offering__exam_cycle_snapshots__cycle_course__cycle__status="OPEN",
                offering__exam_cycle_snapshots__cycle_course__inclusion_status="INCLUDED",
                offering__exam_cycle_snapshots__cycle_course__configuration__workflow_status="OPEN",
            )
            .select_related(
                "tenant",
                "campus",
                "offering",
                "offering__tenant",
                "offering__campus",
            )
            .distinct()
        )
        allowed_keys = cls._bulk_allowed_keys(assignments)
        return any(
            effective_scope is not None
            and effective_scope[0] == tenant_id
            and (assignment.faculty_user_id, *effective_scope) in allowed_keys
            for assignment in assignments
            for effective_scope in (cls._effective_scope(assignment),)
        )

    @classmethod
    def qualifying_sources_by_user(cls, *, cycle_course):
        grouped = defaultdict(list)
        for assignment in cls.source_inventory(cycle_course=cycle_course).eligible_sources:
            grouped[assignment.faculty_user_id].append(assignment)
        return grouped


class ContributionAuthorizationService:
    @staticmethod
    def require_no_active_import(*, contribution):
        if QuestionImportBatch.objects.filter(
            contribution=contribution,
            status__in=QuestionImportBatch.active_statuses(),
        ).exists():
            raise PermissionDenied(
                "An interrupted question import must be completed before other changes are allowed."
            )

    @staticmethod
    def _retained_source_intersects(*, contribution, assignments):
        eligible_source_keys = {
            (
                assignment.id,
                assignment.offering_id,
                *ContributorEligibilityService._effective_scope(assignment),
            )
            for assignment in assignments
        }
        if not eligible_source_keys:
            return False
        retained_current_keys = {
            (
                source.assignment_id_snapshot,
                source.offering_id_snapshot,
                source.tenant_id_snapshot,
                source.campus_id_snapshot,
            )
            for source in contribution.eligibility_sources.all()
            if source.is_current
        }
        return bool(retained_current_keys & eligible_source_keys)

    @staticmethod
    def has_retained_live_eligibility(*, contribution):
        """Return whether a retained current source is still eligible live.

        This is deliberately read-only so GET callers can enforce the same
        source intersection as mutation authorization without synchronizing
        or otherwise rewriting the contributor roster.
        """
        inventory = ContributorEligibilityService.source_inventory(
            cycle_course=contribution.cycle_course,
            faculty_user_id=contribution.faculty_user_id,
        )
        return ContributionAuthorizationService._retained_source_intersects(
            contribution=contribution,
            assignments=inventory.eligible_sources,
        )

    @staticmethod
    def has_retained_current_print_eligibility(*, contribution):
        inventory = ContributorEligibilityService.print_source_inventory(
            cycle_course=contribution.cycle_course,
            faculty_user_id=contribution.faculty_user_id,
        )
        return ContributionAuthorizationService._retained_source_intersects(
            contribution=contribution,
            assignments=inventory.eligible_sources,
        )

    @staticmethod
    def require_common_read_access(*, user, tenant, request_tenant_id, request_campus_id):
        if (
            not user
            or not user.is_authenticated
            or not user.is_active
            or not tenant
            or not tenant.is_active
            or request_tenant_id != tenant.id
        ):
            raise PermissionDenied("Faculty contribution access is unavailable.")
        if not FeatureSettingsService.is_departmental_exam_builder_enabled(
            tenant_id=tenant.id
        ):
            raise PermissionDenied("Departmental Exam Builder is not enabled.")
        if not PermissionService.has_permission(
            user,
            "faculty_portal.access",
            tenant_id=request_tenant_id,
            campus_id=request_campus_id,
        ):
            raise PermissionDenied("Faculty Portal access is required.")

    @classmethod
    def require_mutable_locked(
        cls,
        *,
        contribution,
        configuration,
        request_tenant_id,
        request_campus_id,
    ):
        cycle_course = contribution.cycle_course
        cycle = cycle_course.cycle
        cls.require_common_read_access(
            user=contribution.faculty_user,
            tenant=cycle.tenant,
            request_tenant_id=request_tenant_id,
            request_campus_id=request_campus_id,
        )
        if contribution.status != FacultyContribution.Status.DRAFT:
            raise PermissionDenied("Submitted contributions are read-only.")
        if contribution.roster_status != FacultyContribution.RosterStatus.ACTIVE:
            raise PermissionDenied("This contribution is blocked and read-only.")
        if cycle.status != cycle.Status.OPEN:
            raise PermissionDenied("The examination cycle is not open.")
        if cycle_course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED:
            raise PermissionDenied("Exempt course examinations are read-only.")
        if (
            configuration is None
            or configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.OPEN
        ):
            raise PermissionDenied("Faculty contribution is not open.")
        if not 50 <= contribution.quota_snapshot <= 75:
            raise PermissionDenied("A valid contribution quota is required.")
        deadline = configuration.active_contribution_deadline
        if deadline is None:
            raise PermissionDenied("A contribution deadline is required.")
        if timezone.now() >= deadline:
            raise PermissionDenied("The contribution deadline has passed.")
        if not cls.has_retained_live_eligibility(contribution=contribution):
            raise PermissionDenied("No current qualifying teaching assignment remains.")

    @staticmethod
    def require_revision(*, contribution, expected_revision):
        if expected_revision is None or contribution.revision != expected_revision:
            raise ContributionConflict(
                "This contribution changed after the page was loaded. Review the latest state and try again."
            )

    @staticmethod
    def require_add_capacity(*, contribution, question_count):
        if question_count >= contribution.quota_snapshot:
            raise ContributionQuotaReached(contribution.quota_snapshot)
