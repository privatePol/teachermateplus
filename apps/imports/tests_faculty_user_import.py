from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.faculty_provisioning import FacultyInvitationService
from apps.accounts.models import FacultyInvitation
from apps.auditlog.models import AuditLog
from apps.imports.models import ImportBatch, ImportBatchRow
from apps.imports.services import BulkImportService, ImportTemplateService
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Department, Tenant


User = get_user_model()


class FacultyImportTestMixin:
    headers = [
        "tenant_code",
        "campus_code",
        "department_code",
        "first_name",
        "middle_name",
        "last_name",
        "email",
        "username",
    ]

    def setUp(self):
        super().setUp()
        self.actor = User.objects.create_superuser(
            username="faculty_import_super",
            email="faculty_import_super@ncba.edu.ph",
            password="AdminPass!123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.tenant = Tenant.objects.create(code="FIMP", name="Faculty Import Tenant")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="COLLEGE",
            name="College",
        )
        self.faculty_role, _ = Role.objects.update_or_create(
            code="FACULTY",
            defaults={"name": "Faculty", "is_active": True},
        )
        for code, action in (
            ("admin_portal.access", "access"),
            ("import_batches.read", "read"),
            ("faculty_users.view_import", "view_import"),
            ("faculty_users.import", "import"),
            ("faculty_users.send_import_invitations", "send_import_invitations"),
            ("faculty_users.resend_invitation", "resend_invitation"),
            ("users.read", "read"),
            ("users.update", "update"),
        ):
            Permission.objects.update_or_create(
                code=code,
                defaults={"module": code.split(".", 1)[0], "action": action, "is_active": True},
            )
        self.scope = {
            "tenant_id": self.tenant.id,
            "campus_id": self.campus.id,
            "tenant_ids": [self.tenant.id],
            "campus_ids": [self.campus.id],
            "department_ids": [self.department.id],
        }
        self.preview_request = SimpleNamespace(scope=self.scope)

    def upload(self, rows, *, headers=None, raw_bytes=None, filename="faculty_users.csv"):
        if raw_bytes is None:
            lines = [",".join(headers or self.headers), *rows]
            raw_bytes = "\n".join(lines).encode("utf-8")
        uploaded = SimpleUploadedFile(filename, raw_bytes, content_type="text/csv")
        return BulkImportService.validate_and_stage_upload(
            import_type=ImportBatch.ImportType.FACULTY_USERS,
            uploaded_file=uploaded,
            user=self.actor,
            request=self.preview_request,
        )

    def valid_row(self, *, email="juan.delacruz@ncba.edu.ph", username=""):
        return f"FIMP,MAIN,COLLEGE, Juan , Santos , Dela Cruz , {email} , {username}"

    def confirm(self, batch, *, send=False, request=None):
        return BulkImportService.confirm_batch(
            batch=batch,
            actor=self.actor,
            send_invitation_emails=send,
            request=request,
        )


