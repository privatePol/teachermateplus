"""Positive frozen releases and conservative comparison controls."""
import json
import os
import subprocess
import sys
import time
import unittest
from io import StringIO
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection
from django.http import QueryDict
from django.test import Client, RequestFactory, TransactionTestCase
from django.test.utils import CaptureQueriesContext

from apps.admin_portal import tests_grade_distribution_monitor as fixtures
from apps.admin_portal.grade_distribution import GradeDistributionMonitorService as Monitor
from apps.admin_portal.grade_distribution_releases import prepare_release as controlled_prepare, publish_release
from apps.admin_portal.models import GradeDistributionPublication, GradeDistributionRelease
from apps.grading.models import StudentPeriodGrade


def prepare_release(*args, **kwargs):
    # Test fixtures start with reviewed, empty prior-disclosure history.
    return controlled_prepare(*args, legacy_history_cleared=True, **kwargs)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([
        unittest.TestSuite(loader.loadTestsFromName(name, GradeDistributionReleaseTests)
            for name in GradeDistributionReleaseTests.__dict__ if name.startswith("test_")),
        loader.loadTestsFromTestCase(ReleaseRestartTests),
    ])


class GradeDistributionReleaseTests(fixtures.GradeDistributionMonitorTests):
    def setUp(self):
        super().setUp()
        self.admin_user.is_superuser = True
        self.admin_user.save(update_fields=["is_superuser"])
        self.approver = self._user("approver", "Approval", "User", self.tenant, self.campus, self.department)
        self.approver.is_superuser = True
        self.approver.save(update_fields=["is_superuser"])
        self.client.force_login(self.admin_user)

    def controller(self, actor=None):
        request = RequestFactory().post(self.url, dict(apply="1", campus_id=self.campus.id,
            academic_year_id=self.academic_year.id, term_id=self.term.id, period="PRELIM"))
        request.GET = request.POST.copy()
        request.user = actor or self.admin_user
        request.scope = dict(tenant_id=self.tenant.id, campus_id=self.campus.id)
        return request

    def safe_source(self, code="SAFE", values=None):
        offering = self._create_extra_offering(code, "Safe release", code)
        self._seed_grades(offering, values if values is not None else [98]*8 + [80]*12)
        return offering

    def publish(self, offering):
        release = prepare_release(self.controller(), offering.pk, self.period.pk, "Reviewed source and disclosure policy")
        publish_release(self.controller(self.approver), release.pk, "Independent approval")
        return release

    def row(self, response, offering):
        return next(row for row in response.context["rows"] if row["offering_id"] == offering.pk)

    def test_positive_frozen_release_html_ajax_modal_excel_and_no_private_commitments(self):
        offering = self.safe_source(values=[97, Decimal("97.50"), 98, 99, Decimal("99.99"), 100, 98, 98]+[80]*12)
        release = prepare_release(self.controller(), offering.pk, self.period.pk, "Review")
        self.assertEqual(self.row(self.search(), offering)["range_count"], 8)
        publish_release(self.controller(self.approver), release.pk, "Approve")
        started = time.perf_counter()
        with CaptureQueriesContext(connection) as queries:
            response = self.search()
        print("GRADE_RELEASE_APPLY="+json.dumps(dict(classes=2, queries=len(queries), seconds=round(time.perf_counter()-started, 4))))
        row = self.row(response, offering)
        self.assertEqual((row["range_count"], row["range_denominator"], row["range_percentage"]), (8, 20, Decimal("40")))
        self.assertEqual(response.context["rows"][0]["offering_id"], offering.pk)
        self.assertContains(response, "8 / 20")
        ajax = self.client.get(self.url+"?"+response.context["page_query"]+"&results=1")
        self.assertIn("8 / 20", ajax.json()["html"])
        data = self.detail(response, row).json()
        self.assertEqual(data["range_count"], 8)
        for child in [*data["measures"], data["quiz"]]:
            self.assert_internal_measure(child)
        self.assert_internal_measure(data)
        self.assertEqual(data["incomplete_source_count"], 20)
        sheet = self._workbook(self.client.get(self.url+"?"+response.context["export_query"])).active
        record = next(r for r in list(sheet.values)[1:] if r[2].startswith("SAFE") and r[4] == "Period Grades")
        self.assertEqual(record[5:8], (8, 20, 40))
        public = response.content.decode()+json.dumps(data)+ajax.content.decode()+str(list(sheet.values))
        for private in (release.fingerprint, *release.cohort):
            self.assertNotIn(private, public)
        self.assertEqual(GradeDistributionRelease.objects.count(), 1)
        self.assertEqual(GradeDistributionPublication.objects.count(), 1)
        if os.environ.get("TMP_UX_DOM_PAYLOAD"):
            Path(os.environ["TMP_UX_DOM_PAYLOAD"]+".released").write_text(json.dumps(data), encoding="utf-8")

    def test_safe_zero_four_five_and_complement_boundaries(self):
        for index, (values, expected) in enumerate((([80]*5, 0), ([98]*4, None), ([98]*5, 5),
                ([98]*5+[80]*4, None), ([98]*4+[80]*5, None), ([98]*5+[80]*5, 5))):
            offering = self.safe_source(f"BOUND{index}", values)
            self.publish(offering)
            row = self.row(self.search(), offering)
            self.assertEqual(row["range_count"], sum(97 <= v <= 100 for v in values))
            self.assertEqual(row["range_state"], "disclosed")

    def test_positive_and_zero_release_sorting_pagination_detail_export_parity(self):
        offering = self.safe_source("ZZSAFE")
        zero = None
        for index in range(25):
            other = self.safe_source(f"PAGE{index:02}", [80]*5)
            zero = zero or other
        self.publish(offering)
        self.publish(zero)
        ordered = self.search()
        self.assertEqual([r["range_count"] for r in ordered.context["rows"][:2]], [8, 1])
        response = self.search(sort="faculty_asc")
        self.assertEqual(response.context["result_count"], 27)
        second = self.continue_search(response.context["page_query"])
        row = self.row(second, offering)
        self.assertEqual(row["range_count"], 8)
        ajax = self.client.get(self.url+"?"+response.context["page_query"]+"&page=2&results=1")
        self.assertIn("8 / 20", ajax.json()["html"])
        self.assertEqual(self.detail(second, row).json()["range_count"], 8)
        records = list(self._workbook(self.client.get(self.url+"?"+response.context["export_query"])).active.values)[1:]
        period_records = [r for r in records if r[4] == "Period Grades"]
        self.assertEqual(len(period_records), 27)
        self.assertEqual(next(r for r in period_records if r[2].startswith("ZZSAFE"))[5:8], (8, 20, 40))
        self.assertEqual(next(r for r in period_records if r[2].startswith("PAGE00"))[5:8], (0, 5, 0))

    def test_custom_ranges_and_status_subsets_are_independent_of_legacy_release(self):
        offering = self.safe_source(values=[98]*6+[Decimal("97.50")]+[80]*13)
        self.publish(offering)
        for filters in ({}, {"grade_min":"97.51"}, {"grade_min":"0"}, {"grade_min":"97.5", "grade_max":"97.5"},
                        {"status":"unfinalized"}, {"status":"finalized"}):
            response = self.search(**filters)
            row = self.row(response, offering)
            self.assertEqual(row["range_count"], None if filters.get("status") == "finalized" else 6 if filters.get("grade_min") == "97.51" else 20 if filters.get("grade_min") == "0" else 1 if filters.get("grade_max") == "97.5" else 7)
            self.assertEqual(row["grade_min"], filters.get("grade_min", "97"))
            self.assertEqual(self.detail(response, row).json()["range_count"], row["range_count"])
            record = next(r for r in list(self._workbook(self.client.get(self.url+"?"+response.context["export_query"])).active.values)[1:]
                if r[2].startswith("SAFE") and r[4] == "Period Grades")
            self.assertEqual(record[5], row["range_count"])

    def test_status_complement_and_existing_formula_suppression_are_required(self):
        offering = self.safe_source()
        StudentPeriodGrade.objects.filter(offering=offering, pk=StudentPeriodGrade.objects.filter(offering=offering).first().pk).update(is_finalized=True)
        self.assertFalse(self.publish(offering).disclosed)
        self._formula_fixture(supplemental_count=1)
        self.assertFalse(self.publish(self.offering).disclosed)

    def test_live_source_changes_update_monitor_but_legacy_snapshot_stays_immutable(self):
        offering = self.safe_source()
        release = self.publish(offering)
        original = self.search()
        StudentPeriodGrade.objects.filter(offering=offering).update(period_grade=100)
        response = self.search()
        row = self.row(response, offering)
        self.assertEqual(row["range_count"], 20)
        self.assertEqual(self.detail(original, self.row(original, offering)).json()["range_count"], 20)
        self.assertNotIn("8 / 20", self.client.get(self.url+"?"+original.context["page_query"]+"&results=1").json()["html"])
        record = next(r for r in list(self._workbook(self.client.get(self.url+"?"+original.context["export_query"])).active.values)[1:]
            if r[2].startswith("SAFE") and r[4] == "Period Grades")
        self.assertEqual(record[5:8], (20, 20, 100))
        release.refresh_from_db()
        self.assertEqual(release.qualifying_count, 8)
        with self.assertRaises(ValidationError):
            prepare_release(self.controller(), offering.pk, self.period.pk, "Changed source")

    def test_changed_preparation_needs_new_version_and_distinct_approver(self):
        offering = self.safe_source()
        release = prepare_release(self.controller(), offering.pk, self.period.pk, "Review")
        with self.assertRaises(ValidationError):
            publish_release(self.controller(), release.pk, "Self approval")
        StudentPeriodGrade.objects.filter(offering=offering).update(exam_grade=50)
        with self.assertRaises(ValidationError):
            publish_release(self.controller(self.approver), release.pk, "Stale source")
        revised = prepare_release(self.controller(), offering.pk, self.period.pk, "Recheck")
        self.assertEqual(revised.version, 2)
        publish_release(self.controller(self.approver), revised.pk, "Approve revision")

    def test_equivalent_partial_cohorts_and_conflicting_prepared_releases_are_blocked(self):
        first = self.safe_source()
        others = []
        for size in (20, 5):
            other = self._create_extra_offering(f"OVER{size}", "Overlap", f"OVER{size}")
            for record in StudentPeriodGrade.objects.filter(offering=first).order_by("-period_grade")[:size]:
                fixtures.Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
                    term=self.term, course_offering=other, student=record.student)
                StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=other,
                    template_period=self.period, student=record.student, period_grade=record.period_grade)
            others.append(other)
        drafts = [prepare_release(self.controller(), o.pk, self.period.pk, "Review") for o in [first, *others]]
        publish_release(self.controller(self.approver), drafts[0].pk, "First approval")
        for draft in drafts[1:]:
            with self.assertRaises(ValidationError):
                publish_release(self.controller(self.approver), draft.pk, "Conflicting approval")
            with self.assertRaises(ValidationError):
                prepare_release(self.controller(), draft.offering_id, self.period.pk, "Overlap")

    def test_unreviewed_legacy_history_and_key_rotation_fail_closed(self):
        offering = self.safe_source()
        with self.assertRaises(ValidationError):
            controlled_prepare(self.controller(), offering.pk, self.period.pk, "No history attestation")
        self.publish(offering)
        other = self.safe_source("NEWKEY")
        with self.settings(SECRET_KEY="rotated-secret-key"):
            self.client.force_login(self.admin_user)
            with self.assertRaises(ValidationError):
                prepare_release(self.controller(), other.pk, self.period.pk, "Cannot compare commitments")
            self.assertEqual(self.row(self.search(), offering)["range_count"], 8)

    def test_legacy_publication_does_not_change_monitor_selection_or_values(self):
        offering = self.safe_source()
        response = self.search()
        self.publish(offering)
        self.assertEqual(self.client.get(self.url+"?"+response.context["page_query"]+"&results=1").status_code, 200)
        self.assertEqual(self.client.get(self.url+"?"+response.context["export_query"]).status_code, 200)
        self.assertEqual(self.detail(response, self.row(response, offering)).json()["range_count"], 8)

    def test_snapshots_are_append_only_and_ordinary_routes_never_publish(self):
        offering = self.safe_source()
        release = self.publish(offering)
        with self.assertRaises(ValidationError):
            release.save()
        for operation in (lambda: release.delete(), lambda: GradeDistributionRelease.objects.filter(pk=release.pk).update(qualifying_count=9),
                lambda: GradeDistributionRelease._base_manager.filter(pk=release.pk).update(qualifying_count=9),
                lambda: GradeDistributionPublication.objects.all().delete()):
            with self.assertRaises(ValidationError):
                operation()
        with patch("apps.admin_portal.grade_distribution_releases.prepare_release", side_effect=AssertionError("HTTP preparation")), \
             patch("apps.admin_portal.grade_distribution_releases.publish_release", side_effect=AssertionError("HTTP publication")):
            response = self.search()
            self.detail(response, self.row(response, offering))
            self.continue_search(response.context["page_query"])
            self.client.get(self.url+"?"+response.context["export_query"])
        self.assertEqual(GradeDistributionPublication.objects.count(), 1)

    def test_operator_commands_prepare_then_independent_publication(self):
        offering = self.safe_source()
        output = StringIO()
        call_command("grade_distribution_release", "prepare", actor=self.admin_user.pk, tenant=self.tenant.pk,
            offering=offering.pk, period=self.period.pk, note="Reviewed prior report evidence", legacy_history_cleared=True, stdout=output)
        release = GradeDistributionRelease.objects.get(offering=offering)
        self.assertFalse(GradeDistributionPublication.objects.exists())
        call_command("grade_distribution_release", "publish", actor=self.approver.pk, tenant=self.tenant.pk,
            release=release.pk, note="Independent evidence review", stdout=output)
        self.assertEqual(self.row(self.search(), offering)["range_count"], 8)
        self.assertNotIn(release.fingerprint, output.getvalue())

    def test_non_js_post_refresh_and_pagination_replay_do_not_execute_analytics(self):
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.admin_user)
        shell = browser.get(self.url)
        data = dict(apply="1", academic_year_id=self.academic_year.pk, term_id=self.term.pk, period="PRELIM",
            manual_intent=shell.context["manual_intent"], csrfmiddlewaretoken=str(shell.context["csrf_token"]))
        applied = browser.post(self.url, data)
        self.assertTrue(applied.context["searched"])
        with patch.object(Monitor, "prepare", side_effect=AssertionError("POST replay ran analytics")):
            refreshed = browser.post(self.url, data)
            for invalid in ("", "\u00e9", "x"*101, ["duplicate", "duplicate"]):
                malformed = browser.post(self.url, {**data, "manual_intent": invalid})
                self.assertFalse(malformed.context["searched"])
        self.assertFalse(refreshed.context["searched"])
        self.assertContains(refreshed, "already submitted or expired")
        continuation = QueryDict(applied.context["page_query"]).copy()
        continuation["manual_intent"] = applied.context["manual_intent"]
        continuation["csrfmiddlewaretoken"] = data["csrfmiddlewaretoken"]
        continuation["page"] = "1"
        self.assertTrue(browser.post(self.url, continuation).context["searched"])
        with patch.object(Monitor, "prepare", side_effect=AssertionError("page refresh ran analytics")):
            self.assertFalse(browser.post(self.url, continuation).context["searched"])

    def test_publication_and_positive_read_recheck_direct_deny_and_owner(self):
        offering = self.safe_source()
        self.publish(offering)
        response = self.search()
        permission = fixtures.Permission.objects.get(code="grade_distribution_monitor.read")
        fixtures.UserPermission.objects.create(user=self.admin_user, permission=permission, tenant=self.tenant,
            campus=self.campus, grant_type="DENY")
        self.assertEqual(self.search().status_code, 403)
        self.assertEqual(self.detail(response, self.row(response, offering)).status_code, 400)
        with self.assertRaises(ValidationError):
            prepare_release(self.controller(), offering.pk, self.period.pk, "Denied")
        self.client.force_login(self.approver)
        self.assertEqual(self.client.get(self.url+"?"+response.context["export_query"]).status_code, 400)
        self.approver.is_superuser = False
        with self.assertRaises(PermissionDenied):
            prepare_release(self.controller(self.approver), offering.pk, self.period.pk, "Not controller")

    def test_every_normal_get_is_shell_and_unsigned_results_cannot_start_search(self):
        response = self.search()
        params = QueryDict(response.context["page_query"]).copy()
        params.update(dict(page="2", render="full"))
        with patch.object(Monitor, "prepare", side_effect=AssertionError("GET search")), CaptureQueriesContext(connection) as queries:
            for query in ({}, params, dict(apply="1", render="full", academic_year_id=self.academic_year.id,
                    term_id=self.term.id, period="PRELIM")):
                self.assertEqual(self.client.get(self.url, query).context["rows"], [])
        self.assertFalse(any("student_period_grades" in q["sql"] for q in queries))
        unsigned = self.client.get(self.url, dict(apply="1", results="1", academic_year_id=self.academic_year.id,
            term_id=self.term.id, period="PRELIM"))
        self.assertEqual(unsigned.status_code, 400)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin_user)
        self.assertEqual(csrf.post(self.url, {"apply":"1"}).status_code, 403)


