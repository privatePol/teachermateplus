from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.academics.models import CourseOffering
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.core.services.settings import SystemSettingService
from apps.core.services.audit import AuditService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    GradeCorrectionRequestItem,
    GradeCorrectionRequest,
    GradeCorrectionUnlockWindow,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradeActivity,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    TemplateHotfixRequest,
    TenantGradingProfile,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)


class GradingTemplateService:
    @staticmethod
    def ensure_editable(template):
        if template.approval_status == template.ApprovalStatus.FOR_APPROVAL:
            raise ValidationError("Template is currently under approval review and cannot be edited.")

    @staticmethod
    def _sum_components(period):
        return sum((component.weight_percentage or Decimal("0")) for component in period.components.filter(is_active=True))

    @staticmethod
    def _sum_subcomponents(component):
        return sum(
            (sub.weight_percentage or Decimal("0")) for sub in component.subcomponents.filter(is_active=True)
        )

    @staticmethod
    def _sum_details(subcomponent):
        return sum(
            (detail.weight_percentage or Decimal("0")) for detail in subcomponent.details.filter(is_active=True)
        )

    @classmethod
    def validate_publishable(cls, template):
        errors = []
        periods = list(template.periods.filter(is_active=True).order_by("sequence_no"))
        if not periods:
            errors.append("Template must have at least one active period.")

        for period in periods:
            components = list(period.components.filter(is_active=True))
            if not components:
                errors.append(f"Period {period.code} has no active components.")
                continue
            comp_total = cls._sum_components(period)
            if comp_total != Decimal("100"):
                errors.append(f"Period {period.code} component total must be 100 (current {comp_total}).")
            for component in components:
                subcomponents = list(component.subcomponents.filter(is_active=True))
                if subcomponents:
                    sub_total = cls._sum_subcomponents(component)
                    if sub_total <= Decimal("0"):
                        errors.append(
                            f"Component {component.code} has subcomponents but total weight is {sub_total}. "
                            "Subcomponent total must be greater than 0."
                        )
                for subcomponent in subcomponents:
                    details = list(subcomponent.details.filter(is_active=True))
                    if details:
                        detail_total = cls._sum_details(subcomponent)
                        if detail_total <= Decimal("0"):
                            errors.append(
                                f"Subcomponent {subcomponent.code} has details but total weight is {detail_total}. "
                                "Detail total must be greater than 0."
                            )
        return errors

    @classmethod
    def publish(cls, *, template, actor):
        if template.approval_status != template.ApprovalStatus.APPROVED:
            raise ValidationError("Template must be approved before publishing.")
        errors = cls.validate_publishable(template)
        if errors:
            raise ValidationError(errors)
        template.is_published = True
        template.published_at = timezone.now()
        template.published_by = actor
        template.save(update_fields=["is_published", "published_at", "published_by", "updated_at"])
        return template

    @classmethod
    def submit_for_approval(cls, *, template, actor, remarks: str | None = None):
        errors = cls.validate_publishable(template)
        if errors:
            raise ValidationError(errors)
        if template.approval_status == template.ApprovalStatus.FOR_APPROVAL:
            raise ValidationError("Template is already submitted for approval.")

        template.approval_status = template.ApprovalStatus.FOR_APPROVAL
        template.approval_requested_by = actor
        template.approval_requested_at = timezone.now()
        template.approval_reviewed_by = None
        template.approval_reviewed_at = None
        template.approval_remarks = (remarks or "").strip() or None
        template.save(
            update_fields=[
                "approval_status",
                "approval_requested_by",
                "approval_requested_at",
                "approval_reviewed_by",
                "approval_reviewed_at",
                "approval_remarks",
                "updated_at",
            ]
        )
        return template

    @classmethod
    def review_approval(cls, *, template, actor, approve: bool, remarks: str | None = None):
        if template.approval_status != template.ApprovalStatus.FOR_APPROVAL:
            raise ValidationError("Only templates in FOR_APPROVAL status can be reviewed.")

        if approve:
            errors = cls.validate_publishable(template)
            if errors:
                raise ValidationError(errors)
            template.approval_status = template.ApprovalStatus.APPROVED
        else:
            template.approval_status = template.ApprovalStatus.REJECTED

        template.approval_reviewed_by = actor
        template.approval_reviewed_at = timezone.now()
        template.approval_remarks = (remarks or "").strip() or None
        template.save(
            update_fields=[
                "approval_status",
                "approval_reviewed_by",
                "approval_reviewed_at",
                "approval_remarks",
                "updated_at",
            ]
        )
        return template


