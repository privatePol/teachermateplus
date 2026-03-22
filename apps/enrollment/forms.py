from django import forms

from .models import Enrollment


class EnrollmentForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ["course_offering", "student", "enrollment_status", "is_active"]

    def __init__(self, *args, offering_queryset=None, student_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if offering_queryset is not None:
            self.fields["course_offering"].queryset = offering_queryset.filter(is_active=True)
        if student_queryset is not None:
            self.fields["student"].queryset = student_queryset.filter(is_active=True)

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