class FacultyUserImportPreviewTests(FacultyImportTestMixin, TestCase):
    def test_exact_template_and_permission_mapping(self):
        self.assertEqual(
            ImportTemplateService.get_headers(ImportBatch.ImportType.FACULTY_USERS),
            self.headers,
        )
        self.assertEqual(
            BulkImportService.required_permission(ImportBatch.ImportType.FACULTY_USERS),
            "faculty_users.import",
        )

    def test_valid_single_and_multi_row_preview_normalizes_and_has_no_side_effects(self):
        second = self.valid_row(email="maria.santos@ncba.edu.ph", username="maria.santos")
        batch = self.upload([self.valid_row(), "        ", second])

        self.assertEqual((batch.total_rows, batch.valid_rows, batch.invalid_rows), (2, 2, 0))
        first = batch.rows.order_by("row_number").first()
        self.assertEqual(first.row_number, 2)
        self.assertEqual(first.normalized_data_json["first_name"], "Juan")
        self.assertEqual(first.normalized_data_json["middle_name"], "Santos")
        self.assertEqual(first.normalized_data_json["username"], "juan.delacruz")
        self.assertTrue(first.normalized_data_json["username_derived"])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(UserRole.objects.count(), 0)
        self.assertEqual(FacultyInvitation.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_header_must_be_exact_and_rejects_unexpected_security_fields(self):
        missing = self.upload([self.valid_row()], headers=self.headers[:-1])
        self.assertEqual(missing.status, ImportBatch.Status.VALIDATION_FAILED)

        unexpected_headers = [*self.headers, "role"]
        unexpected = self.upload(
            [self.valid_row() + ",SUPER_ADMIN"],
            headers=unexpected_headers,
            filename="unexpected.csv",
        )
        self.assertEqual(unexpected.status, ImportBatch.Status.VALIDATION_FAILED)
        self.assertIn("Invalid template headers", unexpected.error_summary_json["messages"][0])

    def test_invalid_utf8_is_rejected_and_raw_file_is_never_retained(self):
        batch = self.upload([], raw_bytes=b"\xff\xfe\x00\x01")
        self.assertEqual(batch.status, ImportBatch.Status.VALIDATION_FAILED)
        self.assertFalse(batch.source_file)
        self.assertEqual(batch.metadata_json["raw_file_retention"], "NOT_STORED_AFTER_PARSE")

    def test_invalid_email_and_reference_codes_are_reported(self):
        other_tenant = Tenant.objects.create(code="OTHER", name="Other")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER", name="Other")
        Department.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            code="OTHER",
            name="Other",
        )
        rows = [
            "BAD,MAIN,COLLEGE,A,,B,valid@ncba.edu.ph,valid1",
            "FIMP,BAD,COLLEGE,A,,B,valid2@ncba.edu.ph,valid2",
            "FIMP,MAIN,BAD,A,,B,valid3@ncba.edu.ph,valid3",
            "FIMP,MAIN,COLLEGE,A,,B,not-an-email,valid4",
            "OTHER,OTHER,OTHER,A,,B,valid5@ncba.edu.ph,valid5",
        ]
        self.actor.is_superuser = False
        self.actor.save(update_fields=["is_superuser"])
        batch = self.upload(rows)

        self.assertEqual((batch.valid_rows, batch.invalid_rows), (0, 5))
        errors = " ".join(
            error
            for row in batch.rows.all()
            for error in (row.errors_json or [])
        )
        self.assertIn("tenant_code 'BAD' not found", errors)
        self.assertIn("campus_code 'BAD' not found", errors)
        self.assertIn("department_code 'BAD' not found", errors)
        self.assertIn("Enter a valid email address", errors)
        self.assertIn("outside your scope", errors)

    def test_duplicate_email_and_username_mark_every_conflicting_row_invalid(self):
        batch = self.upload(
            [
                self.valid_row(email="duplicate@ncba.edu.ph", username="first"),
                self.valid_row(email="duplicate@ncba.edu.ph", username="second"),
                self.valid_row(email="third@ncba.edu.ph", username="same"),
                self.valid_row(email="fourth@ncba.edu.ph", username="same"),
            ]
        )

        self.assertEqual((batch.valid_rows, batch.invalid_rows), (0, 4))
        self.assertTrue(
            all(row.result_code == "FAILED_VALIDATION" for row in batch.rows.all())
        )

    def test_derived_username_conflict_is_rejected(self):
        User.objects.create_user(
            username="taken",
            email="someone.else@ncba.edu.ph",
            password="ExistingPass!123",
        )
        batch = self.upload([self.valid_row(email="taken@ncba.edu.ph")])

        self.assertEqual(batch.invalid_rows, 1)
        self.assertIn("Username is already assigned", batch.rows.get().errors_json[0])

    def test_inactive_faculty_role_blocks_preview(self):
        self.faculty_role.is_active = False
        self.faculty_role.save(update_fields=["is_active"])
        batch = self.upload([self.valid_row()])

        self.assertEqual(batch.invalid_rows, 1)
        self.assertIn("active FACULTY role", " ".join(batch.rows.get().errors_json))


