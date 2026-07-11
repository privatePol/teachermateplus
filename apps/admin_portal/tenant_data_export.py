from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import json
import logging
import os
import ntpath
import secrets
import sqlite3
import tempfile
from uuid import UUID

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import models, transaction
from django.db.models import Q
from django.http import FileResponse
from django.template.loader import render_to_string
from django.utils import timezone

from apps.accounts.models import TenantDataExportChallenge, User
from apps.academics.models import (
    AcademicYear,
    ActiveGradingPeriodSetting,
    Course,
    CourseOffering,
    FacultyAssignment,
    FacultyAssignmentReplacementLog,
    Section,
    TenantTermGradingPeriod,
    Term,
)
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.auditlog.models import AuditLog
from apps.core.services.audit import AuditService
from apps.core.services.email_assets import attach_logo_for_src, build_email_logo_context, format_email_subject
from apps.enrollment.models import (
    ClassListChangeRequest,
    ClassListChangeRequestItem,
    Enrollment,
    EnrollmentAdjustmentLog,
)
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CorrectionApprovalRouteStep,
    CorrectionPetitionWindowPolicy,
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    FacultyFinalClearanceReport,
    GradeActivity,
    GradeCorrectionApprovalStep,
    GradeCorrectionAttachment,
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeCorrectionUnlockWindow,
    GradeEncodingControl,
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
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
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
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.student_portal.models import StudentAccountLink
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant

logger = logging.getLogger("teachermateplus.system")


@dataclass
class ExportChallengeResult:
    success: bool
    message: str = ""
    challenge: TenantDataExportChallenge | None = None
    cooldown_seconds: int = 0


class _AutoDeleteFile:
    def __init__(self, path: str):
        self.path = path
        self._file = open(path, "rb")

    def read(self, *args):
        return self._file.read(*args)

    def seek(self, *args):
        return self._file.seek(*args)

    def tell(self):
        return self._file.tell()

    def fileno(self):
        return self._file.fileno()

    def close(self):
        try:
            self._file.close()
        finally:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass


