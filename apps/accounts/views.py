from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.sessions.models import Session
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.conf import settings
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import RedirectView
from django.views.generic import FormView, TemplateView

from apps.accounts.forms import (
    AdminSelfChangePasswordForm,
    AdminLoginForm,
    FacultyForgotPasswordForm,
    FacultyLoginForm,
    FacultyPasswordResetSetForm,
    FacultySelfChangePasswordForm,
    PrivacyConsentForm,
)
from apps.core.decorators import permission_required, portal_required
from apps.core.services.audit import AuditService
from apps.core.services.permissions import PermissionService

User = get_user_model()


def _send_faculty_password_reset_email(request, user, reset_url: str) -> int:
    subject = "EduGradesPro Faculty Password Reset"
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@edugradespro.local")
    recipient = [user.email]
    logo_url = request.build_absolute_uri(f"{settings.MEDIA_URL}logos/egp_logo_official.png")

    context = {
        "user": user,
        "reset_url": reset_url,
        "logo_url": logo_url,
        "privacy_notice_url": "https://ncba.edu.ph/ncba-privacy-notice/",
    }
    text_body = render_to_string("faculty_portal/emails/password_reset.txt", context)
    html_body = render_to_string("faculty_portal/emails/password_reset.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=recipient,
    )
    message.attach_alternative(html_body, "text/html")
    return message.send(fail_silently=True)


def _requires_privacy_consent(user) -> bool:
    required_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
    return (
        not getattr(user, "privacy_consent_at", None)
        or (getattr(user, "privacy_consent_version", None) or "") != required_version
    )


def _resolve_security_redirect(user, portal_code: str) -> str | None:
    portal_code = (portal_code or "").upper()
    if getattr(user, "must_change_password", False):
        return "accounts:admin_change_password" if portal_code == "ADMIN" else "accounts:faculty_change_password"
    if _requires_privacy_consent(user):
        return "accounts:admin_privacy_consent" if portal_code == "ADMIN" else "accounts:faculty_privacy_consent"
    return None


def _enforce_single_device_session(request, user, portal_code: str):
    if not getattr(settings, "ENFORCE_SINGLE_DEVICE_SESSION", True):
        return
    if not request.session.session_key:
        request.session.save()
    current_key = request.session.session_key
    if not current_key:
        return

    revoked_sessions = 0
    active_sessions = Session.objects.filter(expire_date__gte=timezone.now())
    for session in active_sessions:
        decoded = session.get_decoded()
        if str(decoded.get("_auth_user_id")) == str(user.pk) and session.session_key != current_key:
            session.delete()
            revoked_sessions += 1

    if revoked_sessions:
        AuditService.log_event(
            action="SINGLE_SESSION_ENFORCED",
            portal=portal_code,
            entity_type="User",
            entity_id=user.id,
            actor=user,
            metadata={"revoked_sessions": revoked_sessions},
            request=request,
        )


class _BasePortalLoginView(FormView):
    template_name = ""
    form_class = None
    portal_code = ""
    portal_permission = ""
    dashboard_url_name = ""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and PermissionService.has_permission(request.user, self.portal_permission):
            security_redirect = _resolve_security_redirect(request.user, self.portal_code)
            if security_redirect:
                return redirect(reverse(security_redirect))
            return redirect(reverse(self.dashboard_url_name))
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        user = form.get_user()
        if not PermissionService.has_permission(user, self.portal_permission):
            AuditService.log_login_failure(
                self.request, username=form.cleaned_data.get("username", ""), portal=self.portal_code
            )
            form.add_error(None, "You do not have access to this portal.")
            return self.form_invalid(form)

        login(self.request, user)
        _enforce_single_device_session(self.request, user, self.portal_code)
        AuditService.log_login_success(self.request, user=user, portal=self.portal_code)
        security_redirect = _resolve_security_redirect(user, self.portal_code)
        if security_redirect:
            return redirect(reverse(security_redirect))
        return redirect(reverse(self.dashboard_url_name))

    def form_invalid(self, form):
        username = self.request.POST.get("username", "")
        if username:
            AuditService.log_login_failure(self.request, username=username, portal=self.portal_code)
        return super().form_invalid(form)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class AdminLoginView(_BasePortalLoginView):
    template_name = "admin_portal/login.html"
    form_class = AdminLoginForm
    portal_code = "ADMIN"
    portal_permission = "admin_portal.access"
    dashboard_url_name = "admin_portal:dashboard"


