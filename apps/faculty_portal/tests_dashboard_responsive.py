from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import CourseOffering, FacultyAssignment, Section, TenantTermGradingPeriod, Term
from apps.academics.services import AcademicGovernanceService
from apps.auditlog.models import AuditLog
from apps.faculty_portal import tests_dashboard_updates as fixtures
from apps.faculty_portal.dashboard import DashboardActivity, DashboardData
from apps.faculty_portal.models import FacultyDashboardVisitState, FacultyGradebookModification
from apps.faculty_portal.services import FacultyDashboardUpdatesService
from apps.grading.models import (
    GradeEncodingControl, GradeSubmission, GradingPeriodLock, GradingTemplateComponent,
    GradingTemplateSubcomponent, StudentActivityScore,
)
from apps.grading.services import FacultyGradingService, GradingGovernanceService
from apps.notifications.models import SubmissionNonComplianceNotice
from apps.rbac.models import Permission, Role, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


class DashboardFixture:
    setUp = fixtures.FacultyDashboardUpdatesTests.setUp
    _create_faculty = fixtures.FacultyDashboardUpdatesTests._create_faculty
    _create_student = fixtures.FacultyDashboardUpdatesTests._create_student
    _create_active_assignment = fixtures.FacultyDashboardUpdatesTests._create_active_assignment
    _set_timestamp = fixtures.FacultyDashboardUpdatesTests._set_timestamp
    _create_enrollment = fixtures.FacultyDashboardUpdatesTests._create_enrollment
    _create_login_success = fixtures.FacultyDashboardUpdatesTests._create_login_success

    def dashboard(self):
        response = self.client.get(reverse("faculty_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        return response

    def add_offering(self, number, **overrides):
        section = Section.objects.create(
            tenant=self.tenant, campus=self.campus, department=self.department, program=self.program,
            code=f"DASH-{number}", name=f"Dashboard {number}",
        )
        values = dict(tenant=self.tenant, campus=self.campus, department=self.department, program=self.program,
                      academic_year=self.academic_year, term=self.term, course=self.course, section=section)
        values.update(overrides)
        offering = CourseOffering.objects.create(**values)
        FacultyAssignment.objects.create(
            tenant=offering.tenant, campus=offering.campus, offering=offering, faculty_user=self.faculty,
            accepted_at=timezone.now(), response_status="ACCEPTED",
        )
        return offering

    def make_activity(self):
        component, _ = GradingTemplateComponent.objects.get_or_create(
            template_period=self.template_period, code="EXAM", defaults={"name": "Exam", "weight_percentage": 100},
        )
        return FacultyGradingService.create_activity(
            user=self.faculty, offering=self.offering, template_period=self.template_period,
            template_component=component, template_subcomponent=None, template_detail=None,
            title="Exam 1", total_score=Decimal("100"), activity_date=timezone.localdate(),
        )


class FacultyDashboardResponsiveTests(DashboardFixture, TestCase):
    def test_one_row_and_unique_inline_destinations_for_valid_period(self):
        response = self.dashboard()
        row = response.context["stats"]["grade_status_rows"][0]
        self.assertEqual(len(response.context["stats"]["grade_status_rows"]), 1)
        self.assertEqual(row["review_url"], reverse("faculty_portal:period_summary", args=[self.offering.pk, self.template_period.pk]))
        self.assertEqual(row["performance_url"], reverse("faculty_portal:class_performance", args=[self.offering.pk, self.template_period.pk]))
        html = response.content.decode().split('<table class="table align-middle mb-0 dashboard-grade-table">')[1].split("</table>")[0]
        self.assertEqual(html.count("<tr>"), 2)  # one header and one complete class row
        self.assertEqual(html.count(f'href="{row["review_url"]}"'), 1)
        self.assertEqual(html.count(f'href="{row["performance_url"]}"'), 1)
        self.assertIn("dashboard-inline-actions", html)
        self.assertNotIn("faculty-mobile-hidden", html)

    def test_missing_period_has_open_class_without_invalid_period_links(self):
        self.template_period.is_active = False
        self.template_period.save(update_fields=["is_active"])
        stats = self.dashboard().context["stats"]
        row = stats["grade_status_rows"][0]
        self.assertIsNone(row["period"])
        self.assertEqual(row["review_url"], "")
        self.assertEqual(row["performance_url"], "")
        self.assertEqual(row["action_url"], reverse("faculty_portal:offering_periods", args=[self.offering.pk]))
        self.assertEqual(stats["pending_issue_count"], 1)
        self.assertIn("No active grading periods", stats["pending_grade_issues"][0]["message"])

    def test_no_template_is_an_issue_and_does_not_create_bad_links(self):
        self.template.is_published = False
        self.template.save(update_fields=["is_published"])
        stats = self.dashboard().context["stats"]
        self.assertEqual(stats["grade_status_rows"][0]["status"], "Invalid Setup")
        self.assertEqual(stats["pending_issue_count"], 1)
        self.assertFalse(stats["grade_status_rows"][0]["review_url"])

    def test_summary_counts_and_history_have_honest_empty_states(self):
        stats = self.dashboard().context["stats"]
        self.assertEqual(stats["active_classes"], 1)
        self.assertEqual(stats["submitted_periods"], 0)
        self.assertIsNone(stats["previous_visit"])
        self.assertIsNone(stats["last_update"])
        self.assertIsNone(stats["non_compliance"]["total"])

    def test_visit_uses_dashboard_history_and_refresh_does_not_write_again(self):
        self._create_login_success(timezone.now() - timedelta(days=2))
        self._create_login_success(timezone.now() - timedelta(days=1))
        first = self.dashboard()
        self.assertIsNone(first.context["stats"]["previous_visit"])
        first_visit = AuditLog.objects.get(action=DashboardActivity.VISIT_ACTION)
        self.assertIsNone(self.dashboard().context["stats"]["previous_visit"])
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 1)
        self.client = Client()
        self.client.force_login(self.faculty)
        second = self.dashboard()
        self.assertEqual(second.context["stats"]["previous_visit"], first_visit.created_at)
        expected = first_visit.created_at.astimezone(ZoneInfo("Asia/Manila")).strftime("%b")
        self.assertContains(second, expected)
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 2)

    def test_failed_render_does_not_record_a_visit(self):
        with patch("apps.faculty_portal.views.render", side_effect=RuntimeError("render failed")):
            with self.assertRaisesMessage(RuntimeError, "render failed"):
                self.client.get(reverse("faculty_portal:dashboard"))
        self.assertFalse(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).exists())

    def test_head_does_not_record_a_visit(self):
        self.assertEqual(self.client.head(reverse("faculty_portal:dashboard")).status_code, 200)
        self.assertFalse(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).exists())

    def test_expired_visit_window_and_malformed_history(self):
        self.dashboard()
        FacultyDashboardVisitState.objects.update(window_started_at=timezone.now() - timedelta(hours=1))
        session = self.client.session
        key = f"faculty_dashboard_visit:{self.tenant.pk}:{self.campus.pk}"
        session[key] = {"started": (timezone.now() - timedelta(hours=1)).isoformat(), "previous": 123}
        session.save()
        response = self.dashboard()
        self.assertIsNotNone(response.context["stats"]["previous_visit"])
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 2)

    def test_direct_deny_precedes_dashboard_and_visit_recording(self):
        UserPermission.objects.create(
            user=self.faculty, permission=Permission.objects.get(code="dashboard.read"),
            grant_type="DENY", tenant=self.tenant, campus=self.campus,
        )
        response = self.client.get(reverse("faculty_portal:dashboard"))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).exists())

    def test_active_term_and_assignment_scope_exclude_other_records(self):
        AcademicGovernanceService.set_active_scope(tenant_id=self.tenant.pk, academic_year=self.academic_year, term=self.term)
        other_term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year, code="2ND", name="Second", sequence_no=2)
        outside = self.add_offering(2, term=other_term)
        stats = self.dashboard().context["stats"]
        self.assertEqual(stats["active_classes"], 1)
        self.assertEqual([row["offering"].pk for row in stats["grade_status_rows"]], [self.offering.pk])
        self.assertNotIn(outside.pk, [row["offering"].pk for row in stats["grade_status_rows"]])

    def test_submitted_period_keeps_review_and_never_gets_upcoming_deadline_update(self):
        self._create_login_success(timezone.now() - timedelta(days=2))
        self._create_login_success(timezone.now() - timedelta(days=1))
        active_period = TenantTermGradingPeriod.objects.create(
            tenant=self.tenant, term=self.term, code="PRELIM", name="Prelim", sequence_no=1,
        )
        AcademicGovernanceService.set_active_grading_period(
            tenant_id=self.tenant.pk, campus=self.campus, term=self.term, period=active_period, actor=self.faculty,
        )
        GradeSubmission.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
            template_period=self.template_period, status="SUBMITTED", submitted_by_user=self.faculty)
        GradingPeriodLock.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, period_code=self.template_period.code, scope_type="CAMPUS", is_locked=True,
            deadline_at=timezone.now() + timedelta(hours=24))
        stats = self.dashboard().context["stats"]
        self.assertEqual(stats["submitted_periods"], 1)
        self.assertEqual(stats["grade_status_rows"][0]["status"], "Locked")
        self.assertTrue(stats["grade_status_rows"][0]["review_url"])
        self.assertFalse(stats["deadline_reminder"]["has_deadline"])
        self.assertIn("All configured period grades submitted", stats["deadline_reminder"]["title"])
        self.assertFalse(any(item["source"] == "deadline_reminder" for item in stats["updates_summary"]["items"]))

    def test_encoding_control_and_course_lock_precedence_match_services(self):
        for scope_type, offering, locked in [("CAMPUS", None, False), ("COURSE", self.offering, True)]:
            GradingPeriodLock.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
                term=self.term, period_code="PRELIM", scope_type=scope_type, course_offering=offering, is_locked=locked)
        control = GradeEncodingControl.objects.create(tenant=self.tenant, academic_year=self.academic_year,
            term=self.term, campus=self.campus, course_offering=self.offering, status="CLOSED", reason="test")
        data = DashboardData([self.offering], now=timezone.now())
        self.assertEqual(data.lock(self.offering, self.template_period).pk,
            GradingGovernanceService.resolve_lock(offering=self.offering, template_period=self.template_period).pk)
        self.assertEqual(data.closed_control(self.offering, self.template_period).pk, control.pk)
        self.assertEqual(self.dashboard().context["stats"]["grade_status_rows"][0]["status"], "Encoding Closed")

    def test_readiness_batch_matches_governance_for_scores_attendance_and_empty_roster(self):
        self._create_enrollment(self.student_1, self.offering, timezone.now())
        self._create_enrollment(self.student_2, self.offering, timezone.now())
        activity = self.make_activity()
        FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=activity,
            score_payload=[{"student_id": self.student_1.pk, "raw_score": "75"}], recompute=False)
        GradingTemplateSubcomponent.objects.create(template_component=activity.template_component, code="ATT", name="Attendance",
            weight_percentage=100, is_attendance_component=True)
        session, _ = FacultyGradingService.create_or_update_attendance_session(user=self.faculty, offering=self.offering,
            template_period=self.template_period, session_date=timezone.localdate())
        FacultyGradingService.upsert_attendance_records(user=self.faculty, session=session,
            status_payload=[{"student_id": self.student_2.pk, "status_code": "P"}])
        for offering in [self.offering, self.other_offering]:
            data = DashboardData([offering], now=timezone.now())
            actual = data.readiness(offering, self.template_period)
            expected = GradingGovernanceService.evaluate_submission_readiness(offering=offering, template_period=self.template_period)
            for key, value in actual.items():
                self.assertEqual(value, expected[key], key)

    def test_batch_does_not_load_other_campus_or_unowned_course_controls(self):
        campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other")
        GradeEncodingControl.objects.create(tenant=self.tenant, academic_year=self.academic_year, term=self.term,
            campus=campus, status="CLOSED", reason="Other campus")
        GradeEncodingControl.objects.create(tenant=self.tenant, academic_year=self.academic_year, term=self.term,
            campus=self.campus, course_offering=self.other_offering, status="CLOSED", reason="Another class")
        GradingPeriodLock.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, period_code="PRELIM", scope_type="COURSE", course_offering=self.other_offering)
        data = DashboardData([self.offering], now=timezone.now())
        self.assertEqual(data.controls, [])
        self.assertEqual(data.locks, [])

    def test_notice_rows_include_resolved_and_exclude_retries_other_faculty_and_term(self):
        AcademicGovernanceService.set_active_scope(tenant_id=self.tenant.pk, academic_year=self.academic_year, term=self.term)
        base = dict(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.template_period,
                    faculty_user=self.faculty, notice_level="NOTICE", title="Notice", message="Notice",
                    deadline_at=timezone.now(), issued_at=timezone.now(), sequence_no=1)
        SubmissionNonComplianceNotice.objects.create(**base, status="RESOLVED", email_attempt_count=7)
        SubmissionNonComplianceNotice.objects.create(**{**base, "faculty_user": self.other_faculty})
        other_term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year, code="2ND", name="Second", sequence_no=2)
        outside = self.add_offering(2, term=other_term)
        SubmissionNonComplianceNotice.objects.create(**{**base, "offering": outside})
        with patch("apps.faculty_portal.dashboard.FeatureSettingsService.is_submission_non_compliance_notice_enabled", return_value=True):
            metric = self.dashboard().context["stats"]["non_compliance"]
        self.assertEqual(metric["recorded_count"], 1)
        self.assertIsNone(metric["total"])
        self.assertIn("duplicate-free", metric["explanation"])

    def test_duplicate_notice_rows_are_not_presented_as_a_verified_total(self):
        AcademicGovernanceService.set_active_scope(tenant_id=self.tenant.pk, academic_year=self.academic_year, term=self.term)
        for _ in range(2):
            SubmissionNonComplianceNotice.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
                template_period=self.template_period, faculty_user=self.faculty, notice_level="NOTICE", sequence_no=1,
                title="Notice", message="Notice", deadline_at=timezone.now(), issued_at=timezone.now())
        with patch("apps.faculty_portal.dashboard.FeatureSettingsService.is_submission_non_compliance_notice_enabled", return_value=True):
            metric = self.dashboard().context["stats"]["non_compliance"]
        self.assertEqual(metric["recorded_count"], 2)
        self.assertIsNone(metric["total"])

    def test_current_campus_excludes_other_campus_classes_and_modification_state(self):
        from apps.academics.models import Course
        campus = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other")
        department = Department.objects.create(tenant=self.tenant, campus=campus, code="DEPT", name="Department")
        program = Program.objects.create(tenant=self.tenant, campus=campus, department=department, code="PROG", name="Program")
        section = Section.objects.create(tenant=self.tenant, campus=campus, department=department, program=program, code="OTHER", name="Other")
        course = Course.objects.create(tenant=self.tenant, campus=campus, department=department, code="OTHER", title="Other")
        offering = self.add_offering(9, campus=campus, department=department, program=program, section=section, course=course)
        FacultyGradebookModification.objects.create(faculty_user=self.faculty, offering=offering,
            template_period=self.template_period, modified_at=timezone.now())
        stats = self.dashboard().context["stats"]
        self.assertEqual(stats["active_classes"], 1)
        self.assertIsNone(stats["last_update"])

    def test_foreign_tenant_scope_cannot_read_original_class_or_update(self):
        self.make_activity()
        tenant = Tenant.objects.create(code="FOREIGN", name="Foreign")
        UserRole.objects.create(user=self.faculty, role=Role.objects.get(code="FACULTY"), tenant=tenant)
        self.faculty.default_tenant = tenant
        self.faculty.default_campus = None
        self.faculty.default_department = None
        self.faculty.save(update_fields=["default_tenant", "default_campus", "default_department"])
        stats = self.dashboard().context["stats"]
        self.assertEqual(stats["active_classes"], 0)
        self.assertIsNone(stats["last_update"])
        self.assertIsNone(stats["previous_visit"])

    def test_query_growth_is_bounded_for_one_eight_and_twenty_four_classes(self):
        measurements = []
        count = 1
        self._create_enrollment(self.student_1, self.offering, timezone.now())
        self._create_login_success(timezone.now() - timedelta(days=2))
        self._create_login_success(timezone.now() - timedelta(days=1))
        # Class-count growth is measured independently of fixed first-visit
        # claim/audit writes. Cold-visit cost is covered by the external probe.
        self.dashboard()
        for target in (1, 8, 24):
            while count < target:
                offering = self.add_offering(count)
                self._create_enrollment(self.student_1, offering, timezone.now())
                count += 1
            with CaptureQueriesContext(connection) as queries:
                response = self.dashboard()
            self.assertEqual(len(response.context["stats"]["grade_status_rows"]), target)
            measurements.append(len(queries))
        self.assertLessEqual(max(measurements) - min(measurements), 12, measurements)
        self.assertLess(max(measurements), 150, measurements)

    def test_dashboard_update_sources_and_login_anchor_are_bounded(self):
        past = timezone.now() - timedelta(days=2)
        self._create_login_success(past)
        self._create_login_success(past + timedelta(days=1))
        for number in range(10):
            student = self._create_student(f"BOUNDED-{number}", f"Student{number}", "Bounded")
            self._create_enrollment(student, self.offering, past + timedelta(hours=number + 1))
        data = DashboardData([self.offering], now=timezone.now())
        with CaptureQueriesContext(connection) as queries:
            updates = FacultyDashboardUpdatesService.get_dashboard_updates(
                user=self.faculty, offerings=[self.offering], dashboard_data=data,
            )
        self.assertEqual(len(updates["items"]), 5)
        self.assertTrue(updates["has_more"])
        self.assertTrue(any("LIMIT 2" in row["sql"] and "audit_logs" in row["sql"] for row in queries))
        self.assertTrue(any("LIMIT 6" in row["sql"] and "enrollments" in row["sql"] for row in queries))


