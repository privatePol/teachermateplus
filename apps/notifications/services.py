from __future__ import annotations

from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment
from apps.core.services.features import FeatureSettingsService
from apps.grading.models import GradeSubmission, GradingPeriodLock
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.notifications.models import (
    FacultyReminder,
    FacultyReminderEmailQueue,
    NotificationQueue,
    SubmissionNonComplianceNotice,
)
from apps.rbac.models import UserRole


class ReminderService:
    REFERENCE_TYPE = "PERIOD_CLOSE_REMINDER"

    @classmethod
    def _lock_target_offerings(cls, lock: GradingPeriodLock):
        qs = CourseOffering.objects.filter(
            tenant_id=lock.tenant_id,
            campus_id=lock.campus_id,
            academic_year_id=lock.academic_year_id,
            term_id=lock.term_id,
            is_active=True,
        )
        if lock.scope_type == GradingPeriodLock.ScopeType.COURSE and lock.course_offering_id:
            qs = qs.filter(id=lock.course_offering_id)
        return qs.select_related("course", "section", "term")

    @classmethod
    def queue_period_deadline_reminders(cls, *, now=None):
        if now is None:
            now = timezone.now()
        deadline_upper = now + timedelta(days=1, minutes=5)

        locks = GradingPeriodLock.objects.filter(
            is_active=True,
            is_locked=False,
            deadline_at__isnull=False,
            deadline_at__gt=now,
            deadline_at__lte=deadline_upper,
        ).select_related("tenant", "campus", "term")

        created = 0
        for lock in locks:
            offerings = cls._lock_target_offerings(lock)
            for offering in offerings:
                faculty_assignments = offering.faculty_assignments.filter(
                    is_active=True,
                    faculty_user__is_active=True,
                ).select_related("faculty_user")
                for assignment in faculty_assignments:
                    user = assignment.faculty_user

                    # Skip reminders for already submitted periods.
                    submitted = GradeSubmission.objects.filter(
                        offering_id=offering.id,
                        template_period__code=lock.period_code,
                        status=GradeSubmission.Status.SUBMITTED,
                    ).exists()
                    if submitted:
                        continue

                    reference_id = f"{offering.id}:{lock.period_code}:{lock.deadline_at.isoformat()}"
                    obj, was_created = NotificationQueue.objects.get_or_create(
                        recipient_user=user,
                        channel=NotificationQueue.Channel.EMAIL,
                        reference_type=cls.REFERENCE_TYPE,
                        reference_id=reference_id,
                        scheduled_at=now,
                        defaults={
                            "tenant_id": offering.tenant_id,
                            "campus_id": offering.campus_id,
                            "subject": f"Reminder: {lock.period_code} closes soon",
                            "body": (
                                f"{offering.course.code} / {offering.section.code} ({offering.term.code}) "
                                f"for period {lock.period_code} closes at {lock.deadline_at}."
                            ),
                            "status": NotificationQueue.Status.PENDING,
                            "metadata_json": {
                                "offering_id": offering.id,
                                "period_code": lock.period_code,
                                "deadline_at": lock.deadline_at.isoformat(),
                            },
                        },
                    )
                    if was_created:
                        created += 1
                    elif obj.status == NotificationQueue.Status.CANCELLED:
                        obj.status = NotificationQueue.Status.PENDING
                        obj.scheduled_at = now
                        obj.save(update_fields=["status", "scheduled_at", "updated_at"])
        return created


