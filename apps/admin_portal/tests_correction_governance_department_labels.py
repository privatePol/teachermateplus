from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.admin_portal.forms import CorrectionApprovalRouteRuleForm
from apps.core.services.scope import ScopeService
from apps.grading.models import CorrectionApprovalRouteRule
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Tenant


class CorrectionGovernanceDepartmentLabelTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.fairview = Campus.objects.create(
            tenant=self.tenant,
            code="NCBA-FAIRVIEW",
            name="Fairview Campus",
        )
        self.cubao = Campus.objects.create(
            tenant=self.tenant,
            code="NCBA-CUBAO",
            name="Cubao Campus",
        )
        self.fairview_ba = Department.objects.create(
            tenant=self.tenant,
            campus=self.fairview,
            code="BA",
            name="Business Administration",
        )
        self.cubao_ba = Department.objects.create(
            tenant=self.tenant,
            campus=self.cubao,
            code="BA",
            name="Business Administration",
        )
        self.approver_role = Role.objects.create(code="CAO", name="Chief Academic Officer")
        self.admin_role = Role.objects.create(code="ADMIN_CORRECTION_LABELS", name="Correction Labels Admin")
        for code, module, action in (
            ("admin_portal.access", "admin_portal", "access"),
            ("grading_governance_settings.update", "grading_governance_settings", "update"),
        ):
            permission = Permission.objects.create(code=code, module=module, action=action)
            RolePermission.objects.create(role=self.admin_role, permission=permission)
        self.admin_user = User.objects.create_superuser(
            username="correction-label-admin",
            email="correction-label-admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.fairview,
            default_department=self.fairview_ba,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=self.admin_user,
            role=self.admin_role,
            tenant=self.tenant,
        )

    def _route_payload(self, department):
        return {
            "form_action": "save_route",
            "route-faculty_department": str(department.id),
            "route-step_1_role": str(self.approver_role.id),
            "route-step_1_requires_same_department": "",
            "route-step_2_role": "",
            "route-step_2_requires_same_department": "",
            "route-final_role_ordered": "",
            "route-final_requires_same_department_ordered": "",
            "route-notes": "",
            "route-is_active": "on",
        }

    def _login_with_tenant_scope(self):
        self.client.force_login(self.admin_user)
        session = self.client.session
        session[ScopeService.SESSION_TENANT_KEY] = self.tenant.id
        session[ScopeService.SESSION_CAMPUS_KEY] = self.fairview.id
        session.save()

    def test_dropdown_distinguishes_same_code_departments_by_campus(self):
        form = CorrectionApprovalRouteRuleForm(
            tenant=self.tenant,
            department_queryset=Department.objects.filter(id__in=[self.fairview_ba.id, self.cubao_ba.id]),
            role_queryset=Role.objects.filter(id=self.approver_role.id),
        )

        choices = {
            int(getattr(value, "value", value)): label
            for value, label in form.fields["faculty_department"].choices
            if value
        }

        self.assertEqual(choices[self.fairview_ba.id], "NCBA-FAIRVIEW — BA - Business Administration")
        self.assertEqual(choices[self.cubao_ba.id], "NCBA-CUBAO — BA - Business Administration")
        self.assertNotEqual(choices[self.fairview_ba.id], choices[self.cubao_ba.id])

    def test_configured_routes_show_campus_and_department(self):
        for department in (self.fairview_ba, self.cubao_ba):
            CorrectionApprovalRouteRule.objects.create(
                tenant=self.tenant,
                faculty_department=department,
                step1_role=self.approver_role,
            )
        self._login_with_tenant_scope()

        response = self.client.get(reverse("admin_portal:correction_governance_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NCBA-FAIRVIEW — BA - Business Administration")
        self.assertContains(response, "NCBA-CUBAO — BA - Business Administration")

    def test_route_submission_preserves_selected_department_id(self):
        self._login_with_tenant_scope()

        response = self.client.post(
            reverse("admin_portal:correction_governance_settings"),
            data=self._route_payload(self.cubao_ba),
        )

        self.assertEqual(response.status_code, 302)
        route = CorrectionApprovalRouteRule.objects.get(tenant=self.tenant)
        self.assertEqual(route.faculty_department_id, self.cubao_ba.id)
