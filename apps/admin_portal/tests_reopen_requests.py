from datetime import date
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.grading.notifications import GradebookReopenNotificationService
from apps.grading.models import (
    GradeSubmission,
    GradeSubmissionReopenRequest,
    GradingPeriodLock,
    GradingTemplate,
    GradingTemplatePeriod,
)
from apps.grading.services import GradingGovernanceService
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


class GradeSubmissionReopenRequestReviewTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
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
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )
        course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="IT101",
            title="IT 101",
        )
        section = Section.objects.create(
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
            course=course,
            section=section,
        )
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED",
            name="General Education",
            is_published=True,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )
        self.admin_user = User.objects.create_user(
            username="reopen_admin",
            email="reopen_admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code="DEAN", name="Dean")
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
            ("dashboard.read", "dashboard", "read"),
            ("grade_submissions.read", "grade_submissions", "read"),
            ("reopen_requests.review", "reopen_requests", "review"),
            ("reopen_requests.read", "reopen_requests", "read"),
            ("grade_submissions.reopen", "grade_submissions", "reopen"),
        ]:
            permission = Permission.objects.create(code=code, module=module, action=action)
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(
            user=self.admin_user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.submission = GradeSubmission.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            template_period=self.period,
            status=GradeSubmission.Status.REOPENED,
            submitted_by_user=self.admin_user,
            submitted_at=timezone.now(),
        )

    def test_posting_already_reviewed_reopen_request_does_not_raise_validation_error(self):
        reopen_request = GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=self.submission,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.admin_user,
            reviewed_by_user=self.admin_user,
            reviewed_at=timezone.now(),
            status=GradeSubmissionReopenRequest.Status.APPROVED,
            justification="Need to adjust grades.",
        )

        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("admin_portal:grade_submission_reopen_request_review", args=[reopen_request.id]),
            {"decision": "APPROVE", "review_remarks": "Duplicate submit"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only pending reopen requests can be reviewed.")
        reopen_request.refresh_from_db()
        self.assertEqual(reopen_request.status, GradeSubmissionReopenRequest.Status.APPROVED)

    def test_auto_close_policy_blocks_encoding_until_reopen_request_is_approved(self):
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
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(hours=1),
            is_locked=False,
        )
        self.submission.status = GradeSubmission.Status.DRAFT
        self.submission.save(update_fields=["status", "updated_at"])

        self.assertTrue(
            GradingGovernanceService.is_auto_closed_after_deadline(
                offering=self.offering,
                template_period=self.period,
            )
        )
        with self.assertRaises(ValidationError):
            GradingGovernanceService.assert_encoding_allowed(
                offering=self.offering,
                template_period=self.period,
            )

        reopen_request = GradingGovernanceService.create_reopen_request_for_period(
            user=self.admin_user,
            offering=self.offering,
            template_period=self.period,
            justification="Need to finish required records.",
        )
        GradingGovernanceService.review_reopen_request(
            request_obj=reopen_request,
            reviewer=self.admin_user,
            approved=True,
            review_remarks="Approved for completion.",
        )

        self.assertFalse(
            GradingGovernanceService.is_auto_closed_after_deadline(
                offering=self.offering,
                template_period=self.period,
            )
        )
        self.assertTrue(
            GradingGovernanceService.assert_encoding_allowed(
                offering=self.offering,
                template_period=self.period,
            )
        )

    def test_disabled_deadline_policy_keeps_unlocked_gradebook_open_after_deadline(self):
        SystemSettingService.set(
            FeatureSettingsService.GRADE_DEADLINE_ENFORCEMENT_POLICY_KEY,
            FeatureSettingsService.GRADE_DEADLINE_POLICY_DISABLED,
            tenant_id=self.tenant.id,
            value_type="STRING",
            is_active=True,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(hours=1),
            is_locked=False,
        )
        self.submission.status = GradeSubmission.Status.DRAFT
        self.submission.save(update_fields=["status", "updated_at"])

        self.assertFalse(
            GradingGovernanceService.is_auto_closed_after_deadline(
                offering=self.offering,
                template_period=self.period,
            )
        )
        self.assertTrue(
            GradingGovernanceService.assert_encoding_allowed(
                offering=self.offering,
                template_period=self.period,
            )
        )

    def test_approved_reopen_request_expires_after_24_hours_and_auto_locks(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(days=2),
            is_locked=False,
            is_active=True,
        )
        self.submission.status = GradeSubmission.Status.DRAFT
        self.submission.save(update_fields=["status", "updated_at"])
        reopen_request = GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=self.submission,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.admin_user,
            reviewed_by_user=self.admin_user,
            reviewed_at=timezone.now() - timezone.timedelta(hours=25),
            status=GradeSubmissionReopenRequest.Status.APPROVED,
            justification="Need to finish required records.",
        )

        self.assertIsNone(
            GradingGovernanceService.get_active_approved_reopen_request(
                offering=self.offering,
                template_period=self.period,
            )
        )
        result = GradingGovernanceService.auto_lock_due_periods(at=timezone.now())

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rows"][0]["reopen_request_id"], reopen_request.id)
        course_lock = GradingPeriodLock.objects.get(
            course_offering=self.offering,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
        )
        self.assertTrue(course_lock.is_locked)
        self.assertIn("24 hours", course_lock.remarks)
        with self.assertRaises(ValidationError):
            GradingGovernanceService.assert_encoding_allowed(
                offering=self.offering,
                template_period=self.period,
            )
        with self.assertRaisesMessage(ValidationError, "approved reopen window expired"):
            GradingGovernanceService.submit_period(
                user=self.admin_user,
                offering=self.offering,
                template_period=self.period,
            )
        self.assertTrue(
            GradingGovernanceService.can_request_reopen_after_auto_close(
                offering=self.offering,
                template_period=self.period,
            )
        )

    def test_locked_period_submission_requires_active_approved_reopen_request(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(hours=1),
            is_locked=True,
            is_active=True,
        )
        self.submission.status = GradeSubmission.Status.DRAFT
        self.submission.save(update_fields=["status", "updated_at"])

        with self.assertRaisesMessage(ValidationError, "Submit a gradebook reopen request first"):
            GradingGovernanceService.submit_period(
                user=self.admin_user,
                offering=self.offering,
                template_period=self.period,
            )

    def test_newer_active_reopen_request_overrides_older_expired_request(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(days=2),
            is_locked=True,
            is_active=True,
        )
        self.submission.status = GradeSubmission.Status.DRAFT
        self.submission.save(update_fields=["status", "updated_at"])
        older_request = GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=self.submission,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.admin_user,
            reviewed_by_user=self.admin_user,
            reviewed_at=timezone.now() - timezone.timedelta(hours=25),
            status=GradeSubmissionReopenRequest.Status.APPROVED,
            justification="First approved window expired.",
        )
        newer_request = GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=self.submission,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.admin_user,
            reviewed_by_user=self.admin_user,
            reviewed_at=timezone.now(),
            status=GradeSubmissionReopenRequest.Status.APPROVED,
            justification="Second approved window is active.",
        )

        self.assertEqual(
            GradingGovernanceService.get_active_approved_reopen_request(
                offering=self.offering,
                template_period=self.period,
            ),
            newer_request,
        )
        self.assertIsNone(
            GradingGovernanceService.get_latest_expired_approved_reopen_request(
                offering=self.offering,
                template_period=self.period,
            )
        )
        result = GradingGovernanceService.auto_lock_due_periods(at=timezone.now())

        self.assertEqual(result["count"], 0)
        self.assertNotIn(older_request.id, [row.get("reopen_request_id") for row in result["rows"]])

    def test_submitted_after_deadline_uses_correction_not_reopen_request(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(hours=1),
            is_locked=True,
            is_active=True,
        )
        self.submission.status = GradeSubmission.Status.SUBMITTED
        self.submission.submitted_at = timezone.now()
        self.submission.save(update_fields=["status", "submitted_at", "updated_at"])

        self.assertFalse(
            GradingGovernanceService.can_request_reopen_after_auto_close(
                offering=self.offering,
                template_period=self.period,
            )
        )
        with self.assertRaisesMessage(ValidationError, "Correction of Grades"):
            GradingGovernanceService.create_reopen_request(
                user=self.admin_user,
                submission=self.submission,
                justification="Need to change after submission.",
            )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:grade_submission_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use Correction of Grades")
        self.assertNotContains(response, "Request Reopen")

    def test_submitted_before_deadline_can_still_use_reopen_request(self):
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() + timezone.timedelta(hours=1),
            is_locked=False,
            is_active=True,
        )
        self.submission.status = GradeSubmission.Status.SUBMITTED
        self.submission.submitted_at = timezone.now()
        self.submission.save(update_fields=["status", "submitted_at", "updated_at"])

        reopen_request = GradingGovernanceService.create_reopen_request(
            user=self.admin_user,
            submission=self.submission,
            justification="Need to revise before deadline.",
        )

        self.assertEqual(reopen_request.status, GradeSubmissionReopenRequest.Status.PENDING)

    def test_dashboard_shows_pending_reopen_requests_in_scope(self):
        reopen_request = GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=self.submission,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.admin_user,
            status=GradeSubmissionReopenRequest.Status.PENDING,
            justification="Need to finish after deadline.",
        )

        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gradebook Reopen Requests")
        self.assertContains(response, "1 pending")
        self.assertContains(response, "Latest Pending Requests")
        self.assertContains(response, "IT101 / BSIT-1A")
        self.assertContains(
            response,
            reverse("admin_portal:grade_submission_reopen_request_review", args=[reopen_request.id]),
        )

    def test_reopen_request_email_is_sent_to_explicitly_assigned_reviewers(self):
        permission = Permission.objects.get(code="reopen_requests.review")
        direct_reviewer = User.objects.create_user(
            username="direct_reopen_reviewer",
            email="direct_reviewer@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        UserPermission.objects.create(
            user=direct_reviewer,
            permission=permission,
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        superuser = User.objects.create_superuser(
            username="super_reopen_reviewer",
            email="super_reviewer@example.com",
            password="testpass123",
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        no_permission_user = User.objects.create_user(
            username="not_reopen_reviewer",
            email="no_reopen_permission@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        reopen_request = GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=self.submission,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.admin_user,
            status=GradeSubmissionReopenRequest.Status.PENDING,
            justification="Need to finish required records.",
        )

        result = GradebookReopenNotificationService.send_reopen_request_notifications(
            request_obj=reopen_request,
        )

        self.assertEqual(result["attempted"], 2)
        self.assertEqual(result["sent"], 2)
        self.assertCountEqual(
            result["recipients"],
            ["reopen_admin@example.com", "direct_reviewer@example.com"],
        )
        self.assertNotIn(superuser.email, result["recipients"])
        self.assertNotIn(no_permission_user.email, result["recipients"])
        self.assertEqual(len(mail.outbox), 2)

    def test_any_scoped_role_assigned_by_superadmin_can_review_reopen_request(self):
        reviewer_role = Role.objects.create(code="ACADEMIC_REVIEWER", name="Academic Reviewer")
        for code in ["admin_portal.access", "reopen_requests.read", "reopen_requests.review"]:
            RolePermission.objects.create(
                role=reviewer_role,
                permission=Permission.objects.get(code=code),
            )
        reviewer_user = User.objects.create_user(
            username="assigned_reopen_reviewer",
            email="assigned_reopen_reviewer@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version="2026-03",
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=reviewer_user,
            role=reviewer_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        reopen_request = GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=self.submission,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.admin_user,
            status=GradeSubmissionReopenRequest.Status.PENDING,
            justification="Need to finish required records.",
        )

        self.client.force_login(reviewer_user)
        response = self.client.post(
            reverse("admin_portal:grade_submission_reopen_request_review", args=[reopen_request.id]),
            {"decision": "APPROVE", "review_remarks": "Approved by assigned reviewer."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        reopen_request.refresh_from_db()
        self.assertEqual(reopen_request.status, GradeSubmissionReopenRequest.Status.APPROVED)
        self.assertEqual(reopen_request.reviewed_by_user, reviewer_user)

    def test_unassigned_superuser_cannot_review_reopen_request(self):
        superuser = User.objects.create_superuser(
            username="unassigned_superuser",
            email="unassigned_superuser@example.com",
            password="testpass123",
        )
        reopen_request = GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            submission=self.submission,
            offering=self.offering,
            template_period=self.period,
            requested_by_user=self.admin_user,
            status=GradeSubmissionReopenRequest.Status.PENDING,
            justification="Need to finish required records.",
        )

        with self.assertRaisesMessage(ValidationError, "explicitly assigned"):
            GradingGovernanceService.review_reopen_request(
                request_obj=reopen_request,
                reviewer=superuser,
                approved=True,
                review_remarks="Should not be allowed.",
            )
