from django.db import models

from apps.core.models import ActivatableModel, TimeStampedModel


class Enrollment(TimeStampedModel, ActivatableModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        DR = "DR", "Dropped"
        W = "W", "Withdrawn"

    class SourcePortal(models.TextChoices):
        ADMIN = "ADMIN", "Admin Portal"
        FACULTY = "FACULTY", "Faculty Portal"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="enrollments")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="enrollments")
    academic_year = models.ForeignKey("academics.AcademicYear", on_delete=models.PROTECT, related_name="enrollments")
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="enrollments")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="enrollments")
    course_offering = models.ForeignKey(
        "academics.CourseOffering", on_delete=models.PROTECT, related_name="enrollments"
    )
    enrollment_status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    encoded_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="encoded_enrollments",
    )
    encoded_via_portal = models.CharField(
        max_length=16,
        choices=SourcePortal.choices,
        default=SourcePortal.ADMIN,
    )

    class Meta:
        db_table = "enrollments"
        ordering = ["student__last_name", "student__first_name", "student__student_no"]
        constraints = [
            models.UniqueConstraint(fields=["course_offering", "student"], name="uq_enrollments_offering_student"),
        ]

    def __str__(self):
        return f"{self.course_offering_id}:{self.student.student_no}"
