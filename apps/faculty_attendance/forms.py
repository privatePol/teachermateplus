import json

from django import forms

from apps.accounts.models import User
from apps.academics.models import AcademicYear, CourseOffering, Term
from apps.tenants.models import Department

from .models import AttendanceClosureDecision, CheckingRound, DTRAdjustment, SavedCheckerRoute, ScheduleSlot, TeachingMeeting
from .monthly_checklists import DAY_GROUPS, WEEKDAY_NAMES, day_group_for_weekdays


class MonthlyChecklistForm(forms.Form):
    paper = forms.ChoiceField(required=False, initial="A4", choices=[(p, p) for p in ("A4", "Letter", "Legal", "Long Bond")], label="Paper size")
    orientation = forms.ChoiceField(required=False, initial="landscape", choices=[("portrait", "Portrait"), ("landscape", "Landscape")])
    text_size = forms.ChoiceField(required=False, initial="11", choices=[(s, f"{s} pt") for s in ("11", "12", "14")], label="Print text size")
    academic_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none(), label="Academic year")
    term = forms.ModelChoiceField(queryset=Term.objects.none(), label="Semester")
    month = forms.DateField(
        input_formats=["%Y-%m"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month"}),
    )
    weekdays = forms.TypedMultipleChoiceField(
        choices=list(enumerate(WEEKDAY_NAMES)), coerce=int,
        widget=forms.CheckboxSelectMultiple, label="Checklist days",
        error_messages={"required": "Select at least one checklist day."},
    )

    def __init__(self, *args, academic_year_queryset=None, term_queryset=None, **kwargs):
        if args and args[0] is not None:
            data = args[0].copy()
            for key, value in (("paper", "A4"), ("orientation", "landscape"), ("text_size", "11")):
                data.setdefault(key, value)
            if "weekdays" not in data and data.get("day_group") in DAY_GROUPS:
                days = [str(day) for day in DAY_GROUPS[data["day_group"]][0]]
                if hasattr(data, "setlist"):
                    data.setlist("weekdays", days)
                else:
                    data["weekdays"] = days
            args = (data, *args[1:])
        kwargs.setdefault("initial", {}).setdefault("weekdays", [0, 2])
        super().__init__(*args, **kwargs)
        self.fields["academic_year"].queryset = academic_year_queryset or AcademicYear.objects.none()
        self.fields["term"].queryset = term_queryset or Term.objects.none()
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs.setdefault("class", "form-check-input")
            else:
                field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, forms.Select) else "form-control")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("weekdays"):
            cleaned["day_group"] = day_group_for_weekdays(cleaned["weekdays"])
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        if academic_year and term and term.academic_year_id != academic_year.pk:
            self.add_error("term", "Select a semester within the chosen academic year.")
        return cleaned


class MonthlyArrangementForm(forms.Form):
    academic_year_id = forms.IntegerField(min_value=1)
    term_id = forms.IntegerField(min_value=1)
    month = forms.CharField(max_length=7)
    day_group = forms.ChoiceField(choices=[(key, label) for key, (_, label) in DAY_GROUPS.items()])
    expected_revision = forms.IntegerField(required=False, min_value=1)
    row_tokens = forms.CharField(required=False)

    def clean_row_tokens(self):
        raw = self.cleaned_data["row_tokens"]
        if raw.startswith("["):
            try:
                tokens = json.loads(raw)
            except ValueError:
                raise forms.ValidationError("Arrangement contains invalid class rows.")
            if not isinstance(tokens, list) or any(not isinstance(value, str) for value in tokens):
                raise forms.ValidationError("Arrangement contains invalid class rows.")
        else:
            tokens = [value for value in raw.split(",") if value]
        if len(tokens) != len(set(tokens)):
            raise forms.ValidationError("Arrangement contains duplicate class rows.")
        return tokens


