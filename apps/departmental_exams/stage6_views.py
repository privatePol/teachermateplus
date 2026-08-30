from __future__ import annotations

import secrets
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.decorators import portal_required

from .blueprint_services import (
    BlockedContributionResolutionService,
    BlueprintMutationService,
    QuestionPlacementService,
    ScenarioMutationService,
    Stage6Conflict,
)
from .approval_services import ApprovalConflict, ExamApprovalLockService
from .automatic_generation_audit import AutomaticGenerationAuditService
from .automatic_workflow import (
    AutomaticContributionReopenService,
    AutomaticGenerationSummaryService,
)
from .answer_key_release import AnswerKeyReleaseService
from .generation_readiness import (
    Stage6ReadinessService,
    eligible_submitted_question_pool,
)
from .generation_services import (
    ExamGenerationService,
    GenerationConflict,
    GenerationLimitExceeded,
)
from .models import (
    AnswerKeyRelease,
    AutomaticGenerationAuditRun,
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExamBlueprint,
    ExamCourseEquivalencyMembership,
    ExamScenario,
    ExamScenarioMember,
    GeneratedExamSet,
    ExamGenerationRevision,
    ExaminationCycle,
    QuestionnairePrintRelease,
)
from .forms import (
    AnswerKeyReleaseForm,
    AutomaticContributionReopenForm,
    BulkQuestionnairePrintReleaseForm,
    QuestionnairePrintReleaseForm,
)
from .questionnaire_printing import QuestionnairePrintReleaseService
from .services import (
    CourseExamConfigurationConflict,
    DepartmentalExamAuthorizationService,
)
from .stage6_campus_codes import (
    Stage6CampusCodeAmbiguity,
    canonicalize_participating_campus_rows,
)
from .stage6_forms import (
    BlockedContributionResolutionForm,
    BlueprintForm,
    ExamSectionFormSet,
    QuestionPlacementForm,
    ScenarioDeleteForm,
    ScenarioForm,
    GenerationRequestForm,
    RegenerationRequestForm,
    AutomaticRegenerationRequestForm,
    ApproveAndLockForm,
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
    DepartmentalExamAuthorizationService.require_blueprint_structure_management(
        user=request.user, cycle_course=course
    )
    blueprint = getattr(course, "exam_blueprint", None)
    current_revision = ExamGenerationService.current_for_course(cycle_course=course)
    is_locked = bool(
        current_revision
        and current_revision.status == ExamGenerationRevision.Status.LOCKED
    )
    has_current_automatic_generation = bool(
        course.cycle.processing_mode
        == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        and current_revision
        and current_revision.current_marker == 1
        and current_revision.status == ExamGenerationRevision.Status.GENERATED
    )
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
            "locked_revision": current_revision if is_locked else None,
            "has_current_automatic_generation": has_current_automatic_generation,
        },
        status=status,
    )


