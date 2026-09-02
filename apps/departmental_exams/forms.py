from django import forms
from django.utils.dateparse import parse_datetime

from apps.admin_portal.course_exam_department import configure_exam_department_field
from apps.accounts.models import User
from apps.tenants.models import Department

from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExamGenerationRevision,
    ExaminationCycle,
    normalize_contribution_deadline_to_minute,
)


_STALE_FORM_STATE_ERROR = (
    "This page state is missing or invalid. Reload the page and try again."
)


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
        configure_exam_department_field(
            self.fields["responsible_department"],
            self.fields["responsible_department"].queryset,
        )
        self.fields["responsible_department"].widget.attrs["class"] = "form-select"
        self.fields["reviewer"].widget.attrs["class"] = "form-select"


class CycleCourseExemptionForm(forms.Form):
    exemption_category = forms.ChoiceField(
        choices=CycleCourse.ExemptionCategory.choices,
        label="Exemption category",
    )
    reason = forms.CharField(
        min_length=10,
        max_length=500,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Enter a specific reason from 10 to 500 characters.",
    )
    expected_updated_at = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exemption_category"].widget.attrs["class"] = "form-select"
        self.fields["reason"].widget.attrs["class"] = "form-control"


class CycleCourseRestoreForm(forms.Form):
    reason = forms.CharField(
        min_length=10,
        max_length=500,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Restoration reason",
        help_text="Enter a specific reason from 10 to 500 characters.",
    )
    expected_updated_at = forms.CharField(widget=forms.HiddenInput())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["class"] = "form-control"


class ExaminationCycleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-select")
        self.fields["processing_mode"].required = False

    class Meta:
        model = ExaminationCycle
        fields = ["academic_year", "term", "exam_period", "processing_mode"]

    def clean(self):
        cleaned = super().clean()
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        if academic_year and term and (term.tenant_id != academic_year.tenant_id or term.academic_year_id != academic_year.id):
            self.add_error("term", "Choose a term belonging to the selected academic year and tenant.")
        return cleaned

    def clean_processing_mode(self):
        return (
            self.cleaned_data.get("processing_mode")
            or ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        )

