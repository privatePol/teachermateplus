from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django import forms as django_forms
from django.core.mail import EmailMultiAlternatives
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Prefetch, Q, Sum
from io import BytesIO

from django.http import FileResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone

from apps.admin_portal.forms import (
    ActiveAcademicTermSettingForm,
    AcademicYearForm,
    CampusForm,
    ConfigurableFeatureSettingForm,
    CorrectionApprovalRouteRuleForm,
    CorrectionGovernanceSettingForm,
    CourseForm,
    CourseOfferingForm,
    CourseBaseValueOverrideForm,
    CourseTemplateAssignmentForm,
    DocumentPrintSettingForm,
    DepartmentForm,
    FacultyAssignmentForm,
    GradeCorrectionReviewForm,
    GradeSubmissionReopenRequestForm,
    GradeSubmissionReopenReviewForm,
    GradingTemplateComponentForm,
    GradingTemplateApprovalReviewForm,
    GradingTemplateApprovalSubmitForm,
    GradingTemplateDetailForm,
    GradingTemplateForm,
    GradingPeriodLockForm,
    GradingTemplatePeriodForm,
    GradingTemplateSubcomponentForm,
    MenuGroupForm,
    MenuItemForm,
    ProgramForm,
    RoleForm,
    RolePermissionsForm,
    SectionForm,
    StudentForm,
    TenantForm,
    TemplateHotfixRequestForm,
    TemplateHotfixReviewForm,
    TenantGradingProfileForm,
    TermForm,
    UserCreateForm,
    UserChangePasswordForm,
    UserRoleAssignmentForm,
    UserUpdateForm,
)
from apps.academics.services import AcademicGovernanceService
from apps.admin_portal.services import AdminScopeService, model_before_after
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.auditlog.models import AuditLog
from apps.core.decorators import permission_required, portal_required
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.forms import EnrollmentForm
from apps.enrollment.models import Enrollment
from apps.enrollment.services import EnrollmentService
from apps.faculty_portal.views import _build_summary_layout, _build_summary_row_values, _period_edit_state
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    GradeActivity,
    GradeCorrectionApprovalStep,
    GradeCorrectionRequest,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    StudentActivityScore,
    StudentPeriodGrade,
    TemplateHotfixRequest,
    TenantGradingProfile,
)
from apps.grading.notifications import CorrectionNotificationService
from apps.grading.reporting import CorrectionOfficialReportService
from apps.grading.services import (
    FacultyGradingService,
    GradingGovernanceService,
    GradingTemplateService,
    TemplateHotfixService,
)
from apps.imports.models import ImportBatch
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant

User = get_user_model()


def _official_correction_report_filename(correction_request: GradeCorrectionRequest) -> str:
    period_code = correction_request.template_period.code or "PERIOD"
    course_code = correction_request.offering.course.code or "COURSE"
    section_code = correction_request.offering.section.code or "SECTION"
    return f"official-correction-{correction_request.id}-{course_code}-{section_code}-{period_code}.pdf"


def _scope_context(request):
    tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
    campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
    current_tenant_id = getattr(request, "scope", {}).get("tenant_id")
    current_campus_id = getattr(request, "scope", {}).get("campus_id")

    scope_tenants = Tenant.objects.filter(id__in=tenant_ids).order_by("name")
    scope_campuses = Campus.objects.filter(id__in=campus_ids).order_by("name")
    if not request.user.is_superuser:
        scope_tenants = scope_tenants.filter(is_active=True)
        scope_campuses = scope_campuses.filter(is_active=True)

    return {
        "scope_tenants": scope_tenants,
        "scope_campuses": scope_campuses,
        "current_tenant_id": current_tenant_id,
        "current_campus_id": current_campus_id,
    }


def _style_form(form):
    for field in form.fields.values():
        widget = field.widget
        widget_name = widget.__class__.__name__
        if isinstance(field, django_forms.DateField) and not isinstance(field, django_forms.DateTimeField):
            if getattr(widget, "input_type", None) != "date":
                field.widget = django_forms.DateInput(
                    attrs={**widget.attrs, "type": "date"},
                    format="%Y-%m-%d",
                )
                widget = field.widget
                widget_name = widget.__class__.__name__
        if widget_name in {"CheckboxSelectMultiple", "RadioSelect"}:
            # Do not apply single-input classes to grouped choice widgets.
            existing = widget.attrs.get("class", "")
            cleaned = " ".join(
                cls_name
                for cls_name in existing.split()
                if cls_name not in {"form-control", "form-check-input"}
            )
            if cleaned:
                widget.attrs["class"] = cleaned
            else:
                widget.attrs.pop("class", None)
            continue
        if getattr(widget, "input_type", None) == "checkbox":
            widget.attrs["class"] = "form-check-input"
        elif widget_name in {"Select", "SelectMultiple"}:
            widget.attrs["class"] = "form-select"
        else:
            widget.attrs["class"] = "form-control"
    return form


def _get_page(request, queryset, per_page=20):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page", 1))


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _redirect_back_or_default(request, fallback_route_name: str, **kwargs):
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(fallback_route_name, **kwargs)


def _user_has_role_code(user, *role_codes):
    role_code_set = {code.upper() for code in role_codes}
    return UserRole.objects.filter(
        user=user,
        is_active=True,
        role__is_active=True,
        role__code__in=role_code_set,
    ).exists()


def _should_mask_gradebook_student_identity(user):
    active_role_codes = {
        code.upper()
        for code in UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
        ).values_list("role__code", flat=True)
    }
    return (
        "DEAN" in active_role_codes
        or "CAO" in active_role_codes
        or "AC" in active_role_codes
        or any(code.endswith("_AC") for code in active_role_codes)
    )


def _mask_student_number(student_no: str | None) -> str:
    raw_value = str(student_no or "")
    if len(raw_value) <= 4:
        return "*" * len(raw_value)
    masked = []
    for index, char in enumerate(raw_value):
        if char == "-":
            masked.append("-")
        elif index < 2 or index >= len(raw_value) - 2:
            masked.append(char)
        elif index % 3 == 0:
            masked.append(char)
        else:
            masked.append("*")
    return "".join(masked)


