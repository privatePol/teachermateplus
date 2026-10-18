"""Authorized preview only; existing dashboard, menu and CSV contracts stay independent."""
import secrets

from django.core import signing
from django.core.cache import cache
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from apps.core.decorators import permission_required, portal_required
from apps.admin_portal.insights_generation import RecordsChanged, authority, changed_response
from apps.admin_portal.insights_views import enabled, page_context
from apps.admin_portal.learning_insights import LearningSelection, build_report, learning_fingerprint


TOKEN_SALT = "learning-insights-preview-v1"


def parsed(data):
    values = {"course_code": (data.get("course_code") or "").strip()}
    for name in ("campus_id", "academic_year_id", "term_id", "offering_id"):
        raw = str(data.get(name) or "").strip()
        if raw and (not raw.isascii() or not raw.isdigit() or len(raw) > 12 or int(raw) <= 0):
            raise ValueError("Invalid selection.")
        values[name] = int(raw) if raw else None
    return values


def response_data(request, result, token, page_number):
    # Large selections remain bounded in the DOM, chart and textual equivalents.
    items = result["classifications"]
    bars = result["charts"]["bars"]
    paginator = Paginator(range(max(len(items), len(bars))), 25)
    page = paginator.get_page(page_number)
    start, stop = (page.number - 1) * 25, page.number * 25
    context = {**result, "classifications": items[start:stop], "bars": bars[start:stop], "page": page}
    charts = {**result["charts"], "bars": bars[start:stop]}
    return {"token": token, "charts": charts, "export_periods": result["export_periods"],
            "html": render_to_string("admin_portal/grading/_learning_insights_results.html", context, request=request)}


@portal_required("ADMIN")
@permission_required("grading_analytics.read")
@require_http_methods(["GET", "POST"])
@changed_response
def learning_performance_insights_view(request):
    enabled(request)
    selection = LearningSelection(request)
    data = request.POST if request.method == "POST" else request.GET
    try:
        filters = parsed(data)
    except ValueError:
        return JsonResponse({"error": "Select valid academic filters."}, status=400)
    normalized, options = selection.options(filters)
    if request.method == "GET":
        response = render(request, "admin_portal/grading/learning_performance_insights.html", page_context(request, {
            "filters": normalized, "options": options,
        }))
    elif data.get("action") == "options":
        response = JsonResponse({"filters": normalized, "options": options})
    elif data.get("action") in {"report", "page"}:
        state = None
        if data["action"] == "page":
            try:
                state = signing.loads(data.get("token", ""), salt=TOKEN_SALT, max_age=300)
            except signing.BadSignature:
                state = None
            if not state or state.get("scope") != authority(selection):
                return JsonResponse({"error": "Report expired or access changed. Click View Performance again."}, status=409)
            filters = state["filters"]
            normalized, options = selection.options(filters)
        if filters != normalized or not filters["academic_year_id"] or not filters["term_id"]:
            return JsonResponse({"error": "Select valid filters and click View Performance."}, status=400)
        generation = learning_fingerprint(selection, filters)
        if state and state["generation"] != generation:
            raise RecordsChanged
        token = data.get("token") if state else None
        result = cache.get("learning-insights:" + state["nonce"]) if state else None
        if result is None:
            # Hydrate records after the opening fingerprint, just like the existing report.
            selection = LearningSelection(request)
            result = build_report(selection, filters)
        if not state:
            state = {"scope": authority(selection), "generation": generation, "filters": filters,
                     "nonce": secrets.token_urlsafe(32)}
            token = signing.dumps(state, salt=TOKEN_SALT, compress=True)
        payload = response_data(request, result, token, data.get("page"))
        if learning_fingerprint(LearningSelection(request), filters) != generation:
            raise RecordsChanged
        cache.set("learning-insights:" + state["nonce"], result, timeout=300)
        response = JsonResponse(payload)
    else:
        return JsonResponse({"error": "Unknown report action."}, status=400)
    response["Cache-Control"] = "private, no-store"
    return response