@override_settings(FACULTY_IMPORT_EMAIL_ENABLED=False)
class FacultyUserProvisioningTests(FacultyImportTestMixin, TestCase):
    def test_confirm_creates_inactive_scoped_faculty_with_unusable_password(self):
        batch = self.upload([self.valid_row()])
        self.confirm(batch)

        user = User.objects.get(username="juan.delacruz")
        self.assertEqual(user.default_tenant, self.tenant)
        self.assertEqual(user.default_campus, self.campus)
        self.assertEqual(user.default_department, self.department)
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.has_usable_password())
        self.assertIsNone(authenticate(username=user.username, password="anything"))
        assignment = UserRole.objects.get(user=user)
        self.assertEqual(assignment.role.code, "FACULTY")
        self.assertEqual((assignment.tenant, assignment.campus, assignment.department), (
            self.tenant,
            self.campus,
            self.department,
        ))
        invitation = FacultyInvitation.objects.get(user=user)
        self.assertEqual(invitation.status, FacultyInvitation.Status.DISABLED_BY_SYSTEM)
        self.assertEqual(invitation.attempt_count, 0)
        row = batch.rows.get()
        self.assertEqual(row.result_code, "CREATED_EMAIL_DISABLED")
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_USER_CREATED").exists())
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_ROLE_ASSIGNED").exists())

    def test_disabled_environment_never_generates_a_token_even_if_requested(self):
        batch = self.upload([self.valid_row()])
        with patch.object(FacultyInvitationService, "_token") as token_builder:
            self.confirm(batch, send=True)

        token_builder.assert_not_called()
        batch.refresh_from_db()
        self.assertFalse(batch.email_system_enabled_snapshot)
        self.assertFalse(batch.send_invitation_emails_requested)

    def test_matching_faculty_is_skipped_without_changes_or_duplicate_role(self):
        batch = self.upload([self.valid_row()])
        self.confirm(batch)
        original = User.objects.get(username="juan.delacruz")
        original_first_name = original.first_name

        second_batch = self.upload([
            "FIMP,MAIN,COLLEGE,Changed,,Name,juan.delacruz@ncba.edu.ph,juan.delacruz"
        ])
        preview_row = second_batch.rows.get()
        self.assertEqual(preview_row.result_code, "PREVIEW_SKIP_EXISTING")
        self.assertEqual(
            preview_row.result_label,
            "Existing matching Faculty account — will be skipped",
        )
        self.confirm(second_batch)

        original.refresh_from_db()
        self.assertEqual(original.first_name, original_first_name)
        self.assertEqual(User.objects.filter(username="juan.delacruz").count(), 1)
        self.assertEqual(UserRole.objects.filter(user=original).count(), 1)
        self.assertEqual(second_batch.rows.get().result_code, "SKIPPED_EXISTING")
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_IMPORT_ROW_SKIPPED").exists())

    def test_same_batch_cannot_be_confirmed_twice(self):
        batch = self.upload([self.valid_row()])
        self.confirm(batch)
        with self.assertRaises(ValidationError):
            self.confirm(batch)
        self.assertEqual(User.objects.filter(username="juan.delacruz").count(), 1)

    def test_existing_user_conflicts_are_rejected_without_modification(self):
        existing = User.objects.create_user(
            username="existing",
            email="existing@ncba.edu.ph",
            password="ExistingPass!123",
            first_name="Original",
        )
        batch = self.upload([self.valid_row(email=existing.email, username=existing.username)])

        self.assertEqual(batch.invalid_rows, 1)
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, "Original")
        self.assertFalse(UserRole.objects.filter(user=existing, role=self.faculty_role).exists())

    def test_confirmation_revalidates_permission_and_active_references(self):
        batch = self.upload([self.valid_row()])
        self.department.is_active = False
        self.department.save(update_fields=["is_active"])
        self.confirm(batch)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.CONFIRM_FAILED)
        self.assertEqual(batch.rows.get().result_code, "FAILED_PROVISIONING")
        self.assertFalse(User.objects.filter(username="juan.delacruz").exists())

        self.department.is_active = True
        self.department.save(update_fields=["is_active"])
        second_batch = self.upload([self.valid_row()])
        unauthorized = User.objects.create_user(
            username="unauthorized",
            email="unauthorized@ncba.edu.ph",
            password="AdminPass!123",
        )
        with self.assertRaises(PermissionDenied):
            BulkImportService.confirm_batch(batch=second_batch, actor=unauthorized)

    def test_partial_success_keeps_successful_row_when_later_row_conflicts(self):
        batch = self.upload([
            self.valid_row(),
            self.valid_row(email="maria@ncba.edu.ph", username="maria"),
        ])
        User.objects.create_user(
            username="maria",
            email="race@ncba.edu.ph",
            password="RacePass!123",
        )
        self.confirm(batch)

        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.CONFIRM_FAILED)
        self.assertTrue(User.objects.filter(username="juan.delacruz").exists())
        failed = batch.rows.get(row_number=3)
        self.assertEqual(failed.result_code, "FAILED_PROVISIONING")
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_IMPORT_ROW_FAILED").exists())

    def test_cross_tenant_actor_cannot_confirm_batch(self):
        batch = self.upload([self.valid_row()])
        other_tenant = Tenant.objects.create(code="ACTOR", name="Actor Tenant")
        other_campus = Campus.objects.create(tenant=other_tenant, code="ACTOR", name="Actor Campus")
        other_department = Department.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            code="ACTOR",
            name="Actor Department",
        )
        scoped_role = Role.objects.create(code="SCOPED_IMPORTER", name="Scoped Importer")
        RolePermission.objects.create(
            role=scoped_role,
            permission=Permission.objects.get(code="faculty_users.import"),
        )
        scoped_actor = User.objects.create_user(
            username="scoped-actor",
            email="scoped-actor@ncba.edu.ph",
            password="ScopedPass!123",
            default_tenant=other_tenant,
            default_campus=other_campus,
            default_department=other_department,
        )
        UserRole.objects.create(
            user=scoped_actor,
            role=scoped_role,
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
        )

        with self.assertRaises(PermissionDenied):
            BulkImportService.confirm_batch(batch=batch, actor=scoped_actor)
        self.assertFalse(User.objects.filter(username="juan.delacruz").exists())


