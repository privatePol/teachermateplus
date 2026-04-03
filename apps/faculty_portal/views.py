from collections import defaultdict
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re

from django.contrib import messages
from django import forms as django_forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Avg, Count, Prefetch, Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie

from apps.academics.models import CourseOffering
from apps.academics.services import AcademicGovernanceService
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.core.decorators import permission_required, portal_required
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.enrollment.services import EnrollmentService
from apps.faculty_portal.forms import (
    AttendanceSessionForm,
    FacultyEnrollmentForm,
    GradeActivityForm,
    GradeCorrectionRequestForm,
)
from apps.grading.models import (
    GradeCorrectionAttachment,
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeSubmission,
    GradeActivity,
    GradingTemplateDetail,
    GradingTemplateSubcomponent,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.grading.notifications import CorrectionNotificationService
from apps.grading.reporting import CorrectionOfficialReportService
from apps.grading.services import FacultyGradingService, GradingGovernanceService
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


def _faculty_offering_queryset(user):
    return CourseOffering.objects.filter(
        faculty_assignments__faculty_user_id=user.id,
        faculty_assignments__is_active=True,
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


def _format_decimal_display(value):
    if value in (None, ""):
        return ""
    decimal_value = Decimal(str(value))
    formatted = format(decimal_value.quantize(Decimal("0.01")), "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


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


def _resolve_offering_period(request, offering_id: int, period_id: int):
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
    return offering, template, period


def _period_edit_state(offering, period):
    is_locked = GradingGovernanceService.is_locked(offering=offering, template_period=period)
    submission = GradingGovernanceService.get_submission(offering=offering, template_period=period)
    is_submitted = bool(submission and submission.status == GradeSubmission.Status.SUBMITTED)
    active_correction_request = GradingGovernanceService.get_active_correction_request(
        offering=offering, template_period=period
    )
    is_correction_active = bool(active_correction_request)
    is_editable = (not is_locked and not is_submitted) or is_correction_active
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
        "is_correction_active": is_correction_active,
        "active_correction_request": active_correction_request,
        "is_editable": is_editable,
        "predeadline_correction_mode": GradingGovernanceService.get_predeadline_correction_mode(
            tenant_id=offering.tenant_id
        ),
        "correction_mode": correction_mode,
        "system_correction_enabled": system_correction_enabled,
        "can_self_reopen_before_deadline": GradingGovernanceService.can_faculty_self_reopen_before_deadline(
            offering=offering,
            template_period=period,
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
        component_is_exam = "EXAM" in component.code.upper()
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
    near_deadline_cutoff = dashboard_now + timezone.timedelta(hours=48)
    upcoming_deadline_candidates = []

    if active_offering_ids:
        dropped_students = Enrollment.objects.filter(
            course_offering_id__in=active_offering_ids,
            is_active=True,
            enrollment_status=Enrollment.Status.DR,
        ).count()
        withdrawn_students = Enrollment.objects.filter(
            course_offering_id__in=active_offering_ids,
            is_active=True,
            enrollment_status=Enrollment.Status.W,
        ).count()
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
                    lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=period)
                    if lock and lock.deadline_at:
                        upcoming_deadline_candidates.append(
                            {
                                "deadline_at": lock.deadline_at,
                                "period_name": period.name,
                                "period_code": period.code,
                                "offering_id": offering.id,
                                "course_code": offering.course.code,
                                "section_code": offering.section.code,
                            }
                        )
                    if (
                        lock
                        and lock.deadline_at
                        and dashboard_now <= lock.deadline_at <= near_deadline_cutoff
                    ):
                        periods_near_deadline += 1

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

    deadline_reminder = {
        "has_deadline": False,
        "title": "No submission deadline is set yet",
        "note": "Ask your academic or campus administrator to configure the current grading deadline so faculty can track submission timing clearly.",
        "variant": "neutral",
        "deadline_at": None,
        "period_name": None,
        "affected_classes": 0,
    }
    if upcoming_deadline_candidates:
        upcoming_deadline_candidates.sort(key=lambda item: item["deadline_at"])
        current_deadline = upcoming_deadline_candidates[0]
        affected_classes = len(
            {
                item["offering_id"]
                for item in upcoming_deadline_candidates
                if item["deadline_at"] == current_deadline["deadline_at"]
                and item["period_code"] == current_deadline["period_code"]
            }
        )
        is_overdue = current_deadline["deadline_at"] < dashboard_now
        deadline_reminder = {
            "has_deadline": True,
            "title": "Current grading period deadline reminder",
            "note": (
                "This deadline has already passed. Review your pending classes and contact the authorized approver if a governed correction or reopen process is needed."
                if is_overdue
                else "Keep this date in view while encoding, checking summaries, and finalizing submission."
            ),
            "variant": "danger" if is_overdue else "warning",
            "deadline_at": current_deadline["deadline_at"],
            "period_name": current_deadline["period_name"],
            "affected_classes": affected_classes,
        }

    stats = {
        "assigned_courses": offerings_qs.count(),
        "active_classes": len(active_offerings),
        "archived_classes": len(archived_offerings),
        "active_enrollments": active_enrollments_count,
        "activities_encoded": activities_encoded,
        "classes_not_submitted": classes_not_submitted,
        "failed_period_grade_count": failed_period_grade_count,
        "dropped_students": dropped_students,
        "withdrawn_students": withdrawn_students,
        "active_students_without_grades": active_students_without_grades,
        "periods_near_deadline": periods_near_deadline,
        "submitted_periods": submitted_periods,
        "activities_without_scores": activities_without_scores,
        "pending_correction_requests": pending_correction_requests,
        "classes_with_missing_grades": classes_with_missing_grades,
        "deadline_reminder": deadline_reminder,
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

    graded_count = grade_qs.count()
    failed_count = grade_qs.filter(period_grade__lt=Decimal("75")).count()
    passed_count = max(graded_count - failed_count, 0)

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
    grade_map = {
        row["offering_id"]: row
        for row in grade_qs.values("offering_id").annotate(
            avg_grade=Avg("period_grade"),
            graded_rows=Count("id"),
            failed_rows=Count("id", filter=Q(period_grade__lt=Decimal("75"))),
        )
    }

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
        "avg_grade": grade_qs.aggregate(avg=Avg("period_grade")).get("avg"),
    }

    context = {
        "summary": summary,
        "distribution_rows": distribution_rows,
        "class_rows": class_rows,
        "include_archived": include_archived,
    }
    return render(request, "faculty_portal/analytics.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def my_courses_view(request):
    show_archived = request.GET.get("archived") == "1"
    base_qs = (
        _faculty_offering_queryset(request.user)
        .annotate(enrollment_count=Count("enrollments"))
        .distinct()
        .order_by("tenant__code", "campus__code", "term__sequence_no", "course__code", "section__code")
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

    active_offerings = []
    archived_offerings = []
    for offering in base_qs:
        forced_archive = offering.status == CourseOffering.Status.ARCHIVED
        outside_active_scope = not _is_in_active_scope(offering)
        if forced_archive or outside_active_scope:
            archived_offerings.append(offering)
        else:
            active_offerings.append(offering)

    selected_offerings = archived_offerings if show_archived else active_offerings

    grouped_offerings = []
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

    context = {
        "grouped_offerings": grouped_offerings,
        "show_archived": show_archived,
        "active_count": len(active_offerings),
        "archived_count": len(archived_offerings),
    }
    return render(request, "faculty_portal/my_courses.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def offering_periods_view(request, offering_id: int):
    offering = _require_faculty_offering_or_404(request, offering_id)
    try:
        template = FacultyGradingService.resolve_template_for_offering(offering)
        periods = FacultyGradingService.get_template_periods(template)
    except ValidationError as exc:
        template = None
        periods = []
        messages.error(request, str(exc))

    period_cards = []
    for p in periods:
        lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=p)
        submission = GradingGovernanceService.get_submission(offering=offering, template_period=p)
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
    }
    return render(request, "faculty_portal/offering_periods.html", context)


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

    component_option_data = [
        {
            "id": str(component.id),
            "name": component.name,
            "has_subcomponents": subcomponent_qs.filter(template_component_id=component.id).exists(),
        }
        for component in component_qs
    ]
    subcomponent_option_data = [
        {
            "id": str(subcomponent.id),
            "name": subcomponent.name,
            "component_id": str(subcomponent.template_component_id),
            "has_details": detail_qs.filter(template_subcomponent_id=subcomponent.id).exists(),
        }
        for subcomponent in subcomponent_qs
    ]
    detail_option_data = [
        {
            "id": str(detail.id),
            "name": detail.name,
            "subcomponent_id": str(detail.template_subcomponent_id),
        }
        for detail in detail_qs
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
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "is_editable": state["is_editable"],
        "system_correction_enabled": state["system_correction_enabled"],
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
            if enrollment.enrollment_status in {Enrollment.Status.DR, Enrollment.Status.W}:
                continue
            if raw_val in (None, ""):
                if student_id in score_map:
                    payload.append({"student_id": student_id, "clear": True})
                continue

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
        "system_correction_enabled": state["system_correction_enabled"],
    }
    return render(request, "faculty_portal/activity_scores.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_attendance_view(request, offering_id: int, period_id: int):
    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
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
        "system_correction_enabled": state["system_correction_enabled"],
        "can_manage_sessions": not state["is_locked"] and not state["is_submitted"],
    }
    return render(request, "faculty_portal/period_attendance.html", context)


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def period_summary_view(request, offering_id: int, period_id: int):
    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering.id)

    state = _period_edit_state(offering, period)
    if state["is_editable"]:
        summary = FacultyGradingService.recompute_period_summary(
            user=request.user,
            offering=offering,
            template_period=period,
        )
        AuditService.log_event(
            action="COMPUTE",
            portal="FACULTY",
            entity_type="PeriodSummary",
            entity_id=f"{offering.id}:{period.id}",
            actor=request.user,
            tenant=offering.tenant,
            campus=offering.campus,
            metadata={"offering_id": offering.id, "period_id": period.id},
            request=request,
        )
    else:
        period_rows = list(
            Enrollment.objects.filter(course_offering_id=offering.id, is_active=True).select_related("student")
        )
        component_codes = [
            c.code for c in period.components.filter(is_active=True).order_by("sort_order", "id")
        ]
        grade_map = {
            row.student_id: row
            for row in StudentPeriodGrade.objects.filter(offering_id=offering.id, template_period_id=period.id)
        }
        rows = []
        for enrollment in period_rows:
            p = grade_map.get(enrollment.student_id)
            rows.append(
                {
                    "student": enrollment.student,
                    "enrollment_status": enrollment.enrollment_status,
                    "component_scores": {},
                    "class_standing": p.class_standing_grade if p else None,
                    "exam_grade": p.exam_grade if p else None,
                    "period_grade": p.period_grade if p else None,
                }
            )
        summary = {
            "rows": rows,
            "component_codes": component_codes,
            "base_value": FacultyGradingService.resolve_base_value(offering, template),
        }
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

    enriched_rows = []
    for row in rows:
        summary_values = _build_summary_row_values(row, summary_layout, score_by_activity)
        enriched_rows.append(
            {
                "student": row["student"],
                "enrollment_status": row["enrollment_status"],
                "class_standing_blocks": summary_values["class_standing_blocks"],
                "exam_values": summary_values["exam_values"],
                "period_grade": row["period_grade"],
                "print_grade_status": (
                    "PASSED"
                    if row["period_grade"] is not None and Decimal(row["period_grade"]) >= Decimal("75")
                    else "FAILED"
                    if row["period_grade"] is not None
                    else ""
                ),
            }
        )

    summary_lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=period)
    print_header_name = SystemSettingService.get(
        "PRINT_HEADER_SCHOOL_NAME",
        tenant_id=offering.tenant_id,
        default=offering.tenant.name,
    )
    print_header_address = SystemSettingService.get(
        "PRINT_HEADER_SCHOOL_ADDRESS",
        tenant_id=offering.tenant_id,
        default=getattr(offering.campus, "address", "") or "",
    )

    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "summary_layout": summary_layout,
        "rows": enriched_rows,
        "base_value": summary["base_value"],
        "submit_readiness": GradingGovernanceService.evaluate_submission_readiness(
            offering=offering,
            template_period=period,
        ),
        "submission_deadline": summary_lock.deadline_at if summary_lock else None,
        "is_locked": state["is_locked"],
        "is_submitted": state["is_submitted"],
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "submission_status": state["submission_status"],
        "predeadline_correction_mode": state["predeadline_correction_mode"],
        "correction_mode": state["correction_mode"],
        "system_correction_enabled": state["system_correction_enabled"],
        "can_self_reopen_before_deadline": state["can_self_reopen_before_deadline"],
        "print_header_name": print_header_name,
        "print_header_address": print_header_address,
        "generated_at": timezone.localtime(),
        "q": q,
    }
    return render(request, "faculty_portal/period_summary.html", context)


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
            "Encode at least one record or mark students as DR/W first.",
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
                "coverage_percent": str(readiness["coverage_percent"]),
            },
        },
        request=request,
    )
    messages.success(request, f"{period.code} grades submitted successfully.")
    return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)


