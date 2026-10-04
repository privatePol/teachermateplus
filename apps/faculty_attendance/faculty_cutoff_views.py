"""Separate publication and DTR processing for complete faculty slices."""
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.admin_portal.services import AdminScopeService
from apps.core.decorators import portal_required

from .dtr import _finalize_dtr, latest_publication, preview_dtr
from .faculty_cutoffs import publish_faculty_cutoffs, review_faculty_cutoff
from .forms import CutoffScopeForm, FacultyCutoffActionForm
from .notice_locking import lock_notice_campus
from .permissions import DTR_FINALIZE_PERMISSION, DTR_VIEW_PERMISSION, ENCODE_PERMISSION, RECONCILE_PERMISSION, require_attendance_permission


def _publication(row, scope, *, lock=False):
    return latest_publication(tenant_id=scope["tenant_id"], campus_id=scope["campus_id"],
        academic_year_id=scope["academic_year"].pk, term_id=scope["term"].pk,
        start_date=scope["start_date"], end_date=scope["end_date"], faculty_id=row.faculty.pk, lock=lock)


def _allowed(actor, permission, row, scope):
    try:
        for department_id in row.departments:
            require_attendance_permission(user=actor, permission_code=permission,
                tenant_id=scope["tenant_id"], campus_id=scope["campus_id"], department_id=department_id)
    except PermissionDenied:
        return False
    return bool(row.departments)


def processing_rows(*, actor, review):
    # The source scan is shared by every DTR preview. No per-faculty campus scan.
    from .models import AttendanceCutoffPublication, FacultyDTR
    publications = list(AttendanceCutoffPublication.objects.filter(**review.scope)
        .select_related("faculty_scope").order_by("-published_at", "-pk"))
    finals = list(FacultyDTR.objects.filter(**review.scope).order_by("-revision", "-pk"))
    rows = []
    for row in review.slices:
        publication = next((p for p in publications if p.faculty_scope_id in (None, row.faculty.pk)), None)
        final = next((f for f in finals if f.faculty_user_id == row.faculty.pk), None)
        preview = None
        status = "Pending with blockers" if row.blockers else "Ready to publish" if row.requires_dtr else "No DTR required"
        if publication and _allowed(actor, DTR_VIEW_PERMISSION, row, review.scope):
            try:
                preview = preview_dtr(actor=actor, publication=publication, faculty=row.faculty, source_slice=row)
            except ValidationError:
                status = "Published; record/review AC office hours" if row.requires_dtr else "No DTR required"
            if preview:
                if final and final.review_fingerprint == preview.fingerprint and preview.ready:
                    status = "Finalized / current"
                elif final:
                    status = "Finalized needing revision"
                elif preview.ready:
                    status = "Published; faculty review before finalization"
                else:
                    status = "Published; DTR blockers"
        if row.blockers:
            status = "Finalized needing revision" if final else "Pending with blockers"
        warnings = []
        for blocker in row.blockers:
            departments = set(blocker.details.get("departments", []))
            recovery_url = ""
            if departments and departments <= row.departments and _allowed(actor, RECONCILE_PERMISSION, row, review.scope):
                recovery_url = reverse("faculty_attendance:reconciliation")
            daily_url = ""
            if blocker.details.get("date") and departments and _allowed(actor, ENCODE_PERMISSION, row, review.scope):
                daily_url = reverse("faculty_attendance:daily_encoding") + "?" + urlencode({
                    "academic_year": review.scope["academic_year"].pk, "term": review.scope["term"].pk,
                    "meeting_date": blocker.details["date"].isoformat()})
            warnings.append({"blocker": blocker, "recovery_url": recovery_url, "daily_url": daily_url})
        rows.append({"slice": row, "publication": publication, "preview": preview, "final": final, "status": status, "warnings": warnings,
            "can_review": bool(publication and _allowed(actor, DTR_VIEW_PERMISSION, row, review.scope)),
            "can_publish": row.ready and (not publication or publication.review_fingerprint != row.fingerprint),
            "can_finalize": bool(row.ready and publication and preview and preview.ready and _allowed(actor, DTR_FINALIZE_PERMISSION, row, review.scope)
                and (not final or final.review_fingerprint != preview.fingerprint))})
    return rows


@transaction.atomic
def finalize_ready_faculty(*, actor, review_scope, faculty_ids, expected_fingerprints, faculty_review_complete, reason=""):
    if not faculty_review_complete:
        raise ValidationError("Confirm that published attendance was available for faculty review before finalizing.")
    lock_notice_campus(review_scope["campus_id"])
    source = review_faculty_cutoff(actor=actor, **review_scope, lock=True, permission_code=DTR_FINALIZE_PERMISSION)
    selected = sorted(set(int(pk) for pk in faculty_ids))
    if not selected:
        raise ValidationError("Select published faculty DTRs to finalize.")
    plans = []
    for faculty_id in selected:
        row = next((r for r in source.slices if r.faculty.pk == faculty_id), None)
        if row is None:
            raise PermissionDenied("Selected faculty is outside your authorized cutoff.")
        publication = _publication(row, source.scope, lock=True)
        if publication is None:
            raise ValidationError("Publish selected faculty attendance and allow faculty review first.")
        # Complete-campus finals retain their established validation path.
        plans.append((row, publication, expected_fingerprints.get(str(faculty_id))))
    return [_finalize_dtr(actor=actor, publication=p, faculty=row.faculty, expected_fingerprint=fingerprint,
        reason=reason, faculty_review_complete=True, source_slice=row) for row, p, fingerprint in plans]


