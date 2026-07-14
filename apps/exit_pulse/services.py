from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import hmac
import re
import secrets

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.core.services.audit import AuditService
from apps.core.services.client_ip import resolve_client_ip
from apps.enrollment.models import Enrollment
from apps.exit_pulse.models import ExitPulseResponse, ExitPulseSession


class ExitPulseDuplicateResponse(ValidationError):
    pass


class ExitPulseRateLimited(ValidationError):
    pass


class ExitPulseQuestionValidationService:
    PROHIBITED_PATTERNS = (
        r"\brate\s+(my|the|your|our)?\s*(teaching|teacher|instructor|faculty|professor|discussion)\b",
        r"\b(do|did)\s+you\s+like\s+(your|the|my)?\s*(teacher|instructor|faculty|professor|how\s+i\s+teach)\b",
        r"\bwas\s+(your|the|my)?\s*(teacher|instructor|faculty|professor)\s+(effective|good|clear|helpful)\b",
        r"\bwas\s+i\s+(an?\s+)?(effective|good|clear|helpful)\s+teacher\b",
        r"\b(did|does)\s+(the|your)?\s*faculty\s+member\s+explain\s+well\b",
        r"\bdo\s+you\s+like\s+how\s+i\s+teach\b",
        r"\bwas\s+my\s+discussion\s+(boring|interesting|good|bad)\b",
        r"\bteacher\s+rating\b",
        r"\bfaculty\s+(rating|evaluation|score)\b",
        r"\bteaching\s+(rating|score|performance)\b",
        r"\bstar\s+rating\b",
    )
    LEARNING_TERMS = {
        "lesson",
        "topic",
        "activity",
        "understand",
        "understanding",
        "confident",
        "confidence",
        "apply",
        "application",
        "clarification",
        "clarify",
        "explain",
        "explanation",
        "example",
        "examples",
        "practice",
        "learn",
        "learned",
        "learning",
        "review",
        "concept",
        "skill",
    }
    MESSAGE = (
        "Please revise the question so it measures student understanding, confidence, or the need for "
        "additional explanation rather than rating the faculty member."
    )

    @staticmethod
    def normalize(value: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).split())

    @classmethod
    def validate_custom_question(cls, value: str) -> str:
        question = (value or "").strip()
        if not question:
            raise ValidationError("Enter the custom question.")
        if len(question) > 250:
            raise ValidationError("The custom question must be 250 characters or fewer.")
        normalized = cls.normalize(question)
        if any(re.search(pattern, normalized) for pattern in cls.PROHIBITED_PATTERNS):
            raise ValidationError(cls.MESSAGE)
        if not (set(normalized.split()) & cls.LEARNING_TERMS):
            raise ValidationError(cls.MESSAGE)
        return question

    @classmethod
    def question_snapshot(cls, question_code: str, custom_question: str = "") -> str:
        if question_code == ExitPulseSession.QuestionCode.CUSTOM:
            return cls.validate_custom_question(custom_question)
        choices = dict(ExitPulseSession.QuestionCode.choices)
        if question_code not in choices:
            raise ValidationError("Select a valid Exit Pulse question.")
        return choices[question_code]


