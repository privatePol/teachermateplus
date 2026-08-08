from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re

from django.contrib import messages
from django import forms as django_forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.forms import FacultyLoginForm
from apps.accounts.models import UserSignatureUsageLog
from apps.accounts.services import UserSignatureService
from apps.accounts.views import process_valid_portal_login_form
from apps.academics.models import CourseOffering, FacultyAssignment
from apps.academics.services import AcademicGovernanceService, FacultyAssignmentWorkflowService
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.auditlog.models import AuditLog
from apps.admin_portal.services import model_before_after
from apps.core.decorators import permission_required, portal_required
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import ClassListChangeRequest, Enrollment
from apps.enrollment.services import ClassListChangeRequestService, EnrollmentService
from apps.enrollment.forms import ClassListAddRequestForm, ClassListRemoveRequestForm
from apps.faculty_portal.forms import (
    AttendanceSessionForm,
    FacultyMemoForm,
    FacultyFeedbackForm,
    FacultyReminderForm,
    FacultyTemplateIssueReportForm,
    GradeActivityForm,
    GradeCorrectionRequestForm,
)
from apps.faculty_portal.services import (
    FacultyActivityHistoryService,
    FacultyDashboardUpdatesService,
    FacultyPerformanceService,
    StudentInterventionService,
)
from apps.faculty_portal.feedback import create_feedback_submission
from apps.faculty_portal.help_guide import FACULTY_HELP_SECTIONS
from apps.faculty_portal.operational_policies import (
    FACULTY_OPERATIONAL_POLICY_SECTIONS,
    FACULTY_OPERATIONAL_POLICY_STATUS,
)
from apps.grading.models import (
    CourseTemplateAssignment,
    DetailComputationMode,
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
    TemplateHotfixRequest,
)
from apps.grading.explanations import GradeExplanationService
from apps.grading.notifications import CorrectionNotificationService, GradebookReopenNotificationService
from apps.grading.reporting import (
    ClassTabulationSheetPdfService,
    CompleteTabulationSheetDataService,
    CorrectionOfficialReportService,
    FacultyFinalClearanceReportService,
    TabulationSheetAuthorizationService,
)
from apps.grading.services import (
    FacultyGradingService,
    GradeEncodingAccessService,
    GradingGovernanceService,
    GradingTemplateTestingCalculatorService,
    TemplateGovernanceWorkflowService,
    TemplateHotfixService,
)
from apps.grading.tabulation import PeriodicGradePrintDataService, PeriodSummaryLayoutService
from apps.interventions.forms import FacultyDecisionForm, FollowUpForm, InterventionActionForm, ManualInterventionCaseForm
from apps.interventions.models import AcademicInterventionAction, AcademicInterventionCase
from apps.interventions.services import (
    AcademicConcernDetectionService,
    AcademicInterventionAuthorizationService,
    AcademicInterventionCaseService,
)
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


def _has_active_published_course_template_assignment(offering):
    return (
        CourseTemplateAssignment.objects.filter(
            course_id=offering.course_id,
            is_active=True,
            grading_template__is_active=True,
            grading_template__is_published=True,
        )
        .filter(Q(effective_from_term_id=offering.term_id) | Q(effective_from_term__isnull=True))
        .exists()
    )


@require_POST
@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def feedback_submit_view(request):
    form = FacultyFeedbackForm(request.POST)
    result = create_feedback_submission(request=request, form=form)
    payload = {
        "success": result.success,
        "message": result.message,
    }
    if not result.success:
        payload["errors"] = result.errors or {}
    return JsonResponse(payload, status=200 if result.success else 400)


def _faculty_offering_has_submitted_grades(offering, template):
    period_ids = list(template.periods.filter(is_active=True).values_list("id", flat=True))
    if not period_ids:
        return False
    return GradeSubmission.objects.filter(
        offering_id=offering.id,
        template_period_id__in=period_ids,
        status__in=[
            GradeSubmission.Status.SUBMITTED,
            GradeSubmission.Status.REOPENED,
        ],
    ).exists()


def _tenant_passing_threshold_or_default(tenant_id) -> Decimal:
    tenant_value = SystemSettingService.get(
        "PASSING_GRADE_THRESHOLD",
        tenant_id=tenant_id,
        default="75",
    )
    try:
        return GradingGovernanceService._round(Decimal(str(tenant_value)))
    except Exception:
        return Decimal("75.00")


def _can_report_template_issue(user, offering, template):
    if not template or not getattr(template, "is_published", False):
        return False
    if getattr(offering, "faculty_is_read_only", False):
        return False
    if offering.status != CourseOffering.Status.OPEN:
        return False
    if not PermissionService.has_permission(
        user,
        "template_hotfixes.create",
        tenant_id=offering.tenant_id,
        campus_id=offering.campus_id,
    ):
        return False
    if not TemplateGovernanceWorkflowService.user_has_stage_role(
        user=user,
        stage_code=TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST,
        tenant_id=offering.tenant_id,
    ):
        return False
    if _faculty_offering_has_submitted_grades(offering, template):
        return False
    return True


def _faculty_final_clearance_report_filename(report_obj: FacultyFinalClearanceReport) -> str:
    faculty_code = report_obj.faculty_user.username or f"faculty-{report_obj.faculty_user_id}"
    campus_code = report_obj.campus.code or "campus"
    term_code = report_obj.term.code or "term"
    return f"faculty-final-clearance-{campus_code}-{term_code}-{faculty_code}-{report_obj.id}.pdf"


def _faculty_final_clearance_preview_for_scope(*, faculty_user, term, campus):
    preview = FacultyFinalClearanceReportService.evaluate_faculty_clearance(
        faculty_user=faculty_user,
        term=term,
        campus=campus,
    )
    preview["can_print"] = (
        preview.get("clearance_status") == FacultyFinalClearanceReport.ClearanceStatus.CLEARED
    )
    return preview


@ensure_csrf_cookie
def public_index_view(request):
    login_form = FacultyLoginForm(request=request, data=request.POST or None)
    if request.method == "POST":
        if login_form.is_valid():
            response = process_valid_portal_login_form(
                request,
                form=login_form,
                portal_code="FACULTY",
                portal_permission="faculty_portal.access",
                dashboard_url_name="faculty_portal:dashboard",
            )
            if response is not None:
                return response
        username = request.POST.get("username", "")
        if username:
            AuditService.log_login_failure(request, username=username, portal="FACULTY")
    return render(request, "faculty_portal/public_index.html", {"login_form": login_form})


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def guide_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    if not FeatureSettingsService.is_role_based_help_guide_enabled(tenant_id=tenant_id, default=True):
        return render(request, "faculty_portal/guide.html")
    return render(
        request,
        "faculty_portal/guide_role_based.html",
        {"help_sections": FACULTY_HELP_SECTIONS},
    )


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def guide_manual_view(request):
    return render(request, "faculty_portal/guide_manual.html")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def operational_policies_view(request):
    return render(
        request,
        "faculty_portal/operational_policies.html",
        {
            "policy_sections": FACULTY_OPERATIONAL_POLICY_SECTIONS,
            "policy_status": FACULTY_OPERATIONAL_POLICY_STATUS,
        },
    )


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
        offering__tenant__is_active=True,
        offering__campus__is_active=True,
        offering__academic_year__is_active=True,
        offering__term__is_active=True,
        offering__department__is_active=True,
        offering__program__is_active=True,
        offering__program__department__is_active=True,
        offering__course__is_active=True,
        offering__section__is_active=True,
        offering__section__department__is_active=True,
        offering__section__program__is_active=True,
        offering__section__program__department__is_active=True,
    ).filter(
        Q(offering__course__department__isnull=True) | Q(offering__course__department__is_active=True)
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
        tenant__is_active=True,
        campus__is_active=True,
        academic_year__is_active=True,
        term__is_active=True,
        department__is_active=True,
        program__is_active=True,
        program__department__is_active=True,
        course__is_active=True,
        section__is_active=True,
        section__department__is_active=True,
        section__program__is_active=True,
        section__program__department__is_active=True,
    ).filter(
        Q(course__department__isnull=True) | Q(course__department__is_active=True)
    ).select_related("tenant", "campus", "department", "academic_year", "term", "course", "section")


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


def _faculty_activity_last_selection_session_key(*, user_id, offering_id, period_id):
    return f"faculty_activity_last_selection:{user_id}:{offering_id}:{period_id}"


def _validate_faculty_activity_last_selection(selection, *, component_qs, subcomponent_qs, detail_qs):
    if not isinstance(selection, dict):
        return {}

    component_id = _safe_int(selection.get("component_id"))
    subcomponent_id = _safe_int(selection.get("subcomponent_id"))
    detail_id = _safe_int(selection.get("detail_id"))
    if not component_id or not component_qs.filter(id=component_id).exists():
        return {}

    valid_selection = {
        "component_id": component_id,
        "subcomponent_id": None,
        "detail_id": None,
    }
    if not subcomponent_id:
        return valid_selection
    if not subcomponent_qs.filter(id=subcomponent_id, template_component_id=component_id).exists():
        return valid_selection

    valid_selection["subcomponent_id"] = subcomponent_id
    if not detail_id:
        return valid_selection
    if not detail_qs.filter(id=detail_id, template_subcomponent_id=subcomponent_id).exists():
        return valid_selection

    valid_selection["detail_id"] = detail_id
    return valid_selection


def _safe_faculty_activity_query_string(request):
    query = request.GET.copy()
    next_url = (query.get("next") or "").strip()
    if next_url and not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        query.pop("next", None)
    view_mode = (query.get("view") or "").strip()
    if view_mode and view_mode not in {"grouped", "flat"}:
        query.pop("view", None)
    return query.urlencode()


def _faculty_activity_url(request, view_name, *, offering_id, period_id, activity_id=None):
    if activity_id is None:
        url = reverse(view_name, kwargs={"offering_id": offering_id, "period_id": period_id})
    else:
        url = reverse(
            view_name,
            kwargs={"offering_id": offering_id, "period_id": period_id, "activity_id": activity_id},
        )
    query_string = _safe_faculty_activity_query_string(request)
    return f"{url}?{query_string}" if query_string else url


def _faculty_activity_view_mode(request):
    requested_mode = (request.GET.get("view") or "").strip()
    return requested_mode if requested_mode in {"grouped", "flat"} else "grouped"


def _faculty_activity_view_switch_url(request, *, view_mode):
    query = request.GET.copy()
    query["view"] = view_mode
    next_url = (query.get("next") or "").strip()
    if next_url and not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        query.pop("next", None)
    return f"?{query.urlencode()}"


def _faculty_activity_sort_key(activity):
    component = activity.template_component
    subcomponent = activity.template_subcomponent
    detail = activity.template_detail
    return (
        component.sort_order if component else 999999,
        component.id if component else 0,
        subcomponent.sort_order if subcomponent else -1,
        subcomponent.id if subcomponent else 0,
        detail.sort_order if detail else -1,
        detail.id if detail else 0,
        activity.activity_date or date.max,
        (activity.title or "").lower(),
        activity.id,
    )


def _new_activity_group(label_key, item):
    return {
        label_key: item,
        "activity_count": 0,
        "encoded_count": 0,
        "expected_count": 0,
        "activities": [],
    }


def _add_activity_group_counts(group, activity, active_enrollment_count):
    group["activity_count"] += 1
    group["encoded_count"] += int(getattr(activity, "score_count", 0) or 0)
    group["expected_count"] += active_enrollment_count


def _build_faculty_activity_groups(activities, *, active_enrollment_count):
    component_groups = []
    component_lookup = {}

    for activity in sorted(activities, key=_faculty_activity_sort_key):
        component = activity.template_component
        component_group = component_lookup.get(component.id)
        if component_group is None:
            component_group = _new_activity_group("component", component)
            component_group.update(
                {
                    "html_id": f"activity-component-{component.id}",
                    "subcomponent_groups": [],
                    "_subcomponent_lookup": {},
                }
            )
            component_lookup[component.id] = component_group
            component_groups.append(component_group)

        _add_activity_group_counts(component_group, activity, active_enrollment_count)

        subcomponent = activity.template_subcomponent
        if subcomponent is None:
            component_group["activities"].append(activity)
            continue

        subcomponent_lookup = component_group["_subcomponent_lookup"]
        subcomponent_group = subcomponent_lookup.get(subcomponent.id)
        if subcomponent_group is None:
            subcomponent_group = _new_activity_group("subcomponent", subcomponent)
            subcomponent_group.update(
                {
                    "html_id": f"activity-subcomponent-{subcomponent.id}",
                    "detail_groups": [],
                    "_detail_lookup": {},
                }
            )
            subcomponent_lookup[subcomponent.id] = subcomponent_group
            component_group["subcomponent_groups"].append(subcomponent_group)

        _add_activity_group_counts(subcomponent_group, activity, active_enrollment_count)

        detail = activity.template_detail
        if detail is None:
            subcomponent_group["activities"].append(activity)
            continue

        detail_lookup = subcomponent_group["_detail_lookup"]
        detail_group = detail_lookup.get(detail.id)
        if detail_group is None:
            detail_group = _new_activity_group("detail", detail)
            detail_group["html_id"] = f"activity-detail-{detail.id}"
            detail_lookup[detail.id] = detail_group
            subcomponent_group["detail_groups"].append(detail_group)

        _add_activity_group_counts(detail_group, activity, active_enrollment_count)
        detail_group["activities"].append(activity)

    for component_group in component_groups:
        component_group.pop("_subcomponent_lookup", None)
        for subcomponent_group in component_group["subcomponent_groups"]:
            subcomponent_group.pop("_detail_lookup", None)

    return component_groups


def _faculty_offering_scope_state(offering):
    active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=offering.tenant_id)
    has_active_scope = bool(active_academic_year and active_term)
    in_active_scope = True
    if has_active_scope:
        in_active_scope = offering.academic_year_id == active_academic_year.id and offering.term_id == active_term.id
    forced_archive = offering.status == CourseOffering.Status.ARCHIVED
    read_only = forced_archive or not in_active_scope
    if forced_archive:
        reason = "This class is archived and is available for reference only."
    elif not in_active_scope and has_active_scope:
        reason = (
            f"This class is outside the active academic scope "
            f"({active_academic_year.code} / {active_term.code}) and is available for reference only."
        )
    else:
        reason = ""
    return {
        "active_academic_year": active_academic_year,
        "active_term": active_term,
        "has_active_scope": has_active_scope,
        "in_active_scope": in_active_scope,
        "forced_archive": forced_archive,
        "read_only": read_only,
        "reason": reason,
    }


def _attach_faculty_offering_scope_state(offering):
    state = _faculty_offering_scope_state(offering)
    offering.faculty_scope_state = state
    offering.faculty_is_read_only = state["read_only"]
    offering.faculty_read_only_reason = state["reason"]
    return offering


def _faculty_current_offering_queryset(user, *, tenant_id=None):
    queryset = _faculty_offering_queryset(user)
    if tenant_id:
        queryset = queryset.filter(tenant_id=tenant_id)
        _active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
        if active_term:
            queryset = queryset.filter(academic_year_id=active_term.academic_year_id, term_id=active_term.id)
    return queryset.exclude(status=CourseOffering.Status.ARCHIVED)


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
    offering = get_object_or_404(_faculty_offering_queryset(request.user), id=offering_id)
    return _attach_faculty_offering_scope_state(offering)


def _require_pending_faculty_assignment_or_404(request, assignment_id: int):
    return get_object_or_404(
        _faculty_assignment_queryset(request.user).filter(accepted_at__isnull=True),
        id=assignment_id,
    )


