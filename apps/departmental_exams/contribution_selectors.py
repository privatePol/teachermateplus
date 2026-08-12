from __future__ import annotations

from django.db.models import Count, Prefetch, Q

from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService

from .contribution_authorization import ContributorEligibilityService
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    FacultyContribution,
    FacultyContributionEligibilitySource,
)
from .services import DepartmentalExamAuthorizationService


class ContributionSelector:
    @staticmethod
    def owner_queryset(*, user, tenant_id):
        return (
            FacultyContribution.objects.filter(
                faculty_user=user,
                cycle_course__cycle__tenant_id=tenant_id,
                cycle_course__cycle__tenant__is_active=True,
            )
            .select_related(
                "cycle_course",
                "cycle_course__cycle",
                "cycle_course__cycle__tenant",
                "cycle_course__course",
                "cycle_course__configuration",
            )
            .prefetch_related(
                "eligibility_sources",
                "cycle_course__offering_snapshots__campus",
                "cycle_course__offering_snapshots__offering",
            )
            .annotate(saved_question_count=Count("questions"))
            .order_by("cycle_course__cycle__exam_period", "cycle_course__course__code")
        )

    @classmethod
    def faculty_navigation_visible(cls, *, user, tenant_id, campus_id):
        if (
            not tenant_id
            or not user
            or not user.is_authenticated
            or not user.is_active
            or not FeatureSettingsService.is_departmental_exam_builder_enabled(
                tenant_id=tenant_id
            )
            or not PermissionService.has_permission(
                user,
                "faculty_portal.access",
                tenant_id=tenant_id,
                campus_id=campus_id,
            )
        ):
            return False
        if cls.owner_queryset(user=user, tenant_id=tenant_id).exists():
            return True
        return ContributorEligibilityService.has_any_eligible_source(
            user=user,
            tenant_id=tenant_id,
        )


class ContributionMonitoringSelector:
    @staticmethod
    def visible_cycle_courses(*, user, tenant_id):
        base = CycleCourse.objects.filter(cycle__tenant_id=tenant_id)
        configurer = DepartmentalExamAuthorizationService.configurer_visible_cycle_courses(
            user=user,
            tenant_id=tenant_id,
            queryset=base,
        )
        reviewer = DepartmentalExamAuthorizationService.reviewer_visible_cycle_courses(
            user=user,
            tenant_id=tenant_id,
            queryset=base,
        )
        visible_ids = configurer.values("pk").union(reviewer.values("pk"))
        automatic_courses = list(
            base.filter(
                cycle__processing_mode="AUTOMATIC_GENERATION",
                inclusion_status="INCLUDED",
            ).select_related("cycle").prefetch_related("offering_snapshots")
        )
        automatic_permissions = (
            DepartmentalExamAuthorizationService.automatic_permission_map(
                user=user,
                courses=automatic_courses,
                permissions=(
                    DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
                ),
            )
        )
        automatic_ids = {
            course.id
            for course in automatic_courses
            if DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
            in automatic_permissions[course.id]
        }
        contribution_sources = (
            FacultyContributionEligibilitySource.objects.select_related(
                "assignment",
                "assignment__offering",
                "assignment__offering__section",
            )
            .only(
                "id",
                "contribution_id",
                "assignment_id",
                "assignment_id_snapshot",
                "offering_id_snapshot",
                "tenant_id_snapshot",
                "campus_id_snapshot",
                "is_current",
                "assignment__id",
                "assignment__tenant_id",
                "assignment__campus_id",
                "assignment__offering_id",
                "assignment__offering__id",
                "assignment__offering__tenant_id",
                "assignment__offering__campus_id",
                "assignment__offering__course_id",
                "assignment__offering__academic_year_id",
                "assignment__offering__term_id",
                "assignment__offering__section_id",
                "assignment__offering__section__id",
                "assignment__offering__section__code",
            )
            .order_by("assignment_id_snapshot", "id")
        )
        contributions = (
            FacultyContribution.objects.select_related("faculty_user")
            .prefetch_related(
                Prefetch("eligibility_sources", queryset=contribution_sources),
                "blocked_resolution_events",
            )
            .annotate(saved_question_count=Count("questions"))
            .order_by("faculty_user__last_name", "faculty_user__first_name", "id")
        )
        offering_snapshots = CycleCourseOffering.objects.select_related(
            "campus"
        ).order_by("campus__name", "offering_id")
        return (
            base.filter(Q(pk__in=visible_ids) | Q(pk__in=automatic_ids))
            .select_related(
                "cycle",
                "cycle__tenant",
                "cycle__academic_year",
                "cycle__term",
                "course",
                "responsible_department",
                "configuration",
            )
            .prefetch_related(
                Prefetch("offering_snapshots", queryset=offering_snapshots),
                Prefetch("faculty_contributions", queryset=contributions),
            )
            .annotate(
                contribution_count=Count("faculty_contributions", distinct=True),
                question_count=Count("faculty_contributions__questions", distinct=True),
            )
            .order_by("cycle__academic_year__name", "cycle__term__name", "course__code")
        )

    @classmethod
    def navigation_visible(cls, *, user, tenant_id):
        if not FeatureSettingsService.is_departmental_exam_builder_enabled(
            tenant_id=tenant_id
        ):
            return False
        return cls.visible_cycle_courses(user=user, tenant_id=tenant_id).exists()