class ExaminationCycleConfigurationForm(forms.ModelForm):
    expected_updated_at = forms.CharField(
        widget=forms.HiddenInput,
        error_messages={"required": _STALE_FORM_STATE_ERROR},
    )
    reason = forms.CharField(required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 3}), help_text="Required (10-500 characters) when changing defaults on an Open cycle.")

    def clean_processing_mode(self):
        return (
            self.cleaned_data.get("processing_mode")
            or getattr(
                self.instance,
                "processing_mode",
                ExaminationCycle.ProcessingMode.MANUAL_REVIEW,
            )
            or ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        )

    def clean_automatic_campus_contribution_policy(self):
        return (
            self.cleaned_data.get("automatic_campus_contribution_policy")
            or self.instance.automatic_campus_contribution_policy
        )

    def clean_automatic_contributor_completion_policy(self):
        return (
            self.cleaned_data.get("automatic_contributor_completion_policy")
            or self.instance.automatic_contributor_completion_policy
        )

    class Meta:
        model = ExaminationCycle
        fields = [
            "processing_mode",
            "automatic_campus_contribution_policy",
            "automatic_contributor_completion_policy",
            "default_questions_required_per_faculty",
            "default_final_item_count",
            "default_contribution_deadline",
            "default_coverage",
            "contributor_instructions",
        ]
        widgets = {
            "default_questions_required_per_faculty": forms.NumberInput(attrs={"class": "form-control"}),
            "default_final_item_count": forms.NumberInput(attrs={"class": "form-control"}),
            "default_contribution_deadline": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local", "class": "form-control"},
            ),
            "contributor_instructions": forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
            "default_coverage": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["processing_mode"].required = False
        self.fields["processing_mode"].widget.attrs.setdefault("class", "form-select")
        self.fields["automatic_campus_contribution_policy"].widget.attrs.setdefault(
            "class", "form-select"
        )
        self.fields["automatic_campus_contribution_policy"].required = False
        self.fields["automatic_contributor_completion_policy"].widget.attrs.setdefault(
            "class", "form-select"
        )
        self.fields["automatic_contributor_completion_policy"].required = False
        if self.instance.status != ExaminationCycle.Status.DRAFT:
            self.fields["automatic_campus_contribution_policy"].disabled = True
            self.fields["automatic_contributor_completion_policy"].disabled = True
        self.fields["default_contribution_deadline"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["expected_updated_at"].widget.attrs["class"] = "d-none"
        self.fields["reason"].widget.attrs["class"] = "form-control"

    def clean_contributor_instructions(self):
        return (self.cleaned_data.get("contributor_instructions") or "").strip()

    def clean_default_coverage(self):
        return (self.cleaned_data.get("default_coverage") or "").strip()

    def clean_expected_updated_at(self):
        value = self.cleaned_data["expected_updated_at"]
        if parse_datetime(value) is None:
            raise forms.ValidationError(_STALE_FORM_STATE_ERROR)
        return value

    def clean_default_contribution_deadline(self):
        return normalize_contribution_deadline_to_minute(
            self.cleaned_data.get("default_contribution_deadline")
        )

    def clean_reason(self):
        return (self.cleaned_data.get("reason") or "").strip()

    def clean(self):
        cleaned = super().clean()
        for field in ("default_questions_required_per_faculty", "default_final_item_count"):
            value = cleaned.get(field)
            if value is not None and not 50 <= value <= 75:
                self.add_error(field, "Value must be from 50 to 75.")
        return cleaned


class CycleDefaultsConfirmationForm(forms.Form):
    # This is the only field accepted by the confirmation POST. It carries a
    # signed, actor-bound snapshot of every authoritative value; ordinary
    # hidden inputs must never be trusted for a confirmed cycle update.
    confirmation_state = forms.CharField(widget=forms.HiddenInput)


class _CycleTransitionForm(forms.Form):
    expected_updated_at = forms.CharField(widget=forms.HiddenInput)


class ExaminationCycleOpenForm(_CycleTransitionForm):
    pass


class ExaminationCycleCloseForm(_CycleTransitionForm):
    pass


class PrepareFacultyContributionsForm(_CycleTransitionForm):
    pass


class CourseExamConfigurationForm(forms.ModelForm):
    expected_revision = forms.IntegerField(
        widget=forms.HiddenInput,
        error_messages={
            "required": _STALE_FORM_STATE_ERROR,
            "invalid": _STALE_FORM_STATE_ERROR,
        },
    )
    questions_required_per_faculty_mode = forms.ChoiceField(choices=[("DEFAULT", "Use cycle default"), ("OVERRIDE", "Use course override")])
    final_item_count_mode = forms.ChoiceField(choices=[("DEFAULT", "Use cycle default"), ("OVERRIDE", "Use course override")])
    contribution_deadline_mode = forms.ChoiceField(required=False, choices=[("DEFAULT", "Use cycle default"), ("OVERRIDE", "Course override")])
    coverage_mode = forms.ChoiceField(required=False, choices=[("DEFAULT", "Use cycle default"), ("OVERRIDE", "Course override")])

    def __init__(self, *args, cycle=None, **kwargs):
        self.cycle = cycle
        super().__init__(*args, **kwargs)
        self.fields["contribution_deadline"].input_formats = ["%Y-%m-%dT%H:%M"]
        for name in ("questions_required_per_faculty_mode", "final_item_count_mode", "contribution_deadline_mode", "coverage_mode"):
            self.fields[name].widget.attrs["class"] = "form-select"

    class Meta:
        model = CourseExamConfiguration
        fields = [
            "final_item_count", "questions_required_per_faculty", "coverage",
            "additional_instructions", "contribution_deadline", "contribution_deadline_source", "final_item_count_source",
            "questions_required_per_faculty_source", "cycle_defaults_revision_snapshot",
        ]
        widgets = {
            "final_item_count": forms.NumberInput(attrs={"class": "form-control"}),
            "questions_required_per_faculty": forms.NumberInput(attrs={"class": "form-control"}),
            "coverage": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "additional_instructions": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "contribution_deadline": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local", "class": "form-control"}),
            "contribution_deadline_source": forms.HiddenInput(),
            "final_item_count_source": forms.HiddenInput(),
            "questions_required_per_faculty_source": forms.HiddenInput(),
            "cycle_defaults_revision_snapshot": forms.HiddenInput(),
        }

    def clean_coverage(self):
        return (self.cleaned_data.get("coverage") or "").strip()

    def clean_additional_instructions(self):
        return (self.cleaned_data.get("additional_instructions") or "").strip()

    def clean_coverage_mode(self):
        return self.cleaned_data.get("coverage_mode") or None

    def clean_contribution_deadline(self):
        return normalize_contribution_deadline_to_minute(
            self.cleaned_data.get("contribution_deadline")
        )

    def clean(self):
        cleaned = super().clean()
        submitted_coverage = (cleaned.get("coverage") or "").strip()
        coverage_mode = cleaned.get("coverage_mode")
        existing_coverage = (self.instance.coverage or "").strip()
        existing_source = self.instance.coverage_source
        if coverage_mode == CourseExamConfiguration.ValueSource.DEFAULT:
            effective_coverage = (
                (self.cycle.default_coverage or "").strip() if self.cycle else ""
            )
            cleaned["coverage"] = effective_coverage
            self.instance.coverage_source = (
                CourseExamConfiguration.ValueSource.DEFAULT
                if effective_coverage
                else None
            )
        elif coverage_mode == CourseExamConfiguration.ValueSource.OVERRIDE:
            self.instance.coverage_source = (
                CourseExamConfiguration.ValueSource.OVERRIDE
                if submitted_coverage
                else None
            )
        elif not submitted_coverage:
            self.instance.coverage_source = None
        elif (
            submitted_coverage == existing_coverage
            and existing_source in CourseExamConfiguration.ValueSource.values
        ):
            self.instance.coverage_source = existing_source
        else:
            self.instance.coverage_source = CourseExamConfiguration.ValueSource.OVERRIDE
        for field in ("final_item_count", "questions_required_per_faculty"):
            value = cleaned.get(field)
            mode = cleaned.get(f"{field}_mode")
            if mode == "OVERRIDE" and (value is None or not 50 <= value <= 75):
                self.add_error(field, "A course override must be from 50 to 75.")
            if mode == "DEFAULT" and value is not None and not 50 <= value <= 75:
                self.add_error(field, "Value must be from 50 to 75.")
        if self.cycle:
            pairs = (
                ("questions_required_per_faculty", "questions_required_per_faculty_mode", "questions_required_per_faculty_source", self.cycle.default_questions_required_per_faculty),
                ("final_item_count", "final_item_count_mode", "final_item_count_source", self.cycle.default_final_item_count),
            )
            for field, mode_field, source_field, default in pairs:
                if cleaned.get(mode_field) == "DEFAULT":
                    if default is None or not 50 <= default <= 75:
                        self.add_error(field, "A valid cycle default is required.")
                    else:
                        cleaned[field] = default
                        cleaned[source_field] = "DEFAULT"
                elif cleaned.get(mode_field) == "OVERRIDE":
                    cleaned[source_field] = "OVERRIDE"
            cleaned["cycle_defaults_revision_snapshot"] = self.cycle.defaults_revision

        deadline_mode_was_submitted = (
            self.is_bound and "contribution_deadline_mode" in self.data
        )
        deadline_mode = cleaned.get("contribution_deadline_mode")
        if not deadline_mode_was_submitted:
            cleaned["contribution_deadline_mode"] = None
            existing_source = getattr(
                self.instance, "contribution_deadline_source", None
            )
            existing_deadline = getattr(
                self.instance, "contribution_deadline", None
            )
            if existing_source in CourseExamConfiguration.ValueSource.values:
                cleaned["contribution_deadline_source"] = existing_source
                if cleaned.get("contribution_deadline") is None:
                    cleaned["contribution_deadline"] = existing_deadline
            elif cleaned.get("contribution_deadline") is not None:
                cleaned["contribution_deadline_source"] = (
                    CourseExamConfiguration.ValueSource.OVERRIDE
                )
            else:
                cleaned["contribution_deadline_source"] = None
        elif deadline_mode not in CourseExamConfiguration.ValueSource.values:
            if "contribution_deadline_mode" not in self.errors:
                self.add_error(
                    "contribution_deadline_mode",
                    "Select a supported contribution deadline mode.",
                )
        elif deadline_mode == CourseExamConfiguration.ValueSource.DEFAULT:
            if self.cycle is None:
                self.add_error(
                    "contribution_deadline_mode",
                    "The cycle default contribution deadline cannot be resolved without a cycle.",
                )
            else:
                cleaned["contribution_deadline"] = (
                    self.cycle.default_contribution_deadline
                )
                cleaned["contribution_deadline_source"] = (
                    CourseExamConfiguration.ValueSource.DEFAULT
                    if self.cycle.default_contribution_deadline is not None
                    else None
                )
        elif (
            deadline_mode == CourseExamConfiguration.ValueSource.OVERRIDE
            and cleaned.get("contribution_deadline") is None
        ):
            self.add_error(
                "contribution_deadline",
                "A course override requires a contribution deadline.",
            )
        elif deadline_mode == CourseExamConfiguration.ValueSource.OVERRIDE:
            cleaned["contribution_deadline_source"] = (
                CourseExamConfiguration.ValueSource.OVERRIDE
            )
        return cleaned


class _ConfigurationActionForm(forms.Form):
    expected_revision = forms.IntegerField(widget=forms.HiddenInput)


class CourseOverrideRemovalForm(_ConfigurationActionForm):
    return_questions_required_per_faculty = forms.BooleanField(required=False)
    return_final_item_count = forms.BooleanField(required=False)
    return_contribution_deadline = forms.BooleanField(required=False)

    def clean(self):
        cleaned = super().clean()
        if not (
            cleaned.get("return_questions_required_per_faculty")
            or cleaned.get("return_final_item_count")
            or cleaned.get("return_contribution_deadline")
        ):
            raise forms.ValidationError("Select at least one override to return to the cycle default.")
        return cleaned


class CourseContributionOpenForm(_ConfigurationActionForm):
    pass


class CourseContributionReopenForm(_ConfigurationActionForm):
    pass


class AutomaticContributionReopenForm(_ConfigurationActionForm):
    new_deadline = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "form-control"},
        ),
        help_text="Set a new Asia/Manila deadline for unfinished Draft contributions.",
    )

    def clean_new_deadline(self):
        return normalize_contribution_deadline_to_minute(
            self.cleaned_data.get("new_deadline")
        )


