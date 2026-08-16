from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.services.menu import MenuService
from apps.core.services.settings import SystemSettingService
from apps.navigation.models import MenuItem
from apps.rbac.models import (
    Permission,
    Role,
    RolePermission,
    UserPermission,
    UserRole,
)
from apps.tenants.models import Campus

from .models import ExaminationCycle
from .stage4_test_support import Stage4TestCase


class AutomaticGenerationSummaryNavigationTests(Stage4TestCase):
    def setUp(self):
        super().setUp()
        self.summary_user = self.make_user(
            "automatic-summary-user",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.view_generated_exams",
            ),
        )
        self.client = Client()
        self.client.force_login(self.summary_user)
        self.entry_url = reverse(
            "departmental_exams:automatic_generation_summary_entry"
        )

    def _automatic_cycle(self, suffix):
        cycle = self.make_cycle(scope_suffix=suffix)
        cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        cycle.save(update_fields=["processing_mode", "updated_at"])
        self.make_course(cycle=cycle, department=None, code=f"AUTO-{suffix}")
        return cycle

    def _other_tenant_admin(self):
        campus = Campus.objects.create(
            tenant=self.other_tenant,
            code="OTHER-MAIN",
            name="Other Main",
        )
        SystemSettingService.set(
            "FEATURE_DEPARTMENTAL_EXAM_BUILDER_ENABLED",
            True,
            tenant_id=self.other_tenant.id,
            value_type="BOOL",
        )
        user = get_user_model().objects.create_user(
            "other-tenant-summary-user",
            "other-tenant-summary@example.edu",
            "Pass123!",
            default_tenant=self.other_tenant,
            default_campus=campus,
            privacy_consent_version=getattr(
                settings, "PRIVACY_CONSENT_VERSION", "2026-03"
            ),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(
            code="OTHER_TENANT_SUMMARY",
            name="Other Tenant Summary",
        )
        for code in (
            "admin_portal.access",
            "departmental_exams.view_generated_exams",
        ):
            RolePermission.objects.create(
                role=role,
                permission=Permission.objects.get(code=code),
            )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.other_tenant,
            campus=campus,
        )
        return user

    def test_one_applicable_cycle_redirects_to_its_dynamic_summary_url(self):
        cycle = self._automatic_cycle("one-dynamic")

        response = self.client.get(self.entry_url)

        self.assertRedirects(
            response,
            reverse(
                "departmental_exams:automatic_generation_summary",
                args=[cycle.id],
            ),
            fetch_redirect_response=False,
        )

    def test_multiple_applicable_cycles_render_selector(self):
        first_cycle = self._automatic_cycle("multiple-one")
        second_cycle = self._automatic_cycle("multiple-two")

        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response,
            "departmental_exams/admin/automatic_generation_summary_selector.html",
        )
        self.assertEqual(
            {cycle.id for cycle in response.context["cycles"]},
            {first_cycle.id, second_cycle.id},
        )
        for cycle in (first_cycle, second_cycle):
            self.assertContains(
                response,
                reverse(
                    "departmental_exams:automatic_generation_summary",
                    args=[cycle.id],
                ),
            )

    def test_zero_applicable_cycles_render_safe_empty_state(self):
        response = self.client.get(self.entry_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(tuple(response.context["cycles"]), ())
        self.assertContains(
            response,
            "No applicable Automatic exam cycles are available within your current tenant and authority.",
        )

    def test_selector_and_direct_summary_are_tenant_isolated(self):
        foreign_cycle = self._automatic_cycle("tenant-secret")
        client = Client()
        client.force_login(self._other_tenant_admin())

        selector = client.get(self.entry_url)

        self.assertEqual(selector.status_code, 200)
        self.assertEqual(tuple(selector.context["cycles"]), ())
        self.assertNotContains(selector, foreign_cycle.academic_year.name)
        direct = client.get(
            reverse(
                "departmental_exams:automatic_generation_summary",
                args=[foreign_cycle.id],
            )
        )
        self.assertEqual(direct.status_code, 404)

    def test_unauthorized_and_direct_denied_users_cannot_open_a_summary(self):
        cycle = self._automatic_cycle("denied")
        direct_url = reverse(
            "departmental_exams:automatic_generation_summary",
            args=[cycle.id],
        )
        unauthorized = self.make_user(
            "automatic-summary-unauthorized",
            self.department,
            ("admin_portal.access",),
        )
        denied = self.make_user(
            "automatic-summary-denied",
            self.department,
            (
                "admin_portal.access",
                "departmental_exams.view_generated_exams",
            ),
        )
        UserPermission.objects.create(
            user=denied,
            permission=Permission.objects.get(
                code="departmental_exams.view_generated_exams"
            ),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )

        for user in (unauthorized, denied):
            with self.subTest(user=user.username):
                client = Client()
                client.force_login(user)
                selector = client.get(self.entry_url)
                self.assertEqual(selector.status_code, 200)
                self.assertEqual(tuple(selector.context["cycles"]), ())
                self.assertNotContains(selector, direct_url)
                self.assertEqual(client.get(direct_url).status_code, 403)

    def test_menu_item_is_seeded_above_contributor_completion(self):
        item = MenuItem.objects.get(
            portal="ADMIN",
            code="DE_EXAM_AUTOMATIC_GENERATION_SUMMARY",
        )
        contributor = MenuItem.objects.get(
            portal="ADMIN",
            code="DE_EXAM_CONTRIBUTOR_MONITORING",
        )
        self.assertEqual(item.label, "Automatic Generation Summary")
        self.assertEqual(
            item.route_name,
            "departmental_exams:automatic_generation_summary_entry",
        )
        self.assertLess(item.sort_order, contributor.sort_order)
        self.assertEqual(
            set(
                item.menuitempermission_set.values_list(
                    "permission__code", flat=True
                )
            ),
            {
                "departmental_exams.view_generated_exams",
                "departmental_exams.manage_exam_generation",
            },
        )
        tree = MenuService.get_menu_tree(
            self.summary_user,
            portal="ADMIN",
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        visible_codes = {
            node["item"].code
            for group in tree
            for node in group["items"]
        }
        self.assertIn(item.code, visible_codes)
