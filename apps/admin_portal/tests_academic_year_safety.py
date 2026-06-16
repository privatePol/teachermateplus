from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Term
from apps.auditlog.models import AuditLog
from apps.imports.models import ImportBatch
from apps.imports.services import BulkImportService, ImportTemplateService
from apps.rbac.models import Permission
from apps.tenants.models import Campus, Tenant


def _create_admin_permissions(*codes):
    for code in ("admin_portal.access", *codes):
        module, action = code.split(".", 1)
        Permission.objects.get_or_create(
            code=code,
            defaults={"module": module, "action": action},
        )


class AcademicYearIdentifierSafetyTests(TestCase):
    def setUp(self):
        _create_admin_permissions("academic_years.update")
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="AY2526",
            name="Academic Year 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        self.admin = User.objects.create_superuser(
            username="academic_year_admin",
            email="academic_year_admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def _create_term(self):
        return Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="2ND",
            name="Second Semester",
            sequence_no=2,
        )

    def test_identifier_change_is_blocked_after_academic_year_is_used(self):
        self._create_term()
        self.academic_year.code = "2025-2026"

        with self.assertRaises(ValidationError) as exc:
            self.academic_year.save()

        self.assertIn("Code cannot be changed", str(exc.exception))
        self.academic_year.refresh_from_db()
        self.assertEqual(self.academic_year.code, "AY2526")

    def test_non_identifier_fields_remain_editable_after_use(self):
        self._create_term()
        self.academic_year.name = "AY 2025-2026"
        self.academic_year.end_date = date(2026, 6, 15)
        self.academic_year.save()

        self.academic_year.refresh_from_db()
        self.assertEqual(self.academic_year.name, "AY 2025-2026")
        self.assertEqual(self.academic_year.end_date, date(2026, 6, 15))

    def test_edit_page_disables_in_use_tenant_and_code(self):
        self._create_term()

        response = self.client.get(
            reverse("admin_portal:academic_year_update", args=[self.academic_year.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="code"')
        self.assertContains(response, "disabled")
        self.assertContains(response, "Existing CSV files and integrations depend on this code.")

    def test_update_audit_uses_affected_academic_year_tenant(self):
        response = self.client.post(
            reverse("admin_portal:academic_year_update", args=[self.academic_year.id]),
            {
                "tenant": self.tenant.id,
                "code": self.academic_year.code,
                "name": "Updated Academic Year Name",
                "start_date": "2025-06-01",
                "end_date": "2026-05-31",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        audit = AuditLog.objects.filter(
            entity_type="AcademicYear",
            entity_id=str(self.academic_year.id),
            action="UPDATE",
        ).latest("created_at")
        self.assertEqual(audit.tenant_id, self.tenant.id)
        self.assertEqual(audit.before_json["name"], "Academic Year 2025-2026")
        self.assertEqual(audit.after_json["name"], "Updated Academic Year Name")


class AuditLogDetailTests(TestCase):
    def setUp(self):
        _create_admin_permissions("audit_logs.read")
        self.tenant = Tenant.objects.create(code="AUDIT", name="Audit Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.admin = User.objects.create_superuser(
            username="audit_detail_admin",
            email="audit_detail_admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def test_detail_page_shows_before_and_after_changes_and_redacts_secrets(self):
        log = AuditLog.objects.create(
            actor_user=self.admin,
            portal=AuditLog.Portal.ADMIN,
            action="UPDATE",
            entity_type="AcademicYear",
            entity_id="5",
            tenant=self.tenant,
            before_json={"code": "AY2526", "password": "old-secret"},
            after_json={"code": "2025-2026", "password": "new-secret"},
            metadata_json={"reason": "Correct code"},
        )

        response = self.client.get(reverse("admin_portal:audit_log_detail", args=[log.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Changed Fields")
        self.assertContains(response, "AY2526")
        self.assertContains(response, "2025-2026")
        self.assertContains(response, "[REDACTED]")
        self.assertNotContains(response, "old-secret")
        self.assertNotContains(response, "new-secret")
        self.assertContains(response, 'id="audit-before" class="accordion-collapse collapse show"')
        self.assertContains(response, 'id="audit-after" class="accordion-collapse collapse show"')
        self.assertContains(response, 'id="audit-metadata" class="accordion-collapse collapse show"')


class AcademicYearImportGuidanceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="Academic Year 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        self.admin = User.objects.create_superuser(
            username="import_guidance_admin",
            email="import_guidance_admin@example.com",
            password="testpass123",
        )

    def test_missing_academic_year_error_lists_active_codes(self):
        runtime = BulkImportService._build_runtime(self.admin, request=None)
        errors = []

        result = BulkImportService._resolve_academic_year("AY2526", self.tenant, runtime, errors)

        self.assertIsNone(result)
        self.assertEqual(len(errors), 1)
        self.assertIn("Available active codes: 2025-2026", errors[0])

    def test_download_samples_use_current_readable_code_format(self):
        for import_type in (
            ImportBatch.ImportType.COURSE_OFFERINGS,
            ImportBatch.ImportType.FACULTY_ASSIGNMENTS,
            ImportBatch.ImportType.ENROLLMENT,
        ):
            config = ImportTemplateService.get_template_config(import_type)
            column_index = config["headers"].index("academic_year_code")
            self.assertEqual(config["sample_row"][column_index], "2025-2026")
