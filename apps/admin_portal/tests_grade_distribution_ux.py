"""Current public UX contract; historical arithmetic fixtures stay in the monitor suite."""
import json
import os
import unittest
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.http import QueryDict
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.admin_portal import tests_grade_distribution_monitor as fixtures
from apps.admin_portal.grade_distribution import GradeDistributionMonitorService as Monitor
from apps.admin_portal.grade_distribution_disclosure import (
    CELL_STARTS, candidate_range, parse_bounds, partition_counts, public_measure,
    range_is_cell_union, range_label,
)
from apps.admin_portal.grade_distribution_metrics import PrivacyFamily, component_measures, metrics, threshold_for
from apps.grading.models import StudentPeriodGrade


def load_tests(loader, tests, pattern):
    # The subclass reuses fixture helpers; do not rediscover obsolete public UI
    # assertions through inheritance. The original module remains independently
    # discoverable and its unaffected safety/calculation tests are run separately.
    return unittest.TestSuite([
        loader.loadTestsFromTestCase(GradeRangeDisclosureTests),
        unittest.TestSuite(loader.loadTestsFromName(name, GradeDistributionUXTests)
            for name in GradeDistributionUXTests.__dict__ if name.startswith("test_")),
    ])


class GradeRangeDisclosureTests(SimpleTestCase):
    def test_decimal_validation_and_defaults(self):
        self.assertEqual(parse_bounds(QueryDict()), (Decimal("97"), Decimal("100")))
        for query in ("grade_min=0&grade_max=100", "grade_min=.5&grade_max=99.999", "grade_min=97.500&grade_max=97.5"):
            lower, upper = parse_bounds(QueryDict(query))
            self.assertLessEqual(lower, upper)
        self.assertEqual(range_label((Decimal("97.500"), Decimal("100"))), "Grades 97.5–100")
        for value in ("", "NaN", "Infinity", "-1", "101", "1e2", "abc", "1,5", "9" * 33):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse_bounds(QueryDict("grade_min=" + value))
        for query in ("grade_min=98&grade_max=97", "grade_min=97&grade_min=97", "grade_max=100&grade_max=99"):
            with self.assertRaises(ValidationError):
                parse_bounds(QueryDict(query))

    def test_fixed_partition_and_inclusive_unrounded_arithmetic(self):
        self.assertEqual(CELL_STARTS[-1], Decimal("97"))
        self.assertTrue(range_is_cell_union((Decimal("97"), Decimal("100"))))
        self.assertFalse(range_is_cell_union((Decimal("97.51"), Decimal("100"))))
        frequency = [{"grade": str(v), "count": 1} for v in (0, 97, Decimal("97.001"), 100, 101)]
        self.assertEqual(candidate_range(frequency, (Decimal("97"), Decimal("100"))), (3, 5))
        self.assertEqual(candidate_range(frequency, (Decimal("0"), Decimal("0"))), (1, 5))
        self.assertEqual(sum(partition_counts(frequency)), 5)

    def test_demonstrated_seven_minus_six_never_becomes_public(self):
        raw = metrics([98] * 6 + [Decimal("97.50")] + [80] * 13, threshold=75, eligible_count=20)
        raw["analytics_state"] = "disclosed"
        a, b = (Decimal("97"), Decimal("100")), (Decimal("97.51"), Decimal("100"))
        self.assertEqual(candidate_range(raw["frequency"], a)[0], 7)
        self.assertEqual(candidate_range(raw["frequency"], b)[0], 6)
        for bounds in (a, b, a, (Decimal("97.50"), Decimal("97.50"))):
            result = public_measure(raw, bounds)
            self.assertIsNone(result["range_count"])
            self.assertIsNone(result["range_percentage"])
            self.assertEqual(result["frequency"], [])
            self.assertIsNone(result["average"])
            self.assertIsNone(result["minimum"])
            self.assertIsNone(result["maximum"])

    def test_all_locally_safe_cells_zeroes_and_homogeneous_groups_still_hold(self):
        for values in ([98] * 5 + [80] * 5, [80] * 10, [100] * 10, []):
            for bounds in ((Decimal("97"), Decimal("100")), (Decimal("0"), Decimal("100"))):
                raw = metrics(values, threshold=75, eligible_count=10)
                raw.update(analytics_state="disclosed", equivalent_to="secret", equivalent_dataset_id="hidden")
                result = public_measure(raw, bounds)
                self.assertEqual(result["privacy_reason"], "release_history_unverified")
                self.assertNotIn("equivalent_to", result)
                self.assertNotIn("equivalent_dataset_id", result)
                self.assertIsNone(result["range_denominator"])

    def test_hold_notices_do_not_expose_value_dependent_lineage_decisions(self):
        a = metrics([98] * 10, threshold=75, eligible_count=10)
        b = metrics([80] * 10, threshold=75, eligible_count=10)
        a.update(analytics_state="disclosed", calculation_note="Saved totals match")
        b.update(analytics_state="privacy_suppressed", privacy_message="Formula values differ", calculation_note="Saved totals differ")
        bounds = (Decimal("97"), Decimal("100"))
        self.assertEqual(public_measure(a, bounds), public_measure(b, bounds))

    def test_sort_never_uses_a_withheld_count_or_hidden_tie(self):
        base = dict(faculty_name="Faculty", campus="FV", course_code="ACC", section="A", physical_period_id=1)
        rows = [dict(base, offering_id=i, range_state=state, range_count=count) for i, state, count in
                ((2, "withheld", 999), (1, "withheld", 1), (3, "disclosed", 0), (4, "disclosed", 12))]
        self.assertEqual([r["offering_id"] for r in sorted(rows, key=Monitor.sort_key)], [4, 3, 1, 2])
        rows[0]["range_count"], rows[1]["range_count"] = 0, 9999
        self.assertEqual([r["offering_id"] for r in sorted(rows, key=Monitor.sort_key)], [4, 3, 1, 2])


