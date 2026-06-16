from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.grading.models import GradingTemplate
from apps.rbac.models import UserRole


class GradingTemplateAccessService:
    """Central department-visibility rules for admin template access."""

    @staticmethod
    def get_user_active_department_ids(user, *, tenant_id: int | None = None) -> list[int]:
        if not user or not getattr(user, "is_authenticated", False):
            return []
        if getattr(user, "is_superuser", False):
            return ScopeService.get_accessible_department_ids(user, tenant_id=tenant_id)

        roles = UserRole.objects.filter(
            user=user,
            is_active=True,
            role__is_active=True,
            department__isnull=False,
            department__is_active=True,
            department__tenant__is_active=True,
        ).exclude(role__code="FACULTY")
        if tenant_id is not None:
            roles = roles.filter(department__tenant_id=tenant_id)
        assigned_department_ids = list(roles.values_list("department_id", flat=True).distinct())
        return ScopeService.expand_department_ids(
            assigned_department_ids,
            tenant_id=tenant_id,
        )

    @classmethod
    def filter_queryset_for_user(
        cls,
        user,
        queryset: QuerySet,
        *,
        tenant_ids=None,
    ) -> QuerySet:
        if not user or not getattr(user, "is_authenticated", False):
            return queryset.none()
        if getattr(user, "is_superuser", False):
            return queryset

        accessible_tenant_ids = list(tenant_ids or ScopeService.get_accessible_tenant_ids(user))
        department_ids = cls.get_user_active_department_ids(user)
        visibility_filter = Q(department_visibility=GradingTemplate.DepartmentVisibility.ALL)
        if department_ids:
            visibility_filter |= Q(
                department_visibility=GradingTemplate.DepartmentVisibility.SELECTED,
                visible_departments__id__in=department_ids,
            )
        return queryset.filter(tenant_id__in=accessible_tenant_ids).filter(visibility_filter).distinct()

    @classmethod
    def user_can_access_grading_template(cls, user, template: GradingTemplate) -> bool:
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_superuser", False):
            return True
        if template.tenant_id not in ScopeService.get_accessible_tenant_ids(user):
            return False
        if template.department_visibility == GradingTemplate.DepartmentVisibility.ALL:
            return True
        department_ids = cls.get_user_active_department_ids(user, tenant_id=template.tenant_id)
        return template.visible_departments.filter(id__in=department_ids, is_active=True).exists()

    @classmethod
    def user_can_govern_grading_template(
        cls,
        user,
        template: GradingTemplate,
        *,
        permission_code: str,
        campus_id: int | None = None,
    ) -> bool:
        return cls.user_can_access_grading_template(user, template) and PermissionService.has_permission(
            user,
            permission_code,
            tenant_id=template.tenant_id,
            campus_id=campus_id,
        )
