from __future__ import annotations

import csv

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db.models import Count, Min, Prefetch, Q, Value
from django.db.models.functions import Concat
from django.http import HttpResponse, QueryDict
from django.shortcuts import render
from django.utils.text import slugify

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.academics.services import AcademicGovernanceService
from apps.admin_portal.services import AdminScopeService
from apps.core.decorators import permission_required, portal_required
from apps.core.services.csv_safety import csv_safe
from apps.core.services.permissions import PermissionService
from apps.core.services.scope import ScopeService
from apps.enrollment.models import Enrollment
from apps.rbac.models import UserRole
from apps.tenants.models import Campus, Tenant


RECONCILIATION_PERMISSION = "academic_data_reconciliation.view"
CATEGORY_OFFERINGS = "offerings"
CATEGORY_FACULTY = "faculty"
VALID_CATEGORIES = {CATEGORY_OFFERINGS, CATEGORY_FACULTY}
PAGE_SIZE = 50
HIGH_EXCEPTION_RATIO = 0.80
OFFERING_SORTS = {"course_code", "course_title", "section", "faculty"}
FACULTY_SORTS = {"faculty_name", "faculty_id"}


def _safe_int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _scope_context(request):
    tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
    campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
    return {
        "scope_tenants": Tenant.objects.filter(id__in=tenant_ids, is_active=True).order_by("name"),
        "scope_campuses": Campus.objects.filter(
            id__in=campus_ids, is_active=True, tenant__is_active=True
        ).order_by("name"),
        "current_tenant_id": getattr(request, "scope", {}).get("tenant_id"),
        "current_campus_id": getattr(request, "scope", {}).get("campus_id"),
    }


def _selected_scope(request):
    """Return only server-validated report scope values."""
    tenant_id = getattr(request, "scope", {}).get("tenant_id")
    tenant_options = AdminScopeService.active_scoped_tenants(request)
    if not tenant_id or not tenant_options.filter(id=tenant_id).exists():
        return None, None, tenant_options, AdminScopeService.active_scoped_campuses(request).none()

    campus_options = AdminScopeService.active_scoped_campuses(request).filter(tenant_id=tenant_id)
    requested_campus_id = _safe_int(request.GET.get("campus_id"))
    scope_campus_id = getattr(request, "scope", {}).get("campus_id")
    selected_campus = campus_options.filter(id=requested_campus_id).first() if requested_campus_id else None
    if selected_campus is None:
        selected_campus = campus_options.filter(id=scope_campus_id).first() or campus_options.first()

    # A user can have access to multiple campuses but a report permission only at
    # some of them. Do not let a page-level query parameter widen that permission.
    if selected_campus and not PermissionService.has_permission(
        request.user,
        RECONCILIATION_PERMISSION,
        tenant_id=tenant_id,
        campus_id=selected_campus.id,
    ):
        selected_campus = None
    return tenant_id, selected_campus, tenant_options, campus_options


def _selected_period(request, tenant_id):
    year_options = AdminScopeService.active_scoped_academic_years(request).filter(tenant_id=tenant_id)
    term_options = AdminScopeService.active_scoped_terms(request).filter(tenant_id=tenant_id)
    requested_year_id = _safe_int(request.GET.get("academic_year_id"))
    requested_term_id = _safe_int(request.GET.get("term_id"))
    selected_year = year_options.filter(id=requested_year_id).first() if requested_year_id else None
    selected_term = term_options.filter(id=requested_term_id).first() if requested_term_id else None

    if selected_term and selected_year and selected_term.academic_year_id != selected_year.id:
        selected_term = None
    if selected_term and selected_year is None:
        selected_year = selected_term.academic_year
    if selected_term is None:
        active_year, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=tenant_id)
        if active_term and term_options.filter(id=active_term.id).exists():
            selected_term = active_term
            selected_year = active_year
        elif selected_year:
            selected_term = term_options.filter(academic_year_id=selected_year.id).order_by("sequence_no", "id").first()
        else:
            selected_term = term_options.order_by("-academic_year__start_date", "sequence_no", "id").first()
            selected_year = selected_term.academic_year if selected_term else None

    term_options = term_options.filter(academic_year_id=selected_year.id) if selected_year else term_options.none()
    return selected_year, selected_term, year_options, term_options