class TenantDataExportChallengeService:
    OTP_EXPIRY_MINUTES = 5
    MAX_OTP_ATTEMPTS = 5
    RESEND_COOLDOWN_SECONDS = 60
    MAX_RESENDS = 3
    PASSWORD_MAX_FAILURES = 5
    PASSWORD_LOCKOUT_MINUTES = 15
    PASSWORD_FAILURE_COUNT_KEY = "tenant_data_export_password_failure_count"
    PASSWORD_LOCKED_UNTIL_KEY = "tenant_data_export_password_locked_until"

    @staticmethod
    def generate_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    @staticmethod
    def masked_email(email: str) -> str:
        local, _, domain = (email or "").partition("@")
        if not local or not domain:
            return ""
        if len(local) <= 2:
            masked_local = f"{local[:1]}*"
        else:
            masked_local = f"{local[:2]}{'*' * max(len(local) - 2, 2)}"
        return f"{masked_local}@{domain}"

    @classmethod
    def _audit(cls, *, action, actor, tenant, request=None, challenge=None, metadata=None):
        return AuditService.log_event(
            action=action,
            portal="ADMIN",
            entity_type="TenantDataExportChallenge",
            entity_id=getattr(challenge, "token", None),
            actor=actor,
            tenant=tenant,
            metadata=metadata or {},
            request=request,
        )

    @classmethod
    def _password_locked_until(cls, request):
        raw_value = request.session.get(cls.PASSWORD_LOCKED_UNTIL_KEY)
        if not raw_value:
            return None
        try:
            locked_until = datetime.fromisoformat(raw_value)
            if timezone.is_naive(locked_until):
                locked_until = timezone.make_aware(locked_until, timezone.get_current_timezone())
        except (TypeError, ValueError):
            request.session.pop(cls.PASSWORD_LOCKED_UNTIL_KEY, None)
            return None
        if locked_until <= timezone.now():
            request.session.pop(cls.PASSWORD_LOCKED_UNTIL_KEY, None)
            request.session[cls.PASSWORD_FAILURE_COUNT_KEY] = 0
            return None
        return locked_until

    @classmethod
    def _register_password_failure(cls, request):
        count = int(request.session.get(cls.PASSWORD_FAILURE_COUNT_KEY) or 0) + 1
        request.session[cls.PASSWORD_FAILURE_COUNT_KEY] = count
        if count >= cls.PASSWORD_MAX_FAILURES:
            locked_until = timezone.now() + timedelta(minutes=cls.PASSWORD_LOCKOUT_MINUTES)
            request.session[cls.PASSWORD_LOCKED_UNTIL_KEY] = locked_until.isoformat()
        request.session.modified = True

    @classmethod
    def _reset_password_failures(cls, request):
        request.session.pop(cls.PASSWORD_FAILURE_COUNT_KEY, None)
        request.session.pop(cls.PASSWORD_LOCKED_UNTIL_KEY, None)
        request.session.modified = True

    @classmethod
    def start_challenge(cls, *, request, user, tenant: Tenant, password: str) -> ExportChallengeResult:
        locked_until = cls._password_locked_until(request)
        if locked_until:
            cls._audit(
                action="TENANT_EXPORT_PASSWORD_BLOCKED",
                actor=user,
                tenant=tenant,
                request=request,
                metadata={"locked_until": locked_until},
            )
            return ExportChallengeResult(
                success=False,
                message="Too many password verification attempts. Please try again later.",
            )

        if not user.check_password(password or ""):
            cls._register_password_failure(request)
            cls._audit(
                action="TENANT_EXPORT_PASSWORD_FAILURE",
                actor=user,
                tenant=tenant,
                request=request,
                metadata={"failure_count": request.session.get(cls.PASSWORD_FAILURE_COUNT_KEY)},
            )
            return ExportChallengeResult(success=False, message="Password verification failed.")

        cls._audit(
            action="TENANT_EXPORT_PASSWORD_SUCCESS",
            actor=user,
            tenant=tenant,
            request=request,
        )

        email = (getattr(user, "email", "") or "").strip()
        if not email:
            cls._audit(
                action="TENANT_EXPORT_OTP_EMAIL_MISSING",
                actor=user,
                tenant=tenant,
                request=request,
            )
            return ExportChallengeResult(
                success=False,
                message="This administrator account has no registered email address. Export verification cannot continue.",
            )

        cls._reset_password_failures(request)
        now = timezone.now()
        code = cls.generate_code()
        with transaction.atomic():
            TenantDataExportChallenge.objects.filter(
                requesting_user=user,
                status__in=[
                    TenantDataExportChallenge.Status.OTP_SENT,
                    TenantDataExportChallenge.Status.OTP_VERIFIED,
                ],
                consumed_at__isnull=True,
            ).update(status=TenantDataExportChallenge.Status.CANCELLED, updated_at=now)
            challenge = TenantDataExportChallenge.objects.create(
                requesting_user=user,
                selected_tenant=tenant,
                otp_hash=make_password(code),
                sent_to_email=email,
                password_verified_at=now,
                otp_sent_at=now,
                otp_expires_at=now + timedelta(minutes=cls.OTP_EXPIRY_MINUTES),
                last_sent_at=now,
                request_ip=request.META.get("REMOTE_ADDR"),
                user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:255],
            )

        cls._audit(
            action="TENANT_EXPORT_CHALLENGE_STARTED",
            actor=user,
            tenant=tenant,
            challenge=challenge,
            request=request,
            metadata={"expires_at": challenge.otp_expires_at, "email": cls.masked_email(email)},
        )

        try:
            sent_count = cls._send_email(request=request, user=user, challenge=challenge, code=code)
        except Exception as exc:
            logger.exception(
                "Tenant data export OTP email failed for user_id=%s tenant_id=%s recipient_domain=%s",
                user.id,
                tenant.id,
                email.rsplit("@", 1)[-1] if "@" in email else "invalid",
            )
            sent_count = 0
            delivery_error_type = type(exc).__name__
        else:
            delivery_error_type = None

        cls._audit(
            action="TENANT_EXPORT_OTP_SENT",
            actor=user,
            tenant=tenant,
            challenge=challenge,
            request=request,
            metadata={
                "sent": sent_count > 0,
                "email": cls.masked_email(email),
                "expires_at": challenge.otp_expires_at,
                "delivery_error_type": delivery_error_type,
            },
        )
        if sent_count <= 0:
            challenge.status = TenantDataExportChallenge.Status.CANCELLED
            challenge.otp_hash = ""
            challenge.save(update_fields=["status", "otp_hash", "updated_at"])
            return ExportChallengeResult(
                success=False,
                message="TeacherMate+ could not send the verification code. Please try again or contact your administrator.",
                challenge=challenge,
            )
        return ExportChallengeResult(success=True, challenge=challenge)

    @classmethod
    def _send_email(cls, *, request, user, challenge: TenantDataExportChallenge, code: str) -> int:
        subject = format_email_subject("Tenant Export Verification Code")
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@teachermateplus.local")
        logo_context = build_email_logo_context(
            filename="ncba-logo.png",
            cid="ncba-logo",
            external_url=getattr(settings, "EMAIL_SCHOOL_LOGO_URL", ""),
            configured_path=getattr(settings, "EMAIL_SCHOOL_LOGO_PATH", ""),
        )
        context = {
            "user": user,
            "tenant": challenge.selected_tenant,
            "otp_code": code,
            "expires_at": challenge.otp_expires_at,
            "expires_in_minutes": cls.OTP_EXPIRY_MINUTES,
            **logo_context,
        }
        text_body = render_to_string("admin_portal/emails/tenant_data_export_otp.txt", context)
        html_body = render_to_string("admin_portal/emails/tenant_data_export_otp.html", context)
        message = EmailMultiAlternatives(subject=subject, body=text_body, from_email=from_email, to=[challenge.sent_to_email])
        attach_logo_for_src(
            message,
            src=logo_context["email_logo_src"],
            filename="ncba-logo.png",
            cid="ncba-logo",
            configured_path=getattr(settings, "EMAIL_SCHOOL_LOGO_PATH", ""),
        )
        message.attach_alternative(html_body, "text/html")
        return message.send(fail_silently=False)

    @classmethod
    def get_user_challenge(cls, *, user, token) -> TenantDataExportChallenge:
        try:
            return TenantDataExportChallenge.objects.select_related("requesting_user", "selected_tenant").get(
                token=token,
                requesting_user=user,
            )
        except (TenantDataExportChallenge.DoesNotExist, ValueError, TypeError):
            raise PermissionDenied("Invalid export verification challenge.")

    @classmethod
    def resend(cls, *, request, user, token) -> ExportChallengeResult:
        now = timezone.now()
        try:
            with transaction.atomic():
                challenge = TenantDataExportChallenge.objects.select_for_update().select_related("selected_tenant").get(
                    token=token,
                    requesting_user=user,
                )
                if challenge.status != TenantDataExportChallenge.Status.OTP_SENT or challenge.consumed_at:
                    return ExportChallengeResult(success=False, message="This verification challenge can no longer be used.")
                if challenge.otp_expires_at and challenge.otp_expires_at <= now:
                    challenge.status = TenantDataExportChallenge.Status.EXPIRED
                    challenge.save(update_fields=["status", "updated_at"])
                    cls._audit(
                        action="TENANT_EXPORT_CHALLENGE_EXPIRED",
                        actor=user,
                        tenant=challenge.selected_tenant,
                        challenge=challenge,
                        request=request,
                    )
                    return ExportChallengeResult(success=False, message="This verification code has expired. Please start again.")
                if challenge.resend_count >= cls.MAX_RESENDS:
                    return ExportChallengeResult(success=False, message="The maximum number of verification code sends has been reached.")
                cooldown_until = (challenge.last_sent_at or challenge.otp_sent_at or now) + timedelta(
                    seconds=cls.RESEND_COOLDOWN_SECONDS
                )
                if cooldown_until > now:
                    return ExportChallengeResult(
                        success=False,
                        message="Please wait before requesting another verification code.",
                        challenge=challenge,
                        cooldown_seconds=max(1, int((cooldown_until - now).total_seconds())),
                    )

                code = cls.generate_code()
                new_hash = make_password(code)
                new_expires_at = now + timedelta(minutes=cls.OTP_EXPIRY_MINUTES)
        except (TenantDataExportChallenge.DoesNotExist, ValueError, TypeError) as exc:
            raise PermissionDenied("Invalid export verification challenge.") from exc

        try:
            sent_count = cls._send_email(request=request, user=user, challenge=challenge, code=code)
        except Exception as exc:
            logger.exception("Tenant data export OTP resend failed for challenge=%s", challenge.token)
            sent_count = 0
            delivery_error_type = type(exc).__name__
        else:
            delivery_error_type = None

        if sent_count <= 0:
            cls._audit(
                action="TENANT_EXPORT_OTP_RESEND_FAILURE",
                actor=user,
                tenant=challenge.selected_tenant,
                challenge=challenge,
                request=request,
                metadata={"delivery_error_type": delivery_error_type},
            )
            return ExportChallengeResult(
                success=False,
                message="TeacherMate+ could not send another verification code. Please try again later.",
                challenge=challenge,
            )

        with transaction.atomic():
            challenge = TenantDataExportChallenge.objects.select_for_update().get(id=challenge.id)
            challenge.otp_hash = new_hash
            challenge.otp_sent_at = now
            challenge.otp_expires_at = new_expires_at
            challenge.last_sent_at = now
            challenge.resend_count += 1
            challenge.failed_attempt_count = 0
            challenge.save(
                update_fields=[
                    "otp_hash",
                    "otp_sent_at",
                    "otp_expires_at",
                    "last_sent_at",
                    "resend_count",
                    "failed_attempt_count",
                    "updated_at",
                ]
            )
        cls._audit(
            action="TENANT_EXPORT_OTP_RESENT",
            actor=user,
            tenant=challenge.selected_tenant,
            challenge=challenge,
            request=request,
            metadata={"resend_count": challenge.resend_count, "expires_at": challenge.otp_expires_at},
        )
        return ExportChallengeResult(success=True, challenge=challenge)

    @classmethod
    def verify_otp(cls, *, request, user, token, code: str) -> ExportChallengeResult:
        normalized_code = str(code or "").strip().replace(" ", "")
        now = timezone.now()
        try:
            with transaction.atomic():
                challenge = TenantDataExportChallenge.objects.select_for_update().select_related("selected_tenant").get(
                    token=token,
                    requesting_user=user,
                )
                if challenge.status != TenantDataExportChallenge.Status.OTP_SENT or challenge.consumed_at:
                    return ExportChallengeResult(success=False, message="This verification challenge can no longer be used.")
                if challenge.otp_expires_at and challenge.otp_expires_at <= now:
                    challenge.status = TenantDataExportChallenge.Status.EXPIRED
                    challenge.save(update_fields=["status", "updated_at"])
                    cls._audit(
                        action="TENANT_EXPORT_CHALLENGE_EXPIRED",
                        actor=user,
                        tenant=challenge.selected_tenant,
                        challenge=challenge,
                        request=request,
                    )
                    return ExportChallengeResult(success=False, message="This verification code has expired. Please start again.")
                if challenge.failed_attempt_count >= cls.MAX_OTP_ATTEMPTS:
                    challenge.status = TenantDataExportChallenge.Status.LOCKED
                    challenge.save(update_fields=["status", "updated_at"])
                    return ExportChallengeResult(success=False, message="Too many incorrect verification attempts. Please start again.")
                if not normalized_code or not check_password(normalized_code, challenge.otp_hash):
                    challenge.failed_attempt_count += 1
                    if challenge.failed_attempt_count >= cls.MAX_OTP_ATTEMPTS:
                        challenge.status = TenantDataExportChallenge.Status.LOCKED
                        action = "TENANT_EXPORT_CHALLENGE_LOCKED"
                    else:
                        action = "TENANT_EXPORT_OTP_FAILURE"
                    challenge.save(update_fields=["failed_attempt_count", "status", "updated_at"])
                    cls._audit(
                        action=action,
                        actor=user,
                        tenant=challenge.selected_tenant,
                        challenge=challenge,
                        request=request,
                        metadata={"failed_attempt_count": challenge.failed_attempt_count},
                    )
                    return ExportChallengeResult(success=False, message="The verification code is incorrect.", challenge=challenge)

                challenge.otp_verified_at = now
                challenge.status = TenantDataExportChallenge.Status.OTP_VERIFIED
                challenge.save(update_fields=["otp_verified_at", "status", "updated_at"])
        except (TenantDataExportChallenge.DoesNotExist, ValueError, TypeError) as exc:
            raise PermissionDenied("Invalid export verification challenge.") from exc
        cls._audit(
            action="TENANT_EXPORT_OTP_SUCCESS",
            actor=user,
            tenant=challenge.selected_tenant,
            challenge=challenge,
            request=request,
        )
        return ExportChallengeResult(success=True, challenge=challenge)


