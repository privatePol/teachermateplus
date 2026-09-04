from __future__ import annotations

from functools import wraps
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from apps.core.decorators import portal_required
from apps.core.services.settings import SystemSettingService
from apps.tenants.models import Campus

from .contribution_forms import RosterActionForm
from .contribution_authorization import ContributorEligibilityService
from .contribution_selectors import ContributionMonitoringSelector
from .contribution_services import ContributionRosterService
from .blueprint_services import (
    contribution_source_evidence,
    resolution_matches_episode,
)
from .models import CycleCourse, ExaminationCycle, FacultyContribution
from .services import DepartmentalExamAuthorizationService


def _tenant_id(request):
    return getattr(request, "scope", {}).get("tenant_id") or getattr(
        request.user, "default_tenant_id", None
    )


def _admin_error_response(request, *, status):
    if status == 403:
        title = "Contributor monitoring unavailable"
        explanation = "This monitoring page or roster action is not available within your current exact scope."
        next_action = "Return to the Admin Portal and choose an examination available to your assigned role."
    else:
        title = "Roster request could not be processed"
        explanation = "The roster action or submitted confirmation is missing or invalid. No roster change was made."
        next_action = "Return to Contributor Completion and start the action again."
    return render(
        request,
        "departmental_exams/admin/error.html",
        {
            "error_title": title,
            "error_explanation": explanation,
            "error_next_action": next_action,
        },
        status=status,
    )


