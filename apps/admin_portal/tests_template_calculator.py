from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Term
from apps.grading.models import (
    DetailComputationMode,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    TenantGradingProfile,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Tenant


class GradingTemplateCalculatorViewTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="AY 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )

        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP-CALC",
            name="Testing Template",
            is_published=True,
            is_active=True,
        )

        self.period_prelim = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            is_active=True,
        )
        self.period_midterm = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
            is_active=True,
        )
        self.period_prefinal = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PREFINAL_CS",
            name="Pre-Final Class Standing",
            sequence_no=3,
            is_active=True,
        )
        self.period_final_exam = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="FINAL_EXAM",
            name="Final Exam",
            sequence_no=4,
            is_active=True,
        )

        self.prelim_cs = GradingTemplateComponent.objects.create(
            template_period=self.period_prelim,
            code="PRELIM_CS",
            name="Class Standing",
            weight_percentage=Decimal("60.00"),
            sort_order=1,
            is_active=True,
        )
        self.prelim_exam = GradingTemplateComponent.objects.create(
            template_period=self.period_prelim,
            code="PRELIM_EXAM",
            name="Prelim Exam",
            weight_percentage=Decimal("40.00"),
            sort_order=2,
            is_active=True,
        )
        self.midterm_cs = GradingTemplateComponent.objects.create(
            template_period=self.period_midterm,
            code="MIDTERM_CS",
            name="Class Standing",
            weight_percentage=Decimal("60.00"),
            sort_order=1,
            is_active=True,
        )
        self.midterm_exam = GradingTemplateComponent.objects.create(
            template_period=self.period_midterm,
            code="MIDTERM_EXAM",
            name="Midterm Exam",
            weight_percentage=Decimal("40.00"),
            sort_order=2,
            is_active=True,
        )
        self.prefinal_component = GradingTemplateComponent.objects.create(
            template_period=self.period_prefinal,
            code="PREFINAL_CS",
            name="Pre-Final Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )
        self.final_exam_component = GradingTemplateComponent.objects.create(
            template_period=self.period_final_exam,
            code="FINAL_EXAM",
            name="Final Exam",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )

        self.user = User.objects.create_user(
            username="template_calc_admin",
            email="template_calc_admin@example.com",
            password="testpass123",
            first_name="Template",
            last_name="Admin",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin")
        admin_access = Permission.objects.create(code="admin_portal.access", module="admin_portal", action="access")
        template_read = Permission.objects.create(
            code="grading_templates.read",
            module="grading_templates",
            action="read",
        )
        RolePermission.objects.create(role=role, permission=admin_access)
        RolePermission.objects.create(role=role, permission=template_read)
        UserRole.objects.create(
            user=self.user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

    def test_get_calculator_prefills_template_with_default_sample(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("admin_portal:grading_template_calculator"),
            {"grading_template": self.template.id, "sample_value": "85.00"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grading Template Testing Calculator")
        self.assertContains(response, "Testing Template")
        self.assertContains(response, "Final Grade")
        self.assertContains(response, "85.00")

    def test_get_calculator_handles_average_activity_subcomponent_details(self):
        subcomponent = GradingTemplateSubcomponent.objects.create(
            template_component=self.prelim_cs,
            code="PG_CA_PO",
            name="Participation/Output",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            is_active=True,
        )
        GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="RECITATION",
            name="Recitation",
            weight_percentage=Decimal("40.00"),
            sort_order=1,
            is_active=True,
        )
        GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="ASSIGNMENT",
            name="Assignment",
            weight_percentage=Decimal("30.00"),
            sort_order=2,
            is_active=True,
        )
        GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="ACTIVITY",
            name="Activity",
            weight_percentage=Decimal("30.00"),
            sort_order=3,
            is_active=True,
        )

        self.client.force_login(self.user)
        response = self.client.get(
            reverse("admin_portal:grading_template_calculator"),
            {"grading_template": self.template.id, "sample_value": "85.00"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Participation/Output")
        self.assertContains(response, "Final Grade")

    def test_post_calculator_computes_periods_and_final_grade(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("admin_portal:grading_template_calculator"),
            {
                "grading_template": self.template.id,
                "sample_value": "85.00",
                f"component_{self.prelim_cs.id}_raw": "90.00",
                f"component_{self.prelim_cs.id}_total": "100.00",
                f"component_{self.prelim_exam.id}_raw": "80.00",
                f"component_{self.prelim_exam.id}_total": "100.00",
                f"component_{self.midterm_cs.id}_raw": "70.00",
                f"component_{self.midterm_cs.id}_total": "100.00",
                f"component_{self.midterm_exam.id}_raw": "60.00",
                f"component_{self.midterm_exam.id}_total": "100.00",
                f"component_{self.prefinal_component.id}_raw": "88.00",
                f"component_{self.prefinal_component.id}_total": "100.00",
                f"component_{self.final_exam_component.id}_raw": "92.00",
                f"component_{self.final_exam_component.id}_total": "100.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Final Grade Formula Used")
        self.assertContains(response, "91.50")
        self.assertContains(response, "Prelim")
        self.assertContains(response, "Midterm")
        self.assertContains(response, "Pre-Final Class Standing")
        self.assertContains(response, "Final Exam")
        self.assertContains(response, "Final Grade Computation")
        content = response.content.decode()
        self.assertGreater(content.index("Final Grade Computation"), content.index("Final Exam"))

    def test_calculator_uses_weighted_tenant_grading_profile_final_formula(self):
        TenantGradingProfile.objects.create(
            tenant=self.tenant,
            profile_code="REG-WEIGHTED",
            profile_name="Regular Weighted Formula",
            grading_template=self.template,
            final_grade_formula_mode=TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS,
            final_grade_formula_json={
                "period_weights": [
                    {"period_code": "PRELIM", "weight": "50.00"},
                    {"period_code": "MIDTERM", "weight": "50.00"},
                ]
            },
            is_active=True,
        )

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("admin_portal:grading_template_calculator"),
            {
                "grading_template": self.template.id,
                "sample_value": "85.00",
                f"component_{self.prelim_cs.id}_raw": "90.00",
                f"component_{self.prelim_cs.id}_total": "100.00",
                f"component_{self.prelim_exam.id}_raw": "80.00",
                f"component_{self.prelim_exam.id}_total": "100.00",
                f"component_{self.midterm_cs.id}_raw": "70.00",
                f"component_{self.midterm_cs.id}_total": "100.00",
                f"component_{self.midterm_exam.id}_raw": "60.00",
                f"component_{self.midterm_exam.id}_total": "100.00",
                f"component_{self.prefinal_component.id}_raw": "88.00",
                f"component_{self.prefinal_component.id}_total": "100.00",
                f"component_{self.final_exam_component.id}_raw": "92.00",
                f"component_{self.final_exam_component.id}_total": "100.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "REG-WEIGHTED")
        self.assertContains(response, "Weighted Selected Periods")
        self.assertContains(response, "(93.00 x 50.00%) + (83.00 x 50.00%) = 88.00")
        self.assertContains(response, "Official Rounded Final Grade")
