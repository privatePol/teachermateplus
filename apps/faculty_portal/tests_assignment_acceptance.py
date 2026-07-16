from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from PIL import Image

from apps.accounts.models import User, UserSignatureUsageLog
from apps.accounts.services import UserSignatureService
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
from apps.academics.services import AcademicGovernanceService, FacultyAssignmentWorkflowService
from apps.auditlog.models import AuditLog
from apps.enrollment.models import ClassListChangeRequest, Enrollment
from apps.grading.models import (
    CourseTemplateAssignment,
    DetailComputationMode,
    FacultyFinalClearanceReport,
    GradeActivity,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    StudentFinalGrade,
    StudentActivityScore,
    StudentPeriodGrade,
    TemplateHotfixRequest,
    TenantGradingProfile,
)
from apps.grading.explanations import GradeExplanationService
from apps.notifications.models import FacultyMemo, FacultyReminder, SubmissionNonComplianceNotice
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.grading.services import (
    FacultyGradingService,
    GradingGovernanceService,
    GradingTemplateService,
    TemplateGovernanceWorkflowService,
)
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

    def _accept_assignment(self, assignment=None, faculty_user=None):
        assignment = assignment or self.assignment
        faculty_user = faculty_user or self.faculty_user
        assignment.accepted_at = timezone.now()
        assignment.accepted_by = faculty_user
        assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        return assignment

    def _enable_grade_prediction(self):
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

    def _enable_faculty_template_issue_reporting(self):
        faculty_role = Role.objects.get(code="FACULTY")
        permission, _ = Permission.objects.get_or_create(
            code="template_hotfixes.create",
            defaults={"module": "template_hotfixes", "action": "create"},
        )
        RolePermission.objects.get_or_create(role=faculty_role, permission=permission)
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST],
            ["FACULTY"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )

    def _create_second_term_offering(self):
        second_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=second_term,
            course=self.course,
            section=self.section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            faculty_user=self.faculty_user,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
        )
        return second_term, offering

    def test_report_template_issue_button_hidden_when_governance_disallows_faculty(self):
        self._accept_assignment()
        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_grading_template", kwargs={"offering_id": self.offering.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Report Template Issue")

    def test_faculty_can_report_template_issue_when_governance_allows(self):
        self._accept_assignment()
        self._enable_faculty_template_issue_reporting()
        self.client.force_login(self.faculty_user)

        template_response = self.client.get(
            reverse("faculty_portal:offering_grading_template", kwargs={"offering_id": self.offering.id})
        )
        self.assertEqual(template_response.status_code, 200)
        self.assertContains(template_response, "Report Template Issue")

        response = self.client.post(
            reverse("faculty_portal:report_template_issue", kwargs={"offering_id": self.offering.id}),
            {
                "issue_type": "WRONG_WEIGHT",
                "details": "Prelim exam should follow the approved component weight.",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Template issue report submitted")
        hotfix = TemplateHotfixRequest.objects.get(requested_by_user=self.faculty_user)
        self.assertEqual(hotfix.apply_mode, TemplateHotfixRequest.ApplyMode.REQUESTING_FACULTY_OFFERINGS)
        self.assertIn("Prelim exam should follow", hotfix.justification)
        self.assertContains(response, f"Report #{hotfix.id}")

    def test_average_activity_detail_weight_is_hidden_on_activity_page(self):
        self._accept_assignment()
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        participation = GradingTemplateSubcomponent.objects.create(
            template_component=class_standing,
            code="PARTICIPATION",
            name="Participation/Output",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
        )
        recitation = GradingTemplateDetail.objects.create(
            template_subcomponent=participation,
            code="RECITATION",
            name="Recitation",
            weight_percentage=Decimal("25.00"),
            sort_order=1,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            template_subcomponent=participation,
            template_detail=recitation,
            title="R1",
            total_score=Decimal("20.00"),
            created_by_user=self.faculty_user,
        )
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        exam_component.is_exam_component = True
        exam_component.save(update_fields=["is_exam_component", "updated_at"])
        GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            title="Exam 1",
            total_score=Decimal("100.00"),
            created_by_user=self.faculty_user,
        )
        self.client.force_login(self.faculty_user)

        template_response = self.client.get(
            reverse("faculty_portal:offering_grading_template", kwargs={"offering_id": self.offering.id})
        )
        self.assertEqual(template_response.status_code, 200)
        self.assertContains(template_response, "Recitation = 25.00%")
        self.assertContains(template_response, "configured weight; not used in the activity average")

        activities_response = self.client.get(
            reverse("faculty_portal:period_activities", args=[self.offering.id, self.prelim.id])
        )
        self.assertEqual(activities_response.status_code, 200)
        self.assertNotContains(activities_response, "Detail Weight")
        self.assertNotContains(activities_response, "Recitation (25.00% configured weight)")
        self.assertNotContains(activities_response, "Reference only")
        self.assertNotContains(activities_response, "<th>Entry Method</th>", html=True)
        self.assertContains(activities_response, "Grade Summary")
        self.assertContains(activities_response, "activity-icon-btn")
        self.assertContains(activities_response, 'title="Encode Scores"')
        self.assertContains(activities_response, 'aria-label="Encode scores for R1"')
        self.assertContains(activities_response, 'title="Edit"')
        self.assertContains(activities_response, 'aria-label="Edit R1"')
        self.assertContains(activities_response, 'title="Delete"')
        self.assertContains(activities_response, 'aria-label="Delete R1"')
        self.assertContains(activities_response, "activity-taxonomy-component-standing")
        self.assertContains(activities_response, "activity-taxonomy-subcomponent-standing")
        self.assertContains(activities_response, "activity-taxonomy-detail-standing")
        self.assertContains(activities_response, "activity-taxonomy-component-exam")
        self.assertContains(activities_response, "Class Standing")
        self.assertContains(activities_response, "Participation/Output")
        self.assertContains(activities_response, "Recitation")

        scores_response = self.client.get(
            reverse(
                "faculty_portal:activity_scores",
                args=[self.offering.id, self.prelim.id, activity.id],
            )
        )
        self.assertEqual(scores_response.status_code, 200)
        self.assertContains(scores_response, "Participation/Output")
        self.assertContains(scores_response, "Recitation")
        self.assertNotContains(scores_response, "Configured Detail Weight:")
        self.assertNotContains(scores_response, "25.00%")
        self.assertNotContains(scores_response, "reference only under Average Activities")

    def test_report_template_issue_hidden_for_reopened_gradebook(self):
        self._accept_assignment()
        self._enable_faculty_template_issue_reporting()
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.REOPENED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
            reopened_by_user=self.faculty_user,
            reopened_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_grading_template", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Report Template Issue")

    def test_analytics_loads_when_class_has_no_published_template(self):
        self._accept_assignment()
        CourseTemplateAssignment.objects.filter(course=self.course).delete()
        self.template.is_published = False
        self.template.save(update_fields=["is_published", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:analytics"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no published grading template assigned")
        self.assertContains(response, "No template")

    def test_analytics_missing_template_uses_tenant_passing_threshold(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-MISS-TPL-001",
            last_name="Missing",
            first_name="Template",
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            student=student,
            period_grade=Decimal("77.00"),
        )
        SystemSettingService.set(
            "PASSING_GRADE_THRESHOLD",
            "80",
            tenant_id=self.tenant.id,
            value_type="STRING",
            is_active=True,
        )
        CourseTemplateAssignment.objects.filter(course=self.course).delete()
        self.template.is_published = False
        self.template.save(update_fields=["is_published", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:analytics"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["summary"]["failed_rows"], 1)

    def _create_active_student(self, *, student_no="2025-RISK-001", last_name="Risk", first_name="Student"):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no=student_no,
            last_name=last_name,
            first_name=first_name,
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
        return student

    def _create_participation_output_readiness_period(self, *, detail_computation_mode):
        period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PO_READINESS",
            name="Participation Output Readiness",
            sequence_no=20,
            weight_percentage=Decimal("100.00"),
        )
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        participation_output = GradingTemplateSubcomponent.objects.create(
            template_component=component,
            code="PG_CA_PO",
            name="Participation/Output",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            detail_computation_mode=detail_computation_mode,
        )
        recitation = GradingTemplateDetail.objects.create(
            template_subcomponent=participation_output,
            code="RECITATION",
            name="Recitation",
            weight_percentage=Decimal("50.00"),
            sort_order=1,
        )
        assignment = GradingTemplateDetail.objects.create(
            template_subcomponent=participation_output,
            code="ASSIGNMENT",
            name="Assignment",
            weight_percentage=Decimal("50.00"),
            sort_order=2,
        )
        return period, component, participation_output, recitation, assignment

    def _create_participation_output_activity(
        self,
        *,
        period,
        component,
        participation_output,
        detail,
        title="Recitation 1",
    ):
        return GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            template_component=component,
            template_subcomponent=participation_output,
            template_detail=detail,
            title=title,
            total_score=Decimal("100.00"),
            activity_date=self.term.start_date,
        )

    def _create_period_activity_grouping_fixture(self):
        self._accept_assignment()
        students = [
            self._create_active_student(
                student_no="2025-GRP-001",
                last_name="Grouped",
                first_name="One",
            ),
            self._create_active_student(
                student_no="2025-GRP-002",
                last_name="Grouped",
                first_name="Two",
            ),
        ]
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        exam = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        exam.is_exam_component = True
        exam.save(update_fields=["is_exam_component", "updated_at"])
        quizzes = GradingTemplateSubcomponent.objects.create(
            template_component=class_standing,
            code="QUIZZES",
            name="Quizzes",
            weight_percentage=Decimal("50.00"),
            sort_order=1,
        )
        participation = GradingTemplateSubcomponent.objects.create(
            template_component=class_standing,
            code="PARTICIPATION",
            name="Participation/Output",
            weight_percentage=Decimal("50.00"),
            sort_order=2,
        )
        unused_subcomponent = GradingTemplateSubcomponent.objects.create(
            template_component=class_standing,
            code="UNUSED",
            name="Unused Subcomponent",
            weight_percentage=Decimal("0.00"),
            sort_order=3,
        )
        unused_detail = GradingTemplateDetail.objects.create(
            template_subcomponent=unused_subcomponent,
            code="UNUSED_DETAIL",
            name="Unused Detail",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        recitation = GradingTemplateDetail.objects.create(
            template_subcomponent=participation,
            code="RECITATION",
            name="Recitation",
            weight_percentage=Decimal("50.00"),
            sort_order=1,
        )
        assignment = GradingTemplateDetail.objects.create(
            template_subcomponent=participation,
            code="ASSIGNMENT",
            name="Assignment",
            weight_percentage=Decimal("50.00"),
            sort_order=2,
        )
        q2 = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            template_subcomponent=quizzes,
            title="Q2",
            total_score=Decimal("10.00"),
            activity_date=date(2025, 6, 2),
        )
        q1 = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            template_subcomponent=quizzes,
            title="Q1",
            total_score=Decimal("10.00"),
            activity_date=date(2025, 6, 1),
        )
        r1 = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            template_subcomponent=participation,
            template_detail=recitation,
            title="R1",
            total_score=Decimal("10.00"),
            activity_date=date(2025, 6, 3),
        )
        a1 = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            template_subcomponent=participation,
            template_detail=assignment,
            title="A1",
            total_score=Decimal("10.00"),
            activity_date=date(2025, 6, 4),
        )
        pex = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam,
            title="PEX",
            total_score=Decimal("100.00"),
            activity_date=date(2025, 6, 5),
        )
        StudentActivityScore.objects.create(activity=q1, student=students[0], raw_score=Decimal("0.00"))
        StudentActivityScore.objects.create(activity=q1, student=students[1], raw_score=Decimal("8.00"))
        StudentActivityScore.objects.create(activity=q2, student=students[0], raw_score=Decimal("7.00"))
        return {
            "quizzes": quizzes,
            "participation": participation,
            "recitation": recitation,
            "assignment": assignment,
            "unused_subcomponent": unused_subcomponent,
            "unused_detail": unused_detail,
            "activities": {"q1": q1, "q2": q2, "r1": r1, "a1": a1, "pex": pex},
        }

    def _complete_final_clearance_for_offering(self, *, offering=None, student=None):
        offering = offering or self.offering
        student = student or self._create_active_student(
            student_no="2025-CLEAR-001",
            last_name="Clearance",
            first_name="Complete",
        )
        periods = list(self.template.periods.filter(is_active=True).order_by("sequence_no", "id"))
        for period in periods:
            GradeSubmission.objects.update_or_create(
                tenant=offering.tenant,
                campus=offering.campus,
                offering=offering,
                template_period=period,
                defaults={
                    "status": GradeSubmission.Status.SUBMITTED,
                    "submitted_by_user": self.faculty_user,
                    "submitted_at": timezone.now(),
                },
            )
            StudentPeriodGrade.objects.update_or_create(
                tenant=offering.tenant,
                campus=offering.campus,
                offering=offering,
                template_period=period,
                student=student,
                defaults={"period_grade": Decimal("88.00")},
            )
        StudentFinalGrade.objects.update_or_create(
            tenant=offering.tenant,
            campus=offering.campus,
            offering=offering,
            student=student,
            defaults={"final_grade": Decimal("88.00")},
        )
        return student

    def _create_low_exam_score(self, *, student, offering=None, period=None, title="Prelim Exam 1"):
        offering = offering or self.offering
        period = period or self.prelim
        exam_component = GradingTemplateComponent.objects.get(template_period=period, code="EXAM")
        exam_component.is_exam_component = True
        exam_component.save(update_fields=["is_exam_component", "updated_at"])
        activity = GradeActivity.objects.create(
            tenant=offering.tenant,
            campus=offering.campus,
            offering=offering,
            template_period=period,
            template_component=exam_component,
            title=title,
            total_score=100,
            created_by_user=self.faculty_user,
        )
        computed_score = FacultyGradingService.compute_activity_score(
            raw_score=Decimal("20"),
            total_score=Decimal("100"),
            base_value=Decimal("50"),
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=20,
            computed_score=computed_score,
            encoded_by_user=self.faculty_user,
        )
        return activity

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
        self.assertNotContains(response, "Request Clarification")
        self.assertNotContains(response, "Decline")
        self.assertContains(response, "College Template")
        self.assertContains(response, "Campus: Fairview (NCBA-FAIRVIEW)")
        guide_url = reverse("faculty_portal:guide")
        self.assertContains(response, "my-courses-guide-tag")
        self.assertContains(response, f'href="{guide_url}#guide-assignments"', html=False)

    def test_my_courses_labels_accepted_assignments_with_campus_name(self):
        self._accept_assignment()
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accepted Course Assignments")
        self.assertContains(response, "These are your official accepted classes")
        self.assertContains(response, "NCBA / Fairview")
        self.assertContains(response, "(NCBA-FAIRVIEW)")
        guide_url = reverse("faculty_portal:guide")
        self.assertContains(response, "my-courses-guide-tag")
        self.assertContains(response, f'href="{guide_url}#guide-workflow"', html=False)

    def test_period_summary_shows_encoded_zero_scores_metric_not_my_courses(self):
        self._accept_assignment()
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-ZERO",
            last_name="Zero",
            first_name="Encoded",
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
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            title="Zero Score Check",
            total_score=Decimal("100.00"),
            created_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=Decimal("0.00"),
            computed_score=Decimal("50.00"),
            encoded_by_user=self.faculty_user,
        )
        self.client.force_login(self.faculty_user)

        courses_response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(courses_response.status_code, 200)
        self.assertNotContains(courses_response, "Encoded Zero Scores")

        summary_response = self.client.get(
            reverse(
                "faculty_portal:period_summary",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(summary_response.status_code, 200)
        self.assertContains(summary_response, "Encoded Zero Scores")
        self.assertContains(summary_response, "Saved raw scores of 0. Review these before submission")
        self.assertContains(summary_response, '<div class="metric">1</div>', html=False)

    def test_my_courses_shows_syllabus_icon_only_when_course_has_link(self):
        self._accept_assignment()
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertNotContains(response, reverse("faculty_portal:offering_syllabus", args=[self.offering.id]))

        self.course.syllabus_url = "https://drive.google.com/file/d/example/view"
        self.course.save(update_fields=["syllabus_url", "updated_at"])

        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertContains(response, reverse("faculty_portal:offering_syllabus", args=[self.offering.id]))
        self.assertContains(response, "Open syllabus")

    def test_syllabus_redirect_requires_assigned_faculty_and_matching_tenant(self):
        self._accept_assignment()
        self.course.syllabus_url = "https://drive.google.com/file/d/example/view"
        self.course.save(update_fields=["syllabus_url", "updated_at"])
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:offering_syllabus", args=[self.offering.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.course.syllabus_url)
        log = AuditLog.objects.filter(
            action="VIEW_SYLLABUS_LINK",
            portal="FACULTY",
            entity_type="Course",
            entity_id=str(self.course.id),
            actor_user=self.faculty_user,
            tenant=self.tenant,
            campus=self.campus,
        ).latest("created_at")
        self.assertEqual(log.metadata_json["offering_id"], self.offering.id)
        self.assertEqual(log.metadata_json["course_code"], self.course.code)
        self.assertNotIn("syllabus_url", log.metadata_json)

        other_faculty = User.objects.create_user(
            username="other_faculty",
            email="other_faculty@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=other_faculty,
            role=Role.objects.get(code="FACULTY"),
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(other_faculty)

        response = self.client.get(reverse("faculty_portal:offering_syllabus", args=[self.offering.id]))

        self.assertEqual(response.status_code, 404)

    def test_syllabus_redirect_blocks_course_tenant_mismatch(self):
        self._accept_assignment()
        other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        self.course.tenant = other_tenant
        self.course.syllabus_url = "https://drive.google.com/file/d/example/view"
        self.course.save(update_fields=["tenant", "syllabus_url", "updated_at"])
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:offering_syllabus", args=[self.offering.id]))

        self.assertEqual(response.status_code, 404)

    def test_my_courses_warns_faculty_when_course_has_no_template_assignment(self):
        missing_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="NO-TPL",
            title="Course With Missing Template Assignment",
        )
        missing_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=missing_course,
            section=self.section,
        )
        missing_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=missing_offering,
            faculty_user=self.faculty_user,
            is_primary=True,
        )
        self._accept_assignment(missing_assignment)
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Some assigned classes have no grading template yet.")
        self.assertContains(response, "Course With Missing Template Assignment")
        self.assertContains(response, "Not assigned yet")
        self.assertContains(response, "Please coordinate with the MIS Department.")

    def test_my_courses_moves_old_term_class_to_archived_when_active_scope_advances(self):
        self._accept_assignment()
        second_term, active_offering = self._create_second_term_offering()
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=self.academic_year,
            term=second_term,
        )
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_count"], 1)
        self.assertEqual(response.context["archived_count"], 1)
        active_ids = [offering.id for group in response.context["grouped_offerings"] for offering in group["offerings"]]
        self.assertIn(active_offering.id, active_ids)
        self.assertNotIn(self.offering.id, active_ids)

    def test_faculty_topbar_displays_current_academic_scope(self):
        second_term, _active_offering = self._create_second_term_offering()
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=self.academic_year,
            term=second_term,
        )
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Academic Scope:")
        self.assertContains(response, "2025-2026 / 2ND")

    def test_outside_active_scope_class_blocks_direct_activity_creation(self):
        self._accept_assignment()
        second_term, _active_offering = self._create_second_term_offering()
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=self.academic_year,
            term=second_term,
        )
        component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        self.client.force_login(self.faculty_user)

        response = self.client.post(
            reverse("faculty_portal:period_activities", args=[self.offering.id, self.prelim.id]),
            {
                "template_component": component.id,
                "title": "Old Term Activity",
                "total_score": "100",
                "activity_date": "2026-01-15",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            GradeActivity.objects.filter(offering=self.offering, title="Old Term Activity", is_active=True).exists()
        )

    def test_faculty_reminder_dropdown_uses_current_active_scope_only(self):
        self._accept_assignment()
        second_term, active_offering = self._create_second_term_offering()
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=self.academic_year,
            term=second_term,
        )
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:reminder_center"))

        self.assertEqual(response.status_code, 200)
        offering_ids = set(response.context["form"].fields["offering"].queryset.values_list("id", flat=True))
        self.assertIn(active_offering.id, offering_ids)
        self.assertNotIn(self.offering.id, offering_ids)

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

    def test_faculty_cannot_undo_assignment_acceptance_from_portal(self):
        self._accept_assignment()
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Undo Acceptance")

        response = self.client.post(reverse("faculty_portal:faculty_assignment_undo_accept", args=[self.assignment.id]))

        self.assertRedirects(response, reverse("faculty_portal:my_courses"))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.ACCEPTED)
        self.assertIsNotNone(self.assignment.accepted_at)
        self.assertEqual(self.assignment.accepted_by_id, self.faculty_user.id)

    def test_faculty_cannot_undo_assignment_acceptance_after_gradebook_work_starts(self):
        self._accept_assignment()
        component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=component,
            title="Started Work",
            total_score=100,
            created_by_user=self.faculty_user,
        )
        self.client.force_login(self.faculty_user)

        response = self.client.post(reverse("faculty_portal:faculty_assignment_undo_accept", args=[self.assignment.id]))

        self.assertRedirects(response, reverse("faculty_portal:my_courses"))
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.response_status, FacultyAssignment.ResponseStatus.ACCEPTED)
        self.assertIsNotNone(self.assignment.accepted_at)

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

    def test_faculty_grading_template_page_hides_grade_calculator_button(self):
        self._accept_assignment()
        TenantGradingProfile.objects.create(
            tenant=self.tenant,
            profile_code="FAC-CALC",
            profile_name="Faculty Calculator Formula",
            grading_template=self.template,
            final_grade_formula_mode=TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS,
            final_grade_formula_json={
                "period_weights": [
                    {"period_code": "PRELIM", "weight": "100.00"},
                ]
            },
            is_active=True,
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        prelim_exam = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")

        self.client.force_login(self.faculty_user)
        template_response = self.client.get(
            reverse("faculty_portal:offering_grading_template", kwargs={"offering_id": self.offering.id})
        )
        self.assertEqual(template_response.status_code, 200)
        self.assertNotContains(template_response, "Open Grade Calculator")

        response = self.client.post(
            reverse("faculty_portal:offering_grading_calculator", kwargs={"offering_id": self.offering.id}),
            {
                "sample_value": "85.00",
                f"component_{class_standing.id}_raw": "90.00",
                f"component_{class_standing.id}_total": "100.00",
                f"component_{prelim_exam.id}_raw": "80.00",
                f"component_{prelim_exam.id}_total": "100.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grade Calculator")
        self.assertContains(response, "Simulation only")
        self.assertContains(response, "FAC-CALC")
        self.assertContains(response, "Final Grade Computation")
        self.assertContains(response, "(93.00 x 100.00%) = 93.00")

    def test_faculty_can_open_at_risk_monitor_when_prediction_is_enabled(self):
        self._enable_grade_prediction()
        self._accept_assignment()
        self._create_active_student(
            student_no="2025-INT-001",
            last_name="Intervention",
            first_name="Learner",
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            title="Intervention Activity",
            total_score=100,
            created_by_user=self.faculty_user,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:student_at_risk_monitor"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Student Intervention Monitor")
        self.assertContains(response, "Current-period focus")
        self.assertContains(response, "Current Standing")
        self.assertContains(response, "Main Concern")
        self.assertContains(response, "Suggested Intervention")
        self.assertContains(response, "Needs Attention")
        self.assertContains(response, "Monitor")
        self.assertContains(response, "Missing Work")
        self.assertContains(response, "On Track")
        content = response.content.decode().lower()
        for banned_text in [
            "below passing",
            "failing",
            "likely to fail",
            "possible final grade below passing",
            "prediction confidence",
            "coverage percentage",
            "projected final grade",
            "class ranking",
        ]:
            self.assertNotIn(banned_text, content)
        self.assertTrue(
            AuditLog.objects.filter(
                action="VIEW_STUDENT_INTERVENTION_MONITOR",
                entity_type="StudentInterventionMonitor",
                actor_user=self.faculty_user,
            ).exists()
        )

    def test_student_intervention_monitor_prioritizes_missing_work_before_grade_concern(self):
        self._enable_grade_prediction()
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-INT-MISS",
            last_name="Missing",
            first_name="Priority",
        )
        self._create_low_exam_score(student=student)
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            title="Unencoded Activity",
            total_score=100,
            created_by_user=self.faculty_user,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:student_at_risk_monitor"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Missing Work")
        self.assertContains(response, "Not ready to assess")
        self.assertContains(response, "Activity scores still need review.")
        self.assertContains(response, "Review missing activity scores.")
        self.assertNotContains(response, "Current standing needs attention.")

    def test_student_intervention_monitor_uses_soft_grade_concern_wording(self):
        self._enable_grade_prediction()
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-INT-SOFT",
            last_name="Soft",
            first_name="Wording",
        )
        self._create_low_exam_score(student=student)

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:student_at_risk_monitor"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Needs Attention")
        self.assertContains(response, "Needs attention")
        self.assertContains(response, "Exam score needs attention.")
        self.assertContains(response, "Review exam performance and advise the student if needed.")
        self.assertNotContains(response, "Below passing")
        self.assertNotContains(response, "failing")

    def test_student_intervention_monitor_treats_missing_attendance_as_incomplete_encoding(self):
        self._enable_grade_prediction()
        self._accept_assignment()
        self._create_active_student(
            student_no="2025-INT-ATT",
            last_name="Attendance",
            first_name="Missing",
        )
        AttendanceSession.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            session_date=date(2026, 4, 20),
            title="Attendance Session",
            created_by_user=self.faculty_user,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:student_at_risk_monitor"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Missing Work")
        self.assertContains(response, "Attendance records are not yet complete.")
        self.assertContains(response, "Check attendance records before submission.")
        self.assertNotContains(response, "attendance behavior")

    def test_prediction_guide_uses_simple_sample_and_column_labels(self):
        self._enable_grade_prediction()
        self._accept_assignment()

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_prediction_guide",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sample Student Record")
        self.assertContains(response, "Estimated Prelim Grade")
        self.assertContains(response, "Encoded Work")
        self.assertContains(response, "Still Missing")
        self.assertContains(response, "Period Alert")
        self.assertContains(response, "Possible Final Grade")
        self.assertNotContains(response, "Prediction Methodology")
        self.assertNotContains(response, "Computed From / Factors")

    def test_period_prediction_page_uses_teacher_friendly_period_specific_labels(self):
        self._enable_grade_prediction()
        SystemSetting.objects.update_or_create(
            tenant=None,
            setting_key="FEATURE_GRADE_PREDICTION_WHAT_IF_ENABLED",
            defaults={"setting_value": "1", "value_type": SystemSetting.ValueType.BOOL, "is_active": True},
        )

        self._accept_assignment()

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_prediction",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Prelim Grade Prediction")
        self.assertContains(response, "current grading period")
        self.assertContains(response, "It does not change the actual gradebook.")
        self.assertContains(response, "Assumed Average for Missing Work (%)")
        self.assertContains(response, "Scenario Name (Optional)")
        self.assertContains(response, "Predict")
        self.assertContains(response, "Save Scenario")
        self.assertContains(response, "Apply Filter")
        self.assertContains(response, "Clear")
        self.assertContains(response, "Estimated Prelim Grade")
        self.assertContains(response, "If Missing Work Is Perfect")
        self.assertContains(response, "If Missing Work Is Zero")
        self.assertContains(response, "Possible Final Grade")
        self.assertContains(response, "Final Grade Outlook")
        self.assertContains(response, "Score Needed to Pass")
        self.assertContains(response, "Encoded Work")
        self.assertContains(response, "Still Missing")
        self.assertContains(response, "Period Alert")

    def test_reminder_center_shows_submission_non_compliance_notice(self):
        self.client.force_login(self.faculty_user)
        SubmissionNonComplianceNotice.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            offering=self.offering,
            template_period=self.prelim,
            faculty_user=self.faculty_user,
            notice_level=SubmissionNonComplianceNotice.NoticeLevel.WARNING,
            sequence_no=2,
            title="Warning for Continued Non-Compliance",
            message="The Prelim submission is still overdue and needs immediate follow-up.",
            deadline_at=timezone.now() - timedelta(days=4),
            issued_at=timezone.now() - timedelta(hours=1),
            recipient_emails_json=[self.faculty_user.email],
            recipient_roles_json=["FACULTY"],
        )

        response = self.client.get(reverse("faculty_portal:reminder_center"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Submission Compliance Notices")
        self.assertContains(response, "Warning for Continued Non-Compliance")
        self.assertContains(response, "The Prelim submission is still overdue")

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

    def test_dashboard_does_not_surface_student_level_incomplete_kpi(self):
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
        self.assertContains(response, "Grade Encoding Status")
        self.assertNotContains(response, "Incomplete Students")
        self.assertNotContains(response, student.student_no)

    def test_dashboard_pending_issues_replaces_student_follow_up_container(self):
        self._enable_grade_prediction()
        self._accept_assignment()

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Grade Issues")
        self.assertContains(response, "Grade Encoding Status")
        self.assertNotContains(response, "Students At Risk")
        self.assertNotContains(response, "Students Needing Follow-up")
        self.assertNotContains(response, "Student Support")

    def test_dashboard_pending_grade_issues_appear_only_when_relevant(self):
        self._accept_assignment()
        self._create_active_student(student_no="2025-MISS-001", last_name="Missing", first_name="Grade")

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "required grading items are missing")
        self.assertContains(response, "Pending Grade Issues")
        self.assertNotContains(response, "needs follow-up this grading period")

    def test_dashboard_priority_actions_are_scoped_to_logged_in_faculty(self):
        other_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1B",
            name="BSIT 1B",
        )
        other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=other_section,
        )
        other_faculty = User.objects.create_user(
            username="other_faculty",
            email="other_faculty@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        other_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=other_offering,
            faculty_user=other_faculty,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=other_faculty,
        )
        self._accept_assignment()
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=other_offering,
            template_period=self.prelim,
            template_component=exam_component,
            title="Other Faculty Activity",
            total_score=100,
            created_by_user=other_faculty,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Other Faculty Activity")
        self.assertNotContains(response, "BSIT-1B")
        self.assertEqual(other_assignment.faculty_user_id, other_faculty.id)

    def test_dashboard_hides_student_level_at_risk_information(self):
        self._enable_grade_prediction()
        self._accept_assignment()
        visible_student = self._create_active_student(
            student_no="2025-RISK-101",
            last_name="Visible",
            first_name="Learner",
        )
        self._create_low_exam_score(student=visible_student)

        hidden_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1C",
            name="BSIT 1C",
        )
        hidden_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=hidden_section,
        )
        hidden_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-RISK-999",
            last_name="Hidden",
            first_name="Learner",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=hidden_student,
            course_offering=hidden_offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "View Performance")
        self.assertNotContains(response, "needs follow-up this grading period")
        self.assertNotContains(response, "Visible Learner")
        self.assertNotContains(response, "Hidden Learner")
        self.assertNotContains(response, "2025-RISK-999")

    def test_dashboard_consolidates_unencoded_activity_into_pending_issues(self):
        self._accept_assignment()
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            title="Unscored Activity",
            total_score=100,
            created_by_user=self.faculty_user,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(hours=1),
            is_locked=False,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Grade Issues")
        self.assertContains(response, "Not Started")
        self.assertContains(response, "Continue Encoding")
        self.assertContains(
            response,
            reverse("faculty_portal:period_activities", args=[self.offering.id, self.prelim.id]),
        )

    def test_dashboard_status_check_does_not_auto_lock_reopened_gradebook(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-DASH-LOCK-001",
            last_name="Dashboard",
            first_name="Locked",
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            title="Dashboard Locked Activity",
            total_score=100,
            created_by_user=self.faculty_user,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("90")}],
        )
        lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timedelta(days=1),
            is_locked=False,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )
        self.client.force_login(self.faculty_user)
        self.client.post(
            reverse("faculty_portal:period_self_reopen", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
            {"remarks": "Need to revise before cutoff."},
        )
        lock.deadline_at = timezone.now() - timedelta(hours=1)
        lock.save(update_fields=["deadline_at", "updated_at"])

        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Grade Encoding Status")
        lock.refresh_from_db()
        self.assertFalse(lock.is_locked)

    def test_period_card_shows_reopen_request_action_when_auto_closed_after_deadline(self):
        self._accept_assignment()
        SystemSettingService.set(
            FeatureSettingsService.GRADE_DEADLINE_ENFORCEMENT_POLICY_KEY,
            FeatureSettingsService.GRADE_DEADLINE_POLICY_AUTO_CLOSE_REQUIRES_REOPEN,
            tenant_id=self.tenant.id,
            value_type="STRING",
            is_active=True,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(hours=1),
            is_locked=False,
            is_active=True,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:offering_periods", args=[self.offering.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Request Gradebook Reopen")
        self.assertContains(response, f"deadlineReopenRequestModal_{self.prelim.id}")
        self.assertContains(
            response,
            reverse("faculty_portal:period_reopen_request", args=[self.offering.id, self.prelim.id]),
        )

    def test_approved_reopen_request_overrides_locked_period_on_faculty_card(self):
        self._accept_assignment()
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(hours=1),
            is_locked=True,
            is_active=True,
        )
        submission = GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.DRAFT,
        )
        reopen_request = GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=submission,
            offering=self.offering,
            template_period=self.prelim,
            requested_by_user=self.faculty_user,
            reviewed_by_user=self.faculty_user,
            reviewed_at=timezone.now(),
            status=GradeSubmissionReopenRequest.Status.APPROVED,
            justification="Need to finish encoding.",
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:offering_periods", args=[self.offering.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reopened")
        self.assertContains(response, f"Reopen request #{reopen_request.id} was approved")
        self.assertNotContains(response, "This grading period is locked by admin")

        response = self.client.get(reverse("faculty_portal:period_activities", args=[self.offering.id, self.prelim.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Activity encoding is open")
        self.assertNotContains(response, "This period is locked by admin. Editing is disabled.")

    def test_blank_activity_score_saves_as_zero_and_counts_in_average(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-ZERO-001",
            last_name="Zero",
            first_name="Default",
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        first_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            title="Recitation 1",
            total_score=100,
            created_by_user=self.faculty_user,
        )
        second_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            title="Recitation 2",
            total_score=100,
            created_by_user=self.faculty_user,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=first_activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("80")}],
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:activity_scores", args=[self.offering.id, self.prelim.id, second_activity.id])
        )
        self.assertContains(response, f'name="raw_{student.id}"')
        self.assertContains(response, 'value="0"')

        response = self.client.post(
            reverse("faculty_portal:activity_scores", args=[self.offering.id, self.prelim.id, second_activity.id]),
            {f"raw_{student.id}": ""},
        )

        self.assertEqual(response.status_code, 302)
        score = StudentActivityScore.objects.get(activity=second_activity, student=student, is_active=True)
        self.assertEqual(score.raw_score, Decimal("0.00"))
        self.assertEqual(score.computed_score, Decimal("50.00"))
        period_grade = StudentPeriodGrade.objects.get(offering=self.offering, template_period=self.prelim, student=student)
        self.assertEqual(period_grade.class_standing_grade, Decimal("70.00"))

    def test_dashboard_does_not_leak_cross_tenant_priority_actions(self):
        self._accept_assignment()
        other_tenant = Tenant.objects.create(code="OTHER", name="Other School")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER-MAIN", name="Other Main")
        other_department = Department.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            code="OTHER-DEPT",
            name="Other Department",
        )
        other_program = Program.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            code="BSOA",
            name="BS Office Admin",
        )
        other_year = AcademicYear.objects.create(
            tenant=other_tenant,
            code="2025-2026",
            name="AY 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        other_term = Term.objects.create(
            tenant=other_tenant,
            academic_year=other_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )
        other_course = Course.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            code="OTHER-COURSE",
            title="Other Course",
        )
        other_section = Section.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            code="OTHER-1A",
            name="Other 1A",
        )
        other_offering = CourseOffering.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            academic_year=other_year,
            term=other_term,
            course=other_course,
            section=other_section,
        )
        FacultyAssignment.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            offering=other_offering,
            faculty_user=self.faculty_user,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
        )
        other_template = GradingTemplate.objects.create(
            tenant=other_tenant,
            code="OTHER_TEMPLATE",
            name="Other Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
            default_base_value=50,
        )
        other_period = GradingTemplatePeriod.objects.create(
            template=other_template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        other_component = GradingTemplateComponent.objects.create(
            template_period=other_period,
            code="EXAM",
            name="Exam",
            weight_percentage=100,
            sort_order=1,
            is_exam_component=True,
        )
        CourseTemplateAssignment.objects.create(
            course=other_course,
            grading_template=other_template,
            effective_from_term=other_term,
        )
        GradeActivity.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            offering=other_offering,
            template_period=other_period,
            template_component=other_component,
            title="Cross Tenant Activity",
            total_score=100,
            created_by_user=self.faculty_user,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "OTHER-COURSE")
        self.assertNotContains(response, "Cross Tenant Activity")

    def test_period_summary_print_sheet_is_pinnacle_ready(self):
        self._accept_assignment()
        self._create_active_student(
            student_no="2025-PIN-001",
            last_name="Pinnacle",
            first_name="Ready",
        )
        student = Student.objects.get(student_no="2025-PIN-001")
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            student=student,
            period_grade=Decimal("88.00"),
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_summary",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Print Periodic Grades")
        self.assertContains(response, "logos/ncba-logo.png")
        self.assertContains(response, "Summary of Periodic Grades")
        self.assertContains(response, "For encoding of final periodic grades into the Pinnacle system")
        self.assertContains(response, "NATIONAL COLLEGE OF BUSINESS AND ARTS")
        self.assertContains(response, "Grading Period:")
        self.assertContains(response, "Semester / Term:")
        self.assertContains(response, "Faculty:")
        self.assertContains(response, "Course Code:")
        self.assertContains(response, "Course Title:")
        self.assertContains(response, '<th class="print-grade print-period-grade">PRELIM GRADE</th>', html=True)

    def test_class_tabulation_sheet_available_after_all_periods_are_submitted(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-TAB-001",
            last_name="Tabulation",
            first_name="Ready",
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            title="Q1",
            total_score=Decimal("20.00"),
            activity_date=date(2025, 7, 1),
            created_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=Decimal("18.00"),
            computed_score=Decimal("90.00"),
            encoded_by_user=self.faculty_user,
        )
        for index, period in enumerate([self.prelim, self.midterm, self.prefinal, self.final], start=1):
            StudentPeriodGrade.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=self.offering,
                template_period=period,
                student=student,
                period_grade=Decimal(80 + index),
            )
            GradeSubmission.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=self.offering,
                template_period=period,
                status=GradeSubmission.Status.SUBMITTED,
                submitted_by_user=self.faculty_user,
                submitted_at=timezone.now(),
            )
        StudentFinalGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            student=student,
            final_grade=Decimal("88.00"),
            is_submitted=True,
        )

        self.client.force_login(self.faculty_user)
        periods_response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )
        self.assertContains(periods_response, "Complete Tabulation Sheet")

        response = self.client.get(
            reverse("faculty_portal:offering_class_tabulation_sheet", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Complete Tabulation Sheet")
        self.assertContains(response, "/media/logos/ncba-logo.png")
        self.assertContains(response, "Print Official PDF")
        self.assertContains(response, "Q1")
        self.assertContains(response, "90.00")
        self.assertContains(response, "PRELIM")
        self.assertContains(response, "MIDTERM")
        self.assertContains(response, "PRE-FINAL")
        self.assertContains(response, "FINAL")
        self.assertContains(response, "Final Grade")
        self.assertContains(response, "Prepared and Submitted By")
        self.assertContains(response, "**** NOTHING FOLLOWS *****")
        self.assertNotContains(response, ">ACTIVE</td>")

        pdf_response = self.client.get(
            reverse("faculty_portal:offering_class_tabulation_sheet", kwargs={"offering_id": self.offering.id})
            + "?format=pdf"
        )
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))
        pdf = PdfReader(BytesIO(pdf_response.content))
        self.assertGreaterEqual(len(pdf.pages), 4)
        for page in pdf.pages:
            self.assertGreater(float(page.mediabox.width), float(page.mediabox.height))
            self.assertAlmostEqual(float(page.mediabox.width), 1008.0, places=1)
            self.assertAlmostEqual(float(page.mediabox.height), 612.0, places=1)
            self.assertIn("COMPLETE TABULATION SHEET", page.extract_text())
        all_pdf_text = "\n".join(page.extract_text() for page in pdf.pages)
        self.assertIn("PRELIM (PRELIM) - PART 1 OF", all_pdf_text)
        self.assertIn("MIDTERM", all_pdf_text)
        self.assertIn("PRE-FINAL", all_pdf_text)
        self.assertIn("FINAL EXAM", all_pdf_text)
        self.assertIn("Q1", all_pdf_text)
        self.assertIn("90.00", all_pdf_text)

    def test_complete_tabulation_pdf_handles_draft_many_activities_and_missing_zero_exempt(self):
        self._accept_assignment()
        first_student = self._create_active_student(
            student_no="2025-COMPLETE-001",
            last_name="Complete",
            first_name="One",
        )
        second_student = self._create_active_student(
            student_no="2025-COMPLETE-002",
            last_name="Complete",
            first_name="Two",
        )
        component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        active_activities = []
        for index in range(1, 10):
            active_activities.append(
                GradeActivity.objects.create(
                    tenant=self.tenant,
                    campus=self.campus,
                    offering=self.offering,
                    template_period=self.prelim,
                    template_component=component,
                    title=f"Readable Activity {index}",
                    total_score=Decimal("20.00"),
                    activity_date=date(2025, 7, index),
                    created_by_user=self.faculty_user,
                )
            )
        archived_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=component,
            title="Archived Activity Must Not Print",
            total_score=Decimal("20.00"),
            is_active=False,
            created_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=active_activities[0],
            student=first_student,
            raw_score=Decimal("0.00"),
            computed_score=Decimal("50.00"),
            encoded_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=active_activities[1],
            student=first_student,
            raw_score=Decimal("0.00"),
            computed_score=Decimal("0.00"),
            is_excused=True,
            encoded_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=archived_activity,
            student=first_student,
            raw_score=Decimal("20.00"),
            computed_score=Decimal("100.00"),
            encoded_by_user=self.faculty_user,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_class_tabulation_sheet", kwargs={"offering_id": self.offering.id})
            + "?format=pdf"
        )

        self.assertEqual(response.status_code, 200)
        pdf = PdfReader(BytesIO(response.content))
        texts = [page.extract_text() for page in pdf.pages]
        combined = "\n".join(texts)
        normalized_text = " ".join(combined.split())
        self.assertIn("Readable Activity 1", normalized_text)
        self.assertIn("Readable Activity 9", normalized_text)
        self.assertNotIn("Archived Activity Must Not Print", combined)
        self.assertIn("MISSING", combined)
        self.assertIn("EXEMPT", combined)
        self.assertIn("Not Submitted", combined)
        self.assertGreaterEqual(combined.count("PRELIM (PRELIM) - PART"), 2)
        for page_text in texts:
            self.assertIn("Complete, One", page_text)
            self.assertIn("Complete, Two", page_text)

    def test_historical_accepted_faculty_keeps_report_only_access(self):
        self._accept_assignment()
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        self.client.force_login(self.faculty_user)

        courses_response = self.client.get(reverse("faculty_portal:my_courses"))
        report_response = self.client.get(
            reverse("faculty_portal:offering_class_tabulation_sheet", kwargs={"offering_id": self.offering.id})
            + "?format=pdf"
        )

        self.assertEqual(courses_response.status_code, 200)
        self.assertContains(courses_response, "Historical Tabulation Reports")
        self.assertContains(courses_response, "Historical report only")
        self.assertEqual(report_response.status_code, 200)
        self.assertEqual(report_response["Content-Type"], "application/pdf")
        self.assertEqual(
            self.client.get(
                reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
            ).status_code,
            404,
        )

    def test_unassigned_faculty_cannot_open_complete_tabulation(self):
        self._accept_assignment()
        other_faculty = User.objects.create_user(
            username="unassigned_report_faculty",
            email="unassigned-report@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        faculty_role = Role.objects.get(code="FACULTY")
        UserRole.objects.create(
            user=other_faculty,
            role=faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(other_faculty)

        response = self.client.get(
            reverse("faculty_portal:offering_class_tabulation_sheet", kwargs={"offering_id": self.offering.id})
            + "?format=pdf"
        )

        self.assertEqual(response.status_code, 404)

    def test_complete_tabulation_uses_only_faculty_of_record_signature(self):
        self._accept_assignment()
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        other_user = User.objects.create_user(
            username="other_signature_owner",
            email="other-signature@example.com",
            password="testpass123",
        )

        def signature_upload(color):
            buffer = BytesIO()
            Image.new("RGBA", (220, 80), color).save(buffer, format="PNG")
            return SimpleUploadedFile("signature.png", buffer.getvalue(), content_type="image/png")

        UserSignatureService.store_signature(
            user=other_user,
            uploaded_file=signature_upload((180, 10, 10, 255)),
            actor=other_user,
        )
        self.client.force_login(self.faculty_user)
        url = (
            reverse("faculty_portal:offering_class_tabulation_sheet", kwargs={"offering_id": self.offering.id})
            + "?format=pdf"
        )

        without_own_signature = self.client.get(url)
        text_without_own = "\n".join(
            page.extract_text() for page in PdfReader(BytesIO(without_own_signature.content)).pages
        )
        self.assertIn("No stored faculty signature.", text_without_own)
        self.assertFalse(
            UserSignatureUsageLog.objects.filter(
                document_type=UserSignatureUsageLog.DocumentType.COMPLETE_TABULATION_SHEET
            ).exists()
        )

        UserSignatureService.store_signature(
            user=self.faculty_user,
            uploaded_file=signature_upload((10, 80, 20, 255)),
            actor=self.faculty_user,
        )
        html_with_own_signature = self.client.get(
            reverse("faculty_portal:offering_class_tabulation_sheet", kwargs={"offering_id": self.offering.id})
        )
        self.assertContains(html_with_own_signature, 'class="signature-image"', html=False)
        self.assertContains(html_with_own_signature, "data:image/png;base64,", html=False)
        self.assertContains(html_with_own_signature, "Prepared and Submitted By")
        with_own_signature = self.client.get(url)
        text_with_own = "\n".join(
            page.extract_text() for page in PdfReader(BytesIO(with_own_signature.content)).pages
        )
        self.assertNotIn("No stored faculty signature.", text_with_own)
        self.assertTrue(
            UserSignatureUsageLog.objects.filter(
                user=self.faculty_user,
                document_type=UserSignatureUsageLog.DocumentType.COMPLETE_TABULATION_SHEET,
            ).exists()
        )
        self.assertFalse(
            UserSignatureUsageLog.objects.filter(
                user=other_user,
                document_type=UserSignatureUsageLog.DocumentType.COMPLETE_TABULATION_SHEET,
            ).exists()
        )

    def test_mobile_course_and_period_pages_keep_primary_actions_and_collapse_secondary_metadata(self):
        self._accept_assignment()
        self.client.force_login(self.faculty_user)

        courses_response = self.client.get(reverse("faculty_portal:my_courses"))
        periods_response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(courses_response.status_code, 200)
        self.assertContains(courses_response, "course-card-mobile-details")
        self.assertContains(courses_response, "Schedule and room")
        self.assertContains(
            courses_response,
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id}),
        )
        self.assertContains(
            courses_response,
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
        )
        self.assertEqual(periods_response.status_code, 200)
        self.assertContains(periods_response, "period-page-toolbar-actions")
        self.assertContains(periods_response, "Complete Tabulation Sheet")
        self.assertContains(periods_response, "View Grading Template")

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
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

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
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<th rowspan="4" class="metric-col metric-final">PRELIM GRADE</th>', html=True)
        self.assertNotContains(response, "Official computed grades are currently hidden by admin configuration.")
        table_html = response.content.decode().split('class="table table-hover mb-0 align-middle class-record-table"', 1)[1]
        self.assertLess(table_html.index(">Status<"), table_html.index(">PRELIM GRADE<"))
        self.assertLess(table_html.index(">PRELIM GRADE<"), table_html.index(">CLASS STANDING<"))
        student_row = table_html.split("2025-DEFAULT-001", 1)[1].split("</tr>", 1)[0]
        self.assertNotIn("status-active-label", student_row)
        self.assertNotIn(">ACTIVE<", student_row)
        self.assertContains(response, 'id="gradeExplanationPrivacyShield"', html=False)
        self.assertContains(response, "grade-explanation-privacy-shield")
        self.assertContains(response, 'modalEl.addEventListener("show.bs.modal"', html=False)

        explanation_response = self.client.get(
            reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": self.offering.id,
                    "period_id": self.prelim.id,
                    "student_id": student.id,
                    "grade_type": GradeExplanationService.GRADE_TYPE_PERIOD,
                },
            )
        )
        self.assertEqual(explanation_response.status_code, 200)
        self.assertContains(explanation_response, "PRELIM Grade Summary")
        self.assertContains(explanation_response, "Contribution to Period Grade")
        self.assertContains(explanation_response, "Class Standing Breakdown")
        self.assertContains(explanation_response, "Activity Details")
        self.assertContains(explanation_response, "Exam Details")
        self.assertContains(explanation_response, "View full computation details")
        self.assertNotContains(explanation_response, "Show Detailed Computation")
        self.assertNotContains(explanation_response, "Official rounded grade")

        stored_period_grade = StudentPeriodGrade.objects.get(
            offering=self.offering,
            template_period=self.prelim,
            student=student,
        )
        stored_period_grade.class_standing_grade = Decimal("78")
        stored_period_grade.exam_grade = Decimal("60")
        stored_period_grade.period_grade = Decimal("71")
        stored_period_grade.save(
            update_fields=[
                "class_standing_grade",
                "exam_grade",
                "period_grade",
                "updated_at",
            ]
        )

        changed_setup_response = self.client.get(
            reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": self.offering.id,
                    "period_id": self.prelim.id,
                    "student_id": student.id,
                    "grade_type": GradeExplanationService.GRADE_TYPE_PERIOD,
                },
            )
        )
        self.assertEqual(changed_setup_response.status_code, 200)
        self.assertContains(changed_setup_response, "Official Submitted Grade")
        self.assertContains(changed_setup_response, "Official PRELIM Grade")
        self.assertContains(changed_setup_response, "71.00")
        self.assertContains(changed_setup_response, "Current Grading Setup Check")
        self.assertContains(changed_setup_response, "Current setup calculation:")
        self.assertContains(
            changed_setup_response,
            "The grading setup or source records changed after this grade was submitted.",
        )
        self.assertNotContains(changed_setup_response, "Official rounded grade")

    def test_period_summary_average_activities_display_matches_detail_computation_mode(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-AVE-ACT-001",
            last_name="Average",
            first_name="Activity",
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        quizzes = GradingTemplateSubcomponent.objects.create(
            template_component=class_standing,
            code="QUIZZES",
            name="Quizzes",
            weight_percentage=Decimal("40.00"),
            sort_order=1,
            is_active=True,
        )
        participation = GradingTemplateSubcomponent.objects.create(
            template_component=class_standing,
            code="PG_CA_PO",
            name="Participation/Output",
            weight_percentage=Decimal("60.00"),
            sort_order=2,
            detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            is_active=True,
        )
        recitation = GradingTemplateDetail.objects.create(
            template_subcomponent=participation,
            code="RECITATION",
            name="Recitation",
            weight_percentage=Decimal("20.00"),
            sort_order=1,
            is_active=True,
        )
        assignment = GradingTemplateDetail.objects.create(
            template_subcomponent=participation,
            code="ASSIGNMENT",
            name="Assignment",
            weight_percentage=Decimal("40.00"),
            sort_order=2,
            is_active=True,
        )
        oral = GradingTemplateDetail.objects.create(
            template_subcomponent=participation,
            code="ORAL",
            name="Oral Presentation",
            weight_percentage=Decimal("40.00"),
            sort_order=3,
            is_active=True,
        )

        def add_activity_score(title, subcomponent, detail, computed_score):
            activity = GradeActivity.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=self.offering,
                template_period=self.prelim,
                template_component=class_standing,
                template_subcomponent=subcomponent,
                template_detail=detail,
                title=title,
                total_score=Decimal("100.00"),
                created_by_user=self.faculty_user,
                is_active=True,
            )
            StudentActivityScore.objects.create(
                activity=activity,
                student=student,
                raw_score=computed_score,
                computed_score=computed_score,
                encoded_by_user=self.faculty_user,
                is_active=True,
            )

        add_activity_score("Q1", quizzes, None, Decimal("75.00"))
        add_activity_score("R1", participation, recitation, Decimal("97.50"))
        add_activity_score("ASSIGN1", participation, assignment, Decimal("95.00"))
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            student=student,
            class_standing_grade=Decimal("75.00"),
            computed_by_user=self.faculty_user,
            is_finalized=False,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        block = response.context["rows"][0]["class_standing_blocks"][0]
        self.assertEqual(block["sections"][1]["groups"][0]["average"], Decimal("97.50"))
        self.assertEqual(block["sections"][1]["groups"][1]["average"], Decimal("95.00"))
        self.assertEqual(len(block["sections"][1]["groups"]), 2)
        self.assertNotContains(response, "ORAL PRESENTATION")
        self.assertEqual(block["sections"][1]["average"], Decimal("96.25"))
        self.assertEqual(block["total"], Decimal("87.75"))
        self.assertEqual(response.context["summary_layout"]["class_standing_blocks"][0]["sections"][1]["avg_label"], "P/O AVE")
        self.assertContains(response, "P/O AVE")
        self.assertContains(response, "CS AVE")
        self.assertContains(response, "20.00%")
        self.assertContains(response, "40.00%")
        self.assertContains(response, "configured; not used in average")
        quizzes_layout = response.context["summary_layout"]["class_standing_blocks"][0]["sections"][0]
        participation_layout = response.context["summary_layout"]["class_standing_blocks"][0]["sections"][1]
        self.assertEqual(quizzes_layout["color_class"], "summary-group-quizzes")
        self.assertEqual(participation_layout["color_class"], "summary-group-participation")
        self.assertContains(
            response,
            '<th rowspan="3" class="metric-col summary-group-class-standing-total">CS AVE</th>',
            html=True,
        )
        self.assertContains(response, "summary-group-quizzes")
        self.assertContains(response, "summary-group-participation")
        self.assertContains(response, "summary-group-class-standing-total")
        refreshed = StudentPeriodGrade.objects.get(offering=self.offering, template_period=self.prelim, student=student)
        self.assertEqual(refreshed.class_standing_grade, Decimal("88"))

        explanation_response = self.client.get(
            reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": self.offering.id,
                    "period_id": self.prelim.id,
                    "student_id": student.id,
                    "grade_type": GradeExplanationService.GRADE_TYPE_PERIOD,
                },
            )
        )
        self.assertEqual(explanation_response.status_code, 200)
        self.assertContains(explanation_response, "Configured Detail Weight: 20.00%")
        self.assertContains(explanation_response, "reference only; not used in the average")

    def test_period_summary_weighted_details_keeps_empty_detail_columns(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-WGT-DETAIL-001",
            last_name="Weighted",
            first_name="Detail",
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        participation = GradingTemplateSubcomponent.objects.create(
            template_component=class_standing,
            code="PG_CA_PO_WEIGHTED",
            name="Participation/Output",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )
        recitation = GradingTemplateDetail.objects.create(
            template_subcomponent=participation,
            code="RECITATION_WEIGHTED",
            name="Recitation",
            weight_percentage=Decimal("50.00"),
            sort_order=1,
            is_active=True,
        )
        GradingTemplateDetail.objects.create(
            template_subcomponent=participation,
            code="ORAL_WEIGHTED",
            name="Oral Presentation",
            weight_percentage=Decimal("50.00"),
            sort_order=2,
            is_active=True,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            template_subcomponent=participation,
            template_detail=recitation,
            title="R1",
            total_score=Decimal("100.00"),
            created_by_user=self.faculty_user,
            is_active=True,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=Decimal("90.00"),
            computed_score=Decimal("90.00"),
            encoded_by_user=self.faculty_user,
            is_active=True,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        section = response.context["summary_layout"]["class_standing_blocks"][0]["sections"][0]
        self.assertEqual([group["label"] for group in section["groups"]], ["RECITATION", "ORAL PRESENTATION"])
        self.assertContains(response, "ORAL PRESENTATION")

    def test_period_summary_hides_active_status_but_shows_non_active_status(self):
        active_student = self._create_active_student(
            student_no="2025-STAT-001",
            last_name="Active",
            first_name="Hidden",
        )
        dropped_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-STAT-002",
            last_name="Dropped",
            first_name="Shown",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=dropped_student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.DRP,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        for student, grade in ((active_student, "88.00"), (dropped_student, "80.00")):
            StudentPeriodGrade.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=self.offering,
                template_period=self.prelim,
                student=student,
                period_grade=Decimal(grade),
            )
        self._accept_assignment()
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        table_html = response.content.decode().split('class="table table-hover mb-0 align-middle class-record-table"', 1)[1]
        active_row = table_html.split("2025-STAT-001", 1)[1].split("</tr>", 1)[0]
        dropped_row = table_html.split("2025-STAT-002", 1)[1].split("</tr>", 1)[0]
        self.assertNotIn("ACTIVE", active_row)
        self.assertIn("DRP", dropped_row)

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
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<th rowspan="4" class="metric-col metric-final">PRELIM GRADE</th>', html=True)
        self.assertContains(response, "Passed")
        self.assertContains(response, "Failed")
        self.assertContains(response, "93")
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
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.final,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.final.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PRELIM GRADE")
        self.assertContains(response, "MIDTERM GRADE")
        self.assertContains(response, "PRE-FINAL GRADE")
        self.assertContains(response, "FINAL EXAM")
        self.assertContains(response, "FINAL GRADE")
        self.assertContains(response, "81")
        self.assertContains(response, "84")
        self.assertContains(response, "87")
        self.assertContains(response, "89")
        self.assertContains(response, "85")

        period_explanation = self.client.get(
            reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": self.offering.id,
                    "period_id": self.final.id,
                    "student_id": student.id,
                    "grade_type": GradeExplanationService.GRADE_TYPE_PERIOD,
                },
            )
        )
        self.assertEqual(period_explanation.status_code, 200)
        self.assertContains(period_explanation, "FINAL EXAM")
        self.assertContains(period_explanation, "Official FINAL EXAM Grade")
        self.assertContains(period_explanation, "Final Exam Score")

        final_explanation = self.client.get(
            reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": self.offering.id,
                    "period_id": self.final.id,
                    "student_id": student.id,
                    "grade_type": GradeExplanationService.GRADE_TYPE_FINAL,
                },
            )
        )
        self.assertEqual(final_explanation.status_code, 200)
        self.assertContains(final_explanation, "Final Grade Summary")
        self.assertContains(final_explanation, "PRELIM GRADE")
        self.assertContains(final_explanation, "MIDTERM GRADE")
        self.assertContains(final_explanation, "PRE-FINAL GRADE")
        self.assertContains(final_explanation, "FINAL EXAM")
        self.assertContains(final_explanation, "FG = (PRELIM GRADE + MIDTERM GRADE + PRE-FINAL GRADE + FINAL EXAM) / 4")
        self.assertNotContains(final_explanation, "FINAL FINAL GRADE")

    def test_final_period_summary_uses_custom_grade_column_label_when_configured(self):
        self.final.grade_column_label = "FINAL PERIOD GRADE"
        self.final.save(update_fields=["grade_column_label", "updated_at"])

        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-CUSTOM-001",
            last_name="Custom",
            first_name="Label",
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
            final_grade=Decimal("85.00"),
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.final,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.final.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FINAL PERIOD GRADE")
        self.assertContains(response, "FINAL GRADE")
        self.assertContains(response, "85")

        period_explanation = self.client.get(
            reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": self.offering.id,
                    "period_id": self.final.id,
                    "student_id": student.id,
                    "grade_type": GradeExplanationService.GRADE_TYPE_PERIOD,
                },
            )
        )
        self.assertEqual(period_explanation.status_code, 200)
        self.assertContains(period_explanation, "FINAL PERIOD GRADE")
        self.assertContains(period_explanation, "Official FINAL PERIOD Grade")

        final_explanation = self.client.get(
            reverse(
                "faculty_portal:grade_explanation",
                kwargs={
                    "offering_id": self.offering.id,
                    "period_id": self.final.id,
                    "student_id": student.id,
                    "grade_type": GradeExplanationService.GRADE_TYPE_FINAL,
                },
            )
        )
        self.assertEqual(final_explanation.status_code, 200)
        self.assertContains(final_explanation, "FINAL PERIOD GRADE")
        self.assertNotContains(final_explanation, "FINAL FINAL GRADE")

    def test_final_period_summary_uses_custom_prior_period_grade_column_labels(self):
        self.prelim.grade_column_label = "PG"
        self.midterm.grade_column_label = "MG"
        self.prefinal.grade_column_label = "PFG"
        self.prelim.save(update_fields=["grade_column_label", "updated_at"])
        self.midterm.save(update_fields=["grade_column_label", "updated_at"])
        self.prefinal.save(update_fields=["grade_column_label", "updated_at"])

        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-CUSTOM-002",
            last_name="Custom",
            first_name="Periods",
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
            final_grade=Decimal("85.00"),
        )
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.final,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.final.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ">PG<", html=False)
        self.assertContains(response, ">MG<", html=False)
        self.assertContains(response, ">PFG<", html=False)
        self.assertContains(response, "FINAL EXAM")
        self.assertContains(response, "FINAL GRADE")
        self.assertContains(response, '<th class="print-grade print-prior-grade">PG</th>', html=False)
        self.assertContains(response, '<th class="print-grade print-prior-grade">MG</th>', html=False)
        self.assertContains(response, '<th class="print-grade print-prior-grade">PFG</th>', html=False)
        self.assertContains(response, '<th class="print-grade print-period-grade">FINAL EXAM</th>', html=False)
        self.assertContains(response, '<th class="print-grade print-final-grade">FINAL GRADE</th>', html=False)

    def test_final_period_summary_uses_fx_grade_without_extra_exam_column(self):
        student = self._create_active_student(
            student_no="2025-FX-001",
            last_name="Transmuted",
            first_name="Exam",
        )
        exam_component = GradingTemplateComponent.objects.create(
            template_period=self.final,
            code="FINAL_EXAM",
            name="Final Exam",
            weight_percentage=100,
            sort_order=1,
            is_exam_component=True,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.final,
            template_component=exam_component,
            title="Final Exam",
            total_score=Decimal("100.00"),
            created_by_user=self.faculty_user,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": "95"}],
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.final,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )
        self._accept_assignment()

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.final.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "TRANSMUTED FINAL EXAM GRADE")
        self.assertContains(response, "FINAL EXAM")
        self.assertContains(response, "FINAL GRADE")
        self.assertContains(response, "98")

    def test_period_summary_shows_periodic_grade_before_submission_by_default(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-DRAFT-001",
            last_name="Draft",
            first_name="Visible",
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
            title="Draft Quiz",
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
            title="Draft Exam",
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

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<th rowspan="4" class="metric-col metric-final">PRELIM GRADE</th>', html=True)
        self.assertContains(response, "Periodic grades are visible for review")
        self.assertContains(response, "93")
        self.assertNotContains(response, "Print Periodic Grades")
        self.assertNotContains(response, "Summary of Periodic Grades")

    def test_period_summary_hides_gradebook_table_until_submitted_when_configured(self):
        self._accept_assignment()
        self._create_active_student(
            student_no="2025-HIDE-001",
            last_name="Hidden",
            first_name="Summary",
        )
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_OFFICIAL_PERIOD_GRADES_AFTER_SUBMISSION_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Submit this gradebook to view or print the official Summary of Grades")
        self.assertContains(response, "Official Prelim grade is hidden until this gradebook is submitted.")
        self.assertNotContains(response, "Print Periodic Grades")
        self.assertNotContains(response, "<th>Student Name</th>", html=False)

    def test_faculty_can_self_reopen_submitted_gradebook_before_deadline(self):
        self._accept_assignment()
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
        submission = GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )
        self.assertContains(response, "Reopen Before Deadline")
        self.assertContains(response, "Justification")
        self.assertContains(response, "Print Periodic Grades")

        response = self.client.post(
            reverse("faculty_portal:period_self_reopen", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
            {"remarks": ""},
            follow=True,
        )
        submission.refresh_from_db()
        self.assertEqual(submission.status, GradeSubmission.Status.SUBMITTED)
        self.assertContains(response, "Reopen justification is required.")

        response = self.client.post(
            reverse("faculty_portal:period_self_reopen", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
            {"remarks": "Need to review before cutoff."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        submission.refresh_from_db()
        self.assertEqual(submission.status, GradeSubmission.Status.REOPENED)
        self.assertContains(response, "gradebook reopened")
        self.assertContains(response, "Submit this gradebook to view or print the official Summary of Grades")
        self.assertNotContains(response, "Print Periodic Grades")

        response = self.client.get(
            reverse("faculty_portal:period_view_history", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )
        self.assertContains(response, "Reopen Attempts")
        self.assertContains(response, "Need to review before cutoff.")

    def test_reopened_gradebook_is_locked_when_admin_moves_deadline_to_past(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-LOCK-001",
            last_name="Locked",
            first_name="Submit",
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            title="Locked Resubmit Activity",
            total_score=100,
            created_by_user=self.faculty_user,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("90")}],
        )
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        exam_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            title="Locked Resubmit Exam",
            total_score=100,
            created_by_user=self.faculty_user,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=exam_activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("88")}],
        )
        lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timedelta(days=1),
            is_locked=False,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        self.client.post(
            reverse("faculty_portal:period_self_reopen", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
            {"remarks": "Need to revise one score."},
        )
        lock.deadline_at = timezone.now() - timedelta(hours=1)
        lock.save(update_fields=["deadline_at", "updated_at"])

        response = self.client.get(reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            GradingPeriodLock.objects.filter(
                tenant=self.tenant,
                campus=self.campus,
                academic_year=self.academic_year,
                term=self.term,
                period_code=self.prelim.code,
                scope_type=GradingPeriodLock.ScopeType.COURSE,
                course_offering=self.offering,
                is_locked=True,
                is_active=True,
            ).exists()
        )
        self.assertContains(response, "Score editing and submission require a new approved reopen request")
        self.assertNotContains(response, "This grading period stays open until submitted")

        response = self.client.get(
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )
        self.assertContains(response, "Finalize and Submit Prelim Grades")
        self.assertContains(response, "Score editing and submission require a new approved reopen request")
        self.assertNotContains(response, "Submit this gradebook to view or print the official Summary of Grades")

        response = self.client.post(
            reverse("faculty_portal:period_submit", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
            {"confirm_submit": "1", "remarks": "Resubmitting after missed reopen deadline."},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        submission = GradeSubmission.objects.get(offering=self.offering, template_period=self.prelim)
        self.assertEqual(submission.status, GradeSubmission.Status.REOPENED)
        self.assertContains(response, "Submit a gradebook reopen request first")

    def test_my_courses_locks_reopened_gradebook_when_admin_moves_deadline_to_past(self):
        self._accept_assignment()
        lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timedelta(days=1),
            is_locked=False,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )
        self.client.force_login(self.faculty_user)
        self.client.post(
            reverse("faculty_portal:period_self_reopen", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
            {"remarks": "Need to revise one score."},
        )
        lock.deadline_at = timezone.now() - timedelta(hours=1)
        lock.save(update_fields=["deadline_at", "updated_at"])

        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            GradingPeriodLock.objects.filter(
                tenant=self.tenant,
                campus=self.campus,
                academic_year=self.academic_year,
                term=self.term,
                period_code=self.prelim.code,
                scope_type=GradingPeriodLock.ScopeType.COURSE,
                course_offering=self.offering,
                is_locked=True,
                is_active=True,
            ).exists()
        )
        self.assertContains(response, "Reopened gradebook locked after deadline")
        self.assertContains(response, "Score editing and submission require a new approved reopen request")
        self.assertNotContains(response, "You may continue encoding and submit as soon as possible")

    def test_locked_reopened_period_pages_use_read_only_messages(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-READONLY-001",
            last_name="Readonly",
            first_name="Student",
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            title="Read Only Activity",
            total_score=100,
            created_by_user=self.faculty_user,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("90")}],
        )
        lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timedelta(days=1),
            is_locked=False,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )
        self.client.force_login(self.faculty_user)
        self.client.post(
            reverse("faculty_portal:period_self_reopen", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
            {"remarks": "Need to revise one score."},
        )
        lock.deadline_at = timezone.now() - timedelta(hours=1)
        lock.save(update_fields=["deadline_at", "updated_at"])

        activities_response = self.client.get(
            reverse("faculty_portal:period_activities", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id})
        )
        self.assertContains(activities_response, "Score editing and submission require a new approved reopen request")
        self.assertNotContains(activities_response, "You may continue encoding and submit as soon as possible")

        scores_response = self.client.get(
            reverse(
                "faculty_portal:activity_scores",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id, "activity_id": activity.id},
            )
        )
        self.assertContains(scores_response, "Score editing and submission require a new approved reopen request")
        self.assertNotContains(scores_response, "Enter a score only for students you want to record now")
        self.assertContains(scores_response, "disabled")

    def test_my_courses_class_size_counts_active_grading_students_only(self):
        self._accept_assignment()
        self._create_active_student(student_no="2025-ACTIVE-001", last_name="Active", first_name="Student")
        dropped_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-DROP-001",
            last_name="Dropped",
            first_name="Student",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=dropped_student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.DRP,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        offering = response.context["grouped_offerings"][0]["offerings"][0]
        self.assertEqual(offering.enrollment_count, 1)

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

    def test_dashboard_active_grading_period_shows_ay_and_campus_name(self):
        self.campus.code = "NCBA-02"
        self.campus.save(update_fields=["code", "updated_at"])
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
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timezone.timedelta(days=2),
            is_locked=False,
        )
        self._accept_assignment()

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stats"]["active_grading_period_rows"][0]["campus_display"], "NCBA-Fairview")
        self.assertContains(response, '<h4 class="faculty-active-period-scope">', html=False)
        self.assertContains(response, '<span class="scope-campus">NCBA-Fairview</span>', html=False)
        self.assertContains(response, '<span class="scope-ay">AY 2025-2026</span>', html=False)
        self.assertContains(response, '<span class="scope-term">1ST</span>', html=False)
        self.assertContains(response, "TeacherMate+ is currently focused on the period(s) below.")
        self.assertContains(response, "logos/teachermate_logo_official.png")
        self.assertNotContains(response, "logos/egp_logo_official.png")
        self.assertContains(response, "font-size: 0.84rem;")
        self.assertContains(response, "line-height: 1.35;")
        self.assertContains(response, "margin-top: 0.18rem;")
        self.assertContains(response, "Prelim (PRELIM)")
        self.assertNotContains(response, "NCBA-02 / 1ST")
        self.assertContains(response, '<h4 class="faculty-deadline-banner-focus">', html=False)
        self.assertContains(response, 'class="deadline-period"', html=False)
        self.assertContains(response, '<span class="deadline-date">', html=False)
        self.assertContains(response, "Grade Encoding Status")
        self.assertContains(response, "Pending Grade Issues")
        self.assertContains(response, "Performance Trends")
        self.assertContains(response, "View Performance")
        self.assertNotContains(response, "Students Needing Follow-up")
        self.assertNotContains(response, "Student Support")
        self.assertContains(response, "faculty-deadline-guide-tag")
        self.assertContains(response, reverse("faculty_portal:my_courses"))
        self.assertContains(response, reverse("faculty_portal:parallel_section_comparison"))

    def test_offering_periods_uses_configured_period_name_for_fx_card(self):
        self.final.code = "FX"
        self.final.name = "Final Exam"
        self.final.save(update_fields=["code", "name", "updated_at"])
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "FINAL EXAM")
        self.assertContains(response, "Code: FX")
        self.assertContains(response, "Finalize all records")

    def test_my_courses_shows_final_clearance_action(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        self._complete_final_clearance_for_offering()

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
        self.assertContains(response, "Print Final Clearance")
        self.assertContains(response, f'href="{reverse("faculty_portal:guide")}#guide-submission"', html=False)

    def test_my_courses_blocks_final_clearance_print_when_courses_incomplete(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:my_courses"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check Final Clearance")
        self.assertContains(response, "Clearance Pending")
        self.assertNotContains(response, "Print Final Clearance")

    def test_offering_periods_shows_final_clearance_action_on_final_period(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        self._complete_final_clearance_for_offering()

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse(
                "faculty_portal:period_final_clearance",
                kwargs={"offering_id": self.offering.id, "period_id": self.final.id},
            ),
        )
        self.assertContains(response, "Print Final Clearance")

    def test_offering_periods_hides_final_clearance_print_when_courses_incomplete(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse("faculty_portal:offering_periods", kwargs={"offering_id": self.offering.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Final Clearance Pending")
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

    def test_non_active_period_route_stays_open_when_overdue_and_unsubmitted(self):
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
            period=TenantTermGradingPeriod.objects.get(tenant=self.tenant, term=self.term, code="MIDTERM"),
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timedelta(hours=2),
            is_locked=False,
        )
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
        self.assertContains(response, "Submission deadline already passed")

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
        self.assertContains(response, "period-quick-nav")
        self.assertContains(response, "attendance-table-shell")
        self.assertContains(response, "attendance-records-table")
        self.assertContains(response, "attendance-session-actions")
        self.assertContains(response, "attendance-records-footer")
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

    def test_faculty_final_clearance_lists_only_accepted_assignments(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        pending_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="PENDING-SEC",
            name="Pending Section",
        )
        pending_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=pending_section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=pending_offering,
            faculty_user=self.faculty_user,
            is_primary=True,
            response_status=FacultyAssignment.ResponseStatus.PENDING,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_final_clearance",
                kwargs={"offering_id": self.offering.id, "period_id": self.final.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.section.code)
        self.assertNotContains(response, pending_section.code)

    def test_faculty_final_clearance_post_generates_pdf_report(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        self._complete_final_clearance_for_offering()

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

    def test_faculty_final_clearance_post_blocks_pdf_when_courses_incomplete(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_final_clearance",
                kwargs={"offering_id": self.offering.id, "period_id": self.final.id},
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Final Clearance can be printed only when all assigned courses")
        self.assertFalse(
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
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        exam_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            title="Prelim Exam",
            total_score=100,
            activity_date=self.term.start_date,
        )
        StudentActivityScore.objects.create(
            activity=exam_activity,
            student=student,
            raw_score="90.00",
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

    def test_period_submit_succeeds_during_active_approved_reopen_window(self):
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-REOPEN-SUBMIT",
            last_name="Reopen",
            first_name="Submit",
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
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
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
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        exam_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            title="Prelim Exam",
            total_score=100,
            activity_date=self.term.start_date,
        )
        StudentActivityScore.objects.create(
            activity=exam_activity,
            student=student,
            raw_score="90.00",
            computed_score="95.00",
            encoded_by_user=self.faculty_user,
        )
        self._accept_assignment()
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.prelim.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(hours=1),
            is_locked=True,
            is_active=True,
        )
        submission = GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.DRAFT,
        )
        GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=submission,
            offering=self.offering,
            template_period=self.prelim,
            requested_by_user=self.faculty_user,
            reviewed_by_user=self.faculty_user,
            reviewed_at=timezone.now(),
            status=GradeSubmissionReopenRequest.Status.APPROVED,
            justification="Approved completion.",
        )

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
        submission.refresh_from_db()
        self.assertEqual(submission.status, GradeSubmission.Status.SUBMITTED)

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
        exam_component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="EXAM")
        exam_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=exam_component,
            title="Prelim Exam",
            total_score=100,
            activity_date=self.term.start_date,
        )
        StudentActivityScore.objects.create(
            activity=exam_activity,
            student=student,
            raw_score="90.00",
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

    def test_submit_blocks_when_template_component_has_no_activity(self):
        student = self._create_active_student(
            student_no="2025-MISS-COMP",
            last_name="Missing",
            first_name="Component",
        )
        class_standing = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=class_standing,
            title="Class Standing Only",
            total_score=100,
            activity_date=self.term.start_date,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("90")}],
        )
        self._accept_assignment()

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=self.prelim,
        )
        self.assertEqual(readiness["students_missing_any_grade"], 0)
        self.assertEqual(readiness["missing_template_bucket_count"], 1)
        self.assertIn("Prelim Exam", [item["label"] for item in readiness["missing_template_items"]])

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
        self.assertContains(response, "Submission blocked: grading template requirements are incomplete.")
        self.assertFalse(GradeSubmission.objects.filter(offering=self.offering, template_period=self.prelim).exists())

    def test_readiness_reports_missing_template_subcomponent_activity(self):
        period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="SUBCHECK",
            name="Subcomponent Check",
            sequence_no=10,
            weight_percentage=Decimal("100.00"),
        )
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="PERF",
            name="Performance",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        recitation = GradingTemplateSubcomponent.objects.create(
            template_component=component,
            code="REC",
            name="Recitation",
            weight_percentage=Decimal("50.00"),
            sort_order=1,
        )
        GradingTemplateSubcomponent.objects.create(
            template_component=component,
            code="LAB",
            name="Laboratory",
            weight_percentage=Decimal("50.00"),
            sort_order=2,
        )
        student = self._create_active_student(
            student_no="2025-MISS-SUB",
            last_name="Missing",
            first_name="Subcomponent",
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            template_component=component,
            template_subcomponent=recitation,
            title="Recitation 1",
            total_score=100,
            activity_date=self.term.start_date,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("90")}],
        )

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["missing_template_bucket_count"], 1)
        self.assertIn("Performance > Laboratory", [item["label"] for item in readiness["missing_template_items"]])

    def test_readiness_does_not_require_attendance_session_for_template_coverage(self):
        period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="ATTCHECK",
            name="Attendance Check",
            sequence_no=12,
            weight_percentage=Decimal("100.00"),
        )
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="ATT",
            name="Attendance",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        GradingTemplateSubcomponent.objects.create(
            template_component=component,
            code="ATT",
            name="Attendance",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_attendance_component=True,
        )
        self._create_active_student(
            student_no="2025-ATT-001",
            last_name="Attendance",
            first_name="Only",
        )

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["expected_template_bucket_count"], 0)
        self.assertEqual(readiness["missing_template_bucket_count"], 0)
        self.assertEqual(readiness["missing_template_items"], [])

    def test_readiness_reports_missing_template_detail_activity(self):
        period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="DETAILCHECK",
            name="Detail Check",
            sequence_no=11,
            weight_percentage=Decimal("100.00"),
        )
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        subcomponent = GradingTemplateSubcomponent.objects.create(
            template_component=component,
            code="WRK",
            name="Written Works",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        quiz = GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="QUIZ",
            name="Quiz",
            weight_percentage=Decimal("50.00"),
            sort_order=1,
        )
        GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="ASSIGN",
            name="Assignment",
            weight_percentage=Decimal("50.00"),
            sort_order=2,
        )
        student = self._create_active_student(
            student_no="2025-MISS-DET",
            last_name="Missing",
            first_name="Detail",
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            template_component=component,
            template_subcomponent=subcomponent,
            template_detail=quiz,
            title="Quiz 1",
            total_score=100,
            activity_date=self.term.start_date,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("90")}],
        )

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["missing_template_bucket_count"], 1)
        self.assertIn(
            "Class Standing > Written Works > Assignment",
            [item["label"] for item in readiness["missing_template_items"]],
        )

    def test_average_participation_output_blocks_without_any_active_activity(self):
        period, _component, _participation_output, _recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        self._create_active_student(
            student_no="2025-PO-EMPTY",
            last_name="Average",
            first_name="Empty",
        )

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["expected_template_bucket_count"], 1)
        self.assertEqual(readiness["missing_template_bucket_count"], 1)
        self.assertEqual(
            [item["label"] for item in readiness["missing_template_items"]],
            ["Class Standing > Participation/Output"],
        )

    def test_average_participation_output_does_not_count_inactive_activity(self):
        period, component, participation_output, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        self._create_active_student(
            student_no="2025-PO-INACTIVE-ACT",
            last_name="Inactive",
            first_name="Activity",
        )
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        activity.is_active = False
        activity.save(update_fields=["is_active", "updated_at"])

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["expected_activity_count"], 0)
        self.assertEqual(readiness["missing_template_bucket_count"], 1)

    def test_average_participation_output_does_not_count_activity_under_inactive_detail(self):
        period, component, participation_output, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        self._create_active_student(
            student_no="2025-PO-INACTIVE-DETAIL",
            last_name="Inactive",
            first_name="Detail",
        )
        self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        recitation.is_active = False
        recitation.save(update_fields=["is_active", "updated_at"])

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["missing_template_bucket_count"], 1)
        self.assertEqual(
            [item["label"] for item in readiness["missing_template_items"]],
            ["Class Standing > Participation/Output"],
        )

    def test_average_participation_output_allows_submission_with_one_active_item(self):
        period, component, participation_output, recitation, _unused_assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        student = self._create_active_student(
            student_no="2025-PO-ONE",
            last_name="Average",
            first_name="One Item",
        )
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("80.00")}],
        )
        self._accept_assignment()

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )
        self.assertEqual(readiness["missing_template_bucket_count"], 0)
        self.assertEqual(readiness["students_missing_any_grade"], 0)

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_submit",
                kwargs={"offering_id": self.offering.id, "period_id": period.id},
            ),
            {"confirm_submit": "1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PO_READINESS grades submitted successfully.")
        submission = GradeSubmission.objects.get(offering=self.offering, template_period=period)
        self.assertEqual(submission.status, GradeSubmission.Status.SUBMITTED)
        period_grade = StudentPeriodGrade.objects.get(
            offering=self.offering,
            template_period=period,
            student=student,
        )
        self.assertEqual(period_grade.period_grade, Decimal("90.00"))

    def test_weighted_participation_output_still_requires_each_detail(self):
        period, component, participation_output, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.WEIGHTED_DETAILS,
            )
        )
        student = self._create_active_student(
            student_no="2025-PO-WEIGHTED",
            last_name="Weighted",
            first_name="Details",
        )
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("80.00")}],
        )

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["missing_template_bucket_count"], 1)
        self.assertIn(
            "Class Standing > Participation/Output > Assignment",
            [item["label"] for item in readiness["missing_template_items"]],
        )

    def test_weighted_participation_output_valid_setup_allows_submission_and_zero_score(self):
        period, component, participation_output, recitation, assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.WEIGHTED_DETAILS,
            )
        )
        student = self._create_active_student(
            student_no="2025-PO-WEIGHTED-READY",
            last_name="Weighted",
            first_name="Ready",
        )
        recitation_activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        assignment_activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=assignment,
            title="Assignment 1",
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=recitation_activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("0.00")}],
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=assignment_activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("80.00")}],
        )
        self._accept_assignment()

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )
        self.assertEqual(readiness["missing_template_bucket_count"], 0)
        self.assertEqual(readiness["students_missing_any_grade"], 0)

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_submit",
                kwargs={"offering_id": self.offering.id, "period_id": period.id},
            ),
            {"confirm_submit": "1"},
            follow=True,
        )

        self.assertContains(response, "PO_READINESS grades submitted successfully.")
        self.assertEqual(
            StudentActivityScore.objects.get(activity=recitation_activity, student=student).raw_score,
            Decimal("0.00"),
        )

    def test_weighted_participation_output_missing_score_still_blocks_submission(self):
        period, component, participation_output, recitation, assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.WEIGHTED_DETAILS,
            )
        )
        student = self._create_active_student(
            student_no="2025-PO-WEIGHTED-BLANK",
            last_name="Weighted",
            first_name="Blank",
        )
        recitation_activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=assignment,
            title="Assignment 1",
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=recitation_activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("80.00")}],
        )

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["missing_template_bucket_count"], 0)
        self.assertEqual(readiness["students_missing_any_grade"], 1)

    def test_weighted_participation_output_invalid_zero_detail_weights_fail_template_validation(self):
        _period, _component, participation_output, recitation, assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.WEIGHTED_DETAILS,
            )
        )
        recitation.weight_percentage = Decimal("0.00")
        recitation.save(update_fields=["weight_percentage", "updated_at"])
        assignment.weight_percentage = Decimal("0.00")
        assignment.save(update_fields=["weight_percentage", "updated_at"])

        errors = GradingTemplateService.validate_publishable(self.template)

        self.assertTrue(
            any(
                "Subcomponent PG_CA_PO has details but total weight is 0" in error
                for error in errors
            )
        )

    def test_average_participation_output_zero_detail_weights_pass_template_validation(self):
        _period, _component, _participation_output, recitation, assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        recitation.weight_percentage = Decimal("0.00")
        recitation.save(update_fields=["weight_percentage", "updated_at"])
        assignment.weight_percentage = Decimal("0.00")
        assignment.save(update_fields=["weight_percentage", "updated_at"])

        errors = GradingTemplateService.validate_publishable(self.template)

        self.assertFalse(
            any(
                "Subcomponent PG_CA_PO has details but total weight is 0" in error
                for error in errors
            )
        )

    def test_average_mode_does_not_loosen_non_participation_output_details(self):
        period, component, subcomponent, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        subcomponent.code = "WRITTEN_WORK"
        subcomponent.name = "Written Work"
        subcomponent.save(update_fields=["code", "name", "updated_at"])
        student = self._create_active_student(
            student_no="2025-NON-PO-AVERAGE",
            last_name="Average",
            first_name="Written Work",
        )
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=subcomponent,
            detail=recitation,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("80.00")}],
        )

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["missing_template_bucket_count"], 1)
        self.assertIn(
            "Class Standing > Written Work > Assignment",
            [item["label"] for item in readiness["missing_template_items"]],
        )

    def test_average_participation_output_encoded_zero_is_not_missing(self):
        period, component, participation_output, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        student = self._create_active_student(
            student_no="2025-PO-ZERO",
            last_name="Encoded",
            first_name="Zero",
        )
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("0.00")}],
        )

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(readiness["missing_template_bucket_count"], 0)
        self.assertEqual(readiness["students_missing_any_grade"], 0)
        score = StudentActivityScore.objects.get(activity=activity, student=student)
        self.assertEqual(score.raw_score, Decimal("0.00"))

    def test_average_participation_output_still_blocks_blank_student_records(self):
        period, component, participation_output, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        encoded_student = self._create_active_student(
            student_no="2025-PO-ENCODED",
            last_name="Encoded",
            first_name="Student",
        )
        self._create_active_student(
            student_no="2025-PO-BLANK",
            last_name="Blank",
            first_name="Student",
        )
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": encoded_student.id, "raw_score": Decimal("80.00")}],
        )
        self._accept_assignment()

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )
        self.assertEqual(readiness["missing_template_bucket_count"], 0)
        self.assertEqual(readiness["students_missing_any_grade"], 1)

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_submit",
                kwargs={"offering_id": self.offering.id, "period_id": period.id},
            ),
            {"confirm_submit": "1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Submission blocked: some ACTIVE students still have blank required grade or attendance records.",
        )
        self.assertFalse(
            GradeSubmission.objects.filter(offering=self.offering, template_period=period).exists()
        )

    def test_partial_recitation_scores_leave_unscored_students_blank_and_block_submission(self):
        period, component, participation_output, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        students = [
            self._create_active_student(
                student_no=f"2025-PARTIAL-{index:03d}",
                last_name=f"Partial{index:03d}",
                first_name="Recitation",
            )
            for index in range(1, 41)
        ]
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
            title="July 6 Recitation",
        )
        activity.activity_date = date(2025, 7, 6)
        activity.save(update_fields=["activity_date", "updated_at"])
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[
                {"student_id": student.id, "raw_score": Decimal("80.00")}
                for student in students[:10]
            ],
        )
        self._accept_assignment()

        self.assertEqual(
            StudentActivityScore.objects.filter(activity=activity, is_active=True).count(),
            10,
        )
        self.assertFalse(
            StudentActivityScore.objects.filter(activity=activity, student=students[10], is_active=True).exists()
        )

        summary = FacultyGradingService.recompute_period_summary(
            user=self.faculty_user,
            offering=self.offering,
            template_period=period,
        )
        self.assertEqual(len(summary["rows"]), 40)
        self.assertEqual(
            StudentPeriodGrade.objects.filter(offering=self.offering, template_period=period).count(),
            40,
        )

        blank_detail = FacultyGradingService.build_period_grade_detail_for_student(
            offering=self.offering,
            template_period=period,
            student_id=students[10].id,
            include_details=True,
        )
        activity_row = (
            blank_detail["component_breakdown"][0]["subcomponents"][0]["details"][0]["activities"][0]
        )
        self.assertIsNone(activity_row["raw_score"])
        self.assertIsNone(activity_row["computed_score"])
        self.assertTrue(activity_row["missing"])

        readiness = GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )
        self.assertEqual(readiness["eligible_student_count"], 40)
        self.assertEqual(readiness["students_with_any_grade"], 10)
        self.assertEqual(readiness["students_missing_any_grade"], 30)
        self.assertEqual(readiness["students_with_complete_records"], 10)
        self.assertEqual(readiness["coverage_percent"], Decimal("25.00"))
        self.assertEqual(readiness["missing_template_bucket_count"], 0)
        self.assertEqual(readiness["expected_activity_count"], 1)
        self.assertEqual(
            sum(row["missing_activity_records"] for row in readiness["missing_students"]),
            30,
        )

        self.client.force_login(self.faculty_user)
        summary_response = self.client.get(
            reverse("faculty_portal:period_summary", args=[self.offering.id, period.id])
        )
        self.assertEqual(summary_response.status_code, 200)
        self.assertContains(
            summary_response,
            "Submission is blocked because some ACTIVE students still do not have a saved score or attendance row for every required item.",
        )
        self.assertEqual(summary_response.context["submit_readiness"]["students_missing_any_grade"], 30)

        submit_response = self.client.post(
            reverse(
                "faculty_portal:period_submit",
                kwargs={"offering_id": self.offering.id, "period_id": period.id},
            ),
            {"confirm_submit": "1"},
            follow=True,
        )

        self.assertEqual(submit_response.status_code, 200)
        self.assertContains(
            submit_response,
            "Submission blocked: some ACTIVE students still have blank required grade or attendance records.",
        )
        self.assertFalse(
            GradeSubmission.objects.filter(offering=self.offering, template_period=period).exists()
        )

    def test_submission_readiness_is_read_only(self):
        period, component, participation_output, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        student = self._create_active_student(
            student_no="2025-PO-READONLY",
            last_name="Readiness",
            first_name="Only",
        )
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("80.00")}],
        )
        before_counts = {
            "activities": GradeActivity.objects.count(),
            "scores": StudentActivityScore.objects.count(),
            "period_grades": StudentPeriodGrade.objects.count(),
            "submissions": GradeSubmission.objects.count(),
        }

        GradingGovernanceService.evaluate_submission_readiness(
            offering=self.offering,
            template_period=period,
        )

        self.assertEqual(
            {
                "activities": GradeActivity.objects.count(),
                "scores": StudentActivityScore.objects.count(),
                "period_grades": StudentPeriodGrade.objects.count(),
                "submissions": GradeSubmission.objects.count(),
            },
            before_counts,
        )

    def test_period_submit_requires_accepted_faculty_assignment(self):
        period, component, participation_output, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        student = self._create_active_student(
            student_no="2025-PO-PENDING",
            last_name="Pending",
            first_name="Assignment",
        )
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("80.00")}],
        )
        self.client.force_login(self.faculty_user)

        response = self.client.post(
            reverse(
                "faculty_portal:period_submit",
                kwargs={"offering_id": self.offering.id, "period_id": period.id},
            ),
            {"confirm_submit": "1"},
            follow=True,
        )

        self.assertContains(response, "Please accept this faculty assignment first")
        self.assertFalse(
            GradeSubmission.objects.filter(offering=self.offering, template_period=period).exists()
        )

    def test_period_submit_blocks_another_faculty_members_class(self):
        period, component, participation_output, recitation, _assignment = (
            self._create_participation_output_readiness_period(
                detail_computation_mode=DetailComputationMode.AVERAGE_ACTIVITIES,
            )
        )
        student = self._create_active_student(
            student_no="2025-PO-OTHER-FAC",
            last_name="Other",
            first_name="Faculty",
        )
        activity = self._create_participation_output_activity(
            period=period,
            component=component,
            participation_output=participation_output,
            detail=recitation,
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=activity,
            score_payload=[{"student_id": student.id, "raw_score": Decimal("80.00")}],
        )
        self._accept_assignment()
        other_faculty = User.objects.create_user(
            username="other_submit_faculty",
            email="other_submit_faculty@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=other_faculty,
            role=Role.objects.get(code="FACULTY"),
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(other_faculty)

        response = self.client.post(
            reverse(
                "faculty_portal:period_submit",
                kwargs={"offering_id": self.offering.id, "period_id": period.id},
            ),
            {"confirm_submit": "1"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            GradeSubmission.objects.filter(offering=self.offering, template_period=period).exists()
        )

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
        self.assertContains(response, "period-quick-nav")
        self.assertContains(response, "activity-table-shell")
        self.assertContains(response, "activities-table")
        self.assertContains(response, "activity-row-actions")
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

    def test_period_activities_grouped_view_is_default_and_uses_template_hierarchy(self):
        self._create_period_activity_grouping_fixture()

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["activity_view_mode"], "grouped")
        groups = response.context["activity_groups"]
        self.assertEqual([group["component"].name for group in groups], ["Class Standing", "Prelim Exam"])
        class_standing_group = groups[0]
        exam_group = groups[1]
        self.assertEqual(class_standing_group["activity_count"], 4)
        self.assertEqual(class_standing_group["encoded_count"], 3)
        self.assertEqual(class_standing_group["expected_count"], 8)
        self.assertEqual(exam_group["activities"][0].title, "PEX")
        self.assertEqual([group["subcomponent"].name for group in class_standing_group["subcomponent_groups"]], ["Quizzes", "Participation/Output"])
        quizzes_group = class_standing_group["subcomponent_groups"][0]
        participation_group = class_standing_group["subcomponent_groups"][1]
        self.assertEqual([activity.title for activity in quizzes_group["activities"]], ["Q1", "Q2"])
        self.assertEqual([group["detail"].name for group in participation_group["detail_groups"]], ["Recitation", "Assignment"])
        self.assertEqual(participation_group["detail_groups"][0]["activities"][0].title, "R1")
        self.assertEqual(participation_group["detail_groups"][1]["activities"][0].title, "A1")
        self.assertContains(response, "4 activities | 3/8 scores encoded")
        self.assertContains(response, "1 activity | 0/2 scores encoded")
        self.assertContains(response, 'data-bs-toggle="collapse"', html=False)
        self.assertContains(response, 'aria-expanded="true"', html=False)
        self.assertContains(response, 'aria-controls="activity-component-', html=False)
        self.assertContains(response, "activity-group-toggle")
        self.assertContains(response, "activity-group-table")
        rendered_subcomponent_names = [
            subcomponent_group["subcomponent"].name
            for group in groups
            for subcomponent_group in group["subcomponent_groups"]
        ]
        rendered_detail_names = [
            detail_group["detail"].name
            for group in groups
            for subcomponent_group in group["subcomponent_groups"]
            for detail_group in subcomponent_group["detail_groups"]
        ]
        self.assertNotIn("Unused Subcomponent", rendered_subcomponent_names)
        self.assertNotIn("Unused Detail", rendered_detail_names)
        self.assertNotContains(response, "<th>Component</th>", html=False)
        self.assertNotContains(response, "<th>Subcomponent</th>", html=False)
        self.assertNotContains(response, "<th>Detail</th>", html=False)

    def test_period_activities_flat_view_keeps_hierarchy_columns_and_legacy_order(self):
        self._create_period_activity_grouping_fixture()

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
            + "?view=flat"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["activity_view_mode"], "flat")
        self.assertContains(response, "<th>Component</th>", html=False)
        self.assertContains(response, "<th>Subcomponent</th>", html=False)
        self.assertContains(response, "<th>Detail</th>", html=False)
        self.assertContains(response, "activity-taxonomy-component-standing")
        self.assertContains(response, "activity-taxonomy-detail-standing")
        self.assertEqual([row.title for row in response.context["activities"][:3]], ["PEX", "A1", "R1"])

    def test_period_activities_invalid_view_falls_back_and_drops_unsafe_next(self):
        self._create_period_activity_grouping_fixture()

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
            + "?view=calendar&next=https://evil.example/bad"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["activity_view_mode"], "grouped")
        self.assertNotContains(response, "https://evil.example/bad")
        self.assertContains(response, "view=flat")

    def test_period_activities_empty_grouped_view_shows_empty_state(self):
        self._accept_assignment()

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["activity_groups"], [])
        self.assertContains(response, "No activities yet.")

    def test_period_activities_grouped_view_preserves_submitted_view_only_state(self):
        self._create_period_activity_grouping_fixture()
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now(),
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This period is already submitted. Editing is disabled.")
        self.assertContains(response, "Encode Scores")
        self.assertNotContains(response, "Save Activity")
        self.assertNotContains(response, "Edit Q1")
        self.assertNotContains(response, "Delete Q1")

    def test_period_activities_exposes_score_entry_method_guidance_data(self):
        self._accept_assignment()
        component = GradingTemplateComponent.objects.get(template_period=self.prelim, code="CS")
        component.score_input_mode = "DIRECT_PERCENTAGE"
        component.save(update_fields=["score_input_mode"])

        self.client.force_login(self.faculty_user)
        response = self.client.get(
            reverse(
                "faculty_portal:period_activities",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Total Items")
        self.assertContains(response, "Required. Scores will be transmuted using the configured Base value rule.")
        component_options = response.context["component_option_data"]
        self.assertEqual(component_options[0]["score_input_mode"], "DIRECT_PERCENTAGE")

    def test_raw_score_entry_method_label_uses_configured_base_wording(self):
        self.assertEqual(
            FacultyGradingService.score_input_mode_label("RAW_BASE50"),
            "Raw Score (Configured Base)",
        )

    def test_activity_scores_shows_quick_jump_links_and_unsaved_warning_copy(self):
        self.assignment.accepted_at = timezone.now()
        self.assignment.accepted_by = self.faculty_user
        self.assignment.response_status = FacultyAssignment.ResponseStatus.ACCEPTED
        self.assignment.save(update_fields=["accepted_at", "accepted_by", "response_status", "updated_at"])
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_QUICK_SCORE_ENCODING_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        self._create_active_student(
            student_no="2025-QENC-001",
            last_name="Quick",
            first_name="Encode",
        )
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
        self.assertContains(response, "period-quick-nav")
        self.assertContains(response, "score-table-shell")
        self.assertContains(response, "activity-scores-table")
        self.assertContains(response, "score-page-header")
        self.assertContains(response, "score-page-footer")
        self.assertContains(response, "You have unsaved encoded scores. If you leave this page, the encoded data will be lost. Continue?")
        self.assertContains(response, 'event.key === "Enter"')
        self.assertContains(response, 'data-quick-score-encoding="true"')
        self.assertContains(response, 'data-score-input="true"')
        self.assertContains(response, "quick-score-unsaved-indicator")
        self.assertContains(response, "Unsaved changes")
        self.assertContains(response, "singleColumnPasteValues")
        self.assertContains(response, "pasteScoresDown")
        self.assertContains(response, 'event.key === "ArrowDown"')
        self.assertContains(response, 'event.key === "ArrowUp"')
        self.assertContains(
            response,
            reverse("faculty_portal:period_summary", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
        )
        self.assertContains(
            response,
            reverse("faculty_portal:period_attendance", kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id}),
        )

    def test_activity_scores_quick_encoding_can_be_disabled_by_feature_flag(self):
        self._accept_assignment()
        SystemSettingService.set(
            FeatureSettingsService.FACULTY_QUICK_SCORE_ENCODING_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=self.prelim.components.first(),
            title="Quiz Standard Entry",
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
        self.assertContains(response, "activity-scores-form")
        self.assertNotContains(response, 'data-quick-score-encoding="true"')
        self.assertNotContains(response, 'data-score-input="true"')
        self.assertNotContains(response, 'id="quick-score-unsaved-indicator"')
        self.assertNotContains(response, "Unsaved changes")

    def test_activity_scores_rejects_score_above_max_without_saving(self):
        self._accept_assignment()
        student = self._create_active_student(
            student_no="2025-MAX-001",
            last_name="Max",
            first_name="Check",
        )
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=self.prelim.components.first(),
            title="Quiz Max",
            total_score=50,
            activity_date=self.term.start_date,
        )

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:activity_scores",
                kwargs={"offering_id": self.offering.id, "period_id": self.prelim.id, "activity_id": activity.id},
            ),
            {f"raw_{student.id}": "51"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Value must be between 0 and 50")
        self.assertFalse(StudentActivityScore.objects.filter(activity=activity, student=student).exists())

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
        self.assertContains(response, 'id="periodSnapshotCollapse" class="collapse show"', html=False)
        self.assertContains(response, 'aria-expanded="true"', html=False)
        self.assertContains(response, "padding-top: 1rem")

    def test_faculty_can_request_remove_student_from_class_when_faculty_allowed_mode_is_enabled(self):
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
            {
                "action": "request_remove_class_list_change",
                "enrollments": [enrollment.id],
                "remarks": "Please remove after registrar verification.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "forwarded to Campus Admin for AIMS verification")
        self.assertContains(response, "PENDING")
        change_request = ClassListChangeRequest.objects.get(offering=self.offering, faculty_requester=self.faculty_user)
        self.assertEqual(change_request.request_type, ClassListChangeRequest.RequestType.REMOVE)
        self.assertEqual(change_request.status, ClassListChangeRequest.Status.PENDING)
        enrollment.refresh_from_db()
        self.assertTrue(enrollment.is_active)

    def test_faculty_can_request_add_student_with_manual_reference_without_student_match(self):
        self._accept_assignment()
        self.client.force_login(self.faculty_user)

        response = self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
            {
                "action": "request_add_class_list_change",
                "student_number": "2025-CLCR-001",
                "student_name": "Manual Add Student",
                "remarks": "Please verify against AIMS.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "forwarded to Campus Admin for AIMS verification")
        change_request = ClassListChangeRequest.objects.get(offering=self.offering, faculty_requester=self.faculty_user)
        self.assertEqual(change_request.request_type, ClassListChangeRequest.RequestType.ADD)
        self.assertEqual(change_request.status, ClassListChangeRequest.Status.PENDING)
        self.assertEqual(change_request.items.count(), 1)
        item = change_request.items.get()
        self.assertEqual(item.reference_student_no, "2025-CLCR-001")
        self.assertEqual(item.reference_student_name, "Manual Add Student")
        self.assertFalse(Enrollment.objects.filter(course_offering=self.offering).exists())

    def test_faculty_class_list_add_dropdown_excludes_students_already_enrolled_in_current_class(self):
        self._accept_assignment()
        enrolled_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-203",
            last_name="Already",
            first_name="Enrolled",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=enrolled_student,
            course_offering=self.offering,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
            is_active=True,
        )
        available_student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-204",
            last_name="Available",
            first_name="Student",
        )

        self.client.force_login(self.faculty_user)
        response = self.client.get(reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}))

        self.assertEqual(response.status_code, 200)
        add_request_form = response.context["add_request_form"]
        student_queryset = add_request_form.fields["student"].queryset
        self.assertNotIn(enrolled_student, student_queryset)
        self.assertIn(available_student, student_queryset)

    def test_faculty_can_remove_pending_class_list_change_request_from_recent_requests(self):
        self._accept_assignment()
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-205",
            last_name="Pending",
            first_name="Removal",
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
        self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
            {
                "action": "request_remove_class_list_change",
                "enrollments": [enrollment.id],
                "remarks": "Please remove after registrar verification.",
            },
            follow=True,
        )
        change_request = ClassListChangeRequest.objects.get(offering=self.offering, faculty_requester=self.faculty_user)

        response = self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
            {
                "action": "cancel_class_list_change_request",
                "request_id": change_request.id,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "pending class list change request was removed")
        change_request.refresh_from_db()
        enrollment.refresh_from_db()
        self.assertEqual(change_request.status, ClassListChangeRequest.Status.CANCELLED)
        self.assertTrue(enrollment.is_active)

    def test_faculty_can_submit_add_request_via_ajax_without_page_refresh(self):
        self._accept_assignment()
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-206",
            last_name="Ajax",
            first_name="Add",
        )

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
            {
                "action": "request_add_class_list_change",
                "student": student.id,
                "remarks": "Please add after registrar verification.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("forwarded to Campus Admin", payload["message"])
        self.assertIn("class-list-change-requests-area", payload["html"])
        self.assertIn("PENDING", payload["html"])
        self.assertEqual(
            ClassListChangeRequest.objects.filter(offering=self.offering, faculty_requester=self.faculty_user).count(),
            1,
        )

    def test_faculty_can_submit_remove_request_via_ajax_without_page_refresh(self):
        self._accept_assignment()
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-207",
            last_name="Ajax",
            first_name="Remove",
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
            {
                "action": "request_remove_class_list_change",
                "enrollments": [enrollment.id],
                "remarks": "Please remove after registrar verification.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("forwarded to Campus Admin", payload["message"])
        self.assertIn("class-list-change-requests-area", payload["html"])
        self.assertIn("PENDING", payload["html"])
        self.assertEqual(
            ClassListChangeRequest.objects.filter(offering=self.offering, faculty_requester=self.faculty_user).count(),
            1,
        )

    def test_faculty_can_cancel_pending_request_via_ajax_without_page_refresh(self):
        self._accept_assignment()
        student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-208",
            last_name="Ajax",
            first_name="Cancel",
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
        self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
            {
                "action": "request_remove_class_list_change",
                "enrollments": [enrollment.id],
                "remarks": "Please remove after registrar verification.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        change_request = ClassListChangeRequest.objects.get(offering=self.offering, faculty_requester=self.faculty_user)

        response = self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
            {
                "action": "cancel_class_list_change_request",
                "request_id": change_request.id,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("removed", payload["message"])
        self.assertIn("class-list-change-requests-area", payload["html"])
        self.assertIn("CANCELLED", payload["html"])
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, ClassListChangeRequest.Status.CANCELLED)

    def test_faculty_cannot_request_class_list_change_for_unassigned_class(self):
        self._accept_assignment()
        self.client.force_login(self.faculty_user)
        other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=Section.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                code="BSIT-1B",
                name="BSIT 1B",
            ),
        )

        response = self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": other_offering.id}),
            {
                "action": "request_add_class_list_change",
                "student_number": "2025-CLCR-404",
                "student_name": "Not Assigned",
            },
        )

        self.assertEqual(response.status_code, 404)

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
        self.assertContains(response, "Request Class List Change")
        self.assertContains(response, "Request Add Student")
        self.assertContains(response, "Request Remove Student")
        self.assertContains(response, "Your request will be forwarded to the Campus Admin assigned to this class campus for AIMS verification.")
        self.assertContains(response, "<option value=\"DRP\">DRP</option>", html=True)
        self.assertContains(response, "<option value=\"INC\">INC</option>", html=True)
        self.assertContains(response, "text-bg-warning text-dark")

    def test_faculty_can_mark_student_drp_through_prefinal_by_default(self):
        self._accept_assignment()
        prefinal_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="PREFINAL",
            name="Pre-Final",
            sequence_no=3,
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=prefinal_period,
            set_by_user=self.faculty_user,
        )
        student = self._create_active_student(
            student_no="2025-DRP-PF",
            last_name="Allowed",
            first_name="Drop",
        )
        enrollment = Enrollment.objects.get(course_offering=self.offering, student=student)

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
            {
                "action": "update_status",
                "enrollment_id": enrollment.id,
                "enrollment_status": Enrollment.Status.DRP,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, Enrollment.Status.DRP)

    def test_faculty_cannot_newly_mark_student_drp_after_prefinal_when_final_is_active(self):
        self._accept_assignment()
        final_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="FINAL",
            name="Final",
            sequence_no=4,
        )
        ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=final_period,
            set_by_user=self.faculty_user,
        )
        student = self._create_active_student(
            student_no="2025-DRP-FN",
            last_name="Blocked",
            first_name="Drop",
        )
        enrollment = Enrollment.objects.get(course_offering=self.offering, student=student)

        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse("faculty_portal:offering_enrollment", kwargs={"offering_id": self.offering.id}),
            {
                "action": "update_status",
                "enrollment_id": enrollment.id,
                "enrollment_status": Enrollment.Status.DRP,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faculty DRP updates are no longer allowed")
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, Enrollment.Status.ACTIVE)

    def test_period_summary_shows_overdue_notice_without_late_completion_action(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timedelta(days=2),
            is_locked=False,
        )
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
        self.assertContains(response, "Submission deadline already passed")
        self.assertNotContains(response, "Request Late Completion Access")
