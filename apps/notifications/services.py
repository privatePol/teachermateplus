from __future__ import annotations

from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.core.services.features import FeatureSettingsService
from apps.grading.models import GradeSubmission, GradingPeriodLock
from apps.notifications.models import FacultyReminder, FacultyReminderEmailQueue, NotificationQueue


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
