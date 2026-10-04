from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import prefetch_related_objects
from django.utils import timezone

from apps.core.services.audit import AuditService

from .models import (
    AttendanceObservation,
    AttendanceObservationFinding,
    AttendanceResult,
    AttendanceResultRevision,
    CheckingRound,
    CheckingRoundMeeting,
    CoverageReconciliation,
    MeetingReconciliation,
    MeetingSubstitution,
    TeachingMeeting,
)
from .permissions import CORRECT_PERMISSION, ENCODE_PERMISSION, require_attendance_permission
from .notice_locking import lock_notice_campus, lock_observation_notice_scope, lock_result_notice_scope


class StaleAttendanceReview(ValidationError):
    pass


def _canonical_hash(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _meeting_manifest_snapshot(meeting):
    result = getattr(meeting, "attendance_result", None)
    return {
        "meeting_id": meeting.pk,
        "meeting_date": meeting.meeting_date.isoformat(),
        "starts_at": meeting.starts_at.isoformat(),
        "ends_at": meeting.ends_at.isoformat(),
        "scheduled_minutes": meeting.scheduled_minutes,
        "faculty_user_id": meeting.faculty_user_id,
        "unresolved_coverage": meeting.unresolved_coverage,
        "schedule_snapshot": meeting.schedule_snapshot,
        "location_snapshot": meeting.location_snapshot,
        "sections_snapshot": meeting.sections_snapshot,
        "result_revision": result.revision if result else 0,
        "result_status": result.status if result else AttendanceResult.Status.UNVERIFIED,
    }


def _pending_reconciliation_exists(meeting):
    if hasattr(meeting, "_attendance_pending_review"):
        return meeting._attendance_pending_review
    if meeting.reconciliations.filter(status=MeetingReconciliation.Status.PENDING).exists():
        return True
    offering_ids = meeting.offering_links.values_list("offering_id", flat=True)
    return CoverageReconciliation.objects.filter(
        offering_id__in=offering_ids,
        status=CoverageReconciliation.Status.PENDING,
    ).filter(effective_at__isnull=True).exists() or CoverageReconciliation.objects.filter(
        offering_id__in=offering_ids,
        status=CoverageReconciliation.Status.PENDING,
        effective_at__lte=meeting.starts_at,
    ).exists()


FACULTY_ATTRIBUTION_RESULT = "SAVED_RESULT"
FACULTY_ATTRIBUTION_SUBSTITUTION = "SUBSTITUTION"
FACULTY_ATTRIBUTION_MEETING = "MEETING"
FACULTY_ATTRIBUTION_ADOPTION = "COVERAGE_ADOPTION"
FACULTY_ATTRIBUTION_UNRESOLVED = "UNRESOLVED"
FACULTY_ATTRIBUTION_ASSIGNMENT = "ESTABLISHED_ASSIGNMENT"


def prime_meeting_readiness(meetings, *, assignment_evidence=None):
    """Load current readiness evidence once for a fresh, scoped batch of meetings.

    Writers call this only after the campus/source locks. These ephemeral flags
    are never persisted, shared between requests, or used to skip a permission.
    """
    from .college_sync import retired_meeting_ids
    meetings = list(meetings)
    ids = [m.pk for m in meetings]
    prefetch_related_objects(meetings, "faculty_user", "substitution__substitute_faculty",
                             "coverage_adoption__faculty_user", "offering_links")
    legacy = [m for m in meetings if m.unresolved_coverage and not m.faculty_snapshot]
    if legacy:
        from .assignment_attribution import AssignmentEvidence
        from django.db import connection
        prefetch_related_objects(legacy, "reconciliations")
        if assignment_evidence is None:
            prefetch_related_objects(legacy, "offering_links__offering__term")
        evidence = assignment_evidence or AssignmentEvidence(
            [l.offering for m in legacy for l in m.offering_links.all()], lock=connection.in_atomic_block)
        for meeting in legacy:
            meeting._attendance_assignment_evidence = evidence
    retired = set(retired_meeting_ids().filter(meeting_id__in=ids))
    pending = set(MeetingReconciliation.objects.filter(meeting_id__in=ids,
        status=MeetingReconciliation.Status.PENDING).values_list("meeting_id", flat=True))
    offering_ids = {link.offering_id for m in meetings for link in m.offering_links.all()}
    coverage = list(CoverageReconciliation.objects.filter(offering_id__in=offering_ids,
        status=CoverageReconciliation.Status.PENDING).values_list("offering_id", "effective_at"))
    for meeting in meetings:
        linked = {link.offering_id for link in meeting.offering_links.all()}
        meeting._attendance_retired = meeting.pk in retired
        meeting._attendance_pending_review = meeting.pk in pending or any(
            offering_id in linked and (effective is None or effective <= meeting.starts_at)
            for offering_id, effective in coverage)


def resolve_attendance_faculty(meeting, result=None):
    """Return the authoritative dated faculty and its provenance for a meeting."""
    if result is not None and result.faculty_user_id:
        return result.faculty_user, FACULTY_ATTRIBUTION_RESULT
    try:
        substitution = meeting.substitution
    except MeetingSubstitution.DoesNotExist:
        substitution = None
    if substitution is not None:
        return substitution.substitute_faculty, FACULTY_ATTRIBUTION_SUBSTITUTION
    if meeting.faculty_snapshot.get("college_academic_sync"):
        # A later authoritative assignment supersedes an old initial adoption
        # only while the meeting has no saved attendance.
        if meeting.faculty_user_id:
            return meeting.faculty_user, FACULTY_ATTRIBUTION_MEETING
        return None, FACULTY_ATTRIBUTION_UNRESOLVED
    adoption = getattr(meeting, "coverage_adoption", None)
    if adoption is not None:
        return adoption.faculty_user, FACULTY_ATTRIBUTION_ADOPTION
    if meeting.faculty_user_id:
        return meeting.faculty_user, FACULTY_ATTRIBUTION_MEETING
    from .assignment_attribution import legacy_meeting_faculty
    faculty = legacy_meeting_faculty(meeting)
    if faculty:
        return faculty, FACULTY_ATTRIBUTION_ASSIGNMENT
    return None, FACULTY_ATTRIBUTION_UNRESOLVED


def require_confirmable_meeting_faculty(meeting, *, result=None, result_revision=None):
    """Validate current dated coverage without rewriting historical meeting warnings."""
    from .college_sync import retired_meeting_ids
    if getattr(meeting, "_attendance_retired", None) is True or (
        not hasattr(meeting, "_attendance_retired") and meeting.pk in retired_meeting_ids()
    ):
        raise ValidationError("This class was rescheduled in Course Offerings. Use the updated daily list.")
    if meeting.schedule_snapshot.get("college_source_waiting"):
        raise ValidationError("Save matching schedules and rooms for all linked sections in Course Offerings before encoding this combined class.")
    faculty, source = resolve_attendance_faculty(meeting)
    if faculty is None and result is not None:
        from .assignment_attribution import legacy_meeting_faculty
        faculty = legacy_meeting_faculty(meeting, result=result, result_revision=result_revision)
        if faculty is not None:
            faculty, source = resolve_attendance_faculty(meeting, result)
    try:
        has_substitution = meeting.substitution is not None
    except MeetingSubstitution.DoesNotExist:
        has_substitution = False
    if faculty is None or (meeting.unresolved_coverage and not has_substitution
            and not hasattr(meeting, "coverage_adoption")
            and source not in (FACULTY_ATTRIBUTION_ASSIGNMENT, FACULTY_ATTRIBUTION_RESULT)):
        raise ValidationError("No reliable faculty attribution for this class date. Review the existing assignment and dated changes for the linked sections.")
    if _pending_reconciliation_exists(meeting):
        raise ValidationError("Meeting has pending coverage or historical reconciliation and cannot be confirmed.")
    return faculty


def _decimal(value, field_name):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a decimal value.") from exc
    if parsed < 0:
        raise ValidationError(f"{field_name} cannot be negative.")
    return parsed


def _normalize_findings(findings, *, allow_legacy_periods=False):
    if not findings:
        return []
    normalized = []
    seen = {}
    for raw in findings:
        finding_type = str(raw.get("finding_type") or "").strip().upper()
        segment_key = str(raw.get("segment_key") or "").strip()
        if finding_type not in AttendanceObservationFinding.FindingType.values or not segment_key:
            raise ValidationError("Each finding requires a valid type and explicit segment key.")
        key = (finding_type, segment_key)
        notice = str(raw.get("notice_status") or "").strip().upper()
        if key in seen:
            if finding_type == AttendanceObservationFinding.FindingType.ABSENCE and seen[key] != notice:
                raise ValidationError("The same absence segment cannot be both A and N.")
            raise ValidationError("Duplicate finding segment submitted.")
        seen[key] = notice
        if finding_type == AttendanceObservationFinding.FindingType.ABSENCE:
            if notice not in AttendanceObservationFinding.NoticeStatus.values:
                raise ValidationError("Absence segment requires A or N notice status.")
            missed_periods = _decimal(raw.get("missed_periods"), "missed_periods")
            missed_hours = _decimal(raw.get("missed_hours"), "missed_hours")
            if missed_periods is not None and not allow_legacy_periods:
                raise ValidationError("New absence findings require actual missed decimal hours; missed periods are legacy-only.")
            if missed_hours is None and not (allow_legacy_periods and missed_periods is not None):
                raise ValidationError("Absence segment requires actual missed decimal hours.")
            if raw.get("minutes") not in (None, ""):
                raise ValidationError("Absence uses decimal periods/hours, not minutes.")
            normalized.append(
                {
                    "finding_type": finding_type,
                    "segment_key": segment_key,
                    "notice_status": notice,
                    "missed_periods": missed_periods,
                    "missed_hours": missed_hours,
                    "minutes": None,
                }
            )
        else:
            if notice or raw.get("missed_periods") not in (None, "") or raw.get("missed_hours") not in (None, ""):
                raise ValidationError("Late and early-dismissal findings use minute input only.")
            try:
                minutes = int(raw.get("minutes"))
            except (TypeError, ValueError) as exc:
                raise ValidationError("Late and early-dismissal minutes must be an integer, including zero.") from exc
            if minutes < 0:
                raise ValidationError("Minutes cannot be negative.")
            normalized.append(
                {
                    "finding_type": finding_type,
                    "segment_key": segment_key,
                    "notice_status": "",
                    "missed_periods": None,
                    "missed_hours": None,
                    "minutes": minutes,
                }
            )
    return sorted(normalized, key=lambda item: (item["finding_type"], item["segment_key"]))


def _serialize_findings(findings):
    return [
        {
            **item,
            "missed_periods": str(item["missed_periods"]) if item["missed_periods"] is not None else None,
            "missed_hours": str(item["missed_hours"]) if item["missed_hours"] is not None else None,
        }
        for item in findings
    ]


class CheckingRoundService:
    @classmethod
    @transaction.atomic
    def create(
        cls,
        *,
        actor,
        meetings,
        checking_date,
        checking_end_date=None,
        label="",
        saved_route=None,
        academic_year=None,
        term=None,
        daily_occurrence_date=None,
    ):
        meetings = list(meetings)
        if not meetings:
            raise ValidationError("Checking round requires at least one meeting.")
        locked_rows = list(
            TeachingMeeting.objects.select_for_update()
            .select_related("tenant", "campus", "department")
            .filter(pk__in=[item.pk for item in meetings])
            .order_by("pk")
        )
        if len(locked_rows) != len({item.pk for item in meetings}):
            raise ValidationError("One or more checking-round meetings no longer exist.")
        locked_map = {item.pk: item for item in locked_rows}
        locked = [locked_map[item.pk] for item in meetings]
        first = locked[0]
        require_attendance_permission(
            user=actor,
            permission_code=ENCODE_PERMISSION,
            tenant_id=first.tenant_id,
            campus_id=first.campus_id,
            department_id=first.department_id,
        )
        if any(
            item.tenant_id != first.tenant_id
            or item.campus_id != first.campus_id
            or item.department_id != first.department_id
            for item in locked
        ):
            raise ValidationError("Checking-round meetings must share tenant, campus, and department scope.")
        snapshots = [_meeting_manifest_snapshot(item) for item in locked]
        frozen_at = timezone.now()
        checking_round = CheckingRound(
            tenant=first.tenant,
            campus=first.campus,
            department=first.department,
            academic_year=academic_year,
            term=term,
            daily_occurrence_date=daily_occurrence_date,
            checking_date=checking_date,
            checking_end_date=checking_end_date,
            label=label.strip(),
            manifest_hash=_canonical_hash(snapshots),
            manifest_frozen_at=frozen_at,
            created_by=actor,
            saved_route=saved_route,
            route_name_snapshot=saved_route.name if saved_route else "Default ordering",
            route_revision_snapshot=saved_route.revision if saved_route else None,
        )
        checking_round.full_clean()
        checking_round.save()
        for sequence, (meeting, snapshot) in enumerate(zip(locked, snapshots), start=1):
            row = CheckingRoundMeeting(
                checking_round=checking_round,
                meeting=meeting,
                sequence=sequence,
                reviewed_result_revision=snapshot["result_revision"],
                meeting_snapshot=snapshot,
            )
            row.full_clean()
            row.save()
        AuditService.log_event(
            action="FACULTY_ATTENDANCE_CHECKING_ROUND_CREATED",
            portal="ADMIN",
            entity_type="CheckingRound",
            entity_id=checking_round.pk,
            actor=actor,
            tenant=first.tenant,
            campus=first.campus,
            after_data={
                "checking_date": checking_date,
                "checking_end_date": checking_end_date,
                "manifest_revision": checking_round.manifest_revision,
                "manifest_hash": checking_round.manifest_hash,
                "meeting_count": len(locked),
                "saved_route_id": saved_route.pk if saved_route else None,
            },
        )
        return checking_round


class ObservationService:
    @classmethod
    @transaction.atomic
    def record(
        cls,
        *,
        actor,
        checking_round,
        meeting_id,
        manifest_revision,
        submission_key,
        findings,
        note="",
        preserve_legacy_period_absence=False,
        correction_revision=None,
    ):
        lock_notice_campus(checking_round.campus_id)
        checking_round = CheckingRound.objects.select_for_update().get(pk=checking_round.pk)
        require_attendance_permission(
            user=actor,
            permission_code=ENCODE_PERMISSION,
            tenant_id=checking_round.tenant_id,
            campus_id=checking_round.campus_id,
            department_id=checking_round.department_id,
        )
        if checking_round.status != CheckingRound.Status.OPEN:
            raise ValidationError("Checking round is closed.")
        if manifest_revision != checking_round.manifest_revision:
            raise StaleAttendanceReview("Checking-round manifest changed; reload before saving.")
        row = CheckingRoundMeeting.objects.select_for_update().select_related("meeting").filter(
            checking_round=checking_round, meeting_id=meeting_id
        ).first()
        if row is None:
            raise StaleAttendanceReview("Meeting is not part of the frozen checking-round manifest.")
        normalized = _normalize_findings(findings, allow_legacy_periods=preserve_legacy_period_absence)
        if correction_revision is not None:
            saved = AttendanceResult.objects.select_for_update().filter(meeting_id=meeting_id).first()
            require_attendance_permission(user=actor, permission_code=CORRECT_PERMISSION,
                tenant_id=checking_round.tenant_id, campus_id=checking_round.campus_id,
                department_id=checking_round.department_id)
            if saved is None or not saved.revision or saved.revision != correction_revision:
                raise StaleAttendanceReview("Attendance result changed; reload and review before saving.")
            if (any(f.get("finding_type") == "ABSENCE" for f in saved.findings_snapshot)
                    and not any(f["finding_type"] == "ABSENCE" for f in normalized) and not (note or "").strip()):
                raise ValidationError("Enter a checker reason to remove the saved absence.")
        if preserve_legacy_period_absence:
            existing_result = AttendanceResult.objects.filter(meeting_id=meeting_id, revision__gt=0).first()
            if existing_result is None:
                raise ValidationError("A new attendance finding cannot use missed periods.")
            existing_findings = _normalize_findings(
                existing_result.findings_snapshot,
                allow_legacy_periods=True,
            )
            existing_absences = [item for item in existing_findings if item["finding_type"] == "ABSENCE"]
            submitted_absences = [item for item in normalized if item["finding_type"] == "ABSENCE"]
            if not any(item["missed_periods"] is not None for item in existing_absences):
                raise ValidationError("The saved attendance result has no legacy period-based absence to preserve.")
            if submitted_absences != existing_absences:
                raise ValidationError("The saved period-based absence must remain unchanged in this correction.")
        if not normalized and correction_revision is None:
            return None
        serialized = _serialize_findings(normalized)
        payload_hash = _canonical_hash({"findings": serialized, "note": note.strip()})
        existing = AttendanceObservation.objects.filter(
            round_meeting=row, submission_key=submission_key
        ).first()
        if existing:
            if existing.payload_hash == payload_hash:
                return existing
            raise StaleAttendanceReview("Submission key was already used with different findings.")
        observation = AttendanceObservation.objects.create(
            round_meeting=row,
            submission_key=submission_key,
            observed_by=actor,
            note=note.strip(),
            payload_hash=payload_hash,
        )
        for item in normalized:
            finding = AttendanceObservationFinding(observation=observation, **item)
            finding.full_clean()
            finding.save()
        AuditService.log_event(
            action="FACULTY_ATTENDANCE_OBSERVATION_RECORDED",
            portal="ADMIN",
            entity_type="AttendanceObservation",
            entity_id=observation.pk,
            actor=actor,
            tenant=checking_round.tenant_id,
            campus=checking_round.campus_id,
            after_data={"meeting_id": meeting_id, "finding_count": len(normalized)},
        )
        return observation


class AttendanceResultService:
    @staticmethod
    def _summary(findings):
        a_hours = sum(
            (item["missed_hours"] or Decimal("0"))
            for item in findings
            if item["finding_type"] == "ABSENCE" and item["notice_status"] == "A"
        )
        n_hours = sum(
            (item["missed_hours"] or Decimal("0"))
            for item in findings
            if item["finding_type"] == "ABSENCE" and item["notice_status"] == "N"
        )
        periods = sum(
            (item["missed_periods"] or Decimal("0"))
            for item in findings
            if item["finding_type"] == "ABSENCE"
        )
        late = [item for item in findings if item["finding_type"] == "LATE"]
        early = [item for item in findings if item["finding_type"] == "EARLY"]
        return {
            "absent_without_notice_hours": a_hours,
            "absent_with_notice_hours": n_hours,
            "missed_periods": periods,
            "late_flag": bool(late),
            "late_minutes": sum(item["minutes"] for item in late),
            "early_flag": bool(early),
            "early_minutes": sum(item["minutes"] for item in early),
        }

    @classmethod
    def _save_revision(
        cls, *, result, expected_revision, actor, status, faculty, findings, reason, source_observation=None,
        refresh_notices=True, validated_present=False,
    ):
        if result.revision != expected_revision:
            raise StaleAttendanceReview("Attendance result changed; reload and review before saving.")
        serialized = _serialize_findings(findings)
        if (
            result.revision > 0
            and result.status == status
            and result.faculty_user_id == faculty.pk
            and result.findings_snapshot == serialized
            and result.source_observation_id == (source_observation.pk if source_observation else None)
        ):
            return result
        summary = cls._summary(findings)
        result.revision += 1
        result.status = status
        result.faculty_user = faculty
        result.source_observation = source_observation
        result.findings_snapshot = serialized
        result.corrected_by = actor
        reason = (reason or "").strip()
        result.correction_reason = reason
        for field, value in summary.items():
            setattr(result, field, value)
        if validated_present:
            # Bulk callers already locked/validated the manifest, faculty and
            # actor. Zero-valued Present has no arbitrary numeric payload.
            # Keep local field validation and DB FK/unique/check enforcement,
            # without repeating FK-existence and constraint SELECTs per row.
            if status != AttendanceResult.Status.PRESENT or findings or source_observation:
                raise ValidationError("Only validated bulk Present may use this write path.")
            result.full_clean(exclude=["meeting", "faculty_user", "source_observation", "corrected_by"],
                              validate_unique=False, validate_constraints=False)
        else:
            result.full_clean()
        result.save()
        AttendanceResultRevision.objects.create(
            result=result,
            revision=result.revision,
            status=result.status,
            faculty_user=result.faculty_user,
            source_observation=source_observation,
            findings_snapshot=serialized,
            changed_by=actor,
            change_reason=reason,
        )
        from .staff_notices import refresh_for_meeting
        if refresh_notices:
            refresh_for_meeting(result.meeting)
        return result

    @classmethod
    @transaction.atomic
    def select_observation(cls, *, actor, observation, expected_revision, reason):
        lock_observation_notice_scope(observation.pk)
        observation = AttendanceObservation.objects.select_for_update().select_related(
            "round_meeting__meeting"
        ).get(pk=observation.pk)
        meeting = TeachingMeeting.objects.select_for_update().get(pk=observation.round_meeting.meeting_id)
        existing = AttendanceResult.objects.select_for_update().filter(meeting=meeting).first()
        permission = CORRECT_PERMISSION if existing and existing.revision else ENCODE_PERMISSION
        require_attendance_permission(
            user=actor,
            permission_code=permission,
            tenant_id=meeting.tenant_id,
            campus_id=meeting.campus_id,
            department_id=meeting.department_id,
        )
        faculty = require_confirmable_meeting_faculty(meeting)
        result = existing or AttendanceResult.objects.create(meeting=meeting)
        findings = [
            {
                "finding_type": row.finding_type,
                "segment_key": row.segment_key,
                "notice_status": row.notice_status,
                "missed_periods": row.missed_periods,
                "missed_hours": row.missed_hours,
                "minutes": row.minutes,
            }
            for row in observation.findings.all()
        ]
        before_revision = result.revision
        if (any(f.get("finding_type") == "ABSENCE" for f in result.findings_snapshot)
                and not any(f["finding_type"] == "ABSENCE" for f in findings) and not (reason or "").strip()):
            raise ValidationError("Enter a checker reason to remove the saved absence.")
        result = cls._save_revision(
            result=result,
            expected_revision=expected_revision,
            actor=actor,
            status=AttendanceResult.Status.EXCEPTION if findings else AttendanceResult.Status.PRESENT,
            faculty=faculty,
            findings=findings,
            reason=reason,
            source_observation=observation,
        )
        if result.revision != before_revision:
            AuditService.log_event(
                action="FACULTY_ATTENDANCE_RESULT_RECONCILED",
                portal="ADMIN",
                entity_type="AttendanceResult",
                entity_id=result.pk,
                actor=actor,
                tenant=meeting.tenant_id,
                campus=meeting.campus_id,
                before_data={"revision": before_revision},
                after_data={"revision": result.revision, "status": result.status, "faculty_user_id": faculty.pk},
            )
        return result

    @classmethod
    @transaction.atomic
    def confirm_present(cls, *, actor, checking_round, manifest_revision, reviewed_rows, submission_key=""):
        lock_notice_campus(checking_round.campus_id)
        checking_round = CheckingRound.objects.select_for_update().get(pk=checking_round.pk)
        require_attendance_permission(
            user=actor,
            permission_code=ENCODE_PERMISSION,
            tenant_id=checking_round.tenant_id,
            campus_id=checking_round.campus_id,
            department_id=checking_round.department_id,
        )
        if manifest_revision != checking_round.manifest_revision:
            raise StaleAttendanceReview("Checking-round manifest changed; reload before confirming present rows.")
        if checking_round.status != CheckingRound.Status.OPEN:
            raise ValidationError("Checking round is closed.")
        requested = {
            int(item["meeting_id"]): {
                "revision": int(item["result_revision"]),
                "note": (item.get("note") or "").strip(),
            }
            for item in reviewed_rows
        }
        if len(requested) != len(reviewed_rows):
            raise ValidationError("Reviewed meeting IDs must be unique.")
        if not requested:
            raise ValidationError("Select the reviewed present classes first.")
        if len(submission_key) > 64:
            raise ValidationError("Invalid confirmation submission identity.")
        payload_hash = _canonical_hash({"manifest_revision": manifest_revision, "rows": requested})
        from apps.auditlog.models import AuditLog
        prior = None
        if submission_key:
            # A current locking read is needed after the campus mutex, including
            # when an InnoDB repeatable-read snapshot predates another retry.
            prior = AuditLog.objects.select_for_update().filter(action="FACULTY_ATTENDANCE_BULK_PRESENT",
                entity_type="CheckingRound", entity_id=str(checking_round.pk), actor_user=actor,
                tenant_id=checking_round.tenant_id, campus_id=checking_round.campus_id,
                metadata_json__submission_key=submission_key).first()
            if prior and prior.metadata_json["payload_hash"] != payload_hash:
                raise StaleAttendanceReview("Submission key was already used with different selected classes or notes.")
        manifest_rows = {
            row.meeting_id: row
            for row in CheckingRoundMeeting.objects.select_for_update()
            .select_related("meeting")
            .filter(checking_round=checking_round, meeting_id__in=requested)
            .order_by('meeting_id')
        }
        if set(manifest_rows) != set(requested):
            raise StaleAttendanceReview("Reviewed rows must exactly reference meetings in the frozen manifest.")
        locked_results = {
            row.meeting_id: row
            for row in AttendanceResult.objects.select_for_update().filter(meeting_id__in=requested).order_by('meeting_id')
        }
        if prior:
            for meeting_id, revision in prior.after_json["result_revisions"]:
                if meeting_id not in locked_results or locked_results[meeting_id].revision != revision:
                    raise StaleAttendanceReview("A confirmed class changed afterward. Reload and review its current status.")
            return {"confirmed_meeting_ids": prior.after_json["confirmed_meeting_ids"],
                    "skipped_meeting_ids": prior.after_json["skipped_meeting_ids"], "replayed": True}
        for meeting_id, request in requested.items():
            expected = request["revision"]
            current = locked_results.get(meeting_id)
            current_revision = current.revision if current else 0
            if current_revision != expected:
                raise StaleAttendanceReview("Attendance result changed; reload and review before confirming present rows.")
        confirmed = []
        skipped = []
        prime_meeting_readiness([row.meeting for row in manifest_rows.values()])
        faculties = {}
        for meeting_id, row in manifest_rows.items():
            result = locked_results.get(meeting_id)
            if result is None or result.status == AttendanceResult.Status.UNVERIFIED:
                faculties[meeting_id] = require_confirmable_meeting_faculty(row.meeting)
        # New Present rows cannot change absence/late notices: no exception,
        # previous attribution or historical notice exists. Corrections retain
        # normal notice recomputation and its existing rollback/lock semantics.
        new_ids = set(manifest_rows) - set(locked_results)
        AttendanceResult.objects.bulk_create([
            AttendanceResult(meeting=row.meeting) for meeting_id, row in manifest_rows.items()
            if meeting_id in new_ids])
        for result in AttendanceResult.objects.filter(meeting_id__in=new_ids):
            result.meeting = manifest_rows[result.meeting_id].meeting
            locked_results[result.meeting_id] = result
        for meeting_id, request in requested.items():
            expected = request["revision"]
            manifest_row = manifest_rows[meeting_id]
            meeting = manifest_row.meeting
            result = locked_results.get(meeting_id)
            if result and result.status != AttendanceResult.Status.UNVERIFIED:
                skipped.append(meeting_id)
                continue
            faculty = faculties[meeting_id]
            had_history = result.revision > 0
            cls._save_revision(
                result=result,
                expected_revision=expected,
                actor=actor,
                status=AttendanceResult.Status.PRESENT,
                faculty=faculty,
                findings=[],
                reason=request["note"],
                validated_present=True,
                refresh_notices=had_history,
            )
            confirmed.append(meeting_id)
        outcome = {"confirmed_meeting_ids": confirmed, "skipped_meeting_ids": skipped}
        AuditService.log_event(action="FACULTY_ATTENDANCE_BULK_PRESENT", portal="ADMIN",
            entity_type="CheckingRound", entity_id=checking_round.pk, actor=actor,
            tenant=checking_round.tenant_id, campus=checking_round.campus_id,
            after_data={**outcome, "result_revisions": [[mid, locked_results[mid].revision] for mid in sorted(requested)]},
            metadata={"submission_key": submission_key, "payload_hash": payload_hash})
        return outcome

    @classmethod
    @transaction.atomic
    def reconcile_attribution(cls, *, actor, result, expected_revision, faculty_user, reason):
        lock_result_notice_scope(result.pk)
        result = AttendanceResult.objects.select_for_update().select_related("meeting").get(pk=result.pk)
        require_attendance_permission(
            user=actor,
            permission_code=CORRECT_PERMISSION,
            tenant_id=result.meeting.tenant_id,
            campus_id=result.meeting.campus_id,
            department_id=result.meeting.department_id,
        )
        if _pending_reconciliation_exists(result.meeting):
            raise ValidationError("Resolve meeting and coverage reconciliation before changing result attribution.")
        findings = _normalize_findings(result.findings_snapshot, allow_legacy_periods=True)
        return cls._save_revision(
            result=result,
            expected_revision=expected_revision,
            actor=actor,
            status=result.status,
            faculty=faculty_user,
            findings=findings,
            reason=reason,
            source_observation=result.source_observation,
        )

    @classmethod
    @transaction.atomic
    def correct_early_dismissal(cls, *, actor, result, expected_revision, minutes, reason):
        """Replace the E finding in the authoritative result; never add a DTR deduction."""
        lock_result_notice_scope(result.pk)
        result = AttendanceResult.objects.select_for_update().select_related("meeting", "faculty_user").get(pk=result.pk)
        meeting = result.meeting
        require_attendance_permission(
            user=actor, permission_code=CORRECT_PERMISSION,
            tenant_id=meeting.tenant_id, campus_id=meeting.campus_id, department_id=meeting.department_id,
        )
        if result.status == AttendanceResult.Status.UNVERIFIED or result.faculty_user_id is None:
            raise ValidationError("Verify attendance and faculty attribution before correcting early dismissal.")
        if _pending_reconciliation_exists(meeting):
            raise ValidationError("Resolve meeting and coverage reconciliation before correcting attendance.")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= meeting.scheduled_minutes:
            raise ValidationError("Early-dismissal minutes must be within the scheduled meeting duration.")
        findings = [
            item for item in _normalize_findings(result.findings_snapshot, allow_legacy_periods=True)
            if item["finding_type"] != "EARLY"
        ]
        if minutes:
            findings.append({
                "finding_type": "EARLY", "segment_key": "dismissal", "notice_status": "",
                "missed_periods": None, "missed_hours": None, "minutes": minutes,
            })
        before_revision = result.revision
        corrected = cls._save_revision(
            result=result, expected_revision=expected_revision, actor=actor,
            status=AttendanceResult.Status.EXCEPTION if findings else AttendanceResult.Status.PRESENT,
            faculty=result.faculty_user, findings=findings, reason=reason, source_observation=None,
        )
        if corrected.revision != before_revision:
            AuditService.log_event(
                action="FACULTY_ATTENDANCE_EARLY_CORRECTED", portal="ADMIN",
                entity_type="AttendanceResult", entity_id=result.pk,
                actor=actor, tenant=meeting.tenant_id, campus=meeting.campus_id,
                before_data={"revision": before_revision},
                after_data={"revision": corrected.revision, "early_minutes": corrected.early_minutes},
            )
        return corrected