def _department_ids_for_scope(request, *, tenant_id, campus_id):
    if request.user.is_superuser:
        return None
    admin_roles = UserRole.objects.filter(
        user=request.user,
        is_active=True,
        role__is_active=True,
    ).exclude(role__code="FACULTY").filter(
        Q(tenant_id=tenant_id) | Q(tenant__isnull=True),
        Q(campus_id=campus_id) | Q(campus__isnull=True),
    )
    if admin_roles.filter(department__isnull=True).exists():
        return None
    return ScopeService.get_accessible_department_ids(
        request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    )


def _active_offerings(request, *, tenant_id, campus_id, academic_year_id, term_id):
    queryset = AdminScopeService.scoped_course_offerings(request).filter(
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year_id=academic_year_id,
        term_id=term_id,
        is_active=True,
    ).exclude(status=CourseOffering.Status.ARCHIVED)
    return queryset


def _active_faculty_ids(request, *, tenant_id, campus_id):
    faculty_roles = UserRole.objects.filter(
        role__code="FACULTY",
        role__is_active=True,
        is_active=True,
        user__is_active=True,
    ).filter(
        Q(tenant_id=tenant_id) | Q(tenant__isnull=True, user__default_tenant_id=tenant_id),
        Q(campus_id=campus_id) | Q(campus__isnull=True, user__default_campus_id=campus_id),
    )
    department_ids = _department_ids_for_scope(request, tenant_id=tenant_id, campus_id=campus_id)
    if department_ids is not None:
        if not department_ids:
            return faculty_roles.none().values_list("user_id", flat=True)
        faculty_roles = faculty_roles.filter(
            Q(department_id__in=department_ids)
            | Q(
                department__isnull=True,
                user__default_campus_id=campus_id,
                user__default_department_id__in=department_ids,
            )
        )
    return faculty_roles.values_list("user_id", flat=True).distinct()


def _offering_query(request, *, tenant_id, campus_id, academic_year_id, term_id, search_query=""):
    active_assignments = FacultyAssignment.objects.filter(is_active=True).select_related("faculty_user").order_by(
        "-is_primary", "faculty_user__last_name", "faculty_user__first_name", "id"
    )
    queryset = _active_offerings(
        request,
        tenant_id=tenant_id,
        campus_id=campus_id,
        academic_year_id=academic_year_id,
        term_id=term_id,
    ).annotate(
        active_enrollment_count=Count(
            "enrollments",
            filter=Q(
                enrollments__is_active=True,
                enrollments__enrollment_status=Enrollment.Status.ACTIVE,
            ),
            distinct=True,
        ),
        active_assignment_count=Count("faculty_assignments", filter=Q(faculty_assignments__is_active=True), distinct=True),
        faculty_sort_name=Min(
            Concat(
                "faculty_assignments__faculty_user__last_name",
                Value(" "),
                "faculty_assignments__faculty_user__first_name",
                Value(" "),
                "faculty_assignments__faculty_user__username",
            ),
            filter=Q(faculty_assignments__is_active=True),
        ),
    ).prefetch_related(Prefetch("faculty_assignments", queryset=active_assignments, to_attr="reconciliation_assignments"))
    if search_query:
        queryset = queryset.filter(
            Q(course__code__icontains=search_query)
            | Q(course__title__icontains=search_query)
            | Q(section__code__icontains=search_query)
            | Q(section__name__icontains=search_query)
            | Q(faculty_assignments__is_active=True, faculty_assignments__faculty_user__first_name__icontains=search_query)
            | Q(faculty_assignments__is_active=True, faculty_assignments__faculty_user__middle_name__icontains=search_query)
            | Q(faculty_assignments__is_active=True, faculty_assignments__faculty_user__last_name__icontains=search_query)
            | Q(faculty_assignments__is_active=True, faculty_assignments__faculty_user__username__icontains=search_query)
        ).distinct()
    return queryset


