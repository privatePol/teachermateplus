from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, Q

from apps.academics.models import AcademicYear, CourseOffering, FacultyAssignment, Term
from apps.core.services.features import FeatureSettingsService
from apps.enrollment.models import Enrollment
from apps.rbac.models import UserPermission, UserRole
from apps.tenants.models import Campus, Department, Tenant


UNASSIGNED_DEPARTMENT_LABEL = "UNASSIGNED EXAM DEPARTMENT"


@dataclass(frozen=True)
class PlanningReadinessScope:
    tenant_id: int
    global_campus_ids: frozenset[int]
    department_ids_by_campus: dict[int, frozenset[int]]

    @property
    def campus_ids(self):
        return self.global_campus_ids | frozenset(self.department_ids_by_campus)

    def allows_anything(self):
        return bool(self.global_campus_ids or any(self.department_ids_by_campus.values()))

    def intersection(self, other: "PlanningReadinessScope"):
        if self.tenant_id != other.tenant_id:
            return PlanningReadinessScope(self.tenant_id, frozenset(), {})
        global_campuses = self.global_campus_ids & other.global_campus_ids
        department_ids_by_campus = {}
        for campus_id in self.campus_ids & other.campus_ids:
            if campus_id in global_campuses:
                continue
            left = self.department_ids_by_campus.get(campus_id, frozenset())
            right = other.department_ids_by_campus.get(campus_id, frozenset())
            if campus_id in self.global_campus_ids:
                allowed = right
            elif campus_id in other.global_campus_ids:
                allowed = left
            else:
                allowed = left & right
            if allowed:
                department_ids_by_campus[campus_id] = frozenset(allowed)
        return PlanningReadinessScope(
            self.tenant_id,
            frozenset(global_campuses),
            department_ids_by_campus,
        )

    def offering_filter(self):
        scope_filter = Q()
        if self.global_campus_ids:
            scope_filter |= Q(campus_id__in=self.global_campus_ids)
        for campus_id, department_ids in self.department_ids_by_campus.items():
            if department_ids:
                scope_filter |= Q(
                    campus_id=campus_id,
                    course__exam_department_id__in=department_ids,
                )
        return scope_filter


class PlanningReadinessAuthorizationService:
    VIEW_PERMISSION = "departmental_exams.view_planning_readiness"
    PRINT_PERMISSION = "departmental_exams.print_planning_readiness"

    @classmethod
    def scope_for_permission(cls, *, user, tenant_id, permission_code):
        empty = PlanningReadinessScope(tenant_id or 0, frozenset(), {})
        if (
            not tenant_id
            or not user
            or not user.is_authenticated
            or not user.is_active
            or not Tenant.objects.filter(id=tenant_id, is_active=True).exists()
            or not FeatureSettingsService.is_departmental_exam_builder_enabled(
                tenant_id=tenant_id
            )
        ):
            return empty

        active_campus_ids = set(
            Campus.objects.filter(tenant_id=tenant_id, is_active=True).values_list(
                "id", flat=True
            )
        )
        if not active_campus_ids:
            return empty
        direct_rows = UserPermission.objects.filter(
            user=user,
            permission__code=permission_code,
            permission__is_active=True,
            tenant_id=tenant_id,
            campus_id__in=active_campus_ids,
        ).values_list("campus_id", "grant_type")
        direct_allows = set()
        direct_denies = set()
        for campus_id, grant_type in direct_rows:
            if grant_type == UserPermission.GrantType.DENY:
                direct_denies.add(campus_id)
            else:
                direct_allows.add(campus_id)

        role_rows = list(
            UserRole.objects.filter(
                user=user,
                is_active=True,
                role__is_active=True,
                role__role_permissions__permission__code=permission_code,
                role__role_permissions__permission__is_active=True,
                tenant_id=tenant_id,
                campus_id__in=active_campus_ids,
            )
            .values_list("campus_id", "department_id")
            .distinct()
        )
        specific_department_ids = {department_id for _campus_id, department_id in role_rows if department_id}
        valid_department_scope = set(
            Department.objects.filter(
                id__in=specific_department_ids,
                tenant_id=tenant_id,
                campus_id__in=active_campus_ids,
                is_active=True,
            ).values_list("campus_id", "id")
        )

        global_campuses = direct_allows - direct_denies
        departments_by_campus = defaultdict(set)
        for campus_id, department_id in role_rows:
            if campus_id in direct_denies:
                continue
            if department_id is None:
                global_campuses.add(campus_id)
            elif (campus_id, department_id) in valid_department_scope:
                departments_by_campus[campus_id].add(department_id)
        for campus_id in global_campuses:
            departments_by_campus.pop(campus_id, None)

        return PlanningReadinessScope(
            tenant_id,
            frozenset(global_campuses),
            {
                campus_id: frozenset(department_ids)
                for campus_id, department_ids in departments_by_campus.items()
                if department_ids
            },
        )

    @classmethod
    def require_scope(cls, *, user, tenant_id, permission_code):
        scope = cls.scope_for_permission(
            user=user,
            tenant_id=tenant_id,
            permission_code=permission_code,
        )
        if not scope.allows_anything():
            raise PermissionDenied("Planning & Readiness is unavailable in your current exact scope.")
        return scope


