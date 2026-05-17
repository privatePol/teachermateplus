from __future__ import annotations

from collections import defaultdict
import csv
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from urllib.parse import urlencode

from django.contrib import messages
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django import forms as django_forms
from django.core.mail import EmailMultiAlternatives
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Max, Prefetch, Q, Sum
from django.db.models.deletion import ProtectedError, RestrictedError
from io import BytesIO

from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone

from apps.accounts.models import PortalLoginLockoutState, UserDeactivationSchedule
from apps.accounts.services import UserDeactivationService
from apps.admin_portal.forms import (
    ActiveAcademicTermSettingForm,
    ActiveGradingPeriodSettingForm,
    AcademicYearForm,
    CampusForm,
    ConfigurableFeatureSettingForm,
    CorrectionApprovalRouteRuleForm,
    CorrectionGovernanceSettingForm,
    CourseForm,
    CourseOfferingForm,
    CourseBaseValueOverrideForm,
    BulkCourseTemplateAssignmentForm,
    CourseTemplateAssignmentForm,
    GradingTemplateTestingCalculatorForm,
    DocumentPrintSettingForm,
    DepartmentForm,
    FacultyAssignmentForm,
    FacultyDeactivationScheduleForm,
    GradeCorrectionOnBehalfSetupForm,
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
    StudentAccountLinkForm,
    StudentAccountProvisioningForm,
    StudentForm,
    TenantForm,
    TenantTermGradingPeriodForm,
    TemplateGovernanceSettingForm,
    TemplateHotfixRequestForm,
    TemplateHotfixReviewForm,
    TenantGradingProfileForm,
    TermForm,
    UserCreateForm,
    UserChangePasswordForm,
    UserRoleAssignmentForm,
    UserUpdateForm,
)
from apps.faculty_portal.forms import GradeCorrectionRequestForm
from apps.academics.services import AcademicGovernanceService, FacultyAssignmentWorkflowService
from apps.admin_portal.data_reset import ActualDataResetService
from apps.admin_portal.services import AdminScopeService, model_before_after
from apps.admin_portal.grade_distribution import GradeDistributionMonitorService
from apps.academics.models import (
    AcademicYear,
    ActiveGradingPeriodSetting,
    Course,
    CourseOffering,
    FacultyAssignment,
    Section,
    TenantTermGradingPeriod,
    Term,
)
from apps.auditlog.models import AuditLog
from apps.core.decorators import permission_required, portal_required
from apps.core.services.audit import AuditService
from apps.core.services.email_assets import attach_logo_for_src, build_email_logo_context
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.forms import EnrollmentForm
from apps.enrollment.models import Enrollment
from apps.enrollment.services import EnrollmentService
from apps.faculty_portal.views import _build_summary_layout, _build_summary_row_values, _period_edit_state
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    FacultyFinalClearanceReport,
    GradeActivity,
    GradeCorrectionAttachment,
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
    StudentFinalGrade,
    StudentPeriodGrade,
    TemplateHotfixRequest,
    TemplateHotfixWorkflowStep,
    TenantGradingProfile,
)
from apps.grading.duplication import GradingTemplateDuplicationService
from apps.grading.explanations import GradeExplanationService
from apps.grading.notifications import CorrectionNotificationService
from apps.grading.reporting import CorrectionOfficialReportService, FacultyFinalClearanceReportService
from apps.grading.services import (
    FacultyGradingService,
    GradingGovernanceService,
    GradingTemplateTestingCalculatorService,
    GradingTemplateService,
    TemplateGovernanceWorkflowService,
    TemplateHotfixService,
)
from apps.imports.models import ImportBatch
from apps.imports.services import BulkImportService
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.notifications.models import SubmissionNonComplianceNotice
from apps.predictions.services import PredictionAuditService, PredictionSnapshotService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.student_portal.models import StudentAccountLink
from apps.student_portal.services import StudentAccountProvisioningService
from apps.tenants.models import Campus, Department, Program, Tenant

User = get_user_model()
INACTIVE_RECORD_DELETE_PERMISSION = "inactive_records.delete"
CRITICAL_ROLE_CONFIRMATION = "CHANGE PERMISSIONS"
HOTFIX_APPLY_CONFIRMATION = "APPLY HOTFIX"
BROAD_PERIOD_REOPEN_CONFIRMATION = "REOPEN"
CRITICAL_PERMISSION_CODES = {
    "actual_data_reset.run",
    "admin_portal.access",
    "audit_logs.read",
    "corrections.create_on_behalf",
    "corrections.review",
    "grade_submissions.reopen",
    "grade_submissions.revert_before_deadline",
    "gradebook.view_student_identity",
    "grading_periods.lock",
    "grading_periods.reopen",
    "inactive_records.delete",
    "roles.update",
    "system_settings.update",
    "template_hotfixes.create",
    "template_hotfixes.review",
    "users.update",
}
CRITICAL_AUDIT_FILTER = (
    Q(metadata_json__critical_action=True)
    | Q(metadata_json__has_anomaly_flags=True)
    | (
        Q(portal="ADMIN")
        & (
            Q(entity_type__in=["ActualDataReset", "TemplateHotfixRequest", "GradingPeriodLock", "RolePermission"])
            | (Q(entity_type="GradeCorrectionRequest") & Q(action__in=["APPROVE", "REJECT"]))
            | (Q(entity_type="GradeSubmissionReopenRequest") & Q(action__in=["APPROVE", "REJECT"]))
        )
    )
)


def _official_correction_report_filename(correction_request: GradeCorrectionRequest) -> str:
    period_code = correction_request.template_period.code or "PERIOD"
    course_code = correction_request.offering.course.code or "COURSE"
    section_code = correction_request.offering.section.code or "SECTION"
    return f"official-correction-{correction_request.id}-{course_code}-{section_code}-{period_code}.pdf"


def _faculty_final_clearance_report_filename(report_obj: FacultyFinalClearanceReport) -> str:
    faculty_code = report_obj.faculty_user.username or f"faculty-{report_obj.faculty_user_id}"
    campus_code = report_obj.campus.code or "campus"
    term_code = report_obj.term.code or "term"
    return f"faculty-final-clearance-{campus_code}-{term_code}-{faculty_code}-{report_obj.id}.pdf"


def _permission_codes_for_ids(permission_ids) -> list[str]:
    if not permission_ids:
        return []
    return list(
        Permission.objects.filter(id__in=permission_ids)
        .order_by("module", "action", "code")
        .values_list("code", flat=True)
    )


def _role_permission_impact(role: Role, to_add, to_remove) -> dict:
    added_codes = _permission_codes_for_ids(to_add)
    removed_codes = _permission_codes_for_ids(to_remove)
    critical_added = [code for code in added_codes if code in CRITICAL_PERMISSION_CODES]
    critical_removed = [code for code in removed_codes if code in CRITICAL_PERMISSION_CODES]
    affected_users = (
        UserRole.objects.filter(role=role, is_active=True, user__is_active=True)
        .values("user_id")
        .distinct()
        .count()
    )
    return {
        "role_code": role.code,
        "added_permission_count": len(added_codes),
        "removed_permission_count": len(removed_codes),
        "added_permission_codes": added_codes,
        "removed_permission_codes": removed_codes,
        "critical_added_permission_codes": critical_added,
        "critical_removed_permission_codes": critical_removed,
        "critical_permission_count": len(critical_added) + len(critical_removed),
        "has_critical_change": bool(critical_added or critical_removed),
        "affected_active_user_count": affected_users,
    }


def _template_hotfix_impact_preview(hotfix_request: TemplateHotfixRequest) -> dict:
    target_offerings = TemplateHotfixService._resolve_target_offerings(hotfix_request)
    submitted_count = 0
    near_or_after_deadline_count = 0
    campus_codes = set()
    term_codes = set()
    sample_offerings = []
    target_offering_ids = []
    near_deadline_cutoff = timezone.now() + timedelta(days=3)
    for offering in target_offerings:
        target_offering_ids.append(offering.id)
        if TemplateHotfixService._offering_has_submitted_grades(offering):
            submitted_count += 1
        if GradingPeriodLock.objects.filter(
            tenant_id=offering.tenant_id,
            campus_id=offering.campus_id,
            academic_year_id=offering.academic_year_id,
            term_id=offering.term_id,
            is_active=True,
            deadline_at__isnull=False,
            deadline_at__lte=near_deadline_cutoff,
        ).exists():
            near_or_after_deadline_count += 1
        campus_codes.add(offering.campus.code if offering.campus_id else "")
        term_codes.add(offering.term.code if offering.term_id else "")
        if len(sample_offerings) < 8:
            sample_offerings.append(
                {
                    "id": offering.id,
                    "label": f"{offering.course.title} ({offering.course.code}) / "
                    f"{offering.section.name or offering.section.code}",
                }
            )
    return {
        "template_code": hotfix_request.template.code,
        "apply_mode": hotfix_request.apply_mode,
        "target_offering_count": len(target_offerings),
        "submitted_or_reopened_offering_count": submitted_count,
        "near_or_after_deadline_offering_count": near_or_after_deadline_count,
        "campus_count": len([code for code in campus_codes if code]),
        "term_count": len([code for code in term_codes if code]),
        "target_offering_ids": target_offering_ids[:100],
        "sample_offerings": sample_offerings,
    }


def _is_hotfix_apply_step(hotfix_request: TemplateHotfixRequest, current_step) -> bool:
    if not current_step:
        return False
    return not hotfix_request.workflow_steps.filter(
        step_no__gt=current_step.step_no,
        status__in=[TemplateHotfixWorkflowStep.Status.QUEUED, TemplateHotfixWorkflowStep.Status.PENDING],
    ).exists()


def _period_lock_reopen_impact(row: GradingPeriodLock, request) -> dict:
    if row.scope_type == GradingPeriodLock.ScopeType.CAMPUS:
        target_count = AdminScopeService.scoped_course_offerings(request).filter(
            tenant_id=row.tenant_id,
            campus_id=row.campus_id,
            academic_year_id=row.academic_year_id,
            term_id=row.term_id,
            is_active=True,
        ).count()
        scope_label = "Campus-wide"
    else:
        target_count = 1 if row.course_offering_id else 0
        scope_label = "Single offering"
    return {
        "is_broad": row.scope_type == GradingPeriodLock.ScopeType.CAMPUS,
        "scope_type": row.scope_type,
        "scope_label": scope_label,
        "period_code": row.period_code,
        "target_offering_count": target_count,
        "course_offering_id": row.course_offering_id,
        "campus_code": row.campus.code if row.campus_id else "",
        "term_code": row.term.code if row.term_id else "",
        "deadline_at": row.deadline_at.isoformat() if row.deadline_at else None,
    }


def _critical_audit_reason(metadata: dict | None) -> str:
    metadata = metadata or {}
    return (
        metadata.get("reason")
        or metadata.get("review_reason")
        or metadata.get("reset_reason")
        or metadata.get("review_remarks")
        or ""
    )


def _critical_audit_impact_label(metadata: dict | None) -> str:
    metadata = metadata or {}
    impact = metadata.get("impact_summary") or {}
    if not impact:
        return ""
    if "target_offering_count" in impact:
        return f"{impact.get('target_offering_count', 0)} offering(s)"
    if "delete_total" in impact:
        return f"{impact.get('delete_total', 0)} row(s) targeted"
    if "affected_active_user_count" in impact:
        return f"{impact.get('affected_active_user_count', 0)} active user(s)"
    return ""


def _audit_anomaly_flags(metadata: dict | None) -> list:
    metadata = metadata or {}
    flags = metadata.get("anomaly_flags_json") or []
    return flags if isinstance(flags, list) else []


def _governance_alert_rows(request, limit=20):
    rows = list(
        _scoped_audit_queryset(request)
        .filter(metadata_json__has_anomaly_flags=True)
        .order_by("-created_at")[:limit]
    )
    for row in rows:
        flags = _audit_anomaly_flags(row.metadata_json)
        row.anomaly_flags = flags
        row.primary_anomaly = flags[0] if flags else {}
        row.max_anomaly_severity = (row.metadata_json or {}).get("max_anomaly_severity", "")
        row.safe_impact_label = _critical_audit_impact_label(row.metadata_json)
        row.scope_summary = f"{row.tenant.code if row.tenant_id else 'GLOBAL'} / {row.campus.code if row.campus_id else 'ALL'}"
    return rows


def _scope_context(request):
    tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
    campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
    current_tenant_id = getattr(request, "scope", {}).get("tenant_id")
    current_campus_id = getattr(request, "scope", {}).get("campus_id")

    scope_tenants = Tenant.objects.filter(id__in=tenant_ids, is_active=True).order_by("name")
    scope_campuses = Campus.objects.filter(id__in=campus_ids, is_active=True, tenant__is_active=True).order_by("name")

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


def _get_page(request, queryset, per_page=20, page_param="page"):
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get(page_param, 1))
    query_params = request.GET.copy()
    query_params.pop(page_param, None)
    page_obj.querystring = query_params.urlencode()
    page_obj.page_param = page_param
    return page_obj


def _audit_reason_from_metadata(log):
    metadata = log.metadata_json or {}
    return (
        metadata.get("reason")
        or metadata.get("review_remarks")
        or metadata.get("change_reason")
        or metadata.get("reset_reason")
        or ""
    )


def _critical_audit_summary(log):
    metadata = log.metadata_json or {}
    impact = metadata.get("impact_summary") or {}
    parts = []
    for key, label in (
        ("affected_offering_count", "Offerings"),
        ("affected_student_count", "Students"),
        ("affected_user_count", "Users"),
        ("added_critical_count", "Critical added"),
        ("removed_critical_count", "Critical removed"),
        ("audit_export_count", "Audit rows exported"),
    ):
        value = impact.get(key, metadata.get(key))
        if value not in (None, ""):
            parts.append(f"{label}: {value}")
    if metadata.get("confirmation_required"):
        parts.append("Confirmation required")
    if metadata.get("audit_export_path"):
        parts.append("Audit export created")
    return " | ".join(parts)


def _critical_permission_change_preview(*, role, to_add, to_remove):
    added = list(Permission.objects.filter(id__in=to_add).order_by("code"))
    removed = list(Permission.objects.filter(id__in=to_remove).order_by("code"))
    added_critical = [permission for permission in added if permission.code in CRITICAL_PERMISSION_CODES]
    removed_critical = [permission for permission in removed if permission.code in CRITICAL_PERMISSION_CODES]
    affected_user_count = role.user_roles.filter(is_active=True, user__is_active=True).values("user_id").distinct().count()
    return {
        "added": added,
        "removed": removed,
        "added_critical": added_critical,
        "removed_critical": removed_critical,
        "has_critical_change": bool(added_critical or removed_critical),
        "affected_user_count": affected_user_count,
    }


def _period_lock_reopen_preview_for_request(request, row):
    affected_offering_count = 1 if row.scope_type == GradingPeriodLock.ScopeType.COURSE and row.course_offering_id else 0
    if row.scope_type == GradingPeriodLock.ScopeType.CAMPUS:
        affected_offering_count = (
            AdminScopeService.scoped_course_offerings(request)
            .filter(
                tenant_id=row.tenant_id,
                campus_id=row.campus_id,
                academic_year_id=row.academic_year_id,
                term_id=row.term_id,
                is_active=True,
            )
            .count()
        )
    return {
        "scope_type": row.scope_type,
        "is_broad_scope": row.scope_type == GradingPeriodLock.ScopeType.CAMPUS,
        "campus": row.campus.code,
        "term": row.term.code,
        "period_code": row.period_code,
        "deadline_at": row.deadline_at,
        "course_offering_id": row.course_offering_id,
        "affected_offering_count": affected_offering_count,
    }


def _active_inactive_pages(request, queryset, per_page=20):
    active_page_obj = _get_page(request, queryset.filter(is_active=True), per_page=per_page, page_param="active_page")
    inactive_page_obj = _get_page(
        request,
        queryset.filter(is_active=False),
        per_page=per_page,
        page_param="inactive_page",
    )
    return {
        "page_obj": active_page_obj,
        "active_page_obj": active_page_obj,
        "inactive_page_obj": inactive_page_obj,
    }


def _record_delete_confirmation_code(row) -> str:
    for attr in (
        "code",
        "profile_code",
        "student_no",
        "student_number",
        "username",
        "section_code",
        "period_code",
        "name",
        "title",
    ):
        value = getattr(row, attr, None)
        if value:
            return str(value)
    return str(row.pk)


def _inactive_record_dependency_rows(row):
    dependencies = []
    for relation in row._meta.related_objects:
        accessor_name = relation.get_accessor_name()
        if not accessor_name:
            continue
        try:
            related_accessor = getattr(row, accessor_name)
            if relation.one_to_one:
                count = 1 if related_accessor else 0
            else:
                count = related_accessor.count()
        except relation.related_model.DoesNotExist:
            count = 0
        except Exception:
            count = 1
        if count:
            dependencies.append(
                {
                    "label": " ".join(re.findall(r"[A-Z][a-z0-9]*", relation.related_model._meta.object_name))
                    or relation.related_model._meta.object_name,
                    "count": count,
                }
            )
    dependencies.sort(key=lambda item: item["label"])
    return dependencies


def _inactive_record_usage_label(dependencies):
    if not dependencies:
        return "Not assigned"
    visible_items = dependencies[:3]
    label = ", ".join(f"{item['label']} ({item['count']})" for item in visible_items)
    if len(dependencies) > len(visible_items):
        label += f", +{len(dependencies) - len(visible_items)} more"
    return label


def _can_permanently_delete_inactive_records(request, *, model_key: str) -> bool:
    config = _inactive_delete_configs().get(model_key)
    if not config:
        return False
    return request.user.is_superuser or PermissionService.has_permission(request.user, INACTIVE_RECORD_DELETE_PERMISSION)


def _attach_inactive_record_metadata(page_obj, *, model_key: str, allow_delete: bool):
    for row in page_obj:
        dependencies = _inactive_record_dependency_rows(row)
        row.hard_delete_model_key = model_key
        row.hard_delete_confirmation_code = _record_delete_confirmation_code(row)
        row.inactive_usage_dependencies = dependencies
        row.inactive_usage_label = _inactive_record_usage_label(dependencies)
        row.can_show_hard_delete = allow_delete
        row.can_hard_delete = allow_delete and not dependencies
    return page_obj


def _with_inactive_record_metadata(request, context, *, model_key: str):
    inactive_page_obj = context.get("inactive_page_obj")
    if inactive_page_obj:
        _attach_inactive_record_metadata(
            inactive_page_obj,
            model_key=model_key,
            allow_delete=_can_permanently_delete_inactive_records(request, model_key=model_key),
        )
    return context


def _user_dependency_count(user):
    dependencies = _inactive_record_dependency_rows(user)
    schedules = UserDeactivationSchedule.objects.filter(user=user).count()
    if schedules:
        dependencies.append({"label": "User Deactivation Schedules", "count": schedules})
    dependencies.sort(key=lambda item: item["label"])
    return dependencies


def _attach_inactive_user_metadata(request, users):
    allow_delete = _can_permanently_delete_inactive_records(request, model_key="user")
    for row in users:
        dependencies = _user_dependency_count(row)
        row.hard_delete_model_key = "user"
        row.hard_delete_confirmation_code = _record_delete_confirmation_code(row)
        row.inactive_usage_dependencies = dependencies
        row.inactive_usage_label = _inactive_record_usage_label(dependencies)
        row.can_show_hard_delete = allow_delete
        row.can_hard_delete = allow_delete and not dependencies
    return users


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maintenance_scope_tenant_ids(request):
    if request.user.is_superuser:
        return Tenant.objects.values_list("id", flat=True)
    return getattr(request, "scope", {}).get("tenant_ids", [])


def _maintenance_scope_campus_ids(request):
    if request.user.is_superuser:
        return Campus.objects.values_list("id", flat=True)
    campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
    if campus_ids:
        return campus_ids
    return Campus.objects.filter(tenant_id__in=_maintenance_scope_tenant_ids(request)).values_list("id", flat=True)


def _maintenance_scope_department_ids(request):
    if request.user.is_superuser:
        return Department.objects.values_list("id", flat=True)
    department_ids = getattr(request, "scope", {}).get("department_ids", [])
    if department_ids:
        return department_ids
    return Department.objects.filter(
        tenant_id__in=_maintenance_scope_tenant_ids(request),
        campus_id__in=_maintenance_scope_campus_ids(request),
    ).values_list("id", flat=True)


def _maintenance_scoped_tenants_for_delete(request):
    return Tenant.objects.filter(id__in=_maintenance_scope_tenant_ids(request)).order_by("name")


def _maintenance_scoped_campuses_for_delete(request):
    return Campus.objects.filter(
        tenant_id__in=_maintenance_scope_tenant_ids(request),
        id__in=_maintenance_scope_campus_ids(request),
    ).select_related("tenant").order_by("tenant__name", "name")


def _maintenance_scoped_departments_for_delete(request):
    return Department.objects.filter(
        tenant_id__in=_maintenance_scope_tenant_ids(request),
        campus_id__in=_maintenance_scope_campus_ids(request),
        id__in=_maintenance_scope_department_ids(request),
    ).select_related("tenant", "campus", "parent").order_by("tenant__name", "campus__name", "name")


def _maintenance_scoped_programs_for_delete(request):
    return Program.objects.filter(
        tenant_id__in=_maintenance_scope_tenant_ids(request),
        campus_id__in=_maintenance_scope_campus_ids(request),
        department_id__in=_maintenance_scope_department_ids(request),
    ).select_related("tenant", "campus", "department").order_by("tenant__name", "campus__name", "name")


def _maintenance_scoped_academic_years_for_delete(request):
    return AcademicYear.objects.filter(tenant_id__in=_maintenance_scope_tenant_ids(request)).select_related("tenant")


def _maintenance_scoped_terms_for_delete(request):
    return Term.objects.filter(tenant_id__in=_maintenance_scope_tenant_ids(request)).select_related("tenant", "academic_year")


def _maintenance_scoped_courses_for_delete(request):
    return Course.objects.filter(
        tenant_id__in=_maintenance_scope_tenant_ids(request),
    ).filter(
        Q(campus_id__in=_maintenance_scope_campus_ids(request)) | Q(campus__isnull=True),
    ).select_related("tenant", "campus", "department")


def _maintenance_scoped_sections_for_delete(request):
    return Section.objects.filter(
        tenant_id__in=_maintenance_scope_tenant_ids(request),
        campus_id__in=_maintenance_scope_campus_ids(request),
    ).select_related("tenant", "campus", "department", "program")


def _maintenance_scoped_offerings_for_delete(request):
    return CourseOffering.objects.filter(
        tenant_id__in=_maintenance_scope_tenant_ids(request),
        campus_id__in=_maintenance_scope_campus_ids(request),
    ).select_related("tenant", "campus", "academic_year", "term", "course", "section")


def _maintenance_scoped_students_for_delete(request):
    return Student.objects.filter(
        tenant_id__in=_maintenance_scope_tenant_ids(request),
        campus_id__in=_maintenance_scope_campus_ids(request),
    ).select_related("tenant", "campus", "department", "program")


def _maintenance_scoped_enrollments_for_delete(request):
    return Enrollment.objects.filter(
        tenant_id__in=_maintenance_scope_tenant_ids(request),
        campus_id__in=_maintenance_scope_campus_ids(request),
    ).select_related("tenant", "campus", "student", "course_offering")


def _inactive_delete_configs():
    return {
        "tenant": {
            "queryset": _maintenance_scoped_tenants_for_delete,
            "permission": "tenants.update",
            "redirect": "admin_portal:tenant_list",
        },
        "campus": {
            "queryset": _maintenance_scoped_campuses_for_delete,
            "permission": "campuses.update",
            "redirect": "admin_portal:campus_list",
        },
        "department": {
            "queryset": _maintenance_scoped_departments_for_delete,
            "permission": "departments.update",
            "redirect": "admin_portal:department_list",
        },
        "program": {
            "queryset": _maintenance_scoped_programs_for_delete,
            "permission": "programs.update",
            "redirect": "admin_portal:program_list",
        },
        "academic_year": {
            "queryset": _maintenance_scoped_academic_years_for_delete,
            "permission": "academic_years.update",
            "redirect": "admin_portal:academic_year_list",
        },
        "term": {
            "queryset": _maintenance_scoped_terms_for_delete,
            "permission": "terms.update",
            "redirect": "admin_portal:term_list",
        },
        "course": {
            "queryset": _maintenance_scoped_courses_for_delete,
            "permission": "courses.update",
            "redirect": "admin_portal:course_list",
        },
        "section": {
            "queryset": _maintenance_scoped_sections_for_delete,
            "permission": "sections.update",
            "redirect": "admin_portal:section_list",
        },
        "offering": {
            "queryset": _maintenance_scoped_offerings_for_delete,
            "permission": "course_offerings.update",
            "redirect": "admin_portal:offering_list",
        },
        "student": {
            "queryset": _maintenance_scoped_students_for_delete,
            "permission": "students.update",
            "redirect": "admin_portal:student_list",
        },
        "enrollment": {
            "queryset": _maintenance_scoped_enrollments_for_delete,
            "permission": "enrollments.update",
            "redirect": "admin_portal:enrollment_list",
        },
        "grading_template": {
            "queryset": lambda request: AdminScopeService.maintenance_scoped_grading_templates(request),
            "permission": "grading_templates.update",
            "redirect": "admin_portal:grading_template_list",
        },
        "template_period": {
            "queryset": lambda request: AdminScopeService.maintenance_scoped_template_periods(request),
            "permission": "template_periods.update",
            "redirect": "admin_portal:template_period_list",
        },
        "template_component": {
            "queryset": lambda request: AdminScopeService.maintenance_scoped_template_components(request),
            "permission": "template_components.update",
            "redirect": "admin_portal:template_component_list",
        },
        "template_subcomponent": {
            "queryset": lambda request: AdminScopeService.maintenance_scoped_template_subcomponents(request),
            "permission": "template_subcomponents.update",
            "redirect": "admin_portal:template_subcomponent_list",
        },
        "template_detail": {
            "queryset": lambda request: AdminScopeService.maintenance_scoped_template_details(request),
            "permission": "template_details.update",
            "redirect": "admin_portal:template_detail_list",
        },
        "tenant_grading_profile": {
            "queryset": lambda request: AdminScopeService.maintenance_scoped_tenant_grading_profiles(request),
            "permission": "tenant_grading_profiles.update",
            "redirect": "admin_portal:tenant_grading_profile_list",
        },
        "course_template_assignment": {
            "queryset": lambda request: AdminScopeService.maintenance_scoped_course_template_assignments(request),
            "permission": "course_template_assignments.update",
            "redirect": "admin_portal:course_template_assignment_list",
        },
        "course_base_override": {
            "queryset": lambda request: AdminScopeService.maintenance_scoped_course_base_value_overrides(request),
            "permission": "course_base_overrides.update",
            "redirect": "admin_portal:course_base_override_list",
        },
        "period_lock": {
            "queryset": lambda request: AdminScopeService.maintenance_scoped_grading_period_locks(request),
            "permission": "grading_periods.lock",
            "redirect": "admin_portal:grading_period_lock_list",
        },
        "user": {
            "queryset": lambda request: _scoped_users_queryset(request),
            "permission": "users.update",
            "redirect": "admin_portal:user_list",
        },
    }


@portal_required("ADMIN")
def inactive_record_delete_view(request, model_key: str, object_id: int):
    config = _inactive_delete_configs().get(model_key)
    if not config:
        raise Http404("Unsupported maintenance record type.")
    if request.method != "POST":
        return redirect(config["redirect"])
    if not request.user.is_superuser and not PermissionService.has_permission(
        request.user,
        INACTIVE_RECORD_DELETE_PERMISSION,
    ):
        raise PermissionDenied

    row = get_object_or_404(config["queryset"](request), id=object_id)
    if not hasattr(row, "is_active") or row.is_active:
        messages.error(request, "Only inactive records can be permanently deleted.")
        return redirect(config["redirect"])

    confirmation = (request.POST.get("confirmation_code") or "").strip()
    expected_confirmation = _record_delete_confirmation_code(row)
    if confirmation != expected_confirmation:
        messages.error(request, f"Record was not deleted. Type {expected_confirmation} exactly to confirm.")
        return redirect(config["redirect"])

    dependencies = _user_dependency_count(row) if model_key == "user" else _inactive_record_dependency_rows(row)
    if dependencies:
        messages.error(
            request,
            f"Record was not deleted because it is already assigned: {_inactive_record_usage_label(dependencies)}.",
        )
        return redirect(config["redirect"])

    before = model_before_after(row)
    model_name = row.__class__.__name__
    record_label = expected_confirmation
    record_id = row.id
    try:
        with transaction.atomic():
            row.delete()
    except (ProtectedError, RestrictedError) as exc:
        protected_count = len(getattr(exc, "protected_objects", []) or [])
        restricted_count = len(getattr(exc, "restricted_objects", []) or [])
        blocked_count = protected_count + restricted_count
        message = "Record was not deleted because another table still references it."
        if blocked_count:
            message = f"{message} Blocking rows: {blocked_count}."
        messages.error(request, message)
        return redirect(config["redirect"])

    AuditService.log_event(
        action="DELETE",
        portal="ADMIN",
        entity_type=model_name,
        entity_id=record_id,
        actor=request.user,
        before_data=before,
        metadata={
            "hard_delete": True,
            "model_key": model_key,
            "record_label": record_label,
        },
        request=request,
    )
    messages.success(request, f"Inactive record {record_label} was permanently deleted.")
    return redirect(config["redirect"])


