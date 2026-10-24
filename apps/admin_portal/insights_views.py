"""Explicit, CSRF-protected Insights requests; no changes to protected reports."""
import csv
import secrets
from collections import Counter, defaultdict
from urllib.parse import urlencode, urlsplit

from django.core.cache import cache
from django.core import signing
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from apps.admin_portal.academic_performance import AcademicPerformanceInsightService as Official
from apps.admin_portal.insights import (
    Selection, SUPPRESSED, activity_metrics, collect_sections, disclosure_mask, period_trends, releasable_partition, report,
)
from apps.admin_portal.insights_generation import (
    RecordsChanged, authority, changed_response, fingerprint,
)
from apps.core.services.csv_safety import csv_safe
from apps.core.decorators import permission_required, portal_required
from apps.core.services.features import FeatureSettingsService


def enabled(request):
    tenant_id = request.scope.get("tenant_id")
    if not tenant_id or not FeatureSettingsService.is_academic_performance_insights_enabled(tenant_id=tenant_id, default=False):
        raise Http404("Academic Performance Insights is not enabled.")


def page_context(request, context):
    from apps.admin_portal.views import _scope_context
    return {**_scope_context(request), **context}


def parsed(data):
    return {key: Official._safe_int(data.get(key)) for key in (
        "academic_year_id", "term_id", "campus_id", "department_id", "faculty_id",
    )} | {key: (data.get(key) or "").strip() for key in ("course_code", "period_code")}


TOKEN_SALT = "academic-insights-report-v1"


def state_from_token(token, selection):
    try:
        state = signing.loads(token, salt=TOKEN_SALT, max_age=300)
    except signing.BadSignature:
        return None
    return state if state.get("scope") == authority(selection) else None


def require_generation(request, filters, expected):
    if fingerprint(Selection(request), filters) != expected:
        raise RecordsChanged


def snapshot_data(selection, filters, generation):
    result, raw = report(selection, filters)
    return {"result": result, "raw": raw, "filters": filters, "generation": generation,
            "sections": selection._sections, "periods": selection._periods,
            "details": {}}


def consistency_url(selection, section):
    offering = section["offering"]
    # Offering ownership is not the supervising department. Use a supported faculty filter.
    faculty_ids = [a.faculty_user_id for a in selection.assignments if a.offering_id == offering.id]
    return reverse("admin_portal:academic_activity_consistency") + "?" + urlencode({
        "academic_year_id": offering.academic_year_id, "term_id": offering.term_id,
        "campus_id": offering.campus_id, "period_code": section["period"].code,
        "scope_campus_id": offering.campus_id,
        "scope_tenant_id": offering.tenant_id,
        "course_code": offering.course.code, "faculty_id": section.get("faculty_id") or (faculty_ids[0] if faculty_ids else ""),
    })


def metrics_for(raw, selection):
    return activity_metrics(
        {s["offering"].id: s["offering"] for s in raw},
        {s["offering"].id: s["period"].id for s in raw},
        {s["offering"].id: {r["student_id"] for r in s["coverage"]["rows"] if r["usable_grade"]} for s in raw},
        {s["offering"].id: {r["student_id"] for r in s["coverage"]["rows"]} for s in raw},
        {s["offering"].id for s in raw if selection.allows_aggregates(s["offering"])},
    )


def detail_context(selection, public, raw):
    if public["suppressed"]:
        return {"row": public, "activity_consistency_url": consistency_url(selection, raw)}
    return {"row": public, "metrics": metrics_for([raw], selection)[public["id"]],
            "trends": period_trends(selection, raw["offering"], raw),
            "activity_consistency_url": consistency_url(selection, raw)}


