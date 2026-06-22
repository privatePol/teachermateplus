from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.academics.services import AcademicGovernanceService
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.core.services.audit import AuditService
from apps.core.services.permissions import PermissionService
from apps.enrollment.models import Enrollment
from apps.faculty_portal.services import FacultyDashboardUpdatesService, FacultyPerformanceService
from apps.grading.explanations import GradeExplanationService
from apps.grading.models import (
    GradeActivity,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplateSubcomponent,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.notifications.models import FacultyReminder, SubmissionNonComplianceNotice


logger = logging.getLogger(__name__)
MOBILE_PORTAL = "FACULTY"
MOBILE_SOURCE = "mobile_api"
FACULTY_PERMISSION = "faculty_portal.access"


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def success(data=None, *, message=None, status=200):
    return JsonResponse({"ok": True, "data": data if data is not None else {}, "message": message}, status=status)


def failure(code: str, message: str, *, status=400):
    return JsonResponse({"ok": False, "error": {"code": code, "message": message}}, status=status)


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "messages"):
        return "; ".join(str(item) for item in exc.messages)
    return str(exc)


def _validation_code(message: str) -> str:
    lowered = message.lower()
    lock_terms = ("locked", "submitted", "encoding is closed", "encoding is temporarily disabled", "deadline")
    if any(term in lowered for term in lock_terms):
        return "encoding_closed"
    return "validation_error"


def _request_payload(request) -> dict:
    content_type = (request.META.get("CONTENT_TYPE") or "").lower()
    if "application/json" in content_type:
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("validation_error", "Invalid JSON payload.") from exc
    return request.POST.dict()


def mobile_api_view(methods, *, auth_required=True):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return failure("method_not_allowed", "HTTP method is not allowed.", status=405)
            try:
                if auth_required:
                    _require_faculty_user(request.user)
                return view_func(request, *args, **kwargs)
            except ApiError as exc:
                return failure(exc.code, exc.message, status=exc.status)
            except ValidationError as exc:
                message = _validation_message(exc)
                code = _validation_code(message)
                status = 423 if code == "encoding_closed" else 400
                return failure(code, "Encoding is closed for this grading period." if code == "encoding_closed" else message, status=status)
            except Exception:
                logger.exception("Mobile API request failed at %s", request.path)
                return failure("server_error", "The request could not be completed.", status=500)

        return wrapper

    return decorator


def _require_faculty_user(user):
    if not user or not user.is_authenticated:
        raise ApiError("authentication_required", "Authentication is required.", status=401)
    if not getattr(user, "is_active", False):
        raise ApiError("forbidden", "Your account is inactive.", status=403)
    if not PermissionService.has_permission(user, FACULTY_PERMISSION):
        raise ApiError("forbidden", "Faculty access is required.", status=403)


def _user_payload(user):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "name": user.full_name,
        "tenant_id": user.default_tenant_id,
        "campus_id": user.default_campus_id,
        "department_id": user.default_department_id,
    }


def _assigned_assignment_qs(user):
    return (
        FacultyAssignment.objects.filter(
            faculty_user=user,
            is_active=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at__isnull=False,
            offering__is_active=True,
            offering__tenant__is_active=True,
            offering__campus__is_active=True,
        )
        .select_related(
            "offering",
            "offering__tenant",
            "offering__campus",
            "offering__department",
            "offering__academic_year",
            "offering__term",
            "offering__course",
            "offering__section",
        )
        .order_by("offering__course__code", "offering__section__code", "id")
    )


def _is_scoped_faculty(user, offering) -> bool:
    return PermissionService.has_permission(
        user,
        FACULTY_PERMISSION,
        tenant_id=offering.tenant_id,
        campus_id=offering.campus_id,
    )


def _assigned_offerings(user):
    offerings = []
    seen = set()
    for assignment in _assigned_assignment_qs(user):
        offering = assignment.offering
        if offering.id in seen or not _is_scoped_faculty(user, offering):
            continue
        seen.add(offering.id)
        offerings.append(offering)
    return offerings


