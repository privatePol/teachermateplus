from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
import json

from apps.academics.models import (
    AcademicYear,
    ActiveGradingPeriodSetting,
    FacultyAssignment,
    FacultyAssignmentReplacementLog,
    TenantTermGradingPeriod,
    Term,
)
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
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
from apps.notifications.models import NotificationQueue


class AcademicGovernanceService:
    ACTIVE_AY_KEY = "ACTIVE_ACADEMIC_YEAR_CODE"
    ACTIVE_TERM_KEY = "ACTIVE_TERM_CODE"
    ACTIVE_GRADING_PERIOD_AUTO_ADVANCE_KEY = "ACTIVE_GRADING_PERIOD_AUTO_ADVANCE_ENABLED"
    STANDARD_PERIODS = (
        ("PRELIM", "Prelim", 1),
        ("MIDTERM", "Midterm", 2),
        ("PREFINAL", "Pre-Final", 3),
        ("FINAL", "Final", 4),
    )

    @classmethod
    def get_active_codes(cls, *, tenant_id: int):
        ay_code = (
            str(
                SystemSettingService.get(
                    cls.ACTIVE_AY_KEY,
                    tenant_id=tenant_id,
                    default="",
                )
                or ""
            )
            .strip()
            .upper()
        )
        term_code = (
            str(
                SystemSettingService.get(
                    cls.ACTIVE_TERM_KEY,
                    tenant_id=tenant_id,
                    default="",
                )
                or ""
            )
            .strip()
            .upper()
        )
        return ay_code, term_code

    @classmethod
    def resolve_active_scope(cls, *, tenant_id: int):
        ay_code, term_code = cls.get_active_codes(tenant_id=tenant_id)
        if not ay_code or not term_code:
            return None, None

        academic_year = (
            AcademicYear.objects.filter(tenant_id=tenant_id, is_active=True)
            .filter(Q(code__iexact=ay_code) | Q(name__iexact=ay_code))
            .order_by("-start_date", "-id")
            .first()
        )
        if not academic_year:
            return None, None

        term = (
            Term.objects.filter(
                tenant_id=tenant_id,
                academic_year_id=academic_year.id,
                is_active=True,
            )
            .filter(Q(code__iexact=term_code) | Q(name__iexact=term_code))
            .order_by("sequence_no", "id")
            .first()
        )
        if not term:
            return academic_year, None
        return academic_year, term

    @classmethod
    def set_active_scope(
        cls,
        *,
        tenant_id: int,
        academic_year: AcademicYear | None,
        term: Term | None,
    ):
        if academic_year and term and term.academic_year_id != academic_year.id:
            raise ValueError("Active term must belong to the selected academic year.")

        if academic_year is None or term is None:
            SystemSettingService.set(
                cls.ACTIVE_AY_KEY,
                "",
                tenant_id=tenant_id,
                value_type="STRING",
                is_active=False,
            )
            SystemSettingService.set(
                cls.ACTIVE_TERM_KEY,
                "",
                tenant_id=tenant_id,
                value_type="STRING",
                is_active=False,
            )
            return

        SystemSettingService.set(
            cls.ACTIVE_AY_KEY,
            academic_year.code,
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            cls.ACTIVE_TERM_KEY,
            term.code,
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )

    @staticmethod
    def normalize_period_key(value: str | None) -> str:
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
    def is_active_grading_period_auto_advance_enabled(cls, *, tenant_id: int | None, default=True):
        value = SystemSettingService.get(
            cls.ACTIVE_GRADING_PERIOD_AUTO_ADVANCE_KEY,
            tenant_id=tenant_id,
            default=default,
        )
        return bool(value)

    @classmethod
    def set_active_grading_period_auto_advance_enabled(cls, *, tenant_id: int, enabled: bool):
        SystemSettingService.set(
            cls.ACTIVE_GRADING_PERIOD_AUTO_ADVANCE_KEY,
            "1" if enabled else "0",
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
            description="Automatically advance the active grading period when the current period deadline passes.",
        )

    @classmethod
    def get_term_grading_periods(cls, *, tenant_id: int, term_id: int | None):
        queryset = TenantTermGradingPeriod.objects.filter(tenant_id=tenant_id, is_active=True)
        if term_id:
            queryset = queryset.filter(term_id=term_id)
        return queryset.order_by("sequence_no", "name", "id")

    @classmethod
    def get_term_grading_period_catalog(cls, *, tenant_id: int, term_id: int | None):
        queryset = TenantTermGradingPeriod.objects.filter(tenant_id=tenant_id)
        if term_id:
            queryset = queryset.filter(term_id=term_id)
        return queryset.order_by("sequence_no", "name", "id")

    @classmethod
    def seed_standard_term_periods(cls, *, tenant_id: int, term: Term):
        created = []
        for code, name, sequence_no in cls.STANDARD_PERIODS:
            row, was_created = TenantTermGradingPeriod.objects.get_or_create(
                tenant_id=tenant_id,
                term=term,
                code=code,
                defaults={
                    "name": name,
                    "sequence_no": sequence_no,
                    "is_active": True,
                },
            )
            was_reactivated = False
            if not was_created and not row.is_active:
                row.is_active = True
                row.save(update_fields=["is_active", "updated_at"])
                was_reactivated = True
            if was_created or was_reactivated:
                created.append(row)
        return created

    @classmethod
    def resolve_term_period(cls, *, tenant_id: int, term: Term, code_or_name: str | None):
        normalized = cls.normalize_period_key(code_or_name)
        if not normalized:
            return None
        for row in cls.get_term_grading_periods(tenant_id=tenant_id, term_id=term.id):
            if (
                cls.normalize_period_key(row.code) == normalized
                or cls.normalize_period_key(row.name) == normalized
            ):
                return row
        return None

    @classmethod
    def resolve_term_period_for_template_period(cls, *, tenant_id: int, term_id: int | None, template_period):
        if not template_period or not term_id:
            return None
        candidates = {
            cls.normalize_period_key(getattr(template_period, "code", None)),
            cls.normalize_period_key(getattr(template_period, "name", None)),
        }
        candidates.discard("")
        for row in cls.get_term_grading_periods(tenant_id=tenant_id, term_id=term_id):
            row_keys = {
                cls.normalize_period_key(row.code),
                cls.normalize_period_key(row.name),
            }
            if candidates & row_keys:
                return row
        return None

    @classmethod
    def _resolve_deadline_for_period_setting(cls, *, setting: ActiveGradingPeriodSetting):
        normalized_code = cls.normalize_period_key(setting.period.code or setting.period.name)
        lock_queryset = (
            GradingPeriodLock.objects.filter(
                tenant_id=setting.tenant_id,
                campus_id=setting.campus_id,
                academic_year_id=setting.term.academic_year_id,
                term_id=setting.term_id,
                is_active=True,
                deadline_at__isnull=False,
                scope_type=GradingPeriodLock.ScopeType.CAMPUS,
                course_offering__isnull=True,
            )
            .order_by("-updated_at", "-id")
        )
        for lock in lock_queryset:
            if cls.normalize_period_key(lock.period_code) == normalized_code:
                return lock
        return None

    @classmethod
    def _auto_advance_setting_if_due(cls, setting: ActiveGradingPeriodSetting, *, now=None):
        if not setting or not setting.period_id:
            return setting
        if not cls.is_active_grading_period_auto_advance_enabled(tenant_id=setting.tenant_id, default=True):
            return setting
        now = now or timezone.now()
        deadline_lock = cls._resolve_deadline_for_period_setting(setting=setting)
        if not deadline_lock or not deadline_lock.deadline_at or now <= deadline_lock.deadline_at:
            return setting
        next_period = (
            TenantTermGradingPeriod.objects.filter(
                tenant_id=setting.tenant_id,
                term_id=setting.term_id,
                is_active=True,
                sequence_no__gt=setting.period.sequence_no,
            )
            .order_by("sequence_no", "id")
            .first()
        )
        if not next_period:
            return setting
        setting.period = next_period
        setting.set_at = now
        setting.auto_advanced_from_deadline = True
        existing_remarks = (setting.remarks or "").strip()
        auto_note = (
            f"Auto-advanced from {deadline_lock.period_code} after deadline {deadline_lock.deadline_at:%Y-%m-%d %H:%M}."
        )
        setting.remarks = f"{existing_remarks}\n{auto_note}".strip() if existing_remarks else auto_note
        setting.save(
            update_fields=[
                "period",
                "set_at",
                "auto_advanced_from_deadline",
                "remarks",
                "updated_at",
            ]
        )
        return setting

    @classmethod
    def resolve_active_grading_period(cls, *, tenant_id: int, campus_id: int | None, term_id: int | None, now=None):
        if not tenant_id or not campus_id or not term_id:
            return None
        setting = (
            ActiveGradingPeriodSetting.objects.select_related("period", "term", "campus", "set_by_user")
            .filter(
                tenant_id=tenant_id,
                campus_id=campus_id,
                term_id=term_id,
                is_active=True,
            )
            .first()
        )
        if not setting:
            return None
        return cls._auto_advance_setting_if_due(setting, now=now)

    @classmethod
    def set_active_grading_period(
        cls,
        *,
        tenant_id: int,
        campus,
        term: Term,
        period: TenantTermGradingPeriod | None,
        actor=None,
        remarks: str | None = None,
        auto_advanced_from_deadline: bool = False,
    ):
        if period and period.term_id != term.id:
            raise ValueError("Selected grading period does not belong to the selected term.")
        if period is None:
            ActiveGradingPeriodSetting.objects.filter(
                tenant_id=tenant_id,
                campus_id=campus.id,
                term_id=term.id,
            ).delete()
            return None
        setting, _created = ActiveGradingPeriodSetting.objects.update_or_create(
            tenant_id=tenant_id,
            campus=campus,
            term=term,
            defaults={
                "period": period,
                "set_by_user": actor,
                "remarks": (remarks or "").strip() or None,
                "auto_advanced_from_deadline": auto_advanced_from_deadline,
                "is_active": True,
            },
        )
        return setting

    @classmethod
    def template_period_matches_active_period(cls, *, template_period, active_period_setting: ActiveGradingPeriodSetting | None):
        if not active_period_setting or not active_period_setting.period_id or not template_period:
            return False
        active_key = cls.normalize_period_key(active_period_setting.period.code or active_period_setting.period.name)
        template_key = cls.normalize_period_key(
            getattr(template_period, "code", None) or getattr(template_period, "name", None)
        )
        return bool(active_key and template_key and active_key == template_key)

    @classmethod
    def faculty_period_governance_state(
        cls,
        *,
        tenant_id: int,
        campus_id: int | None,
        term_id: int | None,
        template_period,
        active_period_setting: ActiveGradingPeriodSetting | None = None,
        submission_status: str | None = None,
        is_correction_active: bool = False,
        now=None,
    ):
        state = {
            "has_active_period_setting": False,
            "matched_term_period": None,
            "is_active_period": False,
            "is_closed_by_active_period": False,
            "is_reopened_override": False,
            "is_future_period": False,
            "is_past_period": False,
            "message": "",
        }
        if not template_period or not tenant_id or not campus_id or not term_id:
            return state

        active_setting = active_period_setting or cls.resolve_active_grading_period(
            tenant_id=tenant_id,
            campus_id=campus_id,
            term_id=term_id,
            now=now,
        )
        if not active_setting or not active_setting.period_id:
            return state

        state["has_active_period_setting"] = True
        matched_term_period = cls.resolve_term_period_for_template_period(
            tenant_id=tenant_id,
            term_id=term_id,
            template_period=template_period,
        )
        state["matched_term_period"] = matched_term_period
        if not matched_term_period:
            return state

        state["is_active_period"] = matched_term_period.id == active_setting.period_id
        state["is_reopened_override"] = bool(
            is_correction_active or submission_status == GradeSubmission.Status.REOPENED
        )
        if state["is_active_period"] or state["is_reopened_override"]:
            return state

        if matched_term_period.sequence_no > active_setting.period.sequence_no:
            state["is_future_period"] = True
            state["message"] = (
                f"This period is closed until {matched_term_period.name} becomes the active grading period."
            )
        else:
            state["is_past_period"] = True
            state["message"] = (
                "This earlier period is closed under the active grading period policy. "
                "Use reopen or correction if changes are still required."
            )
        state["is_closed_by_active_period"] = True
        return state


