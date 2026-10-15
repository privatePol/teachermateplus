"""Repeatable disposable HTTP benchmarks; fixture setup is outside measurement."""
import json
import time
import unittest
from decimal import Decimal
from django.db import connection
from django.test.utils import CaptureQueriesContext
from apps.admin_portal import tests_grade_distribution_monitor as fixtures
from apps.admin_portal.grade_distribution_data import MonitorReadBundle, configuration_service
from apps.admin_portal.grade_distribution import GradeDistributionMonitorService
from apps.grading.services import FacultyGradingService
from apps.grading.models import (GradingTemplate, CourseTemplateAssignment, CourseBaseValueOverride, TenantGradingProfile,
    StudentPeriodGrade, GradeActivity, StudentActivityScore, GradingTemplateComponent)
from apps.academics.models import CourseOffering, Course
from apps.students.models import Student
from apps.enrollment.models import Enrollment
from apps.tenants.models import Tenant, Campus, Department


def load_tests(loader, tests, pattern):
    # Reuse fixture helpers without discovering the inherited suite twice.
    return unittest.TestSuite(loader.loadTestsFromName(name, GradeDistributionPerformanceTests)
        for name in GradeDistributionPerformanceTests.__dict__ if name.startswith("test_"))


class GradeDistributionPerformanceTests(fixtures.GradeDistributionMonitorTests):
    def _profile_type_parity(self, offering):
        offering = CourseOffering.objects.select_related("course", "term", "section").get(pk=offering.pk)
        expected = FacultyGradingService.resolve_grading_profile_for_offering(offering)
        service = configuration_service({offering.id: offering})
        with self.assertNumQueries(0):
            actual = service.resolve_grading_profile_for_offering(offering)
            self.assertEqual(service.resolve_grading_profile_for_offering(offering), actual)
        self.assertEqual(actual, expected)
        return actual

    def test_ascii_profile_type_iexact_specific_and_generic_fallback(self):
        generic = TenantGradingProfile.objects.create(tenant=self.tenant, grading_template=self.template,
            profile_code="GENERIC", profile_name="Generic", priority=1)
        specific = TenantGradingProfile.objects.create(tenant=self.tenant, grading_template=self.template,
            profile_code="ASCII", profile_name="ASCII", course_type="LECTURE", priority=99)
        for course_type, expected in (("lecture", specific), ("LECTURE", specific), ("TUTORIAL", generic)):
            with self.subTest(course_type=course_type):
                Course.objects.filter(pk=self.course.pk).update(course_type=course_type)
                self.assertEqual(self._profile_type_parity(self.offering), expected)

    def test_unicode_profile_type_uses_backend_iexact_and_generic_fallback(self):
        generic = TenantGradingProfile.objects.create(tenant=self.tenant, grading_template=self.template,
            profile_code="GENERIC", profile_name="Generic", priority=1)
        specific = TenantGradingProfile.objects.create(tenant=self.tenant, grading_template=self.template,
            profile_code="UNICODE", profile_name="Unicode", course_type="\u00c9", priority=99)
        for course_type in ("\u00e9", "\u00c9"):
            with self.subTest(course_type=course_type, backend=connection.vendor):
                Course.objects.filter(pk=self.course.pk).update(course_type=course_type)
                # Expected membership comes from this backend's lookup, not a
                # Python Unicode case mapping or an assumed database collation.
                matches = TenantGradingProfile.objects.filter(pk=specific.pk, course_type__iexact=course_type).exists()
                self.assertEqual(self._profile_type_parity(self.offering), specific if matches else generic)

    def test_profile_type_multiple_candidates_keep_scope_and_precedence(self):
        for code, course_type, priority, scope in (
            ("GENERIC", None, 1, {}),
            ("UPPER", "\u00c9", 5, {}),
            ("LOWER", "\u00e9", 10, {}),
            ("PROGRAM", "\u00e9", 99, {"program": self.program}),
            ("COURSE", "\u00c9", 99, {"course": self.course}),
            ("EMPTY", "", 1, {}),
        ):
            TenantGradingProfile.objects.create(tenant=self.tenant, grading_template=self.template,
                profile_code=code, profile_name=code, course_type=course_type, priority=priority, **scope)
        for course_type in ("\u00e9", "\u00c9", "", "  ", "lecture"):
            with self.subTest(course_type=course_type):
                Course.objects.filter(pk=self.course.pk).update(course_type=course_type)
                self._profile_type_parity(self.offering)

    def test_mixed_profile_types_are_batched_without_per_class_queries(self):
        TenantGradingProfile.objects.create(tenant=self.tenant, grading_template=self.template,
            profile_code="GENERIC", profile_name="Generic")
        for i, course_type in enumerate(("LECTURE", "\u00c9", "\u00e9", "LAB", "")):
            TenantGradingProfile.objects.create(tenant=self.tenant, grading_template=self.template,
                profile_code=f"TYPE{i}", profile_name=f"Type {i}", course_type=course_type)
        counts = []
        for size in (27, 54):
            for i in range(0 if size == 27 else 26, size - 1):
                offering = self._create_extra_offering(f"TYPEBATCH{i}", "Type batching", f"TB{i}")
                Course.objects.filter(pk=offering.course_id).update(course_type=("lecture", "\u00c9", "\u00e9", "LAB", "", f"UNIQUE{i}")[i % 6])
            offerings = {o.pk: o for o in CourseOffering.objects.select_related("course", "term", "section").filter(tenant=self.tenant)}
            expected = {oid: FacultyGradingService.resolve_grading_profile_for_offering(o) for oid, o in offerings.items()}
            with CaptureQueriesContext(connection) as queries:
                service = configuration_service(offerings)
                actual = {oid: service.resolve_grading_profile_for_offering(o) for oid, o in offerings.items()}
            self.assertEqual(actual, expected)
            counts.append(len(queries))
        self.assertEqual(counts, [5, 5])
        print(f"PROFILE_TYPE_BATCH_QUERIES: 27={counts[0]}, 54={counts[1]}")

    def test_courses_are_deduplicated_authorized_signed_and_exported(self):
        other = self._create_extra_offering("SEC101", "Second course", "SEC")
        self._seed_grades(other, [99] * 10)
        self.client.force_login(self.admin_user)
        response = self.search(course_id=[str(other.course_id), str(self.course.id), str(other.course_id)])
        self.assertEqual(response.context["selected"]["course_id"], sorted([str(self.course.id), str(other.course_id)]))
        self.assertEqual(response.context["result_count"], 2)
        selected = self.search(course_id=[str(other.course_id)])
        self.assertEqual([r["offering_id"] for r in selected.context["rows"]], [other.id])
        exported = self._workbook(self.client.get(self.url + "?" + selected.context["export_query"]))
        self.assertEqual(exported.active.max_column, 11)
        self.assertEqual(exported.active.max_row, 4)
        self.assertTrue(self.search(course_id=["nonsense"]).context["errors"])
        foreign_tenant = Tenant.objects.create(code="OUT", name="Outside")
        foreign_campus = Campus.objects.create(tenant=foreign_tenant, code="OUT", name="Outside")
        foreign_dept = Department.objects.create(tenant=foreign_tenant, campus=foreign_campus, code="OUT", name="Outside")
        foreign = Course.objects.create(tenant=foreign_tenant, campus=foreign_campus, department=foreign_dept, code="SECRET", title="Outside")
        options = self.client.get(self.url, {"options": "1"}).json()["courses"]
        self.assertNotIn(str(foreign.id), [c["id"] for c in options])
        self.assertTrue(self.search(course_id=[str(foreign.id)]).context["errors"])
        self.assertEqual(self.client.get(self.url + "?" + selected.context["export_query"] + f"&course_id={self.course.id}").status_code, 400)

    def test_six_sorts_use_disclosed_numeric_counts_and_stable_ties(self):
        base = dict(faculty_name="Faculty", campus="FV", course_code="C", section="S", physical_period_id=1)
        for grade in range(100, 94, -1):
            rows = [dict(base, offering_id=i, analytics_state=state, exact_100_count=count,
                high_grade_breakdown=[dict(grade=f"Exactly {grade}", count=count)])
                for i, state, count in [(3, "privacy_suppressed", 900), (2, "disclosed", 2), (1, "disclosed", 10)]]
            self.assertEqual([r["offering_id"] for r in sorted(rows, key=lambda r: GradeDistributionMonitorService.sort_key(r, f"exact_{grade}_desc"))], [1, 2, 3])
            rows[0]["high_grade_breakdown"][0]["count"] = -999
            self.assertEqual(GradeDistributionMonitorService.sort_key(rows[0], f"exact_{grade}_desc")[1], 0)
        self.client.force_login(self.admin_user)
        for grade in range(100, 94, -1):
            response = self.search(sort=f"exact_{grade}_desc")
            self.assertFalse(response.context["errors"])
            self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 200)

    def test_only_100_membership_detail_and_export_respect_formula_suppression(self):
        self.client.force_login(self.admin_user)
        StudentPeriodGrade.objects.filter(offering=self.offering).update(period_grade=100)
        other = self._create_extra_offering("SMALL", "Small class", "SM")
        self._seed_grades(other, [100] * 4)
        none = self._create_extra_offering("NONE", "No perfect grades", "NO")
        self._seed_grades(none, [99] * 10)
        response = self.search(only_100="1")
        self.assertEqual(response.context["result_count"], 1)
        self.assertEqual([r["offering_id"] for r in response.context["rows"]], [self.offering.id])
        self.assertEqual(self._workbook(self.client.get(self.url + "?" + response.context["export_query"])).active.max_row, 4)
        self.assertEqual(self.detail(response).status_code, 200)
        from django.urls import reverse
        hidden_url = reverse("admin_portal:grade_distribution_monitor_detail", args=[other.id, self.period.id])
        self.assertEqual(self.client.get(hidden_url + "?" + response.context["page_query"]).status_code, 404)
        self._formula_fixture()
        suppressed = self.search(only_100="1")
        self.assertEqual(suppressed.context["result_count"], 0)
        self.assertEqual(self._workbook(self.client.get(self.url + "?" + suppressed.context["export_query"])).active.max_row, 1)

    def test_configuration_precedence_matches_authoritative_service(self):
        def compare():
            offering = CourseOffering.objects.select_related("course", "term", "section").get(pk=self.offering.pk)
            service = configuration_service({offering.id: offering})
            self.assertEqual(service.resolve_grading_profile_for_offering(offering), FacultyGradingService.resolve_grading_profile_for_offering(offering))
            self.assertEqual(service.resolve_template_for_offering(offering), FacultyGradingService.resolve_template_for_offering(offering))
            self.assertEqual(service.resolve_base_value(offering, self.template), FacultyGradingService.resolve_base_value(offering, self.template))
            self.assertEqual(service.resolve_period_grade_strategy(offering), FacultyGradingService.resolve_period_grade_strategy(offering))
            self.assertEqual(service.resolve_passing_threshold_trace(offering), FacultyGradingService.resolve_passing_threshold_trace(offering))
            with CaptureQueriesContext(connection) as queries:
                for _ in range(3):
                    service.resolve_template_for_offering(offering)
                    service.resolve_base_value(offering, self.template)
                    service.resolve_period_grade_strategy(offering)
                    service.resolve_passing_threshold_trace(offering)
            self.assertEqual(len(queries), 0)
        compare()
        alternate = GradingTemplate.objects.create(tenant=self.tenant, code="ALTERNATE", name="Alternate", is_published=True,
            passing_grade_threshold=65, default_base_value=45)
        CourseTemplateAssignment.objects.create(course=self.course, grading_template=alternate)
        compare()
        default = TenantGradingProfile.objects.create(tenant=self.tenant, grading_template=self.template, profile_code="DEFAULT", profile_name="Default",
            default_base_value=40, passing_grade_threshold=70, priority=1)
        compare()

        specific = TenantGradingProfile.objects.create(tenant=self.tenant, campus=self.campus, department=self.department,
            program=self.program, course=self.course, effective_from_term=self.term, grading_template=self.template, profile_code="SPECIFIC", profile_name="Specific",
            default_base_value=60, passing_grade_threshold=80, priority=99)
        compare()
        CourseTemplateAssignment.objects.create(course=self.course, grading_template=self.template)
        CourseTemplateAssignment.objects.create(course=self.course, grading_template=self.template, effective_from_term=self.term)
        CourseBaseValueOverride.objects.create(course=self.course, base_value=20)
        CourseBaseValueOverride.objects.create(course=self.course, base_value=30, effective_from_term=self.term)
        compare()
        specific.is_active = False
        specific.save(update_fields=["is_active"])
        default.passing_grade_threshold = None
        default.save(update_fields=["passing_grade_threshold"])
        compare()

    def test_new_filters_keep_direct_deny_precedence(self):
        self.client.force_login(self.admin_user)
        response = self.search(course_id=[str(self.course.id)], sort="exact_99_desc", only_100="1")
        permission = fixtures.Permission.objects.get(code="grade_distribution_monitor.read")
        fixtures.UserPermission.objects.create(user=self.admin_user, permission=permission, tenant=self.tenant,
            campus=self.campus, grant_type="DENY")
        self.assertEqual(self.search(course_id=[str(self.course.id)], sort="exact_99_desc", only_100="1").status_code, 403)
        self.assertEqual(self.client.get(self.url + "?" + response.context["export_query"]).status_code, 403)

    def test_filtered_pagination_and_export_share_the_complete_ranked_membership(self):
        selected = []
        for i in range(27):
            offering = self._create_extra_offering(f"FILTER{i:02}", "Filtered", f"F{i:02}")
            self._seed_grades(offering, [100, 99, 99] + [90] * 7)
            selected.append(str(offering.course_id))
        self.client.force_login(self.admin_user)
        response = self.search(course_id=selected, only_100="1", sort="exact_99_desc")
        second = self.client.get(self.url + "?" + response.context["page_query"] + "&page=2")
        self.assertEqual(response.context["result_count"], 27)
        rows = response.context["rows"] + second.context["rows"]
        self.assertEqual(len(rows), 27)
        self.assertEqual(len({r["offering_id"] for r in rows}), 27)
        self.assertEqual(self._workbook(self.client.get(self.url + "?" + response.context["export_query"])).active.max_row, 82)
        for row in rows:
            self.assertNotIn("_display", row)
            self.assertIn("only_100=1", row["detail_query"])


    def test_dense_activity_reads_are_capped_and_fail_closed(self):
        component = GradingTemplateComponent.objects.create(template_period=self.period, code="CS", name="Standing", weight_percentage=100)
        activities = GradeActivity.objects.bulk_create([GradeActivity(tenant=self.tenant, campus=self.campus,
            offering=self.offering, template_period=self.period, template_component=component, title=f"A{i}", total_score=100) for i in range(201)])
        offering = CourseOffering.objects.select_related("course", "term", "section").get(pk=self.offering.pk)
        ids = set(StudentPeriodGrade.objects.filter(offering=self.offering).values_list("student_id", flat=True))
        bundle = MonitorReadBundle({offering.id: offering}, {self.period.id: self.period}, {(offering.id, self.period.id): ids})
        self.assertEqual(len(bundle.activities[(offering.id, self.period.id)]), 201)
        self.assertFalse(bundle.scores[(offering.id, self.period.id)])
        self.client.force_login(self.admin_user)
        self.assertEqual(self.search().context["rows"][0]["analytics_state"], "privacy_suppressed")
        GradeActivity.objects.filter(id__in=[a.id for a in activities[101:]]).delete()
        students = Student.objects.bulk_create([Student(tenant=self.tenant, campus=self.campus, department=self.department,
            student_no=f"DENSE{i}", last_name="Dense", first_name="Test") for i in range(201)])
        # The read bundle receives only the authorized eligible cohort from rows().
        cohort = {s.id for s in students}
        StudentActivityScore.objects.bulk_create([StudentActivityScore(activity=a, student=s, raw_score=100, computed_score=100)
            for a in activities[:101] for s in students], batch_size=500)
        with CaptureQueriesContext(connection) as queries:
            bundle = MonitorReadBundle({offering.id: offering}, {self.period.id: self.period}, {(offering.id, self.period.id): cohort})
        self.assertEqual(len(bundle.scores[(offering.id, self.period.id)]), 20001)
        self.assertLessEqual(len(queries), 7)

    def test_benchmark(self):
        self.client.force_login(self.admin_user)
        results = []
        for size in (27, 54):
            start = 0 if size == 27 else 26
            for index in range(start, size - 1):
                offering = self._create_extra_offering(f"BENCH{index:02}", "Benchmark", f"B{index:02}")
                self._seed_grades(offering, [100] * 2 + [90] * 8)
            def measure(name, call):
                before = time.perf_counter()
                with CaptureQueriesContext(connection) as queries:
                    response = call()
                self.assertEqual(response.status_code, 200)
                results.append(dict(classes=size, action=name, seconds=round(time.perf_counter()-before, 4), queries=len(queries)))
                return response
            measure("initial", lambda: self.client.get(self.url))
            response = measure("apply_all_faculty", self.search)
            self.assertEqual(response.context["result_count"], size)
            measure("page_2", lambda: self.client.get(self.url + "?" + response.context["page_query"] + "&page=2"))
            measure("xlsx", lambda: self.client.get(self.url + "?" + response.context["export_query"]))
        print("GRADE_FILTER_BENCHMARK=" + json.dumps(results))

    def test_populated_activity_query_growth(self):
        component = GradingTemplateComponent.objects.create(template_period=self.period, code="CS", name="Standing", weight_percentage=100)
        self.client.force_login(self.admin_user)
        measurements = []
        for size in (27, 54):
            for index in range(0 if size == 27 else 26, size - 1):
                offering = self._create_extra_offering(f"DENSEBENCH{index:02}", "Populated benchmark", f"DB{index:02}")
                self._seed_grades(offering, [100] * 2 + [90] * 8)
            offerings = list(CourseOffering.objects.filter(tenant=self.tenant))
            activities = GradeActivity.objects.bulk_create([GradeActivity(tenant=self.tenant, campus=self.campus,
                offering=o, template_period=self.period, template_component=component, title=f"A{i}", total_score=100)
                for o in offerings if not GradeActivity.objects.filter(offering=o).exists() for i in range(3)])
            students = {}
            for row in StudentPeriodGrade.objects.filter(tenant=self.tenant).values("offering_id", "student_id"):
                students.setdefault(row["offering_id"], []).append(row["student_id"])
            StudentActivityScore.objects.bulk_create([StudentActivityScore(activity=a, student_id=sid, raw_score=90, computed_score=95)
                for a in activities for sid in students[a.offering_id]], batch_size=500)
            start = time.perf_counter()
            with CaptureQueriesContext(connection) as queries:
                response = self.search()
            self.assertEqual(response.context["result_count"], size)
            self.assertLess(len(queries), 150)
            measurements.append(dict(classes=size, activities=size*3, scores=size*30,
                seconds=round(time.perf_counter()-start, 4), queries=len(queries)))
        self.assertLessEqual(measurements[1]["queries"], measurements[0]["queries"] + 2)
        print("GRADE_FILTER_DENSE_BENCHMARK=" + json.dumps(measurements))