class QuestionnairePrintReleaseForm(forms.Form):
    cycle_course_id = forms.IntegerField(widget=forms.HiddenInput)
    generation_revision = forms.ModelChoiceField(
        queryset=ExamGenerationRevision.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    print_from = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "form-control"},
        ),
    )
    print_until = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "form-control"},
        ),
    )

    def __init__(self, *args, cycle_course=None, **kwargs):
        super().__init__(*args, **kwargs)
        if cycle_course is not None:
            self.fields["generation_revision"].queryset = (
                ExamGenerationRevision.objects.filter(cycle_course=cycle_course)
                .order_by("-revision_number")
            )
            self.fields["generation_revision"].label_from_instance = (
                lambda revision: (
                    f"R{revision.revision_number} — {revision.get_status_display()}"
                )
            )

    def clean(self):
        cleaned = super().clean()
        print_from = cleaned.get("print_from")
        print_until = cleaned.get("print_until")
        if print_from and print_until and print_until <= print_from:
            self.add_error(
                "print_until",
                "Print Until must be later than Print From.",
            )
        return cleaned


class BulkQuestionnairePrintReleaseForm(forms.Form):
    selections = forms.MultipleChoiceField(
        choices=(),
        error_messages={
            "required": "Select at least one generated course revision.",
            "invalid_choice": "One or more selected revisions are unavailable.",
        },
    )
    print_from = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "form-control"},
        ),
    )
    print_until = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "form-control"},
        ),
    )

    def __init__(self, *args, selection_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selections"].choices = tuple(selection_choices)

    def clean_selections(self):
        selections = []
        course_ids = set()
        for value in self.cleaned_data["selections"]:
            try:
                course_id, revision_id = (int(part) for part in value.split(":", 1))
            except (TypeError, ValueError) as exc:
                raise forms.ValidationError(
                    "One or more selected revisions are invalid."
                ) from exc
            if course_id in course_ids:
                raise forms.ValidationError(
                    "Select only one revision for each course examination."
                )
            course_ids.add(course_id)
            selections.append((course_id, revision_id))
        return tuple(selections)

    def clean(self):
        cleaned = super().clean()
        print_from = cleaned.get("print_from")
        print_until = cleaned.get("print_until")
        if print_from and print_until and print_until <= print_from:
            self.add_error(
                "print_until",
                "Print Until must be later than Print From.",
            )
        return cleaned


class BulkAnswerKeyReleaseForm(forms.Form):
    CONFIRMATION_TEXT = (
        "I confirm that ALL examination sessions for ALL selected courses "
        "have concluded."
    )

    selections = forms.MultipleChoiceField(
        choices=(),
        error_messages={
            "required": "Select at least one current final course revision.",
            "invalid_choice": "One or more selected revisions are unavailable or stale.",
        },
    )
    available_from = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "form-control"},
        ),
    )
    available_until = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "form-control"},
        ),
    )
    sessions_concluded = forms.BooleanField(
        required=True,
        label=CONFIRMATION_TEXT,
        error_messages={
            "required": (
                "Confirm that all examination sessions for all selected courses "
                "have concluded."
            )
        },
    )

    def __init__(self, *args, selection_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selections"].choices = tuple(selection_choices)

    def clean_selections(self):
        selections = []
        course_ids = set()
        for value in self.cleaned_data["selections"]:
            try:
                course_id, revision_id = (int(part) for part in value.split(":", 1))
            except (TypeError, ValueError) as exc:
                raise forms.ValidationError(
                    "One or more selected revisions are invalid."
                ) from exc
            if course_id in course_ids:
                raise forms.ValidationError(
                    "Select only one revision for each course examination."
                )
            course_ids.add(course_id)
            selections.append((course_id, revision_id))
        return tuple(selections)

    def clean(self):
        cleaned = super().clean()
        available_from = cleaned.get("available_from")
        available_until = cleaned.get("available_until")
        if (
            available_from
            and available_until
            and available_until <= available_from
        ):
            self.add_error(
                "available_until",
                "Available Until must be later than Available From.",
            )
        return cleaned


class AnswerKeyReleaseForm(forms.Form):
    CONFIRMATION_TEXT = (
        "Confirm that all examination sessions for this course have concluded "
        "before releasing the Answer Key to faculty."
    )

    cycle_course_id = forms.IntegerField(widget=forms.HiddenInput)
    generation_revision = forms.ModelChoiceField(
        queryset=ExamGenerationRevision.objects.none(),
        empty_label=None,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    available_from = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "form-control"},
        ),
    )
    available_until = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local", "class": "form-control"},
        ),
    )
    sessions_concluded = forms.BooleanField(
        required=True,
        label=CONFIRMATION_TEXT,
        error_messages={
            "required": "Confirm that all examination sessions have concluded."
        },
    )

    def __init__(self, *args, cycle_course=None, **kwargs):
        super().__init__(*args, **kwargs)
        if cycle_course is not None:
            queryset = ExamGenerationRevision.objects.filter(
                cycle_course=cycle_course,
                current_marker=1,
            )
            if (
                cycle_course.cycle.processing_mode
                == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            ):
                queryset = queryset.filter(status=ExamGenerationRevision.Status.GENERATED)
            else:
                queryset = queryset.filter(status=ExamGenerationRevision.Status.LOCKED)
            self.fields["generation_revision"].queryset = queryset.order_by(
                "-revision_number"
            )
            self.fields["generation_revision"].label_from_instance = (
                lambda revision: (
                    f"R{revision.revision_number} — {revision.get_status_display()}"
                )
            )

    def clean(self):
        cleaned = super().clean()
        available_from = cleaned.get("available_from")
        available_until = cleaned.get("available_until")
        if (
            available_from
            and available_until
            and available_until <= available_from
        ):
            self.add_error(
                "available_until",
                "Available Until must be later than Available From.",
            )
        return cleaned


class _ReasonedConfigurationActionForm(_ConfigurationActionForm):
    reason = forms.CharField(min_length=10, max_length=500, widget=forms.Textarea(attrs={"rows": 3}))

    def clean_reason(self):
        return (self.cleaned_data["reason"] or "").strip()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["class"] = "form-control"


class CourseContributionCloseForm(_ReasonedConfigurationActionForm):
    expected_roster_revision = forms.IntegerField(widget=forms.HiddenInput)


class CourseExamConfigurationRevertForm(_ReasonedConfigurationActionForm):
    pass
