from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.accounts.models import User
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.rbac.models import Permission, UserPermission, UserRole
from apps.tenants.models import Department

from .models import (
    CycleCourse,
    CycleCourseOffering,
    ExaminationCycle,
    FacultyContribution,
    Question,
)


class DepartmentalExamAuthorizationService:
    """Authorization rules that intentionally do not inherit null-department expansion."""

    CONFIGURE_PERMISSION = "departmental_exams.configure"
    REVIEWER_PERMISSION = "departmental_exams.review_generate"

    @staticmethod
    def _effective_permission_annotations(
        *, user, permission_code, tenant_id, campus_id
    ):
        """Build set-based annotations equivalent to ``PermissionService``.

        ``PermissionService`` uses ``__in=[current_id, None]`` for scoped
        permissions. Django removes ``None`` from that lookup, so a concrete
        current tenant/campus requires an exact permission-bearing scope.
        Departmental Exam ownership membership remains a separate
        exact-department check in the calling queryset.
        """
        role_permission = (
            UserRole.objects.filter(
                user=user,
                is_active=True,
                role__is_active=True,
                role__role_permissions__permission__code=permission_code,
                role__role_permissions__permission__is_active=True,
                tenant_id=tenant_id,
                campus_id=campus_id,
            )
        )
        user_permissions = (
            UserPermission.objects.filter(
                user=user,
                permission__code=permission_code,
                permission__is_active=True,
                tenant_id=tenant_id,
                campus_id=campus_id,
            )
        )
        return {
            "has_role_permission": Exists(role_permission),
            "has_direct_allow": Exists(
                user_permissions.filter(grant_type=UserPermission.GrantType.ALLOW)
            ),
            "has_direct_deny": Exists(
                user_permissions.filter(grant_type=UserPermission.GrantType.DENY)
            ),
        }

    @staticmethod
    def require_enabled(*, tenant_id):
        if not FeatureSettingsService.is_departmental_exam_builder_enabled(tenant_id=tenant_id):
            raise PermissionDenied("Departmental Exam Builder is not enabled.")

    @classmethod
    def require_permission(cls, *, user, permission, tenant_id, campus_id=None):
        cls.require_enabled(tenant_id=tenant_id)
        if not PermissionService.has_permission(
            user, permission, tenant_id=tenant_id, campus_id=campus_id
        ):
            raise PermissionDenied("You do not have Departmental Exam Builder permission.")

    @classmethod
    def _exact_department_memberships(cls, *, user, tenant_id, responsible_department):
        """Return active ownership memberships for one exact exam department.

        Departmental Exam Builder deliberately does not inherit parent/child
        department scope.  A null-campus membership can represent the same
        department across campus selection, but permission is still evaluated
        at the responsible department's actual campus.
        """
        if not responsible_department or not responsible_department.is_active:
            return UserRole.objects.none()
        return UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=responsible_department.id,
            user__is_active=True,
        ).filter(
            Q(campus_id=responsible_department.campus_id)
            | Q(campus_id__isnull=True)
        )

    @classmethod
    def is_eligible_configurer(cls, *, user, tenant_id, responsible_department):
        if not user or not user.is_active or not responsible_department:
            return False
        if user.is_superuser:
            return True
        if not cls._exact_department_memberships(
            user=user,
            tenant_id=tenant_id,
            responsible_department=responsible_department,
        ).exists():
            return False
        return PermissionService.has_permission(
            user,
            cls.CONFIGURE_PERMISSION,
            tenant_id=tenant_id,
            campus_id=responsible_department.campus_id,
        )

    @classmethod
    def configurable_departments(cls, *, user, tenant_id):
        """Return departments a user can configure without hierarchy expansion."""
        departments = Department.objects.filter(tenant_id=tenant_id, is_active=True)
        if not user or not user.is_authenticated or not user.is_active:
            return departments.none()
        if user.is_superuser:
            return departments.order_by("campus__name", "name", "code")

        campus_id = OuterRef("campus_id")
        exact_membership = UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=OuterRef("pk"),
        ).filter(Q(campus_id=campus_id) | Q(campus_id__isnull=True))
        return (
            departments.annotate(
                has_exact_membership=Exists(exact_membership),
                **cls._effective_permission_annotations(
                    user=user,
                    permission_code=cls.CONFIGURE_PERMISSION,
                    tenant_id=tenant_id,
                    campus_id=campus_id,
                ),
            )
            .filter(
                has_exact_membership=True,
                has_direct_deny=False,
            )
            .filter(Q(has_role_permission=True) | Q(has_direct_allow=True))
            .order_by("campus__name", "name", "code")
        )

    @classmethod
    def require_configure_cycle_course(cls, *, user, cycle_course):
        """Authorize grouped-course administration, not cycle management."""
        tenant_id = cycle_course.cycle.tenant_id
        cls.require_enabled(tenant_id=tenant_id)
        if not user or not user.is_authenticated or not user.is_active:
            raise PermissionDenied("An active user is required for course administration.")
        if user.is_superuser:
            return
        if not cycle_course.responsible_department_id:
            raise PermissionDenied(
                "Only a superuser may assign the initial exam department."
            )
        if not cls.is_eligible_configurer(
            user=user,
            tenant_id=tenant_id,
            responsible_department=cycle_course.responsible_department,
        ):
            raise PermissionDenied(
                "Course examination is outside your configure scope."
            )

    @classmethod
    def configurer_visible_cycle_courses(
        cls,
        *,
        user,
        cycle=None,
        tenant_id=None,
        queryset=None,
        include_null_for_superuser=True,
    ):
        """Return exact-scope grouped courses a user may administer.

        ``cycle`` supports the existing per-cycle route.  The role-aware
        assigned-course landing page supplies ``tenant_id`` to use the same
        set-based policy across cycles.  This intentionally has no department
        hierarchy expansion.
        """
        if not user or not user.is_authenticated or not user.is_active:
            return CycleCourse.objects.none()

        tenant_id = cycle.tenant_id if cycle is not None else tenant_id
        if tenant_id is None:
            return CycleCourse.objects.none()
        queryset = queryset if queryset is not None else CycleCourse.objects.all()
        cycle_filter = {"cycle": cycle} if cycle is not None else {"cycle__tenant_id": tenant_id}
        if user.is_superuser:
            courses = queryset.filter(**cycle_filter)
            if not include_null_for_superuser:
                courses = courses.filter(
                    responsible_department__isnull=False,
                    responsible_department__is_active=True,
                )
            else:
                courses = courses.filter(
                    Q(responsible_department__isnull=True)
                    | Q(responsible_department__is_active=True)
                )
            return courses

        campus_id = OuterRef("responsible_department__campus_id")
        exact_membership = UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=OuterRef("responsible_department_id"),
        ).filter(Q(campus_id=campus_id) | Q(campus_id__isnull=True))
        return (
            queryset.filter(
                **cycle_filter,
                responsible_department__isnull=False,
                responsible_department__is_active=True,
            )
            .annotate(
                has_exact_configurer_membership=Exists(exact_membership),
                **cls._effective_permission_annotations(
                    user=user,
                    permission_code=cls.CONFIGURE_PERMISSION,
                    tenant_id=tenant_id,
                    campus_id=campus_id,
                ),
            )
            .filter(
                has_exact_configurer_membership=True,
                has_direct_deny=False,
            )
            .filter(Q(has_role_permission=True) | Q(has_direct_allow=True))
        )

    @classmethod
    def _reviewer_scope_memberships(cls, *, tenant_id, responsible_department):
        """Return active memberships for the exact responsible department."""
        if not responsible_department or not responsible_department.is_active:
            return UserRole.objects.none()
        return UserRole.objects.filter(
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=responsible_department.id,
            user__is_active=True,
        ).filter(
            Q(campus_id=responsible_department.campus_id)
            | Q(campus_id__isnull=True)
        )

    @classmethod
    def is_eligible_reviewer(cls, *, user, tenant_id, responsible_department):
        if not user or not user.is_active or not responsible_department:
            return False
        if not cls._reviewer_scope_memberships(
            tenant_id=tenant_id,
            responsible_department=responsible_department,
        ).filter(user=user).exists():
            return False
        return PermissionService.has_permission(
            user,
            cls.REVIEWER_PERMISSION,
            tenant_id=tenant_id,
            campus_id=responsible_department.campus_id,
        )

    @classmethod
    def eligible_reviewers(cls, *, tenant_id, responsible_department):
        if not responsible_department:
            return User.objects.none()
        campus_id = responsible_department.campus_id
        candidate_memberships = cls._reviewer_scope_memberships(
            tenant_id=tenant_id,
            responsible_department=responsible_department,
        )
        candidate_ids = candidate_memberships.values("user_id")

        return (
            User.objects.filter(id__in=candidate_ids, is_active=True)
            .annotate(
                **cls._effective_permission_annotations(
                    user=OuterRef("pk"),
                    permission_code=cls.REVIEWER_PERMISSION,
                    tenant_id=tenant_id,
                    campus_id=campus_id,
                ),
            )
            .filter(
                Q(is_superuser=True)
                | (
                    Q(has_direct_deny=False)
                    & (Q(has_role_permission=True) | Q(has_direct_allow=True))
                )
            )
            .order_by("last_name", "first_name", "username")
        )

    @classmethod
    def reviewer_visible_cycle_courses(
        cls, *, user, cycle=None, tenant_id=None, queryset=None
    ):
        """Return assigned CycleCourses that satisfy current reviewer eligibility.

        This is the set-based counterpart of ``is_eligible_reviewer`` for the
        routed reviewer list.  It deliberately uses the exact responsible
        department and responsible-campus permission semantics rather than the
        broader department hierarchy used for manager scope.
        """
        if not user or not user.is_authenticated or not user.is_active:
            return CycleCourse.objects.none()

        tenant_id = cycle.tenant_id if cycle is not None else tenant_id
        if tenant_id is None:
            return CycleCourse.objects.none()
        queryset = queryset if queryset is not None else CycleCourse.objects.all()
        cycle_filter = {"cycle": cycle} if cycle is not None else {"cycle__tenant_id": tenant_id}
        campus_id = OuterRef("responsible_department__campus_id")
        exact_membership = UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id=OuterRef("responsible_department_id"),
        ).filter(Q(campus_id=campus_id) | Q(campus_id__isnull=True))

        courses = queryset.filter(
            **cycle_filter,
            reviewer=user,
            responsible_department__isnull=False,
            responsible_department__is_active=True,
        ).annotate(has_exact_reviewer_membership=Exists(exact_membership))

        if user.is_superuser:
            if not Permission.objects.filter(
                code=cls.REVIEWER_PERMISSION,
                is_active=True,
            ).exists():
                return CycleCourse.objects.none()
            return courses.filter(has_exact_reviewer_membership=True)

        return courses.annotate(
            **cls._effective_permission_annotations(
                user=user,
                permission_code=cls.REVIEWER_PERMISSION,
                tenant_id=tenant_id,
                campus_id=campus_id,
            ),
        ).filter(
            has_exact_reviewer_membership=True,
            has_direct_deny=False,
        ).filter(Q(has_role_permission=True) | Q(has_direct_allow=True))

    @classmethod
    def require_course_responsibility(cls, *, user, cycle_course, permission=None):
        tenant_id = cycle_course.cycle.tenant_id
        cls.require_enabled(tenant_id=tenant_id)
        if cycle_course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED:
            raise PermissionDenied("Exempt course examinations are read-only.")
        if cycle_course.reviewer_id != user.id:
            raise PermissionDenied("You are not the assigned reviewer for this course examination.")
        if not cls.is_eligible_reviewer(
            user=user,
            tenant_id=tenant_id,
            responsible_department=cycle_course.responsible_department,
        ):
            raise PermissionDenied("Reviewer eligibility is no longer valid for this course examination.")
        required_permission = permission or cls.REVIEWER_PERMISSION
        if required_permission != cls.REVIEWER_PERMISSION:
            cls.require_permission(
                user=user,
                permission=required_permission,
                tenant_id=tenant_id,
                campus_id=cycle_course.responsible_department.campus_id,
            )


