from __future__ import annotations

import json
from decimal import Decimal

from django import forms
from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email

from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.settings import SystemSettingService
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CourseBaseValueOverride,
    CourseTemplateAssignment,
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
from apps.navigation.models import MenuGroup, MenuItem
from apps.rbac.models import Permission, Role, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


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


def _term_label(obj):
    name = (getattr(obj, "name", "") or "").strip()
    code = (getattr(obj, "code", "") or "").strip()
    academic_year = getattr(obj, "academic_year", None)
    ay_name = (getattr(academic_year, "name", "") or getattr(academic_year, "code", "") or "").strip()
    primary = name or code or str(obj)
    if ay_name:
        return f"{primary} - {ay_name}"
    return primary


def _academic_year_label(obj):
    name = (getattr(obj, "name", "") or "").strip()
    code = (getattr(obj, "code", "") or "").strip()
    if name and code and name != code:
        return f"{name} ({code})"
    return name or code or str(obj)


def _offering_label(obj):
    course_label = _course_label(getattr(obj, "course", None))
    section_label = _section_label(getattr(obj, "section", None))
    term_label = _term_label(getattr(obj, "term", None))
    return f"{course_label} | {section_label} | {term_label}"


def _faculty_label(obj):
    full_name = (getattr(obj, "full_name", "") or "").strip()
    username = (getattr(obj, "username", "") or "").strip()
    if full_name and username and full_name != username:
        return f"{full_name} ({username})"
    return full_name or username or str(obj)


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
        fields = ["tenant", "campus", "code", "name", "is_active"]

    def __init__(self, *args, tenant_queryset=None, campus_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        _enforce_active_reference_choices(self)


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
        if department_queryset is not None:
            self.fields["department"].queryset = department_queryset
        _enforce_active_reference_choices(self)


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
            "is_staff",
        ]

    def __init__(self, *args, tenant_queryset=None, campus_queryset=None, department_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["default_tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["default_campus"].queryset = campus_queryset
        if department_queryset is not None:
            self.fields["default_department"].queryset = department_queryset
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
        if department_queryset is not None:
            self.fields["default_department"].queryset = department_queryset
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
        self.fields["code"].help_text = "Use a stable short code for imports and references (example: AY2526)."
        self.fields["name"].help_text = "Human-readable label (example: Academic Year 2025-2026)."
        _enforce_active_reference_choices(self)


class TermForm(forms.ModelForm):
    class Meta:
        model = Term
        fields = ["tenant", "academic_year", "code", "name", "sequence_no", "start_date", "end_date", "is_active"]

    def __init__(self, *args, tenant_queryset=None, academic_year_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if academic_year_queryset is not None:
            self.fields["academic_year"].queryset = academic_year_queryset
        self.fields["code"].help_text = "Use short term code used in imports (example: 1ST, 2ND)."
        self.fields["name"].help_text = "Readable label (example: 1st Semester 2025-2026)."
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
            "code",
            "title",
            "units",
            "course_type",
            "default_base_value",
            "is_active",
        ]

    def __init__(self, *args, tenant_queryset=None, campus_queryset=None, department_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        if campus_queryset is not None:
            self.fields["campus"].queryset = campus_queryset
        if department_queryset is not None:
            self.fields["department"].queryset = department_queryset
        department_field = self.fields["department"]
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
        department_field.help_text = (
            "Optional. Select the campus first to load only that campus' departments. "
            "Leave both campus and department blank for tenant-wide shared course definitions."
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
        if department and not campus:
            raise forms.ValidationError("Department requires a campus. Leave both blank for tenant-wide shared course.")
        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError("Campus does not belong to selected tenant.")
        if department and tenant and department.tenant_id != tenant.id:
            raise forms.ValidationError("Department does not belong to selected tenant.")
        if campus and department and department.campus_id != campus.id:
            raise forms.ValidationError("Department does not belong to selected campus.")
        return cleaned


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
        if department_queryset is not None:
            self.fields["department"].queryset = department_queryset
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
        self.fields["program"].help_text = "Optional if section_code is globally unique. Required when section codes repeat per program."
        self.fields["academic_year"].help_text = "Must match Academic Year used in CSV import (use AY code values from master)."
        self.fields["term"].help_text = "Must match Term code in CSV (example: 1ST, 2ND)."
        self.fields["section"].help_text = "Use exact Section code from Sections master."
        self.fields["room"].label = "Room/Office/Lab"
        _set_choice_label(self.fields.get("academic_year"), _academic_year_label)
        _set_choice_label(self.fields.get("term"), _term_label)
        _set_choice_label(self.fields.get("course"), _course_label)
        _set_choice_label(self.fields.get("section"), _section_label)
        self.fields["is_active"].label = "Record state"
        self.fields["is_active"].widget = forms.Select(
            choices=((True, "Active"), (False, "Inactive"))
        )
        self.fields["is_active"].help_text = (
            "Inactive offerings are hidden from non-superadmin users and excluded from processing."
        )
        _enforce_active_reference_choices(self)

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

        if campus and tenant and campus.tenant_id != tenant.id:
            raise forms.ValidationError("Campus does not belong to tenant.")
        if department and campus and department.campus_id != campus.id:
            raise forms.ValidationError("Department does not belong to campus.")
        if program and department and program.department_id != department.id:
            raise forms.ValidationError("Program does not belong to department.")
        if academic_year and tenant and academic_year.tenant_id != tenant.id:
            raise forms.ValidationError("Academic year does not belong to tenant.")
        if term and academic_year and term.academic_year_id != academic_year.id:
            raise forms.ValidationError("Term does not belong to selected academic year.")
        if course and tenant and course.tenant_id != tenant.id:
            raise forms.ValidationError("Course does not belong to tenant.")
        if section and tenant and section.tenant_id != tenant.id:
            raise forms.ValidationError("Section does not belong to tenant.")
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
        fields = ["tenant", "code", "name", "description", "default_base_value", "is_active"]

    def __init__(self, *args, tenant_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant_queryset is not None:
            self.fields["tenant"].queryset = tenant_queryset
        _enforce_active_reference_choices(self)


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
            self.fields["selected_offerings"].queryset = offering_queryset
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
    review_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class GradingTemplatePeriodForm(forms.ModelForm):
    class Meta:
        model = GradingTemplatePeriod
        fields = ["template", "code", "name", "sequence_no", "weight_percentage", "is_active"]

    def __init__(self, *args, template_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if template_queryset is not None:
            self.fields["template"].queryset = template_queryset
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
        help_text="This value will prefill blank raw-score inputs. For Base-50 items, EduGradesPro will still compute the percentage from raw score and total score.",
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
            self.fields["course_offering"].queryset = offering_queryset
        _set_choice_label(self.fields.get("academic_year"), _academic_year_label)
        _set_choice_label(self.fields.get("term"), _term_label)
        _set_choice_label(self.fields.get("course_offering"), _offering_label)
        _enforce_active_reference_choices(self)
        self.fields["deadline_at"].input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"]
        self.fields["deadline_at"].help_text = (
            "Submission deadline for this period scope. "
            "Admin submission revert is allowed only before this timestamp."
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
        cleaned["period_code"] = period_code

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


class GradeSubmissionReopenRequestForm(forms.Form):
    justification = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Explain why this submitted grading period needs to be reopened.",
    )


class GradeSubmissionReopenReviewForm(forms.Form):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"

    decision = forms.ChoiceField(choices=Decision.choices)
    review_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))


class GradeCorrectionReviewForm(forms.Form):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE", "Approve"
        REJECT = "REJECT", "Reject"

    decision = forms.ChoiceField(choices=Decision.choices)
    review_remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
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


class TenantGradingProfileForm(forms.ModelForm):
    class Meta:
        model = TenantGradingProfile
        fields = [
            "tenant",
            "campus",
            "department",
            "program",
            "course",
            "course_type",
            "profile_code",
            "profile_name",
            "grading_template",
            "default_base_value",
            "passing_grade_threshold",
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
        if department_queryset is not None:
            self.fields["department"].queryset = department_queryset
        if program_queryset is not None:
            self.fields["program"].queryset = program_queryset
        if course_queryset is not None:
            self.fields["course"].queryset = course_queryset
        if template_queryset is not None:
            self.fields["grading_template"].queryset = template_queryset
        if term_queryset is not None:
            self.fields["effective_from_term"].queryset = term_queryset

        self.fields["campus"].help_text = "Leave blank for tenant-wide profile."
        self.fields["department"].help_text = "Optional narrower scope."
        self.fields["program"].help_text = "Optional narrower scope."
        self.fields["course"].help_text = "Optional course-specific override."
        self.fields["course_type"].help_text = "Optional fallback by course type."
        self.fields["passing_grade_threshold"].help_text = (
            "Optional passing threshold for analytics and governance at this profile scope "
            "(example: 75.00). Leave blank to use tenant default."
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

        passing_threshold = cleaned.get("passing_grade_threshold")
        if passing_threshold is not None and (passing_threshold <= 0 or passing_threshold > 100):
            self.add_error(
                "passing_grade_threshold",
                "Passing threshold must be greater than 0 and not greater than 100.",
            )
        return cleaned


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


class CorrectionGovernanceSettingForm(forms.Form):
    CORRECTION_MODE_CHOICES = [
        ("MANUAL_ONLY", "Manual Only (paper form + admin reopen)"),
        ("SYSTEM_REQUEST", "System Request Workflow"),
    ]
    PREDEADLINE_CORRECTION_MODE_CHOICES = [
        ("REQUEST_REVIEW", "Request Review"),
        ("FACULTY_SELF_REOPEN", "Faculty Self-Reopen Before Deadline"),
    ]

    correction_mode = forms.ChoiceField(
        choices=CORRECTION_MODE_CHOICES,
        label="Correction process mode",
        help_text=(
            "Manual Only disables faculty in-portal correction request filing. "
            "System Request enables the in-portal correction workflow."
        ),
    )

    predeadline_correction_mode = forms.ChoiceField(
        choices=PREDEADLINE_CORRECTION_MODE_CHOICES,
        label="Pre-deadline correction handling",
        help_text=(
            "Choose whether faculty must file a correction request even before the deadline, "
            "or may directly reopen their own submitted grading period before deadline."
        ),
    )


class CorrectionApprovalRouteRuleForm(forms.ModelForm):
    class Meta:
        model = CorrectionApprovalRouteRule
        fields = [
            "faculty_department",
            "route_mode",
            "step1_role",
            "step1_requires_same_department",
            "final_role",
            "final_requires_same_department",
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
            self.fields["step1_role"].queryset = role_queryset
            self.fields["final_role"].queryset = role_queryset
        self.fields["faculty_department"].required = False
        self.fields["faculty_department"].help_text = "Leave blank to configure tenant default route."
        self.fields["final_role"].required = False
        _enforce_active_reference_choices(self)

    def clean(self):
        cleaned = super().clean()
        route_mode = cleaned.get("route_mode")
        step1_role = cleaned.get("step1_role")
        final_role = cleaned.get("final_role")
        faculty_department = cleaned.get("faculty_department")

        if not step1_role:
            self.add_error("step1_role", "First approver role is required.")

        if route_mode == CorrectionApprovalRouteRule.RouteMode.TWO_STEP and not final_role:
            self.add_error("final_role", "Final approver role is required for two-step route.")
        if route_mode == CorrectionApprovalRouteRule.RouteMode.DIRECT_TO_FINAL:
            cleaned["final_role"] = None
            cleaned["final_requires_same_department"] = False

        tenant = self.tenant or getattr(self.instance, "tenant", None)
        if tenant and faculty_department and faculty_department.tenant_id != tenant.id:
            self.add_error("faculty_department", "Faculty department must belong to the selected tenant scope.")

        return cleaned

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
    correction_official_report_enabled = forms.BooleanField(
        required=False,
        label="Enable official correction PDF/report generation",
        help_text="When enabled, approved correction workflows may generate an official printable/exportable registrar reference document.",
    )
    correction_submission_approval_email_enabled = forms.BooleanField(
        required=False,
        label="Enable approval notification email on correction submission",
        help_text="When enabled, EduGradesPro emails the selected approval-role recipients as soon as a faculty member submits a petition for correction of grades.",
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
        help_text="When enabled, EduGradesPro may email the official correction PDF automatically after academic approval.",
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

    def __init__(self, *args, role_queryset=None, campus_queryset=None, campus_initial_map=None, **kwargs):
        self.campus_fields = []
        self.campus_queryset = campus_queryset
        campus_initial_map = campus_initial_map or {}
        super().__init__(*args, **kwargs)
        if role_queryset is not None:
            self.fields["correction_submission_approval_email_roles"].queryset = role_queryset
            self.fields["correction_registrar_auto_email_roles"].queryset = role_queryset
            self.fields["grade_prediction_roles"].queryset = role_queryset
            self.fields["grade_prediction_what_if_roles"].queryset = role_queryset
        _enforce_active_reference_choices(self)

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

    def clean(self):
        cleaned = super().clean()

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

        cleaned["correction_registrar_campus_recipient_map"] = campus_recipient_map
        return cleaned