@method_decorator(ensure_csrf_cookie, name="dispatch")
class FacultyLoginView(_BasePortalLoginView):
    template_name = "faculty_portal/login.html"
    form_class = FacultyLoginForm
    portal_code = "FACULTY"
    portal_permission = "faculty_portal.access"
    dashboard_url_name = "faculty_portal:dashboard"


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PublicIndexView(TemplateView):
    template_name = "public/index.html"


@method_decorator(ensure_csrf_cookie, name="dispatch")
class PublicLoginView(RedirectView):
    pattern_name = "accounts:admin_login"
    permanent = False


def admin_logout_view(request):
    if request.user.is_authenticated:
        AuditService.log_event(
            action="LOGOUT",
            portal="ADMIN",
            entity_type="User",
            entity_id=request.user.id,
            actor=request.user,
            request=request,
        )
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("accounts:admin_login")


def faculty_logout_view(request):
    if request.user.is_authenticated:
        AuditService.log_event(
            action="LOGOUT",
            portal="FACULTY",
            entity_type="User",
            entity_id=request.user.id,
            actor=request.user,
            request=request,
        )
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("faculty_portal:public_index")


@method_decorator(ensure_csrf_cookie, name="dispatch")
class FacultyForgotPasswordView(FormView):
    template_name = "faculty_portal/password_forgot.html"
    form_class = FacultyForgotPasswordForm

    def form_valid(self, form):
        identifier = form.cleaned_data["identifier"].strip()
        user = (
            User.objects.filter(Q(username__iexact=identifier) | Q(email__iexact=identifier), is_active=True)
            .order_by("id")
            .first()
        )
        delivered = False
        if user and user.email and PermissionService.has_permission(user, "faculty_portal.access"):
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = self.request.build_absolute_uri(
                reverse(
                    "accounts:faculty_password_reset_confirm",
                    kwargs={"uidb64": uid, "token": token},
                )
            )
            sent_count = _send_faculty_password_reset_email(self.request, user, reset_url)
            delivered = sent_count > 0
        AuditService.log_event(
            action="PASSWORD_RESET_REQUEST",
            portal="FACULTY",
            entity_type="User",
            entity_id=user.id if user else None,
            actor=None,
            metadata={
                "identifier": identifier,
                "delivered": delivered,
                "target_username": user.username if user else None,
            },
            request=self.request,
        )
        messages.success(
            self.request,
            "If the account exists and is allowed for faculty access, a reset link has been sent to the registered email.",
        )
        return redirect("accounts:faculty_forgot_password_done")


@ensure_csrf_cookie
def faculty_forgot_password_done_view(request):
    return render(request, "faculty_portal/password_forgot_done.html")


@ensure_csrf_cookie
def faculty_password_reset_confirm_view(request, uidb64: str, token: str):
    user = None
    valid_link = False
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.filter(pk=uid, is_active=True).first()
    except (TypeError, ValueError, OverflowError):
        user = None

    if user and default_token_generator.check_token(user, token):
        valid_link = True

    if not valid_link:
        messages.error(request, "This reset link is invalid or expired. Please request a new one.")
        return redirect("accounts:faculty_forgot_password")

    form = FacultyPasswordResetSetForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        updated_user = form.save()
        updated_user.must_change_password = False
        updated_user.save(update_fields=["must_change_password"])
        AuditService.log_event(
            action="RESET_PASSWORD",
            portal="FACULTY",
            entity_type="User",
            entity_id=user.id,
            actor=None,
            metadata={"target_username": user.username},
            request=request,
        )
        messages.success(request, "Password has been reset. You can now sign in.")
        return redirect("accounts:faculty_password_reset_complete")

    return render(request, "faculty_portal/password_reset_confirm.html", {"form": form})