def _admin_error_page(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except PermissionDenied:
            return _admin_error_response(request, status=403)
        except ValidationError:
            return _admin_error_response(request, status=400)

    return wrapped


def _monitoring_scope_context(request):
    tenant_id = _tenant_id(request)
    DepartmentalExamAuthorizationService.require_assigned_course_route_capability(
        user=request.user, tenant_id=tenant_id
    )
    if not ContributionMonitoringSelector.navigation_visible(
        user=request.user, tenant_id=tenant_id
    ):
        raise PermissionDenied("No exact-scoped contributor monitoring assignment is available.")
    visible_courses = list(
        ContributionMonitoringSelector.visible_cycle_courses(
            user=request.user, tenant_id=tenant_id
        )
    )
    cycles_by_id = {
        course.cycle_id: course.cycle for course in visible_courses
    }
    courses_by_id = {
        course.course_id: course.course for course in visible_courses
    }
    contributors_by_id = {
        contribution.faculty_user_id: contribution.faculty_user
        for course in visible_courses
        for contribution in course.faculty_contributions.all()
    }

    def selected_int(name, valid_ids):
        try:
            value = int(request.GET.get(name, ""))
        except (TypeError, ValueError):
            return None
        return value if value in valid_ids else None

    selected_cycle_id = selected_int("cycle", cycles_by_id)
    selected_course_id = selected_int("course", courses_by_id)
    selected_period = request.GET.get("period", "")
    if selected_period not in ExaminationCycle.ExamPeriod.values:
        selected_period = ""
    raw_contributor_id = request.GET.get("contributor", "").strip()
    selected_contributor_id = None
    invalid_contributor_filter = False
    if raw_contributor_id:
        try:
            requested_contributor_id = int(raw_contributor_id)
        except (TypeError, ValueError):
            invalid_contributor_filter = True
        else:
            if requested_contributor_id in contributors_by_id:
                selected_contributor_id = requested_contributor_id
            else:
                invalid_contributor_filter = True

    courses = [
        course
        for course in visible_courses
        if (selected_cycle_id is None or course.cycle_id == selected_cycle_id)
        and (not selected_period or course.cycle.exam_period == selected_period)
        and (selected_course_id is None or course.course_id == selected_course_id)
    ]
    if invalid_contributor_filter:
        courses = []
    else:
        for course in courses:
            course.monitoring_contributions = [
                contribution
                for contribution in course.faculty_contributions.all()
                if (
                    selected_contributor_id is None
                    or contribution.faculty_user_id == selected_contributor_id
                )
            ]
        if selected_contributor_id is not None:
            courses = [course for course in courses if course.monitoring_contributions]
    selected_filters = {
        "cycle": selected_cycle_id or "",
        "period": selected_period,
        "course": selected_course_id or "",
        "contributor": (
            selected_contributor_id
            if selected_contributor_id is not None
            else raw_contributor_id if invalid_contributor_filter else ""
        ),
    }
    filter_query = urlencode(
        {key: value for key, value in selected_filters.items() if value}
    )
    return {
        "tenant_id": tenant_id,
        "courses": courses,
        "cycle_choices": sorted(
            cycles_by_id.values(),
            key=lambda cycle: (
                cycle.academic_year.name,
                cycle.term.name,
                cycle.exam_period,
                cycle.id,
            ),
        ),
        "period_choices": ExaminationCycle.ExamPeriod.choices,
        "course_choices": sorted(
            courses_by_id.values(),
            key=lambda course: (course.code, course.id),
        ),
        "contributor_choices": sorted(
            contributors_by_id.values(),
            key=lambda contributor: (
                contributor.last_name,
                contributor.first_name,
                contributor.username,
                contributor.id,
            ),
        ),
        "selected_cycle_id": selected_cycle_id,
        "selected_period": selected_period,
        "selected_course_id": selected_course_id,
        "selected_contributor_id": selected_contributor_id,
        "filter_query": filter_query,
    }


def _course_contributions(course):
    contributions = getattr(course, "monitoring_contributions", None)
    if contributions is not None:
        return contributions
    return list(course.faculty_contributions.all())


def _decorate_contribution_metrics(courses):
    for course in courses:
        for contribution in _course_contributions(course):
            contribution.progress_percent = round(
                (contribution.saved_question_count / contribution.quota_snapshot) * 100
            )


@_admin_error_page
@portal_required("ADMIN")
def contributor_monitoring_view(request):
    context = _monitoring_scope_context(request)
    courses = context["courses"]
    tenant_id = context["tenant_id"]
    _decorate_contribution_metrics(courses)
    _decorate_contributor_locations(courses=courses, tenant_id=tenant_id)
    manual_course_ids = [
        course.pk
        for course in courses
        if course.cycle.processing_mode
        == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
    ]
    configurer_ids = set(
        DepartmentalExamAuthorizationService.configurer_visible_cycle_courses(
            user=request.user,
            tenant_id=tenant_id,
            queryset=CycleCourse.objects.filter(pk__in=manual_course_ids),
        ).values_list("pk", flat=True)
    )
    automatic_permissions = DepartmentalExamAuthorizationService.automatic_permission_map(
        user=request.user,
        courses=courses,
        permissions=(
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
        ),
    )
    automatic_inclusion_permissions = (
        DepartmentalExamAuthorizationService.automatic_inclusion_management_map(
            user=request.user,
            courses=courses,
        )
    )
    manage_permission = (
        DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
    )
    automatic_manage_ids = {
        course.id
        for course in courses
        if manage_permission in automatic_permissions[course.id]
    }
    context["show_automatic_generation_readiness"] = any(
        (
            course.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
            and manage_permission in automatic_permissions[course.id]
        )
        or (
            course.inclusion_status == CycleCourse.InclusionStatus.EXEMPT
            and manage_permission in automatic_inclusion_permissions[course.id]
        )
        for course in courses
    )
    for course in courses:
        for contribution in _course_contributions(course):
            configuration = getattr(course, "configuration", None)
            contribution.is_overdue = bool(
                configuration
                and configuration.active_contribution_deadline
                and contribution.status == contribution.Status.DRAFT
                and configuration.active_contribution_deadline <= timezone.now()
            )
            contribution.blocked_resolution_valid = bool(
                configuration
                and contribution.status == contribution.Status.DRAFT
                and contribution.roster_status == contribution.RosterStatus.BLOCKED
                and any(
                    resolution_matches_episode(
                        resolution=resolution,
                        contribution=contribution,
                        source_hash=contribution_source_evidence(contribution),
                    )
                    for resolution in contribution.blocked_resolution_events.all()
                )
            )
        if (
            course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
        ):
            course.can_configure = course.pk in configurer_ids
        elif (
            course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        ):
            course.can_configure = course.pk in automatic_manage_ids
        else:
            course.can_configure = False
    return render(
        request,
        "departmental_exams/admin/contributor_monitoring.html",
        context,
    )


def _source_assignment_matches_report_scope(*, source, course, offering_scope):
    assignment = source.assignment
    if assignment is None or assignment.id != source.assignment_id_snapshot:
        return False
    offering = assignment.offering
    effective_scope = ContributorEligibilityService._effective_scope(assignment)
    return bool(
        offering.id == source.offering_id_snapshot
        and offering_scope.get(offering.id) == source.campus_id_snapshot
        and effective_scope
        == (source.tenant_id_snapshot, source.campus_id_snapshot)
        and offering.tenant_id == course.cycle.tenant_id
        and offering.course_id == course.course_id
        and offering.academic_year_id == course.cycle.academic_year_id
        and offering.term_id == course.cycle.term_id
    )


def _decorate_contributor_locations(*, courses, tenant_id):
    for course in courses:
        snapshots = list(course.offering_snapshots.all())
        offering_scope = {
            snapshot.offering_id: snapshot.campus_id for snapshot in snapshots
        }
        campus_names = {
            snapshot.campus_id: snapshot.campus.name
            for snapshot in snapshots
            if snapshot.campus.tenant_id == tenant_id
        }
        contributions = _course_contributions(course)
        for contribution in contributions:
            contributor_campuses = set()
            contributor_sections = set()
            for source in contribution.eligibility_sources.all():
                if (
                    not source.is_current
                    or source.tenant_id_snapshot != tenant_id
                    or offering_scope.get(source.offering_id_snapshot)
                    != source.campus_id_snapshot
                ):
                    continue
                campus_name = campus_names.get(source.campus_id_snapshot)
                if campus_name:
                    contributor_campuses.add(campus_name)
                if _source_assignment_matches_report_scope(
                    source=source,
                    course=course,
                    offering_scope=offering_scope,
                ):
                    section_code = (source.assignment.offering.section.code or "").strip()
                    if section_code:
                        contributor_sections.add(section_code)
            contribution.print_campus_names = sorted(
                contributor_campuses, key=str.casefold
            )
            contribution.print_section_codes = sorted(
                contributor_sections, key=str.casefold
            )
        course.print_campus_names = sorted(
            set(campus_names.values()), key=str.casefold
        )
        course.represented_offering_count = len(snapshots)


def _decorate_print_report(*, courses, tenant_id):
    _decorate_contribution_metrics(courses)
    _decorate_contributor_locations(courses=courses, tenant_id=tenant_id)
    for course_number, course in enumerate(courses, start=1):
        contributions = _course_contributions(course)
        course.print_number = course_number
        course.total_contributors = len(contributions)
        course.total_questions_saved = sum(
            contribution.saved_question_count for contribution in contributions
        )
        course.total_questions_required = sum(
            contribution.quota_snapshot for contribution in contributions
        )


def _faculty_report_name(user):
    last_name = (user.last_name or "").strip()
    first_name = (user.first_name or "").strip()
    if last_name and first_name:
        return f"{last_name}, {first_name}"
    return last_name or first_name or user.username


def _faculty_contribution_summary(contributions):
    rows_by_faculty = {}
    seen_contribution_ids = set()
    for contribution in contributions:
        if (
            contribution.id in seen_contribution_ids
            or contribution.status
            not in (
                FacultyContribution.Status.DRAFT,
                FacultyContribution.Status.SUBMITTED,
            )
        ):
            continue
        seen_contribution_ids.add(contribution.id)
        faculty = contribution.faculty_user
        row = rows_by_faculty.setdefault(
            faculty.id,
            {
                "faculty": faculty,
                "display_name": _faculty_report_name(faculty),
                "draft_count": 0,
                "submitted_count": 0,
            },
        )
        if contribution.status == FacultyContribution.Status.DRAFT:
            row["draft_count"] += 1
        else:
            row["submitted_count"] += 1

    rows = list(rows_by_faculty.values())
    for row in rows:
        row["course_count"] = row["draft_count"] + row["submitted_count"]
    return sorted(
        rows,
        key=lambda row: (
            (row["faculty"].last_name or "").casefold(),
            (row["faculty"].first_name or "").casefold(),
            row["faculty"].username.casefold(),
            row["faculty"].id,
        ),
    )


def _effective_report_campus(request, *, tenant_id):
    campus_id = getattr(request, "scope", {}).get("campus_id")
    if not campus_id:
        return None
    return Campus.objects.filter(
        pk=campus_id,
        tenant_id=tenant_id,
        is_active=True,
    ).first()


def _contribution_matches_campus_scope(
    *, contribution, course, tenant_id, campus_id
):
    offering_ids = {
        snapshot.offering_id
        for snapshot in course.offering_snapshots.all()
        if snapshot.campus_id == campus_id
    }
    if not offering_ids:
        return False
    return any(
        source.is_current
        and source.tenant_id_snapshot == tenant_id
        and source.campus_id_snapshot == campus_id
        and source.offering_id_snapshot in offering_ids
        for source in contribution.eligibility_sources.all()
    )


@_admin_error_page
@portal_required("ADMIN")
@require_http_methods(["GET"])
def contributor_monitoring_print_view(request):
    context = _monitoring_scope_context(request)
    courses = context["courses"]
    _decorate_print_report(courses=courses, tenant_id=context["tenant_id"])
    cycle_contexts = []
    seen_cycle_ids = set()
    for course in courses:
        if course.cycle_id in seen_cycle_ids:
            continue
        seen_cycle_ids.add(course.cycle_id)
        cycle_contexts.append(
            f"{course.cycle.academic_year} / {course.cycle.term} / "
            f"{course.cycle.get_exam_period_display()}"
        )
    context.update(
        {
            "cycle_contexts": cycle_contexts,
            "generated_at": timezone.localtime(
                timezone.now(), timezone=ZoneInfo("Asia/Manila")
            ),
            "total_authorized_courses": len(courses),
            "total_course_offerings": sum(
                course.represented_offering_count for course in courses
            ),
        }
    )
    return render(
        request,
        "departmental_exams/admin/contributor_monitoring_print.html",
        context,
    )


@_admin_error_page
@portal_required("ADMIN")
@require_http_methods(["GET"])
def contributor_monitoring_draft_print_view(request, cycle_id):
    tenant_id = _tenant_id(request)
    DepartmentalExamAuthorizationService.require_assigned_course_route_capability(
        user=request.user, tenant_id=tenant_id
    )
    if not ContributionMonitoringSelector.navigation_visible(
        user=request.user, tenant_id=tenant_id
    ):
        raise PermissionDenied(
            "No exact-scoped contributor monitoring assignment is available."
        )
    visible_courses = ContributionMonitoringSelector.visible_cycle_courses(
        user=request.user,
        tenant_id=tenant_id,
        contribution_status=FacultyContribution.Status.DRAFT,
    )
    if not visible_courses.filter(cycle_id=cycle_id).exists():
        raise PermissionDenied(
            "The selected examination cycle is not available within the current exact scope."
        )

    cycle = get_object_or_404(
        ExaminationCycle.objects.select_related("academic_year", "term"),
        pk=cycle_id,
        tenant_id=tenant_id,
    )
    courses = list(
        visible_courses.filter(
            cycle_id=cycle_id,
            inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
            faculty_contributions__status=FacultyContribution.Status.DRAFT,
        )
        .distinct()
        .order_by("course__code", "course__title", "id")
    )
    for course in courses:
        course.monitoring_contributions = list(course.faculty_contributions.all())
    _decorate_contribution_metrics(courses)
    _decorate_contributor_locations(courses=courses, tenant_id=tenant_id)
    faculty_summary = _faculty_contribution_summary(
        contribution
        for course in courses
        for contribution in course.monitoring_contributions
    )

    return render(
        request,
        "departmental_exams/admin/contributor_monitoring_draft_print.html",
        {
            "cycle": cycle,
            "courses": courses,
            "faculty_summary": faculty_summary,
            "generated_at": timezone.localtime(
                timezone.now(), timezone=ZoneInfo("Asia/Manila")
            ),
            "print_header_name": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_NAME",
                tenant_id=tenant_id,
                default="NATIONAL COLLEGE OF BUSINESS AND ARTS",
            ),
            "print_header_address": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_ADDRESS",
                tenant_id=tenant_id,
                default="",
            ),
        },
    )