def _require_accepted_faculty_assignment_or_404(request, assignment_id: int):
    return get_object_or_404(
        _faculty_assignment_queryset(request.user).filter(
            accepted_at__isnull=False,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        ),
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
        if GradingGovernanceService.get_active_approved_reopen_request(
            offering=offering,
            template_period=period,
        ):
            state["message"] = "This earlier period is open because a reopen request was approved."
        elif completion_window_state["is_non_compliant"]:
            state["message"] = "This earlier period can still be submitted if complete, but additional encoding requires a reopen request."
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


def _periodic_grade_report_matches_active_scope(request, offering) -> bool:
    scope = getattr(request, "scope", None)
    if not isinstance(scope, dict):
        return False
    tenant_id = scope.get("tenant_id")
    campus_id = scope.get("campus_id")
    return bool(
        tenant_id
        and campus_id
        and offering.tenant_id == tenant_id
        and offering.campus_id == campus_id
    )


def _period_edit_state(offering, period):
    scope_state = getattr(offering, "faculty_scope_state", None) or _faculty_offering_scope_state(offering)
    GradingGovernanceService.auto_lock_expired_reopened_gradebook(offering=offering, template_period=period)
    GradingGovernanceService.auto_lock_expired_approved_reopen_request_for_period(
        offering=offering,
        template_period=period,
    )
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
    is_auto_closed_after_deadline = GradingGovernanceService.is_auto_closed_after_deadline(
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
    active_approved_reopen_request = GradingGovernanceService.get_active_approved_reopen_request(
        offering=offering,
        template_period=period,
    )
    active_approved_reopen_expires_at = GradingGovernanceService.reopen_request_expires_at(
        active_approved_reopen_request
    )
    effective_is_locked = is_locked and not active_approved_reopen_request
    is_editable = (
        ((not effective_is_locked and not is_submitted) or is_correction_active or active_approved_reopen_request)
        and not is_auto_closed_after_deadline
        and not governance_state["is_closed_by_active_period"]
    )
    can_submit_period = (
        not is_submitted
        and not governance_state["is_closed_by_active_period"]
        and (not effective_is_locked or is_auto_locked_reopened_after_deadline or active_approved_reopen_request)
    )
    pending_reopen_request = GradingGovernanceService.get_pending_reopen_request(
        offering=offering,
        template_period=period,
    )
    can_self_reopen = GradingGovernanceService.can_faculty_self_reopen_before_deadline(
        offering=offering,
        template_period=period,
    )
    correction_mode = GradingGovernanceService.get_correction_mode(tenant_id=offering.tenant_id)
    system_correction_enabled = correction_mode == GradingGovernanceService.CORRECTION_MODE_SYSTEM_REQUEST
    correction_filing_state = GradingGovernanceService.get_correction_request_filing_state(
        offering=offering,
        template_period=period,
    )
    encoding_control = GradeEncodingAccessService.get_closed_control(offering=offering, template_period=period)
    encoding_control_message = GradeEncodingAccessService.build_block_notice(
        encoding_control,
        offering=offering,
        template_period=period,
    )
    return {
        "faculty_scope_state": scope_state,
        "is_read_only_class": scope_state["read_only"],
        "is_locked": effective_is_locked,
        "raw_is_locked": is_locked,
        "is_submitted": is_submitted,
        "submission_status": submission.status if submission else None,
        "submission": submission,
        "submission_deadline": GradingGovernanceService.resolve_submission_deadline(
            offering=offering,
            template_period=period,
        ),
        "completion_grace_until": None,
        "encoding_close_deadline": completion_window_state["encoding_close_deadline"],
        "is_auto_closed_after_deadline": is_auto_closed_after_deadline,
        "is_within_completion_grace": False,
        "grace_expired": False,
        "is_non_compliant": completion_window_state["is_non_compliant"],
        "is_overdue": completion_window_state.get("is_overdue", completion_window_state["is_non_compliant"]),
        "active_late_completion_request": None,
        "pending_late_completion_request": None,
        "pending_reopen_request": pending_reopen_request,
        "active_approved_reopen_request": active_approved_reopen_request,
        "active_approved_reopen_expires_at": active_approved_reopen_expires_at,
        "can_request_deadline_reopen": (
            GradingGovernanceService.can_request_reopen_after_auto_close(
                offering=offering,
                template_period=period,
            )
            and not scope_state["read_only"]
        ),
        "has_active_late_completion_request": False,
        "has_pending_late_completion_request": False,
        "can_request_late_completion": False,
        "is_correction_active": is_correction_active,
        "active_correction_request": active_correction_request,
        "encoding_control_closed": bool(encoding_control),
        "encoding_control_message": encoding_control_message,
        "is_editable": is_editable and not scope_state["read_only"] and not encoding_control,
        "can_submit_period": can_submit_period and not scope_state["read_only"] and not encoding_control,
        "is_auto_locked_reopened_after_deadline": is_auto_locked_reopened_after_deadline,
        "can_self_reopen": can_self_reopen and not scope_state["read_only"],
        "governance_state": governance_state,
        "is_governance_closed": governance_state["is_closed_by_active_period"],
        "governance_message": governance_state["message"],
        "correction_mode": correction_mode,
        "system_correction_enabled": system_correction_enabled,
        "correction_lifecycle_state": correction_filing_state["lifecycle_state"],
        "correction_filing_state": correction_filing_state,
        "can_access_corrections": bool(
            system_correction_enabled and correction_filing_state["is_allowed"] and not scope_state["read_only"]
        ),
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


def _average_label_from_section_label(label, fallback_label="AVE"):
    value = str(label or "").strip()
    if not value:
        return fallback_label
    if "/" in value:
        parts = [part for part in re.split(r"/+", value) if part.strip()]
        initials = [match.group(0)[0].upper() for part in parts if (match := re.search(r"[A-Za-z]", part))]
        if initials:
            return f"{'/'.join(initials)} AVE"
    words = re.findall(r"[A-Za-z]+", value)
    if len(words) >= 2:
        return f"{''.join(word[0].upper() for word in words)} AVE"
    return f"{words[0].upper()} AVE" if words else fallback_label


def _summary_section_color_class(label):
    value = str(label or "").strip().upper()
    if "QUIZ" in value:
        return "summary-group-quizzes"
    if "PARTICIPATION" in value or "OUTPUT" in value:
        return "summary-group-participation"
    return "summary-group-standard"


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
            "total_label": "CS AVE" if not component_is_exam else "AVE",
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

                visible_detail_groups = detail_groups
                if subcomponent.detail_computation_mode == "AVERAGE_ACTIVITIES":
                    visible_detail_groups = [group for group in detail_groups if group["activity_columns"]]

                if visible_detail_groups:
                    nested_activity_titles = [
                        activity.title
                        for group in visible_detail_groups
                        for activity in activities_by_detail.get(group["id"], [])
                    ]
                    component_layout["sections"].append(
                        {
                            "id": subcomponent.id,
                            "label": subcomponent.name.upper(),
                            "color_class": _summary_section_color_class(subcomponent.name or subcomponent.code),
                            "uses_nested": True,
                            "groups": visible_detail_groups,
                            "avg_label": _average_label_from_section_label(
                                subcomponent.name or subcomponent.code,
                                fallback_label=_average_label_from_titles(nested_activity_titles, fallback_label="AVE"),
                            ),
                            "weight_percentage": Decimal(subcomponent.weight_percentage or 0),
                            "detail_computation_mode": subcomponent.detail_computation_mode,
                            "colspan": sum(group["colspan"] for group in visible_detail_groups) + 1,
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
                            "color_class": _summary_section_color_class(subcomponent.name or subcomponent.code),
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
                    "color_class": _summary_section_color_class(component.name or component.code),
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


def _official_grade_release_state(
    *,
    offering,
    template,
    template_period,
    is_period_submitted=False,
    submission_status=None,
    now=None,
):
    now = now or timezone.now()
    period_restricted = FeatureSettingsService.show_faculty_official_period_grades_after_deadline(
        tenant_id=offering.tenant_id,
        default=False,
    )
    submission_restricted = FeatureSettingsService.show_faculty_official_period_grades_after_submission(
        tenant_id=offering.tenant_id,
        default=False,
    )
    final_restricted = FeatureSettingsService.show_faculty_official_final_grades_after_deadline(
        tenant_id=offering.tenant_id,
        default=False,
    )
    period_visibility_allowed = is_period_submitted or not submission_restricted
    period_deadline_passed = _has_passed_period_deadline(
        offering=offering,
        template_period=template_period,
        now=now,
    )
    show_period = (
        period_visibility_allowed
        and ((not period_restricted) or period_deadline_passed)
    )

    final_period = (
        template.periods.filter(is_active=True).order_by("-sequence_no", "-id").first()
        if template is not None
        else None
    )
    is_final_period_view = bool(final_period is not None and template_period.id == final_period.id)
    show_final = bool(
        is_final_period_view
        and is_period_submitted
        and (
            (not final_restricted)
            or _has_passed_period_deadline(offering=offering, template_period=final_period, now=now)
        )
    )

    notes = []
    if submission_restricted and not is_period_submitted:
        notes.append(
            f"The official {template_period.name} grade is hidden until this gradebook is submitted. "
            "Activity scores and supporting computations remain available for review."
        )
    if period_restricted and not period_deadline_passed:
        notes.append(
            f"Official {template_period.name} grade is hidden until the {template_period.name} deadline has passed."
        )
    if is_final_period_view and not is_period_submitted:
        notes.append("Official final grade is hidden until the Final gradebook is submitted.")
    elif final_restricted and is_final_period_view and not show_final:
        notes.append(
            f"Official final grade is hidden until the {final_period.name} deadline has passed."
        )

    return {
        "show_period_grade": show_period,
        "show_final_grade": show_final,
        "notes": notes,
        "final_period": final_period,
        "is_final_period_view": is_final_period_view,
        "submission_restricted": submission_restricted,
        "period_grade_masked_label": (
            "Hidden until submission"
            if submission_restricted and not is_period_submitted
            else "Hidden until deadline"
            if period_restricted and not period_deadline_passed
            else "Not available"
        ),
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
                        "detail_computation_mode": subcomponent.detail_computation_mode,
                        "detail_computation_mode_label": subcomponent.get_detail_computation_mode_display(),
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
            "If no special formula is configured, TeacherMate+ averages the active grading periods "
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
                section_activity_values = []

                for group in section["groups"]:
                    activity_values = [score_by_activity.get((student_id, activity_id)) for activity_id in group["activity_ids"]]
                    section_activity_values.extend(activity_values)
                    average_value = _average_display(activity_values)
                    if average_value is not None:
                        nested_has_data = True
                    if section.get("detail_computation_mode") != "AVERAGE_ACTIVITIES":
                        nested_numeric += (group["weight_percentage"] / nested_weight_total) * (average_value or Decimal("0"))
                    section_values["groups"].append(
                        {
                            "activity_values": activity_values,
                            "average": average_value,
                        }
                    )

                if section.get("detail_computation_mode") == "AVERAGE_ACTIVITIES":
                    section_score = _average_display(section_activity_values) or Decimal("0")
                else:
                    section_score = FacultyGradingService._round(nested_numeric)
                component_numeric += (section["weight_percentage"] / section_weight_total) * section_score
                if nested_has_data:
                    component_has_data = True
                block_values["sections"].append(section_values)
                section_values["average"] = section_score if nested_has_data else None
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
                    section_activity_values = []
                    for group in section["groups"]:
                        activity_values = [score_by_activity.get((student_id, activity_id)) for activity_id in group["activity_ids"]]
                        section_activity_values.extend(activity_values)
                        average_value = _average_display(activity_values)
                        if average_value is not None:
                            nested_has_data = True
                        if section.get("detail_computation_mode") != "AVERAGE_ACTIVITIES":
                            nested_numeric += (group["weight_percentage"] / nested_weight_total) * (average_value or Decimal("0"))
                    if nested_has_data:
                        if section.get("detail_computation_mode") == "AVERAGE_ACTIVITIES":
                            nested_values.append(_average_display(section_activity_values))
                        else:
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


def _period_uses_average_activity_details(period) -> bool:
    return period.components.filter(
        is_active=True,
        subcomponents__is_active=True,
        subcomponents__detail_computation_mode="AVERAGE_ACTIVITIES",
    ).exists()


def _all_template_periods_submitted(offering, periods):
    if not periods:
        return False
    return all(
        GradingGovernanceService.is_submitted(offering=offering, template_period=period)
        for period in periods
    )


def _build_class_tabulation_period_section(*, offering, period, enrollments, stored_grade_map):
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
    summary_layout = PeriodSummaryLayoutService.build_layout(period, activities)
    score_by_activity = {
        (score.student_id, score.activity_id): Decimal(score.computed_score)
        for score in StudentActivityScore.objects.filter(
            activity_id__in=[activity.id for activity in activities],
            is_active=True,
            activity__is_active=True,
        )
    }
    period_grade_map = stored_grade_map.get(period.id, {})
    rows = []
    for enrollment in enrollments:
        grade_row = period_grade_map.get(enrollment.student_id)
        base_row = {
            "student": enrollment.student,
            "enrollment_status": enrollment.enrollment_status,
            "component_scores": {},
            "period_grade": grade_row.period_grade if grade_row else None,
        }
        summary_values = PeriodSummaryLayoutService.build_row_values(base_row, summary_layout, score_by_activity)
        rows.append(
            {
                "student": enrollment.student,
                "enrollment_status": _tabulation_status_display(enrollment.enrollment_status),
                "class_standing_blocks": summary_values["class_standing_blocks"],
                "exam_values": summary_values["exam_values"],
                "period_grade": _format_official_grade_display(grade_row.period_grade if grade_row else None),
            }
        )

    colspan = 4
    for block in summary_layout["class_standing_blocks"]:
        for section in block["sections"]:
            if section["uses_nested"]:
                for group in section["groups"]:
                    colspan += len(group["activity_columns"]) + 1
            else:
                colspan += len(section["activity_columns"]) + 1
        colspan += 1
    colspan += len(summary_layout["exam_components"]) + 1

    return {
        "period": period,
        "summary_layout": summary_layout,
        "rows": rows,
        "colspan": colspan,
    }


def _tabulation_display_value(value):
    if value in (None, ""):
        return ""
    if isinstance(value, Decimal):
        return format(value.quantize(Decimal("0.01")), "f")
    try:
        return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


def _tabulation_status_display(status):
    return "" if status == Enrollment.Status.ACTIVE else str(status or "")


def _tabulation_period_label(period):
    name = (period.name or period.code or "").strip().upper()
    code = (period.code or "").strip().upper()
    if name in {"FINAL", "FINALS", "FX"} or code in {"FINAL", "FINALS", "FX"}:
        return "FINAL EXAM"
    return name


def _tabulation_period_grade_column_label(period):
    custom_label = (getattr(period, "grade_column_label", "") or "").strip()
    if custom_label:
        return custom_label
    base_label = _tabulation_period_label(period)
    return base_label if base_label == "FINAL EXAM" else f"{base_label} GRADE"


def _period_sheet_columns_from_layout(section):
    columns = []
    highest_values = []
    for block in section["summary_layout"]["class_standing_blocks"]:
        for subsection in block["sections"]:
            if subsection["uses_nested"]:
                for group in subsection["groups"]:
                    for column in group["activity_columns"]:
                        columns.append({"label": column["label"]})
                        highest_values.append(_tabulation_display_value(column["total_score"]))
                    columns.append({"label": group["avg_label"]})
                    highest_values.append("")
            else:
                for column in subsection["activity_columns"]:
                    columns.append({"label": column["label"]})
                    highest_values.append(_tabulation_display_value(column["total_score"]))
                columns.append({"label": subsection["avg_label"]})
                highest_values.append("")
        columns.append({"label": block["total_label"]})
        highest_values.append("")
    for exam in section["summary_layout"]["exam_components"]:
        columns.append({"label": exam["label"]})
        total_score = ""
        if exam["sections"] and exam["sections"][0]["activity_columns"]:
            total_score = _tabulation_display_value(exam["sections"][0]["activity_columns"][0]["total_score"])
        highest_values.append(total_score)
    columns.append({"label": f"{_tabulation_period_label(section['period'])} Grade"})
    highest_values.append("")
    return columns, highest_values


def _period_sheet_values_from_row(row):
    values = []
    for block in row["class_standing_blocks"]:
        for subsection in block["sections"]:
            if subsection["uses_nested"]:
                for group in subsection["groups"]:
                    values.extend(_tabulation_display_value(value) for value in group["activity_values"])
                    values.append(_tabulation_display_value(group["average"]))
            else:
                values.extend(_tabulation_display_value(value) for value in subsection["activity_values"])
                values.append(_tabulation_display_value(subsection["average"]))
        values.append(_tabulation_display_value(block["total"]))
    values.extend(_tabulation_display_value(value) for value in row["exam_values"])
    values.append(row["period_grade"] or "")
    return values


def _build_class_tabulation_sheet_grid(*, period_sections, final_grade_map, enrollments):
    period_column_groups = []
    highest_row = []
    for section in period_sections:
        columns, section_highest = _period_sheet_columns_from_layout(section)
        period_column_groups.append(
            {
                "period": section["period"],
                "label": f"{_tabulation_period_label(section['period'])} ({section['period'].code})",
                "columns": columns,
            }
        )
        highest_row.extend(section_highest)

    rows_by_student = defaultdict(list)
    for section in period_sections:
        for row in section["rows"]:
            rows_by_student[row["student"].id].extend(_period_sheet_values_from_row(row))

    sheet_rows = []
    for number, enrollment in enumerate(enrollments, start=1):
        sheet_rows.append(
            {
                "number": number,
                "student_no": enrollment.student.student_no,
                "student_name": f"{enrollment.student.last_name}, {enrollment.student.first_name}",
                "status": _tabulation_status_display(enrollment.enrollment_status),
                "values": rows_by_student.get(enrollment.student_id, []),
                "final_grade": _format_official_grade_display(final_grade_map.get(enrollment.student_id)),
            }
        )

    return {
        "period_column_groups": period_column_groups,
        "highest_row": highest_row,
        "sheet_rows": sheet_rows,
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
            active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
            active_term_cache[tenant_id] = (
                active_academic_year.id if active_academic_year else None,
                active_term.id if active_term else None,
            )
        active_academic_year_id, active_term_id = active_term_cache[tenant_id]
        if not active_academic_year_id or not active_term_id:
            return True
        return offering.academic_year_id == active_academic_year_id and offering.term_id == active_term_id

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
    updates_summary = FacultyDashboardUpdatesService.get_dashboard_updates(
        user=request.user,
        offerings=active_offerings,
        now=dashboard_now,
    )
    grade_status_rows = []
    pending_grade_issues = []
    for offering in active_offerings:
        period = None
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
            periods = list(FacultyGradingService.get_template_periods(template))
            active_period_setting = AcademicGovernanceService.resolve_active_grading_period(
                tenant_id=offering.tenant_id,
                campus_id=offering.campus_id,
                term_id=offering.term_id,
                now=dashboard_now,
            )
            period = next(
                (
                    candidate
                    for candidate in periods
                    if AcademicGovernanceService.template_period_matches_active_period(
                        template_period=candidate,
                        active_period_setting=active_period_setting,
                    )
                ),
                None,
            )
            if period is None:
                period = next(
                    (
                        candidate
                        for candidate in periods
                        if not GradingGovernanceService.is_submitted(
                            offering=offering,
                            template_period=candidate,
                        )
                    ),
                    periods[0] if periods else None,
                )
        except ValidationError as exc:
            template = None
            periods = []
            pending_grade_issues.append(
                {
                    "class_label": f"{offering.course.code} / {offering.section.code}",
                    "period_name": "",
                    "message": str(exc),
                    "url": reverse("faculty_portal:offering_grading_template", args=[offering.id]),
                }
            )

        status = "Invalid Setup"
        status_class = "danger"
        action_label = "Open Class"
        action_url = reverse("faculty_portal:offering_periods", args=[offering.id])
        issue_count = 1 if not template else 0
        if template and period:
            readiness = GradingGovernanceService.evaluate_submission_readiness(
                offering=offering,
                template_period=period,
            )
            submission = GradingGovernanceService.get_submission(
                offering=offering,
                template_period=period,
            )
            lock = GradingGovernanceService.resolve_lock(
                offering=offering,
                template_period=period,
            )
            encoding_control = GradeEncodingAccessService.get_closed_control(offering=offering, template_period=period)
            is_submitted = bool(submission and submission.status == GradeSubmission.Status.SUBMITTED)
            if encoding_control:
                status, status_class = "Encoding Closed", "danger"
                action_label = "View Class"
                action_url = reverse("faculty_portal:period_summary", args=[offering.id, period.id])
            elif lock and lock.is_locked:
                status, status_class = "Locked", "secondary"
                action_label = "Review Grades"
                action_url = reverse("faculty_portal:period_summary", args=[offering.id, period.id])
            elif is_submitted:
                status, status_class = "Submitted", "info"
                action_label = "Review Grades"
                action_url = reverse("faculty_portal:period_summary", args=[offering.id, period.id])
            elif readiness["missing_template_bucket_count"]:
                status, status_class = "Invalid Setup", "danger"
                action_label = "Open Class"
                action_url = reverse("faculty_portal:period_activities", args=[offering.id, period.id])
            elif readiness["students_with_any_grade"] == 0:
                status, status_class = "Not Started", "secondary"
                action_label = "Continue Encoding"
                action_url = reverse("faculty_portal:period_activities", args=[offering.id, period.id])
            elif readiness["students_missing_any_grade"]:
                status, status_class = "In Progress", "warning"
                action_label = "Continue Encoding"
                action_url = reverse("faculty_portal:period_activities", args=[offering.id, period.id])
            else:
                status, status_class = "Ready for Review", "success"
                action_label = "Review / Submit Grades"
                action_url = reverse("faculty_portal:period_summary", args=[offering.id, period.id])

            if readiness["missing_template_bucket_count"]:
                issue_count += readiness["missing_template_bucket_count"]
                pending_grade_issues.append(
                    {
                        "class_label": f"{offering.course.code} / {offering.section.code}",
                        "period_name": period.name,
                        "message": (
                            f"{readiness['missing_template_bucket_count']} required grading "
                            f"item{'' if readiness['missing_template_bucket_count'] == 1 else 's'} "
                            f"{'is' if readiness['missing_template_bucket_count'] == 1 else 'are'} missing."
                        ),
                        "url": reverse("faculty_portal:period_activities", args=[offering.id, period.id]),
                    }
                )
            if readiness["students_missing_any_grade"]:
                issue_count += readiness["students_missing_any_grade"]
                pending_grade_issues.append(
                    {
                        "class_label": f"{offering.course.code} / {offering.section.code}",
                        "period_name": period.name,
                        "message": (
                            f"{readiness['students_missing_any_grade']} student record"
                            f"{'' if readiness['students_missing_any_grade'] == 1 else 's'} "
                            "still has missing scores or attendance."
                        ),
                        "url": reverse("faculty_portal:period_activities", args=[offering.id, period.id]),
                    }
                )
            if (
                not is_submitted
                and readiness["eligible_student_count"] > 0
                and readiness["students_with_any_grade"] > 0
                and not readiness["students_missing_any_grade"]
                and not readiness["missing_template_bucket_count"]
            ):
                pending_grade_issues.append(
                    {
                        "class_label": f"{offering.course.code} / {offering.section.code}",
                        "period_name": period.name,
                        "message": "The gradebook is ready for review but has not been submitted.",
                        "url": reverse("faculty_portal:period_summary", args=[offering.id, period.id]),
                    }
                )
                issue_count += 1

        grade_status_rows.append(
            {
                "offering": offering,
                "period": period,
                "status": status,
                "status_class": status_class,
                "issue_count": issue_count,
                "action_label": action_label,
                "action_url": action_url,
                "performance_url": (
                    reverse("faculty_portal:class_performance", args=[offering.id, period.id])
                    if period
                    else ""
                ),
            }
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
        "updates_summary": updates_summary,
        "grade_status_rows": grade_status_rows,
        "pending_grade_issues": pending_grade_issues,
    }
    return render(request, "faculty_portal/dashboard.html", {"stats": stats})


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def activity_history_view(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return redirect("faculty_portal:dashboard")

    q = (request.GET.get("q") or "").strip()
    offerings = list(_faculty_current_offering_queryset(request.user, tenant_id=tenant_id).distinct())
    history = FacultyActivityHistoryService.get_activity_history(
        user=request.user,
        offerings=offerings,
        now=timezone.now(),
        q=q,
    )
    context = {
        "history": history,
        "history_items": history["items"],
        "history_has_more": history["has_more"],
        "history_has_previous_login": history["has_previous_login"],
        "history_since_at": history["since_at"],
        "history_current_login_at": history["current_login_at"],
        "history_empty_message": history["empty_message"],
        "history_item_count": history["item_count"],
        "history_source_counts": history["source_counts"],
        "history_severity_counts": history["severity_counts"],
        "active_offering_count": len(offerings),
        "q": q,
    }
    return render(request, "faculty_portal/activity_history.html", context)


@portal_required("FACULTY")
@permission_required("faculty_analytics.read")
def analytics_view(request):
    include_archived = request.GET.get("include_archived") == "1"
    offerings_qs = _faculty_offering_queryset(request.user).distinct()
    active_term_cache = {}

    def _is_in_active_scope(offering):
        tenant_id = offering.tenant_id
        if tenant_id not in active_term_cache:
            active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
            active_term_cache[tenant_id] = (
                active_academic_year.id if active_academic_year else None,
                active_term.id if active_term else None,
            )
        active_academic_year_id, active_term_id = active_term_cache[tenant_id]
        if not active_academic_year_id or not active_term_id:
            return True
        return offering.academic_year_id == active_academic_year_id and offering.term_id == active_term_id

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
    missing_template_offering_ids = set()
    for offering in selected_offerings:
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
            expected_periods_by_offering[offering.id] = len(list(FacultyGradingService.get_template_periods(template)))
        except ValidationError:
            expected_periods_by_offering[offering.id] = 0
            missing_template_offering_ids.add(offering.id)

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
        if offering.id in missing_template_offering_ids:
            offering_threshold_map[offering.id] = _tenant_passing_threshold_or_default(offering.tenant_id)
            continue
        try:
            offering_threshold_map[offering.id] = FacultyGradingService.resolve_passing_threshold(offering)
        except ValidationError:
            offering_threshold_map[offering.id] = _tenant_passing_threshold_or_default(offering.tenant_id)
            missing_template_offering_ids.add(offering.id)

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
                "missing_template": offering.id in missing_template_offering_ids,
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
        "missing_template_classes": len(missing_template_offering_ids),
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
                "This reopened gradebook was not resubmitted before the deadline. Score editing and submission require a new approved reopen request."
                if is_locked_reopened
                else "This deadline already passed. Submit from Summary if the gradebook is complete, or request reopen if more encoding is needed."
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
                    "A submission deadline exists in TeacherMate+, but it does not match the campus, academic year, "
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
                "This reopened gradebook was not resubmitted before the deadline. Score editing and submission require a new approved reopen request."
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
        campus_display = _campus_display_name(offering.tenant, offering.campus)
        rows.append(
            {
                "campus_code": offering.campus.code,
                "campus_name": offering.campus.name,
                "campus_display": campus_display,
                "academic_year_code": offering.academic_year.code,
                "academic_year_name": offering.academic_year.name,
                "term_code": offering.term.code,
                "term_name": offering.term.name,
                "period_code": active_setting.period.code,
                "period_name": active_setting.period.name,
                "auto_advanced_from_deadline": active_setting.auto_advanced_from_deadline,
            }
        )
    rows.sort(key=lambda row: (row["campus_display"], row["academic_year_code"], row["term_code"], row["period_code"]))
    return rows


def _campus_display_name(tenant, campus):
    tenant_code = (getattr(tenant, "code", "") or "").strip()
    campus_name = (getattr(campus, "name", "") or "").strip()
    if not campus_name:
        return (getattr(campus, "code", "") or "").strip()
    if tenant_code and campus_name.upper().startswith(tenant_code.upper()):
        return campus_name.replace(" ", "-")
    if tenant_code:
        return f"{tenant_code}-{campus_name}".replace(" ", "-")
    return campus_name


def _period_activity_metric_cards(*, offering, template_period):
    activity_counts = {
        (
            row["template_component_id"],
            row["template_subcomponent_id"],
            row["template_detail_id"],
        ): row["activity_count"]
        for row in GradeActivity.objects.filter(
            offering_id=offering.id,
            template_period_id=template_period.id,
            is_active=True,
        )
        .values("template_component_id", "template_subcomponent_id", "template_detail_id")
        .annotate(activity_count=Count("id"))
    }
    attendance_session_count = AttendanceSession.objects.filter(
        offering_id=offering.id,
        template_period_id=template_period.id,
        is_active=True,
    ).count()
    detail_queryset = GradingTemplateDetail.objects.filter(is_active=True).order_by("sort_order", "name", "id")
    subcomponent_queryset = (
        GradingTemplateSubcomponent.objects.filter(is_active=True)
        .order_by("sort_order", "name", "id")
        .prefetch_related(Prefetch("details", queryset=detail_queryset))
    )
    components = (
        GradingTemplateComponent.objects.filter(template_period_id=template_period.id, is_active=True)
        .order_by("sort_order", "name", "id")
        .prefetch_related(Prefetch("subcomponents", queryset=subcomponent_queryset))
    )
    cards = []
    for component in components:
        subcomponents = list(component.subcomponents.all())
        if not subcomponents:
            activity_count = activity_counts.get((component.id, None, None), 0)
            cards.append(
                {
                    "label": component.name or component.code,
                    "value": activity_count,
                    "note": "Activity" if activity_count == 1 else "Activities",
                    "is_missing": activity_count == 0,
                }
            )
            continue
        for subcomponent in subcomponents:
            if subcomponent.is_attendance_component:
                cards.append(
                    {
                        "label": subcomponent.name or subcomponent.code,
                        "value": attendance_session_count,
                        "note": "Session" if attendance_session_count == 1 else "Sessions",
                        "is_missing": attendance_session_count == 0,
                    }
                )
                continue
            details = list(subcomponent.details.all())
            if not details:
                activity_count = activity_counts.get((component.id, subcomponent.id, None), 0)
                cards.append(
                    {
                        "label": subcomponent.name or subcomponent.code,
                        "value": activity_count,
                        "note": "Activity" if activity_count == 1 else "Activities",
                        "is_missing": activity_count == 0,
                    }
                )
                continue
            for detail in details:
                activity_count = activity_counts.get((component.id, subcomponent.id, detail.id), 0)
                cards.append(
                    {
                        "label": detail.name or detail.code,
                        "value": activity_count,
                        "note": "Activity" if activity_count == 1 else "Activities",
                        "is_missing": activity_count == 0,
                    }
                )
    return cards


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
    offering_qs = _faculty_current_offering_queryset(request.user, tenant_id=tenant_id).distinct()
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

    offering_qs = _faculty_current_offering_queryset(request.user, tenant_id=tenant_id).distinct()
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

    offering_qs = _faculty_current_offering_queryset(request.user, tenant_id=tenant_id).distinct()
    if memo.offering_id:
        offering_qs = (offering_qs | _faculty_offering_queryset(request.user).filter(id=memo.offering_id)).distinct()
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
        messages.error(request, "Student Intervention Monitor is currently disabled by configuration.")
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
            active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_key)
            active_term_cache[tenant_key] = (
                active_academic_year.id if active_academic_year else None,
                active_term.id if active_term else None,
            )
        active_academic_year_id, active_term_id = active_term_cache[tenant_key]
        if not active_academic_year_id or not active_term_id:
            return True
        return offering.academic_year_id == active_academic_year_id and offering.term_id == active_term_id

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

    status_filter = (request.GET.get("status") or "").strip().upper()
    include_on_track = request.GET.get("show_on_track") == "1" or status_filter == StudentInterventionService.STATUS_ON_TRACK
    intervention_result = StudentInterventionService.build_monitor_groups(
        user=request.user,
        monitored_offerings=monitored_offerings,
        q=q,
        status_filter=status_filter,
        include_on_track=include_on_track,
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

    can_view_advanced_analytics = PermissionService.has_permission(
        request.user,
        "faculty_analytics.read",
        tenant_id=tenant_id,
        campus_id=campus_id,
    )
    AuditService.log_event(
        action="VIEW_STUDENT_INTERVENTION_MONITOR",
        portal="FACULTY",
        entity_type="StudentInterventionMonitor",
        entity_id=None,
        actor=request.user,
        tenant=tenant_id,
        campus=campus_id,
        metadata={
            "status_filter": status_filter or "DEFAULT",
            "offering_id": selected_offering_id,
            "show_archived": show_archived,
            "include_on_track": include_on_track,
            "result_count": intervention_result["total_rows"],
        },
        request=request,
    )
    status_options = [
        {"code": "", "label": "Needs Action"},
        {"code": StudentInterventionService.STATUS_CRITICAL, "label": StudentInterventionService.STATUS_LABELS[StudentInterventionService.STATUS_CRITICAL]},
        {"code": StudentInterventionService.STATUS_WARNING, "label": StudentInterventionService.STATUS_LABELS[StudentInterventionService.STATUS_WARNING]},
        {"code": StudentInterventionService.STATUS_MISSING_WORK, "label": StudentInterventionService.STATUS_LABELS[StudentInterventionService.STATUS_MISSING_WORK]},
        {"code": StudentInterventionService.STATUS_ON_TRACK, "label": StudentInterventionService.STATUS_LABELS[StudentInterventionService.STATUS_ON_TRACK]},
    ]

    context = {
        "monitor_groups": intervention_result["groups"],
        "offering_choices": offering_choices,
        "selected_offering_id": selected_offering_id,
        "show_archived": show_archived,
        "q": q,
        "status_filter": status_filter,
        "status_options": status_options,
        "include_on_track": include_on_track,
        "summary_cards": [
            {"label": "Missing Work", "value": intervention_result["counts"][StudentInterventionService.STATUS_MISSING_WORK], "meta": "Encoding needs review."},
            {"label": "Needs Attention", "value": intervention_result["counts"][StudentInterventionService.STATUS_CRITICAL], "meta": "Review soon."},
            {"label": "Monitor", "value": intervention_result["counts"][StudentInterventionService.STATUS_WARNING], "meta": "Watch closely."},
            {"label": "On Track", "value": intervention_result["counts"][StudentInterventionService.STATUS_ON_TRACK], "meta": "No immediate concern."},
        ],
        "can_view_advanced_analytics": can_view_advanced_analytics,
        "using_active_period_filter": intervention_result["using_active_period_filter"],
    }
    return render(request, "faculty_portal/student_at_risk_monitor.html", context)


def _owned_intervention_cases_for_request(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    campus_id = scope.get("campus_id") or getattr(request.user, "default_campus_id", None)
    return AcademicInterventionCase.objects.filter(
        faculty_owner=request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    )


@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
@require_GET
def academic_intervention_list_view(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    campus_id = scope.get("campus_id") or getattr(request.user, "default_campus_id", None)
    AcademicInterventionAuthorizationService.require_enabled(tenant_id=tenant_id, user=request.user, campus_id=campus_id)
    offerings = list(
        _faculty_offering_queryset(request.user)
        .filter(tenant_id=tenant_id, campus_id=campus_id)
        .distinct()
    )
    selected_offering_id = _safe_int(request.GET.get("offering_id"))
    selected_offering = next(
        (offering for offering in offerings if offering.id == selected_offering_id),
        None,
    )
    visible_offerings = [selected_offering] if selected_offering else offerings
    selected_period_id = _safe_int(request.GET.get("period_id"))

    period_map = {}
    for offering in visible_offerings:
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
        except ValidationError:
            continue
        for period in FacultyGradingService.get_template_periods(template):
            period_map[period.id] = period
    period_options = sorted(
        period_map.values(),
        key=lambda period: (period.sequence_no, period.name, period.id),
    )
    if selected_period_id not in period_map:
        selected_period_id = None

    candidates = []
    for offering in visible_offerings:
        candidates.extend(
            {"offering": offering, **candidate}
            for candidate in AcademicConcernDetectionService.candidates_for_offering(
                offering=offering,
                faculty_owner=request.user,
            )
            if not candidate["has_owner_case"]
        )
    if selected_period_id:
        candidates = [item for item in candidates if item["period"].id == selected_period_id]

    cases_queryset = _owned_intervention_cases_for_request(request).select_related(
        "offering", "offering__course", "offering__section", "student", "grading_period"
    )
    if selected_offering:
        cases_queryset = cases_queryset.filter(offering_id=selected_offering.id)
    if selected_period_id:
        cases_queryset = cases_queryset.filter(grading_period_id=selected_period_id)
    cases = list(cases_queryset)

    period_groups_by_id = {}
    for item in candidates:
        period = item["period"]
        group = period_groups_by_id.setdefault(
            period.id,
            {"period": period, "candidates": [], "cases": []},
        )
        group["candidates"].append(item)
    for case in cases:
        period = case.grading_period
        group = period_groups_by_id.setdefault(
            period.id,
            {"period": period, "candidates": [], "cases": []},
        )
        group["cases"].append(case)
    period_groups = sorted(
        period_groups_by_id.values(),
        key=lambda group: (
            group["period"].sequence_no,
            group["period"].name,
            group["period"].id,
        ),
    )

    return render(request, "faculty_portal/academic_interventions/list.html", {
        "cases": cases,
        "candidates": candidates,
        "offerings": offerings,
        "selected_offering": selected_offering,
        "selected_offering_id": selected_offering.id if selected_offering else None,
        "period_options": period_options,
        "selected_period_id": selected_period_id,
        "period_groups": period_groups,
        "manual_offering": selected_offering or (offerings[0] if offerings else None),
    })


@require_POST
@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
def academic_intervention_analytics_create_view(request):
    offering_id = _safe_int(request.POST.get("offering_id"))
    student_id = _safe_int(request.POST.get("student_id"))
    period_id = _safe_int(request.POST.get("period_id"))
    fingerprint = (request.POST.get("fingerprint") or "").strip()
    offering = AcademicInterventionAuthorizationService.authorized_current_offering(user=request.user, offering_id=offering_id)
    candidate = next((item for item in AcademicConcernDetectionService.candidates_for_offering(offering=offering, faculty_owner=request.user)
                      if item["student"].id == student_id and item["period"].id == period_id and item["fingerprint"] == fingerprint), None)
    if candidate is None:
        raise Http404("Academic concern is unavailable.")
    try:
        case = AcademicInterventionCaseService.create_analytics(
            user=request.user, offering_id=offering.id, student=candidate["student"], grading_period_id=period_id,
            fingerprint=fingerprint, snapshot=candidate["snapshot"], request=request,
        )
    except (ValidationError, IntegrityError) as exc:
        messages.error(request, str(exc))
        return redirect("faculty_portal:academic_intervention_list")
    return redirect("faculty_portal:academic_intervention_detail", case_id=case.id)


@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
def academic_intervention_manual_create_view(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    campus_id = scope.get("campus_id") or getattr(request.user, "default_campus_id", None)
    AcademicInterventionAuthorizationService.require_enabled(tenant_id=tenant_id, user=request.user, campus_id=campus_id)
    offerings = list(_faculty_offering_queryset(request.user).filter(tenant_id=tenant_id, campus_id=campus_id).distinct())
    selected_offering_id = _safe_int(request.GET.get("offering_id") or request.POST.get("offering_id"))
    offering = next((row for row in offerings if row.id == selected_offering_id), None)
    if not offering:
        raise Http404("Choose an authorized course offering.")
    template = FacultyGradingService.resolve_template_for_offering(offering)
    periods = FacultyGradingService.get_template_periods(template)
    student_queryset = Student.objects.filter(
        enrollments__course_offering=offering,
        enrollments__is_active=True,
        is_active=True,
    ).exclude(enrollments__enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES).distinct()
    form = ManualInterventionCaseForm(
        request.POST or None,
        student_queryset=student_queryset,
        period_queryset=periods,
        initial={"offering_id": offering.id, "grading_period_id": periods.first().id if periods.exists() else None},
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        try:
            case = AcademicInterventionCaseService.create_manual(
                user=request.user, offering_id=offering.id, student=form.cleaned_data["student"],
                grading_period_id=form.cleaned_data["grading_period_id"], summary=form.cleaned_data["distinct_concern_summary"], request=request,
            )
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("faculty_portal:academic_intervention_detail", case_id=case.id)
    return render(request, "faculty_portal/academic_interventions/manual_form.html", {"form": form, "offering": offering, "periods": periods})


@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
@require_GET
def academic_intervention_detail_view(request, case_id):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    campus_id = scope.get("campus_id") or getattr(request.user, "default_campus_id", None)
    case = get_object_or_404(
        _owned_intervention_cases_for_request(request).select_related(
            "offering", "offering__course", "offering__section", "student", "grading_period"
        ), pk=case_id
    )
    AcademicInterventionAuthorizationService.require_owner(user=request.user, case=case)
    is_open = not case.closed_at and not case.voided_at
    decision_form = _style_form(
        FacultyDecisionForm(
            initial={
                "decision": case.faculty_decision,
                "rationale": case.faculty_rationale,
                "referral_destination": case.referral_destination,
                "referral_destination_label": case.referral_destination_label,
                "referral_date": case.referral_date,
                "referral_reason": case.referral_reason,
            }
        )
    )
    action_form = _style_form(InterventionActionForm())
    follow_up_form = _style_form(FollowUpForm())
    actions = list(case.actions.all())
    follow_ups = list(case.follow_ups.select_related("action").all())
    follow_up_forms = [
        (row, _style_form(FollowUpForm(instance=row))) for row in follow_ups
    ]
    action_forms = [
        (row, _style_form(InterventionActionForm(instance=row)))
        for row in actions
        if row.status == AcademicInterventionAction.Status.PLANNED
    ]
    return render(request, "faculty_portal/academic_interventions/detail.html", {
        "case": case,
        "decision_form": decision_form,
        "action_form": action_form,
        "follow_up_form": follow_up_form,
        "actions": actions,
        "follow_ups": follow_ups,
        "follow_up_forms": follow_up_forms,
        "decision_revisions": case.decision_revisions.select_related("decided_by", "supersedes").all(),
        "action_forms": action_forms,
        "is_open": is_open,
        "can_void": is_open and not any(
            action.status == AcademicInterventionAction.Status.CONDUCTED for action in actions
        ),
    })


@require_POST
@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
def academic_intervention_decision_view(request, case_id):
    case = get_object_or_404(_owned_intervention_cases_for_request(request), pk=case_id)
    form = FacultyDecisionForm(request.POST)
    if form.is_valid():
        try:
            AcademicInterventionCaseService.record_decision(case_id=case.id, user=request.user, request=request, **form.cleaned_data)
            messages.success(request, "Faculty decision saved.")
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Correct the faculty decision form and try again.")
    return redirect("faculty_portal:academic_intervention_detail", case_id=case.id)


@require_POST
@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
def academic_intervention_action_create_view(request, case_id):
    case = get_object_or_404(_owned_intervention_cases_for_request(request), pk=case_id)
    form = InterventionActionForm(request.POST)
    if form.is_valid():
        try:
            AcademicInterventionCaseService.add_action(case_id=case.id, user=request.user, form=form, request=request)
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Correct the intervention action form and try again.")
    return redirect("faculty_portal:academic_intervention_detail", case_id=case.id)


@require_POST
@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
def academic_intervention_action_update_view(request, case_id, action_id):
    case = get_object_or_404(_owned_intervention_cases_for_request(request), pk=case_id)
    action = get_object_or_404(case.actions.filter(status="PLANNED"), pk=action_id)
    form = InterventionActionForm(request.POST, instance=action)
    if form.is_valid():
        try:
            AcademicInterventionCaseService.update_action(
                case_id=case.id,
                action_id=action.id,
                user=request.user,
                form=form,
                request=request,
            )
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Correct the intervention action form and try again.")
    return redirect("faculty_portal:academic_intervention_detail", case_id=case.id)


@require_POST
@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
def academic_intervention_follow_up_create_view(request, case_id):
    case = get_object_or_404(_owned_intervention_cases_for_request(request), pk=case_id)
    form = FollowUpForm(request.POST)
    if form.is_valid():
        try:
            AcademicInterventionCaseService.add_follow_up(case_id=case.id, user=request.user, form=form, request=request)
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Correct the follow-up form and try again.")
    return redirect("faculty_portal:academic_intervention_detail", case_id=case.id)


@require_POST
@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
def academic_intervention_follow_up_update_view(request, case_id, follow_up_id):
    case = get_object_or_404(_owned_intervention_cases_for_request(request), pk=case_id)
    follow_up = get_object_or_404(case.follow_ups.all(), pk=follow_up_id)
    form = FollowUpForm(request.POST, instance=follow_up)
    if form.is_valid():
        try:
            AcademicInterventionCaseService.update_follow_up(case_id=case.id, follow_up_id=follow_up.id, user=request.user, form=form, request=request)
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Correct the follow-up form and try again.")
    return redirect("faculty_portal:academic_intervention_detail", case_id=case.id)


@require_POST
@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
def academic_intervention_close_view(request, case_id):
    case = get_object_or_404(_owned_intervention_cases_for_request(request), pk=case_id)
    try:
        AcademicInterventionCaseService.close(case_id=case.id, user=request.user, request=request)
        messages.success(request, "Intervention record closed.")
    except (ValidationError, IntegrityError) as exc:
        messages.error(request, str(exc))
    return redirect("faculty_portal:academic_intervention_detail", case_id=case.id)


@require_POST
@portal_required("FACULTY")
@permission_required("academic_interventions.manage_own")
def academic_intervention_void_view(request, case_id):
    case = get_object_or_404(_owned_intervention_cases_for_request(request), pk=case_id)
    try:
        AcademicInterventionCaseService.void(
            case_id=case.id,
            user=request.user,
            reason=(request.POST.get("void_reason") or ""),
            request=request,
        )
        messages.success(request, "Eligible mistaken intervention record voided.")
    except (ValidationError, IntegrityError) as exc:
        messages.error(request, str(exc))
    return redirect("faculty_portal:academic_intervention_detail", case_id=case.id)


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
            ),
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
            active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
            active_term_cache[tenant_id] = (
                active_academic_year.id if active_academic_year else None,
                active_term.id if active_term else None,
            )
        active_academic_year_id, active_term_id = active_term_cache[tenant_id]
        if not active_academic_year_id or not active_term_id:
            return True
        return offering.academic_year_id == active_academic_year_id and offering.term_id == active_term_id

    pending_assignments = []
    active_offerings = []
    archived_offerings = []
    for assignment in assignment_qs:
        offering = _attach_faculty_offering_scope_state(assignment.offering)
        offering.assignment = assignment
        offering.enrollment_count = assignment.enrollment_count
        offering.has_course_template_assignment = _has_active_published_course_template_assignment(offering)
        offering.template_assignment_warning = (
            "No grading template is assigned to this course offering yet. Please coordinate with the MIS Department."
            if not offering.has_course_template_assignment
            else ""
        )
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
    missing_template_assignment_offerings = [
        offering
        for offering in active_offerings
        if not offering.has_course_template_assignment
    ]
    missing_template_assignment_pending = [
        assignment
        for assignment in pending_assignments
        if not assignment.offering.has_course_template_assignment
    ]

    grouped_offerings = []
    final_clearance_targets = []
    final_clearance_seen = set()
    final_clearance_preview_cache = {}
    for offering in selected_offerings:
        offering.final_clearance_can_print = False
        offering.final_clearance_incomplete_courses = 0
        if not show_archived and offering.final_template_period:
            key = (offering.tenant_id, offering.campus_id, offering.term_id)
            if key not in final_clearance_preview_cache:
                final_clearance_preview_cache[key] = _faculty_final_clearance_preview_for_scope(
                    faculty_user=request.user,
                    term=offering.term,
                    campus=offering.campus,
                )
            scope_preview = final_clearance_preview_cache[key]
            offering.final_clearance_can_print = bool(scope_preview["can_print"])
            offering.final_clearance_incomplete_courses = scope_preview.get("incomplete_courses", 0)
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
                scope_preview = final_clearance_preview_cache[key]
                final_clearance_targets.append(
                    {
                        "tenant": offering.tenant,
                        "campus": offering.campus,
                        "term": offering.term,
                        "academic_year": offering.academic_year,
                        "offering_id": offering.id,
                        "period_id": offering.final_template_period.id,
                        "can_print": bool(scope_preview["can_print"]),
                        "incomplete_courses": scope_preview.get("incomplete_courses", 0),
                        "complete_courses": scope_preview.get("complete_courses", 0),
                        "total_assigned_courses": scope_preview.get("total_assigned_courses", 0),
                    }
                )

    deadline_banner, _ = _build_deadline_reminder_for_offerings(active_offerings, now=timezone.now())
    active_grading_period_rows = _build_active_grading_period_rows(active_offerings, now=timezone.now())
    visible_assignment_ids = {assignment.id for assignment in assignment_qs}
    historical_report_assignments = list(
        TabulationSheetAuthorizationService.faculty_assignments_for_offering(
            faculty_user=request.user,
            offering_id=None,
        )
        .filter(is_active=False)
        .exclude(id__in=visible_assignment_ids)
    )

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
        "historical_report_assignments": historical_report_assignments,
        "missing_template_assignment_count": (
            len(missing_template_assignment_offerings) + len(missing_template_assignment_pending)
            if not show_archived
            else 0
        ),
    }
    return render(request, "faculty_portal/my_courses.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def offering_syllabus_redirect_view(request, offering_id: int):
    assignment = (
        _faculty_assignment_queryset(request.user)
        .filter(offering_id=offering_id)
        .select_related("offering__course")
        .first()
    )
    if not assignment:
        raise Http404("Syllabus link not found.")
    offering = assignment.offering
    course = offering.course
    syllabus_url = (course.syllabus_url or "").strip()
    if not syllabus_url or course.tenant_id != offering.tenant_id:
        raise Http404("Syllabus link not found.")
    AuditService.log_event(
        action="VIEW_SYLLABUS_LINK",
        portal="FACULTY",
        entity_type="Course",
        entity_id=course.id,
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        metadata={
            "offering_id": offering.id,
            "course_id": course.id,
            "course_code": course.code,
            "section_id": offering.section_id,
            "term_id": offering.term_id,
            "assignment_id": assignment.id,
            "syllabus_link_present": True,
        },
        request=request,
    )
    return redirect(syllabus_url)


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
def faculty_assignment_undo_accept_view(request, assignment_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:my_courses")

    assignment = _require_accepted_faculty_assignment_or_404(request, assignment_id)
    AuditService.log_event(
        action="UNDO_ACCEPTANCE_BLOCKED",
        portal="FACULTY",
        entity_type="FacultyAssignment",
        entity_id=assignment.id,
        actor=request.user,
        tenant=assignment.tenant,
        campus=assignment.campus,
        metadata={
            "event": "faculty_assignment_acceptance_undo_blocked",
            "offering_id": assignment.offering_id,
            "reason": "Faculty cannot undo accepted assignments. Admin or assigned academic office must unassign.",
        },
        request=request,
    )
    messages.error(
        request,
        "Accepted assignments cannot be undone from the Faculty Portal. Please contact the assigning admin or academic office if the load must be unassigned.",
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
    final_clearance_preview = _faculty_final_clearance_preview_for_scope(
        faculty_user=request.user,
        term=offering.term,
        campus=offering.campus,
    )
    can_print_final_clearance = bool(final_clearance_preview["can_print"])
    period_cards = []
    for p in periods:
        period_display_name = (p.name or p.code or "Period").strip()
        period_key = f"{p.code or ''} {p.name or ''}".upper()
        if "PREFINAL" in period_key or "PRE-FINAL" in period_key:
            period_kind = "prefinal"
        elif "MIDTERM" in period_key:
            period_kind = "midterm"
        elif "FINAL" in period_key or (p.code or "").upper() == "FX":
            period_kind = "final"
        else:
            period_kind = "prelim"
        GradingGovernanceService.auto_lock_expired_reopened_gradebook(offering=offering, template_period=p)
        GradingGovernanceService.auto_lock_expired_approved_reopen_request_for_period(
            offering=offering,
            template_period=p,
        )
        lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=p)
        submission = GradingGovernanceService.get_submission(offering=offering, template_period=p)
        pending_reopen_request = GradingGovernanceService.get_pending_reopen_request(
            offering=offering,
            template_period=p,
        )
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
        correction_filing_state = GradingGovernanceService.get_correction_request_filing_state(
            offering=offering,
            template_period=p,
        )
        can_access_corrections = bool(
            GradingGovernanceService.is_system_correction_enabled(tenant_id=offering.tenant_id)
            and correction_filing_state["is_allowed"]
            and not offering.faculty_is_read_only
        )
        active_approved_reopen_request = GradingGovernanceService.get_active_approved_reopen_request(
            offering=offering,
            template_period=p,
        )
        active_approved_reopen_expires_at = GradingGovernanceService.reopen_request_expires_at(
            active_approved_reopen_request
        )
        effective_is_locked = bool(lock and lock.is_locked) and not active_approved_reopen_request
        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=offering,
            template_period=p,
        )
        activity_metric_cards = _period_activity_metric_cards(offering=offering, template_period=p)
        period_cards.append(
            {
                "period": p,
                "display_name": period_display_name,
                "kind": period_kind,
                "is_read_only_class": offering.faculty_is_read_only,
                "is_locked": effective_is_locked,
                "raw_is_locked": bool(lock and lock.is_locked),
                "is_submitted": GradingGovernanceService.is_submitted(offering=offering, template_period=p),
                "submission_status": submission.status if submission else None,
                "is_correction_active": GradingGovernanceService.has_active_unlock_window(
                    offering=offering,
                    template_period=p,
                ),
                "deadline_at": lock.deadline_at if lock else None,
                "completion_grace_until": completion_window_state["completion_grace_until"],
                "is_within_completion_grace": completion_window_state["is_within_completion_grace"],
                "is_auto_closed_after_deadline": completion_window_state["is_auto_closed_after_deadline"],
                "is_non_compliant": completion_window_state["is_non_compliant"],
                "pending_late_completion_request": completion_window_state["pending_late_completion_request"],
                "active_late_completion_request": completion_window_state["active_late_completion_request"],
                "can_request_late_completion": completion_window_state["can_request_late_completion"],
                "pending_reopen_request": pending_reopen_request,
                "active_approved_reopen_request": active_approved_reopen_request,
                "active_approved_reopen_expires_at": active_approved_reopen_expires_at,
                "readiness": readiness,
                "activity_metric_cards": activity_metric_cards,
                "can_request_deadline_reopen": (
                    GradingGovernanceService.can_request_reopen_after_auto_close(
                        offering=offering,
                        template_period=p,
                    )
                    and not offering.faculty_is_read_only
                ),
                "is_active_period": AcademicGovernanceService.template_period_matches_active_period(
                    template_period=p,
                    active_period_setting=active_grading_period,
                ),
                "is_closed_by_active_period": governance_state["is_closed_by_active_period"],
                "is_future_period": governance_state["is_future_period"],
                "is_past_period": governance_state["is_past_period"],
                "closed_message": governance_state["message"],
                "can_access_corrections": can_access_corrections,
                "correction_lifecycle_message": correction_filing_state["message"],
                "is_final_period": bool(periods) and p.id == periods[-1].id,
                "can_print_final_clearance": can_print_final_clearance,
                "final_clearance_incomplete_courses": final_clearance_preview.get("incomplete_courses", 0),
            }
        )

    all_periods_submitted = _all_template_periods_submitted(offering, periods)
    context = {
        "offering": offering,
        "faculty_scope_state": offering.faculty_scope_state,
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
        "all_periods_submitted": all_periods_submitted,
        "deadline_banner": _build_deadline_reminder_for_period_cards(
            offering,
            period_cards,
            now=timezone.now(),
        ),
    }
    return render(request, "faculty_portal/offering_periods.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def class_performance_view(request, offering_id: int, period_id: int):
    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if not template or not period:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    state = _period_edit_state(offering, period)
    official_grade_release = _official_grade_release_state(
        offering=offering,
        template=template,
        template_period=period,
        is_period_submitted=state["is_submitted"],
        submission_status=state["submission_status"],
        now=timezone.now(),
    )
    snapshot = FacultyPerformanceService.get_class_performance_snapshot(offering, period)
    attention_rows = [
        row
        for row in snapshot["rows"]
        if snapshot["has_performance_data"]
        and row["trend_label"]
        in {
            FacultyPerformanceService.TREND_AT_RISK,
            FacultyPerformanceService.TREND_INCOMPLETE,
            FacultyPerformanceService.TREND_DECLINING,
        }
    ]
    if official_grade_release["show_period_grade"]:
        for row in attention_rows:
            row["explain_url"] = reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": offering.id,
                    "period_id": period.id,
                    "student_id": row["student_id"],
                    "grade_type": GradeExplanationService.GRADE_TYPE_PERIOD,
                },
            )
    return render(
        request,
        "faculty_portal/class_performance.html",
        {
            "offering": offering,
            "period": period,
            "snapshot": snapshot,
            "attention_rows": attention_rows,
            "official_period_grade_masked": not official_grade_release["show_period_grade"],
            "official_period_grade_masked_label": official_grade_release["period_grade_masked_label"],
        },
    )


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def student_performance_consultation_view(request, offering_id: int, period_id: int, student_id: int):
    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if not template or not period:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    state = _period_edit_state(offering, period)
    official_grade_release = _official_grade_release_state(
        offering=offering,
        template=template,
        template_period=period,
        is_period_submitted=state["is_submitted"],
        submission_status=state["submission_status"],
        now=timezone.now(),
    )
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("student")
        .filter(
            course_offering_id=offering.id,
            student_id=student_id,
            is_active=True,
            student__is_active=True,
        )
        .exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)
    )
    trend = FacultyPerformanceService.get_student_performance_trend(
        enrollment.student,
        offering,
        period,
    )
    if not trend:
        raise Http404("Student performance data is unavailable.")
    trend_visualization = FacultyPerformanceService.get_student_trend_visualization(
        enrollment.student,
        offering,
        period,
    )
    return render(
        request,
        "faculty_portal/student_performance_consultation.html",
        {
            "offering": offering,
            "period": period,
            "trend": trend,
            "trend_visualization": trend_visualization,
            "official_period_grade_masked": not official_grade_release["show_period_grade"],
            "official_period_grade_masked_label": official_grade_release["period_grade_masked_label"],
        },
    )


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def parallel_section_comparison_view(request):
    accepted_offering_ids = _faculty_assignment_queryset(request.user).filter(
        accepted_at__isnull=False,
        response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
    ).values_list("offering_id", flat=True)
    scoped_offerings = list(
        _faculty_offering_queryset(request.user)
        .filter(id__in=accepted_offering_ids)
        .distinct()
    )
    term_options = {}
    course_options_by_term = defaultdict(dict)
    for offering in scoped_offerings:
        term_options[offering.term_id] = offering.term
        course_options_by_term[offering.term_id][offering.course.code] = offering.course

    selected_term_id = _safe_int(request.GET.get("academic_term"))
    if selected_term_id not in term_options:
        selected_term_id = scoped_offerings[0].term_id if scoped_offerings else None
    selected_term = term_options.get(selected_term_id)

    available_courses = list(course_options_by_term.get(selected_term_id, {}).values())
    available_courses.sort(key=lambda course: course.code)
    selected_course_code = (request.GET.get("course_code") or "").strip()
    valid_course_codes = {course.code for course in available_courses}
    if selected_course_code not in valid_course_codes:
        selected_course_code = available_courses[0].code if available_courses else ""

    matching_offerings = [
        offering
        for offering in scoped_offerings
        if offering.term_id == selected_term_id and offering.course.code == selected_course_code
    ]
    period_options_by_code = {}
    period_seed_by_code = {}
    for offering in matching_offerings:
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
        except ValidationError:
            continue
        for period in FacultyGradingService.get_template_periods(template):
            period_options_by_code.setdefault(period.code, period.name)
            period_seed_by_code.setdefault(period.code, period)
    selected_period_code = (request.GET.get("period_code") or "").strip()
    if selected_period_code not in period_options_by_code:
        selected_period_code = next(iter(period_options_by_code), "")
    selected_period = period_seed_by_code.get(selected_period_code)

    comparison_rows = []
    if selected_term and selected_course_code and selected_period:
        comparison_rows = FacultyPerformanceService.get_parallel_section_comparison(
            request.user,
            selected_course_code,
            selected_term,
            selected_period,
        )
    interpretation = FacultyPerformanceService.get_parallel_section_interpretation(comparison_rows)
    chart_data = FacultyPerformanceService.get_chart_data_for_parallel_sections(comparison_rows)
    return render(
        request,
        "faculty_portal/parallel_section_comparison.html",
        {
            "term_options": sorted(
                term_options.values(),
                key=lambda term: (term.academic_year.code, term.sequence_no, term.code),
                reverse=True,
            ),
            "course_options": available_courses,
            "period_options": [
                {"code": code, "name": name}
                for code, name in period_options_by_code.items()
            ],
            "selected_term_id": selected_term_id,
            "selected_term": selected_term,
            "selected_course_code": selected_course_code,
            "selected_period_code": selected_period_code,
            "comparison_rows": comparison_rows,
            "interpretation": interpretation,
            "chart_data": chart_data,
        },
    )


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def offering_class_tabulation_sheet_view(request, offering_id: int):
    assignment = get_object_or_404(
        TabulationSheetAuthorizationService.faculty_assignments_for_offering(
            faculty_user=request.user,
            offering_id=offering_id,
        )
    )
    offering = _attach_faculty_offering_scope_state(assignment.offering)
    try:
        template = FacultyGradingService.resolve_template_for_offering(offering)
        periods = list(FacultyGradingService.get_template_periods(template))
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    complete_report = CompleteTabulationSheetDataService.build_report(
        offering=offering,
        faculty_user=request.user,
        generated_by=request.user,
        portal_code="FACULTY",
    )
    sheet_grid = {
        "period_column_groups": complete_report["period_column_groups"],
        "highest_row": complete_report["highest_row"],
        "sheet_rows": complete_report["sheet_rows"],
    }

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
    faculty_name = complete_report["faculty_name"]
    context = {
        "offering": offering,
        "template": template,
        "periods": periods,
        "print_header_name": print_header_name,
        "print_header_address": print_header_address,
        "faculty_name": faculty_name,
        **sheet_grid,
        "generated_at": complete_report["generated_at"],
        "complete_report": complete_report,
    }
    if request.GET.get("format") == "pdf":
        pdf_bytes = ClassTabulationSheetPdfService.build_pdf_bytes(report=complete_report)
        filename = f"complete-tabulation-{offering.course.code}-{offering.section.code}.pdf"
        AuditService.log_event(
            action="GENERATE_COMPLETE_TABULATION_SHEET",
            portal="FACULTY",
            entity_type="CourseOffering",
            entity_id=offering.id,
            actor=request.user,
            tenant=offering.tenant,
            campus=offering.campus,
            metadata={"format": "PDF", "historical_assignment": not assignment.is_active},
            request=request,
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response
    if complete_report.get("signature_bytes") and complete_report.get("faculty_user"):
        UserSignatureService.log_signature_usage(
            user=complete_report["faculty_user"],
            document_type=UserSignatureUsageLog.DocumentType.COMPLETE_TABULATION_SHEET,
            document_reference=f"offering:{offering.id}",
            usage_role="FACULTY_OF_RECORD",
            actor=request.user,
            portal_code="FACULTY",
            metadata={"offering_id": offering.id, "format": "HTML_PRINT_PREVIEW"},
        )
    return render(request, "faculty_portal/class_tabulation_sheet.html", context)


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
    can_report_template_issue = _can_report_template_issue(request.user, offering, template)
    template_issue_reports = (
        TemplateHotfixRequest.objects.filter(
            template_id=template.id,
            requested_by_user=request.user,
        )
        .prefetch_related("workflow_steps")
        .order_by("-created_at")[:5]
    )
    for report in template_issue_reports:
        report.current_step = TemplateGovernanceWorkflowService.get_current_hotfix_step(hotfix_request=report)
    context = {
        "offering": offering,
        "template": template,
        "period_rows": preview["period_rows"],
        "final_formula": preview["final_formula"],
        "can_report_template_issue": can_report_template_issue,
        "template_issue_reports": template_issue_reports,
    }
    return render(request, "faculty_portal/offering_grading_template.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def offering_grading_calculator_view(request, offering_id: int):
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

    sample_input = (request.POST.get("sample_value") if request.method == "POST" else request.GET.get("sample_value")) or "85.00"
    try:
        sample_value = Decimal(str(sample_input))
        if sample_value < Decimal("0") or sample_value > Decimal("100"):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        sample_value = GradingTemplateTestingCalculatorService.DEFAULT_SAMPLE_VALUE
        messages.warning(request, "The sample value was invalid, so TeacherMate+ used 85.00 instead.")

    calculation = GradingTemplateTestingCalculatorService.build_calculation(
        template=template,
        offering=offering,
        raw_inputs=request.POST if request.method == "POST" else None,
        default_sample=sample_value,
    )
    if calculation["input_errors"]:
        messages.warning(
            request,
            "Some sample rows had invalid percentages, so TeacherMate+ temporarily used the default sample value for those rows.",
        )

    context = {
        "offering": offering,
        "template": template,
        "calculation": calculation,
        "sample_value": calculation["default_sample"],
    }
    return render(request, "faculty_portal/offering_grading_calculator.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def report_template_issue_view(request, offering_id: int):
    assignment = _find_faculty_assignment(request.user, offering_id)
    if assignment and not assignment.is_accepted:
        messages.error(request, "Please accept this faculty assignment first before reporting a template issue.")
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

    if not _can_report_template_issue(request.user, offering, template):
        messages.error(request, "Template issue reporting is not available for this class under the current governance settings.")
        return redirect("faculty_portal:offering_grading_template", offering_id=offering.id)

    existing_pending = TemplateHotfixRequest.objects.filter(
        template_id=template.id,
        requested_by_user=request.user,
        status=TemplateHotfixRequest.Status.PENDING,
    ).first()
    if existing_pending:
        messages.info(
            request,
            "You already have a pending template issue report for this template. Please wait for governance review.",
        )
        return redirect("faculty_portal:offering_grading_template", offering_id=offering.id)

    form = FacultyTemplateIssueReportForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        issue_label = dict(FacultyTemplateIssueReportForm.IssueType.choices).get(form.cleaned_data["issue_type"])
        justification = (
            f"Faculty-reported template issue\n\n"
            f"Issue Type: {issue_label}\n"
            f"Class: {offering.course.code} / {offering.section.code} / {offering.term.code}\n\n"
            f"Details:\n{form.cleaned_data['details'].strip()}"
        )
        try:
            TemplateGovernanceWorkflowService.ensure_user_can_perform_stage(
                user=request.user,
                stage_code=TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST,
                tenant_id=offering.tenant_id,
            )
            hotfix = TemplateHotfixService.create_request(
                template=template,
                requested_by=request.user,
                apply_mode=TemplateHotfixRequest.ApplyMode.REQUESTING_FACULTY_OFFERINGS,
                justification=justification,
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect("faculty_portal:offering_grading_template", offering_id=offering.id)

        AuditService.log_event(
            action="CREATE",
            portal="FACULTY",
            entity_type="TemplateHotfixRequest",
            entity_id=hotfix.id,
            actor=request.user,
            tenant=offering.tenant,
            campus=offering.campus,
            after_data=model_before_after(hotfix),
            metadata={
                "workflow": "FACULTY_TEMPLATE_ISSUE_REPORT",
                "offering_id": offering.id,
                "template_id": template.id,
                "apply_mode": hotfix.apply_mode,
            },
            request=request,
        )
        messages.success(
            request,
            "Template issue report submitted. Governance reviewers will decide if it becomes an applied hotfix.",
        )
        return redirect("faculty_portal:offering_grading_template", offering_id=offering.id)

    context = {
        "form": form,
        "offering": offering,
        "template": template,
    }
    return render(request, "faculty_portal/report_template_issue.html", context)


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

    preview = _faculty_final_clearance_preview_for_scope(
        faculty_user=request.user,
        term=offering.term,
        campus=offering.campus,
    )

    if request.method == "POST":
        if not preview["can_print"]:
            messages.error(
                request,
                "Final Clearance can be printed only when all assigned courses in this campus-term scope are COMPLETE.",
            )
            return redirect(
                "faculty_portal:period_final_clearance",
                offering_id=offering.id,
                period_id=period.id,
            )
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
    remembered_selection = {}
    if request.method == "GET" and editing_activity is None:
        remembered_key = _faculty_activity_last_selection_session_key(
            user_id=request.user.id,
            offering_id=offering.id,
            period_id=period.id,
        )
        remembered_selection = _validate_faculty_activity_last_selection(
            request.session.get(remembered_key),
            component_qs=component_qs,
            subcomponent_qs=subcomponent_qs,
            detail_qs=detail_qs,
        )
        if remembered_selection:
            stored_selection = request.session.get(remembered_key)
            if stored_selection != remembered_selection:
                request.session[remembered_key] = remembered_selection
                request.session.modified = True
        elif remembered_key in request.session:
            request.session.pop(remembered_key, None)
            request.session.modified = True
    form = GradeActivityForm(
        request.POST or None,
        instance=editing_activity,
        initial=(
            {
                "template_component": remembered_selection["component_id"],
                "template_subcomponent": remembered_selection["subcomponent_id"],
                "template_detail": remembered_selection["detail_id"],
            }
            if remembered_selection
            else None
        ),
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
    elif remembered_selection:
        selected_component_id = str(remembered_selection["component_id"])
        selected_subcomponent_id = (
            str(remembered_selection["subcomponent_id"]) if remembered_selection["subcomponent_id"] else None
        )
        selected_detail_id = str(remembered_selection["detail_id"]) if remembered_selection["detail_id"] else None

    subcomponents = list(subcomponent_qs)
    details = list(detail_qs)
    component_ids_with_subcomponents = {subcomponent.template_component_id for subcomponent in subcomponents}
    subcomponent_ids_with_details = {detail.template_subcomponent_id for detail in details}
    component_option_data = [
        {
            "id": str(component.id),
            "name": component.name,
            "score_input_mode": component.score_input_mode,
            "has_subcomponents": component.id in component_ids_with_subcomponents,
        }
        for component in component_qs
    ]
    subcomponent_option_data = [
        {
            "id": str(subcomponent.id),
            "name": subcomponent.name,
            "component_id": str(subcomponent.template_component_id),
            "score_input_mode": subcomponent.score_input_mode,
            "has_details": subcomponent.id in subcomponent_ids_with_details,
        }
        for subcomponent in subcomponents
    ]
    detail_option_data = [
        {
            "id": str(detail.id),
            "name": detail.name,
            "display_name": (
                detail.name
                if detail.template_subcomponent.detail_computation_mode == "AVERAGE_ACTIVITIES"
                else f"{detail.name} ({detail.weight_percentage}% configured weight)"
            ),
            "weight_percentage": str(detail.weight_percentage),
            "detail_computation_mode": detail.template_subcomponent.detail_computation_mode,
            "score_input_mode": detail.score_input_mode,
            "subcomponent_id": str(detail.template_subcomponent_id),
        }
        for detail in details
    ]

    if request.method == "POST" and form.is_valid():
        if not state["is_editable"]:
            messages.error(
                request,
                state["encoding_control_message"] if state["encoding_control_closed"] else "This period is locked or already submitted.",
            )
            return redirect(
                _faculty_activity_url(
                    request,
                    "faculty_portal:period_activities",
                    offering_id=offering.id,
                    period_id=period.id,
                )
            )
        if state["is_correction_active"] and editing_activity is None:
            messages.error(request, "New activities cannot be created inside a correction window.")
            return redirect(
                _faculty_activity_url(
                    request,
                    "faculty_portal:period_activities",
                    offering_id=offering.id,
                    period_id=period.id,
                )
            )
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
                    request.session[
                        _faculty_activity_last_selection_session_key(
                            user_id=request.user.id,
                            offering_id=offering.id,
                            period_id=period.id,
                        )
                    ] = {
                        "component_id": component.id,
                        "subcomponent_id": subcomponent.id if subcomponent else None,
                        "detail_id": detail.id if detail else None,
                    }
                    request.session.modified = True
                return redirect(
                    _faculty_activity_url(
                        request,
                        "faculty_portal:period_activities",
                        offering_id=offering.id,
                        period_id=period.id,
                    )
                )

    activity_view_mode = _faculty_activity_view_mode(request)
    activities = list(
        GradeActivity.objects.filter(offering_id=offering.id, template_period_id=period.id, is_active=True)
        .select_related("template_component", "template_subcomponent", "template_detail")
        .annotate(score_count=Count("student_scores", filter=Q(student_scores__is_active=True)))
        .order_by("-activity_date", "-created_at", "-id")
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
    show_detail_weight_column = any(
        row.template_detail
        and row.template_subcomponent
        and row.template_subcomponent.detail_computation_mode != "AVERAGE_ACTIVITIES"
        for row in activities
    )
    active_enrollment_count = FacultyGradingService.get_active_enrollments(offering).filter(
        enrollment_status=Enrollment.Status.ACTIVE
    ).count()
    activity_groups = _build_faculty_activity_groups(
        activities,
        active_enrollment_count=active_enrollment_count,
    )
    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "form": form,
        "activities": activities,
        "activity_groups": activity_groups,
        "activity_view_mode": activity_view_mode,
        "activity_grouped_view_url": _faculty_activity_view_switch_url(request, view_mode="grouped"),
        "activity_flat_view_url": _faculty_activity_view_switch_url(request, view_mode="flat"),
        "show_detail_weight_column": show_detail_weight_column,
        "is_locked": state["is_locked"],
        "is_submitted": state["is_submitted"],
        "submission_status": state["submission_status"],
        "can_self_reopen": state["can_self_reopen"],
        "can_view_gradebook_summary": state["is_submitted"],
        "can_submit_period": state["can_submit_period"],
        "is_auto_locked_reopened_after_deadline": state["is_auto_locked_reopened_after_deadline"],
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "encoding_control_closed": state["encoding_control_closed"],
        "encoding_control_message": state["encoding_control_message"],
        "is_editable": state["is_editable"],
        "system_correction_enabled": state["system_correction_enabled"],
        "completion_grace_until": state["completion_grace_until"],
        "is_within_completion_grace": state["is_within_completion_grace"],
        "is_non_compliant": state["is_non_compliant"],
        "is_auto_closed_after_deadline": state["is_auto_closed_after_deadline"],
        "pending_reopen_request": state["pending_reopen_request"],
        "active_approved_reopen_request": state["active_approved_reopen_request"],
        "active_approved_reopen_expires_at": state["active_approved_reopen_expires_at"],
        "can_request_deadline_reopen": state["can_request_deadline_reopen"],
        "pending_late_completion_request": state["pending_late_completion_request"],
        "active_late_completion_request": state["active_late_completion_request"],
        "can_request_late_completion": state["can_request_late_completion"],
        "can_create_activity": state["is_editable"],
        "editing_activity": editing_activity,
        "activity_form_action_url": _faculty_activity_url(
            request,
            "faculty_portal:period_activity_edit" if editing_activity else "faculty_portal:period_activities",
            offering_id=offering.id,
            period_id=period.id,
            activity_id=editing_activity.id if editing_activity else None,
        ),
        "component_option_data": component_option_data,
        "subcomponent_option_data": subcomponent_option_data,
        "detail_option_data": detail_option_data,
        "selected_component_id": selected_component_id,
        "selected_subcomponent_id": selected_subcomponent_id,
        "selected_detail_id": selected_detail_id,
        "active_enrollment_count": active_enrollment_count,
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
        "encoding_control_closed": state["encoding_control_closed"],
        "encoding_control_message": state["encoding_control_message"],
        "is_editable": state["is_editable"],
        "submission_status": state["submission_status"],
        "is_auto_locked_reopened_after_deadline": state["is_auto_locked_reopened_after_deadline"],
        "is_auto_closed_after_deadline": state["is_auto_closed_after_deadline"],
        "system_correction_enabled": state["system_correction_enabled"],
        "completion_grace_until": state["completion_grace_until"],
        "is_within_completion_grace": state["is_within_completion_grace"],
        "is_non_compliant": state["is_non_compliant"],
        "pending_late_completion_request": state["pending_late_completion_request"],
        "active_late_completion_request": state["active_late_completion_request"],
        "can_request_late_completion": state["can_request_late_completion"],
        "pending_reopen_request": state["pending_reopen_request"],
        "active_approved_reopen_request": state["active_approved_reopen_request"],
        "active_approved_reopen_expires_at": state["active_approved_reopen_expires_at"],
        "can_request_deadline_reopen": state["can_request_deadline_reopen"],
        "quick_score_encoding_enabled": FeatureSettingsService.is_faculty_quick_score_encoding_enabled(
            tenant_id=offering.tenant_id,
            default=False,
        ),
    }
    return render(request, "faculty_portal/activity_scores.html", context)


_ATTENDANCE_ABSENCE_LIMITS_BY_UNITS = {
    Decimal("6"): Decimal("20"),
    Decimal("5"): Decimal("18"),
    Decimal("4"): Decimal("14"),
    Decimal("3"): Decimal("10"),
    Decimal("2"): Decimal("3"),
}


def _attendance_allowable_limit_for_course(course):
    units = getattr(course, "units", None)
    if units in (None, ""):
        return None
    try:
        units_decimal = Decimal(str(units))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return _ATTENDANCE_ABSENCE_LIMITS_BY_UNITS.get(units_decimal)


def _attendance_summary_status_for_absences(absence_count, allowable_limit):
    if allowable_limit is None:
        return "OK", 2
    absence_decimal = Decimal(str(absence_count or 0))
    allowable_decimal = Decimal(str(allowable_limit))
    warning_threshold = allowable_decimal * Decimal("0.75")
    if absence_decimal >= allowable_decimal:
        return "Exceeded Limit", 0
    if absence_decimal >= warning_threshold:
        return "Warning", 1
    return "OK", 2


def _attendance_consecutive_absence_count(session_ids, records_by_session_id):
    consecutive_absences = 0
    for session_id in reversed(session_ids):
        record = records_by_session_id.get(session_id)
        if not record or record.status_code != AttendanceRecord.Status.ABSENT:
            break
        consecutive_absences += 1
    return consecutive_absences


def _build_attendance_summary_rows(*, offering, period, status_filter="all"):
    today = timezone.localdate()
    sessions = list(
        AttendanceSession.objects.filter(
            offering_id=offering.id,
            template_period_id=period.id,
            is_active=True,
            session_date__lte=today,
        ).order_by("session_date", "id")
    )
    session_ids = [session.id for session in sessions]
    enrollments = list(FacultyGradingService.get_active_enrollments(offering))
    records = []
    if session_ids and enrollments:
        records = list(
            AttendanceRecord.objects.filter(
                session_id__in=session_ids,
                student_id__in=[enrollment.student_id for enrollment in enrollments],
                is_active=True,
            )
            .select_related("student", "session")
            .order_by("student__last_name", "student__first_name", "student__student_no", "session__session_date", "id")
        )
    records_by_student = defaultdict(list)
    records_by_session = defaultdict(dict)
    for record in records:
        records_by_student[record.student_id].append(record)
        records_by_session[record.student_id][record.session_id] = record

    allowable_limit = _attendance_allowable_limit_for_course(offering.course)
    rows = []
    for enrollment in enrollments:
        student_records = records_by_student.get(enrollment.student_id, [])
        status_counts = Counter(record.status_code for record in student_records)
        absent_count = int(status_counts.get(AttendanceRecord.Status.ABSENT, 0))
        consecutive_absence_count = _attendance_consecutive_absence_count(
            session_ids,
            records_by_session.get(enrollment.student_id, {}),
        )
        status_label, status_rank = _attendance_summary_status_for_absences(absent_count, allowable_limit)
        if status_rank == 2 and consecutive_absence_count >= 3:
            status_label = "Warning"
            status_rank = 1
        if status_filter == "warning" and status_label != "Warning":
            continue
        if status_filter == "exceeded" and status_label != "Exceeded Limit":
            continue
        rows.append(
            {
                "student": enrollment.student,
                "student_no": enrollment.student.student_no,
                "student_name": ", ".join(
                    part
                    for part in [
                        enrollment.student.last_name,
                        enrollment.student.first_name,
                    ]
                    if part
                ),
                "total_meetings": len(session_ids),
                "present_count": int(status_counts.get(AttendanceRecord.Status.PRESENT, 0)),
                "absent_count": absent_count,
                "late_count": int(status_counts.get(AttendanceRecord.Status.LATE, 0)),
                "excused_count": int(status_counts.get(AttendanceRecord.Status.EXCUSED, 0)),
                "consecutive_absence_count": consecutive_absence_count,
                "consecutive_absence_flagged": consecutive_absence_count >= 3,
                "allowable_limit": allowable_limit,
                "allowable_limit_display": _format_decimal_display(allowable_limit) if allowable_limit is not None else "",
                "remaining_allowable": (
                    max(allowable_limit - Decimal(str(absent_count)), Decimal("0")) if allowable_limit is not None else None
                ),
                "remaining_allowable_display": (
                    _format_decimal_display(max(allowable_limit - Decimal(str(absent_count)), Decimal("0")))
                    if allowable_limit is not None
                    else ""
                ),
                "status_label": status_label,
                "status_rank": status_rank,
            }
        )
    rows.sort(
        key=lambda row: (
            row["status_rank"],
            -Decimal(str(row["absent_count"])),
            -Decimal(str(row["consecutive_absence_count"])),
            row["student_name"].lower(),
            row["student_no"],
        )
    )
    for row in rows:
        row.pop("status_rank", None)
    return {
        "rows": rows,
        "session_count": len(session_ids),
        "allowable_limit": allowable_limit,
        "has_records": bool(records),
        "coverage_label": "Beginning of class to current date",
    }


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
        "encoding_control_closed": state["encoding_control_closed"],
        "encoding_control_message": state["encoding_control_message"],
        "is_editable": state["is_editable"],
        "submission_status": state["submission_status"],
        "is_auto_locked_reopened_after_deadline": state["is_auto_locked_reopened_after_deadline"],
        "is_auto_closed_after_deadline": state["is_auto_closed_after_deadline"],
        "is_governance_closed": state["is_governance_closed"],
        "governance_message": state["governance_message"],
        "system_correction_enabled": state["system_correction_enabled"],
        "completion_grace_until": state["completion_grace_until"],
        "is_within_completion_grace": state["is_within_completion_grace"],
        "is_non_compliant": state["is_non_compliant"],
        "pending_late_completion_request": state["pending_late_completion_request"],
        "active_late_completion_request": state["active_late_completion_request"],
        "can_request_late_completion": state["can_request_late_completion"],
        "pending_reopen_request": state["pending_reopen_request"],
        "active_approved_reopen_request": state["active_approved_reopen_request"],
        "active_approved_reopen_expires_at": state["active_approved_reopen_expires_at"],
        "can_request_deadline_reopen": state["can_request_deadline_reopen"],
        "can_manage_sessions": (
            state["is_editable"]
            and not state["is_auto_closed_after_deadline"]
            and not state["is_governance_closed"]
            and not state["is_read_only_class"]
        ),
    }
    return render(request, "faculty_portal/period_attendance.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_attendance_summary_view(request, offering_id: int, period_id: int):
    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    status_filter = (request.GET.get("status") or "all").strip().lower()
    if status_filter not in {"all", "warning", "exceeded"}:
        status_filter = "all"

    summary = _build_attendance_summary_rows(
        offering=offering,
        period=period,
        status_filter=status_filter,
    )
    course_units_display = _format_decimal_display(offering.course.units)
    allowable_limit_display = _format_decimal_display(summary["allowable_limit"])
    if summary["allowable_limit"] is None:
        allowable_limit_display = ""

    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "summary_rows": summary["rows"],
        "status_filter": status_filter,
        "coverage_label": summary["coverage_label"],
        "course_units_display": course_units_display,
        "allowable_limit_display": allowable_limit_display,
        "has_records": summary["has_records"],
        "session_count": summary["session_count"],
        "status_filter_options": [
            {"value": "all", "label": "All"},
            {"value": "warning", "label": "Warning"},
            {"value": "exceeded", "label": "Exceeded Limit"},
        ],
        "empty_state_message": "No attendance records have been encoded for this class yet.",
    }
    return render(request, "faculty_portal/attendance_summary.html", context)


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
    state = _period_edit_state(offering, period)
    official_grade_release = _official_grade_release_state(
        offering=offering,
        template=template,
        template_period=period,
        is_period_submitted=state["is_submitted"],
        submission_status=state["submission_status"],
        now=timezone.now(),
    )
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
        missing_student_ids = set(stored_summary_payload["missing_student_ids"])
        recompute_student_ids = set(missing_student_ids)
        if _period_uses_average_activity_details(period):
            recompute_student_ids.update(
                Enrollment.objects.filter(course_offering_id=offering.id, is_active=True).values_list("student_id", flat=True)
            )
        if recompute_student_ids:
            FacultyGradingService.recompute_period_summary_for_students(
                user=request.user,
                offering=offering,
                template_period=period,
                student_ids=recompute_student_ids,
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
                    "recomputed_student_count": len(recompute_student_ids),
                    "scope": "average_activities_refresh" if recompute_student_ids - missing_student_ids else "missing_students",
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
    summary_layout = PeriodSummaryLayoutService.build_layout(period, activities)
    visible_exam_components = [] if official_grade_release["is_final_period_view"] else summary_layout["exam_components"]
    activity_ids = [activity.id for activity in activities]
    summary_score_rows = list(
        StudentActivityScore.objects.filter(
            activity_id__in=activity_ids,
            is_active=True,
        ).only("student_id", "activity_id", "computed_score", "raw_score")
    )
    score_by_activity = {
        (score.student_id, score.activity_id): Decimal(score.computed_score)
        for score in summary_score_rows
    }
    encoded_zero_score_count = sum(1 for score in summary_score_rows if Decimal(score.raw_score or 0) == 0)

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
    summary_lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=period)
    submission_deadline = summary_lock.deadline_at if summary_lock else None
    deadline_countdown_label = None
    if submission_deadline:
        now = timezone.now()
        delta = submission_deadline - now
        total_seconds = int(abs(delta.total_seconds()))
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        if delta.total_seconds() >= 0:
            if days > 0:
                deadline_countdown_label = f"Due in {days} day{'s' if days != 1 else ''}"
                if hours:
                    deadline_countdown_label += f", {hours} hour{'s' if hours != 1 else ''}"
            elif hours > 0:
                deadline_countdown_label = f"Due in {hours} hour{'s' if hours != 1 else ''}"
            else:
                deadline_countdown_label = "Due today"
        else:
            if days > 0:
                deadline_countdown_label = f"Past due by {days} day{'s' if days != 1 else ''}"
            elif hours > 0:
                deadline_countdown_label = f"Past due by {hours} hour{'s' if hours != 1 else ''}"
            else:
                deadline_countdown_label = "Past due today"
    if official_grade_release["is_final_period_view"] or official_grade_release["show_final_grade"]:
        FacultyGradingService.recompute_final_grades_from_stored_periods(
            user=request.user,
            offering=offering,
            template=template,
        )

    period_grade_header_label = _tabulation_period_grade_column_label(period)

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
                "label": _tabulation_period_grade_column_label(prior),
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
        summary_values = PeriodSummaryLayoutService.build_row_values(row, summary_layout, score_by_activity)
        period_explain_url = None
        final_explain_url = None
        if official_grade_release["show_period_grade"] and row["period_grade"] is not None:
            period_explain_url = reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": offering.id,
                    "period_id": period.id,
                    "student_id": row["student"].id,
                    "grade_type": GradeExplanationService.GRADE_TYPE_PERIOD,
                },
            )
        if official_grade_release["show_final_grade"] and final_grade_map.get(row["student"].id) is not None:
            final_explain_url = reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": offering.id,
                    "period_id": period.id,
                    "student_id": row["student"].id,
                    "grade_type": GradeExplanationService.GRADE_TYPE_FINAL,
                },
            )
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
                "period_explain_url": period_explain_url,
                "final_explain_url": final_explain_url,
                "print_grade_status": (
                    "Passed"
                    if official_grade_release["show_period_grade"]
                    and row["period_grade"] is not None
                    and Decimal(row["period_grade"]) >= passing_threshold
                    else "Failed"
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

    summary_table_colspan = 4
    for block in summary_layout["class_standing_blocks"]:
        for section in block["sections"]:
            if section["uses_nested"]:
                for group in section["groups"]:
                    summary_table_colspan += len(group["activity_columns"]) + 1
                summary_table_colspan += 1
            else:
                summary_table_colspan += len(section["activity_columns"]) + 1
        summary_table_colspan += 1
    summary_table_colspan += len(prior_period_headers)
    summary_table_colspan += len(visible_exam_components)
    summary_table_colspan += 1
    if official_grade_release["show_final_grade"]:
        summary_table_colspan += 1
    can_print_gradebook_summary = state["is_submitted"]

    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "period_grade_header_label": period_grade_header_label,
        "summary_layout": summary_layout,
        "visible_exam_components": visible_exam_components,
        "rows": enriched_rows,
        "base_value": summary["base_value"],
        "prior_period_headers": prior_period_headers,
        "submit_readiness": submit_readiness,
        "submission_deadline": submission_deadline,
        "deadline_countdown_label": deadline_countdown_label,
        "completion_grace_until": state["completion_grace_until"],
        "is_locked": state["is_locked"],
        "is_submitted": state["is_submitted"],
        "can_self_reopen": state["can_self_reopen"],
        "can_print_gradebook_summary": can_print_gradebook_summary,
        "can_submit_period": state["can_submit_period"],
        "is_auto_locked_reopened_after_deadline": state["is_auto_locked_reopened_after_deadline"],
        "is_auto_closed_after_deadline": state["is_auto_closed_after_deadline"],
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "encoding_control_closed": state["encoding_control_closed"],
        "encoding_control_message": state["encoding_control_message"],
        "submission_status": state["submission_status"],
        "correction_mode": state["correction_mode"],
        "system_correction_enabled": state["system_correction_enabled"],
        "can_access_corrections": state["can_access_corrections"],
        "correction_lifecycle_message": state["correction_filing_state"]["message"],
        "q": q,
        "passing_threshold": passing_threshold,
        "show_official_period_grade": official_grade_release["show_period_grade"],
        "official_period_grade_masked": not official_grade_release["show_period_grade"],
        "official_period_grade_masked_label": official_grade_release["period_grade_masked_label"],
        "show_official_final_grade": official_grade_release["show_final_grade"],
        "official_grade_release_notes": official_grade_release["notes"],
        "summary_table_colspan": summary_table_colspan,
        "is_governance_closed": state["is_governance_closed"],
        "governance_message": state["governance_message"],
        "is_within_completion_grace": state["is_within_completion_grace"],
        "is_non_compliant": state["is_non_compliant"],
        "pending_late_completion_request": state["pending_late_completion_request"],
        "active_late_completion_request": state["active_late_completion_request"],
        "can_request_late_completion": state["can_request_late_completion"],
        "pending_reopen_request": state["pending_reopen_request"],
        "active_approved_reopen_request": state["active_approved_reopen_request"],
        "active_approved_reopen_expires_at": state["active_approved_reopen_expires_at"],
        "can_request_deadline_reopen": state["can_request_deadline_reopen"],
        "summary_status_counts": status_counts,
        "summary_passed_count": passed_count,
        "summary_failed_count": failed_count,
        "encoded_zero_score_count": encoded_zero_score_count,
    }
    context["readiness_cards"] = [
        {
            "label": "ACTIVE Students",
            "value": submit_readiness["eligible_student_count"],
            "tone": "success",
            "description": "Students included in this submission.",
        },
        {
            "label": "Template Gaps",
            "value": submit_readiness.get("missing_template_bucket_count", 0),
            "tone": "danger" if submit_readiness.get("missing_template_bucket_count", 0) > 0 else "success",
            "description": "Required parts with no activity yet.",
        },
        {
            "label": "Encoded Zero Scores",
            "value": encoded_zero_score_count,
            "tone": "warning" if encoded_zero_score_count > 0 else "success",
            "description": "Saved raw scores of 0. Review these before submission; 0 is valid and not missing.",
        },
        {
            "label": "DRP",
            "value": status_counts.get(Enrollment.Status.DRP, 0),
            "tone": "danger",
            "description": "Dropped students excluded from checks.",
        },
    ]
    if official_grade_release["show_period_grade"]:
        context["readiness_cards"].extend(
            [
                {
                    "label": "Failed",
                    "value": failed_count,
                    "tone": "danger",
                    "description": f"Below {passing_threshold}.",
                },
            ]
        )
    return render(request, "faculty_portal/period_summary.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
@require_GET
def period_summary_print_view(request, offering_id: int, period_id: int):
    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if not _periodic_grade_report_matches_active_scope(request, offering):
        raise Http404("Periodic grade report not found.")
    if period is None:
        raise Http404("Periodic grade report not found.")
    state = _period_edit_state(offering, period)
    if state["submission_status"] != GradeSubmission.Status.SUBMITTED:
        raise PermissionDenied("The official periodic grade report is available only for a submitted gradebook.")

    enrollments = list(
        Enrollment.objects.filter(course_offering_id=offering.id, is_active=True).select_related("student")
    )
    final_period = template.periods.filter(is_active=True).order_by("-sequence_no", "-id").first()
    is_final_period = bool(final_period and final_period.id == period.id)
    period_grade_label = _tabulation_period_grade_column_label(period)
    report = PeriodicGradePrintDataService.build(
        offering=offering,
        period=period,
        enrollments=enrollments,
        is_final_period=is_final_period,
        grade_label="FINAL GRADE" if is_final_period else period_grade_label,
        final_exam_label=period_grade_label,
    )
    context = {
        "offering": offering,
        "period": period,
        "report": report,
        "print_header_name": SystemSettingService.get(
            "PRINT_HEADER_SCHOOL_NAME",
            tenant_id=offering.tenant_id,
            default="NATIONAL COLLEGE OF BUSINESS AND ARTS",
        ),
        "print_header_address": SystemSettingService.get(
            "PRINT_HEADER_SCHOOL_ADDRESS",
            tenant_id=offering.tenant_id,
            default=getattr(offering.campus, "address", "") or "",
        ),
        "generated_at": timezone.localtime(),
    }
    return render(request, "faculty_portal/periodic_grade_print.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def grade_explanation_view(request, offering_id: int, period_id: int, student_id: int, grade_type: str):
    offering, template, period = _resolve_offering_period(
        request,
        offering_id,
        period_id,
        allow_governance_closed=True,
    )
    if period is None:
        return render(
            request,
            "grading/grade_explanation_detail.html",
            {
                "restricted_message": "Grade explanation is unavailable because the grading template or period could not be resolved.",
            },
        )
    state = _period_edit_state(offering, period)
    official_grade_release = _official_grade_release_state(
        offering=offering,
        template=template,
        template_period=period,
        is_period_submitted=state["is_submitted"],
        submission_status=state["submission_status"],
        now=timezone.now(),
    )
    normalized_grade_type = (grade_type or "").upper()
    if normalized_grade_type == GradeExplanationService.GRADE_TYPE_FINAL:
        if not official_grade_release["show_final_grade"]:
            return render(
                request,
                "grading/grade_explanation_detail.html",
                {
                    "restricted_message": "Final-grade explanation is hidden by the same official-grade visibility policy.",
                    "official_grade_release_notes": official_grade_release["notes"],
                },
            )
    elif not official_grade_release["show_period_grade"]:
        return render(
            request,
            "grading/grade_explanation_detail.html",
            {
                "restricted_message": "Period-grade explanation is hidden by the same official-grade visibility policy.",
                "official_grade_release_notes": official_grade_release["notes"],
            },
        )

    enrollment = get_object_or_404(
        Enrollment.objects.filter(course_offering=offering, is_active=True).select_related("student"),
        student_id=student_id,
    )
    try:
        explanation = GradeExplanationService.build(
            offering=offering,
            student=enrollment.student,
            template_period=period,
            grade_type=normalized_grade_type,
            mask_identity=False,
        )
    except ValidationError as exc:
        return render(
            request,
            "grading/grade_explanation_detail.html",
            {"restricted_message": "; ".join(exc.messages)},
        )
    AuditService.log_event(
        action="READ",
        portal="FACULTY",
        entity_type="GradeExplanation",
        entity_id=f"{offering.id}:{period.id}:{student_id}:{normalized_grade_type}",
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        metadata={
            "offering_id": offering.id,
            "period_id": period.id,
            "student_id": student_id,
            "grade_type": normalized_grade_type,
            "student_identity_visible": True,
            "masked_student_identity": False,
        },
        request=request,
    )
    return render(request, "grading/grade_explanation_detail.html", {"explanation": explanation})


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
            row.status_label = "Needs Scores"
            row.status_variant = "at-risk"
        elif row.at_risk_flag:
            if (
                row.current_projected_period_grade is not None
                and Decimal(row.current_projected_period_grade) < Decimal(passing_threshold)
            ):
                row.status_label = "At Risk This Period"
            else:
                row.status_label = "Needs Follow-up"
            row.status_variant = "at-risk"
        else:
            row.status_label = "On Track"
            row.status_variant = "ok"

    summary = prediction_data["summary"]
    metric_cards = [
        {"label": "Students", "value": summary.student_count, "meta": "Active students in this class."},
        {
            "label": "With Period Estimate",
            "value": summary.students_with_projection,
            "meta": f"{summary.avg_coverage_percent}% average progress",
        },
        {"label": "Need Follow-up", "value": summary.at_risk_count, "meta": "Students with a warning flag."},
        {
            "label": "Average Period Estimate",
            "value": _format_decimal_display(summary.avg_projected_grade),
            "meta": f"Unofficial estimate for {period.name}.",
        },
        {
            "label": "If Missing Perfect",
            "value": _format_decimal_display(summary.avg_best_case_grade),
            "meta": "If remaining items are completed at full score.",
        },
        {
            "label": "If Missing Zero",
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
            "column": f"Estimated {period.name} Grade",
            "meaning": f"The student's estimated grade for {period.name} using the scores encoded right now.",
            "factors": (
                "Encoded activity scores, attendance records, template component/subcomponent/detail weights, "
                "the tenant default assumption mode, and the current period scoring structure."
            ),
            "note": "Read this first. This is the main number on the page.",
        },
        {
            "column": "If Remaining Scores Are Perfect",
            "meaning": "The possible period grade if all still-missing work gets full score.",
            "factors": "Same template weights as the official gradebook, but all still-missing items are treated as full score.",
            "note": "This is the highest possible direction, not a promise.",
        },
        {
            "column": "If Remaining Scores Are Zero",
            "meaning": "The possible period grade if all still-missing work gets zero.",
            "factors": "Same template weights as the official gradebook, but all still-missing items are treated as zero.",
            "note": "This shows the low side if missing work is not completed.",
        },
        {
            "column": "Possible Final Grade",
            "meaning": "A rough guide showing how the current period estimate may affect the final grade later.",
            "factors": (
                "Already available official period grades from other periods in the same class plus the current projected "
                "grade of the selected period."
            ),
            "note": f"The page is still mainly for {period.name}. Treat this column as extra guidance only.",
        },
        {
            "column": "Score Needed to Pass",
            "meaning": "The approximate score needed in the remaining work to reach passing.",
            "factors": "Passing threshold, current worst-case grade, current best-case grade, and the remaining score span available in this period.",
            "note": "If the page says 'Already met', the current projection is already at or above the target. If it says 'Not reachable', the remaining items are not enough to hit the target even with perfect performance.",
        },
        {
            "column": "Final Grade Outlook",
            "meaning": "A simple yes/no guide on whether the student can still finish with a passing final grade based on available records.",
            "factors": (
                "Passing threshold, total active periods in the grading template, official earlier period grades, "
                "the current projected period grade, and the number of remaining future periods."
            ),
            "note": "Use this only for early advising. It is not an official final-grade decision.",
        },
        {
            "column": "Encoded Work",
            "meaning": "How much of the expected work already has scores or attendance records.",
            "factors": "Encoded item count divided by expected item count for active activities plus attendance sessions in the selected period.",
            "note": "Low encoded work means the estimate can still change a lot.",
        },
        {
            "column": "Still Missing",
            "meaning": "How many expected items still have no saved record.",
            "factors": "Expected active items minus currently encoded items for the student in the selected period.",
            "note": "The higher this number is, the less final the estimate is.",
        },
        {
            "column": "Period Alert",
            "meaning": "A quick warning label for the faculty.",
            "factors": "Current Projection compared against the class passing threshold.",
            "note": "This is only an early warning. The official grade still comes from the Summary and submission process.",
        },
    ]

    methodology_steps = [
        {
            "title": "1. Start with only active students and active records",
            "body": (
                "TeacherMate+ reads only ACTIVE students in the class. Students marked DRP, W, or INC are not used "
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
                "For each student, TeacherMate+ keeps a current value from encoded records, a worst-case value that treats "
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
            "impact": "Used to mark students who need follow-up and to compute the target-needed and average-needed advisory values.",
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
                "For normal raw-score activities, TeacherMate+ converts the raw score into a computed score using: "
                "Computed Score = ((Raw Score / Total Score) × Base Value) + (100 − Base Value)."
            ),
        },
        {
            "title": "Default base behavior",
            "body": (
                "If the class does not have a more specific base-value override, TeacherMate+ ultimately falls back to "
                "Base 50. That means zero raw score starts at 50.00 and perfect raw score reaches 100.00."
            ),
        },
        {
            "title": "Direct percentage mode",
            "body": (
                "If an activity uses Direct Percentage mode, TeacherMate+ does not transmute the raw score through Base 50. "
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
        "If a subcomponent has details, TeacherMate+ either uses the detail weights or averages the faculty-created activities under those details, depending on the admin template setting.",
        "If a component has subcomponents, TeacherMate+ averages those subcomponents upward using the subcomponent weights.",
        "The period grade is then the weighted sum of all active top-level components in the selected period.",
        "If the template has a configured exam component and there is still no exam data, the official period grade remains unavailable until the exam side has data.",
    ]

    period_grade_formula = (
        "Period Grade = sum of [Component Score × Component Weight]. "
        "Component Score may itself be built from weighted subcomponents and the configured detail-computation rule."
    )

    final_grade_steps = [
        "TeacherMate+ stores an official period grade per active grading period when that period is recomputed.",
        "The official final grade record is then computed using the final-grade formula resolved from the matched tenant grading profile.",
        "If no special tenant formula is configured, TeacherMate+ falls back to averaging the active grading periods of the assigned template.",
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
                "If the faculty enters 80% as Remaining Performance, TeacherMate+ does not save any grade. "
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
                "TeacherMate+ can show the average still needed across the remaining final periods. "
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

    state = _period_edit_state(offering, period)
    if state["is_read_only_class"]:
        messages.error(request, state["faculty_scope_state"]["reason"])
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
    if not state["can_submit_period"]:
        messages.error(request, "This period is not available for faculty submission.")
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

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

    state = _period_edit_state(offering, period)
    if state["is_read_only_class"]:
        messages.error(request, state["faculty_scope_state"]["reason"])
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
    if not state["can_self_reopen"]:
        messages.error(request, "This gradebook is not available for faculty self-reopen.")
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

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
@permission_required("faculty_portal.access")
def period_reopen_request_view(request, offering_id: int, period_id: int):
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

    state = _period_edit_state(offering, period)
    if state["is_read_only_class"]:
        messages.error(request, state["faculty_scope_state"]["reason"])
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
    if not state["can_request_deadline_reopen"]:
        messages.error(request, "This gradebook is not available for a deadline reopen request.")
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

    justification = (request.POST.get("justification") or "").strip()
    try:
        reopen_request = GradingGovernanceService.create_reopen_request_for_period(
            user=request.user,
            offering=offering,
            template_period=period,
            justification=justification,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

    email_result = GradebookReopenNotificationService.send_reopen_request_notifications(
        request_obj=reopen_request,
    )
    AuditService.log_event(
        action="CREATE",
        portal="FACULTY",
        entity_type="GradeSubmissionReopenRequest",
        entity_id=reopen_request.id,
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        after_data=model_before_after(reopen_request),
        metadata={
            "mode": "FACULTY_DEADLINE_REOPEN_REQUEST",
            "offering_id": offering.id,
            "period_id": period.id,
            "email_result": email_result,
        },
        request=request,
    )
    messages.success(
        request,
        "Reopen request submitted. The campus approver can review it from the Admin Portal queue.",
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
    if not state["correction_filing_state"]["is_allowed"]:
        messages.error(request, state["correction_filing_state"]["message"])
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
    petition_window_state = state["correction_filing_state"]["petition_window_state"]
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
        if not state["correction_filing_state"]["is_allowed"]:
            messages.error(request, state["correction_filing_state"]["message"])
            return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)
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
                pending_step = GradingGovernanceService.get_pending_correction_step(request_obj=correction)
                notification_result = CorrectionNotificationService.send_correction_step_approval_notifications(
                    request_obj=correction,
                    step=pending_step,
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
                    "Correction request submitted for review. Once approved, TeacherMate+ will post the corrected values automatically.",
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
            "approval_steps",
            "approval_steps__approver_role",
            "approval_steps__reviewed_by_user",
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
        req.progress = GradingGovernanceService.correction_progress(request_obj=req)
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
        "petition_window_state": petition_window_state,
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
                "detail_weight": (
                    _format_decimal_display(activity.template_detail.weight_percentage)
                    if activity.template_detail
                    else "-"
                ),
                "detail_weight_reference_only": bool(
                    activity.template_detail
                    and activity.template_subcomponent
                    and activity.template_subcomponent.detail_computation_mode
                    == DetailComputationMode.AVERAGE_ACTIVITIES
                ),
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

    window = GradingGovernanceService.get_active_unlock_window(
        offering=offering,
        template_period=period,
    )
    if not window or window.correction_request_id != correction.id:
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
        with transaction.atomic():
            affected_student_ids = {
                item.student_id
                for item in correction.items.filter(is_active=True, student__isnull=False)
                if item.student_id
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
    offering = _require_faculty_offering_or_404(request, offering_id)
    mode = EnrollmentService.get_enrollment_mode(offering.tenant_id, offering_id=offering.id)
    can_update_status = (
        not offering.faculty_is_read_only
        and EnrollmentService.can_update_classlist_status(
            user=request.user,
            offering=offering,
            portal=Enrollment.SourcePortal.FACULTY,
        )
    )
    student_qs = Student.objects.filter(
        tenant_id=offering.tenant_id,
        campus_id=offering.campus_id,
        is_active=True,
    ).order_by("last_name", "first_name")
    active_enrollments = (
        offering.enrollments.select_related("student")
        .filter(is_active=True)
        .order_by("student__last_name", "student__first_name", "student__student_no")
    )
    active_student_ids = list(active_enrollments.values_list("student_id", flat=True))
    add_request_form = ClassListAddRequestForm(student_queryset=student_qs.exclude(id__in=active_student_ids))
    remove_request_form = ClassListRemoveRequestForm(enrollment_queryset=active_enrollments)
    is_ajax_request = request.headers.get("x-requested-with") == "XMLHttpRequest"

    def build_request_area_context(*, add_form, remove_form):
        return {
            "offering": offering,
            "mode": mode,
            "can_request_class_list_change": not offering.faculty_is_read_only,
            "add_request_form": add_form,
            "remove_request_form": remove_form,
            "enrollments": active_enrollments,
            "class_list_change_requests": (
                ClassListChangeRequest.objects.filter(offering=offering, faculty_requester=request.user)
                .select_related("reviewed_by")
                .prefetch_related("items", "items__student", "items__enrollment", "items__enrollment__student")
                .order_by("-created_at", "-id")
            ),
        }

    def build_request_area_html(*, add_form, remove_form):
        return render_to_string(
            "faculty_portal/partials/class_list_change_requests_area.html",
            build_request_area_context(add_form=add_form, remove_form=remove_form),
            request=request,
        )

    def ajax_response(*, ok: bool, message: str, add_form, remove_form, status: int = 200):
        return JsonResponse(
            {
                "ok": ok,
                "message": message,
                "html": build_request_area_html(add_form=add_form, remove_form=remove_form),
            },
            status=status,
        )

    if request.method == "POST":
        if offering.faculty_is_read_only:
            message = offering.faculty_read_only_reason
            if is_ajax_request:
                return ajax_response(
                    ok=False,
                    message=message,
                    add_form=add_request_form,
                    remove_form=remove_request_form,
                    status=403,
                )
            messages.error(request, message)
            return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
        action = (request.POST.get("action") or "").strip().lower()
        if action == "request_add_class_list_change":
            add_request_form = ClassListAddRequestForm(request.POST, student_queryset=student_qs)
            if add_request_form.is_valid():
                try:
                    request_obj = ClassListChangeRequestService.create_request(
                        user=request.user,
                        offering=offering,
                        request_type=ClassListChangeRequest.RequestType.ADD,
                        remarks=add_request_form.cleaned_data.get("remarks"),
                        student=add_request_form.cleaned_data.get("student"),
                        student_number=add_request_form.cleaned_data.get("student_number"),
                        student_name=add_request_form.cleaned_data.get("student_name"),
                    )
                except (PermissionDenied, ValidationError) as exc:
                    add_request_form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                else:
                    AuditService.log_event(
                        action="CREATE",
                        portal="FACULTY",
                        entity_type="ClassListChangeRequest",
                        entity_id=request_obj.id,
                        actor=request.user,
                        after_data={
                            "offering_id": request_obj.offering_id,
                            "campus_id": request_obj.campus_id,
                            "request_type": request_obj.request_type,
                            "status": request_obj.status,
                            "student_number": request_obj.items.first().reference_student_no if request_obj.items.exists() else "",
                            "student_name": request_obj.items.first().reference_student_name if request_obj.items.exists() else "",
                        },
                        request=request,
                    )
                    message = "Your class list add request was submitted and forwarded to Campus Admin for AIMS verification."
                    if is_ajax_request:
                        return ajax_response(
                            ok=True,
                            message=message,
                            add_form=ClassListAddRequestForm(student_queryset=student_qs.exclude(id__in=active_student_ids)),
                            remove_form=ClassListRemoveRequestForm(enrollment_queryset=active_enrollments),
                        )
                    messages.success(request, message)
                    return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            if is_ajax_request:
                return ajax_response(
                    ok=False,
                    message="Please review the highlighted fields before submitting the add request.",
                    add_form=add_request_form,
                    remove_form=remove_request_form,
                    status=400,
                )
        elif action == "request_remove_class_list_change":
            remove_request_form = ClassListRemoveRequestForm(request.POST, enrollment_queryset=active_enrollments)
            if remove_request_form.is_valid():
                try:
                    request_obj = ClassListChangeRequestService.create_request(
                        user=request.user,
                        offering=offering,
                        request_type=ClassListChangeRequest.RequestType.REMOVE,
                        remarks=remove_request_form.cleaned_data.get("remarks"),
                        enrollments=remove_request_form.cleaned_data.get("enrollments"),
                    )
                except (PermissionDenied, ValidationError) as exc:
                    remove_request_form.add_error(None, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
                else:
                    AuditService.log_event(
                        action="CREATE",
                        portal="FACULTY",
                        entity_type="ClassListChangeRequest",
                        entity_id=request_obj.id,
                        actor=request.user,
                        after_data={
                            "offering_id": request_obj.offering_id,
                            "campus_id": request_obj.campus_id,
                            "request_type": request_obj.request_type,
                            "status": request_obj.status,
                            "item_count": request_obj.items.count(),
                        },
                        request=request,
                    )
                    message = "Your class list remove request was submitted and forwarded to Campus Admin for AIMS verification."
                    if is_ajax_request:
                        return ajax_response(
                            ok=True,
                            message=message,
                            add_form=ClassListAddRequestForm(student_queryset=student_qs.exclude(id__in=active_student_ids)),
                            remove_form=ClassListRemoveRequestForm(enrollment_queryset=active_enrollments),
                        )
                    messages.success(request, message)
                    return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            if is_ajax_request:
                return ajax_response(
                    ok=False,
                    message="Please review the highlighted fields before submitting the remove request.",
                    add_form=add_request_form,
                    remove_form=remove_request_form,
                    status=400,
                )
        elif action == "cancel_class_list_change_request":
            request_id = request.POST.get("request_id")
            request_obj = (
                ClassListChangeRequest.objects.filter(
                    id=request_id,
                    offering=offering,
                    faculty_requester=request.user,
                )
                .select_related("faculty_requester")
                .first()
            )
            if not request_obj:
                messages.error(request, "Pending request not found.")
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            try:
                ClassListChangeRequestService.cancel_request(user=request.user, request_obj=request_obj)
            except (PermissionDenied, ValidationError) as exc:
                message = str(exc)
                if is_ajax_request:
                    return ajax_response(
                        ok=False,
                        message=message,
                        add_form=add_request_form,
                        remove_form=remove_request_form,
                        status=400,
                    )
                messages.error(request, message)
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            AuditService.log_event(
                action="CANCEL",
                portal="FACULTY",
                entity_type="ClassListChangeRequest",
                entity_id=request_obj.id,
                actor=request.user,
                before_data={
                    "offering_id": request_obj.offering_id,
                    "campus_id": request_obj.campus_id,
                    "request_type": request_obj.request_type,
                    "status": ClassListChangeRequest.Status.PENDING,
                },
                after_data={
                    "offering_id": request_obj.offering_id,
                    "campus_id": request_obj.campus_id,
                    "request_type": request_obj.request_type,
                    "status": request_obj.status,
                },
                request=request,
            )
            message = "Your pending class list change request was removed."
            if is_ajax_request:
                return ajax_response(
                    ok=True,
                    message=message,
                    add_form=ClassListAddRequestForm(student_queryset=student_qs.exclude(id__in=active_student_ids)),
                    remove_form=ClassListRemoveRequestForm(enrollment_queryset=active_enrollments),
                )
            messages.success(request, message)
            return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
        if action == "update_status":
            if not can_update_status:
                message = "You are not allowed to update class list status for this offering."
                if is_ajax_request:
                    return ajax_response(
                        ok=False,
                        message=message,
                        add_form=add_request_form,
                        remove_form=remove_request_form,
                        status=403,
                    )
                messages.error(request, message)
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            enrollment_id = request.POST.get("enrollment_id")
            new_status = (request.POST.get("enrollment_status") or "").strip().upper()
            allowed_statuses = {key for key, _ in Enrollment.Status.choices}
            enrollment = offering.enrollments.filter(id=enrollment_id).select_related("student").first()
            if not enrollment:
                message = "Enrollment row not found."
                if is_ajax_request:
                    return ajax_response(
                        ok=False,
                        message=message,
                        add_form=add_request_form,
                        remove_form=remove_request_form,
                        status=404,
                    )
                messages.error(request, message)
                return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)
            if new_status not in allowed_statuses:
                message = "Invalid enrollment status."
                if is_ajax_request:
                    return ajax_response(
                        ok=False,
                        message=message,
                        add_form=add_request_form,
                        remove_form=remove_request_form,
                        status=400,
                    )
                messages.error(request, message)
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
                message = str(exc)
                if is_ajax_request:
                    return ajax_response(
                        ok=False,
                        message=message,
                        add_form=add_request_form,
                        remove_form=remove_request_form,
                        status=400,
                    )
                messages.error(request, message)
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
            message = f"Enrollment status updated to {new_status}."
            if is_ajax_request:
                return ajax_response(
                    ok=True,
                    message=message,
                    add_form=ClassListAddRequestForm(student_queryset=student_qs.exclude(id__in=active_student_ids)),
                    remove_form=ClassListRemoveRequestForm(enrollment_queryset=active_enrollments),
                )
            messages.success(request, message)
            return redirect("faculty_portal:offering_enrollment", offering_id=offering.id)

        if action not in {
            "request_add_class_list_change",
            "request_remove_class_list_change",
            "cancel_class_list_change_request",
            "update_status",
        }:
            message = "Please use the request forms to add or remove class-list students."
            if is_ajax_request:
                return ajax_response(
                    ok=False,
                    message=message,
                    add_form=add_request_form,
                    remove_form=remove_request_form,
                    status=400,
                )
            messages.error(request, message)
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
    class_list_change_requests = (
        ClassListChangeRequest.objects.filter(offering=offering, faculty_requester=request.user)
        .select_related("reviewed_by")
        .prefetch_related("items", "items__student", "items__enrollment", "items__enrollment__student")
        .order_by("-created_at", "-id")
    )
    context = {
        "offering": offering,
        "mode": mode,
        "can_request_class_list_change": not offering.faculty_is_read_only,
        "can_update_status": can_update_status,
        "is_read_only_class": offering.faculty_is_read_only,
        "read_only_reason": offering.faculty_read_only_reason,
        "add_request_form": add_request_form,
        "remove_request_form": remove_request_form,
        "enrollments": enrollments,
        "removed_enrollments": removed_enrollments,
        "class_list_change_requests": class_list_change_requests,
    }
    return render(request, "faculty_portal/offering_enrollment.html", context)
