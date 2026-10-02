"""Campus-scoped in-app notices. Every read rechecks current evidence and recipient authority."""

import hashlib
import json
from datetime import date, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from apps.accounts.models import User
from apps.core.services.permissions import PermissionService
from apps.rbac.models import UserPermission, UserRole

from .closures import latest_closure
from .models import AttendanceResult, AttendanceStaffNotice, TeachingMeeting
from .monitoring import authorized_departments
from .observations import require_confirmable_meeting_faculty
from .notice_locking import lock_notice_campus


def _eligible_results(actor, tenant_id, campus_id, faculty_id, start, end, *, lock=False):
    # In-app staff notices are never delivered through the faculty portal permission alone.
    if not actor.is_active or not PermissionService.has_permission(actor, 'admin_portal.access', tenant_id=tenant_id, campus_id=campus_id):
        raise PermissionDenied('Staff portal access is required.')
    departments = authorized_departments(actor=actor, tenant_id=tenant_id, campus_id=campus_id)
    results = AttendanceResult.objects.filter(faculty_user_id=faculty_id,
        meeting__tenant_id=tenant_id, meeting__campus_id=campus_id,
        meeting__department_id__in=departments, meeting__meeting_date__range=(start, end),
        status='EXCEPTION', revision__gt=0).select_related('meeting').order_by('meeting_id')
    if lock:
        # Current locking read, including under InnoDB REPEATABLE READ: a snapshot
        # opened by pre-lock identity queries must not retain an old monthly count.
        results = results.select_for_update()
    valid = []
    for result in results:
        try:
            require_confirmable_meeting_faculty(result.meeting)
        except ValidationError:
            continue
        if lock:
            from .models import AttendanceClosureDecision
            closure = AttendanceClosureDecision.objects.select_for_update().filter(
                meeting=result.meeting).order_by('-revision', '-pk').first()
        else:
            closure = latest_closure(result.meeting)
        if closure and closure.status == 'CLOSED':
            continue
        valid.append(result)
    return valid


def _payload(*, actor, tenant_id, campus_id, faculty_id, kind, meeting=None, month=None, lock=False):
    start = meeting.meeting_date if meeting else month
    end = start if meeting else date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1) - timedelta(days=1)
    results = _eligible_results(actor, tenant_id, campus_id, faculty_id, start, end, lock=lock)
    if kind == 'ABSENCE':
        result = next((r for r in results if r.meeting_id == meeting.pk), None)
        if result is None or not any(f.get('finding_type') == 'ABSENCE' for f in result.findings_snapshot):
            return None
        return {'meeting_id': meeting.pk, 'date': str(meeting.meeting_date), 'revision': result.revision,
                'a': str(result.absent_without_notice_hours), 'n': str(result.absent_with_notice_hours),
                'label': ', '.join(f"{s.get('course_code', '')} / {s.get('section_code', '')}" for s in meeting.sections_snapshot)}
    late = sorted((r for r in results if r.late_flag), key=lambda r: r.meeting_id)
    if len(late) < 4:
        return None
    return {'month': str(month), 'count': len(late),
            'revisions': [[r.meeting_id, r.revision] for r in late],
            'message': 'Monthly limit reached; staff follow-up. Dean memo remains manual.'}


def _fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@transaction.atomic
def refresh_for_meeting(meeting):
    """Called within authorized result/closure transactions, not from GET or migrations."""
    lock_notice_campus(meeting.campus_id)
    meeting = TeachingMeeting.objects.select_for_update().get(pk=meeting.pk)
    result = AttendanceResult.objects.select_for_update().filter(meeting=meeting).first()
    faculty_ids = set(AttendanceStaffNotice.objects.filter(meeting=meeting).values_list('faculty_user_id', flat=True))
    faculty_ids.update(result.history.select_for_update().exclude(faculty_user_id=None).order_by('pk').values_list('faculty_user_id', flat=True) if result else [])
    if result and result.faculty_user_id:
        faculty_ids.add(result.faculty_user_id)
    primary = meeting.offering_links.select_related('offering').order_by('-is_primary', 'pk').first()
    if primary is None:
        return
    offering = primary.offering
    month = meeting.meeting_date.replace(day=1)
    # Actual scoped roles/direct grants only; effective staff/view permissions are rechecked below.
    role_users = UserRole.objects.filter(is_active=True, role__is_active=True).filter(
        Q(tenant_id=meeting.tenant_id) | Q(tenant_id__isnull=True),
        Q(campus_id=meeting.campus_id) | Q(campus_id__isnull=True)).values_list('user_id', flat=True)
    direct_users = UserPermission.objects.filter(grant_type='ALLOW', permission__is_active=True,
        permission__code__in=['admin_portal.access', 'faculty_attendance.view']).filter(
        Q(tenant_id=meeting.tenant_id) | Q(tenant_id__isnull=True),
        Q(campus_id=meeting.campus_id) | Q(campus_id__isnull=True)).values_list('user_id', flat=True)
    recipients = User.objects.filter(is_active=True).filter(Q(pk__in=role_users) | Q(pk__in=direct_users))
    for faculty_id in sorted(faculty_ids):
        for kind, key in [('ABSENCE', f'absence:{meeting.pk}:{faculty_id}'),
                          ('TARDINESS', f'late:{faculty_id}:{month}')]:
            valid_recipient_ids = []
            for recipient in recipients:
                try:
                    payload = _payload(actor=recipient, tenant_id=meeting.tenant_id, campus_id=meeting.campus_id,
                                       faculty_id=faculty_id, kind=kind, meeting=meeting if kind == 'ABSENCE' else None,
                                       month=month, lock=True)
                except PermissionDenied:
                    continue
                if payload is None:
                    continue
                valid_recipient_ids.append(recipient.pk)
                AttendanceStaffNotice.objects.update_or_create(
                    tenant_id=meeting.tenant_id, campus_id=meeting.campus_id, recipient=recipient,
                    event_key=key, defaults={'faculty_user_id': faculty_id, 'meeting': meeting if kind == 'ABSENCE' else None,
                        'academic_year_id': offering.academic_year_id, 'term_id': offering.term_id,
                        'kind': kind, 'month': month if kind == 'TARDINESS' else None,
                        'source_fingerprint': _fingerprint(payload), 'payload': payload, 'is_active': True})
            AttendanceStaffNotice.objects.filter(tenant_id=meeting.tenant_id, campus_id=meeting.campus_id,
                event_key=key, is_active=True).exclude(recipient_id__in=valid_recipient_ids).update(is_active=False)


def current_notices(*, actor, tenant_id, campus_id):
    """Delivery is this read-only in-app inbox; stale or newly unauthorized items are hidden."""
    authorized_departments(actor=actor, tenant_id=tenant_id, campus_id=campus_id)
    delivered = []
    for notice in AttendanceStaffNotice.objects.filter(recipient=actor, tenant_id=tenant_id,
            campus_id=campus_id, is_active=True).select_related('meeting', 'faculty_user'):
        try:
            payload = _payload(actor=actor, tenant_id=tenant_id, campus_id=campus_id,
                               faculty_id=notice.faculty_user_id, kind=notice.kind,
                               meeting=notice.meeting, month=notice.month)
        except PermissionDenied:
            continue
        if payload is not None and _fingerprint(payload) == notice.source_fingerprint:
            delivered.append(notice)
    return delivered
