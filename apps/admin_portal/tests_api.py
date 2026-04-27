from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.enrollment.models import Enrollment
from apps.grading.models import GradeSubmission, GradingTemplate, GradingTemplatePeriod, StudentPeriodGrade
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


@override_settings(SIS_API_TOKEN="test-sis-token")
class SISPeriodicGradesApiTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus_fairv = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRV", name="Fairview")
        self.campus_cubao = Campus.objects.create(tenant=self.tenant, code="NCBA-CUBAO", name="Cubao")

        self.department_fairv = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus_fairv,
            code="IT",
            name="Information Technology",
        )
        self.department_cubao = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus_cubao,
            code="IT",
            name="Information Technology",
        )

        self.program_fairv = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus_fairv,
            department=self.department_fairv,
            code="BSIT",
            name="BSIT",
        )
        self.program_cubao = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus_cubao,
            department=self.department_cubao,
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
            campus=self.campus_fairv,
            department=self.department_fairv,
            code="A132-ITAPPS",
            title="IT Applications",
        )
        self.section_code = "BSIT 1-1A"
        self.section_fairv = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus_fairv,
            department=self.department_fairv,
            program=self.program_fairv,
            code=self.section_code,
            name=self.section_code,
            year_level="1",
        )
        self.section_cubao = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus_cubao,
            department=self.department_cubao,
            program=self.program_cubao,
            code=self.section_code,
            name=self.section_code,
            year_level="1",
        )

        self.offering_fairv = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus_fairv,
            department=self.department_fairv,
            program=self.program_fairv,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section_fairv,
        )
        self.offering_cubao = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus_cubao,
            department=self.department_cubao,
            program=self.program_cubao,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section_cubao,
        )

        self.student_no = "2025-10606"
        self.student_fairv = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus_fairv,
            department=self.department_fairv,
            program=self.program_fairv,
            student_no=self.student_no,
            last_name="BAUTISTA",
            first_name="KENJIE",
        )
        self.student_cubao = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus_cubao,
            department=self.department_cubao,
            program=self.program_cubao,
            student_no=self.student_no,
            last_name="BAUTISTA",
            first_name="KENJIE",
        )

        self.faculty_user = User.objects.create_user(
            username="faculty_api",
            email="faculty_api@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus_fairv,
            default_department=self.department_fairv,
        )

        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED_COURSES_V1",
            name="General Education Template",
            is_published=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="GENED_PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )

        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus_fairv,
            academic_year=self.academic_year,
            term=self.term,
            student=self.student_fairv,
            course_offering=self.offering_fairv,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus_cubao,
            academic_year=self.academic_year,
            term=self.term,
            student=self.student_cubao,
            course_offering=self.offering_cubao,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )

        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus_fairv,
            offering=self.offering_fairv,
            template_period=self.period,
            student=self.student_fairv,
            class_standing_grade=Decimal("93.05"),
            exam_grade=Decimal("99.50"),
            period_grade=Decimal("95.63"),
            is_finalized=True,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus_cubao,
            offering=self.offering_cubao,
            template_period=self.period,
            student=self.student_cubao,
            class_standing_grade=Decimal("83.05"),
            exam_grade=Decimal("89.50"),
            period_grade=Decimal("85.63"),
            is_finalized=True,
        )

        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus_fairv,
            offering=self.offering_fairv,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus_cubao,
            offering=self.offering_cubao,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
        )

    def _api_get(self, params: dict):
        return self.client.get(
            "/api/v1/sis/periodic-grades/",
            data=params,
            HTTP_X_API_TOKEN="test-sis-token",
        )

    def test_requires_campus_when_section_is_provided(self):
        response = self._api_get({"tenant_code": self.tenant.code, "section_code": self.section_code})
        self.assertEqual(response.status_code, 400)
        self.assertIn("campus_code", response.json().get("error", ""))

    def test_requires_campus_and_section_when_student_is_provided(self):
        response = self._api_get({"tenant_code": self.tenant.code, "student_no": self.student_no})
        self.assertEqual(response.status_code, 400)
        self.assertIn("campus_code", response.json().get("error", ""))

        response = self._api_get(
            {
                "tenant_code": self.tenant.code,
                "campus_code": self.campus_fairv.code,
                "student_no": self.student_no,
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("section_code", response.json().get("error", ""))

    def test_campus_section_student_filters_return_scoped_student_period_grade(self):
        response = self._api_get(
            {
                "tenant_code": self.tenant.code,
                "campus_code": self.campus_fairv.code,
                "section_code": self.section_code,
                "student_no": self.student_no,
                "academic_year_code": self.academic_year.code,
                "term_code": self.term.code,
                "period_code": self.period.code,
            }
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total_count"], 1)
        row = payload["results"][0]
        self.assertEqual(row["campus_code"], self.campus_fairv.code)
        self.assertEqual(row["section_code"], self.section_code)
        self.assertEqual(row["student_no"], self.student_no)
        self.assertEqual(row["class_standing_grade"], "93")
        self.assertEqual(row["exam_grade"], "100")
        self.assertEqual(row["period_grade"], "96")