class ReleaseRestartTests(TransactionTestCase):
    # Reuse only fixture helpers, not its TestCase transaction wrapper or tests.
    for _name in ("setUp", "_user", "_assign_permissions", "_assign_faculty_role", "_seed_grades", "_create_extra_offering"):
        locals()[_name] = getattr(fixtures.GradeDistributionMonitorTests, _name)

    def test_fresh_python_process_reads_same_published_snapshot_from_disposable_db(self):
        self.admin_user.is_superuser = True
        self.admin_user.save(update_fields=["is_superuser"])
        approver = self._user("restart_approve", "Approve", "Restart", self.tenant, self.campus, self.department)
        approver.is_superuser = True
        approver.save(update_fields=["is_superuser"])
        offering = self._create_extra_offering("RESTART", "Restart", "RESTART")
        self._seed_grades(offering, [98]*8+[80]*12)
        request = RequestFactory().post(self.url, dict(apply="1", campus_id=self.campus.id,
            academic_year_id=self.academic_year.id, term_id=self.term.id, period="PRELIM"))
        request.GET, request.user = request.POST.copy(), self.admin_user
        request.scope = dict(tenant_id=self.tenant.id, campus_id=self.campus.id)
        release = prepare_release(request, offering.pk, self.period.pk, "Restart test")
        request.user = approver
        publish_release(request, release.pk, "Restart approval")
        code = """
import os,sys,json
os.environ['DJANGO_SETTINGS_MODULE']='duplicate_test_settings'
import django
from django.conf import settings
settings.DATABASES['default']['NAME']=sys.argv[1]
django.setup()
from apps.admin_portal.grade_distribution_releases import published_map
release=published_map(int(sys.argv[2]),[int(sys.argv[3])],[int(sys.argv[4])])
r=next(iter(release.values()))
from django.contrib.auth import get_user_model
from django.test import Client
from unittest.mock import patch
from datetime import date
client=Client()
client.force_login(get_user_model().objects.get(pk=r.created_by_id))
with patch('apps.admin_portal.grade_distribution.timezone.localdate',return_value=date(2025,7,1)):
    response=client.post('/admin-portal/grading/grade-distribution-monitor/',
        dict(apply='1',results='1',academic_year_id=r.offering.academic_year_id,term_id=r.offering.term_id,period='PRELIM'))
served=response.status_code==200 and '8 / 20' in response.json()['html']
print(json.dumps([r.qualifying_count,r.denominator,str(r.percentage),served]))
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, str(connection.settings_dict["NAME"]),
            str(self.tenant.pk), str(offering.pk), str(self.period.pk)], capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [8, 20, "40.00", True])
