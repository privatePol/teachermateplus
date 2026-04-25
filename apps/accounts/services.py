from __future__ import annotations

import base64
import random
from dataclasses import dataclass
from datetime import timedelta
import hashlib
from io import BytesIO
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from PIL import Image

from apps.accounts.models import LoginOtpChallenge, PortalLoginLockoutState, UserSignatureCredential, UserSignatureUsageLog
from apps.core.services.audit import AuditService
from apps.core.services.features import FeatureSettingsService

User = get_user_model()


@dataclass
class LoginLockoutStatus:
    enabled: bool
    is_locked: bool
    locked_until: object | None
    max_attempts: int
    window_minutes: int
    duration_minutes: int
    state: PortalLoginLockoutState | None = None
    just_locked: bool = False


class LoginLockoutService:
    @staticmethod
    def normalize_username(username: str | None) -> str:
        return str(username or "").strip().lower()

    @classmethod
    def _resolve_user(cls, username: str) -> User | None:
        normalized = cls.normalize_username(username)
        if not normalized:
            return None
        return User.objects.filter(username__iexact=normalized).first()

    @classmethod
    def _tenant_id_for_username(cls, username: str) -> int | None:
        user = cls._resolve_user(username)
        return getattr(user, "default_tenant_id", None) if user else None

    @classmethod
    def _policy(cls, username: str) -> tuple[bool, int, int, int]:
        tenant_id = cls._tenant_id_for_username(username)
        return (
            FeatureSettingsService.is_login_lockout_enabled(tenant_id=tenant_id, default=True),
            FeatureSettingsService.get_login_lockout_max_attempts(tenant_id=tenant_id, default=5),
            FeatureSettingsService.get_login_lockout_window_minutes(tenant_id=tenant_id, default=15),
            FeatureSettingsService.get_login_lockout_duration_minutes(tenant_id=tenant_id, default=15),
        )

    @classmethod
    def _get_state(cls, username: str, portal_code: str) -> PortalLoginLockoutState | None:
        normalized = cls.normalize_username(username)
        if not normalized:
            return None
        return PortalLoginLockoutState.objects.filter(
            username=normalized,
            portal_code=(portal_code or "").upper(),
        ).first()

    @classmethod
    def _reset_state_if_needed(
        cls,
        *,
        state: PortalLoginLockoutState | None,
        now,
        window_minutes: int,
    ) -> PortalLoginLockoutState | None:
        if state is None:
            return None
        fields_to_update: list[str] = []
        if state.locked_until and state.locked_until <= now:
            state.locked_until = None
            state.failed_attempt_count = 0
            state.window_started_at = None
            fields_to_update.extend(["locked_until", "failed_attempt_count", "window_started_at"])
        elif (
            state.window_started_at
            and not state.locked_until
            and state.window_started_at <= now - timedelta(minutes=window_minutes)
        ):
            state.failed_attempt_count = 0
            state.window_started_at = None
            state.last_failed_at = None
            fields_to_update.extend(["failed_attempt_count", "window_started_at", "last_failed_at"])
        if fields_to_update:
            state.save(update_fields=fields_to_update + ["updated_at"])
        return state

    @classmethod
    def get_status(cls, username: str, portal_code: str) -> LoginLockoutStatus:
        enabled, max_attempts, window_minutes, duration_minutes = cls._policy(username)
        if not enabled:
            return LoginLockoutStatus(
                enabled=False,
                is_locked=False,
                locked_until=None,
                max_attempts=max_attempts,
                window_minutes=window_minutes,
                duration_minutes=duration_minutes,
            )
        now = timezone.now()
        state = cls._reset_state_if_needed(
            state=cls._get_state(username, portal_code),
            now=now,
            window_minutes=window_minutes,
        )
        locked_until = state.locked_until if state else None
        return LoginLockoutStatus(
            enabled=True,
            is_locked=bool(locked_until and locked_until > now),
            locked_until=locked_until,
            max_attempts=max_attempts,
            window_minutes=window_minutes,
            duration_minutes=duration_minutes,
            state=state,
        )

    @classmethod
    def build_lockout_message(cls, locked_until) -> str:
        if locked_until is None:
            return "Too many failed login attempts. Please try again later."
        remaining_seconds = max(int((locked_until - timezone.now()).total_seconds()), 0)
        remaining_minutes = max(1, (remaining_seconds + 59) // 60)
        return (
            f"Too many failed login attempts. Try again in about {remaining_minutes} minute(s), "
            "or use the password reset option if available."
        )

    @classmethod
    def register_failure(cls, *, username: str, portal_code: str, request=None) -> LoginLockoutStatus:
        normalized = cls.normalize_username(username)
        enabled, max_attempts, window_minutes, duration_minutes = cls._policy(normalized)
        if not enabled or not normalized:
            return LoginLockoutStatus(
                enabled=enabled,
                is_locked=False,
                locked_until=None,
                max_attempts=max_attempts,
                window_minutes=window_minutes,
                duration_minutes=duration_minutes,
            )

        now = timezone.now()
        state = cls._reset_state_if_needed(
            state=cls._get_state(normalized, portal_code),
            now=now,
            window_minutes=window_minutes,
        )
        if state is None:
            state = PortalLoginLockoutState(username=normalized, portal_code=(portal_code or "").upper())

        state.user = cls._resolve_user(normalized)
        if not state.window_started_at:
            state.window_started_at = now
        state.failed_attempt_count += 1
        state.last_failed_at = now
        state.last_ip = request.META.get("REMOTE_ADDR") if request else None

        just_locked = False
        if state.failed_attempt_count >= max_attempts:
            state.locked_until = now + timedelta(minutes=duration_minutes)
            just_locked = True

        state.save()

        if just_locked:
            AuditService.log_event(
                action="LOGIN_LOCKOUT_TRIGGERED",
                portal=(portal_code or "").upper(),
                entity_type="User",
                entity_id=state.user_id,
                actor=None,
                tenant=state.user.default_tenant_id if state.user_id else None,
                campus=state.user.default_campus_id if state.user_id else None,
                metadata={
                    "username": normalized,
                    "failed_attempt_count": state.failed_attempt_count,
                    "locked_until": state.locked_until.isoformat() if state.locked_until else None,
                },
                request=request,
            )

        return LoginLockoutStatus(
            enabled=True,
            is_locked=bool(state.locked_until and state.locked_until > now),
            locked_until=state.locked_until,
            max_attempts=max_attempts,
            window_minutes=window_minutes,
            duration_minutes=duration_minutes,
            state=state,
            just_locked=just_locked,
        )

    @classmethod
    def register_success(cls, *, username: str, portal_code: str) -> None:
        state = cls._get_state(username, portal_code)
        if state is None:
            return
        state.failed_attempt_count = 0
        state.window_started_at = None
        state.last_failed_at = None
        state.locked_until = None
        state.save(update_fields=["failed_attempt_count", "window_started_at", "last_failed_at", "locked_until", "updated_at"])

    @classmethod
    def log_blocked_attempt(cls, *, username: str, portal_code: str, request=None) -> None:
        normalized = cls.normalize_username(username)
        status = cls.get_status(normalized, portal_code)
        if not status.is_locked:
            return
        matched_user = cls._resolve_user(normalized)
        AuditService.log_event(
            action="LOGIN_LOCKOUT_BLOCKED",
            portal=(portal_code or "").upper(),
            entity_type="User",
            entity_id=matched_user.id if matched_user else None,
            actor=None,
            tenant=matched_user.default_tenant_id if matched_user else None,
            campus=matched_user.default_campus_id if matched_user else None,
            metadata={
                "username": normalized,
                "locked_until": status.locked_until.isoformat() if status.locked_until else None,
            },
            request=request,
        )


@dataclass
class LoginOtpResult:
    success: bool
    message: str = ""
    challenge: LoginOtpChallenge | None = None


class LoginOtpService:
    MAX_ATTEMPTS = 5

    @classmethod
    def is_enabled_for_user(cls, user) -> bool:
        return FeatureSettingsService.is_login_email_otp_enabled(
            tenant_id=getattr(user, "default_tenant_id", None),
            default=False,
        )

    @classmethod
    def _expiry_minutes_for_user(cls, user) -> int:
        return FeatureSettingsService.get_login_email_otp_expiry_minutes(
            tenant_id=getattr(user, "default_tenant_id", None),
            default=10,
        )

    @staticmethod
    def _generate_code() -> str:
        return f"{random.SystemRandom().randint(0, 999999):06d}"

    @staticmethod
    def _masked_email(email: str) -> str:
        local, _, domain = (email or "").partition("@")
        if not local or not domain:
            return email or ""
        if len(local) <= 2:
            masked_local = f"{local[:1]}*"
        else:
            masked_local = f"{local[:2]}{'*' * max(len(local) - 2, 2)}"
        return f"{masked_local}@{domain}"

    @classmethod
    def create_and_send(cls, *, request, user, portal_code: str) -> LoginOtpResult:
        email = (getattr(user, "email", "") or "").strip()
        if not email:
            return LoginOtpResult(
                success=False,
                message="Email verification is enabled, but this account has no registered email address. Please contact your administrator.",
            )

        code = cls._generate_code()
        challenge = LoginOtpChallenge.objects.create(
            user=user,
            portal_code=(portal_code or "").upper(),
            code_hash=make_password(code),
            sent_to_email=email,
            expires_at=timezone.now() + timedelta(minutes=cls._expiry_minutes_for_user(user)),
        )
        sent_count = cls._send_email(request=request, user=user, challenge=challenge, code=code)
        AuditService.log_event(
            action="LOGIN_OTP_SENT",
            portal=(portal_code or "").upper(),
            entity_type="LoginOtpChallenge",
            entity_id=challenge.id,
            actor=None,
            tenant=getattr(user, "default_tenant_id", None),
            campus=getattr(user, "default_campus_id", None),
            metadata={
                "username": user.username,
                "email": cls._masked_email(email),
                "sent": sent_count > 0,
                "expires_at": challenge.expires_at,
            },
            request=request,
        )
        if sent_count <= 0:
            return LoginOtpResult(
                success=False,
                message="EduGradesPro could not send the verification code. Please try again or contact your administrator.",
                challenge=challenge,
            )
        return LoginOtpResult(success=True, challenge=challenge)

    @classmethod
    def _send_email(cls, *, request, user, challenge: LoginOtpChallenge, code: str) -> int:
        subject = "NCBA EduGradesPro Login Verification"
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@edugradespro.local")
        logo_url = request.build_absolute_uri(f"{settings.MEDIA_URL}logos/egp_logo_official.png") if request else ""
        context = {
            "user": user,
            "portal_name": "Admin Portal" if challenge.portal_code == "ADMIN" else "Faculty Portal",
            "otp_code": code,
            "expires_at": challenge.expires_at,
            "expires_in_minutes": max(1, int(((challenge.expires_at - timezone.now()).total_seconds() + 59) // 60)),
            "logo_url": logo_url,
        }
        text_body = render_to_string("accounts/emails/login_otp.txt", context)
        html_body = render_to_string("accounts/emails/login_otp.html", context)
        message = EmailMultiAlternatives(subject=subject, body=text_body, from_email=from_email, to=[challenge.sent_to_email])
        message.attach_alternative(html_body, "text/html")
        return message.send(fail_silently=True)

    @classmethod
    def verify(cls, *, user, portal_code: str, code: str, request=None) -> LoginOtpResult:
        normalized_code = str(code or "").strip().replace(" ", "")
        challenge = (
            LoginOtpChallenge.objects.filter(
                user=user,
                portal_code=(portal_code or "").upper(),
                consumed_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if not challenge:
            return LoginOtpResult(success=False, message="No active verification code was found. Please sign in again.")
        if challenge.expires_at <= timezone.now():
            return LoginOtpResult(success=False, message="This verification code has expired. Please sign in again.", challenge=challenge)
        if challenge.attempt_count >= cls.MAX_ATTEMPTS:
            return LoginOtpResult(
                success=False,
                message="Too many incorrect verification attempts. Please sign in again to receive a new code.",
                challenge=challenge,
            )
        if not normalized_code or not check_password(normalized_code, challenge.code_hash):
            challenge.attempt_count += 1
            challenge.last_attempt_at = timezone.now()
            challenge.save(update_fields=["attempt_count", "last_attempt_at"])
            AuditService.log_event(
                action="LOGIN_OTP_FAILURE",
                portal=(portal_code or "").upper(),
                entity_type="LoginOtpChallenge",
                entity_id=challenge.id,
                actor=None,
                tenant=getattr(user, "default_tenant_id", None),
                campus=getattr(user, "default_campus_id", None),
                metadata={"username": user.username, "attempt_count": challenge.attempt_count},
                request=request,
            )
            return LoginOtpResult(success=False, message="The verification code is incorrect.", challenge=challenge)

        challenge.consumed_at = timezone.now()
        challenge.last_attempt_at = challenge.consumed_at
        challenge.save(update_fields=["consumed_at", "last_attempt_at"])
        AuditService.log_event(
            action="LOGIN_OTP_SUCCESS",
            portal=(portal_code or "").upper(),
            entity_type="LoginOtpChallenge",
            entity_id=challenge.id,
            actor=user,
            tenant=getattr(user, "default_tenant_id", None),
            campus=getattr(user, "default_campus_id", None),
            metadata={"username": user.username},
            request=request,
        )
        return LoginOtpResult(success=True, challenge=challenge)


@dataclass
class SignatureImagePayload:
    image_bytes: bytes
    mime_type: str
    image_format: str
    width: int
    height: int
    file_size_bytes: int
    content_sha256: str


class UserSignatureService:
    MAX_UPLOAD_BYTES = 2 * 1024 * 1024
    ALLOWED_FORMATS = {"PNG", "JPEG", "JPG"}

    @classmethod
    def _encryption_key(cls) -> bytes:
        raw = (os.getenv("SIGNATURE_ENCRYPTION_KEY") or "").strip()
        if raw:
            try:
                decoded = base64.urlsafe_b64decode(raw.encode("utf-8"))
            except Exception as exc:
                raise ValidationError("SIGNATURE_ENCRYPTION_KEY is not a valid base64 value.") from exc
            if len(decoded) != 32:
                raise ValidationError("SIGNATURE_ENCRYPTION_KEY must decode to exactly 32 bytes.")
            return decoded
        return hashlib.sha256((getattr(settings, "SECRET_KEY", "") or "edugradespro-signature-key").encode("utf-8")).digest()

    @classmethod
    def _normalize_image(cls, uploaded_file) -> SignatureImagePayload:
        raw_bytes = uploaded_file.read()
        if not raw_bytes:
            raise ValidationError("Upload a signature image file first.")
        if len(raw_bytes) > cls.MAX_UPLOAD_BYTES:
            raise ValidationError("Signature image must be 2 MB or smaller.")

        try:
            image = Image.open(BytesIO(raw_bytes))
            image.load()
        except Exception as exc:
            raise ValidationError("Uploaded file is not a valid image.") from exc

        image_format = (image.format or "").upper()
        if image_format not in cls.ALLOWED_FORMATS:
            raise ValidationError("Use PNG or JPG/JPEG for the signature image.")

        normalized = image.convert("RGBA")
        output = BytesIO()
        normalized.save(output, format="PNG")
        png_bytes = output.getvalue()
        return SignatureImagePayload(
            image_bytes=png_bytes,
            mime_type="image/png",
            image_format="PNG",
            width=normalized.width,
            height=normalized.height,
            file_size_bytes=len(png_bytes),
            content_sha256=hashlib.sha256(png_bytes).hexdigest(),
        )

    @classmethod
    def store_signature(cls, *, user, uploaded_file, actor):
        payload = cls._normalize_image(uploaded_file)
        nonce = os.urandom(12)
        aesgcm = AESGCM(cls._encryption_key())
        encrypted_blob = aesgcm.encrypt(nonce, payload.image_bytes, None)
        credential, _created = UserSignatureCredential.objects.get_or_create(user=user)
        credential.encrypted_blob = encrypted_blob
        credential.encryption_nonce = nonce
        credential.original_filename = str(getattr(uploaded_file, "name", "") or "signature.png")
        credential.mime_type = payload.mime_type
        credential.image_format = payload.image_format
        credential.image_width = payload.width
        credential.image_height = payload.height
        credential.file_size_bytes = payload.file_size_bytes
        credential.content_sha256 = payload.content_sha256
        credential.uploaded_at = timezone.now()
        credential.uploaded_by_user = actor
        credential.is_enabled = True
        credential.save()
        return credential

    @classmethod
    def clear_signature(cls, *, user):
        credential, _created = UserSignatureCredential.objects.get_or_create(user=user)
        credential.encrypted_blob = None
        credential.encryption_nonce = None
        credential.original_filename = None
        credential.mime_type = None
        credential.image_format = None
        credential.image_width = None
        credential.image_height = None
        credential.file_size_bytes = 0
        credential.content_sha256 = None
        credential.is_enabled = False
        credential.save()
        return credential

    @classmethod
    def get_active_credential(cls, *, user):
        credential = getattr(user, "signature_credential", None)
        if credential and credential.has_signature:
            return credential
        return None

    @classmethod
    def decrypt_signature_bytes(cls, *, credential: UserSignatureCredential) -> bytes:
        if not credential or not credential.has_signature:
            raise ValidationError("No active signature is stored for this user.")
        aesgcm = AESGCM(cls._encryption_key())
        return aesgcm.decrypt(bytes(credential.encryption_nonce), bytes(credential.encrypted_blob), None)

    @classmethod
    def signature_image_bytes_for_user(cls, *, user):
        credential = cls.get_active_credential(user=user)
        if not credential:
            return None
        return cls.decrypt_signature_bytes(credential=credential)

    @classmethod
    def log_signature_usage(
        cls,
        *,
        user,
        document_type: str,
        document_reference: str,
        usage_role: str,
        actor,
        portal_code: str,
        metadata: dict | None = None,
    ):
        credential = cls.get_active_credential(user=user)
        if credential:
            credential.last_used_at = timezone.now()
            credential.save(update_fields=["last_used_at", "updated_at"])
        return UserSignatureUsageLog.objects.create(
            user=user,
            document_type=document_type,
            document_reference=document_reference,
            usage_role=usage_role,
            actor=actor,
            portal_code=portal_code,
            metadata_json=metadata or {},
        )