def export_csv(request, selection, result, raw, kind):
    from apps.admin_portal.views import (
        _can_view_gradebook_student_identity, _mask_student_name, _mask_student_number,
    )
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="academic_performance_{kind}.csv"'
    writer = csv.writer(response)
    if kind == "sections":
        writer.writerow(["Campus", "Academic Year", "Term", "Course", "Section", "Faculty", "Grading Period",
                         "Template/Profile", "Formula", "Passing Threshold", "Active Enrollment", "Usable Computed Grades",
                         "No Usable Grade", "Grade Coverage %", "Passing Count", "Passing Rate %", "Below Threshold Count",
                         "Below Threshold Rate %", "Average", "Median", "Highest", "Lowest", "Readiness State", "Comparison Status",
                         "Report Completeness"])
        for row in result["rows"]:
            writer.writerow([csv_safe(str(value)) for value in (
                row["campus"], row["year"], row["term"], row["course"], row["section"], row["faculty"], row["period"],
                row["template"] + " / " + row["profile"], row["formula"], row["threshold"], row["enrolled"], row["graded"],
                row["no_grade"], row["coverage_rate"], row["passing"], row["passing_rate"], row["below"], row["below_rate"],
                row["average"], row["median"], SUPPRESSED, SUPPRESSED, row["state"], "Review compatible class details",
                "Complete authorized selection; student export protections remain separate",
            )])
    else:
        writer.writerow(["Campus", "Academic Year", "Term", "Course", "Section", "Grading Period", "Student", "Student No.",
                         "Current Grade", "Usable Computed Grade", "Passing Threshold", "Missing Records", "Advisory Label", "Indicators", "Report Completeness"])
        visible_ids = {r["id"] for r in result["rows"] if not r["suppressed"]}
        basis = collect_sections(selection, parsed(request.GET))
        hidden = disclosure_mask(basis)
        visible = [s for pk, s in basis.items() if pk not in hidden and not s["invalid_scope"]]
        review = Official.get_students_for_review(request, {}, comparison={"rows": visible, "limited": False})["rows"]
        memberships = defaultdict(set)
        for row in review:
            memberships[row["student_id"]].add(row["offering"].id)
        atoms = Counter(frozenset(ids) for ids in memberships.values())
        withheld = {pk for ids, count in atoms.items() if count < 5 for pk in ids}
        groups = {}
        for section in visible:
            pk = section["offering"].id
            members = [r for r in review if r["offering"].id == pk]
            total = section["coverage"]["active_enrollment_count"]
            # Do not disclose a small advisory subgroup or its complementary remainder.
            if pk in visible_ids and pk not in withheld and len(members) >= 5 and releasable_partition(len(members), total):
                groups[pk] = members
        for members in groups.values():
            for row in members:
                offering = row["offering"]
                identity = _can_view_gradebook_student_identity(request.user, tenant_id=offering.tenant_id, campus_id=offering.campus_id)
                student = row["student"]
                name = f"{student.first_name} {student.last_name}" if identity else _mask_student_name(student)
                number = student.student_no if identity else _mask_student_number(student.student_no)
                # Individual exact grades/score counts are one-student analytics, even in a large class.
                writer.writerow([csv_safe(str(value)) for value in (
                    offering.campus.code, offering.academic_year.code, offering.term.code, row["course_code"], row["section"],
                    row["period"].name, name, number, SUPPRESSED, SUPPRESSED, row["passing_threshold"], SUPPRESSED,
                    "Academic Concern - For Faculty Review", "Review authorized Faculty Gradebook", "Privacy-safe advisory selection",
                )])
    response["Cache-Control"] = "private, no-store"
    return response


