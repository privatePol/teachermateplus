from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.admin_portal.academic_performance import AcademicPerformanceInsightService
from apps.admin_portal.midterm_exam_performance import (
    MidtermExamPerformanceReportService, _BulkGradingConfiguration,
)
from apps.admin_portal.views import midterm_exam_performance_view
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.departmental_exams.exam_units import ExamCourseEquivalencyService, resolve_examination_unit
from apps.departmental_exams.models import (
    CourseExamConfiguration,
    CycleCourse,
    CycleCourseOffering,
    ExaminationCycle,
    _classification_service_scope,
)
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseBaseValueOverride, CourseTemplateAssignment, GradeSubmission,
    GradingTemplate, GradingTemplatePeriod, StudentPeriodGrade,
    TenantGradingProfile,
)
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.grading.services import FacultyGradingService
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class MidtermExamPerformanceReportTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="TMP", name="TeacherMate Plus")
        self.fairview = self._campus_scope("FV", "Fairview")
        self.cubao = self._campus_scope("CUB", "Cubao")
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2026-2027",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Semester",
            sequence_no=1,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 10, 31),
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="MIDTERM-REPORT",
            name="Midterm Report Template",
            is_active=True,
            is_published=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=1,
        )
        self.admin = self._user(
            "cao",
            self.fairview[0],
            self.fairview[1],
            first_name="Chief",
            last_name="Academic Officer",
        )
        self.faculty_fv = self._faculty("faculty-fv", self.fairview)
        self.faculty_cub = self._faculty("faculty-cub", self.cubao)
        self.cao_role = Role.objects.create(code="CAO", name="Chief Academic Officer")
        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        for permission_code in (
            "admin_portal.access",
            "dashboard.read",
            "grading_analytics.read",
            "departmental_exams.manage_exam_generation",
        ):
            permission, _created = Permission.objects.get_or_create(
                code=permission_code,
                defaults={
                    "module": permission_code.split(".")[0],
                    "action": permission_code.split(".")[-1],
                },
            )
            RolePermission.objects.get_or_create(role=self.cao_role, permission=permission)
        for scope in (self.fairview, self.cubao):
            UserRole.objects.create(
                user=self.admin,
                role=self.cao_role,
                tenant=self.tenant,
                campus=scope[0],
                department=None,
            )
        for faculty, scope in ((self.faculty_fv, self.fairview), (self.faculty_cub, self.cubao)):
            UserRole.objects.create(
                user=faculty,
                role=self.faculty_role,
                tenant=self.tenant,
                campus=scope[0],
                department=scope[1],
            )
        self.cycle = ExaminationCycle.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            term=self.term,
            exam_period=ExaminationCycle.ExamPeriod.MIDTERM,
            status=ExaminationCycle.Status.OPEN,
            processing_mode=ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION,
            created_by=self.admin,
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            code="IS-MID",
            title="Midterm Information Systems",
        )
        self.cycle_course = self._cycle_course(
            self.course,
            CycleCourse.ExamClassification.STANDARDIZED,
        )
        self._configuration(self.cycle_course)
        SystemSettingService.set(
            FeatureSettingsService.ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        self.url = reverse("admin_portal:midterm_exam_performance")

    def test_complete_results_preserve_zero_and_missing_result_stays_blank(self):
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.cycle_course.pk).update(
                exam_classification=CycleCourse.ExamClassification.UNCLASSIFIED_LEGACY,
            )
        complete = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "FV-1")
        students = [self._student(complete, "FV-001"), self._student(complete, "FV-002")]
        self._submitted_results(complete, zip(students, (Decimal("0"), Decimal("80"))))

        incomplete = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "FV-2")
        missing_students = [self._student(incomplete, "FV-003"), self._student(incomplete, "FV-004")]
        self._submission(incomplete, GradeSubmission.Status.SUBMITTED)
        self._period_grade(incomplete, missing_students[0], Decimal("75"), finalized=True)
        invalid_scale = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "FV-3")
        invalid_student = self._student(invalid_scale, "FV-005")
        self._submitted_results(invalid_scale, ((invalid_student, Decimal("101")),))

        rows = self._get_rows()
        complete_row = self._row(rows, "FV-1")
        incomplete_row = self._row(rows, "FV-2")
        invalid_scale_row = self._row(rows, "FV-3")
        self.assertEqual(complete_row["student_count"], 2)
        self.assertEqual(complete_row["lowest_score"], Decimal("0.00"))
        self.assertEqual(complete_row["highest_score"], Decimal("80.00"))
        self.assertEqual(complete_row["class_average"], Decimal("40.00"))
        self.assertEqual(complete_row["rank"], 1)
        self.assertEqual(incomplete_row["student_count"], 2)
        self.assertEqual(incomplete_row["data_status"], MidtermExamPerformanceReportService.STATUS_INCOMPLETE)
        self.assertIsNone(incomplete_row["class_average"])
        self.assertIsNone(incomplete_row["highest_score"])
        self.assertIsNone(incomplete_row["lowest_score"])
        self.assertIsNone(incomplete_row["rank"])
        self.assertEqual(
            invalid_scale_row["data_status"],
            MidtermExamPerformanceReportService.STATUS_INVALID_SCALE,
        )
        self.assertIsNone(invalid_scale_row["class_average"])

    def test_submission_lifecycle_and_sparse_roster_have_explicit_statuses(self):
        no_roster = self._offering(self.cycle_course, self.cubao, self.faculty_cub, "CUB-EMPTY")
        draft = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "FV-DRAFT")
        draft_student = self._student(draft, "FV-010")
        self._submission(draft, GradeSubmission.Status.DRAFT)
        self._period_grade(draft, draft_student, Decimal("91"), finalized=True)
        reopened = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "FV-REOPEN")
        reopened_student = self._student(reopened, "FV-011")
        self._submission(reopened, GradeSubmission.Status.REOPENED)
        self._period_grade(reopened, reopened_student, Decimal("92"), finalized=False)

        rows = self._get_rows()
        self.assertEqual(
            self._row(rows, "CUB-EMPTY")["data_status"],
            MidtermExamPerformanceReportService.STATUS_NO_ROSTER,
        )
        self.assertEqual(
            self._row(rows, "FV-DRAFT")["data_status"],
            MidtermExamPerformanceReportService.STATUS_NOT_SUBMITTED,
        )
        self.assertEqual(
            self._row(rows, "FV-REOPEN")["data_status"],
            MidtermExamPerformanceReportService.STATUS_REOPENED,
        )

    def test_resolved_equivalency_group_ranks_ties_as_one_one_three(self):
        equivalent_course = Course.objects.create(
            tenant=self.tenant,
            code="IS-MID-EQ",
            title="Equivalent Midterm Information Systems",
        )
        equivalent_cycle_course = self._cycle_course(
            equivalent_course,
            CycleCourse.ExamClassification.STANDARDIZED,
        )
        self._configuration(equivalent_cycle_course)
        first = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "EQ-1")
        second = self._offering(equivalent_cycle_course, self.cubao, self.faculty_cub, "EQ-2")
        third = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "EQ-3")
        ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id,
            name="Shared IS Midterm",
            primary_cycle_course_id=self.cycle_course.id,
            member_ids=(self.cycle_course.id, equivalent_cycle_course.id),
            actor=self.admin,
        )
        for index, (offering, value) in enumerate(
            ((first, Decimal("90")), (second, Decimal("90")), (third, Decimal("80"))),
            start=1,
        ):
            student = self._student(offering, f"EQ-{index:03d}")
            self._submitted_results(offering, ((student, value),))

        rows = self._get_rows()
        self.assertEqual({row["course_group"] for row in rows}, {"Shared IS Midterm"})
        ranks = {row["section_code"]: row["rank"] for row in rows}
        self.assertEqual(ranks, {"EQ-1": 1, "EQ-2": 1, "EQ-3": 3})
        narrowed_response = self.client.get(
            self.url,
            {"cycle_id": self.cycle.id, "course_code": equivalent_course.code},
        )
        self.assertEqual(narrowed_response.status_code, 200)
        self.assertEqual(
            {row["section_code"]: row["rank"] for row in narrowed_response.context["rows"]},
            ranks,
        )

    def test_legacy_classified_included_course_with_complete_grades_is_reported(self):
        departmental_course = Course.objects.create(tenant=self.tenant, code="DEPT", title="Departmental")
        departmental = self._cycle_course(
            departmental_course,
            CycleCourse.ExamClassification.DEPARTMENTAL,
        )
        legacy_course = Course.objects.create(tenant=self.tenant, code="LEGACY", title="Legacy")
        legacy = self._cycle_course(
            legacy_course,
            CycleCourse.ExamClassification.UNCLASSIFIED_LEGACY,
        )
        dept_offering = self._offering(departmental, self.fairview, self.faculty_fv, "DEPT-1")
        legacy_offering = self._offering(legacy, self.fairview, self.faculty_fv, "LEGACY-1")
        dept_student = self._student(dept_offering, "DEPT-001")
        legacy_student = self._student(legacy_offering, "LEG-001")
        self._submitted_results(dept_offering, ((dept_student, Decimal("88")),))
        self._submitted_results(legacy_offering, ((legacy_student, Decimal("99")),))

        rows = self._get_rows()
        self.assertEqual(self._row(rows, "DEPT-1")["data_status"], "Complete")
        legacy_row = self._row(rows, "LEGACY-1")
        self.assertEqual(legacy_row["data_status"], "Complete")
        self.assertEqual(legacy_row["class_average"], Decimal("99.00"))
        self.assertEqual(legacy_row["rank"], 1)
        self.assertEqual(legacy_row["highest_score"], Decimal("99.00"))
        self.assertEqual(legacy_row["lowest_score"], Decimal("99.00"))
        response = self.client.get(self.url, {"cycle_id": self.cycle.id})
        self.assertNotContains(response, "Legacy classification unconfirmed")
        self.assertNotContains(response, "UNCLASSIFIED_LEGACY")

    def test_report_accepts_legacy_unit_while_builder_preserves_classification_rule(self):
        course = Course.objects.create(tenant=self.tenant, code="LEG-EQ", title="Legacy equivalent")
        member = self._cycle_course(course, CycleCourse.ExamClassification.STANDARDIZED)
        self._configuration(member)
        first = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "LEG-EQ-1")
        second = self._offering(member, self.cubao, self.faculty_cub, "LEG-EQ-2")
        ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Historical Midterm unit",
            primary_cycle_course_id=self.cycle_course.id,
            member_ids=(self.cycle_course.id, member.id), actor=self.admin,
        )
        for index, (offering, grade) in enumerate(((first, "90"), (second, "80"))):
            student = self._student(offering, f"LEG-EQ-{index}")
            self._submitted_results(offering, ((student, Decimal(grade)),))

        # Simulate historical metadata only in the disposable test database.
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=member.pk).update(
                exam_classification=CycleCourse.ExamClassification.UNCLASSIFIED_LEGACY,
            )
        with self.assertRaisesMessage(ValidationError, "same explicit exam classification"):
            resolve_examination_unit(self.cycle_course)
        rows = self._get_rows()
        self.assertEqual({row["course_group"] for row in rows}, {"Historical Midterm unit"})
        self.assertEqual({row["data_status"] for row in rows}, {"Complete"})
        self.assertEqual({row["section_code"]: row["rank"] for row in rows},
                         {"LEG-EQ-1": 1, "LEG-EQ-2": 2})
        member.refresh_from_db()
        self.assertEqual(member.exam_classification, CycleCourse.ExamClassification.UNCLASSIFIED_LEGACY)

        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.cycle_course.pk).update(
                exam_classification=CycleCourse.ExamClassification.UNCLASSIFIED_LEGACY,
            )
        self.assertEqual({row["data_status"] for row in self._get_rows()}, {"Complete"})

        # Classification independence must not bypass examination-configuration validity.
        CourseExamConfiguration.objects.filter(cycle_course=member).update(
            additional_instructions="Different preserved exam instructions",
        )
        for row in self._get_rows():
            self.assertEqual(row["data_status"], MidtermExamPerformanceReportService.STATUS_INVALID_UNIT)
            for field in ("highest_score", "lowest_score", "class_average", "rank"):
                self.assertIsNone(row[field])

    def test_exempt_course_is_absent_while_included_missing_results_remains_visible(self):
        included = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "INCLUDED")
        self._student(included, "INC-001")
        exempt_course = Course.objects.create(tenant=self.tenant, code="EXEMPT", title="Exempt")
        exempt = CycleCourse.objects.create(
            cycle=self.cycle,
            course=exempt_course,
            responsible_department=self.fairview[1],
            inclusion_status=CycleCourse.InclusionStatus.EXEMPT,
            exam_classification=CycleCourse.ExamClassification.UNCLASSIFIED_LEGACY,
            exemption_category=CycleCourse.ExemptionCategory.PRACTICUM_OJT,
            exemption_reason="Approved practical examination exemption.",
            exemption_changed_by=self.admin,
            exemption_changed_at=timezone.now(),
        )
        self._offering(exempt, self.fairview, self.faculty_fv, "EXEMPT")

        rows = self._get_rows()
        self.assertEqual([row["section_code"] for row in rows], ["INCLUDED"])
        self.assertEqual(
            rows[0]["data_status"],
            MidtermExamPerformanceReportService.STATUS_NOT_SUBMITTED,
        )

    def test_campus_direct_deny_excludes_only_that_campus(self):
        fairview = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "FV-ALLOW")
        cubao = self._offering(self.cycle_course, self.cubao, self.faculty_cub, "CUB-DENY")
        for index, offering in enumerate((fairview, cubao), start=1):
            student = self._student(offering, f"DENY-{index:03d}")
            self._submitted_results(offering, ((student, Decimal("85")),))
        UserPermission.objects.create(
            user=self.admin,
            permission=Permission.objects.get(code="grading_analytics.read"),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.cubao[0],
        )

        rows = self._get_rows()
        self.assertEqual([row["section_code"] for row in rows], ["FV-ALLOW"])

    def test_department_scope_excludes_other_faculty_loads_in_same_campus(self):
        chair = self._user("area-chair", self.fairview[0], self.fairview[1])
        chair_role = Role.objects.create(code="AREA_CHAIR", name="Area Chair")
        for code in ("admin_portal.access", "grading_analytics.read"):
            RolePermission.objects.create(
                role=chair_role,
                permission=Permission.objects.get(code=code),
            )
        UserRole.objects.create(
            user=chair,
            role=chair_role,
            tenant=self.tenant,
            campus=self.fairview[0],
            department=self.fairview[1],
        )
        unrelated_role = Role.objects.create(code="UNRELATED_ADMIN", name="Unrelated Admin")
        RolePermission.objects.create(
            role=unrelated_role,
            permission=Permission.objects.get(code="admin_portal.access"),
        )
        UserRole.objects.create(
            user=chair,
            role=unrelated_role,
            tenant=self.tenant,
            campus=self.fairview[0],
            department=None,
        )
        own = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "OWN-DEPT")
        own_student = self._student(own, "OWN-001")
        self._submitted_results(own, ((own_student, Decimal("86")),))

        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.fairview[0],
            code="FV-OTHER",
            name="Fairview Other Department",
        )
        other_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.fairview[0],
            department=other_department,
            code="FV-OTHER-P",
            name="Fairview Other Program",
        )
        other_faculty = self._user("faculty-other", self.fairview[0], other_department)
        UserRole.objects.create(
            user=other_faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.fairview[0],
            department=other_department,
        )
        other_scope = (self.fairview[0], other_department, other_program)
        other = self._offering(self.cycle_course, other_scope, other_faculty, "OTHER-DEPT")
        other_student = self._student(other, "OTHER-001")
        self._submitted_results(other, ((other_student, Decimal("99")),))

        self.client.force_login(chair)
        response = self.client.get(self.url, {"cycle_id": self.cycle.id})

        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertEqual([row["section_code"] for row in response.context["rows"]], ["OWN-DEPT"])

    def test_direct_allow_uses_campus_wide_department_scope_without_unrelated_role_grant(self):
        viewer = self._user("direct-allow", self.fairview[0], self.fairview[1])
        unrelated_role = Role.objects.create(code="DIRECT_ALLOW_PORTAL", name="Portal Only")
        RolePermission.objects.create(
            role=unrelated_role, permission=Permission.objects.get(code="admin_portal.access"),
        )
        UserRole.objects.create(
            user=viewer, role=unrelated_role, tenant=self.tenant,
            campus=self.fairview[0], department=self.fairview[1],
        )
        UserPermission.objects.create(
            user=viewer, permission=Permission.objects.get(code="grading_analytics.read"),
            grant_type=UserPermission.GrantType.ALLOW, tenant=self.tenant,
            campus=self.fairview[0],
        )
        other_department = Department.objects.create(
            tenant=self.tenant, campus=self.fairview[0], code="ALLOW-OTHER", name="Allowed Other",
        )
        other_program = Program.objects.create(
            tenant=self.tenant, campus=self.fairview[0], department=other_department,
            code="ALLOW-P", name="Allowed Program",
        )
        other_faculty = self._user("allow-faculty", self.fairview[0], other_department)
        UserRole.objects.create(
            user=other_faculty, role=self.faculty_role, tenant=self.tenant,
            campus=self.fairview[0], department=other_department,
        )
        self._offering(
            self.cycle_course, (self.fairview[0], other_department, other_program),
            other_faculty, "DIRECT-ALLOW-OTHER",
        )
        self._offering(self.cycle_course, self.cubao, self.faculty_cub, "DIRECT-ALLOW-CUB")

        self.client.force_login(viewer)
        response = self.client.get(self.url, {"cycle_id": self.cycle.id})
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertEqual(
            [row["section_code"] for row in response.context["rows"]],
            ["DIRECT-ALLOW-OTHER"],
        )

    def test_shared_open_offering_with_null_program_is_visible_only_in_department_scope(self):
        chair = self._user("shared-chair", self.fairview[0], self.fairview[1])
        chair_role = Role.objects.create(code="SHARED_CHAIR", name="Shared Chair")
        for code in ("admin_portal.access", "grading_analytics.read"):
            RolePermission.objects.create(role=chair_role, permission=Permission.objects.get(code=code))
        UserRole.objects.create(
            user=chair, role=chair_role, tenant=self.tenant,
            campus=self.fairview[0], department=self.fairview[1],
        )
        own = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "SHARED-OWN")
        own.program = None
        own.save(update_fields=["program"])
        other_department = Department.objects.create(
            tenant=self.tenant, campus=self.fairview[0], code="SHARED-OTHER", name="Other Department",
        )
        other_program = Program.objects.create(
            tenant=self.tenant, campus=self.fairview[0], department=other_department,
            code="SHARED-P", name="Other Program",
        )
        other_faculty = self._user("shared-other-faculty", self.fairview[0], other_department)
        UserRole.objects.create(
            user=other_faculty, role=self.faculty_role, tenant=self.tenant,
            campus=self.fairview[0], department=other_department,
        )
        other = self._offering(
            self.cycle_course, (self.fairview[0], other_department, other_program),
            other_faculty, "SHARED-OTHER",
        )
        other.program = None
        other.save(update_fields=["program"])

        self.client.force_login(chair)
        response = self.client.get(self.url, {"cycle_id": self.cycle.id})
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertEqual([row["section_code"] for row in response.context["rows"]], ["SHARED-OWN"])
        self.assertEqual(response.context["rows"][0]["data_status"], "No eligible roster")

    def test_incompatible_grading_context_keeps_statistics_but_blocks_unit_ranks(self):
        other_template = GradingTemplate.objects.create(
            tenant=self.tenant, code="MIDTERM-OTHER", name="Other Midterm",
            is_active=True, is_published=True,
        )
        other_period = GradingTemplatePeriod.objects.create(
            template=other_template, code="MIDTERM", name="Midterm", sequence_no=1,
        )
        for scope, template, code in (
            (self.fairview, self.template, "PROFILE-FV"),
            (self.cubao, other_template, "PROFILE-CUB"),
        ):
            TenantGradingProfile.objects.create(
                tenant=self.tenant, campus=scope[0], profile_code=code,
                profile_name=code, grading_template=template,
            )
        first = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "CONTEXT-FV")
        second = self._offering(self.cycle_course, self.cubao, self.faculty_cub, "CONTEXT-CUB")
        first_student = self._student(first, "CONTEXT-001")
        second_student = self._student(second, "CONTEXT-002")
        self._submitted_results(first, ((first_student, Decimal("85")),))
        self._submitted_results(second, ((second_student, Decimal("90")),), period=other_period)

        rows = self._get_rows()
        self.assertEqual({row["class_average"] for row in rows}, {Decimal("85.00"), Decimal("90.00")})
        self.assertEqual(
            {row["data_status"] for row in rows},
            {MidtermExamPerformanceReportService.STATUS_NOT_COMPARABLE},
        )
        self.assertTrue(all(row["rank"] is None for row in rows))

    def test_equivalent_courses_with_different_effective_base_values_are_not_comparable(self):
        other_course = Course.objects.create(
            tenant=self.tenant, code="IS-BASE-OTHER", title="Other Base Value",
            default_base_value=Decimal("60.00"),
        )
        self.course.default_base_value = Decimal("40.00")
        self.course.save(update_fields=["default_base_value"])
        other_cycle_course = self._cycle_course(
            other_course, CycleCourse.ExamClassification.STANDARDIZED,
        )
        self._configuration(other_cycle_course)
        first = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "BASE-FIRST")
        second = self._offering(other_cycle_course, self.fairview, self.faculty_fv, "BASE-SECOND")
        ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Base Value Equivalency",
            primary_cycle_course_id=self.cycle_course.id,
            member_ids=(self.cycle_course.id, other_cycle_course.id), actor=self.admin,
        )
        for index, offering in enumerate((first, second), start=1):
            student = self._student(offering, f"BASE-{index:03d}")
            self._submitted_results(offering, ((student, Decimal("80")),))

        rows = self._get_rows()
        self.assertEqual({row["class_average"] for row in rows}, {Decimal("80.00")})
        self.assertEqual({row["data_status"] for row in rows}, {"Not comparable"})
        self.assertTrue(all(row["rank"] is None for row in rows))

        self.course.default_base_value = Decimal("60.00")
        self.course.save(update_fields=["default_base_value"])
        CourseBaseValueOverride.objects.create(
            course=self.course, base_value=Decimal("45.00"), effective_from_term=self.term,
        )
        override_rows = self._get_rows()
        self.assertEqual({row["data_status"] for row in override_rows}, {"Not comparable"})
        self.assertTrue(all(row["rank"] is None for row in override_rows))

    def test_partial_department_scope_hides_group_name_and_hidden_member_code(self):
        chair = self._user("partial-chair", self.fairview[0], self.fairview[1])
        chair_role = Role.objects.create(code="PARTIAL_CHAIR", name="Partial Chair")
        for code in ("admin_portal.access", "grading_analytics.read"):
            RolePermission.objects.create(role=chair_role, permission=Permission.objects.get(code=code))
        UserRole.objects.create(
            user=chair, role=chair_role, tenant=self.tenant,
            campus=self.fairview[0], department=self.fairview[1],
        )
        hidden_department = Department.objects.create(
            tenant=self.tenant, campus=self.fairview[0], code="HIDDEN-DEPT", name="Hidden Department",
        )
        hidden_program = Program.objects.create(
            tenant=self.tenant, campus=self.fairview[0], department=hidden_department,
            code="HIDDEN-P", name="Hidden Program",
        )
        hidden_faculty = self._user("hidden-faculty", self.fairview[0], hidden_department)
        UserRole.objects.create(
            user=hidden_faculty, role=self.faculty_role, tenant=self.tenant,
            campus=self.fairview[0], department=hidden_department,
        )
        hidden_course = Course.objects.create(
            tenant=self.tenant, code="HIDDEN-MEMBER", title="Hidden Member",
        )
        hidden_cycle_course = self._cycle_course(
            hidden_course, CycleCourse.ExamClassification.STANDARDIZED,
        )
        self._configuration(hidden_cycle_course)
        self._offering(self.cycle_course, self.fairview, self.faculty_fv, "VISIBLE-SECTION")
        self._offering(
            hidden_cycle_course,
            (self.fairview[0], hidden_department, hidden_program),
            hidden_faculty, "HIDDEN-SECTION",
        )
        ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Secret Full Group Name",
            primary_cycle_course_id=self.cycle_course.id,
            member_ids=(self.cycle_course.id, hidden_cycle_course.id), actor=self.admin,
        )

        self.client.force_login(chair)
        response = self.client.get(self.url, {"cycle_id": self.cycle.id})
        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertEqual([row["section_code"] for row in response.context["rows"]], ["VISIBLE-SECTION"])
        self.assertEqual(response.context["rows"][0]["member_codes"], ("IS-MID",))
        self.assertEqual(response.context["rows"][0]["course_group"], "Scoped examination unit")
        self.assertNotContains(response, "HIDDEN-MEMBER")
        self.assertNotContains(response, "Secret Full Group Name")
        hidden_code_response = self.client.get(
            self.url, {"cycle_id": self.cycle.id, "course_code": hidden_course.code},
        )
        self.assertEqual(hidden_code_response.status_code, 200)
        self.assertEqual(hidden_code_response.context["rows"], [])

    def test_bulk_queries_do_not_grow_with_same_context_offerings(self):
        request = RequestFactory().get(self.url, {"cycle_id": self.cycle.id})
        request.user = self.admin
        request.scope = {
            "tenant_ids": [self.tenant.id],
            "campus_ids": [self.fairview[0].id, self.cubao[0].id],
            "department_ids": [self.fairview[1].id],
            "tenant_id": self.tenant.id,
            "campus_id": self.fairview[0].id,
        }
        def report_queries():
            with CaptureQueriesContext(connection) as queries:
                report = MidtermExamPerformanceReportService.build_report(request, self.cycle)
            return report, len(queries)

        self._offering(self.cycle_course, self.fairview, self.faculty_fv, "GROW-0")
        _single_report, single_count = report_queries()
        for number in range(1, 8):
            self._offering(self.cycle_course, self.fairview, self.faculty_fv, f"GROW-{number}")
        many_report, many_count = report_queries()
        self.assertEqual(len(many_report["rows"]), 8)
        self.assertLessEqual(many_count - single_count, 2, "Queries must not grow per section")

    def test_distinct_course_query_growth_is_bounded_by_unit_page(self):
        request = RequestFactory().get(self.url, {"cycle_id": self.cycle.id})
        request.user = self.admin
        request.scope = {
            "tenant_ids": [self.tenant.id],
            "campus_ids": [self.fairview[0].id, self.cubao[0].id],
            "department_ids": [self.fairview[1].id],
            "tenant_id": self.tenant.id,
            "campus_id": self.fairview[0].id,
        }

        def add_courses(start, stop):
            for number in range(start, stop):
                course = Course.objects.create(
                    tenant=self.tenant, code=f"DIST-{number:02d}",
                    title=f"Distinct Course {number}",
                    default_base_value=Decimal(40 + number),
                )
                cycle_course = self._cycle_course(
                    course, CycleCourse.ExamClassification.STANDARDIZED,
                )
                self._offering(cycle_course, self.fairview, self.faculty_fv, f"DIST-SEC-{number:02d}")

        def count_queries():
            with CaptureQueriesContext(connection) as queries:
                report = MidtermExamPerformanceReportService.build_report(request, self.cycle)
            return report, len(queries)

        add_courses(0, 12)
        baseline, baseline_queries = count_queries()
        add_courses(12, 24)
        expanded, expanded_queries = count_queries()
        self.assertEqual(len(baseline["rows"]), 10)
        self.assertEqual(len(expanded["rows"]), 10)
        self.assertEqual(expanded["unit_page"].paginator.num_pages, 3)
        self.assertLessEqual(
            expanded_queries - baseline_queries, 2,
            "Adding courses beyond the current unit page must not add per-course queries",
        )
        self.client.force_login(self.admin)
        last_page = self.client.get(self.url, {"cycle_id": self.cycle.id, "unit_page": 3})
        self.assertEqual(last_page.status_code, 200)
        self.assertEqual(len(last_page.context["rows"]), 4)

    def test_equivalency_unit_over_two_hundred_offerings_is_retrievable_and_ranked(self):
        equivalent_course = Course.objects.create(
            tenant=self.tenant, code="IS-MID-ZEQ", title="Large Unit Equivalent",
        )
        equivalent_cycle_course = self._cycle_course(
            equivalent_course, CycleCourse.ExamClassification.STANDARDIZED,
        )
        self._configuration(equivalent_cycle_course)
        first = None
        last = None
        for number in range(205):
            offering = self._offering(
                equivalent_cycle_course if number == 204 else self.cycle_course,
                self.fairview, self.faculty_fv, f"BIG-{number:03d}",
            )
            if number == 0:
                first = offering
            elif number == 204:
                last = offering
        ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Large Examination Unit",
            primary_cycle_course_id=self.cycle_course.id,
            member_ids=(self.cycle_course.id, equivalent_cycle_course.id), actor=self.admin,
        )
        first_student = self._student(first, "BIG-FIRST")
        last_student = self._student(last, "BIG-LAST")
        self._submitted_results(first, ((first_student, Decimal("80")),))
        self._submitted_results(last, ((last_student, Decimal("90")),))

        self.client.force_login(self.admin)
        first_page = self.client.get(self.url, {"cycle_id": self.cycle.id})
        last_page = self.client.get(
            self.url, {"cycle_id": self.cycle.id, "row_page": 5},
        )
        self.assertEqual(first_page.status_code, 200, first_page.content.decode())
        self.assertEqual(last_page.status_code, 200, last_page.content.decode())
        self.assertEqual(first_page.context["total_offerings"], 205)
        self.assertEqual(first_page.context["row_page"].paginator.num_pages, 5)
        self.assertEqual(len(first_page.context["rows"]), 50)
        self.assertEqual(len(last_page.context["rows"]), 5)
        self.assertEqual(self._row(first_page.context["rows"], "BIG-000")["rank"], 2)
        self.assertEqual(self._row(last_page.context["rows"], "BIG-204")["rank"], 1)

    def test_one_equivalency_unit_distinct_context_query_growth(self):
        """Growing one unit must not trigger grading lookups per member course."""
        members = []
        self.client.force_login(self.admin)

        def add_members(start, stop):
            for number in range(start, stop):
                course = Course.objects.create(
                    tenant=self.tenant, code=f"UNIT-{number:02d}",
                    title=f"Distinct unit member {number}",
                    default_base_value=Decimal(40 + number),
                )
                member = self._cycle_course(course, CycleCourse.ExamClassification.STANDARDIZED)
                members.append(member.id)
                self._configuration(member)
                template = GradingTemplate.objects.create(
                    tenant=self.tenant, code=f"UNIT-T-{number}", name=f"Template {number}",
                    is_active=True, is_published=True,
                )
                period = GradingTemplatePeriod.objects.create(
                    template=template, code="MIDTERM", name="Midterm", sequence_no=1,
                )
                TenantGradingProfile.objects.create(
                    tenant=self.tenant, course=course, grading_template=template,
                    profile_code=f"UNIT-P-{number}", profile_name=f"Profile {number}",
                )
                offering = self._offering(member, self.fairview, self.faculty_fv, f"UNIT-S-{number}")
                student = self._student(offering, f"UNIT-ST-{number}")
                self._submitted_results(offering, ((student, Decimal("80")),), period=period)

        def measure(size):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url, {"cycle_id": self.cycle.id})
            self.assertEqual(response.status_code, 200)
            rows = response.context["rows"]
            self.assertEqual(len(rows), size)
            self.assertEqual(response.context["unit_page"].paginator.count, 1)
            self.assertEqual(len({row["context_signature"] for row in rows}), size)
            self.assertEqual({row["class_average"] for row in rows}, {Decimal("80.00")})
            self.assertEqual({row["data_status"] for row in rows}, {"Not comparable"})
            self.assertTrue(all(row["rank"] is None for row in rows))
            return len(queries)

        add_members(0, 6)
        group = ExamCourseEquivalencyService.create_group(
            cycle_id=self.cycle.id, name="Many distinct configurations",
            primary_cycle_course_id=members[0], member_ids=members, actor=self.admin,
        )
        small_queries = measure(6)
        add_members(6, 24)
        ExamCourseEquivalencyService.replace_members(
            group_id=group.id, primary_cycle_course_id=members[0],
            member_ids=members, actor=self.admin,
        )
        large_queries = measure(24)
        print(f"Single-unit distinct-context queries: 6={small_queries}, 24={large_queries}")
        self.assertLessEqual(
            large_queries - small_queries, 2,
            "One equivalency unit must bulk-load configuration inputs across distinct courses",
        )

    def test_endpoint_is_get_only_and_disables_storage(self):
        self.client.force_login(self.admin)

        response = self.client.get(self.url, {"cycle_id": self.cycle.id})
        post_response = self.client.post(self.url, {"cycle_id": self.cycle.id})

        self.assertEqual(response.status_code, 200, response.content.decode())
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(post_response.status_code, 405)

    def test_bulk_configuration_matches_authoritative_resolution_precedence(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "PARITY")
        offering.program = None
        offering.save(update_fields=["program"])

        def assert_parity(label):
            current = CourseOffering.objects.select_related("course", "section", "term").get(pk=offering.pk)
            template = FacultyGradingService.resolve_template_for_offering(current)
            period = FacultyGradingService.get_template_periods(template).filter(code="MIDTERM").first()
            expected = (
                AcademicPerformanceInsightService._grading_context(current, period)["signature"]
                + (str(MidtermExamPerformanceReportService._round(
                    FacultyGradingService.resolve_base_value(current, template),
                )),)
                if period else None
            )
            inputs = _BulkGradingConfiguration(
                [current], tenant_id=self.tenant.id, term_id=self.term.id, period_code="MIDTERM",
            )
            with self.subTest(label=label), self.assertNumQueries(0):
                actual_period, signature = inputs.resolve(current)
                self.assertEqual(actual_period, period)
                self.assertEqual(signature, expected)
            return signature

        assert_parity("latest published fallback, course base, tenant/system threshold")
        SystemSettingService.set("PASSING_GRADE_THRESHOLD", "72.50", tenant_id=self.tenant.id)
        self.course.default_base_value = None
        self.course.save(update_fields=["default_base_value"])
        assert_parity("template base, tenant threshold")
        self.template.passing_grade_threshold = Decimal("74.25")
        self.template.save(update_fields=["passing_grade_threshold"])
        assert_parity("template threshold")

        parent = Department.objects.create(
            tenant=self.tenant, campus=self.fairview[0], code="PARITY-PARENT", name="Parent",
        )
        department = self.fairview[1]
        department.parent = parent
        department.save(update_fields=["parent"])
        profile = TenantGradingProfile.objects.create(
            tenant=self.tenant, department=parent, grading_template=self.template,
            profile_code="PARITY-PARENT", profile_name="Parent profile",
            default_base_value=Decimal("43.00"), passing_grade_threshold=Decimal("76.00"),
        )
        self.assertEqual(assert_parity("ancestor profile")[1], profile.id)
        scoped_profile = TenantGradingProfile.objects.create(
            tenant=self.tenant, campus=self.fairview[0], department=department,
            program=self.fairview[2], grading_template=self.template,
            profile_code="PARITY-SCOPED", profile_name="Section program profile",
            default_base_value=Decimal("44.00"), passing_grade_threshold=Decimal("77.00"),
            period_grade_formula_mode=TenantGradingProfile.PeriodGradeFormulaMode.DEPED_TRANSMUTATION,
            period_grade_formula_json={"transmutation_table": [
                {"min": "0", "max": "59.99", "grade": "60"},
                {"min": "60", "max": "100", "grade": "90"},
            ]},
        )
        self.assertEqual(assert_parity("null program fallback and transmutation")[1], scoped_profile.id)
        course_profile = TenantGradingProfile.objects.create(
            tenant=self.tenant, course=self.course, grading_template=self.template,
            profile_code="PARITY-COURSE", profile_name="Course profile", priority=999,
        )
        self.assertEqual(assert_parity("course specificity precedes priority")[1], course_profile.id)
        self.course.course_type = "LAB"
        self.course.save(update_fields=["course_type"])
        course_profile.course_type = "lab"
        course_profile.save(update_fields=["course_type"])
        assert_parity("case insensitive course type")
        course_profile.term_type = "SPECIAL"
        course_profile.save(update_fields=["term_type"])
        self.assertEqual(assert_parity("nonmatching term type excluded")[1], scoped_profile.id)

        other_template = GradingTemplate.objects.create(
            tenant=self.tenant, code="PARITY-T", name="Assigned template",
            is_active=True, is_published=True, default_base_value=Decimal("55"),
        )
        other_period = GradingTemplatePeriod.objects.create(
            template=other_template, code="MIDTERM", name="Midterm", sequence_no=1,
        )
        CourseTemplateAssignment.objects.create(course=self.course, grading_template=other_template)
        self.assertEqual(assert_parity("default course assignment before profile")[0], other_template.id)
        exact_assignment = CourseTemplateAssignment.objects.create(
            course=self.course, grading_template=self.template, effective_from_term=self.term,
        )
        self.assertEqual(assert_parity("exact term assignment before default")[0], self.template.id)
        default_override = CourseBaseValueOverride.objects.create(course=self.course, base_value=Decimal("0"))
        self.assertEqual(assert_parity("zero override before profile")[6], "0.00")
        exact_override = CourseBaseValueOverride.objects.create(
            course=self.course, base_value=Decimal("61"), effective_from_term=self.term,
        )
        self.assertEqual(assert_parity("exact term override before default")[6], "61.00")
        exact_override.is_active = False
        exact_override.save(update_fields=["is_active"])
        self.assertEqual(assert_parity("inactive override ignored")[6], "0.00")
        exact_assignment.is_active = False
        exact_assignment.save(update_fields=["is_active"])
        self.assertEqual(assert_parity("inactive assignment ignored")[0], other_template.id)
        other_period.is_active = False
        other_period.save(update_fields=["is_active"])
        self.assertIsNone(assert_parity("missing active Midterm"))

        # A new request must observe changes; no input or result survives it.
        other_period.is_active = True
        other_period.save(update_fields=["is_active"])
        default_override.is_active = False
        default_override.save(update_fields=["is_active"])
        TenantGradingProfile.objects.filter(tenant=self.tenant).update(is_active=False)
        self.assertEqual(assert_parity("fresh inputs after profile and override changes")[6], "55.00")
        other_template.is_published = False
        other_template.save(update_fields=["is_published"])
        self.assertEqual(assert_parity("unpublished assignment ignored")[0], self.template.id)

    def test_bulk_configuration_without_published_template(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, "NO-TEMPLATE")
        self.template.is_published = False
        self.template.save(update_fields=["is_published"])
        inputs = _BulkGradingConfiguration(
            [offering], tenant_id=self.tenant.id, term_id=self.term.id, period_code="MIDTERM",
        )
        with self.assertNumQueries(0):
            self.assertEqual(inputs.resolve(offering), (None, None))

    def test_get_path_issues_no_insert_update_or_delete(self):
        request = RequestFactory().get(self.url, {"cycle_id": self.cycle.id})
        request.user = self.admin
        request.scope = {
            "tenant_ids": [self.tenant.id],
            "campus_ids": [self.fairview[0].id, self.cubao[0].id],
            "department_ids": [self.fairview[1].id],
            "tenant_id": self.tenant.id,
            "campus_id": self.fairview[0].id,
        }

        with CaptureQueriesContext(connection) as queries:
            response = midterm_exam_performance_view(request)

        self.assertEqual(response.status_code, 200)
        mutating = [
            query["sql"]
            for query in queries.captured_queries
            if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(mutating, [])

    def _get_rows(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url, {"cycle_id": self.cycle.id})
        self.assertEqual(response.status_code, 200, response.content.decode())
        return response.context["rows"]

    @staticmethod
    def _row(rows, section_code):
        return next(row for row in rows if row["section_code"] == section_code)

    def _campus_scope(self, code, name):
        campus = Campus.objects.create(tenant=self.tenant, code=code, name=name)
        department = Department.objects.create(
            tenant=self.tenant,
            campus=campus,
            code=f"{code}-IS",
            name=f"{name} Information Systems",
        )
        program = Program.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            code=f"{code}-BSIS",
            name=f"{name} BSIS",
        )
        return campus, department, program

    def _user(self, username, campus, department, *, first_name="Test", last_name="User"):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            first_name=first_name,
            last_name=last_name,
            default_tenant=self.tenant,
            default_campus=campus,
            default_department=department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def _faculty(self, username, scope):
        return self._user(username, scope[0], scope[1], first_name=username, last_name="Faculty")

    def _cycle_course(self, course, classification):
        return CycleCourse.objects.create(
            cycle=self.cycle,
            course=course,
            responsible_department=self.fairview[1],
            exam_classification=classification,
        )

    @staticmethod
    def _configuration(cycle_course):
        return CourseExamConfiguration.objects.create(cycle_course=cycle_course)

    def _offering(self, cycle_course, scope, faculty, section_code):
        campus, department, program = scope
        section = Section.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            code=section_code,
            name=section_code,
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=self.academic_year,
            term=self.term,
            course=cycle_course.course,
            section=section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=campus,
            offering=offering,
            faculty_user=faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )
        CycleCourseOffering.objects.create(
            cycle_course=cycle_course,
            offering=offering,
            campus=campus,
        )
        return offering

    def _student(self, offering, student_no):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            department=offering.department,
            program=offering.program,
            student_no=student_no,
            first_name="Report",
            last_name=student_no,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=offering,
        )
        return student

    def _submission(self, offering, status, *, period=None):
        return GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            offering=offering,
            template_period=period or self.period,
            status=status,
            submitted_by_user=self.faculty_fv,
            submitted_at=timezone.now() if status == GradeSubmission.Status.SUBMITTED else None,
        )

    def _period_grade(self, offering, student, exam_grade, *, finalized, period=None):
        return StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            offering=offering,
            template_period=period or self.period,
            student=student,
            exam_grade=exam_grade,
            is_finalized=finalized,
        )

    def _submitted_results(self, offering, student_values, *, period=None):
        self._submission(offering, GradeSubmission.Status.SUBMITTED, period=period)
        for student, value in student_values:
            self._period_grade(offering, student, value, finalized=True, period=period)
