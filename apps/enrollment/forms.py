from django import forms

from .models import Enrollment


def _offering_label(obj):
    course = getattr(obj, "course", None)
    section = getattr(obj, "section", None)
    term = getattr(obj, "term", None)
    campus = getattr(obj, "campus", None)

    course_title = (getattr(course, "title", "") or "").strip()
    course_code = (getattr(course, "code", "") or "").strip()
    section_name = (getattr(section, "name", "") or "").strip()
    section_code = (getattr(section, "code", "") or "").strip()
    term_name = (getattr(term, "name", "") or "").strip()
    term_code = (getattr(term, "code", "") or "").strip()
    campus_name = (getattr(campus, "name", "") or "").strip()
    campus_code = (getattr(campus, "code", "") or "").strip()

    course_label = f"{course_title} ({course_code})" if course_title and course_code else (course_title or course_code or str(obj))
    section_label = (
        f"{section_name} ({section_code})"
        if section_name and section_code and section_name != section_code
        else (section_name or section_code or "-")
    )
    term_label = term_name or term_code or "-"
    campus_label = campus_name or campus_code or "-"
    return f"{campus_label} | {term_label} | {section_label} | {course_label}"


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


class EnrollmentForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ["course_offering", "student", "enrollment_status", "is_active"]

    def __init__(self, *args, offering_queryset=None, student_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if offering_queryset is not None:
            self.fields["course_offering"].queryset = _active_offerings_for_enrollment(offering_queryset)
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
        return cleaned
