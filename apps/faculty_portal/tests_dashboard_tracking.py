"""Deterministic persisted-value/unique-insert interleavings, plus live DB gates."""

from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Event, Thread, current_thread
from unittest import skipUnless
from unittest.mock import patch

from django.db import IntegrityError, OperationalError, close_old_connections, connection, transaction
from django.db.models.query import QuerySet
from django.test import Client, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.auditlog.models import AuditLog
from apps.faculty_portal.dashboard import DashboardActivity
from apps.faculty_portal.models import FacultyDashboardVisitState, FacultyGradebookModification
from apps.faculty_portal.tests_dashboard_responsive import DashboardFixture
from apps.grading.models import GradeActivity, StudentActivityScore
from apps.grading.services import FacultyGradingService


class TrackingFixture(DashboardFixture):
    def setUp(self):
        super().setUp()
        self._create_enrollment(self.student_1, self.offering, timezone.now())
        self.activity = self.make_activity()
        self.session, _ = FacultyGradingService.create_or_update_attendance_session(
            user=self.faculty, offering=self.offering, template_period=self.template_period,
            session_date=timezone.localdate(),
        )
        self.old_time = timezone.now() - timedelta(days=2)
        FacultyGradebookModification.objects.update(modified_at=self.old_time)

    def marker(self):
        return FacultyGradebookModification.objects.get(
            faculty_user=self.faculty, offering=self.offering, template_period=self.template_period,
        ).modified_at

    def score(self, value):
        return FacultyGradingService.upsert_activity_scores(
            user=self.faculty, activity=self.activity,
            score_payload=[dict(student_id=self.student_1.pk, raw_score=value)], recompute=False,
        )

    def attendance(self, value):
        return FacultyGradingService.upsert_attendance_records(
            user=self.faculty, session=self.session,
            status_payload=[dict(student_id=self.student_1.pk, status_code=value)],
        )


