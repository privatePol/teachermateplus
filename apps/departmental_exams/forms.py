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

    class Meta:
        model = ExaminationCycle
        fields = ["item_count_mode", "fixed_final_item_count", "contributor_instructions"]
        widgets = {"contributor_instructions": forms.Textarea(attrs={"rows": 4})}

    def clean_contributor_instructions(self):
        return (self.cleaned_data.get("contributor_instructions") or "").strip()

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("item_count_mode")
        fixed = cleaned.get("fixed_final_item_count")
        if mode == ExaminationCycle.ItemCountMode.FIXED_ALL and fixed is None:
            self.add_error("fixed_final_item_count", "Fixed final item count is required in Fixed mode.")
        if mode == ExaminationCycle.ItemCountMode.PER_COURSE:
            cleaned["fixed_final_item_count"] = None
        return cleaned


class _CycleTransitionForm(forms.Form):
    expected_updated_at = forms.CharField(widget=forms.HiddenInput)


class ExaminationCycleOpenForm(_CycleTransitionForm):
    pass


class ExaminationCycleCloseForm(_CycleTransitionForm):
    pass


class CourseExamConfigurationForm(forms.ModelForm):
    expected_revision = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = CourseExamConfiguration
        fields = [
            "final_item_count", "questions_required_per_faculty", "coverage",
            "additional_instructions", "contribution_deadline",
        ]
        widgets = {
            "coverage": forms.Textarea(attrs={"rows": 3}),
            "additional_instructions": forms.Textarea(attrs={"rows": 3}),
            "contribution_deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def clean_coverage(self):
        return (self.cleaned_data.get("coverage") or "").strip()

    def clean_additional_instructions(self):
        return (self.cleaned_data.get("additional_instructions") or "").strip()

    def clean(self):
        cleaned = super().clean()
        for field in ("final_item_count", "questions_required_per_faculty"):
            value = cleaned.get(field)
            if value is not None and not 1 <= value <= 200:
                self.add_error(field, "Value must be from 1 to 200.")
        return cleaned


class _ConfigurationActionForm(forms.Form):
    expected_revision = forms.IntegerField(widget=forms.HiddenInput)


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
