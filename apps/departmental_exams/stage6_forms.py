from __future__ import annotations

import re

from django import forms
from django.forms import formset_factory

from .models import ExamBlueprint, ExamSection


class BlockedContributionResolutionForm(forms.Form):
    expected_contribution_revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_roster_revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    reason = forms.CharField(
        min_length=10,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
    )


class BlueprintForm(forms.Form):
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    mode = forms.ChoiceField(
        choices=ExamBlueprint.Mode.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )


class ExamSectionForm(forms.Form):
    id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    title = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"class": "form-control"}))
    instructions = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
    )
    display_order = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={"class": "form-control"}))
    item_quota = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={"class": "form-control"}))


ExamSectionFormSet = formset_factory(ExamSectionForm, extra=1, can_delete=True)


class QuestionPlacementForm(forms.Form):
    expected_placement_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    section = forms.ModelChoiceField(
        queryset=ExamSection.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, blueprint=None, **kwargs):
        super().__init__(*args, **kwargs)
        if blueprint is not None:
            self.fields["section"].queryset = blueprint.sections.order_by("display_order", "id")


class ScenarioForm(forms.Form):
    scenario_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    title = forms.CharField(required=False, max_length=200, widget=forms.TextInput(attrs={"class": "form-control"}))
    stimulus = forms.CharField(max_length=5000, widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"}))
    section = forms.ModelChoiceField(
        required=False,
        queryset=ExamSection.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    ordered_question_ids = forms.CharField(
        help_text="Enter at least two question numbers in the required contiguous order, separated by commas.",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "12, 15, 18"}),
    )

    def __init__(self, *args, blueprint=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.blueprint = blueprint
        if blueprint is not None:
            self.fields["section"].queryset = blueprint.sections.order_by("display_order", "id")
            self.fields["section"].required = blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS

    def clean_ordered_question_ids(self):
        raw = self.cleaned_data["ordered_question_ids"]
        tokens = [token for token in re.split(r"[\s,]+", raw.strip()) if token]
        try:
            question_ids = [int(token) for token in tokens]
        except ValueError as exc:
            raise forms.ValidationError("Question numbers must be positive integers.") from exc
        if len(question_ids) < 2 or any(value <= 0 for value in question_ids):
            raise forms.ValidationError("Enter at least two positive question numbers.")
        if len(question_ids) != len(set(question_ids)):
            raise forms.ValidationError("Each scenario question may appear only once.")
        return question_ids


class ScenarioDeleteForm(forms.Form):
    expected_revision = forms.IntegerField(min_value=1)
