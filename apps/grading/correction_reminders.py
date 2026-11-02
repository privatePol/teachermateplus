"""Correction approval outbox. No grade values, bodies or SMTP exceptions are stored.

The dispatch authorization boundary is the final locked preflight, immediately before
SMTP, not a lock spanning network I/O. See docs/GRADE_CORRECTION_REMINDERS.md.
"""
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from django import forms
from django.core.mail import get_connection
from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.grading.models import (
    CorrectionEmailDelivery as Delivery,
    CorrectionStepNotificationIntent as Intent,
    GradeCorrectionRequest,
)
from apps.rbac.models import RolePermission, UserPermission, UserRole


MANILA = ZoneInfo("Asia/Manila")


class CorrectionReminderSettingsForm(forms.Form):
    enabled = forms.BooleanField(required=False, label="Enable automatic correction reminders")
    interval_days = forms.TypedChoiceField(
        coerce=int, choices=[(1, "Every calendar day"), (2, "Every 2 calendar days"),
                            (3, "Every 3 calendar days"), (7, "Every 7 calendar days")],
        label="Reminder frequency",
    )
    send_time = forms.TimeField(label="Reminder time (Asia/Manila)", input_formats=["%H:%M"],
                               widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}))


class CorrectionReminderPolicy:
    KEY = "GRADE_CORRECTION_REMINDER_POLICY"

    @classmethod
    def get(cls, tenant_id):
        value = SystemSettingService.get(cls.KEY, tenant_id=tenant_id, default={})
        value = value if isinstance(value, dict) else {}
        interval = value.get("interval_days", 1)
        if type(interval) is not int or interval not in (1, 2, 3, 7):
            interval = 1
        try:
            send_time = datetime.strptime(value.get("send_time", "08:00"), "%H:%M").time()
        except (TypeError, ValueError):
            send_time = time(8)
        return {"enabled": value.get("enabled", True) is True, "interval_days": interval,
                "send_time": send_time}

    @classmethod
    def save(cls, tenant_id, cleaned):
        SystemSettingService.set(cls.KEY, {"enabled": cleaned["enabled"],
            "interval_days": cleaned["interval_days"], "send_time": cleaned["send_time"].strftime("%H:%M"),
            "timezone": "Asia/Manila"}, tenant_id=tenant_id, value_type="JSON")


