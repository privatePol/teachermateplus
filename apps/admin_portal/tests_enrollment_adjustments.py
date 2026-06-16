from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.enrollment.models import Enrollment, EnrollmentAdjustmentLog
from apps.enrollment.services import EnrollmentAdjustmentService
from apps.grading.models import (
    GradeActivity,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


User = get_user_model()


class EnrollmentAdjustmentTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="adjust-admin",
            email="adjust-admin@example.com",
            password="testpass123",
        )
        for code in (
            "admin_portal.access",
            "enrollment_adjustment.view",
            "enrollment_adjustment.process",
        ):
            Permission.objects.get_or_create(
                code=code,
                defaults={"module": code.split(".")[0], "action": code.split(".")[-1]},
            )
        self.client.force_login(self.admin)
        self.tenant = Tenant.objects.create(code="TEN-ADJ", name="Tenant Adjustment")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="CS",
            name="Computer Studies",
        )
        self.admin.default_tenant = self.tenant
        self.admin.default_campus = self.campus
        self.admin.default_department = self.department
        self.admin.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.admin.privacy_consent_at = timezone.now()
        self.admin.save(
            update_fields=[
                "default_tenant",
                "default_campus",
                "default_department",
                "privacy_consent_version",
                "privacy_consent_at",
            ]
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSCS",
            name="BSCS",
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
            code="CS101",
            title="Intro to Computing",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSCS-1A",
            name="BSCS 1A",
        )
        self.destination_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSCS-1B",
            name="BSCS 1B",
        )
        self.source_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )
        self.destination_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.destination_section,
        )
        self.student = self._student("2025-10001", "One")
        self.student_two = self._student("2025-10002", "Two")
        self.student_three = self._student("2025-10003", "Three")
        self.enrollment = self._enrollment(self.student)
        self.enrollment_two = self._enrollment(self.student_two)
        self.enrollment_three = self._enrollment(self.student_three)
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="ADJ-TPL",
            name="Adjustment Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            weight_percentage=Decimal("25.00"),
            sequence_no=1,
        )
        self.component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        self.url = reverse("admin_portal:enrollment_adjustments")

    def _student(self, student_no, first_name):
        return Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no=student_no,
            last_name="Student",
            first_name=first_name,
        )

    def _enrollment(self, student):
        return Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.source_offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )

    def _post_data(self, students, **overrides):
        data = {
            "academic_year": self.academic_year.id,
            "term": self.term.id,
            "campus": self.campus.id,
            "source_offering": self.source_offering.id,
            "destination_offering": self.destination_offering.id,
            "selected_students": [str(student.id) for student in students],
            "reason": "Pinnacle approved section correction",
        }
        data.update(overrides)
        return data

    def _view_only_user(self):
        user = User.objects.create_user(username="viewer", email="viewer@example.com", password="testpass123")
        user.default_tenant = self.tenant
        user.default_campus = self.campus
        user.default_department = self.department
        user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        user.privacy_consent_at = timezone.now()
        user.save(
            update_fields=[
                "default_tenant",
                "default_campus",
                "default_department",
                "privacy_consent_version",
                "privacy_consent_at",
            ]
        )
        for code in ("admin_portal.access", "enrollment_adjustment.view"):
            permission = Permission.objects.get(code=code)
        role = Role.objects.create(code="ENROLLMENT_VIEW_ONLY", name="Enrollment View Only")
        for code in ("admin_portal.access", "enrollment_adjustment.view"):
            RolePermission.objects.create(role=role, permission=Permission.objects.get(code=code))
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            is_active=True,
        )
        return user

    def test_single_student_transfer_creates_destination_and_log(self):
        response = self.client.post(self.url, self._post_data([self.student], action="process"))

        self.assertEqual(response.status_code, 200)
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.is_active)
        self.assertTrue(
            Enrollment.objects.filter(
                course_offering=self.destination_offering,
                student=self.student,
                is_active=True,
            ).exists()
        )
        log = EnrollmentAdjustmentLog.objects.get(student=self.student)
        self.assertEqual(log.result, EnrollmentAdjustmentLog.Result.COMPLETED)
        self.assertIsNotNone(log.batch_reference)
        self.assertEqual(log.source_enrollment_id, self.enrollment.id)
        self.assertEqual(log.destination_enrollment_id, Enrollment.objects.get(course_offering=self.destination_offering, student=self.student).id)
        self.assertTrue(log.source_previous_is_active)
        self.assertEqual(log.source_previous_status, Enrollment.Status.ACTIVE)
        self.assertTrue(log.destination_is_active)
        self.assertEqual(log.destination_status, Enrollment.Status.ACTIVE)

    def test_multiple_student_transfer(self):
        self.client.post(self.url, self._post_data([self.student, self.student_two], action="process"))

        self.assertEqual(
            Enrollment.objects.filter(course_offering=self.destination_offering, is_active=True).count(),
            2,
        )
        self.assertEqual(EnrollmentAdjustmentLog.objects.count(), 2)

    def test_transfer_entire_class(self):
        data = self._post_data([], action="process", transfer_entire_class="on")
        self.client.post(self.url, data)

        self.assertEqual(
            Enrollment.objects.filter(course_offering=self.destination_offering, is_active=True).count(),
            3,
        )
        self.assertEqual(EnrollmentAdjustmentLog.objects.count(), 3)

    def test_duplicate_destination_enrollment_is_blocked(self):
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.student,
            course_offering=self.destination_offering,
            enrollment_status=Enrollment.Status.ACTIVE,
        )

        self.client.post(self.url, self._post_data([self.student], action="process"))

        self.enrollment.refresh_from_db()
        self.assertTrue(self.enrollment.is_active)
        log = EnrollmentAdjustmentLog.objects.get(student=self.student)
        self.assertEqual(log.result, EnrollmentAdjustmentLog.Result.BLOCKED)
        self.assertIn("destination offering", " ".join(log.impact_snapshot["reasons"]))

    def test_warning_transfer_requires_confirmation_and_then_completes_with_warning(self):
        GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.source_offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz 1",
            total_score=Decimal("20.00"),
        )

        self.client.post(self.url, self._post_data([self.student], action="process"))
        self.enrollment.refresh_from_db()
        self.assertTrue(self.enrollment.is_active)
        self.assertEqual(EnrollmentAdjustmentLog.objects.get().result, EnrollmentAdjustmentLog.Result.BLOCKED)

        EnrollmentAdjustmentLog.objects.all().delete()
        self.client.post(self.url, self._post_data([self.student], action="process", confirm_warning="on"))
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.is_active)
        self.assertEqual(
            EnrollmentAdjustmentLog.objects.get().result,
            EnrollmentAdjustmentLog.Result.COMPLETED_WITH_WARNING,
        )

    def test_blocked_transfer_for_period_lock_and_final_submission(self):
        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.source_offering,
            student=self.student,
            final_grade=Decimal("85.00"),
            is_submitted=True,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.source_offering,
            is_locked=True,
        )

        self.client.post(self.url, self._post_data([self.student], action="process", confirm_warning="on"))

        self.enrollment.refresh_from_db()
        self.assertTrue(self.enrollment.is_active)
        log = EnrollmentAdjustmentLog.objects.get()
        self.assertEqual(log.result, EnrollmentAdjustmentLog.Result.BLOCKED)
        self.assertIn("Final grade is already submitted.", log.impact_snapshot["reasons"])
        self.assertIn("A grading period is locked", " ".join(log.impact_snapshot["reasons"]))

    def test_campus_level_lock_blocks_transfer(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            course_offering=None,
            is_locked=True,
        )

        analysis = EnrollmentAdjustmentService.analyze(
            source_offering=self.source_offering,
            destination_offering=self.destination_offering,
            student_ids=[self.student.id],
        )

        row = analysis["rows"][0]
        self.assertEqual(row["classification"], EnrollmentAdjustmentService.CLASSIFICATION_BLOCKED)
        self.assertEqual(row["counts"]["period_locks_count"], 1)

    def test_unsubmitted_final_grade_is_warning_not_safe(self):
        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.source_offering,
            student=self.student,
            final_grade=Decimal("85.00"),
            is_submitted=False,
        )

        analysis = EnrollmentAdjustmentService.analyze(
            source_offering=self.source_offering,
            destination_offering=self.destination_offering,
            student_ids=[self.student.id],
        )

        row = analysis["rows"][0]
        self.assertEqual(row["classification"], EnrollmentAdjustmentService.CLASSIFICATION_WARNING)
        self.assertIn("final_grades_count", row["warning_flags"])

    def test_impact_analysis_counts_academic_records(self):
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.source_offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz 1",
            total_score=Decimal("20.00"),
        )
        StudentActivityScore.objects.create(activity=activity, student=self.student, raw_score=Decimal("0.00"))
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.source_offering,
            template_period=self.period,
            student=self.student,
            period_grade=Decimal("75.00"),
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.source_offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
        )
        session = AttendanceSession.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.source_offering,
            template_period=self.period,
            session_date=date(2026, 1, 15),
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            session=session,
            student=self.student,
            status_code=AttendanceRecord.Status.PRESENT,
        )

        analysis = EnrollmentAdjustmentService.analyze(
            source_offering=self.source_offering,
            destination_offering=self.destination_offering,
            student_ids=[self.student.id],
        )

        row = analysis["rows"][0]
        self.assertEqual(row["classification"], EnrollmentAdjustmentService.CLASSIFICATION_WARNING)
        self.assertEqual(row["counts"]["attendance_count"], 1)
        self.assertEqual(row["counts"]["activities_count"], 1)
        self.assertEqual(row["counts"]["scores_count"], 1)
        self.assertEqual(row["counts"]["submissions_count"], 1)
        self.assertEqual(row["counts"]["period_grades_count"], 1)

    def test_view_only_user_can_open_page_but_cannot_process(self):
        user = self._view_only_user()
        self.client.force_login(user)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        analyze_response = self.client.post(self.url, self._post_data([self.student], action="analyze"))
        self.assertContains(
            analyze_response,
            "You can view the impact analysis, but you do not have permission to process adjustments.",
        )

        process_response = self.client.post(self.url, self._post_data([self.student], action="process"))
        self.assertEqual(process_response.status_code, 403)

    def test_history_and_detail_display(self):
        EnrollmentAdjustmentLog.objects.create(
            student=self.student,
            source_offering=self.source_offering,
            destination_offering=self.destination_offering,
            reason="Pinnacle correction",
            processed_by=self.admin,
            processed_at=timezone.now(),
            result=EnrollmentAdjustmentLog.Result.COMPLETED,
            warning_flags=[],
            impact_snapshot={"counts": {"activities_count": 0}, "reasons": []},
        )

        response = self.client.get(self.url)
        self.assertContains(response, "Adjustment History")
        self.assertContains(response, self.student.student_no, status_code=200)
        detail_response = self.client.get(
            reverse("admin_portal:enrollment_adjustment_detail", args=[EnrollmentAdjustmentLog.objects.get().id])
        )
        self.assertContains(detail_response, "Enrollment Adjustment Details")
