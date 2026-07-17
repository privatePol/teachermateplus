from __future__ import annotations

import importlib
import re
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.core.context_processors import portal_menu
from apps.orientation_feedback.forms import OrientationResponseForm
from apps.orientation_feedback.models import (
    OrientationSurveyAnswer,
    OrientationSurveyParticipation,
    OrientationSurveyResponse,
    OrientationSurveySession,
)
from apps.orientation_feedback.services import (
    OrientationSurveyAnalyticsService,
    OrientationSurveyDuplicateResponse,
    OrientationSurveyPublicService,
    OrientationSurveyRateLimited,
    OrientationSurveyRateLimitService,
    OrientationSurveyResponseService,
    OrientationSurveySessionService,
    VerifiedOrientationParticipant,
)
from apps.navigation.models import MenuGroup
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.tenants.models import Campus, Tenant


class OrientationFeedbackTestBase(TestCase):
    permission_codes = (
        "orientation_feedback.view",
        "orientation_feedback.manage",
        "orientation_feedback.start",
        "orientation_feedback.close",
        "orientation_feedback.cancel",
        "orientation_feedback.view_analytics",
        "orientation_feedback.export",
        "admin_portal.access",
    )

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(code="T1", name="Tenant One")
        self.campus = Campus.objects.create(tenant=self.tenant, code="MAIN", name="Main Campus")
        self.other_tenant = Tenant.objects.create(code="T2", name="Tenant Two")
        self.other_campus = Campus.objects.create(
            tenant=self.other_tenant,
            code="OTHER",
            name="Other Campus",
        )
        self.admin_role, _ = Role.objects.update_or_create(
            code="ORIENTATION_MANAGER",
            defaults={"name": "Orientation Manager", "is_active": True},
        )
        self.faculty_role, _ = Role.objects.update_or_create(
            code="FACULTY",
            defaults={"name": "Faculty", "is_active": True},
        )
        self.ac_role, _ = Role.objects.update_or_create(
            code="AC",
            defaults={"name": "Area Chair", "is_active": True},
        )
        self.dean_role, _ = Role.objects.update_or_create(
            code="COLLEGE_DEAN",
            defaults={"name": "College Dean", "is_active": True},
        )
        self.cao_role, _ = Role.objects.update_or_create(
            code="CAO",
            defaults={"name": "Chief Academic Officer", "is_active": True},
        )
        for code in self.permission_codes:
            module, action = code.split(".", 1)
            permission, _ = Permission.objects.update_or_create(
                code=code,
                defaults={
                    "module": module,
                    "action": action,
                    "description": code,
                    "is_active": True,
                },
            )
            RolePermission.objects.get_or_create(role=self.admin_role, permission=permission)
        self.admin = User.objects.create_user(
            username="orientation-admin",
            email="orientation-admin@example.edu",
            password="pass",
            default_tenant=self.tenant,
            default_campus=self.campus,
            is_active=True,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=self.admin,
            role=self.admin_role,
            tenant=self.tenant,
            campus=self.campus,
            is_active=True,
        )
        self.faculty = self.make_user(
            "faculty-one",
            "faculty.one@example.edu",
            self.faculty_role,
        )
        self.inactive_faculty = self.make_user(
            "faculty-inactive",
            "inactive.faculty@example.edu",
            self.faculty_role,
            is_active=False,
        )
        self.academic_head = self.make_user(
            "area-chair",
            "area.chair@example.edu",
            self.ac_role,
        )

    def make_user(
        self,
        username,
        email,
        role,
        *,
        tenant=None,
        campus=None,
        is_active=True,
        role_is_active=True,
    ):
        tenant = tenant or self.tenant
        campus = campus or self.campus
        user = User.objects.create_user(
            username=username,
            email=email,
            password="pass",
            default_tenant=tenant,
            default_campus=campus,
            is_active=is_active,
        )
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=tenant,
            campus=campus,
            is_active=role_is_active,
        )
        return user

    def make_session(self, survey_type=OrientationSurveySession.SurveyType.FACULTY, **overrides):
        values = {
            "survey_type": survey_type,
            "title": "TeacherMate+ Orientation Feedback",
            "description": "Help us improve future orientations.",
            "tenant": self.tenant,
            "campus": self.campus,
            "status": OrientationSurveySession.Status.DRAFT,
            "created_by": self.admin,
        }
        values.update(overrides)
        session = OrientationSurveySession.objects.create(**values)
        if survey_type == OrientationSurveySession.SurveyType.ACADEMIC_HEADS:
            session.eligible_head_roles.set([self.ac_role, self.dean_role, self.cao_role])
        OrientationSurveySessionService.seed_questions(session)
        return session

    def start(self, session):
        return OrientationSurveySessionService.start(session=session, user=self.admin)

    def response_payload(self, session, *, score_code="5", comment="Helpful orientation"):
        questions = session.questions.prefetch_related("choices").order_by("display_order")
        payload = {}
        for question in questions:
            field_name = f"q_{question.code}"
            if question.question_type == question.QuestionType.SCALE:
                payload[field_name] = score_code
            elif question.question_type == question.QuestionType.MULTI_SELECT:
                payload[field_name] = [question.choices.order_by("display_order").first().code]
            else:
                payload[field_name] = comment
        form = OrientationResponseForm(payload, questions=questions)
        self.assertTrue(form.is_valid(), form.errors)
        return form.cleaned_data

    def submit_for(self, session, user, *, score_code="5", comment="Helpful orientation"):
        participation = session.participations.get(user=user)
        verified = VerifiedOrientationParticipant(session=session, participation=participation)
        return OrientationSurveyResponseService.submit(
            verified=verified,
            cleaned_data=self.response_payload(session, score_code=score_code, comment=comment),
        )

    def latest_otp_code(self):
        self.assertTrue(mail.outbox)
        match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
        self.assertIsNotNone(match)
        return match.group(1)

    def add_faculty_respondents(self, count):
        return [
            self.make_user(
                f"faculty-report-{index}",
                f"faculty.report.{index}@example.edu",
                self.faculty_role,
            )
            for index in range(count)
        ]

    def add_head_respondents(self, count):
        return [
            self.make_user(
                f"head-report-{index}",
                f"head.report.{index}@example.edu",
                self.ac_role,
            )
            for index in range(count)
        ]


