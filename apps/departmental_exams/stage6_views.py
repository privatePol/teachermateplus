from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.decorators import portal_required

from .blueprint_services import (
    BlockedContributionResolutionService,
    BlueprintMutationService,
    QuestionPlacementService,
    ScenarioMutationService,
    Stage6Conflict,
)
from .generation_readiness import (
    Stage6ReadinessService,
    eligible_submitted_question_pool,
)
from .models import (
    CourseExamConfiguration,
    CycleCourse,
    ExamBlueprint,
    ExamScenario,
    ExamScenarioMember,
)
from .services import DepartmentalExamAuthorizationService
from .stage6_forms import (
    BlockedContributionResolutionForm,
    BlueprintForm,
    ExamSectionFormSet,
    QuestionPlacementForm,
    ScenarioDeleteForm,
    ScenarioForm,
)


def _tenant_id(request):
    return getattr(request, "scope", {}).get("tenant_id") or getattr(
        request.user, "default_tenant_id", None
    )


def _course(tenant_id, cycle_course_id):
    return get_object_or_404(
        CycleCourse.objects.select_related(
            "cycle",
            "course",
            "responsible_department",
            "responsible_department__campus",
            "reviewer",
            "configuration",
            "exam_blueprint",
        ).prefetch_related("offering_snapshots__campus"),
        pk=cycle_course_id,
        cycle__tenant_id=tenant_id,
    )


