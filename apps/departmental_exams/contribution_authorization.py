from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.rbac.models import UserPermission, UserRole

from .models import CourseExamConfiguration, CycleCourse, FacultyContribution


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
    def _structurally_valid(*, assignment, cycle_course, configuration):
        cycle = cycle_course.cycle
        offering = assignment.offering
        return bool(
            assignment.faculty_user.is_active
            and cycle.tenant.is_active
            and assignment.tenant_id is not None
            and assignment.campus_id is not None
            and assignment.tenant_id == cycle.tenant_id
            and assignment.tenant_id == offering.tenant_id
            and assignment.campus_id == offering.campus_id
            and assignment.campus.is_active
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
            and configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.OPEN
            and FeatureSettingsService.is_departmental_exam_builder_enabled(
                tenant_id=cycle.tenant_id
            )
        )

    @classmethod
    def _bulk_allowed_keys(cls, assignments):
        assignments = tuple(assignments)
        if not assignments:
            return set()
        user_ids = {item.faculty_user_id for item in assignments}
        tenant_ids = {item.tenant_id for item in assignments if item.tenant_id is not None}
        campus_ids = {item.campus_id for item in assignments if item.campus_id is not None}
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
    def source_inventory(cls, *, cycle_course, faculty_user_id=None):
        configuration = getattr(cycle_course, "configuration", None)
        assignments = tuple(
            cls._linked_assignments(
                cycle_course=cycle_course,
                faculty_user_id=faculty_user_id,
            )
        )
        allowed_keys = cls._bulk_allowed_keys(assignments)
        eligible = tuple(
            assignment
            for assignment in assignments
            if cls._structurally_valid(
                assignment=assignment,
                cycle_course=cycle_course,
                configuration=configuration,
            )
            and (
                assignment.faculty_user_id,
                assignment.tenant_id,
                assignment.campus_id,
            )
            in allowed_keys
        )
        return SourceInventory(all_sources=assignments, eligible_sources=eligible)

    @classmethod
    def source_is_eligible(cls, *, assignment, cycle_course):
        configuration = getattr(cycle_course, "configuration", None)
        if not cls._structurally_valid(
            assignment=assignment,
            cycle_course=cycle_course,
            configuration=configuration,
        ):
            return False
        if assignment.tenant_id is None or assignment.campus_id is None:
            return False
        return PermissionService.has_assigned_permission(
            assignment.faculty_user,
            cls.PORTAL_PERMISSION,
            tenant_id=assignment.tenant_id,
            campus_id=assignment.campus_id,
            exact_scope=True,
        )

    @classmethod
    def has_any_eligible_source(cls, *, user, tenant_id):
        """Bounded set-based counterpart for Faculty navigation visibility."""
        if not user or not user.is_authenticated or not user.is_active or not tenant_id:
            return False
        role_permission = UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=OuterRef("tenant_id"),
            campus_id=OuterRef("campus_id"),
            role__role_permissions__permission__code=cls.PORTAL_PERMISSION,
            role__role_permissions__permission__is_active=True,
        )
        direct = UserPermission.objects.filter(
            user=user,
            permission__code=cls.PORTAL_PERMISSION,
            permission__is_active=True,
            tenant_id=OuterRef("tenant_id"),
            campus_id=OuterRef("campus_id"),
        )
        return (
            FacultyAssignment.objects.filter(
                faculty_user=user,
                faculty_user__is_active=True,
                tenant_id=tenant_id,
                tenant__is_active=True,
                campus__is_active=True,
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                accepted_at__isnull=False,
                offering__tenant_id=F("tenant_id"),
                offering__campus_id=F("campus_id"),
                offering__tenant__is_active=True,
                offering__campus__is_active=True,
                offering__is_active=True,
                offering__status=CourseOffering.Status.OPEN,
                offering__exam_cycle_snapshots__campus_id=F("offering__campus_id"),
                offering__exam_cycle_snapshots__cycle_course__course_id=F("offering__course_id"),
                offering__exam_cycle_snapshots__cycle_course__cycle__tenant_id=F("tenant_id"),
                offering__exam_cycle_snapshots__cycle_course__cycle__academic_year_id=F("offering__academic_year_id"),
                offering__exam_cycle_snapshots__cycle_course__cycle__term_id=F("offering__term_id"),
                offering__exam_cycle_snapshots__cycle_course__cycle__status="OPEN",
                offering__exam_cycle_snapshots__cycle_course__inclusion_status="INCLUDED",
                offering__exam_cycle_snapshots__cycle_course__configuration__workflow_status="OPEN",
            )
            .annotate(
                has_role_permission=Exists(role_permission),
                has_direct_allow=Exists(
                    direct.filter(grant_type=UserPermission.GrantType.ALLOW)
                ),
                has_direct_deny=Exists(
                    direct.filter(grant_type=UserPermission.GrantType.DENY)
                ),
            )
            .filter(has_direct_deny=False)
            .filter(Q(has_role_permission=True) | Q(has_direct_allow=True))
            .exists()
        )

    @classmethod
    def qualifying_sources_by_user(cls, *, cycle_course):
        grouped = defaultdict(list)
        for assignment in cls.source_inventory(cycle_course=cycle_course).eligible_sources:
            grouped[assignment.faculty_user_id].append(assignment)
        return grouped


class ContributionAuthorizationService:
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
        eligible_source_keys = {
            (
                assignment.id,
                assignment.offering_id,
                assignment.tenant_id,
                assignment.campus_id,
            )
            for assignment in inventory.eligible_sources
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
        if configuration.contribution_deadline is None:
            raise PermissionDenied("A contribution deadline is required.")
        if timezone.now() >= configuration.contribution_deadline:
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
