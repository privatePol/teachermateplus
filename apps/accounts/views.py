from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.sessions.models import Session
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.conf import settings
from django.http import HttpResponse
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
    LoginOtpVerificationForm,
    FacultyPasswordResetSetForm,
    FacultySelfChangePasswordForm,
    PrivacyConsentForm,
    UserSignatureDeleteForm,
    UserSignatureUploadForm,
)
from apps.accounts.services import LoginLockoutService, LoginOtpService, UserSignatureService
from apps.core.decorators import permission_required, portal_required
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService
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


PENDING_OTP_USER_ID_KEY = "pending_login_otp_user_id"
PENDING_OTP_PORTAL_KEY = "pending_login_otp_portal"
PENDING_OTP_BACKEND_KEY = "pending_login_otp_backend"


def _otp_verify_url_name(portal_code: str) -> str:
    return "accounts:admin_login_otp" if (portal_code or "").upper() == "ADMIN" else "accounts:faculty_login_otp"


def _login_url_name(portal_code: str) -> str:
    return "accounts:admin_login" if (portal_code or "").upper() == "ADMIN" else "accounts:faculty_login"


def _store_pending_otp_login(request, *, user, portal_code: str) -> None:
    request.session[PENDING_OTP_USER_ID_KEY] = user.id
    request.session[PENDING_OTP_PORTAL_KEY] = (portal_code or "").upper()
    request.session[PENDING_OTP_BACKEND_KEY] = getattr(
        user,
        "backend",
        getattr(settings, "AUTHENTICATION_BACKENDS", ["django.contrib.auth.backends.ModelBackend"])[0],
    )
    request.session.modified = True


def _clear_pending_otp_login(request) -> None:
    for key in (PENDING_OTP_USER_ID_KEY, PENDING_OTP_PORTAL_KEY, PENDING_OTP_BACKEND_KEY):
        request.session.pop(key, None)
    request.session.modified = True


def _complete_portal_login(request, *, user, portal_code: str, dashboard_url_name: str, backend: str | None = None):
    LoginLockoutService.register_success(username=user.username, portal_code=portal_code)
    login(request, user, backend=backend)
    _clear_pending_otp_login(request)
    _enforce_single_device_session(request, user, portal_code)
    AuditService.log_login_success(request, user=user, portal=portal_code)
    security_redirect = _resolve_security_redirect(user, portal_code)
    if security_redirect:
        return redirect(reverse(security_redirect))
    return redirect(reverse(dashboard_url_name))


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

        if LoginOtpService.is_enabled_for_user(user):
            otp_result = LoginOtpService.create_and_send(request=self.request, user=user, portal_code=self.portal_code)
            if not otp_result.success:
                form.add_error(None, otp_result.message)
                return self.form_invalid(form)
            _store_pending_otp_login(self.request, user=user, portal_code=self.portal_code)
            messages.info(self.request, "A verification code was sent to your registered email address.")
            return redirect(reverse(_otp_verify_url_name(self.portal_code)))

        return _complete_portal_login(
            self.request,
            user=user,
            portal_code=self.portal_code,
            dashboard_url_name=self.dashboard_url_name,
            backend=getattr(user, "backend", None),
        )

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
class _BaseLoginOtpView(FormView):
    template_name = "accounts/login_otp.html"
    form_class = LoginOtpVerificationForm
    portal_code = ""
    portal_permission = ""
    dashboard_url_name = ""

    def dispatch(self, request, *args, **kwargs):
        self.pending_user = None
        pending_user_id = request.session.get(PENDING_OTP_USER_ID_KEY)
        pending_portal = request.session.get(PENDING_OTP_PORTAL_KEY)
        if not pending_user_id or pending_portal != self.portal_code:
            messages.error(request, "Please sign in again to request a new verification code.")
            return redirect(reverse(_login_url_name(self.portal_code)))
        self.pending_user = User.objects.filter(id=pending_user_id, is_active=True).first()
        if not self.pending_user or not PermissionService.has_permission(self.pending_user, self.portal_permission):
            _clear_pending_otp_login(request)
            messages.error(request, "Please sign in again to request a new verification code.")
            return redirect(reverse(_login_url_name(self.portal_code)))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "portal_name": "Admin Portal" if self.portal_code == "ADMIN" else "Faculty Portal",
                "masked_email": LoginOtpService._masked_email(self.pending_user.email),
                "login_url_name": _login_url_name(self.portal_code),
            }
        )
        return context

    def form_valid(self, form):
        result = LoginOtpService.verify(
            user=self.pending_user,
            portal_code=self.portal_code,
            code=form.cleaned_data["otp_code"],
            request=self.request,
        )
        if not result.success:
            form.add_error("otp_code", result.message)
            return self.form_invalid(form)
        return _complete_portal_login(
            self.request,
            user=self.pending_user,
            portal_code=self.portal_code,
            dashboard_url_name=self.dashboard_url_name,
            backend=self.request.session.get(PENDING_OTP_BACKEND_KEY),
        )


