"""Disposable faculty publication, completeness and partial DTR regressions."""
from copy import deepcopy
from datetime import date
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, get_ident
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, connections, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.http import Http404
from django.urls import reverse

from apps.rbac.models import Permission, RolePermission, UserPermission, UserRole, Role
from . import tests as fixtures
from .cutoffs import publish_cutoff, review_cutoff, faculty_published_entries
from .daily_encoding import prepare_daily_encoding
from .dtr import final_dtr_departments, finalize_dtr, preview_dtr, save_adjustment
from .dtr_intervals import save_mixed_decision
from .faculty_cutoffs import publish_faculty_cutoffs, review_faculty_cutoff
from .faculty_cutoff_views import finalize_ready_faculty, processing_rows
from .models import AttendanceCutoffPublication, AttendanceResult, FacultyDTR, FacultyCoverage
from .observations import AttendanceResultService, CheckingRoundService, ObservationService
from .permissions import PUBLISH_FACULTY_PERMISSION, DTR_FINALIZE_PERMISSION, DTR_PRINT_PERMISSION, DTR_VIEW_PERMISSION, VIEW_PERMISSION
from .services import MeetingService, SubstitutionService, _meeting_snapshot
from .closures import save_closure


class FacultyCutoffTests(TestCase):
    permission_codes = fixtures.FacultyAttendanceFoundationTests.permission_codes + (PUBLISH_FACULTY_PERMISSION,)
    setUpTestData = classmethod(fixtures.FacultyAttendanceFoundationTests.setUpTestData.__func__)
    setUp = fixtures.FacultyAttendanceFoundationTests.setUp
    aware = fixtures.FacultyAttendanceFoundationTests.aware
    coverage = fixtures.FacultyAttendanceFoundationTests.coverage
    schedule = fixtures.FacultyAttendanceFoundationTests.schedule
    meeting = fixtures.FacultyAttendanceFoundationTests.meeting
    _published_dtr_cutoff = fixtures.FacultyAttendanceFoundationTests._published_dtr_cutoff

    def scope(self):
        return dict(tenant_id=self.tenant.pk, campus_id=self.campus.pk, academic_year=self.academic_year,
                    term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))

    def review(self):
        return review_faculty_cutoff(actor=self.actor, **self.scope())

    def slice(self, faculty=None):
        return next(row for row in self.review().slices if row.faculty.pk == (faculty or self.faculty).pk)

    def confirm(self, meeting):
        checking_round = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date)
        AttendanceResultService.confirm_present(actor=self.actor, checking_round=checking_round,
            manifest_revision=checking_round.manifest_revision,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}])

    def pair(self, confirm_second=False):
        self.coverage()
        self.coverage(offering=self.combined_offering, faculty=self.replacement)
        prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 5))
        meetings = list(fixtures.TeachingMeeting.objects.order_by("pk"))
        first = next(m for m in meetings if m.faculty_user_id == self.faculty.pk)
        second = next(m for m in meetings if m.faculty_user_id == self.replacement.pk)
        self.confirm(first)
        if confirm_second:
            self.confirm(second)
        return first, second

    def publish(self, faculty=None, key="faculty-publication", fingerprint=None):
        faculty = faculty or self.faculty
        row = self.slice(faculty)
        return publish_faculty_cutoffs(actor=self.actor, **self.scope(), faculty_ids=[faculty.pk],
            expected_fingerprints={str(faculty.pk): fingerprint or row.fingerprint}, submission_key=key)[0]

    def final(self, publication, faculty=None):
        faculty = faculty or self.faculty
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=faculty)
        return finalize_dtr(actor=self.actor, publication=publication, faculty=faculty,
            expected_fingerprint=preview.fingerprint, reason="Synthetic reviewed final", faculty_review_complete=True)

    def query(self):
        return {k: v.pk if hasattr(v, "pk") else v.isoformat() for k, v in self.scope().items()
                if k not in ("tenant_id", "campus_id")}

    def test_ready_faculty_publishes_and_finalizes_beside_known_blocker(self):
        first, second = self.pair()
        self.assertTrue(self.slice().ready)
        self.assertFalse(self.slice(self.replacement).ready)
        self.assertFalse(review_cutoff(actor=self.actor, **self.scope()).ready)
        publication = self.publish()
        self.assertEqual(publication.entries.count(), 1)
        self.assertEqual(publication.entries.get().meeting_id, first.pk)
        self.assertFalse(FacultyDTR.objects.exists())
        final = self.final(publication)
        self.assertEqual(final.snapshot["net_payable_hours"], "1.00")
        self.assertFalse(AttendanceResult.objects.filter(meeting=second, revision__gt=0).exists())

    def test_own_blocker_rejects_publication_atomically(self):
        self.pair()
        before = AttendanceCutoffPublication.objects.count()
        with self.assertRaises(ValidationError):
            self.publish(self.replacement)
        self.assertEqual(AttendanceCutoffPublication.objects.count(), before)

    def test_unique_unmaterialized_occurrence_blocks_only_dated_candidate(self):
        self.coverage()
        self.coverage(offering=self.combined_offering, faculty=self.replacement)
        prepare_daily_encoding(actor=self.actor, offerings=[self.combined_offering], academic_year=self.academic_year,
            term=self.term, meeting_date=date(2026, 1, 5))
        self.confirm(fixtures.TeachingMeeting.objects.get())
        self.assertIn("UNMATERIALIZED_OCCURRENCE", {b.code for b in self.slice().blockers})
        self.assertTrue(self.slice(self.replacement).ready)
        self.publish(self.replacement)

    def test_missing_attribution_blocks_all_slices_without_omitting_occurrence(self):
        self.coverage()
        prepare_daily_encoding(actor=self.actor, offerings=[self.offering], academic_year=self.academic_year,
            term=self.term, meeting_date=date(2026, 1, 5))
        self.confirm(fixtures.TeachingMeeting.objects.get())
        review = self.review()
        self.assertTrue(review.unattributed)
        self.assertFalse(self.slice().ready)
        self.assertTrue(any(b.details["scope_unproven"] for b in self.slice().blockers))
        self.assertEqual(review.unattributed[0].details["sections"][0]["section_code"], "S2")
        with self.assertRaises(ValidationError):
            self.publish()

    def test_conflicting_combined_coverage_blocks_every_dated_candidate(self):
        version = self.schedule()
        self.coverage()
        self.coverage(offering=self.combined_offering, faculty=self.replacement)
        MeetingService.generate(actor=self.actor, schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5), offerings=[self.combined_offering])
        review = self.review()
        self.assertTrue(review.unattributed)
        self.assertFalse(self.slice().ready)
        self.assertFalse(self.slice(self.replacement).ready)
        self.assertEqual(set(review.unattributed[0].details["candidate_ids"]), {self.faculty.pk, self.replacement.pk})

    def test_combined_sections_publish_and_count_one_class(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        self.confirm(meeting)
        publication = self.publish()
        self.assertEqual(publication.entries.count(), 1)
        self.assertEqual(len(publication.entries.get().meeting_snapshot["sections"]), 2)
        self.assertEqual(self.final(publication).snapshot["class_count"], 1)

    def test_later_other_faculty_publication_preserves_earlier_final(self):
        first, second = self.pair()
        publication = self.publish()
        final = self.final(publication)
        saved = deepcopy(final.snapshot)
        self.confirm(second)
        later = self.publish(self.replacement, key="second-faculty")
        self.assertNotEqual(later.pk, publication.pk)
        final.refresh_from_db()
        self.assertEqual(final.snapshot, saved)
        self.assertTrue(preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty).ready)
        self.assertEqual(self.final(publication).pk, final.pk)

    def test_correction_republish_and_refinalize_preserves_every_prior_snapshot(self):
        first, _ = self.pair()
        original_meeting = deepcopy(_meeting_snapshot(first))
        first_publication = self.publish()
        first_final = self.final(first_publication)
        saved = deepcopy(first_final.snapshot)
        result = AttendanceResult.objects.get(meeting=first)
        AttendanceResultService.correct_early_dismissal(actor=self.actor, result=result,
            expected_revision=result.revision, minutes=15, reason="Synthetic authorized correction")
        with self.assertRaises(ValidationError):
            self.final(first_publication)
        revised = self.publish(key="corrected-faculty")
        final = self.final(revised)
        self.assertEqual((revised.version, revised.supersedes_id), (2, first_publication.pk))
        self.assertEqual((final.revision, final.supersedes_id), (2, first_final.pk))
        self.assertEqual(final.snapshot["net_payable_hours"], "0.75")
        first_final.refresh_from_db()
        self.assertEqual(first_final.snapshot, saved)
        first.refresh_from_db()
        self.assertEqual(_meeting_snapshot(first), original_meeting)
        self.assertEqual(first_publication.entries.get().result_revision_number, 1)

    def test_new_unmaterialized_own_occurrence_blocks_finalization(self):
        first, _ = self.pair()
        publication = self.publish()
        self.offering.schedule_text = "M 08:00-09:00/M 10:00-11:00"
        self.offering.save(update_fields=["schedule_text"])
        with self.assertRaises(ValidationError):
            self.final(publication)
        self.assertFalse(FacultyDTR.objects.exists())

    def test_review_attestation_required_and_publication_does_not_auto_finalize(self):
        self.pair()
        publication = self.publish()
        self.assertFalse(FacultyDTR.objects.exists())
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        with self.assertRaises(ValidationError):
            finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                expected_fingerprint=preview.fingerprint, reason="", faculty_review_complete=False)

    def test_identical_publication_retries_resume_without_duplicate_audit_or_versions(self):
        self.pair()
        publication = self.publish()
        count = fixtures.AuditLog.objects.count()
        self.assertEqual(self.publish().pk, publication.pk)
        self.assertEqual(self.publish(key="resumed-request").pk, publication.pk)
        self.assertEqual(AttendanceCutoffPublication.objects.count(), 1)
        self.assertEqual(fixtures.AuditLog.objects.count(), count)

    def test_stale_selection_rejected_and_mixed_selection_rolls_back(self):
        first, _ = self.pair()
        stale = self.slice().fingerprint
        result = AttendanceResult.objects.get(meeting=first)
        AttendanceResultService.correct_early_dismissal(actor=self.actor, result=result,
            expected_revision=1, minutes=10, reason="Correction after review")
        with self.assertRaises(ValidationError):
            self.publish(fingerprint=stale)
        rows = self.review().slices
        with self.assertRaises(ValidationError):
            publish_faculty_cutoffs(actor=self.actor, **self.scope(), faculty_ids=[self.faculty.pk, self.replacement.pk],
                expected_fingerprints={str(r.faculty.pk): r.fingerprint for r in rows}, submission_key="mixed-request")
        self.assertFalse(AttendanceCutoffPublication.objects.exists())

    def test_foreign_faculty_cannot_preview_finalize_or_adjust_owner_publication(self):
        self.pair()
        publication = self.publish()
        for operation in (preview_dtr,):
            with self.assertRaises(PermissionDenied):
                operation(actor=self.actor, publication=publication, faculty=self.replacement)
        with self.assertRaises(PermissionDenied):
            save_adjustment(actor=self.actor, publication=publication, faculty=self.replacement,
                department=self.department, entry_date=publication.start_date, kind="OTHER", hours=0, reason="")

    def test_explicit_publication_permission_and_direct_deny_get_post_and_service(self):
        self.pair()
        self.client.force_login(self.actor)
        RolePermission.objects.filter(role=self.role, permission__code=PUBLISH_FACULTY_PERMISSION).delete()
        self.assertEqual(self.client.get(reverse("faculty_attendance:faculty_cutoff_review"), self.query()).status_code, 403)
        self.assertEqual(self.client.post(reverse("faculty_attendance:faculty_cutoff_review"), self.query()).status_code, 403)
        RolePermission.objects.create(role=self.role, permission=Permission.objects.get(code=PUBLISH_FACULTY_PERMISSION))
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=PUBLISH_FACULTY_PERMISSION),
            grant_type="DENY", tenant=None, campus=None)
        with self.assertRaises(PermissionDenied):
            self.review()
        self.assertEqual(self.client.get(reverse("faculty_attendance:faculty_cutoff_review"), self.query()).status_code, 403)

    def test_csrf_and_normal_post_publication_preserve_review_opportunity(self):
        self.pair()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        url = reverse("faculty_attendance:faculty_cutoff_review")
        response = client.get(url, self.query())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending with blockers")
        self.assertContains(response, "S2")
        form = response.context["publication_form"]
        values = {**self.query(), "action": "publish_selected", "faculty_ids": [self.faculty.pk],
            "submission_key": form.initial["submission_key"],
            "expected_fingerprints": __import__("json").dumps(form.initial["expected_fingerprints"])}
        self.assertEqual(client.post(url, values).status_code, 403)
        values["csrfmiddlewaretoken"] = client.cookies["csrftoken"].value
        self.assertEqual(client.post(url, values).status_code, 302)
        self.assertEqual(AttendanceCutoffPublication.objects.count(), 1)
        self.assertFalse(FacultyDTR.objects.exists())

    def test_bulk_ready_publish_then_separate_ready_finalization_and_retry(self):
        self.pair(confirm_second=True)
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:faculty_cutoff_review")
        response = self.client.get(url, self.query())
        form = response.context["publication_form"]
        values = {**self.query(), "action": "publish_ready", "submission_key": form.initial["submission_key"],
            "expected_fingerprints": __import__("json").dumps(form.initial["expected_fingerprints"])}
        self.assertEqual(self.client.post(url, values).status_code, 302)
        self.assertEqual(AttendanceCutoffPublication.objects.count(), 2)
        self.assertFalse(FacultyDTR.objects.exists())
        response = self.client.get(url, self.query())
        final_form = response.context["final_form"]
        final_values = {**self.query(), "action": "finalize_ready", "submission_key": final_form.initial["submission_key"],
            "expected_fingerprints": __import__("json").dumps(final_form.initial["expected_fingerprints"])}
        self.assertEqual(self.client.post(url, final_values).status_code, 200)
        self.assertFalse(FacultyDTR.objects.exists())
        final_values["faculty_review_complete"] = "on"
        self.assertEqual(self.client.post(url, final_values).status_code, 302)
        self.assertEqual(FacultyDTR.objects.count(), 2)
        finals = finalize_ready_faculty(actor=self.actor, review_scope=self.scope(),
            faculty_ids=[self.faculty.pk, self.replacement.pk],
            expected_fingerprints=final_form.initial["expected_fingerprints"], faculty_review_complete=True)
        self.assertEqual(len(finals), 2)
        self.assertEqual(FacultyDTR.objects.count(), 2)

    def test_dtr_direct_deny_rejects_finalization_without_mutation(self):
        self.pair()
        publication = self.publish()
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=DTR_FINALIZE_PERMISSION),
            grant_type="DENY", tenant=self.tenant, campus=self.campus)
        with self.assertRaises(PermissionDenied):
            self.final(publication)
        self.assertFalse(FacultyDTR.objects.exists())

    def test_department_and_tenant_scope_fail_closed(self):
        self.pair()
        self.combined_offering.department = self.other_department
        self.combined_offering.save(update_fields=["department"])
        self.assertNotIn(self.replacement.pk, {r.faculty.pk for r in self.review().slices})
        with self.assertRaises(PermissionDenied):
            publish_faculty_cutoffs(actor=self.actor, **self.scope(), faculty_ids=[self.replacement.pk],
                expected_fingerprints={str(self.replacement.pk): "x" * 64}, submission_key="out-of-department")
        wrong = self.scope()
        wrong["tenant_id"] = self.tenant.pk + 10000
        with self.assertRaises(PermissionDenied):
            review_faculty_cutoff(actor=self.actor, **wrong)

    def test_campus_publication_and_existing_dtr_stay_usable(self):
        first, publication = self._published_dtr_cutoff()
        self.assertIsNone(publication.faculty_scope_id)
        self.assertEqual(publication.scope_key, "CAMPUS")
        final = self.final(publication)
        self.assertEqual(final.snapshot["class_count"], 1)

    def test_ac_only_and_genuinely_empty_scope(self):
        fixtures.FacultyAssignment.objects.create(offering=self.combined_offering, faculty_user=self.replacement, is_primary=True)
        role = Role.objects.create(code="AC", name="Synthetic AC")
        UserRole.objects.create(user=self.faculty, role=role, tenant=self.tenant, campus=self.campus, department=self.department)
        empty_scope = {**self.scope(), "start_date": date(2026, 1, 6), "end_date": date(2026, 1, 6)}
        with patch.object(self, "scope", return_value=empty_scope):
            self.assertFalse(self.slice(self.replacement).requires_dtr)
            publication = self.publish()
            self.assertEqual(publication.entries.count(), 0)
            self.assertFalse(preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty).ready)
            save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty, department=self.department,
                entry_date=publication.start_date, kind="ADMIN", hours=2, reason="Synthetic dated office hours")
            self.assertEqual(self.final(publication).snapshot["net_payable_hours"], "2.00")

    def test_closure_pay_basis_and_leave_adjustments_are_preserved(self):
        first, second = self.pair()
        save_closure(actor=self.actor, meeting=second, status="CLOSED", kind="HOLIDAY", pay_basis="REGULAR", reason="", expected_revision=0)
        publication = self.publish(self.replacement)
        self.assertEqual(self.final(publication, self.replacement).snapshot["net_payable_hours"], "1.00")
        first_pub = self.publish(key="first-faculty")
        result = AttendanceResult.objects.get(meeting=first)
        AttendanceResultService.correct_early_dismissal(actor=self.actor, result=result, expected_revision=1, minutes=30, reason="")
        corrected = self.publish(key="leave-publication")
        save_adjustment(actor=self.actor, publication=corrected, faculty=self.faculty, department=self.department,
            entry_date=corrected.start_date, kind="LEAVE", leave_type="SL", offset_kind="E", hours="0.50", reason="")
        self.assertEqual(self.final(corrected).snapshot["net_payable_hours"], "1.00")

    def test_faculty_visibility_only_owns_published_records_and_scopes(self):
        self.pair(confirm_second=True)
        first = self.publish()
        second = self.publish(self.replacement, key="other")
        UserPermission.objects.create(user=self.faculty, permission=Permission.objects.get(code=VIEW_PERMISSION),
            grant_type="ALLOW", tenant=self.tenant, campus=self.campus)
        fixtures.SystemSetting.objects.create(tenant=self.tenant,
            setting_key=fixtures.FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            setting_value="true", value_type="BOOL")
        entries, publications, _ = faculty_published_entries(faculty_user=self.faculty, tenant_id=self.tenant.pk,
            campus_id=self.campus.pk, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        self.assertEqual([p.pk for p in publications], [first.pk])
        self.assertEqual({e.faculty_user_id for e in entries}, {self.faculty.pk})

    def test_source_scan_is_shared_across_faculty_previews(self):
        self.pair(confirm_second=True)
        self.publish()
        self.publish(self.replacement, key="other")
        from . import faculty_cutoffs
        with patch.object(faculty_cutoffs, "_review_cutoff", wraps=faculty_cutoffs._review_cutoff) as scan:
            review = self.review()
            rows = processing_rows(actor=self.actor, review=review)
        self.assertEqual(len(rows), 2)
        self.assertEqual(scan.call_count, 1)

    def test_failed_bulk_publication_audit_rolls_back_every_slice(self):
        self.pair(confirm_second=True)
        review = self.review()
        from . import faculty_cutoffs
        with patch.object(faculty_cutoffs.AuditService, "log_event", side_effect=RuntimeError("synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                publish_faculty_cutoffs(actor=self.actor, **self.scope(), faculty_ids=[self.faculty.pk, self.replacement.pk],
                    expected_fingerprints={str(r.faculty.pk): r.fingerprint for r in review.slices}, submission_key="rollback")
        self.assertFalse(AttendanceCutoffPublication.objects.exists())

    def test_publication_retry_rejects_changed_note_selection_or_actor(self):
        self.pair(confirm_second=True)
        self.publish()
        row = self.slice()
        with self.assertRaises(ValidationError):
            publish_faculty_cutoffs(actor=self.actor, **self.scope(), faculty_ids=[self.faculty.pk],
                expected_fingerprints={str(self.faculty.pk): row.fingerprint}, submission_key="faculty-publication",
                publication_reason="Changed retry note")
        with self.assertRaises(ValidationError):
            self.publish(self.replacement, key="faculty-publication")
        self.assertEqual(AttendanceCutoffPublication.objects.count(), 1)

    def test_publication_finalization_and_adjustments_take_campus_mutex_first(self):
        self.pair()
        from . import faculty_cutoffs, notice_locking
        events = []
        original_lock, original_review = faculty_cutoffs.lock_notice_campus, faculty_cutoffs.review_faculty_cutoff
        def lock(campus_id):
            events.append("mutex")
            return original_lock(campus_id)
        def review(**kwargs):
            events.append("source")
            return original_review(**kwargs)
        with patch.object(faculty_cutoffs, "lock_notice_campus", side_effect=lock), patch.object(faculty_cutoffs, "review_faculty_cutoff", side_effect=review):
            self.publish()
        self.assertEqual(events[-2:], ["mutex", "source"])
        publication = AttendanceCutoffPublication.objects.get()
        with patch.object(notice_locking, "lock_notice_campus", wraps=notice_locking.lock_notice_campus) as mutex:
            self.final(publication)
        self.assertEqual(mutex.call_count, 1)

    def test_faculty_publication_membership_and_final_snapshots_are_immutable(self):
        self.pair()
        publication = self.publish()
        final = self.final(publication)
        with self.assertRaises(ValidationError):
            publication.save()
        with self.assertRaises(ValidationError):
            publication.entries.get().save()
        with self.assertRaises(ValidationError):
            final.save()

    def test_attribution_correction_can_revise_prior_teaching_to_explicit_empty_slice(self):
        first, second = self.pair()
        original = self.publish()
        old_final = self.final(original)
        SubstitutionService.assign(actor=self.actor, meeting=first, substitute_faculty=self.replacement, reason="Synthetic dated correction")
        review = self.review()
        self.assertFalse(next(r for r in review.slices if r.faculty.pk == self.faculty.pk).ready)
        checking_round = CheckingRoundService.create(actor=self.actor, meetings=[first], checking_date=first.meeting_date)
        observation = ObservationService.record(actor=self.actor, checking_round=checking_round, meeting_id=first.pk,
            manifest_revision=checking_round.manifest_revision, submission_key="attribution-correction",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 1}])
        AttendanceResultService.select_observation(actor=self.actor, observation=observation, expected_revision=1, reason="Synthetic attribution correction")
        revised = self.publish(key="empty-correction")
        self.assertEqual(revised.entries.count(), 0)
        final = self.final(revised)
        self.assertEqual(final.snapshot["net_payable_hours"], "0.00")
        self.assertEqual(final.supersedes_id, old_final.pk)
        self.assertEqual(original.entries.get().faculty_user_id, self.faculty.pk)

    def test_seed_has_no_broad_grants_and_navigation_requires_new_permission(self):
        from apps.navigation.models import MenuItemPermission
        self.assertEqual(set(RolePermission.objects.filter(permission__code=PUBLISH_FACULTY_PERMISSION).values_list("role_id", flat=True)), {self.role.pk})
        self.assertTrue(MenuItemPermission.objects.filter(menu_item__code="ATTENDANCE_FACULTY_CUTOFF", permission__code=PUBLISH_FACULTY_PERMISSION).exists())

    def test_empty_faculty_revision_replaces_legacy_campus_membership_without_removing_history(self):
        first, _ = self.pair(confirm_second=True)
        campus_review = review_cutoff(actor=self.actor, **self.scope())
        original = publish_cutoff(actor=self.actor, **self.scope(),
            expected_fingerprint=campus_review.fingerprint, submission_key="legacy-campus",
            publication_reason="Synthetic complete campus publication")
        SubstitutionService.assign(actor=self.actor, meeting=first, substitute_faculty=self.replacement,
            reason="Synthetic dated correction")
        checking_round = CheckingRoundService.create(actor=self.actor, meetings=[first], checking_date=first.meeting_date)
        observation = ObservationService.record(actor=self.actor, checking_round=checking_round, meeting_id=first.pk,
            manifest_revision=checking_round.manifest_revision, submission_key="empty-visibility",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 1}])
        AttendanceResultService.select_observation(actor=self.actor, observation=observation,
            expected_revision=1, reason="Synthetic attribution correction")
        revised = self.publish(key="empty-faculty")
        self.assertEqual(revised.entries.count(), 0)
        UserPermission.objects.create(user=self.faculty, permission=Permission.objects.get(code=VIEW_PERMISSION),
            grant_type="ALLOW", tenant=self.tenant, campus=self.campus)
        fixtures.SystemSetting.objects.create(tenant=self.tenant,
            setting_key=fixtures.FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            setting_value="true", value_type="BOOL")
        entries, _, history = faculty_published_entries(faculty_user=self.faculty, tenant_id=self.tenant.pk,
            campus_id=self.campus.pk, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5), include_history=True)
        self.assertEqual(entries, [])
        self.assertEqual([entry.publication_id for entry in history], [original.pk])
        self.assertEqual(original.entries.filter(faculty_user=self.faculty).count(), 1)

    def test_selected_department_scope_cannot_omit_part_of_a_faculty(self):
        self.pair()
        with self.assertRaises(PermissionDenied):
            review_faculty_cutoff(actor=self.actor, **self.scope(), department_ids=[self.other_department.pk])

    def empty_correction(self):
        first, _ = self.pair()
        original = self.publish()
        old_final = self.final(original)
        SubstitutionService.assign(actor=self.actor, meeting=first, substitute_faculty=self.replacement, reason="Synthetic reattribution")
        round_ = CheckingRoundService.create(actor=self.actor, meetings=[first], checking_date=first.meeting_date)
        observation = ObservationService.record(actor=self.actor, checking_round=round_, meeting_id=first.pk,
            manifest_revision=round_.manifest_revision, submission_key="empty-print-correction",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 1}])
        AttendanceResultService.select_observation(actor=self.actor, observation=observation,
            expected_revision=1, reason="Synthetic reattribution")
        return self.publish(key="empty-print-publication"), old_final

    def test_empty_correction_review_finalize_and_print_use_saved_departments(self):
        publication, old = self.empty_correction()
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:dtr_review")
        values = {"publication": publication.pk, "faculty": self.faculty.pk}
        response = self.client.get(url, values)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_finalize"])
        self.assertTrue(response.context["can_summary_print"])
        result = self.client.post(url, {**values, "action": "finalize", "faculty_review_complete": "on",
            "expected_fingerprint": response.context["preview"].fingerprint, "reason": ""})
        self.assertEqual(result.status_code, 302)
        final = FacultyDTR.objects.get(publication=publication)
        self.assertEqual((final.revision, final.supersedes_id), (2, old.pk))
        self.assertEqual(final.snapshot["lines"], [])
        self.assertEqual(final.snapshot["net_payable_hours"], "0.00")
        self.assertEqual(final_dtr_departments(final), {self.department.pk})
        self.assertTrue(self.client.get(url, values).context["can_print"])
        self.assertEqual(self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id])).status_code, 200)
        summary = self.client.get(reverse("faculty_attendance:dtr_summary"), {"publication": publication.pk})
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.context["rows"][0]["final"].pk, final.pk)
        self.assertContains(summary, "0.00")

    def test_empty_correction_wrong_department_and_direct_denies_reject_get_post_print(self):
        publication, _ = self.empty_correction()
        final = self.final(publication)
        self.client.force_login(self.actor)
        codes = (DTR_VIEW_PERMISSION, DTR_FINALIZE_PERMISSION, DTR_PRINT_PERMISSION)
        RolePermission.objects.filter(role=self.role, permission__code__in=codes).delete()
        scoped_role = Role.objects.create(code="SCOPED_DTR", name="Scoped DTR checker")
        for code in codes:
            RolePermission.objects.create(role=scoped_role, permission=Permission.objects.get(code=code))
        UserRole.objects.create(user=self.actor, role=scoped_role,
            tenant=self.tenant, campus=self.campus, department=self.other_department)
        review = reverse("faculty_attendance:dtr_review")
        values = {"publication": publication.pk, "faculty": self.faculty.pk}
        self.assertEqual(self.client.get(review, values).status_code, 403)
        self.assertEqual(self.client.post(review, {**values, "action": "finalize"}).status_code, 403)
        print_url = reverse("faculty_attendance:dtr_print", args=[final.public_id])
        summary_url = reverse("faculty_attendance:dtr_summary")
        self.assertEqual(self.client.get(print_url).status_code, 403)
        self.assertEqual(self.client.get(summary_url, {"publication": publication.pk}).status_code, 403)
        UserRole.objects.create(user=self.actor, role=scoped_role,
            tenant=self.tenant, campus=self.campus, department=self.department)
        self.assertEqual(self.client.get(print_url).status_code, 200)
        for code in codes:
            with self.subTest(code=code):
                deny = UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=code),
                    grant_type="DENY", tenant=None, campus=None)
                if code == DTR_PRINT_PERMISSION:
                    self.assertEqual(self.client.get(print_url).status_code, 403)
                    self.assertEqual(self.client.get(summary_url, {"publication": publication.pk}).status_code, 403)
                elif code == DTR_VIEW_PERMISSION:
                    self.assertEqual(self.client.get(review, values).status_code, 403)
                else:
                    preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
                    self.assertEqual(self.client.post(review, {**values, "action": "finalize", "faculty_review_complete": "on",
                        "expected_fingerprint": preview.fingerprint}).status_code, 403)
                deny.delete()
        self.assertEqual(FacultyDTR.objects.filter(publication=publication).count(), 1)

    def test_empty_correction_rejects_missing_or_foreign_saved_provenance(self):
        publication, _ = self.empty_correction()
        final = self.final(publication)
        foreign_tenant = fixtures.Tenant.objects.create(code="FOREIGN", name="Foreign")
        foreign_campus = fixtures.Campus.objects.create(tenant=foreign_tenant, code="FOREIGN", name="Foreign")
        other_campus = fixtures.Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other")
        foreign_department = fixtures.Department.objects.create(tenant=foreign_tenant, campus=foreign_campus, code="F", name="Foreign")
        other_department = fixtures.Department.objects.create(tenant=self.tenant, campus=other_campus, code="O", name="Other")
        for departments in ([], [foreign_department.pk], [other_department.pk]):
            with self.subTest(departments=departments):
                forged = deepcopy(final)
                forged.publication.scope_snapshot["departments"] = departments
                with self.assertRaises(PermissionDenied):
                    final_dtr_departments(forged)
        for key, value in (("faculty_user_id", self.replacement.pk), ("tenant_id", foreign_tenant.pk), ("campus_id", other_campus.pk)):
            forged = deepcopy(final)
            forged.snapshot[key] = value
            with self.assertRaises(PermissionDenied):
                final_dtr_departments(forged)

    def test_locked_finalization_handoff_ignores_stale_ordinary_adjustment_and_mixed_loaders(self):
        self.pair()
        publication = self.publish()
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty, department=self.department,
            entry_date=publication.start_date, kind="OTHER", hours="0.25", reason="Synthetic current deduction")
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        from . import dtr
        loader = dtr.current_adjustments
        def stale_loader(**kwargs):
            return loader(**kwargs) if kwargs.get("rows") is not None else []
        with patch.object(dtr, "current_adjustments", side_effect=stale_loader), patch.object(
            dtr, "current_mixed_decisions", side_effect=AssertionError("Finalization must use locked mixed evidence")), patch.object(
            dtr, "latest_dtr", wraps=dtr.latest_dtr) as latest, patch.object(dtr, "latest_publication", wraps=dtr.latest_publication) as publications:
            final = finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                expected_fingerprint=preview.fingerprint, reason="", faculty_review_complete=True)
        self.assertTrue(latest.call_args.kwargs["lock"])
        self.assertTrue(publications.call_args.kwargs["lock"])
        self.assertEqual(final.snapshot["gross_deductions"], "0.25")
        self.assertEqual(final.snapshot["net_payable_hours"], "0.75")

    def test_empty_correction_print_endpoints_cannot_cross_request_tenant_or_campus(self):
        from .dtr_views import dtr_print_view, dtr_cutoff_summary_view
        publication, _ = self.empty_correction()
        final = self.final(publication)
        tenant = fixtures.Tenant.objects.create(code="WRONG", name="Wrong tenant")
        foreign = fixtures.Campus.objects.create(tenant=tenant, code="W", name="Wrong campus")
        other = fixtures.Campus.objects.create(tenant=self.tenant, code="O", name="Other campus")
        for tenant_id, campus_id in ((tenant.pk, foreign.pk), (self.tenant.pk, other.pk)):
            with self.subTest(tenant=tenant_id, campus=campus_id):
                request = RequestFactory().get("/", {"publication": publication.pk})
                request.user = self.actor
                request.scope = {"tenant_id": tenant_id, "campus_id": campus_id}
                # Exercise the endpoint's scoped lookup, independently of portal login.
                with self.assertRaises(Http404):
                    dtr_print_view.__wrapped__(request, final.public_id)
                with self.assertRaises(Http404):
                    dtr_cutoff_summary_view.__wrapped__(request)

    def test_locked_mixed_decision_is_used_without_ordinary_reload(self):
        meeting, publication = self._published_dtr_cutoff([
            {"finding_type": "ABSENCE", "segment_key": "partial", "notice_status": "A", "missed_hours": "0.50"},
            {"finding_type": "LATE", "segment_key": "arrival", "minutes": 10}])
        save_mixed_decision(actor=self.actor, publication=publication, faculty=self.faculty, meeting=meeting,
            intervals_text="A 08:00-08:30", reason="Synthetic verified intervals", expected_revision=0)
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        with patch("apps.faculty_attendance.dtr.current_mixed_decisions", side_effect=AssertionError("Stale ordinary loader")):
            final = finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                expected_fingerprint=preview.fingerprint, reason="", faculty_review_complete=True)
        self.assertEqual(final.snapshot["gross_deductions"], "0.50")
        self.assertEqual(final.snapshot["lines"][0]["mixed_decision_revision"], 1)

    def test_source_locking_reaches_expected_occurrences_and_recurring_projection(self):
        from . import cutoffs, daily_encoding
        self.pair()
        with patch.object(cutoffs, "expected_daily_occurrences", wraps=cutoffs.expected_daily_occurrences) as expected, patch.object(
            cutoffs, "_combined_groups", wraps=daily_encoding._combined_groups) as groups:
            review_faculty_cutoff(actor=self.actor, **self.scope(), lock=True)
        self.assertTrue(expected.call_args.kwargs["lock"])
        self.assertTrue(groups.call_args.kwargs["lock"])