def _get_assigned_offering(user, offering_id: int):
    offering = (
        CourseOffering.objects.select_related(
            "tenant",
            "campus",
            "department",
            "program",
            "academic_year",
            "term",
            "course",
            "section",
        )
        .filter(id=offering_id, is_active=True)
        .first()
    )
    if not offering:
        raise ApiError("not_found", "Class was not found.", status=404)
    assigned = FacultyAssignment.objects.filter(
        offering=offering,
        faculty_user=user,
        is_active=True,
        response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        accepted_at__isnull=False,
    ).exists()
    if not assigned or not _is_scoped_faculty(user, offering):
        raise ApiError("forbidden", "You are not assigned to this class.", status=403)
    return offering


def _student_name(student) -> str:
    return " ".join(part for part in [student.first_name, student.middle_name, student.last_name] if part).strip()


def _student_payload(student):
    return {"id": student.id, "student_no": student.student_no, "name": _student_name(student)}


def _class_payload(offering):
    return {
        "offering_id": offering.id,
        "course_code": offering.course.code,
        "course_title": offering.course.title,
        "section": offering.section.code,
        "section_name": offering.section.name,
        "academic_year": offering.academic_year.code,
        "term": offering.term.code,
        "campus": offering.campus.code,
        "schedule": offering.schedule_text or "",
        "status": offering.status,
    }


def _active_enrollments(offering):
    return (
        FacultyGradingService.get_active_enrollments(offering)
        .filter(student__is_active=True)
        .select_related("student", "student__program")
    )


def _get_enrolled_student(offering, student_id: int, *, active_only=False):
    queryset = Enrollment.objects.filter(
        course_offering=offering,
        student_id=student_id,
        is_active=True,
        student__is_active=True,
    ).select_related("student")
    if active_only:
        queryset = queryset.exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)
    enrollment = queryset.first()
    if not enrollment:
        raise ApiError("forbidden", "Student is not enrolled in this class.", status=403)
    return enrollment.student, enrollment


def _resolve_template(offering):
    try:
        return FacultyGradingService.resolve_template_for_offering(offering)
    except ValidationError as exc:
        raise ApiError("validation_error", _validation_message(exc), status=400) from exc


def _resolve_period(offering, period_id=None):
    template = _resolve_template(offering)
    periods = list(FacultyGradingService.get_template_periods(template))
    if not periods:
        raise ApiError("validation_error", "No active grading period is configured for this class.")
    if period_id:
        for period in periods:
            if period.id == int(period_id):
                return template, period
        raise ApiError("validation_error", "Selected grading period does not belong to this class.")

    active_setting = AcademicGovernanceService.resolve_active_grading_period(
        tenant_id=offering.tenant_id,
        campus_id=offering.campus_id,
        term_id=offering.term_id,
        now=timezone.now(),
    )
    for period in periods:
        if AcademicGovernanceService.template_period_matches_active_period(
            template_period=period,
            active_period_setting=active_setting,
        ):
            return template, period
    return template, periods[0]


def _decimal(value, field_name: str) -> Decimal:
    if value in (None, ""):
        raise ApiError("validation_error", f"{field_name} is required.")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ApiError("validation_error", f"{field_name} must be a valid number.") from exc


def _date(value, *, default_today=False):
    if not value and default_today:
        return timezone.localdate()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ApiError("validation_error", "Date must use YYYY-MM-DD format.") from exc


def _activity_for_faculty(user, activity_id: int):
    activity = (
        GradeActivity.objects.select_related(
            "offering",
            "offering__tenant",
            "offering__campus",
            "offering__course",
            "offering__section",
            "template_period",
            "template_component",
            "template_subcomponent",
            "template_detail",
        )
        .filter(id=activity_id, is_active=True)
        .first()
    )
    if not activity:
        raise ApiError("not_found", "Activity was not found.", status=404)
    _get_assigned_offering(user, activity.offering_id)
    return activity


@csrf_exempt
@mobile_api_view({"POST"}, auth_required=False)
def login_view(request):
    payload = _request_payload(request)
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if not username or not password:
        raise ApiError("validation_error", "Username and password are required.")
    user = authenticate(request, username=username, password=password)
    if user is None:
        AuditService.log_login_failure(request, username=username, portal=MOBILE_PORTAL)
        return failure("invalid_credentials", "Invalid username or password.", status=401)
    _require_faculty_user(user)
    login(request, user)
    AuditService.log_login_success(request, user, portal=MOBILE_PORTAL)
    return success({"user": _user_payload(user)})


