"""Exceptions-only policy: disposable source, publication and DTR regressions."""
from datetime import date
from copy import deepcopy

from django.test import Client, TestCase
from django.urls import reverse

from . import tests_faculty_cutoffs as fixtures
from .daily_encoding import prepare_daily_encoding
from .dtr import preview_dtr
from .models import AttendanceResult, TeachingMeeting
from .observations import ObservationService, AttendanceResultService, CheckingRoundService
from .cutoffs import faculty_published_entries
from .permissions import ENCODE_PERMISSION, PUBLISH_FACULTY_PERMISSION
from apps.rbac.models import Permission, UserPermission
from django.core.exceptions import PermissionDenied
from .monitoring import term_summary
from .dtr import save_admin_hours, current_adjustments
from apps.rbac.models import Role, UserRole
from .permissions import DTR_EDIT_PERMISSION
from .models import DTRAdjustment
from django.core.exceptions import ValidationError


class ExceptionsOnlyTests(TestCase):
    permission_codes = fixtures.FacultyCutoffTests.permission_codes
    setUpTestData = classmethod(fixtures.FacultyCutoffTests.setUpTestData.__func__)
    setUp = fixtures.FacultyCutoffTests.setUp
    aware = fixtures.FacultyCutoffTests.aware
    coverage = fixtures.FacultyCutoffTests.coverage
    scope = fixtures.FacultyCutoffTests.scope
    review = fixtures.FacultyCutoffTests.review
    slice = fixtures.FacultyCutoffTests.slice
    publish = fixtures.FacultyCutoffTests.publish
    final = fixtures.FacultyCutoffTests.final
    confirm = fixtures.FacultyCutoffTests.confirm
    meeting = fixtures.FacultyCutoffTests.meeting
    schedule = fixtures.FacultyCutoffTests.schedule

    def test_assigned_unprepared_class_is_present_publishable_and_finalizable(self):
        self.coverage()
        row = self.slice()
        self.assertTrue(row.ready)
        self.assertEqual(len(row.records), 1)
        self.assertFalse(TeachingMeeting.objects.exists())
        self.assertFalse(AttendanceResult.objects.exists())
        fingerprint = row.fingerprint
        publication = self.publish()
        self.assertEqual(row.scope_snapshot, self.slice().scope_snapshot)
        before, after = row.records[0], self.slice().records[0]
        self.assertEqual((before.occurrence.occurrence_key, before.occurrence.source_schedule_text, before.occurrence.source_room,
            before.meeting.starts_at, before.meeting.ends_at),
            (after.occurrence.occurrence_key, after.occurrence.source_schedule_text, after.occurrence.source_room,
             after.meeting.starts_at, after.meeting.ends_at))
        self.assertEqual(self.slice().fingerprint, fingerprint)
        entry = publication.entries.get()
        self.assertEqual(entry.status, "PRESENT")
        self.assertEqual(entry.meeting_snapshot["attendance_basis"], "ASSIGNED_DEFAULT")
        self.assertFalse(AttendanceResult.objects.exists())
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(preview.ready, preview.blockers)
        self.assertEqual(preview.snapshot["net_payable_hours"], "1.00")
        final = self.final(publication)
        saved = deepcopy(final.snapshot)
        self.assertEqual(self.publish(key="retry").pk, publication.pk)
        final.refresh_from_db()
        self.assertEqual(final.snapshot, saved)

    def test_prepared_blank_is_present_without_confirming_rows(self):
        self.coverage()
        prepare_daily_encoding(actor=self.actor, offerings=[self.offering], academic_year=self.academic_year,
            term=self.term, meeting_date=date(2026, 1, 5))
        publication = self.publish()
        self.assertTrue(preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty).ready)
        self.assertFalse(AttendanceResult.objects.exists())

    def test_unassigned_does_not_block_or_add_hours(self):
        self.coverage()
        review = self.review()
        self.assertTrue(review.unattributed)
        self.assertEqual({row.faculty.pk for row in review.slices if row.records}, {self.faculty.pk})
        self.assertEqual(self.publish().entries.count(), 1)

    def test_cathlea_equivalent_two_hour_assigned_saturday_is_normal(self):
        self.term.end_date = date(2026, 12, 31)
        self.term.save(update_fields=["end_date"])
        self.offering.schedule_text = "S 13:00-15:00"
        self.offering.room = "G-303"
        self.offering.save(update_fields=["schedule_text", "room"])
        self.coverage(effective_from=self.aware(2026, 10, 2, 8))
        scope = {**self.scope(), "start_date": date(2026, 10, 3), "end_date": date(2026, 10, 3)}
        from unittest.mock import patch
        with patch.object(self, "scope", return_value=scope):
            self.assertTrue(self.slice().ready)
            publication = self.publish()
        self.assertEqual(preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty).snapshot["net_payable_hours"], "2.00")
        self.assertFalse(AttendanceResult.objects.exists())

    def test_publication_permission_does_not_require_or_grant_encoding(self):
        self.coverage()
        deny = UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=ENCODE_PERMISSION), grant_type="DENY")
        publication = self.publish()
        self.assertEqual(publication.entries.count(), 1)
        self.assertTrue(UserPermission.objects.filter(pk=deny.pk).exists())
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=PUBLISH_FACULTY_PERMISSION), grant_type="DENY")
        with self.assertRaises(PermissionDenied):
            self.publish(key="denied")

    def test_later_exception_invalidates_normal_publication_without_rewriting_it(self):
        self.coverage()
        publication = self.publish()
        saved = deepcopy(publication.entries.get().meeting_snapshot)
        meeting = TeachingMeeting.objects.get()
        round_ = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date)
        observation = ObservationService.record(actor=self.actor, checking_round=round_, meeting_id=meeting.pk,
            manifest_revision=round_.manifest_revision, submission_key="later-exception",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 15}])
        AttendanceResultService.select_observation(actor=self.actor, observation=observation, expected_revision=0, reason="")
        self.assertFalse(preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty).ready)
        corrected = self.publish(key="exception-published")
        self.assertEqual(preview_dtr(actor=self.actor, publication=corrected, faculty=self.faculty).snapshot["net_payable_hours"], "0.75")
        self.assertEqual(publication.entries.get().meeting_snapshot, saved)

    def test_combined_normal_occurrence_is_counted_once_and_published_visibility_is_saved(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        publication = self.publish()
        self.assertEqual(publication.entries.count(), 1)
        self.assertEqual(self.final(publication).snapshot["class_count"], 1)
        with self.assertRaises(PermissionDenied):
            faculty_published_entries(faculty_user=self.faculty, tenant_id=self.tenant.pk,
                campus_id=self.campus.pk, start_date=meeting.meeting_date, end_date=meeting.meeting_date)
        from .tests_monitoring import TermMonitoringTests
        TermMonitoringTests.faculty_visibility(self)
        entries, _, _ = faculty_published_entries(faculty_user=self.faculty, tenant_id=self.tenant.pk,
            campus_id=self.campus.pk, start_date=meeting.meeting_date, end_date=meeting.meeting_date)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, "PRESENT")

    def test_term_normal_hours_are_not_fabricated_checker_verification(self):
        self.coverage()
        report = term_summary(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, as_of=date(2026, 1, 5))
        row = report["rows"][0]
        self.assertEqual(row["actual"], row["expected"])
        self.assertEqual(row["unverified"], 0)
        self.assertIsNone(row["latest_verified"])
        self.assertFalse(AttendanceResult.objects.exists())