class CycleCourseTransitionConflict(ValidationError):
    """Raised when a confirmation page no longer represents the current row."""


class CycleCourseInclusionService:
    REASON_MIN_LENGTH = 10
    REASON_MAX_LENGTH = 500

    @staticmethod
    def transition_token(cycle_course):
        return cycle_course.updated_at.isoformat()

    @classmethod
    def require_included(cls, *, cycle_course):
        """Instance-only assertion; callers must already own any required lock."""
        if cycle_course.inclusion_status != CycleCourse.InclusionStatus.INCLUDED:
            raise PermissionDenied("Exempt course examinations cannot enter this workflow.")

    @classmethod
    def lock_included_cycle_course(cls, *, cycle_course_id, tenant_id):
        """Lock and return an Included parent for a downstream write transaction.

        Future downstream writers must call this inside ``transaction.atomic()``
        before creating children, preserving the parent-first lock order used by
        inclusion transitions.
        """
        if not transaction.get_connection().in_atomic_block:
            raise RuntimeError(
                "lock_included_cycle_course() must be called inside transaction.atomic()."
            )
        cycle_course = cls._locked_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        cls.require_included(cycle_course=cycle_course)
        return cycle_course

    @classmethod
    def _locked_cycle_course(cls, *, cycle_course_id, tenant_id):
        return (
            CycleCourse.objects.select_for_update()
            .select_related(
                "cycle",
                "cycle__tenant",
                "course",
                "responsible_department",
                "responsible_department__campus",
                "reviewer",
            )
            .get(id=cycle_course_id, cycle__tenant_id=tenant_id)
        )

    @classmethod
    def _validate_reason(cls, reason):
        cleaned = (reason or "").strip()
        if not cls.REASON_MIN_LENGTH <= len(cleaned) <= cls.REASON_MAX_LENGTH:
            raise ValidationError(
                f"Reason must be from {cls.REASON_MIN_LENGTH} to {cls.REASON_MAX_LENGTH} characters."
            )
        return cleaned

    @classmethod
    def _require_current_confirmation(cls, *, cycle_course, expected_updated_at):
        if expected_updated_at != cls.transition_token(cycle_course):
            raise CycleCourseTransitionConflict(
                "The course examination changed after this page was loaded. Review the latest state and try again."
            )

    @staticmethod
    def _require_draft_active_department(*, cycle_course):
        if cycle_course.cycle.status != ExaminationCycle.Status.DRAFT:
            raise ValidationError(
                "Only Draft examination cycles can change course inclusion."
            )
        if (
            cycle_course.responsible_department_id
            and not cycle_course.responsible_department.is_active
        ):
            raise ValidationError(
                "The responsible exam department is inactive. Reactivate or reassign it before changing inclusion."
            )

    @classmethod
    def _require_exempt_transition_window(cls, *, cycle_course):
        cls._require_draft_active_department(cycle_course=cycle_course)
        if FacultyContribution.objects.filter(cycle_course=cycle_course).exists():
            raise ValidationError(
                "This course has downstream faculty contribution data and cannot change inclusion."
            )
        if Question.objects.filter(contribution__cycle_course=cycle_course).exists():
            raise ValidationError(
                "This course has downstream examination question data and cannot change inclusion."
            )

    @classmethod
    def _require_restore_transition_window(cls, *, cycle_course):
        """A dormant configuration is preserved and does not block restoration."""
        cls._require_draft_active_department(cycle_course=cycle_course)

    @staticmethod
    def _state_payload(cycle_course):
        return {
            "inclusion_status": cycle_course.inclusion_status,
            "exemption_category": cycle_course.exemption_category,
            "exemption_reason": cycle_course.exemption_reason,
            "exemption_changed_by_id": cycle_course.exemption_changed_by_id,
            "exemption_changed_at": cycle_course.exemption_changed_at,
            "reviewer_id": cycle_course.reviewer_id,
        }

    @classmethod
    def _audit_transition(
        cls,
        *,
        action,
        cycle_course,
        actor,
        reason,
        before,
        reviewer_revalidated,
        reviewer_cleared,
        expected_updated_at,
        request,
    ):
        offering_rows = list(
            cycle_course.offering_snapshots.values_list("id", "campus_id")
        )
        offering_campus_ids = sorted({campus_id for _id, campus_id in offering_rows})
        AuditService.log_event(
            action=action,
            portal="ADMIN",
            entity_type="CycleCourse",
            entity_id=cycle_course.id,
            actor=actor,
            tenant=cycle_course.cycle.tenant_id,
            campus=(
                cycle_course.responsible_department.campus_id
                if cycle_course.responsible_department_id
                else None
            ),
            before_data=before,
            after_data=cls._state_payload(cycle_course),
            metadata={
                "cycle_id": cycle_course.cycle_id,
                "course_id": cycle_course.course_id,
                "responsible_department_id": cycle_course.responsible_department_id,
                "responsible_department_campus_id": (
                    cycle_course.responsible_department.campus_id
                    if cycle_course.responsible_department_id
                    else None
                ),
                "offering_count": len(offering_rows),
                "offering_campus_ids": offering_campus_ids,
                "transition_reason": reason,
                "reviewer_revalidated": reviewer_revalidated,
                "reviewer_cleared": reviewer_cleared,
                "expected_updated_at": expected_updated_at,
            },
            request=request,
        )

    @classmethod
    @transaction.atomic
    def exempt(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        user,
        exemption_category,
        reason,
        expected_updated_at,
        request=None,
    ):
        cycle_course = cls._locked_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        DepartmentalExamAuthorizationService.require_configure_cycle_course(
            user=user,
            cycle_course=cycle_course,
        )
        if cycle_course.inclusion_status == CycleCourse.InclusionStatus.EXEMPT:
            return cycle_course, False
        cls._require_current_confirmation(
            cycle_course=cycle_course,
            expected_updated_at=expected_updated_at,
        )
        cls._require_exempt_transition_window(cycle_course=cycle_course)
        reason = cls._validate_reason(reason)
        if exemption_category not in CycleCourse.ExemptionCategory.values:
            raise ValidationError("Select an approved exemption category.")

        before = cls._state_payload(cycle_course)
        cycle_course.inclusion_status = CycleCourse.InclusionStatus.EXEMPT
        cycle_course.exemption_category = exemption_category
        cycle_course.exemption_reason = reason
        cycle_course.exemption_changed_by = user
        cycle_course.exemption_changed_at = timezone.now()
        cycle_course.full_clean()
        cycle_course.save(
            update_fields=[
                "inclusion_status",
                "exemption_category",
                "exemption_reason",
                "exemption_changed_by",
                "exemption_changed_at",
                "updated_at",
            ]
        )
        cls._audit_transition(
            action="DE_EXAM_CYCLE_COURSE_EXEMPTED",
            cycle_course=cycle_course,
            actor=user,
            reason=reason,
            before=before,
            reviewer_revalidated=False,
            reviewer_cleared=False,
            expected_updated_at=expected_updated_at,
            request=request,
        )
        return cycle_course, True

    @classmethod
    @transaction.atomic
    def restore(
        cls,
        *,
        cycle_course_id,
        tenant_id,
        user,
        reason,
        expected_updated_at,
        request=None,
    ):
        cycle_course = cls._locked_cycle_course(
            cycle_course_id=cycle_course_id,
            tenant_id=tenant_id,
        )
        DepartmentalExamAuthorizationService.require_configure_cycle_course(
            user=user,
            cycle_course=cycle_course,
        )
        if cycle_course.inclusion_status == CycleCourse.InclusionStatus.INCLUDED:
            return cycle_course, False
        cls._require_current_confirmation(
            cycle_course=cycle_course,
            expected_updated_at=expected_updated_at,
        )
        cls._require_restore_transition_window(cycle_course=cycle_course)
        reason = cls._validate_reason(reason)

        before = cls._state_payload(cycle_course)
        reviewer_revalidated = bool(cycle_course.reviewer_id)
        reviewer_cleared = False
        if cycle_course.reviewer_id and not DepartmentalExamAuthorizationService.is_eligible_reviewer(
            user=cycle_course.reviewer,
            tenant_id=cycle_course.cycle.tenant_id,
            responsible_department=cycle_course.responsible_department,
        ):
            cycle_course.reviewer = None
            reviewer_cleared = True

        cycle_course.inclusion_status = CycleCourse.InclusionStatus.INCLUDED
        cycle_course.exemption_category = ""
        cycle_course.exemption_reason = ""
        cycle_course.exemption_changed_by = user
        cycle_course.exemption_changed_at = timezone.now()
        cycle_course.full_clean()
        cycle_course.save(
            update_fields=[
                "inclusion_status",
                "exemption_category",
                "exemption_reason",
                "exemption_changed_by",
                "exemption_changed_at",
                "reviewer",
                "updated_at",
            ]
        )
        cls._audit_transition(
            action="DE_EXAM_CYCLE_COURSE_RESTORED",
            cycle_course=cycle_course,
            actor=user,
            reason=reason,
            before=before,
            reviewer_revalidated=reviewer_revalidated,
            reviewer_cleared=reviewer_cleared,
            expected_updated_at=expected_updated_at,
            request=request,
        )
        return cycle_course, True