@ensure_csrf_cookie
def faculty_password_reset_complete_view(request):
    return render(request, "faculty_portal/password_reset_complete.html")


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def faculty_change_password_view(request):
    form = FacultySelfChangePasswordForm(user=request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        updated_user = form.save()
        updated_user.must_change_password = False
        updated_user.save(update_fields=["must_change_password"])
        update_session_auth_hash(request, updated_user)
        AuditService.log_event(
            action="CHANGE_PASSWORD",
            portal="FACULTY",
            entity_type="User",
            entity_id=updated_user.id,
            actor=request.user,
            metadata={"target_username": updated_user.username},
            request=request,
        )
        messages.success(request, "Your password has been updated.")
        security_redirect = _resolve_security_redirect(updated_user, "FACULTY")
        if security_redirect:
            return redirect(security_redirect)
        return redirect("faculty_portal:dashboard")
    return render(request, "faculty_portal/password_change.html", {"form": form})


@portal_required("ADMIN")
@permission_required("admin_portal.access")
def admin_change_password_view(request):
    form = AdminSelfChangePasswordForm(user=request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        updated_user = form.save()
        updated_user.must_change_password = False
        updated_user.save(update_fields=["must_change_password"])
        update_session_auth_hash(request, updated_user)
        AuditService.log_event(
            action="CHANGE_PASSWORD",
            portal="ADMIN",
            entity_type="User",
            entity_id=updated_user.id,
            actor=request.user,
            metadata={"target_username": updated_user.username},
            request=request,
        )
        messages.success(request, "Your password has been updated.")
        security_redirect = _resolve_security_redirect(updated_user, "ADMIN")
        if security_redirect:
            return redirect(security_redirect)
        return redirect("admin_portal:dashboard")
    return render(request, "admin_portal/security/self_password_change.html", {"form": form})


@portal_required("ADMIN")
@permission_required("admin_portal.access")
def admin_privacy_consent_view(request):
    if request.user.must_change_password:
        return redirect("accounts:admin_change_password")
    form = PrivacyConsentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        request.user.privacy_consent_at = timezone.now()
        request.user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        request.user.privacy_consent_ip = request.META.get("REMOTE_ADDR")
        request.user.save(
            update_fields=[
                "privacy_consent_at",
                "privacy_consent_version",
                "privacy_consent_ip",
            ]
        )
        AuditService.log_event(
            action="PRIVACY_CONSENT_ACCEPTED",
            portal="ADMIN",
            entity_type="User",
            entity_id=request.user.id,
            actor=request.user,
            metadata={"version": request.user.privacy_consent_version},
            request=request,
        )
        messages.success(request, "Privacy consent recorded.")
        return redirect("admin_portal:dashboard")
    return render(
        request,
        "admin_portal/security/privacy_consent.html",
        {"form": form, "consent_version": getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")},
    )


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def faculty_privacy_consent_view(request):
    if request.user.must_change_password:
        return redirect("accounts:faculty_change_password")
    form = PrivacyConsentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        request.user.privacy_consent_at = timezone.now()
        request.user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        request.user.privacy_consent_ip = request.META.get("REMOTE_ADDR")
        request.user.save(
            update_fields=[
                "privacy_consent_at",
                "privacy_consent_version",
                "privacy_consent_ip",
            ]
        )
        AuditService.log_event(
            action="PRIVACY_CONSENT_ACCEPTED",
            portal="FACULTY",
            entity_type="User",
            entity_id=request.user.id,
            actor=request.user,
            metadata={"version": request.user.privacy_consent_version},
            request=request,
        )
        messages.success(request, "Privacy consent recorded.")
        return redirect("faculty_portal:dashboard")
    return render(
        request,
        "faculty_portal/privacy_consent.html",
        {"form": form, "consent_version": getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")},
    )
