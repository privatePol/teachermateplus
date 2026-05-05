from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import ActivatableModel, TimeStampedModel


class Tenant(TimeStampedModel, ActivatableModel):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=150)

    class Meta:
        db_table = "tenants"
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Campus(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="campuses")
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    address = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "campuses"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="uq_campuses_tenant_code"),
        ]

    def __str__(self):
        return f"{self.tenant.code}:{self.code} - {self.name}"


class Department(TimeStampedModel, ActivatableModel):
    class OperationBranch(models.TextChoices):
        ACADEMIC = "ACADEMIC", "Academic"
        ADMINISTRATIVE = "ADMINISTRATIVE", "Administrative"

    class UnitType(models.TextChoices):
        DIVISION = "DIVISION", "Division"
        AREA = "AREA", "Area"
        OFFICE = "OFFICE", "Office"
        OTHER = "OTHER", "Other"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="departments")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="departments")
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        blank=True,
        null=True,
    )
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    operation_branch = models.CharField(
        max_length=20,
        choices=OperationBranch.choices,
        default=OperationBranch.ACADEMIC,
    )
    unit_type = models.CharField(max_length=20, choices=UnitType.choices, default=UnitType.AREA)

    class Meta:
        db_table = "departments"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "campus", "code"], name="uq_departments_scope_code"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        super().clean()
        if not self.parent_id:
            return
        if self.pk and self.parent_id == self.pk:
            raise ValidationError("Department cannot be its own parent.")
        if self.parent.tenant_id != self.tenant_id:
            raise ValidationError("Parent department must belong to the same tenant.")
        if self.parent.campus_id != self.campus_id:
            raise ValidationError("Parent department must belong to the same campus.")

        ancestor = self.parent
        while ancestor:
            if self.pk and ancestor.pk == self.pk:
                raise ValidationError("Department hierarchy cannot contain a cycle.")
            ancestor = ancestor.parent


class Program(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="programs")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="programs")
    department = models.ForeignKey("tenants.Department", on_delete=models.PROTECT, related_name="programs")
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150)
    level = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = "programs"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "campus", "department", "code"], name="uq_programs_scope_code"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class SystemSetting(models.Model):
    class ValueType(models.TextChoices):
        STRING = "STRING", "String"
        INT = "INT", "Integer"
        BOOL = "BOOL", "Boolean"
        JSON = "JSON", "JSON"

    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.PROTECT, related_name="system_settings", blank=True, null=True
    )
    setting_key = models.CharField(max_length=128)
    setting_value = models.CharField(max_length=255)
    value_type = models.CharField(max_length=10, choices=ValueType.choices, default=ValueType.STRING)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "system_settings"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "setting_key"], name="uq_system_settings_tenant_key"),
        ]

    def __str__(self):
        scope = self.tenant.code if self.tenant_id else "GLOBAL"
        return f"{scope}:{self.setting_key}"


class TenantApiKey(TimeStampedModel):
    class Purpose(models.TextChoices):
        SIS = "SIS", "SIS Integration"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="api_keys")
    name = models.CharField(max_length=120)
    purpose = models.CharField(max_length=20, choices=Purpose.choices, default=Purpose.SIS)
    key_prefix = models.CharField(max_length=32, unique=True)
    key_hash = models.CharField(max_length=128, unique=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)
    last_used_at = models.DateTimeField(blank=True, null=True)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_tenant_api_keys",
    )

    class Meta:
        db_table = "tenant_api_keys"
        ordering = ["tenant__code", "purpose", "name"]
        indexes = [
            models.Index(fields=["key_prefix", "is_active"], name="idx_tapikey_prefix_active"),
            models.Index(fields=["tenant", "purpose", "is_active"], name="idx_tapikey_tenant_purpose"),
        ]

    def __str__(self):
        return f"{self.tenant.code}:{self.purpose}:{self.name}"
