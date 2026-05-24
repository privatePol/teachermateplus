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
from apps.grading.models import GradeCorrectionRequest, GradeSubmissionReopenRequest
from apps.grading.reporting import CorrectionOfficialReportService
from apps.rbac.models import UserPermission, UserRole

User = get_user_model()


class CorrectionNotificationService:
    SCHOOL_NAME = "NATIONAL COLLEGE OF BUSINESS AND ARTS"
    SUBJECT_MESSAGE = "Petition for Correction of Grades Awaiting Your Approval"

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
    def send_correction_submission_approval_notifications(cls, *, request_obj: GradeCorrectionRequest):
        if not FeatureSettingsService.is_correction_submission_approval_email_enabled(tenant_id=request_obj.tenant_id):
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "feature_disabled"}

        recipients = cls._role_recipient_rows(request_obj=request_obj)
        if not recipients:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "no_matching_role_recipients"}

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@EduGrade+.local")
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

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@EduGrade+.local")
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
        candidate_user_ids.update(User.objects.filter(is_active=True, is_superuser=True).values_list("id", flat=True))

        role_name_by_user_id = {}
        for row in role_rows:
            role_name_by_user_id.setdefault(row.user_id, row.role.name)

        recipients = []
        seen = set()
        users = User.objects.filter(id__in=candidate_user_ids, is_active=True).order_by("last_name", "first_name", "id")
        for user in users:
            if not PermissionService.has_permission(
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
            role_name = role_name_by_user_id.get(user.id)
            if not role_name and user.is_superuser:
                role_name = "Superuser"
            recipients.append({"user": user, "role_name": role_name or "Authorized Reopen Reviewer", "email": email})
        return recipients

    @classmethod
    def send_reopen_request_notifications(cls, *, request_obj: GradeSubmissionReopenRequest):
        recipients = cls._recipient_rows(request_obj=request_obj)
        if not recipients:
            return {"attempted": 0, "sent": 0, "errors": [], "recipients": [], "reason": "no_review_recipients"}

        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@EduGrade+.local")
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