def _format_decimal_display(value):
    if value in (None, ""):
        return ""
    decimal_value = Decimal(str(value))
    formatted = format(decimal_value.quantize(Decimal("0.01")), "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


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


def _can_view_gradebook_student_identity(user, *, tenant_id=None, campus_id=None):
    if getattr(user, "is_superuser", False):
        return True
    return PermissionService.has_permission(
        user,
        "gradebook.view_student_identity",
        tenant_id=tenant_id,
        campus_id=campus_id,
    )


def _should_mask_gradebook_student_identity(user, *, tenant_id=None, campus_id=None):
    if _can_view_gradebook_student_identity(user, tenant_id=tenant_id, campus_id=campus_id):
        return False
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


def _assignment_counts(queryset, *, now=None):
    now = now or timezone.now()
    due_soon_cutoff = now + timedelta(days=1)
    assigned_count = queryset.count()
    accepted_count = queryset.filter(response_status=FacultyAssignment.ResponseStatus.ACCEPTED).count()
    pending_count = queryset.filter(response_status=FacultyAssignment.ResponseStatus.PENDING).count()
    clarification_count = queryset.filter(
        response_status=FacultyAssignment.ResponseStatus.CLARIFICATION_REQUESTED
    ).count()
    declined_count = queryset.filter(response_status=FacultyAssignment.ResponseStatus.DECLINED).count()
    expired_count = queryset.filter(response_status=FacultyAssignment.ResponseStatus.EXPIRED).count()
    due_soon_count = queryset.filter(
        response_status=FacultyAssignment.ResponseStatus.PENDING,
        response_due_at__isnull=False,
        response_due_at__gt=now,
        response_due_at__lte=due_soon_cutoff,
    ).count()
    acceptance_rate = round((accepted_count / assigned_count) * 100, 1) if assigned_count else 0
    return {
        "assigned_count": assigned_count,
        "accepted_count": accepted_count,
        "pending_acceptance_count": pending_count,
        "clarification_count": clarification_count,
        "declined_count": declined_count,
        "expired_count": expired_count,
        "due_soon_count": due_soon_count,
        "acceptance_rate": acceptance_rate,
    }


def _resolve_monitor_window(*, window_code: str, selected_term: Term | None, now=None):
    now = now or timezone.now()
    code = (window_code or "7d").strip().lower()
    start = now - timedelta(days=7)
    label = "Last 7 Days"
    note = "Weekly monitoring view."
    if code == "30d":
        start = now - timedelta(days=30)
        label = "Last 30 Days"
        note = "Monthly monitoring view."
    elif code == "term" and selected_term:
        if selected_term.start_date:
            start = timezone.make_aware(
                timezone.datetime.combine(selected_term.start_date, timezone.datetime.min.time()),
                timezone.get_current_timezone(),
            )
        label = f"Current Term: {selected_term.code}"
        note = "Term-to-date monitoring view."
        if selected_term.end_date:
            term_end = timezone.make_aware(
                timezone.datetime.combine(selected_term.end_date, timezone.datetime.max.time()),
                timezone.get_current_timezone(),
            )
            now = min(now, term_end)
    return {
        "code": code if code in {"7d", "30d", "term"} else "7d",
        "start": start,
        "end": now,
        "label": label,
        "note": note,
    }


def _faculty_activity_status(row, *, window_start):
    if row["assigned_classes"] <= 0:
        return {
            "label": "No Accepted Classes",
            "variant": "secondary",
            "note": "This faculty member has no accepted class in the selected scope.",
        }
    if not row["last_login_at"] or row["last_login_at"] < window_start:
        return {
            "label": "No Login",
            "variant": "danger",
            "note": "No faculty login was recorded during the selected monitoring window.",
        }
    if row["gradebook_update_events"] <= 0:
        return {
            "label": "No Gradebook Update",
            "variant": "danger",
            "note": "The faculty logged in but did not create activities, encode grades, or update the gradebook.",
        }
    if row["classes_with_no_activity"] > 0 or row["classes_with_no_scores"] > 0:
        return {
            "label": "Needs Follow-up",
            "variant": "warning",
            "note": "One or more assigned classes still have no activity or no encoded scores.",
        }
    if row["activities_created"] <= 0 and row["scores_saved"] <= 0:
        return {
            "label": "Low Activity",
            "variant": "secondary",
            "note": "The faculty has some system movement, but no new activity or score work in this window.",
        }
    return {
        "label": "Active",
        "variant": "success",
        "note": "Faculty is logging in and updating the gradebook in the selected monitoring window.",
    }


def _faculty_activity_flags(row, *, window_start):
    flags = []
    if row["assigned_classes"] <= 0:
        flags.append({"label": "No Accepted Classes", "variant": "secondary"})
        return flags
    if not row["last_login_at"] or row["last_login_at"] < window_start:
        flags.append({"label": "No Login", "variant": "danger"})
    if row["activities_created"] <= 0:
        flags.append({"label": "No Activity Created", "variant": "warning"})
    if row["scores_saved"] <= 0:
        flags.append({"label": "No Grade Encoding", "variant": "warning"})
    if row["gradebook_update_events"] <= 0:
        flags.append({"label": "No Gradebook Update", "variant": "danger"})
    if row.get("classes_with_no_activity", 0) > 0:
        flags.append({"label": "Classes Without Activity", "variant": "danger"})
    if row.get("classes_with_no_scores", 0) > 0:
        flags.append({"label": "Classes Without Scores", "variant": "warning"})
    return flags


def _build_activity_trend_buckets(*, logs, start, end, actor_user_id=None):
    bucket_count = 4
    total_seconds = max((end - start).total_seconds(), 1)
    bucket_seconds = total_seconds / bucket_count
    buckets = []
    for index in range(bucket_count):
        bucket_start = start + timedelta(seconds=bucket_seconds * index)
        bucket_end = end if index == bucket_count - 1 else start + timedelta(seconds=bucket_seconds * (index + 1))
        buckets.append(
            {
                "label": bucket_start.strftime("%b %d"),
                "range_label": f"{bucket_start.strftime('%b %d')} - {(bucket_end - timedelta(seconds=1)).strftime('%b %d')}",
                "login_count": 0,
                "activity_count": 0,
                "score_count": 0,
                "gradebook_events": 0,
            }
        )
    for log in logs:
        if actor_user_id and log.actor_user_id != actor_user_id:
            continue
        try:
            offset = int(((log.created_at - start).total_seconds()) / bucket_seconds)
        except ZeroDivisionError:
            offset = 0
        offset = max(0, min(bucket_count - 1, offset))
        bucket = buckets[offset]
        if log.action == "LOGIN_SUCCESS" and log.entity_type == "User":
            bucket["login_count"] += 1
            continue
        if log.entity_type == "GradeActivity":
            bucket["activity_count"] += 1
            bucket["gradebook_events"] += 1
        elif log.entity_type == "StudentActivityScore":
            saved_count = 1
            if isinstance(log.metadata_json, dict):
                try:
                    saved_count = int(log.metadata_json.get("saved_count") or 1)
                except (TypeError, ValueError):
                    saved_count = 1
            bucket["score_count"] += saved_count
            bucket["gradebook_events"] += 1
        elif log.entity_type in {"AttendanceSession", "AttendanceRecord", "GradeSubmission", "GradeCorrectionRequest", "Enrollment"}:
            bucket["gradebook_events"] += 1
    max_total = max(
        [
            max(bucket["login_count"], bucket["activity_count"], bucket["score_count"], bucket["gradebook_events"])
            for bucket in buckets
        ],
        default=0,
    )
    for bucket in buckets:
        bucket["login_width"] = round((bucket["login_count"] / max_total) * 100, 1) if max_total else 0
        bucket["activity_width"] = round((bucket["activity_count"] / max_total) * 100, 1) if max_total else 0
        bucket["score_width"] = round((bucket["score_count"] / max_total) * 100, 1) if max_total else 0
        bucket["gradebook_width"] = round((bucket["gradebook_events"] / max_total) * 100, 1) if max_total else 0
    return buckets


def _build_week_over_week_buckets(*, logs, end, actor_user_id=None, week_count=6):
    end = end or timezone.now()
    local_end = timezone.localtime(end)
    current_week_start_date = local_end.date() - timedelta(days=local_end.weekday())
    current_week_start = timezone.make_aware(
        timezone.datetime.combine(current_week_start_date, timezone.datetime.min.time()),
        timezone.get_current_timezone(),
    )
    series_start = current_week_start - timedelta(weeks=max(week_count - 1, 0))
    series_end = current_week_start + timedelta(weeks=1)

    buckets = []
    for index in range(week_count):
        bucket_start = series_start + timedelta(weeks=index)
        bucket_end = bucket_start + timedelta(weeks=1)
        buckets.append(
            {
                "label": bucket_start.strftime("%b %d"),
                "range_label": f"{bucket_start.strftime('%b %d')} - {(bucket_end - timedelta(seconds=1)).strftime('%b %d')}",
                "login_count": 0,
                "activity_count": 0,
                "score_count": 0,
                "gradebook_events": 0,
            }
        )

    for log in logs:
        if actor_user_id and log.actor_user_id != actor_user_id:
            continue
        if log.created_at < series_start or log.created_at >= series_end:
            continue
        offset = int((log.created_at - series_start).total_seconds() // (7 * 24 * 60 * 60))
        offset = max(0, min(week_count - 1, offset))
        bucket = buckets[offset]
        if log.action == "LOGIN_SUCCESS" and log.entity_type == "User":
            bucket["login_count"] += 1
            continue
        if log.entity_type == "GradeActivity":
            bucket["activity_count"] += 1
            bucket["gradebook_events"] += 1
        elif log.entity_type == "StudentActivityScore":
            saved_count = 1
            if isinstance(log.metadata_json, dict):
                try:
                    saved_count = int(log.metadata_json.get("saved_count") or 1)
                except (TypeError, ValueError):
                    saved_count = 1
            bucket["score_count"] += saved_count
            bucket["gradebook_events"] += 1
        elif log.entity_type in {"AttendanceSession", "AttendanceRecord", "GradeSubmission", "GradeCorrectionRequest", "Enrollment"}:
            bucket["gradebook_events"] += 1

    max_total = max(
        [
            max(bucket["login_count"], bucket["activity_count"], bucket["score_count"], bucket["gradebook_events"])
            for bucket in buckets
        ],
        default=0,
    )
    for bucket in buckets:
        bucket["login_width"] = round((bucket["login_count"] / max_total) * 100, 1) if max_total else 0
        bucket["activity_width"] = round((bucket["activity_count"] / max_total) * 100, 1) if max_total else 0
        bucket["score_width"] = round((bucket["score_count"] / max_total) * 100, 1) if max_total else 0
        bucket["gradebook_width"] = round((bucket["gradebook_events"] / max_total) * 100, 1) if max_total else 0
    return buckets


def _offering_monitor_label(offering):
    return f"{offering.course.code} | {offering.section.name or offering.section.code}"


def _scoped_login_lockout_queryset(request):
    scope_tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
    scope_campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
    queryset = PortalLoginLockoutState.objects.select_related(
        "user",
        "user__default_tenant",
        "user__default_campus",
        "user__default_department",
    ).order_by("-updated_at", "portal_code", "username")
    if request.user.is_superuser:
        return queryset
    scope_filter = Q(user__isnull=True)
    if scope_tenant_ids:
        scope_filter |= Q(user__default_tenant_id__in=scope_tenant_ids)
    if scope_campus_ids:
        scope_filter |= Q(user__default_campus_id__in=scope_campus_ids)
    return queryset.filter(scope_filter)


def _faculty_assignment_scope_snapshot(assignment):
    campus = getattr(assignment.faculty_user, "default_campus", None) or assignment.campus or assignment.offering.campus
    department = (
        getattr(assignment.faculty_user, "default_department", None)
        or assignment.offering.department
    )
    full_name = (getattr(assignment.faculty_user, "full_name", "") or "").strip()
    username = (getattr(assignment.faculty_user, "username", "") or "").strip()
    faculty_label = f"{full_name} ({username})" if full_name and full_name != username else (full_name or username)
    return {
        "campus": campus,
        "campus_id": getattr(campus, "id", None),
        "campus_label": getattr(campus, "code", None) or getattr(campus, "name", None) or "-",
        "department": department,
        "department_id": getattr(department, "id", None),
        "department_label": getattr(department, "name", None) or getattr(department, "code", None) or "-",
        "faculty_label": faculty_label or "-",
    }


def _send_new_user_credentials_email(request, user, temporary_password: str) -> int:
    admin_login_url = request.build_absolute_uri(reverse("accounts:admin_login"))
    faculty_public_url = request.build_absolute_uri(reverse("faculty_portal:public_index"))
    logo_context = build_email_logo_context(
        filename="egp_logo_official.png",
        cid="edugradespro-logo",
        external_url=getattr(settings, "EMAIL_LOGO_URL", ""),
        configured_path=getattr(settings, "EMAIL_LOGO_PATH", ""),
    )
    context = {
        "user": user,
        "temporary_password": temporary_password,
        "admin_login_url": admin_login_url,
        "faculty_public_url": faculty_public_url,
        **logo_context,
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
    attach_logo_for_src(
        message,
        src=logo_context["email_logo_src"],
        filename="egp_logo_official.png",
        cid="edugradespro-logo",
        configured_path=getattr(settings, "EMAIL_LOGO_PATH", ""),
    )
    message.attach_alternative(html_body, "text/html")
    return message.send(fail_silently=False)


def admin_portal_root_view(request):
    if not request.user.is_authenticated:
        return redirect("accounts:admin_login")
    if not PermissionService.has_permission(
        request.user,
        "admin_portal.access",
        tenant_id=getattr(request, "scope", {}).get("tenant_id"),
        campus_id=getattr(request, "scope", {}).get("campus_id"),
    ):
        return HttpResponseForbidden("Admin portal access denied.")
    return redirect("admin_portal:dashboard")


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
    has_faculty_assignments_read = PermissionService.has_permission(
        request.user,
        "faculty_assignments.read",
        tenant_id=current_tenant_id,
        campus_id=current_campus_id,
    )
    has_grading_period_lock = PermissionService.has_permission(
        request.user,
        "grading_periods.lock",
        tenant_id=current_tenant_id,
        campus_id=current_campus_id,
    )
    has_governance_alerts = PermissionService.has_permission(
        request.user,
        "audit_logs.read",
        tenant_id=current_tenant_id,
        campus_id=current_campus_id,
    )

    active_academic_year = None
    active_term = None
    active_grading_period = None
    active_grading_period_auto_advance = False
    if current_tenant_id:
        active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(
            tenant_id=current_tenant_id
        )
        if current_campus_id and active_term:
            active_grading_period = AcademicGovernanceService.resolve_active_grading_period(
                tenant_id=current_tenant_id,
                campus_id=current_campus_id,
                term_id=active_term.id,
                now=now,
            )
            active_grading_period_auto_advance = AcademicGovernanceService.is_active_grading_period_auto_advance_enabled(
                tenant_id=current_tenant_id,
                default=True,
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
            "tenants": AdminScopeService.active_scoped_tenants(request).count(),
            "campuses": AdminScopeService.active_scoped_campuses(request).count(),
            "users": _scoped_users_queryset(request).count(),
            "audit_logs": _scoped_audit_queryset(request).count(),
        },
        "has_import_read": has_import_read,
        "has_users_read": has_users_read,
        "has_system_settings_update": has_system_settings_update,
        "has_faculty_assignments_read": has_faculty_assignments_read,
        "import_stats": import_stats,
        "active_user_sessions": active_user_sessions,
        "active_user_count": len(active_user_sessions),
        "active_academic_year": active_academic_year,
        "active_term": active_term,
        "active_grading_period": active_grading_period,
        "active_grading_period_auto_advance": active_grading_period_auto_advance,
        "has_grading_period_lock": has_grading_period_lock,
        "lock_monitor": lock_monitor,
        "has_governance_alerts": has_governance_alerts,
        "governance_alerts": _governance_alert_rows(request, limit=20) if has_governance_alerts else [],
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/dashboard.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.read")
def faculty_activity_monitor_view(request):
    now = timezone.now()
    is_print_mode = request.GET.get("print") == "1"
    term_options = AdminScopeService.active_scoped_terms(request).order_by("-academic_year__start_date", "sequence_no")
    campus_options = AdminScopeService.active_scoped_campuses(request).order_by("code")
    department_options = AdminScopeService.active_scoped_departments(request).order_by("name")

    selected_term_id = _safe_int(request.GET.get("term_id"))
    selected_campus_id = _safe_int(request.GET.get("campus_id"))
    selected_department_id = _safe_int(request.GET.get("department_id"))
    selected_faculty_id = _safe_int(request.GET.get("faculty_user_id"))
    faculty_q = (request.GET.get("faculty_q") or "").strip()
    window_code = (request.GET.get("window") or "7d").strip().lower()

    selected_term = term_options.filter(id=selected_term_id).first() if selected_term_id else None
    if selected_term is None:
        current_tenant_id = getattr(request, "scope", {}).get("tenant_id")
        active_term = None
        if current_tenant_id:
            _active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=current_tenant_id)
        selected_term = active_term if active_term and term_options.filter(id=active_term.id).exists() else term_options.first()
        selected_term_id = getattr(selected_term, "id", None)

    monitor_window = _resolve_monitor_window(window_code=window_code, selected_term=selected_term, now=now)

    assignments_qs = AdminScopeService.scoped_faculty_assignments(request).filter(
        is_active=True,
        response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
    )
    if selected_term_id:
        assignments_qs = assignments_qs.filter(offering__term_id=selected_term_id)
    if selected_campus_id:
        assignments_qs = assignments_qs.filter(offering__campus_id=selected_campus_id)
    assignments = list(
        assignments_qs.select_related(
            "faculty_user",
            "faculty_user__default_campus",
            "faculty_user__default_department",
            "offering",
            "offering__course",
            "offering__section",
            "offering__term",
            "offering__academic_year",
            "offering__campus",
            "offering__department",
        ).order_by(
            "faculty_user__last_name",
            "faculty_user__first_name",
            "offering__course__code",
            "offering__section__code",
        )
    )
    if selected_department_id:
        selected_department_ids = AdminScopeService.expand_department_filter_ids(
            selected_department_id,
            campus_id=selected_campus_id,
        )
        assignments = [
            assignment
            for assignment in assignments
            if _faculty_assignment_scope_snapshot(assignment)["department_id"] in selected_department_ids
        ]

    faculty_ids = sorted({assignment.faculty_user_id for assignment in assignments})
    faculty_qs = User.objects.filter(id__in=faculty_ids, is_active=True).order_by("last_name", "first_name", "username")
    faculty_candidates = faculty_qs
    if faculty_q:
        faculty_candidates = faculty_candidates.filter(
            Q(username__icontains=faculty_q)
            | Q(email__icontains=faculty_q)
            | Q(first_name__icontains=faculty_q)
            | Q(last_name__icontains=faculty_q)
        )
    selected_faculty = faculty_qs.filter(id=selected_faculty_id).first() if selected_faculty_id else None

    offering_map = {assignment.offering_id: assignment.offering for assignment in assignments}
    faculty_assignment_map = defaultdict(list)
    for assignment in assignments:
        faculty_assignment_map[assignment.faculty_user_id].append(assignment)

    offering_ids = list(offering_map.keys())
    activity_counts = {}
    score_counts = {}
    submission_counts = {}
    activity_to_offering = {}
    if offering_ids:
        activity_counts = {
            row["offering_id"]: row
            for row in GradeActivity.objects.filter(offering_id__in=offering_ids, is_active=True)
            .values("offering_id")
            .annotate(total=Count("id"), last_activity_at=Max("updated_at"))
        }
        score_counts = {
            row["activity__offering_id"]: row
            for row in StudentActivityScore.objects.filter(
                activity__offering_id__in=offering_ids,
                activity__is_active=True,
                is_active=True,
            )
            .values("activity__offering_id")
            .annotate(total=Count("id"), last_score_at=Max("updated_at"))
        }
        submission_counts = {
            row["offering_id"]: row
            for row in GradeSubmission.objects.filter(offering_id__in=offering_ids)
            .values("offering_id")
            .annotate(
                submitted=Count("id", filter=Q(status=GradeSubmission.Status.SUBMITTED)),
                reopened=Count("id", filter=Q(status=GradeSubmission.Status.REOPENED)),
                last_submission_at=Max("updated_at"),
            )
        }
        activity_to_offering = {
            row["id"]: row["offering_id"]
            for row in GradeActivity.objects.filter(offering_id__in=offering_ids).values("id", "offering_id")
        }

    relevant_logs = list(
        AuditLog.objects.filter(
            actor_user_id__in=faculty_ids,
            portal=AuditLog.Portal.FACULTY,
            created_at__gte=monitor_window["start"],
            created_at__lte=monitor_window["end"],
        )
        .filter(
            Q(action="LOGIN_SUCCESS", entity_type="User")
            | Q(entity_type__in=[
                "GradeActivity",
                "StudentActivityScore",
                "AttendanceSession",
                "AttendanceRecord",
                "GradeSubmission",
                "GradeCorrectionRequest",
                "Enrollment",
            ])
        )
        .order_by("-created_at")
    )

    faculty_list = list(faculty_qs)
    visible_faculty_list = list(faculty_candidates)

    metrics_by_faculty = {}
    for faculty in faculty_list:
        metrics_by_faculty[faculty.id] = {
            "faculty": faculty,
            "assigned_classes": len(faculty_assignment_map.get(faculty.id, [])),
            "last_login_at": faculty.last_login,
            "login_count": 0,
            "activities_created": 0,
            "activity_updates": 0,
            "scores_saved": 0,
            "score_update_events": 0,
            "attendance_updates": 0,
            "submissions": 0,
            "reopens": 0,
            "corrections_filed": 0,
            "classlist_updates": 0,
            "gradebook_update_events": 0,
            "last_gradebook_update_at": None,
            "recent_logs": [],
        }

    def _resolve_log_offering(log):
        if log.entity_type == "GradeActivity":
            payload = log.after_json or log.before_json or {}
            return payload.get("offering_id")
        if log.entity_type == "StudentActivityScore":
            activity_id = None
            if isinstance(log.metadata_json, dict):
                activity_id = log.metadata_json.get("activity_id")
            if not activity_id:
                activity_id = log.entity_id
            try:
                return activity_to_offering.get(int(activity_id))
            except (TypeError, ValueError):
                return None
        if log.entity_type == "GradeSubmission":
            payload = log.after_json or log.before_json or {}
            return payload.get("offering_id")
        if log.entity_type == "Enrollment":
            payload = log.after_json or log.before_json or {}
            return payload.get("offering_id") or payload.get("course_offering_id")
        return None

    for log in relevant_logs:
        bucket = metrics_by_faculty.get(log.actor_user_id)
        if not bucket:
            continue
        if log.action == "LOGIN_SUCCESS" and log.entity_type == "User":
            bucket["login_count"] += 1
            continue

        if len(bucket["recent_logs"]) < 8:
            bucket["recent_logs"].append(log)
        if not bucket["last_gradebook_update_at"] or log.created_at > bucket["last_gradebook_update_at"]:
            bucket["last_gradebook_update_at"] = log.created_at

        if log.entity_type == "GradeActivity":
            if log.action == "CREATE":
                bucket["activities_created"] += 1
            else:
                bucket["activity_updates"] += 1
            bucket["gradebook_update_events"] += 1
        elif log.entity_type == "StudentActivityScore":
            saved_count = 1
            if isinstance(log.metadata_json, dict):
                try:
                    saved_count = int(log.metadata_json.get("saved_count") or 1)
                except (TypeError, ValueError):
                    saved_count = 1
            bucket["scores_saved"] += saved_count
            bucket["score_update_events"] += 1
            bucket["gradebook_update_events"] += 1
        elif log.entity_type in {"AttendanceSession", "AttendanceRecord"}:
            bucket["attendance_updates"] += 1
            bucket["gradebook_update_events"] += 1
        elif log.entity_type == "GradeSubmission":
            if log.action == "SUBMIT":
                bucket["submissions"] += 1
            elif log.action == "REOPEN":
                bucket["reopens"] += 1
            bucket["gradebook_update_events"] += 1
        elif log.entity_type == "GradeCorrectionRequest":
            if log.action == "CREATE":
                bucket["corrections_filed"] += 1
            bucket["gradebook_update_events"] += 1
        elif log.entity_type == "Enrollment":
            bucket["classlist_updates"] += 1
            bucket["gradebook_update_events"] += 1

    monitor_rows = []
    for faculty in visible_faculty_list:
        bucket = metrics_by_faculty[faculty.id]
        faculty_assignments = faculty_assignment_map.get(faculty.id, [])
        classes_with_no_activity = 0
        classes_with_no_scores = 0
        classes_with_recent_updates = 0
        for assignment in faculty_assignments:
            offering_id = assignment.offering_id
            offering_activity_total = int((activity_counts.get(offering_id) or {}).get("total") or 0)
            offering_score_total = int((score_counts.get(offering_id) or {}).get("total") or 0)
            if offering_activity_total <= 0:
                classes_with_no_activity += 1
            else:
                classes_with_recent_updates += 1
            if offering_score_total <= 0:
                classes_with_no_scores += 1
        bucket["classes_with_no_activity"] = classes_with_no_activity
        bucket["classes_with_no_scores"] = classes_with_no_scores
        bucket["classes_with_recent_updates"] = classes_with_recent_updates
        bucket["classes_without_recent_updates"] = max(bucket["assigned_classes"] - classes_with_recent_updates, 0)
        bucket["flags"] = _faculty_activity_flags(bucket, window_start=monitor_window["start"])
        bucket["status"] = _faculty_activity_status(bucket, window_start=monitor_window["start"])
        monitor_rows.append(bucket)

    monitor_rows.sort(
        key=lambda row: (
            {"danger": 0, "warning": 1, "secondary": 2, "success": 3}.get(row["status"]["variant"], 9),
            row["faculty"].last_name or "",
            row["faculty"].first_name or "",
            row["faculty"].username or "",
        )
    )

    summary_cards = [
        {
            "label": "Faculty Monitored",
            "value": len(monitor_rows),
            "meta": monitor_window["label"],
        },
        {
            "label": "Active in Window",
            "value": sum(1 for row in monitor_rows if row["status"]["label"] == "Active"),
            "meta": "Faculty logging in and updating gradebooks.",
        },
        {
            "label": "No Login",
            "value": sum(1 for row in monitor_rows if row["status"]["label"] == "No Login"),
            "meta": "No faculty login recorded in the selected window.",
        },
        {
            "label": "No Activity Created",
            "value": sum(1 for row in monitor_rows if row["activities_created"] <= 0),
            "meta": "No new grade activities created in the window.",
        },
        {
            "label": "No Grade Encoding",
            "value": sum(1 for row in monitor_rows if row["scores_saved"] <= 0),
            "meta": "No score-save action recorded in the window.",
        },
        {
            "label": "Needs Follow-up",
            "value": sum(
                1
                for row in monitor_rows
                if row["status"]["label"] in {"No Login", "No Gradebook Update", "Needs Follow-up"}
            ),
            "meta": "Faculty requiring AC/CAO follow-up.",
        },
        {
            "label": "Flagged Classes",
            "value": sum(row["classes_with_no_activity"] + row["classes_with_no_scores"] for row in monitor_rows),
            "meta": "Classes still missing activity or score maintenance.",
        },
    ]

    overall_trend_buckets = _build_activity_trend_buckets(
        logs=relevant_logs,
        start=monitor_window["start"],
        end=monitor_window["end"],
    )

    selected_faculty_detail = None
    if selected_faculty and selected_faculty.id in metrics_by_faculty:
        faculty_row = metrics_by_faculty[selected_faculty.id]
        class_rows = []
        for assignment in faculty_assignment_map.get(selected_faculty.id, []):
            offering = assignment.offering
            offering_id = offering.id
            activity_row = activity_counts.get(offering_id) or {}
            score_row = score_counts.get(offering_id) or {}
            submission_row = submission_counts.get(offering_id) or {}
            class_rows.append(
                {
                    "offering": offering,
                    "label": _offering_monitor_label(offering),
                    "activities": int(activity_row.get("total") or 0),
                    "scores": int(score_row.get("total") or 0),
                    "submitted_periods": int(submission_row.get("submitted") or 0),
                    "reopened_periods": int(submission_row.get("reopened") or 0),
                    "last_activity_at": activity_row.get("last_activity_at"),
                    "last_score_at": score_row.get("last_score_at"),
                    "last_submission_at": submission_row.get("last_submission_at"),
                    "no_activity": int(activity_row.get("total") or 0) <= 0,
                    "no_scores": int(score_row.get("total") or 0) <= 0,
                }
            )
        class_rows.sort(key=lambda row: (row["offering"].course.code, row["offering"].section.code))

        recent_actions = []
        for log in faculty_row["recent_logs"]:
            offering_id = _resolve_log_offering(log)
            offering = offering_map.get(offering_id)
            recent_actions.append(
                {
                    "when": log.created_at,
                    "action": log.action.replace("_", " ").title(),
                    "entity_type": log.entity_type,
                    "offering_label": _offering_monitor_label(offering) if offering else "Faculty Account",
                }
            )

        selected_faculty_detail = {
            "row": faculty_row,
            "class_rows": class_rows,
            "recent_actions": recent_actions,
            "trend_buckets": _build_activity_trend_buckets(
                logs=relevant_logs,
                start=monitor_window["start"],
                end=monitor_window["end"],
                actor_user_id=selected_faculty.id,
            ),
            "weekly_comparison_buckets": _build_week_over_week_buckets(
                logs=relevant_logs,
                end=monitor_window["end"],
                actor_user_id=selected_faculty.id,
                week_count=6,
            ),
        }

    context = {
        "title": "Faculty Activity Monitor",
        "term_options": term_options,
        "campus_options": campus_options,
        "department_options": department_options,
        "faculty_candidates": faculty_candidates,
        "selected_term_id": selected_term_id,
        "selected_term": selected_term,
        "selected_campus_id": selected_campus_id,
        "selected_department_id": selected_department_id,
        "selected_faculty_id": selected_faculty_id,
        "selected_faculty": selected_faculty,
        "faculty_q": faculty_q,
        "window_options": [
            {"code": "7d", "label": "Last 7 Days"},
            {"code": "30d", "label": "Last 30 Days"},
            {"code": "term", "label": "Current Term"},
        ],
        "selected_window_code": monitor_window["code"],
        "monitor_window": monitor_window,
        "summary_cards": summary_cards,
        "monitor_rows": monitor_rows,
        "overall_trend_buckets": overall_trend_buckets,
        "selected_faculty_detail": selected_faculty_detail,
        "grade_prediction_enabled": FeatureSettingsService.can_user_access_grade_prediction(
            user=request.user,
            tenant_id=getattr(request, "scope", {}).get("tenant_id"),
        ),
        "is_print_mode": is_print_mode,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/academics/faculty_activity_monitor.html", context)


@portal_required("ADMIN")
@permission_required("grading_analytics.read")
def grading_analytics_view(request):
    offerings_qs = AdminScopeService.scoped_course_offerings(request)
    campus_options = AdminScopeService.active_scoped_campuses(request).order_by("code")
    academic_year_options = AdminScopeService.active_scoped_academic_years(request).order_by("-start_date")
    term_options = AdminScopeService.active_scoped_terms(request).order_by("-academic_year__start_date", "sequence_no")

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
    missing_template_offering_ids = set()
    tenant_threshold_cache = {}
    profile_threshold_offerings = 0
    template_threshold_offerings = 0
    tenant_threshold_offerings = 0

    def _tenant_passing_threshold(offering):
        if offering.tenant_id not in tenant_threshold_cache:
            tenant_raw = SystemSettingService.get(
                "PASSING_GRADE_THRESHOLD",
                tenant_id=offering.tenant_id,
                default="75",
            )
            tenant_threshold_cache[offering.tenant_id] = GradingGovernanceService._round(
                _to_decimal(tenant_raw, Decimal("75.00"))
            )
        return tenant_threshold_cache[offering.tenant_id]

    for offering in offerings:
        profile = FacultyGradingService.resolve_grading_profile_for_offering(offering)
        profile_threshold = None
        if profile and profile.passing_grade_threshold is not None:
            profile_threshold = GradingGovernanceService._round(Decimal(profile.passing_grade_threshold))
        try:
            template = FacultyGradingService.resolve_template_for_offering(offering)
        except ValidationError:
            template = None
            missing_template_offering_ids.add(offering.id)
        if profile_threshold is not None:
            offering_threshold_map[offering.id] = profile_threshold
            offering_threshold_source_map[offering.id] = f"Profile {profile.profile_code}"
            profile_threshold_offerings += 1
            continue
        template_threshold = None
        if template and template.passing_grade_threshold is not None:
            template_threshold = GradingGovernanceService._round(Decimal(template.passing_grade_threshold))
        if template_threshold is not None:
            offering_threshold_map[offering.id] = template_threshold
            offering_threshold_source_map[offering.id] = f"Template {template.code}"
            template_threshold_offerings += 1
            continue
        offering_threshold_map[offering.id] = _tenant_passing_threshold(offering)
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
        "template_threshold_offerings": template_threshold_offerings,
        "tenant_threshold_offerings": tenant_threshold_offerings,
        "missing_template_offerings": len(missing_template_offering_ids),
        "threshold_policy": "Profile threshold -> Template threshold -> Tenant PASSING_GRADE_THRESHOLD -> 75.00",
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
                "missing_template": offering.id in missing_template_offering_ids,
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
                "missing_template": offering.id in missing_template_offering_ids,
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
@permission_required("grade_distribution_monitor.read")
def grade_distribution_monitor_view(request):
    context = GradeDistributionMonitorService.build_context(request)
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="grade_distribution_monitor.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "Faculty",
                "Campus",
                "Department",
                "Course",
                "Section",
                "Term",
                "Period",
                "Level",
                "Activity",
                "Graded Count",
                "Class Average",
                "90-100 %",
                "80-89 %",
                "75-79 %",
                "Below Passing %",
                "Exact 100 %",
                "Highest",
                "Lowest",
                "Spread",
                "Department Average",
                "Subject Average",
                "Flags",
            ]
        )
        for row in context["rows"]:
            writer.writerow(
                [
                    row["faculty_name"],
                    row["campus"],
                    row["department"],
                    f'{row["course_code"]} - {row["course_title"]}',
                    row["section"],
                    f'{row["school_year"]} / {row["term"]}',
                    row["period"],
                    row["level_label"],
                    row["activity_title"],
                    row["graded_count"],
                    row["average"] or "",
                    row["high_pct"],
                    row["band_80_89_pct"],
                    row["band_75_79_pct"],
                    row["below_passing_pct"],
                    row["exact_100_pct"],
                    row["highest"] or "",
                    row["lowest"] or "",
                    row["spread"] or "",
                    row["department_average"] or "",
                    row["subject_average"] or "",
                    ", ".join(flag["label"] for flag in row["flags"]),
                ]
            )
        return response

    paginator = Paginator(context["rows"], 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_query = GradeDistributionMonitorService.sanitized_query(request)
    page_query.pop("page", None)
    context["page_obj"] = page_obj
    context["rows"] = page_obj.object_list
    context["page_query"] = page_query.urlencode()
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/grade_distribution_monitor.html", context)


@portal_required("ADMIN")
def admin_guide_view(request):
    context = {
        "title": "Admin Portal User Guide",
        "show_production_incident_response": _user_has_role_code(request.user, "SUPER_ADMIN") or request.user.is_superuser,
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
@permission_required("system_settings.update")
def active_grading_period_settings_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

    campus_queryset = AdminScopeService.scoped_campuses(request).filter(tenant_id=tenant_id).order_by("name")
    term_queryset = (
        AdminScopeService.scoped_terms(request)
        .filter(tenant_id=tenant_id)
        .select_related("academic_year")
        .order_by("-academic_year__start_date", "sequence_no", "name")
    )
    scope_campus_id = getattr(request, "scope", {}).get("campus_id")
    _active_ay, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
    selected_campus_id = _safe_int(request.GET.get("campus_id")) or scope_campus_id
    selected_term_id = _safe_int(request.GET.get("term_id")) or (active_term.id if active_term else None)
    selected_campus = campus_queryset.filter(id=selected_campus_id).first()
    selected_term = term_queryset.filter(id=selected_term_id).first()

    period_queryset = TenantTermGradingPeriod.objects.none()
    if selected_term:
        period_queryset = AcademicGovernanceService.get_term_grading_periods(
            tenant_id=tenant_id,
            term_id=selected_term.id,
        )

    active_setting = None
    if selected_campus and selected_term:
        active_setting = AcademicGovernanceService.resolve_active_grading_period(
            tenant_id=tenant_id,
            campus_id=selected_campus.id,
            term_id=selected_term.id,
            now=timezone.now(),
        )

    auto_advance_enabled = AcademicGovernanceService.is_active_grading_period_auto_advance_enabled(
        tenant_id=tenant_id,
        default=True,
    )

    active_form = ActiveGradingPeriodSettingForm(
        prefix="active",
        campus_queryset=campus_queryset,
        term_queryset=term_queryset,
        period_queryset=period_queryset,
        initial={
            "campus": selected_campus.id if selected_campus else None,
            "term": selected_term.id if selected_term else None,
            "period": active_setting.period_id if active_setting else None,
            "remarks": active_setting.remarks if active_setting else None,
            "auto_advance_enabled": auto_advance_enabled,
        },
    )
    period_form = TenantTermGradingPeriodForm(prefix="period")
    _style_form(active_form)
    _style_form(period_form)

    if request.method == "POST":
        action = request.POST.get("form_action")
        if action == "save_active_period":
            active_form = ActiveGradingPeriodSettingForm(
                request.POST,
                prefix="active",
                campus_queryset=campus_queryset,
                term_queryset=term_queryset,
                period_queryset=AcademicGovernanceService.get_term_grading_periods(
                    tenant_id=tenant_id,
                    term_id=_safe_int(request.POST.get("active-term")),
                ),
            )
            _style_form(active_form)
            if active_form.is_valid():
                selected_campus = active_form.cleaned_data["campus"]
                selected_term = active_form.cleaned_data["term"]
                selected_period = active_form.cleaned_data.get("period")
                auto_advance_enabled = active_form.cleaned_data.get("auto_advance_enabled", False)
                current_setting = AcademicGovernanceService.resolve_active_grading_period(
                    tenant_id=tenant_id,
                    campus_id=selected_campus.id,
                    term_id=selected_term.id,
                    now=timezone.now(),
                )
                before = {
                    "campus_code": selected_campus.code,
                    "term_code": selected_term.code,
                    "active_period_code": current_setting.period.code if current_setting else None,
                    "auto_advance_enabled": AcademicGovernanceService.is_active_grading_period_auto_advance_enabled(
                        tenant_id=tenant_id,
                        default=True,
                    ),
                }
                AcademicGovernanceService.set_active_grading_period_auto_advance_enabled(
                    tenant_id=tenant_id,
                    enabled=auto_advance_enabled,
                )
                setting = AcademicGovernanceService.set_active_grading_period(
                    tenant_id=tenant_id,
                    campus=selected_campus,
                    term=selected_term,
                    period=selected_period,
                    actor=request.user,
                    remarks=active_form.cleaned_data.get("remarks"),
                    auto_advanced_from_deadline=False,
                )
                after = {
                    "campus_code": selected_campus.code,
                    "term_code": selected_term.code,
                    "active_period_code": setting.period.code if setting else None,
                    "auto_advance_enabled": auto_advance_enabled,
                }
                AuditService.log_event(
                    action="UPDATE_SYSTEM_SETTING",
                    portal="ADMIN",
                    entity_type="ActiveGradingPeriodSetting",
                    entity_id=f"tenant:{tenant_id}:campus:{selected_campus.id}:term:{selected_term.id}",
                    actor=request.user,
                    tenant=tenant_id,
                    campus=selected_campus.id,
                    before_data=before,
                    after_data=after,
                    metadata={
                        "setting_keys": [AcademicGovernanceService.ACTIVE_GRADING_PERIOD_AUTO_ADVANCE_KEY],
                    },
                    request=request,
                )
                if setting:
                    messages.success(
                        request,
                        f"Active grading period set to {setting.period.name} ({setting.period.code}) for {selected_campus.code} / {selected_term.code}.",
                    )
                else:
                    messages.success(request, "Active grading period cleared for the selected campus and term.")
                return redirect(
                    f"{reverse('admin_portal:active_grading_period_settings')}?{urlencode({'campus_id': selected_campus.id, 'term_id': selected_term.id})}"
                )
        elif action == "add_period":
            period_form = TenantTermGradingPeriodForm(request.POST, prefix="period")
            _style_form(period_form)
            selected_term = term_queryset.filter(id=_safe_int(request.POST.get("selected_term_id"))).first()
            selected_campus = campus_queryset.filter(id=_safe_int(request.POST.get("selected_campus_id"))).first() or selected_campus
            if not selected_term:
                messages.error(request, "Select a term first before adding a grading period.")
            elif period_form.is_valid():
                period_row = period_form.save(commit=False)
                period_row.tenant_id = tenant_id
                period_row.term = selected_term
                duplicate_exists = TenantTermGradingPeriod.objects.filter(
                    tenant_id=tenant_id,
                    term=selected_term,
                    code__iexact=period_row.code,
                ).exclude(id=period_row.id).exists()
                if duplicate_exists:
                    period_form.add_error("code", "This period code already exists for the selected term.")
                else:
                    period_row.save()
                    AuditService.log_event(
                        action="CREATE",
                        portal="ADMIN",
                        entity_type="TenantTermGradingPeriod",
                        entity_id=period_row.id,
                        actor=request.user,
                        tenant=tenant_id,
                        campus=selected_campus.id if selected_campus else None,
                        after_data=model_before_after(period_row),
                        request=request,
                    )
                    messages.success(
                        request,
                        f"Grading period {period_row.name} ({period_row.code}) added for {selected_term.code}.",
                    )
                    redirect_params = {"term_id": selected_term.id}
                    if selected_campus:
                        redirect_params["campus_id"] = selected_campus.id
                    return redirect(f"{reverse('admin_portal:active_grading_period_settings')}?{urlencode(redirect_params)}")
        elif action == "seed_standard_periods":
            selected_term = term_queryset.filter(id=_safe_int(request.POST.get("selected_term_id"))).first()
            selected_campus = campus_queryset.filter(id=_safe_int(request.POST.get("selected_campus_id"))).first() or selected_campus
            if not selected_term:
                messages.error(request, "Select a term first before loading the standard period set.")
            else:
                created_rows = AcademicGovernanceService.seed_standard_term_periods(
                    tenant_id=tenant_id,
                    term=selected_term,
                )
                if created_rows:
                    messages.success(
                        request,
                        f"Loaded {len(created_rows)} standard grading period(s) for {selected_term.code}.",
                    )
                else:
                    messages.info(request, "All standard grading periods already exist for the selected term.")
                redirect_params = {"term_id": selected_term.id}
                if selected_campus:
                    redirect_params["campus_id"] = selected_campus.id
                return redirect(f"{reverse('admin_portal:active_grading_period_settings')}?{urlencode(redirect_params)}")
        elif action == "toggle_period":
            period_row = get_object_or_404(
                TenantTermGradingPeriod,
                id=_safe_int(request.POST.get("period_id")),
                tenant_id=tenant_id,
            )
            before = model_before_after(period_row)
            period_row.is_active = not period_row.is_active
            period_row.save(update_fields=["is_active", "updated_at"])
            AuditService.log_event(
                action="UPDATE",
                portal="ADMIN",
                entity_type="TenantTermGradingPeriod",
                entity_id=period_row.id,
                actor=request.user,
                tenant=tenant_id,
                campus=_safe_int(request.POST.get("selected_campus_id")),
                before_data=before,
                after_data=model_before_after(period_row),
                request=request,
            )
            messages.success(
                request,
                f"Grading period {period_row.name} is now {'active' if period_row.is_active else 'inactive'}.",
            )
            redirect_params = {"term_id": period_row.term_id}
            selected_campus_id = _safe_int(request.POST.get("selected_campus_id"))
            if selected_campus_id:
                redirect_params["campus_id"] = selected_campus_id
            return redirect(f"{reverse('admin_portal:active_grading_period_settings')}?{urlencode(redirect_params)}")

    active_period_rows = list(period_queryset)
    next_period = None
    if active_setting:
        next_period = (
            TenantTermGradingPeriod.objects.filter(
                tenant_id=tenant_id,
                term_id=selected_term.id,
                is_active=True,
                sequence_no__gt=active_setting.period.sequence_no,
            )
            .order_by("sequence_no", "id")
            .first()
        )

    context = {
        "title": "Active Grading Period",
        "active_form": active_form,
        "period_form": period_form,
        "campus_options": campus_queryset,
        "term_options": term_queryset,
        "selected_campus": selected_campus,
        "selected_term": selected_term,
        "active_setting": active_setting,
        "active_period_rows": active_period_rows,
        "next_period": next_period,
        "auto_advance_enabled": auto_advance_enabled,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/tools/active_grading_period.html", context)


@portal_required("ADMIN")
@permission_required("grading_governance_settings.update")
def correction_governance_settings_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

    current_correction_mode = GradingGovernanceService.get_correction_mode(tenant_id=tenant_id)
    tenant_obj = Tenant.objects.filter(id=tenant_id).first()
    department_qs = AdminScopeService.active_scoped_departments(request).filter(tenant_id=tenant_id)
    role_qs = Role.objects.filter(is_active=True).order_by("name")

    mode_form = CorrectionGovernanceSettingForm(
        initial={
            "correction_mode": current_correction_mode,
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
                SystemSettingService.set(
                    GradingGovernanceService.CORRECTION_MODE_KEY,
                    selected_correction_mode,
                    tenant_id=tenant_id,
                    value_type="STRING",
                    is_active=True,
                    description="Controls whether correction handling is manual-only or in-system request workflow.",
                )
                AuditService.log_event(
                    action="UPDATE_SYSTEM_SETTING",
                    portal="ADMIN",
                    entity_type="SystemSetting",
                    entity_id=f"tenant:{tenant_id}:correction-mode",
                    actor=request.user,
                    tenant=tenant_id,
                    campus=getattr(request, "scope", {}).get("campus_id"),
                    before_data={
                        "correction_mode": current_correction_mode,
                    },
                    after_data={
                        "correction_mode": selected_correction_mode,
                    },
                    metadata={
                        "setting_keys": [
                            GradingGovernanceService.CORRECTION_MODE_KEY,
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
    current_campus_id = getattr(request, "scope", {}).get("campus_id")
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

    campus_queryset = AdminScopeService.scoped_campuses(request).filter(tenant_id=tenant_id).order_by("code", "name")
    term_queryset = AdminScopeService.scoped_terms(request).filter(tenant_id=tenant_id).order_by(
        "-academic_year__start_date",
        "sequence_no",
    )
    faculty_queryset = (
        User.objects.filter(
            id__in=AdminScopeService.scoped_faculty_users(request),
            is_active=True,
        )
        .order_by("last_name", "first_name", "username")
    )
    _active_academic_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
    selected_term_id = _safe_int(
        request.POST.get("class_master_list_term") if request.method == "POST" else request.GET.get("term_id")
    ) or (active_term.id if active_term else None)
    selected_term = term_queryset.filter(id=selected_term_id).first()
    selected_faculty_id = _safe_int(
        request.POST.get("class_master_list_faculty") if request.method == "POST" else request.GET.get("faculty_user_id")
    )
    selected_faculty = faculty_queryset.filter(id=selected_faculty_id).first()
    offering_queryset = AdminScopeService.scoped_course_offerings(request).filter(tenant_id=tenant_id)
    if current_campus_id:
        offering_queryset = offering_queryset.filter(campus_id=current_campus_id)
    if selected_term:
        offering_queryset = offering_queryset.filter(term_id=selected_term.id)
    else:
        offering_queryset = offering_queryset.none()
    if selected_faculty:
        offering_queryset = offering_queryset.filter(
            faculty_assignments__faculty_user_id=selected_faculty.id,
            faculty_assignments__is_active=True,
        )
    offering_queryset = offering_queryset.prefetch_related(
        Prefetch(
            "faculty_assignments",
            queryset=FacultyAssignment.objects.filter(is_active=True)
            .select_related("faculty_user")
            .order_by("faculty_user__last_name", "faculty_user__first_name"),
        )
    ).distinct()

    def _offering_with_faculty_label(obj):
        faculty_names = []
        faculty_assignment_manager = getattr(obj, "faculty_assignments", None)
        assignments = faculty_assignment_manager.all() if hasattr(faculty_assignment_manager, "all") else []
        for assignment in assignments:
            faculty_user = getattr(assignment, "faculty_user", None)
            if not faculty_user:
                continue
            faculty_name = (getattr(faculty_user, "full_name", "") or "").strip() or faculty_user.username
            if faculty_name and faculty_name not in faculty_names:
                faculty_names.append(faculty_name)
        faculty_suffix = f" ({', '.join(faculty_names)})" if faculty_names else ""
        return (
            f"{obj.course.title} ({obj.course.code}) | "
            f"{obj.section.name} ({obj.section.code})"
            f"{faculty_suffix}"
        )

    offering_labels = {offering.id: _offering_with_faculty_label(offering) for offering in offering_queryset}
    offering_queryset = offering_queryset.order_by(
        "course__code",
        "section__code",
        "id",
    )
    if request.method == "POST":
        selected_offering_ids = [
            offering_id
            for offering_id in request.POST.getlist("class_master_list_offering")
            if _safe_int(offering_id)
        ]
    else:
        selected_offering_ids = request.GET.getlist("offering_id")
    selected_offerings = offering_queryset.filter(id__in=selected_offering_ids)
    role_queryset = Role.objects.filter(is_active=True).order_by("name")

    current_report_enabled = FeatureSettingsService.is_correction_official_report_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_user_signatures_enabled = FeatureSettingsService.is_user_signatures_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_user_signatures_final_clearance_enabled = FeatureSettingsService.is_user_signature_final_clearance_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_user_signatures_correction_report_enabled = FeatureSettingsService.is_user_signature_correction_report_enabled(
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
    current_assignment_reminders_enabled = FeatureSettingsService.is_faculty_assignment_reminders_enabled(
        tenant_id=tenant_id,
        default=True,
    )
    current_assignment_auto_expire_enabled = FeatureSettingsService.is_faculty_assignment_auto_expire_enabled(
        tenant_id=tenant_id,
        default=True,
    )
    current_assignment_primary_default_enabled = FeatureSettingsService.is_faculty_assignment_primary_default_enabled(
        tenant_id=tenant_id,
        default=True,
    )
    current_faculty_reminder_center_enabled = FeatureSettingsService.is_faculty_reminder_center_enabled(
        tenant_id=tenant_id,
        default=True,
    )
    current_faculty_reminder_email_enabled = FeatureSettingsService.is_faculty_reminder_email_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_faculty_memo_center_enabled = FeatureSettingsService.is_faculty_memo_center_enabled(
        tenant_id=tenant_id,
        default=True,
    )
    current_faculty_quick_tour_enabled = FeatureSettingsService.is_faculty_quick_tour_enabled(
        tenant_id=tenant_id,
        default=True,
    )
    current_submission_non_compliance_notice_enabled = (
        FeatureSettingsService.is_submission_non_compliance_notice_enabled(
            tenant_id=tenant_id,
            default=False,
        )
    )
    current_submission_non_compliance_notice_interval_days = (
        FeatureSettingsService.get_submission_non_compliance_notice_interval_days(
            tenant_id=tenant_id,
            default=3,
        )
    )
    current_submission_non_compliance_head_role_codes = (
        FeatureSettingsService.get_submission_non_compliance_head_role_codes(tenant_id=tenant_id)
    )
    current_submission_non_compliance_hr_recipients = (
        FeatureSettingsService.get_submission_non_compliance_hr_recipients(tenant_id=tenant_id)
    )
    current_enrollment_ownership_mode = EnrollmentService.get_enrollment_mode(tenant_id)
    current_enrollment_student_mode = BulkImportService.get_enrollment_student_mode(tenant_id)
    current_faculty_drp_allowed_through_period = EnrollmentService.get_faculty_drp_allowed_through_period(tenant_id)
    current_enrollment_override_map = EnrollmentService.get_enrollment_mode_overrides(tenant_id)
    selected_override_modes = {
        current_enrollment_override_map.get(str(offering.id), "") for offering in selected_offerings
    }
    if len(selected_override_modes) == 1:
        current_selected_offering_override_mode = selected_override_modes.pop()
    else:
        current_selected_offering_override_mode = ""
    current_login_lockout_enabled = FeatureSettingsService.is_login_lockout_enabled(
        tenant_id=tenant_id,
        default=True,
    )
    current_login_lockout_max_attempts = FeatureSettingsService.get_login_lockout_max_attempts(
        tenant_id=tenant_id,
        default=5,
    )
    current_login_lockout_window_minutes = FeatureSettingsService.get_login_lockout_window_minutes(
        tenant_id=tenant_id,
        default=15,
    )
    current_login_lockout_duration_minutes = FeatureSettingsService.get_login_lockout_duration_minutes(
        tenant_id=tenant_id,
        default=15,
    )
    current_login_email_otp_enabled = FeatureSettingsService.is_login_email_otp_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_login_email_otp_expiry_minutes = FeatureSettingsService.get_login_email_otp_expiry_minutes(
        tenant_id=tenant_id,
        default=10,
    )
    current_session_timeout_minutes = FeatureSettingsService.get_session_timeout_minutes(
        tenant_id=tenant_id,
        default=max((getattr(settings, "SESSION_COOKIE_AGE", 3600) or 3600) // 60, 1),
    )
    current_response_window_days = FeatureSettingsService.get_faculty_assignment_response_window_days(
        tenant_id=tenant_id,
        default=3,
    )
    current_first_reminder_days = FeatureSettingsService.get_faculty_assignment_first_reminder_days(
        tenant_id=tenant_id,
        default=1,
    )
    current_repeat_reminder_days = FeatureSettingsService.get_faculty_assignment_repeat_reminder_days(
        tenant_id=tenant_id,
        default=1,
    )
    current_grade_prediction_enabled = FeatureSettingsService.is_grade_prediction_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_grade_prediction_role_codes = FeatureSettingsService.get_grade_prediction_role_codes(tenant_id=tenant_id)
    current_grade_prediction_what_if_enabled = FeatureSettingsService.is_grade_prediction_what_if_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_grade_prediction_what_if_role_codes = FeatureSettingsService.get_grade_prediction_what_if_role_codes(
        tenant_id=tenant_id
    )
    current_grade_prediction_at_risk_enabled = FeatureSettingsService.is_grade_prediction_at_risk_enabled(
        tenant_id=tenant_id,
        default=True,
    )
    current_grade_prediction_show_best_case = FeatureSettingsService.show_grade_prediction_best_case(
        tenant_id=tenant_id,
        default=True,
    )
    current_grade_prediction_show_worst_case = FeatureSettingsService.show_grade_prediction_worst_case(
        tenant_id=tenant_id,
        default=True,
    )
    current_grade_prediction_show_target_needed = FeatureSettingsService.show_grade_prediction_target_needed(
        tenant_id=tenant_id,
        default=True,
    )
    current_grade_prediction_default_assumption = FeatureSettingsService.get_grade_prediction_default_assumption(
        tenant_id=tenant_id,
        default="IGNORE_MISSING",
    )
    current_faculty_official_period_grades_after_deadline = (
        FeatureSettingsService.show_faculty_official_period_grades_after_deadline(
            tenant_id=tenant_id,
            default=False,
        )
    )
    current_faculty_official_period_grades_after_submission = (
        FeatureSettingsService.show_faculty_official_period_grades_after_submission(
            tenant_id=tenant_id,
            default=False,
        )
    )
    current_faculty_official_final_grades_after_deadline = (
        FeatureSettingsService.show_faculty_official_final_grades_after_deadline(
            tenant_id=tenant_id,
            default=False,
        )
    )
    current_grade_distribution_settings = GradeDistributionMonitorService._threshold_settings(request)
    current_grade_distribution_audit_settings = {
        "high_grade_band_min": str(current_grade_distribution_settings["high_grade_band_min"]),
        "high_grade_band_max": str(current_grade_distribution_settings["high_grade_band_max"]),
        "high_grade_concentration_threshold_percent": str(
            current_grade_distribution_settings["high_grade_concentration_threshold_percent"]
        ),
        "exact_100_threshold_percent": str(current_grade_distribution_settings["exact_100_threshold_percent"]),
        "low_variation_threshold": str(current_grade_distribution_settings["low_variation_threshold"]),
        "minimum_student_count_for_flag": int(current_grade_distribution_settings["minimum_student_count_for_flag"]),
    }
    current_student_portal_enabled = FeatureSettingsService.is_student_portal_enabled(
        tenant_id=tenant_id,
        default=False,
    )
    current_student_portal_period_grades_after_submission = (
        FeatureSettingsService.show_student_portal_period_grades_after_submission(
            tenant_id=tenant_id,
            default=True,
        )
    )
    current_student_portal_final_grades_after_submission = (
        FeatureSettingsService.show_student_portal_final_grades_after_submission(
            tenant_id=tenant_id,
            default=True,
        )
    )
    current_student_portal_attendance_details_enabled = FeatureSettingsService.show_student_portal_attendance_details(
        tenant_id=tenant_id,
        default=True,
    )

    form = ConfigurableFeatureSettingForm(
        request.POST or None,
        initial={
            "student_portal_enabled": current_student_portal_enabled,
            "student_portal_period_grades_after_submission": current_student_portal_period_grades_after_submission,
            "student_portal_final_grades_after_submission": current_student_portal_final_grades_after_submission,
            "student_portal_attendance_details_enabled": current_student_portal_attendance_details_enabled,
            "correction_official_report_enabled": current_report_enabled,
            "user_signatures_enabled": current_user_signatures_enabled,
            "user_signatures_final_clearance_enabled": current_user_signatures_final_clearance_enabled,
            "user_signatures_correction_report_enabled": current_user_signatures_correction_report_enabled,
            "correction_submission_approval_email_enabled": current_submission_email_enabled,
            "correction_submission_approval_email_roles": role_queryset.filter(code__in=current_submission_email_role_codes),
            "correction_registrar_auto_email_enabled": current_auto_email_enabled,
            "correction_registrar_auto_email_roles": role_queryset.filter(code__in=current_role_codes),
            "correction_registrar_default_recipients": ", ".join(current_default_recipients),
            "faculty_assignment_reminders_enabled": current_assignment_reminders_enabled,
            "faculty_assignment_auto_expire_enabled": current_assignment_auto_expire_enabled,
            "faculty_assignment_primary_default_enabled": current_assignment_primary_default_enabled,
            "faculty_reminder_center_enabled": current_faculty_reminder_center_enabled,
            "faculty_reminder_email_enabled": current_faculty_reminder_email_enabled,
            "faculty_memo_center_enabled": current_faculty_memo_center_enabled,
            "faculty_quick_tour_enabled": current_faculty_quick_tour_enabled,
            "submission_non_compliance_notice_enabled": current_submission_non_compliance_notice_enabled,
            "submission_non_compliance_notice_interval_days": current_submission_non_compliance_notice_interval_days,
            "submission_non_compliance_head_roles": role_queryset.filter(
                code__in=current_submission_non_compliance_head_role_codes
            ),
            "submission_non_compliance_hr_recipients": ", ".join(current_submission_non_compliance_hr_recipients),
            "grade_distribution_high_grade_band_min": current_grade_distribution_settings["high_grade_band_min"],
            "grade_distribution_high_grade_band_max": current_grade_distribution_settings["high_grade_band_max"],
            "grade_distribution_high_grade_concentration_threshold_percent": current_grade_distribution_settings[
                "high_grade_concentration_threshold_percent"
            ],
            "grade_distribution_exact_100_threshold_percent": current_grade_distribution_settings[
                "exact_100_threshold_percent"
            ],
            "grade_distribution_low_variation_threshold": current_grade_distribution_settings["low_variation_threshold"],
            "grade_distribution_minimum_student_count_for_flag": current_grade_distribution_settings[
                "minimum_student_count_for_flag"
            ],
            "enrollment_ownership_mode": current_enrollment_ownership_mode,
            "enrollment_student_mode": current_enrollment_student_mode,
            "faculty_drp_allowed_through_period": current_faculty_drp_allowed_through_period,
            "class_master_list_term": selected_term.id if selected_term else None,
            "class_master_list_faculty": selected_faculty.id if selected_faculty else None,
            "class_master_list_offering": [offering.id for offering in selected_offerings],
            "class_master_list_override_mode": current_selected_offering_override_mode,
            "login_lockout_enabled": current_login_lockout_enabled,
            "login_lockout_max_attempts": current_login_lockout_max_attempts,
            "login_lockout_window_minutes": current_login_lockout_window_minutes,
            "login_lockout_duration_minutes": current_login_lockout_duration_minutes,
            "login_email_otp_enabled": current_login_email_otp_enabled,
            "login_email_otp_expiry_minutes": current_login_email_otp_expiry_minutes,
            "session_timeout_minutes": current_session_timeout_minutes,
            "faculty_assignment_response_window_days": current_response_window_days,
            "faculty_assignment_first_reminder_days": current_first_reminder_days,
            "faculty_assignment_repeat_reminder_days": current_repeat_reminder_days,
            "grade_prediction_enabled": current_grade_prediction_enabled,
            "grade_prediction_roles": role_queryset.filter(code__in=current_grade_prediction_role_codes),
            "grade_prediction_what_if_enabled": current_grade_prediction_what_if_enabled,
            "grade_prediction_what_if_roles": role_queryset.filter(code__in=current_grade_prediction_what_if_role_codes),
            "grade_prediction_at_risk_enabled": current_grade_prediction_at_risk_enabled,
            "grade_prediction_show_best_case": current_grade_prediction_show_best_case,
            "grade_prediction_show_worst_case": current_grade_prediction_show_worst_case,
            "grade_prediction_show_target_needed": current_grade_prediction_show_target_needed,
            "grade_prediction_default_assumption": current_grade_prediction_default_assumption,
            "faculty_official_period_grades_after_deadline": current_faculty_official_period_grades_after_deadline,
            "faculty_official_period_grades_after_submission": current_faculty_official_period_grades_after_submission,
            "faculty_official_final_grades_after_deadline": current_faculty_official_final_grades_after_deadline,
        },
        role_queryset=role_queryset,
        campus_queryset=campus_queryset,
        campus_initial_map=current_campus_recipients,
        term_queryset=term_queryset,
        faculty_queryset=faculty_queryset,
        offering_queryset=offering_queryset,
    )
    _style_form(form)
    form.fields["class_master_list_offering"].label_from_instance = lambda obj: offering_labels.get(obj.id, obj.course.code)

    if request.method == "POST" and form.is_valid():
        selected_submission_email_role_codes = list(
            form.cleaned_data["correction_submission_approval_email_roles"].values_list("code", flat=True)
        )
        selected_role_codes = list(
            form.cleaned_data["correction_registrar_auto_email_roles"].values_list("code", flat=True)
        )
        selected_submission_non_compliance_head_role_codes = list(
            form.cleaned_data["submission_non_compliance_head_roles"].values_list("code", flat=True)
        )
        selected_grade_prediction_role_codes = list(
            form.cleaned_data["grade_prediction_roles"].values_list("code", flat=True)
        )
        selected_grade_prediction_what_if_role_codes = list(
            form.cleaned_data["grade_prediction_what_if_roles"].values_list("code", flat=True)
        )
        selected_default_recipients = form.cleaned_data["correction_registrar_default_recipient_list"]
        selected_campus_recipients = form.cleaned_data["correction_registrar_campus_recipient_map"]
        selected_submission_non_compliance_hr_recipients = form.cleaned_data[
            "submission_non_compliance_hr_recipient_list"
        ]
        selected_class_override_term = form.cleaned_data.get("class_master_list_term")
        selected_class_override_faculty = form.cleaned_data.get("class_master_list_faculty")
        selected_class_override_offerings = list(form.cleaned_data.get("class_master_list_offering") or [])
        selected_class_override_mode = form.cleaned_data.get("class_master_list_override_mode") or ""
        updated_enrollment_override_map = dict(current_enrollment_override_map)
        for selected_class_override_offering in selected_class_override_offerings:
            override_key = str(selected_class_override_offering.id)
            if selected_class_override_mode in {EnrollmentService.ADMIN_ONLY, EnrollmentService.FACULTY_ALLOWED}:
                updated_enrollment_override_map[override_key] = selected_class_override_mode
            else:
                updated_enrollment_override_map.pop(override_key, None)

        SystemSettingService.set(
            FeatureSettingsService.STUDENT_PORTAL_ENABLED_KEY,
            bool(form.cleaned_data["student_portal_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_PORTAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY,
            bool(form.cleaned_data["student_portal_period_grades_after_submission"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_PORTAL_FINAL_GRADES_AFTER_SUBMISSION_KEY,
            bool(form.cleaned_data["student_portal_final_grades_after_submission"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.STUDENT_PORTAL_ATTENDANCE_DETAILS_ENABLED_KEY,
            bool(form.cleaned_data["student_portal_attendance_details_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_OFFICIAL_REPORT_ENABLED_KEY,
            bool(form.cleaned_data["correction_official_report_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY,
            bool(form.cleaned_data["user_signatures_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_FINAL_CLEARANCE_ENABLED_KEY,
            bool(form.cleaned_data["user_signatures_final_clearance_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_CORRECTION_REPORT_ENABLED_KEY,
            bool(form.cleaned_data["user_signatures_correction_report_enabled"]),
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
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_ASSIGNMENT_REMINDERS_ENABLED_KEY,
            bool(form.cleaned_data["faculty_assignment_reminders_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_ASSIGNMENT_AUTO_EXPIRE_ENABLED_KEY,
            bool(form.cleaned_data["faculty_assignment_auto_expire_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_ASSIGNMENT_PRIMARY_DEFAULT_ENABLED_KEY,
            bool(form.cleaned_data["faculty_assignment_primary_default_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_REMINDER_CENTER_ENABLED_KEY,
            bool(form.cleaned_data["faculty_reminder_center_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_REMINDER_EMAIL_ENABLED_KEY,
            bool(form.cleaned_data["faculty_reminder_email_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_MEMO_CENTER_ENABLED_KEY,
            bool(form.cleaned_data["faculty_memo_center_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_QUICK_TOUR_ENABLED_KEY,
            bool(form.cleaned_data["faculty_quick_tour_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_NOTICE_ENABLED_KEY,
            bool(form.cleaned_data["submission_non_compliance_notice_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_NOTICE_INTERVAL_DAYS_KEY,
            int(form.cleaned_data["submission_non_compliance_notice_interval_days"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_HEAD_ROLE_CODES_KEY,
            selected_submission_non_compliance_head_role_codes,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_HR_RECIPIENTS_KEY,
            selected_submission_non_compliance_hr_recipients,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            GradeDistributionMonitorService.SETTING_KEYS["high_grade_band_min"],
            str(form.cleaned_data["grade_distribution_high_grade_band_min"]),
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            GradeDistributionMonitorService.SETTING_KEYS["high_grade_band_max"],
            str(form.cleaned_data["grade_distribution_high_grade_band_max"]),
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            GradeDistributionMonitorService.SETTING_KEYS["high_grade_concentration_threshold_percent"],
            str(form.cleaned_data["grade_distribution_high_grade_concentration_threshold_percent"]),
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            GradeDistributionMonitorService.SETTING_KEYS["exact_100_threshold_percent"],
            str(form.cleaned_data["grade_distribution_exact_100_threshold_percent"]),
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            GradeDistributionMonitorService.SETTING_KEYS["low_variation_threshold"],
            str(form.cleaned_data["grade_distribution_low_variation_threshold"]),
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            GradeDistributionMonitorService.SETTING_KEYS["minimum_student_count_for_flag"],
            int(form.cleaned_data["grade_distribution_minimum_student_count_for_flag"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            EnrollmentService.MODE_KEY,
            str(form.cleaned_data["enrollment_ownership_mode"]),
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            BulkImportService.ENROLLMENT_STUDENT_MODE_KEY,
            str(form.cleaned_data["enrollment_student_mode"]),
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            EnrollmentService.FACULTY_DRP_ALLOWED_THROUGH_PERIOD_KEY,
            str(form.cleaned_data["faculty_drp_allowed_through_period"]),
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            EnrollmentService.MODE_OVERRIDE_MAP_KEY,
            updated_enrollment_override_map,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_LOCKOUT_ENABLED_KEY,
            bool(form.cleaned_data["login_lockout_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_LOCKOUT_MAX_ATTEMPTS_KEY,
            int(form.cleaned_data["login_lockout_max_attempts"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_LOCKOUT_WINDOW_MINUTES_KEY,
            int(form.cleaned_data["login_lockout_window_minutes"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_LOCKOUT_DURATION_MINUTES_KEY,
            int(form.cleaned_data["login_lockout_duration_minutes"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_EMAIL_OTP_ENABLED_KEY,
            bool(form.cleaned_data["login_email_otp_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.LOGIN_EMAIL_OTP_EXPIRY_MINUTES_KEY,
            int(form.cleaned_data["login_email_otp_expiry_minutes"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.SESSION_TIMEOUT_MINUTES_KEY,
            int(form.cleaned_data["session_timeout_minutes"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_ASSIGNMENT_RESPONSE_WINDOW_DAYS_KEY,
            int(form.cleaned_data["faculty_assignment_response_window_days"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_ASSIGNMENT_FIRST_REMINDER_DAYS_KEY,
            int(form.cleaned_data["faculty_assignment_first_reminder_days"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_ASSIGNMENT_REPEAT_REMINDER_DAYS_KEY,
            int(form.cleaned_data["faculty_assignment_repeat_reminder_days"]),
            tenant_id=tenant_id,
            value_type="INT",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.GRADE_PREDICTION_ENABLED_KEY,
            bool(form.cleaned_data["grade_prediction_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.GRADE_PREDICTION_ROLE_CODES_KEY,
            selected_grade_prediction_role_codes,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.GRADE_PREDICTION_WHAT_IF_ENABLED_KEY,
            bool(form.cleaned_data["grade_prediction_what_if_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.GRADE_PREDICTION_WHAT_IF_ROLE_CODES_KEY,
            selected_grade_prediction_what_if_role_codes,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.GRADE_PREDICTION_AT_RISK_ENABLED_KEY,
            bool(form.cleaned_data["grade_prediction_at_risk_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.GRADE_PREDICTION_SHOW_BEST_CASE_KEY,
            bool(form.cleaned_data["grade_prediction_show_best_case"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.GRADE_PREDICTION_SHOW_WORST_CASE_KEY,
            bool(form.cleaned_data["grade_prediction_show_worst_case"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.GRADE_PREDICTION_SHOW_TARGET_NEEDED_KEY,
            bool(form.cleaned_data["grade_prediction_show_target_needed"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.GRADE_PREDICTION_DEFAULT_ASSUMPTION_KEY,
            str(form.cleaned_data["grade_prediction_default_assumption"]),
            tenant_id=tenant_id,
            value_type="STRING",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_DEADLINE_KEY,
            bool(form.cleaned_data["faculty_official_period_grades_after_deadline"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY,
            bool(form.cleaned_data["faculty_official_period_grades_after_submission"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_OFFICIAL_FINAL_GRADES_AFTER_DEADLINE_KEY,
            bool(form.cleaned_data["faculty_official_final_grades_after_deadline"]),
            tenant_id=tenant_id,
            value_type="BOOL",
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
                "user_signatures_enabled": current_user_signatures_enabled,
                "user_signatures_final_clearance_enabled": current_user_signatures_final_clearance_enabled,
                "user_signatures_correction_report_enabled": current_user_signatures_correction_report_enabled,
                "correction_submission_approval_email_enabled": current_submission_email_enabled,
                "correction_submission_approval_email_role_codes": current_submission_email_role_codes,
                "correction_registrar_auto_email_enabled": current_auto_email_enabled,
                "correction_registrar_auto_email_role_codes": current_role_codes,
                "correction_registrar_default_recipients": current_default_recipients,
                "correction_registrar_campus_recipients": current_campus_recipients,
                "faculty_assignment_reminders_enabled": current_assignment_reminders_enabled,
                "faculty_assignment_auto_expire_enabled": current_assignment_auto_expire_enabled,
                "faculty_assignment_primary_default_enabled": current_assignment_primary_default_enabled,
                "faculty_reminder_center_enabled": current_faculty_reminder_center_enabled,
                "faculty_reminder_email_enabled": current_faculty_reminder_email_enabled,
                "faculty_memo_center_enabled": current_faculty_memo_center_enabled,
                "faculty_quick_tour_enabled": current_faculty_quick_tour_enabled,
                "submission_non_compliance_notice_enabled": current_submission_non_compliance_notice_enabled,
                "submission_non_compliance_notice_interval_days": current_submission_non_compliance_notice_interval_days,
                "submission_non_compliance_head_role_codes": current_submission_non_compliance_head_role_codes,
                "submission_non_compliance_hr_recipients": current_submission_non_compliance_hr_recipients,
                "grade_distribution_settings": current_grade_distribution_audit_settings,
                "enrollment_ownership_mode": current_enrollment_ownership_mode,
                "enrollment_student_mode": current_enrollment_student_mode,
                "faculty_drp_allowed_through_period": current_faculty_drp_allowed_through_period,
                "enrollment_ownership_mode_by_offering": current_enrollment_override_map,
                "login_lockout_enabled": current_login_lockout_enabled,
                "login_lockout_max_attempts": current_login_lockout_max_attempts,
                "login_lockout_window_minutes": current_login_lockout_window_minutes,
                "login_lockout_duration_minutes": current_login_lockout_duration_minutes,
                "login_email_otp_enabled": current_login_email_otp_enabled,
                "login_email_otp_expiry_minutes": current_login_email_otp_expiry_minutes,
                "session_timeout_minutes": current_session_timeout_minutes,
                "faculty_assignment_response_window_days": current_response_window_days,
                "faculty_assignment_first_reminder_days": current_first_reminder_days,
                "faculty_assignment_repeat_reminder_days": current_repeat_reminder_days,
                "grade_prediction_enabled": current_grade_prediction_enabled,
                "grade_prediction_role_codes": current_grade_prediction_role_codes,
                "grade_prediction_what_if_enabled": current_grade_prediction_what_if_enabled,
                "grade_prediction_what_if_role_codes": current_grade_prediction_what_if_role_codes,
                "grade_prediction_at_risk_enabled": current_grade_prediction_at_risk_enabled,
                "grade_prediction_show_best_case": current_grade_prediction_show_best_case,
                "grade_prediction_show_worst_case": current_grade_prediction_show_worst_case,
                "grade_prediction_show_target_needed": current_grade_prediction_show_target_needed,
                "grade_prediction_default_assumption": current_grade_prediction_default_assumption,
                "faculty_official_period_grades_after_deadline": current_faculty_official_period_grades_after_deadline,
                "faculty_official_period_grades_after_submission": current_faculty_official_period_grades_after_submission,
                "faculty_official_final_grades_after_deadline": current_faculty_official_final_grades_after_deadline,
                "student_portal_enabled": current_student_portal_enabled,
                "student_portal_period_grades_after_submission": current_student_portal_period_grades_after_submission,
                "student_portal_final_grades_after_submission": current_student_portal_final_grades_after_submission,
                "student_portal_attendance_details_enabled": current_student_portal_attendance_details_enabled,
            },
            after_data={
                "student_portal_enabled": bool(form.cleaned_data["student_portal_enabled"]),
                "student_portal_period_grades_after_submission": bool(
                    form.cleaned_data["student_portal_period_grades_after_submission"]
                ),
                "student_portal_final_grades_after_submission": bool(
                    form.cleaned_data["student_portal_final_grades_after_submission"]
                ),
                "student_portal_attendance_details_enabled": bool(
                    form.cleaned_data["student_portal_attendance_details_enabled"]
                ),
                "correction_official_report_enabled": bool(form.cleaned_data["correction_official_report_enabled"]),
                "user_signatures_enabled": bool(form.cleaned_data["user_signatures_enabled"]),
                "user_signatures_final_clearance_enabled": bool(
                    form.cleaned_data["user_signatures_final_clearance_enabled"]
                ),
                "user_signatures_correction_report_enabled": bool(
                    form.cleaned_data["user_signatures_correction_report_enabled"]
                ),
                "correction_submission_approval_email_enabled": bool(
                    form.cleaned_data["correction_submission_approval_email_enabled"]
                ),
                "correction_submission_approval_email_role_codes": selected_submission_email_role_codes,
                "correction_registrar_auto_email_enabled": bool(form.cleaned_data["correction_registrar_auto_email_enabled"]),
                "correction_registrar_auto_email_role_codes": selected_role_codes,
                "correction_registrar_default_recipients": selected_default_recipients,
                "correction_registrar_campus_recipients": selected_campus_recipients,
                "faculty_assignment_reminders_enabled": bool(form.cleaned_data["faculty_assignment_reminders_enabled"]),
                "faculty_assignment_auto_expire_enabled": bool(form.cleaned_data["faculty_assignment_auto_expire_enabled"]),
                "faculty_assignment_primary_default_enabled": bool(
                    form.cleaned_data["faculty_assignment_primary_default_enabled"]
                ),
                "faculty_reminder_center_enabled": bool(form.cleaned_data["faculty_reminder_center_enabled"]),
                "faculty_reminder_email_enabled": bool(form.cleaned_data["faculty_reminder_email_enabled"]),
                "faculty_memo_center_enabled": bool(form.cleaned_data["faculty_memo_center_enabled"]),
                "faculty_quick_tour_enabled": bool(form.cleaned_data["faculty_quick_tour_enabled"]),
                "submission_non_compliance_notice_enabled": bool(
                    form.cleaned_data["submission_non_compliance_notice_enabled"]
                ),
                "submission_non_compliance_notice_interval_days": int(
                    form.cleaned_data["submission_non_compliance_notice_interval_days"]
                ),
                "submission_non_compliance_head_role_codes": selected_submission_non_compliance_head_role_codes,
                "submission_non_compliance_hr_recipients": selected_submission_non_compliance_hr_recipients,
                "grade_distribution_settings": {
                    "high_grade_band_min": str(form.cleaned_data["grade_distribution_high_grade_band_min"]),
                    "high_grade_band_max": str(form.cleaned_data["grade_distribution_high_grade_band_max"]),
                    "high_grade_concentration_threshold_percent": str(
                        form.cleaned_data["grade_distribution_high_grade_concentration_threshold_percent"]
                    ),
                    "exact_100_threshold_percent": str(
                        form.cleaned_data["grade_distribution_exact_100_threshold_percent"]
                    ),
                    "low_variation_threshold": str(form.cleaned_data["grade_distribution_low_variation_threshold"]),
                    "minimum_student_count_for_flag": int(
                        form.cleaned_data["grade_distribution_minimum_student_count_for_flag"]
                    ),
                },
                "enrollment_ownership_mode": str(form.cleaned_data["enrollment_ownership_mode"]),
                "enrollment_student_mode": str(form.cleaned_data["enrollment_student_mode"]),
                "faculty_drp_allowed_through_period": str(form.cleaned_data["faculty_drp_allowed_through_period"]),
                "enrollment_ownership_mode_by_offering": updated_enrollment_override_map,
                "selected_class_master_list_term": selected_class_override_term.code if selected_class_override_term else None,
                "selected_class_master_list_faculty": (
                    selected_class_override_faculty.full_name or selected_class_override_faculty.username
                    if selected_class_override_faculty
                    else None
                ),
                "selected_class_master_list_offerings": [
                    offering_labels.get(selected_class_override_offering.id, selected_class_override_offering.course.code)
                    for selected_class_override_offering in selected_class_override_offerings
                ],
                "selected_class_master_list_override_mode": selected_class_override_mode or "INHERIT_DEFAULT",
                "login_lockout_enabled": bool(form.cleaned_data["login_lockout_enabled"]),
                "login_lockout_max_attempts": int(form.cleaned_data["login_lockout_max_attempts"]),
                "login_lockout_window_minutes": int(form.cleaned_data["login_lockout_window_minutes"]),
                "login_lockout_duration_minutes": int(form.cleaned_data["login_lockout_duration_minutes"]),
                "login_email_otp_enabled": bool(form.cleaned_data["login_email_otp_enabled"]),
                "login_email_otp_expiry_minutes": int(form.cleaned_data["login_email_otp_expiry_minutes"]),
                "session_timeout_minutes": int(form.cleaned_data["session_timeout_minutes"]),
                "faculty_assignment_response_window_days": int(form.cleaned_data["faculty_assignment_response_window_days"]),
                "faculty_assignment_first_reminder_days": int(form.cleaned_data["faculty_assignment_first_reminder_days"]),
                "faculty_assignment_repeat_reminder_days": int(form.cleaned_data["faculty_assignment_repeat_reminder_days"]),
                "grade_prediction_enabled": bool(form.cleaned_data["grade_prediction_enabled"]),
                "grade_prediction_role_codes": selected_grade_prediction_role_codes,
                "grade_prediction_what_if_enabled": bool(form.cleaned_data["grade_prediction_what_if_enabled"]),
                "grade_prediction_what_if_role_codes": selected_grade_prediction_what_if_role_codes,
                "grade_prediction_at_risk_enabled": bool(form.cleaned_data["grade_prediction_at_risk_enabled"]),
                "grade_prediction_show_best_case": bool(form.cleaned_data["grade_prediction_show_best_case"]),
                "grade_prediction_show_worst_case": bool(form.cleaned_data["grade_prediction_show_worst_case"]),
                "grade_prediction_show_target_needed": bool(form.cleaned_data["grade_prediction_show_target_needed"]),
                "grade_prediction_default_assumption": str(form.cleaned_data["grade_prediction_default_assumption"]),
                "faculty_official_period_grades_after_deadline": bool(
                    form.cleaned_data["faculty_official_period_grades_after_deadline"]
                ),
                "faculty_official_period_grades_after_submission": bool(
                    form.cleaned_data["faculty_official_period_grades_after_submission"]
                ),
                "faculty_official_final_grades_after_deadline": bool(
                    form.cleaned_data["faculty_official_final_grades_after_deadline"]
                ),
            },
            metadata={
                "setting_keys": [
                    FeatureSettingsService.CORRECTION_OFFICIAL_REPORT_ENABLED_KEY,
                    FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY,
                    FeatureSettingsService.USER_SIGNATURES_FINAL_CLEARANCE_ENABLED_KEY,
                    FeatureSettingsService.USER_SIGNATURES_CORRECTION_REPORT_ENABLED_KEY,
                    FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
                    FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES_KEY,
                    FeatureSettingsService.CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED_KEY,
                    FeatureSettingsService.CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES_KEY,
                    FeatureSettingsService.CORRECTION_REGISTRAR_DEFAULT_RECIPIENTS_KEY,
                    FeatureSettingsService.CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS_KEY,
                    FeatureSettingsService.FACULTY_ASSIGNMENT_REMINDERS_ENABLED_KEY,
                    FeatureSettingsService.FACULTY_ASSIGNMENT_AUTO_EXPIRE_ENABLED_KEY,
                    FeatureSettingsService.FACULTY_ASSIGNMENT_PRIMARY_DEFAULT_ENABLED_KEY,
                    FeatureSettingsService.FACULTY_REMINDER_CENTER_ENABLED_KEY,
                    FeatureSettingsService.FACULTY_REMINDER_EMAIL_ENABLED_KEY,
                    FeatureSettingsService.FACULTY_MEMO_CENTER_ENABLED_KEY,
                    FeatureSettingsService.FACULTY_QUICK_TOUR_ENABLED_KEY,
                    FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_NOTICE_ENABLED_KEY,
                    FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_NOTICE_INTERVAL_DAYS_KEY,
                    FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_HEAD_ROLE_CODES_KEY,
                    FeatureSettingsService.SUBMISSION_NON_COMPLIANCE_HR_RECIPIENTS_KEY,
                    GradeDistributionMonitorService.SETTING_KEYS["high_grade_band_min"],
                    GradeDistributionMonitorService.SETTING_KEYS["high_grade_band_max"],
                    GradeDistributionMonitorService.SETTING_KEYS["high_grade_concentration_threshold_percent"],
                    GradeDistributionMonitorService.SETTING_KEYS["exact_100_threshold_percent"],
                    GradeDistributionMonitorService.SETTING_KEYS["low_variation_threshold"],
                    GradeDistributionMonitorService.SETTING_KEYS["minimum_student_count_for_flag"],
                    EnrollmentService.MODE_KEY,
                    BulkImportService.ENROLLMENT_STUDENT_MODE_KEY,
                    EnrollmentService.FACULTY_DRP_ALLOWED_THROUGH_PERIOD_KEY,
                    EnrollmentService.MODE_OVERRIDE_MAP_KEY,
                    FeatureSettingsService.LOGIN_LOCKOUT_ENABLED_KEY,
                    FeatureSettingsService.LOGIN_LOCKOUT_MAX_ATTEMPTS_KEY,
                    FeatureSettingsService.LOGIN_LOCKOUT_WINDOW_MINUTES_KEY,
                    FeatureSettingsService.LOGIN_LOCKOUT_DURATION_MINUTES_KEY,
                    FeatureSettingsService.LOGIN_EMAIL_OTP_ENABLED_KEY,
                    FeatureSettingsService.LOGIN_EMAIL_OTP_EXPIRY_MINUTES_KEY,
                    FeatureSettingsService.SESSION_TIMEOUT_MINUTES_KEY,
                    FeatureSettingsService.FACULTY_ASSIGNMENT_RESPONSE_WINDOW_DAYS_KEY,
                    FeatureSettingsService.FACULTY_ASSIGNMENT_FIRST_REMINDER_DAYS_KEY,
                    FeatureSettingsService.FACULTY_ASSIGNMENT_REPEAT_REMINDER_DAYS_KEY,
                    FeatureSettingsService.GRADE_PREDICTION_ENABLED_KEY,
                    FeatureSettingsService.GRADE_PREDICTION_ROLE_CODES_KEY,
                    FeatureSettingsService.GRADE_PREDICTION_WHAT_IF_ENABLED_KEY,
                    FeatureSettingsService.GRADE_PREDICTION_WHAT_IF_ROLE_CODES_KEY,
                    FeatureSettingsService.GRADE_PREDICTION_AT_RISK_ENABLED_KEY,
                    FeatureSettingsService.GRADE_PREDICTION_SHOW_BEST_CASE_KEY,
                    FeatureSettingsService.GRADE_PREDICTION_SHOW_WORST_CASE_KEY,
                    FeatureSettingsService.GRADE_PREDICTION_SHOW_TARGET_NEEDED_KEY,
                    FeatureSettingsService.GRADE_PREDICTION_DEFAULT_ASSUMPTION_KEY,
                    FeatureSettingsService.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_DEADLINE_KEY,
                    FeatureSettingsService.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY,
                    FeatureSettingsService.FACULTY_OFFICIAL_FINAL_GRADES_AFTER_DEADLINE_KEY,
                    FeatureSettingsService.STUDENT_PORTAL_ENABLED_KEY,
                    FeatureSettingsService.STUDENT_PORTAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY,
                    FeatureSettingsService.STUDENT_PORTAL_FINAL_GRADES_AFTER_SUBMISSION_KEY,
                    FeatureSettingsService.STUDENT_PORTAL_ATTENDANCE_DETAILS_ENABLED_KEY,
                ],
            },
            request=request,
        )
        messages.success(request, "Configuration management updated.")
        redirect_url = reverse("admin_portal:configurable_features_settings")
        redirect_params = {}
        if selected_class_override_term:
            redirect_params["term_id"] = selected_class_override_term.id
        if selected_class_override_faculty:
            redirect_params["faculty_user_id"] = selected_class_override_faculty.id
        if selected_class_override_offerings:
            redirect_params["offering_id"] = [offering.id for offering in selected_class_override_offerings]
        if redirect_params:
            redirect_url = f"{redirect_url}?{urlencode(redirect_params, doseq=True)}"
        return redirect(redirect_url)

    context = {
        "title": "Configuration Management",
        "form": form,
        "campus_count": campus_queryset.count(),
        "campus_field_rows": [{"campus": campus, "field": form[field_name]} for field_name, campus in form.campus_fields],
        "selected_term": selected_term,
        "selected_faculty": selected_faculty,
        "class_master_list_offerings": list(offering_queryset),
        "selected_offerings": list(selected_offerings),
        "selected_offering_ids": [offering.id for offering in selected_offerings],
        "selected_offering_override_mode": current_selected_offering_override_mode or "INHERIT_DEFAULT",
        "current_enrollment_override_map_size": len(current_enrollment_override_map),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/tools/configurable_features.html", context)


@portal_required("ADMIN")
@permission_required("actual_data_reset.run")
def actual_data_reset_view(request):
    result = None
    production_safety_error = ActualDataResetService.production_safety_error(request.user)
    if request.method == "POST":
        reset_scope = request.POST.get("reset_scope") or ""
        include_enrollments = request.POST.get("include_enrollments") == "on"
        valid_reset_scopes = {"full", "faculty_grade_transactions"}
        confirmation = (request.POST.get("confirmation_phrase") or "").strip()
        reset_reason = (request.POST.get("reset_reason") or "").strip()
        understood = request.POST.get("understood") == "on"
        if production_safety_error:
            messages.error(request, production_safety_error)
        elif reset_scope not in valid_reset_scopes:
            messages.error(request, "Data reset was not run. Choose a valid reset scope.")
        elif not reset_reason:
            messages.error(request, "Data reset was not run. Enter the operational reason for audit accountability.")
        else:
            expected_phrase = (
                ActualDataResetService.TRANSACTIONAL_CONFIRMATION_PHRASE
                if reset_scope == "faculty_grade_transactions"
                else ActualDataResetService.CONFIRMATION_PHRASE
            )
            if confirmation != expected_phrase or not understood:
                messages.error(
                    request,
                    "Data reset was not run. Tick the acknowledgement and type the exact confirmation phrase.",
                )
            else:
                if reset_scope == "faculty_grade_transactions":
                    preview = ActualDataResetService.transactional_preview(include_enrollments=include_enrollments)
                    result = ActualDataResetService.reset_faculty_grade_transactions(
                        include_enrollments=include_enrollments
                    )
                    success_message = "Faculty assignments and grading transaction reset completed."
                else:
                    preview = ActualDataResetService.preview()
                    result = ActualDataResetService.reset(
                        preserve_session_key=getattr(request.session, "session_key", None)
                    )
                    success_message = "Actual data reset completed."
                AuditService.log_event(
                    action="RESET",
                    portal="ADMIN",
                    entity_type="ActualDataReset",
                    actor=request.user,
                    after_data={
                        "deleted_table_count": len(result.get("deleted") or []),
                        "removed_files": result.get("removed_files", 0),
                    },
                    metadata={
                        "critical_action": True,
                        "reason": reset_reason,
                        "reset_scope": reset_scope,
                        "include_enrollments": include_enrollments if reset_scope == "faculty_grade_transactions" else None,
                        "confirmation_required": True,
                        "confirmation_phrase": expected_phrase,
                        "impact_summary": {
                            "delete_total": preview.get("delete_total", 0),
                            "tenant_settings_count": preview.get("tenant_settings_count", 0),
                            "users_kept": preview.get("users_count", 0),
                        },
                        "audit_export_path": result.get("audit_export_path"),
                        "audit_export_count": result.get("audit_export_count", 0),
                        "backup_path": result.get("backup_path"),
                        "backup_validation": result.get("backup_validation"),
                        "audit_export_validation": result.get("audit_export_validation"),
                    },
                    request=None,
                )
                messages.success(request, success_message)

    context = {
        "preview": ActualDataResetService.preview(),
        "transactional_preview": ActualDataResetService.transactional_preview(),
        "transactional_preview_with_enrollments": ActualDataResetService.transactional_preview(include_enrollments=True),
        "result": result,
        "production_safety_error": production_safety_error,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/tools/actual_data_reset.html", context)


@portal_required("ADMIN")
@permission_required("system_settings.update")
def template_governance_settings_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    if not tenant_id:
        messages.error(request, "Select a tenant scope first.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

    role_queryset = Role.objects.filter(is_active=True).order_by("name")
    current_snapshot = TemplateGovernanceWorkflowService.get_workflow_snapshot(tenant_id=tenant_id)
    current_stage_map = {row["code"]: row["role_codes"] for row in current_snapshot["stages"]}

    form = TemplateGovernanceSettingForm(
        request.POST or None,
        initial={
            "draft_roles": role_queryset.filter(
                code__in=current_stage_map.get(TemplateGovernanceWorkflowService.STAGE_DRAFT, [])
            ),
            "submit_roles": role_queryset.filter(
                code__in=current_stage_map.get(TemplateGovernanceWorkflowService.STAGE_SUBMIT_FOR_APPROVAL, [])
            ),
            "approval_review_roles": role_queryset.filter(
                code__in=current_stage_map.get(TemplateGovernanceWorkflowService.STAGE_APPROVAL_REVIEW, [])
            ),
            "publish_roles": role_queryset.filter(
                code__in=current_stage_map.get(TemplateGovernanceWorkflowService.STAGE_PUBLISH, [])
            ),
            "hotfix_request_roles": role_queryset.filter(
                code__in=current_stage_map.get(TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST, [])
            ),
            "hotfix_review_apply_roles": role_queryset.filter(
                code__in=current_stage_map.get(TemplateGovernanceWorkflowService.STAGE_HOTFIX_REVIEW_APPLY, [])
            ),
            "sequential_approval_enabled": current_snapshot["sequential_template_approval_enabled"],
            "approval_review_step_roles": role_queryset.filter(
                code__in=(current_snapshot.get("approval_steps") or [{}])[0].get("role_codes", [])
            ),
            "approval_final_step_roles": role_queryset.filter(
                code__in=(current_snapshot.get("approval_steps") or [{}, {}])[1].get("role_codes", [])
                if len(current_snapshot.get("approval_steps", [])) > 1
                else []
            ),
            "sequential_hotfix_enabled": current_snapshot["sequential_hotfix_enabled"],
            "hotfix_review_step_roles": role_queryset.filter(
                code__in=(current_snapshot.get("hotfix_steps") or [{}])[0].get("role_codes", [])
            ),
            "hotfix_apply_step_roles": role_queryset.filter(
                code__in=(current_snapshot.get("hotfix_steps") or [{}, {}])[1].get("role_codes", [])
                if len(current_snapshot.get("hotfix_steps", [])) > 1
                else []
            ),
            "require_approval_before_publish": current_snapshot["require_approval_before_publish"],
            "allow_same_user_submit_review": current_snapshot["allow_same_user_submit_review"],
            "allow_same_user_review_approve": current_snapshot["allow_same_user_review_approve"],
            "allow_same_user_review_publish": current_snapshot["allow_same_user_review_publish"],
            "allow_same_user_hotfix_request_apply": current_snapshot["allow_same_user_hotfix_request_apply"],
            "allow_same_user_hotfix_review_apply": current_snapshot["allow_same_user_hotfix_review_apply"],
        },
        role_queryset=role_queryset,
    )
    _style_form(form)

    if request.method == "POST" and form.is_valid():
        selected_draft_roles = list(form.cleaned_data["draft_roles"].values_list("code", flat=True))
        selected_submit_roles = list(form.cleaned_data["submit_roles"].values_list("code", flat=True))
        selected_review_roles = list(form.cleaned_data["approval_review_roles"].values_list("code", flat=True))
        selected_publish_roles = list(form.cleaned_data["publish_roles"].values_list("code", flat=True))
        selected_hotfix_request_roles = list(form.cleaned_data["hotfix_request_roles"].values_list("code", flat=True))
        selected_hotfix_review_apply_roles = list(
            form.cleaned_data["hotfix_review_apply_roles"].values_list("code", flat=True)
        )
        selected_approval_review_step_roles = list(
            form.cleaned_data["approval_review_step_roles"].values_list("code", flat=True)
        )
        selected_approval_final_step_roles = list(
            form.cleaned_data["approval_final_step_roles"].values_list("code", flat=True)
        )
        selected_hotfix_review_step_roles = list(
            form.cleaned_data["hotfix_review_step_roles"].values_list("code", flat=True)
        )
        selected_hotfix_apply_step_roles = list(
            form.cleaned_data["hotfix_apply_step_roles"].values_list("code", flat=True)
        )

        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[TemplateGovernanceWorkflowService.STAGE_DRAFT],
            selected_draft_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[
                TemplateGovernanceWorkflowService.STAGE_SUBMIT_FOR_APPROVAL
            ],
            selected_submit_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[
                TemplateGovernanceWorkflowService.STAGE_APPROVAL_REVIEW
            ],
            selected_review_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[TemplateGovernanceWorkflowService.STAGE_PUBLISH],
            selected_publish_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[
                TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST
            ],
            selected_hotfix_request_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[
                TemplateGovernanceWorkflowService.STAGE_HOTFIX_REVIEW_APPLY
            ],
            selected_hotfix_review_apply_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.SEQUENTIAL_APPROVAL_ENABLED_KEY,
            bool(form.cleaned_data["sequential_approval_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.APPROVAL_REVIEW_STEP_ROLE_CODES_KEY,
            selected_approval_review_step_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.APPROVAL_FINAL_STEP_ROLE_CODES_KEY,
            selected_approval_final_step_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.SEQUENTIAL_HOTFIX_ENABLED_KEY,
            bool(form.cleaned_data["sequential_hotfix_enabled"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.HOTFIX_REVIEW_STEP_ROLE_CODES_KEY,
            selected_hotfix_review_step_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.HOTFIX_APPLY_STEP_ROLE_CODES_KEY,
            selected_hotfix_apply_step_roles,
            tenant_id=tenant_id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.REQUIRE_APPROVAL_BEFORE_PUBLISH_KEY,
            bool(form.cleaned_data["require_approval_before_publish"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.ALLOW_SAME_USER_SUBMIT_REVIEW_KEY,
            bool(form.cleaned_data["allow_same_user_submit_review"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.ALLOW_SAME_USER_REVIEW_APPROVE_KEY,
            bool(form.cleaned_data["allow_same_user_review_approve"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.ALLOW_SAME_USER_REVIEW_PUBLISH_KEY,
            bool(form.cleaned_data["allow_same_user_review_publish"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.ALLOW_SAME_USER_HOTFIX_REQUEST_APPLY_KEY,
            bool(form.cleaned_data["allow_same_user_hotfix_request_apply"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.ALLOW_SAME_USER_HOTFIX_REVIEW_APPLY_KEY,
            bool(form.cleaned_data["allow_same_user_hotfix_review_apply"]),
            tenant_id=tenant_id,
            value_type="BOOL",
            is_active=True,
        )

        AuditService.log_event(
            action="UPDATE_SYSTEM_SETTING",
            portal="ADMIN",
            entity_type="SystemSetting",
            entity_id=f"tenant:{tenant_id}:template-governance",
            actor=request.user,
            tenant=tenant_id,
            campus=getattr(request, "scope", {}).get("campus_id"),
            before_data=current_snapshot,
            after_data={
                "require_approval_before_publish": bool(form.cleaned_data["require_approval_before_publish"]),
                "sequential_template_approval_enabled": bool(form.cleaned_data["sequential_approval_enabled"]),
                "sequential_hotfix_enabled": bool(form.cleaned_data["sequential_hotfix_enabled"]),
                "allow_same_user_submit_review": bool(form.cleaned_data["allow_same_user_submit_review"]),
                "allow_same_user_review_approve": bool(form.cleaned_data["allow_same_user_review_approve"]),
                "allow_same_user_review_publish": bool(form.cleaned_data["allow_same_user_review_publish"]),
                "allow_same_user_hotfix_request_apply": bool(
                    form.cleaned_data["allow_same_user_hotfix_request_apply"]
                ),
                "allow_same_user_hotfix_review_apply": bool(
                    form.cleaned_data["allow_same_user_hotfix_review_apply"]
                ),
                "stages": [
                    {
                        "code": TemplateGovernanceWorkflowService.STAGE_DRAFT,
                        "label": TemplateGovernanceWorkflowService.stage_label(
                            TemplateGovernanceWorkflowService.STAGE_DRAFT
                        ),
                        "role_codes": selected_draft_roles,
                    },
                    {
                        "code": TemplateGovernanceWorkflowService.STAGE_SUBMIT_FOR_APPROVAL,
                        "label": TemplateGovernanceWorkflowService.stage_label(
                            TemplateGovernanceWorkflowService.STAGE_SUBMIT_FOR_APPROVAL
                        ),
                        "role_codes": selected_submit_roles,
                    },
                    {
                        "code": TemplateGovernanceWorkflowService.STAGE_APPROVAL_REVIEW,
                        "label": TemplateGovernanceWorkflowService.stage_label(
                            TemplateGovernanceWorkflowService.STAGE_APPROVAL_REVIEW
                        ),
                        "role_codes": selected_review_roles,
                    },
                    {
                        "code": TemplateGovernanceWorkflowService.STAGE_PUBLISH,
                        "label": TemplateGovernanceWorkflowService.stage_label(
                            TemplateGovernanceWorkflowService.STAGE_PUBLISH
                        ),
                        "role_codes": selected_publish_roles,
                    },
                    {
                        "code": TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST,
                        "label": TemplateGovernanceWorkflowService.stage_label(
                            TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST
                        ),
                        "role_codes": selected_hotfix_request_roles,
                    },
                    {
                        "code": TemplateGovernanceWorkflowService.STAGE_HOTFIX_REVIEW_APPLY,
                        "label": TemplateGovernanceWorkflowService.stage_label(
                            TemplateGovernanceWorkflowService.STAGE_HOTFIX_REVIEW_APPLY
                        ),
                        "role_codes": selected_hotfix_review_apply_roles,
                    },
                ],
                "approval_steps": [
                    {
                        "code": TemplateGovernanceWorkflowService.STEP_TEMPLATE_REVIEW,
                        "label": "Template Review",
                        "role_codes": selected_approval_review_step_roles,
                    },
                    {
                        "code": TemplateGovernanceWorkflowService.STEP_TEMPLATE_APPROVAL,
                        "label": "Final Approval",
                        "role_codes": selected_approval_final_step_roles,
                    },
                ],
                "hotfix_steps": [
                    {
                        "code": TemplateGovernanceWorkflowService.STEP_HOTFIX_REVIEW,
                        "label": "Hotfix Review",
                        "role_codes": selected_hotfix_review_step_roles,
                    },
                    {
                        "code": TemplateGovernanceWorkflowService.STEP_HOTFIX_APPLY,
                        "label": "Hotfix Final Apply",
                        "role_codes": selected_hotfix_apply_step_roles,
                    },
                ],
            },
            metadata={
                "setting_keys": [
                    *TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS.values(),
                    TemplateGovernanceWorkflowService.SEQUENTIAL_APPROVAL_ENABLED_KEY,
                    TemplateGovernanceWorkflowService.APPROVAL_REVIEW_STEP_ROLE_CODES_KEY,
                    TemplateGovernanceWorkflowService.APPROVAL_FINAL_STEP_ROLE_CODES_KEY,
                    TemplateGovernanceWorkflowService.SEQUENTIAL_HOTFIX_ENABLED_KEY,
                    TemplateGovernanceWorkflowService.HOTFIX_REVIEW_STEP_ROLE_CODES_KEY,
                    TemplateGovernanceWorkflowService.HOTFIX_APPLY_STEP_ROLE_CODES_KEY,
                    TemplateGovernanceWorkflowService.REQUIRE_APPROVAL_BEFORE_PUBLISH_KEY,
                    TemplateGovernanceWorkflowService.ALLOW_SAME_USER_SUBMIT_REVIEW_KEY,
                    TemplateGovernanceWorkflowService.ALLOW_SAME_USER_REVIEW_APPROVE_KEY,
                    TemplateGovernanceWorkflowService.ALLOW_SAME_USER_REVIEW_PUBLISH_KEY,
                    TemplateGovernanceWorkflowService.ALLOW_SAME_USER_HOTFIX_REQUEST_APPLY_KEY,
                    TemplateGovernanceWorkflowService.ALLOW_SAME_USER_HOTFIX_REVIEW_APPLY_KEY,
                ],
            },
            request=request,
        )
        messages.success(request, "Template governance workflow updated.")
        return _redirect_back_or_default(request, "admin_portal:template_governance_settings")

    stage_cards = []
    for stage in current_snapshot["stages"]:
        stage_cards.append(
            {
                "label": stage["label"],
                "role_names": list(role_queryset.filter(code__in=stage["role_codes"]).values_list("name", flat=True)),
                "role_codes": stage["role_codes"],
            }
        )

    context = {
        "title": "Template Governance",
        "form": form,
        "stage_cards": stage_cards,
        "workflow_snapshot": current_snapshot,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/tools/template_governance.html", context)


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
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    default_timeout_minutes = max((getattr(settings, "SESSION_COOKIE_AGE", 3600) or 3600) // 60, 1)
    session_timeout = timedelta(
        minutes=FeatureSettingsService.get_session_timeout_minutes(
            tenant_id=tenant_id,
            default=default_timeout_minutes,
        )
    )
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
        activity_anchor = None
        if last_log and last_log.created_at:
            activity_anchor = last_log.created_at
        elif getattr(user, "last_login", None):
            activity_anchor = user.last_login

        effective_session_expires_at = session_obj.expire_date if session_obj else None
        if activity_anchor and session_timeout.total_seconds() > 0:
            policy_expires_at = activity_anchor + session_timeout
            if effective_session_expires_at is None or policy_expires_at < effective_session_expires_at:
                effective_session_expires_at = policy_expires_at

        if effective_session_expires_at and effective_session_expires_at <= now:
            continue

        activity_label = "No recent activity"
        if last_log:
            activity_label = last_log.route_name or f"{last_log.action} {last_log.entity_type}".strip()
        rows.append(
            {
                "user": user,
                "session_expires_at": effective_session_expires_at,
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
    context = {"q": q}
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="tenant")
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
    context = {"q": q}
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="campus")
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
    selected_tenant_id = _safe_int(request.GET.get("tenant_id"))
    selected_campus_id = _safe_int(request.GET.get("campus_id"))
    if selected_tenant_id:
        queryset = queryset.filter(tenant_id=selected_tenant_id)
    if selected_campus_id:
        queryset = queryset.filter(campus_id=selected_campus_id)
    if request.GET.get("parent_id"):
        parent_ids = AdminScopeService.expand_department_filter_ids(
            request.GET.get("parent_id"),
            campus_id=_safe_int(request.GET.get("campus_id")),
        )
        queryset = queryset.filter(Q(id__in=parent_ids) | Q(parent_id__in=parent_ids))
    if request.GET.get("operation_branch"):
        queryset = queryset.filter(operation_branch=request.GET.get("operation_branch"))
    if request.GET.get("unit_type"):
        queryset = queryset.filter(unit_type=request.GET.get("unit_type"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    parent_departments = AdminScopeService.scoped_departments(request).filter(parent__isnull=True)
    if selected_tenant_id:
        parent_departments = parent_departments.filter(tenant_id=selected_tenant_id)
    if selected_campus_id:
        parent_departments = parent_departments.filter(campus_id=selected_campus_id)

    context = {
        **_active_inactive_pages(request, queryset),
        "q": q,
        "operation_branch_choices": Department.OperationBranch.choices,
        "unit_type_choices": Department.UnitType.choices,
        "parent_departments": parent_departments.order_by("campus__code", "code"),
        "selected_campus_id": selected_campus_id,
    }
    _with_inactive_record_metadata(request, context, model_key="department")
    context.update(_scope_context(request))
    return render(request, "admin_portal/organization/department_list.html", context)


@portal_required("ADMIN")
@permission_required("departments.create")
def department_create_view(request):
    form = DepartmentForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        parent_queryset=AdminScopeService.scoped_departments(request),
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
        parent_queryset=AdminScopeService.scoped_departments(request),
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
    context = {"q": q}
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="program")
    context.update(_scope_context(request))
    return render(request, "admin_portal/organization/program_list.html", context)


@portal_required("ADMIN")
@permission_required("programs.create")
def program_create_view(request):
    form = ProgramForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.active_scoped_departments(request),
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
        department_queryset=AdminScopeService.active_scoped_departments(request),
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
    staff_filter = request.GET.get("is_staff", "").strip()
    if tenant_filter:
        queryset = queryset.filter(Q(default_tenant_id=tenant_filter) | Q(user_roles__tenant_id=tenant_filter)).distinct()
    if campus_filter:
        queryset = queryset.filter(Q(default_campus_id=campus_filter) | Q(user_roles__campus_id=campus_filter)).distinct()
    if staff_filter == "1":
        queryset = queryset.filter(is_staff=True)
    elif staff_filter == "0":
        queryset = queryset.filter(is_staff=False)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )
    inactive_users = list(queryset.filter(is_active=False).order_by("username"))
    _attach_inactive_user_metadata(request, inactive_users)
    context = {
        "active_users": queryset.filter(is_active=True).order_by("username"),
        "inactive_users": inactive_users,
        "q": q,
        "tenant_filter": tenant_filter,
        "campus_filter": campus_filter,
        "staff_filter": staff_filter,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/security/user_list.html", context)


@portal_required("ADMIN")
@permission_required("users.read")
def login_lockout_list_view(request):
    queryset = _scoped_login_lockout_queryset(request)
    portal_filter = request.GET.get("portal", "").strip().upper()
    status_filter = request.GET.get("status", "").strip().lower()
    q = request.GET.get("q", "").strip()
    now = timezone.now()

    if portal_filter in {"ADMIN", "FACULTY"}:
        queryset = queryset.filter(portal_code=portal_filter)
    if status_filter == "locked":
        queryset = queryset.filter(locked_until__gt=now)
    elif status_filter == "history":
        queryset = queryset.filter(Q(locked_until__isnull=True) | Q(locked_until__lte=now))
    if q:
        queryset = queryset.filter(
            Q(username__icontains=q)
            | Q(user__email__icontains=q)
            | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
        )

    base_queryset = _scoped_login_lockout_queryset(request)
    total_count = base_queryset.count()
    currently_locked_count = base_queryset.filter(locked_until__gt=now).count()
    admin_locked_count = base_queryset.filter(portal_code="ADMIN", locked_until__gt=now).count()
    faculty_locked_count = base_queryset.filter(portal_code="FACULTY", locked_until__gt=now).count()

    context = {
        "page_obj": _get_page(request, queryset),
        "portal_filter": portal_filter,
        "status_filter": status_filter,
        "q": q,
        "metric_cards": [
            {
                "label": "Tracked Lockout Records",
                "value": total_count,
                "meta": "All portal lockout state rows in your visible scope.",
            },
            {
                "label": "Currently Locked",
                "value": currently_locked_count,
                "meta": "Accounts temporarily blocked right now.",
            },
            {
                "label": "Admin Portal Locked",
                "value": admin_locked_count,
                "meta": "Locked Admin Portal sign-ins.",
            },
            {
                "label": "Faculty Portal Locked",
                "value": faculty_locked_count,
                "meta": "Locked Faculty Portal sign-ins.",
            },
        ],
        "now": now,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/security/login_lockout_list.html", context)


@portal_required("ADMIN")
@permission_required("users.update")
def login_lockout_unlock_view(request, lockout_id: int):
    if request.method != "POST":
        return redirect("admin_portal:login_lockout_list")
    lockout_state = get_object_or_404(_scoped_login_lockout_queryset(request), id=lockout_id)
    before = {
        "failed_attempt_count": lockout_state.failed_attempt_count,
        "locked_until": lockout_state.locked_until.isoformat() if lockout_state.locked_until else None,
        "last_failed_at": lockout_state.last_failed_at.isoformat() if lockout_state.last_failed_at else None,
    }
    lockout_state.failed_attempt_count = 0
    lockout_state.window_started_at = None
    lockout_state.last_failed_at = None
    lockout_state.locked_until = None
    lockout_state.save(update_fields=["failed_attempt_count", "window_started_at", "last_failed_at", "locked_until", "updated_at"])
    AuditService.log_event(
        action="LOGIN_LOCKOUT_UNLOCK",
        portal="ADMIN",
        entity_type="PortalLoginLockoutState",
        entity_id=lockout_state.id,
        actor=request.user,
        tenant=lockout_state.user.default_tenant_id if lockout_state.user_id else None,
        campus=lockout_state.user.default_campus_id if lockout_state.user_id else None,
        before_data=before,
        after_data={
            "failed_attempt_count": lockout_state.failed_attempt_count,
            "locked_until": None,
            "last_failed_at": None,
        },
        metadata={
            "username": lockout_state.username,
            "portal_code": lockout_state.portal_code,
        },
        request=request,
    )
    messages.success(
        request,
        f"Login lockout cleared for {lockout_state.username} ({lockout_state.portal_code.title()} Portal).",
    )
    return _redirect_back_or_default(request, "admin_portal:login_lockout_list")


@portal_required("ADMIN")
@permission_required("users.update")
def faculty_deactivation_schedule_view(request):
    faculty_ids = AdminScopeService.scoped_faculty_users(request)
    faculty_queryset = (
        User.objects.filter(id__in=faculty_ids, is_active=True)
        .select_related("default_tenant", "default_campus", "default_department")
        .order_by("last_name", "first_name", "username")
    )

    if request.method == "POST" and request.POST.get("action") == "cancel":
        schedule = get_object_or_404(
            UserDeactivationSchedule.objects.select_related("user"),
            id=request.POST.get("schedule_id"),
            status=UserDeactivationSchedule.Status.PENDING,
            user_id__in=faculty_ids,
        )
        try:
            UserDeactivationService.cancel(schedule=schedule, actor=request.user, request=request)
            messages.success(request, f"Scheduled deactivation cancelled for {schedule.user.username}.")
        except ValidationError as exc:
            messages.error(request, str(exc))
        return redirect("admin_portal:faculty_deactivation_schedule")

    form = FacultyDeactivationScheduleForm(
        request.POST or None,
        faculty_queryset=faculty_queryset,
    )
    _style_form(form)
    if request.method == "POST" and request.POST.get("action") != "cancel" and form.is_valid():
        schedule = UserDeactivationService.schedule(
            user=form.cleaned_data["user"],
            scheduled_for=form.cleaned_data["scheduled_for"],
            actor=request.user,
            reason=form.cleaned_data.get("reason"),
            request=request,
        )
        messages.success(
            request,
            f"{schedule.user.username} will be deactivated on {timezone.localtime(schedule.scheduled_for):%Y-%m-%d %H:%M}.",
        )
        return redirect("admin_portal:faculty_deactivation_schedule")

    pending_schedules = (
        UserDeactivationSchedule.objects.select_related(
            "user",
            "user__default_tenant",
            "user__default_campus",
            "scheduled_by_user",
        )
        .filter(status=UserDeactivationSchedule.Status.PENDING, user_id__in=faculty_ids)
        .order_by("scheduled_for", "user__username")
    )
    recent_schedules = (
        UserDeactivationSchedule.objects.select_related("user", "scheduled_by_user", "cancelled_by_user", "applied_by_user")
        .filter(user_id__in=faculty_ids)
        .exclude(status=UserDeactivationSchedule.Status.PENDING)
        .order_by("-updated_at")[:15]
    )
    context = {
        "form": form,
        "pending_schedules": pending_schedules,
        "recent_schedules": recent_schedules,
        "faculty_count": faculty_queryset.count(),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/security/faculty_deactivation_schedule.html", context)


@portal_required("ADMIN")
@permission_required("users.create")
def user_create_view(request):
    tenant_qs = AdminScopeService.scoped_tenants(request)
    campus_qs = AdminScopeService.scoped_campuses(request)
    department_qs = AdminScopeService.active_scoped_departments(request)
    initial_scope = {}
    if request.method != "POST":
        scoped_tenant_id = getattr(request, "scope", {}).get("tenant_id")
        scoped_campus_id = getattr(request, "scope", {}).get("campus_id")
        if scoped_tenant_id and tenant_qs.filter(id=scoped_tenant_id).exists():
            initial_scope["default_tenant"] = scoped_tenant_id
        if scoped_campus_id and campus_qs.filter(id=scoped_campus_id).exists():
            initial_scope["default_campus"] = scoped_campus_id
    form = UserCreateForm(
        request.POST or None,
        initial=initial_scope,
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
    department_qs = AdminScopeService.active_scoped_departments(request)
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
    department_qs = AdminScopeService.active_scoped_departments(request)

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
    context = {
        "target_user": user,
        "form": form,
        "active_assignments": assignments.filter(is_active=True),
        "inactive_assignments": assignments.filter(is_active=False),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/security/user_roles.html", context)


@portal_required("ADMIN")
@permission_required("roles.read")
def role_list_view(request):
    roles = (
        Role.objects.annotate(
            permission_count=Count("role_permissions", distinct=True),
            assignment_count=Count("user_roles", distinct=True),
        )
        .order_by("name")
    )
    context = {
        "active_roles": roles.filter(is_active=True),
        "inactive_roles": roles.filter(is_active=False),
    }
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
def role_delete_view(request, role_id: int):
    role = get_object_or_404(Role, id=role_id)
    if request.method != "POST":
        return redirect("admin_portal:role_list")

    confirmation = (request.POST.get("confirmation_code") or "").strip()
    if role.is_active:
        messages.error(request, "Only inactive roles can be hard-deleted.")
        return redirect("admin_portal:role_list")
    if role.is_system:
        messages.error(request, "System roles cannot be hard-deleted.")
        return redirect("admin_portal:role_list")
    if confirmation != role.code:
        messages.error(request, f"Role was not deleted. Type {role.code} exactly to confirm.")
        return redirect("admin_portal:role_list")

    before = model_before_after(role)
    permission_count = role.role_permissions.count()
    assignment_count = role.user_roles.count()
    role_code = role.code
    role_id_for_audit = role.id

    with transaction.atomic():
        RolePermission.objects.filter(role=role).delete()
        UserRole.objects.filter(role=role).delete()
        role.delete()

    AuditService.log_event(
        action="DELETE",
        portal="ADMIN",
        entity_type="Role",
        entity_id=role_id_for_audit,
        actor=request.user,
        before_data=before,
        metadata={
            "role_code": role_code,
            "deleted_role_permissions": permission_count,
            "deleted_user_role_assignments": assignment_count,
            "hard_delete": True,
        },
        request=request,
    )
    messages.success(request, f"Inactive role {role_code} was hard-deleted.")
    return redirect("admin_portal:role_list")


@portal_required("ADMIN")
@permission_required("roles.update")
def role_permissions_view(request, role_id: int):
    role = get_object_or_404(Role, id=role_id)
    form = RolePermissionsForm(request.POST or None, role=role)
    _style_form(form)
    critical_permission_impact = None
    if request.method == "POST" and form.is_valid():
        module_to_save = (request.POST.get("save_module") or "").strip()
        before = list(role.role_permissions.values_list("permission_id", flat=True))
        selected_permissions = set(form.cleaned_data["permissions"].values_list("id", flat=True))
        current_permissions = set(before)
        update_label = "all"

        if module_to_save:
            module_permission_ids = set(
                Permission.objects.filter(is_active=True, module=module_to_save).values_list("id", flat=True)
            )
            if not module_permission_ids:
                messages.error(request, "Permission section was not found.")
                return redirect("admin_portal:role_permissions", role_id=role.id)
            selected_module_permissions = selected_permissions & module_permission_ids
            current_module_permissions = current_permissions & module_permission_ids
            to_add = selected_module_permissions - current_module_permissions
            to_remove = current_module_permissions - selected_module_permissions
            update_label = module_to_save
        else:
            to_add = selected_permissions - current_permissions
            to_remove = current_permissions - selected_permissions

        critical_permission_impact = _role_permission_impact(role, to_add, to_remove)
        if critical_permission_impact["has_critical_change"]:
            if not (form.cleaned_data.get("change_reason") or "").strip():
                form.add_error("change_reason", "Enter the reason for changing critical role access.")
            if (form.cleaned_data.get("confirmation_phrase") or "").strip() != CRITICAL_ROLE_CONFIRMATION:
                form.add_error("confirmation_phrase", f"Type {CRITICAL_ROLE_CONFIRMATION} to confirm this critical access change.")

        if not form.errors:
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
                metadata={
                    "critical_action": critical_permission_impact["has_critical_change"],
                    "reason": (form.cleaned_data.get("change_reason") or "").strip(),
                    "confirmation_required": critical_permission_impact["has_critical_change"],
                    "confirmation_phrase": (
                        CRITICAL_ROLE_CONFIRMATION if critical_permission_impact["has_critical_change"] else ""
                    ),
                    "permission_section": update_label,
                    "added_permission_ids": sorted(to_add),
                    "removed_permission_ids": sorted(to_remove),
                    "added_permission_codes": critical_permission_impact["added_permission_codes"],
                    "removed_permission_codes": critical_permission_impact["removed_permission_codes"],
                    "impact_summary": critical_permission_impact,
                },
                request=request,
            )
            if module_to_save:
                messages.success(
                    request,
                    f"{module_to_save.replace('_', ' ').replace('.', ' ').title()} permissions updated.",
                )
                return redirect("admin_portal:role_permissions", role_id=role.id)
            messages.success(request, "Role permissions updated.")
            return _redirect_back_or_default(request, "admin_portal:role_list")

    def _title(value):
        return str(value or "").replace("_", " ").replace(".", " ").title()

    def _permission_label(permission):
        action_labels = {
            "access": "Access",
            "approve": "Approve",
            "create": "Create",
            "delete": "Delete",
            "import": "Import",
            "lock": "Lock",
            "publish": "Publish",
            "read": "View",
            "reopen": "Reopen",
            "review": "Review",
            "submit_for_approval": "Submit for Approval",
            "update": "Edit",
            "view_student_identity": "View Student Identity",
            "revert_before_deadline": "Revert Before Deadline",
        }
        return action_labels.get(permission.action, _title(permission.action))

    def _permission_description(permission):
        module_label = _title(permission.module)
        specific = {
            "admin_portal.access": "Allows the user to sign in to the Admin Portal.",
            "faculty_portal.access": "Allows the user to sign in to the Faculty Portal.",
            "dashboard.read": "Allows viewing dashboard summaries and quick actions.",
            "grading_analytics.read": "Allows viewing admin grading analytics.",
            "grade_distribution_monitor.read": "Allows viewing faculty grade distribution monitoring reports.",
            "faculty_analytics.read": "Allows viewing faculty-side analytics.",
            "gradebook.view_student_identity": "Allows authorized gradebook reviewers to see unmasked student numbers and names within their allowed scope.",
            "system_settings.update": "Allows changing tenant/system configuration settings.",
            "menus.update": "Allows changing portal navigation menu setup.",
            "audit_logs.read": "Allows viewing audit trail records.",
            "corrections.create": "Allows faculty to file grade correction petitions.",
            "corrections.review": "Allows reviewing grade correction petitions.",
            "grade_submissions.reopen": "Allows reopening submitted grade records when policy permits.",
            "grade_submissions.revert_before_deadline": "Allows reverting a submission before the deadline.",
            "grading_periods.lock": "Allows closing or locking grading periods.",
            "grading_periods.reopen": "Allows reopening locked grading periods.",
            "template_hotfixes.review": "Allows reviewing grading template hotfix requests.",
        }
        if permission.code in specific:
            return specific[permission.code]
        action_templates = {
            "access": f"Allows access to {module_label}.",
            "approve": f"Allows approving {module_label} records or workflows.",
            "create": f"Allows adding new {module_label} records.",
            "delete": f"Allows deleting or removing {module_label} records when available.",
            "import": f"Allows importing {module_label} records from a file.",
            "lock": f"Allows locking {module_label}.",
            "publish": f"Allows publishing {module_label}.",
            "read": f"Allows viewing {module_label} records and pages.",
            "reopen": f"Allows reopening {module_label} records when policy permits.",
            "review": f"Allows reviewing {module_label} requests or workflows.",
            "submit_for_approval": f"Allows submitting {module_label} for approval.",
            "update": f"Allows editing existing {module_label} records.",
            "revert_before_deadline": f"Allows reverting {module_label} before the configured deadline.",
        }
        return action_templates.get(permission.action, permission.description or f"Allows {_title(permission.action)} on {module_label}.")

    action_badge_classes = {
        "access": "text-bg-dark",
        "approve": "text-bg-primary",
        "create": "text-bg-success",
        "delete": "text-bg-danger",
        "import": "text-bg-info",
        "lock": "text-bg-warning",
        "publish": "text-bg-primary",
        "read": "text-bg-secondary",
        "reopen": "text-bg-warning",
        "review": "text-bg-info",
        "submit_for_approval": "text-bg-primary",
        "update": "text-bg-success",
        "revert_before_deadline": "text-bg-warning",
    }
    if request.method == "POST":
        selected_permission_ids = {int(value) for value in request.POST.getlist("permissions") if str(value).isdigit()}
    else:
        selected_permission_ids = set(role.role_permissions.values_list("permission_id", flat=True))

    permissions_by_module = []
    module_groups = {}
    for perm in Permission.objects.filter(is_active=True).order_by("module", "action", "code"):
        module_groups.setdefault(perm.module, []).append(perm)
    for module, perms in module_groups.items():
        permission_rows = []
        for perm in perms:
            permission_rows.append(
                {
                    "id": perm.id,
                    "code": perm.code,
                    "action": perm.action,
                    "action_label": _permission_label(perm),
                    "badge_class": action_badge_classes.get(perm.action, "text-bg-light"),
                    "description": _permission_description(perm),
                    "is_selected": perm.id in selected_permission_ids,
                    "is_critical": perm.code in CRITICAL_PERMISSION_CODES,
                }
            )
        permissions_by_module.append(
            {
                "key": module,
                "dom_id": f"module_{module.replace('.', '_').replace('-', '_')}",
                "label": _title(module),
                "permissions": permission_rows,
                "selected_count": sum(1 for item in permission_rows if item["is_selected"]),
                "total_count": len(permission_rows),
            }
        )

    context = {
        "role": role,
        "form": form,
        "permissions_by_module": permissions_by_module,
        "selected_permission_count": len(selected_permission_ids),
        "total_permission_count": Permission.objects.filter(is_active=True).count(),
        "critical_role_confirmation": CRITICAL_ROLE_CONFIRMATION,
        "critical_permission_codes": sorted(CRITICAL_PERMISSION_CODES),
        "critical_permission_impact": critical_permission_impact,
    }
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
@permission_required("audit_logs.read")
def recent_critical_actions_view(request):
    queryset = _scoped_audit_queryset(request).filter(CRITICAL_AUDIT_FILTER)
    action = request.GET.get("action", "").strip()
    entity_type = request.GET.get("entity_type", "").strip()
    q = request.GET.get("q", "").strip()
    if action:
        queryset = queryset.filter(action__icontains=action)
    if entity_type:
        queryset = queryset.filter(entity_type__icontains=entity_type)
    if q:
        queryset = queryset.filter(
            Q(entity_type__icontains=q)
            | Q(entity_id__icontains=q)
            | Q(actor_user__username__icontains=q)
            | Q(metadata_json__reason__icontains=q)
        )
    page_obj = _get_page(request, queryset, per_page=30)
    for row in page_obj.object_list:
        row.safe_reason = _critical_audit_reason(row.metadata_json)
        row.safe_impact_label = _critical_audit_impact_label(row.metadata_json)
        row.confirmation_required = bool((row.metadata_json or {}).get("confirmation_required"))
        row.anomaly_flags = _audit_anomaly_flags(row.metadata_json)
        row.max_anomaly_severity = (row.metadata_json or {}).get("max_anomaly_severity", "")
    context = {
        "page_obj": page_obj,
        "action": action,
        "entity_type": entity_type,
        "q": q,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/audit/recent_critical_actions.html", context)


@portal_required("ADMIN")
@permission_required("academic_years.read")
def academic_year_list_view(request):
    queryset = AdminScopeService.scoped_academic_years(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    context = {"q": q}
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="academic_year")
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
    if request.GET.get("term_type"):
        queryset = queryset.filter(term_type=request.GET.get("term_type"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q))
    context = {
        **_active_inactive_pages(request, queryset),
        "q": q,
        "term_type": request.GET.get("term_type", ""),
        "term_type_choices": Term.TermType.choices,
    }
    _with_inactive_record_metadata(request, context, model_key="term")
    context.update(_scope_context(request))
    context["academic_years"] = AdminScopeService.active_scoped_academic_years(request)
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
        queryset = queryset.filter(
            department_id__in=AdminScopeService.expand_department_filter_ids(
                request.GET.get("department_id"),
                campus_id=_safe_int(request.GET.get("campus_id")),
            )
        )
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(title__icontains=q))
    context = {"q": q}
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="course")
    context.update(_scope_context(request))
    context["departments"] = AdminScopeService.active_scoped_departments(request)
    return render(request, "admin_portal/academics/course_list.html", context)


@portal_required("ADMIN")
@permission_required("courses.create")
def course_create_view(request):
    form = CourseForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.active_scoped_departments(request),
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
        department_queryset=AdminScopeService.active_scoped_departments(request),
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

    terms_queryset = AdminScopeService.active_scoped_terms(request)
    if academic_year_filter:
        terms_queryset = terms_queryset.filter(academic_year_id=academic_year_filter)

    context = {
        **_active_inactive_pages(request, queryset),
        "q": q,
        "academic_year_filter": academic_year_filter or "",
        "term_filter": term_filter or "",
    }
    _with_inactive_record_metadata(request, context, model_key="section")
    context.update(_scope_context(request))
    context["programs"] = AdminScopeService.active_scoped_programs(request)
    context["academic_years"] = AdminScopeService.active_scoped_academic_years(request)
    context["terms"] = terms_queryset
    return render(request, "admin_portal/academics/section_list.html", context)


@portal_required("ADMIN")
@permission_required("sections.create")
def section_create_view(request):
    form = SectionForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.active_scoped_departments(request),
        program_queryset=AdminScopeService.active_scoped_programs(request),
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
        department_queryset=AdminScopeService.active_scoped_departments(request),
        program_queryset=AdminScopeService.active_scoped_programs(request),
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
        queryset = queryset.filter(
            department_id__in=AdminScopeService.expand_department_filter_ids(
                request.GET.get("department_id"),
                campus_id=_safe_int(request.GET.get("campus_id")),
            )
        )
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(course__code__icontains=q) | Q(section__code__icontains=q) | Q(schedule_text__icontains=q)
        )
    context = {"q": q}
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="offering")
    context.update(_scope_context(request))
    context["academic_years"] = AdminScopeService.active_scoped_academic_years(request)
    context["terms"] = AdminScopeService.active_scoped_terms(request)
    context["departments"] = AdminScopeService.active_scoped_departments(request)
    return render(request, "admin_portal/academics/offering_list.html", context)


@portal_required("ADMIN")
@permission_required("offerings.create")
def offering_create_view(request):
    form = CourseOfferingForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.active_scoped_departments(request),
        program_queryset=AdminScopeService.active_scoped_programs(request),
        academic_year_queryset=AdminScopeService.active_scoped_academic_years(request),
        term_queryset=AdminScopeService.active_scoped_terms(request),
        course_queryset=AdminScopeService.active_scoped_courses(request),
        section_queryset=AdminScopeService.active_scoped_sections(request),
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
        department_queryset=AdminScopeService.active_scoped_departments(request),
        program_queryset=AdminScopeService.active_scoped_programs(request),
        academic_year_queryset=AdminScopeService.active_scoped_academic_years(request),
        term_queryset=AdminScopeService.active_scoped_terms(request),
        course_queryset=AdminScopeService.active_scoped_courses(request),
        section_queryset=AdminScopeService.active_scoped_sections(request),
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
    FacultyAssignmentWorkflowService.expire_overdue_assignments()
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
    assignment_note = (request.GET.get("assignment_note") or "").strip()
    sections = AdminScopeService.active_scoped_sections(request).order_by("code")
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
    accepted_count = 0
    pending_acceptance_count = 0
    declined_count = 0
    clarification_count = 0
    expired_count = 0
    due_soon_count = 0
    assignment_metric_cards = []
    if selected_faculty:
        selected_faculty_assignments = (
            AdminScopeService.scoped_faculty_assignments(request)
            .filter(faculty_user_id=selected_faculty.id, is_active=True)
            .select_related(
                "accepted_by",
                "offering",
                "offering__course",
                "offering__section",
                "offering__term",
                "offering__academic_year",
            )
            .order_by("offering__academic_year__start_date", "offering__term__sequence_no", "offering__course__code")
        )
        if selected_section_id:
            selected_faculty_assignments = selected_faculty_assignments.filter(offering__section_id=selected_section_id)
        count_snapshot = _assignment_counts(selected_faculty_assignments)
        assigned_count = count_snapshot["assigned_count"]
        accepted_count = count_snapshot["accepted_count"]
        pending_acceptance_count = count_snapshot["pending_acceptance_count"]
        declined_count = count_snapshot["declined_count"]
        clarification_count = count_snapshot["clarification_count"]
        expired_count = count_snapshot["expired_count"]
        due_soon_count = count_snapshot["due_soon_count"]
        acceptance_rate = count_snapshot["acceptance_rate"]
        assignment_metric_cards = [
            {
                "label": "Assigned Offerings",
                "value": assigned_count,
                "meta": "All active offerings currently assigned to this faculty.",
            },
            {
                "label": "Accepted",
                "value": accepted_count,
                "meta": f"{acceptance_rate:.1f}% of assigned offerings acknowledged.",
            },
            {
                "label": "Pending Acceptance",
                "value": pending_acceptance_count,
                "meta": "Assignments still waiting for faculty acknowledgment.",
            },
            {
                "label": "Due Within 24 Hours",
                "value": due_soon_count,
                "meta": "Pending assignments that are nearing response expiry.",
            },
            {
                "label": "Expired",
                "value": expired_count,
                "meta": "Assignments that need admin follow-up or a renewed response window.",
            },
            {
                "label": "Clarification / Declined",
                "value": clarification_count + declined_count,
                "meta": f"{clarification_count} clarification, {declined_count} declined.",
            },
            {
                "label": "Primary Load",
                "value": selected_faculty_assignments.filter(is_primary=True).count(),
                "meta": "Offerings currently tagged as primary teaching load.",
            },
        ]

    context = {
        "faculty_q": faculty_q,
        "faculty_candidates": faculty_candidates,
        "selected_faculty": selected_faculty,
        "show_assign_box": show_assign_box,
        "offering_q": offering_q,
        "selected_section_id": selected_section_id,
        "assignment_note": assignment_note,
        "sections": sections,
        "assignable_offerings": assignable_offerings,
        "assignable_count": assignable_count,
        "selected_faculty_assignments": selected_faculty_assignments,
        "assigned_count": assigned_count,
        "accepted_count": accepted_count,
        "pending_acceptance_count": pending_acceptance_count,
        "declined_count": declined_count,
        "clarification_count": clarification_count,
        "expired_count": expired_count,
        "due_soon_count": due_soon_count,
        "assignment_metric_cards": assignment_metric_cards,
        "grade_prediction_enabled": FeatureSettingsService.can_user_access_grade_prediction(
            user=request.user,
            tenant_id=getattr(request, "scope", {}).get("tenant_id"),
        ),
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
    visible_exam_components = []
    q = request.GET.get("q", "").strip()
    scope = getattr(request, "scope", {})
    is_masked = _should_mask_gradebook_student_identity(
        request.user,
        tenant_id=scope.get("tenant_id"),
        campus_id=scope.get("campus_id"),
    )
    can_view_student_identity = not is_masked
    period_state = None
    submit_readiness = None
    selected_faculty_assignments = FacultyAssignment.objects.none()
    table_colspan = 5
    is_final_period_view = False

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
                final_period = (
                    template.periods.filter(is_active=True).order_by("-sequence_no", "-id").first()
                    if template is not None
                    else None
                )
                is_final_period_view = bool(final_period is not None and selected_period.id == final_period.id)
                final_grade_map = {
                    row.student_id: row.final_grade
                    for row in StudentFinalGrade.objects.filter(offering_id=selected_offering.id)
                } if is_final_period_view else {}
                visible_exam_components = [] if is_final_period_view else summary_layout["exam_components"]
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
                    period_explain_url = None
                    final_explain_url = None
                    if row["period_grade"] is not None:
                        period_explain_url = reverse(
                            "admin_portal:faculty_gradebook_explanation",
                            kwargs={
                                "offering_id": selected_offering.id,
                                "period_id": selected_period.id,
                                "student_id": row["student"].id,
                                "grade_type": GradeExplanationService.GRADE_TYPE_PERIOD,
                            },
                        )
                    if is_final_period_view and final_grade_map.get(row["student"].id) is not None:
                        final_explain_url = reverse(
                            "admin_portal:faculty_gradebook_explanation",
                            kwargs={
                                "offering_id": selected_offering.id,
                                "period_id": selected_period.id,
                                "student_id": row["student"].id,
                                "grade_type": GradeExplanationService.GRADE_TYPE_FINAL,
                            },
                        )
                    rows.append(
                        {
                            "student": row["student"],
                            "display_student_no": _mask_student_number(row["student"].student_no) if is_masked else row["student"].student_no,
                            "display_student_name": _mask_student_name(row["student"]) if is_masked else f"{row['student'].last_name}, {row['student'].first_name}",
                            "enrollment_status": row["enrollment_status"],
                            "class_standing_blocks": summary_values["class_standing_blocks"],
                            "exam_values": [] if is_final_period_view else summary_values["exam_values"],
                            "period_grade": row["period_grade"],
                            "period_explain_url": period_explain_url,
                            "final_grade": final_grade_map.get(row["student"].id),
                            "final_explain_url": final_explain_url,
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
                    + len(visible_exam_components)
                    + 1
                    + (1 if is_final_period_view else 0)
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
                        "student_identity_visible": can_view_student_identity,
                        "student_identity_visibility_reason": (
                            "gradebook.view_student_identity" if can_view_student_identity else "masked_by_privacy_policy"
                        ),
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
        "visible_exam_components": visible_exam_components,
        "submit_readiness": submit_readiness,
        "q": q,
        "is_masked": is_masked,
        "can_view_student_identity": can_view_student_identity,
        "is_final_period_view": is_final_period_view,
        "table_colspan": table_colspan,
        "grade_prediction_enabled": FeatureSettingsService.can_user_access_grade_prediction(
            user=request.user,
            tenant_id=getattr(request, "scope", {}).get("tenant_id"),
        ),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/academics/faculty_gradebook_monitor.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.read")
def faculty_gradebook_explanation_view(request, offering_id: int, period_id: int, student_id: int, grade_type: str):
    scoped_assignment = (
        AdminScopeService.scoped_faculty_assignments(request)
        .filter(offering_id=offering_id, is_active=True)
        .select_related("offering", "offering__course", "offering__section", "offering__term", "offering__academic_year", "offering__campus")
        .first()
    )
    if not scoped_assignment:
        raise Http404("Grade book row not found in your current scope.")
    offering = scoped_assignment.offering
    try:
        template = FacultyGradingService.resolve_template_for_offering(offering)
    except ValidationError as exc:
        return render(
            request,
            "grading/grade_explanation_detail.html",
            {"restricted_message": "; ".join(exc.messages)},
        )
    period = template.periods.filter(id=period_id, is_active=True).first()
    if period is None:
        raise Http404("Invalid grading period.")
    enrollment = get_object_or_404(
        Enrollment.objects.filter(course_offering=offering, is_active=True).select_related("student"),
        student_id=student_id,
    )
    scope = getattr(request, "scope", {})
    is_masked = _should_mask_gradebook_student_identity(
        request.user,
        tenant_id=scope.get("tenant_id"),
        campus_id=scope.get("campus_id"),
    )
    normalized_grade_type = (grade_type or "").upper()
    try:
        explanation = GradeExplanationService.build(
            offering=offering,
            student=enrollment.student,
            template_period=period,
            grade_type=normalized_grade_type,
            mask_identity=is_masked,
        )
    except ValidationError as exc:
        return render(
            request,
            "grading/grade_explanation_detail.html",
            {"restricted_message": "; ".join(exc.messages)},
        )
    AuditService.log_event(
        action="READ",
        portal="ADMIN",
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
            "student_identity_visible": not is_masked,
            "masked_student_identity": is_masked,
            "student_identity_visibility_reason": (
                "gradebook.view_student_identity" if not is_masked else "masked_by_privacy_policy"
            ),
        },
        request=request,
    )
    return render(request, "grading/grade_explanation_detail.html", {"explanation": explanation})


@portal_required("ADMIN")
@permission_required("faculty_assignments.read")
def grade_prediction_monitor_view(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    if tenant_id and not FeatureSettingsService.can_user_access_grade_prediction(user=request.user, tenant_id=tenant_id):
        messages.error(request, "Grade prediction is currently disabled for your role.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

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
    prediction_rows = []
    summary = None
    metric_cards = []
    selected_faculty_assignments = FacultyAssignment.objects.none()
    is_masked = _should_mask_gradebook_student_identity(request.user)
    q = request.GET.get("q", "").strip()

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
                force_refresh = request.GET.get("refresh") == "1"
                prediction_data = PredictionSnapshotService.get_period_predictions(
                    offering=selected_offering,
                    template_period=selected_period,
                    user=request.user,
                    force_refresh=force_refresh,
                )
                PredictionAuditService.log_view(
                    user=request.user,
                    offering=selected_offering,
                    template_period=selected_period,
                    view_mode="CLASS_SUMMARY",
                )
                summary = prediction_data["summary"]
                prediction_rows = prediction_data["rows"]
                if q:
                    prediction_rows = [
                        row
                        for row in prediction_rows
                        if q.lower() in row.student.student_no.lower()
                        or q.lower() in row.student.last_name.lower()
                        or q.lower() in row.student.first_name.lower()
                    ]
                metric_cards = [
                    {"label": "Students", "value": summary.student_count, "meta": "Active students in this monitored class."},
                    {
                        "label": "With Projection",
                        "value": summary.students_with_projection,
                        "meta": f"{summary.avg_coverage_percent}% average coverage",
                    },
                    {"label": "At Risk", "value": summary.at_risk_count, "meta": "Projected below passing threshold."},
                    {
                        "label": "Average Projection",
                        "value": _format_decimal_display(summary.avg_projected_grade),
                        "meta": "Unofficial projected period grade.",
                    },
                    {
                        "label": "Best Case",
                        "value": _format_decimal_display(summary.avg_best_case_grade),
                        "meta": "If remaining items are completed at full score.",
                    },
                    {
                        "label": "Worst Case",
                        "value": _format_decimal_display(summary.avg_worst_case_grade),
                        "meta": "If remaining items get zero raw score.",
                    },
                ]

    context = {
        "faculty_q": faculty_q,
        "faculty_candidates": faculty_candidates,
        "selected_faculty": selected_faculty,
        "selected_faculty_assignments": selected_faculty_assignments,
        "selected_offering": selected_offering,
        "selected_period": selected_period,
        "periods": periods,
        "rows": prediction_rows,
        "summary": summary,
        "metric_cards": metric_cards,
        "is_masked": is_masked,
        "q": q,
        "at_risk_enabled": FeatureSettingsService.is_grade_prediction_at_risk_enabled(tenant_id=tenant_id, default=True),
        "show_best_case": FeatureSettingsService.show_grade_prediction_best_case(tenant_id=tenant_id, default=True),
        "show_worst_case": FeatureSettingsService.show_grade_prediction_worst_case(tenant_id=tenant_id, default=True),
        "show_target_needed": FeatureSettingsService.show_grade_prediction_target_needed(tenant_id=tenant_id, default=True),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/academics/grade_prediction_monitor.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.read")
def faculty_final_clearance_view(request):
    current_tenant_id = getattr(request, "scope", {}).get("tenant_id")
    current_campus_id = getattr(request, "scope", {}).get("campus_id")
    if not current_tenant_id or not current_campus_id:
        messages.error(request, "Select the correct tenant and campus scope first.")
        return _redirect_back_or_default(request, "admin_portal:dashboard")

    term_queryset = AdminScopeService.active_scoped_terms(request).order_by("-academic_year__start_date", "sequence_no")
    selected_term = None
    selected_term_id = request.GET.get("term_id") or request.POST.get("term_id")
    if selected_term_id:
        selected_term = term_queryset.filter(id=selected_term_id).first()

    faculty_queryset = (
        User.objects.filter(
            faculty_assignments__is_active=True,
            faculty_assignments__offering__tenant_id=current_tenant_id,
            faculty_assignments__offering__campus_id=current_campus_id,
        )
        .distinct()
        .order_by("last_name", "first_name", "username")
    )
    if selected_term:
        faculty_queryset = faculty_queryset.filter(faculty_assignments__offering__term_id=selected_term.id).distinct()

    selected_faculty = None
    selected_faculty_id = request.GET.get("faculty_user_id") or request.POST.get("faculty_user_id")
    if selected_faculty_id:
        selected_faculty = faculty_queryset.filter(id=selected_faculty_id).first()

    lookup_reference_no = (request.GET.get("lookup_reference_no") or "").strip()
    lookup_verification_code = (request.GET.get("lookup_verification_code") or "").strip().upper()
    lookup_report = None
    lookup_error = ""
    if lookup_reference_no or lookup_verification_code:
        if not lookup_reference_no or not lookup_verification_code:
            lookup_error = "Enter both the Reference No. and Verification Code to verify a printed clearance."
        else:
            lookup_report = (
                FacultyFinalClearanceReport.objects.filter(
                    tenant_id=current_tenant_id,
                    campus_id=current_campus_id,
                    reference_no=lookup_reference_no,
                    verification_code=lookup_verification_code,
                )
                .select_related("faculty_user", "term", "academic_year", "generated_by_user")
                .first()
            )
            if not lookup_report:
                lookup_error = (
                    "No official NCBA faculty final clearance report matched the supplied Reference No. "
                    "and Verification Code for the current campus scope."
                )

    campus = get_object_or_404(AdminScopeService.scoped_campuses(request), id=current_campus_id)
    preview = None
    if selected_term and selected_faculty:
        preview = FacultyFinalClearanceReportService.evaluate_faculty_clearance(
            faculty_user=selected_faculty,
            term=selected_term,
            campus=campus,
        )

    if request.method == "POST":
        if not selected_term or not selected_faculty:
            messages.error(request, "Select a term and faculty member first.")
        else:
            messages.error(
                request,
                "Official Final Clearance generation is available only in the Faculty Portal. Admin may preview and verify reports here.",
            )
        query_string = urlencode(
            {
                key: value
                for key, value in {
                    "term_id": selected_term.id if selected_term else "",
                    "faculty_user_id": selected_faculty.id if selected_faculty else "",
                }.items()
                if value
            }
        )
        target_url = reverse("admin_portal:faculty_final_clearance")
        if query_string:
            target_url = f"{target_url}?{query_string}"
        return redirect(target_url)

    recent_reports = (
        FacultyFinalClearanceReport.objects.filter(
            tenant_id=current_tenant_id,
            campus_id=current_campus_id,
        )
        .select_related("faculty_user", "term", "academic_year", "generated_by_user")
        .order_by("-created_at")[:10]
    )

    context = {
        "title": "Faculty Final Clearance",
        "term_options": term_queryset,
        "faculty_options": faculty_queryset,
        "selected_term": selected_term,
        "selected_faculty": selected_faculty,
        "preview": preview,
        "lookup_reference_no": lookup_reference_no,
        "lookup_verification_code": lookup_verification_code,
        "lookup_report": lookup_report,
        "lookup_error": lookup_error,
        "recent_reports": recent_reports,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/academics/faculty_final_clearance.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.read")
def faculty_final_clearance_verify_view(request, report_id: int):
    report_obj = get_object_or_404(
        FacultyFinalClearanceReport.objects.select_related(
            "tenant", "campus", "academic_year", "term", "faculty_user", "generated_by_user"
        ),
        id=report_id,
    )
    scope = getattr(request, "scope", {})
    if report_obj.tenant_id not in set(scope.get("tenant_ids", [])) or report_obj.campus_id not in set(scope.get("campus_ids", [])):
        raise PermissionDenied("You do not have access to this clearance report.")

    context = {
        "title": "Faculty Final Clearance Verification",
        "report_obj": report_obj,
        "rows": (report_obj.snapshot_json or {}).get("rows", []),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/academics/faculty_final_clearance_verify.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.read")
def faculty_assignment_dashboard_view(request):
    FacultyAssignmentWorkflowService.expire_overdue_assignments()

    selected_campus_id = _safe_int(request.GET.get("campus_id"))
    selected_department_id = _safe_int(request.GET.get("department_id"))
    faculty_q = (request.GET.get("faculty_q") or "").strip()

    campus_options = AdminScopeService.active_scoped_campuses(request).order_by("code", "name")
    department_options = AdminScopeService.active_scoped_departments(request).order_by("campus__code", "name")
    if selected_campus_id:
        department_options = department_options.filter(campus_id=selected_campus_id)

    assignment_queryset = (
        AdminScopeService.scoped_faculty_assignments(request)
        .filter(is_active=True)
        .select_related(
            "faculty_user",
            "faculty_user__default_campus",
            "faculty_user__default_department",
            "offering",
            "offering__course",
            "offering__section",
            "offering__campus",
            "offering__department",
        )
    )

    assignment_rows = []
    lowered_faculty_q = faculty_q.lower()
    selected_department_ids = AdminScopeService.expand_department_filter_ids(
        selected_department_id,
        campus_id=selected_campus_id,
    )
    for assignment in assignment_queryset:
        scope_snapshot = _faculty_assignment_scope_snapshot(assignment)
        if selected_campus_id and scope_snapshot["campus_id"] != selected_campus_id:
            continue
        if selected_department_id and scope_snapshot["department_id"] not in selected_department_ids:
            continue
        if faculty_q:
            faculty_haystack = " ".join(
                [
                    scope_snapshot["faculty_label"],
                    assignment.faculty_user.username or "",
                    assignment.faculty_user.email or "",
                ]
            ).lower()
            if lowered_faculty_q not in faculty_haystack:
                continue
        assignment.scope_snapshot = scope_snapshot
        assignment_rows.append(assignment)

    now = timezone.now()
    due_soon_cutoff = now + timedelta(days=1)
    faculty_ids = {assignment.faculty_user_id for assignment in assignment_rows}
    overall_counts = {
        "assigned_count": len(assignment_rows),
        "accepted_count": 0,
        "pending_acceptance_count": 0,
        "clarification_count": 0,
        "declined_count": 0,
        "expired_count": 0,
        "due_soon_count": 0,
    }

    def _apply_bucket_counts(bucket, assignment):
        bucket["assigned_count"] += 1
        status = assignment.response_status
        if status == FacultyAssignment.ResponseStatus.ACCEPTED:
            bucket["accepted_count"] += 1
        elif status == FacultyAssignment.ResponseStatus.PENDING:
            bucket["pending_acceptance_count"] += 1
            if assignment.response_due_at and now < assignment.response_due_at <= due_soon_cutoff:
                bucket["due_soon_count"] += 1
        elif status == FacultyAssignment.ResponseStatus.CLARIFICATION_REQUESTED:
            bucket["clarification_count"] += 1
        elif status == FacultyAssignment.ResponseStatus.DECLINED:
            bucket["declined_count"] += 1
        elif status == FacultyAssignment.ResponseStatus.EXPIRED:
            bucket["expired_count"] += 1

    campus_buckets = {}
    department_buckets = {}
    faculty_buckets = {}
    for assignment in assignment_rows:
        _apply_bucket_counts(overall_counts, assignment)
        scope_snapshot = assignment.scope_snapshot
        campus_key = scope_snapshot["campus_id"] or f"campus:{scope_snapshot['campus_label']}"
        department_key = (
            scope_snapshot["campus_id"],
            scope_snapshot["department_id"] or f"department:{scope_snapshot['department_label']}",
        )
        faculty_key = assignment.faculty_user_id

        campus_bucket = campus_buckets.setdefault(
            campus_key,
            {
                "campus_label": scope_snapshot["campus_label"],
                "faculty_ids": set(),
                "assigned_count": 0,
                "accepted_count": 0,
                "pending_acceptance_count": 0,
                "clarification_count": 0,
                "declined_count": 0,
                "expired_count": 0,
                "due_soon_count": 0,
            },
        )
        campus_bucket["faculty_ids"].add(assignment.faculty_user_id)
        _apply_bucket_counts(campus_bucket, assignment)

        department_bucket = department_buckets.setdefault(
            department_key,
            {
                "campus_label": scope_snapshot["campus_label"],
                "department_label": scope_snapshot["department_label"],
                "faculty_ids": set(),
                "assigned_count": 0,
                "accepted_count": 0,
                "pending_acceptance_count": 0,
                "clarification_count": 0,
                "declined_count": 0,
                "expired_count": 0,
                "due_soon_count": 0,
            },
        )
        department_bucket["faculty_ids"].add(assignment.faculty_user_id)
        _apply_bucket_counts(department_bucket, assignment)

        faculty_bucket = faculty_buckets.setdefault(
            faculty_key,
            {
                "faculty_user_id": assignment.faculty_user_id,
                "faculty_label": scope_snapshot["faculty_label"],
                "campus_label": scope_snapshot["campus_label"],
                "department_label": scope_snapshot["department_label"],
                "assigned_count": 0,
                "accepted_count": 0,
                "pending_acceptance_count": 0,
                "clarification_count": 0,
                "declined_count": 0,
                "expired_count": 0,
                "due_soon_count": 0,
            },
        )
        _apply_bucket_counts(faculty_bucket, assignment)

    def _decorate_rows(rows, include_faculty_total=False):
        decorated = []
        for row in rows:
            assigned = row["assigned_count"]
            row["acceptance_rate"] = round((row["accepted_count"] / assigned) * 100, 1) if assigned else 0
            if "faculty_ids" in row:
                row["faculty_count"] = len(row["faculty_ids"])
            decorated.append(row)
        return decorated

    campus_rows = sorted(
        _decorate_rows(list(campus_buckets.values())),
        key=lambda item: (item["campus_label"], -item["assigned_count"]),
    )
    department_rows = sorted(
        _decorate_rows(list(department_buckets.values())),
        key=lambda item: (item["campus_label"], item["department_label"], -item["assigned_count"]),
    )
    faculty_rows = sorted(
        _decorate_rows(list(faculty_buckets.values())),
        key=lambda item: (-item["pending_acceptance_count"], -item["expired_count"], item["faculty_label"]),
    )

    metric_cards = [
        {
            "label": "Faculty In Scope",
            "value": len(faculty_ids),
            "meta": "Faculty members with at least one assignment in the current filter.",
        },
        {
            "label": "Assigned Loads",
            "value": overall_counts["assigned_count"],
            "meta": "Total active faculty assignments in scope.",
        },
        {
            "label": "Accepted",
            "value": overall_counts["accepted_count"],
            "meta": f"{round((overall_counts['accepted_count'] / overall_counts['assigned_count']) * 100, 1) if overall_counts['assigned_count'] else 0:.1f}% acknowledged.",
        },
        {
            "label": "Pending Acceptance",
            "value": overall_counts["pending_acceptance_count"],
            "meta": "Loads still waiting for faculty response.",
        },
        {
            "label": "Due Within 24 Hours",
            "value": overall_counts["due_soon_count"],
            "meta": "Pending assignments that need immediate follow-up.",
        },
        {
            "label": "Expired",
            "value": overall_counts["expired_count"],
            "meta": "Loads that now require admin renewal.",
        },
    ]

    context = {
        "title": "Faculty Assignment Dashboard",
        "selected_campus_id": selected_campus_id,
        "selected_department_id": selected_department_id,
        "faculty_q": faculty_q,
        "campus_options": campus_options,
        "department_options": department_options,
        "metric_cards": metric_cards,
        "campus_rows": campus_rows,
        "department_rows": department_rows,
        "faculty_rows": faculty_rows,
        "overall_counts": overall_counts,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/academics/faculty_assignment_dashboard.html", context)


@portal_required("ADMIN")
@permission_required("faculty_assignments.update")
def faculty_assignment_renew_window_view(request, assignment_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid request method.")

    row = get_object_or_404(AdminScopeService.scoped_faculty_assignments(request), id=assignment_id, is_active=True)
    redirect_params = {}
    faculty_user_id = _safe_int(request.POST.get("faculty_user_id"))
    faculty_q = (request.POST.get("faculty_q") or "").strip()
    offering_q = (request.POST.get("offering_q") or "").strip()
    selected_section_id = _safe_int(request.POST.get("section_id"))
    if faculty_user_id:
        redirect_params["faculty_user_id"] = faculty_user_id
    if faculty_q:
        redirect_params["faculty_q"] = faculty_q
    if offering_q:
        redirect_params["offering_q"] = offering_q
    if selected_section_id:
        redirect_params["section_id"] = selected_section_id

    if row.response_status != FacultyAssignment.ResponseStatus.EXPIRED:
        messages.error(request, "Only expired faculty assignments can be renewed from this action.")
        return redirect(f"{reverse('admin_portal:faculty_assignment_list')}?{urlencode(redirect_params)}")

    before = model_before_after(row)
    FacultyAssignmentWorkflowService.reset_response_window(row, note=row.assignment_note)
    row.save(
        update_fields=[
            "assignment_note",
            "accepted_at",
            "accepted_by",
            "response_status",
            "faculty_response_note",
            "responded_at",
            "response_due_at",
            "last_reminded_at",
            "reminder_count",
            "updated_at",
        ]
    )
    AuditService.log_event(
        action="UPDATE",
        portal="ADMIN",
        entity_type="FacultyAssignment",
        entity_id=row.id,
        actor=request.user,
        before_data=before,
        after_data=model_before_after(row),
        metadata={"event": "renew_response_window"},
        request=request,
    )
    messages.success(
        request,
        f"Response window renewed for {row.offering.course.code} / {row.offering.section.code}.",
    )
    return redirect(f"{reverse('admin_portal:faculty_assignment_list')}?{urlencode(redirect_params)}")


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
    assignment_note = (request.POST.get("assignment_note") or "").strip()

    redirect_params = {"assign": "1"}
    if faculty_user_id:
        redirect_params["faculty_user_id"] = faculty_user_id
    if faculty_q:
        redirect_params["faculty_q"] = faculty_q
    if offering_q:
        redirect_params["offering_q"] = offering_q
    if selected_section_id:
        redirect_params["section_id"] = selected_section_id
    if assignment_note:
        redirect_params["assignment_note"] = assignment_note

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
            existing.is_primary = FeatureSettingsService.is_faculty_assignment_primary_default_enabled(
                tenant_id=offering.tenant_id,
                default=True,
            )
            FacultyAssignmentWorkflowService.reset_response_window(existing, note=assignment_note or None)
            existing.save(
                update_fields=[
                    "is_active",
                    "is_primary",
                    "assignment_note",
                    "accepted_at",
                    "accepted_by",
                    "response_status",
                    "faculty_response_note",
                    "responded_at",
                    "response_due_at",
                    "last_reminded_at",
                    "reminder_count",
                    "updated_at",
                ]
            )
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
            is_primary=FeatureSettingsService.is_faculty_assignment_primary_default_enabled(
                tenant_id=offering.tenant_id,
                default=True,
            ),
            is_active=True,
        )
        FacultyAssignmentWorkflowService.reset_response_window(created, note=assignment_note or None)
        created.save(
            update_fields=[
                "assignment_note",
                "accepted_at",
                "accepted_by",
                "response_status",
                "faculty_response_note",
                "responded_at",
                "response_due_at",
                "last_reminded_at",
                "reminder_count",
                "updated_at",
            ]
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
    default_primary_enabled = FeatureSettingsService.is_faculty_assignment_primary_default_enabled(
        tenant_id=getattr(request, "scope", {}).get("tenant_id"),
        default=True,
    )
    form = FacultyAssignmentForm(
        request.POST or None,
        offering_queryset=AdminScopeService.scoped_course_offerings(request),
        faculty_queryset=faculty_queryset,
        initial={"is_primary": default_primary_enabled} if request.method != "POST" else None,
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        row.tenant_id = row.offering.tenant_id
        row.campus_id = row.offering.campus_id
        FacultyAssignmentWorkflowService.reset_response_window(row, note=row.assignment_note)
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
        should_reset_window = (
            before.get("offering") != row.offering_id
            or before.get("faculty_user") != row.faculty_user_id
            or (
                before.get("assignment_note") != row.assignment_note
                and row.response_status in {
                    FacultyAssignment.ResponseStatus.CLARIFICATION_REQUESTED,
                    FacultyAssignment.ResponseStatus.DECLINED,
                    FacultyAssignment.ResponseStatus.EXPIRED,
                }
            )
        )
        if should_reset_window:
            FacultyAssignmentWorkflowService.reset_response_window(row, note=row.assignment_note)
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
    context = {"q": q}
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="student")
    context.update(_scope_context(request))
    context["programs"] = AdminScopeService.active_scoped_programs(request)
    return render(request, "admin_portal/students/student_list.html", context)


@portal_required("ADMIN")
@permission_required("students.create")
def student_create_view(request):
    form = StudentForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.active_scoped_departments(request),
        program_queryset=AdminScopeService.active_scoped_programs(request),
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
        department_queryset=AdminScopeService.active_scoped_departments(request),
        program_queryset=AdminScopeService.active_scoped_programs(request),
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


def _scoped_student_account_links(request):
    tenants = AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True)
    campuses = AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True)
    students = AdminScopeService.scoped_students(request).values_list("id", flat=True)
    return (
        StudentAccountLink.objects.filter(tenant_id__in=tenants, campus_id__in=campuses, student_id__in=students)
        .select_related("tenant", "campus", "student", "user", "linked_by_user")
        .order_by("-is_active", "campus__name", "student__last_name", "student__first_name")
    )


def _student_account_link_user_queryset(request):
    tenant_ids = list(AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True))
    campus_ids = list(AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True))
    return (
        User.objects.filter(is_active=True)
        .filter(Q(default_tenant_id__in=tenant_ids) | Q(default_tenant__isnull=True))
        .filter(Q(default_campus_id__in=campus_ids) | Q(default_campus__isnull=True))
        .order_by("last_name", "first_name", "username")
    )


@portal_required("ADMIN")
@permission_required("student_account_links.manage")
def student_account_link_list_view(request):
    queryset = _scoped_student_account_links(request)
    campus_id = request.GET.get("campus_id", "").strip()
    status = request.GET.get("status", "").strip().lower()
    q = request.GET.get("q", "").strip()
    if campus_id:
        queryset = queryset.filter(campus_id=campus_id)
    if status == "active":
        queryset = queryset.filter(is_active=True)
    elif status == "inactive":
        queryset = queryset.filter(is_active=False)
    if q:
        queryset = queryset.filter(
            Q(student__student_no__icontains=q)
            | Q(student__last_name__icontains=q)
            | Q(student__first_name__icontains=q)
            | Q(user__username__icontains=q)
            | Q(user__email__icontains=q)
        )
    paginator = Paginator(queryset, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    context = {
        "title": "Student Account Links",
        "page_obj": page_obj,
        "q": q,
        "status": status,
        "campus_id": campus_id,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/students/student_account_link_list.html", context)


@portal_required("ADMIN")
@permission_required("student_account_links.manage")
def student_account_link_create_view(request):
    form = StudentAccountLinkForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.active_scoped_tenants(request),
        campus_queryset=AdminScopeService.active_scoped_campuses(request),
        student_queryset=AdminScopeService.scoped_students(request).filter(is_active=True),
        user_queryset=_student_account_link_user_queryset(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        row = form.save(commit=False)
        row.linked_by_user = request.user
        row.save()
        AuditService.log_event(
            action="CREATE",
            portal="ADMIN",
            entity_type="StudentAccountLink",
            entity_id=row.id,
            actor=request.user,
            tenant=row.tenant_id,
            campus=row.campus_id,
            after_data=model_before_after(row),
            request=request,
        )
        messages.success(request, "Student account link created.")
        return _redirect_back_or_default(request, "admin_portal:student_account_link_list")
    context = {"form": form, "title": "Create Student Account Link"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("student_account_links.manage")
def student_account_provision_view(request):
    form = StudentAccountProvisioningForm(
        request.POST or None,
        student_queryset=AdminScopeService.scoped_students(request).filter(is_active=True),
        user_queryset=_student_account_link_user_queryset(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        student = form.cleaned_data["student"]
        before_payload = model_before_after(student)
        try:
            result = StudentAccountProvisioningService.provision(
                student=student,
                actor=request.user,
                existing_user=form.cleaned_data.get("existing_user"),
                verify_official_email=form.cleaned_data.get("verify_official_email"),
                notes=form.cleaned_data.get("notes", ""),
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            user = result["user"]
            link = result["link"]
            AuditService.log_event(
                action="PROVISION_STUDENT_ACCOUNT",
                portal="ADMIN",
                entity_type="StudentAccountLink",
                entity_id=link.id,
                actor=request.user,
                tenant=link.tenant_id,
                campus=link.campus_id,
                before_data={"student": before_payload},
                after_data={
                    "student_account_link": model_before_after(link),
                    "user": model_before_after(user),
                    "created_user": result["created_user"],
                    "official_email": result["official_email"],
                },
                request=request,
            )
            if result["created_user"]:
                messages.success(
                    request,
                    f"Student account created for {student.student_no}. Username: {user.username}. "
                    f"Temporary password: {result['temporary_password']}",
                )
                messages.warning(
                    request,
                    "Give the temporary password only through the approved manual credential process. "
                    "Invitation email sending is still deferred.",
                )
            else:
                messages.success(
                    request,
                    f"Existing user {user.username} linked to student {student.student_no}.",
                )
            return _redirect_back_or_default(request, "admin_portal:student_account_link_list")
    context = {"form": form, "title": "Provision Student Portal Account"}
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("student_account_links.manage")
def student_account_link_deactivate_view(request, link_id: int):
    row = get_object_or_404(_scoped_student_account_links(request), id=link_id)
    if request.method != "POST":
        return redirect("admin_portal:student_account_link_list")
    if not row.is_active:
        messages.info(request, "Student account link is already inactive.")
        return _redirect_back_or_default(request, "admin_portal:student_account_link_list")
    before = model_before_after(row)
    row.is_active = False
    row.save(update_fields=["is_active", "updated_at"])
    AuditService.log_event(
        action="DEACTIVATE",
        portal="ADMIN",
        entity_type="StudentAccountLink",
        entity_id=row.id,
        actor=request.user,
        tenant=row.tenant_id,
        campus=row.campus_id,
        before_data=before,
        after_data=model_before_after(row),
        request=request,
    )
    messages.success(request, "Student account link deactivated.")
    return _redirect_back_or_default(request, "admin_portal:student_account_link_list")


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
    status = request.GET.get("status", "").strip().upper()
    if status == "DR":
        status = Enrollment.Status.DRP

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
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(student__student_no__icontains=q)
            | Q(student__last_name__icontains=q)
            | Q(student__first_name__icontains=q)
            | Q(course_offering__course__code__icontains=q)
            | Q(course_offering__section__code__icontains=q)
        )
    status_summary = queryset.filter(is_active=True).aggregate(
        active_count=Count("id", filter=Q(enrollment_status=Enrollment.Status.ACTIVE)),
        drp_count=Count("id", filter=Q(enrollment_status=Enrollment.Status.DRP)),
        withdrawn_count=Count("id", filter=Q(enrollment_status=Enrollment.Status.W)),
        incomplete_count=Count("id", filter=Q(enrollment_status=Enrollment.Status.INC)),
    )
    if status:
        queryset = queryset.filter(enrollment_status=status)

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
    section_options = AdminScopeService.active_scoped_sections(request)
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

    context = {"q": q}
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="enrollment")
    context.update(_scope_context(request))
    context["offerings"] = offerings
    context["academic_years"] = AdminScopeService.active_scoped_academic_years(request)
    context["terms"] = AdminScopeService.active_scoped_terms(request)
    context["sections"] = section_options
    context["courses"] = AdminScopeService.active_scoped_courses(request)
    context["status"] = status
    context["campus_id"] = campus_id
    context["academic_year_id"] = academic_year_id
    context["term_id"] = term_id
    context["section_id"] = section_id
    context["course_id"] = course_id
    context["offering_id"] = offering_id
    context["status_summary"] = {
        "active": status_summary.get("active_count") or 0,
        "drp": status_summary.get("drp_count") or 0,
        "w": status_summary.get("withdrawn_count") or 0,
        "inc": status_summary.get("incomplete_count") or 0,
    }
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


def _ensure_template_workflow_stage_or_forbidden(request, *, tenant_id: int, stage_code: str, back_route: str):
    try:
        TemplateGovernanceWorkflowService.ensure_user_can_perform_stage(
            user=request.user,
            stage_code=stage_code,
            tenant_id=tenant_id,
        )
    except ValidationError as exc:
        messages.error(request, str(exc))
        return _redirect_back_or_default(request, back_route)
    return None


def _ensure_template_draft_access_or_forbidden(request, template, back_route: str = "admin_portal:grading_template_list"):
    locked_response = _ensure_template_editable_or_forbidden(request, template)
    if locked_response:
        return locked_response
    return _ensure_template_workflow_stage_or_forbidden(
        request,
        tenant_id=template.tenant_id,
        stage_code=TemplateGovernanceWorkflowService.STAGE_DRAFT,
        back_route=back_route,
    )


@portal_required("ADMIN")
@permission_required("grading_templates.read")
def grading_template_list_view(request):
    queryset = AdminScopeService.maintenance_scoped_grading_templates(request).prefetch_related(
        Prefetch(
            "periods",
            queryset=GradingTemplatePeriod.objects.filter(is_active=True).order_by("sequence_no", "id"),
            to_attr="active_periods",
        )
    ).annotate(period_count=Count("periods", filter=Q(periods__is_active=True)))
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
    split_pages = _active_inactive_pages(request, queryset)
    current_campus_id = getattr(request, "scope", {}).get("campus_id")
    for page_obj in (split_pages["active_page_obj"], split_pages["inactive_page_obj"]):
        for row in page_obj:
            row.active_period_codes = [period.code for period in getattr(row, "active_periods", [])]
            current_approval_step = TemplateGovernanceWorkflowService.get_current_approval_step(template=row)
            row.current_approval_step = current_approval_step
            row.current_approval_step_label = current_approval_step.step_label if current_approval_step else ""
            row.can_submit = (
                PermissionService.has_permission(
                    request.user,
                    "grading_templates.submit_for_approval",
                    tenant_id=row.tenant_id,
                    campus_id=current_campus_id,
                )
                and not row.is_published
                and row.approval_status != GradingTemplate.ApprovalStatus.FOR_APPROVAL
                and TemplateGovernanceWorkflowService.user_has_stage_role(
                    user=request.user,
                    stage_code=TemplateGovernanceWorkflowService.STAGE_SUBMIT_FOR_APPROVAL,
                    tenant_id=row.tenant_id,
                )
            )
            row.can_review_approval = (
                PermissionService.has_permission(
                    request.user,
                    "grading_templates.approve",
                    tenant_id=row.tenant_id,
                    campus_id=current_campus_id,
                )
                and row.approval_status == GradingTemplate.ApprovalStatus.FOR_APPROVAL
                and TemplateGovernanceWorkflowService.user_can_take_approval_step(
                    template=row,
                    actor=request.user,
                )
            )
            row.can_publish_workflow = (
                PermissionService.has_permission(
                    request.user,
                    "grading_templates.publish",
                    tenant_id=row.tenant_id,
                    campus_id=current_campus_id,
                )
                and not row.is_published
                and (
                    row.approval_status == GradingTemplate.ApprovalStatus.APPROVED
                    or not TemplateGovernanceWorkflowService.require_approval_before_publish(tenant_id=row.tenant_id)
                )
                and row.approval_status != GradingTemplate.ApprovalStatus.FOR_APPROVAL
                and TemplateGovernanceWorkflowService.user_has_stage_role(
                    user=request.user,
                    stage_code=TemplateGovernanceWorkflowService.STAGE_PUBLISH,
                    tenant_id=row.tenant_id,
                )
                and (
                    TemplateGovernanceWorkflowService.allow_same_user_review_publish(tenant_id=row.tenant_id)
                    or row.approval_reviewed_by_id != request.user.id
                )
            )
            row.can_hotfix_request = (
                PermissionService.has_permission(
                    request.user,
                    "template_hotfixes.create",
                    tenant_id=row.tenant_id,
                    campus_id=current_campus_id,
                )
                and row.is_published
                and TemplateGovernanceWorkflowService.user_has_stage_role(
                    user=request.user,
                    stage_code=TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST,
                    tenant_id=row.tenant_id,
                )
            )
    context = {
        "q": q,
        "published": request.GET.get("published", ""),
        "approval_status": approval_status,
        "approval_status_choices": GradingTemplate.ApprovalStatus.choices,
        "involved_personalities": TemplateHotfixService.involved_personalities(),
    }
    context.update(split_pages)
    _with_inactive_record_metadata(request, context, model_key="grading_template")
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
def grading_template_calculator_view(request):
    template_queryset = GradingTemplateTestingCalculatorService.prefetch_templates(
        AdminScopeService.scoped_grading_templates(request).filter(is_active=True).select_related("tenant")
    )
    bound_data = request.POST if request.method == "POST" else (request.GET if request.GET else None)
    form = GradingTemplateTestingCalculatorForm(bound_data, template_queryset=template_queryset)
    _style_form(form)

    selected_template = None
    calculation = None
    if form.is_bound and form.is_valid():
        selected_template = template_queryset.filter(id=form.cleaned_data["grading_template"].id).first()
        if selected_template:
            calculation = GradingTemplateTestingCalculatorService.build_calculation(
                template=selected_template,
                raw_inputs=request.POST if request.method == "POST" else None,
                default_sample=Decimal(form.cleaned_data["sample_value"]),
            )
            if calculation["input_errors"]:
                messages.warning(
                    request,
                    "Some sample rows had invalid percentages, so EduGradesPro temporarily used the default sample value for those rows.",
                )

    context = {
        "title": "Grading Template Testing Calculator",
        "form": form,
        "selected_template": selected_template,
        "calculation": calculation,
        "usage_notes": [
            "This tool is read-only. It does not create grades, activities, or student records.",
            "Enter sample raw score and total score values at the lowest active level of the selected template.",
            "EduGradesPro will first convert raw score to computed percentage, then roll the result upward into component, period, and final grades.",
            "Period grades follow the same current EduGradesPro computation logic used by the official grading engine.",
            "The final-grade section uses the matched active tenant grading profile for this template. If no active profile matches, it shows the active-period average fallback.",
        ],
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/template_testing_calculator.html", context)


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
                queryset=GradingTemplatePeriod.objects.order_by("sequence_no", "id").prefetch_related(
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
        stage_response = _ensure_template_workflow_stage_or_forbidden(
            request,
            tenant_id=form.cleaned_data["tenant"].id,
            stage_code=TemplateGovernanceWorkflowService.STAGE_DRAFT,
            back_route="admin_portal:grading_template_list",
        )
        if stage_response:
            return stage_response
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
    context = {
        "form": form,
        "title": "Create Grading Template",
        "form_extra_template": "admin_portal/grading/template_period_code_reference.html",
        "template_obj": None,
        "template_periods": [],
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("grading_templates.update")
def grading_template_update_view(request, template_id: int):
    row = get_object_or_404(AdminScopeService.scoped_grading_templates(request), id=template_id)
    locked_response = _ensure_template_draft_access_or_forbidden(request, row)
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
    context = {
        "form": form,
        "title": f"Edit Grading Template: {row.code}",
        "form_extra_template": "admin_portal/grading/template_period_code_reference.html",
        "template_obj": row,
        "template_periods": list(row.periods.order_by("sequence_no", "id")),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/shared/form_page.html", context)


@portal_required("ADMIN")
@permission_required("grading_templates.create")
def grading_template_duplicate_view(request, template_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid method.")
    source = get_object_or_404(AdminScopeService.maintenance_scoped_grading_templates(request), id=template_id)
    stage_response = _ensure_template_workflow_stage_or_forbidden(
        request,
        tenant_id=source.tenant_id,
        stage_code=TemplateGovernanceWorkflowService.STAGE_DRAFT,
        back_route="admin_portal:grading_template_list",
    )
    if stage_response:
        return stage_response

    duplicate, counts = GradingTemplateDuplicationService.duplicate_template(source=source)
    AuditService.log_event(
        action="DUPLICATE",
        portal="ADMIN",
        entity_type="GradingTemplate",
        entity_id=duplicate.id,
        actor=request.user,
        tenant=duplicate.tenant,
        after_data=model_before_after(duplicate),
        request=request,
        metadata={
            "source_template_id": source.id,
            "source_template_code": source.code,
            "duplicated_counts": counts,
            "duplicate_status": "DRAFT",
        },
    )
    messages.success(
        request,
        (
            f"Template {source.code} duplicated as {duplicate.code}. "
            "The copy is unpublished and in Draft status."
        ),
    )
    return redirect("admin_portal:grading_template_builder", template_id=duplicate.id)


@portal_required("ADMIN")
@permission_required("grading_templates.publish")
def grading_template_publish_view(request, template_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid method.")
    row = get_object_or_404(AdminScopeService.scoped_grading_templates(request), id=template_id)
    before = model_before_after(row)
    try:
        TemplateGovernanceWorkflowService.ensure_can_publish_template(template=row, actor=request.user)
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
            TemplateGovernanceWorkflowService.ensure_user_can_perform_stage(
                user=request.user,
                stage_code=TemplateGovernanceWorkflowService.STAGE_SUBMIT_FOR_APPROVAL,
                tenant_id=row.tenant_id,
            )
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
            TemplateGovernanceWorkflowService.ensure_can_review_template(template=row, actor=request.user)
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
        row.refresh_from_db()
        next_step = TemplateGovernanceWorkflowService.get_current_approval_step(template=row)
        if approve and row.approval_status == GradingTemplate.ApprovalStatus.FOR_APPROVAL and next_step:
            messages.success(
                request,
                f"Template {row.code} advanced to the next workflow step: {next_step.step_label}.",
            )
        else:
            messages.success(
                request,
                f"Template {row.code} {'approved' if approve else 'rejected'} successfully.",
            )
        return _redirect_back_or_default(request, "admin_portal:grading_template_list")

    approval_workflow = TemplateGovernanceWorkflowService.get_pending_approval_workflow(template=row)
    context = {
        "form": form,
        "title": f"Review Template Approval: {row.code}",
        "template_obj": row,
        "approval_workflow": approval_workflow,
        "approval_steps": list(approval_workflow.steps.order_by("step_no")) if approval_workflow else [],
        "current_step": TemplateGovernanceWorkflowService.get_current_approval_step(template=row),
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
    page_obj = _get_page(request, queryset)
    current_campus_id = getattr(request, "scope", {}).get("campus_id")
    for row in page_obj:
        current_hotfix_step = TemplateGovernanceWorkflowService.get_current_hotfix_step(hotfix_request=row)
        row.current_hotfix_step = current_hotfix_step
        row.current_hotfix_step_label = current_hotfix_step.step_label if current_hotfix_step else ""
        row.can_review_workflow = (
            PermissionService.has_permission(
                request.user,
                "template_hotfixes.review",
                tenant_id=row.tenant_id,
                campus_id=current_campus_id,
            )
            and row.status == TemplateHotfixRequest.Status.PENDING
            and TemplateGovernanceWorkflowService.user_can_take_hotfix_step(
                hotfix_request=row,
                actor=request.user,
            )
        )
    context = {
        "page_obj": page_obj,
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
    ).select_related(
        "course",
        "section",
        "academic_year",
        "term",
        "campus",
    ).order_by(
        "course__title",
        "course__code",
        "section__code",
        "academic_year__code",
        "term__sequence_no",
        "id",
    )
    form = TemplateHotfixRequestForm(request.POST or None, offering_queryset=scoped_offerings)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        selected_offerings = [row.id for row in form.cleaned_data.get("selected_offerings", [])]
        try:
            TemplateGovernanceWorkflowService.ensure_user_can_perform_stage(
                user=request.user,
                stage_code=TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST,
                tenant_id=template.tenant_id,
            )
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

    selected_ids = {
        str(value)
        for value in (
            request.POST.getlist("selected_offerings")
            if request.method == "POST"
            else form.initial.get("selected_offerings", [])
        )
    }
    offering_cards = [
        {
            "id": row.id,
            "title": row.course.title,
            "course_code": row.course.code,
            "section_code": row.section.code,
            "academic_year_code": row.academic_year.code,
            "term_code": row.term.code,
            "campus_code": row.campus.code if row.campus_id else "",
            "checked": str(row.id) in selected_ids,
        }
        for row in scoped_offerings
    ]
    context = {
        "form": form,
        "title": f"Create Hotfix Request: {template.code}",
        "template_obj": template,
        "offering_cards": offering_cards,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/template_hotfix_create.html", context)


@portal_required("ADMIN")
@permission_required("template_hotfixes.review")
def template_hotfix_review_view(request, hotfix_id: int):
    hotfix = get_object_or_404(AdminScopeService.scoped_template_hotfix_requests(request), id=hotfix_id)
    current_step = TemplateGovernanceWorkflowService.get_current_hotfix_step(hotfix_request=hotfix)
    is_apply_step = _is_hotfix_apply_step(hotfix, current_step)
    impact_preview = _template_hotfix_impact_preview(hotfix)
    form = TemplateHotfixReviewForm(request.POST or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        before = model_before_after(hotfix)
        approve = form.cleaned_data["decision"] == TemplateHotfixReviewForm.Decision.APPROVE
        if approve and is_apply_step and (form.cleaned_data.get("confirmation_phrase") or "").strip() != HOTFIX_APPLY_CONFIRMATION:
            form.add_error("confirmation_phrase", f"Type {HOTFIX_APPLY_CONFIRMATION} to apply this hotfix.")
        if form.errors:
            context = {
                "form": form,
                "title": f"Review Hotfix Request #{hotfix.id} ({hotfix.template.code})",
                "hotfix": hotfix,
                "workflow_steps": list(hotfix.workflow_steps.order_by("step_no")) if hotfix.workflow_steps.exists() else [],
                "current_step": current_step,
                "is_apply_step": is_apply_step,
                "hotfix_apply_confirmation": HOTFIX_APPLY_CONFIRMATION,
                "impact_preview": impact_preview,
                "involved_personalities": TemplateHotfixService.involved_personalities(),
            }
            context.update(_scope_context(request))
            return render(request, "admin_portal/grading/template_hotfix_review.html", context)
        try:
            TemplateGovernanceWorkflowService.ensure_can_apply_hotfix(
                hotfix_request=hotfix,
                actor=request.user,
            )
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
                "critical_action": approve and is_apply_step,
                "workflow": "TEMPLATE_HOTFIX",
                "reason": (form.cleaned_data.get("review_remarks") or "").strip(),
                "confirmation_required": approve and is_apply_step,
                "confirmation_phrase": HOTFIX_APPLY_CONFIRMATION if approve and is_apply_step else "",
                "apply_mode": hotfix.apply_mode,
                "status": hotfix.status,
                "impact_summary": impact_preview,
            },
        )
        hotfix.refresh_from_db()
        next_step = TemplateGovernanceWorkflowService.get_current_hotfix_step(hotfix_request=hotfix)
        if approve and hotfix.status == TemplateHotfixRequest.Status.PENDING and next_step:
            messages.success(
                request,
                f"Hotfix request #{hotfix.id} advanced to the next workflow step: {next_step.step_label}.",
            )
        else:
            messages.success(
                request,
                f"Hotfix request #{hotfix.id} {'approved and applied' if approve else 'rejected'}.",
            )
        return _redirect_back_or_default(request, "admin_portal:template_hotfix_list")

    context = {
        "form": form,
        "title": f"Review Hotfix Request #{hotfix.id} ({hotfix.template.code})",
        "hotfix": hotfix,
        "workflow_steps": list(hotfix.workflow_steps.order_by("step_no")) if hotfix.workflow_steps.exists() else [],
        "current_step": current_step,
        "is_apply_step": is_apply_step,
        "hotfix_apply_confirmation": HOTFIX_APPLY_CONFIRMATION,
        "impact_preview": impact_preview,
        "involved_personalities": TemplateHotfixService.involved_personalities(),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/template_hotfix_review.html", context)


@portal_required("ADMIN")
@permission_required("template_periods.read")
def template_period_list_view(request):
    queryset = AdminScopeService.maintenance_scoped_template_periods(request)
    selected_template_id = request.GET.get("template_id", "").strip()
    selected_template_id_int = _safe_int(selected_template_id)
    if selected_template_id_int:
        queryset = queryset.filter(template_id=selected_template_id_int)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(template__name__icontains=q))
    context = {
        "q": q,
        "selected_template_id": selected_template_id,
    }
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="template_period")
    context.update(_scope_context(request))
    context["templates"] = AdminScopeService.maintenance_scoped_grading_templates(request)
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
        locked_response = _ensure_template_draft_access_or_forbidden(request, parent_template)
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
    locked_response = _ensure_template_draft_access_or_forbidden(request, row.template)
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
        locked_response = _ensure_template_draft_access_or_forbidden(request, selected_template)
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
    queryset = AdminScopeService.maintenance_scoped_template_components(request)
    selected_template_id = _safe_int(request.GET.get("template_id"))
    selected_period_id = _safe_int(request.GET.get("period_id"))
    if selected_template_id:
        queryset = queryset.filter(template_period__template_id=selected_template_id)
    if selected_period_id:
        queryset = queryset.filter(template_period_id=selected_period_id)
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(code__icontains=q)
            | Q(name__icontains=q)
            | Q(template_period__code__icontains=q)
            | Q(template_period__template__name__icontains=q)
        )
    context = {
        "q": q,
        "selected_template_id": str(selected_template_id or ""),
        "selected_period_id": str(selected_period_id or ""),
    }
    builder_template_id = selected_template_id
    if not builder_template_id and selected_period_id:
        builder_template_id = (
            AdminScopeService.maintenance_scoped_template_periods(request)
            .filter(id=selected_period_id)
            .values_list("template_id", flat=True)
            .first()
        )
    context["builder_template_id"] = str(builder_template_id or "")
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="template_component")
    context.update(_scope_context(request))
    period_qs = AdminScopeService.maintenance_scoped_template_periods(request)
    if selected_template_id:
        period_qs = period_qs.filter(template_id=selected_template_id)
    context["periods"] = period_qs
    context["templates"] = AdminScopeService.maintenance_scoped_grading_templates(request)
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
        locked_response = _ensure_template_draft_access_or_forbidden(request, parent_template)
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
    locked_response = _ensure_template_draft_access_or_forbidden(request, row.template_period.template)
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
        locked_response = _ensure_template_draft_access_or_forbidden(request, selected_template)
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
    locked_response = _ensure_template_draft_access_or_forbidden(request, row.template_period.template)
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
    queryset = AdminScopeService.maintenance_scoped_template_subcomponents(request)
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
        "q": q,
        "selected_component_id": str(selected_component_id or ""),
        "selected_period_id": str(selected_period_id or ""),
        "selected_template_id": str(selected_template_id or ""),
    }
    builder_template_id = selected_template_id
    if not builder_template_id and selected_period_id:
        builder_template_id = (
            AdminScopeService.maintenance_scoped_template_periods(request)
            .filter(id=selected_period_id)
            .values_list("template_id", flat=True)
            .first()
        )
    if not builder_template_id and selected_component_id:
        builder_template_id = (
            AdminScopeService.maintenance_scoped_template_components(request)
            .filter(id=selected_component_id)
            .values_list("template_period__template_id", flat=True)
            .first()
        )
    context["builder_template_id"] = str(builder_template_id or "")
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="template_subcomponent")
    context.update(_scope_context(request))
    component_qs = AdminScopeService.maintenance_scoped_template_components(request)
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
        locked_response = _ensure_template_draft_access_or_forbidden(request, parent_template)
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
    locked_response = _ensure_template_draft_access_or_forbidden(request, row.template_component.template_period.template)
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
        locked_response = _ensure_template_draft_access_or_forbidden(request, selected_template)
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
    queryset = AdminScopeService.maintenance_scoped_template_details(request)
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
        "q": q,
        "selected_subcomponent_id": str(selected_subcomponent_id or ""),
        "selected_component_id": str(selected_component_id or ""),
        "selected_period_id": str(selected_period_id or ""),
        "selected_template_id": str(selected_template_id or ""),
    }
    builder_template_id = selected_template_id
    if not builder_template_id and selected_period_id:
        builder_template_id = (
            AdminScopeService.maintenance_scoped_template_periods(request)
            .filter(id=selected_period_id)
            .values_list("template_id", flat=True)
            .first()
        )
    if not builder_template_id and selected_component_id:
        builder_template_id = (
            AdminScopeService.maintenance_scoped_template_components(request)
            .filter(id=selected_component_id)
            .values_list("template_period__template_id", flat=True)
            .first()
        )
    if not builder_template_id and selected_subcomponent_id:
        builder_template_id = (
            AdminScopeService.maintenance_scoped_template_subcomponents(request)
            .filter(id=selected_subcomponent_id)
            .values_list("template_component__template_period__template_id", flat=True)
            .first()
        )
    context["builder_template_id"] = str(builder_template_id or "")
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="template_detail")
    context.update(_scope_context(request))
    subcomponent_qs = AdminScopeService.maintenance_scoped_template_subcomponents(request)
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
        locked_response = _ensure_template_draft_access_or_forbidden(request, parent_template)
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
    locked_response = _ensure_template_draft_access_or_forbidden(
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
        locked_response = _ensure_template_draft_access_or_forbidden(request, selected_template)
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
    queryset = AdminScopeService.maintenance_scoped_tenant_grading_profiles(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("campus_id"):
        queryset = queryset.filter(campus_id=request.GET.get("campus_id"))
    if request.GET.get("term_type"):
        queryset = queryset.filter(term_type=request.GET.get("term_type"))
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
        "q": q,
        "term_type": request.GET.get("term_type", ""),
        "term_type_choices": TenantGradingProfile.TermType.choices,
    }
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="tenant_grading_profile")
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/tenant_grading_profile_list.html", context)


@portal_required("ADMIN")
@permission_required("tenant_grading_profiles.create")
def tenant_grading_profile_create_view(request):
    form = TenantGradingProfileForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.active_scoped_departments(request),
        program_queryset=AdminScopeService.active_scoped_programs(request),
        course_queryset=AdminScopeService.active_scoped_courses(request),
        template_queryset=AdminScopeService.scoped_grading_templates(request).filter(is_published=True, is_active=True),
        term_queryset=AdminScopeService.active_scoped_terms(request),
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
    row = get_object_or_404(AdminScopeService.maintenance_scoped_tenant_grading_profiles(request), id=profile_id)
    before = model_before_after(row)
    template_queryset = AdminScopeService.scoped_grading_templates(request).filter(
        Q(is_published=True, is_active=True) | Q(id=row.grading_template_id)
    )
    form = TenantGradingProfileForm(
        request.POST or None,
        instance=row,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        department_queryset=AdminScopeService.active_scoped_departments(request),
        program_queryset=AdminScopeService.active_scoped_programs(request),
        course_queryset=AdminScopeService.active_scoped_courses(request),
        template_queryset=template_queryset,
        term_queryset=AdminScopeService.active_scoped_terms(request),
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
@permission_required("tenant_grading_profiles.create")
def tenant_grading_profile_duplicate_view(request, profile_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid method.")
    source = get_object_or_404(AdminScopeService.maintenance_scoped_tenant_grading_profiles(request), id=profile_id)
    duplicate = GradingTemplateDuplicationService.duplicate_profile(source=source)
    AuditService.log_event(
        action="DUPLICATE",
        portal="ADMIN",
        entity_type="TenantGradingProfile",
        entity_id=duplicate.id,
        actor=request.user,
        tenant=duplicate.tenant,
        campus=duplicate.campus,
        after_data=model_before_after(duplicate),
        request=request,
        metadata={
            "source_profile_id": source.id,
            "source_profile_code": source.profile_code,
            "duplicate_status": "INACTIVE",
        },
    )
    messages.success(
        request,
        (
            f"Tenant grading profile {source.profile_code} duplicated as {duplicate.profile_code}. "
            "The copy is inactive and not marked as default until reviewed."
        ),
    )
    return redirect("admin_portal:tenant_grading_profile_update", profile_id=duplicate.id)


@portal_required("ADMIN")
@permission_required("course_template_assignments.read")
def course_template_assignment_list_view(request):
    courses_qs = AdminScopeService.active_scoped_courses(request).select_related("tenant", "campus", "department")
    assignments_qs = AdminScopeService.maintenance_scoped_course_template_assignments(request)
    tenant_id = request.GET.get("tenant_id")
    course_id = request.GET.get("course_id")
    term_id = request.GET.get("term_id")
    without_template = request.GET.get("without_template") == "1"
    offerings_without_template = request.GET.get("offerings_without_template") == "1"
    if tenant_id:
        courses_qs = courses_qs.filter(tenant_id=tenant_id)
        assignments_qs = assignments_qs.filter(course__tenant_id=tenant_id)
    if course_id:
        courses_qs = courses_qs.filter(id=course_id)
        assignments_qs = assignments_qs.filter(course_id=course_id)
    offerings_qs = AdminScopeService.scoped_course_offerings(request).filter(is_active=True)
    if tenant_id:
        offerings_qs = offerings_qs.filter(tenant_id=tenant_id)
    if course_id:
        offerings_qs = offerings_qs.filter(course_id=course_id)
    if term_id:
        offerings_qs = offerings_qs.filter(term_id=term_id)
    else:
        active_scope_term_ids = []
        for tenant in AdminScopeService.active_scoped_tenants(request):
            _active_ay, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant.id)
            if active_term:
                active_scope_term_ids.append(active_term.id)
        if active_scope_term_ids:
            offerings_qs = offerings_qs.filter(term_id__in=active_scope_term_ids)
    q = request.GET.get("q", "").strip()
    if q:
        assignments_qs = assignments_qs.filter(
            Q(course__code__icontains=q)
            | Q(course__title__icontains=q)
            | Q(grading_template__code__icontains=q)
            | Q(grading_template__name__icontains=q)
        )
        courses_qs = courses_qs.filter(Q(code__icontains=q) | Q(title__icontains=q))
        offerings_qs = offerings_qs.filter(
            Q(course__code__icontains=q)
            | Q(course__title__icontains=q)
            | Q(section__code__icontains=q)
            | Q(section__name__icontains=q)
        )

    active_assignment_course_ids = set(
        AdminScopeService.maintenance_scoped_course_template_assignments(request)
        .filter(is_active=True, grading_template__is_active=True)
        .values_list("course_id", flat=True)
        .distinct()
    )
    filtered_active_course_ids = set(
        assignments_qs.filter(is_active=True, grading_template__is_active=True)
        .values_list("course_id", flat=True)
        .distinct()
    )
    filtered_course_ids = set(courses_qs.values_list("id", flat=True))
    courses_without_assignment_count = len(filtered_course_ids - filtered_active_course_ids)

    total_scoped_courses = AdminScopeService.active_scoped_courses(request).count()
    courses_with_template_count = len(active_assignment_course_ids)
    courses_without_template_total = max(total_scoped_courses - courses_with_template_count, 0)
    active_assignment_rows = AdminScopeService.maintenance_scoped_course_template_assignments(request).filter(
        is_active=True,
        grading_template__is_active=True,
    ).count()
    offering_rows_without_template = []
    for offering in offerings_qs.select_related(
        "tenant",
        "campus",
        "department",
        "program",
        "academic_year",
        "term",
        "course",
        "section",
    ):
        has_course_template_assignment = CourseTemplateAssignment.objects.filter(
            course_id=offering.course_id,
            is_active=True,
            grading_template__is_active=True,
            grading_template__is_published=True,
        ).filter(
            Q(effective_from_term_id=offering.term_id) | Q(effective_from_term__isnull=True)
        ).exists()
        if has_course_template_assignment:
            continue
        faculty_names = [
            assignment.faculty_user.full_name or assignment.faculty_user.username
            for assignment in offering.faculty_assignments.filter(is_active=True)
            .select_related("faculty_user")
            .order_by("faculty_user__last_name", "faculty_user__first_name", "faculty_user__username")
        ]
        offering_rows_without_template.append(
            SimpleNamespace(
                offering=offering,
                faculty_names=", ".join(faculty_names) if faculty_names else "-",
                issue="No active published course-template assignment for this offering term.",
            )
        )
    offerings_without_template_count = len(offering_rows_without_template)

    if without_template:
        rows = [
            SimpleNamespace(
                row_type="course_without_template",
                course=course,
                grading_template=None,
                effective_from_term=None,
                is_active=False,
            )
            for course in courses_qs
            if course.id not in active_assignment_course_ids
        ]
    else:
        rows = None

    context = {
        "q": q,
        "without_template": without_template,
        "offerings_without_template": offerings_without_template,
        "metric_cards": [
            {
                "label": "Courses Without Grading Template",
                "value": courses_without_template_total,
                "meta": "Courses in the current scope with no active template assignment yet.",
            },
            {
                "label": "Offerings Without Course Template",
                "value": offerings_without_template_count,
                "meta": "Current course offerings whose course has no active published template assignment.",
            },
            {
                "label": "Courses With Template",
                "value": courses_with_template_count,
                "meta": "Courses already covered by an active grading template assignment.",
            },
            {
                "label": "Active Assignment Rows",
                "value": active_assignment_rows,
                "meta": "Active course-template assignment rows in the current scope.",
            },
        ],
        "filtered_without_template_count": courses_without_assignment_count,
        "offerings_without_template_count": offerings_without_template_count,
    }
    if offerings_without_template:
        context["offering_page_obj"] = _get_page(request, offering_rows_without_template)
    elif without_template:
        context["page_obj"] = _get_page(request, rows)
    else:
        context.update(_active_inactive_pages(request, assignments_qs))
        _with_inactive_record_metadata(request, context, model_key="course_template_assignment")
    context.update(_scope_context(request))
    context["courses"] = AdminScopeService.active_scoped_courses(request)
    context["terms"] = AdminScopeService.active_scoped_terms(request)
    return render(request, "admin_portal/grading/course_template_assignment_list.html", context)


@portal_required("ADMIN")
@permission_required("course_template_assignments.create")
def course_template_assignment_create_view(request):
    form = BulkCourseTemplateAssignmentForm(
        request.POST or None,
        course_queryset=AdminScopeService.active_scoped_courses(request),
        template_queryset=AdminScopeService.scoped_grading_templates(request).filter(is_published=True, is_active=True),
        term_queryset=AdminScopeService.active_scoped_terms(request),
    )
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        courses = list(form.cleaned_data["courses"])
        grading_template = form.cleaned_data["grading_template"]
        effective_from_term = form.cleaned_data["effective_from_term"]
        is_active = bool(form.cleaned_data["is_active"])

        created_rows = []
        reactivated_rows = []
        skipped_courses = []
        term_scope_label = effective_from_term.name if effective_from_term else "General / no term"

        for course in courses:
            prior_assignment = CourseTemplateAssignment.objects.filter(
                course=course,
                effective_from_term=effective_from_term,
            ).select_related("grading_template").order_by("-is_active", "-created_at").first()
            if prior_assignment:
                if prior_assignment.grading_template_id == grading_template.id:
                    if not prior_assignment.is_active and is_active:
                        before = model_before_after(prior_assignment)
                        prior_assignment.is_active = True
                        prior_assignment.save(update_fields=["is_active", "updated_at"])
                        reactivated_rows.append(prior_assignment)
                        AuditService.log_event(
                            action="UPDATE",
                            portal="ADMIN",
                            entity_type="CourseTemplateAssignment",
                            entity_id=prior_assignment.id,
                            actor=request.user,
                            tenant=course.tenant,
                            campus=course.campus,
                            before_data=before,
                            after_data=model_before_after(prior_assignment),
                            request=request,
                        )
                    else:
                        skipped_courses.append(
                            f"{course.title} ({course.code}) already uses {prior_assignment.grading_template.name} for {term_scope_label}."
                        )
                    continue

                skipped_courses.append(
                    f"{course.title} ({course.code}) already has prior assignment {prior_assignment.grading_template.name} for {term_scope_label}."
                )
                continue

            row = CourseTemplateAssignment.objects.create(
                course=course,
                grading_template=grading_template,
                effective_from_term=effective_from_term,
                is_active=is_active,
            )
            created_rows.append(row)
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

        if created_rows:
            messages.success(
                request,
                f"Assigned {grading_template.name} to {len(created_rows)} course(s).",
            )
        if reactivated_rows:
            messages.success(
                request,
                f"Reactivated {len(reactivated_rows)} existing assignment(s) for {grading_template.name}.",
            )
        if skipped_courses:
            preview = "; ".join(skipped_courses[:3])
            extra = "" if len(skipped_courses) <= 3 else f" Plus {len(skipped_courses) - 3} more."
            messages.warning(
                request,
                f"Skipped {len(skipped_courses)} course(s) with prior assignment in the same term scope. {preview}{extra}",
            )
        if not created_rows and not reactivated_rows and not skipped_courses:
            messages.info(request, "No course assignments were changed.")
        return _redirect_back_or_default(request, "admin_portal:course_template_assignment_list")
    context = {
        "form": form,
        "title": "Bulk Assign Course Templates",
        "term_scope_help": "EduGradesPro will skip courses that already have a prior assignment in the same term scope.",
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/course_template_assignment_bulk_form.html", context)


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
        course_queryset=AdminScopeService.active_scoped_courses(request),
        template_queryset=template_queryset,
        term_queryset=AdminScopeService.active_scoped_terms(request),
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
    queryset = AdminScopeService.maintenance_scoped_course_base_value_overrides(request)
    if request.GET.get("tenant_id"):
        queryset = queryset.filter(course__tenant_id=request.GET.get("tenant_id"))
    if request.GET.get("course_id"):
        queryset = queryset.filter(course_id=request.GET.get("course_id"))
    q = request.GET.get("q", "").strip()
    if q:
        queryset = queryset.filter(Q(course__code__icontains=q))
    context = {"q": q}
    context.update(_active_inactive_pages(request, queryset))
    _with_inactive_record_metadata(request, context, model_key="course_base_override")
    context.update(_scope_context(request))
    context["courses"] = AdminScopeService.active_scoped_courses(request)
    return render(request, "admin_portal/grading/course_base_override_list.html", context)


@portal_required("ADMIN")
@permission_required("course_base_overrides.create")
def course_base_override_create_view(request):
    form = CourseBaseValueOverrideForm(
        request.POST or None,
        course_queryset=AdminScopeService.active_scoped_courses(request),
        term_queryset=AdminScopeService.active_scoped_terms(request),
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
        course_queryset=AdminScopeService.active_scoped_courses(request),
        term_queryset=AdminScopeService.active_scoped_terms(request),
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
    queryset = AdminScopeService.maintenance_scoped_grading_period_locks(request)
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
        "q": q,
        "terms": AdminScopeService.active_scoped_terms(request),
        "broad_period_reopen_confirmation": BROAD_PERIOD_REOPEN_CONFIRMATION,
    }
    context.update(_active_inactive_pages(request, queryset))
    for page_key in ("active_page_obj", "inactive_page_obj"):
        page_obj = context.get(page_key)
        if not page_obj:
            continue
        for lock_row in page_obj.object_list:
            lock_row.reopen_impact = _period_lock_reopen_impact(lock_row, request)
    _with_inactive_record_metadata(request, context, model_key="period_lock")
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/period_lock_list.html", context)


@portal_required("ADMIN")
@permission_required("grading_periods.lock")
def grading_period_lock_create_view(request):
    form = GradingPeriodLockForm(
        request.POST or None,
        tenant_queryset=AdminScopeService.scoped_tenants(request),
        campus_queryset=AdminScopeService.scoped_campuses(request),
        academic_year_queryset=AdminScopeService.active_scoped_academic_years(request),
        term_queryset=AdminScopeService.active_scoped_terms(request),
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
        academic_year_queryset=AdminScopeService.active_scoped_academic_years(request),
        term_queryset=AdminScopeService.active_scoped_terms(request),
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
    reason = (request.POST.get("reopen_reason") or "").strip()
    confirmation = (request.POST.get("confirmation_phrase") or "").strip()
    impact_preview = _period_lock_reopen_impact(row, request)
    if not reason:
        messages.error(request, "Period was not reopened. Enter the operational reason.")
        return _redirect_back_or_default(request, "admin_portal:grading_period_lock_list")
    if impact_preview["is_broad"] and confirmation != BROAD_PERIOD_REOPEN_CONFIRMATION:
        messages.error(request, f"Campus-wide reopen was not run. Type {BROAD_PERIOD_REOPEN_CONFIRMATION} to confirm.")
        return _redirect_back_or_default(request, "admin_portal:grading_period_lock_list")
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
        metadata={
            "critical_action": True,
            "reason": reason,
            "confirmation_required": impact_preview["is_broad"],
            "confirmation_phrase": BROAD_PERIOD_REOPEN_CONFIRMATION if impact_preview["is_broad"] else "",
            "impact_summary": impact_preview,
        },
        request=request,
    )
    messages.success(request, "Period reopened.")
    return _redirect_back_or_default(request, "admin_portal:grading_period_lock_list")


@portal_required("ADMIN")
@permission_required("grade_submissions.read")
def overdue_unsubmitted_report_view(request):
    now = timezone.now()
    accepted_assignment_filter = {
        "faculty_assignments__is_active": True,
        "faculty_assignments__response_status": FacultyAssignment.ResponseStatus.ACCEPTED,
        "faculty_assignments__accepted_at__isnull": False,
        "faculty_assignments__faculty_user__is_active": True,
    }
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
                    **accepted_assignment_filter,
                ).distinct()
            )
        for offering in offerings_by_scope[scope_key]:
            target_key = (offering.id, period_key)
            lock_targets[target_key] = _pick_lock(lock_targets.get(target_key), lock)

    if lock_targets:
        offering_ids = {offering_id for offering_id, _ in lock_targets.keys()}
        offerings = list(
            AdminScopeService.scoped_course_offerings(request)
            .filter(id__in=offering_ids, **accepted_assignment_filter)
            .select_related("tenant", "campus", "academic_year", "term", "course", "section")
            .distinct()
        )
        offerings_map = {row.id: row for row in offerings}
    else:
        offerings_map = {}

    assignment_rows = (
        FacultyAssignment.objects.filter(
            offering_id__in=list(offerings_map.keys()),
            is_active=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at__isnull=False,
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
                "missing_records": None,
                "compliance_stage": "NON_COMPLIANT",
                "offering_id": offering.id,
                "template_period_id": template_period.id if template_period else None,
                "latest_notice_level": None,
                "latest_notice_title": "",
                "latest_notice_issued_at": None,
                "latest_notice_status": None,
            }
        )

    notice_map = {}
    notice_offering_ids = {row["offering_id"] for row in report_rows if row.get("template_period_id")}
    notice_period_ids = {row["template_period_id"] for row in report_rows if row.get("template_period_id")}
    if notice_offering_ids and notice_period_ids:
        notice_rows = SubmissionNonComplianceNotice.objects.filter(
            offering_id__in=notice_offering_ids,
            template_period_id__in=notice_period_ids,
        ).order_by("offering_id", "template_period_id", "-issued_at", "-id")
        for notice in notice_rows:
            key = (notice.offering_id, notice.template_period_id)
            if key not in notice_map:
                notice_map[key] = notice

    for row in report_rows:
        template_period_id = row.get("template_period_id")
        if not template_period_id:
            continue
        latest_notice = notice_map.get((row["offering_id"], template_period_id))
        if latest_notice is None:
            continue
        row["latest_notice_level"] = latest_notice.notice_level
        row["latest_notice_title"] = latest_notice.title
        row["latest_notice_issued_at"] = latest_notice.issued_at
        row["latest_notice_status"] = latest_notice.status

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
        "non_compliant": sum(1 for row in report_rows if row["compliance_stage"] == "NON_COMPLIANT"),
    }
    page_obj = _get_page(request, report_rows, per_page=30)
    page_offering_ids = {row["offering_id"] for row in page_obj.object_list if row.get("template_period_id")}
    page_period_ids = {row["template_period_id"] for row in page_obj.object_list if row.get("template_period_id")}
    page_offerings = {
        offering.id: offering
        for offering in AdminScopeService.scoped_course_offerings(request)
        .filter(id__in=page_offering_ids)
        .select_related("tenant", "campus", "academic_year", "term", "course", "section")
    }
    page_periods = GradingTemplatePeriod.objects.in_bulk(page_period_ids)
    for row in page_obj.object_list:
        template_period_id = row.get("template_period_id")
        offering = page_offerings.get(row["offering_id"])
        template_period = page_periods.get(template_period_id)
        if not offering or not template_period:
            continue
        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=offering,
            template_period=template_period,
        )
        row["missing_records"] = readiness["students_missing_any_grade"]

    context = {
        "page_obj": page_obj,
        "summary": summary,
        "q": request.GET.get("q", ""),
        "period_code": request.GET.get("period_code", ""),
        "campus_options": AdminScopeService.active_scoped_campuses(request).order_by("code"),
        "academic_year_options": AdminScopeService.active_scoped_academic_years(request).order_by("-start_date"),
        "term_options": AdminScopeService.active_scoped_terms(request).order_by("-academic_year__start_date", "sequence_no"),
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
            | Q(offering__faculty_assignments__faculty_user__first_name__icontains=q)
            | Q(offering__faculty_assignments__faculty_user__last_name__icontains=q)
            | Q(offering__faculty_assignments__faculty_user__username__icontains=q)
        )
    queryset = queryset.distinct()
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
    page_offering_ids = {row.offering_id for row in page_obj.object_list}
    assignment_rows = (
        FacultyAssignment.objects.filter(
            offering_id__in=page_offering_ids,
            is_active=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at__isnull=False,
            faculty_user__is_active=True,
        )
        .select_related("faculty_user")
        .order_by("offering_id", "-is_primary", "faculty_user__last_name", "faculty_user__first_name", "id")
    )
    faculty_names_by_offering = {}
    for assignment in assignment_rows:
        faculty_names_by_offering.setdefault(assignment.offering_id, []).append(
            assignment.faculty_user.full_name or assignment.faculty_user.username
        )
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
        row.faculty_names = faculty_names_by_offering.get(row.offering_id, [])

    context = {
        "page_obj": page_obj,
        "q": q,
        "status": request.GET.get("status", ""),
        "terms": AdminScopeService.active_scoped_terms(request),
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
            | Q(initiated_by_user__username__icontains=q)
            | Q(justification__icontains=q)
        )
    context = {
        "page_obj": _get_page(request, queryset),
        "q": q,
        "status": request.GET.get("status", ""),
        "terms": AdminScopeService.active_scoped_terms(request),
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
    can_review_request = reopen_request.status == GradeSubmissionReopenRequest.Status.PENDING
    if request.method == "POST" and not can_review_request:
        messages.error(request, "Only pending reopen requests can be reviewed.")
        return _redirect_back_or_default(request, "admin_portal:grade_submission_reopen_request_list")

    form = GradeSubmissionReopenReviewForm(request.POST or None)
    _style_form(form)
    if can_review_request and request.method == "POST" and form.is_valid():
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
                try:
                    updated = GradingGovernanceService.review_reopen_request(
                        request_obj=reopen_request,
                        reviewer=request.user,
                        approved=approved,
                        review_remarks=form.cleaned_data.get("review_remarks"),
                    )
                except ValidationError as exc:
                    form.add_error(None, "; ".join(exc.messages))
                else:
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
                        metadata={
                            "critical_action": True,
                            "reason": (form.cleaned_data.get("review_remarks") or "").strip(),
                            "impact_summary": {
                                "offering_id": updated.offering_id,
                                "period_code": updated.template_period.code if updated.template_period_id else "",
                                "decision": "APPROVE" if approved else "REJECT",
                            },
                        },
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
                            metadata={
                                "critical_action": True,
                                "reason": (form.cleaned_data.get("review_remarks") or "").strip(),
                                "impact_summary": {
                                    "offering_id": refreshed_submission.offering_id,
                                    "period_id": refreshed_submission.template_period_id,
                                },
                            },
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
        "can_review_request": can_review_request,
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
        "terms": AdminScopeService.active_scoped_terms(request),
        "status_choices": GradeCorrectionRequest.Status.choices,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/correction_request_list.html", context)


@portal_required("ADMIN")
@permission_required("corrections.create_on_behalf")
def grade_correction_request_create_on_behalf_view(request):
    GradingGovernanceService.auto_lapse_expired_correction_windows()
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    current_campus_id = getattr(request, "scope", {}).get("campus_id")
    request_data = request.POST if request.method == "POST" else request.GET

    campus_queryset = AdminScopeService.active_scoped_campuses(request).order_by("code", "name")
    selected_campus_id = _safe_int(request_data.get("campus")) or current_campus_id
    selected_campus = campus_queryset.filter(id=selected_campus_id).first() if selected_campus_id else campus_queryset.first()
    selected_campus_id = selected_campus.id if selected_campus else None

    academic_year_queryset = AdminScopeService.active_scoped_academic_years(request).order_by("-start_date", "code")
    selected_academic_year_id = _safe_int(request_data.get("academic_year"))
    selected_academic_year = (
        academic_year_queryset.filter(id=selected_academic_year_id).first()
        if selected_academic_year_id
        else academic_year_queryset.first()
    )
    selected_academic_year_id = selected_academic_year.id if selected_academic_year else None

    term_queryset = AdminScopeService.active_scoped_terms(request).select_related("academic_year")
    if selected_academic_year_id:
        term_queryset = term_queryset.filter(academic_year_id=selected_academic_year_id)
    term_queryset = term_queryset.order_by("sequence_no", "code")
    selected_term_id = _safe_int(request_data.get("term"))
    selected_term = term_queryset.filter(id=selected_term_id).first() if selected_term_id else term_queryset.first()
    selected_term_id = selected_term.id if selected_term else None

    selected_scope = ScopeService.build_scope(
        request.user,
        tenant_id=tenant_id,
        campus_id=selected_campus_id,
    )
    selected_department_ids = selected_scope.get("department_ids", [])
    base_offering_queryset = (
        CourseOffering.objects.filter(
            tenant_id=tenant_id,
            campus_id=selected_campus_id,
            academic_year_id=selected_academic_year_id,
            term_id=selected_term_id,
            is_active=True,
            academic_year__is_active=True,
            term__is_active=True,
            campus__is_active=True,
            tenant__is_active=True,
            department__is_active=True,
            program__is_active=True,
            program__department__is_active=True,
            course__is_active=True,
            section__is_active=True,
            section__department__is_active=True,
            section__program__is_active=True,
            section__program__department__is_active=True,
        )
        .filter(Q(course__department__isnull=True) | Q(course__department__is_active=True))
        .filter(
            Q(department_id__in=selected_department_ids)
            | Q(faculty_assignments__faculty_user__default_department_id__in=selected_department_ids)
        )
        .select_related("course", "section", "term", "academic_year", "campus")
        .distinct()
        .order_by("-academic_year__start_date", "term__sequence_no", "course__code", "section__code")
    )

    assignment_queryset = FacultyAssignment.objects.filter(
        offering__in=base_offering_queryset,
        is_active=True,
    ).select_related("faculty_user", "offering", "offering__section", "offering__course")
    faculty_ids = assignment_queryset.values_list("faculty_user_id", flat=True).distinct()
    faculty_queryset = User.objects.filter(id__in=faculty_ids).order_by("last_name", "first_name", "username")
    selected_faculty_id = _safe_int(request_data.get("faculty_user"))
    selected_faculty = faculty_queryset.filter(id=selected_faculty_id).first() if selected_faculty_id else None

    scoped_assignment_queryset = assignment_queryset
    if selected_faculty:
        scoped_assignment_queryset = scoped_assignment_queryset.filter(faculty_user_id=selected_faculty.id)

    section_ids = scoped_assignment_queryset.values_list("offering__section_id", flat=True).distinct()
    section_queryset = Section.objects.filter(id__in=section_ids).order_by("code", "name")
    selected_section_id = _safe_int(request_data.get("section"))
    selected_section = section_queryset.filter(id=selected_section_id).first() if selected_section_id else None

    if selected_section:
        scoped_assignment_queryset = scoped_assignment_queryset.filter(offering__section_id=selected_section.id)

    course_ids = scoped_assignment_queryset.values_list("offering__course_id", flat=True).distinct()
    course_queryset = Course.objects.filter(id__in=course_ids).order_by("code", "title")
    selected_course_id = _safe_int(request_data.get("course"))
    selected_course = course_queryset.filter(id=selected_course_id).first() if selected_course_id else None

    selected_offering = None
    if selected_faculty and selected_section and selected_course:
        selected_offering = (
            base_offering_queryset.filter(
                faculty_assignments__faculty_user_id=selected_faculty.id,
                faculty_assignments__is_active=True,
                section_id=selected_section.id,
                course_id=selected_course.id,
            )
            .distinct()
            .first()
        )

    period_queryset = GradingTemplatePeriod.objects.none()
    if selected_offering:
        try:
            template = FacultyGradingService.resolve_template_for_offering(selected_offering)
            period_queryset = template.periods.filter(is_active=True).order_by("sequence_no", "id")
        except ValidationError:
            template = None
    else:
        template = None

    selected_period_id = _safe_int(request_data.get("template_period"))
    selected_period = period_queryset.filter(id=selected_period_id).first() if selected_period_id else None

    setup_data = request_data.copy()
    if selected_campus_id and not setup_data.get("campus"):
        setup_data["campus"] = str(selected_campus_id)
    if selected_academic_year_id and not setup_data.get("academic_year"):
        setup_data["academic_year"] = str(selected_academic_year_id)
    if selected_term_id and not setup_data.get("term"):
        setup_data["term"] = str(selected_term_id)
    setup_form = GradeCorrectionOnBehalfSetupForm(
        setup_data or None,
        campus_queryset=campus_queryset,
        academic_year_queryset=academic_year_queryset,
        term_queryset=term_queryset,
        faculty_queryset=faculty_queryset,
        section_queryset=section_queryset,
        course_queryset=course_queryset,
        period_queryset=period_queryset,
    )
    _style_form(setup_form)

    enrollments = []
    student_qs = Student.objects.none()
    activity_qs = GradeActivity.objects.none()
    score_lookup = {}
    correction_form = None

    can_file = bool(selected_offering and selected_period and selected_faculty)
    if can_file:
        enrollments = list(FacultyGradingService.get_active_enrollments(selected_offering))
        student_ids = [row.student_id for row in enrollments]
        student_qs = Student.objects.filter(id__in=student_ids).order_by("last_name", "first_name", "student_no")
        activity_qs = (
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
        activity_ids = list(activity_qs.values_list("id", flat=True))
        score_lookup = {
            (row.student_id, row.activity_id): _format_decimal_display(row.raw_score)
            for row in StudentActivityScore.objects.filter(
                activity_id__in=activity_ids,
                student_id__in=student_ids,
                is_active=True,
            )
        }
        correction_form = GradeCorrectionRequestForm(
            request.POST or None,
            request.FILES or None,
            student_queryset=student_qs,
            activity_queryset=activity_qs,
            score_lookup=score_lookup,
        )
        _style_form(correction_form)

    if request.method == "POST":
        if not setup_form.is_valid():
            messages.error(request, "Review the petition setup fields before submitting.")
        elif not can_file:
            messages.error(request, "Select a valid offering, grading period, and original faculty member.")
        elif not GradingGovernanceService.is_system_correction_enabled(tenant_id=selected_offering.tenant_id):
            messages.error(request, "Correction requests are disabled by tenant policy (MANUAL_ONLY).")
        elif not GradingGovernanceService.is_submitted(offering=selected_offering, template_period=selected_period):
            messages.error(request, "On-behalf correction petitions are allowed only after period submission.")
        elif correction_form and correction_form.is_valid():
            try:
                correction = GradingGovernanceService.create_correction_request(
                    user=selected_faculty,
                    initiated_by_user=request.user,
                    request_source=GradeCorrectionRequest.RequestSource.ADMIN_ON_BEHALF,
                    on_behalf_reason=setup_form.cleaned_data.get("on_behalf_reason"),
                    offering=selected_offering,
                    template_period=selected_period,
                    justification=correction_form.cleaned_data["justification"],
                    items=correction_form.cleaned_data["items"],
                )
            except ValidationError as exc:
                correction_form.add_error(None, "; ".join(exc.messages))
            else:
                attachment = correction_form.cleaned_data.get("attachment")
                if attachment:
                    attachment_validation = correction_form.cleaned_data.get("attachment_validation")
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
                        portal="ADMIN",
                        entity_type="GradeCorrectionAttachment",
                        entity_id=correction_attachment.id,
                        actor=request.user,
                        tenant=selected_offering.tenant,
                        campus=selected_offering.campus,
                        after_data={
                            "correction_request_id": correction.id,
                            "original_filename": correction_attachment.original_filename,
                            "stored_filename": correction_attachment.file.name,
                            "content_type": correction_attachment.content_type,
                            "file_size_bytes": correction_attachment.file_size_bytes,
                            "on_behalf": True,
                        },
                        request=request,
                    )
                notification_result = CorrectionNotificationService.send_correction_submission_approval_notifications(
                    request_obj=correction
                )
                AuditService.log_event(
                    action="CREATE_ON_BEHALF",
                    portal="ADMIN",
                    entity_type="GradeCorrectionRequest",
                    entity_id=correction.id,
                    actor=request.user,
                    tenant=selected_offering.tenant,
                    campus=selected_offering.campus,
                    after_data={
                        "offering_id": selected_offering.id,
                        "period_id": selected_period.id,
                        "requested_by_user_id": selected_faculty.id,
                        "initiated_by_user_id": request.user.id,
                        "request_source": correction.request_source,
                        "on_behalf_reason": correction.on_behalf_reason,
                        "correction_item_count": len(correction_form.cleaned_data["items"]),
                        "approval_notification_email_attempted": notification_result["attempted"],
                        "approval_notification_email_sent": notification_result["sent"],
                        "approval_notification_email_recipients": notification_result["recipients"],
                    },
                    request=request,
                )
                if notification_result["errors"]:
                    messages.warning(request, "Petition created, but some approval notification emails could not be sent.")
                messages.success(request, "On-behalf correction petition submitted for review.")
                return redirect("admin_portal:grade_correction_request_review", request_id=correction.id)

    context = {
        "title": "Create Correction Petition On Behalf",
        "setup_form": setup_form,
        "form": correction_form,
        "selected_campus": selected_campus,
        "selected_academic_year": selected_academic_year,
        "selected_term": selected_term,
        "offering_count": base_offering_queryset.count(),
        "faculty_count": faculty_queryset.count(),
        "selected_offering": selected_offering,
        "selected_period": selected_period,
        "selected_faculty": selected_faculty,
        "selected_section": selected_section,
        "selected_course": selected_course,
        "can_file": can_file,
        "is_submitted": (
            GradingGovernanceService.is_submitted(offering=selected_offering, template_period=selected_period)
            if selected_offering and selected_period
            else False
        ),
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
        "selected_grade_activity_ids": set(correction_form.data.getlist("grade_activities")) if correction_form and correction_form.is_bound else set(),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/grading/correction_request_create_on_behalf.html", context)


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
                metadata={
                    "critical_action": True,
                    "reason": (form.cleaned_data.get("review_remarks") or "").strip(),
                    "impact_summary": {
                        "offering_id": updated.offering_id,
                        "period_id": updated.template_period_id,
                        "is_final_step": is_final_step,
                        "auto_apply_on_final_approval": auto_apply_on_final_approval,
                        "decision": "APPROVE" if approved else "REJECT",
                    },
                },
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
    AuditService.log_event(
        action="DOWNLOAD_CORRECTION_OFFICIAL_REPORT",
        portal="ADMIN",
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


@portal_required("ADMIN")
@permission_required("corrections.review")
def grade_correction_attachment_download_view(request, request_id: int, attachment_id: int):
    correction_request = get_object_or_404(
        AdminScopeService.scoped_grade_correction_requests(request),
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
        portal="ADMIN",
        entity_type="GradeCorrectionAttachment",
        entity_id=attachment.id,
        actor=request.user,
        tenant=correction_request.tenant,
        campus=correction_request.campus,
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