class OrientationSurveyLifecycleAndEligibilityTests(OrientationFeedbackTestBase):
    def test_draft_cannot_accept_responses_and_start_freezes_questions_and_roster(self):
        session = self.make_session()
        state, _ = OrientationSurveyPublicService.state(session)
        self.assertEqual(state, "draft")
        original_text = session.questions.first().text

        started = self.start(session)

        self.assertEqual(started.status, OrientationSurveySession.Status.OPEN)
        self.assertEqual(started.question_snapshot_version, 1)
        self.assertEqual(started.eligible_count_snapshot, 2)
        self.assertTrue(started.participations.filter(user=self.faculty).exists())
        self.assertTrue(started.participations.filter(user=self.inactive_faculty).exists())
        question = started.questions.first()
        question.text = "Changed after start"
        with self.assertRaises(ValidationError):
            from apps.orientation_feedback.forms import OrientationQuestionFormSet

            formset = OrientationQuestionFormSet(
                data={
                    "form-TOTAL_FORMS": "0",
                    "form-INITIAL_FORMS": "0",
                    "form-MIN_NUM_FORMS": "0",
                    "form-MAX_NUM_FORMS": "1000",
                },
                queryset=started.questions.none(),
            )
            OrientationSurveySessionService.update_questions(
                session=started,
                formset=formset,
                user=self.admin,
            )
        self.assertEqual(started.questions.first().text, original_text)
        published_question = started.questions.first()
        published_question.text = "Direct mutation attempt"
        with self.assertRaises(ValidationError):
            published_question.save()
        published_choice = started.questions.filter(question_type="SCALE").first().choices.first()
        published_choice.score = 1 if published_choice.score != 1 else 2
        with self.assertRaises(ValidationError):
            published_choice.save()

    def test_inactive_role_and_cross_tenant_faculty_are_not_eligible(self):
        inactive_role_user = self.make_user(
            "inactive-role",
            "inactive.role@example.edu",
            self.faculty_role,
            role_is_active=False,
        )
        outside = self.make_user(
            "outside-faculty",
            "outside.faculty@example.edu",
            self.faculty_role,
            tenant=self.other_tenant,
            campus=self.other_campus,
        )
        session = self.start(self.make_session())
        self.assertFalse(session.participations.filter(user=inactive_role_user).exists())
        self.assertFalse(session.participations.filter(user=outside).exists())

    def test_academic_head_roles_and_configured_additional_role_are_eligible(self):
        dean = self.make_user("dean", "dean@example.edu", self.dean_role)
        cao = self.make_user("cao", "cao@example.edu", self.cao_role, is_active=False)
        additional_role = Role.objects.create(code="ORIENTATION_ADMIN", name="Orientation Admin")
        additional = self.make_user("additional", "additional@example.edu", additional_role)
        faculty_only = self.faculty
        session = self.make_session(OrientationSurveySession.SurveyType.ACADEMIC_HEADS)
        session.eligible_head_roles.add(additional_role)
        session = self.start(session)
        for user in (self.academic_head, dean, cao, additional):
            self.assertTrue(session.participations.filter(user=user).exists())
        self.assertFalse(session.participations.filter(user=faculty_only).exists())

    def test_dual_role_user_can_participate_once_in_each_session(self):
        UserRole.objects.create(
            user=self.faculty,
            role=self.ac_role,
            tenant=self.tenant,
            campus=self.campus,
        )
        faculty_session = self.start(self.make_session())
        heads_session = self.start(self.make_session(OrientationSurveySession.SurveyType.ACADEMIC_HEADS))
        self.assertTrue(faculty_session.participations.filter(user=self.faculty).exists())
        self.assertTrue(heads_session.participations.filter(user=self.faculty).exists())
        self.submit_for(faculty_session, self.faculty)
        self.submit_for(heads_session, self.faculty)
        self.assertEqual(OrientationSurveyResponse.objects.filter(participation__user=self.faculty).count(), 2)

    def test_close_cancel_and_invalid_transitions_are_enforced(self):
        session = self.start(self.make_session())
        closed = OrientationSurveySessionService.close(session=session, user=self.admin)
        self.assertEqual(closed.status, OrientationSurveySession.Status.CLOSED)
        with self.assertRaises(ValidationError):
            OrientationSurveySessionService.start(session=closed, user=self.admin)
        with self.assertRaises(ValidationError):
            OrientationSurveySessionService.cancel(session=closed, user=self.admin, reason="No longer needed")
        state, _ = OrientationSurveyPublicService.state(closed)
        self.assertEqual(state, "closed")

        draft = self.make_session()
        with self.assertRaises(ValidationError):
            OrientationSurveySessionService.cancel(session=draft, user=self.admin, reason="")
        cancelled = OrientationSurveySessionService.cancel(
            session=draft,
            user=self.admin,
            reason="Orientation schedule was withdrawn.",
        )
        self.assertEqual(cancelled.status, OrientationSurveySession.Status.CANCELLED)
        self.assertEqual(cancelled.cancellation_reason, "Orientation schedule was withdrawn.")

    def test_automatic_close_blocks_the_public_state(self):
        session = self.make_session(auto_close_at=timezone.now() + timedelta(minutes=1))
        session = self.start(session)
        OrientationSurveySessionService.refresh_auto_close(
            session,
            now=session.auto_close_at + timedelta(seconds=1),
        )
        self.assertEqual(session.status, OrientationSurveySession.Status.CLOSED)
        self.assertEqual(session.closure_reason, OrientationSurveySession.ClosureReason.AUTOMATIC)


