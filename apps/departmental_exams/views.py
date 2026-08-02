from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.http import Http404
from django.core import signing
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from apps.academics.models import AcademicYear, Term
from apps.accounts.models import User
from apps.core.decorators import portal_required
from apps.core.services.audit import AuditService

from .forms import (
    CourseContributionCloseForm,
    CourseContributionOpenForm,
    CourseContributionReopenForm,
    CourseExamConfigurationForm,
    CourseExamConfigurationRevertForm,
    CourseOverrideRemovalForm,
    CycleCourseAdministrationForm,
    CycleCourseExemptionForm,
    CycleCourseRestoreForm,
    ExaminationCycleForm,
    ExaminationCycleCloseForm,
    ExaminationCycleConfigurationForm,
    ExaminationCycleOpenForm,
    CycleDefaultsConfirmationForm,
)
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExaminationCycle,
    FacultyContribution,
    Question,
)
from .services import (
    CourseExamConfigurationConflict,
    CourseExamConfigurationReadinessService,
    CourseExamConfigurationService,
    CourseExamDefaultTrackingPolicy,
    CycleCourseAdministrationService,
    CycleCourseCampusPresentationService,
    CycleCourseInclusionService,
    CycleCourseTransitionConflict,
    DepartmentalExamAuthorizationService,
    ExaminationCycleService,
    ExaminationCycleConfigurationService,
)


def _tenant_id(request):
    return getattr(request, "scope", {}).get("tenant_id") or getattr(
        request.user, "default_tenant_id", None
    )


def _with_downstream_activity_flags(queryset):
    """Annotate activity once so list readiness never adds per-row queries."""
    return queryset.annotate(
        has_contribution_activity=Exists(
            FacultyContribution.objects.filter(cycle_course_id=OuterRef("pk"))
        ),
        has_question_activity=Exists(
            Question.objects.filter(
                contribution__cycle_course_id=OuterRef("pk")
            )
        ),
    )


def _prepare_cycle_course_campus_display(cycle_course):
    """Attach template-ready campus data without querying beyond the prefetch."""
    presentation = CycleCourseCampusPresentationService.from_prefetched_snapshots(
        cycle_course
    )
    cycle_course.campus_presentation = presentation
    cycle_course.included_campuses = presentation["labels"]
    cycle_course.offering_count = presentation["offering_count"]
    return presentation


_CYCLE_DEFAULTS_CONFIRMATION_PURPOSE = "cycle-defaults-apply-v1"
_CYCLE_DEFAULTS_CONFIRMATION_SALT = "departmental-exams-cycle-defaults-v2"
_CYCLE_DEFAULTS_CONFIRMATION_MAX_AGE_SECONDS = 900


def _cycle_defaults_confirmation_token(*, request, cycle, tenant_id, cleaned_data):
    """Issue a POST-only, timestamped confirmation snapshot for one actor."""
    return signing.dumps(
        {
            "purpose": _CYCLE_DEFAULTS_CONFIRMATION_PURPOSE,
            "actor_id": request.user.id,
            "tenant_id": tenant_id,
            "cycle_id": cycle.id,
            "expected_updated_at": cleaned_data["expected_updated_at"],
            "default_questions_required_per_faculty": cleaned_data[
                "default_questions_required_per_faculty"
            ],
            "default_final_item_count": cleaned_data["default_final_item_count"],
            "default_contribution_deadline": (
                cleaned_data["default_contribution_deadline"].isoformat()
                if cleaned_data["default_contribution_deadline"] is not None
                else None
            ),
            "contributor_instructions": cleaned_data["contributor_instructions"],
            "reason": cleaned_data["reason"],
        },
        salt=_CYCLE_DEFAULTS_CONFIRMATION_SALT,
    )


