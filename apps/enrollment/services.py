from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError

from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment


class EnrollmentService:
    MODE_KEY = "ENROLLMENT_OWNERSHIP_MODE"
    LEGACY_MODE_KEY = "enrollment_mode"
    ADMIN_ONLY = "ADMIN_ONLY"
    FACULTY_ALLOWED = "FACULTY_ALLOWED"

    @classmethod
    def get_enrollment_mode(cls, tenant_id: int | None):
        mode = SystemSettingService.get(cls.MODE_KEY, tenant_id=tenant_id, default=None)
        if mode is None:
            mode = SystemSettingService.get(cls.LEGACY_MODE_KEY, tenant_id=tenant_id, default=cls.ADMIN_ONLY)
        mode = str(mode).upper() if mode else cls.ADMIN_ONLY
        if mode not in {cls.ADMIN_ONLY, cls.FACULTY_ALLOWED}:
            return cls.ADMIN_ONLY
        return mode

    @classmethod
    def _is_admin_route_allowed(cls, user, offering, action: str):
        permission = f"enrollment.{action}"
        return PermissionService.has_permission(
            user,
            permission,
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
        )

    @classmethod
    def _is_faculty_offering_owner(cls, user, offering):
        return offering.faculty_assignments.filter(
            faculty_user_id=user.id,
            is_active=True,
            offering__is_active=True,
        ).exists()

    @classmethod
    def can_create_or_update(cls, *, user, offering, portal: str, action: str):
        if not user or not user.is_authenticated:
            return False

        if cls._is_admin_route_allowed(user, offering, action=action):
            return True

        mode = cls.get_enrollment_mode(offering.tenant_id)
        if portal.upper() == "FACULTY" and mode == cls.FACULTY_ALLOWED:
            return cls._is_faculty_offering_owner(user, offering)
        return False

    @classmethod
    def can_update_classlist_status(cls, *, user, offering, portal: str):
        if not user or not user.is_authenticated:
            return False

        if cls._is_admin_route_allowed(user, offering, action="update"):
            return True

        if portal.upper() == "FACULTY":
            return cls._is_faculty_offering_owner(user, offering)
        return False

    @classmethod
    def create_enrollment(
        cls,
        *,
        user,
        offering,
        student,
        enrollment_status: str = Enrollment.Status.ACTIVE,
        portal: str = Enrollment.SourcePortal.ADMIN,
    ):
        if not cls.can_create_or_update(user=user, offering=offering, portal=portal, action="create"):
            raise PermissionDenied("You are not allowed to create enrollment for this offering.")
        if offering.tenant_id != student.tenant_id:
            raise ValidationError("Student and offering tenant mismatch.")
        if offering.campus_id != student.campus_id:
            raise ValidationError("Student and offering campus mismatch.")

        enrollment, created = Enrollment.objects.get_or_create(
            course_offering=offering,
            student=student,
            defaults={
                "tenant_id": offering.tenant_id,
                "campus_id": offering.campus_id,
                "academic_year_id": offering.academic_year_id,
                "term_id": offering.term_id,
                "enrollment_status": enrollment_status,
                "encoded_by_user": user,
                "encoded_via_portal": portal.upper(),
                "is_active": True,
            },
        )
        if not created:
            enrollment.enrollment_status = enrollment_status
            enrollment.encoded_by_user = user
            enrollment.encoded_via_portal = portal.upper()
            enrollment.is_active = True
            enrollment.save(
                update_fields=["enrollment_status", "encoded_by_user", "encoded_via_portal", "is_active", "updated_at"]
            )
        return enrollment, created

    @classmethod
    def update_enrollment(
        cls,
        *,
        user,
        enrollment: Enrollment,
        enrollment_status: str,
        is_active: bool,
        portal: str = Enrollment.SourcePortal.ADMIN,
    ):
        offering = enrollment.course_offering
        if not cls.can_update_classlist_status(user=user, offering=offering, portal=portal):
            raise PermissionDenied("You are not allowed to update enrollment for this offering.")

        enrollment.enrollment_status = enrollment_status
        enrollment.is_active = is_active
        enrollment.encoded_by_user = user
        enrollment.encoded_via_portal = portal.upper()
        enrollment.save(
            update_fields=["enrollment_status", "is_active", "encoded_by_user", "encoded_via_portal", "updated_at"]
        )
        return enrollment
