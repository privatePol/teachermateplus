from django.conf import settings
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.admin_portal.forms import UserCreateForm, UserRoleAssignmentForm, UserUpdateForm
from apps.admin_portal.views import _send_new_user_credentials_email, _send_user_password_change_credentials_email
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

    def test_user_change_password_page_has_generate_password_button(self):
        user = User.objects.create_user(
            username="reset_page_user",
            email="reset_page_user@example.com",
            password="OldPass123!",
        )

        response = self.client.get(reverse("admin_portal:user_change_password", args=[user.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Generate Password")
        self.assertContains(response, "generate-password-btn")
        self.assertContains(response, "generatePassword")
        self.assertContains(response, "id_new_password1")
        self.assertContains(response, "id_new_password2")
        self.assertContains(response, "Save and Email Password")
        self.assertContains(response, 'const upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"')
        self.assertContains(response, 'const lower = "abcdefghijkmnopqrstuvwxyz"')
        self.assertContains(response, 'const digits = "23456789"')
        self.assertContains(response, 'const symbols = "!@#$%^&*()-_=+"')
        self.assertContains(response, "generatePassword(14)")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ALLOWED_HOSTS=["tmp.ncba.edu.ph"],
    )
    def test_user_change_password_saves_and_emails_temporary_credentials(self):
        user = User.objects.create_user(
            username="reset_email_user",
            email="reset_email_user@example.com",
            password="OldPass123!",
            must_change_password=False,
        )

        response = self.client.post(
            reverse("admin_portal:user_change_password", args=[user.id]),
            {
                "new_password1": "NewResetPass123!",
                "new_password2": "NewResetPass123!",
            },
            HTTP_HOST="tmp.ncba.edu.ph",
            secure=True,
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewResetPass123!"))
        self.assertTrue(user.must_change_password)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["reset_email_user@example.com"])
        self.assertIn("Password Updated", email.subject)
        self.assertIn("Username: reset_email_user", email.body)
        self.assertIn("Temporary Password: NewResetPass123!", email.body)
        self.assertIn("https://tmp.ncba.edu.ph/", email.body)
        self.assertNotIn("/admin-portal/", email.body)
        self.assertNotIn("/faculty/", email.body)
        html_body = email.alternatives[0].content
        self.assertIn("PASSWORD UPDATED", html_body)
        self.assertIn("Open TeacherMate+", html_body)
        self.assertIn("NewResetPass123!", html_body)
        self.assertNotIn("/admin-portal/", html_body)
        self.assertNotIn("/faculty/", html_body)
        self.assertTrue(
            AuditLog.objects.filter(
                action="SEND_PASSWORD_CHANGE_EMAIL",
                entity_type="User",
                entity_id=str(user.id),
            ).exists()
        )
        audit_payload = "\n".join(
            str(value)
            for log in AuditLog.objects.filter(entity_type="User", entity_id=str(user.id))
            for value in (log.before_json, log.after_json, log.metadata_json)
        )
        self.assertNotIn("NewResetPass123!", audit_payload)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_user_change_password_invalid_form_does_not_email_or_save(self):
        user = User.objects.create_user(
            username="invalid_reset_user",
            email="invalid_reset_user@example.com",
            password="OldPass123!",
            must_change_password=False,
        )

        response = self.client.post(
            reverse("admin_portal:user_change_password", args=[user.id]),
            {
                "new_password1": "ValidResetPass123!",
                "new_password2": "DifferentResetPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Passwords do not match.")
        user.refresh_from_db()
        self.assertTrue(user.check_password("OldPass123!"))
        self.assertFalse(user.must_change_password)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(
            AuditLog.objects.filter(
                action__in=["CHANGE_PASSWORD", "SEND_PASSWORD_CHANGE_EMAIL"],
                entity_type="User",
                entity_id=str(user.id),
            ).exists()
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ALLOWED_HOSTS=["tmp.ncba.edu.ph"],
    )
    def test_user_can_login_with_admin_reset_password(self):
        admin_access = Permission.objects.get(code="admin_portal.access")
        user = User.objects.create_user(
            username="reset_login_user",
            email="reset_login_user@example.com",
            password="OldPass123!",
            must_change_password=False,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserPermission.objects.create(user=user, permission=admin_access)

        response = self.client.post(
            reverse("admin_portal:user_change_password", args=[user.id]),
            {
                "new_password1": "Generated7!Login",
                "new_password2": "Generated7!Login",
            },
            HTTP_HOST="tmp.ncba.edu.ph",
            secure=True,
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.client.logout()

        login_response = self.client.post(
            reverse("accounts:admin_login"),
            {"username": "reset_login_user", "password": "Generated7!Login"},
            HTTP_HOST="tmp.ncba.edu.ph",
        )

        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.url, reverse("accounts:admin_change_password"))

    def test_user_change_password_permission_still_requires_users_update(self):
        admin_access = Permission.objects.get(code="admin_portal.access")
        limited_admin = User.objects.create_user(
            username="limited_admin",
            email="limited_admin@example.com",
            password="LimitedPass123!",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserPermission.objects.create(user=limited_admin, permission=admin_access)
        target_user = User.objects.create_user(
            username="permission_target",
            email="permission_target@example.com",
            password="OldPass123!",
        )

        self.client.force_login(limited_admin)
        response = self.client.get(reverse("admin_portal:user_change_password", args=[target_user.id]))

        self.assertEqual(response.status_code, 403)

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

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ALLOWED_HOSTS=["tmp.ncba.edu.ph"],
    )
    def test_user_password_change_email_uses_standard_credential_card(self):
        user = User.objects.create_user(
            username="password_card_user",
            email="password_card_user@example.com",
            password="TemporaryPass123!",
        )
        request = RequestFactory().get("/", HTTP_HOST="tmp.ncba.edu.ph", secure=True)

        sent_count = _send_user_password_change_credentials_email(request, user, "ResetCardPass123!")

        self.assertEqual(sent_count, 1)
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertIn("https://tmp.ncba.edu.ph/", email.body)
        self.assertIn("Temporary Password: ResetCardPass123!", email.body)
        self.assertNotIn("/admin-portal/", email.body)
        self.assertNotIn("/faculty/", email.body)
        html_body = email.alternatives[0].content
        self.assertIn("PASSWORD UPDATED", html_body)
        self.assertIn("Open TeacherMate+", html_body)
        self.assertIn("ResetCardPass123!", html_body)
        self.assertNotIn("/admin-portal/", html_body)
        self.assertNotIn("/faculty/", html_body)


class UserRolePermissionSeparationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="NCBA6", name="NCBA6")
        self.campus = Campus.objects.create(tenant=self.tenant, code="NCBA-06", name="Scope Campus")

        self.admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access", defaults={"module": "admin_portal", "action": "access"}
        )
        self.users_read, _ = Permission.objects.get_or_create(
            code="users.read", defaults={"module": "users", "action": "read"}
        )
        self.roles_read, _ = Permission.objects.get_or_create(
            code="roles.read", defaults={"module": "roles", "action": "read"}
        )
        self.roles_update, _ = Permission.objects.get_or_create(
            code="roles.update", defaults={"module": "roles", "action": "update"}
        )
        self.user_roles_update, _ = Permission.objects.get_or_create(
            code="user_roles.update", defaults={"module": "user_roles", "action": "update"}
        )

        self.actor_role = Role.objects.create(code="SCOPE_ADMIN", name="Scope Admin")
        self.assignable_role = Role.objects.create(code="FACULTY", name="Faculty")

        self.actor = User.objects.create_user(
            username="scope_admin",
            email="scope_admin@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.target = User.objects.create_user(
            username="managed_user",
            email="managed_user@example.com",
            password="testpass123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

        UserRole.objects.create(user=self.actor, role=self.actor_role, tenant=self.tenant, campus=self.campus)
        RolePermission.objects.create(role=self.actor_role, permission=self.admin_access)
        RolePermission.objects.create(role=self.actor_role, permission=self.users_read)
        RolePermission.objects.create(role=self.actor_role, permission=self.roles_read)

        self.client.force_login(self.actor)

    def test_user_list_shows_manage_roles_action_with_user_roles_update_only(self):
        RolePermission.objects.create(role=self.actor_role, permission=self.user_roles_update)

        response = self.client.get(reverse("admin_portal:user_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin_portal:user_roles", args=[self.target.id]))

    def test_user_roles_page_allows_dedicated_user_roles_update_without_roles_update(self):
        RolePermission.objects.create(role=self.actor_role, permission=self.user_roles_update)

        response = self.client.get(reverse("admin_portal:user_roles", args=[self.target.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current Assignments")

    def test_user_roles_page_rejects_roles_update_without_user_roles_update(self):
        RolePermission.objects.create(role=self.actor_role, permission=self.roles_update)

        response = self.client.get(reverse("admin_portal:user_roles", args=[self.target.id]))

        self.assertEqual(response.status_code, 403)