def _load_cycle_defaults_confirmation(*, request, cycle, tenant_id):
    """Fail closed without disclosing signer/parser details or raw values."""
    token = request.POST.get("confirmation_state")
    if not token:
        raise Http404("This cycle-default confirmation is no longer available.")
    try:
        state = signing.loads(
            token,
            salt=_CYCLE_DEFAULTS_CONFIRMATION_SALT,
            max_age=_CYCLE_DEFAULTS_CONFIRMATION_MAX_AGE_SECONDS,
        )
    except (signing.BadSignature, TypeError, ValueError):
        raise Http404("This cycle-default confirmation is no longer available.")
    required_keys = {
        "purpose",
        "actor_id",
        "tenant_id",
        "cycle_id",
        "expected_updated_at",
        "default_questions_required_per_faculty",
        "default_final_item_count",
        "default_contribution_deadline",
        "contributor_instructions",
        "reason",
    }
    if not isinstance(state, dict) or not required_keys.issubset(state):
        raise Http404("This cycle-default confirmation is no longer available.")
    if state["purpose"] != _CYCLE_DEFAULTS_CONFIRMATION_PURPOSE:
        raise Http404("This cycle-default confirmation is no longer available.")
    if state["actor_id"] != request.user.id:
        raise PermissionDenied("This cycle-default confirmation is not valid for the current user.")
    if state["tenant_id"] != tenant_id or state["cycle_id"] != cycle.id:
        raise Http404("This cycle-default confirmation is no longer available.")
    if not isinstance(state["expected_updated_at"], str):
        raise Http404("This cycle-default confirmation is no longer available.")
    if state["default_contribution_deadline"] is not None:
        if not isinstance(state["default_contribution_deadline"], str):
            raise Http404("This cycle-default confirmation is no longer available.")
        parsed_deadline = parse_datetime(state["default_contribution_deadline"])
        if parsed_deadline is None:
            raise Http404("This cycle-default confirmation is no longer available.")
        state["default_contribution_deadline"] = parsed_deadline
    return state


def _visible_cycle_ids_for_user(*, user, tenant_id):
    base_courses = CycleCourse.objects.filter(cycle__tenant_id=tenant_id)
    configurer = DepartmentalExamAuthorizationService.configurer_visible_cycle_courses(
        user=user, tenant_id=tenant_id, queryset=base_courses
    )
    reviewer = DepartmentalExamAuthorizationService.reviewer_visible_cycle_courses(
        user=user, tenant_id=tenant_id, queryset=base_courses
    )
    return set(
        base_courses.filter(
            Q(id__in=configurer.values("id")) | Q(id__in=reviewer.values("id"))
        ).values_list("cycle_id", flat=True).distinct()
    )


@portal_required("ADMIN")
def cycle_list_view(request):
    tenant_id = _tenant_id(request)
    DepartmentalExamAuthorizationService.require_permission(
        user=request.user,
        permission="departmental_exams.manage_cycles",
        tenant_id=tenant_id,
    )
    cycles = list(
        ExaminationCycle.objects.filter(tenant_id=tenant_id)
        .select_related("academic_year", "term")
        .order_by("-created_at")
    )
    visible_cycle_ids = _visible_cycle_ids_for_user(user=request.user, tenant_id=tenant_id)
    for cycle in cycles:
        cycle.can_view_course_examinations = cycle.id in visible_cycle_ids
    return render(request, "departmental_exams/admin/cycle_list.html", {"cycles": cycles})


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def cycle_configuration_view(request, cycle_id):
    tenant_id = _tenant_id(request)
    cycle = get_object_or_404(ExaminationCycle.objects.filter(tenant_id=tenant_id), id=cycle_id)
    DepartmentalExamAuthorizationService.require_permission(user=request.user, permission="departmental_exams.manage_cycles", tenant_id=tenant_id)
    lifecycle_flags = ExaminationCycleConfigurationService.lifecycle_flags(cycle)
    can_view_course_examinations = cycle.id in _visible_cycle_ids_for_user(
        user=request.user, tenant_id=tenant_id
    )
    form = ExaminationCycleConfigurationForm(request.POST or None, instance=cycle, initial={"expected_updated_at": ExaminationCycleConfigurationService.transition_token(cycle)})
    status = 200
    if request.method == "POST" and not lifecycle_flags["can_edit_cycle_configuration"]:
        raise Http404("Cycle configuration is read-only in its current lifecycle state.")
    if request.method == "POST" and form.is_valid():
        confirmation_state = _cycle_defaults_confirmation_token(
            request=request,
            cycle=cycle,
            tenant_id=tenant_id,
            cleaned_data=form.cleaned_data,
        )
        confirmation_form = CycleDefaultsConfirmationForm(
            initial={"confirmation_state": confirmation_state}
        )
        return render(
            request,
            "departmental_exams/admin/cycle_defaults_confirm.html",
            {"cycle": cycle, "form": confirmation_form},
        )
    return render(request, "departmental_exams/admin/cycle_configuration.html", {"cycle": cycle, "form": form, "lifecycle_flags": lifecycle_flags, "can_view_course_examinations": can_view_course_examinations}, status=status)


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def cycle_apply_defaults_view(request, cycle_id):
    tenant_id = _tenant_id(request)
    cycle = get_object_or_404(ExaminationCycle.objects.filter(tenant_id=tenant_id), id=cycle_id)
    DepartmentalExamAuthorizationService.require_permission(user=request.user, permission="departmental_exams.manage_cycles", tenant_id=tenant_id)
    if cycle.status == ExaminationCycle.Status.CLOSED:
        raise Http404("Closed cycles cannot apply defaults.")
    if request.method != "POST":
        raise Http404("This cycle-default confirmation is no longer available.")
    form = CycleDefaultsConfirmationForm(request.POST)
    status = 200
    confirmation_state = None
    if not form.is_valid():
        status = 404
    else:
        try:
            confirmation_state = _load_cycle_defaults_confirmation(
                request=request,
                cycle=cycle,
                tenant_id=tenant_id,
            )
        except Http404:
            form.add_error(
                "confirmation_state",
                "This cycle-default confirmation is missing, invalid, or no longer available.",
            )
            status = 404
    if confirmation_state is not None:
        try:
            cycle, changed = ExaminationCycleConfigurationService.save_cycle_configuration(
                cycle_id=cycle.id,
                tenant_id=tenant_id,
                user=request.user,
                expected_updated_at=confirmation_state["expected_updated_at"],
                default_questions_required_per_faculty=confirmation_state[
                    "default_questions_required_per_faculty"
                ],
                default_final_item_count=confirmation_state["default_final_item_count"],
                default_contribution_deadline=confirmation_state[
                    "default_contribution_deadline"
                ],
                contributor_instructions=confirmation_state["contributor_instructions"],
                reason=confirmation_state["reason"],
                request=request,
            )
        except CourseExamConfigurationConflict as exc:
            form.add_error(None, str(exc)); status = 409
        except ValidationError as exc:
            form.add_error(None, exc); status = 400
        else:
            messages.success(request, "Cycle defaults applied." if changed else "Cycle defaults are unchanged.")
            return redirect("departmental_exams:cycle_configuration", cycle_id=cycle.id)
    return render(request, "departmental_exams/admin/cycle_defaults_confirm.html", {"cycle": cycle, "form": form}, status=status)


