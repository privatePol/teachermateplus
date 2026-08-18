from importlib import import_module

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.menu import MenuService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class PlanningReadinessSeedTests(TestCase):
    def test_permissions_and_view_only_menu_are_seeded_without_automatic_grants(self):
        permission_codes = {
            "departmental_exams.view_planning_readiness",
            "departmental_exams.print_planning_readiness",
        }
        self.assertEqual(
            set(Permission.objects.filter(code__in=permission_codes).values_list("code", flat=True)),
            permission_codes,
        )
        self.assertFalse(RolePermission.objects.filter(permission__code__in=permission_codes).exists())
        self.assertFalse(UserPermission.objects.filter(permission__code__in=permission_codes).exists())
        item = MenuItem.objects.get(portal="ADMIN", code="DE_EXAM_PLANNING_READINESS")
        assigned = MenuItem.objects.get(portal="ADMIN", code="DE_EXAM_ASSIGNED_COURSES")
        contributor = MenuItem.objects.get(portal="ADMIN", code="DE_EXAM_CONTRIBUTOR_MONITORING")
        self.assertEqual(item.label, "Planning & Readiness")
        self.assertEqual(item.route_name, "departmental_exams:planning_readiness")
        self.assertGreater(item.sort_order, assigned.sort_order)
        self.assertLess(item.sort_order, contributor.sort_order)
        self.assertEqual(
            set(item.menuitempermission_set.values_list("permission__code", flat=True)),
            {"departmental_exams.view_planning_readiness"},
        )


class PlanningReadinessNavigationMigrationSafetyTests(TestCase):
    def setUp(self):
        self.migration = import_module(
            "apps.navigation.migrations.0024_seed_planning_readiness_menu"
        )
        self.group = MenuGroup.objects.get(portal="ADMIN", code="DEPARTMENTAL_EXAMS")
        self.view_permission = Permission.objects.get(
            code="departmental_exams.view_planning_readiness"
        )
        MenuItemPermission.objects.filter(
            menu_item__portal="ADMIN",
            menu_item__code="DE_EXAM_PLANNING_READINESS",
        ).delete()
        MenuItem.objects.filter(
            portal="ADMIN",
            code="DE_EXAM_PLANNING_READINESS",
        ).delete()

    def test_normal_forward_and_reverse_remove_only_the_intended_item(self):
        self.migration.seed_menu(django_apps, None)
        self.migration.seed_menu(django_apps, None)
        item = MenuItem.objects.get(
            menu_group=self.group,
            portal="ADMIN",
            code="DE_EXAM_PLANNING_READINESS",
        )
        self.assertEqual(
            MenuItemPermission.objects.filter(
                menu_item=item,
                permission=self.view_permission,
            ).count(),
            1,
        )

        self.migration.unseed_menu(django_apps, None)

        self.assertFalse(MenuItem.objects.filter(pk=item.pk).exists())

    def test_wrong_group_collision_is_neither_hijacked_nor_reversed(self):
        other_group = MenuGroup.objects.create(
            portal="ADMIN",
            code="OTHER_PLANNING_GROUP",
            label="Other Planning Group",
        )
        item = MenuItem.objects.create(
            menu_group=other_group,
            portal="ADMIN",
            code="DE_EXAM_PLANNING_READINESS",
            label="Unrelated Planning Item",
            route_name="admin_portal:dashboard",
            sort_order=999,
        )
        unrelated_permission = Permission.objects.create(
            code="unrelated_planning.read",
            module="unrelated_planning",
            action="read",
        )
        link = MenuItemPermission.objects.create(
            menu_item=item,
            permission=unrelated_permission,
        )

        self.migration.seed_menu(django_apps, None)
        self.migration.unseed_menu(django_apps, None)

        item.refresh_from_db()
        self.assertEqual(item.menu_group_id, other_group.id)
        self.assertEqual(item.label, "Unrelated Planning Item")
        self.assertEqual(item.route_name, "admin_portal:dashboard")
        self.assertTrue(MenuItemPermission.objects.filter(pk=link.pk).exists())
        self.assertFalse(
            MenuItemPermission.objects.filter(
                menu_item=item,
                permission=self.view_permission,
            ).exists()
        )

    def test_reverse_preserves_unrelated_permission_link_on_intended_item(self):
        self.migration.seed_menu(django_apps, None)
        item = MenuItem.objects.get(
            menu_group=self.group,
            portal="ADMIN",
            code="DE_EXAM_PLANNING_READINESS",
        )
        unrelated_permission = Permission.objects.create(
            code="planning_readiness.unrelated",
            module="planning_readiness",
            action="unrelated",
        )
        link = MenuItemPermission.objects.create(
            menu_item=item,
            permission=unrelated_permission,
        )

        self.migration.unseed_menu(django_apps, None)

        self.assertTrue(MenuItem.objects.filter(pk=item.pk).exists())
        self.assertTrue(MenuItemPermission.objects.filter(pk=link.pk).exists())
        self.assertFalse(
            MenuItemPermission.objects.filter(
                menu_item=item,
                permission=self.view_permission,
            ).exists()
        )


