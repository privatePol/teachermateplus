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
