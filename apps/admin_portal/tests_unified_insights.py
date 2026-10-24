"""Unified presentation regressions using disposable finalized Gradebook records."""
from copy import deepcopy
from decimal import Decimal
import json
from unittest.mock import patch

from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.test import TestCase
from django.urls import resolve, reverse

from apps.admin_portal import tests_assessment_alignment as fixtures
from apps.admin_portal import assessment_alignment, learning_insights
from apps.admin_portal.unified_insights import present
from apps.grading.models import StudentPeriodGrade, GradeSubmission
from apps.rbac.models import Permission, UserPermission


class UnifiedTests(TestCase):
    # Reuse only fixture builders, not inherited regression test methods.
    for _name in ("_enable_feature", "_role_with_access", "_user", "_offering", "_assign", "_student",
                  "_activity", "_score", "complete_students", "bsa_period_definitions", "post", "report"):
        locals()[_name] = fixtures.AlignmentTests.__dict__[_name]

    def setUp(self):
        fixtures.AlignmentTests.setUp(self)
        self.url = reverse("admin_portal:unified_academic_insights_preview")

    def source(self, tab="alignment", **filters):
        request = self.client.get(self.url).wsgi_request
        selection = learning_insights.LearningSelection(request)
        params = {**self.filters, **filters}
        backend = learning_insights if tab == "overview" else assessment_alignment
        return backend.build_report(selection, params), selection, params

    def test_get_options_lazy_tabs_and_preserved_routes_buttons(self):
        with patch.object(assessment_alignment, "build_report", side_effect=AssertionError("eager alignment")), \
             patch.object(learning_insights, "build_report", side_effect=AssertionError("eager overview")):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.post("options").status_code, 200)
        text = response.content.decode()
        for label in ("Performance Overview", "Exam Comparison", "Formative\u2013Summative Alignment",
                      "Export Sections CSV", "Export Student Review CSV", "Midterm Exam Performance", "Activity Consistency", "Back to Dashboard"):
            self.assertIn(label, text)
        self.assertEqual(text.count('id="learning-filters"'), 1)
        for path in ("performance-insights/", "learning-performance-insights/preview/", "assessment-alignment/preview/"):
            self.assertTrue(resolve("/admin-portal/grading/" + path))

    def test_tabs_share_only_required_engine_and_cache(self):
        with patch.object(learning_insights, "build_report", wraps=learning_insights.build_report) as overview, \
             patch.object(assessment_alignment, "build_report", wraps=assessment_alignment.build_report) as alignment:
            first = self.report(tab="overview")
            self.assertEqual(overview.call_count, 1)
            self.assertEqual(alignment.call_count, 0)
            exam = self.report(tab="exams")
            self.assertEqual(alignment.call_count, 1)
            self.report(action="page", tab="alignment", token=exam["token"])
            self.report(action="page", tab="overview", token=first["token"])
            self.assertEqual(alignment.call_count, 1)
            # The alignment engine invokes the shared loader exactly once too.
            self.assertEqual(overview.call_count, 2)

    def test_existing_endpoint_public_cache_is_reused(self):
        self.client.post(reverse("admin_portal:assessment_alignment_preview"),
            {**self.filters, "action": "report"})
        with patch.object(assessment_alignment, "build_report", side_effect=AssertionError("duplicate build")):
            self.report(tab="alignment")

    def test_three_levels_are_bounded_and_drilldown_retains_filters(self):
        for tab in ("overview", "exams", "alignment"):
            with self.subTest(tab=tab):
                broad = self.report(tab=tab, course_code="", offering_id="")
                self.assertIn("Course Summary", broad["html"])
                self.assertNotIn("data-unified-detail", broad["html"])
                self.assertNotIn("Matched Examination Comparisons", broad["html"])
                self.assertIn('data-unified-course="AE105-FAR"', broad["html"])
                course = self.report(tab=tab, offering_id="")
                self.assertIn("Section Summary", course["html"])
                self.assertIn(f'data-unified-section="{self.offering_a.pk}"', course["html"])
                self.assertNotIn("data-unified-detail", course["html"])
                section = self.report(tab=tab)
                self.assertNotIn("Course Summary", section["html"])
                self.assertIn("Class Period Results" if tab == "overview" else "data-unified-detail", section["html"])

    def test_broad_view_limits_course_rows_and_chart_groups(self):
        result, selection, params = self.source(offering_id=None, course_code="")
        row = result["classifications"][0]
        result["classifications"] = [{**deepcopy(row), "id": i, "course": f"COURSE-{i:03d}"} for i in range(300)]
        result["sections"] = 300
        context, charts = present(result, selection, params, "alignment", 1)
        self.assertEqual(len(context["summary_rows"]), 20)
        self.assertEqual(context["details"], [])
        self.assertLessEqual(len(charts["bars"]), 12)
        self.assertEqual(context["page"].paginator.num_pages, 15)
        # These are compact course summaries; no automatic section-detail pages.
        context, _ = present(result, selection, params, "alignment", 2)
        self.assertEqual(context["summary_rows"][0]["course"], "COURSE-020")

    def test_overview_matches_existing_charts_classification_and_grades(self):
        result, _, _ = self.source("overview")
        response = self.report(tab="overview")
        self.assertEqual(response["charts"]["line"], json.loads(json.dumps(result["charts"]["line"], cls=DjangoJSONEncoder)))
        self.assertEqual(response["charts"]["patterns"][0]["count"], result["patterns"][0]["count"])
        self.assertIn("68.02", response["html"])
        self.assertTrue(response["export_periods"])
        self.assertLessEqual(len(response["charts"]["bars"]), 1)
        self.assertNotIn(f"{self.offering_b.section.code} / {self.campus.code}", response["html"])

    def test_exam_chart_distinguishes_period_and_matched_values(self):
        response = self.report(tab="exams")
        charts = response["charts"]["exams"]
        self.assertEqual(charts["labels"], ["Prelim", "Midterm", "Final"])
        points = [s for s in charts["datasets"] if not s["connected"]]
        segments = [s for s in charts["datasets"] if s["connected"]]
        self.assertTrue(points)
        self.assertEqual(len(segments), 2)
        self.assertIn("90.00", [v for s in points for v in s["values"]])
        self.assertIn("Matched Examination Comparisons", response["html"])
        self.assertIn("Examination chart values", response["html"])
        self.assertNotIn("10.00", [v for s in points for v in s["values"]])  # saved Final period grade
        self.final_exam.score_input_mode = "DIRECT_PERCENTAGE"
        self.final_exam.save(update_fields=["score_input_mode"])
        changed = self.report(tab="exams")["charts"]["exams"]["datasets"]
        self.assertFalse(any(s["connected"] and s["values"][-1] is not None for s in changed))

    def test_pending_exam_is_gap_not_zero_and_no_movement_pie(self):
        self.lock.is_locked = False
        self.lock.save()
        response = self.report(tab="alignment")
        self.assertFalse(any(p["count"] for p in response["charts"]["patterns"]))
        self.assertNotIn('id="learning-patterns"', response["html"])
        self.assertIn("Performance movement will be available", response["html"])
        exam = self.report(tab="exams")["charts"]["exams"]
        self.assertTrue(all(s["values"][-1] is None for s in exam["datasets"]))

    def test_non_overlapping_availability_reconciles(self):
        for state in ("eligible", "pending", "excluded", "unavailable", "withheld"):
            with self.subTest(state=state):
                cache.clear()
                if state == "pending":
                    self.lock.is_locked = False; self.lock.save()
                elif state == "excluded":
                    self.lock.is_locked = True; self.lock.save()
                    self.cycles["FINAL"].inclusion_status = "EXEMPT"
                    self.cycles["FINAL"].save(update_fields=["inclusion_status"])
                elif state == "unavailable":
                    self.cycles["FINAL"].inclusion_status = "INCLUDED"
                    self.cycles["FINAL"].save(update_fields=["inclusion_status"])
                    self.final_exam.score_input_mode = "UNKNOWN"; self.final_exam.save()
                elif state == "withheld":
                    UserPermission.objects.create(user=self.area_chair, permission=self.permission,
                        tenant=self.tenant, campus=self.campus, grant_type="DENY")
                    self.final_exam.score_input_mode = "RAW_BASE50"; self.final_exam.save()
                    StudentPeriodGrade.objects.filter(offering=self.offering_a, student=self.students[-1]).delete()
                result, selection, params = self.source()
                context, _ = present(result, selection, params, "alignment", 1)
                for period in context["availability_periods"]:
                    self.assertEqual(sum(a["count"] for a in period["statuses"]), period["total"])
                    self.assertEqual(period["total"], result["sections"])
                # A remaining group of five out of six is protected by its
                # recoverable one-person complement in the existing privacy plan.
                self.assertEqual(result["classifications"][0]["period_availability"], {
                    "MIDTERM": "withheld" if state == "withheld" else "eligible", "FINAL": state})

    def test_grouping_never_merges_incompatible_signature_groups(self):
        result, selection, params = self.source(offering_id=None, course_code="")
        row = deepcopy(result["classifications"][0])
        other = deepcopy(row); other["id"] = 999
        for p in other["pairs"]:
            p["group"] = "different-profile"
            p["quiz"] = p["participation"] = p["exam"] = Decimal(10)
        result["classifications"] = [row, other]
        _, charts = present(result, selection, params, "alignment", 1)
        self.assertEqual(len(charts["bars"]), 4)
        self.assertTrue(all(b["sections"] == 1 for b in charts["bars"]))

    def test_tokens_bind_engine_filters_scope_and_policy_freshness(self):
        token = self.report(tab="alignment")["token"]
        self.assertEqual(self.post("page", tab="overview", token=token).status_code, 409)
        self.assertEqual(self.post("page", tab="exams", token=token, offering_id="").status_code, 409)
        self.lock.is_locked = False; self.lock.save()
        self.assertEqual(self.post("page", tab="exams", token=token).status_code, 409)
        self.assertEqual(self.post(tab="invalid").status_code, 400)
        self.assertEqual(self.post(tab="overview", offering_id=999999).status_code, 400)

    def test_direct_deny_reauthorization_and_no_individual_disclosure(self):
        token = self.report(tab="alignment")["token"]
        UserPermission.objects.create(user=self.area_chair, permission=self.permission, tenant=self.tenant,
            campus=self.campus, grant_type="DENY")
        self.assertEqual(self.post("page", tab="alignment", token=token).status_code, 409)
        permission = Permission.objects.get(code="grading_analytics.read")
        UserPermission.objects.create(user=self.area_chair, permission=permission, tenant=self.tenant,
            campus=self.campus, grant_type="DENY")
        self.assertIn(self.post().status_code, (302, 400, 403))

    def test_sparse_grade_and_scope_failure_never_leak(self):
        UserPermission.objects.create(user=self.area_chair, permission=self.permission,
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.final).exclude(student=self.students[0]).update(exam_grade=None)
        response = self.report(tab="exams")
        self.assertTrue(all(s["values"][-1] is None for s in response["charts"]["exams"]["datasets"]))
        self.assertNotIn(self.students[0].student_no, response["html"])
        self.assertNotIn("student_values", str(response))
        result, _, _ = self.source()
        self.assertEqual(result["classifications"][0]["period_availability"]["FINAL"], "withheld")
        from apps.tenants.models import Tenant
        foreign = Tenant.objects.create(code="UNIFIED-FOREIGN", name="Foreign")
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.final).update(tenant=foreign)
        self.assertEqual(self.report(tab="exams")["charts"]["exams"]["datasets"], [])

    def test_legacy_and_exempt_preserve_labels_and_no_new_classifications(self):
        from apps.departmental_exams.models import CycleCourse, _classification_service_scope
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.cycles["MIDTERM"].pk).update(exam_classification="UNCLASSIFIED_LEGACY")
        response = self.report(tab="exams")
        self.assertIn("Legacy Exam \u2014 Type Not Recorded", response["html"])
        self.assertFalse(any(p["count"] for p in response["charts"]["patterns"]))
        result, _, _ = self.source()
        self.assertEqual(result["classifications"][0]["period_availability"], {"MIDTERM": "unavailable", "FINAL": "eligible"})

    def test_closing_freshness_blocks_changes_during_render(self):
        from django.template.loader import render_to_string
        def change(*args, **kwargs):
            self.lock.is_locked = False; self.lock.save()
            return render_to_string(*args, **kwargs)
        with patch("apps.admin_portal.unified_insights_views.render_to_string", side_effect=change):
            self.assertEqual(self.post(tab="alignment").status_code, 409)

    def assert_separate_period_availability(self, excluded_period):
        import re
        from apps.departmental_exams.models import CycleCourse
        CycleCourse.objects.filter(pk=self.cycles[excluded_period].pk).update(inclusion_status="EXEMPT")
        result, selection, params = self.source(offering_id=None, course_code="")
        entry = next(e for e in result["classifications"] if e["id"] == self.offering_a.pk)
        expected = {code: "excluded" if code == excluded_period else "eligible" for code in ("MIDTERM", "FINAL")}
        self.assertEqual(entry.get("period_availability"), expected)
        included = next(p for p in entry["pairs"] if p["destination"] != excluded_period)
        self.assertEqual(included["exam"], 90 if excluded_period == "MIDTERM" else 80)
        context, _ = present(result, selection, params, "alignment", 1)
        self.assertEqual(result["sections"], 2)  # A valid class plus B, whose Final is pending.
        other = next(e for e in result["classifications"] if e["id"] == self.offering_b.pk)
        self.assertEqual(other["period_availability"], {"MIDTERM": "unavailable", "FINAL": "pending"})
        for period in context["availability_periods"]:
            self.assertEqual(period["total"], 2)
            self.assertEqual(sum(s["count"] for s in period["statuses"]), 2)
        for tab in ("alignment", "exams"):
            response = self.report(tab=tab, offering_id="", course_code="")
            for code, status in expected.items():
                row = re.search(rf'<tr data-availability-period="{code}">(.*?)</tr>', response["html"], re.S)
                self.assertIsNotNone(row)
                self.assertIn(f'data-status="{status}">1</td>', row.group(1))
                other_status = "unavailable" if code == "MIDTERM" else "pending"
                self.assertIn(f'data-status="{other_status}">1</td>', row.group(1))
            self.assertIn('Unique authorized sections</span><strong class="h3">2</strong>', response["html"])
            self.assertNotIn(self.students[0].student_no, response["html"])
        # Course drilldown must retain the period distinction too.
        course = self.report(tab="alignment", offering_id="")["html"]
        self.assertIn("Midterm: ", course)
        self.assertIn("Final: ", course)

    def test_midterm_included_final_exempt_have_separate_summaries(self):
        self.assert_separate_period_availability("FINAL")

    def test_midterm_exempt_final_included_have_separate_summaries(self):
        self.assert_separate_period_availability("MIDTERM")

    def test_exemption_precedes_pending_and_period_status_changes_stale_tokens(self):
        from apps.departmental_exams.models import CycleCourse
        token = self.report(tab="alignment")["token"]
        self.lock.is_locked = False; self.lock.save()
        CycleCourse.objects.filter(pk=self.cycles["FINAL"].pk).update(inclusion_status="EXEMPT")
        self.assertEqual(self.post("page", tab="exams", token=token).status_code, 409)
        result, _, _ = self.source()
        self.assertEqual(result["classifications"][0]["period_availability"], {"MIDTERM": "eligible", "FINAL": "excluded"})
        CycleCourse.objects.filter(pk=self.cycles["FINAL"].pk).update(inclusion_status="INCLUDED")
        CycleCourse.objects.filter(pk=self.cycles["MIDTERM"].pk).update(inclusion_status="EXEMPT")
        result, _, _ = self.source()
        self.assertEqual(result["classifications"][0]["period_availability"], {"MIDTERM": "excluded", "FINAL": "pending"})

    def test_previous_availability_cache_policy_cannot_reuse_old_token_or_result(self):
        with patch.object(assessment_alignment, "POLICY_VERSION", "template-formative-summative-v3"):
            old = self.report(tab="alignment")
        self.assertEqual(self.post("page", tab="alignment", token=old["token"]).status_code, 409)
        with patch.object(assessment_alignment, "build_report", wraps=assessment_alignment.build_report) as build:
            self.report(tab="alignment")
            self.assertEqual(build.call_count, 1)

    def test_return_verification_skips_build_render_and_result_cache_with_fewer_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from apps.admin_portal import unified_insights_views as view
        for tab in ('overview', 'exams', 'alignment'):
            with self.subTest(tab=tab):
                first = self.report(tab=tab)
                with CaptureQueriesContext(connection) as full:
                    page = self.post('page', tab=tab, token=first['token'])
                cache.clear()  # An evicted server result must not force a rebuild.
                with patch.object(learning_insights, 'build_report', side_effect=AssertionError('report rebuilt')), \
                     patch.object(assessment_alignment, 'build_report', side_effect=AssertionError('report rebuilt')), \
                     patch.object(view, 'present', side_effect=AssertionError('presentation rebuilt')), \
                     patch.object(view, 'render_to_string', side_effect=AssertionError('HTML rebuilt')), \
                     patch.object(view.cache, 'set', wraps=view.cache.set) as writes, \
                     patch.object(view.cache, 'get', wraps=view.cache.get) as reads, \
                     CaptureQueriesContext(connection) as verification:
                    response = self.post('verify', tab=tab, token=first['token'])
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(set(response.json()), {'token', 'tab', 'generation', 'valid'})
                self.assertTrue(response.json()['valid'])
                self.assertEqual(response.json()['generation'], first['generation'])
                self.assertIn('private', response['Cache-Control'])
                self.assertIn('no-store', response['Cache-Control'])
                self.assertFalse(any('-result:' in str(call.args[0]) for call in reads.call_args_list + writes.call_args_list))
                self.assertLess(len(verification), len(full))
                self.assertLess(len(response.content), len(page.content))
                print(f'Unified {tab}: full page {len(full)} queries; verify {len(verification)} queries; zero builds/renders/result-cache operations')

    def test_return_verification_rejects_missing_expired_engine_and_filter_tokens(self):
        from apps.admin_portal.unified_insights_views import TOKEN_SALT
        from django.core import signing
        data = self.report(tab='exams')
        for token in ('', 'tampered'):
            self.assertEqual(self.post('verify', tab='exams', token=token).status_code, 409)
        state = signing.loads(data['token'], salt=TOKEN_SALT)
        with patch('django.core.signing.time.time', return_value=1):
            expired = signing.dumps(state, salt=TOKEN_SALT, compress=True)
        self.assertEqual(self.post('verify', tab='exams', token=expired).status_code, 409)
        self.assertEqual(self.post('verify', tab='overview', token=data['token']).status_code, 409)
        self.assertEqual(self.post('verify', tab='exams', token=data['token'], offering_id='').status_code, 409)
        self.assertEqual(self.post('verify', tab='exams', token=data['token'], campus_id=999999).status_code, 409)

    def test_return_verification_rejects_changed_grades(self):
        data = self.report(tab='overview')
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(exam_grade=81)
        self.assertEqual(self.post('verify', tab='overview', token=data['token']).status_code, 409)

    def test_return_verification_rejects_changed_examination_policy(self):
        from apps.departmental_exams.models import CycleCourse, _classification_service_scope
        data = self.report(tab='alignment')
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.cycles['FINAL'].pk).update(exam_classification='EXEMPT')
        self.assertEqual(self.post('verify', tab='alignment', token=data['token']).status_code, 409)

    def test_return_verification_reauthorizes_monitor_and_analytics_direct_deny(self):
        data = self.report(tab='alignment')
        UserPermission.objects.create(user=self.area_chair, permission=self.permission, tenant=self.tenant,
            campus=self.campus, grant_type='DENY')
        self.assertEqual(self.post('verify', tab='alignment', token=data['token']).status_code, 409)
        analytics = Permission.objects.get(code='grading_analytics.read')
        UserPermission.objects.create(user=self.area_chair, permission=analytics, tenant=self.tenant,
            campus=self.campus, grant_type='DENY')
        self.assertEqual(self.post('verify', tab='alignment', token=data['token']).status_code, 403)

    def test_return_verification_keeps_closing_freshness_guard(self):
        data = self.report(tab='overview')
        original = learning_insights.learning_fingerprint
        calls = 0
        def change(selection, filters):
            nonlocal calls
            calls += 1
            value = original(selection, filters)
            if calls == 1:
                StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(exam_grade=81)
            return value
        with patch.object(learning_insights, 'learning_fingerprint', side_effect=change):
            self.assertEqual(self.post('verify', tab='overview', token=data['token']).status_code, 409)
        self.assertEqual(calls, 2)

    def test_verify_skips_dropdowns_and_reduces_queries_without_caching_authority(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for tab, budget in (("overview", 148), ("exams", 154), ("alignment", 154)):
            data = self.report(tab=tab)
            with patch.object(learning_insights.LearningSelection, 'options', side_effect=AssertionError('dropdowns rebuilt')), \
                 CaptureQueriesContext(connection) as queries:
                response = self.post('verify', tab=tab, token=data['token'])
            self.assertEqual(response.status_code, 200)
            self.assertLessEqual(len(queries), budget)
        # The closing authorization must independently observe a newly denied
        # grant even though this request has already made its opening batch.
        original = learning_insights.learning_fingerprint
        first = self.report(tab='overview')
        calls = 0
        def revoke(selection, filters):
            nonlocal calls
            calls += 1
            result = original(selection, filters)
            if calls == 1:
                UserPermission.objects.create(user=self.area_chair, permission=self.permission,
                    tenant=self.tenant, campus=self.campus, grant_type='DENY')
            return result
        with patch.object(learning_insights, 'learning_fingerprint', side_effect=revoke):
            self.assertEqual(self.post('verify', tab='overview', token=first['token']).status_code, 409)

    def test_unified_assets_are_versioned_and_quiet_state_is_accessible(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn('unified_insights.js?v=assessment-periods-', html)
        self.assertIn('id="learning-verification"', html)
        self.assertIn('Verifying latest results', html)

    def test_return_verification_rejects_another_authorized_user_and_forgery(self):
        from apps.rbac.models import UserRole
        data = self.report(tab='overview')
        self.assertEqual(self.post('verify', tab='overview', token='forged-signature').status_code, 409)
        other = self._user('other-academic-leader', self.campus, self.department)
        UserRole.objects.create(user=other, role=self.area_role, tenant=self.tenant,
            campus=self.campus, department=self.department)
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.post('verify', tab='overview', token=data['token']).status_code, 409)

    def _legacy_menu(self):
        from apps.navigation.models import MenuGroup, MenuItem, MenuItemPermission
        group = MenuGroup.objects.create(portal='ADMIN', code='UX_GRADING', label='Grading')
        item, _ = MenuItem.objects.get_or_create(portal='ADMIN', code='ACADEMIC_PERFORMANCE_INSIGHTS',
            defaults={'menu_group': group, 'label': 'Academic Performance Insights',
                'route_name': 'admin_portal:academic_performance_insights'})
        MenuItemPermission.objects.get_or_create(menu_item=item, permission=Permission.objects.get(code='grading_analytics.read'))
        MenuItem.objects.create(menu_group=group, portal='ADMIN', code='UX_OTHER', label='Other', route_name='admin_portal:dashboard')
        return item

    def test_existing_sidebar_record_opens_unified_without_reseed_or_legacy_export_changes(self):
        item = self._legacy_menu()
        response = self.client.get(self.url)
        nodes = [node for group in response.context['portal_menu'] for node in group['items']]
        matches = [node for node in nodes if node['item'].code == item.code]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['url'], self.url)
        self.assertEqual(next(node['url'] for node in nodes if node['item'].code == 'UX_OTHER'), reverse('admin_portal:dashboard'))
        item.refresh_from_db()
        self.assertEqual(item.route_name, 'admin_portal:academic_performance_insights')
        legacy = reverse('admin_portal:academic_performance_insights')
        self.assertEqual(legacy, '/admin-portal/grading/performance-insights/')
        self.assertContains(response, legacy + '?export=sections')
        self.assertContains(response, legacy + '?export=students')
        self.assertEqual(self.client.get(legacy).status_code, 200)
        for kind in ('sections', 'students'):
            exported = self.client.get(legacy, {**self.filters, 'period_code': 'MIDTERM', 'export': kind})
            self.assertEqual(exported.status_code, 200, exported.content[:200])
            self.assertIn('text/csv', exported['Content-Type'])

    def test_sidebar_override_preserves_denied_and_feature_disabled_visibility(self):
        from apps.core.context_processors import portal_menu
        from apps.core.services.features import FeatureSettingsService
        from apps.core.services.settings import SystemSettingService
        item = self._legacy_menu()
        request = self.client.get(self.url).wsgi_request
        SystemSettingService.set(FeatureSettingsService.ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY,
            False, tenant_id=self.tenant.pk, value_type='BOOL', is_active=True)
        self.assertFalse(any(node['item'].code == item.code for group in portal_menu(request)['portal_menu'] for node in group['items']))
        self._enable_feature()
        UserPermission.objects.create(user=self.area_chair, permission=Permission.objects.get(code='grading_analytics.read'),
            tenant=self.tenant, campus=self.campus, grant_type='DENY')
        request = self.client.get(reverse('admin_portal:dashboard')).wsgi_request
        self.assertFalse(any(node['item'].code == item.code for group in portal_menu(request)['portal_menu'] for node in group['items']))
        self.assertEqual(self.client.get(self.url).status_code, 403)
