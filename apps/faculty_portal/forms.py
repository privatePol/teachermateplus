import json
from decimal import Decimal, InvalidOperation

from django import forms
from django.db import models

from apps.academics.models import CourseOffering
from apps.core.services.uploads import UploadValidationService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeActivity,
    GradeCorrectionRequestItem,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplateSubcomponent,
)
from apps.notifications.models import FacultyMemo
from apps.students.models import Student


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


def _offering_label(obj):
    course = getattr(obj, "course", None)
    section = getattr(obj, "section", None)
    term = getattr(obj, "term", None)
    course_label = " ".join(
        part for part in [getattr(course, "title", ""), f"({getattr(course, 'code', '')})"] if part
    ).strip()
    section_label = getattr(section, "name", None) or getattr(section, "code", None) or "-"
    term_label = getattr(term, "name", None) or getattr(term, "code", None) or "-"
    return f"{course_label} | {section_label} | {term_label}"


def _student_label(obj):
    student_no = getattr(obj, "student_no", None) or ""
    name_parts = [
        getattr(obj, "last_name", ""),
        getattr(obj, "first_name", ""),
        getattr(obj, "middle_name", ""),
    ]
    name = " ".join(part for part in name_parts if part).strip()
    if student_no and name:
        return f"{student_no} - {name}"
    return student_no or name or str(obj)


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


