from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone

from apps.core.services.email_assets import attach_logo_for_src, build_email_logo_context, format_email_subject
from apps.core.services.features import FeatureSettingsService
from apps.core.services.permissions import PermissionService
from apps.auditlog.models import AuditLog
from apps.grading.models import GradeCorrectionRequest, GradeSubmissionReopenRequest
from apps.grading.reporting import CorrectionOfficialReportService
from apps.rbac.models import UserPermission, UserRole

User = get_user_model()


class CorrectionNotificationService:
    SCHOOL_NAME = "NATIONAL COLLEGE OF BUSINESS AND ARTS"
    SUBJECT_MESSAGE = "Petition for Correction of Grades Awaiting Your Approval"
    FACULTY_DECISION_SUBJECT = "Petition for Correction of Grades Decision"

    @classmethod
    def _logo_context(cls) -> dict[str, str]:
        return build_email_logo_context(
            filename="ncba-logo.png",
            cid="ncba-logo",
            external_url=getattr(settings, "EMAIL_SCHOOL_LOGO_URL", ""),
            configured_path=getattr(settings, "EMAIL_SCHOOL_LOGO_PATH", ""),
        )

    @classmethod
    def _role_recipient_rows(cls, *, request_obj: GradeCorrectionRequest):
        role_codes = FeatureSettingsService.get_correction_submission_approval_email_role_codes(
            tenant_id=request_obj.tenant_id
        )
        if not role_codes:
            return []

        assignments = list(
            UserRole.objects.filter(
                role__code__in=role_codes,
                is_active=True,
                role__is_active=True,
                user__is_active=True,
            )
            .filter(
                tenant_id=request_obj.tenant_id,
            )
            .filter(Q(campus_id=request_obj.campus_id) | Q(campus_id__isnull=True))
            .select_related("user", "role")
            .order_by("role__name", "user__last_name", "user__first_name", "user__id")
        )

        recipients = []
        seen_keys = set()
        for role_code in role_codes:
            role_rows = [row for row in assignments if row.role.code == role_code and (row.user.email or "").strip()]
            if not role_rows:
                continue

            if request_obj.faculty_department_id:
                department_rows = [
                    row for row in role_rows if row.user.default_department_id == request_obj.faculty_department_id
                ]
                if department_rows:
                    role_rows = department_rows

            for row in role_rows:
                dedupe_key = (row.user_id, (row.user.email or "").strip().lower())
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                recipients.append(
                    {
                        "user": row.user,
                        "role": row.role,
                        "email": row.user.email.strip(),
                    }
                )
        return recipients

    @classmethod
    def _step_recipient_rows(cls, *, request_obj: GradeCorrectionRequest, step):
        assignments = (
            UserRole.objects.filter(
                role_id=step.approver_role_id,
                is_active=True,
                role__is_active=True,
                user__is_active=True,
            )
            .filter(Q(tenant_id=request_obj.tenant_id) | Q(tenant__isnull=True))
            .filter(Q(campus_id=request_obj.campus_id) | Q(campus__isnull=True))
            .select_related("user", "role", "department")
            .order_by("user__last_name", "user__first_name", "user__id")
        )
        recipients = []
        seen_keys = set()
        for row in assignments:
            email = (row.user.email or "").strip()
            if not email:
                continue
            if row.department_id:
                if not request_obj.faculty_department_id:
                    continue
                from apps.core.services.scope import ScopeService

                if not ScopeService.department_scope_covers(row.department_id, request_obj.faculty_department_id):
                    continue
            elif step.requires_same_department:
                user_dept_id = getattr(row.user, "default_department_id", None)
                if not user_dept_id:
                    continue
                from apps.core.services.scope import ScopeService

                if not ScopeService.department_scope_covers(user_dept_id, request_obj.faculty_department_id):
                    continue
            dedupe_key = (row.user_id, email.lower())
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            recipients.append({"user": row.user, "role": row.role, "email": email})
        return recipients

    @staticmethod
    def _email_event_exists(*, action: str, request_obj: GradeCorrectionRequest, event_key: str):
        return AuditLog.objects.filter(
            action=action,
            entity_type="GradeCorrectionRequest",
            entity_id=str(request_obj.id),
            metadata_json__event_key=event_key,
        ).exists()

    @staticmethod
    def _record_email_event(*, action: str, request_obj: GradeCorrectionRequest, event_key: str, recipients: list[str]):
        AuditLog.objects.create(
            portal=AuditLog.Portal.SYSTEM,
            action=action,
            entity_type="GradeCorrectionRequest",
            entity_id=str(request_obj.id),
            tenant_id=request_obj.tenant_id,
            campus_id=request_obj.campus_id,
            metadata_json={
                "event_key": event_key,
                "recipients": recipients,
            },
        )

    @classmethod
    def send_correction_submission_approval_notifications(cls, *, request_obj: GradeCorrectionRequest):
        if not FeatureSettingsService.is_correction_submission_approval_email_enabled(tenant_id=request_obj.tenant_id):
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "feature_disabled"}

        recipients = cls._role_recipient_rows(request_obj=request_obj)
        if not recipients:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "no_matching_role_recipients"}

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@teachermateplus.local")
        subject = format_email_subject(cls.SUBJECT_MESSAGE)
        petitioner_name = (
            getattr(request_obj.requested_by_user, "full_name", None)
            or request_obj.requested_by_user.get_full_name()
            or request_obj.requested_by_user.username
        )
        context_base = {
            "school_name": cls.SCHOOL_NAME,
            "subject_message": cls.SUBJECT_MESSAGE,
            "campus_name": request_obj.campus.name,
            "course_title": request_obj.offering.course.title,
            "section_name": request_obj.offering.section.name or request_obj.offering.section.code,
            "period_name": request_obj.template_period.name or request_obj.template_period.code,
            "reference_no": f"CGR-{request_obj.id:06d}",
            "submitted_at": timezone.localtime(request_obj.created_at),
            "petitioner_name": petitioner_name,
        }

        sent = 0
        errors = []
        notified_emails = []
        for recipient in recipients:
            context = {
                **context_base,
                "recipient_role_name": recipient["role"].name,
            }
            message = EmailMultiAlternatives(
                subject=subject,
                body="",
                from_email=from_email,
                to=[recipient["email"]],
            )
            logo_context = cls._logo_context()
            context.update(logo_context)
            attach_logo_for_src(
                message,
                src=logo_context["email_logo_src"],
                filename="ncba-logo.png",
                cid="ncba-logo",
                configured_path=getattr(settings, "EMAIL_SCHOOL_LOGO_PATH", ""),
            )
            text_body = render_to_string("grading/emails/correction_submission_notification.txt", context)
            html_body = render_to_string("grading/emails/correction_submission_notification.html", context)
            message.body = text_body
            message.attach_alternative(html_body, "text/html")
            try:
                result = message.send(fail_silently=False)
            except Exception as exc:  # pragma: no cover - defensive branch for SMTP/runtime failures
                errors.append({"email": recipient["email"], "error": str(exc)})
                continue
            if result:
                sent += 1
                notified_emails.append(recipient["email"])

        return {
            "attempted": len(recipients),
            "sent": sent,
            "errors": errors,
            "recipients": notified_emails,
            "reason": None if recipients else "no_matching_role_recipients",
        }

    @classmethod
    def send_correction_step_approval_notifications(cls, *, request_obj: GradeCorrectionRequest, step):
        if not step:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "no_pending_step"}
        if not FeatureSettingsService.is_correction_submission_approval_email_enabled(tenant_id=request_obj.tenant_id):
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "feature_disabled"}

        event_key = f"step:{step.id}:pending"
        if cls._email_event_exists(
            action="EMAIL_CORRECTION_STEP_APPROVER",
            request_obj=request_obj,
            event_key=event_key,
        ):
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "already_sent"}

        recipients = cls._step_recipient_rows(request_obj=request_obj, step=step)
        if not recipients:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "no_matching_step_recipients"}

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@teachermateplus.local")
        subject = format_email_subject(cls.SUBJECT_MESSAGE)
        petitioner_name = (
            getattr(request_obj.requested_by_user, "full_name", None)
            or request_obj.requested_by_user.get_full_name()
            or request_obj.requested_by_user.username
        )
        sent = 0
        errors = []
        notified_emails = []
        for recipient in recipients:
            text_body = (
                f"Petition for Correction of Grades CGR-{request_obj.id:06d} is awaiting "
                f"{step.approver_label or recipient['role'].name or recipient['role'].code} review.\n\n"
                f"Petitioner: {petitioner_name}\n"
                f"Course: {request_obj.offering.course.title}\n"
                f"Section: {request_obj.offering.section.name or request_obj.offering.section.code}\n"
                f"Period: {request_obj.template_period.name or request_obj.template_period.code}\n"
            )
            message = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=from_email,
                to=[recipient["email"]],
            )
            try:
                result = message.send(fail_silently=False)
            except Exception as exc:  # pragma: no cover - defensive branch for SMTP/runtime failures
                errors.append({"email": recipient["email"], "error": str(exc)})
                continue
            if result:
                sent += 1
                notified_emails.append(recipient["email"])

        if notified_emails:
            cls._record_email_event(
                action="EMAIL_CORRECTION_STEP_APPROVER",
                request_obj=request_obj,
                event_key=event_key,
                recipients=notified_emails,
            )
        return {
            "attempted": len(recipients),
            "sent": sent,
            "errors": errors,
            "recipients": notified_emails,
            "reason": None if recipients else "no_matching_step_recipients",
        }

    @classmethod
    def send_correction_faculty_decision_notification(cls, *, request_obj: GradeCorrectionRequest):
        if not FeatureSettingsService.is_correction_submission_approval_email_enabled(tenant_id=request_obj.tenant_id):
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "feature_disabled"}
        if request_obj.status not in {
            GradeCorrectionRequest.Status.APPROVED,
            GradeCorrectionRequest.Status.CLOSED,
            GradeCorrectionRequest.Status.REJECTED,
        }:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "not_final"}
        email = (request_obj.requested_by_user.email or "").strip()
        if not email:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "requester_has_no_email"}

        event_key = f"faculty:{request_obj.status}"
        if cls._email_event_exists(
            action="EMAIL_CORRECTION_FACULTY_DECISION",
            request_obj=request_obj,
            event_key=event_key,
        ):
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "already_sent"}

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@teachermateplus.local")
        subject = format_email_subject(cls.FACULTY_DECISION_SUBJECT)
        decision = "approved" if request_obj.status in {GradeCorrectionRequest.Status.APPROVED, GradeCorrectionRequest.Status.CLOSED} else "rejected"
        text_body = (
            f"Your Petition for Correction of Grades CGR-{request_obj.id:06d} was {decision}.\n\n"
            f"Course: {request_obj.offering.course.title}\n"
            f"Section: {request_obj.offering.section.name or request_obj.offering.section.code}\n"
            f"Period: {request_obj.template_period.name or request_obj.template_period.code}\n"
            f"Remarks: {request_obj.review_remarks or '-'}\n"
        )
        message = EmailMultiAlternatives(subject=subject, body=text_body, from_email=from_email, to=[email])
        try:
            sent = message.send(fail_silently=False)
        except Exception as exc:  # pragma: no cover - defensive branch for SMTP/runtime failures
            return {
                "attempted": 1,
                "sent": 0,
                "errors": [{"email": email, "error": str(exc)}],
                "recipients": [],
                "reason": "send_failed",
            }
        recipients = [email] if sent else []
        if recipients:
            cls._record_email_event(
                action="EMAIL_CORRECTION_FACULTY_DECISION",
                request_obj=request_obj,
                event_key=event_key,
                recipients=recipients,
            )
        return {"attempted": 1, "sent": sent, "errors": [], "recipients": recipients, "reason": None}

    @classmethod
    def _registrar_recipient_emails(cls, *, request_obj: GradeCorrectionRequest) -> list[str]:
        campus_map = FeatureSettingsService.get_correction_registrar_campus_recipients(tenant_id=request_obj.tenant_id)
        campus_emails = campus_map.get(str(request_obj.campus_id), [])
        if campus_emails:
            return campus_emails
        return FeatureSettingsService.get_correction_registrar_default_recipients(tenant_id=request_obj.tenant_id)

    @classmethod
    def send_registrar_official_report_email(
        cls,
        *,
        request_obj: GradeCorrectionRequest,
        trigger_role_code: str | None = None,
    ):
        if not FeatureSettingsService.is_correction_registrar_auto_email_enabled(tenant_id=request_obj.tenant_id):
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "feature_disabled"}

        allowed_role_codes = FeatureSettingsService.get_correction_registrar_auto_email_role_codes(
            tenant_id=request_obj.tenant_id
        )
        if allowed_role_codes and trigger_role_code and trigger_role_code not in allowed_role_codes:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "trigger_role_not_allowed"}

        recipient_emails = cls._registrar_recipient_emails(request_obj=request_obj)
        if not recipient_emails:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "no_registrar_recipient"}

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@teachermateplus.local")
        subject = format_email_subject("Approved Petition for Correction of Grades for Registrar Reference")
        pdf_bytes = CorrectionOfficialReportService.build_pdf_bytes(request_obj=request_obj)
        petitioner_name = (
            getattr(request_obj.requested_by_user, "full_name", None)
            or request_obj.requested_by_user.get_full_name()
            or request_obj.requested_by_user.username
        )
        context = {
            "school_name": cls.SCHOOL_NAME,
            "campus_name": request_obj.campus.name,
            "course_title": request_obj.offering.course.title,
            "section_name": request_obj.offering.section.name or request_obj.offering.section.code,
            "period_name": request_obj.template_period.name or request_obj.template_period.code,
            "reference_no": f"CGR-{request_obj.id:06d}",
            "approved_at": timezone.localtime(request_obj.reviewed_at or timezone.now()),
            "petitioner_name": petitioner_name,
        }
        message = EmailMultiAlternatives(
            subject=subject,
            body="",
            from_email=from_email,
            to=recipient_emails,
        )
        logo_context = cls._logo_context()
        context.update(logo_context)
        attach_logo_for_src(
            message,
            src=logo_context["email_logo_src"],
            filename="ncba-logo.png",
            cid="ncba-logo",
            configured_path=getattr(settings, "EMAIL_SCHOOL_LOGO_PATH", ""),
        )
        text_body = render_to_string("grading/emails/registrar_official_report.txt", context)
        html_body = render_to_string("grading/emails/registrar_official_report.html", context)
        message.body = text_body
        message.attach_alternative(html_body, "text/html")
        filename = f"petition-for-correction-of-grades-{request_obj.id}.pdf"
        message.attach(filename, pdf_bytes, "application/pdf")
        try:
            sent = message.send(fail_silently=False)
        except Exception as exc:  # pragma: no cover - defensive branch for SMTP/runtime failures
            return {
                "attempted": len(recipient_emails),
                "sent": 0,
                "errors": [{"email": ", ".join(recipient_emails), "error": str(exc)}],
                "recipients": [],
                "reason": "send_failed",
            }
        return {
            "attempted": len(recipient_emails),
            "sent": sent,
            "errors": [],
            "recipients": recipient_emails if sent else [],
            "reason": None if sent else "send_failed",
        }