def _cycle_transition_view(request, cycle_id, *, action):
    tenant_id = _tenant_id(request)
    cycle = get_object_or_404(ExaminationCycle.objects.filter(tenant_id=tenant_id), id=cycle_id)
    DepartmentalExamAuthorizationService.require_permission(user=request.user, permission="departmental_exams.manage_cycles", tenant_id=tenant_id)
    lifecycle_flags = ExaminationCycleConfigurationService.lifecycle_flags(cycle)
    if not lifecycle_flags[f"can_{action}_cycle"]:
        raise Http404("This cycle transition is not available in its current lifecycle state.")
    form_class = ExaminationCycleOpenForm if action == "open" else ExaminationCycleCloseForm
    form_data = request.POST if request.method == "POST" else None
    form = form_class(form_data, initial={"expected_updated_at": ExaminationCycleConfigurationService.transition_token(cycle)})
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            method = ExaminationCycleConfigurationService.open_cycle if action == "open" else ExaminationCycleConfigurationService.close_cycle
            cycle, changed = method(cycle_id=cycle.id, tenant_id=tenant_id, user=request.user, expected_updated_at=form.cleaned_data["expected_updated_at"], request=request)
        except CourseExamConfigurationConflict as exc:
            form.add_error(None, str(exc)); status = 409
        except ValidationError as exc:
            form.add_error(None, exc); status = 400
        else:
            messages.success(request, "Cycle opened." if action == "open" and changed else "Cycle closed." if changed else "Cycle is already in that state.")
            return redirect("departmental_exams:cycle_configuration", cycle_id=cycle.id)
    return render(request, "departmental_exams/admin/cycle_transition_confirm.html", {"cycle": cycle, "form": form, "action": action, "lifecycle_flags": lifecycle_flags}, status=status)


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def cycle_open_view(request, cycle_id):
    return _cycle_transition_view(request, cycle_id, action="open")


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def cycle_close_view(request, cycle_id):
    return _cycle_transition_view(request, cycle_id, action="close")


