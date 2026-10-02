from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService

from .models import CoverageReconciliation, MeetingReconciliation, OfferingAttendanceSourceChange, ScheduleVersion, TeachingMeeting
from .permissions import RECONCILE_PERMISSION, require_attendance_permission
from .services import CoverageService
from .schedule_parsing import parse_schedule_text


class AcademicCoverageIntegrationService:
    """Bridge academic mutations without treating acceptance timestamps as teaching coverage."""

    @classmethod
    def module_enabled(cls, *, tenant_id):
        return FeatureSettingsService.is_faculty_attendance_enabled(tenant_id=tenant_id)

    @classmethod
    @transaction.atomic
    def record_assignment_event(
        cls,
        *,
        actor,
        offering,
        event_type,
        source_reference,
        reason,
        source_assignment=None,
        prior_faculty=None,
        proposed_faculty=None,
        effective_at=None,
        apply_when_authorized=False,
    ):
        if not cls.module_enabled(tenant_id=offering.tenant_id):
            return None
        offering = CourseOffering.objects.select_for_update().select_related(
            "tenant", "campus", "department"
        ).get(pk=offering.pk)
        reconciliation, created = CoverageReconciliation.objects.get_or_create(
            source_reference=source_reference,
            defaults={
                "tenant": offering.tenant,
                "campus": offering.campus,
                "department": offering.department,
                "offering": offering,
                "source_assignment": source_assignment,
                "event_type": event_type,
                "prior_faculty": prior_faculty,
                "proposed_faculty": proposed_faculty,
                "effective_at": effective_at,
                "reason": (reason or "").strip(),
                "created_by": actor,
            },
        )
        if not created:
            return reconciliation
        reconciliation.full_clean()
        reconciliation.save()
        AuditService.log_event(
            action="FACULTY_ATTENDANCE_COVERAGE_RECONCILIATION_CREATED",
            portal="ADMIN",
            entity_type="CoverageReconciliation",
            entity_id=reconciliation.pk,
            actor=actor,
            tenant=offering.tenant,
            campus=offering.campus,
            after_data={
                "event_type": event_type,
                "offering_id": offering.pk,
                "source_assignment_id": source_assignment.pk if source_assignment else None,
                "prior_faculty_id": prior_faculty.pk if prior_faculty else None,
                "proposed_faculty_id": proposed_faculty.pk if proposed_faculty else None,
                "effective_at": effective_at,
                "status": reconciliation.status,
            },
        )
        if apply_when_authorized and effective_at is not None:
            try:
                return cls.resolve(
                    actor=actor,
                    reconciliation=reconciliation,
                    effective_at=effective_at,
                    reason=reason,
                )
            except PermissionDenied:
                return reconciliation
        return reconciliation

    @classmethod
    @transaction.atomic
    def resolve(cls, *, actor, reconciliation, effective_at, reason):
        reconciliation = CoverageReconciliation.objects.select_for_update().select_related(
            "offering", "proposed_faculty", "source_assignment"
        ).get(pk=reconciliation.pk)
        require_attendance_permission(
            user=actor,
            permission_code=RECONCILE_PERMISSION,
            tenant_id=reconciliation.tenant_id,
            campus_id=reconciliation.campus_id,
            department_id=reconciliation.department_id,
        )
        if reconciliation.status == CoverageReconciliation.Status.RESOLVED:
            if reconciliation.effective_at == effective_at and reconciliation.resolution_reason == (reason or "").strip():
                return reconciliation
            raise ValidationError("Coverage reconciliation was already resolved with another outcome.")
        reason = (reason or "").strip()
        if reconciliation.proposed_faculty_id:
            CoverageService.create(
                actor=actor,
                offering=reconciliation.offering,
                faculty_user=reconciliation.proposed_faculty,
                source_assignment=reconciliation.source_assignment,
                effective_from=effective_at,
                reason=reason.strip(),
                supersede_current=True,
            )
        elif reconciliation.event_type == CoverageReconciliation.EventType.UNASSIGNMENT:
            CoverageService.close(
                actor=actor,
                offering=reconciliation.offering,
                effective_at=effective_at,
                reason=reason.strip(),
            )
        else:
            raise ValidationError("Select the faculty coverage outcome before resolving this item.")
        reconciliation.effective_at = effective_at
        reconciliation.status = CoverageReconciliation.Status.RESOLVED
        reconciliation.resolved_by = actor
        reconciliation.resolved_at = timezone.now()
        reconciliation.resolution_reason = reason.strip()
        reconciliation.full_clean()
        reconciliation.save(
            update_fields=[
                "effective_at",
                "status",
                "resolved_by",
                "resolved_at",
                "resolution_reason",
                "updated_at",
            ]
        )
        AuditService.log_event(
            action="FACULTY_ATTENDANCE_COVERAGE_RECONCILIATION_RESOLVED",
            portal="ADMIN",
            entity_type="CoverageReconciliation",
            entity_id=reconciliation.pk,
            actor=actor,
            tenant=reconciliation.tenant_id,
            campus=reconciliation.campus_id,
            after_data={
                "status": reconciliation.status,
                "effective_at": effective_at,
                "proposed_faculty_id": reconciliation.proposed_faculty_id,
            },
        )
        return reconciliation


def record_assignment_setup_required(*, actor, assignment, event_type, reason):
    return AcademicCoverageIntegrationService.record_assignment_event(
        actor=actor,
        offering=assignment.offering,
        event_type=event_type,
        source_reference=f"assignment:{assignment.pk}:coverage-setup",
        reason=reason,
        source_assignment=assignment,
        proposed_faculty=assignment.faculty_user,
    )


