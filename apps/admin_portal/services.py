from __future__ import annotations

from django.db import models
from django.forms.models import model_to_dict

from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.scope import ScopeService
from apps.enrollment.models import ClassListChangeRequest, Enrollment
from apps.grading.models import (
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    GradeCorrectionRequest,
    GradeEncodingControl,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    TemplateHotfixRequest,
    TenantGradingProfile,
)
from apps.grading.access import GradingTemplateAccessService
from apps.imports.models import ImportBatch
from apps.rbac.models import UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class AdminScopeService:
    AREA_CHAIR_ROLE_CODES = {"AC", "AREA_CHAIR", "AREA_CHAIRPERSON"}
    COLLEGE_DEAN_ROLE_CODE = "COLLEGE_DEAN"

    @staticmethod
    def expand_department_filter_ids(department_id, *, tenant_id: int | None = None, campus_id: int | None = None):
        try:
            parsed_department_id = int(department_id) if department_id not in (None, "") else None
        except (TypeError, ValueError):
            parsed_department_id = None
        if not parsed_department_id:
            return []
        return ScopeService.expand_department_ids(
            [parsed_department_id],
            tenant_id=tenant_id,
            campus_id=campus_id,
        )

    @staticmethod
    def _has_unrestricted_department_scope(request, tenant_ids, campus_ids):
        if request.user.is_superuser:
            return True
        current_tenant_id = getattr(request, "scope", {}).get("tenant_id")
        current_campus_id = getattr(request, "scope", {}).get("campus_id")
        scoped_roles = UserRole.objects.filter(
            user=request.user,
            is_active=True,
            role__is_active=True,
        ).exclude(role__code="FACULTY")
        if current_tenant_id:
            scoped_roles = scoped_roles.filter(models.Q(tenant_id=current_tenant_id) | models.Q(tenant__isnull=True))
        else:
            scoped_roles = scoped_roles.filter(models.Q(tenant_id__in=tenant_ids) | models.Q(tenant__isnull=True))
        if current_campus_id:
            scoped_roles = scoped_roles.filter(models.Q(campus_id=current_campus_id) | models.Q(campus__isnull=True))
        else:
            scoped_roles = scoped_roles.filter(models.Q(campus_id__in=campus_ids) | models.Q(campus__isnull=True))
        return scoped_roles.filter(department__isnull=True).exists()

    @staticmethod
    def _scoped_tenant_campus_department_ids(request):
        tenants = list(AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True))
        campuses = list(AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True))
        departments = list(AdminScopeService.active_scoped_departments(request).values_list("id", flat=True))
        return tenants, campuses, departments

    @staticmethod
    def _visible_queryset(request, queryset):
        """
        Super admin can view active/inactive records.
        Other users can only view active records.
        """
        if request.user.is_superuser:
            return queryset
        model = getattr(queryset, "model", None)
        if model and any(field.name == "is_active" for field in model._meta.fields):
            return queryset.filter(is_active=True)
        return queryset

    @staticmethod
    def scoped_tenants(request):
        if request.user.is_superuser:
            return Tenant.objects.all().order_by("name")
        tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
        queryset = Tenant.objects.filter(id__in=tenant_ids).order_by("name")
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def active_scoped_tenants(request):
        return AdminScopeService.scoped_tenants(request).filter(is_active=True)

    @staticmethod
    def scoped_campuses(request):
        if request.user.is_superuser:
            return Campus.objects.select_related("tenant").filter(tenant__is_active=True).order_by("tenant__name", "name")
        tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
        campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
        queryset = Campus.objects.filter(tenant_id__in=tenant_ids, id__in=campus_ids).select_related("tenant").order_by(
            "tenant__name", "name"
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def active_scoped_campuses(request):
        return AdminScopeService.scoped_campuses(request).filter(is_active=True, tenant__is_active=True)

    @staticmethod
    def scoped_departments(request):
        campuses = AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True)
        tenants = AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True)
        department_ids = getattr(request, "scope", {}).get("department_ids", [])
        queryset = (
            Department.objects.filter(tenant_id__in=tenants, campus_id__in=campuses)
            .select_related("tenant", "campus", "parent")
            .order_by("tenant__name", "campus__name", "name")
        )
        if not request.user.is_superuser and department_ids:
            queryset = queryset.filter(id__in=department_ids)
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def active_scoped_departments(request):
        return AdminScopeService.scoped_departments(request).filter(is_active=True)

    @staticmethod
    def scoped_programs(request):
        departments = AdminScopeService.active_scoped_departments(request).values_list("id", flat=True)
        queryset = (
            Program.objects.filter(department_id__in=departments)
            .select_related("tenant", "campus", "department")
            .order_by("tenant__name", "campus__name", "department__name", "name")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def active_scoped_programs(request):
        return AdminScopeService.scoped_programs(request).filter(is_active=True, department__is_active=True)

    @staticmethod
    def scoped_academic_years(request):
        tenants = AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True)
        queryset = AcademicYear.objects.filter(tenant_id__in=tenants).select_related("tenant").order_by("-start_date")
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_terms(request):
        academic_years = AdminScopeService.active_scoped_academic_years(request).values_list("id", flat=True)
        queryset = (
            Term.objects.filter(academic_year_id__in=academic_years)
            .select_related("tenant", "academic_year")
            .order_by("-academic_year__start_date", "sequence_no")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def active_scoped_academic_years(request):
        return AdminScopeService.scoped_academic_years(request).filter(is_active=True)

    @staticmethod
    def active_scoped_terms(request):
        return AdminScopeService.scoped_terms(request).filter(is_active=True, academic_year__is_active=True)

    @staticmethod
    def scoped_courses(request):
        tenants = AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True)
        campuses = AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True)
        department_ids = getattr(request, "scope", {}).get("department_ids", [])
        queryset = (
            Course.objects.filter(tenant_id__in=tenants)
            .filter(models.Q(campus__isnull=True) | models.Q(campus_id__in=campuses))
            .filter(models.Q(department__isnull=True) | models.Q(department__is_active=True))
            .select_related("tenant", "campus", "department")
            .order_by("code")
        )
        if not request.user.is_superuser and department_ids:
            queryset = queryset.filter(models.Q(department_id__in=department_ids) | models.Q(department__isnull=True))
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def active_scoped_courses(request):
        return AdminScopeService.scoped_courses(request).filter(
            models.Q(department__isnull=True) | models.Q(department__is_active=True),
            is_active=True,
        )

    @staticmethod
    def scoped_sections(request):
        programs = AdminScopeService.active_scoped_programs(request).values_list("id", flat=True)
        queryset = (
            Section.objects.filter(program_id__in=programs)
            .filter(department__is_active=True, program__is_active=True, program__department__is_active=True)
            .select_related("tenant", "campus", "department", "program")
            .order_by("code")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def active_scoped_sections(request):
        return AdminScopeService.scoped_sections(request).filter(
            is_active=True,
            department__is_active=True,
            program__is_active=True,
            program__department__is_active=True,
        )

    @staticmethod
    def scoped_course_offerings(request):
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        sections = AdminScopeService.active_scoped_sections(request).values_list("id", flat=True)
        queryset = (
            CourseOffering.objects.filter(term_id__in=terms, section_id__in=sections)
            .filter(
                department__is_active=True,
                program__is_active=True,
                program__department__is_active=True,
                course__is_active=True,
                section__is_active=True,
                section__department__is_active=True,
                section__program__is_active=True,
                section__program__department__is_active=True,
            )
            .filter(models.Q(course__department__isnull=True) | models.Q(course__department__is_active=True))
            .select_related(
                "tenant",
                "campus",
                "department",
                "program",
                "academic_year",
                "term",
                "course",
                "section",
            )
            .order_by("-created_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_faculty_assignments(request, *, include_all_campuses=False):
        tenants = list(AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True))
        faculty_ids_by_campus = AdminScopeService._faculty_ids_by_monitoring_campus(
            request,
            include_all_campuses=include_all_campuses,
        )
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        campus_faculty_filter = models.Q(pk__in=[])
        for campus_id, faculty_user_ids in faculty_ids_by_campus.items():
            if faculty_user_ids:
                campus_faculty_filter |= models.Q(
                    offering__campus_id=campus_id,
                    faculty_user_id__in=faculty_user_ids,
                )
        queryset = (
            FacultyAssignment.objects.filter(
                offering__tenant_id__in=tenants,
                offering__term_id__in=terms,
                offering__is_active=True,
                offering__department__is_active=True,
                offering__program__is_active=True,
                offering__program__department__is_active=True,
                offering__course__is_active=True,
                offering__section__is_active=True,
                offering__section__department__is_active=True,
                offering__section__program__is_active=True,
                offering__section__program__department__is_active=True,
            )
            .filter(campus_faculty_filter)
            .filter(models.Q(offering__course__department__isnull=True) | models.Q(offering__course__department__is_active=True))
            .select_related(
                "offering",
                "faculty_user",
                "offering__course",
                "offering__section",
                "offering__term",
                "offering__academic_year",
                "offering__campus",
                "offering__department",
            )
            .order_by("-assigned_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_monitoring_course_offerings(request, *, include_all_campuses=False):
        assignment_offering_ids = (
            AdminScopeService.scoped_faculty_assignments(
                request,
                include_all_campuses=include_all_campuses,
            )
            .filter(
                is_active=True,
                response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
                faculty_user__is_active=True,
            )
            .values_list("offering_id", flat=True)
        )
        queryset = (
            CourseOffering.objects.filter(id__in=assignment_offering_ids)
            .select_related(
                "tenant",
                "campus",
                "department",
                "program",
                "academic_year",
                "term",
                "course",
                "section",
            )
            .order_by("-created_at")
            .distinct()
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_students(request):
        tenants, campuses, departments = AdminScopeService._scoped_tenant_campus_department_ids(request)
        programs = AdminScopeService.active_scoped_programs(request).values_list("id", flat=True)
        queryset = (
            Student.objects.filter(tenant_id__in=tenants, campus_id__in=campuses, department_id__in=departments)
            .filter(department__is_active=True)
            .filter(models.Q(program_id__in=programs) | models.Q(program__isnull=True))
            .select_related("tenant", "campus", "department", "program")
            .order_by("last_name", "first_name")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_enrollments(request):
        tenants, campuses, departments = AdminScopeService._scoped_tenant_campus_department_ids(request)
        academic_years = list(AdminScopeService.active_scoped_academic_years(request).values_list("id", flat=True))
        terms = list(AdminScopeService.active_scoped_terms(request).values_list("id", flat=True))
        queryset = (
            Enrollment.objects.filter(
                tenant_id__in=tenants,
                campus_id__in=campuses,
                academic_year_id__in=academic_years,
                term_id__in=terms,
                student__tenant_id__in=tenants,
                student__campus_id__in=campuses,
                student__department_id__in=departments,
                course_offering__tenant_id__in=tenants,
                course_offering__campus_id__in=campuses,
                course_offering__department_id__in=departments,
                course_offering__section__department_id__in=departments,
                course_offering__academic_year_id__in=academic_years,
                course_offering__term_id__in=terms,
            )
            .filter(
                academic_year__is_active=True,
                term__is_active=True,
                term__academic_year__is_active=True,
                student__is_active=True,
                student__department__is_active=True,
                course_offering__is_active=True,
                course_offering__academic_year__is_active=True,
                course_offering__term__is_active=True,
                course_offering__term__academic_year__is_active=True,
                course_offering__department__is_active=True,
                course_offering__course__is_active=True,
                course_offering__section__is_active=True,
                course_offering__section__department__is_active=True,
                course_offering__section__program__is_active=True,
                course_offering__section__program__department__is_active=True,
            )
            .filter(models.Q(student__program__isnull=True) | models.Q(student__program__is_active=True))
            .filter(models.Q(course_offering__program__isnull=True) | models.Q(course_offering__program__is_active=True))
            .filter(
                models.Q(course_offering__course__department__isnull=True)
                | models.Q(course_offering__course__department__is_active=True)
            )
            .select_related(
                "tenant",
                "campus",
                "academic_year",
                "term",
                "student",
                "course_offering",
                "course_offering__course",
                "course_offering__section",
            )
            .order_by("student__last_name", "student__first_name")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_class_list_change_requests(request):
        offerings = AdminScopeService.scoped_course_offerings(request).values_list("id", flat=True)
        campuses = AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True)
        queryset = (
            ClassListChangeRequest.objects.filter(offering_id__in=offerings, campus_id__in=campuses)
            .exclude(status=ClassListChangeRequest.Status.CANCELLED)
            .select_related(
                "tenant",
                "campus",
                "offering",
                "offering__course",
                "offering__section",
                "offering__term",
                "faculty_requester",
                "reviewed_by",
            )
            .prefetch_related("items", "items__student", "items__enrollment", "items__enrollment__student")
            .order_by("-created_at", "-id")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_faculty_users(request, *, include_all_campuses=False):
        faculty_ids_by_campus = AdminScopeService._faculty_ids_by_monitoring_campus(
            request,
            include_all_campuses=include_all_campuses,
        )
        faculty_user_ids = {
            faculty_user_id
            for campus_faculty_ids in faculty_ids_by_campus.values()
            for faculty_user_id in campus_faculty_ids
        }
        return (
            UserRole.objects.filter(
                role__code="FACULTY",
                is_active=True,
                role__is_active=True,
                user__is_active=True,
                user_id__in=faculty_user_ids,
            )
            .values_list("user_id", flat=True)
            .distinct()
        )

    @staticmethod
    def _monitoring_campus_ids(request, *, include_all_campuses=False):
        campus_ids = list(AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True))
        current_campus_id = getattr(request, "scope", {}).get("campus_id")
        if not include_all_campuses and current_campus_id in campus_ids:
            return [current_campus_id]
        return campus_ids

    @staticmethod
    def _faculty_ids_by_monitoring_campus(request, *, include_all_campuses=False):
        tenant_id = getattr(request, "scope", {}).get("tenant_id")
        if not tenant_id:
            return {}
        campus_ids = AdminScopeService._monitoring_campus_ids(
            request,
            include_all_campuses=include_all_campuses,
        )
        uses_dean_chain = AdminScopeService._uses_college_dean_supervision(request)
        faculty_ids_by_campus = {}
        for campus_id in campus_ids:
            department_ids = None
            if not request.user.is_superuser:
                role_scope = UserRole.objects.filter(
                    user=request.user,
                    is_active=True,
                    role__is_active=True,
                ).exclude(role__code="FACULTY")
                role_scope = role_scope.filter(
                    models.Q(tenant_id=tenant_id) | models.Q(tenant__isnull=True)
                ).filter(models.Q(campus_id=campus_id) | models.Q(campus__isnull=True))
                unrestricted_department_scope = role_scope.filter(department__isnull=True).exists()
                accessible_department_ids = ScopeService.get_accessible_department_ids(
                    request.user,
                    tenant_id=tenant_id,
                    campus_id=campus_id,
                )
                if uses_dean_chain:
                    department_ids = AdminScopeService._college_dean_area_chair_department_ids(
                        request,
                        tenant_ids=[tenant_id],
                        campus_ids=[campus_id],
                        dean_department_ids=accessible_department_ids,
                    )
                elif not unrestricted_department_scope:
                    department_ids = accessible_department_ids

            faculty_roles = UserRole.objects.filter(
                role__code="FACULTY",
                role__is_active=True,
                is_active=True,
                user__is_active=True,
            ).filter(
                models.Q(tenant_id=tenant_id)
                | (
                    models.Q(tenant__isnull=True)
                    & models.Q(user__default_tenant_id=tenant_id)
                )
            ).filter(
                models.Q(campus_id=campus_id)
                | (
                    models.Q(campus__isnull=True)
                    & models.Q(user__default_campus_id=campus_id)
                )
            )
            if department_ids is not None:
                if not department_ids:
                    faculty_ids_by_campus[campus_id] = []
                    continue
                faculty_roles = faculty_roles.filter(
                    models.Q(department_id__in=department_ids)
                    | (
                        models.Q(department__isnull=True)
                        & models.Q(user__default_campus_id=campus_id)
                        & models.Q(user__default_department_id__in=department_ids)
                    )
                )
            faculty_ids_by_campus[campus_id] = list(
                faculty_roles.values_list("user_id", flat=True).distinct()
            )
        return faculty_ids_by_campus

    @staticmethod
    def _uses_college_dean_supervision(request):
        if request.user.is_superuser:
            return False
        active_non_faculty_roles = UserRole.objects.filter(
            user=request.user,
            is_active=True,
            role__is_active=True,
        ).exclude(role__code="FACULTY")
        return (
            active_non_faculty_roles.exists()
            and not active_non_faculty_roles.exclude(role__code=AdminScopeService.COLLEGE_DEAN_ROLE_CODE).exists()
        )

    @staticmethod
    def _college_dean_area_chair_department_ids(
        request,
        *,
        tenant_ids,
        campus_ids,
        dean_department_ids,
    ):
        area_chair_roles = (
            UserRole.objects.filter(
                is_active=True,
                role__is_active=True,
                tenant_id__in=tenant_ids,
                campus_id__in=campus_ids,
            )
            .filter(
                models.Q(role__code__in=AdminScopeService.AREA_CHAIR_ROLE_CODES)
                | models.Q(role__code__endswith="_AC")
            )
            .exclude(department__isnull=True)
        )
        if dean_department_ids:
            area_chair_roles = area_chair_roles.filter(department_id__in=dean_department_ids)
        department_ids = []
        for assignment in area_chair_roles.only("department_id", "tenant_id", "campus_id"):
            department_ids.extend(
                ScopeService.expand_department_ids(
                    [assignment.department_id],
                    tenant_id=assignment.tenant_id,
                    campus_id=assignment.campus_id,
                )
            )
        return sorted(set(department_ids))

    @staticmethod
    def scoped_grading_templates(request):
        tenants = list(AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True))
        queryset = (
            GradingTemplate.objects.filter(tenant_id__in=tenants)
            .select_related("tenant", "published_by")
            .prefetch_related("visible_departments")
            .order_by("tenant__name", "name")
        )
        queryset = GradingTemplateAccessService.filter_queryset_for_user(
            request.user,
            queryset,
            tenant_ids=tenants,
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_grading_templates(request):
        tenants = list(AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True))
        queryset = (
            GradingTemplate.objects.filter(tenant_id__in=tenants)
            .select_related("tenant", "published_by")
            .prefetch_related("visible_departments")
            .order_by("tenant__name", "name")
        )
        return GradingTemplateAccessService.filter_queryset_for_user(
            request.user,
            queryset,
            tenant_ids=tenants,
        )

    @staticmethod
    def scoped_template_periods(request):
        templates = AdminScopeService.scoped_grading_templates(request).values_list("id", flat=True)
        queryset = (
            GradingTemplatePeriod.objects.filter(template_id__in=templates)
            .select_related("template", "template__tenant")
            .order_by("template__name", "sequence_no")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_template_periods(request):
        templates = AdminScopeService.maintenance_scoped_grading_templates(request).values_list("id", flat=True)
        return (
            GradingTemplatePeriod.objects.filter(template_id__in=templates)
            .select_related("template", "template__tenant")
            .order_by("template__name", "sequence_no")
        )

    @staticmethod
    def scoped_template_components(request):
        periods = AdminScopeService.scoped_template_periods(request).values_list("id", flat=True)
        queryset = (
            GradingTemplateComponent.objects.filter(template_period_id__in=periods)
            .select_related("template_period", "template_period__template", "template_period__template__tenant")
            .order_by("template_period__template__name", "template_period__sequence_no", "sort_order")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_template_components(request):
        periods = AdminScopeService.maintenance_scoped_template_periods(request).values_list("id", flat=True)
        return (
            GradingTemplateComponent.objects.filter(template_period_id__in=periods)
            .select_related("template_period", "template_period__template", "template_period__template__tenant")
            .order_by("template_period__template__name", "template_period__sequence_no", "sort_order")
        )

    @staticmethod
    def scoped_template_subcomponents(request):
        components = AdminScopeService.scoped_template_components(request).values_list("id", flat=True)
        queryset = (
            GradingTemplateSubcomponent.objects.filter(template_component_id__in=components)
            .select_related(
                "template_component",
                "template_component__template_period",
                "template_component__template_period__template",
                "template_component__template_period__template__tenant",
            )
            .order_by(
                "template_component__template_period__template__name",
                "template_component__template_period__sequence_no",
                "template_component__sort_order",
                "sort_order",
            )
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_template_subcomponents(request):
        components = AdminScopeService.maintenance_scoped_template_components(request).values_list("id", flat=True)
        return (
            GradingTemplateSubcomponent.objects.filter(template_component_id__in=components)
            .select_related(
                "template_component",
                "template_component__template_period",
                "template_component__template_period__template",
                "template_component__template_period__template__tenant",
            )
            .order_by(
                "template_component__template_period__template__name",
                "template_component__template_period__sequence_no",
                "template_component__sort_order",
                "sort_order",
            )
        )

    @staticmethod
    def scoped_template_details(request):
        subcomponents = AdminScopeService.scoped_template_subcomponents(request).values_list("id", flat=True)
        queryset = (
            GradingTemplateDetail.objects.filter(template_subcomponent_id__in=subcomponents)
            .select_related(
                "template_subcomponent",
                "template_subcomponent__template_component",
                "template_subcomponent__template_component__template_period",
                "template_subcomponent__template_component__template_period__template",
                "template_subcomponent__template_component__template_period__template__tenant",
            )
            .order_by(
                "template_subcomponent__template_component__template_period__template__name",
                "template_subcomponent__template_component__template_period__sequence_no",
                "template_subcomponent__template_component__sort_order",
                "template_subcomponent__sort_order",
                "sort_order",
            )
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_template_details(request):
        subcomponents = AdminScopeService.maintenance_scoped_template_subcomponents(request).values_list("id", flat=True)
        return (
            GradingTemplateDetail.objects.filter(template_subcomponent_id__in=subcomponents)
            .select_related(
                "template_subcomponent",
                "template_subcomponent__template_component",
                "template_subcomponent__template_component__template_period",
                "template_subcomponent__template_component__template_period__template",
                "template_subcomponent__template_component__template_period__template__tenant",
            )
            .order_by(
                "template_subcomponent__template_component__template_period__template__name",
                "template_subcomponent__template_component__template_period__sequence_no",
                "template_subcomponent__template_component__sort_order",
                "template_subcomponent__sort_order",
                "sort_order",
            )
        )

    @staticmethod
    def scoped_course_template_assignments(request):
        courses = AdminScopeService.active_scoped_courses(request).values_list("id", flat=True)
        templates = AdminScopeService.scoped_grading_templates(request).values_list("id", flat=True)
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        queryset = (
            CourseTemplateAssignment.objects.filter(course_id__in=courses)
            .filter(models.Q(grading_template_id__in=templates) | models.Q(grading_template__isnull=True))
            .filter(models.Q(effective_from_term_id__in=terms) | models.Q(effective_from_term__isnull=True))
            .select_related("course", "grading_template", "effective_from_term", "course__tenant")
            .order_by("-created_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_course_template_assignments(request):
        courses = AdminScopeService.active_scoped_courses(request).values_list("id", flat=True)
        templates = AdminScopeService.maintenance_scoped_grading_templates(request).values_list("id", flat=True)
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        return (
            CourseTemplateAssignment.objects.filter(course_id__in=courses)
            .filter(models.Q(grading_template_id__in=templates) | models.Q(grading_template__isnull=True))
            .filter(models.Q(effective_from_term_id__in=terms) | models.Q(effective_from_term__isnull=True))
            .select_related("course", "grading_template", "effective_from_term", "course__tenant")
            .order_by("-created_at")
        )

    @staticmethod
    def scoped_course_base_value_overrides(request):
        courses = AdminScopeService.active_scoped_courses(request).values_list("id", flat=True)
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        queryset = (
            CourseBaseValueOverride.objects.filter(course_id__in=courses)
            .filter(models.Q(effective_from_term_id__in=terms) | models.Q(effective_from_term__isnull=True))
            .select_related("course", "effective_from_term", "course__tenant")
            .order_by("-created_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_course_base_value_overrides(request):
        courses = AdminScopeService.active_scoped_courses(request).values_list("id", flat=True)
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        return (
            CourseBaseValueOverride.objects.filter(course_id__in=courses)
            .filter(models.Q(effective_from_term_id__in=terms) | models.Q(effective_from_term__isnull=True))
            .select_related("course", "effective_from_term", "course__tenant")
            .order_by("-created_at")
        )

    @staticmethod
    def scoped_grading_period_locks(request):
        tenants = AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True)
        campuses = AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True)
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        offerings = AdminScopeService.scoped_course_offerings(request).values_list("id", flat=True)
        queryset = (
            GradingPeriodLock.objects.filter(tenant_id__in=tenants, campus_id__in=campuses, term_id__in=terms)
            .filter(models.Q(course_offering_id__in=offerings) | models.Q(course_offering__isnull=True))
            .select_related("tenant", "campus", "academic_year", "term", "course_offering", "locked_by_user")
            .order_by("-updated_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_grading_period_locks(request):
        tenants = AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True)
        campuses = AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True)
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        offerings = AdminScopeService.scoped_course_offerings(request).values_list("id", flat=True)
        return (
            GradingPeriodLock.objects.filter(tenant_id__in=tenants, campus_id__in=campuses, term_id__in=terms)
            .filter(models.Q(course_offering_id__in=offerings) | models.Q(course_offering__isnull=True))
            .select_related("tenant", "campus", "academic_year", "term", "course_offering", "locked_by_user")
            .order_by("-updated_at")
        )

    @staticmethod
    def scoped_grade_encoding_controls(request):
        tenants = AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True)
        campuses = AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True)
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        offerings = AdminScopeService.scoped_course_offerings(request).values_list("id", flat=True)
        queryset = (
            GradeEncodingControl.objects.filter(tenant_id__in=tenants, term_id__in=terms)
            .filter(models.Q(campus_id__in=campuses) | models.Q(campus__isnull=True))
            .filter(models.Q(course_offering_id__in=offerings) | models.Q(course_offering__isnull=True))
            .select_related(
                "tenant",
                "academic_year",
                "term",
                "campus",
                "course_offering__course",
                "course_offering__section",
                "created_by",
                "updated_by",
            )
            .order_by("-updated_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_grade_encoding_controls(request):
        return AdminScopeService.scoped_grade_encoding_controls(request)

    @staticmethod
    def scoped_grade_submissions(request):
        offerings = AdminScopeService.scoped_monitoring_course_offerings(request).values_list("id", flat=True)
        queryset = (
            GradeSubmission.objects.filter(offering_id__in=offerings)
            .select_related(
                "tenant",
                "campus",
                "offering",
                "offering__course",
                "offering__section",
                "offering__term",
                "template_period",
            )
            .prefetch_related("reopen_requests")
            .order_by("-updated_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_grade_correction_requests(request):
        offerings = AdminScopeService.scoped_monitoring_course_offerings(request).values_list("id", flat=True)
        queryset = (
            GradeCorrectionRequest.objects.filter(offering_id__in=offerings)
            .select_related(
                "tenant",
                "campus",
                "offering",
                "offering__course",
                "offering__section",
                "offering__term",
                "template_period",
                "requested_by_user",
                "initiated_by_user",
                "reviewed_by_user",
                "faculty_department",
                "approval_route",
            )
            .prefetch_related("items", "attachments", "approval_steps", "approval_steps__approver_role")
            .order_by("-created_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_grade_submission_reopen_requests(request):
        offerings = AdminScopeService.scoped_monitoring_course_offerings(request).values_list("id", flat=True)
        queryset = (
            GradeSubmissionReopenRequest.objects.filter(offering_id__in=offerings)
            .select_related(
                "tenant",
                "campus",
                "submission",
                "offering",
                "offering__course",
                "offering__section",
                "offering__term",
                "template_period",
                "requested_by_user",
                "reviewed_by_user",
            )
            .order_by("-created_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)


    @staticmethod
    def scoped_tenant_grading_profiles(request):
        tenants = AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True)
        campuses = AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True)
        departments = AdminScopeService.active_scoped_departments(request).values_list("id", flat=True)
        programs = AdminScopeService.active_scoped_programs(request).values_list("id", flat=True)
        courses = AdminScopeService.active_scoped_courses(request).values_list("id", flat=True)
        templates = AdminScopeService.scoped_grading_templates(request).values_list("id", flat=True)
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        queryset = (
            TenantGradingProfile.objects.filter(tenant_id__in=tenants)
            .filter(models.Q(campus_id__in=campuses) | models.Q(campus__isnull=True))
            .filter(models.Q(department_id__in=departments) | models.Q(department__isnull=True))
            .filter(models.Q(program_id__in=programs) | models.Q(program__isnull=True))
            .filter(models.Q(course_id__in=courses) | models.Q(course__isnull=True))
            .filter(grading_template_id__in=templates)
            .filter(models.Q(effective_from_term_id__in=terms) | models.Q(effective_from_term__isnull=True))
            .select_related(
                "tenant",
                "campus",
                "department",
                "program",
                "course",
                "grading_template",
                "effective_from_term",
            )
            .order_by("tenant__name", "priority", "profile_code")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def maintenance_scoped_tenant_grading_profiles(request):
        tenants = AdminScopeService.active_scoped_tenants(request).values_list("id", flat=True)
        campuses = AdminScopeService.active_scoped_campuses(request).values_list("id", flat=True)
        departments = AdminScopeService.active_scoped_departments(request).values_list("id", flat=True)
        programs = AdminScopeService.active_scoped_programs(request).values_list("id", flat=True)
        courses = AdminScopeService.active_scoped_courses(request).values_list("id", flat=True)
        templates = AdminScopeService.maintenance_scoped_grading_templates(request).values_list("id", flat=True)
        terms = AdminScopeService.active_scoped_terms(request).values_list("id", flat=True)
        return (
            TenantGradingProfile.objects.filter(tenant_id__in=tenants)
            .filter(models.Q(campus_id__in=campuses) | models.Q(campus__isnull=True))
            .filter(models.Q(department_id__in=departments) | models.Q(department__isnull=True))
            .filter(models.Q(program_id__in=programs) | models.Q(program__isnull=True))
            .filter(models.Q(course_id__in=courses) | models.Q(course__isnull=True))
            .filter(grading_template_id__in=templates)
            .filter(models.Q(effective_from_term_id__in=terms) | models.Q(effective_from_term__isnull=True))
            .select_related(
                "tenant",
                "campus",
                "department",
                "program",
                "course",
                "grading_template",
                "effective_from_term",
            )
            .order_by("tenant__name", "priority", "profile_code")
        )

    @staticmethod
    def scoped_template_hotfix_requests(request):
        templates = AdminScopeService.scoped_grading_templates(request).values_list("id", flat=True)
        queryset = (
            TemplateHotfixRequest.objects.filter(template_id__in=templates)
            .select_related("tenant", "template", "requested_by_user", "reviewed_by_user", "applied_by_user")
            .order_by("-created_at")
        )
        return AdminScopeService._visible_queryset(request, queryset)

    @staticmethod
    def scoped_import_batches(request):
        queryset = (
            ImportBatch.objects.select_related("uploaded_by_user", "confirmed_by_user", "tenant", "campus")
            .prefetch_related("rows")
            .order_by("-created_at")
        )
        if request.user.is_superuser:
            return queryset
        tenant_ids = getattr(request, "scope", {}).get("tenant_ids", [])
        campus_ids = getattr(request, "scope", {}).get("campus_ids", [])
        return queryset.filter(
            (models.Q(tenant_id__in=tenant_ids) | models.Q(tenant__isnull=True))
            & (models.Q(campus_id__in=campus_ids) | models.Q(campus__isnull=True))
        )


def model_before_after(instance, extra_fields=None):
    fields = [f.name for f in instance._meta.fields]
    payload = model_to_dict(instance, fields=fields)
    if extra_fields:
        payload.update(extra_fields)
    return payload
