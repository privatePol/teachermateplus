from __future__ import annotations

import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing

from apps.core.decorators import permission_required, portal_required
from apps.core.services.features import FeatureSettingsService
from apps.exit_pulse.forms import (
    ExitPulseCreateForm,
    ExitPulseDashboardFilterForm,
    ExitPulseHistoryFilterForm,
    ExitPulseResponseForm,
)
from apps.exit_pulse.models import ExitPulseSession
from apps.exit_pulse.services import (
    ExitPulseAnalyticsService,
    ExitPulseAnonymousIdentityService,
    ExitPulseDuplicateResponse,
    ExitPulseHistoryService,
    ExitPulseRateLimited,
    ExitPulseRateLimitService,
    ExitPulseResponseService,
    ExitPulseSessionService,
)


logger = logging.getLogger("teachermateplus.security")


def _scope_ids(request):
    scope = getattr(request, "scope", {})
    return scope.get("tenant_id"), scope.get("campus_id")


def _feature_enabled(tenant_id):
    return FeatureSettingsService.is_exit_pulse_enabled(tenant_id=tenant_id, default=True)


def _public_url(request, session):
    entry_url = request.build_absolute_uri(reverse("exit_pulse:public_survey"))
    return f"{entry_url}#{session.public_token}"


def _require_faculty_feature(request):
    tenant_id, _ = _scope_ids(request)
    if not _feature_enabled(tenant_id):
        raise PermissionDenied("Exit Pulse is disabled for this tenant.")


def _owned_session(request, public_id):
    tenant_id, campus_id = _scope_ids(request)
    queryset = ExitPulseSession.objects.select_related(
        "tenant",
        "campus",
        "faculty_user",
        "faculty_assignment",
        "course_offering",
        "academic_year",
        "term",
        "course",
        "section",
    ).filter(public_id=public_id, faculty_user=request.user)
    if tenant_id:
        queryset = queryset.filter(tenant_id=tenant_id)
    if campus_id:
        queryset = queryset.filter(campus_id=campus_id)
    session = queryset.first()
    if not session:
        raise Http404("Exit Pulse session not found.")
    return ExitPulseSessionService.refresh_effective_status(session)


@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def landing_view(request):
    _require_faculty_feature(request)
    tenant_id, campus_id = _scope_ids(request)
    owned_sessions = ExitPulseHistoryService.owned_sessions(
        user=request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    )
    ExitPulseHistoryService.expire_elapsed_sessions(owned_sessions)
    current_assignment_ids = list(
        ExitPulseSessionService.valid_assignments_for_user(
            user=request.user,
            tenant_id=tenant_id,
            campus_id=campus_id,
        )
        .order_by()
        .values_list("id", flat=True)
    )
    scope_rows = list(
        owned_sessions.order_by("academic_year__code", "term__code")
        .values(
            "academic_year_id",
            "academic_year__code",
            "term_id",
            "term__code",
            "term__name",
        )
        .distinct()
    )
    filter_form = ExitPulseDashboardFilterForm(
        request.GET,
        scope_rows=scope_rows,
    )
    if filter_form.is_valid():
        filtered_sessions = ExitPulseHistoryService.apply_dashboard_filters(
            owned_sessions,
            filter_form.cleaned_data,
        )
    else:
        filtered_sessions = owned_sessions.none()

    current_queryset = owned_sessions.filter(
        status__in=[ExitPulseSession.Status.DRAFT, ExitPulseSession.Status.LIVE],
        faculty_assignment_id__in=current_assignment_ids,
    ).select_related("course", "section", "campus", "term")
    current_sessions = []
    for session in current_queryset.order_by("-created_at", "-id")[:10]:
        ExitPulseSessionService.refresh_effective_status(session, check_assignment=False)
        if session.status in {ExitPulseSession.Status.DRAFT, ExitPulseSession.Status.LIVE}:
            current_sessions.append(session)

    dashboard_analytics = ExitPulseAnalyticsService.build_assignment(filtered_sessions)
    recent_queryset = ExitPulseAnalyticsService.annotate_session_metrics(
        ExitPulseAnalyticsService.terminal_sessions(filtered_sessions).select_related(
            "course",
            "section",
            "campus",
            "academic_year",
            "term",
        )
    ).order_by("-started_at", "-id")[:10]
    recent_sessions = ExitPulseAnalyticsService.session_rows(recent_queryset)
    history_assignments = ExitPulseHistoryService.assignment_rows(
        filtered_sessions,
        current_assignment_ids=current_assignment_ids,
    )
    return render(
        request,
        "exit_pulse/landing.html",
        {
            "current_sessions": current_sessions,
            "assignment_count": len(current_assignment_ids),
            "dashboard_analytics": dashboard_analytics,
            "recent_sessions": recent_sessions,
            "history_assignments": history_assignments,
            "filter_form": filter_form,
            "has_filter_options": bool(scope_rows),
        },
    )