def _mask_word(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 2:
        return text[0] + "*" * max(len(text) - 1, 0)
    return text[0] + "*" * (len(text) - 2) + text[-1]


def _mask_student_name(student) -> str:
    parts = [student.first_name, student.middle_name, student.last_name]
    masked_parts = [_mask_word(part) for part in parts if part]
    return " ".join(masked_parts)


def _format_metric_value(value):
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        return format(GradingGovernanceService._round(value), ".2f")
    return str(value)


def _gradebook_metrics(rows, *, passing_threshold: Decimal):
    active_count = len(rows)
    graded_periods = [Decimal(row["period_grade"]) for row in rows if row.get("period_grade") is not None]
    class_standing_values = [Decimal(row["class_standing"]) for row in rows if row.get("class_standing") is not None]
    exam_values = [Decimal(row["exam_grade"]) for row in rows if row.get("exam_grade") is not None]
    graded_count = len(graded_periods)
    passed_count = len([value for value in graded_periods if value >= passing_threshold])
    failed_count = max(graded_count - passed_count, 0)
    coverage = round((graded_count / active_count) * 100, 1) if active_count else 0
    pass_rate = round((passed_count / graded_count) * 100, 1) if graded_count else 0

    def _average(values):
        if not values:
            return None
        return GradingGovernanceService._round(sum(values) / Decimal(len(values)))

    return [
        {
            "label": "Active Students",
            "value": active_count,
            "meta": "Students included in the selected class and period.",
        },
        {
            "label": "With Period Grade",
            "value": f"{graded_count}/{active_count}",
            "meta": f"Coverage {coverage:.1f}%",
        },
        {
            "label": "Class Standing Avg",
            "value": _format_metric_value(_average(class_standing_values)),
            "meta": "Average class standing for graded students.",
        },
        {
            "label": "Exam Avg",
            "value": _format_metric_value(_average(exam_values)),
            "meta": "Average exam score for graded students.",
        },
        {
            "label": "Period Avg",
            "value": _format_metric_value(_average(graded_periods)),
            "meta": f"Passing threshold {format(passing_threshold, '.2f')}",
        },
        {
            "label": "Pass Rate",
            "value": f"{pass_rate:.1f}%",
            "meta": f"{passed_count} passed / {failed_count} failed",
        },
    ]


def _send_new_user_credentials_email(request, user, temporary_password: str) -> int:
    admin_login_url = request.build_absolute_uri(reverse("accounts:admin_login"))
    faculty_public_url = request.build_absolute_uri(reverse("faculty_portal:public_index"))
    logo_url = request.build_absolute_uri(f"{settings.MEDIA_URL}logos/egp_logo_official.png")
    context = {
        "user": user,
        "temporary_password": temporary_password,
        "admin_login_url": admin_login_url,
        "faculty_public_url": faculty_public_url,
        "logo_url": logo_url,
        "privacy_notice_url": "https://ncba.edu.ph/ncba-privacy-notice/",
    }
    text_body = render_to_string("admin_portal/emails/new_user_credentials.txt", context)
    html_body = render_to_string("admin_portal/emails/new_user_credentials.html", context)
    message = EmailMultiAlternatives(
        subject="EduGradesPro Account Created",
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@edugradespro.local"),
        to=[user.email],
    )
    message.attach_alternative(html_body, "text/html")
    return message.send(fail_silently=False)


@portal_required("ADMIN")
@permission_required("dashboard.read")
def dashboard_view(request):
    tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
    campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
    current_tenant_id = getattr(request, "scope", {}).get("tenant_id")
    current_campus_id = getattr(request, "scope", {}).get("campus_id")
    now = timezone.now()
    has_import_read = PermissionService.has_permission(
        request.user,
        "import_batches.read",
        tenant_id=current_tenant_id,
        campus_id=current_campus_id,
    )
    has_users_read = PermissionService.has_permission(
        request.user,
        "users.read",
        tenant_id=current_tenant_id,
        campus_id=current_campus_id,
    )
    has_system_settings_update = PermissionService.has_permission(
        request.user,
        "system_settings.update",
        tenant_id=current_tenant_id,
        campus_id=current_campus_id,
    )
    has_grading_period_lock = PermissionService.has_permission(
        request.user,
        "grading_periods.lock",
        tenant_id=current_tenant_id,
        campus_id=current_campus_id,
    )

    active_academic_year = None
    active_term = None
    if current_tenant_id:
        active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(
            tenant_id=current_tenant_id
        )

    import_stats = None
    if has_import_read:
        import_qs = AdminScopeService.scoped_import_batches(request)
        imported_rows_total = import_qs.aggregate(total=Sum("imported_rows")).get("total") or 0
        import_stats = {
            "total_batches": import_qs.count(),
            "pending_confirmations": import_qs.filter(
                status__in=[ImportBatch.Status.VALIDATED, ImportBatch.Status.CONFIRM_FAILED],
                valid_rows__gt=0,
            ).count(),
            "failed_batches": import_qs.filter(
                status__in=[ImportBatch.Status.VALIDATION_FAILED, ImportBatch.Status.CONFIRM_FAILED]
            ).count(),
            "imported_rows_total": imported_rows_total,
        }

    active_user_sessions = []
    if has_users_read:
        active_user_sessions = _active_user_activity_rows(request, limit=25)

    lock_monitor = None
    if has_grading_period_lock:
        lock_qs = AdminScopeService.scoped_grading_period_locks(request)
        next_due_locks = list(
            lock_qs.filter(
                is_active=True,
                is_locked=False,
                deadline_at__isnull=False,
                deadline_at__gte=now,
            )
            .order_by("deadline_at", "id")[:5]
        )
        recently_auto_locked = list(
            lock_qs.filter(
                is_active=True,
                is_locked=True,
                locked_at__isnull=False,
                remarks__icontains="Auto-locked by deadline",
            )
            .order_by("-locked_at", "-id")[:5]
        )
        overdue_open_locks = lock_qs.filter(
            is_active=True,
            is_locked=False,
            deadline_at__isnull=False,
            deadline_at__lt=now,
        ).count()
        lock_monitor = {
            "checked_at": now,
            "upcoming_count": len(next_due_locks),
            "recent_auto_locked_count": len(recently_auto_locked),
            "overdue_open_count": overdue_open_locks,
            "next_due_locks": next_due_locks,
            "recently_auto_locked": recently_auto_locked,
        }

    context = {
        "stats": {
            "tenants": AdminScopeService.scoped_tenants(request).count(),
            "campuses": AdminScopeService.scoped_campuses(request).count(),
            "users": _scoped_users_queryset(request).count(),
            "audit_logs": _scoped_audit_queryset(request).count(),
        },
        "has_import_read": has_import_read,
        "has_users_read": has_users_read,
        "has_system_settings_update": has_system_settings_update,
        "import_stats": import_stats,
        "active_user_sessions": active_user_sessions,
        "active_user_count": len(active_user_sessions),
        "active_academic_year": active_academic_year,
        "active_term": active_term,
        "has_grading_period_lock": has_grading_period_lock,
        "lock_monitor": lock_monitor,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/dashboard.html", context)


@portal_required("ADMIN")
@permission_required("grading_analytics.read")
def grading_analytics_view(request):
    offerings_qs = AdminScopeService.scoped_course_offerings(request)
    campus_options = AdminScopeService.scoped_campuses(request).order_by("code")
    academic_year_options = AdminScopeService.scoped_academic_years(request).order_by("-start_date")
    term_options = AdminScopeService.scoped_terms(request).order_by("-academic_year__start_date", "sequence_no")

    selected_campus_id = _safe_int(request.GET.get("campus_id"))
    selected_ay_id = _safe_int(request.GET.get("academic_year_id"))
    selected_term_id = _safe_int(request.GET.get("term_id"))

    if selected_campus_id:
        offerings_qs = offerings_qs.filter(campus_id=selected_campus_id)
    if selected_ay_id:
        offerings_qs = offerings_qs.filter(academic_year_id=selected_ay_id)
    if selected_term_id:
        offerings_qs = offerings_qs.filter(term_id=selected_term_id)

    offerings = list(
        offerings_qs.select_related(
            "tenant",
            "campus",
            "department",
            "program",
            "course",
            "section",
            "academic_year",
            "term",
        ).distinct()
    )
    offering_ids = [offering.id for offering in offerings]
    offerings_by_id = {offering.id: offering for offering in offerings}

    submission_qs = AdminScopeService.scoped_grade_submissions(request).filter(offering_id__in=offering_ids)
    correction_qs = AdminScopeService.scoped_grade_correction_requests(request).filter(offering_id__in=offering_ids)
    reopen_qs = AdminScopeService.scoped_grade_submission_reopen_requests(request).filter(offering_id__in=offering_ids)
    active_enrollment_qs = Enrollment.objects.filter(
        course_offering_id__in=offering_ids,
        is_active=True,
        enrollment_status=Enrollment.Status.ACTIVE,
    )
    period_grade_qs = StudentPeriodGrade.objects.filter(offering_id__in=offering_ids, period_grade__isnull=False)
    period_grade_rows = list(period_grade_qs.select_related("template_period"))

    def _to_decimal(value, fallback=Decimal("75.00")):
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return fallback

    def _threshold_label(min_value: Decimal | None, max_value: Decimal | None):
        if min_value is None or max_value is None:
            return "-"
        if min_value == max_value:
            return f"{min_value:.2f}"
        return f"{min_value:.2f} - {max_value:.2f}"

    offering_threshold_map = {}
    offering_threshold_source_map = {}
    tenant_threshold_cache = {}
    profile_threshold_offerings = 0
    tenant_threshold_offerings = 0
    for offering in offerings:
        profile = FacultyGradingService.resolve_grading_profile_for_offering(offering)
        profile_threshold = None
        if profile and profile.passing_grade_threshold is not None:
            profile_threshold = GradingGovernanceService._round(Decimal(profile.passing_grade_threshold))
        if profile_threshold is not None:
            offering_threshold_map[offering.id] = profile_threshold
            offering_threshold_source_map[offering.id] = f"Profile {profile.profile_code}"
            profile_threshold_offerings += 1
            continue
        if offering.tenant_id not in tenant_threshold_cache:
            tenant_raw = SystemSettingService.get(
                "PASSING_GRADE_THRESHOLD",
                tenant_id=offering.tenant_id,
                default="75",
            )
            tenant_threshold_cache[offering.tenant_id] = GradingGovernanceService._round(
                _to_decimal(tenant_raw, Decimal("75.00"))
            )
        offering_threshold_map[offering.id] = tenant_threshold_cache[offering.tenant_id]
        offering_threshold_source_map[offering.id] = "Tenant Default"
        tenant_threshold_offerings += 1

    graded_count = len(period_grade_rows)
    failed_count = 0
    total_period_grade = Decimal("0")
    for row in period_grade_rows:
        value = Decimal(row.period_grade)
        threshold = offering_threshold_map.get(row.offering_id, Decimal("75.00"))
        total_period_grade += value
        if value < threshold:
            failed_count += 1
    passed_count = max(graded_count - failed_count, 0)
    avg_period_grade = GradingGovernanceService._round(total_period_grade / Decimal(graded_count)) if graded_count else None

    def _pct(value, total):
        if not total:
            return 0
        return round((value / total) * 100, 1)

    summary = {
        "offerings": len(offering_ids),
        "active_students": active_enrollment_qs.count(),
        "submitted_periods": submission_qs.filter(status=GradeSubmission.Status.SUBMITTED).count(),
        "reopened_periods": submission_qs.filter(status=GradeSubmission.Status.REOPENED).count(),
        "pending_reopen_requests": reopen_qs.filter(status=GradeSubmissionReopenRequest.Status.PENDING).count(),
        "pending_corrections": correction_qs.filter(status=GradeCorrectionRequest.Status.PENDING).count(),
        "graded_rows": graded_count,
        "passed_rows": passed_count,
        "failed_rows": failed_count,
        "pass_rate": _pct(passed_count, graded_count),
        "fail_rate": _pct(failed_count, graded_count),
        "avg_period_grade": avg_period_grade,
        "profile_threshold_offerings": profile_threshold_offerings,
        "tenant_threshold_offerings": tenant_threshold_offerings,
        "threshold_policy": "Profile threshold -> Tenant PASSING_GRADE_THRESHOLD -> 75.00",
    }

    submission_status_rows = list(
        submission_qs.values("status").annotate(total=Count("id")).order_by("-total", "status")
    )
    max_submission_total = max([row["total"] for row in submission_status_rows], default=0)
    submission_status_palette = {
        GradeSubmission.Status.SUBMITTED: "bg-success-subtle text-success-emphasis",
        GradeSubmission.Status.REOPENED: "bg-warning-subtle text-warning-emphasis",
        GradeSubmission.Status.DRAFT: "bg-secondary-subtle text-secondary-emphasis",
    }
    for row in submission_status_rows:
        row["width_pct"] = _pct(row["total"], max_submission_total) if max_submission_total else 0
        row["badge_class"] = submission_status_palette.get(row["status"], "bg-light text-dark")

    distribution_ranges = [
        ("90+", Decimal("90"), None),
        ("85-89.99", Decimal("85"), Decimal("90")),
        ("80-84.99", Decimal("80"), Decimal("85")),
        ("75-79.99", Decimal("75"), Decimal("80")),
        ("Below 75", None, Decimal("75")),
    ]
    grade_distribution_rows = []
    max_distribution_count = 0
    for label, lower, upper in distribution_ranges:
        total = 0
        for row in period_grade_rows:
            value = Decimal(row.period_grade)
            if lower is not None and value < lower:
                continue
            if upper is not None and value >= upper:
                continue
            total += 1
        max_distribution_count = max(max_distribution_count, total)
        grade_distribution_rows.append(
            {
                "label": label,
                "count": total,
                "share_pct": _pct(total, graded_count),
            }
        )
    for row in grade_distribution_rows:
        row["width_pct"] = _pct(row["count"], max_distribution_count) if max_distribution_count else 0

    campus_offering_rows = list(
        offerings_qs.values("campus_id", "campus__code", "campus__name")
        .annotate(total_offerings=Count("id"))
        .order_by("campus__code")
    )
    campus_enrollment_map = {
        row["course_offering__campus_id"]: row["active_students"]
        for row in active_enrollment_qs.values("course_offering__campus_id").annotate(active_students=Count("id"))
    }
    campus_submission_map = {
        row["offering__campus_id"]: row
        for row in submission_qs.values("offering__campus_id").annotate(
            submitted=Count("id", filter=Q(status=GradeSubmission.Status.SUBMITTED)),
            reopened=Count("id", filter=Q(status=GradeSubmission.Status.REOPENED)),
            total=Count("id"),
        )
    }
    campus_grade_map = {}
    for row in period_grade_rows:
        offering = offerings_by_id.get(row.offering_id)
        if not offering:
            continue
        campus_id = offering.campus_id
        threshold = offering_threshold_map.get(row.offering_id, Decimal("75.00"))
        bucket = campus_grade_map.setdefault(
            campus_id,
            {
                "graded": 0,
                "failed": 0,
                "grade_sum": Decimal("0"),
            },
        )
        bucket["graded"] += 1
        bucket["grade_sum"] += Decimal(row.period_grade)
        if Decimal(row.period_grade) < threshold:
            bucket["failed"] += 1
    campus_rows = []
    for row in campus_offering_rows:
        campus_id = row["campus_id"]
        submission_row = campus_submission_map.get(campus_id, {})
        grade_row = campus_grade_map.get(campus_id, {})
        graded_rows = grade_row.get("graded", 0) or 0
        failed_rows = grade_row.get("failed", 0) or 0
        grade_sum = grade_row.get("grade_sum", Decimal("0"))
        avg_grade = GradingGovernanceService._round(grade_sum / Decimal(graded_rows)) if graded_rows else None
        campus_rows.append(
            {
                "campus_code": row["campus__code"],
                "campus_name": row["campus__name"],
                "offerings": row["total_offerings"],
                "active_students": campus_enrollment_map.get(campus_id, 0),
                "submitted": submission_row.get("submitted", 0),
                "reopened": submission_row.get("reopened", 0),
                "avg_grade": avg_grade,
                "pass_rate": _pct(max(graded_rows - failed_rows, 0), graded_rows),
            }
        )

    top_failing_map = {}
    for row in period_grade_rows:
        threshold = offering_threshold_map.get(row.offering_id, Decimal("75.00"))
        grade = Decimal(row.period_grade)
        if grade >= threshold:
            continue
        bucket = top_failing_map.setdefault(
            row.offering_id,
            {
                "failed_students": 0,
                "graded": 0,
                "grade_sum": Decimal("0"),
            },
        )
        bucket["failed_students"] += 1
    for row in period_grade_rows:
        bucket = top_failing_map.get(row.offering_id)
        if not bucket:
            continue
        bucket["graded"] += 1
        bucket["grade_sum"] += Decimal(row.period_grade)

    top_failing_offerings = []
    for offering_id, bucket in top_failing_map.items():
        offering = offerings_by_id.get(offering_id)
        if not offering:
            continue
        avg_grade = (
            GradingGovernanceService._round(bucket["grade_sum"] / Decimal(bucket["graded"]))
            if bucket["graded"]
            else None
        )
        top_failing_offerings.append(
            {
                "offering_id": offering.id,
                "offering__course__code": offering.course.code,
                "offering__course__title": offering.course.title,
                "offering__section__code": offering.section.code,
                "offering__term__code": offering.term.code,
                "offering__academic_year__code": offering.academic_year.code,
                "failed_students": bucket["failed_students"],
                "avg_grade": avg_grade,
                "threshold": offering_threshold_map.get(offering.id, Decimal("75.00")),
                "threshold_source": offering_threshold_source_map.get(offering.id, "Default"),
            }
        )
    top_failing_offerings.sort(
        key=lambda row: (-row["failed_students"], row["offering__course__code"], row["offering__section__code"])
    )
    top_failing_offerings = top_failing_offerings[:10]

    period_fail_map = {}
    for row in period_grade_rows:
        threshold = offering_threshold_map.get(row.offering_id, Decimal("75.00"))
        bucket = period_fail_map.setdefault(
            row.template_period_id,
            {
                "period_code": row.template_period.code,
                "period_name": row.template_period.name,
                "period_sequence": row.template_period.sequence_no,
                "graded": 0,
                "failed": 0,
                "threshold_min": None,
                "threshold_max": None,
            },
        )
        bucket["graded"] += 1
        if Decimal(row.period_grade) < threshold:
            bucket["failed"] += 1
        if bucket["threshold_min"] is None or threshold < bucket["threshold_min"]:
            bucket["threshold_min"] = threshold
        if bucket["threshold_max"] is None or threshold > bucket["threshold_max"]:
            bucket["threshold_max"] = threshold

    period_fail_rows = []
    for bucket in period_fail_map.values():
        graded = bucket["graded"]
        failed = bucket["failed"]
        period_fail_rows.append(
            {
                **bucket,
                "passed": max(graded - failed, 0),
                "fail_rate": _pct(failed, graded),
                "threshold_display": _threshold_label(bucket["threshold_min"], bucket["threshold_max"]),
            }
        )
    period_fail_rows.sort(key=lambda row: (row["period_sequence"], row["period_code"]))

    period_ids = {row.template_period_id for row in period_grade_rows}
    period_objs = list(
        GradingTemplatePeriod.objects.filter(id__in=period_ids).prefetch_related("components__subcomponents__details")
    )
    component_map = {}
    subcomponent_map = {}
    detail_map = {}
    for period in period_objs:
        active_components = [component for component in period.components.all() if component.is_active]
        active_components.sort(key=lambda component: (component.sort_order, component.id))
        component_map[period.id] = active_components
        for component in active_components:
            active_subcomponents = [sub for sub in component.subcomponents.all() if sub.is_active]
            active_subcomponents.sort(key=lambda sub: (sub.sort_order, sub.id))
            subcomponent_map[component.id] = active_subcomponents
            for sub in active_subcomponents:
                active_details = [detail for detail in sub.details.all() if detail.is_active]
                active_details.sort(key=lambda detail: (detail.sort_order, detail.id))
                detail_map[sub.id] = active_details

    score_lookup = {}
    if period_ids and offering_ids:
        score_rows = StudentActivityScore.objects.filter(
            activity__offering_id__in=offering_ids,
            activity__template_period_id__in=period_ids,
            activity__is_active=True,
            is_active=True,
        ).select_related("activity")
        for score in score_rows:
            activity = score.activity
            key = (
                activity.offering_id,
                activity.template_period_id,
                score.student_id,
                activity.template_component_id,
                activity.template_subcomponent_id,
                activity.template_detail_id,
            )
            score_lookup.setdefault(key, []).append(Decimal(score.computed_score or 0))

    def _score_avg(key):
        values = score_lookup.get(key, [])
        if not values:
            return None
        return GradingGovernanceService._round(sum(values) / Decimal(len(values)))

    student_period_keys = {(row.offering_id, row.template_period_id, row.student_id) for row in period_grade_rows}
    component_fail_map = {}
    detail_fail_map = {}
    period_meta = {period.id: period for period in period_objs}

    for offering_id, period_id, student_id in student_period_keys:
        threshold = offering_threshold_map.get(offering_id, Decimal("75.00"))
        period_obj = period_meta.get(period_id)
        components = component_map.get(period_id, [])
        for component in components:
            component_has_data = False
            component_value = None
            subcomponents = subcomponent_map.get(component.id, [])
            if subcomponents:
                sub_weight_total = sum(Decimal(sub.weight_percentage or 0) for sub in subcomponents)
                sub_denominator = sub_weight_total if sub_weight_total > 0 else Decimal("100")
                component_raw = Decimal("0")
                for sub in subcomponents:
                    details = detail_map.get(sub.id, [])
                    if details:
                        detail_weight_total = sum(Decimal(detail.weight_percentage or 0) for detail in details)
                        detail_denominator = detail_weight_total if detail_weight_total > 0 else Decimal("100")
                        sub_raw = Decimal("0")
                        sub_has_data = False
                        for detail in details:
                            detail_value = _score_avg(
                                (offering_id, period_id, student_id, component.id, sub.id, detail.id)
                            )
                            if detail_value is not None:
                                sub_has_data = True
                                detail_bucket = detail_fail_map.setdefault(
                                    (period_id, component.id, sub.id, detail.id),
                                    {
                                        "period_code": period_obj.code if period_obj else "",
                                        "period_sequence": period_obj.sequence_no if period_obj else 999,
                                        "component_name": component.name,
                                        "component_sort": component.sort_order,
                                        "subcomponent_name": sub.name,
                                        "subcomponent_sort": sub.sort_order,
                                        "detail_name": detail.name,
                                        "detail_sort": detail.sort_order,
                                        "graded": 0,
                                        "failed": 0,
                                        "threshold_min": None,
                                        "threshold_max": None,
                                    },
                                )
                                detail_bucket["graded"] += 1
                                if detail_value < threshold:
                                    detail_bucket["failed"] += 1
                                if (
                                    detail_bucket["threshold_min"] is None
                                    or threshold < detail_bucket["threshold_min"]
                                ):
                                    detail_bucket["threshold_min"] = threshold
                                if (
                                    detail_bucket["threshold_max"] is None
                                    or threshold > detail_bucket["threshold_max"]
                                ):
                                    detail_bucket["threshold_max"] = threshold
                            sub_raw += (Decimal(detail.weight_percentage or 0) / detail_denominator) * (
                                detail_value or Decimal("0")
                            )
                        sub_value = GradingGovernanceService._round(sub_raw) if sub_has_data else None
                    else:
                        sub_value = _score_avg((offering_id, period_id, student_id, component.id, sub.id, None))

                    if sub_value is not None:
                        component_has_data = True
                    component_raw += (Decimal(sub.weight_percentage or 0) / sub_denominator) * (
                        sub_value or Decimal("0")
                    )
                component_value = GradingGovernanceService._round(component_raw) if component_has_data else None
            else:
                component_value = _score_avg((offering_id, period_id, student_id, component.id, None, None))
                component_has_data = component_value is not None

            if component_has_data and component_value is not None:
                component_bucket = component_fail_map.setdefault(
                    (period_id, component.id),
                    {
                        "period_code": period_obj.code if period_obj else "",
                        "period_sequence": period_obj.sequence_no if period_obj else 999,
                        "component_code": component.code,
                        "component_name": component.name,
                        "component_sort": component.sort_order,
                        "graded": 0,
                        "failed": 0,
                        "threshold_min": None,
                        "threshold_max": None,
                    },
                )
                component_bucket["graded"] += 1
                if component_value < threshold:
                    component_bucket["failed"] += 1
                if component_bucket["threshold_min"] is None or threshold < component_bucket["threshold_min"]:
                    component_bucket["threshold_min"] = threshold
                if component_bucket["threshold_max"] is None or threshold > component_bucket["threshold_max"]:
                    component_bucket["threshold_max"] = threshold

    component_fail_rows = []
    for bucket in component_fail_map.values():
        graded = bucket["graded"]
        failed = bucket["failed"]
        component_fail_rows.append(
            {
                **bucket,
                "passed": max(graded - failed, 0),
                "fail_rate": _pct(failed, graded),
                "threshold_display": _threshold_label(bucket["threshold_min"], bucket["threshold_max"]),
            }
        )
    component_fail_rows.sort(key=lambda row: (row["period_sequence"], row["component_sort"], row["component_code"]))

    detail_fail_rows = []
    for bucket in detail_fail_map.values():
        graded = bucket["graded"]
        failed = bucket["failed"]
        detail_fail_rows.append(
            {
                **bucket,
                "passed": max(graded - failed, 0),
                "fail_rate": _pct(failed, graded),
                "threshold_display": _threshold_label(bucket["threshold_min"], bucket["threshold_max"]),
            }
        )
    detail_fail_rows.sort(
        key=lambda row: (
            row["period_sequence"],
            row["component_sort"],
            row["subcomponent_sort"],
            row["detail_sort"],
            row["detail_name"],
        )
    )

    assignment_rows = (
        FacultyAssignment.objects.filter(
            offering_id__in=offering_ids,
            is_active=True,
            faculty_user__is_active=True,
        )
        .select_related("faculty_user")
        .order_by("offering_id", "-is_primary", "id")
    )
    offering_faculty_map = {}
    for assignment in assignment_rows:
        if assignment.offering_id not in offering_faculty_map:
            offering_faculty_map[assignment.offering_id] = assignment.faculty_user

    class_fail_map = {}
    for row in period_grade_rows:
        offering = offerings_by_id.get(row.offering_id)
        if not offering:
            continue
        threshold = offering_threshold_map.get(row.offering_id, Decimal("75.00"))
        faculty_user = offering_faculty_map.get(row.offering_id)
        faculty_id = faculty_user.id if faculty_user else 0
        faculty_name = faculty_user.full_name if faculty_user else "Unassigned Faculty"
        class_key = (offering.campus_id, offering.id, faculty_id)
        bucket = class_fail_map.setdefault(
            class_key,
            {
                "campus_code": offering.campus.code,
                "campus_name": offering.campus.name,
                "course_code": offering.course.code,
                "course_title": offering.course.title,
                "section_code": offering.section.code,
                "academic_year_code": offering.academic_year.code,
                "term_code": offering.term.code,
                "faculty_name": faculty_name,
                "graded": 0,
                "failed": 0,
                "threshold_min": None,
                "threshold_max": None,
            },
        )
        bucket["graded"] += 1
        if Decimal(row.period_grade) < threshold:
            bucket["failed"] += 1
        if bucket["threshold_min"] is None or threshold < bucket["threshold_min"]:
            bucket["threshold_min"] = threshold
        if bucket["threshold_max"] is None or threshold > bucket["threshold_max"]:
            bucket["threshold_max"] = threshold

    faculty_class_fail_rows = []
    for bucket in class_fail_map.values():
        graded = bucket["graded"]
        if graded <= 0:
            continue
        failed = bucket["failed"]
        passed = max(graded - failed, 0)
        fail_rate = _pct(failed, graded)
        bucket["passed"] = passed
        bucket["fail_rate"] = fail_rate
        bucket["pass_fail_ratio"] = f"{passed}:{failed}"
        bucket["threshold_display"] = _threshold_label(bucket["threshold_min"], bucket["threshold_max"])
        faculty_class_fail_rows.append(bucket)
    faculty_class_fail_rows.sort(
        key=lambda row: (-row["fail_rate"], -row["failed"], -row["graded"], row["course_code"], row["section_code"])
    )
    faculty_class_fail_rows = faculty_class_fail_rows[:10]

    course_faculty_map = {}
    for bucket in class_fail_map.values():
        course_key = (bucket["campus_code"], bucket["course_code"])
        faculty_name = bucket["faculty_name"]
        agg = course_faculty_map.setdefault(course_key, {})
        teacher_bucket = agg.setdefault(
            faculty_name,
            {
                "campus_code": bucket["campus_code"],
                "campus_name": bucket["campus_name"],
                "course_code": bucket["course_code"],
                "course_title": bucket["course_title"],
                "faculty_name": faculty_name,
                "graded": 0,
                "failed": 0,
            },
        )
        teacher_bucket["graded"] += bucket["graded"]
        teacher_bucket["failed"] += bucket["failed"]

    faculty_course_compare_rows = []
    for teacher_map in course_faculty_map.values():
        compared_count = len(teacher_map)
        if compared_count <= 1:
            continue
        for teacher_bucket in teacher_map.values():
            graded = teacher_bucket["graded"]
            failed = teacher_bucket["failed"]
            passed = max(graded - failed, 0)
            teacher_bucket["passed"] = passed
            teacher_bucket["fail_rate"] = _pct(failed, graded)
            teacher_bucket["pass_fail_ratio"] = f"{passed}:{failed}"
            teacher_bucket["compared_faculty_count"] = compared_count
            faculty_course_compare_rows.append(teacher_bucket)
    faculty_course_compare_rows.sort(
        key=lambda row: (
            -row["fail_rate"],
            -row["failed"],
            -row["graded"],
            row["campus_code"],
            row["course_code"],
            row["faculty_name"],
        )
    )
    faculty_course_compare_rows = faculty_course_compare_rows[:10]

    context = {
        "summary": summary,
        "submission_status_rows": submission_status_rows,
        "grade_distribution_rows": grade_distribution_rows,
        "period_fail_rows": period_fail_rows,
        "component_fail_rows": component_fail_rows,
        "detail_fail_rows": detail_fail_rows,
        "faculty_class_fail_rows": faculty_class_fail_rows,
        "faculty_course_compare_rows": faculty_course_compare_rows,
        "campus_rows": campus_rows,
        "top_failing_offerings": top_failing_offerings,
        "campus_options": campus_options,
        "academic_year_options": academic_year_options,
        "term_options": term_options,
        "selected_campus_id": selected_campus_id,
        "selected_ay_id": selected_ay_id,
        "selected_term_id": selected_term_id,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/analytics.html", context)


@portal_required("ADMIN")
def admin_guide_view(request):
    context = {
        "title": "Admin Portal User Guide",
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/guide.html", context)


@portal_required("ADMIN")
@permission_required("system_settings.update")
def active_academic_term_settings_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

    ay_queryset = AdminScopeService.scoped_academic_years(request).filter(tenant_id=tenant_id).order_by("-start_date")
    term_queryset = (
        AdminScopeService.scoped_terms(request)
        .filter(tenant_id=tenant_id)
        .select_related("academic_year")
        .order_by("-academic_year__start_date", "sequence_no")
    )
    active_ay, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)

    form = ActiveAcademicTermSettingForm(
        request.POST or None,
        academic_year_queryset=ay_queryset,
        term_queryset=term_queryset,
        initial={
            "active_academic_year": active_ay.id if active_ay else None,
            "active_term": active_term.id if active_term else None,
        },
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        before = {
            "active_academic_year_code": active_ay.code if active_ay else None,
            "active_term_code": active_term.code if active_term else None,
        }
        selected_ay = form.cleaned_data.get("active_academic_year")
        selected_term = form.cleaned_data.get("active_term")
        AcademicGovernanceService.set_active_scope(
            tenant_id=tenant_id,
            academic_year=selected_ay,
            term=selected_term,
        )
        after = {
            "active_academic_year_code": selected_ay.code if selected_ay else None,
            "active_term_code": selected_term.code if selected_term else None,
        }
        AuditService.log_event(
            action="UPDATE_SYSTEM_SETTING",
            portal="ADMIN",
            entity_type="SystemSetting",
            entity_id=f"tenant:{tenant_id}:active-academic-scope",
            actor=request.user,
            tenant=tenant_id,
            campus=getattr(request, "scope", {}).get("campus_id"),
            before_data=before,
            after_data=after,
            metadata={
                "setting_keys": [
                    AcademicGovernanceService.ACTIVE_AY_KEY,
                    AcademicGovernanceService.ACTIVE_TERM_KEY,
                ],
            },
            request=request,
        )
        if selected_ay and selected_term:
            messages.success(
                request,
                f"Active academic scope set to {selected_ay.code} / {selected_term.code}.",
            )
        else:
            messages.success(request, "Active academic scope cleared. Faculty view will not auto-archive by term.")
        return _redirect_back_or_default(request, "admin_portal:active_academic_term_settings")

    context = {
        "form": form,
        "title": "Active Academic Scope",
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("grading_governance_settings.update")
def correction_governance_settings_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

    current_mode = GradingGovernanceService.get_predeadline_correction_mode(tenant_id=tenant_id)
    current_correction_mode = GradingGovernanceService.get_correction_mode(tenant_id=tenant_id)
    tenant_obj = Tenant.objects.filter(id=tenant_id).first()
    department_qs = AdminScopeService.scoped_departments(request).filter(tenant_id=tenant_id)
    role_qs = Role.objects.filter(is_active=True).order_by("name")

    mode_form = CorrectionGovernanceSettingForm(
        initial={
            "correction_mode": current_correction_mode,
            "predeadline_correction_mode": current_mode,
        },
        prefix="mode",
    )
    edit_route_id = request.GET.get("edit_route")
    edit_route = None
    if edit_route_id:
        edit_route = CorrectionApprovalRouteRule.objects.filter(id=edit_route_id, tenant_id=tenant_id).first()

    route_form = CorrectionApprovalRouteRuleForm(
        instance=edit_route,
        tenant=tenant_obj,
        department_queryset=department_qs,
        role_queryset=role_qs,
        prefix="route",
    )
    _style_form(mode_form)
    _style_form(route_form)

    if request.method == "POST":
        action = request.POST.get("form_action")
        if action == "save_mode":
            mode_form = CorrectionGovernanceSettingForm(
                request.POST,
                prefix="mode",
            )
            _style_form(mode_form)
            if mode_form.is_valid():
                selected_correction_mode = mode_form.cleaned_data["correction_mode"]
                selected_mode = mode_form.cleaned_data["predeadline_correction_mode"]
                SystemSettingService.set(
                    GradingGovernanceService.CORRECTION_MODE_KEY,
                    selected_correction_mode,
                    tenant_id=tenant_id,
                    value_type="STRING",
                    is_active=True,
                    description="Controls whether correction handling is manual-only or in-system request workflow.",
                )
                SystemSettingService.set(
                    GradingGovernanceService.PREDEADLINE_CORRECTION_MODE_KEY,
                    selected_mode,
                    tenant_id=tenant_id,
                    value_type="STRING",
                    is_active=True,
                    description="Controls how submitted period corrections are handled before the deadline.",
                )
                AuditService.log_event(
                    action="UPDATE_SYSTEM_SETTING",
                    portal="ADMIN",
                    entity_type="SystemSetting",
                    entity_id=f"tenant:{tenant_id}:predeadline-correction-mode",
                    actor=request.user,
                    tenant=tenant_id,
                    campus=getattr(request, "scope", {}).get("campus_id"),
                    before_data={
                        "correction_mode": current_correction_mode,
                        "predeadline_correction_mode": current_mode,
                    },
                    after_data={
                        "correction_mode": selected_correction_mode,
                        "predeadline_correction_mode": selected_mode,
                    },
                    metadata={
                        "setting_keys": [
                            GradingGovernanceService.CORRECTION_MODE_KEY,
                            GradingGovernanceService.PREDEADLINE_CORRECTION_MODE_KEY,
                        ],
                    },
                    request=request,
                )
                messages.success(request, "Correction governance setting updated.")
                return _redirect_back_or_default(request, "admin_portal:correction_governance_settings")
        elif action == "save_route":
            route_id = request.POST.get("route_id")
            route_instance = None
            if route_id:
                route_instance = get_object_or_404(CorrectionApprovalRouteRule, id=route_id, tenant_id=tenant_id)
            route_form = CorrectionApprovalRouteRuleForm(
                request.POST,
                instance=route_instance,
                tenant=tenant_obj,
                department_queryset=department_qs,
                role_queryset=role_qs,
                prefix="route",
            )
            _style_form(route_form)
            if route_form.is_valid():
                before = model_before_after(route_instance) if route_instance else None
                route_row = route_form.save(commit=False)
                route_row.tenant_id = tenant_id
                route_row.save()
                action_name = "UPDATE" if route_instance else "CREATE"
                AuditService.log_event(
                    action=action_name,
                    portal="ADMIN",
                    entity_type="CorrectionApprovalRouteRule",
                    entity_id=route_row.id,
                    actor=request.user,
                    tenant=tenant_id,
                    campus=getattr(request, "scope", {}).get("campus_id"),
                    before_data=before,
                    after_data=model_before_after(route_row),
                    request=request,
                )
                messages.success(request, "Correction approval route saved.")
                return _redirect_back_or_default(request, "admin_portal:correction_governance_settings")
        elif action == "delete_route":
            route_id = request.POST.get("route_id")
            route_row = get_object_or_404(CorrectionApprovalRouteRule, id=route_id, tenant_id=tenant_id)
            before = model_before_after(route_row)
            route_row.delete()
            AuditService.log_event(
                action="DELETE",
                portal="ADMIN",
                entity_type="CorrectionApprovalRouteRule",
                entity_id=route_id,
                actor=request.user,
                tenant=tenant_id,
                campus=getattr(request, "scope", {}).get("campus_id"),
                before_data=before,
                request=request,
            )
            messages.success(request, "Correction approval route removed.")
            return _redirect_back_or_default(request, "admin_portal:correction_governance_settings")

    routes = (
        CorrectionApprovalRouteRule.objects.filter(tenant_id=tenant_id)
        .select_related("faculty_department", "step1_role", "final_role")
        .order_by("faculty_department__name", "id")
    )

    context = {
        "mode_form": mode_form,
        "route_form": route_form,
        "routes": routes,
        "edit_route": edit_route,
        "title": "Correction Governance",
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/tools/correction_governance.html", context)


@portal_required("ADMIN")
@permission_required("system_settings.update")
def document_print_settings_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

    current_school_name = SystemSettingService.get(
        "PRINT_HEADER_SCHOOL_NAME",
        tenant_id=tenant_id,
        default=Tenant.objects.filter(id=tenant_id).values_list("name", flat=True).first() or "",
    )
    current_school_address = SystemSettingService.get(
        "PRINT_HEADER_SCHOOL_ADDRESS",
        tenant_id=tenant_id,
        default="",
    )

    form = DocumentPrintSettingForm(
        request.POST or None,
        initial={
            "school_name": current_school_name,
            "school_address": current_school_address,
        },
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        selected_school_name = form.cleaned_data["school_name"].strip()
        selected_school_address = form.cleaned_data["school_address"].strip()
        SystemSettingService.set(
            "PRINT_HEADER_SCHOOL_NAME",
            selected_school_name,
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            "PRINT_HEADER_SCHOOL_ADDRESS",
            selected_school_address,
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        AuditService.log_event(
            action="UPDATE_SYSTEM_SETTING",
            portal="ADMIN",
            entity_type="SystemSetting",
            entity_id=f"tenant:{tenant_id}:document-print-header",
            actor=request.user,
            tenant=tenant_id,
            campus=getattr(request, "scope", {}).get("campus_id"),
            before_data={
                "school_name": current_school_name,
                "school_address": current_school_address,
            },
            after_data={
                "school_name": selected_school_name,
                "school_address": selected_school_address,
            },
            metadata={
                "setting_keys": [
                    "PRINT_HEADER_SCHOOL_NAME",
                    "PRINT_HEADER_SCHOOL_ADDRESS",
                ],
            },
            request=request,
        )
        messages.success(request, "Document print header settings updated.")
        return _redirect_back_or_default(request, "admin_portal:document_print_settings")

    context = {
        "form": form,
        "title": "Document Print Header",
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("system_settings.update")
def configurable_features_settings_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

    campus_queryset = AdminScopeService.scoped_campuses(request).filter(tenant_id=tenant_id).order_by("code", "name")
    role_queryset = Role.objects.filter(is_active=True).order_by("name")

    current_report_enabled = FeatureSettingsService.is_correction_official_report_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_submission_email_enabled = FeatureSettingsService.is_correction_submission_approval_email_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_submission_email_role_codes = FeatureSettingsService.get_correction_submission_approval_email_role_codes(
        tenant_id=tenant_id
    )
    current_auto_email_enabled = FeatureSettingsService.is_correction_registrar_auto_email_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_role_codes = FeatureSettingsService.get_correction_registrar_auto_email_role_codes(tenant_id=tenant_id)
    current_default_recipients = FeatureSettingsService.get_correction_registrar_default_recipients(tenant_id=tenant_id)
    current_campus_recipients = FeatureSettingsService.get_correction_registrar_campus_recipients(tenant_id=tenant_id)

    form = ConfigurableFeatureSettingForm(
        request.POST or None,
        initial={
            "correction_official_report_enabled": current_report_enabled,
            "correction_submission_approval_email_enabled": current_submission_email_enabled,
            "correction_submission_approval_email_roles": role_queryset.filter(code__in=current_submission_email_role_codes),
            "correction_registrar_auto_email_enabled": current_auto_email_enabled,
            "correction_registrar_auto_email_roles": role_queryset.filter(code__in=current_role_codes),
            "correction_registrar_default_recipients": ", ".join(current_default_recipients),
        },
        role_queryset=role_queryset,
        campus_queryset=campus_queryset,
        campus_initial_map=current_campus_recipients,
    )
    _style_form(form)

    if request.method == "POST" and form.is_valid():
        selected_submission_email_role_codes = list(
            form.cleaned_data["correction_submission_approval_email_roles"].values_list("code", flat=True)
        )
        selected_role_codes = list(
            form.cleaned_data["correction_registrar_auto_email_roles"].values_list("code", flat=True)
        )
        selected_default_recipients = form.cleaned_data["correction_registrar_default_recipient_list"]
        selected_campus_recipients = form.cleaned_data["correction_registrar_campus_recipient_map"]

        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_OFFICIAL_REPORT_ENABLED_KEY,
            bool(form.cleaned_data["correction_official_report_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            bool(form.cleaned_data["correction_submission_approval_email_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES_KEY,
            selected_submission_email_role_codes,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED_KEY,
            bool(form.cleaned_data["correction_registrar_auto_email_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES_KEY,
            selected_role_codes,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_REGISTRAR_DEFAULT_RECIPIENTS_KEY,
            selected_default_recipients,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS_KEY,
            selected_campus_recipients,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )

        AuditService.log_event(
            action="UPDATE_SYSTEM_SETTING",
            portal="ADMIN",
            entity_type="SystemSetting",
            entity_id=f"tenant:{tenant_id}:configurable-features",
            actor=request.user,
            tenant=tenant_id,
            campus=getattr(request, "scope", {}).get("campus_id"),
            before_data={
                "correction_official_report_enabled": current_report_enabled,
                "correction_submission_approval_email_enabled": current_submission_email_enabled,
                "correction_submission_approval_email_role_codes": current_submission_email_role_codes,
                "correction_registrar_auto_email_enabled": current_auto_email_enabled,
                "correction_registrar_auto_email_role_codes": current_role_codes,
                "correction_registrar_default_recipients": current_default_recipients,
                "correction_registrar_campus_recipients": current_campus_recipients,
            },
            after_data={
                "correction_official_report_enabled": bool(form.cleaned_data["correction_official_report_enabled"]),
                "correction_submission_approval_email_enabled": bool(
                    form.cleaned_data["correction_submission_approval_email_enabled"]
                ),
                "correction_submission_approval_email_role_codes": selected_submission_email_role_codes,
                "correction_registrar_auto_email_enabled": bool(form.cleaned_data["correction_registrar_auto_email_enabled"]),
                "correction_registrar_auto_email_role_codes": selected_role_codes,
                "correction_registrar_default_recipients": selected_default_recipients,
                "correction_registrar_campus_recipients": selected_campus_recipients,
            },
            metadata={
                "setting_keys": [
                    FeatureSettingsService.CORRECTION_OFFICIAL_REPORT_ENABLED_KEY,
                    FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
                    FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES_KEY,
                    FeatureSettingsService.CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED_KEY,
                    FeatureSettingsService.CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES_KEY,
                    FeatureSettingsService.CORRECTION_REGISTRAR_DEFAULT_RECIPIENTS_KEY,
                    FeatureSettingsService.CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS_KEY,
                ],
            },
            request=request,
        )
        messages.success(request, "Configurable features updated.")
        return _redirect_back_or_default(request, "admin_portal:configurable_features_settings")

    context = {
        "title": "Configurable Features",
        "form": form,
        "campus_count": campus_queryset.count(),
        "campus_field_rows": [{"campus": campus, "field": form[field_name]} for field_name, campus in form.campus_fields],
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/tools/configurable_features.html", context)


def _scoped_users_queryset(request):
    qs = User.objects.all().order_by("username")
    if request.user.is_superuser:
        return qs
    tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
    campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
    return (
        qs.filter(
            Q(default_tenant_id__in=tenant_ids)
            | Q(default_campus_id__in=campus_ids)
            | Q(user_roles__tenant_id__in=tenant_ids)
            | Q(user_roles__campus_id__in=campus_ids)
            | Q(user_roles__tenant__isnull=True)
        )
        .filter(is_active=True)
        .distinct()
        .order_by("username")
    )


def _scoped_audit_queryset(request):
    qs = AuditLog.objects.select_related("actor_user", "tenant", "campus").all().order_by("-created_at")
    if request.user.is_superuser:
        return qs
    tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
    campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
    return qs.filter(
        (Q(tenant_id__in=tenant_ids) | Q(tenant__isnull=True))
        & (Q(campus_id__in=campus_ids) | Q(campus__isnull=True))
    )


def _active_user_activity_rows(request, limit=25):
    now = timezone.now()
    active_sessions = Session.objects.filter(expire_date__gte=now).order_by("-expire_date")[:500]

    active_user_ids = []
    session_map = {}
    for sess in active_sessions:
        try:
            decoded = sess.get_decoded()
        except Exception:
            continue
        raw_user_id = decoded.get("_auth_user_id")
        user_id = _safe_int(raw_user_id)
        if not user_id or user_id in session_map:
            continue
        session_map[user_id] = sess
        active_user_ids.append(user_id)

    if not active_user_ids:
        return []

    visible_users_qs = _scoped_users_queryset(request).filter(id__in=active_user_ids).select_related(
        "default_tenant", "default_campus"
    )
    visible_users = {u.id: u for u in visible_users_qs}
    if not visible_users:
        return []

    audit_map = {}
    for log in _scoped_audit_queryset(request).filter(actor_user_id__in=visible_users.keys()).order_by("-created_at"):
        if log.actor_user_id not in audit_map:
            audit_map[log.actor_user_id] = log
        if len(audit_map) == len(visible_users):
            break

    rows = []
    for user_id in active_user_ids:
        user = visible_users.get(user_id)
        if not user:
            continue
        session_obj = session_map.get(user_id)
        last_log = audit_map.get(user_id)
        activity_label = "No recent activity"
        if last_log:
            activity_label = last_log.route_name or f"{last_log.action} {last_log.entity_type}".strip()
        rows.append(
            {
                "user": user,
                "session_expires_at": session_obj.expire_date if session_obj else None,
                "last_activity_at": last_log.created_at if last_log else None,
                "last_activity_label": activity_label,
                "last_activity_action": last_log.action if last_log else None,
                "last_activity_entity": last_log.entity_type if last_log else None,
            }
        )

    rows.sort(
        key=lambda item: (
            item["last_activity_at"] is not None,
            item["last_activity_at"] or now,
        ),
        reverse=True,
    )
    return rows[:limit]


def _assignment_covers_default_scope(
    assignment,
    *,
    default_tenant_id: int | None,
    default_campus_id: int | None,
    default_department_id: int | None,
):
    if default_tenant_id and assignment.tenant_id not in (None, default_tenant_id):
        return False
    if default_campus_id and assignment.campus_id not in (None, default_campus_id):
        return False
    if default_department_id and assignment.department_id not in (None, default_department_id):
        return False
    return True


def _has_active_role_covering_default_scope(user, *, exclude_assignment_id: int | None = None):
    default_tenant_id = getattr(user, "default_tenant_id", None)
    default_campus_id = getattr(user, "default_campus_id", None)
    default_department_id = getattr(user, "default_department_id", None)
    if default_tenant_id is None and default_campus_id:
        default_tenant_id = (
            Campus.objects.filter(id=default_campus_id).values_list("tenant_id", flat=True).first()
        )

    assignments = UserRole.objects.filter(user=user, is_active=True, role__is_active=True)
    if exclude_assignment_id:
        assignments = assignments.exclude(id=exclude_assignment_id)

    has_any = False
    for assignment in assignments.only("tenant_id", "campus_id", "department_id"):
        has_any = True
        if _assignment_covers_default_scope(
            assignment,
            default_tenant_id=default_tenant_id,
            default_campus_id=default_campus_id,
            default_department_id=default_department_id,
        ):
            return True
    return not has_any


@portal_required("ADMIN")
@permission_required("tenants.read")
def tenant_list_view(request):
    queryset = AdminScopeService.scoped_tenants(request)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    return render(request, "admin_portal/organization/tenant_list.html", context)


@portal_required("ADMIN")
@permission_required("tenants.create")
def tenant_create_view(request):
    form = TenantForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        tenant = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Tenant",
            entity_id=tenant.id,
            actor=request.user,
            after_data=model_before_after(tenant),
            request=request,
        )
        messages.success(request, "Tenant created.")
        return _redirect_back_or_default(request, "admin_portal:tenant_list")
    context = {"form": form, "title": "Create Tenant"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("tenants.update")
def tenant_update_view(request, tenant_id: int):
    tenant = get_object_or_404(AdminScopeService.scoped_tenants(request), id=tenant_id)
    before = model_before_after(tenant)
    form = TenantForm(request.POST or None, instance=tenant)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        tenant = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Tenant",
            entity_id=tenant.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(tenant),
            request=request,
        )
        messages.success(request, "Tenant updated.")
        return _redirect_back_or_default(request, "admin_portal:tenant_list")
    context = {"form": form, "title": f"Edit Tenant: {tenant.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("campuses.read")
def campus_list_view(request):
    queryset = AdminScopeService.scoped_campuses(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    return render(request, "admin_portal/organization/campus_list.html", context)


@portal_required("ADMIN")
@permission_required("campuses.create")
def campus_create_view(request):
    form = CampusForm(request.POST or None, tenant_queryset=AdminScopeService.scoped_tenants(request))
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        campus = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Campus",
            entity_id=campus.id,
            actor=request.user,
            after_data=model_before_after(campus),
            request=request,
        )
        messages.success(request, "Campus created.")
        return _redirect_back_or_default(request, "admin_portal:campus_list")
    context = {"form": form, "title": "Create Campus"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("campuses.update")
def campus_update_view(request, campus_id: int):
    campus = get_object_or_404(AdminScopeService.scoped_campuses(request), id=campus_id)
    before = model_before_after(campus)
    form = CampusForm(
        request.POST or None,
        instance=campus,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        campus = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Campus",
            entity_id=campus.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(campus),
            request=request,
        )
        messages.success(request, "Campus updated.")
        return _redirect_back_or_default(request, "admin_portal:campus_list")
    context = {"form": form, "title": f"Edit Campus: {campus.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("departments.read")
def department_list_view(request):
    queryset = AdminScopeService.scoped_departments(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("campus_id"):
        queryset = queryset.filter(campus_id=request.GET.get("campus_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    return render(request, "admin_portal/organization/department_list.html", context)


@portal_required("ADMIN")
@permission_required("departments.create")
def department_create_view(request):
    form = DepartmentForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        department = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Department",
            entity_id=department.id,
            actor=request.user,
            after_data=model_before_after(department),
            request=request,
        )
        messages.success(request, "Department created.")
        return _redirect_back_or_default(request, "admin_portal:department_list")
    context = {"form": form, "title": "Create Department"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("departments.update")
def department_update_view(request, department_id: int):
    department = get_object_or_404(AdminScopeService.scoped_departments(request), id=department_id)
    before = model_before_after(department)
    form = DepartmentForm(
        request.POST or None,
        instance=department,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        department = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Department",
            entity_id=department.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(department),
            request=request,
        )
        messages.success(request, "Department updated.")
        return _redirect_back_or_default(request, "admin_portal:department_list")
    context = {"form": form, "title": f"Edit Department: {department.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("programs.read")
def program_list_view(request):
    queryset = AdminScopeService.scoped_programs(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("campus_id"):
        queryset = queryset.filter(campus_id=request.GET.get("campus_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    return render(request, "admin_portal/organization/program_list.html", context)


@portal_required("ADMIN")
@permission_required("programs.create")
def program_create_view(request):
    form = ProgramForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        program = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Program",
            entity_id=program.id,
            actor=request.user,
            after_data=model_before_after(program),
            request=request,
        )
        messages.success(request, "Program created.")
        return _redirect_back_or_default(request, "admin_portal:program_list")
    context = {"form": form, "title": "Create Program"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("programs.update")
def program_update_view(request, program_id: int):
    program = get_object_or_404(AdminScopeService.scoped_programs(request), id=program_id)
    before = model_before_after(program)
    form = ProgramForm(
        request.POST or None,
        instance=program,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        program = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Program",
            entity_id=program.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(program),
            request=request,
        )
        messages.success(request, "Program updated.")
        return _redirect_back_or_default(request, "admin_portal:program_list")
    context = {"form": form, "title": f"Edit Program: {program.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("users.read")
def user_list_view(request):
    queryset = _scoped_users_queryset(request).select_related("default_tenant", "default_campus", "default_department")
    tenant_filter = request.GET.get("tenant_id", "").strip()
    campus_filter = request.GET.get("campus_id", "").strip()
    if tenant_filter:
        queryset = queryset.filter(Q(default_tenant_id=tenant_filter) | Q(user_roles__tenant_id=tenant_filter)).distinct()
    if campus_filter:
        queryset = queryset.filter(Q(default_campus_id=campus_filter) | Q(user_roles__campus_id=campus_filter)).distinct()
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )
    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "tenant_filter": tenant_filter,
        "campus_filter": campus_filter,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/security/user_list.html", context)


@portal_required("ADMIN")
@permission_required("users.create")
def user_create_view(request):
    tenant_qs = AdminScopeService.scoped_tenants(request)
    campus_qs = AdminScopeService.scoped_campuses(request)
    department_qs = AdminScopeService.scoped_departments(request)
    form = UserCreateForm(
        request.POST or None,
        tenant_queryset=tenant_qs,
        campus_queryset=campus_qs,
        department_queryset=department_qs,
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        raw_password = form.cleaned_data["password"]
        user = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="User",
            entity_id=user.id,
            actor=request.user,
            after_data=model_before_after(user),
            request=request,
        )
        try:
            sent_count = _send_new_user_credentials_email(request, user, raw_password)
            if sent_count:
                messages.success(
                    request,
                    f"User created and credentials email sent to {user.email}.",
                )
                AuditService.log_event(
                    action="SEND_CREDENTIALS_EMAIL",
                    portal="ADMIN",
                    entity_type="User",
                    entity_id=user.id,
                    actor=request.user,
                    metadata={"recipient": user.email, "sent_count": sent_count},
                    request=request,
                )
            else:
                messages.warning(
                    request,
                    f"User created, but credentials email was not sent (no message accepted by SMTP).",
                )
                AuditService.log_event(
                    action="SEND_CREDENTIALS_EMAIL_FAILED",
                    portal="ADMIN",
                    entity_type="User",
                    entity_id=user.id,
                    actor=request.user,
                    metadata={"recipient": user.email, "reason": "smtp_accepted_zero_messages"},
                    request=request,
                )
        except Exception as exc:
            messages.warning(
                request,
                f"User created, but credentials email failed: {exc}",
            )
            AuditService.log_event(
                action="SEND_CREDENTIALS_EMAIL_FAILED",
                portal="ADMIN",
                entity_type="User",
                entity_id=user.id,
                actor=request.user,
                metadata={"recipient": user.email, "error": str(exc)[:800]},
                request=request,
            )
        return _redirect_back_or_default(request, "admin_portal:user_list")
    context = {"form": form, "title": "Create User"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/security/user_create.html", context)


@portal_required("ADMIN")
@permission_required("users.update")
def user_update_view(request, user_id: int):
    user = get_object_or_404(_scoped_users_queryset(request), id=user_id)
    before = model_before_after(user)
    tenant_qs = AdminScopeService.scoped_tenants(request)
    campus_qs = AdminScopeService.scoped_campuses(request)
    department_qs = AdminScopeService.scoped_departments(request)
    form = UserUpdateForm(
        request.POST or None,
        instance=user,
        tenant_queryset=tenant_qs,
        campus_queryset=campus_qs,
        department_queryset=department_qs,
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="User",
            entity_id=user.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(user),
            request=request,
        )
        messages.success(request, "User updated.")
        return _redirect_back_or_default(request, "admin_portal:user_list")
    context = {"form": form, "title": f"Edit User: {user.username}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("users.update")
def user_change_password_view(request, user_id: int):
    user = get_object_or_404(_scoped_users_queryset(request), id=user_id)
    form = UserChangePasswordForm(request.POST or None, user=user)
    _style_form(form)

    if request.method == "POST" and form.is_valid():
        user.set_password(form.cleaned_data["new_password1"])
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])
        AuditService.log_event(
            action="CHANGE_PASSWORD",
            portal="ADMIN",
            entity_type="User",
            entity_id=user.id,
            actor=request.user,
            metadata={"target_username": user.username},
            request=request,
        )
        messages.success(request, f"Password updated for {user.username}.")
        return _redirect_back_or_default(request, "admin_portal:user_list")

    context = {"form": form, "title": f"Change Password: {user.username}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("roles.update")
def user_roles_view(request, user_id: int):
    user = get_object_or_404(_scoped_users_queryset(request), id=user_id)
    tenant_qs = AdminScopeService.scoped_tenants(request)
    campus_qs = AdminScopeService.scoped_campuses(request)
    department_qs = AdminScopeService.scoped_departments(request)

    if request.method == "POST" and request.POST.get("action") == "deactivate":
        assignment = get_object_or_404(UserRole, id=request.POST.get("assignment_id"), user=user)
        if not request.user.is_superuser:
            if assignment.tenant_id and assignment.tenant_id not in getattr(request, "scope", {}).get("tenant_ids", []):
                return HttpResponseForbidden("Forbidden scope.")
            if assignment.campus_id and assignment.campus_id not in getattr(request, "scope", {}).get("campus_ids", []):
                return HttpResponseForbidden("Forbidden scope.")
            if assignment.department_id and assignment.department_id not in getattr(request, "scope", {}).get(
                "department_ids", []
            ):
                return HttpResponseForbidden("Forbidden scope.")
        if not user.is_superuser and assignment.is_active:
            still_aligned = _has_active_role_covering_default_scope(
                user,
                exclude_assignment_id=assignment.id,
            )
            if not still_aligned:
                messages.error(
                    request,
                    "Cannot deactivate this assignment because it would leave active roles outside the user's default tenant/campus/department scope. "
                    "Assign a matching scoped role first, or update user defaults.",
                )
                return _redirect_back_or_default(request, "admin_portal:user_roles", user_id=user.id)
        before = model_before_after(assignment)
        assignment.is_active = False
        assignment.save(update_fields=["is_active"])
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="UserRole",
            entity_id=assignment.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(assignment),
            request=request,
        )
        messages.success(request, "Role assignment deactivated.")
        return _redirect_back_or_default(request, "admin_portal:user_roles", user_id=user.id)

    form = UserRoleAssignmentForm(
        request.POST or None,
        tenant_queryset=tenant_qs,
        campus_queryset=campus_qs,
        department_queryset=department_qs,
        target_user=user,
    )
    _style_form(form)
    if request.method == "POST" and request.POST.get("action") != "deactivate" and form.is_valid():
        assignment, created = UserRole.objects.get_or_create(
            user=user,
            role=form.cleaned_data["role"],
            tenant=form.cleaned_data["tenant"],
            campus=form.cleaned_data["campus"],
            department=form.cleaned_data["department"],
            defaults={"is_active": True},
        )
        if not created and not assignment.is_active:
            before = model_before_after(assignment)
            assignment.is_active = True
            assignment.save(update_fields=["is_active"])
            after = model_before_after(assignment)
        else:
            before = None
            after = model_before_after(assignment)

        AuditService.log_event(
            action="CREATE" if created else "UPDATE",
            portal="ADMIN",
            entity_type="UserRole",
            entity_id=assignment.id,
            actor=request.user,
            before_data=before,
            after_data=after,
            request=request,
        )
        messages.success(request, "Role assignment saved.")
        return _redirect_back_or_default(request, "admin_portal:user_roles", user_id=user.id)

    assignments = user.user_roles.select_related("role", "tenant", "campus", "department").order_by("-assigned_at")
    if not request.user.is_superuser:
        tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
        campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
        department_ids = getattr(request, "scope", {}).get("department_ids", [])
        assignments = assignments.filter(
            Q(tenant_id__in=tenant_ids)
            | Q(tenant__isnull=True)
            | Q(campus_id__in=campus_ids)
            | Q(campus__isnull=True)
        ).filter(
            Q(department_id__in=department_ids) | Q(department__isnull=True)
        )
    context = {"target_user": user, "form": form, "assignments": assignments}
    context.update(_scope_context(request))
    return render(request, "admin_portal/security/user_roles.html", context)


@portal_required("ADMIN")
@permission_required("roles.read")
def role_list_view(request):
    roles = Role.objects.annotate(permission_count=Count("role_permissions")).order_by("name")
    context = {"roles": roles}
    context.update(_scope_context(request))
    return render(request, "admin_portal/security/role_list.html", context)


@portal_required("ADMIN")
@permission_required("roles.create")
def role_create_view(request):
    role_queryset = Role.objects.filter(is_active=True).order_by("name")
    form = RoleForm(request.POST or None, role_queryset=role_queryset)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        source_role = form.cleaned_data.get("source_role")
        role = form.save()
        copied_permission_ids = []
        if source_role:
            copied_permission_ids = list(source_role.role_permissions.values_list("permission_id", flat=True))
            for permission_id in copied_permission_ids:
                RolePermission.objects.get_or_create(role=role, permission_id=permission_id)
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Role",
            entity_id=role.id,
            actor=request.user,
            after_data=model_before_after(role),
            metadata={
                "copied_from_role_id": source_role.id if source_role else None,
                "copied_permission_count": len(copied_permission_ids),
            },
            request=request,
        )
        if source_role:
            messages.success(
                request,
                f"Role created. Copied {len(copied_permission_ids)} permission(s) from {source_role.name}.",
            )
        else:
            messages.success(request, "Role created.")
        return _redirect_back_or_default(request, "admin_portal:role_list")

    context = {"form": form, "title": "Create Role"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("roles.update")
def role_update_view(request, role_id: int):
    role = get_object_or_404(Role, id=role_id)
    before = model_before_after(role)
    form = RoleForm(request.POST or None, instance=role, include_copy_option=False)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        role = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Role",
            entity_id=role.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(role),
            request=request,
        )
        messages.success(
            request,
            "Role updated." if role.is_active else "Role updated and deactivated.",
        )
        return _redirect_back_or_default(request, "admin_portal:role_list")

    context = {"form": form, "title": f"Edit Role: {role.name}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("roles.update")
def role_permissions_view(request, role_id: int):
    role = get_object_or_404(Role, id=role_id)
    form = RolePermissionsForm(request.POST or None, role=role)
    if request.method == "POST" and form.is_valid():
        before = list(role.role_permissions.values_list("permission_id", flat=True))
        selected_permissions = set(form.cleaned_data["permissions"].values_list("id", flat=True))
        current_permissions = set(before)
        to_add = selected_permissions - current_permissions
        to_remove = current_permissions - selected_permissions

        for permission_id in to_add:
            RolePermission.objects.get_or_create(role=role, permission_id=permission_id)
        if to_remove:
            RolePermission.objects.filter(role=role, permission_id__in=to_remove).delete()

        after = list(role.role_permissions.values_list("permission_id", flat=True))
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="RolePermission",
            entity_id=role.id,
            actor=request.user,
            before_data={"permission_ids": before},
            after_data={"permission_ids": after},
            request=request,
        )
        messages.success(request, "Role permissions updated.")
        return _redirect_back_or_default(request, "admin_portal:role_list")

    permissions_by_module = {}
    for perm in Permission.objects.filter(is_active=True).order_by("module", "action", "code"):
        permissions_by_module.setdefault(perm.module, []).append(perm)

    context = {"role": role, "form": form, "permissions_by_module": permissions_by_module}
    context.update(_scope_context(request))
    return render(request, "admin_portal/security/role_permissions.html", context)


@portal_required("ADMIN")
@permission_required("menus.read")
def menu_group_list_view(request):
    groups = MenuGroup.objects.all().order_by("portal", "sort_order", "label")
    if not request.user.is_superuser:
        groups = groups.filter(is_active=True)
    context = {"groups": groups}
    context.update(_scope_context(request))
    return render(request, "admin_portal/navigation/menu_group_list.html", context)


@portal_required("ADMIN")
@permission_required("menus.update")
def menu_group_create_view(request):
    form = MenuGroupForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        group = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="MenuGroup",
            entity_id=group.id,
            actor=request.user,
            after_data=model_before_after(group),
            request=request,
        )
        messages.success(request, "Menu group created.")
        return _redirect_back_or_default(request, "admin_portal:menu_group_list")
    context = {"form": form, "title": "Create Menu Group"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("menus.update")
def menu_group_update_view(request, group_id: int):
    group = get_object_or_404(MenuGroup, id=group_id)
    before = model_before_after(group)
    form = MenuGroupForm(request.POST or None, instance=group)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        group = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="MenuGroup",
            entity_id=group.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(group),
            request=request,
        )
        messages.success(request, "Menu group updated.")
        return _redirect_back_or_default(request, "admin_portal:menu_group_list")
    context = {"form": form, "title": f"Edit Menu Group: {group.label}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("menus.read")
def menu_item_list_view(request):
    items = MenuItem.objects.select_related("menu_group", "parent").all().order_by("portal", "menu_group__sort_order", "sort_order")
    if not request.user.is_superuser:
        items = items.filter(is_active=True)
    context = {"items": items}
    context.update(_scope_context(request))
    return render(request, "admin_portal/navigation/menu_item_list.html", context)


@portal_required("ADMIN")
@permission_required("menus.update")
def menu_item_create_view(request):
    form = MenuItemForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        selected_permissions = form.cleaned_data.get("permissions")
        if selected_permissions:
            for permission in selected_permissions:
                MenuItemPermission.objects.get_or_create(menu_item=item, permission=permission)
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="MenuItem",
            entity_id=item.id,
            actor=request.user,
            after_data=model_before_after(item),
            request=request,
        )
        messages.success(request, "Menu item created.")
        return _redirect_back_or_default(request, "admin_portal:menu_item_list")
    context = {"form": form, "title": "Create Menu Item"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("menus.update")
def menu_item_update_view(request, item_id: int):
    item = get_object_or_404(MenuItem, id=item_id)
    before = model_before_after(item)
    form = MenuItemForm(request.POST or None, instance=item)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        selected_permissions = set(form.cleaned_data.get("permissions").values_list("id", flat=True))
        MenuItemPermission.objects.filter(menu_item=item).exclude(permission_id__in=selected_permissions).delete()
        existing_permission_ids = set(
            MenuItemPermission.objects.filter(menu_item=item).values_list("permission_id", flat=True)
        )
        for permission_id in selected_permissions - existing_permission_ids:
            MenuItemPermission.objects.create(menu_item=item, permission_id=permission_id)

        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="MenuItem",
            entity_id=item.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(item),
            request=request,
        )
        messages.success(request, "Menu item updated.")
        return _redirect_back_or_default(request, "admin_portal:menu_item_list")
    context = {"form": form, "title": f"Edit Menu Item: {item.label}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("audit_logs.read")
def audit_log_list_view(request):
    queryset = _scoped_audit_queryset(request)
    portal = request.GET.get("portal", "").strip()
    action = request.GET.get("action", "").strip()
    q = request.GET.get("q", "").strip()
    if portal:
        queryset = queryset.filter(portal=portal)
    if action:
        queryset = queryset.filter(action__icontains=action)
    if q:
        queryset = queryset.filter(
            Q(entity_type__icontains=q) | Q(entity_id__icontains=q) | Q(actor_user__username__icontains=q)
        )

    context = {
        "page_obj": _get_page(request, queryset, per_page=30),
        "portal": portal,
        "action": action,
        "q": q,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/audit/audit_log_list.html", context)


@portal_required("ADMIN")
@permission_required("academic_years.read")
def academic_year_list_view(request):
    queryset = AdminScopeService.scoped_academic_years(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    return render(request, "admin_portal/academics/academic_year_list.html", context)


@portal_required("ADMIN")
@permission_required("academic_years.create")
def academic_year_create_view(request):
    form = AcademicYearForm(request.POST or None, tenant_queryset=AdminScopeService.scoped_tenants(request))
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="AcademicYear",
            entity_id=row.id,
            actor=request.user,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Academic year created.")
        return _redirect_back_or_default(request, "admin_portal:academic_year_list")
    context = {"form": form, "title": "Create Academic Year"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("academic_years.update")
def academic_year_update_view(request, ay_id: int):
    row = get_object_or_404(AdminScopeService.scoped_academic_years(request), id=ay_id)
    before = model_before_after(row)
    form = AcademicYearForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="AcademicYear",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Academic year updated.")
        return _redirect_back_or_default(request, "admin_portal:academic_year_list")
    context = {"form": form, "title": f"Edit Academic Year: {row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("terms.read")
def term_list_view(request):
    queryset = AdminScopeService.scoped_terms(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("academic_year_id"):
        queryset = queryset.filter(academic_year_id=request.GET.get("academic_year_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    context["academic_years"] = AdminScopeService.scoped_academic_years(request)
    return render(request, "admin_portal/academics/term_list.html", context)


@portal_required("ADMIN")
@permission_required("terms.create")
def term_create_view(request):
    form = TermForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        academic_year_queryset=AdminScopeService.scoped_academic_years(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Term",
            entity_id=row.id,
            actor=request.user,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Term created.")
        return _redirect_back_or_default(request, "admin_portal:term_list")
    context = {"form": form, "title": "Create Term"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("terms.update")
def term_update_view(request, term_id: int):
    row = get_object_or_404(AdminScopeService.scoped_terms(request), id=term_id)
    before = model_before_after(row)
    form = TermForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        academic_year_queryset=AdminScopeService.scoped_academic_years(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Term",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Term updated.")
        return _redirect_back_or_default(request, "admin_portal:term_list")
    context = {"form": form, "title": f"Edit Term: {row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("courses.read")
def course_list_view(request):
    queryset = AdminScopeService.scoped_courses(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("campus_id"):
        queryset = queryset.filter(campus_id=request.GET.get("campus_id"))
    if request.GET.get("department_id"):
        queryset = queryset.filter(department_id=request.GET.get("department_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(title__icontains=q))
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    context["departments"] = AdminScopeService.scoped_departments(request)
    return render(request, "admin_portal/academics/course_list.html", context)


@portal_required("ADMIN")
@permission_required("courses.create")
def course_create_view(request):
    form = CourseForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Course",
            entity_id=row.id,
            actor=request.user,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Course created.")
        return _redirect_back_or_default(request, "admin_portal:course_list")
    context = {"form": form, "title": "Create Course"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("courses.update")
def course_update_view(request, course_id: int):
    row = get_object_or_404(AdminScopeService.scoped_courses(request), id=course_id)
    before = model_before_after(row)
    form = CourseForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Course",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Course updated.")
        return _redirect_back_or_default(request, "admin_portal:course_list")
    context = {"form": form, "title": f"Edit Course: {row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("sections.read")
def section_list_view(request):
    queryset = AdminScopeService.scoped_sections(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("campus_id"):
        queryset = queryset.filter(campus_id=request.GET.get("campus_id"))
    if request.GET.get("program_id"):
        queryset = queryset.filter(program_id=request.GET.get("program_id"))
    academic_year_filter = request.GET.get("academic_year_id")
    term_filter = request.GET.get("term_id")
    if academic_year_filter:
        queryset = queryset.filter(course_offerings__academic_year_id=academic_year_filter)
    if term_filter:
        queryset = queryset.filter(course_offerings__term_id=term_filter)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    queryset = queryset.distinct()

    terms_queryset = AdminScopeService.scoped_terms(request)
    if academic_year_filter:
        terms_queryset = terms_queryset.filter(academic_year_id=academic_year_filter)

    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "academic_year_filter": academic_year_filter or "",
        "term_filter": term_filter or "",
    }
    context.update(_scope_context(request))
    context["programs"] = AdminScopeService.scoped_programs(request)
    context["academic_years"] = AdminScopeService.scoped_academic_years(request)
    context["terms"] = terms_queryset
    return render(request, "admin_portal/academics/section_list.html", context)


@portal_required("ADMIN")
@permission_required("sections.create")
def section_create_view(request):
    form = SectionForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
        program_queryset=AdminScopeService.scoped_programs(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Section",
            entity_id=row.id,
            actor=request.user,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Section created.")
        return _redirect_back_or_default(request, "admin_portal:section_list")
    context = {"form": form, "title": "Create Section"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("sections.update")
def section_update_view(request, section_id: int):
    row = get_object_or_404(AdminScopeService.scoped_sections(request), id=section_id)
    before = model_before_after(row)
    form = SectionForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
        program_queryset=AdminScopeService.scoped_programs(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Section",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Section updated.")
        return _redirect_back_or_default(request, "admin_portal:section_list")
    context = {"form": form, "title": f"Edit Section: {row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("offerings.read")
def offering_list_view(request):
    queryset = AdminScopeService.scoped_course_offerings(request)
    if request.GET.get("campus_id"):
        queryset = queryset.filter(campus_id=request.GET.get("campus_id"))
    if request.GET.get("academic_year_id"):
        queryset = queryset.filter(academic_year_id=request.GET.get("academic_year_id"))
    if request.GET.get("term_id"):
        queryset = queryset.filter(term_id=request.GET.get("term_id"))
    if request.GET.get("department_id"):
        queryset = queryset.filter(department_id=request.GET.get("department_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(course__code__icontains=q) | Q(section__code__icontains=q) | Q(schedule_text__icontains=q)
        )
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    context["academic_years"] = AdminScopeService.scoped_academic_years(request)
    context["terms"] = AdminScopeService.scoped_terms(request)
    context["departments"] = AdminScopeService.scoped_departments(request)
    return render(request, "admin_portal/academics/offering_list.html", context)


@portal_required("ADMIN")
@permission_required("offerings.create")
def offering_create_view(request):
    form = CourseOfferingForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
        program_queryset=AdminScopeService.scoped_programs(request),
        academic_year_queryset=AdminScopeService.scoped_academic_years(request),
        term_queryset=AdminScopeService.scoped_terms(request),
        course_queryset=AdminScopeService.scoped_courses(request),
        section_queryset=AdminScopeService.scoped_sections(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="CourseOffering",
            entity_id=row.id,
            actor=request.user,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Course offering created.")
        return _redirect_back_or_default(request, "admin_portal:offering_list")
    context = {"form": form, "title": "Create Course Offering"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("offerings.update")
def offering_update_view(request, offering_id: int):
    row = get_object_or_404(AdminScopeService.scoped_course_offerings(request), id=offering_id)
    before = model_before_after(row)
    form = CourseOfferingForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
        program_queryset=AdminScopeService.scoped_programs(request),
        academic_year_queryset=AdminScopeService.scoped_academic_years(request),
        term_queryset=AdminScopeService.scoped_terms(request),
        course_queryset=AdminScopeService.scoped_courses(request),
        section_queryset=AdminScopeService.scoped_sections(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="CourseOffering",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Course offering updated.")
        return _redirect_back_or_default(request, "admin_portal:offering_list")
    context = {"form": form, "title": f"Edit Offering #{row.id}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.read")
def faculty_assignment_list_view(request):
    faculty_ids = AdminScopeService.scoped_faculty_users(request)
    all_faculty = (
        User.objects.filter(id__in=faculty_ids, is_active=True)
        .order_by("last_name", "first_name", "username")
    )

    faculty_q = request.GET.get("faculty_q", "").strip()
    faculty_candidates = all_faculty
    if faculty_q:
        faculty_candidates = faculty_candidates.filter(
            Q(username__icontains=faculty_q)
            | Q(email__icontains=faculty_q)
            | Q(first_name__icontains=faculty_q)
            | Q(last_name__icontains=faculty_q)
        )

    selected_faculty_id = _safe_int(request.GET.get("faculty_user_id"))
    selected_faculty = all_faculty.filter(id=selected_faculty_id).first() if selected_faculty_id else None
    show_assign_box = request.GET.get("assign") == "1" and selected_faculty is not None

    offering_q = request.GET.get("offering_q", "").strip()
    selected_section_id = _safe_int(request.GET.get("section_id"))
    sections = AdminScopeService.scoped_sections(request).filter(is_active=True).order_by("code")
    assignable_offerings = AdminScopeService.scoped_course_offerings(request).filter(is_active=True).exclude(
        faculty_assignments__is_active=True
    )
    if selected_section_id:
        assignable_offerings = assignable_offerings.filter(section_id=selected_section_id)
    if offering_q:
        assignable_offerings = assignable_offerings.filter(
            Q(course__code__icontains=offering_q) | Q(course__title__icontains=offering_q)
        )
    assignable_offerings = assignable_offerings.order_by(
        "academic_year__start_date", "term__sequence_no", "course__code", "section__code"
    )
    assignable_count = assignable_offerings.count()

    selected_faculty_assignments = None
    assigned_count = 0
    if selected_faculty:
        selected_faculty_assignments = (
            AdminScopeService.scoped_faculty_assignments(request)
            .filter(faculty_user_id=selected_faculty.id, is_active=True)
            .select_related("offering", "offering__course", "offering__section", "offering__term", "offering__academic_year")
            .order_by("offering__academic_year__start_date", "offering__term__sequence_no", "offering__course__code")
        )
        if selected_section_id:
            selected_faculty_assignments = selected_faculty_assignments.filter(offering__section_id=selected_section_id)
        assigned_count = selected_faculty_assignments.count()

    context = {
        "faculty_q": faculty_q,
        "faculty_candidates": faculty_candidates,
        "selected_faculty": selected_faculty,
        "show_assign_box": show_assign_box,
        "offering_q": offering_q,
        "selected_section_id": selected_section_id,
        "sections": sections,
        "assignable_offerings": assignable_offerings,
        "assignable_count": assignable_count,
        "selected_faculty_assignments": selected_faculty_assignments,
        "assigned_count": assigned_count,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/academics/faculty_assignment_list.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.read")
def faculty_gradebook_monitor_view(request):
    faculty_ids = AdminScopeService.scoped_faculty_users(request)
    all_faculty = User.objects.filter(id__in=faculty_ids, is_active=True).order_by("last_name", "first_name", "username")

    faculty_q = request.GET.get("faculty_q", "").strip()
    faculty_candidates = all_faculty
    if faculty_q:
        faculty_candidates = faculty_candidates.filter(
            Q(username__icontains=faculty_q)
            | Q(email__icontains=faculty_q)
            | Q(first_name__icontains=faculty_q)
            | Q(last_name__icontains=faculty_q)
        )

    selected_faculty_id = _safe_int(request.GET.get("faculty_user_id"))
    selected_faculty = all_faculty.filter(id=selected_faculty_id).first() if selected_faculty_id else None

    selected_offering = None
    selected_period = None
    periods = []
    metric_cards = []
    rows = []
    summary_layout = {"class_standing_blocks": [], "exam_components": []}
    q = request.GET.get("q", "").strip()
    is_masked = _should_mask_gradebook_student_identity(request.user)
    period_state = None
    submit_readiness = None
    selected_faculty_assignments = FacultyAssignment.objects.none()
    table_colspan = 5

    if selected_faculty:
        selected_faculty_assignments = (
            AdminScopeService.scoped_faculty_assignments(request)
            .filter(faculty_user_id=selected_faculty.id, is_active=True)
            .select_related(
                "offering",
                "offering__course",
                "offering__section",
                "offering__term",
                "offering__academic_year",
                "offering__campus",
            )
            .order_by("offering__academic_year__start_date", "offering__term__sequence_no", "offering__course__code")
        )
        offering_map = {assignment.offering_id: assignment.offering for assignment in selected_faculty_assignments}
        selected_offering_id = _safe_int(request.GET.get("offering_id"))
        if selected_offering_id in offering_map:
            selected_offering = offering_map[selected_offering_id]
        elif offering_map:
            selected_offering = next(iter(offering_map.values()))

        if selected_offering:
            try:
                template = FacultyGradingService.resolve_template_for_offering(selected_offering)
            except ValidationError as exc:
                messages.error(request, str(exc))
                template = None
            if template:
                periods = list(template.periods.filter(is_active=True).order_by("sequence_no", "id"))
                period_map = {period.id: period for period in periods}
                selected_period_id = _safe_int(request.GET.get("period_id"))
                if selected_period_id in period_map:
                    selected_period = period_map[selected_period_id]
                elif periods:
                    selected_period = periods[0]

            if selected_period:
                period_state = _period_edit_state(selected_offering, selected_period)
                period_rows = list(
                    Enrollment.objects.filter(course_offering_id=selected_offering.id, is_active=True)
                    .select_related("student")
                    .order_by("student__last_name", "student__first_name", "student__student_no")
                )
                grade_map = {
                    row.student_id: row
                    for row in StudentPeriodGrade.objects.filter(
                        offering_id=selected_offering.id,
                        template_period_id=selected_period.id,
                    )
                }
                base_rows = []
                for enrollment in period_rows:
                    grade_row = grade_map.get(enrollment.student_id)
                    base_rows.append(
                        {
                            "student": enrollment.student,
                            "enrollment_status": enrollment.enrollment_status,
                            "component_scores": {},
                            "class_standing": grade_row.class_standing_grade if grade_row else None,
                            "exam_grade": grade_row.exam_grade if grade_row else None,
                            "period_grade": grade_row.period_grade if grade_row else None,
                        }
                    )

                activities = list(
                    GradeActivity.objects.filter(
                        offering_id=selected_offering.id,
                        template_period_id=selected_period.id,
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
                summary_layout = _build_summary_layout(selected_period, activities)
                score_by_activity = {
                    (score.student_id, score.activity_id): Decimal(score.computed_score)
                    for score in StudentActivityScore.objects.filter(
                        activity_id__in=[activity.id for activity in activities],
                        is_active=True,
                        activity__is_active=True,
                    )
                }
                filtered_rows = base_rows
                if q:
                    lowered_q = q.lower()
                    filtered_rows = [
                        row
                        for row in base_rows
                        if lowered_q in row["student"].student_no.lower()
                        or lowered_q in row["student"].last_name.lower()
                        or lowered_q in row["student"].first_name.lower()
                    ]

                for row in filtered_rows:
                    summary_values = _build_summary_row_values(row, summary_layout, score_by_activity)
                    rows.append(
                        {
                            "student": row["student"],
                            "display_student_no": _mask_student_number(row["student"].student_no) if is_masked else row["student"].student_no,
                            "display_student_name": _mask_student_name(row["student"]) if is_masked else f"{row['student'].last_name}, {row['student'].first_name}",
                            "enrollment_status": row["enrollment_status"],
                            "class_standing_blocks": summary_values["class_standing_blocks"],
                            "exam_values": summary_values["exam_values"],
                            "period_grade": row["period_grade"],
                            "print_grade_status": (
                                "PASSED"
                                if row["period_grade"] is not None
                                and Decimal(row["period_grade"]) >= FacultyGradingService.resolve_passing_threshold(selected_offering)
                                else "FAILED"
                                if row["period_grade"] is not None
                                else ""
                            ),
                        }
                    )

                passing_threshold = FacultyGradingService.resolve_passing_threshold(selected_offering)
                metric_cards = _gradebook_metrics(base_rows, passing_threshold=passing_threshold)
                submit_readiness = GradingGovernanceService.evaluate_submission_readiness(
                    offering=selected_offering,
                    template_period=selected_period,
                )
                table_colspan = (
                    4
                    + sum(block["colspan"] for block in summary_layout["class_standing_blocks"])
                    + len(summary_layout["exam_components"])
                    + 1
                )
                AuditService.log_event(
                    action="READ",
                    portal="ADMIN",
                    entity_type="FacultyGradebookMonitor",
                    entity_id=f"{selected_faculty.id}:{selected_offering.id}:{selected_period.id}",
                    actor=request.user,
                    tenant=selected_offering.tenant,
                    campus=selected_offering.campus,
                    metadata={
                        "faculty_user_id": selected_faculty.id,
                        "offering_id": selected_offering.id,
                        "period_id": selected_period.id,
                        "masked_student_identity": is_masked,
                    },
                    request=request,
                )

    context = {
        "faculty_q": faculty_q,
        "faculty_candidates": faculty_candidates,
        "selected_faculty": selected_faculty,
        "selected_faculty_assignments": selected_faculty_assignments,
        "selected_offering": selected_offering,
        "periods": periods,
        "selected_period": selected_period,
        "period_state": period_state,
        "metric_cards": metric_cards,
        "rows": rows,
        "summary_layout": summary_layout,
        "submit_readiness": submit_readiness,
        "q": q,
        "is_masked": is_masked,
        "table_colspan": table_colspan,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/academics/faculty_gradebook_monitor.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.create")
def faculty_assignment_assign_view(request):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid request method.")

    faculty_user_id = _safe_int(request.POST.get("faculty_user_id"))
    selected_ids = []
    for raw in request.POST.getlist("offering_ids"):
        val = _safe_int(raw)
        if val:
            selected_ids.append(val)
    # Backward compatibility with older single-select post.
    single_offering_id = _safe_int(request.POST.get("offering_id"))
    if single_offering_id:
        selected_ids.append(single_offering_id)
    selected_ids = sorted(set(selected_ids))
    faculty_q = (request.POST.get("faculty_q") or "").strip()
    offering_q = (request.POST.get("offering_q") or "").strip()
    selected_section_id = _safe_int(request.POST.get("section_id"))

    redirect_params = {"assign": "1"}
    if faculty_user_id:
        redirect_params["faculty_user_id"] = faculty_user_id
    if faculty_q:
        redirect_params["faculty_q"] = faculty_q
    if offering_q:
        redirect_params["offering_q"] = offering_q
    if selected_section_id:
        redirect_params["section_id"] = selected_section_id

    if not faculty_user_id or not selected_ids:
        messages.error(request, "Select faculty and at least one course offering before assigning.")
        return redirect(f"{reverse('admin_portal:faculty_assignment_list')}?{urlencode(redirect_params)}")

    faculty_ids = AdminScopeService.scoped_faculty_users(request)
    faculty_user = get_object_or_404(
        User.objects.filter(id__in=faculty_ids, is_active=True),
        id=faculty_user_id,
    )
    offerings_map = {
        row.id: row
        for row in AdminScopeService.scoped_course_offerings(request)
        .filter(is_active=True, id__in=selected_ids)
        .select_related("course", "section", "term")
    }

    created_count = 0
    reactivated_count = 0
    skipped_count = 0

    for offering_id in selected_ids:
        offering = offerings_map.get(offering_id)
        if not offering:
            skipped_count += 1
            continue

        # Workflow rule: only assign offerings that are not yet assigned (active) to any faculty.
        if FacultyAssignment.objects.filter(offering=offering, is_active=True).exists():
            skipped_count += 1
            continue

        existing = FacultyAssignment.objects.filter(offering=offering, faculty_user=faculty_user).first()
        if existing and not existing.is_active:
            before = model_before_after(existing)
            existing.is_active = True
            existing.save(update_fields=["is_active", "updated_at"])
            AuditService.log_event(
                action="UPDATE",
                portal="ADMIN",
                entity_type="FacultyAssignment",
                entity_id=existing.id,
                actor=request.user,
                before_data=before,
                after_data=model_before_after(existing),
                request=request,
            )
            reactivated_count += 1
            continue

        if existing and existing.is_active:
            skipped_count += 1
            continue

        created = FacultyAssignment.objects.create(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            offering=offering,
            faculty_user=faculty_user,
            is_primary=False,
            is_active=True,
        )
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="FacultyAssignment",
            entity_id=created.id,
            actor=request.user,
            after_data=model_before_after(created),
            request=request,
        )
        created_count += 1

    if created_count or reactivated_count:
        messages.success(
            request,
            f"Assignments completed. Created: {created_count}, Reactivated: {reactivated_count}, Skipped: {skipped_count}.",
        )
    else:
        messages.warning(request, "No offerings were assigned. They may already be assigned or out of scope.")

    return redirect(f"{reverse('admin_portal:faculty_assignment_list')}?{urlencode(redirect_params)}")


@portal_required("ADMIN")
@permission_required("faculty_assignments.update")
def faculty_assignment_unassign_view(request):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid request method.")

    faculty_user_id = _safe_int(request.POST.get("faculty_user_id"))
    selected_ids = []
    for raw in request.POST.getlist("assignment_ids"):
        val = _safe_int(raw)
        if val:
            selected_ids.append(val)
    selected_ids = sorted(set(selected_ids))

    faculty_q = (request.POST.get("faculty_q") or "").strip()
    offering_q = (request.POST.get("offering_q") or "").strip()
    selected_section_id = _safe_int(request.POST.get("section_id"))

    redirect_params = {"assign": "1"}
    if faculty_user_id:
        redirect_params["faculty_user_id"] = faculty_user_id
    if faculty_q:
        redirect_params["faculty_q"] = faculty_q
    if offering_q:
        redirect_params["offering_q"] = offering_q
    if selected_section_id:
        redirect_params["section_id"] = selected_section_id

    if not faculty_user_id or not selected_ids:
        messages.error(request, "Select assigned offerings to unassign.")
        return redirect(f"{reverse('admin_portal:faculty_assignment_list')}?{urlencode(redirect_params)}")

    assignments = (
        AdminScopeService.scoped_faculty_assignments(request)
        .filter(id__in=selected_ids, faculty_user_id=faculty_user_id, is_active=True)
        .select_related("offering", "faculty_user")
    )

    unassigned_count = 0
    for assignment in assignments:
        before = model_before_after(assignment)
        assignment.is_active = False
        assignment.is_primary = False
        assignment.save(update_fields=["is_active", "is_primary", "updated_at"])
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="FacultyAssignment",
            entity_id=assignment.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(assignment),
            request=request,
        )
        unassigned_count += 1

    if unassigned_count:
        messages.success(request, f"Unassigned {unassigned_count} offering(s).")
    else:
        messages.warning(request, "No offerings were unassigned. They may already be inactive or out of scope.")

    return redirect(f"{reverse('admin_portal:faculty_assignment_list')}?{urlencode(redirect_params)}")


@portal_required("ADMIN")
@permission_required("faculty_assignments.update")
def faculty_assignment_toggle_primary_view(request):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid request method.")

    assignment_id = _safe_int(request.POST.get("assignment_id"))
    faculty_user_id = _safe_int(request.POST.get("faculty_user_id"))
    faculty_q = (request.POST.get("faculty_q") or "").strip()
    offering_q = (request.POST.get("offering_q") or "").strip()
    selected_section_id = _safe_int(request.POST.get("section_id"))

    redirect_params = {"assign": "1"}
    if faculty_user_id:
        redirect_params["faculty_user_id"] = faculty_user_id
    if faculty_q:
        redirect_params["faculty_q"] = faculty_q
    if offering_q:
        redirect_params["offering_q"] = offering_q
    if selected_section_id:
        redirect_params["section_id"] = selected_section_id

    if not assignment_id:
        messages.error(request, "Invalid assignment selection.")
        return redirect(f"{reverse('admin_portal:faculty_assignment_list')}?{urlencode(redirect_params)}")

    assignment = get_object_or_404(
        AdminScopeService.scoped_faculty_assignments(request).filter(is_active=True),
        id=assignment_id,
    )

    if faculty_user_id and assignment.faculty_user_id != faculty_user_id:
        messages.error(request, "Selected assignment does not match the selected faculty.")
        return redirect(f"{reverse('admin_portal:faculty_assignment_list')}?{urlencode(redirect_params)}")

    new_primary = not assignment.is_primary

    if new_primary:
        other_primary_assignments = (
            AdminScopeService.scoped_faculty_assignments(request)
            .filter(offering_id=assignment.offering_id, is_active=True, is_primary=True)
            .exclude(id=assignment.id)
        )
        for other in other_primary_assignments:
            other_before = model_before_after(other)
            other.is_primary = False
            other.save(update_fields=["is_primary", "updated_at"])
            AuditService.log_event(
                action="UPDATE",
                portal="ADMIN",
                entity_type="FacultyAssignment",
                entity_id=other.id,
                actor=request.user,
                before_data=other_before,
                after_data=model_before_after(other),
                request=request,
            )

    before = model_before_after(assignment)
    assignment.is_primary = new_primary
    assignment.save(update_fields=["is_primary", "updated_at"])
    AuditService.log_event(
        action="UPDATE",
        portal="ADMIN",
        entity_type="FacultyAssignment",
        entity_id=assignment.id,
        actor=request.user,
        before_data=before,
        after_data=model_before_after(assignment),
        request=request,
    )
    messages.success(
        request,
        "Primary faculty set for selected offering." if new_primary else "Primary faculty removed for selected offering.",
    )
    return redirect(f"{reverse('admin_portal:faculty_assignment_list')}?{urlencode(redirect_params)}")


@portal_required("ADMIN")
@permission_required("faculty_assignments.create")
def faculty_assignment_create_view(request):
    faculty_ids = AdminScopeService.scoped_faculty_users(request)
    faculty_queryset = User.objects.filter(id__in=faculty_ids).order_by("username")
    form = FacultyAssignmentForm(
        request.POST or None,
        offering_queryset=AdminScopeService.scoped_course_offerings(request),
        faculty_queryset=faculty_queryset,
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        row.tenant_id = row.offering.tenant_id
        row.campus_id = row.offering.campus_id
        row.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="FacultyAssignment",
            entity_id=row.id,
            actor=request.user,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Faculty assignment created.")
        return _redirect_back_or_default(request, "admin_portal:faculty_assignment_list")
    context = {"form": form, "title": "Create Faculty Assignment"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.update")
def faculty_assignment_update_view(request, assignment_id: int):
    row = get_object_or_404(AdminScopeService.scoped_faculty_assignments(request), id=assignment_id)
    before = model_before_after(row)
    faculty_ids = AdminScopeService.scoped_faculty_users(request)
    faculty_queryset = User.objects.filter(id__in=faculty_ids).order_by("username")
    form = FacultyAssignmentForm(
        request.POST or None,
        instance=row,
        offering_queryset=AdminScopeService.scoped_course_offerings(request),
        faculty_queryset=faculty_queryset,
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        row.tenant_id = row.offering.tenant_id
        row.campus_id = row.offering.campus_id
        row.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="FacultyAssignment",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Faculty assignment updated.")
        return _redirect_back_or_default(request, "admin_portal:faculty_assignment_list")
    context = {"form": form, "title": f"Edit Faculty Assignment #{row.id}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("students.read")
def student_list_view(request):
    queryset = AdminScopeService.scoped_students(request)
    if request.GET.get("campus_id"):
        queryset = queryset.filter(campus_id=request.GET.get("campus_id"))
    if request.GET.get("program_id"):
        queryset = queryset.filter(program_id=request.GET.get("program_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(student_no__icontains=q)
            | Q(last_name__icontains=q)
            | Q(first_name__icontains=q)
        )
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    context["programs"] = AdminScopeService.scoped_programs(request)
    return render(request, "admin_portal/students/student_list.html", context)


@portal_required("ADMIN")
@permission_required("students.create")
def student_create_view(request):
    form = StudentForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
        program_queryset=AdminScopeService.scoped_programs(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="Student",
            entity_id=row.id,
            actor=request.user,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Student created.")
        return _redirect_back_or_default(request, "admin_portal:student_list")
    context = {"form": form, "title": "Create Student"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("students.update")
def student_update_view(request, student_id: int):
    row = get_object_or_404(AdminScopeService.scoped_students(request), id=student_id)
    before = model_before_after(row)
    form = StudentForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
        program_queryset=AdminScopeService.scoped_programs(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Student",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Student updated.")
        return _redirect_back_or_default(request, "admin_portal:student_list")
    context = {"form": form, "title": f"Edit Student: {row.student_no}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("enrollment.read")
def enrollment_list_view(request):
    queryset = AdminScopeService.scoped_enrollments(request)
    campus_id = request.GET.get("campus_id", "").strip()
    academic_year_id = request.GET.get("academic_year_id", "").strip()
    term_id = request.GET.get("term_id", "").strip()
    section_id = request.GET.get("section_id", "").strip()
    course_id = request.GET.get("course_id", "").strip()
    offering_id = request.GET.get("offering_id", "").strip()
    status = request.GET.get("status", "").strip()

    if campus_id:
        queryset = queryset.filter(campus_id=campus_id)
    if academic_year_id:
        queryset = queryset.filter(academic_year_id=academic_year_id)
    if term_id:
        queryset = queryset.filter(term_id=term_id)
    if section_id:
        queryset = queryset.filter(course_offering__section_id=section_id)
    if course_id:
        queryset = queryset.filter(course_offering__course_id=course_id)
    if offering_id:
        queryset = queryset.filter(course_offering_id=offering_id)
    if status:
        queryset = queryset.filter(enrollment_status=status)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(student__student_no__icontains=q)
            | Q(student__last_name__icontains=q)
            | Q(student__first_name__icontains=q)
            | Q(course_offering__course__code__icontains=q)
            | Q(course_offering__section__code__icontains=q)
        )

    offerings = AdminScopeService.scoped_course_offerings(request)
    if campus_id:
        offerings = offerings.filter(campus_id=campus_id)
    if academic_year_id:
        offerings = offerings.filter(academic_year_id=academic_year_id)
    if term_id:
        offerings = offerings.filter(term_id=term_id)
    if section_id:
        offerings = offerings.filter(section_id=section_id)
    if course_id:
        offerings = offerings.filter(course_id=course_id)

    # Section options in filter dropdown should follow Campus + Academic Year + Term selection.
    section_options = AdminScopeService.scoped_sections(request)
    if campus_id:
        section_options = section_options.filter(campus_id=campus_id)
    if academic_year_id or term_id:
        offering_scope = AdminScopeService.scoped_course_offerings(request)
        if campus_id:
            offering_scope = offering_scope.filter(campus_id=campus_id)
        if academic_year_id:
            offering_scope = offering_scope.filter(academic_year_id=academic_year_id)
        if term_id:
            offering_scope = offering_scope.filter(term_id=term_id)
        section_options = section_options.filter(id__in=offering_scope.values_list("section_id", flat=True))
    section_options = section_options.distinct()

    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    context["offerings"] = offerings
    context["academic_years"] = AdminScopeService.scoped_academic_years(request)
    context["terms"] = AdminScopeService.scoped_terms(request)
    context["sections"] = section_options
    context["courses"] = AdminScopeService.scoped_courses(request)
    context["status"] = status
    context["campus_id"] = campus_id
    context["academic_year_id"] = academic_year_id
    context["term_id"] = term_id
    context["section_id"] = section_id
    context["course_id"] = course_id
    context["offering_id"] = offering_id
    return render(request, "admin_portal/enrollment/enrollment_list.html", context)


@portal_required("ADMIN")
@permission_required("enrollment.create")
def enrollment_create_view(request):
    offering_qs = AdminScopeService.scoped_course_offerings(request)
    student_qs = AdminScopeService.scoped_students(request)
    form = EnrollmentForm(request.POST or None, offering_queryset=offering_qs, student_queryset=student_qs)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        offering = form.cleaned_data["course_offering"]
        student = form.cleaned_data["student"]
        enrollment_status = form.cleaned_data["enrollment_status"]
        try:
            enrollment, created = EnrollmentService.create_enrollment(
                user=request.user,
                offering=offering,
                student=student,
                enrollment_status=enrollment_status,
                portal=Enrollment.SourcePortal.ADMIN,
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, str(exc))
            context = {"form": form, "title": "Create Enrollment"}
            context.update(_scope_context(request))
            return render(request, "admin_portal/shared/form_page.html", context)
        AuditService.log_event(
            action="CREATE" if created else "UPDATE",
            portal="ADMIN",
            entity_type="Enrollment",
            entity_id=enrollment.id,
            actor=request.user,
            after_data=model_before_after(enrollment),
            request=request,
        )
        messages.success(request, "Enrollment saved.")
        return _redirect_back_or_default(request, "admin_portal:enrollment_list")
    context = {"form": form, "title": "Create Enrollment"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("enrollment.update")
def enrollment_update_view(request, enrollment_id: int):
    enrollment = get_object_or_404(AdminScopeService.scoped_enrollments(request), id=enrollment_id)
    before = model_before_after(enrollment)
    offering_qs = AdminScopeService.scoped_course_offerings(request)
    student_qs = AdminScopeService.scoped_students(request)
    form = EnrollmentForm(
        request.POST or None,
        instance=enrollment,
        offering_queryset=offering_qs,
        student_queryset=student_qs,
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        enrollment.course_offering = form.cleaned_data["course_offering"]
        enrollment.student = form.cleaned_data["student"]
        enrollment.tenant_id = form.cleaned_data["course_offering"].tenant_id
        enrollment.campus_id = form.cleaned_data["course_offering"].campus_id
        enrollment.academic_year_id = form.cleaned_data["course_offering"].academic_year_id
        enrollment.term_id = form.cleaned_data["course_offering"].term_id
        try:
            enrollment = EnrollmentService.update_enrollment(
                user=request.user,
                enrollment=enrollment,
                enrollment_status=form.cleaned_data["enrollment_status"],
                is_active=form.cleaned_data["is_active"],
                portal=Enrollment.SourcePortal.ADMIN,
            )
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, str(exc))
            context = {"form": form, "title": f"Edit Enrollment #{enrollment.id}"}
            context.update(_scope_context(request))
            return render(request, "admin_portal/shared/form_page.html", context)
        enrollment.save(
            update_fields=[
                "course_offering",
                "student",
                "tenant",
                "campus",
                "academic_year",
                "term",
                "updated_at",
            ]
        )
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="Enrollment",
            entity_id=enrollment.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(enrollment),
            request=request,
        )
        messages.success(request, "Enrollment updated.")
        return _redirect_back_or_default(request, "admin_portal:enrollment_list")
    context = {"form": form, "title": f"Edit Enrollment #{enrollment.id}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


def _ensure_template_editable_or_forbidden(request, template):
    try:
        GradingTemplateService.ensure_editable(template)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")
    return None


@portal_required("ADMIN")
@permission_required("grading_templates.read")
def grading_template_list_view(request):
    queryset = AdminScopeService.scoped_grading_templates(request).annotate(period_count=Count("periods"))
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("published") == "yes":
        queryset = queryset.filter(is_published=True)
    elif request.GET.get("published") == "no":
        queryset = queryset.filter(is_published=False)
    approval_status = request.GET.get("approval_status", "").strip()
    if approval_status:
        queryset = queryset.filter(approval_status=approval_status)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "published": request.GET.get("published", ""),
        "approval_status": approval_status,
        "approval_status_choices": GradingTemplate.ApprovalStatus.choices,
        "involved_personalities": TemplateHotfixService.involved_personalities(),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/template_list.html", context)


@portal_required("ADMIN")
@permission_required("grading_templates.read")
def grading_template_structure_view(request, template_id: int):
    template = get_object_or_404(AdminScopeService.scoped_grading_templates(request), id=template_id)
    template = (
        GradingTemplate.objects.filter(id=template.id)
        .select_related("tenant", "published_by")
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
    if not template:
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")

    period_rows = []
    for period in template.periods.all():
        component_rows = []
        component_total = Decimal("0")
        for component in period.components.all():
            component_weight = Decimal(component.weight_percentage or 0)
            component_total += component_weight

            subcomponent_rows = []
            subcomponent_total = None
            subcomponents = list(component.subcomponents.all())
            if subcomponents:
                subcomponent_total = Decimal("0")
                for subcomponent in subcomponents:
                    sub_weight = Decimal(subcomponent.weight_percentage or 0)
                    subcomponent_total += sub_weight

                    detail_rows = []
                    detail_total = None
                    details = list(subcomponent.details.all())
                    if details:
                        detail_total = Decimal("0")
                        for detail in details:
                            detail_weight = Decimal(detail.weight_percentage or 0)
                            detail_total += detail_weight
                            detail_rows.append({"row": detail, "weight": detail_weight})

                    subcomponent_rows.append(
                        {
                            "row": subcomponent,
                            "weight": sub_weight,
                            "details": detail_rows,
                            "detail_total": detail_total,
                            "detail_total_ok": detail_total is None or detail_total == Decimal("100"),
                        }
                    )

            component_rows.append(
                {
                    "row": component,
                    "weight": component_weight,
                    "subcomponents": subcomponent_rows,
                    "subcomponent_total": subcomponent_total,
                    "subcomponent_total_ok": subcomponent_total is None or subcomponent_total == Decimal("100"),
                }
            )

        period_rows.append(
            {
                "row": period,
                "components": component_rows,
                "component_total": component_total,
                "component_total_ok": component_total == Decimal("100"),
            }
        )

    validation_errors = GradingTemplateService.validate_publishable(template)
    context = {
        "template_obj": template,
        "period_rows": period_rows,
        "validation_errors": validation_errors,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/template_structure_preview.html", context)


@portal_required("ADMIN")
@permission_required("grading_templates.read")
def grading_template_builder_view(request, template_id: int):
    template = get_object_or_404(AdminScopeService.scoped_grading_templates(request), id=template_id)
    template = (
        GradingTemplate.objects.filter(id=template.id)
        .select_related("tenant", "published_by", "approval_requested_by", "approval_reviewed_by")
        .prefetch_related(
            Prefetch(
                "periods",
                queryset=GradingTemplatePeriod.objects.filter(is_active=True).order_by("sequence_no", "id").prefetch_related(
                    Prefetch(
                        "components",
                        queryset=GradingTemplateComponent.objects.filter(is_active=True).order_by("sort_order", "id").prefetch_related(
                            Prefetch(
                                "subcomponents",
                                queryset=GradingTemplateSubcomponent.objects.filter(is_active=True).order_by("sort_order", "id").prefetch_related(
                                    Prefetch(
                                        "details",
                                        queryset=GradingTemplateDetail.objects.filter(is_active=True).order_by("sort_order", "id"),
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
    if not template:
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")

    period_rows = []
    for period in template.periods.all():
        component_rows = []
        for component in period.components.all():
            subcomponent_rows = []
            for subcomponent in component.subcomponents.all():
                subcomponent_rows.append(
                    {
                        "row": subcomponent,
                        "details": list(subcomponent.details.all()),
                    }
                )
            component_rows.append(
                {
                    "row": component,
                    "subcomponents": subcomponent_rows,
                }
            )
        period_rows.append({"row": period, "components": component_rows})

    context = {
        "template_obj": template,
        "period_rows": period_rows,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/template_builder.html", context)


@portal_required("ADMIN")
@permission_required("grading_templates.create")
def grading_template_create_view(request):
    form = GradingTemplateForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="GradingTemplate",
            entity_id=row.id,
            actor=request.user,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Grading template created.")
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")
    context = {"form": form, "title": "Create Grading Template"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("grading_templates.update")
def grading_template_update_view(request, template_id: int):
    row = get_object_or_404(AdminScopeService.scoped_grading_templates(request), id=template_id)
    locked_response = _ensure_template_editable_or_forbidden(request, row)
    if locked_response:
        return locked_response
    before = model_before_after(row)
    form = GradingTemplateForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        if not row.is_published and row.approval_status != GradingTemplate.ApprovalStatus.DRAFT:
            row.approval_status = GradingTemplate.ApprovalStatus.DRAFT
            row.approval_requested_by = None
            row.approval_requested_at = None
            row.approval_reviewed_by = None
            row.approval_reviewed_at = None
            row.approval_remarks = None
            row.save(
                update_fields=[
                    "approval_status",
                    "approval_requested_by",
                    "approval_requested_at",
                    "approval_reviewed_by",
                    "approval_reviewed_at",
                    "approval_remarks",
                    "updated_at",
                ]
            )
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="GradingTemplate",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        if row.is_published:
            messages.warning(
                request,
                "Published template updated. Create a hotfix request so changes are applied with approval and scope control.",
            )
        else:
            messages.success(request, "Grading template updated.")
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")
    context = {"form": form, "title": f"Edit Grading Template: {row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("grading_templates.publish")
def grading_template_publish_view(request, template_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid method.")
    row = get_object_or_404(AdminScopeService.scoped_grading_templates(request), id=template_id)
    before = model_before_after(row)
    try:
        GradingTemplateService.publish(template=row, actor=request.user)
    except ValidationError as exc:
        if hasattr(exc, "messages"):
            for msg in exc.messages:
                messages.error(request, msg)
        else:
            messages.error(request, str(exc))
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")
    AuditService.log_event(
        action="PUBLISH",
        portal="ADMIN",
        entity_type="GradingTemplate",
        entity_id=row.id,
        actor=request.user,
        before_data=before,
        after_data=model_before_after(row),
        request=request,
    )
    messages.success(request, f"Template {row.code} published successfully.")
    return _redirect_back_or_default(request, "admin_portal:grading_template_list")


@portal_required("ADMIN")
@permission_required("grading_templates.submit_for_approval")
def grading_template_submit_for_approval_view(request, template_id: int):
    row = get_object_or_404(AdminScopeService.scoped_grading_templates(request), id=template_id)
    if row.is_published:
        messages.error(request, "Published templates are already active. Use hotfix workflow for changes.")
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")

    form = GradingTemplateApprovalSubmitForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        before = model_before_after(row)
        try:
            GradingTemplateService.submit_for_approval(
                template=row,
                actor=request.user,
                remarks=form.cleaned_data.get("remarks"),
            )
        except ValidationError as exc:
            if hasattr(exc, "messages"):
                for msg in exc.messages:
                    messages.error(request, msg)
            else:
                messages.error(request, str(exc))
            return _redirect_back_or_default(request, "admin_portal:grading_template_list")

        AuditService.log_event(
            action="SUBMIT",
            portal="ADMIN",
            entity_type="GradingTemplateApproval",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
            metadata={"workflow": "TEMPLATE_APPROVAL"},
        )
        messages.success(request, f"Template {row.code} submitted for approval.")
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")

    context = {"form": form, "title": f"Submit Template For Approval: {row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("grading_templates.approve")
def grading_template_review_approval_view(request, template_id: int):
    row = get_object_or_404(AdminScopeService.scoped_grading_templates(request), id=template_id)
    form = GradingTemplateApprovalReviewForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        before = model_before_after(row)
        decision = form.cleaned_data["decision"]
        approve = decision == GradingTemplateApprovalReviewForm.Decision.APPROVE
        try:
            GradingTemplateService.review_approval(
                template=row,
                actor=request.user,
                approve=approve,
                remarks=form.cleaned_data.get("remarks"),
            )
        except ValidationError as exc:
            if hasattr(exc, "messages"):
                for msg in exc.messages:
                    messages.error(request, msg)
            else:
                messages.error(request, str(exc))
            return _redirect_back_or_default(request, "admin_portal:grading_template_list")

        AuditService.log_event(
            action="APPROVE" if approve else "REJECT",
            portal="ADMIN",
            entity_type="GradingTemplateApproval",
            entity_id=row.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
            metadata={"workflow": "TEMPLATE_APPROVAL"},
        )
        messages.success(
            request,
            f"Template {row.code} {'approved' if approve else 'rejected'} successfully.",
        )
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")

    context = {
        "form": form,
        "title": f"Review Template Approval: {row.code}",
        "template_obj": row,
        "involved_personalities": TemplateHotfixService.involved_personalities(),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/template_approval_review.html", context)


@portal_required("ADMIN")
@permission_required("template_hotfixes.read")
def template_hotfix_list_view(request):
    queryset = AdminScopeService.scoped_template_hotfix_requests(request)
    if request.GET.get("template_id"):
        queryset = queryset.filter(template_id=request.GET.get("template_id"))
    if request.GET.get("status"):
        queryset = queryset.filter(status=request.GET.get("status"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(template__code__icontains=q)
            | Q(template__name__icontains=q)
            | Q(requested_by_user__username__icontains=q)
        )
    context = {
        "page_obj": _get_page(request, queryset),
        "templates": AdminScopeService.scoped_grading_templates(request),
        "q": q,
        "status": request.GET.get("status", ""),
        "involved_personalities": TemplateHotfixService.involved_personalities(),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/template_hotfix_list.html", context)


@portal_required("ADMIN")
@permission_required("template_hotfixes.create")
def template_hotfix_create_view(request, template_id: int):
    template = get_object_or_404(AdminScopeService.scoped_grading_templates(request), id=template_id)
    if not template.is_published:
        messages.error(request, "Only published templates can use the hotfix workflow.")
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")

    scoped_offerings = AdminScopeService.scoped_course_offerings(request).filter(
        tenant_id=template.tenant_id,
        status=CourseOffering.Status.OPEN,
        is_active=True,
    )
    form = TemplateHotfixRequestForm(request.POST or None, offering_queryset=scoped_offerings)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        selected_offerings = [row.id for row in form.cleaned_data.get("selected_offerings", [])]
        try:
            hotfix = TemplateHotfixService.create_request(
                template=template,
                requested_by=request.user,
                apply_mode=form.cleaned_data["apply_mode"],
                justification=form.cleaned_data["justification"],
                selected_offering_ids=selected_offerings,
            )
        except ValidationError as exc:
            if hasattr(exc, "messages"):
                for msg in exc.messages:
                    messages.error(request, msg)
            else:
                messages.error(request, str(exc))
            return _redirect_back_or_default(request, "admin_portal:grading_template_list")

        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="TemplateHotfixRequest",
            entity_id=hotfix.id,
            actor=request.user,
            tenant=template.tenant,
            after_data=model_before_after(hotfix),
            request=request,
            metadata={"workflow": "TEMPLATE_HOTFIX"},
        )
        messages.success(request, f"Hotfix request created for template {template.code}.")
        return _redirect_back_or_default(request, "admin_portal:template_hotfix_list")

    context = {"form": form, "title": f"Create Hotfix Request: {template.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("template_hotfixes.review")
def template_hotfix_review_view(request, hotfix_id: int):
    hotfix = get_object_or_404(AdminScopeService.scoped_template_hotfix_requests(request), id=hotfix_id)
    form = TemplateHotfixReviewForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        before = model_before_after(hotfix)
        approve = form.cleaned_data["decision"] == TemplateHotfixReviewForm.Decision.APPROVE
        try:
            TemplateHotfixService.review_and_apply(
                hotfix_request=hotfix,
                reviewer=request.user,
                approve=approve,
                review_remarks=form.cleaned_data.get("review_remarks"),
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
            return _redirect_back_or_default(request, "admin_portal:template_hotfix_list")

        AuditService.log_event(
            action="APPROVE" if approve else "REJECT",
            portal="ADMIN",
            entity_type="TemplateHotfixRequest",
            entity_id=hotfix.id,
            actor=request.user,
            before_data=before,
            after_data=model_before_after(hotfix),
            request=request,
            metadata={
                "workflow": "TEMPLATE_HOTFIX",
                "apply_mode": hotfix.apply_mode,
                "status": hotfix.status,
            },
        )
        messages.success(
            request,
            f"Hotfix request #{hotfix.id} {'approved and applied' if approve else 'rejected'}.",
        )
        return _redirect_back_or_default(request, "admin_portal:template_hotfix_list")

    context = {
        "form": form,
        "title": f"Review Hotfix Request #{hotfix.id} ({hotfix.template.code})",
        "hotfix": hotfix,
        "involved_personalities": TemplateHotfixService.involved_personalities(),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/template_hotfix_review.html", context)


@portal_required("ADMIN")
@permission_required("template_periods.read")
def template_period_list_view(request):
    queryset = AdminScopeService.scoped_template_periods(request)
    selected_template_id = request.GET.get("template_id", "").strip()
    selected_template_id_int = _safe_int(selected_template_id)
    if selected_template_id_int:
        queryset = queryset.filter(template_id=selected_template_id_int)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(template__name__icontains=q))
    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "selected_template_id": selected_template_id,
    }
    context.update(_scope_context(request))
    context["templates"] = AdminScopeService.scoped_grading_templates(request)
    return render(request, "admin_portal/grading/period_list.html", context)


@portal_required("ADMIN")
@permission_required("template_periods.create")
def template_period_create_view(request):
    template_qs = AdminScopeService.scoped_grading_templates(request)
    requested_template_id = _safe_int(request.GET.get("template_id"))
    selected_template = None
    if requested_template_id:
        selected_template = template_qs.filter(id=requested_template_id).first()
        if selected_template:
            template_qs = template_qs.filter(id=selected_template.id)
    form = GradingTemplatePeriodForm(request.POST or None, template_queryset=template_qs)
    if selected_template and request.method == "GET":
        form.fields["template"].initial = selected_template.id
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        parent_template = form.cleaned_data["template"]
        locked_response = _ensure_template_editable_or_forbidden(request, parent_template)
        if locked_response:
            return locked_response
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="GradingTemplatePeriod",
            entity_id=row.id,
            actor=request.user,
            tenant=row.template.tenant,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Template period created.")
        return redirect(f"{reverse('admin_portal:template_period_list')}?template_id={row.template_id}")
    context = {
        "form": form,
        "title": f"Create Template Period{' - ' + (selected_template.name or selected_template.code) if selected_template else ''}",
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("template_periods.update")
def template_period_update_view(request, period_id: int):
    row = get_object_or_404(AdminScopeService.scoped_template_periods(request), id=period_id)
    locked_response = _ensure_template_editable_or_forbidden(request, row.template)
    if locked_response:
        return locked_response
    before = model_before_after(row)
    form = GradingTemplatePeriodForm(
        request.POST or None,
        instance=row,
        template_queryset=AdminScopeService.scoped_grading_templates(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        selected_template = form.cleaned_data["template"]
        locked_response = _ensure_template_editable_or_forbidden(request, selected_template)
        if locked_response:
            return locked_response
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="GradingTemplatePeriod",
            entity_id=row.id,
            actor=request.user,
            tenant=row.template.tenant,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Template period updated.")
        return _redirect_back_or_default(request, "admin_portal:template_period_list")
    context = {"form": form, "title": f"Edit Template Period: {row.name or row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("template_components.read")
def template_component_list_view(request):
    queryset = AdminScopeService.scoped_template_components(request)
    selected_template_id = _safe_int(request.GET.get("template_id"))
    selected_period_id = _safe_int(request.GET.get("period_id"))
    if selected_template_id:
        queryset = queryset.filter(template_period__template_id=selected_template_id)
    if selected_period_id:
        queryset = queryset.filter(template_period_id=selected_period_id)
    status = request.GET.get("status", "active")
    if status == "active":
        queryset = queryset.filter(is_active=True)
    elif status == "inactive":
        queryset = queryset.filter(is_active=False)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(code__icontains=q)
            | Q(name__icontains=q)
            | Q(template_period__code__icontains=q)
            | Q(template_period__template__name__icontains=q)
        )
    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "status": status,
        "selected_template_id": str(selected_template_id or ""),
        "selected_period_id": str(selected_period_id or ""),
    }
    context.update(_scope_context(request))
    period_qs = AdminScopeService.scoped_template_periods(request)
    if selected_template_id:
        period_qs = period_qs.filter(template_id=selected_template_id)
    context["periods"] = period_qs
    context["templates"] = AdminScopeService.scoped_grading_templates(request)
    return render(request, "admin_portal/grading/component_list.html", context)


@portal_required("ADMIN")
@permission_required("template_components.create")
def template_component_create_view(request):
    period_qs = AdminScopeService.scoped_template_periods(request)
    requested_template_id = _safe_int(request.GET.get("template_id"))
    requested_period_id = _safe_int(request.GET.get("period_id"))
    selected_period = None
    if requested_template_id:
        period_qs = period_qs.filter(template_id=requested_template_id)
    if requested_period_id:
        selected_period = period_qs.filter(id=requested_period_id).first()
        if selected_period:
            period_qs = period_qs.filter(id=selected_period.id)
    form = GradingTemplateComponentForm(request.POST or None, period_queryset=period_qs)
    if selected_period and request.method == "GET":
        form.fields["template_period"].initial = selected_period.id
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        parent_template = form.cleaned_data["template_period"].template
        locked_response = _ensure_template_editable_or_forbidden(request, parent_template)
        if locked_response:
            return locked_response
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="GradingTemplateComponent",
            entity_id=row.id,
            actor=request.user,
            tenant=parent_template.tenant,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Template component created.")
        return redirect(
            f"{reverse('admin_portal:template_component_list')}?template_id={row.template_period.template_id}&period_id={row.template_period_id}"
        )
    title_suffix = ""
    if selected_period:
        title_suffix = f" - {(selected_period.template.name or selected_period.template.code)} / {(selected_period.name or selected_period.code)}"
    context = {"form": form, "title": f"Create Template Component{title_suffix}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("template_components.update")
def template_component_update_view(request, component_id: int):
    row = get_object_or_404(AdminScopeService.scoped_template_components(request), id=component_id)
    locked_response = _ensure_template_editable_or_forbidden(request, row.template_period.template)
    if locked_response:
        return locked_response
    before = model_before_after(row)
    form = GradingTemplateComponentForm(
        request.POST or None,
        instance=row,
        period_queryset=AdminScopeService.scoped_template_periods(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        selected_template = form.cleaned_data["template_period"].template
        locked_response = _ensure_template_editable_or_forbidden(request, selected_template)
        if locked_response:
            return locked_response
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="GradingTemplateComponent",
            entity_id=row.id,
            actor=request.user,
            tenant=selected_template.tenant,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Template component updated.")
        return _redirect_back_or_default(request, "admin_portal:template_component_list")
    context = {"form": form, "title": f"Edit Template Component: {row.name or row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("template_components.update")
def template_component_delete_view(request, component_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid method.")
    row = get_object_or_404(AdminScopeService.scoped_template_components(request), id=component_id)
    locked_response = _ensure_template_editable_or_forbidden(request, row.template_period.template)
    if locked_response:
        return locked_response

    before = model_before_after(row)
    if not row.is_active:
        messages.info(request, f"Component {row.name or row.code} is already inactive.")
        return _redirect_back_or_default(request, "admin_portal:template_component_list")

    row.is_active = False
    row.save(update_fields=["is_active", "updated_at"])
    row.subcomponents.filter(is_active=True).update(is_active=False, updated_at=timezone.now())
    GradingTemplateDetail.objects.filter(
        template_subcomponent__template_component=row,
        is_active=True,
    ).update(is_active=False, updated_at=timezone.now())

    AuditService.log_event(
        action="DELETE",
        portal="ADMIN",
        entity_type="GradingTemplateComponent",
        entity_id=row.id,
        actor=request.user,
        tenant=row.template_period.template.tenant,
        before_data=before,
        after_data=model_before_after(row),
        metadata={
            "delete_mode": "SOFT_DELETE",
            "deactivated_subcomponents": row.subcomponents.count(),
        },
        request=request,
    )
    messages.success(request, f"Component {row.name or row.code} deleted (soft delete).")
    return _redirect_back_or_default(request, "admin_portal:template_component_list")


@portal_required("ADMIN")
@permission_required("template_subcomponents.read")
def template_subcomponent_list_view(request):
    queryset = AdminScopeService.scoped_template_subcomponents(request)
    selected_component_id = _safe_int(request.GET.get("component_id"))
    selected_period_id = _safe_int(request.GET.get("period_id"))
    selected_template_id = _safe_int(request.GET.get("template_id"))
    if selected_template_id:
        queryset = queryset.filter(template_component__template_period__template_id=selected_template_id)
    if selected_period_id:
        queryset = queryset.filter(template_component__template_period_id=selected_period_id)
    if selected_component_id:
        queryset = queryset.filter(template_component_id=selected_component_id)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(code__icontains=q)
            | Q(name__icontains=q)
            | Q(template_component__code__icontains=q)
            | Q(template_component__template_period__template__name__icontains=q)
        )
    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "selected_component_id": str(selected_component_id or ""),
        "selected_period_id": str(selected_period_id or ""),
        "selected_template_id": str(selected_template_id or ""),
    }
    context.update(_scope_context(request))
    component_qs = AdminScopeService.scoped_template_components(request)
    if selected_template_id:
        component_qs = component_qs.filter(template_period__template_id=selected_template_id)
    if selected_period_id:
        component_qs = component_qs.filter(template_period_id=selected_period_id)
    context["components"] = component_qs
    return render(request, "admin_portal/grading/subcomponent_list.html", context)


@portal_required("ADMIN")
@permission_required("template_subcomponents.create")
def template_subcomponent_create_view(request):
    component_qs = AdminScopeService.scoped_template_components(request)
    requested_component_id = _safe_int(request.GET.get("component_id"))
    requested_period_id = _safe_int(request.GET.get("period_id"))
    requested_template_id = _safe_int(request.GET.get("template_id"))
    if requested_template_id:
        component_qs = component_qs.filter(template_period__template_id=requested_template_id)
    if requested_period_id:
        component_qs = component_qs.filter(template_period_id=requested_period_id)
    selected_component = None
    if requested_component_id:
        selected_component = component_qs.filter(id=requested_component_id).first()
        if selected_component:
            component_qs = component_qs.filter(id=selected_component.id)
    form = GradingTemplateSubcomponentForm(request.POST or None, component_queryset=component_qs)
    if selected_component and request.method == "GET":
        form.fields["template_component"].initial = selected_component.id
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        parent_template = form.cleaned_data["template_component"].template_period.template
        locked_response = _ensure_template_editable_or_forbidden(request, parent_template)
        if locked_response:
            return locked_response
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="GradingTemplateSubcomponent",
            entity_id=row.id,
            actor=request.user,
            tenant=parent_template.tenant,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Template subcomponent created.")
        return redirect(
            f"{reverse('admin_portal:template_subcomponent_list')}?template_id={row.template_component.template_period.template_id}&period_id={row.template_component.template_period_id}&component_id={row.template_component_id}"
        )
    title_suffix = ""
    if selected_component:
        title_suffix = (
            f" - {(selected_component.template_period.template.name or selected_component.template_period.template.code)}"
            f" / {(selected_component.template_period.name or selected_component.template_period.code)}"
            f" / {(selected_component.name or selected_component.code)}"
        )
    context = {"form": form, "title": f"Create Template Subcomponent{title_suffix}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("template_subcomponents.update")
def template_subcomponent_update_view(request, subcomponent_id: int):
    row = get_object_or_404(AdminScopeService.scoped_template_subcomponents(request), id=subcomponent_id)
    locked_response = _ensure_template_editable_or_forbidden(request, row.template_component.template_period.template)
    if locked_response:
        return locked_response
    before = model_before_after(row)
    form = GradingTemplateSubcomponentForm(
        request.POST or None,
        instance=row,
        component_queryset=AdminScopeService.scoped_template_components(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        selected_template = form.cleaned_data["template_component"].template_period.template
        locked_response = _ensure_template_editable_or_forbidden(request, selected_template)
        if locked_response:
            return locked_response
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="GradingTemplateSubcomponent",
            entity_id=row.id,
            actor=request.user,
            tenant=selected_template.tenant,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Template subcomponent updated.")
        return _redirect_back_or_default(request, "admin_portal:template_subcomponent_list")
    context = {"form": form, "title": f"Edit Template Subcomponent: {row.name or row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("template_details.read")
def template_detail_list_view(request):
    queryset = AdminScopeService.scoped_template_details(request)
    selected_subcomponent_id = _safe_int(request.GET.get("subcomponent_id"))
    selected_component_id = _safe_int(request.GET.get("component_id"))
    selected_period_id = _safe_int(request.GET.get("period_id"))
    selected_template_id = _safe_int(request.GET.get("template_id"))
    if selected_template_id:
        queryset = queryset.filter(template_subcomponent__template_component__template_period__template_id=selected_template_id)
    if selected_period_id:
        queryset = queryset.filter(template_subcomponent__template_component__template_period_id=selected_period_id)
    if selected_component_id:
        queryset = queryset.filter(template_subcomponent__template_component_id=selected_component_id)
    if selected_subcomponent_id:
        queryset = queryset.filter(template_subcomponent_id=selected_subcomponent_id)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(code__icontains=q)
            | Q(name__icontains=q)
            | Q(template_subcomponent__code__icontains=q)
            | Q(template_subcomponent__template_component__code__icontains=q)
            | Q(template_subcomponent__template_component__template_period__template__name__icontains=q)
        )
    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "selected_subcomponent_id": str(selected_subcomponent_id or ""),
        "selected_component_id": str(selected_component_id or ""),
        "selected_period_id": str(selected_period_id or ""),
        "selected_template_id": str(selected_template_id or ""),
    }
    context.update(_scope_context(request))
    subcomponent_qs = AdminScopeService.scoped_template_subcomponents(request)
    if selected_template_id:
        subcomponent_qs = subcomponent_qs.filter(template_component__template_period__template_id=selected_template_id)
    if selected_period_id:
        subcomponent_qs = subcomponent_qs.filter(template_component__template_period_id=selected_period_id)
    if selected_component_id:
        subcomponent_qs = subcomponent_qs.filter(template_component_id=selected_component_id)
    context["subcomponents"] = subcomponent_qs
    return render(request, "admin_portal/grading/detail_list.html", context)


@portal_required("ADMIN")
@permission_required("template_details.create")
def template_detail_create_view(request):
    subcomponent_qs = AdminScopeService.scoped_template_subcomponents(request)
    requested_subcomponent_id = _safe_int(request.GET.get("subcomponent_id"))
    requested_component_id = _safe_int(request.GET.get("component_id"))
    requested_period_id = _safe_int(request.GET.get("period_id"))
    requested_template_id = _safe_int(request.GET.get("template_id"))
    if requested_template_id:
        subcomponent_qs = subcomponent_qs.filter(template_component__template_period__template_id=requested_template_id)
    if requested_period_id:
        subcomponent_qs = subcomponent_qs.filter(template_component__template_period_id=requested_period_id)
    if requested_component_id:
        subcomponent_qs = subcomponent_qs.filter(template_component_id=requested_component_id)
    selected_subcomponent = None
    if requested_subcomponent_id:
        selected_subcomponent = subcomponent_qs.filter(id=requested_subcomponent_id).first()
        if selected_subcomponent:
            subcomponent_qs = subcomponent_qs.filter(id=selected_subcomponent.id)
    form = GradingTemplateDetailForm(request.POST or None, subcomponent_queryset=subcomponent_qs)
    if selected_subcomponent and request.method == "GET":
        form.fields["template_subcomponent"].initial = selected_subcomponent.id
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        parent_template = form.cleaned_data["template_subcomponent"].template_component.template_period.template
        locked_response = _ensure_template_editable_or_forbidden(request, parent_template)
        if locked_response:
            return locked_response
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="GradingTemplateDetail",
            entity_id=row.id,
            actor=request.user,
            tenant=parent_template.tenant,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Template detail created.")
        return redirect(
            f"{reverse('admin_portal:template_detail_list')}?template_id={row.template_subcomponent.template_component.template_period.template_id}&period_id={row.template_subcomponent.template_component.template_period_id}&component_id={row.template_subcomponent.template_component_id}&subcomponent_id={row.template_subcomponent_id}"
        )
    title_suffix = ""
    if selected_subcomponent:
        comp = selected_subcomponent.template_component
        period = comp.template_period
        title_suffix = (
            f" - {(period.template.name or period.template.code)}"
            f" / {(period.name or period.code)}"
            f" / {(comp.name or comp.code)}"
            f" / {(selected_subcomponent.name or selected_subcomponent.code)}"
        )
    context = {"form": form, "title": f"Create Template Detail{title_suffix}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("template_details.update")
def template_detail_update_view(request, detail_id: int):
    row = get_object_or_404(AdminScopeService.scoped_template_details(request), id=detail_id)
    locked_response = _ensure_template_editable_or_forbidden(
        request,
        row.template_subcomponent.template_component.template_period.template,
    )
    if locked_response:
        return locked_response
    before = model_before_after(row)
    form = GradingTemplateDetailForm(
        request.POST or None,
        instance=row,
        subcomponent_queryset=AdminScopeService.scoped_template_subcomponents(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        selected_template = form.cleaned_data["template_subcomponent"].template_component.template_period.template
        locked_response = _ensure_template_editable_or_forbidden(request, selected_template)
        if locked_response:
            return locked_response
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="GradingTemplateDetail",
            entity_id=row.id,
            actor=request.user,
            tenant=selected_template.tenant,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Template detail updated.")
        return _redirect_back_or_default(request, "admin_portal:template_detail_list")
    context = {"form": form, "title": f"Edit Template Detail: {row.name or row.code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("tenant_grading_profiles.read")
def tenant_grading_profile_list_view(request):
    queryset = AdminScopeService.scoped_tenant_grading_profiles(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("campus_id"):
        queryset = queryset.filter(campus_id=request.GET.get("campus_id"))
    if request.GET.get("active") in {"1", "0"}:
        queryset = queryset.filter(is_active=request.GET.get("active") == "1")
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(profile_code__icontains=q)
            | Q(profile_name__icontains=q)
            | Q(course__code__icontains=q)
            | Q(course_type__icontains=q)
            | Q(grading_template__code__icontains=q)
            | Q(grading_template__name__icontains=q)
        )
    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "active": request.GET.get("active", ""),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/tenant_grading_profile_list.html", context)


@portal_required("ADMIN")
@permission_required("tenant_grading_profiles.create")
def tenant_grading_profile_create_view(request):
    form = TenantGradingProfileForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
        program_queryset=AdminScopeService.scoped_programs(request),
        course_queryset=AdminScopeService.scoped_courses(request),
        template_queryset=AdminScopeService.scoped_grading_templates(request).filter(is_published=True, is_active=True),
        term_queryset=AdminScopeService.scoped_terms(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="TenantGradingProfile",
            entity_id=row.id,
            actor=request.user,
            tenant=row.tenant,
            campus=row.campus,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Tenant grading profile created.")
        return _redirect_back_or_default(request, "admin_portal:tenant_grading_profile_list")
    context = {"form": form, "title": "Create Tenant Grading Profile"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("tenant_grading_profiles.update")
def tenant_grading_profile_update_view(request, profile_id: int):
    row = get_object_or_404(AdminScopeService.scoped_tenant_grading_profiles(request), id=profile_id)
    before = model_before_after(row)
    template_queryset = AdminScopeService.scoped_grading_templates(request).filter(
        Q(is_published=True, is_active=True) | Q(id=row.grading_template_id)
    )
    form = TenantGradingProfileForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.scoped_departments(request),
        program_queryset=AdminScopeService.scoped_programs(request),
        course_queryset=AdminScopeService.scoped_courses(request),
        template_queryset=template_queryset,
        term_queryset=AdminScopeService.scoped_terms(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="TenantGradingProfile",
            entity_id=row.id,
            actor=request.user,
            tenant=row.tenant,
            campus=row.campus,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Tenant grading profile updated.")
        return _redirect_back_or_default(request, "admin_portal:tenant_grading_profile_list")
    context = {"form": form, "title": f"Edit Tenant Grading Profile: {row.profile_code}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("course_template_assignments.read")
def course_template_assignment_list_view(request):
    queryset = AdminScopeService.scoped_course_template_assignments(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(course__tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("course_id"):
        queryset = queryset.filter(course_id=request.GET.get("course_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(course__code__icontains=q)
            | Q(grading_template__code__icontains=q)
            | Q(grading_template__name__icontains=q)
        )
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    context["courses"] = AdminScopeService.scoped_courses(request)
    return render(request, "admin_portal/grading/course_template_assignment_list.html", context)


@portal_required("ADMIN")
@permission_required("course_template_assignments.create")
def course_template_assignment_create_view(request):
    form = CourseTemplateAssignmentForm(
        request.POST or None,
        course_queryset=AdminScopeService.scoped_courses(request),
        template_queryset=AdminScopeService.scoped_grading_templates(request).filter(is_published=True, is_active=True),
        term_queryset=AdminScopeService.scoped_terms(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="CourseTemplateAssignment",
            entity_id=row.id,
            actor=request.user,
            tenant=row.course.tenant,
            campus=row.course.campus,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Course template assignment created.")
        return _redirect_back_or_default(request, "admin_portal:course_template_assignment_list")
    context = {"form": form, "title": "Create Course Template Assignment"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("course_template_assignments.update")
def course_template_assignment_update_view(request, assignment_id: int):
    row = get_object_or_404(AdminScopeService.scoped_course_template_assignments(request), id=assignment_id)
    before = model_before_after(row)
    template_queryset = AdminScopeService.scoped_grading_templates(request).filter(
        Q(is_published=True, is_active=True) | Q(id=row.grading_template_id)
    )
    form = CourseTemplateAssignmentForm(
        request.POST or None,
        instance=row,
        course_queryset=AdminScopeService.scoped_courses(request),
        template_queryset=template_queryset,
        term_queryset=AdminScopeService.scoped_terms(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="CourseTemplateAssignment",
            entity_id=row.id,
            actor=request.user,
            tenant=row.course.tenant,
            campus=row.course.campus,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Course template assignment updated.")
        return _redirect_back_or_default(request, "admin_portal:course_template_assignment_list")
    context = {"form": form, "title": f"Edit Course Template Assignment #{row.id}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("course_base_overrides.read")
def course_base_override_list_view(request):
    queryset = AdminScopeService.scoped_course_base_value_overrides(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(course__tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("course_id"):
        queryset = queryset.filter(course_id=request.GET.get("course_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(course__code__icontains=q))
    context = {"page_obj": _get_page(request, queryset), "q": q}
    context.update(_scope_context(request))
    context["courses"] = AdminScopeService.scoped_courses(request)
    return render(request, "admin_portal/grading/course_base_override_list.html", context)


@portal_required("ADMIN")
@permission_required("course_base_overrides.create")
def course_base_override_create_view(request):
    form = CourseBaseValueOverrideForm(
        request.POST or None,
        course_queryset=AdminScopeService.scoped_courses(request),
        term_queryset=AdminScopeService.scoped_terms(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="CourseBaseValueOverride",
            entity_id=row.id,
            actor=request.user,
            tenant=row.course.tenant,
            campus=row.course.campus,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Course base value override created.")
        return _redirect_back_or_default(request, "admin_portal:course_base_override_list")
    context = {"form": form, "title": "Create Course Base Value Override"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("course_base_overrides.update")
def course_base_override_update_view(request, override_id: int):
    row = get_object_or_404(AdminScopeService.scoped_course_base_value_overrides(request), id=override_id)
    before = model_before_after(row)
    form = CourseBaseValueOverrideForm(
        request.POST or None,
        instance=row,
        course_queryset=AdminScopeService.scoped_courses(request),
        term_queryset=AdminScopeService.scoped_terms(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save()
        AuditService.log_event(
            action="UPDATE",
            portal="ADMIN",
            entity_type="CourseBaseValueOverride",
            entity_id=row.id,
            actor=request.user,
            tenant=row.course.tenant,
            campus=row.course.campus,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Course base value override updated.")
        return _redirect_back_or_default(request, "admin_portal:course_base_override_list")
    context = {"form": form, "title": f"Edit Course Base Override #{row.id}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("grading_periods.read")
def grading_period_lock_list_view(request):
    queryset = AdminScopeService.scoped_grading_period_locks(request)
    if request.GET.get("campus_id"):
        queryset = queryset.filter(campus_id=request.GET.get("campus_id"))
    if request.GET.get("term_id"):
        queryset = queryset.filter(term_id=request.GET.get("term_id"))
    if request.GET.get("scope_type"):
        queryset = queryset.filter(scope_type=request.GET.get("scope_type"))
    if request.GET.get("is_locked") in {"1", "0"}:
        queryset = queryset.filter(is_locked=request.GET.get("is_locked") == "1")
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(period_code__icontains=q)
            | Q(course_offering__course__code__icontains=q)
            | Q(course_offering__section__code__icontains=q)
            | Q(remarks__icontains=q)
        )

    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "terms": AdminScopeService.scoped_terms(request),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/period_lock_list.html", context)


@portal_required("ADMIN")
@permission_required("grading_periods.lock")
def grading_period_lock_create_view(request):
    form = GradingPeriodLockForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        academic_year_queryset=AdminScopeService.scoped_academic_years(request),
        term_queryset=AdminScopeService.scoped_terms(request),
        offering_queryset=AdminScopeService.scoped_course_offerings(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        if row.is_locked:
            row.locked_by_user = request.user
            row.locked_at = timezone.now()
        row.save()
        AuditService.log_event(
            action="LOCK" if row.is_locked else "UPDATE",
            portal="ADMIN",
            entity_type="GradingPeriodLock",
            entity_id=row.id,
            actor=request.user,
            tenant=row.tenant,
            campus=row.campus,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Period lock configuration saved.")
        return _redirect_back_or_default(request, "admin_portal:grading_period_lock_list")
    context = {"form": form, "title": "Create Period Lock"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("grading_periods.lock")
def grading_period_lock_update_view(request, lock_id: int):
    row = get_object_or_404(AdminScopeService.scoped_grading_period_locks(request), id=lock_id)
    before = model_before_after(row)
    form = GradingPeriodLockForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        academic_year_queryset=AdminScopeService.scoped_academic_years(request),
        term_queryset=AdminScopeService.scoped_terms(request),
        offering_queryset=AdminScopeService.scoped_course_offerings(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        if row.is_locked and not before.get("is_locked"):
            row.locked_by_user = request.user
            row.locked_at = timezone.now()
        row.save()
        AuditService.log_event(
            action="LOCK" if row.is_locked else "UPDATE",
            portal="ADMIN",
            entity_type="GradingPeriodLock",
            entity_id=row.id,
            actor=request.user,
            tenant=row.tenant,
            campus=row.campus,
            before_data=before,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Period lock updated.")
        return _redirect_back_or_default(request, "admin_portal:grading_period_lock_list")
    context = {"form": form, "title": f"Edit Period Lock #{row.id}"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("grading_periods.lock")
def grading_period_lock_close_view(request, lock_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid method.")
    row = get_object_or_404(AdminScopeService.scoped_grading_period_locks(request), id=lock_id)
    before = model_before_after(row)
    row.is_locked = True
    row.locked_by_user = request.user
    row.locked_at = timezone.now()
    row.save(update_fields=["is_locked", "locked_by_user", "locked_at", "updated_at"])
    AuditService.log_event(
        action="LOCK",
        portal="ADMIN",
        entity_type="GradingPeriodLock",
        entity_id=row.id,
        actor=request.user,
        tenant=row.tenant,
        campus=row.campus,
        before_data=before,
        after_data=model_before_after(row),
        request=request,
    )
    messages.success(request, "Period locked.")
    return _redirect_back_or_default(request, "admin_portal:grading_period_lock_list")


@portal_required("ADMIN")
@permission_required("grading_periods.reopen")
def grading_period_lock_reopen_view(request, lock_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid method.")
    row = get_object_or_404(AdminScopeService.scoped_grading_period_locks(request), id=lock_id)
    before = model_before_after(row)
    row.is_locked = False
    row.reopened_by_user = request.user
    row.reopened_at = timezone.now()
    row.save(update_fields=["is_locked", "reopened_by_user", "reopened_at", "updated_at"])
    AuditService.log_event(
        action="REOPEN",
        portal="ADMIN",
        entity_type="GradingPeriodLock",
        entity_id=row.id,
        actor=request.user,
        tenant=row.tenant,
        campus=row.campus,
        before_data=before,
        after_data=model_before_after(row),
        request=request,
    )
    messages.success(request, "Period reopened.")
    return _redirect_back_or_default(request, "admin_portal:grading_period_lock_list")


@portal_required("ADMIN")
@permission_required("grade_submissions.read")
def overdue_unsubmitted_report_view(request):
    now = timezone.now()
    current_tenant_id = getattr(request, "scope", {}).get("tenant_id")
    current_campus_id = getattr(request, "scope", {}).get("campus_id")

    locks_qs = AdminScopeService.scoped_grading_period_locks(request).filter(
        is_active=True,
        deadline_at__isnull=False,
        deadline_at__lt=now,
    )
    if request.GET.get("campus_id"):
        locks_qs = locks_qs.filter(campus_id=request.GET.get("campus_id"))
    if request.GET.get("academic_year_id"):
        locks_qs = locks_qs.filter(academic_year_id=request.GET.get("academic_year_id"))
    if request.GET.get("term_id"):
        locks_qs = locks_qs.filter(term_id=request.GET.get("term_id"))
    if request.GET.get("period_code"):
        locks_qs = locks_qs.filter(period_code__iexact=request.GET.get("period_code"))

    overdue_locks = list(locks_qs)
    offerings_by_scope = {}
    lock_targets = {}

    def _pick_lock(previous, new_lock):
        if previous is None:
            return new_lock
        if (
            previous.scope_type == GradingPeriodLock.ScopeType.CAMPUS
            and new_lock.scope_type == GradingPeriodLock.ScopeType.COURSE
        ):
            return new_lock
        if previous.scope_type == new_lock.scope_type and new_lock.updated_at > previous.updated_at:
            return new_lock
        return previous

    for lock in overdue_locks:
        period_key = GradingGovernanceService._normalize_period_key(lock.period_code)
        if lock.scope_type == GradingPeriodLock.ScopeType.COURSE and lock.course_offering_id:
            target_key = (lock.course_offering_id, period_key)
            lock_targets[target_key] = _pick_lock(lock_targets.get(target_key), lock)
            continue

        scope_key = (
            lock.tenant_id,
            lock.campus_id,
            lock.academic_year_id,
            lock.term_id,
        )
        if scope_key not in offerings_by_scope:
            offerings_by_scope[scope_key] = list(
                AdminScopeService.scoped_course_offerings(request).filter(
                    tenant_id=lock.tenant_id,
                    campus_id=lock.campus_id,
                    academic_year_id=lock.academic_year_id,
                    term_id=lock.term_id,
                    is_active=True,
                )
            )
        for offering in offerings_by_scope[scope_key]:
            target_key = (offering.id, period_key)
            lock_targets[target_key] = _pick_lock(lock_targets.get(target_key), lock)

    if lock_targets:
        offering_ids = {offering_id for offering_id, _ in lock_targets.keys()}
        offerings = list(
            AdminScopeService.scoped_course_offerings(request)
            .filter(id__in=offering_ids)
            .select_related("tenant", "campus", "academic_year", "term", "course", "section")
        )
        offerings_map = {row.id: row for row in offerings}
    else:
        offerings_map = {}

    assignment_rows = (
        FacultyAssignment.objects.filter(
            offering_id__in=list(offerings_map.keys()),
            is_active=True,
            faculty_user__is_active=True,
        )
        .select_related("faculty_user")
        .order_by("offering_id", "-is_primary", "id")
    )
    offering_faculty = {}
    for assignment in assignment_rows:
        if assignment.offering_id not in offering_faculty:
            offering_faculty[assignment.offering_id] = assignment.faculty_user

    q = (request.GET.get("q") or "").strip().lower()
    report_rows = []
    for (offering_id, period_key), lock in lock_targets.items():
        offering = offerings_map.get(offering_id)
        if not offering:
            continue

        template_period = None
        submission = None
        submission_status = "NO_RECORD"
        skip_reason = None
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
            for period in template.periods.filter(is_active=True):
                if GradingGovernanceService._normalize_period_key(period.code) == period_key:
                    template_period = period
                    break
            if not template_period:
                skip_reason = "Template period not found"
                submission_status = "NO_PERIOD"
            else:
                submission = GradeSubmission.objects.filter(
                    offering_id=offering.id,
                    template_period_id=template_period.id,
                ).first()
                if submission:
                    submission_status = submission.status
        except ValidationError:
            skip_reason = "No template assignment"
            submission_status = "NO_TEMPLATE"

        if submission_status == GradeSubmission.Status.SUBMITTED:
            continue

        faculty_user = offering_faculty.get(offering.id)
        faculty_name = faculty_user.full_name if faculty_user else "-"
        row_text = " ".join(
            [
                offering.campus.code or "",
                offering.academic_year.code or "",
                offering.term.code or "",
                lock.period_code or "",
                offering.course.code or "",
                offering.course.title or "",
                offering.section.code or "",
                faculty_name or "",
                submission_status,
            ]
        ).lower()
        if q and q not in row_text:
            continue

        delta = now - lock.deadline_at if lock.deadline_at else None
        overdue_hours = int(delta.total_seconds() // 3600) if delta else 0
        report_rows.append(
            {
                "lock_id": lock.id,
                "tenant_code": offering.tenant.code,
                "campus_code": offering.campus.code,
                "academic_year_code": offering.academic_year.code,
                "term_code": offering.term.code,
                "period_code": lock.period_code,
                "course_code": offering.course.code,
                "course_title": offering.course.title,
                "section_code": offering.section.code,
                "faculty_name": faculty_name,
                "deadline_at": lock.deadline_at,
                "lock_state": "LOCKED" if lock.is_locked else "OPEN_OVERDUE",
                "submission_status": submission_status,
                "submitted_at": submission.submitted_at if submission else None,
                "skip_reason": skip_reason,
                "overdue_hours": overdue_hours,
                "offering_id": offering.id,
            }
        )

    report_rows.sort(
        key=lambda row: (
            row["deadline_at"] or now,
            row["campus_code"],
            row["course_code"],
            row["section_code"],
            row["period_code"],
        )
    )

    summary = {
        "total_overdue_unsubmitted": len(report_rows),
        "locked_unsubmitted": sum(1 for row in report_rows if row["lock_state"] == "LOCKED"),
        "open_overdue_unsubmitted": sum(1 for row in report_rows if row["lock_state"] == "OPEN_OVERDUE"),
    }
    context = {
        "page_obj": _get_page(request, report_rows, per_page=30),
        "summary": summary,
        "q": request.GET.get("q", ""),
        "period_code": request.GET.get("period_code", ""),
        "campus_options": AdminScopeService.scoped_campuses(request).order_by("code"),
        "academic_year_options": AdminScopeService.scoped_academic_years(request).order_by("-start_date"),
        "term_options": AdminScopeService.scoped_terms(request).order_by("-academic_year__start_date", "sequence_no"),
        "can_force_reopen": PermissionService.has_permission(
            request.user,
            "grade_submissions.reopen",
            tenant_id=current_tenant_id,
            campus_id=current_campus_id,
        ),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/overdue_unsubmitted_report.html", context)


@portal_required("ADMIN")
@permission_required("grade_submissions.read")
def grade_submission_list_view(request):
    queryset = AdminScopeService.scoped_grade_submissions(request)
    if request.GET.get("term_id"):
        queryset = queryset.filter(offering__term_id=request.GET.get("term_id"))
    if request.GET.get("status"):
        queryset = queryset.filter(status=request.GET.get("status"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(offering__course__code__icontains=q)
            | Q(offering__section__code__icontains=q)
            | Q(template_period__code__icontains=q)
        )
    now = timezone.now()
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    campus_id = getattr(request, "scope", {}).get("campus_id")
    can_force_reopen = PermissionService.has_permission(
        request.user,
        "grade_submissions.reopen",
        tenant_id=tenant_id,
        campus_id=campus_id,
    )
    can_revert_before_deadline = PermissionService.has_permission(
        request.user,
        "grade_submissions.revert_before_deadline",
        tenant_id=tenant_id,
        campus_id=campus_id,
    )
    can_create_reopen_request = PermissionService.has_permission(
        request.user,
        "reopen_requests.create",
        tenant_id=tenant_id,
        campus_id=campus_id,
    )
    page_obj = _get_page(request, queryset)
    for row in page_obj.object_list:
        lock = GradingGovernanceService.resolve_lock(
            offering=row.offering,
            template_period=row.template_period,
        )
        row.revert_deadline_at = lock.deadline_at if lock else None
        row.revert_deadline_passed = bool(row.revert_deadline_at and now > row.revert_deadline_at)
        row.pending_reopen_request = row.reopen_requests.filter(
            status=GradeSubmissionReopenRequest.Status.PENDING
        ).first()
        row.can_request_reopen = (
            can_create_reopen_request
            and row.status == GradeSubmission.Status.SUBMITTED
            and row.pending_reopen_request is None
        )

    context = {
        "page_obj": page_obj,
        "q": q,
        "status": request.GET.get("status", ""),
        "terms": AdminScopeService.scoped_terms(request),
        "can_create_reopen_request": can_create_reopen_request,
        "can_force_reopen": can_force_reopen,
        "can_revert_before_deadline": can_revert_before_deadline,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/submission_list.html", context)


@portal_required("ADMIN")
@permission_required("reopen_requests.create")
def grade_submission_reopen_view(request, submission_id: int):
    submission = get_object_or_404(AdminScopeService.scoped_grade_submissions(request), id=submission_id)
    if submission.status != GradeSubmission.Status.SUBMITTED:
        messages.error(request, "Only submitted grading periods can be reopened by request.")
        return _redirect_back_or_default(request, "admin_portal:grade_submission_list")
    if GradeSubmissionReopenRequest.objects.filter(
        submission=submission,
        status=GradeSubmissionReopenRequest.Status.PENDING,
    ).exists():
        messages.error(request, "A pending reopen request already exists for this submission.")
        return _redirect_back_or_default(request, "admin_portal:grade_submission_list")

    form = GradeSubmissionReopenRequestForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        reopen_request = GradingGovernanceService.create_reopen_request(
            user=request.user,
            submission=submission,
            justification=form.cleaned_data["justification"],
        )
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="GradeSubmissionReopenRequest",
            entity_id=reopen_request.id,
            actor=request.user,
            tenant=submission.tenant,
            campus=submission.campus,
            before_data=None,
            after_data=model_before_after(reopen_request),
            request=request,
        )
        messages.success(request, "Reopen request submitted for approval.")
        return _redirect_back_or_default(request, "admin_portal:grade_submission_list")
    context = {
        "form": form,
        "title": f"Request Reopen: {submission.offering.course.code} / {submission.template_period.code}",
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("reopen_requests.read")
def grade_submission_reopen_request_list_view(request):
    queryset = AdminScopeService.scoped_grade_submission_reopen_requests(request)
    if request.GET.get("term_id"):
        queryset = queryset.filter(offering__term_id=request.GET.get("term_id"))
    if request.GET.get("status"):
        queryset = queryset.filter(status=request.GET.get("status"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(offering__course__code__icontains=q)
            | Q(offering__section__code__icontains=q)
            | Q(requested_by_user__username__icontains=q)
            | Q(justification__icontains=q)
        )
    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "status": request.GET.get("status", ""),
        "terms": AdminScopeService.scoped_terms(request),
        "status_choices": GradeSubmissionReopenRequest.Status.choices,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/reopen_request_list.html", context)


@portal_required("ADMIN")
@permission_required("reopen_requests.review")
def grade_submission_reopen_request_review_view(request, request_id: int):
    reopen_request = get_object_or_404(
        AdminScopeService.scoped_grade_submission_reopen_requests(request),
        id=request_id,
    )
    form = GradeSubmissionReopenReviewForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        approved = form.cleaned_data["decision"] == GradeSubmissionReopenReviewForm.Decision.APPROVE
        has_force_reopen = PermissionService.has_permission(
            request.user,
            "grade_submissions.reopen",
            tenant_id=reopen_request.tenant_id,
            campus_id=reopen_request.campus_id,
        )
        has_revert_before_deadline = PermissionService.has_permission(
            request.user,
            "grade_submissions.revert_before_deadline",
            tenant_id=reopen_request.tenant_id,
            campus_id=reopen_request.campus_id,
        )
        if approved and not (has_force_reopen or has_revert_before_deadline):
            form.add_error(None, "You do not have permission to approve reopen requests.")
        else:
            lock = GradingGovernanceService.resolve_lock(
                offering=reopen_request.offering,
                template_period=reopen_request.template_period,
            )
            if approved and not has_force_reopen:
                if not lock or not lock.deadline_at:
                    form.add_error(
                        None,
                        "Cannot approve reopen request because no submission deadline is configured for this period scope.",
                    )
                elif timezone.now() > lock.deadline_at:
                    form.add_error(
                        None,
                        f"Cannot approve reopen request because the deadline passed on {lock.deadline_at:%Y-%m-%d %H:%M}.",
                    )
            if not form.non_field_errors():
                before_request = model_before_after(reopen_request)
                before_submission = model_before_after(reopen_request.submission)
                updated = GradingGovernanceService.review_reopen_request(
                    request_obj=reopen_request,
                    reviewer=request.user,
                    approved=approved,
                    review_remarks=form.cleaned_data.get("review_remarks"),
                )
                AuditService.log_event(
                    action="APPROVE" if approved else "REJECT",
                    portal="ADMIN",
                    entity_type="GradeSubmissionReopenRequest",
                    entity_id=updated.id,
                    actor=request.user,
                    tenant=updated.tenant,
                    campus=updated.campus,
                    before_data=before_request,
                    after_data=model_before_after(updated),
                    request=request,
                )
                if approved:
                    refreshed_submission = GradeSubmission.objects.get(id=updated.submission_id)
                    AuditService.log_event(
                        action="REOPEN",
                        portal="ADMIN",
                        entity_type="GradeSubmission",
                        entity_id=refreshed_submission.id,
                        actor=request.user,
                        tenant=refreshed_submission.tenant,
                        campus=refreshed_submission.campus,
                        before_data=before_submission,
                        after_data=model_before_after(refreshed_submission),
                        request=request,
                    )
                messages.success(
                    request,
                    "Reopen request approved and submission reopened."
                    if approved
                    else "Reopen request rejected.",
                )
                return _redirect_back_or_default(request, "admin_portal:grade_submission_reopen_request_list")
    context = {
        "title": f"Review Reopen Request #{reopen_request.id}",
        "form": form,
        "reopen_request": reopen_request,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/reopen_request_review.html", context)


@portal_required("ADMIN")
@permission_required("corrections.read")
def grade_correction_request_list_view(request):
    GradingGovernanceService.auto_lapse_expired_correction_windows()
    queryset = AdminScopeService.scoped_grade_correction_requests(request)
    if request.GET.get("term_id"):
        queryset = queryset.filter(offering__term_id=request.GET.get("term_id"))
    if request.GET.get("status"):
        queryset = queryset.filter(status=request.GET.get("status"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(offering__course__code__icontains=q)
            | Q(offering__section__code__icontains=q)
            | Q(requested_by_user__username__icontains=q)
            | Q(justification__icontains=q)
        )
    page_obj = _get_page(request, queryset)
    for row in page_obj.object_list:
        pending_step = GradingGovernanceService.get_pending_correction_step(request_obj=row)
        row.current_approval_step = pending_step
        row.current_approver_label = pending_step.approver_label if pending_step else None

    context = {
        "page_obj": page_obj,
        "q": q,
        "status": request.GET.get("status", ""),
        "terms": AdminScopeService.scoped_terms(request),
        "status_choices": GradeCorrectionRequest.Status.choices,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/correction_request_list.html", context)


@portal_required("ADMIN")
@permission_required("corrections.review")
def grade_correction_request_review_view(request, request_id: int):
    GradingGovernanceService.auto_lapse_expired_correction_windows()
    correction_request = get_object_or_404(
        AdminScopeService.scoped_grade_correction_requests(request),
        id=request_id,
    )
    GradingGovernanceService.reconcile_pending_correction_route(request_obj=correction_request)
    correction_request.refresh_from_db()
    pending_step = GradingGovernanceService.get_pending_correction_step(request_obj=correction_request)
    can_review, _, review_guard_message = GradingGovernanceService.can_user_review_correction_request(
        request_obj=correction_request,
        user=request.user,
    )
    is_final_step = GradingGovernanceService.is_final_correction_step(
        request_obj=correction_request,
        step=pending_step,
    )
    auto_apply_on_final_approval = GradingGovernanceService.is_auto_apply_score_correction_request(
        request_obj=correction_request
    )
    official_report_enabled = FeatureSettingsService.is_correction_official_report_enabled(
        tenant_id=correction_request.tenant_id
    )
    form = GradeCorrectionReviewForm(
        request.POST or None,
        require_window=False,
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        if not can_review:
            form.add_error(None, review_guard_message or "You are not allowed to review this correction request.")
            context = {
                "title": f"Review Correction Request #{correction_request.id}",
                "form": form,
                "correction_request": correction_request,
                "pending_step": pending_step,
                "is_final_step": is_final_step,
                "auto_apply_on_final_approval": auto_apply_on_final_approval,
                "official_report_enabled": official_report_enabled,
                "correction_window_hours": GradingGovernanceService.CORRECTION_WINDOW_HOURS,
            }
            context.update(_scope_context(request))
            return render(request, "admin_portal/grading/correction_request_review.html", context)

        before = model_before_after(correction_request)
        decision = form.cleaned_data["decision"]
        approved = decision == GradeCorrectionReviewForm.Decision.APPROVE

        try:
            updated = GradingGovernanceService.review_correction_request(
                request_obj=correction_request,
                reviewer=request.user,
                approved=approved,
                review_remarks=form.cleaned_data.get("review_remarks"),
                window_start=form.cleaned_data.get("window_start"),
                window_end=form.cleaned_data.get("window_end"),
            )
        except ValidationError as exc:
            form.add_error(None, str(exc))
        else:
            after = model_before_after(updated)
            unlock_window = getattr(updated, "unlock_window", None)
            if unlock_window:
                after["unlock_window"] = {
                    "start_at": unlock_window.start_at.isoformat() if unlock_window.start_at else None,
                    "end_at": unlock_window.end_at.isoformat() if unlock_window.end_at else None,
                    "is_active": unlock_window.is_active,
                    "is_consumed": unlock_window.is_consumed,
                }
            AuditService.log_event(
                action="APPROVE" if approved else "REJECT",
                portal="ADMIN",
                entity_type="GradeCorrectionRequest",
                entity_id=updated.id,
                actor=request.user,
                tenant=updated.tenant,
                campus=updated.campus,
                before_data=before,
                after_data=after,
                request=request,
            )
            registrar_email_result = None
            if approved and is_final_step and updated.status in {
                GradeCorrectionRequest.Status.APPROVED,
                GradeCorrectionRequest.Status.CLOSED,
            }:
                registrar_email_result = CorrectionNotificationService.send_registrar_official_report_email(
                    request_obj=updated,
                    trigger_role_code=pending_step.approver_role.code if pending_step and pending_step.approver_role_id else None,
                )
            messages.success(
                request,
                (
                    "Correction request approved, applied to the gradebook, and recomputed."
                    if approved and is_final_step and updated.status == GradeCorrectionRequest.Status.CLOSED
                    else "Correction request approved and unlock window opened."
                    if approved and is_final_step
                    else "Correction request step approved. Waiting for final approver."
                )
                if approved
                else "Correction request rejected.",
            )
            if registrar_email_result:
                if registrar_email_result["errors"]:
                    messages.warning(
                        request,
                        "Correction request was approved, but the registrar email could not be sent. "
                        "Please verify SMTP and registrar recipient configuration.",
                    )
                elif (
                    FeatureSettingsService.is_correction_registrar_auto_email_enabled(tenant_id=updated.tenant_id)
                    and registrar_email_result["attempted"] == 0
                ):
                    messages.warning(
                        request,
                        "Correction request was approved, but no registrar email recipient matched the current configuration "
                        "for this campus or trigger role.",
                    )
            return _redirect_back_or_default(request, "admin_portal:grade_correction_request_list")

    context = {
        "title": f"Review Correction Request #{correction_request.id}",
        "form": form,
        "correction_request": correction_request,
        "pending_step": pending_step,
        "is_final_step": is_final_step,
        "auto_apply_on_final_approval": auto_apply_on_final_approval,
        "official_report_enabled": official_report_enabled,
        "correction_window_hours": GradingGovernanceService.CORRECTION_WINDOW_HOURS,
        "review_guard_message": review_guard_message,
        "can_review": can_review,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/correction_request_review.html", context)


@portal_required("ADMIN")
@permission_required("corrections.review")
def grade_correction_request_official_report_view(request, request_id: int):
    correction_request = get_object_or_404(
        AdminScopeService.scoped_grade_correction_requests(request),
        id=request_id,
    )
    if not FeatureSettingsService.is_correction_official_report_enabled(tenant_id=correction_request.tenant_id):
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

