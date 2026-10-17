"""Read-only generation checks shared by all Insights workers.

Tokens contain filters and keyed digests, never grades or student records. Cache
entries are an optimization: a worker can reconstruct an unchanged generation.
Values are fingerprinted rather than MAX(updated_at), which misses bulk edits,
deletions and membership changes. All periods in the privacy basis are included.
"""
import hashlib
import json
from functools import wraps

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.http import JsonResponse
from django.utils.crypto import salted_hmac

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseBaseValueOverride, CourseTemplateAssignment, GradeActivity, GradeSubmission,
    GradingPeriodLock, GradingTemplate, GradingTemplateComponent, GradingTemplateDetail,
    GradingTemplatePeriod, GradingTemplateSubcomponent, StudentActivityScore,
    StudentPeriodGrade, TenantGradingProfile,
)
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant

CHANGED_MESSAGE = "Academic records have changed. Please click View Performance to refresh the report."


class RecordsChanged(Exception):
    pass


def changed_response(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except RecordsChanged:
            response = JsonResponse({"error": CHANGED_MESSAGE, "code": "records_changed"}, status=409)
            response["Cache-Control"] = "private, no-store"
            return response
    return guarded


def encoded(value):
    return json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, separators=(",", ":")).encode()


def authority(selection):
    value = (selection.request.user.pk, selection.request.scope.get("tenant_id"),
             selection.request.scope.get("campus_id"),
             sorted((a.pk, a.offering_id, a.faculty_user_id) for a in selection.assignments),
             sorted((key, sorted(v for v in values if v is not None))
                    for key, values in selection.faculty_departments.items()))
    return salted_hmac("academic-insights-scope-v1", encoded(value), algorithm="sha256").hexdigest()


def fingerprint(selection, filters):
    """Fingerprint grade inputs, history, membership, configuration and supervision.

    Reads are limited to the authorized year/term for academic records. Shared
    tenant grading/scope configuration is deliberately conservative: a config
    change invalidates a report rather than risking a missed formula dependency.
    """
    tenant_id = selection.request.scope["tenant_id"]
    offerings = selection.matching({key: filters[key] for key in ("academic_year_id", "term_id")})
    ids = [o.id for o in offerings]
    campus_ids = {o.campus_id for o in offerings}
    course_ids = {o.course_id for o in offerings}
    enrollment = Enrollment.objects.filter(course_offering_id__in=ids)
    student_ids = enrollment.values("student_id")
    user_ids = {selection.request.user.pk, *selection.faculty}
    roles = UserRole.objects.filter(user_id__in=user_ids)
    role_ids = roles.values("role_id")
    digest = hashlib.sha256(authority(selection).encode())

    def feed(queryset, fields=None):
        fields = fields or [f.attname for f in queryset.model._meta.concrete_fields]
        digest.update(encoded([queryset.model._meta.label_lower, fields]))
        for row in queryset.order_by("pk").values_list(*fields).iterator(chunk_size=2000):
            digest.update(encoded(row))
            digest.update(b"\n")

    feed(CourseOffering.objects.filter(pk__in=ids))
    feed(enrollment)
    feed(Student.objects.filter(pk__in=student_ids), ["id", "tenant_id", "campus_id", "department_id", "program_id", "is_active"])
    feed(GradeActivity.objects.filter(offering_id__in=ids))
    feed(StudentActivityScore.objects.filter(activity__offering_id__in=ids, student__tenant_id=tenant_id))
    feed(StudentPeriodGrade.objects.filter(offering_id__in=ids, student__tenant_id=tenant_id))
    feed(GradeSubmission.objects.filter(offering_id__in=ids))
    feed(AttendanceSession.objects.filter(offering_id__in=ids))
    feed(AttendanceRecord.objects.filter(session__offering_id__in=ids, student__tenant_id=tenant_id))
    feed(GradingPeriodLock.objects.filter(tenant_id=tenant_id, academic_year_id=filters["academic_year_id"], term_id=filters["term_id"]).filter(
        Q(campus_id__in=campus_ids) | Q(course_offering_id__in=ids)))
    for model in (GradingTemplate, TenantGradingProfile):
        feed(model.objects.filter(tenant_id=tenant_id))
    feed(GradingTemplatePeriod.objects.filter(template__tenant_id=tenant_id))
    feed(GradingTemplateComponent.objects.filter(template_period__template__tenant_id=tenant_id))
    feed(GradingTemplateSubcomponent.objects.filter(template_component__template_period__template__tenant_id=tenant_id))
    feed(GradingTemplateDetail.objects.filter(template_subcomponent__template_component__template_period__template__tenant_id=tenant_id))
    for model in (CourseTemplateAssignment, CourseBaseValueOverride):
        feed(model.objects.filter(course_id__in=course_ids))
    feed(Course.objects.filter(pk__in=course_ids))
    feed(Section.objects.filter(pk__in=[o.section_id for o in offerings]))
    feed(AcademicYear.objects.filter(pk=filters["academic_year_id"], tenant_id=tenant_id))
    feed(Term.objects.filter(pk=filters["term_id"], tenant_id=tenant_id))
    for model in (Department, Program):
        feed(model.objects.filter(tenant_id=tenant_id))
    feed(Campus.objects.filter(tenant_id=tenant_id))
    feed(Tenant.objects.filter(pk=tenant_id), ["id", "is_active"])
    feed(User.objects.filter(pk__in=user_ids), ["id", "is_active", "is_superuser", "default_tenant_id", "default_campus_id", "default_department_id", "first_name", "last_name", "username"])
    feed(roles)
    feed(Role.objects.filter(pk__in=role_ids))
    feed(RolePermission.objects.filter(role_id__in=role_ids))
    feed(UserPermission.objects.filter(user_id=selection.request.user.pk))
    feed(Permission.objects.filter(code__in=("admin_portal.access", "grading_analytics.read", "gradebook.view_student_identity")))
    # Never inspect unrelated settings/secrets.
    feed(SystemSetting.objects.filter(setting_key="PASSING_GRADE_THRESHOLD").filter(Q(tenant_id=tenant_id) | Q(tenant__isnull=True)))
    return salted_hmac("academic-insights-generation-v1", digest.hexdigest(), algorithm="sha256").hexdigest()
