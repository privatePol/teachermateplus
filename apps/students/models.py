from django.db import models

from apps.core.models import ActivatableModel, TimeStampedModel


class Student(TimeStampedModel, ActivatableModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        GRADUATED = "GRADUATED", "Graduated"
        DROPPED = "DROPPED", "Dropped"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="students")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="students")
    department = models.ForeignKey("tenants.Department", on_delete=models.PROTECT, related_name="students")
    program = models.ForeignKey(
        "tenants.Program",
        on_delete=models.PROTECT,
        related_name="students",
        blank=True,
        null=True,
    )
    student_no = models.CharField(max_length=64)
    last_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, blank=True, null=True)
    sex = models.CharField(max_length=16, blank=True, null=True)
    year_level = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "students"
        ordering = ["last_name", "first_name", "student_no"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "student_no"], name="uq_students_tenant_student_no"),
        ]

    def __str__(self):
        return f"{self.student_no} - {self.last_name}, {self.first_name}"
