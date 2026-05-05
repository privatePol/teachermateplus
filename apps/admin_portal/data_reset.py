from __future__ import annotations

import json
from pathlib import Path
from shutil import copy2

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.contrib.sessions.models import Session
from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from apps.accounts.models import (
    LoginOtpChallenge,
    PortalLoginLockoutState,
    User,
    UserDeactivationSchedule,
    UserSignatureCredential,
    UserSignatureUsageLog,
)
from apps.academics.models import (
    AcademicYear,
    ActiveGradingPeriodSetting,
    Course,
    CourseOffering,
    FacultyAssignment,
    Section,
    TenantTermGradingPeriod,
    Term,
)
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    FacultyFinalClearanceReport,
    GradeActivity,
    GradeCorrectionApprovalStep,
    GradeCorrectionAttachment,
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeCorrectionUnlockWindow,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateApprovalStep,
    GradingTemplateApprovalWorkflow,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
    TemplateHotfixRequest,
    TemplateHotfixWorkflowStep,
    TenantGradingProfile,
)
from apps.imports.models import ImportBatch, ImportBatchRow
from apps.notifications.models import (
    FacultyMemo,
    FacultyReminder,
    FacultyReminderEmailQueue,
    NotificationQueue,
    SubmissionNonComplianceNotice,
)
from apps.predictions.models import (
    PredictionDirtyQueue,
    PredictionSettingSnapshot,
    PredictionSnapshot,
    PredictionSummarySnapshot,
    PredictionViewLog,
    PredictionWhatIfDraft,
)
from apps.rbac.models import UserPermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant


class ActualDataResetService:
    CONFIRMATION_PHRASE = "RESET ACTUAL DATA"

    @staticmethod
    def is_production_environment() -> bool:
        return str(getattr(settings, "DJANGO_ENV", "")).lower() == "production"

    @classmethod
    def production_safety_error(cls, user=None) -> str:
        if not cls.is_production_environment():
            return ""
        if not getattr(settings, "ACTUAL_DATA_RESET_ALLOW_PRODUCTION", False):
            return "Actual Data Reset is disabled in production by default."
        if user is not None and not getattr(user, "is_superuser", False):
            return "Actual Data Reset in production requires a superadmin account."
        if not getattr(settings, "MAINTENANCE_MODE", False):
            return "Actual Data Reset in production requires MAINTENANCE_MODE=True."
        return ""

    KEPT_TABLES = (
        ("users", "User accounts are kept, but default tenant/campus/department values are cleared."),
        ("roles", "Role definitions are kept."),
        ("permissions", "Permission definitions are kept."),
        ("role_permissions", "Role-to-permission mappings are kept."),
        ("menu_groups", "Admin/Faculty menu group configuration is kept."),
        ("menu_items", "Menu item configuration is kept."),
        ("menu_item_permissions", "Menu visibility permission mappings are kept."),
        ("system_settings", "Only global settings are kept; tenant-scoped settings are deleted."),
        ("django_migrations", "Migration history is kept."),
        ("django_content_type", "Django content-type registry is kept."),
        ("auth_permission", "Django built-in permissions are kept."),
    )

    DELETE_PREVIEW = (
        ("tenant_setup", "Tenant / Campus / Department / Program", (Tenant, Campus, Department, Program)),
        ("academic_setup", "Academic Year / Term / Course / Section / Offering", (AcademicYear, Term, Course, Section, CourseOffering)),
        ("grading_setup", "Grading Templates / Profiles / Period Settings", (
            GradingTemplate,
            GradingTemplatePeriod,
            GradingTemplateComponent,
            GradingTemplateSubcomponent,
            GradingTemplateDetail,
            TenantGradingProfile,
            TenantTermGradingPeriod,
            ActiveGradingPeriodSetting,
            CourseTemplateAssignment,
            CourseBaseValueOverride,
            CorrectionApprovalRouteRule,
        )),
        ("people_and_loading", "Students / Enrollment / Faculty Loading", (Student, Enrollment, FacultyAssignment)),
        ("grading_records", "Grades / Activities / Submissions / Corrections / Attendance", (
            GradeActivity,
            StudentActivityScore,
            StudentPeriodGrade,
            StudentFinalGrade,
            GradeSubmission,
            GradeSubmissionReopenRequest,
            GradeCorrectionRequest,
            GradeCorrectionApprovalStep,
            GradeCorrectionRequestItem,
            GradeCorrectionAttachment,
            GradeCorrectionUnlockWindow,
            GradingPeriodLock,
            FacultyFinalClearanceReport,
            AttendanceSession,
            AttendanceRecord,
        )),
        ("imports_predictions_notifications", "Imports / Predictions / Notifications", (
            ImportBatch,
            ImportBatchRow,
            PredictionSettingSnapshot,
            PredictionSnapshot,
            PredictionSummarySnapshot,
            PredictionDirtyQueue,
            PredictionWhatIfDraft,
            PredictionViewLog,
            FacultyReminder,
            FacultyReminderEmailQueue,
            FacultyMemo,
            NotificationQueue,
            SubmissionNonComplianceNotice,
        )),
        ("user_scoped_state", "Scoped User State / Sessions / Audit Trail", (
            UserRole,
            UserPermission,
            UserSignatureCredential,
            UserSignatureUsageLog,
            UserDeactivationSchedule,
            PortalLoginLockoutState,
            LoginOtpChallenge,
            Session,
            AuditLog,
        )),
    )

    DELETE_ORDER = (
        Session,
        LoginOtpChallenge,
        PortalLoginLockoutState,
        UserDeactivationSchedule,
        UserSignatureUsageLog,
        UserSignatureCredential,
        AuditLog,
        PredictionViewLog,
        PredictionWhatIfDraft,
        PredictionDirtyQueue,
        PredictionSummarySnapshot,
        PredictionSnapshot,
        PredictionSettingSnapshot,
        NotificationQueue,
        FacultyReminderEmailQueue,
        FacultyReminder,
        FacultyMemo,
        SubmissionNonComplianceNotice,
        AttendanceRecord,
        AttendanceSession,
        GradeCorrectionApprovalStep,
        GradeCorrectionRequestItem,
        GradeCorrectionAttachment,
        GradeCorrectionUnlockWindow,
        GradeCorrectionRequest,
        GradeSubmissionReopenRequest,
        StudentActivityScore,
        GradeActivity,
        StudentPeriodGrade,
        StudentFinalGrade,
        GradeSubmission,
        FacultyFinalClearanceReport,
        GradingPeriodLock,
        ImportBatchRow,
        ImportBatch,
        Enrollment,
        FacultyAssignment,
        CourseOffering,
        TemplateHotfixWorkflowStep,
        TemplateHotfixRequest,
        GradingTemplateApprovalStep,
        GradingTemplateApprovalWorkflow,
        CourseTemplateAssignment,
        CourseBaseValueOverride,
        TenantGradingProfile,
        CorrectionApprovalRouteRule,
        ActiveGradingPeriodSetting,
        TenantTermGradingPeriod,
        GradingTemplateDetail,
        GradingTemplateSubcomponent,
        GradingTemplateComponent,
        GradingTemplatePeriod,
        GradingTemplate,
        UserPermission,
        UserRole,
        Student,
        Section,
        Course,
        Program,
        Department,
        Campus,
        Term,
        AcademicYear,
        Tenant,
    )

    @classmethod
    def preview(cls):
        groups = []
        for key, label, models in cls.DELETE_PREVIEW:
            tables = []
            total = 0
            for model in models:
                count = model.objects.count()
                total += count
                tables.append(
                    {
                        "label": model._meta.verbose_name_plural.title(),
                        "table": model._meta.db_table,
                        "count": count,
                    }
                )
            groups.append({"key": key, "label": label, "total": total, "tables": tables})
        global_settings_count = SystemSetting.objects.filter(tenant__isnull=True).count()
        tenant_settings_count = SystemSetting.objects.filter(tenant__isnull=False).count()
        users_count = User.objects.count()
        return {
            "groups": groups,
            "delete_total": sum(group["total"] for group in groups) + tenant_settings_count,
            "tenant_settings_count": tenant_settings_count,
            "global_settings_count": global_settings_count,
            "users_count": users_count,
            "kept_tables": cls.KEPT_TABLES,
            "confirmation_phrase": cls.CONFIRMATION_PHRASE,
        }

    @classmethod
    def reset(cls, *, preserve_session_key: str | None = None):
        safety_error = cls.production_safety_error()
        if safety_error:
            raise ValidationError(safety_error)
        backup_path = cls._backup_database()
        audit_export = cls.export_audit_logs()
        backup_validation = cls.validate_backup_artifact(backup_path)
        export_validation = cls.validate_audit_export(audit_export)
        if cls.is_production_environment() and not backup_validation["ok"]:
            raise ValidationError(backup_validation["message"])
        if not export_validation["ok"]:
            raise ValidationError(export_validation["message"])
        file_paths = cls._collect_upload_paths()
        deleted = []

        with transaction.atomic():
            User.objects.update(default_tenant=None, default_campus=None, default_department=None)

            tenant_settings_total = cls._delete_queryset(SystemSetting.objects.filter(tenant__isnull=False))
            if tenant_settings_total:
                deleted.append({"table": "system_settings tenant-scoped", "count": tenant_settings_total})

            for model in cls.DELETE_ORDER:
                queryset = model.objects.all()
                if model is Session and preserve_session_key:
                    queryset = queryset.exclude(session_key=preserve_session_key)
                total = cls._delete_queryset(queryset)
                if total:
                    deleted.append({"table": model._meta.db_table, "count": total})

        removed_files = cls._remove_files(file_paths)
        return {
            "backup_path": str(backup_path) if backup_path else "",
            "audit_export_path": str(audit_export["path"]) if audit_export.get("path") else "",
            "audit_export_count": audit_export.get("count", 0),
            "backup_validation": backup_validation,
            "audit_export_validation": export_validation,
            "deleted": deleted,
            "removed_files": removed_files,
        }

    @staticmethod
    def validate_audit_export(audit_export: dict) -> dict:
        path = audit_export.get("path") if audit_export else None
        if not path:
            return {"ok": False, "message": "Audit export was not created."}
        export_path = Path(path)
        if not export_path.exists() or export_path.stat().st_size <= 0:
            return {"ok": False, "message": "Audit export file could not be verified."}
        return {"ok": True, "message": "Audit export verified.", "path": str(export_path)}

    @staticmethod
    def validate_backup_artifact(backup_path) -> dict:
        if backup_path:
            path = Path(backup_path)
            if path.exists() and path.stat().st_size > 0:
                return {"ok": True, "message": "SQLite backup verified.", "path": str(path)}
            return {"ok": False, "message": "Database backup file could not be verified."}
        if settings.DATABASES["default"].get("ENGINE") != "django.db.backends.sqlite3":
            if getattr(settings, "ACTUAL_DATA_RESET_EXTERNAL_BACKUP_CONFIRMED", False):
                return {"ok": True, "message": "External database backup confirmation recorded."}
            return {
                "ok": False,
                "message": "External database backup was not confirmed before Actual Data Reset.",
            }
        return {"ok": False, "message": "SQLite backup was not created."}

    @staticmethod
    def export_audit_logs():
        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        export_dir = Path(settings.BASE_DIR) / "backups" / "audit_exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"audit_logs.before_actual_data_reset_{timestamp}.json"
        rows = []
        for log in AuditLog.objects.select_related("actor_user", "tenant", "campus").order_by("created_at"):
            rows.append(
                {
                    "id": log.id,
                    "created_at": log.created_at,
                    "portal": log.portal,
                    "action": log.action,
                    "entity_type": log.entity_type,
                    "entity_id": log.entity_id,
                    "actor_user_id": log.actor_user_id,
                    "actor_username": log.actor_user.username if log.actor_user else None,
                    "tenant_id": log.tenant_id,
                    "tenant_code": log.tenant.code if log.tenant else None,
                    "campus_id": log.campus_id,
                    "campus_code": log.campus.code if log.campus else None,
                    "route_name": log.route_name,
                    "http_method": log.http_method,
                    "ip_address": log.ip_address,
                    "user_agent": log.user_agent,
                    "before_json": log.before_json,
                    "after_json": log.after_json,
                    "metadata_json": log.metadata_json,
                }
            )
        export_path.write_text(json.dumps(rows, cls=DjangoJSONEncoder, indent=2), encoding="utf-8")
        return {"path": export_path, "count": len(rows)}

    @staticmethod
    def _delete_queryset(queryset: QuerySet) -> int:
        total, _details = queryset.delete()
        return total

    @staticmethod
    def _collect_upload_paths():
        paths = []
        for batch in ImportBatch.objects.exclude(source_file="").only("source_file"):
            if batch.source_file and batch.source_file.name:
                paths.append(Path(settings.MEDIA_ROOT) / batch.source_file.name)
        for attachment in GradeCorrectionAttachment.objects.exclude(file="").only("file"):
            if attachment.file and attachment.file.name:
                paths.append(Path(settings.MEDIA_ROOT) / attachment.file.name)
        return paths

    @staticmethod
    def _remove_files(paths) -> int:
        removed = 0
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    @staticmethod
    def _backup_database():
        database = settings.DATABASES["default"]
        if database.get("ENGINE") != "django.db.backends.sqlite3":
            return None
        db_name = database.get("NAME")
        if not db_name or db_name == ":memory:":
            return None
        source = Path(db_name)
        if not source.exists():
            return None
        timestamp = timezone.localtime().strftime("%Y%m%d_%H%M%S")
        backup_dir = Path(settings.BASE_DIR) / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"db.sqlite3.before_actual_data_reset_{timestamp}"
        connection.close()
        copy2(source, backup_path)
        return backup_path
