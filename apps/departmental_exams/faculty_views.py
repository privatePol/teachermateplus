from __future__ import annotations

import csv
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.views.decorators.cache import never_cache

from apps.core.decorators import portal_required
from apps.core.services.features import FeatureSettingsService
from apps.tenants.models import Tenant

from .contribution_authorization import (
    ContributionAuthorizationService,
    ContributionConflict,
    ContributionExpired,
    ContributionQuotaReached,
)
from .answer_key_release import FacultyAnswerKeyReleaseService
from .contribution_forms import (
    ContributionSubmitForm,
    FacultyCaseDeleteForm,
    FacultyCaseForm,
    FacultyCaseMemberReorderForm,
    QuestionCSVConfirmForm,
    QuestionCSVUploadForm,
    QuestionDOCXRowForm,
    QuestionDOCXUploadForm,
    QuestionDeleteForm,
    QuestionForm,
    QuestionReorderForm,
)
from .contribution_selectors import ContributionSelector
from .contribution_services import (
    ContributionDifficultyDeficient,
    ContributionDifficultyDistributionService,
    QuestionMutationService,
)
from .faculty_case_services import FacultyCaseMutationService, FacultyCasePolicy
from .csv_import import CSV_FILENAME, QuestionCSVImportService
from .docx_import import QuestionDOCXImportService
from .models import (
    ExamBlueprint,
    ExamScenario,
    FacultyContribution,
    Question,
    QuestionImportBatch,
)
from .scenario_content import canonicalize_scenario_content
from .questionnaire_printing import (
    FacultyQuestionnairePrintService,
    _questionnaire_paper_context,
)
from .personalized_answer_sheets import PersonalizedAnswerSheetService


