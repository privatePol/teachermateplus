from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.core import mail
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from PIL import Image
from apps.accounts.models import User
from apps.accounts.models import UserSignatureUsageLog
from apps.accounts.services import UserSignatureService
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment
from apps.faculty_portal.forms import GradeCorrectionRequestForm
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CourseTemplateAssignment,
    FacultyFinalClearanceReport,
    GradeActivity,
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeSubmission,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
    TenantGradingProfile,
)
from apps.grading.notifications import CorrectionNotificationService
from apps.grading.reporting import CorrectionOfficialReportService, FacultyFinalClearanceReportService
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
        self.assertFalse(
            StudentPeriodGrade.objects.filter(
                offering=self.offering,
                template_period=self.period,
                student=self.student2,
            ).exists()
        )
        self.assertFalse(StudentFinalGrade.objects.filter(offering=self.offering, student=self.student2).exists())
        self.assertTrue(
            AuditLog.objects.filter(
                action="UPDATE",
                entity_type="StudentActivityScore",
                entity_id=str(score.id),
                metadata_json__reason="CORRECTION_APPROVAL",
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="RECOMPUTE",
                entity_type="StudentPeriodGrade",
                entity_id=str(period_grade.id),
                metadata_json__reason="CORRECTION_APPROVAL",
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="RECOMPUTE",
                entity_type="StudentFinalGrade",
                entity_id=str(final_grade.id),
                metadata_json__reason="CORRECTION_APPROVAL",
            ).exists()
        )
        self.assertTrue(updated.unlock_window.is_consumed)

    def test_score_write_recomputes_scoped_period_and_final_immediately(self):
        GradeSubmission.objects.filter(offering=self.offering, template_period=self.period).update(
            status=GradeSubmission.Status.REOPENED
        )
        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=self.activity,
            score_payload=[{"student_id": self.student1.id, "raw_score": Decimal("40.00")}],
        )

        score = StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True)
        period_grade = StudentPeriodGrade.objects.get(
            offering=self.offering,
            template_period=self.period,
            student=self.student1,
        )
        final_grade = StudentFinalGrade.objects.get(offering=self.offering, student=self.student1)

        self.assertEqual(score.computed_score, Decimal("90.00"))
        self.assertEqual(period_grade.period_grade, Decimal("90.00"))
        self.assertEqual(final_grade.final_grade, Decimal("90.00"))
        self.assertFalse(
            StudentPeriodGrade.objects.filter(
                offering=self.offering,
                template_period=self.period,
                student=self.student2,
            ).exists()
        )

    def test_exam_component_flag_drives_exam_bucket_without_code_name_dependency(self):
        GradeSubmission.objects.filter(offering=self.offering, template_period=self.period).update(
            status=GradeSubmission.Status.REOPENED
        )
        self.component.weight_percentage = Decimal("60.00")
        self.component.save(update_fields=["weight_percentage", "updated_at"])
        exam_component = GradingTemplateComponent.objects.create(
            template_period=self.period,
            code="ME",
            name="Major Assessment",
            weight_percentage=Decimal("40.00"),
            sort_order=2,
            is_exam_component=True,
        )
        exam_activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=exam_component,
            title="Major Assessment 1",
            total_score=Decimal("50.00"),
            created_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=exam_activity,
            student=self.student1,
            raw_score=Decimal("40.00"),
            computed_score=FacultyGradingService.compute_activity_score(
                raw_score=Decimal("40.00"),
                total_score=Decimal("50.00"),
                base_value=Decimal("50.00"),
            ),
            encoded_by_user=self.faculty_user,
        )

        FacultyGradingService.recompute_period_summary_for_students(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            student_ids=[self.student1.id],
        )

        period_grade = StudentPeriodGrade.objects.get(
            offering=self.offering,
            template_period=self.period,
            student=self.student1,
        )
        self.assertEqual(period_grade.class_standing_grade, Decimal("80.00"))
        self.assertEqual(period_grade.exam_grade, Decimal("90.00"))
        self.assertEqual(period_grade.period_grade, Decimal("84.00"))

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

    def test_official_correction_report_logs_signature_usage_for_requester_and_approver(self):
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_CORRECTION_REPORT_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )

        def _signature_upload(name, color):
            buffer = BytesIO()
            Image.new("RGBA", (180, 60), color).save(buffer, format="PNG")
            return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

        UserSignatureService.store_signature(
            user=self.faculty_user,
            uploaded_file=_signature_upload("faculty-signature.png", (10, 120, 10, 255)),
            actor=self.faculty_user,
        )
        UserSignatureService.store_signature(
            user=self.reviewer_user,
            uploaded_file=_signature_upload("reviewer-signature.png", (120, 10, 10, 255)),
            actor=self.reviewer_user,
        )

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Include stored signatures in the official correction report.",
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
            review_remarks="Approved with signature.",
        )

        pdf_bytes = CorrectionOfficialReportService.build_pdf_bytes(request_obj=closed_request)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertEqual(
            UserSignatureUsageLog.objects.filter(
                document_type=UserSignatureUsageLog.DocumentType.CORRECTION_OFFICIAL_REPORT,
                document_reference=f"CGR-{closed_request.id:06d}",
            ).count(),
            2,
        )
        self.faculty_user.signature_credential.refresh_from_db()
        self.reviewer_user.signature_credential.refresh_from_db()
        self.assertIsNotNone(self.faculty_user.signature_credential.last_used_at)
        self.assertIsNotNone(self.reviewer_user.signature_credential.last_used_at)

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


