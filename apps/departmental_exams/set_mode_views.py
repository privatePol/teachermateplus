"""Dedicated cycle-level generator configuration, under existing cycle authority."""
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from apps.core.services.audit import AuditService
from .models import CourseExamConfiguration, ExaminationCycle, ExamGeneratorPolicy
from .services import DepartmentalExamAuthorizationService
from .views import _tenant_id, portal_required


class ExamGeneratorPolicyForm(forms.ModelForm):
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    class Meta:
        model = ExamGeneratorPolicy
        fields = ["departmental", "standardized", "mode", "shuffle_choices"]
        widgets = {
            "departmental": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "standardized": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "mode": forms.Select(attrs={"class": "form-select"}),
            "shuffle_choices": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {"departmental": "Apply to Departmental exams", "standardized": "Apply to Standardized exams",
                  "mode": "Set mode", "shuffle_choices": "Shuffle answer choices"}


def require_mutable(cycle):
    if (cycle.status == "CLOSED" or cycle.cycle_courses.filter(generation_revisions__isnull=False).exists()
            or CourseExamConfiguration.objects.filter(cycle_course__cycle=cycle, opened_at__isnull=False).exists()):
        raise ValidationError("Generator policy freezes at first contribution Open. Existing exams and historical output are preserved.")


def generator_policy_context(cycle, *, data=None):
    instance = ExamGeneratorPolicy.objects.filter(cycle=cycle).first()
    form = ExamGeneratorPolicyForm(data, instance=instance,
        initial={"expected_revision": instance.revision if instance else 0})
    locked = False
    try:
        require_mutable(cycle)
    except ValidationError:
        locked = True
    return {"generator_form": form, "generator_locked": locked}


def save_generator_policy(request, cycle, form):
    """Shared save path for the embedded card and compatible direct URL."""
    status = 200
    if form.is_valid():
        try:
            with transaction.atomic():
                cycle = ExaminationCycle.objects.select_for_update().get(pk=cycle.pk, tenant_id=cycle.tenant_id)
                DepartmentalExamAuthorizationService.require_permission(user=request.user,
                    permission="departmental_exams.manage_cycles", tenant_id=cycle.tenant_id)
                require_mutable(cycle)
                current = ExamGeneratorPolicy.objects.select_for_update().filter(cycle=cycle).first()
                if form.cleaned_data["expected_revision"] != (current.revision if current else 0):
                    raise ValidationError("Generator policy changed. Reload before saving.")
                policy = form.save(commit=False)
                policy.cycle, policy.revision = cycle, (current.revision + 1 if current else 1)
                policy.save()
                AuditService.log_event(action="DE_EXAM_SET_POLICY_CONFIGURED", portal="ADMIN",
                    actor=request.user, tenant=cycle.tenant_id, entity_type="ExaminationCycle", entity_id=cycle.id,
                    metadata={"cycle_id": cycle.id, "policy_revision": policy.revision,
                              "departmental": policy.departmental, "standardized": policy.standardized,
                              "set_mode": policy.mode, "shuffle_choices": policy.shuffle_choices}, request=request)
            return True, 200
        except ValidationError as exc:
            form.add_error(None, exc)
            status = 409
    return False, status


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def generator_policy_view(request, cycle_id):
    cycle = get_object_or_404(ExaminationCycle, pk=cycle_id, tenant_id=_tenant_id(request))
    DepartmentalExamAuthorizationService.require_permission(user=request.user,
        permission="departmental_exams.manage_cycles", tenant_id=cycle.tenant_id)
    context = generator_policy_context(cycle, data=request.POST if request.method == "POST" else None)
    form, locked = context["generator_form"], context["generator_locked"]
    status = 200
    if request.method == "POST":
        saved, status = save_generator_policy(request, cycle, form)
        if saved:
            return redirect("departmental_exams:generator_policy", cycle_id=cycle.id)
    return render(request, "departmental_exams/admin/generator_policy.html",
                  {"cycle": cycle, "form": form, "locked": locked}, status=status)
