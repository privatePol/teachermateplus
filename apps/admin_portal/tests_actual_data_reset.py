from datetime import date

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, Section, Term
from apps.auditlog.models import AuditLog
from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant


class ActualDataResetTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.admin_access = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
        )
        self.settings_update = Permission.objects.create(
            code="system_settings.update",
            module="system_settings",
            action="update",
        )
        self.actual_data_reset = Permission.objects.create(
            code="actual_data_reset.run",
            module="actual_data_reset",
            action="run",
        )
        self.role = Role.objects.create(code="SUPER_ADMIN", name="Super Admin", is_system=True)
        RolePermission.objects.create(role=self.role, permission=self.admin_access)
        RolePermission.objects.create(role=self.role, permission=self.settings_update)
        RolePermission.objects.create(role=self.role, permission=self.actual_data_reset)
        self.group = MenuGroup.objects.create(portal="ADMIN", code="IMPORTS", label="Tools")
        self.item = MenuItem.objects.create(
            menu_group=self.group,
            portal="ADMIN",
            code="ACTUAL_DATA_RESET",
            label="Actual Data Reset",
            route_name="admin_portal:actual_data_reset",
        )
        MenuItemPermission.objects.create(menu_item=self.item, permission=self.actual_data_reset)

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
            code="2026-2027",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 10, 31),
        )
        Course.objects.create(tenant=self.tenant, code="IT101", title="IT 101")
        Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSIT-1A",
            name="BSIT 1A",
        )
        SystemSetting.objects.create(
            tenant=None,
            setting_key="GLOBAL_ONLY",
            setting_value="1",
        )
        SystemSetting.objects.create(
            tenant=self.tenant,
            setting_key="TENANT_ONLY",
            setting_value="1",
        )
        UserRole.objects.create(user=self.admin, role=self.role, tenant=self.tenant, campus=self.campus)
        self.client.force_login(self.admin)

    def test_reset_page_shows_preview(self):
        response = self.client.get(reverse("admin_portal:actual_data_reset"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Actual Data Reset")
        self.assertContains(response, "Tenant / Campus / Department / Program")
        self.assertContains(response, "RESET ACTUAL DATA")

    def test_reset_keeps_security_shell_and_deletes_actual_data(self):
        response = self.client.post(
            reverse("admin_portal:actual_data_reset"),
            {
                "confirmation_phrase": "RESET ACTUAL DATA",
                "reset_reason": "Approved training-data rebuild.",
                "understood": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Tenant.objects.count(), 0)
        self.assertEqual(Campus.objects.count(), 0)
        self.assertEqual(Department.objects.count(), 0)
        self.assertEqual(Program.objects.count(), 0)
        self.assertEqual(AcademicYear.objects.count(), 0)
        self.assertEqual(Term.objects.count(), 0)
        self.assertEqual(Course.objects.count(), 0)
        self.assertEqual(Section.objects.count(), 0)
        self.assertEqual(UserRole.objects.count(), 0)
        self.assertEqual(SystemSetting.objects.filter(tenant__isnull=False).count(), 0)
        self.assertEqual(SystemSetting.objects.filter(tenant__isnull=True).count(), 1)
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(Role.objects.filter(code="SUPER_ADMIN").exists())
        self.assertTrue(Permission.objects.filter(code="admin_portal.access").exists())
        self.assertTrue(Permission.objects.filter(code="system_settings.update").exists())
        self.assertTrue(Permission.objects.filter(code="actual_data_reset.run").exists())
        self.assertEqual(RolePermission.objects.filter(role=self.role).count(), 3)
        self.assertEqual(MenuGroup.objects.count(), 1)
        self.assertEqual(MenuItem.objects.count(), 1)
        self.assertEqual(MenuItemPermission.objects.count(), 1)
        self.admin.refresh_from_db()
        self.assertIsNone(self.admin.default_tenant_id)
        reset_log = AuditLog.objects.get(entity_type="ActualDataReset", action="RESET")
        self.assertTrue(reset_log.metadata_json["critical_action"])
        self.assertEqual(reset_log.metadata_json["reason"], "Approved training-data rebuild.")
        self.assertIn("audit_export_path", reset_log.metadata_json)
        self.assertTrue(reset_log.metadata_json["audit_export_validation"]["ok"])

    @override_settings(DJANGO_ENV="production", ACTUAL_DATA_RESET_ALLOW_PRODUCTION=False)
    def test_reset_is_blocked_by_default_in_production(self):
        response = self.client.post(
            reverse("admin_portal:actual_data_reset"),
            {
                "confirmation_phrase": "RESET ACTUAL DATA",
                "reset_reason": "Production reset attempt.",
                "understood": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Tenant.objects.count(), 1)
        self.assertFalse(AuditLog.objects.filter(entity_type="ActualDataReset", action="RESET").exists())
        self.assertContains(response, "disabled in production")
