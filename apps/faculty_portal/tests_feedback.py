from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from pathlib import Path

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.faculty_portal.models import FacultyFeedback
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Tenant


class FacultyFeedbackSubmissionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="FB-TEN", name="Feedback Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        self.other_campus = Campus.objects.create(tenant=self.other_tenant, code="OTHER-MAIN", name="Other Campus")
        self.faculty_access = Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
        )
        self.admin_access = Permission.objects.create(
            code="admin_portal.access",
            module="admin_portal",
            action="access",
        )
        self.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        self.admin_role = Role.objects.create(code="TENANT_ADMIN", name="Tenant Admin")
        RolePermission.objects.create(role=self.faculty_role, permission=self.faculty_access)
        RolePermission.objects.create(role=self.admin_role, permission=self.admin_access)
        self.faculty = self._user("faculty-feedback", self.tenant, self.campus)
        UserRole.objects.create(user=self.faculty, role=self.faculty_role, tenant=self.tenant, campus=self.campus)
        self.admin = self._user("admin-feedback", self.tenant, self.campus)
        UserRole.objects.create(user=self.admin, role=self.admin_role, tenant=self.tenant, campus=self.campus)
        self.other_faculty = self._user("other-faculty-feedback", self.tenant, self.campus)
        UserRole.objects.create(
            user=self.other_faculty,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.url = reverse("faculty_portal:feedback_submit")

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

    def _post(self, **overrides):
        payload = {
            "rating": FacultyFeedback.Rating.HAPPY,
            "suggestion": "",
            "page_path": "/faculty/dashboard/?token=secret",
            "route_name": "faculty_portal:dashboard",
            "referrer_path": "https://example.com/unsafe",
            "faculty_user": "9999",
            "tenant": self.other_tenant.id,
            "campus": self.other_campus.id,
        }
        payload.update(overrides)
        return self.client.post(self.url, payload, HTTP_USER_AGENT="Feedback Test Browser 1.0")

    def test_authenticated_faculty_can_submit_happy_feedback_with_safe_scope_and_context(self):
        self.client.force_login(self.faculty)

        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success"], True)
        feedback = FacultyFeedback.objects.get()
        self.assertEqual(feedback.faculty_user, self.faculty)
        self.assertEqual(feedback.tenant, self.tenant)
        self.assertEqual(feedback.campus, self.campus)
        self.assertEqual(feedback.rating, FacultyFeedback.Rating.HAPPY)
        self.assertEqual(feedback.page_path, "/faculty/dashboard/")
        self.assertEqual(feedback.route_name, "faculty_portal:dashboard")
        self.assertEqual(feedback.feature_code, "DASHBOARD")
        self.assertEqual(feedback.referrer_path, "")
        self.assertNotIn("token=secret", feedback.page_path)
        self.assertTrue(
            AuditLog.objects.filter(action="FACULTY_FEEDBACK_SUBMITTED", entity_id=str(feedback.id)).exists()
        )
        audit = AuditLog.objects.get(action="FACULTY_FEEDBACK_SUBMITTED")
        self.assertNotIn("suggestion", audit.metadata_json)

    def test_neutral_feedback_accepts_optional_whitespace_suggestion_as_blank(self):
        self.client.force_login(self.faculty)

        response = self._post(
            rating=FacultyFeedback.Rating.NEUTRAL,
            suggestion="   ",
            route_name="faculty_portal:my_courses",
            page_path="/faculty/my-courses/",
        )

        self.assertEqual(response.status_code, 200)
        feedback = FacultyFeedback.objects.get()
        self.assertEqual(feedback.rating, FacultyFeedback.Rating.NEUTRAL)
        self.assertEqual(feedback.suggestion, "")
        self.assertEqual(feedback.feature_code, "MY_COURSES")

    def test_sad_feedback_accepts_short_plain_text_suggestion(self):
        self.client.force_login(self.faculty)

        response = self._post(
            rating=FacultyFeedback.Rating.SAD,
            suggestion="<script>alert(1)</script>",
            route_name="faculty_portal:period_activities",
            page_path="/faculty/my-courses/1/periods/2/activities/",
        )

        self.assertEqual(response.status_code, 200)
        feedback = FacultyFeedback.objects.get()
        self.assertEqual(feedback.rating, FacultyFeedback.Rating.SAD)
        self.assertEqual(feedback.suggestion, "<script>alert(1)</script>")
        self.assertEqual(feedback.feature_code, "ACTIVITIES")

    def test_invalid_rating_and_long_suggestion_are_rejected(self):
        self.client.force_login(self.faculty)

        invalid_rating = self._post(rating="SMILING")
        long_suggestion = self._post(
            rating=FacultyFeedback.Rating.SAD,
            page_path="/faculty/my-courses/",
            route_name="faculty_portal:my_courses",
            suggestion="x" * 501,
        )

        self.assertEqual(invalid_rating.status_code, 400)
        self.assertEqual(long_suggestion.status_code, 400)
        self.assertEqual(FacultyFeedback.objects.count(), 0)

    def test_cooldown_scope_blocks_same_page_but_allows_different_page_user_and_later_submission(self):
        self.client.force_login(self.faculty)

        first = self._post(page_path="/faculty/dashboard/", route_name="faculty_portal:dashboard")
        second = self._post(page_path="/faculty/dashboard/", route_name="faculty_portal:dashboard")
        different_page = self._post(page_path="/faculty/my-courses/", route_name="faculty_portal:my_courses")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(different_page.status_code, 200)
        self.assertEqual(FacultyFeedback.objects.count(), 2)
        self.assertIn("__all__", second.json()["errors"])

        self.client.force_login(self.other_faculty)
        other_faculty_same_page = self._post(page_path="/faculty/dashboard/", route_name="faculty_portal:dashboard")
        self.assertEqual(other_faculty_same_page.status_code, 200)

        FacultyFeedback.objects.filter(faculty_user=self.faculty, route_name="faculty_portal:dashboard").update(
            created_at=timezone.now() - timedelta(minutes=6)
        )
        self.client.force_login(self.faculty)
        after_cooldown = self._post(page_path="/faculty/dashboard/", route_name="faculty_portal:dashboard")
        self.assertEqual(after_cooldown.status_code, 200)
        self.assertEqual(FacultyFeedback.objects.count(), 4)

    def test_page_path_and_route_context_are_sanitized_with_safe_fallbacks(self):
        self.client.force_login(self.faculty)

        cases = [
            {
                "page_path": "https://evil.example/faculty/dashboard/?secret=1",
                "route_name": "faculty_portal:dashboard",
                "expected_path": "/faculty/feedback/submit/",
                "expected_route": "faculty_portal:dashboard",
                "expected_feature": "DASHBOARD",
            },
            {
                "page_path": "/admin-portal/tools/faculty-feedback/",
                "route_name": "admin_portal:faculty_feedback",
                "expected_path": "/faculty/feedback/submit/",
                "expected_route": "",
                "expected_feature": "OTHER_FACULTY_PORTAL_PAGE",
            },
            {
                "page_path": "http://[malformed",
                "route_name": "not a route name",
                "expected_path": "/faculty/feedback/submit/",
                "expected_route": "",
                "expected_feature": "OTHER_FACULTY_PORTAL_PAGE",
            },
            {
                "page_path": "",
                "route_name": "",
                "expected_path": "/faculty/feedback/submit/",
                "expected_route": "",
                "expected_feature": "OTHER_FACULTY_PORTAL_PAGE",
            },
        ]

        for index, case in enumerate(cases):
            response = self._post(
                rating=FacultyFeedback.Rating.HAPPY,
                page_path=case["page_path"],
                route_name=case["route_name"],
                suggestion=f"context case {index}",
            )
            self.assertEqual(response.status_code, 200)
            feedback = FacultyFeedback.objects.get(suggestion=f"context case {index}")
            self.assertEqual(feedback.page_path, case["expected_path"])
            self.assertEqual(feedback.route_name, case["expected_route"])
            self.assertEqual(feedback.feature_code, case["expected_feature"])
            self.assertNotIn("secret=1", feedback.page_path)
            self.assertNotIn("admin-portal", feedback.page_path)
            feedback.created_at = timezone.now() - timedelta(minutes=6)
            feedback.save(update_fields=["created_at"])

    def test_unauthenticated_and_non_faculty_portal_users_are_denied(self):
        unauthenticated = self._post()
        self.client.force_login(self.admin)
        admin_response = self._post()

        self.assertNotEqual(unauthenticated.status_code, 200)
        self.assertEqual(admin_response.status_code, 403)
        self.assertEqual(FacultyFeedback.objects.count(), 0)

    def test_csrf_is_required_for_submission(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.faculty)

        response = csrf_client.post(self.url, {"rating": FacultyFeedback.Rating.HAPPY})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(FacultyFeedback.objects.count(), 0)

    def test_feedback_widget_appears_only_on_authenticated_faculty_base_pages(self):
        self.client.force_login(self.faculty)

        faculty_response = self.client.get(reverse("faculty_portal:my_courses"))
        public_response = self.client.get(reverse("faculty_portal:public_index"))
        admin_response = self.client.get(reverse("admin_portal:root_slash"))

        self.assertContains(faculty_response, 'id="faculty-feedback-open"', html=False)
        self.assertContains(faculty_response, "Faculty Quick Guide")
        self.assertContains(faculty_response, 'data-route-name="faculty_portal:my_courses"', html=False)
        self.assertNotContains(public_response, "faculty-feedback-open")
        self.assertNotIn(b"faculty-feedback-open", admin_response.content)

    def test_faculty_feedback_migration_directory_contains_only_expected_source_files(self):
        migration_dir = Path(__file__).resolve().parent / "migrations"
        entries = sorted(path.name for path in migration_dir.glob("*.py"))

        self.assertEqual(entries, ["0001_initial.py", "__init__.py"])
