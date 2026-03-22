from django.db import models

from apps.core.models import ActivatableModel, TimeStampedModel


class AttendanceSession(TimeStampedModel, ActivatableModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="attendance_sessions")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="attendance_sessions")
    offering = models.ForeignKey(
        "academics.CourseOffering",
        on_delete=models.PROTECT,
        related_name="attendance_sessions",
    )
    template_period = models.ForeignKey(
        "grading.GradingTemplatePeriod",
        on_delete=models.PROTECT,
        related_name="attendance_sessions",
    )
    session_date = models.DateField()
    title = models.CharField(max_length=120, blank=True, null=True)
    created_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="created_attendance_sessions",
    )

    class Meta:
        db_table = "attendance_sessions"
        ordering = ["-session_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "template_period", "session_date"],
                name="uq_attendance_sessions_offering_period_date",
            ),
        ]

    def __str__(self):
        return f"{self.offering_id}:{self.session_date}"


class AttendanceRecord(TimeStampedModel, ActivatableModel):
    class Status(models.TextChoices):
        PRESENT = "P", "Present"
        ABSENT = "A", "Absent"
        LATE = "L", "Late"
        EXCUSED = "E", "Excused"

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.PROTECT,
        related_name="attendance_records",
        blank=True,
        null=True,
    )
    campus = models.ForeignKey(
        "tenants.Campus",
        on_delete=models.PROTECT,
        related_name="attendance_records",
        blank=True,
        null=True,
    )
    session = models.ForeignKey(
        "attendance.AttendanceSession",
        on_delete=models.PROTECT,
        related_name="records",
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    status_code = models.CharField(max_length=1, choices=Status.choices, default=Status.PRESENT)
    recorded_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="recorded_attendance_records",
    )
    remarks = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = "attendance_records"
        ordering = ["student__last_name", "student__first_name", "student__student_no"]
        constraints = [
            models.UniqueConstraint(fields=["session", "student"], name="uq_attendance_records_session_student"),
        ]

    def __str__(self):
        return f"{self.session_id}:{self.student_id}:{self.status_code}"