class GradebookPersistedValueInterleavingTests(TrackingFixture, TestCase):
    @contextmanager
    def before_record_lock(self, model, parent, mutate):
        """Simulate a competitor committing just before the current row is locked.

        This is a deterministic control-flow/DB-state test, not a claim that
        SQLite implements SELECT FOR UPDATE. The live MariaDB tests below gate
        actual waiting/commit visibility across separate connections.
        """
        original = QuerySet.get
        events = []

        def get(queryset, *args, **kwargs):
            if queryset.query.select_for_update and queryset.model is parent:
                events.append("parent")
            if queryset.query.select_for_update and queryset.model is model and "record" not in events:
                events.append("record")
                mutate()
            return original(queryset, *args, **kwargs)

        with patch.object(QuerySet, "get", get):
            yield
        self.assertEqual(events[:2], ["parent", "record"])

    def test_score_change_after_early_snapshot_is_marked(self):
        row = StudentActivityScore.objects.create(activity=self.activity, student=self.student_1, raw_score=80)
        with self.before_record_lock(StudentActivityScore, GradeActivity,
                lambda: StudentActivityScore.objects.filter(pk=row.pk).update(raw_score=65)):
            self.score("80")
        self.assertGreater(self.marker(), self.old_time)
        row.refresh_from_db()
        self.assertEqual(row.raw_score, Decimal("80"))

    def test_score_concurrent_value_already_equal_is_a_noop(self):
        row = StudentActivityScore.objects.create(activity=self.activity, student=self.student_1, raw_score=65)
        with self.before_record_lock(StudentActivityScore, GradeActivity,
                lambda: StudentActivityScore.objects.filter(pk=row.pk).update(raw_score=80)):
            self.score("80")
        self.assertEqual(self.marker(), self.old_time)

    def test_concurrent_first_score_insert_equal_is_a_noop(self):
        with self.before_record_lock(StudentActivityScore, GradeActivity,
                lambda: StudentActivityScore.objects.create(activity=self.activity, student=self.student_1, raw_score=80)):
            self.score("80")
        self.assertEqual(StudentActivityScore.objects.filter(activity=self.activity).count(), 1)
        self.assertEqual(self.marker(), self.old_time)

    def test_first_score_save_and_clear_advance_marker_but_absent_clear_does_not(self):
        clear = [dict(student_id=self.student_1.pk, clear=True)]
        FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=self.activity, score_payload=clear, recompute=False)
        self.assertEqual(self.marker(), self.old_time)
        self.score("80")
        self.assertGreater(self.marker(), self.old_time)
        FacultyGradebookModification.objects.update(modified_at=self.old_time)
        FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=self.activity, score_payload=clear, recompute=False)
        self.assertGreater(self.marker(), self.old_time)
        FacultyGradebookModification.objects.update(modified_at=self.old_time)
        FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=self.activity, score_payload=clear, recompute=False)
        self.assertEqual(self.marker(), self.old_time)

    def test_clear_observes_concurrent_reactivation_under_lock(self):
        row = StudentActivityScore.objects.create(activity=self.activity, student=self.student_1, raw_score=80, is_active=False)
        with self.before_record_lock(StudentActivityScore, GradeActivity,
                lambda: StudentActivityScore.objects.filter(pk=row.pk).update(is_active=True)):
            FacultyGradingService.upsert_activity_scores(user=self.faculty, activity=self.activity,
                score_payload=[dict(student_id=self.student_1.pk, clear=True)], recompute=False)
        self.assertGreater(self.marker(), self.old_time)
        row.refresh_from_db()
        self.assertFalse(row.is_active)

    def test_attendance_change_after_early_snapshot_is_marked(self):
        row = AttendanceRecord.objects.create(session=self.session, student=self.student_1, status_code="P")
        with self.before_record_lock(AttendanceRecord, AttendanceSession,
                lambda: AttendanceRecord.objects.filter(pk=row.pk).update(status_code="A")):
            self.attendance("P")
        self.assertGreater(self.marker(), self.old_time)
        row.refresh_from_db()
        self.assertEqual(row.status_code, "P")

    def test_attendance_concurrent_value_already_equal_is_a_noop(self):
        row = AttendanceRecord.objects.create(session=self.session, student=self.student_1, status_code="A")
        with self.before_record_lock(AttendanceRecord, AttendanceSession,
                lambda: AttendanceRecord.objects.filter(pk=row.pk).update(status_code="P")):
            self.attendance("P")
        self.assertEqual(self.marker(), self.old_time)

    def test_concurrent_first_attendance_insert_equal_is_a_noop(self):
        with self.before_record_lock(AttendanceRecord, AttendanceSession,
                lambda: AttendanceRecord.objects.create(session=self.session, student=self.student_1, status_code="P")):
            self.attendance("P")
        self.assertEqual(AttendanceRecord.objects.filter(session=self.session).count(), 1)
        self.assertEqual(self.marker(), self.old_time)

    def test_first_attendance_and_reactivation_are_marked(self):
        self.attendance("A")
        self.assertGreater(self.marker(), self.old_time)
        AttendanceRecord.objects.filter(session=self.session).update(is_active=False)
        FacultyGradebookModification.objects.update(modified_at=self.old_time)
        self.attendance("A")
        self.assertGreater(self.marker(), self.old_time)

    def test_outer_rollback_discards_score_attendance_and_markers(self):
        with self.assertRaisesMessage(RuntimeError, "rollback"):
            with transaction.atomic():
                self.score("80")
                self.attendance("P")
                self.assertGreater(self.marker(), self.old_time)
                raise RuntimeError("rollback")
        self.assertFalse(StudentActivityScore.objects.filter(activity=self.activity).exists())
        self.assertFalse(AttendanceRecord.objects.filter(session=self.session).exists())
        self.assertEqual(self.marker(), self.old_time)

    def test_marker_failure_rolls_back_both_supported_write_paths(self):
        for write in (lambda: self.score("80"), lambda: self.attendance("P")):
            with self.subTest(write=write), patch("apps.grading.services.record_gradebook_modification",
                                                side_effect=OperationalError("marker unavailable")):
                with self.assertRaises(OperationalError):
                    write()
            self.assertFalse(StudentActivityScore.objects.filter(activity=self.activity).exists())
            self.assertFalse(AttendanceRecord.objects.filter(session=self.session).exists())
            self.assertEqual(self.marker(), self.old_time)


