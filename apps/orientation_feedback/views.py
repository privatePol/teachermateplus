from __future__ import annotations

import csv
import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing

from apps.core.decorators import permission_required, portal_required
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.orientation_feedback.forms import (
    OrientationCancellationForm,
    OrientationEmailValidationForm,
    OrientationEmailOtpForm,
    OrientationQuestionFormSet,
    OrientationResponseForm,
    OrientationSurveySessionForm,
)
from apps.orientation_feedback.models import OrientationSurveySession
from apps.orientation_feedback.services import (
    OrientationSurveyAnalyticsService,
    OrientationSurveyBrowserService,
    OrientationSurveyDuplicateResponse,
    OrientationSurveyEmailDeliveryError,
    OrientationSurveyPublicService,
    OrientationSurveyRateLimited,
    OrientationSurveyRateLimitService,
    OrientationSurveyResponseService,
    OrientationSurveySessionService,
)
from apps.tenants.models import Campus, Tenant


logger = logging.getLogger(__name__)


def _scope_ids(request):
    scope = getattr(request, "scope", {})
    return scope.get("tenant_id"), scope.get("campus_id")


def _active_scope(request):
    tenant_id, campus_id = _scope_ids(request)
    if tenant_id is None or campus_id is None:
        raise PermissionDenied("An active tenant and campus scope is required.")
    tenant = get_object_or_404(Tenant, pk=tenant_id, is_active=True)
    campus = get_object_or_404(Campus, pk=campus_id, tenant=tenant, is_active=True)
    return tenant, campus


def _require_feature(tenant_id):
    if not FeatureSettingsService.is_orientation_feedback_enabled(tenant_id=tenant_id, default=True):
        raise PermissionDenied("Orientation Feedback is disabled for this tenant.")


def _owned_sessions(request):
    tenant_id, campus_id = _scope_ids(request)
    if tenant_id is None or campus_id is None:
        return OrientationSurveySession.objects.none()
    return OrientationSurveySession.objects.filter(
        tenant_id=tenant_id,
        campus_id=campus_id,
    ).select_related(
        "tenant",
        "campus",
        "academic_year",
        "term",
        "created_by",
        "started_by",
        "closed_by",
        "cancelled_by",
    )


def _owned_session(request, public_id, *, allow_disabled_historical=False):
    session = get_object_or_404(_owned_sessions(request), public_id=public_id)
    session = OrientationSurveySessionService.refresh_auto_close(session)
    feature_enabled = FeatureSettingsService.is_orientation_feedback_enabled(
        tenant_id=session.tenant_id,
        default=True,
    )
    historical = session.status in {
        OrientationSurveySession.Status.CLOSED,
        OrientationSurveySession.Status.CANCELLED,
    }
    if not feature_enabled and not (allow_disabled_historical and historical):
        raise PermissionDenied("Orientation Feedback is disabled for this tenant.")
    return session


def _public_url(request, session):
    entry = request.build_absolute_uri(reverse("orientation_feedback:public_entry"))
    return f"{entry}#{session.public_token}"


def _can(request, permission_code):
    tenant_id, campus_id = _scope_ids(request)
    return PermissionService.has_permission(
        request.user,
        permission_code,
        tenant_id=tenant_id,
        campus_id=campus_id,
    )


@require_GET
@portal_required("ADMIN")
@permission_required("orientation_feedback.view")
def session_list_view(request):
    tenant, _ = _active_scope(request)
    _require_feature(tenant.id)
    sessions = list(_owned_sessions(request).prefetch_related("eligible_head_roles"))
    for session in sessions:
        OrientationSurveySessionService.refresh_auto_close(session)
    return render(
        request,
        "orientation_feedback/session_list.html",
        {
            "sessions": sessions,
            "can_manage": _can(request, "orientation_feedback.manage"),
        },
    )


