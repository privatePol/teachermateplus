"""Opt-in real InnoDB races; SQLite cannot establish row-lock correctness."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time
from threading import Barrier, Event
from unittest import skipUnless

from django.db import connection, connections, transaction
from django.test import TransactionTestCase

from . import tests as existing
from .models import AttendanceNoticeMutex, AttendanceResult, AttendanceStaffNotice
from .notice_locking import lock_notice_campus
from .observations import AttendanceResultService, CheckingRoundService, ObservationService
from .services import MeetingService
from .staff_notices import current_notices


@skipUnless(connection.vendor == 'mysql' and os.environ.get('TMP_ATTENDANCE_INNODB_TESTS') == '1',
            'No explicitly configured isolated InnoDB test runtime; SQLite has no row locks.')
class InnoDBNoticeConcurrencyTests(TransactionTestCase):
    permission_codes = existing.FacultyAttendanceFoundationTests.permission_codes
    aware = existing.FacultyAttendanceFoundationTests.aware
    schedule = existing.FacultyAttendanceFoundationTests.schedule
    coverage = existing.FacultyAttendanceFoundationTests.coverage

    def setUp(self):
        self.assertTrue(str(connection.settings_dict['NAME']).startswith('test_'))
        self.assertIn(connection.settings_dict.get('HOST', ''), ('', '127.0.0.1', 'localhost'))
        with connection.cursor() as cursor:
            cursor.execute('SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (%s, %s)',
                           ['faculty_attendance_notice_mutexes', 'faculty_attendance_results'])
            self.assertEqual([r[0].upper() for r in cursor.fetchall()], ['INNODB', 'INNODB'])
        existing.FacultyAttendanceFoundationTests.setUpTestData.__func__(type(self))
        existing.FacultyAttendanceFoundationTests.setUp(self)

    def _worker_connection(self):
        connections.close_all()
        with connection.cursor() as cursor:
            cursor.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')

    def test_first_use_mutex_creation_and_waiter_are_serialized(self):
        locked, attempted, release, finished = Event(), Event(), Event(), Event()
        campus_id = self.campus.pk
        def first():
            self._worker_connection()
            try:
                with transaction.atomic():
                    lock_notice_campus(campus_id)
                    locked.set()
                    if not release.wait(15):
                        raise AssertionError('Mutex holder was not released')
            finally:
                connections.close_all()
        def second():
            self._worker_connection()
            try:
                with transaction.atomic():
                    attempted.set()
                    lock_notice_campus(campus_id)
                finished.set()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_run = pool.submit(first)
            try:
                self.assertTrue(locked.wait(10))
                second_run = pool.submit(second)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(finished.wait(.25), 'Waiter entered before holder committed')
            finally:
                release.set()
            first_run.result(timeout=20); second_run.result(timeout=20)
        self.assertEqual(AttendanceNoticeMutex.objects.filter(campus=self.campus).count(), 1)

    def test_concurrent_late_removal_and_addition_leave_current_four_notice_visible(self):
        version = self.schedule(corrected_slots=[
            {'weekday': 0, 'start_time': time(8), 'end_time': time(9)},
            {'weekday': 0, 'start_time': time(13), 'end_time': time(14)}])
        self.coverage()
        morning = version.slots.get(start_time=time(8))
        meetings = [MeetingService.generate(actor=self.actor, schedule_slot=morning,
                    meeting_date=date(2026, 1, day), offerings=[]) for day in (5, 12, 19, 26)]
        meetings.append(MeetingService.generate(actor=self.actor, schedule_slot=version.slots.get(start_time=time(13)),
                        meeting_date=date(2026, 1, 26), offerings=[]))
        def observe(meeting, findings, key):
            round_ = CheckingRoundService.create(actor=self.actor, meetings=[meeting], checking_date=meeting.meeting_date)
            return ObservationService.record(actor=self.actor, checking_round=round_, meeting_id=meeting.pk,
                manifest_revision=1, submission_key=key, findings=findings)
        for i, meeting in enumerate(meetings):
            observation = observe(meeting, [{'finding_type': 'LATE' if i < 4 else 'EARLY',
                'segment_key': 'arrival' if i < 4 else 'dismissal', 'minutes': 1}], f'initial-{i}')
            AttendanceResultService.select_observation(actor=self.actor, observation=observation, expected_revision=0, reason='')
        corrections = [observe(meetings[3], [{'finding_type': 'EARLY', 'segment_key': 'dismissal', 'minutes': 1}], 'remove-late'),
                       observe(meetings[4], [{'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 1}], 'add-late')]
        barrier = Barrier(2)
        def correct(observation):
            self._worker_connection()
            try:
                barrier.wait(timeout=15)
                AttendanceResultService.select_observation(actor=self.actor, observation=observation, expected_revision=1, reason='')
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            runs = [pool.submit(correct, observation) for observation in corrections]
            for run in runs:
                run.result(timeout=30)
        self.assertEqual(AttendanceResult.objects.filter(late_flag=True).count(), 4)
        notices = current_notices(actor=self.actor, tenant_id=self.tenant.pk, campus_id=self.campus.pk)
        self.assertEqual(len(notices), 1, 'Committed aggregate must not remain hidden by a stale fingerprint')
        self.assertEqual(notices[0].payload['count'], 4)
        self.assertIn([meetings[4].pk, 2], notices[0].payload['revisions'])
        self.assertNotIn([meetings[3].pk, 1], notices[0].payload['revisions'])
        self.assertEqual(AttendanceStaffNotice.objects.filter(is_active=True).count(), 1)
