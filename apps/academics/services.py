from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.academics.models import AcademicYear, FacultyAssignment, Term
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.notifications.models import NotificationQueue


class AcademicGovernanceService:
    ACTIVE_AY_KEY = "ACTIVE_ACADEMIC_YEAR_CODE"
    ACTIVE_TERM_KEY = "ACTIVE_TERM_CODE"

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