class PlanningReadinessTests(TestCase):
    def setUp(self):
        for code, module, action in (
            ("admin_portal.access", "admin_portal", "access"),
            ("dashboard.read", "dashboard", "read"),
            ("roles.update", "roles", "update"),
            (
                "departmental_exams.view_planning_readiness",
                "departmental_exams",
                "view_planning_readiness",
            ),
            (
                "departmental_exams.print_planning_readiness",
                "departmental_exams",
                "print_planning_readiness",
            ),
        ):
            Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action, "is_active": True},
            )
        self.tenant = Tenant.objects.create(code="PLAN", name="Planning College")
        self.campus_a = Campus.objects.create(tenant=self.tenant, code="A", name="Alpha Campus")
        self.campus_b = Campus.objects.create(tenant=self.tenant, code="B", name="Beta Campus")
        self.department_a = Department.objects.create(
            tenant=self.tenant, campus=self.campus_a, code="ACC", name="Accountancy"
        )
        self.department_b = Department.objects.create(
            tenant=self.tenant, campus=self.campus_b, code="IT", name="Information Technology"
        )
        self.child_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            parent=self.department_a,
            code="ACC-CHILD",
            name="Accountancy Child",
        )
        self.program_a = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
            code="BSA",
            name="BSA",
        )
        self.program_b = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus_b,
            department=self.department_b,
            code="BSIT",
            name="BSIT",
        )
        self.year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2026",
            name="AY 2026-2027",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.year,
            code="T1",
            name="First Term",
        )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.course_a = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_b,
            exam_department=self.department_a,
            code="AE103-MS",
            title="Management Science",
        )
        self.offering_a = self.make_offering(
            course=self.course_a,
            campus=self.campus_a,
            department=self.department_a,
            program=self.program_a,
            section_code="BSA-1A",
        )
        self.course_b = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus_b,
            department=self.department_a,
            exam_department=self.department_b,
            code="IT101",
            title="Introduction to IT",
        )
        self.offering_b = self.make_offering(
            course=self.course_b,
            campus=self.campus_b,
            department=self.department_b,
            program=self.program_b,
            section_code="BSIT-1A",
        )
        self.unassigned_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
            exam_department=None,
            code="GEN101",
            title="General Education",
        )
        self.unassigned_offering = self.make_offering(
            course=self.unassigned_course,
            campus=self.campus_a,
            department=self.department_a,
            program=self.program_a,
            section_code="GEN-1A",
        )
        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        self.active_faculty = self.make_user("active-faculty", active=True, usable_password=True)
        UserRole.objects.create(
            user=self.active_faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
        )
        self.accepted_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            offering=self.offering_a,
            faculty_user=self.active_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        self.inactive_faculty = self.make_user("inactive-faculty", active=False, usable_password=False)
        self.incomplete_acceptance_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            offering=self.offering_a,
            faculty_user=self.inactive_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        )
        self.make_enrollment(self.offering_a, "S-001", active=True, status=Enrollment.Status.ACTIVE)
        self.make_enrollment(self.offering_a, "S-002", active=True, status=Enrollment.Status.ACTIVE)
        self.make_enrollment(self.offering_a, "S-003", active=False, status=Enrollment.Status.ACTIVE)
        self.make_enrollment(self.offering_a, "S-004", active=True, status=Enrollment.Status.DRP)
        self.view_url = reverse("departmental_exams:planning_readiness")
        self.print_url = reverse("departmental_exams:planning_readiness_print")

    def make_user(self, username, *, active=True, usable_password=True):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.edu",
            password="Pass123!" if usable_password else None,
            default_tenant=self.tenant,
            default_campus=self.campus_a,
            default_department=self.department_a,
            is_active=active,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        return user

    def grant(self, user, *, campus, department, permissions, suffix):
        role = Role.objects.create(code=f"PLAN_{suffix}", name=f"Planning {suffix}")
        for permission_code in permissions:
            RolePermission.objects.create(
                role=role,
                permission=Permission.objects.get(code=permission_code),
            )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=campus,
            department=department,
        )
        return role

    def reporter(self, username="reporter", *, department=None, campus=None, permissions=None):
        user = self.make_user(username)
        role_department = (
            None
            if department == "GLOBAL"
            else self.department_a if department is None else department
        )
        self.grant(
            user,
            campus=campus or self.campus_a,
            department=role_department,
            permissions=permissions
            or (
                "admin_portal.access",
                "departmental_exams.view_planning_readiness",
                "departmental_exams.print_planning_readiness",
            ),
            suffix=username.upper().replace("-", "_")[:35],
        )
        return user

    def make_offering(self, *, course, campus, department, program, section_code, status=CourseOffering.Status.OPEN, active=True):
        section = Section.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            code=section_code,
            name=section_code,
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
            status=status,
            is_active=active,
        )

    def make_enrollment(self, offering, student_no, *, active, status):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            department=offering.department,
            program=offering.program,
            student_no=student_no,
            last_name=student_no,
            first_name="Student",
        )
        return Enrollment.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            academic_year=self.year,
            term=self.term,
            student=student,
            course_offering=offering,
            enrollment_status=status,
            is_active=active,
        )

    def get_as(self, user, url=None, params=None):
        client = Client()
        client.force_login(user)
        if params is None:
            return client.get(url or self.view_url)
        return client.get(url or self.view_url, params)

    @staticmethod
    def report_signature(response):
        return [
            (
                group["department_id"],
                [
                    (
                        course["course"].id,
                        [
                            (row["offering_id"], row["assignment_id"])
                            for row in course["rows"]
                        ],
                    )
                    for course in group["courses"]
                ],
            )
            for group in response.context["groups"]
        ]

    def generated_print_response(self, user, screen_response):
        query = screen_response.context["filter_query"]
        url = self.print_url + (f"?{query}" if query else "")
        return self.get_as(user, url)

    def test_menu_view_and_print_permissions_remain_independent(self):
        view_only = self.reporter(
            "view-only",
            permissions=("admin_portal.access", "departmental_exams.view_planning_readiness"),
        )
        print_only = self.reporter(
            "print-only",
            permissions=("admin_portal.access", "departmental_exams.print_planning_readiness"),
        )
        view_codes = {
            node["item"].code
            for group in MenuService.get_menu_tree(
                view_only, portal="ADMIN", tenant_id=self.tenant.id, campus_id=self.campus_a.id
            )
            for node in group["items"]
        }
        print_codes = {
            node["item"].code
            for group in MenuService.get_menu_tree(
                print_only, portal="ADMIN", tenant_id=self.tenant.id, campus_id=self.campus_a.id
            )
            for node in group["items"]
        }
        self.assertIn("DE_EXAM_PLANNING_READINESS", view_codes)
        self.assertNotIn("DE_EXAM_PLANNING_READINESS", print_codes)
        view_response = self.get_as(view_only)
        self.assertEqual(view_response.status_code, 200)
        self.assertIn("private", view_response["Cache-Control"])
        self.assertIn("no-store", view_response["Cache-Control"])
        self.assertNotContains(view_response, "Print / Printer-Friendly View")
        self.assertEqual(self.get_as(view_only, self.print_url).status_code, 403)
        self.assertEqual(self.get_as(print_only).status_code, 403)
        denied_print = self.get_as(print_only, self.print_url)
        self.assertEqual(denied_print.status_code, 403)
        self.assertNotIn("overall", getattr(denied_print, "context", {}) or {})

    def test_permissions_are_assignable_in_existing_role_administration_without_role_grants(self):
        admin = self.make_user("planning-role-admin")
        self.grant(
            admin,
            campus=self.campus_a,
            department=self.department_a,
            permissions=("admin_portal.access", "roles.update"),
            suffix="ROLE_ADMIN",
        )
        target_role = Role.objects.create(code="PLANNING_TARGET", name="Planning Target")
        response = self.get_as(
            admin,
            reverse("admin_portal:role_permissions", args=[target_role.id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "departmental_exams.view_planning_readiness")
        self.assertContains(response, "departmental_exams.print_planning_readiness")
        self.assertFalse(
            target_role.role_permissions.filter(
                permission__code__startswith="departmental_exams."
            ).exists()
        )
        self.assertFalse(Role.objects.filter(code="ACADEMIC_SUPERVISOR").exists())

    def test_specific_department_uses_course_exam_department_and_hides_unassigned(self):
        user = self.reporter()
        response = self.get_as(user)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.course_a.code)
        self.assertContains(response, "ACC — Accountancy")
        self.assertNotContains(response, self.course_b.code)
        self.assertNotContains(response, self.unassigned_course.code)
        self.assertNotContains(response, self.course_a.department.name)
        self.assertEqual(response.context["overall"]["courses"], 1)

    def test_null_department_scope_shows_all_and_unassigned_for_exact_campus(self):
        user = self.reporter("global-a", department="GLOBAL")
        assignment = UserRole.objects.get(user=user, role__code="PLAN_GLOBAL_A")
        assignment.department = None
        assignment.save(update_fields=["department"])
        response = self.get_as(user)
        self.assertContains(response, self.course_a.code)
        self.assertContains(response, self.unassigned_course.code)
        self.assertContains(response, "UNASSIGNED EXAM DEPARTMENT")
        self.assertNotContains(response, self.course_b.code)
        self.assertEqual(response.context["overall"]["courses"], 2)
        self.assertEqual(response.context["overall"]["no_faculty"], 1)

    def test_multiple_exact_campus_grants_union_and_direct_deny_wins(self):
        user = self.reporter("multi", department="GLOBAL")
        first = UserRole.objects.get(user=user, role__code="PLAN_MULTI")
        first.department = None
        first.save(update_fields=["department"])
        self.grant(
            user,
            campus=self.campus_b,
            department=None,
            permissions=("departmental_exams.view_planning_readiness", "departmental_exams.print_planning_readiness"),
            suffix="MULTI_B",
        )
        response = self.get_as(user)
        self.assertContains(response, self.course_a.code)
        self.assertContains(response, self.course_b.code)
        UserPermission.objects.create(
            user=user,
            permission=Permission.objects.get(code="departmental_exams.view_planning_readiness"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus_b,
        )
        denied = self.get_as(user)
        self.assertContains(denied, self.course_a.code)
        self.assertNotContains(denied, self.course_b.code)

    def test_permission_and_department_are_not_cross_combined_and_hierarchy_does_not_expand(self):
        user = self.reporter("no-cross")
        role_without_permission = Role.objects.create(code="PLAN_SCOPE_ONLY", name="Scope only")
        UserRole.objects.create(
            user=user,
            role=role_without_permission,
            tenant=self.tenant,
            campus=self.campus_b,
            department=self.department_b,
        )
        child_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.department_a,
            exam_department=self.child_department,
            code="ACC-CHILD-101",
            title="Child Department Course",
        )
        self.make_offering(
            course=child_course,
            campus=self.campus_a,
            department=self.department_a,
            program=self.program_a,
            section_code="ACC-CHILD-1",
        )
        response = self.get_as(user)
        self.assertContains(response, self.course_a.code)
        self.assertNotContains(response, self.course_b.code)
        self.assertNotContains(response, child_course.code)

    def test_direct_allow_is_global_department_scope_but_null_permission_scope_is_not_wildcard(self):
        user = self.make_user("direct-allow")
        self.grant(
            user,
            campus=self.campus_a,
            department=self.department_a,
            permissions=("admin_portal.access",),
            suffix="DIRECT_PORTAL",
        )
        UserPermission.objects.create(
            user=user,
            permission=Permission.objects.get(code="departmental_exams.view_planning_readiness"),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus_a,
        )
        response = self.get_as(user)
        self.assertContains(response, self.course_a.code)
        self.assertContains(response, self.unassigned_course.code)
        UserPermission.objects.filter(
            user=user, permission__code="departmental_exams.view_planning_readiness"
        ).update(campus=None)
        self.assertEqual(self.get_as(user).status_code, 403)

    def test_null_scoped_view_permission_does_not_render_the_navigation_item(self):
        user = self.make_user("null-menu")
        self.grant(
            user,
            campus=self.campus_a,
            department=self.department_a,
            permissions=("admin_portal.access", "dashboard.read"),
            suffix="NULL_MENU_PORTAL",
        )
        role = Role.objects.create(code="PLAN_NULL_MENU_VIEW", name="Null menu view")
        RolePermission.objects.create(
            role=role,
            permission=Permission.objects.get(code="departmental_exams.view_planning_readiness"),
        )
        UserRole.objects.create(user=user, role=role, tenant=None, campus=None, department=None)
        client = Client()
        client.force_login(user)
        response = client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Planning &amp; Readiness")
        self.assertEqual(client.get(self.view_url).status_code, 403)

    def test_superuser_still_requires_an_explicit_exact_report_scope(self):
        user = self.make_user("explicit-superuser")
        user.is_superuser = True
        user.is_staff = True
        user.save(update_fields=["is_superuser", "is_staff"])
        self.assertEqual(self.get_as(user).status_code, 403)
        UserPermission.objects.create(
            user=user,
            permission=Permission.objects.get(code="departmental_exams.view_planning_readiness"),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus_a,
        )
        response = self.get_as(user)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.unassigned_course.code)

    def test_tenant_isolation_and_forged_filters_return_no_rows(self):
        other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        other_campus = Campus.objects.create(tenant=other_tenant, code="O", name="Secret Campus")
        user = self.reporter("isolated", department="GLOBAL")
        assignment = UserRole.objects.get(user=user, role__code="PLAN_ISOLATED")
        assignment.department = None
        assignment.save(update_fields=["department"])
        response = self.get_as(user, params={"campus": other_campus.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["overall"]["courses"], 0)
        self.assertNotContains(response, "Secret Campus")
        forged_department = self.get_as(user, params={"exam_department": self.department_b.id})
        self.assertEqual(forged_department.context["overall"]["courses"], 0)
        self.assertNotContains(forged_department, self.course_b.code)

    def test_invalid_exam_department_remains_fail_closed_in_generated_print(self):
        user = self.reporter("invalid-department-print")
        screen = self.get_as(user, params={"exam_department": self.department_b.id})
        self.assertEqual(screen.context["overall"]["courses"], 0)
        self.assertIn(
            f"exam_department={self.department_b.id}",
            screen.context["filter_query"],
        )

        printed = self.generated_print_response(user, screen)

        self.assertEqual(printed.status_code, 200)
        self.assertEqual(printed.context["overall"], screen.context["overall"])
        self.assertEqual(self.report_signature(printed), self.report_signature(screen))
        self.assertNotContains(printed, self.course_a.code)
        self.assertNotContains(printed, self.course_b.code)

    def test_unauthorized_campus_remains_fail_closed_without_hidden_counts_in_print(self):
        user = self.reporter("unauthorized-campus-print")
        screen = self.get_as(user, params={"campus": self.campus_b.id})
        self.assertEqual(screen.context["overall"]["courses"], 0)
        self.assertIn(f"campus={self.campus_b.id}", screen.context["filter_query"])

        printed = self.generated_print_response(user, screen)

        self.assertEqual(printed.status_code, 200)
        self.assertEqual(printed.context["overall"], screen.context["overall"])
        self.assertEqual(self.report_signature(printed), self.report_signature(screen))
        self.assertTrue(all(value == 0 for value in printed.context["overall"].values()))
        self.assertNotContains(printed, self.course_a.code)
        self.assertNotContains(printed, self.course_b.code)
        self.assertNotContains(printed, self.campus_b.name)

    def test_malformed_status_filters_remain_fail_closed_in_generated_print(self):
        user = self.reporter("malformed-status-print")
        for name, value in (
            ("assignment_status", "accepted-or-anything"),
            ("faculty_active", "MAYBE"),
            ("account_status", "ACTIVE_OR_PENDING"),
        ):
            with self.subTest(name=name):
                screen = self.get_as(user, params={name: value})
                self.assertEqual(screen.context["overall"]["courses"], 0)
                self.assertIn(name, screen.context["filter_query"])
                printed = self.generated_print_response(user, screen)
                self.assertEqual(printed.status_code, 200)
                self.assertEqual(printed.context["overall"], screen.context["overall"])
                self.assertEqual(
                    self.report_signature(printed),
                    self.report_signature(screen),
                )

    def test_active_open_base_and_no_faculty_operational_row(self):
        user = self.reporter("active-open", department="GLOBAL")
        assignment = UserRole.objects.get(user=user, role__code="PLAN_ACTIVE_OPEN")
        assignment.department = None
        assignment.save(update_fields=["department"])
        closed_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            exam_department=self.department_a,
            code="CLOSED101",
            title="Closed Course",
        )
        self.make_offering(
            course=closed_course,
            campus=self.campus_a,
            department=self.department_a,
            program=self.program_a,
            section_code="CLOSED-1",
            status=CourseOffering.Status.CLOSED,
        )
        inactive_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            exam_department=self.department_a,
            code="INACTIVE101",
            title="Inactive Offering Course",
        )
        self.make_offering(
            course=inactive_course,
            campus=self.campus_a,
            department=self.department_a,
            program=self.program_a,
            section_code="INACTIVE-1",
            active=False,
        )
        response = self.get_as(user)
        self.assertNotContains(response, closed_course.code)
        self.assertNotContains(response, inactive_course.code)
        self.assertContains(response, self.unassigned_course.code)
        self.assertContains(response, "Unassigned")
        self.assertContains(response, "No Faculty Assigned")
        self.assertContains(response, "N/A")

    def test_enrollment_assignment_and_account_definitions_and_totals(self):
        user = self.reporter("definitions")
        response = self.get_as(user)
        overall = response.context["overall"]
        self.assertEqual(overall["offerings"], 1)
        self.assertEqual(overall["enrolled"], 2)
        self.assertEqual(overall["faculty"], 2)
        self.assertEqual(overall["accepted"], 1)
        self.assertEqual(overall["not_accepted"], 1)
        self.assertEqual(overall["inactive_faculty"], 1)
        self.assertEqual(overall["not_activated"], 1)
        course_summary = response.context["groups"][0]["courses"][0]["summary"]
        self.assertEqual(course_summary["enrolled"], 2)
        self.assertEqual(course_summary["offerings"], 1)
        rows = response.context["groups"][0]["courses"][0]["rows"]
        self.assertEqual({row["assignment_status_label"] for row in rows}, {"Accepted", "Not Accepted"})
        self.assertEqual({row["faculty_active_label"] for row in rows}, {"Yes", "No"})
        self.assertEqual({row["account_status_label"] for row in rows}, {"Activated", "Not Activated"})

    def test_faculty_active_requires_a_role_covering_the_offering_department(self):
        faculty = self.make_user("mis-scoped-faculty")
        UserRole.objects.create(
            user=faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus_a,
            department=self.child_department,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus_a,
            offering=self.offering_a,
            faculty_user=faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
        )
        user = self.reporter("faculty-role-scope")
        response = self.get_as(user)
        row = next(
            row
            for row in response.context["groups"][0]["courses"][0]["rows"]
            if row["faculty_user_id"] == faculty.id
        )
        self.assertEqual(row["faculty_active_label"], "No")
        self.assertEqual(row["account_status_label"], "Activated")
        UserRole.objects.filter(user=faculty, role=self.faculty_role).update(department=None)
        faculty.set_unusable_password()
        faculty.save(update_fields=["password"])
        response = self.get_as(user)
        row = next(
            row
            for row in response.context["groups"][0]["courses"][0]["rows"]
            if row["faculty_user_id"] == faculty.id
        )
        self.assertEqual(row["faculty_active_label"], "Yes")
        self.assertEqual(row["account_status_label"], "Not Activated")

    def test_filters_are_derived_and_preserved_in_print(self):
        user = self.reporter("filters")
        params = {
            "academic_year": self.year.id,
            "term": self.term.id,
            "campus": self.campus_a.id,
            "exam_department": self.department_a.id,
            "assignment_status": "ACCEPTED",
            "faculty_active": "YES",
            "account_status": "YES",
            "ignored_parameter": "must-not-propagate",
        }
        response = self.get_as(user, params=params)
        self.assertEqual(response.context["overall"]["accepted"], 1)
        self.assertEqual(response.context["overall"]["not_accepted"], 0)
        self.assertContains(response, "Print / Printer-Friendly View")
        self.assertNotIn("ignored_parameter", response.context["filter_query"])
        print_response = self.generated_print_response(user, response)
        self.assertEqual(print_response.status_code, 200)
        self.assertEqual(print_response.context["overall"], response.context["overall"])
        self.assertEqual(
            self.report_signature(print_response),
            self.report_signature(response),
        )
        self.assertContains(print_response, "Planning College")
        self.assertContains(print_response, "AY 2026-2027")
        for header in (
            "Faculty",
            "Campus",
            "Section",
            "Enrolled",
            "Assignment Status",
            "Faculty Active",
            "TMP Account Status",
        ):
            self.assertContains(print_response, f"<th>{header}</th>", html=True)
        self.assertIn("private", print_response["Cache-Control"])
        self.assertIn("no-store", print_response["Cache-Control"])

    def test_print_scope_is_intersection_of_view_and_print_scopes(self):
        user = self.make_user("split-print")
        self.grant(
            user,
            campus=self.campus_a,
            department=self.department_a,
            permissions=("admin_portal.access", "departmental_exams.view_planning_readiness"),
            suffix="SPLIT_VIEW",
        )
        self.grant(
            user,
            campus=self.campus_b,
            department=self.department_b,
            permissions=("departmental_exams.print_planning_readiness",),
            suffix="SPLIT_PRINT",
        )
        page = self.get_as(user)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Print / Printer-Friendly View")
        self.assertEqual(self.get_as(user, self.print_url).status_code, 403)

    def test_print_direct_deny_blocks_before_report_context(self):
        user = self.reporter("print-denied")
        UserPermission.objects.create(
            user=user,
            permission=Permission.objects.get(code="departmental_exams.print_planning_readiness"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus_a,
        )
        page = self.get_as(user)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Print / Printer-Friendly View")
        denied = self.get_as(user, self.print_url)
        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("overall", getattr(denied, "context", {}) or {})
