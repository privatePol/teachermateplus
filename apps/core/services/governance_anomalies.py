from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.auditlog.models import AuditLog


class GovernanceAnomalyService:
    """Lightweight, non-blocking anomaly flags for governance audit events."""

    SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
    CORRECTION_DAILY_THRESHOLD = 10
    REOPEN_DAILY_THRESHOLD = 3
    MANY_USER_PERMISSION_THRESHOLD = 10

    @classmethod
    def evaluate_event(
        cls,
        *,
        action: str,
        portal: str,
        entity_type: str,
        entity_id=None,
        actor=None,
        tenant=None,
        campus=None,
        before_data=None,
        after_data=None,
        metadata: dict | None = None,
        now=None,
    ) -> list[dict[str, str]]:
        metadata = metadata or {}
        now = now or timezone.now()
        flags: list[dict[str, str]] = []
        flags.extend(
            cls._period_reopen_flags(
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                actor=actor,
                tenant=tenant,
                campus=campus,
                metadata=metadata,
                now=now,
            )
        )
        flags.extend(cls._template_hotfix_flags(action=action, entity_type=entity_type, metadata=metadata))
        flags.extend(
            cls._correction_flags(
                action=action,
                entity_type=entity_type,
                actor=actor,
                tenant=tenant,
                campus=campus,
                before_data=before_data,
                after_data=after_data,
                metadata=metadata,
                now=now,
            )
        )
        flags.extend(cls._role_permission_flags(entity_type=entity_type, entity_id=entity_id, actor=actor, metadata=metadata))
        flags.extend(cls._data_reset_flags(action=action, entity_type=entity_type, metadata=metadata))
        return cls._dedupe(flags)

    @classmethod
    def max_severity(cls, flags: list[dict[str, str]]) -> str:
        if not flags:
            return ""
        return max(flags, key=lambda item: cls.SEVERITY_ORDER.get(item.get("severity", "low"), 0)).get("severity", "low")

    @staticmethod
    def _flag(rule_code: str, severity: str, message: str) -> dict[str, str]:
        return {"rule_code": rule_code, "severity": severity, "message": message}

    @classmethod
    def _dedupe(cls, flags):
        seen = set()
        unique = []
        for flag in flags:
            code = flag.get("rule_code")
            if code in seen:
                continue
            seen.add(code)
            unique.append(flag)
        return unique

    @staticmethod
    def _id(value):
        if hasattr(value, "id"):
            return value.id
        return value

    @classmethod
    def _actor_day_count(cls, *, actor, action: str, entity_type: str, now, tenant=None, campus=None) -> int:
        if not getattr(actor, "is_authenticated", False):
            return 1
        start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        qs = AuditLog.objects.filter(
            actor_user=actor,
            action=action,
            entity_type=entity_type,
            created_at__gte=start,
            created_at__lt=end,
        )
        tenant_id = cls._id(tenant)
        campus_id = cls._id(campus)
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        if campus_id:
            qs = qs.filter(campus_id=campus_id)
        return qs.count() + 1

    @classmethod
    def _period_reopen_flags(cls, *, action, entity_type, entity_id, actor, tenant, campus, metadata, now):
        if entity_type != "GradingPeriodLock" or action != "REOPEN":
            return []
        impact = metadata.get("impact_summary") or {}
        flags = []
        if cls._actor_day_count(actor=actor, action=action, entity_type=entity_type, now=now, tenant=tenant, campus=campus) > cls.REOPEN_DAILY_THRESHOLD:
            username = getattr(actor, "username", "unknown")
            flags.append(cls._flag("PERIOD_REOPEN_HIGH_DAILY_COUNT", "medium", f"High reopen frequency by {username}."))

        course_offering_id = impact.get("course_offering_id")
        if course_offering_id:
            previous_count = AuditLog.objects.filter(
                action="REOPEN",
                entity_type="GradingPeriodLock",
                metadata_json__impact_summary__course_offering_id=course_offering_id,
            ).count()
            if previous_count >= 1:
                flags.append(cls._flag("PERIOD_REOPEN_SAME_CLASS_REPEAT", "medium", "Same class has been reopened multiple times."))

        deadline_at = cls._parse_datetime(impact.get("deadline_at"))
        if deadline_at and deadline_at < now:
            flags.append(cls._flag("PERIOD_REOPEN_AFTER_DEADLINE", "high", "Period was reopened after the configured deadline."))

        if int(impact.get("target_offering_count") or 0) > 1:
            flags.append(cls._flag("PERIOD_REOPEN_MULTI_CLASS", "medium", "Reopen affects more than one class."))
        return flags

    @classmethod
    def _template_hotfix_flags(cls, *, action, entity_type, metadata):
        if entity_type != "TemplateHotfixRequest" or action != "APPROVE":
            return []
        impact = metadata.get("impact_summary") or {}
        if not metadata.get("critical_action"):
            return []
        flags = []
        if int(impact.get("target_offering_count") or 0) > 10:
            flags.append(cls._flag("HOTFIX_LARGE_SCOPE", "high", "Template hotfix affects more than 10 offerings."))
        if int(impact.get("campus_count") or 0) > 1:
            flags.append(cls._flag("HOTFIX_MULTI_CAMPUS", "high", "Template hotfix spans multiple campuses."))
        if int(impact.get("near_or_after_deadline_offering_count") or 0) > 0:
            flags.append(cls._flag("HOTFIX_NEAR_OR_AFTER_DEADLINE", "medium", "Template hotfix affects offerings near or after a submission deadline."))
        if int(impact.get("submitted_or_reopened_offering_count") or 0) > 0:
            flags.append(cls._flag("HOTFIX_PARTIAL_RESULT", "medium", "Template hotfix may be partial because submitted or reopened classes were detected."))
        return flags

    @classmethod
    def _correction_flags(cls, *, action, entity_type, actor, tenant, campus, before_data, after_data, metadata, now):
        flags = []
        if entity_type == "GradeCorrectionRequest" and action == "APPROVE":
            if cls._actor_day_count(actor=actor, action=action, entity_type=entity_type, now=now, tenant=tenant, campus=campus) > cls.CORRECTION_DAILY_THRESHOLD:
                username = getattr(actor, "username", "unknown")
                flags.append(cls._flag("CORRECTION_HIGH_DAILY_APPROVAL_COUNT", "medium", f"Many corrections processed today by {username}."))

        reason = str((metadata or {}).get("reason") or "").upper()
        if entity_type in {"StudentPeriodGrade", "StudentFinalGrade"} and action == "RECOMPUTE" and "CORRECTION" in reason:
            if entity_type == "StudentFinalGrade":
                flags.append(cls._flag("CORRECTION_FINAL_GRADE_CHANGED", "high", "Correction changed a final-grade record."))
            if cls._changed_fail_to_pass(entity_type=entity_type, before_data=before_data, after_data=after_data, threshold=metadata.get("passing_threshold")):
                flags.append(cls._flag("CORRECTION_FAIL_TO_PASS", "high", "Correction changed a grade from failing to passing."))
            student_id = metadata.get("student_id")
            offering_id = metadata.get("offering_id")
            if student_id and offering_id:
                previous_count = AuditLog.objects.filter(
                    action="RECOMPUTE",
                    entity_type__in=["StudentPeriodGrade", "StudentFinalGrade"],
                    metadata_json__student_id=student_id,
                    metadata_json__offering_id=offering_id,
                    metadata_json__reason__icontains="CORRECTION",
                ).count()
                if previous_count >= 1:
                    flags.append(cls._flag("CORRECTION_REPEAT_STUDENT_TERM", "medium", "Same student has multiple correction-related grade changes in this class/term."))
        return flags

    @classmethod
    def _role_permission_flags(cls, *, entity_type, entity_id, actor, metadata):
        if entity_type != "RolePermission":
            return []
        impact = metadata.get("impact_summary") or {}
        flags = []
        critical_added = impact.get("critical_added_permission_codes") or metadata.get("critical_added_permission_codes") or []
        if critical_added:
            flags.append(cls._flag("ROLE_CRITICAL_PERMISSION_GRANTED", "high", "Critical permission was granted to a role."))
        try:
            from apps.rbac.models import UserRole

            if entity_id and getattr(actor, "is_authenticated", False):
                if UserRole.objects.filter(user=actor, role_id=entity_id, is_active=True).exists():
                    flags.append(cls._flag("ROLE_SELF_PERMISSION_CHANGE", "high", "User modified permissions for a role assigned to their own account."))
        except Exception:
            pass
        affected = int(impact.get("affected_active_user_count") or metadata.get("affected_user_count") or 0)
        if affected >= cls.MANY_USER_PERMISSION_THRESHOLD:
            flags.append(cls._flag("ROLE_PERMISSION_MANY_USERS", "medium", "Permission change affects many active users."))
        return flags

    @classmethod
    def _data_reset_flags(cls, *, action, entity_type, metadata):
        if entity_type != "ActualDataReset" or action != "RESET":
            return []
        flags = []
        env_name = str(
            getattr(settings, "DJANGO_ENV", "")
            or getattr(settings, "ENVIRONMENT", "")
            or getattr(settings, "APP_ENV", "")
        ).lower()
        safe_envs = {"", "local", "dev", "development", "staging", "test", "testing"}
        if not settings.DEBUG and env_name not in safe_envs:
            flags.append(cls._flag("DATA_RESET_OUTSIDE_LOCAL_STAGING", "high", "Actual Data Reset ran outside local/staging mode."))
        if metadata.get("audit_export_path") or metadata.get("audit_export_count") is not None:
            flags.append(cls._flag("DATA_RESET_INCLUDES_AUDIT_LOGS", "medium", "Actual Data Reset includes audit logs; export preservation should be reviewed."))
        if not metadata.get("backup_path"):
            flags.append(cls._flag("DATA_RESET_NO_RECENT_BACKUP", "high", "Actual Data Reset did not record a backup path."))
        return flags

    @staticmethod
    def _parse_datetime(value):
        if not value:
            return None
        if hasattr(value, "tzinfo"):
            return value
        parsed = parse_datetime(str(value))
        if parsed and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    @classmethod
    def _changed_fail_to_pass(cls, *, entity_type, before_data, after_data, threshold=None) -> bool:
        before_data = before_data or {}
        after_data = after_data or {}
        field = "final_grade" if entity_type == "StudentFinalGrade" else "period_grade"
        before_value = cls._decimal_or_none(before_data.get(field))
        after_value = cls._decimal_or_none(after_data.get(field))
        threshold_value = cls._decimal_or_none(threshold) or Decimal("75")
        return before_value is not None and after_value is not None and before_value < threshold_value <= after_value

    @staticmethod
    def _decimal_or_none(value: Any):
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None
