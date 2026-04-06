from datetime import date

from django.test import TestCase

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.admin_portal.forms import GradingPeriodLockForm
from apps.grading.models import CourseTemplateAssignment, GradingTemplate, GradingTemplatePeriod
from apps.tenants.models import Campus, Department, Program, Tenant


class GradingPeriodLockFormTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIT",
            name="BSIT",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="AY 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="A132-ITAPPS",
            title="IT Application Tools",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1A",
            name="BSIT 1A",
        )
        self.offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED_V1",
            name="General Education",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="GENED_PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )

    def test_period_lock_form_rejects_arbitrary_term_code(self):
        form = GradingPeriodLockForm(
            data={
                "tenant": self.tenant.id,
                "campus": self.campus.id,
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "period_code": "2526_2NDSEM",
                "scope_type": "CAMPUS",
                "course_offering": "",
                "deadline_at": "2026-04-24T00:00",
                "remarks": "",
                "is_active": "on",
            },
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            offering_queryset=CourseOffering.objects.filter(id=self.offering.id),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("period_code", form.errors)

    def test_period_lock_form_accepts_template_period_code(self):
        form = GradingPeriodLockForm(
            data={
                "tenant": self.tenant.id,
                "campus": self.campus.id,
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "period_code": self.period.code,
                "scope_type": "CAMPUS",
                "course_offering": "",
                "deadline_at": "2026-04-24T00:00",
                "remarks": "",
                "is_active": "on",
            },
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            offering_queryset=CourseOffering.objects.filter(id=self.offering.id),
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_period_lock_form_falls_back_to_tenant_periods_when_course_assignment_lookup_is_empty(self):
        CourseTemplateAssignment.objects.all().delete()

        form = GradingPeriodLockForm(
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            offering_queryset=CourseOffering.objects.filter(id=self.offering.id),
        )

        choices = [value for value, _label in form.fields["period_code"].choices if value]
        self.assertIn(self.period.code, choices)

    def test_period_lock_form_renders_period_code_options_in_html(self):
        form = GradingPeriodLockForm(
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            offering_queryset=CourseOffering.objects.filter(id=self.offering.id),
        )

        rendered = str(form["period_code"])
        self.assertIn('value="GENED_PRELIM"', rendered)
        self.assertIn("Prelim (GENED_PRELIM)", rendered)
