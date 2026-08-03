from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.core.services.audit import AuditService
from apps.core.services.permissions import PermissionService
from apps.tenants.models import Department

from .services import AdminScopeService


def exam_department_label(department) -> str:
    """Return the campus-qualified label used for exact exam ownership."""
    return f"{department.code} — {department.name} — {department.campus.code}"


@dataclass(frozen=True)
class BulkExamDepartmentAssignmentResult:
    department: object
    updated_count: int
    unchanged_same_count: int
    skipped_existing_count: int
    total_selected: int


class BulkExamDepartmentAssignmentService:
    """Validate, lock, update, and audit Course exam ownership atomically."""

    UPDATE_PERMISSION = "courses.update"

    @staticmethod
    def _normalized_course_ids(course_ids) -> list[int]:
        raw_ids = list(course_ids or [])
        if not raw_ids:
            raise ValidationError("Select at least one Course.")

        normalized_ids = []
        for raw_id in raw_ids:
            try:
                course_id = int(raw_id)
            except (TypeError, ValueError) as exc:
                raise ValidationError("One or more selected Course IDs are invalid.") from exc
            if course_id <= 0:
                raise ValidationError("One or more selected Course IDs are invalid.")
            normalized_ids.append(course_id)

        if len(normalized_ids) != len(set(normalized_ids)):
            raise ValidationError("Duplicate Course IDs are not allowed.")
        return normalized_ids

    @classmethod
    @transaction.atomic
    def assign(
        cls,
        *,
        request,
        department_id,
        course_ids,
        replace_existing=False,
    ) -> BulkExamDepartmentAssignmentResult:
        user = getattr(request, "user", None)
        scope = getattr(request, "scope", {})
        tenant_id = scope.get("tenant_id")
        campus_id = scope.get("campus_id")
        if (
            not user
            or not user.is_authenticated
            or not user.is_active
            or not tenant_id
            or not PermissionService.has_permission(
                user,
                "admin_portal.access",
                tenant_id=tenant_id,
                campus_id=campus_id,
            )
            or not PermissionService.has_permission(
                user,
                cls.UPDATE_PERMISSION,
                tenant_id=tenant_id,
                campus_id=campus_id,
            )
        ):
            raise PermissionDenied("You do not have permission to bulk update Courses.")

        try:
            parsed_department_id = int(department_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Select a valid Responsible Exam Department.") from exc
        if parsed_department_id <= 0:
            raise ValidationError("Select a valid Responsible Exam Department.")

        normalized_course_ids = cls._normalized_course_ids(course_ids)

        if type(replace_existing) is not bool:
            raise ValidationError(
                "Replacement authorization must be an explicit boolean value."
            )

        try:
            department = (
                AdminScopeService.active_scoped_departments(request)
                .select_for_update()
                .filter(tenant_id=tenant_id)
                .select_related("tenant", "campus")
                .get(id=parsed_department_id)
            )
        except Department.DoesNotExist:
            raise ValidationError(
                "The selected Responsible Exam Department is inactive or outside your current tenant scope."
            )

        if (
            not department.is_active
            or department.tenant_id != tenant_id
            or not department.tenant.is_active
            or not department.campus.is_active
        ):
            raise ValidationError(
                "The selected Responsible Exam Department is inactive or outside your current tenant scope."
            )

        # Lock the exact target Department first, then Course rows by primary key.
        # Every caller uses this order so concurrent bulk assignments serialize
        # without widening the administrator's existing Department/Course scope.
        courses = list(
            AdminScopeService.active_scoped_courses(request)
            .filter(tenant_id=tenant_id, id__in=normalized_course_ids)
            .select_for_update()
            .select_related("tenant", "campus", "department", "exam_department__campus")
            .order_by("id")
        )
        if len(courses) != len(normalized_course_ids):
            raise ValidationError(
                "One or more selected Courses are inactive or outside your current tenant scope."
            )

        updated_count = 0
        unchanged_same_count = 0
        skipped_existing_count = 0
        for course in courses:
            old_department = course.exam_department
            if course.exam_department_id == department.id:
                unchanged_same_count += 1
                continue
            if course.exam_department_id is not None and not replace_existing:
                skipped_existing_count += 1
                continue

            before_data = {
                "exam_department_id": course.exam_department_id,
                "exam_department_label": (
                    exam_department_label(old_department) if old_department else None
                ),
            }
            course.exam_department = department
            course.save(update_fields=["exam_department", "updated_at"])
            AuditService.log_event(
                action="UPDATE",
                portal="ADMIN",
                entity_type="Course",
                entity_id=course.id,
                actor=user,
                tenant=course.tenant,
                campus=department.campus,
                before_data=before_data,
                after_data={
                    "exam_department_id": department.id,
                    "exam_department_label": exam_department_label(department),
                },
                metadata={
                    "source": "bulk_exam_department_assignment",
                    "replace_existing": replace_existing,
                },
                request=request,
            )
            updated_count += 1

        return BulkExamDepartmentAssignmentResult(
            department=department,
            updated_count=updated_count,
            unchanged_same_count=unchanged_same_count,
            skipped_existing_count=skipped_existing_count,
            total_selected=len(normalized_course_ids),
        )
