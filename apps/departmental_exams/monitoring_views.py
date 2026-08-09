from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from apps.core.decorators import portal_required

from .contribution_forms import RosterActionForm
from .contribution_selectors import ContributionMonitoringSelector
from .contribution_services import ContributionRosterService
from .blueprint_services import (
    contribution_source_evidence,
    resolution_matches_episode,
)
from .models import CycleCourse
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


@_admin_error_page
@portal_required("ADMIN")
def contributor_monitoring_view(request):
    tenant_id = _tenant_id(request)
    DepartmentalExamAuthorizationService.require_assigned_course_route_capability(
        user=request.user, tenant_id=tenant_id
    )
    if not ContributionMonitoringSelector.navigation_visible(
        user=request.user, tenant_id=tenant_id
    ):
        raise PermissionDenied("No exact-scoped contributor monitoring assignment is available.")
    courses = list(
        ContributionMonitoringSelector.visible_cycle_courses(
            user=request.user, tenant_id=tenant_id
        )
    )
    configurer_ids = set(
        DepartmentalExamAuthorizationService.configurer_visible_cycle_courses(
            user=request.user,
            tenant_id=tenant_id,
            queryset=CycleCourse.objects.filter(pk__in=[course.pk for course in courses]),
        ).values_list("pk", flat=True)
    )
    for course in courses:
        for contribution in course.faculty_contributions.all():
            sources = list(contribution.eligibility_sources.all())
            contribution.valid_source_count = sum(1 for source in sources if source.is_current)
            contribution.invalid_source_count = len(sources) - contribution.valid_source_count
            contribution.progress_percent = round(
                (contribution.saved_question_count / contribution.quota_snapshot) * 100
            )
            configuration = getattr(course, "configuration", None)
            contribution.is_overdue = bool(
                configuration
                and configuration.contribution_deadline
                and contribution.status == contribution.Status.DRAFT
                and configuration.contribution_deadline <= timezone.now()
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
        course.can_configure = course.pk in configurer_ids
    return render(
        request,
        "departmental_exams/admin/contributor_monitoring.html",
        {"courses": courses},
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
            return redirect("departmental_exams:contributor_monitoring")
    return render(
        request,
        "departmental_exams/admin/roster_action_confirm.html",
        {"form": form, "cycle_course": cycle_course, "action": action},
        status=400 if request.method == "POST" and form.errors else 200,
    )
