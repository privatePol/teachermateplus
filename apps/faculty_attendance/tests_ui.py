"""Focused rounds eligibility/navigation and authenticated checklist selections."""
from copy import deepcopy
from datetime import date, time

from django.test import TestCase
from django.urls import reverse

from apps.academics.models import AcademicYear, Term
from apps.rbac.models import UserRole
from apps.tenants.models import Campus, Department, Tenant
from . import tests as existing
from .models import AttendanceCutoffPublication, AttendanceResult, FacultyDTR
from .observations import AttendanceResultService, CheckingRoundService
from .services import MeetingService
from .views import CHECKLIST_SESSION_KEY


class AttendanceRoundUITests(TestCase):
    permission_codes = existing.FacultyAttendanceFoundationTests.permission_codes
    setUpTestData = classmethod(existing.FacultyAttendanceFoundationTests.setUpTestData.__func__)
    setUp = existing.FacultyAttendanceFoundationTests.setUp
    aware = existing.FacultyAttendanceFoundationTests.aware
    schedule = existing.FacultyAttendanceFoundationTests.schedule
    coverage = existing.FacultyAttendanceFoundationTests.coverage
    meeting = existing.FacultyAttendanceFoundationTests.meeting

    def round_for(self, meetings):
        round_ = CheckingRoundService.create(actor=self.actor, meetings=meetings,
            checking_date=min(m.meeting_date for m in meetings),
            checking_end_date=max(m.meeting_date for m in meetings),
            academic_year=self.academic_year, term=self.term)
        self.client.force_login(self.actor)
        return round_, reverse("faculty_attendance:round", args=[round_.public_id])

    def finding(self, url, meeting, revision=0, key="finding", **values):
        return self.client.post(url, {"action": "exception", "meeting_id": meeting.pk,
            "expected_revision": revision, "submission_key": key, "late_flag": "on", "late_minutes": "5",
            **values}, HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json")

    def test_unresolved_cards_counts_and_index_are_filtered_without_changing_manifest(self):
        ready = self.meeting()
        unresolved = MeetingService.generate(actor=self.actor,
            schedule_slot=self.schedule(offering=self.combined_offering).slots.get(),
            meeting_date=date(2026, 1, 5), offerings=[])
        round_, url = self.round_for([ready, unresolved])
        frozen = deepcopy(list(round_.manifest_rows.values("meeting_id", "meeting_snapshot")))
        response = self.client.get(url)
        self.assertEqual([row.meeting_id for row in response.context["rows"]], [ready.pk])
        self.assertEqual([row.meeting_id for row in response.context["index_rows"]], [ready.pk])
        self.assertEqual(response.context["counts"], {"unverified": 1, "present": 0, "exception": 0})
        self.assertEqual([row.meeting_id for row in response.context["unresolved_rows"]], [unresolved.pk])
        self.assertNotContains(response, f'id="meeting-row-{unresolved.pk}"')
        self.assertNotContains(response, f'href="#meeting-row-{unresolved.pk}"')
        self.assertNotContains(response, f'value="{unresolved.pk}:0"')
        self.assertEqual(list(round_.manifest_rows.values("meeting_id", "meeting_snapshot")), frozen)
        confirmed = self.client.post(url, {"action": "confirm_present", "manifest_revision": 1,
            "present_rows": [f"{ready.pk}:0"]})
        self.assertEqual(confirmed.status_code, 302)
        self.assertEqual(AttendanceResult.objects.get(meeting=ready).status, "PRESENT")
        self.assertFalse(AttendanceResult.objects.filter(meeting=unresolved).exists())
        self.assertTrue(existing.unresolved_meetings(tenant_id=self.tenant.pk, campus_id=self.campus.pk).filter(pk=unresolved.pk).exists())
        review = existing.review_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=ready.meeting_date, end_date=ready.meeting_date)
        self.assertFalse(review.ready)
        self.assertTrue(any(b.meeting_id == unresolved.pk for b in review.blockers))

    def test_pending_review_keeps_dated_faculty_visible_but_prevents_present_selection(self):
        meeting = self.meeting()
        existing.CoverageReconciliation.objects.create(tenant=self.tenant, campus=self.campus,
            department=self.department, offering=self.offering, event_type="DIRECT_UPDATE",
            source_reference="pending-ui-review", prior_faculty=self.faculty,
            proposed_faculty=self.replacement, reason="Dated review pending", created_by=self.actor)
        round_, url = self.round_for([meeting])
        response = self.client.get(url)
        self.assertEqual(response.context["rows"][0].attributed_faculty, self.faculty)
        self.assertFalse(response.context["rows"][0].can_confirm_present)
        self.assertNotContains(response, f'id="present-{meeting.pk}"')
        rejected = self.client.post(url, {"action": "confirm_present", "manifest_revision": 1,
            "present_rows": [f"{meeting.pk}:0"]})
        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(AttendanceResult.objects.exists())

    def test_posting_a_hidden_unresolved_row_cannot_confirm_any_selected_class(self):
        ready = self.meeting()
        unresolved = MeetingService.generate(actor=self.actor,
            schedule_slot=self.schedule(offering=self.combined_offering).slots.get(),
            meeting_date=date(2026, 1, 5), offerings=[])
        round_, url = self.round_for([ready, unresolved])
        response = self.client.post(url, {"action": "confirm_present", "manifest_revision": 1,
            "present_rows": [f"{ready.pk}:0", f"{unresolved.pk}:0"]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AttendanceResult.objects.exists())
        self.assertEqual(round_.manifest_rows.count(), 2)

    def test_hidden_unresolved_exception_post_does_not_record_an_observation(self):
        unresolved = MeetingService.generate(actor=self.actor, schedule_slot=self.schedule().slots.get(),
            meeting_date=date(2026, 1, 5), offerings=[])
        round_, url = self.round_for([unresolved])
        response = self.finding(url, unresolved)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertFalse(existing.AttendanceObservation.objects.exists())
        self.assertFalse(AttendanceResult.objects.exists())
        self.assertEqual(round_.manifest_rows.count(), 1)

    def test_bulk_confirmation_preserves_exceptions_and_unselected_rows_then_allows_correction(self):
        first = self.meeting()
        second = MeetingService.generate(actor=self.actor,
            schedule_slot=first.schedule_slot, meeting_date=date(2026, 1, 12), offerings=[])
        third = MeetingService.generate(actor=self.actor,
            schedule_slot=first.schedule_slot, meeting_date=date(2026, 1, 19), offerings=[])
        round_, url = self.round_for([first, second, third])
        self.assertEqual(self.finding(url, second).status_code, 200)
        exception = AttendanceResult.objects.get(meeting=second)
        saved = deepcopy(exception.findings_snapshot)
        response = self.client.post(url, {"action": "confirm_present", "manifest_revision": 1,
            "present_rows": [f"{first.pk}:0", f"{second.pk}:1"]})
        self.assertEqual(response.status_code, 302)
        exception.refresh_from_db()
        self.assertEqual((exception.status, exception.revision, exception.findings_snapshot), ("EXCEPTION", 1, saved))
        self.assertFalse(AttendanceResult.objects.filter(meeting=third).exists())
        self.assertFalse(AttendanceCutoffPublication.objects.exists())
        self.assertFalse(FacultyDTR.objects.exists())
        corrected = self.finding(url, first, revision=1, key="after-present", late_minutes="12")
        self.assertEqual(corrected.status_code, 200)
        result = AttendanceResult.objects.get(meeting=first)
        self.assertEqual((result.status, result.revision, result.late_minutes), ("EXCEPTION", 2, 12))
        self.assertEqual(list(result.history.order_by("revision").values_list("status", flat=True)), ["PRESENT", "EXCEPTION"])

    def test_stale_bulk_selection_is_atomic(self):
        first = self.meeting()
        second = MeetingService.generate(actor=self.actor, schedule_slot=first.schedule_slot,
            meeting_date=date(2026, 1, 12), offerings=[])
        round_, url = self.round_for([first, second])
        self.assertEqual(self.finding(url, second).status_code, 200)
        response = self.client.post(url, {"action": "confirm_present", "manifest_revision": 1,
            "present_rows": [f"{first.pk}:0", f"{second.pk}:0"]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AttendanceResult.objects.filter(meeting=first).exists())
        self.assertEqual(AttendanceResult.objects.get(meeting=second).revision, 1)

    def test_index_uses_actual_times_combined_labels_and_stable_ajax_anchors(self):
        morning = self.meeting(offerings=[self.combined_offering])
        slot = existing.ScheduleSlot.objects.create(schedule_version=morning.schedule_slot.schedule_version,
            sequence=2, weekday=0, start_time=time(13), end_time=time(14), room="R202")
        afternoon = MeetingService.generate(actor=self.actor, schedule_slot=slot,
            meeting_date=morning.meeting_date, offerings=[])
        round_, url = self.round_for([afternoon, morning])
        response = self.client.get(url)
        self.assertEqual([r.meeting_id for r in response.context["index_rows"]], [morning.pk, afternoon.pk])
        self.assertEqual([r.meeting_id for r in response.context["rows"]], [afternoon.pk, morning.pk])
        self.assertContains(response, f'{self.course.code} ({self.section.code})', count=4)
        self.assertContains(response, f'{self.course.code} ({self.section2.code})', count=2)
        self.assertContains(response, f'href="#meeting-row-{morning.pk}"', count=1)
        self.assertContains(response, "Room R202")
        payload = self.finding(url, morning).json()
        self.assertTrue(payload["ok"])
        self.assertIn(f'id="meeting-row-{morning.pk}"', payload["row_html"])
        self.assertIn('tabindex="-1"', payload["row_html"])
        self.assertEqual(payload["counts"], {"unverified": 1, "present": 0, "exception": 1})

    def test_filter_options_use_sorted_dated_names_and_deduplicate_schedule_across_dates(self):
        self.faculty.first_name, self.faculty.last_name = "Zoe", "Zulu"
        self.faculty.save(update_fields=["first_name", "last_name"])
        self.replacement.first_name, self.replacement.last_name = "Amy", "Alpha"
        self.replacement.save(update_fields=["first_name", "last_name"])
        first = self.meeting()
        later = MeetingService.generate(actor=self.actor, schedule_slot=first.schedule_slot,
            meeting_date=date(2026, 1, 12), offerings=[])
        other = MeetingService.generate(actor=self.actor,
            schedule_slot=self.schedule(offering=self.combined_offering).slots.get(),
            meeting_date=first.meeting_date, offerings=[])
        existing.SubstitutionService.assign(actor=self.actor, meeting=other,
            substitute_faculty=self.replacement, reason="")
        round_, url = self.round_for([first, later, other])
        response = self.client.get(url)
        self.assertEqual(response.context["faculty_filter_options"],
            [(str(self.replacement.pk), "Alpha, Amy"), (str(self.faculty.pk), "Zulu, Zoe")])
        self.assertEqual(len(response.context["time_filter_options"]), 1)
        rows = response.context["rows"]
        self.assertEqual(rows[0].filter_time_key, rows[1].filter_time_key)
        self.assertEqual(rows[2].filter_faculty_id, str(self.replacement.pk))
        payload = self.finding(url, first).json()
        self.assertIn('data-filter-faculty="' + str(self.faculty.pk) + '"', payload["row_html"])
        self.assertIn("Zulu, Zoe", payload["row_html"])
        self.assertIn('data-filter-time="' + rows[0].filter_time_key + '"', payload["row_html"])

    def test_present_optional_note_is_audited_only_for_selected_unverified_rows(self):
        first = self.meeting()
        other = MeetingService.generate(actor=self.actor, schedule_slot=first.schedule_slot,
            meeting_date=date(2026, 1, 12), offerings=[])
        round_, url = self.round_for([first, other])
        response = self.client.post(url, {"action": "confirm_present", "manifest_revision": 1,
            "present_rows": [f"{first.pk}:0"], f"present_note_{first.pk}": "Synthetic Regular holiday treatment",
            f"present_note_{other.pk}": "Must not implicitly confirm"})
        self.assertEqual(response.status_code, 302)
        result = AttendanceResult.objects.get(meeting=first)
        self.assertEqual(result.correction_reason, "Synthetic Regular holiday treatment")
        self.assertEqual(result.history.get().change_reason, result.correction_reason)
        self.assertFalse(AttendanceResult.objects.filter(meeting=other).exists())
        self.assertFalse(existing.AttendanceClosureDecision.objects.exists())
        self.assertFalse(AttendanceCutoffPublication.objects.exists())
        self.assertFalse(FacultyDTR.objects.exists())
        self.assertEqual(self.client.post(url, {"action": "confirm_present", "manifest_revision": 1,
            "present_rows": [f"{other.pk}:0"]}).status_code, 302)
        self.assertEqual(AttendanceResult.objects.get(meeting=other).correction_reason, "")

    def test_present_note_redisplays_on_stale_selection_without_creating_results(self):
        first = self.meeting()
        round_, url = self.round_for([first])
        response = self.client.post(url, {"action": "confirm_present", "manifest_revision": 99,
            "present_rows": [f"{first.pk}:0"], f"present_note_{first.pk}": "Retain my checked note"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Retain my checked note"')
        self.assertFalse(AttendanceResult.objects.exists())

    def test_direct_encode_deny_prevents_present_and_note_writes(self):
        first = self.meeting()
        round_, url = self.round_for([first])
        existing.UserPermission.objects.create(user=self.actor,
            permission=existing.Permission.objects.get(code=existing.ENCODE_PERMISSION),
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        response = self.client.post(url, {"action": "confirm_present", "manifest_revision": 1,
            "present_rows": [f"{first.pk}:0"], f"present_note_{first.pk}": "Must not save"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AttendanceResult.objects.exists())


class CollegeDailyUITests(TestCase):
    permission_codes = existing.FacultyAttendanceFoundationTests.permission_codes
    setUpTestData = classmethod(existing.FacultyAttendanceFoundationTests.setUpTestData.__func__)
    setUp = existing.FacultyAttendanceFoundationTests.setUp
    aware = existing.FacultyAttendanceFoundationTests.aware
    schedule = existing.FacultyAttendanceFoundationTests.schedule
    coverage = existing.FacultyAttendanceFoundationTests.coverage
    meeting = existing.FacultyAttendanceFoundationTests.meeting

    def daily(self, **extra):
        self.client.force_login(self.actor)
        return self.client.get(reverse("faculty_attendance:daily_encoding"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "meeting_date": "2026-01-05", **extra})

    def test_preview_groups_start_times_with_dated_names_and_room_order_without_writes(self):
        existing.CourseOffering.objects.filter(pk=self.offering.pk).update(
            schedule_text="M 13:00-14:00; M 08:00-10:00", room="R202")
        self.faculty.first_name, self.faculty.last_name = "Zoe", "Zulu"
        self.faculty.save(update_fields=["first_name", "last_name"])
        self.coverage()
        response = self.daily()
        rows = response.context["occurrence_rows"]
        self.assertEqual([r["start_time"] for r in rows], [time(8), time(8), time(13)])
        self.assertEqual([r["occurrence"].source_room for r in rows[:2]], ["R101", "R202"])
        self.assertEqual(rows[0]["faculty_name"], "Faculty unresolved")
        self.assertEqual(rows[1]["faculty_name"], "Zulu, Zoe")
        self.assertContains(response, "Start time: 8:00 AM", count=1)
        self.assertContains(response, "Start time: 1:00 PM", count=1)
        html = response.content.decode()
        self.assertLess(html.index('scope="col">Room'), html.index('scope="col">Faculty'))
        self.assertLess(html.index('scope="col">Faculty'), html.index('scope="col">Course'))
        self.assertLess(html.index('scope="col">Course'), html.index('scope="col">Section'))
        self.assertContains(response, "8:00 AM&ndash;10:00 AM")
        self.assertContains(response, "You do not need to prepare again")
        self.assertNotContains(response, "CSRF-protected")
        self.assertFalse(existing.TeachingMeeting.objects.exists())
        self.assertFalse(existing.ScheduleVersion.objects.exists())
        self.assertFalse(AttendanceResult.objects.exists())

    def test_combined_preview_keeps_saved_attribution_after_assignment_changes(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        round_ = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date)
        AttendanceResultService.confirm_present(actor=self.actor, checking_round=round_,
            manifest_revision=1, reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}])
        result = AttendanceResult.objects.get(meeting=meeting)
        AttendanceResultService.reconcile_attribution(actor=self.actor, result=result,
            expected_revision=1, faculty_user=self.replacement, reason="")
        existing.FacultyAssignment.objects.create(tenant=self.tenant, campus=self.campus,
            offering=self.offering, faculty_user=self.faculty, is_primary=True)
        response = self.daily()
        rows = response.context["occurrence_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["faculty"], self.replacement)
        self.assertEqual(len(rows[0]["occurrence"].linked_offerings), 2)
        self.assertContains(response, "Sections taught together", count=1)
        self.assertEqual(result.history.count(), 2)

    def test_preview_keeps_saved_route_order_inside_start_time_groups(self):
        first_slot = self.schedule().slots.get()
        second_slot = self.schedule(offering=self.combined_offering).slots.get()
        route = existing.SavedRouteService.save(actor=self.actor, tenant_id=self.tenant.pk,
            campus_id=self.campus.pk, name="Synthetic reversed room route",
            schedule_slot_ids=[second_slot.pk, first_slot.pk], expected_revision=None)
        response = self.daily(route=route.pk)
        rows = response.context["occurrence_rows"]
        self.assertEqual([r["occurrence"].primary_offering.pk for r in rows],
            [self.combined_offering.pk, self.offering.pk])
        self.assertEqual([r["route_position"] for r in rows], [1, 2])
        self.assertFalse(existing.TeachingMeeting.objects.exists())

    def test_conflicting_combined_preview_does_not_guess_from_current_assignments(self):
        version = self.schedule()
        self.coverage()
        self.coverage(self.combined_offering, faculty=self.replacement)
        meeting = MeetingService.generate(actor=self.actor, schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5), offerings=[self.combined_offering])
        response = self.daily()
        self.assertEqual(len(response.context["occurrence_rows"]), 1)
        self.assertIsNone(response.context["occurrence_rows"][0]["faculty"])
        self.assertContains(response, "Faculty unresolved")
        self.assertContains(response, "Needs attention")
        self.assertFalse(AttendanceResult.objects.filter(meeting=meeting).exists())

    def test_cutoff_no_routine_closure_action_but_existing_decision_is_preserved(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        closure = existing.save_closure(actor=self.actor, meeting=meeting, status="CLOSED",
            kind="HOLIDAY", pay_basis="REGULAR", reason="Synthetic historical decision", expected_revision=0)
        before = existing.AttendanceClosureDecision.objects.filter(pk=closure.pk).values().get()
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:cutoff_review"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "start_date": "2026-01-05", "end_date": "2026-01-05", "closure": meeting.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Dated holiday or suspension: no class")
        self.assertNotContains(response, "Record closure")
        self.assertNotContains(response, 'id="closure-decision"')
        self.assertTrue(response.context["review"].ready)
        self.assertContains(response, "Synthetic historical decision")
        closure.refresh_from_db()
        self.assertEqual(existing.AttendanceClosureDecision.objects.filter(pk=closure.pk).values().get(), before)
        self.assertEqual(closure.revision, 1)

    def test_manual_present_and_absence_office_practice_needs_no_closure_for_dtr(self):
        regular = self.meeting()
        self.coverage(self.combined_offering, faculty=self.replacement)
        part_time = MeetingService.generate(actor=self.actor,
            schedule_slot=self.schedule(offering=self.combined_offering).slots.get(),
            meeting_date=regular.meeting_date, offerings=[])
        round_ = CheckingRoundService.create(actor=self.actor, meetings=[regular, part_time],
            checking_date=regular.meeting_date, academic_year=self.academic_year, term=self.term)
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:round", args=[round_.public_id])
        present = self.client.post(url, {"action": "confirm_present", "manifest_revision": 1,
            "present_rows": [f"{regular.pk}:0"],
            f"present_note_{regular.pk}": "Synthetic office-calendar Regular treatment"})
        self.assertEqual(present.status_code, 302)
        absent = self.client.post(url, {"action": "exception", "meeting_id": part_time.pk,
            "expected_revision": 0, "submission_key": "synthetic-no-work-absence", "absence_code": "N",
            "missed_hours": "1.00", "reason": "Synthetic office-calendar Part-time treatment"})
        self.assertEqual(absent.status_code, 302)
        values = dict(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=regular.meeting_date, end_date=regular.meeting_date)
        review = existing.review_cutoff(**values)
        self.assertTrue(review.ready, review.blockers)
        publication = existing.publish_cutoff(**values, expected_fingerprint=review.fingerprint,
            submission_key="synthetic-office-practice", publication_reason="")
        paid = existing.preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        unpaid = existing.preview_dtr(actor=self.actor, publication=publication, faculty=self.replacement)
        self.assertTrue(paid.ready, paid.blockers)
        self.assertTrue(unpaid.ready, unpaid.blockers)
        self.assertEqual((paid.snapshot["basic_hours"], paid.snapshot["net_payable_hours"]), ("1.00", "1.00"))
        self.assertEqual((unpaid.snapshot["basic_hours"], unpaid.snapshot["net_payable_hours"]), ("1.00", "0.00"))
        self.assertFalse(existing.AttendanceClosureDecision.objects.exists())


class ChecklistSessionTests(TestCase):
    permission_codes = existing.FacultyAttendanceFoundationTests.permission_codes
    setUpTestData = classmethod(existing.FacultyAttendanceFoundationTests.setUpTestData.__func__)
    setUp = existing.FacultyAttendanceFoundationTests.setUp

    def select(self, **changes):
        self.client.force_login(self.actor)
        return self.client.get(reverse("faculty_attendance:checklist"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk, "month": "2026-01",
            "weekdays": ["0", "2", "6"], "paper": "Legal", "orientation": "portrait", "text_size": "14",
            **changes})

    def test_all_settings_restore_and_propagate_to_print_and_export(self):
        selected = self.select()
        expected = selected.context["monthly_form"].cleaned_data
        self.client.get(reverse("faculty_attendance:daily_encoding"))
        restored = self.client.get(reverse("faculty_attendance:checklist"))
        self.assertEqual(restored.context["monthly_form"].cleaned_data, expected)
        for route in ("monthly_print", "monthly_export"):
            output = self.client.get(reverse("faculty_attendance:" + route))
            self.assertEqual(output.status_code, 200)
        printed = self.client.get(reverse("faculty_attendance:monthly_print"))
        self.assertEqual(printed.context["print_settings"]["paper"], "Legal")
        self.assertEqual(printed.context["print_settings"]["text_size"], 14)
        self.assertIn("day_group=D45", restored.context["filter_query"])
        self.assertIn("paper=Legal", restored.context["filter_query"])
        self.assertIn(f"scope_tenant_id={self.tenant.pk}", restored.context["filter_query"])
        self.assertIn(f"scope_campus_id={self.campus.pk}", restored.context["filter_query"])

    def test_explicit_changes_override_remembered_days_month_and_print_settings(self):
        self.select()
        response = self.client.get(reverse("faculty_attendance:checklist"),
            {"day_group": "F", "month": "2026-02", "paper": "Letter", "text_size": "12"})
        cleaned = response.context["monthly_form"].cleaned_data
        self.assertEqual(cleaned["weekdays"], [4])
        self.assertEqual(cleaned["month"], date(2026, 2, 1))
        self.assertEqual((cleaned["paper"], cleaned["text_size"], cleaned["orientation"]), ("Letter", "12", "portrait"))
        self.assertEqual(self.client.get(reverse("faculty_attendance:checklist")).context["monthly_form"].cleaned_data, cleaned)

    def test_year_change_clears_incompatible_remembered_semester(self):
        self.select()
        year = AcademicYear.objects.create(tenant=self.tenant, code="NEXT", name="Next",
            start_date=date(2026, 6, 1), end_date=date(2027, 5, 31))
        response = self.client.get(reverse("faculty_attendance:checklist"), {"academic_year": year.pk})
        self.assertEqual(response.context["monthly_form"]["academic_year"].value(), str(year.pk))
        self.assertIsNone(response.context["monthly_form"]["term"].value())
        self.assertNotIn("term", self.client.session[CHECKLIST_SESSION_KEY]["values"])
        self.assertEqual(response.context["rows"], [])

    def test_inactive_academic_scope_is_not_restored(self):
        self.select()
        self.academic_year.is_active = False
        self.academic_year.save(update_fields=["is_active"])
        response = self.client.get(reverse("faculty_attendance:checklist"))
        values = self.client.session[CHECKLIST_SESSION_KEY]["values"]
        self.assertNotIn("academic_year", values)
        self.assertNotIn("term", values)
        self.assertEqual(response.context["rows"], [])

    def test_foreign_academic_scope_in_remembered_values_is_rejected(self):
        self.select()
        tenant = Tenant.objects.create(code="FOREIGN", name="Foreign")
        year = AcademicYear.objects.create(tenant=tenant, code="FOREIGN", name="Foreign",
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        term = Term.objects.create(tenant=tenant, academic_year=year, code="F", name="Foreign term",
            start_date=year.start_date, end_date=year.end_date)
        session = self.client.session
        saved = session[CHECKLIST_SESSION_KEY]
        saved["values"].update(academic_year=year.pk, term=term.pk, paper="Invalid", weekdays=[99])
        session[CHECKLIST_SESSION_KEY] = saved
        session.save()
        response = self.client.get(reverse("faculty_attendance:checklist"))
        self.assertEqual(response.context["rows"], [])
        self.assertNotIn("academic_year", self.client.session[CHECKLIST_SESSION_KEY]["values"])
        self.assertNotIn("term", self.client.session[CHECKLIST_SESSION_KEY]["values"])
        self.assertNotContains(response, "Foreign term")

    def test_campus_change_clears_remembered_academic_scope(self):
        self.select()
        campus = Campus.objects.create(tenant=self.tenant, code="C2", name="Campus 2")
        department = Department.objects.create(tenant=self.tenant, campus=campus, code="D3", name="Department 3")
        UserRole.objects.create(user=self.actor, role=self.role, tenant=self.tenant,
            campus=campus, department=department)
        response = self.client.get(reverse("faculty_attendance:checklist"),
            {"scope_tenant_id": self.tenant.pk, "scope_campus_id": campus.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["monthly_form"].is_bound)
        self.assertNotIn(CHECKLIST_SESSION_KEY, self.client.session)

    def test_validation_error_does_not_remember_invalid_value(self):
        self.select()
        invalid = self.client.get(reverse("faculty_attendance:checklist"), {"paper": "Invalid"})
        self.assertIn("paper", invalid.context["monthly_form"].errors)
        self.assertEqual(invalid.context["monthly_form"]["paper"].value(), "Invalid")
        restored = self.client.get(reverse("faculty_attendance:checklist"))
        self.assertEqual(restored.context["monthly_form"].cleaned_data["paper"], "A4")
        self.assertEqual(restored.context["monthly_form"].cleaned_data["term"], self.term)

    def test_logout_flushes_settings_and_new_login_starts_without_them(self):
        self.select()
        self.assertIn(CHECKLIST_SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(reverse("accounts:admin_logout")).status_code, 302)
        self.assertNotIn(CHECKLIST_SESSION_KEY, self.client.session)
        self.client.force_login(self.actor)
        fresh = self.client.get(reverse("faculty_attendance:checklist"))
        self.assertFalse(fresh.context["monthly_form"].is_bound)
        self.assertEqual(fresh.context["rows"], [])
