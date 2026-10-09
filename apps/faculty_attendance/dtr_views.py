"""Scoped checker and faculty DTR screens."""

from urllib.parse import urlencode
from datetime import timedelta

from django import forms
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
from apps.rbac.models import UserRole

from .dtr import (
    _faculty_departments, _faculty_is_ac, _require, ac_department_summary, adjustment_revision_history, current_adjustments, faculty_final_dtr,
    final_dtr_departments, finalize_dtr, latest_dtr, preview_dtr, printable_final_snapshot, save_adjustment,
    resolve_entry_department, save_admin_hours,
)
from .forms import DTRAdjustmentForm, DTRAdjustmentRemovalForm, DTREarlyCorrectionForm, DTRFinalizationForm, DTRMixedFindingForm, AdminHoursFormSet
from .dtr_intervals import current_mixed_decisions, needs_interval_reconciliation, save_mixed_decision
from .dtr_printing import build_dtr_matrix
from .models import AttendanceCutoffPublication, AttendanceResult, DTRAdjustment, FacultyDTR
from .observations import AttendanceResultService
from .permissions import DTR_AC_SUMMARY_PERMISSION, DTR_PRINT_PERMISSION, DTR_VIEW_PERMISSION, can_faculty_view_own_attendance
from .permissions import CORRECT_PERMISSION, DTR_EDIT_PERMISSION, DTR_FINALIZE_PERMISSION, require_attendance_permission
from .permissions import attendance_read_permissions


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
    ).select_related("academic_year", "term", "faculty_scope", "tenant", "campus").order_by("-end_date", "-start_date", "-published_at", "-pk")
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
    if publication is None and request.method == "GET":
        saved = AttendanceCutoffPublication.objects.filter(pk=pk, tenant_id=tenant_id, campus_id=campus_id).first()
        if saved:
            publication = next((item for item in publications if _cutoff_key(item) == _cutoff_key(saved)
                and item.faculty_scope_id == saved.faculty_scope_id), None)
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


def _cutoff_key(publication):
    return f"{publication.academic_year_id}:{publication.term_id}:{publication.start_date}:{publication.end_date}"


def _cutoff_rows(request, publication):
    """Share the existing batched, scoped read path across a cutoff's faculties."""
    from .faculty_cutoffs import review_faculty_cutoff
    from .faculty_cutoff_views import processing_rows
    scope = dict(tenant_id=publication.tenant_id, campus_id=publication.campus_id,
        academic_year=publication.academic_year, term=publication.term,
        start_date=publication.start_date, end_date=publication.end_date)
    with attendance_read_permissions():
        review = review_faculty_cutoff(actor=request.user, **scope, permission_code=DTR_VIEW_PERMISSION)
        items = processing_rows(actor=request.user, review=review)
    # A campus/global permission role alone is not a faculty workload. Allow
    # genuine department-scoped AC assignments, saved teaching/entries/finals,
    # and historical zero-hour corrections even after current unassignment.
    ac_owners = set(UserRole.objects.filter(is_active=True, role__is_active=True,
        role__code__in=("AC", "AREA_CHAIR", "AREA_CHAIRPERSON"), tenant_id=publication.tenant_id,
        campus_id=publication.campus_id, department__tenant_id=publication.tenant_id,
        department__campus_id=publication.campus_id).values_list("user_id", flat=True))
    entry_owners = set(DTRAdjustment.objects.filter(**scope).values_list("faculty_user_id", flat=True))
    rows = []
    for item in items:
        faculty = item["slice"].faculty
        if not (item["slice"].records or item["final"] or faculty.pk in entry_owners
                or faculty.pk in ac_owners or (item["publication"] and item["publication"].scope_snapshot.get("revises_teaching_to_empty"))):
            continue
        rows.append({**item, "faculty": faculty})
    return rows


