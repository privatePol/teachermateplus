from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError

from apps.academics.services import AcademicGovernanceService
from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment


class EnrollmentService:
    MODE_KEY = "ENROLLMENT_OWNERSHIP_MODE"
    MODE_OVERRIDE_MAP_KEY = "ENROLLMENT_OWNERSHIP_MODE_BY_OFFERING"
    FACULTY_DRP_ALLOWED_THROUGH_PERIOD_KEY = "FACULTY_DRP_ALLOWED_THROUGH_PERIOD"
    LEGACY_MODE_KEY = "enrollment_mode"
    ADMIN_ONLY = "ADMIN_ONLY"
    FACULTY_ALLOWED = "FACULTY_ALLOWED"
    PERIOD_ALWAYS = "ALWAYS"
    PERIOD_PRELIM = "PRELIM"
    PERIOD_MIDTERM = "MIDTERM"
    PERIOD_PREFINAL = "PREFINAL"
    PERIOD_FINAL = "FINAL"

    FACULTY_DRP_PERIOD_CHOICES = (
        (PERIOD_MIDTERM, "Through Midterm"),
        (PERIOD_PREFINAL, "Through Pre-Final"),
        (PERIOD_FINAL, "Through Final"),
        (PERIOD_ALWAYS, "Always allow"),
    )
    FACULTY_DRP_PERIOD_ORDER = {
        PERIOD_PRELIM: 1,
        PERIOD_MIDTERM: 2,
        PERIOD_PREFINAL: 3,
        PERIOD_FINAL: 4,
    }

    @classmethod
    def get_enrollment_mode_overrides(cls, tenant_id: int | None) -> dict[str, str]:
        raw_value = SystemSettingService.get(cls.MODE_OVERRIDE_MAP_KEY, tenant_id=tenant_id, default={}) or {}
        if not isinstance(raw_value, dict):
            return {}
        cleaned = {}
        for offering_id, mode in raw_value.items():
            normalized_mode = str(mode or "").upper()
            if normalized_mode in {cls.ADMIN_ONLY, cls.FACULTY_ALLOWED}:
                cleaned[str(offering_id)] = normalized_mode
        return cleaned

    @classmethod
    def get_enrollment_mode(cls, tenant_id: int | None, offering_id: int | None = None):
        mode = SystemSettingService.get(cls.MODE_KEY, tenant_id=tenant_id, default=None)
        if mode is None:
            mode = SystemSettingService.get(cls.LEGACY_MODE_KEY, tenant_id=tenant_id, default=cls.ADMIN_ONLY)
        mode = str(mode).upper() if mode else cls.ADMIN_ONLY
        if mode not in {cls.ADMIN_ONLY, cls.FACULTY_ALLOWED}:
            mode = cls.ADMIN_ONLY

        if offering_id is not None:
            override_mode = cls.get_enrollment_mode_overrides(tenant_id).get(str(offering_id))
            if override_mode in {cls.ADMIN_ONLY, cls.FACULTY_ALLOWED}:
                return override_mode
        return mode

    @classmethod
    def get_faculty_drp_allowed_through_period(cls, tenant_id: int | None):
        raw_value = SystemSettingService.get(
            cls.FACULTY_DRP_ALLOWED_THROUGH_PERIOD_KEY,
            tenant_id=tenant_id,
            default=cls.PERIOD_PREFINAL,
        )
        normalized = str(raw_value or cls.PERIOD_PREFINAL).strip().upper().replace("-", "").replace("_", "")
        if normalized == "PREFINAL":
            return cls.PERIOD_PREFINAL
        if normalized in {cls.PERIOD_MIDTERM, cls.PERIOD_FINAL, cls.PERIOD_ALWAYS}:
            return normalized
        return cls.PERIOD_PREFINAL

    @classmethod
    def _ensure_faculty_drp_allowed_for_period(cls, *, offering, portal: str):
        if portal.upper() != Enrollment.SourcePortal.FACULTY:
            return
        allowed_through = cls.get_faculty_drp_allowed_through_period(offering.tenant_id)
        if allowed_through == cls.PERIOD_ALWAYS:
            return
        active_setting = AcademicGovernanceService.resolve_active_grading_period(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            term_id=offering.term_id,
        )
        if not active_setting or not active_setting.period_id:
            return
        active_key = AcademicGovernanceService.normalize_period_key(
            active_setting.period.code or active_setting.period.name
        )
        active_order = cls.FACULTY_DRP_PERIOD_ORDER.get(active_key)
        cutoff_order = cls.FACULTY_DRP_PERIOD_ORDER.get(allowed_through)
        if active_order and cutoff_order and active_order > cutoff_order:
            raise ValidationError(
                "Faculty DRP updates are no longer allowed for this class under the current active grading period."
            )

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

        mode = cls.get_enrollment_mode(offering.tenant_id, offering_id=offering.id)
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
        if offering.tenant_id != enrollment.student.tenant_id:
            raise ValidationError("Student and offering tenant mismatch.")
        if offering.campus_id != enrollment.student.campus_id:
            raise ValidationError("Student and offering campus mismatch.")
        if enrollment_status == Enrollment.Status.DRP and enrollment.enrollment_status != Enrollment.Status.DRP:
            cls._ensure_faculty_drp_allowed_for_period(offering=offering, portal=portal)

        enrollment.enrollment_status = enrollment_status
        enrollment.is_active = is_active
        enrollment.encoded_by_user = user
        enrollment.encoded_via_portal = portal.upper()
        enrollment.save(
            update_fields=["enrollment_status", "is_active", "encoded_by_user", "encoded_via_portal", "updated_at"]
        )
        return enrollment