@portal_required("ADMIN")
@permission_required("grading_analytics.read")
@require_http_methods(["GET", "POST"])
@changed_response
def academic_performance_insights_view(request):
    enabled(request)
    selection = Selection(request)
    data = request.POST if request.method == "POST" else request.GET
    filters = parsed(data)
    if request.method == "GET" and data.get("export") not in {"sections", "students"}:
        normalized, options = selection.options(filters)
        response = render(request, "admin_portal/grading/academic_performance_insights.html", page_context(request, {
            "filters": normalized, "options": options, "can_compare_campuses": selection.role["can_compare_campuses"],
        }))
    elif request.method == "POST" and data.get("action") == "options":
        normalized, options = selection.options(filters)
        response = JsonResponse({"filters": normalized, "options": options})
    elif request.method == "GET" or data.get("action") == "report":
        normalized, options = selection.options(filters)
        if any(value and normalized[key] != value for key, value in filters.items()) or not Official.required_filters_present(normalized):
            return JsonResponse({"error": "Select valid filters and click View Performance."}, status=400)
        filters = normalized
        if request.method == "GET" and data.get("token"):
            state = state_from_token(data["token"], selection)
            if not state or state["filters"] != filters:
                return JsonResponse({"error": "Report expired or academic scope changed. Click View Performance again."}, status=409)
            generation = state["generation"]
            if fingerprint(selection, filters) != generation:
                raise RecordsChanged
        else:
            generation = fingerprint(selection, filters)
        # Load model instances after the opening fingerprint, not before it.
        selection = Selection(request)
        snapshot = snapshot_data(selection, filters, generation)
        result, raw = snapshot["result"], snapshot["raw"]
        if request.method == "GET":
            response = export_csv(request, selection, result, raw, data["export"])
            require_generation(request, filters, generation)
            return response
        state = {"nonce": secrets.token_urlsafe(32), "scope": authority(selection),
                 "generation": generation, "filters": filters}
        token = signing.dumps(state, salt=TOKEN_SALT, compress=True)
        response = JsonResponse({"token": token, "summary": result["summary"],
            "html": render_to_string("admin_portal/grading/_insights_summary.html", result, request=request)})
        require_generation(request, filters, generation)
        cache.set("academic-insights:" + state["nonce"], snapshot, timeout=300)
    elif data.get("action") in {"classes", "detail"}:
        state = state_from_token(data.get("token") or "", selection)
        if not state:
            return JsonResponse({"error": "Report expired or academic scope changed. Click View Performance again."}, status=409)
        filters, generation = state["filters"], state["generation"]
        if fingerprint(selection, filters) != generation:
            raise RecordsChanged
        selection = Selection(request)
        snapshot = cache.get("academic-insights:" + state["nonce"])
        if snapshot is None or snapshot["generation"] != generation:
            # Cache-independent recovery on another worker; only unchanged records qualify.
            snapshot = snapshot_data(selection, filters, generation)
        selection._sections, selection._periods = snapshot["sections"], snapshot["periods"]
        result, raw = snapshot["result"], snapshot["raw"]
        if data["action"] == "classes":
            page = Paginator(result["rows"], 25).get_page(data.get("page"))
            ids = {r["id"] for r in page.object_list if not r["suppressed"]}
            metrics = metrics_for([s for s in raw if s["offering"].id in ids], selection)
            rows = [{**r, "metrics": metrics.get(r["id"])} for r in page.object_list]
            response = JsonResponse({"html": render_to_string("admin_portal/grading/_insights_classes.html", {
                "rows": rows, "page": page,
            }, request=request), "page": page.number, "pages": page.paginator.num_pages})
        else:
            pk = Official._safe_int(data.get("offering_id"))
            row = next((r for r in result["rows"] if r["id"] == pk), None)
            section = next((s for s in raw if s["offering"].id == pk), None)
            if row is None or section is None:
                raise Http404
            if pk not in snapshot["details"]:
                snapshot["details"][pk] = render_to_string("admin_portal/grading/_insights_detail.html",
                    detail_context(selection, row, section), request=request)
            response = JsonResponse({"html": snapshot["details"][pk]})
        require_generation(request, filters, generation)
        cache.set("academic-insights:" + state["nonce"], snapshot, timeout=300)
    else:
        return JsonResponse({"error": "Unknown report action."}, status=400)
    response["Cache-Control"] = "private, no-store"
    return response


@portal_required("ADMIN")
@permission_required("grading_analytics.read")
@require_http_methods(["GET"])
@changed_response
def academic_performance_section_detail_view(request, offering_id, period_code):
    enabled(request)
    selection = Selection(request)
    offering = selection.offerings.get(offering_id)
    if not offering:
        raise Http404
    filters = {"academic_year_id": offering.academic_year_id, "term_id": offering.term_id,
               "period_code": period_code, "campus_id": offering.campus_id,
               "department_id": None, "course_code": offering.course.code, "faculty_id": None}
    generation = fingerprint(selection, filters)
    selection = Selection(request)
    result, raw = report(selection, filters)
    row = next((r for r in result["rows"] if r["id"] == offering_id), None)
    section = next((s for s in raw if s["offering"].id == offering_id), None)
    if not row or not section:
        raise Http404
    context = detail_context(selection, row, section)
    insights_url = reverse("admin_portal:academic_performance_insights")
    next_url = request.GET.get("next") or ""
    allowed = {insights_url, reverse("admin_portal:academic_activity_consistency")}
    context["back_url"] = next_url if url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ) and urlsplit(next_url).path in allowed else insights_url
    response = render(request, "admin_portal/grading/academic_performance_section_detail.html", page_context(request, context))
    require_generation(request, filters, generation)
    response["Cache-Control"] = "private, no-store"
    return response