class GradebookReopenNotificationService:
    SCHOOL_NAME = "NATIONAL COLLEGE OF BUSINESS AND ARTS"
    SUBJECT_MESSAGE = "Gradebook Reopen Request Awaiting Review"

    @classmethod
    def _logo_context(cls) -> dict[str, str]:
        return build_email_logo_context(
            filename="ncba-logo.png",
            cid="ncba-logo",
            external_url=getattr(settings, "EMAIL_SCHOOL_LOGO_URL", ""),
            configured_path=getattr(settings, "EMAIL_SCHOOL_LOGO_PATH", ""),
        )

    @classmethod
    def _recipient_rows(cls, *, request_obj: GradeSubmissionReopenRequest):
        permission_code = "reopen_requests.review"
        role_rows = list(
            UserRole.objects.filter(
                is_active=True,
                role__is_active=True,
                user__is_active=True,
                role__role_permissions__permission__code=permission_code,
                role__role_permissions__permission__is_active=True,
                tenant_id__in=[request_obj.tenant_id, None],
                campus_id__in=[request_obj.campus_id, None],
            )
            .select_related("user", "role")
            .order_by("role__name", "user__last_name", "user__first_name", "user__id")
            .distinct()
        )
        candidate_user_ids = {row.user_id for row in role_rows}
        candidate_user_ids.update(
            UserPermission.objects.filter(
                user__is_active=True,
                permission__code=permission_code,
                permission__is_active=True,
                grant_type=UserPermission.GrantType.ALLOW,
                tenant_id__in=[request_obj.tenant_id, None],
                campus_id__in=[request_obj.campus_id, None],
            ).values_list("user_id", flat=True)
        )
        role_name_by_user_id = {}
        for row in role_rows:
            role_name_by_user_id.setdefault(row.user_id, row.role.name)

        recipients = []
        seen = set()
        users = User.objects.filter(id__in=candidate_user_ids, is_active=True).order_by(
            "last_name", "first_name", "id"
        )
        for user in users:
            if not PermissionService.has_assigned_permission(
                user,
                permission_code,
                tenant_id=request_obj.tenant_id,
                campus_id=request_obj.campus_id,
            ):
                continue
            email = (user.email or "").strip()
            if not email:
                continue
            key = (user.id, email.lower())
            if key in seen:
                continue
            seen.add(key)
            recipients.append(
                {
                    "user": user,
                    "role_name": role_name_by_user_id.get(user.id) or "Assigned Reopen Reviewer",
                    "email": email,
                }
            )
        return recipients

    @classmethod
    def send_reopen_request_notifications(cls, *, request_obj: GradeSubmissionReopenRequest):
        recipients = cls._recipient_rows(request_obj=request_obj)
        if not recipients:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "no_review_recipients"}

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@teachermateplus.local")
        subject = format_email_subject(cls.SUBJECT_MESSAGE)
        requester_name = (
            getattr(request_obj.requested_by_user, "full_name", None)
            or request_obj.requested_by_user.get_full_name()
            or request_obj.requested_by_user.username
        )
        context_base = {
            "school_name": cls.SCHOOL_NAME,
            "subject_message": cls.SUBJECT_MESSAGE,
            "campus_name": request_obj.campus.name,
            "course_title": request_obj.offering.course.title,
            "course_code": request_obj.offering.course.code,
            "section_name": request_obj.offering.section.name or request_obj.offering.section.code,
            "period_name": request_obj.template_period.name or request_obj.template_period.code,
            "reference_no": f"GBR-{request_obj.id:06d}",
            "submitted_at": timezone.localtime(request_obj.created_at),
            "requester_name": requester_name,
            "justification": request_obj.justification,
            "submission_status": request_obj.submission.status,
        }

        sent = 0
        errors = []
        notified_emails = []
        for recipient in recipients:
            context = {**context_base, "recipient_role_name": recipient["role_name"]}
            message = EmailMultiAlternatives(subject=subject, body="", from_email=from_email, to=[recipient["email"]])
            logo_context = cls._logo_context()
            context.update(logo_context)
            attach_logo_for_src(
                message,
                src=logo_context["email_logo_src"],
                filename="ncba-logo.png",
                cid="ncba-logo",
                configured_path=getattr(settings, "EMAIL_SCHOOL_LOGO_PATH", ""),
            )
            text_body = render_to_string("grading/emails/gradebook_reopen_request_notification.txt", context)
            html_body = render_to_string("grading/emails/gradebook_reopen_request_notification.html", context)
            message.body = text_body
            message.attach_alternative(html_body, "text/html")
            try:
                result = message.send(fail_silently=False)
            except Exception as exc:  # pragma: no cover
                errors.append({"email": recipient["email"], "error": str(exc)})
                continue
            if result:
                sent += 1
                notified_emails.append(recipient["email"])

        return {
            "attempted": len(recipients),
            "sent": sent,
            "errors": errors,
            "recipients": notified_emails,
            "reason": None if sent else "send_failed",
        }