class FacultyAssignmentWorkflowService:
    REMINDER_REFERENCE_TYPE = "FACULTY_ASSIGNMENT_RESPONSE_REMINDER"

    @classmethod
    def reminders_enabled(cls, *, tenant_id: int | None):
        return FeatureSettingsService.is_faculty_assignment_reminders_enabled(
            tenant_id=tenant_id,
            default=True,
        )

    @classmethod
    def auto_expire_enabled(cls, *, tenant_id: int | None):
        return FeatureSettingsService.is_faculty_assignment_auto_expire_enabled(
            tenant_id=tenant_id,
            default=True,
        )

    @classmethod
    def response_window_days(cls, *, tenant_id: int | None):
        return FeatureSettingsService.get_faculty_assignment_response_window_days(
            tenant_id=tenant_id,
            default=3,
        )

    @classmethod
    def first_reminder_after_days(cls, *, tenant_id: int | None):
        return FeatureSettingsService.get_faculty_assignment_first_reminder_days(
            tenant_id=tenant_id,
            default=1,
        )

    @classmethod
    def repeat_reminder_days(cls, *, tenant_id: int | None):
        return FeatureSettingsService.get_faculty_assignment_repeat_reminder_days(
            tenant_id=tenant_id,
            default=1,
        )

    @classmethod
    def calculate_due_at(cls, *, tenant_id: int | None, base_at=None):
        anchor = base_at or timezone.now()
        return anchor + timedelta(days=cls.response_window_days(tenant_id=tenant_id))

    @classmethod
    def reset_response_window(cls, assignment: FacultyAssignment, *, note: str | None = None, assigned_at=None):
        if note is not None:
            assignment.assignment_note = note
        assignment.accepted_at = None
        assignment.accepted_by = None
        assignment.response_status = FacultyAssignment.ResponseStatus.PENDING
        assignment.faculty_response_note = None
        assignment.responded_at = None
        assignment.response_due_at = cls.calculate_due_at(
            tenant_id=assignment.tenant_id or assignment.offering.tenant_id,
            base_at=assigned_at or timezone.now(),
        )
        assignment.last_reminded_at = None
        assignment.reminder_count = 0
        return assignment

    @classmethod
    def clear_response_window(cls, assignment: FacultyAssignment):
        assignment.response_due_at = None
        assignment.last_reminded_at = None
        assignment.reminder_count = 0
        return assignment

    @classmethod
    def expire_overdue_assignments(cls, *, now=None, tenant_id: int | None = None, dry_run: bool = False):
        now = now or timezone.now()
        queryset = FacultyAssignment.objects.filter(
            is_active=True,
            offering__is_active=True,
            response_status=FacultyAssignment.ResponseStatus.PENDING,
            response_due_at__isnull=False,
            response_due_at__lt=now,
        )
        if tenant_id is not None:
            queryset = queryset.filter(tenant_id=tenant_id)

        expired_count = 0
        for assignment in queryset.select_related("offering"):
            if not cls.auto_expire_enabled(tenant_id=assignment.tenant_id or assignment.offering.tenant_id):
                continue
            expired_count += 1
            if dry_run:
                continue
            assignment.response_status = FacultyAssignment.ResponseStatus.EXPIRED
            assignment.responded_at = now
            assignment.response_due_at = None
            assignment.save(update_fields=["response_status", "responded_at", "response_due_at", "updated_at"])
        return expired_count

    @classmethod
    def queue_pending_assignment_reminders(cls, *, now=None, tenant_id: int | None = None, dry_run: bool = False):
        now = now or timezone.now()
        queryset = FacultyAssignment.objects.filter(
            is_active=True,
            offering__is_active=True,
            faculty_user__is_active=True,
            response_status=FacultyAssignment.ResponseStatus.PENDING,
            response_due_at__isnull=False,
            response_due_at__gt=now,
        ).select_related("tenant", "campus", "faculty_user", "offering", "offering__course", "offering__section")
        if tenant_id is not None:
            queryset = queryset.filter(tenant_id=tenant_id)

        created = 0
        for assignment in queryset:
            scoped_tenant_id = assignment.tenant_id or assignment.offering.tenant_id
            if not cls.reminders_enabled(tenant_id=scoped_tenant_id):
                continue

            first_days = cls.first_reminder_after_days(tenant_id=scoped_tenant_id)
            repeat_days = cls.repeat_reminder_days(tenant_id=scoped_tenant_id)

            if assignment.last_reminded_at:
                next_reminder_at = assignment.last_reminded_at + timedelta(days=repeat_days)
            else:
                next_reminder_at = assignment.assigned_at + timedelta(days=first_days)

            if next_reminder_at > now:
                continue

            if dry_run:
                created += 1
                continue

            reference_id = f"{assignment.id}:{assignment.reminder_count + 1}"
            notification, was_created = NotificationQueue.objects.get_or_create(
                recipient_user=assignment.faculty_user,
                channel=NotificationQueue.Channel.EMAIL,
                reference_type=cls.REMINDER_REFERENCE_TYPE,
                reference_id=reference_id,
                scheduled_at=now,
                defaults={
                    "tenant_id": scoped_tenant_id,
                    "campus_id": assignment.campus_id or assignment.offering.campus_id,
                    "subject": f"Faculty assignment reminder: {assignment.offering.course.code} / {assignment.offering.section.code}",
                    "body": (
                        f"Please review and acknowledge your assignment for "
                        f"{assignment.offering.course.title} ({assignment.offering.course.code}) / "
                        f"{assignment.offering.section.code}. Response due: "
                        f"{assignment.response_due_at:%Y-%m-%d %H:%M}."
                    ),
                    "status": NotificationQueue.Status.PENDING,
                    "metadata_json": {
                        "assignment_id": assignment.id,
                        "offering_id": assignment.offering_id,
                        "response_due_at": assignment.response_due_at.isoformat() if assignment.response_due_at else None,
                        "response_status": assignment.response_status,
                    },
                },
            )
            if was_created:
                created += 1
                assignment.last_reminded_at = now
                assignment.reminder_count = assignment.reminder_count + 1
                assignment.save(update_fields=["last_reminded_at", "reminder_count", "updated_at"])
            elif notification.status == NotificationQueue.Status.CANCELLED:
                notification.status = NotificationQueue.Status.PENDING
                notification.scheduled_at = now
                notification.save(update_fields=["status", "scheduled_at", "updated_at"])
        return created


