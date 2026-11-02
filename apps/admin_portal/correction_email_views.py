from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.admin_portal.services import AdminScopeService
from apps.core.services.audit import AuditService
from apps.core.decorators import permission_required, portal_required
from apps.grading.correction_reminders import CorrectionReminderPolicy, CorrectionReminderSettingsForm
from apps.grading.models import CorrectionEmailDelivery
from apps.rbac.models import UserPermission


def _enforce_direct_denies(request, *codes):
    scope = getattr(request, "scope", {})
    denied = UserPermission.objects.filter(user=request.user, permission__code__in=codes,
        permission__is_active=True, grant_type=UserPermission.GrantType.DENY).filter(
        Q(tenant_id=scope.get("tenant_id")) | Q(tenant__isnull=True)).filter(
        Q(campus_id=scope.get("campus_id")) | Q(campus__isnull=True)).exists()
    if denied:
        raise PermissionDenied("A direct permission denial applies to this action.")


@portal_required("ADMIN")
@permission_required("system_settings.update")
@require_http_methods(["GET", "POST"])
def correction_email_settings_view(request):
    _enforce_direct_denies(request, "admin_portal.access", "system_settings.update")
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    if not tenant_id:
        raise PermissionDenied("Select an authorized tenant first.")
    initial = CorrectionReminderPolicy.get(tenant_id)
    form = CorrectionReminderSettingsForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        from django.db import transaction
        with transaction.atomic():
            CorrectionReminderPolicy.save(tenant_id, form.cleaned_data)
            AuditService.log_event(action="UPDATE", portal="ADMIN", entity_type="SystemSetting",
                entity_id=CorrectionReminderPolicy.KEY, actor=request.user, tenant=tenant_id,
                after_data={"enabled": form.cleaned_data["enabled"],
                    "interval_days": form.cleaned_data["interval_days"],
                    "send_time": form.cleaned_data["send_time"].strftime("%H:%M"), "timezone": "Asia/Manila"},
                request=request)
        messages.success(request, "Grade Correction reminder settings saved.")
        return redirect("admin_portal:correction_email_settings")
    return render(request, "admin_portal/tools/correction_email_settings.html", {
        "title": "Grade Correction Email Reminders", "form": form})


@portal_required("ADMIN")
@permission_required("corrections.review")
@require_GET
def correction_email_history_view(request, request_id):
    _enforce_direct_denies(request, "admin_portal.access", "corrections.review")
    scope = getattr(request, "scope", {})
    queryset = AdminScopeService.scoped_grade_correction_requests(request).filter(tenant_id=scope.get("tenant_id"))
    if scope.get("campus_id"):
        queryset = queryset.filter(campus_id=scope["campus_id"])
    correction = get_object_or_404(queryset, pk=request_id)
    rows = CorrectionEmailDelivery.objects.filter(correction_request=correction,
        tenant_id=correction.tenant_id, campus_id=correction.campus_id).select_related("recipient", "step").order_by("-scheduled_at", "-pk")
    return render(request, "admin_portal/grading/correction_email_history.html", {
        "title": f"Correction CGR-{correction.pk:06d} Email History", "correction_request": correction,
        "page_obj": Paginator(rows, 50).get_page(request.GET.get("page"))})
