"""Scoped, previewed legacy initialization and explicit frozen-meeting recovery."""
from datetime import datetime, time, timedelta
import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment
from .models import (AttendanceCutoffPublicationEntry, AttendanceResult,
                     FacultyCoverage, MeetingCoverageAdoption, MeetingReconciliation, TeachingMeeting)
from .permissions import MANAGE_COVERAGE_PERMISSION, RECONCILE_PERMISSION
from .services import CoverageService, ReconciliationService, _audit, _meeting_snapshot, _require


class CoverageAdoptionService:
    @staticmethod
    def _draft(*, actor, meeting, offerings, reason="", lock=False):
        """Shared read-only eligibility/evidence checks; POST repeats them under locks."""
        if not offerings or not meeting.unresolved_coverage or meeting.faculty_user_id:
            raise ValidationError("Only an unresolved initial attribution can adopt coverage.")
        if meeting.faculty_snapshot.get("college_academic_sync"):
            raise ValidationError(
                "Unresolved College synchronization evidence cannot be recovered by legacy adoption. "
                "Review the dated Faculty Assignments and linked Course Offerings, including Effective from, for this class.")
        if hasattr(meeting, "substitution"):
            raise ValidationError("An explicit substitution already controls this meeting; keep it and review separately.")
        if (AttendanceResult.objects.filter(meeting=meeting, revision__gt=0).exists()
                or AttendanceCutoffPublicationEntry.objects.filter(meeting=meeting).exists()
                or meeting.closure_decisions.exists()):
            raise ValidationError("Recorded or published attendance needs the authorized correction/republication workflow.")
        coverages = []
        for offering in offerings:
            queryset = FacultyCoverage.objects.filter(
                offering=offering, effective_from__lte=meeting.starts_at).filter(
                Q(effective_until__isnull=True) | Q(effective_until__gt=meeting.starts_at))
            if lock:
                queryset = queryset.select_for_update()
            matches = list(queryset)
            if len(matches) != 1:
                raise ValidationError("Every linked section needs one verified coverage interval at class start.")
            coverages.extend(matches)
        if len({c.faculty_user_id for c in coverages}) != 1:
            raise ValidationError("Linked sections have conflicting dated faculty coverage.")
        pending = list(meeting.reconciliations.filter(status="PENDING"))
        refs = {f"coverage:{c.pk}" for c in coverages}
        if any(r.source_type != "COVERAGE" or r.source_reference not in refs for r in pending):
            raise ValidationError("Resolve other source/history changes before adopting this coverage.")
        primary_ids = list(meeting.offering_links.filter(is_primary=True).values_list("offering_id", flat=True))
        if len(primary_ids) != 1:
            raise ValidationError("The dated meeting needs exactly one primary offering link before recovery.")
        primary_id = primary_ids[0]
        primary = next(c for c in coverages if c.offering_id == primary_id)
        adoption = MeetingCoverageAdoption(
            meeting=meeting, coverage=primary, faculty_user=primary.faculty_user, adopted_by=actor,
            reason=(reason or "").strip(), decision_snapshot={
                "original_meeting": _meeting_snapshot(meeting),
                "faculty_user_id": primary.faculty_user_id, "faculty_name": primary.faculty_user.full_name,
                "coverage": [{"id": c.pk, "offering_id": c.offering_id,
                              "source_assignment_id": c.source_assignment_id,
                              "effective_from": c.effective_from.isoformat(),
                              "effective_until": c.effective_until.isoformat() if c.effective_until else None}
                             for c in coverages],
            })
        adoption.full_clean()
        return adoption, pending

    @classmethod
    def preview(cls, *, actor, meeting):
        """Return verified candidate evidence without saving or resolving anything."""
        offerings = list(CourseOffering.objects.filter(attendance_meetings=meeting).order_by("pk"))
        for offering in offerings:
            _require(actor, MANAGE_COVERAGE_PERMISSION, offering)
            _require(actor, RECONCILE_PERMISSION, offering)
        _require(actor, RECONCILE_PERMISSION, meeting)
        adoption, _pending = cls._draft(actor=actor, meeting=meeting, offerings=offerings)
        return adoption.faculty_user

    @classmethod
    @transaction.atomic
    def adopt(cls, *, actor, meeting, reason=""):
        # Academic mutations also lock offerings first. Never invert this order.
        offerings = list(CourseOffering.objects.select_for_update().filter(
            attendance_meetings=meeting).order_by("pk"))
        for offering in offerings:
            _require(actor, MANAGE_COVERAGE_PERMISSION, offering)
            _require(actor, RECONCILE_PERMISSION, offering)
        meeting = TeachingMeeting.objects.select_for_update().get(pk=meeting.pk)
        _require(actor, RECONCILE_PERMISSION, meeting)
        existing = MeetingCoverageAdoption.objects.filter(meeting=meeting).first()
        if existing:
            return existing
        adoption, pending = cls._draft(actor=actor, meeting=meeting, offerings=offerings, reason=reason, lock=True)
        adoption.save()
        for reconciliation in pending:
            ReconciliationService.resolve(actor=actor, reconciliation=reconciliation,
                decision=MeetingReconciliation.Decision.REVISE_FUTURE, reason=reason)
        _audit(action="FACULTY_ATTENDANCE_COVERAGE_ADOPTED", entity=adoption, actor=actor,
               after=adoption.decision_snapshot, metadata={"original_snapshots_preserved": True})
        return adoption


