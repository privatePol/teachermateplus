from django import forms

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
        return cleaned
