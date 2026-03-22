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
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="departments")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="departments")
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=150)

    class Meta:
        db_table = "departments"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "campus", "code"], name="uq_departments_scope_code"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


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