class OrientationSurveyResponseIntegrityTests(OrientationFeedbackTestBase):
    def test_submission_is_atomic_and_duplicate_is_blocked(self):
        session = self.start(self.make_session())
        response = self.submit_for(session, self.faculty)
        self.assertEqual(response.answers.count(), session.questions.count())
        participation = session.participations.get(user=self.faculty)
        self.assertIsNotNone(participation.submitted_at)
        with self.assertRaises(OrientationSurveyDuplicateResponse):
            self.submit_for(session, self.faculty)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrientationSurveyResponse.objects.create(session=session, participation=participation)

    def test_tampered_choice_code_is_rejected_without_partial_response(self):
        session = self.start(self.make_session())
        participation = session.participations.get(user=self.faculty)
        payload = self.response_payload(session)
        first = session.questions.filter(question_type="SCALE").first()
        payload[f"q_{first.code}"] = "999"
        verified = VerifiedOrientationParticipant(session=session, participation=participation)
        with self.assertRaises(ValidationError):
            OrientationSurveyResponseService.submit(verified=verified, cleaned_data=payload)
        self.assertFalse(OrientationSurveyResponse.objects.filter(participation=participation).exists())
        self.assertFalse(OrientationSurveyAnswer.objects.filter(response__participation=participation).exists())

    def test_closed_and_cancelled_sessions_reject_final_submission(self):
        closed = self.start(self.make_session())
        participation = closed.participations.get(user=self.faculty)
        payload = self.response_payload(closed)
        OrientationSurveySessionService.close(session=closed, user=self.admin)
        with self.assertRaises(ValidationError):
            OrientationSurveyResponseService.submit(
                verified=VerifiedOrientationParticipant(session=closed, participation=participation),
                cleaned_data=payload,
            )

        cancelled = self.start(self.make_session())
        participation = cancelled.participations.get(user=self.faculty)
        payload = self.response_payload(cancelled)
        OrientationSurveySessionService.cancel(
            session=cancelled,
            user=self.admin,
            reason="Cancelled for testing",
        )
        with self.assertRaises(ValidationError):
            OrientationSurveyResponseService.submit(
                verified=VerifiedOrientationParticipant(session=cancelled, participation=participation),
                cleaned_data=payload,
            )