@portal_required("ADMIN")
def faculty_cutoff_review_view(request):
    scope = getattr(request, "scope", {})
    tenant_id, campus_id = scope.get("tenant_id"), scope.get("campus_id")
    if not tenant_id or not campus_id:
        return HttpResponseForbidden("Select a tenant and campus first.")
    from .faculty_cutoffs import _departments
    from .permissions import PUBLISH_FACULTY_PERMISSION
    try:
        _departments(request.user, tenant_id, campus_id, PUBLISH_FACULTY_PERMISSION)
    except PermissionDenied:
        return HttpResponseForbidden("Faculty cutoff publication authority is required.")
    form = CutoffScopeForm(request.POST if request.method == "POST" else request.GET or None,
        academic_year_queryset=AdminScopeService.active_scoped_academic_years(request).filter(tenant_id=tenant_id),
        term_queryset=AdminScopeService.active_scoped_terms(request).filter(tenant_id=tenant_id))
    context = {"form": form, "rows": [], "review": None}
    if form.is_valid():
        values = form.cleaned_data
        review_scope = dict(tenant_id=tenant_id, campus_id=campus_id,
            department_ids=set(AdminScopeService.scoped_departments(request).filter(
                tenant_id=tenant_id, campus_id=campus_id).values_list("pk", flat=True)), **values)
        try:
            review = review_faculty_cutoff(actor=request.user, **review_scope)
            rows = processing_rows(actor=request.user, review=review)
            publication_choices = [(r["slice"].faculty.pk, r["slice"].faculty.full_name) for r in rows if r["slice"].ready]
            final_choices = [(r["slice"].faculty.pk, r["slice"].faculty.full_name) for r in rows if r["preview"] and r["preview"].ready]
            publication_initial = {"submission_key": str(uuid4()), "expected_fingerprints": {
                str(r["slice"].faculty.pk): r["slice"].fingerprint for r in rows if r["slice"].ready}}
            final_initial = {"submission_key": str(uuid4()), "expected_fingerprints": {
                str(r["slice"].faculty.pk): r["preview"].fingerprint for r in rows if r["preview"] and r["preview"].ready}}
            publication_form = FacultyCutoffActionForm(faculty_choices=publication_choices, initial=publication_initial, auto_id="publication-%s")
            final_form = FacultyCutoffActionForm(faculty_choices=final_choices, initial=final_initial, auto_id="finalize-%s")
            if request.method == "POST":
                action = request.POST.get("action")
                finalizing = action in ("finalize_selected", "finalize_ready")
                action_form = FacultyCutoffActionForm(request.POST, faculty_choices=final_choices if finalizing else publication_choices)
                action_form.auto_id = "finalize-%s" if finalizing else "publication-%s"
                if finalizing:
                    final_form = action_form
                else:
                    publication_form = action_form
                if action not in ("publish_selected", "publish_ready", "finalize_selected", "finalize_ready"):
                    action_form.add_error(None, "Choose a supported faculty cutoff action.")
                elif action_form.is_valid():
                    selected = action_form.cleaned_data["faculty_ids"]
                    if action.endswith("_ready"):
                        flag = "can_finalize" if finalizing else "can_publish"
                        selected = [r["slice"].faculty.pk for r in rows if r[flag]]
                    try:
                        if finalizing:
                            finals = finalize_ready_faculty(actor=request.user, review_scope=review_scope, faculty_ids=selected,
                                expected_fingerprints=action_form.cleaned_data["expected_fingerprints"],
                                faculty_review_complete=action_form.cleaned_data["faculty_review_complete"], reason=action_form.cleaned_data["reason"])
                            messages.success(request, f"{len(finals)} faculty DTR(s) finalized after published attendance review.")
                        else:
                            publications = publish_faculty_cutoffs(actor=request.user, **review_scope, faculty_ids=selected,
                                expected_fingerprints=action_form.cleaned_data["expected_fingerprints"],
                                submission_key=action_form.cleaned_data["submission_key"], publication_reason=action_form.cleaned_data["reason"])
                            messages.success(request, f"{len(publications)} faculty cutoff(s) published. Allow faculty review, then finalize eligible DTRs separately.")
                    except ValidationError as exc:
                        action_form.add_error(None, exc)
                    else:
                        query = {k: v.pk if hasattr(v, "pk") else v.isoformat() for k, v in values.items()}
                        return redirect(f"{reverse('faculty_attendance:faculty_cutoff_review')}?{urlencode(query)}")
            context.update(review=review, rows=rows, publication_form=publication_form, final_form=final_form)
            diagnostics = []
            from types import SimpleNamespace
            for blocker in review.unattributed:
                diagnostic_scope = SimpleNamespace(departments=set(blocker.details.get("departments", [])))
                recovery_url = reverse("faculty_attendance:reconciliation") if _allowed(
                    request.user, RECONCILE_PERMISSION, diagnostic_scope, review.scope) else ""
                diagnostics.append({"blocker": blocker, "recovery_url": recovery_url})
            context["diagnostics"] = diagnostics
        except PermissionDenied:
            return HttpResponseForbidden("Faculty cutoff action is outside your authorized scope.")
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, "faculty_attendance/faculty_cutoff_review.html", context)
