"""Focused save/feedback regressions; all data belongs to Django's disposable DB."""
from datetime import date, time, timedelta
from copy import deepcopy
from time import perf_counter
from unittest.mock import patch
from html.parser import HTMLParser

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from . import tests as existing
from . import tests_ui as existing_ui
from .models import AttendanceResult, AttendanceResultRevision
from .observations import AttendanceResultService, StaleAttendanceReview
from .services import MeetingService, _meeting_snapshot


class PageMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.elements, self.headers, self.summaries = [], [], []
        self.capture = None
        self.feed(content.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "summary" or (tag == "th" and attrs.get("scope") == "col"):
            self.capture = tag
            (self.headers if tag == "th" else self.summaries).append("")

    def handle_data(self, data):
        if self.capture:
            target = self.headers if self.capture == "th" else self.summaries
            target[-1] += data

    def handle_endtag(self, tag):
        if tag == self.capture:
            self.capture = None


class AttendanceSaveFeedbackTests(TestCase):
    permission_codes = existing_ui.AttendanceRoundUITests.permission_codes
    setUpTestData = classmethod(existing_ui.AttendanceRoundUITests.setUpTestData.__func__)
    setUp = existing_ui.AttendanceRoundUITests.setUp
    aware = existing_ui.AttendanceRoundUITests.aware
    schedule = existing_ui.AttendanceRoundUITests.schedule
    coverage = existing_ui.AttendanceRoundUITests.coverage
    meeting = existing_ui.AttendanceRoundUITests.meeting
    round_for = existing_ui.AttendanceRoundUITests.round_for
    finding = existing_ui.AttendanceRoundUITests.finding

    def test_bulk_query_budget_for_forty_reviewed_classes(self):
        first = self.meeting()
        slots = [first.schedule_slot]
        for number in range(1, 8):
            slots.append(existing.ScheduleSlot.objects.create(
                schedule_version=first.schedule_slot.schedule_version, sequence=number + 1,
                weekday=0, start_time=time(8 + number), end_time=time(9 + number), room=f"R{number}"))
        meetings = [first]
        for week in range(5):
            for slot in slots:
                if week == 0 and slot.pk == first.schedule_slot_id:
                    continue
                meetings.append(MeetingService.generate(actor=self.actor, schedule_slot=slot,
                    meeting_date=date(2026, 1, 5) + timedelta(days=7 * week), offerings=[]))
        round_, _url = self.round_for(meetings)
        started = perf_counter()
        with CaptureQueriesContext(connection) as queries:
            outcome = AttendanceResultService.confirm_present(actor=self.actor, checking_round=round_,
                manifest_revision=1, reviewed_rows=[{"meeting_id": m.pk, "result_revision": 0} for m in meetings])
        print(f"BULK40: {len(queries)} SQL queries; {perf_counter() - started:.3f}s; atomic 40-row confirmation")
        self.assertEqual(len(outcome["confirmed_meeting_ids"]), 40)
        self.assertEqual(AttendanceResultRevision.objects.count(), 40)
        self.assertLess(len(queries), 130, "Bulk confirmation must not repeatedly recompute unchanged monthly notices.")

    def test_bulk_same_identity_retries_once_and_rejects_changed_payload_or_later_revision(self):
        meeting = self.meeting()
        round_, url = self.round_for([meeting])
        kwargs = dict(actor=self.actor, checking_round=round_, manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0, "note": "Checked paper"}], submission_key="bulk-retry")
        first = AttendanceResultService.confirm_present(**kwargs)
        retry = AttendanceResultService.confirm_present(**kwargs)
        self.assertEqual(first["confirmed_meeting_ids"], retry["confirmed_meeting_ids"])
        self.assertTrue(retry["replayed"])
        self.assertEqual(AttendanceResultRevision.objects.count(), 1)
        with self.assertRaises(StaleAttendanceReview):
            AttendanceResultService.confirm_present(**{**kwargs, "reviewed_rows": [{"meeting_id": meeting.pk, "result_revision": 0, "note": "Different"}]})
        self.assertEqual(self.finding(url, meeting, revision=1, key="later").status_code, 200)
        with self.assertRaises(StaleAttendanceReview):
            AttendanceResultService.confirm_present(**kwargs)
        self.assertEqual(AttendanceResultRevision.objects.count(), 2)

    def test_bulk_audit_failure_rolls_back_all_results_and_history(self):
        first = self.meeting()
        second = MeetingService.generate(actor=self.actor, schedule_slot=first.schedule_slot,
            meeting_date=date(2026, 1, 12), offerings=[])
        round_, _url = self.round_for([first, second])
        with patch("apps.faculty_attendance.observations.AuditService.log_event", side_effect=RuntimeError("audit failure")):
            with self.assertRaises(RuntimeError):
                AttendanceResultService.confirm_present(actor=self.actor, checking_round=round_, manifest_revision=1,
                    reviewed_rows=[{"meeting_id": m.pk, "result_revision": 0} for m in [first, second]], submission_key="rollback")
        self.assertFalse(AttendanceResult.objects.exists())
        self.assertFalse(AttendanceResultRevision.objects.exists())

    def test_bulk_ajax_success_error_and_normal_post_fallback(self):
        first = self.meeting()
        second = MeetingService.generate(actor=self.actor, schedule_slot=first.schedule_slot,
            meeting_date=date(2026, 1, 12), offerings=[])
        round_, url = self.round_for([first, second])
        data = {"action": "confirm_present", "manifest_revision": 1, "present_rows": [f"{first.pk}:0"], "submission_key": "ajax-bulk"}
        response = self.client.post(url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["counts"], {"unverified": 1, "present": 1, "exception": 0})
        self.assertEqual(response.json()["rows"][0]["meeting_id"], first.pk)
        self.assertTrue(self.client.post(url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()["replayed"])
        failure = self.client.post(url, {**data, "manifest_revision": 99}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(failure.status_code, 409)
        self.assertFalse(failure.json()["ok"])
        fallback = self.client.post(url, {**data, "submission_key": "fallback", "present_rows": [f"{second.pk}:0"]})
        self.assertEqual(fallback.status_code, 302)
        self.assertEqual(AttendanceResultRevision.objects.count(), 2)

    def remove_absence(self, code, *, keep_late=False):
        meeting = self.meeting()
        _round, url = self.round_for([meeting])
        created = self.finding(url, meeting, absence_code=code, missed_hours="1.00", late_flag="", late_minutes="")
        self.assertEqual(created.status_code, 200)
        before = AttendanceResult.objects.get(meeting=meeting).findings_snapshot
        corrected = self.finding(url, meeting, revision=1, key="remove-absence", absence_code="",
            missed_hours="not-a-decimal", late_flag="on" if keep_late else "", late_minutes="12" if keep_late else "",
            early_flag="on" if keep_late else "", early_minutes="5" if keep_late else "", reason="Corrected paper checking")
        self.assertEqual(corrected.status_code, 200, corrected.content)
        result = AttendanceResult.objects.get(meeting=meeting)
        self.assertEqual(result.revision, 2)
        self.assertEqual(result.status, "EXCEPTION" if keep_late else "PRESENT")
        self.assertEqual(result.absent_without_notice_hours + result.absent_with_notice_hours, 0)
        self.assertEqual(result.history.get(revision=1).findings_snapshot, before)
        self.assertFalse(any(f["finding_type"] == "ABSENCE" for f in result.findings_snapshot))
        self.assertEqual(result.correction_reason, "Corrected paper checking")
        from .staff_notices import current_notices
        self.assertFalse(any(n.kind == "ABSENCE" for n in current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)))
        if keep_late:
            self.assertEqual((result.late_minutes, result.early_minutes), (12, 5))
        return url, meeting, result

    def test_saved_a_to_no_absence_becomes_present_and_retains_history(self):
        self.remove_absence("A")

    def test_saved_n_to_no_absence_retains_late_early_and_history(self):
        self.remove_absence("N", keep_late=True)

    def test_saved_n_to_no_absence_normal_post_and_stale_correction(self):
        url, meeting, result = self.remove_absence("N")
        stale = self.finding(url, meeting, revision=1, key="stale-removal", absence_code="", missed_hours="1", late_flag="")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(result.history.count(), 2)
        response = self.client.post(url, {"action": "exception", "meeting_id": meeting.pk, "expected_revision": 2,
            "submission_key": "normal-post", "absence_code": "", "missed_hours": "1.00", "reason": "Rechecked"})
        self.assertEqual(response.status_code, 302)
        result.refresh_from_db()
        self.assertEqual((result.status, result.revision), ("PRESENT", 3))

    def test_new_blank_is_unverified_and_direct_correction_deny_preserves_saved_absence(self):
        meeting = self.meeting()
        _round, url = self.round_for([meeting])
        blank = self.finding(url, meeting, late_flag="", late_minutes="")
        self.assertEqual(blank.status_code, 400)
        self.assertFalse(AttendanceResult.objects.exists())
        self.assertEqual(self.finding(url, meeting, absence_code="A", missed_hours="1", late_flag="").status_code, 200)
        existing.UserPermission.objects.create(user=self.actor,
            permission=existing.Permission.objects.get(code=existing.CORRECT_PERMISSION),
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        denied = self.finding(url, meeting, revision=1, key="denied-remove", absence_code="", missed_hours="1", late_flag="", reason="Rechecked paper")
        self.assertEqual(denied.status_code, 403)
        result = AttendanceResult.objects.get(meeting=meeting)
        self.assertEqual((result.revision, result.status, result.absent_without_notice_hours), (1, "EXCEPTION", 1))

    def test_absence_removal_requires_reason_on_form_and_supported_service(self):
        from .observations import ObservationService
        meeting = self.meeting()
        round_, url = self.round_for([meeting])
        self.assertEqual(self.finding(url, meeting, absence_code="A", missed_hours="1", late_flag="").status_code, 200)
        response = self.finding(url, meeting, revision=1, key="blank-reason", absence_code="", missed_hours="1", late_flag="", reason=" ")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Enter a checker reason", response.json()["row_html"])
        with self.assertRaisesMessage(ValidationError, "Enter a checker reason"):
            ObservationService.record(actor=self.actor, checking_round=round_, meeting_id=meeting.pk,
                manifest_revision=1, submission_key="service-blank-reason", findings=[], correction_revision=1, note="")
        result = AttendanceResult.objects.get(meeting=meeting)
        self.assertEqual((result.revision, result.status, result.history.count()), (1, "EXCEPTION", 1))

    def test_bulk_new_present_does_not_change_existing_monthly_late_notice(self):
        from .staff_notices import current_notices
        first = self.meeting()
        meetings = [first]
        for number in range(1, 4):
            slot = existing.ScheduleSlot.objects.create(schedule_version=first.schedule_slot.schedule_version,
                sequence=number + 1, weekday=0, start_time=time(8 + number), end_time=time(9 + number), room="R101")
            meetings.append(MeetingService.generate(actor=self.actor, schedule_slot=slot,
                meeting_date=first.meeting_date, offerings=[]))
        present = MeetingService.generate(actor=self.actor, schedule_slot=first.schedule_slot,
            meeting_date=date(2026, 1, 12), offerings=[])
        round_, url = self.round_for([*meetings, present])
        for meeting in meetings:
            self.assertEqual(self.finding(url, meeting, key=f"late-{meeting.pk}").status_code, 200)
        before = next(n for n in current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk) if n.kind == "TARDINESS")
        fingerprint = before.source_fingerprint
        self.assertEqual(before.payload["count"], 4)
        AttendanceResultService.confirm_present(actor=self.actor, checking_round=round_, manifest_revision=1,
            reviewed_rows=[{"meeting_id": present.pk, "result_revision": 0}], submission_key="notice-preserved")
        after = next(n for n in current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk) if n.kind == "TARDINESS")
        self.assertEqual((after.pk, after.source_fingerprint, after.payload["count"]), (before.pk, fingerprint, 4))

    def test_daily_accordions_start_collapsed_with_counts_and_processing_forms(self):
        self.meeting()
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:daily_encoding"), {"academic_year": self.academic_year.pk,
            "term": self.term.pk, "meeting_date": "2026-01-05"})
        self.assertEqual(response.status_code, 200)
        markup = PageMarkup(response.content)
        for name in ["daily-attention", "daily-expected"]:
            panels = [attrs for tag, attrs in markup.elements if tag == "details" and attrs.get("id") == name]
            self.assertEqual(len(panels), 1)
            self.assertNotIn("open", panels[0])
        self.assertTrue(any("Expected classes" in summary and "class" in summary for summary in markup.summaries))
        self.assertTrue(any(tag == "form" and attrs.get("method") == "post" and "data-attendance-processing" in attrs
                            for tag, attrs in markup.elements))
        # A form hook alone does nothing unless the full page loads its handler.
        pages = [response, self.client.get(reverse("faculty_attendance:checklist")),
                 self.client.get(reverse("faculty_attendance:cutoff_review"))]
        for page in pages:
            self.assertEqual(page.status_code, 200)
            parsed = PageMarkup(page.content)
            self.assertTrue(any(tag == "form" and "data-attendance-processing" in attrs
                                for tag, attrs in parsed.elements))
            self.assertTrue(any(tag == "script" and attrs.get("src", "").endswith("/faculty_attendance/processing.js")
                                for tag, attrs in parsed.elements))

    def test_cutoff_warning_identity_scope_and_chronological_combined_records(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        later = MeetingService.generate(actor=self.actor, schedule_slot=meeting.schedule_slot,
            meeting_date=date(2026, 1, 12), offerings=[self.combined_offering])
        _round, url = self.round_for([later, meeting])
        scope = {"academic_year": self.academic_year.pk, "term": self.term.pk,
            "start_date": "2026-01-05", "end_date": "2026-01-12"}
        cutoff_url = reverse("faculty_attendance:cutoff_review")
        response = self.client.get(cutoff_url, scope)
        self.assertEqual(response.status_code, 200)
        warning = next(w for w in response.context["cutoff_blockers"] if w["blocker"].meeting_id == meeting.pk)
        self.assertEqual(warning["details"]["faculty"], self.faculty)
        self.assertEqual(len(warning["details"]["sections"]), 2)
        self.assertEqual(warning["details"]["room"], "R101")
        self.assertIn("meeting_date=2026-01-05", warning["url"])
        self.assertEqual(self.finding(url, later, late_minutes="7").status_code, 200)
        self.assertEqual(self.finding(url, meeting, late_minutes="3").status_code, 200)
        response = self.client.get(cutoff_url, scope)
        records = response.context["supporting_records"]
        self.assertEqual([r["record"].meeting.pk for r in records], [meeting.pk, later.pk])
        self.assertEqual(len(records[0]["sections"]), 2)
        self.assertEqual(records[0]["courses"], [(self.course.code, self.course.title)])
        self.assertContains(response, "Saved attendance history (1)")
        markup = PageMarkup(response.content)
        self.assertEqual(markup.headers,
            ["Faculty", "Course name", "Section", "Recorded finding or closure", "Revision history"])
        existing.UserPermission.objects.create(user=self.actor,
            permission=existing.Permission.objects.get(code=existing.PUBLISH_PERMISSION),
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        denied = self.client.get(cutoff_url, scope)
        self.assertEqual(denied.status_code, 403)
        self.assertNotContains(denied, self.course.code, status_code=403)

    def test_cutoff_pending_warning_does_not_guess_faculty_or_leak_another_campus(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        self.round_for([meeting])
        existing.CoverageReconciliation.objects.create(tenant=self.tenant, campus=self.campus,
            department=self.department, offering=self.offering, event_type="DIRECT_UPDATE",
            source_reference="pending-cutoff-feedback", prior_faculty=self.faculty,
            proposed_faculty=self.replacement, reason="Coverage evidence under review", created_by=self.actor)
        other_campus = existing.Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other campus")
        # Deliberately isolated source; never linked to this campus's meeting.
        other_department = existing.Department.objects.create(tenant=self.tenant, campus=other_campus,
            code="OTHER", name="Other department")
        other_program = existing.Program.objects.create(tenant=self.tenant, campus=other_campus,
            department=other_department, code="OTHER", name="Other program")
        other_section = existing.Section.objects.create(tenant=self.tenant, campus=other_campus,
            department=other_department, program=other_program, code="OTHER", name="Other section")
        secret_course = existing.Course.objects.create(tenant=self.tenant, campus=other_campus,
            department=other_department, code="PRIVATE-CAMPUS", title="Not in this cutoff")
        existing.CourseOffering.objects.create(tenant=self.tenant, campus=other_campus,
            department=other_department, program=other_program, academic_year=self.academic_year,
            term=self.term, course=secret_course, section=other_section, schedule_text="TBA")
        response = self.client.get(reverse("faculty_attendance:cutoff_review"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "start_date": "2026-01-05", "end_date": "2026-01-05"})
        self.assertEqual(response.status_code, 200)
        warning = next(w for w in response.context["cutoff_blockers"] if w["blocker"].meeting_id == meeting.pk)
        self.assertIsNone(warning["details"]["faculty"])
        self.assertEqual(len(warning["details"]["sections"]), 2)
        self.assertContains(response, "Faculty unresolved")
        self.assertNotContains(response, "PRIVATE-CAMPUS")
        self.assertFalse(response.context["review"].ready)

    def test_cutoff_supporting_same_date_times_sort_before_manifest_order(self):
        morning = self.meeting()
        existing.CourseOffering.objects.filter(pk=self.offering.pk).update(schedule_text="M 08:00-09:00; M 13:00-14:00")
        slot = existing.ScheduleSlot.objects.create(schedule_version=morning.schedule_slot.schedule_version,
            sequence=2, weekday=0, start_time=time(13), end_time=time(14), room="R101")
        afternoon = MeetingService.generate(actor=self.actor, schedule_slot=slot, meeting_date=morning.meeting_date, offerings=[])
        _round, url = self.round_for([afternoon, morning])
        self.assertEqual(self.finding(url, afternoon).status_code, 200)
        self.assertEqual(self.finding(url, morning).status_code, 200)
        response = self.client.get(reverse("faculty_attendance:cutoff_review"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "start_date": "2026-01-05", "end_date": "2026-01-05"})
        self.assertEqual([r["record"].meeting.pk for r in response.context["supporting_records"]], [morning.pk, afternoon.pk])

    def test_ge109_contiguous_same_faculty_legacy_recovery_uses_start_coverage_without_backdating(self):
        """Synthetic reproduction of supplied GE109 facts, never staging data."""
        from .coverage_initialization import CoverageAdoptionService
        from .observations import require_confirmable_meeting_faculty
        self.academic_year.start_date, self.academic_year.end_date = date(2026, 6, 1), date(2027, 5, 31)
        self.academic_year.save(update_fields=["start_date", "end_date"])
        self.term.start_date, self.term.end_date = date(2026, 9, 1), date(2026, 12, 31)
        self.term.save(update_fields=["start_date", "end_date"])
        self.offering.schedule_text = "M 07:30-09:00"
        self.offering.save(update_fields=["schedule_text"])
        assignment = existing.FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus,
            offering=self.offering, faculty_user=self.faculty, is_primary=True, response_status="ACCEPTED",
            accepted_at=self.aware(2026, 10, 3))
        version = self.schedule(effective_from=date(2026, 10, 2))
        meeting = MeetingService.generate(actor=self.actor, schedule_slot=version.slots.get(),
            meeting_date=date(2026, 10, 5), offerings=[])
        round_, _url = self.round_for([meeting])
        boundary = self.aware(2026, 10, 5, 8)
        first = self.coverage(effective_from=self.aware(2026, 10, 2, 8), effective_until=boundary, source_assignment=assignment)
        second = self.coverage(effective_from=boundary, source_assignment=assignment)
        for reconciliation in meeting.reconciliations.filter(status="PENDING"):
            existing.ReconciliationService.resolve(actor=self.actor, reconciliation=reconciliation,
                decision="KEEP_SNAPSHOT", reason="Synthetic legacy snapshot retained")
        meeting.refresh_from_db()
        self.assertTrue(meeting.unresolved_coverage)
        self.assertFalse(meeting.faculty_snapshot.get("college_academic_sync"))
        before = deepcopy(_meeting_snapshot(meeting))
        frozen = deepcopy(round_.manifest_rows.get().meeting_snapshot)
        original_reconciliation = list(meeting.reconciliations.values("pk", "status", "decision"))
        self.assertLess(first.effective_until, meeting.ends_at)
        self.assertGreater(second.effective_from, meeting.starts_at)
        self.assertEqual(first.effective_until, second.effective_from)
        self.assertEqual(first.faculty_user_id, second.faculty_user_id)
        with self.assertRaises(ValidationError):
            require_confirmable_meeting_faculty(meeting)
        self.assertEqual(existing.CoverageService.effective_for(offering_id=self.offering.pk, at=meeting.starts_at), first)
        adoption = CoverageAdoptionService.adopt(actor=self.actor, meeting=meeting,
            reason="Synthetic contiguous same-faculty legacy recovery")
        meeting.refresh_from_db()
        self.assertEqual(adoption.coverage, first)
        self.assertEqual(require_confirmable_meeting_faculty(meeting), self.faculty)
        self.assertEqual(_meeting_snapshot(meeting), before)
        self.assertEqual(round_.manifest_rows.get().meeting_snapshot, frozen)
        self.assertEqual(list(meeting.reconciliations.values("pk", "status", "decision")), original_reconciliation)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.effective_until, second.effective_from), (boundary, boundary))
        self.assertTrue(existing.AuditLog.objects.filter(action="FACULTY_ATTENDANCE_COVERAGE_ADOPTED").exists())
        AttendanceResultService.confirm_present(actor=self.actor, checking_round=round_, manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}], submission_key="synthetic-ge109")
        result = AttendanceResult.objects.get(meeting=meeting)
        self.assertEqual((result.faculty_user_id, result.revision, result.status), (self.faculty.pk, 1, "PRESENT"))
        self.assertEqual(round_.manifest_rows.get().meeting_snapshot, frozen)
