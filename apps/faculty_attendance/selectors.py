from datetime import date

from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.core.services.features import FeatureSettingsService

from .models import AttendanceResult, FacultyCoverage, MeetingReconciliation, TeachingMeeting
from .permissions import VIEW_PERMISSION, can_faculty_view_own_attendance, require_attendance_permission


def faculty_meeting_history(*, tenant_id, faculty_user_id, start_date=None, end_date=None):
    queryset = TeachingMeeting.objects.filter(tenant_id=tenant_id).filter(
        Q(faculty_user_id=faculty_user_id) | Q(substitution__substitute_faculty_id=faculty_user_id)
    )
    if start_date:
        queryset = queryset.filter(meeting_date__gte=start_date)
    if end_date:
        queryset = queryset.filter(meeting_date__lte=end_date)
    return queryset.distinct()


def coverage_history(*, tenant_id, faculty_user_id):
    return FacultyCoverage.objects.filter(tenant_id=tenant_id, faculty_user_id=faculty_user_id)


def unresolved_meetings(*, tenant_id, campus_id=None, department_id=None):
    queryset = TeachingMeeting.objects.filter(tenant_id=tenant_id).filter(
        Q(unresolved_coverage=True) | Q(reconciliations__status=MeetingReconciliation.Status.PENDING)
    )
    if campus_id is not None:
        queryset = queryset.filter(campus_id=campus_id)
    if department_id is not None:
        queryset = queryset.filter(department_id=department_id)
    return queryset.distinct()


def _authorized_result_queryset(*, actor, faculty_user, tenant_id, start_date, end_date):
    if not FeatureSettingsService.is_faculty_attendance_enabled(tenant_id=tenant_id):
        raise PermissionDenied("Faculty Attendance is disabled for this tenant.")
    queryset = AttendanceResult.objects.filter(
        meeting__tenant_id=tenant_id,
        faculty_user=faculty_user,
        meeting__meeting_date__gte=start_date,
        meeting__meeting_date__lte=end_date,
    ).select_related("meeting", "meeting__campus", "meeting__department")
    if actor.pk == faculty_user.pk and can_faculty_view_own_attendance(
        user=actor,
        tenant_id=tenant_id,
        campus_id=getattr(actor, "default_campus_id", None),
    ):
        return queryset, True

    allowed_ids = []
    all_rows = list(queryset)
    for row in all_rows:
        try:
            require_attendance_permission(
                user=actor,
                permission_code=VIEW_PERMISSION,
                tenant_id=row.meeting.tenant_id,
                campus_id=row.meeting.campus_id,
                department_id=row.meeting.department_id,
            )
        except PermissionDenied:
            continue
        allowed_ids.append(row.pk)
    return queryset.filter(pk__in=allowed_ids), len(allowed_ids) == len(all_rows)


def date_range_results(*, actor, faculty_user, tenant_id, start_date, end_date):
    if end_date < start_date:
        raise ValueError("end_date cannot precede start_date")
    return _authorized_result_queryset(
        actor=actor,
        faculty_user=faculty_user,
        tenant_id=tenant_id,
        start_date=start_date,
        end_date=end_date,
    )


def monthly_tardiness_summary(*, actor, faculty_user, tenant_id, year, month):
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    queryset, is_complete = _authorized_result_queryset(
        actor=actor,
        faculty_user=faculty_user,
        tenant_id=tenant_id,
        start_date=start,
        end_date=date.fromordinal(end.toordinal() - 1),
    )
    count = queryset.filter(late_flag=True).count()
    if not is_complete:
        threshold_state = None
    elif count >= 5:
        threshold_state = "exceeded"
    elif count == 4:
        threshold_state = "reached"
    elif count == 3:
        threshold_state = "nearing"
    else:
        threshold_state = "within_limit"
    return {
        "count": count,
        "threshold_state": threshold_state,
        "is_complete": is_complete,
        "scope_label": "complete_faculty_month" if is_complete else "scope_limited_subtotal",
        "start_date": start,
        "end_date": date.fromordinal(end.toordinal() - 1),
    }
