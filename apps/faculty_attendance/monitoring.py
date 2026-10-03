"""Read-only, provisional AY/term monitoring; never creates meetings or payable DTRs."""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.rbac.models import UserRole
from apps.tenants.models import Department

from .closures import latest_closure
from .cutoffs import _meeting_signature, _occurrence_signature
from .daily_encoding import _combined_groups, expected_daily_occurrences
from .dtr_intervals import needs_interval_reconciliation
from .models import AttendanceClosureDecision, AttendanceResult, DTRAdjustment, DTRMixedFindingDecision, FacultyCoverage, TeachingMeeting
from .observations import require_confirmable_meeting_faculty, resolve_attendance_faculty
from .permissions import VIEW_PERMISSION, require_attendance_permission

ZERO = Decimal('0')
AC_CODES = {'AC', 'AREA_CHAIR', 'AREA_CHAIRPERSON'}


def hours(value):
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def authorized_departments(*, actor, tenant_id, campus_id):
    """Endpoint and notice delivery share the same effective permission/AC boundary."""
    roles = UserRole.objects.filter(user=actor, is_active=True, role__is_active=True,
                                    role__code__in=AC_CODES)
    is_ac = not actor.is_superuser and roles.exists()
    scoped_ac = roles.filter(tenant_id=tenant_id, campus_id=campus_id)
    allowed = []
    for department in Department.objects.filter(tenant_id=tenant_id, campus_id=campus_id):
        # A broad direct ALLOW must not widen an AC's assigned department.
        if is_ac and not scoped_ac.filter(department_id=department.pk).exists():
            continue
        try:
            require_attendance_permission(user=actor, permission_code=VIEW_PERMISSION,
                                          tenant_id=tenant_id, campus_id=campus_id,
                                          department_id=department.pk)
        except PermissionDenied:
            continue
        allowed.append(department.pk)
    if not allowed:
        raise PermissionDenied('Faculty Attendance viewing authority is required in this campus/department.')
    return allowed


def _deductions(meeting, result):
    amounts = {'A': Decimal(result.absent_without_notice_hours),
               'N': Decimal(result.absent_with_notice_hours),
               'L': Decimal(result.late_minutes) / 60, 'E': Decimal(result.early_minutes) / 60}
    if result.missed_periods:
        return None, 'Legacy missed periods require a checker-authorized hour correction.'
    if needs_interval_reconciliation(result):
        decision = DTRMixedFindingDecision.objects.filter(
            meeting=meeting, result_revision__result=result,
            result_revision__revision=result.revision, faculty_user_id=result.faculty_user_id,
        ).order_by('-created_at', '-revision', '-pk').first()
        if decision is None:
            return None, 'Mixed A/N and L/E findings need verified non-overlapping intervals.'
        amounts = {kind: sum((Decimal(item['minutes']) / 60 for item in decision.intervals
                             if item['kind'] == kind), ZERO) for kind in amounts}
    if sum(amounts.values(), ZERO) > Decimal(meeting.scheduled_minutes) / 60:
        return None, 'Missed time exceeds scheduled hours; checker correction is required.'
    return amounts, ''


def _recorded_row(meeting):
    result = getattr(meeting, 'attendance_result', None)
    faculty, attribution = resolve_attendance_faculty(meeting, result)
    row = {'meeting': meeting, 'meeting_id': meeting.pk, 'date': meeting.meeting_date,
           'faculty': faculty, 'department_id': meeting.department_id,
           'label': ', '.join(f"{s.get('course_code', '')} / {s.get('section_code', '')}"
                              for s in meeting.sections_snapshot),
           'time': f'{timezone.localtime(meeting.starts_at):%H:%M}–{timezone.localtime(meeting.ends_at):%H:%M}',
           'expected': Decimal(meeting.scheduled_minutes) / 60, 'actual': ZERO,
           'paid_closure': ZERO, 'verified': False, 'state': 'Unverified',
           'revision': result.revision if result else 0, 'attribution': attribution,
           'a': result.absent_without_notice_hours if result else ZERO,
           'n': result.absent_with_notice_hours if result else ZERO,
           'late_minutes': result.late_minutes if result else 0,
           'early_minutes': result.early_minutes if result else 0,
           'late_flag': bool(result and result.late_flag), 'deductions': {}, 'warning': ''}
    try:
        require_confirmable_meeting_faculty(meeting)
    except ValidationError as exc:
        row['warning'] = '; '.join(exc.messages)
        row['state'] = 'Needs reconciliation'
        return row
    closure = latest_closure(meeting)
    if closure and closure.status == AttendanceClosureDecision.Status.CLOSED:
        if closure.result_revision_at_decision != row['revision'] or closure.faculty_user_id != getattr(faculty, 'pk', None):
            row['warning'] = 'Closure attribution or attendance changed; review the dated decision.'
            row['state'] = 'Needs closure review'
        else:
            row['state'] = 'Closed — no teaching'
            row['paid_closure'] = row['expected'] if closure.pay_basis == 'REGULAR' else ZERO
        return row
    if not result or result.status == AttendanceResult.Status.UNVERIFIED or not result.revision:
        return row
    amounts, warning = _deductions(meeting, result)
    if warning:
        row['warning'] = warning
        row['state'] = 'Needs reconciliation'
        return row
    row.update(verified=True, state=result.get_status_display(), deductions=amounts,
               actual=row['expected'] - sum(amounts.values(), ZERO))
    return row


