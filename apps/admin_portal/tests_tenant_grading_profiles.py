from datetime import date

from django.test import TestCase

from apps.academics.models import AcademicYear, Course, Term
from apps.admin_portal.forms import TenantGradingProfileForm
from apps.grading.models import GradingTemplate, GradingTemplatePeriod, TenantGradingProfile
from apps.tenants.models import Campus, Department, Program, Tenant


class TenantGradingProfileFormTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="TEN", name="Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="CS",
            name="Computer Studies",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSCS",
            name="BS Computer Science",
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
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS101",
            title="Intro to Computing",
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP1",
            name="Template",
            is_published=True,
            is_active=True,
        )
        GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            is_active=True,
        )
        GradingTemplatePeriod.objects.create(
            template=self.template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
            is_active=True,
        )
        GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PREFINAL",
            name="Pre-Final",
            sequence_no=3,
            is_active=True,
        )
        GradingTemplatePeriod.objects.create(
            template=self.template,
            code="FINAL",
            name="Final",
            sequence_no=4,
            is_active=True,
        )

    def test_form_saves_weighted_final_grade_configuration(self):
        form = TenantGradingProfileForm(
            data={
                "tenant": self.tenant.id,
                "campus": self.campus.id,
                "department": self.department.id,
                "program": self.program.id,
                "course": "",
                "course_type": "",
                "profile_code": "PROFILE1",
                "profile_name": "Weighted Profile",
                "grading_template": self.template.id,
                "default_base_value": "",
                "passing_grade_threshold": "75.00",
                "final_grade_formula_mode": TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS,
                "final_grade_period_weights_text": "PRELIM=25\nMIDTERM=25\nPREFINAL=25\nFINAL=25",
                "priority": "100",
                "effective_from_term": self.term.id,
                "is_default": "on",
                "is_active": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        profile = form.save()

        self.assertEqual(
            profile.final_grade_formula_mode,
            TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS,
        )
        self.assertEqual(
            profile.final_grade_formula_json,
            {
                "period_weights": [
                    {"period_code": "PRELIM", "weight": "25.00"},
                    {"period_code": "MIDTERM", "weight": "25.00"},
                    {"period_code": "PREFINAL", "weight": "25.00"},
                    {"period_code": "FINAL", "weight": "25.00"},
                ]
            },
        )
