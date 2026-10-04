"""Scoped checker and faculty DTR screens."""

from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseForbidden, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import User
from apps.academics.models import CourseOffering
from apps.admin_portal.services import AdminScopeService
from apps.core.decorators import portal_required
from apps.core.services.features import FeatureSettingsService
from apps.tenants.models import Campus, Department, Tenant

from .dtr import (
    _faculty_departments, _faculty_is_ac, _require, ac_department_summary, adjustment_revision_history, checker_summary, current_adjustments, faculty_final_dtr,
    final_dtr_departments, finalize_dtr, latest_dtr, preview_dtr, printable_final_snapshot, save_adjustment,
)
from .forms import DTRAdjustmentForm, DTRAdjustmentRemovalForm, DTREarlyCorrectionForm, DTRFinalizationForm, DTRMixedFindingForm
from .dtr_intervals import current_mixed_decisions, needs_interval_reconciliation, save_mixed_decision
from .models import AttendanceCutoffPublication, AttendanceResult, DTRAdjustment, FacultyDTR
from .observations import AttendanceResultService
from .permissions import DTR_AC_SUMMARY_PERMISSION, DTR_PRINT_PERMISSION, DTR_VIEW_PERMISSION, can_faculty_view_own_attendance
from .permissions import CORRECT_PERMISSION, DTR_EDIT_PERMISSION, DTR_FINALIZE_PERMISSION, require_attendance_permission


