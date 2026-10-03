from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.db.models.deletion import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.auditlog.models import AuditLog
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.academics.services import FacultyAssignmentSafetyService
from apps.core.services.features import FeatureSettingsService
from apps.core.services.menu import MenuService
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, SystemSetting, Tenant

from .models import (
    FacultyCoverage,
    AttendanceResult,
    AttendanceResultRevision,
    AttendanceObservation,
    AttendanceCutoffPublication,
    AttendanceCutoffPublicationEntry,
    AttendanceClosureDecision,
    DTRAdjustment,
    DTRMixedFindingDecision,
    FacultyDTR,
    CheckingRound,
    CoverageReconciliation,
    MeetingReconciliation,
    MeetingSubstitution,
    MonthlyChecklistArrangement,
    RecurringCombinedClass,
    ScheduleSlot,
    ScheduleVersion,
    TeachingMeeting,
)
from . import forms as attendance_forms
from .permissions import (
    MANAGE_COVERAGE_PERMISSION,
    MANAGE_MEETINGS_PERMISSION,
    MANAGE_SCHEDULES_PERMISSION,
    MANAGE_SUBSTITUTIONS_PERMISSION,
    RECONCILE_PERMISSION,
    ENCODE_PERMISSION,
    CORRECT_PERMISSION,
    MANAGE_ROUTES_PERMISSION,
    PRINT_PERMISSION,
    PUBLISH_PERMISSION,
    VIEW_PERMISSION,
    DTR_VIEW_PERMISSION,
    DTR_EDIT_PERMISSION,
    DTR_FINALIZE_PERMISSION,
    DTR_PRINT_PERMISSION,
    DTR_AC_SUMMARY_PERMISSION,
    can_faculty_view_own_attendance,
)
from .schedule_parsing import parse_schedule_text
from .selectors import (
    coverage_history,
    date_range_results,
    faculty_meeting_history,
    monthly_tardiness_summary,
    unresolved_meetings,
)
from .services import CoverageService, MeetingService, ReconciliationService, ScheduleService, SubstitutionService
from .academic_integration import AcademicCoverageIntegrationService, AcademicOfferingSourceIntegrationService
from .cutoffs import faculty_published_entries, publish_cutoff, review_cutoff
from .closures import save_closure
from .dtr_intervals import save_mixed_decision
from .daily_encoding import expected_daily_occurrences, prepare_daily_encoding
from .observations import (
    AttendanceResultService,
    CheckingRoundService,
    ObservationService,
    StaleAttendanceReview,
)
from .route_services import SavedRouteService, ordered_meetings
from .monthly_checklists import MonthlyArrangementService, build_monthly_rows, month_dates
from .combined_classes import RecurringCombinedClassService
from .dtr import (
    ac_department_summary, calculate_hour_totals, checker_summary, current_adjustments,
    faculty_final_dtr, finalize_dtr, preview_dtr, printable_final_snapshot, save_adjustment,
)


