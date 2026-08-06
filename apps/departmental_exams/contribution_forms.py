from django import forms

from .models import Question


class BootstrapFormMixin:
    """Apply the portal's Bootstrap widget and bound-error conventions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        invalid_fields = set(self.errors) if self.is_bound else set()
        for name, field in self.fields.items():
            widget = field.widget
            if widget.is_hidden:
                continue
            if isinstance(widget, forms.CheckboxInput):
                bootstrap_class = "form-check-input"
            elif isinstance(widget, forms.Select):
                bootstrap_class = "form-select"
            else:
                bootstrap_class = "form-control"
            current_classes = widget.attrs.get("class", "").split()
            if bootstrap_class not in current_classes:
                current_classes.append(bootstrap_class)
            if name in invalid_fields:
                current_classes.append("is-invalid")
                widget.attrs["aria-invalid"] = "true"
            widget.attrs["class"] = " ".join(current_classes)


class ContributionRevisionForm(BootstrapFormMixin, forms.Form):
    expected_contribution_revision = forms.IntegerField(
        min_value=1, widget=forms.HiddenInput
    )


class QuestionForm(ContributionRevisionForm):
    expected_question_revision = forms.IntegerField(
        min_value=1, required=False, widget=forms.HiddenInput
    )
    question_text = forms.CharField(
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Plain text only; meaningful line breaks are preserved.",
    )
    choice_a = forms.CharField(max_length=1000)
    choice_b = forms.CharField(max_length=1000)
    choice_c = forms.CharField(max_length=1000)
    choice_d = forms.CharField(max_length=1000)
    correct_answer = forms.ChoiceField(choices=((value, value) for value in "ABCD"))
    difficulty = forms.ChoiceField(choices=Question.Difficulty.choices)


class QuestionDeleteForm(ContributionRevisionForm):
    expected_question_revision = forms.IntegerField(
        min_value=1, widget=forms.HiddenInput
    )


class QuestionReorderForm(ContributionRevisionForm):
    ordered_question_ids = forms.CharField(widget=forms.HiddenInput)

    def clean_ordered_question_ids(self):
        raw = self.cleaned_data["ordered_question_ids"]
        try:
            return [int(value) for value in raw.split(",") if value.strip()]
        except (TypeError, ValueError):
            raise forms.ValidationError("Question order is malformed.")


class QuestionCSVUploadForm(ContributionRevisionForm):
    csv_file = forms.FileField(
        help_text="UTF-8 CSV only, maximum 2 MB and 200 nonblank data rows."
    )


class QuestionCSVConfirmForm(BootstrapFormMixin, forms.Form):
    file_sha256 = forms.CharField(max_length=64, widget=forms.HiddenInput)


class ContributionSubmitForm(ContributionRevisionForm):
    confirm_exact_quota = forms.BooleanField(
        label="I confirm that this contribution is final and will become read-only."
    )


class RosterActionForm(BootstrapFormMixin, forms.Form):
    confirm = forms.BooleanField(
        label="I confirm this exact-scoped contributor roster action."
    )