def _error(request, *, status, message):
    return render(
        request,
        "departmental_exams/admin/stage6_error.html",
        {"error_message": message},
        status=status,
    )


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def blueprint_configuration_view(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    course = _course(tenant_id, cycle_course_id)
    DepartmentalExamAuthorizationService.require_configure_cycle_course(
        user=request.user, cycle_course=course
    )
    blueprint = getattr(course, "exam_blueprint", None)
    existing_sections = list(
        blueprint.sections.order_by("display_order", "id") if blueprint else ()
    )
    initial_sections = [
        {
            "id": section.id,
            "title": section.title,
            "instructions": section.instructions,
            "display_order": section.display_order,
            "item_quota": section.item_quota,
        }
        for section in existing_sections
    ]
    form = BlueprintForm(
        request.POST or None,
        initial={
            "expected_revision": blueprint.revision if blueprint else 0,
            "mode": blueprint.mode if blueprint else ExamBlueprint.Mode.NO_SECTIONS,
        },
    )
    section_formset = ExamSectionFormSet(
        request.POST or None,
        initial=initial_sections,
        prefix="sections",
    )
    status = 200
    if request.method == "POST" and form.is_valid() and section_formset.is_valid():
        section_data = [
            row
            for row in section_formset.cleaned_data
            if row and not row.get("DELETE")
        ]
        if form.cleaned_data["mode"] == ExamBlueprint.Mode.NO_SECTIONS:
            # Empty extra forms are ignored; explicitly supplied section rows
            # still fail in the service.
            section_data = [row for row in section_data if row.get("title")]
        try:
            _blueprint, changed = BlueprintMutationService.save_structure(
                cycle_course_id=course.id,
                tenant_id=tenant_id,
                actor=request.user,
                expected_revision=form.cleaned_data["expected_revision"],
                mode=form.cleaned_data["mode"],
                sections=section_data,
                request=request,
            )
        except Stage6Conflict as exc:
            form.add_error(None, " ".join(exc.messages))
            status = 409
        except ValidationError as exc:
            form.add_error(None, exc)
            status = 400
        else:
            messages.success(
                request,
                "Stage 6 blueprint saved." if changed else "Stage 6 blueprint is unchanged.",
            )
            return redirect(
                "departmental_exams:blueprint_configuration",
                cycle_course_id=course.id,
            )
    elif request.method == "POST":
        status = 400
    readiness = Stage6ReadinessService.evaluate(cycle_course=course)
    return render(
        request,
        "departmental_exams/admin/blueprint_configuration.html",
        {
            "cycle_course": course,
            "configuration": getattr(course, "configuration", None),
            "blueprint": blueprint,
            "form": form,
            "section_formset": section_formset,
            "readiness": readiness,
        },
        status=status,
    )


@portal_required("ADMIN")
@require_GET
def blueprint_review_view(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    course = _course(tenant_id, cycle_course_id)
    DepartmentalExamAuthorizationService.require_course_responsibility(
        user=request.user, cycle_course=course
    )
    blueprint = getattr(course, "exam_blueprint", None)
    configuration = getattr(course, "configuration", None)
    can_edit = bool(
        blueprint
        and configuration
        and configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.CLOSED
        and course.cycle.status == course.cycle.Status.OPEN
        and course.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
    )
    questions = []
    scenarios = []
    if can_edit:
        participating_codes = tuple(
            course.offering_snapshots.order_by("campus__code", "campus_id").values_list(
                "campus__code", flat=True
            )
        )
        questions, _invalid_count = eligible_submitted_question_pool(
            cycle_course=course,
            participating_codes=participating_codes,
        )
        placement_by_question = {
            placement.question_id: placement
            for placement in blueprint.question_placements.select_related("section").filter(
                question_id__in=[question.id for question in questions]
            )
        }
        for question in questions:
            placement = placement_by_question.get(question.id)
            question.stage6_placement = placement
            question.placement_form = QuestionPlacementForm(
                blueprint=blueprint,
                initial={
                    "expected_placement_revision": placement.revision if placement else 0,
                    "section": placement.section_id if placement else None,
                },
            )
        scenarios = list(
            ExamScenario.objects.filter(blueprint=blueprint)
            .select_related("section")
            .prefetch_related(
                Prefetch(
                    "members",
                    queryset=ExamScenarioMember.objects.select_related("question").order_by(
                        "position", "id"
                    ),
                )
            )
            .order_by("id")
        )
        for scenario in scenarios:
            scenario.edit_form = ScenarioForm(
                blueprint=blueprint,
                initial={
                    "scenario_id": scenario.id,
                    "expected_revision": scenario.revision,
                    "title": scenario.title,
                    "stimulus": scenario.stimulus,
                    "section": scenario.section_id,
                    "ordered_question_ids": ", ".join(
                        str(member.question_id) for member in scenario.members.all()
                    ),
                },
            )
    readiness = Stage6ReadinessService.evaluate(cycle_course=course)
    return render(
        request,
        "departmental_exams/admin/blueprint_review.html",
        {
            "cycle_course": course,
            "configuration": configuration,
            "blueprint": blueprint,
            "can_edit": can_edit,
            "questions": questions,
            "scenarios": scenarios,
            "scenario_form": ScenarioForm(
                blueprint=blueprint,
                initial={"expected_revision": 0},
            ) if blueprint else None,
            "readiness": readiness,
        },
    )


@portal_required("ADMIN")
@require_POST
def blocked_contribution_resolve_view(request, contribution_id):
    form = BlockedContributionResolutionForm(request.POST)
    if not form.is_valid():
        return _error(request, status=400, message="The resolution request is malformed.")
    try:
        resolution = BlockedContributionResolutionService.resolve(
            contribution_id=contribution_id,
            tenant_id=_tenant_id(request),
            actor=request.user,
            expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
            expected_roster_revision=form.cleaned_data["expected_roster_revision"],
            reason=form.cleaned_data["reason"],
            request=request,
        )
    except Http404:
        raise
    except PermissionDenied:
        raise
    except Stage6Conflict as exc:
        return _error(request, status=409, message=" ".join(exc.messages))
    except ValidationError as exc:
        return _error(request, status=400, message=" ".join(exc.messages))
    messages.success(request, f"Blocked Draft resolution #{resolution.id} recorded.")
    return redirect("departmental_exams:contributor_monitoring")


@portal_required("ADMIN")
@require_POST
def question_placement_view(request, question_id):
    tenant_id = _tenant_id(request)
    blueprint_id = request.POST.get("blueprint_id")
    blueprint = ExamBlueprint.objects.filter(
        pk=blueprint_id,
        cycle_course__cycle__tenant_id=tenant_id,
    ).first()
    if blueprint is None:
        raise Http404
    form = QuestionPlacementForm(request.POST, blueprint=blueprint)
    if not form.is_valid():
        return _error(request, status=400, message="The placement request is malformed.")
    try:
        _placement, changed = QuestionPlacementService.place(
            question_id=question_id,
            section_id=form.cleaned_data["section"].id,
            tenant_id=tenant_id,
            actor=request.user,
            expected_placement_revision=form.cleaned_data["expected_placement_revision"],
            request=request,
        )
    except (Http404, PermissionDenied):
        raise
    except Stage6Conflict as exc:
        return _error(request, status=409, message=" ".join(exc.messages))
    except ValidationError as exc:
        return _error(request, status=400, message=" ".join(exc.messages))
    messages.success(request, "Question classification updated." if changed else "Question classification is unchanged.")
    return redirect(
        "departmental_exams:blueprint_review",
        cycle_course_id=blueprint.cycle_course_id,
    )


@portal_required("ADMIN")
@require_POST
def scenario_save_view(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    course = _course(tenant_id, cycle_course_id)
    DepartmentalExamAuthorizationService.require_course_responsibility(
        user=request.user, cycle_course=course
    )
    blueprint = getattr(course, "exam_blueprint", None)
    if blueprint is None:
        return _error(request, status=409, message="Configure the examination blueprint first.")
    form = ScenarioForm(request.POST, blueprint=blueprint)
    if not form.is_valid():
        return _error(request, status=400, message="The scenario request is malformed.")
    try:
        ScenarioMutationService.save(
            cycle_course_id=course.id,
            tenant_id=tenant_id,
            actor=request.user,
            title=form.cleaned_data["title"],
            stimulus=form.cleaned_data["stimulus"],
            question_ids=form.cleaned_data["ordered_question_ids"],
            section_id=(form.cleaned_data["section"].id if form.cleaned_data["section"] else None),
            scenario_id=form.cleaned_data["scenario_id"],
            expected_revision=form.cleaned_data["expected_revision"],
            request=request,
        )
    except (Http404, PermissionDenied):
        raise
    except Stage6Conflict as exc:
        return _error(request, status=409, message=" ".join(exc.messages))
    except ValidationError as exc:
        return _error(request, status=400, message=" ".join(exc.messages))
    messages.success(request, "Scenario saved.")
    return redirect("departmental_exams:blueprint_review", cycle_course_id=course.id)


@portal_required("ADMIN")
@require_POST
def scenario_delete_view(request, scenario_id):
    form = ScenarioDeleteForm(request.POST)
    if not form.is_valid():
        return _error(request, status=400, message="The scenario delete request is malformed.")
    try:
        course_id = ScenarioMutationService.delete(
            scenario_id=scenario_id,
            tenant_id=_tenant_id(request),
            actor=request.user,
            expected_revision=form.cleaned_data["expected_revision"],
            request=request,
        )
    except (Http404, PermissionDenied):
        raise
    except Stage6Conflict as exc:
        return _error(request, status=409, message=" ".join(exc.messages))
    except ValidationError as exc:
        return _error(request, status=400, message=" ".join(exc.messages))
    messages.success(request, "Scenario deleted.")
    return redirect("departmental_exams:blueprint_review", cycle_course_id=course_id)
