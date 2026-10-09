"""Saved-evidence matrix, pagination and actual print route regressions."""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.rbac.models import Role, UserRole
from .dtr import calculate_hour_totals, save_adjustment
from .dtr_printing import build_dtr_matrix
from .models import FacultyDTR
from . import tests_dtr_selection as fixtures


def saved_class(day, *, offering=1, section="SECTION-A", time="10:30-12:00", hours="1.50"):
    return {"kind": "CLASS", "date": day, "time": time, "label": f"COURSE / {section}",
        "teaching": hours, "exact_teaching": hours, "status": "Present (assigned class; no exception)",
        "a": "0.00", "n": "0.00", "late": "0.00", "early": "0.00", "other": "0.00", "leave": "0.00",
        "late_minutes": 0, "early_minutes": 0, "meeting_id": offering * 100 + date.fromisoformat(day).day,
        "sections": [{"offering_id": offering, "course_code": "COURSE", "section_code": section}]}


def saved_record(lines, *, start="2026-10-05", end="2026-10-09", teaching="33.00", admin="0.00"):
    return {"start_date": start, "end_date": end, "faculty_name": "Saved Faculty", "lines": lines,
        **calculate_hour_totals(teaching=Decimal(teaching), admin=Decimal(admin))}


def matrix_rows(matrix):
    return [row for page in matrix["pages"] for group in page.get("groups", []) for row in group["rows"]]