class PlanningReadinessReport:
    FILTER_PARAMETER_NAMES = (
        "academic_year",
        "term",
        "exam_department",
        "campus",
        "assignment_status",
        "faculty_active",
        "account_status",
    )
    ASSIGNMENT_LABELS = {
        "ACCEPTED": "Accepted",
        "NOT_ACCEPTED": "Not Accepted",
        "NO_FACULTY": "No Faculty Assigned",
    }
    BOOLEAN_LABELS = {"YES": "Yes", "NO": "No", "NA": "N/A"}

    def __init__(self, *, tenant, scope, params):
        self.tenant = tenant
        self.scope = scope
        self.params = params

    def _authorized_base(self):
        return CourseOffering.objects.filter(
            self.scope.offering_filter(),
            tenant_id=self.tenant.id,
            tenant__is_active=True,
            campus__is_active=True,
            is_active=True,
            status=CourseOffering.Status.OPEN,
        )

    @staticmethod
    def _selected_id(params, name, choices, *, default=None):
        choice_map = {row.id: row for row in choices}
        if name not in params:
            return default, False
        raw = (params.get(name) or "").strip()
        if not raw:
            return default, False
        try:
            selected_id = int(raw)
        except (TypeError, ValueError):
            return None, True
        return choice_map.get(selected_id), selected_id not in choice_map

    @staticmethod
    def _selected_code(params, name, available_codes):
        raw = (params.get(name) or "").strip().upper()
        if not raw:
            return "", False
        return (raw, False) if raw in available_codes else (raw, True)

    @staticmethod
    def _faculty_role_keys(*, user_ids, tenant_id, campus_ids):
        rows = UserRole.objects.filter(
            user_id__in=user_ids,
            user__is_active=True,
            is_active=True,
            role__is_active=True,
            role__code="FACULTY",
        ).filter(
            Q(tenant_id=tenant_id) | Q(tenant__isnull=True),
            Q(campus_id__in=campus_ids) | Q(campus__isnull=True),
        ).values_list("user_id", "tenant_id", "campus_id", "department_id")
        return set(rows)

    @staticmethod
    def _faculty_role_covers(keys, *, user_id, tenant_id, campus_id, department_id):
        return any(
            (user_id, role_tenant_id, role_campus_id, role_department_id) in keys
            for role_tenant_id in (tenant_id, None)
            for role_campus_id in (campus_id, None)
            for role_department_id in (department_id, None)
        )

    @staticmethod
    def _summary(rows):
        offerings = {}
        faculty_ids = set()
        inactive_faculty_ids = set()
        not_activated_ids = set()
        no_faculty_offering_ids = set()
        course_ids = set()
        accepted = 0
        not_accepted = 0
        for row in rows:
            offerings[row["offering_id"]] = row["enrolled"]
            course_ids.add(row["course_id"])
            if row["faculty_user_id"] is None:
                no_faculty_offering_ids.add(row["offering_id"])
                continue
            faculty_ids.add(row["faculty_user_id"])
            if row["assignment_status"] == "ACCEPTED":
                accepted += 1
            else:
                not_accepted += 1
            if not row["faculty_active"]:
                inactive_faculty_ids.add(row["faculty_user_id"])
            if not row["account_activated"]:
                not_activated_ids.add(row["faculty_user_id"])
        return {
            "courses": len(course_ids),
            "offerings": len(offerings),
            "enrolled": sum(offerings.values()),
            "faculty": len(faculty_ids),
            "accepted": accepted,
            "not_accepted": not_accepted,
            "no_faculty": len(no_faculty_offering_ids),
            "inactive_faculty": len(inactive_faculty_ids),
            "not_activated": len(not_activated_ids),
        }

    def _load_rows(self, queryset):
        assignment_queryset = (
            FacultyAssignment.objects.filter(is_active=True)
            .select_related("faculty_user")
            .order_by("offering_id", "-is_primary", "faculty_user__last_name", "faculty_user__first_name", "id")
        )
        offerings = list(
            queryset.select_related(
                "academic_year",
                "term",
                "campus",
                "course",
                "course__exam_department",
                "section",
            )
            .annotate(
                enrolled_count=Count(
                    "enrollments",
                    filter=Q(
                        enrollments__is_active=True,
                        enrollments__enrollment_status=Enrollment.Status.ACTIVE,
                    ),
                    distinct=True,
                )
            )
            .prefetch_related(
                Prefetch(
                    "faculty_assignments",
                    queryset=assignment_queryset,
                    to_attr="planning_assignments",
                )
            )
            .order_by(
                "course__exam_department__name",
                "course__code",
                "campus__name",
                "section__code",
                "id",
            )
        )
        user_ids = {
            assignment.faculty_user_id
            for offering in offerings
            for assignment in offering.planning_assignments
        }
        campus_ids = {offering.campus_id for offering in offerings}
        faculty_role_keys = self._faculty_role_keys(
            user_ids=user_ids,
            tenant_id=self.tenant.id,
            campus_ids=campus_ids,
        )
        rows = []
        for offering in offerings:
            department = offering.course.exam_department
            common = {
                "offering_id": offering.id,
                "course_id": offering.course_id,
                "course": offering.course,
                "exam_department_id": department.id if department else None,
                "exam_department_label": (
                    f"{department.code} — {department.name}"
                    if department
                    else UNASSIGNED_DEPARTMENT_LABEL
                ),
                "campus_id": offering.campus_id,
                "campus": offering.campus,
                "section": offering.section,
                "enrolled": offering.enrolled_count,
            }
            if not offering.planning_assignments:
                rows.append(
                    {
                        **common,
                        "assignment_id": None,
                        "faculty_user_id": None,
                        "faculty_name": "Unassigned",
                        "assignment_status": "NO_FACULTY",
                        "assignment_status_label": self.ASSIGNMENT_LABELS["NO_FACULTY"],
                        "faculty_active": None,
                        "faculty_active_label": "N/A",
                        "account_activated": None,
                        "account_status_label": "N/A",
                    }
                )
                continue
            for assignment in offering.planning_assignments:
                user = assignment.faculty_user
                accepted = bool(
                    assignment.response_status == FacultyAssignment.ResponseStatus.ACCEPTED
                    and assignment.accepted_at is not None
                )
                faculty_active = bool(
                    user.is_active
                    and self._faculty_role_covers(
                        faculty_role_keys,
                        user_id=user.id,
                        tenant_id=offering.tenant_id,
                        campus_id=offering.campus_id,
                        department_id=offering.department_id,
                    )
                )
                account_activated = bool(user.is_active and user.has_usable_password())
                assignment_status = "ACCEPTED" if accepted else "NOT_ACCEPTED"
                rows.append(
                    {
                        **common,
                        "assignment_id": assignment.id,
                        "faculty_user_id": user.id,
                        "faculty_name": user.full_name,
                        "assignment_status": assignment_status,
                        "assignment_status_label": self.ASSIGNMENT_LABELS[assignment_status],
                        "faculty_active": faculty_active,
                        "faculty_active_label": "Yes" if faculty_active else "No",
                        "account_activated": account_activated,
                        "account_status_label": "Activated" if account_activated else "Not Activated",
                    }
                )
        return rows

    def _group_rows(self, rows):
        department_rows = defaultdict(list)
        for row in rows:
            department_rows[(row["exam_department_id"], row["exam_department_label"])].append(row)
        groups = []
        for (department_id, label), scoped_rows in sorted(
            department_rows.items(),
            key=lambda item: (item[0][0] is None, item[0][1].casefold()),
        ):
            course_rows = defaultdict(list)
            for row in scoped_rows:
                course_rows[row["course_id"]].append(row)
            courses = []
            for _course_id, grouped_rows in sorted(
                course_rows.items(), key=lambda item: (item[1][0]["course"].code, item[0])
            ):
                summary = self._summary(grouped_rows)
                courses.append(
                    {
                        "course": grouped_rows[0]["course"],
                        "campuses": sorted(
                            {row["campus"].name for row in grouped_rows}, key=str.casefold
                        ),
                        "summary": summary,
                        "rows": sorted(
                            grouped_rows,
                            key=lambda row: (
                                row["campus"].name.casefold(),
                                row["section"].code.casefold(),
                                row["faculty_name"].casefold(),
                                row["assignment_id"] or 0,
                            ),
                        ),
                    }
                )
            groups.append(
                {
                    "department_id": department_id,
                    "label": label,
                    "summary": self._summary(scoped_rows),
                    "courses": courses,
                }
            )
        return groups

    def build(self):
        authorized_base = self._authorized_base()
        year_ids = authorized_base.values_list("academic_year_id", flat=True).distinct()
        academic_years = list(
            AcademicYear.objects.filter(id__in=year_ids).order_by(
                "-start_date", "-id"
            )
        )
        default_year = academic_years[0] if academic_years else None
        selected_year, invalid_year = self._selected_id(
            self.params, "academic_year", academic_years, default=default_year
        )

        term_ids = (
            authorized_base.filter(academic_year=selected_year)
            .values_list("term_id", flat=True)
            .distinct()
            if selected_year and not invalid_year
            else Term.objects.none().values_list("id", flat=True)
        )
        terms = list(
            Term.objects.filter(id__in=term_ids).order_by(
                "sequence_no", "id"
            )
        )
        default_term = terms[0] if terms else None
        selected_term, invalid_term = self._selected_id(
            self.params, "term", terms, default=default_term
        )

        period_base = authorized_base.none()
        if selected_year and selected_term and not invalid_year and not invalid_term:
            period_base = authorized_base.filter(
                academic_year=selected_year,
                term=selected_term,
            )

        campus_ids = period_base.values_list("campus_id", flat=True).distinct()
        campuses = list(
            Campus.objects.filter(id__in=campus_ids, is_active=True).order_by("name", "code", "id")
        )
        selected_campus, invalid_campus = self._selected_id(
            self.params, "campus", campuses
        )

        department_ids = period_base.exclude(course__exam_department__isnull=True).values_list(
            "course__exam_department_id", flat=True
        ).distinct()
        departments = list(
            Department.objects.filter(id__in=department_ids).order_by("name", "code", "id")
        )
        unassigned_available = period_base.filter(course__exam_department__isnull=True).exists()
        raw_department = (self.params.get("exam_department") or "").strip()
        selected_department = ""
        invalid_department = False
        if raw_department:
            if raw_department.lower() == "unassigned" and unassigned_available:
                selected_department = "unassigned"
            else:
                try:
                    department_id = int(raw_department)
                except (TypeError, ValueError):
                    invalid_department = True
                else:
                    department_map = {row.id: row for row in departments}
                    if department_id in department_map:
                        selected_department = department_map[department_id]
                    else:
                        invalid_department = True

        filtered_base = period_base
        if selected_campus:
            filtered_base = filtered_base.filter(campus=selected_campus)
        if selected_department == "unassigned":
            filtered_base = filtered_base.filter(course__exam_department__isnull=True)
        elif selected_department:
            filtered_base = filtered_base.filter(course__exam_department=selected_department)
        if invalid_campus or invalid_department:
            filtered_base = filtered_base.none()

        candidate_rows = self._load_rows(filtered_base)
        assignment_codes = {row["assignment_status"] for row in candidate_rows}
        faculty_codes = {
            "NA" if row["faculty_active"] is None else ("YES" if row["faculty_active"] else "NO")
            for row in candidate_rows
        }
        account_codes = {
            "NA" if row["account_activated"] is None else ("YES" if row["account_activated"] else "NO")
            for row in candidate_rows
        }
        selected_assignment, invalid_assignment = self._selected_code(
            self.params, "assignment_status", assignment_codes
        )
        selected_faculty_active, invalid_faculty = self._selected_code(
            self.params, "faculty_active", faculty_codes
        )
        selected_account, invalid_account = self._selected_code(
            self.params, "account_status", account_codes
        )

        rows = candidate_rows
        if any((invalid_year, invalid_term, invalid_campus, invalid_department, invalid_assignment, invalid_faculty, invalid_account)):
            rows = []
        else:
            if selected_assignment:
                rows = [row for row in rows if row["assignment_status"] == selected_assignment]
            if selected_faculty_active:
                rows = [
                    row
                    for row in rows
                    if ("NA" if row["faculty_active"] is None else ("YES" if row["faculty_active"] else "NO"))
                    == selected_faculty_active
                ]
            if selected_account:
                rows = [
                    row
                    for row in rows
                    if ("NA" if row["account_activated"] is None else ("YES" if row["account_activated"] else "NO"))
                    == selected_account
                ]

        groups = self._group_rows(rows)
        overall = self._summary(rows)
        overall["exam_departments"] = len(groups)
        query_values = [
            (name, self.params.get(name))
            for name in self.FILTER_PARAMETER_NAMES
            if name in self.params
        ]

        selected_filter_labels = [
            ("Academic Year", selected_year.name if selected_year else "Unavailable"),
            ("Term", selected_term.name if selected_term else "Unavailable"),
            ("Exam Department", (
                UNASSIGNED_DEPARTMENT_LABEL
                if selected_department == "unassigned"
                else (
                    f"{selected_department.code} — {selected_department.name}"
                    if selected_department
                    else "All authorized"
                )
            )),
            ("Campus", selected_campus.name if selected_campus else "All authorized"),
            ("Assignment Status", self.ASSIGNMENT_LABELS.get(selected_assignment, "All")),
            ("Faculty Active", self.BOOLEAN_LABELS.get(selected_faculty_active, "All")),
            ("TMP Account Status", (
                "Activated" if selected_account == "YES" else "Not Activated" if selected_account == "NO" else "N/A" if selected_account == "NA" else "All"
            )),
        ]
        return {
            "tenant": self.tenant,
            "academic_years": academic_years,
            "terms": terms,
            "campuses": campuses,
            "departments": departments,
            "unassigned_available": unassigned_available,
            "assignment_status_options": [
                (code, self.ASSIGNMENT_LABELS[code])
                for code in ("ACCEPTED", "NOT_ACCEPTED", "NO_FACULTY")
                if code in assignment_codes
            ],
            "faculty_active_options": [
                (code, self.BOOLEAN_LABELS[code])
                for code in ("YES", "NO", "NA")
                if code in faculty_codes
            ],
            "account_status_options": [
                (code, "Activated" if code == "YES" else "Not Activated" if code == "NO" else "N/A")
                for code in ("YES", "NO", "NA")
                if code in account_codes
            ],
            "selected_academic_year": selected_year,
            "selected_term": selected_term,
            "selected_campus": selected_campus,
            "selected_exam_department": selected_department,
            "selected_assignment_status": selected_assignment,
            "selected_faculty_active": selected_faculty_active,
            "selected_account_status": selected_account,
            "selected_filter_labels": selected_filter_labels,
            "filter_query": urlencode(query_values),
            "groups": groups,
            "overall": overall,
        }
