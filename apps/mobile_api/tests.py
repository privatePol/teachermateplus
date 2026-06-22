import json
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.attendance.models import AttendanceRecord
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    GradeActivity,
    GradeEncodingControl,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    StudentActivityScore,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class MobileApiFoundationTests(TestCase):
    password = "testpass123"

    def setUp(self):
        self.tenant = Tenant.objects.create(code="MOB", name="Mobile Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.other_campus = Campus.objects.create(tenant=self.tenant, code="EXT", name="Extension Campus")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="CS",
            name="Computer Studies",
        )
        self.other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            code="EXTCS",
            name="Extension Computer Studies",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSCS",
            name="BSCS",
        )
        self.other_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
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
        self.course = self._course("CS101", "Intro to Computing", self.campus, self.department)
        self.section = self._section("BSCS-1A", self.campus, self.department, self.program)
        self.offering = self._offering(self.course, self.section, self.campus, self.department, self.program)
        self.other_course = self._course("CS102", "Programming", self.campus, self.department)
        self.other_section = self._section("BSCS-1B", self.campus, self.department, self.program)
        self.other_offering = self._offering(
            self.other_course,
            self.other_section,
            self.campus,
            self.department,
            self.program,
        )
        self.out_of_scope_course = self._course("IT101", "IT Fundamentals", self.other_campus, self.other_department)
        self.out_of_scope_section = self._section(
            "BSIT-1A",
            self.other_campus,
            self.other_department,
            self.other_program,
        )
        self.out_of_scope_offering = self._offering(
            self.out_of_scope_course,
            self.out_of_scope_section,
            self.other_campus,
            self.other_department,
            self.other_program,
        )

        self.faculty = User.objects.create_user(
            username="mobile-faculty",
            email="mobile-faculty@example.com",
            password=self.password,
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.other_faculty = User.objects.create_user(
            username="other-mobile-faculty",
            email="other-mobile-faculty@example.com",
            password=self.password,
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        faculty_access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access"},
        )
        role = Role.objects.create(code="FACULTY-MOBILE", name="Faculty Mobile")
        RolePermission.objects.create(role=role, permission=faculty_access)
        UserRole.objects.create(user=self.faculty, role=role, tenant=self.tenant, campus=self.campus)
        UserRole.objects.create(user=self.other_faculty, role=role, tenant=self.tenant, campus=self.campus)

        self._assign(self.faculty, self.offering)
        self._assign(self.other_faculty, self.other_offering)
        self._assign(self.faculty, self.out_of_scope_offering)

        self.student = self._student("2026-0001", "One", "Student", self.campus, self.department, self.program)
        self.student_two = self._student("2026-0002", "Two", "Student", self.campus, self.department, self.program)
        self.other_student = self._student("2026-9001", "Other", "Student", self.campus, self.department, self.program)
        self._enroll(self.student, self.offering)
        self._enroll(self.student_two, self.offering)
        self._enroll(self.other_student, self.other_offering)

        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="MOBILE-TPL",
            name="Mobile Template",
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
        self.component_with_subcomponent = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="LAB",
            name="Laboratory",
            weight_percentage=Decimal("0.00"),
            sort_order=2,
            is_active=True,
        )
        GradingTemplateSubcomponent.objects.create(
            template_component=self.component_with_subcomponent,
            code="OUTPUT",
            name="Output",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )
        for course in [self.course, self.other_course, self.out_of_scope_course]:
            CourseTemplateAssignment.objects.create(
                course=course,
                grading_template=self.template,
                effective_from_term=self.term,
                is_active=True,
            )
        self.activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz 1",
            total_score=Decimal("20.00"),
            activity_date=date(2026, 1, 10),
            created_by_user=self.faculty,
            is_active=True,
        )
        self.other_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.other_offering,
            template_period=self.period,
            template_component=self.component,
            title="Other Quiz",
            total_score=Decimal("20.00"),
            activity_date=date(2026, 1, 10),
            created_by_user=self.other_faculty,
            is_active=True,
        )

    def _course(self, code, title, campus, department):
        return Course.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            code=code,
            title=title,
        )

    def _section(self, code, campus, department, program):
        return Section.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            code=code,
            name=code,
        )

    def _offering(self, course, section, campus, department, program):
        return CourseOffering.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=self.academic_year,
            term=self.term,
            course=course,
            section=section,
        )

    def _student(self, student_no, first_name, last_name, campus, department, program):
        return Student.objects.create(
            tenant=self.tenant,
            campus=campus,
            department=department,
            program=program,
            student_no=student_no,
            first_name=first_name,
            last_name=last_name,
        )

    def _enroll(self, student, offering):
        return Enrollment.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            academic_year=self.academic_year,
            term=self.term,
            course_offering=offering,
            student=student,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )

    def _assign(self, user, offering):
        return FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=offering.campus,
            offering=offering,
            faculty_user=user,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=user,
            accepted_at=timezone.now(),
        )

    def post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def test_faculty_login_and_current_user_endpoint_work(self):
        response = self.post_json(
            reverse("mobile_api:login"),
            {"username": self.faculty.username, "password": self.password},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        response = self.client.get(reverse("mobile_api:me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["user"]["username"], self.faculty.username)
        self.assertTrue(
            AuditLog.objects.filter(
                actor_user=self.faculty,
                portal=AuditLog.Portal.FACULTY,
                action="LOGIN_SUCCESS",
            ).exists()
        )

    def test_unauthenticated_requests_are_json_rejected(self):
        response = self.client.get(reverse("mobile_api:classes"))
        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "authentication_required")
        self.assertNotIn("Traceback", response.content.decode())

    def test_faculty_classes_are_assignment_and_campus_scoped(self):
        self.client.force_login(self.faculty)
        response = self.client.get(reverse("mobile_api:classes"))
        self.assertEqual(response.status_code, 200)
        offering_ids = {row["offering_id"] for row in response.json()["data"]["classes"]}
        self.assertIn(self.offering.id, offering_ids)
        self.assertNotIn(self.other_offering.id, offering_ids)
        self.assertNotIn(self.out_of_scope_offering.id, offering_ids)

        response = self.client.get(reverse("mobile_api:class_snapshot", args=[self.other_offering.id]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

        response = self.client.get(reverse("mobile_api:class_snapshot", args=[self.out_of_scope_offering.id]))
        self.assertEqual(response.status_code, 403)

    def test_student_consultation_and_grade_explanation_reject_unrelated_students(self):
        self.client.force_login(self.faculty)
        consultation_url = reverse(
            "mobile_api:consultation_summary",
            args=[self.offering.id, self.other_student.id],
        )
        response = self.client.get(consultation_url)
        self.assertEqual(response.status_code, 403)

        explanation_url = reverse(
            "mobile_api:grade_explanation",
            args=[self.offering.id, self.other_student.id],
        )
        response = self.client.get(explanation_url)
        self.assertEqual(response.status_code, 403)

    def test_grade_explanation_uses_server_side_tmp_logic(self):
        self.client.force_login(self.faculty)
        StudentActivityScore.objects.create(
            activity=self.activity,
            student=self.student,
            raw_score=Decimal("18.00"),
            computed_score=Decimal("95.00"),
            encoded_by_user=self.faculty,
            is_active=True,
        )
        response = self.client.get(
            reverse("mobile_api:grade_explanation", args=[self.offering.id, self.student.id]),
            {"period_id": self.period.id},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertIn("server_explanation", payload)
        self.assertIn("formula_text", payload["computed_result"])
        self.assertTrue(
            AuditLog.objects.filter(
                actor_user=self.faculty,
                entity_type="GradeExplanation",
                metadata_json__source="mobile_api",
            ).exists()
        )

    def test_attendance_save_validates_scope_membership_status_and_audits(self):
        self.client.force_login(self.faculty)
        url = reverse("mobile_api:attendance_save", args=[self.offering.id])
        response = self.post_json(
            url,
            {
                "period_id": self.period.id,
                "date": "2026-01-20",
                "records": [{"student_id": self.student.id, "status_code": "BAD"}],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

        response = self.post_json(
            url,
            {
                "period_id": self.period.id,
                "date": "2026-01-20",
                "records": [{"student_id": self.other_student.id, "status_code": AttendanceRecord.Status.PRESENT}],
            },
        )
        self.assertEqual(response.status_code, 403)

        response = self.post_json(
            url,
            {
                "period_id": self.period.id,
                "date": "2026-01-20",
                "records": [{"student_id": self.student.id, "status_code": AttendanceRecord.Status.LATE}],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertTrue(
            AuditLog.objects.filter(
                actor_user=self.faculty,
                entity_type="AttendanceRecord",
                metadata_json__source="mobile_api",
            ).exists()
        )

    def test_write_operations_respect_encoding_closed(self):
        self.client.force_login(self.faculty)
        GradeEncodingControl.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            campus=self.campus,
            course_offering=self.offering,
            status=GradeEncodingControl.Status.CLOSED,
            reason="Mobile lock test",
            is_active=True,
        )

        score_response = self.post_json(
            reverse("mobile_api:activity_scores_save", args=[self.activity.id]),
            {"scores": [{"student_id": self.student.id, "raw_score": "10"}]},
        )
        self.assertEqual(score_response.status_code, 423)
        self.assertEqual(score_response.json()["error"]["code"], "encoding_closed")

        attendance_response = self.post_json(
            reverse("mobile_api:attendance_save", args=[self.offering.id]),
            {
                "period_id": self.period.id,
                "records": [{"student_id": self.student.id, "status_code": AttendanceRecord.Status.PRESENT}],
            },
        )
        self.assertEqual(attendance_response.status_code, 423)

        activity_response = self.post_json(
            reverse("mobile_api:quick_activity_create", args=[self.offering.id]),
            {
                "period_id": self.period.id,
                "component_id": self.component.id,
                "activity_type": "Quiz",
                "total_points": "10",
                "date": "2026-01-22",
            },
        )
        self.assertEqual(activity_response.status_code, 423)

    def test_quick_activity_creation_validates_grading_setup(self):
        self.client.force_login(self.faculty)
        response = self.post_json(
            reverse("mobile_api:quick_activity_create", args=[self.offering.id]),
            {
                "period_id": self.period.id,
                "component_id": self.component_with_subcomponent.id,
                "activity_type": "Lab",
                "total_points": "10",
                "date": "2026-01-22",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_score_save_validates_range_preserves_blank_vs_zero_and_rejects_unrelated_ids(self):
        self.client.force_login(self.faculty)
        url = reverse("mobile_api:activity_scores_save", args=[self.activity.id])

        response = self.post_json(url, {"scores": [{"student_id": self.student.id, "raw_score": "-1"}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"], "Score cannot be below zero.")

        response = self.post_json(url, {"scores": [{"student_id": self.student.id, "raw_score": "21"}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"], "Score cannot exceed total points.")

        response = self.post_json(url, {"scores": [{"student_id": self.other_student.id, "raw_score": "10"}]})
        self.assertEqual(response.status_code, 403)

        response = self.post_json(url, {"scores": [{"student_id": self.student.id, "raw_score": "0"}]})
        self.assertEqual(response.status_code, 200)
        score = StudentActivityScore.objects.get(activity=self.activity, student=self.student, is_active=True)
        self.assertEqual(score.raw_score, Decimal("0.00"))

        response = self.post_json(url, {"scores": [{"student_id": self.student.id, "raw_score": ""}]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            StudentActivityScore.objects.filter(activity=self.activity, student=self.student, is_active=True).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                actor_user=self.faculty,
                entity_type="StudentActivityScore",
                metadata_json__source="mobile_api",
            ).exists()
        )

        other_activity_response = self.post_json(
            reverse("mobile_api:activity_scores_save", args=[self.other_activity.id]),
            {"scores": [{"student_id": self.student.id, "raw_score": "10"}]},
        )
        self.assertEqual(other_activity_response.status_code, 403)

    def test_submission_readiness_and_missing_scores_are_available_read_only(self):
        self.client.force_login(self.faculty)
        readiness_response = self.client.get(
            reverse("mobile_api:submission_readiness", args=[self.offering.id]),
            {"period_id": self.period.id},
        )
        self.assertEqual(readiness_response.status_code, 200)
        self.assertIn("readiness", readiness_response.json()["data"])

        missing_response = self.client.get(
            reverse("mobile_api:missing_scores", args=[self.offering.id]),
            {"period_id": self.period.id},
        )
        self.assertEqual(missing_response.status_code, 200)
        self.assertIn("missing_students", missing_response.json()["data"])