@override_settings(
    FACULTY_IMPORT_EMAIL_ENABLED=True,
    FACULTY_INVITATION_EXPIRY_HOURS=24,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class FacultyInvitationTests(FacultyImportTestMixin, TestCase):
    def request(self):
        request = RequestFactory().get("/")
        request.user = self.actor
        request.scope = self.scope
        return request

    def create_and_send(self):
        batch = self.upload([self.valid_row()])
        self.confirm(batch, send=True, request=self.request())
        user = User.objects.get(username="juan.delacruz")
        invitation = FacultyInvitation.objects.get(user=user)
        return batch, user, invitation

    def test_email_requires_system_batch_and_permission_and_contains_link_not_password(self):
        batch, user, invitation = self.create_and_send()

        self.assertEqual(invitation.status, FacultyInvitation.Status.SENT)
        self.assertEqual(invitation.attempt_count, 1)
        self.assertIsNotNone(invitation.last_successfully_sent_at)
        expected_expiry = invitation.last_successfully_sent_at + timedelta(hours=24)
        self.assertLess(abs((invitation.expires_at - expected_expiry).total_seconds()), 1)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        html_body = mail.outbox[0].alternatives[0].content
        self.assertIn("/faculty/invitation/", body)
        self.assertNotIn("temporary password", body.lower())
        self.assertNotIn("password is", body.lower())
        self.assertIn("ACCOUNT ONBOARDING", body)
        self.assertIn("ACCOUNT ONBOARDING", html_body)
        self.assertIn("Welcome to TeacherMate+", html_body)
        self.assertIn("Set Password and Activate Account", html_body)
        self.assertIn("linear-gradient(90deg,#1f6a3d 0%,#2f8a4f 45%,#bfa629 100%)", html_body)
        self.assertNotIn("/admin-portal/", html_body)
        self.assertEqual(batch.rows.get().result_code, "CREATED_INVITATION_SENT")
        model_fields = {field.name for field in FacultyInvitation._meta.fields}
        self.assertFalse({"token", "password", "setup_url"} & model_fields)

    def test_enabled_environment_without_batch_option_creates_no_email_or_usable_link(self):
        batch = self.upload([self.valid_row()])
        self.confirm(batch, send=False, request=self.request())

        invitation = FacultyInvitation.objects.get(user__username="juan.delacruz")
        self.assertEqual(invitation.status, FacultyInvitation.Status.NOT_REQUESTED)
        self.assertEqual(invitation.attempt_count, 0)
        self.assertIsNone(invitation.expires_at)
        self.assertEqual(batch.rows.get().result_code, "CREATED_INVITATION_NOT_REQUESTED")
        self.assertEqual(len(mail.outbox), 0)

    def test_valid_link_sets_password_activates_and_is_single_use(self):
        _, user, invitation = self.create_and_send()
        token = FacultyInvitationService._token(invitation)
        url = reverse(
            "accounts:faculty_invitation_accept",
            kwargs={"public_id": invitation.public_id},
        )
        self.assertEqual(Client().get(url).status_code, 200)
        response = Client().post(
            url,
            {
                "invitation_token": token,
                "new_password1": "SecureFacultyPass!123",
                "new_password2": "SecureFacultyPass!123",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("faculty_portal:public_index"))
        user.refresh_from_db()
        invitation.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("SecureFacultyPass!123"))
        self.assertEqual(invitation.status, FacultyInvitation.Status.ACCEPTED)
        invalid_response = Client().get(url)
        self.assertContains(invalid_response, 'href="/faculty/"', status_code=200)
        self.assertNotContains(invalid_response, 'href="/faculty/login/"')
        self.assertNotContains(invalid_response, "Go to Admin Login")
        self.assertIsNone(FacultyInvitationService.resolve_valid(public_id=invitation.public_id, token=token))
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_INVITATION_ACCEPTED").exists())
        admin_client = Client()
        admin_client.force_login(self.actor)
        detail = admin_client.get(reverse("admin_portal:user_update", args=[user.id]))
        self.assertContains(detail, "Accepted")
        self.assertNotContains(detail, "Resend Invitation")

    def test_expired_link_cannot_activate(self):
        _, user, invitation = self.create_and_send()
        token = FacultyInvitationService._token(invitation)
        invitation.expires_at = timezone.now() - timedelta(seconds=1)
        invitation.save(update_fields=["expires_at"])

        self.assertIsNone(FacultyInvitationService.resolve_valid(public_id=invitation.public_id, token=token))
        user.refresh_from_db()
        invitation.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(invitation.status, FacultyInvitation.Status.EXPIRED)

    def test_resend_supersedes_previous_link_without_creating_user(self):
        _, user, invitation = self.create_and_send()
        first_token = FacultyInvitationService._token(invitation)
        first_expiry = invitation.expires_at
        invitation.last_attempted_at = timezone.now() - timedelta(minutes=6)
        invitation.save(update_fields=["last_attempted_at"])

        result = FacultyInvitationService.send_or_resend(
            user=user,
            actor=self.actor,
            request=self.request(),
            resend=True,
        )
        result.invitation.refresh_from_db()
        second_token = FacultyInvitationService._token(result.invitation)

        self.assertTrue(result.sent)
        self.assertTrue(result.resent)
        self.assertEqual(User.objects.filter(username=user.username).count(), 1)
        self.assertEqual(result.invitation.attempt_count, 2)
        self.assertGreater(result.invitation.expires_at, first_expiry)
        self.assertIsNone(
            FacultyInvitationService.resolve_valid(public_id=invitation.public_id, token=first_token)
        )
        self.assertIsNotNone(
            FacultyInvitationService.resolve_valid(public_id=invitation.public_id, token=second_token)
        )
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_INVITATION_RESENT").exists())

    def test_resend_throttle_and_accepted_account_block_resend(self):
        _, user, invitation = self.create_and_send()
        with self.assertRaisesMessage(ValidationError, "five minutes"):
            FacultyInvitationService.send_or_resend(
                user=user,
                actor=self.actor,
                request=self.request(),
                resend=True,
            )

        invitation.last_attempted_at = timezone.now() - timedelta(minutes=6)
        invitation.status = FacultyInvitation.Status.ACCEPTED
        invitation.save(update_fields=["last_attempted_at", "status"])
        with self.assertRaisesMessage(ValidationError, "already been accepted"):
            FacultyInvitationService.send_or_resend(
                user=user,
                actor=self.actor,
                request=self.request(),
                resend=True,
            )

    def test_smtp_failure_keeps_account_and_records_sanitized_failure(self):
        batch = self.upload([self.valid_row()])
        with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=RuntimeError("secret smtp detail")):
            self.confirm(batch, send=True, request=self.request())

        user = User.objects.get(username="juan.delacruz")
        invitation = FacultyInvitation.objects.get(user=user)
        self.assertFalse(user.is_active)
        self.assertEqual(invitation.status, FacultyInvitation.Status.FAILED)
        self.assertEqual(invitation.failure_reason, "RuntimeError")
        self.assertEqual(batch.rows.get().result_code, "CREATED_INVITATION_FAILED")
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_INVITATION_FAILED").exists())

    def test_sender_without_send_permission_is_blocked(self):
        batch = self.upload([self.valid_row()])
        limited_role = Role.objects.create(code="LIMITED_IMPORTER", name="Limited Importer")
        import_permission = Permission.objects.get(code="faculty_users.import")
        RolePermission.objects.create(role=limited_role, permission=import_permission)
        limited = User.objects.create_user(
            username="limited",
            email="limited@ncba.edu.ph",
            password="LimitedPass!123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=limited,
            role=limited_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )

        with self.assertRaisesMessage(ValidationError, "permission"):
            BulkImportService.confirm_batch(
                batch=batch,
                actor=limited,
                send_invitation_emails=True,
                request=self.request(),
            )
        self.assertFalse(User.objects.filter(username="juan.delacruz").exists())

    def test_resend_requires_permission_and_token_is_absent_from_audits(self):
        _, user, invitation = self.create_and_send()
        token = FacultyInvitationService._token(invitation)
        invitation.last_attempted_at = timezone.now() - timedelta(minutes=6)
        invitation.save(update_fields=["last_attempted_at"])
        unauthorized = User.objects.create_user(
            username="no-resend",
            email="no-resend@ncba.edu.ph",
            password="NoResendPass!123",
        )

        with self.assertRaises(PermissionDenied):
            FacultyInvitationService.send_or_resend(
                user=user,
                actor=unauthorized,
                request=self.request(),
                resend=True,
            )
        audit_payload = " ".join(
            str(value)
            for audit in AuditLog.objects.all()
            for value in (audit.before_json, audit.after_json, audit.metadata_json)
        )
        self.assertNotIn(token, audit_payload)

    def test_same_csv_reupload_skips_without_resending_or_duplicate_role(self):
        first_batch, user, invitation = self.create_and_send()
        original_version = invitation.version
        original_role_count = UserRole.objects.filter(user=user, role=self.faculty_role).count()
        self.assertEqual(len(mail.outbox), 1)

        second_batch = self.upload([self.valid_row()])
        self.assertEqual(second_batch.rows.get().result_code, "PREVIEW_SKIP_EXISTING")
        self.confirm(second_batch, send=True, request=self.request())

        invitation.refresh_from_db()
        self.assertEqual(second_batch.rows.get().result_code, "SKIPPED_EXISTING")
        self.assertEqual(User.objects.filter(username=user.username).count(), 1)
        self.assertEqual(UserRole.objects.filter(user=user, role=self.faculty_role).count(), original_role_count)
        self.assertEqual(invitation.version, original_version)
        self.assertEqual(len(mail.outbox), 1)