@portal_required("ADMIN")
@permission_required("orientation_feedback.manage")
def session_create_view(request):
    tenant, campus = _active_scope(request)
    _require_feature(tenant.id)
    form = OrientationSurveySessionForm(
        request.POST or None,
        tenant_id=tenant.id,
        campus_id=campus.id,
    )
    if request.method == "POST" and form.is_valid():
        session = OrientationSurveySessionService.create_draft(
            form=form,
            user=request.user,
            tenant=tenant,
            campus=campus,
            request=request,
        )
        messages.success(request, "Orientation feedback survey draft created.")
        return redirect("orientation_feedback:session_questions", public_id=session.public_id)
    return render(
        request,
        "orientation_feedback/session_form.html",
        {"form": form, "page_title": "Create Orientation Feedback Survey", "session": None},
    )


@portal_required("ADMIN")
@permission_required("orientation_feedback.manage")
def session_edit_view(request, public_id):
    session = _owned_session(request, public_id)
    if session.status != OrientationSurveySession.Status.DRAFT:
        messages.error(request, "Published survey settings cannot be edited.")
        return redirect("orientation_feedback:facilitator", public_id=session.public_id)
    form = OrientationSurveySessionForm(
        request.POST or None,
        instance=session,
        tenant_id=session.tenant_id,
        campus_id=session.campus_id,
    )
    if request.method == "POST" and form.is_valid():
        session = OrientationSurveySessionService.update_draft(
            session=session,
            form=form,
            user=request.user,
            request=request,
        )
        messages.success(request, "Survey settings updated.")
        return redirect("orientation_feedback:facilitator", public_id=session.public_id)
    return render(
        request,
        "orientation_feedback/session_form.html",
        {"form": form, "page_title": "Edit Orientation Feedback Survey", "session": session},
    )


@portal_required("ADMIN")
@permission_required("orientation_feedback.manage")
def session_questions_view(request, public_id):
    session = _owned_session(request, public_id)
    questions = session.questions.prefetch_related("choices").order_by("display_order")
    formset = OrientationQuestionFormSet(request.POST or None, queryset=questions)
    if request.method == "POST":
        if session.status != OrientationSurveySession.Status.DRAFT:
            messages.error(request, "Published survey questions cannot be edited.")
            return redirect("orientation_feedback:facilitator", public_id=session.public_id)
        if formset.is_valid():
            OrientationSurveySessionService.update_questions(
                session=session,
                formset=formset,
                user=request.user,
                request=request,
            )
            messages.success(request, "Draft question wording updated.")
            return redirect("orientation_feedback:session_questions", public_id=session.public_id)
    question_forms = []
    for question, question_form in zip(questions, formset.forms):
        question_forms.append({"question": question, "form": question_form})
    return render(
        request,
        "orientation_feedback/session_questions.html",
        {"session": session, "formset": formset, "question_forms": question_forms},
    )


@require_GET
@portal_required("ADMIN")
@permission_required("orientation_feedback.view")
def facilitator_view(request, public_id):
    session = _owned_session(request, public_id)
    return render(
        request,
        "orientation_feedback/facilitator.html",
        {
            "session": session,
            "public_url": _public_url(request, session),
            "completed_count": session.responses.count(),
            "cancel_form": OrientationCancellationForm(),
            "can_manage": _can(request, "orientation_feedback.manage"),
            "can_start": _can(request, "orientation_feedback.start"),
            "can_close": _can(request, "orientation_feedback.close"),
            "can_cancel": _can(request, "orientation_feedback.cancel"),
            "can_view_analytics": _can(request, "orientation_feedback.view_analytics"),
        },
    )


@require_GET
@portal_required("ADMIN")
@permission_required("orientation_feedback.view")
def facilitator_status_view(request, public_id):
    session = _owned_session(request, public_id)
    completed = session.responses.count()
    eligible = session.eligible_count_snapshot
    response_rate = round((completed * 100 / eligible), 1) if eligible else None
    return JsonResponse(
        {
            "status": session.status,
            "status_label": session.get_status_display(),
            "completed": completed,
            "eligible": eligible,
            "response_rate": response_rate,
        }
    )


