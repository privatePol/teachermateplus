from django.db import models

from apps.core.models import ActivatableModel, TimeStampedModel


class Role(TimeStampedModel, ActivatableModel):
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True, null=True)
    is_system = models.BooleanField(default=False)

    class Meta:
        db_table = "roles"
        ordering = ["name"]

    def __str__(self):
        return f"{self.code}"


class Permission(TimeStampedModel, ActivatableModel):
    code = models.CharField(max_length=128, unique=True)
    module = models.CharField(max_length=64)
    action = models.CharField(max_length=64)
    description = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "permissions"
        ordering = ["module", "action", "code"]

    def __str__(self):
        return self.code


class UserRole(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="user_roles")
    role = models.ForeignKey("rbac.Role", on_delete=models.PROTECT, related_name="user_roles")
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.PROTECT, related_name="user_roles", blank=True, null=True
    )
    campus = models.ForeignKey(
        "tenants.Campus", on_delete=models.PROTECT, related_name="user_roles", blank=True, null=True
    )
    department = models.ForeignKey(
        "tenants.Department", on_delete=models.PROTECT, related_name="user_roles", blank=True, null=True
    )
    is_active = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_roles"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role", "tenant", "campus", "department"],
                name="uq_user_roles_scoped",
            ),
        ]

    def __str__(self):
        return f"{self.user.username}:{self.role.code}"


class RolePermission(models.Model):
    role = models.ForeignKey("rbac.Role", on_delete=models.PROTECT, related_name="role_permissions")
    permission = models.ForeignKey("rbac.Permission", on_delete=models.PROTECT, related_name="role_permissions")

    class Meta:
        db_table = "role_permissions"
        constraints = [
            models.UniqueConstraint(fields=["role", "permission"], name="uq_role_permissions"),
        ]

    def __str__(self):
        return f"{self.role.code}:{self.permission.code}"


class UserPermission(models.Model):
    class GrantType(models.TextChoices):
        ALLOW = "ALLOW", "Allow"
        DENY = "DENY", "Deny"

    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="user_permissions_scoped")
    permission = models.ForeignKey(
        "rbac.Permission", on_delete=models.PROTECT, related_name="user_permissions_scoped"
    )
    grant_type = models.CharField(max_length=10, choices=GrantType.choices, default=GrantType.ALLOW)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.PROTECT, related_name="user_permissions", blank=True, null=True
    )
    campus = models.ForeignKey(
        "tenants.Campus", on_delete=models.PROTECT, related_name="user_permissions", blank=True, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_permissions"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "permission", "grant_type", "tenant", "campus"], name="uq_user_permissions_scoped"
            ),
        ]

    def __str__(self):
        return f"{self.user.username}:{self.permission.code}:{self.grant_type}"
