from django import forms

from .models import ExaminationCycle
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
