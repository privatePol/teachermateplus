from django import forms

from .models import Question


class BootstrapFormMixin:
    """Apply the portal's Bootstrap widget and bound-error conventions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_dynamic_fields()
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

    def configure_dynamic_fields(self):
        """Hook for request-scoped fields that must exist before bound validation."""


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

    def __init__(
        self,
        *args,
        sections=None,
        require_section=False,
        fixed_section=None,
        scenario_id=None,
        **kwargs,
    ):
        self._case_sections = sections
        self._case_require_section = require_section
        self._case_fixed_section = fixed_section
        self._case_scenario_id = scenario_id
        super().__init__(*args, **kwargs)

    def configure_dynamic_fields(self):
        if self._case_sections is not None:
            choices = [("", "Select Exam Section"), *(
                (str(section.id), section.title) for section in self._case_sections
            )]
            self.fields["section_id"] = forms.ChoiceField(
                choices=choices,
                required=self._case_require_section,
                label="Exam Section",
                widget=(forms.HiddenInput() if self._case_fixed_section else forms.Select()),
            )
            if self._case_fixed_section:
                self.initial["section_id"] = str(self._case_fixed_section.id)
            elif "section_id" not in self.initial:
                self.initial["section_id"] = ""
            if not self.fields["section_id"].widget.is_hidden:
                self.fields["section_id"].widget.attrs["class"] = "form-select"
        if self._case_scenario_id is not None:
            self.fields["scenario_id"] = forms.IntegerField(
                min_value=1,
                widget=forms.HiddenInput,
                initial=self._case_scenario_id,
            )


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


class QuestionDOCXUploadForm(ContributionRevisionForm):
    docx_file = forms.FileField(
        help_text="Word .docx only, maximum 2 MB and 200 detected questions."
    )


class QuestionDOCXRowForm(QuestionForm):
    expected_question_revision = None
    difficulty = forms.ChoiceField(
        choices=(("", "Select difficulty"), *Question.Difficulty.choices),
        required=True,
    )


class ContributionSubmitForm(ContributionRevisionForm):
    confirm_exact_quota = forms.BooleanField(
        label="I confirm that this contribution is final and will become read-only."
    )


class FacultyCaseForm(ContributionRevisionForm):
    expected_scenario_revision = forms.IntegerField(
        min_value=0, required=False, widget=forms.HiddenInput
    )
    title = forms.CharField(max_length=200, required=False)
    stimulus = forms.CharField(
        max_length=100000,
        widget=forms.HiddenInput(attrs={"data-case-source": True}),
        label="Case / Scenario content",
    )

    def __init__(self, *args, sections=(), require_section=False, **kwargs):
        self._case_sections = sections
        self._case_require_section = require_section
        super().__init__(*args, **kwargs)

    def configure_dynamic_fields(self):
        if self._case_require_section:
            self.fields["section_id"] = forms.ChoiceField(
                label="Exam Section",
                choices=[("", "Select Exam Section"), *(
                    (str(section.id), section.title) for section in self._case_sections
                )],
                required=True,
                widget=forms.Select(attrs={"class": "form-select"}),
            )


class FacultyCaseDeleteForm(ContributionRevisionForm):
    expected_scenario_revision = forms.IntegerField(
        min_value=1, widget=forms.HiddenInput
    )


class FacultyCaseMemberReorderForm(FacultyCaseDeleteForm):
    ordered_question_ids = forms.CharField(widget=forms.HiddenInput)

    def clean_ordered_question_ids(self):
        raw = self.cleaned_data["ordered_question_ids"]
        try:
            return [int(value) for value in raw.split(",") if value.strip()]
        except (TypeError, ValueError) as exc:
            raise forms.ValidationError("Linked Question order is malformed.") from exc


class RosterActionForm(BootstrapFormMixin, forms.Form):
    confirm = forms.BooleanField(
        label="I confirm this exact-scoped contributor roster action."
    )