class ExitPulseSessionService:
    LIVE_DURATION = timedelta(minutes=5)

    @staticmethod
    def valid_assignments_for_user(*, user, tenant_id=None, campus_id=None):
        queryset = (
            FacultyAssignment.objects.filter(
                faculty_user=user,
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                accepted_at__isnull=False,
                offering__is_active=True,
                offering__status=CourseOffering.Status.OPEN,
                offering__tenant__is_active=True,
                offering__campus__is_active=True,
                offering__academic_year__is_active=True,
                offering__term__is_active=True,
                offering__department__is_active=True,
                offering__program__is_active=True,
                offering__program__department__is_active=True,
                offering__course__is_active=True,
                offering__section__is_active=True,
                offering__section__department__is_active=True,
                offering__section__program__is_active=True,
                offering__section__program__department__is_active=True,
            )
            .filter(
                models_q_course_department_active()
            )
            .select_related(
                "offering",
                "offering__tenant",
                "offering__campus",
                "offering__academic_year",
                "offering__term",
                "offering__course",
                "offering__section",
            )
            .order_by("offering__course__title", "offering__course__code", "offering__section__code")
        )
        if tenant_id:
            queryset = queryset.filter(offering__tenant_id=tenant_id)
        if campus_id:
            queryset = queryset.filter(offering__campus_id=campus_id)
        return queryset

    @classmethod
    def validate_assignment_ownership(cls, *, user, assignment, tenant_id=None, campus_id=None):
        if not cls.valid_assignments_for_user(
            user=user,
            tenant_id=tenant_id,
            campus_id=campus_id,
        ).filter(pk=assignment.pk).exists():
            raise PermissionDenied("This faculty assignment is not available for Exit Pulse.")
        return assignment

    @classmethod
    @transaction.atomic
    def create_draft(
        cls,
        *,
        user,
        assignment,
        topic,
        question_code,
        custom_question="",
        allow_written_feedback=False,
        feedback_review_enabled=False,
        feedback_learned_enabled=False,
        tenant_id=None,
        campus_id=None,
        request=None,
    ):
        assignment = cls.validate_assignment_ownership(
            user=user,
            assignment=assignment,
            tenant_id=tenant_id,
            campus_id=campus_id,
        )
        clean_topic = (topic or "").strip()
        if not clean_topic:
            raise ValidationError({"topic": "Enter the lesson topic."})
        if len(clean_topic) > 200:
            raise ValidationError({"topic": "The lesson topic must be 200 characters or fewer."})
        snapshot = ExitPulseQuestionValidationService.question_snapshot(question_code, custom_question)
        allow_written_feedback = bool(allow_written_feedback)
        feedback_review_enabled = bool(allow_written_feedback and feedback_review_enabled)
        feedback_learned_enabled = bool(allow_written_feedback and feedback_learned_enabled)
        if allow_written_feedback and not (feedback_review_enabled or feedback_learned_enabled):
            raise ValidationError("Enable at least one written-feedback prompt.")
        offering = assignment.offering
        session = ExitPulseSession(
            tenant=offering.tenant,
            campus=offering.campus,
            faculty_user=user,
            faculty_assignment=assignment,
            course_offering=offering,
            academic_year=offering.academic_year,
            term=offering.term,
            course=offering.course,
            section=offering.section,
            topic=clean_topic,
            question_code=question_code,
            question_text_snapshot=snapshot,
            custom_question=snapshot if question_code == ExitPulseSession.QuestionCode.CUSTOM else "",
            allow_written_feedback=allow_written_feedback,
            feedback_review_enabled=feedback_review_enabled,
            feedback_review_prompt_snapshot=(
                ExitPulseSession.FEEDBACK_REVIEW_PROMPT if feedback_review_enabled else ""
            ),
            feedback_learned_enabled=feedback_learned_enabled,
            feedback_learned_prompt_snapshot=(
                ExitPulseSession.FEEDBACK_LEARNED_PROMPT if feedback_learned_enabled else ""
            ),
            created_by=user,
        )
        session.full_clean()
        session.save()
        AuditService.log_event(
            action="EXIT_PULSE_CREATED",
            portal="FACULTY",
            entity_type="ExitPulseSession",
            entity_id=session.public_id,
            actor=user,
            tenant=session.tenant,
            campus=session.campus,
            metadata={
                "faculty_assignment_id": assignment.id,
                "course_offering_id": offering.id,
                "question_code": question_code,
                "written_feedback_enabled": allow_written_feedback,
            },
            request=request,
        )
        return session

    @classmethod
    @transaction.atomic
    def start(cls, *, session, user, request=None, now=None):
        row = ExitPulseSession.objects.select_for_update().get(pk=session.pk)
        cls._assert_owner(row, user)
        if row.status != ExitPulseSession.Status.DRAFT:
            raise ValidationError("Only a draft Exit Pulse can be started.")
        now = now or timezone.now()
        row.status = ExitPulseSession.Status.LIVE
        row.started_at = now
        row.expires_at = now + cls.LIVE_DURATION
        row.save(update_fields=["status", "started_at", "expires_at", "updated_at"])
        cls._audit_action("EXIT_PULSE_STARTED", row, user, request)
        return row

    @staticmethod
    def _assert_owner(session, user):
        if session.faculty_user_id != user.id:
            raise PermissionDenied("You cannot access another faculty member's Exit Pulse session.")

    @classmethod
    def refresh_effective_status(cls, session, *, now=None):
        now = now or timezone.now()
        if (
            session.status == ExitPulseSession.Status.LIVE
            and session.expires_at
            and session.expires_at <= now
        ):
            ended_at = session.expires_at
            updated = ExitPulseSession.objects.filter(
                pk=session.pk,
                status=ExitPulseSession.Status.LIVE,
                expires_at__lte=now,
            ).update(status=ExitPulseSession.Status.EXPIRED, closed_at=ended_at, updated_at=now)
            if updated:
                session.status = ExitPulseSession.Status.EXPIRED
                session.closed_at = ended_at
                session.updated_at = now
            else:
                session.refresh_from_db()
        return session

    @classmethod
    @transaction.atomic
    def extend(cls, *, session, user, request=None, now=None):
        row = ExitPulseSession.objects.select_for_update().get(pk=session.pk)
        cls._assert_owner(row, user)
        now = now or timezone.now()
        cls.refresh_effective_status(row, now=now)
        if row.status != ExitPulseSession.Status.LIVE:
            raise ValidationError("Only a live Exit Pulse can be extended.")
        if row.extension_count >= 1:
            raise ValidationError("This Exit Pulse has already used its one extension.")
        row.expires_at = row.expires_at + cls.LIVE_DURATION
        row.extended_at = now
        row.extension_count = 1
        row.save(update_fields=["expires_at", "extended_at", "extension_count", "updated_at"])
        cls._audit_action("EXIT_PULSE_EXTENDED", row, user, request)
        return row

    @classmethod
    @transaction.atomic
    def close(cls, *, session, user, request=None, now=None):
        row = ExitPulseSession.objects.select_for_update().get(pk=session.pk)
        cls._assert_owner(row, user)
        now = now or timezone.now()
        cls.refresh_effective_status(row, now=now)
        if row.status != ExitPulseSession.Status.LIVE:
            raise ValidationError("Only a live Exit Pulse can be closed.")
        row.status = ExitPulseSession.Status.CLOSED
        row.closed_at = now
        row.save(update_fields=["status", "closed_at", "updated_at"])
        cls._audit_action("EXIT_PULSE_CLOSED", row, user, request)
        return row

    @classmethod
    @transaction.atomic
    def cancel(cls, *, session, user, request=None, now=None):
        row = ExitPulseSession.objects.select_for_update().get(pk=session.pk)
        cls._assert_owner(row, user)
        now = now or timezone.now()
        cls.refresh_effective_status(row, now=now)
        if row.status not in {ExitPulseSession.Status.DRAFT, ExitPulseSession.Status.LIVE}:
            raise ValidationError("This Exit Pulse can no longer be cancelled.")
        row.status = ExitPulseSession.Status.CANCELLED
        row.cancelled_at = now
        row.save(update_fields=["status", "cancelled_at", "updated_at"])
        cls._audit_action("EXIT_PULSE_CANCELLED", row, user, request)
        return row

    @staticmethod
    def _audit_action(action, session, user, request):
        AuditService.log_event(
            action=action,
            portal="FACULTY",
            entity_type="ExitPulseSession",
            entity_id=session.public_id,
            actor=user,
            tenant=session.tenant_id,
            campus=session.campus_id,
            metadata={
                "faculty_assignment_id": session.faculty_assignment_id,
                "course_offering_id": session.course_offering_id,
                "status": session.status,
                "extension_count": session.extension_count,
            },
            request=request,
        )