@portal_required("ADMIN")
def cycle_create_view(request):
    tenant_id = _tenant_id(request)
    DepartmentalExamAuthorizationService.require_permission(
        user=request.user,
        permission="departmental_exams.manage_cycles",
        tenant_id=tenant_id,
    )
    form = ExaminationCycleForm(request.POST or None)
    form.fields["academic_year"].queryset = AcademicYear.objects.filter(
        tenant_id=tenant_id, is_active=True
    )
    form.fields["term"].queryset = Term.objects.filter(tenant_id=tenant_id, is_active=True)
    if request.method == "POST" and form.is_valid():
        ExaminationCycleService.create_cycle(
            user=request.user,
            tenant=form.cleaned_data["academic_year"].tenant,
            **form.cleaned_data,
            request=request,
        )
        messages.success(
            request,
            "Examination cycle created with active offerings grouped by course.",
        )
        return redirect("departmental_exams:cycle_list")
    return render(request, "departmental_exams/admin/cycle_form.html", {"form": form})


@portal_required("ADMIN")
def cycle_course_list_view(request, cycle_id):
    tenant_id = _tenant_id(request)
    DepartmentalExamAuthorizationService.require_enabled(tenant_id=tenant_id)
    cycle = get_object_or_404(
        ExaminationCycle.objects.filter(tenant_id=tenant_id), id=cycle_id
    )
    base_courses = CycleCourse.objects.filter(cycle=cycle).select_related(
        "cycle", "course", "responsible_department", "reviewer", "configuration"
    ).prefetch_related("offering_snapshots__campus")
    configurer_courses = (
        DepartmentalExamAuthorizationService.configurer_visible_cycle_courses(
            user=request.user,
            cycle=cycle,
            queryset=base_courses,
        )
    )
    reviewer_courses = (
        DepartmentalExamAuthorizationService.reviewer_visible_cycle_courses(
            user=request.user,
            cycle=cycle,
            queryset=base_courses,
        )
    )
    configurer_ids = set(configurer_courses.values_list("id", flat=True))
    courses = _with_downstream_activity_flags(
        base_courses.filter(
            Q(id__in=configurer_courses.values("id"))
            | Q(id__in=reviewer_courses.values("id"))
        ).distinct()
    )

    courses = list(courses.order_by("course__code"))
    if not courses:
        raise PermissionDenied("You do not have current course examination access.")
    for course in courses:
        _prepare_cycle_course_campus_display(course)
        course.can_administer = course.id in configurer_ids
        course.readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=course, configuration=getattr(course, "configuration", None), user=request.user
        )
    return render(
        request,
        "departmental_exams/admin/cycle_course_list.html",
        {"cycle": cycle, "courses": courses},
    )


@portal_required("ADMIN")
def assigned_course_examinations_view(request):
    """List only the grouped course examinations currently assigned to the user."""
    tenant_id = _tenant_id(request)
    DepartmentalExamAuthorizationService.require_assigned_course_route_capability(
        user=request.user, tenant_id=tenant_id
    )
    base_courses = (
        CycleCourse.objects.filter(cycle__tenant_id=tenant_id)
        .select_related(
            "cycle__academic_year",
            "cycle__term",
            "cycle__term__academic_year",
            "course",
            "responsible_department",
            "reviewer",
            "configuration",
        )
        .prefetch_related("offering_snapshots__campus")
    )
    configurer_courses = (
        DepartmentalExamAuthorizationService.configurer_visible_cycle_courses(
            user=request.user,
            tenant_id=tenant_id,
            queryset=base_courses,
            include_null_for_superuser=False,
        )
    )
    reviewer_courses = (
        DepartmentalExamAuthorizationService.reviewer_visible_cycle_courses(
            user=request.user,
            tenant_id=tenant_id,
            queryset=base_courses,
        )
    )
    configurer_ids = set(configurer_courses.values_list("id", flat=True))
    courses = list(
        _with_downstream_activity_flags(
            base_courses.filter(
                Q(id__in=configurer_courses.values("id"))
                | Q(id__in=reviewer_courses.values("id"))
            ).distinct()
        )
        .order_by("-cycle__created_at", "course__code")
    )
    for course in courses:
        _prepare_cycle_course_campus_display(course)
        course.can_administer = course.id in configurer_ids
        course.readiness = CourseExamConfigurationReadinessService.evaluate_readiness(
            cycle_course=course, configuration=getattr(course, "configuration", None), user=request.user
        )
    return render(
        request,
        "departmental_exams/admin/assigned_course_examination_list.html",
        {"courses": courses},
    )


