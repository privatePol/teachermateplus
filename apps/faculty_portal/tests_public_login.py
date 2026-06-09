from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils import timezone

from apps.accounts.models import User
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Tenant


class FacultyPublicLoginTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="FAIRVIEW", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
        )
        faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        faculty_access = Permission.objects.create(
            code="faculty_portal.access",
            module="faculty_portal",
            action="access",
        )
        RolePermission.objects.create(role=faculty_role, permission=faculty_access)
        self.user = User.objects.create_user(
            username="faculty_landing",
            email="faculty_landing@example.com",
            password="FacultyPass123!",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            must_change_password=False,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=self.user,
            role=faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

    def test_public_faculty_login_form_posts_to_landing_page(self):
        response = self.client.get(reverse("faculty_portal:public_index"))

        self.assertContains(response, 'action="/faculty/"', status_code=200)
        self.assertContains(response, "logos/teachermate_logo_official.png")
        self.assertNotContains(response, "logos/egp_logo_official.png")
        self.assertContains(response, "Welcome to NCBA's TeacherMate+")
        self.assertContains(response, "TeacherMate+ helps our faculty members manage teaching loads")
        self.assertContains(response, "What NCBA Faculty Members See When They Enter TeacherMate+")
        self.assertContains(response, "Connected Grade Records for NCBA Operations")
        self.assertContains(response, "Why NCBA Uses TeacherMate+ Instead of Standalone Grade Files")
        self.assertContains(response, "NCBA's authorized systems")
        self.assertNotContains(response, "your existing SIS")
        self.assertNotContains(response, "TeacherMate+ vs Standalone Grade Files")
        self.assertNotContains(response, "helps institutions")

    def test_site_root_redirects_to_faculty_landing_page(self):
        response = self.client.get("/")

        self.assertRedirects(
            response,
            reverse("faculty_portal:public_index"),
            status_code=302,
            target_status_code=200,
        )

    def test_invalid_public_faculty_login_stays_on_landing_page(self):
        response = self.client.post(
            reverse("faculty_portal:public_index"),
            {"username": self.user.username, "password": "wrong-password"},
        )

        self.assertContains(response, "Invalid username or password.", status_code=200)
        self.assertEqual(response.wsgi_request.path, reverse("faculty_portal:public_index"))

    def test_valid_public_faculty_login_uses_normal_dashboard_redirect(self):
        response = self.client.post(
            reverse("faculty_portal:public_index"),
            {"username": self.user.username, "password": "FacultyPass123!"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("faculty_portal:dashboard"))

    def test_faculty_password_recovery_links_return_to_public_landing(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        urls = [
            reverse("accounts:faculty_forgot_password"),
            reverse("accounts:faculty_forgot_password_done"),
            reverse(
                "accounts:faculty_password_reset_confirm",
                kwargs={"uidb64": uid, "token": token},
            ),
            reverse("accounts:faculty_password_reset_complete"),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'href="/faculty/"', status_code=200)
                self.assertContains(response, "logos/teachermateplus_logo.png")
                self.assertNotContains(response, 'href="/faculty/login/"')