@mobile_api_view({"POST"})
def logout_view(request):
    logout(request)
    return success({}, message="Signed out.")


@ensure_csrf_cookie
@mobile_api_view({"GET"})
def me_view(request):
    return success({"user": _user_payload(request.user)})


@mobile_api_view({"GET"})
def dashboard_view(request):
    offerings = _assigned_offerings(request.user)
    active_offerings = [offering for offering in offerings if offering.status == CourseOffering.Status.OPEN]
    student_count = Enrollment.objects.filter(
        course_offering_id__in=[offering.id for offering in offerings],
        is_active=True,
    ).exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES).count()
    actions = FacultyDashboardUpdatesService.build_priority_actions(
        user=request.user,
        active_offerings=active_offerings,
        at_risk_total=0,
    )
    updates = FacultyDashboardUpdatesService.get_dashboard_updates(
        user=request.user,
        offerings=offerings,
        limit=5,
    )
    return success(
        {
            "faculty": _user_payload(request.user),
            "assigned_class_count": len(offerings),
            "active_class_count": len(active_offerings),
            "active_student_count": student_count,
            "priority_actions": actions,
            "recent_updates": updates,
        }
    )


@mobile_api_view({"GET"})
def notifications_view(request):
    offerings = _assigned_offerings(request.user)
    updates = FacultyDashboardUpdatesService.get_dashboard_updates(
        user=request.user,
        offerings=offerings,
        limit=20,
    )
    reminders = [
        {
            "id": row.id,
            "title": row.title,
            "message": row.message or "",
            "due_at": row.due_at,
            "offering_id": row.offering_id,
        }
        for row in FacultyReminder.objects.filter(
            faculty_user=request.user,
            offering_id__in=[offering.id for offering in offerings],
            is_active=True,
        ).order_by("-created_at", "-id")[:20]
    ]
    notices = [
        {
            "id": row.id,
            "title": row.title,
            "notice_level": row.notice_level,
            "issued_at": row.issued_at,
            "offering_id": row.offering_id,
            "period_id": row.template_period_id,
        }
        for row in SubmissionNonComplianceNotice.objects.filter(
            faculty_user=request.user,
            offering_id__in=[offering.id for offering in offerings],
        ).order_by("-issued_at", "-id")[:20]
    ]
    return success({"updates": updates, "reminders": reminders, "submission_notices": notices})


@mobile_api_view({"GET"})
def classes_view(request):
    return success({"classes": [_class_payload(offering) for offering in _assigned_offerings(request.user)]})


