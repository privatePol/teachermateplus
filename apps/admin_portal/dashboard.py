"""Read-only Admin dashboard selectors and fragment endpoints."""
from datetime import datetime, time, timedelta

from django.contrib.sessions.models import Session
from django.core import signing
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache

from apps.accounts.models import ActivePortalSession, User
from apps.academics.services import AcademicGovernanceService
from apps.core.decorators import portal_required, permission_required
from apps.core.services.permissions import PermissionService
from apps.grading.models import GradeSubmissionReopenRequest
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


def scope_identity(request):
    year, term = academic_scope(request)
    scope = getattr(request, "scope", {})
    return [request.user.pk, scope.get("tenant_id"), scope.get("campus_id"),
            sorted(scope.get("department_ids", [])), getattr(year, "pk", None), getattr(term, "pk", None)]


def scope_token(request):
    return signing.dumps(scope_identity(request), salt="admin-dashboard-scope", compress=True)


def reopen_monitor(request, *, permissions, now=None):
    can_read = "reopen_requests.read" in permissions
    can_review = "reopen_requests.review" in permissions
    if not (can_read or can_review):
        return None
    now = now or timezone.now()
    qs = AdminScopeService.scoped_grade_submission_reopen_requests(request)
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
    return render(request, "admin_portal/_dashboard_reopen_card.html", {"reopen_request_monitor": monitor})
