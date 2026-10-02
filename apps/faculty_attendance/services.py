from __future__ import annotations

from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.core.services.audit import AuditService
from apps.rbac.models import UserRole

from .models import (
    FacultyCoverage,
    MeetingOffering,
    MeetingReconciliation,
    MeetingSubstitution,
    ScheduleSlot,
    ScheduleVersion,
    TeachingMeeting,
)
from .permissions import (
    MANAGE_COVERAGE_PERMISSION,
    MANAGE_MEETINGS_PERMISSION,
    MANAGE_SCHEDULES_PERMISSION,
    MANAGE_SUBSTITUTIONS_PERMISSION,
    RECONCILE_PERMISSION,
    require_attendance_permission,
)
from .schedule_parsing import ParsedScheduleSlot, parse_schedule_text


def _audit(*, action, entity, actor, before=None, after=None, metadata=None):
    AuditService.log_event(
        action=action,
        portal="ADMIN",
        entity_type=entity.__class__.__name__,
        entity_id=entity.pk,
        actor=actor,
        tenant=entity.tenant_id if hasattr(entity, "tenant_id") else entity.meeting.tenant_id,
        campus=entity.campus_id if hasattr(entity, "campus_id") else entity.meeting.campus_id,
        before_data=before,
        after_data=after,
        metadata=metadata,
    )


def _require(actor, permission, scoped):
    require_attendance_permission(
        user=actor,
        permission_code=permission,
        tenant_id=scoped.tenant_id,
        campus_id=scoped.campus_id,
        department_id=scoped.department_id,
    )


def _user_has_scope_evidence(*, user, tenant_id, campus_id, department_id, offering_ids=()):
    default_matches = (
        user.default_tenant_id == tenant_id
        and user.default_campus_id == campus_id
        and user.default_department_id in (None, department_id)
    )
    if default_matches:
        return True
    if UserRole.objects.filter(
        user=user,
        is_active=True,
        role__is_active=True,
    ).filter(Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
             Q(campus_id=campus_id) | Q(campus_id__isnull=True),
             Q(department_id=department_id) | Q(department_id__isnull=True)).exists():
        return True
    if offering_ids and FacultyAssignment.objects.filter(
        faculty_user=user,
        offering__tenant_id=tenant_id,
        offering__campus_id=campus_id,
        offering__department_id=department_id,
        offering_id__in=offering_ids,
    ).filter(Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
             Q(campus_id=campus_id) | Q(campus_id__isnull=True)).exists():
        return True
    return False


def _schedule_overlap_query(effective_from, effective_until):
    query = Q(effective_until__isnull=True) | Q(effective_until__gte=effective_from)
    if effective_until:
        query &= Q(effective_from__lte=effective_until)
    return query


def _coverage_overlap_query(effective_from, effective_until):
    query = Q(effective_until__isnull=True) | Q(effective_until__gt=effective_from)
    if effective_until:
        query &= Q(effective_from__lt=effective_until)
    return query


def _meeting_snapshot(meeting):
    return {
        "meeting_id": meeting.pk,
        "schedule_snapshot": meeting.schedule_snapshot,
        "faculty_user_id": meeting.faculty_user_id,
        "faculty_snapshot": meeting.faculty_snapshot,
        "location_snapshot": meeting.location_snapshot,
        "sections_snapshot": meeting.sections_snapshot,
        "unresolved_coverage": meeting.unresolved_coverage,
    }


def _create_reconciliations(*, meetings, source_type, source_reference, reason, proposed_snapshot, actor):
    created = []
    for meeting in meetings:
        item, was_created = MeetingReconciliation.objects.get_or_create(
            meeting=meeting,
            source_type=source_type,
            source_reference=source_reference,
            defaults={
                "reason": reason,
                "before_snapshot": _meeting_snapshot(meeting),
                "proposed_snapshot": proposed_snapshot,
                "detected_by": actor,
            },
        )
        if was_created:
            created.append(item)
    return created