def models_q_course_department_active():
    from django.db.models import Q

    return Q(offering__course__department__isnull=True) | Q(offering__course__department__is_active=True)


class ExitPulseAnonymousIdentityService:
    COOKIE_NAME = "exit_pulse_client"
    COOKIE_SALT = "teachermateplus.exit-pulse.browser.v1"
    MAX_AGE_SECONDS = 24 * 60 * 60

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
        created = not bool(raw_value)
        if created:
            raw_value = secrets.token_urlsafe(32)
            signed_value = signing.dumps({"client": raw_value}, salt=cls.COOKIE_SALT, compress=True)
        return raw_value, signed_value, created

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


class ExitPulseRateLimitService:
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
    def check(cls, *, request, session, anonymous_hash):
        bucket = timezone.now().strftime("%Y%m%d%H%M")
        browser_limit = int(getattr(settings, "EXIT_PULSE_BROWSER_RATE_LIMIT_PER_MINUTE", 6) or 6)
        ip_limit = int(getattr(settings, "EXIT_PULSE_IP_RATE_LIMIT_PER_MINUTE", 120) or 120)
        browser_key = f"exit-pulse-rate:browser:{session.public_id}:{anonymous_hash[:16]}:{bucket}"
        if cls._increment(browser_key, timeout=65) > browser_limit:
            raise ExitPulseRateLimited("Too many submission attempts. Please wait a moment and try again.")
        ip_address = resolve_client_ip(request) or "unknown"
        ip_hash = hashlib.sha256(ip_address.encode("utf-8")).hexdigest()[:20]
        ip_key = f"exit-pulse-rate:ip:{session.public_id}:{ip_hash}:{bucket}"
        if cls._increment(ip_key, timeout=65) > ip_limit:
            raise ExitPulseRateLimited("The survey is receiving too many requests. Please try again shortly.")