class DTRMatrixTests(SimpleTestCase):
    def test_33_hour_cutoff_pivots_every_actual_date_without_mutating_saved_values(self):
        lines = []
        for day in (5, 7):
            for offering, (time, hours) in enumerate((("10:30-12:00", "1.50"), ("13:00-14:30", "1.50"),
                    ("14:30-16:00", "1.50"), ("16:00-17:30", "1.50"), ("18:00-21:00", "3.00")), 1):
                lines.append(saved_class(f"2026-10-{day:02}", offering=offering,
                    section=f"SECTION-{offering}", time=time, hours=hours))
        for day in (6, 8):
            for offering in range(6, 10):
                lines.append(saved_class(f"2026-10-{day:02}", offering=offering,
                    section=f"SECTION-{offering}", time=f"{offering+7}:00-{offering+8}:30"))
        lines.append(saved_class("2026-10-09", offering=10, time="08:00-11:00", hours="3.00"))
        saved = saved_record(lines)
        before = deepcopy(saved)
        matrix = build_dtr_matrix(saved)
        self.assertEqual(len(matrix["pages"]), 1)
        self.assertEqual(matrix["class_rows"], 10)
        page = matrix["pages"][0]
        self.assertEqual([group["label"] for group in page["groups"]], ["MW", "TTH", "FRI"])
        self.assertEqual([(d["day"], d["weekday"]) for d in page["dates"]],
            [(5, "Mo"), (6, "Tu"), (7, "We"), (8, "Th"), (9, "Fr")])
        self.assertEqual(page["teaching_total"], "33.00")
        self.assertEqual(page["groups"][0]["rows"][0]["cells"][1]["hours"], "")
        self.assertEqual(matrix["total_units"], "Not saved")
        self.assertEqual(saved, before)

    def test_same_course_different_sections_or_schedule_versions_remain_separate(self):
        a = saved_class("2026-10-05", offering=1)
        b = saved_class("2026-10-05", offering=2, section="SECTION-B")
        c = saved_class("2026-10-07", offering=1)
        entries = [{"meeting_id": row["meeting_id"], "meeting_snapshot": {
            "schedule": {"schedule_version_id": version}}} for row, version in ((a, 1), (b, 1), (c, 2))]
        matrix = build_dtr_matrix(saved_record([a, b, c], teaching="4.50"), published_entries=entries)
        self.assertEqual(matrix["class_rows"], 3)
        self.assertEqual(matrix["pages"][0]["teaching_total"], "4.50")

    def test_combined_sections_and_saved_metadata_count_once(self):
        a = saved_class("2026-10-05")
        a["sections"] = [{"offering_id": 1, "course_code": "COURSE", "section_code": "A", "student_count": 10, "units": "3"},
            {"offering_id": 2, "course_code": "COURSE", "section_code": "B", "student_count": 11, "units": "3"}]
        b = {**deepcopy(a), "date": "2026-10-07", "meeting_id": 107}
        matrix = build_dtr_matrix(saved_record([a, b], teaching="3.00"))
        row = matrix_rows(matrix)[0]
        self.assertEqual((matrix["class_rows"], row["size"], row["units"], row["total"]), (1, "21", "3.00", "3.00"))
        self.assertEqual(matrix["total_units"], "3.00")
        self.assertEqual(row["sections"], "A, B")

    def test_multiple_admin_entries_per_date_are_aggregated_without_zero_fill(self):
        lines = [{"kind": "ADMIN", "date": day, "admin": hours, "exact_admin": hours}
            for day, hours in (("2026-10-05", "1.25"), ("2026-10-05", "0.75"),
                ("2026-10-07", "1.50"), ("2026-10-09", "2.40"))]
        saved = saved_record(lines, teaching="0", admin="5.90")
        before = deepcopy(saved)
        matrix = build_dtr_matrix(saved)
        self.assertEqual(matrix["pages"][0]["admin_cells"], ["2.00", "-", "1.50", "-", "2.40"])
        self.assertEqual(matrix["pages"][0]["admin_total"], "5.90")
        self.assertEqual(saved, before)

    def test_exception_minutes_do_not_reduce_matrix_hours_or_recalculate_payable_total(self):
        line = saved_class("2026-10-05", hours="1.00")
        line.update(status="Exception", late_minutes=1, early_minutes=1, late="0.02", early="0.02")
        saved = saved_record([line], teaching="1.00")
        saved.update(calculate_hour_totals(teaching=Decimal("1"), late=Decimal(1)/60, early=Decimal(1)/60))
        before = deepcopy(saved)
        matrix = build_dtr_matrix(saved)
        cell = matrix_rows(matrix)[0]["cells"][0]
        self.assertEqual((cell["hours"], cell["labels"]), ("1.00", ["L 1m", "E 1m"]))
        self.assertEqual(saved["gross_deductions"], "0.03")
        self.assertEqual(saved["net_payable_hours"], "0.97")
        self.assertEqual(saved, before)

    def test_legacy_absence_and_paid_closure_keep_saved_basis_and_labels(self):
        line = saved_class("2026-10-05")
        line.update(a="0.25", status="Exception")
        closed = saved_class("2026-10-07", hours="0.00")
        closed.update(status="Holiday closure; Part-time", closure_revision=1)
        matrix = build_dtr_matrix(saved_record([line, closed], teaching="1.50"))
        cells = matrix_rows(matrix)[0]["cells"]
        self.assertEqual(cells[0]["labels"], ["A 0.25h"])
        self.assertEqual((cells[2]["hours"], cells[2]["labels"]), ("0.00", ["Closure"]))

    def test_cross_month_leap_dates_and_long_cutoffs_paginate_with_complete_headers(self):
        saved = saved_record([], start="2028-02-27", end="2028-04-02", teaching="0")
        matrix = build_dtr_matrix(saved)
        dates = [day["date"] for page in matrix["pages"] for day in page["dates"]]
        expected = [date(2028, 2, 27) + timedelta(days=i) for i in range(36)]
        self.assertEqual(dates, expected)
        self.assertIn(date(2028, 2, 29), dates)
        self.assertEqual(len(matrix["pages"]), 3)
        self.assertTrue(all(len(page["dates"]) <= 16 for page in matrix["pages"]))

    def test_large_workloads_repeat_date_headers_but_admin_hours_only_once_per_band(self):
        lines = [saved_class("2026-10-05", offering=i, section=f"SECTION-{i}") for i in range(1, 31)]
        lines.append({"kind": "ADMIN", "date": "2026-10-05", "admin": "2.40"})
        matrix = build_dtr_matrix(saved_record(lines, teaching="45.00", admin="2.40"))
        self.assertEqual(len(matrix["pages"]), 3)
        self.assertEqual(sum(page["show_admin"] for page in matrix["pages"]), 1)
        self.assertEqual(sum(Decimal(page["teaching_total"]) for page in matrix["pages"]), Decimal("45"))
        self.assertTrue(all(len(page["dates"]) == 5 for page in matrix["pages"]))

    def test_wide_saved_hour_values_use_more_date_bands_instead_of_smaller_text(self):
        saved = saved_record([saved_class("2026-10-05", hours="10.25")],
            start="2026-10-01", end="2026-10-16", teaching="10.25")
        matrix = build_dtr_matrix(saved)
        self.assertEqual([len(page["dates"]) for page in matrix["pages"]], [12, 4])
        self.assertEqual(matrix["pages"][0]["teaching_total"], "10.25")