class GradebookMetadataInterleavingTests(TrackingFixture, TestCase):
    def update_activity(self, **overrides):
        values = dict(user=self.faculty, activity=self.activity, template_period=self.template_period,
            template_component=self.activity.template_component, template_subcomponent=None, template_detail=None,
            title=self.activity.title, total_score=self.activity.total_score, activity_date=self.activity.activity_date)
        values.update(overrides)
        return FacultyGradingService.update_activity(**values)

    def update_session(self, **overrides):
        values = dict(user=self.faculty, offering=self.offering, template_period=self.template_period,
            session_date=self.session.session_date, title=self.session.title)
        values.update(overrides)
        return FacultyGradingService.create_or_update_attendance_session(**values)

    @contextmanager
    def before_metadata_lock(self, model, mutate):
        # A deterministic commit just before the locking read; SQLite itself
        # does not provide the row-lock waiting exercised by the MariaDB gate.
        original, events = QuerySet.get, []

        def get(queryset, *args, **kwargs):
            if queryset.model is model and queryset.query.select_for_update and not events:
                self.assertTrue(connection.in_atomic_block)
                events.append("locked read")
                mutate()
            return original(queryset, *args, **kwargs)

        with patch.object(QuerySet, "get", get):
            yield
        self.assertEqual(events, ["locked read"])

    def test_missed_activity_timestamp_from_stale_caller(self):
        GradeActivity.objects.filter(pk=self.activity.pk).update(title="Competing edit")
        self.update_activity()  # Reverts to the caller's old title: a real write.
        self.assertGreater(self.marker(), self.old_time)
        self.assertEqual(GradeActivity.objects.get(pk=self.activity.pk).title, self.activity.title)

    def test_noop_activity_with_stale_caller_does_not_mark(self):
        GradeActivity.objects.filter(pk=self.activity.pk).update(title="Already saved")
        self.update_activity(title="Already saved")
        self.assertEqual(self.marker(), self.old_time)

    def test_missed_session_timestamp_after_competing_update(self):
        with self.before_metadata_lock(AttendanceSession,
                lambda: AttendanceSession.objects.filter(pk=self.session.pk).update(title="Competing edit")):
            self.update_session()
        self.assertGreater(self.marker(), self.old_time)
        self.assertIsNone(AttendanceSession.objects.get(pk=self.session.pk).title)

    def test_noop_session_after_competing_update_does_not_mark(self):
        with self.before_metadata_lock(AttendanceSession,
                lambda: AttendanceSession.objects.filter(pk=self.session.pk).update(title="Already saved")):
            self.update_session(title="Already saved")
        self.assertEqual(self.marker(), self.old_time)

    def test_repeated_archive_with_stale_active_caller_does_not_mark(self):
        stale = GradeActivity.objects.get(pk=self.activity.pk)
        FacultyGradingService.archive_activity(user=self.faculty, activity=self.activity)
        first = self.marker()
        FacultyGradingService.archive_activity(user=self.faculty, activity=stale)
        self.assertEqual(self.marker(), first)

    def test_activity_competing_change_before_lock_is_marked(self):
        with self.before_metadata_lock(GradeActivity,
                lambda: GradeActivity.objects.filter(pk=self.activity.pk).update(total_score=90)):
            self.update_activity()
        self.assertGreater(self.marker(), self.old_time)

    def test_activity_competing_equal_value_before_lock_is_noop(self):
        with self.before_metadata_lock(GradeActivity,
                lambda: GradeActivity.objects.filter(pk=self.activity.pk).update(title="Already saved")):
            self.update_activity(title="Already saved")
        self.assertEqual(self.marker(), self.old_time)

    def test_archive_stale_inactive_caller_observes_real_active_state(self):
        self.activity.is_active = False
        FacultyGradingService.archive_activity(user=self.faculty, activity=self.activity)
        self.assertGreater(self.marker(), self.old_time)
        self.assertFalse(GradeActivity.objects.get(pk=self.activity.pk).is_active)

    def test_archive_observes_competing_archive_under_lock(self):
        with self.before_metadata_lock(GradeActivity,
                lambda: GradeActivity.objects.filter(pk=self.activity.pk).update(is_active=False)):
            FacultyGradingService.archive_activity(user=self.faculty, activity=self.activity)
        self.assertEqual(self.marker(), self.old_time)

    def test_session_first_creation_and_concurrent_equal_insert(self):
        day = self.session.session_date + timedelta(days=1)
        row, created = self.update_session(session_date=day, title="New session")
        self.assertTrue(created)
        self.assertGreater(self.marker(), self.old_time)
        FacultyGradebookModification.objects.update(modified_at=self.old_time)
        next_day = day + timedelta(days=1)
        with self.before_metadata_lock(AttendanceSession, lambda: AttendanceSession.objects.create(
                tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.template_period,
                session_date=next_day, title="New session", created_by_user=self.faculty)):
            row, created = self.update_session(session_date=next_day, title="New session")
        self.assertFalse(created)
        self.assertEqual(self.marker(), self.old_time)
        self.assertEqual(AttendanceSession.objects.filter(offering=self.offering, session_date=next_day).count(), 1)

    def test_session_reactivation_and_normalized_title(self):
        AttendanceSession.objects.filter(pk=self.session.pk).update(title="Meeting", is_active=False)
        self.update_session(title=" Meeting ")
        self.assertGreater(self.marker(), self.old_time)
        FacultyGradebookModification.objects.update(modified_at=self.old_time)
        self.update_session(title=" Meeting ")
        self.assertEqual(self.marker(), self.old_time)

    def test_session_unique_insert_collision_compares_winning_persisted_value(self):
        for changed in (True, False):
            day = self.session.session_date + timedelta(days=3 + int(changed))
            original, collided = QuerySet.get, []

            def get(queryset, *args, **kwargs):
                if queryset.model is AttendanceSession and queryset.query.select_for_update and not collided:
                    try:
                        return original(queryset, *args, **kwargs)
                    except AttendanceSession.DoesNotExist:
                        collided.append(True)
                        AttendanceSession.objects.create(tenant=self.tenant, campus=self.campus,
                            offering=self.offering, template_period=self.template_period, session_date=day,
                            title="Competitor" if changed else "New session", created_by_user=self.faculty)
                        raise
                return original(queryset, *args, **kwargs)

            FacultyGradebookModification.objects.update(modified_at=self.old_time)
            with self.subTest(changed=changed), patch.object(QuerySet, "get", get):
                row, created = self.update_session(session_date=day, title="New session")
                self.assertFalse(created)
                self.assertEqual(row.title, "New session")
                self.assertEqual(collided, [True])
                self.assertEqual(AttendanceSession.objects.filter(offering=self.offering, session_date=day).count(), 1)
                if changed:
                    self.assertGreater(self.marker(), self.old_time)
                else:
                    self.assertEqual(self.marker(), self.old_time)

    def test_missing_and_wrong_scope_activity_cannot_write_or_mark(self):
        for write in (self.update_activity,
                lambda **kwargs: FacultyGradingService.archive_activity(user=self.faculty, **kwargs)):
            for field, value in (("pk", 999999999), ("tenant_id", 999999999), ("campus_id", 999999999),
                                  ("offering_id", 999999999)):
                stale = GradeActivity.objects.get(pk=self.activity.pk)
                setattr(stale, field, value)
                with self.subTest(write=write, field=field), self.assertRaises(GradeActivity.DoesNotExist):
                    write(activity=stale)
        self.assertEqual(self.marker(), self.old_time)
        self.assertTrue(GradeActivity.objects.get(pk=self.activity.pk).is_active)

    def test_all_metadata_writes_and_markers_rollback_together(self):
        score = StudentActivityScore.objects.create(activity=self.activity, student=self.student_1, raw_score=80,
                                                   computed_score=80)
        new_day = self.session.session_date + timedelta(days=1)
        with self.assertRaisesMessage(RuntimeError, "rollback"):
            with transaction.atomic():
                updated, count = self.update_activity(title="Changed", total_score=Decimal("50"))
                self.assertEqual(count, 1)
                self.update_session(title="Changed")
                self.update_session(session_date=new_day, title="Created inside rollback")
                FacultyGradingService.archive_activity(user=self.faculty, activity=updated)
                self.assertGreater(self.marker(), self.old_time)
                raise RuntimeError("rollback")
        self.activity.refresh_from_db()
        self.session.refresh_from_db()
        score.refresh_from_db()
        self.assertEqual(self.activity.title, "Exam 1")
        self.assertTrue(self.activity.is_active)
        self.assertIsNone(self.session.title)
        self.assertFalse(AttendanceSession.objects.filter(offering=self.offering, session_date=new_day).exists())
        self.assertTrue(score.is_active)
        self.assertEqual(score.computed_score, Decimal("80"))
        self.assertEqual(self.marker(), self.old_time)

    def test_marker_failure_rolls_back_each_metadata_write(self):
        for write in (lambda: self.update_activity(title="Changed"), lambda: self.update_session(title="Changed"),
                      lambda: FacultyGradingService.archive_activity(user=self.faculty, activity=self.activity)):
            with self.subTest(write=write), patch("apps.grading.services.record_gradebook_modification",
                                                side_effect=OperationalError("marker unavailable")):
                with self.assertRaises(OperationalError):
                    write()
            self.activity.refresh_from_db()
            self.session.refresh_from_db()
            self.assertEqual(self.activity.title, "Exam 1")
            self.assertTrue(self.activity.is_active)
            self.assertIsNone(self.session.title)
            self.assertEqual(self.marker(), self.old_time)

    def test_attribution_requires_accepted_faculty_and_preserves_other_scope(self):
        other = self._create_faculty("metadata_other")
        FacultyGradingService.update_activity(**dict(user=other, activity=self.activity,
            template_period=self.template_period, template_component=self.activity.template_component,
            template_subcomponent=None, template_detail=None, title="Other edit", total_score=100,
            activity_date=self.activity.activity_date))
        self.assertFalse(FacultyGradebookModification.objects.filter(faculty_user=other).exists())
        self.assertEqual(self.marker(), self.old_time)
        self.update_activity(title="My edit")
        marker = FacultyGradebookModification.objects.get()
        self.assertEqual((marker.faculty_user_id, marker.offering_id, marker.template_period_id),
                         (self.faculty.pk, self.offering.pk, self.template_period.pk))
        self.assertGreater(marker.modified_at, self.old_time)

    def test_activity_effective_persisted_values_are_compared(self):
        self.update_activity(title=" Exam 1 ", total_score="100.00",
                             activity_date=self.activity.activity_date.isoformat())
        self.assertEqual(self.marker(), self.old_time)

    def test_session_wrong_scope_cannot_overwrite_existing_row(self):
        from copy import copy
        for field in ("tenant_id", "campus_id"):
            wrong = copy(self.offering)
            setattr(wrong, field, 999999999)
            with self.subTest(field=field), self.assertRaises(IntegrityError):
                self.update_session(offering=wrong, title="Wrong scope")
        self.session.refresh_from_db()
        self.assertIsNone(self.session.title)
        self.assertEqual(self.marker(), self.old_time)