def _scope(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id")
    campus_id = scope.get("campus_id")
    if not tenant_id or not campus_id:
        raise PermissionDenied("Select a tenant and campus scope first.")
    return tenant_id, campus_id


def _publications(tenant_id, campus_id):
    publications = AttendanceCutoffPublication.objects.filter(
        tenant_id=tenant_id, campus_id=campus_id,
    ).select_related("academic_year", "term", "faculty_scope").order_by("-end_date", "-start_date", "-version")
    latest = {}
    for publication in publications:
        key = (publication.academic_year_id, publication.term_id, publication.start_date, publication.end_date, publication.faculty_scope_id)
        latest.setdefault(key, publication)
    return list(latest.values())


def _selected_publication(request, tenant_id, campus_id, publications):
    value = request.POST.get("publication") if request.method == "POST" else request.GET.get("publication")
    if not value:
        return publications[0] if publications else None
    try:
        pk = int(value)
    except (TypeError, ValueError) as exc:
        raise Http404("Unknown campus cutoff publication.") from exc
    publication = next((item for item in publications if item.pk == pk), None)
    if publication is None:
        raise Http404("Unknown or superseded campus cutoff publication.")
    return publication


def _selected_faculty(request, publication, summary):
    value = request.POST.get("faculty") if request.method == "POST" else request.GET.get("faculty")
    if not value:
        return summary[0]["faculty"] if summary else None
    try:
        pk = int(value)
    except (TypeError, ValueError) as exc:
        raise Http404("Unknown DTR faculty.") from exc
    faculty = next((item["faculty"] for item in summary if item["faculty"].pk == pk), None)
    if faculty is None:
        raise Http404("Faculty is not in this authorized cutoff.")
    return faculty


def _allowed(request, publication, code, departments):
    try:
        _require(request.user, code, publication, departments)
        return True
    except PermissionDenied:
        return False


def _is_ajax(request):
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


def _adjustment_form(*, departments, publication, data=None, initial=None):
    return DTRAdjustmentForm(
        data,
        department_queryset=departments,
        cutoff_start_date=publication.start_date,
        cutoff_end_date=publication.end_date,
        initial=initial,
    )


def _removal_form(*, data=None, initial=None):
    return DTRAdjustmentRemovalForm(data, initial=initial)


def _detail_context(*, request, publication, faculty, editable_departments, departments,
                    adjustment_form, early_form, final_form, mixed_form, edit_item,
                    early_item, mixed_item, removal_form, remove_item, page_notice=""):
    preview = preview_dtr(actor=request.user, publication=publication, faculty=faculty) if faculty else None
    if preview:
        for line in preview.snapshot["lines"]:
            line["can_correct"] = line["kind"] == "CLASS" and not line.get("closure_revision") and _allowed(
                request, publication, CORRECT_PERMISSION, {line["department_id"]},
            )
            line["can_reconcile"] = line["kind"] == "CLASS" and line.get("needs_mixed_reconciliation") and _allowed(
                request, publication, DTR_EDIT_PERMISSION, {line["department_id"]},
            )
    final = latest_dtr(publication=publication, faculty=faculty) if faculty else None
    adjustment_rows = current_adjustments(publication=publication, faculty=faculty) if faculty else []
    revision_history = adjustment_revision_history(publication=publication, faculty=faculty) if faculty else {}
    for item in adjustment_rows:
        item.can_edit_dtr = item.department_id in editable_departments
        item.is_removed = item.hours == 0
        item.can_remove_dtr = item.can_edit_dtr and not item.is_removed
        item.prior_revisions = revision_history.get(item.entry_key, [])[1:]
    if preview and not final_form.is_bound:
        final_form = DTRFinalizationForm(initial={"expected_fingerprint": preview.fingerprint})
    has_mixed_reconciliation = bool(preview and any(
        line.get("can_reconcile") for line in preview.snapshot["lines"]
    ))
    return {
        "faculty": faculty,
        "preview": preview,
        "final": final,
        "adjustment_rows": adjustment_rows,
        "adjustment_form": adjustment_form,
        "removal_form": removal_form,
        "remove_item": remove_item,
        "page_notice": page_notice,
        "early_form": early_form,
        "final_form": final_form,
        "edit_item": edit_item,
        "mixed_form": mixed_form,
        "mixed_item": mixed_item,
        "early_item": early_item,
        "show_early_form": bool(early_item or request.method == "POST" and request.POST.get("action") == "early"),
        "show_mixed_form": bool(mixed_item or request.method == "POST" and request.POST.get("action") == "mixed"),
        "show_removal_form": bool(remove_item or request.method == "POST" and request.POST.get("action") == "remove_adjustment"),
        "needs_refinalization": bool(final and preview and final.review_fingerprint != preview.fingerprint),
        "already_final": bool(final and preview and final.review_fingerprint == preview.fingerprint),
        "has_mixed_reconciliation": has_mixed_reconciliation,
        "can_edit": bool(editable_departments),
        "can_finalize": bool(preview and _allowed(
            request, publication, DTR_FINALIZE_PERMISSION,
            _faculty_departments(publication, faculty),
        )),
        "can_print": bool(final and _allowed(
            request, publication, DTR_PRINT_PERMISSION,
            final_dtr_departments(final),
        )),
    }


def _ajax_dtr_payload(*, request, context, summary, publication, message, ok, status=200):
    faculty = context.get("faculty")
    row = next((item for item in summary if faculty and item["faculty"].pk == faculty.pk), None)
    return JsonResponse({
        "ok": ok,
        "message": message,
        "faculty_id": faculty.pk if faculty else None,
        "faculty_html": render_to_string("faculty_attendance/_dtr_faculty_detail.html", context, request=request),
        "summary_row_html": render_to_string(
            "faculty_attendance/_dtr_summary_row.html",
            {"row": row, "publication": publication, "faculty": faculty},
            request=request,
        ) if row else "",
    }, status=status)


@portal_required("ADMIN")
def dtr_review_view(request):
    tenant_id, campus_id = _scope(request)
    if not FeatureSettingsService.is_faculty_attendance_enabled(tenant_id=tenant_id):
        return HttpResponseForbidden("Faculty Attendance is disabled for this tenant.")
    publications = _publications(tenant_id, campus_id)
    # Faculty publications are exposed only where every saved department is
    # authorized. Complete-campus publications retain their existing gate.
    publications = [p for p in publications if not p.faculty_scope_id or _allowed(
        request, p, DTR_VIEW_PERMISSION, set(p.scope_snapshot.get("departments", [])))]
    if not publications:
        department_ids = set(CourseOffering.objects.filter(
            tenant_id=tenant_id, campus_id=campus_id,
        ).values_list("department_id", flat=True)) or set(Department.objects.filter(
            tenant_id=tenant_id, campus_id=campus_id,
        ).values_list("pk", flat=True))
        if not department_ids:
            return HttpResponseForbidden("DTR viewing authority requires an authorized campus department.")
        try:
            for department_id in department_ids:
                require_attendance_permission(
                    user=request.user, permission_code=DTR_VIEW_PERMISSION,
                    tenant_id=tenant_id, campus_id=campus_id, department_id=department_id,
                )
        except PermissionDenied:
            return HttpResponseForbidden("Complete-campus DTR viewing authority is required.")
        if request.method == "POST":
            return HttpResponseForbidden("Publish a campus cutoff before posting DTR changes.")
        return render(request, "faculty_attendance/dtr_review.html", {
            "publications": [], "publication": None, "summary": [],
            "tenant_name": Tenant.objects.filter(pk=tenant_id).values_list("name", flat=True).first(),
            "campus_name": Campus.objects.filter(pk=campus_id).values_list("name", flat=True).first(),
        })
    publication = _selected_publication(request, tenant_id, campus_id, publications)
    try:
        summary = checker_summary(actor=request.user, publication=publication)
    except PermissionDenied:
        return HttpResponseForbidden("Complete-campus DTR viewing authority is required.")
    faculty = _selected_faculty(request, publication, summary)
    departments = AdminScopeService.scoped_departments(request).filter(
        tenant_id=tenant_id, campus_id=campus_id,
    )
    editable_departments = [row.pk for row in departments if _allowed(request, publication, DTR_EDIT_PERMISSION, {row.pk})]
    departments = departments.filter(pk__in=editable_departments)
    adjustment_form = _adjustment_form(departments=departments, publication=publication)
    removal_form = _removal_form()
    early_form = DTREarlyCorrectionForm()
    mixed_form = DTRMixedFindingForm()
    final_form = DTRFinalizationForm()
    edit_item = None
    remove_item = None
    page_notice = ""
    early_item = None
    mixed_item = None
    ajax_message = ""
    if faculty and request.method == "GET" and request.GET.get("edit"):
        try:
            edit_pk = int(request.GET["edit"])
        except (TypeError, ValueError) as exc:
            raise Http404("Unknown DTR entry.") from exc
        edit_item = next((item for item in current_adjustments(publication=publication, faculty=faculty) if item.pk == edit_pk), None)
        if edit_item is None:
            raise Http404("This DTR entry is not current for the selected faculty.")
        adjustment_form = _adjustment_form(departments=departments, publication=publication, initial={
            "entry_date": edit_item.entry_date, "department": edit_item.department_id,
            "kind": edit_item.kind, "hours": edit_item.hours, "leave_type": edit_item.leave_type,
            "offset_kind": edit_item.offset_kind, "reason": edit_item.reason,
            "previous_id": edit_item.pk, "expected_revision": edit_item.revision,
        })
    if faculty and request.method == "GET" and request.GET.get("remove"):
        try:
            remove_pk = int(request.GET["remove"])
        except (TypeError, ValueError):
            remove_pk = None
        remove_item = next(
            (
                item for item in current_adjustments(publication=publication, faculty=faculty)
                if item.pk == remove_pk and item.hours > 0
            ),
            None,
        )
        if remove_item is None:
            page_notice = "That checker entry is no longer active. Review the current entry list and use its latest available action."
        elif not _allowed(request, publication, DTR_EDIT_PERMISSION, {remove_item.department_id}):
            return HttpResponseForbidden("DTR entry removal authority is required.")
        else:
            removal_form = _removal_form(initial={
                "entry_id": remove_item.pk,
                "expected_revision": remove_item.revision,
            })
    if faculty and request.method == "GET" and request.GET.get("early"):
        try:
            meeting_pk = int(request.GET["early"])
        except (TypeError, ValueError) as exc:
            raise Http404("Unknown published class.") from exc
        early_item = publication.entries.filter(faculty_user=faculty, meeting_id=meeting_pk).select_related("meeting__attendance_result").first()
        if early_item is None or not hasattr(early_item.meeting, "attendance_result"):
            raise Http404("This class has no current attendance finding.")
        if not _allowed(request, publication, CORRECT_PERMISSION, {early_item.meeting.department_id}):
            return HttpResponseForbidden("Attendance correction authority is required.")
        result = early_item.meeting.attendance_result
        early_form = DTREarlyCorrectionForm(initial={
            "result_id": result.pk, "expected_revision": result.revision,
            "minutes": result.early_minutes,
        })
    if faculty and request.method == "GET" and request.GET.get("mixed"):
        try:
            mixed_pk = int(request.GET["mixed"])
        except (TypeError, ValueError) as exc:
            raise Http404("Unknown mixed-finding class.") from exc
        mixed_item = publication.entries.filter(faculty_user=faculty, meeting_id=mixed_pk).first()
        if mixed_item is None or not needs_interval_reconciliation(mixed_item):
            raise Http404("Select a published class with mixed A/N and other missed findings.")
        if not _allowed(request, publication, DTR_EDIT_PERMISSION, {mixed_item.meeting.department_id}):
            return HttpResponseForbidden("DTR interval reconciliation authority is required.")
        previous = current_mixed_decisions(publication=publication, faculty=faculty).get(mixed_pk)
        mixed_form = DTRMixedFindingForm(initial={
            "meeting_id": mixed_pk, "expected_revision": previous.revision if previous else 0,
            "intervals": "\n".join(
                f"{item['kind']} {item['start']}-{item['end']}" for item in previous.intervals
            ) if previous else "",
        })
    if request.method == "POST":
        if not faculty:
            return HttpResponseForbidden("Select a faculty DTR first.")
        action = request.POST.get("action")
        try:
            if action == "adjustment":
                adjustment_form = _adjustment_form(
                    departments=departments, publication=publication, data=request.POST,
                )
                if adjustment_form.is_valid():
                    values = adjustment_form.cleaned_data
                    prior = None
                    if values.get("previous_id"):
                        prior = get_object_or_404(
                            DTRAdjustment, pk=values["previous_id"], faculty_user=faculty,
                            tenant_id=tenant_id, campus_id=campus_id,
                            start_date=publication.start_date, end_date=publication.end_date,
                        )
                    save_adjustment(
                        actor=request.user, publication=publication, faculty=faculty,
                        department=values["department"], entry_date=values["entry_date"],
                        kind=values["kind"], hours=values["hours"], reason=values["reason"],
                        leave_type=values.get("leave_type") or "", offset_kind=values.get("offset_kind") or "",
                        previous=prior, expected_revision=values.get("expected_revision") or 0,
                    )
                    if not _is_ajax(request):
                        messages.success(request, "DTR entry saved with its checker revision and reason.")
                        return redirect(f"{reverse('faculty_attendance:dtr_review')}?{urlencode({'publication': publication.pk, 'faculty': faculty.pk})}")
                    ajax_message = "DTR entry saved as a new checker revision."
                    adjustment_form = _adjustment_form(departments=departments, publication=publication)
                    edit_item = None
            elif action == "remove_adjustment":
                removal_form = _removal_form(data=request.POST)
                try:
                    requested_entry_id = int(request.POST.get("entry_id", ""))
                except (TypeError, ValueError):
                    requested_entry_id = None
                remove_item = next(
                    (
                        item for item in current_adjustments(publication=publication, faculty=faculty)
                        if item.pk == requested_entry_id
                    ),
                    None,
                )
                if removal_form.is_valid():
                    values = removal_form.cleaned_data
                    if remove_item is None or remove_item.hours == 0:
                        historical = DTRAdjustment.objects.filter(
                            pk=values["entry_id"], faculty_user=faculty,
                            tenant_id=tenant_id, campus_id=campus_id,
                            academic_year_id=publication.academic_year_id, term_id=publication.term_id,
                            start_date=publication.start_date, end_date=publication.end_date,
                        ).select_related("department").first()
                        if historical and not _allowed(
                            request, publication, DTR_EDIT_PERMISSION, {historical.department_id},
                        ):
                            raise PermissionDenied("DTR entry removal is outside your authorized department scope.")
                        remove_item = None
                        page_notice = "That checker entry changed after its removal link was opened. Reload the current state; no revision was removed."
                        raise ValidationError(page_notice)
                    if remove_item.department_id not in editable_departments:
                        raise PermissionDenied("DTR entry removal is outside your authorized department scope.")
                    removed = save_adjustment(
                        actor=request.user, publication=publication, faculty=faculty,
                        department=remove_item.department, entry_date=remove_item.entry_date,
                        kind=remove_item.kind, hours=0, reason=values["reason"],
                        leave_type=remove_item.leave_type, offset_kind=remove_item.offset_kind,
                        previous=remove_item, expected_revision=values["expected_revision"],
                    )
                    message = (
                        f"Entry R{remove_item.revision} was removed as zero-hour entry revision R{removed.revision}. "
                        "Review the changed DTR, then finalize a new DTR revision."
                    )
                    if not _is_ajax(request):
                        messages.success(request, message)
                        return redirect(
                            f"{reverse('faculty_attendance:dtr_review')}?"
                            f"{urlencode({'publication': publication.pk, 'faculty': faculty.pk})}"
                        )
                    ajax_message = message
                    removal_form = _removal_form()
                    remove_item = None
            elif action == "early":
                early_form = DTREarlyCorrectionForm(request.POST)
                if early_form.is_valid():
                    values = early_form.cleaned_data
                    entry = publication.entries.filter(faculty_user=faculty, meeting__attendance_result__pk=values["result_id"]).first()
                    if entry is None:
                        raise PermissionDenied("The class is not in this faculty's published cutoff.")
                    result = AttendanceResult.objects.get(pk=values["result_id"])
                    AttendanceResultService.correct_early_dismissal(
                        actor=request.user, result=result, expected_revision=values["expected_revision"],
                        minutes=values["minutes"], reason=values["reason"],
                    )
                    messages.success(request, "Early dismissal corrected in attendance history. Republish this cutoff before DTR finalization.")
                    return redirect(f"{reverse('faculty_attendance:dtr_review')}?{urlencode({'publication': publication.pk, 'faculty': faculty.pk})}")
            elif action == "mixed":
                mixed_form = DTRMixedFindingForm(request.POST)
                if mixed_form.is_valid():
                    values = mixed_form.cleaned_data
                    mixed_item = publication.entries.filter(
                        faculty_user=faculty, meeting_id=values["meeting_id"],
                    ).select_related("meeting").first()
                    if mixed_item is None:
                        raise PermissionDenied("The class is not in this faculty's published cutoff.")
                    saved = save_mixed_decision(
                        actor=request.user, publication=publication, faculty=faculty,
                        meeting=mixed_item.meeting, intervals_text=values["intervals"],
                        reason=values["reason"], expected_revision=values["expected_revision"],
                    )
                    messages.success(request, f"Actual missed intervals saved as DTR decision revision {saved.revision}.")
                    return redirect(f"{reverse('faculty_attendance:dtr_review')}?{urlencode({'publication': publication.pk, 'faculty': faculty.pk})}")
            elif action == "finalize":
                final_form = DTRFinalizationForm(request.POST)
                if final_form.is_valid():
                    values = final_form.cleaned_data
                    final = finalize_dtr(
                        actor=request.user, publication=publication, faculty=faculty,
                        expected_fingerprint=values["expected_fingerprint"],
                        reason=values["reason"], faculty_review_complete=values["faculty_review_complete"],
                    )
                    if not _is_ajax(request):
                        messages.success(request, f"Faculty DTR finalized as revision {final.revision}.")
                        return redirect(f"{reverse('faculty_attendance:dtr_review')}?{urlencode({'publication': publication.pk, 'faculty': faculty.pk})}")
                    ajax_message = f"Faculty DTR finalized as revision {final.revision}."
                    final_form = DTRFinalizationForm()
            else:
                return HttpResponseNotAllowed(["GET", "POST"])
        except PermissionDenied:
            return HttpResponseForbidden("DTR action is outside your authorized scope.")
        except ValidationError as exc:
            if action == "adjustment":
                if hasattr(exc, "error_dict"):
                    for field, errors in exc.error_dict.items():
                        if field in adjustment_form.fields:
                            adjustment_form.add_error(field, errors)
                        else:
                            adjustment_form.add_error(None, errors)
                else:
                    adjustment_form.add_error(None, exc)
            elif action == "remove_adjustment":
                removal_form.add_error(None, exc)
            elif action == "early":
                early_form.add_error(None, exc)
            elif action == "mixed":
                mixed_form.add_error(None, exc)
            else:
                final_form.add_error(None, exc)
    if ajax_message:
        summary = checker_summary(actor=request.user, publication=publication)
    detail = _detail_context(
        request=request, publication=publication, faculty=faculty,
        editable_departments=editable_departments, departments=departments,
        adjustment_form=adjustment_form, early_form=early_form, final_form=final_form,
        mixed_form=mixed_form, edit_item=edit_item, early_item=early_item, mixed_item=mixed_item,
        removal_form=removal_form, remove_item=remove_item, page_notice=page_notice,
    )
    context = {
        "publications": publications, "publication": publication, "summary": summary,
        **detail,
        "can_summary_print": _allowed(request, publication, DTR_PRINT_PERMISSION,
                                      set().union(*(_faculty_departments(publication, row["faculty"]) for row in summary))),
        "ac_departments": [item for item in AdminScopeService.scoped_departments(request).filter(
            tenant_id=tenant_id, campus_id=campus_id,
        ) if _faculty_is_ac(faculty=request.user, publication=publication, department_id=item.pk)
           and _allowed(request, publication, DTR_AC_SUMMARY_PERMISSION, {item.pk})
           and _allowed(request, publication, DTR_PRINT_PERMISSION, {item.pk})],
        "tenant_name": publication.tenant.name, "campus_name": publication.campus.name,
    }
    if _is_ajax(request) and (
        request.GET.get("partial") == "faculty"
        or request.POST.get("action") in {"adjustment", "remove_adjustment", "finalize"}
    ):
        ok = bool(ajax_message) or request.method == "GET"
        message = ajax_message or (
            "Faculty DTR loaded." if request.method == "GET"
            else "Correct the highlighted DTR entry before saving."
        )
        return _ajax_dtr_payload(
            request=request, context=context, summary=summary, publication=publication,
            message=message, ok=ok, status=200 if ok else 400,
        )
    return render(request, "faculty_attendance/dtr_review.html", context)


@portal_required("ADMIN")
def dtr_cutoff_summary_view(request):
    tenant_id, campus_id = _scope(request)
    publication = get_object_or_404(AttendanceCutoffPublication, pk=request.GET.get("publication"), tenant_id=tenant_id, campus_id=campus_id)
    if publication not in _publications(tenant_id, campus_id):
        raise Http404("Select the latest version of this cutoff.")
    try:
        rows = checker_summary(actor=request.user, publication=publication)
        departments = set().union(*(_faculty_departments(publication, row["faculty"]) for row in rows))
        _require(request.user, DTR_PRINT_PERMISSION, publication, departments)
    except PermissionDenied:
        return HttpResponseForbidden("Complete-campus DTR printing authority is required.")
    return render(request, "faculty_attendance/dtr_summary_print.html", {"publication": publication, "rows": rows})


@portal_required("ADMIN")
def dtr_ac_summary_view(request):
    tenant_id, campus_id = _scope(request)
    publication = get_object_or_404(AttendanceCutoffPublication, pk=request.GET.get("publication"), tenant_id=tenant_id, campus_id=campus_id)
    if publication not in _publications(tenant_id, campus_id):
        raise Http404("Select the latest version of this cutoff.")
    department = get_object_or_404(Department, pk=request.GET.get("department"), tenant_id=tenant_id, campus_id=campus_id)
    try:
        rows = ac_department_summary(actor=request.user, publication=publication, department=department)
    except PermissionDenied:
        return HttpResponseForbidden("AC department DTR summary is unavailable in this scope.")
    return render(request, "faculty_attendance/dtr_ac_summary_print.html", {
        "publication": publication, "department": department, "rows": rows,
    })


@portal_required("ADMIN")
def dtr_print_view(request, public_id):
    tenant_id, campus_id = _scope(request)
    final = get_object_or_404(FacultyDTR, public_id=public_id, tenant_id=tenant_id, campus_id=campus_id)
    try:
        _require(request.user, DTR_PRINT_PERMISSION, final.publication, final_dtr_departments(final))
    except PermissionDenied:
        return HttpResponseForbidden("DTR printing authority is required for every included department.")
    return render(request, "faculty_attendance/dtr_print.html", {
        "final": final, "snapshot": printable_final_snapshot(final.snapshot),
    })


@portal_required("FACULTY")
def my_dtr_view(request):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or request.user.default_tenant_id
    campus_id = scope.get("campus_id") or request.user.default_campus_id
    if not tenant_id or not campus_id or not can_faculty_view_own_attendance(
        user=request.user, tenant_id=tenant_id, campus_id=campus_id,
    ):
        return HttpResponseForbidden("My DTR is disabled or unavailable for this account.")
    history = request.GET.get("history") == "1"
    finals = list(FacultyDTR.objects.filter(
        tenant_id=tenant_id, campus_id=campus_id, faculty_user=request.user,
    ).order_by("-end_date", "-revision", "-pk"))
    if not history:
        latest = {}
        for row in finals:
            latest.setdefault((row.academic_year_id, row.term_id, row.start_date, row.end_date), row)
        finals = list(latest.values())
    return render(request, "faculty_attendance/my_dtr.html", {"finals": finals, "history": history})


@portal_required("FACULTY")
def my_dtr_print_view(request, public_id):
    scope = getattr(request, "scope", {})
    tenant_id = scope.get("tenant_id") or request.user.default_tenant_id
    campus_id = scope.get("campus_id") or request.user.default_campus_id
    final = get_object_or_404(FacultyDTR, public_id=public_id, tenant_id=tenant_id, campus_id=campus_id)
    try:
        faculty_final_dtr(user=request.user, final=final)
    except PermissionDenied:
        return HttpResponseForbidden("Only the DTR owner may print this finalized record.")
    return render(request, "faculty_attendance/dtr_print.html", {
        "final": final, "snapshot": printable_final_snapshot(final.snapshot),
    })
