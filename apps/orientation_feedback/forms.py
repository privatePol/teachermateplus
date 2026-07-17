from __future__ import annotations

from django import forms

from apps.academics.models import AcademicYear, Term
from apps.orientation_feedback.models import (
    OrientationSurveyQuestion,
    OrientationSurveySession,
)
from apps.rbac.models import Role


def _apply_bootstrap(form):
    for name, field in form.fields.items():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", "form-check-input")
        elif isinstance(field.widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
            # Multi-widget attributes are also placed on Django's outer group
            # container. The fixed-size Bootstrap input class would therefore
            # collapse the complete option list on narrow screens.
            pass
        else:
            field.widget.attrs.setdefault(
                "class",
                "form-select" if isinstance(field.widget, forms.Select) else "form-control",
            )
        if form.is_bound and name in form.errors:
            field.widget.attrs["aria-invalid"] = "true"


class OrientationSurveySessionForm(forms.ModelForm):
    eligible_head_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Eligible academic-head roles",
        help_text="Used only for the Academic Heads survey. AC, College Dean, and CAO are selected by default when available.",
    )

    class Meta:
        model = OrientationSurveySession
        fields = [
            "survey_type",
            "title",
            "description",
            "academic_year",
            "term",
            "orientation_date",
            "intended_start_time",
            "intended_end_time",
            "auto_close_at",
            "eligible_head_roles",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "orientation_date": forms.DateInput(attrs={"type": "date"}),
            "intended_start_time": forms.TimeInput(attrs={"type": "time"}),
            "intended_end_time": forms.TimeInput(attrs={"type": "time"}),
            "auto_close_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
        help_texts = {
            "auto_close_at": "Optional. The session closes automatically at this date and time.",
        }

    def __init__(self, *args, tenant_id, campus_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant_id = tenant_id
        self.campus_id = campus_id
        self.fields["academic_year"].queryset = AcademicYear.objects.filter(
            tenant_id=tenant_id,
            is_active=True,
        ).order_by("-start_date", "code")
        self.fields["term"].queryset = Term.objects.filter(
            tenant_id=tenant_id,
            is_active=True,
            academic_year__is_active=True,
        ).select_related("academic_year").order_by("-academic_year__start_date", "sequence_no")
        self.fields["eligible_head_roles"].queryset = Role.objects.filter(is_active=True).exclude(
            code__in=["FACULTY", "SUPER_ADMIN"]
        ).order_by("name", "code")
        if not self.is_bound and not self.instance.pk:
            defaults = Role.objects.filter(
                is_active=True,
                code__in=["AC", "COLLEGE_DEAN", "CAO"],
            )
            self.initial.setdefault("eligible_head_roles", list(defaults.values_list("pk", flat=True)))
        elif self.instance.pk:
            self.initial.setdefault(
                "eligible_head_roles",
                list(self.instance.eligible_head_roles.values_list("pk", flat=True)),
            )
        _apply_bootstrap(self)

    def clean(self):
        cleaned = super().clean()
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        if academic_year and academic_year.tenant_id != self.tenant_id:
            self.add_error("academic_year", "Choose an academic year in the active tenant scope.")
        if term:
            if term.tenant_id != self.tenant_id:
                self.add_error("term", "Choose a term in the active tenant scope.")
            elif academic_year and term.academic_year_id != academic_year.id:
                self.add_error("term", "Choose a term from the selected academic year.")
        if (
            cleaned.get("survey_type") == OrientationSurveySession.SurveyType.ACADEMIC_HEADS
            and not cleaned.get("eligible_head_roles")
        ):
            self.add_error("eligible_head_roles", "Select at least one eligible academic-head role.")
        if cleaned.get("survey_type") == OrientationSurveySession.SurveyType.FACULTY:
            cleaned["eligible_head_roles"] = Role.objects.none()
        return cleaned


class OrientationQuestionEditForm(forms.ModelForm):
    class Meta:
        model = OrientationSurveyQuestion
        fields = ["text", "is_required"]
        widgets = {"text": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap(self)

    def clean_text(self):
        return " ".join((self.cleaned_data.get("text") or "").split())


OrientationQuestionFormSet = forms.modelformset_factory(
    OrientationSurveyQuestion,
    form=OrientationQuestionEditForm,
    extra=0,
    can_delete=False,
)


class OrientationCancellationForm(forms.Form):
    reason = forms.CharField(
        max_length=500,
        label="Cancellation reason",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap(self)

    def clean_reason(self):
        value = " ".join((self.cleaned_data.get("reason") or "").split())
        if not value:
            raise forms.ValidationError("Enter a cancellation reason.")
        return value


class OrientationEmailValidationForm(forms.Form):
    email = forms.EmailField(
        max_length=254,
        label="Registered institutional email address",
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "inputmode": "email",
                "placeholder": "name@example.edu",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap(self)

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip()


class OrientationEmailOtpForm(forms.Form):
    otp_code = forms.CharField(
        min_length=6,
        max_length=6,
        label="Verification code",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]{6}",
                "placeholder": "6-digit code",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_bootstrap(self)

    def clean_otp_code(self):
        value = (self.cleaned_data.get("otp_code") or "").strip().replace(" ", "")
        if not value.isdigit() or len(value) != 6:
            raise forms.ValidationError("Enter the 6-digit verification code.")
        return value


class OrientationResponseForm(forms.Form):
    def __init__(self, *args, questions, **kwargs):
        super().__init__(*args, **kwargs)
        self.questions = list(questions)
        self.question_rows = []
        for question in self.questions:
            field_name = f"q_{question.code}"
            choice_rows = list(question.choices.all())
            if question.question_type == OrientationSurveyQuestion.QuestionType.SCALE:
                field = forms.ChoiceField(
                    required=question.is_required,
                    label=question.text,
                    choices=[
                        (choice.code, f"{choice.emoji} {choice.label}".strip())
                        for choice in choice_rows
                    ],
                    widget=forms.RadioSelect,
                    error_messages={"required": "Choose one response."},
                )
            elif question.question_type == OrientationSurveyQuestion.QuestionType.MULTI_SELECT:
                field = forms.MultipleChoiceField(
                    required=question.is_required,
                    label=question.text,
                    choices=[(choice.code, choice.label) for choice in choice_rows],
                    widget=forms.CheckboxSelectMultiple,
                    error_messages={"required": "Choose at least one area."},
                )
            else:
                field = forms.CharField(
                    required=question.is_required,
                    label=question.text,
                    max_length=2000,
                    widget=forms.Textarea(attrs={"rows": 3}),
                )
            self.fields[field_name] = field
            other_field_name = ""
            if any(choice.allows_other_text for choice in choice_rows):
                other_field_name = f"other_{question.code}"
                self.fields[other_field_name] = forms.CharField(
                    required=False,
                    max_length=250,
                    label="Other area (optional)",
                    widget=forms.TextInput(attrs={"placeholder": "Please specify"}),
                )
            self.question_rows.append(
                {
                    "question": question,
                    "field_name": field_name,
                    "other_field_name": other_field_name,
                }
            )
        _apply_bootstrap(self)
        for row in self.question_rows:
            row["field"] = self[row["field_name"]]
            row["other_field"] = self[row["other_field_name"]] if row["other_field_name"] else None

    def clean(self):
        cleaned = super().clean()
        for question in self.questions:
            if question.question_type != OrientationSurveyQuestion.QuestionType.MULTI_SELECT:
                continue
            field_name = f"q_{question.code}"
            selected = set(cleaned.get(field_name) or [])
            if not selected:
                continue
            choices = {choice.code: choice for choice in question.choices.all()}
            none_codes = {code for code, choice in choices.items() if choice.label == "None at the moment"}
            if selected & none_codes and len(selected) > 1:
                self.add_error(field_name, "Choose either None at the moment or the applicable guidance areas.")
        return cleaned
