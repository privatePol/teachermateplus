from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.services.audit import AuditService
from apps.faculty_portal.models import FacultyFeedback
from apps.tenants.models import Campus, Tenant


FEATURES_BY_ROUTE = {
    "faculty_portal:dashboard": ("DASHBOARD", "Dashboard"),
    "faculty_portal:my_courses": ("MY_COURSES", "My Courses"),
    "faculty_portal:offering_periods": ("MY_COURSES", "My Courses"),
    "faculty_portal:period_activities": ("ACTIVITIES", "Activities"),
    "faculty_portal:activity_scores": ("SCORE_ENCODING", "Score Encoding"),
    "faculty_portal:period_attendance": ("ATTENDANCE", "Attendance"),
    "faculty_portal:attendance_summary": ("ATTENDANCE", "Attendance"),
    "faculty_portal:period_summary": ("GRADE_SUMMARY", "Grade Summary"),
    "faculty_portal:period_submit": ("GRADE_SUBMISSION", "Grade Submission"),
    "faculty_portal:period_self_reopen": ("GRADE_SUBMISSION", "Grade Submission"),
    "faculty_portal:period_reopen_request": ("GRADE_SUBMISSION", "Grade Submission"),
    "faculty_portal:period_corrections": ("CORRECTIONS", "Corrections"),
    "faculty_portal:student_performance_consultation": ("STUDENT_CONSULTATION", "Student Consultation"),
    "faculty_portal:guide": ("FACULTY_GUIDE", "Faculty Guide"),
    "faculty_portal:guide_manual": ("FACULTY_GUIDE", "Faculty Guide"),
    "faculty_portal:operational_policies": ("FACULTY_GUIDE", "Faculty Guide"),
}
DEFAULT_FEATURE = ("OTHER_FACULTY_PORTAL_PAGE", "Other Faculty Portal Page")
COOLDOWN_MINUTES = 5


@dataclass(frozen=True)
class FeedbackSubmissionResult:
    success: bool
    message: str
    feedback: FacultyFeedback | None = None
    errors: dict[str, list[str]] | None = None


def sanitize_relative_path(value: str | None) -> str:
    raw_value = (value or "").strip()
    if not raw_value:
        return ""
    try:
        parsed = urlsplit(raw_value)
    except ValueError:
        return ""
    if parsed.scheme or parsed.netloc:
        return ""
    path = parsed.path or ""
    if not path.startswith("/"):
        path = f"/{path}"
    if not path.startswith("/faculty/"):
        return ""
    return path[:255]


def sanitize_route_name(value: str | None) -> str:
    route_name = (value or "").strip()[:128]
    if not re.fullmatch(r"[A-Za-z0-9_:\-.]+", route_name or ""):
        return ""
    if not route_name.startswith("faculty_portal:"):
        return ""
    return route_name


def feature_for_route(route_name: str) -> tuple[str, str]:
    return FEATURES_BY_ROUTE.get(route_name, DEFAULT_FEATURE)


def user_agent_summary(request) -> str:
    user_agent = (request.META.get("HTTP_USER_AGENT") or "").replace("\r", " ").replace("\n", " ")
    return " ".join(user_agent.split())[:160]


def _scope_object(model, value):
    if hasattr(value, "pk"):
        return value
    if value:
        return model.objects.filter(pk=value).first()
    return None


def resolve_feedback_scope(request):
    tenant = _scope_object(Tenant, getattr(request, "scope", {}).get("tenant_id"))
    campus = _scope_object(Campus, getattr(request, "scope", {}).get("campus_id"))
    if tenant is None:
        tenant = getattr(request.user, "default_tenant", None)
    if campus is None:
        campus = getattr(request.user, "default_campus", None)
    if tenant is None:
        raise ValidationError("A tenant scope is required before submitting feedback.")
    if campus and campus.tenant_id != tenant.id:
        campus = None
    return tenant, campus


def create_feedback_submission(*, request, form) -> FeedbackSubmissionResult:
    if not form.is_valid():
        return FeedbackSubmissionResult(
            success=False,
            message="Please correct the feedback form.",
            errors={field: [str(error) for error in errors] for field, errors in form.errors.items()},
        )

    tenant, campus = resolve_feedback_scope(request)
    page_path = sanitize_relative_path(form.cleaned_data.get("page_path")) or sanitize_relative_path(request.path)
    referrer_path = sanitize_relative_path(form.cleaned_data.get("referrer_path"))
    route_name = sanitize_route_name(form.cleaned_data.get("route_name"))
    feature_code, feature_label = feature_for_route(route_name)

    cooldown_since = timezone.now() - timedelta(minutes=COOLDOWN_MINUTES)
    duplicate_qs = FacultyFeedback.objects.filter(
        faculty_user=request.user,
        tenant=tenant,
        created_at__gte=cooldown_since,
    )
    if route_name:
        duplicate_qs = duplicate_qs.filter(route_name=route_name)
    else:
        duplicate_qs = duplicate_qs.filter(page_path=page_path)
    if duplicate_qs.exists():
        return FeedbackSubmissionResult(
            success=False,
            message="Feedback was already submitted for this page. Please try again in a few minutes.",
            errors={"__all__": ["Please wait a few minutes before submitting feedback again for this page."]},
        )

    feedback = FacultyFeedback.objects.create(
        faculty_user=request.user,
        tenant=tenant,
        campus=campus,
        rating=form.cleaned_data["rating"],
        suggestion=form.cleaned_data.get("suggestion") or "",
        page_path=page_path,
        route_name=route_name,
        feature_code=feature_code,
        referrer_path=referrer_path,
        app_version=(getattr(settings, "APP_VERSION", "") or getattr(settings, "RELEASE_VERSION", ""))[:64],
        user_agent_summary=user_agent_summary(request),
    )
    AuditService.log_event(
        action="FACULTY_FEEDBACK_SUBMITTED",
        portal="FACULTY",
        entity_type="FacultyFeedback",
        entity_id=feedback.id,
        actor=request.user,
        tenant=tenant,
        campus=campus,
        metadata={
            "rating": feedback.rating,
            "route_name": feedback.route_name,
            "feature_code": feedback.feature_code,
            "feature_label": feature_label,
        },
        request=request,
    )
    return FeedbackSubmissionResult(
        success=True,
        message="Thank you. Your feedback has been submitted.",
        feedback=feedback,
    )
