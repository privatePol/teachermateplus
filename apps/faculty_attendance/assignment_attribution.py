"""Read-only attribution for established assignments predating attendance setup.

Supplementary coverage is not the academic assignment. Never extend a dated
end, override a recorded owner, or infer an exact teaching start from acceptance.
"""
from collections import defaultdict

from django.db import connection
from django.db.models import prefetch_related_objects
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.academics.models import FacultyAssignment
from apps.auditlog.models import AuditLog
from .models import FacultyCoverage, CoverageReconciliation


INITIALIZED = "FACULTY_ATTENDANCE_LEGACY_COVERAGE_INITIALIZED"
CLOSED = "FACULTY_ATTENDANCE_COVERAGE_CLOSED"


class AssignmentEvidence:
    def __init__(self, offerings, *, lock=False):
        self.offerings = {o.pk: o for o in offerings}
        prefetch_related_objects(list(self.offerings.values()), "term")
        self.assignments, self.coverages, self.changes = (defaultdict(list) for _ in range(3))
        self.origins = defaultdict(set)
        self.closure_audits = defaultdict(list)
        ids = list(self.offerings)
        queries = (
            (FacultyAssignment.objects.filter(offering_id__in=ids).select_related("faculty_user"), self.assignments),
            (FacultyCoverage.objects.filter(offering_id__in=ids).select_related("faculty_user"), self.coverages),
            (CoverageReconciliation.objects.filter(offering_id__in=ids), self.changes),
        )
        for query, destination in queries:
            for row in query.select_for_update() if lock else query:
                destination[row.offering_id].append(row)
        coverage_ids = [str(c.pk) for rows in self.coverages.values() for c in rows]
        audits = AuditLog.objects.filter(entity_type="FacultyCoverage", entity_id__in=coverage_ids,
            action__in=(INITIALIZED, "FACULTY_ATTENDANCE_COVERAGE_CREATED",
                        CLOSED, "FACULTY_ATTENDANCE_ACADEMIC_COVERAGE_SYNCED"))
        for audit in audits.select_for_update() if lock else audits:
            self.origins[int(audit.entity_id)].add(audit.action)
            if audit.action == CLOSED:
                self.closure_audits[int(audit.entity_id)].append(audit)

    def _later_unassignment_closure(self, coverage, changes):
        """Correlate close's audit with the already validated unassignment.

        resolve closes coverage before saving its resolution actor/time. The
        audit must identify that coverage, scope, actor and effective boundary
        within the reconciliation's lifetime; its action alone is insufficient.
        All evidence is loaded by the existing locked/batched audit query.
        """
        audits = self.closure_audits[coverage.pk]
        if not audits:
            return False
        for audit in audits:
            try:
                boundary = parse_datetime(audit.after_json.get("effective_until"))
            except (AttributeError, TypeError, ValueError):
                return False
            if (boundary is None or timezone.is_naive(boundary)
                    or boundary != coverage.effective_until
                    or (audit.tenant_id, audit.campus_id) != (coverage.tenant_id, coverage.campus_id)
                    or not any(change.effective_at == boundary and change.resolved_by_id
                        and audit.actor_user_id == change.resolved_by_id and change.resolved_at
                        and change.created_at <= audit.created_at <= change.resolved_at
                        for change in changes)):
                return False
        return True

    def at(self, offering, at, *, saved_faculty=None, through=None):
        offering = self.offerings[offering.pk]
        rows = self.coverages[offering.pk]
        scope = (offering.tenant_id, offering.campus_id, offering.department_id)
        if any((c.tenant_id, c.campus_id, c.department_id) != scope for c in rows):
            return None
        dated = [c for c in rows if c.effective_from <= at
                 and (c.effective_until is None or at < c.effective_until)]
        if dated:
            return dated[0].faculty_user if len(dated) == 1 else None
        # Only one retained assignment can prove this legacy baseline. An
        # inactive owner needs validated saved evidence of a later unassignment.
        assignments = self.assignments[offering.pk]
        if len(assignments) != 1:
            return None
        assignment = assignments[0]
        changes = self.changes[offering.pk]
        # An immutable saved owner can retain the earlier baseline after a
        # resolved later unassignment, not authorize classes at/after it.
        historical = bool(saved_faculty and through and changes and all(
            (c.tenant_id, c.campus_id, c.department_id) == scope
            and c.status == "RESOLVED" and c.event_type == "UNASSIGNMENT"
            and c.source_assignment_id == assignment.pk
            and c.prior_faculty_id == saved_faculty.pk and c.proposed_faculty_id is None
            and c.effective_at is not None and c.effective_at >= through
            and c.effective_at > at for c in changes))
        if saved_faculty and assignment.faculty_user_id != saved_faculty.pk:
            return None
        if ((not assignment.is_active and not historical) or not assignment.is_primary
                or assignment.response_status != "ACCEPTED" or not assignment.faculty_user.is_active
                or assignment.tenant_id not in (None, offering.tenant_id)
                or assignment.campus_id not in (None, offering.campus_id)
                or not assignment.assigned_at or not assignment.accepted_at
                or assignment.assigned_at > at or assignment.accepted_at > at):
            return None
        day = timezone.localtime(at).date()
        if not offering.term.start_date <= day <= offering.term.end_date:
            return None
        # No fallback past an explicit end, across ownership intervals, or before
        # a dated synchronization/manual change. Untagged legacy supplemental
        # rows alone do not establish a real-world activation date.
        if len(rows) > 1 or any(c.source_assignment_id != assignment.pk
                or c.faculty_user_id != assignment.faculty_user_id
                or c.effective_from <= at for c in rows):
            return None
        for c in rows:
            origin = self.origins[c.pk]
            if CLOSED in origin:
                if not historical or not self._later_unassignment_closure(c, changes):
                    return None
                origin = origin - {CLOSED}
            if origin and (INITIALIZED not in origin
                    or "FACULTY_ATTENDANCE_ACADEMIC_COVERAGE_SYNCED" in origin):
                return None
        # Initial-setup reconciliation is supplementary only when its persisted
        # origin explicitly says so. Every other historical event stays binding.
        initialized = {c.pk for c in rows if INITIALIZED in self.origins[c.pk]}
        for change in self.changes[offering.pk]:
            if historical:
                continue
            if not (initialized and change.status == "RESOLVED" and not change.prior_faculty_id
                    and change.source_assignment_id == assignment.pk
                    and change.proposed_faculty_id == assignment.faculty_user_id
                    and change.source_reference == f"assignment:{assignment.pk}:coverage-setup"):
                return None
        return assignment.faculty_user

    def linked_at(self, offerings, at, *, saved_faculty=None, through=None):
        faculties = [self.at(o, at, saved_faculty=saved_faculty, through=through) for o in offerings]
        return faculties[0] if faculties and all(f is not None for f in faculties) and len({f.pk for f in faculties}) == 1 else None