class DashboardVisitPersistenceTests(DashboardFixture, TestCase):
    def history(self):
        row = AuditLog.objects.create(actor_user=self.faculty, portal="FACULTY", action=DashboardActivity.VISIT_ACTION,
            entity_type="FacultyDashboard", tenant=self.tenant, campus=self.campus)
        return self._set_timestamp(row, "created_at", timezone.now() - timedelta(days=1))

    def test_audit_failure_keeps_response_previous_value_and_retry_without_session_advance(self):
        previous = self.history()
        key = f"faculty_dashboard_visit:{self.tenant.pk}:{self.campus.pk}"
        with self.assertLogs("apps.faculty_portal.dashboard", level="WARNING") as logs:
            with patch("apps.faculty_portal.dashboard.AuditService.log_event", side_effect=OperationalError("sensitive SQL secret")):
                response = self.dashboard()
        self.assertEqual(response.context["stats"]["previous_visit"], previous.created_at)
        self.assertNotIn(key, self.client.session)
        self.assertIsNone(FacultyDashboardVisitState.objects.get().recorded_at)
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 1)
        self.assertNotIn("sensitive", " ".join(logs.output))
        self.assertNotIn(self.faculty.username, " ".join(logs.output))
        retry = self.dashboard()
        self.assertEqual(retry.context["stats"]["previous_visit"], previous.created_at)
        self.assertIn(key, self.client.session)
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 2)

    def test_final_state_save_failure_rolls_back_audit_and_does_not_advance_session(self):
        original = FacultyDashboardVisitState.save

        def fail(state, *args, **kwargs):
            if kwargs.get("update_fields") == ["recorded_at"]:
                raise IntegrityError("private persistence detail")
            return original(state, *args, **kwargs)

        with self.assertLogs("apps.faculty_portal.dashboard", level="WARNING"):
            with patch.object(FacultyDashboardVisitState, "save", fail):
                self.dashboard()
        self.assertFalse(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).exists())
        self.assertIsNone(FacultyDashboardVisitState.objects.get().recorded_at)
        self.assertNotIn(f"faculty_dashboard_visit:{self.tenant.pk}:{self.campus.pk}", self.client.session)
        self.dashboard()
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 1)

    def test_claim_failure_is_informational_but_unrelated_errors_propagate(self):
        with self.assertLogs("apps.faculty_portal.dashboard", level="WARNING"):
            with patch("apps.faculty_portal.dashboard.DashboardActivity._claim_visit", side_effect=OperationalError("private")):
                self.assertIsNone(self.dashboard().context["stats"]["previous_visit"])
        with patch("apps.faculty_portal.dashboard.DashboardActivity._claim_visit", side_effect=RuntimeError("bug")):
            with self.assertRaisesMessage(RuntimeError, "bug"):
                self.client.get(reverse("faculty_portal:dashboard"))

    def test_non_duplicate_claim_integrity_error_does_not_become_a_dashboard_500(self):
        with self.assertLogs("apps.faculty_portal.dashboard", level="WARNING"):
            with patch("apps.faculty_portal.dashboard.FacultyDashboardVisitState.objects.create",
                       side_effect=IntegrityError("private foreign key detail")):
                self.dashboard()
        self.assertFalse(FacultyDashboardVisitState.objects.exists())
        self.assertNotIn(f"faculty_dashboard_visit:{self.tenant.pk}:{self.campus.pk}", self.client.session)

    def test_identical_initial_session_requests_share_unique_claim_previous_and_audit(self):
        previous = self.history()
        other = Client()
        other.cookies = self.client.cookies.copy()
        original = QuerySet.get
        competitor = []

        def interleave(queryset, *args, **kwargs):
            if queryset.model is FacultyDashboardVisitState and not queryset.query.select_for_update and not competitor:
                try:
                    return original(queryset, *args, **kwargs)
                except FacultyDashboardVisitState.DoesNotExist:
                    competitor.append(None)
                    # Request B completes after A's initial miss, before A's
                    # unique INSERT. A must recover the actual winning row.
                    competitor[0] = other.get(reverse("faculty_portal:dashboard"))
                    raise
            return original(queryset, *args, **kwargs)

        with patch.object(QuerySet, "get", interleave):
            first = self.dashboard()
        self.assertEqual(competitor[0].status_code, 200)
        self.assertEqual(first.context["stats"]["previous_visit"], previous.created_at)
        self.assertEqual(competitor[0].context["stats"]["previous_visit"], previous.created_at)
        self.assertEqual(FacultyDashboardVisitState.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 2)
        key = f"faculty_dashboard_visit:{self.tenant.pk}:{self.campus.pk}"
        self.assertEqual(self.client.session[key], other.session[key])
        self.dashboard()
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 2)

    def test_stale_session_without_cache_reuses_recorded_window_without_writes(self):
        self.dashboard()
        key = f"faculty_dashboard_visit:{self.tenant.pk}:{self.campus.pk}"
        session = self.client.session
        session.pop(key)
        session.save()
        self.dashboard()
        self.assertEqual(FacultyDashboardVisitState.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 1)

    def test_normal_refresh_does_not_query_visit_state_or_write_audit(self):
        self.dashboard()
        with CaptureQueriesContext(connection) as queries:
            self.dashboard()
        self.assertFalse(any("faculty_dashboard_visit_states" in row["sql"] for row in queries))
        self.assertFalse(any("INSERT" in row["sql"] and "audit_logs" in row["sql"] for row in queries))

    def test_two_requests_at_window_rollover_keep_the_same_previous_visit(self):
        self.dashboard()
        previous = AuditLog.objects.get(action=DashboardActivity.VISIT_ACTION)
        expired = timezone.now() - timedelta(hours=1)
        FacultyDashboardVisitState.objects.update(window_started_at=expired)
        key = f"faculty_dashboard_visit:{self.tenant.pk}:{self.campus.pk}"
        session = self.client.session
        session[key]["started"] = expired.isoformat()
        session.save()
        other = Client()
        other.cookies = self.client.cookies.copy()
        original, competitor = QuerySet.get, []

        def interleave(queryset, *args, **kwargs):
            state = original(queryset, *args, **kwargs)
            if queryset.model is FacultyDashboardVisitState and not queryset.query.select_for_update and not competitor:
                competitor.append(None)
                competitor[0] = other.get(reverse("faculty_portal:dashboard"))
            return state  # Deliberately return A's stale pre-lock state.

        with patch.object(QuerySet, "get", interleave):
            response = self.dashboard()
        self.assertEqual(response.context["stats"]["previous_visit"], previous.created_at)
        self.assertEqual(competitor[0].context["stats"]["previous_visit"], previous.created_at)
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 2)
        self.assertEqual(FacultyDashboardVisitState.objects.count(), 1)

    def test_other_faculty_has_separate_state_and_no_access_to_previous_visit(self):
        self.dashboard()
        other = Client()
        other.force_login(self.other_faculty)
        response = other.get(reverse("faculty_portal:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["stats"]["previous_visit"])
        self.assertEqual(set(FacultyDashboardVisitState.objects.values_list("faculty_user_id", flat=True)),
                         {self.faculty.pk, self.other_faculty.pk})

    def test_failed_render_leaves_only_unrecorded_claim_and_retry_records_once(self):
        with patch("apps.faculty_portal.views.render", side_effect=RuntimeError("render failed")):
            with self.assertRaises(RuntimeError):
                self.client.get(reverse("faculty_portal:dashboard"))
        self.assertIsNone(FacultyDashboardVisitState.objects.get().recorded_at)
        self.assertFalse(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).exists())
        self.dashboard()
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 1)


