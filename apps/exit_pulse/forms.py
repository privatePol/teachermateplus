from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from apps.academics.models import FacultyAssignment
from apps.exit_pulse.models import ExitPulseResponse, ExitPulseSession
from apps.exit_pulse.services import ExitPulseQuestionValidationService, ExitPulseSessionService


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
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 200}),
    )
    feedback_learned = forms.CharField(
        required=False,
        max_length=200,
        widget=forms.Textarea(attrs={"rows": 3, "maxlength": 200}),
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
