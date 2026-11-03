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

    def test_final_preserves_competition_ranks_but_missing_history_blocks_movement(self):
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
        self.assertEqual([(rows[f'MOVE-{i}']['rank'], rows[f'MOVE-{i}']['midterm_rank'], rows[f'MOVE-{i}']['rank_movement']) for i in range(3)], [(3, None, None), (1, None, None), (1, None, None)])
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

    def test_pass_statistics_do_not_invent_standalone_exam_formula(self):
        offering = self._offering(self.cycle_course, self.fairview, self.faculty_fv, 'PASS')
        student = self._student(offering, 'PASS')
        self._submitted_results(offering, [(student, Decimal(90))])
        response = self._get(cycle_id=self.cycle.pk)
        row = response.context['rows'][0]
        self.assertIsNone(row['passed_count'])
        self.assertIsNone(row['passing_rate'])
        self.assertEqual(row['class_average'], Decimal('90.00'))
        self.assertContains(response, 'verified standalone examination grade')

    def test_top_endpoint_is_lazy_private_and_explicitly_blocked(self):
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
        self.assertFalse(response.json()['available'])
        self.assertIn('historical', response.json()['message'])
        self.assertNotIn(student.student_no, response.content.decode())
        self.assertIn('no-store', response['Cache-Control'])
        self.assertFalse(any('student_period_grades' in q['sql'].lower() or 'student_activity_scores' in q['sql'].lower() for q in captured))
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
            self.assertIsNone(report['rows'][0]['midterm_rank'])
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
        self.assertContains(response, 'data-top-url=')
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

    def test_profile_change_cannot_be_used_as_historical_grading_evidence(self):
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
        self.assertIsNone(final['midterm_rank'])
        self.assertIsNone(final['rank_movement'])
        after = self._get(cycle_id=self.cycle.pk).context['rows'][0]
        self.assertEqual((old['class_average'], old['rank']), (after['class_average'], after['rank']))

    def test_multi_member_unit_with_missing_or_incompatible_history_is_na(self):
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
        # Confirm all members fail closed even where the live grade contexts match.
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
            self.assertIsNone(row['midterm_rank'])
            self.assertIsNone(row['rank_movement'])
        GradeSubmission.objects.filter(offering=other, template_period=self.period).update(
            template_snapshot_json={'template_id': -1, 'period_code': 'MIDTERM'})
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
            with patch.object(Service, '_summaries', wraps=Service._summaries) as summarized, CaptureQueriesContext(connection) as captured:
                report = Service.build_report(self._request(exam_period='FINAL'), self.final_cycle)
            summarized.assert_called_once()
            # One aggregate SELECT reads all selected-unit offering results.
            populations = [q for q in captured if 'student_period_grades' in q['sql'].lower()]
            self.assertEqual(len(populations), 1)
            measurements.append((len(captured), max(len(q['sql']) for q in captured)))
            self.assertEqual(report['total_rows'], count)
            self.assertEqual([row['rank'] for row in report['rows']], list(range(1, min(50, count)+1)))
        self.assertLessEqual(measurements[-1][0] - measurements[0][0], 2)
        self.assertLessEqual(measurements[-1][1] - measurements[0][1], 6000)
        print(f'Final offering query/SQL growth (10/50/100): {measurements}')


# Reuse fixtures without inheriting or collecting the existing regression tests twice.
for _name, _value in fixtures.MidtermExamPerformanceReportTests.__dict__.items():
    if _name.startswith('_') and not _name.startswith('__') and callable(getattr(fixtures.MidtermExamPerformanceReportTests, _name, None)):
        setattr(PeriodicalExamPerformanceTests, _name, _value)