def _assignment_snapshot(instance):
    payload = model_to_dict(instance, fields=[field.name for field in instance._meta.fields])
    return json.loads(json.dumps(payload, cls=DjangoJSONEncoder))


class FacultyAssignmentSafetyService:
    KEEP_OLD_ASSIGNMENT_TYPES = {
        FacultyAssignmentReplacementLog.ReplacementType.TEMPORARY,
        FacultyAssignmentReplacementLog.ReplacementType.SECONDARY,
    }

    @classmethod
    def get_assignment_impact_summary(cls, assignment: FacultyAssignment) -> dict:
        offering = assignment.offering
        relevant_locks = Q(course_offering=offering) | Q(
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            course_offering__isnull=True,
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            academic_year_id=offering.academic_year_id,
            term_id=offering.term_id,
        )
        return {
            "activities": GradeActivity.objects.filter(offering=offering, is_active=True).count(),
            "scores": StudentActivityScore.objects.filter(activity__offering=offering, is_active=True).count(),
            "submissions": GradeSubmission.objects.filter(offering=offering).count(),
            "period_grades": StudentPeriodGrade.objects.filter(offering=offering).count(),
            "final_grades": StudentFinalGrade.objects.filter(offering=offering).count(),
            "correction_requests": GradeCorrectionRequest.objects.filter(offering=offering).count(),
            "reopen_requests": GradeSubmissionReopenRequest.objects.filter(offering=offering).count(),
            "grading_locks": GradingPeriodLock.objects.filter(relevant_locks, is_active=True, is_locked=True).count(),
        }

    @classmethod
    def assignment_has_academic_dependencies(cls, assignment: FacultyAssignment) -> bool:
        return any(cls.get_assignment_impact_summary(assignment).values())

    @classmethod
    def validate_direct_assignment_change(cls, *, assignment: FacultyAssignment, new_offering_id, new_faculty_user_id):
        if not assignment.pk:
            return
        if int(new_offering_id) == assignment.offering_id and int(new_faculty_user_id) == assignment.faculty_user_id:
            return
        if cls.assignment_has_academic_dependencies(assignment):
            raise ValueError(
                "This faculty assignment is already in use. Use Replace Faculty so existing activities, scores, "
                "submissions, locks, and audit history remain clear."
            )

    @classmethod
    def generate_batch_reference(cls) -> str:
        return f"FAR-{timezone.now():%Y%m%d%H%M%S%f}"[:40]

    @classmethod
    @transaction.atomic
    def process_replacement(
        cls,
        *,
        assignments,
        replacement_faculty,
        replacement_type,
        reason_category,
        remarks,
        processed_by_user,
        request=None,
    ):
        batch_reference = cls.generate_batch_reference()
        keep_old = replacement_type in cls.KEEP_OLD_ASSIGNMENT_TYPES
        logs = []
        for assignment in assignments:
            current = (
                FacultyAssignment.objects.select_for_update()
                .select_related("offering", "offering__course", "offering__section", "faculty_user")
                .get(id=assignment.id)
            )
            old_before = _assignment_snapshot(current)

            if keep_old:
                old_after = _assignment_snapshot(current)
            else:
                current.is_active = False
                current.is_primary = False
                current.save(update_fields=["is_active", "is_primary", "updated_at"])
                old_after = _assignment_snapshot(current)
                AuditService.log_event(
                    action="UPDATE",
                    portal="ADMIN",
                    entity_type="FacultyAssignment",
                    entity_id=current.id,
                    actor=processed_by_user,
                    before_data=old_before,
                    after_data=old_after,
                    metadata={
                        "event": "faculty_replacement_old_assignment_deactivated",
                        "batch_reference": batch_reference,
                    },
                    request=request,
                )

            new_assignment = (
                FacultyAssignment.objects.select_for_update()
                .filter(offering=current.offering, faculty_user=replacement_faculty)
                .first()
            )
            new_before = _assignment_snapshot(new_assignment) if new_assignment else None
            if not new_assignment:
                new_assignment = FacultyAssignment(
                    tenant_id=current.offering.tenant_id,
                    campus_id=current.offering.campus_id,
                    offering=current.offering,
                    faculty_user=replacement_faculty,
                )
            new_assignment.is_active = True
            new_assignment.is_primary = not keep_old
            FacultyAssignmentWorkflowService.reset_response_window(new_assignment, note=remarks)
            new_assignment.save()

            if new_assignment.is_primary:
                other_primary_rows = (
                    FacultyAssignment.objects.select_for_update()
                    .filter(offering=current.offering, is_active=True, is_primary=True)
                    .exclude(id=new_assignment.id)
                )
                for other in other_primary_rows:
                    other_before = _assignment_snapshot(other)
                    other.is_primary = False
                    other.save(update_fields=["is_primary", "updated_at"])
                    AuditService.log_event(
                        action="UPDATE",
                        portal="ADMIN",
                        entity_type="FacultyAssignment",
                        entity_id=other.id,
                        actor=processed_by_user,
                        before_data=other_before,
                        after_data=_assignment_snapshot(other),
                        metadata={
                            "event": "faculty_replacement_primary_conflict_resolved",
                            "batch_reference": batch_reference,
                        },
                        request=request,
                    )

            AuditService.log_event(
                action="CREATE" if new_before is None else "UPDATE",
                portal="ADMIN",
                entity_type="FacultyAssignment",
                entity_id=new_assignment.id,
                actor=processed_by_user,
                before_data=new_before,
                after_data=_assignment_snapshot(new_assignment),
                metadata={"event": "faculty_replacement_new_assignment", "batch_reference": batch_reference},
                request=request,
            )

            log = FacultyAssignmentReplacementLog.objects.create(
                batch_reference=batch_reference,
                tenant_id=current.offering.tenant_id,
                campus_id=current.offering.campus_id,
                offering=current.offering,
                source_faculty=current.faculty_user,
                replacement_faculty=replacement_faculty,
                old_assignment=current,
                new_assignment=new_assignment,
                replacement_type=replacement_type,
                reason_category=reason_category,
                remarks=remarks,
                processed_by_user=processed_by_user,
                processed_at=timezone.now(),
                old_assignment_before_json=old_before,
                old_assignment_after_json=old_after,
                new_assignment_before_json=new_before,
                new_assignment_after_json=_assignment_snapshot(new_assignment),
                impact_snapshot_json=cls.get_assignment_impact_summary(current),
            )
            AuditService.log_event(
                action="CREATE",
                portal="ADMIN",
                entity_type="FacultyAssignmentReplacementLog",
                entity_id=log.id,
                actor=processed_by_user,
                after_data={
                    "batch_reference": batch_reference,
                    "source_faculty_id": current.faculty_user_id,
                    "replacement_faculty_id": replacement_faculty.id,
                    "offering_id": current.offering_id,
                    "old_assignment_id": current.id,
                    "new_assignment_id": new_assignment.id,
                    "replacement_type": replacement_type,
                    "reason_category": reason_category,
                },
                metadata={"event": "faculty_replacement_processed", "batch_reference": batch_reference},
                request=request,
            )
            logs.append(log)
        return logs
