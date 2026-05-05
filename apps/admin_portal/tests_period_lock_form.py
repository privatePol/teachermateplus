from datetime import UTC, date

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import AcademicYear, Course, CourseOffering, Section, Term
from apps.accounts.models import User
from apps.admin_portal.forms import GradingPeriodLockForm
from apps.auditlog.models import AuditLog
from apps.grading.models import CourseTemplateAssignment, GradingPeriodLock, GradingTemplate, GradingTemplatePeriod
from apps.rbac.models import Permission
from apps.tenants.models import Campus, Department, Program, Tenant


class GradingPeriodLockFormTests(TestCase):
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
            code="2ND",
            name="Second Term",
            sequence_no=2,
            start_date=date(2025, 11, 1),
            end_date=date(2026, 3, 31),
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
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED_V1",
            name="General Education",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="GENED_PRELIM",
            name="Prelim",
            sequence_no=1,
        )
        CourseTemplateAssignment.objects.create(
            course=self.course,
            grading_template=self.template,
            effective_from_term=self.term,
            is_active=True,
        )

    def test_period_lock_form_rejects_arbitrary_term_code(self):
        form = GradingPeriodLockForm(
            data={
                "tenant": self.tenant.id,
                "campus": self.campus.id,
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "period_code": "2526_2NDSEM",
                "scope_type": "CAMPUS",
                "course_offering": "",
                "deadline_at": "2026-04-24T00:00",
                "remarks": "",
                "is_active": "on",
            },
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            offering_queryset=CourseOffering.objects.filter(id=self.offering.id),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("period_code", form.errors)

    def test_period_lock_form_accepts_template_period_code(self):
        form = GradingPeriodLockForm(
            data={
                "tenant": self.tenant.id,
                "campus": self.campus.id,
                "academic_year": self.academic_year.id,
                "term": self.term.id,
                "period_code": self.period.code,
                "scope_type": "CAMPUS",
                "course_offering": "",
                "deadline_at": "2026-04-24T00:00",
                "remarks": "",
                "is_active": "on",
            },
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            offering_queryset=CourseOffering.objects.filter(id=self.offering.id),
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_period_lock_form_falls_back_to_tenant_periods_when_course_assignment_lookup_is_empty(self):
        CourseTemplateAssignment.objects.all().delete()

        form = GradingPeriodLockForm(
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            offering_queryset=CourseOffering.objects.filter(id=self.offering.id),
        )

        choices = [value for value, _label in form.fields["period_code"].choices if value]
        self.assertIn(self.period.code, choices)

    def test_period_lock_form_renders_period_code_options_in_html(self):
        form = GradingPeriodLockForm(
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            offering_queryset=CourseOffering.objects.filter(id=self.offering.id),
        )

        rendered = str(form["period_code"])
        self.assertIn('value="GENED_PRELIM"', rendered)
        self.assertIn("Prelim (GENED_PRELIM)", rendered)

    def test_period_lock_form_explains_lock_scope_and_active_flags(self):
        form = GradingPeriodLockForm(
            tenant_queryset=Tenant.objects.filter(id=self.tenant.id),
            campus_queryset=Campus.objects.filter(id=self.campus.id),
            academic_year_queryset=AcademicYear.objects.filter(id=self.academic_year.id),
            term_queryset=Term.objects.filter(id=self.term.id),
            offering_queryset=CourseOffering.objects.filter(id=self.offering.id),
        )

        self.assertIn("faculty score, activity, and attendance editing is disabled", form.fields["is_locked"].help_text)
        self.assertIn("Campus to apply the same rule", form.fields["scope_type"].help_text)
        self.assertIn("ignored by faculty pages", form.fields["is_active"].help_text)

    def test_period_lock_list_separates_active_and_inactive_rules(self):
        Permission.objects.bulk_create(
            [
                Permission(code="admin_portal.access", module="admin_portal", action="access"),
                Permission(code="grading_periods.read", module="grading_periods", action="read"),
                Permission(code="grading_periods.lock", module="grading_periods", action="lock"),
                Permission(code="grading_periods.reopen", module="grading_periods", action="reopen"),
            ]
        )
        admin_user = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        active_lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.datetime(2026, 4, 24, 0, 0, tzinfo=UTC),
            is_locked=False,
            is_active=True,
        )
        inactive_lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.COURSE,
            course_offering=self.offering,
            deadline_at=timezone.datetime(2026, 4, 24, 0, 0, tzinfo=UTC),
            is_locked=True,
            is_active=False,
        )

        self.client.force_login(admin_user)
        response = self.client.get(reverse("admin_portal:grading_period_lock_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Active Grading Period Lock Rules")
        self.assertContains(response, "Inactive Grading Period Lock Rules")
        self.assertContains(response, "Rule State")
        self.assertContains(response, f"Edit</a>", count=2, html=False)
        self.assertContains(response, str(active_lock.period_code))
        self.assertContains(response, "Ignored")
        self.assertContains(response, f"/period-locks/{inactive_lock.id}/edit/")

    def test_broad_period_reopen_requires_reason_and_confirmation(self):
        Permission.objects.bulk_create(
            [
                Permission(code="admin_portal.access", module="admin_portal", action="access"),
                Permission(code="grading_periods.read", module="grading_periods", action="read"),
                Permission(code="grading_periods.reopen", module="grading_periods", action="reopen"),
            ]
        )
        admin_user = User.objects.create_superuser(
            username="period_admin",
            email="period_admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        lock = GradingPeriodLock.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            academic_year=self.academic_year,
            term=self.term,
            period_code=self.period.code,
            scope_type=GradingPeriodLock.ScopeType.CAMPUS,
            deadline_at=timezone.datetime(2026, 4, 24, 0, 0, tzinfo=UTC),
            is_locked=True,
            is_active=True,
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse("admin_portal:grading_period_lock_reopen", args=[lock.id]),
            {"reopen_reason": "Resume encoding after schedule correction."},
        )

        self.assertEqual(response.status_code, 302)
        lock.refresh_from_db()
        self.assertTrue(lock.is_locked)

        response = self.client.post(
            reverse("admin_portal:grading_period_lock_reopen", args=[lock.id]),
            {
                "reopen_reason": "Resume encoding after schedule correction.",
                "confirmation_phrase": "REOPEN",
            },
        )

        self.assertEqual(response.status_code, 302)
        lock.refresh_from_db()
        self.assertFalse(lock.is_locked)
        log = AuditLog.objects.filter(entity_type="GradingPeriodLock", entity_id=str(lock.id), action="REOPEN").latest(
            "created_at"
        )
        self.assertTrue(log.metadata_json["critical_action"])
        self.assertEqual(log.metadata_json["reason"], "Resume encoding after schedule correction.")
