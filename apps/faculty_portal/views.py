from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re

from django.contrib import messages
from django import forms as django_forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Avg, Count, Prefetch, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.academics.services import AcademicGovernanceService, FacultyAssignmentWorkflowService
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.auditlog.models import AuditLog
from apps.admin_portal.services import model_before_after
from apps.core.decorators import permission_required, portal_required
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.enrollment.services import EnrollmentService
from apps.faculty_portal.forms import (
    AttendanceSessionForm,
    FacultyEnrollmentForm,
    FacultyMemoForm,
    FacultyReminderForm,
    GradeActivityForm,
    GradeCorrectionRequestForm,
)
from apps.faculty_portal.services import FacultyDashboardService
from apps.grading.models import (
    FacultyFinalClearanceReport,
    GradeCorrectionAttachment,
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeSubmission,
    GradeActivity,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.grading.notifications import CorrectionNotificationService
from apps.grading.reporting import CorrectionOfficialReportService, FacultyFinalClearanceReportService
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.predictions.services import (
    PredictionAuditService,
    PredictionComputationService,
    PredictionSnapshotService,
    PredictionWhatIfService,
)
from apps.notifications.models import FacultyMemo, FacultyReminder, SubmissionNonComplianceNotice
from apps.notifications.services import FacultyReminderService
from apps.students.models import Student


def _activity_before_data(activity: GradeActivity):
    return {
        "id": activity.id,
        "title": activity.title,
        "activity_date": str(activity.activity_date) if activity.activity_date else None,
        "total_score": str(activity.total_score),
        "is_active": activity.is_active,
        "offering_id": activity.offering_id,
        "template_period_id": activity.template_period_id,
        "template_component_id": activity.template_component_id,
        "template_subcomponent_id": activity.template_subcomponent_id,
        "template_detail_id": activity.template_detail_id,
    }


def _faculty_final_clearance_report_filename(report_obj: FacultyFinalClearanceReport) -> str:
    faculty_code = report_obj.faculty_user.username or f"faculty-{report_obj.faculty_user_id}"
    campus_code = report_obj.campus.code or "campus"
    term_code = report_obj.term.code or "term"
    return f"faculty-final-clearance-{campus_code}-{term_code}-{faculty_code}-{report_obj.id}.pdf"


@ensure_csrf_cookie
def public_index_view(request):
    return render(request, "faculty_portal/public_index.html")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def guide_view(request):
    return render(request, "faculty_portal/guide.html")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def guide_manual_view(request):
    return render(request, "faculty_portal/guide_manual.html")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def quick_tour_disable_view(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required."}, status=405)
    before_value = bool(getattr(request.user, "faculty_quick_tour_disabled", False))
    request.user.faculty_quick_tour_disabled = True
    request.user.save(update_fields=["faculty_quick_tour_disabled", "updated_at"])
    AuditService.log_event(
        action="FACULTY_QUICK_TOUR_DISABLED",
        portal="FACULTY",
        entity_type="User",
        entity_id=request.user.id,
        actor=request.user,
        tenant=getattr(request.user, "default_tenant_id", None),
        campus=getattr(request.user, "default_campus_id", None),
        before_data={"faculty_quick_tour_disabled": before_value},
        after_data={"faculty_quick_tour_disabled": True},
        request=request,
    )
    return JsonResponse({"ok": True})


def _faculty_assignment_queryset(user):
    return FacultyAssignment.objects.filter(
        faculty_user_id=user.id,
        is_active=True,
        offering__is_active=True,
    ).select_related(
        "tenant",
        "campus",
        "offering",
        "offering__tenant",
        "offering__campus",
        "offering__department",
        "offering__term",
        "offering__course",
        "offering__section",
        "accepted_by",
    )


def _faculty_offering_queryset(user):
    return CourseOffering.objects.filter(
        faculty_assignments__faculty_user_id=user.id,
        faculty_assignments__is_active=True,
        faculty_assignments__accepted_at__isnull=False,
        is_active=True,
    ).select_related("tenant", "campus", "department", "term", "course", "section")


def _style_form(form):
    for field in form.fields.values():
        widget = field.widget
        if isinstance(field, django_forms.DateField) and not isinstance(field, django_forms.DateTimeField):
            if getattr(widget, "input_type", None) != "date":
                field.widget = django_forms.DateInput(
                    attrs={**widget.attrs, "type": "date"},
                    format="%Y-%m-%d",
                )
                widget = field.widget
        if getattr(widget, "input_type", None) == "checkbox":
            widget.attrs["class"] = "form-check-input"
        elif widget.__class__.__name__ in {"CheckboxSelectMultiple"}:
            continue
        else:
            widget.attrs["class"] = widget.attrs.get("class", "form-control")
    return form


def _parse_decimal(value, fallback=Decimal("0")):
    try:
        if value is None or value == "":
            return fallback
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return fallback


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_decimal_display(value):
    if value in (None, ""):
        return ""
    decimal_value = Decimal(str(value))
    formatted = format(decimal_value.quantize(Decimal("0.01")), "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def _format_official_grade_display(value):
    if value in (None, ""):
        return ""
    rounded = FacultyGradingService._round_official_grade(Decimal(str(value)))
    return format(rounded, "f")


def _official_correction_report_filename(correction_request: GradeCorrectionRequest) -> str:
    period_code = correction_request.template_period.code or "PERIOD"
    course_code = correction_request.offering.course.code or "COURSE"
    section_code = correction_request.offering.section.code or "SECTION"
    return f"official-correction-{correction_request.id}-{course_code}-{section_code}-{period_code}.pdf"


def _correction_activity_label(activity: GradeActivity):
    parts = []
    if activity.title:
        parts.append(activity.title)
    if activity.template_detail and activity.template_detail.name and activity.template_detail.name != activity.title:
        parts.append(activity.template_detail.name)
    elif activity.template_subcomponent and activity.template_subcomponent.name and activity.template_subcomponent.name != activity.title:
        parts.append(activity.template_subcomponent.name)
    elif activity.template_component and activity.template_component.name and activity.template_component.name != activity.title:
        parts.append(activity.template_component.name)
    return " - ".join(parts) if parts else "Grading Item"


def _require_faculty_offering_or_404(request, offering_id: int):
    return get_object_or_404(_faculty_offering_queryset(request.user), id=offering_id)


def _require_pending_faculty_assignment_or_404(request, assignment_id: int):
    return get_object_or_404(
        _faculty_assignment_queryset(request.user).filter(accepted_at__isnull=True),
        id=assignment_id,
    )


def _require_faculty_reminder_or_404(request, reminder_id: int):
    tenant_id = getattr(request, "scope", {}).get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    qs = FacultyReminder.objects.filter(id=reminder_id, faculty_user=request.user, is_active=True)
    if tenant_id:
        qs = qs.filter(tenant_id=tenant_id)
    return get_object_or_404(qs.select_related("tenant", "campus", "offering", "offering__course", "offering__section"))


def _find_faculty_assignment(user, offering_id: int):
    return _faculty_assignment_queryset(user).filter(offering_id=offering_id).first()


def _apply_assignment_response(*, request, assignment, response_status: str, success_message: str, faculty_note: str = ""):
    before_data = {
        "response_status": assignment.response_status,
        "faculty_response_note": assignment.faculty_response_note,
        "responded_at": assignment.responded_at.isoformat() if assignment.responded_at else None,
        "accepted_at": assignment.accepted_at.isoformat() if assignment.accepted_at else None,
        "accepted_by_id": assignment.accepted_by_id,
        "response_due_at": assignment.response_due_at.isoformat() if assignment.response_due_at else None,
        "last_reminded_at": assignment.last_reminded_at.isoformat() if assignment.last_reminded_at else None,
        "reminder_count": assignment.reminder_count,
    }
    assignment.response_status = response_status
    assignment.faculty_response_note = faculty_note or None
    assignment.responded_at = timezone.now()
    if response_status == FacultyAssignment.ResponseStatus.ACCEPTED:
        assignment.accepted_at = assignment.responded_at
        assignment.accepted_by = request.user
    else:
        assignment.accepted_at = None
        assignment.accepted_by = None
    FacultyAssignmentWorkflowService.clear_response_window(assignment)
    assignment.save(
        update_fields=[
            "response_status",
            "faculty_response_note",
            "responded_at",
            "accepted_at",
            "accepted_by",
            "response_due_at",
            "last_reminded_at",
            "reminder_count",
            "updated_at",
        ]
    )
    AuditService.log_event(
        action="UPDATE",
        portal="FACULTY",
        entity_type="FacultyAssignment",
        entity_id=assignment.id,
        actor=request.user,
        tenant=assignment.tenant,
        campus=assignment.campus,
        before_data=before_data,
        after_data={
            "response_status": assignment.response_status,
            "faculty_response_note": assignment.faculty_response_note,
            "responded_at": assignment.responded_at.isoformat() if assignment.responded_at else None,
            "accepted_at": assignment.accepted_at.isoformat() if assignment.accepted_at else None,
            "accepted_by_id": assignment.accepted_by_id,
            "response_due_at": assignment.response_due_at.isoformat() if assignment.response_due_at else None,
            "last_reminded_at": assignment.last_reminded_at.isoformat() if assignment.last_reminded_at else None,
            "reminder_count": assignment.reminder_count,
        },
        metadata={
            "event": "faculty_assignment_response",
            "offering_id": assignment.offering_id,
        },
        request=request,
    )
    messages.success(request, success_message)


def _resolve_faculty_period_governance_state(
    offering,
    period,
    *,
    active_grading_period=None,
    submission=None,
    completion_window_state=None,
):
    if period is None:
        return {
            "has_active_period_setting": False,
            "matched_term_period": None,
            "is_active_period": False,
            "is_closed_by_active_period": False,
            "is_reopened_override": False,
            "is_future_period": False,
            "is_past_period": False,
            "message": "",
        }
    if submission is None:
        submission = GradingGovernanceService.get_submission(offering=offering, template_period=period)
    if completion_window_state is None:
        completion_window_state = GradingGovernanceService.get_completion_window_state(
            offering=offering,
            template_period=period,
        )
    state = AcademicGovernanceService.faculty_period_governance_state(
        tenant_id=offering.tenant_id,
        campus_id=offering.campus_id,
        term_id=offering.term_id,
        template_period=period,
        active_period_setting=active_grading_period,
        submission_status=submission.status if submission else None,
        is_correction_active=GradingGovernanceService.has_active_unlock_window(
            offering=offering,
            template_period=period,
        ),
        now=timezone.now(),
    )
    if state["is_past_period"] and (submission is None or submission.status != GradeSubmission.Status.SUBMITTED):
        state["is_reopened_override"] = True
        state["is_closed_by_active_period"] = False
        if completion_window_state["is_non_compliant"]:
            state["message"] = "This earlier period remains open until submitted even though the deadline already passed."
        else:
            state["message"] = "This earlier period remains open until submitted."
    return state


def _resolve_offering_period(request, offering_id: int, period_id: int, *, allow_governance_closed: bool = False):
    assignment = _find_faculty_assignment(request.user, offering_id)
    if assignment and not assignment.is_accepted:
        messages.error(request, "Please accept this faculty assignment first before opening the class.")
        return assignment.offering, None, None
    offering = _require_faculty_offering_or_404(request, offering_id)
    try:
        template = FacultyGradingService.resolve_template_for_offering(offering)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return offering, None, None
    period = template.periods.filter(id=period_id, is_active=True).first()
    if not period:
        messages.error(request, "Invalid grading period for this offering.")
        return offering, template, None
    governance_state = _resolve_faculty_period_governance_state(offering, period)
    if governance_state["is_closed_by_active_period"] and not allow_governance_closed:
        messages.error(request, governance_state["message"])
        return offering, template, None
    return offering, template, period


def _period_edit_state(offering, period):
    GradingGovernanceService.auto_lock_expired_reopened_gradebook(offering=offering, template_period=period)
    is_locked = GradingGovernanceService.is_locked(offering=offering, template_period=period)
    submission = GradingGovernanceService.get_submission(offering=offering, template_period=period)
    is_submitted = bool(submission and submission.status == GradeSubmission.Status.SUBMITTED)
    active_correction_request = GradingGovernanceService.get_active_correction_request(
        offering=offering, template_period=period
    )
    is_correction_active = bool(active_correction_request)
    is_auto_locked_reopened_after_deadline = GradingGovernanceService.is_auto_locked_reopened_after_deadline(
        offering=offering,
        template_period=period,
    )
    completion_window_state = GradingGovernanceService.get_completion_window_state(
        offering=offering,
        template_period=period,
    )
    governance_state = _resolve_faculty_period_governance_state(
        offering,
        period,
        submission=submission,
        completion_window_state=completion_window_state,
    )
    is_editable = (
        ((not is_locked and not is_submitted) or is_correction_active)
        and not governance_state["is_closed_by_active_period"]
    )
    can_submit_period = (
        not is_submitted
        and not governance_state["is_closed_by_active_period"]
        and (not is_locked or is_auto_locked_reopened_after_deadline)
    )
    can_self_reopen = GradingGovernanceService.can_faculty_self_reopen_before_deadline(
        offering=offering,
        template_period=period,
    )
    correction_mode = GradingGovernanceService.get_correction_mode(tenant_id=offering.tenant_id)
    system_correction_enabled = correction_mode == GradingGovernanceService.CORRECTION_MODE_SYSTEM_REQUEST
    return {
        "is_locked": is_locked,
        "is_submitted": is_submitted,
        "submission_status": submission.status if submission else None,
        "submission": submission,
        "submission_deadline": GradingGovernanceService.resolve_submission_deadline(
            offering=offering,
            template_period=period,
        ),
        "completion_grace_until": None,
        "encoding_close_deadline": None,
        "is_within_completion_grace": False,
        "grace_expired": False,
        "is_non_compliant": completion_window_state["is_non_compliant"],
        "is_overdue": completion_window_state.get("is_overdue", completion_window_state["is_non_compliant"]),
        "active_late_completion_request": None,
        "pending_late_completion_request": None,
        "has_active_late_completion_request": False,
        "has_pending_late_completion_request": False,
        "can_request_late_completion": False,
        "is_correction_active": is_correction_active,
        "active_correction_request": active_correction_request,
        "is_editable": is_editable,
        "can_submit_period": can_submit_period,
        "is_auto_locked_reopened_after_deadline": is_auto_locked_reopened_after_deadline,
        "can_self_reopen": can_self_reopen,
        "governance_state": governance_state,
        "is_governance_closed": governance_state["is_closed_by_active_period"],
        "governance_message": governance_state["message"],
        "correction_mode": correction_mode,
        "system_correction_enabled": system_correction_enabled,
    }


def _average_display(values):
    actual_values = [Decimal(value) for value in values if value is not None]
    if not actual_values:
        return None
    return FacultyGradingService._round(sum(actual_values) / Decimal(len(actual_values)))


def _average_label_from_titles(titles, fallback_label="AVE"):
    for title in titles:
        prefix = []
        for char in str(title or ""):
            if char.isalpha():
                prefix.append(char.upper())
            else:
                break
        if prefix:
            return f"{''.join(prefix)}.AVE"
    return fallback_label


def _activity_title_sort_key(title: str):
    value = (title or "").strip().upper()
    match = re.match(r"^([A-Z]+)\s*([0-9]+)?(.*)$", value)
    if not match:
        return (value, 999999, "")
    prefix, number, remainder = match.groups()
    return (prefix, int(number) if number else 999999, remainder.strip())


def _build_summary_layout(period, activities):
    components = list(
        period.components.filter(is_active=True)
        .prefetch_related("subcomponents__details")
        .order_by("sort_order", "id")
    )
    activities_by_component = defaultdict(list)
    activities_by_subcomponent = defaultdict(list)
    activities_by_detail = defaultdict(list)
    for activity in activities:
        activities_by_component[activity.template_component_id].append(activity)
        if activity.template_subcomponent_id:
            activities_by_subcomponent[activity.template_subcomponent_id].append(activity)
        if activity.template_detail_id:
            activities_by_detail[activity.template_detail_id].append(activity)

    class_standing_blocks = []
    exam_components = []

    for component in components:
        component_is_exam = FacultyGradingService.is_exam_component(component)
        subcomponents = [sub for sub in component.subcomponents.all() if sub.is_active]
        component_layout = {
            "component_id": component.id,
            "component_code": component.code,
            "label": component.name.upper(),
            "sections": [],
            "total_label": "AVE",
            "colspan": 1,
        }

        if subcomponents:
            for subcomponent in subcomponents:
                sub_activities = activities_by_subcomponent.get(subcomponent.id, [])
                details = [detail for detail in subcomponent.details.all() if detail.is_active]
                detail_groups = []
                for detail in details:
                    detail_activities = sorted(
                        activities_by_detail.get(detail.id, []),
                        key=lambda activity: (_activity_title_sort_key(activity.title), activity.activity_date or "", activity.id),
                    )
                    detail_groups.append(
                        {
                            "id": detail.id,
                            "label": detail.name.upper(),
                            "activity_ids": [activity.id for activity in detail_activities],
                            "activity_columns": [
                                {
                                    "id": activity.id,
                                    "label": activity.title,
                                    "total_score": activity.total_score,
                                }
                                for activity in detail_activities
                            ],
                            "avg_label": _average_label_from_titles(
                                [activity.title for activity in detail_activities],
                                fallback_label="AVE",
                            ),
                            "weight_percentage": Decimal(detail.weight_percentage or 0),
                            "colspan": len(detail_activities) + 1,
                        }
                    )

                if detail_groups and any(group["activity_columns"] for group in detail_groups):
                    component_layout["sections"].append(
                        {
                            "id": subcomponent.id,
                            "label": subcomponent.name.upper(),
                            "uses_nested": True,
                            "groups": detail_groups,
                            "weight_percentage": Decimal(subcomponent.weight_percentage or 0),
                            "colspan": sum(group["colspan"] for group in detail_groups),
                        }
                    )
                else:
                    ordered_sub_activities = sorted(
                        sub_activities,
                        key=lambda activity: (_activity_title_sort_key(activity.title), activity.activity_date or "", activity.id),
                    )
                    component_layout["sections"].append(
                        {
                            "id": subcomponent.id,
                            "label": subcomponent.name.upper(),
                            "uses_nested": False,
                            "activity_ids": [activity.id for activity in ordered_sub_activities],
                            "activity_columns": [
                                {
                                    "id": activity.id,
                                    "label": activity.title,
                                    "total_score": activity.total_score,
                                }
                                for activity in ordered_sub_activities
                            ],
                            "avg_label": _average_label_from_titles(
                                [activity.title for activity in ordered_sub_activities],
                                fallback_label="AVE",
                            ),
                            "weight_percentage": Decimal(subcomponent.weight_percentage or 0),
                            "colspan": len(ordered_sub_activities) + 1,
                        }
                    )
        else:
            direct_activities = sorted(
                activities_by_component.get(component.id, []),
                key=lambda activity: (_activity_title_sort_key(activity.title), activity.activity_date or "", activity.id),
            )
            component_layout["sections"].append(
                {
                    "id": component.id,
                    "label": component.name.upper(),
                    "uses_nested": False,
                    "activity_ids": [activity.id for activity in direct_activities],
                    "activity_columns": [
                        {
                            "id": activity.id,
                            "label": activity.title,
                            "total_score": activity.total_score,
                        }
                        for activity in direct_activities
                    ],
                    "avg_label": _average_label_from_titles(
                        [activity.title for activity in direct_activities],
                        fallback_label="AVE",
                    ),
                    "weight_percentage": Decimal("100"),
                    "colspan": len(direct_activities) + 1,
                }
            )

        component_layout["colspan"] = sum(section["colspan"] for section in component_layout["sections"]) + 1
        if component_is_exam:
            exam_components.append(component_layout)
        else:
            class_standing_blocks.append(component_layout)

    return {
        "class_standing_blocks": class_standing_blocks,
        "exam_components": exam_components,
    }


def _has_passed_period_deadline(*, offering, template_period, now=None) -> bool:
    now = now or timezone.now()
    lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=template_period)
    if not lock or not lock.deadline_at:
        return False
    return lock.deadline_at <= now


def _official_grade_release_state(*, offering, template, template_period, now=None):
    now = now or timezone.now()
    period_restricted = FeatureSettingsService.show_faculty_official_period_grades_after_deadline(
        tenant_id=offering.tenant_id,
        default=False,
    )
    final_restricted = FeatureSettingsService.show_faculty_official_final_grades_after_deadline(
        tenant_id=offering.tenant_id,
        default=False,
    )
    show_period = (not period_restricted) or _has_passed_period_deadline(offering=offering, template_period=template_period, now=now)

    final_period = (
        template.periods.filter(is_active=True).order_by("-sequence_no", "-id").first()
        if template is not None
        else None
    )
    is_final_period_view = bool(final_period is not None and template_period.id == final_period.id)
    show_final = bool(
        is_final_period_view
        and (
            (not final_restricted)
            or _has_passed_period_deadline(offering=offering, template_period=final_period, now=now)
        )
    )

    notes = []
    if period_restricted and not show_period:
        notes.append(
            f"Official {template_period.name} grade is hidden until the {template_period.name} deadline has passed."
        )
    if final_restricted and is_final_period_view and not show_final:
        notes.append(
            f"Official final grade is hidden until the {final_period.name} deadline has passed."
        )

    return {
        "show_period_grade": show_period,
        "show_final_grade": show_final,
        "notes": notes,
        "final_period": final_period,
        "is_final_period_view": is_final_period_view,
    }


def _load_template_preview(template_id: int):
    return (
        GradingTemplate.objects.filter(id=template_id)
        .select_related("tenant")
        .prefetch_related(
            Prefetch(
                "periods",
                queryset=GradingTemplatePeriod.objects.filter(is_active=True)
                .order_by("sequence_no", "id")
                .prefetch_related(
                    Prefetch(
                        "components",
                        queryset=GradingTemplateComponent.objects.filter(is_active=True)
                        .order_by("sort_order", "id")
                        .prefetch_related(
                            Prefetch(
                                "subcomponents",
                                queryset=GradingTemplateSubcomponent.objects.filter(is_active=True)
                                .order_by("sort_order", "id")
                                .prefetch_related(
                                    Prefetch(
                                        "details",
                                        queryset=GradingTemplateDetail.objects.filter(is_active=True).order_by(
                                            "sort_order", "id"
                                        ),
                                    )
                                ),
                            )
                        ),
                    )
                ),
            )
        )
        .first()
    )


def _build_faculty_template_preview(template):
    period_rows = []
    active_period_names = []

    for period in template.periods.all():
        active_period_names.append(period.name)
        component_rows = []
        formula_parts = []
        for component in period.components.all():
            component_weight = Decimal(component.weight_percentage or 0)
            formula_parts.append(f"{component.name} ({component_weight}%)")
            subcomponent_rows = []
            for subcomponent in component.subcomponents.all():
                detail_rows = []
                for detail in subcomponent.details.all():
                    detail_rows.append(
                        {
                            "name": detail.name,
                            "weight": Decimal(detail.weight_percentage or 0),
                            "entry_method": FacultyGradingService.score_input_mode_label(
                                FacultyGradingService.resolve_score_input_mode(
                                    template_component=component,
                                    template_subcomponent=subcomponent,
                                    template_detail=detail,
                                )
                            ),
                        }
                    )
                subcomponent_rows.append(
                    {
                        "name": subcomponent.name,
                        "weight": Decimal(subcomponent.weight_percentage or 0),
                        "entry_method": FacultyGradingService.score_input_mode_label(
                            FacultyGradingService.resolve_score_input_mode(
                                template_component=component,
                                template_subcomponent=subcomponent,
                            )
                        ),
                        "details": detail_rows,
                    }
                )

            component_rows.append(
                {
                    "name": component.name,
                    "weight": component_weight,
                    "entry_method": FacultyGradingService.score_input_mode_label(
                        FacultyGradingService.resolve_score_input_mode(template_component=component)
                    ),
                    "subcomponents": subcomponent_rows,
                }
            )

        period_rows.append(
            {
                "row": period,
                "formula": f"{period.name.upper()} GRADE = " + " + ".join(formula_parts) if formula_parts else None,
                "components": component_rows,
            }
        )

    final_formula = None
    if active_period_names:
        final_formula = (
            "FINAL GRADE follows the tenant grading profile formula for the class. "
            "If no special formula is configured, EduGradesPro averages the active grading periods "
            f"({', '.join(active_period_names)})."
        )

    return {
        "period_rows": period_rows,
        "final_formula": final_formula,
    }


def _build_summary_row_values(row, summary_layout, score_by_activity):
    student_id = row["student"].id
    class_standing_blocks = []
    exam_values = []

    for block in summary_layout["class_standing_blocks"]:
        block_values = {"component_code": block["component_code"], "sections": [], "total": None}
        component_numeric = Decimal("0")
        component_has_data = False
        section_weight_total = sum(section["weight_percentage"] for section in block["sections"]) or Decimal("100")

        for section in block["sections"]:
            if section["uses_nested"]:
                section_values = {"uses_nested": True, "groups": []}
                nested_numeric = Decimal("0")
                nested_has_data = False
                nested_weight_total = sum(group["weight_percentage"] for group in section["groups"]) or Decimal("100")

                for group in section["groups"]:
                    activity_values = [score_by_activity.get((student_id, activity_id)) for activity_id in group["activity_ids"]]
                    average_value = _average_display(activity_values)
                    if average_value is not None:
                        nested_has_data = True
                    nested_numeric += (group["weight_percentage"] / nested_weight_total) * (average_value or Decimal("0"))
                    section_values["groups"].append(
                        {
                            "activity_values": activity_values,
                            "average": average_value,
                        }
                    )

                section_score = FacultyGradingService._round(nested_numeric)
                component_numeric += (section["weight_percentage"] / section_weight_total) * section_score
                if nested_has_data:
                    component_has_data = True
                block_values["sections"].append(section_values)
            else:
                activity_values = [score_by_activity.get((student_id, activity_id)) for activity_id in section["activity_ids"]]
                average_value = _average_display(activity_values)
                if average_value is not None:
                    component_has_data = True
                section_score = average_value or Decimal("0")
                component_numeric += (section["weight_percentage"] / section_weight_total) * section_score
                block_values["sections"].append(
                    {
                        "uses_nested": False,
                        "activity_values": activity_values,
                        "average": average_value,
                    }
                )

        if component_has_data:
            block_values["total"] = FacultyGradingService._round(component_numeric)
        class_standing_blocks.append(block_values)

    for component in summary_layout["exam_components"]:
        component_scores = row.get("component_scores", {}) or {}
        exam_value = component_scores.get(component["component_code"])
        if exam_value is None:
            section_scores = []
            for section in component["sections"]:
                if section["uses_nested"]:
                    nested_values = []
                    nested_weight_total = sum(group["weight_percentage"] for group in section["groups"]) or Decimal("100")
                    nested_numeric = Decimal("0")
                    nested_has_data = False
                    for group in section["groups"]:
                        activity_values = [score_by_activity.get((student_id, activity_id)) for activity_id in group["activity_ids"]]
                        average_value = _average_display(activity_values)
                        if average_value is not None:
                            nested_has_data = True
                        nested_numeric += (group["weight_percentage"] / nested_weight_total) * (average_value or Decimal("0"))
                    if nested_has_data:
                        nested_values.append(FacultyGradingService._round(nested_numeric))
                    section_scores.extend(nested_values)
                else:
                    activity_values = [score_by_activity.get((student_id, activity_id)) for activity_id in section["activity_ids"]]
                    average_value = _average_display(activity_values)
                    if average_value is not None:
                        section_scores.append(average_value)
            exam_value = section_scores[0] if len(section_scores) == 1 else _average_display(section_scores)
        exam_values.append(exam_value)

    return {
        "class_standing_blocks": class_standing_blocks,
        "exam_values": exam_values,
    }


@portal_required("FACULTY")
@permission_required("dashboard.read")
def dashboard_view(request):
    offerings_qs = _faculty_offering_queryset(request.user)
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    campus_id = scope.get("campus_id") or getattr(request.user, "default_campus_id", None)
    if tenant_id:
        offerings_qs = offerings_qs.filter(tenant_id=tenant_id)
    if campus_id:
        offerings_qs = offerings_qs.filter(campus_id=campus_id)
    active_term_cache = {}

    def _is_in_active_scope(offering):
        tenant_id = offering.tenant_id
        if tenant_id not in active_term_cache:
            _, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
            active_term_cache[tenant_id] = active_term.id if active_term else None
        active_term_id = active_term_cache[tenant_id]
        if not active_term_id:
            return True
        return offering.term_id == active_term_id

    active_offerings = []
    archived_offerings = []
    for offering in offerings_qs.distinct():
        forced_archive = offering.status == CourseOffering.Status.ARCHIVED
        outside_active_scope = not _is_in_active_scope(offering)
        if forced_archive or outside_active_scope:
            archived_offerings.append(offering)
        else:
            active_offerings.append(offering)

    active_offering_ids = [offering.id for offering in active_offerings]
    dropped_students = 0
    withdrawn_students = 0
    incomplete_students = 0
    active_enrollments_count = 0
    activities_encoded = 0
    classes_not_submitted = 0
    failed_period_grade_count = 0
    active_students_without_grades = 0
    periods_near_deadline = 0
    submitted_periods = 0
    activities_without_scores = 0
    pending_correction_requests = 0
    classes_with_missing_grades = 0
    dashboard_now = timezone.now()

    if active_offering_ids:
        enrollment_status_counts = Enrollment.objects.filter(
            course_offering_id__in=active_offering_ids,
            is_active=True,
        ).aggregate(
            dropped=Count("id", filter=Q(enrollment_status=Enrollment.Status.DRP)),
            withdrawn=Count("id", filter=Q(enrollment_status=Enrollment.Status.W)),
            incomplete=Count("id", filter=Q(enrollment_status=Enrollment.Status.INC)),
        )
        dropped_students = enrollment_status_counts.get("dropped") or 0
        withdrawn_students = enrollment_status_counts.get("withdrawn") or 0
        incomplete_students = enrollment_status_counts.get("incomplete") or 0
        active_enrollments = list(
            Enrollment.objects.filter(
                course_offering_id__in=active_offering_ids,
                is_active=True,
                enrollment_status=Enrollment.Status.ACTIVE,
            ).only("course_offering_id", "student_id")
        )
        active_enrollments_count = len(active_enrollments)
        activities_encoded = GradeActivity.objects.filter(
            offering_id__in=active_offering_ids,
            is_active=True,
        ).count()
        activities_without_scores = (
            GradeActivity.objects.filter(
                offering_id__in=active_offering_ids,
                is_active=True,
            )
            .annotate(active_score_count=Count("student_scores", filter=Q(student_scores__is_active=True)))
            .filter(active_score_count=0)
            .count()
        )
        pending_correction_requests = GradeCorrectionRequest.objects.filter(
            offering_id__in=active_offering_ids,
            requested_by_user=request.user,
            status=GradeCorrectionRequest.Status.PENDING,
        ).count()

        active_enrollment_ids_by_offering = defaultdict(list)
        for enrollment in active_enrollments:
            active_enrollment_ids_by_offering[enrollment.course_offering_id].append(enrollment.student_id)

        grade_rows = StudentPeriodGrade.objects.filter(
            offering_id__in=active_offering_ids
        ).only("offering_id", "template_period_id", "student_id", "period_grade")
        grade_lookup = {
            (row.offering_id, row.template_period_id, row.student_id): row.period_grade
            for row in grade_rows
        }

        for offering in active_offerings:
            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
                periods = list(FacultyGradingService.get_template_periods(template))
            except ValidationError:
                classes_not_submitted += 1
                continue

            has_unsubmitted_period = False
            offering_has_missing_grades = False
            active_student_ids = active_enrollment_ids_by_offering.get(offering.id, [])
            for period in periods:
                submission = GradingGovernanceService.get_submission(offering=offering, template_period=period)
                if submission and submission.status == GradeSubmission.Status.SUBMITTED:
                    submitted_periods += 1
                else:
                    has_unsubmitted_period = True

                for student_id in active_student_ids:
                    period_grade = grade_lookup.get((offering.id, period.id, student_id))
                    if period_grade is None:
                        active_students_without_grades += 1
                        offering_has_missing_grades = True
                        continue
                    if Decimal(period_grade) < Decimal("75"):
                        failed_period_grade_count += 1

            if has_unsubmitted_period:
                classes_not_submitted += 1
            if offering_has_missing_grades:
                classes_with_missing_grades += 1

    deadline_reminder, periods_near_deadline = _build_deadline_reminder_for_offerings(
        active_offerings,
        now=dashboard_now,
    )
    active_grading_period_rows = _build_active_grading_period_rows(active_offerings, now=dashboard_now)
    at_risk_preview = FacultyDashboardService.build_at_risk_students_preview(
        user=request.user,
        active_offerings=active_offerings,
        tenant_id=tenant_id,
        limit=5,
    )
    priority_actions = FacultyDashboardService.build_priority_actions(
        user=request.user,
        active_offerings=active_offerings,
        now=dashboard_now,
        at_risk_total=at_risk_preview["total_count"] if at_risk_preview["enabled"] else 0,
    )

    stats = {
        "assigned_courses": len(active_offerings) + len(archived_offerings),
        "active_classes": len(active_offerings),
        "archived_classes": len(archived_offerings),
        "active_enrollments": active_enrollments_count,
        "activities_encoded": activities_encoded,
        "classes_not_submitted": classes_not_submitted,
        "failed_period_grade_count": failed_period_grade_count,
        "dropped_students": dropped_students,
        "withdrawn_students": withdrawn_students,
        "incomplete_students": incomplete_students,
        "active_students_without_grades": active_students_without_grades,
        "periods_near_deadline": periods_near_deadline,
        "submitted_periods": submitted_periods,
        "activities_without_scores": activities_without_scores,
        "pending_correction_requests": pending_correction_requests,
        "classes_with_missing_grades": classes_with_missing_grades,
        "deadline_reminder": deadline_reminder,
        "active_grading_period_rows": active_grading_period_rows,
        "priority_actions": priority_actions,
        "at_risk_preview": at_risk_preview,
    }
    return render(request, "faculty_portal/dashboard.html", {"stats": stats})


@portal_required("FACULTY")
@permission_required("faculty_analytics.read")
def analytics_view(request):
    include_archived = request.GET.get("include_archived") == "1"
    offerings_qs = _faculty_offering_queryset(request.user).distinct()
    active_term_cache = {}

    def _is_in_active_scope(offering):
        tenant_id = offering.tenant_id
        if tenant_id not in active_term_cache:
            _, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
            active_term_cache[tenant_id] = active_term.id if active_term else None
        active_term_id = active_term_cache[tenant_id]
        if not active_term_id:
            return True
        return offering.term_id == active_term_id

    active_offerings = []
    archived_offerings = []
    for offering in offerings_qs:
        forced_archive = offering.status == CourseOffering.Status.ARCHIVED
        outside_active_scope = not _is_in_active_scope(offering)
        if forced_archive or outside_active_scope:
            archived_offerings.append(offering)
        else:
            active_offerings.append(offering)

    selected_offerings = active_offerings + archived_offerings if include_archived else active_offerings
    selected_offering_ids = [offering.id for offering in selected_offerings]

    submission_qs = GradeSubmission.objects.filter(offering_id__in=selected_offering_ids)
    grade_qs = StudentPeriodGrade.objects.filter(
        offering_id__in=selected_offering_ids,
        period_grade__isnull=False,
    )
    active_enrollment_qs = Enrollment.objects.filter(
        course_offering_id__in=selected_offering_ids,
        is_active=True,
        enrollment_status=Enrollment.Status.ACTIVE,
    )

    def _pct(value, total):
        if not total:
            return 0
        return round((value / total) * 100, 1)

    expected_periods_by_offering = {}
    for offering in selected_offerings:
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
            expected_periods_by_offering[offering.id] = len(list(FacultyGradingService.get_template_periods(template)))
        except ValidationError:
            expected_periods_by_offering[offering.id] = 0

    submission_map = {
        row["offering_id"]: row
        for row in submission_qs.values("offering_id").annotate(
            submitted=Count("id", filter=Q(status=GradeSubmission.Status.SUBMITTED)),
            reopened=Count("id", filter=Q(status=GradeSubmission.Status.REOPENED)),
        )
    }
    enrollment_map = {
        row["course_offering_id"]: row["active_students"]
        for row in active_enrollment_qs.values("course_offering_id").annotate(active_students=Count("id"))
    }
    offering_threshold_map = {}
    grade_map = {}
    graded_count = 0
    failed_count = 0
    total_grade_sum = Decimal("0")
    for offering in selected_offerings:
        offering_threshold_map[offering.id] = FacultyGradingService.resolve_passing_threshold(offering)

    for row in grade_qs.values("offering_id", "period_grade"):
        offering_id = row["offering_id"]
        period_grade = Decimal(row["period_grade"])
        threshold = offering_threshold_map.get(offering_id, Decimal("75.00"))
        bucket = grade_map.setdefault(
            offering_id,
            {
                "grade_sum": Decimal("0"),
                "graded_rows": 0,
                "failed_rows": 0,
            },
        )
        bucket["grade_sum"] += period_grade
        bucket["graded_rows"] += 1
        graded_count += 1
        total_grade_sum += period_grade
        if period_grade < threshold:
            bucket["failed_rows"] += 1
            failed_count += 1
    for bucket in grade_map.values():
        bucket["avg_grade"] = (
            GradingGovernanceService._round(bucket["grade_sum"] / Decimal(bucket["graded_rows"]))
            if bucket["graded_rows"]
            else None
        )
    passed_count = max(graded_count - failed_count, 0)

    class_rows = []
    for offering in selected_offerings:
        submission_row = submission_map.get(offering.id, {})
        grade_row = grade_map.get(offering.id, {})
        graded_rows = grade_row.get("graded_rows", 0) or 0
        failed_rows = grade_row.get("failed_rows", 0) or 0
        submitted_periods = submission_row.get("submitted", 0) or 0
        expected_periods = expected_periods_by_offering.get(offering.id, 0)
        class_rows.append(
            {
                "offering": offering,
                "active_students": enrollment_map.get(offering.id, 0),
                "submitted_periods": submitted_periods,
                "pending_periods": max(expected_periods - submitted_periods, 0),
                "avg_grade": grade_row.get("avg_grade"),
                "failed_rows": failed_rows,
                "pass_rate": _pct(max(graded_rows - failed_rows, 0), graded_rows),
            }
        )
    class_rows.sort(key=lambda item: (-item["pending_periods"], -item["failed_rows"], item["offering"].course.code))

    distribution_ranges = [
        ("90+", Decimal("90"), None),
        ("85-89.99", Decimal("85"), Decimal("90")),
        ("80-84.99", Decimal("80"), Decimal("85")),
        ("75-79.99", Decimal("75"), Decimal("80")),
        ("Below 75", None, Decimal("75")),
    ]
    distribution_rows = []
    max_distribution_count = 0
    for label, lower, upper in distribution_ranges:
        qs = grade_qs
        if lower is not None:
            qs = qs.filter(period_grade__gte=lower)
        if upper is not None:
            qs = qs.filter(period_grade__lt=upper)
        total = qs.count()
        max_distribution_count = max(max_distribution_count, total)
        distribution_rows.append(
            {
                "label": label,
                "count": total,
                "share_pct": _pct(total, graded_count),
            }
        )
    for row in distribution_rows:
        row["width_pct"] = _pct(row["count"], max_distribution_count) if max_distribution_count else 0

    summary = {
        "active_classes": len(active_offerings),
        "archived_classes": len(archived_offerings),
        "included_classes": len(selected_offerings),
        "active_students": active_enrollment_qs.count(),
        "submitted_periods": submission_qs.filter(status=GradeSubmission.Status.SUBMITTED).count(),
        "reopened_periods": submission_qs.filter(status=GradeSubmission.Status.REOPENED).count(),
        "graded_rows": graded_count,
        "passed_rows": passed_count,
        "failed_rows": failed_count,
        "pass_rate": _pct(passed_count, graded_count),
        "avg_grade": (
            GradingGovernanceService._round(total_grade_sum / Decimal(graded_count))
            if graded_count
            else None
        ),
    }

    context = {
        "summary": summary,
        "distribution_rows": distribution_rows,
        "class_rows": class_rows,
        "include_archived": include_archived,
    }
    return render(request, "faculty_portal/analytics.html", context)


def _faculty_reminder_status(reminder, now):
    if reminder.completed_at:
        return "Completed", "success"
    if reminder.snoozed_until and reminder.snoozed_until > now:
        return "Snoozed", "secondary"
    if reminder.due_at and reminder.due_at < now:
        return "Overdue", "danger"
    if reminder.due_at and reminder.due_at.date() == now.date():
        return "Due Today", "warning"
    if reminder.email_last_sent_at:
        return "Sent", "info"
    return "Upcoming", "primary"


def _faculty_memo_queryset(user):
    return FacultyMemo.objects.filter(
        faculty_user_id=user.id,
        is_active=True,
    ).select_related(
        "tenant",
        "campus",
        "offering",
        "offering__tenant",
        "offering__campus",
        "offering__department",
        "offering__term",
        "offering__course",
        "offering__section",
        "student",
        "created_by",
    )


def _require_faculty_memo_or_404(request, memo_id: int):
    return get_object_or_404(
        _faculty_memo_queryset(request.user),
        id=memo_id,
        faculty_user=request.user,
    )


def _format_deadline_scope_list(scopes, *, limit=2):
    scope_labels = []
    for campus_code, academic_year_code, term_code in scopes:
        label = " / ".join(
            [
                str(value).strip()
                for value in (campus_code, academic_year_code, term_code)
                if value
            ]
        )
        if label:
            scope_labels.append(label)
    if not scope_labels:
        return ""
    if len(scope_labels) <= limit:
        return ", ".join(scope_labels)
    remaining = len(scope_labels) - limit
    return f"{', '.join(scope_labels[:limit])} and {remaining} more"


def _format_code_list(values, *, limit=4):
    normalized = [str(value).strip() for value in values if str(value or "").strip()]
    if not normalized:
        return ""
    if len(normalized) <= limit:
        return ", ".join(normalized)
    remaining = len(normalized) - limit
    return f"{', '.join(normalized[:limit])} and {remaining} more"


def _build_deadline_reminder_for_offerings(offerings, *, now=None):
    now = now or timezone.now()
    deadline_candidates = []
    near_deadline_cutoff = now + timezone.timedelta(hours=48)
    periods_near_deadline = 0
    offering_period_codes = set()

    for offering in offerings:
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
            periods = list(FacultyGradingService.get_template_periods(template))
        except ValidationError:
            continue

        for period in periods:
            GradingGovernanceService.auto_lock_expired_reopened_gradebook(
                offering=offering,
                template_period=period,
                at=now,
            )
            if period.code:
                offering_period_codes.add(period.code)
            submission = GradingGovernanceService.get_submission(offering=offering, template_period=period)
            if submission and submission.status == GradeSubmission.Status.SUBMITTED:
                continue
            lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=period)
            if lock and lock.deadline_at:
                deadline_candidates.append(
                    {
                        "deadline_at": lock.deadline_at,
                        "period_name": period.name,
                        "period_code": period.code,
                        "offering_id": offering.id,
                        "course_code": offering.course.code,
                        "section_code": offering.section.code,
                        "submission_status": submission.status if submission else None,
                        "is_locked": lock.is_locked,
                    }
                )
                if now <= lock.deadline_at <= near_deadline_cutoff:
                    periods_near_deadline += 1

    reminder = {
        "has_deadline": False,
        "title": "No submission deadline is set yet",
        "note": "Ask your academic or campus administrator to configure the current grading deadline so faculty can track submission timing clearly.",
        "variant": "neutral",
        "deadline_at": None,
        "period_name": None,
        "affected_classes": 0,
        "next_url": None,
        "helper": "No active unsubmitted period deadline is available for reminder display.",
    }

    if deadline_candidates:
        locked_reopened_candidates = [
            item
            for item in deadline_candidates
            if item["submission_status"] == GradeSubmission.Status.REOPENED and item["is_locked"]
        ]
        target_candidates = locked_reopened_candidates or deadline_candidates
        target_candidates.sort(key=lambda item: item["deadline_at"])
        current_deadline = target_candidates[0]
        matching_candidates = [
            item
            for item in deadline_candidates
            if item["deadline_at"] == current_deadline["deadline_at"]
            and item["period_code"] == current_deadline["period_code"]
            and (
                not locked_reopened_candidates
                or (
                    item["submission_status"] == GradeSubmission.Status.REOPENED
                    and item["is_locked"]
                )
            )
        ]
        affected_classes = len({item["offering_id"] for item in matching_candidates})
        is_overdue = current_deadline["deadline_at"] < now
        is_locked_reopened = (
            current_deadline["submission_status"] == GradeSubmission.Status.REOPENED
            and current_deadline["is_locked"]
        )
        reminder = {
            "has_deadline": True,
            "title": (
                "Reopened gradebook locked after deadline"
                if is_locked_reopened
                else "Grade submission deadline reminder"
            ),
            "note": (
                "This reopened gradebook was not resubmitted before the deadline. Score editing is disabled, but the faculty can still resubmit the gradebook from Summary."
                if is_locked_reopened
                else "This deadline already passed. You may continue encoding and submit as soon as possible. Late submission is recorded in the non-compliance monitor."
                if is_overdue
                else "Keep this deadline in view while encoding, checking summaries, and preparing final period submission."
            ),
            "variant": "danger" if is_overdue else "warning",
            "deadline_at": current_deadline["deadline_at"],
            "period_name": current_deadline["period_name"],
            "affected_classes": affected_classes,
            "next_url": reverse("faculty_portal:offering_periods", args=[current_deadline["offering_id"]]),
            "helper": (
                f"Next class to review: {current_deadline['course_code']} / {current_deadline['section_code']}"
            ),
        }

    if not deadline_candidates and offerings:
        offering_scopes = sorted(
            {
                (
                    getattr(offering.campus, "code", None),
                    getattr(offering.academic_year, "code", None),
                    getattr(offering.term, "code", None),
                )
                for offering in offerings
            }
        )
        tenant_ids = {offering.tenant_id for offering in offerings if offering.tenant_id}
        academic_year_ids = {offering.academic_year_id for offering in offerings if offering.academic_year_id}
        term_ids = {offering.term_id for offering in offerings if offering.term_id}
        configured_lock_rows = list(
            GradingPeriodLock.objects.filter(
                tenant_id__in=tenant_ids,
                academic_year_id__in=academic_year_ids,
                term_id__in=term_ids,
                is_active=True,
                deadline_at__isnull=False,
            )
            .select_related("campus", "academic_year", "term")
            .values_list("campus__code", "academic_year__code", "term__code", "period_code")
            .distinct()
        )
        configured_scope_rows = sorted({row[:3] for row in configured_lock_rows})
        matching_scope_rows = [row for row in configured_lock_rows if row[:3] in offering_scopes]
        if matching_scope_rows:
            configured_period_codes = sorted({row[3] for row in matching_scope_rows if row[3]})
            reminder = {
                "has_deadline": False,
                "title": "No matching period deadline for your active classes yet",
                "note": (
                    "A deadline exists for your campus, academic year, and term, but its period code does not "
                    "match the grading periods used by your accepted classes."
                ),
                "variant": "neutral",
                "deadline_at": None,
                "period_name": None,
                "affected_classes": 0,
                "next_url": None,
                "helper": (
                    f"Your active class scopes: {_format_deadline_scope_list(offering_scopes)}. "
                    f"Your class period codes: {_format_code_list(sorted(offering_period_codes))}. "
                    f"Configured deadline period codes in the same scope: {_format_code_list(configured_period_codes)}."
                ),
            }
        elif configured_scope_rows:
            reminder = {
                "has_deadline": False,
                "title": "No matching deadline for your active classes yet",
                "note": (
                    "A submission deadline exists in EduGradesPro, but it does not match the campus, academic year, "
                    "or term of your accepted classes."
                ),
                "variant": "neutral",
                "deadline_at": None,
                "period_name": None,
                "affected_classes": 0,
                "next_url": None,
                "helper": (
                    f"Your active class scopes: {_format_deadline_scope_list(offering_scopes)}. "
                    f"Configured deadline scopes found: {_format_deadline_scope_list(configured_scope_rows)}."
                ),
            }

    return reminder, periods_near_deadline


def _build_deadline_reminder_for_period_cards(offering, period_cards, *, now=None):
    now = now or timezone.now()
    candidates = [
        {
            "deadline_at": item["deadline_at"],
            "period_name": item["period"].name,
            "period_code": item["period"].code,
            "submission_status": item.get("submission_status"),
            "is_locked": item.get("is_locked"),
        }
        for item in period_cards
        if item.get("deadline_at") and item.get("submission_status") != GradeSubmission.Status.SUBMITTED
    ]
    reminder = {
        "has_deadline": False,
        "title": "No submission deadline is set yet for this class",
        "note": "Ask your admin to configure the period submission deadline for this class if you need a formal due-date reminder.",
        "variant": "neutral",
        "deadline_at": None,
        "period_name": None,
        "helper": f"{offering.course.code} | {offering.section.code}",
    }
    if candidates:
        locked_reopened_candidates = [
            item
            for item in candidates
            if item["submission_status"] == GradeSubmission.Status.REOPENED and item["is_locked"]
        ]
        target_candidates = locked_reopened_candidates or candidates
        target_candidates.sort(key=lambda item: item["deadline_at"])
        current_deadline = target_candidates[0]
        is_overdue = current_deadline["deadline_at"] < now
        is_locked_reopened = (
            current_deadline["submission_status"] == GradeSubmission.Status.REOPENED
            and current_deadline["is_locked"]
        )
        reminder = {
            "has_deadline": True,
            "title": (
                "Reopened gradebook locked after deadline"
                if is_locked_reopened
                else "Class period deadline reminder"
            ),
            "note": (
                "This reopened gradebook was not resubmitted before the deadline. Score editing is disabled, but the faculty can still resubmit the gradebook from Summary."
                if is_locked_reopened
                else "This class period deadline already passed. Continue encoding and submit as soon as the class period is complete. Late submission is recorded for non-compliance monitoring."
                if is_overdue
                else "Finish score encoding and summary review before this class period deadline."
            ),
            "variant": "danger" if is_overdue else "warning",
            "deadline_at": current_deadline["deadline_at"],
            "period_name": current_deadline["period_name"],
            "helper": f"{offering.course.code} | {offering.section.code}",
        }
    else:
        same_scope_period_codes = sorted(
            {
                period_code
                for period_code in GradingPeriodLock.objects.filter(
                    tenant_id=offering.tenant_id,
                    campus_id=offering.campus_id,
                    academic_year_id=offering.academic_year_id,
                    term_id=offering.term_id,
                    is_active=True,
                    deadline_at__isnull=False,
                )
                .values_list("period_code", flat=True)
                .distinct()
                if period_code
            }
        )
        if same_scope_period_codes:
            class_period_codes = sorted(
                {
                    item["period"].code
                    for item in period_cards
                    if getattr(item.get("period"), "code", None)
                }
            )
            reminder["title"] = "No matching period deadline is set for this class yet"
            reminder["note"] = (
                "A deadline exists for this campus and term, but its period code does not match this class template."
            )
            reminder["helper"] = (
                f"{offering.course.code} | {offering.section.code}. "
                f"Class period codes: {_format_code_list(class_period_codes)}. "
                f"Configured deadline period codes: {_format_code_list(same_scope_period_codes)}."
            )
            return reminder

        other_scope_deadlines = list(
            GradingPeriodLock.objects.filter(
                tenant_id=offering.tenant_id,
                academic_year_id=offering.academic_year_id,
                term_id=offering.term_id,
                is_active=True,
                deadline_at__isnull=False,
            )
            .exclude(campus_id=offering.campus_id)
            .select_related("campus")
            .values_list("campus__code", flat=True)
            .distinct()
        )
        if other_scope_deadlines:
            reminder["title"] = "No matching deadline is set for this class yet"
            reminder["note"] = (
                "A deadline exists for another campus scope in the same academic term, but not for this class."
            )
            reminder["helper"] = (
                f"{offering.course.code} | {offering.section.code}. "
                f"Other deadline scopes found: {', '.join(other_scope_deadlines)}."
            )
    return reminder


def _build_active_grading_period_rows(offerings, *, now=None):
    now = now or timezone.now()
    rows = []
    seen_scopes = set()
    for offering in offerings:
        scope_key = (offering.tenant_id, offering.campus_id, offering.term_id)
        if scope_key in seen_scopes:
            continue
        seen_scopes.add(scope_key)
        active_setting = AcademicGovernanceService.resolve_active_grading_period(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            term_id=offering.term_id,
            now=now,
        )
        if not active_setting:
            continue
        rows.append(
            {
                "campus_code": offering.campus.code,
                "campus_name": offering.campus.name,
                "term_code": offering.term.code,
                "term_name": offering.term.name,
                "period_code": active_setting.period.code,
                "period_name": active_setting.period.name,
                "auto_advanced_from_deadline": active_setting.auto_advanced_from_deadline,
            }
        )
    rows.sort(key=lambda row: (row["campus_code"], row["term_code"], row["period_code"]))
    return rows


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def reminder_center_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return redirect("faculty_portal:dashboard")

    if not FeatureSettingsService.is_faculty_reminder_center_enabled(tenant_id=tenant_id, default=True):
        messages.info(
            request,
            "Faculty reminder center is disabled by configuration. You can still use other faculty portal features.",
        )

    now = timezone.now()
    FacultyAssignmentWorkflowService.expire_overdue_assignments(tenant_id=tenant_id)
    offering_qs = _faculty_offering_queryset(request.user).filter(tenant_id=tenant_id).distinct()
    send_email_enabled = FeatureSettingsService.is_faculty_reminder_email_enabled(tenant_id=tenant_id, default=False)
    center_enabled = FeatureSettingsService.is_faculty_reminder_center_enabled(tenant_id=tenant_id, default=True)
    form = FacultyReminderForm(
        request.POST or None,
        offering_queryset=offering_qs,
        send_email_enabled=send_email_enabled,
    )

    if request.method == "POST" and not center_enabled:
        messages.error(request, "Faculty reminder center is disabled by configuration.")
        return redirect("faculty_portal:dashboard")

    if request.method == "POST" and form.is_valid():
        reminder = FacultyReminder.objects.create(
            tenant_id=tenant_id,
            campus_id=form.cleaned_data["offering"].campus_id,
            faculty_user=request.user,
            offering=form.cleaned_data["offering"],
            reminder_type=form.cleaned_data["reminder_type"],
            title=form.cleaned_data["title"],
            period_label=form.cleaned_data.get("period_label") or None,
            notes=form.cleaned_data.get("notes") or None,
            remind_at=form.cleaned_data["remind_at"],
            due_at=form.cleaned_data.get("due_at"),
            send_email=bool(form.cleaned_data.get("send_email")) and send_email_enabled,
            created_by=request.user,
            is_active=True,
        )
        AuditService.log_event(
            action="CREATE",
            portal="FACULTY",
            entity_type="FacultyReminder",
            entity_id=reminder.id,
            actor=request.user,
            tenant_id=tenant_id,
            campus=reminder.campus,
            after_data={
                "title": reminder.title,
                "reminder_type": reminder.reminder_type,
                "offering_id": reminder.offering_id,
                "remind_at": reminder.remind_at.isoformat(),
                "due_at": reminder.due_at.isoformat() if reminder.due_at else None,
                "send_email": reminder.send_email,
            },
            request=request,
        )
        messages.success(request, "Reminder saved.")
        return redirect("faculty_portal:reminder_center")

    reminders_qs = (
        FacultyReminder.objects.filter(tenant_id=tenant_id, faculty_user=request.user, is_active=True)
        .select_related("tenant", "campus", "offering", "offering__course", "offering__section")
        .order_by("completed_at", "snoozed_until", "remind_at", "-created_at")
    )

    reminders = []
    compliance_notices = []
    counts = {
        "upcoming": 0,
        "due_today": 0,
        "overdue": 0,
        "sent": 0,
        "completed": 0,
        "snoozed": 0,
    }
    for reminder in reminders_qs:
        status_label, status_variant = _faculty_reminder_status(reminder, now)
        if status_label == "Completed":
            counts["completed"] += 1
        elif status_label == "Snoozed":
            counts["snoozed"] += 1
        elif status_label == "Overdue":
            counts["overdue"] += 1
        elif status_label == "Due Today":
            counts["due_today"] += 1
        elif status_label == "Sent":
            counts["sent"] += 1
        else:
            counts["upcoming"] += 1
        reminders.append(
            {
                "obj": reminder,
                "status_label": status_label,
                "status_variant": status_variant,
                "is_due_today": bool(reminder.due_at and reminder.due_at.date() == now.date()),
                "is_overdue": bool(reminder.due_at and reminder.due_at < now and not reminder.completed_at),
            }
        )

    notice_qs = (
        SubmissionNonComplianceNotice.objects.filter(
            tenant_id=tenant_id,
            faculty_user=request.user,
        )
        .select_related("campus", "department", "offering", "offering__course", "offering__section", "template_period")
        .order_by("-issued_at", "-id")
    )
    for notice in notice_qs[:8]:
        if notice.status == SubmissionNonComplianceNotice.Status.RESOLVED:
            badge_variant = "success"
            status_label = "Resolved"
        elif notice.notice_level == SubmissionNonComplianceNotice.NoticeLevel.ESCALATION:
            badge_variant = "danger"
            status_label = "Escalated"
        elif notice.notice_level == SubmissionNonComplianceNotice.NoticeLevel.WARNING:
            badge_variant = "warning"
            status_label = "Warning"
        else:
            badge_variant = "info"
            status_label = "Notice"
        compliance_notices.append(
            {
                "obj": notice,
                "badge_variant": badge_variant,
                "status_label": status_label,
                "recipient_roles": ", ".join(notice.recipient_roles_json or []),
            }
        )

    context = {
        "form": form,
        "reminders": reminders,
        "compliance_notices": compliance_notices,
        "counts": counts,
        "send_email_enabled": send_email_enabled,
        "reminder_center_enabled": center_enabled,
    }
    return render(request, "faculty_portal/reminder_center.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def reminder_complete_view(request, reminder_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:reminder_center")
    reminder = _require_faculty_reminder_or_404(request, reminder_id)
    before = {
        "completed_at": reminder.completed_at.isoformat() if reminder.completed_at else None,
        "snoozed_until": reminder.snoozed_until.isoformat() if reminder.snoozed_until else None,
    }
    reminder.completed_at = timezone.now()
    reminder.snoozed_until = None
    reminder.save(update_fields=["completed_at", "snoozed_until", "updated_at"])
    AuditService.log_event(
        action="UPDATE",
        portal="FACULTY",
        entity_type="FacultyReminder",
        entity_id=reminder.id,
        actor=request.user,
        tenant=reminder.tenant,
        campus=reminder.campus,
        before_data=before,
        after_data={
            "completed_at": reminder.completed_at.isoformat() if reminder.completed_at else None,
        },
        request=request,
    )
    messages.success(request, "Reminder marked as completed.")
    return redirect("faculty_portal:reminder_center")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def reminder_snooze_view(request, reminder_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:reminder_center")
    reminder = _require_faculty_reminder_or_404(request, reminder_id)
    try:
        snooze_days = int(request.POST.get("snooze_days") or 1)
    except (TypeError, ValueError):
        snooze_days = 1
    snooze_days = max(snooze_days, 1)
    before = {
        "snoozed_until": reminder.snoozed_until.isoformat() if reminder.snoozed_until else None,
    }
    reminder.snoozed_until = timezone.now() + timedelta(days=snooze_days)
    reminder.save(update_fields=["snoozed_until", "updated_at"])
    AuditService.log_event(
        action="UPDATE",
        portal="FACULTY",
        entity_type="FacultyReminder",
        entity_id=reminder.id,
        actor=request.user,
        tenant=reminder.tenant,
        campus=reminder.campus,
        before_data=before,
        after_data={
            "snoozed_until": reminder.snoozed_until.isoformat() if reminder.snoozed_until else None,
        },
        request=request,
    )
    messages.success(request, f"Reminder snoozed for {snooze_days} day(s).")
    return redirect("faculty_portal:reminder_center")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def memo_center_view(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    campus_id = scope.get("campus_id") or getattr(request.user, "default_campus_id", None)
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return redirect("faculty_portal:dashboard")

    center_enabled = FeatureSettingsService.is_faculty_memo_center_enabled(tenant_id=tenant_id, default=True)
    if request.method == "POST" and not center_enabled:
        messages.error(request, "Faculty memo center is disabled by configuration.")
        return redirect("faculty_portal:dashboard")

    offering_qs = _faculty_offering_queryset(request.user).filter(tenant_id=tenant_id).distinct()
    student_qs = Student.objects.filter(
        id__in=Enrollment.objects.filter(course_offering__in=offering_qs, is_active=True)
        .values_list("student_id", flat=True)
        .distinct()
    ).select_related("tenant", "campus", "program").order_by("last_name", "first_name", "student_no")

    form = FacultyMemoForm(
        request.POST or None,
        offering_queryset=offering_qs,
        student_queryset=student_qs,
    )

    if request.method == "POST" and form.is_valid():
        memo = FacultyMemo.objects.create(
            tenant_id=tenant_id,
            campus_id=form.cleaned_data["offering"].campus_id if form.cleaned_data.get("offering") else campus_id,
            faculty_user=request.user,
            offering=form.cleaned_data.get("offering"),
            student=form.cleaned_data.get("student"),
            memo_type=form.cleaned_data["memo_type"],
            title=form.cleaned_data["title"],
            body=form.cleaned_data["body"],
            is_pinned=bool(form.cleaned_data.get("is_pinned")),
            created_by=request.user,
            is_active=True,
        )
        AuditService.log_event(
            action="CREATE",
            portal="FACULTY",
            entity_type="FacultyMemo",
            entity_id=memo.id,
            actor=request.user,
            tenant=tenant_id,
            campus=memo.campus,
            after_data={
                "title": memo.title,
                "memo_type": memo.memo_type,
                "offering_id": memo.offering_id,
                "student_id": memo.student_id,
                "is_pinned": memo.is_pinned,
            },
            request=request,
        )
        messages.success(request, "Memo saved.")
        return redirect("faculty_portal:memo_center")

    memos_qs = _faculty_memo_queryset(request.user).filter(tenant_id=tenant_id)
    q = (request.GET.get("q") or "").strip()
    pinned_only = request.GET.get("pinned") == "1"
    if q:
        memos_qs = memos_qs.filter(
            Q(title__icontains=q)
            | Q(body__icontains=q)
            | Q(offering__course__title__icontains=q)
            | Q(offering__course__code__icontains=q)
            | Q(offering__section__code__icontains=q)
            | Q(student__student_no__icontains=q)
            | Q(student__last_name__icontains=q)
            | Q(student__first_name__icontains=q)
        )
    if pinned_only:
        memos_qs = memos_qs.filter(is_pinned=True)

    memos_qs = memos_qs.select_related("offering__course", "offering__section", "student", "student__program")
    memos_qs = memos_qs.order_by("-is_pinned", "-updated_at", "-created_at")
    memos = []
    counts = {"total": 0, "pinned": 0, "class": 0, "student": 0, "general": 0}
    for memo in memos_qs:
        counts["total"] += 1
        if memo.is_pinned:
            counts["pinned"] += 1
        if memo.memo_type == FacultyMemo.MemoType.CLASS:
            counts["class"] += 1
        elif memo.memo_type == FacultyMemo.MemoType.STUDENT:
            counts["student"] += 1
        else:
            counts["general"] += 1
        memo_kind = {
            FacultyMemo.MemoType.GENERAL: ("General", "primary"),
            FacultyMemo.MemoType.CLASS: ("Class Memo", "success"),
            FacultyMemo.MemoType.STUDENT: ("Student Memo", "warning"),
            FacultyMemo.MemoType.CUSTOM: ("Custom", "secondary"),
        }.get(memo.memo_type, ("Memo", "primary"))
        memos.append(
            {
                "obj": memo,
                "kind_label": memo_kind[0],
                "kind_variant": memo_kind[1],
            }
        )

    context = {
        "form": form,
        "memos": memos,
        "counts": counts,
        "memo_center_enabled": center_enabled,
        "q": q,
        "pinned_only": pinned_only,
    }
    return render(request, "faculty_portal/memo_center.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def memo_edit_view(request, memo_id: int):
    memo = _require_faculty_memo_or_404(request, memo_id)
    tenant_id = memo.tenant_id
    center_enabled = FeatureSettingsService.is_faculty_memo_center_enabled(tenant_id=tenant_id, default=True)
    if request.method == "POST" and not center_enabled:
        messages.error(request, "Faculty memo center is disabled by configuration.")
        return redirect("faculty_portal:memo_center")

    offering_qs = _faculty_offering_queryset(request.user).filter(tenant_id=tenant_id).distinct()
    student_qs = Student.objects.filter(
        id__in=Enrollment.objects.filter(course_offering__in=offering_qs, is_active=True)
        .values_list("student_id", flat=True)
        .distinct()
    ).select_related("tenant", "campus", "program").order_by("last_name", "first_name", "student_no")
    form = FacultyMemoForm(
        request.POST or None,
        instance=memo,
        offering_queryset=offering_qs,
        student_queryset=student_qs,
    )
    if request.method == "POST" and form.is_valid():
        updated = form.save(commit=False)
        updated.tenant_id = tenant_id
        updated.campus_id = form.cleaned_data["offering"].campus_id if form.cleaned_data.get("offering") else memo.campus_id
        updated.faculty_user = request.user
        updated.created_by = memo.created_by or request.user
        updated.save()
        AuditService.log_event(
            action="UPDATE",
            portal="FACULTY",
            entity_type="FacultyMemo",
            entity_id=memo.id,
            actor=request.user,
            tenant=tenant_id,
            campus=updated.campus,
            before_data={
                "title": memo.title,
                "memo_type": memo.memo_type,
                "offering_id": memo.offering_id,
                "student_id": memo.student_id,
                "is_pinned": memo.is_pinned,
                "body": memo.body,
            },
            after_data={
                "title": updated.title,
                "memo_type": updated.memo_type,
                "offering_id": updated.offering_id,
                "student_id": updated.student_id,
                "is_pinned": updated.is_pinned,
                "body": updated.body,
            },
            request=request,
        )
        messages.success(request, "Memo updated.")
        return redirect("faculty_portal:memo_center")

    context = {
        "form": form,
        "memo": memo,
        "memo_center_enabled": center_enabled,
        "is_edit": True,
    }
    return render(request, "faculty_portal/memo_form.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def memo_toggle_pin_view(request, memo_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:memo_center")
    memo = _require_faculty_memo_or_404(request, memo_id)
    before = {"is_pinned": memo.is_pinned}
    memo.is_pinned = not memo.is_pinned
    memo.save(update_fields=["is_pinned", "updated_at"])
    AuditService.log_event(
        action="UPDATE",
        portal="FACULTY",
        entity_type="FacultyMemo",
        entity_id=memo.id,
        actor=request.user,
        tenant=memo.tenant,
        campus=memo.campus,
        before_data=before,
        after_data={"is_pinned": memo.is_pinned},
        request=request,
    )
    messages.success(request, "Memo pin updated.")
    return redirect("faculty_portal:memo_center")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def memo_delete_view(request, memo_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:memo_center")
    memo = _require_faculty_memo_or_404(request, memo_id)
    before = {"is_active": memo.is_active}
    memo.is_active = False
    memo.save(update_fields=["is_active", "updated_at"])
    AuditService.log_event(
        action="DELETE",
        portal="FACULTY",
        entity_type="FacultyMemo",
        entity_id=memo.id,
        actor=request.user,
        tenant=memo.tenant,
        campus=memo.campus,
        before_data=before,
        after_data={"is_active": memo.is_active},
        request=request,
    )
    messages.success(request, "Memo removed.")
    return redirect("faculty_portal:memo_center")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def student_at_risk_monitor_view(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    campus_id = scope.get("campus_id") or getattr(request.user, "default_campus_id", None)
    if not FeatureSettingsService.can_user_access_grade_prediction(user=request.user, tenant_id=tenant_id):
        messages.error(request, "Grade prediction is currently disabled for your role.")
        return redirect("faculty_portal:my_courses")
    if not FeatureSettingsService.is_grade_prediction_at_risk_enabled(tenant_id=tenant_id, default=True):
        messages.error(request, "Student at-risk monitoring is currently disabled by configuration.")
        return redirect("faculty_portal:my_courses")

    show_archived = request.GET.get("archived") == "1"
    selected_offering_id = _safe_int(request.GET.get("offering_id"))
    q = (request.GET.get("q") or "").strip()

    assignment_qs = _faculty_assignment_queryset(request.user).filter(accepted_at__isnull=False)
    if tenant_id:
        assignment_qs = assignment_qs.filter(tenant_id=tenant_id)
    if campus_id:
        assignment_qs = assignment_qs.filter(campus_id=campus_id)

    active_term_cache = {}

    def _is_in_active_scope(offering):
        tenant_key = offering.tenant_id
        if tenant_key not in active_term_cache:
            _, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_key)
            active_term_cache[tenant_key] = active_term.id if active_term else None
        active_term_id = active_term_cache[tenant_key]
        if not active_term_id:
            return True
        return offering.term_id == active_term_id

    monitored_offerings = []
    for assignment in assignment_qs:
        offering = assignment.offering
        if selected_offering_id and offering.id != selected_offering_id:
            continue
        forced_archive = offering.status == CourseOffering.Status.ARCHIVED
        outside_active_scope = not _is_in_active_scope(offering)
        if not show_archived and (forced_archive or outside_active_scope):
            continue
        offering.assignment = assignment
        monitored_offerings.append(offering)

    monitor_groups = []
    at_risk_student_count = 0
    at_risk_group_count = 0
    coverage_values = []
    projection_values = []
    active_period_setting_cache = {}
    using_active_period_filter = False

    for offering in monitored_offerings:
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
        except ValidationError:
            continue
        passing_threshold = FacultyGradingService.resolve_passing_threshold(offering)
        active_cache_key = (offering.tenant_id, offering.campus_id, offering.term_id)
        if active_cache_key not in active_period_setting_cache:
            active_period_setting_cache[active_cache_key] = AcademicGovernanceService.resolve_active_grading_period(
                tenant_id=offering.tenant_id,
                campus_id=offering.campus_id,
                term_id=offering.term_id,
            )
        active_period_setting = active_period_setting_cache[active_cache_key]
        periods = list(template.periods.filter(is_active=True).order_by("sequence_no", "id"))
        if active_period_setting:
            using_active_period_filter = True
            periods = [
                period
                for period in periods
                if AcademicGovernanceService.template_period_matches_active_period(
                    template_period=period,
                    active_period_setting=active_period_setting,
                )
            ]
        for period in periods:
            prediction_data = PredictionSnapshotService.get_period_predictions(
                offering=offering,
                template_period=period,
                user=request.user,
            )
            group_rows = []
            for row in prediction_data["rows"]:
                if not getattr(row, "at_risk_flag", False):
                    continue
                if q:
                    student_no = getattr(row.student, "student_no", "") or ""
                    student_name = " ".join(
                        part
                        for part in [
                            getattr(row.student, "last_name", ""),
                            getattr(row.student, "first_name", ""),
                            getattr(row.student, "middle_name", ""),
                        ]
                        if part
                    ).strip()
                    if (
                        q.lower() not in student_no.lower()
                        and q.lower() not in student_name.lower()
                        and q.lower() not in offering.course.code.lower()
                        and q.lower() not in offering.course.title.lower()
                        and q.lower() not in offering.section.code.lower()
                        and q.lower() not in period.name.lower()
                    ):
                        continue

                period_gap = None
                if row.current_projected_period_grade is not None:
                    period_gap = max(
                        Decimal(passing_threshold) - Decimal(row.current_projected_period_grade),
                        Decimal("0"),
                    )
                final_gap = None
                if row.current_projected_final_grade is not None:
                    final_gap = max(
                        Decimal(passing_threshold) - Decimal(row.current_projected_final_grade),
                        Decimal("0"),
                    )
                if period_gap is None:
                    risk_reason = "Not enough encoded scores yet"
                    suggested_action = "Open the period prediction page and check missing score coverage."
                    risk_variant = "warning"
                elif final_gap and final_gap > Decimal("0"):
                    risk_reason = "Possible final grade is below passing"
                    suggested_action = "Review earlier period grades and use the prediction page for final-grade recovery planning."
                    risk_variant = "danger"
                elif period_gap >= Decimal("5"):
                    risk_reason = "Current period projection is far below passing"
                    suggested_action = "Prioritize follow-up, missing work, and remediation for this period."
                    risk_variant = "danger"
                elif int(row.remaining_item_count or 0) > 0:
                    risk_reason = "Current period projection is below passing"
                    suggested_action = "Review remaining activities and guide the student before period submission."
                    risk_variant = "warning"
                else:
                    risk_reason = "Current period grade is below passing"
                    suggested_action = "Review the official period summary and determine the appropriate intervention."
                    risk_variant = "danger"
                group_rows.append(
                    {
                        "student": row.student,
                        "current_projected_period_grade": row.current_projected_period_grade,
                        "current_projected_period_grade_display": _format_decimal_display(
                            row.current_projected_period_grade
                        ),
                        "best_case_period_grade": row.best_case_period_grade,
                        "best_case_period_grade_display": _format_decimal_display(row.best_case_period_grade),
                        "worst_case_period_grade": row.worst_case_period_grade,
                        "worst_case_period_grade_display": _format_decimal_display(row.worst_case_period_grade),
                        "coverage_percent": row.coverage_percent,
                        "coverage_percent_display": _format_decimal_display(row.coverage_percent),
                        "remaining_item_count": row.remaining_item_count,
                        "period_gap_display": _format_decimal_display(period_gap),
                        "risk_reason": risk_reason,
                        "suggested_action": suggested_action,
                        "risk_variant": risk_variant,
                    }
                )
                at_risk_student_count += 1
                coverage_values.append(Decimal(row.coverage_percent))
                if row.current_projected_period_grade is not None:
                    projection_values.append(Decimal(row.current_projected_period_grade))

            if group_rows:
                at_risk_group_count += 1
                monitor_groups.append(
                    {
                        "offering": offering,
                        "period": period,
                        "rows": group_rows,
                        "at_risk_count": len(group_rows),
                        "avg_coverage": _format_decimal_display(
                            sum(coverage_values[-len(group_rows) :]) / Decimal(len(group_rows))
                            if len(group_rows) else None
                        ),
                        "current_template_name": template.name,
                    }
                )

    class_count = len(monitored_offerings)
    avg_coverage = _format_decimal_display(
        sum(coverage_values) / Decimal(len(coverage_values)) if coverage_values else None
    )
    avg_projection = _format_decimal_display(
        sum(projection_values) / Decimal(len(projection_values)) if projection_values else None
    )

    offering_choices = [
        {
            "id": offering.id,
            "label": f"{offering.course.title} ({offering.course.code}) | {offering.section.name or offering.section.code}",
            "selected": offering.id == selected_offering_id,
        }
        for offering in monitored_offerings
    ]
    if not selected_offering_id:
        offering_choices.insert(0, {"id": "", "label": "All Classes", "selected": True})

    context = {
        "monitor_groups": monitor_groups,
        "offering_choices": offering_choices,
        "selected_offering_id": selected_offering_id,
        "show_archived": show_archived,
        "q": q,
        "summary_cards": [
            {"label": "Classes Monitored", "value": class_count, "meta": "Accepted classes in the current scope."},
            {"label": "At-Risk Groups", "value": at_risk_group_count, "meta": "Current-period class groups with risk."},
            {"label": "At-Risk Students", "value": at_risk_student_count, "meta": "Students flagged below the pass line."},
            {"label": "Average Coverage", "value": avg_coverage, "meta": "Average score coverage across at-risk rows."},
            {"label": "Average Projection", "value": avg_projection, "meta": "Average projected period grade."},
        ],
        "at_risk_enabled": True,
        "using_active_period_filter": using_active_period_filter,
    }
    return render(request, "faculty_portal/student_at_risk_monitor.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def my_courses_view(request):
    FacultyAssignmentWorkflowService.expire_overdue_assignments()
    show_archived = request.GET.get("archived") == "1"
    assignment_qs = (
        _faculty_assignment_queryset(request.user)
        .annotate(
            enrollment_count=Count(
                "offering__enrollments",
                filter=Q(
                    offering__enrollments__is_active=True,
                    offering__enrollments__enrollment_status=Enrollment.Status.ACTIVE,
                ),
                distinct=True,
            )
        )
        .distinct()
        .order_by(
            "offering__tenant__code",
            "offering__campus__code",
            "offering__term__sequence_no",
            "offering__course__code",
            "offering__section__code",
        )
    )

    active_term_cache = {}

    def _is_in_active_scope(offering):
        tenant_id = offering.tenant_id
        if tenant_id not in active_term_cache:
            _, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
            active_term_cache[tenant_id] = active_term.id if active_term else None
        active_term_id = active_term_cache[tenant_id]
        if not active_term_id:
            return True
        return offering.term_id == active_term_id

    pending_assignments = []
    active_offerings = []
    archived_offerings = []
    for assignment in assignment_qs:
        offering = assignment.offering
        offering.assignment = assignment
        offering.enrollment_count = assignment.enrollment_count
        try:
            resolved_template = FacultyGradingService.resolve_template_for_offering(offering)
        except ValidationError:
            resolved_template = None
        offering.resolved_template = resolved_template
        offering.final_template_period = (
            resolved_template.periods.filter(is_active=True).order_by("-sequence_no", "-id").first()
            if resolved_template is not None
            else None
        )
        forced_archive = offering.status == CourseOffering.Status.ARCHIVED
        outside_active_scope = not _is_in_active_scope(offering)
        if not assignment.is_accepted and not forced_archive and not outside_active_scope:
            pending_assignments.append(assignment)
        elif forced_archive or outside_active_scope:
            archived_offerings.append(offering)
        else:
            active_offerings.append(offering)

    selected_offerings = archived_offerings if show_archived else active_offerings

    grouped_offerings = []
    final_clearance_targets = []
    final_clearance_seen = set()
    for offering in selected_offerings:
        if (
            not grouped_offerings
            or grouped_offerings[-1]["tenant_id"] != offering.tenant_id
            or grouped_offerings[-1]["campus_id"] != offering.campus_id
        ):
            grouped_offerings.append(
                {
                    "tenant_id": offering.tenant_id,
                    "campus_id": offering.campus_id,
                    "tenant": offering.tenant,
                    "campus": offering.campus,
                    "offerings": [],
                }
            )
        grouped_offerings[-1]["offerings"].append(offering)
        if not show_archived and offering.final_template_period:
            key = (offering.tenant_id, offering.campus_id, offering.term_id)
            if key not in final_clearance_seen:
                final_clearance_seen.add(key)
                final_clearance_targets.append(
                    {
                        "tenant": offering.tenant,
                        "campus": offering.campus,
                        "term": offering.term,
                        "academic_year": offering.academic_year,
                        "offering_id": offering.id,
                        "period_id": offering.final_template_period.id,
                    }
                )

    deadline_banner, _ = _build_deadline_reminder_for_offerings(active_offerings, now=timezone.now())
    active_grading_period_rows = _build_active_grading_period_rows(active_offerings, now=timezone.now())

    context = {
        "grouped_offerings": grouped_offerings,
        "pending_assignments": pending_assignments if not show_archived else [],
        "show_archived": show_archived,
        "active_count": len(active_offerings),
        "archived_count": len(archived_offerings),
        "pending_count": len(pending_assignments),
        "now": timezone.now(),
        "deadline_banner": deadline_banner if not show_archived else None,
        "active_grading_period_rows": active_grading_period_rows if not show_archived else [],
        "final_clearance_targets": final_clearance_targets if not show_archived else [],
    }
    return render(request, "faculty_portal/my_courses.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def faculty_assignment_accept_view(request, assignment_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:my_courses")

    FacultyAssignmentWorkflowService.expire_overdue_assignments()
    assignment = _require_pending_faculty_assignment_or_404(request, assignment_id)
    if assignment.response_status == FacultyAssignment.ResponseStatus.EXPIRED:
        messages.error(
            request,
            "This assignment response window already expired. Please ask admin to refresh the assignment window.",
        )
        return redirect("faculty_portal:my_courses")
    _apply_assignment_response(
        request=request,
        assignment=assignment,
        response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        success_message=f"Assignment accepted for {assignment.offering.course.code} / {assignment.offering.section.code}.",
    )
    return redirect("faculty_portal:my_courses")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def faculty_assignment_response_view(request, assignment_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:my_courses")

    FacultyAssignmentWorkflowService.expire_overdue_assignments()
    assignment = _require_pending_faculty_assignment_or_404(request, assignment_id)
    if assignment.response_status == FacultyAssignment.ResponseStatus.EXPIRED:
        messages.error(
            request,
            "This assignment response window already expired. Please ask admin to refresh the assignment window.",
        )
        return redirect("faculty_portal:my_courses")
    response_action = (request.POST.get("response_action") or "").strip().lower()
    faculty_note = (request.POST.get("faculty_response_note") or "").strip()

    if response_action == "clarification":
        if not faculty_note:
            messages.error(request, "Please enter your clarification request before sending it.")
            return redirect("faculty_portal:my_courses")
        _apply_assignment_response(
            request=request,
            assignment=assignment,
            response_status=FacultyAssignment.ResponseStatus.CLARIFICATION_REQUESTED,
            faculty_note=faculty_note,
            success_message="Clarification request sent to admin.",
        )
    elif response_action == "decline":
        if not faculty_note:
            messages.error(request, "Please explain why you are declining this assignment.")
            return redirect("faculty_portal:my_courses")
        _apply_assignment_response(
            request=request,
            assignment=assignment,
            response_status=FacultyAssignment.ResponseStatus.DECLINED,
            faculty_note=faculty_note,
            success_message="Assignment marked as declined and sent back to admin.",
        )
    else:
        messages.error(request, "Invalid assignment response.")
        return redirect("faculty_portal:my_courses")

    return redirect("faculty_portal:my_courses")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def offering_periods_view(request, offering_id: int):
    assignment = _find_faculty_assignment(request.user, offering_id)
    if assignment and not assignment.is_accepted:
        messages.error(request, "Please accept this faculty assignment first before opening the class.")
        return redirect("faculty_portal:my_courses")
    offering = _require_faculty_offering_or_404(request, offering_id)
    try:
        template = FacultyGradingService.resolve_template_for_offering(offering)
        periods = list(FacultyGradingService.get_template_periods(template))
    except ValidationError as exc:
        template = None
        periods = []
        messages.error(request, str(exc))

    active_grading_period = AcademicGovernanceService.resolve_active_grading_period(
        tenant_id=offering.tenant_id,
        campus_id=offering.campus_id,
        term_id=offering.term_id,
        now=timezone.now(),
    )
    period_cards = []
    for p in periods:
        GradingGovernanceService.auto_lock_expired_reopened_gradebook(offering=offering, template_period=p)
        lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=p)
        submission = GradingGovernanceService.get_submission(offering=offering, template_period=p)
        completion_window_state = GradingGovernanceService.get_completion_window_state(
            offering=offering,
            template_period=p,
        )
        governance_state = _resolve_faculty_period_governance_state(
            offering,
            p,
            active_grading_period=active_grading_period,
            submission=submission,
            completion_window_state=completion_window_state,
        )
        can_access_corrections = bool(
            submission
            and submission.status in {GradeSubmission.Status.SUBMITTED, GradeSubmission.Status.REOPENED}
        )
        period_cards.append(
            {
                "period": p,
                "is_locked": bool(lock and lock.is_locked),
                "is_submitted": GradingGovernanceService.is_submitted(offering=offering, template_period=p),
                "submission_status": submission.status if submission else None,
                "is_correction_active": GradingGovernanceService.has_active_unlock_window(
                    offering=offering,
                    template_period=p,
                ),
                "deadline_at": lock.deadline_at if lock else None,
                "completion_grace_until": completion_window_state["completion_grace_until"],
                "is_within_completion_grace": completion_window_state["is_within_completion_grace"],
                "is_non_compliant": completion_window_state["is_non_compliant"],
                "pending_late_completion_request": completion_window_state["pending_late_completion_request"],
                "active_late_completion_request": completion_window_state["active_late_completion_request"],
                "can_request_late_completion": completion_window_state["can_request_late_completion"],
                "is_active_period": AcademicGovernanceService.template_period_matches_active_period(
                    template_period=p,
                    active_period_setting=active_grading_period,
                ),
                "is_closed_by_active_period": governance_state["is_closed_by_active_period"],
                  "is_future_period": governance_state["is_future_period"],
                  "is_past_period": governance_state["is_past_period"],
                  "closed_message": governance_state["message"],
                  "can_access_corrections": can_access_corrections,
                  "is_final_period": bool(periods) and p.id == periods[-1].id,
              }
          )

    context = {
        "offering": offering,
        "template": template,
        "periods": periods,
        "period_cards": period_cards,
        "enrollment_count": FacultyGradingService.get_active_enrollments(offering).count(),
        "system_correction_enabled": GradingGovernanceService.is_system_correction_enabled(
            tenant_id=offering.tenant_id
        ),
        "grade_prediction_enabled": FeatureSettingsService.can_user_access_grade_prediction(
            user=request.user,
            tenant_id=offering.tenant_id,
        ),
        "active_grading_period": active_grading_period,
        "deadline_banner": _build_deadline_reminder_for_period_cards(
            offering,
            period_cards,
            now=timezone.now(),
        ),
    }
    return render(request, "faculty_portal/offering_periods.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def offering_grading_template_view(request, offering_id: int):
    assignment = _find_faculty_assignment(request.user, offering_id)
    if assignment and not assignment.is_accepted:
        messages.error(request, "Please accept this faculty assignment first before opening the class.")
        return redirect("faculty_portal:my_courses")
    offering = _require_faculty_offering_or_404(request, offering_id)
    try:
        resolved_template = FacultyGradingService.resolve_template_for_offering(offering)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    template = _load_template_preview(resolved_template.id)
    if not template:
        messages.error(request, "The grading template for this class could not be loaded.")
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    preview = _build_faculty_template_preview(template)
    context = {
        "offering": offering,
        "template": template,
        "period_rows": preview["period_rows"],
        "final_formula": preview["final_formula"],
    }
    return render(request, "faculty_portal/offering_grading_template.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_view_history_view(request, offering_id: int, period_id: int):
    assignment = _find_faculty_assignment(request.user, offering_id)
    if assignment and not assignment.is_accepted:
        messages.error(request, "Please accept this faculty assignment first before opening the class.")
        return redirect("faculty_portal:my_courses")

    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    view_logs = []
    log_qs = (
        AuditLog.objects.filter(
            entity_type="FacultyGradebookMonitor",
            metadata_json__faculty_user_id=request.user.id,
            metadata_json__offering_id=offering.id,
            metadata_json__period_id=period.id,
        )
        .select_related("actor_user")
        .order_by("-created_at")
    )
    for log in log_qs:
        actor = log.actor_user
        role_labels = []
        if actor is not None:
            role_labels = list(
                actor.user_roles.filter(is_active=True, role__is_active=True)
                .values_list("role__name", flat=True)
                .distinct()
            )
        full_name = actor.full_name.strip() if actor and getattr(actor, "full_name", "").strip() else ""
        view_logs.append(
            {
                "log": log,
                "actor_name": full_name or (actor.username if actor else "Unknown User"),
                "actor_username": actor.username if actor else "",
                "actor_roles": ", ".join(role_labels) if role_labels else "Role not available",
                "masked_student_identity": bool((log.metadata_json or {}).get("masked_student_identity")),
            }
        )

    reopen_logs = list(
        AuditLog.objects.filter(
            entity_type="GradeSubmission",
            action="REOPEN",
            metadata_json__offering_id=offering.id,
            metadata_json__period_id=period.id,
        )
        .select_related("actor_user")
        .order_by("-created_at")
    )

    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "view_logs": view_logs,
        "reopen_logs": reopen_logs,
    }
    return render(request, "faculty_portal/period_view_history.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_final_clearance_view(request, offering_id: int, period_id: int):
    assignment = _find_faculty_assignment(request.user, offering_id)
    if assignment and not assignment.is_accepted:
        messages.error(request, "Please accept this faculty assignment first before opening the class.")
        return redirect("faculty_portal:my_courses")

    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    template_periods = list(FacultyGradingService.get_template_periods(template))
    if not template_periods or period.id != template_periods[-1].id:
        messages.error(request, "Final clearance is available only from the final grading period card.")
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    preview = FacultyFinalClearanceReportService.evaluate_faculty_clearance(
        faculty_user=request.user,
        term=offering.term,
        campus=offering.campus,
    )

    if request.method == "POST":
        report_obj = FacultyFinalClearanceReportService.generate_report_record(
            faculty_user=request.user,
            term=offering.term,
            campus=offering.campus,
            generated_by_user=request.user,
        )
        AuditService.log_event(
            action="GENERATE",
            portal="FACULTY",
            entity_type="FacultyFinalClearanceReport",
            entity_id=report_obj.id,
            actor=request.user,
            tenant=report_obj.tenant,
            campus=report_obj.campus,
            after_data={
                "reference_no": report_obj.reference_no,
                "verification_code": report_obj.verification_code,
                "clearance_status": report_obj.clearance_status,
                "faculty_user": report_obj.faculty_user.full_name or report_obj.faculty_user.username,
                "term": report_obj.term.code,
            },
            request=request,
        )
        filename = _faculty_final_clearance_report_filename(report_obj)
        pdf_bytes = FacultyFinalClearanceReportService.build_pdf_bytes(report_obj=report_obj)
        AuditService.log_event(
            action="DOWNLOAD",
            portal="FACULTY",
            entity_type="FacultyFinalClearanceReport",
            entity_id=report_obj.id,
            actor=request.user,
            tenant=report_obj.tenant,
            campus=report_obj.campus,
            metadata={
                "reference_no": report_obj.reference_no,
                "filename": filename,
                "content_type": "application/pdf",
            },
            request=request,
        )
        return FileResponse(
            BytesIO(pdf_bytes),
            as_attachment=False,
            filename=filename,
            content_type="application/pdf",
        )

    recent_reports = (
        FacultyFinalClearanceReport.objects.filter(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            term_id=offering.term_id,
            faculty_user=request.user,
        )
        .select_related("term", "academic_year")
        .order_by("-created_at")[:5]
    )

    context = {
        "offering": offering,
        "period": period,
        "preview": preview,
        "recent_reports": recent_reports,
    }
    return render(request, "faculty_portal/period_final_clearance.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_activities_view(request, offering_id: int, period_id: int, activity_id: int | None = None):
    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    state = _period_edit_state(offering, period)

    component_qs = period.components.filter(is_active=True).order_by("sort_order", "name")
    subcomponent_qs = GradingTemplateSubcomponent.objects.filter(
        template_component__template_period_id=period.id,
        is_active=True,
    ).select_related("template_component")
    detail_qs = GradingTemplateDetail.objects.filter(
        template_subcomponent__template_component__template_period_id=period.id,
        is_active=True,
    ).select_related("template_subcomponent", "template_subcomponent__template_component")
    editing_activity = None
    if activity_id is not None:
        editing_activity = get_object_or_404(
            GradeActivity.objects.select_related("template_component", "template_subcomponent", "template_detail"),
            id=activity_id,
            offering_id=offering.id,
            template_period_id=period.id,
            is_active=True,
        )
    form = GradeActivityForm(
        request.POST or None,
        instance=editing_activity,
        component_queryset=component_qs,
        subcomponent_queryset=subcomponent_qs,
        detail_queryset=detail_qs,
    )
    _style_form(form)

    selected_component_id = None
    selected_subcomponent_id = None
    selected_detail_id = None
    if form.is_bound:
        selected_component_id = form.data.get(form.add_prefix("template_component")) or None
        selected_subcomponent_id = form.data.get(form.add_prefix("template_subcomponent")) or None
        selected_detail_id = form.data.get(form.add_prefix("template_detail")) or None
    elif editing_activity is not None:
        selected_component_id = str(editing_activity.template_component_id)
        selected_subcomponent_id = (
            str(editing_activity.template_subcomponent_id) if editing_activity.template_subcomponent_id else None
        )
        selected_detail_id = str(editing_activity.template_detail_id) if editing_activity.template_detail_id else None

    subcomponents = list(subcomponent_qs)
    details = list(detail_qs)
    component_ids_with_subcomponents = {subcomponent.template_component_id for subcomponent in subcomponents}
    subcomponent_ids_with_details = {detail.template_subcomponent_id for detail in details}
    component_option_data = [
        {
            "id": str(component.id),
            "name": component.name,
            "has_subcomponents": component.id in component_ids_with_subcomponents,
        }
        for component in component_qs
    ]
    subcomponent_option_data = [
        {
            "id": str(subcomponent.id),
            "name": subcomponent.name,
            "component_id": str(subcomponent.template_component_id),
            "has_details": subcomponent.id in subcomponent_ids_with_details,
        }
        for subcomponent in subcomponents
    ]
    detail_option_data = [
        {
            "id": str(detail.id),
            "name": detail.name,
            "subcomponent_id": str(detail.template_subcomponent_id),
        }
        for detail in details
    ]

    if request.method == "POST" and form.is_valid():
        if not state["is_editable"]:
            messages.error(request, "This period is locked or already submitted.")
            return redirect("faculty_portal:period_activities", offering_id=offering.id, period_id=period.id)
        if state["is_correction_active"] and editing_activity is None:
            messages.error(request, "New activities cannot be created inside a correction window.")
            return redirect("faculty_portal:period_activities", offering_id=offering.id, period_id=period.id)
        component = form.cleaned_data["template_component"]
        subcomponent = form.cleaned_data["template_subcomponent"]
        detail = form.cleaned_data["template_detail"]
        score_input_mode = FacultyGradingService.resolve_score_input_mode(
            template_component=component,
            template_subcomponent=subcomponent,
            template_detail=detail,
        )
        if subcomponent and subcomponent.template_component_id != component.id:
            form.add_error("template_subcomponent", "Subcomponent does not match selected component.")
        elif detail and (not subcomponent or detail.template_subcomponent_id != subcomponent.id):
            form.add_error("template_detail", "Detail does not match selected subcomponent.")
        else:
            try:
                if editing_activity is not None:
                    before = _activity_before_data(editing_activity)
                    activity, recomputed_score_count = FacultyGradingService.update_activity(
                        user=request.user,
                        activity=editing_activity,
                        template_period=period,
                        template_component=component,
                        template_subcomponent=subcomponent,
                        template_detail=detail,
                        title=form.cleaned_data["title"],
                        total_score=form.cleaned_data["total_score"],
                        activity_date=form.cleaned_data["activity_date"],
                    )
                else:
                    activity = FacultyGradingService.create_activity(
                        user=request.user,
                        offering=offering,
                        template_period=period,
                        template_component=component,
                        template_subcomponent=subcomponent,
                        template_detail=detail,
                        title=form.cleaned_data["title"],
                        total_score=form.cleaned_data["total_score"],
                        activity_date=form.cleaned_data["activity_date"],
                    )
            except ValidationError as exc:
                form.add_error(None, str(exc))
            else:
                FacultyReminderService.sync_activity_reminder(
                    activity=activity,
                    faculty_user=request.user,
                    created_by=request.user,
                )
                after_data = {
                    "offering_id": offering.id,
                    "period_id": period.id,
                    "component_id": component.id,
                    "subcomponent_id": subcomponent.id if subcomponent else None,
                    "detail_id": detail.id if detail else None,
                    "title": activity.title,
                    "total_score": str(activity.total_score),
                    "score_input_mode": score_input_mode,
                    "activity_date": str(activity.activity_date) if activity.activity_date else None,
                }
                if editing_activity is not None:
                    AuditService.log_event(
                        action="UPDATE",
                        portal="FACULTY",
                        entity_type="GradeActivity",
                        entity_id=activity.id,
                        actor=request.user,
                        tenant=offering.tenant,
                        campus=offering.campus,
                        before_data=before,
                        after_data=after_data,
                        metadata={"recomputed_score_count": recomputed_score_count},
                        request=request,
                    )
                    messages.success(request, f"Activity updated. Recomputed {recomputed_score_count} encoded score(s).")
                else:
                    AuditService.log_event(
                        action="CREATE",
                        portal="FACULTY",
                        entity_type="GradeActivity",
                        entity_id=activity.id,
                        actor=request.user,
                        tenant=offering.tenant,
                        campus=offering.campus,
                        after_data=after_data,
                        request=request,
                    )
                    messages.success(request, "Activity created.")
                return redirect("faculty_portal:period_activities", offering_id=offering.id, period_id=period.id)

    activities = (
        GradeActivity.objects.filter(offering_id=offering.id, template_period_id=period.id, is_active=True)
        .select_related("template_component", "template_subcomponent", "template_detail")
        .annotate(score_count=Count("student_scores", filter=Q(student_scores__is_active=True)))
        .order_by("-activity_date", "-created_at")
    )
    for row in activities:
        row.resolved_score_input_mode = FacultyGradingService.resolve_score_input_mode(
            template_component=row.template_component,
            template_subcomponent=row.template_subcomponent,
            template_detail=row.template_detail,
        )
        row.resolved_score_input_mode_label = FacultyGradingService.score_input_mode_label(
            row.resolved_score_input_mode
        )
    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "form": form,
        "activities": activities,
        "is_locked": state["is_locked"],
        "is_submitted": state["is_submitted"],
        "submission_status": state["submission_status"],
        "can_self_reopen": state["can_self_reopen"],
        "can_view_gradebook_summary": state["is_submitted"],
        "can_submit_period": state["can_submit_period"],
        "is_auto_locked_reopened_after_deadline": state["is_auto_locked_reopened_after_deadline"],
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "is_editable": state["is_editable"],
        "system_correction_enabled": state["system_correction_enabled"],
        "completion_grace_until": state["completion_grace_until"],
        "is_within_completion_grace": state["is_within_completion_grace"],
        "is_non_compliant": state["is_non_compliant"],
        "pending_late_completion_request": state["pending_late_completion_request"],
        "active_late_completion_request": state["active_late_completion_request"],
        "can_request_late_completion": state["can_request_late_completion"],
        "can_create_activity": not state["is_locked"] and not state["is_submitted"],
        "editing_activity": editing_activity,
        "component_option_data": component_option_data,
        "subcomponent_option_data": subcomponent_option_data,
        "detail_option_data": detail_option_data,
        "selected_component_id": selected_component_id,
        "selected_subcomponent_id": selected_subcomponent_id,
        "selected_detail_id": selected_detail_id,
        "active_enrollment_count": FacultyGradingService.get_active_enrollments(offering).filter(
            enrollment_status=Enrollment.Status.ACTIVE
        ).count(),
    }
    return render(request, "faculty_portal/period_activities.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_activity_delete_view(request, offering_id: int, period_id: int, activity_id: int):
    if request.method != "POST":
        raise PermissionDenied("Invalid request method.")

    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    state = _period_edit_state(offering, period)
    activity = get_object_or_404(
        GradeActivity.objects.select_related("template_component", "template_subcomponent", "template_detail"),
        id=activity_id,
        offering_id=offering.id,
        template_period_id=period.id,
        is_active=True,
    )

    if not state["is_editable"]:
        messages.error(request, "This period is locked or already submitted. Activity deletion is not allowed.")
        return redirect("faculty_portal:period_activities", offering_id=offering.id, period_id=period.id)

    if state["is_correction_active"]:
        messages.error(
            request,
            "Activity deletion is disabled while an approved correction window is active.",
        )
        return redirect("faculty_portal:period_activities", offering_id=offering.id, period_id=period.id)

    before = _activity_before_data(activity)
    score_count = activity.student_scores.filter(is_active=True).count()
    FacultyGradingService.archive_activity(user=request.user, activity=activity)
    FacultyReminderService.cancel_activity_reminder(
        activity=activity,
        reason="Activity reminder cancelled because the activity was deleted.",
    )
    AuditService.log_event(
        action="DELETE",
        portal="FACULTY",
        entity_type="GradeActivity",
        entity_id=activity.id,
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        before_data=before,
        after_data={**before, "is_active": False},
        metadata={
            "delete_mode": "SOFT_DELETE",
            "deactivated_score_count": score_count,
            "recomputed_period_id": period.id,
        },
        request=request,
    )
    messages.success(request, f"Activity '{activity.title}' deleted.")
    return redirect("faculty_portal:period_activities", offering_id=offering.id, period_id=period.id)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def activity_scores_view(request, offering_id: int, period_id: int, activity_id: int):
    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)
    state = _period_edit_state(offering, period)

    activity = get_object_or_404(
        GradeActivity.objects.select_related("template_component", "template_subcomponent", "template_detail"),
        id=activity_id,
        offering_id=offering.id,
        template_period_id=period.id,
        is_active=True,
    )
    score_input_mode = FacultyGradingService.resolve_score_input_mode(
        template_component=activity.template_component,
        template_subcomponent=activity.template_subcomponent,
        template_detail=activity.template_detail,
    )
    score_input_mode_label = FacultyGradingService.score_input_mode_label(score_input_mode)
    score_input_max = Decimal("100") if score_input_mode == "DIRECT_PERCENTAGE" else Decimal(activity.total_score)

    enrollments = list(FacultyGradingService.get_active_enrollments(offering))
    score_map = {
        row.student_id: row
        for row in StudentActivityScore.objects.filter(activity_id=activity.id, is_active=True)
    }

    if request.method == "POST":
        if not state["is_editable"]:
            messages.error(request, "This period is locked or already submitted.")
            return redirect(
                "faculty_portal:activity_scores",
                offering_id=offering.id,
                period_id=period.id,
                activity_id=activity.id,
            )
        payload = []
        has_error = False
        for enrollment in enrollments:
            student_id = enrollment.student_id
            raw_val = request.POST.get(f"raw_{student_id}", "")
            if enrollment.enrollment_status in Enrollment.NON_ACTIVE_GRADING_STATUSES:
                continue
            if raw_val in (None, ""):
                raw_val = "0"

            raw_score = _parse_decimal(raw_val, fallback=None)
            if raw_score is None:
                has_error = True
                messages.error(
                    request,
                    f"Invalid score for {enrollment.student.last_name}, {enrollment.student.first_name}. "
                    "Please enter a valid number or leave the field blank.",
                )
                break
            if raw_score < 0 or raw_score > score_input_max:
                has_error = True
                messages.error(
                    request,
                    f"Invalid score for {enrollment.student.last_name}, {enrollment.student.first_name}. "
                    f"Value must be between 0 and {score_input_max}.",
                )
                break
            payload.append({"student_id": student_id, "raw_score": raw_score})

        if not has_error:
            try:
                saved_count = FacultyGradingService.upsert_activity_scores(
                    user=request.user,
                    activity=activity,
                    score_payload=payload,
                )
            except ValidationError as exc:
                messages.error(request, str(exc))
            else:
                AuditService.log_event(
                    action="UPDATE",
                    portal="FACULTY",
                    entity_type="StudentActivityScore",
                    entity_id=activity.id,
                    actor=request.user,
                    tenant=offering.tenant,
                    campus=offering.campus,
                    metadata={"saved_count": saved_count, "activity_id": activity.id},
                    request=request,
                )
                messages.success(request, f"Scores saved for {saved_count} student(s).")
                return redirect(
                    "faculty_portal:activity_scores",
                    offering_id=offering.id,
                    period_id=period.id,
                    activity_id=activity.id,
                )

    rows = []
    for enrollment in enrollments:
        existing = score_map.get(enrollment.student_id)
        rows.append(
            {
                "enrollment": enrollment,
                "score": existing,
            }
        )

    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "activity": activity,
        "score_input_mode": score_input_mode,
        "score_input_mode_label": score_input_mode_label,
        "score_input_max": score_input_max,
        "rows": rows,
        "is_locked": state["is_locked"],
        "is_submitted": state["is_submitted"],
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "is_editable": state["is_editable"],
        "submission_status": state["submission_status"],
        "is_auto_locked_reopened_after_deadline": state["is_auto_locked_reopened_after_deadline"],
        "system_correction_enabled": state["system_correction_enabled"],
        "completion_grace_until": state["completion_grace_until"],
        "is_within_completion_grace": state["is_within_completion_grace"],
        "is_non_compliant": state["is_non_compliant"],
        "pending_late_completion_request": state["pending_late_completion_request"],
        "active_late_completion_request": state["active_late_completion_request"],
        "can_request_late_completion": state["can_request_late_completion"],
    }
    return render(request, "faculty_portal/activity_scores.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_attendance_view(request, offering_id: int, period_id: int):
    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)
    state = _period_edit_state(offering, period)

    session_form = AttendanceSessionForm(request.POST or None)
    _style_form(session_form)
    selected_session_id = request.GET.get("session_id") or request.POST.get("session_id")

    if request.method == "POST" and request.POST.get("action") == "create_session":
        if not state["is_editable"]:
            messages.error(request, "This period is locked or already submitted.")
            return redirect("faculty_portal:period_attendance", offering_id=offering.id, period_id=period.id)
        if state["is_correction_active"]:
            messages.error(request, "New attendance sessions cannot be created inside a correction window.")
            return redirect("faculty_portal:period_attendance", offering_id=offering.id, period_id=period.id)
        if session_form.is_valid():
            try:
                session, created = FacultyGradingService.create_or_update_attendance_session(
                    user=request.user,
                    offering=offering,
                    template_period=period,
                    session_date=session_form.cleaned_data["session_date"],
                    title=session_form.cleaned_data.get("title"),
                )
            except ValidationError as exc:
                messages.error(request, str(exc))
            else:
                AuditService.log_event(
                    action="CREATE" if created else "UPDATE",
                    portal="FACULTY",
                    entity_type="AttendanceSession",
                    entity_id=session.id,
                    actor=request.user,
                    tenant=offering.tenant,
                    campus=offering.campus,
                    after_data={"session_date": str(session.session_date), "title": session.title},
                    request=request,
                )
                messages.success(request, "Attendance session saved.")
                return redirect(
                    f"{reverse('faculty_portal:period_attendance', kwargs={'offering_id': offering.id, 'period_id': period.id})}?session_id={session.id}"
                )

    sessions = AttendanceSession.objects.filter(
        offering_id=offering.id,
        template_period_id=period.id,
        is_active=True,
    ).order_by("-session_date", "-created_at")

    selected_session = None
    if selected_session_id:
        selected_session = sessions.filter(id=selected_session_id).first()
    if selected_session is None:
        selected_session = sessions.first()

    enrollments = list(FacultyGradingService.get_active_enrollments(offering))
    record_map = {}
    if selected_session:
        record_map = {
            row.student_id: row
            for row in AttendanceRecord.objects.filter(session_id=selected_session.id, is_active=True)
        }

    if request.method == "POST" and request.POST.get("action") == "save_records":
        if not state["is_editable"]:
            messages.error(request, "This period is locked or already submitted.")
            return redirect("faculty_portal:period_attendance", offering_id=offering.id, period_id=period.id)
        if not selected_session:
            messages.error(request, "Create or select an attendance session first.")
            return redirect("faculty_portal:period_attendance", offering_id=offering.id, period_id=period.id)
        payload = []
        for enrollment in enrollments:
            student_id = enrollment.student_id
            status_code = request.POST.get(f"status_{student_id}", AttendanceRecord.Status.PRESENT)
            remarks = request.POST.get(f"remarks_{student_id}", "")
            payload.append({"student_id": student_id, "status_code": status_code, "remarks": remarks})
        saved_count = FacultyGradingService.upsert_attendance_records(
            user=request.user,
            session=selected_session,
            status_payload=payload,
        )
        AuditService.log_event(
            action="UPDATE",
            portal="FACULTY",
            entity_type="AttendanceRecord",
            entity_id=selected_session.id,
            actor=request.user,
            tenant=offering.tenant,
            campus=offering.campus,
            metadata={"session_id": selected_session.id, "saved_count": saved_count},
            request=request,
        )
        messages.success(request, f"Attendance saved for {saved_count} student(s).")
        return redirect(
            f"{reverse('faculty_portal:period_attendance', kwargs={'offering_id': offering.id, 'period_id': period.id})}?session_id={selected_session.id}"
        )

    attendance_rows = []
    for enrollment in enrollments:
        attendance_rows.append(
            {
                "enrollment": enrollment,
                "record": record_map.get(enrollment.student_id),
            }
        )

    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "session_form": session_form,
        "sessions": sessions,
        "selected_session": selected_session,
        "attendance_rows": attendance_rows,
        "status_choices": AttendanceRecord.Status.choices,
        "is_locked": state["is_locked"],
        "is_submitted": state["is_submitted"],
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "is_editable": state["is_editable"],
        "submission_status": state["submission_status"],
        "is_auto_locked_reopened_after_deadline": state["is_auto_locked_reopened_after_deadline"],
        "is_governance_closed": state["is_governance_closed"],
        "governance_message": state["governance_message"],
        "system_correction_enabled": state["system_correction_enabled"],
        "completion_grace_until": state["completion_grace_until"],
        "is_within_completion_grace": state["is_within_completion_grace"],
        "is_non_compliant": state["is_non_compliant"],
        "pending_late_completion_request": state["pending_late_completion_request"],
        "active_late_completion_request": state["active_late_completion_request"],
        "can_request_late_completion": state["can_request_late_completion"],
        "can_manage_sessions": not state["is_locked"] and not state["is_submitted"] and not state["is_governance_closed"],
    }
    return render(request, "faculty_portal/period_attendance.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_summary_view(request, offering_id: int, period_id: int):
    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)
    official_grade_release = _official_grade_release_state(
        offering=offering,
        template=template,
        template_period=period,
        now=timezone.now(),
    )

    state = _period_edit_state(offering, period)
    def _stored_period_summary():
        period_enrollments = list(
            Enrollment.objects.filter(course_offering_id=offering.id, is_active=True).select_related("student")
        )
        component_codes = [
            c.code for c in period.components.filter(is_active=True).order_by("sort_order", "id")
        ]
        stored_grade_map = {
            row.student_id: row
            for row in StudentPeriodGrade.objects.filter(offering_id=offering.id, template_period_id=period.id)
        }
        summary_rows = []
        missing_student_ids = []
        for enrollment in period_enrollments:
            period_grade_row = stored_grade_map.get(enrollment.student_id)
            if period_grade_row is None:
                missing_student_ids.append(enrollment.student_id)
            summary_rows.append(
                {
                    "student": enrollment.student,
                    "enrollment_status": enrollment.enrollment_status,
                    "component_scores": {},
                    "class_standing": period_grade_row.class_standing_grade if period_grade_row else None,
                    "exam_grade": period_grade_row.exam_grade if period_grade_row else None,
                    "period_grade": period_grade_row.period_grade if period_grade_row else None,
                }
            )
        return {
            "summary": {
                "rows": summary_rows,
                "component_codes": component_codes,
                "base_value": FacultyGradingService.resolve_base_value(offering, template),
            },
            "missing_student_ids": missing_student_ids,
        }

    stored_summary_payload = _stored_period_summary()
    if state["is_editable"]:
        missing_student_ids = stored_summary_payload["missing_student_ids"]
        if missing_student_ids:
            FacultyGradingService.recompute_period_summary_for_students(
                user=request.user,
                offering=offering,
                template_period=period,
                student_ids=missing_student_ids,
                audit_portal="FACULTY",
            )
            AuditService.log_event(
                action="COMPUTE",
                portal="FACULTY",
                entity_type="PeriodSummary",
                entity_id=f"{offering.id}:{period.id}",
                actor=request.user,
                tenant=offering.tenant,
                campus=offering.campus,
                metadata={
                    "offering_id": offering.id,
                    "period_id": period.id,
                    "recomputed_student_count": len(missing_student_ids),
                    "scope": "missing_students",
                },
                request=request,
            )
            stored_summary_payload = _stored_period_summary()
        summary = stored_summary_payload["summary"]
    else:
        summary = stored_summary_payload["summary"]
    activities = list(
        GradeActivity.objects.filter(
            offering_id=offering.id,
            template_period_id=period.id,
            is_active=True,
        )
        .select_related("template_component", "template_subcomponent", "template_detail")
        .order_by(
            "template_component__sort_order",
            "template_subcomponent__sort_order",
            "template_detail__sort_order",
            "activity_date",
            "id",
        )
    )
    summary_layout = _build_summary_layout(period, activities)
    visible_exam_components = [] if official_grade_release["is_final_period_view"] else summary_layout["exam_components"]
    score_by_activity = {
        (score.student_id, score.activity_id): Decimal(score.computed_score)
        for score in StudentActivityScore.objects.filter(
            activity_id__in=[activity.id for activity in activities],
            is_active=True,
            activity__is_active=True,
        )
    }

    q = request.GET.get("q", "").strip()
    rows = summary["rows"]
    if q:
        rows = [
            row
            for row in rows
            if q.lower() in row["student"].student_no.lower()
            or q.lower() in row["student"].last_name.lower()
            or q.lower() in row["student"].first_name.lower()
        ]

    passing_threshold = FacultyGradingService.resolve_passing_threshold(offering)
    submit_readiness = GradingGovernanceService.evaluate_submission_readiness(
        offering=offering,
        template_period=period,
    )
    if official_grade_release["is_final_period_view"] or official_grade_release["show_final_grade"]:
        FacultyGradingService.recompute_final_grades_from_stored_periods(
            user=request.user,
            offering=offering,
            template=template,
        )

    final_grade_map = {
        row.student_id: row.final_grade
        for row in StudentFinalGrade.objects.filter(offering_id=offering.id)
    }
    prior_period_headers = []
    prior_period_grade_map = {}
    if official_grade_release["is_final_period_view"]:
        prior_periods = list(
            template.periods.filter(is_active=True, sequence_no__lt=period.sequence_no).order_by("sequence_no", "id")
        )
        prior_period_headers = [
            {
                "id": prior.id,
                "label": f"{prior.name.upper()} GRADE",
            }
            for prior in prior_periods
        ]
        prior_period_grade_map = {
            (row.student_id, row.template_period_id): row.period_grade
            for row in StudentPeriodGrade.objects.filter(
                offering_id=offering.id,
                template_period_id__in=[prior["id"] for prior in prior_period_headers],
            )
        }

    enriched_rows = []
    for row in rows:
        summary_values = _build_summary_row_values(row, summary_layout, score_by_activity)
        enriched_rows.append(
            {
                "student": row["student"],
                "enrollment_status": row["enrollment_status"],
                "class_standing_blocks": summary_values["class_standing_blocks"],
                "prior_period_grades": [
                    _format_official_grade_display(prior_period_grade_map.get((row["student"].id, prior["id"])))
                    for prior in prior_period_headers
                ],
                "exam_values": [] if official_grade_release["is_final_period_view"] else summary_values["exam_values"],
                "period_grade": (
                    _format_official_grade_display(row["period_grade"])
                    if official_grade_release["show_period_grade"]
                    else None
                ),
                "final_grade": (
                    _format_official_grade_display(final_grade_map.get(row["student"].id))
                    if official_grade_release["show_final_grade"]
                    else None
                ),
                "print_grade_status": (
                    "PASSED"
                    if official_grade_release["show_period_grade"]
                    and row["period_grade"] is not None
                    and Decimal(row["period_grade"]) >= passing_threshold
                    else "FAILED"
                    if official_grade_release["show_period_grade"] and row["period_grade"] is not None
                    else ""
                ),
            }
        )

    all_summary_rows = summary["rows"]
    status_counts = {
        Enrollment.Status.ACTIVE: 0,
        Enrollment.Status.DRP: 0,
        Enrollment.Status.W: 0,
        Enrollment.Status.INC: 0,
    }
    passed_count = 0
    failed_count = 0
    for row in all_summary_rows:
        enrollment_status = row["enrollment_status"]
        status_counts[enrollment_status] = status_counts.get(enrollment_status, 0) + 1
        if enrollment_status != Enrollment.Status.ACTIVE:
            continue
        period_grade = row.get("period_grade")
        if period_grade is None:
            continue
        if Decimal(period_grade) >= passing_threshold:
            passed_count += 1
        else:
            failed_count += 1

    summary_lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=period)
    print_header_name = SystemSettingService.get(
        "PRINT_HEADER_SCHOOL_NAME",
        tenant_id=offering.tenant_id,
        default="NATIONAL COLLEGE OF BUSINESS AND ARTS",
    )
    print_header_address = SystemSettingService.get(
        "PRINT_HEADER_SCHOOL_ADDRESS",
        tenant_id=offering.tenant_id,
        default=getattr(offering.campus, "address", "") or "",
    )
    summary_table_colspan = 4
    for block in summary_layout["class_standing_blocks"]:
        for section in block["sections"]:
            if section["uses_nested"]:
                for group in section["groups"]:
                    summary_table_colspan += len(group["activity_columns"]) + 1
            else:
                summary_table_colspan += len(section["activity_columns"]) + 1
        summary_table_colspan += 1
    summary_table_colspan += len(prior_period_headers)
    summary_table_colspan += len(visible_exam_components)
    if official_grade_release["show_period_grade"]:
        summary_table_colspan += 1
    if official_grade_release["show_final_grade"]:
        summary_table_colspan += 1
    print_sheet_colspan = 3 + len(prior_period_headers)
    if official_grade_release["show_period_grade"]:
        print_sheet_colspan += 1
        print_sheet_colspan += 1
    if official_grade_release["show_final_grade"]:
        print_sheet_colspan += 1

    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "summary_layout": summary_layout,
        "visible_exam_components": visible_exam_components,
        "rows": enriched_rows,
        "base_value": summary["base_value"],
        "prior_period_headers": prior_period_headers,
        "submit_readiness": submit_readiness,
        "submission_deadline": summary_lock.deadline_at if summary_lock else None,
        "completion_grace_until": state["completion_grace_until"],
        "is_locked": state["is_locked"],
        "is_submitted": state["is_submitted"],
        "can_self_reopen": state["can_self_reopen"],
        "can_view_gradebook_summary": state["is_submitted"],
        "can_submit_period": state["can_submit_period"],
        "is_auto_locked_reopened_after_deadline": state["is_auto_locked_reopened_after_deadline"],
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "submission_status": state["submission_status"],
        "correction_mode": state["correction_mode"],
        "system_correction_enabled": state["system_correction_enabled"],
        "print_header_name": print_header_name,
        "print_header_address": print_header_address,
        "generated_at": timezone.localtime(),
        "q": q,
        "passing_threshold": passing_threshold,
        "show_official_period_grade": official_grade_release["show_period_grade"],
        "show_official_final_grade": official_grade_release["show_final_grade"],
        "official_grade_release_notes": official_grade_release["notes"],
        "summary_table_colspan": summary_table_colspan,
        "print_sheet_colspan": print_sheet_colspan,
        "is_governance_closed": state["is_governance_closed"],
        "governance_message": state["governance_message"],
        "is_within_completion_grace": state["is_within_completion_grace"],
        "is_non_compliant": state["is_non_compliant"],
        "pending_late_completion_request": state["pending_late_completion_request"],
        "active_late_completion_request": state["active_late_completion_request"],
        "can_request_late_completion": state["can_request_late_completion"],
        "summary_status_counts": status_counts,
        "summary_passed_count": passed_count,
        "summary_failed_count": failed_count,
    }
    context["readiness_cards"] = [
        {
            "label": "ACTIVE Students",
            "value": submit_readiness["eligible_student_count"],
            "tone": "success",
            "description": "Students still expected to have complete visible period records before submission.",
        },
        {
            "label": "Complete Records",
            "value": submit_readiness["students_with_complete_records"],
            "tone": "primary",
            "description": "ACTIVE students whose visible activity and attendance records are already complete.",
        },
        {
            "label": "Missing Records",
            "value": submit_readiness["students_missing_any_grade"],
            "tone": "danger" if submit_readiness["students_missing_any_grade"] > 0 else "success",
            "description": "ACTIVE students who still have blank required grade or attendance cells in this period.",
        },
        {
            "label": "Template Gaps",
            "value": submit_readiness.get("missing_template_bucket_count", 0),
            "tone": "danger" if submit_readiness.get("missing_template_bucket_count", 0) > 0 else "success",
            "description": "Required template components, subcomponents, or details with no activity setup.",
        },
        {
            "label": "Coverage",
            "value": f'{submit_readiness["coverage_percent"]}%',
            "tone": (
                "warning"
                if submit_readiness["students_missing_any_grade"] > 0
                or submit_readiness.get("missing_template_bucket_count", 0) > 0
                else "info"
            ),
            "description": "Percentage of ACTIVE students whose visible period records are complete.",
        },
        {
            "label": "DRP",
            "value": status_counts.get(Enrollment.Status.DRP, 0),
            "tone": "danger",
            "description": "Students marked dropped from this class and excluded from grading completion checks.",
        },
        {
            "label": "W",
            "value": status_counts.get(Enrollment.Status.W, 0),
            "tone": "warning",
            "description": "Students withdrawn from the term and excluded from grading completion checks.",
        },
        {
            "label": "INC",
            "value": status_counts.get(Enrollment.Status.INC, 0),
            "tone": "secondary",
            "description": "Students tagged incomplete and excluded from active submission-readiness blocking.",
        },
    ]
    if official_grade_release["show_period_grade"]:
        context["readiness_cards"].extend(
            [
                {
                    "label": "Passed",
                    "value": passed_count,
                    "tone": "success",
                    "description": f"ACTIVE students at or above the current passing threshold of {passing_threshold}.",
                },
                {
                    "label": "Failed",
                    "value": failed_count,
                    "tone": "danger",
                    "description": f"ACTIVE students below the current passing threshold of {passing_threshold}.",
                },
            ]
        )
    return render(request, "faculty_portal/period_summary.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_prediction_view(request, offering_id: int, period_id: int):
    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)
    if not FeatureSettingsService.can_user_access_grade_prediction(user=request.user, tenant_id=offering.tenant_id):
        messages.error(request, "Grade prediction is currently disabled for your role.")
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

    state = _period_edit_state(offering, period)
    action = (request.POST.get("action") or "").strip().lower()
    force_refresh = action == "refresh"
    prediction_data = PredictionSnapshotService.get_period_predictions(
        offering=offering,
        template_period=period,
        user=request.user,
        force_refresh=force_refresh,
    )
    PredictionAuditService.log_view(
        user=request.user,
        offering=offering,
        template_period=period,
        view_mode="CLASS_SUMMARY",
    )

    q = (request.GET.get("q") or request.POST.get("q") or "").strip()
    rows = prediction_data["rows"]
    if q:
        rows = [
            row
            for row in rows
            if q.lower() in row.student.student_no.lower()
            or q.lower() in row.student.last_name.lower()
            or q.lower() in row.student.first_name.lower()
        ]
    passing_threshold = FacultyGradingService.resolve_passing_threshold(offering)
    for row in rows:
        final_requirement = PredictionComputationService.final_requirement_for_remaining_periods(
            offering=offering,
            template_period=period,
            student_id=row.student_id,
            current_period_grade=row.current_projected_period_grade,
        )
        row.final_requirement_status = final_requirement["status"]
        row.final_requirement_label = final_requirement["label"]
        row.final_requirement_value = final_requirement["required_average"]
        row.final_requirement_period_names = final_requirement["remaining_period_names"]
        if row.current_projected_period_grade is None:
            row.period_prediction_message = "Prediction not available yet because there are not enough encoded scores yet."
        else:
            row.period_prediction_message = ""
        if row.current_projected_final_grade is None and final_requirement["status"] == "UNAVAILABLE":
            row.final_prediction_message = final_requirement["label"]
        else:
            row.final_prediction_message = ""
        if final_requirement["status"] == "NOT_REACHABLE":
            row.can_still_pass_label = "No"
        elif final_requirement["status"] == "UNAVAILABLE":
            row.can_still_pass_label = "Not available"
        elif (
            final_requirement["status"] == "NO_REMAINING"
            and row.current_projected_final_grade is not None
            and Decimal(row.current_projected_final_grade) < Decimal(passing_threshold)
        ):
            row.can_still_pass_label = "No"
        else:
            row.can_still_pass_label = "Yes"
        if row.current_projected_period_grade is None:
            row.status_label = "Needs Attention"
            row.status_variant = "at-risk"
        elif row.at_risk_flag:
            if (
                row.current_projected_final_grade is not None
                and Decimal(row.current_projected_final_grade) < Decimal(passing_threshold)
            ):
                row.status_label = "Final At Risk"
            else:
                row.status_label = "At Risk"
            row.status_variant = "at-risk"
        else:
            row.status_label = "On Track"
            row.status_variant = "ok"

    summary = prediction_data["summary"]
    metric_cards = [
        {"label": "Students", "value": summary.student_count, "meta": "Active students in this class."},
        {
            "label": "With Estimate",
            "value": summary.students_with_projection,
            "meta": f"{summary.avg_coverage_percent}% average progress",
        },
        {"label": "At Risk", "value": summary.at_risk_count, "meta": "Projected below passing threshold."},
        {
            "label": "Average Estimated Grade",
            "value": _format_decimal_display(summary.avg_projected_grade),
            "meta": f"Unofficial estimate for {period.name}.",
        },
        {
            "label": "Highest Possible",
            "value": _format_decimal_display(summary.avg_best_case_grade),
            "meta": "If remaining items are completed at full score.",
        },
        {
            "label": "Lowest Possible",
            "value": _format_decimal_display(summary.avg_worst_case_grade),
            "meta": "If remaining items get zero raw score.",
        },
    ]

    what_if_result = None
    what_if_student = None
    can_use_what_if = FeatureSettingsService.can_user_access_grade_prediction_what_if(
        user=request.user,
        tenant_id=offering.tenant_id,
    )
    if request.method == "POST" and action in {"simulate", "save_draft"} and can_use_what_if:
        selected_student_id = _safe_int(request.POST.get("student_id"))
        assumed_remaining_percent = _parse_decimal(request.POST.get("assumed_remaining_percent"), Decimal("0"))
        target_grade = _parse_decimal(request.POST.get("target_grade"), Decimal("0"))
        selected_snapshot = next((row for row in prediction_data["rows"] if row.student_id == selected_student_id), None)
        if selected_snapshot:
            what_if_student = selected_snapshot.student
            what_if_result = PredictionWhatIfService.simulate(
                snapshot=selected_snapshot,
                assumed_remaining_percent=assumed_remaining_percent,
            )
            what_if_result["final_requirement"] = PredictionComputationService.final_requirement_for_remaining_periods(
                offering=offering,
                template_period=period,
                student_id=selected_snapshot.student_id,
                current_period_grade=what_if_result["projected_period_grade"],
            )
            if selected_snapshot.remaining_item_count == 0:
                messages.info(
                    request,
                    "This student has no remaining items in the selected period. "
                    "What-if results will stay the same because the grade is already based on completed records.",
                )
            PredictionAuditService.log_view(
                user=request.user,
                offering=offering,
                template_period=period,
                student=selected_snapshot.student,
                view_mode="WHAT_IF",
            )
            if action == "save_draft":
                scenario_name = (request.POST.get("scenario_name") or "").strip() or f"{selected_snapshot.student.student_no} Scenario"
                PredictionWhatIfService.save_draft(
                    user=request.user,
                    snapshot=selected_snapshot,
                    scenario_name=scenario_name,
                    assumed_remaining_percent=assumed_remaining_percent,
                    target_grade=target_grade if target_grade > 0 else None,
                )
                messages.success(request, "What-if scenario draft saved.")

    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "prediction_page_title": f"{period.name} Grade Prediction",
        "state": state,
        "rows": rows,
        "metric_cards": metric_cards,
        "q": q,
        "summary": summary,
        "settings_snapshot": prediction_data["setting_snapshot"],
        "show_best_case": prediction_data["setting_snapshot"].show_best_case,
        "show_worst_case": prediction_data["setting_snapshot"].show_worst_case,
        "show_target_needed": prediction_data["setting_snapshot"].show_target_needed,
        "at_risk_enabled": FeatureSettingsService.is_grade_prediction_at_risk_enabled(tenant_id=offering.tenant_id),
        "can_use_what_if": can_use_what_if,
        "what_if_result": what_if_result,
        "what_if_student": what_if_student,
    }
    return render(request, "faculty_portal/period_prediction.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_prediction_guide_view(request, offering_id: int, period_id: int):
    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)
    if not FeatureSettingsService.can_user_access_grade_prediction(user=request.user, tenant_id=offering.tenant_id):
        messages.error(request, "Grade prediction is currently disabled for your role.")
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

    default_assumption = FeatureSettingsService.get_grade_prediction_default_assumption(
        tenant_id=offering.tenant_id,
        default="IGNORE_MISSING",
    )
    assumption_explanations = {
        "IGNORE_MISSING": {
            "label": "Ignore Missing",
            "meaning": "Only the encoded items are used in the current projection. Missing future items do not yet pull the estimate down.",
        },
        "RAW_ZERO": {
            "label": "Assume Zero Raw Score",
            "meaning": "Missing remaining items are treated as zero, so the projection becomes more conservative.",
        },
        "FULL_SCORE": {
            "label": "Assume Full Score",
            "meaning": "Missing remaining items are treated as full score, so the projection shows an optimistic result.",
        },
    }
    default_assumption_info = assumption_explanations.get(
        default_assumption,
        assumption_explanations["IGNORE_MISSING"],
    )

    column_guides = [
        {
            "column": "Student No.",
            "meaning": "The official student number of the learner in this class.",
            "factors": "Student master record only.",
            "note": "This is only an identifier. It does not affect the prediction formula.",
        },
        {
            "column": "Student Name",
            "meaning": "The enrolled student whose current records are being projected.",
            "factors": "Student master record only.",
            "note": "Use this together with the student number to avoid checking the wrong learner.",
        },
        {
            "column": "Current Projection",
            "meaning": "The unofficial projected grade for the selected period based on the assigned grading template and the records already encoded.",
            "factors": (
                "Encoded activity scores, attendance records, template component/subcomponent/detail weights, "
                "the tenant default assumption mode, and the current period scoring structure."
            ),
            "note": "This is the main estimate faculty should read first, but it can still change when more scores are encoded.",
        },
        {
            "column": "Best Case",
            "meaning": "The possible outcome if the remaining unencoded items are completed at full score.",
            "factors": "Same template weights as the official gradebook, but all still-missing items are treated as full score.",
            "note": "This shows the upper bound of the likely period result, not a guaranteed final outcome.",
        },
        {
            "column": "Worst Case",
            "meaning": "The possible outcome if the remaining unencoded items receive zero raw score.",
            "factors": "Same template weights as the official gradebook, but all still-missing items are treated as zero.",
            "note": "This helps identify the downside risk if missing work is never completed.",
        },
        {
            "column": "Projected Final",
            "meaning": "The unofficial projected final grade using the same final-grade rule configured by the system, including the official formula path for the course.",
            "factors": (
                "Already available official period grades from other periods in the same class plus the current projected "
                "grade of the selected period."
            ),
            "note": "This is a forward-looking estimate only. It is not yet an official final grade.",
        },
        {
            "column": "Target Needed",
            "meaning": "The approximate remaining performance needed to reach the target threshold used by the prediction engine.",
            "factors": "Passing threshold, current worst-case grade, current best-case grade, and the remaining score span available in this period.",
            "note": "If the page says 'Already met', the current projection is already at or above the target. If it says 'Not reachable', the remaining items are not enough to hit the target even with perfect performance.",
        },
        {
            "column": "Average Needed to Pass Final",
            "meaning": "The average grade still needed across the remaining future periods to finish with a passing final grade.",
            "factors": (
                "Passing threshold, total active periods in the grading template, official earlier period grades, "
                "the current projected period grade, and the number of remaining future periods."
            ),
            "note": "Example: if the page says '54.39% average needed across PRE-FINAL, FX', the student needs around that average on the remaining final periods to end with a passing final grade.",
        },
        {
            "column": "Coverage",
            "meaning": "How much of the expected graded work is already encoded for the student.",
            "factors": "Encoded item count divided by expected item count for active activities plus attendance sessions in the selected period.",
            "note": "Low coverage means the projection is still early and should be interpreted carefully.",
        },
        {
            "column": "Remaining",
            "meaning": "The count of still-unencoded activities or expected records that can still affect the prediction.",
            "factors": "Expected active items minus currently encoded items for the student in the selected period.",
            "note": "The higher this number is, the more the current projection may still move.",
        },
        {
            "column": "Risk",
            "meaning": "At-risk status is shown when the projected grade is below the passing threshold currently used by the system.",
            "factors": "Current Projection compared against the class passing threshold.",
            "note": "At-risk is an early warning tool, not an official failure decision.",
        },
    ]

    methodology_steps = [
        {
            "title": "1. Start with only active students and active records",
            "body": (
                "EduGradesPro reads only ACTIVE students in the class. Students marked DRP, W, or INC are not used "
                "in the prediction computation. It also reads only active grade activities and active attendance sessions "
                "in the selected grading period."
            ),
        },
        {
            "title": "2. Convert each encoded record using the official gradebook rules",
            "body": (
                "Every encoded score is converted using the same score input mode, total score, base value, and attendance "
                "mapping used by the official grading engine. Prediction does not invent a separate formula."
            ),
        },
        {
            "title": "3. Roll values upward through the grading template weights",
            "body": (
                "Detail rows feed into subcomponents, subcomponents feed into components, and components feed into the "
                "period result using the configured percentage weights of the assigned grading template."
            ),
        },
        {
            "title": "4. Build three period outcomes",
            "body": (
                "For each student, EduGradesPro keeps a current value from encoded records, a worst-case value that treats "
                "missing work as zero, and a best-case value that treats missing work as full score."
            ),
        },
        {
            "title": "5. Apply the tenant's default assumption to choose Current Projection",
            "body": (
                "Current Projection depends on the configured assumption mode: Ignore Missing uses only encoded work, "
                "Assume Zero Raw Score uses the conservative worst case, and Assume Full Score uses the optimistic best case."
            ),
        },
        {
            "title": "6. Derive projected final and advisory targets",
            "body": (
                "Projected Final uses the available official period grades of the student in the same class plus the current "
                "projected value for the selected period. Target Needed and Average Needed to Pass Final then use the class "
                "passing threshold and the remaining grade space still available."
            ),
        },
    ]

    methodology_factors = [
        {
            "factor": "Assigned grading template",
            "impact": "Controls component, subcomponent, and detail weights, plus the ordered list of grading periods.",
        },
        {
            "factor": "Encoded activity scores",
            "impact": "Provide the actual current evidence already entered in the gradebook for the selected period.",
        },
        {
            "factor": "Attendance sessions and records",
            "impact": "Contribute when the template includes attendance-based grading.",
        },
        {
            "factor": "Assumption mode",
            "impact": "Determines whether missing items are ignored, treated as zero, or treated as full score in the main projection.",
        },
        {
            "factor": "Passing threshold",
            "impact": "Used to mark at-risk students and to compute the target-needed and average-needed advisory values.",
        },
        {
            "factor": "Coverage and remaining items",
            "impact": "Explain how stable or unstable the projection still is at the moment you open the page.",
        },
        {
            "factor": "Official grades from other periods",
            "impact": "Used when the system estimates the Projected Final and the average still needed to finish passing.",
        },
    ]

    methodology_notes = [
        "If all expected items are already encoded and the official period grade already exists, prediction locks to that official period grade for this period.",
        "If an earlier official period grade is still missing, the system may show the final-pass requirement as unavailable because it does not have enough confirmed history yet.",
        "What-if simulation reuses the same worst-case and best-case bounds, then places your assumed remaining performance between those two limits.",
    ]

    transmutation_guides = [
        {
            "title": "Raw-to-computed score using Base 50",
            "body": (
                "For normal raw-score activities, EduGradesPro converts the raw score into a computed score using: "
                "Computed Score = ((Raw Score / Total Score) × Base Value) + (100 − Base Value)."
            ),
        },
        {
            "title": "Default base behavior",
            "body": (
                "If the class does not have a more specific base-value override, EduGradesPro ultimately falls back to "
                "Base 50. That means zero raw score starts at 50.00 and perfect raw score reaches 100.00."
            ),
        },
        {
            "title": "Direct percentage mode",
            "body": (
                "If an activity uses Direct Percentage mode, EduGradesPro does not transmute the raw score through Base 50. "
                "The entered percentage itself becomes the computed score."
            ),
        },
    ]

    transmutation_examples = [
        {
            "example": "Quiz score 18 out of 20 using Base 50",
            "result": "((18 / 20) × 50) + (100 − 50) = 45 + 50 = 95.00",
        },
        {
            "example": "Quiz score 10 out of 20 using Base 50",
            "result": "((10 / 20) × 50) + (100 − 50) = 25 + 50 = 75.00",
        },
        {
            "example": "Direct Percentage activity with encoded 82",
            "result": "Computed Score = 82.00 because Direct Percentage bypasses Base 50 transmutation.",
        },
    ]

    period_grade_steps = [
        "Each encoded activity score is first converted into a computed score using the active scoring rule of that activity.",
        "If a subcomponent has details, EduGradesPro averages those details upward using the detail weights.",
        "If a component has subcomponents, EduGradesPro averages those subcomponents upward using the subcomponent weights.",
        "The period grade is then the weighted sum of all active top-level components in the selected period.",
        "If the template has a configured exam component and there is still no exam data, the official period grade remains unavailable until the exam side has data.",
    ]

    period_grade_formula = (
        "Period Grade = sum of [Component Score × Component Weight]. "
        "Component Score may itself be built from weighted subcomponents and weighted details."
    )

    final_grade_steps = [
        "EduGradesPro stores an official period grade per active grading period when that period is recomputed.",
        "The official final grade record is then computed using the final-grade formula resolved from the matched tenant grading profile.",
        "If no special tenant formula is configured, EduGradesPro falls back to averaging the active grading periods of the assigned template.",
    ]

    final_grade_formula = (
        "Final Grade = tenant grading profile formula for the class. "
        "Prediction follows the same path, but replaces the selected period with its current projected value."
    )

    sample_walkthrough = [
        {
            "title": "Example 1: Early in the period",
            "body": (
                "A student has only one quiz encoded and still has many missing activities. "
                "Coverage may be low, so the Current Projection is still only an early estimate. "
                "Faculty should avoid using this as a final judgment until more records are encoded."
            ),
        },
        {
            "title": "Example 2: Reading Best Case and Worst Case",
            "body": (
                "If Current Projection is 82.00, Best Case is 90.00, and Worst Case is 71.00, "
                "the student still has enough remaining work to move upward or downward. "
                "This range helps the faculty understand how sensitive the current grade is to the remaining items."
            ),
        },
        {
            "title": "Example 3: Using What-If Simulation",
            "body": (
                "If the faculty enters 80% as Remaining Performance, EduGradesPro does not save any grade. "
                "It only answers: 'If the student performs at around 80% on the remaining items, what might the period grade become?' "
                "The result is for planning and advising only."
            ),
        },
        {
            "title": "Example 4: Interpreting Projected Final",
            "body": (
                "If the course uses a final formula such as FG = (PG + MG + Pre-Final Class Standing + Final Exam) / 4, "
                "then the Projected Final follows that same official rule path. "
                "It is still unofficial because some periods or exams may still change later."
            ),
        },
        {
            "title": "Example 5: Average Needed to Pass Final",
            "body": (
                "If PRELIM is 91.43 and MIDTERM is 99.80, and the passing final grade is 75.00, "
                "EduGradesPro can show the average still needed across the remaining final periods. "
                "For example, it may say '54.39% average needed across PRE-FINAL, FX'."
            ),
        },
    ]

    interpretation_rules = [
        "Prediction is unofficial and read-only. It never writes to the gradebook.",
        "Prediction uses the assigned grading template and the official computation path of the class.",
        "Low coverage means the prediction is still unstable and may move significantly.",
        "What-if simulation is only a planning tool. It does not encode grades.",
        "Faculty should continue relying on the official summary and encoded records for formal submission decisions.",
    ]

    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "default_assumption_info": default_assumption_info,
        "column_guides": column_guides,
        "methodology_steps": methodology_steps,
        "methodology_factors": methodology_factors,
        "methodology_notes": methodology_notes,
        "transmutation_guides": transmutation_guides,
        "transmutation_examples": transmutation_examples,
        "period_grade_steps": period_grade_steps,
        "period_grade_formula": period_grade_formula,
        "final_grade_steps": final_grade_steps,
        "final_grade_formula": final_grade_formula,
        "sample_walkthrough": sample_walkthrough,
        "interpretation_rules": interpretation_rules,
    }
    return render(request, "faculty_portal/period_prediction_guide.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_submit_view(request, offering_id: int, period_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:period_summary", offering_id=offering_id, period_id=period_id)

    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering_id)

    readiness = GradingGovernanceService.evaluate_submission_readiness(
        offering=offering,
        template_period=period,
    )
    if readiness["eligible_student_count"] <= 0:
        messages.error(request, "No ACTIVE students available for submission in this period.")
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
    if readiness["students_with_any_grade"] <= 0:
        messages.error(
            request,
            "Submission blocked: no encoded grade/attendance records found for ACTIVE students. "
            "Encode at least one record or mark students as DRP/W/INC first.",
        )
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
    if readiness.get("missing_template_bucket_count", 0) > 0:
        messages.error(
            request,
            "Submission blocked: grading template requirements are incomplete. "
            "Create at least one activity for every required component, subcomponent, or detail.",
        )
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
    if readiness["students_missing_any_grade"] > 0:
        messages.error(
            request,
            "Submission blocked: some ACTIVE students still have blank required grade or attendance records. "
            "Complete all visible records first, or update student status to DRP/W/INC where applicable.",
        )
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
    if request.POST.get("confirm_submit") != "1":
        messages.warning(
            request,
            "Please review the submission readiness and confirm before final submission.",
        )
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

    try:
        submission = GradingGovernanceService.submit_period(
            user=request.user,
            offering=offering,
            template_period=period,
            remarks=request.POST.get("remarks"),
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

    AuditService.log_event(
        action="SUBMIT",
        portal="FACULTY",
        entity_type="GradeSubmission",
        entity_id=submission.id,
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        after_data={
            "offering_id": offering.id,
            "period_id": period.id,
            "status": submission.status,
            "submitted_at": submission.submitted_at.isoformat() if submission.submitted_at else None,
            "submit_readiness": {
                "eligible_student_count": readiness["eligible_student_count"],
                "students_with_any_grade": readiness["students_with_any_grade"],
                "students_missing_any_grade": readiness["students_missing_any_grade"],
                "students_with_complete_records": readiness["students_with_complete_records"],
                "missing_template_bucket_count": readiness.get("missing_template_bucket_count", 0),
                "coverage_percent": str(readiness["coverage_percent"]),
            },
        },
        request=request,
    )
    messages.success(request, f"{period.code} grades submitted successfully.")
    return redirect("faculty_portal:offering_periods", offering_id=offering.id)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_self_reopen_view(request, offering_id: int, period_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:period_summary", offering_id=offering_id, period_id=period_id)

    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering_id)

    before_submission = model_before_after(
        GradingGovernanceService.get_submission(offering=offering, template_period=period)
    )
    justification = (request.POST.get("remarks") or "").strip()
    try:
        submission = GradingGovernanceService.faculty_self_reopen_before_deadline(
            user=request.user,
            offering=offering,
            template_period=period,
            remarks=justification,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

    AuditService.log_event(
        action="REOPEN",
        portal="FACULTY",
        entity_type="GradeSubmission",
        entity_id=submission.id if submission else None,
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        before_data=before_submission,
        after_data=model_before_after(submission),
        metadata={
            "mode": "FACULTY_SELF_REOPEN_BEFORE_DEADLINE",
            "offering_id": offering.id,
            "period_id": period.id,
            "justification": justification,
        },
        request=request,
    )
    messages.success(
        request,
        f"{period.name} gradebook reopened. Review any needed changes and resubmit before the deadline.",
    )
    return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)


@portal_required("FACULTY")
@permission_required("corrections.create")
def period_corrections_view(request, offering_id: int, period_id: int):
    GradingGovernanceService.auto_lapse_expired_correction_windows()
    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    state = _period_edit_state(offering, period)
    if not state["system_correction_enabled"]:
        messages.info(
            request,
            "Correction requests are disabled by tenant policy (MANUAL_ONLY). "
            "Please follow the manual paper approval process and request authorized admin reopen.",
        )
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
    enrollments = list(FacultyGradingService.get_active_enrollments(offering))
    student_ids = [row.student_id for row in enrollments]
    student_qs = Student.objects.filter(id__in=student_ids).order_by("last_name", "first_name", "student_no")
    activity_qs = GradeActivity.objects.filter(
        offering_id=offering.id,
        template_period_id=period.id,
        is_active=True,
    ).select_related("template_component", "template_subcomponent", "template_detail").order_by(
        "template_component__sort_order",
        "template_subcomponent__sort_order",
        "template_detail__sort_order",
        "activity_date",
        "id",
    )
    activity_ids = list(activity_qs.values_list("id", flat=True))
    score_lookup = {
        (row.student_id, row.activity_id): _format_decimal_display(row.raw_score)
        for row in StudentActivityScore.objects.filter(
            activity_id__in=activity_ids,
            student_id__in=student_ids,
            is_active=True,
        )
    }
    form = GradeCorrectionRequestForm(
        request.POST or None,
        request.FILES or None,
        student_queryset=student_qs,
        activity_queryset=activity_qs,
        score_lookup=score_lookup,
    )

    if request.method == "POST":
        if not state["is_submitted"]:
            messages.error(request, "You can request correction only after period submission.")
            return redirect("faculty_portal:period_corrections", offering_id=offering.id, period_id=period.id)
        if form.is_valid():
            items = form.cleaned_data["items"]
            try:
                correction = GradingGovernanceService.create_correction_request(
                    user=request.user,
                    offering=offering,
                    template_period=period,
                    justification=form.cleaned_data["justification"],
                    items=items,
                )
            except ValidationError as exc:
                messages.error(request, str(exc))
            else:
                attachment = form.cleaned_data.get("attachment")
                if attachment:
                    attachment_validation = form.cleaned_data.get("attachment_validation")
                    correction_attachment = GradeCorrectionAttachment.objects.create(
                        correction_request=correction,
                        file=attachment,
                        uploaded_by_user=request.user,
                        original_filename=attachment_validation.original_filename if attachment_validation else attachment.name,
                        content_type=attachment_validation.content_type if attachment_validation else getattr(attachment, "content_type", ""),
                        file_size_bytes=attachment_validation.file_size_bytes if attachment_validation else getattr(attachment, "size", 0),
                    )
                    AuditService.log_event(
                        action="UPLOAD_CORRECTION_ATTACHMENT",
                        portal="FACULTY",
                        entity_type="GradeCorrectionAttachment",
                        entity_id=correction_attachment.id,
                        actor=request.user,
                        tenant=offering.tenant,
                        campus=offering.campus,
                        after_data={
                            "correction_request_id": correction.id,
                            "original_filename": correction_attachment.original_filename,
                            "stored_filename": correction_attachment.file.name,
                            "content_type": correction_attachment.content_type,
                            "file_size_bytes": correction_attachment.file_size_bytes,
                        },
                        request=request,
                    )
                notification_result = CorrectionNotificationService.send_correction_submission_approval_notifications(
                    request_obj=correction
                )
                AuditService.log_event(
                    action="CREATE",
                    portal="FACULTY",
                    entity_type="GradeCorrectionRequest",
                    entity_id=correction.id,
                    actor=request.user,
                    tenant=offering.tenant,
                    campus=offering.campus,
                    after_data={
                        "offering_id": offering.id,
                        "period_id": period.id,
                        "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                        "correction_item_count": len(items),
                        "student_count": len({item["student_id"] for item in items}),
                        "grading_item_count": len({item["grade_activity_id"] for item in items}),
                        "approval_notification_email_attempted": notification_result["attempted"],
                        "approval_notification_email_sent": notification_result["sent"],
                        "approval_notification_email_recipients": notification_result["recipients"],
                    },
                    request=request,
                )
                if notification_result["errors"]:
                    messages.warning(
                        request,
                        "Correction request submitted, but some approval notification emails could not be sent. "
                        "Please verify SMTP and recipient role configuration.",
                    )
                elif (
                    FeatureSettingsService.is_correction_submission_approval_email_enabled(tenant_id=offering.tenant_id)
                    and notification_result["attempted"] == 0
                ):
                    messages.warning(
                        request,
                        "Correction request submitted, but no approver email recipients matched the current feature settings. "
                        "Please review Configuration Management and the recipient role assignments.",
                    )
                messages.success(
                    request,
                    "Correction request submitted for review. Once approved, EduGradesPro will post the corrected values automatically.",
                )
                return redirect("faculty_portal:period_corrections", offering_id=offering.id, period_id=period.id)

    requests_qs = (
        GradeCorrectionRequest.objects.filter(
            offering_id=offering.id,
            template_period_id=period.id,
            requested_by_user=request.user,
        )
        .prefetch_related(
            Prefetch(
                "items",
                queryset=GradeCorrectionRequestItem.objects.select_related(
                    "student",
                    "grade_activity",
                    "grade_activity__template_component",
                    "grade_activity__template_subcomponent",
                    "grade_activity__template_detail",
                ),
            ),
            "attachments",
        )
        .order_by("-created_at")
    )
    official_report_enabled = FeatureSettingsService.is_correction_official_report_enabled(
        tenant_id=offering.tenant_id
    )
    requests = list(requests_qs)
    for req in requests:
        action_codes = {
            item.requested_action
            for item in req.items.all()
            if getattr(item, "requested_action", None)
        }
        req.is_direct_score_request = bool(action_codes) and action_codes == {
            GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE
        }
        req.requires_manual_finalize = bool(action_codes) and not req.is_direct_score_request
    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "form": form,
        "requests": requests,
        "is_locked": state["is_locked"],
        "is_submitted": state["is_submitted"],
        "submission_status": state["submission_status"],
        "submission_deadline": state["submission_deadline"],
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "official_report_enabled": official_report_enabled,
        "correction_students": [
            {
                "id": enrollment.student_id,
                "label": f"{enrollment.student.student_no} - {enrollment.student.last_name}, {enrollment.student.first_name}",
                "student_no": enrollment.student.student_no,
                "name": f"{enrollment.student.last_name}, {enrollment.student.first_name}",
                "status": enrollment.enrollment_status,
            }
            for enrollment in enrollments
        ],
        "correction_activities": [
            {
                "id": activity.id,
                "label": _correction_activity_label(activity),
                "title": activity.title,
                "component_name": activity.template_component.name,
                "subcomponent_name": activity.template_subcomponent.name if activity.template_subcomponent else "-",
                "detail_name": activity.template_detail.name if activity.template_detail else "-",
                "entry_method_label": FacultyGradingService.score_input_mode_label(
                    FacultyGradingService.resolve_score_input_mode(
                        template_component=activity.template_component,
                        template_subcomponent=activity.template_subcomponent,
                        template_detail=activity.template_detail,
                    )
                ),
                "score_input_mode": FacultyGradingService.resolve_score_input_mode(
                    template_component=activity.template_component,
                    template_subcomponent=activity.template_subcomponent,
                    template_detail=activity.template_detail,
                ),
                "score_input_max": _format_decimal_display(
                    Decimal("100")
                    if FacultyGradingService.resolve_score_input_mode(
                        template_component=activity.template_component,
                        template_subcomponent=activity.template_subcomponent,
                        template_detail=activity.template_detail,
                    )
                    == "DIRECT_PERCENTAGE"
                    else activity.total_score
                ),
                "total_score": _format_decimal_display(activity.total_score),
            }
            for activity in activity_qs
        ],
        "correction_score_map": {
            f"{student_id}:{activity_id}": value for (student_id, activity_id), value in score_lookup.items()
        },
        "selected_grade_activity_ids": set(form.data.getlist("grade_activities")) if form.is_bound else set(),
    }
    return render(request, "faculty_portal/period_corrections.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_correction_attachment_download_view(request, offering_id: int, period_id: int, request_id: int, attachment_id: int):
    assignment = _find_faculty_assignment(request.user, offering_id)
    if assignment and not assignment.is_accepted:
        messages.error(request, "Please accept this faculty assignment first before opening the class.")
        return redirect("faculty_portal:my_courses")

    offering, _template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    correction_request = get_object_or_404(
        GradeCorrectionRequest.objects.filter(
            offering_id=offering.id,
            template_period_id=period.id,
            requested_by_user=request.user,
        ),
        id=request_id,
    )
    attachment = get_object_or_404(
        GradeCorrectionAttachment.objects.filter(correction_request=correction_request),
        id=attachment_id,
    )
    try:
        file_handle = attachment.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Attachment file was not found.") from exc

    AuditService.log_event(
        action="DOWNLOAD_CORRECTION_ATTACHMENT",
        portal="FACULTY",
        entity_type="GradeCorrectionAttachment",
        entity_id=attachment.id,
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        metadata={
            "correction_request_id": correction_request.id,
            "original_filename": attachment.original_filename,
            "stored_filename": attachment.file.name,
            "content_type": attachment.content_type,
            "file_size_bytes": attachment.file_size_bytes,
        },
        request=request,
    )
    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=attachment.original_filename or "correction-attachment",
        content_type=attachment.content_type or "application/octet-stream",
    )

@portal_required("FACULTY")
@permission_required("corrections.create")
def period_correction_finalize_view(request, offering_id: int, period_id: int, request_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:period_corrections", offering_id=offering_id, period_id=period_id)

    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering_id)

    if not GradingGovernanceService.is_system_correction_enabled(tenant_id=offering.tenant_id):
        messages.error(
            request,
            "Correction finalization is disabled by tenant policy (MANUAL_ONLY).",
        )
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

    GradingGovernanceService.auto_lapse_expired_correction_windows()
    correction = get_object_or_404(
        GradeCorrectionRequest.objects.select_related("unlock_window", "offering", "template_period"),
        id=request_id,
        offering_id=offering.id,
        template_period_id=period.id,
        requested_by_user=request.user,
    )
    if correction.status != GradeCorrectionRequest.Status.APPROVED:
        if correction.status == GradeCorrectionRequest.Status.LAPSED:
            messages.error(
                request,
                "This correction request already lapsed after the 24-hour validity window. Please file a new request.",
            )
        else:
            messages.error(request, "This correction request is not in approved status.")
        return redirect("faculty_portal:period_corrections", offering_id=offering.id, period_id=period.id)

    window = getattr(correction, "unlock_window", None)
    if not window or not window.is_active or window.is_consumed:
        messages.error(request, "No active correction window to finalize.")
        return redirect("faculty_portal:period_corrections", offering_id=offering.id, period_id=period.id)

    if all(
        item.requested_action == GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE
        for item in correction.items.filter(is_active=True)
    ):
        messages.info(
            request,
            "This score correction petition is already applied and closed automatically after approval.",
        )
        return redirect("faculty_portal:period_corrections", offering_id=offering.id, period_id=period.id)

    try:
        affected_student_ids = {
            item.student_id for item in correction.items.filter(is_active=True, student__isnull=False) if item.student_id
        }
        if affected_student_ids:
            FacultyGradingService.recompute_period_summary_for_students(
                user=request.user,
                offering=offering,
                template_period=period,
                student_ids=affected_student_ids,
                audit_reason="CORRECTION_FINALIZE",
                audit_portal="FACULTY",
                period_is_finalized=True,
                final_is_submitted=True,
            )
        else:
            FacultyGradingService.recompute_period_summary(
                user=request.user,
                offering=offering,
                template_period=period,
                audit_reason="CORRECTION_FINALIZE",
                audit_portal="FACULTY",
                period_is_finalized=True,
                final_is_submitted=True,
            )
        GradingGovernanceService.close_correction_window(request_obj=correction, actor=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        AuditService.log_event(
            action="UPDATE",
            portal="FACULTY",
            entity_type="GradeCorrectionRequest",
            entity_id=correction.id,
            actor=request.user,
            tenant=offering.tenant,
            campus=offering.campus,
            metadata={"finalized": True},
            request=request,
        )
        messages.success(request, "Correction finalized and period scope re-locked.")
    return redirect("faculty_portal:period_corrections", offering_id=offering.id, period_id=period.id)


@portal_required("FACULTY")
@permission_required("corrections.create")
def period_correction_official_report_view(request, offering_id: int, period_id: int, request_id: int):
    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        raise PermissionDenied("Invalid period.")

    correction_request = get_object_or_404(
        GradeCorrectionRequest.objects.filter(
            offering_id=offering.id,
            template_period_id=period.id,
            requested_by_user=request.user,
        ),
        id=request_id,
    )
    if not FeatureSettingsService.is_correction_official_report_enabled(tenant_id=offering.tenant_id):
        raise PermissionDenied("Official correction report generation is disabled.")
    if correction_request.status not in {
        GradeCorrectionRequest.Status.APPROVED,
        GradeCorrectionRequest.Status.CLOSED,
    }:
        raise PermissionDenied("Official correction report is available only after final approval.")

    pdf_bytes = CorrectionOfficialReportService.build_pdf_bytes(request_obj=correction_request)
    filename = _official_correction_report_filename(correction_request)
    AuditService.log_event(
        action="DOWNLOAD_CORRECTION_OFFICIAL_REPORT",
        portal="FACULTY",
        entity_type="GradeCorrectionRequest",
        entity_id=correction_request.id,
        actor=request.user,
        tenant=correction_request.tenant,
        campus=correction_request.campus,
        metadata={
            "filename": filename,
            "content_type": "application/pdf",
            "status": correction_request.status,
        },
        request=request,
    )
    return FileResponse(
        BytesIO(pdf_bytes),
        as_attachment=False,
        filename=filename,
        content_type="application/pdf",
    )


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def offering_enrollment_view(request, offering_id: int):
    assignment = _find_faculty_assignment(request.user, offering_id)
    if assignment and not assignment.is_accepted:
        messages.error(request, "Please accept this faculty assignment first before opening the class.")
        return redirect("faculty_portal:my_courses")
    offering = get_object_or_404(
        CourseOffering.objects.select_related("term", "course", "section", "campus", "department", "tenant"),
        id=offering_id,
        is_active=True,
        faculty_assignments__faculty_user_id=request.user.id,
        faculty_assignments__is_active=True,
        faculty_assignments__accepted_at__isnull=False,
    )
    mode = EnrollmentService.get_enrollment_mode(offering.tenant_id, offering_id=offering.id)
    can_create_enrollment = EnrollmentService.can_create_or_update(
        user=request.user,
        offering=offering,
        portal=Enrollment.SourcePortal.FACULTY,
        action="create",
    )
    can_update_status = EnrollmentService.can_update_classlist_status(
        user=request.user,
        offering=offering,
        portal=Enrollment.SourcePortal.FACULTY,
    )
    student_qs = Student.objects.filter(
        tenant_id=offering.tenant_id,
        campus_id=offering.campus_id,
        is_active=True,
    ).order_by("last_name", "first_name")

    form = FacultyEnrollmentForm(student_queryset=student_qs)
    if request.method == "POST":
        action = (request.POST.get("action") or "upsert_student").strip().lower()
        if action == "remove_from_class":
            if not can_create_enrollment:
                messages.error(
                    request,
                    "Student movement updates are admin-only for this tenant. Ask the academic office to update the class master list.",
                )
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            enrollment_id = request.POST.get("enrollment_id")
            enrollment = offering.enrollments.filter(id=enrollment_id, is_active=True).select_related("student").first()
            if not enrollment:
                messages.error(request, "Active class-list row not found.")
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            enrollment = EnrollmentService.update_enrollment(
                user=request.user,
                enrollment=enrollment,
                enrollment_status=Enrollment.Status.ACTIVE,
                is_active=False,
                portal=Enrollment.SourcePortal.FACULTY,
            )
            AuditService.log_event(
                action="UPDATE",
                portal="FACULTY",
                entity_type="Enrollment",
                entity_id=enrollment.id,
                actor=request.user,
                after_data={
                    "student_id": enrollment.student_id,
                    "course_offering_id": enrollment.course_offering_id,
                    "status": enrollment.enrollment_status,
                    "is_active": enrollment.is_active,
                    "source": enrollment.encoded_via_portal,
                    "movement_action": "REMOVE_FROM_CLASS",
                },
                request=request,
            )
            messages.success(request, "Student removed from this class list. Use this when the student transferred to a different class schedule.")
            return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)

        if action == "update_status":
            if not can_update_status:
                messages.error(request, "You are not allowed to update class list status for this offering.")
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            enrollment_id = request.POST.get("enrollment_id")
            new_status = (request.POST.get("enrollment_status") or "").strip().upper()
            allowed_statuses = {key for key, _ in Enrollment.Status.choices}
            enrollment = offering.enrollments.filter(id=enrollment_id).select_related("student").first()
            if not enrollment:
                messages.error(request, "Enrollment row not found.")
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            if new_status not in allowed_statuses:
                messages.error(request, "Invalid enrollment status.")
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            previous_status = enrollment.enrollment_status
            try:
                enrollment = EnrollmentService.update_enrollment(
                    user=request.user,
                    enrollment=enrollment,
                    enrollment_status=new_status,
                    is_active=True,
                    portal=Enrollment.SourcePortal.FACULTY,
                )
            except (PermissionDenied, ValidationError) as exc:
                messages.error(request, str(exc))
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            AuditService.log_event(
                action="UPDATE",
                portal="FACULTY",
                entity_type="Enrollment",
                entity_id=enrollment.id,
                actor=request.user,
                after_data={
                    "student_id": enrollment.student_id,
                    "course_offering_id": enrollment.course_offering_id,
                    "from_status": previous_status,
                    "to_status": enrollment.enrollment_status,
                    "source": enrollment.encoded_via_portal,
                },
                request=request,
            )
            messages.success(request, f"Enrollment status updated to {new_status}.")
            return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)

        if not can_create_enrollment:
            messages.error(request, "Enrollment creation is admin-only for this tenant.")
            return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)

        form = FacultyEnrollmentForm(request.POST or None, student_queryset=student_qs)
        if form.is_valid():
            try:
                enrollment, created = EnrollmentService.create_enrollment(
                    user=request.user,
                    offering=offering,
                    student=form.cleaned_data["student"],
                    enrollment_status=form.cleaned_data["enrollment_status"],
                    portal=Enrollment.SourcePortal.FACULTY,
                )
            except (PermissionDenied, ValidationError) as exc:
                messages.error(request, str(exc))
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            AuditService.log_event(
                action="CREATE" if created else "UPDATE",
                portal="FACULTY",
                entity_type="Enrollment",
                entity_id=enrollment.id,
                actor=request.user,
                after_data={
                    "student_id": enrollment.student_id,
                    "course_offering_id": enrollment.course_offering_id,
                    "status": enrollment.enrollment_status,
                    "source": enrollment.encoded_via_portal,
                },
                request=request,
            )
            messages.success(request, "Enrollment record saved.")
            return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)

    enrollments = (
        offering.enrollments.select_related("student")
        .filter(is_active=True)
        .order_by("student__last_name", "student__first_name", "student__student_no")
    )
    removed_enrollments = (
        offering.enrollments.select_related("student")
        .filter(is_active=False)
        .order_by("-updated_at", "student__last_name", "student__first_name", "student__student_no")
    )
    context = {
        "offering": offering,
        "mode": mode,
        "can_manage": can_create_enrollment,
        "can_update_status": can_update_status,
        "form": form,
        "enrollments": enrollments,
        "removed_enrollments": removed_enrollments,
    }
    return render(request, "faculty_portal/offering_enrollment.html", context)
