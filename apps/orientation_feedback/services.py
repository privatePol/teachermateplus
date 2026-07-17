from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.utils import timezone

from apps.core.services.audit import AuditService
from apps.core.services.client_ip import resolve_client_ip
from apps.core.services.email_assets import format_email_subject
from apps.core.services.features import FeatureSettingsService
from apps.orientation_feedback.models import (
    OrientationSurveyAnswer,
    OrientationSurveyAnswerChoice,
    OrientationSurveyChoice,
    OrientationSurveyParticipation,
    OrientationSurveyQuestion,
    OrientationSurveyResponse,
    OrientationSurveySession,
)
from apps.orientation_feedback.questions import SCALE_CHOICES, definitions_for
from apps.rbac.models import UserRole


logger = logging.getLogger(__name__)


class OrientationSurveyDuplicateResponse(ValidationError):
    pass


class OrientationSurveyRateLimited(ValidationError):
    pass


class OrientationSurveyEmailDeliveryError(ValidationError):
    pass


class OrientationSurveySessionService:
    DEFAULT_HEAD_ROLE_CODES = ("AC", "COLLEGE_DEAN", "CAO")

    @classmethod
    @transaction.atomic
    def create_draft(cls, *, form, user, tenant, campus, request=None):
        session = form.save(commit=False)
        session.tenant = tenant
        session.campus = campus
        session.created_by = user
        session.status = OrientationSurveySession.Status.DRAFT
        session.full_clean()
        session.save()
        form.save_m2m()
        cls.seed_questions(session)
        cls._audit("ORIENTATION_SURVEY_CREATED", session, user, request)
        return session

    @classmethod
    @transaction.atomic
    def update_draft(cls, *, session, form, user, request=None):
        row = OrientationSurveySession.objects.select_for_update().get(pk=session.pk)
        if row.status != OrientationSurveySession.Status.DRAFT:
            raise ValidationError("Only a draft survey may be edited.")
        old_type = row.survey_type
        before = {"survey_type": row.survey_type, "title": row.title}
        updated = form.save(commit=False)
        for field in (
            "survey_type",
            "title",
            "description",
            "academic_year",
            "term",
            "orientation_date",
            "intended_start_time",
            "intended_end_time",
            "auto_close_at",
        ):
            setattr(row, field, getattr(updated, field))
        row.full_clean()
        row.save()
        row.eligible_head_roles.set(form.cleaned_data["eligible_head_roles"])
        if old_type != row.survey_type:
            row.questions.all().delete()
            cls.seed_questions(row)
        AuditService.log_event(
            action="ORIENTATION_SURVEY_EDITED",
            portal="ADMIN",
            entity_type="OrientationSurveySession",
            entity_id=row.public_id,
            actor=user,
            tenant=row.tenant,
            campus=row.campus,
            before_data=before,
            after_data={"survey_type": row.survey_type, "title": row.title},
            request=request,
        )
        return row

    @staticmethod
    def seed_questions(session):
        if session.questions.exists():
            return
        for definition in definitions_for(session.survey_type):
            options = definition.pop("options", None)
            question = OrientationSurveyQuestion.objects.create(session=session, **definition)
            if question.question_type == OrientationSurveyQuestion.QuestionType.SCALE:
                option_rows = [
                    {
                        "code": code,
                        "label": label,
                        "emoji": emoji,
                        "score": score,
                        "display_order": index,
                        "allows_other_text": False,
                    }
                    for index, (code, label, emoji, score) in enumerate(
                        SCALE_CHOICES[question.scale_kind],
                        start=1,
                    )
                ]
            else:
                option_rows = options or []
            OrientationSurveyChoice.objects.bulk_create(
                [OrientationSurveyChoice(question=question, **row) for row in option_rows]
            )

    @classmethod
    @transaction.atomic
    def update_questions(cls, *, session, formset, user, request=None):
        row = OrientationSurveySession.objects.select_for_update().get(pk=session.pk)
        if row.status != OrientationSurveySession.Status.DRAFT:
            raise ValidationError("Published survey questions cannot be edited.")
        expected_ids = set(row.questions.values_list("id", flat=True))
        submitted_ids = {form.instance.id for form in formset.forms if form.instance.id}
        if expected_ids != submitted_ids:
            raise ValidationError("Survey question set does not match this draft.")
        changed_codes = []
        for form in formset.forms:
            if form.has_changed():
                question = form.save(commit=False)
                if question.session_id != row.id:
                    raise PermissionDenied("Question is outside this survey session.")
                question.full_clean()
                question.save(update_fields=["text", "is_required", "updated_at"])
                changed_codes.append(question.code)
        AuditService.log_event(
            action="ORIENTATION_SURVEY_QUESTIONS_EDITED",
            portal="ADMIN",
            entity_type="OrientationSurveySession",
            entity_id=row.public_id,
            actor=user,
            tenant=row.tenant,
            campus=row.campus,
            metadata={"changed_question_codes": changed_codes},
            request=request,
        )
        return row

    @classmethod
    def _eligible_role_rows(cls, session):
        roles = UserRole.objects.filter(
            is_active=True,
            role__is_active=True,
            user__email__gt="",
        )
        if session.survey_type == OrientationSurveySession.SurveyType.FACULTY:
            roles = roles.filter(
                role__code="FACULTY",
                tenant_id=session.tenant_id,
            ).filter(Q(campus_id=session.campus_id) | Q(campus__isnull=True))
        else:
            role_ids = list(session.eligible_head_roles.values_list("id", flat=True))
            if not role_ids:
                raise ValidationError("Select at least one eligible academic-head role before starting.")
            roles = roles.filter(role_id__in=role_ids).filter(
                Q(tenant_id=session.tenant_id) | Q(tenant__isnull=True),
                Q(campus_id=session.campus_id) | Q(campus__isnull=True),
            )
        return roles.values_list("user_id", "role__code").order_by("user_id", "role__code")

    @classmethod
    @transaction.atomic
    def start(cls, *, session, user, request=None, now=None):
        row = OrientationSurveySession.objects.select_for_update().get(pk=session.pk)
        if row.status != OrientationSurveySession.Status.DRAFT:
            raise ValidationError("Only a draft survey can be started.")
        if not row.questions.exists() or row.questions.filter(
            question_type=OrientationSurveyQuestion.QuestionType.SCALE,
            choices__isnull=True,
        ).exists():
            raise ValidationError("The survey question snapshot is incomplete.")
        by_user = defaultdict(list)
        for user_id, role_code in cls._eligible_role_rows(row):
            by_user[user_id].append(role_code)
        if not by_user:
            raise ValidationError("No eligible users were found in this tenant and campus scope.")
        OrientationSurveyParticipation.objects.bulk_create(
            [
                OrientationSurveyParticipation(
                    session=row,
                    user_id=user_id,
                    eligible_role_codes_snapshot=sorted(set(role_codes)),
                )
                for user_id, role_codes in by_user.items()
            ],
            ignore_conflicts=True,
        )
        now = now or timezone.now()
        row.status = OrientationSurveySession.Status.OPEN
        row.started_by = user
        row.started_at = now
        row.eligible_count_snapshot = row.participations.count()
        row.question_snapshot_version = max(1, row.question_snapshot_version + 1)
        row.full_clean()
        row.save(
            update_fields=[
                "status",
                "started_by",
                "started_at",
                "eligible_count_snapshot",
                "question_snapshot_version",
                "updated_at",
            ]
        )
        cls._audit("ORIENTATION_SURVEY_STARTED", row, user, request)
        cls._audit("ORIENTATION_SURVEY_PUBLIC_LINK_ACTIVATED", row, user, request)
        return row

    @classmethod
    @transaction.atomic
    def close(cls, *, session, user, request=None, now=None):
        row = OrientationSurveySession.objects.select_for_update().get(pk=session.pk)
        cls.refresh_auto_close(row, now=now)
        if row.status != OrientationSurveySession.Status.OPEN:
            raise ValidationError("Only an open survey can be ended.")
        row.status = OrientationSurveySession.Status.CLOSED
        row.closed_by = user
        row.closed_at = now or timezone.now()
        row.closure_reason = OrientationSurveySession.ClosureReason.MANUAL
        row.save(update_fields=["status", "closed_by", "closed_at", "closure_reason", "updated_at"])
        cls._audit("ORIENTATION_SURVEY_CLOSED", row, user, request)
        return row

    @classmethod
    @transaction.atomic
    def cancel(cls, *, session, user, reason, request=None, now=None):
        row = OrientationSurveySession.objects.select_for_update().get(pk=session.pk)
        if row.status not in {OrientationSurveySession.Status.DRAFT, OrientationSurveySession.Status.OPEN}:
            raise ValidationError("This survey can no longer be cancelled.")
        clean_reason = " ".join((reason or "").split())
        if not clean_reason:
            raise ValidationError("A cancellation reason is required.")
        row.status = OrientationSurveySession.Status.CANCELLED
        row.cancelled_by = user
        row.cancelled_at = now or timezone.now()
        row.cancellation_reason = clean_reason
        row.full_clean()
        row.save(
            update_fields=[
                "status",
                "cancelled_by",
                "cancelled_at",
                "cancellation_reason",
                "updated_at",
            ]
        )
        cls._audit("ORIENTATION_SURVEY_CANCELLED", row, user, request, reason=clean_reason)
        return row

    @classmethod
    def refresh_auto_close(cls, session, *, now=None):
        now = now or timezone.now()
        if (
            session.status == OrientationSurveySession.Status.OPEN
            and session.auto_close_at
            and session.auto_close_at <= now
        ):
            closed_at = session.auto_close_at
            updated = OrientationSurveySession.objects.filter(
                pk=session.pk,
                status=OrientationSurveySession.Status.OPEN,
                auto_close_at__lte=now,
            ).update(
                status=OrientationSurveySession.Status.CLOSED,
                closed_at=closed_at,
                closure_reason=OrientationSurveySession.ClosureReason.AUTOMATIC,
                updated_at=now,
            )
            if updated:
                session.status = OrientationSurveySession.Status.CLOSED
                session.closed_at = closed_at
                session.closure_reason = OrientationSurveySession.ClosureReason.AUTOMATIC
                AuditService.log_event(
                    action="ORIENTATION_SURVEY_AUTO_CLOSED",
                    portal="SYSTEM",
                    entity_type="OrientationSurveySession",
                    entity_id=session.public_id,
                    tenant=session.tenant_id,
                    campus=session.campus_id,
                    metadata={"reason": "AUTO_CLOSE_AT_REACHED"},
                )
            else:
                session.refresh_from_db()
        return session

    @staticmethod
    def _audit(action, session, user, request, *, reason=""):
        metadata = {
            "survey_type": session.survey_type,
            "status": session.status,
            "eligible_count_snapshot": session.eligible_count_snapshot,
        }
        if reason:
            metadata["reason"] = reason
        AuditService.log_event(
            action=action,
            portal="ADMIN",
            entity_type="OrientationSurveySession",
            entity_id=session.public_id,
            actor=user,
            tenant=session.tenant,
            campus=session.campus,
            metadata=metadata,
            request=request,
        )