def _automatic_entry_department(*, publication, faculty, departments, data):
    """Derive the entry's department from evidence, never a posted department."""
    if data.get("previous_id"):
        try:
            previous_id = int(data["previous_id"])
        except (TypeError, ValueError):
            raise ValidationError({"kind": "The checker entry revision is invalid. Reload its current entry."})
        previous = get_object_or_404(DTRAdjustment, pk=previous_id, faculty_user=faculty,
            tenant_id=publication.tenant_id, campus_id=publication.campus_id,
            academic_year_id=publication.academic_year_id, term_id=publication.term_id,
            start_date=publication.start_date, end_date=publication.end_date)
    else:
        previous = None
    try:
        entry_date = forms.DateField().clean(data.get("entry_date"))
    except ValidationError:
        entry_date = None
    pk = resolve_entry_department(publication=publication, faculty=faculty, kind=data.get("kind"),
        previous=previous, entry_date=entry_date, offset_kind=data.get("offset_kind", "")).pk
    if not departments.filter(pk=pk).exists():
        raise PermissionDenied("DTR entry department is outside your authorized scope.")
    return pk


def _is_ajax(request):
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    )


def _adjustment_form(*, departments, publication, data=None, initial=None):
    form = DTRAdjustmentForm(
        data,
        department_queryset=departments,
        cutoff_start_date=publication.start_date,
        cutoff_end_date=publication.end_date,
        initial=initial,
    )
    form.fields["department"].widget = forms.HiddenInput()
    return form


def _removal_form(*, data=None, initial=None):
    return DTRAdjustmentRemovalForm(data, initial=initial)


def _admin_hours_formset(publication, faculty, data=None):
    rows = current_adjustments(publication=publication, faculty=faculty) if faculty else []
    initial = [{"entry_date": r.entry_date, "hours": r.hours, "previous_id": r.pk,
        "expected_revision": r.revision} for r in rows if r.kind == "ADMIN"]
    return AdminHoursFormSet(data, initial=initial, prefix="admin_hours", form_kwargs={
        "cutoff_start_date": publication.start_date, "cutoff_end_date": publication.end_date})


