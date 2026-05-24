from datetime import date

from django.test import TestCase
from django.utils import timezone

from apps.academics.models import (
    AcademicYear,
    ActiveGradingPeriodSetting,
    TenantTermGradingPeriod,
    Term,
)
from apps.academics.services import AcademicGovernanceService
from apps.grading.models import GradingPeriodLock
from apps.tenants.models import Campus, Tenant


class ActiveGradingPeriodServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-FAIRVIEW", name="Fairview")
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
        self.prelim = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        self.midterm = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant,
            term=self.term,
            code="MIDTERM",
            name="Midterm",
            sequence_no=2,
        )

    def test_resolve_active_grading_period_auto_advances_when_deadline_passes(self):
        setting = ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=self.prelim,
        )
        GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code="PRELIM",
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.now() - timezone.timedelta(minutes=10),
            is_locked=False,
            is_active=True,
        )

        resolved = AcademicGovernanceService.resolve_active_grading_period(
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            term_id=self.term.id,
            now=timezone.now(),
        )

        self.assertEqual(resolved.id, setting.id)
        self.assertEqual(resolved.period_id, self.midterm.id)
        self.assertTrue(resolved.auto_advanced_from_deadline)

    def test_template_period_match_uses_canonical_period_keys(self):
        setting = ActiveGradingPeriodSetting.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            term=self.term,
            period=self.prelim,
        )

        template_like_period = type("TemplatePeriod", (), {"code": "GENED_PRELIM", "name": "PRELIM"})()

        self.assertTrue(
            AcademicGovernanceService.template_period_matches_active_period(
                template_period=template_like_period,
                active_period_setting=setting,
            )
        )

    def test_seed_standard_periods_reactivates_existing_inactive_rows(self):
        self.prelim.is_active = False
        self.prelim.save(update_fields=["is_active"])

        changed_rows = AcademicGovernanceService.seed_standard_term_periods(
            tenant_id=self.tenant.id,
            term=self.term,
        )

        self.prelim.refresh_from_db()
        self.assertTrue(self.prelim.is_active)
        self.assertIn(self.prelim.id, {row.id for row in changed_rows})