class TenantSQLiteExportService:
    FORMAT_VERSION = "1"
    MAX_TOTAL_ROWS = 200_000
    USER_PASSWORD_VALUE = "!"
    EXCLUDED_CATEGORIES = [
        "Django sessions and CSRF/session identifiers",
        "Login OTP challenges, tenant export challenge OTP hashes, and password reset secrets",
        "Tenant API key hashes, database/email credentials, secret keys, and infrastructure logs",
        "Signature encrypted blobs and uploaded file binaries",
        "Import batch source files and temporary upload files",
    ]

    @classmethod
    def create_download_response(cls, *, request, challenge: TenantDataExportChallenge) -> FileResponse:
        """Reserve the verified challenge before file generation.

        Once `_consume_challenge()` changes OTP_VERIFIED to CONSUMED, the code is one-use only.
        If generation later fails, the failure is audited and the same challenge cannot be retried.
        """
        tenant = challenge.selected_tenant
        cls._consume_challenge(request=request, challenge=challenge)
        AuditService.log_event(
            action="TENANT_EXPORT_GENERATION_STARTED",
            portal="ADMIN",
            entity_type="TenantDataExportChallenge",
            entity_id=challenge.token,
            actor=request.user,
            tenant=tenant,
            request=request,
        )
        path = None
        try:
            path, row_counts = cls._build_sqlite_file(tenant=tenant, requesting_user=request.user)
        except ValidationError as exc:
            if path:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            AuditService.log_event(
                action="TENANT_EXPORT_GENERATION_FAILED",
                portal="ADMIN",
                entity_type="TenantDataExportChallenge",
                entity_id=challenge.token,
                actor=request.user,
                tenant=tenant,
                metadata={"error_type": type(exc).__name__},
                request=request,
            )
            raise
        except Exception as exc:
            if path:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            logger.exception("Tenant SQLite export failed for tenant_id=%s challenge=%s", tenant.id, challenge.token)
            AuditService.log_event(
                action="TENANT_EXPORT_GENERATION_FAILED",
                portal="ADMIN",
                entity_type="TenantDataExportChallenge",
                entity_id=challenge.token,
                actor=request.user,
                tenant=tenant,
                metadata={"error_type": type(exc).__name__},
                request=request,
            )
            raise

        filename = cls._filename(tenant)
        response = FileResponse(
            _AutoDeleteFile(path),
            as_attachment=True,
            filename=filename,
            content_type="application/vnd.sqlite3",
        )
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        AuditService.log_event(
            action="TENANT_EXPORT_GENERATION_COMPLETED",
            portal="ADMIN",
            entity_type="TenantDataExportChallenge",
            entity_id=challenge.token,
            actor=request.user,
            tenant=tenant,
            metadata={"filename": filename, "row_counts": row_counts},
            request=request,
        )
        AuditService.log_event(
            action="TENANT_EXPORT_DOWNLOAD_INITIATED",
            portal="ADMIN",
            entity_type="TenantDataExportChallenge",
            entity_id=challenge.token,
            actor=request.user,
            tenant=tenant,
            metadata={"filename": filename},
            request=request,
        )
        return response

    @classmethod
    def _consume_challenge(cls, *, request, challenge: TenantDataExportChallenge):
        with transaction.atomic():
            locked = TenantDataExportChallenge.objects.select_for_update().select_related("selected_tenant").get(
                id=challenge.id,
                requesting_user=request.user,
            )
            if locked.status != TenantDataExportChallenge.Status.OTP_VERIFIED or locked.consumed_at:
                raise PermissionDenied("This export verification challenge cannot be used.")
            if locked.otp_expires_at and locked.otp_expires_at <= timezone.now():
                locked.status = TenantDataExportChallenge.Status.EXPIRED
                locked.save(update_fields=["status", "updated_at"])
                raise PermissionDenied("This export verification challenge has expired.")
            locked.status = TenantDataExportChallenge.Status.CONSUMED
            locked.consumed_at = timezone.now()
            locked.save(update_fields=["status", "consumed_at", "updated_at"])
        AuditService.log_event(
            action="TENANT_EXPORT_CHALLENGE_CONSUMED",
            portal="ADMIN",
            entity_type="TenantDataExportChallenge",
            entity_id=challenge.token,
            actor=request.user,
            tenant=challenge.selected_tenant,
            request=request,
        )

    @classmethod
    def _filename(cls, tenant: Tenant) -> str:
        safe_code = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in tenant.code.lower())
        timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d_%H%M%S")
        return f"teachermateplus_{safe_code}_{timestamp}.sqlite3"

    @classmethod
    def _build_sqlite_file(cls, *, tenant: Tenant, requesting_user) -> tuple[str, dict[str, int]]:
        export_rows = cls._collect_export_rows(tenant=tenant, requesting_user=requesting_user)
        total_rows = sum(len(rows) for rows in export_rows.values())
        if total_rows > cls.MAX_TOTAL_ROWS:
            raise ValidationError("This tenant export is too large for synchronous download.")

        fd, path = tempfile.mkstemp(prefix="teachermateplus_export_", suffix=".sqlite3")
        os.close(fd)
        try:
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys = OFF")
            with connection:
                row_counts = cls._write_tables(connection, export_rows)
                cls._write_manifest(
                    connection,
                    tenant=tenant,
                    requesting_user=requesting_user,
                    row_counts=row_counts,
                )
            connection.close()
        except Exception:
            try:
                connection.close()
            except Exception:
                pass
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            raise
        return path, row_counts

    @classmethod
    def _collect_export_rows(cls, *, tenant: Tenant, requesting_user) -> OrderedDict[type[models.Model], list[models.Model]]:
        tenant_id = tenant.id
        rows: OrderedDict[type[models.Model], list[models.Model]] = OrderedDict()

        def add(model, queryset):
            rows[model] = list(queryset.order_by("id"))
            return rows[model]

        add(Tenant, Tenant.objects.filter(id=tenant_id))
        campus_rows = add(Campus, Campus.objects.filter(tenant_id=tenant_id))
        department_rows = add(Department, Department.objects.filter(tenant_id=tenant_id))
        program_rows = add(Program, Program.objects.filter(tenant_id=tenant_id))
        add(SystemSetting, SystemSetting.objects.filter(tenant_id=tenant_id))

        add(AcademicYear, AcademicYear.objects.filter(tenant_id=tenant_id))
        add(Term, Term.objects.filter(tenant_id=tenant_id))
        add(TenantTermGradingPeriod, TenantTermGradingPeriod.objects.filter(tenant_id=tenant_id))
        add(ActiveGradingPeriodSetting, ActiveGradingPeriodSetting.objects.filter(tenant_id=tenant_id))
        course_rows = add(Course, Course.objects.filter(tenant_id=tenant_id))
        section_rows = add(Section, Section.objects.filter(tenant_id=tenant_id))
        offering_rows = add(CourseOffering, CourseOffering.objects.filter(tenant_id=tenant_id))
        offering_ids = [row.id for row in offering_rows]
        student_rows = add(Student, Student.objects.filter(tenant_id=tenant_id))
        student_ids = [row.id for row in student_rows]
        add(Enrollment, Enrollment.objects.filter(tenant_id=tenant_id))
        add(
            EnrollmentAdjustmentLog,
            EnrollmentAdjustmentLog.objects.filter(
                Q(source_offering_id__in=offering_ids) | Q(destination_offering_id__in=offering_ids)
            ),
        )
        class_request_rows = add(ClassListChangeRequest, ClassListChangeRequest.objects.filter(tenant_id=tenant_id))
        add(ClassListChangeRequestItem, ClassListChangeRequestItem.objects.filter(request_id__in=[r.id for r in class_request_rows]))
        add(FacultyAssignment, FacultyAssignment.objects.filter(tenant_id=tenant_id))
        add(FacultyAssignmentReplacementLog, FacultyAssignmentReplacementLog.objects.filter(tenant_id=tenant_id))

        template_rows = add(GradingTemplate, GradingTemplate.objects.filter(tenant_id=tenant_id))
        template_ids = [row.id for row in template_rows]
        workflow_rows = add(GradingTemplateApprovalWorkflow, GradingTemplateApprovalWorkflow.objects.filter(tenant_id=tenant_id))
        add(GradingTemplateApprovalStep, GradingTemplateApprovalStep.objects.filter(workflow_id__in=[r.id for r in workflow_rows]))
        hotfix_rows = add(TemplateHotfixRequest, TemplateHotfixRequest.objects.filter(tenant_id=tenant_id))
        add(TemplateHotfixWorkflowStep, TemplateHotfixWorkflowStep.objects.filter(hotfix_request_id__in=[r.id for r in hotfix_rows]))
        period_rows = add(GradingTemplatePeriod, GradingTemplatePeriod.objects.filter(template_id__in=template_ids))
        period_ids = [row.id for row in period_rows]
        component_rows = add(GradingTemplateComponent, GradingTemplateComponent.objects.filter(template_period_id__in=period_ids))
        component_ids = [row.id for row in component_rows]
        subcomponent_rows = add(GradingTemplateSubcomponent, GradingTemplateSubcomponent.objects.filter(template_component_id__in=component_ids))
        subcomponent_ids = [row.id for row in subcomponent_rows]
        add(GradingTemplateDetail, GradingTemplateDetail.objects.filter(template_subcomponent_id__in=subcomponent_ids))
        add(TenantGradingProfile, TenantGradingProfile.objects.filter(tenant_id=tenant_id))
        add(CourseTemplateAssignment, CourseTemplateAssignment.objects.filter(course_id__in=[c.id for c in course_rows]))
        add(CourseBaseValueOverride, CourseBaseValueOverride.objects.filter(course_id__in=[c.id for c in course_rows]))

        activity_rows = add(GradeActivity, GradeActivity.objects.filter(tenant_id=tenant_id))
        activity_ids = [row.id for row in activity_rows]
        add(StudentActivityScore, StudentActivityScore.objects.filter(activity_id__in=activity_ids, student_id__in=student_ids))
        add(StudentPeriodGrade, StudentPeriodGrade.objects.filter(tenant_id=tenant_id))
        add(StudentFinalGrade, StudentFinalGrade.objects.filter(tenant_id=tenant_id))
        add(FacultyFinalClearanceReport, FacultyFinalClearanceReport.objects.filter(tenant_id=tenant_id))
        add(GradingPeriodLock, GradingPeriodLock.objects.filter(tenant_id=tenant_id))
        add(GradeEncodingControl, GradeEncodingControl.objects.filter(tenant_id=tenant_id))
        add(GradeSubmission, GradeSubmission.objects.filter(tenant_id=tenant_id))
        add(GradeSubmissionReopenRequest, GradeSubmissionReopenRequest.objects.filter(tenant_id=tenant_id))
        route_rows = add(CorrectionApprovalRouteRule, CorrectionApprovalRouteRule.objects.filter(tenant_id=tenant_id))
        add(CorrectionApprovalRouteStep, CorrectionApprovalRouteStep.objects.filter(route_rule_id__in=[r.id for r in route_rows]))
        add(CorrectionPetitionWindowPolicy, CorrectionPetitionWindowPolicy.objects.filter(tenant_id=tenant_id))
        correction_rows = add(GradeCorrectionRequest, GradeCorrectionRequest.objects.filter(tenant_id=tenant_id))
        correction_ids = [row.id for row in correction_rows]
        add(GradeCorrectionApprovalStep, GradeCorrectionApprovalStep.objects.filter(correction_request_id__in=correction_ids))
        add(GradeCorrectionRequestItem, GradeCorrectionRequestItem.objects.filter(correction_request_id__in=correction_ids))
        add(GradeCorrectionAttachment, GradeCorrectionAttachment.objects.filter(correction_request_id__in=correction_ids))
        add(GradeCorrectionUnlockWindow, GradeCorrectionUnlockWindow.objects.filter(correction_request_id__in=correction_ids))

        session_rows = add(AttendanceSession, AttendanceSession.objects.filter(tenant_id=tenant_id))
        add(AttendanceRecord, AttendanceRecord.objects.filter(Q(tenant_id=tenant_id) | Q(session_id__in=[r.id for r in session_rows])))
        add(FacultyReminder, FacultyReminder.objects.filter(tenant_id=tenant_id))
        add(FacultyMemo, FacultyMemo.objects.filter(tenant_id=tenant_id))
        add(FacultyReminderEmailQueue, FacultyReminderEmailQueue.objects.filter(tenant_id=tenant_id))
        add(NotificationQueue, NotificationQueue.objects.filter(tenant_id=tenant_id))
        add(SubmissionNonComplianceNotice, SubmissionNonComplianceNotice.objects.filter(tenant_id=tenant_id))

        add(PredictionSettingSnapshot, PredictionSettingSnapshot.objects.filter(tenant_id=tenant_id))
        add(PredictionSnapshot, PredictionSnapshot.objects.filter(tenant_id=tenant_id))
        add(PredictionSummarySnapshot, PredictionSummarySnapshot.objects.filter(tenant_id=tenant_id))
        add(PredictionDirtyQueue, PredictionDirtyQueue.objects.filter(tenant_id=tenant_id))
        add(PredictionWhatIfDraft, PredictionWhatIfDraft.objects.filter(tenant_id=tenant_id))
        add(PredictionViewLog, PredictionViewLog.objects.filter(tenant_id=tenant_id))
        add(StudentAccountLink, StudentAccountLink.objects.filter(tenant_id=tenant_id))
        add(AuditLog, AuditLog.objects.filter(tenant_id=tenant_id))

        user_ids = cls._collect_user_ids(rows)
        user_ids.add(requesting_user.id)
        tenant_user_role_rows = list(UserRole.objects.filter(tenant_id=tenant_id).order_by("id"))
        tenant_user_permission_rows = list(UserPermission.objects.filter(tenant_id=tenant_id).order_by("id"))
        user_ids.update(row.user_id for row in tenant_user_role_rows)
        user_ids.update(row.user_id for row in tenant_user_permission_rows)
        user_rows = list(
            User.objects.filter(
                Q(id__in=user_ids)
                | Q(default_tenant_id=tenant_id)
                | Q(default_campus_id__in=[row.id for row in campus_rows])
                | Q(default_department_id__in=[row.id for row in department_rows])
            ).order_by("id")
        )
        rows[User] = user_rows
        exported_user_ids = [row.id for row in user_rows]
        user_role_rows = list(
            UserRole.objects.filter(Q(tenant_id=tenant_id) | Q(user_id__in=exported_user_ids, tenant__isnull=True)).order_by("id")
        )
        user_permission_rows = list(
            UserPermission.objects.filter(Q(tenant_id=tenant_id) | Q(user_id__in=exported_user_ids, tenant__isnull=True)).order_by("id")
        )
        rows[UserRole] = user_role_rows
        rows[UserPermission] = user_permission_rows
        role_ids = {row.role_id for row in user_role_rows}
        permission_ids = {row.permission_id for row in user_permission_rows}
        for model, model_rows in rows.items():
            for row in model_rows:
                for field in model._meta.fields:
                    if isinstance(field, models.ForeignKey) and field.remote_field.model is Role:
                        value = getattr(row, field.attname)
                        if value:
                            role_ids.add(value)

        role_permission_rows = list(RolePermission.objects.filter(role_id__in=role_ids).order_by("id"))
        permission_ids.update(row.permission_id for row in role_permission_rows)
        menu_permission_rows = list(MenuItemPermission.objects.filter(permission_id__in=permission_ids).order_by("id"))
        permission_ids.update(row.permission_id for row in menu_permission_rows)
        menu_item_ids = {row.menu_item_id for row in menu_permission_rows}
        menu_item_rows = list(MenuItem.objects.filter(id__in=menu_item_ids).order_by("id"))
        menu_group_ids = {row.menu_group_id for row in menu_item_rows}
        rows[Role] = list(Role.objects.filter(id__in=role_ids).order_by("id"))
        rows[Permission] = list(Permission.objects.filter(id__in=permission_ids).order_by("id"))
        rows[RolePermission] = role_permission_rows
        rows[MenuGroup] = list(MenuGroup.objects.filter(id__in=menu_group_ids).order_by("id"))
        rows[MenuItem] = menu_item_rows
        rows[MenuItemPermission] = menu_permission_rows
        return rows

    @classmethod
    def _collect_user_ids(cls, rows: OrderedDict[type[models.Model], list[models.Model]]) -> set[int]:
        user_ids = set()
        for model, model_rows in rows.items():
            for field in model._meta.fields:
                if isinstance(field, models.ForeignKey) and field.remote_field.model is User:
                    for row in model_rows:
                        value = getattr(row, field.attname)
                        if value:
                            user_ids.add(value)
        return user_ids

    @classmethod
    def _write_tables(cls, connection, export_rows) -> dict[str, int]:
        row_counts = {}
        for model, rows in export_rows.items():
            cls._create_table(connection, model)
            cls._insert_rows(connection, model, rows)
            row_counts[model._meta.db_table] = len(rows)
        return row_counts

    @classmethod
    def _create_table(cls, connection, model):
        columns = []
        for field in model._meta.concrete_fields:
            column_def = f'"{field.column}" {cls._sqlite_type(field)}'
            if field.primary_key:
                column_def += " PRIMARY KEY"
            columns.append(column_def)
        connection.execute(f'CREATE TABLE "{model._meta.db_table}" ({", ".join(columns)})')

    @classmethod
    def _sqlite_type(cls, field):
        if isinstance(field, (models.AutoField, models.BigAutoField, models.IntegerField, models.BigIntegerField, models.PositiveIntegerField, models.PositiveBigIntegerField, models.BooleanField, models.ForeignKey)):
            return "INTEGER"
        if isinstance(field, (models.DecimalField, models.FloatField)):
            return "TEXT"
        if isinstance(field, models.BinaryField):
            return "BLOB"
        return "TEXT"

    @classmethod
    def _insert_rows(cls, connection, model, rows):
        fields = list(model._meta.concrete_fields)
        columns = [field.column for field in fields]
        placeholders = ", ".join(["?"] * len(columns))
        quoted_columns = ", ".join(f'"{column}"' for column in columns)
        sql = f'INSERT INTO "{model._meta.db_table}" ({quoted_columns}) VALUES ({placeholders})'
        values = [[cls._field_value(row, field) for field in fields] for row in rows]
        if values:
            connection.executemany(sql, values)

    @classmethod
    def _field_value(cls, row, field):
        if row.__class__ is User and field.name == "password":
            return cls.USER_PASSWORD_VALUE
        value = getattr(row, field.attname if isinstance(field, models.ForeignKey) else field.name)
        if value is None:
            return None
        if isinstance(field, models.FileField):
            return cls._safe_file_name(getattr(value, "name", "") or "")
        if isinstance(field, models.BooleanField):
            return 1 if value else 0
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        if isinstance(field, models.JSONField):
            return json.dumps(value, default=str, sort_keys=True)
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, default=str, sort_keys=True)
        if isinstance(value, bytes):
            return value
        return str(value) if not isinstance(value, (int, float)) else value

    @staticmethod
    def _safe_file_name(name: str) -> str:
        normalized = str(name or "").replace("\\", "/")
        if not normalized:
            return ""
        first_segment = normalized.split("/", 1)[0]
        if normalized.startswith("/") or ":" in first_segment:
            return ntpath.basename(normalized)
        return normalized

    @classmethod
    def _write_manifest(cls, connection, *, tenant, requesting_user, row_counts):
        connection.execute(
            'CREATE TABLE "tmp_export_manifest" ("key" TEXT PRIMARY KEY, "value" TEXT)'
        )
        connection.execute(
            'CREATE TABLE "tmp_export_row_counts" ("table_name" TEXT PRIMARY KEY, "row_count" INTEGER)'
        )
        connection.execute(
            'CREATE TABLE "tmp_export_exclusions" ("category" TEXT PRIMARY KEY, "detail" TEXT)'
        )
        manifest = {
            "format_version": cls.FORMAT_VERSION,
            "generated_at": timezone.now().isoformat(),
            "source_database_type": settings.DATABASES.get("default", {}).get("ENGINE", ""),
            "selected_tenant_id": tenant.id,
            "selected_tenant_code": tenant.code,
            "requested_by_user_id": requesting_user.id,
            "included_tables": sorted(row_counts.keys()),
            "uploaded_file_binaries_included": False,
            "account_passwords_replaced_with_unusable_values": True,
            "export_purpose": "authorized investigation/troubleshooting",
            "schema_strategy": "investigation-oriented SQLite package using explicit TeacherMate+ table allowlist",
            "compatibility_notes": "Not a full Django development database; foreign-key constraints are not recreated.",
        }
        connection.executemany(
            'INSERT INTO "tmp_export_manifest" ("key", "value") VALUES (?, ?)',
            [(key, json.dumps(value, default=str) if isinstance(value, (list, dict, bool)) else str(value)) for key, value in manifest.items()],
        )
        connection.executemany(
            'INSERT INTO "tmp_export_row_counts" ("table_name", "row_count") VALUES (?, ?)',
            sorted(row_counts.items()),
        )
        connection.executemany(
            'INSERT INTO "tmp_export_exclusions" ("category", "detail") VALUES (?, ?)',
            [(f"excluded_{index}", detail) for index, detail in enumerate(cls.EXCLUDED_CATEGORIES, start=1)],
        )
