from __future__ import annotations

from django.contrib import messages
from django import forms
from django.conf import settings
from django.core.mail import send_mail
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.decorators import portal_required
from apps.core.services.audit import AuditService
from apps.core.services.permissions import PermissionService
from apps.imports.forms import ImportUploadForm
from apps.imports.models import ImportBatch, ImportBatchRow
from apps.imports.services import BulkImportService, ImportTemplateService
from apps.tenants.models import Campus, Tenant

from .services import AdminScopeService
from .views import _get_page, _scope_context, _style_form


class EmailDiagnosticsForm(forms.Form):
    to_email = forms.EmailField(
        required=False,
        label="Send test email to",
        help_text="Leave blank to use your account email; fallback uses DEFAULT_FROM_EMAIL.",
    )
    subject = forms.CharField(
        required=True,
        max_length=160,
        initial="EduGrade+ SMTP Diagnostic",
    )
    message = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 5}),
        initial=(
            "This is a test email from EduGrade+.\n\n"
            "If you received this message, SMTP settings are working."
        ),
    )


def _batch_error_tips(batch: ImportBatch):
    messages_list = []
    summary = batch.error_summary_json or {}
    for row in summary.get("top_errors", []) or []:
        if isinstance(row, dict):
            messages_list.append(str(row.get("message", "")))
    for item in summary.get("messages", []) or []:
        messages_list.append(str(item))

    tips = []
    all_text = " ".join(messages_list).lower()
    if "academic_year_code" in all_text and "not found" in all_text:
        tips.append("Use Academic Year CODE from Academic Years (example: `AY2526`), not just display label.")
    if "term_code" in all_text and "not found" in all_text:
        tips.append("Use Term CODE from Terms (example: `1ST`, `2ND`).")
    if "course_code" in all_text and "not found" in all_text:
        tips.append("Ensure each course_code already exists in Course Master for the same tenant.")
    if "section_code" in all_text and "not found" in all_text:
        tips.append("Import sections first, then use exact section_code values from Sections table.")
    if "program_code" in all_text and "required" in all_text:
        tips.append("If section codes are reused across programs, include program_code to remove ambiguity.")
    if "student_no" in all_text and "not found" in all_text:
        tips.append(
            "Missing students are rejected in STRICT_EXISTING mode. "
            "Set ENROLLMENT_STUDENT_MODE to AUTO_CREATE for the CSV tenant to auto-create missing students during import."
        )
    if "enrollment_student_mode is strict_existing" in all_text:
        tips.append(
            "The CSV tenant uses STRICT_EXISTING. Switch ENROLLMENT_STUDENT_MODE to AUTO_CREATE "
            "if you want enrollment import to create missing students."
        )
    return tips


def _enrollment_student_mode_references(batch: ImportBatch):
    if batch.import_type != ImportBatch.ImportType.ENROLLMENT:
        return []

    seen = set()
    references = []
    for raw_data in batch.rows.order_by("row_number").values_list("raw_data_json", flat=True):
        if not isinstance(raw_data, dict):
            continue
        tenant_code = str(raw_data.get("tenant_code") or "").strip()
        campus_code = str(raw_data.get("campus_code") or "").strip()
        key = (tenant_code.upper(), campus_code.upper())
        if not tenant_code or key in seen:
            continue
        seen.add(key)

        tenant = Tenant.objects.filter(code__iexact=tenant_code).first()
        campus = None
        if tenant and campus_code:
            campus = Campus.objects.filter(tenant=tenant, code__iexact=campus_code).first()
        references.append(
            {
                "tenant_code": tenant_code,
                "campus_code": campus_code,
                "tenant_found": bool(tenant),
                "campus_found": bool(campus) if campus_code else None,
                "student_mode": (
                    BulkImportService.get_enrollment_student_mode(tenant.id)
                    if tenant
                    else None
                ),
            }
        )
    return references


def _scope_ids(request):
    scope = getattr(request, "scope", {})
    return scope.get("tenant_id"), scope.get("campus_id")


