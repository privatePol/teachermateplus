from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
import uuid

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.exit_pulse.forms import ExitPulseCreateForm
from apps.exit_pulse.models import ExitPulseResponse, ExitPulseSession
from apps.exit_pulse.services import (
    ExitPulseAnalyticsService,
    ExitPulseDuplicateResponse,
    ExitPulseEnrollmentVerificationService,
    ExitPulseHistoryService,
    ExitPulseQuestionValidationService,
    ExitPulseResponseService,
    ExitPulseSessionService,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class ExitPulseTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="PULSE", name="Pulse College")
        cls.campus = Campus.objects.create(tenant=cls.tenant, code="MAIN", name="Main Campus")
        cls.department = Department.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            code="IT",
            name="Information Technology",
        )
        cls.program = Program.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            code="BSIT",
            name="BS Information Technology",
        )
        cls.academic_year = AcademicYear.objects.create(
            tenant=cls.tenant,
            code="2026-2027",
            name="AY 2026-2027",
            start_date=date(2026, 6, 1),
            end_date=date(2027, 5, 31),
        )
        cls.term = Term.objects.create(
            tenant=cls.tenant,
            academic_year=cls.academic_year,
            code="1ST",
            name="First Semester",
        )
        cls.course = Course.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            code="IT101",
            title="Introduction to Computing",
        )
        cls.section = Section.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            program=cls.program,
            code="BSIT-1A",
            name="BSIT 1A",
        )
        cls.offering = CourseOffering.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            program=cls.program,
            academic_year=cls.academic_year,
            term=cls.term,
            course=cls.course,
            section=cls.section,
            status=CourseOffering.Status.OPEN,
        )
        consent_version = settings.PRIVACY_CONSENT_VERSION
        consent_at = timezone.now()
        cls.faculty = User.objects.create_user(
            username="pulse-faculty",
            email="pulse-faculty@example.com",
            password="StrongPass123!",
            default_tenant=cls.tenant,
            default_campus=cls.campus,
            default_department=cls.department,
            privacy_consent_version=consent_version,
            privacy_consent_at=consent_at,
        )
        cls.other_faculty = User.objects.create_user(
            username="other-faculty",
            email="other-faculty@example.com",
            password="StrongPass123!",
            default_tenant=cls.tenant,
            default_campus=cls.campus,
            default_department=cls.department,
            privacy_consent_version=consent_version,
            privacy_consent_at=consent_at,
        )
        cls.faculty_role = Role.objects.create(code="FACULTY", name="Faculty")
        for code, module in (("faculty_portal.access", "faculty_portal"), ("exit_pulse.use", "exit_pulse")):
            permission, _ = Permission.objects.get_or_create(
                code=code,
                defaults={"module": module, "action": "use"},
            )
            RolePermission.objects.get_or_create(role=cls.faculty_role, permission=permission)
        for user in (cls.faculty, cls.other_faculty):
            UserRole.objects.create(
                user=user,
                role=cls.faculty_role,
                tenant=cls.tenant,
                campus=cls.campus,
                department=cls.department,
            )
        accepted_at = timezone.now()
        cls.assignment = FacultyAssignment.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            offering=cls.offering,
            faculty_user=cls.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=accepted_at,
            accepted_by=cls.faculty,
        )

    def setUp(self):
        self.client.force_login(self.faculty)

    def make_draft(self, **overrides):
        defaults = {
            "user": self.faculty,
            "assignment": self.assignment,
            "topic": "Database Normalization",
            "question_code": ExitPulseSession.QuestionCode.UNDERSTANDING,
            "tenant_id": self.tenant.id,
            "campus_id": self.campus.id,
        }
        defaults.update(overrides)
        return ExitPulseSessionService.create_draft(**defaults)

    def make_live(self, **overrides):
        draft = self.make_draft(**overrides)
        return ExitPulseSessionService.start(session=draft, user=self.faculty)

    def create_enrollments(self, count):
        for index in range(count):
            student = Student.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                department=self.department,
                program=self.program,
                student_no=f"P{index:04d}",
                last_name=f"Student{index}",
                first_name="Test",
            )
            Enrollment.objects.create(
                tenant=self.tenant,
                campus=self.campus,
                academic_year=self.academic_year,
                term=self.term,
                student=student,
                course_offering=self.offering,
            )

    def eligible_enrollment_for_session(self, session, *, suffix="PRIMARY"):
        enrollment = Enrollment.objects.filter(
            course_offering=session.course_offering,
            is_active=True,
            enrollment_status=Enrollment.Status.ACTIVE,
            student__student_no=f"VERIFY-{session.id}-{suffix}",
        ).first()
        if enrollment:
            return enrollment
        student = Student.objects.create(
            tenant=session.tenant,
            campus=session.campus,
            department=self.department,
            program=self.program,
            student_no=f"VERIFY-{session.id}-{suffix}",
            last_name=f"IdentitySecret{suffix}",
            first_name="IdentitySecretFirst",
        )
        return Enrollment.objects.create(
            tenant=session.tenant,
            campus=session.campus,
            academic_year=session.academic_year,
            term=session.term,
            student=student,
            course_offering=session.course_offering,
        )

    def submit_as_anonymous(
        self,
        session,
        *,
        response_code="CONFIDENT",
        data=None,
        client=None,
        ip="10.0.0.5",
        enrollment=None,
    ):
        public_client = client or Client()
        enrollment = enrollment or self.eligible_enrollment_for_session(session)
        _, survey = self.open_public_survey(
            session,
            client=public_client,
            ip=ip,
            enrollment=enrollment,
        )
        if survey.status_code != 200 or survey.context.get("pulse_state") != "live":
            return public_client, survey
        payload = {"response_code": response_code}
        payload.update(data or {})
        response = public_client.post(reverse("exit_pulse:public_submit"), payload, REMOTE_ADDR=ip)
        return public_client, response

    def open_public_survey(
        self,
        session,
        *,
        client=None,
        ip="10.0.0.5",
        enrollment=None,
        verify=True,
    ):
        public_client = client or Client()
        public_client.get(reverse("exit_pulse:public_survey"), REMOTE_ADDR=ip)
        response = public_client.post(
            reverse("exit_pulse:public_open"),
            {"public_token": session.public_token},
            REMOTE_ADDR=ip,
        )
        if verify and response.status_code == 200 and response.context.get("pulse_state") == "verify":
            enrollment = enrollment or self.eligible_enrollment_for_session(session)
            verified = public_client.post(
                reverse("exit_pulse:public_verify"),
                {
                    "public_token": session.public_token,
                    "student_number": enrollment.student.student_no,
                },
                REMOTE_ADDR=ip,
            )
            if verified.status_code == 302:
                response = public_client.get(verified.url, REMOTE_ADDR=ip)
            else:
                response = verified
        return public_client, response


