from datetime import date, timedelta
from decimal import Decimal
import json
import os
import re
import sqlite3
import tempfile
from unittest.mock import patch

from django.contrib.auth.hashers import is_password_usable
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core import mail
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import TenantDataExportChallenge, User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.admin_portal.tenant_data_export import TenantSQLiteExportService
from apps.auditlog.models import AuditLog
from apps.enrollment.models import Enrollment
from apps.grading.models import GradeActivity, GradingTemplate, GradingTemplateComponent, GradingTemplatePeriod, StudentActivityScore
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant, TenantApiKey


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TenantDataExportTests(TestCase):
    def setUp(self):
        self.url = reverse("admin_portal:tenant_data_export")
        self.admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={"module": "admin_portal", "action": "access", "is_active": True},
        )
        self.export_permission, _ = Permission.objects.get_or_create(
            code="tenant_data_export.execute",
            defaults={"module": "tenant_data_export", "action": "execute", "is_active": True},
        )
        self.tenant_admin_role, _ = Role.objects.get_or_create(
            code="TENANT_ADMIN",
            defaults={"name": "Tenant Admin", "is_active": True},
        )
        self.campus_admin_role, _ = Role.objects.get_or_create(
            code="CAMPUS_ADMIN",
            defaults={"name": "Campus Admin", "is_active": True},
        )
        self.faculty_role, _ = Role.objects.get_or_create(
            code="FACULTY",
            defaults={"name": "Faculty", "is_active": True},
        )
        RolePermission.objects.get_or_create(role=self.tenant_admin_role, permission=self.admin_access)
        RolePermission.objects.get_or_create(role=self.tenant_admin_role, permission=self.export_permission)
        RolePermission.objects.get_or_create(role=self.campus_admin_role, permission=self.admin_access)

        self.tenant_a = self._create_tenant_bundle("TEN-A", "Tenant A", "2026-A-001")
        self.tenant_b = self._create_tenant_bundle("TEN-B", "Tenant B", "2026-B-001")
        TenantApiKey.objects.create(
            tenant=self.tenant_a["tenant"],
            name="SIS",
            key_prefix="ten-a-prefix",
            key_hash="hashed-api-key-value",
        )
        self.tenant_admin = User.objects.create_user(
            username="tenant-admin",
            email="tenant-admin@example.com",
            password="testpass123",
            default_tenant=self.tenant_a["tenant"],
            default_campus=self.tenant_a["campus"],
            default_department=self.tenant_a["department"],
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=self.tenant_admin,
            role=self.tenant_admin_role,
            tenant=self.tenant_a["tenant"],
            campus=self.tenant_a["campus"],
        )
        self.campus_admin = User.objects.create_user(
            username="campus-admin",
            email="campus-admin@example.com",
            password="testpass123",
            default_tenant=self.tenant_a["tenant"],
            default_campus=self.tenant_a["campus"],
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=self.campus_admin,
            role=self.campus_admin_role,
            tenant=self.tenant_a["tenant"],
            campus=self.tenant_a["campus"],
        )
        self.faculty = User.objects.create_user(
            username="faculty-export",
            email="faculty-export@example.com",
            password="testpass123",
            default_tenant=self.tenant_a["tenant"],
            default_campus=self.tenant_a["campus"],
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=self.faculty,
            role=self.faculty_role,
            tenant=self.tenant_a["tenant"],
            campus=self.tenant_a["campus"],
        )

    def _create_tenant_bundle(self, code, name, student_no):
        tenant = Tenant.objects.create(code=code, name=name)
        campus = Campus.objects.create(tenant=tenant, code=f"{code}-MAIN", name="Main")
        department = Department.objects.create(tenant=tenant, campus=campus, code=f"{code}-COLL", name="College")
        program = Program.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            code=f"{code}-BSIT",
            name="BSIT",
        )
        academic_year = AcademicYear.objects.create(
            tenant=tenant,
            code=f"{code}-AY",
            name="AY",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        term = Term.objects.create(
            tenant=tenant,
            academic_year=academic_year,
            code="1ST",
            name="First Term",
            sequence_no=1,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 10, 31),
        )
        course = Course.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            code=f"{code}-IT101",
            title="IT Fundamentals",
            units=Decimal("3.00"),
        )
        section = Section.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            code=f"{code}-1A",
            name="1A",
        )
        offering = CourseOffering.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            academic_year=academic_year,
            term=term,
            course=course,
            section=section,
        )
        student = Student.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            student_no=student_no,
            last_name="Student",
            first_name=code,
        )
        enrollment = Enrollment.objects.create(
            tenant=tenant,
            campus=campus,
            academic_year=academic_year,
            term=term,
            student=student,
            course_offering=offering,
        )
        template = GradingTemplate.objects.create(
            tenant=tenant,
            code=f"{code}-TPL",
            name="Template",
            is_published=True,
            approval_status=GradingTemplate.ApprovalStatus.APPROVED,
        )
        period = GradingTemplatePeriod.objects.create(template=template, code="PRELIM", name="Prelim", sequence_no=1)
        component = GradingTemplateComponent.objects.create(
            template_period=period,
            code="CS",
            name="Class Standing",
            weight_percentage=Decimal("100.00"),
        )
        activity = GradeActivity.objects.create(
            tenant=tenant,
            campus=campus,
            offering=offering,
            template_period=period,
            template_component=component,
            title="Q1",
            total_score=Decimal("20.00"),
            activity_date=date(2026, 7, 1),
        )
        StudentActivityScore.objects.create(
            activity=activity,
            student=student,
            raw_score=Decimal("18.00"),
            computed_score=Decimal("95.00"),
        )
        return {
            "tenant": tenant,
            "campus": campus,
            "department": department,
            "offering": offering,
            "student": student,
            "enrollment": enrollment,
        }

    def _login_tenant_admin(self):
        self.client.force_login(self.tenant_admin)

    def _start_challenge(self, tenant=None, password="testpass123"):
        self._login_tenant_admin()
        return self.client.post(
            self.url,
            {
                "action": "start",
                "tenant": (tenant or self.tenant_a["tenant"]).id,
                "acknowledgement": "on",
                "password": password,
            },
        )

    def _latest_otp(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def _service_request(self):
        request = RequestFactory().post(self.url)
        request.user = self.tenant_admin
        request.scope = {
            "tenant_id": self.tenant_a["tenant"].id,
            "campus_id": self.tenant_a["campus"].id,
        }
        request.session = self.client.session
        return request

    def _download_response(self):
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()
        code = self._latest_otp()
        return self.client.post(
            self.url,
            {
                "action": "verify_otp",
                "challenge_token": challenge.token,
                "otp_code": code,
            },
        )

    def test_superadmin_and_tenant_admin_can_access_but_campus_and_faculty_are_denied(self):
        superadmin = User.objects.create_superuser(
            username="super-export",
            email="super-export@example.com",
            password="testpass123",
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        self.client.force_login(superadmin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

        self._login_tenant_admin()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TEN-A")
        self.assertNotContains(response, "TEN-B")

        self.client.force_login(self.campus_admin)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.faculty)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_tenant_admin_cannot_start_export_for_another_tenant(self):
        response = self._start_challenge(tenant=self.tenant_b["tenant"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(TenantDataExportChallenge.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_wrong_password_is_rejected_and_does_not_send_otp(self):
        response = self._start_challenge(password="wrong-password")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(TenantDataExportChallenge.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, "Password verification failed.")

    def test_correct_password_sends_hashed_otp_to_authenticated_admin_email(self):
        response = self._start_challenge()

        self.assertEqual(response.status_code, 200)
        challenge = TenantDataExportChallenge.objects.get()
        otp = self._latest_otp()
        self.assertEqual(challenge.sent_to_email, self.tenant_admin.email)
        self.assertNotEqual(challenge.otp_hash, otp)
        self.assertNotContains(response, otp)
        self.assertContains(response, "te**********@example.com")

    def test_missing_email_blocks_workflow(self):
        self.tenant_admin.email = ""
        self.tenant_admin.save(update_fields=["email"])

        response = self._start_challenge()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no registered email address")
        self.assertEqual(TenantDataExportChallenge.objects.count(), 0)

    def test_wrong_otp_fails_and_attempt_limit_locks_challenge(self):
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()

        for _index in range(5):
            response = self.client.post(
                self.url,
                {
                    "action": "verify_otp",
                    "challenge_token": challenge.token,
                    "otp_code": "000000",
                },
            )

        self.assertEqual(response.status_code, 200)
        challenge.refresh_from_db()
        self.assertEqual(challenge.failed_attempt_count, 5)
        self.assertEqual(challenge.status, TenantDataExportChallenge.Status.LOCKED)

    def test_resend_cooldown_and_new_code_invalidates_prior_code(self):
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()
        first_code = self._latest_otp()

        cooldown_response = self.client.post(
            self.url,
            {"action": "resend", "challenge_token": challenge.token},
        )
        self.assertContains(cooldown_response, "Please wait")

        challenge.last_sent_at = timezone.now() - timedelta(seconds=61)
        challenge.save(update_fields=["last_sent_at", "updated_at"])
        resend_response = self.client.post(
            self.url,
            {"action": "resend", "challenge_token": challenge.token},
        )
        self.assertEqual(resend_response.status_code, 200)
        second_code = self._latest_otp()
        self.assertNotEqual(first_code, second_code)

        old_response = self.client.post(
            self.url,
            {
                "action": "verify_otp",
                "challenge_token": challenge.token,
                "otp_code": first_code,
            },
        )
        self.assertContains(old_response, "verification code is incorrect")

    def test_max_resend_count_is_enforced(self):
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()

        for _index in range(3):
            challenge.last_sent_at = timezone.now() - timedelta(seconds=61)
            challenge.save(update_fields=["last_sent_at", "updated_at"])
            response = self.client.post(
                self.url,
                {"action": "resend", "challenge_token": challenge.token},
            )
            self.assertEqual(response.status_code, 200)
            challenge.refresh_from_db()

        challenge.last_sent_at = timezone.now() - timedelta(seconds=61)
        challenge.save(update_fields=["last_sent_at", "updated_at"])
        blocked_response = self.client.post(
            self.url,
            {"action": "resend", "challenge_token": challenge.token},
        )

        self.assertContains(blocked_response, "maximum number")
        challenge.refresh_from_db()
        self.assertEqual(challenge.resend_count, 3)

    def test_expired_otp_cannot_be_used(self):
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()
        challenge.otp_expires_at = timezone.now() - timedelta(minutes=1)
        challenge.save(update_fields=["otp_expires_at", "updated_at"])

        response = self.client.post(
            self.url,
            {
                "action": "verify_otp",
                "challenge_token": challenge.token,
                "otp_code": self._latest_otp(),
            },
        )

        self.assertContains(response, "verification code has expired")
        challenge.refresh_from_db()
        self.assertEqual(challenge.status, TenantDataExportChallenge.Status.EXPIRED)

    def test_verified_challenge_downloads_valid_sqlite_and_is_consumed_once(self):
        temp_dir = tempfile.gettempdir()
        before_temp_files = {
            os.path.join(temp_dir, name)
            for name in os.listdir(temp_dir)
            if name.startswith("teachermateplus_export_") and name.endswith(".sqlite3")
        }
        response = self._download_response()

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("teachermateplus_ten-a_", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])
        during_temp_files = {
            os.path.join(temp_dir, name)
            for name in os.listdir(temp_dir)
            if name.startswith("teachermateplus_export_") and name.endswith(".sqlite3")
        }
        created_temp_files = during_temp_files - before_temp_files
        self.assertEqual(len(created_temp_files), 1)
        content = b"".join(response.streaming_content)
        response.close()
        self.assertFalse(any(os.path.exists(path) for path in created_temp_files))
        self.assertTrue(content.startswith(b"SQLite format 3\x00"))

        sqlite_path = os.path.join(settings.BASE_DIR, "tenant-export-test.sqlite3")
        with open(sqlite_path, "wb") as handle:
            handle.write(content)
        try:
            connection = sqlite3.connect(sqlite_path)
            tenants = connection.execute("select code from tenants order by code").fetchall()
            courses = connection.execute("select code from courses order by code").fetchall()
            students = connection.execute("select student_no from students order by student_no").fetchall()
            passwords = connection.execute("select password from users").fetchall()
            integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
            manifest_tenant = connection.execute(
                "select value from tmp_export_manifest where key='selected_tenant_code'"
            ).fetchone()[0]
            row_count = connection.execute(
                "select row_count from tmp_export_row_counts where table_name='students'"
            ).fetchone()[0]
            table_names = {
                row[0]
                for row in connection.execute("select name from sqlite_master where type='table'").fetchall()
            }
            connection.close()
        finally:
            os.remove(sqlite_path)

        self.assertEqual(tenants, [("TEN-A",)])
        self.assertIn(("TEN-A-IT101",), courses)
        self.assertNotIn(("TEN-B-IT101",), courses)
        self.assertIn(("2026-A-001",), students)
        self.assertNotIn(("2026-B-001",), students)
        self.assertEqual(integrity_check, "ok")
        self.assertTrue(all(password == "!" and not is_password_usable(password) for (password,) in passwords))
        self.assertEqual(manifest_tenant, "TEN-A")
        self.assertEqual(row_count, 1)
        self.assertIn("grade_activities", table_names)
        self.assertNotIn("django_session", table_names)
        self.assertNotIn("login_otp_challenges", table_names)
        self.assertNotIn("tenant_data_export_challenges", table_names)
        self.assertNotIn("tenant_api_keys", table_names)

        challenge = TenantDataExportChallenge.objects.get()
        self.assertEqual(challenge.status, TenantDataExportChallenge.Status.CONSUMED)
        reuse = self.client.post(
            self.url,
            {
                "action": "verify_otp",
                "challenge_token": challenge.token,
                "otp_code": self._latest_otp(),
            },
        )
        self.assertEqual(reuse.status_code, 200)
        self.assertContains(reuse, "can no longer be used")

    def test_download_blocked_before_otp_verification_and_after_consumption(self):
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()
        request = self._service_request()

        with self.assertRaises(PermissionDenied):
            TenantSQLiteExportService.create_download_response(request=request, challenge=challenge)

        challenge.status = TenantDataExportChallenge.Status.OTP_VERIFIED
        challenge.otp_verified_at = timezone.now()
        challenge.save(update_fields=["status", "otp_verified_at", "updated_at"])
        response = TenantSQLiteExportService.create_download_response(request=request, challenge=challenge)
        _content = b"".join(response.streaming_content)
        response.close()

        challenge.refresh_from_db()
        self.assertEqual(challenge.status, TenantDataExportChallenge.Status.CONSUMED)
        with self.assertRaises(PermissionDenied):
            TenantSQLiteExportService.create_download_response(request=request, challenge=challenge)

    def test_generation_failure_consumes_challenge_and_does_not_leave_temp_file(self):
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()
        challenge.status = TenantDataExportChallenge.Status.OTP_VERIFIED
        challenge.otp_verified_at = timezone.now()
        challenge.save(update_fields=["status", "otp_verified_at", "updated_at"])
        temp_dir = tempfile.gettempdir()
        before_temp_files = {
            os.path.join(temp_dir, name)
            for name in os.listdir(temp_dir)
            if name.startswith("teachermateplus_export_") and name.endswith(".sqlite3")
        }

        with patch.object(TenantSQLiteExportService, "_write_tables", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                TenantSQLiteExportService.create_download_response(
                    request=self._service_request(),
                    challenge=challenge,
                )

        challenge.refresh_from_db()
        after_temp_files = {
            os.path.join(temp_dir, name)
            for name in os.listdir(temp_dir)
            if name.startswith("teachermateplus_export_") and name.endswith(".sqlite3")
        }
        self.assertEqual(challenge.status, TenantDataExportChallenge.Status.CONSUMED)
        self.assertEqual(after_temp_files, before_temp_files)
        self.assertTrue(
            AuditLog.objects.filter(
                action="TENANT_EXPORT_GENERATION_FAILED",
                entity_id=str(challenge.token),
            ).exists()
        )

    def test_safe_validation_failure_after_consumption_does_not_allow_retry(self):
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()
        challenge.status = TenantDataExportChallenge.Status.OTP_VERIFIED
        challenge.otp_verified_at = timezone.now()
        challenge.save(update_fields=["status", "otp_verified_at", "updated_at"])

        with patch.object(
            TenantSQLiteExportService,
            "_build_sqlite_file",
            side_effect=ValidationError("Export is too large."),
        ):
            with self.assertRaises(ValidationError):
                TenantSQLiteExportService.create_download_response(
                    request=self._service_request(),
                    challenge=challenge,
                )

        challenge.refresh_from_db()
        self.assertEqual(challenge.status, TenantDataExportChallenge.Status.CONSUMED)
        with self.assertRaises(PermissionDenied):
            TenantSQLiteExportService.create_download_response(
                request=self._service_request(),
                challenge=challenge,
            )

    def test_export_audit_events_do_not_store_password_or_otp_secret_values(self):
        self.client.force_login(self.tenant_admin)
        self.client.get(self.url)
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()
        otp = self._latest_otp()

        response = self.client.post(
            self.url,
            {
                "action": "verify_otp",
                "challenge_token": challenge.token,
                "otp_code": otp,
            },
        )
        _content = b"".join(response.streaming_content)
        response.close()

        actions = set(AuditLog.objects.values_list("action", flat=True))
        for action in {
            "TENANT_EXPORT_PAGE_ACCESSED",
            "TENANT_EXPORT_PASSWORD_SUCCESS",
            "TENANT_EXPORT_CHALLENGE_STARTED",
            "TENANT_EXPORT_OTP_SENT",
            "TENANT_EXPORT_OTP_SUCCESS",
            "TENANT_EXPORT_CHALLENGE_CONSUMED",
            "TENANT_EXPORT_GENERATION_STARTED",
            "TENANT_EXPORT_GENERATION_COMPLETED",
            "TENANT_EXPORT_DOWNLOAD_INITIATED",
        }:
            self.assertIn(action, actions)

        challenge.refresh_from_db()
        audit_payload = json.dumps(
            list(AuditLog.objects.values("before_json", "after_json", "metadata_json")),
            default=str,
        )
        self.assertNotIn("testpass123", audit_payload)
        self.assertNotIn(otp, audit_payload)
        self.assertNotIn(challenge.otp_hash, audit_payload)

    def test_challenge_cannot_be_used_by_another_admin(self):
        self._start_challenge()
        challenge = TenantDataExportChallenge.objects.get()
        other_admin = User.objects.create_user(
            username="other-admin",
            email="other-admin@example.com",
            password="testpass123",
            default_tenant=self.tenant_a["tenant"],
        )
        UserRole.objects.create(user=other_admin, role=self.tenant_admin_role, tenant=self.tenant_a["tenant"])
        self.client.force_login(other_admin)

        response = self.client.post(
            self.url,
            {
                "action": "verify_otp",
                "challenge_token": challenge.token,
                "otp_code": self._latest_otp(),
            },
        )

        self.assertEqual(response.status_code, 403)
