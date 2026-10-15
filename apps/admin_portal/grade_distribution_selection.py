"""Monitor-local selection. Shared supervision and grading services remain unchanged."""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db.models import F, Q
from django.http import QueryDict
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, FacultyAssignment, Term
from apps.admin_portal.services import AdminScopeService
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.grading.services import GradingGovernanceService
from apps.rbac.models import UserRole
from apps.tenants.models import Department

PERIODS = (("PRELIM", "Prelim"), ("MIDTERM", "Midterm"), ("PREFINAL", "Pre-Final"), ("FINAL", "Final"))
SALT = "grade-distribution-search-v1"


def faculty_name(user):
    last, first = user.last_name.strip(), user.first_name.strip()
    return f"{last}, {first}" if last and first else (last or first or f"Faculty #{user.pk}")


def logical_period(period):
    # These aliases are verified in the existing correction-governance mapping.
    # No sequence-based fallback; contradictory code/name evidence is unresolved.
    keys = {key for key, _ in PERIODS}
    code = GradingGovernanceService._normalize_period_key(period.code)
    name = GradingGovernanceService._normalize_period_key(period.name)
    if code in keys and name in keys and code != name:
        return None
    return code if code in keys else (name if not period.code.strip() and name in keys else None)


class MonitorSelection:
    SORTS = tuple((f"exact_{grade}_desc", f"Exactly {grade} Count (highest first)") for grade in range(100, 94, -1))
    FILTER_KEYS = ("campus_id", "academic_year_id", "term_id", "period", "faculty_id", "department_id", "status", "sort", "course_id", "only_100")

    def __init__(self, request):
        self.request = request
        self.tenant_id = getattr(request, "scope", {}).get("tenant_id")
        self.errors = []
        self.selected = {}
        for key in self.FILTER_KEYS:
            self.selected[key] = (request.GET.get(key) or "").strip()
        self.selected["course_id"] = sorted(set(v.strip() for v in request.GET.getlist("course_id") if v.strip()))
        self.selected["campus_id"] = self.selected["campus_id"] or str(getattr(request, "scope", {}).get("campus_id") or "")
        self.selected["status"] = self.selected["status"] or "all"
        self.selected["sort"] = self.selected["sort"] or "exact_100_desc"
        self.campuses = list(AdminScopeService.active_scoped_campuses(request).filter(tenant_id=self.tenant_id).order_by("code"))
        self.campuses = [c for c in self.campuses if PermissionService.has_permission(
            request.user, "grade_distribution_monitor.read", tenant_id=self.tenant_id, campus_id=c.id
        ) and not PermissionService._scoped_user_permissions(
            request.user, tenant_id=self.tenant_id, campus_id=c.id
        ).filter(permission__code="grade_distribution_monitor.read", grant_type="DENY").exists()]
        # Ask the unchanged service for campus-specific supervised faculty, including
        # its Area Chair / College Dean chain. Never scope by offering department.
        self.faculty_map = AdminScopeService._faculty_ids_by_monitoring_campus(request, include_all_campuses=True)
        self.faculty_map = {c.id: self.faculty_map.get(c.id, []) for c in self.campuses}
        campus_value = self.selected["campus_id"]
        self.campus_ids = list(self.faculty_map) if campus_value == "all" else [c.id for c in self.campuses if str(c.id) == campus_value]
        self.years = AcademicYear.objects.filter(tenant_id=self.tenant_id).order_by("-start_date", "id")
        self.terms = Term.objects.filter(tenant_id=self.tenant_id, academic_year__tenant_id=self.tenant_id)
        if self.selected["academic_year_id"].isdigit():
            self.terms = self.terms.filter(academic_year_id=self.selected["academic_year_id"])
        self.terms = self.terms.select_related("academic_year").order_by("-academic_year__start_date", "sequence_no", "id")
        self.assignments = self._assignments()
        self.departments = self._departments()
        faculty_ids = self.assignments.values("faculty_user_id")
        self.faculty = list(get_user_model().objects.filter(id__in=faculty_ids).order_by("last_name", "first_name", "id"))
        labels = Counter(faculty_name(u).casefold() for u in self.faculty)
        self.options = {
            "campuses": self.campuses, "academic_years": self.years, "terms": self.terms,
            "periods": [{"id": key, "label": label} for key, label in PERIODS],
            "faculty": [{"id": u.id, "label": faculty_name(u) + (f" (#{u.id})" if labels[faculty_name(u).casefold()] > 1 else "")} for u in self.faculty],
            "departments": [{"id": d.id, "label": f"{d.name} ({d.campus.code} / {d.code}, #{d.id})"} for d in self.departments],
            "courses": [{"id": str(c.id), "label": f"{c.code} — {c.title} ({c.campus.code if c.campus_id else 'Campus not specified'}, #{c.id})"} for c in
                Course.objects.filter(id__in=self.assignments.values("offering__course_id"), tenant_id=self.tenant_id)
                    .select_related("campus").order_by("code", "id")],
            "sorts": [{"id": key, "label": label} for key, label in self.SORTS] + [{"id": "faculty_asc", "label": "Faculty (A to Z)"}],
        }

    def _assignments(self):
        campus_faculty = Q(pk__in=[])
        for campus_id in self.campus_ids:
            campus_faculty |= Q(offering__campus_id=campus_id, faculty_user_id__in=self.faculty_map[campus_id])
        historical_offering = (
            Q(offering__status__in=["CLOSED", "ARCHIVED"])
            | Q(offering__term__end_date__lt=timezone.localdate())
            | Q(offering__term__end_date__isnull=True, offering__academic_year__end_date__lt=timezone.localdate())
        )
        return FacultyAssignment.objects.filter(
            campus_faculty, offering__tenant_id=self.tenant_id,
            offering__academic_year__tenant_id=self.tenant_id, offering__term__tenant_id=self.tenant_id,
            offering__term__academic_year_id=F("offering__academic_year_id"),
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        ).filter(
            Q(is_active=True, offering__is_active=True)
            | (Q(accepted_at__isnull=False) & historical_offering)
        ).filter(Q(tenant_id=self.tenant_id) | Q(tenant__isnull=True)).filter(
            Q(campus_id=F("offering__campus_id")) | Q(campus__isnull=True)
        ).order_by()

    def _departments(self):
        # Department choices refer to the faculty's relationships in this campus,
        # not to the departments owning their offerings.
        faculty_ids = self.assignments.values("faculty_user_id")
        role_departments = self._faculty_roles().filter(user_id__in=faculty_ids).values("department_id")
        defaults = get_user_model().objects.filter(id__in=faculty_ids, default_campus_id__in=self.campus_ids).values("default_department_id")
        accessible = set()
        scope = getattr(self.request, "scope", {})
        for campus_id in self.campus_ids:
            if scope.get("tenant_id") == self.tenant_id and scope.get("campus_id") == campus_id and "department_ids" in scope:
                accessible.update(scope["department_ids"])
            else:
                accessible.update(ScopeService.get_accessible_department_ids(self.request.user, tenant_id=self.tenant_id, campus_id=campus_id))
        return list(Department.objects.filter(id__in=accessible).filter(
            Q(id__in=role_departments) | Q(id__in=defaults), tenant_id=self.tenant_id,
            campus_id__in=self.campus_ids, is_active=True,
        ).select_related("campus").order_by("name", "campus__code", "code", "id"))

    def _faculty_roles(self, campus_ids=None):
        """Match the shared supervision service's explicit/default scope rules."""
        campus_roles = Q(pk__in=[])
        for campus_id in self.campus_ids if campus_ids is None else campus_ids:
            campus_roles |= Q(user_id__in=self.faculty_map.get(campus_id, [])) & (
                Q(campus_id=campus_id) | Q(campus__isnull=True, user__default_campus_id=campus_id)
            )
        return UserRole.objects.filter(
            campus_roles, role__code="FACULTY", role__is_active=True, is_active=True,
        ).filter(Q(tenant_id=self.tenant_id) | Q(tenant__isnull=True, user__default_tenant_id=self.tenant_id))

    def validate(self):
        s = self.selected
        if not self.tenant_id or not self.campus_ids:
            self.errors.append("Select an authorized campus in the current tenant.")
        for key, qs, label in (("academic_year_id", self.years, "Academic Year"), ("term_id", self.terms, "Term")):
            if not s[key].isdigit() or not qs.filter(pk=s[key]).exists():
                self.errors.append(f"Select a valid {label}.")
        if s["period"] not in dict(PERIODS):
            self.errors.append("Select a valid Grading Period.")
        if s["status"] not in {"all", "finalized", "unfinalized"}:
            self.errors.append("Select a valid grade status.")
        if s["sort"] not in {key for key, _ in self.SORTS} | {"faculty_asc"}:
            self.errors.append("Select a valid sort order.")
        if s["only_100"] not in {"", "1"}:
            self.errors.append("Select a valid Classes with 100s option.")
        if not set(s["course_id"]).issubset({c["id"] for c in self.options["courses"]}):
            self.errors.append("A selected course is unavailable in the authorized scope.")
        for key, choices, label in (("faculty_id", self.options["faculty"], "faculty"), ("department_id", self.options["departments"], "department")):
            if s[key] and s[key] not in {str(item["id"]) for item in choices}:
                self.errors.append(f"The selected {label} is unavailable in the authorized scope.")
        if self.errors:
            raise ValidationError(self.errors)

    def filtered_assignments(self):
        s = self.selected
        qs = self.assignments.filter(offering__academic_year_id=s["academic_year_id"], offering__term_id=s["term_id"])
        if s["faculty_id"]:
            qs = qs.filter(faculty_user_id=s["faculty_id"])
        if s["course_id"]:
            qs = qs.filter(offering__course_id__in=s["course_id"])
        if s["department_id"]:
            dept = next(d for d in self.departments if str(d.id) == s["department_id"])
            dept_ids = AdminScopeService.expand_department_filter_ids(dept.id, tenant_id=self.tenant_id, campus_id=dept.campus_id)
            role_faculty_ids = self._faculty_roles([dept.campus_id]).filter(department_id__in=dept_ids).values("user_id")
            faculty_ids = get_user_model().objects.filter(
                Q(default_department_id__in=dept_ids, default_campus_id=dept.campus_id)
                | Q(id__in=role_faculty_ids)
            ).values("id")
            qs = qs.filter(offering__campus_id=dept.campus_id, faculty_user_id__in=faculty_ids)
        return qs

    def identity(self):
        scope = getattr(self.request, "scope", {})
        authority = {"faculty": {k: sorted(v) for k, v in self.faculty_map.items()}, "tenant": self.tenant_id, "campus": scope.get("campus_id")}
        return {"user": self.request.user.pk, "filters": self.selected,
                "authority": hashlib.sha256(json.dumps(authority, sort_keys=True).encode()).hexdigest()}

    def token(self):
        return signing.dumps(self.identity(), salt=SALT, compress=True)

    def validate_token(self):
        try:
            payload = signing.loads(self.request.GET.get("context", ""), salt=SALT, max_age=3600)
        except signing.BadSignature as exc:
            raise ValidationError("Search context expired or is invalid. Apply Filters again.") from exc
        if payload != self.identity():
            raise ValidationError("Filters or authorized scope changed. Apply Filters again.")

    def query(self, token):
        q = QueryDict(mutable=True)
        for key, value in self.selected.items():
            if value:
                if isinstance(value, list):
                    q.setlist(key, value)
                else:
                    q[key] = value
        q["apply"] = "1"
        q["context"] = token
        return q
