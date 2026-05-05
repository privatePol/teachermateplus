from __future__ import annotations

import os
import secrets
import logging
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db.models import F, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from apps.core.services.api_keys import ApiRateLimitService, TenantApiKeyService
from apps.core.services.audit import AuditService
from apps.enrollment.models import Enrollment
from apps.grading.models import GradeSubmission, StudentPeriodGrade
from apps.tenants.models import Campus, Tenant

logger = logging.getLogger("edugradespro.api")


def _configured_sis_api_token() -> str:
    token = getattr(settings, "SIS_API_TOKEN", "")
    if token:
        return token
    return os.getenv("SIS_API_TOKEN", "")


def _request_api_token(request) -> str:
    header_token = request.META.get("HTTP_X_API_TOKEN", "").strip()
    if header_token:
        return header_token
    authorization = request.META.get("HTTP_AUTHORIZATION", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _json_error(message: str, *, status: int, retry_after: int | None = None):
    response = JsonResponse({"ok": False, "error": message}, status=status)
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


def _legacy_sis_token_enabled() -> bool:
    return bool(getattr(settings, "SIS_API_LEGACY_TOKEN_ENABLED", True))


def _audit_sis_api_access(
    *,
    action: str,
    request,
    tenant=None,
    campus=None,
    status_code: int,
    auth_mode: str,
    api_key=None,
    metadata: dict | None = None,
):
    payload = {
        "status_code": status_code,
        "auth_mode": auth_mode,
        "tenant_code": request.GET.get("tenant_code", ""),
        "campus_code": request.GET.get("campus_code", ""),
        "key_prefix": api_key.key_prefix if api_key else "",
    }
    payload.update(metadata or {})
    log_method = logger.warning if action in {"DENY", "RATE_LIMIT"} else logger.info
    log_method(
        "SIS API %s status=%s auth_mode=%s tenant=%s campus=%s key_prefix=%s",
        action,
        status_code,
        auth_mode,
        payload.get("tenant_code") or getattr(tenant, "code", ""),
        payload.get("campus_code") or getattr(campus, "code", ""),
        payload.get("key_prefix", ""),
    )
    AuditService.log_event(
        action=action,
        portal="API",
        entity_type="SISPeriodicGradesAPI",
        actor=None,
        tenant=tenant,
        campus=campus,
        metadata=payload,
        request=request,
    )


def _authenticate_sis_api_request(request):
    request_token = _request_api_token(request)
    if not request_token:
        return None, _json_error("Unauthorized. Provide a valid API token.", status=401)

    key_auth = TenantApiKeyService.authenticate_sis_token(request_token)
    if key_auth.ok:
        rate = ApiRateLimitService.check(request, api_key=key_auth.tenant_api_key)
        if not rate.ok:
            _audit_sis_api_access(
                action="RATE_LIMIT",
                request=request,
                tenant=key_auth.tenant_api_key.tenant,
                status_code=rate.status_code,
                auth_mode="TENANT_API_KEY",
                api_key=key_auth.tenant_api_key,
                metadata={"reason": rate.error},
            )
            return key_auth, _json_error(rate.error, status=rate.status_code, retry_after=rate.retry_after_seconds)
        return key_auth, None

    configured_token = _configured_sis_api_token()
    if (
        _legacy_sis_token_enabled()
        and configured_token
        and secrets.compare_digest(request_token, configured_token)
    ):
        rate = ApiRateLimitService.check(request, legacy_token=True)
        if not rate.ok:
            _audit_sis_api_access(
                action="RATE_LIMIT",
                request=request,
                status_code=rate.status_code,
                auth_mode="LEGACY_TOKEN",
                metadata={"reason": rate.error},
            )
            return key_auth, _json_error(rate.error, status=rate.status_code, retry_after=rate.retry_after_seconds)
        key_auth.ok = True
        key_auth.legacy_token = True
        key_auth.error = ""
        return key_auth, None

    rate = ApiRateLimitService.check(request)
    _audit_sis_api_access(
        action="DENY",
        request=request,
        status_code=401,
        auth_mode="INVALID",
        metadata={"reason": key_auth.error or "Invalid token."},
    )
    if rate.rate_limited:
        return key_auth, _json_error(rate.error, status=rate.status_code, retry_after=rate.retry_after_seconds)
    return key_auth, _json_error("Unauthorized. Provide a valid API token.", status=401)


def _parse_iso_datetime(param_name: str, value: str):
    parsed = parse_datetime(value)
    if parsed is None:
        return None, f"Invalid '{param_name}' datetime. Use ISO format, e.g. 2026-03-22T00:00:00+08:00."
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed, None


def _format_official_grade(value):
    if value is None:
        return None
    return format(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP), "f")


