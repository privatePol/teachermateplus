from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentFinalGrade,
    StudentPeriodGrade,
    StudentActivityScore,
)
from apps.grading.services import FacultyGradingService
from apps.predictions.services import PredictionComputationService, PredictionSnapshotService, PredictionWhatIfService
from apps.rbac.models import Role, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class PredictionSnapshotTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="FVW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="IS",
            name="Information Systems",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSIS",
            name="BSIS",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="2025-2026",
            start_date="2025-06-01",
            end_date="2026-03-31",
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="1st",
            sequence_no=1,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            code="A132-ITAPPS",
            title="IT Application Tools",
            default_base_value=Decimal("50.00"),
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSA1",
            name="BSA 1",
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
        self.user = User.objects.create_user(
            username="faculty",
            email="faculty@ncba.edu.ph",
            password="StrongPass123!",
            first_name="Faculty",
            last_name="Member",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        role = Role.objects.create(code="FACULTY", name="Faculty")
        UserRole.objects.create(
            user=self.user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            is_active=True,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-0001",
            first_name="Juan",
            last_name="Dela Cruz",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            course_offering=self.offering,
            student=self.student,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="COLL",
            name="College",
            default_base_value=Decimal("50.00"),
            is_published=True,
            is_active=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("25.00"),
        )
        component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="PG_CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        CourseTemplateAssignment.objects.create(course=self.course, grading_template=self.template, is_active=True)
        self.activity_1 = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=component,
            title="Q1",
            total_score=Decimal("100.00"),
            created_by_user=self.user,
            is_active=True,
        )
        self.activity_2 = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=component,
            title="Q2",
            total_score=Decimal("100.00"),
            created_by_user=self.user,
            is_active=True,
        )
        StudentActivityScore.objects.create(
            activity=self.activity_1,
            student=self.student,
            raw_score=Decimal("80.00"),
            computed_score=Decimal("90.00"),
            encoded_by_user=self.user,
            is_active=True,
        )

    def test_prediction_snapshot_computes_current_best_worst(self):
        result = PredictionComputationService.refresh_offering_period(
            offering=self.offering,
            template_period=self.period,
            user=self.user,
        )
        row = result["rows"][0]
        self.assertEqual(row.current_projected_period_grade, Decimal("90.00"))
        self.assertEqual(row.worst_case_period_grade, Decimal("70.00"))
        self.assertEqual(row.best_case_period_grade, Decimal("95.00"))
        self.assertEqual(row.target_needed_percent, Decimal("20.00"))

    def test_what_if_simulator_interpolates_between_bounds(self):
        result = PredictionSnapshotService.get_period_predictions(
            offering=self.offering,
            template_period=self.period,
            user=self.user,
            force_refresh=True,
        )
        row = result["rows"][0]
        scenario = PredictionWhatIfService.simulate(snapshot=row, assumed_remaining_percent=Decimal("50.00"))
        self.assertEqual(scenario["projected_period_grade"], Decimal("82.50"))

    def test_completed_period_prediction_matches_official_gradebook_values(self):
        StudentActivityScore.objects.create(
            activity=self.activity_2,
            student=self.student,
            raw_score=Decimal("100.00"),
            computed_score=Decimal("100.00"),
            encoded_by_user=self.user,
            is_active=True,
        )
        FacultyGradingService.recompute_period_summary(
            user=self.user,
            offering=self.offering,
            template_period=self.period,
        )

        result = PredictionSnapshotService.get_period_predictions(
            offering=self.offering,
            template_period=self.period,
            user=self.user,
            force_refresh=True,
        )
        row = result["rows"][0]
        official_period = StudentPeriodGrade.objects.get(
            offering=self.offering,
            template_period=self.period,
            student=self.student,
        )
        official_final = StudentFinalGrade.objects.get(
            offering=self.offering,
            student=self.student,
        )

        self.assertEqual(row.remaining_item_count, 0)
        self.assertEqual(row.current_projected_period_grade, official_period.period_grade)
        self.assertEqual(row.best_case_period_grade, official_period.period_grade)
        self.assertEqual(row.worst_case_period_grade, official_period.period_grade)
        self.assertEqual(row.current_projected_final_grade, official_final.final_grade)

    def test_final_requirement_for_remaining_periods_reports_needed_average(self):
        midterm = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
            weight_percentage=Decimal("25.00"),
        )
        prefinal = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PREFINAL",
            name="Pre-Final",
            sequence_no=3,
            weight_percentage=Decimal("25.00"),
        )
        final_exam = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="FINAL",
            name="FX",
            sequence_no=4,
            weight_percentage=Decimal("25.00"),
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            student=self.student,
            class_standing_grade=Decimal("91.43"),
            period_grade=Decimal("91.43"),
        )

        requirement = PredictionComputationService.final_requirement_for_remaining_periods(
            offering=self.offering,
            template_period=midterm,
            student_id=self.student.id,
            current_period_grade=Decimal("99.80"),
        )

        self.assertEqual(requirement["status"], "REQUIRED")
        self.assertEqual(requirement["required_average"], Decimal("54.38"))
        self.assertEqual(requirement["remaining_period_names"], ["Pre-Final", "FX"])
