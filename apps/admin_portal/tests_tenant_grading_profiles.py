from datetime import date

from django.test import TestCase

from apps.academics.models import AcademicYear, Course, Term
from apps.admin_portal.forms import TenantGradingProfileForm
from apps.grading.duplication import GradingTemplateDuplicationService
from apps.grading.models import (
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    TenantGradingProfile,
)
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
                "term_type": TenantGradingProfile.TermType.SUMMER,
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
        self.assertEqual(profile.term_type, TenantGradingProfile.TermType.SUMMER)
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

    def test_department_choices_are_campus_dependent_and_labeled_with_campus(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="EXT", name="Extension Campus")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code=self.department.code,
            name=self.department.name,
        )

        form = TenantGradingProfileForm(
            initial={"campus": self.campus.id},
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(tenant=self.tenant),
            department_queryset=Department.objects.filter(tenant=self.tenant),
            program_queryset=Program.objects.filter(tenant=self.tenant),
            course_queryset=Course.objects.filter(tenant=self.tenant),
            template_queryset=GradingTemplate.objects.filter(tenant=self.tenant),
            term_queryset=Term.objects.filter(tenant=self.tenant),
        )

        department_ids = list(form.fields["department"].queryset.values_list("id", flat=True))
        self.assertEqual(department_ids, [self.department.id])
        self.assertNotIn(other_department.id, department_ids)
        self.assertEqual(form.fields["department"].label_from_instance(self.department), "MAIN | CS - Computer Studies")

    def test_course_choices_are_sorted_by_title_then_code(self):
        accounting = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="ACC101",
            title="Accounting Fundamentals",
        )
        business = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BUS101",
            title="Business Fundamentals",
        )

        form = TenantGradingProfileForm(
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(tenant=self.tenant),
            department_queryset=Department.objects.filter(tenant=self.tenant),
            program_queryset=Program.objects.filter(tenant=self.tenant),
            course_queryset=Course.objects.filter(tenant=self.tenant),
            template_queryset=GradingTemplate.objects.filter(tenant=self.tenant),
            term_queryset=Term.objects.filter(tenant=self.tenant),
        )

        course_ids = list(form.fields["course"].queryset.values_list("id", flat=True))
        self.assertEqual(course_ids, [accounting.id, business.id, self.course.id])

    def test_duplicate_template_copies_structure_as_draft(self):
        period = self.template.periods.get(code="PRELIM")
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="CS",
            name="Class Standing",
            weight_percentage="60.00",
            sort_order=1,
            is_exam_component=False,
        )
        subcomponent = GradingTemplateSubcomponent.objects.create(
            template_component=component,
            code="QUIZ",
            name="Quizzes",
            weight_percentage="100.00",
            sort_order=1,
            admin_locked=False,
        )
        GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="Q1",
            name="Quiz 1",
            weight_percentage="100.00",
            sort_order=1,
            admin_locked=False,
        )
        self.template.approval_status = GradingTemplate.ApprovalStatus.APPROVED
        self.template.is_published = True
        self.template.save(update_fields=["approval_status", "is_published", "updated_at"])

        duplicate, counts = GradingTemplateDuplicationService.duplicate_template(source=self.template)

        self.assertNotEqual(duplicate.code, self.template.code)
        self.assertEqual(duplicate.approval_status, GradingTemplate.ApprovalStatus.DRAFT)
        self.assertFalse(duplicate.is_published)
        self.assertTrue(duplicate.is_active)
        self.assertEqual(counts, {"periods": 4, "components": 1, "subcomponents": 1, "details": 1})
        copied_period = duplicate.periods.get(code="PRELIM")
        copied_component = copied_period.components.get(code="CS")
        copied_subcomponent = copied_component.subcomponents.get(code="QUIZ")
        copied_detail = copied_subcomponent.details.get(code="Q1")
        self.assertEqual(str(copied_component.weight_percentage), "60.00")
        self.assertEqual(str(copied_subcomponent.weight_percentage), "100.00")
        self.assertEqual(str(copied_detail.weight_percentage), "100.00")
        self.assertFalse(copied_subcomponent.admin_locked)
        self.assertFalse(copied_detail.admin_locked)

    def test_duplicate_profile_copies_formula_as_inactive_non_default(self):
        profile = TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            course=self.course,
            term_type=TenantGradingProfile.TermType.SUMMER,
            profile_code="SUM-LAB",
            profile_name="Summer Lab",
            grading_template=self.template,
            default_base_value="50.00",
            passing_grade_threshold="75.00",
            final_grade_formula_mode=TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS,
            final_grade_formula_json={
                "period_weights": [
                    {"period_code": "MIDTERM", "weight": "33.33"},
                    {"period_code": "PREFINAL", "weight": "33.33"},
                    {"period_code": "FINAL", "weight": "33.34"},
                ]
            },
            priority=10,
            effective_from_term=self.term,
            is_default=True,
            is_active=True,
        )

        duplicate = GradingTemplateDuplicationService.duplicate_profile(source=profile)

        self.assertNotEqual(duplicate.profile_code, profile.profile_code)
        self.assertEqual(duplicate.profile_name, "Copy of Summer Lab")
        self.assertEqual(duplicate.final_grade_formula_json, profile.final_grade_formula_json)
        self.assertEqual(duplicate.final_grade_formula_mode, profile.final_grade_formula_mode)
        self.assertEqual(duplicate.grading_template, profile.grading_template)
        self.assertFalse(duplicate.is_active)
        self.assertFalse(duplicate.is_default)