@require_GET
def sis_periodic_grades_api_view(request):
    auth, auth_error = _authenticate_sis_api_request(request)
    if auth_error:
        return auth_error

    tenant_code = (request.GET.get("tenant_code") or "").strip()
    if not tenant_code:
        return _json_error("Missing required query parameter: tenant_code.", status=400)
    try:
        tenant = Tenant.objects.get(code=tenant_code, is_active=True)
    except Tenant.DoesNotExist:
        _audit_sis_api_access(
            action="DENY",
            request=request,
            status_code=403,
            auth_mode="TENANT_API_KEY" if auth and auth.tenant_api_key else "LEGACY_TOKEN",
            api_key=auth.tenant_api_key if auth else None,
            metadata={"reason": "Unknown or inactive tenant."},
        )
        return _json_error("Tenant is not available for API access.", status=403)
    if auth and auth.tenant_api_key and auth.tenant_api_key.tenant_id != tenant.id:
        _audit_sis_api_access(
            action="DENY",
            request=request,
            tenant=auth.tenant_api_key.tenant,
            status_code=403,
            auth_mode="TENANT_API_KEY",
            api_key=auth.tenant_api_key,
            metadata={"reason": "API key tenant does not match requested tenant."},
        )
        return _json_error("API key is not authorized for the requested tenant.", status=403)

    campus_code = (request.GET.get("campus_code") or "").strip()
    academic_year_code = (request.GET.get("academic_year_code") or "").strip()
    term_code = (request.GET.get("term_code") or "").strip()
    period_code = (request.GET.get("period_code") or "").strip()
    course_code = (request.GET.get("course_code") or "").strip()
    section_code = (request.GET.get("section_code") or "").strip()
    student_no = (request.GET.get("student_no") or "").strip()
    updated_since_raw = (request.GET.get("updated_since") or "").strip()
    submitted_since_raw = (request.GET.get("submitted_since") or "").strip()

    try:
        page = max(int(request.GET.get("page", "1")), 1)
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid 'page'. Must be an integer >= 1."}, status=400)
    try:
        page_size = int(request.GET.get("page_size", "500"))
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid 'page_size'. Must be an integer."}, status=400)
    page_size = min(max(page_size, 1), 2000)

    # Guardrails for campus-separated SIS integrations:
    # student_no lookups must be scoped by campus + section to avoid ambiguous identity matches.
    if section_code and not campus_code:
        return _json_error("Query parameter 'campus_code' is required when 'section_code' is provided.", status=400)
    if student_no and not campus_code:
        return _json_error("Query parameter 'campus_code' is required when 'student_no' is provided.", status=400)
    if student_no and not section_code:
        return _json_error("Query parameter 'section_code' is required when 'student_no' is provided.", status=400)

    campus = None
    if campus_code:
        campus = Campus.objects.filter(tenant=tenant, code=campus_code, is_active=True).first()
        if campus is None:
            _audit_sis_api_access(
                action="DENY",
                request=request,
                tenant=tenant,
                status_code=403,
                auth_mode="TENANT_API_KEY" if auth and auth.tenant_api_key else "LEGACY_TOKEN",
                api_key=auth.tenant_api_key if auth else None,
                metadata={"reason": "Campus is not active or does not belong to requested tenant."},
            )
            return _json_error("Campus is not available for the requested tenant.", status=403)

    updated_since = None
    if updated_since_raw:
        updated_since, err = _parse_iso_datetime("updated_since", updated_since_raw)
        if err:
            return _json_error(err, status=400)

    submitted_since = None
    if submitted_since_raw:
        submitted_since, err = _parse_iso_datetime("submitted_since", submitted_since_raw)
        if err:
            return _json_error(err, status=400)

    submitted_at_subquery = GradeSubmission.objects.filter(
        offering_id=OuterRef("offering_id"),
        template_period_id=OuterRef("template_period_id"),
        status=GradeSubmission.Status.SUBMITTED,
    ).values("submitted_at")[:1]
    submission_remarks_subquery = GradeSubmission.objects.filter(
        offering_id=OuterRef("offering_id"),
        template_period_id=OuterRef("template_period_id"),
        status=GradeSubmission.Status.SUBMITTED,
    ).values("remarks")[:1]

    queryset = (
        StudentPeriodGrade.objects.select_related(
            "tenant",
            "campus",
            "offering__academic_year",
            "offering__term",
            "offering__course",
            "offering__section",
            "student",
            "template_period",
        )
        .annotate(
            submitted_at=Subquery(submitted_at_subquery),
            submission_remarks=Subquery(submission_remarks_subquery),
        )
        .filter(
            tenant_id=tenant.id,
            tenant__is_active=True,
            campus__tenant_id=tenant.id,
            campus__is_active=True,
            offering__is_active=True,
            offering__tenant_id=tenant.id,
            offering__campus_id=F("campus_id"),
            offering__tenant__is_active=True,
            offering__campus__is_active=True,
            student__tenant_id=tenant.id,
            student__campus_id=F("campus_id"),
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
            student__is_active=True,
            student__department__is_active=True,
            offering__grade_submissions__status=GradeSubmission.Status.SUBMITTED,
            offering__grade_submissions__template_period_id=F("template_period_id"),
        )
        .filter(Q(offering__course__department__isnull=True) | Q(offering__course__department__is_active=True))
        .filter(Q(student__program__isnull=True) | Q(student__program__is_active=True))
        .order_by(
            "tenant__code",
            "campus__code",
            "offering__academic_year__code",
            "offering__term__sequence_no",
            "template_period__sequence_no",
            "offering__course__code",
            "offering__section__code",
            "student__student_no",
            "id",
        )
    )

    if campus_code:
        queryset = queryset.filter(campus_id=campus.id)
    if academic_year_code:
        queryset = queryset.filter(offering__academic_year__code=academic_year_code)
    if term_code:
        queryset = queryset.filter(offering__term__code=term_code)
    if period_code:
        queryset = queryset.filter(template_period__code__iexact=period_code)
    if course_code:
        queryset = queryset.filter(offering__course__code__iexact=course_code)
    if section_code:
        queryset = queryset.filter(offering__section__code__iexact=section_code)
    if student_no:
        queryset = queryset.filter(student__student_no__iexact=student_no)
    if updated_since:
        queryset = queryset.filter(updated_at__gte=updated_since)
    if submitted_since:
        queryset = queryset.filter(submitted_at__gte=submitted_since)

    total_count = queryset.count()
    start = (page - 1) * page_size
    end = start + page_size
    rows = list(queryset[start:end])

    enrollment_status_lookup = {
        (item["course_offering_id"], item["student_id"]): item["enrollment_status"]
        for item in Enrollment.objects.filter(
            course_offering_id__in={row.offering_id for row in rows},
            student_id__in={row.student_id for row in rows},
            is_active=True,
        ).values("course_offering_id", "student_id", "enrollment_status")
    }

    results = []
    for row in rows:
        student_full_name = ", ".join(
            part for part in [row.student.last_name, row.student.first_name, row.student.middle_name] if part
        )
        results.append(
            {
                "tenant_code": row.tenant.code,
                "campus_code": row.campus.code,
                "academic_year_code": row.offering.academic_year.code,
                "term_code": row.offering.term.code,
                "period_code": row.template_period.code,
                "period_name": row.template_period.name,
                "course_code": row.offering.course.code,
                "course_title": row.offering.course.title,
                "section_code": row.offering.section.code,
                "student_no": row.student.student_no,
                "student_name": student_full_name,
                "student_status": enrollment_status_lookup.get((row.offering_id, row.student_id), "UNKNOWN"),
                "class_standing_grade": _format_official_grade(row.class_standing_grade),
                "exam_grade": _format_official_grade(row.exam_grade),
                "period_grade": _format_official_grade(row.period_grade),
                "is_finalized": bool(row.is_finalized),
                "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                "submission_remarks": row.submission_remarks,
                "computed_at": row.computed_at.isoformat() if row.computed_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )

    if auth and auth.tenant_api_key:
        TenantApiKeyService.mark_used(auth.tenant_api_key)
    _audit_sis_api_access(
        action="READ",
        request=request,
        tenant=tenant,
        campus=campus,
        status_code=200,
        auth_mode="TENANT_API_KEY" if auth and auth.tenant_api_key else "LEGACY_TOKEN",
        api_key=auth.tenant_api_key if auth else None,
        metadata={
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "returned_count": len(results),
        },
    )

    return JsonResponse(
        {
            "ok": True,
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "results": results,
        }
    )
