from __future__ import annotations

from typing import Iterable, Set

from apps.rbac.models import Permission, UserPermission, UserRole


class PermissionService:
    @staticmethod
    def _scoped_user_roles(user, tenant_id: int | None = None, campus_id: int | None = None):
        roles_qs = UserRole.objects.filter(user=user, is_active=True, role__is_active=True)
        if tenant_id is not None:
            roles_qs = roles_qs.filter(tenant_id__in=[tenant_id, None])
        if campus_id is not None:
            roles_qs = roles_qs.filter(campus_id__in=[campus_id, None])
        return roles_qs

    @staticmethod
    def _scoped_user_permissions(user, tenant_id: int | None = None, campus_id: int | None = None):
        perms_qs = UserPermission.objects.filter(user=user, permission__is_active=True)
        if tenant_id is not None:
            perms_qs = perms_qs.filter(tenant_id__in=[tenant_id, None])
        if campus_id is not None:
            perms_qs = perms_qs.filter(campus_id__in=[campus_id, None])
        return perms_qs

    @classmethod
    def get_effective_permission_codes(
        cls, user, tenant_id: int | None = None, campus_id: int | None = None
    ) -> Set[str]:
        if not user or not user.is_authenticated:
            return set()

        if user.is_superuser:
            return set(Permission.objects.filter(is_active=True).values_list("code", flat=True))

        role_ids = cls._scoped_user_roles(user, tenant_id=tenant_id, campus_id=campus_id).values_list(
            "role_id", flat=True
        )
        role_permission_codes = set(
            Permission.objects.filter(is_active=True, role_permissions__role_id__in=role_ids).values_list(
                "code", flat=True
            )
        )

        scoped_user_perms = cls._scoped_user_permissions(user, tenant_id=tenant_id, campus_id=campus_id)
        allow_codes = set(
            scoped_user_perms.filter(grant_type=UserPermission.GrantType.ALLOW).values_list(
                "permission__code", flat=True
            )
        )
        deny_codes = set(
            scoped_user_perms.filter(grant_type=UserPermission.GrantType.DENY).values_list(
                "permission__code", flat=True
            )
        )
        return (role_permission_codes | allow_codes) - deny_codes

    @classmethod
    def has_permission(
        cls, user, permission_code: str, tenant_id: int | None = None, campus_id: int | None = None
    ) -> bool:
        return permission_code in cls.get_effective_permission_codes(
            user, tenant_id=tenant_id, campus_id=campus_id
        )

    @classmethod
    def has_assigned_permission(
        cls, user, permission_code: str, tenant_id: int | None = None, campus_id: int | None = None
    ) -> bool:
        """Check explicit RBAC assignment without the superuser permission shortcut."""
        if not user or not user.is_authenticated:
            return False

        scoped_user_perms = cls._scoped_user_permissions(user, tenant_id=tenant_id, campus_id=campus_id)
        if scoped_user_perms.filter(
            permission__code=permission_code,
            grant_type=UserPermission.GrantType.DENY,
        ).exists():
            return False
        if scoped_user_perms.filter(
            permission__code=permission_code,
            grant_type=UserPermission.GrantType.ALLOW,
        ).exists():
            return True

        return cls._scoped_user_roles(
            user,
            tenant_id=tenant_id,
            campus_id=campus_id,
        ).filter(
            role__role_permissions__permission__code=permission_code,
            role__role_permissions__permission__is_active=True,
        ).exists()

    @classmethod
    def has_any_permission(
        cls, user, permission_codes: Iterable[str], tenant_id: int | None = None, campus_id: int | None = None
    ) -> bool:
        code_set = cls.get_effective_permission_codes(user, tenant_id=tenant_id, campus_id=campus_id)
        return any(code in code_set for code in permission_codes)