@skipUnless(connection.vendor == "mysql" and os.environ.get("TMP_ATTENDANCE_INNODB_TESTS") == "1",
            "Requires an explicitly configured disposable local InnoDB test runtime; SQLite cannot prove row locks.")
class FacultyCutoffInnoDBTests(TransactionTestCase):
    permission_codes = FacultyCutoffTests.permission_codes
    aware = FacultyCutoffTests.aware
    coverage = FacultyCutoffTests.coverage
    confirm = FacultyCutoffTests.confirm
    scope = FacultyCutoffTests.scope
    review = FacultyCutoffTests.review
    slice = FacultyCutoffTests.slice
    pair = FacultyCutoffTests.pair
    publish = FacultyCutoffTests.publish
    final = FacultyCutoffTests.final
    _published_dtr_cutoff = FacultyCutoffTests._published_dtr_cutoff
    schedule = FacultyCutoffTests.schedule
    meeting = FacultyCutoffTests.meeting

    def setUp(self):
        self.assertTrue(str(connection.settings_dict["NAME"]).startswith("test_"))
        self.assertIn(connection.settings_dict.get("HOST", ""), ("", "localhost", "127.0.0.1"))
        with connection.cursor() as cursor:
            cursor.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (%s,%s)",
                ["faculty_attendance_notice_mutexes", "faculty_attendance_cutoff_publications"])
            self.assertEqual([row[0].upper() for row in cursor.fetchall()], ["INNODB", "INNODB"])
        for code in self.permission_codes:
            Permission.objects.get_or_create(code=code, defaults={"module": "faculty_attendance", "action": code.split(".")[-1]})
        fixtures.FacultyAttendanceFoundationTests.setUpTestData.__func__(type(self))
        fixtures.FacultyAttendanceFoundationTests.setUp(self)

    def workers(self, operations):
        barrier = Barrier(len(operations))
        def run(operation):
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                barrier.wait(timeout=20)
                return operation()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            futures = [pool.submit(run, operation) for operation in operations]
            return [future.result(timeout=45) for future in futures]

    def test_concurrent_identical_requests_create_one_faculty_publication(self):
        self.pair()
        ids = self.workers([lambda: self.publish().pk, lambda: self.publish().pk])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(AttendanceCutoffPublication.objects.count(), 1)

    def test_other_faculty_publication_and_identical_final_retry_preserve_earlier_final(self):
        self.pair(confirm_second=True)
        publication = self.publish()
        final = self.final(publication)
        saved = deepcopy(final.snapshot)
        self.workers([lambda: self.publish(self.replacement, key="other-faculty").pk, lambda: self.final(publication).pk])
        final.refresh_from_db()
        self.assertEqual(final.snapshot, saved)
        self.assertEqual(FacultyDTR.objects.count(), 1)

    def waiting_final(self, publication, preview, change):
        """Establish RR snapshot, then block behind a real held campus mutex."""
        from . import notice_locking
        entered = Event()
        main_thread = get_ident()
        original = notice_locking.lock_notice_campus
        def acquire(campus_id):
            if get_ident() != main_thread:
                # Deliberately preserve an old consistent snapshot before waiting.
                AttendanceResult.objects.count()
                entered.set()
            return original(campus_id)
        def waiting():
            connections.close_all()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                return finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                    expected_fingerprint=preview.fingerprint, reason="Synthetic reviewed final", faculty_review_complete=True)
            finally:
                connections.close_all()
        with patch.object(notice_locking, "lock_notice_campus", side_effect=acquire), ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                original(publication.campus_id)
                future = pool.submit(waiting)
                self.assertTrue(entered.wait(timeout=15), "Finalizer did not establish its RR snapshot")
                changed = change()
            # Commit releases mutex; finalizer must now read current evidence.
            return future.result(timeout=45), changed

    def test_waiting_final_rejects_adjustment_committed_after_rr_snapshot(self):
        self.pair()
        publication = self.publish()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        with self.assertRaisesMessage(ValidationError, "inputs changed"):
            self.waiting_final(publication, preview, lambda: save_adjustment(actor=self.actor,
                publication=publication, faculty=self.faculty, department=self.department,
                entry_date=publication.start_date, kind="OTHER", hours="0.25", reason="Synthetic concurrent deduction"))
        self.assertFalse(FacultyDTR.objects.exists())
        self.assertEqual(self.final(publication).snapshot["gross_deductions"], "0.25")

    def test_waiting_final_rejects_new_mixed_decision_revision(self):
        meeting, publication = self._published_dtr_cutoff([
            {"finding_type": "ABSENCE", "segment_key": "partial", "notice_status": "A", "missed_hours": "0.50"},
            {"finding_type": "LATE", "segment_key": "arrival", "minutes": 10}])
        save_mixed_decision(actor=self.actor, publication=publication, faculty=self.faculty, meeting=meeting,
            intervals_text="A 08:00-08:30", reason="Synthetic intervals", expected_revision=0)
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        with self.assertRaisesMessage(ValidationError, "inputs changed"):
            self.waiting_final(publication, preview, lambda: save_mixed_decision(actor=self.actor,
                publication=publication, faculty=self.faculty, meeting=meeting,
                intervals_text="A 08:00-08:20", reason="Synthetic revised actual interval", expected_revision=1))
        self.assertFalse(FacultyDTR.objects.exists())
        self.assertEqual(self.final(publication).snapshot["gross_deductions"], "0.33")

    def test_waiting_final_rejects_new_publication_and_result_revision(self):
        first, _ = self.pair()
        publication = self.publish()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        def change():
            result = AttendanceResult.objects.get(meeting=first)
            AttendanceResultService.correct_early_dismissal(actor=self.actor, result=result,
                expected_revision=1, minutes=15, reason="Synthetic concurrent correction")
            return self.publish(key="concurrent-republication")
        with self.assertRaisesMessage(ValidationError, "newer cutoff publication"):
            self.waiting_final(publication, preview, change)
        self.assertFalse(FacultyDTR.objects.exists())

    def test_waiting_final_rejects_new_expected_source_slot(self):
        self.pair()
        publication = self.publish()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        def change():
            # Disposable source mutation isolates membership freshness, not sync UX.
            fixtures.CourseOffering.objects.filter(pk=self.offering.pk).update(schedule_text="M 08:00-09:00/M 10:00-11:00")
        with self.assertRaisesMessage(ValidationError, "cutoff is blocked"):
            self.waiting_final(publication, preview, change)
        self.assertFalse(FacultyDTR.objects.exists())

    def test_waiting_identical_finalizations_resume_current_revision_and_lineage(self):
        self.pair()
        publication = self.publish()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        resumed, first = self.waiting_final(publication, preview, lambda: self.final(publication))
        self.assertEqual((resumed.pk, resumed.revision, resumed.supersedes_id), (first.pk, 1, None))
        self.assertEqual(FacultyDTR.objects.count(), 1)
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty, department=self.department,
            entry_date=publication.start_date, kind="OTHER", hours="0.25", reason="Synthetic new revision")
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        resumed, second = self.waiting_final(publication, preview, lambda: self.final(publication))
        self.assertEqual((resumed.pk, resumed.revision, resumed.supersedes_id), (second.pk, 2, first.pk))
        self.assertEqual(FacultyDTR.objects.count(), 2)