@mobile_api_view({"GET"})
def class_snapshot_view(request, offering_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    template, period = _resolve_period(offering, request.GET.get("period_id"))
    active_count = _active_enrollments(offering).exclude(
        enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES
    ).count()
    activity_count = GradeActivity.objects.filter(
        offering=offering,
        template_period=period,
        is_active=True,
    ).count()
    attendance_session_count = AttendanceSession.objects.filter(
        offering=offering,
        template_period=period,
        is_active=True,
    ).count()
    readiness = GradingGovernanceService.evaluate_submission_readiness(
        offering=offering,
        template_period=period,
    )
    return success(
        {
            "class": _class_payload(offering),
            "template": {"id": template.id, "code": template.code, "name": template.name},
            "period": {"id": period.id, "code": period.code, "name": period.name},
            "active_student_count": active_count,
            "activity_count": activity_count,
            "attendance_session_count": attendance_session_count,
            "readiness": readiness,
        }
    )


@mobile_api_view({"GET"})
def students_view(request, offering_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    rows = [
        {
            **_student_payload(enrollment.student),
            "status": enrollment.enrollment_status,
            "program": enrollment.student.program.code if enrollment.student.program_id else "",
            "year_level": enrollment.student.year_level or "",
        }
        for enrollment in _active_enrollments(offering)
    ]
    return success({"class": _class_payload(offering), "students": rows})


@mobile_api_view({"GET"})
def student_search_view(request, offering_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    query = (request.GET.get("q") or "").strip()
    enrollments = _active_enrollments(offering)
    if query:
        enrollments = enrollments.filter(
            Q(student__student_no__icontains=query)
            | Q(student__first_name__icontains=query)
            | Q(student__middle_name__icontains=query)
            | Q(student__last_name__icontains=query)
        )
    return success({"students": [_student_payload(row.student) for row in enrollments[:25]]})


@mobile_api_view({"GET"})
def student_summary_view(request, offering_id: int, student_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    student, enrollment = _get_enrolled_student(offering, student_id)
    period_rows = [
        {
            "period_id": row.template_period_id,
            "period_code": row.template_period.code,
            "period_name": row.template_period.name,
            "period_grade": row.period_grade,
            "class_standing_grade": row.class_standing_grade,
            "exam_grade": row.exam_grade,
        }
        for row in StudentPeriodGrade.objects.filter(offering=offering, student=student)
        .select_related("template_period")
        .order_by("template_period__sequence_no", "template_period_id")
    ]
    return success(
        {
            "student": _student_payload(student),
            "class": _class_payload(offering),
            "enrollment_status": enrollment.enrollment_status,
            "period_grades": period_rows,
        }
    )


@mobile_api_view({"GET"})
def consultation_summary_view(request, offering_id: int, student_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    student, _enrollment = _get_enrolled_student(offering, student_id, active_only=True)
    _template, period = _resolve_period(offering, request.GET.get("period_id"))
    trend = FacultyPerformanceService.get_student_performance_trend(student, offering, period)
    if not trend:
        raise ApiError("not_found", "Student performance data is unavailable.", status=404)
    weakest = trend.get("weakest_component") or {}
    component_breakdown = trend.get("detail", {}).get("component_breakdown", [])
    ranked_components = sorted(
        [row for row in component_breakdown if row.get("score") is not None],
        key=lambda row: Decimal(row["score"]),
    )
    return success(
        {
            "student": _student_payload(student),
            "class": _class_payload(offering),
            "period": {"id": period.id, "code": period.code, "name": period.name},
            "current_standing": trend.get("current_grade"),
            "period_trend": {
                "current_grade": trend.get("current_grade"),
                "previous_grade": trend.get("previous_grade"),
                "delta": trend.get("delta"),
                "label": trend.get("trend_label"),
                "reason": trend.get("primary_reason"),
            },
            "component_strengths": ranked_components[-3:],
            "component_weaknesses": ranked_components[:3],
            "missing_activities": trend.get("missing_outputs", []),
            "attendance_concerns": [],
            "talking_points": [item for item in [trend.get("primary_reason"), weakest.get("name")] if item],
        }
    )


def _flatten_activity_scores(explanation):
    rows = []
    for component in explanation.get("component_breakdown", []):
        for activity in component.get("activities", []):
            rows.append(activity)
        for subcomponent in component.get("subcomponents", []):
            for activity in subcomponent.get("activities", []):
                rows.append(activity)
            for detail in subcomponent.get("details", []):
                for activity in detail.get("activities", []):
                    rows.append(activity)
    return rows


@mobile_api_view({"GET"})
def grade_explanation_view(request, offering_id: int, student_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    student, _enrollment = _get_enrolled_student(offering, student_id)
    _template, period = _resolve_period(offering, request.GET.get("period_id"))
    grade_type = (request.GET.get("grade_type") or GradeExplanationService.GRADE_TYPE_PERIOD).upper()
    if grade_type not in {GradeExplanationService.GRADE_TYPE_PERIOD, GradeExplanationService.GRADE_TYPE_FINAL}:
        raise ApiError("validation_error", "grade_type must be PERIOD or FINAL.")
    explanation = GradeExplanationService.build(
        offering=offering,
        student=student,
        template_period=period,
        grade_type=grade_type,
        mask_identity=False,
        include_correction_history=False,
    )
    AuditService.log_event(
        action="READ",
        portal=MOBILE_PORTAL,
        entity_type="GradeExplanation",
        entity_id=f"{offering.id}:{period.id}:{student.id}:{grade_type}",
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        metadata={
            "source": MOBILE_SOURCE,
            "offering_id": offering.id,
            "period_id": period.id,
            "student_id": student.id,
            "grade_type": grade_type,
        },
        request=request,
    )
    return success(
        {
            "student": _student_payload(student),
            "class": _class_payload(offering),
            "period": explanation.get("period"),
            "components": explanation.get("component_breakdown", []),
            "activity_scores": _flatten_activity_scores(explanation),
            "attendance_contribution": None,
            "missing_scores": [row for row in _flatten_activity_scores(explanation) if row.get("missing")],
            "computed_result": {
                "grade_type": grade_type,
                "official_value": explanation.get("official_value"),
                "computed_official_value": explanation.get("computed_official_value"),
                "raw_value": explanation.get("raw_value"),
                "pass_fail": explanation.get("pass_fail"),
                "formula_text": explanation.get("formula_text"),
            },
            "notes": explanation.get("warnings", []),
            "server_explanation": explanation,
        }
    )


@mobile_api_view({"GET"})
def attendance_today_view(request, offering_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    _template, period = _resolve_period(offering, request.GET.get("period_id"))
    today = timezone.localdate()
    session = AttendanceSession.objects.filter(
        offering=offering,
        template_period=period,
        session_date=today,
        is_active=True,
    ).first()
    records = {}
    if session:
        records = {row.student_id: row for row in session.records.filter(is_active=True)}
    rows = []
    for enrollment in _active_enrollments(offering).exclude(
        enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES
    ):
        record = records.get(enrollment.student_id)
        rows.append(
            {
                "student": _student_payload(enrollment.student),
                "status_code": record.status_code if record else None,
                "status_label": record.get_status_code_display() if record else None,
                "remarks": record.remarks if record else "",
            }
        )
    return success(
        {
            "class": _class_payload(offering),
            "period": {"id": period.id, "code": period.code, "name": period.name},
            "date": today,
            "session": {"id": session.id, "title": session.title} if session else None,
            "records": rows,
            "status_choices": [{"code": code, "label": label} for code, label in AttendanceRecord.Status.choices],
        }
    )


@mobile_api_view({"POST"})
def attendance_save_view(request, offering_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    payload = _request_payload(request)
    _template, period = _resolve_period(offering, payload.get("period_id"))
    session_date = _date(payload.get("date"), default_today=True)
    rows = payload.get("records") or []
    if not isinstance(rows, list):
        raise ApiError("validation_error", "records must be a list.")
    valid_statuses = {code for code, _label in AttendanceRecord.Status.choices}
    enrolled_ids = set(
        _active_enrollments(offering)
        .exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)
        .values_list("student_id", flat=True)
    )
    status_payload = []
    for row in rows:
        student_id = int(row.get("student_id") or 0)
        if student_id not in enrolled_ids:
            raise ApiError("forbidden", "Student is not enrolled in this class.", status=403)
        status_code = str(row.get("status_code") or row.get("status") or "").upper()
        if status_code not in valid_statuses:
            raise ApiError("validation_error", "Attendance status is invalid.")
        status_payload.append(
            {
                "student_id": student_id,
                "status_code": status_code,
                "remarks": row.get("remarks") or "",
            }
        )
    session, _created = FacultyGradingService.create_or_update_attendance_session(
        user=request.user,
        offering=offering,
        template_period=period,
        session_date=session_date,
        title=payload.get("title"),
    )
    saved = FacultyGradingService.upsert_attendance_records(
        user=request.user,
        session=session,
        status_payload=status_payload,
    )
    AuditService.log_event(
        action="WRITE",
        portal=MOBILE_PORTAL,
        entity_type="AttendanceRecord",
        entity_id=session.id,
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        after_data={"saved_count": saved, "session_id": session.id, "date": session_date},
        metadata={"source": MOBILE_SOURCE},
        request=request,
    )
    return success({"session_id": session.id, "saved_count": saved})


@mobile_api_view({"GET"})
def quick_activity_options_view(request, offering_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    template = _resolve_template(offering)
    periods = []
    for period in FacultyGradingService.get_template_periods(template).prefetch_related(
        "components",
        "components__subcomponents",
        "components__subcomponents__details",
    ):
        component_rows = []
        for component in period.components.filter(is_active=True).order_by("sort_order", "id"):
            component_rows.append(
                {
                    "id": component.id,
                    "code": component.code,
                    "name": component.name,
                    "weight_percentage": component.weight_percentage,
                    "subcomponents": [
                        {
                            "id": subcomponent.id,
                            "code": subcomponent.code,
                            "name": subcomponent.name,
                            "weight_percentage": subcomponent.weight_percentage,
                            "is_attendance_component": subcomponent.is_attendance_component,
                            "details": [
                                {"id": detail.id, "code": detail.code, "name": detail.name}
                                for detail in subcomponent.details.filter(is_active=True).order_by("sort_order", "id")
                            ],
                        }
                        for subcomponent in component.subcomponents.filter(is_active=True).order_by("sort_order", "id")
                    ],
                }
            )
        periods.append({"id": period.id, "code": period.code, "name": period.name, "components": component_rows})
    return success({"class": _class_payload(offering), "template": {"id": template.id, "name": template.name}, "periods": periods})


@mobile_api_view({"POST"})
def quick_activity_create_view(request, offering_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    payload = _request_payload(request)
    _template, period = _resolve_period(offering, payload.get("period_id"))
    component = GradingTemplateComponent.objects.filter(
        id=payload.get("component_id"),
        template_period=period,
        is_active=True,
    ).first()
    if not component:
        raise ApiError("validation_error", "Selected component is invalid.")
    subcomponent = None
    if payload.get("subcomponent_id"):
        subcomponent = GradingTemplateSubcomponent.objects.filter(
            id=payload.get("subcomponent_id"),
            template_component=component,
            is_active=True,
        ).first()
        if not subcomponent:
            raise ApiError("validation_error", "Selected subcomponent is invalid.")
    detail = None
    if payload.get("detail_id"):
        detail = GradingTemplateDetail.objects.filter(
            id=payload.get("detail_id"),
            template_subcomponent=subcomponent,
            is_active=True,
        ).first()
        if not detail:
            raise ApiError("validation_error", "Selected detail is invalid.")

    total_points = _decimal(payload.get("total_points") or payload.get("total_score"), "total_points")
    if total_points <= 0:
        raise ApiError("validation_error", "Total points must be greater than zero.")
    activity_date = _date(payload.get("date") or payload.get("activity_date"), default_today=True)
    activity_type = (payload.get("activity_type") or "").strip()
    title = (payload.get("title") or "").strip()
    if not title:
        title = f"{activity_type or 'Activity'} {activity_date.isoformat()}"
    activity = FacultyGradingService.create_activity(
        user=request.user,
        offering=offering,
        template_period=period,
        template_component=component,
        template_subcomponent=subcomponent,
        template_detail=detail,
        title=title,
        total_score=total_points,
        activity_date=activity_date,
    )
    AuditService.log_event(
        action="CREATE",
        portal=MOBILE_PORTAL,
        entity_type="GradeActivity",
        entity_id=activity.id,
        actor=request.user,
        tenant=offering.tenant,
        campus=offering.campus,
        after_data={"title": activity.title, "total_score": activity.total_score, "period_id": period.id},
        metadata={"source": MOBILE_SOURCE},
        request=request,
    )
    return success({"activity": _activity_payload(activity)}, status=201)


def _activity_payload(activity):
    return {
        "id": activity.id,
        "offering_id": activity.offering_id,
        "period_id": activity.template_period_id,
        "title": activity.title,
        "total_score": activity.total_score,
        "activity_date": activity.activity_date,
        "component_id": activity.template_component_id,
        "subcomponent_id": activity.template_subcomponent_id,
        "detail_id": activity.template_detail_id,
    }


@mobile_api_view({"GET"})
def activity_scores_view(request, activity_id: int):
    activity = _activity_for_faculty(request.user, activity_id)
    score_by_student = {
        row.student_id: row
        for row in StudentActivityScore.objects.filter(activity=activity, is_active=True)
    }
    rows = []
    for enrollment in _active_enrollments(activity.offering).exclude(
        enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES
    ):
        score = score_by_student.get(enrollment.student_id)
        rows.append(
            {
                "student": _student_payload(enrollment.student),
                "raw_score": score.raw_score if score else None,
                "computed_score": score.computed_score if score else None,
                "remarks": score.remarks if score else "",
                "encoded": score is not None,
            }
        )
    return success({"activity": _activity_payload(activity), "scores": rows})


@mobile_api_view({"POST"})
def activity_scores_save_view(request, activity_id: int):
    activity = _activity_for_faculty(request.user, activity_id)
    payload = _request_payload(request)
    rows = payload.get("scores") or payload.get("records") or []
    if not isinstance(rows, list):
        raise ApiError("validation_error", "scores must be a list.")
    enrolled_ids = set(
        _active_enrollments(activity.offering)
        .exclude(enrollment_status__in=Enrollment.NON_ACTIVE_GRADING_STATUSES)
        .values_list("student_id", flat=True)
    )
    before_rows = list(
        StudentActivityScore.objects.filter(activity=activity, is_active=True)
        .values("student_id", "raw_score", "computed_score")
    )
    score_payload = []
    for row in rows:
        student_id = int(row.get("student_id") or 0)
        if student_id not in enrolled_ids:
            raise ApiError("forbidden", "Student is not enrolled in this class.", status=403)
        raw_value = row.get("raw_score")
        if raw_value in (None, ""):
            score_payload.append({"student_id": student_id, "clear": True})
            continue
        raw = _decimal(raw_value, "raw_score")
        if raw < 0:
            raise ApiError("validation_error", "Score cannot be below zero.")
        if raw > Decimal(activity.total_score):
            raise ApiError("validation_error", "Score cannot exceed total points.")
        score_payload.append({"student_id": student_id, "raw_score": raw, "remarks": row.get("remarks") or ""})
    saved = FacultyGradingService.upsert_activity_scores(
        user=request.user,
        activity=activity,
        score_payload=score_payload,
        audit_portal=MOBILE_PORTAL,
    )
    after_rows = list(
        StudentActivityScore.objects.filter(activity=activity, is_active=True)
        .values("student_id", "raw_score", "computed_score")
    )
    AuditService.log_event(
        action="WRITE",
        portal=MOBILE_PORTAL,
        entity_type="StudentActivityScore",
        entity_id=activity.id,
        actor=request.user,
        tenant=activity.offering.tenant,
        campus=activity.offering.campus,
        before_data=before_rows,
        after_data=after_rows,
        metadata={"source": MOBILE_SOURCE, "saved_count": saved, "activity_id": activity.id},
        request=request,
    )
    return success({"activity_id": activity.id, "saved_count": saved})


@mobile_api_view({"GET"})
def missing_scores_view(request, offering_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    _template, period = _resolve_period(offering, request.GET.get("period_id"))
    readiness = GradingGovernanceService.evaluate_submission_readiness(
        offering=offering,
        template_period=period,
    )
    return success(
        {
            "class": _class_payload(offering),
            "period": {"id": period.id, "code": period.code, "name": period.name},
            "missing_students": readiness.get("missing_students", []),
            "missing_template_items": readiness.get("missing_template_items", []),
        }
    )


@mobile_api_view({"GET"})
def submission_readiness_view(request, offering_id: int):
    offering = _get_assigned_offering(request.user, offering_id)
    _template, period = _resolve_period(offering, request.GET.get("period_id"))
    readiness = GradingGovernanceService.evaluate_submission_readiness(
        offering=offering,
        template_period=period,
    )
    submission = GradingGovernanceService.get_submission(offering=offering, template_period=period)
    lock = GradingGovernanceService.resolve_lock(offering=offering, template_period=period)
    return success(
        {
            "class": _class_payload(offering),
            "period": {"id": period.id, "code": period.code, "name": period.name},
            "readiness": readiness,
            "submission_status": submission.status if submission else None,
            "is_locked": bool(lock and lock.is_locked),
        }
    )
