"""Read-only Admin dashboard selectors and fragment endpoints."""
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from django.contrib.sessions.models import Session
from django.core import signing
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache

from apps.accounts.models import ActivePortalSession, User
from apps.academics.services import AcademicGovernanceService
from apps.core.decorators import portal_required, permission_required
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.enrollment.models import ClassListChangeRequest
from apps.grading.models import GradeCorrectionRequest, GradeSubmissionReopenRequest
from apps.grading.services import GradingGovernanceService
from .services import AdminScopeService


def logged_in_users(request, *, now=None):
    """Distinct active users with a registered, unexpired DB session.

    SessionTimeoutMiddleware saves the real sliding idle deadline in expire_date.
    Audit timestamps are deliberately not used as idle-time proxies.
    """
    live = ActivePortalSession.objects.filter(
        user_id=OuterRef("pk"),
        session_key__in=Session.objects.filter(expire_date__gt=now or timezone.now()).values("session_key"),
    )
    users = User.objects.filter(is_active=True)
    scope = getattr(request, "scope", {})
    # A selected campus/department must not broaden to every tenant-wide user.
    if not request.user.is_superuser:
        roles = Q(user_roles__is_active=True, user_roles__role__is_active=True)
        tenant_id, campus_id = scope.get("tenant_id"), scope.get("campus_id")
        if not tenant_id or not campus_id:
            return users.none()
        departments = scope.get("department_ids", [])
        defaults = Q(default_tenant_id=tenant_id, default_campus_id=campus_id)
        roles &= Q(user_roles__tenant_id=tenant_id, user_roles__campus_id=campus_id)
        if not AdminScopeService._has_unrestricted_department_scope(
            request, scope.get("tenant_ids", []), scope.get("campus_ids", [])
        ):
            defaults &= Q(default_department_id__in=departments)
            roles &= Q(user_roles__department_id__in=departments)
        users = users.filter(defaults | roles).distinct()
    else:
        if scope.get("tenant_id"):
            users = users.filter(default_tenant_id=scope["tenant_id"])
        if scope.get("campus_id"):
            users = users.filter(default_campus_id=scope["campus_id"])
    return users.filter(Exists(live)).order_by("username", "pk")


def academic_scope(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    cached = getattr(request, "_admin_dashboard_academic_scope", None)
    if cached is not None and cached[0] == tenant_id:
        return cached[1:]
    result = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id) if tenant_id else (None, None)
    request._admin_dashboard_academic_scope = (tenant_id, *result)
    return result


def active_term_requests(request, queryset):
    """Use the same tenant-configured AY/term on cards and their destinations.

    Missing configuration must not silently expand an active-term queue to history.
    Existing AdminScopeService selectors retain all authorization/supervision rules.
    """
    year, term = academic_scope(request)
    if not (year and term):
        return queryset.none()
    return queryset.filter(offering__academic_year=year, offering__term=term)


def monitoring_offering_ids(request):
    """Reuse the dashboard's exact supervision selector within this request only.

    Keep a lazy SQL subquery, never a materialized request/offering listing. Scope
    identity guards against reuse if a caller changes selection on this request.
    Other AdminScopeService consumers continue resolving their scope normally.
    """
    scope = getattr(request, "scope", {})
    key = (request.user.pk, scope.get("tenant_id"), scope.get("campus_id"),
           tuple(sorted(scope.get("tenant_ids", []))), tuple(sorted(scope.get("campus_ids", []))),
           tuple(sorted(scope.get("department_ids", []))))
    cached = getattr(request, "_dashboard_monitoring_offering_ids", None)
    if cached is None or cached[0] != key:
        ids = AdminScopeService.scoped_monitoring_course_offerings(request).values_list("id", flat=True)
        cached = request._dashboard_monitoring_offering_ids = (key, ids)
    return cached[1]


def selected_campus_cml_requests(request):
    """Intersect existing CML ownership/department scope with selected scope.

    Entry points enforce their existing permission in this selected tenant/campus
    (view for dashboard/list, review for the review route). Page-level URL filters
    may narrow this queryset but cannot substitute another campus for selection.
    """
    scope = getattr(request, "scope", {})
    tenant_id, campus_id = scope.get("tenant_id"), scope.get("campus_id")
    qs = AdminScopeService.scoped_class_list_change_requests(request)
    if (not tenant_id or not campus_id or tenant_id not in scope.get("tenant_ids", [])
            or campus_id not in scope.get("campus_ids", [])):
        return qs.none()
    return qs.filter(tenant_id=tenant_id, campus_id=campus_id,
                     offering__tenant_id=tenant_id, offering__campus_id=campus_id)