@require_GET
@portal_required("ADMIN")
@permission_required("orientation_feedback.view")
def qr_view(request, public_id):
    session = _owned_session(request, public_id)
    qr = QrCodeWidget(_public_url(request, session))
    bounds = qr.getBounds()
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    size = 720 if request.GET.get("full") == "1" else 300
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(qr)
    response = HttpResponse(renderSVG.drawToString(drawing), content_type="image/svg+xml")
    response["Cache-Control"] = "private, no-store"
    if request.GET.get("download") == "1":
        response["Content-Disposition"] = f'attachment; filename="orientation-feedback-{session.public_id}.svg"'
    return response


@require_POST
@portal_required("ADMIN")
@permission_required("orientation_feedback.start")
def start_view(request, public_id):
    session = _owned_session(request, public_id)
    try:
        OrientationSurveySessionService.start(session=session, user=request.user, request=request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Survey started. The public link is now accepting eligible respondents.")
    return redirect("orientation_feedback:facilitator", public_id=session.public_id)


@require_POST
@portal_required("ADMIN")
@permission_required("orientation_feedback.close")
def close_view(request, public_id):
    session = _owned_session(request, public_id)
    try:
        OrientationSurveySessionService.close(session=session, user=request.user, request=request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Survey ended. New validations and submissions are blocked.")
    return redirect("orientation_feedback:facilitator", public_id=session.public_id)


@require_POST
@portal_required("ADMIN")
@permission_required("orientation_feedback.cancel")
def cancel_view(request, public_id):
    session = _owned_session(request, public_id)
    form = OrientationCancellationForm(request.POST)
    if form.is_valid():
        try:
            OrientationSurveySessionService.cancel(
                session=session,
                user=request.user,
                reason=form.cleaned_data["reason"],
                request=request,
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Survey cancelled. Existing responses were preserved.")
    else:
        messages.error(request, "Enter a cancellation reason.")
    return redirect("orientation_feedback:facilitator", public_id=session.public_id)


@require_GET
@portal_required("ADMIN")
@permission_required("orientation_feedback.view_analytics")
def analytics_view(request, public_id):
    session = _owned_session(request, public_id, allow_disabled_historical=True)
    if session.status in {OrientationSurveySession.Status.DRAFT, OrientationSurveySession.Status.OPEN}:
        messages.info(request, "Aggregate analytics become available after the survey ends or is cancelled.")
        return redirect("orientation_feedback:facilitator", public_id=session.public_id)
    analytics = OrientationSurveyAnalyticsService.build(session)
    return render(
        request,
        "orientation_feedback/analytics.html",
        {
            "session": session,
            "analytics": analytics,
            "can_export": _can(request, "orientation_feedback.export"),
        },
    )


def _safe_csv_cell(value):
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


@require_GET
@portal_required("ADMIN")
@permission_required("orientation_feedback.export")
def export_view(request, public_id):
    session = _owned_session(request, public_id, allow_disabled_historical=True)
    if session.status in {OrientationSurveySession.Status.DRAFT, OrientationSurveySession.Status.OPEN}:
        raise PermissionDenied("Only ended or cancelled survey results may be exported.")
    analytics = OrientationSurveyAnalyticsService.build(session)
    if not analytics["results_released"]:
        raise PermissionDenied(
            f"Aggregate export requires at least {analytics['minimum_responses']} completed responses."
        )
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="orientation-feedback-{session.public_id}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Orientation Feedback Aggregate Export"])
    writer.writerow(["Survey", _safe_csv_cell(session.title)])
    writer.writerow(["Survey type", session.get_survey_type_display()])
    writer.writerow(["Status", session.get_status_display()])
    writer.writerow(["Tenant", _safe_csv_cell(session.tenant.name)])
    writer.writerow(["Campus", _safe_csv_cell(session.campus.name)])
    writer.writerow(["Eligible respondents", analytics["eligible"]])
    writer.writerow(["Completed responses", analytics["completed"]])
    writer.writerow(["Response rate", f'{analytics["response_rate"]}%' if analytics["response_rate"] is not None else "Unavailable"])
    writer.writerow([])
    writer.writerow(["Scale question", "Answered", "Unanswered", "Weighted mean", "Interpretation"])
    for row in analytics["scale_rows"]:
        writer.writerow(
            [
                _safe_csv_cell(row["question"].text),
                row["answered"],
                row["unanswered"],
                row["mean"] if row["mean"] is not None else "",
                row["interpretation"],
            ]
        )
        writer.writerow(["Choice", "Score", "Count", "Percent"])
        for distribution in row["distributions"]:
            writer.writerow(
                [
                    _safe_csv_cell(distribution["label"]),
                    distribution["score"],
                    distribution["count"],
                    f'{distribution["percent"]}%',
                ]
            )
    writer.writerow([])
    writer.writerow(["Guidance area", "Count", "Percent of completed respondents"])
    for row in analytics["checkbox_rows"]:
        for option in row["options"]:
            writer.writerow([_safe_csv_cell(option["label"]), option["count"], f'{option["percent"]}%'])
    writer.writerow([])
    writer.writerow(["Anonymous open feedback"])
    for comment in analytics["comments"]:
        writer.writerow([_safe_csv_cell(comment["question"]), _safe_csv_cell(comment["text"])])
    AuditService.log_event(
        action="ORIENTATION_SURVEY_EXPORT_GENERATED",
        portal="ADMIN",
        entity_type="OrientationSurveySession",
        entity_id=session.public_id,
        actor=request.user,
        tenant=session.tenant,
        campus=session.campus,
        metadata={"format": "CSV", "status": session.status},
        request=request,
    )
    return response


def _render_public(
    request,
    *,
    state,
    session=None,
    form=None,
    state_message="",
    public_token="",
    status=200,
    extra_context=None,
):
    context = {
        "survey_state": state,
        "session": session,
        "form": form,
        "state_message": state_message,
        "public_token": public_token,
        "privacy_notice": OrientationSurveyPublicService.PRIVACY_NOTICE,
    }
    context.update(extra_context or {})
    response = render(
        request,
        "orientation_feedback/public.html",
        context,
        status=status,
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Referrer-Policy"] = "same-origin"
    return response


@never_cache
@require_GET
def public_entry_view(request):
    return _render_public(request, state="entry")


@never_cache
@sensitive_post_parameters("public_token")
@require_POST
def public_open_view(request):
    public_token = request.POST.get("public_token", "")
    session = OrientationSurveyPublicService.resolve_session(public_token)
    state, message = OrientationSurveyPublicService.state(session)
    if state != "open":
        return _render_public(
            request,
            state=state,
            session=session,
            state_message=message,
            status=404 if state == "invalid" else 200,
        )
    _, signed_value = OrientationSurveyBrowserService.resolve_or_create(request)
    OrientationSurveyPublicService.clear_state(request)
    response = _render_public(
        request,
        state="validate",
        session=session,
        form=OrientationEmailValidationForm(),
        public_token=public_token,
    )
    OrientationSurveyBrowserService.set_cookie(response, signed_value)
    return response


@never_cache
@sensitive_post_parameters("public_token", "email")
@require_POST
def public_validate_view(request):
    public_token = request.POST.get("public_token", "")
    session = OrientationSurveyPublicService.resolve_session(public_token)
    state, message = OrientationSurveyPublicService.state(session)
    if state != "open":
        return _render_public(
            request,
            state=state,
            session=session,
            state_message=message,
            status=404 if state == "invalid" else 409,
        )
    raw_value, signed_value = OrientationSurveyBrowserService.resolve_or_create(request)
    browser_hash = OrientationSurveyBrowserService.hash_for_session(session=session, raw_value=raw_value)
    try:
        OrientationSurveyRateLimitService.check(
            request=request,
            session=session,
            browser_hash=browser_hash,
            purpose="validate",
        )
        form = OrientationEmailValidationForm(request.POST)
        if not form.is_valid():
            raise ValidationError(OrientationSurveyPublicService.VALIDATION_ERROR)
        participation = OrientationSurveyPublicService.match_participation(
            session=session,
            email=form.cleaned_data["email"],
        )
        if not participation:
            raise ValidationError(OrientationSurveyPublicService.VALIDATION_ERROR)
        if hasattr(participation, "response"):
            response = _render_public(
                request,
                state="duplicate",
                session=session,
                state_message="You have already submitted a response for this survey. Thank you for participating.",
                status=409,
            )
        else:
            OrientationSurveyPublicService.start_email_verification(
                request=request,
                session=session,
                participation=participation,
                browser_hash=browser_hash,
            )
            response = redirect("orientation_feedback:public_verify")
    except OrientationSurveyRateLimited as exc:
        response = _render_public(
            request,
            state="validate",
            session=session,
            form=OrientationEmailValidationForm(),
            public_token=public_token,
            state_message="; ".join(exc.messages),
            status=429,
        )
    except (OrientationSurveyEmailDeliveryError, ValidationError) as exc:
        safe_form = OrientationEmailValidationForm({"email": ""})
        safe_form.is_valid()
        safe_form.add_error("email", "; ".join(exc.messages))
        response = _render_public(
            request,
            state="validate",
            session=session,
            form=safe_form,
            public_token=public_token,
            status=400,
        )
    except Exception:
        logger.exception("Orientation feedback email validation failed for session %s", session.public_id)
        response = _render_public(
            request,
            state="validate",
            session=session,
            form=OrientationEmailValidationForm(),
            public_token=public_token,
            state_message="A temporary server error occurred. Please try again.",
            status=503,
        )
    OrientationSurveyBrowserService.set_cookie(response, signed_value)
    return response


@never_cache
@require_http_methods(["GET", "POST"])
def public_confirm_view(request):
    return redirect("orientation_feedback:public_verify")


@never_cache
@sensitive_post_parameters("otp_code")
@require_http_methods(["GET", "POST"])
def public_verify_view(request):
    raw_value, signed_value = OrientationSurveyBrowserService.resolve_or_create(request)
    verified = OrientationSurveyPublicService.resolve_state(
        request=request,
        raw_browser_value=raw_value,
        require_confirmed=False,
    )
    if not verified:
        response = _render_public(
            request,
            state="invalid",
            state_message="Email verification has expired. Please scan the survey QR code and validate again.",
            status=403,
        )
    elif request.method == "POST":
        form = OrientationEmailOtpForm(request.POST)
        try:
            browser_hash = OrientationSurveyBrowserService.hash_for_session(
                session=verified.session,
                raw_value=raw_value,
            )
            OrientationSurveyRateLimitService.check(
                request=request,
                session=verified.session,
                browser_hash=browser_hash,
                purpose="verify-email",
            )
            if not form.is_valid():
                raise ValidationError("Enter the 6-digit verification code.")
            OrientationSurveyPublicService.verify_email_otp(
                request=request,
                raw_browser_value=raw_value,
                code=form.cleaned_data["otp_code"],
            )
        except OrientationSurveyDuplicateResponse as exc:
            response = _render_public(
                request,
                state="duplicate",
                session=verified.session,
                state_message="; ".join(exc.messages),
                status=409,
            )
        except OrientationSurveyRateLimited as exc:
            response = _render_public(
                request,
                state="verify",
                session=verified.session,
                form=OrientationEmailOtpForm(),
                state_message="; ".join(exc.messages),
                status=429,
            )
        except ValidationError as exc:
            response = _render_public(
                request,
                state="verify",
                session=verified.session,
                form=OrientationEmailOtpForm(),
                state_message="; ".join(exc.messages),
                status=400,
            )
        else:
            response = redirect("orientation_feedback:public_response")
    else:
        response = _render_public(
            request,
            state="verify",
            session=verified.session,
            form=OrientationEmailOtpForm(),
            extra_context={
                "otp_expiry_minutes": OrientationSurveyPublicService._otp_expiry_minutes(),
            },
        )
    OrientationSurveyBrowserService.set_cookie(response, signed_value)
    return response


def _verified_public_state(request):
    raw_value, signed_value = OrientationSurveyBrowserService.resolve_or_create(request)
    verified = OrientationSurveyPublicService.resolve_state(
        request=request,
        raw_browser_value=raw_value,
        require_confirmed=True,
    )
    return verified, raw_value, signed_value


@never_cache
@require_GET
def public_response_view(request):
    verified, _, signed_value = _verified_public_state(request)
    if not verified:
        response = _render_public(
            request,
            state="invalid",
            state_message="Your validated survey session is unavailable. Please scan the QR code and validate again.",
            status=403,
        )
    elif hasattr(verified.participation, "response"):
        response = _render_public(
            request,
            state="duplicate",
            session=verified.session,
            state_message="You have already submitted a response for this survey. Thank you for participating.",
            status=409,
        )
    else:
        questions = verified.session.questions.prefetch_related("choices").order_by("display_order")
        form = OrientationResponseForm(questions=questions)
        response = render(
            request,
            "orientation_feedback/response_form.html",
            {"session": verified.session, "form": form},
        )
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response["Referrer-Policy"] = "same-origin"
    OrientationSurveyBrowserService.set_cookie(response, signed_value)
    return response


@never_cache
@sensitive_post_parameters()
@require_POST
def public_submit_view(request):
    verified, raw_value, signed_value = _verified_public_state(request)
    if not verified:
        response = _render_public(
            request,
            state="invalid",
            state_message="Your validated survey session is unavailable. Please scan the QR code and validate again.",
            status=403,
        )
        OrientationSurveyBrowserService.set_cookie(response, signed_value)
        return response
    questions = verified.session.questions.prefetch_related("choices").order_by("display_order")
    form = OrientationResponseForm(request.POST, questions=questions)
    browser_hash = OrientationSurveyBrowserService.hash_for_session(
        session=verified.session,
        raw_value=raw_value,
    )
    try:
        OrientationSurveyRateLimitService.check(
            request=request,
            session=verified.session,
            browser_hash=browser_hash,
            purpose="submit",
        )
        if not form.is_valid():
            response = render(
                request,
                "orientation_feedback/response_form.html",
                {"session": verified.session, "form": form},
                status=400,
            )
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response["Referrer-Policy"] = "same-origin"
        else:
            OrientationSurveyResponseService.submit(
                verified=verified,
                cleaned_data=form.cleaned_data,
                request=request,
            )
            OrientationSurveyPublicService.clear_state(request)
            response = redirect("orientation_feedback:public_thanks")
    except OrientationSurveyDuplicateResponse as exc:
        response = _render_public(
            request,
            state="duplicate",
            session=verified.session,
            state_message="; ".join(exc.messages),
            status=409,
        )
    except OrientationSurveyRateLimited as exc:
        response = render(
            request,
            "orientation_feedback/response_form.html",
            {"session": verified.session, "form": form, "state_message": "; ".join(exc.messages)},
            status=429,
        )
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    except ValidationError as exc:
        verified.session.refresh_from_db(fields=["status", "closed_at", "cancelled_at"])
        state, message = OrientationSurveyPublicService.state(verified.session)
        response = _render_public(
            request,
            state=state if state != "open" else "invalid",
            session=verified.session,
            state_message=message or "; ".join(exc.messages),
            status=409,
        )
    except Exception:
        logger.exception(
            "Orientation feedback submission failed for session %s",
            verified.session.public_id,
        )
        response = _render_public(
            request,
            state="invalid",
            session=verified.session,
            state_message="A temporary server error occurred. Your feedback was not submitted. Please try again.",
            status=503,
        )
    OrientationSurveyBrowserService.set_cookie(response, signed_value)
    return response


@never_cache
@require_GET
def public_thanks_view(request):
    return _render_public(
        request,
        state="thanks",
        state_message="Thank you for sharing your feedback. Your response has been recorded.",
    )