class RecurringCombinedClassForm(forms.Form):
    academic_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none(), label="Academic year")
    term = forms.ModelChoiceField(queryset=Term.objects.none(), label="Semester")
    offerings = forms.ModelMultipleChoiceField(queryset=CourseOffering.objects.none(), label="Sections taught together")
    weekday = forms.TypedChoiceField(
        choices=[(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")], coerce=int,
    )
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    end_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    effective_from = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    effective_until = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Confirmation note (optional)")

    def __init__(self, *args, academic_year_queryset=None, term_queryset=None, offering_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_year"].queryset = academic_year_queryset or AcademicYear.objects.none()
        self.fields["term"].queryset = term_queryset or Term.objects.none()
        self.fields["offerings"].queryset = offering_queryset or CourseOffering.objects.none()
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, forms.Select) else "form-control")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("term") and cleaned.get("academic_year") and cleaned["term"].academic_year_id != cleaned["academic_year"].pk:
            self.add_error("term", "Select a semester within the chosen academic year.")
        if cleaned.get("start_time") and cleaned.get("end_time") and cleaned["end_time"] <= cleaned["start_time"]:
            self.add_error("end_time", "End time must be later than start time.")
        if cleaned.get("effective_from") and cleaned.get("effective_until") and cleaned["effective_until"] < cleaned["effective_from"]:
            self.add_error("effective_until", "End date cannot precede start date.")
        if cleaned.get("offerings") is not None and len(cleaned["offerings"]) < 2:
            self.add_error("offerings", "Select at least two offerings.")
        return cleaned


class ChecklistPreparationForm(forms.Form):
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    route = forms.ModelChoiceField(queryset=SavedCheckerRoute.objects.none(), required=False)

    def __init__(self, *args, route_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["route"].queryset = route_queryset or SavedCheckerRoute.objects.none()

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date") or start
        if start and end and end < start:
            self.add_error("end_date", "End date cannot precede start date.")
        cleaned["end_date"] = end
        return cleaned


class CreateCheckingRoundForm(ChecklistPreparationForm):
    label = forms.CharField(required=False, max_length=120)
    meeting_ids = forms.CharField(widget=forms.HiddenInput)

    def clean_meeting_ids(self):
        raw = self.cleaned_data["meeting_ids"]
        try:
            values = [int(value) for value in raw.split(",") if value.strip()]
        except ValueError as exc:
            raise forms.ValidationError("Meeting selection is invalid.") from exc
        if not values or len(values) != len(set(values)):
            raise forms.ValidationError("Select unique meetings for the checking round.")
        return values


class SavedRouteForm(forms.Form):
    name = forms.CharField(max_length=120)
    expected_revision = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    schedule_slot_ids = forms.CharField(widget=forms.HiddenInput)

    def clean_schedule_slot_ids(self):
        try:
            values = [int(value) for value in self.cleaned_data["schedule_slot_ids"].split(",") if value.strip()]
        except ValueError as exc:
            raise forms.ValidationError("Route order is invalid.") from exc
        if len(values) != len(set(values)):
            raise forms.ValidationError("Route order contains duplicate entries.")
        return values


class ScheduleCorrectionForm(forms.Form):
    offering = forms.ModelChoiceField(queryset=CourseOffering.objects.none())
    effective_from = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    weekday = forms.TypedChoiceField(
        choices=[(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"), (4, "Friday"), (5, "Saturday"), (6, "Sunday")],
        coerce=int,
    )
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    end_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    building = forms.CharField(required=False, max_length=120)
    floor = forms.CharField(required=False, max_length=80)
    room = forms.CharField(required=False, max_length=120)
    correction_reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Correction note (optional)")

    def __init__(self, *args, offering_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["offering"].queryset = offering_queryset or CourseOffering.objects.none()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("start_time") and cleaned.get("end_time") and cleaned["end_time"] <= cleaned["start_time"]:
            self.add_error("end_time", "End time must be later than start time.")
        return cleaned


class CoverageForm(forms.Form):
    offering = forms.ModelChoiceField(queryset=CourseOffering.objects.none())
    faculty_user = forms.ModelChoiceField(queryset=User.objects.none())
    effective_from = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    effective_until = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Coverage note (optional)")

    def __init__(self, *args, offering_queryset=None, faculty_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["offering"].queryset = offering_queryset or CourseOffering.objects.none()
        self.fields["faculty_user"].queryset = faculty_queryset or User.objects.none()


class MeetingGenerationForm(forms.Form):
    schedule_slot = forms.ModelChoiceField(queryset=ScheduleSlot.objects.none())
    meeting_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    combined_offerings = forms.ModelMultipleChoiceField(queryset=CourseOffering.objects.none(), required=False)

    def __init__(self, *args, slot_queryset=None, offering_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["schedule_slot"].queryset = slot_queryset or ScheduleSlot.objects.none()
        self.fields["combined_offerings"].queryset = offering_queryset or CourseOffering.objects.none()


class SubstitutionForm(forms.Form):
    meeting = forms.ModelChoiceField(queryset=TeachingMeeting.objects.none())
    substitute_faculty = forms.ModelChoiceField(queryset=User.objects.none())
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Substitution note (optional)")

    def __init__(self, *args, meeting_queryset=None, faculty_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["meeting"].queryset = meeting_queryset or TeachingMeeting.objects.none()
        self.fields["substitute_faculty"].queryset = faculty_queryset or User.objects.none()


class ExceptionEncodingForm(forms.Form):
    meeting_id = forms.IntegerField(widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    absence_code = forms.ChoiceField(choices=[("", "No absence"), ("A", "A - without notice"), ("N", "N - with notice")], required=False)
    missed_hours = forms.DecimalField(required=False, min_value=0, max_digits=7, decimal_places=2)
    late_flag = forms.BooleanField(required=False)
    late_minutes = forms.IntegerField(required=False, min_value=0)
    early_flag = forms.BooleanField(required=False)
    early_minutes = forms.IntegerField(required=False, min_value=0)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Checker reason (optional)")

    def __init__(self, *args, preserve_legacy_period_absence=False, correcting_saved_result=False, saved_absence=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.preserve_legacy_period_absence = preserve_legacy_period_absence
        self.removable_saved_absence = saved_absence and not preserve_legacy_period_absence
        # A saved hours-based finding can be removed explicitly. Ignore its old
        # hours before DecimalField validation, including a stale invalid value.
        # Initial blank submissions and legacy-period compatibility stay strict.
        if self.is_bound and correcting_saved_result and not preserve_legacy_period_absence and not self.data.get("absence_code"):
            self.data = self.data.copy()
            self.data["missed_hours"] = ""

    def clean(self):
        cleaned = super().clean()
        code = cleaned.get("absence_code")
        hours = cleaned.get("missed_hours")
        if self.data.get("missed_periods") not in (None, ""):
            self.add_error(None, "Missed periods are no longer accepted. Enter actual missed decimal hours.")
        if self.preserve_legacy_period_absence:
            if code or hours is not None:
                self.add_error(None, "The saved period-based absence is preserved unchanged; do not replace it in this card.")
        elif code and hours is None:
            self.add_error("missed_hours", "A/N requires actual missed decimal hours.")
        elif not code and hours is not None:
            self.add_error("absence_code", "Select A or N for an absence segment.")
        if self.removable_saved_absence and not code and not (cleaned.get("reason") or "").strip():
            self.add_error("reason", "Enter a checker reason to remove the saved absence.")
        if cleaned.get("late_flag") and cleaned.get("late_minutes") is None:
            self.add_error("late_minutes", "Enter late minutes; zero is allowed.")
        if cleaned.get("early_flag") and cleaned.get("early_minutes") is None:
            self.add_error("early_minutes", "Enter early-dismissal minutes; zero is allowed.")
        return cleaned


class ReconciliationForm(forms.Form):
    decision = forms.ChoiceField(choices=(("KEEP_SNAPSHOT", "Keep historical snapshot"), ("REVISE_FUTURE", "Use for future processing"), ("MANUAL_CORRECTION", "Manual correction required")))
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Resolution note (optional)")


class CoverageReconciliationForm(forms.Form):
    effective_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Resolution note (optional)")


class CoverageAdoptionForm(forms.Form):
    meeting_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    confirmed = forms.BooleanField(label="I confirm the verified candidate is the assigned faculty for this dated class.")
    reason = forms.CharField(label="Recovery reason", widget=forms.Textarea(attrs={"rows": 3}))

    def clean_confirmed(self):
        if self.data.get("confirmed") != "on":
            raise forms.ValidationError("Confirm adoption of verified coverage before applying.")
        return self.cleaned_data["confirmed"]


class FacultyAttendanceFilterForm(forms.Form):
    start_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))


class DailyEncodingForm(forms.Form):
    academic_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none(), widget=forms.HiddenInput)
    term = forms.ModelChoiceField(queryset=Term.objects.none(), widget=forms.HiddenInput)
    meeting_date = forms.DateField(label="Attendance date", widget=forms.DateInput(attrs={"type": "date"}))
    route = forms.ModelChoiceField(queryset=SavedCheckerRoute.objects.none(), required=False, label="Saved classroom route")

    def __init__(self, *args, academic_year_queryset=None, term_queryset=None, route_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_year"].queryset = academic_year_queryset or AcademicYear.objects.none()
        self.fields["term"].queryset = term_queryset or Term.objects.none()
        self.fields["route"].queryset = route_queryset or SavedCheckerRoute.objects.none()
        self.fields["meeting_date"].widget.attrs.setdefault("class", "form-control")
        self.fields["route"].widget.attrs.setdefault("class", "form-select")

    def clean(self):
        cleaned = super().clean()
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        meeting_date = cleaned.get("meeting_date")
        if academic_year and term and term.academic_year_id != academic_year.pk:
            self.add_error("term", "Select a semester within the chosen academic year.")
        if term and meeting_date and not (term.start_date <= meeting_date <= term.end_date):
            self.add_error("meeting_date", "Choose a date within the selected semester.")
        return cleaned


class CutoffScopeForm(forms.Form):
    academic_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none(), label="Academic year")
    term = forms.ModelChoiceField(queryset=Term.objects.none(), label="Semester")
    start_date = forms.DateField(label="Date from", widget=forms.DateInput(attrs={"type": "date"}))
    end_date = forms.DateField(label="Date to", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, academic_year_queryset=None, term_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["academic_year"].queryset = academic_year_queryset or AcademicYear.objects.none()
        self.fields["term"].queryset = term_queryset or Term.objects.none()
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, forms.Select) else "form-control")

    def clean(self):
        cleaned = super().clean()
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        start_date = cleaned.get("start_date")
        end_date = cleaned.get("end_date")
        if academic_year and term and term.academic_year_id != academic_year.pk:
            self.add_error("term", "Select a semester within the chosen academic year.")
        if start_date and end_date and end_date < start_date:
            self.add_error("end_date", "Date to cannot precede Date from.")
        return cleaned


class CutoffPublicationForm(CutoffScopeForm):
    review_fingerprint = forms.CharField(widget=forms.HiddenInput)
    submission_key = forms.CharField(max_length=64, widget=forms.HiddenInput)
    publication_reason = forms.CharField(required=False, label="Publication note (optional)", widget=forms.Textarea(attrs={"rows": 3}))


class FacultyCutoffActionForm(forms.Form):
    faculty_ids = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    expected_fingerprints = forms.JSONField(widget=forms.HiddenInput)
    submission_key = forms.CharField(max_length=40, widget=forms.HiddenInput)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Note (optional)")
    faculty_review_complete = forms.BooleanField(required=False,
        label="Published attendance was available for faculty review; checker concerns were settled")

    def __init__(self, *args, faculty_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["faculty_ids"].choices = faculty_choices

    def clean_expected_fingerprints(self):
        values = self.cleaned_data["expected_fingerprints"]
        if not isinstance(values, dict) or any(not isinstance(v, str) or len(v) != 64 for v in values.values()):
            raise forms.ValidationError("Reload the faculty cutoff review before submitting.")
        return values


class SourceChangeReconciliationForm(forms.Form):
    effective_from = forms.DateField(label="Effective date", widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(required=False, label="Resolution note (optional)", widget=forms.Textarea(attrs={"rows": 3}))


class DTRAdjustmentForm(forms.Form):
    entry_date = forms.DateField(label="Date", widget=forms.DateInput(attrs={"type": "date"}))
    department = forms.ModelChoiceField(queryset=Department.objects.none(), label="Department")
    kind = forms.ChoiceField(choices=DTRAdjustment.Kind.choices, label="Entry type")
    hours = forms.DecimalField(max_digits=8, decimal_places=2, min_value=0, label="Decimal hours")
    leave_type = forms.ChoiceField(choices=(("", "No leave"), *DTRAdjustment.LeaveType.choices), required=False)
    offset_kind = forms.ChoiceField(choices=(("", "Choose deduction"), *DTRAdjustment.Offset.choices), required=False, label="Deduction offset")
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Checker reason (optional)")
    previous_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, required=False, widget=forms.HiddenInput)

    def __init__(self, *args, department_queryset=None, cutoff_start_date=None, cutoff_end_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department"].queryset = department_queryset or Department.objects.none()
        self.cutoff_start_date = cutoff_start_date
        self.cutoff_end_date = cutoff_end_date
        if cutoff_start_date and cutoff_end_date:
            self.fields["entry_date"].help_text = (
                f"Allowed cutoff date: {cutoff_start_date.isoformat()} to {cutoff_end_date.isoformat()}."
            )
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, forms.Select) else "form-control")

    def clean_entry_date(self):
        entry_date = self.cleaned_data["entry_date"]
        if self.cutoff_start_date and self.cutoff_end_date and not (
            self.cutoff_start_date <= entry_date <= self.cutoff_end_date
        ):
            raise forms.ValidationError(
                "Date must be within this cutoff: "
                f"{self.cutoff_start_date.isoformat()} to {self.cutoff_end_date.isoformat()}."
            )
        return entry_date

    def clean(self):
        values = super().clean()
        if values.get("kind") == DTRAdjustment.Kind.LEAVE:
            if not values.get("leave_type") or not values.get("offset_kind"):
                self.add_error("leave_type", "Choose VL/SL/EL and the deduction this credit offsets.")
        elif values.get("leave_type") or values.get("offset_kind"):
            self.add_error("leave_type", "Only leave credit uses these fields.")
        return values


class DTRAdjustmentRemovalForm(forms.Form):
    entry_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    reason = forms.CharField(
        required=False,
        label="Removal note (optional)",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    confirm_removal = forms.BooleanField(
        label="I confirm removal of this active checker entry",
        error_messages={"required": "Confirm removal before saving the zero-hour reversal."},
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs.setdefault("class", "form-control")


class DTREarlyCorrectionForm(forms.Form):
    result_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    minutes = forms.IntegerField(min_value=0, label="Correct early-dismissal minutes")
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Correction note (optional)")


class DTRFinalizationForm(forms.Form):
    expected_fingerprint = forms.CharField(widget=forms.HiddenInput)
    faculty_review_complete = forms.BooleanField(
        label="Published attendance was available for faculty review; checker concerns were settled"
    )
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Finalization note (optional)")


class AttendanceClosureForm(forms.Form):
    meeting_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    status = forms.ChoiceField(choices=AttendanceClosureDecision.Status.choices, label="Decision")
    kind = forms.ChoiceField(choices=AttendanceClosureDecision.Kind.choices, label="Closure type")
    pay_basis = forms.ChoiceField(choices=AttendanceClosureDecision.PayBasis.choices, label="Verified pay basis")
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Checker note (optional)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("class", "form-select" if isinstance(field.widget, forms.Select) else "form-control")


class DTRMixedFindingForm(forms.Form):
    meeting_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    intervals = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}), label="Actual missed intervals",
        help_text="One non-overlapping span per line, such as A 08:00-08:30 or L 08:30-08:40. Omit a fully overlapped deduction.",
    )
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Checker note (optional)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.HiddenInput):
                field.widget.attrs.setdefault("class", "form-control")