class AdminLoginOtpView(_BaseLoginOtpView):
    portal_code = "ADMIN"
    portal_permission = "admin_portal.access"
    dashboard_url_name = "admin_portal:dashboard"


class FacultyLoginOtpView(_BaseLoginOtpView):
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


def _signature_feature_enabled(*, user) -> bool:
    return FeatureSettingsService.is_user_signatures_enabled(
        tenant_id=getattr(user, "default_tenant_id", None),
        default=False,
    )


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def faculty_signature_view(request):
    if not _signature_feature_enabled(user=request.user):
        messages.error(request, "Stored signature management is currently disabled for this tenant.")
        return redirect("faculty_portal:dashboard")

    credential = getattr(request.user, "signature_credential", None)
    upload_form = UserSignatureUploadForm(request.user, request.POST or None, request.FILES or None, prefix="upload")
    delete_form = UserSignatureDeleteForm(request.user, request.POST or None, prefix="delete")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "upload":
            if upload_form.is_valid():
                try:
                    credential = UserSignatureService.store_signature(
                        user=request.user,
                        uploaded_file=upload_form.cleaned_data["signature_file"],
                        actor=request.user,
                    )
                except ValidationError as exc:
                    upload_form.add_error("signature_file", exc.message)
                else:
                    AuditService.log_event(
                        action="UPLOAD_SIGNATURE",
                        portal="FACULTY",
                        entity_type="UserSignatureCredential",
                        entity_id=credential.id,
                        actor=request.user,
                        tenant=request.user.default_tenant_id,
                        campus=request.user.default_campus_id,
                        metadata={
                            "filename": credential.original_filename,
                            "mime_type": credential.mime_type,
                            "file_size_bytes": credential.file_size_bytes,
                        },
                        request=request,
                    )
                    messages.success(request, "Your encrypted signature image has been saved.")
                    return redirect("accounts:faculty_signature")
            else:
                messages.error(request, "Please correct the signature upload errors below.")
        elif action == "delete":
            if delete_form.is_valid():
                credential = UserSignatureService.clear_signature(user=request.user)
                AuditService.log_event(
                    action="REMOVE_SIGNATURE",
                    portal="FACULTY",
                    entity_type="UserSignatureCredential",
                    entity_id=credential.id,
                    actor=request.user,
                    tenant=request.user.default_tenant_id,
                    campus=request.user.default_campus_id,
                    request=request,
                )
                messages.success(request, "Your stored signature has been removed.")
                return redirect("accounts:faculty_signature")
            messages.error(request, "Please correct the signature removal confirmation first.")

    return render(
        request,
        "faculty_portal/signature_profile.html",
        {
            "upload_form": upload_form,
            "delete_form": delete_form,
            "credential": credential,
        },
    )