class AcademicOfferingSourceIntegrationService:
    """Preserve Course Offering schedule/room changes as attendance evidence, never as a rewrite."""

    @classmethod
    @transaction.atomic
    def record_change(
        cls,
        *,
        actor,
        offering,
        old_schedule_text,
        old_room,
        effective_from=None,
        reason="",
    ):
        if not FeatureSettingsService.is_faculty_attendance_enabled(tenant_id=offering.tenant_id):
            return None
        offering = CourseOffering.objects.select_for_update().select_related("tenant", "campus", "department").get(
            pk=offering.pk
        )
        old_schedule_text = old_schedule_text or ""
        old_room = old_room or ""
        new_schedule_text = offering.schedule_text or ""
        new_room = offering.room or ""
        if old_schedule_text == new_schedule_text and old_room == new_room:
            return None
        reference = f"course-offering:{offering.pk}:{offering.updated_at.isoformat()}"
        change = OfferingAttendanceSourceChange(
            tenant=offering.tenant,
            campus=offering.campus,
            department=offering.department,
            offering=offering,
            old_schedule_text=old_schedule_text,
            new_schedule_text=new_schedule_text,
            old_room=old_room,
            new_room=new_room,
            effective_from=effective_from,
            reason=(reason or "").strip(),
            source_reference=reference,
            created_by=actor,
        )
        change.full_clean()
        change.save()
        meetings = TeachingMeeting.objects.filter(offering_links__offering=offering).distinct()
        if effective_from:
            meetings = meetings.filter(meeting_date__gte=effective_from)
        for meeting in meetings:
            MeetingReconciliation.objects.get_or_create(
                meeting=meeting,
                source_type=MeetingReconciliation.SourceType.SCHEDULE,
                source_reference=reference,
                defaults={
                    "reason": change.reason,
                    "before_snapshot": {
                        "schedule_text": old_schedule_text,
                        "room": old_room,
                        "meeting_schedule": meeting.schedule_snapshot,
                        "meeting_location": meeting.location_snapshot,
                    },
                    "proposed_snapshot": {
                        "schedule_text": new_schedule_text,
                        "room": new_room,
                        "effective_from": effective_from.isoformat() if effective_from else None,
                        "source_change_id": change.pk,
                    },
                    "detected_by": actor,
                },
            )
        AuditService.log_event(
            action="FACULTY_ATTENDANCE_COURSE_OFFERING_SOURCE_CHANGED",
            portal="ADMIN",
            entity_type="OfferingAttendanceSourceChange",
            entity_id=change.pk,
            actor=actor,
            tenant=offering.tenant,
            campus=offering.campus,
            after_data={
                "offering_id": offering.pk,
                "effective_from": effective_from,
                "old_schedule_text": old_schedule_text,
                "new_schedule_text": new_schedule_text,
                "old_room": old_room,
                "new_room": new_room,
            },
        )
        return change

    @classmethod
    @transaction.atomic
    def resolve(cls, *, actor, source_change, effective_from, reason):
        source_change = OfferingAttendanceSourceChange.objects.select_for_update().select_related(
            "offering", "tenant", "campus", "department"
        ).get(pk=source_change.pk)
        require_attendance_permission(
            user=actor,
            permission_code=RECONCILE_PERMISSION,
            tenant_id=source_change.tenant_id,
            campus_id=source_change.campus_id,
            department_id=source_change.department_id,
        )
        reason = (reason or "").strip()
        if effective_from is None:
            raise ValidationError("Course Offering source reconciliation requires an explicit effective date.")
        if not parse_schedule_text(source_change.old_schedule_text).confirmed:
            raise ValidationError("The prior Course Offering schedule is ambiguous; preserve it through an explicit attendance correction before resolving.")
        if not parse_schedule_text(source_change.new_schedule_text).confirmed:
            raise ValidationError("Correct the current Course Offering schedule before resolving this attendance source change.")
        pending_meetings = MeetingReconciliation.objects.filter(
            source_reference=source_change.source_reference,
            status=MeetingReconciliation.Status.PENDING,
        )
        if pending_meetings.exists():
            raise ValidationError("Resolve every affected dated meeting before closing the Course Offering source review.")
        if source_change.status == OfferingAttendanceSourceChange.Status.RESOLVED:
            if source_change.effective_from == effective_from and source_change.resolution_reason == reason.strip():
                return source_change
            raise ValidationError("Course Offering source change was already resolved with another boundary or reason.")
        source_change.effective_from = effective_from
        source_change.status = OfferingAttendanceSourceChange.Status.RESOLVED
        source_change.resolved_by = actor
        source_change.resolved_at = timezone.now()
        source_change.resolution_reason = reason.strip()
        source_change.full_clean()
        source_change.save(
            update_fields=[
                "effective_from",
                "status",
                "resolved_by",
                "resolved_at",
                "resolution_reason",
                "updated_at",
            ]
        )
        old_source_versions = ScheduleVersion.objects.select_for_update().filter(
            offering=source_change.offering,
            source_kind="COURSE_OFFERING",
            original_text=source_change.old_schedule_text,
            effective_from__lt=effective_from,
        ).filter(Q(effective_until__isnull=True) | Q(effective_until__gte=effective_from))
        for version in old_source_versions:
            version.effective_until = effective_from - timedelta(days=1)
            version.full_clean()
            version.save(update_fields=["effective_until", "updated_at"])
        AuditService.log_event(
            action="FACULTY_ATTENDANCE_COURSE_OFFERING_SOURCE_RESOLVED",
            portal="ADMIN",
            entity_type="OfferingAttendanceSourceChange",
            entity_id=source_change.pk,
            actor=actor,
            tenant=source_change.tenant,
            campus=source_change.campus,
            after_data={"effective_from": effective_from, "status": source_change.status},
        )
        return source_change
