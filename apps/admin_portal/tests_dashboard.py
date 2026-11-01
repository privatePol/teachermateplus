from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import TestCase, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, ActivePortalSession
from apps.core.services.scope import ScopeService
from apps.rbac.models import Permission, Role, RolePermission, UserRole, UserPermission
from apps.tenants.models import Tenant, Campus, Department
from .dashboard import logged_in_users, scope_token


class AdminDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="DASH", name="Dashboard")
        cls.campus = Campus.objects.create(tenant=cls.tenant, code="MAIN", name="Main")
        cls.other_campus = Campus.objects.create(tenant=cls.tenant, code="OTHER", name="Other")
        cls.department = Department.objects.create(tenant=cls.tenant, campus=cls.campus, code="D", name="Department")
        cls.other_department = Department.objects.create(tenant=cls.tenant, campus=cls.campus, code="X", name="Other department")
        cls.other_tenant = Tenant.objects.create(code="OUT", name="Outside")
        cls.role = Role.objects.create(code="DASH", name="Dashboard administrator")
        for code in ["admin_portal.access", "dashboard.read", "users.read", "audit_logs.read", "roles.read", "import_batches.read", "reopen_requests.read", "reopen_requests.review"]:
            module, action = code.split(".")
            permission, _ = Permission.objects.get_or_create(code=code, defaults={"module": module, "action": action})
            RolePermission.objects.create(role=cls.role, permission=permission)
        cls.admin = cls.make_user("dash-admin")
        UserRole.objects.create(user=cls.admin, role=cls.role, tenant=cls.tenant, campus=cls.campus, department=cls.department)

    @classmethod
    def make_user(cls, name, **kwargs):
        values = dict(default_tenant=cls.tenant, default_campus=cls.campus, default_department=cls.department,
                      must_change_password=False, privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"), privacy_consent_at=timezone.now())
        values.update(kwargs)
        return User.objects.create_user(username=name, email=name+"@example.test", password="testpass123", **values)

    def setUp(self):
        self.client.force_login(self.admin)

    def request(self):
        request = RequestFactory().get("/")
        request.user = self.admin
        request.scope = ScopeService.build_scope(self.admin)
        return request

    def register(self, user, *, expiry=3600):
        store = SessionStore()
        store["_auth_user_id"] = str(user.id)
        store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
        store["_auth_user_hash"] = user.get_session_auth_hash()
        store.set_expiry(expiry)
        store.save()
        ActivePortalSession.objects.create(user=user, session_key=store.session_key)
        return store.session_key

    def deny(self, code):
        UserPermission.objects.create(user=self.admin, permission=Permission.objects.get(code=code),
                                      tenant=self.tenant, campus=self.campus, grant_type="DENY")

    def test_render_does_not_load_removed_details_or_aggregates(self):
        with patch("apps.admin_portal.views._active_user_activity_rows", side_effect=AssertionError("details")), patch("apps.admin_portal.views._governance_alert_rows", side_effect=AssertionError("governance")), patch("apps.admin_portal.services.AdminScopeService.scoped_import_batches", side_effect=AssertionError("imports")), patch.object(Session, "get_decoded", side_effect=AssertionError("decode")):
            response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        for text in ["Stage 0.75", "Import Batches", "Pending Confirmations", "Failed Batches", "Imported Rows", "window.location.reload", "30000"]:
            self.assertNotContains(response, text)
        self.assertContains(response, "View Governance Alerts")
        self.assertContains(response, "View Logged-in Users")
        self.assertContains(response, "Pending Review")
        self.assertNotIn("audit_logs", response.context["stats"])

    def test_count_distinct_valid_expiry_and_listing_parity_above_caps(self):
        users = [self.make_user(f"active-{index:03}") for index in range(505)]
        sessions = []
        now = timezone.now()
        # Bulk synthetic registry rows: avoid per-session saves in the growth regression.
        for index, user in enumerate(users):
            key = f"live{index:036}"
            sessions.append(Session(session_key=key, session_data=SessionStore().encode({"_auth_user_id":str(user.pk),"_auth_user_backend":"django.contrib.auth.backends.ModelBackend","_auth_user_hash":user.get_session_auth_hash()}), expire_date=now + timedelta(hours=1)))
        Session.objects.bulk_create(sessions)
        ActivePortalSession.objects.bulk_create([ActivePortalSession(user=user, session_key=session.session_key) for user, session in zip(users,sessions)])
        self.register(users[0])
        expired = self.make_user("expired")
        self.register(expired, expiry=-60)
        inactive = self.make_user("inactive", is_active=False)
        self.register(inactive)
        ActivePortalSession.objects.create(user=self.make_user("missing"), session_key="m"*40)
        self.register(self.make_user("other-campus", default_campus=self.other_campus))
        self.register(self.make_user("other-department", default_department=self.other_department))
        self.register(self.make_user("other-tenant", default_tenant=self.other_tenant, default_campus=None))
        # The valid force_login viewer is now reconciled before dashboard rendering.
        self.client.get(reverse("admin_portal:dashboard"))
        request = self.request()
        with CaptureQueriesContext(connection) as queries:
            count = logged_in_users(request).count()
        self.assertEqual(count, 506)
        self.assertLess(len(queries), 8)
        response = self.client.get(reverse("admin_portal:dashboard_logged_in_users"))
        self.assertEqual(response.context["page_obj"].paginator.count, count)
        self.assertEqual(len(response.context["page_obj"]), 50)
        self.assertTrue(all(u.session_expires_at > now for u in response.context["page_obj"]))
        dashboard = self.client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(dashboard.context["active_user_count"], count)

    def test_idle_deadline_uses_session_expiry_without_audit_proxy(self):
        user = self.make_user("idle")
        key = self.register(user)
        self.assertIn(user, logged_in_users(self.request()))
        Session.objects.filter(session_key=key).update(expire_date=timezone.now()-timedelta(seconds=1))
        self.assertNotIn(user, logged_in_users(self.request()))

    def test_users_direct_deny_removes_summary_and_blocks_listing(self):
        self.deny("users.read")
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertNotContains(response, "View Logged-in Users")
        self.assertNotContains(response, "Manage Users")
        self.assertEqual(self.client.get(reverse("admin_portal:dashboard_logged_in_users")).status_code, 403)

    def test_governance_direct_deny_hides_links(self):
        self.deny("audit_logs.read")
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertNotContains(response, "View Governance Alerts")
        self.assertNotContains(response, "View Audit Logs")

    def test_fragment_requires_auth_and_dashboard_permission(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("admin_portal:dashboard_reopen_card")).status_code, 302)
        self.client.force_login(self.admin)
        self.deny("dashboard.read")
        self.assertEqual(self.client.get(reverse("admin_portal:dashboard_reopen_card")).status_code, 403)

    def test_fragment_is_card_only_and_no_cache(self):
        response = self.client.get(reverse("admin_portal:dashboard"))
        token = response.context["dashboard_scope_token"]
        fragment = self.client.get(reverse("admin_portal:dashboard_reopen_card"), {"scope_token": token})
        self.assertEqual(fragment.status_code, 200)
        self.assertContains(fragment, "Pending Review")
        self.assertNotContains(fragment, "<html")
        self.assertNotContains(fragment, "Admin Portal")
        self.assertIn("no-store", fragment["Cache-Control"])

    def test_fragment_rechecks_revoked_reopen_permissions(self):
        token = scope_token(self.request())
        self.deny("reopen_requests.read")
        self.deny("reopen_requests.review")
        response = self.client.get(reverse("admin_portal:dashboard_reopen_card"), {"scope_token": token})
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "Pending Review", status_code=403)

    def test_fragment_rejects_tampered_or_changed_scope(self):
        url = reverse("admin_portal:dashboard_reopen_card")
        self.assertEqual(self.client.get(url, {"scope_token":"bad"}).status_code,409)
        request = self.request()
        request.scope["department_ids"] = []
        self.assertEqual(self.client.get(url,{"scope_token":scope_token(request)}).status_code,409)

    def test_background_refresh_preserves_idle_deadline_even_on_permission_revocation(self):
        response = self.client.get(reverse("admin_portal:dashboard"))
        token = response.context["dashboard_scope_token"]
        key = self.client.session.session_key
        deadline = timezone.now() + timedelta(seconds=90)
        Session.objects.filter(session_key=key).update(expire_date=deadline)
        fragment = self.client.get(reverse("admin_portal:dashboard_reopen_card"), {"scope_token":token})
        self.assertEqual(fragment.status_code,200)
        self.assertEqual(Session.objects.get(session_key=key).expire_date,deadline)
        self.deny("reopen_requests.read")
        self.deny("reopen_requests.review")
        self.assertEqual(self.client.get(reverse("admin_portal:dashboard_reopen_card"),{"scope_token":token}).status_code,403)
        self.assertEqual(Session.objects.get(session_key=key).expire_date,deadline)
        # An interactive visit still renews the configured sliding idle deadline.
        self.client.get(reverse("admin_portal:dashboard"))
        self.assertGreater(Session.objects.get(session_key=key).expire_date,deadline)


    def test_monitoring_endpoints_are_get_only(self):
        for name in ("dashboard_reopen_card", "dashboard_logged_in_users"):
            self.assertEqual(self.client.post(reverse("admin_portal:"+name)).status_code,405)

    def test_render_authorized_and_denied_navigation_fixtures(self):
        import os
        from pathlib import Path
        from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
        group = MenuGroup.objects.create(portal="ADMIN", code="DASHBOARD_TEST", label="Administration")
        for code, label, route, permission in [
            ("DASH", "Dashboard", "dashboard", "dashboard.read"),
            ("USERS", "Users", "user_list", "users.read"),
            ("AUDIT", "Audit Logs", "audit_log_list", "audit_logs.read"),
        ]:
            item = MenuItem.objects.create(portal="ADMIN", menu_group=group, code=code, label=label, route_name="admin_portal:"+route)
            MenuItemPermission.objects.create(menu_item=item, permission=Permission.objects.get(code=permission))
        for variant in ("authorized", "denied"):
            if variant == "denied":
                self.deny("users.read")
                self.deny("audit_logs.read")
            response = self.client.get(reverse("admin_portal:dashboard"))
            self.assertContains(response, 'data-menu-code="DASH"')
            if variant == "denied":
                self.assertNotContains(response, 'data-menu-code="USERS"')
                self.assertNotContains(response, 'data-menu-code="AUDIT"')
            else:
                self.assertContains(response, 'data-menu-code="USERS"')
                self.assertContains(response, 'data-menu-code="AUDIT"')
            if os.environ.get("TMP_ADMIN_EVIDENCE"):
                Path(os.environ["TMP_ADMIN_EVIDENCE"], variant+".html").write_text(response.content.decode(),encoding="utf-8")


    def test_password_change_invalidates_monitor_registry_without_deleting_session(self):
        user = self.make_user("changed-password")
        key = self.register(user)
        user.set_password("NewPassword123!")
        user.save(update_fields=["password"])
        self.assertTrue(Session.objects.filter(session_key=key).exists())
        self.assertFalse(ActivePortalSession.objects.filter(session_key=key).exists())
        self.assertNotIn(user, logged_in_users(self.request()))

    def test_password_monitor_invalidation_rolls_back_with_password(self):
        from django.db import transaction
        user = self.make_user("rollback-password")
        key = self.register(user)
        previous_password = user.password
        try:
            with transaction.atomic():
                user.set_password("RollbackPassword123!")
                user.save(update_fields=["password"])
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        user.refresh_from_db()
        self.assertEqual(user.password, previous_password)
        self.assertTrue(ActivePortalSession.objects.filter(session_key=key).exists())


    def test_stale_password_registration_is_not_advertised_as_live(self):
        from apps.accounts.services import ActivePortalSessionService
        user = self.make_user("stale-registration")
        current = User.objects.get(pk=user.pk)
        current.set_password("ChangedBeforeRegistration123!")
        current.save(update_fields=["password"])
        request = RequestFactory().get("/")
        request.session = SessionStore()
        request.session["_auth_user_id"] = str(user.pk)
        request.session["_auth_user_hash"] = user.get_session_auth_hash()
        ActivePortalSessionService.register(request=request,user=user,enforce_single_session=False)
        self.assertFalse(ActivePortalSession.objects.filter(user=user).exists())

    def test_django_admin_password_change_rotates_live_registry_without_logout(self):
        self.admin.is_superuser = True
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin:password_change"), {
            "old_password":"testpass123", "new_password1":"NewRegistryPassword123!", "new_password2":"NewRegistryPassword123!",
        },follow=True)
        self.assertEqual(response.status_code,200)
        key = self.client.session.session_key
        self.assertTrue(ActivePortalSession.objects.filter(user=self.admin,session_key=key).exists())
        self.assertEqual(str(self.admin.pk), self.client.session["_auth_user_id"])


    def test_unchanged_password_does_not_drop_monitor_entry(self):
        user = self.make_user("same-password")
        key = self.register(user)
        user.first_name = "Renamed"
        user.save()
        self.assertTrue(ActivePortalSession.objects.filter(session_key=key).exists())