def _detail_context(*, request, publication, faculty, editable_departments, departments,
                    adjustment_form, early_form, final_form, mixed_form, edit_item,
                    early_item, mixed_item, removal_form, remove_item, page_notice="", pending_publication=False):
    preview = preview_dtr(actor=request.user, publication=publication, faculty=faculty) if faculty and not pending_publication else None
    if preview:
        for line in preview.snapshot["lines"]:
            line["can_correct"] = line["kind"] == "CLASS" and not line.get("closure_revision") and _allowed(
                request, publication, CORRECT_PERMISSION, {line["department_id"]},
            )
            line["can_reconcile"] = line["kind"] == "CLASS" and line.get("needs_mixed_reconciliation") and _allowed(
                request, publication, DTR_EDIT_PERMISSION, {line["department_id"]},
            )
    final = latest_dtr(publication=publication, faculty=faculty) if faculty else None
    final_versions = list(FacultyDTR.objects.filter(tenant_id=publication.tenant_id,
        campus_id=publication.campus_id, academic_year_id=publication.academic_year_id,
        term_id=publication.term_id, start_date=publication.start_date, end_date=publication.end_date,
        faculty_user=faculty).select_related("publication").order_by("-revision", "-pk")) if faculty else []
    final_versions = [item for item in final_versions if _allowed(
        request, item.publication, DTR_VIEW_PERMISSION, final_dtr_departments(item))]
    for item in final_versions:
        item.can_print_dtr = _allowed(request, item.publication, DTR_PRINT_PERMISSION, final_dtr_departments(item))
    displayed_final = next((item for item in final_versions if item == final), None)
    if request.method == "GET" and request.GET.get("version"):
        displayed_final = next((item for item in final_versions
            if str(item.revision) == request.GET["version"]), None)
        if displayed_final is None:
            raise Http404("Unknown or unauthorized DTR version for this faculty and cutoff.")
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
        "pending_publication": pending_publication,
        "preview": preview,
        "final": final,
        "displayed_final": displayed_final,
        "displayed_can_print": bool(displayed_final and displayed_final.can_print_dtr),
        "displayed_snapshot": printable_final_snapshot(displayed_final.snapshot) if displayed_final else None,
        "final_versions": final_versions,
        "previous_final_versions": [item for item in final_versions if not final or item.pk != final.pk],
        "adjustment_rows": adjustment_rows,
        "admin_hours_formset": _admin_hours_formset(publication, faculty),
        "admin_dates": [publication.start_date + timedelta(days=i)
            for i in range((publication.end_date - publication.start_date).days + 1)],
        "can_admin_hours": bool(faculty and not pending_publication and (
            any(item.kind == "ADMIN" for item in adjustment_rows) or any(_faculty_is_ac(
                faculty=faculty, publication=publication, department_id=pk)
                for pk in _faculty_departments(publication, faculty)))),
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
        "can_edit": bool(faculty and editable_departments and not pending_publication),
        "can_finalize": bool(preview and _allowed(
            request, publication, DTR_FINALIZE_PERMISSION,
            _faculty_departments(publication, faculty),
        )),
        "can_print": bool(final and _allowed(
            request, final.publication, DTR_PRINT_PERMISSION,
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
        "publication_id": publication.pk,
        "cutoff": _cutoff_key(publication),
        "workspace_html": render_to_string("faculty_attendance/_dtr_workspace.html", context, request=request)
            if request.method == "GET" else "",
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
    cutoffs = {}
    for item in publications:
        cutoffs.setdefault(_cutoff_key(item), {"key": _cutoff_key(item), "publication": item})
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
    if request.method == "GET" and request.GET.get("cutoff"):
        selected = cutoffs.get(request.GET["cutoff"])
        if selected is None:
            raise Http404("Unknown or unauthorized cutoff.")
        publication = selected["publication"]
    else:
        publication = _selected_publication(request, tenant_id, campus_id, publications)
    try:
        summary = _cutoff_rows(request, publication)
    except PermissionDenied:
        return HttpResponseForbidden("Complete-campus DTR viewing authority is required.")
    selection_notice = ""
    pending_publication = False
    if request.method == "GET":
        requested_faculty = request.GET.get("faculty")
        if request.GET.get("reset_faculty") == "1":
            requested_faculty = None
        row = next((r for r in summary if str(r["faculty"].pk) == requested_faculty), None)
        if row is None and requested_faculty and not (request.GET.get("review") or request.GET.get("publication")):
            raise Http404("Faculty is not in this authorized cutoff.")
        if row is None:
            row = next((r for r in summary if r["faculty"].pk == publication.faculty_scope_id), None) or (summary[0] if summary else None)
            if requested_faculty:
                selection_notice = "The previous faculty is unavailable in this cutoff. The faculty choices have been refreshed."
        faculty = row["faculty"] if row else None
        if row:
            pending_publication = row["publication"] is None
            publication = row["publication"] or publication
    else:
        faculty = _selected_faculty(request, publication, summary)
        if faculty and not next(r["publication"] for r in summary if r["faculty"].pk == faculty.pk):
            return HttpResponseForbidden("Publish this faculty's attendance before saving DTR entries.")
        if faculty and publication.faculty_scope_id and publication.faculty_scope_id != faculty.pk:
            return HttpResponseForbidden("This publication belongs to another faculty.")
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
    page_notice = selection_notice
    early_item = None
    mixed_item = None
    ajax_message = ""
    admin_hours_formset = None
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
            if action == "admin_hours":
                admin_hours_formset = _admin_hours_formset(publication, faculty, request.POST)
                if admin_hours_formset.is_valid():
                    try:
                        save_admin_hours(actor=request.user, publication=publication, faculty=faculty,
                            rows=[form.cleaned_data for form in admin_hours_formset.forms])
                    except ValidationError as exc:
                        if hasattr(exc, "error_dict"):
                            for index, errors in exc.error_dict.items():
                                admin_hours_formset.forms[int(index)].add_error(None, errors)
                        else:
                            admin_hours_formset._non_form_errors = admin_hours_formset.error_class(exc.messages)
                    else:
                        if not _is_ajax(request):
                            messages.success(request, "All dated admin-hours entries saved. No duplicate hours were added.")
                            return redirect(f"{reverse('faculty_attendance:dtr_review')}?{urlencode({'publication': publication.pk, 'faculty': faculty.pk})}")
                        ajax_message = "All dated admin-hours entries saved. No duplicate hours were added."
                        admin_hours_formset = None
            elif action == "adjustment":
                data = request.POST.copy()
                adjustment_form = _adjustment_form(
                    departments=departments, publication=publication, data=data,
                )
                try:
                    data["department"] = _automatic_entry_department(publication=publication, faculty=faculty,
                        departments=departments, data=data)
                except ValidationError as exc:
                    # Run form cleaning before adding a field error; keep date,
                    # amount and note values on the validation redisplay.
                    adjustment_form.is_valid()
                    adjustment_form.errors.pop("department", None)
                    adjustment_form.add_error("kind", exc.error_dict["kind"])
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
                        if field == "department":
                            field = "kind"
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
        summary = _cutoff_rows(request, publication)
    detail = _detail_context(
        request=request, publication=publication, faculty=faculty,
        editable_departments=editable_departments, departments=departments,
        adjustment_form=adjustment_form, early_form=early_form, final_form=final_form,
        mixed_form=mixed_form, edit_item=edit_item, early_item=early_item, mixed_item=mixed_item,
        removal_form=removal_form, remove_item=remove_item, page_notice=page_notice,
        pending_publication=pending_publication,
    )
    context = {
        "publications": publications, "publication": publication, "summary": summary,
        "cutoffs": list(cutoffs.values()), "selected_cutoff": _cutoff_key(publication),
        **detail,
        "can_summary_print": bool(summary) and all(_allowed(request, row["publication"] or publication, DTR_PRINT_PERMISSION,
                                      row["slice"].departments)
            and (not row["final"] or _allowed(request, row["final"].publication,
                DTR_PRINT_PERMISSION, final_dtr_departments(row["final"]))) for row in summary),
        "ac_departments": [item for item in AdminScopeService.scoped_departments(request).filter(
            tenant_id=tenant_id, campus_id=campus_id,
        ) if _faculty_is_ac(faculty=request.user, publication=publication, department_id=item.pk)
           and _allowed(request, publication, DTR_AC_SUMMARY_PERMISSION, {item.pk})
           and _allowed(request, publication, DTR_PRINT_PERMISSION, {item.pk})],
        "tenant_name": publication.tenant.name, "campus_name": publication.campus.name,
    }
    if admin_hours_formset is not None:
        context["admin_hours_formset"] = admin_hours_formset
    if _is_ajax(request) and (
        request.GET.get("partial") in {"faculty", "selection"}
        or request.POST.get("action") in {"adjustment", "admin_hours", "remove_adjustment", "finalize"}
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
    publications = _publications(tenant_id, campus_id)
    if request.GET.get("cutoff"):
        publication = next((p for p in publications if _cutoff_key(p) == request.GET["cutoff"]), None)
        if publication is None:
            raise Http404("Unknown or unauthorized cutoff.")
    else:
        publication = _selected_publication(request, tenant_id, campus_id, publications)
    if publication is None:
        raise Http404("No published cutoff.")
    try:
        rows = _cutoff_rows(request, publication)
        if not rows:
            raise PermissionDenied("No authorized faculty workload is available to print.")
        for row in rows:
            if not row["publication"] or not row["preview"]:
                _require(request.user, DTR_PRINT_PERMISSION, publication, row["slice"].departments)
                row["pending_publication"] = True
                continue
            _require(request.user, DTR_PRINT_PERMISSION, row["publication"],
                _faculty_departments(row["publication"], row["faculty"]))
            if row["final"]:
                _require(request.user, DTR_PRINT_PERMISSION, row["final"].publication,
                    final_dtr_departments(row["final"]))
            row["print_snapshot"] = printable_final_snapshot(row["final"].snapshot) if row["final"] else row["preview"].snapshot
            row["print_has_pending_changes"] = bool(row["final"] and (
                not row["preview"].ready or row["final"].review_fingerprint != row["preview"].fingerprint))
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
    return render(request, "faculty_attendance/dtr_print.html", _final_print_context(final))


def _final_print_context(final):
    # Enrich only from this selected final's immutable publication, never the
    # latest publication or current course/section/roster/schedule records.
    entries = final.publication.entries.filter(faculty_user_id=final.faculty_user_id).values(
        "meeting_id", "meeting_snapshot")
    return {"final": final, "snapshot": printable_final_snapshot(final.snapshot),
        "matrix": build_dtr_matrix(final.snapshot, published_entries=entries)}


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
    return render(request, "faculty_attendance/dtr_print.html", _final_print_context(final))
