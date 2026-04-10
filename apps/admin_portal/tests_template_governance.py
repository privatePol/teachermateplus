from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.auditlog.models import AuditLog
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.grading.models import (
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    TemplateHotfixRequest,
)
from apps.grading.services import TemplateGovernanceWorkflowService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


class TemplateGovernanceWorkflowTests(TestCase):
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

        self.super_admin_role = Role.objects.create(code="SUPER_ADMIN", name="Super Admin")
        self.tenant_admin_role = Role.objects.create(code="TENANT_ADMIN", name="Tenant Admin")
        self.cao_role = Role.objects.create(code="CAO", name="Chief Academic Officer")
        self.dean_role = Role.objects.create(code="DEAN", name="Academic Dean")

        self.permission_admin_access = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
        )
        self.permission_system_settings = Permission.objects.create(
            code="system_settings.update",
            module="system_settings",
            action="update",
        )
        self.permission_template_read = Permission.objects.create(
            code="grading_templates.read",
            module="grading_templates",
            action="read",
        )
        self.permission_template_submit = Permission.objects.create(
            code="grading_templates.submit_for_approval",
            module="grading_templates",
            action="submit_for_approval",
        )
        self.permission_template_approve = Permission.objects.create(
            code="grading_templates.approve",
            module="grading_templates",
            action="approve",
        )
        self.permission_template_publish = Permission.objects.create(
            code="grading_templates.publish",
            module="grading_templates",
            action="publish",
        )
        self.permission_hotfix_read = Permission.objects.create(
            code="template_hotfixes.read",
            module="template_hotfixes",
            action="read",
        )
        self.permission_hotfix_create = Permission.objects.create(
            code="template_hotfixes.create",
            module="template_hotfixes",
            action="create",
        )
        self.permission_hotfix_review = Permission.objects.create(
            code="template_hotfixes.review",
            module="template_hotfixes",
            action="review",
        )

        for permission in (
            self.permission_admin_access,
            self.permission_system_settings,
            self.permission_template_read,
            self.permission_template_submit,
            self.permission_template_approve,
            self.permission_template_publish,
            self.permission_hotfix_read,
            self.permission_hotfix_create,
            self.permission_hotfix_review,
        ):
            RolePermission.objects.create(role=self.super_admin_role, permission=permission)
            RolePermission.objects.create(role=self.tenant_admin_role, permission=permission)
            RolePermission.objects.create(role=self.cao_role, permission=permission)
            RolePermission.objects.create(role=self.dean_role, permission=permission)

        self.workflow_admin = self._make_user("workflow_admin", "Workflow Admin")
        UserRole.objects.create(
            user=self.workflow_admin,
            role=self.tenant_admin_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

        self.super_reviewer = self._make_user("workflow_super", "Workflow Super")
        UserRole.objects.create(
            user=self.super_reviewer,
            role=self.super_admin_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.dean_reviewer = self._make_user("workflow_dean", "Workflow Dean")
        UserRole.objects.create(
            user=self.dean_reviewer,
            role=self.dean_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.cao_reviewer = self._make_user("workflow_cao", "Workflow Cao")
        UserRole.objects.create(
            user=self.cao_reviewer,
            role=self.cao_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

    def _make_user(self, username: str, full_name: str) -> User:
        first_name, last_name = full_name.split(" ", 1)
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            first_name=first_name,
            last_name=last_name,
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def _set_scope(self):
        session = self.client.session
        session[ScopeService.SESSION_TENANT_KEY] = self.tenant.id
        session[ScopeService.SESSION_CAMPUS_KEY] = self.campus.id
        session.save()

    def _make_template(self, *, code: str, published: bool = False, approval_status: str = GradingTemplate.ApprovalStatus.DRAFT):
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code=code,
            name=f"Template {code}",
            approval_status=approval_status,
            is_published=published,
            published_at=timezone.now() if published else None,
            published_by=self.super_reviewer if published else None,
        )
        period = GradingTemplatePeriod.objects.create(
            template=template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            is_active=True,
        )
        GradingTemplateComponent.objects.create(
            template_period=period,
            code="PRELIM_EXAM",
            name="Prelim Exam",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
            is_active=True,
        )
        return template

    def test_settings_page_saves_role_matrix_and_safeguards(self):
        self.client.force_login(self.workflow_admin)
        self._set_scope()

        response = self.client.post(
            reverse("admin_portal:template_governance_settings"),
            {
                "draft_roles": [self.tenant_admin_role.id],
                "submit_roles": [self.tenant_admin_role.id],
                "approval_review_roles": [self.cao_role.id],
                "publish_roles": [self.tenant_admin_role.id, self.super_admin_role.id],
                "hotfix_request_roles": [self.tenant_admin_role.id],
                "hotfix_review_apply_roles": [self.cao_role.id],
                "require_approval_before_publish": "on",
                "allow_same_user_submit_review": "",
                "allow_same_user_review_publish": "",
                "allow_same_user_hotfix_request_apply": "",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
            response.context["form"].errors.as_json() if hasattr(response, "context") and response.context else "",
        )
        snapshot = TemplateGovernanceWorkflowService.get_workflow_snapshot(tenant_id=self.tenant.id)
        stage_map = {row["code"]: row["role_codes"] for row in snapshot["stages"]}
        self.assertEqual(stage_map[TemplateGovernanceWorkflowService.STAGE_DRAFT], ["TENANT_ADMIN"])
        self.assertEqual(stage_map[TemplateGovernanceWorkflowService.STAGE_APPROVAL_REVIEW], ["CAO"])
        self.assertCountEqual(
            stage_map[TemplateGovernanceWorkflowService.STAGE_PUBLISH],
            ["TENANT_ADMIN", "SUPER_ADMIN"],
        )
        self.assertTrue(snapshot["require_approval_before_publish"])
        self.assertFalse(snapshot["allow_same_user_submit_review"])
        self.assertEqual(
            AuditLog.objects.filter(entity_type="SystemSetting", entity_id=f"tenant:{self.tenant.id}:template-governance").count(),
            1,
        )

    def test_settings_page_can_store_sequential_workflow_roles(self):
        self.client.force_login(self.workflow_admin)
        self._set_scope()

        response = self.client.post(
            reverse("admin_portal:template_governance_settings"),
            {
                "draft_roles": [self.tenant_admin_role.id],
                "submit_roles": [self.tenant_admin_role.id],
                "approval_review_roles": [self.cao_role.id],
                "publish_roles": [self.tenant_admin_role.id],
                "hotfix_request_roles": [self.tenant_admin_role.id],
                "hotfix_review_apply_roles": [self.cao_role.id],
                "sequential_approval_enabled": "on",
                "approval_review_step_roles": [self.dean_role.id],
                "approval_final_step_roles": [self.cao_role.id],
                "sequential_hotfix_enabled": "on",
                "hotfix_review_step_roles": [self.dean_role.id],
                "hotfix_apply_step_roles": [self.cao_role.id],
                "require_approval_before_publish": "on",
                "allow_same_user_submit_review": "",
                "allow_same_user_review_approve": "",
                "allow_same_user_review_publish": "",
                "allow_same_user_hotfix_request_apply": "",
                "allow_same_user_hotfix_review_apply": "",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
            response.context["form"].errors.as_json() if hasattr(response, "context") and response.context else "",
        )
        snapshot = TemplateGovernanceWorkflowService.get_workflow_snapshot(tenant_id=self.tenant.id)
        self.assertTrue(snapshot["sequential_template_approval_enabled"])
        self.assertTrue(snapshot["sequential_hotfix_enabled"])
        self.assertEqual(snapshot["approval_steps"][0]["role_codes"], ["DEAN"])
        self.assertEqual(snapshot["approval_steps"][1]["role_codes"], ["CAO"])
        self.assertEqual(snapshot["hotfix_steps"][0]["role_codes"], ["DEAN"])
        self.assertEqual(snapshot["hotfix_steps"][1]["role_codes"], ["CAO"])

    def test_same_user_cannot_submit_and_review_template_when_blocked(self):
        template = self._make_template(code="TMP-REVIEW")
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[TemplateGovernanceWorkflowService.STAGE_SUBMIT_FOR_APPROVAL],
            ["SUPER_ADMIN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[TemplateGovernanceWorkflowService.STAGE_APPROVAL_REVIEW],
            ["SUPER_ADMIN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.ALLOW_SAME_USER_SUBMIT_REVIEW_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

        self.client.force_login(self.super_reviewer)
        self._set_scope()
        submit_response = self.client.post(
            reverse("admin_portal:grading_template_submit_for_approval", kwargs={"template_id": template.id}),
            {"remarks": "Ready for review."},
        )
        self.assertEqual(submit_response.status_code, 302)

        review_response = self.client.post(
            reverse("admin_portal:grading_template_review_approval", kwargs={"template_id": template.id}),
            {"decision": "APPROVE", "remarks": "Approving own draft."},
            follow=True,
        )

        self.assertEqual(review_response.status_code, 200)
        self.assertContains(review_response, "cannot submit and review this template")
        template.refresh_from_db()
        self.assertEqual(template.approval_status, GradingTemplate.ApprovalStatus.FOR_APPROVAL)
        self.assertIsNone(template.approval_reviewed_by)

    def test_publish_can_bypass_approval_when_workflow_allows_direct_publish(self):
        template = self._make_template(code="TMP-PUBLISH")
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.REQUIRE_APPROVAL_BEFORE_PUBLISH_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[TemplateGovernanceWorkflowService.STAGE_PUBLISH],
            ["SUPER_ADMIN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )

        self.client.force_login(self.super_reviewer)
        self._set_scope()
        response = self.client.post(
            reverse("admin_portal:grading_template_publish", kwargs={"template_id": template.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "published successfully")
        template.refresh_from_db()
        self.assertTrue(template.is_published)
        self.assertEqual(template.approval_status, GradingTemplate.ApprovalStatus.APPROVED)
        self.assertEqual(template.approval_reviewed_by, self.super_reviewer)

    def test_same_user_cannot_request_and_apply_hotfix_when_blocked(self):
        template = self._make_template(
            code="TMP-HOTFIX",
            published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST],
            ["SUPER_ADMIN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[
                TemplateGovernanceWorkflowService.STAGE_HOTFIX_REVIEW_APPLY
            ],
            ["SUPER_ADMIN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.ALLOW_SAME_USER_HOTFIX_REQUEST_APPLY_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )

        self.client.force_login(self.super_reviewer)
        self._set_scope()
        create_response = self.client.post(
            reverse("admin_portal:template_hotfix_create", kwargs={"template_id": template.id}),
            {
                "apply_mode": TemplateHotfixRequest.ApplyMode.FUTURE_ONLY,
                "justification": "Adjust the published template safely.",
            },
        )
        self.assertEqual(create_response.status_code, 302)
        hotfix = TemplateHotfixRequest.objects.get(template=template)

        review_response = self.client.post(
            reverse("admin_portal:template_hotfix_review", kwargs={"hotfix_id": hotfix.id}),
            {"decision": "APPROVE", "review_remarks": "Applying my own hotfix."},
            follow=True,
        )

        self.assertEqual(review_response.status_code, 200)
        self.assertContains(review_response, "cannot request and apply this hotfix")
        hotfix.refresh_from_db()
        self.assertEqual(hotfix.status, TemplateHotfixRequest.Status.PENDING)
        self.assertIsNone(hotfix.reviewed_by_user)

    def test_hotfix_create_page_sorts_selected_offerings_by_course_title(self):
        template = self._make_template(
            code="TMP-HOTFIX-UI",
            published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        course_a = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="ENG201",
            title="Business Writing",
        )
        course_b = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="FIL101",
            title="Panitikan",
        )
        section_b = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-2A",
            name="BSIT 2A",
        )
        CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=course_b,
            section=section_b,
        )
        CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=course_a,
            section=self.section,
        )

        self.client.force_login(self.workflow_admin)
        self._set_scope()
        response = self.client.get(reverse("admin_portal:template_hotfix_create", kwargs={"template_id": template.id}))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin_portal/grading/template_hotfix_create.html")
        self.assertContains(response, "Search by title, course code, section, or term")
        content = response.content.decode("utf-8")
        self.assertLess(content.index("Business Writing"), content.index("IT Application Tools"))
        self.assertLess(content.index("IT Application Tools"), content.index("Panitikan"))

    def test_sequential_template_workflow_advances_then_final_approves(self):
        template = self._make_template(code="TMP-SEQUENTIAL")
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.SEQUENTIAL_APPROVAL_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.APPROVAL_REVIEW_STEP_ROLE_CODES_KEY,
            ["DEAN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.APPROVAL_FINAL_STEP_ROLE_CODES_KEY,
            ["CAO"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[TemplateGovernanceWorkflowService.STAGE_SUBMIT_FOR_APPROVAL],
            ["TENANT_ADMIN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )

        self.client.force_login(self.workflow_admin)
        self._set_scope()
        submit_response = self.client.post(
            reverse("admin_portal:grading_template_submit_for_approval", kwargs={"template_id": template.id}),
            {"remarks": "Please review."},
        )
        self.assertEqual(submit_response.status_code, 302)
        template.refresh_from_db()
        workflow = template.approval_workflows.latest("created_at")
        self.assertEqual(workflow.steps.count(), 2)
        first_step, second_step = workflow.steps.order_by("step_no")
        self.assertEqual(first_step.status, "PENDING")
        self.assertEqual(second_step.status, "QUEUED")

        self.client.force_login(self.dean_reviewer)
        self._set_scope()
        first_review_response = self.client.post(
            reverse("admin_portal:grading_template_review_approval", kwargs={"template_id": template.id}),
            {"decision": "APPROVE", "remarks": "Dean reviewed."},
            follow=True,
        )
        self.assertEqual(first_review_response.status_code, 200)
        self.assertContains(first_review_response, "advanced to the next workflow step")
        template.refresh_from_db()
        workflow.refresh_from_db()
        first_step.refresh_from_db()
        second_step.refresh_from_db()
        self.assertEqual(template.approval_status, GradingTemplate.ApprovalStatus.FOR_APPROVAL)
        self.assertEqual(first_step.status, "APPROVED")
        self.assertEqual(second_step.status, "PENDING")

        self.client.force_login(self.cao_reviewer)
        self._set_scope()
        final_review_response = self.client.post(
            reverse("admin_portal:grading_template_review_approval", kwargs={"template_id": template.id}),
            {"decision": "APPROVE", "remarks": "CAO approved."},
            follow=True,
        )
        self.assertEqual(final_review_response.status_code, 200)
        template.refresh_from_db()
        workflow.refresh_from_db()
        second_step.refresh_from_db()
        self.assertEqual(template.approval_status, GradingTemplate.ApprovalStatus.APPROVED)
        self.assertEqual(workflow.status, "APPROVED")
        self.assertEqual(second_step.status, "APPROVED")

    def test_sequential_hotfix_workflow_advances_then_applies(self):
        template = self._make_template(
            code="TMP-HOTFIX-SEQUENTIAL",
            published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.SEQUENTIAL_HOTFIX_ENABLED_KEY,
            True,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.HOTFIX_REVIEW_STEP_ROLE_CODES_KEY,
            ["DEAN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.HOTFIX_APPLY_STEP_ROLE_CODES_KEY,
            ["CAO"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )
        SystemSettingService.set(
            TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST],
            ["TENANT_ADMIN"],
            tenant_id=self.tenant.id,
            value_type="JSON",
            is_active=True,
        )

        self.client.force_login(self.workflow_admin)
        self._set_scope()
        create_response = self.client.post(
            reverse("admin_portal:template_hotfix_create", kwargs={"template_id": template.id}),
            {
                "apply_mode": TemplateHotfixRequest.ApplyMode.FUTURE_ONLY,
                "justification": "Need a governed hotfix.",
            },
        )
        self.assertEqual(create_response.status_code, 302)
        hotfix = TemplateHotfixRequest.objects.get(template=template)
        first_step, second_step = hotfix.workflow_steps.order_by("step_no")
        self.assertEqual(first_step.status, "PENDING")
        self.assertEqual(second_step.status, "QUEUED")

        self.client.force_login(self.dean_reviewer)
        self._set_scope()
        first_review_response = self.client.post(
            reverse("admin_portal:template_hotfix_review", kwargs={"hotfix_id": hotfix.id}),
            {"decision": "APPROVE", "review_remarks": "Dean reviewed."},
            follow=True,
        )
        self.assertEqual(first_review_response.status_code, 200)
        self.assertContains(first_review_response, "advanced to the next workflow step")
        hotfix.refresh_from_db()
        first_step.refresh_from_db()
        second_step.refresh_from_db()
        self.assertEqual(hotfix.status, TemplateHotfixRequest.Status.PENDING)
        self.assertEqual(first_step.status, "APPROVED")
        self.assertEqual(second_step.status, "PENDING")

        self.client.force_login(self.cao_reviewer)
        self._set_scope()
        final_review_response = self.client.post(
            reverse("admin_portal:template_hotfix_review", kwargs={"hotfix_id": hotfix.id}),
            {"decision": "APPROVE", "review_remarks": "CAO applied."},
            follow=True,
        )
        self.assertEqual(final_review_response.status_code, 200)
        hotfix.refresh_from_db()
        second_step.refresh_from_db()
        self.assertEqual(hotfix.status, TemplateHotfixRequest.Status.APPLIED)
        self.assertEqual(second_step.status, "APPROVED")