@_admin_error_page
@portal_required("ADMIN")
@require_http_methods(["GET"])
def contributor_monitoring_faculty_submission_print_view(request, cycle_id):
    tenant_id = _tenant_id(request)
    DepartmentalExamAuthorizationService.require_assigned_course_route_capability(
        user=request.user, tenant_id=tenant_id
    )
    if not ContributionMonitoringSelector.navigation_visible(
        user=request.user, tenant_id=tenant_id
    ):
        raise PermissionDenied(
            "No exact-scoped contributor monitoring assignment is available."
        )
    visible_courses = ContributionMonitoringSelector.visible_cycle_courses(
        user=request.user,
        tenant_id=tenant_id,
    )
    if not visible_courses.filter(cycle_id=cycle_id).exists():
        raise PermissionDenied(
            "The selected examination cycle is not available within the current exact scope."
        )

    cycle = get_object_or_404(
        ExaminationCycle.objects.select_related("academic_year", "term"),
        pk=cycle_id,
        tenant_id=tenant_id,
    )
    default_campus = _effective_report_campus(request, tenant_id=tenant_id)
    summary_rows = []
    if default_campus is not None:
        courses = list(
            visible_courses.filter(
                cycle_id=cycle_id,
                inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
            ).order_by("course__code", "course__title", "id")
        )
        qualifying_contributions = []
        for course in courses:
            for contribution in course.faculty_contributions.all():
                if (
                    contribution.status
                    in (
                        FacultyContribution.Status.DRAFT,
                        FacultyContribution.Status.SUBMITTED,
                    )
                    and _contribution_matches_campus_scope(
                        contribution=contribution,
                        course=course,
                        tenant_id=tenant_id,
                        campus_id=default_campus.id,
                    )
                ):
                    qualifying_contributions.append(contribution)
        summary_rows = _faculty_contribution_summary(qualifying_contributions)

    return render(
        request,
        "departmental_exams/admin/contributor_monitoring_faculty_submission_print.html",
        {
            "cycle": cycle,
            "default_campus": default_campus,
            "summary_rows": summary_rows,
            "generated_at": timezone.localtime(
                timezone.now(), timezone=ZoneInfo("Asia/Manila")
            ),
            "print_header_name": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_NAME",
                tenant_id=tenant_id,
                default="NATIONAL COLLEGE OF BUSINESS AND ARTS",
            ),
            "print_header_address": SystemSettingService.get(
                "PRINT_HEADER_SCHOOL_ADDRESS",
                tenant_id=tenant_id,
                default="",
            ),
        },
    )