class FinalGradeFormulaTests(TestCase):
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
            username="faculty_formula",
            email="faculty_formula@example.com",
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
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP-FINAL",
            name="Final Formula Template",
            is_published=True,
            is_active=True,
        )
        self.prelim = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            is_active=True,
        )
        self.midterm = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
            is_active=True,
        )
        self.prefinal = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PREFINAL",
            name="Pre-Final",
            sequence_no=3,
            is_active=True,
        )
        self.final_period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="FINAL",
            name="Final",
            sequence_no=4,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
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
            enrollment_status=Enrollment.Status.ACTIVE,
            is_active=True,
        )

    def _create_period_grade(self, period, value):
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=period,
            student=self.student,
            period_grade=Decimal(value),
            class_standing_grade=Decimal(value),
            exam_grade=Decimal(value),
            computed_by_user=self.faculty_user,
            is_finalized=True,
        )

    def test_default_final_grade_averages_all_active_periods(self):
        self._create_period_grade(self.prelim, "92.00")
        self._create_period_grade(self.midterm, "88.00")

        FacultyGradingService.recompute_final_grades_from_stored_periods(
            user=self.faculty_user,
            offering=self.offering,
            template=self.template,
        )

        final_grade = StudentFinalGrade.objects.get(offering=self.offering, student=self.student)
        self.assertEqual(final_grade.final_grade, Decimal("45.00"))

    def test_weighted_final_grade_uses_profile_configuration(self):
        TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            profile_code="WEIGHTED",
            profile_name="Weighted Formula",
            grading_template=self.template,
            final_grade_formula_mode=TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS,
            final_grade_formula_json={
                "period_weights": [
                    {"period_code": "PRELIM", "weight": "20.00"},
                    {"period_code": "MIDTERM", "weight": "20.00"},
                    {"period_code": "PREFINAL", "weight": "20.00"},
                    {"period_code": "FINAL", "weight": "40.00"},
                ]
            },
            is_default=True,
            is_active=True,
        )
        self._create_period_grade(self.prelim, "92.00")
        self._create_period_grade(self.midterm, "88.00")

        FacultyGradingService.recompute_final_grades_from_stored_periods(
            user=self.faculty_user,
            offering=self.offering,
            template=self.template,
        )

        final_grade = StudentFinalGrade.objects.get(offering=self.offering, student=self.student)
        self.assertEqual(final_grade.final_grade, Decimal("36.00"))

    def test_passing_threshold_falls_back_to_template_threshold(self):
        self.template.passing_grade_threshold = Decimal("80.00")
        self.template.save(update_fields=["passing_grade_threshold"])

        self.assertEqual(
            FacultyGradingService.resolve_passing_threshold(self.offering),
            Decimal("80.00"),
        )

    def test_profile_passing_threshold_overrides_template_threshold(self):
        self.template.passing_grade_threshold = Decimal("80.00")
        self.template.save(update_fields=["passing_grade_threshold"])
        TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            profile_code="THRESHOLD",
            profile_name="Threshold Override",
            grading_template=self.template,
            passing_grade_threshold=Decimal("78.00"),
            is_default=True,
            is_active=True,
        )

        self.assertEqual(
            FacultyGradingService.resolve_passing_threshold(self.offering),
            Decimal("78.00"),
        )


class FacultyFinalClearanceQrTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="FAIRVIEW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLL",
            name="College",
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
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
        )
        self.user = User.objects.create_user(
            username="faculty_clearance_qr",
            email="faculty_clearance_qr@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        self.report_obj = FacultyFinalClearanceReport.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            faculty_user=self.user,
            generated_by_user=self.user,
            reference_no="FCR-TEST-001",
            verification_code="ABCDEF1234567890",
            clearance_status=FacultyFinalClearanceReport.ClearanceStatus.CLEARED,
            total_assigned_courses=1,
            complete_courses=1,
            incomplete_courses=0,
            snapshot_json={"rows": [], "clearance_status": "CLEARED"},
        )

    @override_settings(SITE_URL="https://grades.ncba.edu.ph")
    def test_verification_lookup_value_uses_site_url_when_available(self):
        value = FacultyFinalClearanceReportService.verification_lookup_value(report_obj=self.report_obj)

        self.assertIn("https://grades.ncba.edu.ph/admin-portal/academics/faculty-final-clearance/", value)
        self.assertIn("lookup_reference_no=FCR-TEST-001", value)
        self.assertIn("lookup_verification_code=ABCDEF1234567890", value)

    @override_settings(SITE_URL="")
    def test_verification_lookup_value_falls_back_to_manual_payload(self):
        value = FacultyFinalClearanceReportService.verification_lookup_value(report_obj=self.report_obj)

        self.assertIn("NCBA Faculty Final Clearance Verification", value)
        self.assertIn("Reference No: FCR-TEST-001", value)
        self.assertIn("Verification Code: ABCDEF1234567890", value)

    def test_final_clearance_pdf_logs_signature_usage_when_enabled(self):
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        SystemSettingService.set(
            FeatureSettingsService.USER_SIGNATURES_FINAL_CLEARANCE_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        buffer = BytesIO()
        Image.new("RGBA", (180, 60), (40, 80, 140, 255)).save(buffer, format="PNG")
        UserSignatureService.store_signature(
            user=self.user,
            uploaded_file=SimpleUploadedFile("clearance-signature.png", buffer.getvalue(), content_type="image/png"),
            actor=self.user,
        )

        pdf_bytes = FacultyFinalClearanceReportService.build_pdf_bytes(report_obj=self.report_obj)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertEqual(
            UserSignatureUsageLog.objects.filter(
                document_type=UserSignatureUsageLog.DocumentType.FINAL_CLEARANCE,
                document_reference="FCR-TEST-001",
            ).count(),
            1,
        )


class CompletionGraceWindowTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="TEN-GRACE", name="Tenant Grace")
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
            username="faculty_grace",
            email="faculty_grace@example.com",
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
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-10001",
            last_name="Arcilla",
            first_name="Janica",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            course_offering=self.offering,
            student=self.student,
            enrollment_status=Enrollment.Status.ACTIVE,
            encoded_via_portal=Enrollment.SourcePortal.ADMIN,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TPL-GRACE",
            name="Grace Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
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
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )
        now = timezone.now()
        self.lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            is_locked=False,
            deadline_at=now - timedelta(hours=1),
        )

    def test_assert_encoding_allowed_when_period_is_overdue_but_unsubmitted(self):
        GradingGovernanceService.assert_encoding_allowed(
            offering=self.offering,
            template_period=self.period,
        )

    def test_auto_lock_does_not_lock_overdue_unsubmitted_period(self):
        result = GradingGovernanceService.auto_lock_due_periods(at=timezone.now())

        self.lock.refresh_from_db()
        self.assertEqual(result["count"], 0)
        self.assertFalse(self.lock.is_locked)
