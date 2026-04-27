from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.grading.models import GradeSubmission, GradeSubmissionReopenRequest, GradingTemplate, GradingTemplatePeriod
from apps.rbac.models import Permission, Role, RolePermission, UserRole
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
        role = Role.objects.create(code="REGISTRAR", name="Registrar")
        for code, module, action in [
            ("admin_portal.access", "admin_portal", "access"),
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
