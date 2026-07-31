from unittest.mock import patch

from django.conf import settings
from django.contrib.messages import get_messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.admin_portal.forms import CourseForm
from apps.auditlog.models import AuditLog
from apps.core.services.menu import MenuService
from apps.core.services.permissions import PermissionService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant

from .forms import ExaminationCycleForm
from .models import CycleCourse, CycleCourseOffering, ExaminationCycle
from .services import DepartmentalExamAuthorizationService, ExaminationCycleService
from .views import cycle_course_administration_view


class CycleCourseAdministrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="DE-TEST", name="Departmental Exam Test")
        cls.other_tenant = Tenant.objects.create(code="DE-OTHER", name="Other Tenant")
        cls.campus_a = Campus.objects.create(tenant=cls.tenant, code="A", name="Campus A")
        cls.campus_b = Campus.objects.create(tenant=cls.tenant, code="B", name="Campus B")
        cls.department_a = Department.objects.create(
            tenant=cls.tenant, campus=cls.campus_a, code="BUS", name="Business"
        )
        cls.department_b = Department.objects.create(
            tenant=cls.tenant, campus=cls.campus_b, code="SCI", name="Science"
        )
        cls.year = AcademicYear.objects.create(
            tenant=cls.tenant,
            code="AY",
            name="AY",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        cls.term = Term.objects.create(
            tenant=cls.tenant, academic_year=cls.year, code="T1", name="Term 1"
        )
        cls.other_year = AcademicYear.objects.create(
            tenant=cls.other_tenant,
            code="OAY",
            name="Other AY",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        cls.other_term = Term.objects.create(
            tenant=cls.other_tenant,
            academic_year=cls.other_year,
            code="OT",
            name="Other Term",
        )
        cls.course = Course.objects.create(
            tenant=cls.tenant,
            code="EX101",
            title="Exam Course",
            exam_department=cls.department_a,
        )
        cls.admin = get_user_model().objects.create_superuser(
            "exam-admin",
            "exam-admin@example.edu",
            "Pass123!",
            default_tenant=cls.tenant,
            default_campus=cls.campus_a,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        permission_specs = {
            "admin_portal.access": ("admin_portal", "access"),
            "faculty_portal.access": ("faculty_portal", "access"),
            "departmental_exams.manage_cycles": ("departmental_exams", "manage_cycles"),
            "departmental_exams.configure": ("departmental_exams", "configure"),
            "departmental_exams.review_generate": ("departmental_exams", "review_generate"),
            "courses.update": ("courses", "update"),
        }
        for code, (module, action) in permission_specs.items():
            Permission.objects.get_or_create(
                code=code,
                defaults={
                    "module": module,
                    "action": action,
                    "description": code,
                    "is_active": True,
                },
            )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=cls.tenant.id,
            value_type="BOOL",
        )

    def _offering(self, *, course=None, campus=None, department=None, suffix="1"):
        course = course or self.course
        campus = campus or self.campus_a
        department = department or self.department_a
        program = Program.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            code=f"P{course.id}{suffix}",
            name=f"Program {suffix}",
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            code=f"S{course.id}{suffix}",
            name=f"Section {suffix}",
        )
        return CourseOffering.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=self.year,
            term=self.term,
            course=course,
            section=section,
        )

    def _create_cycle(self):
        return ExaminationCycleService.create_cycle(
            user=self.admin,
            tenant=self.tenant,
            academic_year=self.year,
            term=self.term,
            exam_period="MIDTERM",
        )

    def _bulk_offering_dataset(self, *, count):
        program_a = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
            code="BULK-A",
            name="Bulk Program A",
        )
        program_b = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus_b,
            department=self.department_b,
            code="BULK-B",
            name="Bulk Program B",
        )
        section_a = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
            program=program_a,
            code="BULK-A",
            name="Bulk Section A",
        )
        section_b = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus_b,
            department=self.department_b,
            program=program_b,
            code="BULK-B",
            name="Bulk Section B",
        )
        Course.objects.bulk_create(
            [
                Course(
                    tenant=self.tenant,
                    code=f"BATCH{number:03d}",
                    title=f"Batch Course {number:03d}",
                    exam_department=(
                        self.department_a if number % 2 == 0 else self.department_b
                    ),
                )
                for number in range(count)
            ],
            batch_size=ExaminationCycleService.SNAPSHOT_BATCH_SIZE,
        )
        courses = list(
            Course.objects.filter(
                tenant=self.tenant,
                code__startswith="BATCH",
            ).order_by("code")
        )
        CourseOffering.objects.bulk_create(
            [
                CourseOffering(
                    tenant=self.tenant,
                    campus=self.campus_b if number % 2 == 0 else self.campus_a,
                    department=(
                        self.department_b if number % 2 == 0 else self.department_a
                    ),
                    program=program_b if number % 2 == 0 else program_a,
                    academic_year=self.year,
                    term=self.term,
                    course=course,
                    section=section_b if number % 2 == 0 else section_a,
                )
                for number, course in enumerate(courses)
            ],
            batch_size=ExaminationCycleService.SNAPSHOT_BATCH_SIZE,
        )
        return courses

    def _grouped_course(self, *, cross_campus=False):
        self._offering(suffix="1")
        if cross_campus:
            self._offering(
                campus=self.campus_b,
                department=self.department_b,
                suffix="2",
            )
        cycle = self._create_cycle()
        return cycle, CycleCourse.objects.get(cycle=cycle, course=self.course)

    def _scoped_user(
        self,
        *,
        username,
        tenant=None,
        department=None,
        permissions=(),
        role_active=True,
        membership_active=True,
    ):
        tenant = tenant or self.tenant
        campus = department.campus if department else self.campus_a
        user = get_user_model().objects.create_user(
            username,
            f"{username}@example.edu",
            "Pass123!",
            default_tenant=tenant,
            default_campus=campus,
            default_department=department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(
            code=f"DE_TEST_{username.upper()}",
            name=f"Departmental Exam {username}",
            is_active=role_active,
        )
        for permission in permissions:
            RolePermission.objects.create(
                role=role, permission=Permission.objects.get(code=permission)
            )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=tenant,
            campus=campus,
            department=department,
            is_active=membership_active,
        )
        return user

    def _reviewer(self, *, username, department=None, **kwargs):
        return self._scoped_user(
            username=username,
            department=department or self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            ),
            **kwargs,
        )

    def _configurer(self, *, username, department=None, permissions=()):
        return self._scoped_user(
            username=username,
            department=department or self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.configure",
                *permissions,
            ),
        )

    def _department_hierarchy(self):
        parent = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            code="BUS-PARENT",
            name="Business Parent",
        )
        self.department_a.parent = parent
        self.department_a.save(update_fields=["parent", "updated_at"])
        child = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            parent=self.department_a,
            code="BUS-CHILD",
            name="Business Child",
        )
        return parent, child

    def test_exam_department_is_nullable_and_independent_from_ordinary_departments(self):
        self.assertEqual(self.course.exam_department_id, self.department_a.id)
        self.assertIsNone(self.course.department_id)
        offering = self._offering(department=self.department_b)
        self.assertEqual(offering.department_id, self.department_b.id)
        self.assertEqual(self.course.exam_department_id, self.department_a.id)

        self.course.exam_department = None
        self.course.full_clean()

        other_campus = Campus.objects.create(
            tenant=self.other_tenant, code="OTHER", name="Other Campus"
        )
        other_department = Department.objects.create(
            tenant=self.other_tenant,
            campus=other_campus,
            code="OTHER",
            name="Other Department",
        )
        self.course.exam_department = other_department
        with self.assertRaises(ValidationError):
            self.course.full_clean()

    def test_course_form_allows_scoped_exam_department_without_ordinary_department(self):
        form = CourseForm(
            data={
                "tenant": self.tenant.id,
                "campus": "",
                "department": "",
                "exam_department": self.department_a.id,
                "code": "EX104",
                "title": "Form-owned Exam Course",
                "units": "",
                "course_type": "",
                "default_base_value": "",
                "syllabus_url": "",
                "is_active": "on",
            },
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(tenant=self.tenant),
            department_queryset=Department.objects.filter(tenant=self.tenant),
        )
        self.assertTrue(form.is_valid(), form.errors)
        course = form.save()
        self.assertIsNone(course.department_id)
        self.assertEqual(course.exam_department_id, self.department_a.id)

    def test_course_update_rejects_cross_tenant_exam_department_post(self):
        other_campus = Campus.objects.create(
            tenant=self.other_tenant, code="OTHER", name="Other Campus"
        )
        other_department = Department.objects.create(
            tenant=self.other_tenant,
            campus=other_campus,
            code="OTHER",
            name="Other Department",
        )
        self.client.force_login(self.admin)
        original_title = self.course.title
        audit_queryset = AuditLog.objects.filter(
            action="UPDATE",
            entity_type="Course",
            entity_id=str(self.course.id),
        )
        audit_count = audit_queryset.count()

        response = self.client.post(
            reverse("admin_portal:course_update", args=[self.course.id]),
            {
                "tenant": self.tenant.id,
                "campus": "",
                "department": "",
                "exam_department": other_department.id,
                "code": self.course.code,
                "title": "SHOULD NOT PERSIST",
                "units": "",
                "course_type": "",
                "default_base_value": "",
                "syllabus_url": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Exam department does not belong to selected tenant.",
            response.context["form"].errors["exam_department"],
        )
        self.course.refresh_from_db()
        self.assertEqual(self.course.exam_department_id, self.department_a.id)
        self.assertEqual(self.course.title, original_title)
        self.assertEqual(audit_queryset.count(), audit_count)
        self.assertNotIn(
            "Course updated.",
            [message.message for message in get_messages(response.wsgi_request)],
        )

    def test_cycle_copies_exam_department_and_offering_changes_do_not_change_snapshot(self):
        offering = self._offering(department=self.department_b)
        cycle = self._create_cycle()
        cycle_course = CycleCourse.objects.get(cycle=cycle, course=self.course)
        self.assertEqual(cycle_course.responsible_department_id, self.department_a.id)

        self.course.exam_department = self.department_b
        self.course.save(update_fields=["exam_department", "updated_at"])
        offering.department = self.department_a
        offering.save(update_fields=["department", "updated_at"])
        cycle_course.refresh_from_db()
        self.assertEqual(cycle_course.responsible_department_id, self.department_a.id)

    def test_grouping_is_by_course_and_preserves_offering_campuses(self):
        first = self._offering(suffix="1")
        second = self._offering(
            campus=self.campus_b,
            department=self.department_b,
            suffix="2",
        )
        cycle = self._create_cycle()
        cycle_course = CycleCourse.objects.get(cycle=cycle, course=self.course)
        self.assertEqual(CycleCourse.objects.filter(cycle=cycle).count(), 1)
        self.assertEqual(cycle_course.responsible_department_id, self.department_a.id)
        self.assertEqual(
            set(cycle_course.offering_snapshots.values_list("offering_id", flat=True)),
            {first.id, second.id},
        )
        self.assertEqual(
            set(cycle_course.offering_snapshots.values_list("campus_id", flat=True)),
            {self.campus_a.id, self.campus_b.id},
        )

    def test_missing_exam_department_fails_closed_until_an_admin_assigns_one(self):
        self.course.exam_department = None
        self.course.save(update_fields=["exam_department", "updated_at"])
        _, cycle_course = self._grouped_course()
        self.assertIsNone(cycle_course.responsible_department_id)
        url = reverse("departmental_exams:cycle_course_administration", args=[cycle_course.id])
        reviewer = self._reviewer(username="missing-department-reviewer")

        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertContains(response, "Needs Exam Department")
        response = self.client.post(url, {"reviewer_id": reviewer.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select an exam department")
        cycle_course.refresh_from_db()
        self.assertIsNone(cycle_course.reviewer_id)

        response = self.client.post(
            url,
            {"responsible_department": self.department_a.id, "reviewer_id": reviewer.id},
        )
        self.assertRedirects(response, url)
        cycle_course.refresh_from_db()
        self.assertEqual(cycle_course.responsible_department_id, self.department_a.id)
        self.assertEqual(cycle_course.reviewer_id, reviewer.id)

    def test_cycle_form_rejects_cross_tenant_term(self):
        form = ExaminationCycleForm(
            data={
                "academic_year": self.year.id,
                "term": self.other_term.id,
                "exam_period": "MIDTERM",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("term", form.errors)

    def test_central_cycle_manager_can_access_cycle_operations_and_unprivileged_user_cannot(self):
        cycle_manager = self._scoped_user(
            username="central-cycle-manager",
            department=None,
            permissions=("admin_portal.access", "departmental_exams.manage_cycles"),
        )
        cycle_list_url = reverse("departmental_exams:cycle_list")
        cycle_create_url = reverse("departmental_exams:cycle_create")

        self.client.force_login(cycle_manager)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 200)
        self.assertEqual(self.client.get(cycle_create_url).status_code, 200)
        response = self.client.post(
            cycle_create_url,
            {
                "academic_year": self.year.id,
                "term": self.term.id,
                "exam_period": "MIDTERM",
            },
        )
        self.assertRedirects(response, cycle_list_url)

        unprivileged_user = self._scoped_user(
            username="no-cycle-management",
            department=None,
            permissions=("admin_portal.access",),
        )
        self.client.force_login(unprivileged_user)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
        self.assertEqual(self.client.get(cycle_create_url).status_code, 403)

    def test_cycle_course_list_is_feature_gated_and_shows_snapshot_summary(self):
        self._offering(suffix="1")
        self._offering(suffix="2")
        cycle = self._create_cycle()
        cycle_course = CycleCourse.objects.get(cycle=cycle, course=self.course)
        url = reverse("departmental_exams:cycle_course_list", args=[cycle.id])
        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.campus_a.name)
        self.assertContains(response, "2")
        self.assertContains(
            response,
            reverse("departmental_exams:cycle_course_administration", args=[cycle_course.id]),
        )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("departmental_exams:cycle_list")).status_code, 403
        )

    def test_review_generate_only_reviewer_list_is_limited_to_explicit_assignment(self):
        self._offering(suffix="1")
        other_course = Course.objects.create(
            tenant=self.tenant,
            code="EX102",
            title="Other Course",
            exam_department=self.department_a,
        )
        self._offering(course=other_course, suffix="2")
        cycle = self._create_cycle()
        assigned = CycleCourse.objects.get(cycle=cycle, course=self.course)
        other = CycleCourse.objects.get(cycle=cycle, course=other_course)
        reviewer = self._scoped_user(
            username="list-reviewer",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.review_generate",
            ),
        )
        assigned.reviewer = reviewer
        assigned.save(update_fields=["reviewer", "updated_at"])

        self.client.force_login(reviewer)
        response = self.client.get(
            reverse("departmental_exams:cycle_course_list", args=[cycle.id])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.course.code)
        self.assertNotContains(response, other.course.code)
        self.assertNotContains(
            response,
            reverse("departmental_exams:cycle_course_administration", args=[assigned.id]),
        )

    def test_routed_reviewer_list_revalidates_exact_current_assignment_scope(self):
        parent, child = self._department_hierarchy()
        self._offering(suffix="routed-list-a")
        self._offering(
            campus=self.campus_b,
            department=self.department_b,
            suffix="routed-list-b",
        )
        cycle = self._create_cycle()
        cycle_course = CycleCourse.objects.get(cycle=cycle, course=self.course)
        reviewer = self._scoped_user(
            username="routed-list-reviewer",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.review_generate",
            ),
        )
        membership = UserRole.objects.get(user=reviewer, role__code="DE_TEST_ROUTED-LIST-REVIEWER")
        role = membership.role
        review_permission = Permission.objects.get(code="departmental_exams.review_generate")
        cycle_course.reviewer = reviewer
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        url = reverse("departmental_exams:cycle_course_list", args=[cycle.id])

        self.client.force_login(reviewer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row.id for row in response.context["courses"]}, {cycle_course.id})
        self.assertTrue(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=reviewer,
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            )
        )
        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=reviewer,
            cycle_course=cycle_course,
        )

        def assert_denied():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)
            self.assertFalse(
                DepartmentalExamAuthorizationService.reviewer_visible_cycle_courses(
                    user=reviewer,
                    cycle=cycle,
                ).filter(id=cycle_course.id).exists()
            )

        membership.department = parent
        membership.save(update_fields=["department"])
        assert_denied()

        cycle_course.responsible_department = parent
        cycle_course.save(update_fields=["responsible_department", "updated_at"])
        membership.department = child
        membership.save(update_fields=["department"])
        assert_denied()

        cycle_course.responsible_department = self.department_a
        cycle_course.save(update_fields=["responsible_department", "updated_at"])
        membership.department = self.department_b
        membership.campus = self.campus_b
        membership.save(update_fields=["department", "campus"])
        assert_denied()

        membership.department = None
        membership.campus = self.campus_a
        membership.save(update_fields=["department", "campus"])
        assert_denied()

        membership.department = self.department_a
        membership.tenant = self.other_tenant
        membership.save(update_fields=["department", "tenant"])
        assert_denied()

        membership.tenant = self.tenant
        membership.campus = self.campus_b
        membership.save(update_fields=["tenant", "campus"])
        assert_denied()

        membership.campus = self.campus_a
        membership.save(update_fields=["campus"])
        UserPermission.objects.create(
            user=reviewer,
            permission=review_permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus_a,
        )
        assert_denied()
        UserPermission.objects.filter(
            user=reviewer,
            permission=review_permission,
            grant_type=UserPermission.GrantType.DENY,
        ).delete()

        membership.is_active = False
        membership.save(update_fields=["is_active"])
        assert_denied()
        membership.is_active = True
        membership.save(update_fields=["is_active"])

        role.is_active = False
        role.save(update_fields=["is_active"])
        assert_denied()
        role.is_active = True
        role.save(update_fields=["is_active"])

        reviewer.is_active = False
        reviewer.save(update_fields=["is_active"])
        self.assertNotEqual(self.client.get(url).status_code, 200)
        reviewer.is_active = True
        reviewer.save(update_fields=["is_active"])

        cycle_course.reviewer = None
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        assert_denied()

        cycle_course.reviewer = reviewer
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        membership.delete()
        assert_denied()
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=cycle_course,
            )

    def test_null_department_role_does_not_become_unrestricted(self):
        _, cycle_course = self._grouped_course()
        tenant_wide_user = self._scoped_user(
            username="null-department-manager",
            department=None,
            permissions=("admin_portal.access", "departmental_exams.configure"),
        )
        self.client.force_login(tenant_wide_user)
        self.assertEqual(
            self.client.get(
                reverse(
                    "departmental_exams:assigned_course_examinations"
                )
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:cycle_course_administration", args=[cycle_course.id])
            ).status_code,
            403,
        )

    def test_reviewer_assignment_requires_active_role_scope_and_review_generate(self):
        _, cycle_course = self._grouped_course()
        url = reverse("departmental_exams:cycle_course_administration", args=[cycle_course.id])
        no_permission = self._scoped_user(
            username="no-review-permission",
            department=self.department_a,
            permissions=("admin_portal.access", "departmental_exams.configure"),
        )
        inactive_membership = self._reviewer(
            username="inactive-membership", membership_active=False
        )
        inactive_role = self._reviewer(username="inactive-role", role_active=False)
        inactive_user = self._reviewer(username="inactive-user")
        inactive_user.is_active = False
        inactive_user.save(update_fields=["is_active"])
        wrong_department = self._reviewer(
            username="wrong-department", department=self.department_b
        )
        other_campus = Campus.objects.create(
            tenant=self.other_tenant, code="OTHER", name="Other Campus"
        )
        other_department = Department.objects.create(
            tenant=self.other_tenant,
            campus=other_campus,
            code="OTHER",
            name="Other Department",
        )
        other_tenant = self._scoped_user(
            username="other-tenant-reviewer",
            tenant=self.other_tenant,
            department=other_department,
            permissions=(
                "admin_portal.access",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            ),
        )

        self.client.force_login(self.admin)
        for candidate in (
            no_permission,
            inactive_membership,
            inactive_role,
            inactive_user,
            wrong_department,
            other_tenant,
        ):
            response = self.client.post(
                url,
                {
                    "responsible_department": self.department_a.id,
                    "reviewer_id": candidate.id,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "review/generate permission")
            cycle_course.refresh_from_db()
            self.assertIsNone(cycle_course.reviewer_id)

    def test_reviewer_authorization_matrix_requires_exact_department_scope(self):
        parent, child = self._department_hierarchy()
        exact = self._reviewer(username="matrix-exact")
        parent_only = self._reviewer(
            username="matrix-parent",
            department=parent,
        )
        child_only = self._reviewer(
            username="matrix-child",
            department=child,
        )
        unrelated = self._reviewer(
            username="matrix-unrelated",
            department=self.department_b,
        )
        null_department = self._scoped_user(
            username="matrix-null",
            department=None,
            permissions=("departmental_exams.review_generate",),
        )
        wrong_tenant = self._reviewer(
            username="matrix-wrong-tenant",
            tenant=self.other_tenant,
            department=self.department_a,
        )
        inactive_user = self._reviewer(username="matrix-inactive-user")
        inactive_user.is_active = False
        inactive_user.save(update_fields=["is_active"])
        inactive_membership = self._reviewer(
            username="matrix-inactive-membership",
            membership_active=False,
        )
        inactive_role = self._reviewer(
            username="matrix-inactive-role",
            role_active=False,
        )
        expected = {
            exact.id: True,
            parent_only.id: False,
            child_only.id: False,
            unrelated.id: False,
            null_department.id: False,
            wrong_tenant.id: False,
            inactive_user.id: False,
            inactive_membership.id: False,
            inactive_role.id: False,
        }

        with CaptureQueriesContext(connection) as query_context:
            listed_ids = {
                candidate.id
                for candidate in DepartmentalExamAuthorizationService.eligible_reviewers(
                    tenant_id=self.tenant.id,
                    responsible_department=self.department_a,
                )
            }
        self.assertLessEqual(len(query_context), 2)
        for user_id, allowed in expected.items():
            candidate = get_user_model().objects.get(id=user_id)
            with self.subTest(username=candidate.username):
                self.assertEqual(
                    DepartmentalExamAuthorizationService.is_eligible_reviewer(
                        user=candidate,
                        tenant_id=self.tenant.id,
                        responsible_department=self.department_a,
                    ),
                    allowed,
                )
                self.assertEqual(user_id in listed_ids, allowed)

        _, cycle_course = self._grouped_course()
        url = reverse(
            "departmental_exams:cycle_course_administration",
            args=[cycle_course.id],
        )
        self.client.force_login(self.admin)
        rejected = self.client.post(
            url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": parent_only.id,
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertIn("reviewer", rejected.context["form"].errors)
        accepted = self.client.post(
            url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": exact.id,
            },
        )
        self.assertRedirects(accepted, url)
        cycle_course.refresh_from_db()
        self.assertEqual(cycle_course.reviewer_id, exact.id)

    def test_reviewer_campus_scope_preserves_grouped_cross_campus_access(self):
        _, cycle_course = self._grouped_course(cross_campus=True)
        null_campus = self._reviewer(username="null-campus-reviewer")
        UserRole.objects.filter(
            user=null_campus,
            tenant=self.tenant,
            department=self.department_a,
        ).update(campus=None)
        UserPermission.objects.create(
            user=null_campus,
            permission=Permission.objects.get(
                code="departmental_exams.review_generate"
            ),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus_a,
        )
        unrelated_campus = self._reviewer(username="unrelated-campus-reviewer")
        UserRole.objects.filter(
            user=unrelated_campus,
            tenant=self.tenant,
            department=self.department_a,
        ).update(campus=self.campus_b)

        listed_ids = set(
            DepartmentalExamAuthorizationService.eligible_reviewers(
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            ).values_list("id", flat=True)
        )
        self.assertIn(null_campus.id, listed_ids)
        self.assertNotIn(unrelated_campus.id, listed_ids)
        self.assertTrue(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=null_campus,
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            )
        )
        self.assertFalse(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=unrelated_campus,
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            )
        )

        cycle_course.reviewer = null_campus
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=null_campus,
            cycle_course=cycle_course,
        )

    def test_reviewer_effective_permission_semantics_match_candidate_and_mutation(self):
        review_permission = Permission.objects.get(
            code="departmental_exams.review_generate"
        )
        direct_allow = self._scoped_user(
            username="direct-review-allow",
            department=self.department_a,
            permissions=("admin_portal.access", "departmental_exams.configure"),
        )
        UserPermission.objects.create(
            user=direct_allow,
            permission=review_permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus_a,
        )

        denied = self._scoped_user(
            username="direct-review-deny",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.review_generate",
            ),
        )
        UserPermission.objects.create(
            user=denied,
            permission=review_permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus_a,
        )
        UserPermission.objects.create(
            user=denied,
            permission=review_permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus_a,
        )

        permission_from_another_role = self._scoped_user(
            username="review-other-role",
            department=self.department_a,
            permissions=("admin_portal.access", "departmental_exams.configure"),
        )
        permission_role = Role.objects.create(
            code="DE_TEST_REVIEW_PERMISSION_ROLE",
            name="Departmental Exam Permission Role",
        )
        RolePermission.objects.create(
            role=permission_role,
            permission=review_permission,
        )
        UserRole.objects.create(
            user=permission_from_another_role,
            role=permission_role,
            tenant=self.tenant,
            campus=self.campus_a,
            department=None,
        )

        duplicate_memberships = self._reviewer(username="duplicate-reviewer")
        duplicate_role = Role.objects.create(
            code="DE_TEST_DUPLICATE_REVIEWER",
            name="Duplicate Reviewer Role",
        )
        RolePermission.objects.create(
            role=duplicate_role,
            permission=review_permission,
        )
        UserRole.objects.create(
            user=duplicate_memberships,
            role=duplicate_role,
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
        )

        superuser = get_user_model().objects.create_superuser(
            "review-superuser",
            "review-superuser@example.edu",
            "Pass123!",
            default_tenant=self.tenant,
            default_campus=self.campus_a,
        )
        self.assertFalse(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=superuser,
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            )
        )
        super_scope_role = Role.objects.create(
            code="DE_TEST_SUPER_REVIEW_SCOPE",
            name="Superuser Reviewer Scope",
        )
        UserRole.objects.create(
            user=superuser,
            role=super_scope_role,
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
        )

        listed_ids = list(
            DepartmentalExamAuthorizationService.eligible_reviewers(
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            ).values_list("id", flat=True)
        )
        self.assertIn(direct_allow.id, listed_ids)
        self.assertNotIn(denied.id, listed_ids)
        self.assertIn(permission_from_another_role.id, listed_ids)
        self.assertIn(duplicate_memberships.id, listed_ids)
        self.assertEqual(listed_ids.count(duplicate_memberships.id), 1)
        self.assertIn(superuser.id, listed_ids)
        for candidate in (
            direct_allow,
            permission_from_another_role,
            duplicate_memberships,
            superuser,
        ):
            self.assertTrue(
                DepartmentalExamAuthorizationService.is_eligible_reviewer(
                    user=candidate,
                    tenant_id=self.tenant.id,
                    responsible_department=self.department_a,
                )
            )
        self.assertFalse(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=denied,
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            )
        )

        _, cycle_course = self._grouped_course()
        url = reverse(
            "departmental_exams:cycle_course_administration",
            args=[cycle_course.id],
        )
        self.client.force_login(self.admin)
        accepted = self.client.post(
            url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": direct_allow.id,
            },
        )
        self.assertRedirects(accepted, url)
        cycle_course.refresh_from_db()
        self.client.force_login(direct_allow)
        reviewer_list_response = self.client.get(
            reverse("departmental_exams:cycle_course_list", args=[cycle_course.cycle_id])
        )
        self.assertEqual(reviewer_list_response.status_code, 200)
        self.assertEqual(
            {row.id for row in reviewer_list_response.context["courses"]},
            {cycle_course.id},
        )
        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=direct_allow,
            cycle_course=cycle_course,
        )

        self.client.force_login(self.admin)
        rejected = self.client.post(
            url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": denied.id,
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertIn("reviewer", rejected.context["form"].errors)
        cycle_course.refresh_from_db()
        self.assertEqual(cycle_course.reviewer_id, direct_allow.id)

        cycle_course.reviewer = denied
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        self.client.force_login(denied)
        denied_list_response = self.client.get(
            reverse("departmental_exams:cycle_course_list", args=[cycle_course.cycle_id])
        )
        self.assertEqual(denied_list_response.status_code, 403)
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=denied,
                cycle_course=cycle_course,
            )

    def test_assigned_reviewer_action_access_revalidates_current_eligibility(self):
        parent, _child = self._department_hierarchy()
        _, cycle_course = self._grouped_course(cross_campus=True)
        reviewer = self._reviewer(username="revalidated-reviewer")
        membership = UserRole.objects.get(
            user=reviewer,
            tenant=self.tenant,
            department=self.department_a,
        )
        role = membership.role
        review_permission = Permission.objects.get(
            code="departmental_exams.review_generate"
        )
        cycle_course.reviewer = reviewer
        cycle_course.save(update_fields=["reviewer", "updated_at"])

        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=reviewer,
            cycle_course=cycle_course,
        )

        reviewer.is_active = False
        reviewer.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=cycle_course,
            )
        reviewer.is_active = True
        reviewer.save(update_fields=["is_active"])

        membership.is_active = False
        membership.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=cycle_course,
            )
        membership.is_active = True
        membership.save(update_fields=["is_active"])

        role.is_active = False
        role.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=cycle_course,
            )
        role.is_active = True
        role.save(update_fields=["is_active"])

        membership.department = parent
        membership.save(update_fields=["department"])
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=cycle_course,
            )
        membership.department = self.department_a
        membership.save(update_fields=["department"])

        RolePermission.objects.get(
            role=role,
            permission=review_permission,
        ).delete()
        unrelated_permission_role = Role.objects.create(
            code="DE_TEST_UNRELATED_CAMPUS_PERMISSION",
            name="Unrelated Campus Permission",
        )
        RolePermission.objects.create(
            role=unrelated_permission_role,
            permission=review_permission,
        )
        UserRole.objects.create(
            user=reviewer,
            role=unrelated_permission_role,
            tenant=self.tenant,
            campus=self.campus_b,
            department=None,
        )
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=cycle_course,
            )

        RolePermission.objects.create(role=role, permission=review_permission)
        UserPermission.objects.create(
            user=reviewer,
            permission=review_permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus_a,
        )
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=cycle_course,
            )

    def test_reviewer_candidate_query_count_stays_bounded_for_multiple_candidates(self):
        self._reviewer(username="candidate-base")

        with CaptureQueriesContext(connection) as small_query_context:
            small_candidates = list(
                DepartmentalExamAuthorizationService.eligible_reviewers(
                    tenant_id=self.tenant.id,
                    responsible_department=self.department_a,
                )
            )

        for number in range(8):
            self._reviewer(username=f"candidate-{number}")
        with CaptureQueriesContext(connection) as large_query_context:
            large_candidates = list(
                DepartmentalExamAuthorizationService.eligible_reviewers(
                    tenant_id=self.tenant.id,
                    responsible_department=self.department_a,
                )
            )

        self.assertEqual(len(small_candidates), 1)
        self.assertEqual(len(large_candidates), 9)
        self.assertEqual(
            len({candidate.id for candidate in large_candidates}),
            len(large_candidates),
        )
        self.assertLessEqual(
            len(large_query_context), len(small_query_context) + 1
        )
        self.assertLessEqual(len(large_query_context), 2)

    def test_routed_reviewer_list_queries_stay_bounded_for_multiple_course_rows(self):
        self._offering(suffix="routed-query-base")
        cycle = self._create_cycle()
        base_cycle_course = CycleCourse.objects.get(cycle=cycle, course=self.course)
        reviewer = self._scoped_user(
            username="routed-query-reviewer",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.review_generate",
            ),
        )
        review_permission = Permission.objects.get(code="departmental_exams.review_generate")
        second_scope_role = Role.objects.create(
            code="DE_TEST_ROUTED_QUERY_SECOND_SCOPE",
            name="Routed Query Second Scope",
        )
        RolePermission.objects.create(role=second_scope_role, permission=review_permission)
        UserRole.objects.create(
            user=reviewer,
            role=second_scope_role,
            tenant=self.tenant,
            campus=self.campus_b,
            department=self.department_b,
        )
        configure_role = Role.objects.create(
            code="DE_TEST_ROUTED_QUERY_CONFIGURE",
            name="Routed Query Configure",
        )
        RolePermission.objects.create(
            role=configure_role,
            permission=Permission.objects.get(code="departmental_exams.configure"),
        )
        UserRole.objects.create(
            user=reviewer,
            role=configure_role,
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
        )
        other_reviewer = self._scoped_user(
            username="routed-query-other-reviewer",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.review_generate",
            ),
        )
        base_cycle_course.reviewer = reviewer
        base_cycle_course.save(update_fields=["reviewer", "updated_at"])
        url = reverse("departmental_exams:cycle_course_list", args=[cycle.id])
        self.client.force_login(reviewer)

        with CaptureQueriesContext(connection) as small_query_context:
            small_response = self.client.get(url)
        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(
            {row.id for row in small_response.context["courses"]},
            {base_cycle_course.id},
        )

        expected_visible_ids = {base_cycle_course.id}
        for number in range(8):
            department = self.department_a if number % 2 == 0 else self.department_b
            campus = department.campus
            course = Course.objects.create(
                tenant=self.tenant,
                code=f"RQL{number}",
                title=f"Routed Query Course {number}",
                exam_department=department,
            )
            offering = self._offering(
                course=course,
                campus=campus,
                department=department,
                suffix=f"routed-query-{number}",
            )
            cycle_course = CycleCourse.objects.create(
                cycle=cycle,
                course=course,
                responsible_department=department,
                reviewer=reviewer if number < 6 else other_reviewer,
            )
            CycleCourseOffering.objects.create(
                cycle_course=cycle_course,
                offering=offering,
                campus=campus,
            )
            if department == self.department_a:
                expected_visible_ids.add(cycle_course.id)

        parent, child = self._department_hierarchy()
        unrelated_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            code="RQL-UNRELATED",
            name="Routed Query Unrelated",
        )
        unrelated_campus_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus_b,
            code="RQL-OTHER-CAMPUS",
            name="Routed Query Other Campus",
        )
        for suffix, department in (
            ("parent", parent),
            ("child", child),
            ("unrelated", unrelated_department),
            ("other-campus", unrelated_campus_department),
        ):
            course = Course.objects.create(
                tenant=self.tenant,
                code=f"RQL-{suffix.upper()}",
                title=f"Routed Query {suffix}",
                exam_department=department,
            )
            offering = self._offering(
                course=course,
                campus=department.campus,
                department=department,
                suffix=f"routed-query-{suffix}",
            )
            cycle_course = CycleCourse.objects.create(
                cycle=cycle,
                course=course,
                responsible_department=department,
                reviewer=reviewer,
            )
            CycleCourseOffering.objects.create(
                cycle_course=cycle_course,
                offering=offering,
                campus=department.campus,
            )

        configure_course = Course.objects.create(
            tenant=self.tenant,
            code="RQL-CONFIGURE",
            title="Routed Query Configure",
            exam_department=self.department_a,
        )
        configure_offering = self._offering(
            course=configure_course,
            campus=self.campus_a,
            department=self.department_a,
            suffix="routed-query-configure",
        )
        configure_cycle_course = CycleCourse.objects.create(
            cycle=cycle,
            course=configure_course,
            responsible_department=self.department_a,
        )
        CycleCourseOffering.objects.create(
            cycle_course=configure_cycle_course,
            offering=configure_offering,
            campus=self.campus_a,
        )
        expected_visible_ids.add(configure_cycle_course.id)

        UserPermission.objects.create(
            user=reviewer,
            permission=review_permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus_b,
        )

        with CaptureQueriesContext(connection) as large_query_context:
            large_response = self.client.get(url)
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(
            {row.id for row in large_response.context["courses"]},
            expected_visible_ids,
        )
        self.assertLessEqual(
            len(large_query_context), len(small_query_context) + 2
        )

    def test_administration_page_snapshot_queries_stay_bounded(self):
        self._offering(suffix="small")
        large_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-LARGE",
            title="Large Snapshot Course",
            exam_department=self.department_a,
        )
        for number in range(6):
            self._offering(
                course=large_course,
                campus=self.campus_a if number % 2 == 0 else self.campus_b,
                department=self.department_a if number % 2 == 0 else self.department_b,
                suffix=f"large-{number}",
            )
        cycle = self._create_cycle()
        small_cycle_course = CycleCourse.objects.get(cycle=cycle, course=self.course)
        large_cycle_course = CycleCourse.objects.get(cycle=cycle, course=large_course)
        self.client.force_login(self.admin)

        with CaptureQueriesContext(connection) as small_query_context:
            small_response = self.client.get(
                reverse(
                    "departmental_exams:cycle_course_administration",
                    args=[small_cycle_course.id],
                )
            )
        with CaptureQueriesContext(connection) as large_query_context:
            large_response = self.client.get(
                reverse(
                    "departmental_exams:cycle_course_administration",
                    args=[large_cycle_course.id],
                )
            )

        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(large_response.status_code, 200)
        self.assertContains(large_response, self.campus_a.name)
        self.assertContains(large_response, self.campus_b.name)
        self.assertLessEqual(
            len(large_query_context), len(small_query_context) + 2
        )

    def test_cycle_creation_streams_grouped_snapshots_without_offering_department_ownership(self):
        first = self._offering(suffix="stream-one")
        second = self._offering(
            campus=self.campus_b,
            department=self.department_b,
            suffix="stream-two",
        )
        other_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-STREAM",
            title="Streamed Course",
            exam_department=self.department_b,
        )
        third = self._offering(
            course=other_course,
            campus=self.campus_b,
            department=self.department_a,
            suffix="stream-three",
        )

        cycle = self._create_cycle()

        first_group = CycleCourse.objects.get(cycle=cycle, course=self.course)
        second_group = CycleCourse.objects.get(cycle=cycle, course=other_course)
        self.assertEqual(CycleCourse.objects.filter(cycle=cycle).count(), 2)
        self.assertEqual(first_group.responsible_department_id, self.department_a.id)
        self.assertEqual(second_group.responsible_department_id, self.department_b.id)
        self.assertEqual(
            set(first_group.offering_snapshots.values_list("offering_id", flat=True)),
            {first.id, second.id},
        )
        self.assertEqual(
            set(second_group.offering_snapshots.values_list("offering_id", flat=True)),
            {third.id},
        )

    def test_cycle_creation_batches_snapshots_across_more_than_200_course_groups(self):
        courses = self._bulk_offering_dataset(
            count=ExaminationCycleService.SNAPSHOT_BATCH_SIZE + 5
        )
        batch_sizes = []
        manager = CycleCourseOffering.objects
        original_bulk_create = manager.bulk_create

        def recording_bulk_create(objects, *args, **kwargs):
            batch_sizes.append(len(objects))
            return original_bulk_create(objects, *args, **kwargs)

        with patch.object(
            manager,
            "bulk_create",
            side_effect=recording_bulk_create,
        ):
            cycle = self._create_cycle()

        expected_count = ExaminationCycleService.SNAPSHOT_BATCH_SIZE + 5
        self.assertEqual(batch_sizes, [ExaminationCycleService.SNAPSHOT_BATCH_SIZE, 5])
        self.assertEqual(CycleCourse.objects.filter(cycle=cycle).count(), expected_count)
        self.assertEqual(
            CycleCourseOffering.objects.filter(cycle_course__cycle=cycle).count(),
            expected_count,
        )
        self.assertEqual(
            CycleCourse.objects.filter(cycle=cycle)
            .values("course_id")
            .distinct()
            .count(),
            expected_count,
        )
        self.assertEqual(
            {course.id for course in courses},
            set(
                CycleCourse.objects.filter(cycle=cycle).values_list(
                    "course_id",
                    flat=True,
                )
            ),
        )

        snapshots = CycleCourseOffering.objects.filter(
            cycle_course__cycle=cycle
        ).select_related(
            "cycle_course__course",
            "offering",
        )
        for snapshot in snapshots:
            with self.subTest(offering_id=snapshot.offering_id):
                self.assertEqual(
                    snapshot.cycle_course.course_id,
                    snapshot.offering.course_id,
                )
                self.assertEqual(snapshot.campus_id, snapshot.offering.campus_id)
                self.assertEqual(
                    snapshot.cycle_course.responsible_department_id,
                    snapshot.cycle_course.course.exam_department_id,
                )
                self.assertNotEqual(
                    snapshot.cycle_course.responsible_department_id,
                    snapshot.offering.department_id,
                )

    def test_later_snapshot_batch_failure_rolls_back_entire_cycle(self):
        self._bulk_offering_dataset(
            count=ExaminationCycleService.SNAPSHOT_BATCH_SIZE + 5
        )
        manager = CycleCourseOffering.objects
        original_bulk_create = manager.bulk_create
        batch_calls = 0

        def fail_second_batch(objects, *args, **kwargs):
            nonlocal batch_calls
            batch_calls += 1
            if batch_calls == 2:
                raise RuntimeError("simulated later snapshot batch failure")
            return original_bulk_create(objects, *args, **kwargs)

        with patch.object(
            manager,
            "bulk_create",
            side_effect=fail_second_batch,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated later snapshot batch failure",
            ):
                self._create_cycle()

        self.assertEqual(batch_calls, 2)
        self.assertFalse(
            ExaminationCycle.objects.filter(
                tenant=self.tenant,
                academic_year=self.year,
                term=self.term,
                exam_period="MIDTERM",
            ).exists()
        )
        self.assertEqual(CycleCourse.objects.count(), 0)
        self.assertEqual(CycleCourseOffering.objects.count(), 0)
        self.assertFalse(
            AuditLog.objects.filter(action="DE_EXAM_CYCLE_CREATED").exists()
        )

    def test_reviewer_assignment_change_removal_and_immediate_revocation_are_audited(self):
        _, cycle_course = self._grouped_course(cross_campus=True)
        reviewer_one = self._reviewer(username="reviewer-one")
        reviewer_two = self._reviewer(username="reviewer-two")
        url = reverse("departmental_exams:cycle_course_administration", args=[cycle_course.id])

        self.client.force_login(self.admin)
        response = self.client.post(
            url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": reviewer_one.id,
            },
        )
        self.assertRedirects(response, url)
        cycle_course.refresh_from_db()
        DepartmentalExamAuthorizationService.require_course_responsibility(
            user=reviewer_one,
            cycle_course=cycle_course,
            permission="departmental_exams.review_generate",
        )

        response = self.client.post(
            url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": reviewer_two.id,
            },
        )
        self.assertRedirects(response, url)
        response = self.client.post(
            url, {"responsible_department": self.department_a.id}
        )
        self.assertRedirects(response, url)
        cycle_course.refresh_from_db()
        self.assertIsNone(cycle_course.reviewer_id)
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer_two,
                cycle_course=cycle_course,
                permission="departmental_exams.review_generate",
            )
        from apps.auditlog.models import AuditLog

        audit_entries = AuditLog.objects.filter(
            action="DE_EXAM_CYCLE_COURSE_ADMIN_UPDATED",
            entity_type="CycleCourse",
            entity_id=str(cycle_course.id),
        ).order_by("id")
        self.assertEqual(audit_entries.count(), 3)
        self.assertEqual(audit_entries[0].after_json["reviewer_id"], reviewer_one.id)
        self.assertEqual(audit_entries[1].after_json["reviewer_id"], reviewer_two.id)
        self.assertIsNone(audit_entries[2].after_json["reviewer_id"])

    def test_reviewer_cannot_access_unrelated_cycle_course_or_tenant(self):
        cycle, cycle_course = self._grouped_course()
        reviewer = self._reviewer(username="assigned-reviewer")
        cycle_course.reviewer = reviewer
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        other_course = Course.objects.create(
            tenant=self.tenant,
            code="EX103",
            title="Other Exam Course",
            exam_department=self.department_a,
        )
        unrelated = CycleCourse.objects.create(
            cycle=cycle,
            course=other_course,
            responsible_department=self.department_a,
        )
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=unrelated,
                permission="departmental_exams.review_generate",
            )

        other_campus = Campus.objects.create(
            tenant=self.other_tenant, code="OTHER", name="Other Campus"
        )
        other_department = Department.objects.create(
            tenant=self.other_tenant,
            campus=other_campus,
            code="OTHER",
            name="Other Department",
        )
        other_course = Course.objects.create(
            tenant=self.other_tenant,
            code="OTHER101",
            title="Other Tenant Course",
            exam_department=other_department,
        )
        other_cycle = ExaminationCycle.objects.create(
            tenant=self.other_tenant,
            academic_year=self.other_year,
            term=self.other_term,
            exam_period="MIDTERM",
            created_by=self.admin,
        )
        other_cycle_course = CycleCourse.objects.create(
            cycle=other_cycle,
            course=other_course,
            responsible_department=other_department,
            reviewer=reviewer,
        )
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=reviewer,
                cycle_course=other_cycle_course,
                permission="departmental_exams.review_generate",
            )

    def test_administration_change_rolls_back_when_audit_fails(self):
        self.course.exam_department = None
        self.course.save(update_fields=["exam_department", "updated_at"])
        _, cycle_course = self._grouped_course()
        url = reverse("departmental_exams:cycle_course_administration", args=[cycle_course.id])
        request = RequestFactory().post(
            url, {"responsible_department": self.department_a.id}
        )
        request.user = self.admin
        request.scope = {
            "tenant_id": self.tenant.id,
            "campus_id": self.campus_a.id,
        }
        with patch(
            "apps.departmental_exams.views.AuditService.log_event",
            side_effect=RuntimeError("audit unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                cycle_course_administration_view(
                    request, cycle_course_id=cycle_course.id
                )
        cycle_course.refresh_from_db()
        self.assertIsNone(cycle_course.responsible_department_id)
        self.assertIsNone(cycle_course.reviewer_id)

    def test_manage_cycles_and_configure_are_separate_authorities(self):
        self._offering(suffix="permission-boundary")
        cycle = self._create_cycle()
        cycle_course = CycleCourse.objects.get(cycle=cycle, course=self.course)
        cycle_list_url = reverse("departmental_exams:cycle_list")
        cycle_create_url = reverse("departmental_exams:cycle_create")
        course_list_url = reverse(
            "departmental_exams:cycle_course_list", args=[cycle.id]
        )
        administration_url = reverse(
            "departmental_exams:cycle_course_administration", args=[cycle_course.id]
        )
        reviewer = self._reviewer(username="boundary-reviewer")

        manage_only = self._scoped_user(
            username="boundary-manage-only",
            department=self.department_a,
            permissions=("admin_portal.access", "departmental_exams.manage_cycles"),
        )
        self.client.force_login(manage_only)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 200)
        self.assertEqual(self.client.get(cycle_create_url).status_code, 200)
        self.assertEqual(self.client.get(course_list_url).status_code, 403)
        self.assertEqual(self.client.get(administration_url).status_code, 403)
        denied_mutation = self.client.post(
            administration_url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": reviewer.id,
            },
        )
        self.assertEqual(denied_mutation.status_code, 403)
        cycle_course.refresh_from_db()
        self.assertIsNone(cycle_course.reviewer_id)

        configure_only = self._configurer(username="boundary-configure-only")
        self.client.force_login(configure_only)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
        self.assertEqual(self.client.get(cycle_create_url).status_code, 403)
        configured_list = self.client.get(course_list_url)
        self.assertEqual(configured_list.status_code, 200)
        self.assertEqual(
            {row.id for row in configured_list.context["courses"]}, {cycle_course.id}
        )
        self.assertTrue(configured_list.context["courses"][0].can_administer)
        self.assertEqual(self.client.get(administration_url).status_code, 200)
        configured_mutation = self.client.post(
            administration_url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": reviewer.id,
            },
        )
        self.assertRedirects(configured_mutation, administration_url)
        cycle_course.refresh_from_db()
        self.assertEqual(cycle_course.reviewer_id, reviewer.id)

        dual_user = self._configurer(
            username="boundary-dual-user",
            permissions=("departmental_exams.manage_cycles",),
        )
        self.client.force_login(dual_user)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 200)
        self.assertEqual(self.client.get(course_list_url).status_code, 200)
        self.assertEqual(self.client.get(administration_url).status_code, 200)

        no_path = self._scoped_user(
            username="boundary-no-path",
            department=self.department_a,
            permissions=("admin_portal.access",),
        )
        self.client.force_login(no_path)
        self.assertEqual(self.client.get(course_list_url).status_code, 403)

        scope_free_configurer = self._scoped_user(
            username="boundary-scope-free-configurer",
            department=None,
            permissions=("admin_portal.access", "departmental_exams.configure"),
        )
        self.client.force_login(scope_free_configurer)
        self.assertEqual(self.client.get(course_list_url).status_code, 403)

    def test_configurer_scope_is_exact_and_honors_permission_state(self):
        parent, child = self._department_hierarchy()
        parent_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-PARENT",
            title="Parent Scope Course",
            exam_department=parent,
        )
        child_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-CHILD",
            title="Child Scope Course",
            exam_department=child,
        )
        other_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-OTHER-SCOPE",
            title="Other Scope Course",
            exam_department=self.department_b,
        )
        self._offering(course=parent_course, department=parent, suffix="parent-scope")
        self._offering(course=child_course, department=child, suffix="child-scope")
        self._offering(
            course=other_course,
            campus=self.campus_b,
            department=self.department_b,
            suffix="other-scope",
        )
        cycle = self._create_cycle()
        parent_cycle_course = CycleCourse.objects.get(cycle=cycle, course=parent_course)
        child_cycle_course = CycleCourse.objects.get(cycle=cycle, course=child_course)
        other_cycle_course = CycleCourse.objects.get(cycle=cycle, course=other_course)
        url = reverse("departmental_exams:cycle_course_list", args=[cycle.id])

        parent_configurer = self._configurer(
            username="parent-configurer", department=parent
        )
        self.client.force_login(parent_configurer)
        parent_response = self.client.get(url)
        self.assertEqual(parent_response.status_code, 200)
        self.assertEqual(
            {row.id for row in parent_response.context["courses"]},
            {parent_cycle_course.id},
        )
        self.assertNotIn(child_cycle_course.id, {row.id for row in parent_response.context["courses"]})

        child_configurer = self._configurer(
            username="child-configurer", department=child
        )
        self.client.force_login(child_configurer)
        child_response = self.client.get(url)
        self.assertEqual(child_response.status_code, 200)
        self.assertEqual(
            {row.id for row in child_response.context["courses"]},
            {child_cycle_course.id},
        )
        self.assertNotIn(parent_cycle_course.id, {row.id for row in child_response.context["courses"]})

        unrelated_configurer = self._configurer(
            username="unrelated-configurer", department=self.department_b
        )
        self.client.force_login(unrelated_configurer)
        unrelated_response = self.client.get(url)
        self.assertEqual(unrelated_response.status_code, 200)
        self.assertEqual(
            {row.id for row in unrelated_response.context["courses"]},
            {other_cycle_course.id},
        )

        wrong_campus = self._configurer(username="wrong-campus-configurer")
        wrong_campus_membership = UserRole.objects.get(user=wrong_campus)
        wrong_campus_membership.campus = self.campus_b
        wrong_campus_membership.save(update_fields=["campus"])
        self.client.force_login(wrong_campus)
        self.assertEqual(self.client.get(url).status_code, 403)

        denied_configurer = self._configurer(username="denied-configurer")
        UserPermission.objects.create(
            user=denied_configurer,
            permission=Permission.objects.get(code="departmental_exams.configure"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus_a,
        )
        self.client.force_login(denied_configurer)
        self.assertEqual(self.client.get(url).status_code, 403)

        inactive_user = self._configurer(username="inactive-configurer")
        inactive_user.is_active = False
        inactive_user.save(update_fields=["is_active"])
        self.client.force_login(inactive_user)
        self.assertNotEqual(self.client.get(url).status_code, 200)

        inactive_membership = self._configurer(username="inactive-configurer-role")
        UserRole.objects.filter(user=inactive_membership).update(is_active=False)
        self.client.force_login(inactive_membership)
        self.assertEqual(self.client.get(url).status_code, 403)

        inactive_role = self._configurer(username="inactive-configurer-role-record")
        role = UserRole.objects.get(user=inactive_role).role
        role.is_active = False
        role.save(update_fields=["is_active"])
        self.client.force_login(inactive_role)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_mixed_configurer_and_reviewer_list_is_a_deduplicated_union(self):
        ineligible_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            code="MIXED-NO-SCOPE",
            name="Mixed No Scope",
        )
        configure_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-MIXED-CONFIGURE",
            title="Configure Course",
            exam_department=self.department_a,
        )
        reviewer_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-MIXED-REVIEWER",
            title="Reviewer Course",
            exam_department=self.department_b,
        )
        dual_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-MIXED-DUAL",
            title="Configure and Reviewer Course",
            exam_department=self.department_a,
        )
        ineligible_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-MIXED-INELIGIBLE",
            title="Ineligible Assigned Course",
            exam_department=ineligible_department,
        )
        unrelated_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-MIXED-UNRELATED",
            title="Unrelated Course",
            exam_department=self.department_b,
        )
        null_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-MIXED-NULL",
            title="Needs Department Course",
            exam_department=None,
        )
        self._offering(course=configure_course, suffix="mixed-configure")
        self._offering(
            course=reviewer_course,
            campus=self.campus_b,
            department=self.department_b,
            suffix="mixed-reviewer",
        )
        self._offering(course=dual_course, suffix="mixed-dual")
        self._offering(
            course=ineligible_course,
            campus=self.campus_a,
            department=ineligible_department,
            suffix="mixed-ineligible",
        )
        self._offering(
            course=unrelated_course,
            campus=self.campus_b,
            department=self.department_b,
            suffix="mixed-unrelated",
        )
        self._offering(
            course=null_course,
            campus=self.campus_b,
            department=self.department_b,
            suffix="mixed-null",
        )
        cycle = self._create_cycle()
        configure_cycle_course = CycleCourse.objects.get(cycle=cycle, course=configure_course)
        reviewer_cycle_course = CycleCourse.objects.get(cycle=cycle, course=reviewer_course)
        dual_cycle_course = CycleCourse.objects.get(cycle=cycle, course=dual_course)
        ineligible_cycle_course = CycleCourse.objects.get(cycle=cycle, course=ineligible_course)
        unrelated_cycle_course = CycleCourse.objects.get(cycle=cycle, course=unrelated_course)
        null_cycle_course = CycleCourse.objects.get(cycle=cycle, course=null_course)

        user = self._configurer(
            username="mixed-configurer-reviewer",
            permissions=("departmental_exams.manage_cycles",),
        )
        reviewer_role = Role.objects.create(
            code="DE_TEST_MIXED_REVIEWER",
            name="Mixed Reviewer",
        )
        RolePermission.objects.create(
            role=reviewer_role,
            permission=Permission.objects.get(code="departmental_exams.review_generate"),
        )
        UserRole.objects.create(
            user=user,
            role=reviewer_role,
            tenant=self.tenant,
            campus=self.campus_b,
            department=self.department_b,
        )
        UserRole.objects.create(
            user=user,
            role=reviewer_role,
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
        )
        reviewer_cycle_course.reviewer = user
        reviewer_cycle_course.save(update_fields=["reviewer", "updated_at"])
        dual_cycle_course.reviewer = user
        dual_cycle_course.save(update_fields=["reviewer", "updated_at"])
        ineligible_cycle_course.reviewer = user
        ineligible_cycle_course.save(update_fields=["reviewer", "updated_at"])

        self.client.force_login(user)
        response = self.client.get(
            reverse("departmental_exams:cycle_course_list", args=[cycle.id])
        )
        self.assertEqual(response.status_code, 200)
        visible = response.context["courses"]
        self.assertEqual(
            {row.id for row in visible},
            {configure_cycle_course.id, reviewer_cycle_course.id, dual_cycle_course.id},
        )
        self.assertEqual(len(visible), 3)
        self.assertNotIn(ineligible_cycle_course.id, {row.id for row in visible})
        self.assertNotIn(unrelated_cycle_course.id, {row.id for row in visible})
        self.assertNotIn(null_cycle_course.id, {row.id for row in visible})
        self.assertTrue(
            next(row for row in visible if row.id == configure_cycle_course.id).can_administer
        )
        self.assertFalse(
            next(row for row in visible if row.id == reviewer_cycle_course.id).can_administer
        )
        self.assertTrue(
            next(row for row in visible if row.id == dual_cycle_course.id).can_administer
        )
        assigned_response = self.client.get(
            reverse("departmental_exams:assigned_course_examinations")
        )
        self.assertEqual(assigned_response.status_code, 200)
        assigned_rows = assigned_response.context["courses"]
        self.assertEqual(
            {row.id for row in assigned_rows},
            {configure_cycle_course.id, reviewer_cycle_course.id, dual_cycle_course.id},
        )
        self.assertEqual(
            [row.id for row in assigned_rows].count(dual_cycle_course.id), 1
        )
        self.assertContains(
            assigned_response,
            reverse(
                "departmental_exams:cycle_course_administration",
                args=[configure_cycle_course.id],
            ),
        )
        self.assertContains(
            assigned_response,
            reverse(
                "departmental_exams:cycle_course_administration",
                args=[dual_cycle_course.id],
            ),
        )
        self.assertNotContains(
            assigned_response,
            reverse(
                "departmental_exams:cycle_course_administration",
                args=[reviewer_cycle_course.id],
            ),
        )
        self.assertNotContains(assigned_response, ineligible_course.code)
        self.assertNotContains(assigned_response, unrelated_course.code)
        self.assertNotContains(assigned_response, null_course.code)

    def test_null_responsible_course_is_superuser_only_until_initial_assignment(self):
        self.course.exam_department = None
        self.course.save(update_fields=["exam_department", "updated_at"])
        _, cycle_course = self._grouped_course()
        list_url = reverse("departmental_exams:cycle_course_list", args=[cycle_course.cycle_id])
        administration_url = reverse(
            "departmental_exams:cycle_course_administration", args=[cycle_course.id]
        )
        reviewer = self._reviewer(username="null-state-reviewer")

        manage_only = self._scoped_user(
            username="null-state-manager",
            department=self.department_a,
            permissions=("admin_portal.access", "departmental_exams.manage_cycles"),
        )
        configure_only = self._configurer(username="null-state-configurer")
        unrelated_scope = self._configurer(
            username="null-state-unrelated", department=self.department_b
        )
        null_department_role = self._scoped_user(
            username="null-state-null-department",
            department=None,
            permissions=("admin_portal.access", "departmental_exams.configure"),
        )
        for user in (
            manage_only,
            configure_only,
            unrelated_scope,
            null_department_role,
        ):
            self.client.force_login(user)
            self.assertEqual(self.client.get(list_url).status_code, 403)
            self.assertEqual(self.client.get(administration_url).status_code, 403)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(list_url).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:assigned_course_examinations")
            ).status_code,
            403,
        )
        self.assertEqual(self.client.get(administration_url).status_code, 200)
        missing_department = self.client.post(
            administration_url, {"reviewer_id": reviewer.id}
        )
        self.assertEqual(missing_department.status_code, 200)
        self.assertContains(missing_department, "Select an exam department")
        cycle_course.refresh_from_db()
        self.assertIsNone(cycle_course.reviewer_id)

        other_campus = Campus.objects.create(
            tenant=self.other_tenant, code="NULL-OTHER", name="Null Other Campus"
        )
        other_department = Department.objects.create(
            tenant=self.other_tenant,
            campus=other_campus,
            code="NULL-OTHER",
            name="Null Other Department",
        )
        cross_tenant = self.client.post(
            administration_url,
            {"responsible_department": other_department.id},
        )
        self.assertEqual(cross_tenant.status_code, 200)
        self.assertIn("responsible_department", cross_tenant.context["form"].errors)
        cycle_course.refresh_from_db()
        self.assertIsNone(cycle_course.responsible_department_id)

        assigned = self.client.post(
            administration_url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": reviewer.id,
            },
        )
        self.assertRedirects(assigned, administration_url)
        cycle_course.refresh_from_db()
        self.assertEqual(cycle_course.responsible_department_id, self.department_a.id)
        self.assertEqual(cycle_course.reviewer_id, reviewer.id)
        self.assertTrue(
            AuditLog.objects.filter(
                action="DE_EXAM_CYCLE_COURSE_ADMIN_UPDATED",
                entity_id=str(cycle_course.id),
            ).exists()
        )

        self.client.force_login(configure_only)
        self.assertEqual(self.client.get(administration_url).status_code, 200)
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:assigned_course_examinations")
            ).status_code,
            200,
        )
        self.client.force_login(unrelated_scope)
        self.assertEqual(self.client.get(administration_url).status_code, 403)

        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(list_url).status_code, 403)
        self.assertEqual(self.client.get(administration_url).status_code, 403)

    def test_assigned_course_navigation_matches_manager_configurer_and_reviewer_paths(self):
        self._offering(suffix="navigation-configurer")
        reviewer_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-NAV-REVIEW",
            title="Navigation Reviewer Course",
            exam_department=self.department_b,
        )
        self._offering(
            course=reviewer_course,
            campus=self.campus_b,
            department=self.department_b,
            suffix="navigation-reviewer",
        )
        cycle = self._create_cycle()
        reviewer_cycle_course = CycleCourse.objects.get(
            cycle=cycle, course=reviewer_course
        )
        manager = self._scoped_user(
            username="navigation-manager",
            department=self.department_a,
            permissions=("admin_portal.access", "departmental_exams.manage_cycles"),
        )
        configurer = self._configurer(username="navigation-configurer")
        reviewer = self._scoped_user(
            username="navigation-reviewer",
            department=self.department_b,
            permissions=(
                "admin_portal.access",
                "departmental_exams.review_generate",
            ),
        )
        reviewer_cycle_course.reviewer = reviewer
        reviewer_cycle_course.save(update_fields=["reviewer", "updated_at"])
        dual = self._configurer(
            username="navigation-dual",
            permissions=("departmental_exams.manage_cycles",),
        )
        no_path = self._scoped_user(
            username="navigation-no-path",
            department=self.department_a,
            permissions=("admin_portal.access",),
        )

        def visible_codes(user):
            return {
                node["item"].code
                for group in MenuService.get_menu_tree(
                    user,
                    portal="ADMIN",
                    tenant_id=self.tenant.id,
                    campus_id=user.default_campus_id,
                )
                for node in group["items"]
            }

        self.assertEqual(visible_codes(manager), {"DE_EXAM_CYCLES"})
        self.assertEqual(visible_codes(configurer), {"DE_EXAM_ASSIGNED_COURSES"})
        self.assertEqual(visible_codes(reviewer), {"DE_EXAM_ASSIGNED_COURSES"})
        self.assertEqual(
            visible_codes(dual),
            {"DE_EXAM_CYCLES", "DE_EXAM_ASSIGNED_COURSES"},
        )
        self.assertFalse(visible_codes(no_path))

        cycle_list_url = reverse("departmental_exams:cycle_list")
        cycle_create_url = reverse("departmental_exams:cycle_create")
        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        self.client.force_login(manager)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 200)
        self.assertEqual(self.client.get(assigned_url).status_code, 403)
        self.client.force_login(configurer)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
        self.assertEqual(self.client.get(cycle_create_url).status_code, 403)
        configured_response = self.client.get(assigned_url)
        self.assertEqual(configured_response.status_code, 200)
        self.assertContains(configured_response, self.course.code)
        self.client.force_login(reviewer)
        reviewer_response = self.client.get(assigned_url)
        self.assertEqual(reviewer_response.status_code, 200)
        self.assertContains(reviewer_response, reviewer_course.code)
        self.assertNotContains(
            reviewer_response,
            reverse(
                "departmental_exams:cycle_course_administration",
                args=[reviewer_cycle_course.id],
            ),
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "departmental_exams:cycle_course_administration",
                    args=[reviewer_cycle_course.id],
                )
            ).status_code,
            403,
        )

    def test_assigned_course_configurer_parity_uses_effective_permission_semantics(self):
        _, cycle_course = self._grouped_course()
        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        configure_permission = Permission.objects.get(code="departmental_exams.configure")

        direct_allow = self._scoped_user(
            username="assigned-direct-configure-allow",
            department=self.department_a,
            permissions=("admin_portal.access",),
        )
        UserPermission.objects.create(
            user=direct_allow,
            permission=configure_permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus_a,
        )

        separate_role = self._scoped_user(
            username="assigned-separate-configure-role",
            department=self.department_a,
            permissions=("admin_portal.access",),
        )
        permission_role = Role.objects.create(
            code="DE_TEST_ASSIGNED_CONFIGURE_PERMISSION_ROLE",
            name="Assigned Configure Permission Role",
        )
        RolePermission.objects.create(
            role=permission_role, permission=configure_permission
        )
        UserRole.objects.create(
            user=separate_role,
            role=permission_role,
            tenant=self.tenant,
            campus=self.campus_a,
            department=None,
        )

        duplicate_memberships = self._configurer(
            username="assigned-duplicate-configurer"
        )
        duplicate_role = Role.objects.create(
            code="DE_TEST_ASSIGNED_DUPLICATE_CONFIGURER",
            name="Assigned Duplicate Configurer",
        )
        RolePermission.objects.create(role=duplicate_role, permission=configure_permission)
        UserRole.objects.create(
            user=duplicate_memberships,
            role=duplicate_role,
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
        )

        denied = self._configurer(username="assigned-direct-configure-deny")
        UserPermission.objects.create(
            user=denied,
            permission=configure_permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus_a,
        )
        wrong_tenant = self._configurer(username="assigned-wrong-tenant-configurer")
        UserRole.objects.filter(user=wrong_tenant).update(tenant=self.other_tenant)

        for user in (direct_allow, separate_role, duplicate_memberships):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.get(assigned_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [row.id for row in response.context["courses"]].count(
                        cycle_course.id
                    ),
                    1,
                )
                self.assertContains(
                    response,
                    reverse(
                        "departmental_exams:cycle_course_administration",
                        args=[cycle_course.id],
                    ),
                )

        for user in (denied, wrong_tenant):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(assigned_url).status_code, 403)

    def test_configurer_null_scoped_permissions_match_permission_service(self):
        _, cycle_course = self._grouped_course()
        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        administration_url = reverse(
            "departmental_exams:cycle_course_administration",
            args=[cycle_course.id],
        )
        configure_permission = Permission.objects.get(
            code="departmental_exams.configure"
        )

        def ownership_user(username):
            return self._scoped_user(
                username=username,
                department=self.department_a,
                permissions=("admin_portal.access",),
            )

        def add_permission_role(user, suffix, *, tenant, campus):
            role = Role.objects.create(
                code=f"DE_TEST_SCOPE_{suffix}",
                name=f"Scope {suffix}",
            )
            RolePermission.objects.create(role=role, permission=configure_permission)
            UserRole.objects.create(
                user=user,
                role=role,
                tenant=tenant,
                campus=campus,
                department=None,
            )

        exact_role = ownership_user("scope-config-exact-role")
        add_permission_role(
            exact_role,
            "CONFIG_EXACT_ROLE",
            tenant=self.tenant,
            campus=self.campus_a,
        )
        global_role = ownership_user("scope-config-global-role")
        add_permission_role(
            global_role,
            "CONFIG_GLOBAL_ROLE",
            tenant=None,
            campus=None,
        )
        campus_null_role = ownership_user("scope-config-campus-null-role")
        add_permission_role(
            campus_null_role,
            "CONFIG_CAMPUS_NULL_ROLE",
            tenant=self.tenant,
            campus=None,
        )

        direct_exact = ownership_user("scope-config-direct-exact")
        direct_global = ownership_user("scope-config-direct-global")
        direct_campus_null = ownership_user("scope-config-direct-campus-null")
        for user, tenant, campus in (
            (direct_exact, self.tenant, self.campus_a),
            (direct_global, None, None),
            (direct_campus_null, self.tenant, None),
        ):
            UserPermission.objects.create(
                user=user,
                permission=configure_permission,
                grant_type=UserPermission.GrantType.ALLOW,
                tenant=tenant,
                campus=campus,
            )

        for user in (exact_role, direct_exact):
            with self.subTest(allowed=user.username):
                self.assertTrue(
                    PermissionService.has_permission(
                        user,
                        configure_permission.code,
                        tenant_id=self.tenant.id,
                        campus_id=self.campus_a.id,
                    )
                )
                self.assertIn(
                    self.department_a.id,
                    DepartmentalExamAuthorizationService.configurable_departments(
                        user=user, tenant_id=self.tenant.id
                    ).values_list("id", flat=True),
                )
                self.client.force_login(user)
                response = self.client.get(assigned_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [row.id for row in response.context["courses"]],
                    [cycle_course.id],
                )
                self.assertContains(response, administration_url)
                self.assertEqual(self.client.get(administration_url).status_code, 200)

        for user in (
            global_role,
            campus_null_role,
            direct_global,
            direct_campus_null,
        ):
            with self.subTest(non_applicable_allow=user.username):
                self.assertFalse(
                    PermissionService.has_permission(
                        user,
                        configure_permission.code,
                        tenant_id=self.tenant.id,
                        campus_id=self.campus_a.id,
                    )
                )
                self.assertNotIn(
                    self.department_a.id,
                    DepartmentalExamAuthorizationService.configurable_departments(
                        user=user, tenant_id=self.tenant.id
                    ).values_list("id", flat=True),
                )
                self.client.force_login(user)
                self.assertEqual(self.client.get(assigned_url).status_code, 403)
                self.assertEqual(self.client.get(administration_url).status_code, 403)

        denied_users = []
        for suffix, tenant, campus in (
            ("EXACT", self.tenant, self.campus_a),
            ("GLOBAL", None, None),
            ("CAMPUS_NULL", self.tenant, None),
        ):
            user = self._configurer(username=f"scope-config-deny-{suffix.lower()}")
            UserPermission.objects.create(
                user=user,
                permission=configure_permission,
                grant_type=UserPermission.GrantType.DENY,
                tenant=tenant,
                campus=campus,
            )
            denied_users.append(user)

        exact_denied, global_deny, campus_null_deny = denied_users
        with self.subTest(denied=exact_denied.username):
            self.assertFalse(
                PermissionService.has_permission(
                    exact_denied,
                    configure_permission.code,
                    tenant_id=self.tenant.id,
                    campus_id=self.campus_a.id,
                )
            )
            self.assertNotIn(
                self.department_a.id,
                DepartmentalExamAuthorizationService.configurable_departments(
                    user=exact_denied, tenant_id=self.tenant.id
                ).values_list("id", flat=True),
            )
            self.client.force_login(exact_denied)
            self.assertEqual(self.client.get(assigned_url).status_code, 403)
            self.assertEqual(self.client.get(administration_url).status_code, 403)

        for user in (global_deny, campus_null_deny):
            with self.subTest(non_applicable_deny=user.username):
                self.assertTrue(
                    PermissionService.has_permission(
                        user,
                        configure_permission.code,
                        tenant_id=self.tenant.id,
                        campus_id=self.campus_a.id,
                    )
                )
                self.assertIn(
                    self.department_a.id,
                    DepartmentalExamAuthorizationService.configurable_departments(
                        user=user, tenant_id=self.tenant.id
                    ).values_list("id", flat=True),
                )
                self.client.force_login(user)
                response = self.client.get(assigned_url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, administration_url)
                self.assertEqual(self.client.get(administration_url).status_code, 200)

        permission_without_ownership = self._scoped_user(
            username="scope-config-global-without-ownership",
            department=None,
            permissions=("admin_portal.access",),
        )
        add_permission_role(
            permission_without_ownership,
            "CONFIG_WITHOUT_OWNERSHIP",
            tenant=None,
            campus=None,
        )
        self.assertFalse(
            PermissionService.has_permission(
                permission_without_ownership,
                configure_permission.code,
                tenant_id=self.tenant.id,
                campus_id=self.campus_a.id,
            )
        )
        self.assertFalse(
            DepartmentalExamAuthorizationService.configurable_departments(
                user=permission_without_ownership, tenant_id=self.tenant.id
            ).exists()
        )
        self.client.force_login(permission_without_ownership)
        self.assertEqual(self.client.get(assigned_url).status_code, 403)

        configure_permission.is_active = False
        configure_permission.save(update_fields=["is_active", "updated_at"])
        self.assertFalse(
            PermissionService.has_permission(
                exact_role,
                configure_permission.code,
                tenant_id=self.tenant.id,
                campus_id=self.campus_a.id,
            )
        )
        self.client.force_login(exact_role)
        self.assertEqual(self.client.get(assigned_url).status_code, 403)

    def test_reviewer_null_scoped_permissions_match_candidates_and_routes(self):
        _, cycle_course = self._grouped_course()
        cycle_list_url = reverse(
            "departmental_exams:cycle_course_list", args=[cycle_course.cycle_id]
        )
        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        administration_url = reverse(
            "departmental_exams:cycle_course_administration",
            args=[cycle_course.id],
        )
        review_permission = Permission.objects.get(
            code="departmental_exams.review_generate"
        )

        def ownership_user(username):
            return self._scoped_user(
                username=username,
                department=self.department_a,
                permissions=("admin_portal.access",),
            )

        def add_review_role(user, suffix, *, tenant, campus):
            role = Role.objects.create(
                code=f"DE_TEST_REVIEW_SCOPE_{suffix}",
                name=f"Review Scope {suffix}",
            )
            RolePermission.objects.create(role=role, permission=review_permission)
            UserRole.objects.create(
                user=user,
                role=role,
                tenant=tenant,
                campus=campus,
                department=None,
            )

        exact = ownership_user("scope-review-exact")
        global_role = ownership_user("scope-review-global-role")
        campus_null_role = ownership_user("scope-review-campus-null-role")
        add_review_role(
            exact,
            "EXACT",
            tenant=self.tenant,
            campus=self.campus_a,
        )
        add_review_role(global_role, "GLOBAL", tenant=None, campus=None)
        add_review_role(
            campus_null_role,
            "CAMPUS_NULL",
            tenant=self.tenant,
            campus=None,
        )
        direct_global = ownership_user("scope-review-direct-global")
        UserPermission.objects.create(
            user=direct_global,
            permission=review_permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=None,
            campus=None,
        )

        allowed_users = (exact,)
        candidate_ids = set(
            DepartmentalExamAuthorizationService.eligible_reviewers(
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            ).values_list("id", flat=True)
        )
        for user in allowed_users:
            with self.subTest(allowed=user.username):
                self.assertIn(user.id, candidate_ids)
                self.assertTrue(
                    DepartmentalExamAuthorizationService.is_eligible_reviewer(
                        user=user,
                        tenant_id=self.tenant.id,
                        responsible_department=self.department_a,
                    )
                )
                cycle_course.reviewer = user
                cycle_course.save(update_fields=["reviewer", "updated_at"])
                self.assertTrue(
                    DepartmentalExamAuthorizationService.reviewer_visible_cycle_courses(
                        user=user,
                        cycle=cycle_course.cycle,
                    ).filter(id=cycle_course.id).exists()
                )
                DepartmentalExamAuthorizationService.require_course_responsibility(
                    user=user, cycle_course=cycle_course
                )
                self.client.force_login(user)
                self.assertEqual(self.client.get(cycle_list_url).status_code, 200)
                assigned = self.client.get(assigned_url)
                self.assertEqual(assigned.status_code, 200)
                self.assertNotContains(assigned, administration_url)

        for user in (global_role, campus_null_role, direct_global):
            with self.subTest(non_applicable_allow=user.username):
                self.assertNotIn(user.id, candidate_ids)
                self.assertFalse(
                    DepartmentalExamAuthorizationService.is_eligible_reviewer(
                        user=user,
                        tenant_id=self.tenant.id,
                        responsible_department=self.department_a,
                    )
                )
                cycle_course.reviewer = user
                cycle_course.save(update_fields=["reviewer", "updated_at"])
                self.assertFalse(
                    DepartmentalExamAuthorizationService.reviewer_visible_cycle_courses(
                        user=user,
                        cycle=cycle_course.cycle,
                    ).filter(id=cycle_course.id).exists()
                )
                self.client.force_login(user)
                self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
                self.assertEqual(self.client.get(assigned_url).status_code, 403)

        self.client.force_login(self.admin)
        rejected = self.client.post(
            administration_url,
            {
                "responsible_department": self.department_a.id,
                "reviewer_id": global_role.id,
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertIn("reviewer", rejected.context["form"].errors)

        denied_users = []
        for suffix, tenant, campus in (
            ("EXACT", self.tenant, self.campus_a),
            ("GLOBAL", None, None),
            ("CAMPUS_NULL", self.tenant, None),
        ):
            user = self._scoped_user(
                username=f"scope-review-deny-{suffix.lower()}",
                department=self.department_a,
                permissions=(
                    "admin_portal.access",
                    "departmental_exams.review_generate",
                ),
            )
            UserPermission.objects.create(
                user=user,
                permission=review_permission,
                grant_type=UserPermission.GrantType.DENY,
                tenant=tenant,
                campus=campus,
            )
            denied_users.append(user)

        exact_denied, global_deny, campus_null_deny = denied_users
        cycle_course.reviewer = exact_denied
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        self.assertFalse(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=exact_denied,
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            )
        )
        self.client.force_login(exact_denied)
        self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
        self.assertEqual(self.client.get(assigned_url).status_code, 403)
        with self.assertRaises(PermissionDenied):
            DepartmentalExamAuthorizationService.require_course_responsibility(
                user=exact_denied, cycle_course=cycle_course
            )

        for user in (global_deny, campus_null_deny):
            with self.subTest(non_applicable_deny=user.username):
                cycle_course.reviewer = user
                cycle_course.save(update_fields=["reviewer", "updated_at"])
                self.assertTrue(
                    DepartmentalExamAuthorizationService.is_eligible_reviewer(
                        user=user,
                        tenant_id=self.tenant.id,
                        responsible_department=self.department_a,
                    )
                )
                self.assertIn(
                    user.id,
                    DepartmentalExamAuthorizationService.eligible_reviewers(
                        tenant_id=self.tenant.id,
                        responsible_department=self.department_a,
                    ).values_list("id", flat=True),
                )
                self.client.force_login(user)
                self.assertEqual(self.client.get(cycle_list_url).status_code, 200)
                self.assertEqual(self.client.get(assigned_url).status_code, 200)
                DepartmentalExamAuthorizationService.require_course_responsibility(
                    user=user, cycle_course=cycle_course
                )

        no_ownership = self._scoped_user(
            username="scope-review-global-without-ownership",
            department=None,
            permissions=("admin_portal.access",),
        )
        add_review_role(no_ownership, "WITHOUT_OWNERSHIP", tenant=None, campus=None)
        cycle_course.reviewer = no_ownership
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        self.assertFalse(
            PermissionService.has_permission(
                no_ownership,
                review_permission.code,
                tenant_id=self.tenant.id,
                campus_id=self.campus_a.id,
            )
        )
        self.assertFalse(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=no_ownership,
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            )
        )
        self.client.force_login(no_ownership)
        self.assertEqual(self.client.get(assigned_url).status_code, 403)

        review_permission.is_active = False
        review_permission.save(update_fields=["is_active", "updated_at"])
        cycle_course.reviewer = exact
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        self.assertFalse(
            DepartmentalExamAuthorizationService.is_eligible_reviewer(
                user=exact,
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            )
        )
        self.assertNotIn(
            exact.id,
            DepartmentalExamAuthorizationService.eligible_reviewers(
                tenant_id=self.tenant.id,
                responsible_department=self.department_a,
            ).values_list("id", flat=True),
        )
        self.client.force_login(exact)
        self.assertEqual(self.client.get(assigned_url).status_code, 403)

    def test_inactive_responsible_department_is_hidden_without_changing_snapshot(self):
        _, cycle_course = self._grouped_course()
        configurer = self._configurer(username="inactive-department-configurer")
        reviewer = self._scoped_user(
            username="inactive-department-reviewer",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.review_generate",
            ),
        )
        cycle_course.reviewer = reviewer
        cycle_course.save(update_fields=["reviewer", "updated_at"])
        original_department_id = cycle_course.responsible_department_id
        cycle_list_url = reverse(
            "departmental_exams:cycle_course_list", args=[cycle_course.cycle_id]
        )
        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        administration_url = reverse(
            "departmental_exams:cycle_course_administration",
            args=[cycle_course.id],
        )

        self.department_a.is_active = False
        self.department_a.save(update_fields=["is_active", "updated_at"])

        self.assertFalse(
            DepartmentalExamAuthorizationService.configurable_departments(
                user=configurer, tenant_id=self.tenant.id
            ).exists()
        )
        for user in (configurer, reviewer):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(cycle_list_url).status_code, 403)
                assigned = self.client.get(assigned_url)
                self.assertEqual(assigned.status_code, 403)
                self.assertNotContains(
                    assigned, administration_url, status_code=403
                )
                self.assertEqual(self.client.get(administration_url).status_code, 403)

        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(assigned_url).status_code, 403)
        self.assertEqual(self.client.get(administration_url).status_code, 200)
        cycle_course.refresh_from_db()
        self.assertEqual(cycle_course.responsible_department_id, original_department_id)

        self.department_a.is_active = True
        self.department_a.save(update_fields=["is_active", "updated_at"])
        for user in (configurer, reviewer):
            with self.subTest(reactivated=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(cycle_list_url).status_code, 200)
                self.assertEqual(self.client.get(assigned_url).status_code, 200)

    def test_manager_configurer_reviewer_combinations_keep_row_authority_separate(self):
        self._offering(suffix="combination-primary")
        unrelated_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-COMBINATION-UNRELATED",
            title="Combination Unrelated",
            exam_department=self.department_b,
        )
        self._offering(
            course=unrelated_course,
            campus=self.campus_b,
            department=self.department_b,
            suffix="combination-unrelated",
        )
        cycle = self._create_cycle()
        row = CycleCourse.objects.get(cycle=cycle, course=self.course)
        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        administration_url = reverse(
            "departmental_exams:cycle_course_administration", args=[row.id]
        )

        manager_reviewer = self._scoped_user(
            username="combination-manager-reviewer",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.manage_cycles",
                "departmental_exams.review_generate",
            ),
        )
        configurer_reviewer = self._scoped_user(
            username="combination-configurer-reviewer",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            ),
        )
        manager_configurer = self._scoped_user(
            username="combination-manager-configurer",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.manage_cycles",
                "departmental_exams.configure",
            ),
        )
        all_three = self._scoped_user(
            username="combination-all-three",
            department=self.department_a,
            permissions=(
                "admin_portal.access",
                "departmental_exams.manage_cycles",
                "departmental_exams.configure",
                "departmental_exams.review_generate",
            ),
        )

        def visible_codes(user):
            return {
                node["item"].code
                for group in MenuService.get_menu_tree(
                    user,
                    portal="ADMIN",
                    tenant_id=self.tenant.id,
                    campus_id=self.campus_a.id,
                )
                for node in group["items"]
            }

        cases = (
            (
                manager_reviewer,
                {"DE_EXAM_CYCLES", "DE_EXAM_ASSIGNED_COURSES"},
                False,
            ),
            (configurer_reviewer, {"DE_EXAM_ASSIGNED_COURSES"}, True),
            (
                manager_configurer,
                {"DE_EXAM_CYCLES", "DE_EXAM_ASSIGNED_COURSES"},
                True,
            ),
            (
                all_three,
                {"DE_EXAM_CYCLES", "DE_EXAM_ASSIGNED_COURSES"},
                True,
            ),
        )
        for user, expected_menu, can_administer in cases:
            with self.subTest(user=user.username):
                row.reviewer = user if "reviewer" in user.username or user is all_three else None
                row.save(update_fields=["reviewer", "updated_at"])
                self.assertEqual(visible_codes(user), expected_menu)
                self.client.force_login(user)
                response = self.client.get(assigned_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [course.id for course in response.context["courses"]].count(row.id),
                    1,
                )
                self.assertNotContains(response, unrelated_course.code)
                if can_administer:
                    self.assertContains(response, administration_url)
                else:
                    self.assertNotContains(response, administration_url)

    def test_assigned_course_page_queries_stay_bounded_across_cycles(self):
        self._offering(suffix="assigned-query-configurer")
        reviewer_course = Course.objects.create(
            tenant=self.tenant,
            code="EX-ASSIGNED-QUERY-REVIEW",
            title="Assigned Query Reviewer Course",
            exam_department=self.department_b,
        )
        self._offering(
            course=reviewer_course,
            campus=self.campus_b,
            department=self.department_b,
            suffix="assigned-query-reviewer",
        )
        user = self._configurer(username="assigned-query-user")
        review_role = Role.objects.create(
            code="DE_TEST_ASSIGNED_QUERY_REVIEW",
            name="Assigned Query Review",
        )
        RolePermission.objects.create(
            role=review_role,
            permission=Permission.objects.get(code="departmental_exams.review_generate"),
        )
        for department in (self.department_a, self.department_b):
            UserRole.objects.create(
                user=user,
                role=review_role,
                tenant=self.tenant,
                campus=department.campus,
                department=department,
            )

        first_cycle = self._create_cycle()
        second_cycle = ExaminationCycleService.create_cycle(
            user=self.admin,
            tenant=self.tenant,
            academic_year=self.year,
            term=self.term,
            exam_period="FINAL",
        )
        expected_visible_ids = set()
        for cycle in (first_cycle, second_cycle):
            configurer_row = CycleCourse.objects.get(cycle=cycle, course=self.course)
            reviewer_row = CycleCourse.objects.get(cycle=cycle, course=reviewer_course)
            reviewer_row.reviewer = user
            reviewer_row.save(update_fields=["reviewer", "updated_at"])
            expected_visible_ids.update({configurer_row.id, reviewer_row.id})

        assigned_url = reverse("departmental_exams:assigned_course_examinations")
        self.client.force_login(user)
        with CaptureQueriesContext(connection) as small_query_context:
            small_response = self.client.get(assigned_url)
        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(
            {row.id for row in small_response.context["courses"]}, expected_visible_ids
        )

        parent, child = self._department_hierarchy()
        campus_c = Campus.objects.create(
            tenant=self.tenant,
            code="C",
            name="Campus C",
        )
        unrelated_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            code="ASSIGNED-UNRELATED",
            name="Assigned Unrelated",
        )
        unrelated_campus_department = Department.objects.create(
            tenant=self.tenant,
            campus=campus_c,
            code="ASSIGNED-OTHER-CAMPUS",
            name="Assigned Other Campus",
        )
        denied_department = Department.objects.create(
            tenant=self.tenant,
            campus=campus_c,
            code="ASSIGNED-DENIED",
            name="Assigned Denied",
        )
        denied_role = Role.objects.create(
            code="DE_TEST_ASSIGNED_QUERY_DENIED",
            name="Assigned Query Denied",
        )
        RolePermission.objects.create(
            role=denied_role,
            permission=Permission.objects.get(code="departmental_exams.configure"),
        )
        UserRole.objects.create(
            user=user,
            role=denied_role,
            tenant=self.tenant,
            campus=campus_c,
            department=denied_department,
        )
        UserPermission.objects.create(
            user=user,
            permission=Permission.objects.get(code="departmental_exams.configure"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=campus_c,
        )

        def add_rows(code, title, department, *, reviewer=False, visible=False):
            course = Course.objects.create(
                tenant=self.tenant,
                code=code,
                title=title,
                exam_department=department,
            )
            offering = self._offering(
                course=course,
                campus=department.campus,
                department=department,
                suffix=f"assigned-query-{code}",
            )
            for cycle in (first_cycle, second_cycle):
                row = CycleCourse.objects.create(
                    cycle=cycle,
                    course=course,
                    responsible_department=department,
                    reviewer=user if reviewer else None,
                )
                CycleCourseOffering.objects.create(
                    cycle_course=row,
                    offering=offering,
                    campus=department.campus,
                )
                if visible:
                    expected_visible_ids.add(row.id)

        add_rows(
            "EX-ASSIGNED-CONFIGURE",
            "Assigned Query Configure",
            self.department_a,
            visible=True,
        )
        add_rows(
            "EX-ASSIGNED-REVIEW",
            "Assigned Query Review",
            self.department_b,
            reviewer=True,
            visible=True,
        )
        add_rows(
            "EX-ASSIGNED-DUAL",
            "Assigned Query Dual",
            self.department_a,
            reviewer=True,
            visible=True,
        )
        add_rows("EX-ASSIGNED-PARENT", "Assigned Query Parent", parent, reviewer=True)
        add_rows("EX-ASSIGNED-CHILD", "Assigned Query Child", child, reviewer=True)
        add_rows(
            "EX-ASSIGNED-UNRELATED",
            "Assigned Query Unrelated",
            unrelated_department,
            reviewer=True,
        )
        add_rows(
            "EX-ASSIGNED-OTHER-CAMPUS",
            "Assigned Query Other Campus",
            unrelated_campus_department,
            reviewer=True,
        )
        add_rows(
            "EX-ASSIGNED-DENIED",
            "Assigned Query Denied",
            denied_department,
        )

        with CaptureQueriesContext(connection) as large_query_context:
            large_response = self.client.get(assigned_url)
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(
            {row.id for row in large_response.context["courses"]},
            expected_visible_ids,
        )
        self.assertLessEqual(
            len(large_query_context), len(small_query_context) + 2
        )

    def test_incomplete_course_setup_and_faculty_contribution_routes_are_unavailable(self):
        _, cycle_course = self._grouped_course()
        with self.assertRaises(NoReverseMatch):
            reverse("departmental_exams:course_setup", args=[cycle_course.id])
        with self.assertRaises(NoReverseMatch):
            reverse("departmental_exams:my_contributions")

        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.get(
                f"/admin-portal/departmental-exams/courses/{cycle_course.id}/"
            ).status_code,
            404,
        )
        faculty_user = self._scoped_user(
            username="faculty-route-check",
            department=self.department_a,
            permissions=("faculty_portal.access",),
        )
        self.client.force_login(faculty_user)
        self.assertEqual(self.client.get("/faculty/departmental-exams/").status_code, 404)

    def test_snapshot_validation_keeps_actual_offering_campus(self):
        offering = self._offering()
        cycle = ExaminationCycle.objects.create(
            tenant=self.tenant,
            academic_year=self.year,
            term=self.term,
            exam_period="MIDTERM",
            created_by=self.admin,
        )
        cycle_course = CycleCourse.objects.create(
            cycle=cycle,
            course=self.course,
            responsible_department=self.department_a,
        )
        with self.assertRaises(ValidationError):
            CycleCourseOffering(
                cycle_course=cycle_course,
                offering=offering,
                campus=self.campus_b,
            ).full_clean()