class AdminHoursBatchTests(TestCase):
    permission_codes = ExceptionsOnlyTests.permission_codes
    setUpTestData = ExceptionsOnlyTests.__dict__["setUpTestData"]
    aware = ExceptionsOnlyTests.aware
    coverage = ExceptionsOnlyTests.coverage
    review = ExceptionsOnlyTests.review
    slice = ExceptionsOnlyTests.slice
    publish = ExceptionsOnlyTests.publish
    final = ExceptionsOnlyTests.final

    def scope(self):
        return {**fixtures.FacultyCutoffTests.scope(self), "end_date": date(2026, 1, 6)}

    def setUp(self):
        fixtures.FacultyCutoffTests.setUp(self)
        role = Role.objects.create(code="AC", name="Synthetic dated office-hours owner")
        UserRole.objects.create(user=self.faculty, role=role, tenant=self.tenant,
            campus=self.campus, department=self.department)
        self.coverage()
        self.publication = self.publish()
        self.client.force_login(self.actor)

    def rows(self):
        return [{"entry_date": date(2026, 1, 5), "hours": "1.25"},
                {"entry_date": date(2026, 1, 6), "hours": "2.50"}]

    def data(self, rows=None):
        rows = rows if rows is not None else self.rows()
        data = {"publication": self.publication.pk, "faculty": self.faculty.pk, "action": "admin_hours",
            "admin_hours-TOTAL_FORMS": str(len(rows)), "admin_hours-INITIAL_FORMS": "0"}
        for i, row in enumerate(rows):
            data.update({f"admin_hours-{i}-{key}": value for key, value in row.items()})
        return data

    def test_batch_save_retry_correction_removal_and_restore_are_revisioned_not_duplicate(self):
        save = lambda rows: save_admin_hours(actor=self.actor, publication=self.publication, faculty=self.faculty, rows=rows)
        first = save(self.rows())
        final = self.final(self.publication)
        saved_snapshot = deepcopy(final.snapshot)
        save(self.rows())
        self.assertEqual(DTRAdjustment.objects.count(), 2)
        changed = [{"entry_date": row.entry_date, "hours": "3.00" if i == 0 else str(row.hours),
            "previous_id": row.pk, "expected_revision": row.revision} for i, row in enumerate(first)]
        latest = save(changed)
        self.assertEqual(preview_dtr(actor=self.actor, publication=self.publication, faculty=self.faculty).snapshot["admin_hours"], "5.50")
        save([{**changed[0], "previous_id": latest[0].pk, "expected_revision": latest[0].revision, "DELETE": True}])
        zero = next(r for r in current_adjustments(publication=self.publication, faculty=self.faculty) if r.entry_date == date(2026, 1, 5))
        save([{"entry_date": zero.entry_date, "hours": "1.25", "previous_id": zero.pk, "expected_revision": zero.revision}])
        self.assertEqual(preview_dtr(actor=self.actor, publication=self.publication, faculty=self.faculty).snapshot["admin_hours"], "3.75")
        self.assertEqual(len(current_adjustments(publication=self.publication, faculty=self.faculty)), 2)
        final.refresh_from_db()
        self.assertEqual(final.snapshot, saved_snapshot)

    def test_ajax_saves_all_without_department_and_failed_rows_retain_values_atomically(self):
        url = reverse("faculty_attendance:dtr_review")
        data = self.data()
        data["admin_hours-1-entry_date"] = "2026-02-01"
        response = self.client.post(url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Date must be within this cutoff", response.json()["faculty_html"])
        self.assertIn('value="1.25"', response.json()["faculty_html"])
        self.assertFalse(DTRAdjustment.objects.exists())
        response = self.client.post(url, self.data(), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(DTRAdjustment.objects.count(), 2)
        self.assertNotIn('<select name="department"', response.json()["faculty_html"])

    def test_duplicate_dates_and_stale_or_foreign_ids_reject_without_partial_save(self):
        rows = self.rows()
        rows[1]["entry_date"] = rows[0]["entry_date"]
        with self.assertRaises(ValidationError):
            save_admin_hours(actor=self.actor, publication=self.publication, faculty=self.faculty, rows=rows)
        self.assertFalse(DTRAdjustment.objects.exists())
        rows = self.rows()
        rows[1].update(previous_id=999999, expected_revision=1)
        with self.assertRaises(ValidationError):
            save_admin_hours(actor=self.actor, publication=self.publication, faculty=self.faculty, rows=rows)
        self.assertFalse(DTRAdjustment.objects.exists())
        saved = save_admin_hours(actor=self.actor, publication=self.publication, faculty=self.faculty, rows=self.rows())
        with self.assertRaises(ValidationError):
            save_admin_hours(actor=self.actor, publication=self.publication, faculty=self.faculty,
                rows=[{"entry_date": saved[0].entry_date, "hours": "3", "previous_id": saved[0].pk, "expected_revision": 0}])
        self.assertEqual(DTRAdjustment.objects.count(), 2)

    def test_batch_direct_deny_and_csrf_remain_enforced(self):
        url = reverse("faculty_attendance:dtr_review")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        self.assertEqual(client.post(url, self.data()).status_code, 403)
        deny = UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=DTR_EDIT_PERMISSION), grant_type="DENY")
        self.assertEqual(self.client.post(url, self.data()).status_code, 403)
        with self.assertRaises(PermissionDenied):
            save_admin_hours(actor=self.actor, publication=self.publication, faculty=self.faculty, rows=self.rows())
        self.assertFalse(DTRAdjustment.objects.exists())
        deny.delete()

    def test_service_write_error_retains_row_error_and_rolls_back_batch(self):
        from unittest.mock import patch
        from .dtr import save_adjustment
        calls = []
        def failing(**kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise ValidationError({"kind": "Synthetic changed AC scope"})
            return save_adjustment(**kwargs)
        with patch("apps.faculty_attendance.dtr.save_adjustment", side_effect=failing):
            response = self.client.post(reverse("faculty_attendance:dtr_review"), self.data(), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Synthetic changed AC scope", response.json()["faculty_html"])
        self.assertIn('value="2.50"', response.json()["faculty_html"])
        self.assertFalse(DTRAdjustment.objects.exists())
