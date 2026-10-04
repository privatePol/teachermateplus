"""Supplied staging shapes reproduced in disposable SQLite, never live records."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import os
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.academics.models import FacultyAssignment
from apps.auditlog.models import AuditLog
from apps.rbac.models import Permission, UserPermission
from . import tests_faculty_cutoffs as fixtures
from .assignment_attribution import AssignmentEvidence, INITIALIZED
from .coverage_initialization import CoverageInitializationService
from .daily_encoding import expected_daily_occurrences, inspect_daily_occurrences, prepare_daily_encoding, resolve_occurrence_faculty
from .dtr import preview_dtr
from .models import AttendanceResult, CoverageReconciliation, FacultyCoverage, MeetingReconciliation, TeachingMeeting
from .observations import AttendanceResultService, CheckingRoundService, ObservationService, require_confirmable_meeting_faculty, resolve_attendance_faculty, prime_meeting_readiness
from .permissions import ENCODE_PERMISSION, PUBLISH_FACULTY_PERMISSION


class LegacyAssignmentTests(TestCase):
    permission_codes = fixtures.FacultyCutoffTests.permission_codes
    setUpTestData = classmethod(fixtures.FacultyCutoffTests.setUpTestData.__func__)
    aware = fixtures.FacultyCutoffTests.aware
    review = fixtures.FacultyCutoffTests.review
    slice = fixtures.FacultyCutoffTests.slice
    publish = fixtures.FacultyCutoffTests.publish
    final = fixtures.FacultyCutoffTests.final

    def scope(self):
        return dict(tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=date(2026, 10, 1), end_date=date(2026, 10, 1))

    def setUp(self):
        fixtures.FacultyCutoffTests.setUp(self)
        type(self.academic_year).objects.filter(pk=self.academic_year.pk).update(
            start_date=date(2026, 6, 22), end_date=date(2027, 5, 31), code="2026-2027")
        type(self.term).objects.filter(pk=self.term.pk).update(
            start_date=date(2026, 6, 22), end_date=date(2026, 10, 30))
        self.term.refresh_from_db()
        self.academic_year.refresh_from_db()
        self.client.force_login(self.actor)

    def case(self, *, gil=False, offering=None, faculty=None, coverage=True, materialize=True):
        offering, faculty = offering or self.offering, faculty or self.faculty
        type(faculty).objects.filter(pk=faculty.pk).update(first_name="Gil" if gil else "Joy Preachie R.", last_name="Chavez" if gil else "Ramirez")
        faculty.refresh_from_db()
        type(offering).objects.filter(pk=offering.pk).update(
            schedule_text="T/TH 07:30-09:00" if gil else "T/TH 19:00-20:30", room="301" if gil else "304")
        offering.refresh_from_db()
        assignment = FacultyAssignment(offering=offering, faculty_user=faculty, tenant=None, campus=None,
            is_active=True, is_primary=True, response_status="ACCEPTED",
            accepted_at=self.aware(2026, 8, 7, 14, 42) if gil else self.aware(2026, 7, 24, 18, 16))
        FacultyAssignment.objects.bulk_create([assignment])
        FacultyAssignment.objects.filter(pk=assignment.pk).update(assigned_at=self.aware(2026, 7, 20, 0, 1))
        if coverage:
            FacultyCoverage.objects.create(tenant=self.tenant, campus=self.campus, department=self.department,
                offering=offering, faculty_user=faculty, source_assignment=assignment,
                effective_from=self.aware(2026, 10, 2), effective_until=self.aware(2026, 10, 31), created_by=self.actor)
        if not materialize:
            return assignment, None, None
        rounds, _ = prepare_daily_encoding(actor=self.actor, offerings=[offering], academic_year=self.academic_year,
            term=self.term, meeting_date=date(2026, 10, 1))
        return assignment, rounds[0], rounds[0].manifest_rows.get().meeting

    def occurrences(self, offerings=None, day=date(2026, 10, 1)):
        return expected_daily_occurrences(offerings=offerings or [self.offering], term=self.term, start_date=day, end_date=day)[0]

    def assert_case(self, *, gil=False):
        assignment, round_, meeting = self.case(gil=gil)
        self.assertTrue(meeting.unresolved_coverage)
        self.assertIsNone(meeting.faculty_user_id)
        self.assertIsNone(meeting.coverage_id)
        original = deepcopy((meeting.faculty_snapshot, list(round_.manifest_rows.values())))
        self.assertEqual(resolve_occurrence_faculty(self.occurrences()[0]).pk, self.faculty.pk)
        self.assertFalse(inspect_daily_occurrences(self.occurrences()))
        response = self.client.get(reverse("faculty_attendance:round", args=[round_.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r.meeting_id for r in response.context["rows"]], [meeting.pk])
        self.assertContains(response, self.faculty.last_name)
        self.assertContains(response, 'name="late_minutes"')
        self.assertContains(response, 'name="absence_code"')
        self.assertNotContains(response, 'id="present-form"')
        publication = self.publish()
        self.assertEqual(publication.entries.get().meeting_id, meeting.pk)
        self.assertEqual(publication.entries.get().meeting_snapshot["assignment_attribution"]["source_assignment_ids"], [assignment.pk])
        self.assertEqual(preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty).snapshot["net_payable_hours"], "1.50")
        self.assertEqual(self.final(publication).snapshot["net_payable_hours"], "1.50")
        meeting.refresh_from_db()
        self.assertEqual((meeting.faculty_snapshot, list(round_.manifest_rows.values())), original)
        self.assertEqual(FacultyCoverage.objects.get().effective_from, self.aware(2026, 10, 2))
        self.assertEqual(TeachingMeeting.objects.count(), 1)
        self.assertFalse(AttendanceResult.objects.exists())
        print(f"AFTER: {self.faculty.full_name}; October 1; existing OPEN row; 1.50 hours; immutable manifest; no checker finding.")

    def test_joy_existing_open_row_publication_and_dtr(self):
        self.assert_case()

    def test_gil_existing_open_row_publication_and_dtr(self):
        self.assert_case(gil=True)

    def test_missing_supplementary_coverage_and_unmaterialized_class(self):
        self.case(coverage=False)
        # Another scheduled date has no meeting: preview and publication agree.
        scope = {**self.scope(), "start_date": date(2026, 9, 29), "end_date": date(2026, 9, 29)}
        with patch.object(self, "scope", return_value=scope):
            self.assertTrue(self.slice().ready)
            publication = self.publish()
        self.assertEqual(self.final(publication).snapshot["net_payable_hours"], "1.50")
        self.assertEqual(TeachingMeeting.objects.count(), 2)
        self.assertFalse(FacultyCoverage.objects.exists())

    def test_saved_exception_republication_preserves_prior_final_and_manifest(self):
        _, round_, meeting = self.case()
        initial = self.publish()
        final = self.final(initial)
        snapshot = deepcopy(final.snapshot)
        observation = ObservationService.record(actor=self.actor, checking_round=round_, meeting_id=meeting.pk,
            manifest_revision=1, submission_key="legacy-late", findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 15}])
        AttendanceResultService.select_observation(actor=self.actor, observation=observation, expected_revision=0, reason="")
        revised = self.publish(key="legacy-exception")
        self.assertEqual(self.final(revised).snapshot["net_payable_hours"], "1.25")
        final.refresh_from_db()
        self.assertEqual(final.snapshot, snapshot)
        self.assertEqual(AttendanceResult.objects.get().revision, 1)

    def saved_late_case(self):
        assignment, round_, meeting = self.case()
        observation = ObservationService.record(actor=self.actor, checking_round=round_, meeting_id=meeting.pk,
            manifest_revision=1, submission_key="later-unassignment-late",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 15}])
        result = AttendanceResultService.select_observation(actor=self.actor, observation=observation,
            expected_revision=0, reason="")
        return assignment, round_, meeting, result

    def later_unassignment(self, assignment, *, at=None, original_path=False):
        from apps.academics.services import record_attendance_assignment_event
        permission, _ = Permission.objects.get_or_create(code="faculty_assignments.update",
            defaults={"module": "faculty_assignments", "action": "update"})
        UserPermission.objects.get_or_create(user=self.actor, permission=permission, grant_type="ALLOW",
            tenant=self.tenant, campus=self.campus)
        assignment.is_active = False
        assignment.save(update_fields=["is_active", "updated_at"])
        change = record_attendance_assignment_event(actor=self.actor, assignment=assignment,
            event_type="UNASSIGNMENT", reason="Authorized later unassignment", clear_coverage=True,
            prior_faculty=self.faculty, effective_at=at or self.aware(2026, 10, 10),
            source_reference="focused-later-unassignment",
            academic_permission=None if original_path else "faculty_assignments.update")
        if original_path:
            from .academic_integration import AcademicCoverageIntegrationService
            self.assertEqual(change.status, "PENDING")
            change = AcademicCoverageIntegrationService.resolve(actor=self.actor, reconciliation=change,
                effective_at=at or self.aware(2026, 10, 10), reason="Authorized later unassignment")
            coverage = FacultyCoverage.objects.get(source_assignment=assignment)
            audit = AuditLog.objects.get(entity_type="FacultyCoverage", entity_id=str(coverage.pk),
                action="FACULTY_ATTENDANCE_COVERAGE_CLOSED")
            self.assertEqual(coverage.effective_until, change.effective_at)
            self.assertEqual(audit.after_json["effective_until"], change.effective_at.isoformat())
        change.refresh_from_db()
        self.assertEqual(change.status, "RESOLVED")
        return change

    def test_later_unassignment_keeps_saved_exception_ready_and_final_unchanged(self):
        self.assert_later_unassignment_preserves_history()

    def test_original_path_later_unassignment_keeps_saved_exception_ready_and_final_unchanged(self):
        self.assert_later_unassignment_preserves_history(original_path=True)

    def assert_later_unassignment_preserves_history(self, *, original_path=False):
        import json
        from django.core.serializers.json import DjangoJSONEncoder
        assignment, round_, meeting, result = self.saved_late_case()
        publication = self.publish()
        final = self.final(publication)
        def final_bytes():
            return json.dumps(type(final).objects.filter(pk=final.pk).values().get(),
                cls=DjangoJSONEncoder, sort_keys=True).encode()
        original_final = final_bytes()
        original_history = deepcopy(list(result.history.values()))
        original_manifest = deepcopy(list(round_.manifest_rows.values()))
        self.later_unassignment(assignment, original_path=original_path)
        meeting.refresh_from_db()
        self.assertIsNone(meeting.faculty_user_id)
        self.assertIsNone(meeting.coverage_id)
        self.assertEqual(resolve_attendance_faculty(meeting, result), (self.faculty, "SAVED_RESULT"))
        reviewed = self.slice()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        print("LATER_UNASSIGNMENT:", "original" if original_path else "College",
            [b.code for b in reviewed.blockers], preview.blockers)
        self.assertTrue(reviewed.ready)
        self.assertEqual(len(reviewed.records), 1)
        self.assertEqual(reviewed.records[0].result.pk, result.pk)
        self.assertEqual(reviewed.records[0].faculty.pk, self.faculty.pk)
        self.assertFalse(preview.blockers)
        self.assertEqual(preview.snapshot["net_payable_hours"], "1.25")
        self.assertEqual(preview.snapshot["gross_deductions"], "0.25")
        # Both publication/finalization retries re-read the locked source;
        # unchanged earlier evidence must reuse the existing immutable records.
        self.assertEqual(self.publish(key="after-later-unassignment").pk, publication.pk)
        self.assertEqual(self.final(publication).pk, final.pk)
        self.assertEqual(final_bytes(), original_final)
        self.assertEqual(list(result.history.values()), original_history)
        self.assertEqual(list(round_.manifest_rows.values()), original_manifest)
        self.assertIsNone(resolve_occurrence_faculty(self.occurrences(day=date(2026, 10, 13))[0]))

    def test_saved_exception_after_unassignment_has_no_historical_fallback(self):
        from datetime import timedelta
        assignment, _, meeting, result = self.saved_late_case()
        self.later_unassignment(assignment)
        for day in (10, 13):
            with self.subTest(day=day):
                meeting.starts_at = self.aware(2026, 10, day, 19)
                meeting.ends_at = meeting.starts_at + timedelta(minutes=90)
                meeting.meeting_date = date(2026, 10, day)
                with self.assertRaises(ValidationError):
                    require_confirmable_meeting_faculty(meeting, result=result,
                        result_revision=result.history.get(revision=result.revision))

    def test_saved_exception_does_not_bypass_on_or_before_class_change(self):
        assignment, _, meeting, result = self.saved_late_case()
        change = self.later_unassignment(assignment)
        for boundary in (self.aware(2026, 10, 1), meeting.starts_at, self.aware(2026, 10, 1, 19, 30)):
            with self.subTest(boundary=boundary):
                CoverageReconciliation.objects.filter(pk=change.pk).update(effective_at=boundary)
                self.assertFalse(self.slice().ready)

    def test_saved_exception_does_not_bypass_pending_reconciliation(self):
        assignment, _, meeting, _ = self.saved_late_case()
        change = self.later_unassignment(assignment)
        pending = MeetingReconciliation.objects.create(meeting=meeting, source_type="COVERAGE",
            source_reference="focused-pending-history", detected_by=self.actor)
        self.assertFalse(self.slice().ready)
        pending.delete()
        CoverageReconciliation.objects.filter(pk=change.pk).update(status="PENDING")
        self.assertFalse(self.slice().ready)

    def test_saved_exception_does_not_bypass_conflicting_ownership(self):
        assignment, _, meeting, _ = self.saved_late_case()
        self.later_unassignment(assignment)
        coverage = FacultyCoverage.objects.get()
        FacultyCoverage.objects.filter(pk=coverage.pk).update(faculty_user=self.replacement)
        self.assertFalse(self.slice().ready)
        FacultyCoverage.objects.filter(pk=coverage.pk).update(faculty_user=self.faculty)
        TeachingMeeting.objects.filter(pk=meeting.pk).update(faculty_user=self.replacement, unresolved_coverage=False)
        self.assertIn("FACULTY_ATTRIBUTION_CONFLICT", [b.code for b in self.slice().blockers])

    def test_saved_exception_does_not_bypass_foreign_scope(self):
        assignment, _, _, _ = self.saved_late_case()
        change = self.later_unassignment(assignment)
        foreign_campus = type(self.campus).objects.create(tenant=self.tenant, code="LATER_FOREIGN", name="Other campus")
        for row in (assignment, FacultyCoverage.objects.get(), change):
            with self.subTest(model=type(row).__name__):
                original = row.campus_id
                type(row).objects.filter(pk=row.pk).update(campus=foreign_campus)
                self.assertFalse(self.slice().ready)
                type(row).objects.filter(pk=row.pk).update(campus_id=original)
        foreign_tenant = type(self.tenant).objects.create(code="LATER_FOREIGN", name="Other tenant")
        for row in (assignment, FacultyCoverage.objects.get(), change):
            with self.subTest(model=type(row).__name__, scope="tenant"):
                original = row.tenant_id
                type(row).objects.filter(pk=row.pk).update(tenant=foreign_tenant)
                self.assertFalse(self.slice().ready)
                type(row).objects.filter(pk=row.pk).update(tenant_id=original)
        foreign_department = type(self.department).objects.create(tenant=self.tenant,
            campus=self.campus, code="LATER_FOREIGN", name="Other department")
        for row in (FacultyCoverage.objects.get(), change):
            with self.subTest(model=type(row).__name__, scope="department"):
                type(row).objects.filter(pk=row.pk).update(department=foreign_department)
                self.assertFalse(self.slice().ready)
                type(row).objects.filter(pk=row.pk).update(department=self.department)

    def test_saved_exception_requires_matching_current_revision(self):
        assignment, _, _, result = self.saved_late_case()
        self.later_unassignment(assignment)
        revision = result.history.get(revision=result.revision)
        for field, invalid in (("faculty_user_id", self.replacement.pk), ("revision", 99),
                ("status", "UNVERIFIED"), ("findings_snapshot", []), ("source_observation_id", None)):
            with self.subTest(field=field):
                original = getattr(revision, field)
                type(revision).objects.filter(pk=revision.pk).update(**{field: invalid})
                self.assertFalse(self.slice().ready)
                type(revision).objects.filter(pk=revision.pk).update(**{field: original})
        self.assertTrue(self.slice().ready)

    def test_saved_exception_readiness_reuses_batched_evidence_without_queries(self):
        assignment, _, meeting, result = self.saved_late_case()
        self.later_unassignment(assignment)
        revision = result.history.get(revision=result.revision)
        prime_meeting_readiness([meeting])
        with self.assertNumQueries(0):
            self.assertEqual(require_confirmable_meeting_faculty(meeting,
                result=result, result_revision=revision).pk, self.faculty.pk)

    def test_saved_exception_does_not_bypass_retirement_or_source_waiting(self):
        assignment, _, meeting, result = self.saved_late_case()
        self.later_unassignment(assignment)
        revision = result.history.get(revision=result.revision)
        meeting._attendance_retired = True
        with self.assertRaisesMessage(ValidationError, "rescheduled"):
            require_confirmable_meeting_faculty(meeting, result=result, result_revision=revision)
        meeting._attendance_retired = False
        meeting.schedule_snapshot["college_source_waiting"] = True
        with self.assertRaisesMessage(ValidationError, "matching schedules"):
            require_confirmable_meeting_faculty(meeting, result=result, result_revision=revision)

    def assert_original_path_guard(self, guard):
        unassign = self.later_unassignment
        with patch.object(self, "later_unassignment", side_effect=lambda assignment, **kwargs:
                unassign(assignment, original_path=True, **kwargs)):
            guard()

    def test_original_path_after_unassignment_has_no_historical_fallback(self):
        self.assert_original_path_guard(self.test_saved_exception_after_unassignment_has_no_historical_fallback)

    def test_original_path_does_not_bypass_on_or_before_class_change(self):
        self.assert_original_path_guard(self.test_saved_exception_does_not_bypass_on_or_before_class_change)

    def test_original_path_does_not_bypass_pending_reconciliation(self):
        self.assert_original_path_guard(self.test_saved_exception_does_not_bypass_pending_reconciliation)

    def test_original_path_does_not_bypass_conflicting_ownership(self):
        self.assert_original_path_guard(self.test_saved_exception_does_not_bypass_conflicting_ownership)

    def test_original_path_does_not_bypass_foreign_scope(self):
        self.assert_original_path_guard(self.test_saved_exception_does_not_bypass_foreign_scope)

    def test_original_path_requires_matching_current_revision(self):
        self.assert_original_path_guard(self.test_saved_exception_requires_matching_current_revision)

    def test_original_path_readiness_reuses_batched_evidence_without_queries(self):
        self.assert_original_path_guard(self.test_saved_exception_readiness_reuses_batched_evidence_without_queries)

    def test_original_path_does_not_bypass_retirement_or_source_waiting(self):
        self.assert_original_path_guard(self.test_saved_exception_does_not_bypass_retirement_or_source_waiting)

    def test_original_path_requires_correlated_closure_audit(self):
        from datetime import timedelta
        assignment, _, _, _ = self.saved_late_case()
        change = self.later_unassignment(assignment, original_path=True)
        coverage = FacultyCoverage.objects.get()
        audit = AuditLog.objects.get(entity_type="FacultyCoverage", entity_id=str(coverage.pk),
            action="FACULTY_ATTENDANCE_COVERAGE_CLOSED")
        self.assertTrue(self.slice().ready)
        for field, invalid in (("after_json", {}), ("after_json", []),
                ("after_json", {"effective_until": "invalid"}),
                ("after_json", {"effective_until": "2026-10-10T00:00:00"}),
                ("after_json", {"effective_until": self.aware(2026, 10, 11).isoformat()}),
                ("tenant_id", None), ("campus_id", None), ("actor_user_id", self.replacement.pk),
                ("created_at", change.created_at - timedelta(seconds=1)),
                ("created_at", change.resolved_at + timedelta(seconds=1))):
            with self.subTest(audit_field=field, invalid=invalid):
                original = getattr(audit, field)
                AuditLog.objects.filter(pk=audit.pk).update(**{field: invalid})
                self.assertFalse(self.slice().ready)
                AuditLog.objects.filter(pk=audit.pk).update(**{field: original})
                self.assertTrue(self.slice().ready)
        for field, invalid in (("source_assignment_id", None), ("prior_faculty_id", self.replacement.pk),
                ("proposed_faculty_id", self.replacement.pk), ("offering_id", self.combined_offering.pk),
                ("event_type", "ASSIGNMENT_CREATED"), ("resolved_by_id", None), ("resolved_at", None)):
            with self.subTest(reconciliation_field=field):
                original = getattr(change, field)
                CoverageReconciliation.objects.filter(pk=change.pk).update(**{field: invalid})
                self.assertFalse(self.slice().ready)
                CoverageReconciliation.objects.filter(pk=change.pk).update(**{field: original})
                self.assertTrue(self.slice().ready)
        # A genuine correlated close does not excuse a manual activation/sync,
        # nor another close bearing the same tag but unrelated audit evidence.
        for tag in ("FACULTY_ATTENDANCE_COVERAGE_CREATED", "FACULTY_ATTENDANCE_ACADEMIC_COVERAGE_SYNCED",
                "FACULTY_ATTENDANCE_COVERAGE_CLOSED"):
            with self.subTest(extra_origin=tag):
                extra = AuditLog.objects.create(entity_type="FacultyCoverage", entity_id=str(coverage.pk),
                    tenant=self.tenant, campus=self.campus, actor_user=self.actor, action=tag,
                    after_json={"effective_until": self.aware(2026, 10, 11).isoformat()})
                self.assertFalse(self.slice().ready)
                extra.delete()
                self.assertTrue(self.slice().ready)

    def test_genuine_boundaries_conflicts_and_end_remain_binding(self):
        assignment, _, meeting = self.case()
        coverage = FacultyCoverage.objects.get()
        AuditLog.objects.create(entity_type="FacultyCoverage", entity_id=str(coverage.pk),
            tenant=self.tenant, campus=self.campus, action="FACULTY_ATTENDANCE_COVERAGE_CREATED")
        self.assertIsNone(resolve_occurrence_faculty(self.occurrences()[0]))
        self.assertEqual(resolve_occurrence_faculty(self.occurrences(day=date(2026, 10, 6))[0]).pk, self.faculty.pk)
        AuditLog.objects.filter(entity_type="FacultyCoverage").delete()
        change = CoverageReconciliation.objects.create(tenant=self.tenant, campus=self.campus, department=self.department,
            offering=self.offering, source_assignment=assignment, event_type="ASSIGNMENT_CREATED", effective_at=self.aware(2026, 10, 2),
            status="RESOLVED", proposed_faculty=self.faculty, created_by=self.actor, source_reference="genuine-start")
        self.assertIsNone(resolve_occurrence_faculty(self.occurrences()[0]))
        change.delete()
        FacultyCoverage.objects.filter(pk=coverage.pk).update(faculty_user=self.replacement)
        self.assertIsNone(resolve_occurrence_faculty(self.occurrences()[0]))
        FacultyCoverage.objects.filter(pk=coverage.pk).update(faculty_user=self.faculty)
        evidence = AssignmentEvidence([self.offering])
        self.assertIsNone(evidence.at(self.offering, self.aware(2026, 10, 31)))
        self.assertIsNone(evidence.at(self.offering, self.aware(2026, 7, 1)))

    def test_initialized_coverage_has_explicit_supplementary_origin(self):
        assignment, _, meeting = self.case(coverage=False)
        scope = dict(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, effective_from=self.aware(2026, 10, 2))
        plan = CoverageInitializationService.preview(**scope)
        CoverageInitializationService.apply(**scope, fingerprint=plan["fingerprint"], recover=False, confirmed=True)
        self.assertTrue(AuditLog.objects.filter(action=INITIALIZED).exists())
        self.assertEqual(require_confirmable_meeting_faculty(meeting).pk, self.faculty.pk)
        self.assertEqual(FacultyCoverage.objects.get().effective_until, self.aware(2026, 10, 31))

    def test_legacy_combined_sections_count_once(self):
        from .services import MeetingService, ScheduleService
        self.case(materialize=False)
        self.case(offering=self.combined_offering, materialize=False)
        version = ScheduleService.create_version(actor=self.actor, offering=self.offering, effective_from=self.term.start_date)
        slot = version.slots.get(weekday=3)
        meeting = MeetingService.generate(actor=self.actor, schedule_slot=slot, meeting_date=date(2026, 10, 1),
            offerings=[self.combined_offering])
        self.assertEqual(require_confirmable_meeting_faculty(meeting).pk, self.faculty.pk)
        self.assertEqual(len(self.occurrences([self.offering, self.combined_offering])), 1)
        publication = self.publish()
        self.assertEqual(publication.entries.count(), 1)
        final = self.final(publication)
        self.assertEqual(final.snapshot["net_payable_hours"], "1.50")
        self.assertEqual(final.snapshot["class_count"], 1)
        self.assertEqual(TeachingMeeting.objects.count(), 1)

    def test_legacy_closure_remains_effective(self):
        from .closures import save_closure
        _, _, meeting = self.case()
        closure = save_closure(actor=self.actor, meeting=meeting, status="CLOSED", kind="HOLIDAY",
            pay_basis="PART_TIME", reason="Existing dated holiday", expected_revision=0)
        publication = self.publish()
        self.assertEqual(publication.entries.get().closure_decision_id, closure.pk)
        self.assertEqual(self.final(publication).snapshot["net_payable_hours"], "0.00")

    def test_unassigned_wrong_scope_and_direct_deny(self):
        assignment, round_, meeting = self.case()
        self.assertIsNone(AssignmentEvidence([self.combined_offering]).at(self.combined_offering, meeting.starts_at))
        FacultyAssignment.objects.filter(pk=assignment.pk).update(tenant=None, campus=None)
        type(self.campus).objects.create(tenant=self.tenant, code="FOREIGN", name="Other campus")
        foreign_campus = type(self.campus).objects.get(code="FOREIGN")
        FacultyAssignment.objects.filter(pk=assignment.pk).update(campus=foreign_campus)
        self.assertIsNone(resolve_occurrence_faculty(self.occurrences()[0]))
        FacultyAssignment.objects.filter(pk=assignment.pk).update(campus=None)
        url = reverse("faculty_attendance:round", args=[round_.public_id])
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=ENCODE_PERMISSION), grant_type="DENY")
        self.assertEqual(self.client.get(url).status_code, 403)
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=PUBLISH_FACULTY_PERMISSION), grant_type="DENY")
        with self.assertRaises(PermissionDenied):
            self.publish()

    def test_render_pair_artifact(self):
        type(self.course).objects.filter(pk=self.course.pk).update(code="AE212-BLR", title="AE212-BLR")
        type(self.section).objects.filter(pk=self.section.pk).update(code="BSA_2A", name="BSA 2-BSA_2A")
        gil_course = type(self.course).objects.create(tenant=self.tenant, campus=self.campus,
            department=self.department, code="GE105-MATH", title="GE105-MATH")
        type(self.combined_offering).objects.filter(pk=self.combined_offering.pk).update(course=gil_course)
        type(self.section2).objects.filter(pk=self.section2.pk).update(code="BSA_1A", name="BSA 1-BSA_1A")
        _, round_, _ = self.case()
        # Use another department-neutral linked offering with Gil's distinct time.
        self.case(gil=True, offering=self.combined_offering, faculty=self.replacement)
        round_ = CheckingRoundService.create(actor=self.actor, meetings=list(TeachingMeeting.objects.all()),
            academic_year=self.academic_year, term=self.term, checking_date=date(2026, 10, 1))
        response = self.client.get(reverse("faculty_attendance:round", args=[round_.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["rows"]), 2)
        self.assertContains(response, "Ramirez, Joy Preachie R.")
        self.assertContains(response, "Chavez, Gil")
        destination = os.environ.get("TMP_LEGACY_RENDER_PATH")
        if destination:
            Path(destination).write_bytes(response.content)

    def test_baseline_date_scan_uses_shared_evidence_without_per_date_queries(self):
        self.case(materialize=False)
        offering = type(self.offering).objects.get(pk=self.offering.pk)
        evidence = AssignmentEvidence([offering])
        with self.assertNumQueries(0):
            for day in range(1, 30):
                self.assertEqual(evidence.linked_at([offering], self.aware(2026, 9, day, 19)).pk, self.faculty.pk)

    def test_legacy_term_monitoring_uses_same_owner_and_normal_hours(self):
        from .monitoring import term_summary
        self.case()
        report = term_summary(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, as_of=date(2026, 10, 1))
        row = next(r for r in report["rows"] if r["faculty"].pk == self.faculty.pk)
        detail = next(d for d in row["details"] if d["date"] == date(2026, 10, 1))
        self.assertEqual(str(detail["actual"]), "1.5")
        self.assertTrue(detail["normal_present"])
        self.assertIsNone(row["latest_verified"])
