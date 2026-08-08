from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from django import forms
from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.utils import timezone

from apps.academics.models import (
    AcademicYear,
    Course,
    CourseOffering,
    FacultyAssignment,
    FacultyAssignmentReplacementLog,
    Section,
    TenantTermGradingPeriod,
    Term,
)
from apps.academics.services import FacultyAssignmentSafetyService
from apps.admin_portal.course_exam_department import configure_exam_department_field
from apps.core.services.settings import SystemSettingService
from apps.core.services.features import FeatureSettingsService
from apps.enrollment.services import EnrollmentService
from apps.imports.services import BulkImportService
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CorrectionApprovalRouteStep,
    CorrectionPetitionWindowPolicy,
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    GradeEncodingControl,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    TemplateHotfixRequest,
    TenantGradingProfile,
)
from apps.grading.services import (
    CourseOfferingSafetyService,
    CourseTemplateAssignmentSafetyService,
    FacultyGradingService,
    GradingGovernanceService,
)
from apps.navigation.models import MenuGroup, MenuItem
from apps.rbac.models import Permission, Role, UserRole
from apps.students.models import Student
from apps.student_portal.models import StudentAccountLink
from apps.tenants.models import Campus, Department, Program, Tenant


def _normalize_correction_policy_period_key(period) -> str:
    if not period:
        return ""
    # Correction Governance has exact aliases.  Do not use the general
    # academic substring classifier here: custom periods such as
    # MIDTERM-REMEDIAL retain their own policy identity.
    return GradingGovernanceService.canonical_correction_period_key(period)


User = get_user_model()


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


def _set_choice_label(field, formatter):
    if field is not None:
        field.label_from_instance = formatter


class TenantDataExportStartForm(forms.Form):
    tenant = forms.ModelChoiceField(
        queryset=Tenant.objects.none(),
        label="Tenant to export",
        empty_label="Select tenant",
    )
    acknowledgement = forms.BooleanField(
        required=True,
        label="I understand that this export contains confidential institutional data and must only be used for authorized investigation.",
    )
    password = forms.CharField(
        label="Current account password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "class": "form-control",
            }
        ),
    )

    def __init__(self, *args, tenant_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tenant"].queryset = tenant_queryset if tenant_queryset is not None else Tenant.objects.none()
        self.fields["tenant"].widget.attrs["class"] = "form-select"
        self.fields["acknowledgement"].widget.attrs["class"] = "form-check-input"


class TenantDataExportOtpForm(forms.Form):
    challenge_token = forms.UUIDField(widget=forms.HiddenInput())
    otp_code = forms.CharField(
        label="Email verification code",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "class": "form-control",
            }
        ),
    )

    def clean_otp_code(self):
        code = (self.cleaned_data.get("otp_code") or "").strip().replace(" ", "")
        if not code.isdigit():
            raise forms.ValidationError("Enter the six-digit verification code.")
        return code


