from django import forms
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import render

from apps.admin_portal.services import AdminScopeService
from apps.core.decorators import portal_required
from .coverage_initialization import CoverageInitializationService
from .permissions import MANAGE_COVERAGE_PERMISSION, RECONCILE_PERMISSION
from .views import _scope, _page_permission


class CoverageInitializationForm(forms.Form):
    academic_year = forms.ModelChoiceField(queryset=None)
    term = forms.ModelChoiceField(queryset=None, label="Semester")
    effective_from = forms.DateTimeField(label="Confirmed teaching coverage starts", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
    recover = forms.BooleanField(required=False, label="Adopt this verified coverage for already-prepared unresolved classes with no saved attendance")
    reason = forms.CharField(required=False, label="Checker note (optional)", widget=forms.Textarea(attrs={"rows": 2}))
    confirmed = forms.BooleanField(required=False, label="I confirm the effective date/time and the previewed assignments")

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_year"].queryset = AdminScopeService.active_scoped_academic_years(request)
        self.fields["term"].queryset = AdminScopeService.active_scoped_terms(request)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control"


@portal_required("ADMIN")
def coverage_initialization_view(request):
    tenant_id, campus_id, department_ids = _scope(request)
    for permission in (MANAGE_COVERAGE_PERMISSION, RECONCILE_PERMISSION):
        _page_permission(request, permission, department_ids)
    form = CoverageInitializationForm(request.POST or request.GET or None, request=request)
    plan, applied, token = None, None, ""
    if form.is_bound and form.is_valid():
        values = form.cleaned_data
        scope = dict(actor=request.user, tenant_id=tenant_id, campus_id=campus_id,
                     department_ids=department_ids, academic_year=values["academic_year"],
                     term=values["term"], effective_from=values["effective_from"])
        identity = dict(actor=request.user.pk, tenant=tenant_id, campus=campus_id,
                        year=values["academic_year"].pk, term=values["term"].pk,
                        start=values["effective_from"].isoformat(), recover=values["recover"])
        try:
            if request.method == "POST" and request.POST.get("action") == "apply":
                reviewed = signing.loads(request.POST.get("preview_token", ""), salt="attendance-coverage-preview", max_age=900)
                if reviewed["identity"] != identity:
                    raise ValidationError("Scope or effective boundary changed; preview again before applying.")
                applied = CoverageInitializationService.apply(**scope, fingerprint=reviewed["fingerprint"],
                    recover=values["recover"], confirmed=values["confirmed"], reason=values["reason"])
            plan = CoverageInitializationService.preview(**scope)
            token = signing.dumps({"identity": identity, "fingerprint": plan["fingerprint"]}, salt="attendance-coverage-preview")
        except PermissionDenied:
            return HttpResponseForbidden("Coverage initialization is outside your authorized scope.")
        except (ValidationError, signing.BadSignature, KeyError) as exc:
            form.add_error(None, "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Preview expired or invalid; preview again.")
    return render(request, "faculty_attendance/coverage_initialization.html", {
        "form": form, "plan": plan, "applied": applied, "preview_token": token})