def request_queues(request, *, permissions):
    """Pending applications only, per the approved two-level dashboard design.

    Never materialize requests/items or inspect approved correction windows here.
    Approved status alone is not authoritative for correction access; the existing
    workflow retains its live governance and immutable reviewer/audit validation.
    """
    correction = cml = None
    can_correct = "corrections.read" in permissions
    can_cml = "class_list_change_requests.view" in permissions
    if not (can_correct or can_cml):
        return correction, cml
    year, term = academic_scope(request)
    configured = bool(year and term)
    if can_correct:
        correction = {
            "pending_count": None, "configured": configured,
            "url": reverse("admin_portal:grade_correction_request_list") + "?academic_scope=active&status=PENDING",
        }
        if configured:
            correction["pending_count"] = active_term_requests(
                request, AdminScopeService.scoped_grade_correction_requests(
                    request, monitoring_offering_ids=monitoring_offering_ids(request),
                ),
            ).filter(status=GradeCorrectionRequest.Status.PENDING).values("pk").distinct().count()
    if can_cml:
        cml = {
            "pending_count": None, "configured": configured,
            "url": reverse("admin_portal:class_list_change_request_list") + "?" + urlencode({
                "academic_scope": "active", "status": "PENDING",
                "scope_tenant_id": getattr(request, "scope", {}).get("tenant_id") or "",
                "scope_campus_id": getattr(request, "scope", {}).get("campus_id") or "",
            }),
        }
        if configured:
            cml["pending_count"] = active_term_requests(
                request, selected_campus_cml_requests(request),
            ).filter(status=ClassListChangeRequest.Status.PENDING).values("pk").distinct().count()
    return correction, cml


def scope_identity(request):
    year, term = academic_scope(request)
    scope = getattr(request, "scope", {})
    return [request.user.pk, scope.get("tenant_id"), scope.get("campus_id"),
            sorted(scope.get("department_ids", [])), getattr(year, "pk", None), getattr(term, "pk", None),
            sorted(scope.get("tenant_ids", [])), sorted(scope.get("campus_ids", []))]


def scope_token(request):
    return signing.dumps(scope_identity(request), salt="admin-dashboard-scope", compress=True)


def persisted_poll_scope_matches(request):
    """Catch a selection saved by another tab while this poll was computing."""
    session = Session.objects.filter(
        session_key=request.session.session_key, expire_date__gt=timezone.now(),
    ).only("session_data").first()
    if session is None:
        return False
    saved = session.get_decoded()
    scope = getattr(request, "scope", {})
    tenant_id = saved.get(ScopeService.SESSION_TENANT_KEY)
    campus_id = saved.get(ScopeService.SESSION_CAMPUS_KEY)
    if ScopeService.SESSION_TENANT_KEY not in saved or ScopeService.SESSION_CAMPUS_KEY not in saved:
        # Sessions inherited from an earlier release may not have saved selectors
        # yet. Resolve their authorized defaults just as scope middleware does.
        defaults = ScopeService.build_scope(request.user, tenant_id=tenant_id, campus_id=campus_id)
        tenant_id, campus_id = defaults.get("tenant_id"), defaults.get("campus_id")
    return tenant_id == scope.get("tenant_id") and campus_id == scope.get("campus_id")


@require_GET
@never_cache
def queue_counts_view(request):
    """Count-only polling: fresh authorization and signed current session scope."""
    if {"scope_tenant_id", "scope_campus_id"} & request.GET.keys():
        return JsonResponse({"error": "scope_changed"}, status=409)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "sign_in_expired"}, status=401)
    scope = getattr(request, "scope", {})
    permissions = PermissionService.get_effective_permission_codes(
        request.user, tenant_id=scope.get("tenant_id"), campus_id=scope.get("campus_id"),
    )
    if not {"admin_portal.access", "dashboard.read"}.issubset(permissions):
        return JsonResponse({"error": "permission_changed"}, status=403)
    token = request.GET.get("scope_token", "")
    try:
        identity = signing.loads(token, salt="admin-dashboard-scope", max_age=86400)
    except signing.BadSignature:
        return JsonResponse({"error": "scope_changed"}, status=409)
    if identity != scope_identity(request):
        return JsonResponse({"error": "scope_changed"}, status=409)
    if not {"corrections.read", "class_list_change_requests.view"} & permissions:
        return JsonResponse({"error": "permission_changed"}, status=403)
    correction, cml = request_queues(request, permissions=permissions)
    if not persisted_poll_scope_matches(request):
        return JsonResponse({"error": "scope_changed"}, status=409)
    return JsonResponse({"scope_token": token, "correction": correction, "cml": cml})


