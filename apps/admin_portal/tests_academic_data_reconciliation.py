import csv
import html
import re
from datetime import date
from importlib import import_module
from io import StringIO
from urllib.parse import parse_qs, urlparse

from django.apps import apps as django_apps
from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.enrollment.models import Enrollment
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class AcademicDataReconciliationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="REC-A", name="Reconciliation A")
        cls.other_tenant = Tenant.objects.create(code="REC-B", name="Reconciliation B")
        cls.campus = Campus.objects.create(tenant=cls.tenant, code="MAIN", name="Main Campus")
        cls.other_campus = Campus.objects.create(tenant=cls.tenant, code="EAST", name="East Campus")
        cls.other_tenant_campus = Campus.objects.create(tenant=cls.other_tenant, code="MAIN", name="Other Main")
        cls.department, cls.program = cls._department_program(cls.tenant, cls.campus, "CS", "BSCS")
        cls.other_department, cls.other_program = cls._department_program(cls.tenant, cls.other_campus, "BUS", "BSBA")
        cls.other_tenant_department, cls.other_tenant_program = cls._department_program(
            cls.other_tenant, cls.other_tenant_campus, "IT", "BSIT"
        )
        cls.academic_year = AcademicYear.objects.create(
            tenant=cls.tenant, code="AY2526", name="AY 2025-2026", start_date=date(2025, 6, 1), end_date=date(2026, 5, 31)
        )
        cls.term = Term.objects.create(tenant=cls.tenant, academic_year=cls.academic_year, code="1ST", name="First Term", sequence_no=1)
        cls.second_term = Term.objects.create(tenant=cls.tenant, academic_year=cls.academic_year, code="2ND", name="Second Term", sequence_no=2)
        cls.other_year = AcademicYear.objects.create(
            tenant=cls.other_tenant, code="AY2526", name="AY 2025-2026", start_date=date(2025, 6, 1), end_date=date(2026, 5, 31)
        )
        cls.other_term = Term.objects.create(tenant=cls.other_tenant, academic_year=cls.other_year, code="1ST", name="First Term", sequence_no=1)
        cls.campus_admin_role = Role.objects.get_or_create(code="CAMPUS_ADMIN", defaults={"name": "Campus Admin", "is_system": True})[0]
        cls.faculty_role = Role.objects.get_or_create(code="FACULTY", defaults={"name": "Faculty", "is_system": True})[0]
        cls.permission, _ = Permission.objects.get_or_create(
            code="academic_data_reconciliation.view",
            defaults={"module": "academic_data_reconciliation", "action": "view"},
        )
        cls.portal_permission, _ = Permission.objects.get_or_create(
            code="admin_portal.access", defaults={"module": "admin_portal", "action": "access"}
        )
        RolePermission.objects.get_or_create(role=cls.campus_admin_role, permission=cls.permission)
        RolePermission.objects.get_or_create(role=cls.campus_admin_role, permission=cls.portal_permission)
        cls.admin = cls._user("reconciliation-admin", cls.tenant, cls.campus, cls.department)
        UserRole.objects.create(user=cls.admin, role=cls.campus_admin_role, tenant=cls.tenant, campus=cls.campus)
        cls.superadmin = User.objects.create_superuser(
            username="reconciliation-superadmin", email="reconciliation-superadmin@example.com", password="testpass123",
            default_tenant=cls.tenant, default_campus=cls.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        cls.faculty_viewer = cls._user("reconciliation-faculty-viewer", cls.tenant, cls.campus, cls.department)
        UserRole.objects.create(user=cls.faculty_viewer, role=cls.faculty_role, tenant=cls.tenant, campus=cls.campus)
        cls.url = reverse("admin_portal:academic_data_reconciliation")

    @classmethod
    def _department_program(cls, tenant, campus, department_code, program_code):
        department = Department.objects.create(tenant=tenant, campus=campus, code=department_code, name=department_code)
        program = Program.objects.create(tenant=tenant, campus=campus, department=department, code=program_code, name=program_code)
        return department, program

    @classmethod
    def _user(cls, username, tenant, campus, department):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            first_name=username.split("-")[0].title(),
            last_name=username.split("-")[-1].title(),
            default_tenant=tenant,
            default_campus=campus,
            default_department=department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    @classmethod
    def _offering(
        cls,
        code,
        section_code,
        *,
        campus=None,
        department=None,
        program=None,
        academic_year=None,
        term=None,
        active=True,
        status=CourseOffering.Status.OPEN,
        course_title=None,
        section_name=None,
        schedule_text="",
        room="",
    ):
        campus = campus or cls.campus
        department = department or cls.department
        program = program or cls.program
        academic_year = academic_year or cls.academic_year
        term = term or cls.term
        course = Course.objects.create(
            tenant=academic_year.tenant,
            campus=campus,
            department=department,
            code=code,
            title=course_title if course_title is not None else f"{code} Title",
        )
        section = Section.objects.create(
            tenant=academic_year.tenant,
            campus=campus,
            department=department,
            program=program,
            code=section_code,
            name=section_name if section_name is not None else f"{section_code} Name",
        )
        return CourseOffering.objects.create(
            tenant=academic_year.tenant, campus=campus, department=department, program=program,
            academic_year=academic_year, term=term, course=course, section=section, is_active=active, status=status,
            schedule_text=schedule_text,
            room=room,
        )

    @classmethod
    def _faculty(cls, username, *, campus=None, department=None, tenant=None, active=True):
        campus = campus or cls.campus
        department = department or cls.department
        tenant = tenant or cls.tenant
        user = cls._user(username, tenant, campus, department)
        user.is_active = active
        user.save(update_fields=["is_active"])
        UserRole.objects.create(user=user, role=cls.faculty_role, tenant=tenant, campus=campus, department=department)
        return user

    @classmethod
    def _enroll(
        cls,
        offering,
        student_no,
        *,
        active=True,
        status=Enrollment.Status.ACTIVE,
        first_name="Student",
        last_name=None,
    ):
        student = Student.objects.create(
            tenant=offering.tenant, campus=offering.campus, department=offering.department, program=offering.program,
            student_no=student_no, first_name=first_name, last_name=last_name or student_no,
        )
        return Enrollment.objects.create(
            tenant=offering.tenant, campus=offering.campus, academic_year=offering.academic_year, term=offering.term,
            student=student, course_offering=offering, is_active=active, enrollment_status=status,
        )

    @classmethod
    def _params(cls, **extra):
        params = {"campus_id": cls.campus.id, "academic_year_id": cls.academic_year.id, "term_id": cls.term.id}
        params.update(extra)
        return params

    def test_campus_admin_and_superadmin_can_access(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url, self._params())
        self.assertEqual(response.status_code, 200, getattr(response, "url", ""))
        self.client.force_login(self.superadmin)
        self.assertEqual(self.client.get(self.url, self._params()).status_code, 200)

    def test_faculty_is_denied_and_navigation_is_hidden_without_permission(self):
        group, _ = MenuGroup.objects.get_or_create(portal="ADMIN", code="ACADEMICS", defaults={"label": "Academics"})
        item, _ = MenuItem.objects.get_or_create(
            portal="ADMIN", code="ACADEMIC_DATA_RECONCILIATION",
            defaults={"menu_group": group, "label": "Data Reconciliation", "route_name": "admin_portal:academic_data_reconciliation"},
        )
        MenuItemPermission.objects.get_or_create(menu_item=item, permission=self.permission)
        RolePermission.objects.get_or_create(role=self.faculty_role, permission=self.portal_permission)
        self.client.force_login(self.faculty_viewer)
        response = self.client.get(self.url, self._params())
        self.assertEqual(response.status_code, 403)
        dashboard_permission, _ = Permission.objects.get_or_create(
            code="dashboard.read", defaults={"module": "dashboard", "action": "read"}
        )
        RolePermission.objects.get_or_create(role=self.faculty_role, permission=dashboard_permission)
        self.assertNotContains(self.client.get(reverse("admin_portal:dashboard")), "Data Reconciliation")

    def test_unauthorized_export_and_lazy_requests_are_denied(self):
        RolePermission.objects.get_or_create(role=self.faculty_role, permission=self.portal_permission)
        self.client.force_login(self.faculty_viewer)

        export_response = self.client.get(self.url, self._params(category="offerings", export="csv"))
        lazy_response = self.client.get(self.url, self._params(category="offerings", page=2, lazy=1))

        self.assertEqual(export_response.status_code, 403)
        self.assertEqual(lazy_response.status_code, 403)

    def test_course_offerings_include_all_active_offerings_and_count_active_enrollments(self):
        no_enrollment = self._offering("CS101", "BSCS-1A")
        enrolled = self._offering("CS102", "BSCS-1B")
        multiple = self._offering("CS102M", "BSCS-1BM")
        withdrawn_only = self._offering("CS103", "BSCS-1C")
        inactive = self._offering("CS104", "BSCS-1D", active=False)
        archived = self._offering("CS105", "BSCS-1E", status=CourseOffering.Status.ARCHIVED)
        self._enroll(enrolled, "REC-001")
        for number in range(3):
            self._enroll(multiple, f"REC-M-{number}")
        self._enroll(withdrawn_only, "REC-002", status=Enrollment.Status.W)
        self._enroll(withdrawn_only, "REC-003", active=False)
        self.client.force_login(self.admin)
        response = self.client.get(self.url, self._params(category="offerings"))
        self.assertContains(response, no_enrollment.course.code)
        self.assertContains(response, enrolled.course.code)
        self.assertContains(response, multiple.course.code)
        self.assertContains(response, withdrawn_only.course.code)
        self.assertNotContains(response, inactive.course.code)
        self.assertNotContains(response, archived.course.code)
        counts = {row.course.code: row.active_enrollment_count for row in response.context["offerings"]}
        self.assertEqual(counts, {"CS101": 0, "CS102": 1, "CS102M": 3, "CS103": 0})
        self.assertEqual(response.context["summary"]["total_active_offerings"], 4)
        self.assertEqual(response.context["summary"]["offerings_without_enrollment"], 2)
        self.assertEqual(response.context["summary"]["offerings_without_enrollment_percent"], 50.0)
        self.assertContains(response, "Course Offerings")

    def test_offering_faculty_display_and_active_assignment_count(self):
        offering = self._offering("CS201", "BSCS-2A")
        primary = self._faculty("primary-faculty")
        secondary = self._faculty("secondary-faculty")
        inactive = self._faculty("inactive-assignment-faculty")
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=offering, faculty_user=primary, is_primary=True)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=offering, faculty_user=secondary)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=offering, faculty_user=inactive, is_active=False)
        self.client.force_login(self.admin)
        response = self.client.get(self.url, self._params(category="offerings"))
        self.assertContains(response, primary.full_name)
        self.assertContains(response, secondary.full_name)
        self.assertNotContains(response, inactive.full_name)
        row = response.context["offerings"][0]
        self.assertEqual(row.active_assignment_count, 2)
        self.assertContains(response, "Multiple faculty assigned")

    def test_faculty_without_active_assignment_is_reported_by_term(self):
        no_assignment = self._faculty("no-assignment")
        assigned = self._faculty("assigned-faculty")
        inactive_assignment = self._faculty("inactive-assignment")
        inactive_user = self._faculty("inactive-user", active=False)
        non_faculty = self._user("not-faculty", self.tenant, self.campus, self.department)
        other_term_faculty = self._faculty("other-term-faculty")
        offering = self._offering("CS301", "BSCS-3A")
        other_term_offering = self._offering("CS302", "BSCS-3B", term=self.second_term)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=offering, faculty_user=assigned)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=offering, faculty_user=inactive_assignment, is_active=False)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=other_term_offering, faculty_user=other_term_faculty)
        self.client.force_login(self.admin)
        response = self.client.get(self.url, self._params(category="faculty"))
        for user in (no_assignment, inactive_assignment, other_term_faculty):
            self.assertContains(response, user.username)
        for user in (assigned, inactive_user, non_faculty):
            self.assertNotContains(response, user.username)

    def test_other_tenant_and_campus_never_leak_even_with_manipulated_parameters(self):
        visible = self._offering("CS401", "BSCS-4A")
        other_campus = self._offering("BUS401", "BSBA-4A", campus=self.other_campus, department=self.other_department, program=self.other_program)
        other_tenant = self._offering(
            "IT401", "BSIT-4A", campus=self.other_tenant_campus, department=self.other_tenant_department,
            program=self.other_tenant_program, academic_year=self.other_year, term=self.other_term,
        )
        self.client.force_login(self.admin)
        response = self.client.get(self.url, {
            "scope_tenant_id": self.other_tenant.id,
            "scope_campus_id": self.other_tenant_campus.id,
            "campus_id": self.other_campus.id,
            "academic_year_id": self.other_year.id,
            "term_id": self.other_term.id,
            "category": "offerings",
        })
        self.assertContains(response, visible.course.code)
        self.assertNotContains(response, other_campus.course.code)
        self.assertNotContains(response, other_tenant.course.code)

    def test_visible_other_campus_without_reconciliation_permission_cannot_be_selected(self):
        other_campus_access_role = Role.objects.create(
            code="REC-OTHER-CAMPUS-ACCESS",
            name="Other Campus Portal Access",
        )
        RolePermission.objects.create(role=other_campus_access_role, permission=self.portal_permission)
        UserRole.objects.create(
            user=self.admin,
            role=other_campus_access_role,
            tenant=self.tenant,
            campus=self.other_campus,
        )
        hidden = self._offering(
            "BUS-SCOPED",
            "BSBA-SCOPED",
            campus=self.other_campus,
            department=self.other_department,
            program=self.other_program,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            self.url,
            self._params(campus_id=self.other_campus.id, category="offerings"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["scope_unavailable"])
        self.assertNotContains(response, hidden.course.code)

    def test_mismatched_and_cross_tenant_period_ids_fall_back_without_leaking(self):
        selected_scope_offering = self._offering("PERIOD-MAIN", "PERIOD-MAIN")
        same_tenant_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2627",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        same_tenant_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=same_tenant_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
        )
        mismatched_scope_offering = self._offering(
            "PERIOD-OTHER",
            "PERIOD-OTHER",
            academic_year=same_tenant_year,
            term=same_tenant_term,
        )
        cross_tenant_offering = self._offering(
            "PERIOD-FOREIGN",
            "PERIOD-FOREIGN",
            campus=self.other_tenant_campus,
            department=self.other_tenant_department,
            program=self.other_tenant_program,
            academic_year=self.other_year,
            term=self.other_term,
        )
        self.client.force_login(self.admin)

        mismatched_response = self.client.get(
            self.url,
            self._params(
                academic_year_id=self.academic_year.id,
                term_id=same_tenant_term.id,
                category="offerings",
            ),
        )
        self.assertEqual(mismatched_response.context["selected_year"], self.academic_year)
        self.assertEqual(mismatched_response.context["selected_term"], self.term)
        self.assertContains(mismatched_response, selected_scope_offering.course.code)
        self.assertNotContains(mismatched_response, mismatched_scope_offering.course.code)

        cross_tenant_response = self.client.get(
            self.url,
            self._params(
                academic_year_id=self.other_year.id,
                term_id=self.other_term.id,
                category="offerings",
            ),
        )
        self.assertEqual(cross_tenant_response.context["selected_year"].tenant_id, self.tenant.id)
        self.assertEqual(cross_tenant_response.context["selected_term"].tenant_id, self.tenant.id)
        self.assertNotContains(cross_tenant_response, cross_tenant_offering.course.code)

    def test_search_and_csv_export_match_current_category(self):
        offering = self._offering("SEARCH101", "SEARCH-1A")
        faculty = self._faculty("search-faculty")
        self.client.force_login(self.admin)
        offering_response = self.client.get(self.url, self._params(category="offerings", q="SEARCH-1A"))
        self.assertContains(offering_response, offering.course.code)
        faculty_response = self.client.get(self.url, self._params(category="faculty", q="search-faculty"))
        self.assertContains(faculty_response, faculty.username)
        export = self.client.get(self.url, self._params(category="offerings", q="SEARCH101", export="csv"))
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("SEARCH101", export.content.decode("utf-8"))
        self.assertNotIn("search-faculty", export.content.decode("utf-8"))

    def test_offering_csv_escapes_formula_cells_and_exports_section_name(self):
        self._offering(
            "=FORMULA",
            "-SECTION",
            course_title="+Formula Title",
            section_name="@Formula Section",
            schedule_text="=Schedule",
            room="-Room",
        )
        self.client.force_login(self.admin)

        export = self.client.get(
            self.url,
            self._params(category="offerings", q="FORMULA", export="csv"),
        )

        rows = list(csv.DictReader(StringIO(export.content.decode("utf-8"))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Course Code"], "'=FORMULA")
        self.assertEqual(rows[0]["Course Title"], "'+Formula Title")
        self.assertEqual(rows[0]["Schedule Text"], "'=Schedule")
        self.assertEqual(rows[0]["Room"], "'-Room")
        self.assertEqual(rows[0]["Class Size"], "0")
        self.assertEqual(rows[0]["Section Code"], "'-SECTION")
        self.assertEqual(rows[0]["Section Name"], "'@Formula Section")
        self.assertEqual(
            list(rows[0]),
            [
                "Course Code", "Course Title", "Schedule Text", "Room", "Class Size", "Section Code", "Section Name",
                "Faculty Assigned", "Other Finding",
            ],
        )

    def test_course_offerings_csv_uses_filters_full_rows_and_deterministic_findings(self):
        no_faculty = self._offering("CSVNONE", "CSV-NONE")
        one_faculty = self._offering("CSVONE", "CSV-ONE")
        multi_faculty = self._offering("CSVMULTI", "CSV-MULTI")
        first = self._faculty("csv-first")
        second = self._faculty("csv-second")
        first.first_name = "=Formula Faculty"
        first.save(update_fields=["first_name"])
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=one_faculty, faculty_user=first)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=multi_faculty, faculty_user=first)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=multi_faculty, faculty_user=second)
        self._enroll(one_faculty, "CSV-ONE-STUDENT")
        self.client.force_login(self.admin)

        export = self.client.get(self.url, self._params(
            category="offerings", faculty_assignment="multiple", class_size="0", export="csv",
        ))
        rows = list(csv.DictReader(StringIO(export.content.decode("utf-8"))))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Course Code"], multi_faculty.course.code)
        self.assertEqual(rows[0]["Class Size"], "0")
        self.assertEqual(rows[0]["Faculty Assigned"], "'=Formula Faculty First, Csv Second")
        self.assertEqual(rows[0]["Other Finding"], "Multiple faculty assigned; No active enrollment")
        self.assertNotIn(no_faculty.course.code, export.content.decode("utf-8"))
        self.assertNotIn(one_faculty.course.code, export.content.decode("utf-8"))

    def test_high_exception_warning_uses_actual_count_and_denominator(self):
        for number in range(4):
            self._offering(f"WARN{number}", f"WARN-{number}")
        enrolled = self._offering("WARN-ACTIVE", "WARN-5")
        self._enroll(enrolled, "WARN-001")
        self.client.force_login(self.admin)

        response = self.client.get(self.url, self._params(category="offerings"))

        self.assertEqual(response.context["summary"]["offerings_without_enrollment"], 4)
        self.assertEqual(response.context["summary"]["total_active_offerings"], 5)
        self.assertEqual(response.context["summary"]["offerings_without_enrollment_percent"], 80.0)
        self.assertEqual(len(response.context["high_exception_warnings"]), 2)
        self.assertContains(response, "4 of 5 active course offerings currently have no active enrollment records.")
        self.assertContains(response, "1 of 1 active faculty members currently have no active assignments.")
        self.assertContains(response, "No faculty assigned")

    def test_no_enrollment_lazy_loading_and_export_include_all_filtered_rows(self):
        for number in range(51):
            self._offering(f"PAGE{number:03d}", f"PAGE-{number:03d}")
        self.client.force_login(self.admin)

        initial_page = self.client.get(self.url, self._params(category="offerings", q="PAGE", sort="section"))
        self.assertEqual(initial_page.context["result_total"], 51)
        self.assertEqual(initial_page.context["page_obj"].number, 1)
        self.assertEqual(len(initial_page.context["offerings"]), 50)
        self.assertContains(initial_page, "Showing 1–50 of 51 records.")
        self.assertContains(initial_page, "Load more")
        self.assertContains(initial_page, '<a class="btn btn-sm btn-outline-secondary" id="course-offering-load-more"')
        self.assertNotContains(initial_page, "Previous")
        self.assertIn("q=PAGE", initial_page.context["lazy_next_url"])
        self.assertIn("category=offerings", initial_page.context["lazy_next_url"])
        self.assertIn("lazy=1", initial_page.context["lazy_next_url"])

        next_page_url = initial_page.context["next_page_url"]
        next_page_query = parse_qs(urlparse(next_page_url).query)
        self.assertNotIn("lazy", next_page_query)
        self.assertEqual(next_page_query["campus_id"], [str(self.campus.id)])
        self.assertEqual(next_page_query["academic_year_id"], [str(self.academic_year.id)])
        self.assertEqual(next_page_query["term_id"], [str(self.term.id)])
        self.assertEqual(next_page_query["category"], ["offerings"])
        self.assertEqual(next_page_query["q"], ["PAGE"])
        self.assertEqual(next_page_query["sort"], ["section"])
        self.assertEqual(next_page_query["page"], ["2"])

        conventional_page = self.client.get(f"{self.url}{next_page_url}")
        self.assertEqual(conventional_page.status_code, 200)
        self.assertEqual(conventional_page.context["page_obj"].number, 2)
        self.assertEqual(len(conventional_page.context["offerings"]), 1)
        self.assertContains(conventional_page, "Academic Data Reconciliation")
        self.assertContains(conventional_page, "PAGE050")
        self.assertContains(conventional_page, "Showing 51–51 of 51 records.")
        self.assertEqual(conventional_page.context["search_query"], "PAGE")
        self.assertEqual(conventional_page.context["selected_sort"], "section")
        self.assertEqual(conventional_page.context["class_size"], "all")
        self.assertEqual(conventional_page.context["faculty_assignment"], "all")

        lazy_page = self.client.get(self.url, self._params(category="offerings", q="PAGE", sort="section", page=2, lazy=1))
        self.assertEqual(lazy_page.status_code, 200)
        self.assertContains(lazy_page, "PAGE050")
        self.assertNotContains(lazy_page, "Academic Data Reconciliation")
        self.assertNotIn("X-Reconciliation-Next-Url", lazy_page)

        export = self.client.get(self.url, self._params(category="offerings", q="PAGE", sort="section", page=2, export="csv"))
        lines = export.content.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 52)
        self.assertIn("PAGE000", export.content.decode("utf-8"))
        self.assertIn("PAGE050", export.content.decode("utf-8"))

    def test_class_size_filters_cover_boundaries_and_invalid_values_fall_back_to_all(self):
        boundaries = [
            ("0", 0), ("1", 1), ("10", 10), ("11", 11), ("20", 20), ("21", 21),
            ("30", 30), ("31", 31), ("40", 40), ("41", 41), ("50", 50), ("51", 51),
        ]
        offerings = {}
        for code_suffix, size in boundaries:
            offering = self._offering(f"SIZE{code_suffix}", f"SIZE-{code_suffix}")
            offerings[size] = offering
            for student_number in range(size):
                self._enroll(offering, f"SIZE-{code_suffix}-{student_number}")
        expected_filter = {
            "0": {0}, "1_10": {1, 10}, "11_20": {11, 20}, "21_30": {21, 30},
            "31_40": {31, 40}, "41_50": {41, 50}, "51_plus": {51},
        }
        self.client.force_login(self.admin)
        for filter_value, expected_sizes in expected_filter.items():
            response = self.client.get(self.url, self._params(category="offerings", class_size=filter_value))
            codes = {row.course.code for row in response.context["offerings"]}
            self.assertEqual(codes, {offerings[size].course.code for size in expected_sizes})
        invalid = self.client.get(self.url, self._params(category="offerings", class_size="all_records"))
        self.assertEqual(invalid.context["class_size"], "all")
        self.assertEqual(invalid.context["result_total"], len(boundaries))

    def test_faculty_assignment_filters_use_active_assignment_counts(self):
        no_assignment = self._offering("FACNONE", "FAC-NONE")
        one_assignment = self._offering("FACONE", "FAC-ONE")
        multiple_assignments = self._offering("FACMULTI", "FAC-MULTI")
        inactive_only = self._offering("FACINACTIVE", "FAC-INACTIVE")
        first = self._faculty("filter-first")
        second = self._faculty("filter-second")
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=one_assignment, faculty_user=first)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=multiple_assignments, faculty_user=first)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=multiple_assignments, faculty_user=second)
        FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus, offering=inactive_only, faculty_user=first, is_active=False
        )
        self.client.force_login(self.admin)

        def filtered_codes(value):
            response = self.client.get(self.url, self._params(category="offerings", faculty_assignment=value))
            return response, {row.course.code for row in response.context["offerings"]}

        response, codes = filtered_codes("none")
        self.assertEqual(codes, {no_assignment.course.code, inactive_only.course.code})
        self.assertEqual(response.context["faculty_assignment"], "none")
        _response, codes = filtered_codes("assigned")
        self.assertEqual(codes, {one_assignment.course.code, multiple_assignments.course.code})
        _response, codes = filtered_codes("multiple")
        self.assertEqual(codes, {multiple_assignments.course.code})
        invalid, codes = filtered_codes("all_assigned")
        self.assertEqual(invalid.context["faculty_assignment"], "all")
        self.assertEqual(codes, {
            no_assignment.course.code, one_assignment.course.code, multiple_assignments.course.code, inactive_only.course.code,
        })

    def test_annotated_counts_are_correct_with_multiple_students_and_faculty(self):
        offering = self._offering("JOIN101", "JOIN-1A")
        first = self._faculty("join-first")
        second = self._faculty("join-second")
        for number in range(3):
            self._enroll(offering, f"JOIN-{number}")
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=offering, faculty_user=first)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=offering, faculty_user=second)
        self.client.force_login(self.admin)

        response = self.client.get(self.url, self._params(category="offerings", q="JOIN101"))

        rows = list(response.context["offerings"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].active_enrollment_count, 3)
        self.assertEqual(rows[0].active_assignment_count, 2)

    def test_roster_is_scoped_active_only_and_has_a_non_javascript_fallback(self):
        offering = self._offering("ROSTER101", "ROSTER-1A", schedule_text="MW 08:00", room="Room 101")
        another_offering = self._offering("ROSTER102", "ROSTER-1B")
        self._enroll(offering, "ROSTER-2", first_name="Zed", last_name="Zulu")
        self._enroll(offering, "ROSTER-1", first_name="Amy", last_name="Alpha")
        self._enroll(offering, "ROSTER-W", status=Enrollment.Status.W)
        self._enroll(offering, "ROSTER-I", active=False)
        self._enroll(another_offering, "ROSTER-OTHER", first_name="Other", last_name="Student")
        self.client.force_login(self.admin)

        main_response = self.client.get(self.url, self._params(category="offerings", q="ROSTER101"))
        link_match = re.search(
            r'href="([^"]*academic-data-reconciliation/offerings/%s/roster/[^"]*)"' % offering.id,
            main_response.content.decode("utf-8"),
        )
        self.assertIsNotNone(link_match)
        fallback_url = html.unescape(link_match.group(1))
        roster_response = self.client.get(fallback_url)
        self.assertEqual(roster_response.status_code, 200)
        self.assertEqual(roster_response.context["active_class_size"], 2)
        self.assertEqual(
            [enrollment.student.student_no for enrollment in roster_response.context["enrollments"]],
            ["ROSTER-1", "ROSTER-2"],
        )
        self.assertContains(roster_response, "Class Roster")
        self.assertContains(roster_response, "ROSTER-1")
        self.assertNotContains(roster_response, "ROSTER-W")
        self.assertNotContains(roster_response, "ROSTER-I")
        self.assertNotContains(roster_response, "ROSTER-OTHER")
        self.assertEqual(self.client.post(fallback_url).status_code, 405)
        modal_response = self.client.get(f"{fallback_url}&modal=1")
        self.assertEqual(modal_response.status_code, 200)
        self.assertNotContains(modal_response, "Academic Data Reconciliation</a></li>")

        self.client.force_login(self.faculty_viewer)
        self.assertEqual(self.client.get(fallback_url).status_code, 403)

        self.client.force_login(self.admin)
        foreign = self._offering(
            "ROSTER-FOREIGN", "ROSTER-FOREIGN", campus=self.other_tenant_campus,
            department=self.other_tenant_department, program=self.other_tenant_program,
            academic_year=self.other_year, term=self.other_term,
        )
        foreign_url = reverse("admin_portal:academic_data_reconciliation_roster", args=[foreign.id])
        self.assertEqual(self.client.get(foreign_url, self._params()).status_code, 404)

        other_campus_role = Role.objects.create(code="REC-ROSTER-OTHER-CAMPUS", name="Roster Other Campus Access")
        RolePermission.objects.create(role=other_campus_role, permission=self.portal_permission)
        UserRole.objects.create(user=self.admin, role=other_campus_role, tenant=self.tenant, campus=self.other_campus)
        other_campus_offering = self._offering(
            "ROSTER-EAST", "ROSTER-EAST", campus=self.other_campus,
            department=self.other_department, program=self.other_program,
        )
        other_campus_url = reverse("admin_portal:academic_data_reconciliation_roster", args=[other_campus_offering.id])
        self.assertEqual(
            self.client.get(other_campus_url, self._params(campus_id=self.other_campus.id)).status_code,
            404,
        )

    def test_roster_empty_state_and_query_count_are_bounded(self):
        empty = self._offering("ROSTEREMPTY", "ROSTER-EMPTY")
        small = self._offering("ROSTERSMALL", "ROSTER-SMALL")
        large = self._offering("ROSTERLARGE", "ROSTER-LARGE")
        for number in range(3):
            self._enroll(small, f"SMALL-{number}")
        for number in range(50):
            self._enroll(large, f"LARGE-{number}")
        self.client.force_login(self.admin)

        empty_response = self.client.get(
            reverse("admin_portal:academic_data_reconciliation_roster", args=[empty.id]), self._params()
        )
        self.assertContains(empty_response, "No active enrolled students were found for this course offering.")
        with CaptureQueriesContext(connection) as small_queries:
            small_response = self.client.get(
                reverse("admin_portal:academic_data_reconciliation_roster", args=[small.id]), self._params()
            )
        with CaptureQueriesContext(connection) as large_queries:
            large_response = self.client.get(
                reverse("admin_portal:academic_data_reconciliation_roster", args=[large.id]), self._params()
            )
        self.assertEqual(len(small_response.context["enrollments"]), 3)
        self.assertEqual(len(large_response.context["enrollments"]), 50)
        self.assertLessEqual(len(large_queries), 75, f"Large roster used {len(large_queries)} queries")
        self.assertLessEqual(
            len(large_queries), len(small_queries) + 5,
            f"Roster queries grew from {len(small_queries)} to {len(large_queries)}",
        )

    def test_rbac_reverse_preserves_later_role_and_direct_user_permissions(self):
        direct_permission = UserPermission.objects.create(
            user=self.faculty_viewer,
            permission=self.permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        migration = import_module("apps.rbac.migrations.0031_seed_academic_data_reconciliation_permission")

        migration.unseed_permission(django_apps, None)

        self.assertFalse(
            RolePermission.objects.filter(role=self.campus_admin_role, permission=self.permission).exists()
        )
        self.assertTrue(Permission.objects.filter(pk=self.permission.pk).exists())
        self.assertTrue(UserPermission.objects.filter(pk=direct_permission.pk).exists())

        direct_permission.delete()
        migration.seed_permission(django_apps, None)
        custom_role = Role.objects.create(code="REC-CUSTOM-REVIEWER", name="Custom Reconciliation Reviewer")
        RolePermission.objects.create(role=custom_role, permission=self.permission)

        migration.unseed_permission(django_apps, None)

        self.assertFalse(
            RolePermission.objects.filter(role=self.campus_admin_role, permission=self.permission).exists()
        )
        self.assertTrue(RolePermission.objects.filter(role=custom_role, permission=self.permission).exists())
        self.assertTrue(Permission.objects.filter(pk=self.permission.pk).exists())

    def test_offering_page_query_count_is_bounded_with_multiple_assignments(self):
        offerings = [self._offering(f"QUERY{number:03d}", f"QUERY-{number:03d}") for number in range(50)]
        primary = self._faculty("query-primary")
        secondary = self._faculty("query-secondary")
        FacultyAssignment.objects.bulk_create(
            [
                FacultyAssignment(
                    tenant=self.tenant,
                    campus=self.campus,
                    offering=offering,
                    faculty_user=faculty,
                    is_primary=faculty == primary,
                )
                for offering in offerings
                for faculty in (primary, secondary)
            ]
        )
        for offering in offerings:
            for student_number in range(3):
                self._enroll(offering, f"QUERY-{offering.id}-{student_number}")
        self.client.force_login(self.admin)

        with CaptureQueriesContext(connection) as single_row_queries:
            single_row_response = self.client.get(
                self.url,
                self._params(category="offerings", q="QUERY000", sort="faculty"),
            )
        with CaptureQueriesContext(connection) as captured_queries:
            response = self.client.get(
                self.url,
                self._params(category="offerings", q="QUERY", sort="faculty"),
            )

        single_row_query_count = len(single_row_queries)
        query_count = len(captured_queries)
        self.assertEqual(single_row_response.status_code, 200)
        self.assertEqual(len(single_row_response.context["offerings"]), 1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["offerings"]), 50)
        self.assertLessEqual(query_count, 75, f"Offering page used {query_count} database queries")
        self.assertLessEqual(
            query_count,
            single_row_query_count + 5,
            f"Queries grew from {single_row_query_count} for one row to {query_count} for fifty rows",
        )

    def test_sorting_is_allowlisted_and_tabs_preserve_filters(self):
        self._offering("ZZZ101", "SECTION-Z")
        self._offering("AAA101", "SECTION-A")
        faculty_zebra = self._faculty("zebra-one")
        faculty_alpha = self._faculty("alpha-two")
        self.client.force_login(self.admin)

        default_response = self.client.get(self.url, self._params(category="offerings"))
        self.assertEqual(default_response.context["selected_sort"], "course_code")
        self.assertEqual([row.course.code for row in default_response.context["offerings"]], ["AAA101", "ZZZ101"])

        invalid_sort = self.client.get(self.url, self._params(category="offerings", sort="offering__tenant_id"))
        self.assertEqual(invalid_sort.context["selected_sort"], "course_code")
        self.assertEqual([row.course.code for row in invalid_sort.context["offerings"]], ["AAA101", "ZZZ101"])

        faculty_sort = self.client.get(self.url, self._params(category="faculty", sort="faculty_id"))
        self.assertEqual(faculty_sort.context["selected_sort"], "faculty_id")
        self.assertEqual(
            [row.username for row in faculty_sort.context["faculty"]],
            [faculty_alpha.username, self.faculty_viewer.username, faculty_zebra.username],
        )

        preserved = self.client.get(self.url, self._params(
            category="offerings", q="AAA101", sort="section", class_size="1_10", faculty_assignment="assigned",
        ))
        self.assertIn("q=AAA101", preserved.context["tab_queries"]["faculty"])
        self.assertIn("sort=section", preserved.context["tab_queries"]["faculty"])
        self.assertIn(f"term_id={self.term.id}", preserved.context["tab_queries"]["faculty"])
        self.assertNotIn("class_size", preserved.context["tab_queries"]["faculty"])
        self.assertNotIn("faculty_assignment", preserved.context["tab_queries"]["faculty"])
        self.assertIn("class_size=1_10", preserved.context["sort_links"]["course_title"])
        self.assertIn("faculty_assignment=assigned", preserved.context["sort_links"]["course_title"])
        self.assertIn(f"academic_year_id={self.academic_year.id}", preserved.context["reset_query"])
        self.assertIn(f"term_id={self.term.id}", preserved.context["reset_query"])