from . import tests_reopen_requests as reopen_fixtures
from apps.grading.models import GradeSubmissionReopenRequest


class DashboardReopenTests(TestCase):
    setUp = reopen_fixtures.GradeSubmissionReopenRequestReviewTests.setUp

    def pending(self):
        return GradeSubmissionReopenRequest.objects.create(
            tenant=self.tenant, campus=self.campus, submission=self.submission,
            offering=self.offering, template_period=self.period,
            requested_by_user=self.faculty_user, justification="Check dashboard eligibility",
        )

    def test_initial_card_and_fragment_have_same_scoped_pending_request(self):
        row = self.pending()
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.context["reopen_request_monitor"]["pending_count"],1)
        review_url = reverse("admin_portal:grade_submission_reopen_request_review", args=[row.pk])
        self.assertContains(response, review_url)
        fragment = self.client.get(reverse("admin_portal:dashboard_reopen_card"), {"scope_token":response.context["dashboard_scope_token"]})
        self.assertContains(fragment,review_url)

    def test_superuser_permission_shortcut_does_not_create_reviewer_assignment(self):
        row = self.pending()
        self.admin_user.is_superuser = True
        self.admin_user.save(update_fields=["is_superuser"])
        RolePermission.objects.filter(permission__code="reopen_requests.review").delete()
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertNotContains(response, reverse("admin_portal:grade_submission_reopen_request_review",args=[row.pk]))

    def test_request_outside_faculty_supervision_scope_is_excluded(self):
        self.pending()
        other = Department.objects.create(tenant=self.tenant,campus=self.campus,code="OUT",name="Outside")
        UserRole.objects.filter(user=self.faculty_user).update(department=other)
        self.faculty_user.default_department = other
        self.faculty_user.save(update_fields=["default_department"])
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:dashboard"))
        self.assertEqual(response.context["reopen_request_monitor"]["pending_count"],0)

    def test_fragment_detects_active_academic_term_changed_in_another_tab(self):
        from apps.academics.services import AcademicGovernanceService
        from apps.core.services.settings import SystemSettingService
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin_portal:dashboard"))
        token = response.context["dashboard_scope_token"]
        SystemSettingService.set(AcademicGovernanceService.ACTIVE_AY_KEY,self.academic_year.code,tenant_id=self.tenant.pk,value_type="STRING")
        SystemSettingService.set(AcademicGovernanceService.ACTIVE_TERM_KEY,self.term.code,tenant_id=self.tenant.pk,value_type="STRING")
        fragment = self.client.get(reverse("admin_portal:dashboard_reopen_card"),{"scope_token":token})
        self.assertEqual(fragment.status_code,409)
