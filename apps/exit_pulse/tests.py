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
    ExitPulseAnonymousIdentityService,
    ExitPulseDuplicateResponse,
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

    def submit_as_anonymous(self, session, *, response_code="CONFIDENT", data=None, client=None, ip="10.0.0.5"):
        public_client = client or Client()
        public_client.get(reverse("exit_pulse:public_survey"), REMOTE_ADDR=ip)
        public_client.post(
            reverse("exit_pulse:public_open"),
            {"public_token": session.public_token},
            REMOTE_ADDR=ip,
        )
        payload = {"public_token": session.public_token, "response_code": response_code}
        payload.update(data or {})
        response = public_client.post(reverse("exit_pulse:public_submit"), payload, REMOTE_ADDR=ip)
        return public_client, response

    def open_public_survey(self, session, *, client=None, ip="10.0.0.5"):
        public_client = client or Client()
        public_client.get(reverse("exit_pulse:public_survey"), REMOTE_ADDR=ip)
        response = public_client.post(
            reverse("exit_pulse:public_open"),
            {"public_token": session.public_token},
            REMOTE_ADDR=ip,
        )
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
                anonymous_hash="a" * 64,
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

    def test_same_browser_is_rejected_but_same_ip_different_browser_is_allowed(self):
        session = self.make_live()
        client_one, first = self.submit_as_anonymous(session, ip="10.1.1.1")
        self.assertEqual(first.status_code, 302)
        _, duplicate = self.submit_as_anonymous(session, client=client_one, ip="10.1.1.1")
        self.assertEqual(duplicate.status_code, 409)
        _, second_browser = self.submit_as_anonymous(session, client=Client(), ip="10.1.1.1")
        self.assertEqual(second_browser.status_code, 302)
        self.assertEqual(session.responses.count(), 2)

    def test_database_constraint_safely_rejects_duplicate_hash(self):
        session = self.make_live()
        ExitPulseResponse.objects.create(session=session, response_code="CONFIDENT", anonymous_token_hash="d" * 64)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExitPulseResponse.objects.create(
                    session=session,
                    response_code="NEEDS_PRACTICE",
                    anonymous_token_hash="d" * 64,
                )

    def test_service_converts_insert_race_to_safe_duplicate_error(self):
        session = self.make_live()
        with patch(
            "apps.exit_pulse.services.ExitPulseResponse.objects.create",
            side_effect=IntegrityError("simulated duplicate race"),
        ):
            with self.assertRaises(ExitPulseDuplicateResponse):
                ExitPulseResponseService.submit(
                    session=session,
                    response_code="CONFIDENT",
                    anonymous_hash="r" * 64,
                )

    def test_malformed_token_and_get_to_submit_are_handled_safely(self):
        malformed = self.client.post(reverse("exit_pulse:public_open"), {"public_token": "bad"})
        self.assertEqual(malformed.status_code, 404)
        get_submit = self.client.get(reverse("exit_pulse:public_submit"))
        self.assertEqual(get_submit.status_code, 405)

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
            self.assertEqual(response.status_code, 409)

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
        csrf_client.get(reverse("exit_pulse:public_survey"))
        token = csrf_client.cookies["csrftoken"].value
        opened = csrf_client.post(
            reverse("exit_pulse:public_open"),
            {"public_token": session.public_token, "csrfmiddlewaretoken": token},
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(opened.status_code, 200)
        accepted = csrf_client.post(
            submit_url,
            {
                "public_token": session.public_token,
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


@override_settings(EXIT_PULSE_BROWSER_RATE_LIMIT_PER_MINUTE=1)
class ExitPulseRateLimitTests(ExitPulseTestBase):
    def test_basic_browser_rate_limit_uses_existing_cache_pattern(self):
        session = self.make_live()
        public_client = Client()
        public_client.get(reverse("exit_pulse:public_survey"))
        public_client.post(reverse("exit_pulse:public_open"), {"public_token": session.public_token})
        submit_url = reverse("exit_pulse:public_submit")
        invalid = public_client.post(
            submit_url,
            {"public_token": session.public_token, "response_code": "ANGRY"},
        )
        self.assertEqual(invalid.status_code, 400)
        first_valid = public_client.post(
            submit_url,
            {"public_token": session.public_token, "response_code": "CONFIDENT"},
        )
        self.assertEqual(first_valid.status_code, 302)
        # Clearing the stored response isolates the public rate limiter from duplicate handling.
        ExitPulseResponse.objects.all().delete()
        limited = public_client.post(
            submit_url,
            {"public_token": session.public_token, "response_code": "CONFIDENT"},
        )
        self.assertEqual(limited.status_code, 429)

    def test_cache_failure_fails_closed_without_storing_response(self):
        session = self.make_live()
        public_client = Client()
        self.open_public_survey(session, client=public_client)
        with patch("apps.exit_pulse.services.cache.add", side_effect=RuntimeError("cache unavailable")):
            response = public_client.post(
                reverse("exit_pulse:public_submit"),
                {"public_token": session.public_token, "response_code": "CONFIDENT"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "A temporary server error occurred.", status_code=503)
        self.assertFalse(ExitPulseResponse.objects.exists())
