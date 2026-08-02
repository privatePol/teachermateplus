"""Shared fixtures for Stage 4 tests; these are intentionally execution-neutral."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.test import TestCase, TransactionTestCase

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant

from .models import CourseExamConfiguration, CycleCourse, CycleCourseOffering, ExaminationCycle


_DEFAULT_DEPARTMENT = object()


class Stage4TestCase(TestCase):
    """Minimal tenant/campus/responsibility fixture for actual Stage 4 writers."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="S4", name="Stage 4 Tenant")
        cls.other_tenant = Tenant.objects.create(code="S4O", name="Other Stage 4 Tenant")
        cls.campus = Campus.objects.create(tenant=cls.tenant, code="MAIN", name="Main")
        cls.other_campus = Campus.objects.create(tenant=cls.tenant, code="NORTH", name="North")
        cls.department = Department.objects.create(tenant=cls.tenant, campus=cls.campus, code="EXAM", name="Exam")
        cls.other_department = Department.objects.create(tenant=cls.tenant, campus=cls.other_campus, code="OTHER", name="Other")
        cls.year = AcademicYear.objects.create(tenant=cls.tenant, code="AY", name="AY", start_date="2026-06-01", end_date="2027-05-31")
        cls.term = Term.objects.create(tenant=cls.tenant, academic_year=cls.year, code="T1", name="Term 1")
        cls.admin = get_user_model().objects.create_superuser(
            "stage4-admin", "stage4-admin@example.edu", "Pass123!", default_tenant=cls.tenant,
            default_campus=cls.campus, privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"), privacy_consent_at=timezone.now(),
        )
        for code, action in (
            ("admin_portal.access", "access"),
            ("departmental_exams.manage_cycles", "manage_cycles"),
            ("departmental_exams.configure", "configure"),
            ("departmental_exams.review_generate", "review_generate"),
        ):
            Permission.objects.get_or_create(code=code, defaults={"module": code.rsplit(".", 1)[0], "action": action, "description": code, "is_active": True})

    def setUp(self):
        SystemSettingService.set("FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED", True, tenant_id=self.tenant.id, value_type="BOOL")
        self.configurer = self.make_user("configurer", self.department, ("admin_portal.access", "departmental_exams.configure"))
        self.manager = self.make_user("manager", self.department, ("admin_portal.access", "departmental_exams.manage_cycles"))
        self.reviewer = self.make_user("reviewer", self.department, ("admin_portal.access", "departmental_exams.review_generate"))

    def make_user(self, username, department, permissions, *, campus=None):
        campus = campus or (department.campus if department else self.campus)
        user = get_user_model().objects.create_user(
            username, f"{username}@example.edu", "Pass123!", default_tenant=self.tenant, default_campus=campus,
            default_department=department, privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"), privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code=f"S4_{username}".upper(), name=username)
        for code in permissions:
            RolePermission.objects.create(role=role, permission=Permission.objects.get(code=code))
        UserRole.objects.create(user=user, role=role, tenant=self.tenant, campus=campus, department=department)
        return user

    def make_cycle(
        self,
        *,
        status=ExaminationCycle.Status.DRAFT,
        default_questions_required_per_faculty=None,
        default_final_item_count=None,
        default_contribution_deadline=None,
        instructions="",
        scope_suffix=None,
    ):
        academic_year = self.year
        term = self.term
        if scope_suffix is not None:
            academic_year = AcademicYear.objects.create(
                tenant=self.tenant,
                code=f"AY-{scope_suffix}",
                name=f"AY {scope_suffix}",
                start_date="2026-06-01",
                end_date="2027-05-31",
            )
            term = Term.objects.create(
                tenant=self.tenant,
                academic_year=academic_year,
                code=f"T1-{scope_suffix}",
                name=f"Term 1 {scope_suffix}",
            )
        return ExaminationCycle.objects.create(
            tenant=self.tenant, academic_year=academic_year, term=term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM, status=status,
            default_questions_required_per_faculty=default_questions_required_per_faculty,
            default_final_item_count=default_final_item_count,
            default_contribution_deadline=default_contribution_deadline,
            contributor_instructions=instructions, created_by=self.admin,
        )

    def make_course(self, *, cycle=None, department=_DEFAULT_DEPARTMENT, code="S4-101"):
        cycle = cycle or self.make_cycle()
        department = self.department if department is _DEFAULT_DEPARTMENT else department
        course = Course.objects.create(tenant=self.tenant, code=f"{code}-{Course.objects.count()}", title="Stage 4 Course", exam_department=department)
        program = Program.objects.create(tenant=self.tenant, campus=self.campus, department=self.department, code=f"P{course.id}", name=f"Program {course.id}")
        section = Section.objects.create(tenant=self.tenant, campus=self.campus, department=self.department, program=program, code=f"S{course.id}", name=f"Section {course.id}")
        offering = CourseOffering.objects.create(tenant=self.tenant, campus=self.campus, department=self.department, program=program, academic_year=cycle.academic_year, term=cycle.term, course=course, section=section)
        parent = CycleCourse.objects.create(cycle=cycle, course=course, responsible_department=department)
        CycleCourseOffering.objects.create(cycle_course=parent, offering=offering, campus=self.campus)
        return parent

    def future_deadline(self):
        return timezone.now() + timezone.timedelta(days=7)

    def make_configuration(
        self,
        parent,
        *,
        quota=50,
        final_count=50,
        quota_source="OVERRIDE",
        final_source="OVERRIDE",
        workflow=CourseExamConfiguration.WorkflowStatus.DRAFT,
        opened_at=None,
        coverage="Core outcomes",
        deadline=None,
        deadline_source="OVERRIDE",
    ):
        return CourseExamConfiguration.objects.create(
            cycle_course=parent,
            questions_required_per_faculty=quota,
            questions_required_per_faculty_source=quota_source,
            final_item_count=final_count,
            final_item_count_source=final_source,
            cycle_defaults_revision_snapshot=parent.cycle.defaults_revision,
            workflow_status=workflow,
            opened_at=opened_at,
            opened_by=self.admin if opened_at else None,
            coverage=coverage,
            contribution_deadline=deadline or self.future_deadline(),
            contribution_deadline_source=deadline_source,
        )


class Stage4TransactionTestCase(TransactionTestCase):
    """Same fixture contract for tests that require real transaction boundaries."""

    make_user = Stage4TestCase.make_user
    make_cycle = Stage4TestCase.make_cycle
    make_course = Stage4TestCase.make_course
    future_deadline = Stage4TestCase.future_deadline
    make_configuration = Stage4TestCase.make_configuration

    def setUp(self):
        super().setUp()
        Stage4TestCase.setUpTestData.__func__(type(self))
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.configurer = self.make_user(
            "configurer", self.department,
            ("admin_portal.access", "departmental_exams.configure"),
        )
        self.manager = self.make_user(
            "manager", self.department,
            ("admin_portal.access", "departmental_exams.manage_cycles"),
        )
        self.reviewer = self.make_user(
            "reviewer", self.department,
            ("admin_portal.access", "departmental_exams.review_generate"),
        )
