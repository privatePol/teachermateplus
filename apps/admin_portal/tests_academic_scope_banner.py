from datetime import date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Term
from apps.academics.services import AcademicGovernanceService
from apps.accounts.models import User
from apps.core.services.settings import SystemSettingService
from apps.rbac.models import Permission
from apps.tenants.models import Campus, Tenant


class AdminAcademicScopeBannerTests(TestCase):
    def setUp(self):
        Permission.objects.create(code="admin_portal.access", module="admin_portal", action="access")
        Permission.objects.create(code="dashboard.read", module="dashboard", action="read")
        Permission.objects.create(code="system_settings.update", module="system_settings", action="update")
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-01", name="Cubao")
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2526",
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
        )
        SystemSettingService.set(
            AcademicGovernanceService.ACTIVE_AY_KEY,
            self.academic_year.code,
            tenant_id=self.tenant.id,
            value_type="STRING",
        )
        SystemSettingService.set(
            AcademicGovernanceService.ACTIVE_TERM_KEY,
            self.term.code,
            tenant_id=self.tenant.id,
            value_type="STRING",
        )
        self.admin = User.objects.create_superuser(
            username="scope_banner_admin",
            email="scope_banner_admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def test_admin_topbar_displays_current_academic_scope(self):
        response = self.client.get(reverse("admin_portal:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Academic Scope:")
        self.assertContains(response, "AY2526 / 1ST")
        self.assertContains(response, "AY 2025-2026 | First Term")
        self.assertContains(response, "scope_banner_admin")
        self.assertContains(response, "Superadmin")

    def test_active_academic_scope_save_persists_term_for_topbar_resolution(self):
        SystemSettingService.set(
            AcademicGovernanceService.ACTIVE_TERM_KEY,
            "",
            tenant_id=self.tenant.id,
            value_type="STRING",
            is_active=False,
        )

        response = self.client.post(
            reverse("admin_portal:active_academic_term_settings"),
            {
                "active_academic_year": self.academic_year.id,
                "active_term": self.term.id,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            SystemSettingService.get(AcademicGovernanceService.ACTIVE_TERM_KEY, tenant_id=self.tenant.id),
            "1ST",
        )
        active_ay, active_term = AcademicGovernanceService.resolve_active_scope(tenant_id=self.tenant.id)
        self.assertEqual(active_ay, self.academic_year)
        self.assertEqual(active_term, self.term)