class ScheduleService:
    @classmethod
    @transaction.atomic
    def create_version(
        cls,
        *,
        actor,
        offering,
        effective_from,
        effective_until=None,
        corrected_slots: list[dict] | None = None,
        correction_reason="",
        supersede_current=False,
    ):
        offering = CourseOffering.objects.select_for_update().select_related(
            "tenant", "campus", "department"
        ).get(pk=offering.pk)
        _require(actor, MANAGE_SCHEDULES_PERMISSION, offering)
        if effective_until and effective_until < effective_from:
            raise ValidationError("Schedule effective-until date cannot precede effective-from date.")
        overlaps = list(
            ScheduleVersion.objects.select_for_update()
            .filter(offering=offering)
            .filter(_schedule_overlap_query(effective_from, effective_until))
            .order_by("effective_from")
        )
        if overlaps:
            can_close_current = (
                supersede_current
                and corrected_slots is not None
                and len(overlaps) == 1
                and overlaps[0].effective_from < effective_from
                and (overlaps[0].effective_until is None or overlaps[0].effective_until >= effective_from)
            )
            if not can_close_current:
                raise ValidationError("Schedule version effective dates overlap an existing version.")
            previous = overlaps[0]
            previous.effective_until = effective_from - timedelta(days=1)
            previous.full_clean()
            previous.save(update_fields=["effective_until", "updated_at"])

        if corrected_slots is None:
            parsed = parse_schedule_text(offering.schedule_text)
            status = (
                ScheduleVersion.InterpretationStatus.CONFIRMED
                if parsed.confirmed
                else ScheduleVersion.InterpretationStatus.CORRECTION_REQUIRED
            )
            slot_values = [
                {"weekday": item.weekday, "start_time": item.start_time, "end_time": item.end_time}
                for item in parsed.slots
            ]
            reason = "" if parsed.confirmed else parsed.reason
        else:
            if not corrected_slots:
                raise ValidationError("An explicit schedule correction requires at least one structured slot.")
            slot_values = corrected_slots
            status = ScheduleVersion.InterpretationStatus.CONFIRMED
            reason = (correction_reason or "").strip()

        version_number = (
            ScheduleVersion.objects.filter(offering=offering).aggregate(value=Max("version_number"))["value"] or 0
        ) + 1
        version = ScheduleVersion(
            tenant=offering.tenant,
            campus=offering.campus,
            department=offering.department,
            offering=offering,
            version_number=version_number,
            original_text=offering.schedule_text or "",
            interpretation_status=status,
            effective_from=effective_from,
            effective_until=effective_until,
            correction_reason=reason,
            created_by=actor,
        )
        version.full_clean()
        version.save()

        for sequence, values in enumerate(slot_values, start=1):
            try:
                slot = ScheduleSlot(
                    schedule_version=version,
                    sequence=sequence,
                    weekday=values["weekday"],
                    start_time=values["start_time"],
                    end_time=values["end_time"],
                    room_text=values.get("room_text", offering.room or ""),
                    building=values.get("building", ""),
                    floor=values.get("floor", ""),
                    room=values.get("room", ""),
                )
            except KeyError as exc:
                raise ValidationError(f"Corrected schedule slot is missing {exc.args[0]}.") from exc
            slot.full_clean()
            slot.save()

        impacted = TeachingMeeting.objects.filter(
            offering_links__offering=offering,
            meeting_date__gte=effective_from,
        ).distinct()
        if effective_until:
            impacted = impacted.filter(meeting_date__lte=effective_until)
        reconciliations = _create_reconciliations(
            meetings=impacted,
            source_type=MeetingReconciliation.SourceType.SCHEDULE,
            source_reference=f"schedule-version:{version.pk}",
            reason=(correction_reason or "").strip(),
            proposed_snapshot={"schedule_version_id": version.pk, "version_number": version.version_number},
            actor=actor,
        )
        _audit(
            action="FACULTY_ATTENDANCE_SCHEDULE_VERSION_CREATED",
            entity=version,
            actor=actor,
            after={
                "version_number": version.version_number,
                "status": version.interpretation_status,
                "effective_from": effective_from,
                "effective_until": effective_until,
                "slot_count": len(slot_values),
            },
            metadata={"reconciliation_count": len(reconciliations)},
        )
        return version


