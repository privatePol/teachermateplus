from __future__ import annotations

import json

from apps.tenants.models import SystemSetting


class SystemSettingService:
    @staticmethod
    def _cast_value(value: str, value_type: str):
        value_type = (value_type or "STRING").upper()
        if value_type == "INT":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if value_type == "BOOL":
            return str(value).lower() in {"1", "true", "yes", "on"}
        if value_type == "JSON":
            try:
                return json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return None
        return value

    @classmethod
    def get(cls, key: str, tenant_id: int | None = None, default=None):
        setting = None
        if tenant_id is not None:
            setting = SystemSetting.objects.filter(
                setting_key=key, is_active=True, tenant_id=tenant_id
            ).first()
        if setting is None:
            setting = SystemSetting.objects.filter(
                setting_key=key, is_active=True, tenant__isnull=True
            ).first()
        if setting is None:
            return default
        return cls._cast_value(setting.setting_value, setting.value_type)

    @staticmethod
    def _stringify(value):
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    @classmethod
    def set(
        cls,
        key: str,
        value,
        *,
        tenant_id: int | None = None,
        value_type: str = SystemSetting.ValueType.STRING,
        is_active: bool = True,
        description: str | None = None,
    ):
        # `description` is accepted for forward compatibility with callers,
        # but the current SystemSetting model does not persist it.
        setting, _ = SystemSetting.objects.update_or_create(
            tenant_id=tenant_id,
            setting_key=key,
            defaults={
                "setting_value": cls._stringify(value),
                "value_type": value_type,
                "is_active": is_active,
            },
        )
        return setting
