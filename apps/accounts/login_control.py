"""Login access only: never change activation, roles, assignments or grading data."""
from datetime import timedelta, timezone as datetime_timezone

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, F
from django.utils import timezone

from apps.accounts.models import ClearanceLoginDeadline, HistoricalClearanceCycle, User
from apps.accounts.login_obligations import lock_login_users, retry_login_write as retry_sqlite_write
from apps.academics.models import FacultyAssignment
from apps.core.services.audit import AuditService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, UserPermission, UserRole
from apps.tenants.models import SystemSetting, Tenant


class StaleLoginControl(ValidationError):
    pass


class LoginAccessService:
    PERMISSION = "users.login_access.update"
    FEATURE_KEY = "FEATURE_CLEARANCE_LOGIN_DISABLE_ENABLED"
    DELAY_KEY = "CLEARANCE_LOGIN_DISABLE_DELAY_DAYS"
    DEFAULT_DELAY_DAYS = 2

    @classmethod
    def automatic_enabled(cls, tenant_id):
        # This policy is tenant-only. A global setting cannot enable tenants
        # whose administrators have never opted in.
        if not tenant_id:
            return False
        setting = SystemSetting.objects.filter(
            setting_key=cls.FEATURE_KEY, tenant_id=tenant_id, is_active=True, value_type="BOOL",
        ).first()
        return bool(setting and SystemSettingService._cast_value(setting.setting_value, "BOOL") is True)

    @staticmethod
    def validate_delay_days(days):
        if type(days) is not int or days < 1:
            raise ValidationError("Delay must be a whole number of at least 1 day.")
        try:
            timezone.now() + timedelta(days=days)
        except OverflowError:
            raise ValidationError("Delay is too large to represent a deadline.")
        return days

    @classmethod
    def automatic_delay_days(cls, tenant_id):
        setting = SystemSetting.objects.filter(
            setting_key=cls.DELAY_KEY, tenant_id=tenant_id, tenant__isnull=False,
            is_active=True, value_type="INT",
        ).first()
        try:
            return cls.validate_delay_days(int(setting.setting_value)) if setting else cls.DEFAULT_DELAY_DAYS
        except (ValueError, ValidationError):
            return cls.DEFAULT_DELAY_DAYS

    @classmethod
    @retry_sqlite_write
    def save_automatic_policy(cls, *, tenant_id, enabled, delay_days, actor, campus_id=None, request=None):
        cls.validate_delay_days(delay_days)
        if type(enabled) is not bool or not tenant_id:
            raise ValidationError("A tenant and boolean policy setting are required.")
        with transaction.atomic():
            lock_login_users([actor.pk])
            Tenant.objects.select_for_update().get(pk=tenant_id)
            before = {"enabled": cls.automatic_enabled(tenant_id), "delay_days": cls.automatic_delay_days(tenant_id)}
            SystemSettingService.set(cls.FEATURE_KEY, enabled, tenant_id=tenant_id, value_type="BOOL", is_active=True)
            SystemSettingService.set(cls.DELAY_KEY, delay_days, tenant_id=tenant_id, value_type="INT", is_active=True)
            canceled = 0
            if not enabled:
                now = timezone.now()
                events = ClearanceLoginDeadline.objects.select_for_update().filter(tenant_id=tenant_id, state="PENDING")
                for event in events:
                    event.state, event.reason = "CANCELED", "Automatic login disabling turned OFF."
                    event.resolved_at, event.resolved_by = now, actor
                    event.save(update_fields=["state", "reason", "resolved_at", "resolved_by"])
                    canceled += 1
                    AuditService.log_event(
                        action="CLEARANCE_LOGIN_CANCELED", portal="ADMIN", actor=actor,
                        entity_type="ClearanceLoginDeadline", entity_id=event.pk,
                        tenant=tenant_id, campus=event.campus_id,
                        before_data={"state": "PENDING"}, after_data={"state": "CANCELED", "reason": event.reason},
                        request=request,
                    )
            AuditService.log_event(
                action="CLEARANCE_LOGIN_POLICY", portal="ADMIN", entity_type="SystemSetting",
                actor=actor, tenant=tenant_id, campus=campus_id, before_data=before,
                after_data={"enabled": enabled, "delay_days": delay_days, "canceled_deadlines": canceled}, request=request,
            )

    @staticmethod
    def _scope_filter(tenant_id, campus_id):
        return (Q(tenant_id=tenant_id) | Q(tenant__isnull=True)) & (Q(campus_id=campus_id) | Q(campus__isnull=True))

    @classmethod
    def has_management_permission(cls, actor, tenant_id, campus_id):
        if not actor or not actor.is_authenticated or not actor.is_active or actor.login_disabled:
            return False
        if not Permission.objects.filter(code=cls.PERMISSION, is_active=True).exists():
            return False
        direct = UserPermission.objects.filter(user=actor, permission__code__in=[cls.PERMISSION, "admin_portal.access"], permission__is_active=True).filter(cls._scope_filter(tenant_id, campus_id))
        # A direct DENY wins even over superuser privileges for this action.
        if direct.filter(grant_type=UserPermission.GrantType.DENY).exists():
            return False
        return actor.is_superuser or direct.filter(permission__code=cls.PERMISSION, grant_type=UserPermission.GrantType.ALLOW).exists() or UserRole.objects.filter(
            user=actor, is_active=True, role__is_active=True, department__isnull=True,
            role__role_permissions__permission__code=cls.PERMISSION,
            role__role_permissions__permission__is_active=True,
        ).filter(cls._scope_filter(tenant_id, campus_id)).exists()

    @classmethod
    def target_scopes(cls, user):
        scopes = set()
        if user.default_tenant_id or user.default_campus_id:
            scopes.add((user.default_tenant_id, user.default_campus_id))
        scopes.update(UserRole.objects.filter(user=user, is_active=True, role__is_active=True).values_list("tenant_id", "campus_id"))
        scopes.update(UserPermission.objects.filter(user=user, permission__is_active=True, grant_type="ALLOW").values_list("tenant_id", "campus_id"))
        scopes.update(FacultyAssignment.objects.filter(faculty_user=user, is_active=True).values_list("offering__tenant_id", "offering__campus_id"))
        return scopes

    @classmethod
    def can_manage(cls, *, actor, target, tenant_id, campus_id):
        scopes = cls.target_scopes(target)
        administrative = target.is_staff or UserRole.objects.filter(
            user=target, is_active=True, role__is_active=True,
            role__role_permissions__permission__code="admin_portal.access",
            role__role_permissions__permission__is_active=True,
        ).exists() or UserPermission.objects.filter(user=target, permission__code="admin_portal.access", permission__is_active=True, grant_type="ALLOW").exists()
        any_deny = UserPermission.objects.filter(user=actor, permission__code__in=[cls.PERMISSION, "admin_portal.access"], grant_type="DENY", permission__is_active=True).exists()
        return cls._target_allowed(actor, target, scopes, administrative, tenant_id, campus_id, lambda t, c: cls.has_management_permission(actor, t, c), any_deny)

    @staticmethod
    def _target_allowed(actor, target, scopes, administrative, tenant_id, campus_id, permitted, any_deny):
        if actor.pk == target.pk or target.is_superuser:
            return False
        if actor.is_superuser:
            if any(not t or not c for t, c in scopes) and any_deny:
                return False
            # Global/unbounded targets require a global permission check too.
            return all(permitted(t, c) for t, c in (scopes | {(tenant_id, campus_id)}))
        if not tenant_id or not campus_id or scopes != {(tenant_id, campus_id)}:
            return False
        # A scoped operator cannot lock out an administrative account. A
        # different superuser remains able to administer ordinary admin users.
        return not administrative and permitted(tenant_id, campus_id)

    @classmethod
    def decorate_users(cls, users, *, actor, tenant_id, campus_id):
        """Batch UI facts only; mutations independently reload and authorize."""
        scopes = {u.pk: {(u.default_tenant_id, u.default_campus_id)} if u.default_tenant_id or u.default_campus_id else set() for u in users}
        roles = UserRole.objects.filter(user_id__in=scopes, is_active=True, role__is_active=True)
        direct = UserPermission.objects.filter(user_id__in=scopes, permission__is_active=True, grant_type="ALLOW")
        assignments = FacultyAssignment.objects.filter(faculty_user_id__in=scopes, is_active=True)
        for user_id, tenant, campus in roles.values_list("user_id", "tenant_id", "campus_id"):
            scopes[user_id].add((tenant, campus))
        for user_id, tenant, campus in direct.values_list("user_id", "tenant_id", "campus_id"):
            scopes[user_id].add((tenant, campus))
        for user_id, tenant, campus in assignments.values_list("faculty_user_id", "offering__tenant_id", "offering__campus_id"):
            scopes[user_id].add((tenant, campus))
        admin_ids = set(roles.filter(role__role_permissions__permission__code="admin_portal.access", role__role_permissions__permission__is_active=True).values_list("user_id", flat=True))
        admin_ids.update(direct.filter(permission__code="admin_portal.access").values_list("user_id", flat=True))
        any_deny = UserPermission.objects.filter(user=actor, permission__code__in=[cls.PERMISSION, "admin_portal.access"], permission__is_active=True, grant_type="DENY").exists()
        cache = {}
        def permitted(t, c):
            if (t, c) not in cache:
                cache[t, c] = cls.has_management_permission(actor, t, c)
            return cache[t, c]
        for user in users:
            user.can_control_login = cls._target_allowed(actor, user, scopes[user.pk], user.is_staff or user.pk in admin_ids, tenant_id, campus_id, permitted, any_deny)

    @classmethod
    @retry_sqlite_write
    def set_manual(cls, *, actor, target_id, disabled, expected_version, tenant_id, campus_id, request=None):
        from apps.accounts.services import ActivePortalSessionService
        with transaction.atomic():
            target = lock_login_users([target_id, actor.pk])[target_id]
            # Refresh the actor as well: a cached request must not grant a
            # recently disabled administrator authority to mutate login state.
            actor = User.objects.get(pk=actor.pk)
            if not cls.can_manage(actor=actor, target=target, tenant_id=tenant_id, campus_id=campus_id):
                raise PermissionDenied("You cannot change login access for this account and scope.")
            if target.login_access_version != expected_version or target.login_disabled == disabled:
                raise StaleLoginControl("Login access changed. Reload Users and try again.")
            # Match policy OFF/worker order before touching deadline rows.
            # Audit inserts also reference tenants, so delaying these locks
            # until after overriding events can deadlock a different admin's OFF.
            tenant_ids = set(ClearanceLoginDeadline.objects.filter(user=target).values_list("tenant_id", flat=True))
            tenant_ids.add(tenant_id)
            list(Tenant.objects.select_for_update().filter(pk__in=tenant_ids - {None}).order_by("pk").values_list("pk", flat=True))
            before = {"login_disabled": target.login_disabled, "version": target.login_access_version}
            now = timezone.now()
            changed = User.objects.filter(pk=target.pk, login_access_version=expected_version).update(
                login_disabled=disabled, login_access_version=F("login_access_version") + 1, updated_at=now,
            )
            if changed != 1:
                raise StaleLoginControl("Login access changed. Reload Users and try again.")
            # Both pending and already-applied events become permanently
            # overridden on enabling. Unique cycle rows prevent reprint rearm.
            if not disabled:
                ClearanceLoginDeadline.objects.filter(user=target, state__in=["PENDING", "APPLIED"]).update(
                    state="OVERRIDDEN", resolved_at=now, resolved_by=actor, reason="Manual Enable Login",
                )
            revoked = ActivePortalSessionService.revoke_all(user=target)
            AuditService.log_event(
                action="DISABLE_LOGIN" if disabled else "ENABLE_LOGIN", portal="ADMIN",
                entity_type="User", entity_id=target.pk, actor=actor, tenant=tenant_id, campus=campus_id,
                before_data=before, after_data={"login_disabled": disabled, "version": expected_version + 1},
                metadata={"target_username": target.username, "changed_at": now, "revoked_sessions": revoked}, request=request,
            )
            return target.pk

    @staticmethod
    def exception_reason(user, campus_id, term_id):
        # Conservative: active/open loading remains continuing until operations
        # closes/unassigns it, even when calendar dates have elapsed.
        if user.is_superuser or user.is_staff:
            return "Administrative account; MIS review required."
        if UserRole.objects.filter(user=user, is_active=True, role__is_active=True).exclude(role__code="FACULTY").exists():
            return "Another active role; MIS review required."
        if UserPermission.objects.filter(user=user, grant_type="ALLOW", permission__is_active=True, permission__code="admin_portal.access").exists():
            return "Administrative access grant; MIS review required."
        continuing = FacultyAssignment.objects.filter(
            faculty_user=user, is_active=True, offering__is_active=True, offering__status="OPEN",
        )
        if continuing.exclude(offering__campus_id=campus_id).exists():
            return "Continuing active assignment in another campus; MIS review required."
        # The cleared campus/term is the only cycle covered by this PDF.
        # Any other still-active/open term is continuing work, including a
        # next-term assignment at this same campus. No calendar guess retires it.
        if continuing.exclude(offering__term_id=term_id).exists():
            return "Continuing active assignment in another term; MIS review required."
        return ""

    @classmethod
    @retry_sqlite_write
    def generate_clearance_pdf(cls, *, report, access_version, filename, request):
        """Serialize successful PDF construction with policy and cycle changes."""
        from apps.grading.reporting import FacultyFinalClearanceReportService
        with transaction.atomic():
            lock_login_users([report.faculty_user_id])
            Tenant.objects.select_for_update().only("pk").get(pk=report.tenant_id)
            pdf_bytes = FacultyFinalClearanceReportService.build_pdf_bytes(report_obj=report)
            generated_at = timezone.now()
            AuditService.log_event(
                action="DOWNLOAD", portal="FACULTY", entity_type="FacultyFinalClearanceReport",
                entity_id=report.pk, actor=request.user, tenant=report.tenant_id, campus=report.campus_id,
                metadata={"reference_no": report.reference_no, "filename": filename, "content_type": "application/pdf"},
                request=request,
            )
            cls.record_generation(report=report, generated_at=generated_at, access_version=access_version)
            return pdf_bytes

    @classmethod
    @retry_sqlite_write
    def record_generation(cls, *, report, generated_at=None, access_version=None):
        """Call only after successful official PDF construction, never on GET."""
        if report.clearance_status != "CLEARED":
            return None
        at = generated_at or timezone.now()
        if timezone.is_naive(at):
            raise ValidationError("A timezone-aware generation timestamp is required.")
        with transaction.atomic():
            user = lock_login_users([report.faculty_user_id])[report.faculty_user_id]
            Tenant.objects.select_for_update().only("pk").get(pk=report.tenant_id)
            enabled = cls.automatic_enabled(report.tenant_id)
            delay_days = cls.automatic_delay_days(report.tenant_id)
            # These belong to the same account lock as manual enable and worker
            # processing; repeated generation never updates an existing row.
            event, created = ClearanceLoginDeadline.objects.get_or_create(
                user=user, tenant_id=report.tenant_id, campus_id=report.campus_id, term_id=report.term_id,
                defaults={"source_report": report, "first_generated_at": at,
                    "disable_at": at.astimezone(datetime_timezone.utc) + timedelta(days=delay_days),
                    "delay_days": delay_days, "access_version": user.login_access_version},
            )
            if not created:
                return event
            reason = cls.exception_reason(user, event.campus_id, event.term_id)
            if not enabled:
                # Keep a terminal cycle marker even while OFF: a later reprint
                # must not turn this first successful PDF into a new deadline.
                event.state, event.reason, event.resolved_at = "CANCELED", "Automatic login disabling was OFF at first generation.", at
                event.save(update_fields=["state", "reason", "resolved_at"])
            elif HistoricalClearanceCycle.objects.filter(
                user=user, tenant_id=report.tenant_id, campus_id=report.campus_id, term_id=report.term_id,
            ).exists():
                # A legacy report is not proof of successful PDF rendering. Its
                # outcome is unknown, so never arm this historical cycle.
                event.state, event.reason, event.resolved_at = "EXEMPT", "Historical clearance cycle; PDF success unknown, MIS review required.", at
                event.save(update_fields=["state", "reason", "resolved_at"])
            elif access_version is not None and access_version != user.login_access_version:
                event.state, event.reason, event.resolved_at = "OVERRIDDEN", "Account access changed during generation.", at
                event.save(update_fields=["state", "reason", "resolved_at"])
            elif reason:
                event.state, event.reason, event.resolved_at = "EXEMPT", reason, at
                event.save(update_fields=["state", "reason", "resolved_at"])
            AuditService.log_event(
                action="CLEARANCE_LOGIN_CANCELED" if event.state == "CANCELED" else "CLEARANCE_LOGIN_OVERRIDDEN" if event.state == "OVERRIDDEN" else "CLEARANCE_LOGIN_EXCEPTION" if event.state == "EXEMPT" else "CLEARANCE_LOGIN_SCHEDULED", portal="SYSTEM",
                entity_type="ClearanceLoginDeadline", entity_id=event.pk, tenant=event.tenant_id, campus=event.campus_id,
                after_data={"user_id": user.pk, "first_generated_at": at, "disable_at": event.disable_at, "delay_days": delay_days, "state": event.state, "reason": event.reason},
                metadata={"source_report_id": report.pk, "term_id": event.term_id, "target_username": user.username},
            )
            return event

    @classmethod
    @retry_sqlite_write
    def process_user(cls, user_id, *, at=None, dry_run=False):
        from apps.accounts.services import ActivePortalSessionService
        now = at or timezone.now()
        if timezone.is_naive(now):
            raise ValidationError("A timezone-aware processing timestamp is required.")
        with transaction.atomic():
            user = lock_login_users([user_id], write=not dry_run)[user_id]
            # Same lock as the policy form; OFF and automatic transitions have a
            # defined commit order. Acquire multiple tenant locks in ID order.
            tenant_ids = list(ClearanceLoginDeadline.objects.filter(user=user, state="PENDING", disable_at__lte=now).values_list("tenant_id", flat=True))
            list(Tenant.objects.select_for_update().filter(pk__in=tenant_ids).order_by("pk").values_list("pk", flat=True))
            # Policy OFF locks tenant then events; acquire event locks only
            # after tenant locks to avoid a worker/policy deadlock.
            events = list(ClearanceLoginDeadline.objects.select_for_update().filter(user=user, state="PENDING", disable_at__lte=now).order_by("disable_at", "id"))
            results = []
            for event in events:
                if not cls.automatic_enabled(event.tenant_id):
                    continue
                reason = cls.exception_reason(user, event.campus_id, event.term_id)
                state = "EXEMPT" if reason else "APPLIED"
                if event.access_version != user.login_access_version or user.login_disabled or not user.is_active:
                    state, reason = "OVERRIDDEN", "Account access changed since generation."
                results.append({"event_id": event.pk, "user_id": user.pk, "state": state})
                if dry_run:
                    continue
                before_disabled = user.login_disabled
                if state == "APPLIED":
                    changed = User.objects.filter(pk=user.pk, login_access_version=user.login_access_version, login_disabled=False).update(
                        login_disabled=True, login_access_version=F("login_access_version") + 1, updated_at=now,
                    )
                    if changed != 1:
                        raise StaleLoginControl("Concurrent account access change; retry processing.")
                    user.login_disabled = True
                    user.login_access_version += 1
                    ActivePortalSessionService.revoke_all(user=user)
                event.state, event.reason, event.resolved_at = state, reason, now
                event.save(update_fields=["state", "reason", "resolved_at"])
                AuditService.log_event(
                    action="AUTO_DISABLE_LOGIN" if state == "APPLIED" else "CLEARANCE_LOGIN_EXCEPTION" if state == "EXEMPT" else "CLEARANCE_LOGIN_OVERRIDDEN",
                    portal="SYSTEM", entity_type="ClearanceLoginDeadline", entity_id=event.pk,
                    tenant=event.tenant_id, campus=event.campus_id,
                    before_data={"state": "PENDING", "login_disabled": before_disabled},
                    after_data={"state": state, "reason": reason, "user_id": user.pk, "login_disabled": user.login_disabled, "resolved_at": now},
                    metadata={"source_report_id": event.source_report_id, "term_id": event.term_id, "target_username": user.username},
                )
            return results

    @classmethod
    def process_due(cls, *, at=None, limit=100, dry_run=False):
        now = at or timezone.now()
        user_ids = list(ClearanceLoginDeadline.objects.filter(state="PENDING", disable_at__lte=now).order_by("user_id").values_list("user_id", flat=True).distinct())
        # Filter paused tenants before limiting, so a paused backlog cannot
        # starve enabled tenants. Candidate IDs are always rechecked under lock.
        rows = []
        processed_users = 0
        for user_id in user_ids:
            if processed_users >= limit:
                break
            result = cls.process_user(user_id, at=now, dry_run=dry_run)
            if result:
                processed_users += 1
                rows.extend(result)
        return rows

    @classmethod
    def login_allowed(cls, user):
        if not user.is_active or user.login_disabled:
            return False
        # Enforce the exact cutoff even if cron is late. No work on healthy
        # sessions except an indexed due-event existence check.
        if ClearanceLoginDeadline.objects.filter(user_id=user.pk, state="PENDING", disable_at__lte=timezone.now()).exists():
            cls.process_user(user.pk)
            user.refresh_from_db(fields=["is_active", "login_disabled", "login_access_version"])
        return user.is_active and not user.login_disabled