class ExaminationCycleService:
    SNAPSHOT_BATCH_SIZE = 200

    @classmethod
    def _flush_snapshot_batch(cls, snapshot_batch):
        if snapshot_batch:
            CycleCourseOffering.objects.bulk_create(
                snapshot_batch,
                batch_size=cls.SNAPSHOT_BATCH_SIZE,
            )
            snapshot_batch.clear()

    @classmethod
    @transaction.atomic
    def create_cycle(cls, *, user, tenant, academic_year, term, exam_period, request=None):
        DepartmentalExamAuthorizationService.require_permission(
            user=user,
            permission="departmental_exams.manage_cycles",
            tenant_id=tenant.id,
        )
        cycle = ExaminationCycle(
            tenant=tenant,
            academic_year=academic_year,
            term=term,
            exam_period=exam_period,
            created_by=user,
        )
        cycle.full_clean()
        cycle.save()
        offerings = (
            CourseOffering.objects.filter(
                tenant=tenant,
                academic_year=academic_year,
                term=term,
                is_active=True,
            )
            .exclude(status=CourseOffering.Status.ARCHIVED)
            .select_related("course__exam_department")
            .only(
                "id",
                "course_id",
                "campus_id",
                "course__id",
                "course__tenant_id",
                "course__exam_department_id",
            )
            .order_by("course_id", "id")
        )
        current_course_id = None
        cycle_course = None
        snapshot_batch = []
        course_count = 0
        # Offerings are already constrained to this cycle's tenant, year, and
        # term. Grouping by the ordered course ID and copying each offering's
        # campus ID therefore preserves the CycleCourseOffering invariants
        # without retaining the full tenant offering set in memory.
        for offering in offerings.iterator(chunk_size=cls.SNAPSHOT_BATCH_SIZE):
            if offering.course_id != current_course_id:
                cycle_course = CycleCourse(
                    cycle=cycle,
                    course=offering.course,
                    responsible_department=offering.course.exam_department,
                )
                cycle_course.full_clean()
                cycle_course.save()
                current_course_id = offering.course_id
                course_count += 1
            snapshot_batch.append(
                CycleCourseOffering(
                    cycle_course=cycle_course,
                    offering_id=offering.id,
                    campus_id=offering.campus_id,
                )
            )
            if len(snapshot_batch) >= cls.SNAPSHOT_BATCH_SIZE:
                cls._flush_snapshot_batch(snapshot_batch)
        cls._flush_snapshot_batch(snapshot_batch)
        AuditService.log_event(
            action="DE_EXAM_CYCLE_CREATED",
            portal="ADMIN",
            entity_type="ExaminationCycle",
            entity_id=cycle.id,
            actor=user,
            tenant=tenant,
            metadata={"exam_period": exam_period, "course_count": course_count},
            request=request,
        )
        return cycle