class OrientationSurveyPublicFlowTests(OrientationFeedbackTestBase):
    def test_inactive_user_validates_by_email_case_insensitively_and_verifies_otp(self):
        session = self.start(self.make_session())
        open_response = self.client.post(
            reverse("orientation_feedback:public_open"),
            {"public_token": session.public_token},
        )
        self.assertEqual(open_response.status_code, 200)
        response = self.client.post(
            reverse("orientation_feedback:public_validate"),
            {"public_token": session.public_token, "email": " INACTIVE.FACULTY@EXAMPLE.EDU "},
        )
        self.assertRedirects(response, reverse("orientation_feedback:public_verify"), fetch_redirect_response=False)
        self.assertEqual(mail.outbox[-1].to, [self.inactive_faculty.email])
        verify_page = self.client.get(reverse("orientation_feedback:public_verify"))
        self.assertContains(verify_page, "Enter your verification code")
        self.assertNotContains(verify_page, self.inactive_faculty.username)
        self.assertNotContains(verify_page, self.inactive_faculty.email)
        verified = self.client.post(
            reverse("orientation_feedback:public_verify"),
            {"otp_code": self.latest_otp_code()},
        )
        self.assertRedirects(verified, reverse("orientation_feedback:public_response"), fetch_redirect_response=False)
        participation = session.participations.get(user=self.inactive_faculty)
        self.assertEqual(participation.validation_method, participation.ValidationMethod.EMAIL_OTP)
        self.assertIsNotNone(participation.email_verified_at)
        self.assertEqual(participation.email_otp_hash, "")

    def test_otp_is_required_and_bound_to_the_same_browser_session(self):
        session = self.start(self.make_session())
        self.client.post(reverse("orientation_feedback:public_open"), {"public_token": session.public_token})
        self.client.post(
            reverse("orientation_feedback:public_validate"),
            {"public_token": session.public_token, "email": self.faculty.email},
        )
        code = self.latest_otp_code()
        other_browser = Client()
        denied = other_browser.post(reverse("orientation_feedback:public_verify"), {"otp_code": code})
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(session.responses.exists())
        verified = self.client.post(reverse("orientation_feedback:public_verify"), {"otp_code": code})
        self.assertRedirects(verified, reverse("orientation_feedback:public_response"), fetch_redirect_response=False)

    @override_settings(ORIENTATION_FEEDBACK_EMAIL_OTP_MAX_ATTEMPTS=2)
    def test_otp_attempt_limit_and_expiry_clear_the_pending_state(self):
        session = self.start(self.make_session())
        self.client.post(reverse("orientation_feedback:public_open"), {"public_token": session.public_token})
        self.client.post(
            reverse("orientation_feedback:public_validate"),
            {"public_token": session.public_token, "email": self.faculty.email},
        )
        actual_code = self.latest_otp_code()
        wrong_code = "000000" if actual_code != "000000" else "999999"
        first = self.client.post(reverse("orientation_feedback:public_verify"), {"otp_code": wrong_code})
        second = self.client.post(reverse("orientation_feedback:public_verify"), {"otp_code": wrong_code})
        self.assertEqual(first.status_code, 400)
        self.assertEqual(second.status_code, 400)
        self.assertContains(second, "Too many incorrect", status_code=400)
        blocked = self.client.post(
            reverse("orientation_feedback:public_verify"),
            {"otp_code": actual_code},
        )
        self.assertEqual(blocked.status_code, 403)

        self.client.post(reverse("orientation_feedback:public_open"), {"public_token": session.public_token})
        self.client.post(
            reverse("orientation_feedback:public_validate"),
            {"public_token": session.public_token, "email": self.faculty.email},
        )
        participation = session.participations.get(user=self.faculty)
        participation.email_otp_expires_at = timezone.now() - timedelta(seconds=1)
        participation.save(update_fields=["email_otp_expires_at", "updated_at"])
        expired = self.client.post(
            reverse("orientation_feedback:public_verify"),
            {"otp_code": self.latest_otp_code()},
        )
        self.assertEqual(expired.status_code, 400)
        self.assertContains(expired, "expired", status_code=400)

    def test_complete_public_flow_and_duplicate_message(self):
        session = self.start(self.make_session())
        self.client.post(reverse("orientation_feedback:public_open"), {"public_token": session.public_token})
        self.client.post(
            reverse("orientation_feedback:public_validate"),
            {"public_token": session.public_token, "email": self.faculty.email},
        )
        self.client.post(
            reverse("orientation_feedback:public_verify"),
            {"otp_code": self.latest_otp_code()},
        )
        page = self.client.get(reverse("orientation_feedback:public_response"))
        self.assertContains(page, "Step 3 of 3")
        self.assertContains(page, 'name="q_overall_rating"')
        self.assertContains(page, 'class="orientation-options mt-3"')
        self.assertContains(page, "<fieldset", html=False)
        self.assertNotIn(
            "class",
            page.context["form"].fields["q_overall_rating"].widget.attrs,
        )
        self.assertNotIn(
            "class",
            page.context["form"].fields["q_guidance_areas"].widget.attrs,
        )
        self.assertNotContains(page, 'id="id_q_overall_rating" class="form-check-input"')
        self.assertNotContains(page, f'name="q_{session.questions.first().id}"')
        questions = session.questions.prefetch_related("choices").order_by("display_order")
        payload = {}
        for question in questions:
            key = f"q_{question.code}"
            if question.question_type == question.QuestionType.SCALE:
                payload[key] = "5"
            elif question.question_type == question.QuestionType.MULTI_SELECT:
                payload[key] = [question.choices.first().code]
            else:
                payload[key] = "Useful session"
        submitted = self.client.post(reverse("orientation_feedback:public_submit"), payload)
        self.assertRedirects(submitted, reverse("orientation_feedback:public_thanks"), fetch_redirect_response=False)
        self.assertEqual(session.responses.count(), 1)
        audit = AuditLog.objects.filter(action="ORIENTATION_SURVEY_PARTICIPATION_COMPLETED").latest("id")
        self.assertEqual(audit.metadata_json, {})
        self.assertIsNone(audit.ip_address)
        self.assertIsNone(audit.user_agent)
        self.assertIsNone(audit.route_name)

        self.client.post(reverse("orientation_feedback:public_open"), {"public_token": session.public_token})
        duplicate = self.client.post(
            reverse("orientation_feedback:public_validate"),
            {"public_token": session.public_token, "email": self.faculty.email},
        )
        self.assertEqual(duplicate.status_code, 409)
        self.assertContains(duplicate, "already submitted", status_code=409)

    def test_invalid_validation_is_neutral_clears_email_and_public_pages_are_not_indexed(self):
        session = self.start(self.make_session())
        entry = self.client.get(reverse("orientation_feedback:public_entry"))
        self.assertContains(entry, 'name="robots" content="noindex,nofollow,noarchive"')
        self.assertIn("no-store", entry["Cache-Control"])
        self.client.post(reverse("orientation_feedback:public_open"), {"public_token": session.public_token})
        invalid_email = "missing.person@example.edu"
        response = self.client.post(
            reverse("orientation_feedback:public_validate"),
            {"public_token": session.public_token, "email": invalid_email},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, OrientationSurveyPublicService.VALIDATION_ERROR, status_code=400)
        self.assertNotContains(response, invalid_email, status_code=400)
        malformed = self.client.post(
            reverse("orientation_feedback:public_open"),
            {"public_token": "not-a-valid-token"},
        )
        self.assertEqual(malformed.status_code, 404)

    @override_settings(
        ORIENTATION_FEEDBACK_BROWSER_RATE_LIMIT_PER_MINUTE=1,
        ORIENTATION_FEEDBACK_IP_RATE_LIMIT_PER_MINUTE=100,
    )
    def test_validation_rate_limit(self):
        session = self.start(self.make_session())
        request = RequestFactory().post("/orientation-feedback/validate/")
        OrientationSurveyRateLimitService.check(
            request=request,
            session=session,
            browser_hash="a" * 64,
            purpose="validate",
        )
        with self.assertRaises(OrientationSurveyRateLimited):
            OrientationSurveyRateLimitService.check(
                request=request,
                session=session,
                browser_hash="a" * 64,
                purpose="validate",
            )

    def test_submission_marks_all_post_values_sensitive_and_returns_safe_temporary_error(self):
        session = self.start(self.make_session())
        self.client.post(reverse("orientation_feedback:public_open"), {"public_token": session.public_token})
        self.client.post(
            reverse("orientation_feedback:public_validate"),
            {"public_token": session.public_token, "email": self.faculty.email},
        )
        self.client.post(
            reverse("orientation_feedback:public_verify"),
            {"otp_code": self.latest_otp_code()},
        )
        payload = self.response_payload(session, comment="private answer marker")
        with patch(
            "apps.orientation_feedback.views.OrientationSurveyResponseService.submit",
            side_effect=RuntimeError("forced failure"),
        ):
            response = self.client.post(reverse("orientation_feedback:public_submit"), payload)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.wsgi_request.sensitive_post_parameters, "__ALL__")
        self.assertNotContains(response, "private answer marker", status_code=503)


