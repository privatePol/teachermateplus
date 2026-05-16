from __future__ import annotations

import secrets
import string

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.academics.models import FacultyAssignment
from apps.attendance.models import AttendanceRecord
from apps.core.services.features import FeatureSettingsService
from apps.enrollment.models import Enrollment
from apps.grading.models import GradeSubmission, StudentFinalGrade, StudentPeriodGrade
from apps.rbac.models import Permission, UserPermission

from .models import StudentAccountLink

User = get_user_model()


class StudentPortalAccessError(PermissionDenied):
    pass


class StudentPortalService:
    @staticmethod
    def get_active_link_for_user(user):
        if not user or not user.is_authenticated:
            raise StudentPortalAccessError("Sign in to use the Student Portal.")
        try:
            return (
                StudentAccountLink.objects.select_related(
                    "tenant",
                    "campus",
                    "student",
                    "student__department",
                    "student__program",
                    "user",
                )
                .get(user=user, is_active=True)
            )
        except StudentAccountLink.DoesNotExist as exc:
            raise StudentPortalAccessError("No active student account link is available for this user.") from exc

    @staticmethod
    def validate_link(link: StudentAccountLink):
        link.full_clean()
        return link

    @classmethod
    def scoped_enrollments(cls, link: StudentAccountLink):
        return (
            Enrollment.objects.select_related(
                "tenant",
                "campus",
                "academic_year",
                "term",
                "student",
                "course_offering",
                "course_offering__course",
                "course_offering__section",
                "course_offering__campus",
            )
            .filter(
                tenant=link.tenant,
                campus=link.campus,
                student=link.student,
            )
            .order_by("-academic_year__start_date", "term__sequence_no", "course_offering__course__code")
        )

    @classmethod
    def active_enrollments(cls, link: StudentAccountLink):
        return cls.scoped_enrollments(link).filter(is_active=True)

    @classmethod
    def get_owned_enrollment_for_offering(cls, link: StudentAccountLink, offering_id: int):
        enrollment = (
            cls.scoped_enrollments(link)
            .filter(course_offering_id=offering_id, is_active=True)
            .first()
        )
        if not enrollment:
            raise StudentPortalAccessError("This class is not available for the signed-in student.")
        return enrollment

    @staticmethod
    def primary_faculty_by_offering(offering_ids):
        rows = (
            FacultyAssignment.objects.select_related("faculty_user")
            .filter(offering_id__in=offering_ids, is_active=True)
            .order_by("offering_id", "-is_primary", "faculty_user__last_name", "faculty_user__first_name", "id")
        )
        faculty_map = {}
        for row in rows:
            faculty_map.setdefault(row.offering_id, row.faculty_user)
        return faculty_map

    @classmethod
    def visible_grade_rows(cls, link: StudentAccountLink, *, offering_id: int | None = None):
        enrollments = cls.active_enrollments(link)
        if offering_id is not None:
            enrollments = enrollments.filter(course_offering_id=offering_id)
        enrollments = list(enrollments)
        offering_ids = [row.course_offering_id for row in enrollments]
        period_grades_by_offering = {offering_id: [] for offering_id in offering_ids}
        final_grades_by_offering = {}

        show_periods = FeatureSettingsService.show_student_portal_period_grades_after_submission(
            tenant_id=link.tenant_id,
        )
        show_finals = FeatureSettingsService.show_student_portal_final_grades_after_submission(
            tenant_id=link.tenant_id,
        )

        if show_periods and offering_ids:
            submitted_pairs = set(
                GradeSubmission.objects.filter(
                    tenant=link.tenant,
                    campus=link.campus,
                    offering_id__in=offering_ids,
                    status=GradeSubmission.Status.SUBMITTED,
                ).values_list("offering_id", "template_period_id")
            )
            period_grades = (
                StudentPeriodGrade.objects.select_related("template_period", "offering", "offering__course")
                .filter(
                    tenant=link.tenant,
                    campus=link.campus,
                    student=link.student,
                    offering_id__in=offering_ids,
                )
                .order_by("offering_id", "template_period__sequence_no", "template_period__name")
            )
            for grade in period_grades:
                if (grade.offering_id, grade.template_period_id) in submitted_pairs:
                    period_grades_by_offering.setdefault(grade.offering_id, []).append(grade)

        if show_finals and offering_ids:
            final_grades_by_offering = {
                grade.offering_id: grade
                for grade in StudentFinalGrade.objects.select_related("offering", "offering__course").filter(
                    tenant=link.tenant,
                    campus=link.campus,
                    student=link.student,
                    offering_id__in=offering_ids,
                    is_submitted=True,
                )
            }

        faculty_map = cls.primary_faculty_by_offering(offering_ids)
        rows = []
        for enrollment in enrollments:
            offering = enrollment.course_offering
            rows.append(
                {
                    "enrollment": enrollment,
                    "offering": offering,
                    "faculty": faculty_map.get(offering.id),
                    "period_grades": period_grades_by_offering.get(offering.id, []),
                    "final_grade": final_grades_by_offering.get(offering.id),
                }
            )
        return rows

    @classmethod
    def attendance_rows(cls, link: StudentAccountLink):
        enrollments = list(cls.active_enrollments(link))
        offering_ids = [row.course_offering_id for row in enrollments]
        records_by_offering = {offering_id: [] for offering_id in offering_ids}
        show_details = FeatureSettingsService.show_student_portal_attendance_details(tenant_id=link.tenant_id)

        if offering_ids:
            records = (
                AttendanceRecord.objects.select_related(
                    "session",
                    "session__template_period",
                    "session__offering",
                    "session__offering__course",
                    "student",
                )
                .filter(
                    tenant=link.tenant,
                    campus=link.campus,
                    student=link.student,
                    session__tenant=link.tenant,
                    session__campus=link.campus,
                    session__offering_id__in=offering_ids,
                    is_active=True,
                    session__is_active=True,
                )
                .order_by("session__offering_id", "-session__session_date", "-session__created_at")
            )
            for record in records:
                records_by_offering.setdefault(record.session.offering_id, []).append(record)

        faculty_map = cls.primary_faculty_by_offering(offering_ids)
        rows = []
        for enrollment in enrollments:
            offering = enrollment.course_offering
            records = records_by_offering.get(offering.id, [])
            counts = {
                AttendanceRecord.Status.PRESENT: 0,
                AttendanceRecord.Status.ABSENT: 0,
                AttendanceRecord.Status.LATE: 0,
                AttendanceRecord.Status.EXCUSED: 0,
            }
            for record in records:
                if record.status_code in counts:
                    counts[record.status_code] += 1
            total = sum(counts.values())
            rows.append(
                {
                    "enrollment": enrollment,
                    "offering": offering,
                    "faculty": faculty_map.get(offering.id),
                    "total_count": total,
                    "present_count": counts[AttendanceRecord.Status.PRESENT],
                    "absent_count": counts[AttendanceRecord.Status.ABSENT],
                    "late_count": counts[AttendanceRecord.Status.LATE],
                    "excused_count": counts[AttendanceRecord.Status.EXCUSED],
                    "records": records if show_details else [],
                }
            )
        return rows