class TemplateHotfixService:
    @staticmethod
    def involved_personalities():
        return [
            {"role": "FACULTY", "responsibility": "Raise grading impact concerns to admin."},
            {"role": "DEAN", "responsibility": "Academic policy approver for template hotfixes."},
            {"role": "REGISTRAR", "responsibility": "Records governance approver and compliance check."},
            {"role": "CAMPUS_ADMIN", "responsibility": "Campus operations approver and execution monitor."},
            {"role": "SUPER_ADMIN", "responsibility": "Cross-campus oversight and emergency override."},
        ]

    @classmethod
    def _offering_has_submitted_grades(cls, offering):
        return GradeSubmission.objects.filter(
            offering_id=offering.id,
            status=GradeSubmission.Status.SUBMITTED,
        ).exists()

    @classmethod
    def _candidate_offerings_for_template(cls, template):
        candidate_qs = (
            CourseOffering.objects.filter(tenant_id=template.tenant_id, is_active=True)
            .select_related("term", "academic_year", "course", "section", "campus")
            .order_by("-created_at")
        )
        matched = []
        for offering in candidate_qs:
            try:
                resolved = FacultyGradingService.resolve_template_for_offering(offering)
            except ValidationError:
                continue
            if resolved.id == template.id:
                matched.append(offering)
        return matched

    @classmethod
    def _resolve_target_offerings(cls, hotfix_request: TemplateHotfixRequest):
        offerings = cls._candidate_offerings_for_template(hotfix_request.template)
        today = timezone.localdate()

        if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.FUTURE_ONLY:
            future = []
            for offering in offerings:
                term_start = offering.term.start_date
                ay_start = offering.academic_year.start_date
                if (term_start and term_start > today) or (not term_start and ay_start and ay_start > today):
                    future.append(offering)
            return future

        if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.ACTIVE_NOT_SUBMITTED:
            return [
                offering
                for offering in offerings
                if offering.status == offering.Status.OPEN and not cls._offering_has_submitted_grades(offering)
            ]

        if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.SELECTED_OFFERINGS:
            selected_ids = hotfix_request.selected_offering_ids_json or []
            selected_ids = {int(x) for x in selected_ids if str(x).isdigit()}
            return [offering for offering in offerings if offering.id in selected_ids]

        return []

    @classmethod
    @transaction.atomic
    def create_request(
        cls,
        *,
        template,
        requested_by,
        apply_mode: str,
        justification: str,
        selected_offering_ids: list[int] | None = None,
    ):
        if not template.is_published:
            raise ValidationError("Hotfix request requires a published template.")
        if apply_mode == TemplateHotfixRequest.ApplyMode.SELECTED_OFFERINGS and not selected_offering_ids:
            raise ValidationError("Selected offerings are required for SELECTED_OFFERINGS mode.")

        return TemplateHotfixRequest.objects.create(
            tenant_id=template.tenant_id,
            template=template,
            apply_mode=apply_mode,
            status=TemplateHotfixRequest.Status.PENDING,
            justification=(justification or "").strip(),
            selected_offering_ids_json=selected_offering_ids or None,
            requested_by_user=requested_by,
        )

    @classmethod
    @transaction.atomic
    def review_and_apply(
        cls,
        *,
        hotfix_request: TemplateHotfixRequest,
        reviewer,
        approve: bool,
        review_remarks: str | None = None,
    ):
        if hotfix_request.status != TemplateHotfixRequest.Status.PENDING:
            raise ValidationError("Only pending hotfix requests can be reviewed.")

        hotfix_request.reviewed_by_user = reviewer
        hotfix_request.reviewed_at = timezone.now()
        hotfix_request.review_remarks = (review_remarks or "").strip() or None

        if not approve:
            hotfix_request.status = TemplateHotfixRequest.Status.REJECTED
            hotfix_request.save(
                update_fields=[
                    "reviewed_by_user",
                    "reviewed_at",
                    "review_remarks",
                    "status",
                    "updated_at",
                ]
            )
            return hotfix_request

        hotfix_request.status = TemplateHotfixRequest.Status.APPROVED
        hotfix_request.save(
            update_fields=[
                "reviewed_by_user",
                "reviewed_at",
                "review_remarks",
                "status",
                "updated_at",
            ]
        )

        target_offerings = cls._resolve_target_offerings(hotfix_request)
        recomputed = 0
        skipped = []
        processed = []

        if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.FUTURE_ONLY:
            hotfix_request.status = TemplateHotfixRequest.Status.APPLIED
            hotfix_request.applied_by_user = reviewer
            hotfix_request.applied_at = timezone.now()
            hotfix_request.affected_offering_count = len(target_offerings)
            hotfix_request.recomputed_offering_count = 0
            hotfix_request.impact_snapshot_json = {
                "mode": hotfix_request.apply_mode,
                "note": "No immediate recomputation. Hotfix applies to future offerings.",
                "offering_ids": [o.id for o in target_offerings],
            }
            hotfix_request.save(
                update_fields=[
                    "status",
                    "applied_by_user",
                    "applied_at",
                    "affected_offering_count",
                    "recomputed_offering_count",
                    "impact_snapshot_json",
                    "updated_at",
                ]
            )
            return hotfix_request

        for offering in target_offerings:
            if hotfix_request.apply_mode == TemplateHotfixRequest.ApplyMode.SELECTED_OFFERINGS and cls._offering_has_submitted_grades(offering):
                skipped.append(
                    {
                        "offering_id": offering.id,
                        "reason": "Submitted grades detected. Use correction workflow before hotfix recompute.",
                    }
                )
                continue

            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
                if template.id != hotfix_request.template_id:
                    skipped.append(
                        {"offering_id": offering.id, "reason": "Offering no longer resolves to target template."}
                    )
                    continue
                periods = template.periods.filter(is_active=True).order_by("sequence_no", "id")
                for period in periods:
                    FacultyGradingService.recompute_period_summary(
                        user=reviewer,
                        offering=offering,
                        template_period=period,
                    )
                recomputed += 1
                processed.append(offering.id)
            except ValidationError as exc:
                skipped.append({"offering_id": offering.id, "reason": str(exc)})

        hotfix_request.status = TemplateHotfixRequest.Status.APPLIED
        hotfix_request.applied_by_user = reviewer
        hotfix_request.applied_at = timezone.now()
        hotfix_request.affected_offering_count = len(target_offerings)
        hotfix_request.recomputed_offering_count = recomputed
        hotfix_request.impact_snapshot_json = {
            "mode": hotfix_request.apply_mode,
            "processed_offering_ids": processed,
            "skipped": skipped,
        }
        hotfix_request.save(
            update_fields=[
                "status",
                "applied_by_user",
                "applied_at",
                "affected_offering_count",
                "recomputed_offering_count",
                "impact_snapshot_json",
                "updated_at",
            ]
        )
        return hotfix_request