class ExitPulseAuthorizationAndCreationTests(ExitPulseTestBase):
    def test_assigned_faculty_can_create_and_question_snapshot_is_stored(self):
        response = self.client.post(
            reverse("exit_pulse:create"),
            {
                "faculty_assignment": self.assignment.id,
                "topic": "Relational databases",
                "question_code": "UNDERSTANDING",
            },
        )
        session = ExitPulseSession.objects.get()
        self.assertRedirects(response, reverse("exit_pulse:live", kwargs={"public_id": session.public_id}))
        self.assertEqual(session.status, ExitPulseSession.Status.LIVE)
        self.assertEqual(session.question_text_snapshot, "How well do you understand today’s topic?")
        self.assertEqual(session.tenant_id, self.tenant.id)
        self.assertEqual(session.campus_id, self.campus.id)
        self.assertEqual(session.course_offering_id, self.offering.id)
        self.assertTrue(AuditLog.objects.filter(action="EXIT_PULSE_CREATED").exists())
        self.assertTrue(AuditLog.objects.filter(action="EXIT_PULSE_STARTED").exists())

    def test_unassigned_faculty_cannot_create_for_another_assignment(self):
        other_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.other_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.other_faculty,
        )
        response = self.client.post(
            reverse("exit_pulse:create"),
            {
                "faculty_assignment": other_assignment.id,
                "topic": "Security",
                "question_code": "UNDERSTANDING",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(ExitPulseSession.objects.exists())

    def test_inactive_assignment_is_rejected(self):
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        response = self.client.post(
            reverse("exit_pulse:create"),
            {
                "faculty_assignment": self.assignment.id,
                "topic": "Security",
                "question_code": "UNDERSTANDING",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ExitPulseSession.objects.exists())

    def test_other_faculty_cannot_access_live_or_results(self):
        session = self.make_live()
        self.client.force_login(self.other_faculty)
        self.assertEqual(reverse("exit_pulse:live", kwargs={"public_id": session.public_id}), f"/faculty/exit-pulse/{session.public_id}/live/")
        self.assertEqual(self.client.get(reverse("exit_pulse:live", kwargs={"public_id": session.public_id})).status_code, 404)
        self.assertEqual(self.client.get(reverse("exit_pulse:results", kwargs={"public_id": session.public_id})).status_code, 404)

    def test_current_tenant_and_campus_scope_are_enforced(self):
        other_tenant = Tenant.objects.create(code="OTHER", name="Other Tenant")
        other_campus = Campus.objects.create(tenant=other_tenant, code="OTHER", name="Other Campus")
        self.faculty.default_tenant = other_tenant
        self.faculty.default_campus = other_campus
        self.faculty.save(update_fields=["default_tenant", "default_campus", "updated_at"])
        UserRole.objects.create(user=self.faculty, role=self.faculty_role, tenant=other_tenant, campus=other_campus)
        self.client.force_login(self.faculty)
        response = self.client.get(reverse("exit_pulse:create"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].fields["faculty_assignment"].queryset.exists())

    def test_tenant_feature_switch_blocks_faculty_and_public_access(self):
        session = self.make_live()
        SystemSettingService.set(
            FeatureSettingsService.EXIT_PULSE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.assertEqual(self.client.get(reverse("exit_pulse:landing")).status_code, 403)
        _, public_response = self.open_public_survey(session)
        self.assertEqual(public_response.status_code, 200)
        self.assertContains(public_response, "Exit Pulse is currently unavailable.")


class ExitPulseQuestionValidationTests(ExitPulseTestBase):
    def test_three_predefined_questions_work(self):
        for code in ("UNDERSTANDING", "APPLICATION_CONFIDENCE", "NEEDS_EXPLANATION"):
            form = ExitPulseCreateForm(
                data={"faculty_assignment": self.assignment.id, "topic": "Topic", "question_code": code},
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
            )
            self.assertTrue(form.is_valid(), form.errors)
            self.assertTrue(form.question_snapshot)

    def test_safe_custom_question_works(self):
        question = "Which lesson concept needs another example?"
        self.assertEqual(ExitPulseQuestionValidationService.validate_custom_question(question), question)

    def test_blank_overlength_and_faculty_rating_questions_fail(self):
        for question in ("", "x" * 251, "How would you rate your instructor?", "Was my discussion boring?"):
            with self.assertRaises(ValidationError):
                ExitPulseQuestionValidationService.validate_custom_question(question)

    def test_rejected_custom_wording_is_not_saved(self):
        response = self.client.post(
            reverse("exit_pulse:create"),
            {
                "faculty_assignment": self.assignment.id,
                "topic": "Topic",
                "question_code": "CUSTOM",
                "custom_question": "Rate my teaching.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please revise the question")
        self.assertFalse(ExitPulseSession.objects.exists())


class ExitPulseLifecycleTests(ExitPulseTestBase):
    def test_draft_starts_with_five_minute_expiration(self):
        draft = self.make_draft()
        self.assertEqual(draft.status, ExitPulseSession.Status.DRAFT)
        self.assertIsNone(draft.enrollment_count_snapshot)
        started = ExitPulseSessionService.start(session=draft, user=self.faculty)
        self.assertEqual(started.status, ExitPulseSession.Status.LIVE)
        self.assertAlmostEqual((started.expires_at - started.started_at).total_seconds(), 300, delta=1)

    def test_start_captures_only_active_eligible_enrollments(self):
        self.create_enrollments(3)
        enrollments = list(Enrollment.objects.order_by("id"))
        enrollments[0].enrollment_status = Enrollment.Status.DRP
        enrollments[0].save(update_fields=["enrollment_status", "updated_at"])
        enrollments[1].is_active = False
        enrollments[1].save(update_fields=["is_active", "updated_at"])

        draft = self.make_draft()
        self.assertIsNone(draft.enrollment_count_snapshot)
        started = ExitPulseSessionService.start(session=draft, user=self.faculty)

        self.assertEqual(started.enrollment_count_snapshot, 1)

    def test_zero_enrollment_snapshot_is_stored_as_zero(self):
        started = self.make_live()
        self.assertEqual(started.enrollment_count_snapshot, 0)
        self.assertIsNotNone(started.enrollment_count_snapshot)

    def test_started_snapshot_is_not_recalculated_after_enrollment_changes(self):
        self.create_enrollments(2)
        started = self.make_live()
        Enrollment.objects.update(
            is_active=False,
            enrollment_status=Enrollment.Status.W,
        )

        with self.assertRaises(ValidationError):
            ExitPulseSessionService.start(session=started, user=self.faculty)

        started.refresh_from_db()
        self.assertEqual(started.enrollment_count_snapshot, 2)
        analytics = ExitPulseAnalyticsService.build(started)
        self.assertEqual(analytics.enrolled_students, 2)
        self.assertTrue(analytics.enrollment_denominator_is_historical)

    def test_close_and_cancel_stop_session(self):
        closed = ExitPulseSessionService.close(session=self.make_live(), user=self.faculty)
        self.assertEqual(closed.status, ExitPulseSession.Status.CLOSED)
        cancelled = ExitPulseSessionService.cancel(session=self.make_live(), user=self.faculty)
        self.assertEqual(cancelled.status, ExitPulseSession.Status.CANCELLED)

    def test_extension_adds_five_minutes_only_once(self):
        session = self.make_live()
        original_expiry = session.expires_at
        extended = ExitPulseSessionService.extend(session=session, user=self.faculty)
        self.assertEqual(extended.expires_at, original_expiry + timedelta(minutes=5))
        self.assertEqual(extended.extension_count, 1)
        with self.assertRaises(ValidationError):
            ExitPulseSessionService.extend(session=extended, user=self.faculty)

    def test_server_side_expiration_is_enforced(self):
        session = self.make_live()
        ExitPulseSession.objects.filter(pk=session.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        session.refresh_from_db()
        expected_end = session.expires_at
        ExitPulseSessionService.refresh_effective_status(session)
        self.assertEqual(session.status, ExitPulseSession.Status.EXPIRED)
        self.assertEqual(session.closed_at, expected_end)
        with self.assertRaises(ValidationError):
            ExitPulseResponseService.submit(
                session=session,
                response_code="CONFIDENT",
                browser_hash="a" * 64,
            )

    def test_owner_only_lifecycle_action(self):
        with self.assertRaises(PermissionDenied):
            ExitPulseSessionService.close(session=self.make_live(), user=self.other_faculty)

    def test_assignment_deactivation_auto_cancels_live_session_and_blocks_extension(self):
        session = self.make_live()
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])

        updated = ExitPulseSessionService.extend(session=session, user=self.faculty)
        self.assertEqual(updated.status, ExitPulseSession.Status.CANCELLED)
        session.refresh_from_db()
        self.assertEqual(session.status, ExitPulseSession.Status.CANCELLED)
        self.assertIsNotNone(session.cancelled_at)
        self.assertTrue(AuditLog.objects.filter(action="EXIT_PULSE_AUTO_CANCELLED").exists())

        _, public_response = self.open_public_survey(session)
        self.assertContains(public_response, "This Exit Pulse was cancelled.", status_code=200)
        self.assertFalse(ExitPulseResponse.objects.exists())


class ExitPulseAnonymousResponseTests(ExitPulseTestBase):
    def test_no_login_required_and_valid_reaction_is_accepted(self):
        session = self.make_live()
        public_client, response = self.submit_as_anonymous(session)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ExitPulseResponse.objects.get().response_code, "CONFIDENT")
        success = public_client.get(response.url)
        self.assertContains(success, "Thank you. Your response has been recorded.")

    def test_invalid_reaction_and_overlength_feedback_are_rejected(self):
        session = self.make_live(allow_written_feedback=True, feedback_review_enabled=True)
        _, invalid = self.submit_as_anonymous(session, response_code="ANGRY")
        self.assertEqual(invalid.status_code, 400)
        _, overlength = self.submit_as_anonymous(
            session,
            client=Client(),
            data={"feedback_review": "x" * 201},
        )
        self.assertEqual(overlength.status_code, 400)
        self.assertFalse(ExitPulseResponse.objects.exists())

    def test_same_enrollment_is_rejected_across_browsers_and_another_student_is_allowed(self):
        session = self.make_live()
        first_enrollment = self.eligible_enrollment_for_session(session)
        client_one, first = self.submit_as_anonymous(session, ip="10.1.1.1")
        self.assertEqual(first.status_code, 302)
        _, duplicate = self.submit_as_anonymous(
            session,
            client=Client(),
            ip="10.1.1.1",
            enrollment=first_enrollment,
        )
        self.assertEqual(duplicate.status_code, 409)
        second_enrollment = self.eligible_enrollment_for_session(session, suffix="SECOND")
        _, second_student = self.submit_as_anonymous(
            session,
            client=client_one,
            ip="10.1.1.1",
            enrollment=second_enrollment,
        )
        self.assertEqual(second_student.status_code, 302)
        self.assertEqual(session.responses.count(), 2)
        another_session = self.make_live(topic="Another confidential check")
        _, another_response = self.submit_as_anonymous(
            another_session,
            client=Client(),
            enrollment=first_enrollment,
        )
        self.assertEqual(another_response.status_code, 302)

    def test_database_constraint_safely_rejects_duplicate_enrollment(self):
        session = self.make_live()
        enrollment = self.eligible_enrollment_for_session(session)
        notice_at = timezone.now()
        ExitPulseResponse.objects.create(
            session=session,
            student_enrollment=enrollment,
            privacy_notice_version=ExitPulseEnrollmentVerificationService.PRIVACY_NOTICE_VERSION,
            privacy_notice_acknowledged_at=notice_at,
            response_code="CONFIDENT",
            anonymous_token_hash="d" * 64,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExitPulseResponse.objects.create(
                    session=session,
                    student_enrollment=enrollment,
                    privacy_notice_version=(
                        ExitPulseEnrollmentVerificationService.PRIVACY_NOTICE_VERSION
                    ),
                    privacy_notice_acknowledged_at=notice_at,
                    response_code="NEEDS_PRACTICE",
                    anonymous_token_hash="e" * 64,
                )

    def test_database_constraint_rejects_unexpected_response_code(self):
        session = self.make_live()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExitPulseResponse.objects.create(
                    session=session,
                    response_code="UNEXPECTED",
                    anonymous_token_hash="u" * 64,
                )

    def test_service_converts_insert_race_to_safe_duplicate_error(self):
        session = self.make_live()
        enrollment = self.eligible_enrollment_for_session(session)
        with patch(
            "apps.exit_pulse.services.ExitPulseResponse.objects.create",
            side_effect=IntegrityError("simulated duplicate race"),
        ):
            with self.assertRaises(ExitPulseDuplicateResponse):
                ExitPulseResponseService.submit(
                    session=session,
                    response_code="CONFIDENT",
                    browser_hash="r" * 64,
                    enrollment=enrollment,
                    privacy_notice_version=(
                        ExitPulseEnrollmentVerificationService.PRIVACY_NOTICE_VERSION
                    ),
                    privacy_notice_acknowledged_at=timezone.now(),
                )

    def test_malformed_token_and_get_to_submit_are_handled_safely(self):
        malformed = self.client.post(reverse("exit_pulse:public_open"), {"public_token": "bad"})
        self.assertEqual(malformed.status_code, 404)
        get_submit = self.client.get(reverse("exit_pulse:public_submit"))
        self.assertEqual(get_submit.status_code, 405)
        self.assertEqual(self.client.get(reverse("exit_pulse:public_verify")).status_code, 405)
        self.assertEqual(self.client.post(reverse("exit_pulse:public_response")).status_code, 405)

    def test_public_token_uses_fragment_and_is_not_sent_in_request_path(self):
        session = self.make_live()
        live = self.client.get(reverse("exit_pulse:live", kwargs={"public_id": session.public_id}))
        expected_url = f"http://testserver{reverse('exit_pulse:public_survey')}#{session.public_token}"
        self.assertContains(live, expected_url)
        self.assertNotContains(live, f"/pulse/{session.public_token}/")
        entry = Client().get(reverse("exit_pulse:public_survey"))
        self.assertNotIn(session.public_token, entry.request["PATH_INFO"])

    def test_closed_and_cancelled_sessions_reject_submissions(self):
        for status in (ExitPulseSession.Status.CLOSED, ExitPulseSession.Status.CANCELLED):
            session = self.make_live()
            ExitPulseSession.objects.filter(pk=session.pk).update(status=status, closed_at=timezone.now())
            session.refresh_from_db()
            _, response = self.submit_as_anonymous(session, client=Client())
            self.assertEqual(response.status_code, 200)
            self.assertFalse(ExitPulseResponse.objects.filter(session=session).exists())

    def test_public_post_uses_normal_csrf_protection(self):
        session = self.make_live()
        csrf_client = Client(enforce_csrf_checks=True)
        submit_url = reverse("exit_pulse:public_submit")
        self.assertEqual(
            csrf_client.post(
                submit_url,
                {"public_token": session.public_token, "response_code": "CONFIDENT"},
            ).status_code,
            403,
        )
        entry = csrf_client.get(reverse("exit_pulse:public_survey"))
        self.assertEqual(entry["Referrer-Policy"], "same-origin")
        token = csrf_client.cookies["csrftoken"].value
        null_origin = csrf_client.post(
            reverse("exit_pulse:public_open"),
            {"public_token": session.public_token, "csrfmiddlewaretoken": token},
            HTTP_ORIGIN="null",
        )
        self.assertEqual(null_origin.status_code, 403)
        opened = csrf_client.post(
            reverse("exit_pulse:public_open"),
            {"public_token": session.public_token, "csrfmiddlewaretoken": token},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(opened.status_code, 200)
        enrollment = self.eligible_enrollment_for_session(session)
        verified = csrf_client.post(
            reverse("exit_pulse:public_verify"),
            {
                "public_token": session.public_token,
                "student_number": enrollment.student.student_no,
                "csrfmiddlewaretoken": token,
            },
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(verified.status_code, 302)
        csrf_client.get(verified.url)
        accepted = csrf_client.post(
            submit_url,
            {
                "response_code": "CONFIDENT",
                "csrfmiddlewaretoken": token,
            },
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(accepted.status_code, 302)

    def test_identifier_can_be_anonymized_after_24_hours(self):
        session = self.make_live()
        response = ExitPulseResponse.objects.create(
            session=session,
            response_code="CONFIDENT",
            anonymous_token_hash="z" * 64,
            technical_identifier_expires_at=timezone.now() - timedelta(seconds=1),
        )
        ExitPulseResponseService.anonymize_expired_identifiers(session_id=session.id)
        response.refresh_from_db()
        self.assertIsNone(response.anonymous_token_hash)
        self.assertIsNone(response.technical_identifier_expires_at)

    def test_cleanup_command_is_idempotent_supports_dry_run_and_preserves_response_data(self):
        session = self.make_live(allow_written_feedback=True, feedback_review_enabled=True)
        response = ExitPulseResponse.objects.create(
            session=session,
            response_code="NEEDS_CLARIFICATION",
            feedback_review="Review joins",
            anonymous_token_hash="c" * 64,
            technical_identifier_expires_at=timezone.now() - timedelta(seconds=1),
        )
        output = StringIO()
        call_command(
            "anonymize_exit_pulse_identifiers",
            "--tenant-id",
            str(self.tenant.id),
            "--dry-run",
            stdout=output,
        )
        self.assertIn("Would anonymize Exit Pulse technical identifiers: 1", output.getvalue())
        response.refresh_from_db()
        self.assertEqual(response.anonymous_token_hash, "c" * 64)

        output = StringIO()
        call_command(
            "anonymize_exit_pulse_identifiers",
            "--tenant-id",
            str(self.tenant.id),
            stdout=output,
        )
        self.assertIn("Anonymized Exit Pulse technical identifiers: 1", output.getvalue())
        response.refresh_from_db()
        self.assertIsNone(response.anonymous_token_hash)
        self.assertIsNone(response.technical_identifier_expires_at)
        self.assertEqual(response.response_code, "NEEDS_CLARIFICATION")
        self.assertEqual(response.feedback_review, "Review joins")

        output = StringIO()
        call_command("anonymize_exit_pulse_identifiers", stdout=output)
        self.assertIn("Anonymized Exit Pulse technical identifiers: 0", output.getvalue())


class ExitPulseIdentityValidationTests(ExitPulseTestBase):
    def _verify(self, session, enrollment, *, client=None, student_number=None):
        public_client = client or Client()
        self.open_public_survey(session, client=public_client, verify=False)
        return public_client, public_client.post(
            reverse("exit_pulse:public_verify"),
            {
                "public_token": session.public_token,
                "student_number": (
                    student_number
                    if student_number is not None
                    else enrollment.student.student_no
                ),
                "student_enrollment": enrollment.id,
            },
        )

    def _other_scope_enrollment(self, code, *, tenant=None, campus=None):
        tenant = tenant or self.tenant
        campus = campus or Campus.objects.create(
            tenant=tenant,
            code=f"{code}C",
            name=f"{code} Campus",
        )
        department = Department.objects.create(
            tenant=tenant,
            campus=campus,
            code=f"{code}D",
            name=f"{code} Department",
        )
        program = Program.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            code=f"{code}P",
            name=f"{code} Program",
        )
        academic_year = AcademicYear.objects.create(
            tenant=tenant,
            code=f"{code}-AY",
            name=f"{code} Academic Year",
            start_date=date(2030, 1, 1),
            end_date=date(2030, 12, 31),
        )
        term = Term.objects.create(
            tenant=tenant,
            academic_year=academic_year,
            code=f"{code}T",
            name=f"{code} Term",
        )
        course = Course.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            code=f"{code}101",
            title=f"{code} Course",
        )
        section = Section.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            code=f"{code}S",
            name=f"{code} Section",
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
            status=CourseOffering.Status.OPEN,
        )
        student = Student.objects.create(
            tenant=tenant,
            campus=campus,
            department=department,
            program=program,
            student_no=f"{code}-STUDENT",
            last_name=f"{code}Last",
            first_name=f"{code}First",
        )
        return Enrollment.objects.create(
            tenant=tenant,
            campus=campus,
            academic_year=academic_year,
            term=term,
            student=student,
            course_offering=offering,
        )

    def test_notice_is_visible_and_entering_student_number_is_the_consent_action(self):
        session = self.make_live()
        _, response = self.open_public_survey(session, verify=False)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pulse_state"], "verify")
        self.assertContains(
            response,
            "By entering your student number, you understand that it will be used to verify",
        )
        self.assertContains(response, "Submitting your student number confirms")
        self.assertContains(response, 'name="student_number"', html=False)
        self.assertNotContains(response, 'type="checkbox"', html=False)
        investigation_permission = Permission.objects.get(
            code="exit_pulse.response_identity_investigate"
        )
        self.assertFalse(
            RolePermission.objects.filter(permission=investigation_permission).exists()
        )

    def test_valid_enrollment_whitespace_and_case_store_server_controlled_identity_and_notice(self):
        session = self.make_live()
        enrollment = self.eligible_enrollment_for_session(session)
        public_client, verified = self._verify(
            session,
            enrollment,
            student_number=f"  {enrollment.student.student_no.lower()}  ",
        )
        self.assertRedirects(verified, reverse("exit_pulse:public_response"))
        response_form = public_client.get(verified.url)
        self.assertContains(response_form, session.question_text_snapshot)
        self.assertNotContains(response_form, enrollment.student.student_no)

        other_enrollment = self.eligible_enrollment_for_session(session, suffix="FORGED")
        submitted = public_client.post(
            reverse("exit_pulse:public_submit"),
            {
                "response_code": ExitPulseResponse.ResponseCode.CONFIDENT,
                "student_enrollment": other_enrollment.id,
                "student_number": other_enrollment.student.student_no,
            },
        )
        self.assertEqual(submitted.status_code, 302)
        stored = ExitPulseResponse.objects.get()
        self.assertEqual(stored.student_enrollment, enrollment)
        self.assertEqual(
            stored.privacy_notice_version,
            ExitPulseEnrollmentVerificationService.PRIVACY_NOTICE_VERSION,
        )
        self.assertIsNotNone(stored.privacy_notice_acknowledged_at)
        stored.student_enrollment = other_enrollment
        with self.assertRaises(ValidationError):
            stored.save()

    def test_unknown_other_class_cross_campus_and_cross_tenant_numbers_fail_generically(self):
        session = self.make_live()
        other_class = self._other_scope_enrollment("OTHERCLASS", campus=self.campus)
        cross_campus = self._other_scope_enrollment("OTHERCAMPUS")
        other_tenant = Tenant.objects.create(code="IDTENANT", name="Identity Other Tenant")
        cross_tenant = self._other_scope_enrollment("OTHERTENANT", tenant=other_tenant)
        expected = ExitPulseEnrollmentVerificationService.VERIFICATION_ERROR

        for student_number in (
            "DOES-NOT-EXIST",
            other_class.student.student_no,
            cross_campus.student.student_no,
            cross_tenant.student.student_no,
        ):
            _, response = self._verify(
                session,
                self.eligible_enrollment_for_session(session),
                student_number=student_number,
            )
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, expected, status_code=400)
            self.assertNotContains(response, student_number, status_code=400)
            self.assertNotContains(
                response,
                other_class.student.first_name,
                status_code=400,
            )

    def test_inactive_and_withdrawn_enrollments_are_denied(self):
        session = self.make_live()
        for index, updates in enumerate(
            (
                {"is_active": False},
                {"enrollment_status": Enrollment.Status.W},
            )
        ):
            enrollment = self.eligible_enrollment_for_session(session, suffix=f"INACTIVE{index}")
            Enrollment.objects.filter(pk=enrollment.pk).update(**updates)
            _, response = self._verify(session, enrollment)
            self.assertEqual(response.status_code, 400)
            self.assertContains(
                response,
                ExitPulseEnrollmentVerificationService.VERIFICATION_ERROR,
                status_code=400,
            )

    def test_direct_forged_expired_and_cross_session_verification_state_are_denied(self):
        session = self.make_live()
        enrollment = self.eligible_enrollment_for_session(session)
        direct = Client().post(
            reverse("exit_pulse:public_submit"),
            {"response_code": ExitPulseResponse.ResponseCode.CONFIDENT},
        )
        self.assertEqual(direct.status_code, 403)

        public_client, verified = self._verify(session, enrollment)
        self.assertEqual(verified.status_code, 302)
        state = public_client.session
        payload = dict(state[ExitPulseEnrollmentVerificationService.STATE_KEY])
        payload["browser_hash"] = "forged"
        state[ExitPulseEnrollmentVerificationService.STATE_KEY] = payload
        state.save()
        self.assertEqual(
            public_client.get(reverse("exit_pulse:public_response")).status_code,
            403,
        )

        public_client, verified = self._verify(session, enrollment, client=Client())
        state = public_client.session
        payload = dict(state[ExitPulseEnrollmentVerificationService.STATE_KEY])
        payload["privacy_notice_acknowledged_at"] = (
            timezone.now() - timedelta(minutes=11)
        ).isoformat()
        state[ExitPulseEnrollmentVerificationService.STATE_KEY] = payload
        state.save()
        self.assertEqual(
            public_client.get(reverse("exit_pulse:public_response")).status_code,
            403,
        )

        other_session = self.make_live(topic="Different verification session")
        public_client, verified = self._verify(session, enrollment, client=Client())
        state = public_client.session
        payload = dict(state[ExitPulseEnrollmentVerificationService.STATE_KEY])
        payload["session_public_id"] = str(other_session.public_id)
        state[ExitPulseEnrollmentVerificationService.STATE_KEY] = payload
        state.save()
        self.assertEqual(
            public_client.get(reverse("exit_pulse:public_response")).status_code,
            403,
        )

    def test_submission_service_rejects_stale_privacy_notice_evidence(self):
        session = self.make_live()
        enrollment = self.eligible_enrollment_for_session(session)

        with self.assertRaises(ValidationError):
            ExitPulseResponseService.submit(
                session=session,
                enrollment=enrollment,
                privacy_notice_version=(
                    ExitPulseEnrollmentVerificationService.PRIVACY_NOTICE_VERSION
                ),
                privacy_notice_acknowledged_at=timezone.now() - timedelta(minutes=11),
                response_code=ExitPulseResponse.ResponseCode.CONFIDENT,
                browser_hash="s" * 64,
            )

        self.assertFalse(ExitPulseResponse.objects.exists())

    def test_feature_disabled_after_verification_blocks_response_page_and_submission(self):
        session = self.make_live()
        enrollment = self.eligible_enrollment_for_session(session)
        page_client, page_verified = self._verify(session, enrollment)
        submit_client, submit_verified = self._verify(session, enrollment, client=Client())
        self.assertEqual(page_verified.status_code, 302)
        self.assertEqual(submit_verified.status_code, 302)
        SystemSettingService.set(
            FeatureSettingsService.EXIT_PULSE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )

        response_page = page_client.get(reverse("exit_pulse:public_response"))
        submission = submit_client.post(
            reverse("exit_pulse:public_submit"),
            {"response_code": ExitPulseResponse.ResponseCode.CONFIDENT},
        )

        self.assertEqual(response_page.status_code, 403)
        self.assertContains(
            response_page,
            "Exit Pulse is currently unavailable.",
            status_code=403,
        )
        self.assertEqual(submission.status_code, 403)
        self.assertFalse(ExitPulseResponse.objects.exists())

    def test_identity_remains_linked_after_enrollment_and_assignment_changes(self):
        session = self.make_live()
        enrollment = self.eligible_enrollment_for_session(session)
        self.submit_as_anonymous(session, enrollment=enrollment)
        Enrollment.objects.filter(pk=enrollment.pk).update(
            is_active=False,
            enrollment_status=Enrollment.Status.W,
        )
        FacultyAssignment.objects.filter(pk=self.assignment.pk).update(is_active=False)

        response = ExitPulseResponse.objects.get()
        self.assertEqual(response.student_enrollment_id, enrollment.id)
        self.assertEqual(response.student_enrollment.student_id, enrollment.student_id)

    def test_legacy_response_remains_valid_without_identity_or_notice_evidence(self):
        session = self.make_live()
        response = ExitPulseResponse.objects.create(
            session=session,
            response_code=ExitPulseResponse.ResponseCode.CONFIDENT,
        )

        self.assertIsNone(response.student_enrollment_id)
        self.assertEqual(response.privacy_notice_version, "")
        self.assertIsNone(response.privacy_notice_acknowledged_at)

    def test_student_identity_is_absent_from_all_routine_faculty_pages(self):
        session = self.make_live(
            allow_written_feedback=True,
            feedback_review_enabled=True,
        )
        enrollment = self.eligible_enrollment_for_session(session)
        confidential_feedback = "Please revisit normalization examples."
        self.submit_as_anonymous(
            session,
            enrollment=enrollment,
            data={"feedback_review": confidential_feedback},
        )
        live = self.client.get(
            reverse("exit_pulse:live", kwargs={"public_id": session.public_id})
        )
        ExitPulseSessionService.close(session=session, user=self.faculty)
        pages = (
            live,
            self.client.get(
                reverse("exit_pulse:results", kwargs={"public_id": session.public_id})
            ),
            self.client.get(reverse("exit_pulse:landing")),
            self.client.get(
                reverse(
                    "exit_pulse:history",
                    kwargs={"session_public_id": session.public_id},
                )
            ),
            self.client.get(reverse("exit_pulse:assignment_comparison")),
        )
        for page in pages:
            content = page.content.decode()
            self.assertNotIn(enrollment.student.student_no, content)
            self.assertNotIn(enrollment.student.first_name, content)
            self.assertNotIn(enrollment.student.last_name, content)
            self.assertNotIn("student_enrollment", content)
        self.assertContains(pages[1], confidential_feedback)


@override_settings(EXIT_PULSE_VERIFICATION_BROWSER_RATE_LIMIT_PER_MINUTE=1)
class ExitPulseIdentityVerificationRateLimitTests(ExitPulseTestBase):
    def test_repeated_failed_verification_is_rate_limited_without_enumeration(self):
        session = self.make_live()
        public_client = Client()
        self.open_public_survey(session, client=public_client, verify=False)
        verify_url = reverse("exit_pulse:public_verify")
        first = public_client.post(
            verify_url,
            {"public_token": session.public_token, "student_number": "UNKNOWN-ONE"},
        )
        second = public_client.post(
            verify_url,
            {"public_token": session.public_token, "student_number": "UNKNOWN-TWO"},
        )

        self.assertEqual(first.status_code, 400)
        self.assertContains(
            first,
            ExitPulseEnrollmentVerificationService.VERIFICATION_ERROR,
            status_code=400,
        )
        self.assertEqual(second.status_code, 429)
        self.assertNotContains(second, "UNKNOWN-TWO", status_code=429)
        self.assertFalse(ExitPulseResponse.objects.exists())

    def test_verification_cache_failure_fails_closed_without_echoing_student_number(self):
        session = self.make_live()
        enrollment = self.eligible_enrollment_for_session(session)
        public_client = Client()
        self.open_public_survey(session, client=public_client, verify=False)

        with patch("apps.exit_pulse.services.cache.add", side_effect=RuntimeError("cache unavailable")):
            response = public_client.post(
                reverse("exit_pulse:public_verify"),
                {
                    "public_token": session.public_token,
                    "student_number": enrollment.student.student_no,
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "A temporary server error occurred.", status_code=503)
        self.assertNotContains(
            response,
            enrollment.student.student_no,
            status_code=503,
        )
        self.assertFalse(ExitPulseResponse.objects.exists())


class ExitPulseFeedbackAndResultsTests(ExitPulseTestBase):
    def test_written_feedback_defaults_off_and_fields_are_hidden(self):
        session = self.make_live()
        _, response = self.open_public_survey(session)
        self.assertNotContains(response, "Which part of today")
        self.assertNotContains(response, "feedback_review")

    def test_enabled_prompt_appears_and_answer_is_optional(self):
        session = self.make_live(allow_written_feedback=True, feedback_review_enabled=True)
        public_client = Client()
        _, survey = self.open_public_survey(session, client=public_client)
        self.assertContains(survey, session.feedback_review_prompt_snapshot)
        self.assertContains(survey, 'class="form-control"', html=False)
        self.assertContains(survey, 'aria-describedby="feedback-review-help feedback-review-error"', html=False)
        _, submitted = self.submit_as_anonymous(session, client=public_client)
        self.assertEqual(submitted.status_code, 302)
        self.assertEqual(ExitPulseResponse.objects.get().feedback_review, "")

    def test_written_feedback_hidden_live_and_visible_only_to_owner_after_close(self):
        session = self.make_live(allow_written_feedback=True, feedback_review_enabled=True)
        self.submit_as_anonymous(
            session,
            data={"feedback_review": "Please review <script>alert(1)</script> joins."},
        )
        live = self.client.get(reverse("exit_pulse:live", kwargs={"public_id": session.public_id}))
        self.assertNotContains(live, "Please review")
        ExitPulseSessionService.close(session=session, user=self.faculty)
        results = self.client.get(reverse("exit_pulse:results", kwargs={"public_id": session.public_id}))
        self.assertContains(results, "Please review &lt;script&gt;alert(1)&lt;/script&gt; joins.", html=False)
        self.assertNotContains(results, "<script>alert(1)</script>", html=False)
        self.client.force_login(self.other_faculty)
        denied = self.client.get(reverse("exit_pulse:results", kwargs={"public_id": session.public_id}))
        self.assertEqual(denied.status_code, 404)

    def test_cancelled_session_does_not_reveal_written_feedback(self):
        session = self.make_live(allow_written_feedback=True, feedback_review_enabled=True)
        self.submit_as_anonymous(session, data={"feedback_review": "Private cancelled-session note"})
        session = ExitPulseSessionService.cancel(session=session, user=self.faculty)
        analytics = ExitPulseAnalyticsService.build(session)
        self.assertEqual(analytics.written_review, ())
        results = self.client.get(reverse("exit_pulse:results", kwargs={"public_id": session.public_id}))
        self.assertNotContains(results, "Private cancelled-session note")
        self.assertNotContains(results, "Anonymous Written Responses")

    def test_counts_percentages_response_rate_and_zero_safety(self):
        self.create_enrollments(4)
        session = self.make_live()
        for index, code in enumerate(("CONFIDENT", "MOSTLY_UNDERSTOOD", "NEEDS_CLARIFICATION", "NEEDS_PRACTICE")):
            ExitPulseResponse.objects.create(
                session=session,
                response_code=code,
                anonymous_token_hash=f"{index:064d}",
            )
        session.status = ExitPulseSession.Status.CLOSED
        session.closed_at = timezone.now()
        session.save(update_fields=["status", "closed_at", "updated_at"])
        analytics = ExitPulseAnalyticsService.build(session)
        self.assertEqual(analytics.total_responses, 4)
        self.assertEqual(analytics.response_rate, Decimal("100.0"))
        self.assertEqual(analytics.understanding_rate, Decimal("50.0"))
        self.assertEqual(analytics.support_needed_rate, Decimal("50.0"))
        empty = self.make_live()
        empty.status = ExitPulseSession.Status.CLOSED
        empty.closed_at = timezone.now()
        empty.save(update_fields=["status", "closed_at", "updated_at"])
        empty_analytics = ExitPulseAnalyticsService.build(empty)
        self.assertEqual(empty_analytics.total_responses, 0)
        self.assertEqual(empty_analytics.understanding_rate, Decimal("0.0"))

    def test_legacy_results_identify_current_enrollment_as_an_estimate(self):
        self.create_enrollments(2)
        session = ExitPulseSessionService.close(
            session=self.make_live(topic="Legacy result"),
            user=self.faculty,
        )
        ExitPulseSession.objects.filter(pk=session.pk).update(
            enrollment_count_snapshot=None,
        )

        response = self.client.get(
            reverse("exit_pulse:results", kwargs={"public_id": session.public_id})
        )

        self.assertContains(response, "Current eligible enrollment estimate")
        self.assertContains(response, "Estimated response rate")
        self.assertContains(response, "Historical enrollment was not captured")
        self.assertContains(response, "current active class list")

    def test_topic_and_custom_question_xss_are_escaped(self):
        session = self.make_live(
            topic="<script>alert('topic')</script>",
            question_code=ExitPulseSession.QuestionCode.CUSTOM,
            custom_question="Which lesson concept needs <img src=x onerror=alert(1)> clarification?",
        )
        _, response = self.open_public_survey(session)
        self.assertContains(response, "&lt;script&gt;alert(&#x27;topic&#x27;)&lt;/script&gt;", html=False)
        self.assertNotContains(response, "<script>alert('topic')</script>", html=False)
        self.assertNotContains(response, "<img src=x", html=False)

    def test_qr_and_polling_endpoint_do_not_expose_comments(self):
        session = self.make_live(allow_written_feedback=True, feedback_review_enabled=True)
        ExitPulseResponse.objects.create(
            session=session,
            response_code="CONFIDENT",
            feedback_review="private anonymous text",
            anonymous_token_hash="q" * 64,
        )
        qr = self.client.get(reverse("exit_pulse:qr", kwargs={"public_id": session.public_id}))
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr["Content-Type"], "image/svg+xml")
        status = self.client.get(reverse("exit_pulse:status", kwargs={"public_id": session.public_id}))
        self.assertEqual(status.json()["response_count"], 1)
        self.assertNotIn("private anonymous text", status.content.decode())

    def test_results_cleanup_is_limited_to_the_opened_session(self):
        first_session = self.make_live()
        second_session = self.make_live()
        first_response = ExitPulseResponse.objects.create(
            session=first_session,
            response_code="CONFIDENT",
            anonymous_token_hash="1" * 64,
            technical_identifier_expires_at=timezone.now() - timedelta(seconds=1),
        )
        second_response = ExitPulseResponse.objects.create(
            session=second_session,
            response_code="CONFIDENT",
            anonymous_token_hash="2" * 64,
            technical_identifier_expires_at=timezone.now() - timedelta(seconds=1),
        )
        for session in (first_session, second_session):
            ExitPulseSession.objects.filter(pk=session.pk).update(
                status=ExitPulseSession.Status.CLOSED,
                closed_at=timezone.now(),
            )

        self.client.get(reverse("exit_pulse:results", kwargs={"public_id": first_session.public_id}))
        first_response.refresh_from_db()
        second_response.refresh_from_db()
        self.assertIsNone(first_response.anonymous_token_hash)
        self.assertEqual(second_response.anonymous_token_hash, "2" * 64)


class ExitPulseCheckpointOneAnalyticsTests(ExitPulseTestBase):
    def _terminal_session(
        self,
        *,
        topic,
        snapshot,
        status=ExitPulseSession.Status.CLOSED,
        started_at=None,
    ):
        session = self.make_live(topic=topic)
        session.status = status
        session.enrollment_count_snapshot = snapshot
        session.started_at = started_at or session.started_at
        session.closed_at = session.started_at + timedelta(minutes=5)
        session.save(
            update_fields=[
                "status",
                "enrollment_count_snapshot",
                "started_at",
                "closed_at",
                "updated_at",
            ]
        )
        return session

    @staticmethod
    def _add_responses(session, codes, prefix):
        for index, code in enumerate(codes):
            ExitPulseResponse.objects.create(
                session=session,
                response_code=code,
                anonymous_token_hash=f"{prefix}{index:063d}"[:64],
            )

    def test_legacy_null_denominator_is_distinct_and_uses_estimated_display_fallback(self):
        self.create_enrollments(2)
        legacy = self._terminal_session(topic="Legacy topic", snapshot=None)
        zero = self._terminal_session(topic="Zero topic", snapshot=0)

        legacy_analytics = ExitPulseAnalyticsService.build(legacy)
        zero_analytics = ExitPulseAnalyticsService.build(zero)

        self.assertEqual(legacy_analytics.enrolled_students, 2)
        self.assertFalse(legacy_analytics.enrollment_denominator_is_historical)
        self.assertEqual(
            legacy_analytics.enrollment_denominator_source,
            ExitPulseAnalyticsService.DENOMINATOR_SOURCE_LEGACY_ESTIMATE,
        )
        self.assertEqual(zero_analytics.enrolled_students, 0)
        self.assertTrue(zero_analytics.enrollment_denominator_is_historical)
        self.assertEqual(
            zero_analytics.enrollment_denominator_source,
            ExitPulseAnalyticsService.DENOMINATOR_SOURCE_SNAPSHOT,
        )

    def test_terminal_filter_excludes_draft_live_and_cancelled_sessions(self):
        closed = self._terminal_session(topic="Closed", snapshot=4)
        expired = self._terminal_session(
            topic="Expired",
            snapshot=4,
            status=ExitPulseSession.Status.EXPIRED,
        )
        draft = self.make_draft(topic="Draft")
        live = self.make_live(topic="Live")
        cancelled = ExitPulseSessionService.cancel(
            session=self.make_live(topic="Cancelled"),
            user=self.faculty,
        )
        for index, session in enumerate((closed, expired, draft, live, cancelled)):
            self._add_responses(session, [ExitPulseResponse.ResponseCode.CONFIDENT], str(index))

        analytics = ExitPulseAnalyticsService.build_assignment(ExitPulseSession.objects.all())

        self.assertEqual(analytics.terminal_session_count, 2)
        self.assertEqual(analytics.total_responses, 2)
        self.assertEqual(analytics.distinct_topic_count, 2)

    def test_distinct_topic_count_trims_values_and_excludes_whitespace_only_topics(self):
        first = self._terminal_session(topic="Normalization", snapshot=3)
        duplicate = self._terminal_session(topic="Temporary duplicate", snapshot=3)
        blank = self._terminal_session(topic="Temporary blank", snapshot=3)
        ExitPulseSession.objects.filter(pk=duplicate.pk).update(topic="  Normalization  ")
        ExitPulseSession.objects.filter(pk=blank.pk).update(topic="   ")

        analytics = ExitPulseAnalyticsService.build_assignment(
            ExitPulseSession.objects.filter(pk__in=[first.pk, duplicate.pk, blank.pk])
        )

        self.assertEqual(analytics.terminal_session_count, 3)
        self.assertEqual(analytics.distinct_topic_count, 1)

    def test_weighted_assignment_calculations_exclude_legacy_response_rate_denominator(self):
        first_at = timezone.now() - timedelta(days=2)
        latest_at = timezone.now() - timedelta(days=1)
        first = self._terminal_session(
            topic="Normalization",
            snapshot=2,
            started_at=first_at,
        )
        second = self._terminal_session(
            topic="SQL joins",
            snapshot=8,
            status=ExitPulseSession.Status.EXPIRED,
            started_at=latest_at,
        )
        legacy = self._terminal_session(topic="Legacy topic", snapshot=None)
        self._add_responses(first, [ExitPulseResponse.ResponseCode.CONFIDENT], "a")
        self._add_responses(
            second,
            [
                ExitPulseResponse.ResponseCode.CONFIDENT,
                ExitPulseResponse.ResponseCode.MOSTLY_UNDERSTOOD,
                ExitPulseResponse.ResponseCode.NEEDS_CLARIFICATION,
                ExitPulseResponse.ResponseCode.NEEDS_PRACTICE,
                ExitPulseResponse.ResponseCode.NEEDS_PRACTICE,
                ExitPulseResponse.ResponseCode.CONFIDENT,
            ],
            "b",
        )
        self._add_responses(
            legacy,
            [
                ExitPulseResponse.ResponseCode.CONFIDENT,
                ExitPulseResponse.ResponseCode.NEEDS_CLARIFICATION,
            ],
            "c",
        )

        with self.assertNumQueries(2):
            analytics = ExitPulseAnalyticsService.build_assignment(
                ExitPulseSession.objects.filter(faculty_assignment=self.assignment)
            )

        self.assertEqual(analytics.terminal_session_count, 3)
        self.assertEqual(analytics.distinct_topic_count, 3)
        self.assertEqual(analytics.latest_terminal_session_at, legacy.started_at)
        self.assertEqual(analytics.total_responses, 9)
        self.assertEqual(analytics.historical_denominator_session_count, 2)
        self.assertEqual(analytics.missing_denominator_session_count, 1)
        self.assertEqual(analytics.enrollment_denominator_total, 10)
        self.assertEqual(analytics.response_total_with_historical_denominator, 7)
        self.assertEqual(analytics.weighted_response_rate, Decimal("70.0"))
        self.assertEqual(analytics.understanding_response_count, 5)
        self.assertEqual(analytics.support_needed_response_count, 4)
        self.assertEqual(analytics.weighted_understanding_rate, Decimal("55.6"))
        self.assertEqual(analytics.weighted_support_needed_rate, Decimal("44.4"))

    def test_zero_response_and_zero_denominator_are_zero_safe(self):
        self._terminal_session(topic="Empty", snapshot=0)
        analytics = ExitPulseAnalyticsService.build_assignment(ExitPulseSession.objects.all())

        self.assertEqual(analytics.terminal_session_count, 1)
        self.assertEqual(analytics.enrollment_denominator_total, 0)
        self.assertEqual(analytics.weighted_response_rate, Decimal("0.0"))
        self.assertEqual(analytics.weighted_understanding_rate, Decimal("0.0"))
        self.assertEqual(analytics.weighted_support_needed_rate, Decimal("0.0"))

    def test_later_enrollment_changes_do_not_change_historical_assignment_rate(self):
        self.create_enrollments(3)
        session = self.make_live(topic="Immutable denominator")
        self._add_responses(
            session,
            [ExitPulseResponse.ResponseCode.CONFIDENT],
            "d",
        )
        ExitPulseSessionService.close(session=session, user=self.faculty)
        Enrollment.objects.update(
            is_active=False,
            enrollment_status=Enrollment.Status.DRP,
        )

        analytics = ExitPulseAnalyticsService.build_assignment(ExitPulseSession.objects.all())

        self.assertEqual(analytics.enrollment_denominator_total, 3)
        self.assertEqual(analytics.weighted_response_rate, Decimal("33.3"))


class ExitPulseDashboardAndHistoryTests(ExitPulseTestBase):
    def _terminal_session(
        self,
        *,
        topic,
        snapshot=4,
        status=ExitPulseSession.Status.CLOSED,
        question_code=ExitPulseSession.QuestionCode.UNDERSTANDING,
        started_at=None,
        allow_written_feedback=False,
    ):
        session = self.make_live(
            topic=topic,
            question_code=question_code,
            custom_question=(
                "Which lesson concept needs more clarification?"
                if question_code == ExitPulseSession.QuestionCode.CUSTOM
                else ""
            ),
            allow_written_feedback=allow_written_feedback,
            feedback_review_enabled=allow_written_feedback,
        )
        session.status = status
        session.enrollment_count_snapshot = snapshot
        session.started_at = started_at or session.started_at
        session.closed_at = session.started_at + timedelta(minutes=5)
        session.save(
            update_fields=[
                "status",
                "enrollment_count_snapshot",
                "started_at",
                "closed_at",
                "updated_at",
            ]
        )
        return session

    @staticmethod
    def _add_responses(session, codes, prefix, *, feedback=""):
        for index, code in enumerate(codes):
            ExitPulseResponse.objects.create(
                session=session,
                response_code=code,
                feedback_review=feedback,
                anonymous_token_hash=f"{prefix}{index:063d}"[:64],
            )

    def test_dashboard_uses_terminal_weighted_metrics_and_neutral_legacy_notices(self):
        stored = self._terminal_session(topic="Stored denominator", snapshot=2)
        legacy = self._terminal_session(
            topic="Legacy denominator",
            snapshot=None,
            allow_written_feedback=True,
        )
        self._add_responses(
            stored,
            [
                ExitPulseResponse.ResponseCode.CONFIDENT,
                ExitPulseResponse.ResponseCode.MOSTLY_UNDERSTOOD,
                ExitPulseResponse.ResponseCode.NEEDS_PRACTICE,
            ],
            "a",
        )
        self._add_responses(
            legacy,
            [ExitPulseResponse.ResponseCode.NEEDS_CLARIFICATION],
            "b",
            feedback="private dashboard feedback",
        )
        cancelled = ExitPulseSessionService.cancel(
            session=self.make_live(topic="Cancelled topic"),
            user=self.faculty,
        )
        self._add_responses(
            cancelled,
            [ExitPulseResponse.ResponseCode.CONFIDENT],
            "c",
        )
        self.make_draft(topic="Draft topic")
        self.make_live(topic="Live topic")

        response = self.client.get(reverse("exit_pulse:landing"))
        analytics = response.context["dashboard_analytics"]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(analytics.terminal_session_count, 2)
        self.assertEqual(analytics.weighted_response_rate, Decimal("150.0"))
        self.assertEqual(analytics.missing_denominator_session_count, 1)
        self.assertEqual(analytics.weighted_understanding_rate, Decimal("50.0"))
        self.assertEqual(analytics.weighted_support_needed_rate, Decimal("50.0"))
        self.assertContains(response, "do not have a stored historical enrollment count")
        self.assertContains(response, "shown without being capped")
        self.assertNotContains(response, "private dashboard feedback")
        self.assertNotContains(response, "faculty ranking")

    def test_dashboard_recent_sessions_are_owned_limited_and_newest_first(self):
        other_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.other_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.other_faculty,
        )
        other_session = ExitPulseSessionService.create_draft(
            user=self.other_faculty,
            assignment=other_assignment,
            topic="Other faculty private topic",
            question_code=ExitPulseSession.QuestionCode.UNDERSTANDING,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        other_session = ExitPulseSessionService.start(
            session=other_session,
            user=self.other_faculty,
        )
        ExitPulseSessionService.close(session=other_session, user=self.other_faculty)
        for index in range(12):
            self._terminal_session(topic=f"Owned topic {index}")

        response = self.client.get(reverse("exit_pulse:landing"))
        recent = response.context["recent_sessions"]

        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[0].session.topic, "Owned topic 11")
        self.assertNotContains(response, "Other faculty private topic")

    def test_dashboard_permission_and_snapshot_scope_are_enforced(self):
        hidden = self._terminal_session(topic="Wrong scope topic")
        other_tenant = Tenant.objects.create(code="PULSE2", name="Other Pulse College")
        other_campus = Campus.objects.create(
            tenant=other_tenant,
            code="OTHER",
            name="Other Campus",
        )
        ExitPulseSession.objects.filter(pk=hidden.pk).update(
            tenant=other_tenant,
            campus=other_campus,
        )
        response = self.client.get(reverse("exit_pulse:landing"))
        self.assertNotContains(response, "Wrong scope topic")

        RolePermission.objects.filter(permission__code="exit_pulse.use").delete()
        denied = self.client.get(reverse("exit_pulse:landing"))
        self.assertEqual(denied.status_code, 403)

    def test_dashboard_fails_closed_without_tenant_and_campus_scope(self):
        self._terminal_session(topic="Must remain scoped")

        with patch("apps.exit_pulse.views._scope_ids", return_value=(None, None)):
            response = self.client.get(reverse("exit_pulse:landing"))

        self.assertEqual(response.status_code, 403)

    def test_original_faculty_retains_history_after_deactivation_but_cannot_create_from_it(self):
        session = self._terminal_session(topic="Historical normalization")
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        history_url = reverse("exit_pulse:history", kwargs={"session_public_id": session.public_id})

        response = self.client.get(history_url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_current_assignment"])
        self.assertContains(response, "Historical assignment")
        self.assertContains(response, "new sessions cannot be created")
        create_response = self.client.post(
            reverse("exit_pulse:create"),
            {
                "faculty_assignment": self.assignment.id,
                "topic": "Unauthorized new pulse",
                "question_code": ExitPulseSession.QuestionCode.UNDERSTANDING,
            },
        )
        self.assertContains(create_response, "Select a valid choice")
        self.assertFalse(ExitPulseSession.objects.filter(topic="Unauthorized new pulse").exists())
        dashboard = self.client.get(reverse("exit_pulse:landing"))
        self.assertContains(dashboard, "Historical normalization")
        self.assertContains(dashboard, "Historical")
        self.assertNotContains(dashboard, 'href="/faculty/exit-pulse/create/"', html=False)

    def test_replacement_faculty_cannot_inherit_prior_faculty_history(self):
        session = self._terminal_session(topic="Prior faculty session")
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.other_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.other_faculty,
        )
        history_url = reverse("exit_pulse:history", kwargs={"session_public_id": session.public_id})

        self.client.force_login(self.other_faculty)
        self.assertEqual(self.client.get(history_url).status_code, 404)
        self.client.force_login(self.faculty)
        self.assertEqual(self.client.get(history_url).status_code, 200)

    def test_history_forged_uuid_and_cross_scope_access_are_denied(self):
        session = self._terminal_session(topic="Scoped history")
        self.assertEqual(
            self.client.get(
                reverse("exit_pulse:history", kwargs={"session_public_id": uuid.uuid4()})
            ).status_code,
            404,
        )
        other_tenant = Tenant.objects.create(code="HIST2", name="History Tenant")
        other_campus = Campus.objects.create(
            tenant=other_tenant,
            code="HIST",
            name="History Campus",
        )
        self.faculty.default_tenant = other_tenant
        self.faculty.default_campus = other_campus
        self.faculty.save(update_fields=["default_tenant", "default_campus", "updated_at"])
        UserRole.objects.create(
            user=self.faculty,
            role=self.faculty_role,
            tenant=other_tenant,
            campus=other_campus,
        )
        self.client.logout()
        self.client.force_login(self.faculty)
        history_url = reverse("exit_pulse:history", kwargs={"session_public_id": session.public_id})
        self.assertEqual(self.client.get(history_url).status_code, 404)

    def test_history_displays_terminal_rows_denominator_integrity_and_no_feedback_content(self):
        older = self._terminal_session(
            topic="<script>older topic</script>",
            snapshot=2,
            started_at=timezone.now() - timedelta(days=3),
            allow_written_feedback=True,
        )
        zero = self._terminal_session(
            topic="Zero denominator",
            snapshot=0,
            status=ExitPulseSession.Status.EXPIRED,
            started_at=timezone.now() - timedelta(days=2),
        )
        legacy = self._terminal_session(
            topic="Legacy unavailable",
            snapshot=None,
            started_at=timezone.now() - timedelta(days=1),
        )
        self._add_responses(
            older,
            [ExitPulseResponse.ResponseCode.CONFIDENT],
            "d",
            feedback="secret history response",
        )
        self.make_draft(topic="Excluded draft")
        self.make_live(topic="Excluded live")
        ExitPulseSessionService.cancel(
            session=self.make_live(topic="Excluded cancelled"),
            user=self.faculty,
        )

        response = self.client.get(
            reverse("exit_pulse:history", kwargs={"session_public_id": older.public_id})
        )
        rows = response.context["page_obj"].object_list

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].session, legacy)
        self.assertEqual(rows[1].enrollment_denominator, 0)
        self.assertTrue(rows[1].response_rate_available)
        self.assertIsNone(rows[0].response_rate)
        self.assertContains(response, "Unavailable")
        self.assertContains(response, "Not historically comparable")
        self.assertContains(response, "&lt;script&gt;older topic&lt;/script&gt;", html=False)
        self.assertNotContains(response, "secret history response")
        self.assertNotContains(response, "Excluded draft")
        self.assertNotContains(response, "Excluded live")
        self.assertNotContains(response, "Excluded cancelled")
        self.assertContains(response, 'role="progressbar"', html=False)
        self.assertContains(response, "View results for")

    def test_history_filters_dates_question_topic_status_and_invalid_range(self):
        first = self._terminal_session(
            topic="Database Normalization",
            started_at=timezone.now() - timedelta(days=5),
        )
        target = self._terminal_session(
            topic="Advanced SQL Joins",
            status=ExitPulseSession.Status.EXPIRED,
            question_code=ExitPulseSession.QuestionCode.APPLICATION_CONFIDENCE,
            started_at=timezone.now() - timedelta(days=2),
        )
        self._terminal_session(topic="Current Networks", started_at=timezone.now())
        history_url = reverse("exit_pulse:history", kwargs={"session_public_id": first.public_id})
        target_date = timezone.localtime(target.started_at).date().isoformat()

        response = self.client.get(
            history_url,
            {
                "date_from": target_date,
                "date_to": target_date,
                "question_type": ExitPulseSession.QuestionCode.APPLICATION_CONFIDENCE,
                "topic": "  sql   joins ",
                "status": ExitPulseSession.Status.EXPIRED,
            },
        )

        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertEqual(response.context["page_obj"].object_list[0].session, target)
        self.assertEqual(response.context["filter_form"].cleaned_data["topic"], "sql joins")
        invalid = self.client.get(
            history_url,
            {"date_from": "2026-07-10", "date_to": "2026-07-01"},
        )
        self.assertContains(invalid, "Date to must be on or after Date from.")
        self.assertEqual(invalid.context["page_obj"].paginator.count, 0)

    def test_history_paginates_twenty_and_preserves_filters(self):
        reference = None
        for index in range(21):
            reference = self._terminal_session(topic=f"Pagination topic {index}")
        history_url = reverse(
            "exit_pulse:history",
            kwargs={"session_public_id": reference.public_id},
        )

        first_page = self.client.get(history_url, {"topic": "Pagination", "page": "1"})
        second_page = self.client.get(history_url, {"topic": "Pagination", "page": "2"})

        self.assertEqual(len(first_page.context["page_obj"].object_list), 20)
        self.assertEqual(len(second_page.context["page_obj"].object_list), 1)
        self.assertEqual(first_page.context["page_obj"].paginator.count, 21)
        self.assertIn("topic=Pagination", first_page.context["page_query"])
        self.assertContains(first_page, "topic=Pagination&amp;page=2", html=False)
        self.assertContains(first_page, f'href="{history_url}">Reset filters</a>', html=False)
        invalid_page = self.client.get(history_url, {"topic": "Pagination", "page": "not-a-page"})
        self.assertEqual(invalid_page.context["page_obj"].number, 1)
        for page_value in ("0", "-2", "999999"):
            edge_page = self.client.get(
                history_url,
                {"topic": "Pagination", "page": page_value},
            )
            self.assertEqual(edge_page.status_code, 200)
            self.assertGreaterEqual(edge_page.context["page_obj"].number, 1)
            self.assertLessEqual(
                edge_page.context["page_obj"].number,
                edge_page.context["page_obj"].paginator.num_pages,
            )

    def test_history_order_is_stable_when_session_timestamps_match(self):
        same_start = timezone.now() - timedelta(days=1)
        sessions = [
            self._terminal_session(
                topic=f"Stable ordering {index}",
                started_at=same_start,
            )
            for index in range(22)
        ]
        history_url = reverse(
            "exit_pulse:history",
            kwargs={"session_public_id": sessions[0].public_id},
        )

        first_page = self.client.get(history_url, {"page": "1"})
        second_page = self.client.get(history_url, {"page": "2"})
        first_ids = [row.session.id for row in first_page.context["page_obj"].object_list]
        second_ids = [row.session.id for row in second_page.context["page_obj"].object_list]

        self.assertEqual(first_ids, sorted(first_ids, reverse=True))
        self.assertEqual(second_ids, sorted(second_ids, reverse=True))
        self.assertEqual(set(first_ids).intersection(second_ids), set())
        self.assertEqual(set(first_ids + second_ids), {session.id for session in sessions})

    def test_dashboard_and_history_reject_non_get_requests(self):
        session = self._terminal_session(topic="Read only routes")
        history_url = reverse(
            "exit_pulse:history",
            kwargs={"session_public_id": session.public_id},
        )

        self.assertEqual(self.client.post(reverse("exit_pulse:landing")).status_code, 405)
        self.assertEqual(self.client.post(history_url).status_code, 405)

    def test_dashboard_expiry_update_is_limited_to_owned_scope(self):
        owned = self.make_live(topic="Owned elapsed")
        other_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.other_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.other_faculty,
        )
        other = ExitPulseSessionService.create_draft(
            user=self.other_faculty,
            assignment=other_assignment,
            topic="Other elapsed",
            question_code=ExitPulseSession.QuestionCode.UNDERSTANDING,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        other = ExitPulseSessionService.start(session=other, user=self.other_faculty)
        elapsed_at = timezone.now() - timedelta(seconds=1)
        ExitPulseSession.objects.filter(pk__in=[owned.pk, other.pk]).update(
            expires_at=elapsed_at,
        )

        response = self.client.get(reverse("exit_pulse:landing"))
        owned.refresh_from_db()
        other.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(owned.status, ExitPulseSession.Status.EXPIRED)
        self.assertEqual(owned.closed_at, elapsed_at)
        self.assertEqual(other.status, ExitPulseSession.Status.LIVE)
        self.assertIsNone(other.closed_at)

    def test_history_metric_queries_do_not_grow_per_row(self):
        sessions = [self._terminal_session(topic=f"Query topic {index}") for index in range(5)]
        for index, session in enumerate(sessions):
            self._add_responses(
                session,
                [ExitPulseResponse.ResponseCode.CONFIDENT],
                str(index),
            )
        terminal = ExitPulseAnalyticsService.terminal_sessions(ExitPulseSession.objects.all())

        with self.assertNumQueries(1):
            rows = ExitPulseAnalyticsService.session_rows(
                ExitPulseAnalyticsService.annotate_session_metrics(terminal).order_by("id")
            )
            self.assertEqual(len(rows), 5)
        with self.assertNumQueries(1):
            assignment_rows = ExitPulseHistoryService.assignment_rows(
                ExitPulseSession.objects.all(),
                current_assignment_ids=[self.assignment.id],
            )
            self.assertEqual(len(assignment_rows), 1)

    def test_dashboard_academic_filters_select_owned_historical_scope(self):
        first = self._terminal_session(topic="First academic scope")
        second_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2027-2028",
            name="AY 2027-2028",
            start_date=date(2027, 6, 1),
            end_date=date(2028, 5, 31),
        )
        second_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=second_year,
            code="2ND",
            name="Second Semester",
        )
        second_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=second_year,
            term=second_term,
            course=self.course,
            section=self.section,
            status=CourseOffering.Status.OPEN,
        )
        second_assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=second_offering,
            faculty_user=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.faculty,
        )
        second = ExitPulseSessionService.create_draft(
            user=self.faculty,
            assignment=second_assignment,
            topic="Second academic scope",
            question_code=ExitPulseSession.QuestionCode.UNDERSTANDING,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        second = ExitPulseSessionService.start(session=second, user=self.faculty)
        ExitPulseSessionService.close(session=second, user=self.faculty)

        response = self.client.get(
            reverse("exit_pulse:landing"),
            {"academic_year": second_year.id, "term": second_term.id},
        )

        self.assertEqual(response.context["dashboard_analytics"].terminal_session_count, 1)
        self.assertContains(response, "Second academic scope")
        self.assertNotContains(response, first.topic)
        invalid = self.client.get(reverse("exit_pulse:landing"), {"academic_year": "999999"})
        self.assertContains(invalid, 'aria-describedby="id_academic_year_errors"', html=False)

    def test_history_empty_state_and_feature_flag_enforcement(self):
        live = self.make_live(topic="Only live reference")
        history_url = reverse("exit_pulse:history", kwargs={"session_public_id": live.public_id})
        response = self.client.get(history_url)
        self.assertContains(response, "No completed Exit Pulse sessions are available for this assignment.")

        SystemSettingService.set(
            FeatureSettingsService.EXIT_PULSE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.assertEqual(self.client.get(history_url).status_code, 403)


class ExitPulseAssignmentComparisonTests(ExitPulseTestBase):
    def _create_assignment(
        self,
        index,
        *,
        user=None,
        academic_year=None,
        term=None,
        course=None,
        section=None,
    ):
        user = user or self.faculty
        academic_year = academic_year or self.academic_year
        term = term or self.term
        course = course or Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code=f"IT{index:03d}",
            title=f"Comparison Course {index}",
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=academic_year,
            term=term,
            course=course,
            section=section or self.section,
            status=CourseOffering.Status.OPEN,
        )
        return FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            faculty_user=user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=user,
        )

    def _terminal_session(
        self,
        *,
        assignment=None,
        user=None,
        topic="Comparison topic",
        snapshot=4,
        status=ExitPulseSession.Status.CLOSED,
        question_code=ExitPulseSession.QuestionCode.UNDERSTANDING,
        custom_question="",
        started_at=None,
        allow_written_feedback=False,
    ):
        assignment = assignment or self.assignment
        user = user or self.faculty
        session = ExitPulseSessionService.create_draft(
            user=user,
            assignment=assignment,
            topic=topic,
            question_code=question_code,
            custom_question=custom_question,
            allow_written_feedback=allow_written_feedback,
            feedback_review_enabled=allow_written_feedback,
            tenant_id=assignment.offering.tenant_id,
            campus_id=assignment.offering.campus_id,
        )
        session = ExitPulseSessionService.start(session=session, user=user)
        session.status = status
        session.enrollment_count_snapshot = snapshot
        session.started_at = started_at or session.started_at
        session.closed_at = session.started_at + timedelta(minutes=5)
        session.save(
            update_fields=[
                "status",
                "enrollment_count_snapshot",
                "started_at",
                "closed_at",
                "updated_at",
            ]
        )
        return session

    @staticmethod
    def _add_responses(session, codes, prefix="r", *, feedback=""):
        for index, code in enumerate(codes):
            ExitPulseResponse.objects.create(
                session=session,
                response_code=code,
                feedback_review=feedback,
                anonymous_token_hash=f"{prefix}{index:063d}"[:64],
            )

    def test_shared_faculty_utility_stack_renders_once_on_exit_pulse_pages(self):
        session = self._terminal_session(topic="Utility footer coverage")
        urls = (
            reverse("exit_pulse:landing"),
            reverse("exit_pulse:results", kwargs={"public_id": session.public_id}),
            reverse("exit_pulse:history", kwargs={"session_public_id": session.public_id}),
            reverse("exit_pulse:assignment_comparison"),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="faculty-utility-stack"', count=1, html=False)
                self.assertContains(response, 'id="faculty-feedback-open"', count=1, html=False)
                self.assertContains(response, 'id="faculty-quick-guide-link"', count=1, html=False)
                self.assertContains(response, 'data-tour-id="privacy-notice"', count=1, html=False)
                content = response.content
                self.assertLess(
                    content.index(b'id="faculty-feedback-open"'),
                    content.index(b'id="faculty-quick-guide-link"'),
                )
                self.assertLess(
                    content.index(b'id="faculty-quick-guide-link"'),
                    content.index(b'data-tour-id="privacy-notice"'),
                )

    def test_current_assignment_without_sessions_has_neutral_no_data_row(self):
        response = self.client.get(reverse("exit_pulse:assignment_comparison"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        row = response.context["page_obj"].object_list[0]
        self.assertEqual(row.assignment, self.assignment)
        self.assertTrue(row.is_current)
        self.assertEqual(row.analytics.terminal_session_count, 0)
        self.assertContains(response, "No completed Exit Pulse data")
        self.assertContains(response, "No completed survey")

    def test_dashboard_and_history_link_to_assignment_comparison(self):
        comparison_url = reverse("exit_pulse:assignment_comparison")
        session = self._terminal_session(topic="Comparison navigation")

        dashboard = self.client.get(reverse("exit_pulse:landing"))
        history = self.client.get(
            reverse("exit_pulse:history", kwargs={"session_public_id": session.public_id})
        )

        self.assertContains(dashboard, f'href="{comparison_url}"')
        self.assertContains(history, f'href="{comparison_url}"')

    def test_weighted_rows_and_summary_use_terminal_snapshot_and_response_totals(self):
        same_start = timezone.now() - timedelta(days=1)
        stored = self._terminal_session(
            topic="  Normalization  ",
            snapshot=2,
            started_at=same_start,
        )
        legacy = self._terminal_session(
            topic="Normalization",
            snapshot=None,
            started_at=same_start,
        )
        zero = self._terminal_session(
            topic="Temporary",
            snapshot=0,
            status=ExitPulseSession.Status.EXPIRED,
            started_at=same_start,
        )
        ExitPulseSession.objects.filter(pk=zero.pk).update(topic="   ")
        self._add_responses(
            stored,
            [
                ExitPulseResponse.ResponseCode.CONFIDENT,
                ExitPulseResponse.ResponseCode.MOSTLY_UNDERSTOOD,
                ExitPulseResponse.ResponseCode.NEEDS_PRACTICE,
            ],
            "s",
        )
        self._add_responses(
            legacy,
            [ExitPulseResponse.ResponseCode.NEEDS_CLARIFICATION],
            "l",
        )
        self._add_responses(
            zero,
            [ExitPulseResponse.ResponseCode.CONFIDENT],
            "z",
        )

        response = self.client.get(reverse("exit_pulse:assignment_comparison"))
        row = response.context["page_obj"].object_list[0]
        summary = response.context["comparison_analytics"]

        self.assertEqual(row.analytics.terminal_session_count, 3)
        self.assertEqual(row.analytics.distinct_topic_count, 1)
        self.assertEqual(row.analytics.historical_denominator_session_count, 2)
        self.assertEqual(row.analytics.missing_denominator_session_count, 1)
        self.assertEqual(row.analytics.enrollment_denominator_total, 2)
        self.assertEqual(row.analytics.response_total_with_historical_denominator, 4)
        self.assertEqual(row.analytics.weighted_response_rate, Decimal("200.0"))
        self.assertEqual(row.analytics.weighted_understanding_rate, Decimal("60.0"))
        self.assertEqual(row.analytics.weighted_support_needed_rate, Decimal("40.0"))
        self.assertEqual(row.latest_session_public_id, zero.public_id)
        self.assertEqual(summary.weighted_response_rate, row.analytics.weighted_response_rate)
        self.assertEqual(summary.weighted_understanding_rate, row.analytics.weighted_understanding_rate)
        self.assertContains(response, "shown without being capped")
        self.assertContains(response, "without a stored historical enrollment count")

    def test_terminal_metrics_ignore_cancelled_draft_and_live_sessions(self):
        draft = self.make_draft(topic="Draft comparison topic")
        live = self.make_live(topic="Live comparison topic")
        cancelled = ExitPulseSessionService.cancel(
            session=self.make_live(topic="Cancelled comparison topic"),
            user=self.faculty,
        )
        for index, session in enumerate((draft, live, cancelled)):
            self._add_responses(
                session,
                [ExitPulseResponse.ResponseCode.CONFIDENT],
                f"x{index}",
            )

        response = self.client.get(reverse("exit_pulse:assignment_comparison"))
        row = response.context["page_obj"].object_list[0]

        self.assertEqual(row.analytics.terminal_session_count, 0)
        self.assertEqual(row.analytics.total_responses, 0)
        self.assertEqual(row.analytics.distinct_topic_count, 0)
        self.assertIsNone(row.latest_session_public_id)
        self.assertNotContains(response, "Draft comparison topic")
        self.assertNotContains(response, "Live comparison topic")
        self.assertNotContains(response, "Cancelled comparison topic")

    def test_route_requires_get_authentication_permission_feature_and_complete_scope(self):
        url = reverse("exit_pulse:assignment_comparison")

        self.assertEqual(self.client.post(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.faculty)
        RolePermission.objects.filter(permission__code="exit_pulse.use").delete()
        self.assertEqual(self.client.get(url).status_code, 403)
        permission = Permission.objects.get(code="exit_pulse.use")
        RolePermission.objects.create(role=self.faculty_role, permission=permission)

        with patch("apps.exit_pulse.views._scope_ids", return_value=(None, self.campus.id)):
            self.assertEqual(self.client.get(url).status_code, 403)
        with patch("apps.exit_pulse.views._scope_ids", return_value=(self.tenant.id, None)):
            self.assertEqual(self.client.get(url).status_code, 403)

        SystemSettingService.set(
            FeatureSettingsService.EXIT_PULSE_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
        )
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_owner_history_survives_replacement_without_granting_replacement_access(self):
        original = self._terminal_session(topic="Original faculty private comparison")
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        replacement = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.other_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            accepted_by=self.other_faculty,
        )

        original_response = self.client.get(reverse("exit_pulse:assignment_comparison"))
        original_rows = original_response.context["page_obj"].object_list
        self.assertEqual([row.assignment.id for row in original_rows], [self.assignment.id])
        self.assertFalse(original_rows[0].is_current)
        self.assertContains(original_response, "Original faculty private comparison")

        self.client.force_login(self.other_faculty)
        replacement_response = self.client.get(reverse("exit_pulse:assignment_comparison"))
        replacement_rows = replacement_response.context["page_obj"].object_list
        self.assertEqual([row.assignment.id for row in replacement_rows], [replacement.id])
        self.assertNotContains(replacement_response, original.topic)

        self.other_faculty.is_superuser = True
        self.other_faculty.save(update_fields=["is_superuser", "updated_at"])
        superuser_response = self.client.get(reverse("exit_pulse:assignment_comparison"))
        self.assertEqual(
            [row.assignment.id for row in superuser_response.context["page_obj"].object_list],
            [replacement.id],
        )

    def test_exact_tenant_and_campus_scope_excludes_inconsistent_records(self):
        hidden_tenant = self._terminal_session(topic="Cross-tenant comparison secret")
        hidden_campus_assignment = self._create_assignment(250)
        hidden_campus = self._terminal_session(
            assignment=hidden_campus_assignment,
            topic="Cross-campus comparison secret",
        )
        other_tenant = Tenant.objects.create(code="CMP2", name="Comparison Tenant 2")
        other_tenant_campus = Campus.objects.create(
            tenant=other_tenant,
            code="CMP2",
            name="Comparison Campus 2",
        )
        other_campus = Campus.objects.create(
            tenant=self.tenant,
            code="CMP3",
            name="Other Campus in Tenant",
        )
        ExitPulseSession.objects.filter(pk=hidden_tenant.pk).update(
            tenant=other_tenant,
            campus=other_tenant_campus,
        )
        FacultyAssignment.objects.filter(pk=self.assignment.pk).update(
            tenant=other_tenant,
            campus=other_tenant_campus,
        )
        ExitPulseSession.objects.filter(pk=hidden_campus.pk).update(campus=other_campus)
        FacultyAssignment.objects.filter(pk=hidden_campus_assignment.pk).update(
            campus=other_campus,
        )

        response = self.client.get(reverse("exit_pulse:assignment_comparison"))

        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertNotContains(response, hidden_tenant.topic)
        self.assertNotContains(response, hidden_campus.topic)
        self.assertContains(response, "No assignments are available")

    def test_similar_labels_remain_distinct_and_current_and_historical_rows_are_clear(self):
        historical_session = self._terminal_session(topic="Earlier academic scope")
        self.assignment.is_active = False
        self.assignment.save(update_fields=["is_active", "updated_at"])
        later_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2027-2028",
            name="AY 2027-2028",
            start_date=date(2027, 6, 1),
            end_date=date(2028, 5, 31),
        )
        later_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=later_year,
            code="1ST",
            name="First Semester",
        )
        current = self._create_assignment(
            202,
            academic_year=later_year,
            term=later_term,
            course=self.course,
            section=self.section,
        )

        response = self.client.get(reverse("exit_pulse:assignment_comparison"))
        rows = response.context["page_obj"].object_list

        self.assertEqual({row.assignment.id for row in rows}, {self.assignment.id, current.id})
        states = {row.assignment.id: row.is_current for row in rows}
        self.assertEqual(states, {self.assignment.id: False, current.id: True})
        self.assertContains(response, historical_session.topic)
        self.assertContains(response, "Historical")
        self.assertContains(response, "Current")

    def test_legacy_unavailable_and_stored_zero_are_distinct(self):
        legacy = self._terminal_session(topic="Legacy-only comparison", snapshot=None)
        zero_assignment = self._create_assignment(203)
        zero = self._terminal_session(
            assignment=zero_assignment,
            topic="Stored-zero comparison",
            snapshot=0,
        )
        self._add_responses(legacy, [ExitPulseResponse.ResponseCode.CONFIDENT], "legacy")
        self._add_responses(zero, [ExitPulseResponse.ResponseCode.NEEDS_PRACTICE], "zero")

        response = self.client.get(reverse("exit_pulse:assignment_comparison"))
        rows = {row.assignment.id: row for row in response.context["page_obj"].object_list}

        self.assertFalse(rows[self.assignment.id].response_rate_available)
        self.assertTrue(rows[zero_assignment.id].response_rate_available)
        self.assertEqual(rows[zero_assignment.id].analytics.enrollment_denominator_total, 0)
        self.assertEqual(rows[zero_assignment.id].analytics.weighted_response_rate, Decimal("0.0"))
        self.assertContains(response, "Not historically comparable")
        self.assertContains(response, "0.0%")

    def test_filters_apply_to_rows_and_summary_and_reject_forged_values(self):
        first = self._terminal_session(topic="First filter topic", snapshot=2)
        self._add_responses(first, [ExitPulseResponse.ResponseCode.CONFIDENT], "filter1")
        later_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2028-2029",
            name="AY 2028-2029",
            start_date=date(2028, 6, 1),
            end_date=date(2029, 5, 31),
        )
        later_term = Term.objects.create(
            tenant=self.tenant,
            academic_year=later_year,
            code="2ND",
            name="Second Semester",
        )
        later_assignment = self._create_assignment(
            204,
            academic_year=later_year,
            term=later_term,
        )
        later = self._terminal_session(
            assignment=later_assignment,
            topic="Custom filter topic",
            snapshot=4,
            question_code=ExitPulseSession.QuestionCode.CUSTOM,
            custom_question="Which concept should we revisit?",
        )
        self._add_responses(later, [ExitPulseResponse.ResponseCode.NEEDS_CLARIFICATION], "filter2")

        url = reverse("exit_pulse:assignment_comparison")
        filtered = self.client.get(
            url,
            {
                "academic_year": later_year.id,
                "term": later_term.id,
                "question_type": ExitPulseSession.QuestionCode.CUSTOM,
            },
        )
        rows = filtered.context["page_obj"].object_list
        summary = filtered.context["comparison_analytics"]
        self.assertEqual([row.assignment.id for row in rows], [later_assignment.id])
        self.assertEqual(summary.terminal_session_count, 1)
        self.assertEqual(summary.total_responses, 1)
        self.assertNotContains(filtered, first.topic)

        invalid = self.client.get(url, {"question_type": "FORGED"})
        self.assertFalse(invalid.context["filter_form"].is_valid())
        self.assertEqual(invalid.context["page_obj"].paginator.count, 0)
        self.assertEqual(invalid.context["comparison_analytics"].terminal_session_count, 0)
        self.assertContains(invalid, "Choose filter values available")

    def test_latest_terminal_is_deterministic_escaped_and_never_exposes_feedback_or_tokens(self):
        same_start = timezone.now() - timedelta(hours=1)
        older = self._terminal_session(topic="Older safe topic", started_at=same_start)
        latest = self._terminal_session(
            topic="<script>alert('topic')</script>",
            question_code=ExitPulseSession.QuestionCode.CUSTOM,
            custom_question="What concept needs more explanation?",
            started_at=same_start,
            allow_written_feedback=True,
        )
        ExitPulseSession.objects.filter(pk=latest.pk).update(
            question_text_snapshot="<img src=x onerror=alert('question')>",
        )
        self._add_responses(
            latest,
            [ExitPulseResponse.ResponseCode.CONFIDENT],
            "private",
            feedback="PRIVATE WRITTEN FEEDBACK",
        )
        live = self.make_live(topic="Newer live topic")

        response = self.client.get(reverse("exit_pulse:assignment_comparison"))
        row = response.context["page_obj"].object_list[0]
        content = response.content.decode()
        visible_text = strip_tags(content)

        self.assertGreater(latest.id, older.id)
        self.assertEqual(row.latest_session_public_id, latest.public_id)
        self.assertNotEqual(row.latest_session_public_id, live.public_id)
        self.assertNotIn("<script>alert('topic')</script>", content)
        self.assertIn("&lt;script&gt;", content)
        self.assertNotIn("<img src=x onerror=alert('question')>", content)
        self.assertNotIn("PRIVATE WRITTEN FEEDBACK", content)
        self.assertNotIn(latest.public_token, content)
        self.assertNotIn(str(latest.public_id), visible_text)

    def test_pagination_is_stable_complete_and_preserves_filters(self):
        assignment_ids = {self.assignment.id}
        for index in range(205, 225):
            assignment_ids.add(self._create_assignment(index).id)
        url = reverse("exit_pulse:assignment_comparison")

        first = self.client.get(
            url,
            {"question_type": ExitPulseSession.QuestionCode.UNDERSTANDING},
        )
        second = self.client.get(
            url,
            {"question_type": ExitPulseSession.QuestionCode.UNDERSTANDING, "page": 2},
        )
        first_ids = {row.assignment.id for row in first.context["page_obj"].object_list}
        second_ids = {row.assignment.id for row in second.context["page_obj"].object_list}

        self.assertEqual(first.context["page_obj"].paginator.count, 21)
        self.assertEqual(len(first_ids), 20)
        self.assertEqual(len(second_ids), 1)
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(first_ids | second_ids, assignment_ids)
        self.assertContains(first, "question_type=UNDERSTANDING&amp;page=2", html=False)
        self.assertEqual(self.client.get(url, {"page": "invalid"}).context["page_obj"].number, 1)
        self.assertEqual(self.client.get(url, {"page": 0}).context["page_obj"].number, 2)
        self.assertEqual(self.client.get(url, {"page": -1}).context["page_obj"].number, 2)
        self.assertEqual(self.client.get(url, {"page": 999}).context["page_obj"].number, 2)

    def test_comparison_row_aggregation_uses_one_bounded_query(self):
        for index in range(225, 230):
            assignment = self._create_assignment(index)
            session = self._terminal_session(assignment=assignment, snapshot=3)
            self._add_responses(session, [ExitPulseResponse.ResponseCode.CONFIDENT], f"q{index}")
        owned = ExitPulseHistoryService.owned_sessions(
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )
        terminal = ExitPulseAnalyticsService.terminal_sessions(owned)
        current_ids = list(
            ExitPulseSessionService.valid_assignments_for_user(
                user=self.faculty,
                tenant_id=self.tenant.id,
                campus_id=self.campus.id,
            ).values_list("id", flat=True)
        )
        assignments = ExitPulseHistoryService.comparison_assignments(
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
            current_assignment_ids=current_ids,
            sessions=terminal,
        )
        comparison = ExitPulseAnalyticsService.annotate_assignment_comparison(
            assignments,
            terminal_sessions=terminal,
            owned_sessions=owned,
            current_assignment_ids=current_ids,
            user=self.faculty,
            tenant_id=self.tenant.id,
            campus_id=self.campus.id,
        )

        with self.assertNumQueries(1):
            rows = ExitPulseAnalyticsService.comparison_rows(comparison)

        self.assertEqual(len(rows), 6)