class FacultyImportSecurityAndUiTests(FacultyImportTestMixin, TestCase):
    def test_faculty_upload_page_shows_account_flow_modal_and_image(self):
        client = Client()
        client.force_login(self.actor)

        response = client.get(reverse("admin_portal:import_upload", args=["faculty-users"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-bs-target="#faculty-account-flow-modal"')
        self.assertContains(response, 'id="faculty-account-flow-modal"')
        self.assertContains(response, f'{settings.MEDIA_URL}imahe/faculty-users-acct-flow.png')
        self.assertContains(response, "View Faculty user account flow")

    def limited_admin(self, *, permission_codes, username="limited-ui"):
        role = Role.objects.create(code=f"ROLE_{username.upper()}", name=f"Role {username}")
        for code in permission_codes:
            RolePermission.objects.create(role=role, permission=Permission.objects.get(code=code))
        user = User.objects.create_user(
            username=username,
            email=f"{username}@ncba.edu.ph",
            password="LimitedUiPass!123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        return user

    def test_error_report_neutralizes_spreadsheet_formulas(self):
        batch = self.upload([
            "FIMP,MAIN,COLLEGE,=CMD,,Dela Cruz,not-an-email,formula-user"
        ])
        client = Client()
        client.force_login(self.actor)
        response = client.get(reverse("admin_portal:import_batch_error_report", args=[batch.id]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("'=CMD", response.content.decode("utf-8"))
        self.assertNotIn("Traceback", response.content.decode("utf-8"))

    def test_faculty_batch_pii_requires_view_permission(self):
        batch = self.upload([self.valid_row()])
        limited_role = Role.objects.create(code="IMPORT_ONLY", name="Import Only")
        limited = User.objects.create_user(
            username="import-only",
            email="import-only@ncba.edu.ph",
            password="ImportOnlyPass!123",
            default_tenant=self.tenant,
            default_campus=self.campus,
            default_department=self.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        for code in ("admin_portal.access", "import_batches.read", "faculty_users.import"):
            RolePermission.objects.create(
                role=limited_role,
                permission=Permission.objects.get(code=code),
            )
        UserRole.objects.create(
            user=limited,
            role=limited_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        client = Client()
        client.force_login(limited)
        response = client.get(reverse("admin_portal:import_batch_detail", args=[batch.id]))

        self.assertEqual(response.status_code, 403)

    @override_settings(FACULTY_IMPORT_EMAIL_ENABLED=False)
    def test_detail_checkbox_is_unchecked_and_disabled_and_user_link_is_present(self):
        batch = self.upload([self.valid_row()])
        client = Client()
        client.force_login(self.actor)
        response = client.get(reverse("admin_portal:import_batch_detail", args=[batch.id]))
        content = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="send-invitation-emails"', content)
        self.assertIn("disabled", content)
        self.assertIn("Invitation emails are disabled in this environment", content)
        checkbox_markup = content.split('id="send-invitation-emails"', 1)[1].split(">", 1)[0]
        self.assertNotIn("checked", checkbox_markup)

        self.confirm(batch)
        response = client.get(reverse("admin_portal:import_batch_detail", args=[batch.id]))
        content = response.content.decode("utf-8")
        self.assertIn("Open User", content)
        self.assertIn("Email disabled by system", content)
        self.assertIn("DISABLED_BY_SYSTEM", content)
        self.assertNotIn("/faculty/invitation/", content)

    @override_settings(FACULTY_IMPORT_EMAIL_ENABLED=True)
    def test_detail_checkbox_is_visible_enabled_and_unchecked_when_available(self):
        batch = self.upload([self.valid_row()])
        client = Client()
        client.force_login(self.actor)
        response = client.get(reverse("admin_portal:import_batch_detail", args=[batch.id]))
        content = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        checkbox_markup = content.split('id="send-invitation-emails"', 1)[1].split(">", 1)[0]
        self.assertNotIn("disabled", checkbox_markup)
        self.assertNotIn("checked", checkbox_markup)
        self.assertIn("available and unchecked by default", content)

    @override_settings(FACULTY_IMPORT_EMAIL_ENABLED=True)
    def test_detail_checkbox_is_disabled_without_send_permission(self):
        batch = self.upload([self.valid_row()])
        limited = self.limited_admin(
            permission_codes=[
                "admin_portal.access",
                "import_batches.read",
                "faculty_users.view_import",
                "faculty_users.import",
            ],
            username="no-send-ui",
        )
        client = Client()
        client.force_login(limited)
        response = client.get(reverse("admin_portal:import_batch_detail", args=[batch.id]))
        content = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        checkbox_markup = content.split('id="send-invitation-emails"', 1)[1].split(">", 1)[0]
        self.assertIn("disabled", checkbox_markup)
        self.assertIn("faculty_users.send_import_invitations", content)

    @override_settings(FACULTY_IMPORT_EMAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_import_result_displays_not_requested_failed_and_sent_states(self):
        client = Client()
        client.force_login(self.actor)

        not_requested = self.upload([self.valid_row(email="not-requested@ncba.edu.ph", username="not-requested")])
        self.confirm(not_requested, send=False)
        response = client.get(reverse("admin_portal:import_batch_detail", args=[not_requested.id]))
        self.assertContains(response, "Invitation not requested")
        self.assertContains(response, "NOT_REQUESTED")

        failed = self.upload([self.valid_row(email="failed@ncba.edu.ph", username="failed")])
        with patch("django.core.mail.EmailMultiAlternatives.send", side_effect=RuntimeError("smtp detail")):
            self.confirm(failed, send=True, request=RequestFactory().get("/"))
        response = client.get(reverse("admin_portal:import_batch_detail", args=[failed.id]))
        self.assertContains(response, "Invitation failed")
        self.assertContains(response, "FAILED")
        failed_user = User.objects.get(username="failed")
        failed_detail = client.get(reverse("admin_portal:user_update", args=[failed_user.id]))
        self.assertContains(failed_detail, "Failed")
        self.assertContains(failed_detail, "Resend Invitation")

        sent = self.upload([self.valid_row(email="sent@ncba.edu.ph", username="sent")])
        request = RequestFactory().get("/")
        request.user = self.actor
        request.scope = self.scope
        self.confirm(sent, send=True, request=request)
        response = client.get(reverse("admin_portal:import_batch_detail", args=[sent.id]))
        self.assertContains(response, "Invitation sent")
        self.assertContains(response, "SENT")
        self.assertContains(response, "Invitations requested")
        sent_content = response.content.decode("utf-8")
        checkbox_markup = sent_content.split('id="send-invitation-emails"', 1)[1].split(">", 1)[0]
        self.assertIn("checked", checkbox_markup)

    @override_settings(FACULTY_IMPORT_EMAIL_ENABLED=False)
    def test_user_detail_persistently_shows_email_disabled_and_send_action(self):
        batch = self.upload([self.valid_row()])
        self.confirm(batch)
        user = User.objects.get(username="juan.delacruz")
        client = Client()
        client.force_login(self.actor)
        response = client.get(reverse("admin_portal:user_update", args=[user.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faculty Account Invitation")
        self.assertContains(response, "Email disabled")
        self.assertContains(response, "Send Invitation")
        self.assertContains(response, "Invitation emails are disabled in this environment")

    @override_settings(FACULTY_IMPORT_EMAIL_ENABLED=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_user_detail_resend_remains_available_later_and_restarts_expiry(self):
        batch = self.upload([self.valid_row()])
        request = RequestFactory().get("/")
        request.user = self.actor
        request.scope = self.scope
        self.confirm(batch, send=True, request=request)
        user = User.objects.get(username="juan.delacruz")
        invitation = FacultyInvitation.objects.get(user=user)
        old_version = invitation.version
        old_token = FacultyInvitationService._token(invitation)
        invitation.last_attempted_at = timezone.now() - timedelta(days=1)
        invitation.expires_at = timezone.now() - timedelta(hours=1)
        invitation.save(update_fields=["last_attempted_at", "expires_at"])

        client = Client()
        client.force_login(self.actor)
        detail = client.get(reverse("admin_portal:user_update", args=[user.id]))
        self.assertContains(detail, "Expired")
        self.assertContains(detail, "Resend Invitation")
        user_count = User.objects.count()
        response = client.post(reverse("admin_portal:faculty_user_invitation_send", args=[user.id]))

        self.assertEqual(response.status_code, 302)
        invitation.refresh_from_db()
        self.assertEqual(User.objects.count(), user_count)
        self.assertGreater(invitation.version, old_version)
        self.assertGreater(invitation.expires_at, timezone.now() + timedelta(hours=23, minutes=59))
        self.assertIsNone(
            FacultyInvitationService.resolve_valid(public_id=invitation.public_id, token=old_token)
        )

    def test_preview_and_result_clearly_distinguish_create_skip_and_error(self):
        existing_batch = self.upload([self.valid_row(email="existing-faculty@ncba.edu.ph", username="existing-faculty")])
        self.confirm(existing_batch)
        mixed = self.upload(
            [
                self.valid_row(email="new-faculty@ncba.edu.ph", username="new-faculty"),
                self.valid_row(email="existing-faculty@ncba.edu.ph", username="existing-faculty"),
                self.valid_row(email="bad-email", username="bad-row"),
            ]
        )
        codes = list(mixed.rows.order_by("row_number").values_list("result_code", flat=True))
        self.assertEqual(codes, ["PREVIEW_CREATE", "PREVIEW_SKIP_EXISTING", "FAILED_VALIDATION"])
        client = Client()
        client.force_login(self.actor)
        response = client.get(reverse("admin_portal:import_batch_detail", args=[mixed.id]))
        self.assertContains(response, "New Faculty account — will be created")
        self.assertContains(response, "Existing matching Faculty account — will be skipped")
        self.assertContains(response, "Validation error")

    def test_users_page_import_action_requires_both_faculty_permissions(self):
        client = Client()
        client.force_login(self.actor)
        response = client.get(reverse("admin_portal:user_list"))
        self.assertContains(response, "Import Faculty CSV")