class FacultyReminderService:
    REMINDER_REFERENCE_TYPE = "FACULTY_REMINDER_EMAIL"
    DEFAULT_ACTIVITY_REMINDER_HOUR = 7

    @staticmethod
    def _portal_url(path_name: str) -> str:
        base_url = (
            getattr(settings, "FACULTY_PORTAL_BASE_URL", "")
            or getattr(settings, "SITE_URL", "")
            or ""
        ).strip().rstrip("/")
        path = reverse(path_name)
        if base_url:
            return f"{base_url}{path}"
        return path

    @classmethod
    def _build_email_payload(cls, reminder: FacultyReminder):
        portal_url = cls._portal_url("faculty_portal:reminder_center")
        subject = f"NCBA-EDUGRADESPRO: {reminder.title}"
        context = {
            "reminder": reminder,
            "portal_url": portal_url,
            "logo_url": getattr(settings, "FACULTY_PORTAL_REMINDER_LOGO_URL", "").strip()
            or "/media/logos/ncba-logo.png",
        }
        text_body = render_to_string("faculty_portal/emails/reminder_notification.txt", context)
        html_body = render_to_string("faculty_portal/emails/reminder_notification.html", context)
        return subject, text_body, html_body

    @classmethod
    def _activity_reminder_notes(cls, activity) -> str:
        course_code = getattr(getattr(activity, "offering", None), "course", None)
        section_code = getattr(getattr(activity, "offering", None), "section", None)
        course_label = getattr(course_code, "code", None) or "Course"
        section_label = getattr(section_code, "code", None) or "Section"
        activity_date = activity.activity_date.strftime("%B %d, %Y") if activity.activity_date else "scheduled date"
        return (
            f"Auto-created from future activity scheduling for {course_label} / {section_label}. "
            f"Prepare materials and scoring setup before {activity_date}."
        )

    @classmethod
    def _activity_reminder_datetimes(cls, activity_date):
        current_tz = timezone.get_current_timezone()
        remind_at = timezone.make_aware(
            datetime.combine(activity_date, time(hour=cls.DEFAULT_ACTIVITY_REMINDER_HOUR, minute=0)),
            current_tz,
        )
        due_at = timezone.make_aware(datetime.combine(activity_date, time(hour=23, minute=59)), current_tz)
        return remind_at, due_at

    @classmethod
    def cancel_activity_reminder(cls, *, activity, reason: str | None = None, now=None):
        now = now or timezone.now()
        reminder = FacultyReminder.objects.filter(grade_activity=activity, is_active=True).first()
        if reminder is None:
            return None

        reminder.is_active = False
        reminder.cancelled_at = now
        if reason:
            reminder.notes = reason
        reminder.save(update_fields=["is_active", "cancelled_at", "notes", "updated_at"])
        FacultyReminderEmailQueue.objects.filter(
            reminder=reminder,
            status__in=[
                FacultyReminderEmailQueue.Status.PENDING,
                FacultyReminderEmailQueue.Status.FAILED,
            ],
        ).update(
            status=FacultyReminderEmailQueue.Status.CANCELLED,
            error_message="Activity reminder cancelled because the activity is no longer scheduled in the future.",
            updated_at=now,
        )
        return reminder

    @classmethod
    def sync_activity_reminder(cls, *, activity, faculty_user, created_by=None, now=None):
        now = now or timezone.now()
        activity_date = getattr(activity, "activity_date", None)
        tenant_id = getattr(activity, "tenant_id", None)
        if not FeatureSettingsService.is_faculty_reminder_center_enabled(tenant_id=tenant_id, default=True):
            cls.cancel_activity_reminder(
                activity=activity,
                reason="Activity reminder auto-creation is disabled by tenant configuration.",
                now=now,
            )
            return None
        if not getattr(activity, "is_active", True) or activity_date is None or activity_date <= timezone.localdate():
            cls.cancel_activity_reminder(
                activity=activity,
                reason="Activity reminder cancelled because the activity date is not in the future.",
                now=now,
            )
            return None
        if faculty_user is None:
            return None

        remind_at, due_at = cls._activity_reminder_datetimes(activity_date)
        send_email = FeatureSettingsService.is_faculty_reminder_email_enabled(tenant_id=tenant_id, default=False)
        reminder, _ = FacultyReminder.objects.update_or_create(
            grade_activity=activity,
            defaults={
                "tenant_id": activity.tenant_id,
                "campus_id": activity.campus_id,
                "faculty_user": faculty_user,
                "offering_id": activity.offering_id,
                "reminder_type": FacultyReminder.ReminderType.ACTIVITY_PREPARATION,
                "title": f"Prepare Activity: {activity.title}",
                "period_label": getattr(activity.template_period, "name", None) or getattr(activity.template_period, "code", None),
                "notes": cls._activity_reminder_notes(activity),
                "remind_at": remind_at,
                "due_at": due_at,
                "send_email": send_email,
                "created_by": created_by or faculty_user,
                "is_active": True,
                "completed_at": None,
                "cancelled_at": None,
                "snoozed_until": None,
            },
        )
        FacultyReminderEmailQueue.objects.filter(
            reminder=reminder,
            status__in=[
                FacultyReminderEmailQueue.Status.PENDING,
                FacultyReminderEmailQueue.Status.FAILED,
            ],
        ).exclude(scheduled_at=remind_at).update(
            status=FacultyReminderEmailQueue.Status.CANCELLED,
            error_message="Activity reminder was rescheduled to a different date.",
            updated_at=now,
        )
        return reminder

    @classmethod
    def queue_due_email_notifications(cls, *, now=None, tenant_id: int | None = None, dry_run: bool = False):
        now = now or timezone.now()
        reminders = FacultyReminder.objects.filter(
            is_active=True,
            send_email=True,
            completed_at__isnull=True,
            cancelled_at__isnull=True,
            remind_at__lte=now,
        ).select_related("tenant", "campus", "faculty_user", "offering", "offering__course", "offering__section")
        if tenant_id is not None:
            reminders = reminders.filter(tenant_id=tenant_id)

        queued = 0
        for reminder in reminders:
            scoped_tenant_id = reminder.tenant_id
            if not FeatureSettingsService.is_faculty_reminder_email_enabled(tenant_id=scoped_tenant_id):
                continue
            recipient_email = (reminder.faculty_user.email or "").strip()
            if not recipient_email:
                continue
            dedupe_key = f"faculty-reminder:{reminder.id}:{reminder.remind_at.isoformat()}"
            if dry_run:
                queued += 1
                continue

            subject, text_body, html_body = cls._build_email_payload(reminder)
            queue_entry, was_created = FacultyReminderEmailQueue.objects.get_or_create(
                dedupe_key=dedupe_key,
                defaults={
                    "tenant_id": scoped_tenant_id,
                    "campus_id": reminder.campus_id or getattr(reminder.offering, "campus_id", None),
                    "reminder": reminder,
                    "recipient_user": reminder.faculty_user,
                    "recipient_email": recipient_email,
                    "subject": subject,
                    "text_body": text_body,
                    "html_body": html_body,
                    "scheduled_at": now,
                    "status": FacultyReminderEmailQueue.Status.PENDING,
                    "priority": 50 if reminder.reminder_type != FacultyReminder.ReminderType.GRADE_SUBMISSION else 10,
                    "metadata_json": {
                        "reminder_id": reminder.id,
                        "reminder_type": reminder.reminder_type,
                        "offering_id": reminder.offering_id,
                        "due_at": reminder.due_at.isoformat() if reminder.due_at else None,
                        "remind_at": reminder.remind_at.isoformat(),
                    },
                },
            )
            if was_created:
                queued += 1
                reminder.email_last_queued_at = now
                reminder.save(update_fields=["email_last_queued_at", "updated_at"])
            elif queue_entry.status == FacultyReminderEmailQueue.Status.CANCELLED:
                queue_entry.status = FacultyReminderEmailQueue.Status.PENDING
                queue_entry.scheduled_at = now
                queue_entry.save(update_fields=["status", "scheduled_at", "updated_at"])
        return queued

    @classmethod
    def process_email_queue(cls, *, now=None, batch_size: int = 50, dry_run: bool = False):
        now = now or timezone.now()
        queue = FacultyReminderEmailQueue.objects.filter(
            status=FacultyReminderEmailQueue.Status.PENDING,
            scheduled_at__lte=now,
        ).exclude(recipient_email__isnull=True).exclude(recipient_email__exact="").select_related("reminder", "recipient_user", "tenant", "campus").order_by("priority", "scheduled_at", "id")

        processed = 0
        for entry in queue[:batch_size]:
            processed += 1
            if dry_run:
                continue
            entry.status = FacultyReminderEmailQueue.Status.PROCESSING
            entry.last_attempt_at = now
            entry.attempt_count += 1
            entry.save(update_fields=["status", "last_attempt_at", "attempt_count", "updated_at"])
            try:
                from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@edugradespro.local")
                message = EmailMultiAlternatives(
                    subject=entry.subject,
                    body=entry.text_body,
                    from_email=from_email,
                    to=[entry.recipient_email],
                )
                message.attach_alternative(entry.html_body, "text/html")
                message.send(fail_silently=False)
                entry.status = FacultyReminderEmailQueue.Status.SENT
                entry.sent_at = now
                entry.error_message = None
                entry.save(update_fields=["status", "sent_at", "error_message", "updated_at"])

                reminder = entry.reminder
                reminder.email_last_sent_at = now
                reminder.email_attempt_count = reminder.email_attempt_count + 1
                reminder.save(update_fields=["email_last_sent_at", "email_attempt_count", "updated_at"])
            except Exception as exc:
                entry.status = FacultyReminderEmailQueue.Status.FAILED
                entry.error_message = str(exc)
                entry.save(update_fields=["status", "error_message", "updated_at"])
        return processed


