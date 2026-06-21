from django import forms
from django.db import models

from apps.grading.services import EnrollmentSafetyService

from .models import Enrollment


def _offering_label(obj):
    course = getattr(obj, "course", None)
    course_title = (getattr(course, "title", "") or "").strip()
    course_code = (getattr(course, "code", "") or "").strip()

    return f"{course_title} ({course_code})" if course_title and course_code else (course_title or course_code or str(obj))


def _offering_group_label(obj):
    section = getattr(obj, "section", None)
    term = getattr(obj, "term", None)
    campus = getattr(obj, "campus", None)

    section_name = (getattr(section, "name", "") or "").strip()
    section_code = (getattr(section, "code", "") or "").strip()
    term_name = (getattr(term, "name", "") or "").strip()
    term_code = (getattr(term, "code", "") or "").strip()
    campus_name = (getattr(campus, "name", "") or "").strip()
    campus_code = (getattr(campus, "code", "") or "").strip()

    section_label = (
        f"{section_name} ({section_code})"
        if section_name and section_code and section_name != section_code
        else (section_name or section_code or "-")
    )
    term_label = term_name or term_code or "-"
    campus_label = campus_name or campus_code or "-"
    return f"{campus_label} | {term_label} | {section_label}"


def _active_offerings_for_enrollment(queryset):
    return (
        queryset.filter(is_active=True)
        .select_related("campus", "term", "course", "section")
        .order_by(
            "campus__name",
            "campus__code",
            "term__sequence_no",
            "term__name",
            "section__name",
            "section__code",
            "course__title",
            "course__code",
            "id",
        )
    )


def _grouped_offering_choices(queryset):
    grouped = []
    group_index = {}
    for offering in queryset:
        group_label = _offering_group_label(offering)
        if group_label not in group_index:
            group_index[group_label] = len(grouped)
            grouped.append((group_label, []))
        grouped[group_index[group_label]][1].append((offering.pk, _offering_label(offering)))
    return grouped


class EnrollmentForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ["course_offering", "student", "enrollment_status", "is_active"]

    def __init__(self, *args, offering_queryset=None, student_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if offering_queryset is not None:
            offering_queryset = _active_offerings_for_enrollment(offering_queryset)
            self.fields["course_offering"].queryset = offering_queryset
            self.fields["course_offering"].choices = _grouped_offering_choices(offering_queryset)
        if student_queryset is not None:
            self.fields["student"].queryset = student_queryset.filter(is_active=True)
        self.fields["course_offering"].label_from_instance = _offering_label

    def clean(self):
        cleaned = super().clean()
        offering = cleaned.get("course_offering")
        student = cleaned.get("student")
        if offering and student:
            if student.tenant_id != offering.tenant_id:
                raise forms.ValidationError("Student and offering must belong to the same tenant.")
            if student.campus_id != offering.campus_id:
                raise forms.ValidationError("Student campus must match offering campus.")
        EnrollmentSafetyService.validate_changes_allowed(enrollment=self.instance, cleaned_data=cleaned)
        return cleaned


class ClassListAddRequestForm(forms.Form):
    student = forms.ModelChoiceField(queryset=None, required=False)
    student_number = forms.CharField(max_length=32, required=False)
    student_name = forms.CharField(max_length=150, required=False)
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional note for Campus Admin review.",
    )

    def __init__(self, *args, student_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["student"].queryset = student_queryset if student_queryset is not None else self.fields["student"].queryset
        self.fields["student"].required = False
        self.fields["student"].label_from_instance = (
            lambda obj: f"{obj.student_no} - {obj.last_name}, {obj.first_name}"
        )
        self.fields["student"].widget.attrs["class"] = "form-select"
        self.fields["student_number"].widget.attrs["class"] = "form-control"
        self.fields["student_name"].widget.attrs["class"] = "form-control"
        self.fields["student_number"].widget.attrs["placeholder"] = "Student number"
        self.fields["student_name"].widget.attrs["placeholder"] = "Student name"
        self.fields["remarks"].widget.attrs["class"] = "form-control"

    def clean(self):
        cleaned = super().clean()
        student = cleaned.get("student")
        student_number = (cleaned.get("student_number") or "").strip()
        student_name = (cleaned.get("student_name") or "").strip()
        if not student and not student_number and not student_name:
            raise forms.ValidationError("Provide a matched student or enter a student number or name for the request.")
        if student:
            cleaned["student_number"] = student.student_no
            cleaned["student_name"] = f"{student.last_name}, {student.first_name}"
        else:
            cleaned["student_number"] = student_number
            cleaned["student_name"] = student_name or student_number
        return cleaned


class ClassListRemoveRequestForm(forms.Form):
    enrollments = forms.ModelMultipleChoiceField(queryset=None, required=True, widget=forms.CheckboxSelectMultiple)
    remarks = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Explain why these students should be removed from the class master list.",
    )

    def __init__(self, *args, enrollment_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["enrollments"].queryset = (
            enrollment_queryset if enrollment_queryset is not None else self.fields["enrollments"].queryset
        )
        self.fields["enrollments"].label_from_instance = (
            lambda obj: f"{obj.student.student_no} - {obj.student.last_name}, {obj.student.first_name}"
        )
        self.fields["remarks"].widget.attrs["class"] = "form-control"


class ClassListChangeRequestReviewForm(forms.Form):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"

    decision = forms.ChoiceField(choices=Decision.choices, widget=forms.Select(attrs={"class": "form-select"}))
    review_remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
        help_text="Required when rejecting a request.",
    )
    resolved_student = forms.ModelChoiceField(queryset=None, required=False)

    def __init__(self, *args, student_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["resolved_student"].queryset = (
            student_queryset if student_queryset is not None else self.fields["resolved_student"].queryset
        )
        self.fields["resolved_student"].required = False
        self.fields["resolved_student"].label_from_instance = (
            lambda obj: f"{obj.student_no} - {obj.last_name}, {obj.first_name}"
        )
        self.fields["resolved_student"].widget.attrs["class"] = "form-select"

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get("decision")
        review_remarks = (cleaned.get("review_remarks") or "").strip()
        if decision == self.Decision.REJECT and not review_remarks:
            raise forms.ValidationError("A rejection reason is required.")
        return cleaned