def reopen_monitor(request, *, permissions, now=None):
    can_read = "reopen_requests.read" in permissions
    can_review = "reopen_requests.review" in permissions
    if not (can_read or can_review):
        return None
    now = now or timezone.now()
    qs = AdminScopeService.scoped_grade_submission_reopen_requests(
        request, monitoring_offering_ids=monitoring_offering_ids(request),
    )
    year, term = academic_scope(request)
    if year and term:
        qs = qs.filter(offering__academic_year=year, offering__term=term)
    # Evaluate per-campus explicit reviewer eligibility at most once per card (five rows).
    pending = list(qs.filter(status=GradeSubmissionReopenRequest.Status.PENDING).order_by("-created_at", "-id")[:5])
    eligibility = {}
    for row in pending:
        key = (row.tenant_id, row.campus_id)
        if key not in eligibility:
            eligibility[key] = can_review and GradingGovernanceService.is_assigned_reopen_reviewer(
                user=request.user, tenant_id=row.tenant_id, campus_id=row.campus_id,
            )
        row.dashboard_can_review = eligibility[key]
    local_day = timezone.localdate(now)
    start = timezone.make_aware(datetime.combine(local_day, time.min))
    end = timezone.make_aware(datetime.combine(local_day + timedelta(days=1), time.min))
    counts = qs.aggregate(
        pending_count=Count("pk", filter=Q(status=GradeSubmissionReopenRequest.Status.PENDING)),
        reviewed_today_count=Count("pk", filter=Q(
            status__in=[GradeSubmissionReopenRequest.Status.APPROVED, GradeSubmissionReopenRequest.Status.REJECTED],
            reviewed_at__gte=start, reviewed_at__lt=end,
        )),
    )
    return {**counts, "latest_pending": pending, "can_read": can_read, "checked_at": now}


@require_GET
@never_cache
@portal_required("ADMIN")
@permission_required("users.read")
def logged_in_users_view(request):
    now = timezone.now()
    users = logged_in_users(request, now=now)
    session_expiry = Session.objects.filter(session_key=OuterRef("session_key"), expire_date__gt=now).values("expire_date")[:1]
    live_expiry = ActivePortalSession.objects.filter(user_id=OuterRef("pk")).annotate(
        expires=Subquery(session_expiry),
    ).filter(expires__isnull=False).order_by("-expires").values("expires")[:1]
    # Details and expiry are fetched only for this paginated listing.
    page = Paginator(users.annotate(session_expires_at=Subquery(live_expiry)).select_related(
        "default_tenant", "default_campus", "default_department",
    ), 50).get_page(request.GET.get("page"))
    from .views import _scope_context
    return render(request, "admin_portal/logged_in_users.html", {
        **_scope_context(request), "page_obj": page, "checked_at": now,
    })


@require_GET
@never_cache
@portal_required("ADMIN")
@permission_required("dashboard.read")
def reopen_card_view(request):
    if {"scope_tenant_id", "scope_campus_id"} & request.GET.keys():
        return JsonResponse({"error": "scope_changed"}, status=409)
    scope = getattr(request, "scope", {})
    permissions = PermissionService.get_effective_permission_codes(
        request.user, tenant_id=scope.get("tenant_id"), campus_id=scope.get("campus_id"),
    )
    if not ({"reopen_requests.read", "reopen_requests.review"} & permissions):
        return JsonResponse({"error": "permission_changed"}, status=403)
    try:
        identity = signing.loads(request.GET.get("scope_token", ""), salt="admin-dashboard-scope", max_age=86400)
    except signing.BadSignature:
        return JsonResponse({"error": "scope_changed"}, status=409)
    if identity != scope_identity(request):
        return JsonResponse({"error": "scope_changed"}, status=409)
    monitor = reopen_monitor(request, permissions=permissions)
    if not persisted_poll_scope_matches(request):
        return JsonResponse({"error": "scope_changed"}, status=409)
    return render(request, "admin_portal/_dashboard_reopen_card.html", {"reopen_request_monitor": monitor})
