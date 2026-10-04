"""Disposable cutoff UX, attribution and production-sized query regressions."""
import json
import time
import os
from datetime import date
from django.test import TestCase
from django.db import connection
from django.urls import reverse
from .tests_faculty_cutoffs import FacultyCutoffTests
from . import tests as fixtures
from .faculty_cutoffs import publish_faculty_cutoffs
from .daily_encoding import prepare_daily_encoding, expected_daily_occurrences, _source_for_date
from .observations import CheckingRoundService, AttendanceResultService
from .models import AttendanceCutoffPublication, FacultyDTR, CoverageReconciliation, FacultyCoverage, OfferingAttendanceSourceChange
from .faculty_cutoffs import review_faculty_cutoff
from .forms import FacultyCutoffActionForm
from .faculty_cutoff_views import processing_rows
from .permissions import attendance_read_permissions, require_attendance_permission, PUBLISH_FACULTY_PERMISSION
from django.core.exceptions import PermissionDenied
from unittest.mock import patch


class CutoffProcessingTests(TestCase):
    permission_codes = FacultyCutoffTests.permission_codes
    setUpTestData = classmethod(FacultyCutoffTests.setUpTestData.__func__)
    setUp = FacultyCutoffTests.setUp
    aware = FacultyCutoffTests.aware
    coverage = FacultyCutoffTests.coverage
    scope = FacultyCutoffTests.scope
    query = FacultyCutoffTests.query
    review = FacultyCutoffTests.review
    slice = FacultyCutoffTests.slice
    pair = FacultyCutoffTests.pair
    confirm = FacultyCutoffTests.confirm
    publish = FacultyCutoffTests.publish

    def new_offering(self, section_code, schedule="M 18:00-21:00", course=None):
        section = fixtures.Section.objects.create(tenant=self.tenant, campus=self.campus,
            department=self.department, program=self.program, code=section_code, name=section_code)
        return fixtures.CourseOffering.objects.create(tenant=self.tenant, campus=self.campus,
            department=self.department, program=self.program, academic_year=self.academic_year,
            term=self.term, course=course or self.course, section=section, room="SYNTHETIC", schedule_text=schedule)

    def action(self, form, action, **extra):
        return {**self.query(), "action": action, "submission_key": form.initial["submission_key"],
            "expected_fingerprints": json.dumps(form.initial["expected_fingerprints"]),
            "ready_faculty_ids": json.dumps(form.initial["ready_faculty_ids"]), **extra}

    def test_unrelated_unassigned_offering_does_not_hold_ready_faculty(self):
        first, _ = self.pair()
        unknown = self.new_offering("UNASSIGNED")
        row = self.slice()
        self.assertTrue(row.ready)
        self.assertTrue(any(unknown.pk in b.offering_ids or any(s["section_code"] == "UNASSIGNED" for s in b.details["sections"])
            for b in self.review().unattributed))
        self.assertEqual(self.publish().entries.get().meeting_id, first.pk)

    def test_ba221_queue_excludes_ariel_but_own_is322_is323_remain_pending(self):
        self.term.end_date = date(2026, 12, 31)
        self.term.save(update_fields=["end_date"])
        self.academic_year.end_date = date(2027, 5, 31)
        self.academic_year.save(update_fields=["end_date"])
        self.faculty.first_name, self.faculty.last_name = "Ariel", "Ual"
        self.faculty.save(update_fields=["first_name", "last_name"])
        self.offering.schedule_text = "S 15:00-18:00"
        self.offering.save(update_fields=["schedule_text"])
        self.course.code = "IS322"
        self.course.save(update_fields=["code"])
        ba = fixtures.Course.objects.create(tenant=self.tenant, campus=self.campus, department=self.department,
            code="BA221-RSRCH", title="Synthetic BA221")
        self.combined_offering.course = ba
        self.combined_offering.schedule_text = "S 18:00-21:00"
        self.combined_offering.save(update_fields=["course", "schedule_text"])
        self.section2.code = "BSBA HRM 3-BA3_HRM"
        self.section2.save(update_fields=["code"])
        unknown = self.new_offering("BSBA HRM 3-BA3_HRM_TRANSFEREES", "S 18:00-21:00", ba)
        is323 = fixtures.Course.objects.create(tenant=self.tenant, campus=self.campus, department=self.department,
            code="IS323", title="Synthetic IS323")
        own = self.new_offering("ARIEL-IS323", "S 18:00-21:00", is323)
        self.coverage()
        self.coverage(offering=own)
        scope = {**self.scope(), "start_date": date(2026, 10, 3), "end_date": date(2026, 10, 3)}
        prepare_daily_encoding(actor=self.actor, offerings=[self.offering, own], academic_year=self.academic_year,
            term=self.term, meeting_date=scope["start_date"])
        review = review_faculty_cutoff(actor=self.actor, **scope)
        ariel = next(r for r in review.slices if r.faculty.pk == self.faculty.pk)
        self.assertFalse(ariel.ready)
        self.assertEqual({s["course_code"] for b in ariel.blockers for s in b.details["sections"]}, {"IS322", "IS323"})
        self.assertEqual(len(review.unattributed), 2)
        self.assertTrue(all(not b.details["candidate_ids"] for b in review.unattributed))
        for meeting in fixtures.TeachingMeeting.objects.all():
            self.confirm(meeting)
        review = review_faculty_cutoff(actor=self.actor, **scope)
        ariel = next(r for r in review.slices if r.faculty.pk == self.faculty.pk)
        self.assertTrue(ariel.ready)
        self.assertEqual(len(ariel.records), 2)
        publications = publish_faculty_cutoffs(actor=self.actor, **scope, faculty_ids=[self.faculty.pk],
            expected_fingerprints={str(self.faculty.pk): ariel.fingerprint}, submission_key="BA221-safe")
        self.assertEqual(publications[0].entries.count(), 2)

    def test_future_pending_coverage_is_excluded_relevant_change_is_not(self):
        self.pair(confirm_second=True)
        item = CoverageReconciliation.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
            department=self.department, event_type="DIRECT_UPDATE", source_reference="synthetic-future",
            effective_at=self.aware(2026, 1, 10), proposed_faculty=self.faculty, created_by=self.actor)
        self.assertTrue(self.slice().ready)
        item.effective_at = self.aware(2026, 1, 5)
        item.save(update_fields=["effective_at"])
        self.assertFalse(self.slice().ready)
        self.assertTrue(self.slice(self.replacement).ready)

    def test_future_pending_schedule_change_does_not_hide_earlier_expected_classes(self):
        first, _ = self.pair(confirm_second=True)
        old_schedule, old_room = self.offering.schedule_text, self.offering.room
        change = OfferingAttendanceSourceChange.objects.create(tenant=self.tenant, campus=self.campus,
            department=self.department, offering=self.offering, effective_from=date(2026, 1, 10),
            old_schedule_text=old_schedule, old_room=old_room,
            new_schedule_text="M 10:00-11:00", new_room="SYNTHETIC-NEW",
            source_reference="synthetic-future-schedule", created_by=self.actor)
        self.offering.schedule_text, self.offering.room = change.new_schedule_text, change.new_room
        self.offering.save(update_fields=["schedule_text", "room"])
        original_snapshot = first.schedule_snapshot.copy()
        original_location = first.location_snapshot.copy()
        for lock in (False, True):
            review = review_faculty_cutoff(actor=self.actor, **self.scope(), lock=lock)
            row = next(r for r in review.slices if r.faculty.pk == self.faculty.pk)
            self.assertTrue(row.ready, row.blockers)
            self.assertFalse(row.blockers)
            self.assertEqual([r.meeting.pk for r in row.records], [first.pk])
            occurrences, issues = expected_daily_occurrences(offerings=[self.offering], term=self.term,
                start_date=date(2026, 1, 5), end_date=date(2026, 1, 5), lock=lock)
            self.assertFalse(issues)
            self.assertEqual(len(occurrences), 1)
            self.assertEqual((occurrences[0].source_schedule_text, occurrences[0].source_room), (old_schedule, old_room))
            self.assertEqual(occurrences[0].source_effective_until, date(2026, 1, 9))
        # A pending change remains blocked on its boundary and after it, never
        # silently becoming a new authoritative recurring schedule.
        for day in (10, 12):
            occurrences, issues = expected_daily_occurrences(offerings=[self.offering], term=self.term,
                start_date=date(2026, 1, day), end_date=date(2026, 1, day))
            self.assertFalse(occurrences)
            self.assertEqual([issue.code for issue in issues], ["SOURCE_CHANGE_PENDING"])
        first.refresh_from_db()
        self.assertEqual(first.schedule_snapshot, original_snapshot)
        self.assertEqual(first.location_snapshot, original_location)
        change.effective_from = date(2026, 1, 5)
        change.save(update_fields=["effective_from"])
        self.assertFalse(self.slice().ready)
        self.assertTrue(self.slice(self.replacement).ready)

    def source_change(self, *, day, old, new, old_room, new_room, resolved=False):
        return OfferingAttendanceSourceChange.objects.create(tenant=self.tenant, campus=self.campus,
            department=self.department, offering=self.offering, effective_from=date(2026, 1, day),
            old_schedule_text=old, new_schedule_text=new, old_room=old_room, new_room=new_room,
            source_reference=f"synthetic-source-{OfferingAttendanceSourceChange.objects.count()}",
            created_by=self.actor, status="RESOLVED" if resolved else "PENDING",
            resolved_by=self.actor if resolved else None, resolved_at=self.aware(2026, 1, 1) if resolved else None)

    def test_multiple_future_pending_changes_use_first_recorded_pre_change_source(self):
        old, middle, latest = "M 08:00-09:00", "M 10:00-11:00", "M 13:00-14:00"
        for second_day in (20, 10, 15):
            with self.subTest(second_day=second_day):
                OfferingAttendanceSourceChange.objects.all().delete()
                self.source_change(day=15, old=old, new=middle, old_room="OLD", new_room="MIDDLE")
                self.source_change(day=second_day, old=middle, new=latest, old_room="MIDDLE", new_room="LIVE")
                self.offering.schedule_text, self.offering.room = latest, "LIVE"
                self.offering.save(update_fields=["schedule_text", "room"])
                offering = fixtures.CourseOffering.objects.prefetch_related("attendance_source_changes").get(pk=self.offering.pk)
                # Input is boundary-sorted by the existing batched reader;
                # boundaries are not a substitute for saved edit chronology.
                changes = sorted(offering.attendance_source_changes.all(), key=lambda item: (item.effective_from, item.pk))
                with self.assertNumQueries(0):
                    source = _source_for_date(offering=offering, term=self.term,
                        meeting_date=date(2026, 1, 5), changes=changes)
                self.assertEqual(source, (old, "OLD", self.term.start_date, date(2026, 1, min(15, second_day) - 1)))

    def test_future_pending_changes_preserve_resolved_history_precedence(self):
        first = self.source_change(day=5, old="M 08:00-09:00", new="M 09:00-10:00",
            old_room="ORIGINAL", new_room="RESOLVED-1", resolved=True)
        second = self.source_change(day=12, old=first.new_schedule_text, new="M 10:00-11:00",
            old_room=first.new_room, new_room="RESOLVED-2", resolved=True)
        pending = self.source_change(day=20, old="M 11:00-12:00", new="M 13:00-14:00",
            old_room="UNVERIFIED-OLD", new_room="LIVE")
        self.offering.schedule_text, self.offering.room = pending.new_schedule_text, pending.new_room
        self.offering.save(update_fields=["schedule_text", "room"])
        for day, expected in ((4, (first.old_schedule_text, first.old_room, self.term.start_date, date(2026, 1, 4))),
                (5, (first.new_schedule_text, first.new_room, date(2026, 1, 5), date(2026, 1, 11))),
                (12, (second.new_schedule_text, second.new_room, date(2026, 1, 12), self.term.end_date))):
            with self.subTest(day=day):
                self.assertEqual(_source_for_date(offering=self.offering, term=self.term,
                    meeting_date=date(2026, 1, day), changes=[pending, second, first]), expected)
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering], term=self.term,
            start_date=date(2026, 1, 12), end_date=date(2026, 1, 12))
        self.assertFalse(issues)
        self.assertEqual([(o.source_schedule_text, o.source_room) for o in occurrences], [(second.new_schedule_text, second.new_room)])

    def test_future_pending_change_with_missing_old_source_never_guesses_live_schedule(self):
        change = self.source_change(day=10, old="", new="M 10:00-11:00", old_room="", new_room="LIVE")
        self.offering.schedule_text, self.offering.room = change.new_schedule_text, change.new_room
        self.offering.save(update_fields=["schedule_text", "room"])
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering], term=self.term,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        self.assertFalse(occurrences)
        self.assertEqual([issue.code for issue in issues], ["SCHEDULE_AMBIGUOUS"])

    def test_current_unassignment_retains_saved_teaching_and_immutable_final(self):
        from .dtr import finalize_dtr, preview_dtr
        from copy import deepcopy
        first, _ = self.pair()
        publication = self.publish()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        final = finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=preview.fingerprint, faculty_review_complete=True, reason="")
        saved = deepcopy(final.snapshot)
        # The current academic assignment is inactive; its ended dated coverage
        # and saved attendance still prove the historical teaching occurrence.
        from apps.academics.models import FacultyAssignment
        FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
            faculty_user=self.faculty, is_active=False)
        FacultyCoverage.objects.filter(offering=self.offering).update(effective_until=self.aware(2026, 1, 6))
        row = self.slice()
        self.assertEqual(row.records[0].meeting.pk, first.pk)
        self.assertEqual(row.records[0].result.faculty_user_id, self.faculty.pk)
        final.refresh_from_db()
        self.assertEqual(final.snapshot, saved)
        self.assertEqual(publication.entries.count(), 1)

    def test_batch_preview_matches_individual_calculation_without_per_class_loaders(self):
        from .dtr import preview_dtr
        self.pair(confirm_second=True)
        publication = self.publish()
        expected = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        review = self.review()
        with attendance_read_permissions(), patch("apps.faculty_attendance.dtr.latest_closure", side_effect=AssertionError("per-class query")), \
             patch("apps.faculty_attendance.dtr.current_mixed_decisions", side_effect=AssertionError("per-faculty query")):
            rows = processing_rows(actor=self.actor, review=review)
        actual = next(r["preview"] for r in rows if r["slice"].faculty.pk == self.faculty.pk)
        self.assertEqual(actual.snapshot, expected.snapshot)
        self.assertEqual(actual.fingerprint, expected.fingerprint)

    def test_empty_maps_show_actionable_error_and_disabled_actions(self):
        self.pair()
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:faculty_cutoff_review")
        values = {**self.query(), "action": "publish_ready", "submission_key": "empty", "expected_fingerprints": "{}", "ready_faculty_ids": "[]"}
        response = self.client.post(url, values)
        self.assertContains(response, "No eligible faculty selected")
        self.assertNotContains(response, "This field is required.")
        self.assertFalse(AttendanceCutoffPublication.objects.exists())
        response = self.client.get(url, self.query())
        self.assertEqual(response.context["finalize_count"], 0)
        self.assertContains(response, 'value="finalize_selected" disabled')
        self.assertContains(response, 'value="finalize_ready" disabled')
        self.assertContains(response, "No DTR currently needs finalization")

    def test_all_ready_retry_freezes_batch_and_does_not_add_new_ready_faculty(self):
        _, second = self.pair()
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:faculty_cutoff_review")
        response = self.client.get(url, self.query())
        values = self.action(response.context["publication_form"], "publish_ready")
        self.assertEqual(self.client.post(url, values).status_code, 302)
        self.confirm(second)
        self.assertEqual(self.client.post(url, values).status_code, 302)
        self.assertEqual(AttendanceCutoffPublication.objects.count(), 1)
        self.assertFalse(FacultyDTR.objects.exists())
        response = self.client.get(url, self.query())
        self.assertEqual(response.context["publication_form"].initial["ready_faculty_ids"], [self.replacement.pk])

    def test_post_success_does_not_build_dashboard_before_dispatch(self):
        self.pair()
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:faculty_cutoff_review")
        response = self.client.get(url, self.query())
        values = self.action(response.context["publication_form"], "publish_selected", faculty_ids=[self.faculty.pk])
        with patch("apps.faculty_attendance.faculty_cutoff_views.processing_rows", side_effect=AssertionError("pre-POST dashboard")):
            self.assertEqual(self.client.post(url, values).status_code, 302)

    def test_eligible_choices_exclude_published_current_and_finalized_current(self):
        self.pair()
        self.publish()
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:faculty_cutoff_review")
        response = self.client.get(url, self.query())
        self.assertEqual(response.context["publish_count"], 0)
        self.assertEqual(response.context["finalize_count"], 1)
        values = self.action(response.context["final_form"], "finalize_selected", faculty_ids=[self.faculty.pk])
        self.assertEqual(self.client.post(url, values).status_code, 302)
        response = self.client.get(url, self.query())
        self.assertEqual(response.context["finalize_count"], 0)
        self.assertEqual(response.context["counts"]["finalized"], 1)

    def test_five_actions_have_processing_and_no_extra_attestation(self):
        self.pair()
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:faculty_cutoff_review"), self.query())
        self.assertContains(response, "data-attendance-processing=", count=3)
        for action in ("publish_selected", "publish_ready", "finalize_selected", "finalize_ready"):
            self.assertContains(response, f'name="action" value="{action}"')
        self.assertContains(response, "faculty_attendance/processing.js")
        self.assertNotContains(response, 'name="faculty_review_complete"')

    def test_missing_hidden_evidence_retains_inputs_without_save(self):
        self.pair()
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:faculty_cutoff_review")
        response = self.client.get(url, self.query())
        values = self.action(response.context["publication_form"], "publish_selected", faculty_ids=[self.faculty.pk], reason="Retained synthetic note")
        values.pop("submission_key")
        response = self.client.post(url, values)
        self.assertContains(response, "Request identity is missing.")
        self.assertContains(response, "Retained synthetic note")
        self.assertFalse(AttendanceCutoffPublication.objects.exists())

    def test_malformed_hidden_evidence_is_rejected_without_revision_bypass(self):
        for value in ("[]", "false", '""', '{"7":"short"}', "not-json"):
            form = FacultyCutoffActionForm({"submission_key": "retained", "expected_fingerprints": value})
            self.assertFalse(form.is_valid(), value)
            self.assertIn("expected_fingerprints", form.errors)
        form = FacultyCutoffActionForm({"submission_key": "retained", "expected_fingerprints": "{}", "ready_faculty_ids": "[]"})
        self.assertTrue(form.is_valid())

    def test_permission_cache_is_scope_specific_and_ends_before_new_request(self):
        from apps.rbac.models import UserPermission, Permission
        args = dict(user=self.actor, permission_code=PUBLISH_FACULTY_PERMISSION,
            tenant_id=self.tenant.pk, campus_id=self.campus.pk, department_id=self.department.pk)
        with attendance_read_permissions():
            require_attendance_permission(**args)
            with self.assertNumQueries(0):
                require_attendance_permission(**args)
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=PUBLISH_FACULTY_PERMISSION),
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        with attendance_read_permissions(), self.assertRaises(PermissionDenied):
            require_attendance_permission(**args)

    def test_production_size_profile(self):
        """Same 76 faculty / three dates / 40 unknown offerings before and after."""
        offerings = [self.offering, self.combined_offering]
        faculty = [self.faculty, self.replacement]
        for i in range(74):
            faculty.append(fixtures.User.objects.create_user(f"profile-{i}", f"profile-{i}@example.test", password="x",
                first_name="Synthetic", last_name=str(i), default_tenant=self.tenant, default_campus=self.campus))
            section = fixtures.Section.objects.create(tenant=self.tenant, campus=self.campus,
                department=self.department, program=self.program, code=f"PROFILE-{i}", name=f"Synthetic {i}")
            offerings.append(fixtures.CourseOffering.objects.create(tenant=self.tenant, campus=self.campus,
                department=self.department, program=self.program, academic_year=self.academic_year,
                term=self.term, course=self.course, section=section, room="SYNTHETIC"))
        for offering, teacher in zip(offerings, faculty):
            offering.schedule_text = "M 08:00-09:00; T 08:00-09:00; W 08:00-09:00"
            offering.save(update_fields=["schedule_text"])
            self.coverage(offering=offering, faculty=teacher)
        for day in (5, 6, 7):
            prepare_daily_encoding(actor=self.actor, offerings=offerings,
                academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, day))
        meetings = list(fixtures.TeachingMeeting.objects.order_by("pk"))
        round_ = CheckingRoundService.create(actor=self.actor, meetings=meetings, checking_date=date(2026, 1, 7))
        AttendanceResultService.confirm_present(actor=self.actor, checking_round=round_,
            manifest_revision=round_.manifest_revision,
            reviewed_rows=[{"meeting_id": m.pk, "result_revision": 0} for m in meetings])
        scope = {**self.scope(), "end_date": date(2026, 1, 7)}
        from .faculty_cutoffs import review_faculty_cutoff
        review = review_faculty_cutoff(actor=self.actor, **scope)
        publish_faculty_cutoffs(actor=self.actor, **scope, faculty_ids=[f.pk for f in faculty],
            expected_fingerprints={str(r.faculty.pk): r.fingerprint for r in review.slices}, submission_key="profile")
        for i in range(40):
            section = fixtures.Section.objects.create(tenant=self.tenant, campus=self.campus,
                department=self.department, program=self.program, code=f"UNKNOWN-{i}", name="Synthetic unknown")
            fixtures.CourseOffering.objects.create(tenant=self.tenant, campus=self.campus,
                department=self.department, program=self.program, academic_year=self.academic_year,
                term=self.term, course=self.course, section=section, room="SYNTHETIC", schedule_text="M 18:00-21:00")
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:faculty_cutoff_review")
        for days in (1, 3):
            query = {**self.query(), "end_date": f"2026-01-{4 + days:02d}"}
            started = time.perf_counter()
            measured = [0]
            def count_query(execute, sql, params, many, context):
                measured[0] += 1
                return execute(sql, params, many, context)
            with connection.execute_wrapper(count_query):
                response = self.client.get(url, query)
            elapsed = time.perf_counter() - started
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["rows"]), 76)
            print(f"PROFILE faculty=76 days={days} meetings=228 unknown=40 queries={measured[0]} elapsed={elapsed:.3f}s bytes={len(response.content)}", flush=True)
            if not os.environ.get("TMP_CUTOFF_BASELINE"):
                self.assertLess(measured[0], 400, "Dashboard SQL must stay bounded as faculty/class count grows")
                self.assertLess(len(response.content), 250000, "Unassigned occurrences must not be repeated under every faculty")