@skipUnless(connection.vendor == "mysql" and getattr(connection, "mysql_is_mariadb", False),
            "Requires a disposable MariaDB/InnoDB test database; SQLite cannot prove row-lock concurrency.")
class MariaDBTrackingConcurrencyTests(TrackingFixture, TransactionTestCase):
    serialized_rollback = True

    def metadata_overlap(self, model, instance, *, update, write, changed):
        held, reader_ready = Event(), Event()
        errors = []
        original = QuerySet.get

        def get(queryset, *args, **kwargs):
            if (current_thread().name == "metadata-reader" and queryset.model is model
                    and queryset.query.select_for_update):
                reader_ready.set()
            return original(queryset, *args, **kwargs)

        def competitor():
            close_old_connections()
            try:
                with transaction.atomic():
                    model.objects.select_for_update().get(pk=instance.pk)
                    held.set()
                    if not reader_ready.wait(10):
                        raise TimeoutError("Metadata reader did not reach its row lock")
                    model.objects.filter(pk=instance.pk).update(**update)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def reader():
            close_old_connections()
            try:
                if not held.wait(10):
                    raise TimeoutError("Metadata writer did not acquire its row lock")
                write()
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        FacultyGradebookModification.objects.update(modified_at=self.old_time)
        with patch.object(QuerySet, "get", get):
            threads = [Thread(target=competitor), Thread(target=reader, name="metadata-reader")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        if changed:
            self.assertGreater(self.marker(), self.old_time)
        else:
            self.assertEqual(self.marker(), self.old_time)

    def test_activity_metadata_waits_and_compares_current_state(self):
        for changed in (True, False):
            with self.subTest(changed=changed):
                self.metadata_overlap(GradeActivity, self.activity, update={"title": "Competing edit"},
                    write=lambda: GradebookMetadataInterleavingTests.update_activity(
                        self, title="Exam 1" if changed else "Competing edit"), changed=changed)

    def test_session_metadata_waits_and_compares_current_state(self):
        for changed in (True, False):
            with self.subTest(changed=changed):
                self.metadata_overlap(AttendanceSession, self.session, update={"title": "Competing edit"},
                    write=lambda: GradebookMetadataInterleavingTests.update_session(
                        self, title=None if changed else "Competing edit"), changed=changed)

    def test_archive_waits_and_compares_current_active_state(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                self.metadata_overlap(GradeActivity, self.activity, update={"is_active": changed},
                    write=lambda: FacultyGradingService.archive_activity(user=self.faculty, activity=self.activity),
                    changed=changed)

    def overlapping_write(self, *, attendance):
        if attendance:
            model, parent, instance = AttendanceRecord, AttendanceSession, self.session
            row = model.objects.create(session=instance, student=self.student_1, status_code="P")
            update, write = {"status_code": "A"}, lambda: self.attendance("P")
        else:
            model, parent, instance = StudentActivityScore, GradeActivity, self.activity
            row = model.objects.create(activity=instance, student=self.student_1, raw_score=80)
            update, write = {"raw_score": 65}, lambda: self.score("80")
        held, reader_ready, committed = Event(), Event(), Event()
        errors = []
        original = QuerySet.get

        def get(queryset, *args, **kwargs):
            if current_thread().name == "tracking-reader" and queryset.query.select_for_update:
                if queryset.model is parent:
                    reader_ready.set()  # New implementation waits here.
                elif queryset.model is model:
                    reader_ready.set()  # Old implementation already snapshotted.
                    if not committed.wait(10):
                        raise TimeoutError("Competing writer did not commit")
            return original(queryset, *args, **kwargs)

        def competitor():
            close_old_connections()
            try:
                with transaction.atomic():
                    parent.objects.select_for_update().get(pk=instance.pk)
                    held.set()
                    if not reader_ready.wait(10):
                        raise TimeoutError("Reader did not reach its lock")
                    model.objects.filter(pk=row.pk).update(**update)
                committed.set()
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def reader():
            close_old_connections()
            try:
                if not held.wait(10):
                    raise TimeoutError("Writer did not acquire parent lock")
                write()
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with patch.object(QuerySet, "get", get):
            threads = [Thread(target=competitor), Thread(target=reader, name="tracking-reader")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(20)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertGreater(self.marker(), self.old_time)

    def test_score_waits_for_competing_commit(self):
        self.overlapping_write(attendance=False)

    def test_attendance_waits_for_competing_commit(self):
        self.overlapping_write(attendance=True)

    def test_two_simultaneous_requests_record_one_visit(self):
        other = Client()
        other.cookies = self.client.cookies.copy()
        barrier, results = Barrier(2), []

        def request(client):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = client.get(reverse("faculty_portal:dashboard"))
                results.append((response.status_code, response.context["stats"]["previous_visit"]))
            finally:
                close_old_connections()

        threads = [Thread(target=request, args=(client,)) for client in (self.client, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(results, [(200, None), (200, None)])
        self.assertEqual(AuditLog.objects.filter(action=DashboardActivity.VISIT_ACTION).count(), 1)
