"""Cutoff-first DTR selection, immutable print versions and automatic scope."""
from copy import deepcopy
from datetime import date
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.rbac.models import Permission, Role, UserPermission, UserRole
from . import tests_faculty_cutoffs as fixtures
from .daily_encoding import prepare_daily_encoding
from .dtr import save_adjustment
from .dtr_views import _cutoff_key
from .models import AttendanceCutoffPublication, DTRAdjustment, FacultyDTR, TeachingMeeting
from .permissions import DTR_VIEW_PERMISSION, DTR_PRINT_PERMISSION, DTR_EDIT_PERMISSION


class DTRSelectionTests(TestCase):
    permission_codes = fixtures.FacultyCutoffTests.permission_codes
    setUpTestData = classmethod(fixtures.FacultyCutoffTests.setUpTestData.__func__)
    setUp = fixtures.FacultyCutoffTests.setUp
    aware = fixtures.FacultyCutoffTests.aware
    coverage = fixtures.FacultyCutoffTests.coverage
    scope = fixtures.FacultyCutoffTests.scope
    review = fixtures.FacultyCutoffTests.review
    slice = fixtures.FacultyCutoffTests.slice
    pair = fixtures.FacultyCutoffTests.pair
    confirm = fixtures.FacultyCutoffTests.confirm
    publish = fixtures.FacultyCutoffTests.publish
    final = fixtures.FacultyCutoffTests.final

    def get_page(self, **query):
        self.client.force_login(self.actor)
        return self.client.get(reverse("faculty_attendance:dtr_review"), query)

    def test_one_cutoff_contains_all_published_faculties_and_legacy_stale_pair_recovers(self):
        self.pair(confirm_second=True)
        first = self.publish()
        second = self.publish(self.replacement, key="second")
        response = self.get_page(publication=second.pk, faculty=self.faculty.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["faculty"], self.faculty)
        self.assertEqual(response.context["publication"], first)
        self.assertEqual(len(response.context["cutoffs"]), 1)
        self.assertEqual({r["faculty"].pk for r in response.context["summary"]}, {self.faculty.pk, self.replacement.pk})
        response = self.get_page(cutoff=_cutoff_key(first), faculty=self.replacement.pk)
        self.assertEqual(response.context["publication"], second)
        # POST never silently changes the submitted faculty/publication pair.
        response = self.client.post(reverse("faculty_attendance:dtr_review"), {
            "publication": second.pk, "faculty": self.faculty.pk, "action": "adjustment",
            "entry_date": "2026-01-05", "kind": "OTHER", "hours": "0.25"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DTRAdjustment.objects.exists())

    def test_cutoff_change_resets_stale_faculty_and_ajax_refreshes_entire_workspace(self):
        self.pair(confirm_second=True)
        first = self.publish()
        self.publish(self.replacement, key="other")
        self.combined_offering.attendance_coverages.update(effective_until=self.aware(2026, 1, 6))
        prepare_daily_encoding(actor=self.actor, offerings=[self.offering], academic_year=self.academic_year,
            term=self.term, meeting_date=date(2026, 1, 12))
        self.confirm(TeachingMeeting.objects.get(meeting_date=date(2026, 1, 12)))
        with patch.object(self, "scope", return_value={**self.scope(), "start_date": date(2026, 1, 12), "end_date": date(2026, 1, 12)}):
            later = self.publish(key="later-cutoff")
        response = self.get_page(cutoff=_cutoff_key(later), faculty=self.replacement.pk, review="1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["faculty"], self.faculty)
        response = self.client.get(reverse("faculty_attendance:dtr_review"), {
            "cutoff": _cutoff_key(first), "partial": "selection", "reset_faculty": "1"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn('id="cutoff-select"', payload["workspace_html"])
        self.assertIn(self.replacement.full_name, payload["workspace_html"])
        self.assertEqual(payload["cutoff"], _cutoff_key(first))

    def test_latest_final_print_and_previous_version_history_use_saved_values(self):
        self.pair()
        publication = self.publish()
        first = self.final(publication)
        saved_first = deepcopy(first.snapshot)
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
            department=self.department, entry_date=publication.start_date, kind="OTHER", hours="0.25", reason="")
        second = self.final(publication)
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
            department=self.department, entry_date=publication.start_date, kind="OTHER", hours="0.25", reason="")
        response = self.get_page(cutoff=_cutoff_key(publication), faculty=self.faculty.pk)
        self.assertEqual(response.context["final"], second)
        self.assertEqual(response.context["displayed_final"], second)
        self.assertEqual(response.context["displayed_snapshot"]["net_payable_hours"], "0.75")
        previous = self.get_page(cutoff=_cutoff_key(publication), faculty=self.faculty.pk, version="1")
        self.assertEqual(previous.context["displayed_final"], first)
        self.assertEqual(previous.context["displayed_snapshot"]["net_payable_hours"], "1.00")
        self.assertEqual(self.get_page(cutoff=_cutoff_key(publication), faculty=self.faculty.pk, version="999").status_code, 404)
        self.assertContains(response, "Print saved DTR R2")
        self.assertContains(previous, "Print saved DTR R1")
        summary = self.client.get(reverse("faculty_attendance:dtr_summary"), {"cutoff": _cutoff_key(publication)})
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.context["rows"][0]["print_snapshot"]["net_payable_hours"], "0.75")
        self.assertContains(summary, "Changes awaiting review")
        for final, amount in ((first, "1.00"), (second, "0.75")):
            printed = self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id]))
            self.assertEqual(printed.context["snapshot"]["net_payable_hours"], amount)
        first.refresh_from_db()
        self.assertEqual(first.snapshot, saved_first)

    def test_cutoff_summary_aggregates_latest_publication_for_each_faculty(self):
        self.pair(confirm_second=True)
        first = self.publish()
        self.publish(self.replacement, key="second")
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:dtr_summary"), {"cutoff": _cutoff_key(first)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual({r["faculty"].pk for r in response.context["rows"]}, {self.faculty.pk, self.replacement.pk})

    def test_department_is_derived_on_create_and_correction_and_spoof_is_ignored(self):
        self.pair()
        publication = self.publish()
        self.client.force_login(self.actor)
        data = {"publication": publication.pk, "faculty": self.faculty.pk, "action": "adjustment",
            "entry_date": "2026-01-05", "kind": "OTHER", "hours": "0.25", "reason": ""}
        response = self.client.post(reverse("faculty_attendance:dtr_review"), data)
        self.assertEqual(response.status_code, 302)
        entry = DTRAdjustment.objects.get()
        self.assertEqual(entry.department_id, self.department.pk)
        data.update(previous_id=entry.pk, expected_revision=entry.revision, hours="0.50", department=self.other_department.pk)
        response = self.client.post(reverse("faculty_attendance:dtr_review"), data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(response.json()["ok"])
        revised = DTRAdjustment.objects.get(revision=2)
        self.assertEqual(revised.department_id, entry.department_id)
        self.assertEqual(revised.supersedes_id, entry.pk)
        self.assertNotIn('<select name="department"', response.json()["faculty_html"])

    def test_missing_ac_retains_values_and_dated_teaching_resolves_department(self):
        self.pair()
        publication = self.publish()
        self.client.force_login(self.actor)
        data = {"publication": publication.pk, "faculty": self.faculty.pk, "action": "adjustment",
            "entry_date": "2026-01-05", "kind": "ADMIN", "hours": "0.75", "reason": "Retained"}
        response = self.client.post(reverse("faculty_attendance:dtr_review"), data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertIn("no active AC assignment", response.json()["faculty_html"])
        self.assertIn("Retained", response.json()["faculty_html"])
        data["kind"] = "OTHER"
        # Dated class evidence narrows a wider faculty scope without asking the checker.
        response = self.client.post(reverse("faculty_attendance:dtr_review"), data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(DTRAdjustment.objects.get().department_id, self.department.pk)

    def test_general_role_without_workload_is_hidden_but_scoped_ac_is_available(self):
        role = Role.objects.create(code="AC", name="Synthetic AC")
        membership = UserRole.objects.create(user=self.faculty, role=role,
            tenant=self.tenant, campus=self.campus, department=self.department)
        empty = {**self.scope(), "start_date": date(2026, 1, 6), "end_date": date(2026, 1, 6)}
        with patch.object(self, "scope", return_value=empty):
            publication = self.publish()
        # Retain a legacy empty publication, but do not invent work from a general role.
        membership.tenant = membership.campus = membership.department = None
        membership.save()
        response = self.get_page(publication=publication.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["summary"])
        self.assertIsNone(response.context["faculty"])
        self.assertNotContains(response, f'<option value="{self.faculty.pk}">')
        self.assertEqual(AttendanceCutoffPublication.objects.count(), 1)
        self.assertEqual(self.client.post(reverse("faculty_attendance:dtr_review"), {
            "publication": publication.pk, "action": "adjustment"}).status_code, 403)
        self.assertFalse(DTRAdjustment.objects.exists())
        membership.tenant, membership.campus, membership.department = self.tenant, self.campus, self.department
        membership.save()
        response = self.get_page(publication=publication.pk)
        self.assertEqual([r["faculty"].pk for r in response.context["summary"]], [self.faculty.pk])

    def test_valid_pending_publication_faculty_has_actionable_state_not_404(self):
        self.pair()
        first = self.publish()
        response = self.get_page(cutoff=_cutoff_key(first), faculty=self.replacement.pk, review="1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["faculty"], self.replacement)
        self.assertTrue(response.context["pending_publication"])
        self.assertContains(response, "Open faculty cutoff processing")
        self.assertNotContains(response, "Finalize DTR hours")
        summary = self.client.get(reverse("faculty_attendance:dtr_summary"), {"cutoff": _cutoff_key(first)})
        self.assertEqual(summary.status_code, 200)
        self.assertContains(summary, "Awaiting attendance publication")

    def test_superseded_publication_get_resolves_latest_but_post_remains_rejected(self):
        first, _ = self.pair()
        publication = self.publish()
        from .observations import AttendanceResultService
        from .models import AttendanceResult
        result = AttendanceResult.objects.get(meeting=first)
        AttendanceResultService.correct_early_dismissal(actor=self.actor, result=result,
            expected_revision=result.revision, minutes=10, reason="Synthetic corrected finding")
        newest = self.publish(key="newest")
        response = self.get_page(publication=publication.pk, faculty=self.faculty.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["publication"], newest)
        self.assertEqual(self.client.post(reverse("faculty_attendance:dtr_review"), {
            "publication": publication.pk, "faculty": self.faculty.pk, "action": "adjustment"}).status_code, 404)

    def test_selection_and_print_scope_and_direct_denies(self):
        self.pair()
        publication = self.publish()
        final = self.final(publication)
        self.client.force_login(self.actor)
        for code in (DTR_VIEW_PERMISSION, DTR_PRINT_PERMISSION, DTR_EDIT_PERMISSION):
            deny = UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=code), grant_type="DENY")
            if code == DTR_VIEW_PERMISSION:
                response = self.get_page(cutoff=_cutoff_key(publication), faculty=self.faculty.pk)
                self.assertEqual(response.status_code, 403)
            elif code == DTR_PRINT_PERMISSION:
                viewed = self.get_page(cutoff=_cutoff_key(publication), faculty=self.faculty.pk)
                self.assertEqual(viewed.context["displayed_final"], final)
                self.assertFalse(viewed.context["displayed_can_print"])
                self.assertNotContains(viewed, "Print saved DTR")
                self.assertEqual(self.client.get(reverse("faculty_attendance:dtr_summary"), {"cutoff": _cutoff_key(publication)}).status_code, 403)
                self.assertEqual(self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id])).status_code, 403)
            else:
                response = self.client.post(reverse("faculty_attendance:dtr_review"), {"publication": publication.pk,
                    "faculty": self.faculty.pk, "action": "adjustment", "entry_date": "2026-01-05", "kind": "OTHER", "hours": "1"})
                self.assertEqual(response.status_code, 403)
            deny.delete()
        self.assertEqual(self.get_page(cutoff="999:999:2026-01-05:2026-01-05").status_code, 404)
        self.assertFalse(DTRAdjustment.objects.exists())

    def test_automatic_department_entry_still_requires_csrf(self):
        self.pair()
        publication = self.publish()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        url = reverse("faculty_attendance:dtr_review")
        data = {"publication": publication.pk, "faculty": self.faculty.pk, "action": "adjustment",
            "entry_date": "2026-01-05", "kind": "OTHER", "hours": "0.25"}
        self.assertEqual(client.post(url, data).status_code, 403)
        self.assertFalse(DTRAdjustment.objects.exists())
        self.assertEqual(client.get(url, {"cutoff": _cutoff_key(publication)}).status_code, 200)
        data["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
        self.assertEqual(client.post(url, data).status_code, 302)
        self.assertEqual(DTRAdjustment.objects.get().department_id, self.department.pk)

    def test_workspace_names_numbering_and_ajax_selection_use_structured_fields(self):
        self.pair(confirm_second=True)
        first = self.publish()
        self.publish(self.replacement, key="workspace-second")
        User = type(self.faculty)
        User.objects.filter(pk=self.faculty.pk).update(first_name="Laarni Grace", middle_name="C.", last_name=" Lagman ")
        User.objects.filter(pk=self.replacement.pk).update(first_name="Juan", middle_name="", last_name="Garcia")
        self.faculty.refresh_from_db(); self.replacement.refresh_from_db()
        response = self.get_page(cutoff=_cutoff_key(first), faculty=self.faculty.pk)
        rows = response.context["summary"]
        self.assertEqual([(row["row_number"], row["faculty_display_name"]) for row in rows],
            [(1, "Garcia, Juan"), (2, "Lagman, Laarni Grace C.")])
        self.assertContains(response, "Lagman, Laarni Grace C.")
        selected = self.client.get(reverse("faculty_attendance:dtr_review"),
            {"cutoff": _cutoff_key(first), "faculty": self.replacement.pk, "partial": "faculty"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()
        self.assertEqual(selected["faculty_id"], self.replacement.pk)
        self.assertIn("Garcia, Juan", selected["faculty_html"])
        self.assertEqual(selected["cutoff"], _cutoff_key(first))
        self.assertEqual(selected["faculty_html"].count("data-dtr-detail-table"), 1)
        # Equal surname/given names use stable user identity, not incidental row order.
        User.objects.filter(pk__in=[self.faculty.pk,self.replacement.pk]).update(first_name="Juan",last_name="Garcia")
        tied = self.get_page(cutoff=_cutoff_key(first))
        self.assertEqual([r["faculty"].pk for r in tied.context["summary"]], sorted([self.faculty.pk,self.replacement.pk]))

    def test_one_table_saved_previous_and_current_review_keep_correct_evidence(self):
        self.pair()
        publication = self.publish()
        first = self.final(publication)
        original = deepcopy(first.snapshot)
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
            department=self.department, entry_date=publication.start_date, kind="OTHER", hours="0.25", reason="First correction")
        second = self.final(publication)
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
            department=self.department, entry_date=publication.start_date, kind="OTHER", hours="0.25", reason="Pending correction")
        query = {"cutoff":_cutoff_key(publication), "faculty":self.faculty.pk}
        saved = self.get_page(**query)
        self.assertFalse(saved.context["review_current"])
        self.assertEqual(saved.context["detail_snapshot"]["net_payable_hours"], "0.75")
        self.assertContains(saved, "Changes awaiting review")
        self.assertContains(saved, "data-dtr-detail-table", count=1)
        self.assertNotContains(saved, 'data-dtr-ajax="adjustment"')
        self.assertNotContains(saved, "Current calculation / changes for review")
        self.assertNotContains(saved, "Present (assigned class; no exception)")
        previous = self.get_page(**query, version="1")
        self.assertContains(previous, "previous version")
        self.assertEqual(previous.context["detail_snapshot"]["net_payable_hours"], "1.00")
        self.assertNotContains(previous, "Correct E")
        self.assertNotContains(previous, "Finalize DTR hours")
        current = self.get_page(**query, view="current")
        self.assertTrue(current.context["review_current"])
        self.assertEqual(current.context["detail_snapshot"]["net_payable_hours"], "0.50")
        self.assertContains(current, "data-dtr-detail-table", count=1)
        self.assertContains(current, 'data-dtr-ajax="adjustment"')
        self.assertContains(current, "Print saved DTR R2")
        for heading in ("Date", "Subject / Section", "Time", "Attendance / Finding", "Teaching", "Admin", "A", "N", "L", "Early", "Other", "Leave", "Action"):
            self.assertIn(f">{heading}", current.content.decode())
        self.assertNotContains(current, "A 0.00 / N")
        first.refresh_from_db()
        self.assertEqual(first.snapshot, original)
        self.assertEqual(second.snapshot["net_payable_hours"], "0.75")

    def test_correction_navigation_and_post_keep_current_record_ids_and_number(self):
        self.pair()
        publication = self.publish()
        final = self.final(publication)
        self.get_page(cutoff=_cutoff_key(publication))
        posted = self.client.post(reverse("faculty_attendance:dtr_review"), {
            "publication":publication.pk,"faculty":self.faculty.pk,"action":"adjustment",
            "entry_date":"2026-01-05","kind":"OTHER","hours":"0.25"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(posted.status_code,200)
        self.assertEqual(posted.json()["view"],"current")
        self.assertIn('<td class="dtr-number">1</td>',posted.json()["summary_row_html"])
        self.assertIn("Current DTR for review",posted.json()["faculty_html"])
        entry = DTRAdjustment.objects.get()
        edit = self.get_page(cutoff=_cutoff_key(publication),faculty=self.faculty.pk,edit=entry.pk)
        self.assertTrue(edit.context["review_current"])
        self.assertEqual(edit.context["edit_item"],entry)
        self.assertContains(edit,'data-dtr-navigation')
        early = self.get_page(cutoff=_cutoff_key(publication),faculty=self.faculty.pk,
            early=publication.entries.get().meeting_id)
        self.assertTrue(early.context["show_early_form"])
        self.assertContains(early,"Save attendance revision")
        final.refresh_from_db()
        self.assertEqual(final.snapshot["net_payable_hours"],"1.00")

    def test_failed_finalization_retains_review_and_bound_errors(self):
        self.pair()
        publication = self.publish()
        self.final(publication)
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
            department=self.department, entry_date=publication.start_date, kind="OTHER", hours="0.25", reason="")
        current = self.get_page(cutoff=_cutoff_key(publication),faculty=self.faculty.pk,view="current")
        response = self.client.post(reverse("faculty_attendance:dtr_review"),{
            "publication":publication.pk,"faculty":self.faculty.pk,"action":"finalize",
            "expected_fingerprint":current.context["preview"].fingerprint},HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.json()["view"],"current")
        self.assertIn("errorlist",response.json()["faculty_html"])
        self.assertEqual(FacultyDTR.objects.count(),1)