class ScheduleParsingTests(SimpleTestCase):
    def test_checklist_days_render_as_checkboxes_without_text_input_styling(self):
        from .forms import MonthlyChecklistForm
        html = str(MonthlyChecklistForm()["weekdays"])
        self.assertEqual(html.count('type="checkbox"'), 7)
        self.assertNotIn('class="form-control"', html)
        self.assertIn('class="form-check-input"', html)
        self.assertEqual(html.count(" checked"), 2)

    def test_arrangement_form_preserves_multiday_tokens_and_rejects_bad_payload(self):
        from .forms import MonthlyArrangementForm
        base = {"academic_year_id": 1, "term_id": 1, "month": "2026-09", "day_group": "D41"}
        token = "1:d0,6@0800-0900~u1"
        form = MonthlyArrangementForm({**base, "row_tokens": '["' + token + '"]'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["row_tokens"], [token])
        for payload in ('[1]', '["same", "same"]', '['):
            form = MonthlyArrangementForm({**base, "row_tokens": payload})
            self.assertFalse(form.is_valid())

    def test_recognizes_compact_and_named_multi_meeting_formats(self):
        compact = parse_schedule_text("MWF 8:00 AM-9:00 AM")
        named = parse_schedule_text("Mon/Wed 08:00-09:30; Fri 10:00-11:00")
        self.assertTrue(compact.confirmed)
        self.assertEqual([slot.weekday for slot in compact.slots], [0, 2, 4])
        self.assertTrue(named.confirmed)
        self.assertEqual(len(named.slots), 3)

    def test_rejects_ambiguous_or_invalid_formats(self):
        for value in (None, "TBA", "M 8:00-9:00 AM", "TT 08:00-09:00", "M 09:00-08:00"):
            with self.subTest(value=value):
                self.assertFalse(parse_schedule_text(value).confirmed)

    def test_recognizes_positional_slash_formats_and_whitespace(self):
        parsed = parse_schedule_text("  T/TH\n02:30PM-04:00PM / 03:00PM-04:30PM  ")
        self.assertTrue(parsed.confirmed)
        self.assertEqual(
            [(slot.weekday, slot.start_time, slot.end_time) for slot in parsed.slots],
            [(1, time(14, 30), time(16)), (3, time(15), time(16, 30))],
        )
        self.assertTrue(parse_schedule_text("S 08:00AM-11:00AM").confirmed)
        for value in (
            "M/W 10:30AM-12:00PM/10:30AM-12:00PM",
            "T/TH 02:30PM-04:00PM/02:30PM-04:00PM",
            "T/TH 01:00PM-02:30PM/01:00PM-02:30PM",
        ):
            with self.subTest(value=value):
                self.assertTrue(parse_schedule_text(value).confirmed)

    def test_rejects_positional_day_time_count_mismatch(self):
        parsed = parse_schedule_text("M/W 10:30AM-12:00PM/10:30AM-12:00PM/01:00PM-02:00PM")
        self.assertFalse(parsed.confirmed)
        self.assertIn("counts do not match", parsed.reason)


class FacultyAttendanceFoundationTests(TestCase):
    permission_codes = (
        VIEW_PERMISSION,
        MANAGE_SCHEDULES_PERMISSION,
        MANAGE_COVERAGE_PERMISSION,
        MANAGE_MEETINGS_PERMISSION,
        MANAGE_SUBSTITUTIONS_PERMISSION,
        RECONCILE_PERMISSION,
        ENCODE_PERMISSION,
        CORRECT_PERMISSION,
        MANAGE_ROUTES_PERMISSION,
        PRINT_PERMISSION,
        PUBLISH_PERMISSION,
        DTR_VIEW_PERMISSION,
        DTR_EDIT_PERMISSION,
        DTR_FINALIZE_PERMISSION,
        DTR_PRINT_PERMISSION,
        DTR_AC_SUMMARY_PERMISSION,
    )

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="T1", name="Tenant 1")
        cls.campus = Campus.objects.create(tenant=cls.tenant, code="C1", name="Campus 1")
        cls.department = Department.objects.create(
            tenant=cls.tenant, campus=cls.campus, code="D1", name="Department 1"
        )
        cls.other_department = Department.objects.create(
            tenant=cls.tenant, campus=cls.campus, code="D2", name="Department 2"
        )
        cls.program = Program.objects.create(
            tenant=cls.tenant, campus=cls.campus, department=cls.department, code="P1", name="Program 1"
        )
        cls.other_program = Program.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.other_department,
            code="P2",
            name="Program 2",
        )
        cls.academic_year = AcademicYear.objects.create(
            tenant=cls.tenant,
            code="2025-2026",
            name="2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        cls.term = Term.objects.create(
            tenant=cls.tenant,
            academic_year=cls.academic_year,
            code="T1",
            name="First Term",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 5, 31),
        )
        cls.course = Course.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            code="MATH101",
            title="Mathematics",
        )
        cls.section = Section.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            program=cls.program,
            code="S1",
            name="Section 1",
        )
        cls.section2 = Section.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            program=cls.program,
            code="S2",
            name="Section 2",
        )
        cls.offering = CourseOffering.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            program=cls.program,
            academic_year=cls.academic_year,
            term=cls.term,
            course=cls.course,
            section=cls.section,
            room="R101",
            schedule_text="M 08:00-09:00",
        )
        cls.combined_offering = CourseOffering.objects.create(
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
            program=cls.program,
            academic_year=cls.academic_year,
            term=cls.term,
            course=cls.course,
            section=cls.section2,
            room="R101",
            schedule_text="M 08:00-09:00",
        )
        cls.actor = User.objects.create_user(
            "checker", "checker@example.test", "x", default_tenant=cls.tenant, default_campus=cls.campus,
            default_department=cls.department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )
        cls.faculty = User.objects.create_user(
            "faculty", "faculty@example.test", "x", default_tenant=cls.tenant, default_campus=cls.campus,
            default_department=cls.department,
        )
        cls.replacement = User.objects.create_user(
            "replacement", "replacement@example.test", "x", default_tenant=cls.tenant, default_campus=cls.campus
        )
        cls.role = Role.objects.create(code="ATTENDANCE_CHECKER_TEST", name="Attendance checker")
        for code in cls.permission_codes:
            permission = Permission.objects.get(code=code)
            RolePermission.objects.create(role=cls.role, permission=permission)
        admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access", defaults={"module": "admin_portal", "action": "access"}
        )
        RolePermission.objects.get_or_create(role=cls.role, permission=admin_access)
        UserRole.objects.create(
            user=cls.actor,
            role=cls.role,
            tenant=cls.tenant,
            campus=cls.campus,
            department=cls.department,
        )

    def setUp(self):
        SystemSetting.objects.create(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_ENABLED_KEY,
            setting_value="true",
            value_type=SystemSetting.ValueType.BOOL,
        )

    def aware(self, year, month, day, hour=0, minute=0):
        return timezone.make_aware(datetime(year, month, day, hour, minute), timezone.get_current_timezone())

    def schedule(self, **kwargs):
        values = {"actor": self.actor, "offering": self.offering, "effective_from": date(2026, 1, 1)}
        values.update(kwargs)
        return ScheduleService.create_version(**values)

    def coverage(self, offering=None, faculty=None, **kwargs):
        values = {
            "actor": self.actor,
            "offering": offering or self.offering,
            "faculty_user": faculty or self.faculty,
            "effective_from": self.aware(2026, 1, 1),
            "reason": "",
        }
        values.update(kwargs)
        return CoverageService.create(**values)

    def meeting(self, offerings=None):
        version = self.schedule()
        self.coverage()
        for offering in offerings or []:
            self.coverage(offering=offering)
        return MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=offerings or [],
        )

    def recurring_combined(self, effective_from=date(2026, 1, 12), effective_until=date(2026, 1, 19)):
        for offering in (self.offering, self.combined_offering):
            FacultyAssignment.objects.get_or_create(
                tenant=self.tenant, campus=self.campus, offering=offering, faculty_user=self.faculty,
                defaults={"is_primary": True},
            )
        return RecurringCombinedClassService.create(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year_id=self.academic_year.pk, term_id=self.term.pk,
            offering_ids=[self.offering.pk, self.combined_offering.pk], weekday=0,
            start_time=time(8), end_time=time(9), effective_from=effective_from,
            effective_until=effective_until, reason="",
        )

    def test_switches_default_off_and_faculty_visibility_cannot_bypass_master(self):
        SystemSetting.objects.filter(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_ENABLED_KEY,
        ).delete()
        SystemSetting.objects.create(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            setting_value="true",
            value_type=SystemSetting.ValueType.BOOL,
        )
        self.assertFalse(FeatureSettingsService.is_faculty_attendance_enabled(tenant_id=self.tenant.pk))
        self.assertFalse(
            FeatureSettingsService.is_faculty_attendance_faculty_visibility_enabled(tenant_id=self.tenant.pk)
        )
        self.assertFalse(
            can_faculty_view_own_attendance(user=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        )

    def test_attendance_navigation_group_obeys_master_switch(self):
        enabled_groups = MenuService.get_menu_tree(
            self.actor,
            portal="ADMIN",
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
        )
        attendance_group = next(row for row in enabled_groups if row["group"].code == "FACULTY_ATTENDANCE")
        labels = {node["item"].code: node["item"].label for node in attendance_group["items"]}
        self.assertNotIn("ATTENDANCE_SETUP", labels)
        self.assertEqual(labels["ATTENDANCE_CHECKLIST"], "Monthly Attendance Checklist")
        self.assertEqual(labels["ATTENDANCE_RECONCILIATION"], "Changes Needing Review")
        SystemSetting.objects.filter(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_ENABLED_KEY,
        ).update(setting_value="false")
        disabled_groups = MenuService.get_menu_tree(
            self.actor,
            portal="ADMIN",
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
        )
        self.assertNotIn("FACULTY_ATTENDANCE", {row["group"].code for row in disabled_groups})

    def test_faculty_visibility_requires_its_own_switch_and_permission(self):
        self.assertFalse(
            can_faculty_view_own_attendance(user=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        )
        SystemSetting.objects.create(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            setting_value="true",
            value_type=SystemSetting.ValueType.BOOL,
        )
        self.assertTrue(
            can_faculty_view_own_attendance(user=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        )

    def test_direct_deny_blocks_service_mutation(self):
        UserPermission.objects.create(
            user=self.actor,
            permission=Permission.objects.get(code=MANAGE_SCHEDULES_PERMISSION),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self.schedule()

    def test_unauthorized_and_wrong_department_roles_are_rejected(self):
        unauthorized = User.objects.create_user("none", "none@example.test", "x")
        with self.assertRaises(PermissionDenied):
            ScheduleService.create_version(
                actor=unauthorized, offering=self.offering, effective_from=date(2026, 1, 1)
            )
        other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.other_department,
            code="SCI101",
            title="Science",
        )
        other_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.other_department,
            program=self.other_program,
            code="OS1",
            name="Other Section",
        )
        other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.other_department,
            program=self.other_program,
            academic_year=self.academic_year,
            term=self.term,
            course=other_course,
            section=other_section,
            schedule_text="M 08:00-09:00",
        )
        with self.assertRaises(PermissionDenied):
            ScheduleService.create_version(
                actor=self.actor, offering=other_offering, effective_from=date(2026, 1, 1)
            )

    def test_ambiguous_schedule_is_saved_as_correction_required(self):
        self.offering.schedule_text = "TBA"
        self.offering.save(update_fields=["schedule_text"])
        version = self.schedule()
        self.assertEqual(version.interpretation_status, ScheduleVersion.InterpretationStatus.CORRECTION_REQUIRED)
        self.assertFalse(version.slots.exists())

    def test_explicit_correction_versions_schedule_and_closes_prior_boundary(self):
        first = self.schedule()
        corrected = ScheduleService.create_version(
            actor=self.actor,
            offering=self.offering,
            effective_from=date(2026, 2, 1),
            corrected_slots=[
                {
                    "weekday": 2,
                    "start_time": time(9),
                    "end_time": time(10, 30),
                    "building": "Main",
                    "floor": "2",
                    "room": "201",
                }
            ],
            correction_reason="",
            supersede_current=True,
        )
        first.refresh_from_db()
        self.assertEqual(first.effective_until, date(2026, 1, 31))
        self.assertEqual(corrected.version_number, 2)
        self.assertEqual(corrected.correction_reason, "")
        self.assertEqual(corrected.slots.get().building, "Main")
        self.assertEqual(self.offering.schedule_text, "M 08:00-09:00")

    def test_explicit_correction_requires_at_least_one_slot(self):
        with self.assertRaises(ValidationError):
            self.schedule(corrected_slots=[], correction_reason="No valid slot supplied")

    def test_schedule_overlap_without_explicit_supersession_is_rejected(self):
        self.schedule()
        with self.assertRaises(ValidationError):
            self.schedule(effective_from=date(2026, 2, 1))

    def test_coverage_half_open_boundaries_gaps_and_overlap(self):
        boundary = self.aware(2026, 2, 1)
        first = self.coverage(effective_until=boundary)
        second = self.coverage(
            faculty=self.replacement,
            effective_from=boundary,
            reason="Adjacent replacement",
        )
        self.assertEqual(first.reason, "")
        self.assertEqual(first.effective_until, second.effective_from)
        with self.assertRaises(ValidationError):
            self.coverage(
                faculty=self.actor,
                effective_from=self.aware(2026, 1, 15),
                effective_until=self.aware(2026, 1, 20),
                reason="Conflicting overlap",
            )

    def test_cross_tenant_faculty_coverage_is_rejected(self):
        other_tenant = Tenant.objects.create(code="FOREIGN", name="Foreign Tenant")
        other_campus = Campus.objects.create(tenant=other_tenant, code="FC", name="Foreign Campus")
        foreign_faculty = User.objects.create_user(
            "foreign", "foreign@example.test", "x", default_tenant=other_tenant, default_campus=other_campus
        )
        with self.assertRaises(ValidationError):
            self.coverage(faculty=foreign_faculty)

    def test_assignment_acceptance_is_not_used_as_coverage_start(self):
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=self.aware(2025, 12, 1),
        )
        coverage = self.coverage(source_assignment=assignment, effective_from=self.aware(2026, 1, 10))
        self.assertEqual(coverage.effective_from, self.aware(2026, 1, 10))
        self.assertNotEqual(coverage.effective_from, assignment.accepted_at)

    def test_unassigned_gap_generates_discoverable_unresolved_meeting(self):
        version = self.schedule()
        meeting = MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[],
        )
        self.assertTrue(meeting.unresolved_coverage)
        self.assertIsNone(meeting.faculty_user_id)
        self.assertEqual(list(unresolved_meetings(tenant_id=self.tenant.pk)), [meeting])

    def test_repeated_generation_is_idempotent_and_conflicting_combination_is_rejected(self):
        meeting = self.meeting()
        repeated = MeetingService.generate(
            actor=self.actor,
            schedule_slot=meeting.schedule_slot,
            meeting_date=meeting.meeting_date,
            offerings=[],
        )
        self.assertEqual(repeated.pk, meeting.pk)
        with self.assertRaises(ValidationError):
            MeetingService.generate(
                actor=self.actor,
                schedule_slot=meeting.schedule_slot,
                meeting_date=meeting.meeting_date,
                offerings=[self.combined_offering],
            )

    def test_explicit_combined_sections_share_one_meeting_and_do_not_auto_merge(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        self.assertEqual(meeting.offering_links.count(), 2)
        self.assertEqual({item["section_code"] for item in meeting.sections_snapshot}, {"S1", "S2"})
        self.assertEqual(TeachingMeeting.objects.count(), 1)

    def test_cross_tenant_combined_offering_is_rejected(self):
        version = self.schedule()
        other_tenant = Tenant.objects.create(code="T2", name="Tenant 2")
        other_campus = Campus.objects.create(tenant=other_tenant, code="C2", name="Campus 2")
        other_department = Department.objects.create(
            tenant=other_tenant, campus=other_campus, code="D2", name="Other Department"
        )
        other_program = Program.objects.create(
            tenant=other_tenant, campus=other_campus, department=other_department, code="P2", name="Other"
        )
        other_year = AcademicYear.objects.create(
            tenant=other_tenant,
            code="2025-2026",
            name="2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        other_term = Term.objects.create(
            tenant=other_tenant, academic_year=other_year, code="T1", name="First Term"
        )
        other_course = Course.objects.create(tenant=other_tenant, code="X", title="Other")
        other_section = Section.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            code="X",
            name="Other",
        )
        cross_scope = CourseOffering.objects.create(
            tenant=other_tenant,
            campus=other_campus,
            department=other_department,
            program=other_program,
            academic_year=other_year,
            term=other_term,
            course=other_course,
            section=other_section,
        )
        with self.assertRaises(ValidationError):
            MeetingService.generate(
                actor=self.actor,
                schedule_slot=version.slots.get(),
                meeting_date=date(2026, 1, 5),
                offerings=[cross_scope],
            )

    def test_substitution_is_meeting_specific_and_preserves_permanent_coverage(self):
        meeting = self.meeting()
        original_coverage_id = meeting.coverage_id
        substitution = SubstitutionService.assign(
            actor=self.actor,
            meeting=meeting,
            substitute_faculty=self.replacement,
            reason="",
        )
        meeting.refresh_from_db()
        self.assertEqual(meeting.coverage_id, original_coverage_id)
        self.assertEqual(meeting.faculty_user_id, self.faculty.pk)
        self.assertEqual(substitution.substitute_faculty_id, self.replacement.pk)
        self.assertEqual(substitution.reason, "")
        self.assertEqual(MeetingSubstitution.objects.count(), 1)

    def test_inactive_faculty_and_assignment_remain_historically_queryable_and_protected(self):
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            is_active=False,
        )
        self.schedule()
        coverage = self.coverage(source_assignment=assignment)
        meeting = MeetingService.generate(
            actor=self.actor,
            schedule_slot=ScheduleVersion.objects.get().slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[],
        )
        self.faculty.is_active = False
        self.faculty.save(update_fields=["is_active"])
        self.assertEqual(list(coverage_history(tenant_id=self.tenant.pk, faculty_user_id=self.faculty.pk)), [coverage])
        self.assertEqual(list(faculty_meeting_history(tenant_id=self.tenant.pk, faculty_user_id=self.faculty.pk)), [meeting])
        with self.assertRaises(ProtectedError):
            coverage.delete()

    def test_backdated_coverage_requires_reconciliation_and_preserves_snapshot(self):
        meeting = self.meeting()
        old_snapshot = dict(meeting.faculty_snapshot)
        replacement_coverage = self.coverage(
            faculty=self.replacement,
            effective_from=meeting.starts_at,
            reason="Backdated replacement approved for review",
            supersede_current=True,
        )
        meeting.refresh_from_db()
        reconciliation = MeetingReconciliation.objects.get(
            meeting=meeting,
            source_reference=f"coverage:{replacement_coverage.pk}",
        )
        self.assertEqual(meeting.faculty_user_id, self.faculty.pk)
        self.assertEqual(meeting.faculty_snapshot, old_snapshot)
        self.assertEqual(reconciliation.status, MeetingReconciliation.Status.PENDING)
        ReconciliationService.resolve(
            actor=self.actor,
            reconciliation=reconciliation,
            decision=MeetingReconciliation.Decision.KEEP_SNAPSHOT,
            reason="",
        )
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, MeetingReconciliation.Status.RESOLVED)
        self.assertEqual(reconciliation.resolution_reason, "")

    def test_backdated_coverage_does_not_silently_rewrite_existing_attendance_attribution(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        AttendanceResultService.confirm_present(
            actor=self.actor,
            checking_round=checking_round,
            manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
        )
        result = AttendanceResult.objects.get(meeting=meeting)

        replacement_coverage = self.coverage(
            faculty=self.replacement,
            effective_from=meeting.starts_at,
            reason="Backdated reassignment after attendance encoding",
            supersede_current=True,
        )
        reconciliation = MeetingReconciliation.objects.get(
            meeting=meeting,
            source_reference=f"coverage:{replacement_coverage.pk}",
        )
        result.refresh_from_db()
        self.assertEqual(result.faculty_user_id, self.faculty.pk)

        ReconciliationService.resolve(
            actor=self.actor,
            reconciliation=reconciliation,
            decision=MeetingReconciliation.Decision.MANUAL_CORRECTION,
            reason="",
        )
        AttendanceResultService.reconcile_attribution(
            actor=self.actor,
            result=result,
            expected_revision=result.revision,
            faculty_user=self.replacement,
            reason="",
        )
        result.refresh_from_db()
        meeting.refresh_from_db()
        self.assertEqual(result.faculty_user_id, self.replacement.pk)
        self.assertEqual(result.revision, 2)
        self.assertEqual(result.history.count(), 2)
        self.assertEqual(meeting.faculty_user_id, self.faculty.pk)

    def test_schedule_supersession_detects_impacted_meeting_without_rewriting_it(self):
        meeting = self.meeting()
        old_snapshot = dict(meeting.schedule_snapshot)
        version = ScheduleService.create_version(
            actor=self.actor,
            offering=self.offering,
            effective_from=meeting.meeting_date,
            corrected_slots=[{"weekday": 0, "start_time": time(10), "end_time": time(11)}],
            correction_reason="Backdated corrected time",
            supersede_current=True,
        )
        meeting.refresh_from_db()
        self.assertEqual(meeting.schedule_snapshot, old_snapshot)
        self.assertTrue(
            MeetingReconciliation.objects.filter(
                meeting=meeting, source_reference=f"schedule-version:{version.pk}"
            ).exists()
        )

    def test_academic_permanent_replacement_without_boundary_rejects_before_mutation(self):
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            is_primary=True,
        )
        with self.assertRaisesMessage(ValidationError, "Effective from"):
            FacultyAssignmentSafetyService.process_replacement(
                assignments=[assignment], replacement_faculty=self.replacement,
                replacement_type="PERMANENT", reason_category="RESIGNATION",
                remarks="Approved permanent replacement", processed_by_user=self.actor)
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)
        self.assertFalse(CoverageReconciliation.objects.exists())
        self.assertFalse(FacultyAssignment.objects.filter(faculty_user=self.replacement).exists())

    def test_academic_replacement_with_authorized_boundary_splits_coverage(self):
        permission, _ = Permission.objects.get_or_create(code="faculty_replacement.process",
            defaults={"module": "faculty_replacement", "action": "process"})
        RolePermission.objects.get_or_create(role=self.role, permission=permission)
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            is_primary=True,
        )
        outgoing = self.coverage(source_assignment=assignment)
        boundary = self.aware(2026, 2, 1, 8)
        logs = FacultyAssignmentSafetyService.process_replacement(
            assignments=[assignment],
            replacement_faculty=self.replacement,
            replacement_type="PERMANENT",
            reason_category="RESIGNATION",
            remarks="Approved effective replacement",
            processed_by_user=self.actor,
            attendance_effective_at=boundary,
        )
        outgoing.refresh_from_db()
        reconciliation = CoverageReconciliation.objects.get(source_reference=f"faculty-replacement:{logs[0].pk}")
        self.assertEqual(outgoing.effective_until, boundary)
        self.assertEqual(reconciliation.status, CoverageReconciliation.Status.RESOLVED)
        self.assertTrue(
            FacultyCoverage.objects.filter(
                offering=self.offering,
                faculty_user=self.replacement,
                effective_from=boundary,
            ).exists()
        )

    def test_unassignment_resolution_creates_explicit_gap_without_absence(self):
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
        )
        coverage = self.coverage(source_assignment=assignment)
        boundary = self.aware(2026, 2, 1)
        reconciliation = AcademicCoverageIntegrationService.record_assignment_event(
            actor=self.actor,
            offering=self.offering,
            event_type=CoverageReconciliation.EventType.UNASSIGNMENT,
            source_reference="test-unassignment-gap",
            reason="Approved unassignment",
            source_assignment=assignment,
            prior_faculty=self.faculty,
            effective_at=boundary,
        )
        AcademicCoverageIntegrationService.resolve(
            actor=self.actor,
            reconciliation=reconciliation,
            effective_at=boundary,
            reason="Coverage ends at approved boundary",
        )
        coverage.refresh_from_db()
        self.assertEqual(coverage.effective_until, boundary)
        self.assertFalse(FacultyCoverage.objects.filter(offering=self.offering, effective_from__gte=boundary).exists())
        self.assertFalse(AttendanceResult.objects.exists())

    def test_module_off_academic_replacement_preserves_behavior_without_attendance_rows(self):
        SystemSetting.objects.filter(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_ENABLED_KEY,
        ).update(setting_value="false")
        assignment = FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            is_primary=True,
        )
        FacultyAssignmentSafetyService.process_replacement(
            assignments=[assignment],
            replacement_faculty=self.replacement,
            replacement_type="PERMANENT",
            reason_category="RESIGNATION",
            remarks="Academic replacement while attendance is disabled",
            processed_by_user=self.actor,
        )
        self.assertFalse(CoverageReconciliation.objects.exists())
        assignment.refresh_from_db()
        self.assertFalse(assignment.is_active)

    def test_checking_round_freezes_manifest_and_does_not_reset_current_result(self):
        meeting = self.meeting()
        first_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date, label="First route"
        )
        AttendanceResultService.confirm_present(
            actor=self.actor,
            checking_round=first_round,
            manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
        )
        result = AttendanceResult.objects.get(meeting=meeting)
        second_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date, label="Second route"
        )
        result.refresh_from_db()
        self.assertEqual((result.status, result.revision), (AttendanceResult.Status.PRESENT, 1))
        self.assertEqual(second_round.manifest_rows.get().reviewed_result_revision, 1)

    def test_saved_route_reorders_recurring_slots_and_round_freezes_order_after_later_edit(self):
        first = self.meeting()
        second_version = ScheduleService.create_version(
            actor=self.actor,
            offering=self.combined_offering,
            effective_from=date(2026, 1, 1),
            corrected_slots=[{
                "weekday": 0,
                "start_time": time(9),
                "end_time": time(10),
                "building": "B",
                "floor": "2",
                "room": "201",
            }],
            correction_reason="Explicit second route stop",
        )
        self.coverage(offering=self.combined_offering)
        second = MeetingService.generate(
            actor=self.actor,
            schedule_slot=second_version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[],
        )
        unmatched_slot = ScheduleSlot.objects.create(
            schedule_version=first.schedule_slot.schedule_version,
            sequence=2,
            weekday=0,
            start_time=time(10),
            end_time=time(11),
            building="C",
            floor="3",
            room="301",
        )
        unmatched = MeetingService.generate(
            actor=self.actor,
            schedule_slot=unmatched_slot,
            meeting_date=date(2026, 1, 5),
            offerings=[],
        )
        route = SavedRouteService.save(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            name="Building route",
            schedule_slot_ids=[second.schedule_slot_id, first.schedule_slot_id],
        )
        ordered = ordered_meetings(
            TeachingMeeting.objects.filter(pk__in=[first.pk, second.pk, unmatched.pk]), route
        )
        self.assertEqual([row.pk for row in ordered], [second.pk, first.pk, unmatched.pk])
        checking_round = CheckingRoundService.create(
            actor=self.actor,
            meetings=ordered,
            checking_date=date(2026, 1, 5),
            saved_route=route,
        )
        SavedRouteService.save(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            name=route.name,
            schedule_slot_ids=[first.schedule_slot_id],
            expected_revision=route.revision,
            route=route,
        )
        self.assertEqual(route.entries.count(), 2)
        self.assertEqual(
            list(checking_round.manifest_rows.order_by("sequence").values_list("meeting_id", flat=True)),
            [second.pk, first.pk, unmatched.pk],
        )
        checking_round.refresh_from_db()
        self.assertEqual(checking_round.route_revision_snapshot, 1)

    def test_saved_route_rejects_duplicate_and_stale_orders(self):
        meeting = self.meeting()
        with self.assertRaises(ValidationError):
            SavedRouteService.save(
                actor=self.actor,
                tenant_id=self.tenant.pk,
                campus_id=self.campus.pk,
                name="Duplicate",
                schedule_slot_ids=[meeting.schedule_slot_id, meeting.schedule_slot_id],
            )
        route = SavedRouteService.save(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            name="Stable",
            schedule_slot_ids=[meeting.schedule_slot_id],
        )
        with self.assertRaises(ValidationError):
            SavedRouteService.save(
                actor=self.actor,
                tenant_id=self.tenant.pk,
                campus_id=self.campus.pk,
                name=route.name,
                schedule_slot_ids=[meeting.schedule_slot_id],
                expected_revision=0,
                route=route,
            )

    def test_checklist_get_is_read_only_and_print_uses_frozen_rows(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        before_rounds = CheckingRound.objects.count()
        self.client.force_login(self.actor)
        preview = self.client.get(
            reverse("faculty_attendance:checklist"),
            {"start_date": "2026-01-05", "end_date": "2026-01-05"},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(CheckingRound.objects.count(), before_rounds)
        printed = self.client.get(reverse("faculty_attendance:print", args=[checking_round.public_id]))
        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, self.section.code)
        self.assertContains(printed, self.section2.code)
        self.assertContains(printed, str(checking_round.public_id))

    def test_monthly_checklist_uses_offerings_without_materialized_meetings(self):
        self.offering.schedule_text = "M/W 10:30AM-12:00PM/10:30AM-12:00PM"
        self.offering.save(update_fields=["schedule_text"])
        FacultyAssignment.objects.create(
            tenant=self.tenant, campus=self.campus, offering=self.offering,
            faculty_user=self.faculty, is_primary=True,
        )
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:checklist"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "month": "2026-01", "day_group": "MW",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monthly Attendance Checklist")
        self.assertContains(response, "admin-shell")
        self.assertContains(response, "Dashboard")
        self.assertContains(response, self.course.code)
        self.assertContains(response, "Save Arrangement")
        self.assertContains(response, "Print Checklist")
        self.assertContains(response, "Daily Attendance Encoding")
        self.assertContains(response, "Open Daily Attendance Encoding")
        self.assertFalse(TeachingMeeting.objects.exists())

    def test_selected_weekdays_include_sunday_and_exclude_other_dates(self):
        self.offering.schedule_text = "M/W/SUN 08:00-09:00"
        self.offering.save(update_fields=["schedule_text"])
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:checklist"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "month": "2026-09", "weekdays": ["6", "0", "0"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_day_group"], "D41")
        self.assertTrue(response.context["rows"])
        self.assertEqual({d.weekday() for d in response.context["date_columns"]}, {0, 6})
        self.assertTrue(all(d.weekday() in {0, 6} for row in response.context["rows"] for d in row.applicable_dates))
        self.assertFalse(TeachingMeeting.objects.exists())

    def test_selected_legacy_weekdays_reuse_saved_arrangement_and_revision(self):
        self.offering.schedule_text = "M/W 08:00-09:00"
        self.offering.save(update_fields=["schedule_text"])
        arrangement = MonthlyArrangementService.save(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year_id=self.academic_year.pk, term_id=self.term.pk,
            day_group="MW", tokens=[], expected_revision=None,
        )
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:checklist"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "month": "2026-09", "weekdays": ["2", "0"],
        })
        self.assertEqual(response.context["arrangement"].pk, arrangement.pk)
        self.assertEqual(response.context["selected_day_group"], "MW")
        self.assertEqual(response.context["arrangement"].revision, 1)
        custom = MonthlyArrangementService.save(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year_id=self.academic_year.pk, term_id=self.term.pk,
            day_group="D01", tokens=[], expected_revision=None,
        )
        self.assertNotEqual(custom.pk, arrangement.pk)
        with self.assertRaises(ValidationError):
            MonthlyArrangementService.save(
                actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                academic_year_id=self.academic_year.pk, term_id=self.term.pk,
                day_group="D01", tokens=[], expected_revision=2,
            )

    def test_invalid_weekday_selection_retains_scope_and_does_not_build_rows(self):
        self.client.force_login(self.actor)
        for days in ([], ["7"]):
            response = self.client.get(reverse("faculty_attendance:checklist"), {
                "academic_year": self.academic_year.pk, "term": self.term.pk,
                "month": "2026-09", "weekdays": days,
            })
            self.assertEqual(response.status_code, 200)
            self.assertIn("weekdays", response.context["monthly_form"].errors)
            self.assertEqual(str(response.context["monthly_form"]["academic_year"].value()), str(self.academic_year.pk))
            self.assertEqual(response.context["rows"], [])

    def test_full_week_print_batches_all_dates_without_duplicate_date_columns(self):
        self.offering.schedule_text = "M/T/W/TH/F/S/SUN 08:00-09:00"
        self.offering.save(update_fields=["schedule_text"])
        self.combined_offering.is_active = False
        self.combined_offering.save(update_fields=["is_active"])
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:monthly_print"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "month": "2026-09", "weekdays": [str(d) for d in range(7)],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([len(batch) for batch in response.context["date_batches"]], [12, 12, 6])
        self.assertEqual(len(response.context["date_columns"]), 30)
        for d in response.context["date_columns"]:
            self.assertContains(response, f'<th class="date-cell">{d:%a}<br>Sep {d.day}</th>', count=1, html=True)

    def test_monthly_checklist_excludes_ambiguous_schedule_with_specific_message(self):
        self.offering.schedule_text = "M/W 10:30AM-12:00PM/unknown"
        self.offering.save(update_fields=["schedule_text"])
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:checklist"), {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "month": "2026-01", "day_group": "MW",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correction or review needed")
        self.assertContains(response, "Unsupported or ambiguous schedule format")

    def test_monthly_arrangement_is_scoped_revisioned_and_preserves_academic_schedule(self):
        self.offering.schedule_text = "M/W 10:30AM-12:00PM/10:30AM-12:00PM"
        self.offering.save(update_fields=["schedule_text"])
        self.combined_offering.schedule_text = self.offering.schedule_text
        self.combined_offering.save(update_fields=["schedule_text"])
        token = f"{self.offering.pk}:d0,2@1030-1200"
        other_token = f"{self.combined_offering.pk}:d0,2@1030-1200"
        arrangement = MonthlyArrangementService.save(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year_id=self.academic_year.pk, term_id=self.term.pk,
            day_group="MW", tokens=[token, other_token], expected_revision=None,
        )
        self.assertEqual(arrangement.revision, 1)
        self.assertEqual(arrangement.entries.count(), 2)
        arrangement = MonthlyArrangementService.save(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year_id=self.academic_year.pk, term_id=self.term.pk,
            day_group="MW", tokens=[other_token], expected_revision=1,
        )
        self.assertEqual(arrangement.revision, 2)
        self.assertEqual(
            list(arrangement.entries.order_by("position").values_list("offering_id", flat=True)),
            [self.combined_offering.pk, self.offering.pk],
        )
        self.offering.refresh_from_db()
        self.assertEqual(self.offering.schedule_text, "M/W 10:30AM-12:00PM/10:30AM-12:00PM")
        with self.assertRaises(ValidationError):
            MonthlyArrangementService.save(
                actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                academic_year_id=self.academic_year.pk, term_id=self.term.pk,
                day_group="MW", tokens=[token], expected_revision=1,
            )

    def test_monthly_print_has_selected_dates_and_direct_deny_wins(self):
        # Printable rows now require assignment/dated coverage/substitution evidence.
        FacultyAssignment.objects.create(offering=self.offering, faculty_user=self.faculty, is_primary=True)
        self.offering.schedule_text = "F 08:00AM-11:00AM"
        self.offering.save(update_fields=["schedule_text"])
        self.client.force_login(self.actor)
        query = {"academic_year": self.academic_year.pk, "term": self.term.pk, "month": "2026-01", "day_group": "F"}
        response = self.client.get(reverse("faculty_attendance:monthly_print"), query)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "admin-shell")
        self.assertContains(response, "Back to Monthly Attendance Checklist")
        self.assertContains(response, "<h1>Monthly Attendance Checklist</h1>", html=True)
        self.assertContains(response, self.tenant.name)
        self.assertContains(response, self.campus.name)
        self.assertNotContains(response, "NCBA TeacherMate")
        for day in (2, 9, 16, 23, 30):
            self.assertContains(response, f"Jan {day}")
        UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=PRINT_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        denied = self.client.get(reverse("faculty_attendance:monthly_print"), query)
        self.assertEqual(denied.status_code, 403)
        screen = self.client.get(reverse("faculty_attendance:checklist"), query)
        self.assertEqual(screen.status_code, 200)
        self.assertNotContains(screen, "Print Checklist")

    def test_september_2026_friday_print_dates_are_exact(self):
        self.offering.schedule_text = "F 08:00AM-11:00AM"
        self.offering.save(update_fields=["schedule_text"])
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:monthly_print"), {
            "academic_year": self.academic_year.pk,
            "term": self.term.pk,
            "month": "2026-09",
            "day_group": "F",
        })
        self.assertEqual(response.status_code, 200)
        for day in (4, 11, 18, 25):
            self.assertContains(response, f'<th class="date-cell">Fri<br>Sep {day}</th>', count=1, html=True)
        for day in (3, 5, 10, 12, 17, 19, 24, 26):
            self.assertNotContains(response, f'<th class="date-cell">Fri<br>Sep {day}</th>', html=True)

    def test_nonapplicable_cells_are_na_and_applicable_cells_remain_blank(self):
        self.offering.schedule_text = "M 08:00AM-09:00AM"
        self.offering.save(update_fields=["schedule_text"])
        self.combined_offering.is_active = False
        self.combined_offering.save(update_fields=["is_active"])
        self.client.force_login(self.actor)
        query = {
            "academic_year": self.academic_year.pk,
            "term": self.term.pk,
            "month": "2026-09",
            "day_group": "MW",
        }
        screen = self.client.get(reverse("faculty_attendance:checklist"), query)
        printed = self.client.get(reverse("faculty_attendance:monthly_print"), query)
        self.assertEqual(screen.status_code, 200)
        self.assertEqual(printed.status_code, 200)
        self.assertContains(screen, "Blank, unverified", count=4)
        self.assertContains(screen, '<td class="date-cell not-applicable">N/A</td>', html=True)
        self.assertContains(printed, 'class="date-cell write"')
        self.assertContains(printed, 'class="date-cell na"')
        self.assertContains(printed, "N/A &mdash; this row does not apply on this date.")

    def test_print_permission_does_not_require_arrangement_save_permission(self):
        self.offering.schedule_text = "F 08:00AM-11:00AM"
        self.offering.save(update_fields=["schedule_text"])
        UserPermission.objects.create(
            user=self.actor,
            permission=Permission.objects.get(code=MANAGE_ROUTES_PERMISSION),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.client.force_login(self.actor)
        query = {
            "academic_year": self.academic_year.pk,
            "term": self.term.pk,
            "month": "2026-09",
            "day_group": "F",
        }
        screen = self.client.get(reverse("faculty_attendance:checklist"), query)
        printed = self.client.get(reverse("faculty_attendance:monthly_print"), query)
        self.assertEqual(screen.status_code, 200)
        self.assertNotContains(screen, "Save Arrangement")
        self.assertContains(screen, "Print Checklist")
        self.assertEqual(printed.status_code, 200)

    def test_explicit_recurring_combination_is_one_row_only_on_effective_dates(self):
        group = self.recurring_combined()
        rows, corrections, dates = build_monthly_rows(
            offerings=[self.offering, self.combined_offering], year=2026, month=1,
            day_group="MW", combined_classes=[group],
        )
        combined = [row for row in rows if row.is_combined]
        self.assertEqual(len(combined), 1)
        self.assertEqual({row.pk for row in combined[0].linked_offerings}, {self.offering.pk, self.combined_offering.pk})
        self.assertEqual(combined[0].applicable_dates, frozenset({date(2026, 1, 12), date(2026, 1, 19)}))
        for meeting_date in combined[0].applicable_dates:
            self.assertEqual(sum(meeting_date in row.applicable_dates for row in rows), 1)
        self.assertFalse(corrections)

    def test_monthly_print_shows_combined_sections_once_only_for_effective_dates(self):
        self.recurring_combined()
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:monthly_print"), {
            "academic_year": self.academic_year.pk,
            "term": self.term.pk,
            "month": "2026-01",
            "day_group": "MW",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sections Taught Together", count=1)
        self.assertContains(response, self.section.code)
        self.assertContains(response, self.section2.code)
        self.assertContains(response, "Jan 12")
        self.assertContains(response, "Jan 19")

    def test_matching_faculty_time_room_never_merge_without_definition(self):
        for offering in (self.offering, self.combined_offering):
            FacultyAssignment.objects.create(
                tenant=self.tenant, campus=self.campus, offering=offering,
                faculty_user=self.faculty, is_primary=True,
            )
        rows, _, _ = build_monthly_rows(
            offerings=[self.offering, self.combined_offering], year=2026, month=1, day_group="MW",
        )
        self.assertEqual(len(rows), 2)
        self.assertFalse(any(row.is_combined for row in rows))

    def test_combined_definition_rejects_conflicts_and_overlap(self):
        group = self.recurring_combined()
        self.assertEqual(group.reason, "")
        with self.assertRaisesMessage(ValidationError, "overlapping combined definition"):
            self.recurring_combined(effective_from=date(2026, 1, 19), effective_until=date(2026, 1, 26))
        self.assertEqual(RecurringCombinedClass.objects.count(), 1)
        self.combined_offering.room = "R202"
        self.combined_offering.save(update_fields=["room"])
        with self.assertRaisesMessage(ValidationError, "conflicting rooms"):
            self.recurring_combined()

    def test_combined_definition_direct_deny_wins_and_dated_history_is_unchanged(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        original_snapshot = list(meeting.sections_snapshot)
        UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=MANAGE_MEETINGS_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self.recurring_combined()
        meeting.refresh_from_db()
        self.assertEqual(meeting.sections_snapshot, original_snapshot)
        self.assertEqual(meeting.offering_links.count(), 2)

    def test_combined_definition_rejects_unauthorized_department_and_cross_campus(self):
        other_course = Course.objects.create(
            tenant=self.tenant, campus=self.campus, department=self.other_department,
            code="SCI201", title="Science",
        )
        other_section = Section.objects.create(
            tenant=self.tenant, campus=self.campus, department=self.other_department,
            program=self.other_program, code="D2S1", name="Department 2 Section",
        )
        unauthorized = CourseOffering.objects.create(
            tenant=self.tenant, campus=self.campus, department=self.other_department,
            program=self.other_program, academic_year=self.academic_year, term=self.term,
            course=other_course, section=other_section, room="R101", schedule_text="M 08:00-09:00",
        )
        for offering in (self.offering, unauthorized):
            FacultyAssignment.objects.create(
                tenant=self.tenant, campus=self.campus, offering=offering,
                faculty_user=self.faculty, is_primary=True,
            )
        with self.assertRaises(PermissionDenied):
            RecurringCombinedClassService.create(
                actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                academic_year_id=self.academic_year.pk, term_id=self.term.pk,
                offering_ids=[self.offering.pk, unauthorized.pk], weekday=0,
                start_time=time(8), end_time=time(9), effective_from=date(2026, 1, 1),
                effective_until=None, reason="Should not cross authority",
            )
        other_campus = Campus.objects.create(tenant=self.tenant, code="C2", name="Campus 2")
        other_campus_department = Department.objects.create(
            tenant=self.tenant, campus=other_campus, code="C2D", name="Campus 2 Department",
        )
        other_campus_program = Program.objects.create(
            tenant=self.tenant, campus=other_campus, department=other_campus_department,
            code="C2P", name="Campus 2 Program",
        )
        other_campus_course = Course.objects.create(
            tenant=self.tenant, campus=other_campus, department=other_campus_department,
            code="C2C", title="Campus 2 Course",
        )
        other_campus_section = Section.objects.create(
            tenant=self.tenant, campus=other_campus, department=other_campus_department,
            program=other_campus_program, code="C2S", name="Campus 2 Section",
        )
        cross_campus = CourseOffering.objects.create(
            tenant=self.tenant, campus=other_campus, department=other_campus_department,
            program=other_campus_program, academic_year=self.academic_year, term=self.term,
            course=other_campus_course, section=other_campus_section,
            room="R101", schedule_text="M 08:00-09:00",
        )
        with self.assertRaisesMessage(
            ValidationError,
            "All offerings must be in the selected tenant, campus, academic year, and semester.",
        ):
            RecurringCombinedClassService.create(
                actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                academic_year_id=self.academic_year.pk, term_id=self.term.pk,
                offering_ids=[self.offering.pk, cross_campus.pk], weekday=0,
                start_time=time(8), end_time=time(9), effective_from=date(2026, 1, 1),
                effective_until=None, reason="Should not cross campus",
            )

    def test_dated_combined_meeting_stays_exact_and_does_not_create_recurrence(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        self.offering.schedule_text = "TBA"
        self.combined_offering.schedule_text = "TBA"
        self.offering.save(update_fields=["schedule_text"])
        self.combined_offering.save(update_fields=["schedule_text"])
        rows, _, _ = build_monthly_rows(
            offerings=[self.offering, self.combined_offering], year=2026, month=1,
            day_group="MW", dated_meetings=[meeting],
        )
        combined = [row for row in rows if row.is_combined]
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0].applicable_dates, frozenset({date(2026, 1, 5)}))
        self.assertFalse(RecurringCombinedClass.objects.exists())

    def test_combined_row_saved_order_is_stable_and_stale_save_fails(self):
        group = self.recurring_combined()
        rows, _, _ = build_monthly_rows(
            offerings=[self.offering, self.combined_offering], year=2026, month=1,
            day_group="MW", combined_classes=[group],
        )
        token = next(row.token for row in rows if row.is_combined)
        arrangement = MonthlyArrangementService.save(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year_id=self.academic_year.pk, term_id=self.term.pk,
            day_group="MW", tokens=[token], expected_revision=None,
        )
        self.assertEqual(arrangement.entries.get().pattern_key, token.split(":", 1)[1])
        with self.assertRaisesMessage(ValidationError, "changed in another page"):
            MonthlyArrangementService.save(
                actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                academic_year_id=self.academic_year.pk, term_id=self.term.pk,
                day_group="MW", tokens=[token], expected_revision=0,
            )

    def test_old_setup_redirects_and_secondary_correction_pages_are_read_only(self):
        self.meeting()
        before_versions = ScheduleVersion.objects.count()
        before_coverages = FacultyCoverage.objects.count()
        before_results = AttendanceResult.objects.count()
        self.client.force_login(self.actor)
        setup = self.client.get(
            reverse("faculty_attendance:setup"),
            {"academic_year": self.academic_year.pk, "term": self.term.pk, "month": "2026-09", "day_group": "F"},
        )
        corrections = self.client.get(reverse("faculty_attendance:corrections"))
        reconciliation = self.client.get(reverse("faculty_attendance:reconciliation"))
        self.assertEqual(setup.status_code, 302)
        self.assertEqual(
            setup.url,
            f"{reverse('faculty_attendance:checklist')}?academic_year={self.academic_year.pk}&term={self.term.pk}&month=2026-09&day_group=F",
        )
        self.assertEqual(corrections.status_code, 200)
        self.assertEqual(reconciliation.status_code, 200)
        self.assertContains(corrections, "Attendance Corrections")
        self.assertNotContains(corrections, "Attendance Setup")
        self.assertContains(reconciliation, "Changes Needing Review")
        self.assertNotContains(reconciliation, "materialized meetings")
        self.assertEqual(ScheduleVersion.objects.count(), before_versions)
        self.assertEqual(FacultyCoverage.objects.count(), before_coverages)
        self.assertEqual(AttendanceResult.objects.count(), before_results)

    def test_old_setup_post_is_not_replayed_and_unauthenticated_get_requires_login(self):
        before_versions = ScheduleVersion.objects.count()
        unauthenticated = self.client.get(reverse("faculty_attendance:setup"))
        self.assertEqual(unauthenticated.status_code, 302)
        self.assertIn(reverse("accounts:admin_login"), unauthenticated.url)
        self.client.force_login(self.actor)
        rejected = self.client.post(
            reverse("faculty_attendance:setup"),
            {"action": "schedule", "offering": self.offering.pk},
        )
        self.assertEqual(rejected.status_code, 405)
        self.assertEqual(ScheduleVersion.objects.count(), before_versions)

    def test_schedule_correction_handler_preserves_invalid_entered_values(self):
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("faculty_attendance:corrections"),
            {
                "action": "schedule",
                "offering": self.offering.pk,
                "effective_from": "2026-01-01",
                "weekday": "0",
                "start_time": "10:00",
                "end_time": "09:00",
                "building": "Main Building",
                "floor": "2",
                "room": "M201",
                "correction_reason": "Explicit correction entered by checker",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End time must be later than start time")
        self.assertContains(response, "Main Building")
        self.assertFalse(ScheduleVersion.objects.exists())

    def test_checklist_post_creates_round_with_selected_frozen_order(self):
        meeting = self.meeting()
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("faculty_attendance:checklist"),
            {
                "start_date": "2026-01-05",
                "end_date": "2026-01-05",
                "route": "",
                "label": "Morning paper route",
                "meeting_ids": str(meeting.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        checking_round = CheckingRound.objects.get(label="Morning paper route")
        self.assertEqual(checking_round.manifest_rows.get().meeting_id, meeting.pk)

    def test_print_direct_deny_blocks_frozen_checklist(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        UserPermission.objects.create(
            user=self.actor,
            permission=Permission.objects.get(code=PRINT_PERMISSION),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:print", args=[checking_round.public_id]))
        self.assertEqual(response.status_code, 403)

    def test_faculty_my_attendance_requires_visibility_switch_and_owns_results(self):
        self.coverage()
        self.coverage(self.combined_offering)
        rounds, issues = prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 5),
        )
        self.assertFalse(issues)
        checking_round = rounds[0]
        meetings = list(TeachingMeeting.objects.filter(meeting_date=date(2026, 1, 5)).order_by("pk"))
        AttendanceResultService.confirm_present(
            actor=self.actor,
            checking_round=checking_round,
            manifest_revision=checking_round.manifest_revision,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0} for meeting in meetings],
        )
        faculty_role = Role.objects.create(code="FACULTY_ATTENDANCE_SELF_TEST", name="Faculty self view")
        for code in ("faculty_portal.access", VIEW_PERMISSION):
            permission, _ = Permission.objects.get_or_create(
                code=code, defaults={"module": code.split(".")[0], "action": code.split(".")[-1]}
            )
            RolePermission.objects.get_or_create(role=faculty_role, permission=permission)
        UserRole.objects.create(
            user=self.faculty,
            role=faculty_role,
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
        )
        self.faculty.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.faculty.privacy_consent_at = timezone.now()
        self.faculty.save(update_fields=["privacy_consent_version", "privacy_consent_at"])
        self.client.force_login(self.faculty)
        hidden = self.client.get(reverse("faculty_attendance:my_attendance"))
        self.assertEqual(hidden.status_code, 403)
        SystemSetting.objects.create(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            setting_value="true",
            value_type=SystemSetting.ValueType.BOOL,
        )
        visible = self.client.get(
            reverse("faculty_attendance:my_attendance"),
            {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        )
        self.assertEqual(visible.status_code, 200)
        self.assertContains(visible, "My Attendance")
        self.assertNotContains(visible, self.section.code)
        review = review_cutoff(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            academic_year=self.academic_year,
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )
        publish_cutoff(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            academic_year=self.academic_year,
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            expected_fingerprint=review.fingerprint,
            submission_key="faculty-self-published",
            publication_reason="Checker released complete campus cutoff.",
        )
        visible = self.client.get(
            reverse("faculty_attendance:my_attendance"),
            {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        )
        self.assertContains(visible, self.section.code)

    def test_blank_or_invalid_findings_do_not_become_attendance_results(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        self.assertIsNone(
            ObservationService.record(
                actor=self.actor,
                checking_round=checking_round,
                meeting_id=meeting.pk,
                manifest_revision=1,
                submission_key="blank",
                findings=[],
            )
        )
        with self.assertRaises(ValidationError):
            ObservationService.record(
                actor=self.actor,
                checking_round=checking_round,
                meeting_id=meeting.pk,
                manifest_revision=1,
                submission_key="invalid",
                findings=[{"finding_type": "ABSENCE", "segment_key": "p1"}],
            )
        self.assertFalse(AttendanceResult.objects.exists())

    def test_partial_a_and_n_hour_segments_and_simultaneous_late_early_are_preserved(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        observation = ObservationService.record(
            actor=self.actor,
            checking_round=checking_round,
            meeting_id=meeting.pk,
            manifest_revision=1,
            submission_key="exceptions-v1",
            findings=[
                {"finding_type": "ABSENCE", "segment_key": "first-half", "notice_status": "A", "missed_hours": "0.50"},
                {"finding_type": "ABSENCE", "segment_key": "second-half", "notice_status": "N", "missed_hours": "1.00"},
                {"finding_type": "LATE", "segment_key": "arrival", "minutes": 0},
                {"finding_type": "EARLY", "segment_key": "dismissal", "minutes": 15},
            ],
        )
        result = AttendanceResultService.select_observation(
            actor=self.actor,
            observation=observation,
            expected_revision=0,
            reason="Checker reconciled explicit findings",
        )
        self.assertEqual(result.absent_without_notice_hours, Decimal("0.50"))
        self.assertEqual(result.absent_with_notice_hours, Decimal("1.00"))
        self.assertEqual(result.missed_periods, Decimal("0"))
        self.assertTrue(result.late_flag)
        self.assertEqual(result.late_minutes, 0)
        self.assertTrue(result.early_flag)
        self.assertEqual(result.early_minutes, 15)

    def test_new_absence_service_rejects_period_only_finding(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )

        with self.assertRaisesMessage(ValidationError, "missed periods are legacy-only"):
            ObservationService.record(
                actor=self.actor,
                checking_round=checking_round,
                meeting_id=meeting.pk,
                manifest_revision=1,
                submission_key="new-period-only",
                findings=[{
                    "finding_type": "ABSENCE",
                    "segment_key": "checker-entry",
                    "notice_status": "A",
                    "missed_periods": "1.00",
                }],
            )
        self.assertFalse(AttendanceObservation.objects.exists())

    def test_same_absence_segment_cannot_be_both_a_and_n(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        with self.assertRaisesMessage(ValidationError, "cannot be both A and N"):
            ObservationService.record(
                actor=self.actor,
                checking_round=checking_round,
                meeting_id=meeting.pk,
                manifest_revision=1,
                submission_key="contradiction",
                findings=[
                    {"finding_type": "ABSENCE", "segment_key": "p1", "notice_status": "A", "missed_hours": "0.5"},
                    {"finding_type": "ABSENCE", "segment_key": "p1", "notice_status": "N", "missed_hours": "0.5"},
                ],
            )

    def test_observation_request_is_idempotent_and_conflicting_retry_is_rejected(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        payload = [{"finding_type": "LATE", "segment_key": "arrival", "minutes": 5}]
        first = ObservationService.record(
            actor=self.actor,
            checking_round=checking_round,
            meeting_id=meeting.pk,
            manifest_revision=1,
            submission_key="same-request",
            findings=payload,
        )
        repeated = ObservationService.record(
            actor=self.actor,
            checking_round=checking_round,
            meeting_id=meeting.pk,
            manifest_revision=1,
            submission_key="same-request",
            findings=payload,
        )
        self.assertEqual(first.pk, repeated.pk)
        with self.assertRaises(StaleAttendanceReview):
            ObservationService.record(
                actor=self.actor,
                checking_round=checking_round,
                meeting_id=meeting.pk,
                manifest_revision=1,
                submission_key="same-request",
                findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 6}],
            )

    def test_present_confirmation_is_exact_revision_checked_and_does_not_overwrite_exception(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        observation = ObservationService.record(
            actor=self.actor,
            checking_round=checking_round,
            meeting_id=meeting.pk,
            manifest_revision=1,
            submission_key="late",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 3}],
        )
        result = AttendanceResultService.select_observation(
            actor=self.actor, observation=observation, expected_revision=0, reason="Late observed"
        )
        with self.assertRaises(StaleAttendanceReview):
            AttendanceResultService.confirm_present(
                actor=self.actor,
                checking_round=checking_round,
                manifest_revision=1,
                reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
            )
        skipped = AttendanceResultService.confirm_present(
            actor=self.actor,
            checking_round=checking_round,
            manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": result.revision}],
        )
        self.assertEqual(skipped["skipped_meeting_ids"], [meeting.pk])
        result.refresh_from_db()
        self.assertEqual(result.status, AttendanceResult.Status.EXCEPTION)

    def test_pending_coverage_reconciliation_blocks_confirmation_only_for_affected_meeting(self):
        meeting = self.meeting()
        CoverageReconciliation.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            offering=self.offering,
            event_type=CoverageReconciliation.EventType.DIRECT_UPDATE,
            source_reference="pending-for-meeting",
            prior_faculty=self.faculty,
            proposed_faculty=self.replacement,
            reason="Awaiting effective boundary",
            created_by=self.actor,
        )
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        with self.assertRaisesMessage(ValidationError, "pending coverage"):
            AttendanceResultService.confirm_present(
                actor=self.actor,
                checking_round=checking_round,
                manifest_revision=1,
                reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
            )

    def test_exact_meeting_substitution_can_supply_attribution_without_permanent_coverage(self):
        version = self.schedule()
        meeting = MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[],
        )
        SubstitutionService.assign(
            actor=self.actor,
            meeting=meeting,
            substitute_faculty=self.replacement,
            reason="Exact meeting substitute",
        )
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        AttendanceResultService.confirm_present(
            actor=self.actor,
            checking_round=checking_round,
            manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
        )
        self.assertEqual(AttendanceResult.objects.get(meeting=meeting).faculty_user_id, self.replacement.pk)

    def _unresolved_cutoff_meeting(self):
        version = self.schedule()
        return MeetingService.generate(
            actor=self.actor, schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5), offerings=[self.combined_offering],
        )

    def _review_meeting_cutoff(self, meeting):
        return review_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=meeting.meeting_date, end_date=meeting.meeting_date,
        )

    def _confirm_substituted_cutoff_meeting(self, meeting):
        SubstitutionService.assign(
            actor=self.actor, meeting=meeting, substitute_faculty=self.replacement,
            reason="Synthetic exact-date substitute for coverage recovery",
        )
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date,
        )
        AttendanceResultService.confirm_present(
            actor=self.actor, checking_round=checking_round, manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
        )
        return AttendanceResult.objects.get(meeting=meeting)

    def test_cutoff_substitute_confirmation_resolves_historical_warning_without_rewriting_it(self):
        meeting = self._unresolved_cutoff_meeting()
        snapshot = dict(meeting.faculty_snapshot)
        self.assertTrue(meeting.unresolved_coverage)
        self.assertEqual(
            [item.code for item in self._review_meeting_cutoff(meeting).blockers],
            ["MEETING_RECONCILIATION_PENDING"],
        )
        substitution = SubstitutionService.assign(
            actor=self.actor, meeting=meeting, substitute_faculty=self.replacement,
            reason="Synthetic exact-date substitute for coverage recovery",
        )
        self.assertEqual(
            [item.code for item in self._review_meeting_cutoff(meeting).blockers],
            ["UNVERIFIED_ATTENDANCE"],
        )
        result = self._confirm_substituted_cutoff_meeting(meeting)
        review = self._review_meeting_cutoff(meeting)
        self.assertTrue(review.ready, review.blockers)
        self.assertEqual([row.meeting.pk for row in review.records], [meeting.pk])
        self.assertEqual(result.faculty_user_id, self.replacement.pk)
        self.assertEqual(result.revision, 1)
        self.assertEqual(result.history.count(), 1)
        meeting.refresh_from_db()
        self.assertTrue(meeting.unresolved_coverage)
        self.assertEqual(meeting.faculty_snapshot, snapshot)
        self.assertIsNone(meeting.faculty_user_id)
        self.assertTrue(substitution.decision_snapshot["meeting_snapshot"]["unresolved_coverage"])
        self.assertTrue(AuditLog.objects.filter(
            action="FACULTY_ATTENDANCE_SUBSTITUTION_ASSIGNED", entity_id=str(substitution.pk),
            actor_user=self.actor,
        ).exists())

    def test_cutoff_substitute_does_not_bypass_pending_reconciliation(self):
        meeting = self._unresolved_cutoff_meeting()
        result = self._confirm_substituted_cutoff_meeting(meeting)
        coverage = self.coverage(effective_from=meeting.starts_at)
        reconciliation = MeetingReconciliation.objects.get(
            meeting=meeting, source_reference=f"coverage:{coverage.pk}",
        )
        self.assertEqual(
            [item.code for item in self._review_meeting_cutoff(meeting).blockers],
            ["MEETING_RECONCILIATION_PENDING"],
        )
        ReconciliationService.resolve(
            actor=self.actor, reconciliation=reconciliation,
            decision=MeetingReconciliation.Decision.KEEP_SNAPSHOT,
            reason="Synthetic review retains the dated substitute and historical warning",
        )
        review = self._review_meeting_cutoff(meeting)
        self.assertTrue(review.ready, review.blockers)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.status, MeetingReconciliation.Status.RESOLVED)
        result.refresh_from_db()
        self.assertEqual(result.revision, 1)
        meeting.refresh_from_db()
        self.assertTrue(meeting.unresolved_coverage)

    def test_cutoff_resolved_reconciliation_alone_does_not_supply_missing_coverage(self):
        meeting = self._unresolved_cutoff_meeting()
        coverage = self.coverage(effective_from=meeting.starts_at)
        reconciliation = MeetingReconciliation.objects.get(
            meeting=meeting, source_reference=f"coverage:{coverage.pk}",
        )
        ReconciliationService.resolve(
            actor=self.actor, reconciliation=reconciliation,
            decision=MeetingReconciliation.Decision.KEEP_SNAPSHOT,
            reason="Synthetic history review is not a substitution decision",
        )
        review = self._review_meeting_cutoff(meeting)
        self.assertFalse(review.ready)
        self.assertEqual([item.code for item in review.blockers], ["MEETING_RECONCILIATION_PENDING"])
        self.assertFalse(AttendanceResult.objects.filter(meeting=meeting).exists())

    def test_cutoff_substitute_requires_matching_revision_checked_result_attribution(self):
        meeting = self._unresolved_cutoff_meeting()
        result = self._confirm_substituted_cutoff_meeting(meeting)
        result = AttendanceResultService.reconcile_attribution(
            actor=self.actor, result=result, expected_revision=1, faculty_user=self.faculty,
            reason="Synthetic conflicting attribution to exercise cutoff protection",
        )
        review = self._review_meeting_cutoff(meeting)
        self.assertFalse(review.ready)
        self.assertEqual([item.code for item in review.blockers], ["FACULTY_ATTRIBUTION_CONFLICT"])
        with self.assertRaises(StaleAttendanceReview):
            AttendanceResultService.reconcile_attribution(
                actor=self.actor, result=result, expected_revision=1, faculty_user=self.replacement,
                reason="Synthetic stale correction must fail",
            )
        self.assertEqual(result.history.count(), 2)
        result = AttendanceResultService.reconcile_attribution(
            actor=self.actor, result=result, expected_revision=2, faculty_user=self.replacement,
            reason="Synthetic correction restores the authorized dated substitute",
        )
        self.assertTrue(self._review_meeting_cutoff(meeting).ready)
        self.assertEqual(result.revision, 3)
        self.assertEqual(
            list(result.history.order_by("revision").values_list("faculty_user_id", flat=True)),
            [self.replacement.pk, self.faculty.pk, self.replacement.pk],
        )

    def test_cutoff_substitute_recovery_preserves_scope_and_direct_deny(self):
        meeting = self._unresolved_cutoff_meeting()
        outsider = User.objects.create_user(
            username="synthetic-no-scope", email="synthetic-no-scope@example.invalid",
        )
        with self.assertRaisesMessage(ValidationError, "authorized scope"):
            SubstitutionService.assign(
                actor=self.actor, meeting=meeting, substitute_faculty=outsider,
                reason="Synthetic out-of-scope substitute must fail",
            )
        denial = UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=MANAGE_SUBSTITUTIONS_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self._confirm_substituted_cutoff_meeting(meeting)
        self.assertFalse(MeetingSubstitution.objects.filter(meeting=meeting).exists())
        denial.delete()
        self._confirm_substituted_cutoff_meeting(meeting)
        UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=PUBLISH_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            self._review_meeting_cutoff(meeting)

    def test_correction_revisions_remove_late_count_without_erasing_history(self):
        meeting = self.meeting()
        first_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        late = ObservationService.record(
            actor=self.actor,
            checking_round=first_round,
            meeting_id=meeting.pk,
            manifest_revision=1,
            submission_key="late-zero",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 0}],
        )
        result = AttendanceResultService.select_observation(
            actor=self.actor, observation=late, expected_revision=0, reason="Late flag confirmed"
        )
        self.assertEqual(
            monthly_tardiness_summary(
                actor=self.actor, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=1
            )["count"],
            1,
        )
        second_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        early_only = ObservationService.record(
            actor=self.actor,
            checking_round=second_round,
            meeting_id=meeting.pk,
            manifest_revision=1,
            submission_key="remove-late",
            findings=[{"finding_type": "EARLY", "segment_key": "dismissal", "minutes": 2}],
        )
        AttendanceResultService.select_observation(
            actor=self.actor,
            observation=early_only,
            expected_revision=result.revision,
            reason="Checker corrected late flag after review",
        )
        result.refresh_from_db()
        self.assertFalse(result.late_flag)
        self.assertEqual(result.history.count(), 2)
        self.assertEqual(
            monthly_tardiness_summary(
                actor=self.actor, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=1
            )["count"],
            0,
        )

    def test_monthly_late_thresholds_count_meetings_once_across_rounds(self):
        version = self.schedule(effective_from=date(2026, 3, 1))
        self.coverage(effective_from=self.aware(2026, 3, 1))
        meetings = []
        for meeting_date in (date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16), date(2026, 3, 23), date(2026, 3, 30)):
            meeting = MeetingService.generate(
                actor=self.actor, schedule_slot=version.slots.get(), meeting_date=meeting_date, offerings=[]
            )
            checking_round = CheckingRoundService.create(
                actor=self.actor, meetings=[meeting], checking_date=meeting_date
            )
            observation = ObservationService.record(
                actor=self.actor,
                checking_round=checking_round,
                meeting_id=meeting.pk,
                manifest_revision=1,
                submission_key=f"late-{meeting.pk}",
                findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 0}],
            )
            AttendanceResultService.select_observation(
                actor=self.actor, observation=observation, expected_revision=0, reason="Late flag confirmed"
            )
            meetings.append(meeting)
            summary = monthly_tardiness_summary(
                actor=self.actor, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=3
            )
            if len(meetings) == 3:
                self.assertEqual(summary["threshold_state"], "nearing")
            elif len(meetings) == 4:
                self.assertEqual(summary["threshold_state"], "reached")
            elif len(meetings) == 5:
                self.assertEqual(summary["threshold_state"], "exceeded")
        repeated_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meetings[0]], checking_date=meetings[0].meeting_date
        )
        self.assertEqual(repeated_round.manifest_rows.get().reviewed_result_revision, 1)
        self.assertEqual(
            monthly_tardiness_summary(
                actor=self.actor, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=3
            )["count"],
            5,
        )

    def test_date_range_and_calendar_month_boundaries_exclude_adjacent_dates(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        observation = ObservationService.record(
            actor=self.actor,
            checking_round=checking_round,
            meeting_id=meeting.pk,
            manifest_revision=1,
            submission_key="january-boundary-late",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 0}],
        )
        AttendanceResultService.select_observation(
            actor=self.actor, observation=observation, expected_revision=0, reason="January late confirmed"
        )

        january, january_complete = date_range_results(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            faculty_user=self.faculty,
        )
        february, february_complete = date_range_results(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 28),
            faculty_user=self.faculty,
        )
        self.assertEqual(list(january.values_list("meeting_id", flat=True)), [meeting.pk])
        self.assertFalse(february.exists())
        self.assertTrue(january_complete)
        self.assertTrue(february_complete)
        self.assertEqual(
            monthly_tardiness_summary(
                actor=self.actor, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=1
            )["count"],
            1,
        )
        self.assertEqual(
            monthly_tardiness_summary(
                actor=self.actor, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=2
            )["count"],
            0,
        )

    def test_inactive_faculty_current_result_remains_reportable(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        AttendanceResultService.confirm_present(
            actor=self.actor,
            checking_round=checking_round,
            manifest_revision=1,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
        )
        self.faculty.is_active = False
        self.faculty.save(update_fields=["is_active"])
        self.assertEqual(
            monthly_tardiness_summary(
                actor=self.actor, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=1
            )["count"],
            0,
        )

    def test_direct_deny_blocks_checking_round_encoding(self):
        meeting = self.meeting()
        UserPermission.objects.create(
            user=self.actor,
            permission=Permission.objects.get(code=ENCODE_PERMISSION),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            CheckingRoundService.create(
                actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
            )

    def test_combined_sections_contribute_one_monthly_late_count(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        observation = ObservationService.record(
            actor=self.actor,
            checking_round=checking_round,
            meeting_id=meeting.pk,
            manifest_revision=1,
            submission_key="combined-late",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 1}],
        )
        AttendanceResultService.select_observation(
            actor=self.actor, observation=observation, expected_revision=0, reason="Combined meeting late"
        )
        self.assertEqual(meeting.offering_links.count(), 2)
        self.assertEqual(
            monthly_tardiness_summary(
                actor=self.actor, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=1
            )["count"],
            1,
        )

    def test_scope_limited_tardiness_is_not_labeled_complete_but_faculty_self_view_is_complete(self):
        local_meeting = self.meeting()
        local_round = CheckingRoundService.create(
            actor=self.actor, meetings=[local_meeting], checking_date=local_meeting.meeting_date
        )
        local_observation = ObservationService.record(
            actor=self.actor,
            checking_round=local_round,
            meeting_id=local_meeting.pk,
            manifest_revision=1,
            submission_key="local-late",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 2}],
        )
        AttendanceResultService.select_observation(
            actor=self.actor, observation=local_observation, expected_revision=0, reason="Local late"
        )

        other_course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.other_department,
            code="OTHER101",
            title="Other Department Course",
        )
        other_section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.other_department,
            program=self.other_program,
            code="OTHER-S1",
            name="Other Section",
        )
        other_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.other_department,
            program=self.other_program,
            academic_year=self.academic_year,
            term=self.term,
            course=other_course,
            section=other_section,
            schedule_text="M 08:00-09:00",
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=other_offering,
            faculty_user=self.faculty,
        )
        superuser = User.objects.create_superuser("scope-admin", "scope-admin@example.test", "x")
        other_version = ScheduleService.create_version(
            actor=superuser, offering=other_offering, effective_from=date(2026, 1, 1)
        )
        CoverageService.create(
            actor=superuser,
            offering=other_offering,
            faculty_user=self.faculty,
            effective_from=self.aware(2026, 1, 1),
            reason="Other department coverage",
        )
        other_meeting = MeetingService.generate(
            actor=superuser,
            schedule_slot=other_version.slots.get(),
            meeting_date=date(2026, 1, 12),
            offerings=[],
        )
        other_round = CheckingRoundService.create(
            actor=superuser, meetings=[other_meeting], checking_date=other_meeting.meeting_date
        )
        other_observation = ObservationService.record(
            actor=superuser,
            checking_round=other_round,
            meeting_id=other_meeting.pk,
            manifest_revision=1,
            submission_key="other-late",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 4}],
        )
        AttendanceResultService.select_observation(
            actor=superuser, observation=other_observation, expected_revision=0, reason="Other late"
        )
        restricted = monthly_tardiness_summary(
            actor=self.actor, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=1
        )
        self.assertEqual(restricted["count"], 1)
        self.assertFalse(restricted["is_complete"])
        self.assertIsNone(restricted["threshold_state"])

        SystemSetting.objects.create(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            setting_value="true",
            value_type=SystemSetting.ValueType.BOOL,
        )
        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code=VIEW_PERMISSION),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        complete = monthly_tardiness_summary(
            actor=self.faculty, faculty_user=self.faculty, tenant_id=self.tenant.pk, year=2026, month=1
        )
        self.assertEqual(complete["count"], 2)
        self.assertTrue(complete["is_complete"])

    def test_module_off_does_not_change_existing_academic_workflow(self):
        SystemSetting.objects.filter(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_ENABLED_KEY,
        ).update(setting_value="false")
        self.offering.room = "R102"
        self.offering.save(update_fields=["room"])
        self.assertEqual(CourseOffering.objects.get(pk=self.offering.pk).room, "R102")
        self.assertFalse(ScheduleVersion.objects.exists())
        with self.assertRaises(PermissionDenied):
            self.schedule()

    def test_daily_encoding_get_is_read_only_and_post_materializes_without_manual_setup(self):
        self.coverage()
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:daily_encoding")
        query = {
            "academic_year": self.academic_year.pk,
            "term": self.term.pk,
            "meeting_date": "2026-01-05",
        }
        response = self.client.get(url, query)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TeachingMeeting.objects.exists())
        self.assertFalse(ScheduleVersion.objects.exists())
        response = self.client.post(url, query)
        self.assertEqual(response.status_code, 302)
        meeting = TeachingMeeting.objects.get(
            meeting_date=date(2026, 1, 5),
            offering_links__offering=self.offering,
        )
        self.assertEqual(meeting.source_kind, "COURSE_OFFERING")
        self.assertTrue(meeting.occurrence_key)
        self.assertEqual(CheckingRound.objects.filter(daily_occurrence_date=date(2026, 1, 5)).count(), 1)
        self.assertFalse(AttendanceResult.objects.exists())

    def test_daily_preview_marks_a_linked_combined_row_needing_attention(self):
        version = self.schedule()
        self.coverage()
        self.coverage(self.combined_offering)
        meeting = MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[self.combined_offering],
        )
        MeetingReconciliation.objects.create(
            meeting=meeting,
            source_type=MeetingReconciliation.SourceType.SCHEDULE,
            source_reference="linked-preview-status",
            reason="The combined meeting needs an attendance-history decision.",
            before_snapshot={},
            proposed_snapshot={},
            detected_by=self.actor,
        )
        self.client.force_login(self.actor)
        response = self.client.get(
            reverse("faculty_attendance:daily_encoding"),
            {
                "academic_year": self.academic_year.pk,
                "term": self.term.pk,
                "meeting_date": "2026-01-05",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["occurrence_rows"]), 1)
        self.assertEqual(len(response.context["occurrence_rows"][0]["issues"]), 1)
        self.assertContains(response, self.section.code)
        self.assertContains(response, self.section2.code)
        self.assertContains(response, "Needs attention")
        self.assertNotContains(response, "Ready to encode")

    def test_round_requires_deliberate_present_selection_and_preserves_invalid_exception_values(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor,
            meetings=[meeting],
            checking_date=meeting.meeting_date,
            academic_year=self.academic_year,
            term=self.term,
            daily_occurrence_date=meeting.meeting_date,
        )
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:round", args=[checking_round.public_id])

        initial = self.client.get(url)
        self.assertEqual(initial.status_code, 200)
        self.assertFalse(initial.context["rows"][0].present_selected)
        self.assertContains(initial, "Leaving it blank keeps it unverified.")
        self.assertFalse(AttendanceResult.objects.exists())

        invalid = self.client.post(
            url,
            {
                "action": "exception",
                "meeting_id": meeting.pk,
                "expected_revision": 0,
                "submission_key": "invalid-preserved-values",
                "absence_code": "A",
                "missed_hours": "",
                "missed_periods": "",
                "late_minutes": "",
                "early_minutes": "",
                "reason": "Paper record needs correction before saving.",
            },
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "A/N requires actual missed decimal hours.")
        self.assertContains(invalid, "Paper record needs correction before saving.")
        self.assertEqual(
            invalid.context["rows"][0].exception_values["reason"],
            "Paper record needs correction before saving.",
        )
        self.assertFalse(AttendanceResult.objects.exists())

        confirmed = self.client.post(
            url,
            {
                "action": "confirm_present",
                "manifest_revision": checking_round.manifest_revision,
                "present_rows": f"{meeting.pk}:0",
            },
        )
        self.assertEqual(confirmed.status_code, 302)
        self.assertEqual(AttendanceResult.objects.get(meeting=meeting).status, AttendanceResult.Status.PRESENT)

    def test_round_records_full_class_absence_in_hours_without_periods(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:round", args=[checking_round.public_id])

        page = self.client.get(url)
        self.assertContains(page, "Actual missed time (decimal hours)")
        self.assertContains(page, "1.00 for a full 8:00&ndash;9:00 class", html=False)
        self.assertNotContains(page, 'name="missed_periods"')

        response = self.client.post(
            url,
            {
                "action": "exception",
                "meeting_id": meeting.pk,
                "expected_revision": 0,
                "submission_key": "full-hour-absence",
                "absence_code": "A",
                "missed_hours": "1.00",
                "reason": "Full scheduled class missed without notice.",
            },
        )

        self.assertEqual(response.status_code, 302)
        result = AttendanceResult.objects.get(meeting=meeting)
        self.assertEqual(result.absent_without_notice_hours, Decimal("1.00"))
        self.assertEqual(result.missed_periods, Decimal("0"))
        absence = next(item for item in result.findings_snapshot if item["finding_type"] == "ABSENCE")
        self.assertEqual(absence["missed_hours"], "1.00")
        self.assertIsNone(absence["missed_periods"])

    def test_round_records_partial_absence_in_decimal_hours(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("faculty_attendance:round", args=[checking_round.public_id]),
            {
                "action": "exception",
                "meeting_id": meeting.pk,
                "expected_revision": 0,
                "submission_key": "partial-hour-absence",
                "absence_code": "N",
                "missed_hours": "0.50",
                "reason": "Half hour missed with notice.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        result = AttendanceResult.objects.get(meeting=meeting)
        self.assertEqual(result.absent_with_notice_hours, Decimal("0.50"))
        self.assertEqual(result.missed_periods, Decimal("0"))

    def test_round_rejects_period_only_new_absence_and_redisplays_checker_values(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("faculty_attendance:round", args=[checking_round.public_id]),
            {
                "action": "exception",
                "meeting_id": meeting.pk,
                "expected_revision": 0,
                "submission_key": "period-only-rejected",
                "absence_code": "A",
                "missed_periods": "1.00",
                "reason": "Keep this checker note visible.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("Missed periods are no longer accepted", payload["row_html"])
        self.assertIn("A/N requires actual missed decimal hours", payload["row_html"])
        self.assertIn('<option value="A" selected', payload["row_html"])
        self.assertIn("Keep this checker note visible.", payload["row_html"])
        self.assertFalse(AttendanceObservation.objects.exists())
        self.assertFalse(AttendanceResult.objects.exists())

    def test_round_correction_preserves_existing_period_absence_without_conversion(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        legacy_snapshot = [{
            "finding_type": "ABSENCE",
            "segment_key": "legacy-period",
            "notice_status": "A",
            "missed_periods": "1.00",
            "missed_hours": None,
            "minutes": None,
        }]
        result = AttendanceResult.objects.create(
            meeting=meeting,
            revision=1,
            status=AttendanceResult.Status.EXCEPTION,
            faculty_user=self.faculty,
            missed_periods=Decimal("1.00"),
            findings_snapshot=legacy_snapshot,
            corrected_by=self.actor,
            correction_reason="Historical period-based finding.",
        )
        AttendanceResultRevision.objects.create(
            result=result,
            revision=1,
            status=result.status,
            faculty_user=self.faculty,
            findings_snapshot=legacy_snapshot,
            changed_by=self.actor,
            change_reason=result.correction_reason,
        )
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:round", args=[checking_round.public_id])

        page = self.client.get(url)
        self.assertContains(page, "Saved period-based absence retained.")
        self.assertEqual(page.context["rows"][0].legacy_period_absence_findings, legacy_snapshot)
        self.assertContains(page, "1.00 missed period(s)")
        self.assertNotContains(page, f'id="absence-{meeting.pk}"')
        self.assertNotContains(page, f'id="hours-{meeting.pk}"')

        rejected_replacement = self.client.post(
            url,
            {
                "action": "exception",
                "meeting_id": meeting.pk,
                "expected_revision": 1,
                "submission_key": "legacy-period-replacement-rejected",
                "missed_periods": "2.00",
                "reason": "Attempted period replacement.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(rejected_replacement.status_code, 400)
        self.assertIn("Missed periods are no longer accepted", rejected_replacement.json()["row_html"])
        result.refresh_from_db()
        self.assertEqual((result.revision, result.missed_periods), (1, Decimal("1.00")))

        correction = self.client.post(
            url,
            {
                "action": "exception",
                "meeting_id": meeting.pk,
                "expected_revision": 1,
                "submission_key": "legacy-period-correction",
                "late_flag": "on",
                "late_minutes": "5",
                "reason": "Added verified late detail without changing legacy absence.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(correction.status_code, 200)
        result.refresh_from_db()
        self.assertEqual(result.revision, 2)
        self.assertEqual(result.missed_periods, Decimal("1.00"))
        self.assertEqual(result.absent_without_notice_hours, Decimal("0"))
        self.assertEqual(result.findings_snapshot[0], legacy_snapshot[0])
        self.assertEqual([item.revision for item in result.history.order_by("revision")], [1, 2])
        self.assertEqual(result.history.get(revision=1).findings_snapshot, legacy_snapshot)

    def test_round_card_uses_dated_faculty_coverage_after_current_assignment_changes(self):
        meeting = self.meeting()
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.replacement,
            is_primary=True,
        )
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        self.client.force_login(self.actor)

        response = self.client.get(reverse("faculty_attendance:round", args=[checking_round.public_id]))

        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row.attributed_faculty, self.faculty)
        self.assertEqual(row.faculty_attribution_label, "Dated faculty coverage")
        self.assertContains(response, "Assigned faculty")
        self.assertContains(response, self.faculty.username)
        self.assertNotContains(response, self.replacement.username)

    def test_round_card_prefers_saved_result_faculty_attribution(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        AttendanceResultService.confirm_present(
            actor=self.actor,
            checking_round=checking_round,
            manifest_revision=checking_round.manifest_revision,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
        )
        result = AttendanceResult.objects.get(meeting=meeting)
        AttendanceResultService.reconcile_attribution(
            actor=self.actor,
            result=result,
            expected_revision=1,
            faculty_user=self.replacement,
            reason="Corrected saved attendance attribution.",
        )
        self.client.force_login(self.actor)

        response = self.client.get(reverse("faculty_attendance:round", args=[checking_round.public_id]))

        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row.attributed_faculty, self.replacement)
        self.assertEqual(row.faculty_attribution_label, "Saved attendance attribution")
        self.assertContains(response, self.replacement.username)

    def test_round_card_shows_one_dated_faculty_for_combined_sections(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        self.client.force_login(self.actor)

        response = self.client.get(reverse("faculty_attendance:round", args=[checking_round.public_id]))

        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row.attributed_faculty, self.faculty)
        self.assertEqual(len(meeting.sections_snapshot), 2)
        self.assertContains(response, self.section.code)
        self.assertContains(response, self.section2.code)
        self.assertContains(response, f"<strong>{self.faculty.username}</strong>", count=1)

    def test_round_card_keeps_conflicting_combined_coverage_unresolved(self):
        version = self.schedule()
        self.coverage()
        self.coverage(offering=self.combined_offering, faculty=self.replacement)
        meeting = MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[self.combined_offering],
        )
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        self.client.force_login(self.actor)

        response = self.client.get(reverse("faculty_attendance:round", args=[checking_round.public_id]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(meeting.unresolved_coverage)
        self.assertEqual(response.context["rows"], [])
        self.assertEqual(response.context["index_rows"], [])
        self.assertEqual(response.context["counts"], {"unverified": 0, "present": 0, "exception": 0})
        self.assertEqual(response.context["unresolved_rows"][0].meeting_id, meeting.pk)
        self.assertContains(response, "Classes awaiting faculty assignment (1)")
        self.assertNotContains(response, f'id="meeting-row-{meeting.pk}"')
        self.assertNotContains(response, f"<strong>{self.faculty.username}</strong>")
        self.assertNotContains(response, f"<strong>{self.replacement.username}</strong>")

    def test_round_card_prefers_explicit_meeting_substitute(self):
        meeting = self.meeting()
        SubstitutionService.assign(
            actor=self.actor,
            meeting=meeting,
            substitute_faculty=self.replacement,
            reason="Approved exact-meeting substitute.",
        )
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        self.client.force_login(self.actor)

        response = self.client.get(reverse("faculty_attendance:round", args=[checking_round.public_id]))

        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row.attributed_faculty, self.replacement)
        self.assertEqual(row.faculty_attribution_label, "Explicit meeting substitute")
        self.assertContains(response, self.replacement.username)

    def test_round_ajax_saves_late_and_early_without_replacing_other_meeting(self):
        first = self.meeting()
        second_version = self.schedule(offering=self.combined_offering)
        self.coverage(self.combined_offering)
        second = MeetingService.generate(
            actor=self.actor,
            schedule_slot=second_version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[],
        )
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[first, second], checking_date=first.meeting_date,
            academic_year=self.academic_year, term=self.term, daily_occurrence_date=first.meeting_date,
        )
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("faculty_attendance:round", args=[checking_round.public_id]),
            {
                "action": "exception", "meeting_id": first.pk, "expected_revision": 0,
                "submission_key": "ajax-late-early", "late_flag": "on", "late_minutes": 0,
                "early_flag": "on", "early_minutes": 12, "reason": "Paper check recorded both.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["counts"], {"unverified": 1, "present": 0, "exception": 1})
        self.assertIn('data-meeting-id="%s"' % first.pk, payload["row_html"])
        self.assertNotIn('data-meeting-id="%s"' % second.pk, payload["row_html"])
        result = AttendanceResult.objects.get(meeting=first)
        self.assertTrue(result.late_flag)
        self.assertEqual(result.late_minutes, 0)
        self.assertTrue(result.early_flag)
        self.assertEqual(result.early_minutes, 12)
        self.assertFalse(AttendanceResult.objects.filter(meeting=second).exists())

        rendered = self.client.get(reverse("faculty_attendance:round", args=[checking_round.public_id]))
        self.assertContains(rendered, "Current saved finding")
        self.assertContains(rendered, "L — 0 late minutes")
        self.assertContains(rendered, "E — 12 early-dismissal minutes")

    def test_round_ajax_returns_field_errors_and_does_not_create_a_result(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date)
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("faculty_attendance:round", args=[checking_round.public_id]),
            {
                "action": "exception", "meeting_id": meeting.pk, "expected_revision": 0,
                "submission_key": "ajax-invalid", "absence_code": "A", "reason": "Paper note incomplete.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertIn("A/N requires actual missed decimal hours", payload["row_html"])
        self.assertIn("Paper note incomplete.", payload["row_html"])
        self.assertFalse(AttendanceResult.objects.exists())

    def test_round_ajax_blocks_correction_before_creating_an_observation_when_directly_denied(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date)
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:round", args=[checking_round.public_id])
        initial = self.client.post(
            url,
            {
                "action": "exception", "meeting_id": meeting.pk, "expected_revision": 0,
                "submission_key": "ajax-initial", "late_flag": "on", "late_minutes": 4, "reason": "Initial paper check.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(initial.status_code, 200)
        UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=CORRECT_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        observations_before = AttendanceObservation.objects.count()
        denied = self.client.post(
            url,
            {
                "action": "exception", "meeting_id": meeting.pk, "expected_revision": 1,
                "submission_key": "ajax-denied-correction", "late_flag": "on", "late_minutes": 9,
                "reason": "Attempted correction.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(denied.json()["ok"])
        self.assertEqual(AttendanceObservation.objects.count(), observations_before)
        self.assertEqual(AttendanceResult.objects.get(meeting=meeting).late_minutes, 4)

    def test_round_ajax_reports_a_stale_correction_without_creating_an_observation(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date)
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:round", args=[checking_round.public_id])
        first = self.client.post(
            url,
            {
                "action": "exception", "meeting_id": meeting.pk, "expected_revision": 0,
                "submission_key": "ajax-stale-initial", "late_flag": "on", "late_minutes": 4,
                "reason": "Initial paper check.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(first.status_code, 200)
        observations_before = AttendanceObservation.objects.count()
        stale = self.client.post(
            url,
            {
                "action": "exception", "meeting_id": meeting.pk, "expected_revision": 0,
                "submission_key": "ajax-stale-correction", "late_flag": "on", "late_minutes": 9,
                "reason": "Stale correction attempt.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(stale.status_code, 409)
        self.assertFalse(stale.json()["ok"])
        self.assertIn("changed; reload and review", stale.json()["message"])
        self.assertEqual(AttendanceObservation.objects.count(), observations_before)
        self.assertEqual(AttendanceResult.objects.get(meeting=meeting).late_minutes, 4)

    def test_round_ajax_correction_returns_revision_two_and_preserves_revision_one(self):
        meeting = self.meeting()
        checking_round = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date)
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:round", args=[checking_round.public_id])
        initial = self.client.post(
            url,
            {
                "action": "exception", "meeting_id": meeting.pk, "expected_revision": 0,
                "submission_key": "ajax-revision-one", "late_flag": "on", "late_minutes": 10,
                "early_flag": "on", "early_minutes": 5, "reason": "Initial paper finding.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(initial.status_code, 200)
        correction = self.client.post(
            url,
            {
                "action": "exception", "meeting_id": meeting.pk, "expected_revision": 1,
                "submission_key": "ajax-revision-two", "late_flag": "on", "late_minutes": 12,
                "early_flag": "on", "early_minutes": 5, "reason": "Corrected against paper check.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json",
        )
        self.assertEqual(correction.status_code, 200)
        payload = correction.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["revision"], 2)
        self.assertIn("L — 12 late minutes", payload["row_html"])
        self.assertIn("E — 5 early-dismissal minutes", payload["row_html"])
        result = AttendanceResult.objects.get(meeting=meeting)
        self.assertEqual((result.revision, result.late_minutes, result.early_minutes), (2, 12, 5))
        history = list(result.history.order_by("revision"))
        self.assertEqual([item.revision for item in history], [1, 2])
        revision_one_late = next(item for item in history[0].findings_snapshot if item["finding_type"] == "LATE")
        self.assertEqual(revision_one_late["minutes"], 10)
        self.assertEqual(history[1].change_reason, "Corrected against paper check.")

    def test_round_ajax_uses_the_form_action_attribute_when_action_is_a_hidden_field_name(self):
        source = Path(settings.BASE_DIR, "static", "faculty_attendance", "round_encoding.js").read_text(encoding="utf-8")
        self.assertIn('form.getAttribute("action") || window.location.href', source)
        self.assertNotIn("fetch(form.action || window.location.href", source)

    def test_daily_encoding_reuses_compatible_manual_schedule_and_dated_meeting_idempotently(self):
        version = self.schedule()
        self.coverage()
        meeting = MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[],
        )
        original_counts = {
            "versions": ScheduleVersion.objects.count(),
            "slots": ScheduleSlot.objects.count(),
            "meetings": TeachingMeeting.objects.count(),
            "results": AttendanceResult.objects.count(),
        }

        first_rounds, first_issues = prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 5),
        )
        second_rounds, second_issues = prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 5),
        )

        self.assertFalse(first_issues)
        self.assertFalse(second_issues)
        self.assertEqual(first_rounds[0].pk, second_rounds[0].pk)
        self.assertEqual(first_rounds[0].manifest_rows.get().meeting_id, meeting.pk)
        self.assertEqual(ScheduleVersion.objects.count(), original_counts["versions"])
        self.assertEqual(ScheduleSlot.objects.count(), original_counts["slots"])
        self.assertEqual(TeachingMeeting.objects.count(), original_counts["meetings"])
        self.assertEqual(AttendanceResult.objects.count(), original_counts["results"])
        meeting.refresh_from_db()
        self.assertEqual(meeting.source_kind, "MANUAL")
        self.assertIsNone(meeting.occurrence_key)

    def test_daily_encoding_reuses_explicit_dated_combined_meeting_without_recurrence(self):
        version = self.schedule()
        self.coverage()
        self.coverage(self.combined_offering)
        meeting = MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[self.combined_offering],
        )
        occurrences, issues = expected_daily_occurrences(
            offerings=[self.offering, self.combined_offering],
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )
        self.client.force_login(self.actor)
        preview = self.client.get(
            reverse("faculty_attendance:daily_encoding"),
            {
                "academic_year": self.academic_year.pk,
                "term": self.term.pk,
                "meeting_date": "2026-01-05",
            },
        )

        first_rounds, first_issues = prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 5),
        )
        second_rounds, second_issues = prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 5),
        )

        self.assertFalse(RecurringCombinedClass.objects.exists())
        self.assertFalse(issues)
        self.assertEqual(len(occurrences), 1)
        self.assertTrue(occurrences[0].is_combined)
        self.assertEqual(occurrences[0].dated_meeting_id, meeting.pk)
        self.assertEqual(
            {item.pk for item in occurrences[0].linked_offerings},
            {self.offering.pk, self.combined_offering.pk},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, self.section.code)
        self.assertContains(preview, self.section2.code)
        self.assertContains(preview, "Sections taught together")
        self.assertFalse(first_issues)
        self.assertFalse(second_issues)
        self.assertEqual(TeachingMeeting.objects.count(), 1)
        self.assertEqual(meeting.offering_links.count(), 2)
        self.assertEqual(first_rounds[0].manifest_rows.get().meeting_id, meeting.pk)
        self.assertEqual(first_rounds[0].pk, second_rounds[0].pk)

    def test_dated_combination_stays_exact_to_its_date_and_time(self):
        for offering in (self.offering, self.combined_offering):
            offering.schedule_text = "M 08:00-09:00; M 10:00-11:00"
            offering.save(update_fields=["schedule_text", "updated_at"])
        version = self.schedule()
        self.coverage()
        self.coverage(self.combined_offering)
        MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(start_time=time(8)),
            meeting_date=date(2026, 1, 5),
            offerings=[self.combined_offering],
        )

        combined_date, combined_issues = expected_daily_occurrences(
            offerings=[self.offering, self.combined_offering],
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )
        other_date, other_issues = expected_daily_occurrences(
            offerings=[self.offering, self.combined_offering],
            term=self.term,
            start_date=date(2026, 1, 12),
            end_date=date(2026, 1, 12),
        )

        self.assertFalse(combined_issues)
        self.assertEqual(len(combined_date), 3)
        self.assertEqual(sum(item.is_combined for item in combined_date), 1)
        self.assertEqual(
            sum(item.start_time == time(10) and not item.is_combined for item in combined_date),
            2,
        )
        self.assertFalse(other_issues)
        self.assertEqual(len(other_date), 4)
        self.assertFalse(any(item.is_combined for item in other_date))

    def test_dated_combined_cutoff_counts_one_unverified_then_one_verified_meeting(self):
        version = self.schedule()
        self.coverage()
        self.coverage(self.combined_offering)
        meeting = MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[self.combined_offering],
        )

        unverified = review_cutoff(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            academic_year=self.academic_year,
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )
        rounds, issues = prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 5),
        )
        AttendanceResultService.confirm_present(
            actor=self.actor,
            checking_round=rounds[0],
            manifest_revision=rounds[0].manifest_revision,
            reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
        )
        verified = review_cutoff(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            academic_year=self.academic_year,
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        self.assertFalse(issues)
        self.assertEqual([item.code for item in unverified.blockers], ["UNVERIFIED_ATTENDANCE"])
        self.assertNotIn("UNMATERIALIZED_OCCURRENCE", {item.code for item in unverified.blockers})
        self.assertNotIn("RECORDED_MEETING_SOURCE_MISMATCH", {item.code for item in unverified.blockers})
        self.assertTrue(verified.ready)
        self.assertEqual(len(verified.records), 1)
        self.assertEqual(verified.records[0].meeting_id if hasattr(verified.records[0], "meeting_id") else verified.records[0].meeting.pk, meeting.pk)

    def test_dated_combination_never_folds_a_partial_scope_or_room_conflict(self):
        version = self.schedule()
        self.coverage()
        self.coverage(self.combined_offering)
        meeting = MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[self.combined_offering],
        )

        partial, partial_issues = expected_daily_occurrences(
            offerings=[self.offering],
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )
        self.combined_offering.room = "R202"
        self.combined_offering.save(update_fields=["room", "updated_at"])
        conflicted, conflict_issues = expected_daily_occurrences(
            offerings=[self.offering, self.combined_offering],
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )

        self.assertFalse(partial_issues)
        self.assertEqual(len(partial), 1)
        self.assertFalse(partial[0].is_combined)
        self.assertEqual(partial[0].linked_offerings, (self.offering,))
        self.assertFalse(conflicted)
        self.assertEqual({item.code for item in conflict_issues}, {"DATED_COMBINED_ROOM_CONFLICT"})
        snapshot = TeachingMeeting.objects.get(pk=meeting.pk).location_snapshot
        self.assertIn("R101", {snapshot.get("room"), snapshot.get("room_text")})

    def test_competing_dated_and_recurring_memberships_remain_blocked(self):
        section3 = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="S3",
            name="Section 3",
        )
        third_offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=section3,
            room="R101",
            schedule_text="M 08:00-09:00",
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=third_offering,
            faculty_user=self.faculty,
            is_primary=True,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty,
            is_primary=True,
        )
        version = self.schedule()
        self.coverage()
        self.coverage(self.combined_offering)
        MeetingService.generate(
            actor=self.actor,
            schedule_slot=version.slots.get(),
            meeting_date=date(2026, 1, 5),
            offerings=[self.combined_offering],
        )
        group = RecurringCombinedClassService.create(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            academic_year_id=self.academic_year.pk,
            term_id=self.term.pk,
            offering_ids=[self.offering.pk, third_offering.pk],
            weekday=0,
            start_time=time(8),
            end_time=time(9),
            effective_from=date(2026, 1, 1),
            effective_until=date(2026, 1, 31),
            reason="Conflicting fixture evidence",
        )

        occurrences, issues = expected_daily_occurrences(
            offerings=[self.offering, self.combined_offering, third_offering],
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
            combined_classes=[group],
        )

        self.assertFalse(occurrences)
        self.assertIn("DATED_COMBINED_CONFLICT", {item.code for item in issues})

    def test_daily_encoding_renders_contextual_deduplicated_blockers_inline(self):
        source_permission, _ = Permission.objects.get_or_create(code="offerings.view",
            defaults={"module": "offerings", "action": "view"})
        UserPermission.objects.create(user=self.actor, permission=source_permission,
            grant_type="ALLOW", tenant=self.tenant, campus=self.campus)
        ScheduleService.create_version(
            actor=self.actor,
            offering=self.offering,
            effective_from=date(2026, 1, 1),
            corrected_slots=[{
                "weekday": 0,
                "start_time": time(9),
                "end_time": time(10),
                "room_text": "R999",
                "room": "R999",
            }],
            correction_reason="Recorded source deliberately conflicts for the blocker regression.",
        )
        self.combined_offering.schedule_text = "TBA / SEE CHECKER"
        self.combined_offering.save(update_fields=["schedule_text", "updated_at"])
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:daily_encoding")
        payload = {
            "academic_year": self.academic_year.pk,
            "term": self.term.pk,
            "meeting_date": "2026-01-05",
            "route": "",
        }
        before = {
            "versions": ScheduleVersion.objects.count(),
            "slots": ScheduleSlot.objects.count(),
            "meetings": TeachingMeeting.objects.count(),
            "rounds": CheckingRound.objects.count(),
        }

        get_response = self.client.get(url, payload)
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(len(get_response.context["issues"]), 2)
        self.assertEqual(
            {
                "versions": ScheduleVersion.objects.count(),
                "slots": ScheduleSlot.objects.count(),
                "meetings": TeachingMeeting.objects.count(),
                "rounds": CheckingRound.objects.count(),
            },
            before,
        )
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["issues"]), 2)
        self.assertContains(response, "Records needing attention")
        self.assertContains(response, "2 issues")
        self.assertContains(response, "MATH101")
        self.assertContains(response, "S1")
        self.assertContains(response, "S2")
        self.assertContains(response, "Monday, January 5, 2026")
        self.assertContains(response, "conflicts with the Course Offering source")
        self.assertContains(response, "Correct the Course Offering schedule")
        self.assertContains(response, reverse("admin_portal:offering_list"))
        self.assertNotContains(response, "['")
        self.assertEqual(response.context["form"]["academic_year"].value(), str(self.academic_year.pk))
        self.assertEqual(response.context["form"]["term"].value(), str(self.term.pk))
        self.assertEqual(response.context["form"]["meeting_date"].value(), "2026-01-05")
        self.assertEqual(
            {
                "versions": ScheduleVersion.objects.count(),
                "slots": ScheduleSlot.objects.count(),
                "meetings": TeachingMeeting.objects.count(),
                "rounds": CheckingRound.objects.count(),
            },
            before,
        )
        UserPermission.objects.create(
            user=self.actor,
            permission=source_permission,
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        denied_link = self.client.get(url, payload)
        self.assertContains(denied_link, "Records needing attention")
        self.assertNotContains(denied_link, reverse("admin_portal:offering_list"))

    def test_daily_encoding_preview_honors_direct_view_deny(self):
        UserPermission.objects.create(
            user=self.actor,
            permission=Permission.objects.get(code=VIEW_PERMISSION),
            grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant,
            campus=self.campus,
        )
        self.client.force_login(self.actor)
        response = self.client.get(
            reverse("faculty_attendance:daily_encoding"),
            {
                "academic_year": self.academic_year.pk,
                "term": self.term.pk,
                "meeting_date": "2026-01-05",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TeachingMeeting.objects.exists())
        self.assertFalse(ScheduleVersion.objects.exists())

    def test_cutoff_blocks_unmaterialized_occurrence_and_pending_source_change(self):
        self.coverage()
        review = review_cutoff(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            academic_year=self.academic_year,
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 5),
        )
        self.assertFalse(review.ready)
        self.assertIn("UNMATERIALIZED_OCCURRENCE", {item.code for item in review.blockers})
        prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 5),
        )
        self.offering.schedule_text = "M 09:00-10:00"
        self.offering.save(update_fields=["schedule_text", "updated_at"])
        AcademicOfferingSourceIntegrationService.record_change(
            actor=self.actor,
            offering=self.offering,
            old_schedule_text="M 08:00-09:00",
            old_room="R101",
            effective_from=date(2026, 1, 12),
            reason="Registrar-approved room and schedule adjustment.",
        )
        review = review_cutoff(
            actor=self.actor,
            tenant_id=self.tenant.pk,
            campus_id=self.campus.pk,
            academic_year=self.academic_year,
            term=self.term,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 12),
        )
        self.assertIn("SOURCE_CHANGE_PENDING", {item.code for item in review.blockers})

    def test_resolved_course_offering_source_change_preserves_old_meeting_and_materializes_new_period(self):
        self.coverage()
        prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 5),
        )
        old_meeting = TeachingMeeting.objects.get(meeting_date=date(2026, 1, 5))
        self.offering.schedule_text = "M 09:00-10:00"
        self.offering.save(update_fields=["schedule_text", "updated_at"])
        source_change = AcademicOfferingSourceIntegrationService.record_change(
            actor=self.actor,
            offering=self.offering,
            old_schedule_text="M 08:00-09:00",
            old_room="R101",
            effective_from=date(2026, 1, 12),
            reason="",
        )
        AcademicOfferingSourceIntegrationService.resolve(
            actor=self.actor,
            source_change=source_change,
            effective_from=date(2026, 1, 12),
            reason="",
        )
        rounds, issues = prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 12),
        )
        self.assertFalse(issues)
        self.assertEqual(len(rounds), 1)
        old_meeting.refresh_from_db()
        self.assertEqual(old_meeting.schedule_snapshot["start_time"], "08:00:00")
        self.assertEqual(TeachingMeeting.objects.get(meeting_date=date(2026, 1, 12)).schedule_snapshot["start_time"], "09:00:00")

    def test_complete_campus_cutoff_requires_every_department_publish_scope(self):
        other_course = Course.objects.create(
            tenant=self.tenant, campus=self.campus, department=self.other_department, code="CAMPUS201", title="Campus Course"
        )
        other_section = Section.objects.create(
            tenant=self.tenant, campus=self.campus, department=self.other_department, program=self.other_program, code="CAMP-S1", name="Campus Section"
        )
        CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.other_department,
            program=self.other_program,
            academic_year=self.academic_year,
            term=self.term,
            course=other_course,
            section=other_section,
            room="R201",
            schedule_text="M 08:00-09:00",
        )
        with self.assertRaisesMessage(PermissionDenied, "Complete-campus cutoff publication authority"):
            review_cutoff(
                actor=self.actor,
                tenant_id=self.tenant.pk,
                campus_id=self.campus.pk,
                academic_year=self.academic_year,
                term=self.term,
                start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 5),
            )

    def test_publication_is_immutable_and_faculty_sees_only_latest_published_revision(self):
        self.coverage()
        self.coverage(self.combined_offering)
        rounds, issues = prepare_daily_encoding(
            actor=self.actor,
            offerings=[self.offering, self.combined_offering],
            academic_year=self.academic_year,
            term=self.term,
            meeting_date=date(2026, 1, 5),
        )
        self.assertFalse(issues)
        meeting = TeachingMeeting.objects.get(
            meeting_date=date(2026, 1, 5),
            offering_links__offering=self.offering,
        )
        AttendanceResultService.confirm_present(
            actor=self.actor,
            checking_round=rounds[0],
            manifest_revision=rounds[0].manifest_revision,
            reviewed_rows=[
                {"meeting_id": item.pk, "result_revision": 0}
                for item in TeachingMeeting.objects.filter(meeting_date=date(2026, 1, 5))
            ],
        )
        first_review = review_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5),
        )
        first = publish_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5),
            expected_fingerprint=first_review.fingerprint, submission_key="first-publication", publication_reason="First complete campus cutoff.",
        )
        correction_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date
        )
        observation = ObservationService.record(
            actor=self.actor, checking_round=correction_round, meeting_id=meeting.pk,
            manifest_revision=correction_round.manifest_revision, submission_key="late-correction",
            findings=[{"finding_type": "LATE", "segment_key": "arrival", "minutes": 12}],
        )
        result = meeting.attendance_result
        AttendanceResultService.select_observation(
            actor=self.actor, observation=observation, expected_revision=result.revision, reason="Checker corrected recorded late arrival."
        )
        self.assertEqual(
            AttendanceCutoffPublicationEntry.objects.get(publication=first, meeting=meeting).status,
            "PRESENT",
        )
        second_review = review_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5),
        )
        second = publish_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 5), end_date=date(2026, 1, 5),
            expected_fingerprint=second_review.fingerprint, submission_key="second-publication", publication_reason="Republished after checker correction.",
        )
        self.assertEqual(second.version, 2)
        SystemSetting.objects.create(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            setting_value="true",
            value_type=SystemSetting.ValueType.BOOL,
        )
        UserPermission.objects.create(
            user=self.faculty,
            permission=Permission.objects.get(code=VIEW_PERMISSION),
            grant_type=UserPermission.GrantType.ALLOW,
            tenant=self.tenant,
            campus=self.campus,
        )
        entries, _publications, history = faculty_published_entries(
            faculty_user=self.faculty, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            start_date=date(2026, 1, 5), end_date=date(2026, 1, 5), include_history=True,
        )
        self.assertEqual(len(entries), 2)
        corrected_entry = next(item for item in entries if item.meeting_id == meeting.pk)
        self.assertTrue(corrected_entry.late_flag)
        self.assertEqual({entry.publication_id for entry in history}, {first.pk, second.pk})
        self.assertEqual(AttendanceCutoffPublication.objects.count(), 2)

    def _published_dtr_cutoff(self, findings=None):
        meeting = self.meeting(offerings=[self.combined_offering])
        checking_round = CheckingRoundService.create(
            actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date,
        )
        if findings is None:
            AttendanceResultService.confirm_present(
                actor=self.actor, checking_round=checking_round, manifest_revision=1,
                reviewed_rows=[{"meeting_id": meeting.pk, "result_revision": 0}],
            )
        else:
            observation = ObservationService.record(
                actor=self.actor, checking_round=checking_round, meeting_id=meeting.pk,
                manifest_revision=1, submission_key="dtr-findings", findings=findings,
            )
            AttendanceResultService.select_observation(
                actor=self.actor, observation=observation, expected_revision=0,
                reason="",
            )
        review = review_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=meeting.meeting_date, end_date=meeting.meeting_date,
        )
        self.assertTrue(review.ready, review.blockers)
        publication = publish_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=meeting.meeting_date, end_date=meeting.meeting_date,
            expected_fingerprint=review.fingerprint, submission_key="synthetic-dtr-cutoff",
            publication_reason="",
        )
        return meeting, publication

    def _dtr_entry(self, publication, *, kind, hours, reason, leave_type="", offset_kind="",
                   faculty=None, department=None, previous=None, expected_revision=0):
        return save_adjustment(
            actor=self.actor, publication=publication, faculty=faculty or self.faculty,
            department=department or self.department, entry_date=publication.start_date,
            kind=kind, hours=hours, reason=reason,
            leave_type=leave_type, offset_kind=offset_kind,
            previous=previous, expected_revision=expected_revision,
        )

    def _published_closure_dtr(self, pay_basis):
        meeting = self.meeting(offerings=[self.combined_offering])
        values = dict(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=meeting.meeting_date, end_date=meeting.meeting_date,
        )
        before = review_cutoff(**values)
        self.assertIn("UNVERIFIED_ATTENDANCE", {item.code for item in before.blockers})
        self.assertFalse(AttendanceResult.objects.filter(meeting=meeting).exists())
        closure = save_closure(
            actor=self.actor, meeting=meeting, status="CLOSED", kind="HOLIDAY",
            pay_basis=pay_basis, reason="",
            expected_revision=0,
        )
        review = review_cutoff(**values)
        self.assertTrue(review.ready, review.blockers)
        publication = publish_cutoff(
            **values, expected_fingerprint=review.fingerprint,
            submission_key=f"synthetic-closure-{pay_basis}",
            publication_reason="",
        )
        return meeting, closure, publication

    def test_dtr_regular_holiday_closure_credits_scheduled_hours_without_attendance(self):
        meeting, closure, publication = self._published_closure_dtr("REGULAR")
        entry = publication.entries.get()
        self.assertEqual(entry.status, "CLOSED")
        self.assertEqual(entry.closure_decision_id, closure.pk)
        self.assertIsNone(entry.result_revision_id)
        self.assertFalse(AttendanceResult.objects.filter(meeting=meeting).exists())
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(preview.ready, preview.blockers)
        self.assertEqual(preview.snapshot["basic_hours"], "1.00")
        self.assertEqual(preview.snapshot["gross_deductions"], "0.00")
        self.assertEqual(preview.snapshot["net_payable_hours"], "1.00")
        self.assertEqual(preview.snapshot["lines"][0]["scheduled_minutes"], 60)
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_ATTENDANCE_CLOSURE_RECORDED", entity_id=str(closure.pk)).exists())
        final = finalize_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=preview.fingerprint, reason="Synthetic paid closure review",
            faculty_review_complete=True,
        )
        revoked = save_closure(
            actor=self.actor, meeting=meeting, status="REVOKED", kind="HOLIDAY",
            pay_basis="REGULAR", reason="Synthetic closure reversal", expected_revision=1,
        )
        self.assertEqual(revoked.revision, 2)
        self.assertEqual(final.snapshot["net_payable_hours"], "1.00")
        self.assertFalse(preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty).ready)
        reverted_review = review_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=meeting.meeting_date, end_date=meeting.meeting_date,
        )
        self.assertIn("UNVERIFIED_ATTENDANCE", {item.code for item in reverted_review.blockers})
        with self.assertRaisesMessage(ValidationError, "changed"):
            save_closure(
                actor=self.actor, meeting=meeting, status="CLOSED", kind="HOLIDAY",
                pay_basis="REGULAR", reason="Stale synthetic decision", expected_revision=1,
            )

    def test_dtr_part_time_suspension_has_zero_no_work_credit(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        closure = save_closure(
            actor=self.actor, meeting=meeting, status="CLOSED", kind="SUSPENSION",
            pay_basis="PART_TIME", reason="Synthetic suspended class and verified part-time basis",
            expected_revision=0,
        )
        review = review_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=meeting.meeting_date, end_date=meeting.meeting_date,
        )
        self.assertTrue(review.ready, review.blockers)
        publication = publish_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=meeting.meeting_date, end_date=meeting.meeting_date,
            expected_fingerprint=review.fingerprint, submission_key="synthetic-parttime-closure",
            publication_reason="Synthetic no-work closure cutoff",
        )
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(preview.ready, preview.blockers)
        self.assertEqual(preview.snapshot["basic_hours"], "0.00")
        self.assertEqual(preview.snapshot["net_payable_hours"], "0.00")
        self.assertEqual(preview.snapshot["scheduled_minutes"], 60)
        self.assertEqual(preview.snapshot["lines"][0]["closure_revision"], closure.revision)
        final = finalize_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=preview.fingerprint, reason="Synthetic part-time no-work review",
            faculty_review_complete=True,
        )
        self.assertEqual(final.snapshot["net_payable_hours"], "0.00")

    def test_dtr_normal_dated_meeting_needs_no_closure_decision(self):
        meeting, publication = self._published_dtr_cutoff()
        self.assertFalse(AttendanceClosureDecision.objects.exists())
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(preview.ready, preview.blockers)
        self.assertEqual(preview.snapshot["basic_hours"], "1.00")
        save_closure(
            actor=self.actor, meeting=meeting, status="CLOSED", kind="HOLIDAY",
            pay_basis="REGULAR", reason="Synthetic later closure supersedes attendance for DTR",
            expected_revision=0,
        )
        self.assertFalse(preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty).ready)
        self.assertEqual(publication.entries.get().status, "PRESENT")

    def test_dtr_mixed_overlap_rejected_and_only_actual_nonoverlap_deducted(self):
        meeting, publication = self._published_dtr_cutoff([
            {"finding_type": "ABSENCE", "segment_key": "partial", "notice_status": "A", "missed_hours": "0.50"},
            {"finding_type": "LATE", "segment_key": "arrival", "minutes": 10},
        ])
        pending = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertFalse(pending.ready)
        self.assertEqual(pending.snapshot["gross_deductions"], "0.00")
        with self.assertRaisesMessage(ValidationError, "overlap"):
            save_mixed_decision(
                actor=self.actor, publication=publication, faculty=self.faculty, meeting=meeting,
                intervals_text="A 08:00-08:30\nL 08:20-08:30", reason="Synthetic overlapping probe",
                expected_revision=0,
            )
        self.assertFalse(DTRMixedFindingDecision.objects.exists())
        decision = save_mixed_decision(
            actor=self.actor, publication=publication, faculty=self.faculty, meeting=meeting,
            intervals_text="A 08:00-08:30", reason="Synthetic L was fully within the A interval",
            expected_revision=0,
        )
        self.assertEqual(decision.revision, 1)
        self.assertEqual(publication.entries.get().late_minutes, 10)
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(preview.ready, preview.blockers)
        self.assertEqual(preview.snapshot["gross_deductions"], "0.50")
        self.assertEqual(preview.snapshot["late_hours"], "0.00")
        self.assertEqual(preview.snapshot["net_payable_hours"], "0.50")

    def test_dtr_empty_cutoff_checks_view_direct_deny_on_get_and_post(self):
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:dtr_review")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {}).status_code, 403)
        UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=DTR_VIEW_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {}).status_code, 403)

    def test_dtr_closure_and_mixed_interval_post_routes_enforce_revisioned_checker_decisions(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        self.client.force_login(self.actor)
        cutoff_url = reverse("faculty_attendance:cutoff_review")
        scope = {
            "academic_year": self.academic_year.pk, "term": self.term.pk,
            "start_date": meeting.meeting_date.isoformat(), "end_date": meeting.meeting_date.isoformat(),
        }
        response = self.client.post(cutoff_url, {
            **scope, "action": "closure", "meeting_id": meeting.pk, "expected_revision": 0,
            "status": "CLOSED", "kind": "HOLIDAY", "pay_basis": "REGULAR",
            "reason": "Synthetic posted dated closure and verified basis",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceClosureDecision.objects.get(meeting=meeting).revision, 1)
        UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=CORRECT_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        denied = self.client.post(cutoff_url, {
            **scope, "action": "closure", "meeting_id": meeting.pk, "expected_revision": 1,
            "status": "REVOKED", "kind": "HOLIDAY", "pay_basis": "REGULAR",
            "reason": "Synthetic unauthorized reversal",
        })
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(AttendanceClosureDecision.objects.filter(meeting=meeting).count(), 1)

    def test_dtr_mixed_interval_post_preserves_published_finding_and_denies_unauthorized_edit(self):
        meeting, publication = self._published_dtr_cutoff([
            {"finding_type": "ABSENCE", "segment_key": "partial", "notice_status": "A", "missed_hours": "0.50"},
            {"finding_type": "LATE", "segment_key": "arrival", "minutes": 10},
        ])
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:dtr_review")
        self.assertEqual(self.client.get(url, {"publication": publication.pk, "faculty": self.faculty.pk, "mixed": meeting.pk}).status_code, 200)
        values = {
            "publication": publication.pk, "faculty": self.faculty.pk, "action": "mixed",
            "meeting_id": meeting.pk, "expected_revision": 0,
            "intervals": "A 08:00-08:30", "reason": "Synthetic posted fully overlapping L minutes",
        }
        self.assertEqual(self.client.post(url, values).status_code, 302)
        self.assertEqual(DTRMixedFindingDecision.objects.get(meeting=meeting).revision, 1)
        self.assertEqual(publication.entries.get().late_minutes, 10)
        denial = UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=DTR_EDIT_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        values["expected_revision"] = 1
        self.assertEqual(self.client.post(url, values).status_code, 403)
        self.assertEqual(DTRMixedFindingDecision.objects.filter(meeting=meeting).count(), 1)
        denial.delete()

    def test_dtr_print_sections_and_display_rounding_bridge(self):
        _meeting, publication = self._published_dtr_cutoff()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        final = finalize_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=preview.fingerprint, reason="Synthetic print review",
            faculty_review_complete=True,
        )
        totals = calculate_hour_totals(teaching=Decimal(50) / 60, late=Decimal(1) / 60)
        self.assertEqual(totals["basic_hours"], "0.83")
        self.assertEqual(totals["gross_deductions"], "0.02")
        self.assertEqual(totals["net_payable_hours"], "0.82")
        self.assertEqual(totals["display_rounding_bridge"], "+0.01")
        synthetic = {**final.snapshot, **totals, "paid_teaching_minutes": 50,
                     "scheduled_minutes": 50, "late_minutes": 1, "early_minutes": 0}
        synthetic["lines"] = [{**final.snapshot["lines"][0], "scheduled_minutes": 50,
                               "paid_teaching_minutes": 50, "late_minutes": 1}]
        html = render_to_string("faculty_attendance/dtr_print.html", {
            "final": final, "snapshot": printable_final_snapshot(synthetic),
        })
        for heading in ("Dated teaching schedule", "Dated AC administrative office hours", "Dated deductions and VL / SL / EL offsets"):
            self.assertIn(heading, html)
        self.assertIn("+0.01 display-rounding bridge", html)
        self.assertIn("Net Payable Hours: 0.82", html)

    def test_final_dtr_print_institution_campus_logo_and_id_only_footer(self):
        _meeting, publication = self._published_dtr_cutoff()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        final = finalize_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=preview.fingerprint, reason="Synthetic final print note",
            faculty_review_complete=True,
        )
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "NATIONAL COLLEGE OF BUSINESS AND ARTS")
        self.assertContains(response, f'<div class="campus">{self.campus.name}</div>', html=False)
        self.assertContains(response, 'src="/media/logos/ncba-logo.png"')
        self.assertContains(response, f'<div class="foot">DTR ID {final.public_id}</div>', html=False)
        self.assertNotContains(response, "NCBA | TeacherMate+")
        self.assertNotContains(response, "Finalization note:")
        self.assertNotContains(response, "Synthetic final print note")

    def test_older_final_print_uses_saved_class_rows_and_hours_without_live_recalculation(self):
        _meeting, publication = self._published_dtr_cutoff()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        final = finalize_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=preview.fingerprint, reason="Synthetic historical final print",
            faculty_review_complete=True,
        )
        saved = {**final.snapshot}
        for key in ("class_count", "scheduled_minutes", "paid_teaching_minutes", "admin_count"):
            saved.pop(key, None)
        saved["lines"] = [{key: value for key, value in row.items() if key not in (
            "scheduled_minutes", "paid_teaching_minutes", "late_minutes", "early_minutes",
        )} for row in saved["lines"]]
        final.snapshot = saved
        final.save(update_fields=["snapshot"])
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, saved["lines"][0]["label"])
        self.assertContains(response, saved["lines"][0]["time"])
        self.assertContains(response, saved["lines"][0]["status"])
        self.assertContains(response, "1.00 saved hours")
        self.assertContains(response, "Credited teaching: 1.00 saved hours")
        self.assertNotContains(response, "No scheduled teaching classes")
        final.refresh_from_db()
        self.assertNotIn("class_count", final.snapshot)
        self.assertNotIn("paid_teaching_minutes", final.snapshot)

    def test_final_print_combines_one_late_and_one_early_minute_before_rounding(self):
        _meeting, publication = self._published_dtr_cutoff()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        final = finalize_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=preview.fingerprint, reason="Synthetic minute print",
            faculty_review_complete=True,
        )
        totals = calculate_hour_totals(
            teaching=Decimal("1.00"), late=Decimal(1) / 60, early=Decimal(1) / 60,
        )
        self.assertEqual((totals["basic_hours"], totals["gross_deductions"], totals["net_payable_hours"]),
                         ("1.00", "0.03", "0.97"))
        saved = {**final.snapshot, **totals, "late_minutes": 1, "early_minutes": 1}
        saved["lines"] = [{**final.snapshot["lines"][0], "late_minutes": 1, "early_minutes": 1,
                           "late": "0.02", "early": "0.02"}]
        final.snapshot = saved
        final.save(update_fields=["snapshot"])
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.content.decode().count('<td class="number">1 min</td>'), 2)
        self.assertContains(response, "L 1 min + E 1 min = 0.03 hours (rounded together)")
        self.assertContains(response, "Gross Deductions: 0.03")
        self.assertContains(response, "Basic 1.00 - gross 0.03 + applied VL/SL/EL 0.00")
        self.assertContains(response, "Net Payable Hours: 0.97")
        self.assertNotContains(response, "L 1 min / 60 = 0.02")

    def test_dtr_reference_hour_calculations_and_final_total_rounding(self):
        faculty_example = calculate_hour_totals(
            teaching=Decimal("30.00"), a=Decimal("1.00"),
            late=Decimal(24) / 60, early=Decimal(30) / 60,
        )
        self.assertEqual(faculty_example["gross_deductions"], "1.90")
        self.assertEqual(faculty_example["net_payable_hours"], "28.10")
        ac_example = calculate_hour_totals(teaching=Decimal("52.50"), admin=Decimal("26.40"))
        self.assertEqual(ac_example["basic_hours"], "78.90")
        self.assertEqual(ac_example["net_payable_hours"], "78.90")
        one_minute = calculate_hour_totals(teaching=Decimal("1"), late=Decimal(1) / 60)
        two_minutes = calculate_hour_totals(teaching=Decimal("1"), late=Decimal(2) / 60)
        self.assertEqual(one_minute["late_hours"], "0.02")
        self.assertEqual(two_minutes["late_hours"], "0.03")

    def test_dtr_combined_meeting_counts_once_and_ac_hours_are_dated(self):
        meeting, publication = self._published_dtr_cutoff()
        self.assertEqual(publication.entries.count(), 1)
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertEqual(preview.snapshot["teaching_hours"], "1.00")
        self.assertEqual(len(preview.snapshot["lines"][0]["sections"]), 2)
        ac_role = Role.objects.create(code="AC", name="Area Chair synthetic")
        UserRole.objects.create(user=self.faculty, role=ac_role, tenant=self.tenant, campus=self.campus, department=self.department)
        UserRole.objects.create(user=self.actor, role=ac_role, tenant=self.tenant, campus=self.campus, department=self.department)
        admin = self._dtr_entry(publication, kind="ADMIN", hours="0.50", reason="Synthetic office hours")
        self.assertEqual(admin.entry_date, meeting.meeting_date)
        with self.assertRaisesMessage(ValidationError, "already exist"):
            self._dtr_entry(publication, kind="ADMIN", hours="0.50", reason="Duplicate synthetic hours")
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertEqual(preview.snapshot["basic_hours"], "1.50")
        final = finalize_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=preview.fingerprint, reason="Reviewed synthetic AC hours",
            faculty_review_complete=True,
        )
        self.assertEqual(final.snapshot["net_payable_hours"], "1.50")
        self.assertEqual(ac_department_summary(actor=self.actor, publication=publication, department=self.department)[0]["net"], "1.50")
        with self.assertRaises(PermissionDenied):
            ac_department_summary(actor=self.actor, publication=publication, department=self.other_department)
        self.assertEqual(len(checker_summary(actor=self.actor, publication=publication)), 1)

    def test_dtr_checker_form_posts_save_and_finalize_reviewed_hours(self):
        _meeting, publication = self._published_dtr_cutoff()
        self.client.force_login(self.actor)
        invalid = self.client.post(reverse("faculty_attendance:dtr_review"), {
            "action": "adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_date": (publication.end_date + timedelta(days=1)).isoformat(), "department": self.department.pk,
            "kind": "OTHER", "hours": "0.10", "reason": "Synthetic invalid normal fallback",
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Date must be within this cutoff")
        response = self.client.post(reverse("faculty_attendance:dtr_review"), {
            "action": "adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_date": publication.start_date.isoformat(), "department": self.department.pk,
            "kind": "OTHER", "hours": "0.10", "reason": "Synthetic reviewed deduction",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DTRAdjustment.objects.filter(kind="OTHER", faculty_user=self.faculty).count(), 1)
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertEqual(preview.snapshot["net_payable_hours"], "0.90")
        response = self.client.post(reverse("faculty_attendance:dtr_review"), {
            "action": "finalize", "publication": publication.pk, "faculty": self.faculty.pk,
            "expected_fingerprint": preview.fingerprint, "faculty_review_complete": "on",
            "reason": "Synthetic checker review complete",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(FacultyDTR.objects.get(faculty_user=self.faculty).snapshot["net_payable_hours"], "0.90")

    def test_dtr_ajax_selection_adjustment_correction_and_finalization(self):
        _meeting, publication = self._published_dtr_cutoff()
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:dtr_review")
        ajax_headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest", "HTTP_ACCEPT": "application/json"}

        selected = self.client.get(url, {
            "publication": publication.pk, "faculty": self.faculty.pk, "partial": "faculty",
        }, **ajax_headers)
        self.assertEqual(selected.status_code, 200)
        self.assertTrue(selected.json()["ok"])
        self.assertEqual(selected.json()["message"], "Faculty DTR loaded.")
        self.assertIn('id="faculty-dtr-card"', selected.json()["faculty_html"])
        self.assertGreaterEqual(
            selected.json()["faculty_html"].count('action="/admin-portal/faculty-attendance/dtr/"'), 2,
        )

        outside_date = publication.end_date + timedelta(days=1)
        invalid = self.client.post(url, {
            "action": "adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_date": outside_date.isoformat(), "department": self.department.pk,
            "kind": "OTHER", "hours": "0.10", "reason": "Synthetic out-of-range entry",
        }, **ajax_headers)
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(invalid.json()["ok"])
        self.assertIn("Date must be within this cutoff", invalid.json()["faculty_html"])
        self.assertIn(outside_date.isoformat(), invalid.json()["faculty_html"])

        saved = self.client.post(url, {
            "action": "adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_date": publication.start_date.isoformat(), "department": self.department.pk,
            "kind": "OTHER", "hours": "0.10", "reason": "Synthetic AJAX deduction",
        }, **ajax_headers)
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(saved.json()["ok"])
        current = DTRAdjustment.objects.get(faculty_user=self.faculty, kind="OTHER")
        self.assertIn("Entry R1", saved.json()["faculty_html"])

        corrected = self.client.post(url, {
            "action": "adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_date": publication.start_date.isoformat(), "department": self.department.pk,
            "kind": "OTHER", "hours": "0.20", "reason": "Synthetic AJAX correction",
            "previous_id": current.pk, "expected_revision": current.revision,
        }, **ajax_headers)
        self.assertEqual(corrected.status_code, 200)
        self.assertTrue(corrected.json()["ok"])
        self.assertIn("Entry R2", corrected.json()["faculty_html"])
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertEqual(preview.snapshot["net_payable_hours"], "0.80")

        finalized = self.client.post(url, {
            "action": "finalize", "publication": publication.pk, "faculty": self.faculty.pk,
            "expected_fingerprint": preview.fingerprint, "faculty_review_complete": "on",
            "reason": "Synthetic AJAX finalization",
        }, **ajax_headers)
        self.assertEqual(finalized.status_code, 200)
        self.assertTrue(finalized.json()["ok"])
        self.assertIn("Final DTR R1", finalized.json()["faculty_html"])
        self.assertIn("R1", finalized.json()["summary_row_html"])

    def test_dtr_remove_active_entries_is_revisioned_and_requires_new_final(self):
        _meeting, publication = self._published_dtr_cutoff()
        ac_role = Role.objects.create(code="AC", name="Area Chair synthetic")
        UserRole.objects.create(
            user=self.faculty, role=ac_role, tenant=self.tenant,
            campus=self.campus, department=self.department,
        )
        admin = self._dtr_entry(
            publication, kind="ADMIN", hours="0.50", reason="Synthetic active AC hours",
        )
        other = self._dtr_entry(
            publication, kind="OTHER", hours="0.25", reason="Synthetic active other deduction",
        )
        leave = self._dtr_entry(
            publication, kind="LEAVE", hours="0.10", leave_type="VL", offset_kind="OTHER",
            reason="Synthetic active leave credit",
        )
        initial_preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(initial_preview.ready, initial_preview.blockers)
        first_final = finalize_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=initial_preview.fingerprint, reason="Synthetic first final before removal",
            faculty_review_complete=True,
        )
        self.assertEqual(first_final.revision, 1)
        self.assertEqual(first_final.snapshot["basic_hours"], "1.50")

        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:dtr_review")
        ajax_headers = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest", "HTTP_ACCEPT": "application/json"}
        invalid = self.client.post(url, {
            "action": "remove_adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_id": admin.pk, "expected_revision": admin.revision,
            "reason": "Synthetic missing confirmation",
        }, **ajax_headers)
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("Confirm removal", invalid.json()["faculty_html"])
        self.assertEqual(DTRAdjustment.objects.filter(entry_key=admin.entry_key).count(), 1)

        for item in (admin, other, leave):
            response = self.client.post(url, {
                "action": "remove_adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
                "entry_id": item.pk, "expected_revision": item.revision,
                "reason": "" if item.kind == "ADMIN" else f"Synthetic removal of {item.kind} entry",
                "confirm_removal": "on",
            }, **ajax_headers)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.json()["ok"])
            latest = DTRAdjustment.objects.get(entry_key=item.entry_key, revision=2)
            self.assertEqual(latest.hours, Decimal("0.00"))
            self.assertEqual(latest.supersedes_id, item.pk)
            self.assertIn("Removed", response.json()["faculty_html"])
            self.assertIn("Entry revision history", response.json()["faculty_html"])

        self.assertEqual(DTRAdjustment.objects.filter(hours=0).count(), 3)
        current = current_adjustments(publication=publication, faculty=self.faculty)
        self.assertEqual({item.revision for item in current}, {2})
        self.assertEqual(preview_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
        ).snapshot["basic_hours"], "1.00")
        current_preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(current_preview.ready, current_preview.blockers)
        self.assertNotEqual(current_preview.fingerprint, first_final.review_fingerprint)
        self.assertEqual(first_final.snapshot["basic_hours"], "1.50")

        finalized = self.client.post(url, {
            "action": "finalize", "publication": publication.pk, "faculty": self.faculty.pk,
            "expected_fingerprint": current_preview.fingerprint, "faculty_review_complete": "on",
            "reason": "Synthetic review after removal",
        }, **ajax_headers)
        self.assertEqual(finalized.status_code, 200)
        self.assertTrue(finalized.json()["ok"])
        self.assertIn("Final DTR R2", finalized.json()["faculty_html"])
        self.assertEqual(FacultyDTR.objects.get(revision=2).snapshot["basic_hours"], "1.00")
        self.assertEqual(FacultyDTR.objects.get(revision=1).snapshot["basic_hours"], "1.50")

    def test_dtr_remove_entry_respects_direct_deny_and_normal_post_fallback(self):
        _meeting, publication = self._published_dtr_cutoff()
        entry = self._dtr_entry(
            publication, kind="OTHER", hours="0.10", reason="Synthetic removable deduction",
        )
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:dtr_review")
        denial = UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=DTR_EDIT_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        denied = self.client.post(url, {
            "action": "remove_adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_id": entry.pk, "expected_revision": entry.revision,
            "reason": "Synthetic denied removal", "confirm_removal": "on",
        })
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(DTRAdjustment.objects.filter(entry_key=entry.entry_key).count(), 1)
        denial.delete()
        fallback = self.client.post(url, {
            "action": "remove_adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_id": entry.pk, "expected_revision": entry.revision,
            "reason": "Synthetic normal fallback removal", "confirm_removal": "on",
        })
        self.assertEqual(fallback.status_code, 302)
        self.assertEqual(DTRAdjustment.objects.get(entry_key=entry.entry_key, revision=2).hours, Decimal("0.00"))

    def test_dtr_notes_are_optional_but_confirmation_and_attestation_remain_required(self):
        optional_note_fields = (
            (attendance_forms.RecurringCombinedClassForm, "reason"),
            (attendance_forms.ScheduleCorrectionForm, "correction_reason"),
            (attendance_forms.CoverageForm, "reason"),
            (attendance_forms.SubstitutionForm, "reason"),
            (attendance_forms.ExceptionEncodingForm, "reason"),
            (attendance_forms.ReconciliationForm, "reason"),
            (attendance_forms.CoverageReconciliationForm, "reason"),
            (attendance_forms.CutoffPublicationForm, "publication_reason"),
            (attendance_forms.SourceChangeReconciliationForm, "reason"),
            (attendance_forms.DTRAdjustmentForm, "reason"),
            (attendance_forms.DTRAdjustmentRemovalForm, "reason"),
            (attendance_forms.DTREarlyCorrectionForm, "reason"),
            (attendance_forms.DTRFinalizationForm, "reason"),
            (attendance_forms.AttendanceClosureForm, "reason"),
            (attendance_forms.DTRMixedFindingForm, "reason"),
        )
        for form_class, field_name in optional_note_fields:
            with self.subTest(form=form_class.__name__, field=field_name):
                self.assertFalse(form_class().fields[field_name].required)
        self.assertTrue(attendance_forms.DTRFinalizationForm().fields["faculty_review_complete"].required)
        self.assertTrue(attendance_forms.DTRAdjustmentRemovalForm().fields["confirm_removal"].required)

        meeting, publication = self._published_dtr_cutoff([
            {"finding_type": "EARLY", "segment_key": "departure", "minutes": 5},
        ])
        entry = self._dtr_entry(publication, kind="OTHER", hours="0.10", reason="")
        self.assertEqual(entry.reason, "")
        corrected = self._dtr_entry(
            publication, kind="OTHER", hours="0.20", reason="", previous=entry, expected_revision=entry.revision,
        )
        self.assertEqual(corrected.reason, "")
        result = AttendanceResult.objects.get(meeting=meeting)
        attendance_revision = AttendanceResultService.correct_early_dismissal(
            actor=self.actor, result=result, expected_revision=result.revision, minutes=6, reason="",
        )
        self.assertEqual(attendance_revision.correction_reason, "")
        self.assertEqual(attendance_revision.history.latest("revision").change_reason, "")

        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        with self.assertRaisesMessage(ValidationError, "faculty review"):
            finalize_dtr(
                actor=self.actor, publication=publication, faculty=self.faculty,
                expected_fingerprint=preview.fingerprint, reason="", faculty_review_complete=False,
            )

    def test_stale_remove_get_is_safe_notice_and_stale_post_does_not_create_revision(self):
        _meeting, publication = self._published_dtr_cutoff()
        entry = self._dtr_entry(publication, kind="OTHER", hours="0.10", reason="Synthetic active entry")
        self.client.force_login(self.actor)
        url = reverse("faculty_attendance:dtr_review")
        query = {"publication": publication.pk, "faculty": self.faculty.pk, "remove": entry.pk}
        confirm = self.client.get(url, query)
        self.assertEqual(confirm.status_code, 200)
        self.assertContains(confirm, "Confirm removal of active entry")

        removed = self.client.post(url, {
            "action": "remove_adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_id": entry.pk, "expected_revision": entry.revision,
            "reason": "Synthetic confirmed removal", "confirm_removal": "on",
        })
        self.assertEqual(removed.status_code, 302)
        stale_get = self.client.get(url, query)
        self.assertEqual(stale_get.status_code, 200)
        self.assertContains(stale_get, "That checker entry is no longer active")
        self.assertNotContains(stale_get, "Confirm removal of active entry")

        stale_post = self.client.post(url, {
            "action": "remove_adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_id": entry.pk, "expected_revision": entry.revision,
            "reason": "Synthetic stale attempt", "confirm_removal": "on",
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_ACCEPT="application/json")
        self.assertEqual(stale_post.status_code, 400)
        self.assertIn("That checker entry changed after its removal link was opened", stale_post.json()["faculty_html"])
        self.assertEqual(DTRAdjustment.objects.filter(entry_key=entry.entry_key).count(), 2)

    def test_dtr_review_displays_validation_by_field_and_prints_in_new_tab(self):
        _meeting, publication = self._published_dtr_cutoff()
        self.client.force_login(self.actor)
        response = self.client.post(reverse("faculty_attendance:dtr_review"), {
            "action": "adjustment", "publication": publication.pk, "faculty": self.faculty.pk,
            "entry_date": publication.start_date.isoformat(), "department": self.department.pk,
            "kind": "ADMIN", "hours": "0.50", "reason": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This faculty has no active AC assignment")
        html = response.content.decode()
        department_start = html.index('name="department"')
        kind_start = html.index('name="kind"', department_start)
        hours_start = html.index('name="hours"', kind_start)
        self.assertIn("errorlist", html[department_start:kind_start])
        self.assertIn("errorlist", html[kind_start:hours_start])

        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        final = finalize_dtr(
            actor=self.actor, publication=publication, faculty=self.faculty,
            expected_fingerprint=preview.fingerprint, reason="", faculty_review_complete=True,
        )
        response = self.client.get(reverse("faculty_attendance:dtr_review"), {
            "publication": publication.pk, "faculty": self.faculty.pk,
        })
        self.assertContains(response, "This DTR is already final at the reviewed version")
        self.assertNotContains(response, "Finalize DTR hours")
        self.assertContains(response, f'href="/admin-portal/faculty-attendance/dtr/{final.public_id}/print/" target="_blank" rel="noopener noreferrer"')
        self.assertEqual(final.finalization_reason, "")

    def test_dtr_absence_late_early_other_and_leave_never_double_credit(self):
        meeting, publication = self._published_dtr_cutoff([
            {"finding_type": "ABSENCE", "segment_key": "partial", "notice_status": "A", "missed_hours": "0.50"},
            {"finding_type": "LATE", "segment_key": "arrival", "minutes": 12},
            {"finding_type": "EARLY", "segment_key": "departure", "minutes": 6},
        ])
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertFalse(preview.ready)
        self.assertIn("non-overlapping actual intervals", str(preview.blockers))
        save_mixed_decision(
            actor=self.actor, publication=publication, faculty=self.faculty, meeting=meeting,
            intervals_text="A 08:00-08:30\nL 08:30-08:42\nE 08:54-09:00",
            reason="Synthetic checker-verified separate missed spans", expected_revision=0,
        )
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(preview.ready, preview.blockers)
        self.assertEqual(preview.snapshot["gross_deductions"], "0.80")
        self.assertEqual(preview.snapshot["net_payable_hours"], "0.20")
        self._dtr_entry(publication, kind="LEAVE", hours="0.30", leave_type="VL", offset_kind="A", reason="Synthetic VL offset")
        excess = self._dtr_entry(publication, kind="LEAVE", hours="0.20", leave_type="SL", offset_kind="E", reason="Synthetic excess SL")
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertEqual(preview.snapshot["leave_credit"], "0.40")
        self.assertEqual(preview.snapshot["net_payable_hours"], "0.60")
        self.assertFalse(preview.ready)
        with self.assertRaisesMessage(ValidationError, "Resolve DTR blockers"):
            finalize_dtr(
                actor=self.actor, publication=publication, faculty=self.faculty,
                expected_fingerprint=preview.fingerprint, reason="Must not finalize excess credit",
                faculty_review_complete=True,
            )
        revised = self._dtr_entry(
            publication, kind="LEAVE", hours="0.10", leave_type="SL", offset_kind="E",
            reason="Synthetic corrected SL", previous=excess, expected_revision=1,
        )
        self.assertEqual(revised.revision, 2)
        self.assertEqual(DTRAdjustment.objects.filter(entry_key=excess.entry_key).count(), 2)
        self._dtr_entry(publication, kind="OTHER", hours="0.10", reason="Synthetic authorized other deduction")
        self._dtr_entry(publication, kind="LEAVE", hours="0.10", leave_type="EL", offset_kind="OTHER", reason="Synthetic EL offset")
        ready = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(ready.ready, ready.blockers)
        self.assertEqual(ready.snapshot["gross_deductions"], "0.90")
        self.assertEqual(ready.snapshot["leave_credit"], "0.50")
        self.assertEqual(ready.snapshot["net_payable_hours"], "0.60")

    def test_dtr_notified_absence_hours_are_separate_from_unnotified_hours(self):
        _meeting, publication = self._published_dtr_cutoff([
            {"finding_type": "ABSENCE", "segment_key": "notified", "notice_status": "N", "missed_hours": "0.50"},
        ])
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertTrue(preview.ready, preview.blockers)
        self.assertEqual(preview.snapshot["n_hours"], "0.50")
        self.assertEqual(preview.snapshot["a_hours"], "0.00")
        self.assertEqual(preview.snapshot["gross_deductions"], "0.50")
        self.assertEqual(preview.snapshot["net_payable_hours"], "0.50")

    def test_dtr_early_correction_replaces_finding_and_requires_republication(self):
        meeting, first = self._published_dtr_cutoff([
            {"finding_type": "LATE", "segment_key": "arrival", "minutes": 6},
            {"finding_type": "EARLY", "segment_key": "departure", "minutes": 6},
        ])
        before = preview_dtr(actor=self.actor, publication=first, faculty=self.faculty)
        first_final = finalize_dtr(
            actor=self.actor, publication=first, faculty=self.faculty,
            expected_fingerprint=before.fingerprint, reason="First reviewed DTR",
            faculty_review_complete=True,
        )
        result = AttendanceResult.objects.get(meeting=meeting)
        corrected = AttendanceResultService.correct_early_dismissal(
            actor=self.actor, result=result, expected_revision=1, minutes=12,
            reason="Synthetic checker ED correction",
        )
        self.assertEqual(corrected.revision, 2)
        self.assertEqual(corrected.early_minutes, 12)
        self.assertEqual(corrected.late_minutes, 6)
        self.assertEqual(list(result.history.values_list("revision", flat=True)), [1, 2])
        self.assertEqual(first.entries.get().early_minutes, 6)
        with self.assertRaises(StaleAttendanceReview):
            AttendanceResultService.correct_early_dismissal(
                actor=self.actor, result=result, expected_revision=1, minutes=13,
                reason="Stale synthetic correction",
            )
        stale = preview_dtr(actor=self.actor, publication=first, faculty=self.faculty)
        self.assertFalse(stale.ready)
        review = review_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=meeting.meeting_date, end_date=meeting.meeting_date,
        )
        second = publish_cutoff(
            actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term,
            start_date=meeting.meeting_date, end_date=meeting.meeting_date,
            expected_fingerprint=review.fingerprint, submission_key="synthetic-dtr-republish",
            publication_reason="Synthetic republish after ED correction",
        )
        current = preview_dtr(actor=self.actor, publication=second, faculty=self.faculty)
        self.assertEqual(current.snapshot["early_hours"], "0.20")
        self.assertEqual(current.snapshot["gross_deductions"], "0.30")
        second_final = finalize_dtr(
            actor=self.actor, publication=second, faculty=self.faculty,
            expected_fingerprint=current.fingerprint, reason="Reviewed revised DTR",
            faculty_review_complete=True,
        )
        self.assertEqual(second_final.revision, 2)
        self.assertEqual(first_final.snapshot["early_hours"], "0.10")
        self.assertEqual(first_final.publication_id, first.pk)
        self.assertEqual(second_final.supersedes_id, first_final.pk)

    def test_dtr_finalization_blocks_stale_inputs_and_requires_review(self):
        _meeting, publication = self._published_dtr_cutoff()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        with self.assertRaisesMessage(ValidationError, "faculty review"):
            finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                         expected_fingerprint=preview.fingerprint, reason="Premature", faculty_review_complete=False)
        self._dtr_entry(publication, kind="OTHER", hours="0.10", reason="Synthetic authorized other deduction")
        with self.assertRaisesMessage(ValidationError, "inputs changed"):
            finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                         expected_fingerprint=preview.fingerprint, reason="Stale", faculty_review_complete=True)
        current = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        final = finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                             expected_fingerprint=current.fingerprint, reason="Current reviewed state", faculty_review_complete=True)
        self.assertEqual(final.snapshot["net_payable_hours"], "0.90")
        with self.assertRaisesMessage(ValidationError, "already final"):
            finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                         expected_fingerprint=current.fingerprint, reason="Duplicate", faculty_review_complete=True)
        self._dtr_entry(publication, kind="LEAVE", hours="0.10", leave_type="VL", offset_kind="OTHER", reason="Synthetic approved leave")
        revised_preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        revised = finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                               expected_fingerprint=revised_preview.fingerprint, reason="Revised checked leave", faculty_review_complete=True)
        self.assertEqual(revised.revision, 2)
        self.assertEqual(revised.snapshot["net_payable_hours"], "1.00")
        self.assertEqual(final.snapshot["net_payable_hours"], "0.90")
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_ATTENDANCE_DTR_FINALIZED", entity_id=str(revised.pk)).exists())

    def test_faculty_dashboard_role_scope_allows_empty_my_dtr_and_denies_checker_page(self):
        faculty_role = Role.objects.create(code="FACULTY_DTR_DEMO_TEST", name="Faculty DTR demo")
        faculty_access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access", defaults={"module": "faculty_portal", "action": "access"},
        )
        dashboard_read, _ = Permission.objects.get_or_create(
            code="dashboard.read", defaults={"module": "dashboard", "action": "read"},
        )
        for permission in (faculty_access, dashboard_read, Permission.objects.get(code=VIEW_PERMISSION)):
            RolePermission.objects.create(role=faculty_role, permission=permission)
        UserRole.objects.create(
            user=self.faculty, role=faculty_role, tenant=self.tenant,
            campus=self.campus, department=self.department,
        )
        SystemSetting.objects.update_or_create(
            tenant=self.tenant,
            setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            defaults={"setting_value": "true", "value_type": SystemSetting.ValueType.BOOL, "is_active": True},
        )
        self.faculty.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.faculty.privacy_consent_at = timezone.now()
        self.faculty.save(update_fields=["privacy_consent_version", "privacy_consent_at"])

        login = self.client.post(
            reverse("faculty_portal:public_index"), {"username": "faculty", "password": "x"}, follow=False,
        )
        self.assertRedirects(login, reverse("faculty_portal:dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("faculty_portal:dashboard")).status_code, 200)
        my_dtr = self.client.get(reverse("faculty_attendance:my_dtr"))
        self.assertEqual(my_dtr.status_code, 200)
        self.assertContains(my_dtr, "No finalized DTR is available in this campus yet.")
        self.assertEqual(self.client.get(reverse("faculty_attendance:dtr_review")).status_code, 403)

        UserPermission.objects.create(
            user=self.faculty, permission=dashboard_read, grant_type=UserPermission.GrantType.DENY,
            tenant=self.tenant, campus=self.campus,
        )
        self.assertEqual(self.client.get(reverse("faculty_portal:dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("faculty_attendance:my_dtr")).status_code, 200)

    def test_dtr_direct_deny_faculty_ownership_and_print_rendering(self):
        _meeting, publication = self._published_dtr_cutoff()
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        final = finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                             expected_fingerprint=preview.fingerprint, reason="Synthetic checked final", faculty_review_complete=True)
        self.client.force_login(self.actor)
        review_response = self.client.get(reverse("faculty_attendance:dtr_review"), {"publication": publication.pk, "faculty": self.faculty.pk})
        self.assertEqual(review_response.status_code, 200)
        self.assertContains(review_response, "Basic Hours")
        self.assertEqual(self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id])).status_code, 200)
        print_denial = UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=DTR_PRINT_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        self.assertEqual(self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id])).status_code, 403)
        print_denial.delete()
        self.assertEqual(self.client.get(reverse("faculty_attendance:dtr_summary"), {"publication": publication.pk}).status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="FACULTY_ATTENDANCE_DTR_FINALIZED").exists())
        denial = UserPermission.objects.create(
            user=self.actor, permission=Permission.objects.get(code=DTR_VIEW_PERMISSION),
            grant_type=UserPermission.GrantType.DENY, tenant=self.tenant, campus=self.campus,
        )
        with self.assertRaises(PermissionDenied):
            preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertEqual(self.client.get(reverse("faculty_attendance:dtr_review"), {"publication": publication.pk}).status_code, 403)
        denial.delete()
        with self.assertRaises(PermissionDenied):
            faculty_final_dtr(user=self.replacement, final=final)
        with self.assertRaises(PermissionDenied):
            faculty_final_dtr(user=self.faculty, final=final)
        SystemSetting.objects.create(
            tenant=self.tenant, setting_key=FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            setting_value="true", value_type=SystemSetting.ValueType.BOOL,
        )
        UserPermission.objects.create(
            user=self.faculty, permission=Permission.objects.get(code=VIEW_PERMISSION),
            grant_type=UserPermission.GrantType.ALLOW, tenant=self.tenant, campus=self.campus,
        )
        faculty_portal_access, _ = Permission.objects.get_or_create(
            code="faculty_portal.access", defaults={"module": "faculty_portal", "action": "access"},
        )
        UserPermission.objects.create(
            user=self.faculty, permission=faculty_portal_access,
            grant_type=UserPermission.GrantType.ALLOW, tenant=self.tenant, campus=self.campus,
        )
        self.faculty.privacy_consent_version = getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03")
        self.faculty.privacy_consent_at = timezone.now()
        self.faculty.save(update_fields=["privacy_consent_version", "privacy_consent_at"])
        self.assertEqual(faculty_final_dtr(user=self.faculty, final=final).pk, final.pk)
        self.client.force_login(self.faculty)
        self.assertEqual(self.client.get(reverse("faculty_attendance:my_dtr_print", args=[final.public_id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("faculty_attendance:my_dtr")).status_code, 200)
        self.client.force_login(self.replacement)
        self.assertEqual(self.client.get(reverse("faculty_attendance:my_dtr_print", args=[final.public_id])).status_code, 403)