class OrientationSurveyAnalyticsPrivacyTests(OrientationFeedbackTestBase):
    @override_settings(ORIENTATION_FEEDBACK_MINIMUM_REPORT_RESPONSES=1)
    def test_reporting_threshold_cannot_be_configured_below_five(self):
        self.assertEqual(OrientationSurveyAnalyticsService.minimum_responses(), 5)

    def test_weighted_means_distributions_reverse_scoring_and_original_reporting(self):
        second = self.make_user("faculty-two", "faculty.two@example.edu", self.faculty_role)
        additional = self.add_faculty_respondents(3)
        session = self.start(self.make_session())
        self.submit_for(session, self.faculty, score_code="5", comment="=private formula")
        self.submit_for(session, second, score_code="3", comment="Second comment")
        for user in additional:
            self.submit_for(session, user, score_code="4", comment="")
        OrientationSurveySessionService.close(session=session, user=self.admin)
        analytics = OrientationSurveyAnalyticsService.build(session)
        overall = next(row for row in analytics["scale_rows"] if row["question"].code == "overall_rating")
        manual = next(
            row for row in analytics["scale_rows"] if row["question"].code == "manual_records_preference"
        )
        tech_index = next(row for row in analytics["indexes"] if row["code"] == "TECHNOLOGY_OPENNESS")
        self.assertEqual(overall["mean"], Decimal("4.00"))
        self.assertEqual(sum(item["count"] for item in overall["distributions"]), 5)
        self.assertEqual(manual["mean"], Decimal("4.00"))
        self.assertLess(tech_index["mean"], Decimal("4.00"))

    def test_analytics_and_csv_do_not_expose_identity_and_cancelled_export_is_marked(self):
        additional = self.add_faculty_respondents(4)
        session = self.start(self.make_session())
        self.submit_for(session, self.faculty, comment="Anonymous suggestion")
        for user in additional:
            self.submit_for(session, user, comment="")
        session = OrientationSurveySessionService.cancel(
            session=session,
            user=self.admin,
            reason="Facilitator ended the event early.",
        )
        self.client.force_login(self.admin)
        page = self.client.get(reverse("orientation_feedback:analytics", args=[session.public_id]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Cancelled session")
        self.assertContains(page, "Anonymous suggestion")
        for value in (self.faculty.email, self.faculty.username):
            self.assertNotContains(page, value)
        export = self.client.get(reverse("orientation_feedback:export", args=[session.public_id]))
        self.assertEqual(export.status_code, 200)
        content = export.content.decode("utf-8")
        self.assertIn("Cancelled", content)
        self.assertIn("Anonymous suggestion", content)
        self.assertNotIn(self.faculty.email, content)
        self.assertNotIn(self.faculty.username, content)

    def test_results_and_export_are_suppressed_below_five_for_both_survey_types(self):
        for survey_type, user in (
            (OrientationSurveySession.SurveyType.FACULTY, self.faculty),
            (OrientationSurveySession.SurveyType.ACADEMIC_HEADS, self.academic_head),
        ):
            with self.subTest(survey_type=survey_type):
                session = self.start(self.make_session(survey_type))
                self.submit_for(session, user, comment="must remain protected")
                session = OrientationSurveySessionService.cancel(
                    session=session,
                    user=self.admin,
                    reason="Cancelled privacy-threshold test",
                )
                analytics = OrientationSurveyAnalyticsService.build(session)
                self.assertFalse(analytics["results_released"])
                self.assertEqual(analytics["scale_rows"], [])
                self.assertEqual(analytics["comments"], [])
                self.client.force_login(self.admin)
                page = self.client.get(reverse("orientation_feedback:analytics", args=[session.public_id]))
                self.assertContains(page, "Detailed results are protected")
                self.assertNotContains(page, "must remain protected")
                export = self.client.get(reverse("orientation_feedback:export", args=[session.public_id]))
                self.assertEqual(export.status_code, 403)

    def test_academic_heads_personal_interaction_preference_is_not_reverse_scored(self):
        question = next(
            definition
            for definition in importlib.import_module(
                "apps.orientation_feedback.questions"
            ).definitions_for(OrientationSurveySession.SurveyType.ACADEMIC_HEADS)
            if definition["code"] == "personal_interaction_preference"
        )
        self.assertFalse(question["reverse_scored"])

    def test_five_academic_head_responses_release_cancelled_results(self):
        additional = self.add_head_respondents(4)
        session = self.start(self.make_session(OrientationSurveySession.SurveyType.ACADEMIC_HEADS))
        self.submit_for(session, self.academic_head, comment="Released head feedback")
        for user in additional:
            self.submit_for(session, user, comment="")
        session = OrientationSurveySessionService.cancel(
            session=session,
            user=self.admin,
            reason="Cancelled after completed Academic Heads orientation",
        )
        analytics = OrientationSurveyAnalyticsService.build(session)
        self.assertTrue(analytics["results_released"])
        self.assertEqual(analytics["completed"], 5)
        self.assertIn("Released head feedback", [row["text"] for row in analytics["comments"]])


class OrientationSurveyAuthorizationAndFeatureTests(OrientationFeedbackTestBase):
    def test_permissions_and_scope_are_enforced(self):
        session = self.make_session()
        unauthorized = User.objects.create_user(
            username="unauthorized",
            email="unauthorized@example.edu",
            password="pass",
            default_tenant=self.tenant,
            default_campus=self.campus,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        UserRole.objects.create(
            user=unauthorized,
            role=self.faculty_role,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.client.force_login(unauthorized)
        denied = self.client.get(reverse("orientation_feedback:session_list"))
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.admin)
        allowed = self.client.get(reverse("orientation_feedback:facilitator", args=[session.public_id]))
        self.assertEqual(allowed.status_code, 200)
        outside_session = self.make_session(tenant=self.other_tenant, campus=self.other_campus)
        outside = self.client.get(
            reverse("orientation_feedback:facilitator", args=[outside_session.public_id])
        )
        self.assertEqual(outside.status_code, 404)

    def test_feature_toggle_blocks_admin_and_public_access(self):
        session = self.start(self.make_session())
        SystemSettingService.set(
            FeatureSettingsService.ORIENTATION_FEEDBACK_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse("orientation_feedback:session_list"))
        self.assertEqual(admin_response.status_code, 403)
        menu_request = RequestFactory().get("/admin-portal/")
        menu_request.user = self.admin
        menu_request.scope = {"tenant_id": self.tenant.id, "campus_id": self.campus.id}
        menu_context = portal_menu(menu_request)
        menu_codes = {
            node["item"].code
            for group in menu_context["portal_menu"]
            for node in group["items"]
        }
        self.assertNotIn("ORIENTATION_FEEDBACK", menu_codes)
        self.client.logout()
        public = self.client.post(
            reverse("orientation_feedback:public_open"),
            {"public_token": session.public_token},
        )
        self.assertEqual(public.status_code, 200)
        self.assertContains(public, "currently unavailable")

    def test_disabled_feature_allows_read_only_access_to_ended_reports(self):
        additional = self.add_faculty_respondents(4)
        session = self.start(self.make_session())
        self.submit_for(session, self.faculty)
        for user in additional:
            self.submit_for(session, user, comment="")
        session = OrientationSurveySessionService.close(session=session, user=self.admin)
        SystemSettingService.set(
            FeatureSettingsService.ORIENTATION_FEEDBACK_ENABLED_KEY,
            False,
            tenant_id=self.tenant.id,
            value_type="BOOL",
            is_active=True,
        )
        self.client.force_login(self.admin)
        analytics = self.client.get(reverse("orientation_feedback:analytics", args=[session.public_id]))
        export = self.client.get(reverse("orientation_feedback:export", args=[session.public_id]))
        facilitator = self.client.get(reverse("orientation_feedback:facilitator", args=[session.public_id]))
        self.assertEqual(analytics.status_code, 200)
        self.assertEqual(export.status_code, 200)
        self.assertEqual(facilitator.status_code, 403)


class OrientationSurveyNavigationMigrationTests(OrientationFeedbackTestBase):
    def test_menu_seed_preserves_existing_shared_group_customization(self):
        group, _ = MenuGroup.objects.update_or_create(
            portal="ADMIN",
            code="IMPORTS",
            defaults={
                "label": "Campus Utilities",
                "icon": "custom-icon",
                "sort_order": 17,
                "is_active": False,
            },
        )
        migration = importlib.import_module(
            "apps.navigation.migrations.0013_seed_orientation_feedback_menu"
        )
        from django.apps import apps as django_apps

        migration.seed_menu(django_apps, None)
        group.refresh_from_db()
        self.assertEqual(group.label, "Campus Utilities")
        self.assertEqual(group.icon, "custom-icon")
        self.assertEqual(group.sort_order, 17)
        self.assertFalse(group.is_active)
