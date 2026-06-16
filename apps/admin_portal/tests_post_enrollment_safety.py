from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.enrollment.models import Enrollment
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
from apps.grading.services import CourseOfferingSafetyService, EnrollmentSafetyService
from apps.rbac.models import Permission
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


User = get_user_model()


class PostEnrollmentSafetyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="safety-admin",
            email="safety-admin@example.com",
            password="testpass123",
        )
        for code in (
            "admin_portal.access",
            "offerings.update",
            "enrollment.update",
        ):
            Permission.objects.get_or_create(
                code=code,
                defaults={"module": code.split(".")[0], "action": code.split(".")[-1]},
            )
        self.faculty = User.objects.create_user(
            username="safety-faculty",
            email="safety-faculty@example.com",
            password="testpass123",
        )
        self.client.force_login(self.admin)

        self.tenant = Tenant.objects.create(code="TEN-SAFE", name="Tenant Safety")
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
        self.other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS102",
            title="Data Fundamentals",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSCS-1A",
            name="BSCS 1A",
        )
        self.other_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSCS-1B",
            name="BSCS 1B",
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
            room="101",
            schedule_text="MW 8:00-9:00",
        )
        self.other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.other_course,
            section=self.other_section,
            room="102",
            schedule_text="TTH 8:00-9:00",
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-10001",
            last_name="Student",
            first_name="One",
        )
        self.other_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-10002",
            last_name="Student",
            first_name="Two",
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="SAFE-TPL",
            name="Safety Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
            is_active=True,
        )
        self.component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )

    def _offering_post_data(self, **overrides):
        data = {
            "tenant": self.offering.tenant_id,
            "campus": self.offering.campus_id,
            "department": self.offering.department_id,
            "program": self.offering.program_id,
            "academic_year": self.offering.academic_year_id,
            "term": self.offering.term_id,
            "course": self.offering.course_id,
            "section": self.offering.section_id,
            "room": self.offering.room,
            "schedule_text": self.offering.schedule_text,
            "status": self.offering.status,
            "is_active": "True",
        }
        data.update(overrides)
        return data

    def _enrollment_post_data(self, enrollment, **overrides):
        data = {
            "course_offering": enrollment.course_offering_id,
            "student": enrollment.student_id,
            "enrollment_status": enrollment.enrollment_status,
            "is_active": "on" if enrollment.is_active else "",
        }
        data.update(overrides)
        return data

    def _create_enrollment(self):
        return Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            course_offering=self.offering,
            student=self.student,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )

    def _create_activity(self):
        return GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Q1",
            total_score=Decimal("20.00"),
            activity_date=date(2026, 1, 10),
            created_by_user=self.faculty,
        )

    def test_unused_offering_can_edit_identity_fields(self):
        response = self.client.post(
            reverse("admin_portal:offering_update", args=[self.offering.id]),
            self._offering_post_data(section=self.other_section.id),
        )

        self.assertEqual(response.status_code, 302)
        self.offering.refresh_from_db()
        self.assertEqual(self.offering.section_id, self.other_section.id)

    def test_in_use_offering_blocks_identity_change_and_shows_warning(self):
        self._create_enrollment()

        get_response = self.client.get(reverse("admin_portal:offering_update", args=[self.offering.id]))
        self.assertContains(get_response, "In use offering")
        self.assertContains(get_response, "Enrollments:")

        response = self.client.post(
            reverse("admin_portal:offering_update", args=[self.offering.id]),
            self._offering_post_data(course=self.other_course.id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, CourseOfferingSafetyService.CHANGE_BLOCK_MESSAGE)
        self.offering.refresh_from_db()
        self.assertEqual(self.offering.course_id, self.course.id)

    def test_in_use_offering_allows_room_and_schedule_edit(self):
        self._create_enrollment()

        response = self.client.post(
            reverse("admin_portal:offering_update", args=[self.offering.id]),
            self._offering_post_data(room="LAB 1", schedule_text="MW 9:00-10:00"),
        )

        self.assertEqual(response.status_code, 302)
        self.offering.refresh_from_db()
        self.assertEqual(self.offering.room, "LAB 1")
        self.assertEqual(self.offering.schedule_text, "MW 9:00-10:00")

    def test_offering_usage_summary_counts_dependencies(self):
        enrollment = self._create_enrollment()
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
        )
        activity = self._create_activity()
        StudentActivityScore.objects.create(activity=activity, student=self.student, raw_score=10, computed_score=75)
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            student=self.student,
            period_grade=Decimal("75.00"),
        )
        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            student=self.student,
            final_grade=Decimal("75.00"),
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.offering,
            is_locked=True,
        )
        session = AttendanceSession.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            session_date=date(2026, 1, 11),
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            session=session,
            student=enrollment.student,
            status_code=AttendanceRecord.Status.PRESENT,
        )

        summary = CourseOfferingSafetyService.get_usage_summary(self.offering)

        self.assertTrue(summary["is_in_use"])
        self.assertEqual(summary["enrollments_count"], 1)
        self.assertEqual(summary["faculty_assignments_count"], 1)
        self.assertEqual(summary["accepted_faculty_assignments_count"], 1)
        self.assertEqual(summary["activities_count"], 1)
        self.assertEqual(summary["scores_count"], 1)
        self.assertEqual(summary["period_grades_count"], 1)
        self.assertEqual(summary["final_grades_count"], 1)
        self.assertEqual(summary["submissions_count"], 1)
        self.assertEqual(summary["period_locks_count"], 1)
        self.assertEqual(summary["attendance_count"], 2)

    def test_unused_enrollment_can_move_to_another_offering(self):
        enrollment = self._create_enrollment()

        response = self.client.post(
            reverse("admin_portal:enrollment_update", args=[enrollment.id]),
            self._enrollment_post_data(enrollment, course_offering=self.other_offering.id),
        )

        self.assertEqual(response.status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.course_offering_id, self.other_offering.id)

    def test_in_use_enrollment_blocks_transfer_and_student_change(self):
        enrollment = self._create_enrollment()
        activity = self._create_activity()
        StudentActivityScore.objects.create(activity=activity, student=self.student, raw_score=10, computed_score=75)

        get_response = self.client.get(reverse("admin_portal:enrollment_update", args=[enrollment.id]))
        self.assertContains(get_response, "In use enrollment")
        self.assertContains(get_response, "Scores:")

        transfer_response = self.client.post(
            reverse("admin_portal:enrollment_update", args=[enrollment.id]),
            self._enrollment_post_data(enrollment, course_offering=self.other_offering.id),
        )
        self.assertEqual(transfer_response.status_code, 200)
        self.assertContains(transfer_response, EnrollmentSafetyService.TRANSFER_BLOCK_MESSAGE)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.course_offering_id, self.offering.id)

        student_response = self.client.post(
            reverse("admin_portal:enrollment_update", args=[enrollment.id]),
            self._enrollment_post_data(enrollment, student=self.other_student.id),
        )
        self.assertEqual(student_response.status_code, 200)
        self.assertContains(student_response, EnrollmentSafetyService.TRANSFER_BLOCK_MESSAGE)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.student_id, self.student.id)

    def test_in_use_enrollment_allows_status_change(self):
        enrollment = self._create_enrollment()
        activity = self._create_activity()
        StudentActivityScore.objects.create(activity=activity, student=self.student, raw_score=10, computed_score=75)

        response = self.client.post(
            reverse("admin_portal:enrollment_update", args=[enrollment.id]),
            self._enrollment_post_data(enrollment, enrollment_status=Enrollment.Status.DRP),
        )

        self.assertEqual(response.status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, Enrollment.Status.DRP)

    def test_enrollment_usage_summary_counts_dependencies(self):
        enrollment = self._create_enrollment()
        activity = self._create_activity()
        StudentActivityScore.objects.create(activity=activity, student=self.student, raw_score=10, computed_score=75)
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            student=self.student,
            period_grade=Decimal("75.00"),
        )
        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            student=self.student,
            final_grade=Decimal("75.00"),
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.offering,
            is_locked=True,
        )
        session = AttendanceSession.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            session_date=date(2026, 1, 11),
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            session=session,
            student=self.student,
            status_code=AttendanceRecord.Status.PRESENT,
        )

        summary = EnrollmentSafetyService.get_usage_summary(enrollment)

        self.assertTrue(summary["is_in_use"])
        self.assertEqual(summary["scores_count"], 1)
        self.assertEqual(summary["period_grades_count"], 1)
        self.assertEqual(summary["final_grades_count"], 1)
        self.assertEqual(summary["submissions_count"], 1)
        self.assertEqual(summary["period_locks_count"], 1)
        self.assertEqual(summary["attendance_count"], 1)
