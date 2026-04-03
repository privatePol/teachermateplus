from __future__ import annotations

import os
import secrets

from django.conf import settings
from django.db.models import F, OuterRef, Subquery
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from apps.enrollment.models import Enrollment
from apps.grading.models import GradeSubmission, StudentPeriodGrade


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


def _parse_iso_datetime(param_name: str, value: str):
    parsed = parse_datetime(value)
    if parsed is None:
        return None, f"Invalid '{param_name}' datetime. Use ISO format, e.g. 2026-03-22T00:00:00+08:00."
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed, None


@require_GET
def sis_periodic_grades_api_view(request):
    configured_token = _configured_sis_api_token()
    if not configured_token:
        return JsonResponse(
            {
                "ok": False,
                "error": "SIS API token is not configured on the server.",
            },
            status=503,
        )

    request_token = _request_api_token(request)
    if not request_token or not secrets.compare_digest(request_token, configured_token):
        return JsonResponse(
            {
                "ok": False,
                "error": "Unauthorized. Provide a valid API token.",
            },
            status=401,
        )

    tenant_code = (request.GET.get("tenant_code") or "").strip()
    if not tenant_code:
        return JsonResponse(
            {
                "ok": False,
                "error": "Missing required query parameter: tenant_code.",
            },
            status=400,
        )

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
        return JsonResponse(
            {
                "ok": False,
                "error": "Query parameter 'campus_code' is required when 'section_code' is provided.",
            },
            status=400,
        )
    if student_no and not campus_code:
        return JsonResponse(
            {
                "ok": False,
                "error": "Query parameter 'campus_code' is required when 'student_no' is provided.",
            },
            status=400,
        )
    if student_no and not section_code:
        return JsonResponse(
            {
                "ok": False,
                "error": "Query parameter 'section_code' is required when 'student_no' is provided.",
            },
            status=400,
        )

    updated_since = None
    if updated_since_raw:
        updated_since, err = _parse_iso_datetime("updated_since", updated_since_raw)
        if err:
            return JsonResponse({"ok": False, "error": err}, status=400)

    submitted_since = None
    if submitted_since_raw:
        submitted_since, err = _parse_iso_datetime("submitted_since", submitted_since_raw)
        if err:
            return JsonResponse({"ok": False, "error": err}, status=400)

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
            tenant__code=tenant_code,
            offering__grade_submissions__status=GradeSubmission.Status.SUBMITTED,
            offering__grade_submissions__template_period_id=F("template_period_id"),
        )
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
        queryset = queryset.filter(campus__code=campus_code)
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
                "class_standing_grade": str(row.class_standing_grade) if row.class_standing_grade is not None else None,
                "exam_grade": str(row.exam_grade) if row.exam_grade is not None else None,
                "period_grade": str(row.period_grade) if row.period_grade is not None else None,
                "is_finalized": bool(row.is_finalized),
                "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
                "submission_remarks": row.submission_remarks,
                "computed_at": row.computed_at.isoformat() if row.computed_at else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
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