class CoverageService:
    @classmethod
    @transaction.atomic
    def create(
        cls,
        *,
        actor,
        offering,
        faculty_user,
        effective_from,
        effective_until=None,
        reason,
        source_assignment=None,
        supersede_current=False,
    ):
        offering = CourseOffering.objects.select_for_update().select_related(
            "tenant", "campus", "department"
        ).get(pk=offering.pk)
        _require(actor, MANAGE_COVERAGE_PERMISSION, offering)
        reason = (reason or "").strip()
        if not _user_has_scope_evidence(
            user=faculty_user,
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            department_id=offering.department_id,
            offering_ids=(offering.pk,),
        ):
            raise ValidationError("Covered faculty has no evidence in the offering's authorized scope.")
        if effective_until and effective_until <= effective_from:
            raise ValidationError("Coverage uses a half-open interval and must end after it starts.")
        overlaps = list(
            FacultyCoverage.objects.select_for_update()
            .filter(offering=offering)
            .filter(_coverage_overlap_query(effective_from, effective_until))
            .order_by("effective_from")
        )
        if overlaps:
            can_close_current = (
                supersede_current
                and len(overlaps) == 1
                and overlaps[0].effective_from < effective_from
                and (overlaps[0].effective_until is None or overlaps[0].effective_until > effective_from)
            )
            if not can_close_current:
                raise ValidationError("Permanent faculty coverage overlaps an existing interval.")
            previous = overlaps[0]
            previous.effective_until = effective_from
            previous.full_clean()
            previous.save(update_fields=["effective_until", "updated_at"])
        coverage = FacultyCoverage(
            tenant=offering.tenant,
            campus=offering.campus,
            department=offering.department,
            offering=offering,
            faculty_user=faculty_user,
            source_assignment=source_assignment,
            effective_from=effective_from,
            effective_until=effective_until,
            reason=reason.strip(),
            created_by=actor,
        )
        coverage.full_clean()
        coverage.save()

        impacted = TeachingMeeting.objects.filter(
            offering_links__offering=offering,
            starts_at__gte=effective_from,
        ).distinct()
        if effective_until:
            impacted = impacted.filter(starts_at__lt=effective_until)
        reconciliations = _create_reconciliations(
            meetings=impacted,
            source_type=MeetingReconciliation.SourceType.COVERAGE,
            source_reference=f"coverage:{coverage.pk}",
            reason=reason.strip(),
            proposed_snapshot={"coverage_id": coverage.pk, "faculty_user_id": faculty_user.pk},
            actor=actor,
        )
        _audit(
            action="FACULTY_ATTENDANCE_COVERAGE_CREATED",
            entity=coverage,
            actor=actor,
            after={
                "faculty_user_id": faculty_user.pk,
                "effective_from": effective_from,
                "effective_until": effective_until,
            },
            metadata={"reconciliation_count": len(reconciliations)},
        )
        return coverage

    @staticmethod
    def effective_for(*, offering_id, at):
        return FacultyCoverage.objects.filter(
            offering_id=offering_id,
            effective_from__lte=at,
        ).filter(Q(effective_until__isnull=True) | Q(effective_until__gt=at)).order_by("effective_from").first()

    @classmethod
    @transaction.atomic
    def close(cls, *, actor, offering, effective_at, reason):
        offering = CourseOffering.objects.select_for_update().select_related(
            "tenant", "campus", "department"
        ).get(pk=offering.pk)
        _require(actor, MANAGE_COVERAGE_PERMISSION, offering)
        reason = (reason or "").strip()
        coverage = (
            FacultyCoverage.objects.select_for_update()
            .filter(offering=offering, effective_from__lt=effective_at)
            .filter(Q(effective_until__isnull=True) | Q(effective_until__gt=effective_at))
            .order_by("-effective_from")
            .first()
        )
        if coverage is None:
            return None
        before = {"effective_until": coverage.effective_until}
        coverage.effective_until = effective_at
        coverage.full_clean()
        coverage.save(update_fields=["effective_until", "updated_at"])
        impacted = TeachingMeeting.objects.filter(
            offering_links__offering=offering,
            starts_at__gte=effective_at,
        ).distinct()
        reconciliations = _create_reconciliations(
            meetings=impacted,
            source_type=MeetingReconciliation.SourceType.COVERAGE,
            source_reference=f"coverage-close:{coverage.pk}:{effective_at.isoformat()}",
            reason=reason.strip(),
            proposed_snapshot={"coverage_id": None, "faculty_user_id": None},
            actor=actor,
        )
        _audit(
            action="FACULTY_ATTENDANCE_COVERAGE_CLOSED",
            entity=coverage,
            actor=actor,
            before=before,
            after={"effective_until": effective_at},
            metadata={"reconciliation_count": len(reconciliations)},
        )
        return coverage


