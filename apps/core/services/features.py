from __future__ import annotations

from apps.core.services.settings import SystemSettingService


class FeatureSettingsService:
    CORRECTION_OFFICIAL_REPORT_ENABLED_KEY = "FEATURE_CORRECTION_OFFICIAL_REPORT_ENABLED"
    CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY = "FEATURE_CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED"
    CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES_KEY = "FEATURE_CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES"
    CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED_KEY = "FEATURE_CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED"
    CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES_KEY = "FEATURE_CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES"
    CORRECTION_REGISTRAR_DEFAULT_RECIPIENTS_KEY = "FEATURE_CORRECTION_REGISTRAR_DEFAULT_RECIPIENTS"
    CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS_KEY = "FEATURE_CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS"

    @classmethod
    def is_correction_official_report_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.CORRECTION_OFFICIAL_REPORT_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def is_correction_submission_approval_email_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_correction_submission_approval_email_role_codes(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES_KEY,
            tenant_id=tenant_id,
            default=default or [],
        )
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def is_correction_registrar_auto_email_enabled(cls, *, tenant_id: int | None, default: bool = False) -> bool:
        return bool(
            SystemSettingService.get(
                cls.CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED_KEY,
                tenant_id=tenant_id,
                default=default,
            )
        )

    @classmethod
    def get_correction_registrar_auto_email_role_codes(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES_KEY,
            tenant_id=tenant_id,
            default=default or [],
        )
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def get_correction_registrar_default_recipients(
        cls,
        *,
        tenant_id: int | None,
        default: list[str] | None = None,
    ) -> list[str]:
        value = SystemSettingService.get(
            cls.CORRECTION_REGISTRAR_DEFAULT_RECIPIENTS_KEY,
            tenant_id=tenant_id,
            default=default or [],
        )
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    @classmethod
    def get_correction_registrar_campus_recipients(
        cls,
        *,
        tenant_id: int | None,
        default: dict[str, list[str]] | None = None,
    ) -> dict[str, list[str]]:
        value = SystemSettingService.get(
            cls.CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS_KEY,
            tenant_id=tenant_id,
            default=default or {},
        )
        if not isinstance(value, dict):
            return {}
        normalized = {}
        for campus_id, recipients in value.items():
            if isinstance(recipients, list):
                emails = [str(item).strip() for item in recipients if str(item).strip()]
            else:
                emails = []
            normalized[str(campus_id)] = emails
        return normalized