class EnrollmentAdjustmentForm(forms.Form):
    academic_year = forms.ModelChoiceField(queryset=AcademicYear.objects.none(), required=True)
    term = forms.ModelChoiceField(queryset=Term.objects.none(), required=True)
    campus = forms.ModelChoiceField(queryset=Campus.objects.none(), required=True)
    source_offering = forms.ModelChoiceField(queryset=CourseOffering.objects.none(), required=True)
    destination_offering = forms.ModelChoiceField(queryset=CourseOffering.objects.none(), required=True)
    selected_students = forms.MultipleChoiceField(
        choices=(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    transfer_entire_class = forms.BooleanField(required=False)
    confirm_warning = forms.BooleanField(required=False)
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Enter the approved reason from Pinnacle or the authorized school office.",
    )

    def __init__(
        self,
        *args,
        academic_year_queryset=None,
        term_queryset=None,
        campus_queryset=None,
        offering_queryset=None,
        source_offering_queryset=None,
        destination_offering_queryset=None,
        enrollment_queryset=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.fields["academic_year"].queryset = academic_year_queryset or AcademicYear.objects.none()
        self.fields["term"].queryset = term_queryset or Term.objects.none()
        self.fields["campus"].queryset = campus_queryset or Campus.objects.none()
        default_offering_queryset = offering_queryset or CourseOffering.objects.none()
        self.fields["source_offering"].queryset = (
            source_offering_queryset if source_offering_queryset is not None else default_offering_queryset
        )
        self.fields["destination_offering"].queryset = (
            destination_offering_queryset if destination_offering_queryset is not None else default_offering_queryset
        )
        self.fields["selected_students"].choices = [
            (
                str(enrollment.student_id),
                f"{enrollment.student.student_no} - {enrollment.student.last_name}, {enrollment.student.first_name} ({enrollment.enrollment_status})",
            )
            for enrollment in (enrollment_queryset or [])
        ]
        for field_name in ("academic_year", "term", "campus", "source_offering", "destination_offering"):
            self.fields[field_name].widget.attrs.update({"class": "form-select"})
        for field_name in ("academic_year", "term", "campus"):
            self.fields[field_name].widget.attrs["data-enrollment-adjustment-filter"] = "true"
        for field_name in ("source_offering", "destination_offering"):
            self.fields[field_name].widget.attrs["data-enrollment-adjustment-offering"] = "true"
            self.fields[field_name].widget.attrs["data-placeholder"] = "---------"
            self.fields[field_name].widget.attrs["aria-describedby"] = f"id_{field_name}-status"
        self.fields["reason"].widget.attrs.update({"class": "form-control"})

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source_offering")
        destination = cleaned.get("destination_offering")
        selected_students = cleaned.get("selected_students") or []
        action = self.data.get("action") if self.is_bound else ""
        if source and destination and source.id == destination.id:
            raise DjangoValidationError("Source and destination offerings must be different.")
        if action in {"analyze", "process"} and not cleaned.get("transfer_entire_class") and not selected_students:
            raise DjangoValidationError("Select at least one student or choose Transfer Entire Class.")
        return cleaned


def _course_label(obj):
    title = (getattr(obj, "title", "") or "").strip()
    code = (getattr(obj, "code", "") or "").strip()
    if title and code:
        return f"{title} ({code})"
    return title or code or str(obj)


def _section_label(obj):
    name = (getattr(obj, "name", "") or "").strip()
    code = (getattr(obj, "code", "") or "").strip()
    if name and code and name != code:
        return f"{name} ({code})"
    return name or code or str(obj)


def _program_label(obj):
    name = (getattr(obj, "name", "") or "").strip()
    code = (getattr(obj, "code", "") or "").strip()
    if name and code and name != code:
        return f"{code} - {name}"
    return code or name or str(obj)


def _term_label(obj):
    name = (getattr(obj, "name", "") or "").strip()
    code = (getattr(obj, "code", "") or "").strip()
    academic_year = getattr(obj, "academic_year", None)
    ay_name = (getattr(academic_year, "name", "") or getattr(academic_year, "code", "") or "").strip()
    primary = name or code or str(obj)
    if ay_name:
        return f"{primary} - {ay_name}"
    return primary


def _period_label(obj):
    name = (getattr(obj, "name", "") or "").strip()
    code = (getattr(obj, "code", "") or "").strip()
    if name and code and name != code:
        return f"{name} ({code})"
    return name or code or str(obj)


def _academic_year_label(obj):
    name = (getattr(obj, "name", "") or "").strip()
    code = (getattr(obj, "code", "") or "").strip()
    if name and code and name != code:
        return f"{name} ({code})"
    return name or code or str(obj)


def _campus_label(obj):
    code = (getattr(obj, "code", "") or "").strip()
    name = (getattr(obj, "name", "") or "").strip()
    if code and name and code != name:
        return f"{code} - {name}"
    return code or name or str(obj)


def _department_with_campus_label(obj):
    campus = getattr(obj, "campus", None)
    campus_label = _campus_label(campus) if campus else ""
    code = (getattr(obj, "code", "") or "").strip()
    name = (getattr(obj, "name", "") or "").strip()
    department_label = f"{code} - {name}" if code and name else code or name or str(obj)
    return f"{campus_label} | {department_label}" if campus_label else department_label


def _offering_label(obj):
    course_label = _course_label(getattr(obj, "course", None))
    section_label = _section_label(getattr(obj, "section", None))
    return f"{course_label} | {section_label}"


def _faculty_label(obj):
    full_name = (getattr(obj, "full_name", "") or "").strip()
    username = (getattr(obj, "username", "") or "").strip()
    if full_name and username and full_name != username:
        return f"{full_name} ({username})"
    return full_name or username or str(obj)


def _student_label(obj):
    student_no = (getattr(obj, "student_no", "") or "").strip()
    last_name = (getattr(obj, "last_name", "") or "").strip()
    first_name = (getattr(obj, "first_name", "") or "").strip()
    name = ", ".join(part for part in [last_name, first_name] if part)
    return f"{student_no} - {name}" if student_no and name else student_no or name or str(obj)


def _resolve_user_default_scope_ids(user):
    default_tenant_id = getattr(user, "default_tenant_id", None)
    default_campus_id = getattr(user, "default_campus_id", None)
    default_department_id = getattr(user, "default_department_id", None)
    if default_tenant_id is None and default_campus_id:
        default_tenant_id = (
            Campus.objects.filter(id=default_campus_id).values_list("tenant_id", flat=True).first()
        )
    return default_tenant_id, default_campus_id, default_department_id


def _assignment_covers_default_scope(
    *,
    assignment_tenant_id: int | None,
    assignment_campus_id: int | None,
    assignment_department_id: int | None,
    default_tenant_id: int | None,
    default_campus_id: int | None,
    default_department_id: int | None,
) -> bool:
    if default_tenant_id and assignment_tenant_id not in (None, default_tenant_id):
        return False
    if default_campus_id and assignment_campus_id not in (None, default_campus_id):
        return False
    if default_department_id and assignment_department_id not in (None, default_department_id):
        return False
    return True


def _user_has_active_role_covering_default_scope(
    user,
    *,
    default_tenant_id: int | None,
    default_campus_id: int | None,
    default_department_id: int | None,
):
    assignments = UserRole.objects.filter(user=user, is_active=True, role__is_active=True).only(
        "tenant_id",
        "campus_id",
        "department_id",
    )
    for assignment in assignments:
        if _assignment_covers_default_scope(
            assignment_tenant_id=assignment.tenant_id,
            assignment_campus_id=assignment.campus_id,
            assignment_department_id=assignment.department_id,
            default_tenant_id=default_tenant_id,
            default_campus_id=default_campus_id,
            default_department_id=default_department_id,
        ):
            return True
    return False


class TenantForm(forms.ModelForm):
    class Meta:
        model = Tenant
        fields = ["code", "name", "is_active"]


class CampusForm(forms.ModelForm):
    class Meta:
        model = Campus
        fields = ["tenant", "code", "name", "address", "is_active"]

    def __init__(self, *args, tenant_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        _enforce_active_reference_choices(self)


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["tenant", "campus", "parent", "code", "name", "operation_branch", "unit_type", "is_active"]

    def __init__(self, *args, tenant_queryset=None, campus_queryset=None, parent_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        _configure_campus_dependent_parent_department_field(self, parent_queryset=parent_queryset)
        self.fields["operation_branch"].help_text = "Academic is used for grading governance. Administrative is for non-academic office grouping."
        self.fields["unit_type"].help_text = "Use Division for broad groups and Area for BA, IS/CS, Elementary, JHS, SHS, and similar owners."
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get("tenant")
        campus = cleaned.get("campus")
        parent = cleaned.get("parent")
        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError("Campus does not belong to selected tenant.")
        if parent and tenant and parent.tenant_id != tenant.id:
            raise forms.ValidationError("Parent department must belong to the selected tenant.")
        if parent and campus and parent.campus_id != campus.id:
            raise forms.ValidationError("Parent department must belong to the selected campus.")
        return cleaned


class ProgramForm(forms.ModelForm):
    class Meta:
        model = Program
        fields = ["tenant", "campus", "department", "code", "name", "level", "is_active"]

    def __init__(
        self, *args, tenant_queryset=None, campus_queryset=None, department_queryset=None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        _configure_campus_dependent_department_field(
            self,
            campus_field_name="campus",
            department_field_name="department",
            department_queryset=department_queryset,
        )
        _enforce_active_reference_choices(self)


def _configure_campus_dependent_department_field(
    form,
    *,
    campus_field_name: str,
    department_field_name: str,
    department_queryset,
):
    department_field = form.fields[department_field_name]
    if department_queryset is not None:
        department_field.queryset = department_queryset
    department_field.queryset = _active_only_queryset(department_field.queryset)

    raw_campus_id = None
    if form.is_bound:
        raw_campus_id = form.data.get(form.add_prefix(campus_field_name))
    if raw_campus_id in (None, ""):
        raw_campus_id = form.initial.get(campus_field_name)
    if hasattr(raw_campus_id, "id"):
        raw_campus_id = raw_campus_id.id
    if raw_campus_id in (None, ""):
        raw_campus_id = getattr(getattr(form, "instance", None), f"{campus_field_name}_id", None)

    try:
        selected_campus_id = int(raw_campus_id) if raw_campus_id not in (None, "") else None
    except (TypeError, ValueError):
        selected_campus_id = None

    full_queryset = department_field.queryset.select_related("campus", "parent").order_by(
        "campus__code", "parent__code", "code"
    )
    department_options = [
        {
            "id": department.id,
            "campus_id": department.campus_id,
            "label": (
                f"{department.campus.code} | {department.code} - {department.name}"
                if department.campus_id
                else f"{department.code} - {department.name}"
            ),
        }
        for department in full_queryset
    ]

    if selected_campus_id:
        department_field.queryset = full_queryset.filter(campus_id=selected_campus_id)
    else:
        department_field.queryset = full_queryset.none()

    department_field.label_from_instance = _department_with_campus_label
    department_field.widget.attrs["data-campus-dependent"] = "true"
    department_field.widget.attrs["data-campus-field-id"] = f"id_{campus_field_name}"
    department_field.widget.attrs["data-department-options"] = json.dumps(department_options)
    department_field.widget.attrs["data-placeholder"] = "---------"
    department_field.help_text = "Select the campus first to show only that campus' departments."


def _configure_campus_dependent_parent_department_field(form, *, parent_queryset):
    parent_field = form.fields["parent"]
    if parent_queryset is not None:
        parent_field.queryset = parent_queryset
    if form.instance and form.instance.pk:
        parent_field.queryset = parent_field.queryset.exclude(pk=form.instance.pk)
    parent_field.queryset = _active_only_queryset(parent_field.queryset)

    raw_campus_id = None
    if form.is_bound:
        raw_campus_id = form.data.get(form.add_prefix("campus"))
    if raw_campus_id in (None, ""):
        raw_campus_id = form.initial.get("campus")
    if hasattr(raw_campus_id, "id"):
        raw_campus_id = raw_campus_id.id
    if raw_campus_id in (None, ""):
        raw_campus_id = getattr(getattr(form, "instance", None), "campus_id", None)

    try:
        selected_campus_id = int(raw_campus_id) if raw_campus_id not in (None, "") else None
    except (TypeError, ValueError):
        selected_campus_id = None

    full_queryset = parent_field.queryset.select_related("campus").order_by("campus__code", "code", "name")
    parent_options = [
        {
            "id": department.id,
            "campus_id": department.campus_id,
            "label": _department_with_campus_label(department),
        }
        for department in full_queryset
    ]
    parent_field.queryset = full_queryset.filter(campus_id=selected_campus_id) if selected_campus_id else full_queryset.none()
    parent_field.label_from_instance = _department_with_campus_label
    parent_field.widget.attrs["data-campus-dependent"] = "true"
    parent_field.widget.attrs["data-campus-field-id"] = "id_campus"
    parent_field.widget.attrs["data-department-options"] = json.dumps(parent_options)
    parent_field.widget.attrs["data-placeholder"] = "---------"
    parent_field.help_text = (
        "Optional. Select the campus first to show only that campus' possible parent departments."
    )


class UserCreateForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput, min_length=8)

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "middle_name",
            "last_name",
            "default_tenant",
            "default_campus",
            "default_department",
            "is_active",
        ]

    def __init__(self, *args, tenant_queryset=None, campus_queryset=None, department_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["default_tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["default_campus"].queryset = campus_queryset
        _configure_campus_dependent_department_field(
            self,
            campus_field_name="default_campus",
            department_field_name="default_department",
            department_queryset=department_queryset,
        )
        allowed_domains = self._allowed_domains_for_tenant(self._resolve_selected_tenant_id())
        self.fields["email"].help_text = (
            "Allowed email domain(s): " + ", ".join(allowed_domains)
        )
        _enforce_active_reference_choices(self)

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    @staticmethod
    def _normalize_domains(raw_value) -> list[str]:
        if isinstance(raw_value, list):
            raw = ",".join(str(v) for v in raw_value)
        else:
            raw = str(raw_value or "")
        parts = [p.strip().lower() for p in raw.replace(";", ",").split(",")]
        domains = [p for p in parts if p]
        return domains or ["ncba.edu.ph"]

    def _resolve_selected_tenant_id(self):
        if self.is_bound:
            tenant_val = self.data.get(self.add_prefix("default_tenant"))
            try:
                return int(tenant_val) if tenant_val else None
            except (TypeError, ValueError):
                return None
        initial_tenant = self.initial.get("default_tenant")
        if hasattr(initial_tenant, "id"):
            return initial_tenant.id
        try:
            return int(initial_tenant) if initial_tenant else None
        except (TypeError, ValueError):
            return None

    def _allowed_domains_for_tenant(self, tenant_id: int | None) -> list[str]:
        raw_value = SystemSettingService.get(
            "USER_EMAIL_ALLOWED_DOMAINS",
            tenant_id=tenant_id,
            default="ncba.edu.ph",
        )
        return self._normalize_domains(raw_value)

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if "@" not in email:
            raise forms.ValidationError("Enter a valid email address.")
        _, domain = email.rsplit("@", 1)
        tenant_id = self._resolve_selected_tenant_id()
        allowed_domains = self._allowed_domains_for_tenant(tenant_id)
        if domain not in allowed_domains:
            raise forms.ValidationError(
                f"Email domain '{domain}' is not allowed. Allowed domain(s): {', '.join(allowed_domains)}."
            )
        return email

    def clean(self):
        cleaned = super().clean()
        campus = cleaned.get("default_campus")
        tenant = cleaned.get("default_tenant")
        department = cleaned.get("default_department")
        if campus and tenant and campus.tenant_id != tenant.id:
            raise DjangoValidationError("Default campus must belong to the selected default tenant.")
        if department and tenant and department.tenant_id != tenant.id:
            raise DjangoValidationError("Default department must belong to the selected default tenant.")
        if department and campus and department.campus_id != campus.id:
            raise DjangoValidationError("Default department must belong to the selected default campus.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        user.must_change_password = True
        if commit:
            user.save()
        return user


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "email",
            "first_name",
            "middle_name",
            "last_name",
            "default_tenant",
            "default_campus",
            "default_department",
            "is_active",
            "is_staff",
        ]

    def __init__(self, *args, tenant_queryset=None, campus_queryset=None, department_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["default_tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["default_campus"].queryset = campus_queryset
        _configure_campus_dependent_department_field(
            self,
            campus_field_name="default_campus",
            department_field_name="default_department",
            department_queryset=department_queryset,
        )
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        campus = cleaned.get("default_campus")
        tenant = cleaned.get("default_tenant")
        department = cleaned.get("default_department")
        if campus and tenant and campus.tenant_id != tenant.id:
            raise DjangoValidationError("Default campus must belong to the selected default tenant.")
        if department and tenant and department.tenant_id != tenant.id:
            raise DjangoValidationError("Default department must belong to the selected default tenant.")
        if department and campus and department.campus_id != campus.id:
            raise DjangoValidationError("Default department must belong to the selected default campus.")

        if self.instance and self.instance.pk and not self.instance.is_superuser:
            default_tenant_id = tenant.id if tenant else None
            default_campus_id = campus.id if campus else None
            default_department_id = department.id if department else None
            has_any_active_role = UserRole.objects.filter(
                user=self.instance,
                is_active=True,
                role__is_active=True,
            ).exists()
            if has_any_active_role:
                is_covered = _user_has_active_role_covering_default_scope(
                    self.instance,
                    default_tenant_id=default_tenant_id,
                    default_campus_id=default_campus_id,
                    default_department_id=default_department_id,
                )
                if not is_covered:
                    raise DjangoValidationError(
                        "Default tenant/campus/department is outside the user's active role scope. "
                        "Assign a matching role scope first, or update role assignments."
                    )
        return cleaned


class UserChangePasswordForm(forms.Form):
    new_password1 = forms.CharField(
        label="New Password",
        widget=forms.PasswordInput,
        min_length=8,
    )
    new_password2 = forms.CharField(
        label="Confirm New Password",
        widget=forms.PasswordInput,
        min_length=8,
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        pw1 = cleaned.get("new_password1")
        pw2 = cleaned.get("new_password2")

        if pw1 and pw2 and pw1 != pw2:
            raise forms.ValidationError("Passwords do not match.")

        if pw1:
            validate_password(pw1, user=self.user)
        return cleaned


class UserPrivacyConsentResetForm(forms.Form):
    confirmation_phrase = forms.CharField(
        label="Typed confirmation",
        help_text="Type RESET PRIVACY CONSENT exactly to show the consent page again for this user.",
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )

    CONFIRMATION_PHRASE = "RESET PRIVACY CONSENT"

    def clean_confirmation_phrase(self):
        value = (self.cleaned_data.get("confirmation_phrase") or "").strip()
        if value != self.CONFIRMATION_PHRASE:
            raise forms.ValidationError(f"Type {self.CONFIRMATION_PHRASE} exactly to confirm.")
        return value


class FacultyDeactivationScheduleForm(forms.Form):
    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Faculty Account",
        help_text="Select the faculty account that should be deactivated.",
    )
    scheduled_for = forms.DateTimeField(
        label="Deactivate On",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text="The account will be deactivated when the scheduled job runs on or after this date and time.",
    )
    reason = forms.CharField(
        label="Reason / Remarks",
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        help_text="Optional note for audit and admin reference.",
    )

    def __init__(self, *args, faculty_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if faculty_queryset is not None:
            self.fields["user"].queryset = faculty_queryset
        self.fields["user"].label_from_instance = _faculty_label

    def clean_user(self):
        user = self.cleaned_data["user"]
        if user.is_superuser:
            raise forms.ValidationError("Superuser accounts cannot be scheduled for deactivation.")
        if not user.is_active:
            raise forms.ValidationError("This account is already inactive.")
        return user

    def clean_scheduled_for(self):
        scheduled_for = self.cleaned_data["scheduled_for"]
        if timezone.is_naive(scheduled_for):
            scheduled_for = timezone.make_aware(scheduled_for, timezone.get_current_timezone())
        if scheduled_for <= timezone.now():
            raise forms.ValidationError("Choose a future deactivation date and time.")
        return scheduled_for


class UserRoleAssignmentForm(forms.Form):
    role = forms.ModelChoiceField(queryset=Role.objects.filter(is_active=True).order_by("name"))
    tenant = forms.ModelChoiceField(queryset=Tenant.objects.none(), required=False)
    campus = forms.ModelChoiceField(queryset=Campus.objects.none(), required=False)
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False)

    def __init__(self, *args, tenant_queryset=None, campus_queryset=None, department_queryset=None, target_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_user = target_user
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if department_queryset is not None:
            self.fields["department"].queryset = department_queryset
        department_field = self.fields["department"]
        department_field.queryset = _active_only_queryset(department_field.queryset)
        selected_campus_id = None
        raw_campus_id = self.data.get(self.add_prefix("campus")) if self.is_bound else self.initial.get("campus")
        try:
            selected_campus_id = int(raw_campus_id) if raw_campus_id not in (None, "") else None
        except (TypeError, ValueError):
            selected_campus_id = None

        department_queryset = department_field.queryset.select_related("campus", "parent").order_by(
            "campus__code", "parent__code", "code"
        )
        department_options = [
            {
                "id": department.id,
                "campus_id": department.campus_id,
                "label": (
                    f"{department.code} - {department.name}"
                    if selected_campus_id
                    else f"{department.campus.code} / {department.code} - {department.name}"
                ),
            }
            for department in department_queryset
        ]
        if selected_campus_id:
            department_field.queryset = department_queryset.filter(campus_id=selected_campus_id)
        else:
            department_field.queryset = department_queryset.none()
        department_field.widget.attrs["data-campus-dependent"] = "true"
        department_field.widget.attrs["data-department-options"] = json.dumps(department_options)
        department_field.widget.attrs["data-placeholder"] = "---------"
        department_field.help_text = "Select the campus first to show only that campus' departments."
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned_data = super().clean()
        tenant = cleaned_data.get("tenant")
        campus = cleaned_data.get("campus")
        department = cleaned_data.get("department")
        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError("Selected campus does not belong to the selected tenant.")
        if department and tenant and department.tenant_id != tenant.id:
            raise forms.ValidationError("Selected department does not belong to the selected tenant.")
        if department and campus and department.campus_id != campus.id:
            raise forms.ValidationError("Selected department does not belong to the selected campus.")
        if department and not campus:
            raise forms.ValidationError("Select a campus when assigning a department-scoped role.")

        if self.target_user and not self.target_user.is_superuser:
            default_tenant_id, default_campus_id, default_department_id = _resolve_user_default_scope_ids(self.target_user)
            if default_tenant_id or default_campus_id or default_department_id:
                new_assignment_covers_default = _assignment_covers_default_scope(
                    assignment_tenant_id=tenant.id if tenant else None,
                    assignment_campus_id=campus.id if campus else None,
                    assignment_department_id=department.id if department else None,
                    default_tenant_id=default_tenant_id,
                    default_campus_id=default_campus_id,
                    default_department_id=default_department_id,
                )
                if not new_assignment_covers_default:
                    has_existing_cover = _user_has_active_role_covering_default_scope(
                        self.target_user,
                        default_tenant_id=default_tenant_id,
                        default_campus_id=default_campus_id,
                        default_department_id=default_department_id,
                    )
                    if not has_existing_cover:
                        raise forms.ValidationError(
                            "Role scope mismatch: this assignment does not include the user's default tenant/campus/department. "
                            "Assign a matching scope first, or update the user's default scope."
                        )
        return cleaned_data


class RoleForm(forms.ModelForm):
    source_role = forms.ModelChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Copy permissions from",
        help_text="Optional. Select an existing role to copy its current permissions into the new role.",
    )

    class Meta:
        model = Role
        fields = ["code", "name", "description", "is_active"]

    def __init__(self, *args, role_queryset=None, include_copy_option: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        if include_copy_option:
            self.fields["source_role"].queryset = (role_queryset or Role.objects.filter(is_active=True)).order_by("name")
        else:
            self.fields.pop("source_role", None)
        _enforce_active_reference_choices(self)

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        if not code:
            raise forms.ValidationError("Role code is required.")
        return code


class RolePermissionsForm(forms.Form):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.filter(is_active=True).order_by("module", "action", "code"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    change_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Reason for critical permission change",
        help_text="Required only when critical access is added or removed.",
    )
    confirmation_phrase = forms.CharField(
        required=False,
        label="Typed confirmation",
        help_text="Required only for critical permission changes.",
    )

    def __init__(self, *args, role=None, **kwargs):
        super().__init__(*args, **kwargs)
        if role is not None:
            self.fields["permissions"].initial = role.role_permissions.values_list("permission_id", flat=True)
        _enforce_active_reference_choices(self)


class MenuGroupForm(forms.ModelForm):
    class Meta:
        model = MenuGroup
        fields = ["portal", "code", "label", "icon", "sort_order", "is_active"]


class MenuItemForm(forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.filter(is_active=True).order_by("module", "action", "code"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = MenuItem
        fields = [
            "menu_group",
            "portal",
            "code",
            "label",
            "route_name",
            "icon",
            "parent",
            "sort_order",
            "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["permissions"].initial = self.instance.menuitempermission_set.values_list(
                "permission_id", flat=True
            )
            self.fields["parent"].queryset = MenuItem.objects.exclude(id=self.instance.pk).order_by("portal", "label")
        else:
            self.fields["parent"].queryset = MenuItem.objects.all().order_by("portal", "label")
        _enforce_active_reference_choices(self)


class AcademicYearForm(forms.ModelForm):
    class Meta:
        model = AcademicYear
        fields = ["tenant", "code", "name", "start_date", "end_date", "is_active"]

    def __init__(self, *args, tenant_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        self.fields["code"].help_text = (
            "Use the exact stable code required by CSV imports and integrations "
            "(example: 2025-2026). It becomes locked after the academic year is used."
        )
        self.fields["name"].help_text = "Human-readable label (example: Academic Year 2025-2026)."
        if self.instance and self.instance.pk and self.instance.identifiers_are_in_use():
            self.fields["tenant"].disabled = True
            self.fields["tenant"].help_text = (
                "Locked because this academic year is already used by academic records."
            )
            self.fields["code"].disabled = True
            self.fields["code"].help_text = (
                f"Locked as {self.instance.code}. Existing CSV files and integrations depend on this code."
            )
        _enforce_active_reference_choices(self)


class TermForm(forms.ModelForm):
    class Meta:
        model = Term
        fields = [
            "tenant",
            "academic_year",
            "code",
            "name",
            "term_type",
            "sequence_no",
            "start_date",
            "end_date",
            "is_active",
        ]

    def __init__(self, *args, tenant_queryset=None, academic_year_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if academic_year_queryset is not None:
            self.fields["academic_year"].queryset = academic_year_queryset
        self.fields["code"].help_text = "Use short term code used in imports (example: 1ST, 2ND)."
        self.fields["name"].help_text = "Readable label (example: 1st Semester 2025-2026)."
        self.fields["term_type"].help_text = (
            "Classify the term so grading profiles can apply different rules for regular, summer, or special terms."
        )
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get("tenant")
        academic_year = cleaned.get("academic_year")
        if tenant and academic_year and academic_year.tenant_id != tenant.id:
            raise forms.ValidationError("Selected academic year does not belong to selected tenant.")
        return cleaned


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = [
            "tenant",
            "campus",
            "department",
            "exam_department",
            "code",
            "title",
            "units",
            "course_type",
            "default_base_value",
            "syllabus_url",
            "is_active",
        ]

    def __init__(
        self,
        *args,
        tenant_queryset=None,
        campus_queryset=None,
        department_queryset=None,
        exam_department_queryset=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if department_queryset is not None:
            self.fields["department"].queryset = department_queryset
        if exam_department_queryset is not None:
            self.fields["exam_department"].queryset = exam_department_queryset
        elif department_queryset is not None:
            self.fields["exam_department"].queryset = department_queryset
        department_field = self.fields["department"]
        department_field.queryset = _active_only_queryset(department_field.queryset)
        exam_department_field = self.fields["exam_department"]
        exam_department_queryset = _active_only_queryset(
            exam_department_field.queryset
        )
        course_instance = getattr(self, "instance", None)
        current_exam_department_id = getattr(
            course_instance, "exam_department_id", None
        )
        current_tenant_id = getattr(course_instance, "tenant_id", None)
        if (
            getattr(course_instance, "pk", None)
            and current_exam_department_id
            and current_tenant_id
        ):
            current_exam_department_queryset = Department.objects.filter(
                id=current_exam_department_id,
                tenant_id=current_tenant_id,
                tenant__is_active=True,
                campus__is_active=True,
                is_active=True,
            )
            exam_department_queryset = (
                exam_department_queryset | current_exam_department_queryset
            )
        configure_exam_department_field(
            exam_department_field,
            exam_department_queryset,
        )
        selected_campus_id = None
        raw_campus_id = (
            self.data.get(self.add_prefix("campus"))
            or self.initial.get("campus")
            or getattr(getattr(self, "instance", None), "campus_id", None)
        )
        try:
            selected_campus_id = int(raw_campus_id) if raw_campus_id not in (None, "") else None
        except (TypeError, ValueError):
            selected_campus_id = None

        department_queryset = department_field.queryset.select_related("campus").order_by("campus__name", "name", "code")
        department_options = [
            {
                "id": department.id,
                "campus_id": department.campus_id,
                "label": f"{department.code} - {department.name}",
            }
            for department in department_queryset
        ]
        if selected_campus_id:
            department_field.queryset = department_queryset.filter(campus_id=selected_campus_id)
        else:
            department_field.queryset = department_queryset.none()

        self.fields["campus"].help_text = "Leave blank to share this course across all campuses of the tenant."
        self.fields["syllabus_url"].label = "Syllabus Link"
        self.fields["syllabus_url"].help_text = (
            "Optional. Paste the Google Drive syllabus link for this course. Faculty can open it only from "
            "their own assigned class card, and Google Workspace still enforces the document sharing rules."
        )
        department_field.help_text = (
            "Optional. Select the campus first to load only that campus' departments. "
            "Leave both campus and department blank for tenant-wide shared course definitions."
        )
        exam_department_field.label = "Exam Department"
        exam_department_field.help_text = (
            "Optional Departmental Exam Builder ownership. This does not change ordinary "
            "course or offering visibility."
        )
        department_field.widget.attrs["data-campus-dependent"] = "true"
        department_field.widget.attrs["data-department-options"] = json.dumps(department_options)
        department_field.widget.attrs["data-placeholder"] = "---------"
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get("tenant")
        campus = cleaned.get("campus")
        department = cleaned.get("department")
        exam_department = cleaned.get("exam_department")
        if department and not campus:
            raise forms.ValidationError("Department requires a campus. Leave both blank for tenant-wide shared course.")
        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError("Campus does not belong to selected tenant.")
        if department and tenant and department.tenant_id != tenant.id:
            raise forms.ValidationError("Department does not belong to selected tenant.")
        if campus and department and department.campus_id != campus.id:
            raise forms.ValidationError("Department does not belong to selected campus.")
        if department and not department.is_active:
            raise forms.ValidationError("Department is inactive and cannot be used for courses.")
        if exam_department and tenant and exam_department.tenant_id != tenant.id:
            self.add_error(
                "exam_department",
                "Exam department does not belong to selected tenant.",
            )
        if exam_department and not exam_department.is_active:
            self.add_error(
                "exam_department",
                "Exam department is inactive and cannot own departmental examinations.",
            )
        return cleaned


class StrictCheckboxInput(forms.CheckboxInput):
    def value_from_datadict(self, data, files, name):
        return data.get(name) if name in data else None


class StrictCheckboxBooleanField(forms.BooleanField):
    widget = StrictCheckboxInput

    def to_python(self, value):
        if value in (None, "") or value is False:
            return False
        if value == "on" or value is True:
            return True
        raise forms.ValidationError(
            "Invalid replacement selection. Use the replacement checkbox explicitly."
        )


class BulkExamDepartmentAssignmentForm(forms.Form):
    department = forms.ModelChoiceField(
        queryset=Department.objects.none(),
        label="Responsible Exam Department",
        empty_label="Select a Department",
    )
    course_ids = forms.MultipleChoiceField(
        choices=(),
        label="Courses",
        error_messages={
            "required": "Select at least one Course.",
            "invalid_choice": "One or more selected Courses are outside your current scope.",
        },
    )
    replace_existing = StrictCheckboxBooleanField(
        required=False,
        initial=False,
        label="Replace existing Exam Department assignments",
    )

    def __init__(self, *args, department_queryset=None, course_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if department_queryset is not None:
            configure_exam_department_field(
                self.fields["department"],
                department_queryset,
            )
        self.fields["department"].widget.attrs["form"] = "bulk-exam-assignment-form"
        if course_queryset is not None:
            self.fields["course_ids"].choices = [
                (str(course_id), str(course_id))
                for course_id in course_queryset.values_list("id", flat=True)
            ]

    def clean_course_ids(self):
        course_ids = self.cleaned_data["course_ids"]
        if len(course_ids) != len(set(course_ids)):
            raise forms.ValidationError("Duplicate Course IDs are not allowed.")
        return [int(course_id) for course_id in course_ids]


class ExamDepartmentFilterForm(forms.Form):
    current_department_id = forms.ModelChoiceField(
        queryset=Department.objects.none(),
        required=False,
        empty_label="Any Department",
        label="Current Exam Department",
    )

    def __init__(self, *args, department_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if department_queryset is not None:
            configure_exam_department_field(
                self.fields["current_department_id"],
                department_queryset,
            )
        self.fields["current_department_id"].widget.attrs["id"] = "current-department"


class SectionForm(forms.ModelForm):
    class Meta:
        model = Section
        fields = ["tenant", "campus", "department", "program", "code", "name", "year_level", "is_active"]

    def __init__(
        self, *args, tenant_queryset=None, campus_queryset=None, department_queryset=None, program_queryset=None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        _configure_campus_dependent_department_field(
            self,
            campus_field_name="campus",
            department_field_name="department",
            department_queryset=department_queryset,
        )
        if program_queryset is not None:
            self.fields["program"].queryset = program_queryset
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get("tenant")
        campus = cleaned.get("campus")
        department = cleaned.get("department")
        program = cleaned.get("program")
        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError("Campus does not belong to selected tenant.")
        if department and campus and department.campus_id != campus.id:
            raise forms.ValidationError("Department does not belong to selected campus.")
        if program and department and program.department_id != department.id:
            raise forms.ValidationError("Program does not belong to selected department.")
        return cleaned


class CourseOfferingForm(forms.ModelForm):
    class Meta:
        model = CourseOffering
        fields = [
            "tenant",
            "campus",
            "department",
            "program",
            "academic_year",
            "term",
            "course",
            "section",
            "room",
            "schedule_text",
            "status",
            "is_active",
        ]

    def __init__(
        self,
        *args,
        tenant_queryset=None,
        campus_queryset=None,
        department_queryset=None,
        program_queryset=None,
        academic_year_queryset=None,
        term_queryset=None,
        course_queryset=None,
        section_queryset=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if program_queryset is not None:
            self.fields["program"].queryset = program_queryset
        if academic_year_queryset is not None:
            self.fields["academic_year"].queryset = academic_year_queryset
        if term_queryset is not None:
            self.fields["term"].queryset = term_queryset
        if course_queryset is not None:
            self.fields["course"].queryset = course_queryset
        if section_queryset is not None:
            self.fields["section"].queryset = section_queryset
        self.fields["department"].required = False
        _configure_campus_dependent_department_field(
            self,
            campus_field_name="campus",
            department_field_name="department",
            department_queryset=department_queryset,
        )
        selected_tenant_id = self._selected_model_id("tenant")
        selected_campus_id = self._selected_model_id("campus")
        selected_department_id = self._selected_model_id("department")
        selected_program_id = self._selected_model_id("program")
        selected_academic_year_id = self._selected_model_id("academic_year")

        if selected_tenant_id:
            self.fields["campus"].queryset = self.fields["campus"].queryset.filter(tenant_id=selected_tenant_id)
            self.fields["academic_year"].queryset = self.fields["academic_year"].queryset.filter(tenant_id=selected_tenant_id)
            self.fields["course"].queryset = self.fields["course"].queryset.filter(tenant_id=selected_tenant_id)
            self.fields["section"].queryset = self.fields["section"].queryset.filter(tenant_id=selected_tenant_id)
            self.fields["term"].queryset = self.fields["term"].queryset.filter(tenant_id=selected_tenant_id)
            self.fields["program"].queryset = self.fields["program"].queryset.filter(tenant_id=selected_tenant_id)

        if selected_campus_id:
            self.fields["program"].queryset = self.fields["program"].queryset.filter(campus_id=selected_campus_id)
            self.fields["course"].queryset = self.fields["course"].queryset.filter(
                models.Q(campus_id=selected_campus_id) | models.Q(campus__isnull=True)
            )
            self.fields["section"].queryset = self.fields["section"].queryset.filter(campus_id=selected_campus_id)
        else:
            self.fields["program"].queryset = self.fields["program"].queryset.none()
            self.fields["section"].queryset = self.fields["section"].queryset.none()

        self._configure_department_dependent_program_field(program_queryset=program_queryset)

        if selected_department_id:
            self.fields["section"].queryset = self.fields["section"].queryset.filter(department_id=selected_department_id)
        if selected_program_id:
            self.fields["section"].queryset = self.fields["section"].queryset.filter(program_id=selected_program_id)
        if selected_academic_year_id:
            self.fields["term"].queryset = self.fields["term"].queryset.filter(academic_year_id=selected_academic_year_id)

        self._configure_scope_dependent_section_field(section_queryset=section_queryset)

        for field_name in ("tenant", "campus", "department", "program", "academic_year", "term"):
            self.fields[field_name].queryset = self.fields[field_name].queryset.distinct()
        self.fields["course"].queryset = self.fields["course"].queryset.distinct().order_by("title", "code", "id")
        self.fields["section"].queryset = self.fields["section"].queryset.distinct().order_by("code", "name", "id")

        self.fields["department"].help_text = (
            "Choose the offering owner for the selected campus. Only departments from the selected campus are shown."
        )
        self.fields["program"].help_text = (
            "Optional when section codes are unique. Select a department first to show only matching programs."
        )
        self.fields["academic_year"].help_text = "Must match Academic Year used in CSV import (use AY code values from master)."
        self.fields["term"].help_text = "Must match Term code in CSV (example: 1ST, 2ND)."
        self.fields["section"].help_text = (
            "Use the exact section from the selected campus. Department and program selections narrow this list."
        )
        self.fields["room"].label = "Room/Office/Lab"
        _set_choice_label(self.fields.get("academic_year"), _academic_year_label)
        _set_choice_label(self.fields.get("term"), _term_label)
        _set_choice_label(self.fields.get("course"), _course_label)
        _set_choice_label(self.fields.get("section"), _section_label)
        _set_choice_label(self.fields.get("program"), _program_label)
        self.fields["is_active"].label = "Record state"
        self.fields["is_active"].widget = forms.Select(
            choices=((True, "Active"), (False, "Inactive"))
        )
        self.fields["is_active"].help_text = (
            "Inactive offerings are hidden from non-superadmin users and excluded from processing."
        )
        _enforce_active_reference_choices(self)

    def _selected_model_id(self, field_name: str):
        raw_value = None
        if self.is_bound:
            raw_value = self.data.get(self.add_prefix(field_name))
        if raw_value in (None, ""):
            raw_value = self.initial.get(field_name)
        if hasattr(raw_value, "id"):
            raw_value = raw_value.id
        if raw_value in (None, ""):
            raw_value = getattr(getattr(self, "instance", None), f"{field_name}_id", None)
        try:
            return int(raw_value) if raw_value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _configure_department_dependent_program_field(self, *, program_queryset):
        program_field = self.fields["program"]
        if program_queryset is not None:
            base_queryset = _active_only_queryset(program_queryset)
        else:
            base_queryset = _active_only_queryset(program_field.queryset)
        full_queryset = base_queryset.select_related("campus", "department").order_by(
            "campus__code", "department__code", "code", "name"
        )
        program_options = [
            {
                "id": program.id,
                "campus_id": program.campus_id,
                "department_id": program.department_id,
                "label": _program_label(program),
            }
            for program in full_queryset
        ]
        selected_campus_id = self._selected_model_id("campus")
        selected_department_id = self._selected_model_id("department")
        if selected_campus_id and selected_department_id:
            program_field.queryset = full_queryset.filter(
                campus_id=selected_campus_id,
                department_id=selected_department_id,
            )
        else:
            program_field.queryset = full_queryset.none()
        program_field.widget.attrs["data-department-dependent"] = "true"
        program_field.widget.attrs["data-campus-field-id"] = "id_campus"
        program_field.widget.attrs["data-department-field-id"] = "id_department"
        program_field.widget.attrs["data-program-options"] = json.dumps(program_options)
        program_field.widget.attrs["data-placeholder"] = "---------"

    def _configure_scope_dependent_section_field(self, *, section_queryset):
        section_field = self.fields["section"]
        if section_queryset is not None:
            base_queryset = _active_only_queryset(section_queryset)
        else:
            base_queryset = _active_only_queryset(section_field.queryset)
        full_queryset = base_queryset.select_related("campus", "department", "program").order_by(
            "campus__code", "department__code", "program__code", "code", "name"
        )
        section_options = [
            {
                "id": section.id,
                "campus_id": section.campus_id,
                "department_id": section.department_id,
                "program_id": section.program_id,
                "label": _section_label(section),
            }
            for section in full_queryset
        ]
        selected_campus_id = self._selected_model_id("campus")
        selected_department_id = self._selected_model_id("department")
        selected_program_id = self._selected_model_id("program")
        if selected_campus_id:
            scoped_queryset = full_queryset.filter(campus_id=selected_campus_id)
            if selected_department_id:
                scoped_queryset = scoped_queryset.filter(department_id=selected_department_id)
            if selected_program_id:
                scoped_queryset = scoped_queryset.filter(program_id=selected_program_id)
            section_field.queryset = scoped_queryset
        else:
            section_field.queryset = full_queryset.none()
        section_field.widget.attrs["data-section-dependent"] = "true"
        section_field.widget.attrs["data-campus-field-id"] = "id_campus"
        section_field.widget.attrs["data-department-field-id"] = "id_department"
        section_field.widget.attrs["data-program-field-id"] = "id_program"
        section_field.widget.attrs["data-section-options"] = json.dumps(section_options)
        section_field.widget.attrs["data-placeholder"] = "---------"

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get("tenant")
        campus = cleaned.get("campus")
        department = cleaned.get("department")
        program = cleaned.get("program")
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        course = cleaned.get("course")
        section = cleaned.get("section")

        if not department:
            if section:
                department = section.department
                cleaned["department"] = department
            elif course and course.department_id:
                department = course.department
                cleaned["department"] = department
        if section and not program:
            program = section.program
            cleaned["program"] = program

        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError("Campus does not belong to tenant.")
        if not department:
            self.add_error("department", "Select a department, or select a section/course that has a department.")
        if department and tenant and department.tenant_id != tenant.id:
            raise forms.ValidationError("Department does not belong to tenant.")
        if department and campus and department.campus_id != campus.id:
            raise forms.ValidationError("Department does not belong to campus.")
        if program and department and program.department_id != department.id:
            raise forms.ValidationError("Program does not belong to department.")
        if department and not department.is_active:
            raise forms.ValidationError("Department is inactive and cannot be used for course offerings.")
        if academic_year and tenant and academic_year.tenant_id != tenant.id:
            raise forms.ValidationError("Academic year does not belong to tenant.")
        if term and academic_year and term.academic_year_id != academic_year.id:
            raise forms.ValidationError("Term does not belong to selected academic year.")
        if course and tenant and course.tenant_id != tenant.id:
            raise forms.ValidationError("Course does not belong to tenant.")
        if course and course.campus_id and campus and course.campus_id != campus.id:
            raise forms.ValidationError("Course campus does not match the offering campus.")
        if section and tenant and section.tenant_id != tenant.id:
            raise forms.ValidationError("Section does not belong to tenant.")
        if section and campus and section.campus_id != campus.id:
            raise forms.ValidationError("Section does not belong to campus.")
        if section and department and section.department_id != department.id:
            raise forms.ValidationError("Section does not belong to department.")
        if section and section.department_id and not section.department.is_active:
            raise forms.ValidationError("Section belongs to an inactive department.")
        if course and course.department_id and not course.department.is_active:
            raise forms.ValidationError("Course belongs to an inactive department.")
        if section and program and section.program_id != program.id:
            raise forms.ValidationError("Section does not belong to program.")
        CourseOfferingSafetyService.validate_changes_allowed(offering=self.instance, cleaned_data=cleaned)
        return cleaned


class FacultyAssignmentForm(forms.ModelForm):
    class Meta:
        model = FacultyAssignment
        fields = ["offering", "faculty_user", "assignment_note", "is_primary", "is_active"]

    def __init__(self, *args, offering_queryset=None, faculty_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if offering_queryset is not None:
            self.fields["offering"].queryset = offering_queryset
        if faculty_queryset is not None:
            self.fields["faculty_user"].queryset = faculty_queryset
        _set_choice_label(self.fields.get("offering"), _offering_label)
        _set_choice_label(self.fields.get("faculty_user"), _faculty_label)
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        offering = cleaned.get("offering")
        faculty_user = cleaned.get("faculty_user")
        if offering and faculty_user and self.instance and self.instance.pk:
            try:
                FacultyAssignmentSafetyService.validate_direct_assignment_change(
                    assignment=self.instance,
                    new_offering_id=offering.id,
                    new_faculty_user_id=faculty_user.id,
                )
            except ValueError as exc:
                raise forms.ValidationError(str(exc))
        return cleaned


class FacultyAssignmentReplacementForm(forms.Form):
    assignment_ids = forms.MultipleChoiceField(widget=forms.MultipleHiddenInput)
    replacement_faculty = forms.ModelChoiceField(queryset=get_user_model().objects.none())
    replacement_type = forms.ChoiceField(choices=FacultyAssignmentReplacementLog.ReplacementType.choices)
    reason_category = forms.ChoiceField(choices=FacultyAssignmentReplacementLog.ReasonCategory.choices)
    remarks = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), min_length=5)

    def __init__(self, *args, assignment_queryset=None, faculty_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        assignment_queryset = assignment_queryset or FacultyAssignment.objects.none()
        faculty_queryset = faculty_queryset or get_user_model().objects.none()
        self.assignment_queryset = assignment_queryset
        self.fields["assignment_ids"].choices = [(str(row.id), str(row.id)) for row in assignment_queryset]
        self.fields["replacement_faculty"].queryset = faculty_queryset
        _set_choice_label(self.fields.get("replacement_faculty"), _faculty_label)

    def clean_assignment_ids(self):
        ids = []
        valid_ids = {str(row.id) for row in self.assignment_queryset}
        for raw in self.cleaned_data["assignment_ids"]:
            if raw not in valid_ids:
                raise forms.ValidationError("Selected assignment is no longer available in your scope.")
            ids.append(int(raw))
        if not ids:
            raise forms.ValidationError("Select at least one assigned offering to replace.")
        return ids

    def clean(self):
        cleaned = super().clean()
        replacement_faculty = cleaned.get("replacement_faculty")
        assignment_ids = cleaned.get("assignment_ids") or []
        if replacement_faculty and assignment_ids:
            same_faculty_count = self.assignment_queryset.filter(
                id__in=assignment_ids,
                faculty_user=replacement_faculty,
            ).count()
            if same_faculty_count:
                raise forms.ValidationError("Replacement faculty must be different from the current faculty.")
        return cleaned


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = [
            "tenant",
            "campus",
            "department",
            "program",
            "student_no",
            "last_name",
            "first_name",
            "middle_name",
            "official_email",
            "official_email_verified_at",
            "sex",
            "year_level",
            "status",
            "is_active",
        ]

    def __init__(
        self, *args, tenant_queryset=None, campus_queryset=None, department_queryset=None, program_queryset=None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if department_queryset is not None:
            self.fields["department"].queryset = department_queryset
        if program_queryset is not None:
            self.fields["program"].queryset = program_queryset
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get("tenant")
        campus = cleaned.get("campus")
        department = cleaned.get("department")
        program = cleaned.get("program")
        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError("Campus does not belong to selected tenant.")
        if department and campus and department.campus_id != campus.id:
            raise forms.ValidationError("Department does not belong to selected campus.")
        if program and department and program.department_id != department.id:
            raise forms.ValidationError("Program does not belong to selected department.")
        return cleaned


class GradingTemplateForm(forms.ModelForm):
    class Meta:
        model = GradingTemplate
        fields = [
            "tenant",
            "code",
            "name",
            "description",
            "default_base_value",
            "passing_grade_threshold",
            "department_visibility",
            "visible_departments",
            "is_active",
        ]
        widgets = {
            "visible_departments": forms.SelectMultiple(
                attrs={
                    "size": 8,
                    "data-template-visible-departments": "true",
                }
            ),
        }

    def __init__(self, *args, tenant_queryset=None, department_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if department_queryset is not None:
            self.fields["visible_departments"].queryset = department_queryset.select_related(
                "tenant", "campus"
            ).order_by("tenant__name", "campus__name", "name")
        self.fields["tenant"].help_text = "Choose the tenant that owns this grading template."
        self.fields["code"].help_text = "Short unique template code used as the admin/system identifier."
        self.fields["name"].help_text = "Readable template name shown to admins and faculty."
        self.fields["description"].help_text = "Optional notes that explain when or where this template should be used."
        self.fields["default_base_value"].help_text = (
            "Default base value used when TeacherMate+ transmutes raw scores under this template."
        )
        self.fields["passing_grade_threshold"].help_text = (
            "Optional template-level passing threshold. Use this when the passing rule belongs to the template itself. "
            "Tenant Grading Profile threshold still overrides this when a more specific scoped profile exists."
        )
        self.fields["department_visibility"].label = "Department Visibility"
        self.fields["department_visibility"].help_text = (
            "Choose All Departments for tenant-wide access, or Selected Departments to limit admin viewing and governance."
        )
        self.fields["department_visibility"].widget.attrs["data-template-department-visibility"] = "true"
        self.fields["visible_departments"].label = "Visible Departments"
        self.fields["visible_departments"].help_text = (
            "Required for Selected Departments. Hold Ctrl (Windows) or Command (Mac) to choose more than one."
        )
        _set_choice_label(self.fields.get("visible_departments"), _department_with_campus_label)
        self.fields["is_active"].help_text = "Only active templates can be assigned and used in grading resolution."
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get("tenant")
        visibility = cleaned.get("department_visibility")
        visible_departments = cleaned.get("visible_departments")
        passing_threshold = cleaned.get("passing_grade_threshold")
        if passing_threshold is not None and (passing_threshold <= 0 or passing_threshold > 100):
            self.add_error(
                "passing_grade_threshold",
                "Passing threshold must be greater than 0 and not greater than 100.",
            )
        if visibility == GradingTemplate.DepartmentVisibility.SELECTED:
            if not visible_departments:
                self.add_error(
                    "visible_departments",
                    "Select at least one department when Department Visibility is Selected Departments.",
                )
            elif tenant and visible_departments.exclude(tenant_id=tenant.id).exists():
                self.add_error(
                    "visible_departments",
                    "Every selected department must belong to the template tenant.",
                )
        elif visibility == GradingTemplate.DepartmentVisibility.ALL:
            cleaned["visible_departments"] = Department.objects.none()
        return cleaned


class GradingTemplateApprovalSubmitForm(forms.Form):
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional notes for approvers (Dean/Registrar/Campus Admin/Super Admin).",
    )


class GradingTemplateApprovalReviewForm(forms.Form):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"

    decision = forms.ChoiceField(choices=Decision.choices)
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class TemplateHotfixRequestForm(forms.Form):
    apply_mode = forms.ChoiceField(choices=TemplateHotfixRequest.ApplyMode.choices)
    justification = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    selected_offerings = forms.ModelMultipleChoiceField(
        queryset=CourseOffering.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Required only for SELECTED_OFFERINGS mode.",
    )

    def __init__(self, *args, offering_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if offering_queryset is not None:
            self.fields["selected_offerings"].queryset = offering_queryset.order_by(
                "course__title",
                "course__code",
                "section__code",
                "academic_year__code",
                "term__sequence_no",
                "id",
            )
        self.fields["apply_mode"].help_text = (
            "Choose how far the published-template hotfix should reach. "
            "Use Selected Offerings for a tightly controlled live patch, or Requesting Faculty's Accepted Offerings "
            "when the request should affect only classes handled by the requester."
        )
        self.fields["justification"].help_text = (
            "Explain the academic or governance reason for the hotfix so reviewers can assess impact quickly."
        )
        self.fields["selected_offerings"].help_text = (
            "Required only for Selected Offerings mode. Offerings are sorted by course title for easier scanning."
        )
        _set_choice_label(self.fields.get("selected_offerings"), _offering_label)
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        apply_mode = cleaned.get("apply_mode")
        selected = cleaned.get("selected_offerings")
        if apply_mode == TemplateHotfixRequest.ApplyMode.SELECTED_OFFERINGS and not selected:
            self.add_error("selected_offerings", "Select at least one offering for SELECTED_OFFERINGS mode.")
        return cleaned


class TemplateHotfixReviewForm(forms.Form):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve & Apply"
        REJECT = "REJECT", "Reject"

    decision = forms.ChoiceField(choices=Decision.choices)
    review_remarks = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Decision reason",
        help_text="Required for audit accountability.",
    )
    confirmation_phrase = forms.CharField(
        required=False,
        label="Typed confirmation",
        help_text="Required only when this approval applies the hotfix. Type APPLY HOTFIX.",
    )


class GradingTemplatePeriodForm(forms.ModelForm):
    class Meta:
        model = GradingTemplatePeriod
        fields = ["template", "code", "name", "grade_column_label", "sequence_no", "weight_percentage", "is_active"]

    def __init__(self, *args, template_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if template_queryset is not None:
            self.fields["template"].queryset = template_queryset
        self.fields["grade_column_label"].label = "Grade column label"
        self.fields["grade_column_label"].help_text = (
            "Optional. Used as the grade table header. Leave blank to use the default period label."
        )
        self.fields["grade_column_label"].widget.attrs.setdefault("placeholder", "FINAL EXAM")
        _enforce_active_reference_choices(self)
        _set_choice_label(
            self.fields.get("template"),
            lambda obj: getattr(obj, "name", None) or getattr(obj, "code", str(obj)),
        )


class GradingTemplateComponentForm(forms.ModelForm):
    class Meta:
        model = GradingTemplateComponent
        fields = [
            "template_period",
            "code",
            "name",
            "weight_percentage",
            "sort_order",
            "score_input_mode",
            "is_exam_component",
            "is_active",
        ]

    def __init__(self, *args, period_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if period_queryset is not None:
            self.fields["template_period"].queryset = period_queryset
        _set_choice_label(
            self.fields.get("template_period"),
            lambda obj: f"{obj.template.name} - {obj.name}" if getattr(obj, "template_id", None) else (obj.name or obj.code),
        )
        self.fields["score_input_mode"].label = "Score Entry Method"
        self.fields["score_input_mode"].help_text = (
            "Set the default entry method for this major component. "
            "Use Raw Score (Base-50) for quizzes/exams scored by points, "
            "or Direct Percentage for items encoded directly as a percentage."
        )
        self.fields["is_exam_component"].label = "Exam component"
        self.fields["is_exam_component"].help_text = (
            "Enable this for the major exam component of the period. TeacherMate+ uses this flag, not the component code, "
            "to separate exam grade from class standing."
        )
        _enforce_active_reference_choices(self)


class GradingTemplateSubcomponentForm(forms.ModelForm):
    class Meta:
        model = GradingTemplateSubcomponent
        fields = [
            "template_component",
            "code",
            "name",
            "weight_percentage",
            "sort_order",
            "score_input_mode",
            "detail_computation_mode",
            "is_attendance_component",
            "admin_locked",
            "is_active",
        ]

    def __init__(self, *args, component_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if component_queryset is not None:
            self.fields["template_component"].queryset = component_queryset
        _set_choice_label(
            self.fields.get("template_component"),
            lambda obj: f"{obj.template_period.name} - {obj.name}" if getattr(obj, "template_period_id", None) else (obj.name or obj.code),
        )
        self.fields["score_input_mode"].label = "Score Entry Method"
        self.fields["score_input_mode"].help_text = (
            "Choose how this subcomponent accepts scores when activities are encoded directly here. "
            "Use Inherit Parent Rule to follow the major component setting."
        )
        self.fields["detail_computation_mode"].label = "Detail Computation"
        self.fields["detail_computation_mode"].help_text = (
            "Weighted Details uses each detail item's configured weight. "
            "Average Activities ignores detail weights and averages the faculty-created activities under this subcomponent."
        )
        _enforce_active_reference_choices(self)


class GradingTemplateDetailForm(forms.ModelForm):
    class Meta:
        model = GradingTemplateDetail
        fields = [
            "template_subcomponent",
            "code",
            "name",
            "weight_percentage",
            "sort_order",
            "score_input_mode",
            "admin_locked",
            "is_active",
        ]

    def __init__(self, *args, subcomponent_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if subcomponent_queryset is not None:
            self.fields["template_subcomponent"].queryset = subcomponent_queryset
        _set_choice_label(
            self.fields.get("template_subcomponent"),
            lambda obj: f"{obj.template_component.name} - {obj.name}" if getattr(obj, "template_component_id", None) else (obj.name or obj.code),
        )
        self.fields["score_input_mode"].label = "Score Entry Method"
        self.fields["score_input_mode"].help_text = (
            "Use Inherit Parent Rule to follow the subcomponent setting, or override it here for this detail item."
        )
        _enforce_active_reference_choices(self)


class CourseTemplateAssignmentForm(forms.ModelForm):
    class Meta:
        model = CourseTemplateAssignment
        fields = ["course", "grading_template", "effective_from_term", "is_active"]

    def __init__(self, *args, course_queryset=None, template_queryset=None, term_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if course_queryset is not None:
            self.fields["course"].queryset = course_queryset
        if template_queryset is not None:
            self.fields["grading_template"].queryset = template_queryset
        if term_queryset is not None:
            self.fields["effective_from_term"].queryset = term_queryset
        self.fields["grading_template"].required = not bool(self.instance and self.instance.pk)
        if self.instance and self.instance.pk:
            self.fields["grading_template"].help_text = (
                "Leave blank to clear this assignment when it has no protected grading records."
            )
        _set_choice_label(self.fields.get("course"), _course_label)
        _set_choice_label(
            self.fields.get("grading_template"),
            lambda obj: getattr(obj, "name", None) or getattr(obj, "code", str(obj)),
        )
        _set_choice_label(self.fields.get("effective_from_term"), _term_label)
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        course = cleaned.get("course")
        grading_template = cleaned.get("grading_template")
        effective_from_term = cleaned.get("effective_from_term")

        if course and grading_template and course.tenant_id != grading_template.tenant_id:
            raise forms.ValidationError("Course and template must belong to the same tenant.")
        if effective_from_term and course and effective_from_term.tenant_id != course.tenant_id:
            raise forms.ValidationError("Effective term does not belong to the course tenant.")
        try:
            CourseTemplateAssignmentSafetyService.validate_template_clear_allowed(
                assignment=self.instance,
                new_template=grading_template,
            )
            CourseTemplateAssignmentSafetyService.validate_template_replacement_allowed(
                assignment=self.instance,
                new_template=grading_template,
            )
            CourseTemplateAssignmentSafetyService.validate_assignment_activation_allowed(
                assignment=self.instance,
                course=course,
                grading_template=grading_template,
                effective_from_term=effective_from_term,
                is_active=bool(cleaned.get("is_active")),
            )
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return cleaned


class BulkCourseTemplateAssignmentForm(forms.Form):
    courses = forms.ModelMultipleChoiceField(
        queryset=Course.objects.none(),
        label="Courses",
        help_text="Select one or more courses to assign to the same grading template.",
        widget=forms.SelectMultiple(attrs={"size": 14}),
    )
    grading_template = forms.ModelChoiceField(
        queryset=GradingTemplate.objects.none(),
        label="Grading template",
    )
    effective_from_term = forms.ModelChoiceField(
        queryset=Term.objects.none(),
        required=False,
        label="Effective term",
        help_text="Leave blank to create a general assignment used when no term-specific assignment exists.",
    )
    is_active = forms.BooleanField(
        required=False,
        initial=True,
        label="Active",
    )

    def __init__(self, *args, course_queryset=None, template_queryset=None, term_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if course_queryset is not None:
            self.fields["courses"].queryset = course_queryset.order_by("title", "code", "id")
        if template_queryset is not None:
            self.fields["grading_template"].queryset = template_queryset
        if term_queryset is not None:
            self.fields["effective_from_term"].queryset = term_queryset
        _set_choice_label(self.fields.get("grading_template"), lambda obj: getattr(obj, "name", None) or getattr(obj, "code", str(obj)))
        _set_choice_label(self.fields.get("effective_from_term"), _term_label)
        self.fields["courses"].label_from_instance = _course_label
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        courses = cleaned.get("courses")
        grading_template = cleaned.get("grading_template")
        effective_from_term = cleaned.get("effective_from_term")
        if courses and grading_template:
            tenant_ids = {course.tenant_id for course in courses}
            if grading_template.tenant_id not in tenant_ids or len(tenant_ids) > 1:
                raise forms.ValidationError("Selected courses and grading template must belong to the same tenant.")
        if effective_from_term and courses:
            tenant_ids = {course.tenant_id for course in courses}
            if effective_from_term.tenant_id not in tenant_ids or len(tenant_ids) > 1:
                raise forms.ValidationError("Effective term must belong to the same tenant as the selected courses.")
        return cleaned


class GradingTemplateTestingCalculatorForm(forms.Form):
    grading_template = forms.ModelChoiceField(
        queryset=GradingTemplate.objects.none(),
        label="Grading template",
        help_text="Select the grading template you want to test using sample raw score and total score values.",
    )
    sample_value = forms.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        initial=Decimal("85.00"),
        label="Default sample raw score",
        help_text="This value will prefill blank raw-score inputs. For Base-50 items, TeacherMate+ will still compute the percentage from raw score and total score.",
    )

    def __init__(self, *args, template_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if template_queryset is not None:
            self.fields["grading_template"].queryset = template_queryset.order_by("name", "code", "id")
        _set_choice_label(
            self.fields.get("grading_template"),
            lambda obj: getattr(obj, "name", None) or getattr(obj, "code", str(obj)),
        )
        _enforce_active_reference_choices(self)


class CourseBaseValueOverrideForm(forms.ModelForm):
    class Meta:
        model = CourseBaseValueOverride
        fields = ["course", "base_value", "effective_from_term", "is_active"]

    def __init__(self, *args, course_queryset=None, term_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if course_queryset is not None:
            self.fields["course"].queryset = course_queryset
        if term_queryset is not None:
            self.fields["effective_from_term"].queryset = term_queryset
        _set_choice_label(self.fields.get("course"), _course_label)
        _set_choice_label(self.fields.get("effective_from_term"), _term_label)
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        course = cleaned.get("course")
        effective_from_term = cleaned.get("effective_from_term")
        if effective_from_term and course and effective_from_term.tenant_id != course.tenant_id:
            raise forms.ValidationError("Effective term does not belong to the course tenant.")
        return cleaned


class GradingPeriodLockForm(forms.ModelForm):
    class Meta:
        model = GradingPeriodLock
        fields = [
            "tenant",
            "campus",
            "academic_year",
            "term",
            "period_code",
            "scope_type",
            "course_offering",
            "is_locked",
            "deadline_at",
            "remarks",
            "is_active",
        ]
        widgets = {
            "deadline_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(
        self,
        *args,
        tenant_queryset=None,
        campus_queryset=None,
        academic_year_queryset=None,
        term_queryset=None,
        offering_queryset=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if academic_year_queryset is not None:
            self.fields["academic_year"].queryset = academic_year_queryset
        if term_queryset is not None:
            self.fields["term"].queryset = term_queryset
        if offering_queryset is not None:
            sorted_offering_queryset = offering_queryset.select_related(
                "course",
                "section",
                "term__academic_year",
            ).order_by(
                "course__title",
                "course__code",
                "section__name",
                "section__code",
                "term__academic_year__name",
                "term__name",
                "id",
            )
            self.fields["course_offering"].queryset = sorted_offering_queryset
        self.fields["course_offering"].widget.attrs.update(
            {
                "data-searchable-select": "true",
                "data-search-placeholder": "Search course offering by title, code, section, or term",
            }
        )

        self._valid_period_codes = set()
        period_field = self.fields["period_code"]
        period_field.widget = forms.Select()

        period_queryset = GradingTemplatePeriod.objects.filter(is_active=True)
        if offering_queryset is not None:
            course_ids = list(offering_queryset.values_list("course_id", flat=True).distinct())
            if course_ids:
                template_ids = CourseTemplateAssignment.objects.filter(
                    course_id__in=course_ids,
                    is_active=True,
                ).values_list("grading_template_id", flat=True)
                filtered_period_queryset = period_queryset.filter(template_id__in=template_ids)
                if filtered_period_queryset.exists():
                    period_queryset = filtered_period_queryset
                elif tenant_queryset is not None:
                    period_queryset = period_queryset.filter(template__tenant__in=tenant_queryset)
            elif tenant_queryset is not None:
                period_queryset = period_queryset.filter(template__tenant__in=tenant_queryset)
        elif tenant_queryset is not None:
            period_queryset = period_queryset.filter(template__tenant__in=tenant_queryset)

        period_options = []
        seen_codes = set()
        for period in period_queryset.select_related("template").order_by("sequence_no", "name", "code"):
            code = (period.code or "").strip().upper()
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            period_options.append((code, _period_label(period)))

        instance_period_code = (getattr(self.instance, "period_code", "") or "").strip().upper()
        if instance_period_code and instance_period_code not in seen_codes:
            period_options.insert(0, (instance_period_code, f"{instance_period_code} (current saved value)"))

        period_field.choices = [("", "---------"), *period_options]
        period_field.widget.choices = period_field.choices
        self._valid_period_codes = {value for value, _label in period_options if value}

        _set_choice_label(self.fields.get("academic_year"), _academic_year_label)
        _set_choice_label(self.fields.get("term"), _term_label)
        _set_choice_label(self.fields.get("course_offering"), _offering_label)
        _enforce_active_reference_choices(self)
        self.fields["deadline_at"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"]
        self.fields["deadline_at"].help_text = (
            "Submission deadline for this period scope. Unsubmitted grade books remain open after this timestamp, "
            "but TeacherMate+ marks them as overdue for faculty reminder banners and admin non-compliance monitoring."
        )
        self.fields["scope_type"].help_text = (
            "Choose Campus to apply the same rule to all course offerings in the selected campus, term, and period. "
            "Choose Course Offering to override the campus rule for one specific class."
        )
        self.fields["is_locked"].help_text = (
            "When checked, faculty score, activity, and attendance editing is disabled for this rule's scope. "
            "Leave unchecked when the rule is only setting a submission deadline/reminder."
        )
        self.fields["is_active"].help_text = (
            "When unchecked, this rule is ignored by faculty pages, deadline checks, and auto-lock processing."
        )
        self.fields["period_code"].help_text = (
            "Choose the actual grading period code used by the grading template, such as PRELIM or GENED_PRELIM. "
            "Do not enter term codes like 2526_2NDSEM."
        )

    def clean(self):
        cleaned = super().clean()
        scope_type = cleaned.get("scope_type")
        offering = cleaned.get("course_offering")
        period_code = (cleaned.get("period_code") or "").strip().upper()
        tenant = cleaned.get("tenant")
        campus = cleaned.get("campus")
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        deadline_at = cleaned.get("deadline_at")
        cleaned["period_code"] = period_code

        if period_code and self._valid_period_codes and period_code not in self._valid_period_codes:
            self.add_error(
                "period_code",
                "Select a valid grading period code from the template periods used by your scoped offerings.",
            )

        if scope_type == GradingPeriodLock.ScopeType.CAMPUS and offering:
            raise forms.ValidationError("Campus-wide lock must not have a course offering.")
        if scope_type == GradingPeriodLock.ScopeType.COURSE and not offering:
            raise forms.ValidationError("Course-scoped lock requires a course offering.")

        if offering:
            if tenant and offering.tenant_id != tenant.id:
                raise forms.ValidationError("Offering tenant does not match lock tenant.")
            if campus and offering.campus_id != campus.id:
                raise forms.ValidationError("Offering campus does not match lock campus.")
            if academic_year and offering.academic_year_id != academic_year.id:
                raise forms.ValidationError("Offering academic year does not match lock academic year.")
            if term and offering.term_id != term.id:
                raise forms.ValidationError("Offering term does not match lock term.")
        return cleaned


class GradeEncodingControlForm(forms.ModelForm):
    class Meta:
        model = GradeEncodingControl
        fields = [
            "tenant",
            "academic_year",
            "term",
            "period_code",
            "campus",
            "course_offering",
            "status",
            "reason",
            "notice_to_faculty",
            "is_active",
        ]
        widgets = {
            "notice_to_faculty": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(
        self,
        *args,
        tenant_queryset=None,
        campus_queryset=None,
        academic_year_queryset=None,
        term_queryset=None,
        offering_queryset=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if academic_year_queryset is not None:
            self.fields["academic_year"].queryset = academic_year_queryset
        if term_queryset is not None:
            self.fields["term"].queryset = term_queryset
        if offering_queryset is not None:
            self.fields["course_offering"].queryset = offering_queryset.select_related(
                "course",
                "section",
                "term__academic_year",
                "campus",
            ).order_by(
                "course__title",
                "course__code",
                "section__name",
                "section__code",
                "term__academic_year__name",
                "term__name",
                "id",
            )
        self.fields["period_code"].required = False
        self.fields["period_code"].widget = forms.Select()
        period_queryset = GradingTemplatePeriod.objects.filter(is_active=True)
        if tenant_queryset is not None:
            period_queryset = period_queryset.filter(template__tenant__in=tenant_queryset)
        period_options = []
        seen_codes = set()
        for period in period_queryset.select_related("template").order_by("sequence_no", "name", "code"):
            code = (period.code or "").strip().upper()
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            period_options.append((code, _period_label(period)))
        instance_period_code = (getattr(self.instance, "period_code", "") or "").strip().upper()
        if instance_period_code and instance_period_code not in seen_codes:
            period_options.insert(0, (instance_period_code, f"{instance_period_code} (current saved value)"))
        self.fields["period_code"].choices = [("", "All grading periods"), *period_options]
        self.fields["period_code"].widget.choices = self.fields["period_code"].choices
        self.fields["campus"].required = False
        self.fields["course_offering"].required = False
        self.fields["reason"].required = False
        self.fields["notice_to_faculty"].required = False
        self.fields["period_code"].help_text = (
            "Leave blank to apply to all grading periods in the selected academic year and term."
        )
        self.fields["campus"].help_text = "Leave blank for all campuses within your allowed scope."
        self.fields["course_offering"].help_text = "Leave blank unless this control is for one specific class."
        self.fields["reason"].help_text = "Required when status is Closed."
        self.fields["notice_to_faculty"].help_text = "Required when status is Closed. This is shown to faculty."
        self.fields["status"].help_text = (
            "Closed blocks faculty encoding and submission. Open does not override a broader Closed control."
        )
        self.fields["is_active"].help_text = "Inactive controls are ignored."
        self.fields["course_offering"].widget.attrs.update(
            {
                "data-searchable-select": "true",
                "data-search-placeholder": "Search course offering by title, code, section, or term",
            }
        )
        _set_choice_label(self.fields.get("academic_year"), _academic_year_label)
        _set_choice_label(self.fields.get("term"), _term_label)
        _set_choice_label(self.fields.get("course_offering"), _offering_label)
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get("tenant")
        campus = cleaned.get("campus")
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        period_code = (cleaned.get("period_code") or "").strip().upper()
        course_offering = cleaned.get("course_offering")
        status = cleaned.get("status")
        reason = (cleaned.get("reason") or "").strip()
        notice = (cleaned.get("notice_to_faculty") or "").strip()
        is_active = cleaned.get("is_active")
        cleaned["period_code"] = period_code or None

        if term and academic_year and term.academic_year_id != academic_year.id:
            raise forms.ValidationError({"term": "Term must belong to the selected academic year."})
        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError({"campus": "Campus must belong to the selected tenant."})
        if course_offering:
            if tenant and course_offering.tenant_id != tenant.id:
                raise forms.ValidationError({"course_offering": "Course offering must belong to the selected tenant."})
            if academic_year and course_offering.academic_year_id != academic_year.id:
                raise forms.ValidationError(
                    {"course_offering": "Course offering must belong to the selected academic year."}
                )
            if term and course_offering.term_id != term.id:
                raise forms.ValidationError({"course_offering": "Course offering must belong to the selected term."})
            if campus and course_offering.campus_id != campus.id:
                raise forms.ValidationError({"course_offering": "Course offering must belong to the selected campus."})
        if status == GradeEncodingControl.Status.CLOSED:
            errors = {}
            if not reason:
                errors["reason"] = "Enter the reason when closing grade encoding."
            if not notice:
                errors["notice_to_faculty"] = "Enter the faculty notice when closing grade encoding."
            if errors:
                raise forms.ValidationError(errors)
        if tenant and academic_year and term and is_active:
            duplicate_qs = GradeEncodingControl.objects.filter(
                tenant=tenant,
                academic_year=academic_year,
                term=term,
                period_code=cleaned["period_code"],
                campus=campus,
                course_offering=course_offering,
                is_active=True,
            )
            if self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                raise forms.ValidationError(
                    "An active grade encoding control already exists for this exact scope. Edit the existing control instead."
                )
        return cleaned


class GradeSubmissionReopenRequestForm(forms.Form):
    justification = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=(
            "Explain why this submitted grading period needs to be reopened before the deadline. "
            "After the deadline, submitted gradebooks must use Correction of Grades."
        ),
    )


class GradeSubmissionReopenReviewForm(forms.Form):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"

    decision = forms.ChoiceField(choices=Decision.choices)
    review_remarks = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Decision reason",
        help_text="Required for audit accountability.",
    )


class GradeCorrectionReviewForm(forms.Form):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"

    decision = forms.ChoiceField(choices=Decision.choices)
    review_remarks = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Decision reason",
        help_text="Required for audit accountability.",
    )
    window_start = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    window_end = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, require_window: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_window = require_window
        if not self.require_window:
            self.fields.pop("window_start", None)
            self.fields.pop("window_end", None)

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get("decision")
        window_start = cleaned.get("window_start")
        window_end = cleaned.get("window_end")
        if decision == self.Decision.APPROVE and self.require_window:
            if not window_start:
                self.add_error("window_start", "Window start is required for approval.")
            if not window_end:
                self.add_error("window_end", "Window end is required for approval.")
            if window_start and window_end and window_end <= window_start:
                self.add_error("window_end", "Window end must be later than window start.")
        return cleaned


class GradeCorrectionOnBehalfSetupForm(forms.Form):
    campus = forms.ModelChoiceField(
        queryset=Campus.objects.none(),
        required=False,
        label="Correction campus",
    )
    academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.none(),
        required=False,
        label="Academic year",
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.none(),
        required=False,
        label="Term",
    )
    faculty_user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Original faculty member",
        help_text="Select the faculty member responsible for the submitted gradebook, even if the account is inactive.",
    )
    section = forms.ModelChoiceField(
        queryset=Section.objects.none(),
        required=False,
        label="Section",
    )
    course = forms.ModelChoiceField(
        queryset=Course.objects.none(),
        required=False,
        label="Course",
    )
    template_period = forms.ModelChoiceField(
        queryset=GradingTemplatePeriod.objects.none(),
        required=False,
        label="Grading period",
    )
    on_behalf_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="On-behalf reason",
        help_text="Optional operational note, for example: original faculty is no longer connected.",
    )

    def __init__(
        self,
        *args,
        campus_queryset=None,
        academic_year_queryset=None,
        term_queryset=None,
        faculty_queryset=None,
        section_queryset=None,
        course_queryset=None,
        period_queryset=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if academic_year_queryset is not None:
            self.fields["academic_year"].queryset = academic_year_queryset
        if term_queryset is not None:
            self.fields["term"].queryset = term_queryset
        if faculty_queryset is not None:
            self.fields["faculty_user"].queryset = faculty_queryset
        if section_queryset is not None:
            self.fields["section"].queryset = section_queryset
        if course_queryset is not None:
            self.fields["course"].queryset = course_queryset
        if period_queryset is not None:
            self.fields["template_period"].queryset = period_queryset
        _set_choice_label(self.fields.get("campus"), _campus_label)
        _set_choice_label(self.fields.get("academic_year"), _academic_year_label)
        _set_choice_label(self.fields.get("term"), _term_label)
        self.fields["faculty_user"].label_from_instance = _faculty_label
        _set_choice_label(self.fields.get("section"), _section_label)
        _set_choice_label(self.fields.get("course"), _course_label)
        self.fields["template_period"].label_from_instance = lambda obj: obj.name or obj.code


class TenantGradingProfileForm(forms.ModelForm):
    deped_transmutation_table_text = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": "98.40-99.99=99\n96.80-98.39=98\n0.00-3.99=60",
            }
        ),
        label="DepEd Transmutation Table",
        help_text=(
            "Required only when using DepEd Transmutation Table. Leave blank to use the standard DepEd K-12 table. "
            "Enter one range per line using MIN-MAX=GRADE."
        ),
    )
    final_grade_period_weights_text = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "PRELIM=25\nMIDTERM=25\nPREFINAL=25\nFINAL=25",
            }
        ),
        label="Final Grade Period Weights",
        help_text=(
            "Required only when using Weighted Selected Periods. Enter one period code and weight per line "
            "using PERIOD_CODE=WEIGHT. Example: PRELIM=25"
        ),
    )

    class Meta:
        model = TenantGradingProfile
        fields = [
            "tenant",
            "campus",
            "department",
            "program",
            "course",
            "course_type",
            "term_type",
            "profile_code",
            "profile_name",
            "grading_template",
            "default_base_value",
            "passing_grade_threshold",
            "period_grade_formula_mode",
            "deped_transmutation_table_text",
            "final_grade_formula_mode",
            "final_grade_period_weights_text",
            "priority",
            "effective_from_term",
            "is_default",
            "is_active",
        ]

    def __init__(
        self,
        *args,
        tenant_queryset=None,
        campus_queryset=None,
        department_queryset=None,
        program_queryset=None,
        course_queryset=None,
        template_queryset=None,
        term_queryset=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        _configure_campus_dependent_department_field(
            self,
            campus_field_name="campus",
            department_field_name="department",
            department_queryset=department_queryset,
        )
        if program_queryset is not None:
            self.fields["program"].queryset = program_queryset
        if course_queryset is not None:
            self.fields["course"].queryset = course_queryset.select_related("campus", "department").order_by(
                "title", "code", "campus__code"
            )
        if template_queryset is not None:
            self.fields["grading_template"].queryset = template_queryset
        if term_queryset is not None:
            self.fields["effective_from_term"].queryset = term_queryset

        self.fields["tenant"].help_text = (
            "Choose the tenant that owns this grading policy. All other scope fields and templates must belong to this tenant."
        )
        self.fields["campus"].help_text = (
            "Optional campus scope. Leave blank if this profile should apply across the whole tenant."
        )
        self.fields["department"].help_text = (
            "Optional narrower scope under the selected campus. Select the campus first so duplicate department names from other campuses are not shown."
        )
        self.fields["program"].help_text = (
            "Optional narrower scope under the selected department. Use this when one program needs its own grading rule."
        )
        self.fields["course"].help_text = (
            "Optional course-specific override. Use this only when one exact course should follow a different grading profile."
        )
        self.fields["course_type"].help_text = (
            "Optional fallback by course type. Use this when several courses share the same course-type rule instead of selecting one exact course."
        )
        self.fields["term_type"].label = "Applicable Term Type"
        self.fields["term_type"].help_text = (
            "Leave blank to apply to all terms. Select a value to restrict this profile to a specific term type "
            "(e.g., Summer)."
        )
        self.fields["profile_code"].help_text = (
            "Short unique code for this grading profile, used as the admin reference identifier."
        )
        self.fields["profile_name"].help_text = (
            "Readable profile name that explains the purpose of this grading rule, such as 'NCBA Gen Ed Standard'."
        )
        self.fields["grading_template"].help_text = (
            "Select the grading template that will drive period, component, subcomponent, attendance, and activity computation for this profile scope."
        )
        self.fields["default_base_value"].help_text = (
            "Optional profile-level base value for raw-score transmutation. Leave blank to let TeacherMate+ fall back to course or template defaults."
        )
        self.fields["passing_grade_threshold"].help_text = (
            "Optional passing threshold for analytics and governance at this profile scope "
            "(example: 75.00). Leave blank to use tenant default."
        )
        self.fields["period_grade_formula_mode"].help_text = (
            "Choose how TeacherMate+ computes each official period grade. Use weighted components for the existing "
            "TeacherMate+ behavior, or DepEd Transmutation Table for K-12 E-Class Record style grading."
        )
        self.fields["final_grade_formula_mode"].help_text = (
            "Choose how TeacherMate+ computes the official final grade for offerings matched by this profile. "
            "Use the default average mode for NCBA-style equal-period averaging, or choose weighted mode when a tenant uses specific period weights."
        )
        self.fields["priority"].help_text = (
            "Lower numbers are matched first. Use priority when multiple profiles may fit the same offering scope."
        )
        self.fields["effective_from_term"].help_text = (
            "Optional starting term for this profile. Leave blank if the rule should be available for any term in the selected scope."
        )
        self.fields["is_default"].help_text = (
            "Mark as default when this should act as the normal fallback profile after more specific matches have already been checked."
        )
        self.fields["is_active"].help_text = (
            "Only active profiles are considered during grading resolution."
        )
        _set_choice_label(self.fields.get("course"), _course_label)
        _set_choice_label(
            self.fields.get("grading_template"),
            lambda obj: getattr(obj, "name", None) or getattr(obj, "code", str(obj)),
        )
        _set_choice_label(self.fields.get("effective_from_term"), _term_label)
        _enforce_active_reference_choices(self)

        selected_template = self.instance.grading_template if getattr(self.instance, "grading_template_id", None) else None
        if self.is_bound:
            template_raw = self.data.get(self.add_prefix("grading_template"))
            if template_raw:
                try:
                    selected_template = self.fields["grading_template"].queryset.filter(id=int(template_raw)).first()
                except (TypeError, ValueError):
                    selected_template = selected_template

        available_period_codes = []
        if selected_template:
            available_period_codes = list(
                selected_template.periods.filter(is_active=True)
                .order_by("sequence_no", "id")
                .values_list("code", flat=True)
            )
        if available_period_codes:
            self.fields["final_grade_period_weights_text"].help_text += (
                " Active template periods for this profile: " + ", ".join(available_period_codes) + "."
            )
        else:
            self.fields["final_grade_period_weights_text"].help_text += (
                " Select the grading template first so TeacherMate+ can show the valid period codes for this formula."
            )

        if not self.is_bound and getattr(self.instance, "final_grade_formula_json", None):
            weights = (self.instance.final_grade_formula_json or {}).get("period_weights") or []
            self.initial["final_grade_period_weights_text"] = "\n".join(
                f"{item.get('period_code')}={item.get('weight')}" for item in weights if item.get("period_code")
            )
        if not self.is_bound and getattr(self.instance, "period_grade_formula_json", None):
            table = (self.instance.period_grade_formula_json or {}).get("transmutation_table") or []
            self.initial["deped_transmutation_table_text"] = "\n".join(
                f"{item.get('min')}-{item.get('max')}={item.get('grade')}"
                for item in table
                if item.get("min") is not None and item.get("max") is not None and item.get("grade") is not None
            )

    def clean(self):
        cleaned = super().clean()
        tenant = cleaned.get("tenant")
        campus = cleaned.get("campus")
        department = cleaned.get("department")
        program = cleaned.get("program")
        course = cleaned.get("course")
        grading_template = cleaned.get("grading_template")
        effective_from_term = cleaned.get("effective_from_term")

        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError("Campus does not belong to tenant.")
        if department and campus and department.campus_id != campus.id:
            raise forms.ValidationError("Department does not belong to selected campus.")
        if program and department and program.department_id != department.id:
            raise forms.ValidationError("Program does not belong to selected department.")
        if course and tenant and course.tenant_id != tenant.id:
            raise forms.ValidationError("Course does not belong to selected tenant.")
        if grading_template and tenant and grading_template.tenant_id != tenant.id:
            raise forms.ValidationError("Template does not belong to selected tenant.")
        if effective_from_term and tenant and effective_from_term.tenant_id != tenant.id:
            raise forms.ValidationError("Effective term does not belong to selected tenant.")

        if department and not campus:
            raise forms.ValidationError("Department scope requires campus scope.")
        if program and not department:
            raise forms.ValidationError("Program scope requires department scope.")

        course_type = (cleaned.get("course_type") or "").strip()
        if course and course_type:
            raise forms.ValidationError("Choose either course-specific or course_type fallback, not both.")
        cleaned["course_type"] = course_type or None
        cleaned["term_type"] = (cleaned.get("term_type") or "").strip() or None

        passing_threshold = cleaned.get("passing_grade_threshold")
        if passing_threshold is not None and (passing_threshold <= 0 or passing_threshold > 100):
            self.add_error(
                "passing_grade_threshold",
                "Passing threshold must be greater than 0 and not greater than 100.",
            )
        period_formula_mode = (
            cleaned.get("period_grade_formula_mode")
            or TenantGradingProfile.PeriodGradeFormulaMode.WEIGHTED_COMPONENTS
        )
        deped_table_text = (cleaned.get("deped_transmutation_table_text") or "").strip()
        period_formula_json = None
        if period_formula_mode == TenantGradingProfile.PeriodGradeFormulaMode.DEPED_TRANSMUTATION:
            parsed_table = []
            if deped_table_text:
                for line_no, raw_line in enumerate(deped_table_text.splitlines(), start=1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    if "=" not in line or "-" not in line.split("=", 1)[0]:
                        self.add_error(
                            "deped_transmutation_table_text",
                            f"Line {line_no} must follow MIN-MAX=GRADE.",
                        )
                        continue
                    range_raw, grade_raw = line.split("=", 1)
                    min_raw, max_raw = range_raw.split("-", 1)
                    try:
                        minimum = Decimal(min_raw.strip())
                        maximum = Decimal(max_raw.strip())
                        grade = Decimal(grade_raw.strip())
                    except (InvalidOperation, ValueError):
                        self.add_error(
                            "deped_transmutation_table_text",
                            f"Line {line_no} has an invalid number.",
                        )
                        continue
                    if minimum < 0 or maximum > 100 or minimum > maximum:
                        self.add_error(
                            "deped_transmutation_table_text",
                            f"Line {line_no} must use a valid 0.00 to 100.00 range.",
                        )
                        continue
                    if grade < 0 or grade > 100:
                        self.add_error(
                            "deped_transmutation_table_text",
                            f"Line {line_no} grade must be between 0 and 100.",
                        )
                        continue
                    parsed_table.append(
                        {
                            "min": f"{minimum.quantize(Decimal('0.01'))}",
                            "max": f"{maximum.quantize(Decimal('0.01'))}",
                            "grade": f"{grade.quantize(Decimal('1'))}",
                        }
                    )
            else:
                parsed_table = [
                    {"min": item["min"], "max": item["max"], "grade": item["grade"]}
                    for item in FacultyGradingService.DEFAULT_DEPED_TRANSMUTATION_TABLE
                ]
            if not self.errors.get("deped_transmutation_table_text"):
                if not parsed_table:
                    self.add_error("deped_transmutation_table_text", "Enter at least one transmutation-table row.")
                else:
                    period_formula_json = {"transmutation_table": parsed_table}
        formula_mode = cleaned.get("final_grade_formula_mode") or TenantGradingProfile.FinalGradeFormulaMode.AVERAGE_ACTIVE_PERIODS
        weights_text = (cleaned.get("final_grade_period_weights_text") or "").strip()
        final_formula_json = None
        if formula_mode == TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS:
            if not grading_template:
                self.add_error("grading_template", "Select a grading template before configuring weighted final-grade periods.")
            active_period_codes = {
                (code or "").strip().upper()
                for code in (grading_template.periods.filter(is_active=True).values_list("code", flat=True) if grading_template else [])
            }
            if not weights_text:
                self.add_error("final_grade_period_weights_text", "Enter at least one weighted period line.")
            else:
                parsed_weights = []
                seen_codes = set()
                total_weight = Decimal("0")
                for line_no, raw_line in enumerate(weights_text.splitlines(), start=1):
                    line = raw_line.strip()
                    if not line:
                        continue
                    if "=" not in line:
                        self.add_error(
                            "final_grade_period_weights_text",
                            f"Line {line_no} must follow PERIOD_CODE=WEIGHT.",
                        )
                        continue
                    period_code_raw, weight_raw = line.split("=", 1)
                    period_code = period_code_raw.strip().upper()
                    if not period_code:
                        self.add_error("final_grade_period_weights_text", f"Line {line_no} is missing a period code.")
                        continue
                    if period_code in seen_codes:
                        self.add_error(
                            "final_grade_period_weights_text",
                            f"Period code {period_code} is listed more than once.",
                        )
                        continue
                    if active_period_codes and period_code not in active_period_codes:
                        self.add_error(
                            "final_grade_period_weights_text",
                            f"Period code {period_code} does not belong to the selected active grading template.",
                        )
                        continue
                    try:
                        weight = Decimal(weight_raw.strip())
                    except (InvalidOperation, ValueError):
                        self.add_error(
                            "final_grade_period_weights_text",
                            f"Line {line_no} has an invalid weight value.",
                        )
                        continue
                    if weight <= 0 or weight > 100:
                        self.add_error(
                            "final_grade_period_weights_text",
                            f"Weight for {period_code} must be greater than 0 and not greater than 100.",
                        )
                        continue
                    seen_codes.add(period_code)
                    total_weight += weight
                    parsed_weights.append(
                        {
                            "period_code": period_code,
                            "weight": f"{weight.quantize(Decimal('0.01'))}",
                        }
                    )
                if not self.errors.get("final_grade_period_weights_text"):
                    if parsed_weights and total_weight != Decimal("100"):
                        self.add_error(
                            "final_grade_period_weights_text",
                            f"Weighted periods must total exactly 100.00. Current total: {total_weight.quantize(Decimal('0.01'))}.",
                        )
                    elif not parsed_weights:
                        self.add_error("final_grade_period_weights_text", "Enter at least one valid weighted period line.")
                    else:
                        final_formula_json = {"period_weights": parsed_weights}
        cleaned["_period_grade_formula_json"] = period_formula_json
        cleaned["_final_grade_formula_json"] = final_formula_json
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.period_grade_formula_json = self.cleaned_data.get("_period_grade_formula_json")
        instance.final_grade_formula_json = self.cleaned_data.get("_final_grade_formula_json")
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class ActiveAcademicTermSettingForm(forms.Form):
    active_academic_year = forms.ModelChoiceField(
        queryset=AcademicYear.objects.none(),
        required=False,
        label="Active Academic Year",
        help_text="Faculty course cards will use this year as active scope when set.",
    )
    active_term = forms.ModelChoiceField(
        queryset=Term.objects.none(),
        required=False,
        label="Active Term",
        help_text="Only offerings under this term are treated as active in faculty view.",
    )

    def __init__(self, *args, academic_year_queryset=None, term_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if academic_year_queryset is not None:
            self.fields["active_academic_year"].queryset = academic_year_queryset
        if term_queryset is not None:
            self.fields["active_term"].queryset = term_queryset
        _set_choice_label(self.fields.get("active_academic_year"), _academic_year_label)
        _set_choice_label(self.fields.get("active_term"), _term_label)
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        ay = cleaned.get("active_academic_year")
        term = cleaned.get("active_term")
        if (ay and not term) or (term and not ay):
            raise forms.ValidationError("Select both Active Academic Year and Active Term, or clear both.")
        if ay and term and term.academic_year_id != ay.id:
            self.add_error("active_term", "Selected term does not belong to the selected academic year.")
        return cleaned


class TenantTermGradingPeriodForm(forms.ModelForm):
    class Meta:
        model = TenantTermGradingPeriod
        fields = ["code", "name", "sequence_no", "is_active"]

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()


class ActiveGradingPeriodSettingForm(forms.Form):
    campus = forms.ModelChoiceField(
        queryset=Campus.objects.none(),
        required=False,
        label="Campus",
    )
    term = forms.ModelChoiceField(
        queryset=Term.objects.none(),
        required=False,
        label="Term",
    )
    period = forms.ModelChoiceField(
        queryset=TenantTermGradingPeriod.objects.none(),
        required=False,
        label="Active Grading Period",
        help_text="Choose the current grading period for the selected campus and term. Leave blank to clear it.",
    )
    remarks = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Optional note for why this active period was set or changed.",
    )
    auto_advance_enabled = forms.BooleanField(
        required=False,
        label="Auto-advance after deadline",
        help_text="When enabled, TeacherMate+ will move to the next configured period after the current period deadline passes.",
    )

    def __init__(self, *args, campus_queryset=None, term_queryset=None, period_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if term_queryset is not None:
            self.fields["term"].queryset = term_queryset
        if period_queryset is not None:
            self.fields["period"].queryset = period_queryset
        _set_choice_label(self.fields.get("campus"), lambda obj: getattr(obj, "name", None) or getattr(obj, "code", str(obj)))
        _set_choice_label(self.fields.get("term"), _term_label)
        _set_choice_label(self.fields.get("period"), _period_label)
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        term = cleaned.get("term")
        period = cleaned.get("period")
        if period and term and period.term_id != term.id:
            self.add_error("period", "Selected grading period does not belong to the selected term.")
        return cleaned


class CorrectionGovernanceSettingForm(forms.Form):
    CORRECTION_MODE_CHOICES = [
        ("MANUAL_ONLY", "Manual Only (paper form + admin reopen)"),
        ("SYSTEM_REQUEST", "System Request Workflow"),
    ]

    correction_mode = forms.ChoiceField(
        choices=CORRECTION_MODE_CHOICES,
        label="Correction process mode",
        help_text=(
            "Manual Only disables faculty in-portal correction request filing. "
            "System Request enables the in-portal correction workflow."
        ),
    )


class CorrectionPetitionWindowPolicyForm(forms.ModelForm):
    class Meta:
        model = CorrectionPetitionWindowPolicy
        fields = [
            "campus",
            "academic_year",
            "term",
            "grading_period",
            "policy_mode",
            "allowed_days_after_period_end",
            "manual_notice",
            "is_active",
        ]

    def __init__(
        self,
        *args,
        tenant=None,
        campus_queryset=None,
        academic_year_queryset=None,
        term_queryset=None,
        grading_period_queryset=None,
        **kwargs,
    ):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if academic_year_queryset is not None:
            self.fields["academic_year"].queryset = academic_year_queryset
        if term_queryset is not None:
            self.fields["term"].queryset = term_queryset
        if grading_period_queryset is not None:
            candidate_periods = list(
                grading_period_queryset.filter(
                    is_active=True,
                    template__is_active=True,
                    template__is_published=True,
                )
                .select_related("template")
                .order_by("sequence_no", "template__code", "id")
            )
            representative_ids = {}
            for period in candidate_periods:
                canonical_key = _normalize_correction_policy_period_key(period)
                if canonical_key:
                    representative_ids.setdefault(canonical_key, period.id)
            allowed_period_ids = list(representative_ids.values())
            if self.instance and self.instance.pk and self.instance.grading_period_id:
                allowed_period_ids.append(self.instance.grading_period_id)
            self.fields["grading_period"].queryset = GradingTemplatePeriod.objects.filter(
                id__in=allowed_period_ids
            ).select_related("template").order_by("sequence_no", "template__code", "id")
        self.fields["campus"].required = False
        self.fields["academic_year"].required = False
        self.fields["term"].required = False
        self.fields["campus"].help_text = "Leave blank to apply the policy across the entire tenant."
        self.fields["academic_year"].help_text = "Leave blank to apply across all academic years."
        self.fields["term"].help_text = "Leave blank to apply across all terms within the selected academic-year scope."
        self.fields["manual_notice"].required = False
        self.fields["manual_notice"].widget = forms.Textarea(attrs={"rows": 3})
        self.fields["manual_notice"].help_text = (
            "Optional note shown to faculty when this policy limits new correction petitions."
        )
        self.fields["allowed_days_after_period_end"].required = False
        self.fields["allowed_days_after_period_end"].help_text = (
            "Required only for the Days After Period End policy mode."
        )
        _set_choice_label(self.fields.get("campus"), lambda obj: getattr(obj, "name", None) or getattr(obj, "code", str(obj)))
        _set_choice_label(self.fields.get("academic_year"), _academic_year_label)
        _set_choice_label(self.fields.get("term"), _term_label)
        _set_choice_label(self.fields.get("grading_period"), _period_label)
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        tenant = self.tenant or getattr(self.instance, "tenant", None)
        campus = cleaned.get("campus")
        academic_year = cleaned.get("academic_year")
        term = cleaned.get("term")
        grading_period = cleaned.get("grading_period")
        policy_mode = cleaned.get("policy_mode")
        allowed_days = cleaned.get("allowed_days_after_period_end")
        is_active = bool(cleaned.get("is_active"))

        if tenant and campus and campus.tenant_id != tenant.id:
            self.add_error("campus", "Campus must belong to the selected tenant scope.")
        if tenant and academic_year and academic_year.tenant_id != tenant.id:
            self.add_error("academic_year", "Academic year must belong to the selected tenant scope.")
        if tenant and term and term.tenant_id != tenant.id:
            self.add_error("term", "Term must belong to the selected tenant scope.")
        if tenant and grading_period and grading_period.template.tenant_id != tenant.id:
            self.add_error("grading_period", "Grading period must belong to the selected tenant scope.")
        if academic_year and term and term.academic_year_id != academic_year.id:
            self.add_error("term", "Selected term does not belong to the selected academic year.")
        if term and not academic_year:
            self.add_error("term", "Select an academic year when a term scope is selected.")
        if not grading_period:
            self.add_error("grading_period", "Grading period is required.")
        elif not (
            grading_period.is_active
            and grading_period.template.is_active
            and grading_period.template.is_published
        ):
            self.add_error("grading_period", "Choose an active period from an active published grading template.")
        elif tenant:
            eligible_ids = {
                period.id
                for period in GradingGovernanceService.eligible_configurable_correction_periods(
                    tenant_id=tenant.id,
                    campus_id=campus.id if campus else None,
                    academic_year_id=academic_year.id if academic_year else None,
                    term_id=term.id if term else None,
                )
            }
            if grading_period.id not in eligible_ids:
                self.add_error(
                    "grading_period",
                    "Choose a grading period applicable to the selected campus, academic year, and term scope.",
                )

        if policy_mode == CorrectionPetitionWindowPolicy.PolicyMode.DAYS_AFTER_PERIOD_END:
            if allowed_days in (None, ""):
                self.add_error(
                    "allowed_days_after_period_end",
                    "Allowed days is required when using the days-after-period-end policy mode.",
                )
        else:
            cleaned["allowed_days_after_period_end"] = None

        if is_active and tenant and grading_period:
            canonical_period_key = _normalize_correction_policy_period_key(grading_period)
            self.instance.canonical_period_key = canonical_period_key
            duplicate_qs = CorrectionPetitionWindowPolicy.objects.filter(
                tenant=tenant,
                canonical_period_key=canonical_period_key,
                is_active=True,
            )
            if campus:
                duplicate_qs = duplicate_qs.filter(campus=campus)
            else:
                duplicate_qs = duplicate_qs.filter(campus__isnull=True)
            if academic_year:
                duplicate_qs = duplicate_qs.filter(academic_year=academic_year)
            else:
                duplicate_qs = duplicate_qs.filter(academic_year__isnull=True)
            if term:
                duplicate_qs = duplicate_qs.filter(term=term)
            else:
                duplicate_qs = duplicate_qs.filter(term__isnull=True)
            if self.instance and self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                self.add_error(
                    None,
                    "An active correction petition window policy already exists for this scope.",
                )

        return cleaned


class CorrectionApprovalRouteRuleForm(forms.ModelForm):
    step_1_role = forms.ModelChoiceField(
        queryset=Role.objects.none(),
        label="Step 1 approver role",
        help_text="Usually Area Chair / AC for department-level review.",
    )
    step_1_requires_same_department = forms.BooleanField(
        required=False,
        label="Step 1 requires department scope",
    )
    step_2_role = forms.ModelChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Step 2 approver role",
        help_text="Optional. Use College Dean / Dean when this department has a dean step.",
    )
    step_2_requires_same_department = forms.BooleanField(
        required=False,
        label="Step 2 requires department scope",
    )
    final_role_ordered = forms.ModelChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Final approver role",
        help_text="Usually CAO. Leave blank only when Step 1 is the final approver.",
    )
    final_requires_same_department_ordered = forms.BooleanField(
        required=False,
        label="Final approver requires department scope",
    )

    class Meta:
        model = CorrectionApprovalRouteRule
        fields = [
            "faculty_department",
            "step_1_role",
            "step_1_requires_same_department",
            "step_2_role",
            "step_2_requires_same_department",
            "final_role_ordered",
            "final_requires_same_department_ordered",
            "notes",
            "is_active",
        ]

    def __init__(
        self,
        *args,
        tenant=None,
        department_queryset=None,
        role_queryset=None,
        **kwargs,
    ):
        self.tenant = tenant
        super().__init__(*args, **kwargs)
        if department_queryset is not None:
            self.fields["faculty_department"].queryset = department_queryset
        if role_queryset is not None:
            self.fields["step_1_role"].queryset = role_queryset
            self.fields["step_2_role"].queryset = role_queryset
            self.fields["final_role_ordered"].queryset = role_queryset
        self.fields["faculty_department"].required = False
        self.fields["faculty_department"].help_text = "Leave blank to configure tenant default route."
        self._load_ordered_step_initials()
        _enforce_active_reference_choices(self)

    def _load_ordered_step_initials(self):
        if not self.instance or not self.instance.pk or self.is_bound:
            return
        ordered_steps = list(self.instance.ordered_steps.filter(is_active=True).order_by("step_order", "id"))
        if ordered_steps:
            self.fields["step_1_role"].initial = ordered_steps[0].approver_role_id
            self.fields["step_1_requires_same_department"].initial = ordered_steps[0].requires_same_department
            if len(ordered_steps) == 2:
                self.fields["final_role_ordered"].initial = ordered_steps[1].approver_role_id
                self.fields["final_requires_same_department_ordered"].initial = ordered_steps[1].requires_same_department
            elif len(ordered_steps) >= 3:
                self.fields["step_2_role"].initial = ordered_steps[1].approver_role_id
                self.fields["step_2_requires_same_department"].initial = ordered_steps[1].requires_same_department
                self.fields["final_role_ordered"].initial = ordered_steps[-1].approver_role_id
                self.fields["final_requires_same_department_ordered"].initial = ordered_steps[-1].requires_same_department
            return
        self.fields["step_1_role"].initial = self.instance.step1_role_id
        self.fields["step_1_requires_same_department"].initial = self.instance.step1_requires_same_department
        if self.instance.route_mode == CorrectionApprovalRouteRule.RouteMode.TWO_STEP:
            self.fields["final_role_ordered"].initial = self.instance.final_role_id
            self.fields["final_requires_same_department_ordered"].initial = self.instance.final_requires_same_department

    def clean(self):
        cleaned = super().clean()
        step1_role = cleaned.get("step_1_role")
        step2_role = cleaned.get("step_2_role")
        final_role = cleaned.get("final_role_ordered")
        faculty_department = cleaned.get("faculty_department")

        if not step1_role:
            self.add_error("step_1_role", "Step 1 approver role is required.")

        if step2_role and not final_role:
            self.add_error("final_role_ordered", "Final approver role is required when Step 2 is configured.")

        role_steps = [
            ("step_1_role", step1_role),
            ("step_2_role", step2_role),
            ("final_role_ordered", final_role),
        ]
        seen_role_ids = {}
        for field_name, role in role_steps:
            if not role:
                continue
            if role.id in seen_role_ids:
                self.add_error(
                    field_name,
                    "Each correction approval step must use a different approver role. "
                    "Leave later steps blank for a direct route.",
                )
                self.add_error(
                    seen_role_ids[role.id],
                    "Each correction approval step must use a different approver role.",
                )
            else:
                seen_role_ids[role.id] = field_name

        tenant = self.tenant or getattr(self.instance, "tenant", None)
        if tenant and faculty_department and faculty_department.tenant_id != tenant.id:
            self.add_error("faculty_department", "Faculty department must belong to the selected tenant scope.")

        return cleaned

    def _ordered_step_payload(self):
        cleaned = self.cleaned_data
        payload = [
            {
                "role": cleaned["step_1_role"],
                "requires_same_department": bool(cleaned.get("step_1_requires_same_department")),
            }
        ]
        if cleaned.get("step_2_role"):
            payload.append(
                {
                    "role": cleaned["step_2_role"],
                    "requires_same_department": bool(cleaned.get("step_2_requires_same_department")),
                }
            )
        if cleaned.get("final_role_ordered"):
            payload.append(
                {
                    "role": cleaned["final_role_ordered"],
                    "requires_same_department": bool(cleaned.get("final_requires_same_department_ordered")),
                }
            )
        return payload

    def save(self, commit=True):
        route = super().save(commit=False)
        payload = self._ordered_step_payload()
        route.step1_role = payload[0]["role"]
        route.step1_requires_same_department = payload[0]["requires_same_department"]
        route.route_mode = (
            CorrectionApprovalRouteRule.RouteMode.TWO_STEP
            if len(payload) > 1
            else CorrectionApprovalRouteRule.RouteMode.DIRECT_TO_FINAL
        )
        if len(payload) > 1:
            route.final_role = payload[-1]["role"]
            route.final_requires_same_department = payload[-1]["requires_same_department"]
        else:
            route.final_role = None
            route.final_requires_same_department = False
        if commit:
            route.save()
            self.save_ordered_steps(route)
        return route

    def save_ordered_steps(self, route):
        payload = self._ordered_step_payload()
        route.ordered_steps.all().delete()
        CorrectionApprovalRouteStep.objects.bulk_create(
            [
                CorrectionApprovalRouteStep(
                    route_rule=route,
                    step_order=index,
                    approver_role=row["role"],
                    approver_label=row["role"].name or row["role"].code,
                    requires_same_department=row["requires_same_department"],
                    is_active=True,
                )
                for index, row in enumerate(payload, start=1)
            ]
        )

class DocumentPrintSettingForm(forms.Form):
    school_name = forms.CharField(
        max_length=255,
        label="School Name",
        help_text="Used as the primary heading when printing tenant-specific documents such as the faculty grade book.",
    )
    school_address = forms.CharField(
        max_length=255,
        required=False,
        label="School Address",
        help_text="Printed below the school name. Leave blank if the tenant prefers not to show an address line.",
    )


class ConfigurableFeatureSettingForm(forms.Form):
    departmental_exam_builder_enabled = forms.BooleanField(
        required=False,
        label="Enable Departmental Exam Builder",
        help_text=(
            "Enables authorized examination-cycle management, grouped course administration, "
            "Included/Exempt course control, faculty question contribution, and aggregate-only "
            "contributor completion monitoring for this tenant."
        ),
    )
    student_academic_intervention_tracking_enabled = forms.BooleanField(
        required=False,
        label="Enable Student Academic Intervention Tracking",
        help_text="Allows authorized faculty to record academic-intervention decisions and authorized academic heads to monitor them read-only.",
    )
    academic_performance_insights_enabled = forms.BooleanField(
        required=False,
        label="Enable Academic Performance Insights",
        help_text=(
            "Allows authorized Area Chair, College Dean, and CAO users to open read-only section, "
            "course, activity-consistency, and campus comparisons within their assigned scope."
        ),
    )
    role_based_help_guide_enabled = forms.BooleanField(
        required=False,
        label="Use the revised role-based Help Guide",
        help_text=(
            "Shows practical guide topics based on the user's portal permissions. "
            "Turn this off to restore the previous Admin and Faculty guide pages."
        ),
    )
    grade_deadline_enforcement_policy = forms.ChoiceField(
        required=True,
        label="Grade deadline enforcement",
        choices=[
            (
                FeatureSettingsService.GRADE_DEADLINE_POLICY_AUTO_CLOSE_REQUIRES_REOPEN,
                "Enabled: Close encoding and require assigned reviewer approval after deadline",
            ),
            (
                FeatureSettingsService.GRADE_DEADLINE_POLICY_DISABLED,
                "Disabled: Do not close encoding automatically at the deadline",
            ),
        ],
        help_text=(
            "When enabled, both encoding and submission close at the deadline for unsubmitted gradebooks. "
            "Faculty must request reopening, and only a reviewer explicitly assigned by the Superadmin for that scope can approve it."
        ),
    )
    student_portal_enabled = forms.BooleanField(
        required=False,
        label="Enable Student Portal",
        help_text="Allows linked and permitted student users to open the read-only Student Portal for this tenant.",
    )
    student_portal_period_grades_after_submission = forms.BooleanField(
        required=False,
        label="Show period grades after submission",
        help_text="Shows only submitted official period grades in the Student Portal. Draft and reopened gradebooks stay hidden.",
    )
    student_portal_final_grades_after_submission = forms.BooleanField(
        required=False,
        label="Show final grade after submission",
        help_text="Shows only submitted official final grades in the Student Portal.",
    )
    student_portal_attendance_details_enabled = forms.BooleanField(
        required=False,
        label="Show attendance details",
        help_text="Shows session-level attendance records to linked student users. Summary counts remain read-only.",
    )
    sis_periodic_grades_api_enabled = forms.BooleanField(
        required=False,
        label="Enable SIS periodic grades API",
        help_text="Allows authorized third-party SIS/AIMS clients to pull submitted periodic grades for this tenant.",
    )
    correction_official_report_enabled = forms.BooleanField(
        required=False,
        label="Enable official correction PDF/report generation",
        help_text="When enabled, approved correction workflows may generate an official printable/exportable registrar reference document.",
    )
    user_signatures_enabled = forms.BooleanField(
        required=False,
        label="Enable encrypted user signatures",
        help_text="Allows portal users to upload and maintain an encrypted signature image in their own account profile.",
    )
    user_signatures_final_clearance_enabled = forms.BooleanField(
        required=False,
        label="Allow stored signatures on Faculty Final Clearance",
        help_text="When enabled, TeacherMate+ may place the generating faculty member's stored signature on the printed Final Clearance PDF.",
    )
    user_signatures_correction_report_enabled = forms.BooleanField(
        required=False,
        label="Allow stored signatures on Correction Official Report",
        help_text="When enabled, TeacherMate+ may place stored requester and approver signatures on the official correction PDF when those users have uploaded a signature.",
    )
    correction_submission_approval_email_enabled = forms.BooleanField(
        required=False,
        label="Enable approval notification email on correction submission",
        help_text="When enabled, TeacherMate+ emails the selected approval-role recipients as soon as a faculty member submits a petition for correction of grades.",
    )
    correction_submission_approval_email_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Recipient roles for correction submission notification",
        help_text="Select the approval roles that should receive the notification email. Recommended: CAO and College Dean.",
    )
    correction_registrar_auto_email_enabled = forms.BooleanField(
        required=False,
        label="Enable automatic registrar email after final approval",
        help_text="When enabled, TeacherMate+ may email the official correction PDF automatically after academic approval.",
    )
    correction_registrar_auto_email_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed to trigger automatic registrar email",
        help_text="Leave blank to allow any final approver role to trigger the automatic email when the feature is enabled.",
    )
    correction_registrar_default_recipients = forms.CharField(
        required=False,
        label="Default registrar recipient email(s)",
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Fallback recipient list used when a campus-specific branch email is not configured. Separate multiple emails with commas or new lines.",
    )
    faculty_assignment_reminders_enabled = forms.BooleanField(
        required=False,
        label="Enable faculty assignment reminder notifications",
        help_text="When enabled, pending faculty assignments can queue reminder notifications before the response deadline.",
    )
    faculty_assignment_auto_expire_enabled = forms.BooleanField(
        required=False,
        label="Enable automatic expiration of overdue faculty assignments",
        help_text="When enabled, pending faculty assignments automatically move to expired status after the response deadline.",
    )
    faculty_assignment_primary_default_enabled = forms.BooleanField(
        required=False,
        label="Set new faculty assignments as primary by default",
        help_text="When enabled, new faculty assignments start as primary. Admins may still change the primary tag manually afterward.",
    )
    faculty_reminder_center_enabled = forms.BooleanField(
        required=False,
        label="Enable faculty reminder center",
        help_text="Shows the faculty reminder center page and related reminder actions in the faculty portal.",
    )
    faculty_reminder_email_enabled = forms.BooleanField(
        required=False,
        label="Enable faculty reminder email queue",
        help_text="Queues reminder emails in the background when faculty reminders become due.",
    )
    faculty_memo_center_enabled = forms.BooleanField(
        required=False,
        label="Enable faculty memo center",
        help_text="Shows a private notes/memo area for faculty to keep class and student reminders inside the portal.",
    )
    faculty_quick_tour_enabled = forms.BooleanField(
        required=False,
        label="Enable faculty portal quick tour",
        help_text="Shows the guided callout tour for faculty users who have not disabled it on their account.",
    )
    faculty_quick_score_encoding_enabled = forms.BooleanField(
        required=False,
        label="Enable faculty quick score encoding",
        help_text=(
            "Adds keyboard navigation, single-column paste, autofocus, and an unsaved indicator to the existing "
            "Faculty Portal score encoding page. Turning this off restores the standard score-entry behavior."
        ),
    )
    exit_pulse_enabled = forms.BooleanField(
        required=False,
        label="Enable Exit Pulse",
        help_text=(
            "Lets assigned faculty run five-minute anonymous classroom Learning Checks. "
            "Exit Pulse does not affect attendance, grades, or faculty evaluation."
        ),
    )
    orientation_feedback_enabled = forms.BooleanField(
        required=False,
        label="Enable Orientation Feedback Surveys",
        help_text=(
            "Allows authorized Admin Portal users to run identity-validated Faculty and Academic Heads "
            "orientation surveys with aggregate results reported without names."
        ),
    )
    submission_non_compliance_notice_enabled = forms.BooleanField(
        required=False,
        label="Enable non-compliance notices for overdue grade submissions",
        help_text=(
            "When enabled, TeacherMate+ can issue up to three overdue notices: faculty only, "
            "faculty plus Area Chair, then faculty plus Area Chair plus CAO."
        ),
    )
    submission_readiness_email_enabled = forms.BooleanField(required=False, label="Enable submission readiness email alerts")
    submission_readiness_email_days_before = forms.IntegerField(required=False, min_value=0, max_value=365, initial=5, label="Days before deadline")
    submission_readiness_email_threshold = forms.IntegerField(required=False, min_value=0, max_value=100, initial=50, label="Readiness threshold (%)")
    submission_readiness_email_roles = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple, choices=[("AREA_CHAIR", "Area Chair"), ("COLLEGE_DEAN", "College Dean"), ("CAO", "Chief Academic Officer")], label="Recipient roles")
    submission_readiness_email_send_empty = forms.BooleanField(
        required=False,
        disabled=True,
        label="Send empty reports",
        help_text="Disabled for this exception-only workflow. Emails are sent only when matching assignments exist.",
    )
    submission_readiness_email_include_link = forms.BooleanField(required=False, label="Include Submission Readiness dashboard link")
    submission_readiness_email_repeat = forms.BooleanField(required=False, label="Allow repeat reminders")
    submission_non_compliance_notice_interval_days = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=1,
        initial=1,
        label="Scheduler cadence",
        help_text=(
            "How often TeacherMate+ checks for overdue gradebooks. This does not directly "
            "determine the notice escalation day."
        ),
    )
    submission_non_compliance_first_notice_after_days = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        label="First notice after deadline",
        help_text="Number of days after the deadline before the first notice is sent.",
    )
    submission_non_compliance_level_interval_days = forms.IntegerField(
        required=False,
        min_value=1,
        initial=1,
        label="Notice interval",
        help_text="Number of days between notice levels.",
    )
    submission_non_compliance_max_notice_count = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=3,
        initial=3,
        label="Maximum notices",
        help_text="TeacherMate+ stops automatic notices after this count. Current NCBA policy uses 3 notices.",
    )
    submission_non_compliance_head_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Academic head roles for visibility and escalation",
        help_text=(
            "Legacy visibility setting. The NCBA email cadence uses scoped Area Chair recipients on "
            "the second notice and scoped CAO recipients on the third notice."
        ),
    )
    submission_non_compliance_hr_recipients = forms.CharField(
        required=False,
        label="HR escalation recipient email(s)",
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=(
            "Legacy field kept for existing settings. The current NCBA three-notice policy does not "
            "send automatic overdue-gradebook notices to HR."
        ),
    )
    grade_distribution_high_grade_band_min = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        decimal_places=2,
        label="High grade band minimum",
        help_text="Lowest grade included in the high-grade concentration band. Default: 90.",
    )
    grade_distribution_high_grade_band_max = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        decimal_places=2,
        label="High grade band maximum",
        help_text="Highest grade included in the high-grade concentration band. Default: 100.",
    )
    grade_distribution_high_grade_concentration_threshold_percent = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        decimal_places=2,
        label="High grade concentration threshold (%)",
        help_text="Percent of graded students in the high-grade band before a row is marked for review. Default: 75.",
    )
    grade_distribution_exact_100_threshold_percent = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        decimal_places=2,
        label="Exact 100 threshold (%)",
        help_text="Percent of exact 100 grades before a row is marked for review. Default: 30.",
    )
    grade_distribution_low_variation_threshold = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        label="Low variation threshold",
        help_text="Grade spread at or below this value is marked as low variation. Default: 5.",
    )
    grade_distribution_minimum_student_count_for_flag = forms.IntegerField(
        required=False,
        min_value=1,
        label="Minimum student count for review flags",
        help_text="Rows below this count show Small Sample instead of review flags. Default: 10.",
    )
    enrollment_ownership_mode = forms.ChoiceField(
        required=True,
        label="Class master list ownership mode",
        choices=[
            (EnrollmentService.ADMIN_ONLY, "Admin Only"),
            (EnrollmentService.FACULTY_ALLOWED, "Faculty Allowed"),
        ],
        help_text="Controls whether faculty may maintain the class master list for their own assigned classes or whether roster maintenance stays admin-only.",
    )
    enrollment_student_mode = forms.ChoiceField(
        required=False,
        label="Enrollment import student handling",
        choices=[
            (BulkImportService.ENROLLMENT_STUDENT_MODE_STRICT, "Require existing students"),
            (BulkImportService.ENROLLMENT_STUDENT_MODE_AUTO_CREATE, "Auto-create missing students"),
        ],
        help_text="Controls whether enrollment CSV uploads reject missing student_no values or create student records from the student name columns.",
    )
    faculty_drp_allowed_through_period = forms.ChoiceField(
        required=False,
        label="Faculty DRP allowed through",
        choices=EnrollmentService.FACULTY_DRP_PERIOD_CHOICES,
        help_text="Controls the latest active grading period where assigned faculty may newly mark a student as DRP. Default: Through Pre-Final.",
    )
    class_master_list_term = forms.ModelChoiceField(
        queryset=Term.objects.none(),
        required=False,
        label="Term for class override",
        help_text="Choose the term first so TeacherMate+ can list only the classes under the selected tenant, campus, and term.",
    )
    class_master_list_faculty = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Faculty filter",
        help_text="Optional. Select a faculty member first if you want to show only classes currently assigned to that faculty. Leave blank for no faculty filtering.",
    )
    class_master_list_offering = forms.ModelMultipleChoiceField(
        queryset=CourseOffering.objects.none(),
        required=False,
        label="Class override target",
        help_text="Optional. Select one or more classes if you want to override the tenant default only for those selected offerings.",
    )
    class_master_list_override_mode = forms.ChoiceField(
        required=False,
        label="Selected class override mode",
        choices=[
            ("", "Use tenant default"),
            (EnrollmentService.ADMIN_ONLY, "Admin Only"),
            (EnrollmentService.FACULTY_ALLOWED, "Faculty Allowed"),
        ],
        help_text="Use this only when one class needs a different class master list rule from the tenant-wide default above.",
    )
    login_lockout_enabled = forms.BooleanField(
        required=False,
        label="Enable login lockout after repeated failed attempts",
        help_text="Temporarily blocks Admin Portal and Faculty Portal sign-in after too many failed password attempts.",
    )
    login_lockout_max_attempts = forms.IntegerField(
        required=True,
        min_value=1,
        label="Maximum failed login attempts",
        help_text="How many failed sign-in attempts are allowed before the account is temporarily locked for that portal.",
    )
    login_lockout_window_minutes = forms.IntegerField(
        required=True,
        min_value=1,
        label="Failure counting window (minutes)",
        help_text="Only failed attempts inside this rolling window are counted toward lockout.",
    )
    login_lockout_duration_minutes = forms.IntegerField(
        required=True,
        min_value=1,
        label="Lockout duration (minutes)",
        help_text="How long the temporary lockout stays active before the user can try again.",
    )
    login_email_otp_enabled = forms.BooleanField(
        required=False,
        label="Enable email OTP during login",
        help_text="Requires users to enter a one-time code sent to their registered email after a correct password.",
    )
    login_email_otp_expiry_minutes = forms.IntegerField(
        required=False,
        min_value=1,
        label="Email OTP expiry (minutes)",
        help_text="How long a login verification code remains valid.",
    )
    single_device_session_enforcement_enabled = forms.BooleanField(
        required=False,
        label="Allow only one active login session per user",
        help_text="When enabled, a new login signs out the same user from any other browser or device.",
    )
    session_timeout_minutes = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=1440,
        label="Session timeout (minutes)",
        help_text="How long an authenticated Admin or Faculty Portal session may stay active between requests.",
    )
    faculty_assignment_response_window_days = forms.IntegerField(
        required=True,
        min_value=1,
        label="Faculty assignment response window (days)",
        help_text="How many days faculty have to accept, request clarification, or decline a newly assigned load.",
    )
    faculty_assignment_first_reminder_days = forms.IntegerField(
        required=True,
        min_value=0,
        label="First reminder after assignment (days)",
        help_text="How many days after assignment before the first reminder is queued. Use 0 for same-day reminders.",
    )
    faculty_assignment_repeat_reminder_days = forms.IntegerField(
        required=True,
        min_value=1,
        label="Repeat reminder interval (days)",
        help_text="How many days between follow-up reminders while the assignment is still pending.",
    )
    grade_prediction_enabled = forms.BooleanField(
        required=False,
        label="Enable grade prediction module",
        help_text="Turns the prediction module on or off without affecting the official gradebook.",
    )
    grade_prediction_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed to access grade prediction",
        help_text="Select which roles may open prediction pages once the feature is enabled.",
    )
    grade_prediction_what_if_enabled = forms.BooleanField(
        required=False,
        label="Enable what-if simulator",
        help_text="Allows approved roles to run unofficial scenarios on remaining graded work.",
    )
    grade_prediction_what_if_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed to use what-if simulator",
        help_text="Select which roles may run what-if simulations.",
    )
    grade_prediction_at_risk_enabled = forms.BooleanField(
        required=False,
        label="Enable at-risk flags",
        help_text="Highlights students whose projected grade is below the passing threshold.",
    )
    grade_prediction_show_best_case = forms.BooleanField(
        required=False,
        label="Show best-case projection",
    )
    grade_prediction_show_worst_case = forms.BooleanField(
        required=False,
        label="Show worst-case projection",
    )
    grade_prediction_show_target_needed = forms.BooleanField(
        required=False,
        label="Show target-needed calculation",
    )
    grade_prediction_default_assumption = forms.ChoiceField(
        required=False,
        label="Default prediction assumption",
        choices=[
            ("IGNORE_MISSING", "Ignore Missing"),
            ("RAW_ZERO", "Assume Zero Raw Score"),
            ("FULL_SCORE", "Assume Full Score"),
        ],
        help_text="Controls the primary projected grade shown on prediction tables.",
    )
    faculty_official_period_grades_after_deadline = forms.BooleanField(
        required=False,
        label="Restrict official periodic grades until period deadline",
        help_text="When turned on, official computed period grades such as PG, MG, and PFG stay hidden from faculty until the deadline of that specific period has already passed. When turned off, they remain visible by default.",
    )
    faculty_official_period_grades_after_submission = forms.BooleanField(
        required=False,
        label="Mask official periodic grade until submission",
        help_text="When turned on, faculty may continue viewing the complete gradebook and supporting scores, but the official computed grade for the selected period, such as PG, MG, PFG, or FX, remains hidden until that period gradebook is submitted.",
    )
    faculty_official_final_grades_after_deadline = forms.BooleanField(
        required=False,
        label="Restrict official final grade until final deadline",
        help_text="When turned on, the official computed final grade stays hidden from faculty until the final grading-period deadline has already passed. When turned off, it remains visible by default.",
    )

    def __init__(
        self,
        *args,
        role_queryset=None,
        campus_queryset=None,
        campus_initial_map=None,
        term_queryset=None,
        faculty_queryset=None,
        offering_queryset=None,
        **kwargs,
    ):
        self.campus_fields = []
        self.campus_queryset = campus_queryset
        campus_initial_map = campus_initial_map or {}
        super().__init__(*args, **kwargs)
        if role_queryset is not None:
            self.fields["correction_submission_approval_email_roles"].queryset = role_queryset
            self.fields["correction_registrar_auto_email_roles"].queryset = role_queryset
            self.fields["submission_non_compliance_head_roles"].queryset = role_queryset
            self.fields["grade_prediction_roles"].queryset = role_queryset
            self.fields["grade_prediction_what_if_roles"].queryset = role_queryset
        if term_queryset is not None:
            self.fields["class_master_list_term"].queryset = term_queryset
        if faculty_queryset is not None:
            self.fields["class_master_list_faculty"].queryset = faculty_queryset
        if offering_queryset is not None:
            self.fields["class_master_list_offering"].queryset = offering_queryset
        _enforce_active_reference_choices(self)
        _set_choice_label(self.fields.get("class_master_list_term"), _term_label)
        _set_choice_label(self.fields.get("class_master_list_faculty"), _faculty_label)
        _set_choice_label(self.fields.get("class_master_list_offering"), _offering_label)
        self.fields["class_master_list_offering"].widget.attrs["size"] = 8

        for campus in campus_queryset or []:
            field_name = f"campus_recipient_{campus.id}"
            self.fields[field_name] = forms.CharField(
                required=False,
                label=f"{campus.code} registrar recipient email(s)",
                widget=forms.Textarea(attrs={"rows": 2}),
                help_text="Used for approved correction PDFs for this campus/branch. Separate multiple emails with commas or new lines.",
                initial=", ".join(campus_initial_map.get(str(campus.id), [])),
            )
            self.campus_fields.append((field_name, campus))

    @staticmethod
    def _parse_email_list(raw_value: str) -> list[str]:
        chunks = []
        for piece in (raw_value or "").replace("\r", "\n").replace(",", "\n").split("\n"):
            cleaned = piece.strip()
            if cleaned:
                chunks.append(cleaned)
        return chunks

    def non_compliance_schedule_preview(self) -> str:
        def _field_int(field_name: str, default: int) -> int:
            raw_value = None
            if self.is_bound:
                raw_value = self.data.get(self.add_prefix(field_name))
            if raw_value in (None, ""):
                raw_value = self.initial.get(field_name, default)
            try:
                return max(int(raw_value), 1)
            except (TypeError, ValueError):
                return default

        first_day = _field_int("submission_non_compliance_first_notice_after_days", 1)
        interval = _field_int("submission_non_compliance_level_interval_days", 1)
        max_count = min(_field_int("submission_non_compliance_max_notice_count", 3), 3)
        parts = [
            f"Notice {sequence_no} on Day {first_day + ((sequence_no - 1) * interval)}"
            for sequence_no in range(1, max_count + 1)
        ]
        return "Current schedule: " + ", ".join(parts) + "."

    def clean(self):
        cleaned = super().clean()

        if not cleaned.get("user_signatures_enabled"):
            cleaned["user_signatures_final_clearance_enabled"] = False
            cleaned["user_signatures_correction_report_enabled"] = False

        if (
            cleaned.get("correction_submission_approval_email_enabled")
            and not cleaned.get("correction_submission_approval_email_roles")
        ):
            self.add_error(
                "correction_submission_approval_email_roles",
                "Select at least one recipient role before enabling approval notification email.",
            )

        parsed_default = self._parse_email_list(cleaned.get("correction_registrar_default_recipients", ""))
        for email in parsed_default:
            validate_email(email)
        cleaned["correction_registrar_default_recipient_list"] = parsed_default

        parsed_hr_recipients = self._parse_email_list(cleaned.get("submission_non_compliance_hr_recipients", ""))
        for email in parsed_hr_recipients:
            try:
                validate_email(email)
            except DjangoValidationError:
                self.add_error("submission_non_compliance_hr_recipients", f"{email} is not a valid email address.")
        cleaned["submission_non_compliance_hr_recipient_list"] = parsed_hr_recipients

        campus_recipient_map = {}
        for field_name, campus in self.campus_fields:
            parsed_emails = self._parse_email_list(cleaned.get(field_name, ""))
            for email in parsed_emails:
                try:
                    validate_email(email)
                except DjangoValidationError:
                    self.add_error(field_name, f"{email} is not a valid email address.")
            if parsed_emails:
                campus_recipient_map[str(campus.id)] = parsed_emails

        if cleaned.get("correction_registrar_auto_email_enabled"):
            if not parsed_default and not campus_recipient_map:
                self.add_error(
                    "correction_registrar_default_recipients",
                    "Provide a default registrar recipient or at least one campus-specific recipient before enabling automatic email.",
                )

        response_window_days = cleaned.get("faculty_assignment_response_window_days")
        first_reminder_days = cleaned.get("faculty_assignment_first_reminder_days")
        repeat_reminder_days = cleaned.get("faculty_assignment_repeat_reminder_days")
        if (
            response_window_days is not None
            and first_reminder_days is not None
            and first_reminder_days > response_window_days
        ):
            self.add_error(
                "faculty_assignment_first_reminder_days",
                "First reminder day cannot be later than the assignment response window.",
            )
        if (
            response_window_days is not None
            and repeat_reminder_days is not None
            and repeat_reminder_days > response_window_days
        ):
            self.add_error(
                "faculty_assignment_repeat_reminder_days",
                "Repeat reminder interval should not be longer than the assignment response window.",
            )

        if cleaned.get("grade_prediction_enabled") and not cleaned.get("grade_prediction_roles"):
            self.add_error(
                "grade_prediction_roles",
                "Select at least one role before enabling grade prediction.",
            )
        if cleaned.get("grade_prediction_what_if_enabled") and not cleaned.get("grade_prediction_what_if_roles"):
            self.add_error(
                "grade_prediction_what_if_roles",
                "Select at least one role before enabling the what-if simulator.",
            )
        if not cleaned.get("grade_prediction_default_assumption"):
            cleaned["grade_prediction_default_assumption"] = "IGNORE_MISSING"

        if not cleaned.get("faculty_reminder_center_enabled"):
            cleaned["faculty_reminder_center_enabled"] = False
        if not cleaned.get("faculty_reminder_email_enabled"):
            cleaned["faculty_reminder_email_enabled"] = False
        if not cleaned.get("faculty_memo_center_enabled"):
            cleaned["faculty_memo_center_enabled"] = False
        if not cleaned.get("faculty_quick_tour_enabled"):
            cleaned["faculty_quick_tour_enabled"] = False
        if not cleaned.get("faculty_quick_score_encoding_enabled"):
            cleaned["faculty_quick_score_encoding_enabled"] = False
        if not cleaned.get("exit_pulse_enabled"):
            cleaned["exit_pulse_enabled"] = False
        if not cleaned.get("submission_non_compliance_notice_enabled"):
            cleaned["submission_non_compliance_notice_enabled"] = False
        if cleaned.get("submission_readiness_email_days_before") is None:
            cleaned["submission_readiness_email_days_before"] = 5
        if cleaned.get("submission_readiness_email_threshold") is None:
            cleaned["submission_readiness_email_threshold"] = 50
        cleaned["submission_readiness_email_send_empty"] = False
        if cleaned.get("submission_readiness_email_enabled") and not cleaned.get("submission_readiness_email_roles"):
            self.add_error("submission_readiness_email_roles", "Select at least one recipient role when alerts are enabled.")
        cleaned["submission_non_compliance_notice_interval_days"] = 1
        if cleaned.get("submission_non_compliance_first_notice_after_days") is None:
            cleaned["submission_non_compliance_first_notice_after_days"] = 1
        if cleaned.get("submission_non_compliance_level_interval_days") is None:
            cleaned["submission_non_compliance_level_interval_days"] = 1
        if cleaned.get("submission_non_compliance_max_notice_count") is None:
            cleaned["submission_non_compliance_max_notice_count"] = 3
        grade_distribution_defaults = {
            "grade_distribution_high_grade_band_min": 90,
            "grade_distribution_high_grade_band_max": 100,
            "grade_distribution_high_grade_concentration_threshold_percent": 75,
            "grade_distribution_exact_100_threshold_percent": 30,
            "grade_distribution_low_variation_threshold": 5,
            "grade_distribution_minimum_student_count_for_flag": 10,
        }
        for field_name, default_value in grade_distribution_defaults.items():
            if cleaned.get(field_name) is None:
                cleaned[field_name] = default_value
        high_min = cleaned.get("grade_distribution_high_grade_band_min")
        high_max = cleaned.get("grade_distribution_high_grade_band_max")
        if high_min is not None and high_max is not None and high_min > high_max:
            self.add_error(
                "grade_distribution_high_grade_band_min",
                "High grade band minimum cannot be greater than the maximum.",
            )
        if not cleaned.get("enrollment_ownership_mode"):
            cleaned["enrollment_ownership_mode"] = EnrollmentService.ADMIN_ONLY
        if not cleaned.get("enrollment_student_mode"):
            cleaned["enrollment_student_mode"] = BulkImportService.ENROLLMENT_STUDENT_MODE_STRICT
        if not cleaned.get("faculty_drp_allowed_through_period"):
            cleaned["faculty_drp_allowed_through_period"] = EnrollmentService.PERIOD_PREFINAL
        if not cleaned.get("class_master_list_override_mode"):
            cleaned["class_master_list_override_mode"] = ""
        if not cleaned.get("login_lockout_enabled"):
            cleaned["login_lockout_enabled"] = False
        if not cleaned.get("login_email_otp_enabled"):
            cleaned["login_email_otp_enabled"] = False
        if cleaned.get("login_email_otp_expiry_minutes") is None:
            cleaned["login_email_otp_expiry_minutes"] = 10
        if not cleaned.get("single_device_session_enforcement_enabled"):
            cleaned["single_device_session_enforcement_enabled"] = False
        if cleaned.get("session_timeout_minutes") is None:
            cleaned["session_timeout_minutes"] = 60

        selected_term = cleaned.get("class_master_list_term")
        selected_offerings = cleaned.get("class_master_list_offering")
        selected_faculty = cleaned.get("class_master_list_faculty")
        if selected_offerings and not selected_term:
            self.add_error(
                "class_master_list_term",
                "Select the term first before choosing a class override target.",
            )
        if selected_offerings and selected_term:
            invalid_offering = next(
                (offering for offering in selected_offerings if offering.term_id != selected_term.id),
                None,
            )
            if invalid_offering:
                self.add_error(
                    "class_master_list_offering",
                    "One or more selected classes do not belong to the selected term.",
                )
        if selected_faculty and selected_offerings:
            invalid_faculty_offering = next(
                (
                    offering
                    for offering in selected_offerings
                    if not offering.faculty_assignments.filter(
                        faculty_user_id=selected_faculty.id,
                        is_active=True,
                    ).exists()
                ),
                None,
            )
            if invalid_faculty_offering:
                self.add_error(
                    "class_master_list_offering",
                    "One or more selected classes do not belong to the selected faculty filter.",
                )
        if cleaned.get("class_master_list_override_mode") and not selected_offerings:
            self.add_error(
                "class_master_list_offering",
                "Select at least one class before applying a class-level ownership override.",
            )

        if (
            cleaned.get("login_lockout_enabled")
            and cleaned.get("login_lockout_max_attempts") is not None
            and cleaned.get("login_lockout_max_attempts") < 2
        ):
            self.add_error(
                "login_lockout_max_attempts",
                "Use at least 2 failed attempts so a single typo does not lock a user immediately.",
            )

        cleaned["correction_registrar_campus_recipient_map"] = campus_recipient_map
        return cleaned


class StudentAccountLinkForm(forms.ModelForm):
    class Meta:
        model = StudentAccountLink
        fields = ["tenant", "campus", "student", "user", "is_active", "notes"]

    def __init__(
        self,
        *args,
        tenant_queryset=None,
        campus_queryset=None,
        student_queryset=None,
        user_queryset=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if student_queryset is not None:
            self.fields["student"].queryset = student_queryset
        if user_queryset is not None:
            self.fields["user"].queryset = user_queryset
        _enforce_active_reference_choices(self)
        _set_choice_label(self.fields.get("student"), _student_label)
        _set_choice_label(self.fields.get("user"), _faculty_label)
        self.fields["notes"].required = False
        self.fields["notes"].widget.attrs["rows"] = 3
        self.fields["is_active"].help_text = "Deactivate links instead of deleting them so account-link history remains reviewable."

    def clean(self):
        cleaned = super().clean()
        instance = self.instance
        for field_name in ("tenant", "campus", "student", "user", "is_active", "notes"):
            if field_name in cleaned:
                setattr(instance, field_name, cleaned[field_name])
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise exc
        return cleaned


class StudentAccountProvisioningForm(forms.Form):
    student = forms.ModelChoiceField(
        queryset=Student.objects.none(),
        label="Student",
        help_text="Select the existing student record to provision for Student Portal access.",
    )
    existing_user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        label="Existing user account",
        help_text="Optional. Leave blank to create or match a user from the student's official email.",
    )
    verify_official_email = forms.BooleanField(
        required=False,
        label="I confirm the official student email has been verified",
        help_text="Required when the student record does not already have an email verification timestamp.",
    )
    notes = forms.CharField(
        required=False,
        label="Notes",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, student_queryset=None, user_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if student_queryset is not None:
            self.fields["student"].queryset = student_queryset
        if user_queryset is not None:
            self.fields["existing_user"].queryset = user_queryset
        _enforce_active_reference_choices(self)
        _set_choice_label(self.fields.get("student"), _student_label)
        _set_choice_label(self.fields.get("existing_user"), _faculty_label)

    def clean_student(self):
        student = self.cleaned_data["student"]
        if not student.official_email:
            raise forms.ValidationError("This student has no official email. Update the student record first.")
        return student

    def clean(self):
        cleaned = super().clean()
        student = cleaned.get("student")
        existing_user = cleaned.get("existing_user")
        if student and existing_user:
            if existing_user.default_tenant_id and existing_user.default_tenant_id != student.tenant_id:
                self.add_error("existing_user", "Existing user default tenant does not match the selected student.")
            if existing_user.default_campus_id and existing_user.default_campus_id != student.campus_id:
                self.add_error("existing_user", "Existing user default campus does not match the selected student.")
        if student and not student.official_email_verified_at and not cleaned.get("verify_official_email"):
            self.add_error("verify_official_email", "Confirm email verification before provisioning this student.")
        return cleaned


class TemplateGovernanceSettingForm(forms.Form):
    draft_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed to create and edit draft templates",
        help_text="These roles may prepare draft templates and adjust draft structure before submission.",
        widget=forms.CheckboxSelectMultiple,
    )
    submit_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed to submit templates for approval",
        help_text="These roles may move a draft template into the approval queue.",
        widget=forms.CheckboxSelectMultiple,
    )
    approval_review_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed to review template approval",
        help_text="These roles may approve or reject a submitted template in Phase 1.",
        widget=forms.CheckboxSelectMultiple,
    )
    publish_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed to publish approved templates",
        help_text="Publishing activates the template for use by course offerings.",
        widget=forms.CheckboxSelectMultiple,
    )
    hotfix_request_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed to request a template hotfix",
        help_text="These roles may open a hotfix request on a published template.",
        widget=forms.CheckboxSelectMultiple,
    )
    hotfix_review_apply_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed to review and apply a hotfix",
        help_text="In Phase 1, this role performs the approve/apply step for hotfixes.",
        widget=forms.CheckboxSelectMultiple,
    )
    sequential_approval_enabled = forms.BooleanField(
        required=False,
        label="Use a sequential template approval chain",
        help_text="When enabled, TeacherMate+ will require Template Review first, then Final Approval.",
    )
    approval_review_step_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed for Template Review",
        help_text="Step 1 of the Phase 2 approval chain. These roles can review and endorse the template forward.",
        widget=forms.CheckboxSelectMultiple,
    )
    approval_final_step_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed for Final Approval",
        help_text="Step 2 of the Phase 2 approval chain. These roles issue the final approval or rejection.",
        widget=forms.CheckboxSelectMultiple,
    )
    sequential_hotfix_enabled = forms.BooleanField(
        required=False,
        label="Use a sequential hotfix workflow",
        help_text="When enabled, TeacherMate+ will require Hotfix Review first, then Hotfix Final Apply.",
    )
    hotfix_review_step_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed for Hotfix Review",
        help_text="Step 1 of the hotfix chain. These roles review the requested change before final application.",
        widget=forms.CheckboxSelectMultiple,
    )
    hotfix_apply_step_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.none(),
        required=False,
        label="Roles allowed for Hotfix Final Apply",
        help_text="Step 2 of the hotfix chain. These roles perform the final approve/apply or rejection step.",
        widget=forms.CheckboxSelectMultiple,
    )
    require_approval_before_publish = forms.BooleanField(
        required=False,
        label="Require approval before publish",
        help_text="When disabled, authorized publishers may publish a valid template directly without a prior approval step.",
    )
    allow_same_user_submit_review = forms.BooleanField(
        required=False,
        label="Allow the same user to submit and review",
        help_text="Turn on only if the same governance role is allowed to both submit a template and approve/reject it.",
    )
    allow_same_user_review_approve = forms.BooleanField(
        required=False,
        label="Allow the same user to review and final-approve",
        help_text="When sequential approval is enabled, this lets the same user complete both review and final approval.",
    )
    allow_same_user_review_publish = forms.BooleanField(
        required=False,
        label="Allow the same user to review and publish",
        help_text="Turn on only if the same role is trusted to approve a template and then publish it immediately.",
    )
    allow_same_user_hotfix_request_apply = forms.BooleanField(
        required=False,
        label="Allow the same user to request and apply a hotfix",
        help_text="Turn on only if hotfix requestors may also perform the review/apply step themselves.",
    )
    allow_same_user_hotfix_review_apply = forms.BooleanField(
        required=False,
        label="Allow the same user to review and final-apply a hotfix",
        help_text="When sequential hotfix is enabled, this lets the same user complete both the hotfix review and final apply steps.",
    )

    STAGE_FIELDS = (
        "draft_roles",
        "submit_roles",
        "approval_review_roles",
        "publish_roles",
        "hotfix_request_roles",
        "hotfix_review_apply_roles",
    )

    def __init__(self, *args, role_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if role_queryset is not None:
            for field_name in self.STAGE_FIELDS:
                self.fields[field_name].queryset = role_queryset
            for field_name in (
                "approval_review_step_roles",
                "approval_final_step_roles",
                "hotfix_review_step_roles",
                "hotfix_apply_step_roles",
            ):
                self.fields[field_name].queryset = role_queryset
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        for field_name in self.STAGE_FIELDS:
            if not cleaned.get(field_name):
                self.add_error(field_name, "Select at least one role for this workflow stage.")

        if (
            cleaned.get("require_approval_before_publish")
            and not cleaned.get("approval_review_roles")
        ):
            self.add_error(
                "approval_review_roles",
                "Select at least one approval-review role when publish requires approval first.",
            )

        if not cleaned.get("require_approval_before_publish") and not cleaned.get("publish_roles"):
            self.add_error(
                "publish_roles",
                "Select at least one publish role when direct publishing is allowed.",
            )

        if cleaned.get("sequential_approval_enabled"):
            if not cleaned.get("approval_review_step_roles"):
                self.add_error(
                    "approval_review_step_roles",
                    "Select at least one role for the Template Review step.",
                )
            if not cleaned.get("approval_final_step_roles"):
                self.add_error(
                    "approval_final_step_roles",
                    "Select at least one role for the Final Approval step.",
                )

        if cleaned.get("sequential_hotfix_enabled"):
            if not cleaned.get("hotfix_review_step_roles"):
                self.add_error(
                    "hotfix_review_step_roles",
                    "Select at least one role for the Hotfix Review step.",
                )
            if not cleaned.get("hotfix_apply_step_roles"):
                self.add_error(
                    "hotfix_apply_step_roles",
                    "Select at least one role for the Hotfix Final Apply step.",
                )

        return cleaned
