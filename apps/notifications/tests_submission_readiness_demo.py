from datetime import date, datetime, time
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.academics.models import FacultyAssignment
from apps.admin_portal.services import AdminScopeService
from apps.admin_portal.submission_readiness import GradeSubmissionReadinessService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.scope import ScopeService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import GradingPeriodLock
from apps.notifications.models import SubmissionReadinessNotificationLog
from apps.notifications.submission_readiness import SubmissionReadinessEmailService
from apps.rbac.models import Permission, Role, RolePermission
from apps.students.models import Student
from apps.tenants.models import Campus, SystemSetting, Tenant


@override_settings(DEBUG=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SubmissionReadinessEmailDemoCommandTests(TestCase):
    AS_OF = date(2026, 7, 20)

    def setUp(self):
        self.tenant = Tenant.objects.create(code="RDEMO", name="Readiness Demo Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        faculty_role, _ = Role.objects.get_or_create(code="FACULTY", defaults={"name": "Faculty"})
        area_role, _ = Role.objects.get_or_create(code="AC", defaults={"name": "Area Chair"})
        for code, module, action in (
            ("admin_portal.access", "admin_portal", "access"),
            ("faculty_activity_monitor.read", "faculty_activity_monitor", "read"),
        ):
            permission, _ = Permission.objects.get_or_create(
                code=code, defaults={"module": module, "action": action}
            )
            RolePermission.objects.get_or_create(role=area_role, permission=permission)
        self.unrelated_user = User.objects.create_user(
            username="unrelated-user", email="unrelated@example.invalid", password="safe-pass-123"
        )
        SystemSettingService.set(
            FeatureSettingsService.SUBMISSION_READINESS_EMAIL_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )

    def _seed(self):
        output = StringIO()
        call_command(
            "seed_submission_readiness_email_demo",
            confirm_demo_data=True,
            recipient_email="head@example.invalid",
            as_of_date=self.AS_OF.isoformat(),
            tenant=self.tenant.code,
            campus=self.campus.code,
            stdout=output,
        )
        return output.getvalue()

    def test_seed_builds_expected_scope_readiness_and_dry_run_then_reruns_idempotently(self):
        output = self._seed()
        assignments = list(
            FacultyAssignment.objects.filter(
                offering__course__code__startswith="TEST-READINESS-EMAIL-"
            ).select_related("faculty_user", "offering__course", "offering__section", "offering__academic_year")
        )
        self.assertEqual(len({row.faculty_user_id for row in assignments}), 2)
        self.assertEqual(len(assignments), 6)
        self.assertEqual(
            [Enrollment.objects.filter(course_offering=row.offering, is_active=True).count() for row in assignments],
            [3] * 6,
        )
        readiness = GradeSubmissionReadinessService.calculate(
            assignments,
            selected_period_code="PRELIM",
            now=datetime.combine(self.AS_OF, time(1, 0), tzinfo=ZoneInfo("Asia/Manila")),
        )
        by_letter = {row.assignment.offering.course.code.rsplit("-", 1)[-1]: row for row in readiness}
        self.assertEqual(
            {letter: row.progress_percent for letter, row in by_letter.items()},
            {
                "A": Decimal("16.67"),
                "B": Decimal("33.33"),
                "C": Decimal("50.00"),
                "D": Decimal("16.67"),
                "E": Decimal("33.33"),
                "F": Decimal("100.00"),
            },
        )
        self.assertEqual(by_letter["F"].status, GradeSubmissionReadinessService.SUBMITTED)
        self.assertEqual(
            {letter for letter, row in by_letter.items() if row.progress_percent < Decimal("50")},
            {"A", "B", "D", "E"},
        )
        self.assertIn("Exactly 50% threshold control | 50.00%", output)

        head = User.objects.get(username="test-readiness-area-chair")
        request = SimpleNamespace(
            user=head,
            scope=ScopeService.build_scope(head, tenant_id=self.tenant.id, campus_id=self.campus.id),
        )
        visible = AdminScopeService.scoped_faculty_assignments(request).filter(
            offering__course__code__startswith="TEST-READINESS-EMAIL-"
        )
        self.assertEqual(visible.count(), 6)

        lock = GradingPeriodLock.objects.get(academic_year__code="TEST-READINESS-EMAIL-AY")
        self.assertFalse(lock.is_locked)
        self.assertEqual(lock.deadline_at.astimezone(ZoneInfo("Asia/Manila")).date(), self.AS_OF.replace(day=25))

        result = SubmissionReadinessEmailService.run(
            now=datetime.combine(self.AS_OF, time(1, 0), tzinfo=ZoneInfo("Asia/Manila")),
            as_of_date=self.AS_OF,
            tenant_id=self.tenant.id,
            dry_run=True,
        )
        self.assertEqual(result["eligible"], 4)
        self.assertEqual(result["dry_run"], 1)
        log = SubmissionReadinessNotificationLog.objects.get()
        self.assertEqual(log.recipient, head)
        self.assertEqual(log.assignment_count, 4)
        expected_ids = {by_letter[letter].assignment.id for letter in ("A", "B", "D", "E")}
        self.assertEqual(set(log.metadata_json["assignment_ids"]), expected_ids)
        self.assertEqual(len(mail.outbox), 0)

        sent = SubmissionReadinessEmailService.run(
            now=datetime.combine(self.AS_OF, time(1, 1), tzinfo=ZoneInfo("Asia/Manila")),
            as_of_date=self.AS_OF,
            tenant_id=self.tenant.id,
        )
        self.assertEqual(sent["sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        for letter in ("A", "B", "D", "E"):
            self.assertIn(f"TEST-READINESS-EMAIL-{letter}", mail.outbox[0].body)
        for letter in ("C", "F"):
            self.assertNotIn(f"TEST-READINESS-EMAIL-{letter}", mail.outbox[0].body)

        self._seed()
        self.assertEqual(
            FacultyAssignment.objects.filter(offering__course__code__startswith="TEST-READINESS-EMAIL-").count(),
            6,
        )
        self.assertEqual(Student.objects.filter(student_no__startswith="TEST-READINESS-EMAIL-").count(), 18)
        inspect_output = StringIO()
        call_command(
            "seed_submission_readiness_email_demo",
            confirm_demo_data=True,
            inspect=True,
            tenant=self.tenant.code,
            stdout=inspect_output,
        )
        self.assertIn("6 reused", inspect_output.getvalue())
        self.assertIn("Exactly 50% threshold control | 50.00%", inspect_output.getvalue())

    def test_reset_removes_only_owned_records_and_restores_policy(self):
        self._seed()
        call_command(
            "seed_submission_readiness_email_demo",
            confirm_demo_data=True,
            reset=True,
            tenant=self.tenant.code,
            stdout=StringIO(),
        )
        self.assertFalse(
            FacultyAssignment.objects.filter(offering__course__code__startswith="TEST-READINESS-EMAIL-").exists()
        )
        self.assertFalse(Student.objects.filter(student_no__startswith="TEST-READINESS-EMAIL-").exists())
        self.assertTrue(User.objects.filter(pk=self.unrelated_user.pk).exists())
        self.assertFalse(
            FeatureSettingsService.get_submission_readiness_email_policy(tenant_id=self.tenant.id)["enabled"]
        )
        self.assertFalse(
            SystemSetting.objects.filter(
                tenant=self.tenant, setting_key__startswith="TEST-READINESS-EMAIL-BACKUP-"
            ).exists()
        )