def _require_permission(request, permission_code: str):
    tenant_id, campus_id = _scope_ids(request)
    return PermissionService.has_permission(
        request.user,
        permission_code,
        tenant_id=tenant_id,
        campus_id=campus_id,
    )


def _require_import_read(request):
    if not _require_permission(request, "import_batches.read"):
        raise PermissionError("You do not have permission to view import batches.")


def _resolve_import_type(import_slug: str) -> str:
    import_type = BulkImportService.slug_to_import_type(import_slug)
    if not import_type:
        raise Http404("Unsupported import type.")
    return import_type


@portal_required("ADMIN")
def import_batch_list_view(request):
    try:
        _require_import_read(request)
    except PermissionError:
        return HttpResponseForbidden("You do not have permission to view import batches.")

    queryset = AdminScopeService.scoped_import_batches(request)
    import_type = request.GET.get("import_type", "").strip()
    status = request.GET.get("status", "").strip()
    q = request.GET.get("q", "").strip()

    if import_type:
        queryset = queryset.filter(import_type=import_type)
    if status:
        queryset = queryset.filter(status=status)
    if q:
        queryset = queryset.filter(
            Q(original_filename__icontains=q) | Q(uploaded_by_user__username__icontains=q)
        )

    import_type_labels = dict(ImportBatch.ImportType.choices)
    import_cards = []
    for code in BulkImportService.list_import_types():
        permission_code = BulkImportService.required_permission(code)
        if _require_permission(request, permission_code):
            import_cards.append(
                {
                    "import_type": code,
                    "label": import_type_labels.get(code, code),
                    "slug": BulkImportService.import_type_to_slug(code),
                    "permission_code": permission_code,
                }
            )

    context = {
        "page_obj": _get_page(request, queryset),
        "import_type": import_type,
        "status": status,
        "q": q,
        "import_cards": import_cards,
        "import_type_choices": ImportBatch.ImportType.choices,
        "status_choices": ImportBatch.Status.choices,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/imports/import_batch_list.html", context)


@portal_required("ADMIN")
def import_template_download_view(request, import_slug: str):
    import_type = _resolve_import_type(import_slug)
    required_permission = BulkImportService.required_permission(import_type)
    if not _require_permission(request, required_permission):
        return HttpResponseForbidden("You do not have permission to download this template.")

    response = ImportTemplateService.generate_csv_response(import_type)
    AuditService.log_event(
        action="DOWNLOAD_TEMPLATE",
        portal="ADMIN",
        entity_type="ImportTemplate",
        entity_id=import_type,
        actor=request.user,
        metadata={"import_type": import_type},
        request=request,
    )
    return response


@portal_required("ADMIN")
def import_upload_view(request, import_slug: str):
    import_type = _resolve_import_type(import_slug)
    required_permission = BulkImportService.required_permission(import_type)
    if not _require_permission(request, required_permission):
        return HttpResponseForbidden("You do not have permission to import this module.")
    try:
        _require_import_read(request)
    except PermissionError:
        return HttpResponseForbidden("You do not have permission to view import batches.")

    form = ImportUploadForm(request.POST or None, request.FILES or None)
    _style_form(form)
    if request.method == "POST" and form.is_valid():
        batch = BulkImportService.validate_and_stage_upload(
            import_type=import_type,
            uploaded_file=form.cleaned_data["csv_file"],
            user=request.user,
            request=request,
        )
        AuditService.log_event(
            action="IMPORT_UPLOAD",
            portal="ADMIN",
            entity_type="ImportBatch",
            entity_id=batch.id,
            actor=request.user,
            tenant=batch.tenant,
            campus=batch.campus,
            after_data={
                "import_type": batch.import_type,
                "status": batch.status,
                "total_rows": batch.total_rows,
                "valid_rows": batch.valid_rows,
                "invalid_rows": batch.invalid_rows,
                "filename": batch.original_filename,
                "stored_filename": batch.source_file.name if batch.source_file else "",
                "content_type": (batch.metadata_json or {}).get("content_type", ""),
                "file_size_bytes": (batch.metadata_json or {}).get("file_size_bytes", 0),
            },
            request=request,
        )
        if batch.status == ImportBatch.Status.VALIDATION_FAILED:
            messages.error(request, "Upload failed template validation. Please review batch details.")
        elif batch.invalid_rows:
            messages.warning(
                request,
                f"Upload validated with row errors. {batch.valid_rows} valid / {batch.invalid_rows} invalid.",
            )
        else:
            messages.success(request, f"Upload validated successfully with {batch.valid_rows} rows.")
        return redirect("admin_portal:import_batch_detail", batch_id=batch.id)

    template_meta = ImportTemplateService.get_template_config(import_type)
    context = {
        "title": f"Bulk Import: {dict(ImportBatch.ImportType.choices).get(import_type, import_type)}",
        "form": form,
        "form_enctype": "multipart/form-data",
        "import_type": import_type,
        "import_slug": import_slug,
        "template_headers": template_meta["headers"],
        "template_sample": template_meta["sample_row"],
        "import_guide": template_meta.get("guide", {}),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/imports/import_upload.html", context)


@portal_required("ADMIN")
def import_batch_detail_view(request, batch_id: int):
    try:
        _require_import_read(request)
    except PermissionError:
        return HttpResponseForbidden("You do not have permission to view import batches.")

    batch = get_object_or_404(AdminScopeService.scoped_import_batches(request), id=batch_id)
    rows_qs = batch.rows.order_by("row_number")
    page_obj = _get_page(request, rows_qs, per_page=30)
    required_permission = BulkImportService.required_permission(batch.import_type)
    can_confirm = (
        _require_permission(request, required_permission)
        and batch.status in {ImportBatch.Status.VALIDATED, ImportBatch.Status.CONFIRM_FAILED}
        and rows_qs.filter(row_status=ImportBatchRow.RowStatus.VALID).exists()
    )

    context = {
        "batch": batch,
        "page_obj": page_obj,
        "can_confirm": can_confirm,
        "import_slug": BulkImportService.import_type_to_slug(batch.import_type),
        "import_guide": ImportTemplateService.get_template_config(batch.import_type).get("guide", {}),
        "error_tips": _batch_error_tips(batch),
        "enrollment_student_mode_references": _enrollment_student_mode_references(batch),
        "status_badge_class": {
            ImportBatch.Status.VALIDATED: "text-bg-primary",
            ImportBatch.Status.VALIDATION_FAILED: "text-bg-danger",
            ImportBatch.Status.CONFIRMED: "text-bg-success",
            ImportBatch.Status.CONFIRM_FAILED: "text-bg-warning",
        }.get(batch.status, "text-bg-secondary"),
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/imports/import_batch_detail.html", context)


@portal_required("ADMIN")
def import_batch_confirm_view(request, batch_id: int):
    if request.method != "POST":
        return HttpResponseForbidden("Invalid method.")

    try:
        _require_import_read(request)
    except PermissionError:
        return HttpResponseForbidden("You do not have permission to view import batches.")

    batch = get_object_or_404(AdminScopeService.scoped_import_batches(request), id=batch_id)
    required_permission = BulkImportService.required_permission(batch.import_type)
    if not _require_permission(request, required_permission):
        return HttpResponseForbidden("You do not have permission to confirm this import.")

    before = {
        "status": batch.status,
        "imported_rows": batch.imported_rows,
        "valid_rows": batch.valid_rows,
        "invalid_rows": batch.invalid_rows,
    }
    try:
        updated = BulkImportService.confirm_batch(batch=batch, actor=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("admin_portal:import_batch_detail", batch_id=batch.id)

    AuditService.log_event(
        action="IMPORT_CONFIRM",
        portal="ADMIN",
        entity_type="ImportBatch",
        entity_id=updated.id,
        actor=request.user,
        tenant=updated.tenant,
        campus=updated.campus,
        before_data=before,
        after_data={
            "status": updated.status,
            "imported_rows": updated.imported_rows,
            "valid_rows": updated.valid_rows,
            "invalid_rows": updated.invalid_rows,
        },
        request=request,
    )
    if updated.status == ImportBatch.Status.CONFIRMED:
        messages.success(request, f"Import confirmed. {updated.imported_rows} rows imported.")
    else:
        messages.warning(
            request,
            f"Import finished with errors. Imported: {updated.imported_rows}, remaining invalid: {updated.invalid_rows}.",
        )
    return redirect("admin_portal:import_batch_detail", batch_id=updated.id)


@portal_required("ADMIN")
def email_diagnostics_view(request):
    try:
        _require_import_read(request)
    except PermissionError:
        return HttpResponseForbidden("You do not have permission to access email diagnostics.")

    form = EmailDiagnosticsForm(request.POST or None)
    _style_form(form)

    smtp_meta = {
        "EMAIL_BACKEND": settings.EMAIL_BACKEND,
        "EMAIL_HOST": settings.EMAIL_HOST,
        "EMAIL_PORT": settings.EMAIL_PORT,
        "EMAIL_USE_TLS": settings.EMAIL_USE_TLS,
        "EMAIL_USE_SSL": settings.EMAIL_USE_SSL,
        "EMAIL_HOST_USER": settings.EMAIL_HOST_USER or "(empty)",
        "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
        "EMAIL_TIMEOUT": settings.EMAIL_TIMEOUT,
    }

    last_result = None
    if request.method == "POST" and form.is_valid():
        recipient = (
            form.cleaned_data["to_email"]
            or getattr(request.user, "email", "").strip()
            or settings.DEFAULT_FROM_EMAIL
        )
        if not recipient:
            form.add_error("to_email", "Provide a recipient email or set your account email.")
        else:
            try:
                sent_count = send_mail(
                    subject=form.cleaned_data["subject"],
                    message=form.cleaned_data["message"],
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient],
                    fail_silently=False,
                )
                last_result = {
                    "ok": True,
                    "recipient": recipient,
                    "sent_count": sent_count,
                }
                messages.success(
                    request,
                    f"Test email sent to {recipient}. Sent count: {sent_count}.",
                )
                AuditService.log_event(
                    action="EMAIL_DIAGNOSTIC_SUCCESS",
                    portal="ADMIN",
                    entity_type="SystemEmail",
                    entity_id="smtp_test",
                    actor=request.user,
                    metadata={
                        "recipient": recipient,
                        "sent_count": sent_count,
                        "email_backend": settings.EMAIL_BACKEND,
                        "email_host": settings.EMAIL_HOST,
                        "email_port": settings.EMAIL_PORT,
                        "email_use_tls": settings.EMAIL_USE_TLS,
                        "email_use_ssl": settings.EMAIL_USE_SSL,
                    },
                    request=request,
                )
            except Exception as exc:
                error_text = str(exc)
                last_result = {
                    "ok": False,
                    "recipient": recipient,
                    "error": error_text,
                }
                messages.error(request, f"Email test failed: {error_text}")
                AuditService.log_event(
                    action="EMAIL_DIAGNOSTIC_FAILED",
                    portal="ADMIN",
                    entity_type="SystemEmail",
                    entity_id="smtp_test",
                    actor=request.user,
                    metadata={
                        "recipient": recipient,
                        "error": error_text[:800],
                        "email_backend": settings.EMAIL_BACKEND,
                        "email_host": settings.EMAIL_HOST,
                        "email_port": settings.EMAIL_PORT,
                        "email_use_tls": settings.EMAIL_USE_TLS,
                        "email_use_ssl": settings.EMAIL_USE_SSL,
                    },
                    request=request,
                )

    context = {
        "title": "Email Diagnostics",
        "form": form,
        "smtp_meta": smtp_meta,
        "last_result": last_result,
    }
    context.update(_scope_context(request))
    return render(request, "admin_portal/tools/email_diagnostics.html", context)
