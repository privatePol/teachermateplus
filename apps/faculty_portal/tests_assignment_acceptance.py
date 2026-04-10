from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.academics.models import (
    AcademicYear,
    ActiveGradingPeriodSetting,
    Course,
    CourseOffering,
    FacultyAssignment,
    Section,
    TenantTermGradingPeriod,
    Term,
)
from apps.academics.services import FacultyAssignmentWorkflowService
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    FacultyFinalClearanceReport,
    GradeActivity,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentFinalGrade,
    StudentActivityScore,
    StudentPeriodGrade,
)
from apps.notifications.models import FacultyMemo, FacultyReminder
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.grading.services import FacultyGradingService
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant


class FacultyAssignmentAcceptanceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
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
            code="A132-ITAPPS",
            title="IT Application Tools",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1A",
            name="BSIT 1A",
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

        self.faculty_user = User.objects.create_user(
            username="faculty_accept",
            email="faculty_accept@example.com",
            password="testpass123",
            first_name="Faculty",
            last_name="Member",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        faculty_access = Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
        )
        dashboard_read = Permission.objects.create(
            code="dashboard.read",
            module="dashboard",
            action="read",
        )
        analytics_read, _ = Permission.objects.get_or_create(
            code="faculty_analytics.read",
            defaults={
                "module": "faculty_analytics",
                "action": "read",
            },
        )
        RolePermission.objects.create(role=faculty_role, permission=faculty_access)
        RolePermission.objects.create(role=faculty_role, permission=dashboard_read)
        RolePermission.objects.create(role=faculty_role, permission=analytics_read)
        UserRole.objects.create(
            user=self.faculty_user,
            role=faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

        self.assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            is_primary=True,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="COLLEGE_TEMPLATE",
            name="College Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
            default_base_value=50,
        )
        self.prelim = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        self.midterm = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="GENED_MIDTERM",
            name="Midterm",
            sequence_no=2,
        )
        self.prefinal = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="GENED_PREFINAL",
            name="Pre-Final",
            sequence_no=3,
        )
        self.final = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="GENED_FINAL",
            name="Final",
            sequence_no=4,
        )
        class_standing = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="CS",
            name="Class Standing",
            weight_percentage=60,
            sort_order=1,
        )
        GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="EXAM",
            name="Prelim Exam",
            weight_percentage=40,
            sort_order=2,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
        )
        FacultyAssignmentWorkflowService.reset_response_window(self.assignment)
        self.assignment.save(
            update_fields=[
                "assignment_note",
                "accepted_at",
                "accepted_by",
                "response_status",
                "faculty_response_note",
                "responded_at",
                "response_due_at",
                "last_reminded_at",
                "reminder_count",
                "updated_at",
            ]
        )

    def test_faculty_must_accept_assignment_before_opening_course(self):
        self.client.force_login(self.faculty_user)

        response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )

        self.assertRedirects(response, reverse("faculty_portal:my_courses"))

        accept_response = self.client.post(
            reverse(
                "faculty_portal:faculty_assignment_accept",
                kwargs={"assignment_id": self.assignment.id},
            )
        )
        self.assertRedirects(accept_response, reverse("faculty_portal:my_courses"))

        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.accepted_at)
        self.assertEqual(self.assignment.accepted_by_id, self.faculty_user.id)

    def test_my_courses_lists_pending_assignments_before_acceptance(self):
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Faculty Assignments")
        self.assertContains(response, "Accept Assignment")
        self.assertContains(response, "College Template")

    def test_faculty_can_request_clarification_with_note(self):
        self.client.force_login(self.faculty_user)

        response = self.client.post(
            reverse(
                "faculty_portal:faculty_assignment_response",
                kwargs={"assignment_id": self.assignment.id},
            ),
            {
                "response_action": "clarification",
                "faculty_response_note": "Please confirm the schedule overlap before I accept this load.",
            },
        )

        self.assertRedirects(response, reverse("faculty_portal:my_courses"))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.CLARIFICATION_REQUESTED)
        self.assertEqual(
            self.assignment.faculty_response_note,
            "Please confirm the schedule overlap before I accept this load.",
        )

    def test_expired_assignment_cannot_be_accepted_until_admin_refreshes_window(self):
        self.assignment.response_status = FacultyAssignment.ResponseStatus.EXPIRED
        self.assignment.response_due_at = None
        self.assignment.responded_at = timezone.now()
        self.assignment.save(update_fields=["response_status", "response_due_at", "responded_at", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:faculty_assignment_accept",
                kwargs={"assignment_id": self.assignment.id},
            )
        )

        self.assertRedirects(response, reverse("faculty_portal:my_courses"))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.EXPIRED)

    def test_faculty_can_open_read_only_grading_template_view_after_acceptance(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_grading_template", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grading Template")
        self.assertContains(response, "College Template")
        self.assertContains(response, "PRELIM GRADE = Class Standing (60.00%) + Prelim Exam (40.00%)")

    def test_faculty_can_open_at_risk_monitor_when_prediction_is_enabled(self):
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="FEATURE_GRADE_PREDICTION_ENABLED",
            defaults={"setting_value": "1", "value_type": SystemSetting.ValueType.BOOL, "is_active": True},
        )
        SystemSetting.objects.update_or_create(
            tenant=self.tenant,
            setting_key="GRADE_PREDICTION_ENABLED",
            defaults={"setting_value": "1", "value_type": SystemSetting.ValueType.BOOL, "is_active": True},
        )

        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:student_at_risk_monitor"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student At-Risk Monitor")

    def test_prediction_guide_includes_methodology_and_column_factors(self):
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="FEATURE_GRADE_PREDICTION_ENABLED",
            defaults={"setting_value": "1", "value_type": SystemSetting.ValueType.BOOL, "is_active": True},
        )
        SystemSetting.objects.update_or_create(
            tenant=self.tenant,
            setting_key="GRADE_PREDICTION_ENABLED",
            defaults={"setting_value": "1", "value_type": SystemSetting.ValueType.BOOL, "is_active": True},
        )

        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_prediction_guide",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Prediction Methodology")
        self.assertContains(response, "Main Factors Used by the Prediction Engine")
        self.assertContains(response, "Computed From / Factors")
        self.assertContains(response, "How EduGradesPro Transmutes Scores with Base 50")
        self.assertContains(response, "How Periodic Grades Are Computed")
        self.assertContains(response, "How Final Grades Are Computed")
        self.assertContains(response, "Computed Score = ((Raw Score / Total Score)")
        self.assertContains(response, "Assigned grading template")
        self.assertContains(response, "Current Projection")

    def test_faculty_can_create_and_view_private_memo(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse("faculty_portal:memo_center"),
            {
                "memo_type": FacultyMemo.MemoType.CLASS,
                "offering": str(self.offering.id),
                "student": "",
                "title": "Follow up on Quiz 1",
                "body": "Check the class standing scores before Friday.",
                "is_pinned": "on",
            },
        )

        self.assertRedirects(response, reverse("faculty_portal:memo_center"))
        self.assertTrue(
            FacultyMemo.objects.filter(
                tenant=self.tenant,
                faculty_user=self.faculty_user,
                title="Follow up on Quiz 1",
                body="Check the class standing scores before Friday.",
                is_pinned=True,
                is_active=True,
            ).exists()
        )

        center_response = self.client.get(reverse("faculty_portal:memo_center"))
        self.assertEqual(center_response.status_code, 200)
        self.assertContains(center_response, "Faculty Notes / Private Memo")
        self.assertContains(center_response, "Follow up on Quiz 1")

    def test_dashboard_deadline_banner_explains_scope_mismatch(self):
        other_campus = Campus.objects.create(
            tenant=self.tenant,
            code="NCBA-CUBAO",
            name="Cubao",
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timezone.timedelta(days=3),
            is_locked=False,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No matching deadline for your active classes yet")
        self.assertContains(response, "NCBA-FAIRVIEW / 2025-2026 / 1ST")
        self.assertContains(response, "NCBA-CUBAO / 2025-2026 / 1ST")

    def test_dashboard_deadline_banner_explains_period_code_mismatch(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="2526_1STSEM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timezone.timedelta(days=3),
            is_locked=False,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No matching period deadline for your active classes yet")
        self.assertContains(response, "PRELIM")
        self.assertContains(response, "2526_1STSEM")

    def test_dashboard_surfaces_incomplete_student_kpi(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-INC-001",
            last_name="Incomplete",
            first_name="Learner",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.INC,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Incomplete Students")
        self.assertContains(response, "Students currently marked as")
        self.assertContains(response, "INC")

    def test_period_summary_hides_official_period_and_final_grades_before_deadline(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-HIDE-001",
            last_name="Hidden",
            first_name="Grade",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        class_standing_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        quiz = FacultyGradingService.create_activity(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing_component,
            template_subcomponent=None,
            template_detail=None,
            title="Quiz 1",
            total_score=Decimal("20"),
            activity_date=date(2025, 6, 10),
        )
        exam = FacultyGradingService.create_activity(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            template_subcomponent=None,
            template_detail=None,
            title="Prelim Exam",
            total_score=Decimal("50"),
            activity_date=date(2025, 6, 12),
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=quiz,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("18"), "remarks": ""}],
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=exam,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("40"), "remarks": ""}],
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_DEADLINE_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_OFFICIAL_FINAL_GRADES_AFTER_DEADLINE_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timedelta(days=1),
            is_locked=False,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.final.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timedelta(days=10),
            is_locked=False,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Official Prelim grade is hidden until the Prelim deadline has passed.")
        self.assertNotContains(response, "Official final grade is hidden until the Final deadline has passed.")
        self.assertNotContains(response, "FINAL GRADE")
        self.assertNotContains(response, "<th rowspan=\"4\" class=\"metric-col metric-final\">PRELIM Grade</th>", html=False)

    def test_period_summary_shows_official_period_grade_by_default_without_release_restriction(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-DEFAULT-001",
            last_name="Default",
            first_name="Visible",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        class_standing_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        quiz = FacultyGradingService.create_activity(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing_component,
            template_subcomponent=None,
            template_detail=None,
            title="Quiz Visible",
            total_score=Decimal("20"),
            activity_date=date(2025, 6, 10),
        )
        exam = FacultyGradingService.create_activity(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            template_subcomponent=None,
            template_detail=None,
            title="Exam Visible",
            total_score=Decimal("50"),
            activity_date=date(2025, 6, 12),
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=quiz,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("18"), "remarks": ""}],
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=exam,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("40"), "remarks": ""}],
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRELIM Grade", count=1)
        self.assertNotContains(response, "Official computed grades are currently hidden by admin configuration.")

    def test_period_summary_shows_official_period_grade_after_deadline_but_not_final_grade_before_final_period(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-SHOW-001",
            last_name="Visible",
            first_name="Grade",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        class_standing_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        quiz = FacultyGradingService.create_activity(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing_component,
            template_subcomponent=None,
            template_detail=None,
            title="Quiz 1",
            total_score=Decimal("20"),
            activity_date=date(2025, 6, 10),
        )
        exam = FacultyGradingService.create_activity(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            template_subcomponent=None,
            template_detail=None,
            title="Prelim Exam",
            total_score=Decimal("50"),
            activity_date=date(2025, 6, 12),
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=quiz,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("18"), "remarks": ""}],
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=exam,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("40"), "remarks": ""}],
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_DEADLINE_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_OFFICIAL_FINAL_GRADES_AFTER_DEADLINE_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timedelta(days=1),
            is_locked=False,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.final.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timedelta(days=1),
            is_locked=False,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRELIM Grade", count=1)
        self.assertContains(response, "Passed")
        self.assertContains(response, "Failed")
        self.assertContains(response, "93.00")
        self.assertNotContains(response, "FINAL GRADE")

    def test_final_period_summary_shows_prior_period_grade_columns_and_final_grade(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-FINAL-001",
            last_name="Final",
            first_name="Column",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        GradingTemplateComponent.objects.create(
            template_period=self.final,
            code="CS",
            name="Class Standing",
            weight_percentage=60,
            sort_order=1,
        )
        GradingTemplateComponent.objects.create(
            template_period=self.final,
            code="EXAM",
            name="Final Exam",
            weight_percentage=40,
            sort_order=2,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            student=student,
            period_grade=Decimal("81.00"),
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.midterm,
            student=student,
            period_grade=Decimal("84.00"),
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prefinal,
            student=student,
            period_grade=Decimal("87.00"),
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.final,
            student=student,
            exam_grade=Decimal("91.00"),
            period_grade=Decimal("89.00"),
        )
        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            student=student,
            final_grade=Decimal("85.25"),
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.final.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRELIM GRADE")
        self.assertContains(response, "MIDTERM GRADE")
        self.assertContains(response, "PRE-FINAL GRADE")
        self.assertContains(response, "FINAL GRADE")
        self.assertContains(response, "81.00")
        self.assertContains(response, "84.00")
        self.assertContains(response, "87.00")
        self.assertContains(response, "63.00")

    def test_analytics_uses_template_threshold_when_profile_override_missing(self):
        student_one = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-AN-001",
            last_name="Alpha",
            first_name="Ana",
        )
        student_two = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-AN-002",
            last_name="Bravo",
            first_name="Ben",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student_one,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student_two,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        self.template.passing_grade_threshold = Decimal("80.00")
        self.template.save(update_fields=["passing_grade_threshold"])
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            student=student_one,
            period_grade=Decimal("78.00"),
            class_standing_grade=Decimal("78.00"),
            exam_grade=Decimal("78.00"),
            computed_by_user=self.faculty_user,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            student=student_two,
            period_grade=Decimal("82.00"),
            class_standing_grade=Decimal("82.00"),
            exam_grade=Decimal("82.00"),
            computed_by_user=self.faculty_user,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:analytics"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["failed_rows"], 1)
        self.assertEqual(response.context["summary"]["passed_rows"], 1)
        self.assertEqual(response.context["summary"]["pass_rate"], 50.0)
        self.assertEqual(response.context["class_rows"][0]["failed_rows"], 1)

    def test_offering_periods_highlights_active_grading_period(self):
        canonical_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=canonical_period,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current Active Grading Period")
        self.assertContains(response, "Active Period")

    def test_my_courses_shows_final_clearance_action(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse(
                "faculty_portal:period_final_clearance",
                kwargs={"offering_id": self.offering.id, "period_id": self.final.id},
            ),
        )

    def test_offering_periods_no_longer_shows_final_clearance_action(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            reverse(
                "faculty_portal:period_final_clearance",
                kwargs={"offering_id": self.offering.id, "period_id": self.final.id},
            ),
        )

    def test_offering_periods_close_non_active_periods_under_active_period_governance(self):
        canonical_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        for code, name, sequence in (
            ("MIDTERM", "Midterm", 2),
            ("PREFINAL", "Pre-Final", 3),
            ("FINAL", "Final", 4),
        ):
            TenantTermGradingPeriod.objects.create(
                tenant=self.tenant,
                term=self.term,
                code=code,
                name=name,
                sequence_no=sequence,
            )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=canonical_period,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Closed by Active Period Policy", count=3)
        self.assertContains(response, "This period is closed until Midterm becomes the active grading period.")
        self.assertContains(response, "This period is closed until Pre-Final becomes the active grading period.")
        self.assertContains(response, "This period is closed until Final becomes the active grading period.")

    def test_non_active_period_route_is_blocked_until_reopened(self):
        canonical_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=canonical_period,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.midterm.id},
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This period is closed until Midterm becomes the active grading period.")

    def test_non_active_period_attendance_is_accessible_but_read_only(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-ATT-001",
            last_name="Readonly",
            first_name="Attendance",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
        )
        session = AttendanceSession.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.midterm,
            session_date=self.term.start_date,
            title="Week 1",
            created_by_user=self.faculty_user,
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            session=session,
            student=student,
            status_code=AttendanceRecord.Status.PRESENT,
            recorded_by_user=self.faculty_user,
        )
        canonical_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=canonical_period,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_attendance",
                kwargs={"offering_id": self.offering.id, "period_id": self.midterm.id},
            ),
            {"session_id": session.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This period is currently read-only under active grading period governance.")
        self.assertContains(response, "Attendance Records")
        self.assertContains(response, "disabled")
        self.assertNotContains(response, "Save Attendance")

    def test_non_active_period_summary_is_accessible_but_read_only(self):
        canonical_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=canonical_period,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_summary",
                kwargs={"offering_id": self.offering.id, "period_id": self.midterm.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This period is currently read-only under active grading period governance.")
        self.assertContains(response, "Summary |")
        self.assertNotContains(response, "Finalize and Submit")

    def test_reopened_non_active_period_remains_accessible(self):
        canonical_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=canonical_period,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.midterm,
            status=GradeSubmission.Status.REOPENED,
            reopened_by_user=self.faculty_user,
            reopened_at=timezone.now(),
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.midterm.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Midterm | Activities")

    def test_faculty_final_clearance_page_is_available_from_final_period(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_final_clearance",
                kwargs={"offering_id": self.offering.id, "period_id": self.final.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Final Clearance")
        self.assertContains(response, self.faculty_user.full_name)

    def test_faculty_final_clearance_post_generates_pdf_report(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_final_clearance",
                kwargs={"offering_id": self.offering.id, "period_id": self.final.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(
            FacultyFinalClearanceReport.objects.filter(
                faculty_user=self.faculty_user,
                term=self.term,
                campus=self.campus,
            ).exists()
        )

    def test_period_submit_redirects_to_periods_overview_after_success(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-100",
            last_name="Submit",
            first_name="Ready",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.FACULTY,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=self.prelim.components.first(),
            title="Quiz 1",
            total_score=50,
            activity_date=self.term.start_date,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score="45.00",
            computed_score="95.00",
            encoded_by_user=self.faculty_user,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_submit",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            ),
            {"confirm_submit": "1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRELIM grades submitted successfully.")
        self.assertContains(response, "A132-ITAPPS | BSIT-1A | 1ST")

    def test_period_submit_blocks_when_active_students_still_have_blank_activity_records(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-101",
            last_name="Blank",
            first_name="Cell",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.FACULTY,
        )
        component = self.prelim.components.first()
        activity_one = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=component,
            title="Quiz 1",
            total_score=50,
            activity_date=self.term.start_date,
        )
        GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=component,
            title="Quiz 2",
            total_score=50,
            activity_date=self.term.start_date,
        )
        StudentActivityScore.objects.create(
            activity=activity_one,
            student=student,
            raw_score="45.00",
            computed_score="95.00",
            encoded_by_user=self.faculty_user,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_submit",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            ),
            {"confirm_submit": "1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Submission blocked: some ACTIVE students still have blank required grade or attendance records.",
        )
        self.assertContains(response, "With complete records:")

    def test_future_activity_creation_auto_creates_faculty_reminder(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        self.client.force_login(self.faculty_user)

        future_date = timezone.localdate() + timedelta(days=3)
        response = self.client.post(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            ),
            {
                "template_component": self.prelim.components.first().id,
                "template_subcomponent": "",
                "template_detail": "",
                "title": "Quiz Reminder",
                "total_score": "50",
                "activity_date": future_date.isoformat(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity created.")
        reminder = FacultyReminder.objects.get(title="Prepare Activity: Quiz Reminder")
        self.assertEqual(reminder.faculty_user_id, self.faculty_user.id)
        self.assertEqual(reminder.offering_id, self.offering.id)
        self.assertFalse(reminder.send_email)

    def test_future_activity_creation_uses_optional_email_queue_flag(self):
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_REMINDER_EMAIL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        self.client.force_login(self.faculty_user)

        future_date = timezone.localdate() + timedelta(days=5)
        self.client.post(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            ),
            {
                "template_component": self.prelim.components.first().id,
                "template_subcomponent": "",
                "template_detail": "",
                "title": "Quiz Email Reminder",
                "total_score": "50",
                "activity_date": future_date.isoformat(),
            },
            follow=True,
        )

        reminder = FacultyReminder.objects.get(title="Prepare Activity: Quiz Email Reminder")
        self.assertTrue(reminder.send_email)

    def test_period_view_history_is_available_even_when_period_is_closed(self):
        canonical_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=canonical_period,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        admin_viewer = User.objects.create_user(
            username="academicviewer",
            email="academicviewer@example.com",
            password="testpass123",
            first_name="Academic",
            last_name="Viewer",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        AuditLog.objects.create(
            actor_user=admin_viewer,
            portal=AuditLog.Portal.ADMIN,
            action="READ",
            entity_type="FacultyGradebookMonitor",
            entity_id=f"{self.faculty_user.id}:{self.offering.id}:{self.prelim.id}",
            tenant=self.tenant,
            campus=self.campus,
            metadata_json={
                "faculty_user_id": self.faculty_user.id,
                "offering_id": self.offering.id,
                "period_id": self.prelim.id,
                "masked_student_identity": True,
            },
        )

        self.client.force_login(self.faculty_user)
        periods_response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )
        self.assertEqual(periods_response.status_code, 200)
        self.assertContains(
            periods_response,
            reverse(
                "faculty_portal:period_view_history",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            ),
        )

        history_response = self.client.get(
            reverse(
                "faculty_portal:period_view_history",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, "Who Viewed This Grade Book")
        self.assertContains(history_response, "Academic Viewer")
        self.assertContains(history_response, "Masked")

    def test_period_activities_shows_quick_jump_links(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Jump:")
        self.assertContains(
            response,
            reverse("faculty_portal:period_attendance", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
        )
        self.assertContains(
            response,
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
        )
        self.assertContains(
            response,
            reverse("faculty_portal:period_view_history", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
        )

    def test_activity_scores_shows_quick_jump_links_and_unsaved_warning_copy(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=self.prelim.components.first(),
            title="Quiz Nav",
            total_score=50,
            activity_date=self.term.start_date,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:activity_scores",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id, "activity_id": activity.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Jump:")
        self.assertContains(response, "You have unsaved encoded scores. If you leave this page, the encoded data will be lost. Continue?")
        self.assertContains(
            response,
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
        )
        self.assertContains(
            response,
            reverse("faculty_portal:period_attendance", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
        )

    def test_period_summary_shows_quick_jump_links_to_activities_and_attendance(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_summary",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Jump:")
        self.assertContains(
            response,
            reverse("faculty_portal:period_activities", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
        )
        self.assertContains(
            response,
            reverse("faculty_portal:period_attendance", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
        )

    def test_period_summary_readiness_cards_show_status_labels(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        for student_no, status in [
            ("2025-301", Enrollment.Status.ACTIVE),
            ("2025-302", Enrollment.Status.DRP),
            ("2025-303", Enrollment.Status.W),
            ("2025-304", Enrollment.Status.INC),
        ]:
            student = Student.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                student_no=student_no,
                last_name=f"Last{student_no[-1]}",
                first_name=f"First{student_no[-1]}",
            )
            Enrollment.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                academic_year=self.academic_year,
                term=self.term,
                student=student,
                course_offering=self.offering,
                enrollment_status=status,
                encoded_by_user=self.faculty_user,
                encoded_via_portal=Enrollment.SourcePortal.FACULTY,
            )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_summary",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ACTIVE Students")
        self.assertContains(response, "DRP")
        self.assertContains(response, "W")
        self.assertContains(response, "INC")
        self.assertContains(response, "Passing threshold used for pass/fail interpretation")

    def test_faculty_can_remove_student_from_class_when_faculty_allowed_mode_is_enabled(self):
        SystemSettingService.set(
            "ENROLLMENT_OWNERSHIP_MODE",
            "FACULTY_ALLOWED",
            tenant_id=self.tenant.id,
            value_type="STRING",
            is_active=True,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-201",
            last_name="Moved",
            first_name="Student",
        )
        enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
            {"action": "remove_from_class", "enrollment_id": enrollment.id},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student removed from this class list")
        enrollment.refresh_from_db()
        self.assertFalse(enrollment.is_active)

    def test_class_list_page_shows_search_help_and_remove_confirmation_prompt(self):
        SystemSettingService.set(
            "ENROLLMENT_OWNERSHIP_MODE",
            "FACULTY_ALLOWED",
            tenant_id=self.tenant.id,
            value_type="STRING",
            is_active=True,
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-202",
            last_name="Warning",
            first_name="Status",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.W,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<th class=\"class-list-line-no\">No.</th>", html=True)
        self.assertContains(response, "Status Legend")
        self.assertContains(response, "Dropped from this class.")
        self.assertContains(response, "Incomplete class record.")
        self.assertContains(response, "Type student number or last name, first name")
        self.assertContains(response, "Type 'remove' to confirm removing this student from this class.")
        self.assertContains(response, "data-bs-title=\"Remove from Class\"")
        self.assertContains(response, "<option value=\"DRP\">DRP</option>", html=True)
        self.assertContains(response, "<option value=\"INC\">INC</option>", html=True)
        self.assertContains(response, "text-bg-warning text-dark")