def _scope(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or getattr(request.user, "default_tenant_id", None)
    return tenant_id, scope.get("campus_id")


def _error_response(request, exc=None, *, default_status=400):
    if isinstance(exc, ContributionQuotaReached):
        status = 409
    elif isinstance(exc, ContributionConflict):
        status = 409
    elif isinstance(exc, ContributionExpired):
        status = 410
    elif isinstance(exc, PermissionDenied):
        status = 403
    else:
        status = default_status
    if isinstance(exc, ContributionQuotaReached):
        page = (
            "Contribution quota reached",
            f"You have reached the required quota of {exc.quota} questions. No additional questions were added.",
            "Return to your contribution workspace to review, reorder, delete, or submit your questions.",
        )
    else:
        page = {
        400: (
            "Request could not be processed",
            "The submitted page state is missing or invalid. No changes were made.",
            "Reload the current workspace and try the action again.",
        ),
        403: (
            "Action unavailable",
            "This contribution or action is not currently available to your account.",
            "Return to your contribution list and review the current read-only or eligibility state.",
        ),
        409: (
            "Page state is out of date",
            "The contribution changed after this page or preview was loaded. No stale change was applied.",
            "Return to the contribution list, reopen the workspace, and try again from the latest state.",
        ),
        410: (
            "Confidential preview expired",
            "This confidential CSV preview is no longer available and its stored row payload cannot be reused.",
            "Return to the contribution workspace and upload the CSV again.",
        ),
        }.get(status)
    if page is None:
        page = (
            "Request unavailable",
            "The requested action could not be completed.",
            "Return to your contribution list and try again.",
        )
    title, explanation, next_action = page
    return render(
        request,
        "departmental_exams/faculty/error.html",
        {
            "error_title": title,
            "error_explanation": explanation,
            "error_next_action": next_action,
            "return_url": reverse("departmental_exams:contribution_list"),
        },
        status=status,
    )


def _faculty_error_page(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            return view_func(request, *args, **kwargs)
        except (PermissionDenied, ContributionQuotaReached) as exc:
            return _error_response(request, exc, default_status=403)

    return wrapped


def _owner_contribution(request, contribution_id):
    tenant_id, campus_id = _scope(request)
    contribution = get_object_or_404(
        ContributionSelector.owner_queryset(user=request.user, tenant_id=tenant_id),
        pk=contribution_id,
    )
    ContributionAuthorizationService.require_common_read_access(
        user=request.user,
        tenant=contribution.cycle_course.cycle.tenant,
        request_tenant_id=tenant_id,
        request_campus_id=campus_id,
    )
    return contribution


def _workspace_context(request, contribution):
    questions = list(
        contribution.questions.filter(
            Q(import_batch__isnull=True)
            | Q(import_batch__status=QuestionImportBatch.Status.CONFIRMED)
        ).order_by("position", "id")
    )
    saved_count = len(questions)
    quota = contribution.quota_snapshot
    configuration = contribution.cycle_course.configuration
    cycle = contribution.cycle_course.cycle
    tenant_id, campus_id = _scope(request)
    try:
        authority = ContributionAuthorizationService.require_mutable_locked(
            user=request.user,
            contribution=contribution,
            configuration=configuration,
            request_tenant_id=tenant_id,
            request_campus_id=campus_id,
        )
        live_eligibility_read_only = False
    except PermissionDenied as exc:
        authority = None
        live_eligibility_read_only = str(exc) == (
            "No current qualifying teaching assignment remains."
        )
    active_import = (
        contribution.question_import_batches.filter(
            uploading_user=contribution.faculty_user,
            status__in=QuestionImportBatch.active_statuses(),
        )
        .order_by("created_at", "id")
        .first()
    )
    is_mutable = authority is not None and active_import is None
    quota_reached = is_mutable and saved_count >= quota
    difficulty_distribution = ContributionDifficultyDistributionService.evaluate(
        questions=questions,
        quota=quota,
        configuration=configuration,
    )
    try:
        case_context = FacultyCasePolicy.context(
            contribution=contribution,
            tenant_id=tenant_id,
            required=False,
        )
    except (PermissionDenied, ValidationError):
        case_context = None
    scenarios = []
    linked_question_ids = set()
    if case_context is not None:
        scenarios = list(
            contribution.faculty_scenarios.select_related("section")
            .prefetch_related("members__question")
            .order_by("created_at", "id")
        )
        for scenario in scenarios:
            scenario.ordered_members = sorted(
                scenario.members.all(), key=lambda member: (member.position, member.id)
            )
            linked_question_ids.update(
                member.question_id for member in scenario.ordered_members
            )
    return {
        "contribution": contribution,
        "questions": questions,
        "standalone_questions": [
            question for question in questions if question.id not in linked_question_ids
        ],
        "faculty_cases": scenarios,
        "saved_count": saved_count,
        "quota": quota,
        "remaining": max(quota - saved_count, 0),
        "progress_percent": round((saved_count / quota) * 100) if quota else 0,
        "configuration": configuration,
        "offering_snapshots": contribution.cycle_course.offering_snapshots.all(),
        "is_mutable": is_mutable,
        "reopened_draft": (
            authority
            == ContributionAuthorizationService.REOPENED_DRAFT_AUTHORITY
        ),
        "live_eligibility_read_only": live_eligibility_read_only,
        "quota_reached": quota_reached,
        "difficulty_distribution": difficulty_distribution,
        "active_import": active_import,
        "active_import_progress": (
            QuestionCSVImportService.status_payload(active_import)
            if active_import
            else None
        ),
        "active_import_form": (
            QuestionCSVConfirmForm(initial={"file_sha256": active_import.file_sha256})
            if active_import
            else None
        ),
        "docx_import_enabled": FeatureSettingsService.is_departmental_exam_docx_import_enabled(
            tenant_id=cycle.tenant_id, default=False
        ),
        "case_authoring_enabled": case_context is not None,
        "case_mutation_enabled": case_context is not None and is_mutable,
    }


def _question_structure_options(request, contribution, question=None, scenario=None):
    tenant_id, _campus_id = _scope(request)
    context = FacultyCasePolicy.context(
        contribution=contribution,
        tenant_id=tenant_id,
        required=False,
    )
    if context is None:
        return {"sections": None, "require_section": False, "section_id": None, "scenario_id": None}
    blueprint, sections = context
    fixed_section = scenario.section if scenario else None
    section_id = None
    if fixed_section:
        section_id = fixed_section.id
    elif question is not None:
        placement = getattr(question, "blueprint_placement", None)
        section_id = placement.section_id if placement else None
    return {
        "sections": sections if blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS else None,
        "require_section": blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS,
        "fixed_section": fixed_section,
        "section_id": section_id,
        "scenario_id": scenario.id if scenario else None,
    }


def _require_currently_mutable(request, contribution):
    tenant_id, campus_id = _scope(request)
    ContributionAuthorizationService.require_mutable_locked(
        user=request.user,
        contribution=contribution,
        configuration=contribution.cycle_course.configuration,
        request_tenant_id=tenant_id,
        request_campus_id=campus_id,
    )
    ContributionAuthorizationService.require_no_active_import(
        contribution=contribution,
    )


def _require_add_capacity(contribution):
    ContributionAuthorizationService.require_add_capacity(
        contribution=contribution,
        question_count=contribution.questions.filter(
            Q(import_batch__isnull=True)
            | Q(import_batch__status=QuestionImportBatch.Status.CONFIRMED)
        ).count(),
    )


def _require_faculty_builder_access(request):
    tenant_id, campus_id = _scope(request)
    tenant = get_object_or_404(Tenant, pk=tenant_id, is_active=True)
    ContributionAuthorizationService.require_common_read_access(
        user=request.user,
        tenant=tenant,
        request_tenant_id=tenant_id,
        request_campus_id=campus_id,
    )
    if not ContributionSelector.faculty_navigation_visible(
        user=request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    ):
        raise PermissionDenied("Departmental Exam Builder access is unavailable.")


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def resources_view(request):
    _require_faculty_builder_access(request)
    return render(request, "departmental_exams/faculty/resources.html")


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def answer_sheet_view(request):
    _require_faculty_builder_access(request)
    return render(
        request,
        "departmental_exams/faculty/answer_sheet.html",
        {
            "answer_columns": (
                range(1, 26),
                range(26, 51),
                range(51, 76),
            ),
            **_questionnaire_paper_context(request.GET.get("paper")),
        },
    )


@_faculty_error_page
@portal_required("FACULTY")
def contribution_list_view(request):
    tenant_id, campus_id = _scope(request)
    tenant = get_object_or_404(Tenant, pk=tenant_id, is_active=True)
    ContributionAuthorizationService.require_common_read_access(
        user=request.user,
        tenant=tenant,
        request_tenant_id=tenant_id,
        request_campus_id=campus_id,
    )
    if not ContributionSelector.faculty_navigation_visible(
        user=request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    ):
        raise PermissionDenied("No readable or qualifying contribution is available.")
    contributions = list(
        ContributionSelector.owner_queryset(user=request.user, tenant_id=tenant_id)
    )
    print_options = FacultyQuestionnairePrintService.available_options(
        contributions=contributions,
    )
    answer_key_options = FacultyAnswerKeyReleaseService.available_options(
        contributions=contributions,
    )
    for contribution in contributions:
        contribution.progress_percent = round(
            (contribution.saved_question_count / contribution.quota_snapshot) * 100
        )
        contribution.questionnaire_print = print_options.get(contribution.id)
        contribution.answer_key_release = answer_key_options.get(contribution.id)
        contribution.is_reopened_draft = (
            ContributionAuthorizationService.has_authorized_reopened_existing_draft_authority(
                user=request.user,
                contribution=contribution,
                configuration=contribution.cycle_course.configuration,
                request_tenant_id=tenant_id,
                request_campus_id=campus_id,
            )
        )
    return render(
        request,
        "departmental_exams/faculty/contribution_list.html",
        {"contributions": contributions},
    )


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def questionnaire_print_view(
    request,
    contribution_id,
    release_id,
    set_code,
):
    contribution = _owner_contribution(request, contribution_id)
    context = FacultyQuestionnairePrintService.build_safe_context(
        contribution=contribution,
        release_id=release_id,
        set_code=set_code,
        actor=request.user,
        request=request,
        paper_size=request.GET.get("paper"),
    )
    response = render(
        request,
        "departmental_exams/faculty/questionnaire_print.html",
        context,
    )
    response["Cache-Control"] = "no-store, no-cache, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def personalized_answer_sheet_overview_view(request, contribution_id, release_id):
    contribution = _owner_contribution(request, contribution_id)
    context = PersonalizedAnswerSheetService.overview_context(
        contribution=contribution,
        release_id=release_id,
    )
    response = render(
        request,
        "departmental_exams/faculty/personalized_answer_sheet_overview.html",
        context,
    )
    response["Cache-Control"] = "no-store, no-cache, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@_faculty_error_page
@portal_required("FACULTY")
@require_POST
def personalized_answer_sheet_prepare_view(
    request,
    contribution_id,
    release_id,
    offering_id,
):
    contribution = _owner_contribution(request, contribution_id)
    result = PersonalizedAnswerSheetService.prepare(
        contribution=contribution,
        release_id=release_id,
        offering_id=offering_id,
        actor=request.user,
        request=request,
    )
    if result["created_count"]:
        messages.success(
            request,
            f"Prepared {result['created_count']} personalized answer sheet assignment(s).",
        )
    else:
        messages.info(request, "All active students already have persistent Set assignments.")
    return redirect(
        "departmental_exams:personalized_answer_sheet_overview",
        contribution_id=contribution.id,
        release_id=release_id,
    )


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def personalized_answer_sheet_print_view(
    request,
    contribution_id,
    release_id,
    offering_id,
    set_filter,
):
    contribution = _owner_contribution(request, contribution_id)
    context = PersonalizedAnswerSheetService.print_context(
        contribution=contribution,
        release_id=release_id,
        offering_id=offering_id,
        set_filter=set_filter,
        actor=request.user,
        request=request,
        paper_size=request.GET.get("paper"),
    )
    response = render(
        request,
        "departmental_exams/faculty/personalized_answer_sheet_print.html",
        context,
    )
    response["Cache-Control"] = "no-store, no-cache, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def _answer_key_response(request, *, contribution_id, release_id, set_code, printable):
    contribution = _owner_contribution(request, contribution_id)
    context, audit_context = FacultyAnswerKeyReleaseService.build_safe_context(
        contribution=contribution,
        release_id=release_id,
        set_code=set_code,
        actor=request.user,
        printable=printable,
        request=request,
        include_audit_context=True,
    )
    response = render(
        request,
        (
            "departmental_exams/faculty/answer_key_print.html"
            if printable
            else "departmental_exams/faculty/answer_key.html"
        ),
        context,
    )
    FacultyAnswerKeyReleaseService.record_access(
        audit_context=audit_context,
        actor=request.user,
        printable=printable,
        request=request,
    )
    response["Cache-Control"] = "no-store, no-cache, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def answer_key_view(request, contribution_id, release_id, set_code):
    return _answer_key_response(
        request,
        contribution_id=contribution_id,
        release_id=release_id,
        set_code=set_code,
        printable=False,
    )


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def answer_key_print_view(request, contribution_id, release_id, set_code):
    return _answer_key_response(
        request,
        contribution_id=contribution_id,
        release_id=release_id,
        set_code=set_code,
        printable=True,
    )


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def checking_master_print_view(request, contribution_id, release_id, set_code):
    contribution = _owner_contribution(request, contribution_id)
    context = FacultyAnswerKeyReleaseService.build_checking_master_context(
        contribution=contribution,
        release_id=release_id,
        set_code=set_code,
        actor=request.user,
        request=request,
    )
    context.update(_questionnaire_paper_context(request.GET.get("paper")))
    response = render(
        request,
        "departmental_exams/faculty/checking_master_print.html",
        context,
    )
    response["Cache-Control"] = "no-store, no-cache, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@_faculty_error_page
@never_cache
@portal_required("FACULTY")
def contribution_workspace_view(request, contribution_id):
    contribution = _owner_contribution(request, contribution_id)
    return render(
        request,
        "departmental_exams/faculty/contribution_workspace.html",
        _workspace_context(request, contribution),
    )


def _question_initial(question, contribution):
    initial = {
        "expected_contribution_revision": contribution.revision,
        "expected_question_revision": question.revision if question else None,
    }
    if question:
        initial.update(
            {
                field: getattr(question, field)
                for field in (
                    "question_text",
                    "choice_a",
                    "choice_b",
                    "choice_c",
                    "choice_d",
                    "correct_answer",
                    "difficulty",
                )
            }
        )
    return initial


@_faculty_error_page
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def question_create_view(request, contribution_id, scenario_id=None):
    contribution = _owner_contribution(request, contribution_id)
    _require_currently_mutable(request, contribution)
    _require_add_capacity(contribution)
    scenario = None
    if scenario_id is not None:
        scenario = get_object_or_404(
            ExamScenario,
            pk=scenario_id,
            contribution=contribution,
            contribution__faculty_user=request.user,
        )
    structure = _question_structure_options(
        request, contribution, scenario=scenario
    )
    initial = _question_initial(None, contribution)
    if structure["section_id"]:
        initial["section_id"] = structure["section_id"]
    form = QuestionForm(
        request.POST or None,
        initial=initial,
        sections=structure["sections"],
        require_section=structure["require_section"],
        fixed_section=structure.get("fixed_section"),
        scenario_id=structure["scenario_id"],
    )
    if request.method == "POST" and form.is_valid():
        tenant_id, campus_id = _scope(request)
        try:
            question = QuestionMutationService.create(
                contribution_id=contribution.id,
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
                payload=form.cleaned_data,
                section_id=form.cleaned_data.get("section_id"),
                scenario_id=form.cleaned_data.get("scenario_id"),
                request=request,
            )
        except (ContributionConflict, ValidationError) as exc:
            return _error_response(request, exc)
        if getattr(question, "duplicate_warning", False):
            messages.warning(request, "This question resembles another question you have saved. It was saved because duplicates are warning-only.")
        messages.success(request, "Linked Question added." if scenario else "Question added.")
        return redirect("departmental_exams:contribution_workspace", contribution_id=contribution.id)
    return render(
        request,
        "departmental_exams/faculty/question_form.html",
        {"form": form, "contribution": contribution, "mode": "create", "scenario": scenario},
        status=400 if request.method == "POST" else 200,
    )


@_faculty_error_page
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def question_edit_view(request, contribution_id, question_id):
    contribution = _owner_contribution(request, contribution_id)
    question = get_object_or_404(
        Question,
        pk=question_id,
        contribution=contribution,
        contribution__faculty_user=request.user,
    )
    _require_currently_mutable(request, contribution)
    membership = getattr(question, "exam_scenario_membership", None)
    scenario = membership.scenario if membership else None
    structure = _question_structure_options(
        request, contribution, question=question, scenario=scenario
    )
    initial = _question_initial(question, contribution)
    if structure["section_id"]:
        initial["section_id"] = structure["section_id"]
    form = QuestionForm(
        request.POST or None,
        initial=initial,
        sections=structure["sections"],
        require_section=structure["require_section"],
        fixed_section=structure.get("fixed_section"),
        scenario_id=structure["scenario_id"],
    )
    if request.method == "POST" and form.is_valid():
        tenant_id, campus_id = _scope(request)
        try:
            updated_question, changed = QuestionMutationService.update(
                contribution_id=contribution.id,
                question_id=question.id,
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
                expected_question_revision=form.cleaned_data["expected_question_revision"],
                payload=form.cleaned_data,
                section_id=form.cleaned_data.get("section_id"),
                scenario_id=form.cleaned_data.get("scenario_id"),
                request=request,
            )
        except (ContributionConflict, ValidationError) as exc:
            return _error_response(request, exc)
        if getattr(updated_question, "duplicate_warning", False):
            messages.warning(request, "This question resembles another question you have saved. It remains allowed as a warning-only duplicate.")
        messages.success(request, "Question updated." if changed else "No question changes were needed.")
        return redirect("departmental_exams:contribution_workspace", contribution_id=contribution.id)
    return render(
        request,
        "departmental_exams/faculty/question_form.html",
        {"form": form, "contribution": contribution, "question": question, "mode": "edit", "scenario": scenario},
        status=400 if request.method == "POST" else 200,
    )


@_faculty_error_page
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def question_delete_view(request, contribution_id, question_id):
    contribution = _owner_contribution(request, contribution_id)
    question = get_object_or_404(
        Question,
        pk=question_id,
        contribution=contribution,
        contribution__faculty_user=request.user,
    )
    _require_currently_mutable(request, contribution)
    form = QuestionDeleteForm(
        request.POST or None,
        initial={
            "expected_contribution_revision": contribution.revision,
            "expected_question_revision": question.revision,
        },
    )
    if request.method == "POST" and form.is_valid():
        tenant_id, campus_id = _scope(request)
        try:
            QuestionMutationService.delete(
                contribution_id=contribution.id,
                question_id=question.id,
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
                expected_question_revision=form.cleaned_data["expected_question_revision"],
                request=request,
            )
        except (ContributionConflict, ValidationError) as exc:
            return _error_response(request, exc)
        messages.success(request, "Question deleted.")
        return redirect("departmental_exams:contribution_workspace", contribution_id=contribution.id)
    return render(
        request,
        "departmental_exams/faculty/question_delete.html",
        {"form": form, "contribution": contribution, "question": question},
        status=400 if request.method == "POST" else 200,
    )


@_faculty_error_page
@portal_required("FACULTY")
@require_POST
def question_reorder_view(request, contribution_id):
    contribution = _owner_contribution(request, contribution_id)
    form = QuestionReorderForm(request.POST)
    if not form.is_valid():
        return _error_response(request, default_status=400)
    tenant_id, campus_id = _scope(request)
    try:
        QuestionMutationService.reorder(
            contribution_id=contribution.id,
            ordered_question_ids=form.cleaned_data["ordered_question_ids"],
            user=request.user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
            request=request,
        )
    except (ContributionConflict, ValidationError) as exc:
        return _error_response(request, exc)
    messages.success(request, "Question order saved.")
    return redirect("departmental_exams:contribution_workspace", contribution_id=contribution.id)


def _owner_case(request, contribution, scenario_id):
    scenario = get_object_or_404(
        ExamScenario.objects.select_related("section", "blueprint"),
        pk=scenario_id,
        contribution=contribution,
        contribution__faculty_user=request.user,
    )
    tenant_id, _campus_id = _scope(request)
    FacultyCasePolicy.context(
        contribution=contribution,
        tenant_id=tenant_id,
    )
    return scenario


def _case_form(request, contribution, scenario=None):
    tenant_id, _campus_id = _scope(request)
    blueprint, sections = FacultyCasePolicy.context(
        contribution=contribution,
        tenant_id=tenant_id,
    )
    initial = {
        "expected_contribution_revision": contribution.revision,
        "expected_scenario_revision": scenario.revision if scenario else 0,
        "title": scenario.title if scenario else "",
        "stimulus": scenario.stimulus if scenario else "",
    }
    if scenario and scenario.section_id:
        initial["section_id"] = scenario.section_id
    return FacultyCaseForm(
        request.POST or None,
        initial=initial,
        sections=sections,
        require_section=blueprint.mode == ExamBlueprint.Mode.USE_SECTIONS,
    )


def _render_case_form(request, *, contribution, form, scenario=None, status=200):
    return render(
        request,
        "departmental_exams/faculty/case_form.html",
        {
            "contribution": contribution,
            "scenario": scenario,
            "form": form,
            "preview_url": reverse(
                "departmental_exams:faculty_case_preview",
                args=[contribution.id],
            ),
        },
        status=status,
    )


@_faculty_error_page
@never_cache
@portal_required("FACULTY")
@require_POST
def faculty_case_preview_view(request, contribution_id):
    contribution = _owner_contribution(request, contribution_id)
    _require_currently_mutable(request, contribution)
    tenant_id, _campus_id = _scope(request)
    FacultyCasePolicy.context(contribution=contribution, tenant_id=tenant_id)
    try:
        canonical = canonicalize_scenario_content(
            request.POST.get("stimulus", ""),
            input_format=request.POST.get("input_format", "html"),
        )
    except ValidationError as exc:
        return JsonResponse({"errors": exc.messages}, status=400)
    return JsonResponse(
        {"html": canonical.html, "warnings": list(canonical.warnings)}
    )


@_faculty_error_page
@never_cache
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def faculty_case_create_view(request, contribution_id):
    contribution = _owner_contribution(request, contribution_id)
    _require_currently_mutable(request, contribution)
    form = _case_form(request, contribution)
    if request.method == "POST" and form.is_valid():
        tenant_id, campus_id = _scope(request)
        try:
            scenario, _creating = FacultyCaseMutationService.save(
                contribution_id=contribution.id,
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
                expected_scenario_revision=form.cleaned_data.get("expected_scenario_revision") or 0,
                title=form.cleaned_data["title"],
                raw_content=form.cleaned_data["stimulus"],
                section_id=form.cleaned_data.get("section_id"),
                request=request,
            )
        except (ContributionConflict, ValidationError) as exc:
            form.add_error(None, exc)
        else:
            for warning in getattr(scenario, "content_warnings", ()):
                messages.warning(request, warning)
            messages.success(request, "Case saved. You can now add Linked Questions.")
            return redirect(
                "departmental_exams:faculty_case_detail",
                contribution_id=contribution.id,
                scenario_id=scenario.id,
            )
    return _render_case_form(
        request,
        contribution=contribution,
        form=form,
        status=400 if request.method == "POST" else 200,
    )


@_faculty_error_page
@never_cache
@portal_required("FACULTY")
def faculty_case_detail_view(request, contribution_id, scenario_id):
    contribution = _owner_contribution(request, contribution_id)
    scenario = _owner_case(request, contribution, scenario_id)
    members = list(
        scenario.members.select_related("question").order_by("position", "id")
    )
    tenant_id, campus_id = _scope(request)
    try:
        mutable = bool(
            ContributionAuthorizationService.require_mutable_locked(
                user=request.user,
                contribution=contribution,
                configuration=contribution.cycle_course.configuration,
                request_tenant_id=tenant_id,
                request_campus_id=campus_id,
            )
        )
    except PermissionDenied:
        mutable = False
    return render(
        request,
        "departmental_exams/faculty/case_detail.html",
        {
            "contribution": contribution,
            "scenario": scenario,
            "members": members,
            "is_mutable": mutable,
        },
    )


@_faculty_error_page
@never_cache
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def faculty_case_edit_view(request, contribution_id, scenario_id):
    contribution = _owner_contribution(request, contribution_id)
    scenario = _owner_case(request, contribution, scenario_id)
    _require_currently_mutable(request, contribution)
    form = _case_form(request, contribution, scenario)
    if request.method == "POST" and form.is_valid():
        tenant_id, campus_id = _scope(request)
        try:
            updated, _creating = FacultyCaseMutationService.save(
                contribution_id=contribution.id,
                scenario_id=scenario.id,
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
                expected_scenario_revision=form.cleaned_data["expected_scenario_revision"],
                title=form.cleaned_data["title"],
                raw_content=form.cleaned_data["stimulus"],
                section_id=form.cleaned_data.get("section_id"),
                request=request,
            )
        except (ContributionConflict, ValidationError) as exc:
            form.add_error(None, exc)
        else:
            for warning in getattr(updated, "content_warnings", ()):
                messages.warning(request, warning)
            messages.success(request, "Case updated.")
            return redirect(
                "departmental_exams:faculty_case_detail",
                contribution_id=contribution.id,
                scenario_id=scenario.id,
            )
    return _render_case_form(
        request,
        contribution=contribution,
        form=form,
        scenario=scenario,
        status=400 if request.method == "POST" else 200,
    )


@_faculty_error_page
@never_cache
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def faculty_case_delete_view(request, contribution_id, scenario_id):
    contribution = _owner_contribution(request, contribution_id)
    scenario = _owner_case(request, contribution, scenario_id)
    _require_currently_mutable(request, contribution)
    form = FacultyCaseDeleteForm(
        request.POST or None,
        initial={
            "expected_contribution_revision": contribution.revision,
            "expected_scenario_revision": scenario.revision,
        },
    )
    if request.method == "POST" and form.is_valid():
        tenant_id, campus_id = _scope(request)
        try:
            FacultyCaseMutationService.delete(
                contribution_id=contribution.id,
                scenario_id=scenario.id,
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
                expected_scenario_revision=form.cleaned_data["expected_scenario_revision"],
                request=request,
            )
        except (ContributionConflict, ValidationError) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Case deleted.")
            return redirect(
                "departmental_exams:contribution_workspace",
                contribution_id=contribution.id,
            )
    return render(
        request,
        "departmental_exams/faculty/case_delete.html",
        {"contribution": contribution, "scenario": scenario, "form": form},
        status=400 if request.method == "POST" else 200,
    )


@_faculty_error_page
@portal_required("FACULTY")
@require_POST
def faculty_case_reorder_view(request, contribution_id, scenario_id):
    contribution = _owner_contribution(request, contribution_id)
    scenario = _owner_case(request, contribution, scenario_id)
    form = FacultyCaseMemberReorderForm(request.POST)
    if not form.is_valid():
        return _error_response(request, default_status=400)
    tenant_id, campus_id = _scope(request)
    try:
        FacultyCaseMutationService.reorder_members(
            contribution_id=contribution.id,
            scenario_id=scenario.id,
            ordered_question_ids=form.cleaned_data["ordered_question_ids"],
            user=request.user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
            expected_scenario_revision=form.cleaned_data["expected_scenario_revision"],
            request=request,
        )
    except (ContributionConflict, ValidationError) as exc:
        return _error_response(request, exc)
    messages.success(request, "Linked Question order saved.")
    return redirect(
        "departmental_exams:faculty_case_detail",
        contribution_id=contribution.id,
        scenario_id=scenario.id,
    )


@_faculty_error_page
@portal_required("FACULTY")
def csv_template_view(request, contribution_id):
    _owner_contribution(request, contribution_id)
    response = HttpResponse(
        QuestionCSVImportService.template_bytes(),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{CSV_FILENAME}"'
    return response


@_faculty_error_page
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def csv_upload_view(request, contribution_id):
    contribution = _owner_contribution(request, contribution_id)
    _require_currently_mutable(request, contribution)
    _require_add_capacity(contribution)
    saved_count = contribution.questions.filter(
        Q(import_batch__isnull=True)
        | Q(import_batch__status=QuestionImportBatch.Status.CONFIRMED)
    ).count()
    form = QuestionCSVUploadForm(
        request.POST or None,
        request.FILES or None,
        initial={"expected_contribution_revision": contribution.revision},
    )
    if request.method == "POST" and form.is_valid():
        tenant_id, campus_id = _scope(request)
        try:
            batch = QuestionCSVImportService.create_preview(
                contribution_id=contribution.id,
                uploaded_file=form.cleaned_data["csv_file"],
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
            )
        except (ContributionConflict, ValidationError) as exc:
            return _error_response(request, exc)
        return redirect("departmental_exams:csv_preview", token=batch.token)
    return render(
        request,
        "departmental_exams/faculty/csv_upload.html",
        {
            "form": form,
            "contribution": contribution,
            "saved_count": saved_count,
            "remaining": max(contribution.quota_snapshot - saved_count, 0),
        },
        status=400 if request.method == "POST" else 200,
    )


@_faculty_error_page
@portal_required("FACULTY")
def csv_preview_view(request, token):
    tenant_id, campus_id = _scope(request)
    try:
        batch = QuestionCSVImportService.owner_batch(
            token=token, user=request.user, tenant_id=tenant_id
        )
    except ContributionExpired as exc:
        return _error_response(request, exc)
    if batch.source_format != QuestionImportBatch.SourceFormat.CSV:
        raise Http404
    ContributionAuthorizationService.require_common_read_access(
        user=request.user,
        tenant=batch.contribution.cycle_course.cycle.tenant,
        request_tenant_id=tenant_id,
        request_campus_id=campus_id,
    )
    existing_count = batch.contribution.questions.filter(
        Q(import_batch__isnull=True)
        | Q(import_batch__status=QuestionImportBatch.Status.CONFIRMED)
    ).count()
    if (
        batch.status == batch.Status.READY
        and not batch.error_count
        and existing_count >= batch.contribution.quota_snapshot
    ):
        return _error_response(
            request,
            ContributionQuotaReached(batch.contribution.quota_snapshot),
        )
    can_confirm = (
        batch.status in QuestionImportBatch.resumable_statuses() and not batch.error_count
    )
    if can_confirm:
        try:
            ContributionAuthorizationService.require_mutable_locked(
                user=request.user,
                contribution=batch.contribution,
                configuration=batch.contribution.cycle_course.configuration,
                request_tenant_id=tenant_id,
                request_campus_id=campus_id,
            )
        except PermissionDenied:
            can_confirm = False
    return render(
        request,
        "departmental_exams/faculty/csv_preview.html",
        {
            "batch": batch,
            "rows": list(batch.rows.all()),
            "existing_count": existing_count,
            "remaining": max(batch.contribution.quota_snapshot - existing_count, 0),
            "can_confirm": can_confirm,
            "confirm_form": QuestionCSVConfirmForm(initial={"file_sha256": batch.file_sha256}),
            "import_progress": QuestionCSVImportService.status_payload(batch),
        },
    )


@_faculty_error_page
@portal_required("FACULTY")
def csv_error_report_view(request, token):
    tenant_id, campus_id = _scope(request)
    try:
        batch = QuestionCSVImportService.owner_batch(
            token=token, user=request.user, tenant_id=tenant_id
        )
    except ContributionExpired as exc:
        return _error_response(request, exc)
    if batch.source_format != QuestionImportBatch.SourceFormat.CSV:
        raise Http404
    ContributionAuthorizationService.require_common_read_access(
        user=request.user,
        tenant=batch.contribution.cycle_course.cycle.tenant,
        request_tenant_id=tenant_id,
        request_campus_id=campus_id,
    )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="TeacherMatePlus_Question_Import_Errors.csv"'
    writer = csv.writer(response)
    writer.writerow(["row_number", "field", "error"])
    for row in batch.rows.all():
        for error in row.errors:
            writer.writerow([row.row_number, error.get("field", "row"), error.get("message", "Invalid row.")])
    return response


@_faculty_error_page
@portal_required("FACULTY")
@require_POST
def csv_confirm_view(request, token):
    form = QuestionCSVConfirmForm(request.POST)
    if not form.is_valid():
        return _error_response(request, default_status=400)
    tenant_id, campus_id = _scope(request)
    source_format = QuestionImportBatch.objects.filter(
        token=token,
        tenant_id=tenant_id,
        uploading_user=request.user,
        contribution__faculty_user=request.user,
    ).values_list("source_format", flat=True).first()
    if source_format != QuestionImportBatch.SourceFormat.CSV:
        raise Http404
    is_async = request.headers.get("x-requested-with") == "XMLHttpRequest"
    try:
        if is_async:
            batch, _created = QuestionCSVImportService.process_next_chunk(
                token=token,
                expected_file_sha256=form.cleaned_data["file_sha256"],
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                request=request,
            )
            payload = QuestionCSVImportService.status_payload(batch)
            payload.update({
                "status_url": reverse("departmental_exams:csv_status", args=[batch.token]),
                "resume_url": reverse("departmental_exams:csv_confirm", args=[batch.token]),
                "workspace_url": reverse(
                    "departmental_exams:contribution_workspace",
                    args=[batch.contribution_id],
                ),
            })
            return JsonResponse(payload)
        batch, changed = QuestionCSVImportService.confirm(
            token=token,
            expected_file_sha256=form.cleaned_data["file_sha256"],
            user=request.user,
            tenant_id=tenant_id,
            campus_id=campus_id,
            request=request,
        )
    except (PermissionDenied, ContributionConflict, ContributionExpired, ValidationError) as exc:
        if is_async:
            status = (
                403
                if isinstance(exc, PermissionDenied)
                else 410
                if isinstance(exc, ContributionExpired)
                else 409
                if isinstance(exc, ContributionConflict)
                else 400
            )
            try:
                failed_batch = QuestionCSVImportService.owner_batch(
                    token=token,
                    user=request.user,
                    tenant_id=tenant_id,
                )
                payload = QuestionCSVImportService.status_payload(failed_batch)
            except (Http404, ContributionExpired):
                payload = {
                    "status": "UNAVAILABLE",
                    "committed_rows": 0,
                    "total_rows": 0,
                    "percentage": 0,
                    "can_resume": False,
                    "completed": False,
                    "failure_code": "UNAVAILABLE",
                    "failure_message": "The import is no longer available.",
                }
            payload["error"] = payload.get("failure_message") or "The import could not continue safely."
            return JsonResponse(payload, status=status)
        return _error_response(request, exc)
    messages.success(request, "CSV questions imported." if changed else "This CSV was already imported.")
    return redirect(
        "departmental_exams:contribution_workspace",
        contribution_id=batch.contribution_id,
    )


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def csv_status_view(request, token):
    tenant_id, campus_id = _scope(request)
    try:
        batch = QuestionCSVImportService.owner_batch(
            token=token,
            user=request.user,
            tenant_id=tenant_id,
        )
    except ContributionExpired as exc:
        return _error_response(request, exc)
    if batch.source_format != QuestionImportBatch.SourceFormat.CSV:
        raise Http404
    ContributionAuthorizationService.require_common_read_access(
        user=request.user,
        tenant=batch.contribution.cycle_course.cycle.tenant,
        request_tenant_id=tenant_id,
        request_campus_id=campus_id,
    )
    payload = QuestionCSVImportService.status_payload(batch)
    payload.update({
        "status_url": reverse("departmental_exams:csv_status", args=[batch.token]),
        "resume_url": reverse("departmental_exams:csv_confirm", args=[batch.token]),
        "workspace_url": reverse(
            "departmental_exams:contribution_workspace",
            args=[batch.contribution_id],
        ),
    })
    return JsonResponse(payload)


@_faculty_error_page
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def docx_upload_view(request, contribution_id):
    contribution = _owner_contribution(request, contribution_id)
    _require_currently_mutable(request, contribution)
    _require_add_capacity(contribution)
    tenant_id, campus_id = _scope(request)
    QuestionDOCXImportService.require_feature(tenant_id=tenant_id)
    saved_count = contribution.questions.filter(
        Q(import_batch__isnull=True) | Q(import_batch__status=QuestionImportBatch.Status.CONFIRMED)
    ).count()
    form = QuestionDOCXUploadForm(
        request.POST or None, request.FILES or None,
        initial={"expected_contribution_revision": contribution.revision},
    )
    if request.method == "POST" and form.is_valid():
        try:
            batch = QuestionDOCXImportService.create_preview(
                contribution_id=contribution.id,
                uploaded_file=form.cleaned_data["docx_file"],
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
            )
        except (ContributionConflict, ValidationError) as exc:
            return _error_response(request, exc)
        return redirect("departmental_exams:docx_preview", token=batch.token)
    return render(request, "departmental_exams/faculty/docx_upload.html", {
        "form": form, "contribution": contribution, "saved_count": saved_count,
        "remaining": max(contribution.quota_snapshot - saved_count, 0),
    }, status=400 if request.method == "POST" else 200)


def _docx_owner_batch(request, token):
    tenant_id, campus_id = _scope(request)
    batch = QuestionDOCXImportService.owner_batch(
        token=token, user=request.user, tenant_id=tenant_id
    )
    if batch.source_format != QuestionImportBatch.SourceFormat.DOCX:
        raise Http404
    QuestionDOCXImportService.require_feature(tenant_id=tenant_id)
    ContributionAuthorizationService.require_common_read_access(
        user=request.user,
        tenant=batch.contribution.cycle_course.cycle.tenant,
        request_tenant_id=tenant_id,
        request_campus_id=campus_id,
    )
    return batch


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def docx_preview_view(request, token):
    try:
        batch = _docx_owner_batch(request, token)
    except ContributionExpired as exc:
        return _error_response(request, exc)
    tenant_id, campus_id = _scope(request)
    existing_count = batch.contribution.questions.filter(
        Q(import_batch__isnull=True) | Q(import_batch__status=QuestionImportBatch.Status.CONFIRMED)
    ).count()
    can_confirm = batch.status in QuestionImportBatch.resumable_statuses() and not batch.error_count
    if can_confirm:
        try:
            ContributionAuthorizationService.require_mutable_locked(
                user=request.user,
                contribution=batch.contribution,
                configuration=batch.contribution.cycle_course.configuration,
                request_tenant_id=tenant_id,
                request_campus_id=campus_id,
            )
        except PermissionDenied:
            can_confirm = False
    return render(request, "departmental_exams/faculty/docx_preview.html", {
        "batch": batch, "rows": list(batch.rows.all()), "existing_count": existing_count,
        "remaining": max(batch.contribution.quota_snapshot - existing_count, 0),
        "can_confirm": can_confirm,
        "confirm_form": QuestionCSVConfirmForm(initial={"file_sha256": batch.file_sha256}),
        "import_progress": QuestionDOCXImportService.status_payload(batch),
    })


@_faculty_error_page
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def docx_row_edit_view(request, token, row_number):
    try:
        batch = _docx_owner_batch(request, token)
    except ContributionExpired as exc:
        return _error_response(request, exc)
    row = get_object_or_404(batch.rows, row_number=row_number)
    initial = dict(row.payload)
    initial["expected_contribution_revision"] = batch.contribution_revision_snapshot
    form = QuestionDOCXRowForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        tenant_id, campus_id = _scope(request)
        try:
            QuestionDOCXImportService.update_staged_row(
                token=token, row_number=row_number, payload=form.cleaned_data,
                user=request.user, tenant_id=tenant_id, campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
            )
        except (ContributionConflict, ContributionExpired, ValidationError) as exc:
            return _error_response(request, exc)
        messages.success(request, f"Word question {row_number - 1} was revalidated and staged.")
        return redirect("departmental_exams:docx_preview", token=token)
    return render(request, "departmental_exams/faculty/docx_row_edit.html", {
        "batch": batch, "row": row, "form": form,
    }, status=400 if request.method == "POST" else 200)


@_faculty_error_page
@portal_required("FACULTY")
@require_POST
def docx_confirm_view(request, token):
    form = QuestionCSVConfirmForm(request.POST)
    if not form.is_valid():
        return _error_response(request, default_status=400)
    tenant_id, campus_id = _scope(request)
    source_format = QuestionImportBatch.objects.filter(
        token=token,
        tenant_id=tenant_id,
        uploading_user=request.user,
        contribution__faculty_user=request.user,
    ).values_list("source_format", flat=True).first()
    if source_format != QuestionImportBatch.SourceFormat.DOCX:
        raise Http404
    is_async = request.headers.get("x-requested-with") == "XMLHttpRequest"
    try:
        method = QuestionDOCXImportService.process_next_chunk if is_async else QuestionDOCXImportService.confirm
        batch, changed = method(
            token=token, expected_file_sha256=form.cleaned_data["file_sha256"],
            user=request.user, tenant_id=tenant_id, campus_id=campus_id, request=request,
        )
        if batch.source_format != QuestionImportBatch.SourceFormat.DOCX:
            raise Http404
        if is_async:
            payload = QuestionDOCXImportService.status_payload(batch)
            payload.update({
                "status_url": reverse("departmental_exams:docx_status", args=[batch.token]),
                "resume_url": reverse("departmental_exams:docx_confirm", args=[batch.token]),
                "workspace_url": reverse("departmental_exams:contribution_workspace", args=[batch.contribution_id]),
            })
            return JsonResponse(payload)
    except (PermissionDenied, ContributionConflict, ContributionExpired, ValidationError) as exc:
        if not is_async:
            return _error_response(request, exc)
        status = 403 if isinstance(exc, PermissionDenied) else 410 if isinstance(exc, ContributionExpired) else 409 if isinstance(exc, ContributionConflict) else 400
        return JsonResponse({"error": str(exc), "failure_message": str(exc), "completed": False, "can_resume": False, "committed_rows": 0, "total_rows": 0, "percentage": 0}, status=status)
    messages.success(request, "Word questions imported." if changed else "This Word preview was already imported.")
    return redirect("departmental_exams:contribution_workspace", contribution_id=batch.contribution_id)


@_faculty_error_page
@portal_required("FACULTY")
@require_GET
def docx_status_view(request, token):
    try:
        batch = _docx_owner_batch(request, token)
    except ContributionExpired as exc:
        return _error_response(request, exc)
    payload = QuestionDOCXImportService.status_payload(batch)
    payload.update({
        "status_url": reverse("departmental_exams:docx_status", args=[batch.token]),
        "resume_url": reverse("departmental_exams:docx_confirm", args=[batch.token]),
        "workspace_url": reverse("departmental_exams:contribution_workspace", args=[batch.contribution_id]),
    })
    return JsonResponse(payload)


@_faculty_error_page
@portal_required("FACULTY")
@require_http_methods(["GET", "POST"])
def contribution_submit_view(request, contribution_id):
    contribution = _owner_contribution(request, contribution_id)
    if request.method == "GET" and contribution.status == FacultyContribution.Status.SUBMITTED:
        return redirect("departmental_exams:contribution_workspace", contribution_id=contribution.id)
    if contribution.status != FacultyContribution.Status.SUBMITTED:
        _require_currently_mutable(request, contribution)
    form = ContributionSubmitForm(
        request.POST or None,
        initial={"expected_contribution_revision": contribution.revision},
    )
    if request.method == "POST" and form.is_valid():
        tenant_id, campus_id = _scope(request)
        try:
            _contribution, changed = QuestionMutationService.submit(
                contribution_id=contribution.id,
                user=request.user,
                tenant_id=tenant_id,
                campus_id=campus_id,
                expected_contribution_revision=form.cleaned_data["expected_contribution_revision"],
                request=request,
            )
        except ContributionDifficultyDeficient as exc:
            form.add_error(None, exc.messages[0])
        except (ContributionConflict, ValidationError) as exc:
            return _error_response(request, exc)
        else:
            messages.success(request, "Contribution submitted." if changed else "Contribution was already submitted.")
            return redirect("departmental_exams:contribution_workspace", contribution_id=contribution.id)
    return render(
        request,
        "departmental_exams/faculty/contribution_submit.html",
        {
            "form": form,
            "contribution": contribution,
            "difficulty_distribution": ContributionDifficultyDistributionService.evaluate(
                questions=contribution.questions.filter(
                    Q(import_batch__isnull=True)
                    | Q(import_batch__status=QuestionImportBatch.Status.CONFIRMED)
                ),
                quota=contribution.quota_snapshot,
                configuration=contribution.cycle_course.configuration,
            ),
        },
        status=400 if request.method == "POST" else 200,
    )
