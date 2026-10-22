"""Unified preview: one set of filters, lazy existing report engines."""
import hashlib
import secrets

from django.core import signing
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from apps.core.decorators import permission_required, portal_required
from apps.admin_portal import assessment_alignment, learning_insights
from apps.admin_portal.insights_generation import RecordsChanged, authority, changed_response, encoded
from apps.admin_portal.insights_views import enabled, page_context
from apps.admin_portal.learning_insights_views import parsed
from apps.admin_portal.unified_insights import present

TOKEN_SALT = "unified-academic-insights-v1"
TABS = {"overview": "learning-insights", "exams": "assessment-alignment", "alignment": "assessment-alignment"}


@portal_required("ADMIN")
@permission_required("grading_analytics.read")
@require_http_methods(["GET", "POST"])
@changed_response
def unified_insights_view(request):
    enabled(request)
    selection = learning_insights.LearningSelection(request)
    data = request.POST if request.method == "POST" else request.GET
    try:
        filters = parsed(data)
    except ValueError:
        return JsonResponse({"error": "Select valid academic filters."}, status=400)
    normalized, options = selection.options(filters)
    if request.method == "GET":
        response = render(request, "admin_portal/grading/unified_insights.html", page_context(request,
            {"filters": normalized, "options": options}))
    elif data.get("action") == "options":
        response = JsonResponse({"filters": normalized, "options": options})
    elif data.get("action") in {"report", "page"}:
        tab = data.get("tab", "overview")
        if tab not in TABS:
            return JsonResponse({"error": "Select a valid analysis tab."}, status=400)
        engine = TABS[tab]
        backend = learning_insights if tab == "overview" else assessment_alignment
        fingerprint = learning_insights.learning_fingerprint if tab == "overview" else assessment_alignment.assessment_fingerprint
        state = None
        scope = authority(selection)
        if data["action"] == "page":
            try:
                state = signing.loads(data.get("token", ""), salt=TOKEN_SALT, max_age=300)
            except signing.BadSignature:
                pass
            if not state or state.get("scope") != scope or state.get("engine") != engine:
                return JsonResponse({"error": "Report expired or access changed. Click View Performance again."}, status=409)
            # Never let a retained token silently replace currently chosen filters.
            if state["filters"] != filters:
                raise RecordsChanged
        if filters != normalized or not filters["academic_year_id"] or not filters["term_id"]:
            return JsonResponse({"error": "Select valid filters and click View Performance."}, status=400)
        generation = fingerprint(selection, filters)
        if state and state["generation"] != generation:
            raise RecordsChanged
        # Identical to the existing endpoints' public-result cache keys. Exam
        # Comparison and Alignment share one engine/result; no duplicate build.
        result_key = engine + "-result:" + hashlib.sha256(encoded([scope, generation, filters])).hexdigest()
        result = cache.get(result_key)
        if result is None:
            selection.refresh_offerings()
            result = backend.build_report(selection, filters)
        context, charts = present(result, selection, filters, tab, data.get("page"))
        if not state:
            state = {"scope": scope, "generation": generation, "filters": filters,
                "engine": engine, "nonce": secrets.token_urlsafe(32)}
        payload = {"token": signing.dumps(state, salt=TOKEN_SALT, compress=True), "tab": tab,
            "charts": charts, "export_periods": result["export_periods"],
            "html": render_to_string("admin_portal/grading/_unified_insights_results.html", context, request=request)}
        closing = learning_insights.LearningSelection(request)
        if authority(closing) != scope or fingerprint(closing, filters) != generation:
            raise RecordsChanged
        cache.set(result_key, result, timeout=300)
        response = JsonResponse(payload)
    else:
        return JsonResponse({"error": "Unknown report action."}, status=400)
    response["Cache-Control"] = "private, no-store"
    return response
