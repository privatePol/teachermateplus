from __future__ import annotations

from apps.rbac.models import UserRole
from apps.tenants.models import Campus, Department, Tenant


class ScopeService:
    SESSION_TENANT_KEY = "EduGrade+_scope_tenant_id"
    SESSION_CAMPUS_KEY = "EduGrade+_scope_campus_id"

    @staticmethod
    def _parse_int(value):
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def expand_department_ids(department_ids, *, tenant_id: int | None = None, campus_id: int | None = None):
        expanded_ids = {int(dept_id) for dept_id in department_ids or [] if dept_id}
        pending_ids = set(expanded_ids)
        while pending_ids:
            children = Department.objects.filter(parent_id__in=pending_ids, is_active=True)
            if tenant_id:
                children = children.filter(tenant_id=tenant_id)
            if campus_id:
                children = children.filter(campus_id=campus_id)
            child_ids = set(children.values_list("id", flat=True)) - expanded_ids
            expanded_ids.update(child_ids)
            pending_ids = child_ids
        return list(expanded_ids)

    @staticmethod
    def department_ancestor_ids(department_id, *, include_self: bool = True):
        try:
            parsed_department_id = int(department_id) if department_id not in (None, "") else None
        except (TypeError, ValueError):
            parsed_department_id = None
        if not parsed_department_id:
            return []
        department = Department.objects.filter(id=parsed_department_id).only("id", "parent_id").first()
        if not department:
            return []
        ancestor_ids = [department.id] if include_self else []
        parent_id = department.parent_id
        seen = {department.id}
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            ancestor_ids.append(parent_id)
            parent_id = Department.objects.filter(id=parent_id).values_list("parent_id", flat=True).first()
        return ancestor_ids

    @staticmethod
    def department_scope_covers(scope_department_id, target_department_id):
        if not scope_department_id or not target_department_id:
            return False
        expanded_ids = ScopeService.expand_department_ids([scope_department_id])
        return int(target_department_id) in set(expanded_ids)

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
        non_faculty_roles = roles.exclude(role__code="FACULTY")
        if non_faculty_roles.exists():
            roles = non_faculty_roles
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
        scoped_department_ids = list(
            roles.exclude(department__isnull=True)
            .filter(department__is_active=True)
            .values_list("department_id", flat=True)
            .distinct()
        )
        return ScopeService.expand_department_ids(scoped_department_ids, tenant_id=tenant_id, campus_id=campus_id)

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
