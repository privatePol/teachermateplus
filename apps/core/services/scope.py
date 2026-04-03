from __future__ import annotations

from apps.rbac.models import UserRole
from apps.tenants.models import Campus, Department, Tenant


class ScopeService:
    SESSION_TENANT_KEY = "edugradespro_scope_tenant_id"
    SESSION_CAMPUS_KEY = "edugradespro_scope_campus_id"

    @staticmethod
    def _parse_int(value):
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def get_accessible_tenant_ids(user):
        if not user or not user.is_authenticated:
            return []
        if user.is_superuser:
            return list(Tenant.objects.filter(is_active=True).values_list("id", flat=True))

        roles = UserRole.objects.filter(user=user, is_active=True, role__is_active=True)
        if roles.filter(tenant__isnull=True).exists():
            return list(Tenant.objects.filter(is_active=True).values_list("id", flat=True))
        return list(roles.exclude(tenant__isnull=True).values_list("tenant_id", flat=True).distinct())

    @staticmethod
    def get_accessible_campus_ids(user, tenant_id: int | None = None):
        if not user or not user.is_authenticated:
            return []
        if user.is_superuser:
            qs = Campus.objects.filter(is_active=True)
            if tenant_id:
                qs = qs.filter(tenant_id=tenant_id)
            return list(qs.values_list("id", flat=True))

        roles = UserRole.objects.filter(user=user, is_active=True, role__is_active=True)
        if tenant_id:
            roles = roles.filter(tenant_id__in=[tenant_id, None])

        if roles.filter(campus__isnull=True).exists():
            qs = Campus.objects.filter(is_active=True)
            if tenant_id:
                qs = qs.filter(tenant_id=tenant_id)
            return list(qs.values_list("id", flat=True))
        return list(roles.exclude(campus__isnull=True).values_list("campus_id", flat=True).distinct())

    @staticmethod
    def get_accessible_department_ids(user, tenant_id: int | None = None, campus_id: int | None = None):
        if not user or not user.is_authenticated:
            return []
        if user.is_superuser:
            qs = Department.objects.filter(is_active=True)
            if tenant_id:
                qs = qs.filter(tenant_id=tenant_id)
            if campus_id:
                qs = qs.filter(campus_id=campus_id)
            return list(qs.values_list("id", flat=True))

        roles = UserRole.objects.filter(user=user, is_active=True, role__is_active=True)
        non_faculty_roles = roles.exclude(role__code="FACULTY")
        if non_faculty_roles.exists():
            roles = non_faculty_roles
        if tenant_id:
            roles = roles.filter(tenant_id__in=[tenant_id, None])
        if campus_id:
            roles = roles.filter(campus_id__in=[campus_id, None])

        if roles.filter(department__isnull=True).exists():
            qs = Department.objects.filter(is_active=True)
            if tenant_id:
                qs = qs.filter(tenant_id=tenant_id)
            if campus_id:
                qs = qs.filter(campus_id=campus_id)
            return list(qs.values_list("id", flat=True))
        return list(roles.exclude(department__isnull=True).values_list("department_id", flat=True).distinct())

    @classmethod
    def build_scope(cls, user, tenant_id: int | None = None, campus_id: int | None = None):
        tenant_ids = cls.get_accessible_tenant_ids(user)
        if tenant_id not in tenant_ids:
            tenant_id = getattr(user, "default_tenant_id", None) if getattr(user, "default_tenant_id", None) in tenant_ids else None
        if tenant_id is None and tenant_ids:
            tenant_id = tenant_ids[0]

        campus_ids = cls.get_accessible_campus_ids(user, tenant_id=tenant_id)
        if campus_id not in campus_ids:
            campus_id = getattr(user, "default_campus_id", None) if getattr(user, "default_campus_id", None) in campus_ids else None
        if campus_id is None and campus_ids:
            campus_id = campus_ids[0]

        department_ids = cls.get_accessible_department_ids(user, tenant_id=tenant_id, campus_id=campus_id)

        return {
            "tenant_ids": tenant_ids,
            "campus_ids": campus_ids,
            "department_ids": department_ids,
            "tenant_id": tenant_id,
            "campus_id": campus_id,
        }

    @classmethod
    def attach_scope_to_request(cls, request):
        if not request.user.is_authenticated:
            request.scope = {
                "tenant_ids": [],
                "campus_ids": [],
                "department_ids": [],
                "tenant_id": None,
                "campus_id": None,
            }
            return request.scope
        session = getattr(request, "session", None)

        # Use dedicated scope selectors from topbar to avoid clashing with page-level filters.
        tenant_from_get = request.GET.get("scope_tenant_id")
        campus_from_get = request.GET.get("scope_campus_id")

        if tenant_from_get is not None or campus_from_get is not None:
            tenant_id = cls._parse_int(tenant_from_get)
            campus_id = cls._parse_int(campus_from_get)
        else:
            tenant_id = cls._parse_int(session.get(cls.SESSION_TENANT_KEY) if session else None)
            campus_id = cls._parse_int(session.get(cls.SESSION_CAMPUS_KEY) if session else None)

        request.scope = cls.build_scope(request.user, tenant_id=tenant_id, campus_id=campus_id)

        if session is not None:
            session[cls.SESSION_TENANT_KEY] = request.scope.get("tenant_id")
            session[cls.SESSION_CAMPUS_KEY] = request.scope.get("campus_id")

        return request.scope