@portal_required("ADMIN")
@require_GET
def blueprint_review_view(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    course = _course(tenant_id, cycle_course_id)
    DepartmentalExamAuthorizationService.require_generation_input_management(
        user=request.user, cycle_course=course
    )
    blueprint = getattr(course, "exam_blueprint", None)
    configuration = getattr(course, "configuration", None)
    current_revision = ExamGenerationService.current_for_course(cycle_course=course)
    is_locked = bool(
        current_revision
        and current_revision.status == ExamGenerationRevision.Status.LOCKED
    )
    can_edit = bool(
        blueprint
        and configuration
        and configuration.workflow_status == CourseExamConfiguration.WorkflowStatus.CLOSED
        and course.cycle.status == course.cycle.Status.OPEN
        and course.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
        and not is_locked
        and not (
            course.cycle.processing_mode
            == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
            and current_revision
            and current_revision.current_marker == 1
        )
    )
    questions = []
    scenarios = []
    if can_edit:
        try:
            participating_codes = canonicalize_participating_campus_rows(
                course.offering_snapshots.order_by(
                    "campus__code", "campus_id"
                ).values_list("campus_id", "campus__code")
            )
        except Stage6CampusCodeAmbiguity as exc:
            return _error(request, status=409, message=str(exc))
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
            "locked_revision": current_revision if is_locked else None,
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
    monitoring_url = reverse("departmental_exams:contributor_monitoring")
    filter_query = urlencode(
        {
            key: request.POST.get(key)
            for key in ("cycle", "period", "course", "contributor")
            if request.POST.get(key)
        }
    )
    if filter_query:
        monitoring_url = f"{monitoring_url}?{filter_query}"
    return redirect(monitoring_url)


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
    DepartmentalExamAuthorizationService.require_generation_input_management(
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


def _generation_form_initial(*, problem, current):
    return {
        "expected_current_revision": current.revision_number if current else 0,
        "input_fingerprint": problem.input_fingerprint if problem else "",
        "request_token": secrets.token_urlsafe(32),
    }


@portal_required("ADMIN")
@require_GET
def generation_workspace_view(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    course = _course(tenant_id, cycle_course_id)
    automatic_mode = (
        course.cycle.processing_mode
        == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
    )
    if automatic_mode:
        DepartmentalExamAuthorizationService.require_generation_management(
            user=request.user,
            cycle_course=course,
        )
    else:
        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=request.user,
            cycle_course=course,
        )
    problem, readiness = Stage6ReadinessService.build_problem(cycle_course=course)
    current = ExamGenerationService.current_for_course(cycle_course=course)
    is_locked = bool(
        current and current.status == ExamGenerationRevision.Status.LOCKED
    )
    initial = _generation_form_initial(problem=problem, current=current)
    return render(
        request,
        "departmental_exams/admin/generation_workspace.html",
        {
            "cycle_course": course,
            "readiness": readiness,
            "problem": problem,
            "current_revision": current,
            "generation_form": (
                None if automatic_mode else GenerationRequestForm(initial=initial)
            ),
            "regeneration_form": (
                AutomaticRegenerationRequestForm(initial=initial)
                if automatic_mode
                else RegenerationRequestForm(initial=initial)
            ),
            "is_locked": is_locked,
            "automatic_mode": automatic_mode,
        },
    )


def _generation_post(request, *, cycle_course_id, regeneration):
    tenant_id = _tenant_id(request)
    course = _course(tenant_id, cycle_course_id)
    automatic_mode = (
        course.cycle.processing_mode
        == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
    )
    if automatic_mode:
        DepartmentalExamAuthorizationService.require_generation_management(
            user=request.user,
            cycle_course=course,
        )
        if not regeneration:
            raise PermissionDenied(
                "Automatic-mode first generation is performed by deadline processing."
            )
    else:
        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=request.user,
            cycle_course=course,
        )
    form_class = (
        AutomaticRegenerationRequestForm
        if automatic_mode and regeneration
        else RegenerationRequestForm
        if regeneration
        else GenerationRequestForm
    )
    form = form_class(request.POST)
    if not form.is_valid():
        return _error(
            request,
            status=400,
            message="The generation request is malformed. Refresh the workspace and try again.",
        )
    try:
        outcome = ExamGenerationService.generate(
            cycle_course_id=course.id,
            tenant_id=tenant_id,
            actor=request.user,
            expected_current_revision=form.cleaned_data["expected_current_revision"],
            expected_input_fingerprint=form.cleaned_data["input_fingerprint"],
            request_token=form.cleaned_data["request_token"],
            regeneration=regeneration,
            regeneration_reason=form.cleaned_data.get("reason", ""),
            request=request,
        )
    except (GenerationConflict, GenerationLimitExceeded) as exc:
        return _error(request, status=409, message=" ".join(exc.messages))
    except ValidationError as exc:
        return _error(request, status=400, message=" ".join(exc.messages))
    messages.success(
        request,
        (
            "The existing generation revision was reused for this request."
            if outcome.reused
            else "Set A and Set B were generated atomically."
        ),
    )
    return redirect(
        "departmental_exams:generated_revision_detail",
        revision_id=outcome.revision.id,
    )


@portal_required("ADMIN")
@require_POST
def generate_exam_view(request, cycle_course_id):
    return _generation_post(
        request,
        cycle_course_id=cycle_course_id,
        regeneration=False,
    )


@portal_required("ADMIN")
@require_POST
def regenerate_exam_view(request, cycle_course_id):
    return _generation_post(
        request,
        cycle_course_id=cycle_course_id,
        regeneration=True,
    )


@portal_required("ADMIN")
@require_GET
def generated_revision_detail_view(request, revision_id):
    tenant_id = _tenant_id(request)
    revision = ExamGenerationService.revision_for_tenant(
        revision_id=revision_id,
        tenant_id=tenant_id,
    )
    automatic_mode = (
        revision.cycle_course.cycle.processing_mode
        == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
    )
    can_manage_generation = False
    if automatic_mode:
        can_manage_generation = (
            DepartmentalExamAuthorizationService.has_automatic_course_permission(
                user=request.user,
                cycle_course=revision.cycle_course,
                permissions=(
                    DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
                ),
            )
        )
        if not can_manage_generation:
            DepartmentalExamAuthorizationService.require_automatic_course_permission(
                user=request.user,
                cycle_course=revision.cycle_course,
                permissions=(
                    DepartmentalExamAuthorizationService.VIEW_GENERATED_PERMISSION,
                ),
            )
            if not (
                revision.current_marker == 1
                and revision.status == ExamGenerationRevision.Status.GENERATED
            ):
                raise PermissionDenied(
                    "Historical automatic generation revisions require management authority."
                )
    else:
        DepartmentalExamAuthorizationService.require_generated_exam_view(
            user=request.user,
            cycle_course=revision.cycle_course,
        )
    generated_sets = list(
        GeneratedExamSet.objects.filter(generation_revision=revision)
        .prefetch_related("items")
        .order_by("set_code")
    )
    for generated_set in generated_sets:
        generated_set.ordered_items = sorted(
            generated_set.items.all(),
            key=lambda item: item.position,
        )
    history = (
        list(
            ExamGenerationRevision.objects.filter(cycle_course=revision.cycle_course)
            .select_related("generated_by", "locked_by", "supersedes")
            .order_by("-revision_number")
        )
        if not automatic_mode or can_manage_generation
        else []
    )
    is_current_generated = bool(
        not automatic_mode
        and
        revision.current_marker == 1
        and revision.status == ExamGenerationRevision.Status.GENERATED
        and revision.cycle_course.cycle.status == revision.cycle_course.cycle.Status.OPEN
    )
    return render(
        request,
        "departmental_exams/admin/generated_revision_detail.html",
        {
            "revision": revision,
            "cycle_course": revision.cycle_course,
            "generated_sets": generated_sets,
            "revision_history": history,
            "approve_form": (
                ApproveAndLockForm(
                    initial={
                        "expected_revision_number": revision.revision_number,
                        "expected_source_input_fingerprint": revision.source_input_fingerprint,
                    }
                )
                if is_current_generated
                else None
            ),
            "automatic_mode": automatic_mode,
            "can_manage_generation": can_manage_generation,
            "can_regenerate": bool(
                revision.current_marker == 1
                and revision.status == ExamGenerationRevision.Status.GENERATED
                and (not automatic_mode or can_manage_generation)
            ),
        },
    )


@portal_required("ADMIN")
@require_POST
def approve_and_lock_view(request, revision_id):
    revision = ExamGenerationService.revision_for_tenant(
        revision_id=revision_id,
        tenant_id=_tenant_id(request),
    )
    if (
        revision.cycle_course.cycle.processing_mode
        == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
    ):
        raise PermissionDenied("Automatic-mode generations do not use Approve & Lock.")
    form = ApproveAndLockForm(request.POST)
    if not form.is_valid():
        return _error(
            request,
            status=400,
            message="Every approval acknowledgement is required.",
        )
    try:
        outcome = ExamApprovalLockService.approve_and_lock(
            revision_id=revision_id,
            tenant_id=_tenant_id(request),
            actor=request.user,
            expected_revision_number=form.cleaned_data["expected_revision_number"],
            expected_source_input_fingerprint=form.cleaned_data[
                "expected_source_input_fingerprint"
            ],
            request=request,
        )
    except (Http404, PermissionDenied):
        raise
    except ApprovalConflict as exc:
        return _error(request, status=409, message=" ".join(exc.messages))
    except ValidationError as exc:
        return _error(request, status=400, message=" ".join(exc.messages))
    messages.success(
        request,
        (
            "The already locked final examination was reused."
            if outcome.reused
            else "The final examination was approved and permanently locked."
        ),
    )
    return redirect(
        "departmental_exams:generated_revision_detail",
        revision_id=outcome.revision.id,
    )


@portal_required("ADMIN")
@require_GET
def automatic_generation_summary_view(request, cycle_id):
    tenant_id = _tenant_id(request)
    cycle = get_object_or_404(
        ExaminationCycle.objects.filter(tenant_id=tenant_id),
        pk=cycle_id,
        processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
    )
    courses = list(
        CycleCourse.objects.filter(
            cycle=cycle,
        ).select_related("cycle").prefetch_related("offering_snapshots")
    )
    if not courses:
        raise PermissionDenied("No applicable automatic course examinations exist.")
    included_courses = [
        course
        for course in courses
        if course.inclusion_status == CycleCourse.InclusionStatus.INCLUDED
    ]
    exempt_courses = [
        course
        for course in courses
        if course.inclusion_status == CycleCourse.InclusionStatus.EXEMPT
    ]
    permission_map = DepartmentalExamAuthorizationService.automatic_permission_map(
        user=request.user,
        courses=included_courses,
        permissions=(
            DepartmentalExamAuthorizationService.VIEW_GENERATED_PERMISSION,
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
        ),
    )
    if included_courses and any(
        DepartmentalExamAuthorizationService.ANY_AUTOMATIC_PERMISSION
        not in permission_map[course.id]
        for course in included_courses
    ):
        raise PermissionDenied(
            "You do not have automatic examination authority for every participating campus."
        )
    inclusion_map = (
        DepartmentalExamAuthorizationService.automatic_inclusion_management_map(
            user=request.user,
            courses=exempt_courses,
        )
    )
    can_view_exempt = bool(exempt_courses) and all(
        DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
        in inclusion_map[course.id]
        for course in exempt_courses
    )
    if not included_courses and not can_view_exempt:
        raise PermissionDenied(
            "You do not have automatic examination authority for every participating campus."
        )
    visible_course_ids = [course.id for course in included_courses]
    if can_view_exempt:
        visible_course_ids.extend(course.id for course in exempt_courses)
    summary = AutomaticGenerationSummaryService.build(
        cycle=cycle,
        cycle_course_ids=visible_course_ids,
    )
    for item in summary["generated"]:
        item["can_manage_generation"] = (
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
            in permission_map[item["course"].id]
        )
        if (
            item["can_manage_generation"]
            and item.get("regeneration_input_fingerprint")
        ):
            item["regeneration_form"] = AutomaticRegenerationRequestForm(
                initial={
                    "expected_current_revision": item["revision"].revision_number,
                    "input_fingerprint": item["regeneration_input_fingerprint"],
                    "request_token": secrets.token_urlsafe(32),
                }
            )
    for item in summary["not_generated"]:
        item["can_manage_generation"] = (
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
            in permission_map[item["course"].id]
        )
    return render(
        request,
        "departmental_exams/admin/automatic_generation_summary.html",
        {"cycle": cycle, **summary},
    )


def _automatic_summary_permission_map(*, user, courses):
    return DepartmentalExamAuthorizationService.automatic_permission_map(
        user=user,
        courses=courses,
        permissions=(
            DepartmentalExamAuthorizationService.VIEW_GENERATED_PERMISSION,
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
        ),
    )


def _has_automatic_summary_access(courses, permission_map):
    return bool(courses) and all(
        DepartmentalExamAuthorizationService.ANY_AUTOMATIC_PERMISSION
        in permission_map[course.id]
        for course in courses
    )


@portal_required("ADMIN")
@require_GET
def automatic_generation_summary_entry_view(request):
    tenant_id = _tenant_id(request)
    DepartmentalExamAuthorizationService.require_enabled(tenant_id=tenant_id)
    courses = list(
        CycleCourse.objects.filter(
            cycle__tenant_id=tenant_id,
            cycle__processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
            inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
        )
        .select_related("cycle", "cycle__academic_year", "cycle__term")
        .prefetch_related("offering_snapshots")
        .order_by("-cycle__created_at", "-cycle_id", "course__code", "id")
    )
    permission_map = _automatic_summary_permission_map(
        user=request.user,
        courses=courses,
    )
    courses_by_cycle = {}
    for course in courses:
        courses_by_cycle.setdefault(course.cycle_id, []).append(course)
    cycles = tuple(
        cycle_courses[0].cycle
        for cycle_courses in courses_by_cycle.values()
        if _has_automatic_summary_access(cycle_courses, permission_map)
    )
    if len(cycles) == 1:
        return redirect(
            "departmental_exams:automatic_generation_summary",
            cycle_id=cycles[0].id,
        )
    return render(
        request,
        "departmental_exams/admin/automatic_generation_summary_selector.html",
        {"cycles": cycles},
    )


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def questionnaire_print_release_view(request):
    tenant_id = _tenant_id(request)
    courses = list(
        CycleCourse.objects.filter(
            cycle__tenant_id=tenant_id,
            inclusion_status=CycleCourse.InclusionStatus.INCLUDED,
            generation_revisions__isnull=False,
        )
        .select_related(
            "cycle",
            "cycle__academic_year",
            "cycle__term",
            "course",
            "responsible_department",
            "reviewer",
        )
        .prefetch_related(
            Prefetch(
                "offering_snapshots",
                queryset=CycleCourseOffering.objects.select_related("campus").order_by(
                    "id"
                ),
            ),
            Prefetch(
                "generation_revisions",
                queryset=ExamGenerationRevision.objects.prefetch_related(
                    Prefetch(
                        "automatic_audit_runs",
                        queryset=AutomaticGenerationAuditRun.objects.select_related(
                            "run_by"
                        ).order_by("-run_at", "-id"),
                    )
                ).order_by("-revision_number"),
            ),
            Prefetch(
                "questionnaire_print_releases",
                queryset=QuestionnairePrintRelease.objects.select_related(
                    "generation_revision",
                    "released_by",
                    "revoked_by",
                ).order_by("-released_at", "-id"),
            ),
            Prefetch(
                "answer_key_releases",
                queryset=AnswerKeyRelease.objects.select_related(
                    "generation_revision",
                    "released_by",
                    "revoked_by",
                ).order_by("-released_at", "-id"),
            ),
        )
        .distinct()
        .order_by(
            "-cycle__academic_year__start_date",
            "cycle__term__name",
            "course__code",
        )
    )
    management_map = DepartmentalExamAuthorizationService.automatic_permission_map(
        user=request.user,
        courses=courses,
        permissions=(
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
        ),
    )
    print_map = DepartmentalExamAuthorizationService.automatic_permission_map(
        user=request.user,
        courses=courses,
        permissions=(
            DepartmentalExamAuthorizationService.PRINT_GENERATED_PERMISSION,
        ),
    )
    audit_map = DepartmentalExamAuthorizationService.automatic_permission_map(
        user=request.user,
        courses=courses,
        permissions=(
            DepartmentalExamAuthorizationService.AUDIT_GENERATED_PERMISSION,
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION,
        ),
    )
    answer_key_release_map = {
        course.id: DepartmentalExamAuthorizationService.can_release_answer_keys(
            user=request.user,
            cycle_course=course,
        )
        for course in courses
    }
    courses = [
        course
        for course in courses
        if (
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
            in management_map[course.id]
            or DepartmentalExamAuthorizationService.PRINT_GENERATED_PERMISSION
            in print_map[course.id]
            or DepartmentalExamAuthorizationService.ANY_AUTOMATIC_PERMISSION
            in audit_map[course.id]
            or answer_key_release_map[course.id]
        )
    ]
    if not courses:
        raise PermissionDenied(
            "No generated course examination is available within your output authority."
        )
    for course in courses:
        seen_campus_ids = set()
        print_release_campuses = []
        for snapshot in course.offering_snapshots.all():
            if snapshot.campus_id in seen_campus_ids:
                continue
            seen_campus_ids.add(snapshot.campus_id)
            print_release_campuses.append(snapshot.campus)
        course.print_release_campuses = tuple(print_release_campuses)

    course_by_id = {course.id: course for course in courses}
    revision_by_id = {
        revision.id: revision
        for course in courses
        for revision in course.generation_revisions.all()
    }
    equivalency_primary_by_course_id = dict(
        ExamCourseEquivalencyMembership.objects.filter(
            cycle_course_id__in=course_by_id,
            active_marker=1,
            group__is_active=True,
        ).values_list("cycle_course_id", "group__primary_cycle_course_id")
    )
    can_bulk_release = any(
        DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
        in management_map[course.id]
        for course in courses
    )
    bulk_selection_rows = []
    for course in courses:
        if equivalency_primary_by_course_id.get(course.id, course.id) != course.id:
            continue
        if (
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
            not in management_map[course.id]
        ):
            continue
        for revision in course.generation_revisions.all():
            if not (
                revision.current_marker == 1
                and revision.status == ExamGenerationRevision.Status.GENERATED
            ):
                continue
            value = f"{course.id}:{revision.id}"
            bulk_selection_rows.append(
                {
                    "value": value,
                    "course": course,
                    "revision": revision,
                }
            )
    bulk_selection_choices = tuple(
        (
            row["value"],
            f"{row['course'].course.code} R{row['revision'].revision_number}",
        )
        for row in bulk_selection_rows
    )
    bound_course_id = None
    bound_form = None
    bound_answer_key_course_id = None
    bound_answer_key_form = None
    status = 200
    now = timezone.now()
    local_now = timezone.localtime(now).replace(second=0, microsecond=0)
    bulk_form = BulkQuestionnairePrintReleaseForm(
        selection_choices=bulk_selection_choices,
        initial={
            "print_from": local_now,
            "print_until": local_now + timezone.timedelta(days=1),
        },
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "bulk_release":
            bulk_form = BulkQuestionnairePrintReleaseForm(
                request.POST,
                selection_choices=bulk_selection_choices,
            )
            if bulk_form.is_valid():
                try:
                    releases = QuestionnairePrintReleaseService.bulk_release(
                        selections=bulk_form.cleaned_data["selections"],
                        tenant_id=tenant_id,
                        actor=request.user,
                        print_from=bulk_form.cleaned_data["print_from"],
                        print_until=bulk_form.cleaned_data["print_until"],
                        request=request,
                    )
                except ValidationError as exc:
                    if hasattr(exc, "message_dict"):
                        for field, errors in exc.message_dict.items():
                            target = field if field in bulk_form.fields else None
                            for error in errors:
                                bulk_form.add_error(target, error)
                    else:
                        bulk_form.add_error(None, exc)
                    status = 400
                else:
                    messages.success(
                        request,
                        f"Released {len(releases)} exact questionnaire revisions with the common faculty print window.",
                    )
                    return redirect("departmental_exams:questionnaire_print_release")
            else:
                status = 400
        elif action == "revoke":
            try:
                QuestionnairePrintReleaseService.revoke(
                    release_id=int(request.POST.get("release_id") or 0),
                    tenant_id=tenant_id,
                    actor=request.user,
                    request=request,
                )
            except (ValueError, ValidationError) as exc:
                messages.error(request, " ".join(getattr(exc, "messages", (str(exc),))))
                status = 400
            else:
                messages.success(request, "Questionnaire print release revoked.")
                return redirect("departmental_exams:questionnaire_print_release")
        elif action == "run_audit":
            try:
                revision_id = int(request.POST.get("revision_id") or 0)
            except (TypeError, ValueError):
                revision_id = 0
            if revision_id not in revision_by_id:
                raise PermissionDenied(
                    "The selected revision is outside your automatic-audit authority."
                )
            audit_run = AutomaticGenerationAuditService.run(
                revision_id=revision_id,
                tenant_id=tenant_id,
                actor=request.user,
                request=request,
            )
            messages.success(
                request,
                f"Automatic audit completed with status {audit_run.status}.",
            )
            return redirect(
                "departmental_exams:automatic_generation_audit_result",
                revision_id=revision_id,
                audit_run_id=audit_run.id,
            )
        elif action == "release":
            try:
                bound_course_id = int(request.POST.get("cycle_course_id") or 0)
            except (TypeError, ValueError):
                bound_course_id = 0
            course = course_by_id.get(bound_course_id)
            if course is None:
                raise PermissionDenied(
                    "The selected course examination is outside your management authority."
                )
            bound_form = QuestionnairePrintReleaseForm(
                request.POST,
                cycle_course=course,
                auto_id=f"id_course_{course.id}_%s",
            )
            if bound_form.is_valid():
                try:
                    QuestionnairePrintReleaseService.release(
                        cycle_course_id=course.id,
                        revision_id=bound_form.cleaned_data["generation_revision"].id,
                        tenant_id=tenant_id,
                        actor=request.user,
                        print_from=bound_form.cleaned_data["print_from"],
                        print_until=bound_form.cleaned_data["print_until"],
                        request=request,
                    )
                except ValidationError as exc:
                    if hasattr(exc, "message_dict"):
                        for field, errors in exc.message_dict.items():
                            target = field if field in bound_form.fields else None
                            for error in errors:
                                bound_form.add_error(target, error)
                    else:
                        bound_form.add_error(None, exc)
                    status = 400
                else:
                    messages.success(
                        request,
                        "Exact questionnaire revision released for faculty printing.",
                    )
                    return redirect("departmental_exams:questionnaire_print_release")
            else:
                status = 400
        elif action == "answer_key_release":
            try:
                bound_answer_key_course_id = int(
                    request.POST.get("cycle_course_id") or 0
                )
            except (TypeError, ValueError):
                bound_answer_key_course_id = 0
            course = course_by_id.get(bound_answer_key_course_id)
            if course is None or not answer_key_release_map.get(course.id):
                raise PermissionDenied(
                    "The selected course examination is outside your Answer Key release authority."
                )
            bound_answer_key_form = AnswerKeyReleaseForm(
                request.POST,
                cycle_course=course,
                auto_id=f"id_answer_key_course_{course.id}_%s",
            )
            if bound_answer_key_form.is_valid():
                try:
                    AnswerKeyReleaseService.release(
                        cycle_course_id=course.id,
                        revision_id=bound_answer_key_form.cleaned_data[
                            "generation_revision"
                        ].id,
                        tenant_id=tenant_id,
                        actor=request.user,
                        available_from=bound_answer_key_form.cleaned_data[
                            "available_from"
                        ],
                        available_until=bound_answer_key_form.cleaned_data[
                            "available_until"
                        ],
                        attestation_confirmed=bound_answer_key_form.cleaned_data[
                            "sessions_concluded"
                        ],
                        request=request,
                    )
                except ValidationError as exc:
                    if hasattr(exc, "message_dict"):
                        for field, errors in exc.message_dict.items():
                            target = (
                                field
                                if field in bound_answer_key_form.fields
                                else None
                            )
                            for error in errors:
                                bound_answer_key_form.add_error(target, error)
                    else:
                        bound_answer_key_form.add_error(None, exc)
                    status = 400
                else:
                    messages.success(
                        request,
                        "Exact Answer Key revision released to currently assigned faculty.",
                    )
                    return redirect("departmental_exams:questionnaire_print_release")
            else:
                status = 400
        elif action == "answer_key_revoke":
            try:
                AnswerKeyReleaseService.revoke(
                    release_id=int(request.POST.get("release_id") or 0),
                    tenant_id=tenant_id,
                    actor=request.user,
                    request=request,
                )
            except (ValueError, ValidationError) as exc:
                messages.error(
                    request,
                    " ".join(getattr(exc, "messages", (str(exc),))),
                )
                status = 400
            else:
                messages.success(request, "Faculty Answer Key release revoked.")
                return redirect("departmental_exams:questionnaire_print_release")
        else:
            raise Http404("Unknown questionnaire print release action.")

    for course in courses:
        course.can_manage_release = (
            DepartmentalExamAuthorizationService.MANAGE_GENERATION_PERMISSION
            in management_map[course.id]
        )
        course.can_manage_answer_key_release = answer_key_release_map[course.id]
        course.can_print_questionnaire = bool(
            course.can_manage_release
            or DepartmentalExamAuthorizationService.PRINT_GENERATED_PERMISSION
            in print_map[course.id]
        )
        course.can_run_automatic_audit = bool(
            DepartmentalExamAuthorizationService.ANY_AUTOMATIC_PERMISSION
            in audit_map[course.id]
        )
        course.available_revisions = list(course.generation_revisions.all())
        for revision in course.available_revisions:
            revision.can_admin_print = bool(
                course.can_manage_release
                or (
                    DepartmentalExamAuthorizationService.PRINT_GENERATED_PERMISSION
                    in print_map[course.id]
                    and revision.current_marker == 1
                    and revision.status == ExamGenerationRevision.Status.GENERATED
                )
            )
            revision.can_run_automatic_audit = course.can_run_automatic_audit
            revision.audit_history = list(revision.automatic_audit_runs.all())
            revision.latest_automatic_audit = (
                revision.audit_history[0] if revision.audit_history else None
            )
        course.release_history = list(course.questionnaire_print_releases.all())
        course.active_print_release = next(
            (
                release
                for release in course.release_history
                if release.status == QuestionnairePrintRelease.Status.ACTIVE
                and release.active_marker == 1
            ),
            None,
        )
        active = course.active_print_release
        if active is None:
            course.print_window_status = "Not released"
        elif now < active.print_from:
            course.print_window_status = "Scheduled"
        elif now > active.print_until:
            course.print_window_status = "Window ended"
        else:
            course.print_window_status = "Printable now"
        course.newer_revision_exists = bool(
            active
            and any(
                revision.revision_number
                > active.generation_revision.revision_number
                for revision in course.available_revisions
            )
        )
        course.answer_key_release_history = list(course.answer_key_releases.all())
        course.active_answer_key_release = next(
            (
                release
                for release in course.answer_key_release_history
                if release.status == AnswerKeyRelease.Status.ACTIVE
                and release.active_marker == 1
            ),
            None,
        )
        active_answer_key = course.active_answer_key_release
        course.answer_key_revision_superseded = bool(
            active_answer_key
            and not (
                active_answer_key.generation_revision.current_marker == 1
                and (
                    (
                        course.cycle.processing_mode
                        == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
                        and active_answer_key.generation_revision.status
                        == ExamGenerationRevision.Status.GENERATED
                    )
                    or (
                        course.cycle.processing_mode
                        == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
                        and active_answer_key.generation_revision.status
                        == ExamGenerationRevision.Status.LOCKED
                    )
                )
            )
        )
        if active_answer_key is None:
            course.answer_key_window_status = "Not released"
        elif course.answer_key_revision_superseded:
            course.answer_key_window_status = "Superseded — blocked"
        elif now < active_answer_key.available_from:
            course.answer_key_window_status = "Scheduled"
        elif now > active_answer_key.available_until:
            course.answer_key_window_status = "Window ended"
        else:
            course.answer_key_window_status = "Available now"

        if not course.can_manage_answer_key_release:
            course.answer_key_release_form = None
        elif (
            bound_answer_key_form is not None
            and course.id == bound_answer_key_course_id
        ):
            course.answer_key_release_form = bound_answer_key_form
        else:
            eligible_revisions = [
                revision
                for revision in course.available_revisions
                if revision.current_marker == 1
                and (
                    (
                        course.cycle.processing_mode
                        == ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
                        and revision.status == ExamGenerationRevision.Status.GENERATED
                    )
                    or (
                        course.cycle.processing_mode
                        == ExaminationCycle.ProcessingMode.MANUAL_REVIEW
                        and revision.status == ExamGenerationRevision.Status.LOCKED
                    )
                )
            ]
            course.answer_key_release_form = AnswerKeyReleaseForm(
                cycle_course=course,
                auto_id=f"id_answer_key_course_{course.id}_%s",
                initial={
                    "cycle_course_id": course.id,
                    "generation_revision": (
                        eligible_revisions[0] if eligible_revisions else None
                    ),
                    "available_from": (
                        active_answer_key.available_from
                        if active_answer_key
                        else local_now
                    ),
                    "available_until": (
                        active_answer_key.available_until
                        if active_answer_key
                        else local_now + timezone.timedelta(days=1)
                    ),
                },
            )
        if not course.can_manage_release:
            course.release_form = None
        elif bound_form is not None and course.id == bound_course_id:
            course.release_form = bound_form
        else:
            course.release_form = QuestionnairePrintReleaseForm(
                cycle_course=course,
                auto_id=f"id_course_{course.id}_%s",
                initial={
                    "cycle_course_id": course.id,
                    "generation_revision": (
                        course.available_revisions[0]
                        if course.available_revisions
                        else None
                    ),
                    "print_from": active.print_from if active else local_now,
                    "print_until": (
                        active.print_until
                        if active
                        else local_now + timezone.timedelta(days=1)
                    ),
                },
            )
    return render(
        request,
        "departmental_exams/admin/questionnaire_print_release.html",
        {
            "courses": courses,
            "now": now,
            "bulk_form": bulk_form,
            "can_bulk_release": can_bulk_release,
            "bulk_selection_rows": bulk_selection_rows,
            "bulk_selection_row_count": len(bulk_selection_rows),
            "bulk_selected_values": set(bulk_form["selections"].value() or ()),
        },
        status=status,
    )


@portal_required("ADMIN")
@require_http_methods(["GET", "POST"])
def automatic_contribution_reopen_view(request, cycle_course_id):
    tenant_id = _tenant_id(request)
    course = _course(tenant_id, cycle_course_id)
    DepartmentalExamAuthorizationService.require_generation_management(
        user=request.user,
        cycle_course=course,
    )
    configuration = getattr(course, "configuration", None)
    if configuration is None:
        raise Http404("Course configuration does not exist.")
    form = AutomaticContributionReopenForm(
        request.POST or None,
        initial={"expected_revision": configuration.revision},
    )
    status = 200
    if request.method == "POST" and form.is_valid():
        try:
            AutomaticContributionReopenService.reopen(
                cycle_course_id=course.id,
                tenant_id=tenant_id,
                actor=request.user,
                expected_revision=form.cleaned_data["expected_revision"],
                new_deadline=form.cleaned_data["new_deadline"],
                request=request,
            )
        except CourseExamConfigurationConflict as exc:
            form.add_error(None, str(exc))
            status = 409
        except ValidationError as exc:
            form.add_error(None, exc)
            status = 400
        else:
            messages.success(
                request,
                "Contributions reopened. Submitted contributions remain immutable.",
            )
            return redirect(
                "departmental_exams:automatic_generation_summary",
                cycle_id=course.cycle_id,
            )
    return render(
        request,
        "departmental_exams/admin/automatic_contribution_reopen.html",
        {"cycle_course": course, "configuration": configuration, "form": form},
        status=status,
    )