@portal_required("FACULTY")
@permission_required("corrections.create")
def period_corrections_view(request, offering_id: int, period_id: int):
    GradingGovernanceService.auto_lapse_expired_correction_windows()
    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
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
                    GradeCorrectionAttachment.objects.create(
                        correction_request=correction,
                        file=attachment,
                        uploaded_by_user=request.user,
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
                        "Please review Configurable Features and the recipient role assignments.",
                    )
                messages.success(request, "Correction request submitted for review.")
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
    context = {
        "offering": offering,
        "template": template,
        "period": period,
        "form": form,
        "requests": requests_qs,
        "is_locked": state["is_locked"],
        "is_submitted": state["is_submitted"],
        "submission_status": state["submission_status"],
        "submission_deadline": state["submission_deadline"],
        "is_correction_active": state["is_correction_active"],
        "active_correction_request": state["active_correction_request"],
        "predeadline_correction_mode": state["predeadline_correction_mode"],
        "can_self_reopen_before_deadline": state["can_self_reopen_before_deadline"],
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
@permission_required("corrections.create")
def period_self_reopen_view(request, offering_id: int, period_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:period_corrections", offering_id=offering_id, period_id=period_id)

    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
    if period is None:
        return redirect("faculty_portal:offering_periods", offering_id=offering_id)

    if not GradingGovernanceService.is_system_correction_enabled(tenant_id=offering.tenant_id):
        messages.error(
            request,
            "Self-reopen is disabled by tenant policy (MANUAL_ONLY).",
        )
        return redirect("faculty_portal:period_summary", offering_id=offering.id, period_id=period.id)

    if not GradingGovernanceService.can_faculty_self_reopen_before_deadline(
        offering=offering,
        template_period=period,
    ):
        messages.error(
            request,
            "Self-reopen is not allowed for this period. Use the correction request workflow instead.",
        )
        return redirect("faculty_portal:period_corrections", offering_id=offering.id, period_id=period.id)

    submission = GradingGovernanceService.get_submission(offering=offering, template_period=period)
    before = {
        "submission_status": submission.status if submission else None,
        "submitted_at": submission.submitted_at.isoformat() if submission and submission.submitted_at else None,
    }
    reopened = GradingGovernanceService.reopen_period(
        user=request.user,
        offering=offering,
        template_period=period,
        remarks="Faculty self-reopen before deadline",
    )
    AuditService.log_event(
        action="REOPEN",
        portal="FACULTY",
        entity_type="GradeSubmission",
        entity_id=reopened.id if reopened else None,
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        before_data=before,
        after_data={
            "submission_status": reopened.status if reopened else GradeSubmission.Status.REOPENED,
            "reopened_at": reopened.reopened_at.isoformat() if reopened and reopened.reopened_at else None,
            "mode": "FACULTY_SELF_REOPEN",
        },
        request=request,
    )
    messages.success(request, "Period reopened. You may now update grades and resubmit before the deadline.")
    return redirect("faculty_portal:offering_periods", offering_id=offering.id)


@portal_required("FACULTY")
@permission_required("corrections.create")
def period_correction_finalize_view(request, offering_id: int, period_id: int, request_id: int):
    if request.method != "POST":
        return redirect("faculty_portal:period_corrections", offering_id=offering_id, period_id=period_id)

    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
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

    try:
        FacultyGradingService.recompute_period_summary(
            user=request.user,
            offering=offering,
            template_period=period,
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
    offering, template, period = _resolve_offering_period(request, offering_id, period_id)
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
    return FileResponse(
        BytesIO(pdf_bytes),
        as_attachment=False,
        filename=filename,
        content_type="application/pdf",
    )


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def offering_enrollment_view(request, offering_id: int):
    offering = get_object_or_404(
        CourseOffering.objects.select_related("term", "course", "section", "campus", "department", "tenant"),
        id=offering_id,
        is_active=True,
        faculty_assignments__faculty_user_id=request.user.id,
        faculty_assignments__is_active=True,
    )
    mode = EnrollmentService.get_enrollment_mode(offering.tenant_id)
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
        .all()
        .order_by("student__last_name", "student__first_name", "student__student_no")
    )
    context = {
        "offering": offering,
        "mode": mode,
        "can_manage": can_create_enrollment,
        "can_update_status": can_update_status,
        "form": form,
        "enrollments": enrollments,
    }
    return render(request, "faculty_portal/offering_enrollment.html", context)
