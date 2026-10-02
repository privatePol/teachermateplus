"""Focused AY/term, published warnings and in-app notice regression cases."""
from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.rbac.models import Permission, Role, RolePermission, UserPermission, UserRole
from apps.tenants.models import SystemSetting
from . import tests as existing
from .closures import save_closure
from .cutoffs import faculty_published_entries, publish_cutoff, published_tardiness_summary, review_cutoff
from .dtr import finalize_dtr, preview_dtr, save_adjustment
from .dtr_intervals import save_mixed_decision
from .models import AttendanceResult, AttendanceStaffNotice, FacultyDTR
from .monitoring import authorized_departments, term_summary
from .observations import AttendanceResultService, CheckingRoundService, ObservationService
from .permissions import VIEW_PERMISSION
from .services import CoverageService, MeetingService, SubstitutionService
from .staff_notices import current_notices, refresh_for_meeting


class TermMonitoringTests(TestCase):
    # Reuse setup/helpers without inheriting unrelated test methods or rerunning a broad suite.
    permission_codes = existing.FacultyAttendanceFoundationTests.permission_codes
    setUpTestData = classmethod(existing.FacultyAttendanceFoundationTests.setUpTestData.__func__)
    setUp = existing.FacultyAttendanceFoundationTests.setUp
    aware = existing.FacultyAttendanceFoundationTests.aware
    schedule = existing.FacultyAttendanceFoundationTests.schedule
    coverage = existing.FacultyAttendanceFoundationTests.coverage
    meeting = existing.FacultyAttendanceFoundationTests.meeting
    _published_dtr_cutoff = existing.FacultyAttendanceFoundationTests._published_dtr_cutoff

    def report(self, as_of=date(2026, 1, 5), actor=None):
        return term_summary(actor=actor or self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                            academic_year=self.academic_year, term=self.term, as_of=as_of)

    def result(self, meeting, findings=None, revision=0):
        round_ = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date)
        if findings is None and revision == 0:
            AttendanceResultService.confirm_present(actor=self.actor, checking_round=round_, manifest_revision=1,
                reviewed_rows=[{'meeting_id': meeting.pk, 'result_revision': 0}])
            return AttendanceResult.objects.get(meeting=meeting)
        observation = ObservationService.record(actor=self.actor, checking_round=round_, meeting_id=meeting.pk,
            manifest_revision=1, submission_key=f'monitor-{meeting.pk}-{revision}', findings=findings)
        return AttendanceResultService.select_observation(actor=self.actor, observation=observation,
                                                          expected_revision=revision, reason='')

    def faculty_visibility(self):
        SystemSetting.objects.create(tenant=self.tenant,
            setting_key=existing.FeatureSettingsService.FACULTY_ATTENDANCE_FACULTY_VISIBILITY_ENABLED_KEY,
            setting_value='true', value_type='BOOL')
        UserPermission.objects.create(user=self.faculty, permission=Permission.objects.get(code=VIEW_PERMISSION),
                                      grant_type='ALLOW', tenant=self.tenant, campus=self.campus)

    def test_unprepared_expected_hours_are_not_verified_and_get_is_read_only(self):
        self.coverage()
        before = AttendanceResult.objects.count()
        report = self.report()
        row = report['rows'][0]
        self.assertEqual((row['expected'], row['actual'], row['unverified']), (Decimal('1.00'), Decimal('0.00'), 1))
        self.assertIsNone(row['latest_verified'])
        self.assertTrue(report['provisional'])
        self.assertEqual(AttendanceResult.objects.count(), before)
        self.assertFalse(existing.TeachingMeeting.objects.exists())
        self.client.force_login(self.actor)
        response = self.client.get(reverse('faculty_attendance:term_summary'), {
            'academic_year': self.academic_year.pk, 'term': self.term.pk, 'as_of': '2026-01-05'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Provisional as of')
        self.assertFalse(AttendanceStaffNotice.objects.exists())

    def test_prepared_unverified_stays_expected_only_and_cannot_publish(self):
        meeting = self.meeting()
        row = self.report()['rows'][0]
        self.assertEqual((row['expected'], row['actual'], row['unverified']), (Decimal('1'), Decimal('0'), 1))
        review = review_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=meeting.meeting_date, end_date=meeting.meeting_date)
        self.assertFalse(review.ready)
        with self.assertRaises(ValidationError):
            publish_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                academic_year=self.academic_year, term=self.term, start_date=meeting.meeting_date,
                end_date=meeting.meeting_date, expected_fingerprint=review.fingerprint,
                submission_key='unverified', publication_reason='')

    def test_malformed_unverified_publication_basic_is_provisional_not_final(self):
        meeting, publication = self._published_dtr_cutoff()
        result = AttendanceResult.objects.get(meeting=meeting)
        result.status = 'UNVERIFIED'; result.save(update_fields=['status'])
        publication.entries.filter(meeting=meeting).update(status='UNVERIFIED')
        preview = preview_dtr(actor=self.actor, publication=publication, faculty=self.faculty)
        self.assertEqual(preview.snapshot['basic_hours'], '1.00')
        self.assertEqual(preview.snapshot['net_payable_hours'], '1.00')
        self.assertIn('attendance remains unverified', ' '.join(preview.blockers))
        with self.assertRaises(ValidationError):
            finalize_dtr(actor=self.actor, publication=publication, faculty=self.faculty,
                expected_fingerprint=preview.fingerprint, reason='', faculty_review_complete=True)
        self.assertFalse(FacultyDTR.objects.exists())

    def test_verified_teaching_and_detail_keep_minutes_and_class_date(self):
        meeting = self.meeting()
        self.result(meeting, [{'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 1},
                              {'finding_type': 'EARLY', 'segment_key': 'dismissal', 'minutes': 1}])
        row = self.report()['rows'][0]
        self.assertEqual(row['actual'], Decimal('0.97'))
        self.assertEqual(row['expected'], Decimal('1.00'))
        self.assertEqual(row['latest_verified'], date(2026, 1, 5))
        self.client.force_login(self.actor)
        response = self.client.get(reverse('faculty_attendance:term_faculty_details', args=[self.faculty.pk]),
            {'academic_year': self.academic_year.pk, 'term': self.term.pk, 'as_of': '2026-01-05'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MATH101 / S1')
        self.assertContains(response, 'Late min')
        self.assertContains(response, 'Attendance R1')

    def test_term_asof_boundary_excludes_future_and_other_scopes(self):
        self.coverage()
        self.assertFalse(self.report(as_of=date(2025, 12, 31))['rows'])
        report = self.report(as_of=date(2026, 1, 12))
        self.assertEqual(report['rows'][0]['expected'], Decimal('2.00'))
        self.assertEqual(report['rows'][0]['actual'], Decimal('0.00'))
        with patch('apps.faculty_attendance.monitoring.timezone.localdate', return_value=date(2026, 1, 5)):
            self.assertEqual(self.report(as_of=date(2026, 1, 12))['as_of'], date(2026, 1, 5))
        wrong = existing.AcademicYear.objects.create(tenant=self.tenant, code='OTHER', name='Other',
                                                     start_date=date(2025, 1, 1), end_date=date(2027, 1, 1))
        with self.assertRaises(PermissionDenied):
            term_summary(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                         academic_year=wrong, term=self.term, as_of=date(2026, 1, 5))

    def test_combined_meeting_counted_once(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        self.result(meeting)
        report = self.report()
        self.assertEqual(len(report['rows']), 1)
        self.assertEqual(report['rows'][0]['expected'], Decimal('1.00'))
        self.assertEqual(report['rows'][0]['actual'], Decimal('1.00'))
        self.assertEqual(len(report['rows'][0]['details']), 1)
        self.assertIn(self.section2.code, report['rows'][0]['details'][0]['label'])

    def test_saved_attribution_and_inactive_history_survive_reassignment(self):
        meeting = self.meeting()
        result = self.result(meeting)
        self.faculty.is_active = False; self.faculty.save(update_fields=['is_active'])
        self.coverage(faculty=self.replacement, effective_from=self.aware(2026, 1, 6), supersede_current=True)
        row = self.report()['rows'][0]
        self.assertEqual(row['faculty'].pk, result.faculty_user_id)
        self.assertEqual(row['actual'], Decimal('1.00'))

    def test_explicit_substitute_is_attributed_without_permanent_replacement(self):
        meeting = self.meeting()
        SubstitutionService.assign(actor=self.actor, meeting=meeting, substitute_faculty=self.replacement, reason='')
        result = self.result(meeting)
        self.assertEqual(self.report()['rows'][0]['faculty'].pk, result.faculty_user_id)
        self.assertEqual(result.faculty_user_id, self.replacement.pk)

    def test_conflicting_combined_coverage_stays_unresolved(self):
        meeting = self.meeting(offerings=[self.combined_offering])
        existing.TeachingMeeting.objects.filter(pk=meeting.pk).update(faculty_user=None, unresolved_coverage=True)
        row = self.report()['rows'][0]
        self.assertIsNone(row['faculty'])
        self.assertEqual(row['actual'], 0)
        self.assertEqual(row['unverified'], 1)

    def test_admin_leave_closure_and_logical_revisions_stay_separate(self):
        meeting, publication = self._published_dtr_cutoff(findings=[
            {'finding_type': 'ABSENCE', 'segment_key': 'missed', 'notice_status': 'A', 'missed_hours': '0.50'}])
        ac, _ = Role.objects.get_or_create(code='AC', defaults={'name': 'AC'})
        UserRole.objects.create(user=self.faculty, role=ac, tenant=self.tenant, campus=self.campus, department=self.department)
        admin = save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
            department=self.department, entry_date=meeting.meeting_date, kind='ADMIN', hours='0.50', reason='')
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
            department=self.department, entry_date=meeting.meeting_date, kind='ADMIN', hours='0.75', reason='',
            previous=admin, expected_revision=1)
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
            department=self.department, entry_date=meeting.meeting_date, kind='LEAVE', hours='0.25', reason='',
            leave_type='VL', offset_kind='A')
        row = self.report()['rows'][0]
        self.assertEqual((row['expected'], row['actual'], row['admin'], row['leave']),
                         (Decimal('1'), Decimal('0.50'), Decimal('0.75'), Decimal('0.25')))
        save_closure(actor=self.actor, meeting=meeting, status='CLOSED', kind='HOLIDAY', pay_basis='REGULAR', reason='', expected_revision=0)
        row = self.report()['rows'][0]
        self.assertEqual((row['actual'], row['paid_closure'], row['leave']), (0, Decimal('1'), 0))

    def test_mixed_unreconciled_deductions_never_invent_actual_hours(self):
        meeting = self.meeting()
        self.result(meeting, [{'finding_type': 'ABSENCE', 'segment_key': 'missed', 'notice_status': 'A', 'missed_hours': '0.50'},
                              {'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 10}])
        row = self.report()['rows'][0]
        self.assertEqual(row['actual'], 0)
        self.assertEqual(row['unverified'], 1)
        self.assertIn('non-overlapping', row['details'][0]['warning'])

    def test_ac_scope_and_direct_deny_on_summary_details_and_empty_state(self):
        self.meeting()
        ac = Role.objects.create(code='AC', name='AC test')
        UserRole.objects.create(user=self.actor, role=ac, tenant=self.tenant, campus=self.campus, department=self.department)
        # Even a broad ALLOW does not grant this AC another department or campus.
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=VIEW_PERMISSION),
                                      grant_type='ALLOW', tenant=self.tenant, campus=self.campus)
        self.assertEqual(authorized_departments(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk), [self.department.pk])
        with self.assertRaises(PermissionDenied):
            authorized_departments(actor=self.actor, tenant_id=self.tenant.pk, campus_id=999999)
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(reverse('faculty_attendance:term_faculty_details', args=[self.replacement.pk]),
            {'academic_year': self.academic_year.pk, 'term': self.term.pk, 'as_of': '2026-01-05'}).status_code, 403)
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=VIEW_PERMISSION),
                                      grant_type='DENY', tenant=self.tenant, campus=self.campus)
        for route in [reverse('faculty_attendance:term_summary'), reverse('faculty_attendance:term_faculty_details', args=[self.faculty.pk])]:
            self.assertEqual(self.client.get(route).status_code, 403)

    def test_disabled_master_denies_monitoring_and_notice_delivery(self):
        meeting = self.meeting()
        self.result(meeting, [{'finding_type': 'ABSENCE', 'segment_key': 'missed', 'notice_status': 'N', 'missed_hours': '0.50'}])
        SystemSetting.objects.filter(tenant=self.tenant,
            setting_key=existing.FeatureSettingsService.FACULTY_ATTENDANCE_ENABLED_KEY).update(setting_value='false')
        with self.assertRaises(PermissionDenied):
            self.report()
        with self.assertRaises(PermissionDenied):
            current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)

    def test_absence_notice_deduplicates_corrects_and_resolves_without_faculty_leak(self):
        meeting = self.meeting()
        result = self.result(meeting, [{'finding_type': 'ABSENCE', 'segment_key': 'missed', 'notice_status': 'A', 'missed_hours': '1.00'}])
        refresh_for_meeting(meeting)
        notices = current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].payload['revision'], 1)
        result = self.result(meeting, [{'finding_type': 'ABSENCE', 'segment_key': 'missed', 'notice_status': 'N', 'missed_hours': '0.50'}], revision=result.revision)
        notices = current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        self.assertEqual(len(notices), 1)
        self.assertEqual((notices[0].payload['revision'], notices[0].payload['n']), (2, '0.50'))
        self.assertEqual(AttendanceStaffNotice.objects.count(), 1)
        self.faculty_visibility()
        entries, _, _ = faculty_published_entries(faculty_user=self.faculty, tenant_id=self.tenant.pk,
            campus_id=self.campus.pk, start_date=meeting.meeting_date, end_date=meeting.meeting_date)
        self.assertFalse(entries)
        self.result(meeting, [{'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 0}], revision=result.revision)
        self.assertFalse(current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk))
        self.assertFalse(AttendanceStaffNotice.objects.get().is_active)
        self.assertEqual(AttendanceResult.objects.get(meeting=meeting).history.count(), 3)

    def test_notice_delivery_rechecks_revision_recipient_scope_and_direct_deny(self):
        meeting = self.meeting()
        self.result(meeting, [{'finding_type': 'ABSENCE', 'segment_key': 'missed', 'notice_status': 'A', 'missed_hours': '0.50'}])
        self.assertEqual(len(current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)), 1)
        with self.assertRaises(PermissionDenied):
            current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=999999)
        UserRole.objects.filter(user=self.actor).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        UserRole.objects.filter(user=self.actor).update(is_active=True)
        AttendanceStaffNotice.objects.update(source_fingerprint='stale')
        self.assertFalse(current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk))
        refresh_for_meeting(meeting)
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=VIEW_PERMISSION),
                                      grant_type='DENY', tenant=self.tenant, campus=self.campus)
        with self.assertRaises(PermissionDenied):
            current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)

    def test_ongoing_month_published_warning_and_four_followup_survive_corrections(self):
        self.faculty_visibility()
        version = self.schedule(); self.coverage(); self.coverage(self.combined_offering)
        meetings = []
        for day in [5, 12, 19, 26]:
            meeting = MeetingService.generate(actor=self.actor, schedule_slot=version.slots.get(),
                meeting_date=date(2026, 1, day), offerings=[self.combined_offering])
            self.result(meeting, [{'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 1}])
            review = review_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                academic_year=self.academic_year, term=self.term, start_date=meeting.meeting_date, end_date=meeting.meeting_date)
            self.assertTrue(review.ready, review.blockers)
            publish_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
                academic_year=self.academic_year, term=self.term, start_date=meeting.meeting_date,
                end_date=meeting.meeting_date, expected_fingerprint=review.fingerprint,
                submission_key=f'late-day-{day}', publication_reason='')
            meetings.append(meeting)
            summary = published_tardiness_summary(faculty_user=self.faculty, tenant_id=self.tenant.pk,
                campus_id=self.campus.pk, year=2026, month=1)
            self.assertFalse(summary['is_complete'])
            if len(meetings) == 3:
                self.assertEqual(summary['threshold_state'], 'nearing')
        self.assertEqual(summary['threshold_state'], 'reached')
        notices = current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].payload['count'], 4)
        self.assertEqual(len(self.report(as_of=date(2026, 1, 26))['rows'][0]['followups']), 1)
        refresh_for_meeting(meetings[-1])
        self.assertEqual(AttendanceStaffNotice.objects.count(), 1)
        self.result(meetings[-1], [{'finding_type': 'EARLY', 'segment_key': 'dismissal', 'minutes': 1}], revision=1)
        self.assertFalse(current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk))
        # Faculty warning still reflects the immutable publication until explicit republish.
        self.assertEqual(published_tardiness_summary(faculty_user=self.faculty, tenant_id=self.tenant.pk,
            campus_id=self.campus.pk, year=2026, month=1)['count'], 4)
        self.assertEqual(published_tardiness_summary(faculty_user=self.faculty, tenant_id=self.tenant.pk,
            campus_id=self.campus.pk, year=2026, month=2)['count'], 0)

    def test_faculty_page_shows_ongoing_warning_but_never_staff_notices(self):
        self.faculty_visibility()
        self.faculty.privacy_consent_version = getattr(settings, 'PRIVACY_CONSENT_VERSION', '2026-03')
        self.faculty.privacy_consent_at = timezone.now()
        self.faculty.save(update_fields=['privacy_consent_version', 'privacy_consent_at'])
        permission, _ = Permission.objects.get_or_create(code='faculty_portal.access', defaults={'module': 'faculty_portal', 'action': 'access'})
        UserPermission.objects.create(user=self.faculty, permission=permission, grant_type='ALLOW', tenant=self.tenant, campus=self.campus)
        self.client.force_login(self.faculty)
        for state, count, text in [('nearing', 3, 'Nearing the monthly limit (3/4)'), ('reached', 4, 'Monthly limit reached (4/4)')]:
            with patch('apps.faculty_attendance.views.published_tardiness_summary', return_value={
                    'count': count, 'threshold_state': state, 'start_date': date(2026, 1, 1), 'is_complete': False}):
                response = self.client.get(reverse('faculty_attendance:my_attendance'), {'start_date': '2026-01-01', 'end_date': '2026-01-26'})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, text)
            self.assertContains(response, 'partial calendar month')
            self.assertNotContains(response, 'Staff notices and follow-up')
        self.assertEqual(self.client.get(reverse('faculty_attendance:term_summary')).status_code, 403)

    def test_ac_direct_detail_denies_real_other_department_record(self):
        self.meeting()
        UserRole.objects.create(user=self.actor, role=self.role, tenant=self.tenant, campus=self.campus, department=self.other_department)
        section = existing.Section.objects.create(tenant=self.tenant, campus=self.campus, department=self.other_department,
            program=self.other_program, code='OTHER', name='Other department')
        offering = existing.CourseOffering.objects.create(tenant=self.tenant, campus=self.campus, department=self.other_department,
            program=self.other_program, academic_year=self.academic_year, term=self.term,
            course=self.course, section=section, schedule_text='M 10:00-11:00')
        version = self.schedule(offering=offering)
        self.coverage(offering=offering, faculty=self.replacement)
        meeting = MeetingService.generate(actor=self.actor, schedule_slot=version.slots.get(), meeting_date=date(2026, 1, 5), offerings=[])
        self.result(meeting)
        ac = Role.objects.create(code='AC', name='AC scoped')
        UserRole.objects.create(user=self.actor, role=ac, tenant=self.tenant, campus=self.campus, department=self.department)
        self.assertNotIn(self.replacement.pk, [r['faculty'].pk for r in self.report()['rows'] if r['faculty']])
        self.client.force_login(self.actor)
        self.assertEqual(self.client.get(reverse('faculty_attendance:term_faculty_details', args=[self.replacement.pk]),
            {'academic_year': self.academic_year.pk, 'term': self.term.pk, 'as_of': '2026-01-05'}).status_code, 403)

    def test_verified_mixed_intervals_supply_actual_hours_without_overlap(self):
        meeting, publication = self._published_dtr_cutoff(findings=[
            {'finding_type': 'ABSENCE', 'segment_key': 'missed', 'notice_status': 'A', 'missed_hours': '0.50'},
            {'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 15}])
        save_mixed_decision(actor=self.actor, publication=publication, faculty=self.faculty, meeting=meeting,
            intervals_text='A 08:00-08:30\nL 08:30-08:45', reason='', expected_revision=0)
        row = self.report()['rows'][0]
        self.assertEqual(row['actual'], Decimal('0.25'))
        self.assertEqual(row['unverified'], 0)
        result = AttendanceResult.objects.get(meeting=meeting)
        self.result(meeting, [{'finding_type': 'ABSENCE', 'segment_key': 'missed', 'notice_status': 'A', 'missed_hours': '1.00'}], revision=result.revision)
        self.assertEqual(self.report()['rows'][0]['actual'], Decimal('0'))

    def test_no_attendance_permission_or_wrong_tenant_can_view_summary(self):
        outsider = User.objects.create_user('outsider-monitor', email='outsider-monitor@example.invalid', default_tenant=self.tenant, default_campus=self.campus)
        with self.assertRaises(PermissionDenied):
            self.report(actor=outsider)
        with self.assertRaises(PermissionDenied):
            term_summary(actor=self.actor, tenant_id=999999, campus_id=self.campus.pk,
                         academic_year=self.academic_year, term=self.term, as_of=date(2026, 1, 5))

    def test_notice_reassignment_and_closure_remove_old_current_delivery(self):
        meeting = self.meeting()
        result = self.result(meeting, [{'finding_type': 'ABSENCE', 'segment_key': 'missed', 'notice_status': 'N', 'missed_hours': '0.50'}])
        old_notice = AttendanceStaffNotice.objects.get()
        AttendanceResultService.reconcile_attribution(actor=self.actor, result=result, expected_revision=1,
                                                      faculty_user=self.replacement, reason='')
        notices = current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].faculty_user_id, self.replacement.pk)
        old_notice.refresh_from_db(); self.assertFalse(old_notice.is_active)
        # Explicit substitute supplies the same dated faculty for the no-class decision.
        SubstitutionService.assign(actor=self.actor, meeting=meeting, substitute_faculty=self.replacement, reason='')
        save_closure(actor=self.actor, meeting=meeting, status='CLOSED', kind='SUSPENSION', pay_basis='PART_TIME', reason='', expected_revision=0)
        self.assertFalse(current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk))

    def test_prepared_morning_keeps_disjoint_afternoon_expected_unverified(self):
        self.offering.schedule_text = 'M 08:00-09:00; M 13:00-14:00'
        self.offering.save(update_fields=['schedule_text'])
        version = self.schedule(); self.coverage()
        morning = MeetingService.generate(actor=self.actor, schedule_slot=version.slots.get(start_time=time(8)),
                                          meeting_date=date(2026, 1, 5), offerings=[])
        self.result(morning)
        row = self.report()['rows'][0]
        self.assertEqual((row['expected'], row['actual'], row['unverified']), (Decimal('2.00'), Decimal('1.00'), 1))
        self.assertEqual(len(row['details']), 2)
        self.assertIsNone(row['details'][1]['meeting_id'])

    def test_overlapping_changed_schedule_stays_review_required_not_double_counted(self):
        meeting = self.meeting(); self.result(meeting)
        self.offering.schedule_text = 'M 08:30-09:30'
        self.offering.save(update_fields=['schedule_text'])
        report = self.report()
        self.assertEqual(report['rows'][0]['expected'], Decimal('1.00'))
        self.assertTrue(any('Recorded schedule differs' in issue for issue in report['issues']))

    def test_overlapping_publications_choose_new_owner_before_filtering(self):
        meeting, first = self._published_dtr_cutoff(findings=[{'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 1}])
        self.faculty_visibility()
        UserPermission.objects.create(user=self.replacement, permission=Permission.objects.get(code=VIEW_PERMISSION),
            grant_type='ALLOW', tenant=self.tenant, campus=self.campus)
        result = AttendanceResult.objects.get(meeting=meeting)
        AttendanceResultService.reconcile_attribution(actor=self.actor, result=result, expected_revision=1,
                                                      faculty_user=self.replacement, reason='')
        # Different range = different publication lineage, overlapping the same occurrence.
        review = review_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 4), end_date=date(2026, 1, 5))
        self.assertTrue(review.ready, review.blockers)
        second = publish_cutoff(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk,
            academic_year=self.academic_year, term=self.term, start_date=date(2026, 1, 4), end_date=date(2026, 1, 5),
            expected_fingerprint=review.fingerprint, submission_key='owner-corrected-overlap', publication_reason='')
        self.assertNotEqual(first.lineage_key, second.lineage_key)
        for faculty, count in [(self.faculty, 0), (self.replacement, 1)]:
            entries, _, history = faculty_published_entries(faculty_user=faculty, tenant_id=self.tenant.pk,
                campus_id=self.campus.pk, start_date=meeting.meeting_date, end_date=meeting.meeting_date, include_history=True)
            self.assertEqual(len(entries), count)
            self.assertEqual(published_tardiness_summary(faculty_user=faculty, tenant_id=self.tenant.pk,
                campus_id=self.campus.pk, year=2026, month=1)['count'], count)
            self.assertTrue(history)
        first.entries.get().refresh_from_db()
        self.assertEqual(first.entries.get().faculty_user_id, self.faculty.pk)

    def test_notice_source_lock_precedes_write_and_aggregate_lock_precedes_calculation(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .notice_locking import lock_notice_campus
        from .staff_notices import _payload
        meeting = self.meeting()
        events = []
        def lock(campus_id):
            events.append('lock')
            return lock_notice_campus(campus_id)
        def payload(**kwargs):
            self.assertIn('lock', events)
            self.assertTrue(kwargs['lock'])
            events.append('calculate')
            return _payload(**kwargs)
        with patch('apps.faculty_attendance.notice_locking.lock_notice_campus', side_effect=lock), \
             patch('apps.faculty_attendance.staff_notices.lock_notice_campus', side_effect=lock), \
             patch('apps.faculty_attendance.staff_notices._payload', side_effect=payload), CaptureQueriesContext(connection) as queries:
            self.result(meeting, [{'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 1}])
        source_write = next(i for i, q in enumerate(queries) if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE'))
                            and 'attendance_results' in q['sql'])
        mutex_reads = [i for i, q in enumerate(queries) if 'faculty_attendance_notice_mutexes' in q['sql']]
        self.assertLess(min(mutex_reads), source_write)
        self.assertLess(events.index('lock'), events.index('calculate'))

    def test_failed_notice_recomputation_rolls_back_source_revision(self):
        meeting = self.meeting()
        with patch('apps.faculty_attendance.staff_notices._payload', side_effect=RuntimeError('Synthetic recompute failure')):
            with self.assertRaises(RuntimeError):
                self.result(meeting, [{'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 1}])
        self.assertFalse(AttendanceResult.objects.filter(meeting=meeting).exists())
        self.assertFalse(AttendanceStaffNotice.objects.exists())