class ExitPulseResponseService:
    TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,64}$")

    @classmethod
    def resolve_public_session(cls, public_token):
        if not cls.TOKEN_PATTERN.fullmatch(public_token or ""):
            return None
        session = (
            ExitPulseSession.objects.select_related(
                "tenant",
                "campus",
                "faculty_assignment",
                "course_offering",
                "course",
                "section",
            )
            .filter(public_token=public_token)
            .first()
        )
        if session:
            ExitPulseSessionService.refresh_effective_status(session)
        return session

    @classmethod
    def already_submitted(cls, *, session, anonymous_hash):
        return ExitPulseResponse.objects.filter(
            session=session,
            anonymous_token_hash=anonymous_hash,
        ).exists()

    @classmethod
    def submit(
        cls,
        *,
        session,
        response_code,
        anonymous_hash,
        feedback_review="",
        feedback_learned="",
        request=None,
        now=None,
    ):
        now = now or timezone.now()
        with transaction.atomic():
            locked = ExitPulseSession.objects.select_for_update().get(pk=session.pk)
            ExitPulseSessionService.refresh_effective_status(locked, now=now)
            if locked.status != ExitPulseSession.Status.LIVE:
                raise ValidationError("This Exit Pulse is no longer accepting responses.")
            if response_code not in ExitPulseResponse.ResponseCode.values:
                raise ValidationError("Select a valid learning-status response.")
            review = (feedback_review or "").strip()
            learned = (feedback_learned or "").strip()
            if len(review) > 200 or len(learned) > 200:
                raise ValidationError("Written responses must be 200 characters or fewer.")
            if not locked.allow_written_feedback:
                review = ""
                learned = ""
            else:
                if not locked.feedback_review_enabled:
                    review = ""
                if not locked.feedback_learned_enabled:
                    learned = ""
            if cls.already_submitted(session=locked, anonymous_hash=anonymous_hash):
                raise ExitPulseDuplicateResponse("This browser has already submitted a response.")
            try:
                with transaction.atomic():
                    return ExitPulseResponse.objects.create(
                        session=locked,
                        response_code=response_code,
                        feedback_review=review,
                        feedback_learned=learned,
                        anonymous_token_hash=anonymous_hash,
                        technical_identifier_expires_at=now + timedelta(hours=24),
                    )
            except IntegrityError as exc:
                raise ExitPulseDuplicateResponse("This browser has already submitted a response.") from exc

    @staticmethod
    def anonymize_expired_identifiers(*, now=None):
        now = now or timezone.now()
        return ExitPulseResponse.objects.filter(
            technical_identifier_expires_at__lte=now,
            anonymous_token_hash__isnull=False,
        ).update(anonymous_token_hash=None, technical_identifier_expires_at=None, updated_at=now)