class GradingGovernanceService:
    PREDEADLINE_CORRECTION_MODE_KEY = "PREDEADLINE_CORRECTION_MODE"
    PREDEADLINE_CORRECTION_MODE_REQUEST = "REQUEST_REVIEW"
    PREDEADLINE_CORRECTION_MODE_SELF_REOPEN = "FACULTY_SELF_REOPEN"

    @staticmethod
    def _round(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def _normalize_period_key(value: str | None) -> str:
        raw = (value or "").strip().upper().replace("-", "").replace("_", "").replace(" ", "")
        if "PREFINAL" in raw or "PREFI" in raw:
            return "PREFINAL"
        if "MIDTERM" in raw:
            return "MIDTERM"
        if raw == "FINAL" or raw.endswith("FINAL") or "FINALEXAM" in raw:
            return "FINAL"
        if "PRELIM" in raw:
            return "PRELIM"
        return raw

    @classmethod
    def get_predeadline_correction_mode(cls, *, tenant_id: int | None):
        mode = SystemSettingService.get(
            cls.PREDEADLINE_CORRECTION_MODE_KEY,
            tenant_id=tenant_id,
            default=cls.PREDEADLINE_CORRECTION_MODE_REQUEST,
        )
        if mode not in {
            cls.PREDEADLINE_CORRECTION_MODE_REQUEST,
            cls.PREDEADLINE_CORRECTION_MODE_SELF_REOPEN,
        }:
            return cls.PREDEADLINE_CORRECTION_MODE_REQUEST
        return mode

    @classmethod
    def resolve_submission_deadline(cls, *, offering, template_period: GradingTemplatePeriod):
        lock = cls.resolve_lock(offering=offering, template_period=template_period)
        return lock.deadline_at if lock else None

    @classmethod
    def can_faculty_self_reopen_before_deadline(cls, *, offering, template_period: GradingTemplatePeriod):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if not submission or submission.status != GradeSubmission.Status.SUBMITTED:
            return False
        mode = cls.get_predeadline_correction_mode(tenant_id=offering.tenant_id)
        if mode != cls.PREDEADLINE_CORRECTION_MODE_SELF_REOPEN:
            return False
        deadline_at = cls.resolve_submission_deadline(offering=offering, template_period=template_period)
        if not deadline_at:
            return False
        return timezone.now() <= deadline_at

    @classmethod
    def evaluate_submission_readiness(cls, *, offering, template_period: GradingTemplatePeriod):
        eligible_enrollments = list(
            Enrollment.objects.filter(
                course_offering_id=offering.id,
                is_active=True,
            )
            .exclude(enrollment_status__in={Enrollment.Status.DR, Enrollment.Status.W})
            .select_related("student")
        )
        eligible_student_ids = [row.student_id for row in eligible_enrollments]
        eligible_count = len(eligible_student_ids)

        if eligible_count == 0:
            return {
                "eligible_student_count": 0,
                "students_with_any_grade": 0,
                "students_missing_any_grade": 0,
                "coverage_percent": Decimal("0.00"),
                "missing_students": [],
            }

        score_student_ids = set(
            StudentActivityScore.objects.filter(
                activity__offering_id=offering.id,
                activity__template_period_id=template_period.id,
                activity__is_active=True,
                is_active=True,
                student_id__in=eligible_student_ids,
            )
            .values_list("student_id", flat=True)
            .distinct()
        )
        attendance_student_ids = set(
            AttendanceRecord.objects.filter(
                session__offering_id=offering.id,
                session__template_period_id=template_period.id,
                session__is_active=True,
                is_active=True,
                student_id__in=eligible_student_ids,
            )
            .values_list("student_id", flat=True)
            .distinct()
        )
        students_with_any_grade_ids = score_student_ids | attendance_student_ids
        students_with_any_grade = len(students_with_any_grade_ids)
        missing_students = [
            {
                "student_id": enrollment.student_id,
                "student_no": enrollment.student.student_no,
                "last_name": enrollment.student.last_name,
                "first_name": enrollment.student.first_name,
            }
            for enrollment in eligible_enrollments
            if enrollment.student_id not in students_with_any_grade_ids
        ]
        missing_count = len(missing_students)
        coverage_percent = cls._round((Decimal(students_with_any_grade) / Decimal(eligible_count)) * Decimal("100"))

        return {
            "eligible_student_count": eligible_count,
            "students_with_any_grade": students_with_any_grade,
            "students_missing_any_grade": missing_count,
            "coverage_percent": coverage_percent,
            "missing_students": missing_students,
        }

    @classmethod
    def resolve_lock(cls, *, offering, template_period: GradingTemplatePeriod):
        target_period_key = cls._normalize_period_key(template_period.code or template_period.name)
        lock_qs = GradingPeriodLock.objects.filter(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            academic_year_id=offering.academic_year_id,
            term_id=offering.term_id,
            is_active=True,
        ).order_by("-updated_at")

        matching_locks = [
            lock
            for lock in lock_qs
            if cls._normalize_period_key(lock.period_code) == target_period_key
        ]
        if not matching_locks:
            return None

        for lock in matching_locks:
            if (
                lock.scope_type == GradingPeriodLock.ScopeType.COURSE
                and lock.course_offering_id == offering.id
            ):
                return lock

        for lock in matching_locks:
            if (
                lock.scope_type == GradingPeriodLock.ScopeType.CAMPUS
                and lock.course_offering_id is None
            ):
                return lock
        return None

    @classmethod
    @transaction.atomic
    def auto_lock_due_periods(cls, *, at=None, limit: int | None = None, dry_run: bool = False):
        now = at or timezone.now()
        queryset = (
            GradingPeriodLock.objects.select_related("tenant", "campus", "academic_year", "term", "course_offering")
            .filter(
                is_active=True,
                is_locked=False,
                deadline_at__isnull=False,
                deadline_at__lte=now,
            )
            .order_by("deadline_at", "id")
        )
        if limit:
            queryset = queryset[:limit]

        locked_rows = []
        for lock in queryset:
            before_data = {
                "is_locked": lock.is_locked,
                "deadline_at": lock.deadline_at,
                "locked_at": lock.locked_at,
                "remarks": lock.remarks,
            }
            remarks = (lock.remarks or "").strip()
            auto_note = "Auto-locked by deadline."
            if auto_note not in remarks:
                remarks = f"{remarks} {auto_note}".strip() if remarks else auto_note

            locked_rows.append(
                {
                    "id": lock.id,
                    "tenant_code": getattr(lock.tenant, "code", None),
                    "campus_code": getattr(lock.campus, "code", None),
                    "academic_year_code": getattr(lock.academic_year, "code", None),
                    "term_code": getattr(lock.term, "code", None),
                    "period_code": lock.period_code,
                    "scope_type": lock.scope_type,
                    "course_offering_id": lock.course_offering_id,
                    "deadline_at": lock.deadline_at,
                    "before": before_data,
                    "after": {
                        "is_locked": True,
                        "deadline_at": lock.deadline_at,
                        "locked_at": now,
                        "remarks": remarks,
                    },
                }
            )

            if dry_run:
                continue

            lock.is_locked = True
            lock.locked_at = now
            lock.remarks = remarks
            lock.save(update_fields=["is_locked", "locked_at", "remarks", "updated_at"])

            AuditService.log_event(
                action="AUTO_LOCK",
                portal="ADMIN",
                entity_type="GradingPeriodLock",
                entity_id=lock.id,
                actor=None,
                tenant=lock.tenant,
                campus=lock.campus,
                before_data=before_data,
                after_data={
                    "is_locked": lock.is_locked,
                    "deadline_at": lock.deadline_at,
                    "locked_at": lock.locked_at,
                    "remarks": lock.remarks,
                },
                metadata={
                    "mode": "CRON_DEADLINE_AUTO_LOCK",
                    "scope_type": lock.scope_type,
                    "period_code": lock.period_code,
                    "course_offering_id": lock.course_offering_id,
                },
            )

        return {
            "checked_at": now,
            "count": len(locked_rows),
            "dry_run": dry_run,
            "rows": locked_rows,
        }

    @classmethod
    def get_submission(cls, *, offering, template_period: GradingTemplatePeriod):
        return GradeSubmission.objects.filter(
            offering_id=offering.id,
            template_period_id=template_period.id,
        ).order_by("-updated_at").first()

    @classmethod
    def is_locked(cls, *, offering, template_period: GradingTemplatePeriod):
        lock = cls.resolve_lock(offering=offering, template_period=template_period)
        return bool(lock and lock.is_locked)

    @classmethod
    def is_submitted(cls, *, offering, template_period: GradingTemplatePeriod):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        return bool(submission and submission.status == GradeSubmission.Status.SUBMITTED)

    @classmethod
    def get_active_unlock_window(cls, *, offering, template_period: GradingTemplatePeriod, at=None):
        now = at or timezone.now()
        return (
            GradeCorrectionUnlockWindow.objects.select_related("correction_request")
            .filter(
                offering_id=offering.id,
                template_period_id=template_period.id,
                is_active=True,
                is_consumed=False,
                start_at__lte=now,
                end_at__gte=now,
                correction_request__status=GradeCorrectionRequest.Status.APPROVED,
            )
            .order_by("-created_at")
            .first()
        )

    @classmethod
    def has_active_unlock_window(cls, *, offering, template_period: GradingTemplatePeriod, at=None):
        return bool(cls.get_active_unlock_window(offering=offering, template_period=template_period, at=at))

    @classmethod
    def get_active_correction_request(cls, *, offering, template_period: GradingTemplatePeriod, at=None):
        window = cls.get_active_unlock_window(offering=offering, template_period=template_period, at=at)
        return window.correction_request if window else None

    @classmethod
    def _is_in_correction_scope(
        cls,
        *,
        window: GradeCorrectionUnlockWindow,
        student_id: int | None = None,
        activity_id: int | None = None,
        requested_action: str | None = None,
    ):
        items = window.correction_request.items.filter(is_active=True)
        if student_id is not None:
            items = items.filter(Q(student_id=student_id) | Q(student__isnull=True))
        if activity_id is not None:
            items = items.filter(Q(grade_activity_id=activity_id) | Q(grade_activity__isnull=True))
        if requested_action:
            items = items.filter(requested_action=requested_action)
        return items.exists()

    @classmethod
    def is_edit_allowed_under_correction(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        student_id: int | None = None,
        activity_id: int | None = None,
        requested_action: str | None = None,
    ):
        window = cls.get_active_unlock_window(offering=offering, template_period=template_period)
        if not window:
            return False
        return cls._is_in_correction_scope(
            window=window,
            student_id=student_id,
            activity_id=activity_id,
            requested_action=requested_action,
        )

    @classmethod
    def assert_summary_compute_allowed(cls, *, offering, template_period: GradingTemplatePeriod):
        if cls.is_locked(offering=offering, template_period=template_period) or cls.is_submitted(
            offering=offering, template_period=template_period
        ):
            if not cls.has_active_unlock_window(offering=offering, template_period=template_period):
                raise ValidationError(f"{template_period.code} is locked/submitted and has no active correction window.")
        return True

    @classmethod
    def assert_encoding_allowed(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        student_id: int | None = None,
        activity_id: int | None = None,
        requested_action: str | None = None,
    ):
        is_locked = cls.is_locked(offering=offering, template_period=template_period)
        is_submitted = cls.is_submitted(offering=offering, template_period=template_period)
        if not is_locked and not is_submitted:
            return True

        if student_id is not None and requested_action:
            allowed = cls.is_edit_allowed_under_correction(
                offering=offering,
                template_period=template_period,
                student_id=student_id,
                activity_id=activity_id,
                requested_action=requested_action,
            )
            if allowed:
                return True

        if is_locked:
            raise ValidationError(f"{template_period.code} is locked by academic governance.")
        if is_submitted:
            raise ValidationError(f"{template_period.code} has already been submitted.")
        return True

    @classmethod
    @transaction.atomic
    def submit_period(cls, *, user, offering, template_period: GradingTemplatePeriod, remarks: str | None = None):
        cls.assert_encoding_allowed(offering=offering, template_period=template_period)
        readiness = cls.evaluate_submission_readiness(offering=offering, template_period=template_period)
        if readiness["eligible_student_count"] <= 0:
            raise ValidationError("No ACTIVE students available for submission in this period.")
        if readiness["students_with_any_grade"] <= 0:
            raise ValidationError(
                "Cannot submit yet. No grade records are encoded for ACTIVE students. "
                "Encode at least one grade/attendance record or mark students as DR/W first."
            )

        summary = FacultyGradingService.recompute_period_summary(
            user=user,
            offering=offering,
            template_period=template_period,
        )
        period_rows = StudentPeriodGrade.objects.filter(
            offering_id=offering.id,
            template_period_id=template_period.id,
        )
        period_rows.update(is_finalized=True)
        StudentFinalGrade.objects.filter(offering_id=offering.id).update(is_submitted=True)

        template = FacultyGradingService.resolve_template_for_offering(offering)
        submission, _ = GradeSubmission.objects.update_or_create(
            offering=offering,
            template_period=template_period,
            defaults={
                "tenant_id": offering.tenant_id,
                "campus_id": offering.campus_id,
                "status": GradeSubmission.Status.SUBMITTED,
                "submitted_by_user": user,
                "submitted_at": timezone.now(),
                "remarks": (remarks or "").strip() or None,
                "submission_snapshot_json": {
                    "component_codes": summary.get("component_codes", []),
                    "student_count": len(summary.get("rows", [])),
                    "submitted_at": timezone.now().isoformat(),
                },
                "template_snapshot_json": {
                    "template_id": template.id,
                    "template_code": template.code,
                    "template_name": template.name,
                    "period_code": template_period.code,
                    "period_name": template_period.name,
                },
            },
        )
        return submission

    @classmethod
    @transaction.atomic
    def reopen_period(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        remarks: str | None = None,
    ):
        submission = cls.get_submission(offering=offering, template_period=template_period)
        if submission:
            submission.status = GradeSubmission.Status.REOPENED
            submission.reopened_by_user = user
            submission.reopened_at = timezone.now()
            submission.remarks = (remarks or "").strip() or submission.remarks
            submission.save(
                update_fields=["status", "reopened_by_user", "reopened_at", "remarks", "updated_at"]
            )

        StudentPeriodGrade.objects.filter(
            offering_id=offering.id,
            template_period_id=template_period.id,
        ).update(is_finalized=False)
        StudentFinalGrade.objects.filter(offering_id=offering.id).update(is_submitted=False)
        return submission

    @classmethod
    @transaction.atomic
    def create_reopen_request(
        cls,
        *,
        user,
        submission: GradeSubmission,
        justification: str,
    ):
        if submission.status != GradeSubmission.Status.SUBMITTED:
            raise ValidationError("Only submitted grade periods can be reopened by request.")

        if GradeSubmissionReopenRequest.objects.filter(
            submission=submission,
            status=GradeSubmissionReopenRequest.Status.PENDING,
        ).exists():
            raise ValidationError("A pending reopen request already exists for this submission.")

        return GradeSubmissionReopenRequest.objects.create(
            tenant_id=submission.tenant_id,
            campus_id=submission.campus_id,
            submission=submission,
            offering_id=submission.offering_id,
            template_period_id=submission.template_period_id,
            requested_by_user=user,
            status=GradeSubmissionReopenRequest.Status.PENDING,
            justification=justification.strip(),
        )

    @classmethod
    @transaction.atomic
    def review_reopen_request(
        cls,
        *,
        request_obj: GradeSubmissionReopenRequest,
        reviewer,
        approved: bool,
        review_remarks: str | None = None,
    ):
        if request_obj.status != GradeSubmissionReopenRequest.Status.PENDING:
            raise ValidationError("Only pending reopen requests can be reviewed.")

        request_obj.reviewed_by_user = reviewer
        request_obj.reviewed_at = timezone.now()
        request_obj.review_remarks = (review_remarks or "").strip() or None

        if approved:
            if request_obj.submission.status != GradeSubmission.Status.SUBMITTED:
                raise ValidationError("This submission is no longer in submitted status.")
            cls.reopen_period(
                user=reviewer,
                offering=request_obj.offering,
                template_period=request_obj.template_period,
                remarks=request_obj.review_remarks,
            )
            request_obj.status = GradeSubmissionReopenRequest.Status.APPROVED
        else:
            request_obj.status = GradeSubmissionReopenRequest.Status.REJECTED

        request_obj.save(
            update_fields=[
                "status",
                "reviewed_by_user",
                "reviewed_at",
                "review_remarks",
                "updated_at",
            ]
        )
        return request_obj

    @classmethod
    @transaction.atomic
    def create_correction_request(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        justification: str,
        items: list[dict],
    ):
        if not cls.is_submitted(offering=offering, template_period=template_period):
            raise ValidationError("Correction requests are allowed only after period submission.")
        if not items:
            raise ValidationError("At least one correction scope item is required.")

        request_obj = GradeCorrectionRequest.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            offering=offering,
            template_period=template_period,
            requested_by_user=user,
            status=GradeCorrectionRequest.Status.PENDING,
            justification=justification.strip(),
        )
        item_rows = []
        for item in items:
            item_rows.append(
                GradeCorrectionRequestItem(
                    correction_request=request_obj,
                    requested_action=item["requested_action"],
                    student_id=item.get("student_id"),
                    grade_activity_id=item.get("grade_activity_id"),
                    old_value=(item.get("old_value") or "")[:255] or None,
                    new_value=(item.get("new_value") or "")[:255] or None,
                    is_active=True,
                )
            )
        GradeCorrectionRequestItem.objects.bulk_create(item_rows)
        return request_obj

    @classmethod
    @transaction.atomic
    def review_correction_request(
        cls,
        *,
        request_obj: GradeCorrectionRequest,
        reviewer,
        approved: bool,
        review_remarks: str | None = None,
        window_start=None,
        window_end=None,
    ):
        if request_obj.status != GradeCorrectionRequest.Status.PENDING:
            raise ValidationError("Only pending correction requests can be reviewed.")

        now = timezone.now()
        request_obj.reviewed_by_user = reviewer
        request_obj.reviewed_at = now
        request_obj.review_remarks = (review_remarks or "").strip() or None

        if approved:
            if not window_start or not window_end:
                raise ValidationError("Window start and end are required for approval.")
            if window_end <= window_start:
                raise ValidationError("Correction window end must be later than start.")
            request_obj.status = GradeCorrectionRequest.Status.APPROVED

            GradeCorrectionUnlockWindow.objects.filter(
                offering_id=request_obj.offering_id,
                template_period_id=request_obj.template_period_id,
                is_active=True,
                is_consumed=False,
            ).update(is_active=False, is_consumed=True, closed_at=now)

            GradeCorrectionUnlockWindow.objects.update_or_create(
                correction_request=request_obj,
                defaults={
                    "offering_id": request_obj.offering_id,
                    "template_period_id": request_obj.template_period_id,
                    "start_at": window_start,
                    "end_at": window_end,
                    "is_active": True,
                    "is_consumed": False,
                    "closed_at": None,
                },
            )
        else:
            request_obj.status = GradeCorrectionRequest.Status.REJECTED

        request_obj.save(
            update_fields=[
                "status",
                "reviewed_by_user",
                "reviewed_at",
                "review_remarks",
                "updated_at",
            ]
        )
        return request_obj

    @classmethod
    @transaction.atomic
    def close_correction_window(cls, *, request_obj: GradeCorrectionRequest, actor=None):
        window = getattr(request_obj, "unlock_window", None)
        now = timezone.now()
        if window and window.is_active and not window.is_consumed:
            window.is_active = False
            window.is_consumed = True
            window.closed_at = now
            window.save(update_fields=["is_active", "is_consumed", "closed_at", "updated_at"])

        if request_obj.status == GradeCorrectionRequest.Status.APPROVED:
            request_obj.status = GradeCorrectionRequest.Status.CLOSED
            request_obj.save(update_fields=["status", "updated_at"])
        return request_obj


