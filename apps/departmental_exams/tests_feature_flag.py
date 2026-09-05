from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService
from apps.core.services.menu import MenuService
from apps.core.services.settings import SystemSettingService
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Tenant


class DepartmentalExamFeatureFlagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant_a = Tenant.objects.create(code="DEA", name="Departmental Exams A")
        cls.tenant_b = Tenant.objects.create(code="DEB", name="Departmental Exams B")
        cls.user = get_user_model().objects.create_superuser(
            "de-feature-admin",
            "de-feature@example.edu",
            "FeaturePass123!",
            default_tenant=cls.tenant_a,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        permission_specs = {
            "admin_portal.access": ("admin_portal", "access"),
            "system_settings.update": ("system_settings", "update"),
            "departmental_exams.manage_cycles": ("departmental_exams", "manage_cycles"),
            "departmental_exams.configure": ("departmental_exams", "configure"),
            "departmental_exams.review_generate": ("departmental_exams", "review_generate"),
            "faculty_portal.access": ("faculty_portal", "access"),
        }
        for code, (module, action) in permission_specs.items():
            Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": action, "is_active": True},
            )
        cls.permission = Permission.objects.get(code="departmental_exams.manage_cycles")
        cls.faculty_permission = Permission.objects.get(code="faculty_portal.access")
        cls.group = MenuGroup.objects.get(portal="ADMIN", code="DEPARTMENTAL_EXAMS")
        cls.item, _ = MenuItem.objects.get_or_create(menu_group=cls.group, portal="ADMIN", code="DE_EXAM_TEST", defaults={"label": "Exam Cycles"})
        MenuItemPermission.objects.get_or_create(menu_item=cls.item, permission=cls.permission)
        cls.faculty_group = MenuGroup.objects.get(
            portal="FACULTY", code="DEPARTMENTAL_EXAMS"
        )
        cls.faculty_item = MenuItem.objects.create(
            menu_group=cls.faculty_group,
            portal="FACULTY",
            code="DE_EXAM_FACULTY_TEST",
            label="Future Faculty Workflow",
        )
        MenuItemPermission.objects.create(
            menu_item=cls.faculty_item, permission=cls.faculty_permission
        )

    def _feature_settings_payload(self, *, enabled, structured=False):
        payload = {
            "grade_deadline_enforcement_policy": "AUTO_CLOSE_REQUIRES_REOPEN",
            "enrollment_ownership_mode": "ADMIN_ONLY",
            "login_lockout_max_attempts": "5",
            "login_lockout_window_minutes": "15",
            "login_lockout_duration_minutes": "15",
            "faculty_assignment_response_window_days": "3",
            "faculty_assignment_first_reminder_days": "1",
            "faculty_assignment_repeat_reminder_days": "1",
            "grade_prediction_default_assumption": "IGNORE_MISSING",
        }
        if enabled:
            payload["departmental_exam_builder_enabled"] = "on"
        if structured:
            payload["departmental_exam_structured_lifecycle_enabled"] = "on"
        return payload

    def test_absent_setting_defaults_off_and_is_tenant_scoped(self):
        self.assertFalse(FeatureSettingsService.is_departmental_exam_builder_enabled(tenant_id=self.tenant_a.id))
        SystemSettingService.set(FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY, True, tenant_id=self.tenant_a.id, value_type="BOOL")
        self.assertTrue(FeatureSettingsService.is_departmental_exam_builder_enabled(tenant_id=self.tenant_a.id))
        self.assertFalse(FeatureSettingsService.is_departmental_exam_builder_enabled(tenant_id=self.tenant_b.id))

    def test_structured_exam_lifecycle_defaults_off_and_is_tenant_scoped(self):
        self.assertFalse(
            FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
                tenant_id=self.tenant_a.id
            )
        )
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
            True,
            tenant_id=self.tenant_a.id,
            value_type="BOOL",
        )
        self.assertTrue(
            FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
                tenant_id=self.tenant_a.id
            )
        )
        self.assertFalse(
            FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
                tenant_id=self.tenant_b.id
            )
        )

    def test_configurable_features_can_enable_structured_exam_lifecycle(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("admin_portal:configurable_features_settings"),
            self._feature_settings_payload(enabled=True, structured=True),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
                tenant_id=self.tenant_a.id
            )
        )
        self.assertFalse(
            FeatureSettingsService.is_departmental_exam_structured_lifecycle_enabled(
                tenant_id=self.tenant_b.id
            )
        )

    def test_configurable_features_get_shows_only_current_foundation_capabilities(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("admin_portal:configurable_features_settings")
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            response.context["form"].initial["departmental_exam_builder_enabled"]
        )
        self.assertContains(response, "examination-cycle management")
        self.assertContains(response, "grouped course administration")
        self.assertContains(response, "Included/Exempt course control")
        self.assertNotContains(response, "Course Setup")
        self.assertContains(response, "faculty question contribution")

    def test_configurable_features_post_enables_departmental_exam_builder_for_current_tenant(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("admin_portal:configurable_features_settings"),
            self._feature_settings_payload(enabled=True),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            FeatureSettingsService.is_departmental_exam_builder_enabled(
                tenant_id=self.tenant_a.id
            )
        )
        self.assertFalse(
            FeatureSettingsService.is_departmental_exam_builder_enabled(
                tenant_id=self.tenant_b.id
            )
        )

    def test_configurable_features_post_disables_departmental_exam_builder_for_current_tenant(self):
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY,
            True,
            tenant_id=self.tenant_a.id,
            value_type="BOOL",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("admin_portal:configurable_features_settings"),
            self._feature_settings_payload(enabled=False),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            FeatureSettingsService.is_departmental_exam_builder_enabled(
                tenant_id=self.tenant_a.id
            )
        )

    def test_menu_requires_enabled_feature_and_permission(self):
        off_tree = MenuService.get_menu_tree(self.user, portal="ADMIN", tenant_id=self.tenant_a.id, effective_codes={self.permission.code})
        self.assertFalse(any(row["group"].code == "DEPARTMENTAL_EXAMS" for row in off_tree))
        SystemSettingService.set(FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY, True, tenant_id=self.tenant_a.id, value_type="BOOL")
        on_tree = MenuService.get_menu_tree(self.user, portal="ADMIN", tenant_id=self.tenant_a.id, effective_codes={self.permission.code})
        self.assertTrue(any(row["group"].code == "DEPARTMENTAL_EXAMS" for row in on_tree))
        denied_tree = MenuService.get_menu_tree(self.user, portal="ADMIN", tenant_id=self.tenant_a.id, effective_codes=set())
        self.assertFalse(any(row["group"].code == "DEPARTMENTAL_EXAMS" for row in denied_tree))

    def test_seeded_assigned_courses_menu_is_tenant_gated_and_permission_or_visible(self):
        configured = Permission.objects.get(code="departmental_exams.configure")
        reviewer = Permission.objects.get(code="departmental_exams.review_generate")
        assigned_item = MenuItem.objects.get(
            portal="ADMIN", code="DE_EXAM_ASSIGNED_COURSES"
        )
        self.assertEqual(
            assigned_item.route_name,
            "departmental_exams:assigned_course_examinations",
        )
        self.assertNotEqual(assigned_item.route_name, "#")

        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY,
            True,
            tenant_id=self.tenant_a.id,
            value_type="BOOL",
        )
        for effective_codes in ({configured.code}, {reviewer.code}):
            with self.subTest(effective_codes=effective_codes):
                tree = MenuService.get_menu_tree(
                    self.user,
                    portal="ADMIN",
                    tenant_id=self.tenant_a.id,
                    effective_codes=effective_codes,
                )
                visible_codes = {
                    item["item"].code for group in tree for item in group["items"]
                }
                self.assertIn("DE_EXAM_ASSIGNED_COURSES", visible_codes)

        tenant_b_tree = MenuService.get_menu_tree(
            self.user,
            portal="ADMIN",
            tenant_id=self.tenant_b.id,
            effective_codes={configured.code, reviewer.code},
        )
        self.assertFalse(
            any(row["group"].code == "DEPARTMENTAL_EXAMS" for row in tenant_b_tree)
        )

    def test_actual_permissions_enforce_assigned_menu_deny_tenant_and_feature_scope(self):
        campus_a = Campus.objects.create(
            tenant=self.tenant_a,
            code="DE-MENU-A",
            name="DE Menu A",
        )
        department_a = Department.objects.create(
            tenant=self.tenant_a,
            campus=campus_a,
            code="DE-MENU-A",
            name="DE Menu A",
        )
        campus_b = Campus.objects.create(
            tenant=self.tenant_b,
            code="DE-MENU-B",
            name="DE Menu B",
        )
        department_b = Department.objects.create(
            tenant=self.tenant_b,
            campus=campus_b,
            code="DE-MENU-B",
            name="DE Menu B",
        )
        admin_access = Permission.objects.get(code="admin_portal.access")
        configure = Permission.objects.get(code="departmental_exams.configure")
        review = Permission.objects.get(code="departmental_exams.review_generate")

        def scoped_user(username, *, permission, tenant, campus, department):
            user = get_user_model().objects.create_user(
                username,
                f"{username}@example.edu",
                "FeaturePass123!",
                default_tenant=self.tenant_a,
                default_campus=campus_a,
                default_department=department_a,
                privacy_consent_version=getattr(
                    settings, "PRIVACY_CONSENT_VERSION", "2026-03"
                ),
                privacy_consent_at=timezone.now(),
            )
            role = Role.objects.create(
                code=f"DE_MENU_{username.upper()}",
                name=f"DE Menu {username}",
            )
            RolePermission.objects.create(role=role, permission=admin_access)
            RolePermission.objects.create(role=role, permission=permission)
            UserRole.objects.create(
                user=user,
                role=role,
                tenant=tenant,
                campus=campus,
                department=department,
            )
            return user

        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY,
            True,
            tenant_id=self.tenant_a.id,
            value_type="BOOL",
        )

        configure_user = scoped_user(
            "actual-configure",
            permission=configure,
            tenant=self.tenant_a,
            campus=campus_a,
            department=department_a,
        )
        reviewer_user = scoped_user(
            "actual-reviewer",
            permission=review,
            tenant=self.tenant_a,
            campus=campus_a,
            department=department_a,
        )

        def assigned_visible(user):
            return "DE_EXAM_ASSIGNED_COURSES" in {
                node["item"].code
                for group in MenuService.get_menu_tree(
                    user,
                    portal="ADMIN",
                    tenant_id=self.tenant_a.id,
                    campus_id=campus_a.id,
                )
                for node in group["items"]
            }

        self.assertTrue(assigned_visible(configure_user))
        self.assertTrue(assigned_visible(reviewer_user))

        UserPermission.objects.create(
            user=configure_user,
            permission=configure,
            grant_type=UserPermission.GrantType.DENY,
            tenant=None,
            campus=None,
        )
        self.assertTrue(assigned_visible(configure_user))
        UserPermission.objects.create(
            user=configure_user,
            permission=configure,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant_a,
            campus=campus_a,
        )
        self.assertFalse(assigned_visible(configure_user))

        other_tenant_permission = scoped_user(
            "other-tenant-configure",
            permission=configure,
            tenant=self.tenant_b,
            campus=campus_b,
            department=department_b,
        )
        self.assertFalse(assigned_visible(other_tenant_permission))

        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY,
            False,
            tenant_id=self.tenant_a.id,
            value_type="BOOL",
        )
        self.assertFalse(assigned_visible(reviewer_user))
        self.assertFalse(assigned_visible(self.user))
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(
                reverse("departmental_exams:assigned_course_examinations")
            ).status_code,
            403,
        )

    def test_faculty_group_is_hidden_while_off_and_requires_permission_when_on(self):
        off_tree = MenuService.get_menu_tree(
            self.user,
            portal="FACULTY",
            tenant_id=self.tenant_a.id,
            effective_codes={self.faculty_permission.code},
        )
        self.assertFalse(any(row["group"].code == "DEPARTMENTAL_EXAMS" for row in off_tree))
        SystemSettingService.set(
            FeatureSettingsService.DEPARTMENTAL_EXAM_BUILDER_ENABLED_KEY,
            True,
            tenant_id=self.tenant_a.id,
            value_type="BOOL",
        )
        on_tree = MenuService.get_menu_tree(
            self.user,
            portal="FACULTY",
            tenant_id=self.tenant_a.id,
            effective_codes={self.faculty_permission.code},
        )
        self.assertTrue(any(row["group"].code == "DEPARTMENTAL_EXAMS" for row in on_tree))
        denied_tree = MenuService.get_menu_tree(
            self.user,
            portal="FACULTY",
            tenant_id=self.tenant_a.id,
            effective_codes=set(),
        )
        self.assertFalse(any(row["group"].code == "DEPARTMENTAL_EXAMS" for row in denied_tree))