@dataclass(frozen=True)
class ExitPulseAnalytics:
    total_responses: int
    enrolled_students: int
    response_rate: Decimal
    reaction_rows: tuple
    understanding_rate: Decimal
    support_needed_rate: Decimal
    duration_minutes: Decimal
    written_review: tuple
    written_learned: tuple


class ExitPulseAnalyticsService:
    @staticmethod
    def _percent(count, total):
        if not total:
            return Decimal("0.0")
        return (Decimal(count) * Decimal("100") / Decimal(total)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )

    @classmethod
    def build(cls, session):
        counts = OrderedDict((code, 0) for code in ExitPulseResponse.ResponseCode.values)
        for row in session.responses.values("response_code").annotate(total=Count("id")):
            if row["response_code"] in counts:
                counts[row["response_code"]] = row["total"]
        total = sum(counts.values())
        labels = dict(ExitPulseResponse.ResponseCode.choices)
        reaction_rows = tuple(
            {
                "code": code,
                "label": labels[code],
                "count": count,
                "percentage": cls._percent(count, total),
            }
            for code, count in counts.items()
        )
        enrolled = Enrollment.objects.filter(
            course_offering=session.course_offering,
            is_active=True,
            enrollment_status=Enrollment.Status.ACTIVE,
        ).count()
        understanding = counts[ExitPulseResponse.ResponseCode.CONFIDENT] + counts[
            ExitPulseResponse.ResponseCode.MOSTLY_UNDERSTOOD
        ]
        support = counts[ExitPulseResponse.ResponseCode.NEEDS_CLARIFICATION] + counts[
            ExitPulseResponse.ResponseCode.NEEDS_PRACTICE
        ]
        end_at = session.cancelled_at or session.closed_at or session.expires_at or timezone.now()
        duration_seconds = max(0, (end_at - session.started_at).total_seconds()) if session.started_at else 0
        written_review = ()
        written_learned = ()
        if session.status in {
            ExitPulseSession.Status.CLOSED,
            ExitPulseSession.Status.EXPIRED,
        } and session.allow_written_feedback:
            if session.feedback_review_enabled:
                written_review = tuple(
                    session.responses.exclude(feedback_review="")
                    .values_list("feedback_review", flat=True)
                )
            if session.feedback_learned_enabled:
                written_learned = tuple(
                    session.responses.exclude(feedback_learned="")
                    .values_list("feedback_learned", flat=True)
                )
        return ExitPulseAnalytics(
            total_responses=total,
            enrolled_students=enrolled,
            response_rate=cls._percent(total, enrolled),
            reaction_rows=reaction_rows,
            understanding_rate=cls._percent(understanding, total),
            support_needed_rate=cls._percent(support, total),
            duration_minutes=(Decimal(str(duration_seconds)) / Decimal("60")).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            ),
            written_review=written_review,
            written_learned=written_learned,
        )