@portal_required("FACULTY")
@permission_required("faculty_portal.access")
def faculty_signature_preview_view(request):
    if not _signature_feature_enabled(user=request.user):
        return HttpResponse(status=404)
    credential = UserSignatureService.get_active_credential(user=request.user)
    if not credential:
        return HttpResponse(status=404)
    image_bytes = UserSignatureService.decrypt_signature_bytes(credential=credential)
    AuditService.log_event(
        action="PREVIEW_SIGNATURE",
        portal="FACULTY",
        entity_type="UserSignatureCredential",
        entity_id=credential.id,
        actor=request.user,
        tenant=request.user.default_tenant_id,
        campus=request.user.default_campus_id,
        metadata={
            "mime_type": credential.mime_type,
            "file_size_bytes": credential.file_size_bytes,
        },
        request=request,
    )
    return HttpResponse(image_bytes, content_type=credential.mime_type or "image/png")


@portal_required("ADMIN")
@permission_required("admin_portal.access")
def admin_signature_view(request):
    if not _signature_feature_enabled(user=request.user):
        messages.error(request, "Stored signature management is currently disabled for this tenant.")
        return redirect("admin_portal:dashboard")

    credential = getattr(request.user, "signature_credential", None)
    upload_form = UserSignatureUploadForm(request.user, request.POST or None, request.FILES or None, prefix="upload")
    delete_form = UserSignatureDeleteForm(request.user, request.POST or None, prefix="delete")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "upload":
            if upload_form.is_valid():
                try:
                    credential = UserSignatureService.store_signature(
                        user=request.user,
                        uploaded_file=upload_form.cleaned_data["signature_file"],
                        actor=request.user,
                    )
                except ValidationError as exc:
                    upload_form.add_error("signature_file", exc.message)
                else:
                    AuditService.log_event(
                        action="UPLOAD_SIGNATURE",
                        portal="ADMIN",
                        entity_type="UserSignatureCredential",
                        entity_id=credential.id,
                        actor=request.user,
                        tenant=request.user.default_tenant_id,
                        campus=request.user.default_campus_id,
                        metadata={
                            "filename": credential.original_filename,
                            "mime_type": credential.mime_type,
                            "file_size_bytes": credential.file_size_bytes,
                        },
                        request=request,
                    )
                    messages.success(request, "Your encrypted signature image has been saved.")
                    return redirect("accounts:admin_signature")
            else:
                messages.error(request, "Please correct the signature upload errors below.")
        elif action == "delete":
            if delete_form.is_valid():
                credential = UserSignatureService.clear_signature(user=request.user)
                AuditService.log_event(
                    action="REMOVE_SIGNATURE",
                    portal="ADMIN",
                    entity_type="UserSignatureCredential",
                    entity_id=credential.id,
                    actor=request.user,
                    tenant=request.user.default_tenant_id,
                    campus=request.user.default_campus_id,
                    request=request,
                )
                messages.success(request, "Your stored signature has been removed.")
                return redirect("accounts:admin_signature")
            messages.error(request, "Please correct the signature removal confirmation first.")

    return render(
        request,
        "admin_portal/security/signature_profile.html",
        {
            "upload_form": upload_form,
            "delete_form": delete_form,
            "credential": credential,
        },
    )


@portal_required("ADMIN")
@permission_required("admin_portal.access")
def admin_signature_preview_view(request):
    if not _signature_feature_enabled(user=request.user):
        return HttpResponse(status=404)
    credential = UserSignatureService.get_active_credential(user=request.user)
    if not credential:
        return HttpResponse(status=404)
    image_bytes = UserSignatureService.decrypt_signature_bytes(credential=credential)
    AuditService.log_event(
        action="PREVIEW_SIGNATURE",
        portal="ADMIN",
        entity_type="UserSignatureCredential",
        entity_id=credential.id,
        actor=request.user,
        tenant=request.user.default_tenant_id,
        campus=request.user.default_campus_id,
        metadata={
            "mime_type": credential.mime_type,
            "file_size_bytes": credential.file_size_bytes,
        },
        request=request,
    )
    return HttpResponse(image_bytes, content_type=credential.mime_type or "image/png")