class DTRMatrixPrintRouteTests(TestCase):
    permission_codes = fixtures.DTRSelectionTests.permission_codes
    setUpTestData = fixtures.DTRSelectionTests.__dict__["setUpTestData"]
    setUp = fixtures.DTRSelectionTests.setUp
    aware = fixtures.DTRSelectionTests.aware
    coverage = fixtures.DTRSelectionTests.coverage
    scope = fixtures.DTRSelectionTests.scope
    review = fixtures.DTRSelectionTests.review
    slice = fixtures.DTRSelectionTests.slice
    pair = fixtures.DTRSelectionTests.pair
    confirm = fixtures.DTRSelectionTests.confirm
    publish = fixtures.DTRSelectionTests.publish
    final = fixtures.DTRSelectionTests.final

    def test_print_uses_selected_final_and_immutable_publication_after_live_academic_changes(self):
        self.pair()
        publication = self.publish()
        final = self.final(publication)
        original = deepcopy(final.snapshot)
        type(self.course).objects.filter(pk=self.course.pk).update(code="CURRENT-ONLY", units="99")
        type(self.section).objects.filter(pk=self.section.pk).update(code="CURRENT-SECTION")
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "CURRENT-ONLY")
        self.assertNotContains(response, "CURRENT-SECTION")
        self.assertEqual(response.context["matrix"]["pages"][0]["teaching_total"], "1.00")
        self.assertNotContains(response, "Present (assigned class; no exception)")
        self.assertContains(response, "Office Hours - Admin")
        final.refresh_from_db()
        self.assertEqual(final.snapshot, original)

    def test_final_admin_matrix_uses_saved_multiple_dates_and_entries_after_new_correction(self):
        role = Role.objects.create(code="AC", name="Synthetic AC")
        UserRole.objects.create(user=self.faculty, role=role, tenant=self.tenant,
            campus=self.campus, department=self.department)
        UserRole.objects.create(user=self.faculty, role=role, tenant=self.tenant,
            campus=self.campus, department=self.other_department)
        UserRole.objects.create(user=self.actor, role=self.role, tenant=self.tenant,
            campus=self.campus, department=self.other_department)
        scope = {**self.scope(), "end_date": date(2026, 1, 9)}
        self.coverage()
        with patch.object(self, "scope", return_value=scope):
            publication = self.publish()
        entries = []
        for index, (day, hours) in enumerate(((5, "1.25"), (5, "0.75"), (7, "1.50"), (9, "2.40"))):
            entries.append(save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
                department=self.other_department if index == 1 else self.department,
                entry_date=date(2026, 1, day), kind="ADMIN", hours=hours, reason=""))
        final = self.final(publication)
        original = deepcopy(final.snapshot)
        save_adjustment(actor=self.actor, publication=publication, faculty=self.faculty,
            department=self.department, entry_date=entries[0].entry_date, kind="ADMIN", hours="9.00",
            reason="Later correction", previous=entries[0], expected_revision=entries[0].revision)
        self.client.force_login(self.actor)
        response = self.client.get(reverse("faculty_attendance:dtr_print", args=[final.public_id]))
        self.assertEqual(response.status_code, 200)
        page = response.context["matrix"]["pages"][0]
        self.assertEqual(page["admin_cells"], ["2.00", "-", "1.50", "-", "2.40"])
        self.assertEqual(page["admin_total"], "5.90")
        self.assertNotContains(response, "Later correction")
        final.refresh_from_db()
        self.assertEqual(final.snapshot, original)
