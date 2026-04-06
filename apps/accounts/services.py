from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.accounts.models import PortalLoginLockoutState
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
