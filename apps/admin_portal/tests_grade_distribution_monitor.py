from datetime import date
from decimal import Decimal
import json

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from unittest.mock import patch
from urllib.parse import parse_qs

from apps.admin_portal.grade_distribution import GradeDistributionMonitorService
from apps.admin_portal.grade_distribution_metrics import FormulaPrivacyPlan, PrivacyFamily, distribution
from apps.admin_portal.grade_distribution_selection import logical_period
from apps.grading.services import FacultyGradingService
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.academics.models import AcademicYear, Course, CourseOffering, FacultyAssignment, Section, Term
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeActivity,
    GradingTemplate,
    GradingTemplateComponent,
    GradingTemplatePeriod,
    StudentActivityScore,
    StudentPeriodGrade,
    TenantGradingProfile,
    GradingTemplateSubcomponent,
    GradingTemplateDetail,
)
from apps.rbac.models import Permission, Role, RolePermission, UserRole, UserPermission
from apps.students.models import Student
from apps.tenants.models import Campus, Department, Program, Tenant


class GradeDistributionMonitorTests(TestCase):
    def setUp(self):
        fixture_clock = patch("apps.admin_portal.grade_distribution.timezone.localdate", return_value=date(2025, 7, 1))
        fixture_clock.start()
        self.addCleanup(fixture_clock.stop)
        self.tenant = Tenant.objects.create(code="NCBA", name="NCBA")
        self.campus = Campus.objects.create(tenant=self.tenant, code="FV", name="Fairview")
        self.department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="BSA",
            name="Accountancy",
        )
        self.program = Program.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="BSA",
            name="BS Accountancy",
        )
        self.academic_year = AcademicYear.objects.create(
            tenant=self.tenant,
            code="2025-2026",
            name="AY 2025-2026",
            start_date=date(2025, 6, 1),
            end_date=date(2026, 5, 31),
        )
        self.term = Term.objects.create(
            tenant=self.tenant,
            academic_year=self.academic_year,
            code="1ST",
            name="First Semester",
            sequence_no=1,
            start_date=date(2025, 6, 1),
            end_date=date(2025, 10, 31),
        )
        self.template = GradingTemplate.objects.create(
            tenant=self.tenant,
            code="GENED",
            name="General Education",
            is_published=True,
            is_active=True,
            passing_grade_threshold=Decimal("75"),
        )
        self.period = GradingTemplatePeriod.objects.create(
            template=self.template,
            code="PRELIM",
            name="Prelim",
            sequence_no=1,
        )

        self.course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code="ACC101",
            title="Accounting 101",
        )
        self.section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code="BSA1A",
            name="BSA 1A",
        )
        self.offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=self.course,
            section=self.section,
        )

        self.admin_user = self._user("dean", "Dean", "User", self.tenant, self.campus, self.department)
        self.faculty_user = self._user("faculty", "Faculty", "One", self.tenant, self.campus, self.department)
        self._assign_permissions(self.admin_user, include_monitor=True)
        self._assign_faculty_role(self.faculty_user)
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=self.offering,
            faculty_user=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )

        self._seed_grades(self.offering, [95, 94, 93, 92, 91, 90, 96, 97, 80, 76])
        self.url = reverse("admin_portal:grade_distribution_monitor")

    def search(self, **kwargs):
        return self.client.get(self.url, {"apply": "1", "academic_year_id": self.academic_year.id, "term_id": self.term.id, "period": "PRELIM", **kwargs})

    def detail(self, response, row=None):
        row = row or response.context["rows"][0]
        url = reverse("admin_portal:grade_distribution_monitor_detail", args=[row["offering_id"], row["physical_period_id"]])
        return self.client.get(url + "?" + row["detail_query"])

    def test_monitor_requires_specific_permission(self):
        user = self._user("no_monitor", "No", "Monitor", self.tenant, self.campus, self.department)
        self._assign_permissions(user, include_monitor=False)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin_portal:grade_distribution_monitor_detail", args=[self.offering.id, self.period.id])).status_code, 403)
        self.assertEqual(self.client.get(self.url, {"export": "csv"}).status_code, 403)

    def test_initial_load_and_unsubmitted_filters_do_not_query_grade_results(self):
        self.client.force_login(self.admin_user)
        for params in ({}, {"academic_year_id": self.academic_year.id, "term_id": self.term.id, "period": "PRELIM"}, {"options": "1"}):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url, params)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(any('student_period_grades' in q["sql"] or 'student_activity_scores' in q["sql"] or 'grade_activities' in q["sql"] for q in queries))
        self.assertContains(self.client.get(self.url), "Select criteria and click Apply Filters")

    def test_required_and_malformed_selections_fail_before_grade_retrieval(self):
        self.client.force_login(self.admin_user)
        for params in ({"apply": "1"}, {"apply": "1", "term_id": "bad"}, {"apply": "1", "academic_year_id": "999999", "term_id": self.term.id, "period": "FINAL"}):
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(self.url, params)
            self.assertTrue(response.context["errors"])
            self.assertEqual(response.context["rows"], [])
            self.assertFalse(any('student_period_grades' in q["sql"] for q in queries))

    def test_term_must_belong_to_year(self):
        other_year = AcademicYear.objects.create(tenant=self.tenant, code="OLD", name="Old", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))
        self.client.force_login(self.admin_user)
        self.assertTrue(self.search(academic_year_id=other_year.id).context["errors"])

    def test_summary_is_aggregate_and_average_uses_saved_values(self):
        self.client.force_login(self.admin_user)
        response = self.search()
        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row["average"], Decimal("90.40"))
        self.assertEqual(row["graded_count"], 10)
        self.assertEqual(row["status"], "Unfinalized")
        self.assertEqual(sum(b["count"] for b in row["distribution"]), 10)
        self.assertContains(response, "Class Ave.")
        self.assertNotContains(response, "Review Flags")
        self.assertNotContains(response, "Masked Student")
        self.assertNotIn("grade_details", row)

    def test_all_four_logical_periods_and_exact_options(self):
        students = list(StudentPeriodGrade.objects.filter(offering=self.offering).values_list("student_id", flat=True))
        for key, name, seq in (("MIDTERM", "Midterm", 2), ("PRE-FINAL", "Pre-Final", 3), ("FINAL_EXAM", "Final Exam", 4)):
            p = GradingTemplatePeriod.objects.create(template=self.template, code=key, name=name, sequence_no=seq)
            StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=p, student_id=students[0], period_grade=Decimal("100"))
        self.client.force_login(self.admin_user)
        options = self.client.get(self.url).context["filter_options"]["periods"]
        self.assertEqual([p["label"] for p in options], ["Prelim", "Midterm", "Pre-Final", "Final"])
        for key in ("PRELIM", "MIDTERM", "PREFINAL", "FINAL"):
            self.assertEqual(len(self.search(period=key).context["rows"]), 1)

    def test_ambiguous_period_not_guessed_and_distinct_physical_records_not_merged(self):
        unknown = GradingTemplatePeriod.objects.create(template=self.template, code="LEGACY1", name="Period 1", sequence_no=1)
        conflict = GradingTemplatePeriod.objects.create(template=self.template, code="MIDTERM", name="Final", sequence_no=2)
        self.assertIsNone(logical_period(unknown))
        self.assertIsNone(logical_period(conflict))
        other_template = GradingTemplate.objects.create(tenant=self.tenant, code="OLD", name="Historical")
        other_period = GradingTemplatePeriod.objects.create(template=other_template, code="PRELIM", name="Prelim", sequence_no=1, is_active=False)
        student_id = StudentPeriodGrade.objects.first().student_id
        for p in (unknown, conflict, other_period):
            StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=p, student_id=student_id, period_grade=90)
        self.client.force_login(self.admin_user)
        response = self.search()
        self.assertEqual(len(response.context["rows"]), 2)
        self.assertContains(response, "2 saved physical period mapping(s) are unresolved")
        self.assertNotContains(response, "LEGACY1")

    def test_statuses_zero_and_missing_are_separate(self):
        grades = list(StudentPeriodGrade.objects.filter(offering=self.offering).order_by("id"))
        grades[0].period_grade = 0; grades[0].is_finalized = True; grades[0].save()
        grades[1].period_grade = None; grades[1].save()
        self.client.force_login(self.admin_user)
        row = self.search().context["rows"][0]
        self.assertEqual(row["graded_count"], 9)
        self.assertIsNone(row["minimum"])
        self.assertTrue(row["privacy_suppressed"])
        self.assertEqual(row["missing_count"], 1)
        self.assertEqual(row["status"], "Mixed")
        self.assertIsNone(row["average"])
        self.assertEqual(row["privacy_reason"], "complementary_population")
        final = self.search(status="finalized").context["rows"][0]
        self.assertEqual(final["graded_count"], 1)
        self.assertIsNone(final["average"])
        self.assertTrue(final["privacy_suppressed"])
        self.assertEqual(final["status_excluded_count"], 8)
        self.assertEqual(final["missing_count"], 1)
        self.assertEqual(self.search(status="unfinalized").context["rows"][0]["graded_count"], 8)

    def test_numeric_threshold_boundaries_and_perfect_concentration(self):
        values = list(map(Decimal, ("100", "90", "89.99", "75", "74.99", "70", "69.99", "60", "59.99", "0")))
        bands = distribution(values, Decimal("75"))
        self.assertEqual([b["count"] for b in bands], [2, 2, 2, 2, 2])
        self.assertEqual([b["range"] for b in bands], ["90 and above", "75 to below 90", "70 to below 75", "60 to below 70", "below 60"])
        StudentPeriodGrade.objects.filter(offering=self.offering).update(period_grade=100)
        self.client.force_login(self.admin_user)
        row = self.search().context["rows"][0]
        self.assertEqual(row["exact_100_count"], 10)
        self.assertEqual(row["exact_100_percentage"], 100)
        self.assertTrue(row["all_100"])

    def test_profile_template_tenant_and_system_threshold_precedence(self):
        self.client.force_login(self.admin_user)
        SystemSettingService.set("PASSING_GRADE_THRESHOLD", "80", tenant_id=self.tenant.id)
        self.assertEqual(self.search().context["rows"][0]["passing_threshold"], 75)
        profile = TenantGradingProfile.objects.create(tenant=self.tenant, campus=self.campus, profile_code="CUSTOM", profile_name="Custom", grading_template=self.template, passing_grade_threshold=82)
        row = self.search().context["rows"][0]
        self.assertEqual(row["passing_threshold"], 82)
        self.assertIn("profile", row["threshold_source"])
        profile.is_active = False; profile.save()
        self.template.is_published = False; self.template.save()
        self.assertEqual(self.search().context["rows"][0]["passing_threshold"], 80)
        SystemSettingService.set("PASSING_GRADE_THRESHOLD", "invalid", tenant_id=self.tenant.id)
        self.assertEqual(self.search().context["rows"][0]["passing_threshold"], 75)

    def test_faculty_labels_sorted_duplicate_and_missing_names(self):
        self.faculty_user.first_name = "Ana"; self.faculty_user.last_name = "Zulu"; self.faculty_user.save()
        other = self._user("duplicate", "Ana", "Zulu", self.tenant, self.campus, self.department)
        self._assign_faculty_role(other)
        FacultyAssignment.objects.create(offering=self.offering, faculty_user=other, response_status="ACCEPTED")
        blank = self._user("blank@example.com", "", "", self.tenant, self.campus, self.department)
        self._assign_faculty_role(blank)
        FacultyAssignment.objects.create(offering=self.offering, faculty_user=blank, response_status="ACCEPTED")
        self.client.force_login(self.admin_user)
        faculty = self.client.get(self.url).context["filter_options"]["faculty"]
        self.assertEqual(faculty[0]["label"], f"Faculty #{blank.id}")
        self.assertEqual(faculty[1]["label"], f"Zulu, Ana (#{self.faculty_user.id})")
        self.assertNotIn("@", str(faculty))

    def test_privacy_html_json_csv_have_no_student_identities(self):
        self.client.force_login(self.admin_user)
        response = self.search()
        detail = self.detail(response)
        export = self.client.get(self.url + "?" + response.context["export_query"])
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(export.status_code, 200)
        for content in (response.content.decode(), detail.content.decode(), export.content.decode()):
            for forbidden in ("ACC101-001", "Student1", "student_id", "student_no", "masked_name", "masked_student", "@example.com"):
                self.assertNotIn(forbidden, content)
        payload = detail.json()
        self.assertEqual(sum(f["count"] for f in payload["frequency"]), payload["graded_count"])
        self.assertContains(export, "Class Ave.")

    def _privacy_assert_payload(self, payload):
        self.assertTrue(payload["privacy_suppressed"])
        for key in ("average", "minimum", "maximum", "exact_100_count", "exact_100_percentage", "all_100"):
            self.assertIsNone(payload[key], key)
        self.assertEqual(payload["frequency"], [])
        self.assertTrue(all(b["count"] is None and b["percentage"] is None for b in payload["distribution"]))
        self.assertNotIn("student_id", payload)

    def _privacy_csv_row(self, response):
        import csv
        import io
        return next(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_privacy_zero_one_to_four_five_and_six_html_json_csv_parity(self):
        self.client.force_login(self.admin_user)
        grades = list(StudentPeriodGrade.objects.filter(offering=self.offering).order_by("id"))
        for count in range(7):
            with self.subTest(count=count):
                StudentPeriodGrade.objects.filter(offering=self.offering).update(period_grade=None)
                StudentPeriodGrade.objects.filter(pk__in=[g.pk for g in grades[:count]]).update(period_grade=Decimal("83.27"))
                response = self.search()
                row = response.context["rows"][0]
                modal = self.detail(response).json()
                csv_response = self.client.get(self.url + "?" + response.context["export_query"])
                csv_row = self._privacy_csv_row(csv_response)
                self.assertEqual((row["graded_count"], row["missing_count"]), (count, 10 - count))
                self.assertEqual(row["analytics_state"], modal["analytics_state"])
                self.assertEqual(row["analytics_state"], csv_row["Analytics Availability"])
                if 0 < count < 5:
                    self._privacy_assert_payload(row)
                    self._privacy_assert_payload(modal)
                    self.assertContains(response, "fewer than five students")
                    self.assertNotContains(response, "83.27")
                    self.assertNotContains(csv_response, "83.27")
                    self.assertNotIn("83.27", str(modal))
                    self.assertNotContains(response, 'class="gdm-distribution"')
                    for column in ("Class Ave.", "Minimum", "Maximum", "Exactly 100 Count", "Exactly 100 %", "All Graded Values Exactly 100"):
                        self.assertEqual(csv_row[column], "")
                    self.assertIn("Withheld", csv_row["Threshold-Aware Distribution"])
                elif count == 0:
                    self.assertEqual(row["analytics_state"], "unavailable")
                    self.assertFalse(row["privacy_suppressed"])
                    self.assertIsNone(row["average"])
                else:
                    self.assertEqual(row["analytics_state"], "disclosed")
                    self.assertEqual(row["average"], Decimal("83.27"))
                    self.assertEqual(Decimal(modal["average"]), Decimal("83.27"))
                    self.assertEqual(csv_row["Class Ave."], "83.27")
        StudentPeriodGrade.objects.filter(offering=self.offering).update(period_grade=None)
        StudentPeriodGrade.objects.filter(pk__in=[g.pk for g in grades[:5]]).update(period_grade=0)
        self.assertEqual(self.search().context["rows"][0]["average"], 0)

    def test_privacy_complementary_status_suppression_is_identical_across_endpoints(self):
        self.client.force_login(self.admin_user)
        ids = list(StudentPeriodGrade.objects.filter(offering=self.offering).order_by("id").values_list("id", flat=True))
        StudentPeriodGrade.objects.filter(offering=self.offering).update(period_grade=Decimal("87.43"), is_finalized=True)
        StudentPeriodGrade.objects.filter(pk=ids[-1]).update(is_finalized=False)
        for status in ("all", "finalized", "unfinalized"):
            response = self.search(status=status)
            row = response.context["rows"][0]
            self._privacy_assert_payload(row)
            self._privacy_assert_payload(self.detail(response).json())
            self.assertEqual(row["missing_count"], 0)
            for output in (response.content, self.client.get(self.url + "?" + response.context["export_query"]).content):
                self.assertNotIn(b"87.43", output)
        StudentPeriodGrade.objects.filter(pk__in=ids[5:]).update(is_finalized=False)
        for status, count in (("all", 10), ("finalized", 5), ("unfinalized", 5)):
            row = self.search(status=status).context["rows"][0]
            self.assertEqual((row["analytics_state"], row["graded_count"]), ("disclosed", count))

    def test_privacy_saved_measures_and_component_distinct_populations(self):
        self.client.force_login(self.admin_user)
        ids = list(StudentPeriodGrade.objects.filter(offering=self.offering).order_by("id").values_list("student_id", flat=True))
        StudentPeriodGrade.objects.filter(student_id__in=ids[:3], offering=self.offering).update(exam_grade=Decimal("86.19"))
        StudentPeriodGrade.objects.filter(student_id__in=ids[:5], offering=self.offering).update(class_standing_grade=Decimal("78.31"))
        component = GradingTemplateComponent.objects.create(template_period=self.period, code="PRIV-EX", name="Private Exam", weight_percentage=100, is_exam_component=True, score_input_mode="DIRECT_PERCENTAGE")
        for i in range(2):
            activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
                template_period=self.period, template_component=component, title=f"Private source {i}", total_score=100, created_by_user=self.faculty_user)
            for sid in ids[:3]:
                StudentActivityScore.objects.create(activity=activity, student_id=sid, raw_score=Decimal("82.17"), computed_score=Decimal("82.17"))
        data = self.detail(self.search()).json()
        self._privacy_assert_payload(data)
        self._privacy_assert_payload(data["measures"][0])
        self._privacy_assert_payload(data["measures"][1])
        self.assertEqual(data["measures"][1]["graded_count"], 5)
        for measure in data["measures"][2:]:
            self.assertEqual(measure["graded_count"], 3)
            self._privacy_assert_payload(measure)
        self.assertNotIn("86.19", str(data))
        self.assertNotIn("82.17", str(data))

    def _formula_fixture(self, *, supplemental_count=1, exam=True, supplemental_weight=20, common_weight=40, other_weight=40):
        """Persist engine-derived totals in the disposable test DB, not invented formulas."""
        ids = list(StudentPeriodGrade.objects.filter(offering=self.offering).order_by("id").values_list("student_id", flat=True))
        components = []
        for code, weight, values in (("Common", common_weight, list(range(70, 80))),
                                     ("Supplemental", supplemental_weight, [43] * supplemental_count),
                                     ("Other", other_weight, [80] * 10)):
            component = GradingTemplateComponent.objects.create(template_period=self.period, code=code,
                name=code, weight_percentage=weight, is_exam_component=exam if code != "Other" else not exam,
                score_input_mode="DIRECT_PERCENTAGE")
            activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
                template_period=self.period, template_component=component, title=code, total_score=100,
                created_by_user=self.faculty_user)
            for sid, value in zip(ids, values):
                StudentActivityScore.objects.create(activity=activity, student_id=sid, raw_score=value, computed_score=value)
            components.append(component)
        self._persist_formula_totals(ids)
        self.client.force_login(self.admin_user)
        return ids, components

    def _persist_formula_totals(self, ids):
        for sid in ids:
            detail = FacultyGradingService.build_period_grade_detail_for_student(
                offering=self.offering, template_period=self.period, student_id=sid)
            StudentPeriodGrade.objects.filter(offering=self.offering, student_id=sid).update(
                period_grade=detail["period_grade"], exam_grade=detail["exam_grade"],
                class_standing_grade=detail["class_standing"])

    def test_formula_privacy_independent_public_payload_subtraction_attack(self):
        self._formula_fixture()
        self.assertEqual(sum(StudentPeriodGrade.objects.filter(offering=self.offering).values_list("exam_grade", flat=True)), 788)
        response = self.search()
        data = self.detail(response).json()
        common = next(m for m in data["measures"] if m["label"] == "Examination - Common")
        total = data["measures"][0]
        # Attacker uses only the actual public JSON, including chart/table frequencies.
        attacks = []
        if total["average"] is not None and common["average"] is not None:
            attacks.append(Decimal(total["average"]) * total["graded_count"] - Decimal(common["average"]) * common["graded_count"])
        if total["frequency"] and common["frequency"]:
            public_sum = lambda m: sum(Decimal(f["grade"]) * f["count"] for f in m["frequency"])
            attacks.append(public_sum(total) - public_sum(common))
        self.assertNotIn(Decimal("43"), attacks, "745/43/788 recovered from released fields")
        self._privacy_assert_payload(total)
        # A useful common breakdown remains public, but no related total can
        # complete the subtraction. This tests the actual retained disclosures.
        self.assertEqual(common["analytics_state"], "disclosed")
        self.assertEqual(sum(Decimal(f["grade"]) * f["count"] for f in common["frequency"]), 745)
        self.assertEqual(Decimal(common["average"]) * common["graded_count"], 745)
        self._privacy_assert_payload(data)

    def test_formula_privacy_html_json_csv_chart_consistency_and_statuses(self):
        ids, _ = self._formula_fixture()
        StudentPeriodGrade.objects.filter(offering=self.offering, student_id__in=ids[:5]).update(is_finalized=True)
        for status in ("all", "finalized", "unfinalized"):
            response = self.search(status=status)
            row = response.context["rows"][0]
            modal = self.detail(response).json()
            export = self.client.get(self.url + "?" + response.context["export_query"])
            csv_row = self._privacy_csv_row(export)
            self._privacy_assert_payload(row)
            self._privacy_assert_payload(modal)
            self.assertEqual(csv_row["Analytics Availability"], row["analytics_state"])
            for column in ("Class Ave.", "Minimum", "Maximum", "Exactly 100 Count", "Exactly 100 %"):
                self.assertEqual(csv_row[column], "")
            self.assertNotContains(response, 'class="gdm-distribution"')
            for measure in modal["measures"]:
                if measure["analytics_state"] != "disclosed":
                    self.assertEqual(measure["frequency"], [])
                    self.assertIsNone(measure["average"])
                    self.assertTrue(all(b["count"] is None for b in measure["distribution"]))
                else:
                    self.assertGreaterEqual(measure["graded_count"], 5)
                    self.assertTrue(measure["frequency"])
            self.assertNotIn('"grade": "43', json.dumps(modal))
            self.assertEqual(row["missing_count"], 0)
            self.assertEqual(row["privacy_reason"], "formula_dependency")
        # A different physical class has no formula connection to this one.
        other = self._create_extra_offering("SAFE", "Unaffected", "SAFE-A")
        self._seed_grades(other, [90] * 10)
        safe = next(r for r in self.search().context["rows"] if r["offering_id"] == other.id)
        self.assertEqual((safe["analytics_state"], safe["average"]), ("disclosed", Decimal("90")))

    def test_formula_privacy_different_weights_period_and_bucket_equations(self):
        self._formula_fixture(common_weight=100, supplemental_weight=200, other_weight=100)
        rows = list(StudentPeriodGrade.objects.filter(offering=self.offering).values("period_grade", "exam_grade", "class_standing_grade"))
        self.assertEqual(sum(r["period_grade"] - r["exam_grade"] - r["class_standing_grade"] for r in rows), Decimal("43"))
        data = self.detail(self.search()).json()
        exam, standing = data["measures"][:2]
        attacks = []
        for field in ("average", "frequency"):
            def public_sum(m):
                if field == "average":
                    return Decimal(m[field]) * m["graded_count"] if m[field] is not None else None
                return sum(Decimal(f["grade"]) * f["count"] for f in m[field]) if m[field] else None
            totals = [public_sum(m) for m in (data, exam, standing)]
            if all(v is not None for v in totals):
                attacks.append(totals[0] - totals[1] - totals[2])
        # With weights 100/200/100, these three totals recover supplemental 43
        # even if BOTH individual exam components are withheld.
        self.assertNotIn(Decimal("43"), attacks)
        for measure in (data, exam):
            self._privacy_assert_payload(measure)
        self.assertEqual(Decimal(standing["average"]), 80)

    def test_formula_privacy_class_standing_activity_average_and_subtotal(self):
        ids, components = self._formula_fixture(supplemental_count=5, exam=False)
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
            template_period=self.period, template_component=components[0], title="Small quiz", total_score=100,
            created_by_user=self.faculty_user)
        StudentActivityScore.objects.create(activity=activity, student_id=ids[0], raw_score=43, computed_score=43)
        self._persist_formula_totals(ids)
        data = self.detail(self.search()).json()
        # The component has TEN students, but one activity has ONE. Standing
        # activities are internal sources, not displayed measures in the modal.
        common = next(m for m in data["measures"] if m["label"] == "Class standing - Common")
        self.assertEqual(common["graded_count"], 10)
        self._privacy_assert_payload(common)
        self._privacy_assert_payload(data["measures"][1])
        self._privacy_assert_payload(data)

    def test_formula_privacy_actual_percentage_configured_grade_differ(self):
        _, components = self._formula_fixture()
        activity = GradeActivity.objects.get(template_component=components[1])
        activity.total_score = 50
        activity.save()
        StudentActivityScore.objects.filter(activity=activity).update(raw_score=Decimal("21.5"), computed_score=Decimal("71.5"))
        self._persist_formula_totals(list(StudentPeriodGrade.objects.filter(offering=self.offering).values_list("student_id", flat=True)))
        data = self.detail(self.search()).json()
        for label in ("Supplemental - Actual examination percentage", "Supplemental - Saved configured activity grade"):
            self._privacy_assert_payload(next(m for m in data["measures"] if m["label"] == label))
        self._privacy_assert_payload(data["measures"][0])
        self._privacy_assert_payload(data)

    def test_formula_privacy_weighted_subcomponent_details_with_small_activity(self):
        ids, components = self._formula_fixture(supplemental_count=5, exam=False)
        sub = GradingTemplateSubcomponent.objects.create(template_component=components[0], code="DETAILS",
            name="Weighted details", weight_percentage=100)
        dense = GradingTemplateDetail.objects.create(template_subcomponent=sub, code="DENSE", name="Dense", weight_percentage=70)
        sparse = GradingTemplateDetail.objects.create(template_subcomponent=sub, code="SPARSE", name="Sparse", weight_percentage=30)
        common = GradeActivity.objects.get(template_component=components[0])
        common.template_subcomponent = sub
        common.template_detail = dense
        common.save()
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
            template_period=self.period, template_component=components[0], template_subcomponent=sub,
            template_detail=sparse, title="Private weighted detail", total_score=100, created_by_user=self.faculty_user)
        StudentActivityScore.objects.create(activity=activity, student_id=ids[0], raw_score=43, computed_score=43)
        self._persist_formula_totals(ids)
        response = self.search()
        data = self.detail(response).json()
        self._privacy_assert_payload(data)
        self._privacy_assert_payload(data["measures"][1])
        self.assertEqual(next(m for m in data["measures"] if m["label"] == "Class standing - Common")["graded_count"], 10)

    def test_formula_privacy_sparse_attendance_session_protects_dense_standing(self):
        from apps.attendance.models import AttendanceRecord, AttendanceSession
        ids, components = self._formula_fixture(supplemental_count=5, exam=False)
        sub = GradingTemplateSubcomponent.objects.create(template_component=components[0], code="ATT",
            name="Attendance", weight_percentage=100, is_attendance_component=True)
        for index, participants in enumerate((ids, ids[:1])):
            session = AttendanceSession.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
                template_period=self.period, session_date=date(2025, 7, index + 1), created_by_user=self.faculty_user)
            for sid in participants:
                AttendanceRecord.objects.create(session=session, student_id=sid, status_code="P")
        self._persist_formula_totals(ids)
        data = self.detail(self.search()).json()
        self._privacy_assert_payload(data)
        self._privacy_assert_payload(next(m for m in data["measures"] if m["label"] == "Class standing - Common"))

    def test_formula_privacy_five_boundary_retains_safe_detailed_analytics(self):
        ids, _ = self._formula_fixture(supplemental_count=5)
        for status in ("all", "unfinalized"):
            data = self.detail(self.search(status=status)).json()
            self.assertEqual(data["analytics_state"], "disclosed")
            self.assertTrue(all(m["analytics_state"] == "disclosed" for m in data["measures"]))
            supplemental = next(m for m in data["measures"] if m["label"] == "Examination - Supplemental")
            self.assertEqual((supplemental["graded_count"], Decimal(supplemental["average"])), (5, Decimal("43")))
        score = StudentActivityScore.objects.get(student_id=ids[4], activity__title="Supplemental")
        score.delete()
        self._persist_formula_totals(ids)
        data = self.detail(self.search()).json()
        self._privacy_assert_payload(data)
        self._privacy_assert_payload(data["measures"][0])

    def test_formula_privacy_zero_weight_independence_preserves_period_and_standing(self):
        self._formula_fixture(common_weight=0, supplemental_weight=0, other_weight=100)
        response = self.search()
        row = response.context["rows"][0]
        data = self.detail(response).json()
        self.assertEqual((row["analytics_state"], row["average"]), ("disclosed", Decimal("80")))
        self.assertEqual(Decimal(data["average"]), row["average"])
        self._privacy_assert_payload(data["measures"][0])
        self.assertEqual(Decimal(data["measures"][1]["average"]), Decimal("80"))
        self.assertEqual(self._privacy_csv_row(self.client.get(self.url + "?" + response.context["export_query"]))["Class Ave."], "80.00")

    def test_privacy_overlap_guard_does_not_change_summary_when_opening_modal(self):
        ids = list(StudentPeriodGrade.objects.filter(offering=self.offering).order_by("id").values_list("student_id", flat=True))
        StudentPeriodGrade.objects.filter(student_id__in=ids[:9], offering=self.offering).update(exam_grade=Decimal("84.29"))
        self.client.force_login(self.admin_user)
        for status in ("all", "unfinalized"):
            response = self.search(status=status)
            row = response.context["rows"][0]
            data = self.detail(response).json()
            self.assertEqual(row["average"], data["average"])
            self._privacy_assert_payload(row)
            self._privacy_assert_payload(data)
            self._privacy_assert_payload(data["measures"][0])
            self.assertEqual(data["measures"][0]["privacy_reason"], "complementary_population")
            self.assertNotIn("84.29", str(data))
        # Changed status without matching signed filters cannot obtain another population.
        params = parse_qs(response.context["page_query"])
        params["status"] = "finalized"
        self.assertEqual(self.client.get(reverse("admin_portal:grade_distribution_monitor_detail", args=[self.offering.id, self.period.id]), params).status_code, 400)

    def test_privacy_historical_transfer_and_multi_campus_authorization(self):
        campus, dept, target = self._second_campus(grant_admin=True)
        self.offering.status = "ARCHIVED"
        self.offering.save()
        ids = list(StudentPeriodGrade.objects.filter(offering=self.offering).order_by("id").values_list("student_id", flat=True))
        StudentPeriodGrade.objects.filter(offering=self.offering).update(period_grade=None)
        StudentPeriodGrade.objects.filter(offering=self.offering, student_id__in=ids[:4]).update(period_grade=Decimal("88.13"))
        Student.objects.filter(pk=ids[0]).update(campus=campus, department=dept, program=target.program)
        self.client.force_login(self.admin_user)
        response = self.search(campus_id="all")
        self.assertEqual(len(response.context["rows"]), 2)
        for row in response.context["rows"]:
            self._privacy_assert_payload(row)
            self._privacy_assert_payload(self.detail(response, row=row).json())
        self.assertEqual(response.context["rows"][0]["eligible_count"], 10)
        self.assertNotIn(b"88.13", self.client.get(self.url + "?" + response.context["export_query"]).content)
        UserPermission.objects.create(user=self.admin_user, permission=Permission.objects.get(code="grade_distribution_monitor.read"), tenant=self.tenant, campus=campus, grant_type="DENY")
        self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 400)
        self.assertEqual(len(self.search(campus_id="all").context["rows"]), 1)

    def test_detail_and_exports_require_valid_context(self):
        self.client.force_login(self.admin_user)
        response = self.search()
        self.assertEqual(self.client.get(self.url, {"export": "csv"}).status_code, 400)
        row = response.context["rows"][0]
        detail_url = reverse("admin_portal:grade_distribution_monitor_detail", args=[row["offering_id"], row["physical_period_id"]])
        self.assertEqual(self.client.get(detail_url, {"apply": 1, "academic_year_id": self.academic_year.id, "term_id": self.term.id, "period": "PRELIM"}).status_code, 400)
        query = parse_qs(row["detail_query"])
        query["period"] = "FINAL"
        self.assertEqual(self.client.get(detail_url, {k: v[0] if isinstance(v, list) else v for k, v in query.items()}).status_code, 400)
        query = parse_qs(row["detail_query"]); query["context"] = "tampered"
        self.assertEqual(self.client.get(detail_url, {k: v[0] if isinstance(v, list) else v for k, v in query.items()}).status_code, 400)

    def test_expired_context(self):
        self.client.force_login(self.admin_user)
        response = self.search()
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 4000):
            self.assertEqual(self.detail(response).status_code, 400)

    def test_direct_deny_and_revoked_supervision_block_detail_export(self):
        self.client.force_login(self.admin_user)
        response = self.search()
        deny = UserPermission.objects.create(user=self.admin_user, permission=Permission.objects.get(code="grade_distribution_monitor.read"), tenant=self.tenant, campus=self.campus, grant_type="DENY")
        self.assertEqual(self.detail(response).status_code, 403)
        self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 403)
        deny.delete()
        UserRole.objects.filter(user=self.faculty_user).update(is_active=False)
        self.assertEqual(self.detail(response).status_code, 400)
        self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 400)

    def test_historical_saved_values_retained_without_current_active_roster(self):
        self.offering.status = "ARCHIVED"; self.offering.is_active = False; self.offering.save()
        FacultyAssignment.objects.filter(offering=self.offering).update(is_active=False)
        Enrollment.objects.filter(course_offering=self.offering).update(is_active=False)
        Student.objects.filter(tenant=self.tenant).update(is_active=False)
        self.academic_year.is_active = False; self.academic_year.save()
        self.term.is_active = False; self.term.save()
        self.client.force_login(self.admin_user)
        response = self.search()
        row = response.context["rows"][0]
        self.assertEqual(row["graded_count"], 10)
        self.assertEqual(row["average"], Decimal("90.40"))
        self.assertTrue(row["historical"])
        payload = self.detail(response).json()
        self.assertEqual(len(payload["measures"]), 2)
        self.assertIsNone(payload["incomplete_source_count"])
        self.assertIn("Original roster", " ".join(payload["notes"]))

    def test_historical_transfer_preserves_summary_modal_csv_and_original_authority(self):
        self.offering.status = "ARCHIVED"
        self.offering.save()
        campus, dept, target = self._second_campus()
        student = StudentPeriodGrade.objects.filter(offering=self.offering, period_grade=95).get().student
        old_enrollment = list(Enrollment.objects.filter(student=student).values())
        self.client.force_login(self.admin_user)
        keys = ("eligible_count", "graded_count", "average", "distribution", "missing_count", "coverage_rate",
                "finalized_count", "unfinalized_count", "status_excluded_count", "physical_period_id")
        for finalized in (False, True):
            with self.subTest(finalized=finalized):
                Student.objects.filter(pk=student.pk).update(campus=self.campus, department=self.department, program=self.program)
                StudentPeriodGrade.objects.filter(offering=self.offering).update(is_finalized=finalized)
                saved_before = list(StudentPeriodGrade.objects.filter(offering=self.offering).values())
                captures = []
                for status in ("all", "finalized", "unfinalized"):
                    before = self.search(status=status)
                    row = before.context["rows"][0]
                    modal = self.detail(before).json()
                    modal.pop("detail_query")  # Signing timestamp is not an analytics value.
                    captures.append((status, {k: row[k] for k in keys}, modal,
                                     self.client.get(self.url + "?" + before.context["export_query"]).content))
                Student.objects.filter(pk=student.pk).update(campus=campus, department=dept, program=target.program)
                for status, summary, modal, csv in captures:
                    after = self.search(status=status)
                    self.assertEqual({k: after.context["rows"][0][k] for k in keys}, summary)
                    after_modal = self.detail(after).json()
                    after_modal.pop("detail_query")
                    self.assertEqual(after_modal, modal)
                    self.assertEqual(self.client.get(self.url + "?" + after.context["export_query"]).content, csv)
                row = self.search().context["rows"][0]
                self.assertEqual((row["eligible_count"], row["graded_count"], row["average"], row["coverage_rate"]),
                                 (10, 10, Decimal("90.40"), Decimal("100.00")))
                self.assertEqual([b["count"] for b in row["distribution"]], [8, 2, 0, 0, 0])
                self.assertEqual(list(Enrollment.objects.filter(student=student).values()), old_enrollment)
                self.assertEqual(list(StudentPeriodGrade.objects.filter(offering=self.offering).values()), saved_before)
        authorized = self.search()
        head = self._user("new_head", "New", "Head", self.tenant, campus, dept)
        self._assign_permissions(head, include_monitor=True)
        self.client.force_login(head)
        own = self.search()
        self.assertEqual([r["offering_id"] for r in own.context["rows"]], [target.id])
        previous_url = reverse("admin_portal:grade_distribution_monitor_detail", args=[self.offering.id, self.period.id])
        self.assertEqual(self.client.get(previous_url + "?" + own.context["page_query"]).status_code, 404)
        self.assertNotIn(b"ACC101", self.client.get(self.url + "?" + own.context["export_query"]).content)
        self.assertEqual(self.detail(authorized).status_code, 400)
        self.assertEqual(self.client.get(self.url + "?" + authorized.context["export_query"]).status_code, 400)
        self.assertTrue(self.search(campus_id=self.campus.id).context["errors"])
        self.client.force_login(self.admin_user)
        UserPermission.objects.create(user=self.admin_user, permission=Permission.objects.get(code="grade_distribution_monitor.read"),
                                      tenant=self.tenant, campus=self.campus, grant_type="DENY")
        self.assertEqual(self.detail(authorized).status_code, 403)
        self.assertEqual(self.client.get(self.url + "?" + authorized.context["export_query"]).status_code, 403)
        UserPermission.objects.filter(user=self.admin_user).delete()
        UserRole.objects.filter(user=self.faculty_user, campus=self.campus).update(is_active=False)
        self.assertEqual(self.detail(authorized).status_code, 400)
        self.assertEqual(self.client.get(self.url + "?" + authorized.context["export_query"]).status_code, 400)
        self.assertEqual(self.search().context["rows"], [])

    def test_historical_transfer_does_not_override_tenant_or_retained_enrollment_evidence(self):
        self.offering.status = "ARCHIVED"
        self.offering.save()
        student = StudentPeriodGrade.objects.filter(offering=self.offering, period_grade=95).get().student
        foreign = Tenant.objects.create(code="FOREIGN-HISTORY", name="Foreign history")
        Student.objects.filter(pk=student.pk).update(tenant=foreign)
        self.client.force_login(self.admin_user)
        row = self.search().context["rows"][0]
        self.assertEqual((row["eligible_count"], row["graded_count"], row["average"]), (9, 9, Decimal("89.89")))
        Student.objects.filter(pk=student.pk).update(tenant=self.tenant)
        Enrollment.objects.filter(student=student).delete()
        row = self.search().context["rows"][0]
        self.assertEqual((row["eligible_count"], row["graded_count"]), (9, 9))

    def test_scope_wide_class_limit_rejects_oversized_later_page_without_partial_results(self):
        for i in range(26):
            later = self._create_extra_offering(f"GLOBAL{i}", "Global limit", f"GL{i}")
            self._seed_grades(later, [90])
        self.client.force_login(self.admin_user)
        with patch.object(GradeDistributionMonitorService, "MAX_CLASS_STUDENTS", 10):
            valid = self.search()
            self.assertEqual((len(valid.context["rows"]), valid.context["result_count"]), (25, 27))
            for index in range(2, 12):
                student = Student.objects.create(tenant=self.tenant, campus=self.campus, department=self.department,
                    program=self.program, student_no=f"GLOBAL-LAST-{index}", first_name="Limit", last_name="Student")
                Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
                    term=self.term, student=student, course_offering=later)
            requests = ({"apply": "1", "academic_year_id": self.academic_year.id, "term_id": self.term.id, "period": "PRELIM"},
                        parse_qs(valid.context["page_query"] + "&page=2"), parse_qs(valid.context["export_query"]))
            for params in requests:
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.get(self.url, params)
                if "export" in params:
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(response.has_header("Content-Disposition"))
                else:
                    self.assertEqual(response.context["rows"], [])
                    self.assertIsNone(response.context["page_obj"])
                    self.assertFalse(response.context["searched"])
                self.assertContains(response, "10 student analysis limit", status_code=response.status_code)
                self.assertContains(response, "no records were truncated", status_code=response.status_code)
                aggregate = [q["sql"] for q in queries if 'enrollments' in q["sql"] and 'GROUP BY' in q["sql"] and 'COUNT(DISTINCT' in q["sql"]]
                self.assertEqual(len(aggregate), 1)
                self.assertFalse(any('student_period_grades' in q["sql"] and 'LIMIT 25' in q["sql"] for q in queries))
            detail = self.detail(valid)
            self.assertEqual(detail.status_code, 400)
            self.assertIn("10 student analysis limit", " ".join(detail.json()["errors"]))

    def test_global_limit_uses_historical_roster_and_live_eligibility(self):
        self.client.force_login(self.admin_user)
        Student.objects.filter(pk=StudentPeriodGrade.objects.filter(offering=self.offering).first().student_id).update(is_active=False)
        with patch.object(GradeDistributionMonitorService, "MAX_CLASS_STUDENTS", 9):
            live = self.search()
            self.assertFalse(live.context["errors"])
            self.assertEqual(live.context["rows"][0]["eligible_count"], 9)
            self.period.is_active = False
            self.period.save()
            self.assertTrue(self.search().context["errors"])
            self.period.is_active = True
            self.period.save()
            self.offering.status = "ARCHIVED"
            self.offering.save()
            self.assertTrue(self.search().context["errors"])
            with patch.object(GradeDistributionMonitorService, "MAX_CLASS_STUDENTS", 10):
                self.assertEqual(self.search().context["rows"][0]["eligible_count"], 10)

    def test_real_two_thousand_student_limit_accepts_boundary_and_rejects_next(self):
        students = Student.objects.bulk_create([
            Student(tenant=self.tenant, campus=self.campus, department=self.department, program=self.program,
                    student_no=f"REAL-LIMIT-{i}", first_name="Boundary", last_name="Student") for i in range(1991)
        ])
        enrollments = [Enrollment(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year, term=self.term,
                                 course_offering=self.offering, student=s) for s in students]
        Enrollment.objects.bulk_create(enrollments[:1990])
        self.client.force_login(self.admin_user)
        exact = self.search()
        self.assertFalse(exact.context["errors"])
        self.assertEqual(exact.context["rows"][0]["eligible_count"], 2000)
        self.assertEqual(self.detail(exact).status_code, 200)
        self.assertEqual(self.client.get(self.url + "?" + exact.context["export_query"]).status_code, 200)
        Enrollment.objects.bulk_create(enrollments[1990:])
        rejected = self.search()
        self.assertEqual(rejected.context["rows"], [])
        self.assertContains(rejected, "2,000 student analysis limit")
        self.assertEqual(self.detail(exact).status_code, 400)
        self.assertEqual(self.client.get(self.url + "?" + exact.context["export_query"]).status_code, 400)

    def test_real_scope_limits_500_rows_and_50000_saved_and_roster_records(self):
        # Reuse one hundred students across five hundred distinct classes, all in scope.
        students = list(Student.objects.filter(tenant=self.tenant).order_by("id"))
        students += Student.objects.bulk_create([
            Student(tenant=self.tenant, campus=self.campus, department=self.department, program=self.program,
                    student_no=f"VOLUME-{i}", first_name="Volume", last_name="Student") for i in range(90)
        ])
        sections = Section.objects.bulk_create([
            Section(tenant=self.tenant, campus=self.campus, department=self.department, program=self.program,
                    code=f"VOLUME-{i}", name=f"Volume {i}") for i in range(499)
        ])
        offerings = [self.offering] + CourseOffering.objects.bulk_create([
            CourseOffering(tenant=self.tenant, campus=self.campus, department=self.department, program=self.program,
                academic_year=self.academic_year, term=self.term, course=self.course, section=section) for section in sections
        ])
        FacultyAssignment.objects.bulk_create([
            FacultyAssignment(tenant=self.tenant, campus=self.campus, offering=o, faculty_user=self.faculty_user,
                              response_status="ACCEPTED", accepted_at=timezone.now()) for o in offerings[1:]
        ])
        pairs = [(o, student) for o in offerings for student in students
                 if o.pk != self.offering.pk or student.pk not in {s.pk for s in students[:10]}]
        Enrollment.objects.bulk_create([
            Enrollment(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year, term=self.term,
                       course_offering=o, student=student) for o, student in pairs
        ], batch_size=500)
        StudentPeriodGrade.objects.bulk_create([
            StudentPeriodGrade(tenant=self.tenant, campus=self.campus, offering=o, template_period=self.period,
                               student=student, period_grade=90) for o, student in pairs
        ], batch_size=500)
        self.assertEqual(Enrollment.objects.count(), 50000)
        self.assertEqual(StudentPeriodGrade.objects.count(), 50000)
        self.client.force_login(self.admin_user)
        exact = self.search()
        self.assertFalse(exact.context["errors"])
        self.assertEqual((exact.context["result_count"], len(exact.context["rows"])), (500, 25))
        self.assertEqual(self.detail(exact).status_code, 200)
        self.assertEqual(self.client.get(self.url + "?" + exact.context["page_query"] + "&page=20").status_code, 200)
        csv = self.client.get(self.url + "?" + exact.context["export_query"])
        self.assertEqual(csv.status_code, 200)
        import csv as csv_module
        import io
        self.assertEqual(len(list(csv_module.reader(io.StringIO(csv.content.decode("utf-8-sig"))))), 501)

        def assert_rejected(message):
            for params in ({"apply": "1", "academic_year_id": self.academic_year.id, "term_id": self.term.id, "period": "PRELIM"},
                           parse_qs(exact.context["page_query"] + "&page=20")):
                rejected = self.client.get(self.url, params)
                self.assertEqual(rejected.context["rows"], [])
                self.assertContains(rejected, message)
            detail = self.detail(exact)
            self.assertEqual(detail.status_code, 400)
            self.assertIn(message, " ".join(detail.json()["errors"]))
            export = self.client.get(self.url + "?" + exact.context["export_query"])
            self.assertEqual(export.status_code, 400)
            self.assertFalse(export.has_header("Content-Disposition"))

        extra_period = GradingTemplatePeriod.objects.create(template=self.template, code="PrelimExam", name="Prelim", sequence_no=5)
        extra_grade = StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
            template_period=extra_period, student=students[0], period_grade=90)
        assert_rejected("50,000 saved grade records")
        extra_grade.delete()
        new_student = Student.objects.create(tenant=self.tenant, campus=self.campus, department=self.department,
            program=self.program, student_no="VOLUME-EXCESS", first_name="Excess", last_name="Student")
        extra_enrollment = Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, course_offering=self.offering, student=new_student)
        assert_rejected("50,000 records")
        extra_enrollment.delete()
        StudentPeriodGrade.objects.filter(offering=self.offering, student=students[-1], template_period=self.period).delete()
        StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering,
            template_period=extra_period, student=students[0], period_grade=90)
        self.assertEqual(StudentPeriodGrade.objects.count(), 50000)
        assert_rejected("500 class/period rows")

    def test_all_search_limits_accept_exact_boundary_and_reject_one_over(self):
        self.client.force_login(self.admin_user)
        self.assertEqual((GradeDistributionMonitorService.MAX_CLASS_ROWS, GradeDistributionMonitorService.MAX_SAVED_ROWS,
                          GradeDistributionMonitorService.MAX_ROSTER_ROWS, GradeDistributionMonitorService.MAX_CLASS_STUDENTS),
                         (500, 50000, 50000, 2000))
        # Scale only constants: the same database predicates and comparisons execute.
        for name, exact in (("MAX_CLASS_ROWS", 1), ("MAX_SAVED_ROWS", 10), ("MAX_ROSTER_ROWS", 10), ("MAX_CLASS_STUDENTS", 10)):
            with self.subTest(limit=name), patch.object(GradeDistributionMonitorService, name, exact):
                response = self.search()
                self.assertFalse(response.context["errors"])
                self.assertEqual(len(response.context["rows"]), 1)
                self.assertEqual(self.detail(response).status_code, 200)
                self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 200)
                with patch.object(GradeDistributionMonitorService, name, exact - 1):
                    for params in ({"apply": "1", "academic_year_id": self.academic_year.id, "term_id": self.term.id, "period": "PRELIM"},
                                   parse_qs(response.context["page_query"] + "&page=1")):
                        rejected = self.client.get(self.url, params)
                        self.assertTrue(rejected.context["errors"])
                        self.assertEqual(rejected.context["rows"], [])
                    self.assertEqual(self.detail(response).status_code, 400)
                    self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 400)

    def test_numeric_ranges_are_visible_dynamic_with_counts(self):
        from html.parser import HTMLParser
        class VisibleBands(HTMLParser):
            def __init__(self):
                super().__init__()
                self.active = False
                self.text = []
            def handle_starttag(self, tag, attrs):
                if tag == "dl" and "gdm-band-ranges" in dict(attrs).get("class", ""):
                    self.active = True
            def handle_endtag(self, tag):
                if tag == "dl":
                    self.active = False
            def handle_data(self, data):
                if self.active:
                    self.text.append(data)
        self.client.force_login(self.admin_user)
        for threshold in (75, 80):
            with self.subTest(threshold=threshold):
                self.template.passing_grade_threshold = threshold
                self.template.save()
                response = self.search()
                parser = VisibleBands()
                parser.feed(response.content.decode())
                visible = " ".join(parser.text)
                for band in response.context["rows"][0]["distribution"]:
                    self.assertIn(band["label"], visible)
                    self.assertIn(band["range"], visible)
                    self.assertIn(str(band["count"]), visible)

    def test_historical_assignment_without_acceptance_evidence_unavailable(self):
        self.offering.status = "CLOSED"; self.offering.save()
        FacultyAssignment.objects.filter(offering=self.offering).update(is_active=False, accepted_at=None)
        self.client.force_login(self.admin_user)
        self.assertEqual(self.search().context["rows"], [])

    def test_live_roster_excludes_inactive_dropped_and_foreign_students(self):
        first = Enrollment.objects.filter(course_offering=self.offering).first()
        first.is_active = False; first.save()
        second = Enrollment.objects.filter(course_offering=self.offering, is_active=True).first()
        second.enrollment_status = "DRP"; second.save()
        self.client.force_login(self.admin_user)
        row = self.search().context["rows"][0]
        self.assertEqual(row["eligible_count"], 8)
        self.assertEqual(row["graded_count"], 8)

    def test_database_pagination_and_export_limits(self):
        for i in range(26):
            offering = self._create_extra_offering(f"PAGE{i}", "Pagination Course", f"SEC{i}")
            self._seed_grades(offering, [90])
        self.client.force_login(self.admin_user)
        with CaptureQueriesContext(connection) as queries:
            response = self.search()
        self.assertEqual(len(response.context["rows"]), 25)
        self.assertEqual(response.context["result_count"], 27)
        self.assertTrue(any('GROUP BY' in q["sql"] and 'LIMIT 25' in q["sql"] for q in queries))
        page_two = self.client.get(self.url + "?" + response.context["page_query"] + "&page=2")
        self.assertEqual(len(page_two.context["rows"]), 2)
        self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 200)
        with patch.object(GradeDistributionMonitorService, "MAX_CLASS_ROWS", 26):
            narrowed = self.search()
            self.assertEqual(narrowed.context["rows"], [])
            self.assertContains(narrowed, "Narrow your criteria")
            self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 400)
        with patch.object(GradeDistributionMonitorService, "MAX_SAVED_ROWS", 5):
            self.assertTrue(self.search().context["errors"])
        with patch.object(GradeDistributionMonitorService, "MAX_CLASS_STUDENTS", 5):
            self.assertTrue(self.search().context["errors"])

    def test_monitor_never_writes_gradebook_or_calls_recompute(self):
        self.client.force_login(self.admin_user)
        before = list(StudentPeriodGrade.objects.values())
        with patch.object(FacultyGradingService, "recompute_period_summary", side_effect=AssertionError("recompute forbidden")):
            with CaptureQueriesContext(connection) as queries:
                response = self.search()
                self.detail(response)
                self.client.get(self.url + "?" + response.context["export_query"])
        self.assertEqual(list(StudentPeriodGrade.objects.values()), before)
        writes = [q["sql"] for q in queries if q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertFalse(any('student_period_grades' in q or 'student_activity_scores' in q or 'grade_submissions' in q or 'audit_logs' in q for q in writes))

    def test_raw_examination_and_configured_activity_grade_remain_distinct(self):
        exam = GradingTemplateComponent.objects.create(template_period=self.period, code="EXAM", name="Exam", weight_percentage=50, is_exam_component=True)
        standing = GradingTemplateComponent.objects.create(template_period=self.period, code="CS", name="Class Standing", weight_percentage=50)
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.period, template_component=exam, title="Exam", total_score=50, created_by_user=self.faculty_user)
        quiz = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.period, template_component=standing, title="Quiz", total_score=100, created_by_user=self.faculty_user)
        students = list(Student.objects.filter(enrollments__course_offering=self.offering).order_by("id")[:5])
        for student in students:
            StudentActivityScore.objects.create(activity=activity, student=student, raw_score=25, computed_score=75)
            StudentActivityScore.objects.create(activity=quiz, student=student, raw_score=100, computed_score=100)
        StudentPeriodGrade.objects.filter(student__in=students, offering=self.offering).update(exam_grade=75, class_standing_grade=100)
        self.client.force_login(self.admin_user)
        data = self.detail(self.search()).json()
        measures = {m["label"]: m for m in data["measures"]}
        self.assertEqual(Decimal(measures["Exam - Actual examination percentage"]["average"]), 50)
        self.assertEqual(Decimal(measures["Exam - Saved configured activity grade"]["average"]), 75)
        self.assertEqual(Decimal(measures["Examination - Exam"]["average"]), 75)
        self.assertEqual(Decimal(measures["Class standing - Class Standing"]["average"]), 100)
        self.assertEqual(data["incomplete_source_count"], 5)

    def test_direct_percentage_multiple_components_and_no_combined_normalization(self):
        students = list(Student.objects.filter(enrollments__course_offering=self.offering).order_by("id")[:5])
        for i in range(2):
            c = GradingTemplateComponent.objects.create(template_period=self.period, code=f"EX{i}", name=f"Exam {i}", weight_percentage=50, is_exam_component=True, score_input_mode="DIRECT_PERCENTAGE")
            a = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.period, template_component=c, title=f"Exam {i}", total_score=100, created_by_user=self.faculty_user)
            for student in students:
                StudentActivityScore.objects.create(activity=a, student=student, raw_score=100, computed_score=100)
        StudentPeriodGrade.objects.filter(student__in=students, offering=self.offering).update(exam_grade=200)
        self.client.force_login(self.admin_user)
        data = self.detail(self.search()).json()
        total = data["measures"][0]
        self.assertEqual(Decimal(total["average"]), 200)
        self.assertEqual(total["exact_100_count"], 0)
        components = [m for m in data["measures"] if m["label"].startswith("Examination - Exam")]
        self.assertEqual(len(components), 2)
        self.assertTrue(all(m["all_100"] for m in components))

    def test_monitor_scope_follows_faculty_assignment_scope_not_offering_department(self):
        reviewer_department = Department.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            code="FVW_COLL_IS",
            name="Fairview Information Systems",
        )
        scoped_reviewer = self._user(
            "ac_scope",
            "Area",
            "Chair",
            self.tenant,
            self.campus,
            reviewer_department,
        )
        scoped_faculty = self._user(
            "faculty_scope",
            "Scoped",
            "Faculty",
            self.tenant,
            self.campus,
            reviewer_department,
        )
        self._assign_permissions(scoped_reviewer, include_monitor=True)
        self._assign_faculty_role(scoped_faculty)
        offering = self._create_extra_offering("MISMATCH101", "Offering Department Mismatch", "BSA1D")
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            faculty_user=scoped_faculty,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )
        self._seed_grades(offering, [91, 92, 93, 94, 95, 96, 97, 98, 81, 76])

        self.client.force_login(scoped_reviewer)
        response = self.search(department_id=reviewer_department.id, faculty_id=scoped_faculty.id)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MISMATCH101")
        self.assertContains(response, "Offering Department Mismatch")

        self.assertIn(scoped_faculty.id, [f["id"] for f in response.context["filter_options"]["faculty"]])
        self.assertIn(reviewer_department.id, [d["id"] for d in response.context["filter_options"]["departments"]])
        self.assertEqual(len(response.context["rows"]), 1)
        row = response.context["rows"][0]
        detail = self.detail(response, row)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["course_code"], "MISMATCH101")
        export = self.client.get(self.url + "?" + response.context["export_query"])
        self.assertContains(export, "MISMATCH101")
        self.assertNotContains(export, "ACC101")

    def _second_campus(self, grant_admin=False):
        campus = Campus.objects.create(tenant=self.tenant, code="CUB", name="Cubao")
        dept = Department.objects.create(tenant=self.tenant, campus=campus, code="BSA-CUB", name="Accountancy")
        program = Program.objects.create(tenant=self.tenant, campus=campus, department=dept, code="BSA", name="BSA")
        course = Course.objects.create(tenant=self.tenant, campus=campus, department=dept, code="CUB101", title="Cubao Accounting")
        section = Section.objects.create(tenant=self.tenant, campus=campus, department=dept, program=program, code="CUB1", name="CUB1")
        offering = CourseOffering.objects.create(tenant=self.tenant, campus=campus, department=dept, program=program, academic_year=self.academic_year, term=self.term, course=course, section=section)
        UserRole.objects.create(user=self.faculty_user, role=Role.objects.get(code="FACULTY"), tenant=self.tenant, campus=campus, department=dept)
        FacultyAssignment.objects.create(tenant=self.tenant, campus=campus, offering=offering, faculty_user=self.faculty_user, response_status="ACCEPTED", accepted_at=timezone.now())
        self._seed_grades(offering, [100, 100])
        if grant_admin:
            role = self.admin_user.user_roles.exclude(role__code="FACULTY").get().role
            UserRole.objects.create(user=self.admin_user, role=role, tenant=self.tenant, campus=campus, department=dept)
        return campus, dept, offering

    def test_same_faculty_in_other_campus_cannot_leak_filters_results_modal_csv(self):
        campus, dept, offering = self._second_campus()
        self.client.force_login(self.admin_user)
        response = self.search(campus_id="all")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertNotContains(response, "Cubao Accounting")
        self.assertNotIn(dept.id, [d["id"] for d in response.context["filter_options"]["departments"]])
        self.assertNotContains(self.client.get(self.url + "?" + response.context["export_query"]), "CUB101")
        url = reverse("admin_portal:grade_distribution_monitor_detail", args=[offering.id, self.period.id])
        self.assertEqual(self.client.get(url + "?" + response.context["page_query"]).status_code, 404)
        self.assertTrue(self.search(campus_id=campus.id).context["errors"])
        self.assertTrue(self.search(department_id=dept.id).context["errors"])

    def test_authorized_all_campuses_selected_campus_and_duplicate_department_names(self):
        campus, dept, offering = self._second_campus(grant_admin=True)
        self.client.force_login(self.admin_user)
        default = self.search()
        self.assertEqual(len(default.context["rows"]), 1)
        all_response = self.search(campus_id="all")
        self.assertEqual(len(all_response.context["rows"]), 2)
        labels = [d["label"] for d in all_response.context["filter_options"]["departments"]]
        self.assertEqual(len(labels), 2)
        self.assertEqual(len(set(labels)), 2)
        selected = self.search(campus_id=campus.id)
        self.assertEqual(selected.context["rows"][0]["course_code"], "CUB101")
        self.assertEqual([d["id"] for d in selected.context["filter_options"]["departments"]], [dept.id])
        self.assertEqual(self.detail(selected).status_code, 200)
        deny = UserPermission.objects.create(user=self.admin_user, permission=Permission.objects.get(code="grade_distribution_monitor.read"), tenant=self.tenant, campus=campus, grant_type="DENY")
        self.assertEqual(self.client.get(self.url + "?" + all_response.context["export_query"]).status_code, 400)
        narrowed = self.search(campus_id="all")
        self.assertEqual(len(narrowed.context["rows"]), 1)
        deny.delete()

    def test_foreign_tenant_offering_and_period_cannot_be_requested(self):
        other_tenant = Tenant.objects.create(code="OTHER", name="Other")
        other_template = GradingTemplate.objects.create(tenant=other_tenant, code="OTHER", name="Other")
        other_period = GradingTemplatePeriod.objects.create(template=other_template, code="PRELIM", name="Prelim")
        # Even inconsistent foreign physical-period links must fail closed.
        row = StudentPeriodGrade.objects.filter(offering=self.offering).first()
        row.template_period = other_period; row.save()
        self.client.force_login(self.admin_user)
        response = self.search()
        self.assertEqual(response.context["rows"][0]["graded_count"], 9)
        url = reverse("admin_portal:grade_distribution_monitor_detail", args=[self.offering.id, other_period.id])
        self.assertEqual(self.client.get(url + "?" + response.context["page_query"]).status_code, 404)
        self.assertTrue(self.search(academic_year_id=999999).context["errors"])

    def test_search_context_bound_to_user_and_topbar_scope(self):
        self.client.force_login(self.admin_user)
        response = self.search()
        other = self._user("other_review", "Other", "Review", self.tenant, self.campus, self.department)
        self._assign_permissions(other, include_monitor=True)
        self.client.force_login(other)
        self.assertEqual(self.detail(response).status_code, 400)

    def test_subcomponent_detail_weighting_and_transmutation_preserve_saved_period(self):
        component = GradingTemplateComponent.objects.create(template_period=self.period, code="EX", name="Weighted Exam", weight_percentage=100, is_exam_component=True)
        sub = GradingTemplateSubcomponent.objects.create(template_component=component, code="PART", name="Part", weight_percentage=100)
        students = list(Student.objects.filter(enrollments__course_offering=self.offering).order_by("id")[:5])
        for i, weight, grade in ((1, 25, 40), (2, 75, 100)):
            d = GradingTemplateDetail.objects.create(template_subcomponent=sub, code=f"D{i}", name=f"Detail {i}", weight_percentage=weight)
            a = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.period, template_component=component, template_subcomponent=sub, template_detail=d, title=f"Part {i}", total_score=100, created_by_user=self.faculty_user)
            for student in students:
                StudentActivityScore.objects.create(activity=a, student=student, raw_score=grade, computed_score=grade)
        self.client.force_login(self.admin_user)
        data = self.detail(self.search()).json()
        measure = next(m for m in data["measures"] if m["label"] == "Examination - Weighted Exam")
        self.assertEqual(Decimal(measure["average"]), 85)
        TenantGradingProfile.objects.create(tenant=self.tenant, profile_code="DEPED", profile_name="DepEd", grading_template=self.template, period_grade_formula_mode="DEPED_TRANSMUTATION")
        data = self.detail(self.search()).json()
        measure = next(m for m in data["measures"] if m["label"] == "Examination - Weighted Exam")
        self.assertEqual(Decimal(measure["average"]), 70)
        self.assertEqual(Decimal(data["average"]), Decimal("90.40"))

    def test_engine_attendance_interpretation_is_batched_and_read_only(self):
        from apps.attendance.models import AttendanceSession, AttendanceRecord
        c = GradingTemplateComponent.objects.create(template_period=self.period, code="CS", name="Attendance Standing", weight_percentage=100)
        GradingTemplateSubcomponent.objects.create(template_component=c, code="ATT", name="Attendance", weight_percentage=100, is_attendance_component=True)
        session = AttendanceSession.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.period, session_date=date(2025, 7, 1), created_by_user=self.faculty_user)
        students = list(Student.objects.filter(enrollments__course_offering=self.offering).order_by("id")[:5])
        for student in students:
            AttendanceRecord.objects.create(session=session, student=student, status_code="P")
        self.client.force_login(self.admin_user)
        response = self.search()
        with CaptureQueriesContext(connection) as queries:
            data = self.detail(response).json()
        measure = next(m for m in data["measures"] if m["label"] == "Class standing - Attendance Standing")
        self.assertEqual(Decimal(measure["average"]), 100)
        self.assertEqual(measure["graded_count"], 5)
        self.assertEqual(data["incomplete_source_count"], 5)
        self.assertLessEqual(sum('attendance_records' in q["sql"] for q in queries), 2)

    def test_missing_assessment_routes_and_zero_sources_are_not_missing_saved_grades(self):
        c = GradingTemplateComponent.objects.create(template_period=self.period, code="EX", name="Exam", weight_percentage=100, is_exam_component=True)
        for code in ("A", "B"):
            GradingTemplateSubcomponent.objects.create(template_component=c, code=code, name=code, weight_percentage=50)
        sub = c.subcomponents.get(code="A")
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.period, template_component=c, template_subcomponent=sub, title="Zero exam", total_score=100, created_by_user=self.faculty_user)
        students = list(Student.objects.filter(enrollments__course_offering=self.offering).order_by("id")[:5])
        for student in students:
            StudentActivityScore.objects.create(activity=activity, student=student, raw_score=0, computed_score=50)
        self.client.force_login(self.admin_user)
        data = self.detail(self.search()).json()
        self.assertEqual(data["missing_count"], 0)
        self.assertEqual(data["incomplete_source_count"], 10)
        raw = next(m for m in data["measures"] if m["label"] == "Zero exam - Actual examination percentage")
        self.assertEqual(raw["graded_count"], 5)
        self.assertEqual(Decimal(raw["minimum"]), 0)
        self.assertEqual(Decimal(data["average"]), Decimal("90.40"))

    def test_component_activity_limit_is_explicit_and_saved_totals_remain_available(self):
        c = GradingTemplateComponent.objects.create(template_period=self.period, code="EX", name="Exam", weight_percentage=100, is_exam_component=True)
        GradeActivity.objects.bulk_create([GradeActivity(tenant=self.tenant, campus=self.campus, offering=self.offering, template_period=self.period, template_component=c, title=f"Exam {i}", total_score=100, created_by_user=self.faculty_user) for i in range(201)])
        self.client.force_login(self.admin_user)
        data = self.detail(self.search()).json()
        self.assertEqual(len(data["measures"]), 2)
        self.assertIn("200 activities", " ".join(data["notes"]))
        self.assertIsNone(data["incomplete_source_count"])
        self.assertEqual(data["graded_count"], 10)
        self._privacy_assert_payload(data)
        self._privacy_assert_payload(self.search().context["rows"][0])

    def test_superuser_explicit_monitor_deny_has_no_dataset_access(self):
        self.admin_user.is_superuser = True; self.admin_user.save()
        UserPermission.objects.create(user=self.admin_user, permission=Permission.objects.get(code="grade_distribution_monitor.read"), tenant=self.tenant, campus=self.campus, grant_type="DENY")
        self.client.force_login(self.admin_user)
        response = self.search()
        self.assertEqual(response.context["rows"], [])
        self.assertTrue(response.context["errors"])

    def test_missing_historical_roster_explains_omitted_saved_records(self):
        self.offering.status = "ARCHIVED"; self.offering.save()
        Enrollment.objects.filter(course_offering=self.offering).delete()
        self.client.force_login(self.admin_user)
        data = self.detail(self.search()).json()
        self.assertEqual(data["eligible_count"], 0)
        self.assertEqual(data["graded_count"], 0)
        self.assertIn("10 saved record(s)", " ".join(data["notes"]))
        self.assertIn("No dependable eligible enrollment", " ".join(data["notes"]))

    def test_ended_open_term_uses_retained_historical_assignments_and_roster(self):
        self.term.end_date = date(2025, 6, 30)
        self.term.save()
        FacultyAssignment.objects.filter(offering=self.offering).update(is_active=False)
        Enrollment.objects.filter(course_offering=self.offering).update(is_active=False)
        Student.objects.filter(tenant=self.tenant).update(is_active=False)
        self.client.force_login(self.admin_user)
        response = self.search()
        row = response.context["rows"][0]
        self.assertTrue(row["historical"])
        self.assertEqual(row["graded_count"], 10)
        self.assertEqual(row["average"], Decimal("90.40"))
        self.assertEqual(self.detail(response).status_code, 200)

    def test_faculty_department_filters_preserve_default_scope_role_fallbacks(self):
        other_department = Department.objects.create(tenant=self.tenant, campus=self.campus, code="DEFAULT", name="Default Department")
        self.faculty_user.default_department = other_department
        self.faculty_user.save()
        UserRole.objects.filter(user=self.faculty_user, role__code="FACULTY").update(tenant=None, campus=None)
        self.client.force_login(self.admin_user)
        response = self.search(department_id=self.department.id)
        self.assertEqual([d["id"] for d in response.context["filter_options"]["departments"]], [self.department.id])
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(self.detail(response).status_code, 200)
        self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 200)

    def _user(self, username, first_name, last_name, tenant, campus, department):
        return User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="testpass123",
            first_name=first_name,
            last_name=last_name,
            default_tenant=tenant,
            default_campus=campus,
            default_department=department,
            privacy_consent_version=getattr(settings, "PRIVACY_CONSENT_VERSION", "2026-03"),
            privacy_consent_at=timezone.now(),
        )

    def _assign_permissions(self, user, *, include_monitor):
        role, _ = Role.objects.get_or_create(code=f"ROLE_{user.username}", defaults={"name": f"Role {user.username}"})
        admin_access, _ = Permission.objects.get_or_create(
            code="admin_portal.access",
            defaults={"module": "admin_portal", "action": "access"},
        )
        RolePermission.objects.get_or_create(role=role, permission=admin_access)
        if include_monitor:
            monitor, _ = Permission.objects.get_or_create(
                code="grade_distribution_monitor.read",
                defaults={"module": "grade_distribution_monitor", "action": "read"},
            )
            RolePermission.objects.get_or_create(role=role, permission=monitor)
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=user.default_tenant,
            campus=user.default_campus,
            department=user.default_department,
        )

    def _assign_faculty_role(self, user):
        role, _ = Role.objects.get_or_create(code="FACULTY", defaults={"name": "Faculty"})
        UserRole.objects.create(
            user=user,
            role=role,
            tenant=user.default_tenant,
            campus=user.default_campus,
            department=user.default_department,
        )

    def _seed_grades(self, offering, values, period=None, extra_enrolled_without_grade=0):
        period = period or self.period
        for index, value in enumerate(values, start=1):
            student = Student.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                department=offering.department,
                program=offering.program,
                student_no=f"{offering.course.code}-{index:03d}",
                last_name=f"Student{index}",
                first_name="Test",
            )
            Enrollment.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                academic_year=offering.academic_year,
                term=offering.term,
                student=student,
                course_offering=offering,
            )
            StudentPeriodGrade.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                offering=offering,
                template_period=period,
                student=student,
                period_grade=Decimal(str(value)),
            )
        for index in range(len(values) + 1, len(values) + extra_enrolled_without_grade + 1):
            student = Student.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                department=offering.department,
                program=offering.program,
                student_no=f"{offering.course.code}-{index:03d}",
                last_name=f"Student{index}",
                first_name="NoGrade",
            )
            Enrollment.objects.create(
                tenant=offering.tenant,
                campus=offering.campus,
                academic_year=offering.academic_year,
                term=offering.term,
                student=student,
                course_offering=offering,
            )

    def _create_extra_offering(self, course_code, course_title, section_code):
        course = Course.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            code=course_code,
            title=course_title,
        )
        section = Section.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            code=section_code,
            name=section_code,
        )
        offering = CourseOffering.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            department=self.department,
            program=self.program,
            academic_year=self.academic_year,
            term=self.term,
            course=course,
            section=section,
        )
        FacultyAssignment.objects.create(
            tenant=self.tenant,
            campus=self.campus,
            offering=offering,
            faculty_user=self.faculty_user,
            response_status=FacultyAssignment.ResponseStatus.ACCEPTED,
            accepted_at=timezone.now(),
            is_primary=True,
        )
        return offering