@override_settings(EXIT_PULSE_BROWSER_RATE_LIMIT_PER_MINUTE=1)
class ExitPulseRateLimitTests(ExitPulseTestBase):
    def test_basic_browser_rate_limit_uses_existing_cache_pattern(self):
        session = self.make_live()
        public_client = Client()
        enrollment = self.eligible_enrollment_for_session(session)
        self.open_public_survey(
            session,
            client=public_client,
            enrollment=enrollment,
        )
        submit_url = reverse("exit_pulse:public_submit")
        invalid = public_client.post(
            submit_url,
            {"response_code": "ANGRY"},
        )
        self.assertEqual(invalid.status_code, 400)
        first_valid = public_client.post(
            submit_url,
            {"response_code": "CONFIDENT"},
        )
        self.assertEqual(first_valid.status_code, 302)
        # Clearing the stored response isolates the public rate limiter from duplicate handling.
        ExitPulseResponse.objects.all().delete()
        self.open_public_survey(
            session,
            client=public_client,
            enrollment=enrollment,
        )
        limited = public_client.post(
            submit_url,
            {"response_code": "CONFIDENT"},
        )
        self.assertEqual(limited.status_code, 429)

    def test_cache_failure_fails_closed_without_storing_response(self):
        session = self.make_live()
        public_client = Client()
        self.open_public_survey(session, client=public_client)
        with patch("apps.exit_pulse.services.cache.add", side_effect=RuntimeError("cache unavailable")):
            response = public_client.post(
                reverse("exit_pulse:public_submit"),
                {"response_code": "CONFIDENT"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "A temporary server error occurred.", status_code=503)
        self.assertFalse(ExitPulseResponse.objects.exists())