class MeetingService:
    @classmethod
    @transaction.atomic
    def generate(
        cls,
        *,
        actor,
        schedule_slot,
        meeting_date,
        offerings,
        permission_code=MANAGE_MEETINGS_PERMISSION,
        occurrence_key=None,
        source_kind="MANUAL",
    ):
        slot = ScheduleSlot.objects.select_for_update().select_related(
            "schedule_version__offering__tenant",
            "schedule_version__offering__campus",
            "schedule_version__offering__department",
        ).get(pk=schedule_slot.pk)
        version = slot.schedule_version
        primary = version.offering
        _require(actor, permission_code, primary)
        if version.interpretation_status != ScheduleVersion.InterpretationStatus.CONFIRMED:
            raise ValidationError("A correction-required schedule cannot generate meetings.")
        if not (version.effective_from <= meeting_date and (not version.effective_until or meeting_date <= version.effective_until)):
            raise ValidationError("Meeting date is outside the schedule version's effective range.")
        if meeting_date.weekday() != slot.weekday:
            raise ValidationError("Meeting date does not match the confirmed schedule weekday.")

        offering_ids = {item.pk for item in offerings}
        offering_ids.add(primary.pk)
        linked = list(
            CourseOffering.objects.select_for_update()
            .select_related("course", "section")
            .filter(pk__in=offering_ids)
            .order_by("pk")
        )
        if len(linked) != len(offering_ids):
            raise ValidationError("One or more combined offerings do not exist.")
        for offering in linked:
            if (
                offering.tenant_id != primary.tenant_id
                or offering.campus_id != primary.campus_id
                or offering.department_id != primary.department_id
            ):
                raise ValidationError("Combined offerings must share tenant, campus, and department scope.")

        starts_at = timezone.make_aware(datetime.combine(meeting_date, slot.start_time), timezone.get_current_timezone())
        ends_at = timezone.make_aware(datetime.combine(meeting_date, slot.end_time), timezone.get_current_timezone())
        scheduled_minutes = int((ends_at - starts_at).total_seconds() // 60)
        coverages = [CoverageService.effective_for(offering_id=item.pk, at=starts_at) for item in linked]
        faculty_ids = {item.faculty_user_id for item in coverages if item is not None}
        resolved = len(coverages) == len(linked) and len(faculty_ids) == 1
        primary_coverage = next((item for item in coverages if item and item.offering_id == primary.pk), None)
        if resolved and primary_coverage is None:
            resolved = False

        sections_snapshot = [
            {
                "offering_id": item.pk,
                "course_code": item.course.code,
                "course_title": item.course.title,
                "section_code": item.section.code,
                "section_name": item.section.name,
            }
            for item in linked
        ]
        expected = {
            "starts_at": starts_at,
            "ends_at": ends_at,
            "scheduled_minutes": scheduled_minutes,
            "offering_ids": sorted(offering_ids),
            "coverage_id": primary_coverage.pk if resolved else None,
            "faculty_user_id": primary_coverage.faculty_user_id if resolved else None,
            "occurrence_key": occurrence_key,
            "source_kind": source_kind,
        }
        existing = TeachingMeeting.objects.filter(schedule_slot=slot, meeting_date=meeting_date).first()
        if existing:
            actual = {
                "starts_at": existing.starts_at,
                "ends_at": existing.ends_at,
                "scheduled_minutes": existing.scheduled_minutes,
                "offering_ids": sorted(existing.offering_links.values_list("offering_id", flat=True)),
                "coverage_id": existing.coverage_id,
                "faculty_user_id": existing.faculty_user_id,
                "occurrence_key": existing.occurrence_key,
                "source_kind": existing.source_kind,
            }
            if actual != expected:
                raise ValidationError("Meeting already exists with conflicting historical generation inputs.")
            return existing

        meeting = TeachingMeeting(
            tenant=primary.tenant,
            campus=primary.campus,
            department=primary.department,
            schedule_slot=slot,
            source_kind=source_kind,
            occurrence_key=occurrence_key,
            meeting_date=meeting_date,
            starts_at=starts_at,
            ends_at=ends_at,
            scheduled_minutes=scheduled_minutes,
            coverage=primary_coverage if resolved else None,
            faculty_user=primary_coverage.faculty_user if resolved else None,
            schedule_snapshot={
                "schedule_version_id": version.pk,
                "version_number": version.version_number,
                "original_text": version.original_text,
                "weekday": slot.weekday,
                "start_time": slot.start_time.isoformat(),
                "end_time": slot.end_time.isoformat(),
            },
            faculty_snapshot=(
                {
                    "coverage_id": primary_coverage.pk,
                    "faculty_user_id": primary_coverage.faculty_user_id,
                    "faculty_name": primary_coverage.faculty_user.full_name,
                }
                if resolved
                else {}
            ),
            location_snapshot={
                "room_text": slot.room_text,
                "building": slot.building,
                "floor": slot.floor,
                "room": slot.room,
            },
            sections_snapshot=sections_snapshot,
            unresolved_coverage=not resolved,
            generated_by=actor,
        )
        meeting.full_clean()
        meeting.save()
        for item in linked:
            link = MeetingOffering(
                meeting=meeting,
                offering=item,
                is_primary=item.pk == primary.pk,
                course_code_snapshot=item.course.code,
                course_title_snapshot=item.course.title,
                section_code_snapshot=item.section.code,
                section_name_snapshot=item.section.name,
            )
            link.full_clean()
            link.save()
        _audit(
            action="FACULTY_ATTENDANCE_MEETING_GENERATED",
            entity=meeting,
            actor=actor,
            after=_meeting_snapshot(meeting),
            metadata={"combined_offering_count": len(linked)},
        )
        return meeting


class SubstitutionService:
    @classmethod
    @transaction.atomic
    def assign(cls, *, actor, meeting, substitute_faculty, reason):
        meeting = TeachingMeeting.objects.select_for_update().get(pk=meeting.pk)
        _require(actor, MANAGE_SUBSTITUTIONS_PERMISSION, meeting)
        reason = (reason or "").strip()
        offering_ids = tuple(meeting.offering_links.values_list("offering_id", flat=True))
        if not _user_has_scope_evidence(
            user=substitute_faculty,
            tenant_id=meeting.tenant_id,
            campus_id=meeting.campus_id,
            department_id=meeting.department_id,
            offering_ids=offering_ids,
        ):
            raise ValidationError("Substitute faculty has no evidence in the meeting's authorized scope.")
        existing = MeetingSubstitution.objects.filter(meeting=meeting).first()
        if existing:
            if existing.substitute_faculty_id == substitute_faculty.pk and existing.reason == reason.strip():
                return existing
            raise ValidationError("Meeting already has a different substitution decision.")
        substitution = MeetingSubstitution(
            meeting=meeting,
            original_faculty=meeting.faculty_user,
            substitute_faculty=substitute_faculty,
            reason=reason.strip(),
            decided_by=actor,
            decision_snapshot={
                "original_faculty_user_id": meeting.faculty_user_id,
                "substitute_faculty_user_id": substitute_faculty.pk,
                "meeting_snapshot": _meeting_snapshot(meeting),
            },
        )
        substitution.full_clean()
        substitution.save()
        _audit(
            action="FACULTY_ATTENDANCE_SUBSTITUTION_ASSIGNED",
            entity=substitution,
            actor=actor,
            after={"meeting_id": meeting.pk, "substitute_faculty_user_id": substitute_faculty.pk},
        )
        return substitution


class ReconciliationService:
    @classmethod
    @transaction.atomic
    def resolve(cls, *, actor, reconciliation, decision, reason):
        reconciliation = MeetingReconciliation.objects.select_for_update().select_related("meeting").get(
            pk=reconciliation.pk
        )
        _require(actor, RECONCILE_PERMISSION, reconciliation.meeting)
        if decision not in MeetingReconciliation.Decision.values:
            raise ValidationError("Unknown reconciliation decision.")
        reason = (reason or "").strip()
        if reconciliation.status == MeetingReconciliation.Status.RESOLVED:
            if reconciliation.decision == decision and reconciliation.resolution_reason == reason.strip():
                return reconciliation
            raise ValidationError("Reconciliation was already resolved with another outcome.")
        before = {"status": reconciliation.status, "decision": reconciliation.decision}
        reconciliation.status = MeetingReconciliation.Status.RESOLVED
        reconciliation.decision = decision
        reconciliation.resolved_by = actor
        reconciliation.resolved_at = timezone.now()
        reconciliation.resolution_reason = reason.strip()
        reconciliation.full_clean()
        reconciliation.save(
            update_fields=["status", "decision", "resolved_by", "resolved_at", "resolution_reason", "updated_at"]
        )
        _audit(
            action="FACULTY_ATTENDANCE_RECONCILIATION_RESOLVED",
            entity=reconciliation,
            actor=actor,
            before=before,
            after={"status": reconciliation.status, "decision": reconciliation.decision},
        )
        return reconciliation
