from __future__ import annotations

import hashlib
import ipaddress
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import FacultyAssignment
from apps.admin_portal.submission_readiness import GradeSubmissionReadinessService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.scope import ScopeService
from apps.notifications.models import SubmissionReadinessNotificationLog
from apps.rbac.models import UserRole
from apps.tenants.models import Department, Tenant


class SubmissionReadinessEmailService:
    MANILA = ZoneInfo("Asia/Manila")
    POLICY_VERSION = "v1"
    ROLE_ALIASES = {
        "AREA_CHAIR": {"AC", "AREA_CHAIR", "AREA_CHAIRPERSON", "AREA_CHAIRMAN"},
        "COLLEGE_DEAN": {"COLLEGE_DEAN", "DEAN"},
        "CAO": {"CAO", "CHIEF_ACADEMIC_OFFICER"},
    }

    @classmethod
    def _active_assignments(cls, tenant_id):
        return list(
            FacultyAssignment.objects.filter(
                offering__tenant_id=tenant_id,
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                faculty_user__is_active=True,
                offering__is_active=True,
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
            )
            .select_related(
                "faculty_user", "offering", "offering__tenant", "offering__campus", "offering__department",
                "offering__academic_year", "offering__term", "offering__course", "offering__section",
                "offering__section__program",
            )
            .order_by("id")
        )

    @classmethod
    def _recipient_rows(cls, *, tenant_id, configured_roles):
        allowed_codes = set()
        for configured in configured_roles:
            allowed_codes.update(cls.ROLE_ALIASES.get(configured, {configured}))
        role_filter = Q(role__code__in=allowed_codes)
        if "AREA_CHAIR" in configured_roles:
            role_filter |= Q(role__code__endswith="_AC")
        return list(
            UserRole.objects.filter(
                Q(tenant_id=tenant_id) | Q(tenant__isnull=True),
                role_filter,
                is_active=True, role__is_active=True, user__is_active=True,
            ).select_related("user", "role", "tenant", "campus", "department")
        )

    @classmethod
    def _role_department_ids(cls, role_row, *, tenant_id):
        if role_row.role.code in cls.ROLE_ALIASES["CAO"]:
            return None
        if role_row.role.code not in cls.ROLE_ALIASES["COLLEGE_DEAN"]:
            if not role_row.department_id:
                return None
            return set(
                ScopeService.expand_department_ids(
                    [role_row.department_id], tenant_id=tenant_id, campus_id=role_row.campus_id
                )
            )
        if role_row.department_id:
            department_ids = set(
                ScopeService.expand_department_ids(
                    [role_row.department_id], tenant_id=tenant_id, campus_id=role_row.campus_id
                )
            )
        else:
            departments = Department.objects.filter(tenant_id=tenant_id, is_active=True)
            if role_row.campus_id:
                departments = departments.filter(campus_id=role_row.campus_id)
            department_ids = set(departments.values_list("id", flat=True))
        area_rows = UserRole.objects.filter(
            is_active=True,
            role__is_active=True,
            tenant_id=tenant_id,
            department_id__in=department_ids,
        ).filter(Q(role__code__in=cls.ROLE_ALIASES["AREA_CHAIR"]) | Q(role__code__endswith="_AC"))
        if role_row.campus_id:
            area_rows = area_rows.filter(campus_id=role_row.campus_id)
        supervised_ids = set()
        for area in area_rows.only("department_id", "campus_id"):
            supervised_ids.update(
                ScopeService.expand_department_ids(
                    [area.department_id], tenant_id=tenant_id, campus_id=area.campus_id
                )
            )
        return supervised_ids

    @classmethod
    def _faculty_department_ids_by_scope(cls, *, tenant_id, results):
        faculty_scopes = {
            (
                result.assignment.faculty_user_id,
                result.assignment.offering.tenant_id,
                result.assignment.offering.campus_id,
            )
            for result in results
        }
        if not faculty_scopes:
            return {}

        user_ids = {scope[0] for scope in faculty_scopes}
        campus_ids = {scope[2] for scope in faculty_scopes}
        department_ids_by_scope = defaultdict(set)
        faculty_roles = UserRole.objects.filter(
            user_id__in=user_ids,
            user__is_active=True,
            role__code="FACULTY",
            role__is_active=True,
            tenant_id=tenant_id,
            campus_id__in=campus_ids,
            department__isnull=False,
            department__is_active=True,
            department__tenant_id=F("tenant_id"),
            department__campus_id=F("campus_id"),
            is_active=True,
        ).values_list("user_id", "tenant_id", "campus_id", "department_id")
        for user_id, role_tenant_id, campus_id, department_id in faculty_roles:
            scope = (user_id, role_tenant_id, campus_id)
            if scope in faculty_scopes:
                department_ids_by_scope[scope].add(department_id)

        return {
            scope: next(iter(department_ids)) if len(department_ids) == 1 else None
            for scope, department_ids in department_ids_by_scope.items()
        }

    @classmethod
    def _role_covers(cls, role_row, result, department_ids, faculty_department_id):
        offering = result.assignment.offering
        if role_row.role.code in cls.ROLE_ALIASES["CAO"]:
            return role_row.tenant_id == offering.tenant_id
        if role_row.tenant_id and role_row.tenant_id != offering.tenant_id:
            return False
        if role_row.campus_id and role_row.campus_id != offering.campus_id:
            return False
        if not faculty_department_id:
            return False
        return department_ids is None or faculty_department_id in department_ids

    @staticmethod
    def _primary_concern(result):
        priorities = (
            "Required grading setup is incomplete.",
            "Some eligible students still have incomplete required records.",
            "No grade or attendance records have been encoded.",
            "No eligible active students are available for submission.",
            "Grade encoding is closed by academic governance.",
            "The grading period is locked.",
        )
        for concern in priorities:
            if concern in result.submission_blockers:
                return concern
        return result.submission_blockers[0] if result.submission_blockers else "No recent grading activity has been recorded."

    @classmethod
    def _idempotency_key(cls, *, recipient_id, result, policy, local_date):
        raw = "|".join([
            str(recipient_id), str(result.submission_deadline.isoformat()), str(result.template_period.id),
            str(result.assignment.offering.academic_year_id), str(result.assignment.offering.term_id),
            str(policy["days_before"]), str(policy["threshold"]), cls.POLICY_VERSION,
            local_date.isoformat() if policy["repeat"] else "single",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _dashboard_url(cls):
        base = (getattr(settings, "ADMIN_PORTAL_BASE_URL", "") or getattr(settings, "SITE_URL", "")).strip().rstrip("/")
        path = reverse("admin_portal:grade_submission_readiness")
        parsed = urlsplit(base)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not hostname or hostname == "localhost" or hostname.endswith(".local"):
            return ""
        try:
            if ipaddress.ip_address(hostname).is_loopback:
                return ""
        except ValueError:
            pass
        return f"{base}{path}"

    @classmethod
    def run(cls, *, now=None, as_of_date: date | None = None, tenant_id=None, dry_run=False, force=False):
        now = now or timezone.now()
        generated_at = now.astimezone(cls.MANILA)
        local_date = as_of_date or generated_at.date()
        tenants = Tenant.objects.filter(is_active=True)
        if tenant_id:
            tenants = tenants.filter(id=tenant_id)
        summary = {"tenants": 0, "eligible": 0, "sent": 0, "failed": 0, "duplicates": 0, "dry_run": 0}

        for tenant in tenants:
            policy = FeatureSettingsService.get_submission_readiness_email_policy(tenant_id=tenant.id)
            if not policy["enabled"] or not policy["role_codes"]:
                continue
            summary["tenants"] += 1
            assignments = cls._active_assignments(tenant.id)
            snapshot = GradeSubmissionReadinessService.calculate(assignments, now=now)
            qualifying = []
            for row in snapshot:
                if not row.template_period or not row.submission_deadline:
                    continue
                remaining_days = (row.submission_deadline.astimezone(cls.MANILA).date() - local_date).days
                date_matches = remaining_days == policy["days_before"] or (
                    policy["repeat"] and 0 <= remaining_days < policy["days_before"]
                )
                if date_matches and row.status != GradeSubmissionReadinessService.SUBMITTED and row.progress_percent < Decimal(str(policy["threshold"])):
                    qualifying.append(row)
            summary["eligible"] += len(qualifying)
            if not qualifying and not policy["send_empty"]:
                continue

            recipient_roles = cls._recipient_rows(tenant_id=tenant.id, configured_roles=policy["role_codes"])
            department_ids_by_role = {
                role_row.id: cls._role_department_ids(role_row, tenant_id=tenant.id)
                for role_row in recipient_roles
            }
            faculty_department_ids_by_scope = cls._faculty_department_ids_by_scope(
                tenant_id=tenant.id,
                results=qualifying,
            )
            faculty_department_ids_by_assignment = {}
            for result in qualifying:
                offering = result.assignment.offering
                faculty_scope = (
                    result.assignment.faculty_user_id,
                    offering.tenant_id,
                    offering.campus_id,
                )
                faculty_department_ids_by_assignment[result.assignment.id] = faculty_department_ids_by_scope.get(
                    faculty_scope
                )
            grouped = defaultdict(lambda: {"rows": {}, "roles": set(), "role_rows": []})
            for role_row in recipient_roles:
                for result in qualifying:
                    if not cls._role_covers(
                        role_row,
                        result,
                        department_ids_by_role[role_row.id],
                        faculty_department_ids_by_assignment[result.assignment.id],
                    ):
                        continue
                    group_key = (
                        role_row.user_id, result.assignment.offering.academic_year_id,
                        result.assignment.offering.term_id, result.template_period.id, result.submission_deadline,
                    )
                    grouped[group_key]["rows"][result.assignment.id] = result
                    grouped[group_key]["roles"].add(role_row.role.code)
                    grouped[group_key]["role_rows"].append(role_row)

            for group_key, payload in grouped.items():
                recipient_id = group_key[0]
                rows = sorted(payload["rows"].values(), key=GradeSubmissionReadinessService.sort_key)
                if not rows:
                    continue
                first = rows[0]
                key = cls._idempotency_key(recipient_id=recipient_id, result=first, policy=policy, local_date=local_date)
                prior_sent = SubmissionReadinessNotificationLog.objects.filter(
                    idempotency_key=key, status=SubmissionReadinessNotificationLog.Status.SENT
                ).exists()
                if prior_sent and not force:
                    summary["duplicates"] += 1
                    continue
                recipient = payload["role_rows"][0].user
                email = (recipient.email or "").strip()
                scope_context = {
                    "role_campus_ids": sorted({r.campus_id for r in payload["role_rows"] if r.campus_id}),
                    "role_department_ids": sorted({r.department_id for r in payload["role_rows"] if r.department_id}),
                    "report_campus_ids": sorted({r.assignment.offering.campus_id for r in rows}),
                    "report_department_ids": sorted(
                        {
                            faculty_department_ids_by_assignment[r.assignment.id]
                            for r in rows
                            if faculty_department_ids_by_assignment[r.assignment.id]
                        }
                    ),
                }
                report_campus_ids = scope_context["report_campus_ids"]
                context = {
                    "recipient": recipient, "rows": [
                        {"result": row, "concern": cls._primary_concern(row)} for row in rows
                    ], "threshold": policy["threshold"],
                    "days_before": (first.submission_deadline.astimezone(cls.MANILA).date() - local_date).days,
                    "generated_at": generated_at,
                    "deadline": first.submission_deadline.astimezone(cls.MANILA),
                    "academic_year": first.assignment.offering.academic_year,
                    "term": first.assignment.offering.term, "period": first.template_period,
                    "faculty_count": len({r.assignment.faculty_user_id for r in rows}),
                    "assignment_count": len(rows), "dashboard_url": cls._dashboard_url() if policy["include_link"] else "",
                }
                subject = f"TeacherMate+ Alert: Grade Submission Readiness - {context['days_before']} Days Before Deadline"
                log_key = key
                if dry_run:
                    log_key = hashlib.sha256(f"{key}|dry-run|{now.isoformat()}".encode()).hexdigest()
                elif force and prior_sent:
                    log_key = hashlib.sha256(f"{key}|force|{now.isoformat()}".encode()).hexdigest()
                log_defaults = dict(
                    tenant=tenant,
                    campus=first.assignment.offering.campus if len(report_campus_ids) == 1 else None,
                    recipient=recipient,
                    recipient_email=email, recipient_roles_json=sorted(payload["roles"]), scope_context_json=scope_context,
                    academic_year=first.assignment.offering.academic_year, term=first.assignment.offering.term,
                    template_period=first.template_period, deadline_at=first.submission_deadline,
                    threshold=policy["threshold"], days_before=policy["days_before"], policy_version=cls.POLICY_VERSION,
                    generated_at=now, faculty_count=context["faculty_count"], assignment_count=len(rows),
                    status=(SubmissionReadinessNotificationLog.Status.DRY_RUN if dry_run else SubmissionReadinessNotificationLog.Status.PROCESSING),
                    failure_reason="", idempotency_key=log_key, attempt_count=0 if dry_run else 1,
                    metadata_json={"assignment_ids": [r.assignment.id for r in rows]},
                )
                try:
                    with transaction.atomic():
                        log = SubmissionReadinessNotificationLog.objects.create(**log_defaults)
                except IntegrityError:
                    log = SubmissionReadinessNotificationLog.objects.get(idempotency_key=log_key)
                    if log.status == SubmissionReadinessNotificationLog.Status.SENT:
                        summary["duplicates"] += 1
                        continue
                    if log.status == SubmissionReadinessNotificationLog.Status.PROCESSING and log.generated_at >= now - timedelta(hours=2):
                        summary["duplicates"] += 1
                        continue
                    log.status = SubmissionReadinessNotificationLog.Status.PROCESSING
                    log.generated_at = now
                    log.failure_reason = ""
                    log.attempt_count += 1
                    log.save(update_fields=["status", "generated_at", "failure_reason", "attempt_count", "updated_at"])
                if dry_run:
                    summary["dry_run"] += 1
                    continue
                status = SubmissionReadinessNotificationLog.Status.FAILED
                failure = ""
                if not email:
                    failure = "Recipient has no email address."
                else:
                    try:
                        text_body = render_to_string("notifications/emails/submission_readiness_alert.txt", context)
                        html_body = render_to_string("notifications/emails/submission_readiness_alert.html", context)
                        message = EmailMultiAlternatives(
                            subject=subject, body=text_body, from_email=settings.DEFAULT_FROM_EMAIL, to=[email]
                        )
                        message.attach_alternative(html_body, "text/html")
                        message.send(fail_silently=False)
                        status = SubmissionReadinessNotificationLog.Status.SENT
                    except Exception as exc:
                        failure = f"{exc.__class__.__name__}: Email delivery failed."
                log.status = status
                log.failure_reason = failure
                log.save(update_fields=["status", "failure_reason", "updated_at"])
                if status == SubmissionReadinessNotificationLog.Status.SENT:
                    summary["sent"] += 1
                else:
                    summary["failed"] += 1
        return summary