class CorrectionReminderService:
    LEASE = timedelta(minutes=15)
    RETRY = timedelta(minutes=5)

    @staticmethod
    def current_step(request_obj):
        from apps.grading.services import GradingGovernanceService

        if request_obj.status != GradeCorrectionRequest.Status.PENDING:
            return None
        valid, step, _ = GradingGovernanceService.get_correction_approval_snapshot_state(request_obj=request_obj)
        return step if valid else None

    @staticmethod
    def eligible(request_obj, step, user):
        """Intersect ordinary step assignment, explicit RBAC and the review-page selector."""
        from apps.admin_portal.services import AdminScopeService
        from apps.grading.services import GradingGovernanceService

        if not user.is_active or not (user.email or "").strip():
            return False
        # Overrides alone never select recipients; require a real assignment to this step.
        assignments = UserRole.objects.filter(user=user, role_id=step.approver_role_id,
            is_active=True, role__is_active=True).filter(
            Q(tenant_id=request_obj.tenant_id) | Q(tenant__isnull=True)).filter(
            Q(campus_id=request_obj.campus_id) | Q(campus__isnull=True))
        ordinary_assignment = False
        for assignment in assignments.select_related("department"):
            department_id = assignment.department_id
            if not department_id and step.requires_same_department:
                department_id = user.default_department_id
                if not department_id:
                    continue
            if department_id:
                if not request_obj.faculty_department_id:
                    continue
                # Bound the hierarchy to the petition tenant/campus.
                if not assignment.department_id and not user.default_department:
                    continue
                from apps.tenants.models import Department
                if not Department.objects.filter(pk=department_id, tenant_id=request_obj.tenant_id,
                        campus_id=request_obj.campus_id, is_active=True).exists():
                    continue
                if request_obj.faculty_department_id not in ScopeService.expand_department_ids(
                        [department_id], tenant_id=request_obj.tenant_id, campus_id=request_obj.campus_id):
                    continue
            ordinary_assignment = True
            break
        if not ordinary_assignment:
            return False
        # Include NULL scopes explicitly (SQL IN with None does not match NULL).
        permissions = UserPermission.objects.filter(user=user,
            permission__is_active=True).filter(
            Q(tenant_id=request_obj.tenant_id) | Q(tenant__isnull=True)).filter(
            Q(campus_id=request_obj.campus_id) | Q(campus__isnull=True))
        scoped_roles = UserRole.objects.filter(user=user, is_active=True, role__is_active=True).filter(
            Q(tenant_id=request_obj.tenant_id) | Q(tenant__isnull=True)).filter(
            Q(campus_id=request_obj.campus_id) | Q(campus__isnull=True))
        for code in ("corrections.review", "admin_portal.access"):
            scoped_permissions = permissions.filter(permission__code=code)
            if scoped_permissions.filter(grant_type=UserPermission.GrantType.DENY).exists():
                return False
            granted = scoped_permissions.filter(grant_type=UserPermission.GrantType.ALLOW).exists() or RolePermission.objects.filter(
                role_id__in=scoped_roles.values("role_id"), permission__code=code,
                permission__is_active=True).exists()
            if not granted:
                return False
        allowed, actionable_step, _ = GradingGovernanceService.can_user_review_correction_request(
            request_obj=request_obj, user=user)
        if not allowed or not actionable_step or actionable_step.pk != step.pk:
            return False
        scope = ScopeService.build_scope(user, tenant_id=request_obj.tenant_id, campus_id=request_obj.campus_id)
        if scope["tenant_id"] != request_obj.tenant_id or scope["campus_id"] != request_obj.campus_id:
            return False
        review_request = SimpleNamespace(user=user, scope=scope)
        return AdminScopeService.scoped_grade_correction_requests(review_request).filter(
            pk=request_obj.pk, tenant_id=request_obj.tenant_id, campus_id=request_obj.campus_id).exists()

    @classmethod
    def recipients(cls, request_obj, step):
        from django.contrib.auth import get_user_model

        assignments = UserRole.objects.filter(role_id=step.approver_role_id,
            is_active=True, role__is_active=True).filter(
            Q(tenant_id=request_obj.tenant_id) | Q(tenant__isnull=True)).filter(
            Q(campus_id=request_obj.campus_id) | Q(campus__isnull=True))
        candidates = get_user_model().objects.filter(is_active=True, pk__in=assignments.values("user_id")).order_by("pk")
        return [user for user in candidates if cls.eligible(request_obj, step, user)]

    @staticmethod
    def _activation_time(request_obj, step):
        prior = request_obj.approval_steps.filter(step_order__lt=step.step_order).order_by("-step_order").first()
        return (prior.reviewed_at if prior else None) or request_obj.created_at

    @classmethod
    def due_at(cls, intent, policy, now):
        local = now.astimezone(MANILA)
        age = (local.date() - intent.activated_at.astimezone(MANILA).date()).days
        if age < 1 or age % policy["interval_days"]:
            return None
        due = datetime.combine(local.date(), policy["send_time"], tzinfo=MANILA)
        return due if local >= due else None

    @classmethod
    def _enqueue(cls, request_obj, step, user, kind, scheduled_at):
        row, _ = Delivery.objects.get_or_create(step=step, recipient=user, kind=kind,
            scheduled_local_date=scheduled_at.astimezone(MANILA).date(), defaults={
                "correction_request": request_obj, "tenant_id": request_obj.tenant_id,
                "campus_id": request_obj.campus_id, "scheduled_at": scheduled_at})
        # Settings/assignments may be repaired during the same scheduled date.
        if row.status == Delivery.Status.SKIPPED and row.outcome_code in {
                "recipient_ineligible", "step_not_current", "initial_disabled", "reminders_disabled", "schedule_changed_or_expired",
                "claim_lost"}:  # Recover unsent rows stranded by the former expired-claim finalization.
            Delivery.objects.filter(pk=row.pk, status=Delivery.Status.SKIPPED).update(
                status=Delivery.Status.PENDING, scheduled_at=scheduled_at, outcome_code="", next_attempt_at=None)
        return row

    @classmethod
    @transaction.atomic
    def activate(cls, request_obj, *, dispatch=True):
        """Called inside governance transactions; even zero-recipient intent survives commit."""
        request_obj = GradeCorrectionRequest.objects.select_for_update().get(pk=request_obj.pk)
        step = cls.current_step(request_obj)
        if not step:
            return None
        intent, _ = Intent.objects.get_or_create(step=step, defaults={
            "activated_at": cls._activation_time(request_obj, step)})
        if FeatureSettingsService.is_correction_submission_approval_email_enabled(tenant_id=request_obj.tenant_id):
            for user in cls.recipients(request_obj, step):
                row = cls._enqueue(request_obj, step, user, Delivery.Kind.INITIAL, intent.activated_at)
                # Preserve individual successful initial notifications from the legacy audit trail.
                if row.status == Delivery.Status.PENDING and not row.attempt_count:
                    from apps.auditlog.models import AuditLog
                    legacy = AuditLog.objects.filter(action="EMAIL_CORRECTION_STEP_APPROVER",
                        entity_type="GradeCorrectionRequest", entity_id=str(request_obj.pk),
                        tenant_id=request_obj.tenant_id, metadata_json__event_key=f"step:{step.pk}:pending").first()
                    if legacy and user.email.strip().lower() in {
                            str(email).lower() for email in (legacy.metadata_json or {}).get("recipients", [])}:
                        row.status = Delivery.Status.SENT
                        row.sent_at = legacy.created_at
                        row.recipient_email = user.email.strip()
                        row.outcome_code = "legacy_backend_accepted"
                        row.save(update_fields=["status", "sent_at", "recipient_email", "outcome_code", "updated_at"])
        if dispatch:
            transaction.on_commit(lambda: cls.dispatch_safely(request_obj.pk), robust=True)
        return intent

    @classmethod
    def dispatch_safely(cls, request_id):
        # No raw callback exception (which can contain SQL/email details) reaches operational logs.
        try:
            cls.dispatch(request_id=request_id)
        except Exception:
            return {"deferred": True}

    @classmethod
    def discover(cls, *, now=None):
        now = now or timezone.now()
        discovered = 0
        for request_obj in GradeCorrectionRequest.objects.filter(status=GradeCorrectionRequest.Status.PENDING).iterator():
            with transaction.atomic():
                intent = cls.activate(request_obj, dispatch=False)
                if not intent:
                    continue
                policy = CorrectionReminderPolicy.get(request_obj.tenant_id)
                due = cls.due_at(intent, policy, now) if policy["enabled"] else None
                if due:
                    for user in cls.recipients(request_obj, intent.step):
                        cls._enqueue(request_obj, intent.step, user, Delivery.Kind.REMINDER, due)
            discovered += 1
        return discovered

    @classmethod
    def claim(cls, pk, *, now):
        token = uuid4()
        available = Q(status__in=[Delivery.Status.PENDING, Delivery.Status.FAILED]) & (
            Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        available |= Q(status=Delivery.Status.PROCESSING, claimed_until__lte=now)
        updated = Delivery.objects.filter(pk=pk, scheduled_at__lte=now).filter(available).update(
            status=Delivery.Status.PROCESSING, claim_token=token, claimed_until=now + cls.LEASE,
            first_attempt_at=Coalesce("first_attempt_at", Value(now)),
            last_attempt_at=now, attempt_count=F("attempt_count") + 1, outcome_code="",
            updated_at=now)
        if updated:
            return token
        return None

    @classmethod
    def preflight(cls, pk, token, *, now):
        with transaction.atomic():
            row = Delivery.objects.get(pk=pk)
            request_obj = GradeCorrectionRequest.objects.select_for_update().get(pk=row.correction_request_id)
            row = Delivery.objects.select_for_update().select_related("recipient").get(pk=pk)
            if row.claim_token != token or row.status != Delivery.Status.PROCESSING or row.claimed_until <= now:
                return None, "claim_lost"
            step = cls.current_step(request_obj)
            if not step or step.pk != row.step_id:
                return None, "step_not_current"
            if row.tenant_id != request_obj.tenant_id or row.campus_id != request_obj.campus_id:
                return None, "scope_mismatch"
            if not cls.eligible(request_obj, step, row.recipient):
                return None, "recipient_ineligible"
            if row.kind == Delivery.Kind.INITIAL:
                if not FeatureSettingsService.is_correction_submission_approval_email_enabled(tenant_id=row.tenant_id):
                    return None, "initial_disabled"
            else:
                policy = CorrectionReminderPolicy.get(row.tenant_id)
                if not policy["enabled"]:
                    return None, "reminders_disabled"
                due = cls.due_at(Intent.objects.get(step=step), policy, now)
                if not due or row.scheduled_local_date != due.date():
                    return None, "schedule_changed_or_expired"
                row.scheduled_at = due
                today_start = datetime.combine(due.date(), time.min, tzinfo=MANILA)
                if Delivery.objects.filter(step=step, recipient=row.recipient, kind=Delivery.Kind.INITIAL,
                        status=Delivery.Status.SENT, sent_at__gte=today_start,
                        sent_at__lt=today_start + timedelta(days=1)).exists():
                    return None, "initial_sent_today"
            row.recipient_email = row.recipient.email.strip()
            row.save(update_fields=["recipient_email", "scheduled_at", "updated_at"])
            return (row, request_obj, step), ""

    @classmethod
    def dispatch(cls, *, request_id=None, now=None, limit=500):
        from apps.grading.notifications import CorrectionNotificationService

        fixed_now = now
        now = now or timezone.now()
        candidates = Delivery.objects.filter(scheduled_at__lte=now).filter(
            Q(status__in=[Delivery.Status.PENDING, Delivery.Status.FAILED]) |
            Q(status=Delivery.Status.PROCESSING, claimed_until__lte=now))
        if request_id:
            candidates = candidates.filter(correction_request_id=request_id)
        counts = {"sent": 0, "failed": 0, "skipped": 0}
        # Retry eligibility is filtered before the bound to avoid starving later rows.
        candidates = candidates.filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
        for pk in list(candidates.values_list("pk", flat=True)[:limit]):
            token = cls.claim(pk, now=fixed_now or timezone.now())
            if not token:
                continue
            try:
                ready, reason = cls.preflight(pk, token, now=fixed_now or timezone.now())
                if not ready:
                    if reason == "claim_lost":
                        # Leave expired claims reclaimable and replacement owners untouched.
                        continue
                    status = Delivery.Status.SKIPPED
                else:
                    row, request_obj, step = ready
                    message = CorrectionNotificationService._build_correction_approval_message(
                        request_obj=request_obj, recipient_email=row.recipient_email,
                        approver_label=step.approver_label or step.approver_role.name)
                    # Bound network time well below the recovery lease.
                    message.connection = get_connection(fail_silently=False, timeout=60)
                    from django.core.mail.backends.console import EmailBackend as ConsoleBackend
                    if isinstance(message.connection, ConsoleBackend):
                        # Cron redirects stdout to operational logs; never print email bodies there.
                        status, reason = Delivery.Status.FAILED, "unsafe_console_backend"
                    else:
                        accepted = message.send(fail_silently=False)
                        status = Delivery.Status.SENT if accepted else Delivery.Status.FAILED
                        reason = "backend_accepted" if accepted else "backend_not_accepted"
            except Exception:
                status, reason = Delivery.Status.FAILED, "email_or_preflight_failed"
            finished = timezone.now()
            updated = Delivery.objects.filter(pk=pk, claim_token=token, status=Delivery.Status.PROCESSING).update(
                status=status, outcome_code=reason, sent_at=finished if status == Delivery.Status.SENT else None,
                claimed_until=None, claim_token=None, updated_at=finished,
                next_attempt_at=finished + cls.RETRY if status == Delivery.Status.FAILED else None)
            if updated:
                counts[{Delivery.Status.SENT: "sent", Delivery.Status.FAILED: "failed"}.get(status, "skipped")] += 1
        return counts
