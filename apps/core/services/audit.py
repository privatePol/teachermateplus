from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from django.urls import resolve

from apps.auditlog.models import AuditLog


class AuditService:
    @classmethod
    def _json_safe(cls, value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, dict):
            return {str(key): cls._json_safe(val) for key, val in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe(item) for item in value]
        if hasattr(value, "pk"):
            return value.pk
        return str(value)

    @staticmethod
    def _request_metadata(request):
        route_name = None
        if request:
            try:
                route_name = resolve(request.path_info).view_name
            except Exception:
                route_name = None
        return {
            "route_name": route_name,
            "http_method": request.method if request else None,
            "ip_address": request.META.get("REMOTE_ADDR") if request else None,
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:512] if request else None,
        }

    @classmethod
    def log_event(
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
        metadata=None,
        request=None,
    ):
        req_meta = cls._request_metadata(request)
        actor_user = actor if getattr(actor, "is_authenticated", False) else None
        tenant_obj = tenant
        campus_obj = campus
        if tenant_obj is None and request and hasattr(request, "scope"):
            tenant_obj = request.scope.get("tenant_id")
        if campus_obj is None and request and hasattr(request, "scope"):
            campus_obj = request.scope.get("campus_id")
        metadata_payload = dict(metadata or {})
        try:
            from apps.core.services.governance_anomalies import GovernanceAnomalyService

            anomaly_flags = GovernanceAnomalyService.evaluate_event(
                action=action,
                portal=portal,
                entity_type=entity_type,
                entity_id=entity_id,
                actor=actor_user,
                tenant=tenant_obj,
                campus=campus_obj,
                before_data=before_data,
                after_data=after_data,
                metadata=metadata_payload,
            )
            if anomaly_flags:
                metadata_payload["anomaly_flags_json"] = anomaly_flags
                metadata_payload["has_anomaly_flags"] = True
                metadata_payload["max_anomaly_severity"] = GovernanceAnomalyService.max_severity(anomaly_flags)
        except Exception:
            # Audit logging must never block the operational action being audited.
            pass

        return AuditLog.objects.create(
            actor_user=actor_user,
            portal=portal,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            tenant_id=tenant_obj.id if hasattr(tenant_obj, "id") else tenant_obj,
            campus_id=campus_obj.id if hasattr(campus_obj, "id") else campus_obj,
            route_name=req_meta["route_name"],
            http_method=req_meta["http_method"],
            ip_address=req_meta["ip_address"],
            user_agent=req_meta["user_agent"],
            before_json=cls._json_safe(before_data),
            after_json=cls._json_safe(after_data),
            metadata_json=cls._json_safe(metadata_payload),
        )

    @classmethod
    def log_login_success(cls, request, user, portal: str):
        return cls.log_event(
            action="LOGIN_SUCCESS",
            portal=portal,
            entity_type="User",
            entity_id=user.id,
            actor=user,
            metadata={"username": user.username},
            request=request,
        )

    @classmethod
    def log_login_failure(cls, request, username: str, portal: str):
        return cls.log_event(
            action="LOGIN_FAILURE",
            portal=portal,
            entity_type="User",
            entity_id=None,
            actor=None,
            metadata={"username": username},
            request=request,
        )