def legacy_meeting_faculty(meeting, *, evidence=None, result=None, result_revision=None):
    """Resolve NULL initial snapshots without changing a meeting or its manifest."""
    if (not meeting.unresolved_coverage or meeting.faculty_user_id
            or meeting.faculty_snapshot or meeting.schedule_snapshot.get("college_source_waiting")):
        return None
    evidence = evidence or getattr(meeting, "_attendance_assignment_evidence", None)
    links = list(meeting.offering_links.all())
    if not links or sum(link.is_primary for link in links) != 1:
        return None
    if evidence and any(l.offering_id not in evidence.offerings for l in links):
        return None
    offerings = ([evidence.offerings[l.offering_id] for l in links] if evidence
                 else [l.offering for l in links])
    if any((o.tenant_id, o.campus_id, o.department_id)
           != (meeting.tenant_id, meeting.campus_id, meeting.department_id) for o in offerings):
        return None
    if meeting.reconciliations.exists():
        return None
    evidence = evidence or AssignmentEvidence(offerings, lock=connection.in_atomic_block)
    saved_faculty = None
    if result is not None:
        if (result.meeting_id != meeting.pk or not result.faculty_user_id or not result.revision
                or result.status not in ("PRESENT", "EXCEPTION") or result_revision is None
                or result_revision.result_id != result.pk or result_revision.revision != result.revision
                or result_revision.faculty_user_id != result.faculty_user_id
                or result_revision.status != result.status
                or result_revision.findings_snapshot != result.findings_snapshot
                or result_revision.source_observation_id != result.source_observation_id):
            return None
        saved_faculty = result.faculty_user
    faculty = evidence.linked_at(offerings, meeting.starts_at,
        saved_faculty=saved_faculty, through=meeting.ends_at if saved_faculty else None)
    if faculty:
        meeting._legacy_assignment_snapshot = {
            "basis": "ESTABLISHED_ASSIGNMENT", "faculty_user_id": faculty.pk,
            "source_assignment_ids": [a.pk for o in offerings for a in evidence.assignments[o.pk]],
            "coverage_dates_unchanged": True,
        }
    return faculty
