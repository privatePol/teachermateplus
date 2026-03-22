from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.academics.models import CourseOffering
from apps.grading.models import GradeSubmission, GradingPeriodLock
from apps.notifications.models import NotificationQueue


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
