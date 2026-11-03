from decimal import Decimal
from unittest.mock import patch
import os
from pathlib import Path
from time import perf_counter
from statistics import median

from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import tests_midterm_exam_performance as fixtures
from .midterm_exam_performance import MidtermExamPerformanceReportService as Service
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.departmental_exams.models import CycleCourse, CycleCourseOffering, ExaminationCycle
from apps.grading.models import GradeSubmission, GradingTemplatePeriod, StudentPeriodGrade, TenantGradingProfile
from apps.rbac.models import Permission, RolePermission, UserPermission


class PeriodicalExamPerformanceTests(TestCase):
    setUp = fixtures.MidtermExamPerformanceReportTests.setUp

    def _final(self, offering):
        if not hasattr(self, 'final_cycle'):
            self.final_cycle = ExaminationCycle.objects.create(
                tenant=self.tenant, academic_year=self.academic_year, term=self.term,
                exam_period='FINAL', status='OPEN', created_by=self.admin,
            )
            self.final_period = GradingTemplatePeriod.objects.create(
                template=self.template, code='FINAL', name='Final', sequence_no=3,
            )
            self.final_course = CycleCourse.objects.create(
                cycle=self.final_cycle, course=self.course,
                exam_classification=CycleCourse.ExamClassification.STANDARDIZED,
            )
        CycleCourseOffering.objects.create(cycle_course=self.final_course, offering=offering, campus=offering.campus)

    def _request(self, **params):
        request = RequestFactory().get(self.url, params)
        request.user = self.admin
        request.scope = {
            'tenant_id': self.tenant.pk, 'tenant_ids': [self.tenant.pk],
            'campus_id': self.fairview[0].pk, 'campus_ids': [self.fairview[0].pk, self.cubao[0].pk],
            'department_ids': [self.fairview[1].pk],
        }
        return request

    def _get(self, **params):
        self.client.force_login(self.admin)
        return self.client.get(self.url, params)

    def test_period_selection_and_invalid_cycle_do_not_cross_period(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'SELECT')
        self._final(offering)
        self.assertEqual(list(Service.cycle_options(self._request())), [self.cycle])
        self.assertEqual(list(Service.cycle_options(self._request(exam_period='FINAL'))), [self.final_cycle])
        self.assertIsNone(Service.selected_cycle(self._request(exam_period='FINAL', cycle_id=self.cycle.pk)))
        self.assertEqual(Service.selected_exam_period(self._request(exam_period='bad')), 'MIDTERM')

    def test_final_compares_confirmed_midterm_competition_ranks(self):
        for index, (mid, final) in enumerate(((90, 70), (80, 95), (80, 95))):
            offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, f'MOVE-{index}')
            student = self._student(offering, f'MOVE-{index}')
            self._submitted_results(offering, [(student, Decimal(mid))])
            self._final(offering)
            self._submitted_results(offering, [(student, Decimal(final))], period=self.final_period)
        # A pre-final result must never replace the actual Midterm.
        GradingTemplatePeriod.objects.create(template=self.template, code='PREFINAL', name='Pre-final', sequence_no=2)
        response = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk)
        self.assertContains(response, 'Periodical Exam Performance')
        rows = {r['section_code']: r for r in response.context['rows']}
        self.assertEqual([(rows[f'MOVE-{i}']['rank'], rows[f'MOVE-{i}']['midterm_rank'], rows[f'MOVE-{i}']['rank_movement']) for i in range(3)], [(3, 1, -2), (1, 2, 1), (1, 2, 1)])
        self.assertContains(response, 'Midterm Rank')
        self.assertContains(response, 'Rank Movement')

    def test_missing_midterm_cycle_is_na(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'NO-MID')
        student = self._student(offering, 'NO-MID')
        self._final(offering)
        self._submitted_results(offering, [(student, Decimal(90))], period=self.final_period)
        self.cycle.is_active = False
        self.cycle.save()
        row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertEqual(row['rank'], 1)
        self.assertIsNone(row['midterm_rank'])
        self.assertIsNone(row['rank_movement'])

    def test_changed_population_blocks_movement(self):
        first = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'POP-1')
        student = self._student(first, 'POP-1')
        self._submitted_results(first, [(student, Decimal(90))])
        self._final(first)
        self._submitted_results(first, [(student, Decimal(90))], period=self.final_period)
        other = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'POP-2')
        other_student = self._student(other, 'POP-2')
        self._submitted_results(other, [(other_student, Decimal(80))])
        row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertIsNone(row['midterm_rank'])

    def test_reopened_midterm_and_late_roster_changes_block_movement(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'LIFE')
        student = self._student(offering, 'LIFE')
        self._submitted_results(offering, [(student, Decimal(90))])
        self._final(offering)
        self._submitted_results(offering, [(student, Decimal(90))], period=self.final_period)
        GradeSubmission.objects.filter(offering=offering, template_period=self.period).update(status='REOPENED')
        response = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk)
        self.assertIsNone(response.context['rows'][0]['midterm_rank'])
        GradeSubmission.objects.filter(offering=offering, template_period=self.period).update(status='SUBMITTED')
        offering.enrollments.update(updated_at=timezone.now())
        self.assertEqual(self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]['midterm_rank'], 1)
        self._student(offering, 'LATE-ROSTER')
        self.assertIsNone(self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]['midterm_rank'])

    def test_cycle_specific_exemption_and_departmental_badge(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'EXEMPT')
        self._final(offering)
        from apps.departmental_exams.models import _classification_service_scope
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.cycle_course.pk).update(exam_classification='DEPARTMENTAL')
        self.assertContains(self._get(cycle_id=self.cycle.pk), 'DEPTAL')
        CycleCourse.objects.filter(pk=self.final_course.pk).update(inclusion_status='EXEMPT')
        self.assertEqual(self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'], [])
        self.assertEqual(len(self._get(cycle_id=self.cycle.pk).context['rows']), 1)

    def test_final_incomplete_is_unranked_and_uses_final_status(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'FINAL-MISSING')
        self._student(offering, 'FINAL-MISSING')
        self._final(offering)
        row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertEqual(row['data_status'], 'Final grades not submitted')
        self.assertIsNone(row['rank'])

    def test_fx_final_alias_reads_exact_physical_period_results(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'FX')
        student = self._student(offering, 'FX')
        self._final(offering)
        self.final_period.code = 'FX'
        self.final_period.save()
        self._submitted_results(offering, [(student, Decimal(87))], period=self.final_period)
        self._submitted_results(offering, [(student, Decimal(13))], period=self.period)
        row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertEqual(row['data_status'], 'Complete')
        self.assertEqual(row['class_average'], Decimal('87.00'))
        self.assertEqual(row['context_signature'][5], self.final_period.pk)
        self.assertEqual(self._get(cycle_id=self.cycle.pk).context['rows'][0]['class_average'], Decimal('13.00'))

    def test_pass_statistics_compare_saved_grade_without_transmutation(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'PASS')
        student = self._student(offering, 'PASS')
        self._submitted_results(offering, [(student, Decimal(90))])
        response = self._get(cycle_id=self.cycle.pk)
        row = response.context['rows'][0]
        self.assertEqual(row['passed_count'], 1)
        self.assertEqual(row['passing_rate'], Decimal('100.00'))
        self.assertEqual(row['class_average'], Decimal('90.00'))
        self.assertContains(response, 'saved official exam grades only')

    def test_top_endpoint_reads_confirmed_saved_grades_only_after_identity_access(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'TOP')
        student = self._student(offering, 'PRIVATE-STUDENT-NUMBER')
        self._submitted_results(offering, [(student, Decimal(99))])
        permission, _ = Permission.objects.get_or_create(code='gradebook.view_student_identity', defaults={'action': 'view_student_identity', 'module': 'gradebook'})
        RolePermission.objects.get_or_create(role=self.cao_role, permission=permission)
        self.client.force_login(self.admin)
        url = reverse('admin_portal:periodical_exam_top_performers', args=[self.course.pk])
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url, {'cycle_id': self.cycle.pk})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['available'])
        self.assertEqual(response.json()['campuses'][0]['rows'][0]['exam_grade'], '99.00')
        self.assertIn(student.student_no, response.content.decode())
        self.assertIn('no-store', response['Cache-Control'])
        self.assertTrue(any('student_period_grades' in q['sql'].lower() for q in captured))
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_top_endpoint_denies_other_course_cycle_campus_and_identity(self):
        self._offering(self.cycle_course, self.cubao, self.faculty_cub, 'DENIED')
        self.client.force_login(self.admin)
        url = reverse('admin_portal:periodical_exam_top_performers', args=[self.course.pk])
        response = self.client.get(url, {'cycle_id': self.cycle.pk})
        self.assertIn('identity access', response.json()['message'])
        self.assertEqual(self.client.get(reverse('admin_portal:periodical_exam_top_performers', args=[999999]), {'cycle_id': self.cycle.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {'cycle_id': 999999}).status_code, 404)
        permission = Permission.objects.get(code='grading_analytics.read')
        UserPermission.objects.create(user=self.admin, permission=permission, grant_type='DENY', tenant=self.tenant, campus=self.cubao[0])
        self.assertEqual(self.client.get(url, {'cycle_id': self.cycle.pk}).status_code, 404)

    def test_feature_off_blocks_top_and_report(self):
        SystemSettingService.set(FeatureSettingsService.ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY, False, tenant_id=self.tenant.pk, value_type='BOOL')
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(reverse('admin_portal:periodical_exam_top_performers', args=[self.course.pk])).status_code, 404)

    def test_final_fragments_bind_period_and_current_rank_changes(self):
        for index in range(3):
            offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, f'CURSOR-{index}')
            student = self._student(offering, f'CURSOR-{index}')
            self._submitted_results(offering, [(student, Decimal(80 + index))])
            self._final(offering)
            self._submitted_results(offering, [(student, Decimal(90))], period=self.final_period)
        with patch.object(Service, 'ROWS_PER_PAGE', 1):
            response = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk)
            cursor = response.context['next_url']
            self.assertIn('exam_period=FINAL', cursor)
            fragment = self.client.get(self.url + cursor, HTTP_X_MIDTERM_FRAGMENT='1')
            self.assertEqual(fragment.status_code, 200)
            self.assertIn('Midterm Rank', fragment.json()['html'])
            self.assertEqual(fragment.json()['total_rows'], 3)
            StudentPeriodGrade.objects.filter(template_period=self.final_period, student=student).update(exam_grade=Decimal(0))
            stale = self.client.get(self.url + cursor, HTTP_X_MIDTERM_FRAGMENT='1')
            self.assertEqual(stale.status_code, 409)
            wrong = self.client.get(self.url + cursor.replace('exam_period=FINAL', 'exam_period=MIDTERM'), HTTP_X_MIDTERM_FRAGMENT='1')
            self.assertEqual(wrong.status_code, 409)

    def test_initial_report_does_not_call_top_and_query_growth_is_bounded(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'PERF')
        self._final(offering)
        def measure():
            with CaptureQueriesContext(connection) as captured, patch.object(Service, 'top_performers', side_effect=AssertionError('Not lazy')):
                report = Service.build_report(self._request(exam_period='FINAL'), self.final_cycle)
            return len(captured), report
        first, _ = measure()
        for index in range(12):
            student = self._student(offering, f'PERF-{index}')
            self._period_grade(offering, student, Decimal(90), finalized=True, period=self.final_period)
        second, report = measure()
        self.assertLessEqual(second - first, 2)
        self.assertEqual(report['total_rows'], 1)
        print(f'Periodical Final query growth: 0 students={first}, 12 students={second}')

    def test_final_complete_query_count_and_latency_do_not_grow_per_student(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'COMPLETE-PERF')
        self._final(offering)
        students = []
        def submit_all():
            for period in (self.period, self.final_period):
                GradeSubmission.objects.filter(offering=offering, template_period=period).delete()
                self._submission(offering, 'SUBMITTED', period=period)
        def measure():
            durations = []
            for _ in range(3):
                start = perf_counter()
                with CaptureQueriesContext(connection) as captured:
                    report = Service.build_report(self._request(exam_period='FINAL'), self.final_cycle)
                durations.append((perf_counter() - start) * 1000)
            self.assertEqual(report['rows'][0]['midterm_rank'], 1)
            return len(captured), median(durations)
        for i in range(10):
            student = self._student(offering, f'FULL-{i}')
            students.append(student)
            for period in (self.period, self.final_period):
                self._period_grade(offering, student, Decimal(90), finalized=True, period=period)
        submit_all()
        small, small_ms = measure()
        for i in range(10, 100):
            student = self._student(offering, f'FULL-{i}')
            for period in (self.period, self.final_period):
                self._period_grade(offering, student, Decimal(90), finalized=True, period=period)
        submit_all()
        large, large_ms = measure()
        self.assertLessEqual(large - small, 2)
        print(f'Final complete benchmark: 10 students {small} queries, median {small_ms:.1f}ms; 100 students {large} queries, median {large_ms:.1f}ms (3 runs each)')

    def test_top_direct_identity_deny_precedes_superuser(self):
        self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'SUPER')
        self.admin.is_superuser = True
        self.admin.save()
        permission, _ = Permission.objects.get_or_create(code='gradebook.view_student_identity', defaults={'module': 'gradebook', 'action': 'view_student_identity'})
        UserPermission.objects.create(user=self.admin, permission=permission, grant_type='DENY', tenant=self.tenant, campus=self.fairview[0])
        result = Service.top_performers(self._request(), self.cycle, self.course.pk)
        self.assertIn('identity access', result['message'])

    def test_rendered_final_fixture_and_noscript_period_selection(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'RENDER')
        student = self._student(offering, 'RENDER')
        self._submitted_results(offering, [(student, Decimal(90))])
        self._final(offering)
        self._submitted_results(offering, [(student, Decimal(85))], period=self.final_period)
        response = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk)
        self.assertNotContains(response, 'data-top-url=')
        self.assertContains(response, 'midterm-load-progress')
        self.assertNotContains(response, 'Load more')
        if os.getenv('TMP_PERIODICAL_EVIDENCE'):
            Path(os.environ['TMP_PERIODICAL_EVIDENCE'], 'final-rendered.html').write_bytes(response.content)
        self.cycle.is_active = False
        self.cycle.save()
        empty = self._get()
        self.assertNotContains(empty, 'type="submit" disabled')

    def test_missing_final_period_remains_explicit_and_unranked(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'NO-FINAL')
        self._student(offering, 'NO-FINAL')
        self._final(offering)
        self.final_period.is_active = False
        self.final_period.save()
        row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertEqual(row['data_status'], 'Final grading period unavailable')
        self.assertIsNone(row['rank'])

    def test_multiple_final_aliases_are_ambiguous_even_with_one_submitted(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'AMBIG')
        student = self._student(offering, 'AMBIG')
        self._final(offering)
        self._submitted_results(offering, [(student, Decimal(91))], period=self.final_period)
        GradingTemplatePeriod.objects.create(template=self.template, code='FX', name='Final', sequence_no=4)
        row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertEqual(row['data_status'], 'Ambiguous Final grading period')
        self.assertIsNone(row['rank'])
        self.assertIsNone(row['class_average'])

    def test_contradictory_final_code_and_name_do_not_select_an_arbitrary_period(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'CONFLICT')
        self._student(offering, 'CONFLICT')
        self._final(offering)
        self.final_period.code = 'FX'
        self.final_period.name = 'Midterm'
        self.final_period.save()
        row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertEqual(row['data_status'], 'Ambiguous Final grading period')
        self.assertIsNone(row['rank'])

    def test_profile_change_does_not_invalidate_confirmed_historical_grades(self):
        profile = TenantGradingProfile.objects.create(
            tenant=self.tenant, grading_template=self.template, profile_code='HISTORY', profile_name='History',
            default_base_value=Decimal(50), passing_grade_threshold=Decimal(75),
        )
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'HISTORY')
        student = self._student(offering, 'HISTORY')
        self._submitted_results(offering, [(student, Decimal(80))])
        # This is exactly the metadata retained by the authoritative submission writer.
        GradeSubmission.objects.filter(offering=offering, template_period=self.period).update(
            template_snapshot_json={'template_id': self.template.pk, 'template_code': self.template.code,
                'template_name': self.template.name, 'period_code': self.period.code, 'period_name': self.period.name})
        old = self._get(cycle_id=self.cycle.pk).context['rows'][0]
        profile.default_base_value = Decimal(65)
        profile.passing_grade_threshold = Decimal(85)
        profile.save()
        self._final(offering)
        self._submitted_results(offering, [(student, Decimal(95))], period=self.final_period)
        final = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertEqual(final['rank'], 1)
        self.assertEqual(final['midterm_rank'], 1)
        self.assertEqual(final['rank_movement'], 0)
        after = self._get(cycle_id=self.cycle.pk).context['rows'][0]
        self.assertEqual((old['class_average'], old['rank']), (after['class_average'], after['rank']))

    def test_multi_member_unit_uses_saved_grades_and_rejects_reopened_history(self):
        from apps.academics.models import Course
        from apps.departmental_exams.exam_units import ExamCourseEquivalencyService
        equivalent = Course.objects.create(tenant=self.tenant, code='HIST-EQ', title='Historical equivalent')
        equivalent_member = self._cycle_course(equivalent, CycleCourse.ExamClassification.STANDARDIZED)
        self._configuration(equivalent_member)
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'HIST-ONE')
        other = self._offering(equivalent_member, self.fairview, self.faculty_fv, 'HIST-TWO')
        # Unit creation below uses the same protected service as existing regressions.
        self._final(offering)
        final_other = CycleCourse.objects.create(cycle=self.final_cycle, course=equivalent,
            exam_classification=CycleCourse.ExamClassification.STANDARDIZED)
        self._configuration(self.final_course)
        self._configuration(final_other)
        self.final_cycle.processing_mode = ExaminationCycle.ProcessingMode.AUTOMATIC_GENERATION
        self.final_cycle.save()
        CycleCourseOffering.objects.create(cycle_course=final_other, offering=other, campus=other.campus)
        for current in (offering, other):
            student = self._student(current, 'HIST-'+str(current.pk))
            self._submitted_results(current, [(student, Decimal(81))])
            self._submitted_results(current, [(student, Decimal(93))], period=self.final_period)
        # Both periods retain the same validated examination unit.
        for cycle in (self.cycle, self.final_cycle):
            members = list(CycleCourse.objects.filter(cycle=cycle).order_by('pk'))
            ExamCourseEquivalencyService.create_group(
                cycle_id=cycle.pk, primary_cycle_course_id=members[0].pk, member_ids=[m.pk for m in members],
                name='History unit', actor=self.admin,
            )
        rows = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows']
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row['rank'], 1)
            self.assertEqual(row['midterm_rank'], 1)
            self.assertEqual(row['rank_movement'], 0)
        GradeSubmission.objects.filter(offering=other, template_period=self.period).update(
            status=GradeSubmission.Status.REOPENED)
        incompatible = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows']
        self.assertTrue(all(row['midterm_rank'] is None and row['rank_movement'] is None for row in incompatible))

    def test_final_aliases_preserve_identity_and_ignore_inactive_duplicates(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'ALIASES')
        student = self._student(offering, 'ALIASES')
        self._final(offering)
        self._submitted_results(offering, [(student, Decimal(89))], period=self.final_period)
        for alias in ('FX', 'FINALS', 'FINAL EXAM'):
            with self.subTest(alias=alias):
                self.final_period.code = alias
                self.final_period.save()
                inactive = GradingTemplatePeriod.objects.create(template=self.template,
                    code='FINAL', name='Final', sequence_no=4, is_active=False)
                row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
                self.assertEqual(row['class_average'], Decimal('89.00'))
                self.assertEqual(row['context_signature'][5], self.final_period.pk)
                inactive.delete()

    def test_full_population_aggregate_and_configuration_are_evaluated_once(self):
        previous = 0
        measurements = []
        for count in (10, 50, 100):
            for index in range(previous, count):
                offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, f'QUERY-{index:03d}')
                student = self._student(offering, f'QUERY-{index:03d}')
                self._final(offering)
                self._submitted_results(offering, [(student, Decimal(100-index))], period=self.final_period)
            previous = count
            started = perf_counter()
            with patch.object(Service, '_summaries', wraps=Service._summaries) as summarized, CaptureQueriesContext(connection) as captured:
                report = Service.build_report(self._request(exam_period='FINAL'), self.final_cycle)
            elapsed = round((perf_counter() - started) * 1000, 1)
            summarized.assert_called_once()
            # One aggregate SELECT per period reads all selected-unit results.
            populations = [q for q in captured if 'student_period_grades' in q['sql'].lower()]
            self.assertEqual(len(populations), 2)
            measurements.append((len(captured), max(len(q['sql']) for q in captured), elapsed))
            self.assertEqual(report['total_rows'], count)
            self.assertEqual([row['rank'] for row in report['rows']], list(range(1, min(50, count)+1)))
        self.assertLessEqual(measurements[-1][0] - measurements[0][0], 2)
        self.assertLessEqual(measurements[-1][1] - measurements[0][1], 6000)
        print(f'Final offering queries/max SQL chars/ms (10/50/100): {measurements}')


    def _identity_permission(self):
        permission, _ = Permission.objects.get_or_create(code='gradebook.view_student_identity',
            defaults={'module': 'gradebook', 'action': 'view_student_identity'})
        RolePermission.objects.get_or_create(role=self.cao_role, permission=permission)

    def test_saved_pass_threshold_precedence_boundaries_and_no_grade_writes(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'THRESHOLD')
        students = [self._student(offering, f'T-{i}') for i in range(5)]
        self._submitted_results(offering, zip(students, map(Decimal, ('0', '79.99', '80', '85', '90'))))
        before = list(StudentPeriodGrade.objects.order_by('pk').values())
        self.template.passing_grade_threshold = Decimal(85)
        self.template.save()
        SystemSettingService.set('PASSING_GRADE_THRESHOLD', '80', tenant_id=self.tenant.pk)
        profile = TenantGradingProfile.objects.create(tenant=self.tenant, grading_template=self.template,
            profile_code='THRESHOLD', profile_name='Threshold', passing_grade_threshold=Decimal(90))
        for profile_value, template_value, expected in ((90, 85, 1), (None, 85, 2), (None, None, 3), (0, None, 5), (101, None, None)):
            profile.passing_grade_threshold = profile_value
            profile.save()
            self.template.passing_grade_threshold = template_value
            self.template.save()
            with self.subTest(profile=profile_value, template=template_value):
                with CaptureQueriesContext(connection) as captured:
                    row = Service.build_report(self._request(), self.cycle)['rows'][0]
                self.assertEqual(row['passed_count'], expected)
                self.assertEqual(row['passing_rate'], Decimal(expected * 20) if expected is not None else None)
                self.assertIsNotNone(row['class_average'])
                self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in captured))
        self.assertEqual(before, list(StudentPeriodGrade.objects.order_by('pk').values()))

    def test_top_competition_cutoff_ties_campus_scope_and_no_cross_campus_assumption(self):
        self._identity_permission()
        for scope, faculty, values in ((self.fairview, self.faculty_fv, list(range(99, 90, -1)) + [90, 90, 90]),
                                       (self.cubao, self.faculty_cub, [100])):
            offering = self._offering(self.cycle_course, scope, faculty, scope[0].code+'-TOP')
            self._submitted_results(offering, [(self._student(offering, f'TOP-{scope[0].code}-{i}'), Decimal(v)) for i, v in enumerate(values)])
        before = list(StudentPeriodGrade.objects.order_by('pk').values())
        result = Service.top_performers(self._request(), self.cycle, self.course.pk)
        rows = next(c['rows'] for c in result['campuses'] if c['campus'] == 'FV')
        self.assertEqual(len(rows), 12)
        self.assertEqual([r['rank'] for r in rows], list(range(1, 10)) + [10, 10, 10])
        self.assertIsNone(result['overall'])
        self.assertIn('revision', result['overall_note'])
        self.assertEqual(before, list(StudentPeriodGrade.objects.order_by('pk').values()))

    def test_stored_raw_pair_missing_ambiguous_and_later_changes_do_not_hide_grade(self):
        from apps.grading.models import GradeActivity, GradingTemplateComponent, StudentActivityScore
        self._identity_permission()
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'RAW')
        student = self._student(offering, 'RAW')
        component = GradingTemplateComponent.objects.create(template_period=self.period,
            code='EXAM', name='Exam', weight_percentage=100, is_exam_component=True)
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=offering.campus, offering=offering,
            template_period=self.period, template_component=component, title='Exam', total_score=40)
        score = StudentActivityScore.objects.create(activity=activity, student=student, raw_score=35, computed_score=1)
        self._submitted_results(offering, [(student, Decimal('93.75'))])
        def top():
            result = Service.top_performers(self._request(), self.cycle, self.course.pk)
            self.assertTrue(result['available'])
            self.assertEqual(result['overall'], result['campuses'][0]['rows'])
            row = result['campuses'][0]['rows'][0]
            self.assertEqual(row['exam_grade'], '93.75')
            return row
        row = top()
        self.assertEqual((row['raw_score'], row['maximum_points']), ('35.00', '40.00'))
        score.raw_score = 36
        score.save()
        self.assertIsNone(top()['raw_score'])
        saved = StudentPeriodGrade.objects.get(offering=offering, student=student)
        saved.save()  # A later confirmed stored result can retain the updated raw input.
        self.assertEqual(top()['raw_score'], '36.00')
        GradeActivity.objects.create(tenant=self.tenant, campus=offering.campus, offering=offering,
            template_period=self.period, template_component=component, title='Second exam', total_score=50)
        self.assertIsNone(top()['raw_score'])

    def test_incomplete_offering_keeps_confirmed_top_but_never_counts_missing_as_failed(self):
        self._identity_permission()
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'PARTIAL')
        known = self._student(offering, 'KNOWN')
        self._student(offering, 'MISSING')
        self._submitted_results(offering, [(known, Decimal(85))])
        row = self._get(cycle_id=self.cycle.pk).context['rows'][0]
        self.assertIsNone(row['passing_rate'])
        self.assertIsNone(row['passed_count'])
        top = Service.top_performers(self._request(), self.cycle, self.course.pk)
        self.assertEqual(top['campuses'][0]['rows'][0]['student_no'], known.student_no)
        self.assertIn('incomplete', top['message'])
        StudentPeriodGrade.objects.filter(offering=offering).update(is_finalized=False)
        self.assertFalse(Service.top_performers(self._request(), self.cycle, self.course.pk)['available'])
        self.assertNotContains(self._get(cycle_id=self.cycle.pk), 'data-top-url=')

    def test_denied_identity_never_queries_student_results(self):
        self._identity_permission()
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'IDENTITY')
        student = self._student(offering, 'CONFIDENTIAL')
        self._submitted_results(offering, [(student, Decimal(90))])
        permission = Permission.objects.get(code='gradebook.view_student_identity')
        UserPermission.objects.create(user=self.admin, permission=permission,
            tenant=self.tenant, campus=self.fairview[0], grant_type='DENY')
        with CaptureQueriesContext(connection) as captured:
            result = Service.top_performers(self._request(), self.cycle, self.course.pk)
        self.assertFalse(result['available'])
        self.assertFalse(any('student_period_grades' in q['sql'].lower() or 'student_activity_scores' in q['sql'].lower() for q in captured))
        response = self._get(cycle_id=self.cycle.pk)
        self.assertNotContains(response, 'data-top-url=')
        self.assertNotContains(response, student.student_no)

    def test_historical_saved_midterm_survives_template_replacement_and_ambiguous_period_is_rejected(self):
        from apps.grading.models import GradingTemplate
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'OLD-TEMPLATE')
        student = self._student(offering, 'OLD-TEMPLATE')
        self._submitted_results(offering, [(student, Decimal(88))])
        old = self.period
        self._final(offering)
        replacement = GradingTemplate.objects.create(tenant=self.tenant, code='NEW-TEMPLATE', name='New', is_published=True)
        self.final_period.template = replacement
        self.final_period.save()
        self.template.is_active = False
        self.template.save()
        self._submitted_results(offering, [(student, Decimal(90))], period=self.final_period)
        row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertEqual((row['midterm_rank'], row['rank_movement']), (1, 0))
        another = GradingTemplatePeriod.objects.create(template=replacement, code='MIDTERM', name='Midterm', sequence_no=1)
        self._submitted_results(offering, [(student, Decimal(75))], period=another)
        row = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk).context['rows'][0]
        self.assertIsNone(row['midterm_rank'])
        self.assertEqual(StudentPeriodGrade.objects.get(offering=offering, template_period=old).exam_grade, Decimal(88))

    def test_top_queries_are_bounded_for_ten_fifty_and_hundred_offerings(self):
        self._identity_permission()
        measurements = []
        previous = 0
        for count in (10, 50, 100):
            for index in range(previous, count):
                offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, f'TOP-QUERY-{index}')
                student = self._student(offering, f'TOP-QUERY-{index}')
                self._submitted_results(offering, [(student, Decimal(100-index))])
            previous = count
            start = perf_counter()
            with CaptureQueriesContext(connection) as queries:
                result = Service.top_performers(self._request(), self.cycle, self.course.pk)
            measurements.append((count, len(queries), round((perf_counter()-start)*1000, 1)))
            self.assertEqual(len(result['campuses'][0]['rows']), 10)
        self.assertLessEqual(measurements[-1][1]-measurements[0][1], 2)
        print('Top saved-results benchmarks (offerings, queries, ms):', measurements)


    def test_top_excludes_ambiguous_duplicate_students_and_reopened_results(self):
        from apps.enrollment.models import Enrollment
        self._identity_permission()
        first = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'DUP-1')
        second = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'DUP-2')
        duplicate = self._student(first, 'DUPLICATE')
        valid = self._student(first, 'UNIQUE')
        Enrollment.objects.create(tenant=self.tenant, campus=second.campus, academic_year=self.academic_year,
            term=self.term, student=duplicate, course_offering=second)
        self._submitted_results(first, [(duplicate, Decimal(99)), (valid, Decimal(80))])
        self._submitted_results(second, [(duplicate, Decimal(100))])
        top = Service.top_performers(self._request(), self.cycle, self.course.pk)
        self.assertEqual([r['student_no'] for r in top['campuses'][0]['rows']], ['UNIQUE'])
        GradeSubmission.objects.filter(offering=first).update(status='REOPENED')
        top = Service.top_performers(self._request(), self.cycle, self.course.pk)
        self.assertEqual([r['student_no'] for r in top['campuses'][0]['rows']], ['DUPLICATE'])
        GradeSubmission.objects.filter(offering=second).update(status='DRAFT')
        self.assertFalse(Service.top_performers(self._request(), self.cycle, self.course.pk)['available'])

    def test_history_is_stable_across_pages_and_midterm_edits_invalidate_cursor(self):
        for index, (midterm, final) in enumerate(((90, 70), (80, 95), (80, 95))):
            offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, f'H-PAGE-{index}')
            student = self._student(offering, f'H-PAGE-{index}')
            self._submitted_results(offering, [(student, Decimal(midterm))])
            self._final(offering)
            self._submitted_results(offering, [(student, Decimal(final))], period=self.final_period)
        with patch.object(Service, 'ROWS_PER_PAGE', 1):
            first = self._get(exam_period='FINAL', cycle_id=self.final_cycle.pk)
            self.assertEqual((first.context['rows'][0]['rank'], first.context['rows'][0]['midterm_rank']), (1, 2))
            cursor = first.context['next_url']
            second = self.client.get(self.url + cursor)
            self.assertEqual((second.context['rows'][0]['rank'], second.context['rows'][0]['midterm_rank']), (1, 2))
            StudentPeriodGrade.objects.filter(student=student, template_period=self.period).update(exam_grade=100)
            self.assertEqual(self.client.get(self.url + cursor, HTTP_X_MIDTERM_FRAGMENT='1').status_code, 409)

    def test_incompatible_campus_contexts_hide_top_without_disabling_class_pass_counts(self):
        self._identity_permission()
        from apps.academics.models import Course
        from apps.departmental_exams.exam_units import ExamCourseEquivalencyService
        other_course = Course.objects.create(tenant=self.tenant, code='OTHER-CONTEXT', title='Other')
        other_member = self._cycle_course(other_course, CycleCourse.ExamClassification.STANDARDIZED)
        self._configuration(other_member)
        for member, code in ((self.cycle_course, 'C-1'), (other_member, 'C-2')):
            offering = self._offering(member, self.fairview, self.faculty_fv, code)
            self._submitted_results(offering, [(self._student(offering, code), Decimal(90))])
        ExamCourseEquivalencyService.create_group(cycle_id=self.cycle.pk,
            primary_cycle_course_id=self.cycle_course.pk, member_ids=[self.cycle_course.pk, other_member.pk],
            name='Context unit', actor=self.admin)
        TenantGradingProfile.objects.create(tenant=self.tenant, course=other_course, grading_template=self.template,
            profile_code='DIFFERENT', profile_name='Different', passing_grade_threshold=Decimal(95))
        result = Service.top_performers(self._request(), self.cycle, self.course.pk)
        self.assertFalse(result['available'])
        response = self._get(cycle_id=self.cycle.pk)
        self.assertNotContains(response, 'data-top-url=')
        self.assertEqual(sorted(r['passed_count'] for r in response.context['rows']), [0, 1])


# Reuse fixtures without inheriting or collecting the existing regression tests twice.
for _name, _value in fixtures.MidtermExamPerformanceReportTests.__dict__.items():
    if _name.startswith('_') and not _name.startswith('__') and callable(getattr(fixtures.MidtermExamPerformanceReportTests, _name, None)):
        setattr(PeriodicalExamPerformanceTests, _name, _value)
