from django import forms

from .models import CourseExamConfiguration, CycleCourse, ExaminationCycle
from apps.tenants.models import Department
from apps.accounts.models import User


class CycleCourseAdministrationForm(forms.Form):
    responsible_department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)
    reviewer = forms.ModelChoiceField(queryset=User.objects.none(), required=False)

    def __init__(
        self,
        *args,
        cycle_course=None,
        department_queryset=None,
        reviewer_queryset=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if cycle_course:
            self.fields["responsible_department"].queryset = (
                department_queryset
                if department_queryset is not None
                else Department.objects.none()
            )
            self.fields["responsible_department"].initial = cycle_course.responsible_department_id
            self.fields["reviewer"].queryset = (
                reviewer_queryset if reviewer_queryset is not None else User.objects.none()
            )
            self.fields["reviewer"].initial = cycle_course.reviewer_id


class CycleCourseExemptionForm(forms.Form):
    exemption_category = forms.ChoiceField(
        choices=CycleCourse.ExemptionCategory.choices,
        label="Exemption category",
    )
    reason = forms.CharField(
        min_length=10,
        max_length=500,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Enter a specific reason from 10 to 500 characters.",
    )
    expected_updated_at = forms.CharField(widget=forms.HiddenInput())


class CycleCourseRestoreForm(forms.Form):
    reason = forms.CharField(
        min_length=10,
        max_length=500,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Restoration reason",
        help_text="Enter a specific reason from 10 to 500 characters.",
    )
    expected_updated_at = forms.CharField(widget=forms.HiddenInput())


class ExaminationCycleForm(forms.ModelForm):
    class Meta:
        model = ExaminationCycle
        fields = ["academic_year", "term", "exam_period"]

    def clean(self):
        cleaned = super().clean()
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        if academic_year and term and (term.tenant_id != academic_year.tenant_id or term.academic_year_id != academic_year.id):
            self.add_error("term", "Choose a term belonging to the selected academic year and tenant.")
        return cleaned


class ExaminationCycleConfigurationForm(forms.ModelForm):
    expected_updated_at = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 3}), help_text="Required (10-500 characters) when changing defaults on an Open cycle.")

    class Meta:
        model = ExaminationCycle
        fields = ["default_questions_required_per_faculty", "default_final_item_count", "contributor_instructions"]
        widgets = {"contributor_instructions": forms.Textarea(attrs={"rows": 4})}

    def clean_contributor_instructions(self):
        return (self.cleaned_data.get("contributor_instructions") or "").strip()

    def clean_reason(self):
        return (self.cleaned_data.get("reason") or "").strip()

    def clean(self):
        cleaned = super().clean()
        for field in ("default_questions_required_per_faculty", "default_final_item_count"):
            value = cleaned.get(field)
            if value is not None and not 50 <= value <= 75:
                self.add_error(field, "Value must be from 50 to 75.")
        return cleaned


class CycleDefaultsConfirmationForm(forms.Form):
    # This is the only field accepted by the confirmation POST. It carries a
    # signed, actor-bound snapshot of every authoritative value; ordinary
    # hidden inputs must never be trusted for a confirmed cycle update.
    confirmation_state = forms.CharField(widget=forms.HiddenInput)


class _CycleTransitionForm(forms.Form):
    expected_updated_at = forms.CharField(widget=forms.HiddenInput)


class ExaminationCycleOpenForm(_CycleTransitionForm):
    pass


class ExaminationCycleCloseForm(_CycleTransitionForm):
    pass


class CourseExamConfigurationForm(forms.ModelForm):
    expected_revision = forms.IntegerField(widget=forms.HiddenInput)
    questions_required_per_faculty_mode = forms.ChoiceField(choices=[("DEFAULT", "Use cycle default"), ("OVERRIDE", "Use course override")])
    final_item_count_mode = forms.ChoiceField(choices=[("DEFAULT", "Use cycle default"), ("OVERRIDE", "Use course override")])

    def __init__(self, *args, cycle=None, **kwargs):
        self.cycle = cycle
        super().__init__(*args, **kwargs)

    class Meta:
        model = CourseExamConfiguration
        fields = [
            "final_item_count", "questions_required_per_faculty", "coverage",
            "additional_instructions", "contribution_deadline", "final_item_count_source",
            "questions_required_per_faculty_source", "cycle_defaults_revision_snapshot",
        ]
        widgets = {
            "coverage": forms.Textarea(attrs={"rows": 3}),
            "additional_instructions": forms.Textarea(attrs={"rows": 3}),
            "contribution_deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "final_item_count_source": forms.HiddenInput(),
            "questions_required_per_faculty_source": forms.HiddenInput(),
            "cycle_defaults_revision_snapshot": forms.HiddenInput(),
        }

    def clean_coverage(self):
        return (self.cleaned_data.get("coverage") or "").strip()

    def clean_additional_instructions(self):
        return (self.cleaned_data.get("additional_instructions") or "").strip()

    def clean(self):
        cleaned = super().clean()
        for field in ("final_item_count", "questions_required_per_faculty"):
            value = cleaned.get(field)
            mode = cleaned.get(f"{field}_mode")
            if mode == "OVERRIDE" and (value is None or not 50 <= value <= 75):
                self.add_error(field, "A course override must be from 50 to 75.")
            if mode == "DEFAULT" and value is not None and not 50 <= value <= 75:
                self.add_error(field, "Value must be from 50 to 75.")
        if self.cycle:
            pairs = (
                ("questions_required_per_faculty", "questions_required_per_faculty_mode", "questions_required_per_faculty_source", self.cycle.default_questions_required_per_faculty),
                ("final_item_count", "final_item_count_mode", "final_item_count_source", self.cycle.default_final_item_count),
            )
            for field, mode_field, source_field, default in pairs:
                if cleaned.get(mode_field) == "DEFAULT":
                    if default is None or not 50 <= default <= 75:
                        self.add_error(field, "A valid cycle default is required.")
                    else:
                        cleaned[field] = default
                        cleaned[source_field] = "DEFAULT"
                elif cleaned.get(mode_field) == "OVERRIDE":
                    cleaned[source_field] = "OVERRIDE"
            cleaned["cycle_defaults_revision_snapshot"] = self.cycle.defaults_revision
        return cleaned


class _ConfigurationActionForm(forms.Form):
    expected_revision = forms.IntegerField(widget=forms.HiddenInput)


class CourseOverrideRemovalForm(_ConfigurationActionForm):
    return_questions_required_per_faculty = forms.BooleanField(required=False)
    return_final_item_count = forms.BooleanField(required=False)

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("return_questions_required_per_faculty") or cleaned.get("return_final_item_count")):
            raise forms.ValidationError("Select at least one override to return to the cycle default.")
        return cleaned


class CourseContributionOpenForm(_ConfigurationActionForm):
    pass


class CourseContributionReopenForm(_ConfigurationActionForm):
    pass


class _ReasonedConfigurationActionForm(_ConfigurationActionForm):
    reason = forms.CharField(min_length=10, max_length=500, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_reason(self):
        return (self.cleaned_data["reason"] or "").strip()


class CourseContributionCloseForm(_ReasonedConfigurationActionForm):
    pass


class CourseExamConfigurationRevertForm(_ReasonedConfigurationActionForm):
    pass
