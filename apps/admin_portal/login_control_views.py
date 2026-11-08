from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.accounts.login_control import LoginAccessService, StaleLoginControl
from apps.accounts.models import User
from apps.core.decorators import permission_required, portal_required
from apps.admin_portal.correction_email_views import _enforce_direct_denies


class LoginControlForm(forms.Form):
    operation = forms.ChoiceField(choices=[("enable", "Enable Login"), ("disable", "Disable Login")])
    version = forms.IntegerField(min_value=0)
    confirmed = forms.BooleanField(required=True)


class ClearanceLoginPolicyForm(forms.Form):
    enabled = forms.BooleanField(required=False, label="Automatic Faculty Login Disabling")
    delay_days = forms.IntegerField(
        min_value=1, initial=LoginAccessService.DEFAULT_DELAY_DAYS, label="Delay in days",
        help_text="Whole days (minimum 1, default 2). Each day is 24 elapsed hours. Changes apply to new clearance events only.",
        widget=forms.NumberInput(attrs={"min": 1, "step": 1}),
    )

    def clean_delay_days(self):
        return LoginAccessService.validate_delay_days(self.cleaned_data["delay_days"])


@require_POST
@portal_required("ADMIN")
def user_login_control_view(request, user_id):
    _enforce_direct_denies(request, "admin_portal.access", LoginAccessService.PERMISSION)
    form = LoginControlForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Confirm the operation and reload Users if the form is outdated.", status=400)
    get_object_or_404(User, pk=user_id)
    scope = getattr(request, "scope", {})
    try:
        LoginAccessService.set_manual(
            actor=request.user, target_id=user_id, disabled=form.cleaned_data["operation"] == "disable",
            expected_version=form.cleaned_data["version"], tenant_id=scope.get("tenant_id"), campus_id=scope.get("campus_id"), request=request,
        )
    except StaleLoginControl as exc:
        return HttpResponse(exc.messages[0], status=409)
    messages.success(request, "Login access updated. Existing sessions were revoked; historical records are preserved.")
    return redirect("admin_portal:user_list")


@require_POST
@portal_required("ADMIN")
@permission_required("system_settings.update")
def clearance_login_policy_view(request):
    _enforce_direct_denies(request, "admin_portal.access", "system_settings.update")
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id")
    if not tenant_id or tenant_id not in scope.get("tenant_ids", []):
        raise PermissionDenied("Select an authorized tenant scope.")
    form = ClearanceLoginPolicyForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Invalid clearance login policy.", status=400)
    LoginAccessService.save_automatic_policy(
        tenant_id=tenant_id, enabled=form.cleaned_data["enabled"], delay_days=form.cleaned_data["delay_days"],
        actor=request.user, campus_id=scope.get("campus_id"), request=request,
    )
    messages.success(request, "Clearance login policy saved. Existing login restrictions remain unchanged.")
    return redirect("admin_portal:configurable_features_settings")