class CoverageInitializationService:
    @classmethod
    def preview(cls, *, actor, tenant_id, campus_id, academic_year, term, effective_from,
                department_ids=None, lock=False):
        if (academic_year.tenant_id != tenant_id or term.tenant_id != tenant_id
                or term.academic_year_id != academic_year.pk or not term.start_date or not term.end_date):
            raise ValidationError("Select a valid academic scope with semester dates.")
        if timezone.is_naive(effective_from) or not term.start_date <= timezone.localtime(effective_from).date() <= term.end_date:
            raise ValidationError("Confirm a date/time inside the selected semester.")
        until = timezone.make_aware(datetime.combine(term.end_date + timedelta(days=1), time.min))
        queryset = CourseOffering.objects.filter(tenant_id=tenant_id, campus_id=campus_id,
            academic_year=academic_year, term=term, is_active=True, status=CourseOffering.Status.OPEN)
        if department_ids:
            queryset = queryset.filter(department_id__in=department_ids)
        if lock:
            queryset = queryset.select_for_update()
        rows, evidence = [], []
        for offering in queryset.select_related("course", "section", "tenant", "campus", "department").order_by("pk"):
            _require(actor, MANAGE_COVERAGE_PERMISSION, offering)
            _require(actor, RECONCILE_PERMISSION, offering)
            assignments = FacultyAssignment.objects.filter(offering=offering).select_related("faculty_user").order_by("pk")
            coverages = FacultyCoverage.objects.filter(offering=offering).order_by("pk")
            if lock:
                assignments, coverages = assignments.select_for_update(), coverages.select_for_update()
            assignments, coverages = list(assignments), list(coverages)
            active = [a for a in assignments if a.is_active]
            eligible = [a for a in active if a.response_status == "ACCEPTED" and a.accepted_at and a.faculty_user.is_active]
            explicit_conflict = any(a.tenant_id not in (None, tenant_id) or a.campus_id not in (None, campus_id) for a in active)
            pending = list(offering.attendance_coverage_reconciliations.filter(status="PENDING").order_by("pk"))
            assignment = eligible[0] if len(eligible) == 1 else None
            overlaps = [c for c in coverages if c.effective_from < until and (c.effective_until is None or c.effective_until > effective_from)]
            status, message, coverage = "READY", "Initialize verified assignment", None
            if not active:
                status, message = "SKIP", "No active assignment; no coverage inferred"
            elif explicit_conflict or len(active) != 1 or assignment is None:
                status, message = "REVIEW", "Assignment scope, acceptance or multiple faculty needs review"
            elif any(p.source_assignment_id != assignment.pk or p.proposed_faculty_id != assignment.faculty_user_id
                     or p.prior_faculty_id or p.effective_at not in (None, effective_from)
                     or p.event_type not in ("ASSIGNMENT_CREATED", "ASSIGNMENT_IMPORTED", "ASSIGNMENT_ACCEPTED", "ASSIGNMENT_REACTIVATED")
                     for p in pending):
                status, message = "REVIEW", "An existing faculty change needs its own reconciliation"
            elif overlaps:
                if len(overlaps) == 1 and overlaps[0].faculty_user_id == assignment.faculty_user_id and overlaps[0].effective_from <= effective_from:
                    status, message, coverage = "EXISTING", "Keep existing coverage dates; no replacement or extension", overlaps[0]
                else:
                    status, message = "REVIEW", "Existing coverage conflicts/overlaps; no replacement inferred"
            meetings = list(TeachingMeeting.objects.filter(offering_links__offering=offering,
                starts_at__gte=effective_from, starts_at__lt=until).distinct().order_by("pk"))
            rows.append({"offering": offering, "assignment": assignment, "coverage": coverage,
                         "status": status, "message": message, "meetings": meetings, "pending": pending})
            evidence.append({"offering": offering.pk, "status": status,
                "assignments": [(a.pk, a.faculty_user_id, a.faculty_user.is_active, a.is_active, a.response_status,
                                 a.accepted_at, a.tenant_id, a.campus_id) for a in assignments],
                "coverage": [(c.pk, c.faculty_user_id, c.effective_from, c.effective_until) for c in coverages],
                "pending": [(p.pk, p.proposed_faculty_id, p.prior_faculty_id, p.effective_at) for p in pending],
                "meetings": [(m.pk, m.faculty_user_id, m.unresolved_coverage,
                    list(m.offering_links.values_list("offering_id", flat=True)),
                    list(m.reconciliations.values_list("pk", "status")),
                    list(m.closure_decisions.values_list("pk", "revision")),
                    list(m.cutoff_publication_entries.values_list("pk", flat=True)),
                    getattr(getattr(m, "attendance_result", None), "revision", 0),
                    getattr(getattr(m, "coverage_adoption", None), "pk", None),
                    getattr(getattr(m, "substitution", None), "pk", None)) for m in meetings]})
        fingerprint = hashlib.sha256(json.dumps({"scope": (tenant_id, campus_id, academic_year.pk, term.pk),
            "from": effective_from, "until": until, "evidence": evidence}, default=str, sort_keys=True).encode()).hexdigest()
        return {"rows": rows, "fingerprint": fingerprint, "effective_until": until}

    @classmethod
    @transaction.atomic
    def apply(cls, *, fingerprint, recover, confirmed, reason="", **scope):
        if not confirmed:
            raise ValidationError("Confirm the effective boundary and reviewed assignments before applying.")
        plan = cls.preview(**scope, lock=True)
        if plan["fingerprint"] != fingerprint:
            raise ValidationError("Assignments or attendance changed after preview. Preview again; nothing was applied.")
        created, adopted, recovery = 0, 0, []
        for row in plan["rows"]:
            if row["status"] not in ("READY", "EXISTING"):
                continue
            offering, assignment = row["offering"], row["assignment"]
            if row["coverage"] is None:
                row["coverage"] = CoverageService.create(actor=scope["actor"], offering=offering,
                    faculty_user=assignment.faculty_user, source_assignment=assignment,
                    effective_from=scope["effective_from"], effective_until=plan["effective_until"], reason=reason)
                from .assignment_attribution import INITIALIZED
                _audit(action=INITIALIZED, entity=row["coverage"], actor=scope["actor"],
                       after={"source_assignment_id": assignment.pk,
                              "attendance_setup_from": scope["effective_from"],
                              "exclusive_term_end": plan["effective_until"]},
                       metadata={"supplementary_initialization": True})
                created += 1
            for pending in row["pending"]:
                pending.status = "RESOLVED"
                pending.effective_at = scope["effective_from"]
                pending.resolved_by, pending.resolved_at = scope["actor"], timezone.now()
                pending.resolution_reason = (reason or "").strip()
                pending.full_clean()
                pending.save()
                _audit(action="FACULTY_ATTENDANCE_INITIAL_SETUP_RESOLVED", entity=pending,
                       actor=scope["actor"], after={"coverage_id": row["coverage"].pk})
        if recover:
            meetings = {m.pk: m for row in plan["rows"] if row["status"] in ("READY", "EXISTING") for m in row["meetings"]}
            for meeting in sorted(meetings.values(), key=lambda m: m.pk):
                if not meeting.unresolved_coverage:
                    continue
                try:
                    with transaction.atomic():
                        was_adopted = hasattr(meeting, "coverage_adoption")
                        CoverageAdoptionService.adopt(actor=scope["actor"], meeting=meeting, reason=reason)
                        adopted += not was_adopted
                except ValidationError as exc:
                    recovery.append({"meeting": meeting, "message": "; ".join(exc.messages)})
        return {"created": created, "adopted": adopted, "recovery": recovery, "plan": plan}