class FacultyGradingService:
    ATTENDANCE_SCORE_MAP = {
        AttendanceRecord.Status.PRESENT: Decimal("100"),
        AttendanceRecord.Status.EXCUSED: Decimal("100"),
        AttendanceRecord.Status.LATE: Decimal("90"),
        AttendanceRecord.Status.ABSENT: Decimal("0"),
    }

    @staticmethod
    def _round(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    @classmethod
    def resolve_score_input_mode(
        cls,
        *,
        template_component: GradingTemplateComponent,
        template_subcomponent: GradingTemplateSubcomponent | None = None,
        template_detail: GradingTemplateDetail | None = None,
    ) -> str:
        if template_detail and getattr(template_detail, "score_input_mode", "INHERIT") != "INHERIT":
            return template_detail.score_input_mode
        if template_subcomponent and getattr(template_subcomponent, "score_input_mode", "INHERIT") != "INHERIT":
            return template_subcomponent.score_input_mode
        return getattr(template_component, "score_input_mode", "RAW_BASE50") or "RAW_BASE50"

    @classmethod
    def score_input_mode_label(cls, score_input_mode: str) -> str:
        return {
            "RAW_BASE50": "Raw Score (Base-50)",
            "DIRECT_PERCENTAGE": "Direct Percentage",
        }.get(score_input_mode, "Raw Score (Base-50)")

    @classmethod
    def resolve_template_for_offering(cls, offering):
        assignments = (
            CourseTemplateAssignment.objects.filter(
                course_id=offering.course_id,
                is_active=True,
                grading_template__is_active=True,
                grading_template__is_published=True,
            )
            .select_related("grading_template", "effective_from_term")
            .order_by("-created_at")
        )

        exact = assignments.filter(effective_from_term_id=offering.term_id).first()
        if exact:
            return exact.grading_template

        no_effective_term = assignments.filter(effective_from_term__isnull=True).first()
        if no_effective_term:
            return no_effective_term.grading_template

        profile = cls.resolve_grading_profile_for_offering(offering)
        if profile:
            return profile.grading_template

        fallback = (
            GradingTemplate.objects.filter(
                tenant_id=offering.tenant_id,
                is_active=True,
                is_published=True,
            )
            .order_by("-published_at", "-created_at")
            .first()
        )
        if fallback:
            return fallback
        raise ValidationError("No published grading template is assigned for this course offering.")

    @classmethod
    def resolve_grading_profile_for_offering(cls, offering):
        course_type = (offering.course.course_type or "").strip()
        # Offerings may be shared/open and keep program null; in that case fall back to section program.
        effective_program_id = offering.program_id
        if not effective_program_id and offering.section_id:
            section_obj = getattr(offering, "section", None)
            if section_obj is None:
                section_obj = offering.section
            effective_program_id = section_obj.program_id if section_obj else None

        profiles_qs = (
            TenantGradingProfile.objects.filter(
                tenant_id=offering.tenant_id,
                is_active=True,
                grading_template__is_active=True,
                grading_template__is_published=True,
            )
            .filter(Q(campus_id=offering.campus_id) | Q(campus__isnull=True))
            .filter(Q(department_id=offering.department_id) | Q(department__isnull=True))
            .filter(Q(program_id=effective_program_id) | Q(program__isnull=True))
            .filter(Q(course_id=offering.course_id) | Q(course__isnull=True))
            .filter(Q(effective_from_term_id=offering.term_id) | Q(effective_from_term__isnull=True))
            .select_related("grading_template")
        )
        if course_type:
            profiles_qs = profiles_qs.filter(Q(course_type__iexact=course_type) | Q(course_type__isnull=True) | Q(course_type=""))
        else:
            profiles_qs = profiles_qs.filter(
                Q(course_type__isnull=True) | Q(course_type="") | Q(course_id=offering.course_id)
            )

        profiles = list(profiles_qs)
        if not profiles:
            return None

        def specificity_score(profile):
            return (
                1 if profile.course_id else 0,
                1 if (profile.course_type or "").strip() else 0,
                1 if profile.program_id else 0,
                1 if profile.department_id else 0,
                1 if profile.campus_id else 0,
                1 if profile.effective_from_term_id else 0,
            )

        def sort_key(profile):
            score = specificity_score(profile)
            return (
                -score[0],
                -score[1],
                -score[2],
                -score[3],
                -score[4],
                -score[5],
                profile.priority,
                0 if profile.is_default else 1,
                -profile.id,
            )

        profiles.sort(key=sort_key)
        return profiles[0]

    @classmethod
    def resolve_base_value(cls, offering, template):
        override = (
            CourseBaseValueOverride.objects.filter(
                course_id=offering.course_id,
                is_active=True,
            )
            .filter(Q(effective_from_term_id=offering.term_id) | Q(effective_from_term__isnull=True))
            .order_by("-effective_from_term_id", "-created_at")
            .first()
        )
        if override:
            return Decimal(override.base_value)
        profile = cls.resolve_grading_profile_for_offering(offering)
        if profile and profile.default_base_value is not None:
            return Decimal(profile.default_base_value)
        if offering.course.default_base_value is not None:
            return Decimal(offering.course.default_base_value)
        if template.default_base_value is not None:
            return Decimal(template.default_base_value)
        return Decimal("50")

    @staticmethod
    def get_active_enrollments(offering):
        return (
            Enrollment.objects.filter(course_offering_id=offering.id, is_active=True)
            .select_related("student")
            .order_by("student__last_name", "student__first_name", "student__student_no")
        )

    @staticmethod
    def get_template_periods(template):
        return template.periods.filter(is_active=True).order_by("sequence_no", "id")

    @staticmethod
    def user_can_manage_offering(user, offering):
        return offering.faculty_assignments.filter(
            faculty_user_id=user.id,
            is_active=True,
        ).exists()

    @classmethod
    def compute_activity_score(
        cls,
        *,
        raw_score: Decimal,
        total_score: Decimal,
        base_value: Decimal,
        score_input_mode: str = "RAW_BASE50",
    ):
        if raw_score < 0:
            raise ValidationError("Score cannot be negative.")
        if score_input_mode == "DIRECT_PERCENTAGE":
            if raw_score > Decimal("100"):
                raise ValidationError("Direct percentage score cannot be greater than 100.")
            return cls._round(raw_score)
        if total_score <= 0:
            raise ValidationError("Total score must be greater than zero.")
        computed = ((raw_score / total_score) * base_value) + (Decimal("100") - base_value)
        return cls._round(computed)

    @classmethod
    def _validate_activity_structure(
        cls,
        *,
        template_period: GradingTemplatePeriod,
        template_component: GradingTemplateComponent,
        template_subcomponent: GradingTemplateSubcomponent | None,
        template_detail: GradingTemplateDetail | None,
    ):
        if template_component.template_period_id != template_period.id:
            raise ValidationError("Selected component does not belong to selected period.")
        has_subcomponents = template_component.subcomponents.filter(is_active=True).exists()
        if has_subcomponents and not template_subcomponent:
            raise ValidationError("Selected component requires a subcomponent.")
        if template_subcomponent and template_subcomponent.template_component_id != template_component.id:
            raise ValidationError("Selected subcomponent does not belong to selected component.")
        has_details = (
            template_subcomponent.details.filter(is_active=True).exists() if template_subcomponent else False
        )
        if has_details and not template_detail:
            raise ValidationError("Selected subcomponent requires a detail selection.")
        if template_detail and not template_subcomponent:
            raise ValidationError("Detail requires selected subcomponent.")
        if template_detail and template_detail.template_subcomponent_id != template_subcomponent.id:
            raise ValidationError("Selected detail does not belong to selected subcomponent.")

    @classmethod
    @transaction.atomic
    def create_activity(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        template_component: GradingTemplateComponent,
        template_subcomponent: GradingTemplateSubcomponent | None,
        template_detail: GradingTemplateDetail | None,
        title: str,
        total_score: Decimal,
        activity_date,
    ):
        cls._validate_activity_structure(
            template_period=template_period,
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
        )
        score_input_mode = cls.resolve_score_input_mode(
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
        )
        GradingGovernanceService.assert_encoding_allowed(offering=offering, template_period=template_period)

        return GradeActivity.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            offering=offering,
            template_period=template_period,
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
            title=title.strip(),
            total_score=Decimal("100") if score_input_mode == "DIRECT_PERCENTAGE" else total_score,
            activity_date=activity_date,
            created_by_user=user,
            is_active=True,
        )

    @classmethod
    @transaction.atomic
    def update_activity(
        cls,
        *,
        user,
        activity: GradeActivity,
        template_period: GradingTemplatePeriod,
        template_component: GradingTemplateComponent,
        template_subcomponent: GradingTemplateSubcomponent | None,
        template_detail: GradingTemplateDetail | None,
        title: str,
        total_score: Decimal,
        activity_date,
    ):
        cls._validate_activity_structure(
            template_period=template_period,
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
        )
        GradingGovernanceService.assert_encoding_allowed(
            offering=activity.offering,
            template_period=template_period,
        )
        score_input_mode = cls.resolve_score_input_mode(
            template_component=template_component,
            template_subcomponent=template_subcomponent,
            template_detail=template_detail,
        )

        activity.template_component = template_component
        activity.template_subcomponent = template_subcomponent
        activity.template_detail = template_detail
        activity.title = title.strip()
        activity.total_score = Decimal("100") if score_input_mode == "DIRECT_PERCENTAGE" else total_score
        activity.activity_date = activity_date
        activity.save(
            update_fields=[
                "template_component",
                "template_subcomponent",
                "template_detail",
                "title",
                "total_score",
                "activity_date",
                "updated_at",
            ]
        )

        recomputed_score_count = 0
        active_scores = list(activity.student_scores.filter(is_active=True))
        if active_scores:
            template = cls.resolve_template_for_offering(activity.offering)
            base_value = cls.resolve_base_value(activity.offering, template)
            for score in active_scores:
                score.computed_score = cls.compute_activity_score(
                    raw_score=Decimal(score.raw_score or 0),
                    total_score=Decimal(activity.total_score),
                    base_value=base_value,
                    score_input_mode=score_input_mode,
                )
                score.encoded_by_user = user
                score.save(update_fields=["computed_score", "encoded_by_user", "updated_at"])
                recomputed_score_count += 1

        cls.recompute_period_summary(
            user=user,
            offering=activity.offering,
            template_period=template_period,
        )
        return activity, recomputed_score_count

    @classmethod
    @transaction.atomic
    def upsert_activity_scores(cls, *, user, activity: GradeActivity, score_payload: list[dict]):
        template = cls.resolve_template_for_offering(activity.offering)
        base_value = cls.resolve_base_value(activity.offering, template)
        score_input_mode = cls.resolve_score_input_mode(
            template_component=activity.template_component,
            template_subcomponent=activity.template_subcomponent,
            template_detail=activity.template_detail,
        )
        enrolled_student_ids = set(
            cls.get_active_enrollments(activity.offering).values_list("student_id", flat=True)
        )

        saved = 0
        for row in score_payload:
            student_id = int(row["student_id"])
            if student_id not in enrolled_student_ids:
                continue
            GradingGovernanceService.assert_encoding_allowed(
                offering=activity.offering,
                template_period=activity.template_period,
                student_id=student_id,
                activity_id=activity.id,
                requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
            )
            if row.get("clear"):
                StudentActivityScore.objects.filter(
                    activity=activity,
                    student_id=student_id,
                    is_active=True,
                ).update(is_active=False)
                continue
            raw = Decimal(str(row.get("raw_score", "0") or "0"))
            remarks = row.get("remarks") or ""
            computed = cls.compute_activity_score(
                raw_score=raw,
                total_score=Decimal(activity.total_score),
                base_value=base_value,
                score_input_mode=score_input_mode,
            )
            StudentActivityScore.objects.update_or_create(
                activity=activity,
                student_id=student_id,
                defaults={
                    "raw_score": raw,
                    "computed_score": computed,
                    "encoded_by_user": user,
                    "remarks": remarks[:255] if remarks else None,
                    "is_active": True,
                },
            )
            saved += 1
        return saved

    @classmethod
    @transaction.atomic
    def create_or_update_attendance_session(
        cls,
        *,
        user,
        offering,
        template_period: GradingTemplatePeriod,
        session_date,
        title: str | None = None,
    ):
        GradingGovernanceService.assert_encoding_allowed(offering=offering, template_period=template_period)
        if isinstance(session_date, str):
            parsed = parse_date(session_date)
            if parsed is None:
                raise ValidationError("Invalid session date.")
            session_date = parsed

        session, created = AttendanceSession.objects.update_or_create(
            offering=offering,
            template_period=template_period,
            session_date=session_date,
            defaults={
                "tenant_id": offering.tenant_id,
                "campus_id": offering.campus_id,
                "title": (title or "").strip() or None,
                "created_by_user": user,
                "is_active": True,
            },
        )
        return session, created

    @classmethod
    @transaction.atomic
    def archive_activity(cls, *, user, activity: GradeActivity):
        GradingGovernanceService.assert_encoding_allowed(
            offering=activity.offering,
            template_period=activity.template_period,
        )

        activity.is_active = False
        activity.save(update_fields=["is_active", "updated_at"])
        activity.student_scores.filter(is_active=True).update(is_active=False, updated_at=timezone.now())

        cls.recompute_period_summary(
            user=user,
            offering=activity.offering,
            template_period=activity.template_period,
        )
        return activity

    @classmethod
    @transaction.atomic
    def upsert_attendance_records(cls, *, user, session: AttendanceSession, status_payload: list[dict]):
        enrolled_student_ids = set(
            cls.get_active_enrollments(session.offering).values_list("student_id", flat=True)
        )
        saved = 0
        for row in status_payload:
            student_id = int(row["student_id"])
            if student_id not in enrolled_student_ids:
                continue
            GradingGovernanceService.assert_encoding_allowed(
                offering=session.offering,
                template_period=session.template_period,
                student_id=student_id,
                requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_ATTENDANCE,
            )
            status_code = str(row.get("status_code") or AttendanceRecord.Status.PRESENT).upper()
            if status_code not in cls.ATTENDANCE_SCORE_MAP:
                status_code = AttendanceRecord.Status.PRESENT
            remarks = (row.get("remarks") or "").strip()
            AttendanceRecord.objects.update_or_create(
                session=session,
                student_id=student_id,
                defaults={
                    "tenant_id": session.tenant_id,
                    "campus_id": session.campus_id,
                    "status_code": status_code,
                    "recorded_by_user": user,
                    "remarks": remarks[:255] if remarks else None,
                    "is_active": True,
                },
            )
            saved += 1
        return saved

    @classmethod
    def _attendance_subcomponent_score(
        cls,
        *,
        offering,
        template_period: GradingTemplatePeriod,
        student_id: int,
        base_value: Decimal,
    ):
        records = AttendanceRecord.objects.filter(
            session__offering_id=offering.id,
            session__template_period_id=template_period.id,
            session__is_active=True,
            student_id=student_id,
            is_active=True,
        ).select_related("session")

        if not records.exists():
            return None

        computed_scores = []
        for record in records:
            raw = cls.ATTENDANCE_SCORE_MAP.get(record.status_code, Decimal("0"))
            computed = cls.compute_activity_score(
                raw_score=raw,
                total_score=Decimal("100"),
                base_value=base_value,
                score_input_mode="DIRECT_PERCENTAGE",
            )
            computed_scores.append(computed)
        return cls._round(sum(computed_scores) / Decimal(len(computed_scores)))

    @classmethod
    def _average_score_or_none(cls, score_lookup, key):
        vals = score_lookup.get(key, [])
        if not vals:
            return None
        return cls._round(sum(vals) / Decimal(len(vals)))

    @classmethod
    @transaction.atomic
    def recompute_period_summary(cls, *, user, offering, template_period: GradingTemplatePeriod):
        GradingGovernanceService.assert_summary_compute_allowed(
            offering=offering,
            template_period=template_period,
        )
        template = cls.resolve_template_for_offering(offering)
        base_value = cls.resolve_base_value(offering, template)

        enrollments = list(cls.get_active_enrollments(offering))
        components = list(
            template_period.components.filter(is_active=True)
            .prefetch_related("subcomponents", "subcomponents__details")
            .order_by("sort_order", "id")
        )

        activity_scores = StudentActivityScore.objects.filter(
            activity__offering_id=offering.id,
            activity__template_period_id=template_period.id,
            activity__is_active=True,
            is_active=True,
        ).select_related("activity")

        score_lookup = defaultdict(list)
        for score in activity_scores:
            key = (
                score.student_id,
                score.activity.template_component_id,
                score.activity.template_subcomponent_id,
                score.activity.template_detail_id,
            )
            score_lookup[key].append(Decimal(score.computed_score or 0))

        rows = []
        period_values_for_final = defaultdict(list)

        for enrollment in enrollments:
            student = enrollment.student
            student_id = student.id

            if enrollment.enrollment_status in {Enrollment.Status.DR, Enrollment.Status.W}:
                StudentPeriodGrade.objects.update_or_create(
                    offering=offering,
                    template_period=template_period,
                    student=student,
                    defaults={
                        "tenant_id": offering.tenant_id,
                        "campus_id": offering.campus_id,
                        "class_standing_grade": None,
                        "exam_grade": None,
                        "period_grade": None,
                        "computed_by_user": user,
                        "is_finalized": False,
                    },
                )
                rows.append(
                    {
                        "student": student,
                        "enrollment_status": enrollment.enrollment_status,
                        "component_scores": {},
                        "class_standing": None,
                        "exam_grade": None,
                        "period_grade": None,
                    }
                )
                continue

            component_scores = {}
            class_standing = Decimal("0")
            exam_grade = None
            weighted_period_grade = Decimal("0")
            has_exam_component = False
            has_exam_data = False

            for component in components:
                subcomponents = list(component.subcomponents.filter(is_active=True).order_by("sort_order", "id"))
                component_has_data = False
                if subcomponents:
                    sub_total = sum(Decimal(sub.weight_percentage or 0) for sub in subcomponents)
                    sub_denominator = sub_total if sub_total > 0 else Decimal("100")
                    component_raw = Decimal("0")
                    for sub in subcomponents:
                        detail_rows = list(sub.details.filter(is_active=True).order_by("sort_order", "id"))
                        if sub.is_attendance_component:
                            sub_score = cls._attendance_subcomponent_score(
                                offering=offering,
                                template_period=template_period,
                                student_id=student_id,
                                base_value=base_value,
                            )
                        elif detail_rows:
                            detail_total = sum(Decimal(detail.weight_percentage or 0) for detail in detail_rows)
                            detail_denominator = detail_total if detail_total > 0 else Decimal("100")
                            detail_raw = Decimal("0")
                            detail_has_data = False
                            for detail in detail_rows:
                                detail_score = cls._average_score_or_none(
                                    score_lookup,
                                    (student_id, component.id, sub.id, detail.id),
                                )
                                if detail_score is not None:
                                    detail_has_data = True
                                    detail_raw += (Decimal(detail.weight_percentage) / detail_denominator) * detail_score
                            sub_score = cls._round(detail_raw)
                            if detail_has_data:
                                component_has_data = True
                        else:
                            sub_score = cls._average_score_or_none(
                                score_lookup,
                                (student_id, component.id, sub.id, None),
                            )
                            if sub_score is not None:
                                component_has_data = True
                        if sub_score is not None:
                            component_raw += (Decimal(sub.weight_percentage) / sub_denominator) * sub_score
                    component_score = cls._round(component_raw)
                else:
                    component_score = cls._average_score_or_none(
                        score_lookup,
                        (student_id, component.id, None, None),
                    )
                    component_has_data = component_score is not None
                component_scores[component.code] = component_score

                if "EXAM" in component.code.upper():
                    has_exam_component = True
                    if component_has_data:
                        exam_grade = (exam_grade or Decimal("0")) + component_score
                        has_exam_data = True
                else:
                    class_standing += component_score or Decimal("0")
                weighted_period_grade += (Decimal(component.weight_percentage) / Decimal("100")) * (
                    component_score or Decimal("0")
                )

            class_standing = cls._round(class_standing)
            exam_grade = cls._round(exam_grade) if exam_grade is not None else None
            period_grade = None
            if not has_exam_component or has_exam_data:
                period_grade = cls._round(weighted_period_grade)

            StudentPeriodGrade.objects.update_or_create(
                offering=offering,
                template_period=template_period,
                student=student,
                defaults={
                    "tenant_id": offering.tenant_id,
                    "campus_id": offering.campus_id,
                    "class_standing_grade": class_standing,
                    "exam_grade": exam_grade,
                    "period_grade": period_grade,
                    "computed_by_user": user,
                    "is_finalized": False,
                },
            )
            period_values_for_final[student_id].append(period_grade)
            rows.append(
                {
                    "student": student,
                    "enrollment_status": enrollment.enrollment_status,
                    "component_scores": component_scores,
                    "class_standing": class_standing,
                    "exam_grade": exam_grade,
                    "period_grade": period_grade,
                }
            )

        all_period_codes = list(
            template.periods.filter(is_active=True).order_by("sequence_no").values_list("id", flat=True)
        )
        period_grade_map = defaultdict(dict)
        existing_period_rows = StudentPeriodGrade.objects.filter(
            offering=offering,
            template_period_id__in=all_period_codes,
        )
        for row in existing_period_rows:
            if row.period_grade is not None:
                period_grade_map[row.student_id][row.template_period_id] = Decimal(row.period_grade)

        for enrollment in enrollments:
            student_id = enrollment.student_id
            if enrollment.enrollment_status in {Enrollment.Status.DR, Enrollment.Status.W}:
                final_value = None
            else:
                vals = list(period_grade_map.get(student_id, {}).values())
                final_value = cls._round(sum(vals) / Decimal(len(vals))) if vals else None
            StudentFinalGrade.objects.update_or_create(
                offering=offering,
                student_id=student_id,
                defaults={
                    "tenant_id": offering.tenant_id,
                    "campus_id": offering.campus_id,
                    "final_grade": final_value,
                    "computed_by_user": user,
                    "is_submitted": False,
                },
            )

        return {
            "rows": rows,
            "component_codes": [c.code for c in components],
            "base_value": base_value,
        }
