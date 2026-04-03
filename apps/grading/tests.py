from datetime import date
from decimal import Decimal

from django.core import mail
from django.test import TestCase

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.enrollment.models import Enrollment
from apps.faculty_portal.forms import GradeCorrectionRequestForm
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CourseTemplateAssignment,
    GradeActivity,
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeSubmission,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
)
from apps.grading.notifications import CorrectionNotificationService
from apps.grading.reporting import CorrectionOfficialReportService
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.rbac.models import Role, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService


class CorrectionWorkflowTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="TEN", name="Tenant")
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
            name="BS Computer Science",
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
            username="faculty1",
            email="faculty1@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            is_primary=True,
        )
        self.reviewer_user = User.objects.create_user(
            username="reviewer1",
            email="reviewer1@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        self.reviewer_role = Role.objects.create(code="DEAN", name="Dean")
        UserRole.objects.create(
            user=self.reviewer_user,
            role=self.reviewer_role,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.cao_user = User.objects.create_user(
            username="cao1",
            email="cao1@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
        )
        self.cao_role = Role.objects.create(code="CAO", name="Chief Academic Officer")
        UserRole.objects.create(
            user=self.cao_user,
            role=self.cao_role,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.super_admin_user = User.objects.create_user(
            username="superadmin1",
            email="superadmin1@example.com",
            password="testpass123",
            default_tenant=self.tenant,
        )
        self.super_admin_role = Role.objects.create(code="SUPER_ADMIN", name="Super Admin")
        UserRole.objects.create(
            user=self.super_admin_user,
            role=self.super_admin_role,
            tenant=self.tenant,
            campus=None,
        )
        self.route_rule = CorrectionApprovalRouteRule.objects.create(
            tenant=self.tenant,
            faculty_department=self.department,
            route_mode=CorrectionApprovalRouteRule.RouteMode.DIRECT_TO_FINAL,
            step1_role=self.reviewer_role,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TEMP1",
            name="Default Template",
            is_published=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )
        self.component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
        )
        self.student1 = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-001",
            last_name="Alpha",
            first_name="Ada",
        )
        self.student2 = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-002",
            last_name="Bravo",
            first_name="Ben",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.student1,
            course_offering=self.offering,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.FACULTY,
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            student=self.student2,
            course_offering=self.offering,
            encoded_by_user=self.faculty_user,
            encoded_via_portal=Enrollment.SourcePortal.FACULTY,
        )
        self.activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            title="Quiz 1",
            total_score=Decimal("50.00"),
            created_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=self.activity,
            student=self.student1,
            raw_score=Decimal("30.00"),
            computed_score=FacultyGradingService.compute_activity_score(
                raw_score=Decimal("30.00"),
                total_score=Decimal("50.00"),
                base_value=Decimal("50.00"),
            ),
            encoded_by_user=self.faculty_user,
        )
        GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.SUBMITTED,
            submitted_by_user=self.faculty_user,
            submission_snapshot_json={},
            template_snapshot_json={},
        )

    def test_correction_request_form_accepts_multiple_students_and_items(self):
        form = GradeCorrectionRequestForm(
            data={
                "students": [self.student1.id, self.student2.id],
                "grade_activities": [self.activity.id],
                "correction_payload": (
                    f'[{{"student_id":"{self.student1.id}","grade_activity_id":"{self.activity.id}","new_value":"35"}},'
                    f'{{"student_id":"{self.student2.id}","grade_activity_id":"{self.activity.id}","new_value":"40"}}]'
                ),
                "justification": "Requested correction for quiz scores.",
            },
            student_queryset=Student.objects.filter(id__in=[self.student1.id, self.student2.id]),
            activity_queryset=GradeActivity.objects.filter(id=self.activity.id),
            score_lookup={
                (self.student1.id, self.activity.id): "30",
                (self.student2.id, self.activity.id): "",
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(list(form.fields["grade_activities"].widget.choices)), 1)
        self.assertEqual(len(form.cleaned_data["items"]), 2)
        self.assertEqual(form.cleaned_data["items"][0]["old_value"], "30")
        self.assertEqual(form.cleaned_data["items"][0]["new_value"], "35")

    def test_create_correction_request_normalizes_old_values_from_gradebook(self):
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Please correct two scores.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "36",
                },
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student2.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "42",
                },
            ],
        )

        items = list(correction.items.order_by("student_id"))
        self.assertEqual(correction.status, GradeCorrectionRequest.Status.PENDING)
        self.assertEqual(items[0].old_value, "30")
        self.assertEqual(items[0].new_value, "36")
        self.assertEqual(items[1].old_value, None)
        self.assertEqual(items[1].new_value, "42")
        self.assertEqual(correction.approval_route_id, self.route_rule.id)

    def test_create_correction_request_uses_only_active_route_when_department_not_set(self):
        self.faculty_user.default_department = None
        self.faculty_user.save(update_fields=["default_department", "updated_at"])
        custom_role = Role.objects.create(code="NCBA_CAO", name="Chief Academic Officer")
        self.route_rule.step1_role = custom_role
        self.route_rule.save(update_fields=["step1_role", "updated_at"])

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Route should use configured tenant route.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "40",
                }
            ],
        )

        pending_step = correction.approval_steps.order_by("step_order").first()
        self.assertEqual(correction.approval_route_id, self.route_rule.id)
        self.assertEqual(pending_step.approver_role.code, "NCBA_CAO")

    def test_correction_request_form_rejects_corrected_value_above_activity_total(self):
        form = GradeCorrectionRequestForm(
            data={
                "students": [self.student1.id],
                "grade_activities": [self.activity.id],
                "correction_payload": (
                    f'[{{"student_id":"{self.student1.id}","grade_activity_id":"{self.activity.id}","new_value":"51"}}]'
                ),
                "justification": "Invalid correction value.",
            },
            student_queryset=Student.objects.filter(id=self.student1.id),
            activity_queryset=GradeActivity.objects.filter(id=self.activity.id),
            score_lookup={(self.student1.id, self.activity.id): "30"},
        )

        self.assertFalse(form.is_valid())
        self.assertIn("between 0 and 50", str(form.errors))

    def test_super_admin_role_can_review_even_if_step_role_differs(self):
        alt_role = Role.objects.create(code="FINAL_APPROVER", name="Final Approver")
        self.route_rule.step1_role = alt_role
        self.route_rule.save(update_fields=["step1_role", "updated_at"])
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Super admin should be allowed to review.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "35",
                }
            ],
        )

        can_review, pending_step, reason = GradingGovernanceService.can_user_review_correction_request(
            request_obj=correction,
            user=self.super_admin_user,
        )
        self.assertTrue(can_review)
        self.assertIsNotNone(pending_step)
        self.assertIsNone(reason)

    def test_final_approval_auto_applies_score_corrections_and_closes_request(self):
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Correct posted quiz scores.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "45",
                }
            ],
        )

        updated = GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=self.reviewer_user,
            approved=True,
            review_remarks="Approved",
        )

        score = StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True)
        period_grade = StudentPeriodGrade.objects.get(
            offering=self.offering,
            template_period=self.period,
            student=self.student1,
        )
        final_grade = StudentFinalGrade.objects.get(offering=self.offering, student=self.student1)

        self.assertEqual(updated.status, GradeCorrectionRequest.Status.CLOSED)
        self.assertEqual(score.raw_score, Decimal("45.00"))
        self.assertEqual(score.encoded_by_user_id, self.faculty_user.id)
        self.assertEqual(score.computed_score, Decimal("95.00"))
        self.assertEqual(period_grade.period_grade, Decimal("95.00"))
        self.assertTrue(period_grade.is_finalized)
        self.assertEqual(final_grade.final_grade, Decimal("95.00"))
        self.assertTrue(final_grade.is_submitted)
        self.assertTrue(updated.unlock_window.is_consumed)

    def test_official_correction_report_builds_pdf_for_closed_request(self):
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Registrar-ready corrected quiz score.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "45",
                }
            ],
        )
        closed_request = GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=self.reviewer_user,
            approved=True,
            review_remarks="Approved for posting.",
        )

        report_data = CorrectionOfficialReportService.build_report_data(request_obj=closed_request)
        pdf_bytes = CorrectionOfficialReportService.build_pdf_bytes(request_obj=closed_request)

        self.assertEqual(report_data["request_obj"].campus.name, "Main Campus")
        self.assertEqual(report_data["official_grade_label"], "PG")
        self.assertEqual(report_data["official_grade_rows"][0][0], self.student1.student_no)
        self.assertEqual(report_data["official_grade_rows"][0][2], "80")
        self.assertEqual(report_data["official_grade_rows"][0][3], "95")
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertGreater(len(pdf_bytes), 1000)

    def test_correction_submission_notification_emails_configured_roles(self):
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES_KEY,
            ["CAO", "DEAN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
        )
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Notify approvers that a petition is waiting.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "35",
                }
            ],
        )

        result = CorrectionNotificationService.send_correction_submission_approval_notifications(
            request_obj=correction
        )

        self.assertEqual(result["attempted"], 2)
        self.assertEqual(result["sent"], 2)
        self.assertCountEqual(result["recipients"], ["cao1@example.com", "reviewer1@example.com"])
        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertEqual(
                message.subject,
                "NCBA-EDUGRADESPRO: Petition for Correction of Grades Awaiting Your Approval",
            )
            self.assertEqual(len(message.alternatives), 1)
            html_body = message.alternatives[0].content
            self.assertIn("NATIONAL COLLEGE OF BUSINESS AND ARTS", html_body)
            self.assertIn("Approval Notification", html_body)
            self.assertIn("Petitioner:", html_body)

    def test_correction_submission_notification_supports_tenant_wide_role_assignment(self):
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ROLE_CODES_KEY,
            ["SUPER_ADMIN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
        )
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Notify tenant-wide approver.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "35",
                }
            ],
        )

        result = CorrectionNotificationService.send_correction_submission_approval_notifications(
            request_obj=correction
        )

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["recipients"], ["superadmin1@example.com"])

    def test_registrar_official_report_email_sends_pdf_attachment(self):
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_REGISTRAR_AUTO_EMAIL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_REGISTRAR_AUTO_EMAIL_ROLE_CODES_KEY,
            ["DEAN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
        )
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_REGISTRAR_CAMPUS_RECIPIENTS_KEY,
            {str(self.campus.id): ["registrar@example.com"]},
            tenant_id=self.tenant.id,
            value_type="JSON",
        )
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Registrar email should include official PDF.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "45",
                }
            ],
        )
        closed_request = GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=self.reviewer_user,
            approved=True,
            review_remarks="Approved for registrar posting.",
        )

        result = CorrectionNotificationService.send_registrar_official_report_email(
            request_obj=closed_request,
            trigger_role_code="DEAN",
        )

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["recipients"], ["registrar@example.com"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            "NCBA-EDUGRADESPRO: Approved Petition for Correction of Grades for Registrar Reference",
        )
        self.assertEqual(mail.outbox[0].to, ["registrar@example.com"])
        pdf_filenames = [
            item[0]
            for item in mail.outbox[0].attachments
            if isinstance(item, tuple) and len(item) >= 3 and item[2] == "application/pdf"
        ]
        self.assertEqual(len(pdf_filenames), 1)
        self.assertTrue(pdf_filenames[0].endswith(".pdf"))
