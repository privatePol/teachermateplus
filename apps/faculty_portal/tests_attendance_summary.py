import re
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.enrollment.models import Enrollment
from apps.faculty_portal.views import _attendance_allowable_limit_for_course
from apps.grading.models import CourseTemplateAssignment, GradingTemplate, GradingTemplateComponent, GradingTemplatePeriod
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class FacultyAttendanceSummaryTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="TEN-FAS", name="Tenant Faculty Attendance Summary")
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
            units=Decimal("3"),
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSCS-1A",
            name="BSCS 1A",
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

        self.empty_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS102",
            title="Programming Basics",
            units=Decimal("4"),
        )
        self.empty_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSCS-1B",
            name="BSCS 1B",
        )
        self.empty_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.empty_course,
            section=self.empty_section,
        )

        self.unassigned_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="CS103",
            title="Data Structures",
            units=Decimal("5"),
        )
        self.unassigned_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSCS-1C",
            name="BSCS 1C",
        )
        self.unassigned_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.unassigned_course,
            section=self.unassigned_section,
        )

        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TPL-FAS",
            name="Attendance Summary Template",
            is_published=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
            is_active=True,
        )
        GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.empty_course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.unassigned_course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )

        self.faculty = User.objects.create_user(
            username="faculty-attendance-summary",
            email="faculty-attendance-summary@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        faculty_access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access"},
        )
        dashboard_read, _ = Permission.objects.get_or_create(
            code="dashboard.read",
            defaults={"module": "dashboard", "action": "read"},
        )
        role = Role.objects.create(code="FACULTY", name="Faculty")
        RolePermission.objects.create(role=role, permission=faculty_access)
        RolePermission.objects.create(role=role, permission=dashboard_read)
        UserRole.objects.create(user=self.faculty, role=role, tenant=self.tenant, campus=self.campus)

        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=self.faculty,
            accepted_at=timezone.now(),
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.empty_offering,
            faculty_user=self.faculty,
            is_primary=False,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_by=self.faculty,
            accepted_at=timezone.now(),
        )

        self.students = {
            "ok": Student.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                student_no="2025-20001",
                last_name="Able",
                first_name="Anna",
            ),
            "warning": Student.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                student_no="2025-20002",
                last_name="Baker",
                first_name="Ben",
            ),
            "flagged": Student.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                student_no="2025-20003",
                last_name="Diaz",
                first_name="Dana",
            ),
            "exceeded": Student.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                student_no="2025-20004",
                last_name="Cruz",
                first_name="Cara",
            ),
        }
        for student in self.students.values():
            Enrollment.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                academic_year=self.academic_year,
                term=self.term,
                student=student,
                course_offering=self.offering,
                enrollment_status=Enrollment.Status.ACTIVE,
                encoded_by_user=self.faculty,
                encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            )

        today = timezone.localdate()
        self.past_sessions = []
        for offset in range(10, 0, -1):
            self.past_sessions.append(
                AttendanceSession.objects.create(
                    tenant=self.tenant,
                    campus=self.campus,
                    offering=self.offering,
                    template_period=self.period,
                    session_date=today - timezone.timedelta(days=offset),
                    title=f"Session {offset}",
                    created_by_user=self.faculty,
                )
            )
        self.future_session = AttendanceSession.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            session_date=today + timezone.timedelta(days=2),
            title="Future Session",
            created_by_user=self.faculty,
        )

        for session in self.past_sessions[:2]:
            AttendanceRecord.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                session=session,
                student=self.students["warning"],
                status_code=AttendanceRecord.Status.PRESENT,
                recorded_by_user=self.faculty,
            )
        for session in self.past_sessions[2:]:
            AttendanceRecord.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                session=session,
                student=self.students["warning"],
                status_code=AttendanceRecord.Status.ABSENT,
                recorded_by_user=self.faculty,
            )

        for session in self.past_sessions:
            AttendanceRecord.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                session=session,
                student=self.students["exceeded"],
                status_code=AttendanceRecord.Status.ABSENT,
                recorded_by_user=self.faculty,
            )

        for session in self.past_sessions[:7]:
            AttendanceRecord.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                session=session,
                student=self.students["flagged"],
                status_code=AttendanceRecord.Status.PRESENT,
                recorded_by_user=self.faculty,
            )
        for session in self.past_sessions[7:]:
            AttendanceRecord.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                session=session,
                student=self.students["flagged"],
                status_code=AttendanceRecord.Status.ABSENT,
                recorded_by_user=self.faculty,
            )

        for session in self.past_sessions:
            AttendanceRecord.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                session=session,
                student=self.students["ok"],
                status_code=AttendanceRecord.Status.PRESENT,
                recorded_by_user=self.faculty,
            )

        AttendanceRecord.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            session=self.future_session,
            student=self.students["warning"],
            status_code=AttendanceRecord.Status.ABSENT,
            recorded_by_user=self.faculty,
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            session=self.future_session,
            student=self.students["exceeded"],
            status_code=AttendanceRecord.Status.ABSENT,
            recorded_by_user=self.faculty,
        )

        self.client.force_login(self.faculty)

    def test_attendance_page_shows_attendance_summary_button(self):
        response = self.client.get(
            reverse(
                "faculty_portal:period_attendance",
                kwargs={"offering_id": self.offering.id, "period_id": self.period.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        summary_url = reverse(
            "faculty_portal:attendance_summary",
            kwargs={"offering_id": self.offering.id, "period_id": self.period.id},
        )
        self.assertContains(response, "Attendance Summary")
        self.assertContains(response, summary_url)

    def test_attendance_summary_button_uses_solid_button_class(self):
        response = self.client.get(
            reverse(
                "faculty_portal:period_attendance",
                kwargs={"offering_id": self.offering.id, "period_id": self.period.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "btn btn-sm btn-success",
            html=False,
        )
        summary_url = reverse(
            "faculty_portal:attendance_summary",
            kwargs={"offering_id": self.offering.id, "period_id": self.period.id},
        )
        self.assertNotRegex(response.content.decode(), rf'href="{re.escape(summary_url)}"[^>]*btn-outline-success')

    def test_faculty_can_open_attendance_summary_for_assigned_class(self):
        response = self.client.get(
            reverse(
                "faculty_portal:attendance_summary",
                kwargs={"offering_id": self.offering.id, "period_id": self.period.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Attendance Summary")
        self.assertContains(response, "Coverage: Beginning of class to current date")
        self.assertContains(response, "Course Units")
        self.assertContains(response, "Maximum Allowable Absences")
        self.assertEqual(response.context["allowable_limit_display"], "10")

    def test_faculty_cannot_open_attendance_summary_for_unassigned_class(self):
        response = self.client.get(
            reverse(
                "faculty_portal:attendance_summary",
                kwargs={"offering_id": self.unassigned_offering.id, "period_id": self.period.id},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_attendance_allowable_limit_applies_policy_table(self):
        expected_limits = {
            Decimal("2"): Decimal("3"),
            Decimal("3"): Decimal("10"),
            Decimal("4"): Decimal("14"),
            Decimal("5"): Decimal("18"),
            Decimal("6"): Decimal("20"),
        }

        for units, limit in expected_limits.items():
            course = Course.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                code=f"CS{int(units)}00",
                title=f"Units {units}",
                units=units,
            )
            self.assertEqual(_attendance_allowable_limit_for_course(course), limit)

    def test_attendance_summary_sorts_by_status_and_ignores_future_dates(self):
        response = self.client.get(
            reverse(
                "faculty_portal:attendance_summary",
                kwargs={"offering_id": self.offering.id, "period_id": self.period.id},
            )
        )

        rows = response.context["summary_rows"]
        self.assertEqual([row["status_label"] for row in rows], ["Exceeded Limit", "Warning", "Warning", "OK"])
        self.assertEqual([row["student_no"] for row in rows], ["2025-20004", "2025-20002", "2025-20003", "2025-20001"])
        warning_row = next(row for row in rows if row["student_no"] == "2025-20002")
        flagged_row = next(row for row in rows if row["student_no"] == "2025-20003")
        exceeded_row = next(row for row in rows if row["student_no"] == "2025-20004")
        ok_row = next(row for row in rows if row["student_no"] == "2025-20001")
        self.assertEqual(warning_row["absent_count"], 8)
        self.assertEqual(warning_row["total_meetings"], 10)
        self.assertTrue(flagged_row["consecutive_absence_flagged"])
        self.assertEqual(flagged_row["consecutive_absence_count"], 3)
        self.assertEqual(flagged_row["absent_count"], 3)
        self.assertEqual(exceeded_row["absent_count"], 10)
        self.assertEqual(exceeded_row["total_meetings"], 10)
        self.assertEqual(ok_row["absent_count"], 0)
        self.assertEqual(ok_row["remaining_allowable_display"], "10")

    def test_students_with_no_absences_show_full_remaining_allowable_count(self):
        response = self.client.get(
            reverse(
                "faculty_portal:attendance_summary",
                kwargs={"offering_id": self.offering.id, "period_id": self.period.id},
            )
        )

        ok_row = next(row for row in response.context["summary_rows"] if row["student_no"] == "2025-20001")
        self.assertEqual(ok_row["remaining_allowable_display"], "10")
        self.assertEqual(ok_row["status_label"], "OK")

    def test_attendance_summary_empty_state_shows_when_no_records_exist(self):
        response = self.client.get(
            reverse(
                "faculty_portal:attendance_summary",
                kwargs={"offering_id": self.empty_offering.id, "period_id": self.period.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No attendance records have been encoded for this class yet.")