class GradeDistributionPrivacyMetricTests(SimpleTestCase):
    def assert_withheld(self, result):
        for key in ("average", "minimum", "maximum", "exact_100_count", "exact_100_percentage", "all_100"):
            self.assertIsNone(result[key], key)
        self.assertEqual(result["frequency"], [])
        self.assertTrue(all(b["count"] is None and b["percentage"] is None for b in result["distribution"]))

    def test_zero_to_six_distinct_saved_values_and_real_zero(self):
        for n in range(7):
            with self.subTest(n=n):
                privacy = PrivacyFamily({i: False for i in range(n)}, "all", Decimal("75"))
                result = privacy.measure({i: Decimal("0") for i in range(n)}, eligible_count=10)
                self.assertEqual(result["graded_count"], n)
                self.assertEqual(result["analytics_state"], "unavailable" if n == 0 else ("privacy_suppressed" if n < 5 else "disclosed"))
                if n < 5:
                    self.assert_withheld(result)
                else:
                    self.assertEqual(result["average"], 0)
                    self.assertEqual(result["minimum"], 0)

    def test_nine_finalized_one_unfinalized_suppresses_all_three_selections(self):
        statuses = {i: i < 9 for i in range(10)}
        for status in ("all", "finalized", "unfinalized"):
            result = PrivacyFamily(statuses, status, 75).measure({i: 97 for i in range(10)}, eligible_count=10)
            self.assertTrue(result["privacy_suppressed"])
            self.assert_withheld(result)
        self.assertEqual(PrivacyFamily(statuses, "finalized", 75).measure({i: 97 for i in range(10)}, eligible_count=10)["privacy_reason"], "complementary_population")

    def test_five_finalized_five_unfinalized_allow_all_and_both_subsets(self):
        statuses = {i: i < 5 for i in range(10)}
        for status, count in (("all", 10), ("finalized", 5), ("unfinalized", 5)):
            result = PrivacyFamily(statuses, status, 75).measure({i: 97 for i in range(10)}, eligible_count=10)
            self.assertEqual(result["analytics_state"], "disclosed")
            self.assertEqual(result["graded_count"], count)

    def test_owner_measure_example_period_thirty_exam_three_standing_twenty_five(self):
        family = PrivacyFamily({i: False for i in range(30)}, "all", 75)
        period = family.measure({i: 90 for i in range(30)}, eligible_count=30)
        exam = family.measure({i: 100 for i in range(3)}, eligible_count=30)
        standing = family.measure({i: 80 for i in range(25)}, eligible_count=30)
        self.assertEqual([r["analytics_state"] for r in (period, exam, standing)], ["disclosed", "privacy_suppressed", "disclosed"])
        self.assert_withheld(exam)

    def test_overlapping_populations_prevent_small_complements_and_intersections(self):
        family = PrivacyFamily({i: False for i in range(10)}, "all", 75)
        first = family.measure({i: 90 for i in range(10)}, eligible_count=10)
        second = family.measure({i: 80 for i in range(5)}, eligible_count=10)
        # All sets have at least five; comparing these two subsets would isolate one.
        third = family.measure({i: 70 for i in (0, 1, 2, 3, 5)}, eligible_count=10)
        self.assertEqual((first["analytics_state"], second["analytics_state"]), ("disclosed", "disclosed"))
        self.assertEqual(third["privacy_reason"], "complementary_population")
        self.assert_withheld(third)

    def test_unknown_period_status_is_a_third_complement_for_components(self):
        family = PrivacyFamily({i: True for i in range(9)}, "all", 75)
        result = family.measure({i: 90 for i in range(10)}, eligible_count=10)
        self.assertTrue(result["privacy_suppressed"])
        self.assert_withheld(result)

    def test_repeated_source_records_cannot_inflate_distinct_population(self):
        samples = {i % 4: 99 for i in range(40)}
        result = PrivacyFamily({i: False for i in range(4)}, "all", 75).measure(samples, eligible_count=10)
        self.assertEqual(result["graded_count"], 4)
        self.assert_withheld(result)

    def test_formula_plan_sparse_leaf_blocks_all_paths_not_just_memberships(self):
        family = PrivacyFamily({i: False for i in range(10)}, "all", 75)
        plan = FormulaPrivacyPlan(family)
        for key, samples in (("period", {i: 90 for i in range(10)}),
                             ("exam", {i: 79 for i in range(10)}),
                             ("common", {i: 75 for i in range(10)}), ("supplemental", {0: 43})):
            plan.add(key, samples)
        for a, b in (("period", "exam"), ("exam", "common"), ("exam", "supplemental")):
            plan.link(a, b)
        plan.finalize()
        for key in ("period", "exam", "common", "supplemental"):
            self.assert_withheld(plan.measure(key, eligible_count=10))

    def test_formula_plan_unrelated_family_and_unknown_dependencies(self):
        for unresolved in (False, True):
            family = PrivacyFamily({i: False for i in range(10)}, "all", 75)
            plan = FormulaPrivacyPlan(family)
            plan.add("safe_period", {i: 90 for i in range(10)})
            plan.add("exam", {i: 79 for i in range(10)})
            plan.add("supplemental", {0: 43})
            plan.link("exam", "supplemental")
            plan.finalize(unresolved=unresolved)
            period = plan.measure("safe_period", eligible_count=10)
            self.assertEqual(period["analytics_state"], "privacy_suppressed" if unresolved else "disclosed")
            self.assert_withheld(plan.measure("exam", eligible_count=10))

    def test_formula_plan_never_calculates_metrics_for_blocked_family(self):
        plan = FormulaPrivacyPlan(PrivacyFamily({i: False for i in range(10)}, "all", 75))
        plan.add("period", {i: 90 for i in range(10)})
        plan.add("source", {0: 43})
        plan.link("period", "source")
        with patch("apps.admin_portal.grade_distribution_metrics.metrics", side_effect=AssertionError("hidden statistics calculated")):
            plan.finalize()
            self.assert_withheld(plan.measure("period", eligible_count=10))

    def test_formula_plan_retains_safe_siblings_after_cutting_all_ancestor_paths(self):
        plan = FormulaPrivacyPlan(PrivacyFamily({i: False for i in range(10)}, "all", 75))
        for key, samples in (("period", {i: 90 for i in range(10)}), ("exam", {i: 79 for i in range(10)}),
                             ("common", {i: 75 for i in range(10)}), ("standing", {i: 80 for i in range(10)}),
                             ("supplemental", {0: 43})):
            plan.add(key, samples)
        for parent, child in (("period", "exam"), ("period", "standing"), ("exam", "common"), ("exam", "supplemental")):
            plan.depends_on(parent, child)
        plan.finalize()
        for key in ("period", "exam", "supplemental"):
            self.assert_withheld(plan.measure(key, eligible_count=10))
        for key in ("common", "standing"):
            self.assertEqual(plan.measure(key, eligible_count=10)["analytics_state"], "disclosed")
