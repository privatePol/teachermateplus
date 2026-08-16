from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.core.decorators import portal_required

from .automatic_generation_audit import audit_automatic_generation_result_access
from .generation_reporting import (
    GenerationAnswerKeyService,
    GenerationReportingAuthorizationService,
    GenerationSelectionAuditReportService,
    audit_generation_report_access,
)
from .generation_services import ExamGenerationService
from .models import AutomaticGenerationAuditRun
from .questionnaire_printing import AdminQuestionnairePrintService
from .services import DepartmentalExamAuthorizationService


def _tenant_id(request):
    tenant_id = getattr(request, "scope", {}).get("tenant_id") or getattr(
        request.user, "default_tenant_id", None
    )
    if not tenant_id:
        raise PermissionDenied("An active tenant scope is required.")
    return tenant_id


def _revision(request, revision_id):
    return ExamGenerationService.revision_for_tenant(
        revision_id=revision_id,
        tenant_id=_tenant_id(request),
    )


def _confidential_response(request, template_name, context):
    response = render(request, template_name, context)
    response["Cache-Control"] = "no-store, no-cache, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


@portal_required("ADMIN")
@require_GET
def generation_selection_audit_view(request, revision_id):
    revision = _revision(request, revision_id)
    GenerationReportingAuthorizationService.require_view(
        user=request.user,
        revision=revision,
    )
    context = GenerationSelectionAuditReportService.build_context(
        revision=revision,
        filter_code=request.GET.get("filter", "all"),
    )
    audit_generation_report_access(
        revision=revision,
        actor=request.user,
        request=request,
        action="DE_GENERATION_SELECTION_AUDIT_ACCESSED",
        metadata={
            "filter": context["active_filter"],
            "returned_rows": len(context["rows"]),
            "source_snapshot_available": context["audit_available"],
        },
    )
    return _confidential_response(
        request,
        "departmental_exams/admin/generation_selection_audit.html",
        context,
    )


@portal_required("ADMIN")
@require_GET
def generation_selection_audit_print_view(request, revision_id):
    revision = _revision(request, revision_id)
    GenerationReportingAuthorizationService.require_view(
        user=request.user,
        revision=revision,
    )
    context = GenerationSelectionAuditReportService.build_context(
        revision=revision,
        filter_code=request.GET.get("filter", "all"),
    )
    audit_generation_report_access(
        revision=revision,
        actor=request.user,
        request=request,
        action="DE_GENERATION_SELECTION_AUDIT_PRINTED",
        metadata={
            "filter": context["active_filter"],
            "returned_rows": len(context["rows"]),
            "source_snapshot_available": context["audit_available"],
        },
    )
    return _confidential_response(
        request,
        "departmental_exams/admin/generation_selection_audit_print.html",
        context,
    )


@portal_required("ADMIN")
@require_GET
def generation_answer_key_view(request, revision_id, set_code):
    revision = _revision(request, revision_id)
    GenerationReportingAuthorizationService.require_view(
        user=request.user,
        revision=revision,
    )
    context = GenerationAnswerKeyService.build_context(
        revision=revision,
        set_code=set_code,
    )
    context["can_print"] = GenerationReportingAuthorizationService.can_print(
        user=request.user,
        revision=revision,
    )
    audit_generation_report_access(
        revision=revision,
        actor=request.user,
        request=request,
        action="DE_GENERATION_KEY_ACCESSED",
        metadata={
            "set_code": context["set_code"],
            "item_count": len(context["items"]),
        },
    )
    return _confidential_response(
        request,
        "departmental_exams/admin/generation_answer_key.html",
        context,
    )


@portal_required("ADMIN")
@require_GET
def generation_answer_key_print_view(request, revision_id, set_code):
    revision = _revision(request, revision_id)
    GenerationReportingAuthorizationService.require_print(
        user=request.user,
        revision=revision,
    )
    context = GenerationAnswerKeyService.build_context(
        revision=revision,
        set_code=set_code,
    )
    audit_generation_report_access(
        revision=revision,
        actor=request.user,
        request=request,
        action="DE_GENERATION_KEY_PRINTED",
        metadata={
            "set_code": context["set_code"],
            "item_count": len(context["items"]),
        },
    )
    return _confidential_response(
        request,
        "departmental_exams/admin/generation_answer_key_print.html",
        context,
    )


@portal_required("ADMIN")
@require_GET
def admin_questionnaire_print_view(request, revision_id, set_code):
    revision = _revision(request, revision_id)
    context = AdminQuestionnairePrintService.build_safe_context(
        revision=revision,
        set_code=set_code,
        actor=request.user,
        request=request,
        paper_size=request.GET.get("paper"),
    )
    return _confidential_response(
        request,
        "departmental_exams/faculty/questionnaire_print.html",
        context,
    )


def _automatic_audit_run(request, revision_id, audit_run_id):
    revision = _revision(request, revision_id)
    DepartmentalExamAuthorizationService.require_generation_audit(
        user=request.user,
        cycle_course=revision.cycle_course,
    )
    try:
        return AutomaticGenerationAuditRun.objects.select_related(
            "generation_revision__cycle_course__cycle__academic_year",
            "generation_revision__cycle_course__cycle__term",
            "generation_revision__cycle_course__course",
            "run_by",
        ).get(
            pk=audit_run_id,
            generation_revision=revision,
        )
    except AutomaticGenerationAuditRun.DoesNotExist as exc:
        raise Http404("Automatic generation audit result does not exist.") from exc


def _automatic_audit_context(run):
    revision = run.generation_revision
    cycle = revision.cycle_course.cycle
    return {
        "audit_run": run,
        "revision": revision,
        "cycle_course": revision.cycle_course,
        "academic_year": cycle.academic_year.name,
        "term": cycle.term.name,
        "exam_period": cycle.get_exam_period_display(),
        "findings": tuple(run.findings_snapshot or ()),
        "summary": run.summary_counts_snapshot or {},
    }


@portal_required("ADMIN")
@require_GET
def automatic_generation_audit_result_view(
    request, revision_id, audit_run_id
):
    run = _automatic_audit_run(request, revision_id, audit_run_id)
    audit_automatic_generation_result_access(
        run=run,
        actor=request.user,
        request=request,
    )
    return _confidential_response(
        request,
        "departmental_exams/admin/automatic_generation_audit_result.html",
        _automatic_audit_context(run),
    )


@portal_required("ADMIN")
@require_GET
def automatic_generation_audit_result_print_view(
    request, revision_id, audit_run_id
):
    run = _automatic_audit_run(request, revision_id, audit_run_id)
    audit_automatic_generation_result_access(
        run=run,
        actor=request.user,
        request=request,
        printable=True,
    )
    return _confidential_response(
        request,
        "departmental_exams/admin/automatic_generation_audit_result_print.html",
        _automatic_audit_context(run),
    )