def _course_configuration_context(*, tenant_id, cycle_course_id, user):
    parent = get_object_or_404(
        _with_downstream_activity_flags(
            CycleCourse.objects.select_related("cycle", "course", "responsible_department", "responsible_department__campus", "reviewer").prefetch_related("offering_snapshots__campus")
        ),
        id=cycle_course_id,
        cycle__tenant_id=tenant_id,
    )
    DepartmentalExamAuthorizationService.require_configure_cycle_course(user=user, cycle_course=parent)
    _prepare_cycle_course_campus_display(parent)
    configuration = CourseExamConfiguration.objects.filter(cycle_course=parent).first()
    readiness = CourseExamConfigurationReadinessService.evaluate_readiness(cycle_course=parent, configuration=configuration, user=user)
    return parent, configuration, readiness


def _course_action_flags(*, parent, configuration, readiness):
    """Derive the mutation surface from lifecycle state, not status alone."""
    responsible_department_is_active = bool(
        parent.responsible_department_id
        and parent.responsible_department.is_active
    )
    mutable = (
        parent.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
        and responsible_department_is_active
        and parent.cycle.status != ExaminationCycle.Status.CLOSED
    )
    has_activity = bool(
        configuration
        and CourseExamDefaultTrackingPolicy.has_downstream_activity(parent)
    )
    open_blockers = set(readiness["blockers"]) - {"Closed"}
    return {
        "can_save_draft": mutable
        and parent.cycle.status in (ExaminationCycle.Status.DRAFT, ExaminationCycle.Status.OPEN)
        and (not configuration or (configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.DRAFT and not configuration.opened_at)),
        "can_open": bool(
            mutable
            and configuration
            and parent.cycle.status == ExaminationCycle.Status.OPEN
            and configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.DRAFT
            and readiness["can_open"]
        ),
        "can_close": bool(
            mutable
            and configuration
            and parent.cycle.status == ExaminationCycle.Status.OPEN
            and configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.OPEN
            and not has_activity
        ),
        "can_reopen": bool(
            mutable
            and configuration
            and parent.cycle.status == ExaminationCycle.Status.OPEN
            and configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.CLOSED
            and not has_activity
            and not open_blockers
        ),
        "can_revert": bool(
            mutable
            and configuration
            and parent.cycle.status == ExaminationCycle.Status.OPEN
            and configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.CLOSED
            and not has_activity
        ),
    }


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def course_configuration_view(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    parent, configuration, readiness = _course_configuration_context(tenant_id=tenant_id, cycle_course_id=cycle_course_id, user=request.user)
    action_flags = _course_action_flags(
        parent=parent, configuration=configuration, readiness=readiness
    )
    initial = {"expected_revision": configuration.revision if configuration else 0}
    if configuration:
        initial.update({"questions_required_per_faculty_mode": configuration.questions_required_per_faculty_source or "DEFAULT", "final_item_count_mode": configuration.final_item_count_source or "DEFAULT", "contribution_deadline_mode": configuration.contribution_deadline_source or "DEFAULT"})
    else:
        initial["contribution_deadline_mode"] = "DEFAULT"
    form = CourseExamConfigurationForm(request.POST or None, instance=configuration, initial=initial, cycle=parent.cycle)
    status = 200
    if request.method == "POST" and not action_flags["can_save_draft"]:
        raise Http404("Course configuration is read-only in its current lifecycle state.")
    if request.method == "POST" and form.is_valid():
        try:
            configuration, changed = CourseExamConfigurationService.save_course_draft(cycle_course_id=parent.id, tenant_id=tenant_id, user=request.user, expected_revision=form.cleaned_data["expected_revision"], **{key: form.cleaned_data[key] for key in ("final_item_count", "questions_required_per_faculty", "final_item_count_mode", "questions_required_per_faculty_mode", "coverage", "additional_instructions", "contribution_deadline", "contribution_deadline_mode")}, request=request)
        except CourseExamConfigurationConflict as exc:
            form.add_error(None, str(exc)); status = 409
        except ValidationError as exc:
            form.add_error(None, exc); status = 400
        else:
            messages.success(request, "Course examination configuration saved." if changed else "Course examination configuration is unchanged.")
            return redirect("departmental_exams:course_configuration", cycle_course_id=parent.id)
    return render(request, "departmental_exams/admin/course_configuration.html", {"cycle_course": parent, "configuration": configuration, "readiness": readiness, "action_flags": action_flags, "form": form}, status=status)


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def course_remove_overrides_view(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    parent, configuration, readiness = _course_configuration_context(tenant_id=tenant_id, cycle_course_id=cycle_course_id, user=request.user)
    if not configuration or configuration.opened_at or configuration.workflow_status != CourseExamConfiguration.WorkflowStatus.DRAFT:
        raise Http404("Overrides cannot be removed in this lifecycle state.")
    if request.method == "GET" and parent.cycle.status == ExaminationCycle.Status.CLOSED:
        raise Http404("Overrides cannot be removed in this lifecycle state.")
    form = CourseOverrideRemovalForm(request.POST or None, initial={"expected_revision": configuration.revision})
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            configuration, changed = CourseExamConfigurationService.remove_overrides(cycle_course_id=parent.id, tenant_id=tenant_id, user=request.user, expected_revision=form.cleaned_data["expected_revision"], return_questions_required_per_faculty=form.cleaned_data["return_questions_required_per_faculty"], return_final_item_count=form.cleaned_data["return_final_item_count"], return_contribution_deadline=form.cleaned_data["return_contribution_deadline"], request=request)
        except CourseExamConfigurationConflict as exc:
            form.add_error(None, str(exc)); status = 409
        except ValidationError as exc:
            form.add_error(None, exc); status = 400
        else:
            messages.success(request, "Selected overrides returned to the cycle defaults." if changed else "No overrides changed.")
            return redirect("departmental_exams:course_configuration", cycle_course_id=parent.id)
    return render(request, "departmental_exams/admin/course_override_remove_confirm.html", {"cycle_course": parent, "configuration": configuration, "readiness": readiness, "form": form}, status=status)


def _course_contribution_transition_view(request, cycle_course_id, *, action):
    tenant_id = _tenant_id(request)
    parent, configuration, readiness = _course_configuration_context(tenant_id=tenant_id, cycle_course_id=cycle_course_id, user=request.user)
    if not configuration:
        raise Http404("Course configuration does not exist.")
    action_flags = _course_action_flags(
        parent=parent, configuration=configuration, readiness=readiness
    )
    if not action_flags[f"can_{action}"]:
        raise Http404("This course contribution action is not available in its current lifecycle state.")
    form_class = {"open": CourseContributionOpenForm, "close": CourseContributionCloseForm, "reopen": CourseContributionReopenForm, "revert": CourseExamConfigurationRevertForm}[action]
    form = form_class(request.POST or None, initial={"expected_revision": configuration.revision})
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            if action in ("open", "reopen"):
                method = CourseExamConfigurationService.open_for_contribution if action == "open" else CourseExamConfigurationService.reopen_contribution
                configuration, changed = method(cycle_course_id=parent.id, tenant_id=tenant_id, user=request.user, expected_revision=form.cleaned_data["expected_revision"], request=request)
            elif action == "close":
                configuration, changed = CourseExamConfigurationService.close_contribution(cycle_course_id=parent.id, tenant_id=tenant_id, user=request.user, expected_revision=form.cleaned_data["expected_revision"], reason=form.cleaned_data["reason"], request=request)
            else:
                configuration, changed = CourseExamConfigurationService.revert_unpublished_configuration(cycle_course_id=parent.id, tenant_id=tenant_id, user=request.user, expected_revision=form.cleaned_data["expected_revision"], reason=form.cleaned_data["reason"], request=request)
        except CourseExamConfigurationConflict as exc:
            form.add_error(None, str(exc)); status = 409
        except ValidationError as exc:
            form.add_error(None, exc); status = 400
        else:
            messages.success(request, "Course contribution workflow updated." if changed else "Course contribution workflow is already in that state.")
            return redirect("departmental_exams:course_configuration", cycle_course_id=parent.id)
    return render(request, "departmental_exams/admin/course_contribution_confirm.html", {"cycle_course": parent, "configuration": configuration, "readiness": readiness, "form": form, "action": action}, status=status)


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def course_contribution_open_view(request, cycle_course_id):
    return _course_contribution_transition_view(request, cycle_course_id, action="open")


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def course_contribution_close_view(request, cycle_course_id):
    return _course_contribution_transition_view(request, cycle_course_id, action="close")


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def course_contribution_reopen_view(request, cycle_course_id):
    return _course_contribution_transition_view(request, cycle_course_id, action="reopen")


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def course_configuration_revert_view(request, cycle_course_id):
    return _course_contribution_transition_view(request, cycle_course_id, action="revert")


@portal_required("ADMIN")
def cycle_course_administration_view(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    course_queryset = CycleCourse.objects.select_related(
        "cycle", "course", "responsible_department", "reviewer", "configuration"
    ).prefetch_related(
        Prefetch(
            "offering_snapshots",
            queryset=CycleCourseOffering.objects.select_related("campus"),
        )
    )
    if request.method == "POST":
        with transaction.atomic():
            cycle_course = get_object_or_404(
                course_queryset,
                id=cycle_course_id,
                cycle__tenant_id=tenant_id,
            )
            DepartmentalExamAuthorizationService.require_configure_cycle_course(
                user=request.user, cycle_course=cycle_course
            )
            _prepare_cycle_course_campus_display(cycle_course)
            department_queryset = (
                DepartmentalExamAuthorizationService.configurable_departments(
                    user=request.user, tenant_id=tenant_id
                )
            )
            reviewer_queryset = DepartmentalExamAuthorizationService.eligible_reviewers(
                tenant_id=tenant_id,
                responsible_department=cycle_course.responsible_department,
            )
            form = CycleCourseAdministrationForm(
                request.POST,
                cycle_course=cycle_course,
                department_queryset=department_queryset,
                reviewer_queryset=reviewer_queryset,
            )
            if not form.is_valid():
                return render(
                    request,
                    "departmental_exams/admin/cycle_course_administration.html",
                    {"cycle_course": cycle_course, "form": form},
                )

            department = form.cleaned_data["responsible_department"]
            reviewer_id = request.POST.get("reviewer_id") or None
            if not department:
                form.add_error(
                    "responsible_department",
                    "Select an exam department before assigning or changing a reviewer.",
                )
                return render(
                    request,
                    "departmental_exams/admin/cycle_course_administration.html",
                    {"cycle_course": cycle_course, "form": form},
                )
            if not DepartmentalExamAuthorizationService.is_eligible_configurer(
                user=request.user,
                tenant_id=tenant_id,
                responsible_department=department,
            ):
                raise PermissionDenied("Exam department is outside your scope.")

            reviewer = None
            if reviewer_id:
                try:
                    reviewer_id = int(reviewer_id)
                except (TypeError, ValueError):
                    reviewer_id = None
                reviewer = (
                    User.objects.filter(id=reviewer_id).first() if reviewer_id else None
                )
                if not reviewer:
                    form.add_error(
                        "reviewer",
                        "Reviewer no longer exists.",
                    )
                    return render(
                        request,
                        "departmental_exams/admin/cycle_course_administration.html",
                        {"cycle_course": cycle_course, "form": form},
                    )

            try:
                cycle_course, _changed = CycleCourseAdministrationService.update_responsibility(
                    cycle_course_id=cycle_course.id,
                    tenant_id=tenant_id,
                    user=request.user,
                    responsible_department=department,
                    reviewer=reviewer,
                    request=request,
                )
            except ValidationError as exc:
                if hasattr(exc, "error_dict"):
                    for field, errors in exc.error_dict.items():
                        form.add_error(field if field in form.fields else None, errors)
                else:
                    form.add_error(None, exc)
                return render(
                    request,
                    "departmental_exams/admin/cycle_course_administration.html",
                    {"cycle_course": cycle_course, "form": form},
                )
        messages.success(request, "Exam department and reviewer updated.")
        return redirect(
            "departmental_exams:cycle_course_administration",
            cycle_course_id=cycle_course.id,
        )

    cycle_course = get_object_or_404(
        course_queryset,
        id=cycle_course_id,
        cycle__tenant_id=tenant_id,
    )
    DepartmentalExamAuthorizationService.require_configure_cycle_course(
        user=request.user, cycle_course=cycle_course
    )
    _prepare_cycle_course_campus_display(cycle_course)
    department_queryset = DepartmentalExamAuthorizationService.configurable_departments(
        user=request.user, tenant_id=tenant_id
    )
    reviewer_queryset = DepartmentalExamAuthorizationService.eligible_reviewers(
        tenant_id=tenant_id,
        responsible_department=cycle_course.responsible_department,
    )
    form = CycleCourseAdministrationForm(
        cycle_course=cycle_course,
        department_queryset=department_queryset,
        reviewer_queryset=reviewer_queryset,
    )
    return render(
        request,
        "departmental_exams/admin/cycle_course_administration.html",
        {"cycle_course": cycle_course, "form": form},
    )


def _transition_cycle_course(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    cycle_course = get_object_or_404(
        CycleCourse.objects.select_related(
            "cycle",
            "course",
            "responsible_department",
            "responsible_department__campus",
            "reviewer",
        ),
        id=cycle_course_id,
        cycle__tenant_id=tenant_id,
    )
    DepartmentalExamAuthorizationService.require_configure_cycle_course(
        user=request.user,
        cycle_course=cycle_course,
    )
    if (
        cycle_course.responsible_department_id
        and not cycle_course.responsible_department.is_active
    ):
        raise PermissionDenied(
            "The responsible exam department is inactive. Reactivate or reassign it before changing inclusion."
        )
    return tenant_id, cycle_course


def _transition_errors(form, exc):
    for message in getattr(exc, "messages", None) or [str(exc)]:
        form.add_error(None, message)


def _redirect_wrong_transition_source(request, *, cycle_course, expected_status):
    if request.method != "GET" or cycle_course.inclusion_status == expected_status:
        return None
    messages.info(request, "This course examination is already in its current inclusion state.")
    return redirect(
        "departmental_exams:cycle_course_administration",
        cycle_course_id=cycle_course.id,
    )


@require_http_methods(["GET", "POST"])
@portal_required("ADMIN")
def cycle_course_exempt_view(request, cycle_course_id):
    tenant_id, cycle_course = _transition_cycle_course(request, cycle_course_id)
    wrong_state_response = _redirect_wrong_transition_source(
        request,
        cycle_course=cycle_course,
        expected_status=CycleCourse.InclusionStatus.INCLUDED,
    )
    if wrong_state_response:
        return wrong_state_response
    initial = {
        "expected_updated_at": CycleCourseInclusionService.transition_token(
            cycle_course
        )
    }
    form = CycleCourseExemptionForm(request.POST or None, initial=initial)
    response_status = 200
    if request.method == "POST" and form.is_valid():
        try:
            updated, changed = CycleCourseInclusionService.exempt(
                cycle_course_id=cycle_course.id,
                tenant_id=tenant_id,
                user=request.user,
                exemption_category=form.cleaned_data["exemption_category"],
                reason=form.cleaned_data["reason"],
                expected_updated_at=form.cleaned_data["expected_updated_at"],
                request=request,
            )
        except CycleCourse.DoesNotExist as exc:
            raise Http404 from exc
        except CycleCourseTransitionConflict as exc:
            _transition_errors(form, exc)
            response_status = 409
        except ValidationError as exc:
            _transition_errors(form, exc)
            response_status = 400
        else:
            messages.success(
                request,
                "Course examination exempted."
                if changed
                else "Course examination is already exempt.",
            )
            return redirect(
                "departmental_exams:cycle_course_administration",
                cycle_course_id=updated.id,
            )
    return render(
        request,
        "departmental_exams/admin/cycle_course_transition_confirm.html",
        {
            "cycle_course": cycle_course,
            "form": form,
            "transition": "EXEMPT",
            "title": "Exempt this course examination?",
            "confirm_label": "Confirm Exemption",
            "cancel_label": "Keep Included",
        },
        status=response_status,
    )


@require_http_methods(["GET", "POST"])
@portal_required("ADMIN")
def cycle_course_restore_view(request, cycle_course_id):
    tenant_id, cycle_course = _transition_cycle_course(request, cycle_course_id)
    wrong_state_response = _redirect_wrong_transition_source(
        request,
        cycle_course=cycle_course,
        expected_status=CycleCourse.InclusionStatus.EXEMPT,
    )
    if wrong_state_response:
        return wrong_state_response
    initial = {
        "expected_updated_at": CycleCourseInclusionService.transition_token(
            cycle_course
        )
    }
    form = CycleCourseRestoreForm(request.POST or None, initial=initial)
    response_status = 200
    if request.method == "POST" and form.is_valid():
        try:
            updated, changed = CycleCourseInclusionService.restore(
                cycle_course_id=cycle_course.id,
                tenant_id=tenant_id,
                user=request.user,
                reason=form.cleaned_data["reason"],
                expected_updated_at=form.cleaned_data["expected_updated_at"],
                request=request,
            )
        except CycleCourse.DoesNotExist as exc:
            raise Http404 from exc
        except CycleCourseTransitionConflict as exc:
            _transition_errors(form, exc)
            response_status = 409
        except ValidationError as exc:
            _transition_errors(form, exc)
            response_status = 400
        else:
            messages.success(
                request,
                "Course examination restored to Included status."
                if changed
                else "Course examination is already included.",
            )
            return redirect(
                "departmental_exams:cycle_course_administration",
                cycle_course_id=updated.id,
            )
    return render(
        request,
        "departmental_exams/admin/cycle_course_transition_confirm.html",
        {
            "cycle_course": cycle_course,
            "form": form,
            "transition": "RESTORE",
            "title": "Restore this course examination?",
            "confirm_label": "Restore to Included",
            "cancel_label": "Keep Exempt",
        },
        status=response_status,
    )