@_admin_error_page
@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def roster_action_view(request, cycle_course_id, action):
    if action not in {"initialize", "synchronize"}:
        raise ValidationError("Unsupported roster action.")
    tenant_id = _tenant_id(request)
    cycle_course = get_object_or_404(
        CycleCourse.objects.select_related(
            "cycle", "cycle__tenant", "course", "responsible_department", "configuration"
        ),
        pk=cycle_course_id,
        cycle__tenant_id=tenant_id,
    )
    DepartmentalExamAuthorizationService.require_configure_cycle_course(
        user=request.user, cycle_course=cycle_course
    )
    form = RosterActionForm(request.POST or None)
    filter_query = urlencode(
        {
            key: request.GET.get(key)
            for key in ("cycle", "period", "course", "contributor")
            if request.GET.get(key)
        }
    )
    if request.method == "POST" and form.is_valid():
        method = (
            ContributionRosterService.initialize
            if action == "initialize"
            else ContributionRosterService.synchronize
        )
        try:
            result = method(
                cycle_course_id=cycle_course.id,
                tenant_id=tenant_id,
                actor=request.user,
                request=request,
            )
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            messages.success(
                request,
                f"Roster {action} complete: {result['created']} created, "
                f"{result['activated']} activated, {result['blocked']} blocked.",
            )
            monitoring_url = reverse("departmental_exams:contributor_monitoring")
            if filter_query:
                monitoring_url = f"{monitoring_url}?{filter_query}"
            return redirect(monitoring_url)
    return render(
        request,
        "departmental_exams/admin/roster_action_confirm.html",
        {
            "form": form,
            "cycle_course": cycle_course,
            "action": action,
            "filter_query": filter_query,
        },
        status=400 if request.method == "POST" and form.errors else 200,
    )