def create_student_account_link(*, tenant, campus, student, user, linked_by_user=None, notes=""):
    link = StudentAccountLink(
        tenant=tenant,
        campus=campus,
        student=student,
        user=user,
        linked_by_user=linked_by_user,
        notes=notes,
    )
    try:
        StudentPortalService.validate_link(link)
    except ValidationError:
        raise
    link.save()
    return link


class StudentAccountProvisioningService:
    @staticmethod
    def trusted_email_for_student(student):
        return (getattr(student, "official_email", "") or "").strip().lower()

    @staticmethod
    def _temporary_password(length=14):
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        while True:
            value = "".join(secrets.choice(alphabet) for _ in range(length))
            if (
                any(ch.islower() for ch in value)
                and any(ch.isupper() for ch in value)
                and any(ch.isdigit() for ch in value)
                and any(ch in "!@#$%^&*" for ch in value)
            ):
                return value

    @staticmethod
    def _base_username(student):
        raw = f"student-{student.campus.code}-{student.student_no}".lower()
        return "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "-" for ch in raw).strip("-")[:130]

    @classmethod
    def _unique_username(cls, student):
        base = cls._base_username(student) or f"student-{student.id}"
        candidate = base
        suffix = 1
        while User.objects.filter(username__iexact=candidate).exists():
            suffix += 1
            suffix_text = f"-{suffix}"
            candidate = f"{base[:150 - len(suffix_text)]}{suffix_text}"
        return candidate

    @classmethod
    @transaction.atomic
    def provision(cls, *, student, actor, existing_user=None, verify_official_email=False, notes=""):
        if StudentAccountLink.objects.filter(student=student, is_active=True).exists():
            raise ValidationError("This student already has an active Student Portal account link.")

        official_email = cls.trusted_email_for_student(student)
        if not official_email:
            raise ValidationError("Student has no official email. Update the student record before provisioning.")
        if not student.official_email_verified_at:
            if verify_official_email:
                student.official_email_verified_at = timezone.now()
                student.save(update_fields=["official_email_verified_at", "updated_at"])
            else:
                raise ValidationError("Confirm that the official student email has been verified before provisioning.")

        created_user = False
        temporary_password = None
        user = existing_user
        if user is None:
            user = User.objects.filter(email__iexact=official_email, is_active=True).order_by("id").first()
        if user is None:
            temporary_password = cls._temporary_password()
            user = User.objects.create_user(
                username=cls._unique_username(student),
                email=official_email,
                password=temporary_password,
                first_name=student.first_name,
                middle_name=student.middle_name,
                last_name=student.last_name,
                default_tenant=student.tenant,
                default_campus=student.campus,
                default_department=student.department,
                is_active=True,
                is_staff=False,
                must_change_password=True,
            )
            created_user = True
        else:
            if StudentAccountLink.objects.filter(user=user, is_active=True).exists():
                raise ValidationError("Selected or matched user already has an active Student Portal account link.")
            if user.default_tenant_id and user.default_tenant_id != student.tenant_id:
                raise ValidationError("Matched user default tenant does not match the selected student.")
            if user.default_campus_id and user.default_campus_id != student.campus_id:
                raise ValidationError("Matched user default campus does not match the selected student.")
            changed_fields = []
            if not user.default_tenant_id:
                user.default_tenant = student.tenant
                changed_fields.append("default_tenant")
            if not user.default_campus_id:
                user.default_campus = student.campus
                changed_fields.append("default_campus")
            if not user.default_department_id:
                user.default_department = student.department
                changed_fields.append("default_department")
            if changed_fields:
                changed_fields.append("updated_at")
                user.save(update_fields=changed_fields)

        permission, _ = Permission.objects.get_or_create(
            code="student_portal.access",
            defaults={
                "module": "student_portal",
                "action": "access",
                "description": "Allows a linked student user to access the Student Portal.",
                "is_active": True,
            },
        )
        UserPermission.objects.get_or_create(
            user=user,
            permission=permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=student.tenant,
            campus=student.campus,
        )
        link = create_student_account_link(
            tenant=student.tenant,
            campus=student.campus,
            student=student,
            user=user,
            linked_by_user=actor,
            notes=notes,
        )
        return {
            "user": user,
            "link": link,
            "created_user": created_user,
            "temporary_password": temporary_password,
            "official_email": official_email,
        }