class OrientationSurveyBrowserService:
    COOKIE_NAME = "orientation_feedback_client"
    COOKIE_SALT = "teachermateplus.orientation-feedback.browser.v1"
    MAX_AGE_SECONDS = 8 * 60 * 60

    @classmethod
    def resolve_or_create(cls, request):
        signed_value = request.COOKIES.get(cls.COOKIE_NAME, "")
        raw_value = ""
        if signed_value:
            try:
                payload = signing.loads(signed_value, salt=cls.COOKIE_SALT, max_age=cls.MAX_AGE_SECONDS)
                candidate = payload.get("client", "") if isinstance(payload, dict) else ""
                if re.fullmatch(r"[A-Za-z0-9_-]{32,64}", candidate):
                    raw_value = candidate
            except signing.BadSignature:
                raw_value = ""
        if not raw_value:
            raw_value = secrets.token_urlsafe(32)
            signed_value = signing.dumps({"client": raw_value}, salt=cls.COOKIE_SALT, compress=True)
        return raw_value, signed_value

    @staticmethod
    def hash_for_session(*, session, raw_value):
        message = f"{session.public_id}:{raw_value}".encode("utf-8")
        return hmac.new(settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()

    @classmethod
    def set_cookie(cls, response, signed_value):
        response.set_cookie(
            cls.COOKIE_NAME,
            signed_value,
            max_age=cls.MAX_AGE_SECONDS,
            httponly=True,
            secure=bool(getattr(settings, "SESSION_COOKIE_SECURE", False)),
            samesite="Lax",
        )


@dataclass(frozen=True)
class VerifiedOrientationParticipant:
    session: OrientationSurveySession
    participation: OrientationSurveyParticipation


class OrientationSurveyPublicService:
    TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
    STATE_KEY = "orientation_feedback_verified_participant"
    STATE_MAX_AGE_SECONDS = 4 * 60 * 60
    DEFAULT_OTP_EXPIRY_MINUTES = 10
    DEFAULT_OTP_MAX_ATTEMPTS = 5
    PRIVACY_NOTICE_VERSION = "2026-07-orientation-feedback-v1"
    PRIVACY_NOTICE = (
        "Your identity is used only to confirm eligibility and prevent duplicate responses. "
        "Your answers will be shown in reports without your name."
    )
    VALIDATION_ERROR = (
        "We could not validate the information provided for this survey. Please check your entry "
        "or ask the facilitator for assistance."
    )

    @classmethod
    def resolve_session(cls, public_token, *, refresh=True):
        if not cls.TOKEN_PATTERN.fullmatch(public_token or ""):
            return None
        session = (
            OrientationSurveySession.objects.select_related("tenant", "campus", "academic_year", "term")
            .filter(public_token=public_token)
            .first()
        )
        if session and refresh:
            OrientationSurveySessionService.refresh_auto_close(session)
        return session

    @staticmethod
    def state(session):
        if session is None:
            return "invalid", "This orientation feedback link is invalid."
        if session.status == OrientationSurveySession.Status.DRAFT:
            return "draft", "This survey has not started yet."
        if session.status == OrientationSurveySession.Status.CLOSED:
            return "closed", "This survey has ended and is no longer accepting responses."
        if session.status == OrientationSurveySession.Status.CANCELLED:
            return "cancelled", "This survey has been cancelled by the facilitator."
        if not FeatureSettingsService.is_orientation_feedback_enabled(
            tenant_id=session.tenant_id,
            default=True,
        ):
            return "closed", "Orientation feedback surveys are currently unavailable."
        return "open", ""

    @staticmethod
    def match_participation(*, session, email):
        normalized = (email or "").strip()
        if not normalized:
            return None
        matches = list(
            OrientationSurveyParticipation.objects.select_related("user", "session__campus")
            .filter(session=session, user__email__iexact=normalized)
            .order_by("id")[:2]
        )
        return matches[0] if len(matches) == 1 else None

    @classmethod
    def store_pending(cls, *, request, session, participation, browser_hash, now=None):
        now = now or timezone.now()
        request.session[cls.STATE_KEY] = {
            "session_public_id": str(session.public_id),
            "participation_id": participation.id,
            "browser_hash": browser_hash,
            "validated_at": now.isoformat(),
            "confirmed": False,
        }
        request.session.modified = True

    @classmethod
    def clear_state(cls, request):
        if cls.STATE_KEY in request.session:
            del request.session[cls.STATE_KEY]
            request.session.modified = True

    @classmethod
    def resolve_state(cls, *, request, raw_browser_value, require_confirmed=True, now=None):
        now = now or timezone.now()
        payload = request.session.get(cls.STATE_KEY)
        if not isinstance(payload, dict):
            return None
        try:
            validated_at = datetime.fromisoformat(str(payload.get("validated_at", "")))
            if timezone.is_naive(validated_at):
                raise ValueError
            age_seconds = (now - validated_at).total_seconds()
            session_public_id = UUID(str(payload.get("session_public_id", "")))
            participation_id = int(payload.get("participation_id"))
        except (TypeError, ValueError):
            cls.clear_state(request)
            return None
        if not 0 <= age_seconds <= cls.STATE_MAX_AGE_SECONDS:
            cls.clear_state(request)
            return None
        session = OrientationSurveySession.objects.select_related("tenant", "campus").filter(
            public_id=session_public_id
        ).first()
        if not session:
            cls.clear_state(request)
            return None
        expected_hash = OrientationSurveyBrowserService.hash_for_session(
            session=session,
            raw_value=raw_browser_value,
        )
        if not hmac.compare_digest(str(payload.get("browser_hash", "")), expected_hash):
            cls.clear_state(request)
            return None
        OrientationSurveySessionService.refresh_auto_close(session, now=now)
        state, _ = cls.state(session)
        if state != "open" or (require_confirmed and not payload.get("confirmed")):
            if state != "open":
                cls.clear_state(request)
            return None
        participation = OrientationSurveyParticipation.objects.select_related("user").filter(
            pk=participation_id,
            session=session,
        ).first()
        if not participation:
            cls.clear_state(request)
            return None
        return VerifiedOrientationParticipant(session=session, participation=participation)

    @staticmethod
    def _otp_expiry_minutes():
        return max(
            1,
            int(
                getattr(
                    settings,
                    "ORIENTATION_FEEDBACK_EMAIL_OTP_EXPIRY_MINUTES",
                    OrientationSurveyPublicService.DEFAULT_OTP_EXPIRY_MINUTES,
                )
                or OrientationSurveyPublicService.DEFAULT_OTP_EXPIRY_MINUTES
            ),
        )

    @staticmethod
    def _otp_max_attempts():
        return max(
            1,
            int(
                getattr(
                    settings,
                    "ORIENTATION_FEEDBACK_EMAIL_OTP_MAX_ATTEMPTS",
                    OrientationSurveyPublicService.DEFAULT_OTP_MAX_ATTEMPTS,
                )
                or OrientationSurveyPublicService.DEFAULT_OTP_MAX_ATTEMPTS
            ),
        )

    @staticmethod
    def _generate_otp():
        return f"{secrets.randbelow(1_000_000):06d}"

    @classmethod
    def _send_otp_email(cls, *, participation, code):
        context = {
            "session": participation.session,
            "otp_code": code,
            "expires_in_minutes": cls._otp_expiry_minutes(),
        }
        text_body = render_to_string("orientation_feedback/emails/verification_code.txt", context)
        html_body = render_to_string("orientation_feedback/emails/verification_code.html", context)
        message = EmailMultiAlternatives(
            subject=format_email_subject("Orientation Feedback Verification Code"),
            body=text_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@teachermateplus.local"),
            to=[participation.user.email],
        )
        message.attach_alternative(html_body, "text/html")
        return message.send(fail_silently=False)

    @classmethod
    def start_email_verification(
        cls,
        *,
        request,
        session,
        participation,
        browser_hash,
        now=None,
    ):
        now = now or timezone.now()
        code = cls._generate_otp()
        with transaction.atomic():
            locked = (
                OrientationSurveyParticipation.objects.select_for_update()
                .select_related("user", "session")
                .get(pk=participation.pk, session=session)
            )
            if hasattr(locked, "response"):
                raise OrientationSurveyDuplicateResponse(
                    "You have already submitted a response for this survey. Thank you for participating."
                )
            locked.validation_method = OrientationSurveyParticipation.ValidationMethod.EMAIL_OTP
            locked.email_otp_hash = make_password(code)
            locked.email_otp_sent_at = now
            locked.email_otp_expires_at = now + timedelta(minutes=cls._otp_expiry_minutes())
            locked.email_otp_failed_attempts = 0
            locked.email_verified_at = None
            locked.save(
                update_fields=[
                    "validation_method",
                    "email_otp_hash",
                    "email_otp_sent_at",
                    "email_otp_expires_at",
                    "email_otp_failed_attempts",
                    "email_verified_at",
                    "updated_at",
                ]
            )
        try:
            sent_count = cls._send_otp_email(participation=locked, code=code)
        except Exception as exc:
            logger.exception(
                "Orientation feedback verification email delivery failed for session %s",
                session.public_id,
            )
            sent_count = 0
            delivery_error = exc
        else:
            delivery_error = None
        if sent_count <= 0:
            OrientationSurveyParticipation.objects.filter(pk=locked.pk).update(
                email_otp_hash="",
                email_otp_expires_at=None,
                email_otp_failed_attempts=0,
            )
            cls.clear_state(request)
            raise OrientationSurveyEmailDeliveryError(
                "TeacherMate+ could not send the verification code. Please try again or ask the facilitator for help."
            ) from delivery_error
        cls.store_pending(
            request=request,
            session=session,
            participation=locked,
            browser_hash=browser_hash,
            now=now,
        )
        return locked

    @classmethod
    def verify_email_otp(cls, *, request, raw_browser_value, code, now=None):
        verified = cls.resolve_state(
            request=request,
            raw_browser_value=raw_browser_value,
            require_confirmed=False,
            now=now,
        )
        if not verified:
            raise ValidationError("Email verification has expired. Please validate again.")
        now = now or timezone.now()
        error_message = ""
        clear_pending = False
        with transaction.atomic():
            participation = OrientationSurveyParticipation.objects.select_for_update().get(
                pk=verified.participation.pk,
                session=verified.session,
            )
            if hasattr(participation, "response"):
                raise OrientationSurveyDuplicateResponse(
                    "You have already submitted a response for this survey. Thank you for participating."
                )
            if (
                not participation.email_otp_hash
                or not participation.email_otp_expires_at
                or participation.email_otp_expires_at <= now
            ):
                participation.email_otp_hash = ""
                participation.save(update_fields=["email_otp_hash", "updated_at"])
                error_message = "The verification code has expired. Please validate again."
                clear_pending = True
            else:
                max_attempts = cls._otp_max_attempts()
                if participation.email_otp_failed_attempts >= max_attempts:
                    error_message = "Too many incorrect verification attempts. Please validate again."
                    clear_pending = True
                else:
                    normalized_code = str(code or "").strip().replace(" ", "")
                    if not check_password(normalized_code, participation.email_otp_hash):
                        participation.email_otp_failed_attempts += 1
                        participation.save(update_fields=["email_otp_failed_attempts", "updated_at"])
                        if participation.email_otp_failed_attempts >= max_attempts:
                            error_message = "Too many incorrect verification attempts. Please validate again."
                            clear_pending = True
                        else:
                            error_message = "The verification code is incorrect."
                    else:
                        participation.validated_at = now
                        participation.email_verified_at = now
                        participation.email_otp_hash = ""
                        participation.email_otp_failed_attempts = 0
                        participation.save(
                            update_fields=[
                                "validated_at",
                                "email_verified_at",
                                "email_otp_hash",
                                "email_otp_failed_attempts",
                                "updated_at",
                            ]
                        )
        if error_message:
            if clear_pending:
                cls.clear_state(request)
            raise ValidationError(error_message)
        request.session[cls.STATE_KEY]["confirmed"] = True
        request.session.modified = True
        return VerifiedOrientationParticipant(session=verified.session, participation=participation)


class OrientationSurveyRateLimitService:
    WINDOW_SECONDS = 60

    @staticmethod
    def _increment(key, *, timeout):
        if cache.add(key, 1, timeout=timeout):
            return 1
        try:
            return cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=timeout)
            return 1

    @classmethod
    def check(cls, *, request, session, browser_hash, purpose):
        bucket = timezone.now().strftime("%Y%m%d%H%M")
        browser_limit = int(
            getattr(settings, "ORIENTATION_FEEDBACK_BROWSER_RATE_LIMIT_PER_MINUTE", 6) or 6
        )
        ip_limit = int(getattr(settings, "ORIENTATION_FEEDBACK_IP_RATE_LIMIT_PER_MINUTE", 60) or 60)
        browser_key = f"orientation-feedback:{purpose}:browser:{session.public_id}:{browser_hash[:16]}:{bucket}"
        ip_address = resolve_client_ip(request) or "unknown"
        ip_hash = hashlib.sha256(ip_address.encode("utf-8")).hexdigest()[:20]
        ip_key = f"orientation-feedback:{purpose}:ip:{session.public_id}:{ip_hash}:{bucket}"
        if cls._increment(browser_key, timeout=65) > browser_limit or cls._increment(
            ip_key,
            timeout=65,
        ) > ip_limit:
            AuditService.log_event(
                action="ORIENTATION_SURVEY_VALIDATION_THROTTLED",
                portal="PUBLIC",
                entity_type="OrientationSurveySession",
                entity_id=session.public_id,
                tenant=session.tenant_id,
                campus=session.campus_id,
                metadata={"purpose": purpose},
                request=request,
            )
            raise OrientationSurveyRateLimited("Too many attempts. Please wait a moment and try again.")


