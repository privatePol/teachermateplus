from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

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
        started = ExitPulseSessionService.start(session=draft, user=self.faculty)
        self.assertEqual(started.status, ExitPulseSession.Status.LIVE)
        self.assertAlmostEqual((started.expires_at - started.started_at).total_seconds(), 300, delta=1)

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
