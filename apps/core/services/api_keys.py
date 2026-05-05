from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.tenants.models import TenantApiKey


@dataclass
class ApiAuthResult:
    ok: bool
    tenant_api_key: Optional[TenantApiKey] = None
    legacy_token: bool = False
    error: str = ""
    status_code: int = 401
    rate_limited: bool = False
    retry_after_seconds: int = 60


class TenantApiKeyService:
    TOKEN_PREFIX = "egp_sis"

    @classmethod
    def create_key(cls, *, tenant, name: str, purpose: str = TenantApiKey.Purpose.SIS, created_by_user=None, expires_at=None):
        prefix = secrets.token_hex(6)
        secret = secrets.token_urlsafe(32)
        raw_token = f"{cls.TOKEN_PREFIX}_{prefix}_{secret}"
        row = TenantApiKey.objects.create(
            tenant=tenant,
            name=name,
            purpose=purpose,
            key_prefix=prefix,
            key_hash=cls.hash_token(raw_token),
            created_by_user=created_by_user,
            expires_at=expires_at,
        )
        return row, raw_token

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    def prefix_from_token(cls, raw_token: str) -> str:
        parts = (raw_token or "").split("_")
        if len(parts) >= 4 and "_".join(parts[:2]) == cls.TOKEN_PREFIX:
            return parts[2]
        return ""

    @classmethod
    def authenticate_sis_token(cls, raw_token: str) -> ApiAuthResult:
        raw_token = (raw_token or "").strip()
        if not raw_token:
            return ApiAuthResult(ok=False, error="Unauthorized. Provide a valid API token.")
        prefix = cls.prefix_from_token(raw_token)
        if not prefix:
            return ApiAuthResult(ok=False, error="Unauthorized. Provide a valid tenant API key.")
        row = (
            TenantApiKey.objects.select_related("tenant")
            .filter(
                key_prefix=prefix,
                purpose=TenantApiKey.Purpose.SIS,
                is_active=True,
                revoked_at__isnull=True,
            )
            .first()
        )
        if not row or not secrets.compare_digest(row.key_hash, cls.hash_token(raw_token)):
            return ApiAuthResult(ok=False, error="Unauthorized. Provide a valid tenant API key.")
        if row.expires_at and row.expires_at <= timezone.now():
            return ApiAuthResult(ok=False, error="API key has expired.")
        if not row.tenant.is_active:
            return ApiAuthResult(ok=False, error="API key tenant is inactive.")
        return ApiAuthResult(ok=True, tenant_api_key=row)

    @classmethod
    def mark_used(cls, row: TenantApiKey):
        TenantApiKey.objects.filter(id=row.id).update(last_used_at=timezone.now())

    @classmethod
    def revoke(cls, row: TenantApiKey):
        TenantApiKey.objects.filter(id=row.id).update(is_active=False, revoked_at=timezone.now())


class ApiRateLimitService:
    @staticmethod
    def _identity_for_request(request, *, api_key: TenantApiKey | None = None, legacy_token: bool = False) -> str:
        if api_key:
            return f"key:{api_key.key_prefix}"
        ip_address = request.META.get("REMOTE_ADDR") or "unknown"
        return f"{'legacy' if legacy_token else 'ip'}:{ip_address}"

    @classmethod
    def check(cls, request, *, api_key: TenantApiKey | None = None, legacy_token: bool = False) -> ApiAuthResult:
        limit = int(getattr(settings, "SIS_API_RATE_LIMIT_PER_MINUTE", 60) or 60)
        if limit <= 0:
            return ApiAuthResult(ok=True)
        identity = cls._identity_for_request(request, api_key=api_key, legacy_token=legacy_token)
        minute_bucket = timezone.now().strftime("%Y%m%d%H%M")
        cache_key = f"sis-api-rate:{identity}:{minute_bucket}"
        added = cache.add(cache_key, 1, timeout=65)
        if added:
            count = 1
        else:
            try:
                count = cache.incr(cache_key)
            except ValueError:
                cache.set(cache_key, 1, timeout=65)
                count = 1
        if count > limit:
            return ApiAuthResult(
                ok=False,
                error="Rate limit exceeded. Try again later.",
                status_code=429,
                rate_limited=True,
                retry_after_seconds=60,
            )
        return ApiAuthResult(ok=True)