class OrientationSurveyResponseService:
    @classmethod
    @transaction.atomic
    def submit(cls, *, verified, cleaned_data, request=None, now=None):
        now = now or timezone.now()
        session = OrientationSurveySession.objects.select_for_update().get(pk=verified.session.pk)
        OrientationSurveySessionService.refresh_auto_close(session, now=now)
        if session.status != OrientationSurveySession.Status.OPEN:
            raise ValidationError("This survey is no longer accepting responses.")
        participation = OrientationSurveyParticipation.objects.select_for_update().get(
            pk=verified.participation.pk,
            session=session,
        )
        if OrientationSurveyResponse.objects.filter(participation=participation).exists():
            raise OrientationSurveyDuplicateResponse(
                "You have already submitted a response for this survey. Thank you for participating."
            )
        questions = list(session.questions.prefetch_related("choices").order_by("display_order"))
        try:
            with transaction.atomic():
                response = OrientationSurveyResponse(session=session, participation=participation)
                response.full_clean()
                response.save()
                for question in questions:
                    field_name = f"q_{question.code}"
                    value = cleaned_data.get(field_name)
                    other_text = (cleaned_data.get(f"other_{question.code}") or "").strip()
                    if question.question_type == OrientationSurveyQuestion.QuestionType.TEXT:
                        text_value = (value or "").strip()
                        if not text_value and not question.is_required:
                            continue
                        answer = OrientationSurveyAnswer(
                            response=response,
                            question=question,
                            text_value=text_value,
                        )
                        answer.full_clean()
                        answer.save()
                        continue
                    selected_codes = [value] if isinstance(value, str) else list(value or [])
                    if not selected_codes and not question.is_required:
                        continue
                    valid_choices = {
                        choice.code: choice
                        for choice in question.choices.all()
                    }
                    if not selected_codes or any(code not in valid_choices for code in selected_codes):
                        raise ValidationError("A submitted answer is not valid for this survey.")
                    if question.question_type == OrientationSurveyQuestion.QuestionType.SCALE and len(selected_codes) != 1:
                        raise ValidationError("Choose one response for each scale question.")
                    if question.question_type == OrientationSurveyQuestion.QuestionType.MULTI_SELECT:
                        selected_labels = {valid_choices[code].label for code in selected_codes}
                        if "None at the moment" in selected_labels and len(selected_codes) > 1:
                            raise ValidationError(
                                "Choose either None at the moment or the applicable guidance areas."
                            )
                        if not any(valid_choices[code].allows_other_text for code in selected_codes):
                            other_text = ""
                    answer = OrientationSurveyAnswer(
                        response=response,
                        question=question,
                        text_value=other_text,
                    )
                    answer.full_clean()
                    answer.save()
                    for code in selected_codes:
                        selection = OrientationSurveyAnswerChoice(
                            answer=answer,
                            choice=valid_choices[code],
                        )
                        selection.full_clean()
                        selection.save()
                participation.submitted_at = now
                participation.save(update_fields=["submitted_at", "updated_at"])
        except IntegrityError as exc:
            raise OrientationSurveyDuplicateResponse(
                "You have already submitted a response for this survey. Thank you for participating."
            ) from exc
        AuditService.log_event(
            action="ORIENTATION_SURVEY_PARTICIPATION_COMPLETED",
            portal="PUBLIC",
            entity_type="OrientationSurveySession",
            entity_id=session.public_id,
            tenant=session.tenant_id,
            campus=session.campus_id,
            metadata={},
            # Public response network metadata is deliberately not retained on
            # completion events because it can be correlated with submission time.
            request=None,
        )
        return response


