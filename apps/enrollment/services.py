from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.academics.services import AcademicGovernanceService
from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment, EnrollmentAdjustmentLog
from apps.grading.models import (
    GradeActivity,
    GradeCorrectionRequest,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)


@dataclass(frozen=True)
class EnrollmentAdjustmentImpact:
    student_id: int
    student_number: str
    student_name: str
    enrollment_status: str
    classification: str
    reasons: list[str]
    warning_flags: list[str]
    counts: dict[str, int]

    @property
    def eligible(self):
        return self.classification in {
            EnrollmentAdjustmentService.CLASSIFICATION_SAFE,
            EnrollmentAdjustmentService.CLASSIFICATION_WARNING,
        }

    def to_dict(self):
        return {
            "student_id": self.student_id,
            "student_number": self.student_number,
            "student_name": self.student_name,
            "enrollment_status": self.enrollment_status,
            "classification": self.classification,
            "reasons": self.reasons,
            "warning_flags": self.warning_flags,
            "counts": self.counts,
            "eligible": self.eligible,
        }


class EnrollmentAdjustmentService:
    CLASSIFICATION_SAFE = "SAFE"
    CLASSIFICATION_WARNING = "WARNING"
    CLASSIFICATION_BLOCKED = "BLOCKED"
    WARNING_MESSAGE = (
        "This student already has academic records in the source offering. "
        "Historical records may require faculty verification."
    )

    WARNING_COUNT_KEYS = (
        "attendance_count",
        "activities_count",
        "scores_count",
        "submissions_count",
        "period_grades_count",
        "final_grades_count",
        "correction_requests_count",
        "reopen_requests_count",
    )
    BLOCKING_COUNT_KEYS = (
        "submitted_final_grades_count",
        "period_locks_count",
    )

    @classmethod
    def _student_name(cls, student):
        middle = f" {student.middle_name}" if getattr(student, "middle_name", "") else ""
        return f"{student.last_name}, {student.first_name}{middle}".strip()

    @classmethod
    def get_active_source_enrollments(cls, *, source_offering, student_ids=None):
        queryset = (
            Enrollment.objects.filter(
                course_offering=source_offering,
                is_active=True,
            )
            .select_related("student")
            .order_by("student__last_name", "student__first_name", "student__student_no")
        )
        if student_ids is not None:
            queryset = queryset.filter(student_id__in=student_ids)
        return queryset

    @classmethod
    def get_impact_counts(cls, *, source_offering, student):
        activity_qs = GradeActivity.objects.filter(offering=source_offering, is_active=True)
        attendance_session_qs = AttendanceSession.objects.filter(offering=source_offering, is_active=True)
        period_lock_qs = GradingPeriodLock.objects.filter(
            tenant_id=source_offering.tenant_id,
            campus_id=source_offering.campus_id,
            academic_year_id=source_offering.academic_year_id,
            term_id=source_offering.term_id,
            is_active=True,
            is_locked=True,
        ).filter(
            models.Q(
                scope_type=GradingPeriodLock.ScopeType.COURSE,
                course_offering=source_offering,
            )
            | models.Q(
                scope_type=GradingPeriodLock.ScopeType.CAMPUS,
                course_offering__isnull=True,
            )
        )
        return {
            "attendance_count": AttendanceRecord.objects.filter(
                session__offering=source_offering,
                student=student,
                is_active=True,
            ).count(),
            "activities_count": activity_qs.count(),
            "scores_count": StudentActivityScore.objects.filter(
                activity__offering=source_offering,
                student=student,
                is_active=True,
            ).count(),
            "submissions_count": GradeSubmission.objects.filter(offering=source_offering).count(),
            "period_grades_count": StudentPeriodGrade.objects.filter(
                offering=source_offering,
                student=student,
            ).count(),
            "final_grades_count": StudentFinalGrade.objects.filter(
                offering=source_offering,
                student=student,
            ).count(),
            "submitted_final_grades_count": StudentFinalGrade.objects.filter(
                offering=source_offering,
                student=student,
                is_submitted=True,
            ).count(),
            "correction_requests_count": GradeCorrectionRequest.objects.filter(
                offering=source_offering,
            ).count(),
            "reopen_requests_count": GradeSubmissionReopenRequest.objects.filter(
                offering=source_offering,
            ).count(),
            "period_locks_count": period_lock_qs.count(),
            "attendance_sessions_count": attendance_session_qs.count(),
        }

    @classmethod
    def classify_impact(cls, *, source_offering, destination_offering, enrollment):
        student = enrollment.student
        counts = cls.get_impact_counts(source_offering=source_offering, student=student)
        reasons = []
        warning_flags = []

        if source_offering.id == destination_offering.id:
            reasons.append("Source and destination offerings are the same.")
        if Enrollment.objects.filter(course_offering=destination_offering, student=student).exists():
            reasons.append("Student already has an enrollment in the destination offering.")
        if counts["submitted_final_grades_count"]:
            reasons.append("Final grade is already submitted.")
        if counts["period_locks_count"]:
            reasons.append("A grading period is locked for the source offering.")

        if reasons:
            return EnrollmentAdjustmentImpact(
                student_id=student.id,
                student_number=student.student_no,
                student_name=cls._student_name(student),
                enrollment_status=enrollment.enrollment_status,
                classification=cls.CLASSIFICATION_BLOCKED,
                reasons=reasons,
                warning_flags=warning_flags,
                counts=counts,
            )

        for key in cls.WARNING_COUNT_KEYS:
            if counts.get(key, 0) > 0:
                warning_flags.append(key)

        classification = cls.CLASSIFICATION_WARNING if warning_flags else cls.CLASSIFICATION_SAFE
        if classification == cls.CLASSIFICATION_WARNING:
            reasons.append(cls.WARNING_MESSAGE)
        else:
            reasons.append("No academic records found in the source offering.")

        return EnrollmentAdjustmentImpact(
            student_id=student.id,
            student_number=student.student_no,
            student_name=cls._student_name(student),
            enrollment_status=enrollment.enrollment_status,
            classification=classification,
            reasons=reasons,
            warning_flags=warning_flags,
            counts=counts,
        )

    @classmethod
    def analyze(cls, *, source_offering, destination_offering, student_ids):
        impacts = [
            cls.classify_impact(
                source_offering=source_offering,
                destination_offering=destination_offering,
                enrollment=enrollment,
            )
            for enrollment in cls.get_active_source_enrollments(
                source_offering=source_offering,
                student_ids=student_ids,
            )
        ]
        return {
            "rows": [impact.to_dict() for impact in impacts],
            "totals": cls.summarize_impacts(impacts),
        }

    @classmethod
    def summarize_impacts(cls, impacts):
        rows = [impact.to_dict() if isinstance(impact, EnrollmentAdjustmentImpact) else impact for impact in impacts]
        return {
            "transferable": sum(1 for row in rows if row["classification"] == cls.CLASSIFICATION_SAFE),
            "warning": sum(1 for row in rows if row["classification"] == cls.CLASSIFICATION_WARNING),
            "blocked": sum(1 for row in rows if row["classification"] == cls.CLASSIFICATION_BLOCKED),
            "total": len(rows),
        }

    @classmethod
    def generate_batch_reference(cls):
        return f"EA-{timezone.now():%Y%m%d%H%M%S}-{uuid4().hex[:8].upper()}"

    @classmethod
    def _create_log(
        cls,
        *,
        student,
        source_offering,
        destination_offering,
        reason,
        processed_by,
        result,
        impact,
        batch_reference,
        source_enrollment=None,
        destination_enrollment=None,
        source_previous_is_active=None,
        source_previous_status=None,
    ):
        return EnrollmentAdjustmentLog.objects.create(
            student=student,
            source_offering=source_offering,
            destination_offering=destination_offering,
            source_enrollment_id=source_enrollment.id if source_enrollment else None,
            destination_enrollment_id=destination_enrollment.id if destination_enrollment else None,
            source_previous_is_active=source_previous_is_active,
            source_previous_status=source_previous_status,
            destination_is_active=destination_enrollment.is_active if destination_enrollment else None,
            destination_status=destination_enrollment.enrollment_status if destination_enrollment else None,
            batch_reference=batch_reference,
            reason=reason,
            processed_by=processed_by,
            processed_at=timezone.now(),
            result=result,
            warning_flags=impact.warning_flags,
            impact_snapshot=impact.to_dict(),
        )

    @classmethod
    def process(cls, *, user, source_offering, destination_offering, student_ids, reason, confirm_warning=False):
        if not (reason or "").strip():
            raise ValidationError("Enter the official reason for this enrollment adjustment.")
        results = []
        batch_reference = cls.generate_batch_reference()
        for student_id in student_ids:
            try:
                parsed_student_id = int(student_id)
            except (TypeError, ValueError):
                continue
            enrollment = None
            try:
                with transaction.atomic():
                    enrollment = (
                        cls.get_active_source_enrollments(
                            source_offering=source_offering,
                            student_ids=[parsed_student_id],
                        )
                        .select_for_update()
                        .select_related("student")
                        .first()
                    )
                    if not enrollment:
                        continue
                    impact = cls.classify_impact(
                        source_offering=source_offering,
                        destination_offering=destination_offering,
                        enrollment=enrollment,
                    )
                    student = enrollment.student
                    source_previous_is_active = enrollment.is_active
                    source_previous_status = enrollment.enrollment_status
                    if impact.classification == cls.CLASSIFICATION_BLOCKED:
                        log = cls._create_log(
                            student=student,
                            source_offering=source_offering,
                            destination_offering=destination_offering,
                            reason=reason,
                            processed_by=user,
                            result=EnrollmentAdjustmentLog.Result.BLOCKED,
                            impact=impact,
                            batch_reference=batch_reference,
                            source_enrollment=enrollment,
                            source_previous_is_active=source_previous_is_active,
                            source_previous_status=source_previous_status,
                        )
                        results.append({"student_id": student.id, "result": log.result, "impact": impact.to_dict()})
                        continue
                    if impact.classification == cls.CLASSIFICATION_WARNING and not confirm_warning:
                        log = cls._create_log(
                            student=student,
                            source_offering=source_offering,
                            destination_offering=destination_offering,
                            reason=reason,
                            processed_by=user,
                            result=EnrollmentAdjustmentLog.Result.BLOCKED,
                            impact=impact,
                            batch_reference=batch_reference,
                            source_enrollment=enrollment,
                            source_previous_is_active=source_previous_is_active,
                            source_previous_status=source_previous_status,
                        )
                        results.append({"student_id": student.id, "result": log.result, "impact": impact.to_dict()})
                        continue

                    destination_enrollment = Enrollment.objects.create(
                        tenant_id=destination_offering.tenant_id,
                        campus_id=destination_offering.campus_id,
                        academic_year_id=destination_offering.academic_year_id,
                        term_id=destination_offering.term_id,
                        student=student,
                        course_offering=destination_offering,
                        enrollment_status=enrollment.enrollment_status,
                        encoded_by_user=user,
                        encoded_via_portal=Enrollment.SourcePortal.ADMIN,
                        is_active=True,
                    )
                    enrollment.is_active = False
                    enrollment.encoded_by_user = user
                    enrollment.encoded_via_portal = Enrollment.SourcePortal.ADMIN
                    enrollment.save(update_fields=["is_active", "encoded_by_user", "encoded_via_portal", "updated_at"])
                    result = (
                        EnrollmentAdjustmentLog.Result.COMPLETED_WITH_WARNING
                        if impact.classification == cls.CLASSIFICATION_WARNING
                        else EnrollmentAdjustmentLog.Result.COMPLETED
                    )
                    log = cls._create_log(
                        student=student,
                        source_offering=source_offering,
                        destination_offering=destination_offering,
                        reason=reason,
                        processed_by=user,
                        result=result,
                        impact=impact,
                        batch_reference=batch_reference,
                        source_enrollment=enrollment,
                        destination_enrollment=destination_enrollment,
                        source_previous_is_active=source_previous_is_active,
                        source_previous_status=source_previous_status,
                    )
                    results.append({"student_id": student.id, "result": log.result, "impact": impact.to_dict()})
            except IntegrityError as exc:
                student = getattr(enrollment, "student", None)
                if student is None:
                    continue
                failed_impact = EnrollmentAdjustmentImpact(
                    student_id=student.id,
                    student_number=student.student_no,
                    student_name=cls._student_name(student),
                    enrollment_status=getattr(enrollment, "enrollment_status", ""),
                    classification=cls.CLASSIFICATION_BLOCKED,
                    reasons=["Destination enrollment already exists or could not be created safely."],
                    warning_flags=[],
                    counts=cls.get_impact_counts(source_offering=source_offering, student=student),
                )
                log = cls._create_log(
                    student=student,
                    source_offering=source_offering,
                    destination_offering=destination_offering,
                    reason=reason,
                    processed_by=user,
                    result=EnrollmentAdjustmentLog.Result.FAILED,
                    impact=failed_impact,
                    batch_reference=batch_reference,
                    source_enrollment=enrollment,
                    source_previous_is_active=getattr(enrollment, "is_active", None),
                    source_previous_status=getattr(enrollment, "enrollment_status", None),
                )
                results.append({"student_id": student.id, "result": log.result, "impact": failed_impact.to_dict()})
        return {
            "rows": results,
            "totals": {
                "completed": sum(1 for row in results if row["result"] == EnrollmentAdjustmentLog.Result.COMPLETED),
                "completed_with_warning": sum(
                    1 for row in results if row["result"] == EnrollmentAdjustmentLog.Result.COMPLETED_WITH_WARNING
                ),
                "blocked": sum(1 for row in results if row["result"] == EnrollmentAdjustmentLog.Result.BLOCKED),
                "failed": sum(1 for row in results if row["result"] == EnrollmentAdjustmentLog.Result.FAILED),
                "total": len(results),
            },
        }


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
