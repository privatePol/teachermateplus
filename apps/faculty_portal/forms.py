from django import forms

from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeActivity,
    GradeCorrectionRequestItem,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplateSubcomponent,
)


def _active_only_queryset(queryset):
    if queryset is None:
        return queryset
    model = getattr(queryset, "model", None)
    if model and any(field.name == "is_active" for field in model._meta.fields):
        return queryset.filter(is_active=True)
    return queryset


def _enforce_active_reference_choices(form):
    for field in form.fields.values():
        if isinstance(field, (forms.ModelChoiceField, forms.ModelMultipleChoiceField)):
            queryset = getattr(field, "queryset", None)
            if queryset is not None:
                field.queryset = _active_only_queryset(queryset)


class FacultyEnrollmentForm(forms.Form):
    student = forms.ModelChoiceField(queryset=None)
    enrollment_status = forms.ChoiceField(choices=Enrollment.Status.choices, initial=Enrollment.Status.ACTIVE)

    def __init__(self, *args, student_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = student_queryset if student_queryset is not None else self.fields["student"].queryset
        _enforce_active_reference_choices(self)
        self.fields["student"].label_from_instance = (
            lambda obj: f"{obj.student_no} - {obj.last_name}, {obj.first_name}"
        )
        self.fields["student"].widget.attrs["class"] = "form-select"
        self.fields["enrollment_status"].widget.attrs["class"] = "form-select"


class GradeActivityForm(forms.ModelForm):
    class Meta:
        model = GradeActivity
        fields = [
            "template_component",
            "template_subcomponent",
            "template_detail",
            "title",
            "total_score",
            "activity_date",
        ]
        widgets = {
            "activity_date": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "form-control",
                },
                format="%Y-%m-%d",
            ),
        }

    def __init__(self, *args, component_queryset=None, subcomponent_queryset=None, detail_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template_component"].queryset = (
            component_queryset if component_queryset is not None else GradingTemplateComponent.objects.none()
        )
        self.fields["template_subcomponent"].queryset = (
            subcomponent_queryset if subcomponent_queryset is not None else GradingTemplateSubcomponent.objects.none()
        )
        self.fields["template_detail"].queryset = (
            detail_queryset if detail_queryset is not None else GradingTemplateDetail.objects.none()
        )
        _enforce_active_reference_choices(self)
        self.fields["template_subcomponent"].required = False
        self.fields["template_detail"].required = False
        self.fields["total_score"].required = False
        self.fields["template_component"].label_from_instance = lambda obj: obj.name
        self.fields["template_subcomponent"].label_from_instance = lambda obj: obj.name
        self.fields["template_detail"].label_from_instance = lambda obj: obj.name
        self.fields["activity_date"].input_formats = ["%Y-%m-%d", "%m-%d-%Y", "%m/%d/%Y"]
        self.fields["title"].help_text = (
            "Keep titles short and consistent so the summary stays easy to read. "
            "Examples: Q1, Q2, R1, A1, AC1, Prelim Exam."
        )
        self.fields["total_score"].help_text = (
            "Required for Raw Score (Base-50). For Direct Percentage items, EduGradesPro will use 100 automatically."
        )
        self.fields["activity_date"].help_text = (
            "Use the actual activity date. EduGradesPro uses this for activity order, audit clarity, and class record review."
        )

        for name, field in self.fields.items():
            if getattr(field.widget, "input_type", None) == "date":
                field.widget.attrs["class"] = "form-control"
            elif getattr(field.widget, "input_type", None) in {"number", "text"}:
                field.widget.attrs["class"] = "form-control"
            else:
                field.widget.attrs["class"] = "form-select"
        self.fields["title"].widget.attrs["placeholder"] = "Example: Q1, R1, A1, AC1, Prelim Exam"
        self.fields["template_subcomponent"].widget.attrs["disabled"] = "disabled"
        self.fields["template_detail"].widget.attrs["disabled"] = "disabled"

    def clean(self):
        cleaned = super().clean()
        component = cleaned.get("template_component")
        subcomponent = cleaned.get("template_subcomponent")
        detail = cleaned.get("template_detail")
        total_score = cleaned.get("total_score")
        if subcomponent and component and subcomponent.template_component_id != component.id:
            raise forms.ValidationError("Subcomponent does not belong to selected component.")
        if detail and (not subcomponent or detail.template_subcomponent_id != subcomponent.id):
            raise forms.ValidationError("Detail does not belong to selected subcomponent.")
        mode = self._resolve_score_input_mode(component, subcomponent, detail)
        if mode == "DIRECT_PERCENTAGE":
            cleaned["total_score"] = total_score or 100
        elif total_score in (None, ""):
            self.add_error("total_score", "Total score is required for Raw Score (Base-50) items.")
        return cleaned

    @staticmethod
    def _resolve_score_input_mode(component, subcomponent, detail):
        if detail:
            detail_mode = getattr(detail, "score_input_mode", None)
            if detail_mode and detail_mode != "INHERIT":
                return detail_mode
        if subcomponent:
            sub_mode = getattr(subcomponent, "score_input_mode", None)
            if sub_mode and sub_mode != "INHERIT":
                return sub_mode
        return getattr(component, "score_input_mode", "RAW_BASE50") if component else "RAW_BASE50"


class AttendanceSessionForm(forms.Form):
    session_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}))
    title = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs={"class": "form-control"}))


class GradeCorrectionRequestForm(forms.Form):
    requested_action = forms.ChoiceField(
        choices=GradeCorrectionRequestItem.RequestedAction.choices,
    )
    student = forms.ModelChoiceField(queryset=None, required=False)
    grade_activity = forms.ModelChoiceField(queryset=None, required=False)
    old_value = forms.CharField(max_length=255, required=False)
    new_value = forms.CharField(max_length=255, required=False)
    justification = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    attachment = forms.FileField(required=False)

    def __init__(self, *args, student_queryset=None, activity_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = student_queryset if student_queryset is not None else self.fields["student"].queryset
        self.fields["grade_activity"].queryset = activity_queryset if activity_queryset is not None else self.fields["grade_activity"].queryset
        _enforce_active_reference_choices(self)
        self.fields["student"].label_from_instance = (
            lambda obj: f"{obj.student_no} - {obj.last_name}, {obj.first_name}"
        )
        self.fields["grade_activity"].label_from_instance = (
            lambda obj: (
                f"{obj.title} ({obj.template_component.code}"
                f"{'/' + obj.template_subcomponent.code if obj.template_subcomponent else ''}"
                f"{'/' + obj.template_detail.code if obj.template_detail else ''})"
            )
        )
        for name, field in self.fields.items():
            if name == "justification":
                field.widget.attrs["class"] = "form-control"
            elif name == "requested_action":
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs["class"] = "form-control"

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get("requested_action")
        student = cleaned.get("student")
        grade_activity = cleaned.get("grade_activity")

        if action == GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE:
            if not student:
                self.add_error("student", "Student is required for score correction.")
            if not grade_activity:
                self.add_error("grade_activity", "Activity is required for score correction.")
        elif action in {
            GradeCorrectionRequestItem.RequestedAction.UPDATE_ATTENDANCE,
            GradeCorrectionRequestItem.RequestedAction.UPDATE_STATUS,
        }:
            if not student:
                self.add_error("student", "Student is required for this correction action.")
        return cleaned