@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def history_view(request, session_public_id):
    _require_faculty_feature(request)
    tenant_id, campus_id = _scope_ids(request)
    owned_sessions = ExitPulseHistoryService.owned_sessions(
        user=request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    )
    ExitPulseHistoryService.expire_elapsed_sessions(owned_sessions)
    reference_session = (
        owned_sessions.select_related(
            "faculty_user",
            "faculty_assignment",
            "course_offering",
            "course",
            "section",
            "campus",
            "academic_year",
            "term",
        )
        .filter(public_id=session_public_id)
        .first()
    )
    if reference_session is None:
        raise Http404("Exit Pulse history not found.")

    assignment_sessions = owned_sessions.filter(
        faculty_assignment_id=reference_session.faculty_assignment_id,
    )
    terminal_sessions = ExitPulseAnalyticsService.terminal_sessions(assignment_sessions)
    assignment_context = ExitPulseHistoryService.history_context(terminal_sessions)
    is_current_assignment = ExitPulseSessionService.valid_assignments_for_user(
        user=request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    ).filter(pk=reference_session.faculty_assignment_id).exists()

    filter_form = ExitPulseHistoryFilterForm(request.GET)
    if filter_form.is_valid():
        filtered_sessions = ExitPulseHistoryService.apply_history_filters(
            terminal_sessions,
            filter_form.cleaned_data,
        )
    else:
        filtered_sessions = terminal_sessions.none()
    filtered_analytics = ExitPulseAnalyticsService.build_assignment(filtered_sessions)
    history_queryset = ExitPulseAnalyticsService.annotate_session_metrics(
        filtered_sessions.select_related(
            "course",
            "section",
            "campus",
            "academic_year",
            "term",
        )
    ).order_by("-started_at", "-id")
    paginator = Paginator(history_queryset, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_obj.object_list = ExitPulseAnalyticsService.session_rows(page_obj.object_list)
    page_query = request.GET.copy()
    page_query.pop("page", None)
    active_filter_keys = ("date_from", "date_to", "question_type", "topic", "status")
    has_active_filters = any((request.GET.get(key) or "").strip() for key in active_filter_keys)

    return render(
        request,
        "exit_pulse/history.html",
        {
            "reference_session": reference_session,
            "assignment_context": assignment_context,
            "is_current_assignment": is_current_assignment,
            "filter_form": filter_form,
            "filtered_analytics": filtered_analytics,
            "page_obj": page_obj,
            "page_query": page_query.urlencode(),
            "has_active_filters": has_active_filters,
        },
    )


@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def create_view(request):
    _require_faculty_feature(request)
    tenant_id, campus_id = _scope_ids(request)
    form = ExitPulseCreateForm(
        request.POST or None,
        user=request.user,
        tenant_id=tenant_id,
        campus_id=campus_id,
    )
    if request.method == "POST" and form.is_valid():
        try:
            session = ExitPulseSessionService.create_draft(
                user=request.user,
                assignment=form.cleaned_data["faculty_assignment"],
                topic=form.cleaned_data["topic"],
                question_code=form.cleaned_data["question_code"],
                custom_question=form.cleaned_data.get("custom_question", ""),
                allow_written_feedback=form.cleaned_data.get("allow_written_feedback", False),
                feedback_review_enabled=form.cleaned_data.get("feedback_review_enabled", False),
                feedback_learned_enabled=form.cleaned_data.get("feedback_learned_enabled", False),
                tenant_id=tenant_id,
                campus_id=campus_id,
                request=request,
            )
            session = ExitPulseSessionService.start(session=session, user=request.user, request=request)
        except (PermissionDenied, ValidationError) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Exit Pulse started. Share the QR code or public link with the class.")
            return redirect("exit_pulse:live", public_id=session.public_id)
    return render(request, "exit_pulse/create.html", {"form": form})


@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def live_view(request, public_id):
    _require_faculty_feature(request)
    session = _owned_session(request, public_id)
    if session.status not in {ExitPulseSession.Status.DRAFT, ExitPulseSession.Status.LIVE}:
        return redirect("exit_pulse:results", public_id=session.public_id)
    public_url = _public_url(request, session)
    remaining_seconds = max(0, int((session.expires_at - timezone.now()).total_seconds())) if session.expires_at else 0
    return render(
        request,
        "exit_pulse/live.html",
        {
            "session": session,
            "public_url": public_url,
            "remaining_seconds": remaining_seconds,
            "response_count": session.responses.count(),
            "can_extend": session.status == ExitPulseSession.Status.LIVE and session.extension_count == 0,
        },
    )


@require_GET
@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def status_view(request, public_id):
    _require_faculty_feature(request)
    session = _owned_session(request, public_id)
    remaining_seconds = max(0, int((session.expires_at - timezone.now()).total_seconds())) if session.expires_at else 0
    return JsonResponse(
        {
            "status": session.status,
            "status_label": session.get_status_display(),
            "response_count": session.responses.count(),
            "remaining_seconds": remaining_seconds,
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
            "can_extend": session.status == ExitPulseSession.Status.LIVE and session.extension_count == 0,
            "results_url": reverse("exit_pulse:results", kwargs={"public_id": session.public_id}),
        }
    )


@require_GET
@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def qr_view(request, public_id):
    _require_faculty_feature(request)
    session = _owned_session(request, public_id)
    public_url = _public_url(request, session)
    qr = QrCodeWidget(public_url)
    bounds = qr.getBounds()
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    size = 240
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(qr)
    response = HttpResponse(renderSVG.drawToString(drawing), content_type="image/svg+xml")
    response["Cache-Control"] = "private, no-store"
    return response


def _perform_action(request, public_id, action):
    _require_faculty_feature(request)
    session = _owned_session(request, public_id)
    try:
        updated = action(session=session, user=request.user, request=request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("exit_pulse:live", public_id=session.public_id)
    if updated.status in {
        ExitPulseSession.Status.CLOSED,
        ExitPulseSession.Status.EXPIRED,
        ExitPulseSession.Status.CANCELLED,
    }:
        return redirect("exit_pulse:results", public_id=updated.public_id)
    return redirect("exit_pulse:live", public_id=updated.public_id)


@require_POST
@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def extend_view(request, public_id):
    return _perform_action(request, public_id, ExitPulseSessionService.extend)


@require_POST
@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def close_view(request, public_id):
    return _perform_action(request, public_id, ExitPulseSessionService.close)


@require_POST
@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def cancel_view(request, public_id):
    return _perform_action(request, public_id, ExitPulseSessionService.cancel)


@portal_required("FACULTY")
@permission_required("exit_pulse.use")
def results_view(request, public_id):
    _require_faculty_feature(request)
    session = _owned_session(request, public_id)
    if session.status in {ExitPulseSession.Status.DRAFT, ExitPulseSession.Status.LIVE}:
        return redirect("exit_pulse:live", public_id=session.public_id)
    ExitPulseResponseService.anonymize_expired_identifiers(session_id=session.id)
    analytics = ExitPulseAnalyticsService.build(session)
    return render(
        request,
        "exit_pulse/results.html",
        {"session": session, "analytics": analytics},
    )


def _public_state(session):
    if session is None:
        return "invalid", "This Exit Pulse link is invalid."
    if session.status == ExitPulseSession.Status.DRAFT:
        return "not_live", "This Exit Pulse has not started yet."
    if session.status == ExitPulseSession.Status.EXPIRED:
        return "expired", "This Exit Pulse has expired."
    if session.status == ExitPulseSession.Status.CLOSED:
        return "closed", "This Exit Pulse has been closed."
    if session.status == ExitPulseSession.Status.CANCELLED:
        return "cancelled", "This Exit Pulse was cancelled."
    return "live", ""


def _render_public(request, *, session=None, form=None, state=None, state_message="", status=200):
    remaining_seconds = 0
    if session and session.expires_at:
        remaining_seconds = max(0, int((session.expires_at - timezone.now()).total_seconds()))
    response = render(
        request,
        "exit_pulse/public_survey.html",
        {
            "session": session,
            "form": form,
            "pulse_state": state,
            "state_message": state_message,
            "remaining_seconds": remaining_seconds,
            "public_token": session.public_token if session else "",
            "reaction_options": (
                ("CONFIDENT", "❤️", "I understand it well and feel confident"),
                ("MOSTLY_UNDERSTOOD", "😊", "I understand most of it"),
                ("NEEDS_CLARIFICATION", "🤔", "I need a little clarification"),
                ("NEEDS_PRACTICE", "🧩", "I need more examples or practice"),
            ),
        },
        status=status,
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_GET
def public_survey_view(request):
    return _render_public(request, state="entry")


def _open_public_session(request, public_token):
    session = ExitPulseResponseService.resolve_public_session(public_token)
    state, state_message = _public_state(session)
    if session and not _feature_enabled(session.tenant_id):
        state, state_message = "closed", "Exit Pulse is currently unavailable."
    if state != "live":
        return _render_public(
            request,
            session=session,
            state=state,
            state_message=state_message,
            status=404 if state == "invalid" else 200,
        )
    raw_value, signed_value, _ = ExitPulseAnonymousIdentityService.resolve_or_create(request)
    anonymous_hash = ExitPulseAnonymousIdentityService.hash_for_session(session=session, raw_value=raw_value)
    if ExitPulseResponseService.already_submitted(
        session=session,
        anonymous_hash=anonymous_hash,
    ):
        response = _render_public(
            request,
            session=session,
            state="submitted",
            state_message="Thank you. Your response has been recorded.",
        )
    else:
        response = _render_public(
            request,
            session=session,
            form=ExitPulseResponseForm(session=session),
            state="live",
        )
    ExitPulseAnonymousIdentityService.set_cookie(response, signed_value)
    return response


@never_cache
@sensitive_post_parameters("public_token")
@require_POST
def public_open_view(request):
    return _open_public_session(request, request.POST.get("public_token", ""))


@never_cache
@sensitive_post_parameters("public_token", "response_code", "feedback_review", "feedback_learned")
@require_POST
def public_submit_view(request):
    public_token = request.POST.get("public_token", "")
    session = ExitPulseResponseService.resolve_public_session(public_token, refresh=False)
    state, state_message = _public_state(session)
    if session and not _feature_enabled(session.tenant_id):
        state, state_message = "closed", "Exit Pulse is currently unavailable."
    if state != "live":
        return _render_public(
            request,
            session=session,
            state=state,
            state_message=state_message,
            status=404 if state == "invalid" else 409,
        )
    raw_value, signed_value, _ = ExitPulseAnonymousIdentityService.resolve_or_create(request)
    anonymous_hash = ExitPulseAnonymousIdentityService.hash_for_session(session=session, raw_value=raw_value)
    form = ExitPulseResponseForm(request.POST, session=session)
    if not form.is_valid():
        response = _render_public(request, session=session, form=form, state="live", status=400)
        ExitPulseAnonymousIdentityService.set_cookie(response, signed_value)
        return response
    try:
        ExitPulseRateLimitService.check(
            request=request,
            session=session,
            anonymous_hash=anonymous_hash,
        )
        ExitPulseResponseService.submit(
            session=session,
            response_code=form.cleaned_data["response_code"],
            anonymous_hash=anonymous_hash,
            feedback_review=form.cleaned_data.get("feedback_review", ""),
            feedback_learned=form.cleaned_data.get("feedback_learned", ""),
            request=request,
        )
    except ExitPulseDuplicateResponse:
        response = _render_public(
            request,
            session=session,
            state="submitted",
            state_message="This browser has already submitted a response.",
            status=409,
        )
    except ExitPulseRateLimited as exc:
        response = _render_public(
            request,
            session=session,
            form=form,
            state="live",
            state_message="; ".join(exc.messages),
            status=429,
        )
    except ValidationError as exc:
        session.refresh_from_db(fields=["status", "closed_at", "cancelled_at", "updated_at"])
        effective_state, effective_message = _public_state(session)
        if effective_state == "live":
            response = _render_public(
                request,
                session=session,
                form=form,
                state="live",
                state_message="; ".join(exc.messages),
                status=409,
            )
        else:
            response = _render_public(
                request,
                session=session,
                state=effective_state,
                state_message=effective_message,
                status=409,
            )
    except Exception:
        logger.exception(
            "Exit Pulse anonymous submission failed for session %s",
            session.public_id,
        )
        response = _render_public(
            request,
            session=session,
            form=form,
            state="live",
            state_message="A temporary server error occurred. Please try again.",
            status=503,
        )
    else:
        response = redirect("exit_pulse:public_thanks")
    ExitPulseAnonymousIdentityService.set_cookie(response, signed_value)
    return response


@never_cache
@require_GET
def public_thanks_view(request):
    return _render_public(
        request,
        state="submitted",
        state_message="Thank you. Your response has been recorded.",
    )