class GradeDistributionUXTests(fixtures.GradeDistributionMonitorTests):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin_user)

    def params(self, **kwargs):
        return dict(apply="1", academic_year_id=self.academic_year.id, term_id=self.term.id, period="PRELIM", **kwargs)

    def assert_withheld(self, measure):
        for key in ("range_count", "range_denominator", "range_percentage", "average", "minimum", "maximum", "exact_100_count", "all_100"):
            self.assertIsNone(measure[key], key)
        for key in ("frequency", "distribution", "high_grade_breakdown"):
            self.assertEqual(measure[key], [], key)
        self.assertNotIn("equivalent_dataset_id", measure)

    def local_analysis(self, *, historical=False, status="all"):
        """Inspect the internal arithmetic separately from the public HOLD."""
        offering = fixtures.CourseOffering.objects.select_related("tenant", "campus", "department", "program", "course", "section", "term").get(pk=self.offering.pk)
        records = list(StudentPeriodGrade.objects.filter(offering=offering, template_period=self.period).values("student_id", "period_grade", "exam_grade", "class_standing_grade", "is_finalized"))
        ids = set(fixtures.Enrollment.objects.filter(course_offering=offering).values_list("student_id", flat=True))
        threshold, _ = threshold_for(offering)
        privacy = PrivacyFamily({r["student_id"]: r["is_finalized"] for r in records}, status, threshold)
        return component_measures(offering, self.period, records, ids, threshold, historical, privacy)

    def test_internal_base50_values_still_match_saved_gradebook_columns(self):
        activity, ids = self._gradebook_activity_fixture(values=[0, 10, 20, 25, 30, 35, 40, 45, 49, 50])
        self._persist_formula_totals(ids)
        period, measures, _, _, _ = self.local_analysis()
        activity_measure = next(m for m in measures if m["activity_id"] == activity.id)
        from collections import Counter
        actual = {Decimal(f["grade"]): f["count"] for f in activity_measure["frequency"]}
        saved = fixtures.StudentActivityScore.objects.filter(activity=activity).values_list("computed_score", flat=True)
        self.assertEqual(actual, Counter(saved))
        self.assertIn(Decimal("75"), actual)
        self.assertNotIn(Decimal("25"), actual)
        self.assertEqual(period["analytics_state"], "disclosed")
        self.assert_withheld(self.detail(self.search()).json())

    def test_internal_exact_decimal_counts_and_over_100_values_are_not_clamped(self):
        StudentPeriodGrade.objects.filter(offering=self.offering).update(period_grade=Decimal("99.75"), exam_grade=Decimal("150"), class_standing_grade=Decimal("99.75"))
        period, measures, _, _, _ = self.local_analysis(historical=True)
        self.assertEqual(period["frequency"], [{"grade": Decimal("99.75"), "count": 10, "percentage": Decimal("100")}])
        self.assertEqual(measures[0]["maximum"], Decimal("150"))
        self.assertEqual(candidate_range(measures[0]["frequency"], (Decimal("97"), Decimal("100"))), (0, 10))
        self.assert_withheld(self.detail(self.search()).json())

    def test_internal_sparse_formula_protection_survives_new_public_boundary(self):
        self._formula_fixture(supplemental_count=1)
        period, measures, _, _, _ = self.local_analysis()
        self.assertEqual(period["analytics_state"], "privacy_suppressed")
        self.assertEqual(measures[0]["analytics_state"], "privacy_suppressed")
        self.assert_withheld(self.detail(self.search()).json())

    def test_internal_quiz_adapter_and_historical_snapshot_semantics_remain_authoritative(self):
        self._calculated_quiz_fixture()
        _, _, _, _, quiz = self.local_analysis()
        self.assertEqual(quiz["analytics_state"], "disclosed")
        self.assertEqual(sum(item["count"] for item in quiz["frequency"]), quiz["graded_count"])
        _, _, _, _, historical = self.local_analysis(historical=True)
        self.assertEqual(historical["frequency"], [])
        self.assertIn("historical", historical["calculation_note"])
        self.assert_withheld(self.detail(self.search()).json()["quiz"])

    def test_applied_shell_is_grade_free_and_has_fallback_and_range(self):
        with patch.object(Monitor, "prepare", side_effect=AssertionError("shell ran analytics")), CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, self.params())
        self.assertContains(response, "Grade Range")
        self.assertContains(response, 'value="97"')
        self.assertContains(response, 'value="100"')
        self.assertNotContains(response, 'data-auto-load="true"')
        self.assertContains(response, 'method="post"')
        self.assertNotContains(response, 'name="course_id"')
        self.assertNotContains(response, "Search course codes")
        self.assertEqual(response.context["rows"], [])
        self.assertFalse(any("student_period_grades" in q["sql"] or "student_activity_scores" in q["sql"] for q in queries))

    def test_invalid_and_obsolete_parameters_fail_before_grade_reads(self):
        for extra in ({"course_id": "1"}, {"only_100": "1"}, {"grade_min": "NaN"}, {"grade_min": "99", "grade_max": "97"}, {"grade_min": ["97", "97"]}, {"sort": "exact_100_desc"}):
            with self.subTest(extra=extra), patch.object(Monitor, "prepare", wraps=Monitor.prepare), CaptureQueriesContext(connection) as queries:
                response = self.client.post(self.url, self.params(results="1", **extra))
            self.assertEqual(response.status_code, 400)
            self.assertTrue(response.json()["errors"])
            self.assertFalse(any("student_period_grades" in q["sql"] for q in queries))

    def test_clear_response_has_defaults_without_analytics(self):
        with patch.object(Monitor, "prepare", side_effect=AssertionError("clear ran analytics")):
            data = self.client.get(self.url, {"results": "1"}).json()
        self.assertEqual((data["selected"]["grade_min"], data["selected"]["grade_max"]), ("97", "100"))
        self.assertIn("Select criteria", data["html"])
        self.assertEqual(data["url"], self.url)

    def test_ajax_full_detail_and_excel_share_hold_and_requested_bounds(self):
        response = self.search(grade_min="97.51")
        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assert_withheld(row)
        self.assertContains(response, "Grades 97.51–100")
        ajax = self.client.post(self.url, self.params(results="1", grade_min="97.51"))
        self.assertEqual(ajax.status_code, 200)
        self.assertIn("Grades 97.51–100", ajax.json()["html"])
        data = self.detail(response).json()
        for measure in [data, *data["measures"], data["quiz"]]:
            self.assert_withheld(measure)
            self.assertEqual(measure["grade_min"], "97.51")
        self.assertNotIn("notes", data)
        workbook = self._workbook(self.client.get(self.url + "?" + response.context["export_query"]))
        rows = list(workbook.active.values)
        self.assertEqual(rows[0][5:9], ("Grades 97.51–100", "Total Graded Students", "Percentage", "Analytics Status"))
        self.assertEqual([r[4] for r in rows[1:]], ["Period Grades", "Examination Grades", "Class Standing Grades", "Quizzes"])
        self.assertTrue(all(r[5:8] == (None, None, None) and r[8] == "Withheld" for r in rows[1:]))

    def test_saved_values_and_sources_remain_unchanged_by_every_route(self):
        self._gradebook_activity_fixture()
        before = list(StudentPeriodGrade.objects.values())
        scores = list(fixtures.StudentActivityScore.objects.values())
        with patch.object(fixtures.FacultyGradingService, "recompute_period_summary_for_students", side_effect=AssertionError("write")):
            response = self.search()
            self.detail(response)
            self.client.get(self.url + "?" + response.context["export_query"])
        self.assertEqual(before, list(StudentPeriodGrade.objects.values()))
        self.assertEqual(scores, list(fixtures.StudentActivityScore.objects.values()))

    def test_current_django_dom_payload(self):
        self._gradebook_activity_fixture(exam=True, title="Standing title")
        data = self.detail(self.search()).json()
        self.assert_withheld(data)
        if os.environ.get("TMP_UX_DOM_PAYLOAD"):
            Path(os.environ["TMP_UX_DOM_PAYLOAD"]).write_text(json.dumps(data), encoding="utf-8")

    def test_structural_groups_ignore_activity_titles_and_keep_units(self):
        activity, ids = self._gradebook_activity_fixture(exam=True, title="Class Standing quiz")
        self._persist_formula_totals(ids)
        data = self.detail(self.search()).json()
        measure = next(m for m in data["measures"] if m["activity_id"] == activity.id)
        self.assertEqual(measure["group"], "examination")
        self.assertIn("may exceed 100", data["measures"][0]["unit"])
        self.assert_withheld(measure)

    def test_range_context_cannot_be_tampered_reused_by_user_or_expired(self):
        response = self.search(grade_min="97.500")
        query = response.context["page_query"]
        self.assertIn("grade_min=97.5", query)
        self.assertEqual(self.client.get(self.url + "?" + query.replace("grade_min=97.5", "grade_min=97.51") + "&results=1").status_code, 400)
        self.assertEqual(self.client.get(self.url + "?" + query + "&grade_min=97.5&results=1").status_code, 400)
        with patch("apps.admin_portal.grade_distribution_selection.signing.time.time", return_value=9999999999):
            self.assertEqual(self.client.get(self.url + "?" + query + "&results=1").status_code, 400)

    def test_every_mode_is_private_no_store_including_denial_and_redirect(self):
        response = self.search()
        urls = [self.url, self.url + "?results=1", self.url + "?options=1", self.url + "?" + response.context["export_query"]]
        detail_url = reverse("admin_portal:grade_distribution_monitor_detail", args=[self.offering.id, self.period.id]) + "?" + response.context["rows"][0]["detail_query"]
        for url in urls + [detail_url]:
            cached = self.client.get(url)
            self.assertIn("no-store", cached["Cache-Control"])
            self.assertIn("private", cached["Cache-Control"])
        fixtures.UserPermission.objects.create(user=self.admin_user, permission=fixtures.Permission.objects.get(code="grade_distribution_monitor.read"), tenant=self.tenant, campus=self.campus, grant_type="DENY")
        self.assertEqual(self.client.get(self.url + "?results=1").status_code, 403)
        self.assertIn("no-store", self.client.get(detail_url)["Cache-Control"])
        self.client.logout()
        self.assertEqual(self.client.get(self.url + "?results=1").status_code, 302)
        self.assertIn("no-store", self.client.get(self.url)["Cache-Control"])

    def test_zero_qualifying_saved_classes_and_pagination_remain_visible(self):
        for index in range(26):
            other = self._create_extra_offering(f"UX{index:02}", "Zero qualifying", f"UX{index:02}")
            self._seed_grades(other, [80] * 5)
        response = self.search()
        self.assertEqual(response.context["result_count"], 27)
        page = self.continue_search(response.context["page_query"], 2)
        rows = response.context["rows"] + page.context["rows"]
        self.assertEqual(len({r["offering_id"] for r in rows}), 27)
        ajax = self.client.get(self.url + "?" + response.context["page_query"] + "&page=2&results=1")
        self.assertEqual(ajax.status_code, 200)
        self.assertIn("page=2", ajax.json()["url"])
        workbook = self._workbook(self.client.get(self.url + "?" + response.context["export_query"]))
        self.assertEqual(workbook.active.max_row, 109)

    def test_empty_export_preserves_selected_range_header(self):
        StudentPeriodGrade.objects.filter(offering=self.offering).delete()
        response = self.search(grade_min="98.5")
        self.assertEqual(response.context["result_count"], 0)
        workbook = self._workbook(self.client.get(self.url + "?" + response.context["export_query"]))
        self.assertEqual(workbook.active.cell(1, 6).value, "Grades 98.5–100")

    def test_overlapping_cohorts_statuses_and_live_edits_never_release(self):
        other = self._create_extra_offering("OVERLAP", "Overlap", "OVERLAP")
        for record in StudentPeriodGrade.objects.filter(offering=self.offering):
            fixtures.Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year, term=self.term, student=record.student, course_offering=other)
            StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=other, template_period=self.period, student=record.student, period_grade=record.period_grade)
        for status in ("all", "finalized", "unfinalized"):
            for lower in ("97", "97.51", "0"):
                response = self.search(status=status, grade_min=lower)
                self.assertEqual(response.context["result_count"], 2)
                for row in response.context["rows"]:
                    self.assert_withheld(row)
        StudentPeriodGrade.objects.filter(offering=other).update(period_grade=100, is_finalized=True)
        for row in self.search().context["rows"]:
            self.assert_withheld(row)

    def test_seven_minus_six_attack_across_html_ajax_modal_and_excel(self):
        other = self._create_extra_offering("DIFFERENCE", "Differencing probe", "DIFF")
        self._seed_grades(other, [98] * 6 + [Decimal("97.50")] + [80] * 13)
        orders = []
        for minimum in ("97", "97.51", "97"):
            response = self.search(grade_min=minimum)
            orders.append([row["offering_id"] for row in response.context["rows"]])
            row = next(r for r in response.context["rows"] if r["offering_id"] == other.id)
            self.assert_withheld(row)
            data = self.detail(response, row).json()
            self.assert_withheld(data)
            self.assertNotIn("97.50", json.dumps(data))
            self.assertNotContains(response, "97.50")
            ajax = self.client.post(self.url, self.params(results="1", grade_min=minimum))
            self.assertNotIn("97.50", ajax.json()["html"])
            workbook = self._workbook(self.client.get(self.url + "?" + response.context["export_query"]))
            for record in list(workbook.active.values)[1:]:
                self.assertEqual(record[5:8], (None, None, None))
                self.assertEqual(record[8], "Withheld")
        self.assertEqual(orders[0], orders[1])
        self.assertEqual(orders[1], orders[2])

    def test_shell_and_options_cost_do_not_grow_with_null_campus_courses(self):
        counts = []
        for size in (1, 27):
            if size == 27:
                for i in range(26):
                    other = self._create_extra_offering(f"NULLUX{i}", "No course campus", f"N{i}")
                    fixtures.Course.objects.filter(pk=other.course_id).update(campus=None)
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url)
            with CaptureQueriesContext(connection) as option_queries:
                options = self.client.get(self.url, {"options": "1"})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("courses", options.json())
            counts.append((len(queries), len(option_queries)))
        self.assertEqual(counts[0], counts[1])
        print("GRADE_UX_SHELL_QUERIES=" + json.dumps(counts))
