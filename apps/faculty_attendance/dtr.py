"""Teaching-hour DTR previews, checker decisions, and immutable final versions."""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.services.audit import AuditService
from apps.rbac.models import UserRole

from .models import (
    AttendanceClosureDecision, AttendanceCutoffPublication, AttendanceResult, DTRAdjustment,
    DTRMixedFindingDecision, FacultyDTR,
)
from .closures import latest_closure
from .dtr_intervals import current_mixed_decisions, needs_interval_reconciliation
from .permissions import (
    DTR_AC_SUMMARY_PERMISSION, DTR_EDIT_PERMISSION, DTR_FINALIZE_PERMISSION,
    DTR_PRINT_PERMISSION, DTR_VIEW_PERMISSION, can_faculty_view_own_attendance,
    require_attendance_permission,
)


HOUR = Decimal("0.01")
ZERO = Decimal("0")
AC_CODES = ("AC", "AREA_CHAIR", "AREA_CHAIRPERSON")


def _hours(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid decimal number of hours.") from exc
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(HOUR):
        raise ValidationError("Hours must be nonnegative with at most two decimal places.")
    return amount


def _display(value):
    return str(value.quantize(HOUR, rounding=ROUND_HALF_UP))


def calculate_hour_totals(*, teaching, admin=ZERO, a=ZERO, n=ZERO, late=ZERO,
                          early=ZERO, other=ZERO, leave=ZERO):
    """Keep exact decimal hours until the displayed totals are rounded."""
    basic = teaching + admin
    gross = a + n + late + early + other
    display_bridge = Decimal(_display(basic - gross + leave)) - (
        Decimal(_display(basic)) - Decimal(_display(gross)) + Decimal(_display(leave))
    )
    return {
        "teaching_hours": _display(teaching), "admin_hours": _display(admin),
        "basic_hours": _display(basic), "a_hours": _display(a), "n_hours": _display(n),
        "late_hours": _display(late), "early_hours": _display(early),
        "other_hours": _display(other), "gross_deductions": _display(gross),
        "leave_credit": _display(leave), "net_payable_hours": _display(basic - gross + leave),
        "display_rounding_bridge": f"{display_bridge:+.2f}",
    }


def printable_final_snapshot(saved):
    """Add print-only labels from an immutable final, never from current attendance."""
    snapshot = {**saved, "lines": [{**line} for line in saved.get("lines", [])]}
    snapshot["class_count"] = sum(line.get("kind") == "CLASS" for line in snapshot["lines"])
    snapshot["admin_count"] = sum(line.get("kind") == "ADMIN" for line in snapshot["lines"])
    for line in snapshot["lines"]:
        if line.get("kind") == "CLASS":
            minutes = line.get("scheduled_minutes")
            line["print_scheduled"] = f"{minutes} min" if minutes is not None else "Not saved"
            minutes = line.get("paid_teaching_minutes")
            line["print_credited"] = (
                f"{minutes} min" if minutes is not None else f"{line.get('teaching', '0.00')} saved hours"
            )
            for kind in ("late", "early"):
                minutes = line.get(f"{kind}_minutes")
                line[f"print_{kind}"] = (
                    f"{minutes} min" if minutes is not None else f"{line.get(kind, '0.00')} saved hours"
                )
    paid_minutes = snapshot.get("paid_teaching_minutes")
    snapshot["print_teaching_basis"] = (
        f"Credited teaching: {paid_minutes} min / 60 = {snapshot['teaching_hours']} hours"
        if paid_minutes is not None else
        f"Credited teaching: {snapshot['teaching_hours']} saved hours (minute breakdown not saved)"
    )
    late_minutes, early_minutes = snapshot.get("late_minutes"), snapshot.get("early_minutes")
    snapshot["print_late_early_basis"] = (
        f"L {late_minutes} min + E {early_minutes} min = "
        f"{_display(Decimal(late_minutes + early_minutes) / 60)} hours (rounded together)"
        if late_minutes is not None and early_minutes is not None else
        f"Saved L {snapshot.get('late_hours', '0.00')} hours; "
        f"saved E {snapshot.get('early_hours', '0.00')} hours (minute breakdown not saved)"
    )
    if "display_rounding_bridge" not in snapshot:
        bridge = Decimal(snapshot["net_payable_hours"]) - (
            Decimal(snapshot["basic_hours"]) - Decimal(snapshot["gross_deductions"])
            + Decimal(snapshot["leave_credit"])
        )
        snapshot["display_rounding_bridge"] = f"{bridge:+.2f}"
    return snapshot


def _faculty_is_ac(*, faculty, publication, department_id):
    return UserRole.objects.filter(
        user=faculty, is_active=True, role__is_active=True, role__code__in=AC_CODES,
        tenant_id__in=[publication.tenant_id, None], campus_id__in=[publication.campus_id, None],
        department_id__in=[department_id, None],
    ).exists()


def _require(actor, code, publication, department_ids):
    if not department_ids:
        raise PermissionDenied("No authorized DTR department is available.")
    for department_id in set(department_ids):
        require_attendance_permission(
            user=actor, permission_code=code, tenant_id=publication.tenant_id,
            campus_id=publication.campus_id, department_id=department_id,
        )


def latest_publication(*, tenant_id, campus_id, academic_year_id, term_id, start_date, end_date):
    return AttendanceCutoffPublication.objects.filter(
        tenant_id=tenant_id, campus_id=campus_id, academic_year_id=academic_year_id,
        term_id=term_id, start_date=start_date, end_date=end_date,
    ).order_by("-version", "-pk").first()


def _adjustment_rows(*, publication, faculty):
    return DTRAdjustment.objects.filter(
        tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        academic_year_id=publication.academic_year_id, term_id=publication.term_id,
        start_date=publication.start_date, end_date=publication.end_date, faculty_user=faculty,
    ).select_related("department", "created_by").order_by("entry_key", "-revision", "-pk")


def adjustment_revision_history(*, publication, faculty):
    """Return immutable revisions grouped by logical checker entry, latest first."""
    history = {}
    for row in _adjustment_rows(publication=publication, faculty=faculty):
        history.setdefault(row.entry_key, []).append(row)
    return history


def current_adjustments(*, publication, faculty):
    latest = {}
    for row in _adjustment_rows(publication=publication, faculty=faculty):
        latest.setdefault(row.entry_key, row)
    return sorted(latest.values(), key=lambda item: (item.entry_date, item.kind, item.pk))


def _published_entries(publication, faculty):
    return list(publication.entries.filter(faculty_user=faculty).select_related(
        "meeting", "meeting__department", "result_revision",
        "closure_decision",
    ).order_by("meeting_date", "starts_at", "pk"))


def _faculty_departments(publication, faculty, entries=None, adjustments=None):
    entries = entries if entries is not None else _published_entries(publication, faculty)
    adjustments = adjustments if adjustments is not None else current_adjustments(publication=publication, faculty=faculty)
    return {item.meeting.department_id for item in entries if item.meeting_id} | {
        item.department_id for item in adjustments
    }


@dataclass(frozen=True)
class DTRPreview:
    publication: AttendanceCutoffPublication
    faculty: object
    snapshot: dict
    blockers: tuple[str, ...]
    fingerprint: str

    @property
    def ready(self):
        return not self.blockers


def _calculate(publication, faculty, entries, adjustments, mixed_decisions):
    blockers = []
    lines = []
    seen_meetings = set()
    buckets = defaultdict(lambda: ZERO)
    totals = defaultdict(lambda: ZERO)
    for entry in entries:
        if entry.meeting_id in seen_meetings:
            blockers.append(f"{entry.meeting_date}: duplicate published meeting; review cutoff source before DTR finalization.")
            continue
        seen_meetings.add(entry.meeting_id)
        if not entry.meeting_id:
            blockers.append(f"{entry.meeting_date}: published class is missing its dated meeting.")
            continue
        if entry.meeting.reconciliations.filter(status="PENDING").exists():
            blockers.append(f"{entry.meeting_date}: dated meeting reconciliation is pending; resolve and republish before DTR finalization.")
        department_id = entry.meeting.department_id
        scheduled = Decimal(entry.scheduled_minutes) / 60
        closed = entry.status == "CLOSED"
        paid_teaching = scheduled
        applied_late_minutes = entry.late_minutes
        applied_early_minutes = entry.early_minutes
        decision_revision = None
        if closed:
            closure = entry.closure_decision
            current_closure = latest_closure(entry.meeting)
            current_result = AttendanceResult.objects.filter(meeting=entry.meeting).first()
            if not closure or not current_closure or current_closure.pk != closure.pk or (
                current_result.revision if current_result else 0
            ) != closure.result_revision_at_decision or closure.faculty_user_id != faculty.pk:
                blockers.append(f"{entry.meeting_date}: closure changed after publication; republish before DTR finalization.")
            paid_teaching = scheduled if closure and closure.pay_basis == AttendanceClosureDecision.PayBasis.REGULAR else ZERO
            deductions = {"A": ZERO, "N": ZERO, "L": ZERO, "E": ZERO}
            applied_late_minutes = applied_early_minutes = 0
            status_label = (
                f"{closure.get_kind_display()} closure; {closure.get_pay_basis_display()}"
                if closure else "Unresolved class closure"
            )
            decision_revision = closure.revision if closure else None
        else:
            new_closure = latest_closure(entry.meeting)
            if new_closure and new_closure.status == AttendanceClosureDecision.Status.CLOSED:
                blockers.append(f"{entry.meeting_date}: a new class closure exists; republish this cutoff before DTR finalization.")
            if not entry.result_revision_id:
                blockers.append(f"{entry.meeting_date}: published class is missing its attendance result revision.")
                continue
            current = AttendanceResult.objects.filter(meeting_id=entry.meeting_id).first()
            if current is None or current.revision != entry.result_revision_number or current.faculty_user_id != faculty.pk:
                blockers.append(f"{entry.meeting_date}: attendance changed after publication; republish the cutoff before finalization.")
            if entry.status == AttendanceResult.Status.UNVERIFIED:
                blockers.append(f"{entry.meeting_date}: attendance remains unverified.")
            if entry.missed_periods:
                blockers.append(f"{entry.meeting_date}: legacy missed periods need a checker-authorized hour correction; no conversion was inferred.")
            deductions = {
                "A": Decimal(entry.absent_without_notice_hours),
                "N": Decimal(entry.absent_with_notice_hours),
                "L": Decimal(entry.late_minutes) / 60,
                "E": Decimal(entry.early_minutes) / 60,
            }
            if needs_interval_reconciliation(entry):
                decision = mixed_decisions.get(entry.meeting_id)
                if decision is None or decision.result_revision_id != entry.result_revision_id:
                    blockers.append(
                        f"{entry.meeting_date}: A/N and other missed findings need dated, non-overlapping actual intervals; reconcile before finalizing."
                    )
                    deductions = {"A": ZERO, "N": ZERO, "L": ZERO, "E": ZERO}
                    applied_late_minutes = applied_early_minutes = 0
                else:
                    minutes = defaultdict(int)
                    for interval in decision.intervals:
                        minutes[interval["kind"]] += interval["minutes"]
                    deductions = {kind: Decimal(minutes[kind]) / 60 for kind in ("A", "N", "L", "E")}
                    applied_late_minutes = minutes["L"]
                    applied_early_minutes = minutes["E"]
                    decision_revision = decision.revision
            status_label = entry.get_status_display()
        if sum(deductions.values(), ZERO) > scheduled:
            blockers.append(f"{entry.meeting_date}: attendance deductions exceed this scheduled class; correct overlapping findings.")
        for kind, value in deductions.items():
            buckets[(entry.meeting_date, department_id, kind)] += value
            totals[kind] += value
        totals["teaching"] += paid_teaching
        totals["teaching_minutes"] += entry.scheduled_minutes if paid_teaching else 0
        totals["scheduled_minutes"] += entry.scheduled_minutes
        totals["late_minutes"] += applied_late_minutes
        totals["early_minutes"] += applied_early_minutes
        sections = entry.meeting_snapshot.get("sections") or []
        lines.append({
            "date": entry.meeting_date.isoformat(), "department_id": department_id,
            "kind": "CLASS", "label": ", ".join(
                f"{row.get('course_code', '')} / {row.get('section_code', '')}" for row in sections
            ) or "Recorded class", "time": f"{timezone.localtime(entry.starts_at):%H:%M}–{timezone.localtime(entry.ends_at):%H:%M}",
            "teaching": _display(paid_teaching), "admin": "0.00", "status": status_label,
            "a": _display(deductions["A"]), "n": _display(deductions["N"]),
            "late": _display(deductions["L"]), "early": _display(deductions["E"]),
            "other": "0.00", "leave": "0.00", "leave_type": "",
            "result_revision": entry.result_revision_number, "meeting_id": entry.meeting_id,
            "closure_revision": closure.revision if closed and closure else None,
            "mixed_decision_revision": decision_revision if not closed else None,
            "needs_mixed_reconciliation": not closed and needs_interval_reconciliation(entry),
            "scheduled_minutes": entry.scheduled_minutes,
            "paid_teaching_minutes": entry.scheduled_minutes if paid_teaching else 0,
            "late_minutes": applied_late_minutes, "early_minutes": applied_early_minutes,
            "original_a": str(entry.absent_without_notice_hours), "original_n": str(entry.absent_with_notice_hours),
            "original_late_minutes": entry.late_minutes, "original_early_minutes": entry.early_minutes,
            "sections": sections,
            "exact_teaching": str(paid_teaching), "exact_admin": "0", "exact_a": str(deductions["A"]),
            "exact_n": str(deductions["N"]), "exact_late": str(deductions["L"]),
            "exact_early": str(deductions["E"]), "exact_other": "0", "exact_leave": "0",
        })
    # Populate all deduction buckets before allocating leave, regardless of entry order.
    for item in sorted(adjustments, key=lambda row: row.kind == DTRAdjustment.Kind.LEAVE):
        # A zero-hour latest revision is an auditable removal, not a payable row.
        # Its history remains available through adjustment_revision_history().
        if item.hours == ZERO:
            continue
        line = {
            "date": item.entry_date.isoformat(), "department_id": item.department_id,
            "department_label": item.department.code,
            "kind": item.kind, "label": item.get_kind_display(), "time": "",
            "teaching": "0.00", "admin": "0.00", "status": item.reason,
            "a": "0.00", "n": "0.00", "late": "0.00", "early": "0.00",
            "other": "0.00", "leave": "0.00", "leave_type": item.leave_type,
            "scheduled_minutes": 0, "paid_teaching_minutes": 0,
            "late_minutes": 0, "early_minutes": 0,
            "adjustment_id": item.pk, "adjustment_revision": item.revision,
            "exact_teaching": "0", "exact_admin": "0", "exact_a": "0", "exact_n": "0",
            "exact_late": "0", "exact_early": "0", "exact_other": "0", "exact_leave": "0",
        }
        if item.kind == DTRAdjustment.Kind.ADMIN:
            totals["admin"] += item.hours
            line["admin"] = _display(item.hours)
            line["exact_admin"] = str(item.hours)
        elif item.kind == DTRAdjustment.Kind.OTHER:
            totals["other"] += item.hours
            buckets[(item.entry_date, item.department_id, "OTHER")] += item.hours
            line["other"] = _display(item.hours)
            line["exact_other"] = str(item.hours)
        else:
            bucket = (item.entry_date, item.department_id, item.offset_kind)
            available = max(ZERO, buckets[bucket] - totals[("leave_used", bucket)])
            applied = min(item.hours, available)
            totals[("leave_used", bucket)] += applied
            totals["leave"] += applied
            line["leave"] = _display(applied)
            line["exact_leave"] = str(applied)
            line["label"] = f"{item.leave_type} credit against {item.get_offset_kind_display()}"
            if applied != item.hours:
                blockers.append(f"{item.entry_date}: {item.leave_type} credit exceeds the matching dated deduction; revise the entry.")
        lines.append(line)
    basic = totals["teaching"] + totals["admin"]
    gross = sum((totals[kind] for kind in ("A", "N", "L", "E", "other")), ZERO)
    net = basic - gross + totals["leave"]
    if net < ZERO or net > basic:
        blockers.append("Deductions and leave produce payable hours outside 0 to Basic Hours; review the entries.")
    lines.sort(key=lambda row: (row["date"], row["time"], row["kind"], row.get("adjustment_id", 0)))
    snapshot = {
        "tenant_id": publication.tenant_id, "campus_id": publication.campus_id,
        "faculty_user_id": faculty.pk, "faculty_name": faculty.full_name,
        "academic_year": publication.academic_year.code, "term": publication.term.name,
        "start_date": publication.start_date.isoformat(), "end_date": publication.end_date.isoformat(),
        "publication_id": publication.pk, "publication_version": publication.version,
        "scheduled_minutes": int(totals["scheduled_minutes"]), "paid_teaching_minutes": int(totals["teaching_minutes"]),
        "late_minutes": int(totals["late_minutes"]), "early_minutes": int(totals["early_minutes"]),
        "class_count": sum(line["kind"] == "CLASS" for line in lines),
        "admin_count": sum(line["kind"] == DTRAdjustment.Kind.ADMIN for line in lines),
        "lines": lines,
        **calculate_hour_totals(
            teaching=totals["teaching"], admin=totals["admin"], a=totals["A"], n=totals["N"],
            late=totals["L"], early=totals["E"], other=totals["other"], leave=totals["leave"],
        ),
    }
    return snapshot, tuple(dict.fromkeys(blockers))


def preview_dtr(*, actor, publication, faculty, permission_code=DTR_VIEW_PERMISSION):
    publication = AttendanceCutoffPublication.objects.get(pk=publication.pk)
    entries = _published_entries(publication, faculty)
    adjustments = current_adjustments(publication=publication, faculty=faculty)
    departments = _faculty_departments(publication, faculty, entries, adjustments)
    _require(actor, permission_code, publication, departments)
    if not entries and not any(
        item.kind == DTRAdjustment.Kind.ADMIN and item.hours > ZERO for item in adjustments
    ):
        raise ValidationError("This faculty has no published teaching meetings or scheduled AC office hours for the cutoff.")
    mixed_decisions = current_mixed_decisions(publication=publication, faculty=faculty)
    snapshot, blockers = _calculate(publication, faculty, entries, adjustments, mixed_decisions)
    payload = json.dumps({"snapshot": snapshot, "blockers": blockers}, sort_keys=True, separators=(",", ":"))
    return DTRPreview(publication, faculty, snapshot, blockers, hashlib.sha256(payload.encode()).hexdigest())


@transaction.atomic
def save_adjustment(*, actor, publication, faculty, department, entry_date, kind, hours, reason,
                    leave_type="", offset_kind="", previous=None, expected_revision=0):
    publication = AttendanceCutoffPublication.objects.select_for_update().get(pk=publication.pk)
    _require(actor, DTR_EDIT_PERMISSION, publication, {department.pk})
    if department.tenant_id != publication.tenant_id or department.campus_id != publication.campus_id:
        raise ValidationError({"department": "Choose a department in this campus."})
    if not publication.start_date <= entry_date <= publication.end_date:
        raise ValidationError({"entry_date": "Choose a date within the selected cutoff."})
    if kind not in DTRAdjustment.Kind.values:
        raise ValidationError("Choose a valid DTR entry type.")
    if kind == DTRAdjustment.Kind.ADMIN and previous is None and not _faculty_is_ac(
        faculty=faculty, publication=publication, department_id=department.pk,
    ):
        message = "This faculty has no active AC assignment in this department for the cutoff."
        raise ValidationError({"department": message, "kind": message})
    if kind == DTRAdjustment.Kind.LEAVE and (
        leave_type not in DTRAdjustment.LeaveType.values or offset_kind not in DTRAdjustment.Offset.values
    ):
        raise ValidationError("Choose VL, SL, or EL and the exact deduction this credit offsets.")
    if kind != DTRAdjustment.Kind.LEAVE and (leave_type or offset_kind):
        raise ValidationError("Admin hours and other deductions cannot have leave offsets.")
    faculty_entries = _published_entries(publication, faculty)
    if faculty_entries and not any(row.meeting and row.meeting.department_id == department.pk for row in faculty_entries) and not (
        kind == DTRAdjustment.Kind.ADMIN and (previous is not None or _faculty_is_ac(
            faculty=faculty, publication=publication, department_id=department.pk,
        ))
    ):
        raise ValidationError({"department": "This faculty has no published teaching attribution in the selected department."})
    if not faculty_entries and not (kind == DTRAdjustment.Kind.ADMIN and (previous is not None or _faculty_is_ac(
        faculty=faculty, publication=publication, department_id=department.pk,
    ))):
        raise ValidationError("Faculty must have published attendance or authorized AC admin hours in this cutoff.")
    amount = _hours(hours)
    reason = (reason or "").strip()
    scope = dict(
        tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        academic_year_id=publication.academic_year_id, term_id=publication.term_id,
        start_date=publication.start_date, end_date=publication.end_date, faculty_user=faculty,
    )
    if previous is not None:
        prior = DTRAdjustment.objects.select_for_update().filter(pk=previous.pk, **scope).first()
        if prior is None:
            raise ValidationError("This DTR entry is outside the selected faculty cutoff.")
        latest = DTRAdjustment.objects.select_for_update().filter(entry_key=prior.entry_key).order_by("-revision", "-pk").first()
        if prior.pk != latest.pk or expected_revision != prior.revision:
            raise ValidationError("DTR entry changed; reload its latest revision before correcting.")
        if (prior.department_id, prior.entry_date, prior.kind) != (department.pk, entry_date, kind):
            raise ValidationError("A correction must keep its dated department and entry type.")
    else:
        if expected_revision:
            raise ValidationError("A new DTR entry cannot claim an earlier revision.")
        prior = None
        if kind == DTRAdjustment.Kind.ADMIN and any(
            item.kind == kind and item.department_id == department.pk and item.entry_date == entry_date
            for item in current_adjustments(publication=publication, faculty=faculty)
        ):
            raise ValidationError("Scheduled admin hours already exist for this date and department; correct that entry.")
    item = DTRAdjustment(
        **scope, department=department, entry_date=entry_date, kind=kind, hours=amount,
        leave_type=leave_type, offset_kind=offset_kind, reason=reason, created_by=actor,
        entry_key=prior.entry_key if prior else None,
        revision=prior.revision + 1 if prior else 1, supersedes=prior,
    )
    if prior is None:
        item.entry_key = DTRAdjustment._meta.get_field("entry_key").get_default()
    item.full_clean()
    item.save()
    AuditService.log_event(
        action="FACULTY_ATTENDANCE_DTR_ENTRY_REVISED" if prior else "FACULTY_ATTENDANCE_DTR_ENTRY_CREATED",
        portal="ADMIN", entity_type="DTRAdjustment", entity_id=item.pk,
        actor=actor, tenant=publication.tenant_id, campus=publication.campus_id,
        before_data={"revision": prior.revision, "hours": str(prior.hours)} if prior else None,
        after_data={"revision": item.revision, "faculty_user_id": faculty.pk, "kind": kind, "hours": str(amount)},
    )
    return item


def latest_dtr(*, publication, faculty):
    return FacultyDTR.objects.filter(
        tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        academic_year_id=publication.academic_year_id, term_id=publication.term_id,
        start_date=publication.start_date, end_date=publication.end_date, faculty_user=faculty,
    ).order_by("-revision", "-pk").first()


@transaction.atomic
def finalize_dtr(*, actor, publication, faculty, expected_fingerprint, reason, faculty_review_complete):
    publication = AttendanceCutoffPublication.objects.select_for_update().get(pk=publication.pk)
    if not faculty_review_complete:
        raise ValidationError("Confirm that the published attendance was available for faculty review before finalizing.")
    reason = (reason or "").strip()
    latest = latest_publication(
        tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        academic_year_id=publication.academic_year_id, term_id=publication.term_id,
        start_date=publication.start_date, end_date=publication.end_date,
    )
    if latest.pk != publication.pk:
        raise ValidationError("A newer cutoff publication exists; reload its DTR before finalizing.")
    entries = _published_entries(publication, faculty)
    result_ids = [item.result_revision.result_id for item in entries if item.result_revision_id]
    list(AttendanceResult.objects.select_for_update().filter(pk__in=result_ids))
    list(AttendanceClosureDecision.objects.select_for_update().filter(
        pk__in=[item.closure_decision_id for item in entries if item.closure_decision_id],
    ))
    list(DTRAdjustment.objects.select_for_update().filter(
        tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        start_date=publication.start_date, end_date=publication.end_date, faculty_user=faculty,
    ))
    list(DTRMixedFindingDecision.objects.select_for_update().filter(publication=publication, faculty_user=faculty))
    preview = preview_dtr(actor=actor, publication=publication, faculty=faculty, permission_code=DTR_FINALIZE_PERMISSION)
    if preview.fingerprint != expected_fingerprint:
        raise ValidationError("DTR inputs changed after review; reload before finalizing.")
    if preview.blockers:
        raise ValidationError("Resolve DTR blockers before finalizing: " + "; ".join(preview.blockers))
    previous = latest_dtr(publication=publication, faculty=faculty)
    if previous and previous.review_fingerprint == preview.fingerprint:
        raise ValidationError("This DTR is already final at the reviewed version.")
    final = FacultyDTR(
        tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        academic_year_id=publication.academic_year_id, term_id=publication.term_id,
        start_date=publication.start_date, end_date=publication.end_date,
        faculty_user=faculty, publication=publication,
        revision=previous.revision + 1 if previous else 1, supersedes=previous,
        review_fingerprint=preview.fingerprint, snapshot=preview.snapshot,
        finalization_reason=reason, finalized_by=actor, finalized_at=timezone.now(),
    )
    final.full_clean()
    final.save()
    AuditService.log_event(
        action="FACULTY_ATTENDANCE_DTR_FINALIZED", portal="ADMIN",
        entity_type="FacultyDTR", entity_id=final.pk, actor=actor,
        tenant=publication.tenant_id, campus=publication.campus_id,
        after_data={"revision": final.revision, "faculty_user_id": faculty.pk, "publication_id": publication.pk},
    )
    return final


def checker_summary(*, actor, publication):
    entries = list(publication.entries.select_related("meeting", "faculty_user"))
    faculty_ids = {item.faculty_user_id for item in entries if item.faculty_user_id}
    adjustments = list(DTRAdjustment.objects.filter(
        tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        start_date=publication.start_date, end_date=publication.end_date,
        academic_year_id=publication.academic_year_id, term_id=publication.term_id,
    ))
    faculty_ids.update(item.faculty_user_id for item in adjustments if item.kind == DTRAdjustment.Kind.ADMIN)
    _require(actor, DTR_VIEW_PERMISSION, publication, {item.meeting.department_id for item in entries if item.meeting_id} | {
        item.department_id for item in adjustments
    })
    from apps.accounts.models import User
    rows = []
    for faculty in User.objects.filter(pk__in=faculty_ids).order_by("last_name", "first_name", "pk"):
        try:
            preview = preview_dtr(actor=actor, publication=publication, faculty=faculty)
        except (PermissionDenied, ValidationError):
            continue
        rows.append({"faculty": faculty, "preview": preview, "final": latest_dtr(publication=publication, faculty=faculty)})
    return rows


def ac_department_summary(*, actor, publication, department):
    if department.tenant_id != publication.tenant_id or department.campus_id != publication.campus_id:
        raise PermissionDenied("Department is outside this campus.")
    if not _faculty_is_ac(faculty=actor, publication=publication, department_id=department.pk):
        raise PermissionDenied("An active AC role for this department is required.")
    _require(actor, DTR_AC_SUMMARY_PERMISSION, publication, {department.pk})
    _require(actor, DTR_PRINT_PERMISSION, publication, {department.pk})
    finals = FacultyDTR.objects.filter(
        tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        academic_year_id=publication.academic_year_id, term_id=publication.term_id,
        start_date=publication.start_date, end_date=publication.end_date,
    ).order_by("faculty_user_id", "-revision", "-pk")
    latest = {}
    for final in finals:
        latest.setdefault(final.faculty_user_id, final)
    rows = []
    for final in latest.values():
        if final.publication_id != publication.pk:
            continue
        relevant = [line for line in final.snapshot["lines"] if line["department_id"] == department.pk]
        if not relevant:
            continue
        sum_field = lambda field: sum((Decimal(line["exact_" + field]) for line in relevant), ZERO)
        basic = sum_field("teaching") + sum_field("admin")
        deductions = sum((sum_field(field) for field in ("a", "n", "late", "early", "other")), ZERO)
        leave = sum_field("leave")
        rows.append({
            "faculty_name": final.snapshot["faculty_name"], "revision": final.revision,
            "teaching": _display(sum_field("teaching")), "admin": _display(sum_field("admin")),
            "basic": _display(basic), "deductions": _display(deductions),
            "leave": _display(leave), "net": _display(basic - deductions + leave),
        })
    return rows


def faculty_final_dtr(*, user, final):
    if final.faculty_user_id != user.pk or not can_faculty_view_own_attendance(
        user=user, tenant_id=final.tenant_id, campus_id=final.campus_id,
    ):
        raise PermissionDenied("Only the DTR owner can view this finalized record.")
    return final
