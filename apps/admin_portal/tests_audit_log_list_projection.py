from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Tenant


class AuditLogListProjectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="AUDIT", name="Audit")
        cls.campus = Campus.objects.create(tenant=cls.tenant, code="MAIN", name="Main")
        cls.other_campus = Campus.objects.create(tenant=cls.tenant, code="OTHER", name="Other")
        cls.other_tenant = Tenant.objects.create(code="OUT", name="Outside")
        cls.user = User.objects.create_user(
            username="audit-reader", email="audit-reader@example.test", password="testpass123", default_tenant=cls.tenant,
            default_campus=cls.campus, must_change_password=False,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code="AUDIT_READER", name="Audit reader")
        for code in ("admin_portal.access", "audit_logs.read"):
            module, action = code.split(".")
            permission, _ = Permission.objects.get_or_create(code=code, defaults={"module": module, "action": action})
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(user=cls.user, role=role, tenant=cls.tenant, campus=cls.campus)

    def setUp(self):
        self.client.force_login(self.user)

    def log(self, **kwargs):
        values = dict(actor_user=self.user, portal="ADMIN", action="UPDATE", entity_type="Grade",
                      entity_id="visible", tenant=self.tenant, campus=self.campus,
                      before_json={"nested": [{"password": "before-secret", "safe": "before-visible"}]},
                      after_json={"nested": [{"token": "after-secret", "safe": "after-visible"}]},
                      metadata_json={"nested": {"api_key": "meta-secret", "safe": "meta-visible"}})
        values.update(kwargs)
        return AuditLog.objects.create(**values)

    def audit_selects(self, queries):
        quote = connection.ops.quote_name
        return [q["sql"] for q in queries if q["sql"].lstrip().upper().startswith("SELECT")
                and f"FROM {quote('audit_logs')}" in q["sql"] and "COUNT(" not in q["sql"].upper()]

    def test_list_projection_render_pagination_and_scope_without_deferred_fetches(self):
        logs = [self.log(entity_id=f"item-{i:02}") for i in range(31)]
        self.log(entity_id="other-campus", campus=self.other_campus)
        self.log(entity_id="other-tenant", tenant=self.other_tenant, campus=None)
        global_log = self.log(entity_id="global-record", tenant=None, campus=None)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("admin_portal:audit_log_list"))
        self.assertEqual(response.status_code, 200)
        page = response.context["page_obj"]
        self.assertEqual(page.paginator.per_page, 30)
        self.assertEqual(page.paginator.count, 32)
        self.assertEqual(len(page.object_list), 30)
        self.assertEqual(page.object_list[0].pk, global_log.pk)
        self.assertContains(response, "global-record")
        self.assertNotContains(response, "other-campus")
        self.assertNotContains(response, "other-tenant")
        selects = self.audit_selects(queries)
        self.assertEqual(len(selects), 1, selects)
        quote = connection.ops.quote_name
        for field in ("before_json", "after_json", "metadata_json"):
            self.assertNotIn(f"{quote('audit_logs')}.{quote(field)}", selects[0])
        self.assertEqual(page.object_list[0].get_deferred_fields(), {"before_json", "after_json", "metadata_json"})
        page2 = self.client.get(reverse("admin_portal:audit_log_list"), {"page": 2})
        self.assertEqual([row.pk for row in page2.context["page_obj"]], [logs[1].pk, logs[0].pk])

    def test_list_filters_portal_action_and_entity_actor_or_id(self):
        match = self.log(entity_id="needle-42", action="GRADE_UPDATE")
        self.log(entity_id="needle-42", action="DELETE")
        self.log(entity_id="needle-42", action="GRADE_UPDATE", portal="FACULTY")
        for q in ("needle", "Grade", "audit-reader"):
            response = self.client.get(reverse("admin_portal:audit_log_list"),
                                       {"portal": "ADMIN", "action": "grade_up", "q": q})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([row.pk for row in response.context["page_obj"]], [match.pk])
            self.assertContains(response, "GRADE_UPDATE")

    def test_detail_selects_payloads_and_redacts_nested_values(self):
        log = self.log()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("admin_portal:audit_log_detail", args=[log.pk]))
        self.assertEqual(response.status_code, 200)
        selects = self.audit_selects(queries)
        self.assertEqual(len(selects), 1, selects)
        quote = connection.ops.quote_name
        for field in ("before_json", "after_json", "metadata_json"):
            self.assertIn(f"{quote('audit_logs')}.{quote(field)}", selects[0])
        for visible in ("before-visible", "after-visible", "meta-visible", "[REDACTED]"):
            self.assertContains(response, visible)
        for secret in ("before-secret", "after-secret", "meta-secret"):
            self.assertNotContains(response, secret)
        self.assertFalse(response.context["log"].get_deferred_fields())
        out = self.log(tenant=self.other_tenant, campus=None)
        self.assertEqual(self.client.get(reverse("admin_portal:audit_log_detail", args=[out.pk])).status_code, 404)

    def test_direct_denies_override_role_grants_for_list_and_detail(self):
        log = self.log()
        for code in ("admin_portal.access", "audit_logs.read"):
            with self.subTest(code=code):
                deny = UserPermission.objects.create(user=self.user, permission=Permission.objects.get(code=code),
                                                     tenant=self.tenant, campus=self.campus, grant_type="DENY")
                self.assertEqual(self.client.get(reverse("admin_portal:audit_log_list")).status_code, 403)
                self.assertEqual(self.client.get(reverse("admin_portal:audit_log_detail", args=[log.pk])).status_code, 403)
                deny.delete()
