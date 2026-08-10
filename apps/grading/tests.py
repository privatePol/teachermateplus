from datetime import date, timedelta
from decimal import Decimal
from importlib import import_module
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.exceptions import InvalidTag
from django.conf import settings
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from PIL import Image
from reportlab.platypus import Paragraph as ReportLabParagraph
from apps.accounts.models import User
from apps.accounts.models import UserSignatureUsageLog
from apps.accounts.services import UserSignatureService
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.academics.services import AcademicGovernanceService
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment
from apps.faculty_portal.forms import GradeCorrectionRequestForm
from apps.grading.models import (
    CorrectionApprovalRouteRule,
    CorrectionApprovalRouteStep,
    CorrectionPetitionWindowPolicy,
    CourseBaseValueOverride,
    CourseTemplateAssignment,
    DetailComputationMode,
    FacultyFinalClearanceReport,
    GradeActivity,
    GradeCorrectionApprovalAuthoritySnapshot,
    GradeCorrectionApprovalStep,
    GradeCorrectionRequest,
    GradeCorrectionRequestItem,
    GradeCorrectionUnlockWindow,
    GradeEncodingControl,
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplateSubcomponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentFinalGrade,
    StudentPeriodGrade,
    TenantGradingProfile,
)
from apps.grading.explanations import GradeExplanationService
from apps.grading.notifications import CorrectionNotificationService
from apps.grading.reporting import CorrectionOfficialReportService, FacultyFinalClearanceReportService
from apps.grading.services import FacultyGradingService, GradeEncodingAccessService, GradingGovernanceService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
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
        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        UserRole.objects.create(
            user=self.faculty_user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
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
        self.quiz_subcomponent = GradingTemplateSubcomponent.objects.create(
            template_component=self.component,
            code="PRELIM-QUIZZES",
            name="Quizzes",
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
            template_subcomponent=self.quiz_subcomponent,
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
        self.correction_lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.offering,
            is_locked=True,
            deadline_at=timezone.now() - timedelta(hours=1),
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
            files={
                "attachment": SimpleUploadedFile(
                    "quiz-evidence.pdf", b"%PDF-1.4\nquiz evidence", content_type="application/pdf"
                )
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

    def _create_correction_activity(self, *, code, title, is_exam_component=False):
        component = self.component
        if is_exam_component:
            component = GradingTemplateComponent.objects.create(
                template_period=self.period,
                code=code,
                name=title,
                weight_percentage=Decimal("100.00"),
                sort_order=2,
                is_exam_component=True,
            )
        return GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            template_component=component,
            title=title,
            total_score=Decimal("50.00"),
            created_by_user=self.faculty_user,
        )

    def _correction_request_form(self, *, activities, attachment=None):
        activity_ids = [activity.id for activity in activities]
        payload = ",".join(
            (
                f'{{"student_id":"{self.student1.id}","grade_activity_id":"{activity.id}",'
                '"new_value":"35"}'
            )
            for activity in activities
        )
        return GradeCorrectionRequestForm(
            data={
                "students": [self.student1.id],
                "grade_activities": activity_ids,
                "correction_payload": f"[{payload}]",
                "justification": "Correction evidence is attached when required.",
            },
            files={"attachment": attachment} if attachment else None,
            student_queryset=Student.objects.filter(id=self.student1.id),
            activity_queryset=GradeActivity.objects.filter(id__in=activity_ids),
            score_lookup={(self.student1.id, activity.id): "30" for activity in activities},
        )

    def _prepare_correction_filing_page(self):
        self._grant_faculty_correction_access()
        self._set_correction_lifecycle(deadline_at=timezone.now() - timedelta(hours=1), is_locked=True)
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
            is_active=True,
        )
        self.client.force_login(self.faculty_user)
        return reverse("faculty_portal:period_corrections", args=[self.offering.id, self.period.id])

    def test_prelim_exam_correction_requires_attachment(self):
        form = self._correction_request_form(
            activities=[self._create_correction_activity(code="PRE_EXAM", title="Prelim Exam", is_exam_component=True)]
        )

        self.assertFalse(form.is_valid())
        self.assertIn("attachment is required", str(form.errors))

    def test_midterm_exam_correction_requires_attachment(self):
        form = self._correction_request_form(
            activities=[self._create_correction_activity(code="MID_EXAM", title="Midterm Exam", is_exam_component=True)]
        )

        self.assertFalse(form.is_valid())
        self.assertIn("attachment is required", str(form.errors))

    def test_final_exam_correction_requires_attachment(self):
        form = self._correction_request_form(
            activities=[self._create_correction_activity(code="FINAL_EXAM", title="Final Exam", is_exam_component=True)]
        )

        self.assertFalse(form.is_valid())
        self.assertIn("attachment is required", str(form.errors))

    def test_exam_correction_accepts_valid_attachment(self):
        form = self._correction_request_form(
            activities=[self._create_correction_activity(code="EXAM_EVIDENCE", title="Prelim Exam", is_exam_component=True)],
            attachment=SimpleUploadedFile("evidence.pdf", b"%PDF-1.4\nexam evidence", content_type="application/pdf"),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["attachment_validation"].content_type, "application/pdf")

    def test_quiz_only_correction_requires_attachment(self):
        form = self._correction_request_form(activities=[self.activity])

        self.assertFalse(form.is_valid())
        self.assertIn("quiz or examination score", str(form.errors))

    def test_quiz_only_correction_accepts_valid_attachment(self):
        form = self._correction_request_form(
            activities=[self.activity],
            attachment=SimpleUploadedFile("quiz-evidence.pdf", b"%PDF-1.4\nquiz evidence", content_type="application/pdf"),
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["attachment_validation"].content_type, "application/pdf")

    def test_activity_only_correction_does_not_require_attachment(self):
        activity = self._create_correction_activity(code="ACTIVITY", title="Activity 1")
        form = self._correction_request_form(activities=[activity])

        self.assertTrue(form.is_valid(), form.errors)

    def test_mixed_exam_and_non_exam_correction_requires_attachment(self):
        exam_activity = self._create_correction_activity(code="MIXED_EXAM", title="Midterm Exam", is_exam_component=True)
        non_exam_activity = self._create_correction_activity(code="ACTIVITY", title="Activity 1")
        form = self._correction_request_form(activities=[non_exam_activity, exam_activity])

        self.assertFalse(form.is_valid())
        self.assertIn("attachment is required", str(form.errors))

    def test_mixed_quiz_and_non_exam_correction_requires_attachment(self):
        non_exam_activity = self._create_correction_activity(code="ACTIVITY", title="Activity 1")
        form = self._correction_request_form(activities=[self.activity, non_exam_activity])

        self.assertFalse(form.is_valid())
        self.assertIn("attachment is required", str(form.errors))

    def test_correction_attachment_security_validation_is_preserved(self):
        form = self._correction_request_form(
            activities=[self._create_correction_activity(code="SECURITY_EXAM", title="Final Exam", is_exam_component=True)],
            attachment=SimpleUploadedFile("evidence.txt", b"not an approved evidence file", content_type="text/plain"),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("must be PDF, PNG, JPG, or JPEG", str(form.errors))

    def test_invalid_correction_post_renders_focusable_validation_summary(self):
        url = self._prepare_correction_filing_page()

        response = self.client.post(
            url,
            {
                "students": [self.student1.id],
                "grade_activities": [self.activity.id],
                "correction_payload": (
                    f'[{{"student_id":"{self.student1.id}","grade_activity_id":"{self.activity.id}","new_value":"35"}}]'
                ),
                "justification": "Quiz correction without evidence.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["correction_activities"][0]["is_quiz_activity"])
        self.assertContains(response, 'id="correction-validation-summary"')
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'tabindex="-1"')
        self.assertContains(response, "An attachment is required when requesting a correction to a quiz or examination score.")
        self.assertContains(response, "scrollIntoView")
        self.assertContains(response, "validationSummary.focus")
        self.assertContains(response, "Attachment (required for quiz and examination corrections)")

    def test_successful_correction_page_does_not_render_validation_summary(self):
        non_exam_activity = self._create_correction_activity(code="ACTIVITY", title="Activity 1")
        url = self._prepare_correction_filing_page()

        response = self.client.post(
            url,
            {
                "students": [self.student1.id],
                "grade_activities": [non_exam_activity.id],
                "correction_payload": (
                    f'[{{"student_id":"{self.student1.id}","grade_activity_id":"{non_exam_activity.id}","new_value":"35"}}]'
                ),
                "justification": "Activity correction without attachment.",
            },
        )

        self.assertEqual(response.status_code, 302)
        follow_up = self.client.get(url)
        self.assertNotContains(follow_up, 'id="correction-validation-summary"')

    def _send_step_approval_notification(self, *, role, user, approver_label):
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=1,
            approver_role=role,
            approver_label=approver_label,
            requires_same_department=role.code == "AREA_CHAIR",
        )
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Notify the configured approval step.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "35",
                }
            ],
        )
        pending_step = GradingGovernanceService.get_pending_correction_step(request_obj=correction)
        result = CorrectionNotificationService.send_correction_step_approval_notifications(
            request_obj=correction,
            step=pending_step,
        )
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["recipients"], [user.email])
        return mail.outbox[0], correction

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_area_chair_step_notification_uses_standard_card_email(self):
        area_role = Role.objects.create(code="AREA_CHAIR", name="Area Chairman")
        area_user = User.objects.create_user(
            username="area-email",
            email="area-email@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        UserRole.objects.create(
            user=area_user,
            role=area_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

        message, _correction = self._send_step_approval_notification(
            role=area_role,
            user=area_user,
            approver_label="Area Chairman",
        )

        self.assertEqual(len(message.alternatives), 1)
        self.assertIn('alt="NCBA"', message.alternatives[0].content)
        self.assertIn("Awaiting Area Chairman Review", message.alternatives[0].content)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_college_dean_step_notification_uses_standard_card_email(self):
        message, _correction = self._send_step_approval_notification(
            role=self.reviewer_role,
            user=self.reviewer_user,
            approver_label="College Dean",
        )

        self.assertEqual(len(message.alternatives), 1)
        self.assertIn("Awaiting College Dean Review", message.alternatives[0].content)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_cao_step_notification_uses_standard_card_email(self):
        message, _correction = self._send_step_approval_notification(
            role=self.cao_role,
            user=self.cao_user,
            approver_label="Chief Academic Officer",
        )

        self.assertEqual(len(message.alternatives), 1)
        self.assertIn("Awaiting Chief Academic Officer Review", message.alternatives[0].content)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_step_notification_card_includes_required_correction_request_details(self):
        message, correction = self._send_step_approval_notification(
            role=self.reviewer_role,
            user=self.reviewer_user,
            approver_label="College Dean",
        )
        html_body = message.alternatives[0].content

        self.assertIn(f"CGR-{correction.id:06d}", html_body)
        self.assertIn("Awaiting College Dean Review", html_body)
        self.assertIn(self.faculty_user.full_name or self.faculty_user.username, html_body)
        self.assertIn(self.course.title, html_body)
        self.assertIn(self.section.name, html_body)
        self.assertIn(self.period.name, html_body)
        self.assertIn(self.campus.name, html_body)
        self.assertIn("College Dean", html_body)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_step_notification_card_keeps_ncba_confidentiality_footer(self):
        message, _correction = self._send_step_approval_notification(
            role=self.reviewer_role,
            user=self.reviewer_user,
            approver_label="College Dean",
        )

        self.assertIn("NCBA confidentiality notice", message.alternatives[0].content)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_step_notification_keeps_plain_text_fallback(self):
        message, correction = self._send_step_approval_notification(
            role=self.reviewer_role,
            user=self.reviewer_user,
            approver_label="College Dean",
        )

        self.assertIn(f"CGR-{correction.id:06d}", message.body)
        self.assertIn("Awaiting College Dean Review", message.body)
        self.assertIn("NCBA confidentiality notice", message.body)

    def test_on_behalf_correction_can_be_created_for_inactive_faculty(self):
        self.faculty_user.is_active = False
        self.faculty_user.save(update_fields=["is_active"])

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            initiated_by_user=self.reviewer_user,
            request_source=GradeCorrectionRequest.RequestSource.ADMIN_ON_BEHALF,
            on_behalf_reason="Original faculty is no longer connected.",
            offering=self.offering,
            template_period=self.period,
            justification="Correct submitted quiz score for former faculty.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "old_value": "30",
                    "new_value": "35",
                }
            ],
        )

        self.assertEqual(correction.requested_by_user, self.faculty_user)
        self.assertEqual(correction.initiated_by_user, self.reviewer_user)
        self.assertEqual(correction.request_source, GradeCorrectionRequest.RequestSource.ADMIN_ON_BEHALF)
        self.assertEqual(correction.faculty_department, self.department)
        self.assertEqual(correction.approval_route, self.route_rule)

    def test_on_behalf_initiator_cannot_review_same_petition(self):
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            initiated_by_user=self.reviewer_user,
            request_source=GradeCorrectionRequest.RequestSource.ADMIN_ON_BEHALF,
            on_behalf_reason="Original faculty is unavailable.",
            offering=self.offering,
            template_period=self.period,
            justification="Correct submitted quiz score for former faculty.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "old_value": "30",
                    "new_value": "35",
                }
            ],
        )

        can_review, _step, reason = GradingGovernanceService.can_user_review_correction_request(
            request_obj=correction,
            user=self.reviewer_user,
        )
        super_can_review, _super_step, _super_reason = GradingGovernanceService.can_user_review_correction_request(
            request_obj=correction,
            user=self.super_admin_user,
        )

        self.assertFalse(can_review)
        self.assertIn("initiated an on-behalf correction petition", reason)
        self.assertTrue(super_can_review)

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

    def test_correction_route_ignores_missing_default_department_when_faculty_role_is_scoped(self):
        self.faculty_user.default_department = None
        self.faculty_user.save(update_fields=["default_department", "updated_at"])
        custom_role = Role.objects.create(code="NCBA_CAO", name="Chief Academic Officer")
        self.route_rule.step1_role = custom_role
        self.route_rule.save(update_fields=["step1_role", "updated_at"])

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Faculty role scope should govern without a user default department.",
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

    def test_correction_route_falls_back_to_parent_department_rule(self):
        parent_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.department.parent = parent_department
        self.department.save(update_fields=["parent", "updated_at"])
        self.route_rule.faculty_department = parent_department
        self.route_rule.save(update_fields=["faculty_department", "updated_at"])

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Parent route should cover child department.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "40",
                }
            ],
        )

        self.assertEqual(correction.faculty_department_id, self.department.id)
        self.assertEqual(correction.approval_route_id, self.route_rule.id)

    def test_correction_route_ignores_same_code_parent_department_from_other_campus(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other Campus")
        other_parent_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="COLLEGE",
            name="Other Campus College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.route_rule.faculty_department = other_parent_department
        self.route_rule.save(update_fields=["faculty_department", "updated_at"])
        default_role = Role.objects.create(code="TENANT_DEFAULT_APPROVER", name="Tenant Default Approver")
        default_route = CorrectionApprovalRouteRule.objects.create(
            tenant=self.tenant,
            faculty_department=None,
            route_mode=CorrectionApprovalRouteRule.RouteMode.DIRECT_TO_FINAL,
            step1_role=default_role,
        )

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Other campus parent must not govern this faculty department.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "40",
                }
            ],
        )

        self.assertEqual(correction.approval_route_id, default_route.id)
        self.assertNotEqual(correction.approval_route_id, self.route_rule.id)

    def test_same_department_correction_review_allows_parent_department_approver(self):
        parent_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.department.parent = parent_department
        self.department.save(update_fields=["parent", "updated_at"])
        self.reviewer_user.default_department = parent_department
        self.reviewer_user.save(update_fields=["default_department", "updated_at"])
        self.route_rule.faculty_department = parent_department
        self.route_rule.step1_requires_same_department = True
        self.route_rule.save(update_fields=["faculty_department", "step1_requires_same_department", "updated_at"])

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Parent reviewer should cover child faculty department.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "40",
                }
            ],
        )

        can_review, pending_step, reason = GradingGovernanceService.can_user_review_correction_request(
            request_obj=correction,
            user=self.reviewer_user,
        )

        self.assertTrue(can_review)
        self.assertIsNotNone(pending_step)
        self.assertIsNone(reason)

    def test_department_scoped_reviewer_role_must_cover_correction_department(self):
        parent_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BA",
            name="Business Administration",
        )
        self.department.parent = parent_department
        self.department.save(update_fields=["parent", "updated_at"])
        self.reviewer_user.default_department = parent_department
        self.reviewer_user.save(update_fields=["default_department", "updated_at"])
        UserRole.objects.filter(user=self.reviewer_user, role=self.reviewer_role).update(department=other_department)
        self.route_rule.step1_requires_same_department = True
        self.route_rule.save(update_fields=["step1_requires_same_department", "updated_at"])

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Reviewer role department should control approval scope.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "40",
                }
            ],
        )

        can_review, pending_step, reason = GradingGovernanceService.can_user_review_correction_request(
            request_obj=correction,
            user=self.reviewer_user,
        )

        self.assertFalse(can_review)
        self.assertIsNotNone(pending_step)
        self.assertIn("Only users assigned to approver role", reason)

    def test_three_step_correction_route_applies_only_after_cao_final_approval(self):
        area_role = Role.objects.create(code="AREA_CHAIR", name="Area Chair")
        area_user = User.objects.create_user(
            username="area1",
            email="area1@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        UserRole.objects.create(user=area_user, role=area_role, tenant=self.tenant, campus=self.campus)
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=1,
            approver_role=area_role,
            approver_label="Area Chair",
            requires_same_department=True,
        )
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=2,
            approver_role=self.reviewer_role,
            approver_label="College Dean",
            requires_same_department=True,
        )
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=3,
            approver_role=self.cao_role,
            approver_label="CAO",
        )

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Three approvals before applying the score.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "45",
                }
            ],
        )

        self.assertEqual(
            list(correction.approval_steps.order_by("step_order").values_list("approver_role__code", flat=True)),
            ["AREA_CHAIR", "DEAN", "CAO"],
        )
        first_review = GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=area_user,
            approved=True,
            review_remarks="Area chair endorsed.",
        )
        self.assertEqual(first_review.status, GradeCorrectionRequest.Status.PENDING)
        self.assertEqual(
            StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True).raw_score,
            Decimal("30.00"),
        )

        second_review = GradingGovernanceService.review_correction_request(
            request_obj=first_review,
            reviewer=self.reviewer_user,
            approved=True,
            review_remarks="Dean endorsed.",
        )
        self.assertEqual(second_review.status, GradeCorrectionRequest.Status.PENDING)
        self.assertEqual(
            StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True).raw_score,
            Decimal("30.00"),
        )

        final_review = GradingGovernanceService.review_correction_request(
            request_obj=second_review,
            reviewer=self.cao_user,
            approved=True,
            review_remarks="CAO approved.",
        )

        self.assertEqual(final_review.status, GradeCorrectionRequest.Status.CLOSED)
        self.assertEqual(
            StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True).raw_score,
            Decimal("45.00"),
        )

    def test_ordered_correction_route_can_skip_dean_when_not_configured(self):
        area_role = Role.objects.create(code="AREA_CHAIR", name="Area Chair")
        area_user = User.objects.create_user(
            username="area2",
            email="area2@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        UserRole.objects.create(user=area_user, role=area_role, tenant=self.tenant, campus=self.campus)
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=1,
            approver_role=area_role,
            approver_label="Area Chair",
            requires_same_department=True,
        )
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=2,
            approver_role=self.cao_role,
            approver_label="CAO",
        )
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="No dean step for this department.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "44",
                }
            ],
        )

        self.assertEqual(
            list(correction.approval_steps.order_by("step_order").values_list("approver_role__code", flat=True)),
            ["AREA_CHAIR", "CAO"],
        )
        GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=area_user,
            approved=True,
            review_remarks="Area chair endorsed.",
        )
        updated = GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=self.cao_user,
            approved=True,
            review_remarks="CAO approved.",
        )

        self.assertEqual(updated.status, GradeCorrectionRequest.Status.CLOSED)
        self.assertEqual(
            StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True).raw_score,
            Decimal("44.00"),
        )

    def test_correction_route_ignores_user_default_department_when_faculty_role_is_scoped(self):
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="GENED",
            name="General Education",
        )
        self.faculty_user.default_department = other_department
        self.faculty_user.save(update_fields=["default_department", "updated_at"])

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Faculty role department should govern the route.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "40",
                }
            ],
        )

        self.assertEqual(correction.faculty_department_id, self.department.id)
        self.assertEqual(correction.approval_route_id, self.route_rule.id)

    def test_correction_route_excludes_inactive_ancestor(self):
        parent = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="INACTIVE_PARENT",
            name="Inactive Parent",
            is_active=False,
        )
        Department.objects.filter(pk=self.department.id).update(parent=parent)
        self.route_rule.faculty_department = parent
        self.route_rule.save(update_fields=["faculty_department", "updated_at"])

        self.assertIsNone(
            GradingGovernanceService.resolve_correction_route_rule(
                tenant_id=self.tenant.id,
                faculty_department_id=self.department.id,
            )
        )

    def test_correction_route_excludes_cross_campus_ancestor(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="ANCESTOR_OTHER", name="Ancestor Other")
        parent = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="CROSS_CAMPUS_PARENT",
            name="Cross Campus Parent",
        )
        Department.objects.filter(pk=self.department.id).update(parent=parent)
        self.route_rule.faculty_department = parent
        self.route_rule.save(update_fields=["faculty_department", "updated_at"])

        self.assertIsNone(
            GradingGovernanceService.resolve_correction_route_rule(
                tenant_id=self.tenant.id,
                faculty_department_id=self.department.id,
            )
        )

    def test_correction_route_excludes_cross_tenant_ancestor(self):
        other_tenant = Tenant.objects.create(code="OTHER_ANCESTOR", name="Other Ancestor Tenant")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER_PARENT", name="Other Parent Campus")
        parent = Department.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            code="CROSS_TENANT_PARENT",
            name="Cross Tenant Parent",
        )
        Department.objects.filter(pk=self.department.id).update(parent=parent)
        self.route_rule.faculty_department = parent
        self.route_rule.save(update_fields=["faculty_department", "updated_at"])

        self.assertIsNone(
            GradingGovernanceService.resolve_correction_route_rule(
                tenant_id=self.tenant.id,
                faculty_department_id=self.department.id,
            )
        )

    def test_correction_route_cycle_fails_closed(self):
        parent = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="CYCLE_PARENT",
            name="Cycle Parent",
        )
        Department.objects.filter(pk=self.department.id).update(parent=parent)
        Department.objects.filter(pk=parent.id).update(parent=self.department)
        self.route_rule.faculty_department = parent
        self.route_rule.save(update_fields=["faculty_department", "updated_at"])

        with self.assertRaisesMessage(ValidationError, "hierarchy contains a cycle"):
            GradingGovernanceService.resolve_correction_route_rule(
                tenant_id=self.tenant.id,
                faculty_department_id=self.department.id,
            )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_step_approval_notification_is_scoped_to_current_step_and_deduped(self):
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        area_role = Role.objects.create(code="AREA_CHAIR", name="Area Chair")
        area_user = User.objects.create_user(
            username="area3",
            email="area3@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        UserRole.objects.create(user=area_user, role=area_role, tenant=self.tenant, campus=self.campus)
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=1,
            approver_role=area_role,
            approver_label="Area Chair",
            requires_same_department=True,
        )
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=2,
            approver_role=self.cao_role,
            approver_label="CAO",
        )
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Notify only the current approval step.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "40",
                }
            ],
        )
        pending_step = GradingGovernanceService.get_pending_correction_step(request_obj=correction)

        first_result = CorrectionNotificationService.send_correction_step_approval_notifications(
            request_obj=correction,
            step=pending_step,
        )
        second_result = CorrectionNotificationService.send_correction_step_approval_notifications(
            request_obj=correction,
            step=pending_step,
        )

        self.assertEqual(first_result["sent"], 1)
        self.assertEqual(first_result["recipients"], ["area3@example.com"])
        self.assertEqual(second_result["reason"], "already_sent")
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_step_notification_respects_department_scoped_role_assignment(self):
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BA",
            name="Business Administration",
        )
        area_role = Role.objects.create(code="AREA_CHAIR", name="Area Chair")
        area_user = User.objects.create_user(
            username="area-wrong-scope",
            email="area-wrong-scope@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        UserRole.objects.create(
            user=area_user,
            role=area_role,
            tenant=self.tenant,
            campus=self.campus,
            department=other_department,
        )
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=1,
            approver_role=area_role,
            approver_label="Area Chair",
            requires_same_department=True,
        )
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Wrong department role assignment should not receive email.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "40",
                }
            ],
        )
        pending_step = GradingGovernanceService.get_pending_correction_step(request_obj=correction)

        result = CorrectionNotificationService.send_correction_step_approval_notifications(
            request_obj=correction,
            step=pending_step,
        )

        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result["reason"], "no_matching_step_recipients")
        self.assertEqual(len(mail.outbox), 0)

    def test_correction_route_form_commit_false_saves_ordered_steps(self):
        from apps.admin_portal.forms import CorrectionApprovalRouteRuleForm

        area_role = Role.objects.create(code="AREA_CHAIR", name="Area Chair")
        form = CorrectionApprovalRouteRuleForm(
            data={
                "faculty_department": self.department.id,
                "step_1_role": area_role.id,
                "step_1_requires_same_department": "on",
                "step_2_role": self.reviewer_role.id,
                "step_2_requires_same_department": "on",
                "final_role_ordered": self.cao_role.id,
                "notes": "Area chair, dean, then CAO.",
                "is_active": "on",
            },
            instance=self.route_rule,
            tenant=self.tenant,
            department_queryset=Department.objects.filter(id=self.department.id),
            role_queryset=Role.objects.filter(id__in=[area_role.id, self.reviewer_role.id, self.cao_role.id]),
        )

        self.assertTrue(form.is_valid(), form.errors)
        route = form.save(commit=False)
        route.tenant = self.tenant
        route.save()
        form.save_ordered_steps(route)

        self.assertEqual(
            list(route.ordered_steps.order_by("step_order").values_list("approver_role__code", flat=True)),
            ["AREA_CHAIR", "DEAN", "CAO"],
        )

    def test_correction_route_form_rejects_duplicate_ordered_roles(self):
        from apps.admin_portal.forms import CorrectionApprovalRouteRuleForm

        form = CorrectionApprovalRouteRuleForm(
            data={
                "faculty_department": self.department.id,
                "step_1_role": self.reviewer_role.id,
                "step_2_role": self.reviewer_role.id,
                "final_role_ordered": self.cao_role.id,
                "is_active": "on",
            },
            tenant=self.tenant,
            department_queryset=Department.objects.filter(id=self.department.id),
            role_queryset=Role.objects.filter(id__in=[self.reviewer_role.id, self.cao_role.id]),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Each correction approval step must use a different approver role", str(form.errors))

    def test_correction_petition_policy_form_rejects_duplicate_active_scope(self):
        from apps.admin_portal.forms import CorrectionPetitionWindowPolicyForm

        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=None,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
            is_active=True,
        )
        form = CorrectionPetitionWindowPolicyForm(
            data={
                "campus": "",
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "grading_period": self.period.id,
                "policy_mode": CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
                "manual_notice": "Use the approved channel.",
                "is_active": "on",
            },
            tenant=self.tenant,
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            grading_period_queryset=GradingTemplatePeriod.objects.filter(id=self.period.id),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("active correction petition window policy already exists", str(form.errors))

    def test_correction_period_normalization_is_exact_across_form_service_model_and_migration(self):
        from apps.admin_portal.forms import (
            CorrectionPetitionWindowPolicyForm,
            _normalize_correction_policy_period_key,
        )

        migration_0034 = import_module(
            "apps.grading.migrations.0034_correctionpetitionwindowpolicy_canonical_period"
        )
        values = {
            "PRELIM": "PRELIM",
            "MIDTERM": "MIDTERM",
            "PRE-FINAL": "PREFINAL",
            "PRE FINAL": "PREFINAL",
            "PREFINAL": "PREFINAL",
            "FINAL": "FINAL",
            "MIDTERM-REMEDIAL": "MIDTERMREMEDIAL",
            "PRELIMINARY": "PRELIMINARY",
            "POST-FINAL": "POSTFINAL",
            "PREFI-SPECIAL": "PREFISPECIAL",
            "FINAL-RETAKE": "FINALRETAKE",
            "CUSTOM PERIOD": "CUSTOMPERIOD",
        }
        custom_periods = []
        for sequence_no, (value, expected) in enumerate(values.items(), start=10):
            period = self.period if value == self.period.code else GradingTemplatePeriod.objects.create(
                template=self.template,
                code=value,
                name=value.title(),
                sequence_no=sequence_no,
            )
            self.assertEqual(_normalize_correction_policy_period_key(period), expected)
            self.assertEqual(GradingGovernanceService.canonical_correction_period_key(period), expected)
            self.assertEqual(migration_0034._canonical_period_key(value), expected)
            if value not in {"PRELIM", "MIDTERM", "PRE-FINAL", "PRE FINAL", "PREFINAL", "FINAL"}:
                custom_periods.append(period)

        form = CorrectionPetitionWindowPolicyForm(
            tenant=self.tenant,
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            grading_period_queryset=GradingTemplatePeriod.objects.filter(template=self.template),
        )
        selectable_ids = set(form.fields["grading_period"].queryset.values_list("id", flat=True))
        self.assertTrue({period.id for period in custom_periods}.issubset(selectable_ids))

        custom_period = next(period for period in custom_periods if period.code == "MIDTERM-REMEDIAL")
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=custom_period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
            is_active=True,
        )
        duplicate_form = CorrectionPetitionWindowPolicyForm(
            data={
                "campus": self.campus.id,
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "grading_period": custom_period.id,
                "policy_mode": CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
                "manual_notice": "",
                "is_active": "on",
            },
            tenant=self.tenant,
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            grading_period_queryset=GradingTemplatePeriod.objects.filter(template=self.template),
        )
        self.assertFalse(duplicate_form.is_valid())
        self.assertIn("active correction petition window policy already exists", str(duplicate_form.errors))

    def test_correction_petition_policy_form_requires_days_for_days_after_mode(self):
        from apps.admin_portal.forms import CorrectionPetitionWindowPolicyForm

        form = CorrectionPetitionWindowPolicyForm(
            data={
                "campus": self.campus.id,
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "grading_period": self.period.id,
                "policy_mode": CorrectionPetitionWindowPolicy.PolicyMode.DAYS_AFTER_PERIOD_END,
                "allowed_days_after_period_end": "",
                "manual_notice": "",
                "is_active": "on",
            },
            tenant=self.tenant,
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            grading_period_queryset=GradingTemplatePeriod.objects.filter(id=self.period.id),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Allowed days is required", str(form.errors))

    def test_correction_petition_policy_open_anytime_allows_submission(self):
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
            manual_notice="Follow the published correction route.",
            is_active=True,
        )

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Open policy should allow filing.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "41",
                }
            ],
        )

        self.assertEqual(correction.status, GradeCorrectionRequest.Status.PENDING)
        self.assertEqual(correction.approval_route_id, self.route_rule.id)

    def test_correction_petition_policy_days_after_deadline_blocks_submission(self):
        lock = self.correction_lock
        lock.deadline_at = timezone.now() - timedelta(days=2)
        lock.save(update_fields=["deadline_at", "updated_at"])
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.DAYS_AFTER_PERIOD_END,
            allowed_days_after_period_end=1,
            manual_notice="Late petitions are not allowed anymore.",
            is_active=True,
        )

        with self.assertRaisesMessage(ValidationError, "Correction petitions are closed for this grading period."):
            GradingGovernanceService.create_correction_request(
                user=self.faculty_user,
                offering=self.offering,
                template_period=self.period,
                justification="This should be blocked by the petition window.",
                items=[
                    {
                        "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                        "student_id": self.student1.id,
                        "grade_activity_id": self.activity.id,
                        "new_value": "41",
                    }
                ],
            )

        self.assertTrue(lock.deadline_at < timezone.now())
        self.assertEqual(GradeCorrectionRequest.objects.count(), 0)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_period_corrections_view_blocks_submission_when_policy_closed(self):
        faculty_access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access"},
        )
        corrections_create, _ = Permission.objects.get_or_create(
            code="corrections.create",
            defaults={"module": "corrections", "action": "create"},
        )
        role, _ = Role.objects.get_or_create(code="FACULTY_CORRECTION_WINDOW", defaults={"name": "Faculty Correction Window"})
        RolePermission.objects.get_or_create(role=role, permission=faculty_access)
        RolePermission.objects.get_or_create(role=role, permission=corrections_create)
        UserRole.objects.create(
            user=self.faculty_user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        GradeSubmission.objects.update_or_create(
            offering=self.offering,
            template_period=self.period,
            defaults={
                "tenant": self.tenant,
                "campus": self.campus,
                "status": GradeSubmission.Status.SUBMITTED,
                "submitted_by_user": self.faculty_user,
                "submission_snapshot_json": {},
                "template_snapshot_json": {},
            },
        )
        FacultyAssignment.objects.filter(
            offering=self.offering,
            faculty_user=self.faculty_user,
        ).update(
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            responded_at=timezone.now(),
            updated_at=timezone.now(),
        )
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.CLOSED,
            manual_notice="Use the paper petition route.",
            is_active=True,
        )
        self.faculty_user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.faculty_user.privacy_consent_at = timezone.now()
        self.faculty_user.save(update_fields=["privacy_consent_version", "privacy_consent_at", "updated_at"])
        self.client.force_login(self.faculty_user)

        summary_response = self.client.get(
            reverse("faculty_portal:period_summary", args=[self.offering.id, self.period.id])
        )
        self.assertEqual(summary_response.status_code, 200)
        self.assertFalse(summary_response.context["can_access_corrections"])

        before_count = GradeCorrectionRequest.objects.count()
        response = self.client.post(
            reverse("faculty_portal:period_corrections", args=[self.offering.id, self.period.id]),
            {
                "students": [self.student1.id],
                "grade_activities": [self.activity.id],
                "correction_payload": (
                    f'[{{"student_id":"{self.student1.id}","grade_activity_id":"{self.activity.id}","new_value":"41"}}]'
                ),
                "justification": "Blocked by policy.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correction petitions are closed for this grading period.")
        self.assertEqual(GradeCorrectionRequest.objects.count(), before_count)
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_on_behalf_ui_hides_filing_controls_when_petition_window_is_closed(self):
        admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={"module": "admin_portal", "action": "access"},
        )
        create_on_behalf, _ = Permission.objects.get_or_create(
            code="corrections.create_on_behalf",
            defaults={"module": "corrections", "action": "create_on_behalf"},
        )
        admin_role = Role.objects.create(code="ADMIN_ON_BEHALF_WINDOW", name="Admin On Behalf Window")
        RolePermission.objects.create(role=admin_role, permission=admin_access)
        RolePermission.objects.create(role=admin_role, permission=create_on_behalf)
        admin_user = User.objects.create_user(
            username="admin-on-behalf-window",
            email="admin-on-behalf-window@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=admin_user,
            role=admin_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.CLOSED,
            is_active=True,
        )
        self.client.force_login(admin_user)

        response = self.client.get(
            reverse("admin_portal:grade_correction_request_create_on_behalf"),
            {
                "campus": self.campus.id,
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "faculty_user": self.faculty_user.id,
                "section": self.section.id,
                "course": self.course.id,
                "template_period": self.period.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_file"])
        self.assertFalse(response.context["correction_filing_state"]["is_allowed"])
        self.assertContains(response, "Correction petitions are closed for this grading period.")
        self.assertNotContains(response, "Correction Items")

    def test_correction_governance_page_renders_policy_section(self):
        admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={"module": "admin_portal", "action": "access"},
        )
        governance_update, _ = Permission.objects.get_or_create(
            code="grading_governance_settings.update",
            defaults={"module": "grading_governance_settings", "action": "update"},
        )
        admin_role, _ = Role.objects.get_or_create(code="ADMIN_CORRECTION_WINDOW", defaults={"name": "Admin Correction Window"})
        RolePermission.objects.get_or_create(role=admin_role, permission=admin_access)
        RolePermission.objects.get_or_create(role=admin_role, permission=governance_update)
        admin_user = User.objects.create_user(
            username="admin-correction-window",
            email="admin-correction-window@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=admin_user,
            role=admin_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("admin_portal:correction_governance_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correction Petition Window Policy")
        self.assertContains(response, "No petition window policy configured yet.")

    def test_repeated_final_approval_with_stale_request_is_rejected(self):
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Repeated approval should not apply twice.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "45",
                }
            ],
        )
        stale_request = correction

        updated = GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=self.reviewer_user,
            approved=True,
            review_remarks="Approved once.",
        )

        self.assertEqual(updated.status, GradeCorrectionRequest.Status.CLOSED)
        with self.assertRaisesMessage(ValidationError, "Only pending correction requests can be reviewed."):
            GradingGovernanceService.review_correction_request(
                request_obj=stale_request,
                reviewer=self.reviewer_user,
                approved=True,
                review_remarks="Duplicate submit.",
            )
        self.assertEqual(
            StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True).raw_score,
            Decimal("45.00"),
        )

    def test_correction_progress_includes_step_timestamps_reviewers_and_remarks(self):
        area_role = Role.objects.create(code="AREA_CHAIR", name="Area Chair")
        area_user = User.objects.create_user(
            username="area-progress",
            email="area-progress@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        UserRole.objects.create(user=area_user, role=area_role, tenant=self.tenant, campus=self.campus)
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=1,
            approver_role=area_role,
            approver_label="Area Chair",
            requires_same_department=True,
        )
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=2,
            approver_role=self.cao_role,
            approver_label="CAO",
        )
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Progress should show review history.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "45",
                }
            ],
        )

        initial_progress = GradingGovernanceService.correction_progress(request_obj=correction)
        self.assertEqual(initial_progress["status_label"], "Pending Area Chair")
        self.assertTrue(initial_progress["steps"][0]["is_current"])

        GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=area_user,
            approved=True,
            review_remarks="Area chair endorsed.",
        )
        correction.refresh_from_db()
        intermediate_progress = GradingGovernanceService.correction_progress(request_obj=correction)

        self.assertEqual(intermediate_progress["status_label"], "Pending CAO")
        self.assertEqual(intermediate_progress["steps"][0]["status"], GradeCorrectionApprovalStep.Status.APPROVED)
        self.assertEqual(intermediate_progress["steps"][0]["reviewer_name"], area_user.username)
        self.assertIsNotNone(intermediate_progress["steps"][0]["reviewed_at"])
        self.assertEqual(intermediate_progress["steps"][0]["remarks"], "Area chair endorsed.")
        self.assertTrue(intermediate_progress["steps"][1]["is_current"])

        updated = GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=self.cao_user,
            approved=True,
            review_remarks="CAO approved.",
        )
        final_progress = GradingGovernanceService.correction_progress(request_obj=updated)

        self.assertEqual(final_progress["status_label"], "Approved")
        self.assertEqual(final_progress["steps"][1]["status"], GradeCorrectionApprovalStep.Status.APPROVED)
        self.assertEqual(final_progress["steps"][1]["reviewer_name"], self.cao_user.username)
        self.assertEqual(final_progress["steps"][1]["remarks"], "CAO approved.")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_step_notification_reports_missing_step_recipient_email(self):
        SystemSettingService.set(
            FeatureSettingsService.CORRECTION_SUBMISSION_APPROVAL_EMAIL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        no_email_role = Role.objects.create(code="AREA_CHAIR", name="Area Chair")
        no_email_user = User.objects.create_user(
            username="area-no-email",
            email="",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        UserRole.objects.create(user=no_email_user, role=no_email_role, tenant=self.tenant, campus=self.campus)
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=1,
            approver_role=no_email_role,
            approver_label="Area Chair",
            requires_same_department=True,
        )
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Approver has no email.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": self.activity.id,
                    "new_value": "40",
                }
            ],
        )
        pending_step = GradingGovernanceService.get_pending_correction_step(request_obj=correction)

        result = CorrectionNotificationService.send_correction_step_approval_notifications(
            request_obj=correction,
            step=pending_step,
        )

        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result["reason"], "no_matching_step_recipients")
        self.assertEqual(len(mail.outbox), 0)

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

    def test_final_approval_recomputes_average_activity_detail_mode(self):
        # This recomputation setup is deliberately editable; the locked
        # post-deadline fixture is enabled only for the correction request.
        self.correction_lock.is_locked = False
        self.correction_lock.save(update_fields=["is_locked", "updated_at"])
        participation = GradingTemplateSubcomponent.objects.create(
            template_component=self.component,
            code="PART_OUTPUT",
            name="Participation/Output",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
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
            weight_percentage=Decimal("80.00"),
            sort_order=2,
            is_active=True,
        )

        def add_activity(detail, title, raw_score):
            activity = GradeActivity.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=self.offering,
                template_period=self.period,
                template_component=self.component,
                template_subcomponent=participation,
                template_detail=detail,
                title=title,
                total_score=Decimal("100.00"),
                created_by_user=self.faculty_user,
                is_active=True,
            )
            StudentActivityScore.objects.create(
                activity=activity,
                student=self.student1,
                raw_score=raw_score,
                computed_score=FacultyGradingService.compute_activity_score(
                    raw_score=raw_score,
                    total_score=Decimal("100.00"),
                    base_value=Decimal("50.00"),
                ),
                encoded_by_user=self.faculty_user,
                is_active=True,
            )
            return activity

        recitation_activity = add_activity(recitation, "R1", Decimal("50.00"))
        add_activity(assignment, "ASSIGN1", Decimal("100.00"))

        GradeSubmission.objects.filter(offering=self.offering, template_period=self.period).update(
            status=GradeSubmission.Status.REOPENED
        )
        FacultyGradingService.recompute_period_summary_for_students(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            student_ids=[self.student1.id],
            audit_reason="TEST_INITIAL_AVERAGE_ACTIVITIES",
        )
        initial_period_grade = StudentPeriodGrade.objects.get(
            offering=self.offering,
            template_period=self.period,
            student=self.student1,
        )
        self.assertEqual(initial_period_grade.period_grade, Decimal("88.00"))
        GradeSubmission.objects.filter(offering=self.offering, template_period=self.period).update(
            status=GradeSubmission.Status.SUBMITTED
        )
        self._set_correction_lifecycle(deadline_at=timezone.now() - timedelta(hours=1), is_locked=True)

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Correct recitation score.",
            items=[
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                    "student_id": self.student1.id,
                    "grade_activity_id": recitation_activity.id,
                    "new_value": "100",
                }
            ],
        )

        updated = GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=self.reviewer_user,
            approved=True,
            review_remarks="Approved average activity correction.",
        )

        corrected_score = StudentActivityScore.objects.get(
            activity=recitation_activity,
            student=self.student1,
            is_active=True,
        )
        period_grade = StudentPeriodGrade.objects.get(
            offering=self.offering,
            template_period=self.period,
            student=self.student1,
        )
        final_grade = StudentFinalGrade.objects.get(offering=self.offering, student=self.student1)

        self.assertEqual(updated.status, GradeCorrectionRequest.Status.CLOSED)
        self.assertEqual(corrected_score.computed_score, Decimal("100.00"))
        self.assertEqual(period_grade.period_grade, Decimal("100.00"))
        self.assertEqual(final_grade.final_grade, Decimal("100.00"))
        self.assertTrue(period_grade.is_finalized)
        self.assertTrue(final_grade.is_submitted)

    def test_score_write_recomputes_scoped_period_and_final_immediately(self):
        self.correction_lock.delete()
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

    def test_official_period_and_final_grades_round_to_whole_numbers(self):
        self.correction_lock.delete()
        GradeSubmission.objects.filter(offering=self.offering, template_period=self.period).update(
            status=GradeSubmission.Status.REOPENED
        )

        FacultyGradingService.upsert_activity_scores(
            user=self.faculty_user,
            activity=self.activity,
            score_payload=[{"student_id": self.student1.id, "raw_score": Decimal("33.50")}],
        )

        score = StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True)
        period_grade = StudentPeriodGrade.objects.get(
            offering=self.offering,
            template_period=self.period,
            student=self.student1,
        )
        final_grade = StudentFinalGrade.objects.get(offering=self.offering, student=self.student1)

        self.assertEqual(score.computed_score, Decimal("83.50"))
        self.assertEqual(period_grade.class_standing_grade, Decimal("84"))
        self.assertEqual(period_grade.period_grade, Decimal("84"))
        self.assertEqual(final_grade.final_grade, Decimal("84"))

    def test_exam_component_flag_drives_exam_bucket_without_code_name_dependency(self):
        self.correction_lock.delete()
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

        with patch("apps.grading.reporting.Paragraph", wraps=ReportLabParagraph) as paragraph:
            pdf_bytes = CorrectionOfficialReportService.build_pdf_bytes(request_obj=closed_request)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertFalse(
            any("No stored signature on file." in call.args[0] for call in paragraph.call_args_list)
        )
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

    def test_official_correction_report_uses_fallback_for_invalid_signature_tag(self):
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

        buffer = BytesIO()
        Image.new("RGBA", (180, 60), (120, 10, 10, 255)).save(buffer, format="PNG")
        credential = UserSignatureService.store_signature(
            user=self.reviewer_user,
            uploaded_file=SimpleUploadedFile(
                "reviewer-signature.png",
                buffer.getvalue(),
                content_type="image/png",
            ),
            actor=self.reviewer_user,
        )
        credential.encrypted_blob = credential.encrypted_blob[:-1] + bytes([credential.encrypted_blob[-1] ^ 1])
        credential.save(update_fields=["encrypted_blob"])
        with self.assertRaises(InvalidTag):
            UserSignatureService.decrypt_signature_bytes(credential=credential)

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Gracefully render a report when an old signature cannot decrypt.",
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
            review_remarks="Approved with an unavailable stored signature.",
        )

        with self.assertLogs("teachermateplus.system", level="WARNING") as logs:
            with patch("apps.grading.reporting.Paragraph", wraps=ReportLabParagraph) as paragraph:
                pdf_bytes = CorrectionOfficialReportService.build_pdf_bytes(request_obj=closed_request)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertTrue(
            any("No stored signature on file." in call.args[0] for call in paragraph.call_args_list)
        )
        self.assertIn(f"user_id={self.reviewer_user.id}", logs.output[0])
        self.assertIn(f"credential_id={credential.id}", logs.output[0])
        self.assertEqual(
            UserSignatureUsageLog.objects.filter(
                user=self.reviewer_user,
                document_type=UserSignatureUsageLog.DocumentType.CORRECTION_OFFICIAL_REPORT,
            ).count(),
            0,
        )

    def test_official_correction_report_keeps_fallback_without_signature(self):
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

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Render the existing no-signature fallback.",
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
            review_remarks="Approved without stored signatures.",
        )

        with patch("apps.grading.reporting.Paragraph", wraps=ReportLabParagraph) as paragraph:
            pdf_bytes = CorrectionOfficialReportService.build_pdf_bytes(request_obj=closed_request)

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertEqual(
            sum("No stored signature on file." in call.args[0] for call in paragraph.call_args_list),
            2,
        )
        self.assertEqual(
            UserSignatureUsageLog.objects.filter(
                document_type=UserSignatureUsageLog.DocumentType.CORRECTION_OFFICIAL_REPORT,
            ).count(),
            0,
        )

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
                "NCBA | TeacherMatePlus: Petition for Correction of Grades Awaiting Your Approval",
            )
            self.assertEqual(len(message.alternatives), 1)
            html_body = message.alternatives[0].content
            self.assertIn('alt="NCBA"', html_body)
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
            "NCBA | TeacherMatePlus: Approved Petition for Correction of Grades for Registrar Reference",
        )
        self.assertEqual(mail.outbox[0].to, ["registrar@example.com"])
        pdf_filenames = [
            item[0]
            for item in mail.outbox[0].attachments
            if isinstance(item, tuple) and len(item) >= 3 and item[2] == "application/pdf"
        ]
        self.assertEqual(len(pdf_filenames), 1)
        self.assertTrue(pdf_filenames[0].endswith(".pdf"))

    def _correction_item(self, value="40"):
        return [
            {
                "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
                "student_id": self.student1.id,
                "grade_activity_id": self.activity.id,
                "new_value": value,
            }
        ]

    def _legacy_approved_request_with_window(
        self,
        *,
        approval_route=None,
        step_role=None,
        requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
    ):
        reviewed_at = timezone.now()
        reviewer = self.reviewer_user if step_role in (None, self.reviewer_role) else self.cao_user
        correction = GradeCorrectionRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.faculty_user,
            initiated_by_user=self.faculty_user,
            faculty_department=self.department,
            approval_route=approval_route,
            status=GradeCorrectionRequest.Status.APPROVED,
            justification="Legacy approved correction request.",
            reviewed_by_user=reviewer,
            reviewed_at=reviewed_at,
        )
        step = GradeCorrectionApprovalStep.objects.create(
            correction_request=correction,
            step_order=1,
            approver_role=step_role or self.cao_role,
            approver_label="Legacy approver",
            status=GradeCorrectionApprovalStep.Status.APPROVED,
            reviewed_by_user=reviewer,
            reviewed_at=reviewed_at,
        )
        item = GradeCorrectionRequestItem.objects.create(
            correction_request=correction,
            requested_action=requested_action,
            student=self.student1,
            grade_activity=(
                self.activity
                if requested_action == GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE
                else None
            ),
            old_value="30",
            new_value="45",
        )
        window = GradeCorrectionUnlockWindow.objects.create(
            correction_request=correction,
            offering=self.offering,
            template_period=self.period,
            start_at=timezone.now() - timedelta(minutes=5),
            end_at=timezone.now() + timedelta(hours=1),
            is_active=True,
            is_consumed=False,
        )
        return correction, step, item, window

    def _approved_configured_request_with_window(self):
        """Create a legitimate approved request without auto-applying its score."""
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Configured approved correction for governance regression coverage.",
            items=[
                *self._correction_item(value="45"),
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_STATUS,
                    "student_id": self.student1.id,
                    "new_value": "REVIEWED",
                },
            ],
        )
        return GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=self.reviewer_user,
            approved=True,
            review_remarks="Approved through the configured route.",
        )

    def _set_correction_lifecycle(self, *, deadline_at, is_locked=True, submission_status=GradeSubmission.Status.SUBMITTED):
        GradeSubmission.objects.filter(
            offering=self.offering,
            template_period=self.period,
        ).update(status=submission_status)
        self.correction_lock.deadline_at = deadline_at
        self.correction_lock.is_locked = is_locked
        self.correction_lock.save(update_fields=["deadline_at", "is_locked", "updated_at"])

    def _grant_faculty_correction_access(self):
        faculty_access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access"},
        )
        corrections_create, _ = Permission.objects.get_or_create(
            code="corrections.create",
            defaults={"module": "corrections", "action": "create"},
        )
        RolePermission.objects.get_or_create(role=self.faculty_role, permission=faculty_access)
        RolePermission.objects.get_or_create(role=self.faculty_role, permission=corrections_create)
        FacultyAssignment.objects.filter(
            offering=self.offering,
            faculty_user=self.faculty_user,
        ).update(
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            responded_at=timezone.now(),
        )
        self.faculty_user.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.faculty_user.privacy_consent_at = timezone.now()
        self.faculty_user.save(update_fields=["privacy_consent_version", "privacy_consent_at", "updated_at"])

    def _set_offering_outside_active_scope(self):
        active_academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2026-2027",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        active_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=active_academic_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 10, 31),
        )
        AcademicGovernanceService.set_active_scope(
            tenant_id=self.tenant.id,
            academic_year=active_academic_year,
            term=active_term,
        )

    def test_correction_route_uses_exact_faculty_role_department_not_offering_department(self):
        college = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COL",
            name="College",
        )
        information_systems = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="IS",
            name="Information Systems",
        )
        self.offering.department = college
        self.offering.save(update_fields=["department", "updated_at"])
        self.faculty_user.user_roles.filter(role=self.faculty_role).update(department=information_systems)
        self.route_rule.faculty_department = information_systems
        self.route_rule.save(update_fields=["faculty_department", "updated_at"])
        area_role = Role.objects.create(code="AREA_CHAIR", name="Area Chairman")
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=1,
            approver_role=area_role,
            approver_label="Area Chairman",
            requires_same_department=True,
        )
        CorrectionApprovalRouteStep.objects.create(
            route_rule=self.route_rule,
            step_order=2,
            approver_role=self.cao_role,
            approver_label="Chief Academic Officer",
        )

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Route by the assigned faculty department.",
            items=self._correction_item(),
        )

        self.assertEqual(correction.faculty_department_id, information_systems.id)
        self.assertEqual(correction.approval_route_id, self.route_rule.id)
        self.assertEqual(
            list(correction.approval_steps.order_by("step_order").values_list("approver_role__code", flat=True)),
            ["AREA_CHAIR", "CAO"],
        )

    def test_campus_admin_assignment_does_not_override_faculty_department_route(self):
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BA",
            name="Business Administration",
        )
        campus_admin = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin")
        UserRole.objects.create(
            user=self.faculty_user,
            role=campus_admin,
            tenant=self.tenant,
            campus=self.campus,
            department=other_department,
        )

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Faculty role must remain authoritative.",
            items=self._correction_item(),
        )

        self.assertEqual(correction.faculty_department_id, self.department.id)
        self.assertEqual(correction.approval_route_id, self.route_rule.id)

    def test_missing_or_unrelated_route_fails_closed_without_partial_request(self):
        unrelated_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BA",
            name="Business Administration",
        )
        self.route_rule.faculty_department = unrelated_department
        self.route_rule.save(update_fields=["faculty_department", "updated_at"])
        before_count = GradeCorrectionRequest.objects.count()

        with self.assertRaisesMessage(ValidationError, "No valid Correction Governance approval route"):
            GradingGovernanceService.create_correction_request(
                user=self.faculty_user,
                offering=self.offering,
                template_period=self.period,
                justification="No inferred CAO route is allowed.",
                items=self._correction_item(),
            )

        self.assertEqual(GradeCorrectionRequest.objects.count(), before_count)

    def test_explicit_tenant_default_route_remains_available(self):
        unrelated_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BA",
            name="Business Administration",
        )
        self.route_rule.faculty_department = unrelated_department
        self.route_rule.save(update_fields=["faculty_department", "updated_at"])
        default_route = CorrectionApprovalRouteRule.objects.create(
            tenant=self.tenant,
            faculty_department=None,
            route_mode=CorrectionApprovalRouteRule.RouteMode.DIRECT_TO_FINAL,
            step1_role=self.cao_role,
        )

        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Configured tenant default is permitted.",
            items=self._correction_item(),
        )

        self.assertEqual(correction.approval_route_id, default_route.id)

    def test_multiple_faculty_departments_fail_closed(self):
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BA",
            name="Business Administration",
        )
        UserRole.objects.create(
            user=self.faculty_user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=other_department,
        )

        with self.assertRaisesMessage(ValidationError, "multiple active FACULTY home/mother departments"):
            GradingGovernanceService.create_correction_request(
                user=self.faculty_user,
                offering=self.offering,
                template_period=self.period,
                justification="Ambiguous faculty governance must stop filing.",
                items=self._correction_item(),
            )
        self.assertEqual(GradeCorrectionRequest.objects.count(), 0)

    def test_missing_faculty_department_does_not_fall_back_to_tenant_default_route(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="CUBAO", name="Cubao")
        other_department = Department.objects.create(
            tenant=self.tenant, campus=other_campus, code="IS", name="Information Systems"
        )
        UserRole.objects.create(
            user=self.faculty_user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=other_campus,
            department=other_department,
        )
        self.faculty_user.user_roles.filter(role=self.faculty_role, campus=self.campus).update(department=None)
        default_route = CorrectionApprovalRouteRule.objects.create(
            tenant=self.tenant,
            faculty_department=None,
            route_mode=CorrectionApprovalRouteRule.RouteMode.DIRECT_TO_FINAL,
            step1_role=self.cao_role,
        )
        before_requests = GradeCorrectionRequest.objects.count()
        before_steps = GradeCorrectionApprovalStep.objects.count()

        with self.assertRaisesMessage(ValidationError, "no active FACULTY home/mother department"):
            GradingGovernanceService.create_correction_request(
                user=self.faculty_user,
                offering=self.offering,
                template_period=self.period,
                justification="Missing faculty governance scope must fail closed.",
                items=self._correction_item(),
            )

        self.assertIsNotNone(default_route)
        self.assertEqual(GradeCorrectionRequest.objects.count(), before_requests)
        self.assertEqual(GradeCorrectionApprovalStep.objects.count(), before_steps)

    def test_home_department_is_selected_per_exact_campus_for_same_faculty(self):
        fairview = Campus.objects.create(tenant=self.tenant, code="FAIRVIEW", name="Fairview")
        cubao = Campus.objects.create(tenant=self.tenant, code="CUBAO", name="Cubao")
        fairview_is = Department.objects.create(tenant=self.tenant, campus=fairview, code="IS", name="Information Systems")
        cubao_is = Department.objects.create(tenant=self.tenant, campus=cubao, code="IS", name="Information Systems")
        UserRole.objects.create(user=self.faculty_user, role=self.faculty_role, tenant=self.tenant, campus=fairview, department=fairview_is)
        UserRole.objects.create(user=self.faculty_user, role=self.faculty_role, tenant=self.tenant, campus=cubao, department=cubao_is)

        fairview_home = GradingGovernanceService.resolve_correction_scope_department(
            user=self.faculty_user, tenant_id=self.tenant.id, offering=SimpleNamespace(tenant_id=self.tenant.id, campus_id=fairview.id)
        )
        cubao_home = GradingGovernanceService.resolve_correction_scope_department(
            user=self.faculty_user, tenant_id=self.tenant.id, offering=SimpleNamespace(tenant_id=self.tenant.id, campus_id=cubao.id)
        )

        self.assertEqual(fairview_home.id, fairview_is.id)
        self.assertEqual(cubao_home.id, cubao_is.id)

    def test_home_department_is_selected_per_exact_campus_for_different_academic_areas(self):
        taytay = Campus.objects.create(tenant=self.tenant, code="TAYTAY", name="Taytay")
        cubao = Campus.objects.create(tenant=self.tenant, code="CUBAO", name="Cubao")
        taytay_ba = Department.objects.create(tenant=self.tenant, campus=taytay, code="BA", name="Business Administration")
        cubao_bsa = Department.objects.create(tenant=self.tenant, campus=cubao, code="BSA", name="Accountancy")
        UserRole.objects.create(user=self.faculty_user, role=self.faculty_role, tenant=self.tenant, campus=taytay, department=taytay_ba)
        UserRole.objects.create(user=self.faculty_user, role=self.faculty_role, tenant=self.tenant, campus=cubao, department=cubao_bsa)

        self.assertEqual(
            GradingGovernanceService.resolve_correction_scope_department(
                user=self.faculty_user, tenant_id=self.tenant.id, offering=SimpleNamespace(tenant_id=self.tenant.id, campus_id=taytay.id)
            ).id,
            taytay_ba.id,
        )
        self.assertEqual(
            GradingGovernanceService.resolve_correction_scope_department(
                user=self.faculty_user, tenant_id=self.tenant.id, offering=SimpleNamespace(tenant_id=self.tenant.id, campus_id=cubao.id)
            ).id,
            cubao_bsa.id,
        )

    def test_duplicate_faculty_role_cannot_create_home_department_ambiguity(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UserRole.objects.create(
                    user=self.faculty_user,
                    role=self.faculty_role,
                    tenant=self.tenant,
                    campus=self.campus,
                    department=self.department,
                )
        home_department = GradingGovernanceService.resolve_correction_scope_department(
            user=self.faculty_user,
            tenant_id=self.tenant.id,
            offering=self.offering,
        )
        self.assertEqual(home_department.id, self.department.id)

    def test_pending_request_without_current_step_cannot_be_reviewed_or_finalized(self):
        malformed = GradeCorrectionRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.faculty_user,
            initiated_by_user=self.faculty_user,
            faculty_department=self.department,
            approval_route=self.route_rule,
            status=GradeCorrectionRequest.Status.PENDING,
            justification="Malformed legacy request with no approval step.",
        )

        can_review, pending_step, reason = GradingGovernanceService.can_user_review_correction_request(
            request_obj=malformed,
            user=self.reviewer_user,
        )
        self.assertFalse(can_review)
        self.assertIsNone(pending_step)
        self.assertIn("approval steps do not match", reason)
        with self.assertRaisesMessage(ValidationError, "approval steps do not match"):
            GradingGovernanceService.review_correction_request(
                request_obj=malformed,
                reviewer=self.reviewer_user,
                approved=True,
            )
        malformed.refresh_from_db()
        self.assertEqual(malformed.status, GradeCorrectionRequest.Status.PENDING)
        self.assertFalse(hasattr(malformed, "unlock_window"))

    def test_active_policy_scope_key_enforces_canonical_uniqueness_and_keeps_inactive_history(self):
        active = CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
            is_active=True,
        )
        self.assertTrue(active.active_scope_key)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CorrectionPetitionWindowPolicy.objects.create(
                    tenant=self.tenant,
                    campus=self.campus,
                    academic_year=self.academic_year,
                    term=self.term,
                    grading_period=self.period,
                    policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.CLOSED,
                    is_active=True,
                )
        inactive = CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.CLOSED,
            is_active=False,
        )
        self.assertIsNone(inactive.active_scope_key)

    def test_active_scope_key_is_fixed_length_deterministic_digest(self):
        longest_identifier = 9223372036854775807
        key = CorrectionPetitionWindowPolicy.build_active_scope_key(
            tenant_id=longest_identifier,
            campus_id=longest_identifier,
            academic_year_id=longest_identifier,
            term_id=longest_identifier,
            canonical_period_key="P" * 120,
            is_active=True,
        )
        equivalent_key = CorrectionPetitionWindowPolicy.build_active_scope_key(
            tenant_id=longest_identifier,
            campus_id=longest_identifier,
            academic_year_id=longest_identifier,
            term_id=longest_identifier,
            canonical_period_key="P" * 120,
            is_active=True,
        )
        distinct_key = CorrectionPetitionWindowPolicy.build_active_scope_key(
            tenant_id=longest_identifier,
            campus_id=None,
            academic_year_id=longest_identifier,
            term_id=longest_identifier,
            canonical_period_key="P" * 120,
            is_active=True,
        )
        field = CorrectionPetitionWindowPolicy._meta.get_field("active_scope_key")
        self.assertEqual(len(key), 64)
        self.assertEqual(key, equivalent_key)
        self.assertNotEqual(key, distinct_key)
        self.assertEqual(field.max_length, CorrectionPetitionWindowPolicy.ACTIVE_SCOPE_KEY_MAX_LENGTH)

    def test_correction_period_normalization_uses_only_exact_standard_aliases(self):
        migration_0034 = __import__(
            "apps.grading.migrations.0034_correctionpetitionwindowpolicy_canonical_period",
            fromlist=["_canonical_period_key"],
        )
        standard = {
            "PRELIM": "PRELIM",
            "MIDTERM": "MIDTERM",
            "PRE-FINAL": "PREFINAL",
            "PRE FINAL": "PREFINAL",
            "PREFINAL": "PREFINAL",
            "FINAL": "FINAL",
            "Final Exam": "FINAL",
        }
        custom = {
            "MIDTERM-REMEDIAL": "MIDTERMREMEDIAL",
            "PRELIMINARY": "PRELIMINARY",
            "POST-FINAL": "POSTFINAL",
            "PREFI-SPECIAL": "PREFISPECIAL",
            "FINAL-RETAKE": "FINALRETAKE",
        }
        for source, expected in {**standard, **custom}.items():
            self.assertEqual(GradingGovernanceService._normalize_period_key(source), expected)
            self.assertEqual(migration_0034._canonical_period_key(source), expected)

        base_scope = dict(
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            academic_year_id=self.academic_year.id,
            term_id=self.term.id,
            is_active=True,
        )
        self.assertNotEqual(
            CorrectionPetitionWindowPolicy.build_active_scope_key(
                canonical_period_key=custom["MIDTERM-REMEDIAL"], **base_scope
            ),
            CorrectionPetitionWindowPolicy.build_active_scope_key(
                canonical_period_key=standard["MIDTERM"], **base_scope
            ),
        )

    def test_policy_name_fallback_stores_longest_supported_canonical_identity(self):
        longest_name = "P" * 120
        period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="",
            name=longest_name,
            sequence_no=2,
        )
        policy = CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
            is_active=True,
        )

        self.assertEqual(policy.canonical_period_key, longest_name)
        self.assertEqual(len(policy.canonical_period_key), 120)
        self.assertEqual(len(policy.active_scope_key), 64)
        self.assertEqual(
            CorrectionPetitionWindowPolicy._meta.get_field("canonical_period_key").max_length,
            120,
        )

    def test_approved_null_route_window_cannot_unlock_encode_close_or_rewrite_history(self):
        correction, step, item, window = self._legacy_approved_request_with_window()
        original_step = {
            "status": step.status,
            "approver_role_id": step.approver_role_id,
            "reviewed_by_user_id": step.reviewed_by_user_id,
            "reviewed_at": step.reviewed_at,
        }

        self.assertIsNone(
            GradingGovernanceService.get_active_unlock_window(
                offering=self.offering,
                template_period=self.period,
            )
        )
        with self.assertRaisesMessage(ValidationError, "locked by academic governance"):
            GradingGovernanceService.assert_encoding_allowed(
                offering=self.offering,
                template_period=self.period,
                student_id=self.student1.id,
                activity_id=self.activity.id,
                requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
            )
        with self.assertRaisesMessage(ValidationError, "locked by academic governance"):
            FacultyGradingService.upsert_activity_scores(
                user=self.faculty_user,
                activity=self.activity,
                score_payload=[{"student_id": self.student1.id, "raw_score": Decimal("45.00")}],
            )
        with self.assertRaisesMessage(ValidationError, "no configured approval route snapshot"):
            GradingGovernanceService.close_correction_window(request_obj=correction, actor=self.faculty_user)

        correction.refresh_from_db()
        step.refresh_from_db()
        item.refresh_from_db()
        window.refresh_from_db()
        self.assertEqual(correction.status, GradeCorrectionRequest.Status.APPROVED)
        self.assertIsNone(correction.approval_route_id)
        self.assertEqual(
            {
                "status": step.status,
                "approver_role_id": step.approver_role_id,
                "reviewed_by_user_id": step.reviewed_by_user_id,
                "reviewed_at": step.reviewed_at,
            },
            original_step,
        )
        self.assertTrue(item.is_active)
        self.assertTrue(window.is_active)
        self.assertFalse(window.is_consumed)
        self.assertEqual(
            StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True).raw_score,
            Decimal("30.00"),
        )

    def test_approved_null_route_finalize_denied_without_grade_mutation(self):
        correction, step, _item, window = self._legacy_approved_request_with_window(
            requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_STATUS,
        )
        self._grant_faculty_correction_access()
        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_correction_finalize",
                args=[self.offering.id, self.period.id, correction.id],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No active correction window to finalize.")
        self.assertFalse(
            StudentPeriodGrade.objects.filter(offering=self.offering, template_period=self.period).exists()
        )
        self.assertFalse(StudentFinalGrade.objects.filter(offering=self.offering).exists())
        correction.refresh_from_db()
        step.refresh_from_db()
        window.refresh_from_db()
        self.assertEqual(correction.status, GradeCorrectionRequest.Status.APPROVED)
        self.assertEqual(step.status, GradeCorrectionApprovalStep.Status.APPROVED)
        self.assertTrue(window.is_active)
        self.assertFalse(window.is_consumed)

    def test_approved_malformed_configured_snapshot_cannot_authorize_window(self):
        correction, _step, _item, _window = self._legacy_approved_request_with_window(
            approval_route=self.route_rule,
            step_role=self.cao_role,
        )

        is_valid, reason = GradingGovernanceService.get_approved_correction_governance_state(
            request_obj=correction
        )
        self.assertFalse(is_valid)
        self.assertIn("approval steps do not match", reason)
        self.assertIsNone(
            GradingGovernanceService.get_active_unlock_window(
                offering=self.offering,
                template_period=self.period,
            )
        )

    def test_approved_cross_campus_snapshot_cannot_unlock_encode_or_finalize(self):
        correction = self._approved_configured_request_with_window()
        other_campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other Campus")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="OTHER-CS",
            name="Other Computer Studies",
        )
        correction.faculty_department = other_department
        correction.save(update_fields=["faculty_department", "updated_at"])

        is_valid, reason = GradingGovernanceService.get_approved_correction_governance_state(
            request_obj=correction
        )
        self.assertFalse(is_valid)
        self.assertIn("outside the offering scope", reason)
        self.assertIsNone(
            GradingGovernanceService.get_active_unlock_window(
                offering=self.offering,
                template_period=self.period,
            )
        )
        with self.assertRaisesMessage(ValidationError, "locked by academic governance"):
            GradingGovernanceService.assert_encoding_allowed(
                offering=self.offering,
                template_period=self.period,
                student_id=self.student1.id,
                activity_id=self.activity.id,
                requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
            )
        with self.assertRaisesMessage(ValidationError, "outside the offering scope"):
            GradingGovernanceService.close_correction_window(request_obj=correction)
        self._grant_faculty_correction_access()
        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_correction_finalize",
                args=[self.offering.id, self.period.id, correction.id],
            ),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No active correction window to finalize.")
        self.assertFalse(
            StudentPeriodGrade.objects.filter(offering=self.offering, template_period=self.period).exists()
        )
        self.assertFalse(StudentFinalGrade.objects.filter(offering=self.offering).exists())
        self.assertEqual(
            StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True).raw_score,
            Decimal("30.00"),
        )

    def test_approved_same_campus_wrong_home_snapshot_fails_closed(self):
        correction = self._approved_configured_request_with_window()
        wrong_home = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BA",
            name="Business Administration",
        )
        correction.faculty_department = wrong_home
        correction.save(update_fields=["faculty_department", "updated_at"])

        is_valid, reason = GradingGovernanceService.get_approved_correction_governance_state(
            request_obj=correction
        )
        self.assertFalse(is_valid)
        self.assertIn("no longer matches", reason)
        self.assertIsNone(
            GradingGovernanceService.get_active_unlock_window(
                offering=self.offering,
                template_period=self.period,
            )
        )

    def test_approved_cross_tenant_or_inactive_snapshot_fails_closed(self):
        correction = self._approved_configured_request_with_window()
        other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER", name="Other Campus")
        other_department = Department.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            code="CS",
            name="Computer Studies",
        )
        correction.faculty_department = other_department
        correction.save(update_fields=["faculty_department", "updated_at"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        correction.faculty_department = self.department
        correction.save(update_fields=["faculty_department", "updated_at"])
        self.department.is_active = False
        self.department.save(update_fields=["is_active", "updated_at"])
        is_valid, reason = GradingGovernanceService.get_approved_correction_governance_state(
            request_obj=correction
        )
        self.assertFalse(is_valid)
        self.assertIn("inactive or outside", reason)

    def test_approved_snapshot_fails_closed_when_current_home_is_missing_or_ambiguous(self):
        correction = self._approved_configured_request_with_window()
        faculty_home_role = UserRole.objects.get(
            user=self.faculty_user,
            role=self.faculty_role,
            campus=self.campus,
            department=self.department,
        )
        faculty_home_role.is_active = False
        faculty_home_role.save(update_fields=["is_active"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        faculty_home_role.is_active = True
        faculty_home_role.save(update_fields=["is_active"])
        alternate_home = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BSA",
            name="Accountancy",
        )
        UserRole.objects.create(
            user=self.faculty_user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=alternate_home,
        )
        is_valid, reason = GradingGovernanceService.get_approved_correction_governance_state(
            request_obj=correction
        )
        self.assertFalse(is_valid)
        self.assertIn("multiple active FACULTY", reason)

    def test_auto_lapse_skips_malformed_approved_history_and_lapses_valid_history(self):
        valid = self._approved_configured_request_with_window()
        valid_window = valid.unlock_window
        invalid, _step, _item, invalid_window = self._legacy_approved_request_with_window()
        elapsed = timezone.now() - timedelta(minutes=1)
        invalid_window.end_at = elapsed
        invalid_window.save(update_fields=["end_at", "updated_at"])
        valid_window.end_at = elapsed
        valid_window.save(update_fields=["end_at", "updated_at"])

        result = GradingGovernanceService.auto_lapse_expired_correction_windows(at=timezone.now())
        self.assertEqual(result["count"], 1)
        invalid.refresh_from_db()
        invalid_window.refresh_from_db()
        valid.refresh_from_db()
        valid_window.refresh_from_db()
        self.assertEqual(invalid.status, GradeCorrectionRequest.Status.APPROVED)
        self.assertTrue(invalid_window.is_active)
        self.assertFalse(invalid_window.is_consumed)
        self.assertEqual(valid.status, GradeCorrectionRequest.Status.LAPSED)
        self.assertFalse(valid_window.is_active)
        self.assertTrue(valid_window.is_consumed)

    def test_approved_history_with_reviewer_never_in_configured_role_cannot_unlock_or_encode(self):
        correction = self._approved_configured_request_with_window()
        step = correction.approval_steps.get()
        step.reviewed_by_user = self.cao_user
        step.save(update_fields=["reviewed_by_user", "updated_at"])
        correction.reviewed_by_user = self.cao_user
        correction.save(update_fields=["reviewed_by_user", "updated_at"])

        is_valid, reason = GradingGovernanceService.get_approved_correction_governance_state(
            request_obj=correction
        )
        self.assertFalse(is_valid)
        self.assertIn("reviewer authority", reason)
        self.assertIsNone(
            GradingGovernanceService.get_active_unlock_window(
                offering=self.offering,
                template_period=self.period,
            )
        )
        with self.assertRaisesMessage(ValidationError, "locked by academic governance"):
            GradingGovernanceService.assert_encoding_allowed(
                offering=self.offering,
                template_period=self.period,
                student_id=self.student1.id,
                activity_id=self.activity.id,
                requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
            )

    def test_auto_lapse_skips_history_with_unverified_reviewer_authority(self):
        correction = self._approved_configured_request_with_window()
        step = correction.approval_steps.get()
        step.reviewed_by_user = self.cao_user
        step.save(update_fields=["reviewed_by_user", "updated_at"])
        correction.reviewed_by_user = self.cao_user
        correction.save(update_fields=["reviewed_by_user", "updated_at"])
        window = correction.unlock_window
        window.end_at = timezone.now() - timedelta(minutes=1)
        window.save(update_fields=["end_at", "updated_at"])

        result = GradingGovernanceService.auto_lapse_expired_correction_windows(at=timezone.now())
        self.assertEqual(result["count"], 0)
        correction.refresh_from_db()
        window.refresh_from_db()
        self.assertEqual(correction.status, GradeCorrectionRequest.Status.APPROVED)
        self.assertTrue(window.is_active)
        self.assertFalse(window.is_consumed)

    def test_approved_history_fails_closed_when_audited_reviewer_scope_is_wrong(self):
        correction = self._approved_configured_request_with_window()
        step = correction.approval_steps.get()
        audit = AuditLog.objects.get(
            action="CORRECTION_APPROVAL_REVIEWED",
            entity_type="GradeCorrectionApprovalStep",
            entity_id=str(step.id),
        )
        evidence = dict(audit.metadata_json["reviewer_authority"])
        evidence["campus_id"] = Campus.objects.create(
            tenant=self.tenant,
            code="OTHER",
            name="Other Campus",
        ).id
        audit.metadata_json = {**audit.metadata_json, "reviewer_authority": evidence}
        audit.save(update_fields=["metadata_json"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )
        evidence["campus_id"] = self.campus.id
        evidence["scope_covers_faculty_department"] = False
        audit.metadata_json = {**audit.metadata_json, "reviewer_authority": evidence}
        audit.save(update_fields=["metadata_json"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

    def test_approval_authority_snapshot_binds_exact_request_step_and_audit_event(self):
        correction = self._approved_configured_request_with_window()
        step = correction.approval_steps.get()
        snapshot = GradeCorrectionApprovalAuthoritySnapshot.objects.get(approval_step=step)

        self.assertEqual(snapshot.correction_request_id, correction.id)
        self.assertEqual(snapshot.step_order, step.step_order)
        self.assertEqual(snapshot.reviewer_user_id, step.reviewed_by_user_id)
        self.assertEqual(snapshot.configured_approver_role_id, step.approver_role_id)
        self.assertEqual(snapshot.approval_route_id, correction.approval_route_id)
        self.assertEqual(snapshot.tenant_id, correction.tenant_id)
        self.assertEqual(snapshot.campus_id, correction.campus_id)
        self.assertEqual(snapshot.faculty_department_id, correction.faculty_department_id)
        self.assertEqual(snapshot.decided_at, step.reviewed_at)
        self.assertTrue(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        with self.assertRaisesMessage(ValidationError, "snapshots are immutable"):
            GradeCorrectionApprovalAuthoritySnapshot.objects.filter(pk=snapshot.pk).update(step_order=2)
        snapshot.step_order = 2
        with self.assertRaisesMessage(ValidationError, "snapshots are immutable"):
            snapshot.save()

    def test_approved_history_fails_closed_for_wrong_audit_request_or_step_binding(self):
        correction = self._approved_configured_request_with_window()
        step = correction.approval_steps.get()
        audit = GradeCorrectionApprovalAuthoritySnapshot.objects.get(
            approval_step=step
        ).approval_audit_log
        after_data = dict(audit.after_json)

        after_data["correction_request_id"] = correction.id + 1000
        audit.after_json = after_data
        audit.save(update_fields=["after_json"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        after_data["correction_request_id"] = correction.id
        after_data["approval_step_id"] = step.id + 1000
        audit.after_json = after_data
        audit.save(update_fields=["after_json"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        after_data["approval_step_id"] = step.id
        after_data["step_order"] = step.step_order + 1
        audit.after_json = after_data
        audit.save(update_fields=["after_json"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

    def test_approved_history_fails_closed_for_wrong_audit_actor_role_scope_or_timestamp(self):
        correction = self._approved_configured_request_with_window()
        step = correction.approval_steps.get()
        audit = GradeCorrectionApprovalAuthoritySnapshot.objects.get(
            approval_step=step
        ).approval_audit_log

        audit.actor_user = self.cao_user
        audit.save(update_fields=["actor_user"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        audit.actor_user = self.reviewer_user
        after_data = dict(audit.after_json)
        after_data["approver_role_id"] = self.cao_role.id
        audit.after_json = after_data
        audit.save(update_fields=["actor_user", "after_json"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        other_tenant = Tenant.objects.create(code="AUDIT-TENANT", name="Audit Tenant")
        other_campus = Campus.objects.create(tenant=other_tenant, code="AUDIT", name="Audit Campus")
        audit.tenant = other_tenant
        audit.campus = other_campus
        audit.save(update_fields=["tenant", "campus"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        audit.tenant = self.tenant
        audit.campus = self.campus
        after_data["approver_role_id"] = step.approver_role_id
        after_data["campus_id"] = Campus.objects.create(
            tenant=self.tenant,
            code="AUDIT-OTHER",
            name="Audit Other Campus",
        ).id
        audit.after_json = after_data
        audit.save(update_fields=["tenant", "campus", "after_json"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        after_data["campus_id"] = self.campus.id
        after_data["faculty_department_id"] = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="AUDIT-BA",
            name="Audit Business Administration",
        ).id
        audit.after_json = after_data
        audit.save(update_fields=["after_json"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

        after_data["faculty_department_id"] = self.department.id
        after_data["reviewed_at"] = (step.reviewed_at + timedelta(seconds=1)).isoformat()
        audit.after_json = after_data
        audit.save(update_fields=["after_json"])
        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

    def test_fabricated_or_missing_audit_event_cannot_replace_bound_snapshot_evidence(self):
        correction = self._approved_configured_request_with_window()
        step = correction.approval_steps.get()
        snapshot = GradeCorrectionApprovalAuthoritySnapshot.objects.get(approval_step=step)
        audit = snapshot.approval_audit_log
        after_data = {**audit.after_json, "correction_request_id": correction.id + 1000}
        audit.after_json = after_data
        audit.save(update_fields=["after_json"])
        AuditLog.objects.create(
            actor_user=self.reviewer_user,
            portal="ADMIN",
            action="CORRECTION_APPROVAL_REVIEWED",
            entity_type="GradeCorrectionApprovalStep",
            entity_id=str(step.id),
            tenant=self.tenant,
            campus=self.campus,
            after_json={**audit.after_json, "correction_request_id": correction.id},
            metadata_json={"reviewer_authority": dict(audit.metadata_json["reviewer_authority"])},
        )

        self.assertFalse(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )
        self.assertFalse(
            GradingGovernanceService._correction_approval_audit_matches(
                audit_row=None,
                snapshot=snapshot,
                request_obj=correction,
                step=step,
            )
        )
        with self.assertRaisesMessage(ValidationError, "snapshots are immutable"):
            GradeCorrectionApprovalAuthoritySnapshot.objects.filter(pk=snapshot.pk).update(
                approval_audit_log_id=audit.id + 1
            )

    def test_snapshotted_authority_survives_later_reviewer_scope_reassignment(self):
        correction = self._approved_configured_request_with_window()
        reviewer_assignment = UserRole.objects.get(
            user=self.reviewer_user,
            role=self.reviewer_role,
            tenant=self.tenant,
            campus=self.campus,
        )
        other_campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other Campus")
        other_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="BA",
            name="Business Administration",
        )
        reviewer_assignment.campus = other_campus
        reviewer_assignment.department = other_department
        reviewer_assignment.save(update_fields=["campus", "department"])

        self.assertTrue(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )

    def test_approved_history_fails_closed_for_on_behalf_self_approval(self):
        correction = self._approved_configured_request_with_window()
        correction.request_source = GradeCorrectionRequest.RequestSource.ADMIN_ON_BEHALF
        correction.initiated_by_user = self.reviewer_user
        correction.save(update_fields=["request_source", "initiated_by_user", "updated_at"])

        is_valid, reason = GradingGovernanceService.get_approved_correction_governance_state(
            request_obj=correction
        )
        self.assertFalse(is_valid)
        self.assertIn("self-approval", reason)

    def test_audited_historical_reviewer_authority_survives_later_role_deactivation(self):
        correction = self._approved_configured_request_with_window()
        reviewer_assignment = UserRole.objects.get(
            user=self.reviewer_user,
            role=self.reviewer_role,
            tenant=self.tenant,
            campus=self.campus,
        )
        reviewer_assignment.is_active = False
        reviewer_assignment.save(update_fields=["is_active"])

        self.assertTrue(
            GradingGovernanceService.get_approved_correction_governance_state(request_obj=correction)[0]
        )
        self.assertIsNotNone(
            GradingGovernanceService.get_active_unlock_window(
                offering=self.offering,
                template_period=self.period,
            )
        )

    def test_auto_lapse_skips_expired_approved_request_with_wrong_home_snapshot(self):
        correction = self._approved_configured_request_with_window()
        wrong_home = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BA",
            name="Business Administration",
        )
        correction.faculty_department = wrong_home
        correction.save(update_fields=["faculty_department", "updated_at"])
        window = correction.unlock_window
        window.end_at = timezone.now() - timedelta(minutes=1)
        window.save(update_fields=["end_at", "updated_at"])

        result = GradingGovernanceService.auto_lapse_expired_correction_windows(at=timezone.now())
        self.assertEqual(result["count"], 0)
        correction.refresh_from_db()
        window.refresh_from_db()
        self.assertEqual(correction.status, GradeCorrectionRequest.Status.APPROVED)
        self.assertTrue(window.is_active)
        self.assertFalse(window.is_consumed)

    def test_valid_configured_approved_request_allows_encoding_and_finalize(self):
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="Valid mixed correction follows the configured route.",
            items=[
                *self._correction_item(value="45"),
                {
                    "requested_action": GradeCorrectionRequestItem.RequestedAction.UPDATE_STATUS,
                    "student_id": self.student1.id,
                    "new_value": "REVIEWED",
                },
            ],
        )
        correction = GradingGovernanceService.review_correction_request(
            request_obj=correction,
            reviewer=self.reviewer_user,
            approved=True,
            review_remarks="Approved through configured route.",
        )

        self.assertEqual(correction.status, GradeCorrectionRequest.Status.APPROVED)
        self.assertEqual(
            GradingGovernanceService.get_active_unlock_window(
                offering=self.offering,
                template_period=self.period,
            ).correction_request_id,
            correction.id,
        )
        self.assertTrue(
            GradingGovernanceService.assert_encoding_allowed(
                offering=self.offering,
                template_period=self.period,
                student_id=self.student1.id,
                activity_id=self.activity.id,
                requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
            )
        )

        self._grant_faculty_correction_access()
        self.client.force_login(self.faculty_user)
        response = self.client.post(
            reverse(
                "faculty_portal:period_correction_finalize",
                args=[self.offering.id, self.period.id, correction.id],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correction finalized and period scope re-locked.")
        correction.refresh_from_db()
        correction.unlock_window.refresh_from_db()
        self.assertEqual(correction.status, GradeCorrectionRequest.Status.CLOSED)
        self.assertTrue(correction.unlock_window.is_consumed)
        self.assertTrue(
            StudentPeriodGrade.objects.filter(
                offering=self.offering,
                template_period=self.period,
                student=self.student1,
                is_finalized=True,
            ).exists()
        )
        self.assertTrue(
            StudentFinalGrade.objects.filter(
                offering=self.offering,
                student=self.student1,
                is_submitted=True,
            ).exists()
        )

    def test_untouched_synthetic_pending_route_is_reconciled_but_acted_route_is_not(self):
        synthetic = GradeCorrectionRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.faculty_user,
            initiated_by_user=self.faculty_user,
            faculty_department=None,
            approval_route=None,
            status=GradeCorrectionRequest.Status.PENDING,
            justification="Legacy synthetic route.",
        )
        synthetic_step = GradeCorrectionApprovalStep.objects.create(
            correction_request=synthetic,
            step_order=1,
            approver_role=self.cao_role,
            approver_label="Synthetic CAO",
            status=GradeCorrectionApprovalStep.Status.PENDING,
        )

        self.assertTrue(GradingGovernanceService.reconcile_pending_correction_route(request_obj=synthetic))
        synthetic.refresh_from_db()
        self.assertEqual(synthetic.faculty_department_id, self.department.id)
        self.assertEqual(synthetic.approval_route_id, self.route_rule.id)
        self.assertFalse(GradeCorrectionApprovalStep.objects.filter(id=synthetic_step.id).exists())
        self.assertTrue(
            AuditLog.objects.filter(action="RECONCILE_CORRECTION_ROUTE", entity_id=str(synthetic.id)).exists()
        )
        can_review, pending_step, reason = GradingGovernanceService.can_user_review_correction_request(
            request_obj=synthetic,
            user=self.reviewer_user,
        )
        self.assertTrue(can_review)
        self.assertIsNotNone(pending_step)
        self.assertIsNone(reason)
        self.assertFalse(GradingGovernanceService.reconcile_pending_correction_route(request_obj=synthetic))

        acted = GradeCorrectionRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.faculty_user,
            initiated_by_user=self.faculty_user,
            approval_route=None,
            status=GradeCorrectionRequest.Status.PENDING,
            justification="Acted synthetic route.",
        )
        acted_step = GradeCorrectionApprovalStep.objects.create(
            correction_request=acted,
            step_order=1,
            approver_role=self.cao_role,
            approver_label="Synthetic CAO",
            status=GradeCorrectionApprovalStep.Status.APPROVED,
        )
        self.assertFalse(GradingGovernanceService.reconcile_pending_correction_route(request_obj=acted))
        acted.refresh_from_db()
        acted_step.refresh_from_db()
        self.assertIsNone(acted.approval_route_id)
        self.assertEqual(acted_step.approver_label, "Synthetic CAO")

        request_level_acted = GradeCorrectionRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.faculty_user,
            initiated_by_user=self.faculty_user,
            approval_route=None,
            status=GradeCorrectionRequest.Status.PENDING,
            justification="Legacy request-level action evidence.",
            reviewed_by_user=self.reviewer_user,
            reviewed_at=timezone.now(),
            review_remarks="Do not rewrite this history.",
        )
        GradeCorrectionApprovalStep.objects.create(
            correction_request=request_level_acted,
            step_order=1,
            approver_role=self.cao_role,
            approver_label="Synthetic CAO",
            status=GradeCorrectionApprovalStep.Status.PENDING,
        )
        self.assertFalse(GradingGovernanceService.reconcile_pending_correction_route(request_obj=request_level_acted))

    def test_acted_null_route_synthetic_request_cannot_review_unlock_or_write_scores(self):
        review_permission, _ = Permission.objects.get_or_create(
            code="corrections.review",
            defaults={"module": "corrections", "action": "review"},
        )
        RolePermission.objects.get_or_create(role=self.reviewer_role, permission=review_permission)
        synthetic = GradeCorrectionRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.faculty_user,
            initiated_by_user=self.faculty_user,
            approval_route=None,
            status=GradeCorrectionRequest.Status.PENDING,
            justification="Acted legacy synthetic route must remain audit-only.",
            reviewed_by_user=self.cao_user,
            reviewed_at=timezone.now(),
        )
        GradeCorrectionApprovalStep.objects.create(
            correction_request=synthetic,
            step_order=1,
            approver_role=self.reviewer_role,
            approver_label="Synthetic reviewer",
            status=GradeCorrectionApprovalStep.Status.PENDING,
        )
        GradeCorrectionRequestItem.objects.create(
            correction_request=synthetic,
            requested_action=GradeCorrectionRequestItem.RequestedAction.UPDATE_SCORE,
            student=self.student1,
            grade_activity=self.activity,
            old_value="30",
            new_value="45",
        )

        can_review, pending_step, reason = GradingGovernanceService.can_user_review_correction_request(
            request_obj=synthetic,
            user=self.reviewer_user,
        )
        self.assertFalse(can_review)
        self.assertIsNone(pending_step)
        self.assertIn("no configured approval route snapshot", reason)
        with self.assertRaisesMessage(ValidationError, "no configured approval route snapshot"):
            GradingGovernanceService.review_correction_request(
                request_obj=synthetic,
                reviewer=self.reviewer_user,
                approved=True,
            )
        synthetic.refresh_from_db()
        self.assertEqual(synthetic.status, GradeCorrectionRequest.Status.PENDING)
        self.assertFalse(GradeCorrectionUnlockWindow.objects.filter(correction_request=synthetic).exists())
        self.assertEqual(
            StudentActivityScore.objects.get(activity=self.activity, student=self.student1, is_active=True).raw_score,
            Decimal("30.00"),
        )

    def test_inactive_configured_step_role_cannot_authorize_review_even_with_review_permission(self):
        review_permission, _ = Permission.objects.get_or_create(
            code="corrections.review",
            defaults={"module": "corrections", "action": "review"},
        )
        RolePermission.objects.get_or_create(role=self.reviewer_role, permission=review_permission)
        correction = GradingGovernanceService.create_correction_request(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            justification="The route role is later deactivated.",
            items=self._correction_item(),
        )
        self.reviewer_role.is_active = False
        self.reviewer_role.save(update_fields=["is_active", "updated_at"])

        can_review, pending_step, reason = GradingGovernanceService.can_user_review_correction_request(
            request_obj=correction,
            user=self.reviewer_user,
        )
        self.assertFalse(can_review)
        self.assertIsNone(pending_step)
        self.assertIn("inactive or invalid approver role", reason)

    def test_published_canonical_period_resolver_deduplicates_and_rejects_forged_period(self):
        duplicate_template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TEMP2",
            name="Duplicate Prelim Template",
            is_published=True,
        )
        duplicate_prelim = GradingTemplatePeriod.objects.create(
            template=duplicate_template,
            code="PRE-FINAL",
            name="Pre Final",
            sequence_no=3,
        )
        unpublished_template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="DRAFT",
            name="Draft Template",
            is_published=False,
        )
        GradingTemplatePeriod.objects.create(
            template=unpublished_template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        second_prelim = GradingTemplatePeriod.objects.create(
            template=duplicate_template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )

        configurable_periods = GradingGovernanceService.eligible_configurable_correction_periods(tenant_id=self.tenant.id)
        self.assertEqual(
            [GradingGovernanceService.canonical_correction_period_key(period) for period in configurable_periods],
            ["PRELIM"],
        )
        self.assertEqual(
            [period.id for period in GradingGovernanceService.eligible_correction_periods_for_offering(offering=self.offering)],
            [self.period.id],
        )
        with self.assertRaisesMessage(ValidationError, "not an eligible published grading period"):
            GradingGovernanceService.create_correction_request(
                user=self.faculty_user,
                offering=self.offering,
                template_period=second_prelim,
                justification="Forged template period.",
                items=self._correction_item(),
            )
        self.assertNotEqual(duplicate_prelim.id, second_prelim.id)

    def test_policy_resolution_uses_canonical_period_and_broad_scope(self):
        alias_template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="ALIAS",
            name="Alias Template",
            is_published=True,
        )
        alias_period = GradingTemplatePeriod.objects.create(
            template=alias_template,
            code="PRE-FINAL",
            name="Pre Final",
            sequence_no=3,
        )
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=None,
            academic_year=None,
            term=None,
            grading_period=alias_period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.CLOSED,
            is_active=True,
        )
        self.period.code = "PREFINAL"
        self.period.save(update_fields=["code", "updated_at"])
        self.correction_lock.period_code = "PREFINAL"
        self.correction_lock.save(update_fields=["period_code", "updated_at"])

        state = GradingGovernanceService.get_correction_petition_window_state(
            offering=self.offering,
            template_period=self.period,
        )

        self.assertFalse(state["is_allowed"])
        self.assertEqual(state["policy"].id, CorrectionPetitionWindowPolicy.objects.get(grading_period=alias_period).id)

    def test_correction_lifecycle_allows_submitted_post_deadline_read_only_gradebook_without_physical_lock(self):
        self._set_correction_lifecycle(deadline_at=timezone.now() - timedelta(hours=1), is_locked=False)
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
            is_active=True,
        )

        lifecycle_state = GradingGovernanceService.get_correction_request_lifecycle_state(
            offering=self.offering,
            template_period=self.period,
        )
        filing_state = GradingGovernanceService.get_correction_request_filing_state(
            offering=self.offering,
            template_period=self.period,
        )

        self.assertTrue(lifecycle_state["is_submitted"])
        self.assertTrue(lifecycle_state["is_post_deadline"])
        self.assertTrue(lifecycle_state["is_locked"])
        self.assertFalse(lifecycle_state["is_editable"])
        self.assertTrue(filing_state["is_allowed"])

    def test_correction_lifecycle_denies_before_deadline(self):
        self._set_correction_lifecycle(deadline_at=timezone.now() + timedelta(hours=1), is_locked=False)
        state = GradingGovernanceService.get_correction_request_lifecycle_state(
            offering=self.offering,
            template_period=self.period,
        )
        self.assertFalse(state["is_allowed"])
        self.assertTrue(
            GradingGovernanceService.can_faculty_self_reopen_before_deadline(
                offering=self.offering,
                template_period=self.period,
            )
        )
        with self.assertRaisesMessage(ValidationError, "become available after the submission deadline"):
            GradingGovernanceService.create_correction_request(
                user=self.faculty_user,
                offering=self.offering,
                template_period=self.period,
                justification="Too early.",
                items=self._correction_item(),
            )

    def test_correction_lifecycle_denies_active_reopen_window_after_deadline(self):
        self._set_correction_lifecycle(
            deadline_at=timezone.now() - timedelta(hours=1),
            is_locked=False,
            submission_status=GradeSubmission.Status.REOPENED,
        )
        submission = GradingGovernanceService.get_submission(
            offering=self.offering,
            template_period=self.period,
        )
        GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=submission,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.faculty_user,
            status=GradeSubmissionReopenRequest.Status.APPROVED,
            justification="Approved deadline reopen.",
            reviewed_by_user=self.reviewer_user,
            reviewed_at=timezone.now(),
        )

        state = GradingGovernanceService.get_correction_request_lifecycle_state(
            offering=self.offering,
            template_period=self.period,
        )
        self.assertFalse(state["is_allowed"])
        self.assertTrue(state["is_editable"])

    def test_correction_filing_denies_closed_petition_window(self):
        self._set_correction_lifecycle(deadline_at=timezone.now() - timedelta(hours=1), is_locked=False)
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.CLOSED,
            is_active=True,
        )

        filing_state = GradingGovernanceService.get_correction_request_filing_state(
            offering=self.offering,
            template_period=self.period,
        )

        self.assertTrue(filing_state["lifecycle_state"]["is_allowed"])
        self.assertFalse(filing_state["petition_window_state"]["is_allowed"])
        self.assertFalse(filing_state["is_allowed"])

    def test_correction_request_is_visible_after_deadline_for_read_only_offering_with_open_petition_window(self):
        self._grant_faculty_correction_access()
        self._set_correction_lifecycle(deadline_at=timezone.now() - timedelta(hours=1), is_locked=True)
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.DAYS_AFTER_PERIOD_END,
            allowed_days_after_period_end=5,
            is_active=True,
        )
        self._set_offering_outside_active_scope()
        self.client.force_login(self.faculty_user)

        summary_response = self.client.get(
            reverse("faculty_portal:period_summary", args=[self.offering.id, self.period.id])
        )
        periods_response = self.client.get(reverse("faculty_portal:offering_periods", args=[self.offering.id]))

        self.assertEqual(summary_response.status_code, 200)
        self.assertTrue(summary_response.context["offering"].faculty_is_read_only)
        self.assertTrue(summary_response.context["can_access_corrections"])
        self.assertContains(summary_response, "Correction Requests")
        self.assertEqual(periods_response.status_code, 200)
        self.assertTrue(periods_response.context["period_cards"][0]["is_read_only_class"])
        self.assertTrue(periods_response.context["period_cards"][0]["can_access_corrections"])
        self.assertContains(periods_response, 'aria-label="Corrections"')

    def test_correction_request_is_hidden_before_deadline_while_self_reopen_is_available(self):
        self._grant_faculty_correction_access()
        self._set_correction_lifecycle(deadline_at=timezone.now() + timedelta(hours=1), is_locked=False)
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.OPEN_ANYTIME,
            is_active=True,
        )
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:period_summary", args=[self.offering.id, self.period.id]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_self_reopen"])
        self.assertFalse(response.context["can_access_corrections"])
        self.assertNotContains(response, "Correction Requests")

    def test_correction_request_is_hidden_when_petition_window_is_closed(self):
        self._grant_faculty_correction_access()
        self._set_correction_lifecycle(deadline_at=timezone.now() - timedelta(hours=1), is_locked=True)
        CorrectionPetitionWindowPolicy.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            grading_period=self.period,
            policy_mode=CorrectionPetitionWindowPolicy.PolicyMode.CLOSED,
            is_active=True,
        )
        self.client.force_login(self.faculty_user)

        response = self.client.get(reverse("faculty_portal:period_summary", args=[self.offering.id, self.period.id]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_access_corrections"])
        self.assertNotContains(response, "Correction Requests")

    def test_period_corrections_direct_get_and_post_are_denied_before_deadline(self):
        self._grant_faculty_correction_access()
        self._set_correction_lifecycle(deadline_at=timezone.now() + timedelta(hours=1), is_locked=False)
        self.client.force_login(self.faculty_user)
        url = reverse("faculty_portal:period_corrections", args=[self.offering.id, self.period.id])

        get_response = self.client.get(url, follow=True)
        post_response = self.client.post(
            url,
            {
                "students": [self.student1.id],
                "grade_activities": [self.activity.id],
                "correction_payload": (
                    f'[{{"student_id":"{self.student1.id}","grade_activity_id":"{self.activity.id}","new_value":"41"}}]'
                ),
                "justification": "Must be denied before the deadline.",
            },
            follow=True,
        )

        expected_message = "Grade correction requests become available after the submission deadline once this grading period is locked."
        self.assertContains(get_response, expected_message)
        self.assertContains(post_response, expected_message)
        self.assertEqual(GradeCorrectionRequest.objects.count(), 0)


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

    def _create_profile(self, *, code, term_type=None, priority=100, course=None, is_default=False):
        return TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            course=course,
            term_type=term_type,
            profile_code=code,
            profile_name=code,
            grading_template=self.template,
            priority=priority,
            is_default=is_default,
            is_active=True,
        )

    def _create_offering_for_term(self, term):
        return CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=term,
            course=self.course,
            section=self.section,
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

    def test_regular_four_period_template_final_grade_fallback_divides_by_four(self):
        self._create_period_grade(self.prelim, "80.00")
        self._create_period_grade(self.midterm, "84.00")
        self._create_period_grade(self.prefinal, "88.00")
        self._create_period_grade(self.final_period, "92.00")

        FacultyGradingService.recompute_final_grades_from_stored_periods(
            user=self.faculty_user,
            offering=self.offering,
            template=self.template,
        )

        final_grade = StudentFinalGrade.objects.get(offering=self.offering, student=self.student)
        self.assertEqual(final_grade.final_grade, Decimal("86.00"))

    def test_exact_term_assignment_overrides_default_for_no_data_offering(self):
        summer_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="SUMMER",
            name="Summer",
            sequence_no=3,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 31),
        )
        summer_offering = self._create_offering_for_term(summer_term)
        summer_template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP-SUMMER",
            name="Summer Template",
            is_published=True,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=summer_template,
            effective_from_term=summer_term,
            is_active=True,
        )

        resolved = FacultyGradingService.resolve_template_for_offering(summer_offering)

        self.assertEqual(resolved, summer_template)

    def test_summer_three_period_template_final_grade_fallback_divides_by_three(self):
        summer_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="SUMMER-3P",
            name="Summer 3 Period",
            sequence_no=4,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 31),
        )
        summer_offering = self._create_offering_for_term(summer_term)
        Enrollment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=summer_term,
            course_offering=summer_offering,
            student=self.student,
            enrollment_status=Enrollment.Status.ACTIVE,
            is_active=True,
        )
        summer_template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TMP-SUMMER-3P",
            name="Summer 3 Period Template",
            is_published=True,
            is_active=True,
        )
        summer_midterm = GradingTemplatePeriod.objects.create(
            template=summer_template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=1,
            is_active=True,
        )
        summer_prefinal = GradingTemplatePeriod.objects.create(
            template=summer_template,
            code="PREFINAL",
            name="Pre-Final",
            sequence_no=2,
            is_active=True,
        )
        summer_final = GradingTemplatePeriod.objects.create(
            template=summer_template,
            code="FINAL",
            name="Final",
            sequence_no=3,
            is_active=True,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=summer_template,
            effective_from_term=summer_term,
            is_active=True,
        )
        for period, value in [
            (summer_midterm, "90.00"),
            (summer_prefinal, "87.00"),
            (summer_final, "84.00"),
        ]:
            StudentPeriodGrade.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=summer_offering,
                template_period=period,
                student=self.student,
                period_grade=Decimal(value),
                class_standing_grade=Decimal(value),
                exam_grade=Decimal(value),
                computed_by_user=self.faculty_user,
                is_finalized=True,
            )

        FacultyGradingService.recompute_final_grades_from_stored_periods(
            user=self.faculty_user,
            offering=summer_offering,
            template=summer_template,
        )

        final_grade = StudentFinalGrade.objects.get(offering=summer_offering, student=self.student)
        self.assertEqual(final_grade.final_grade, Decimal("87.00"))

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

    def test_deped_period_formula_uses_raw_component_totals_then_transmutation(self):
        TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            profile_code="DEPED-G1",
            profile_name="DepEd Grade 1",
            grading_template=self.template,
            period_grade_formula_mode=TenantGradingProfile.PeriodGradeFormulaMode.DEPED_TRANSMUTATION,
            period_grade_formula_json={
                "transmutation_table": FacultyGradingService.DEFAULT_DEPED_TRANSMUTATION_TABLE,
            },
            is_default=True,
            is_active=True,
        )
        written = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="WW",
            name="Written Works",
            weight_percentage=Decimal("30.00"),
            sort_order=1,
            is_active=True,
        )
        performance = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="PT",
            name="Performance Tasks",
            weight_percentage=Decimal("50.00"),
            sort_order=2,
            is_active=True,
        )
        quarterly = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="QA",
            name="Quarterly Assessment",
            weight_percentage=Decimal("20.00"),
            sort_order=3,
            is_exam_component=True,
            is_active=True,
        )

        def add_score(component, title, raw, total):
            activity = GradeActivity.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=self.offering,
                template_period=self.prelim,
                template_component=component,
                title=title,
                total_score=Decimal(total),
                created_by_user=self.faculty_user,
                is_active=True,
            )
            StudentActivityScore.objects.create(
                activity=activity,
                student=self.student,
                raw_score=Decimal(raw),
                computed_score=Decimal("0.00"),
                encoded_by_user=self.faculty_user,
                is_active=True,
            )

        add_score(written, "WW1", "15.00", "20.00")
        add_score(written, "WW2", "24.00", "30.00")
        add_score(performance, "PT1", "45.00", "50.00")
        add_score(quarterly, "QA1", "38.00", "40.00")

        result = FacultyGradingService.recompute_period_summary(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
        )

        row = StudentPeriodGrade.objects.get(offering=self.offering, student=self.student, template_period=self.prelim)
        self.assertEqual(result["rows"][0]["component_scores"]["WW"], Decimal("78.00"))
        self.assertEqual(result["rows"][0]["component_scores"]["PT"], Decimal("90.00"))
        self.assertEqual(result["rows"][0]["component_scores"]["QA"], Decimal("95.00"))
        self.assertEqual(result["rows"][0]["period_grade_raw"], Decimal("87.40"))
        self.assertEqual(row.period_grade, Decimal("92"))

    def test_subcomponent_can_average_faculty_activities_instead_of_detail_weights(self):
        component = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )
        subcomponent = GradingTemplateSubcomponent.objects.create(
            template_component=component,
            code="OUTPUTS",
            name="Participation/Output",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )
        recitation = GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="RECITATION",
            name="Recitation",
            weight_percentage=Decimal("40.00"),
            sort_order=1,
            is_active=True,
        )
        assignment = GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="ASSIGNMENT",
            name="Assignment",
            weight_percentage=Decimal("30.00"),
            sort_order=2,
            is_active=True,
        )
        activity_detail = GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="ACTIVITY",
            name="Activity",
            weight_percentage=Decimal("30.00"),
            sort_order=3,
            is_active=True,
        )

        def add_detail_score(detail, title, computed_score):
            activity = GradeActivity.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                offering=self.offering,
                template_period=self.prelim,
                template_component=component,
                template_subcomponent=subcomponent,
                template_detail=detail,
                title=title,
                total_score=Decimal("100.00"),
                created_by_user=self.faculty_user,
                is_active=True,
            )
            StudentActivityScore.objects.create(
                activity=activity,
                student=self.student,
                raw_score=computed_score,
                computed_score=computed_score,
                encoded_by_user=self.faculty_user,
                is_active=True,
            )

        add_detail_score(recitation, "Recitation 1", Decimal("100.00"))
        add_detail_score(recitation, "Recitation 2", Decimal("100.00"))
        add_detail_score(assignment, "Assignment 1", Decimal("50.00"))
        add_detail_score(activity_detail, "Seatwork 1", Decimal("50.00"))

        weighted_result = FacultyGradingService.recompute_period_summary(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
        )
        weighted_row = StudentPeriodGrade.objects.get(
            offering=self.offering,
            student=self.student,
            template_period=self.prelim,
        )

        self.assertEqual(weighted_result["rows"][0]["period_grade_raw"], Decimal("70.00"))
        self.assertEqual(weighted_row.period_grade, Decimal("70"))

        subcomponent.detail_computation_mode = DetailComputationMode.AVERAGE_ACTIVITIES
        subcomponent.save(update_fields=["detail_computation_mode", "updated_at"])

        average_result = FacultyGradingService.recompute_period_summary(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
        )
        average_row = StudentPeriodGrade.objects.get(
            offering=self.offering,
            student=self.student,
            template_period=self.prelim,
        )

        self.assertEqual(average_result["rows"][0]["period_grade_raw"], Decimal("75.00"))
        self.assertEqual(average_row.period_grade, Decimal("75"))

    def test_regular_term_selects_regular_profile_over_general_profile(self):
        fallback = self._create_profile(code="GENERAL", priority=1, is_default=True)
        regular = self._create_profile(
            code="REGULAR",
            term_type=TenantGradingProfile.TermType.REGULAR,
            priority=100,
        )

        resolved = FacultyGradingService.resolve_grading_profile_for_offering(self.offering)

        self.assertEqual(resolved, regular)
        self.assertNotEqual(resolved, fallback)

    def test_summer_term_selects_summer_profile_over_general_profile(self):
        summer_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="SUMMER",
            name="Summer",
            term_type=Term.TermType.SUMMER,
            sequence_no=3,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 31),
        )
        summer_offering = self._create_offering_for_term(summer_term)
        fallback = self._create_profile(code="GENERAL", priority=1, is_default=True)
        summer = self._create_profile(
            code="SUMMER-PROFILE",
            term_type=TenantGradingProfile.TermType.SUMMER,
            priority=100,
        )

        resolved = FacultyGradingService.resolve_grading_profile_for_offering(summer_offering)

        self.assertEqual(resolved, summer)
        self.assertNotEqual(resolved, fallback)

    def test_profile_resolution_falls_back_to_general_when_no_term_type_matches(self):
        summer_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="SUMMER",
            name="Summer",
            term_type=Term.TermType.SUMMER,
            sequence_no=3,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 31),
        )
        summer_offering = self._create_offering_for_term(summer_term)
        fallback = self._create_profile(code="GENERAL", priority=100, is_default=True)
        self._create_profile(
            code="REGULAR",
            term_type=TenantGradingProfile.TermType.REGULAR,
            priority=1,
        )

        resolved = FacultyGradingService.resolve_grading_profile_for_offering(summer_offering)

        self.assertEqual(resolved, fallback)

    def test_profile_specificity_still_overrides_term_type(self):
        summer_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="SUMMER",
            name="Summer",
            term_type=Term.TermType.SUMMER,
            sequence_no=3,
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 31),
        )
        summer_offering = self._create_offering_for_term(summer_term)
        summer = self._create_profile(
            code="SUMMER-PROFILE",
            term_type=TenantGradingProfile.TermType.SUMMER,
            priority=1,
        )
        course_specific = self._create_profile(
            code="COURSE-SPECIFIC",
            course=self.course,
            priority=100,
        )

        resolved = FacultyGradingService.resolve_grading_profile_for_offering(summer_offering)

        self.assertEqual(resolved, course_specific)
        self.assertNotEqual(resolved, summer)

    def test_profile_priority_still_applies_with_same_specificity(self):
        slower = self._create_profile(
            code="REGULAR-SLOWER",
            term_type=TenantGradingProfile.TermType.REGULAR,
            priority=20,
        )
        faster = self._create_profile(
            code="REGULAR-FASTER",
            term_type=TenantGradingProfile.TermType.REGULAR,
            priority=10,
        )

        resolved = FacultyGradingService.resolve_grading_profile_for_offering(self.offering)

        self.assertEqual(resolved, faster)
        self.assertNotEqual(resolved, slower)

    def test_parent_department_profile_applies_to_child_offering(self):
        parent_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.department.parent = parent_department
        self.department.save(update_fields=["parent", "updated_at"])
        parent_profile = TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=parent_department,
            profile_code="PARENT",
            profile_name="Parent Department Profile",
            grading_template=self.template,
            priority=100,
            is_active=True,
        )

        resolved = FacultyGradingService.resolve_grading_profile_for_offering(self.offering)

        self.assertEqual(resolved, parent_profile)

    def test_parent_department_profile_from_other_campus_does_not_apply_to_child_offering(self):
        other_campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other Campus")
        other_parent_department = Department.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            code="COLLEGE",
            name="Other Campus College",
            unit_type=Department.UnitType.DIVISION,
        )
        TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=other_campus,
            department=other_parent_department,
            profile_code="OTHER-PARENT",
            profile_name="Other Parent Department Profile",
            grading_template=self.template,
            priority=1,
            is_active=True,
        )

        resolved = FacultyGradingService.resolve_grading_profile_for_offering(self.offering)

        self.assertIsNone(resolved)

    def test_child_department_profile_wins_over_parent_department_profile(self):
        parent_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        self.department.parent = parent_department
        self.department.save(update_fields=["parent", "updated_at"])
        parent_profile = TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=parent_department,
            profile_code="PARENT",
            profile_name="Parent Department Profile",
            grading_template=self.template,
            priority=1,
            is_active=True,
        )
        child_profile = TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            profile_code="CHILD",
            profile_name="Child Department Profile",
            grading_template=self.template,
            priority=100,
            is_active=True,
        )

        resolved = FacultyGradingService.resolve_grading_profile_for_offering(self.offering)

        self.assertEqual(resolved, child_profile)
        self.assertNotEqual(resolved, parent_profile)

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


class GradeExplanationServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="TEN", name="Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
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
            username="facultyx",
            email="facultyx@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        faculty_access = Permission.objects.get(code="faculty_portal.access")
        RolePermission.objects.create(role=self.faculty_role, permission=faculty_access)
        UserRole.objects.create(
            user=self.faculty_user,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty_user,
            is_primary=True,
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="TEMP",
            name="Standard Template",
            default_base_value=Decimal("50.00"),
            passing_grade_threshold=Decimal("75.00"),
            is_published=True,
        )
        self.prelim = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        self.midterm = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
        )
        self.prefinal = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PREFINAL",
            name="Pre-Final",
            sequence_no=3,
        )
        self.final_period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="FINAL",
            name="Final",
            sequence_no=4,
        )
        self.class_standing = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("60.00"),
            sort_order=1,
        )
        self.exam = GradingTemplateComponent.objects.create(
            template_period=self.prelim,
            code="EXAM",
            name="Exam",
            weight_percentage=Decimal("40.00"),
            sort_order=2,
            is_exam_component=True,
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
            student_no="2025-100",
            last_name="Rizal",
            first_name="Jose",
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

    def _activity_with_score(self, *, component, raw, total=Decimal("100.00"), title="Activity"):
        activity = GradeActivity.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            template_component=component,
            title=title,
            total_score=total,
            activity_date=date(2025, 7, 1),
            created_by_user=self.faculty_user,
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=self.student,
            raw_score=Decimal(raw),
            computed_score=FacultyGradingService.compute_activity_score(
                raw_score=Decimal(raw),
                total_score=total,
                base_value=Decimal("50.00"),
            ),
            encoded_by_user=self.faculty_user,
        )
        return activity

    def test_period_explanation_uses_official_component_path(self):
        self._activity_with_score(component=self.class_standing, raw="80.00", title="Quiz")
        self._activity_with_score(component=self.exam, raw="75.00", title="Prelim Exam")
        FacultyGradingService.recompute_period_summary(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
            audit_reason=None,
        )

        explanation = GradeExplanationService.build(
            offering=self.offering,
            student=self.student,
            template_period=self.prelim,
            grade_type=GradeExplanationService.GRADE_TYPE_PERIOD,
        )

        stored = StudentPeriodGrade.objects.get(offering=self.offering, student=self.student, template_period=self.prelim)
        self.assertEqual(explanation["official_value"], stored.period_grade)
        self.assertEqual(explanation["computed_official_value"], stored.period_grade)
        self.assertEqual(explanation["base_value"]["source"], "template_default")
        self.assertEqual(len(explanation["component_breakdown"]), 2)

    def test_explanation_reports_profile_threshold_source(self):
        TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            profile_code="PROFILE-THRESHOLD",
            profile_name="Profile Threshold",
            grading_template=self.template,
            passing_grade_threshold=Decimal("78.00"),
            is_active=True,
        )

        explanation = GradeExplanationService.build(
            offering=self.offering,
            student=self.student,
            template_period=self.prelim,
            grade_type=GradeExplanationService.GRADE_TYPE_PERIOD,
        )

        self.assertEqual(explanation["passing_threshold"]["source"], "tenant_grading_profile")
        self.assertEqual(explanation["passing_threshold"]["value"], Decimal("78.00"))

    def test_explanation_reports_course_base_value_override_source(self):
        CourseBaseValueOverride.objects.create(
            course=self.course,
            effective_from_term=self.term,
            base_value=Decimal("60.00"),
        )

        explanation = GradeExplanationService.build(
            offering=self.offering,
            student=self.student,
            template_period=self.prelim,
            grade_type=GradeExplanationService.GRADE_TYPE_PERIOD,
        )

        self.assertEqual(explanation["base_value"]["source"], "course_override")
        self.assertEqual(explanation["base_value"]["value"], Decimal("60.00"))

    def test_final_explanation_shows_average_missing_period_behavior(self):
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            student=self.student,
            period_grade=Decimal("92.00"),
            computed_by_user=self.faculty_user,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.midterm,
            student=self.student,
            period_grade=Decimal("88.00"),
            computed_by_user=self.faculty_user,
        )
        FacultyGradingService.recompute_final_grades_from_stored_periods(
            user=self.faculty_user,
            offering=self.offering,
            template=self.template,
        )

        explanation = GradeExplanationService.build(
            offering=self.offering,
            student=self.student,
            grade_type=GradeExplanationService.GRADE_TYPE_FINAL,
        )

        self.assertEqual(explanation["official_value"], Decimal("45.00"))
        self.assertEqual(explanation["final_formula"]["mode"], TenantGradingProfile.FinalGradeFormulaMode.AVERAGE_ACTIVE_PERIODS)
        self.assertTrue(any("included as 0" in warning for warning in explanation["warnings"]))

    def test_final_explanation_uses_weighted_profile_source(self):
        TenantGradingProfile.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            profile_code="WEIGHTED",
            profile_name="Weighted",
            grading_template=self.template,
            final_grade_formula_mode=TenantGradingProfile.FinalGradeFormulaMode.WEIGHTED_PERIODS,
            final_grade_formula_json={
                "period_weights": [
                    {"period_code": "PRELIM", "weight": "50.00"},
                    {"period_code": "MIDTERM", "weight": "50.00"},
                ]
            },
            is_active=True,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            student=self.student,
            period_grade=Decimal("90.00"),
            computed_by_user=self.faculty_user,
        )
        StudentPeriodGrade.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.midterm,
            student=self.student,
            period_grade=Decimal("80.00"),
            computed_by_user=self.faculty_user,
        )
        FacultyGradingService.recompute_final_grades_from_stored_periods(
            user=self.faculty_user,
            offering=self.offering,
            template=self.template,
        )

        explanation = GradeExplanationService.build(
            offering=self.offering,
            student=self.student,
            grade_type=GradeExplanationService.GRADE_TYPE_FINAL,
        )

        self.assertEqual(explanation["official_value"], Decimal("85.00"))
        self.assertEqual(explanation["final_formula"]["source"], "tenant_grading_profile")

    def test_attendance_detail_explains_status_mapping(self):
        attendance_sub = GradingTemplateSubcomponent.objects.create(
            template_component=self.class_standing,
            code="ATT",
            name="Attendance",
            weight_percentage=Decimal("100.00"),
            is_attendance_component=True,
            sort_order=1,
        )
        AttendanceSession.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            session_date=date(2025, 7, 1),
            title="Meeting 1",
        )
        late_session = AttendanceSession.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            session_date=date(2025, 7, 2),
            title="Meeting 2",
        )
        AttendanceRecord.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            session=late_session,
            student=self.student,
            status_code=AttendanceRecord.Status.LATE,
            recorded_by_user=self.faculty_user,
        )

        explanation = GradeExplanationService.build(
            offering=self.offering,
            student=self.student,
            template_period=self.prelim,
            grade_type=GradeExplanationService.GRADE_TYPE_PERIOD,
        )

        attendance_rows = explanation["component_breakdown"][0]["subcomponents"][0]["attendance_records"]
        self.assertEqual(attendance_rows[1]["mapped_score"], Decimal("90"))
        self.assertTrue(attendance_rows[0]["missing"])
        self.assertEqual(attendance_sub.name, "Attendance")

    def test_correction_history_marks_correction_affected_grade(self):
        activity = self._activity_with_score(component=self.class_standing, raw="80.00", title="Quiz")
        correction = GradeCorrectionRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.prelim,
            requested_by_user=self.faculty_user,
            status=GradeCorrectionRequest.Status.CLOSED,
            justification="Correct encoded score.",
            reviewed_by_user=self.faculty_user,
            reviewed_at=timezone.now(),
        )
        GradeCorrectionRequestItem.objects.create(
            correction_request=correction,
            student=self.student,
            grade_activity=activity,
            old_value="80.00",
            new_value="90.00",
        )

        explanation = GradeExplanationService.build(
            offering=self.offering,
            student=self.student,
            template_period=self.prelim,
            grade_type=GradeExplanationService.GRADE_TYPE_PERIOD,
        )

        self.assertEqual(len(explanation["correction_history"]), 1)
        self.assertTrue(any("Approved correction history exists" in warning for warning in explanation["warnings"]))
        self.assertEqual(explanation["correction_history"][0]["old_value"], "80.00")
        self.assertEqual(explanation["correction_history"][0]["new_value"], "90.00")

    def test_faculty_explanation_view_logs_audit_event(self):
        self._activity_with_score(component=self.class_standing, raw="90.00", title="Quiz")
        FacultyGradingService.recompute_period_summary(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.prelim,
            audit_reason=None,
        )
        self.client.force_login(self.faculty_user)
        url = reverse(
            "faculty_portal:grade_explanation",
            kwargs={
                "offering_id": self.offering.id,
                "period_id": self.prelim.id,
                "student_id": self.student.id,
                "grade_type": GradeExplanationService.GRADE_TYPE_PERIOD,
            },
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                action="READ",
                entity_type="GradeExplanation",
                metadata_json__grade_type=GradeExplanationService.GRADE_TYPE_PERIOD,
            ).exists()
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

    def test_assert_encoding_blocks_overdue_unsubmitted_period_until_reopen_request_is_approved(self):
        with self.assertRaises(ValidationError):
            GradingGovernanceService.assert_encoding_allowed(
                offering=self.offering,
                template_period=self.period,
            )

    def test_auto_lock_does_not_lock_overdue_unsubmitted_period(self):
        result = GradingGovernanceService.auto_lock_due_periods(at=timezone.now())

        self.lock.refresh_from_db()
        self.assertEqual(result["count"], 0)
        self.assertFalse(self.lock.is_locked)

    def test_auto_lock_locks_reopened_submission_after_deadline(self):
        submission = GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.REOPENED,
            submitted_by_user=self.faculty_user,
            submitted_at=timezone.now() - timedelta(days=1),
            reopened_by_user=self.faculty_user,
            reopened_at=timezone.now() - timedelta(hours=2),
        )

        result = GradingGovernanceService.auto_lock_due_periods(at=timezone.now())

        self.assertEqual(result["count"], 1)
        course_lock = GradingPeriodLock.objects.get(
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.offering,
            period_code=self.lock.period_code,
        )
        self.assertTrue(course_lock.is_locked)
        submission.refresh_from_db()
        self.assertEqual(submission.status, GradeSubmission.Status.REOPENED)


class GradeEncodingAccessControlTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="TEN-ENC", name="Tenant Encoding")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.other_campus = Campus.objects.create(tenant=self.tenant, code="BRANCH", name="Branch Campus")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="CS",
            name="Computer Studies",
        )
        self.other_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            code="CSB",
            name="Computer Studies Branch",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSCS",
            name="BS Computer Science",
        )
        self.other_program = Program.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            code="BSCS-B",
            name="BS Computer Science Branch",
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
        self.other_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=self.other_program,
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
        )
        self.other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.other_campus,
            department=self.other_department,
            program=self.other_program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.other_section,
        )
        self.faculty_user = User.objects.create_user(
            username="faculty_encoding_gate",
            email="faculty_encoding_gate@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            student_no="2025-90001",
            last_name="Test",
            first_name="Student",
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
            code="TPL-ENC",
            name="Encoding Gate Template",
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
        self.midterm = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
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

    def _closed_control(self, **overrides):
        defaults = {
            "tenant": self.tenant,
            "academic_year": self.academic_year,
            "term": self.term,
            "period_code": None,
            "campus": None,
            "course_offering": None,
            "status": GradeEncodingControl.Status.CLOSED,
            "reason": "Enrollment cleanup",
            "notice_to_faculty": "Please wait for the final class list.",
            "is_active": True,
        }
        defaults.update(overrides)
        return GradeEncodingControl.objects.create(**defaults)

    def test_closed_control_requires_reason_and_notice(self):
        control = GradeEncodingControl(
            tenant=self.tenant,
            academic_year=self.academic_year,
            term=self.term,
            status=GradeEncodingControl.Status.CLOSED,
        )

        with self.assertRaises(ValidationError) as ctx:
            control.full_clean()

        self.assertIn("reason", ctx.exception.message_dict)
        self.assertIn("notice_to_faculty", ctx.exception.message_dict)

    def test_term_level_closed_control_blocks_encoding(self):
        self._closed_control()

        self.assertFalse(
            GradeEncodingAccessService.is_encoding_allowed(offering=self.offering, template_period=self.period)
        )
        with self.assertRaises(ValidationError):
            GradingGovernanceService.assert_encoding_allowed(offering=self.offering, template_period=self.period)

    def test_scope_filters_leave_unaffected_offerings_open(self):
        self._closed_control(campus=self.campus)

        self.assertFalse(
            GradeEncodingAccessService.is_encoding_allowed(offering=self.offering, template_period=self.period)
        )
        self.assertTrue(
            GradeEncodingAccessService.is_encoding_allowed(offering=self.other_offering, template_period=self.period)
        )

    def test_period_specific_control_blocks_only_matching_period(self):
        self._closed_control(period_code="PRELIM")

        self.assertFalse(
            GradeEncodingAccessService.is_encoding_allowed(offering=self.offering, template_period=self.period)
        )
        self.assertTrue(
            GradeEncodingAccessService.is_encoding_allowed(offering=self.offering, template_period=self.midterm)
        )

    def test_lower_scope_open_does_not_override_broader_closed_control(self):
        self._closed_control(campus=self.campus)
        GradeEncodingControl.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            campus=self.campus,
            course_offering=self.offering,
            status=GradeEncodingControl.Status.OPEN,
            is_active=True,
        )

        self.assertFalse(
            GradeEncodingAccessService.is_encoding_allowed(offering=self.offering, template_period=self.period)
        )

    def test_create_activity_is_blocked_when_control_is_closed(self):
        self._closed_control(course_offering=self.offering)

        with self.assertRaises(ValidationError):
            FacultyGradingService.create_activity(
                user=self.faculty_user,
                offering=self.offering,
                template_period=self.period,
                template_component=self.component,
                template_subcomponent=None,
                template_detail=None,
                title="Q1",
                total_score=Decimal("20.00"),
                activity_date=date(2026, 1, 10),
            )

        self.assertFalse(GradeActivity.objects.filter(offering=self.offering).exists())

    def test_score_update_and_submission_are_blocked_when_control_is_closed(self):
        activity = FacultyGradingService.create_activity(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            template_subcomponent=None,
            template_detail=None,
            title="Q1",
            total_score=Decimal("20.00"),
            activity_date=date(2026, 1, 10),
        )
        self._closed_control(course_offering=self.offering)

        with self.assertRaises(ValidationError):
            FacultyGradingService.upsert_activity_scores(
                user=self.faculty_user,
                activity=activity,
                score_payload=[{"student_id": self.student.id, "raw_score": Decimal("0.00")}],
            )
        with self.assertRaises(ValidationError):
            GradingGovernanceService.submit_period(
                user=self.faculty_user,
                offering=self.offering,
                template_period=self.period,
            )

        self.assertFalse(StudentActivityScore.objects.filter(activity=activity).exists())
        self.assertFalse(GradeSubmission.objects.filter(offering=self.offering, template_period=self.period).exists())

    def test_activity_edit_and_archive_are_blocked_when_control_is_closed(self):
        activity = FacultyGradingService.create_activity(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            template_subcomponent=None,
            template_detail=None,
            title="Q1",
            total_score=Decimal("20.00"),
            activity_date=date(2026, 1, 10),
        )
        self._closed_control(course_offering=self.offering)

        with self.assertRaises(ValidationError):
            FacultyGradingService.update_activity(
                user=self.faculty_user,
                activity=activity,
                template_period=self.period,
                template_component=self.component,
                template_subcomponent=None,
                template_detail=None,
                title="Q1 Updated",
                total_score=Decimal("30.00"),
                activity_date=date(2026, 1, 11),
            )
        with self.assertRaises(ValidationError):
            FacultyGradingService.archive_activity(user=self.faculty_user, activity=activity)

        activity.refresh_from_db()
        self.assertEqual(activity.title, "Q1")
        self.assertTrue(activity.is_active)

    def test_attendance_session_and_record_writes_are_blocked_when_control_is_closed(self):
        session, _created = FacultyGradingService.create_or_update_attendance_session(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            session_date=date(2026, 1, 10),
            title="Attendance 1",
        )
        self._closed_control(course_offering=self.offering)

        with self.assertRaises(ValidationError):
            FacultyGradingService.create_or_update_attendance_session(
                user=self.faculty_user,
                offering=self.offering,
                template_period=self.period,
                session_date=date(2026, 1, 11),
                title="Attendance 2",
            )
        with self.assertRaises(ValidationError):
            FacultyGradingService.upsert_attendance_records(
                user=self.faculty_user,
                session=session,
                status_payload=[{"student_id": self.student.id, "status_code": AttendanceRecord.Status.PRESENT}],
            )

        self.assertFalse(AttendanceSession.objects.filter(title="Attendance 2").exists())
        self.assertFalse(AttendanceRecord.objects.filter(session=session).exists())

    def test_open_control_allows_encoding_when_no_other_rules_block(self):
        GradeEncodingControl.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            campus=self.campus,
            course_offering=self.offering,
            status=GradeEncodingControl.Status.OPEN,
            is_active=True,
        )

        activity = FacultyGradingService.create_activity(
            user=self.faculty_user,
            offering=self.offering,
            template_period=self.period,
            template_component=self.component,
            template_subcomponent=None,
            template_detail=None,
            title="Q1",
            total_score=Decimal("20.00"),
            activity_date=date(2026, 1, 10),
        )

        self.assertTrue(activity.pk)