class SubmissionNonComplianceNoticeService:
    FACULTY_PORTAL_REFERENCE_TYPE = "SUBMISSION_NON_COMPLIANCE_NOTICE"

    @classmethod
    def _portal_url(cls, path_name: str) -> str:
        base_url = (
            getattr(settings, "FACULTY_PORTAL_BASE_URL", "")
            or getattr(settings, "SITE_URL", "")
            or ""
        ).strip().rstrip("/")
        path = reverse(path_name)
        if base_url:
            return f"{base_url}{path}"
        return path

    @classmethod
    def _admin_url(cls, path_name: str) -> str:
        base_url = (
            getattr(settings, "ADMIN_PORTAL_BASE_URL", "")
            or getattr(settings, "SITE_URL", "")
            or ""
        ).strip().rstrip("/")
        path = reverse(path_name)
        if base_url:
            return f"{base_url}{path}"
        return path

    @classmethod
    def _latest_open_notice(cls, *, offering, template_period, faculty_user):
        return (
            SubmissionNonComplianceNotice.objects.filter(
                offering=offering,
                template_period=template_period,
                faculty_user=faculty_user,
                status=SubmissionNonComplianceNotice.Status.OPEN,
            )
            .order_by("-issued_at", "-id")
            .first()
        )

    @classmethod
    def _title_for_level(cls, level: str) -> str:
        return {
            SubmissionNonComplianceNotice.NoticeLevel.NOTICE: "Notice for Non-Compliance",
            SubmissionNonComplianceNotice.NoticeLevel.WARNING: "Warning for Continued Non-Compliance",
            SubmissionNonComplianceNotice.NoticeLevel.ESCALATION: "Escalation for Unresolved Non-Compliance",
        }.get(level, "Notice for Non-Compliance")

    @classmethod
    def _message_for_level(cls, *, level: str, offering, template_period, deadline_at):
        deadline_text = timezone.localtime(deadline_at).strftime("%B %d, %Y %I:%M %p")
        prefix = {
            SubmissionNonComplianceNotice.NoticeLevel.NOTICE: "This is a formal notice that the periodic grade submission is already overdue.",
            SubmissionNonComplianceNotice.NoticeLevel.WARNING: "This is a warning that the periodic grade submission remains overdue after the earlier notice.",
            SubmissionNonComplianceNotice.NoticeLevel.ESCALATION: "This is an escalation because the periodic grade submission remains overdue after prior notice and warning follow-up.",
        }.get(level, "The periodic grade submission is overdue.")
        return (
            f"{prefix} "
            f"Class: {offering.course.code} / {offering.section.code}. "
            f"Period: {template_period.name}. "
            f"Original deadline: {deadline_text}. "
            "Please complete any missing records and submit the period as soon as possible."
        )

    @classmethod
    def _collect_overdue_targets(cls, *, now, tenant_id: int | None = None):
        locks_qs = GradingPeriodLock.objects.filter(
            is_active=True,
            deadline_at__isnull=False,
            deadline_at__lt=now,
        )
        if tenant_id is not None:
            locks_qs = locks_qs.filter(tenant_id=tenant_id)
        overdue_locks = list(locks_qs.select_related("tenant", "campus", "term"))
        lock_targets = {}

        def _pick_lock(previous, new_lock):
            if previous is None:
                return new_lock
            if (
                previous.scope_type == GradingPeriodLock.ScopeType.CAMPUS
                and new_lock.scope_type == GradingPeriodLock.ScopeType.COURSE
            ):
                return new_lock
            if previous.scope_type == new_lock.scope_type and new_lock.updated_at > previous.updated_at:
                return new_lock
            return previous

        offerings_by_scope = {}
        for lock in overdue_locks:
            period_key = GradingGovernanceService._normalize_period_key(lock.period_code)
            if lock.scope_type == GradingPeriodLock.ScopeType.COURSE and lock.course_offering_id:
                target_key = (lock.course_offering_id, period_key)
                lock_targets[target_key] = _pick_lock(lock_targets.get(target_key), lock)
                continue
            scope_key = (lock.tenant_id, lock.campus_id, lock.academic_year_id, lock.term_id)
            if scope_key not in offerings_by_scope:
                offerings_by_scope[scope_key] = list(
                    CourseOffering.objects.filter(
                        tenant_id=lock.tenant_id,
                        campus_id=lock.campus_id,
                        academic_year_id=lock.academic_year_id,
                        term_id=lock.term_id,
                        is_active=True,
                    )
                )
            for offering in offerings_by_scope[scope_key]:
                target_key = (offering.id, period_key)
                lock_targets[target_key] = _pick_lock(lock_targets.get(target_key), lock)

        if not lock_targets:
            return []

        offering_ids = {offering_id for offering_id, _period_key in lock_targets.keys()}
        offerings = {
            row.id: row
            for row in CourseOffering.objects.filter(id__in=offering_ids).select_related(
                "tenant",
                "campus",
                "department",
                "academic_year",
                "term",
                "course",
                "section",
            )
        }
        assignment_rows = list(
            FacultyAssignment.objects.filter(
                offering_id__in=offering_ids,
                is_active=True,
                faculty_user__is_active=True,
            ).select_related("faculty_user")
        )
        assignments_by_offering = {}
        grouped_assignments = {}
        for assignment in assignment_rows:
            grouped_assignments.setdefault(assignment.offering_id, []).append(assignment)
        for offering_id, assignments in grouped_assignments.items():
            accepted_primary = next((row for row in assignments if row.accepted_at and row.is_primary), None)
            accepted_any = next((row for row in assignments if row.accepted_at), None)
            primary_any = next((row for row in assignments if row.is_primary), None)
            assignments_by_offering[offering_id] = accepted_primary or accepted_any or primary_any or assignments[0]

        targets = []
        for (offering_id, period_key), lock in lock_targets.items():
            offering = offerings.get(offering_id)
            if offering is None:
                continue
            assignment = assignments_by_offering.get(offering_id)
            if assignment is None:
                continue
            try:
                template = FacultyGradingService.resolve_template_for_offering(offering)
            except Exception:
                continue
            template_period = next(
                (
                    period
                    for period in template.periods.filter(is_active=True).order_by("sequence_no", "id")
                    if GradingGovernanceService._normalize_period_key(period.code or period.name) == period_key
                ),
                None,
            )
            if template_period is None:
                continue
            submission = GradingGovernanceService.get_submission(
                offering=offering,
                template_period=template_period,
            )
            if submission and submission.status == GradeSubmission.Status.SUBMITTED:
                cls.resolve_open_notices_for_scope(
                    offering=offering,
                    template_period=template_period,
                    submission=submission,
                    now=now,
                )
                continue
            targets.append(
                {
                    "offering": offering,
                    "template_period": template_period,
                    "faculty_user": assignment.faculty_user,
                    "deadline_at": lock.deadline_at,
                }
            )
        return targets

    @classmethod
    def resolve_open_notices_for_scope(cls, *, offering, template_period, submission=None, now=None):
        now = now or timezone.now()
        open_rows = SubmissionNonComplianceNotice.objects.filter(
            offering=offering,
            template_period=template_period,
            status=SubmissionNonComplianceNotice.Status.OPEN,
        )
        if submission is None:
            submission = GradingGovernanceService.get_submission(offering=offering, template_period=template_period)
        return open_rows.update(
            status=SubmissionNonComplianceNotice.Status.RESOLVED,
            resolved_at=now,
            resolution_note="Periodic grade submission completed.",
            submission=submission,
            updated_at=now,
        )

    @classmethod
    def resolve_submitted_notices(cls, *, tenant_id: int | None = None, now=None):
        now = now or timezone.now()
        notices = SubmissionNonComplianceNotice.objects.filter(
            status=SubmissionNonComplianceNotice.Status.OPEN,
        ).select_related("offering", "template_period")
        if tenant_id is not None:
            notices = notices.filter(tenant_id=tenant_id)
        resolved = 0
        seen = set()
        for notice in notices:
            key = (notice.offering_id, notice.template_period_id)
            if key in seen:
                continue
            seen.add(key)
            submission = GradingGovernanceService.get_submission(
                offering=notice.offering,
                template_period=notice.template_period,
            )
            if submission and submission.status == GradeSubmission.Status.SUBMITTED:
                resolved += cls.resolve_open_notices_for_scope(
                    offering=notice.offering,
                    template_period=notice.template_period,
                    submission=submission,
                    now=now,
                )
        return resolved

    @classmethod
    def _resolve_head_users(cls, *, offering):
        role_codes = FeatureSettingsService.get_submission_non_compliance_head_role_codes(
            tenant_id=offering.tenant_id
        )
        if not role_codes:
            return []
        rows = (
            UserRole.objects.filter(
                is_active=True,
                role__is_active=True,
                user__is_active=True,
                role__code__in=role_codes,
            )
            .filter(Q(tenant_id=offering.tenant_id) | Q(tenant__isnull=True))
            .filter(Q(campus_id=offering.campus_id) | Q(campus__isnull=True))
            .filter(Q(department_id=offering.department_id) | Q(department__isnull=True))
            .select_related("user", "role")
        )
        result = []
        seen = set()
        for row in rows:
            if row.user_id in seen:
                continue
            seen.add(row.user_id)
            result.append(row.user)
        return result

    @classmethod
    def _recipient_payload(cls, *, level: str, offering, faculty_user):
        faculty_emails = [email for email in [(faculty_user.email or "").strip()] if email]
        head_users = cls._resolve_head_users(offering=offering)
        head_emails = sorted({(user.email or "").strip() for user in head_users if (user.email or "").strip()})
        hr_emails = FeatureSettingsService.get_submission_non_compliance_hr_recipients(
            tenant_id=offering.tenant_id
        )
        recipient_emails = list(faculty_emails)
        recipient_roles = ["FACULTY"]
        if level == SubmissionNonComplianceNotice.NoticeLevel.ESCALATION:
            recipient_emails.extend([email for email in head_emails if email not in recipient_emails])
            recipient_emails.extend([email for email in hr_emails if email not in recipient_emails])
            if head_emails:
                recipient_roles.extend(
                    FeatureSettingsService.get_submission_non_compliance_head_role_codes(tenant_id=offering.tenant_id)
                )
            if hr_emails:
                recipient_roles.append("HR")
        return {
            "emails": recipient_emails,
            "roles": recipient_roles,
        }

    @classmethod
    def _build_email_payload(cls, *, notice):
        faculty_portal_url = cls._portal_url("faculty_portal:reminder_center")
        admin_report_url = cls._admin_url("admin_portal:overdue_unsubmitted_report")
        context = {
            "notice": notice,
            "faculty_portal_url": faculty_portal_url,
            "admin_report_url": admin_report_url,
            "logo_url": getattr(settings, "FACULTY_PORTAL_REMINDER_LOGO_URL", "").strip()
            or "/media/logos/ncba-logo.png",
        }
        text_body = render_to_string("notifications/emails/submission_non_compliance_notice.txt", context)
        html_body = render_to_string("notifications/emails/submission_non_compliance_notice.html", context)
        subject = f"NCBA-EDUGRADESPRO: {notice.title} - {notice.template_period.name}"
        return subject, text_body, html_body

    @classmethod
    def _send_notice_email(cls, *, notice, recipient_emails, now=None, dry_run: bool = False):
        now = now or timezone.now()
        if dry_run:
            return True
        notice.email_attempt_count = notice.email_attempt_count + 1
        if not recipient_emails:
            notice.email_status = SubmissionNonComplianceNotice.Status.FAILED
            notice.email_error_message = "No recipient emails configured for this notice."
            notice.save(update_fields=["email_attempt_count", "email_status", "email_error_message", "updated_at"])
            return False
        subject, text_body, html_body = cls._build_email_payload(notice=notice)
        try:
            from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@edugradespro.local")
            message = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=from_email,
                to=recipient_emails,
            )
            message.attach_alternative(html_body, "text/html")
            message.send(fail_silently=False)
            notice.email_status = SubmissionNonComplianceNotice.Status.RESOLVED
            notice.email_sent_at = now
            notice.email_error_message = None
            notice.save(
                update_fields=[
                    "email_attempt_count",
                    "email_status",
                    "email_sent_at",
                    "email_error_message",
                    "updated_at",
                ]
            )
            return True
        except Exception as exc:
            notice.email_status = SubmissionNonComplianceNotice.Status.FAILED
            notice.email_error_message = str(exc)
            notice.save(
                update_fields=[
                    "email_attempt_count",
                    "email_status",
                    "email_error_message",
                    "updated_at",
                ]
            )
            return False

    @classmethod
    def issue_due_notices(cls, *, now=None, tenant_id: int | None = None, dry_run: bool = False):
        now = now or timezone.now()
        if tenant_id is not None and not FeatureSettingsService.is_submission_non_compliance_notice_enabled(
            tenant_id=tenant_id
        ):
            return {"issued": 0, "resolved": 0, "dry_run": dry_run}

        resolved = cls.resolve_submitted_notices(tenant_id=tenant_id, now=now)
        targets = cls._collect_overdue_targets(now=now, tenant_id=tenant_id)
        issued = 0
        for target in targets:
            offering = target["offering"]
            if not FeatureSettingsService.is_submission_non_compliance_notice_enabled(tenant_id=offering.tenant_id):
                continue
            template_period = target["template_period"]
            faculty_user = target["faculty_user"]
            latest_notice = cls._latest_open_notice(
                offering=offering,
                template_period=template_period,
                faculty_user=faculty_user,
            )
            interval_days = FeatureSettingsService.get_submission_non_compliance_notice_interval_days(
                tenant_id=offering.tenant_id
            )
            if latest_notice and now < latest_notice.issued_at + timedelta(days=interval_days):
                continue
            if latest_notice is None:
                level = SubmissionNonComplianceNotice.NoticeLevel.NOTICE
                sequence_no = 1
            elif latest_notice.notice_level == SubmissionNonComplianceNotice.NoticeLevel.NOTICE:
                level = SubmissionNonComplianceNotice.NoticeLevel.WARNING
                sequence_no = latest_notice.sequence_no + 1
            else:
                level = SubmissionNonComplianceNotice.NoticeLevel.ESCALATION
                sequence_no = latest_notice.sequence_no + 1

            recipient_payload = cls._recipient_payload(
                level=level,
                offering=offering,
                faculty_user=faculty_user,
            )
            if dry_run:
                issued += 1
                continue
            notice = SubmissionNonComplianceNotice.objects.create(
                tenant_id=offering.tenant_id,
                campus_id=offering.campus_id,
                department_id=offering.department_id,
                offering=offering,
                template_period=template_period,
                faculty_user=faculty_user,
                notice_level=level,
                sequence_no=sequence_no,
                title=cls._title_for_level(level),
                message=cls._message_for_level(
                    level=level,
                    offering=offering,
                    template_period=template_period,
                    deadline_at=target["deadline_at"],
                ),
                deadline_at=target["deadline_at"],
                issued_at=now,
                recipient_emails_json=recipient_payload["emails"],
                recipient_roles_json=recipient_payload["roles"],
            )
            cls._send_notice_email(
                notice=notice,
                recipient_emails=recipient_payload["emails"],
                now=now,
                dry_run=dry_run,
            )
            issued += 1
        return {"issued": issued, "resolved": resolved, "dry_run": dry_run}
