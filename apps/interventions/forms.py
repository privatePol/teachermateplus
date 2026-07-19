from django import forms
from django.utils import timezone

from .models import AcademicInterventionAction, AcademicInterventionCase, AcademicInterventionFollowUp


class ManualInterventionCaseForm(forms.Form):
    offering_id = forms.IntegerField(widget=forms.HiddenInput)
    student = forms.ModelChoiceField(queryset=None)
    grading_period_id = forms.ChoiceField(choices=())
    distinct_concern_summary = forms.CharField(max_length=500, min_length=10, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, student_queryset=None, period_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = student_queryset
        self.fields["grading_period_id"].choices = [
            (str(period.id), period.name) for period in (period_queryset or [])
        ]


class FacultyDecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=AcademicInterventionCase.Decision.choices)
    rationale = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=2000)
    referral_destination = forms.ChoiceField(
        required=False,
        choices=[("", "Select an approved office")] + list(AcademicInterventionCase.ReferralDestination.choices),
    )
    referral_destination_label = forms.CharField(required=False, max_length=120)
    referral_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    referral_reason = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2}))
    supersede = forms.BooleanField(required=False)
    correction_reason = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") != AcademicInterventionCase.Decision.CONDUCT and not (cleaned.get("rationale") or "").strip():
            self.add_error("rationale", "Enter a brief faculty rationale for this decision.")
        if cleaned.get("decision") == AcademicInterventionCase.Decision.REFERRED:
            for field in ("referral_destination", "referral_date", "referral_reason"):
                if not cleaned.get(field):
                    self.add_error(field, "Required when referring a student.")
            if (
                cleaned.get("referral_destination") == AcademicInterventionCase.ReferralDestination.OTHER_APPROVED
                and not (cleaned.get("referral_destination_label") or "").strip()
            ):
                self.add_error("referral_destination_label", "Name the approved referral office.")
        if cleaned.get("supersede") and not (cleaned.get("correction_reason") or "").strip():
            self.add_error("correction_reason", "Explain why the prior faculty decision is being superseded.")
        return cleaned


class InterventionActionForm(forms.ModelForm):
    class Meta:
        model = AcademicInterventionAction
        fields = ["intervention_type", "status", "planned_for", "conducted_on", "action_summary", "student_action_plan", "cancellation_reason"]
        widgets = {"planned_for": forms.DateInput(attrs={"type": "date"}), "conducted_on": forms.DateInput(attrs={"type": "date"}), "action_summary": forms.Textarea(attrs={"rows": 3}), "student_action_plan": forms.Textarea(attrs={"rows": 3}), "cancellation_reason": forms.Textarea(attrs={"rows": 2})}

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        if status == AcademicInterventionAction.Status.PLANNED and not cleaned.get("planned_for"):
            self.add_error("planned_for", "Enter the planned intervention date.")
        if status == AcademicInterventionAction.Status.CONDUCTED and not cleaned.get("conducted_on"):
            self.add_error("conducted_on", "Enter the intervention date.")
        if status == AcademicInterventionAction.Status.CANCELLED and not (cleaned.get("cancellation_reason") or "").strip():
            self.add_error("cancellation_reason", "Enter a cancellation reason.")
        return cleaned


class FollowUpForm(forms.ModelForm):
    class Meta:
        model = AcademicInterventionFollowUp
        fields = ["due_on", "status", "result_summary", "completed_on"]
        widgets = {"due_on": forms.DateInput(attrs={"type": "date"}), "completed_on": forms.DateInput(attrs={"type": "date"}), "result_summary": forms.Textarea(attrs={"rows": 3})}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == AcademicInterventionFollowUp.Status.COMPLETED and not cleaned.get("completed_on"):
            cleaned["completed_on"] = timezone.localdate()
        elif cleaned.get("status") != AcademicInterventionFollowUp.Status.COMPLETED:
            cleaned["completed_on"] = None
        return cleaned
