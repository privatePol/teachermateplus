"""Presentation-only AJAX/GET parity and unchanged scope enforcement."""
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse

from apps.rbac.models import Permission, Role, UserPermission, UserRole
from . import tests_monitoring as monitoring_tests
from .permissions import VIEW_PERMISSION


class TermMonitoringAjaxTests(TestCase):
    permission_codes = monitoring_tests.TermMonitoringTests.permission_codes
    setUpTestData = classmethod(monitoring_tests.TermMonitoringTests.setUpTestData.__func__)
    setUp = monitoring_tests.TermMonitoringTests.setUp
    aware = monitoring_tests.TermMonitoringTests.aware
    schedule = monitoring_tests.TermMonitoringTests.schedule
    coverage = monitoring_tests.TermMonitoringTests.coverage
    meeting = monitoring_tests.TermMonitoringTests.meeting
    result = monitoring_tests.TermMonitoringTests.result

    def query(self):
        return {'academic_year': self.academic_year.pk, 'term': self.term.pk, 'as_of': '2026-01-05',
                'scope_tenant_id': self.tenant.pk, 'scope_campus_id': self.campus.pk}

    def test_ajax_summary_equals_normal_get_fragment_and_keeps_filters(self):
        meeting = self.meeting()
        self.result(meeting, [{'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 1},
                              {'finding_type': 'EARLY', 'segment_key': 'dismissal', 'minutes': 1}])
        self.client.force_login(self.actor)
        url = reverse('faculty_attendance:term_summary')
        normal = self.client.get(url, self.query())
        ajax = self.client.get(url, self.query(), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual((normal.status_code, ajax.status_code), (200, 200))
        data = ajax.json()
        self.assertTrue(data['ok'])
        self.assertFalse(data['has_details'])
        self.assertIn(data['html'], normal.content.decode())
        self.assertNotIn('<!doctype', data['html'].lower())
        self.assertIn('value="2026-01-05"', data['html'])
        self.assertIn('data-term-filter', data['html'])
        self.assertIn('method="get"', data['html'])
        self.assertIn('Latest verified class date', data['html'])
        self.assertIn('Earlier classes may still be unverified', data['html'])
        self.assertIn('Not teaching or leave', data['html'])
        self.assertIn('0.97', data['html'])
        self.assertEqual(normal.context['report']['rows'][0]['actual'], Decimal('0.97'))
        self.assertContains(normal, 'term_monitoring.js')

    def test_ajax_details_and_direct_get_keep_dated_minutes_and_attribution(self):
        meeting = self.meeting()
        self.result(meeting, [{'finding_type': 'LATE', 'segment_key': 'arrival', 'minutes': 1}])
        self.client.force_login(self.actor)
        url = reverse('faculty_attendance:term_faculty_details', args=[self.faculty.pk])
        normal = self.client.get(url, self.query())
        ajax = self.client.get(url, self.query(), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual((normal.status_code, ajax.status_code), (200, 200))
        self.assertTrue(ajax.json()['has_details'])
        self.assertIn(ajax.json()['html'], normal.content.decode())
        for label in ('id="term-faculty-details"', 'tabindex="-1"', 'MATH101 / S1', 'Attendance R1', 'marked late'):
            self.assertIn(label, ajax.json()['html'])

    def test_invalid_filter_ajax_returns_bound_errors_and_normal_get_fallback(self):
        self.client.force_login(self.actor)
        url = reverse('faculty_attendance:term_summary')
        query = {**self.query(), 'as_of': 'not-a-date'}
        ajax = self.client.get(url, query, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        normal = self.client.get(url, query)
        self.assertEqual((ajax.status_code, normal.status_code), (400, 200))
        self.assertFalse(ajax.json()['ok'])
        self.assertFalse(ajax.json()['has_details'])
        for html in (ajax.json()['html'], normal.content.decode()):
            self.assertIn('value="not-a-date"', html)
            self.assertIn('errorlist', html)
            self.assertIn('Enter a valid date.', html)
        self.assertEqual(normal.context['form'].data['term'], str(self.term.pk))

    def test_ajax_and_get_direct_scope_deny_are_enforced_before_partial_render(self):
        self.meeting()
        ac = Role.objects.create(code='AC', name='Scoped AC')
        UserRole.objects.create(user=self.actor, role=ac, tenant=self.tenant, campus=self.campus, department=self.department)
        self.client.force_login(self.actor)
        out_of_scope = reverse('faculty_attendance:term_faculty_details', args=[self.replacement.pk])
        for headers in ({}, {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}):
            self.assertEqual(self.client.get(out_of_scope, self.query(), **headers).status_code, 403)
        UserPermission.objects.create(user=self.actor, permission=Permission.objects.get(code=VIEW_PERMISSION),
                                      grant_type='DENY', tenant=None, campus=None)
        for route in (reverse('faculty_attendance:term_summary'), reverse('faculty_attendance:term_faculty_details', args=[self.faculty.pk])):
            for headers in ({}, {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}):
                response = self.client.get(route, self.query(), **headers)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn('term-summary-card', response.content.decode())
