"""College source edits synchronize only unrecorded attendance, using explicit dates."""
from copy import deepcopy
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import FacultyAssignment, FacultyAssignmentReplacementLog
from apps.academics.services import FacultyAssignmentSafetyService, record_attendance_assignment_event
from apps.admin_portal.forms import FacultyAssignmentForm, CourseOfferingForm
from apps.rbac.models import Permission, UserPermission, Role, UserRole

from . import tests as existing
from .academic_integration import AcademicOfferingSourceIntegrationService
from .college_sync import retired_meeting_ids
from .cutoffs import review_cutoff, publish_cutoff
from .daily_encoding import expected_daily_occurrences, inspect_daily_occurrences, prepare_daily_encoding
from .models import (AttendanceResult, CoverageReconciliation, FacultyCoverage,
                     MeetingReconciliation, TeachingMeeting)
from .observations import AttendanceResultService, CheckingRoundService, resolve_attendance_faculty
from .services import MeetingService


class CollegeSynchronizationTests(TestCase):
    permission_codes = existing.FacultyAttendanceFoundationTests.permission_codes
    setUpTestData = classmethod(existing.FacultyAttendanceFoundationTests.setUpTestData.__func__)
    aware = existing.FacultyAttendanceFoundationTests.aware
    schedule = existing.FacultyAttendanceFoundationTests.schedule
    coverage = existing.FacultyAttendanceFoundationTests.coverage

    def setUp(self):
        existing.FacultyAttendanceFoundationTests.setUp(self)
        faculty_role, _ = Role.objects.get_or_create(code="FACULTY", defaults={"name": "Faculty"})
        for faculty in (self.faculty, self.replacement):
            UserRole.objects.get_or_create(user=faculty, role=faculty_role, tenant=self.tenant,
                                          campus=self.campus, department=self.department)
        for code in ("faculty_assignments.create", "faculty_assignments.update", "faculty_assignments.import",
                     "faculty_assignments.read", "faculty_replacement.process", "offerings.update", "offerings.view"):
            permission, _ = Permission.objects.get_or_create(code=code, defaults={"module": code.split('.')[0], "action": code.split('.')[1]})
            UserPermission.objects.create(user=self.actor, permission=permission, grant_type="ALLOW",
                tenant=self.tenant, campus=self.campus)
        self.assignment = FacultyAssignment.objects.create(offering=self.offering, faculty_user=self.faculty,
            tenant=self.tenant, campus=self.campus, is_primary=True)
        self.combined_offering.is_active = False
        self.combined_offering.schedule_text = "T 08:00-09:00"
        self.combined_offering.save()

    def sync(self, assignment=None, at=None, **kwargs):
        assignment = assignment or self.assignment
        return record_attendance_assignment_event(actor=self.actor, assignment=assignment,
            event_type=kwargs.pop("event_type", "ASSIGNMENT_CREATED"), reason="",
            effective_at=at or self.aware(2026, 1, 2), academic_permission=kwargs.pop("permission", "faculty_assignments.create"),
            **kwargs)

    def prepared(self, covered=False, combined=False):
        if combined:
            self.combined_offering.schedule_text = self.offering.schedule_text
            self.combined_offering.save()
        if covered:
            self.sync()
        version = self.schedule()
        meeting = MeetingService.generate(actor=self.actor, schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5), offerings=[self.combined_offering] if combined else [])
        round_ = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date,
            academic_year=self.academic_year, term=self.term, daily_occurrence_date=meeting.meeting_date)
        return meeting, round_

    def replace(self, assignment, faculty, day, type_=None):
        return FacultyAssignmentSafetyService.process_replacement(assignments=[assignment], replacement_faculty=faculty,
            replacement_type=type_ or FacultyAssignmentReplacementLog.ReplacementType.PERMANENT,
            reason_category="SCHEDULE_CONFLICT", remarks="Synthetic College replacement",
            processed_by_user=self.actor, attendance_effective_at=self.aware(2026, 1, day))[0].new_assignment

    def change(self, text="M 10:00-11:30", room="206", day=5):
        old_text, old_room = self.offering.schedule_text, self.offering.room
        self.offering.schedule_text, self.offering.room = text, room
        self.offering.save()
        return AcademicOfferingSourceIntegrationService.record_change(actor=self.actor, offering=self.offering,
            old_schedule_text=old_text, old_room=old_room, effective_from=date(2026, 1, day), synchronize=True)

    def test_prepared_unresolved_class_becomes_encodable_without_adoption_or_second_step(self):
        meeting, round_ = self.prepared()
        frozen = deepcopy(round_.manifest_rows.get().meeting_snapshot)
        self.sync()
        meeting.refresh_from_db()
        round_.refresh_from_db()
        self.assertFalse(meeting.unresolved_coverage)
        self.assertEqual(resolve_attendance_faculty(meeting)[0], self.faculty)
        self.assertEqual(round_.manifest_rows.get().meeting_snapshot, frozen)
        self.assertEqual(round_.manifest_revision, 2)
        self.assertFalse(CoverageReconciliation.objects.filter(status="PENDING").exists())
        AttendanceResultService.confirm_present(actor=self.actor, checking_round=round_, manifest_revision=2,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}])
        self.assertEqual(AttendanceResult.objects.get(meeting=meeting).faculty_user, self.faculty)

    def test_replacement_and_original_return_split_coverage_without_extension_backwards(self):
        self.sync()
        replacement = self.replace(self.assignment, self.replacement, 5, FacultyAssignmentReplacementLog.ReplacementType.TEMPORARY)
        self.assignment.refresh_from_db()
        self.assertFalse(self.assignment.is_active)
        returned = self.replace(replacement, self.faculty, 12)
        self.assertEqual(returned.pk, self.assignment.pk)
        rows = list(FacultyCoverage.objects.order_by("effective_from"))
        self.assertEqual([r.faculty_user_id for r in rows], [self.faculty.pk, self.replacement.pk, self.faculty.pk])
        self.assertEqual(rows[0].effective_from, self.aware(2026, 1, 2))
        self.assertEqual(rows[0].effective_until, rows[1].effective_from)
        self.assertEqual(rows[1].effective_until, rows[2].effective_from)

    def test_replacement_updates_prepared_class_and_repeated_event_is_idempotent(self):
        meeting, round_ = self.prepared(covered=True)
        new = self.replace(self.assignment, self.replacement, 5)
        meeting.refresh_from_db()
        self.assertEqual(resolve_attendance_faculty(meeting)[0], self.replacement)
        self.sync(new, at=self.aware(2026, 1, 5), source_reference="repeat", permission="faculty_replacement.process")
        count = FacultyCoverage.objects.count()
        self.sync(new, at=self.aware(2026, 1, 5), source_reference="repeat", permission="faculty_replacement.process")
        self.assertEqual(FacultyCoverage.objects.count(), count)
        self.assertFalse(MeetingReconciliation.objects.filter(status="PENDING").exists())

    def test_schedule_time_room_sync_preserves_frozen_manifest_and_reuses_meeting(self):
        meeting, round_ = self.prepared(covered=True)
        frozen = deepcopy(round_.manifest_rows.get().meeting_snapshot)
        self.change()
        meeting.refresh_from_db()
        self.assertEqual(meeting.scheduled_minutes, 90)
        self.assertEqual(meeting.location_snapshot["room"], "206")
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering], term=self.term,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        self.assertEqual(issues + inspect_daily_occurrences(occurrences), [])
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 5))
        self.assertEqual(issues, [])
        self.assertEqual(rounds[0].pk, round_.pk)
        self.assertEqual(TeachingMeeting.objects.count(), 1)
        self.assertEqual(round_.manifest_rows.get().meeting_snapshot, frozen)

    def test_weekday_change_retires_only_unencoded_occurrence_keeps_audit(self):
        meeting, round_ = self.prepared(covered=True)
        self.change(text="T 08:00-09:00")
        self.assertIn(meeting.pk, retired_meeting_ids())
        self.assertTrue(TeachingMeeting.objects.filter(pk=meeting.pk).exists())
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 6))
        self.assertEqual(issues, [])
        self.assertEqual(rounds[0].daily_occurrence_date, date(2026, 1, 6))
        self.assertEqual(round_.manifest_rows.count(), 1)

    def test_saved_and_published_findings_keep_faculty_schedule_and_history(self):
        meeting, round_ = self.prepared(covered=True)
        AttendanceResultService.confirm_present(actor=self.actor, checking_round=round_, manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}])
        reviewed = review_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=meeting.meeting_date, end_date=meeting.meeting_date)
        self.assertTrue(reviewed.ready, reviewed.blockers)
        publication = publish_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=meeting.meeting_date, end_date=meeting.meeting_date,
            expected_fingerprint=reviewed.fingerprint, submission_key="college-sync-publication", publication_reason="")
        original = deepcopy(meeting.schedule_snapshot)
        snapshots = list(publication.entries.values_list("meeting_snapshot", "findings_snapshot"))
        self.replace(self.assignment, self.replacement, 5)
        self.change()
        self.change(text="M 11:00-12:00", room="207")
        meeting.refresh_from_db()
        result = AttendanceResult.objects.get(meeting=meeting)
        self.assertEqual(result.faculty_user, self.faculty)
        self.assertEqual(result.revision, 1)
        self.assertEqual(result.history.count(), 1)
        self.assertEqual(meeting.schedule_snapshot, original)
        self.assertEqual(list(publication.entries.values_list("meeting_snapshot", "findings_snapshot")), snapshots)
        self.assertFalse(meeting.reconciliations.filter(status="PENDING").exists())
        reviewed = review_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=meeting.meeting_date, end_date=meeting.meeting_date)
        self.assertTrue(reviewed.ready, reviewed.blockers)

    def test_combined_prepared_class_remains_unresolved_until_assignments_agree(self):
        meeting, _ = self.prepared(combined=True)
        self.sync()
        meeting.refresh_from_db()
        self.assertTrue(meeting.unresolved_coverage)
        other = FacultyAssignment.objects.create(offering=self.combined_offering, faculty_user=self.faculty,
            tenant=self.tenant, campus=self.campus)
        self.sync(other)
        meeting.refresh_from_db()
        self.assertFalse(meeting.unresolved_coverage)
        self.assertEqual(meeting.offering_links.count(), 2)
        self.assertEqual(TeachingMeeting.objects.count(), 1)

    def test_boundary_missing_or_before_existing_activation_is_rejected(self):
        with self.assertRaises(ValidationError):
            record_attendance_assignment_event(actor=self.actor, assignment=self.assignment, event_type="ASSIGNMENT_CREATED",
                reason="", academic_permission="faculty_assignments.create")
        self.assertEqual(FacultyCoverage.objects.count(), 0)
        self.sync()
        with self.assertRaises(ValidationError):
            self.sync(at=self.aware(2026, 1, 1), source_reference="backdate")
        self.assertEqual(FacultyCoverage.objects.get().effective_from, self.aware(2026, 1, 2))

    def test_direct_deny_and_explicit_assignment_scope_conflict_fail_closed(self):
        denied = UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code="faculty_attendance.reconcile"),
            grant_type="DENY", tenant=self.tenant)
        with self.assertRaises(PermissionDenied):
            self.sync()
        self.assertEqual(FacultyCoverage.objects.count(), 0)
        denied.delete()
        self.assignment.campus = existing.Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other")
        self.assignment.save()
        with self.assertRaises(ValidationError):
            self.sync()
        self.assertEqual(FacultyCoverage.objects.count(), 0)

    def test_import_confirmation_creates_dated_coverage_without_setup_queue(self):
        from apps.imports.models import ImportBatch
        from apps.imports.services import BulkImportService
        self.assignment.delete()
        _, assignment = BulkImportService._create_from_row(ImportBatch.ImportType.FACULTY_ASSIGNMENTS,
            {"offering_id": self.offering.pk, "faculty_user_id": self.faculty.pk, "tenant_id": self.tenant.pk,
             "campus_id": self.campus.pk, "is_primary": True, "attendance_effective_at": "2026-01-02T00:00:00+08:00"}, actor=self.actor)
        self.assertEqual(FacultyCoverage.objects.get().source_assignment, assignment)
        self.assertFalse(CoverageReconciliation.objects.filter(status="PENDING").exists())

    def test_forms_require_original_operation_effective_boundary(self):
        form = FacultyAssignmentForm(data={"offering": self.offering.pk, "faculty_user": self.faculty.pk,
            "is_active": "on", "is_primary": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("attendance_effective_at", form.errors)

    def test_later_acceptance_keeps_explicit_source_boundary_without_reopening_queue(self):
        self.sync()
        boundary = FacultyCoverage.objects.get().effective_from
        record_attendance_assignment_event(actor=self.actor, assignment=self.assignment,
            event_type="ASSIGNMENT_ACCEPTED", source_reference="synthetic-later-acceptance", reason="")
        self.assertEqual(FacultyCoverage.objects.get().effective_from, boundary)
        self.assertFalse(CoverageReconciliation.objects.filter(status="PENDING").exists())

    def test_legacy_substitution_evidence_is_not_rewritten_by_academic_replacement(self):
        from .services import SubstitutionService
        meeting, _ = self.prepared(covered=True)
        substitution = SubstitutionService.assign(actor=self.actor, meeting=meeting,
            substitute_faculty=self.replacement, reason="Existing legacy dated decision")
        self.replace(self.assignment, self.replacement, 5)
        meeting.refresh_from_db()
        substitution.refresh_from_db()
        self.assertEqual(meeting.faculty_user, self.faculty)
        self.assertEqual(substitution.original_faculty, self.faculty)
        self.assertEqual(resolve_attendance_faculty(meeting)[0], self.replacement)
        self.assertFalse(meeting.reconciliations.filter(status="PENDING").exists())

    def test_explicit_wrong_assignment_tenant_cannot_bypass_enabled_source(self):
        self.assignment.tenant = existing.Tenant.objects.create(code="OTHER-TENANT", name="Other tenant")
        self.assignment.save()
        with self.assertRaises(ValidationError):
            self.sync()
        self.assertFalse(FacultyCoverage.objects.exists())

    def test_daily_unassigned_class_links_to_academic_source_not_coverage_wizard(self):
        self.prepared()
        self.client.force_login(self.actor)
        payload = {"academic_year": self.academic_year.pk, "term": self.term.pk, "meeting_date": "2026-01-05"}
        page = self.client.get(reverse("faculty_attendance:daily_encoding"), payload)
        self.assertContains(page, "Open Faculty Assignments")
        self.assertContains(page, "Effective from")
        self.assertNotContains(page, "Correct coverage")
        self.assertNotContains(page, "coverage-correction-title")
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code="faculty_assignments.read"),
            grant_type="DENY", tenant=self.tenant, campus=self.campus)
        page = self.client.get(reverse("faculty_attendance:daily_encoding"), payload)
        self.assertNotContains(page, "Open Faculty Assignments")

    def test_direct_edit_cannot_rewrite_assignment_referenced_by_dated_history(self):
        self.sync()
        with self.assertRaisesMessage(ValueError, "Use Replace Faculty"):
            FacultyAssignmentSafetyService.validate_direct_assignment_change(assignment=self.assignment,
                new_offering_id=self.offering.pk, new_faculty_user_id=self.replacement.pk)

    def test_assignment_edit_with_boundary_recovers_existing_prepared_class_in_same_save(self):
        meeting, round_ = self.prepared()
        self.client.force_login(self.actor)
        response = self.client.post(reverse("admin_portal:faculty_assignment_update", args=[self.assignment.pk]),
            {"offering": self.offering.pk, "faculty_user": self.faculty.pk, "is_active": "on", "is_primary": "on",
             "attendance_effective_at": "2026-01-02T00:00"})
        self.assertEqual(response.status_code, 302)
        meeting.refresh_from_db()
        self.assertEqual(resolve_attendance_faculty(meeting)[0], self.faculty)
        self.assertFalse(meeting.unresolved_coverage)

    def test_schedule_same_day_second_edit_appends_version_and_stays_encodable(self):
        meeting, _ = self.prepared(covered=True)
        self.change()
        self.change(text="M 11:00-12:00", room="207")
        meeting.refresh_from_db()
        self.assertEqual(meeting.scheduled_minutes, 60)
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering], term=self.term,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        self.assertEqual(issues + inspect_daily_occurrences(occurrences), [])

    def test_added_afternoon_slot_automatically_resumes_prepared_daily_list(self):
        meeting, round_ = self.prepared(covered=True)
        frozen = deepcopy(round_.manifest_rows.get().meeting_snapshot)
        self.change(text="M 08:00-09:00; M 13:00-14:00", room=self.offering.room)
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering],
            academic_year=self.academic_year, term=self.term, meeting_date=meeting.meeting_date)
        self.assertEqual(issues, [])
        self.assertEqual(rounds[0].pk, round_.pk)
        self.assertEqual(round_.manifest_rows.count(), 2)
        self.assertEqual(round_.manifest_rows.get(meeting=meeting).meeting_snapshot, frozen)
        self.assertEqual(TeachingMeeting.objects.count(), 2)
        prepare_daily_encoding(actor=self.actor, offerings=[self.offering], academic_year=self.academic_year,
            term=self.term, meeting_date=meeting.meeting_date)
        self.assertEqual(round_.manifest_rows.count(), 2)

    def test_linked_schedule_saves_wait_for_sources_then_sync_combined_once(self):
        from .observations import require_confirmable_meeting_faculty
        meeting, _ = self.prepared(covered=True, combined=True)
        other = FacultyAssignment.objects.create(offering=self.combined_offering, faculty_user=self.faculty,
            tenant=self.tenant, campus=self.campus)
        self.sync(other)
        self.change()
        meeting.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "matching schedules"):
            require_confirmable_meeting_faculty(meeting)
        old_text, old_room = self.combined_offering.schedule_text, self.combined_offering.room
        self.combined_offering.schedule_text, self.combined_offering.room = self.offering.schedule_text, self.offering.room
        self.combined_offering.save()
        AcademicOfferingSourceIntegrationService.record_change(actor=self.actor, offering=self.combined_offering,
            old_schedule_text=old_text, old_room=old_room, effective_from=date(2026, 1, 5), synchronize=True)
        meeting.refresh_from_db()
        self.assertEqual(require_confirmable_meeting_faculty(meeting), self.faculty)
        self.assertEqual(meeting.schedule_slot.schedule_version.offering_id, self.offering.pk)
        self.assertEqual(meeting.offering_links.count(), 2)
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering],
            term=self.term, start_date=meeting.meeting_date, end_date=meeting.meeting_date)
        self.assertEqual(issues + inspect_daily_occurrences(occurrences), [])
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(TeachingMeeting.objects.count(), 1)

    def test_retired_class_is_not_reactivated_by_later_source_or_faculty_save(self):
        meeting, _ = self.prepared(covered=True)
        self.change(text="T 08:00-09:00")
        archived = deepcopy(TeachingMeeting.objects.get(pk=meeting.pk).schedule_snapshot)
        self.replace(self.assignment, self.replacement, 5)
        self.change(text="M 11:00-12:00", room="207")
        meeting.refresh_from_db()
        self.assertEqual(meeting.schedule_snapshot, archived)
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering],
            academic_year=self.academic_year, term=self.term, meeting_date=meeting.meeting_date)
        self.assertEqual(issues, [])
        self.assertEqual(rounds[0].manifest_rows.exclude(meeting_id__in=retired_meeting_ids()).count(), 1)
        self.assertNotEqual(rounds[0].manifest_rows.exclude(meeting_id__in=retired_meeting_ids()).get().meeting_id, meeting.pk)

    def test_secondary_source_repair_reuses_compatible_primary_schedule(self):
        meeting, _ = self.prepared(covered=True, combined=True)
        self.combined_offering.room = "BAD"
        self.combined_offering.save()
        self.combined_offering.room = self.offering.room
        self.combined_offering.save()
        AcademicOfferingSourceIntegrationService.record_change(actor=self.actor, offering=self.combined_offering,
            old_schedule_text=self.offering.schedule_text, old_room="BAD", effective_from=date(2026, 1, 5), synchronize=True)
        meeting.refresh_from_db()
        self.assertEqual(meeting.schedule_slot.schedule_version.offering_id, self.offering.pk)
        self.assertFalse(meeting.schedule_snapshot.get("college_source_waiting"))
        self.assertEqual(meeting.offering_links.count(), 2)

    def test_different_campus_or_tenant_actor_has_no_automatic_sync_authority(self):
        outsider = existing.User.objects.create_user("college-outsider", "outsider@example.test", default_tenant=self.tenant,
            default_campus=existing.Campus.objects.create(tenant=self.tenant, code="OUT", name="Out"))
        UserPermission.objects.create(user=outsider, permission=Permission.objects.get(code="faculty_assignments.create"),
            grant_type="ALLOW", tenant=self.tenant, campus=outsider.default_campus)
        with self.assertRaises(PermissionDenied):
            record_attendance_assignment_event(actor=outsider, assignment=self.assignment, event_type="ASSIGNMENT_CREATED",
                reason="", academic_permission="faculty_assignments.create", effective_at=self.aware(2026, 1, 2))
        self.assertFalse(FacultyCoverage.objects.exists())

    def test_schedule_edit_original_form_reports_missing_boundary_and_invalid_text(self):
        values = {field: getattr(self.offering, f"{field}_id") for field in
                  ("tenant", "campus", "department", "program", "academic_year", "term", "course", "section")}
        values.update(schedule_text="TBA", room="new", status="OPEN", is_active="on")
        form = CourseOfferingForm(data=values, instance=self.offering, track_attendance_source=True)
        self.assertFalse(form.is_valid())
        self.assertIn("attendance_effective_from", form.errors)
        self.assertIn("schedule_text", form.errors)

    def test_regular_schedule_update_post_synchronizes_in_original_edit(self):
        meeting, _ = self.prepared(covered=True)
        values = {field: getattr(self.offering, f"{field}_id") for field in
                  ("tenant", "campus", "department", "program", "academic_year", "term", "course", "section")}
        values.update(schedule_text="M 10:00-11:30", room="206", status="OPEN", is_active="True",
            attendance_effective_from="2026-01-05")
        self.client.force_login(self.actor)
        response = self.client.post(reverse("admin_portal:offering_update", args=[self.offering.pk]), values)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        meeting.refresh_from_db()
        self.assertEqual(meeting.scheduled_minutes, 90)
        self.assertEqual(meeting.location_snapshot["room"], "206")
        self.assertFalse(meeting.reconciliations.filter(status="PENDING").exists())

    def test_routine_college_screen_has_no_initialization_or_one_meeting_substitute_action(self):
        self.client.force_login(self.actor)
        page = self.client.get(reverse("faculty_attendance:corrections"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Record a one-meeting substitute")
        self.assertContains(page, "Administrative diagnostics and historical recovery")


class CollegeVerdictCRegressionTests(TestCase):
    """Reuse the fixture helpers without re-discovering the earlier 26 cases."""

    permission_codes = CollegeSynchronizationTests.permission_codes
    setUpTestData = classmethod(CollegeSynchronizationTests.setUpTestData.__func__)
    setUp = CollegeSynchronizationTests.setUp
    aware = CollegeSynchronizationTests.aware
    schedule = CollegeSynchronizationTests.schedule
    coverage = CollegeSynchronizationTests.coverage
    sync = CollegeSynchronizationTests.sync
    prepared = CollegeSynchronizationTests.prepared
    replace = CollegeSynchronizationTests.replace
    change = CollegeSynchronizationTests.change

    def test_remaining_cross_weekday_pair_maps_after_exact_and_same_day_matches(self):
        from datetime import time
        from .college_sync import map_schedule_slots
        from .schedule_parsing import parse_schedule_text
        for old, new in (
            ("M 08:00-09:00; W 13:00-14:00", "T 08:00-09:00; W 13:00-14:00"),
            ("M 08:00-09:00; W 13:00-14:00", "T 08:00-09:00; W 14:00-15:00"),
        ):
            with self.subTest(old=old, new=new):
                mapping = map_schedule_slots(parse_schedule_text(old).slots, parse_schedule_text(new).slots)
                self.assertEqual(mapping[(0, time(8), time(9))], (1, time(8), time(9)))
                expected_wednesday = (2, time(13), time(14)) if "W 13" in new else (2, time(14), time(15))
                self.assertEqual(mapping[(2, time(13), time(14))], expected_wednesday)
                self.assertEqual(len(set(mapping.values())), 2)

    def test_combined_monday_moves_to_tuesday_while_wednesday_and_history_stay_intact(self):
        from datetime import time
        from .college_sync import combined_source_slot
        self.offering.schedule_text = "M 08:00-09:00; W 13:00-14:00"
        self.offering.save()
        group = self.recurring_fixture()
        monday, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 5))
        self.assertEqual(issues, [])
        original = monday[0].manifest_rows.get().meeting
        frozen = deepcopy(monday[0].manifest_rows.get().meeting_snapshot)
        saved_schedule = deepcopy(original.schedule_snapshot)
        wednesday, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 7))
        self.assertEqual(issues, [])
        wednesday_ids = list(wednesday[0].manifest_rows.values_list("meeting_id", flat=True))
        wednesday_snapshots = list(wednesday[0].manifest_rows.values_list("meeting_snapshot", flat=True))
        self.assertEqual(len(wednesday_ids), 2)
        self.change(text="T 08:00-09:00; W 13:00-14:00", day=12, room=self.offering.room)
        self.change_secondary(text="T 08:00-09:00; W 13:00-14:00", day=12, room=self.offering.room)
        self.assertEqual(combined_source_slot(group, self.offering, date(2026, 1, 13)), (1, time(8), time(9)))
        future, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering], term=self.term,
            start_date=date(2026, 1, 13), end_date=date(2026, 1, 13), combined_classes=[group])
        self.assertEqual(issues, [])
        self.assertEqual(len(future), 1)
        self.assertEqual(future[0].combined_definition_id, group.pk)
        tuesday, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 13))
        self.assertEqual(issues, [])
        shared = tuesday[0].manifest_rows.get().meeting
        self.assertEqual(shared.offering_links.count(), 2)
        self.assertEqual(TeachingMeeting.objects.filter(meeting_date=date(2026, 1, 13)).count(), 1)
        again, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 13))
        self.assertEqual(issues, [])
        self.assertEqual(again[0].manifest_rows.get().meeting_id, shared.pk)
        prior, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering], term=self.term,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 5), combined_classes=[group])
        self.assertEqual(issues, [])
        self.assertEqual(len(prior), 1)
        self.assertEqual((prior[0].start_time, prior[0].end_time), (time(8), time(9)))
        original.refresh_from_db()
        self.assertEqual(original.schedule_snapshot, saved_schedule)
        self.assertEqual(monday[0].manifest_rows.get().meeting_snapshot, frozen)
        self.assertEqual(list(wednesday[0].manifest_rows.values_list("meeting_id", flat=True)), wednesday_ids)
        self.assertEqual(list(wednesday[0].manifest_rows.values_list("meeting_snapshot", flat=True)), wednesday_snapshots)
        future_wednesday, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering], term=self.term,
            start_date=date(2026, 1, 14), end_date=date(2026, 1, 14), combined_classes=[group])
        self.assertEqual(issues, [])
        self.assertEqual(len(future_wednesday), 2)
        self.assertTrue(all(o.start_time == time(13) and not o.is_combined for o in future_wednesday))
        group.refresh_from_db()
        self.assertEqual((group.weekday, group.start_time), (0, time(8)))

    def test_ambiguous_cross_weekday_remainder_rejects_without_changing_saved_evidence(self):
        from django.db import transaction
        from .college_sync import map_schedule_slots
        from .schedule_parsing import parse_schedule_text
        old_text = "M 08:00-09:00; W 13:00-14:00"
        new_text = "T 08:00-09:00; TH 13:00-14:00"
        with self.assertRaisesMessage(ValidationError, "multiple unmatched"):
            map_schedule_slots(parse_schedule_text(old_text).slots, parse_schedule_text(new_text).slots)
        self.offering.schedule_text = old_text
        self.offering.save()
        group = self.recurring_fixture()
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 12))
        self.assertEqual(issues, [])
        frozen = deepcopy(rounds[0].manifest_rows.get().meeting_snapshot)
        meeting = rounds[0].manifest_rows.get().meeting
        schedule = deepcopy(meeting.schedule_snapshot)
        with self.assertRaisesMessage(ValidationError, "multiple unmatched"):
            with transaction.atomic():
                self.change(text=new_text, day=12, room=self.offering.room)
        self.offering.refresh_from_db()
        meeting.refresh_from_db()
        self.assertEqual(self.offering.schedule_text, old_text)
        self.assertEqual(meeting.schedule_snapshot, schedule)
        self.assertEqual(rounds[0].manifest_rows.get().meeting_snapshot, frozen)
        self.assertFalse(self.offering.attendance_source_changes.exists())
        current, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering], term=self.term,
            start_date=date(2026, 1, 12), end_date=date(2026, 1, 12), combined_classes=[group])
        self.assertEqual(issues, [])
        self.assertEqual(len(current), 1)

    def test_ambiguous_weekday_change_returns_source_form_error_without_saving(self):
        self.offering.schedule_text = "M 08:00-09:00; W 13:00-14:00"
        self.offering.save()
        self.recurring_fixture()
        values = {field: getattr(self.offering, f"{field}_id") for field in
                  ("tenant", "campus", "department", "program", "academic_year", "term", "course", "section")}
        values.update(schedule_text="T 08:00-09:00; TH 13:00-14:00", room=self.offering.room,
            status="OPEN", is_active="True", attendance_effective_from="2026-01-12")
        self.client.force_login(self.actor)
        response = self.client.post(reverse("admin_portal:offering_update", args=[self.offering.pk]), values)
        self.assertEqual(response.status_code, 200)
        self.assertIn("schedule_text", response.context["form"].errors)
        self.assertContains(response, "multiple unmatched")
        self.offering.refresh_from_db()
        self.assertEqual(self.offering.schedule_text, "M 08:00-09:00; W 13:00-14:00")
        self.assertFalse(self.offering.attendance_source_changes.exists())

    def unassign(self, day):
        self.assignment.is_active = False
        self.assignment.save()
        return self.sync(at=self.aware(2026, 1, day), event_type="UNASSIGNMENT",
            source_reference=f"synthetic-unassignment:{day}", clear_coverage=True,
            permission="faculty_assignments.update")

    def test_reactivation_after_unassignment_is_durable_and_retry_safe(self):
        creation = self.sync()
        self.unassign(5)
        self.assignment.is_active = True
        self.assignment.save()
        reactivation = self.sync(at=self.aware(2026, 1, 12), event_type="ASSIGNMENT_REACTIVATED")
        self.assertNotEqual(creation.source_reference, reactivation.source_reference)
        self.assertTrue(FacultyCoverage.objects.filter(effective_from=self.aware(2026, 1, 12)).exists())
        counts = (FacultyCoverage.objects.count(), CoverageReconciliation.objects.count())
        retry = self.sync(at=self.aware(2026, 1, 12), event_type="ASSIGNMENT_REACTIVATED")
        self.assertEqual(retry.pk, reactivation.pk)
        self.assertEqual((FacultyCoverage.objects.count(), CoverageReconciliation.objects.count()), counts)
        self.unassign(19)
        self.assignment.is_active = True
        self.assignment.save()
        second = self.sync(at=self.aware(2026, 1, 26), event_type="ASSIGNMENT_REACTIVATED")
        self.assertNotEqual(second.pk, reactivation.pk)
        self.assertEqual(list(FacultyCoverage.objects.order_by("effective_from").values_list("effective_until", flat=True))[:2],
            [self.aware(2026, 1, 5), self.aware(2026, 1, 19)])

    def test_removing_morning_slot_preserves_afternoon_and_archives_only_removed(self):
        self.offering.schedule_text = "M 08:00-09:00; M 13:00-14:00"
        self.offering.save()
        self.sync()
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 5))
        self.assertEqual(issues, [])
        original = list(rounds[0].manifest_rows.order_by("sequence"))
        self.change(text="M 13:00-14:00", room=self.offering.room)
        self.assertIn(original[0].meeting_id, retired_meeting_ids())
        self.assertNotIn(original[1].meeting_id, retired_meeting_ids())
        original[1].meeting.refresh_from_db()
        self.assertEqual(original[1].meeting.starts_at, self.aware(2026, 1, 5, 13))
        resumed, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 5))
        self.assertEqual(issues, [])
        self.assertEqual(resumed[0].pk, rounds[0].pk)
        self.assertEqual(list(resumed[0].manifest_rows.exclude(meeting_id__in=retired_meeting_ids()).values_list("meeting_id", flat=True)),
            [original[1].meeting_id])
        self.assertEqual(list(resumed[0].manifest_rows.order_by("sequence").values_list("meeting_snapshot", flat=True)),
            [row.meeting_snapshot for row in original])

    def recurring_fixture(self):
        from datetime import time
        from .combined_classes import RecurringCombinedClassService
        self.combined_offering.schedule_text = self.offering.schedule_text
        self.combined_offering.is_active = True
        self.combined_offering.save()
        self.sync()
        other = FacultyAssignment.objects.create(offering=self.combined_offering, faculty_user=self.faculty,
            tenant=self.tenant, campus=self.campus, is_primary=True)
        self.sync(other)
        group = RecurringCombinedClassService.create(actor=self.actor, tenant_id=self.tenant.pk,
            campus_id=self.campus.pk, academic_year_id=self.academic_year.pk, term_id=self.term.pk,
            offering_ids=[self.offering.pk, self.combined_offering.pk], weekday=0, start_time=time(8), end_time=time(9),
            effective_from=date(2026, 1, 1), effective_until=date(2026, 1, 31), reason="Synthetic explicit combination")
        return group

    def change_secondary(self, text="M 10:00-11:30", room="206", day=5):
        old_text, old_room = self.combined_offering.schedule_text, self.combined_offering.room
        self.combined_offering.schedule_text, self.combined_offering.room = text, room
        self.combined_offering.save()
        return AcademicOfferingSourceIntegrationService.record_change(actor=self.actor, offering=self.combined_offering,
            old_schedule_text=old_text, old_room=old_room, effective_from=date(2026, 1, day), synchronize=True)

    def test_recurring_combination_follows_agreed_sources_for_prepared_and_future_dates(self):
        group = self.recurring_fixture()
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 5))
        self.assertEqual(issues, [])
        frozen = deepcopy(rounds[0].manifest_rows.get().meeting_snapshot)
        self.change()
        self.change_secondary()
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering], term=self.term,
            start_date=date(2026, 1, 12), end_date=date(2026, 1, 12), combined_classes=[group])
        self.assertEqual(issues + inspect_daily_occurrences(occurrences), [])
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].combined_definition_id, group.pk)
        future, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 12))
        self.assertEqual(issues, [])
        self.assertEqual(future[0].manifest_rows.count(), 1)
        self.assertEqual(rounds[0].manifest_rows.get().meeting_snapshot, frozen)
        group.refresh_from_db()
        from datetime import time
        self.assertEqual((group.start_time, group.end_time), (time(8), time(9)))

    def test_new_assigned_class_extends_prepared_list_from_audited_source_evidence(self):
        meeting, round_ = self.prepared(covered=True)
        frozen = deepcopy(round_.manifest_rows.get().meeting_snapshot)
        self.combined_offering.schedule_text = "M 13:00-14:00"
        self.combined_offering.is_active = True
        self.combined_offering.save()
        other = FacultyAssignment.objects.create(offering=self.combined_offering, faculty_user=self.faculty,
            tenant=self.tenant, campus=self.campus, is_primary=True)
        self.sync(other)
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=meeting.meeting_date)
        self.assertEqual(issues, [])
        self.assertEqual(rounds[0].pk, round_.pk)
        self.assertEqual(round_.manifest_rows.count(), 2)
        self.assertEqual(round_.manifest_rows.get(meeting=meeting).meeting_snapshot, frozen)
        rounds[0].refresh_from_db()
        self.assertGreater(rounds[0].manifest_revision, 1)
        revision = rounds[0].manifest_revision
        again, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=meeting.meeting_date)
        self.assertEqual(issues, [])
        self.assertEqual(again[0].manifest_revision, revision)
        self.assertEqual(round_.manifest_rows.count(), 2)

    def test_date_only_schedule_change_on_eight_am_activation_day_is_valid(self):
        self.sync(at=self.aware(2026, 1, 5, 8))
        values = {field: getattr(self.offering, f"{field}_id") for field in
                  ("tenant", "campus", "department", "program", "academic_year", "term", "course", "section")}
        values.update(schedule_text="M 09:00-10:00", room="206", status="OPEN", is_active="True",
            attendance_effective_from="2026-01-05")
        form = CourseOfferingForm(data=values, instance=self.offering, track_attendance_source=True)
        self.assertTrue(form.is_valid(), form.errors)
        self.offering.refresh_from_db()
        self.change(text="M 09:00-10:00")
        self.assertEqual(FacultyCoverage.objects.get().effective_from, self.aware(2026, 1, 5, 8))

    def test_reactivation_retry_normalizes_timezone_and_still_checks_direct_deny(self):
        from datetime import timezone as utc_timezone
        self.sync()
        self.unassign(5)
        self.assignment.is_active = True
        self.assignment.save()
        first = self.sync(at=self.aware(2026, 1, 12), event_type="ASSIGNMENT_REACTIVATED")
        retry = self.sync(at=self.aware(2026, 1, 12).astimezone(utc_timezone.utc),
            event_type="ASSIGNMENT_REACTIVATED", source_reference="different-retry-reference")
        self.assertEqual(retry.pk, first.pk)
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code="faculty_attendance.reconcile"),
            grant_type="DENY", tenant=self.tenant)
        with self.assertRaises(PermissionDenied):
            self.sync(at=self.aware(2026, 1, 12), event_type="ASSIGNMENT_REACTIVATED")
        self.assertEqual(FacultyCoverage.objects.count(), 2)

    def test_removing_recorded_morning_preserves_result_and_legitimate_afternoon(self):
        self.offering.schedule_text = "M 08:00-09:00; M 13:00-14:00"
        self.offering.save()
        self.sync()
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 5))
        self.assertEqual(issues, [])
        rows = list(rounds[0].manifest_rows.order_by("sequence"))
        AttendanceResultService.confirm_present(actor=self.actor, checking_round=rounds[0], manifest_revision=1,
            reviewed_rows=[{"meeting_id": row.meeting_id, "result_revision": 0} for row in rows])
        review = review_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        self.assertTrue(review.ready, review.blockers)
        publication = publish_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5),
            expected_fingerprint=review.fingerprint, submission_key="synthetic-two-slots", publication_reason="")
        frozen = list(publication.entries.values_list("meeting_snapshot", "findings_snapshot"))
        self.change(text="M 13:00-14:00", room=self.offering.room)
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering], term=self.term,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        self.assertEqual(issues + inspect_daily_occurrences(occurrences), [])
        self.assertEqual(len(occurrences), 2)
        self.assertEqual({o.historical_meeting_id for o in occurrences}, {row.meeting_id for row in rows})
        self.assertEqual(list(publication.entries.values_list("meeting_snapshot", "findings_snapshot")), frozen)
        self.assertEqual(list(AttendanceResult.objects.values_list("revision", flat=True)), [1, 1])

    def test_recurring_conflict_blocks_only_explicit_combination_until_sources_agree(self):
        group = self.recurring_fixture()
        self.change()
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering], term=self.term,
            start_date=date(2026, 1, 12), end_date=date(2026, 1, 12), combined_classes=[group])
        self.assertEqual([issue.code for issue in issues], ["COMBINED_SOURCE_CONFLICT"])
        self.assertEqual(occurrences, [])
        self.change_secondary()
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering], term=self.term,
            start_date=date(2026, 1, 12), end_date=date(2026, 1, 12), combined_classes=[group])
        self.assertEqual(issues, [])
        self.assertEqual(len(occurrences), 1)
        from apps.auditlog.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_ATTENDANCE_COLLEGE_COMBINED_SYNC",
            entity_id=str(group.pk), after_json__original_definition_preserved=True).exists())

    def test_recurring_weekday_change_keeps_earlier_evidence_and_one_future_meeting(self):
        from datetime import time
        group = self.recurring_fixture()
        self.change(text="T 10:00-11:30", day=12)
        self.change_secondary(text="T 10:00-11:30", day=12)
        prior, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering], term=self.term,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 11), combined_classes=[group])
        self.assertEqual(issues, [])
        self.assertEqual(len(prior), 1)
        self.assertEqual((prior[0].meeting_date, prior[0].start_time, prior[0].combined_definition_id),
                         (date(2026, 1, 5), time(8), group.pk))
        current, issues = expected_daily_occurrences(offerings=[self.offering, self.combined_offering], term=self.term,
            start_date=date(2026, 1, 12), end_date=date(2026, 1, 13), combined_classes=[group])
        self.assertEqual(issues, [])
        self.assertEqual(len(current), 1)
        self.assertEqual((current[0].meeting_date, current[0].start_time, current[0].combined_definition_id),
                         (date(2026, 1, 13), time(10), group.pk))
        group.refresh_from_db()
        self.assertEqual((group.weekday, group.start_time), (0, time(8)))

    def test_published_combination_survives_source_change_without_duplicate_expected_rows(self):
        group = self.recurring_fixture()
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 5))
        self.assertEqual(issues, [])
        meeting = rounds[0].manifest_rows.get().meeting
        AttendanceResultService.confirm_present(actor=self.actor, checking_round=rounds[0], manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}])
        review = review_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        self.assertTrue(review.ready, review.blockers)
        publication = publish_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5),
            expected_fingerprint=review.fingerprint, submission_key="synthetic-recurring", publication_reason="")
        frozen = deepcopy(publication.entries.get().meeting_snapshot)
        self.change()
        self.change_secondary()
        review = review_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        self.assertTrue(review.ready, review.blockers)
        self.assertEqual(len(review.records), 1)
        self.assertEqual(publication.entries.get().meeting_snapshot, frozen)
        meeting.refresh_from_db()
        self.assertEqual(meeting.scheduled_minutes, 60)
        self.assertEqual(AttendanceResult.objects.get(meeting=meeting).history.count(), 1)

    def test_retired_sibling_does_not_authorize_unaudited_addition(self):
        meeting, round_ = self.prepared(covered=True)
        self.change(text="T 08:00-09:00")
        self.combined_offering.schedule_text = "M 13:00-14:00"
        self.combined_offering.is_active = True
        self.combined_offering.save()
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=meeting.meeting_date)
        self.assertEqual([issue.code for issue in issues], ["DAILY_ROUND_STALE"])
        self.assertEqual(rounds[0].manifest_rows.count(), 1)
        self.assertEqual(rounds[0].manifest_rows.get().meeting_id, meeting.pk)

    def test_audited_new_unassigned_class_is_added_but_remains_unresolved(self):
        from apps.core.services.audit import AuditService
        meeting, round_ = self.prepared(covered=True)
        self.combined_offering.schedule_text = "M 13:00-14:00"
        self.combined_offering.is_active = True
        self.combined_offering.save()
        AuditService.log_event(action="CREATE", portal="ADMIN", entity_type="CourseOffering",
            entity_id=self.combined_offering.pk, actor=self.actor, tenant=self.tenant, campus=self.campus,
            after_data={"schedule_text": self.combined_offering.schedule_text, "room": self.combined_offering.room})
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=meeting.meeting_date)
        self.assertEqual(issues, [])
        self.assertEqual(rounds[0].manifest_rows.count(), 2)
        added = rounds[0].manifest_rows.exclude(meeting=meeting).get().meeting
        self.assertTrue(added.unresolved_coverage)
        self.assertFalse(AttendanceResult.objects.filter(meeting=added).exists())

    def test_same_day_pre_activation_change_rejected_without_backdating(self):
        self.sync(at=self.aware(2026, 1, 5, 8))
        from .college_sync import validate_boundary
        with self.assertRaisesMessage(ValidationError, "before the verified attendance start"):
            validate_boundary(self.offering, date(2026, 1, 5), old_schedule_text="M 08:00-09:00",
                new_schedule_text="M 07:00-08:00")
        self.assertEqual(FacultyCoverage.objects.get().effective_from, self.aware(2026, 1, 5, 8))

    def test_audited_extension_does_not_bypass_encode_direct_deny(self):
        meeting, round_ = self.prepared(covered=True)
        self.combined_offering.schedule_text = "M 13:00-14:00"
        self.combined_offering.is_active = True
        self.combined_offering.save()
        other = FacultyAssignment.objects.create(offering=self.combined_offering, faculty_user=self.faculty,
            tenant=self.tenant, campus=self.campus, is_primary=True)
        self.sync(other)
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code="faculty_attendance.encode"),
            grant_type="DENY", tenant=self.tenant)
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year, term=self.term, meeting_date=meeting.meeting_date)
        self.assertTrue(issues)
        self.assertEqual(round_.manifest_rows.count(), 1)
        self.assertEqual(TeachingMeeting.objects.count(), 1)

    def test_activation_day_schedule_post_preserves_earlier_class_and_coverage_floor(self):
        from datetime import time
        self.offering.schedule_text = "M 07:00-08:00; M 08:00-09:00"
        self.offering.save()
        self.sync(at=self.aware(2026, 1, 5, 8))
        rounds, issues = prepare_daily_encoding(actor=self.actor, offerings=[self.offering],
            academic_year=self.academic_year, term=self.term, meeting_date=date(2026, 1, 5))
        self.assertEqual(issues, [])
        earlier = rounds[0].manifest_rows.order_by("sequence").first().meeting
        saved = deepcopy(earlier.schedule_snapshot)
        values = {field: getattr(self.offering, f"{field}_id") for field in
                  ("tenant", "campus", "department", "program", "academic_year", "term", "course", "section")}
        values.update(schedule_text="M 07:00-08:00; M 09:00-10:00", room=self.offering.room,
            status="OPEN", is_active="True", attendance_effective_from="2026-01-05")
        self.client.force_login(self.actor)
        response = self.client.post(reverse("admin_portal:offering_update", args=[self.offering.pk]), values)
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        earlier.refresh_from_db()
        self.assertEqual(earlier.schedule_snapshot, saved)
        self.assertEqual(earlier.starts_at, self.aware(2026, 1, 5, 7))
        self.assertTrue(earlier.unresolved_coverage)
        self.assertEqual(FacultyCoverage.objects.get().effective_from, self.aware(2026, 1, 5, 8))
        occurrences, issues = expected_daily_occurrences(offerings=[self.offering], term=self.term,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 5))
        self.assertEqual(issues, [])
        self.assertEqual([o.start_time for o in occurrences], [time(7), time(9)])
        self.assertEqual(TeachingMeeting.objects.count(), 2)
