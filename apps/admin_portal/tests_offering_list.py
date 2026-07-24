from datetime import date
import re

from django.conf import settings
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.enrollment.models import Enrollment
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class OfferingListEnhancementTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="T1", name="Tenant One")
        self.campus = Campus.objects.create(tenant=self.tenant, code="C1", name="Campus One")
        self.department = Department.objects.create(tenant=self.tenant, campus=self.campus, code="D1", name="Department")
        self.program = Program.objects.create(
            tenant=self.tenant, campus=self.campus, department=self.department, code="P1", name="Program"
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant, code="AY1", name="Academic Year", start_date=date(2026, 6, 1), end_date=date(2027, 5, 31)
        )
        self.term = Term.objects.create(
            tenant=self.tenant, academic_year=self.academic_year, code="T1", name="First Term", sequence_no=1
        )
        self.course = Course.objects.create(tenant=self.tenant, code="COURSE1", title="Course One")
        self.offering = self.make_offering("SEC-1")
        self.other_offering = self.make_offering("SEC-2")
        self.user = self.make_user("offering_admin", "Admin", "User")
        role = Role.objects.create(code="OFFERING_ADMIN", name="Offering Admin")
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
            ("offerings.read", "offerings", "read"),
        ]:
            permission = Permission.objects.create(code=code, module=module, action=action)
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(user=self.user, role=role, tenant=self.tenant, campus=self.campus, department=self.department)
        self.client.force_login(self.user)

    def make_offering(self, section_code):
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code=section_code,
            name=section_code,
        )
        return CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=section,
        )

    def make_student(self, suffix):
        return Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no=f"STU-{suffix}",
            first_name="Student",
            last_name=suffix,
        )

    def add_active_enrollments(self, offering, count, prefix):
        enrollments = []
        for index in range(count):
            student = Student.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                department=offering.department,
                program=offering.program,
                student_no=f"{prefix}-{index}",
                first_name="Student",
                last_name=f"{prefix}-{index}",
            )
            enrollments.append(
                Enrollment.objects.create(
                    tenant=offering.tenant,
                    campus=offering.campus,
                    academic_year=offering.academic_year,
                    term=offering.term,
                    student=student,
                    course_offering=offering,
                    enrollment_status=Enrollment.Status.ACTIVE,
                )
            )
        return enrollments

    def add_active_faculty_assignment(self, offering, suffix, *, is_primary=False):
        normalized_suffix = suffix.lower().replace("-", "_")
        faculty = User.objects.create(
            username=f"faculty_{normalized_suffix}",
            email=f"faculty_{normalized_suffix}@example.com",
            first_name="Faculty",
            last_name=suffix,
            default_tenant=offering.tenant,
            default_campus=offering.campus,
            default_department=offering.department,
        )
        assignment = FacultyAssignment.objects.create(
            tenant=offering.tenant,
            campus=offering.campus,
            offering=offering,
            faculty_user=faculty,
            is_primary=is_primary,
            is_active=True,
        )
        return assignment

    def make_user(self, username, first_name, last_name):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            first_name=first_name,
            last_name=last_name,
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def offering_row(self, response, offering):
        return next(row for row in response.context["page_obj"].object_list if row.id == offering.id)

    def offering_html(self, response, offering):
        row_match = re.search(
            r"<tr>(?:(?!</tr>).)*<td>"
            + re.escape(offering.section.code)
            + r"</td>(?:(?!</tr>).)*</tr>",
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(row_match)
        return row_match.group(0)

    def test_enrolled_count_uses_only_active_enrollments_for_the_exact_offering(self):
        Enrollment.objects.create(
            tenant=self.tenant, campus=self.campus, academic_year=self.academic_year, term=self.term,
            student=self.make_student("ACTIVE"), course_offering=self.offering, enrollment_status=Enrollment.Status.ACTIVE,
        )
        for suffix, status, is_active in [
            ("DROP", Enrollment.Status.DRP, True),
            ("WITHDRAWN", Enrollment.Status.W, True),
            ("INCOMPLETE", Enrollment.Status.INC, True),
            ("INACTIVE", Enrollment.Status.ACTIVE, False),
        ]:
            Enrollment.objects.create(
                tenant=self.tenant, campus=self.campus, academic_year=self.academic_year, term=self.term,
                student=self.make_student(suffix), course_offering=self.offering, enrollment_status=status, is_active=is_active,
            )
        Enrollment.objects.create(
            tenant=self.tenant, campus=self.campus, academic_year=self.academic_year, term=self.term,
            student=self.make_student("OTHER"), course_offering=self.other_offering, enrollment_status=Enrollment.Status.ACTIVE,
        )

        response = self.client.get(reverse("admin_portal:offering_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.offering_row(response, self.offering).enrolled_count, 1)
        self.assertEqual(self.offering_row(response, self.other_offering).enrolled_count, 1)
        self.assertContains(response, "Enrolled")

    def test_faculty_assignments_are_prefetched_ordered_and_status_aware(self):
        unassigned_offering = self.make_offering("SEC-3")
        pending = self.make_user("pending", "Ana", "Zulu")
        primary = self.make_user("primary", "Bert", "Alpha")
        inactive = self.make_user("inactive", "Cara", "Ignored")
        FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus, offering=self.offering, faculty_user=pending,
            response_status=FacultyAssignment.ResponseStatus.PENDING,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus, offering=self.offering, faculty_user=primary,
            is_primary=True, response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=primary, accepted_at=timezone.now(),
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus, offering=self.offering, faculty_user=inactive, is_active=False,
        )
        other_faculty = self.make_user("other_faculty", "Drew", "Other")
        FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus, offering=self.other_offering, faculty_user=other_faculty,
        )

        response = self.client.get(reverse("admin_portal:offering_list"))
        content = response.content.decode()
        assignment_names = [
            assignment.faculty_user.full_name
            for assignment in self.offering_row(response, self.offering).offering_faculty_assignments
        ]

        self.assertContains(response, "Faculty Assigned")
        self.assertLess(content.index("Bert Alpha"), content.index("Ana Zulu"))
        self.assertIn("Ana Zulu (Pending)", content)
        self.assertNotIn("Bert Alpha (Accepted)", content)
        self.assertNotIn("Cara Ignored", content)
        self.assertIn('<span class="text-muted">---</span>', content)
        self.assertEqual(assignment_names, ["Bert Alpha", "Ana Zulu"])
        self.assertEqual(self.offering_row(response, unassigned_offering).offering_faculty_assignments, [])

    def test_unassigned_filter_uses_only_active_faculty_assignments(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        accepted_faculty = self.make_user("accepted_faculty", "Accepted", "Faculty")
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=accepted_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=accepted_faculty,
            accepted_at=timezone.now(),
        )
        self.add_active_faculty_assignment(self.other_offering, "PENDING")
        active_unassigned = self.make_offering("ACTIVE-UNASSIGNED")
        only_inactive_assignment = self.make_offering("INACTIVE-ASSIGNMENT")
        inactive_faculty = self.make_user("inactive_filter_faculty", "Inactive", "Faculty")
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=only_inactive_assignment,
            faculty_user=inactive_faculty,
            is_active=False,
        )
        inactive_unassigned = self.make_offering("INACTIVE-UNASSIGNED")
        inactive_unassigned.is_active = False
        inactive_unassigned.save(update_fields=["is_active", "updated_at"])
        inactive_assigned = self.make_offering("INACTIVE-ASSIGNED")
        inactive_assigned.is_active = False
        inactive_assigned.save(update_fields=["is_active", "updated_at"])
        self.add_active_faculty_assignment(inactive_assigned, "INACTIVE-LIST")
        url = reverse("admin_portal:offering_list")

        unfiltered_response = self.client.get(url)
        self.assertEqual(unfiltered_response.status_code, 200)
        self.assertSetEqual(
            {row.id for row in unfiltered_response.context["active_page_obj"].object_list},
            {
                self.offering.id,
                self.other_offering.id,
                active_unassigned.id,
                only_inactive_assignment.id,
            },
        )
        self.assertSetEqual(
            {row.id for row in unfiltered_response.context["inactive_page_obj"].object_list},
            {inactive_unassigned.id, inactive_assigned.id},
        )

        response = self.client.get(url, {"unassigned": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertSetEqual(
            {row.id for row in response.context["active_page_obj"].object_list},
            {active_unassigned.id, only_inactive_assignment.id},
        )
        self.assertSetEqual(
            {row.id for row in response.context["inactive_page_obj"].object_list},
            {inactive_unassigned.id},
        )
        self.assertTrue(response.context["unassigned"])
        self.assertIn('name="unassigned" value="1" id="unassigned" checked', response.content.decode())
        self.assertContains(response, "View all unassigned course offerings")

    def test_unassigned_display_uses_the_existing_enrolled_count_annotation(self):
        assigned_offering = self.make_offering("ASSIGNED-DISPLAY")
        self.add_active_faculty_assignment(assigned_offering, "DISPLAY")
        self.add_active_enrollments(self.other_offering, 1, "ENROLLED-UNASSIGNED")

        response = self.client.get(reverse("admin_portal:offering_list"))

        zero_enrollment_html = self.offering_html(response, self.offering)
        enrolled_html = self.offering_html(response, self.other_offering)
        assigned_html = self.offering_html(response, assigned_offering)
        self.assertIn('<span class="text-muted">---</span>', zero_enrollment_html)
        self.assertNotIn("Unassigned", zero_enrollment_html)
        self.assertIn('<span class="badge bg-danger">Unassigned</span>', enrolled_html)
        self.assertNotIn('<span class="text-muted">---</span>', enrolled_html)
        self.assertIn("Faculty DISPLAY", assigned_html)
        self.assertNotIn("Unassigned", assigned_html)
        self.assertNotIn('<span class="text-muted">---</span>', assigned_html)

        unassigned_response = self.client.get(reverse("admin_portal:offering_list"), {"unassigned": "1"})
        unassigned_ids = {row.id for row in unassigned_response.context["page_obj"].object_list}
        self.assertSetEqual(unassigned_ids, {self.offering.id, self.other_offering.id})

    def test_user_without_offerings_read_permission_is_denied(self):
        denied_user = self.make_user("denied", "Denied", "User")
        role = Role.objects.create(code="PORTAL_ONLY", name="Portal Only")
        permission = Permission.objects.get(code="admin_portal.access")
        RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(
            user=denied_user, role=role, tenant=self.tenant, campus=self.campus, department=self.department
        )
        self.client.force_login(denied_user)

        response = self.client.get(reverse("admin_portal:offering_list"))

        self.assertEqual(response.status_code, 403)

    def test_existing_search_and_scope_filters_keep_the_enhancement(self):
        response = self.client.get(
            reverse("admin_portal:offering_list"),
            {
                "q": "SEC-1",
                "campus_id": self.campus.id,
                "academic_year_id": self.academic_year.id,
                "term_id": self.term.id,
                "unassigned": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row.id for row in response.context["active_page_obj"].object_list], [self.offering.id])
        self.assertEqual(response.context["inactive_page_obj"].paginator.count, 0)
        self.assertEqual(self.offering_row(response, self.offering).enrolled_count, 0)
        self.assertTrue(response.context["unassigned"])

    def test_enrolled_count_and_unassigned_filter_are_isolated_across_scopes(self):
        self.add_active_enrollments(self.offering, 2, "TARGET")

        other_campus = Campus.objects.create(tenant=self.tenant, code="C2", name="Campus Two")
        other_department = Department.objects.create(
            tenant=self.tenant, campus=other_campus, code="D1", name="Other Campus Department"
        )
        other_program = Program.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            code="P1",
            name="Other Campus Program",
        )
        other_campus_section = Section.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            code=self.offering.section.code,
            name="Other Campus Same-Code Section",
        )
        other_campus_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=other_campus_section,
        )
        self.add_active_enrollments(other_campus_offering, 3, "OTHER-CAMPUS")

        other_tenant = Tenant.objects.create(code="T2", name="Tenant Two")
        other_tenant_campus = Campus.objects.create(tenant=other_tenant, code="C1", name="Tenant Two Campus")
        other_tenant_department = Department.objects.create(
            tenant=other_tenant, campus=other_tenant_campus, code="D1", name="Tenant Two Department"
        )
        other_tenant_program = Program.objects.create(
            tenant=other_tenant,
            campus=other_tenant_campus,
            department=other_tenant_department,
            code="P1",
            name="Tenant Two Program",
        )
        other_tenant_year = AcademicYear.objects.create(
            tenant=other_tenant,
            code=self.academic_year.code,
            name="Tenant Two Academic Year",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        other_tenant_term = Term.objects.create(
            tenant=other_tenant,
            academic_year=other_tenant_year,
            code=self.term.code,
            name="Tenant Two Term",
            sequence_no=1,
        )
        other_tenant_course = Course.objects.create(
            tenant=other_tenant,
            code=self.course.code,
            title="Tenant Two Same-Code Course",
        )
        other_tenant_section = Section.objects.create(
            tenant=other_tenant,
            campus=other_tenant_campus,
            department=other_tenant_department,
            program=other_tenant_program,
            code=self.offering.section.code,
            name="Tenant Two Same-Code Section",
        )
        other_tenant_offering = CourseOffering.objects.create(
            tenant=other_tenant,
            campus=other_tenant_campus,
            department=other_tenant_department,
            program=other_tenant_program,
            academic_year=other_tenant_year,
            term=other_tenant_term,
            course=other_tenant_course,
            section=other_tenant_section,
        )
        self.add_active_enrollments(other_tenant_offering, 4, "OTHER-TENANT")

        other_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2",
            name="Other Academic Year",
            start_date=date(2027, 6, 1),
            end_date=date(2028, 5, 31),
        )
        other_year_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=other_year,
            code=self.term.code,
            name="Other Academic Year Term",
            sequence_no=1,
        )
        other_year_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=other_year,
            term=other_year_term,
            course=self.course,
            section=self.offering.section,
        )
        self.add_active_enrollments(other_year_offering, 5, "OTHER-YEAR")

        other_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="T2",
            name="Second Term",
            sequence_no=2,
        )
        other_term_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=other_term,
            course=self.course,
            section=self.offering.section,
        )
        self.add_active_enrollments(other_term_offering, 6, "OTHER-TERM")

        response = self.client.get(reverse("admin_portal:offering_list"))

        self.assertEqual(response.status_code, 200)
        visible_rows = {
            row.id: row
            for row in response.context["active_page_obj"].object_list
        }
        self.assertEqual(visible_rows[self.offering.id].enrolled_count, 2)
        self.assertEqual(visible_rows[other_year_offering.id].enrolled_count, 5)
        self.assertEqual(visible_rows[other_term_offering.id].enrolled_count, 6)
        self.assertNotIn(other_campus_offering.id, visible_rows)
        self.assertNotIn(other_tenant_offering.id, visible_rows)
        self.assertNotContains(response, "Other Campus Same-Code Section")
        self.assertNotContains(response, "Tenant Two Same-Code Course")
        self.assertNotEqual(self.offering.id, other_year_offering.id)
        self.assertNotEqual(self.offering.id, other_term_offering.id)

        unassigned_response = self.client.get(reverse("admin_portal:offering_list"), {"unassigned": "1"})
        unassigned_row_ids = {
            row.id for row in unassigned_response.context["active_page_obj"].object_list
        }
        self.assertIn(self.offering.id, unassigned_row_ids)
        self.assertIn(other_year_offering.id, unassigned_row_ids)
        self.assertIn(other_term_offering.id, unassigned_row_ids)
        self.assertNotIn(other_campus_offering.id, unassigned_row_ids)
        self.assertNotIn(other_tenant_offering.id, unassigned_row_ids)

    def test_render_query_count_is_bounded_when_more_offerings_are_added(self):
        for index in range(3):
            offering = self.make_offering(f"BASE-{index}")
            Enrollment.objects.create(
                tenant=self.tenant, campus=self.campus, academic_year=self.academic_year, term=self.term,
                student=self.make_student(f"BASE-{index}"), course_offering=offering,
            )
            self.add_active_faculty_assignment(offering, f"BASE-{index}-PRIMARY", is_primary=True)
            self.add_active_faculty_assignment(offering, f"BASE-{index}-SECONDARY")
        url = reverse("admin_portal:offering_list")
        self.client.get(url)
        with CaptureQueriesContext(connection) as base_queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        for index in range(3):
            offering = self.make_offering(f"MORE-{index}")
            Enrollment.objects.create(
                tenant=self.tenant, campus=self.campus, academic_year=self.academic_year, term=self.term,
                student=self.make_student(f"MORE-{index}"), course_offering=offering,
            )
            self.add_active_faculty_assignment(offering, f"MORE-{index}-PRIMARY", is_primary=True)
            self.add_active_faculty_assignment(offering, f"MORE-{index}-SECONDARY")
        with CaptureQueriesContext(connection) as expanded_queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faculty MORE-0-PRIMARY")
        self.assertContains(response, "Faculty MORE-0-SECONDARY")

        def relevant_query_counts(captured_queries):
            table_names = {
                "enrollment": '"enrollments"',
                "assignment": '"faculty_assignments"',
                "user": '"users"',
            }
            return {
                label: sum(table_name in query["sql"] for query in captured_queries)
                for label, table_name in table_names.items()
            }

        base_counts = relevant_query_counts(base_queries.captured_queries)
        expanded_counts = relevant_query_counts(expanded_queries.captured_queries)
        self.assertGreater(base_counts["enrollment"], 0)
        self.assertGreater(base_counts["assignment"], 0)
        self.assertGreater(base_counts["user"], 0)
        for table_label in ("enrollment", "assignment", "user"):
            self.assertLessEqual(expanded_counts[table_label], base_counts[table_label] + 1)

    def test_unassigned_filter_query_count_is_bounded_when_more_offerings_are_added(self):
        for index in range(3):
            offering = self.make_offering(f"UNASSIGNED-BASE-{index}")
            self.add_active_enrollments(offering, 1, f"UNASSIGNED-BASE-{index}")
        url = reverse("admin_portal:offering_list")
        params = {"unassigned": "1"}
        self.client.get(url, params)
        with CaptureQueriesContext(connection) as base_queries:
            response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)

        for index in range(3):
            offering = self.make_offering(f"UNASSIGNED-MORE-{index}")
            self.add_active_enrollments(offering, 1, f"UNASSIGNED-MORE-{index}")
        with CaptureQueriesContext(connection) as expanded_queries:
            response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)

        def relevant_query_counts(captured_queries):
            table_names = {
                "enrollment": '"enrollments"',
                "assignment": '"faculty_assignments"',
            }
            return {
                label: sum(table_name in query["sql"] for query in captured_queries)
                for label, table_name in table_names.items()
            }

        base_counts = relevant_query_counts(base_queries.captured_queries)
        expanded_counts = relevant_query_counts(expanded_queries.captured_queries)
        self.assertGreater(base_counts["enrollment"], 0)
        self.assertGreater(base_counts["assignment"], 0)
        for table_label in ("enrollment", "assignment"):
            self.assertLessEqual(expanded_counts[table_label], base_counts[table_label] + 1)

    def test_unassigned_filter_pagination_preserves_checkbox_and_current_filters(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])

        for index in range(21):
            self.make_offering(f"A-UNASSIGNED-PAGE-{index:02d}")
        for index in range(21):
            offering = self.make_offering(f"I-UNASSIGNED-PAGE-{index:02d}")
            offering.is_active = False
            offering.save(update_fields=["is_active", "updated_at"])
        url = reverse("admin_portal:offering_list")
        params = {
            "unassigned": "1",
            "q": "UNASSIGNED-PAGE",
            "campus_id": self.campus.id,
            "academic_year_id": self.academic_year.id,
            "term_id": self.term.id,
        }

        active_page_response = self.client.get(url, {**params, "active_page": 2})

        self.assertEqual(active_page_response.status_code, 200)
        self.assertEqual(active_page_response.context["active_page_obj"].number, 2)
        self.assertEqual(active_page_response.context["inactive_page_obj"].number, 1)
        active_querystring = active_page_response.context["active_page_obj"].querystring
        self.assertIn("unassigned=1", active_querystring)
        self.assertIn("q=UNASSIGNED-PAGE", active_querystring)
        self.assertIn(f"campus_id={self.campus.id}", active_querystring)
        self.assertIn(f"academic_year_id={self.academic_year.id}", active_querystring)
        self.assertIn(f"term_id={self.term.id}", active_querystring)

        inactive_page_response = self.client.get(url, {**params, "inactive_page": 2})

        self.assertEqual(inactive_page_response.status_code, 200)
        self.assertEqual(inactive_page_response.context["active_page_obj"].number, 1)
        self.assertEqual(inactive_page_response.context["inactive_page_obj"].number, 2)
        inactive_querystring = inactive_page_response.context["inactive_page_obj"].querystring
        self.assertIn("unassigned=1", inactive_querystring)
        self.assertIn("q=UNASSIGNED-PAGE", inactive_querystring)
        self.assertIn(f"campus_id={self.campus.id}", inactive_querystring)
        self.assertIn(f"academic_year_id={self.academic_year.id}", inactive_querystring)
        self.assertIn(f"term_id={self.term.id}", inactive_querystring)

    def test_active_and_inactive_pagination_keep_counts_assignments_and_independent_parameters(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])

        for index in range(19):
            self.make_offering(f"A-PAGE-{index:02d}")

        inactive_page_two_offering = None
        for index in range(21):
            offering = self.make_offering(f"I-PAGE-{index:02d}")
            offering.is_active = False
            offering.save(update_fields=["is_active", "updated_at"])
            if index == 20:
                inactive_page_two_offering = offering

        self.add_active_enrollments(self.other_offering, 1, "ACTIVE-PAGE-TWO")
        active_assignment = self.add_active_faculty_assignment(
            self.other_offering,
            "ACTIVE-PAGE-TWO",
            is_primary=True,
        )
        self.add_active_enrollments(inactive_page_two_offering, 1, "INACTIVE-PAGE-TWO")
        inactive_assignment = self.add_active_faculty_assignment(
            inactive_page_two_offering,
            "INACTIVE-PAGE-TWO",
            is_primary=True,
        )
        url = reverse("admin_portal:offering_list")

        active_page_response = self.client.get(url, {"active_page": 2})
        self.assertEqual(active_page_response.status_code, 200)
        self.assertEqual(active_page_response.context["active_page_obj"].number, 2)
        self.assertEqual(active_page_response.context["inactive_page_obj"].number, 1)
        active_page_rows = list(active_page_response.context["active_page_obj"].object_list)
        self.assertEqual([row.id for row in active_page_rows], [self.other_offering.id])
        self.assertEqual(active_page_rows[0].enrolled_count, 1)
        self.assertEqual(
            [assignment.id for assignment in active_page_rows[0].offering_faculty_assignments],
            [active_assignment.id],
        )

        inactive_page_response = self.client.get(url, {"inactive_page": 2})
        self.assertEqual(inactive_page_response.status_code, 200)
        self.assertEqual(inactive_page_response.context["active_page_obj"].number, 1)
        self.assertEqual(inactive_page_response.context["inactive_page_obj"].number, 2)
        inactive_page_rows = list(inactive_page_response.context["inactive_page_obj"].object_list)
        self.assertEqual([row.id for row in inactive_page_rows], [inactive_page_two_offering.id])
        self.assertEqual(inactive_page_rows[0].enrolled_count, 1)
        self.assertEqual(
            [assignment.id for assignment in inactive_page_rows[0].offering_faculty_assignments],
            [inactive_assignment.id],
        )