class FacultyTemplateIssueReportForm(forms.Form):
    class IssueType(models.TextChoices):
        WRONG_WEIGHT = "WRONG_WEIGHT", "Wrong component or period weight"
        MISSING_BUCKET = "MISSING_BUCKET", "Missing component or activity category"
        WRONG_STRUCTURE = "WRONG_STRUCTURE", "Wrong period or exam/class-standing structure"
        TEMPLATE_MISMATCH = "TEMPLATE_MISMATCH", "Template does not match approved policy"
        OTHER = "OTHER", "Other template issue"

    issue_type = forms.ChoiceField(
        choices=IssueType.choices,
        label="Issue Type",
    )
    details = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 5}),
        label="What is wrong with the template?",
        help_text="Describe the template problem. Include the period, component, expected weight, or policy reference when available.",
    )


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
        self.fields["template_detail"].label_from_instance = (
            lambda obj: obj.name
            if getattr(obj.template_subcomponent, "detail_computation_mode", None) == "AVERAGE_ACTIVITIES"
            else f"{obj.name} ({obj.weight_percentage}% configured weight)"
        )
        self.fields["activity_date"].input_formats = ["%Y-%m-%d", "%m-%d-%Y", "%m/%d/%Y"]
        self.fields["title"].help_text = (
            "Keep titles short and consistent so the summary stays easy to read. "
            "Examples: Q1, Q2, R1, A1, AC1, Prelim Exam."
        )
        self.fields["total_score"].help_text = (
            "Required for Raw Score (Base-50). For Direct Percentage items, TeacherMate+ will use 100 automatically."
        )
        self.fields["activity_date"].help_text = (
            "Use the actual activity date. TeacherMate+ uses this for activity order, audit clarity, and class record review."
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
    apply_to_all_students = forms.BooleanField(required=False)
    students = forms.ModelMultipleChoiceField(queryset=None, required=False)
    grade_activities = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    correction_payload = forms.CharField(required=False, widget=forms.HiddenInput())
    justification = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    attachment = forms.FileField(required=False)

    def __init__(self, *args, student_queryset=None, activity_queryset=None, score_lookup=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.score_lookup = score_lookup or {}
        self.fields["students"].queryset = (
            student_queryset if student_queryset is not None else self.fields["students"].queryset
        )
        self.fields["grade_activities"].queryset = (
            activity_queryset if activity_queryset is not None else self.fields["grade_activities"].queryset
        )
        _enforce_active_reference_choices(self)
        self.fields["apply_to_all_students"].widget.attrs["class"] = "form-check-input"
        self.fields["students"].label_from_instance = (
            lambda obj: f"{obj.student_no} - {obj.last_name}, {obj.first_name}"
        )
        self.fields["grade_activities"].label_from_instance = (
            lambda obj: (
                f"{obj.template_component.code}"
                f"{' / ' + obj.template_subcomponent.code if obj.template_subcomponent else ''}"
                f"{' / ' + obj.template_detail.code + ' (' + str(obj.template_detail.weight_percentage) + '% configured weight)' if obj.template_detail else ''}"
                f" - {obj.title}"
            )
        )
        self.fields["students"].widget.attrs.update({"class": "form-select", "size": 12})
        self.fields["justification"].widget.attrs["class"] = "form-control"
        self.fields["attachment"].widget.attrs["class"] = "form-control"

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return attachment
        self.cleaned_data["attachment_validation"] = UploadValidationService.validate_correction_attachment(attachment)
        return attachment

    @staticmethod
    def _format_decimal(value):
        if value in (None, ""):
            return ""
        decimal_value = Decimal(str(value))
        formatted = format(decimal_value.quantize(Decimal("0.01")), "f")
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted

    @staticmethod
    def _parse_decimal(value):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _score_input_mode(activity):
        if activity.template_detail and getattr(activity.template_detail, "score_input_mode", "INHERIT") != "INHERIT":
            return activity.template_detail.score_input_mode
        if (
            activity.template_subcomponent
            and getattr(activity.template_subcomponent, "score_input_mode", "INHERIT") != "INHERIT"
        ):
            return activity.template_subcomponent.score_input_mode
        return getattr(activity.template_component, "score_input_mode", "RAW_BASE50") or "RAW_BASE50"

    def clean(self):
        cleaned = super().clean()
        apply_to_all_students = cleaned.get("apply_to_all_students")
        selected_students = cleaned.get("students")
        selected_activities = cleaned.get("grade_activities")
        payload_raw = cleaned.get("correction_payload") or "[]"

        if not apply_to_all_students and not selected_students:
            self.add_error("students", "Select at least one student or choose Entire Class.")
        if not selected_activities:
            self.add_error("grade_activities", "Select at least one grading item for correction.")

        try:
            payload_rows = json.loads(payload_raw)
        except json.JSONDecodeError:
            self.add_error(None, "Unable to read the correction grid. Please review the selected rows and try again.")
            return cleaned

        if not isinstance(payload_rows, list):
            self.add_error(None, "Invalid correction grid payload.")
            return cleaned

        selected_student_ids = set(self.fields["students"].queryset.values_list("id", flat=True)) if apply_to_all_students else {
            student.id for student in (selected_students or [])
        }
        activity_map = {activity.id: activity for activity in (selected_activities or [])}

        items = []
        seen_pairs = set()
        for row in payload_rows:
            if not isinstance(row, dict):
                continue
            student_id = row.get("student_id")
            activity_id = row.get("grade_activity_id")
            if student_id in (None, "") or activity_id in (None, ""):
                continue

            try:
                student_id = int(student_id)
                activity_id = int(activity_id)
            except (TypeError, ValueError):
                self.add_error(None, "Correction grid contains an invalid student or grading item reference.")
                continue

            if student_id not in selected_student_ids or activity_id not in activity_map:
                self.add_error(None, "Correction grid no longer matches the selected students or grading items.")
                continue

            pair_key = (student_id, activity_id)
            if pair_key in seen_pairs:
                self.add_error(None, "Duplicate correction rows were detected.")
                continue
            seen_pairs.add(pair_key)

            new_value = str(row.get("new_value") or "").strip()
            if not new_value:
                continue

            parsed_new_value = self._parse_decimal(new_value)
            if parsed_new_value is None:
                activity = activity_map[activity_id]
                self.add_error(
                    None,
                    f"Invalid corrected value for {activity.title}. Enter a valid number.",
                )
                continue

            activity = activity_map[activity_id]
            score_input_mode = self._score_input_mode(activity)
            max_value = Decimal("100") if score_input_mode == "DIRECT_PERCENTAGE" else Decimal(activity.total_score)
            if parsed_new_value < 0 or parsed_new_value > max_value:
                self.add_error(
                    None,
                    f"Corrected value for {activity.title} must be between 0 and {self._format_decimal(max_value)}.",
                )
                continue

            items.append(
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": student_id,
                    "grade_activity_id": activity_id,
                    "old_value": self.score_lookup.get((student_id, activity_id), ""),
                    "new_value": self._format_decimal(parsed_new_value),
                }
            )

        if not items:
            self.add_error(None, "Enter at least one corrected value before submitting the request.")
        cleaned["items"] = items
        return cleaned


class FacultyReminderForm(forms.Form):
    title = forms.CharField(
        max_length=160,
        label="Reminder title",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    reminder_type = forms.ChoiceField(
        choices=[
            ("ACTIVITY_PREPARATION", "Activity Preparation"),
            ("SCORE_ENCODING", "Score Encoding"),
            ("ASSIGNMENT_ACCEPTANCE", "Assignment Acceptance"),
            ("GRADE_SUBMISSION", "Grade Submission"),
            ("CORRECTION_WINDOW", "Correction Window"),
            ("AT_RISK_FOLLOWUP", "Student Follow-up"),
            ("CUSTOM", "Custom"),
        ],
        label="Reminder type",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    offering = forms.ModelChoiceField(
        queryset=CourseOffering.objects.none(),
        label="Class",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    period_label = forms.CharField(
        max_length=120,
        required=False,
        label="Period / Phase",
        help_text="Optional. Example: PRELIM, MIDTERM, PRE-FINAL, Final Exam.",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    remind_at = forms.DateTimeField(
        label="Remind at",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )
    due_at = forms.DateTimeField(
        required=False,
        label="Due at",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )
    send_email = forms.BooleanField(
        required=False,
        label="Send email reminder",
        help_text="Queue an email reminder to the faculty email address when this reminder becomes due.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    notes = forms.CharField(
        required=False,
        label="Notes",
        widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
    )

    def __init__(self, *args, offering_queryset=None, send_email_enabled: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        if offering_queryset is not None:
            self.fields["offering"].queryset = offering_queryset
        _enforce_active_reference_choices(self)
        self.fields["offering"].label_from_instance = _offering_label
        self.fields["title"].widget.attrs["placeholder"] = "Example: Prepare Quiz 1"
        self.fields["period_label"].widget.attrs["placeholder"] = "Example: PRELIM"
        self.fields["notes"].widget.attrs["placeholder"] = "Add a short note for future reference."
        if not send_email_enabled:
            self.fields["send_email"].initial = False
            self.fields["send_email"].help_text = (
                "Email reminders are currently disabled by configuration. You can still save the reminder."
            )

    def clean(self):
        cleaned = super().clean()
        remind_at = cleaned.get("remind_at")
        due_at = cleaned.get("due_at")
        if remind_at and due_at and due_at < remind_at:
            self.add_error("due_at", "Due date cannot be earlier than the reminder date.")
        if not cleaned.get("send_email"):
            cleaned["send_email"] = False
        return cleaned


class FacultyMemoForm(forms.ModelForm):
    class Meta:
        model = FacultyMemo
        fields = ["memo_type", "offering", "student", "title", "body", "is_pinned"]
        widgets = {
            "memo_type": forms.Select(attrs={"class": "form-select"}),
            "offering": forms.Select(attrs={"class": "form-select"}),
            "student": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "body": forms.Textarea(attrs={"rows": 5, "class": "form-control"}),
            "is_pinned": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, offering_queryset=None, student_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if offering_queryset is not None:
            self.fields["offering"].queryset = offering_queryset
        if student_queryset is not None:
            self.fields["student"].queryset = student_queryset
        _enforce_active_reference_choices(self)
        self.fields["offering"].required = False
        self.fields["student"].required = False
        self.fields["offering"].label_from_instance = _offering_label
        self.fields["student"].label_from_instance = _student_label
        self.fields["title"].widget.attrs["placeholder"] = "Example: Follow up on Quiz 1"
        self.fields["body"].widget.attrs["placeholder"] = "Write a private note for yourself or future follow-up."

    def clean(self):
        cleaned = super().clean()
        offering = cleaned.get("offering")
        student = cleaned.get("student")
        memo_type = cleaned.get("memo_type") or FacultyMemo.MemoType.GENERAL
        if memo_type == FacultyMemo.MemoType.CLASS and not offering:
            self.add_error("offering", "Select a class memo should be linked to.")
        if memo_type == FacultyMemo.MemoType.STUDENT and not student:
            self.add_error("student", "Select a student memo should be linked to.")
        if offering and student:
            if not Enrollment.objects.filter(course_offering=offering, student=student, is_active=True).exists():
                self.add_error("student", "The selected student is not enrolled in the selected class.")
        if offering is not None and student is not None and offering.tenant_id != student.tenant_id:
            self.add_error("student", "The selected student does not belong to this tenant scope.")
        return cleaned
