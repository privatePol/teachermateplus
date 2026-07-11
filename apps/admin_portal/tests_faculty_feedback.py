import csv
from io import StringIO

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.faculty_portal.models import FacultyFeedback
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Tenant


class AdminFacultyFeedbackDashboardTests(TestCase):
    def setUp(self):
        self.url = reverse("admin_portal:faculty_feedback")
        self.admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={"module": "admin_portal", "action": "access"},
        )
        self.read_permission, _ = Permission.objects.get_or_create(
            code="faculty_feedback.read",
            defaults={"module": "faculty_feedback", "action": "read"},
        )
        self.export_permission, _ = Permission.objects.get_or_create(
            code="faculty_feedback.export",
            defaults={"module": "faculty_feedback", "action": "export"},
        )
        self.faculty_access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access",
            defaults={"module": "faculty_portal", "action": "access"},
        )
        self.tenant_admin_role = Role.objects.create(code="TENANT_ADMIN", name="Tenant Admin")
        self.campus_admin_role = Role.objects.create(code="CAMPUS_ADMIN", name="Campus Admin")
        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        for permission in [self.admin_access, self.read_permission, self.export_permission]:
            RolePermission.objects.create(role=self.tenant_admin_role, permission=permission)
        for permission in [self.admin_access, self.read_permission]:
            RolePermission.objects.create(role=self.campus_admin_role, permission=permission)
        RolePermission.objects.create(role=self.faculty_role, permission=self.faculty_access)

        self.tenant_a = Tenant.objects.create(code="TEN-A", name="Tenant A")
        self.campus_a = Campus.objects.create(tenant=self.tenant_a, code="A-MAIN", name="A Main")
        self.campus_a2 = Campus.objects.create(tenant=self.tenant_a, code="A-EXT", name="A Extension")
        self.tenant_b = Tenant.objects.create(code="TEN-B", name="Tenant B")
        self.campus_b = Campus.objects.create(tenant=self.tenant_b, code="B-MAIN", name="B Main")

        self.tenant_admin = self._user("tenant-admin", self.tenant_a, self.campus_a)
        UserRole.objects.create(
            user=self.tenant_admin,
            role=self.tenant_admin_role,
            tenant=self.tenant_a,
            campus=self.campus_a,
        )
        UserRole.objects.create(
            user=self.tenant_admin,
            role=self.tenant_admin_role,
            tenant=self.tenant_a,
            campus=self.campus_a2,
        )
        self.campus_admin = self._user("campus-admin", self.tenant_a, self.campus_a)
        UserRole.objects.create(user=self.campus_admin, role=self.campus_admin_role, tenant=self.tenant_a, campus=self.campus_a)
        self.superadmin = self._user("superadmin-feedback", self.tenant_a, self.campus_a)
        self.superadmin.is_staff = True
        self.superadmin.is_superuser = True
        self.superadmin.save(update_fields=["is_staff", "is_superuser"])
        self.faculty = self._user("faculty-admin-feedback", self.tenant_a, self.campus_a)
        UserRole.objects.create(user=self.faculty, role=self.faculty_role, tenant=self.tenant_a, campus=self.campus_a)

        self.feedback_a = self._feedback(
            faculty_user=self.faculty,
            tenant=self.tenant_a,
            campus=self.campus_a,
            rating=FacultyFeedback.Rating.HAPPY,
            suggestion="=formula attempt",
            page_path="/faculty/dashboard/",
            route_name="faculty_portal:dashboard",
            feature_code="DASHBOARD",
        )
        self.feedback_a2 = self._feedback(
            faculty_user=self.faculty,
            tenant=self.tenant_a,
            campus=self.campus_a2,
            rating=FacultyFeedback.Rating.SAD,
            suggestion="Needs clearer label",
            page_path="/faculty/my-courses/",
            route_name="faculty_portal:my_courses",
            feature_code="MY_COURSES",
        )
        self.feedback_b = self._feedback(
            faculty_user=self._user("other-faculty", self.tenant_b, self.campus_b),
            tenant=self.tenant_b,
            campus=self.campus_b,
            rating=FacultyFeedback.Rating.NEUTRAL,
            suggestion="Other tenant",
            page_path="/faculty/dashboard/",
            route_name="faculty_portal:dashboard",
            feature_code="DASHBOARD",
        )

    def _user(self, username, tenant, campus):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            default_tenant=tenant,
            default_campus=campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def _feedback(self, **kwargs):
        return FacultyFeedback.objects.create(**kwargs)

    def test_tenant_admin_dashboard_is_scoped_to_own_tenant_with_summary(self):
        self.client.force_login(self.tenant_admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        rows = list(response.context["feedback_rows"])
        self.assertIn(self.feedback_a, rows)
        self.assertIn(self.feedback_a2, rows)
        self.assertNotIn(self.feedback_b, rows)
        self.assertEqual(response.context["summary"]["total"], 2)
        self.assertEqual(response.context["summary"]["happy"], 1)
        self.assertEqual(response.context["summary"]["sad"], 1)
        self.assertContains(response, "Faculty Feedback")
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_FEEDBACK_DASHBOARD_ACCESSED").exists())

    def test_superadmin_can_view_and_export_feedback_across_tenants(self):
        self.client.force_login(self.superadmin)

        dashboard = self.client.get(self.url)
        export_response = self.client.get(self.url, {"export": "csv"})

        self.assertEqual(dashboard.status_code, 200)
        rows = list(dashboard.context["feedback_rows"])
        self.assertIn(self.feedback_a, rows)
        self.assertIn(self.feedback_a2, rows)
        self.assertIn(self.feedback_b, rows)
        exported = export_response.content.decode()
        self.assertEqual(export_response.status_code, 200)
        self.assertIn("TEN-A", exported)
        self.assertIn("TEN-B", exported)

    def test_campus_admin_dashboard_is_scoped_to_own_campus(self):
        self.client.force_login(self.campus_admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        rows = list(response.context["feedback_rows"])
        self.assertIn(self.feedback_a, rows)
        self.assertNotIn(self.feedback_a2, rows)
        self.assertNotIn(self.feedback_b, rows)

    def test_unauthorized_role_and_direct_export_are_denied(self):
        self.client.force_login(self.faculty)

        dashboard = self.client.get(self.url)
        export_response = self.client.get(self.url, {"export": "csv"})

        self.assertEqual(dashboard.status_code, 403)
        self.assertEqual(export_response.status_code, 403)

    def test_campus_admin_without_export_permission_cannot_export(self):
        self.client.force_login(self.campus_admin)

        response = self.client.get(self.url, {"export": "csv"})

        self.assertEqual(response.status_code, 403)

    def test_filters_apply_rating_suggestion_date_feature_and_scope(self):
        self.client.force_login(self.tenant_admin)

        response = self.client.get(
            self.url,
            {
                "rating": FacultyFeedback.Rating.HAPPY,
                "has_suggestion": "yes",
                "date_from": timezone.localdate().isoformat(),
                "date_to": timezone.localdate().isoformat(),
                "feature_code": "DASHBOARD",
            },
        )

        self.assertEqual(response.status_code, 200)
        rows = list(response.context["feedback_rows"])
        self.assertEqual(rows, [self.feedback_a])
        self.assertEqual(response.context["summary"]["total"], 1)

    def test_xss_like_suggestion_is_escaped_in_dashboard(self):
        self.feedback_a.suggestion = "<script>alert(1)</script>"
        self.feedback_a.save(update_fields=["suggestion"])
        self.client.force_login(self.tenant_admin)

        response = self.client.get(self.url, {"rating": FacultyFeedback.Rating.HAPPY})
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)

    def test_crafted_tenant_and_campus_filters_cannot_cross_scope(self):
        self.client.force_login(self.tenant_admin)

        response = self.client.get(self.url, {"tenant_id": self.tenant_b.id, "campus_id": self.campus_b.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["feedback_rows"]), [])

    def test_authorized_csv_export_uses_filters_scope_headers_and_formula_protection(self):
        self.client.force_login(self.tenant_admin)

        response = self.client.get(self.url, {"export": "csv", "rating": FacultyFeedback.Rating.HAPPY})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        rows = list(csv.DictReader(StringIO(response.content.decode())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tenant_code"], "TEN-A")
        self.assertEqual(rows[0]["suggestion"], "'=formula attempt")
        self.assertNotIn("TEN-B", response.content.decode())
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_FEEDBACK_CSV_EXPORTED").exists())

    def test_audit_events_exclude_full_suggestion_text(self):
        secret_suggestion = "private suggestion should not enter audit metadata"
        self.feedback_a.suggestion = secret_suggestion
        self.feedback_a.save(update_fields=["suggestion"])
        self.client.force_login(self.tenant_admin)

        dashboard = self.client.get(self.url, {"rating": FacultyFeedback.Rating.HAPPY})
        export_response = self.client.get(self.url, {"export": "csv", "rating": FacultyFeedback.Rating.HAPPY})

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(export_response.status_code, 200)
        dashboard_audit = AuditLog.objects.filter(action="FACULTY_FEEDBACK_DASHBOARD_ACCESSED").latest("created_at")
        export_audit = AuditLog.objects.filter(action="FACULTY_FEEDBACK_CSV_EXPORTED").latest("created_at")
        self.assertNotIn(secret_suggestion, str(dashboard_audit.metadata_json))
        self.assertNotIn(secret_suggestion, str(export_audit.metadata_json))

    def test_csv_formula_injection_protection_for_all_leading_formula_characters(self):
        FacultyFeedback.objects.filter(tenant=self.tenant_a).delete()
        for char in ["=", "+", "-", "@"]:
            self._feedback(
                faculty_user=self.faculty,
                tenant=self.tenant_a,
                campus=self.campus_a,
                rating=FacultyFeedback.Rating.HAPPY,
                suggestion=f"{char}formula",
                page_path="/faculty/dashboard/",
                route_name="faculty_portal:dashboard",
                feature_code="FORMULA_TEST",
            )
        self.client.force_login(self.tenant_admin)

        response = self.client.get(self.url, {"export": "csv", "feature_code": "FORMULA_TEST"})

        self.assertEqual(response.status_code, 200)
        rows = list(csv.DictReader(StringIO(response.content.decode())))
        self.assertEqual(
            sorted(row["suggestion"] for row in rows),
            sorted(["'=formula", "'+formula", "'-formula", "'@formula"]),
        )

    def test_csv_export_applies_same_filters_as_dashboard(self):
        FacultyFeedback.objects.create(
            faculty_user=self.faculty,
            tenant=self.tenant_a,
            campus=self.campus_a,
            rating=FacultyFeedback.Rating.HAPPY,
            suggestion="",
            page_path="/faculty/dashboard/",
            route_name="faculty_portal:dashboard",
            feature_code="DASHBOARD",
        )
        self.client.force_login(self.tenant_admin)
        filters = {
            "tenant_id": str(self.tenant_a.id),
            "campus_id": str(self.campus_a.id),
            "rating": FacultyFeedback.Rating.HAPPY,
            "date_from": timezone.localdate().isoformat(),
            "date_to": timezone.localdate().isoformat(),
            "feature_code": "DASHBOARD",
            "has_suggestion": "yes",
        }

        dashboard = self.client.get(self.url, filters)
        export_response = self.client.get(self.url, {**filters, "export": "csv"})

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(export_response.status_code, 200)
        dashboard_ids = [row.id for row in dashboard.context["feedback_rows"]]
        export_rows = list(csv.DictReader(StringIO(export_response.content.decode())))
        self.assertEqual(dashboard_ids, [self.feedback_a.id])
        self.assertEqual(len(export_rows), 1)
        self.assertEqual(export_rows[0]["faculty_user_id"], str(self.feedback_a.faculty_user_id))
        self.assertEqual(export_rows[0]["tenant_code"], self.tenant_a.code)
        self.assertEqual(export_rows[0]["campus_code"], self.campus_a.code)
        self.assertEqual(export_rows[0]["rating"], FacultyFeedback.Rating.HAPPY)
        self.assertEqual(export_rows[0]["feature_code"], "DASHBOARD")
