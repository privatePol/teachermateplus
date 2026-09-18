"""Tenant-scoped, consolidated departmental contribution deadline reminders."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
import ipaddress
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import F
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService
from apps.departmental_exams.contribution_authorization import ContributionAuthorizationService
from apps.departmental_exams.exam_units import resolve_examination_unit
from apps.departmental_exams.models import CourseExamConfiguration, FacultyContribution
from apps.notifications.models import ContributionDeadlineReminderDelivery
from apps.tenants.models import Tenant


class ContributionDeadlineReminderService:
    MANILA = ZoneInfo("Asia/Manila")
    POLICY_VERSION = "v1"
    SEND_HOUR = 9
    MAX_PRE_SEND_ATTEMPTS = 3
    PREPARING_TIMEOUT = timedelta(minutes=15)

    @classmethod
    def _reminder_date(cls, now):
        local_now = now.astimezone(cls.MANILA)
        if local_now.hour < cls.SEND_HOUR:
            return None
        return local_now.date() + timedelta(days=1)

    @staticmethod
    def _valid_email(value):
        email = (value or "").strip()
        if not email:
            return "", "MISSING_EMAIL"
        try:
            validate_email(email)
        except ValidationError:
            return "", "INVALID_EMAIL"
        return email, ""

    @classmethod
    def _workspace_url(cls):
        base = (getattr(settings, "FACULTY_PORTAL_BASE_URL", "") or getattr(settings, "SITE_URL", "")).strip().rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("A public HTTPS faculty portal URL is required.")
        hostname = parsed.hostname.lower()
        if hostname == "localhost" or hostname.endswith(".local"):
            raise ValueError("A public HTTPS faculty portal URL is required.")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and address.is_loopback:
            raise ValueError("A public HTTPS faculty portal URL is required.")
        return base + reverse("departmental_exams:contribution_list")

    @classmethod
    def _eligible_groups(cls, *, tenant, now, faculty_user_id=None):
        """Return current, editable Draft owners due tomorrow in this tenant."""
        target_date = cls._reminder_date(now)
        if target_date is None:
            return {}
        rows = FacultyContribution.objects.filter(
            cycle_course__cycle__tenant=tenant,
            cycle_course__inclusion_status="INCLUDED",
            cycle_course__configuration__workflow_status=CourseExamConfiguration.WorkflowStatus.OPEN,
            active_marker=1,
            status=FacultyContribution.Status.DRAFT,
            faculty_user__is_active=True,
        )
        if faculty_user_id is not None:
            rows = rows.filter(faculty_user_id=faculty_user_id)
        rows = (
            rows
            .select_related("faculty_user", "cycle_course__cycle__tenant", "cycle_course__configuration")
            .prefetch_related("eligibility_sources", "cycle_course__offering_snapshots__campus", "cycle_course__offering_snapshots__offering")
            .order_by("faculty_user_id", "id")
        )
        grouped = defaultdict(list)
        for contribution in rows:
            configuration = contribution.cycle_course.configuration
            deadline = configuration.active_contribution_deadline
            if not deadline or deadline <= now or deadline.astimezone(cls.MANILA).date() != target_date:
                continue
            try:
                resolve_examination_unit(contribution.cycle_course)
            except ValidationError:
                continue
            campus_ids = {
                source.campus_id_snapshot
                for source in contribution.eligibility_sources.all()
                if source.tenant_id_snapshot == tenant.id
            }
            for campus_id in sorted(campus_ids):
                try:
                    ContributionAuthorizationService.require_mutable_locked(
                        user=contribution.faculty_user,
                        contribution=contribution,
                        configuration=configuration,
                        request_tenant_id=tenant.id,
                        request_campus_id=campus_id,
                    )
                except PermissionDenied:
                    continue
                grouped[contribution.faculty_user_id].append(contribution)
                break
        return grouped

    @classmethod
    def _claim(cls, *, tenant, user, deadline_date, now):
        key = dict(
            tenant=tenant,
            faculty_user=user,
            deadline_local_date=deadline_date,
            policy_version=cls.POLICY_VERSION,
        )
        token = uuid4().hex
        with transaction.atomic():
            try:
                record, created = ContributionDeadlineReminderDelivery.objects.get_or_create(
                    **key,
                    defaults={
                        "status": ContributionDeadlineReminderDelivery.Status.PREPARING,
                        "attempt_count": 1,
                        "claimed_at": now,
                        "failure_code": token,
                    },
                )
            except IntegrityError:
                record = ContributionDeadlineReminderDelivery.objects.get(**key)
                created = False
            if created:
                return record.pk, token
            if record.status in (
                record.Status.SENT, record.Status.DISPATCHING, record.Status.UNCERTAIN,
            ):
                return None
            if record.status == record.Status.PREPARING and record.claimed_at and now - record.claimed_at < cls.PREPARING_TIMEOUT:
                return None
            if record.status == record.Status.FAILED_PRE_SEND and record.attempt_count >= cls.MAX_PRE_SEND_ATTEMPTS:
                return None
            changed = ContributionDeadlineReminderDelivery.objects.filter(
                pk=record.pk, status=record.status, attempt_count=record.attempt_count,
            ).update(
                status=record.Status.PREPARING,
                attempt_count=F("attempt_count") + 1,
                claimed_at=now,
                failure_code=token,
                updated_at=now,
            )
            return (record.pk, token) if changed else None

    @staticmethod
    def _finish(*, record_id, token, status, now, email="", reason=""):
        return ContributionDeadlineReminderDelivery.objects.filter(
            pk=record_id,
            status=ContributionDeadlineReminderDelivery.Status.PREPARING,
            failure_code=token,
        ).update(
            status=status,
            recipient_email=email,
            failure_code=reason,
            updated_at=now,
        )

    @classmethod
    def _dispatch(cls, *, tenant, user, deadline_date, record_id, token, now):
        # The second scan is intentional: selection may have preceded a submit,
        # exemption, permission change, or deadline extension.
        fresh_now = timezone.now() if now is None else now
        ready = (
            cls._reminder_date(fresh_now) == deadline_date
            and Tenant.objects.filter(pk=tenant.pk, is_active=True).exists()
            and FeatureSettingsService.is_contribution_deadline_reminder_enabled(tenant_id=tenant.id)
        )
        fresh_groups = (
            cls._eligible_groups(tenant=tenant, now=fresh_now, faculty_user_id=user.pk)
            if ready else {}
        )
        if user.pk not in fresh_groups:
            cls._finish(record_id=record_id, token=token, status=ContributionDeadlineReminderDelivery.Status.SKIPPED,
                        now=fresh_now, reason="NO_LONGER_ELIGIBLE")
            return "skipped"
        if not FeatureSettingsService.is_contribution_deadline_reminder_enabled(tenant_id=tenant.id):
            cls._finish(record_id=record_id, token=token, status=ContributionDeadlineReminderDelivery.Status.SKIPPED,
                        now=fresh_now, reason="FEATURE_DISABLED")
            return "skipped"
        email, email_error = cls._valid_email(fresh_groups[user.pk][0].faculty_user.email)
        if email_error:
            cls._finish(record_id=record_id, token=token, status=ContributionDeadlineReminderDelivery.Status.SKIPPED,
                        now=fresh_now, reason=email_error)
            return "skipped"
        try:
            url = cls._workspace_url()
            context = {"workspace_url": url}
            message = EmailMultiAlternatives(
                subject="TeacherMate+ contribution deadline reminder",
                body=render_to_string("notifications/emails/contribution_deadline_reminder.txt", context),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[email],
            )
            message.attach_alternative(
                render_to_string("notifications/emails/contribution_deadline_reminder.html", context),
                "text/html",
            )
        except Exception as exc:
            cls._finish(record_id=record_id, token=token,
                        status=ContributionDeadlineReminderDelivery.Status.FAILED_PRE_SEND,
                        now=fresh_now, reason=exc.__class__.__name__)
            return "failed_pre_send"
        # Claim DISPATCHING before network I/O. An interruption from here onward
        # is delivery-uncertain and never becomes an automatic retry.
        changed = ContributionDeadlineReminderDelivery.objects.filter(
            pk=record_id,
            status=ContributionDeadlineReminderDelivery.Status.PREPARING,
            failure_code=token,
        ).update(
            status=ContributionDeadlineReminderDelivery.Status.DISPATCHING,
            recipient_email=email,
            failure_code="",
            updated_at=fresh_now,
        )
        if not changed:
            return "duplicate"
        try:
            if message.send(fail_silently=False) != 1:
                raise RuntimeError("Delivery was not confirmed")
        except Exception:
            ContributionDeadlineReminderDelivery.objects.filter(
                pk=record_id,
                status=ContributionDeadlineReminderDelivery.Status.DISPATCHING,
            ).update(status=ContributionDeadlineReminderDelivery.Status.UNCERTAIN,
                     failure_code="SMTP_OUTCOME_UNKNOWN", updated_at=fresh_now)
            return "uncertain"
        ContributionDeadlineReminderDelivery.objects.filter(
            pk=record_id,
            status=ContributionDeadlineReminderDelivery.Status.DISPATCHING,
        ).update(status=ContributionDeadlineReminderDelivery.Status.SENT,
                 sent_at=fresh_now, failure_code="", updated_at=fresh_now)
        return "sent"

    @classmethod
    def run(cls, *, now=None, tenant_id=None, dry_run=False):
        supplied_now = now
        now = now or timezone.now()
        summary = {"eligible": 0, "sent": 0, "skipped": 0, "duplicates": 0,
                   "failed_pre_send": 0, "uncertain": 0, "dry_run": 0}
        if cls._reminder_date(now) is None:
            return summary
        deadline_date = cls._reminder_date(now)
        tenants = Tenant.objects.filter(is_active=True)
        if tenant_id is not None:
            tenants = tenants.filter(pk=tenant_id)
        for tenant in tenants:
            if not FeatureSettingsService.is_contribution_deadline_reminder_enabled(tenant_id=tenant.id):
                continue
            if not dry_run:
                # A stale network-stage claim is ambiguous, even if the process
                # died before it called SMTP. Keep it ineligible for auto-retry.
                ContributionDeadlineReminderDelivery.objects.filter(
                    tenant=tenant,
                    status=ContributionDeadlineReminderDelivery.Status.DISPATCHING,
                    claimed_at__lt=now - cls.PREPARING_TIMEOUT,
                ).update(
                    status=ContributionDeadlineReminderDelivery.Status.UNCERTAIN,
                    failure_code="INTERRUPTED_DISPATCH",
                    updated_at=now,
                )
            groups = cls._eligible_groups(tenant=tenant, now=now)
            summary["eligible"] += len(groups)
            for contributions in groups.values():
                user = contributions[0].faculty_user
                if dry_run:
                    summary["dry_run"] += 1
                    continue
                claim = cls._claim(tenant=tenant, user=user, deadline_date=deadline_date, now=now)
                if claim is None:
                    summary["duplicates"] += 1
                    continue
                record_id, token = claim
                outcome = cls._dispatch(tenant=tenant, user=user, deadline_date=deadline_date,
                                        record_id=record_id, token=token, now=supplied_now)
                summary[outcome] += 1
        return summary