class OrientationSurveyAnalyticsService:
    DEFAULT_MINIMUM_RESPONSES = 5

    @classmethod
    def minimum_responses(cls):
        return max(
            cls.DEFAULT_MINIMUM_RESPONSES,
            int(
                getattr(
                    settings,
                    "ORIENTATION_FEEDBACK_MINIMUM_REPORT_RESPONSES",
                    cls.DEFAULT_MINIMUM_RESPONSES,
                )
                or cls.DEFAULT_MINIMUM_RESPONSES
            ),
        )

    @staticmethod
    def _percent(value, total):
        if not total:
            return Decimal("0.0")
        return (Decimal(value) * Decimal("100") / Decimal(total)).quantize(
            Decimal("0.1"),
            rounding=ROUND_HALF_UP,
        )

    @staticmethod
    def _interpret(mean, scale_kind):
        if mean is None:
            return "No responses"
        bands = {
            OrientationSurveyQuestion.ScaleKind.AGREEMENT: (
                "Strongly Agree",
                "Agree",
                "Neutral",
                "Disagree",
                "Strongly Disagree",
            ),
            OrientationSurveyQuestion.ScaleKind.QUALITY: (
                "Excellent",
                "Good",
                "Fair",
                "Needs Improvement",
                "Poor",
            ),
            OrientationSurveyQuestion.ScaleKind.EASE: (
                "Very Easy",
                "Easy",
                "Neutral",
                "Difficult",
                "Very Difficult",
            ),
            OrientationSurveyQuestion.ScaleKind.CLARITY: (
                "Very Clear",
                "Clear",
                "Neutral",
                "Unclear",
                "Very Unclear",
            ),
            OrientationSurveyQuestion.ScaleKind.PACE: (
                "Very Appropriate",
                "Appropriate",
                "Neutral",
                "Needs Adjustment",
                "Not Appropriate",
            ),
            OrientationSurveyQuestion.ScaleKind.CONFIDENCE: (
                "Very Confident",
                "Confident",
                "Somewhat Confident",
                "Not Yet Confident",
                "Not Confident",
            ),
            OrientationSurveyQuestion.ScaleKind.READINESS: (
                "Yes, ready",
                "Mostly ready",
                "Needs a little practice",
                "Needs additional guidance",
                "Not yet ready",
            ),
        }
        labels = bands.get(scale_kind, bands[OrientationSurveyQuestion.ScaleKind.AGREEMENT])
        if mean >= Decimal("4.21"):
            return labels[0]
        if mean >= Decimal("3.41"):
            return labels[1]
        if mean >= Decimal("2.61"):
            return labels[2]
        if mean >= Decimal("1.81"):
            return labels[3]
        return labels[4]

    @classmethod
    def build(cls, session):
        completed = session.responses.count()
        eligible = session.eligible_count_snapshot
        response_rate = cls._percent(completed, eligible) if eligible is not None else None
        minimum_responses = cls.minimum_responses()
        results_released = completed >= minimum_responses
        if not results_released:
            return {
                "eligible": eligible,
                "completed": completed,
                "response_rate": response_rate,
                "minimum_responses": minimum_responses,
                "results_released": False,
                "overall_rating": None,
                "confidence": None,
                "readiness": None,
                "scale_rows": [],
                "checkbox_rows": [],
                "comments": [],
                "indexes": [],
            }
        scale_rows = []
        checkbox_rows = []
        comments = []
        index_values = defaultdict(list)
        summary_means = {}
        questions = session.questions.prefetch_related("choices").order_by("display_order")
        for question in questions:
            if question.question_type == OrientationSurveyQuestion.QuestionType.SCALE:
                count_rows = {
                    row["choice_id"]: row["total"]
                    for row in OrientationSurveyAnswerChoice.objects.filter(
                        answer__response__session=session,
                        answer__question=question,
                    ).values("choice_id").annotate(total=Count("id"))
                }
                answered = sum(count_rows.values())
                weighted_total = sum(
                    (choice.score or 0) * count_rows.get(choice.id, 0)
                    for choice in question.choices.all()
                )
                mean = (
                    (Decimal(weighted_total) / Decimal(answered)).quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    )
                    if answered
                    else None
                )
                distributions = []
                for choice in question.choices.all():
                    count = count_rows.get(choice.id, 0)
                    distributions.append(
                        {
                            "label": choice.label,
                            "emoji": choice.emoji,
                            "score": choice.score,
                            "count": count,
                            "percent": cls._percent(count, answered),
                        }
                    )
                scale_rows.append(
                    {
                        "question": question,
                        "answered": answered,
                        "unanswered": max(0, completed - answered),
                        "mean": mean,
                        "mean_percent": cls._percent(mean or 0, 5),
                        "interpretation": cls._interpret(mean, question.scale_kind),
                        "distributions": distributions,
                    }
                )
                summary_means[question.code] = mean
                if question.composite_index_code and mean is not None:
                    index_mean = Decimal("6") - mean if question.reverse_scored else mean
                    index_values[question.composite_index_code].append(
                        {
                            "mean": index_mean,
                            "original_mean": mean,
                            "question": question,
                            "reverse_scored": question.reverse_scored,
                            "answered": answered,
                        }
                    )
            elif question.question_type == OrientationSurveyQuestion.QuestionType.MULTI_SELECT:
                count_rows = {
                    row["choice_id"]: row["total"]
                    for row in OrientationSurveyAnswerChoice.objects.filter(
                        answer__response__session=session,
                        answer__question=question,
                    ).values("choice_id").annotate(total=Count("id"))
                }
                options = []
                for choice in question.choices.all():
                    count = count_rows.get(choice.id, 0)
                    options.append(
                        {
                            "label": choice.label,
                            "count": count,
                            "percent": cls._percent(count, completed),
                        }
                    )
                checkbox_rows.append({"question": question, "options": options})
                for answer in question.answers.filter(response__session=session).exclude(text_value=""):
                    if answer.selected_choices.filter(choice__allows_other_text=True).exists():
                        comments.append(
                            {
                                "question": f"{question.text} — Other",
                                "text": answer.text_value,
                            }
                        )
            else:
                for answer in question.answers.filter(response__session=session).exclude(text_value=""):
                    comments.append({"question": question.text, "text": answer.text_value})
        indexes = []
        for code, sources in sorted(index_values.items()):
            mean = (sum(source["mean"] for source in sources) / Decimal(len(sources))).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
            indexes.append(
                {
                    "code": code,
                    "label": code.replace("_", " ").title(),
                    "mean": mean,
                    "sources": sources,
                }
            )
        return {
            "eligible": eligible,
            "completed": completed,
            "response_rate": response_rate,
            "minimum_responses": minimum_responses,
            "results_released": True,
            "overall_rating": summary_means.get("overall_rating"),
            "confidence": summary_means.get("confidence"),
            "readiness": summary_means.get("readiness"),
            "scale_rows": scale_rows,
            "checkbox_rows": checkbox_rows,
            "comments": comments,
            "indexes": indexes,
        }
