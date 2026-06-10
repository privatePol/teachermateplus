from django.conf import settings
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, Role
from apps.admin_portal.forms import UserCreateForm, UserRoleAssignmentForm, UserUpdateForm
from apps.admin_portal.views import _send_new_user_credentials_email
from apps.tenants.models import Campus, Department, Tenant


class UserListTests(TestCase):
    def setUp(self):
        Permission.objects.create(code="admin_portal.access", module="admin_portal", action="access")
        Permission.objects.create(code="users.read", module="users", action="read")
        Permission.objects.create(code="users.create", module="users", action="create")
        Permission.objects.create(code="users.update", module="users", action="update")
        Permission.objects.create(code="roles.read", module="roles", action="read")
        Permission.objects.create(code="roles.update", module="roles", action="update")
        self.admin = User.objects.create_superuser(
            username="superadmin",
            email="superadmin@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(self.admin)

    def test_user_list_separates_active_inactive_and_filters_staff(self):
        User.objects.create_user(
            username="active_staff_user",
            email="active_staff@example.com",
            password="testpass123",
            is_staff=True,
        )
        User.objects.create_user(
            username="inactive_staff_user",
            email="inactive_staff@example.com",
            password="testpass123",
            is_staff=True,
            is_active=False,
        )
        User.objects.create_user(
            username="active_nonstaff_user",
            email="active_nonstaff@example.com",
            password="testpass123",
            is_staff=False,
        )

        response = self.client.get(reverse("admin_portal:user_list"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Active Records", content)
        self.assertIn("Inactive Records", content)
        self.assertIn("active_staff_user", content)
        self.assertIn("inactive_staff_user", content)
        self.assertIn("active_nonstaff_user", content)
        self.assertIn("Staff Only", content)
        self.assertIn("Non-staff Only", content)

        response = self.client.get(reverse("admin_portal:user_list"), {"is_staff": "0"})

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("active_nonstaff_user", content)
        self.assertNotIn("active_staff_user", content)
        self.assertNotIn("inactive_staff_user", content)

    def test_user_list_shows_and_resets_privacy_consent(self):
        user = User.objects.create_user(
            username="privacy_user",
            email="privacy_user@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
            privacy_consent_ip="127.0.0.1",
        )

        response = self.client.get(reverse("admin_portal:user_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy Consent")
        self.assertContains(response, "Acknowledged")
        self.assertContains(response, reverse("admin_portal:user_privacy_consent_reset", args=[user.id]))

        response = self.client.get(reverse("admin_portal:user_privacy_consent_reset", args=[user.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RESET PRIVACY CONSENT")

        response = self.client.post(
            reverse("admin_portal:user_privacy_consent_reset", args=[user.id]),
            {"confirmation_phrase": "RESET PRIVACY CONSENT"},
            HTTP_REFERER=reverse("admin_portal:user_list"),
        )

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertIsNone(user.privacy_consent_at)
        self.assertIsNone(user.privacy_consent_version)
        self.assertIsNone(user.privacy_consent_ip)
        self.assertTrue(
            AuditLog.objects.filter(
                action="PRIVACY_CONSENT_RESET",
                entity_type="User",
                entity_id=str(user.id),
            ).exists()
        )

    def test_user_role_department_dropdown_filters_by_selected_campus(self):
        tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        cubao = Campus.objects.create(tenant=tenant, code="NCBA-01", name="Cubao")
        fairview = Campus.objects.create(tenant=tenant, code="NCBA-02", name="Fairview")
        cubao_college = Department.objects.create(
            tenant=tenant,
            campus=cubao,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        fairview_college = Department.objects.create(
            tenant=tenant,
            campus=fairview,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )

        form = UserRoleAssignmentForm(
            data={"tenant": tenant.id, "campus": fairview.id, "department": fairview_college.id, "role": ""},
            tenant_queryset=Tenant.objects.all(),
            campus_queryset=Campus.objects.all(),
            department_queryset=Department.objects.all(),
        )

        department_ids = set(form.fields["department"].queryset.values_list("id", flat=True))
        self.assertIn(fairview_college.id, department_ids)
        self.assertNotIn(cubao_college.id, department_ids)
        option_payload = form.fields["department"].widget.attrs["data-department-options"]
        self.assertIn("COLLEGE - College", option_payload)

        unselected_form = UserRoleAssignmentForm(
            tenant_queryset=Tenant.objects.all(),
            campus_queryset=Campus.objects.all(),
            department_queryset=Department.objects.all(),
        )
        option_payload = unselected_form.fields["department"].widget.attrs["data-department-options"]
        self.assertIn("NCBA-01 / COLLEGE", option_payload)
        self.assertIn("NCBA-02 / COLLEGE", option_payload)

    def test_user_roles_page_loads_campus_dependent_department_script(self):
        tenant = Tenant.objects.create(code="NCBA4", name="NCBA4")
        fairview = Campus.objects.create(tenant=tenant, code="NCBA-02", name="Fairview")
        Department.objects.create(
            tenant=tenant,
            campus=fairview,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        Role.objects.create(code="FACULTY", name="Faculty")
        user = User.objects.create_user(
            username="faculty_user",
            email="faculty_user@example.com",
            password="testpass123",
            default_tenant=tenant,
            default_campus=fairview,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

        response = self.client.get(reverse("admin_portal:user_roles", args=[user.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-campus-dependent="true"')
        self.assertContains(response, "data-department-options")
        self.assertContains(response, "rebuildDepartmentOptions")
        self.assertContains(response, "COLLEGE - College")

    def test_user_update_default_department_dropdown_filters_by_default_campus(self):
        tenant = Tenant.objects.create(code="NCBA2", name="NCBA2")
        cubao = Campus.objects.create(tenant=tenant, code="NCBA-01", name="Cubao")
        fairview = Campus.objects.create(tenant=tenant, code="NCBA-02", name="Fairview")
        cubao_college = Department.objects.create(
            tenant=tenant,
            campus=cubao,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        fairview_college = Department.objects.create(
            tenant=tenant,
            campus=fairview,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        user = User.objects.create_user(
            username="edit_user",
            email="edit_user@example.com",
            password="testpass123",
            default_tenant=tenant,
            default_campus=fairview,
            default_department=fairview_college,
        )

        form = UserUpdateForm(
            instance=user,
            tenant_queryset=Tenant.objects.filter(id=tenant.id),
            campus_queryset=Campus.objects.filter(tenant=tenant),
            department_queryset=Department.objects.filter(tenant=tenant),
        )

        department_ids = set(form.fields["default_department"].queryset.values_list("id", flat=True))
        self.assertIn(fairview_college.id, department_ids)
        self.assertNotIn(cubao_college.id, department_ids)
        self.assertEqual(
            form.fields["default_department"].widget.attrs["data-campus-field-id"],
            "id_default_campus",
        )

    def test_user_create_default_department_dropdown_uses_initial_default_campus(self):
        tenant = Tenant.objects.create(code="NCBA3", name="NCBA3")
        cubao = Campus.objects.create(tenant=tenant, code="NCBA-01", name="Cubao")
        fairview = Campus.objects.create(tenant=tenant, code="NCBA-02", name="Fairview")
        cubao_college = Department.objects.create(
            tenant=tenant,
            campus=cubao,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        fairview_college = Department.objects.create(
            tenant=tenant,
            campus=fairview,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )

        form = UserCreateForm(
            initial={"default_tenant": tenant.id, "default_campus": fairview.id},
            tenant_queryset=Tenant.objects.filter(id=tenant.id),
            campus_queryset=Campus.objects.filter(tenant=tenant),
            department_queryset=Department.objects.filter(tenant=tenant),
        )

        department_ids = set(form.fields["default_department"].queryset.values_list("id", flat=True))
        self.assertIn(fairview_college.id, department_ids)
        self.assertNotIn(cubao_college.id, department_ids)

    def test_user_create_page_loads_default_department_campus_filter_script(self):
        tenant = Tenant.objects.create(code="NCBA5", name="NCBA5")
        cubao = Campus.objects.create(tenant=tenant, code="NCBA-01", name="Cubao")
        fairview = Campus.objects.create(tenant=tenant, code="NCBA-02", name="Fairview")
        Department.objects.create(
            tenant=tenant,
            campus=cubao,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )
        Department.objects.create(
            tenant=tenant,
            campus=fairview,
            code="COLLEGE",
            name="College",
            unit_type=Department.UnitType.DIVISION,
        )

        response = self.client.get(reverse("admin_portal:user_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-campus-dependent="true"')
        self.assertContains(response, 'data-campus-field-id="id_default_campus"')
        self.assertContains(response, "data-department-options")
        self.assertContains(response, "rebuildDepartmentOptions")
        self.assertContains(response, "NCBA-01 | COLLEGE")
        self.assertContains(response, "NCBA-02 | COLLEGE")

    def test_user_create_form_does_not_expose_is_staff(self):
        form = UserCreateForm()

        self.assertNotIn("is_staff", form.fields)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ALLOWED_HOSTS=["tmp.ncba.edu.ph"],
    )
    def test_new_user_credentials_email_uses_only_neutral_teachermate_link(self):
        user = User.objects.create_user(
            username="neutral_link_user",
            email="neutral_link_user@example.com",
            password="TemporaryPass123!",
        )
        request = RequestFactory().get("/", HTTP_HOST="tmp.ncba.edu.ph", secure=True)

        sent_count = _send_new_user_credentials_email(request, user, "TemporaryPass123!")

        self.assertEqual(sent_count, 1)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("https://tmp.ncba.edu.ph/", email.body)
        self.assertNotIn("/admin-portal/", email.body)
        self.assertNotIn("/faculty/", email.body)
        self.assertNotIn("Admin Portal", email.body)
        html_body = email.alternatives[0].content
        self.assertIn("Open TeacherMate+", html_body)
        self.assertNotIn("/admin-portal/", html_body)
        self.assertNotIn("/faculty/", html_body)
