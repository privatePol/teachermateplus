from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from apps.academics.models import FacultyAssignment
from apps.exit_pulse.models import ExitPulseResponse, ExitPulseSession
from apps.exit_pulse.services import ExitPulseQuestionValidationService, ExitPulseSessionService


EXIT_PULSE_QUESTION_TYPE_CHOICES = [
    ("", "All question types"),
    (ExitPulseSession.QuestionCode.UNDERSTANDING, "Understanding"),
    (ExitPulseSession.QuestionCode.APPLICATION_CONFIDENCE, "Application confidence"),
    (ExitPulseSession.QuestionCode.NEEDS_EXPLANATION, "Needs explanation"),
    (ExitPulseSession.QuestionCode.CUSTOM, "Custom"),
]


class ExitPulseDashboardFilterForm(forms.Form):
    academic_year = forms.ChoiceField(required=False, label="Academic year")
    term = forms.ChoiceField(required=False, label="Term")

    def __init__(self, *args, scope_rows=(), **kwargs):
        super().__init__(*args, **kwargs)
        year_choices = {}
        term_choices = {}
        for row in scope_rows:
            year_choices[str(row["academic_year_id"])] = row["academic_year__code"]
            term_choices[str(row["term_id"])] = (
                f'{row["academic_year__code"]} - {row["term__name"]} ({row["term__code"]})'
            )
        self.fields["academic_year"].choices = [("", "All academic years"), *year_choices.items()]
        self.fields["term"].choices = [("", "All terms"), *term_choices.items()]
        for name, field in self.fields.items():
            field.widget.attrs["class"] = "form-select"
            if self.is_bound and name in self.errors:
                field.widget.attrs["aria-invalid"] = "true"
                field.widget.attrs["aria-describedby"] = f"id_{name}_errors"


class ExitPulseComparisonFilterForm(ExitPulseDashboardFilterForm):
    question_type = forms.ChoiceField(
        required=False,
        label="Question type",
        choices=EXIT_PULSE_QUESTION_TYPE_CHOICES,
    )


class ExitPulseHistoryFilterForm(forms.Form):
    STATUS_CHOICES = [
        ("", "All completed statuses"),
        (ExitPulseSession.Status.CLOSED, "Closed"),
        (ExitPulseSession.Status.EXPIRED, "Expired"),
    ]

    date_from = forms.DateField(
        required=False,
        label="Date from",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label="Date to",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    question_type = forms.ChoiceField(
        required=False,
        label="Question type",
        choices=EXIT_PULSE_QUESTION_TYPE_CHOICES,
    )
    topic = forms.CharField(
        required=False,
        max_length=200,
        label="Topic contains",
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Search lesson topic"}),
    )
    status = forms.ChoiceField(
        required=False,
        label="Status",
        choices=STATUS_CHOICES,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )
            if self.is_bound and name in self.errors:
                field.widget.attrs["aria-invalid"] = "true"
                field.widget.attrs["aria-describedby"] = f"id_{name}_errors"

    def clean_topic(self):
        return " ".join((self.cleaned_data.get("topic") or "").split())

    def clean(self):
        cleaned = super().clean()
        date_from = cleaned.get("date_from")
        date_to = cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "Date to must be on or after Date from.")
        return cleaned


class ExitPulseCreateForm(forms.Form):
    faculty_assignment = forms.ModelChoiceField(
        queryset=FacultyAssignment.objects.none(),
        label="Class assignment",
        empty_label="Select your class assignment",
    )
    topic = forms.CharField(
        max_length=200,
        label="Lesson topic",
        widget=forms.TextInput(attrs={"placeholder": "Example: Database normalization", "autocomplete": "off"}),
    )
    question_code = forms.ChoiceField(
        choices=ExitPulseSession.QuestionCode.choices,
        label="Learning Check question",
    )
    custom_question = forms.CharField(
        required=False,
        max_length=250,
        label="Custom question",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Ask about the lesson, activity, understanding, confidence, clarification, or practice.",
            }
        ),
        help_text=(
            "Questions must focus on the lesson, topic, activity, or student understanding. "
            "Do not ask students to rate the faculty member."
        ),
    )
    allow_written_feedback = forms.BooleanField(
        required=False,
        initial=False,
        label="Allow optional written feedback",
    )
    feedback_review_enabled = forms.BooleanField(
        required=False,
        initial=False,
        label=ExitPulseSession.FEEDBACK_REVIEW_PROMPT,
    )
    feedback_learned_enabled = forms.BooleanField(
        required=False,
        initial=False,
        label=ExitPulseSession.FEEDBACK_LEARNED_PROMPT,
    )

    def __init__(self, *args, user, tenant_id=None, campus_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = ExitPulseSessionService.valid_assignments_for_user(
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
        )
        self.fields["faculty_assignment"].queryset = queryset
        self.fields["faculty_assignment"].label_from_instance = self._assignment_label
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    @staticmethod
    def _assignment_label(assignment):
        offering = assignment.offering
        return (
            f"{offering.course.title} ({offering.course.code}) - {offering.section.code} | "
            f"{offering.academic_year.code} / {offering.term.code} | {offering.campus.name}"
        )

    def clean(self):
        cleaned = super().clean()
        question_code = cleaned.get("question_code")
        custom_question = cleaned.get("custom_question", "")
        if question_code == ExitPulseSession.QuestionCode.CUSTOM:
            try:
                cleaned["custom_question"] = ExitPulseQuestionValidationService.validate_custom_question(
                    custom_question
                )
            except ValidationError as exc:
                self.add_error("custom_question", exc)
        else:
            cleaned["custom_question"] = ""
        if not cleaned.get("allow_written_feedback"):
            cleaned["feedback_review_enabled"] = False
            cleaned["feedback_learned_enabled"] = False
        elif not (cleaned.get("feedback_review_enabled") or cleaned.get("feedback_learned_enabled")):
            self.add_error(
                "allow_written_feedback",
                "Enable at least one written-feedback prompt.",
            )
        return cleaned

    @property
    def question_snapshot(self):
        return ExitPulseQuestionValidationService.question_snapshot(
            self.cleaned_data["question_code"],
            self.cleaned_data.get("custom_question", ""),
        )


class ExitPulseResponseForm(forms.Form):
    response_code = forms.ChoiceField(
        choices=ExitPulseResponse.ResponseCode.choices,
        widget=forms.RadioSelect,
        label="Choose your learning status",
    )
    feedback_review = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "maxlength": 200,
                "aria-describedby": "feedback-review-help feedback-review-error",
            }
        ),
    )
    feedback_learned = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "maxlength": 200,
                "aria-describedby": "feedback-learned-help feedback-learned-error",
            }
        ),
    )

    def __init__(self, *args, session, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session
        if session.feedback_review_enabled:
            self.fields["feedback_review"].label = session.feedback_review_prompt_snapshot
            self.fields["feedback_review"].widget.attrs["placeholder"] = "Optional response"
        else:
            self.fields.pop("feedback_review")
        if session.feedback_learned_enabled:
            self.fields["feedback_learned"].label = session.feedback_learned_prompt_snapshot
            self.fields["feedback_learned"].widget.attrs["placeholder"] = "Optional response"
        else:
            self.fields.pop("feedback_learned")

    def clean(self):
        cleaned = super().clean()
        if not self.session.allow_written_feedback:
            cleaned["feedback_review"] = ""
            cleaned["feedback_learned"] = ""
        return cleaned
