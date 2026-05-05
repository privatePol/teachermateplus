from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Tenant


class InactiveMaintenanceHardDeleteTests(TestCase):
    def setUp(self):
        Permission.objects.bulk_create(
            [
                Permission(code="admin_portal.access", module="admin_portal", action="access"),
                Permission(code="tenants.read", module="tenants", action="read"),
                Permission(code="tenants.update", module="tenants", action="update"),
                Permission(code="campuses.read", module="campuses", action="read"),
                Permission(code="inactive_records.delete", module="inactive_records", action="delete"),
            ]
        )
        self.admin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def test_inactive_unused_record_can_be_permanently_deleted(self):
        tenant = Tenant.objects.create(code="OLD", name="Old Tenant", is_active=False)

        response = self.client.post(
            reverse("admin_portal:inactive_record_delete", args=["tenant", tenant.id]),
            {"confirmation_code": "OLD"},
        )

        self.assertRedirects(response, reverse("admin_portal:tenant_list"))
        self.assertFalse(Tenant.objects.filter(id=tenant.id).exists())

    def test_superuser_can_delete_without_module_update_permission(self):
        Permission.objects.filter(code="tenants.update").delete()
        tenant = Tenant.objects.create(code="SUPER", name="Super Tenant", is_active=False)

        response = self.client.post(
            reverse("admin_portal:inactive_record_delete", args=["tenant", tenant.id]),
            {"confirmation_code": "SUPER"},
        )

        self.assertRedirects(response, reverse("admin_portal:tenant_list"))
        self.assertFalse(Tenant.objects.filter(id=tenant.id).exists())

    def test_non_superuser_without_inactive_delete_permission_is_denied(self):
        active_tenant = Tenant.objects.create(code="ACTIVE", name="Active Tenant")
        user = User.objects.create_user(
            username="tenant_reader",
            email="tenant_reader@example.com",
            password="testpass123",
            default_tenant=active_tenant,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code="TENANT_READER", name="Tenant Reader")
        for permission in Permission.objects.filter(code__in=["admin_portal.access", "tenants.read"]):
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(user=user, role=role, tenant=active_tenant)
        inactive_tenant = Tenant.objects.create(code="NOPE", name="No Delete", is_active=False)

        self.client.force_login(user)
        response = self.client.post(
            reverse("admin_portal:inactive_record_delete", args=["tenant", inactive_tenant.id]),
            {"confirmation_code": "NOPE"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Tenant.objects.filter(id=inactive_tenant.id).exists())

    def test_non_superuser_with_inactive_delete_permission_can_delete(self):
        active_tenant = Tenant.objects.create(code="ACTIVE", name="Active Tenant")
        inactive_campus = Campus.objects.create(
            tenant=active_tenant,
            code="OKDEL",
            name="Can Delete",
            is_active=False,
        )
        user = User.objects.create_user(
            username="tenant_deleter",
            email="tenant_deleter@example.com",
            password="testpass123",
            default_tenant=active_tenant,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        role = Role.objects.create(code="TENANT_DELETER", name="Tenant Deleter")
        for permission in Permission.objects.filter(
            code__in=["admin_portal.access", "campuses.read", "inactive_records.delete"]
        ):
            RolePermission.objects.create(role=role, permission=permission)
        UserRole.objects.create(user=user, role=role, tenant=active_tenant)

        self.client.force_login(user)
        response = self.client.post(
            reverse("admin_portal:inactive_record_delete", args=["campus", inactive_campus.id]),
            {"confirmation_code": "OKDEL"},
        )

        self.assertRedirects(response, reverse("admin_portal:campus_list"))
        self.assertFalse(Campus.objects.filter(id=inactive_campus.id).exists())

    def test_inactive_record_with_related_rows_is_blocked(self):
        tenant = Tenant.objects.create(code="USED", name="Used Tenant", is_active=False)
        Campus.objects.create(tenant=tenant, code="USED-CAMPUS", name="Used Campus")

        response = self.client.post(
            reverse("admin_portal:inactive_record_delete", args=["tenant", tenant.id]),
            {"confirmation_code": "USED"},
            follow=True,
        )

        self.assertContains(response, "already assigned")
        self.assertTrue(Tenant.objects.filter(id=tenant.id).exists())

    def test_inactive_section_shows_usage_column_and_dependency_count(self):
        tenant = Tenant.objects.create(code="LIST", name="List Tenant", is_active=False)
        Campus.objects.create(tenant=tenant, code="LIST-CAMPUS", name="List Campus")

        response = self.client.get(reverse("admin_portal:tenant_list"))

        self.assertContains(response, "Used In")
        self.assertContains(response, "Campus")
        self.assertContains(response, "In use")
