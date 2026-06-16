from decimal import Decimal

from django.conf import settings
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, Term
from apps.admin_portal.forms import GradingTemplateForm
from apps.admin_portal.services import AdminScopeService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.grading.access import GradingTemplateAccessService
from apps.grading.duplication import GradingTemplateDuplicationService
from apps.grading.models import (
    CourseTemplateAssignment,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplateDetail,
    GradingTemplatePeriod,
    GradingTemplateSubcomponent,
    TemplateHotfixRequest,
    TenantGradingProfile,
)
from apps.grading.services import TemplateGovernanceWorkflowService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Tenant


class GradingTemplateDepartmentVisibilityTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(
            tenant=self.tenant,
            code="MAIN",
            name="Main Campus",
        )
        self.department_a = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="DEPT-A",
            name="Department A",
        )
        self.department_b = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="DEPT-B",
            name="Department B",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2026-2027",
            name="AY 2026-2027",
            start_date="2026-06-01",
            end_date="2027-05-31",
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date="2026-06-01",
            end_date="2026-10-31",
        )
        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_a,
            code="A101",
            title="Department A Course",
        )

        self.dean_role = Role.objects.create(code="DEAN", name="Academic Dean")
        permission_codes = (
            ("admin_portal.access", "admin_portal", "access"),
            ("grading_templates.read", "grading_templates", "read"),
            ("grading_templates.create", "grading_templates", "create"),
            ("grading_templates.update", "grading_templates", "update"),
            ("grading_templates.submit_for_approval", "grading_templates", "submit_for_approval"),
            ("grading_templates.approve", "grading_templates", "approve"),
            ("grading_templates.publish", "grading_templates", "publish"),
            ("template_periods.read", "template_periods", "read"),
            ("template_periods.create", "template_periods", "create"),
            ("template_periods.update", "template_periods", "update"),
            ("template_components.read", "template_components", "read"),
            ("template_components.create", "template_components", "create"),
            ("template_components.update", "template_components", "update"),
            ("template_subcomponents.read", "template_subcomponents", "read"),
            ("template_subcomponents.create", "template_subcomponents", "create"),
            ("template_subcomponents.update", "template_subcomponents", "update"),
            ("template_details.read", "template_details", "read"),
            ("template_details.create", "template_details", "create"),
            ("template_details.update", "template_details", "update"),
            ("template_hotfixes.read", "template_hotfixes", "read"),
            ("template_hotfixes.create", "template_hotfixes", "create"),
            ("template_hotfixes.review", "template_hotfixes", "review"),
            ("tenant_grading_profiles.read", "tenant_grading_profiles", "read"),
            ("tenant_grading_profiles.create", "tenant_grading_profiles", "create"),
            ("tenant_grading_profiles.update", "tenant_grading_profiles", "update"),
            ("course_template_assignments.read", "course_template_assignments", "read"),
            ("course_template_assignments.create", "course_template_assignments", "create"),
            ("course_template_assignments.update", "course_template_assignments", "update"),
        )
        self.permissions = {}
        for code, module, action in permission_codes:
            permission = Permission.objects.create(code=code, module=module, action=action)
            self.permissions[code] = permission
            RolePermission.objects.create(role=self.dean_role, permission=permission)

        self.dean_a = self._create_department_user("dean_a", self.department_a)
        self.dean_b = self._create_department_user("dean_b", self.department_b)
        self.inactive_dean = self._create_department_user(
            "inactive_dean",
            self.department_a,
            role_active=False,
        )
        self.multi_department_dean = self._create_department_user("multi_dean", self.department_a)
        UserRole.objects.create(
            user=self.multi_department_dean,
            role=self.dean_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_b,
        )
        self.superadmin = User.objects.create_superuser(
            username="root",
            email="root@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

        self.all_template = self._create_template("ALL-TEMPLATE")
        self.department_a_template = self._create_template(
            "A-TEMPLATE",
            visibility=GradingTemplate.DepartmentVisibility.SELECTED,
            departments=[self.department_a],
        )
        self.department_b_template = self._create_template(
            "B-TEMPLATE",
            visibility=GradingTemplate.DepartmentVisibility.SELECTED,
            departments=[self.department_b],
        )

    def _create_department_user(self, username, department, *, role_active=True):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=user,
            role=self.dean_role,
            tenant=self.tenant,
            campus=self.campus,
            department=department,
            is_active=role_active,
        )
        return user

    def _create_template(self, code, *, visibility=GradingTemplate.DepartmentVisibility.ALL, departments=None):
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code=code,
            name=code.replace("-", " ").title(),
            department_visibility=visibility,
            is_published=True,
            is_active=True,
        )
        if departments:
            template.visible_departments.set(departments)
        return template

    def _request_for(self, user):
        request = self.factory.get("/")
        request.user = user
        request.session = {}
        ScopeService.attach_scope_to_request(request)
        return request

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session[ScopeService.SESSION_TENANT_KEY] = self.tenant.id
        session[ScopeService.SESSION_CAMPUS_KEY] = self.campus.id
        session.save()

    def _create_template_structure(self, template):
        period = GradingTemplatePeriod.objects.create(
            template=template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
            weight_percentage=Decimal("100.00"),
        )
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="CLASS_STANDING",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        subcomponent = GradingTemplateSubcomponent.objects.create(
            template_component=component,
            code="QUIZZES",
            name="Quizzes",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        detail = GradingTemplateDetail.objects.create(
            template_subcomponent=subcomponent,
            code="Q1",
            name="Quiz 1",
            weight_percentage=Decimal("100.00"),
            sort_order=1,
        )
        return period, component, subcomponent, detail

    def test_existing_template_default_is_all_departments(self):
        template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="LEGACY",
            name="Legacy Template",
        )
        self.assertEqual(
            template.department_visibility,
            GradingTemplate.DepartmentVisibility.ALL,
        )

    def test_visible_department_options_include_campus_and_department(self):
        form = GradingTemplateForm(
            instance=self.department_a_template,
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            department_queryset=Department.objects.filter(tenant=self.tenant),
        )
        choices = {
            int(getattr(value, "value", value)): label
            for value, label in form.fields["visible_departments"].choices
            if value
        }
        self.assertEqual(
            choices[self.department_a.id],
            "MAIN - Main Campus | DEPT-A - Department A",
        )

    def test_selected_visibility_form_requires_department_and_same_tenant(self):
        form = GradingTemplateForm(
            data={
                "tenant": self.tenant.id,
                "code": "FORM-SELECTED",
                "name": "Selected Template",
                "description": "",
                "default_base_value": "50.00",
                "passing_grade_threshold": "75.00",
                "department_visibility": GradingTemplate.DepartmentVisibility.SELECTED,
                "visible_departments": [],
                "is_active": "on",
            },
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            department_queryset=Department.objects.filter(tenant=self.tenant),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("visible_departments", form.errors)

        other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER", name="Other Campus")
        other_department = Department.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            code="OTHER-DEPT",
            name="Other Department",
        )
        cross_tenant_form = GradingTemplateForm(
            data={
                "tenant": self.tenant.id,
                "code": "FORM-CROSS-TENANT",
                "name": "Cross Tenant Template",
                "description": "",
                "default_base_value": "50.00",
                "passing_grade_threshold": "75.00",
                "department_visibility": GradingTemplate.DepartmentVisibility.SELECTED,
                "visible_departments": [other_department.id],
                "is_active": "on",
            },
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            department_queryset=Department.objects.filter(id__in=[self.department_a.id, other_department.id]),
        )
        self.assertFalse(cross_tenant_form.is_valid())
        self.assertIn("visible_departments", cross_tenant_form.errors)

    def test_all_departments_form_clears_stale_selected_departments(self):
        form = GradingTemplateForm(
            data={
                "tenant": self.tenant.id,
                "code": self.department_a_template.code,
                "name": self.department_a_template.name,
                "description": "",
                "default_base_value": "50.00",
                "passing_grade_threshold": "75.00",
                "department_visibility": GradingTemplate.DepartmentVisibility.ALL,
                "visible_departments": [self.department_a.id],
                "is_active": "on",
            },
            instance=self.department_a_template,
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            department_queryset=Department.objects.filter(tenant=self.tenant),
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        template = form.save()
        self.assertEqual(template.department_visibility, GradingTemplate.DepartmentVisibility.ALL)
        self.assertFalse(template.visible_departments.exists())

    def test_department_scoped_visibility_and_multiple_departments(self):
        request_a = self._request_for(self.dean_a)
        request_b = self._request_for(self.dean_b)
        request_multi = self._request_for(self.multi_department_dean)

        self.assertQuerySetEqual(
            AdminScopeService.scoped_grading_templates(request_a).order_by("code"),
            [self.department_a_template, self.all_template],
        )
        self.assertQuerySetEqual(
            AdminScopeService.scoped_grading_templates(request_b).order_by("code"),
            [self.all_template, self.department_b_template],
        )
        self.assertQuerySetEqual(
            AdminScopeService.scoped_grading_templates(request_multi).order_by("code"),
            [self.department_a_template, self.all_template, self.department_b_template],
        )

    def test_inactive_role_assignment_does_not_grant_selected_visibility(self):
        self.assertFalse(
            GradingTemplateAccessService.user_can_access_grading_template(
                self.inactive_dean,
                self.department_a_template,
            )
        )

    def test_inactive_department_does_not_grant_selected_visibility(self):
        self.department_a.is_active = False
        self.department_a.save(update_fields=["is_active", "updated_at"])
        self.assertFalse(
            GradingTemplateAccessService.user_can_access_grading_template(
                self.dean_a,
                self.department_a_template,
            )
        )

    def test_parent_department_assignment_covers_active_child_department(self):
        child_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            parent=self.department_a,
            code="DEPT-A-CHILD",
            name="Department A Child",
        )
        child_template = self._create_template(
            "A-CHILD-TEMPLATE",
            visibility=GradingTemplate.DepartmentVisibility.SELECTED,
            departments=[child_department],
        )
        self.assertTrue(
            GradingTemplateAccessService.user_can_access_grading_template(
                self.dean_a,
                child_template,
            )
        )

    def test_selected_template_requires_both_permission_and_matching_department(self):
        no_department_user = User.objects.create_user(
            username="tenant_dean",
            email="tenant_dean@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=no_department_user,
            role=self.dean_role,
            tenant=self.tenant,
            campus=self.campus,
            department=None,
        )
        self.assertFalse(
            GradingTemplateAccessService.user_can_govern_grading_template(
                no_department_user,
                self.department_a_template,
                permission_code="grading_templates.approve",
                campus_id=self.campus.id,
            )
        )

        limited_role = Role.objects.create(code="LIMITED_DEPT_ADMIN", name="Limited Department Admin")
        RolePermission.objects.create(
            role=limited_role,
            permission=self.permissions["admin_portal.access"],
        )
        limited_user = User.objects.create_user(
            username="limited_dept_admin",
            email="limited_dept_admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department_a,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=limited_user,
            role=limited_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department_a,
        )
        self.assertTrue(
            GradingTemplateAccessService.user_can_access_grading_template(
                limited_user,
                self.department_a_template,
            )
        )
        self.assertFalse(
            GradingTemplateAccessService.user_can_govern_grading_template(
                limited_user,
                self.department_a_template,
                permission_code="grading_templates.approve",
                campus_id=self.campus.id,
            )
        )

    def test_superadmin_can_see_all_templates(self):
        request = self._request_for(self.superadmin)
        self.assertEqual(AdminScopeService.scoped_grading_templates(request).count(), 3)

    def test_direct_url_access_and_actions_are_blocked_for_other_department(self):
        self._login(self.dean_a)
        hidden_id = self.department_b_template.id
        routes = (
            ("admin_portal:grading_template_builder", {"template_id": hidden_id}, "get"),
            ("admin_portal:grading_template_structure", {"template_id": hidden_id}, "get"),
            ("admin_portal:grading_template_update", {"template_id": hidden_id}, "get"),
            ("admin_portal:grading_template_duplicate", {"template_id": hidden_id}, "post"),
            ("admin_portal:grading_template_publish", {"template_id": hidden_id}, "post"),
            ("admin_portal:grading_template_review_approval", {"template_id": hidden_id}, "get"),
            ("admin_portal:template_hotfix_create", {"template_id": hidden_id}, "get"),
        )
        for route_name, kwargs, method in routes:
            with self.subTest(route=route_name):
                response = getattr(self.client, method)(reverse(route_name, kwargs=kwargs))
                self.assertEqual(response.status_code, 404)

    def test_nested_template_urls_and_forged_parent_posts_are_blocked(self):
        period, component, subcomponent, detail = self._create_template_structure(self.department_b_template)
        self._login(self.dean_a)

        routes = (
            ("admin_portal:template_period_update", {"period_id": period.id}, "get"),
            ("admin_portal:template_component_update", {"component_id": component.id}, "get"),
            ("admin_portal:template_component_delete", {"component_id": component.id}, "post"),
            ("admin_portal:template_subcomponent_update", {"subcomponent_id": subcomponent.id}, "get"),
            ("admin_portal:template_detail_update", {"detail_id": detail.id}, "get"),
        )
        for route_name, kwargs, method in routes:
            with self.subTest(route=route_name):
                response = getattr(self.client, method)(reverse(route_name, kwargs=kwargs))
                self.assertEqual(response.status_code, 404)

        period_count = GradingTemplatePeriod.objects.count()
        response = self.client.post(
            reverse("admin_portal:template_period_create"),
            {
                "template": self.department_b_template.id,
                "code": "MIDTERM",
                "name": "Midterm",
                "sequence_no": 2,
                "weight_percentage": "100.00",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(GradingTemplatePeriod.objects.count(), period_count)
        self.assertFormError(
            response.context["form"],
            "template",
            "Select a valid choice. That choice is not one of the available choices.",
        )

    def test_list_calculator_and_dropdowns_hide_other_department_template(self):
        self._login(self.dean_a)

        list_response = self.client.get(reverse("admin_portal:grading_template_list"))
        self.assertContains(list_response, self.department_a_template.code)
        self.assertContains(list_response, self.all_template.code)
        self.assertNotContains(list_response, self.department_b_template.code)

        calculator_response = self.client.get(reverse("admin_portal:grading_template_calculator"))
        self.assertContains(calculator_response, self.department_a_template.name)
        self.assertContains(calculator_response, self.all_template.name)
        self.assertNotContains(calculator_response, self.department_b_template.name)

        profile_response = self.client.get(reverse("admin_portal:tenant_grading_profile_create"))
        self.assertContains(profile_response, self.department_a_template.name)
        self.assertContains(profile_response, self.all_template.name)
        self.assertNotContains(profile_response, self.department_b_template.name)

        assignment_response = self.client.get(reverse("admin_portal:course_template_assignment_create"))
        self.assertContains(assignment_response, self.department_a_template.name)
        self.assertContains(assignment_response, self.all_template.name)
        self.assertNotContains(assignment_response, self.department_b_template.name)

        forged_calculator = self.client.get(
            reverse("admin_portal:grading_template_calculator"),
            {"grading_template": self.department_b_template.id, "sample_value": "85.00"},
        )
        self.assertEqual(forged_calculator.status_code, 200)
        self.assertIsNone(forged_calculator.context["selected_template"])
        self.assertFormError(
            forged_calculator.context["form"],
            "grading_template",
            "Select a valid choice. That choice is not one of the available choices.",
        )

    def test_forged_hidden_template_ids_cannot_create_profile_or_assignment(self):
        hidden_period, _component, _subcomponent, _detail = self._create_template_structure(
            self.department_b_template
        )
        self._login(self.dean_a)

        profile_count = TenantGradingProfile.objects.count()
        profile_response = self.client.post(
            reverse("admin_portal:tenant_grading_profile_create"),
            {
                "tenant": self.tenant.id,
                "campus": self.campus.id,
                "department": self.department_a.id,
                "program": "",
                "course": "",
                "course_type": "",
                "term_type": "",
                "profile_code": "FORGED-PROFILE",
                "profile_name": "Forged Profile",
                "grading_template": self.department_b_template.id,
                "default_base_value": "50.00",
                "passing_grade_threshold": "75.00",
                "period_grade_formula_mode": TenantGradingProfile.PeriodGradeFormulaMode.WEIGHTED_COMPONENTS,
                "deped_transmutation_table_text": "",
                "final_grade_formula_mode": TenantGradingProfile.FinalGradeFormulaMode.AVERAGE_ACTIVE_PERIODS,
                "final_grade_period_weights_text": "",
                "priority": 100,
                "effective_from_term": self.term.id,
                "is_default": "",
                "is_active": "on",
            },
        )
        self.assertEqual(profile_response.status_code, 200)
        self.assertEqual(TenantGradingProfile.objects.count(), profile_count)
        self.assertFormError(
            profile_response.context["form"],
            "grading_template",
            "Select a valid choice. That choice is not one of the available choices.",
        )
        self.assertNotContains(
            profile_response,
            f"Active template periods for this profile: {hidden_period.code}.",
        )

        assignment_count = CourseTemplateAssignment.objects.count()
        assignment_response = self.client.post(
            reverse("admin_portal:course_template_assignment_create"),
            {
                "courses": [self.course.id],
                "grading_template": self.department_b_template.id,
                "effective_from_term": self.term.id,
                "is_active": "on",
            },
        )
        self.assertEqual(assignment_response.status_code, 200)
        self.assertEqual(CourseTemplateAssignment.objects.count(), assignment_count)
        self.assertFormError(
            assignment_response.context["form"],
            "grading_template",
            "Select a valid choice. That choice is not one of the available choices.",
        )

    def test_hidden_prior_course_assignment_name_is_not_disclosed(self):
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.department_b_template,
            effective_from_term=self.term,
        )
        self._login(self.dean_a)
        response = self.client.post(
            reverse("admin_portal:course_template_assignment_create"),
            {
                "courses": [self.course.id],
                "grading_template": self.all_template.id,
                "effective_from_term": self.term.id,
                "is_active": "on",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has a prior grading template assignment")
        self.assertNotContains(response, self.department_b_template.name)
        self.assertEqual(CourseTemplateAssignment.objects.count(), 1)

    def test_hotfix_queue_and_direct_review_hide_other_department_template(self):
        hotfix = TemplateHotfixRequest.objects.create(
            tenant=self.tenant,
            template=self.department_b_template,
            apply_mode=TemplateHotfixRequest.ApplyMode.FUTURE_ONLY,
            justification="Restricted department request.",
            requested_by_user=self.dean_b,
        )
        self._login(self.dean_a)
        list_response = self.client.get(reverse("admin_portal:template_hotfix_list"))
        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, self.department_b_template.code)

        review_response = self.client.get(
            reverse("admin_portal:template_hotfix_review", kwargs={"hotfix_id": hotfix.id})
        )
        self.assertEqual(review_response.status_code, 404)

    def test_grading_setup_guide_explains_department_visibility(self):
        self._login(self.dean_a)
        response = self.client.get(reverse("admin_portal:grading_setup_guide"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose who can see and govern the template")
        self.assertContains(response, "Selected Departments")

    def test_governance_requires_permission_and_matching_department(self):
        self.assertTrue(
            GradingTemplateAccessService.user_can_govern_grading_template(
                self.dean_a,
                self.department_a_template,
                permission_code="grading_templates.approve",
                campus_id=self.campus.id,
            )
        )

    def test_superadmin_dropdowns_include_all_department_templates(self):
        self._login(self.superadmin)
        for route_name in (
            "admin_portal:grading_template_calculator",
            "admin_portal:tenant_grading_profile_create",
            "admin_portal:course_template_assignment_create",
        ):
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, self.department_a_template.name)
                self.assertContains(response, self.department_b_template.name)
        self.assertFalse(
            GradingTemplateAccessService.user_can_govern_grading_template(
                self.dean_a,
                self.department_b_template,
                permission_code="grading_templates.approve",
                campus_id=self.campus.id,
            )
        )

    def test_authorized_department_users_can_submit_review_and_publish(self):
        template = self._create_template(
            "A-GOVERNANCE",
            visibility=GradingTemplate.DepartmentVisibility.SELECTED,
            departments=[self.department_a],
        )
        template.is_published = False
        template.save(update_fields=["is_published", "updated_at"])
        self._create_template_structure(template)
        for stage_code in (
            TemplateGovernanceWorkflowService.STAGE_SUBMIT_FOR_APPROVAL,
            TemplateGovernanceWorkflowService.STAGE_APPROVAL_REVIEW,
            TemplateGovernanceWorkflowService.STAGE_PUBLISH,
        ):
            SystemSettingService.set(
                TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[stage_code],
                ["DEAN"],
                tenant_id=self.tenant.id,
                value_type="JSON",
                is_active=True,
            )

        self._login(self.dean_a)
        submit_response = self.client.post(
            reverse(
                "admin_portal:grading_template_submit_for_approval",
                kwargs={"template_id": template.id},
            ),
            {"remarks": "Department A submission."},
        )
        self.assertEqual(submit_response.status_code, 302)
        template.refresh_from_db()
        self.assertEqual(template.approval_status, GradingTemplate.ApprovalStatus.FOR_APPROVAL)

        self._login(self.multi_department_dean)
        review_response = self.client.post(
            reverse(
                "admin_portal:grading_template_review_approval",
                kwargs={"template_id": template.id},
            ),
            {"decision": "APPROVE", "remarks": "Department A review."},
        )
        self.assertEqual(review_response.status_code, 302)
        template.refresh_from_db()
        self.assertEqual(template.approval_status, GradingTemplate.ApprovalStatus.APPROVED)

        self._login(self.dean_a)
        publish_response = self.client.post(
            reverse(
                "admin_portal:grading_template_publish",
                kwargs={"template_id": template.id},
            ),
        )
        self.assertEqual(publish_response.status_code, 302)
        template.refresh_from_db()
        self.assertTrue(template.is_published)

    def test_authorized_department_users_can_request_and_apply_hotfix(self):
        for stage_code in (
            TemplateGovernanceWorkflowService.STAGE_HOTFIX_REQUEST,
            TemplateGovernanceWorkflowService.STAGE_HOTFIX_REVIEW_APPLY,
        ):
            SystemSettingService.set(
                TemplateGovernanceWorkflowService.STAGE_ROLE_KEYS[stage_code],
                ["DEAN"],
                tenant_id=self.tenant.id,
                value_type="JSON",
                is_active=True,
            )

        self._login(self.dean_a)
        create_response = self.client.post(
            reverse(
                "admin_portal:template_hotfix_create",
                kwargs={"template_id": self.department_a_template.id},
            ),
            {
                "apply_mode": TemplateHotfixRequest.ApplyMode.FUTURE_ONLY,
                "justification": "Department A governed hotfix.",
            },
        )
        self.assertEqual(create_response.status_code, 302)
        hotfix = TemplateHotfixRequest.objects.get(template=self.department_a_template)

        self._login(self.multi_department_dean)
        review_response = self.client.post(
            reverse("admin_portal:template_hotfix_review", kwargs={"hotfix_id": hotfix.id}),
            {
                "decision": "APPROVE",
                "review_remarks": "Approved for Department A.",
                "confirmation_phrase": "APPLY HOTFIX",
            },
        )
        self.assertEqual(review_response.status_code, 302)
        hotfix.refresh_from_db()
        self.assertEqual(hotfix.status, TemplateHotfixRequest.Status.APPLIED)

    def test_duplicate_preserves_department_visibility(self):
        duplicate, _counts = GradingTemplateDuplicationService.duplicate_template(
            source=self.department_a_template
        )
        self.assertEqual(
            duplicate.department_visibility,
            GradingTemplate.DepartmentVisibility.SELECTED,
        )
        self.assertQuerySetEqual(
            duplicate.visible_departments.order_by("id"),
            [self.department_a],
        )