class FacultyGradebookActivityTests(DashboardFixture, TestCase):
    def setUp(self):
        super().setUp()
        self._create_enrollment(self.student_1, self.offering, timezone.now())

    def state(self):
        return FacultyGradebookModification.objects.get(faculty_user=self.faculty, offering=self.offering, template_period=self.template_period)

    def test_activity_create_edit_and_archive_are_tracked(self):
        activity = self.make_activity()
        self.assertEqual(self.state().faculty_user_id, self.faculty.pk)
        past = timezone.now() - timedelta(days=1)
        FacultyGradebookModification.objects.update(modified_at=past)
        FacultyGradingService.update_activity(user=self.faculty, activity=activity, template_period=self.template_period,
            template_component=activity.template_component, template_subcomponent=None, template_detail=None,
            title="Renamed", total_score=activity.total_score, activity_date=activity.activity_date)
        self.assertGreater(self.state().modified_at, past)
        FacultyGradebookModification.objects.update(modified_at=past)
        FacultyGradingService.archive_activity(user=self.faculty, activity=activity)
        self.assertGreater(self.state().modified_at, past)

    def test_noop_activity_definition_and_attendance_session_do_not_advance_marker(self):
        activity = self.make_activity()
        first = self.state().modified_at
        FacultyGradingService.update_activity(user=self.faculty, activity=activity, template_period=self.template_period,
            template_component=activity.template_component, template_subcomponent=None, template_detail=None,
            title=activity.title, total_score=activity.total_score, activity_date=activity.activity_date)
        self.assertEqual(self.state().modified_at, first)
        FacultyGradingService.create_or_update_attendance_session(user=self.faculty, offering=self.offering,
            template_period=self.template_period, session_date=timezone.localdate())
        second = self.state().modified_at
        FacultyGradingService.create_or_update_attendance_session(user=self.faculty, offering=self.offering,
            template_period=self.template_period, session_date=timezone.localdate())
        self.assertEqual(self.state().modified_at, second)

    def test_score_save_clear_and_noop_are_distinguished(self):
        activity = self.make_activity()
        past = timezone.now() - timedelta(days=1)
        FacultyGradebookModification.objects.update(modified_at=past)
        payload = [{"student_id": self.student_1.pk, "raw_score": "80"}]
        FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=activity, score_payload=payload, recompute=False)
        self.assertGreater(self.state().modified_at, past)
        timestamp = self.state().modified_at
        FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=activity, score_payload=payload, recompute=False)
        self.assertEqual(self.state().modified_at, timestamp)
        FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=activity,
            score_payload=[{"student_id": self.student_1.pk, "clear": True}], recompute=False)
        self.assertGreater(self.state().modified_at, timestamp)

    def test_attendance_session_and_records_are_tracked_but_noop_is_not(self):
        session, _ = FacultyGradingService.create_or_update_attendance_session(user=self.faculty, offering=self.offering,
            template_period=self.template_period, session_date=timezone.localdate(), title="Meeting")
        first = self.state().modified_at
        payload = [{"student_id": self.student_1.pk, "status_code": "A"}]
        FacultyGradingService.upsert_attendance_records(user=self.faculty, session=session, status_payload=payload)
        self.assertGreater(self.state().modified_at, first)
        second = self.state().modified_at
        FacultyGradingService.upsert_attendance_records(user=self.faculty, session=session, status_payload=payload)
        self.assertEqual(self.state().modified_at, second)

    def test_rollback_discards_activity_and_marker(self):
        with self.assertRaisesMessage(RuntimeError, "rollback"):
            with transaction.atomic():
                self.make_activity()
                self.assertTrue(FacultyGradebookModification.objects.exists())
                raise RuntimeError("rollback")
        self.assertFalse(FacultyGradebookModification.objects.exists())

    def test_score_rollback_preserves_previous_marker(self):
        activity = self.make_activity()
        first = self.state().modified_at
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=activity,
                    score_payload=[{"student_id": self.student_1.pk, "raw_score": "50"}], recompute=False)
                raise RuntimeError("rollback")
        self.assertEqual(self.state().modified_at, first)
        self.assertFalse(StudentActivityScore.objects.filter(activity=activity).exists())

    def test_derived_grade_and_other_faculty_edits_do_not_advance_own_marker(self):
        activity = self.make_activity()
        first = self.state().modified_at
        FacultyGradingService.recompute_period_summary(user=self.faculty, offering=self.offering, template_period=self.template_period)
        self.assertEqual(self.state().modified_at, first)
        FacultyGradingService.upsert_activity_scores(user=self.other_faculty, activity=activity,
            score_payload=[{"student_id": self.student_1.pk, "raw_score": "70"}], recompute=False)
        self.assertEqual(self.state().modified_at, first)
        self.assertEqual(FacultyGradebookModification.objects.count(), 1)

    def test_submitted_gradebook_blocks_write_without_advancing_marker(self):
        activity = self.make_activity()
        first = self.state().modified_at
        GradeSubmission.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
            template_period=self.template_period, status="SUBMITTED", submitted_by_user=self.faculty)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=activity,
                score_payload=[{"student_id": self.student_1.pk, "raw_score": "10"}], recompute=False)
        self.assertEqual(self.state().modified_at, first)

    def test_dashboard_last_update_is_owned_and_in_active_academic_scope(self):
        self.make_activity()
        self.assertEqual(self.dashboard().context["stats"]["last_update"], self.state().modified_at)
        own = self.state().modified_at
        FacultyGradebookModification.objects.create(faculty_user=self.other_faculty, offering=self.offering,
            template_period=self.template_period, modified_at=own + timedelta(days=1))
        self.assertEqual(self.dashboard().context["stats"]["last_update"], own)
