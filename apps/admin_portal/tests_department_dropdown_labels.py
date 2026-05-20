from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.accounts.models import User
from apps.admin_portal.forms import CourseOfferingForm
from apps.rbac.models import Permission
from apps.tenants.models import Campus, Department, Program, Tenant


class DepartmentDropdownLabelTests(TestCase):
    def setUp(self):
        Permission.objects.create(code="admin_portal.access", module="admin_portal", action="access")
        Permission.objects.create(code="courses.read", module="courses", action="read")
        Permission.objects.create(code="offerings.read", module="offerings", action="read")
        self.admin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.cubao = Campus.objects.create(tenant=self.tenant, code="NCBA-01", name="Cubao")
        self.fairview = Campus.objects.create(tenant=self.tenant, code="NCBA-02", name="Fairview")
        self.cubao_college = Department.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.fairview_college = Department.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.fairview_basic_ed = Department.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            code="BASIC_ED",
            name="Basic Education",
            unit_type=Department.UnitType.DIVISION,
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            code="BSIS",
            name="BSIS",
        )
        self.basic_ed_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_basic_ed,
            code="JHS",
            name="Junior High School",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2526",
            name="AY 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            code="IT101",
            title="Intro to IT",
        )
        self.earlier_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            code="ACCT101",
            title="Accounting Basics",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            program=self.program,
            code="BSIS-1A",
            name="BSIS 1A",
        )
        self.later_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            program=self.program,
            code="BSIS-1B",
            name="BSIS 1B",
        )
        CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )

    def test_course_list_department_filter_prefixes_campus_when_all_campuses_are_visible(self):
        response = self.client.get(reverse("admin_portal:course_list"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("NCBA-01 / COLLEGE", content)
        self.assertIn("NCBA-02 / COLLEGE", content)

    def test_offering_form_department_choices_follow_selected_campus(self):
        form = CourseOfferingForm(
            initial={"tenant": self.tenant.id, "campus": self.fairview.id},
            tenant_queryset=Tenant.objects.all(),
            campus_queryset=Campus.objects.all(),
            department_queryset=Department.objects.all(),
            program_queryset=Program.objects.all(),
            academic_year_queryset=AcademicYear.objects.all(),
            term_queryset=Term.objects.all(),
            course_queryset=Course.objects.all(),
            section_queryset=Section.objects.all(),
        )

        department_ids = set(form.fields["department"].queryset.values_list("id", flat=True))

        self.assertIn(self.fairview_college.id, department_ids)
        self.assertIn(self.fairview_basic_ed.id, department_ids)
        self.assertNotIn(self.cubao_college.id, department_ids)
        self.assertEqual(
            form.fields["department"].widget.attrs["data-campus-dependent"],
            "true",
        )

    def test_offering_form_program_choices_follow_selected_department(self):
        form = CourseOfferingForm(
            initial={
                "tenant": self.tenant.id,
                "campus": self.fairview.id,
                "department": self.fairview_college.id,
            },
            tenant_queryset=Tenant.objects.all(),
            campus_queryset=Campus.objects.all(),
            department_queryset=Department.objects.all(),
            program_queryset=Program.objects.all(),
            academic_year_queryset=AcademicYear.objects.all(),
            term_queryset=Term.objects.all(),
            course_queryset=Course.objects.all(),
            section_queryset=Section.objects.all(),
        )

        program_ids = set(form.fields["program"].queryset.values_list("id", flat=True))

        self.assertIn(self.program.id, program_ids)
        self.assertNotIn(self.basic_ed_program.id, program_ids)
        self.assertEqual(
            form.fields["program"].widget.attrs["data-department-dependent"],
            "true",
        )

    def test_offering_form_course_choices_are_sorted_by_title(self):
        form = CourseOfferingForm(
            initial={"tenant": self.tenant.id, "campus": self.fairview.id},
            tenant_queryset=Tenant.objects.all(),
            campus_queryset=Campus.objects.all(),
            department_queryset=Department.objects.all(),
            program_queryset=Program.objects.all(),
            academic_year_queryset=AcademicYear.objects.all(),
            term_queryset=Term.objects.all(),
            course_queryset=Course.objects.all(),
            section_queryset=Section.objects.all(),
        )

        course_titles = list(form.fields["course"].queryset.values_list("title", flat=True))

        self.assertEqual(course_titles, sorted(course_titles))

    def test_offering_form_section_choices_follow_scope_and_are_sorted(self):
        form = CourseOfferingForm(
            initial={
                "tenant": self.tenant.id,
                "campus": self.fairview.id,
                "department": self.fairview_college.id,
                "program": self.program.id,
            },
            tenant_queryset=Tenant.objects.all(),
            campus_queryset=Campus.objects.all(),
            department_queryset=Department.objects.all(),
            program_queryset=Program.objects.all(),
            academic_year_queryset=AcademicYear.objects.all(),
            term_queryset=Term.objects.all(),
            course_queryset=Course.objects.all(),
            section_queryset=Section.objects.all(),
        )

        section_codes = list(form.fields["section"].queryset.values_list("code", flat=True))

        self.assertEqual(section_codes, sorted(section_codes))
        self.assertEqual(set(section_codes), {"BSIS-1A", "BSIS-1B"})
        self.assertEqual(
            form.fields["section"].widget.attrs["data-section-dependent"],
            "true",
        )

    def test_offering_list_department_filter_prefixes_campus_when_all_campuses_are_visible(self):
        response = self.client.get(reverse("admin_portal:offering_list"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("NCBA-01 / COLLEGE", content)
        self.assertIn("NCBA-02 / COLLEGE", content)

    def test_offering_list_columns_and_rows_are_ordered_by_campus_term_section_course(self):
        CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.earlier_course,
            section=self.section,
            room="101",
        )
        CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            department=self.fairview_college,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.later_section,
            room="102",
        )

        response = self.client.get(reverse("admin_portal:offering_list"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertLess(content.index("<th>Campus</th>"), content.index("<th>Term</th>"))
        self.assertLess(content.index("<th>Term</th>"), content.index("<th>Section</th>"))
        self.assertLess(content.index("<th>Section</th>"), content.index("<th>Course</th>"))
        self.assertLess(content.index("Accounting Basics"), content.index("Intro to IT"))
