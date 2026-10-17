import csv
import io
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.core.cache.backends.locmem import LocMemCache
from django.core import signing
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.admin_portal import tests_academic_performance_insights as fixtures
from apps.admin_portal import insights_views
from apps.admin_portal.academic_performance import AcademicPerformanceInsightService as Official
from apps.admin_portal.insights import SUPPRESSED, UNAVAILABLE, Selection, activity_metrics, disclosure_mask
from apps.admin_portal.insights_generation import CHANGED_MESSAGE
from apps.academics.models import Course, FacultyAssignment
from apps.attendance.models import AttendanceRecord, AttendanceSession
from apps.enrollment.models import Enrollment
from apps.grading.models import GradeActivity, GradingTemplateComponent, GradingTemplatePeriod, StudentActivityScore, StudentPeriodGrade
from apps.rbac.models import Permission, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class InsightsImprovementTests(TestCase):
    _enable_feature = fixtures.AcademicPerformanceInsightsTests._enable_feature
    _role_with_access = fixtures.AcademicPerformanceInsightsTests._role_with_access
    _user = fixtures.AcademicPerformanceInsightsTests._user
    _offering = fixtures.AcademicPerformanceInsightsTests._offering
    _assign = fixtures.AcademicPerformanceInsightsTests._assign
    _student = fixtures.AcademicPerformanceInsightsTests._student
    _activity = fixtures.AcademicPerformanceInsightsTests._activity
    _score = staticmethod(fixtures.AcademicPerformanceInsightsTests._score)

    def setUp(self):
        fixtures.AcademicPerformanceInsightsTests.setUp(self)
        cache.clear()
        for offering, activity in ((self.offering_a, self.activity_a), (self.offering_b, self.activity_b)):
            for index in range(3):
                student = self._student(offering, f"EXTRA-{offering.id}-{index}", f"Extra{index}")
                self._score(activity, student, 90, 95)
        self.client.force_login(self.area_chair)

    def post(self, action, **kwargs):
        return self.client.post(self.url, {**self.filters, "action": action, **kwargs})

    def snapshot(self, **kwargs):
        response = self.post("report", **kwargs)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_get_never_computes_even_with_report_url_parameters(self):
        with patch.object(Official, "get_section_performance_summary") as compute:
            response = self.client.get(self.url, {**self.filters, "action": "report", "generate": "1"})
        compute.assert_not_called()
        self.assertContains(response, "Select your filters and click View Performance.")
        self.assertContains(response, "View Performance")
        self.assertNotContains(response, "Generate Report")

    def test_blank_initial_page_does_not_resolve_every_class_template(self):
        with patch("apps.admin_portal.insights.FacultyGradingService.resolve_template_for_offering") as resolve:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        resolve.assert_not_called()

    def test_five_buttons_preserve_order_labels_and_destinations(self):
        html = self.client.get(self.url).content.decode()
        labels = ["Midterm Exam Performance", "Activity Consistency", "Export Sections CSV", "Export Student Review CSV", "Back to Dashboard"]
        positions = [html.index(">" + label + "</a>") for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(reverse("admin_portal:midterm_exam_performance"), html)
        self.assertIn(reverse("admin_portal:academic_activity_consistency"), html)

    def test_summary_first_classes_and_details_are_explicit_and_lazy(self):
        with patch("apps.admin_portal.insights_views.activity_metrics", wraps=activity_metrics) as metrics:
            snapshot = self.snapshot()
            metrics.assert_not_called()
            self.assertNotIn("Class Activities and Performance", snapshot["html"])
            self.assertEqual(snapshot["summary"]["classes"], 2)
            with patch.object(Official, "get_section_performance_summary") as compute:
                response = self.post("classes", token=snapshot["token"])
                compute.assert_not_called()
            self.assertEqual(response.status_code, 200)
            self.assertIn("Class Activities and Performance", response.json()["html"])
            self.assertEqual(metrics.call_count, 1)

    def test_small_group_suppression_is_shared_by_summary_details_and_csv(self):
        Enrollment.objects.filter(course_offering=self.offering_a, student__student_no__startswith="EXTRA").update(is_active=False)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["summary"]["suppressed"], 1)
        detail = self.post("detail", token=snapshot["token"], offering_id=self.offering_a.id)
        self.assertIn(SUPPRESSED, detail.json()["html"])
        self.assertNotIn("Average Grade:", detail.json()["html"])
        export = self.client.get(self.url, {**self.filters, "export": "sections"})
        rows = list(csv.DictReader(io.StringIO(export.content.decode())))
        self.assertEqual(next(r for r in rows if r["Section"] == self.offering_a.section.code)["Average"], SUPPRESSED)
        self.assertNotIn(self.student_a1.student_no, self.post("classes", token=snapshot["token"]).content.decode())

    def test_overlap_policy_cannot_be_bypassed_by_course_filter(self):
        # One shared student leaves small membership atoms even though both classes have >=5 grades.
        Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, course_offering=self.offering_b, student=self.student_a1)
        self._score(self.activity_b, self.student_a1, 80, 90)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["summary"]["suppressed"], 2)
        self.assertEqual(self.snapshot(faculty_id=self.faculty.id)["summary"]["suppressed"], 2)

    def test_minimum_five_and_membership_atom_boundaries(self):
        def section(ids):
            return {"coverage": {"rows": [{"student_id": pk, "usable_grade": True} for pk in ids], "computed_grade_count": len(ids)}}
        self.assertEqual(disclosure_mask({1: section(range(5))}), set())
        self.assertEqual(disclosure_mask({1: section(range(4))}), {1})
        self.assertEqual(disclosure_mask({1: section(range(5)), 2: section(range(1, 6))}), {1, 2})

    def test_rare_outcome_counts_and_complements_are_withheld(self):
        snapshot = self.snapshot()
        self.assertEqual(snapshot["summary"]["below"], SUPPRESSED)
        detail = self.post("detail", token=snapshot["token"], offering_id=self.offering_a.id).json()["html"]
        self.assertIn("Students Below Passing:</strong> Suppressed", detail)
        self.assertIn("Strongly above threshold", detail)
        self.assertIn("counts withheld", detail)

    def test_overlapping_outcome_differences_cannot_leak_through_charts_or_student_csv(self):
        Enrollment.objects.all().delete()
        StudentActivityScore.objects.all().delete()
        self.component.score_input_mode = "DIRECT_PERCENTAGE"
        self.component.save(update_fields=["score_input_mode"])
        for index in range(20):
            student = self._student(self.offering_a, f"COMMON-{index}", f"Common{index}")
            Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
                term=self.term, course_offering=self.offering_b, student=student)
            grade_a = 40 if index < 10 else 90
            grade_b = 40 if index < 9 or index == 10 else 90
            self._score(self.activity_a, student, grade_a, grade_a)
            self._score(self.activity_b, student, grade_b, grade_b)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["summary"]["disclosed"], 2)
        self.assertEqual(snapshot["summary"]["below"], SUPPRESSED)
        html = self.post("detail", token=snapshot["token"], offering_id=self.offering_a.id).json()["html"]
        self.assertIn("Category counts withheld", html)
        export = self.client.get(self.url, {**self.filters, "export": "students"})
        self.assertEqual(list(csv.DictReader(io.StringIO(export.content.decode()))), [])

    def test_one_student_below_does_not_label_entire_class_as_failing(self):
        snapshot = self.snapshot()
        html = self.post("classes", token=snapshot["token"]).json()["html"]
        self.assertNotIn('badge text-bg-danger', html)
        self.assertIn("Some students are below", html)

    def test_invalid_filters_fail_closed_but_options_clear_them(self):
        response = self.post("report", campus_id=999999, department_id=999999, faculty_id=999999)
        self.assertEqual(response.status_code, 400)
        options = self.post("options", department_id=999999, course_code="NONEXISTENT", faculty_id=999999).json()
        self.assertIsNone(options["filters"]["department_id"])
        self.assertEqual(options["filters"]["course_code"], "")
        self.assertIsNone(options["filters"]["faculty_id"])

    def add_campus(self):
        campus = Campus.objects.create(tenant=self.tenant, code="CUB", name="Cubao")
        department = Department.objects.create(tenant=self.tenant, campus=campus, code="CUB-AC", name="Cubao Area")
        program = Program.objects.create(tenant=self.tenant, campus=campus, department=department, code="CUB-P", name="Cubao")
        faculty = self._user("other-faculty", campus, department)
        UserRole.objects.create(user=faculty, role=self.faculty_role, tenant=self.tenant, campus=campus, department=department)
        offering = self._offering("CUB-A", campus, department, program)
        self._assign(offering, faculty)
        return campus, department, faculty, offering

    def test_cao_campus_department_and_faculty_dependencies_and_direct_deny(self):
        campus, department, faculty, offering = self.add_campus()
        cao = self._user("cao", self.campus, self.department)
        role = self._role_with_access("CAO", "CAO")
        for c in (self.campus, campus):
            UserRole.objects.create(user=cao, role=role, tenant=self.tenant, campus=c)
        self.client.force_login(cao)
        result = self.post("options", campus_id=campus.id).json()
        self.assertEqual([r["value"] for r in result["options"]["department_id"]], [department.id])
        self.assertEqual([r["value"] for r in result["options"]["faculty_id"]], [faculty.id])
        permission = Permission.objects.get(code="grading_analytics.read")
        UserPermission.objects.create(user=cao, permission=permission, tenant=self.tenant, campus=campus, grant_type="DENY")
        self.assertEqual(self.post("report", campus_id=campus.id).status_code, 400)
        result = self.post("options", campus_id="all").json()
        self.assertNotIn(campus.id, [r["value"] for r in result["options"]["campus_id"]])

    def test_department_is_supervising_faculty_department_not_offering_owner(self):
        other = Department.objects.create(tenant=self.tenant, campus=self.campus, code="OWNER", name="Offering Owner")
        self.offering_a.department = other
        self.offering_a.save(update_fields=["department"])
        snapshot = self.snapshot(department_id=self.department.id)
        self.assertEqual(snapshot["summary"]["classes"], 2)

    def test_area_chair_cannot_expand_to_other_campus_or_tenant(self):
        campus, department, faculty, offering = self.add_campus()
        UserRole.objects.create(user=self.area_chair, role=self.area_role, tenant=self.tenant, campus=campus, department=department)
        self.assertEqual(self.post("report", campus_id=campus.id).status_code, 400)
        tenant = Tenant.objects.create(code="OTHER", name="Other")
        foreign = Campus.objects.create(tenant=tenant, code="FOREIGN", name="Foreign")
        self.assertEqual(self.post("report", campus_id=foreign.id).status_code, 400)

    def test_snapshot_rechecks_permissions_and_cannot_be_replayed_by_another_user(self):
        snapshot = self.snapshot()
        other = self._user("other-chair", self.campus, self.department)
        UserRole.objects.create(user=other, role=self.area_role, tenant=self.tenant, campus=self.campus, department=self.department)
        self.client.force_login(other)
        self.assertEqual(self.post("classes", token=snapshot["token"]).status_code, 409)
        self.client.force_login(self.area_chair)
        permission = Permission.objects.get(code="grading_analytics.read")
        UserPermission.objects.create(user=self.area_chair, permission=permission, tenant=self.tenant, campus=self.campus, grant_type="DENY")
        self.assertEqual(self.post("classes", token=snapshot["token"]).status_code, 403)

    def test_expired_snapshot_and_unselected_detail_are_rejected(self):
        self.assertEqual(self.post("classes", token="missing").status_code, 409)
        snapshot = self.snapshot()
        self.assertEqual(self.post("detail", token=snapshot["token"], offering_id=999999).status_code, 404)

    def test_recorded_activity_counts_are_distinct_and_quiz_average_uses_percentages(self):
        response = self.post("detail", token=self.snapshot()["token"], offering_id=self.offering_b.id)
        self.assertEqual(response.status_code, 200)
        html = response.json()["html"]
        self.assertIn("Quizzes: 1; Other Activities: 0", html)
        self.assertIn("Quiz Average: 89.00", html)
        self.assertIn("may already contribute", html)
        for name in ("Prelim", "Midterm", "Pre-Final", "Final"):
            self.assertIn(f"<td>{name}</td>", html)

    def test_created_without_scores_is_not_recorded_and_missing_is_not_zero(self):
        self._activity(self.offering_b, "Never scored", 100)
        metrics = self.metrics(self.offering_b)
        self.assertEqual(metrics["quizzes"], 1)
        StudentActivityScore.objects.filter(activity=self.activity_b).delete()
        metrics = self.metrics(self.offering_b)
        self.assertEqual(metrics["quizzes"], UNAVAILABLE)
        self.assertEqual(metrics["other"], UNAVAILABLE)

    def metrics(self, offering):
        ids = set(Enrollment.objects.filter(course_offering=offering).values_list("student_id", flat=True))
        return activity_metrics({offering.id: offering}, {offering.id: self.period.id}, {offering.id: ids}, {offering.id: ids})[offering.id]

    def test_other_activity_correct_period_and_class_matching(self):
        component = GradingTemplateComponent.objects.create(template_period=self.period, code="PROJECT", name="Project", weight_percentage=0)
        activity = self._activity(self.offering_b, "Other", 100)
        activity.template_component = component
        activity.save(update_fields=["template_component"])
        self._score(activity, self.student_b1, 90, 95)
        foreign_period = GradingTemplatePeriod.objects.create(template=self.template, code="MIDTERM", name="Midterm", sequence_no=2)
        extra = self._activity(self.offering_b, "Wrong period", 100)
        extra.template_period = foreign_period
        extra.save(update_fields=["template_period"])
        self._score(extra, self.student_b1, 90, 95)
        metrics = self.metrics(self.offering_b)
        self.assertEqual(metrics["quizzes"], 1)
        self.assertEqual(metrics["other"], 1)

    def test_excused_inactive_and_invalid_scores_do_not_create_quiz_averages(self):
        score = StudentActivityScore.objects.get(activity=self.activity_b, student=self.student_b1)
        score.is_excused = True
        score.save(update_fields=["is_excused"])
        self.assertEqual(self.metrics(self.offering_b)["quiz_average"], UNAVAILABLE)
        score.is_excused = False
        score.raw_score = 101
        score.save(update_fields=["is_excused", "raw_score"])
        self.assertEqual(self.metrics(self.offering_b)["quiz_average"], UNAVAILABLE)

    def test_activity_queries_are_two_bulk_reads_not_per_student(self):
        with CaptureQueriesContext(connection) as queries:
            ids = set(Enrollment.objects.filter(course_offering=self.offering_b).values_list("student_id", flat=True))
            activity_metrics({self.offering_b.id: self.offering_b}, {self.offering_b.id: self.period.id}, {self.offering_b.id: ids}, {self.offering_b.id: ids})
        self.assertEqual(len(queries), 3)  # one fixture cohort query plus two activity/score reads

    def test_summary_includes_tail_beyond_one_hundred_classes_and_pagination_reuses_snapshot(self):
        for index in range(103):
            offering = self._offering(f"EXTRA-{index:03}", self.campus, self.department, self.program)
            self._assign(offering, self.faculty)
            for enrollment in Enrollment.objects.filter(course_offering=self.offering_b):
                Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
                    term=self.term, course_offering=offering, student_id=enrollment.student_id)
        base = Official.get_section_performance_summary(self.offering_b, self.period)
        def fake(offering, period):
            section = deepcopy(base)
            section.update(offering=offering, period=period, section=offering.section.code)
            if offering.section.code == "EXTRA-102":
                section["class_average"] = section["coverage"]["average"] = Decimal("40")
                for row in section["coverage"]["rows"]:
                    row["current_grade"] = Decimal("40")
            return section
        with patch.object(Official, "get_section_performance_summary", side_effect=fake) as compute:
            snapshot = self.snapshot()
        self.assertEqual(compute.call_count, 105)
        self.assertEqual(snapshot["summary"]["classes"], 105)
        # A's fabricated cohort is not valid for that class; it is correctly withheld.
        self.assertEqual(snapshot["summary"]["disclosed"], 104)
        self.assertEqual(base["coverage"]["average"], Decimal("94.60"))
        self.assertEqual(snapshot["summary"]["average"], "94.08")
        with patch.object(Official, "get_section_performance_summary") as compute:
            response = self.post("classes", token=snapshot["token"], page=5)
        compute.assert_not_called()
        self.assertEqual(response.json()["pages"], 5)

    def test_csv_retains_schema_zero_and_formula_safety(self):
        self.faculty.last_name = "=Faculty"
        self.faculty.save(update_fields=["last_name"])
        self.component.score_input_mode = "DIRECT_PERCENTAGE"
        self.component.save(update_fields=["score_input_mode"])
        StudentActivityScore.objects.update(raw_score=0, computed_score=0)
        response = self.client.get(self.url, {**self.filters, "export": "sections"})
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0]["Average"], "0.00")
        self.assertEqual(rows[0]["Faculty"], "'=Faculty, faculty-one")
        self.assertIn("No Usable Grade", rows[0])
        self.assertEqual(rows[0]["Highest"], SUPPRESSED)

    def test_student_review_never_releases_individual_exact_grades(self):
        StudentActivityScore.objects.update(raw_score=0, computed_score=50)
        response = self.client.get(self.url, {**self.filters, "export": "students"})
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(row["Current Grade"] == SUPPRESSED for row in rows))
        self.assertNotIn(self.student_a1.student_no, response.content.decode())
        permission, _ = Permission.objects.get_or_create(code="gradebook.view_student_identity", defaults={"module": "gradebook", "action": "view_student_identity"})
        RolePermission.objects.get_or_create(role=self.area_role, permission=permission)
        response = self.client.get(self.url, {**self.filters, "export": "students"})
        self.assertIn(self.student_a1.student_no, response.content.decode())
        self.assertTrue(all(row["Current Grade"] == SUPPRESSED for row in csv.DictReader(io.StringIO(response.content.decode()))))

    def test_inconsistent_student_tenant_scope_withholds_the_class(self):
        other = Tenant.objects.create(code="OTHER", name="Other")
        self.student_a1.tenant = other
        self.student_a1.save(update_fields=["tenant"])
        with patch.object(Official, "get_section_performance_summary", wraps=Official.get_section_performance_summary) as compute:
            snapshot = self.snapshot()
        self.assertEqual(compute.call_count, 1)
        detail = self.post("detail", token=snapshot["token"], offering_id=self.offering_a.id).json()["html"]
        self.assertIn(SUPPRESSED, detail)
        self.assertNotIn("Average Grade:", detail)

    def test_complete_comparable_period_trends_and_incomplete_baseline(self):
        period = GradingTemplatePeriod.objects.create(template=self.template, code="MIDTERM", name="Midterm", sequence_no=2)
        component = GradingTemplateComponent.objects.create(template_period=period, code="QUIZZES", name="Quizzes", weight_percentage=100)
        activity = self._activity(self.offering_b, "Midterm Quiz", 100)
        activity.template_period, activity.template_component = period, component
        activity.save(update_fields=["template_period", "template_component"])
        for enrollment in Enrollment.objects.filter(course_offering=self.offering_b):
            self._score(activity, enrollment.student, 100, 100)
        snapshot = self.snapshot(period_code="MIDTERM")
        self.assertIn("1 improving", snapshot["summary"]["trend"])
        detail = self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id).json()["html"]
        self.assertIn("Improving", detail)
        self.assertIn("Midterm", detail)
        StudentActivityScore.objects.filter(activity=self.activity_b, student=self.student_b1).delete()
        snapshot = self.snapshot(period_code="MIDTERM")
        detail = self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id).json()["html"]
        self.assertNotIn(">Improving<", detail)

    def test_activity_link_has_supported_period_faculty_and_scope_filters(self):
        colleague = self._user("colleague", self.campus, self.department)
        UserRole.objects.create(user=colleague, role=self.faculty_role, tenant=self.tenant, campus=self.campus, department=self.department)
        self._assign(self.offering_a, colleague)
        snapshot = self.snapshot(faculty_id=self.faculty.id)
        html = self.post("detail", token=snapshot["token"], offering_id=self.offering_a.id).json()["html"]
        for value in (f"period_code=PRELIM", f"faculty_id={self.faculty.id}", f"campus_id={self.campus.id}"):
            self.assertIn(value, html)

    def test_historical_overlap_suppression_also_applies_to_trend_details(self):
        Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, course_offering=self.offering_b, student=self.student_a1)
        self._score(self.activity_b, self.student_a1, 80, 90)
        period = GradingTemplatePeriod.objects.create(template=self.template, code="MIDTERM", name="Midterm", sequence_no=2)
        component = GradingTemplateComponent.objects.create(template_period=period, code="QUIZZES", name="Quizzes", weight_percentage=100)
        activity = self._activity(self.offering_a, "Midterm Quiz", 100)
        activity.template_period, activity.template_component = period, component
        activity.save(update_fields=["template_period", "template_component"])
        for enrollment in Enrollment.objects.filter(course_offering=self.offering_a):
            self._score(activity, enrollment.student, 100, 100)
        snapshot = self.snapshot(period_code="MIDTERM")
        self.assertEqual(snapshot["summary"]["trend"], UNAVAILABLE)
        html = self.post("detail", token=snapshot["token"], offering_id=self.offering_a.id).json()["html"]
        self.assertIn("<td>Prelim</td><td>Not Available</td>", html)
        self.assertNotIn(">Improving<", html)

    def test_existing_activity_detail_return_navigation_is_preserved(self):
        activity_url = reverse("admin_portal:academic_activity_consistency") + "?period_code=PRELIM"
        response = self.client.get(reverse("admin_portal:academic_performance_section_detail", args=[self.offering_a.id, "PRELIM"]), {"next": activity_url})
        self.assertEqual(response.context["back_url"], activity_url)

    def test_csrf_is_required_for_ajax_reports(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.area_chair)
        self.assertEqual(client.post(self.url, {**self.filters, "action": "report"}).status_code, 403)

    def assert_changed(self, response):
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json(), {"error": CHANGED_MESSAGE, "code": "records_changed"})
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_saved_score_edit_cannot_mix_old_periodic_average_with_new_quiz_average(self):
        snapshot = self.snapshot()
        original = self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id).json()["html"]
        self.assertIn("Average Grade:</strong> 94.60", original)
        self.assertIn("Quiz Average: 89.00", original)
        before = StudentActivityScore.objects.get(activity=self.activity_b, student=self.student_b1).updated_at
        # Reproduce the review: a bulk save that does not advance updated_at.
        StudentActivityScore.objects.filter(activity=self.activity_b).update(raw_score=0, computed_score=0)
        self.assertEqual(StudentActivityScore.objects.get(activity=self.activity_b, student=self.student_b1).updated_at, before)
        for action in ("classes", "detail"):
            response = self.post(action, token=snapshot["token"], offering_id=self.offering_b.id)
            self.assert_changed(response)
            self.assertNotIn("html", response.json())
        refreshed = self.snapshot()
        detail = self.post("detail", token=refreshed["token"], offering_id=self.offering_b.id).json()["html"]
        self.assertIn("Quiz Average: 0.00", detail)
        self.assertNotIn("Average Grade:</strong> 94.60", detail)

    def test_raw_score_computed_score_and_score_deletion_each_invalidate_the_generation(self):
        score = StudentActivityScore.objects.get(activity=self.activity_b, student=self.student_b1)
        mutations = [lambda: StudentActivityScore.objects.filter(pk=score.pk).update(raw_score=1),
                     lambda: StudentActivityScore.objects.filter(pk=score.pk).update(computed_score=1),
                     lambda: StudentActivityScore.objects.filter(pk=score.pk).delete()]
        for mutate in mutations:
            snapshot = self.snapshot()
            mutate()
            self.assert_changed(self.post("classes", token=snapshot["token"]))

    def test_membership_status_deletion_and_new_enrollment_invalidate_the_generation(self):
        enrollment = Enrollment.objects.get(course_offering=self.offering_b, student=self.student_b1)
        for change in ({"enrollment_status": "DRP"}, {"is_active": False}):
            snapshot = self.snapshot()
            Enrollment.objects.filter(pk=enrollment.pk).update(**change)
            self.assert_changed(self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id))
            Enrollment.objects.filter(pk=enrollment.pk).update(is_active=True, enrollment_status="ACTIVE")
        snapshot = self.snapshot()
        Enrollment.objects.filter(pk=enrollment.pk).delete()
        self.assert_changed(self.post("classes", token=snapshot["token"]))
        snapshot = self.snapshot()
        Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, course_offering=self.offering_b, student=self.student_a1)
        self.assert_changed(self.post("classes", token=snapshot["token"]))

    def test_recorded_activity_and_persisted_grade_changes_invalidate_the_generation(self):
        snapshot = self.snapshot()
        self._activity(self.offering_b, "Created after report", 100)
        self.assert_changed(self.post("classes", token=snapshot["token"]))
        snapshot = self.snapshot()
        StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_b,
            template_period=self.period, student=self.student_b1, period_grade=80)
        self.assert_changed(self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id))

    def test_attendance_records_in_the_grade_basis_invalidate_the_generation(self):
        session = AttendanceSession.objects.create(tenant=self.tenant, campus=self.campus,
            offering=self.offering_b, template_period=self.period, session_date="2026-10-17")
        snapshot = self.snapshot()
        AttendanceRecord.objects.create(session=session, tenant=self.tenant, campus=self.campus,
            student=self.student_b1, status_code="A")
        self.assert_changed(self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id))

    def test_historical_and_grading_configuration_changes_invalidate_the_generation(self):
        period = GradingTemplatePeriod.objects.create(template=self.template, code="MIDTERM", name="Midterm", sequence_no=2)
        snapshot = self.snapshot(period_code=period.code)
        StudentActivityScore.objects.filter(activity=self.activity_b).update(raw_score=0)
        self.assert_changed(self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id))
        snapshot = self.snapshot()
        GradingTemplateComponent.objects.filter(pk=self.component.pk).update(weight_percentage=90)
        self.assert_changed(self.post("classes", token=snapshot["token"]))

    def test_changes_during_report_generation_do_not_publish_a_token_or_summary(self):
        original = insights_views.report
        def changing(*args, **kwargs):
            value = original(*args, **kwargs)
            StudentActivityScore.objects.filter(activity=self.activity_b).update(computed_score=0)
            return value
        with patch.object(insights_views, "report", side_effect=changing):
            response = self.post("report")
        self.assert_changed(response)
        self.assertNotIn("token", response.json())

    def test_changes_during_lazy_metrics_or_history_are_rejected_after_the_read(self):
        for action, method in (("classes", "metrics_for"), ("detail", "period_trends")):
            snapshot = self.snapshot()
            original = getattr(insights_views, method)
            def changing(*args, **kwargs):
                value = original(*args, **kwargs)
                StudentActivityScore.objects.filter(activity=self.activity_b).update(raw_score=Decimal("1") if action == "classes" else Decimal("2"))
                return value
            with patch.object(insights_views, method, side_effect=changing):
                response = self.post(action, token=snapshot["token"], offering_id=self.offering_b.id)
            self.assert_changed(response)
            self.assertNotIn("html", response.json())

    def test_independent_worker_cache_miss_rebuilds_only_an_unchanged_generation(self):
        worker_a = LocMemCache("academic-insights-test-worker-a", {})
        worker_b = LocMemCache("academic-insights-test-worker-b", {})
        worker_a.clear(); worker_b.clear()
        with patch.object(insights_views, "cache", worker_a):
            snapshot = self.snapshot()
        with patch.object(insights_views, "cache", worker_b):
            response = self.post("classes", token=snapshot["token"])
            self.assertEqual(response.status_code, 200)
            self.assertIn("94.60", response.json()["html"])
            detail = self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id)
            self.assertIn("Quiz Average: 89.00", detail.json()["html"])
            worker_b.clear()
            StudentActivityScore.objects.filter(activity=self.activity_b).update(raw_score=0)
            self.assert_changed(self.post("classes", token=snapshot["token"]))

    def test_signed_state_contains_no_academic_values_and_expiry_is_not_cache_dependent(self):
        snapshot = self.snapshot()
        state = signing.loads(snapshot["token"], salt=insights_views.TOKEN_SALT)
        self.assertEqual(set(state), {"nonce", "scope", "generation", "filters"})
        self.assertEqual(self.post("classes", token=snapshot["token"] + "tampered").status_code, 409)
        import time
        future = time.time() + 301
        cache.clear()
        with patch("django.core.signing.time.time", return_value=future):
            self.assertEqual(self.post("classes", token=snapshot["token"]).status_code, 409)

    def test_cached_calculations_and_rendered_details_are_reused_only_after_generation_checks(self):
        snapshot = self.snapshot()
        with patch.object(Official, "get_section_performance_summary", wraps=Official.get_section_performance_summary) as compute:
            with CaptureQueriesContext(connection) as first:
                response = self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id)
            self.assertEqual(response.status_code, 200)
            compute.assert_not_called()
            with patch.object(insights_views, "metrics_for") as metrics:
                with CaptureQueriesContext(connection) as repeated:
                    again = self.post("detail", token=snapshot["token"], offering_id=self.offering_b.id)
                metrics.assert_not_called()
                compute.assert_not_called()
        self.assertEqual(response.json(), again.json())
        print(f"Insights guarded detail queries: first={len(first)}, repeated={len(repeated)}; gradebook recomputations=0")

    def test_csv_export_bound_to_displayed_report_rejects_score_changes(self):
        snapshot = self.snapshot()
        response = self.client.get(self.url, {**self.filters, "export": "sections", "token": snapshot["token"]})
        self.assertEqual(response.status_code, 200)
        StudentActivityScore.objects.filter(activity=self.activity_b).update(raw_score=0)
        self.assert_changed(self.client.get(self.url, {**self.filters, "export": "sections", "token": snapshot["token"]}))

    def test_contextual_activity_link_sets_supported_topbar_scope_for_selected_campus(self):
        campus, department, faculty, offering = self.add_campus()
        cao = self._user("link-cao", self.campus, self.department)
        role = self._role_with_access("CAO", "CAO")
        for c in (self.campus, campus):
            UserRole.objects.create(user=cao, role=role, tenant=self.tenant, campus=c)
        self.client.force_login(cao)
        snapshot = self.snapshot(campus_id=campus.id)
        detail = self.post("detail", token=snapshot["token"], offering_id=offering.id).json()["html"]
        import html, re
        url = html.unescape(re.search(r'href="([^"]+)"', detail).group(1))
        self.assertIn(f"scope_campus_id={campus.id}", url)
        self.assertIn(f"scope_tenant_id={self.tenant.id}", url)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["request"].scope["campus_id"], campus.id)

    def grant_gradebook_monitor(self, user=None, campus=None):
        permission, _ = Permission.objects.get_or_create(code="faculty_gradebook_monitor.read",
            defaults={"module": "faculty_gradebook_monitor", "action": "read"})
        return UserPermission.objects.create(user=user or self.area_chair, permission=permission,
            tenant=self.tenant, campus=campus or self.campus, grant_type="ALLOW")

    def small_classes(self):
        Enrollment.objects.filter(student__student_no__startswith="EXTRA").update(is_active=False)

    def test_cao_dean_area_chair_and_superadmin_see_authorized_small_aggregates(self):
        self.small_classes()
        for code in ("CAO", "DEAN", "AREA_CHAIR", "SUPER_ADMIN", "CUSTOM_ACADEMIC_ADMIN"):
            with self.subTest(role=code):
                leader = self._user("leader-" + code.lower(), self.campus, self.department)
                role = self._role_with_access(code, code)
                UserRole.objects.create(user=leader, role=role, tenant=self.tenant,
                    campus=self.campus, department=self.department)
                self.grant_gradebook_monitor(leader)
                self.client.force_login(leader)
                snapshot = self.snapshot(campus_id=self.campus.id)
                self.assertEqual(snapshot["summary"]["suppressed"], 0)
                self.assertEqual(snapshot["summary"]["average"], "82.00")
                self.assertEqual(snapshot["summary"]["below"], 1)
                self.assertEqual(snapshot["summary"]["attention"], 1)
                self.assertNotIn("withheld", snapshot["html"])
                html = self.post("detail", token=snapshot["token"], offering_id=self.offering_a.id).json()["html"]
                for value in ("Average Grade:</strong> 70.00", "Students Below Passing:</strong> 1 (50.0%)", "Quiz Average: 40.00"):
                    self.assertIn(value, html)
                self.assertNotIn(SUPPRESSED, html)
                self.assertNotIn(self.student_a1.student_no, html)
                rows = list(csv.DictReader(io.StringIO(self.client.get(self.url,
                    {**self.filters, "campus_id": self.campus.id, "export": "sections"}).content.decode())))
                self.assertEqual(next(row for row in rows if row["Section"] == self.offering_a.section.code)["Average"], "70.00")
                review = self.client.get(self.url, {**self.filters, "campus_id": self.campus.id, "export": "students"})
                self.assertEqual(list(csv.DictReader(io.StringIO(review.content.decode()))), [])

    def test_superuser_exception_respects_explicit_deny(self):
        self.small_classes()
        self.area_chair.is_superuser = True
        self.area_chair.save(update_fields=["is_superuser"])
        self.client.force_login(self.area_chair)
        self.assertEqual(self.snapshot()["summary"]["below"], 1)
        grant = self.grant_gradebook_monitor()
        grant.grant_type = "DENY"
        grant.save(update_fields=["grant_type"])
        self.assertEqual(self.snapshot()["summary"]["suppressed"], 2)
        permission = Permission.objects.get(code="grading_analytics.read")
        UserPermission.objects.create(user=self.area_chair, permission=permission, tenant=self.tenant,
            campus=self.campus, grant_type="DENY")
        response = self.post("report")
        self.assertIn(response.status_code, (400, 403))

    def test_role_name_without_gradebook_authority_keeps_small_class_protection(self):
        self.small_classes()
        self.assertEqual(self.snapshot()["summary"]["suppressed"], 2)
        grant = self.grant_gradebook_monitor()
        snapshot = self.snapshot()
        grant.grant_type = "DENY"
        grant.save(update_fields=["grant_type"])
        self.assertEqual(self.post("classes", token=snapshot["token"]).status_code, 409)
        self.assertEqual(self.snapshot()["summary"]["suppressed"], 2)

    def test_leadership_permission_does_not_expand_supervision_or_record_scope(self):
        self.grant_gradebook_monitor()
        self.small_classes()
        campus, department, faculty, offering = self.add_campus()
        self.assertEqual(self.post("report", campus_id=campus.id).status_code, 400)
        self.assertEqual(self.post("options").json()["options"]["faculty_id"],
            [{"value": self.faculty.id, "label": "User, faculty-one"}])
        self.assertEqual(self.client.get(reverse("admin_portal:academic_performance_section_detail",
            args=[offering.id, "PRELIM"])).status_code, 404)
        other = Tenant.objects.create(code="INVALID", name="Invalid")
        self.student_a1.tenant = other
        self.student_a1.save(update_fields=["tenant"])
        self.assertEqual(self.snapshot()["summary"]["suppressed"], 1)

    def test_faculty_last_name_sort_and_stored_department_names(self):
        self.faculty.first_name, self.faculty.middle_name, self.faculty.last_name = "Zoe", "M.", "Abad"
        self.faculty.save(update_fields=["first_name", "middle_name", "last_name"])
        colleague = self._user("colleague-z", self.campus, self.department)
        colleague.first_name, colleague.last_name = "Ana", "Zamora"
        colleague.save(update_fields=["first_name", "last_name"])
        UserRole.objects.create(user=colleague, role=self.faculty_role, tenant=self.tenant,
            campus=self.campus, department=self.department)
        self._assign(self.offering_a, colleague)
        result = self.post("options", campus_id=self.campus.id).json()
        self.assertEqual(result["options"]["faculty_id"], [
            {"value": self.faculty.id, "label": "Abad, Zoe M."},
            {"value": colleague.id, "label": "Zamora, Ana"}])
        self.assertEqual(result["options"]["department_id"],
            [{"value": self.department.id, "label": "Information Systems"}])

    def test_department_faculty_filter_does_not_include_coteachers_from_other_departments(self):
        UserRole.objects.filter(user=self.area_chair).update(department=None)
        department = Department.objects.create(tenant=self.tenant, campus=self.campus,
            code="OTHER", name="Other Academic Department")
        colleague = self._user("other-department-faculty", self.campus, department)
        UserRole.objects.create(user=colleague, role=self.faculty_role, tenant=self.tenant,
            campus=self.campus, department=department)
        self._assign(self.offering_a, colleague)
        result = self.post("options", department_id=self.department.id).json()
        self.assertEqual([r["value"] for r in result["options"]["faculty_id"]], [self.faculty.id])
        self.assertIsNone(self.post("options", department_id=self.department.id,
            faculty_id=colleague.id).json()["filters"]["faculty_id"])

    def test_courses_and_faculty_follow_actual_campus_year_term_and_assignments(self):
        campus, department, faculty, offering = self.add_campus()
        course = Course.objects.create(tenant=self.tenant, code="CUB-ONLY", title="Cubao Course")
        offering.course = course
        offering.save(update_fields=["course"])
        cao = self._user("selection-cao", self.campus, self.department)
        role = self._role_with_access("CAO", "CAO")
        for c in (self.campus, campus):
            UserRole.objects.create(user=cao, role=role, tenant=self.tenant, campus=c)
        # Supervision on both campuses does not mean this faculty teaches on both.
        UserRole.objects.create(user=faculty, role=self.faculty_role, tenant=self.tenant,
            campus=self.campus, department=self.department)
        self.client.force_login(cao)
        fairview = self.post("options", campus_id=self.campus.id).json()["options"]
        self.assertEqual([r["value"] for r in fairview["faculty_id"]], [self.faculty.id])
        self.assertEqual([r["value"] for r in fairview["course_code"]], [self.course.code])
        cubao = self.post("options", campus_id=campus.id).json()["options"]
        self.assertEqual([r["value"] for r in cubao["faculty_id"]], [faculty.id])
        self.assertEqual([r["value"] for r in cubao["course_code"]], [course.code])
        from apps.academics.models import Term
        term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year,
            code="2ND", name="Second Semester", sequence_no=2,
            start_date=self.term.start_date, end_date=self.term.end_date)
        offering.term = term
        offering.save(update_fields=["term"])
        self.assertEqual(self.post("options", campus_id=campus.id).json()["options"]["faculty_id"], [])

    def test_closed_and_archived_classes_require_saved_gradebook_evidence(self):
        self.offering_a.status, self.offering_b.status = "CLOSED", "ARCHIVED"
        self.offering_a.save(update_fields=["status"])
        self.offering_b.save(update_fields=["status"])
        self.assertEqual(self.snapshot()["summary"]["classes"], 2)
        StudentActivityScore.objects.filter(activity=self.activity_a).delete()
        self.assertEqual(self.snapshot()["summary"]["classes"], 1)
        StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            student=self.student_a1, template_period=self.period, period_grade=80)
        self.assertEqual(self.snapshot()["summary"]["classes"], 2)
        FacultyAssignment.objects.filter(offering=self.offering_a).update(is_active=False)
        self.assertEqual(self.snapshot()["summary"]["classes"], 1)

    def test_small_class_trends_use_authoritative_grades_and_complete_matching_cohorts(self):
        self.grant_gradebook_monitor()
        self.small_classes()
        period = GradingTemplatePeriod.objects.create(template=self.template, code="MIDTERM", name="Midterm", sequence_no=2)
        component = GradingTemplateComponent.objects.create(template_period=period,
            code="QUIZZES", name="Quizzes", weight_percentage=100)
        activity = self._activity(self.offering_a, "Midterm Quiz", 100)
        activity.template_period, activity.template_component = period, component
        activity.save(update_fields=["template_period", "template_component"])
        for student in (self.student_a1, self.student_a2):
            self._score(activity, student, 100, 100)
        snapshot = self.snapshot(period_code="MIDTERM")
        self.assertIn("1 improving", snapshot["summary"]["trend"])
        detail = self.post("detail", token=snapshot["token"], offering_id=self.offering_a.id).json()["html"]
        self.assertIn("<td>Prelim</td><td>70.00</td>", detail)
        self.assertIn("<td>Midterm</td><td>100.00</td>", detail)
        StudentActivityScore.objects.filter(activity=self.activity_a, student=self.student_a1).delete()
        self.assertEqual(self.snapshot(period_code="MIDTERM")["summary"]["trend"], UNAVAILABLE)

    def test_incompatible_configurations_display_separate_compatible_averages(self):
        original = Official.get_section_performance_summary
        def incompatible(offering, period):
            section = original(offering, period)
            if offering.id == self.offering_b.id:
                section["grading_context"]["signature"] = ("different legitimate configuration",)
            return section
        with patch.object(Official, "get_section_performance_summary", side_effect=incompatible):
            snapshot = self.snapshot()
        self.assertEqual(snapshot["summary"]["average"], UNAVAILABLE)
        self.assertEqual([r["average"] for r in snapshot["summary"]["averages"]], ["85.00", "94.60"])
        self.assertIn("Averages by Compatible Course Group", snapshot["html"])

    def test_leadership_overlap_counts_distinct_students_and_does_not_suppress_outcomes(self):
        self.small_classes()
        self.grant_gradebook_monitor()
        Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, course_offering=self.offering_b, student=self.student_a2)
        self._score(self.activity_b, self.student_a2, 0, 50)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["summary"]["below"], 1)
        self.assertEqual(snapshot["summary"]["attention"], 2)
        self.assertEqual(snapshot["summary"]["disclosed"], 2)
        self.assertEqual(self.snapshot(faculty_id=self.faculty.id)["summary"]["below"], 1)

    def test_leadership_empty_grades_do_not_invent_averages_or_trends(self):
        self.grant_gradebook_monitor()
        StudentActivityScore.objects.all().delete()
        snapshot = self.snapshot()
        self.assertEqual(snapshot["summary"]["average"], UNAVAILABLE)
        self.assertEqual(snapshot["summary"]["trend"], UNAVAILABLE)
        detail = self.post("detail", token=snapshot["token"], offering_id=self.offering_a.id).json()["html"]
        self.assertIn("Average Grade:</strong> Not Available", detail)
        self.assertNotIn("Average Grade:</strong> None", detail)

    def test_leadership_aggregate_permission_preserves_separate_student_export_masking(self):
        self.grant_gradebook_monitor()
        StudentActivityScore.objects.update(raw_score=0, computed_score=50)
        export = self.client.get(self.url, {**self.filters, "export": "students"})
        rows = list(csv.DictReader(io.StringIO(export.content.decode())))
        self.assertEqual(len(rows), 10)
        self.assertTrue(all(r["Current Grade"] == SUPPRESSED for r in rows))
        self.assertNotIn(self.student_a1.student_no, export.content.decode())
        permission, _ = Permission.objects.get_or_create(code="gradebook.view_student_identity",
            defaults={"module": "gradebook", "action": "view_student_identity"})
        UserPermission.objects.create(user=self.area_chair, permission=permission,
            tenant=self.tenant, campus=self.campus, grant_type="ALLOW")
        export = self.client.get(self.url, {**self.filters, "export": "students"})
        self.assertIn(self.student_a1.student_no, export.content.decode())
        self.assertTrue(all(r["Current Grade"] == SUPPRESSED for r in csv.DictReader(io.StringIO(export.content.decode()))))

    def test_aggregate_exception_is_specific_to_the_effectively_authorized_campus(self):
        self.small_classes()
        campus, department, faculty, offering = self.add_campus()
        student = self._student(offering, "CUB-SMALL", "Campus Student")
        activity = self._activity(offering, "CUB Quiz", 100)
        self._score(activity, student, 80, 90)
        cao = self._user("campus-authority-cao", self.campus, self.department)
        role = self._role_with_access("CAO", "CAO")
        for c in (self.campus, campus):
            UserRole.objects.create(user=cao, role=role, tenant=self.tenant, campus=c)
        self.grant_gradebook_monitor(cao, self.campus)
        self.client.force_login(cao)
        self.assertEqual(self.snapshot(campus_id=self.campus.id)["summary"]["disclosed"], 2)
        snapshot = self.snapshot(campus_id=campus.id)
        self.assertEqual(snapshot["summary"]["suppressed"], 1)
        self.assertEqual(snapshot["summary"]["average"], UNAVAILABLE)
        permission = Permission.objects.get(code="faculty_gradebook_monitor.read")
        RolePermission.objects.get_or_create(role=role, permission=permission)
        self.assertEqual(self.snapshot(campus_id=campus.id)["summary"]["average"], "90.00")

    def test_course_catalogue_also_narrows_to_selected_faculty_without_duplicates(self):
        second_course = Course.objects.create(tenant=self.tenant, code="OTHER-COURSE", title="Other Course")
        self.offering_b.course = second_course
        self.offering_b.save(update_fields=["course"])
        colleague = self._user("separate-class-faculty", self.campus, self.department)
        UserRole.objects.create(user=colleague, role=self.faculty_role, tenant=self.tenant,
            campus=self.campus, department=self.department)
        FacultyAssignment.objects.filter(offering=self.offering_b).update(faculty_user=colleague)
        options = self.post("options", faculty_id=colleague.id).json()["options"]
        self.assertEqual(options["course_code"], [{"value": second_course.code,
            "label": "OTHER-COURSE - Other Course"}])

    def test_gradebook_authority_without_analytics_permission_does_not_grant_insights(self):
        self.grant_gradebook_monitor()
        permission = Permission.objects.get(code="grading_analytics.read")
        UserPermission.objects.create(user=self.area_chair, permission=permission,
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        for action in ("options", "report"):
            self.assertEqual(self.post(action).status_code, 403)
        self.assertEqual(self.client.get(self.url, {**self.filters, "export": "sections"}).status_code, 403)

    def test_global_direct_deny_overrides_leadership_grant(self):
        self.grant_gradebook_monitor()
        self.small_classes()
        self.assertEqual(self.snapshot()["summary"]["suppressed"], 0)
        permission = Permission.objects.get(code="faculty_gradebook_monitor.read")
        UserPermission.objects.create(user=self.area_chair, permission=permission,
            tenant=None, campus=None, grant_type="DENY")
        self.assertEqual(self.snapshot()["summary"]["suppressed"], 2)