def term_summary(*, actor, tenant_id, campus_id, academic_year, term, as_of):
    if (academic_year.tenant_id != tenant_id or term.tenant_id != tenant_id
            or term.academic_year_id != academic_year.pk or not term.start_date or not term.end_date):
        raise PermissionDenied('Invalid academic scope.')
    departments = authorized_departments(actor=actor, tenant_id=tenant_id, campus_id=campus_id)
    end = min(as_of, timezone.localdate(), term.end_date)
    offerings = list(CourseOffering.objects.filter(
        tenant_id=tenant_id, campus_id=campus_id, department_id__in=departments,
        academic_year=academic_year, term=term,
    ).select_related('course', 'section', 'department').prefetch_related('attendance_source_changes'))
    groups = _combined_groups(tenant_id=tenant_id, campus_id=campus_id,
                              academic_year_id=academic_year.pk, term_id=term.pk,
                              offering_ids={o.pk for o in offerings})
    occurrences, issues = expected_daily_occurrences(offerings=offerings, term=term,
                                                    start_date=term.start_date, end_date=end,
                                                    combined_classes=groups)
    from .college_sync import retired_meeting_ids
    meetings = list(TeachingMeeting.objects.exclude(pk__in=retired_meeting_ids()).filter(
        tenant_id=tenant_id, campus_id=campus_id, department_id__in=departments,
        meeting_date__range=(term.start_date, end),
        offering_links__offering__academic_year=academic_year, offering_links__offering__term=term,
    ).distinct().select_related('attendance_result__faculty_user', 'faculty_user', 'substitution__substitute_faculty')
                    .prefetch_related('offering_links', 'reconciliations'))
    signatures = {_meeting_signature(m): m for m in meetings}
    keys = {m.occurrence_key: m for m in meetings if m.occurrence_key}
    coverage_by_offering = defaultdict(list)
    for coverage in FacultyCoverage.objects.filter(offering_id__in=[o.pk for o in offerings]).select_related('faculty_user').order_by('effective_from', 'pk'):
        coverage_by_offering[coverage.offering_id].append(coverage)
    rows = [_recorded_row(m) for m in meetings]
    for occurrence in occurrences:
        if occurrence.dated_meeting_id or keys.get(occurrence.occurrence_key) or signatures.get(_occurrence_signature(occurrence)):
            continue
        signature = _occurrence_signature(occurrence)
        if any(s[0:2] == signature[0:2] and s[2] < signature[3] and signature[2] < s[3]
               for s in signatures):
            # Overlapping changed time needs review; disjoint same-day slots remain expected.
            issues.append('Recorded schedule differs from the expected source; reconcile before treating totals as complete.')
            continue
        at = timezone.make_aware(datetime.combine(occurrence.meeting_date, occurrence.start_time))
        # Same half-open effective interval as CoverageService; batch-loaded for a whole semester.
        coverage = [next((c for c in coverage_by_offering[o.pk] if c.effective_from <= at
                          and (c.effective_until is None or c.effective_until > at)), None)
                    for o in occurrence.linked_offerings]
        faculty = coverage[0].faculty_user if all(coverage) and len({c.faculty_user_id for c in coverage}) == 1 else None
        minutes = (datetime.combine(occurrence.meeting_date, occurrence.end_time)
                   - datetime.combine(occurrence.meeting_date, occurrence.start_time)).total_seconds() / 60
        rows.append({'meeting': None, 'meeting_id': None, 'date': occurrence.meeting_date,
                     'faculty': faculty, 'department_id': occurrence.primary_offering.department_id,
                     'label': ', '.join(f'{o.course.code} / {o.section.code}' for o in occurrence.linked_offerings),
                     'time': f'{occurrence.start_time:%H:%M}–{occurrence.end_time:%H:%M}',
                     'expected': Decimal(str(minutes)) / 60, 'actual': ZERO, 'paid_closure': ZERO,
                     'verified': False, 'state': 'Unverified — not prepared', 'revision': 0,
                     'a': ZERO, 'n': ZERO, 'late_minutes': 0, 'early_minutes': 0,
                     'late_flag': False, 'deductions': {},
                     'warning': '' if faculty else 'Faculty unresolved; no assignment was inferred.'})
    summary = {}
    buckets = defaultdict(lambda: ZERO)
    for row in sorted(rows, key=lambda r: (r['date'], r['time'], r['meeting_id'] or 0)):
        faculty = row['faculty']
        item = summary.setdefault(getattr(faculty, 'pk', None), {
            'faculty': faculty, 'expected': ZERO, 'actual': ZERO, 'leave': ZERO,
            'admin': ZERO, 'paid_closure': ZERO, 'latest_verified': None, 'unverified': 0,
            'details': [], 'followups': []})
        for field in ('expected', 'actual', 'paid_closure'):
            item[field] += row[field]
        if row['verified']:
            item['latest_verified'] = row['date']
            for kind, value in row['deductions'].items():
                buckets[(faculty.pk, row['date'], row['department_id'], kind)] += value
        elif not row['state'].startswith('Closed'):
            item['unverified'] += 1
        item['details'].append(row)
    # Latest logical entries, not cutoff snapshots: no obsolete revisions or repeated publications.
    adjustments = DTRAdjustment.objects.filter(tenant_id=tenant_id, campus_id=campus_id,
        academic_year=academic_year, term=term, department_id__in=departments,
        entry_date__range=(term.start_date, end)).select_related('faculty_user').order_by('entry_key', '-revision', '-pk')
    current = {}
    for adjustment in adjustments:
        current.setdefault(adjustment.entry_key, adjustment)
    for adjustment in sorted(current.values(), key=lambda a: (a.kind == 'LEAVE', a.entry_date, a.pk)):
        item = summary.setdefault(adjustment.faculty_user_id, {
            'faculty': adjustment.faculty_user, 'expected': ZERO, 'actual': ZERO, 'leave': ZERO,
            'admin': ZERO, 'paid_closure': ZERO, 'latest_verified': None, 'unverified': 0,
            'details': [], 'followups': []})
        if adjustment.kind == 'ADMIN':
            item['admin'] += adjustment.hours
        elif adjustment.kind == 'OTHER':
            buckets[(adjustment.faculty_user_id, adjustment.entry_date, adjustment.department_id, 'OTHER')] += adjustment.hours
        else:
            key = (adjustment.faculty_user_id, adjustment.entry_date, adjustment.department_id, adjustment.offset_kind)
            applied = min(adjustment.hours, max(ZERO, buckets[key]))
            buckets[key] -= applied
            item['leave'] += applied
    # Whole calendar months, including other academic scopes; no rolling-30-day policy.
    for item in summary.values():
        for field in ('expected', 'actual', 'leave', 'admin', 'paid_closure'):
            item[field] = hours(item[field])
        if not item['faculty']:
            continue
        late_results = AttendanceResult.objects.filter(
            faculty_user=item['faculty'], meeting__tenant_id=tenant_id, meeting__campus_id=campus_id,
            meeting__department_id__in=departments, late_flag=True,
            status=AttendanceResult.Status.EXCEPTION, revision__gt=0,
            meeting__meeting_date__range=(term.start_date.replace(day=1), end))
        counts = defaultdict(int)
        for result in late_results.select_related('meeting'):
            try:
                require_confirmable_meeting_faculty(result.meeting)
            except ValidationError:
                continue
            closure = latest_closure(result.meeting)
            if closure and closure.status == 'CLOSED':
                continue
            counts[result.meeting.meeting_date.replace(day=1)] += 1
        item['followups'] = [{'month': month, 'count': count} for month, count in sorted(counts.items()) if count >= 4]
    return {'rows': sorted(summary.values(), key=lambda r: (r['faculty'] is None, getattr(r['faculty'], 'full_name', ''))),
            'issues': [getattr(i, 'contextual_message', str(i)) for i in issues],
            'as_of': end, 'provisional': end < term.end_date, 'departments': departments}