def _faculty_query(request, *, tenant_id, campus_id, academic_year_id, term_id, search_query=""):
    User = get_user_model()
    active_assignment_filter = Q(
        faculty_assignments__is_active=True,
        faculty_assignments__offering__tenant_id=tenant_id,
        faculty_assignments__offering__campus_id=campus_id,
        faculty_assignments__offering__academic_year_id=academic_year_id,
        faculty_assignments__offering__term_id=term_id,
        faculty_assignments__offering__is_active=True,
        faculty_assignments__offering__status__in=[CourseOffering.Status.OPEN, CourseOffering.Status.CLOSED],
    )
    queryset = User.objects.filter(id__in=_active_faculty_ids(request, tenant_id=tenant_id, campus_id=campus_id)).annotate(
        assignment_count=Count("faculty_assignments", filter=active_assignment_filter, distinct=True),
        full_name_search=Concat("first_name", Value(" "), "middle_name", Value(" "), "last_name"),
    )
    if search_query:
        queryset = queryset.filter(
            Q(username__icontains=search_query)
            | Q(first_name__icontains=search_query)
            | Q(middle_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(full_name_search__icontains=search_query)
            | Q(email__icontains=search_query)
        )
    return queryset


def _selected_sort(category, value):
    allowed = OFFERING_SORTS if category == CATEGORY_OFFERINGS else FACULTY_SORTS
    default = "course_code" if category == CATEGORY_OFFERINGS else "faculty_name"
    return value if value in allowed else default


def _apply_sort(queryset, *, category, sort):
    if category == CATEGORY_OFFERINGS:
        ordering = {
            "course_code": ("course__code", "section__code", "id"),
            "course_title": ("course__title", "course__code", "section__code", "id"),
            "section": ("section__code", "course__code", "id"),
            "faculty": ("faculty_sort_name", "course__code", "section__code", "id"),
        }
    else:
        ordering = {
            "faculty_name": ("last_name", "first_name", "username"),
            "faculty_id": ("username",),
        }
    return queryset.order_by(*ordering[sort])


def _percent(numerator, denominator):
    if not denominator:
        return 0
    return round((numerator / denominator) * 100, 1)


def _high_exception_warnings(summary):
    warnings = []
    offering_total = summary["total_active_offerings"]
    offering_count = summary["offerings_without_enrollment"]
    if offering_total and offering_count / offering_total >= HIGH_EXCEPTION_RATIO:
        warnings.append(
            "Enrollment data may still be incomplete for the selected academic period. "
            f"{offering_count} of {offering_total} active course offerings currently have no active enrollment records."
        )
    faculty_total = summary["total_active_faculty"]
    faculty_count = summary["faculty_without_assignments"]
    if faculty_total and faculty_count / faculty_total >= HIGH_EXCEPTION_RATIO:
        warnings.append(
            "Faculty assignment data may still be incomplete for the selected academic period. "
            f"{faculty_count} of {faculty_total} active faculty members currently have no active assignments."
        )
    return warnings


def _faculty_names(offering):
    assignments = getattr(offering, "reconciliation_assignments", [])
    return ", ".join(assignment.faculty_user.full_name for assignment in assignments) or "No faculty assigned"


def _csv_response(*, category, offerings, faculty, campus, academic_year, term):
    filename_prefix = "course_offerings_without_enrollment" if category == CATEGORY_OFFERINGS else "faculty_without_assignments"
    filename = "_".join(
        slugify(value) or "scope" for value in (filename_prefix, campus.code, academic_year.code, term.code)
    ) + ".csv"
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    if category == CATEGORY_OFFERINGS:
        writer.writerow([
            "Campus", "Course Code", "Course Title", "Section Code", "Section Name", "Faculty Assigned", "Number of Faculty Assignments",
            "Enrollment Count", "Academic Year", "Term", "Reconciliation Status",
        ])
        for offering in offerings:
            writer.writerow([
                csv_safe(offering.campus.code), csv_safe(offering.course.code), csv_safe(offering.course.title),
                csv_safe(offering.section.code), csv_safe(offering.section.name), csv_safe(_faculty_names(offering)), offering.active_assignment_count,
                offering.active_enrollment_count, csv_safe(offering.academic_year.code), csv_safe(offering.term.code), "No Enrollment",
            ])
    else:
        writer.writerow(["Campus", "Faculty ID", "Faculty Name", "Email", "Assignment Count", "Academic Year", "Term", "Reconciliation Status"])
        for user in faculty:
            writer.writerow([
                csv_safe(campus.code), csv_safe(user.username), csv_safe(user.full_name), csv_safe(user.email),
                user.assignment_count, csv_safe(academic_year.code), csv_safe(term.code), "No Assignment",
            ])
    return response


@portal_required("ADMIN")
@permission_required(RECONCILIATION_PERMISSION)
def academic_data_reconciliation_view(request):
    tenant_id, selected_campus, tenant_options, campus_options = _selected_scope(request)
    if not tenant_id or selected_campus is None:
        context = {
            "scope_unavailable": True,
            "tenant_options": tenant_options,
            "campus_options": campus_options,
        }
        context.update(_scope_context(request))
        return render(request, "admin_portal/academics/academic_data_reconciliation.html", context)

    selected_year, selected_term, year_options, term_options = _selected_period(request, tenant_id)
    category = (request.GET.get("category") or CATEGORY_OFFERINGS).lower()
    category = category if category in VALID_CATEGORIES else CATEGORY_OFFERINGS
    search_query = (request.GET.get("q") or "").strip()[:100]
    selected_sort = _selected_sort(category, (request.GET.get("sort") or "").strip())

    offerings = CourseOffering.objects.none()
    faculty = get_user_model().objects.none()
    summary = {
        "total_active_offerings": 0,
        "offerings_without_enrollment": 0,
        "total_active_faculty": 0,
        "faculty_without_assignments": 0,
        "offerings_without_enrollment_percent": 0,
        "faculty_without_assignments_percent": 0,
    }
    if selected_year and selected_term:
        offering_base = _offering_query(
            request,
            tenant_id=tenant_id,
            campus_id=selected_campus.id,
            academic_year_id=selected_year.id,
            term_id=selected_term.id,
        )
        faculty_base = _faculty_query(
            request,
            tenant_id=tenant_id,
            campus_id=selected_campus.id,
            academic_year_id=selected_year.id,
            term_id=selected_term.id,
        )
        summary = {
            "total_active_offerings": offering_base.count(),
            "offerings_without_enrollment": offering_base.filter(active_enrollment_count=0).count(),
            "total_active_faculty": faculty_base.count(),
            "faculty_without_assignments": faculty_base.filter(assignment_count=0).count(),
        }
        summary["offerings_without_enrollment_percent"] = _percent(
            summary["offerings_without_enrollment"], summary["total_active_offerings"]
        )
        summary["faculty_without_assignments_percent"] = _percent(
            summary["faculty_without_assignments"], summary["total_active_faculty"]
        )
        if category == CATEGORY_OFFERINGS:
            offerings = _apply_sort(_offering_query(
                request,
                tenant_id=tenant_id,
                campus_id=selected_campus.id,
                academic_year_id=selected_year.id,
                term_id=selected_term.id,
                search_query=search_query,
            ).filter(active_enrollment_count=0), category=category, sort=selected_sort)
        else:
            faculty = _apply_sort(_faculty_query(
                request,
                tenant_id=tenant_id,
                campus_id=selected_campus.id,
                academic_year_id=selected_year.id,
                term_id=selected_term.id,
                search_query=search_query,
            ).filter(assignment_count=0), category=category, sort=selected_sort)

    if request.GET.get("export") == "csv" and selected_year and selected_term:
        return _csv_response(
            category=category,
            offerings=offerings if category == CATEGORY_OFFERINGS else (),
            faculty=faculty if category == CATEGORY_FACULTY else (),
            campus=selected_campus,
            academic_year=selected_year,
            term=selected_term,
        )

    result_queryset = offerings if category == CATEGORY_OFFERINGS else faculty
    paginator = Paginator(result_queryset, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = QueryDict(mutable=True)
    query_params["campus_id"] = selected_campus.id
    if selected_year:
        query_params["academic_year_id"] = selected_year.id
    if selected_term:
        query_params["term_id"] = selected_term.id
    query_params["category"] = category
    query_params["sort"] = selected_sort
    if search_query:
        query_params["q"] = search_query
    sort_links = {}
    for sort_code in OFFERING_SORTS if category == CATEGORY_OFFERINGS else FACULTY_SORTS:
        sort_params = query_params.copy()
        sort_params["sort"] = sort_code
        sort_links[sort_code] = sort_params.urlencode()
    tab_queries = {}
    for tab_category in VALID_CATEGORIES:
        tab_params = query_params.copy()
        tab_params["category"] = tab_category
        tab_params.pop("page", None)
        tab_queries[tab_category] = tab_params.urlencode()
    lazy_next_url = ""
    next_page_url = ""
    if category == CATEGORY_OFFERINGS and page_obj.has_next():
        next_page_params = query_params.copy()
        next_page_params["page"] = page_obj.next_page_number()
        next_page_url = f"?{next_page_params.urlencode()}"
        lazy_params = query_params.copy()
        lazy_params["page"] = page_obj.next_page_number()
        lazy_params["lazy"] = "1"
        lazy_next_url = f"?{lazy_params.urlencode()}"
    active_tab_description = (
        "Showing active, non-archived course offerings in the selected scope with no active student enrollment records."
        if category == CATEGORY_OFFERINGS
        else "Showing active faculty members with a scoped FACULTY role and no active teaching assignments in the selected term."
    )
    context = {
        "category": category,
        "search_query": search_query,
        "selected_sort": selected_sort,
        "selected_campus": selected_campus,
        "selected_campus_id": selected_campus.id,
        "selected_year": selected_year,
        "selected_year_id": getattr(selected_year, "id", None),
        "selected_term": selected_term,
        "selected_term_id": getattr(selected_term, "id", None),
        "campus_options": campus_options,
        "academic_year_options": year_options,
        "term_options": term_options,
        "offerings": page_obj.object_list if category == CATEGORY_OFFERINGS else (),
        "faculty": page_obj.object_list if category == CATEGORY_FACULTY else (),
        "page_obj": page_obj,
        "summary": summary,
        "high_exception_warnings": _high_exception_warnings(summary),
        "active_tab_description": active_tab_description,
        "export_label": "Export No-Enrollment Offerings" if category == CATEGORY_OFFERINGS else "Export Unassigned Faculty",
        "sort_links": sort_links,
        "tab_queries": tab_queries,
        "result_total": paginator.count,
        "pagination_query": query_params.urlencode(),
        "lazy_next_url": lazy_next_url,
        "next_page_url": next_page_url,
    }
    context.update(_scope_context(request))
    if category == CATEGORY_OFFERINGS and request.GET.get("lazy") == "1":
        response = render(request, "admin_portal/academics/_academic_data_reconciliation_offering_rows.html", context)
        if lazy_next_url:
            response["X-Reconciliation-Next-Url"] = lazy_next_url
        if next_page_url:
            response["X-Reconciliation-Next-Page-Url"] = next_page_url
        return response
    return render(request, "admin_portal/academics/academic_data_reconciliation.html", context)
