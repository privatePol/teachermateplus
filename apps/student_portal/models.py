from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import TimeStampedModel


class StudentAccountLink(TimeStampedModel):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="student_account_links")
    campus = models.ForeignKey("tenants.Campus", on_delete=models.PROTECT, related_name="student_account_links")
    student = models.ForeignKey("students.Student", on_delete=models.PROTECT, related_name="account_links")
    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="student_account_links")
    is_active = models.BooleanField(default=True)
    linked_by_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="created_student_account_links",
        blank=True,
        null=True,
    )
    linked_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "student_account_links"
        ordering = ["tenant", "campus", "student__last_name", "student__first_name", "-linked_at"]
        indexes = [
            models.Index(fields=["user", "is_active"], name="idx_student_link_user_active"),
            models.Index(fields=["tenant", "campus", "student", "is_active"], name="idx_student_link_scope"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["student"],
                condition=Q(is_active=True),
                name="uq_active_student_account_link_student",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_active=True),
                name="uq_active_student_account_link_user",
            ),
        ]

    def clean(self):
        super().clean()
        if self.campus_id and self.tenant_id and self.campus.tenant_id != self.tenant_id:
            raise ValidationError({"campus": "Campus must belong to the selected tenant."})
        if self.student_id:
            errors = {}
            if self.tenant_id and self.student.tenant_id != self.tenant_id:
                errors["student"] = "Student must belong to the selected tenant."
            if self.campus_id and self.student.campus_id != self.campus_id:
                errors["student"] = "Student must belong to the selected campus."
            if errors:
                raise ValidationError(errors)
        if self.user_id:
            errors = {}
            if self.user.default_tenant_id and self.tenant_id and self.user.default_tenant_id != self.tenant_id:
                errors["user"] = "User default tenant must match the student link tenant."
            if self.user.default_campus_id and self.campus_id and self.user.default_campus_id != self.campus_id:
                errors["user"] = "User default campus must match the student link campus."
            if errors:
                raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        status = "active" if self.is_active else "inactive"
        return f"{self.student.student_no} -> {self.user.username} ({status})"

