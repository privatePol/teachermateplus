"""Representative finalized records; all databases are disposable Django test databases."""
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from unittest.mock import patch

from django.core.cache import cache
from django.core import signing
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import ActiveGradingPeriodSetting, Course, FacultyAssignment, TenantTermGradingPeriod
from apps.admin_portal import tests_academic_performance_insights as fixtures
from apps.admin_portal import learning_insights_views as views
from apps.admin_portal import learning_insights as learning
from apps.admin_portal.insights import Selection
from apps.admin_portal.learning_insights import (
    INSUFFICIENT, PATTERNS, LearningSelection, build_report, classify, performance_change,
)
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    CourseBaseValueOverride, GradeActivity, GradeCorrectionRequest, GradeCorrectionUnlockWindow, GradeSubmission, GradeSubmissionReopenRequest,
    GradingPeriodLock, GradingTemplateComponent, GradingTemplateDetail, GradingTemplatePeriod, GradingTemplateSubcomponent,
    StudentActivityScore, StudentPeriodGrade,
)
from apps.rbac.models import Permission, RolePermission, UserPermission, UserRole
from apps.tenants.models import Campus, Department, Program, Tenant


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class LearningInsightsTests(TestCase):
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
        self.url = reverse("admin_portal:learning_performance_insights_preview")
        self.filters = {"academic_year_id": self.academic_year.id, "term_id": self.term.id,
                        "campus_id": self.campus.id, "course_code": self.course.code, "offering_id": None}
        permission, _ = Permission.objects.get_or_create(code="faculty_gradebook_monitor.read", defaults={"module": "faculty_gradebook_monitor", "action": "read"})
        RolePermission.objects.get_or_create(role=self.area_role, permission=permission)
        self.permission = permission
        self.component.code = "CS"
        self.component.weight_percentage = 70
        self.component.save()
        self.quiz_sub = GradingTemplateSubcomponent.objects.create(template_component=self.component, code="QUIZZES", name="Quizzes", weight_percentage=100)
        GradeActivity.objects.update(template_subcomponent=self.quiz_sub)
        self.midterm = GradingTemplatePeriod.objects.create(template=self.template, code="MIDTERM", name="Midterm", sequence_no=2)
        self.prefinal = GradingTemplatePeriod.objects.create(template=self.template, code="PREFINAL", name="Pre-Final", sequence_no=3)
        self.final = GradingTemplatePeriod.objects.create(template=self.template, code="FINAL", name="Final", sequence_no=4)
        self.mid_cs = GradingTemplateComponent.objects.create(template_period=self.midterm, code="CS", name="Class Standing", weight_percentage=70)
        self.mid_quiz = GradingTemplateSubcomponent.objects.create(template_component=self.mid_cs, code="QUIZZES", name="Quizzes", weight_percentage=100)
        self.pre_exam = GradingTemplateComponent.objects.create(template_period=self.period, code="EXAM", name="Examination", weight_percentage=30, is_exam_component=True, score_input_mode="DIRECT_PERCENTAGE")
        self.mid_exam = GradingTemplateComponent.objects.create(template_period=self.midterm, code="EXAM", name="Examination", weight_percentage=30, is_exam_component=True, score_input_mode="DIRECT_PERCENTAGE")
        self.term_periods = {}
        for number, (code, name) in enumerate((("PRELIM", "Prelim"), ("MIDTERM", "Midterm"), ("PREFINAL", "Pre-Final"), ("FINAL", "Final")), 1):
            self.term_periods[code] = TenantTermGradingPeriod.objects.create(tenant=self.tenant, term=self.term, code=code, name=name, sequence_no=number)
        self.active = ActiveGradingPeriodSetting.objects.create(tenant=self.tenant, campus=self.campus, term=self.term, period=self.term_periods["PREFINAL"])
        for offering in (self.offering_a, self.offering_b):
            cohort = list(Enrollment.objects.filter(course_offering=offering).values_list("student_id", flat=True))
            StudentActivityScore.objects.filter(activity__offering=offering).update(raw_score=70, computed_score=85)
            activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=offering,
                template_period=self.midterm, template_component=self.mid_cs, template_subcomponent=self.mid_quiz,
                title="Any title", total_score=100)
            for sid in cohort:
                StudentActivityScore.objects.create(activity=activity, student_id=sid, raw_score=80, computed_score=90)
                for period, standing, exam, periodic in ((self.period, 85, 75, 82), (self.midterm, 90, 85, 89)):
                    StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=offering,
                        template_period=period, student_id=sid, class_standing_grade=standing, exam_grade=exam,
                        period_grade=periodic, is_finalized=True)
            for period in (self.period, self.midterm):
                GradeSubmission.objects.create(tenant=self.tenant, campus=self.campus, offering=offering,
                    template_period=period, status="SUBMITTED", submitted_at=timezone.now() - timedelta(days=2))
        self.client.force_login(self.area_chair)

    def post(self, action="report", **kwargs):
        return self.client.post(self.url, {**{k: v for k, v in self.filters.items() if v is not None}, "action": action, **kwargs})

    def report(self, **kwargs):
        response = self.post(**kwargs)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def raw(self, **kwargs):
        request = self.client.get(self.url).wsgi_request
        return build_report(LearningSelection(request), {**self.filters, **kwargs})

    def complete_students(self, total, offering=None):
        offering = offering or self.offering_a
        students = [e.student for e in Enrollment.objects.filter(course_offering=offering).select_related("student")]
        for index in range(total - len(students)):
            student = self._student(offering, f"PRIVACY-{offering.id}-{index}", f"Privacy{index}")
            students.append(student)
            for period, quiz, standing, exam, periodic in (
                (self.period, 70, 85, 75, 82), (self.midterm, 80, 90, 85, 89),
            ):
                activity = GradeActivity.objects.get(offering=offering, template_period=period)
                self._score(activity, student, quiz, standing)
                StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=offering,
                    template_period=period, student=student, class_standing_grade=standing,
                    exam_grade=exam, period_grade=periodic, is_finalized=True)
        return students

    def bsa_period_definitions(self):
        """Disposable AE105-FAR structure from the supplied staging facts."""
        self.course.code = "AE105-FAR"
        self.course.save(update_fields=["code"])
        self.filters["course_code"] = self.course.code
        self.template.code = "BSA_GRADING_TEMPLATE"
        self.template.save(update_fields=["code"])
        for period, standing, exam, quiz, prefix, raw, cs_grade, exam_grade, periodic in (
            (self.period, self.component, self.pre_exam, self.quiz_sub, "PRELIM", "57.69", "84.65", "83.96", "84.35"),
            (self.midterm, self.mid_cs, self.mid_exam, self.mid_quiz, "MIDTERM", "36.05", "78.88", "80.00", "79.31"),
        ):
            standing.code, standing.weight_percentage = prefix + "_CA", 60
            exam.code, exam.weight_percentage, exam.score_input_mode = prefix + "_EXAM", 40, "RAW_BASE50"
            standing.save()
            exam.save()
            quiz.code, quiz.weight_percentage, quiz.detail_computation_mode = prefix + "_CA_QUIZZES", 60, "AVERAGE_ACTIVITIES"
            quiz.save()
            participation = GradingTemplateSubcomponent.objects.create(template_component=standing,
                code=prefix + "_CA_PARTICIPATION_OUTPUT", name="Participation/Output", weight_percentage=40,
                detail_computation_mode="AVERAGE_ACTIVITIES")
            for code, weight in (("ASSIGNMENT", 20), ("ACTIVITY", 20), ("ORAL_DEFENSE", 20),
                                 ("ORAL_PRESENTATION", 20), ("OTHERS", 10), ("RECITATION", 10)):
                GradingTemplateDetail.objects.create(template_subcomponent=participation,
                    code=prefix + "_CA_PARTICIPATION_OUTPUT_" + code, name=code, weight_percentage=weight)
            StudentActivityScore.objects.filter(activity__template_period=period).update(raw_score=raw,
                computed_score=learning.FacultyGradingService.compute_activity_score(raw_score=Decimal(raw),
                    total_score=Decimal("100"), base_value=Decimal("50")))
            StudentPeriodGrade.objects.filter(template_period=period).update(
                class_standing_grade=cs_grade, exam_grade=exam_grade, period_grade=periodic)

    def test_bsa_prefixed_periods_classify_declining_matched_results_and_show_charts(self):
        self.bsa_period_definitions()
        result = self.raw(offering_id=self.offering_a.id)
        self.assertEqual((result["analyzed"], result["insufficient"]), (1, 0))
        classification = result["classifications"][0]
        self.assertEqual(classification["detail"], "Declined together")
        self.assertEqual(classification["changes"], dict.fromkeys(("quiz", "standing", "exam"), "DECLINING"))
        self.assertEqual(classification["matched_students"], 2)
        self.assertEqual([row["quiz"] for row in result["charts"]["line"]], [Decimal("57.69"), Decimal("36.05")])
        self.assertEqual([row["standing"] for row in result["charts"]["line"]], [Decimal("84.65"), Decimal("78.88")])
        self.assertEqual([row["exam"] for row in result["charts"]["line"]], [Decimal("83.96"), Decimal("80.00")])
        self.assertEqual([row["count"] for row in result["patterns"]], [1, 0, 0])
        self.assertEqual(len(result["comparison_rows"]), 2)
        response = self.report(offering_id=self.offering_a.id)
        self.assertIn('id="learning-line"', response["html"])
        self.assertIn('id="learning-patterns"', response["html"])
        self.assertIn("Declined together", response["html"])

    def test_bsa_prefixed_periods_retain_minimum_disclosure_and_matching(self):
        self.complete_students(6)
        self.bsa_period_definitions()
        self.deny_aggregate_access()
        result = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(result["analyzed"], 1)
        self.assertEqual(result["classifications"][0]["matched_students"], 6)
        self.assertEqual([row["count"] for row in result["patterns"]], [1, 0, 0])
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm,
            student=self.student_a1).update(is_finalized=False)
        result = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(result["analyzed"], 0)
        self.assertEqual(result["charts"]["line"], [])

    def test_bsa_meaningful_component_and_nested_differences_still_reject_comparison(self):
        self.bsa_period_definitions()
        detail = GradingTemplateDetail.objects.filter(template_subcomponent__template_component=self.mid_cs).first()
        for obj, changes in (
            (self.mid_cs, {"weight_percentage": 65}),
            (self.mid_cs, {"score_input_mode": "DIRECT_PERCENTAGE"}),
            (self.mid_cs, {"code": "MIDTERM_LAB"}),
            (self.mid_exam, {"is_exam_component": False}),
            (self.mid_quiz, {"weight_percentage": 59}),
            (self.mid_quiz, {"detail_computation_mode": "WEIGHTED_DETAILS"}),
            (self.mid_quiz, {"score_input_mode": "DIRECT_PERCENTAGE"}),
            (self.mid_quiz, {"is_attendance_component": True}),
            (detail, {"code": "MIDTERM_CA_PARTICIPATION_OUTPUT_PROJECT"}),
            (detail, {"weight_percentage": 19}),
            (detail, {"score_input_mode": "DIRECT_PERCENTAGE"}),
        ):
            original = type(obj).objects.filter(pk=obj.pk).values(*changes).get()
            with self.subTest(model=obj._meta.label, changes=changes):
                type(obj).objects.filter(pk=obj.pk).update(**changes)
                result = self.raw(offering_id=self.offering_a.id)
                self.assertEqual(result["analyzed"], 0)
                self.assertEqual(result["classifications"][0]["category"], INSUFFICIENT)
                self.assertIn("Grading methods or component scales differ", result["classifications"][0]["detail"])
                self.assertEqual(result["charts"]["line_mode"], "descriptive")
                self.assertTrue(result["charts"]["line"])
                self.assertTrue(all("matched_students" not in point for point in result["charts"]["line"]))
            type(obj).objects.filter(pk=obj.pk).update(**original)

    def test_bsa_normalization_preserves_original_template_codes_and_shared_signature(self):
        self.bsa_period_definitions()
        models = (GradingTemplateComponent, GradingTemplateSubcomponent, GradingTemplateDetail)
        before = {model: list(model.objects.order_by("pk").values()) for model in models}
        original_prelim = learning.component_signature(self.period)
        original_midterm = learning.component_signature(self.midterm)
        self.assertNotEqual(original_prelim, original_midterm)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(learning.comparable_component_signature(self.period),
                             learning.comparable_component_signature(self.midterm))
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(learning.component_signature(self.period), original_prelim)
        self.assertEqual({model: list(model.objects.order_by("pk").values()) for model in models}, before)

    def test_bsa_prefixed_and_unprefixed_nested_identities_compare_equally(self):
        self.bsa_period_definitions()
        for model, query in (
            (GradingTemplateComponent, {"template_period": self.midterm}),
            (GradingTemplateSubcomponent, {"template_component__template_period": self.midterm}),
            (GradingTemplateDetail, {"template_subcomponent__template_component__template_period": self.midterm}),
        ):
            for row in model.objects.filter(**query):
                row.code = row.code.removeprefix("MIDTERM_")
                row.save(update_fields=["code"])
        self.assertEqual(self.raw(offering_id=self.offering_a.id)["analyzed"], 1)

    def test_comparability_does_not_strip_unknown_or_another_periods_prefix(self):
        self.bsa_period_definitions()
        for code in ("PRELIM_CA", "UNIT1_CA", "MIDTERMINAL_CA", "MIDTERM_"):
            with self.subTest(code=code):
                GradingTemplateComponent.objects.filter(pk=self.mid_cs.pk).update(code=code)
                self.assertEqual(self.raw(offering_id=self.offering_a.id)["analyzed"], 0)

    def test_colliding_normalized_component_identities_fail_closed(self):
        self.bsa_period_definitions()
        for period in (self.period, self.midterm):
            GradingTemplateComponent.objects.create(template_period=period, code="CA", name="Ambiguous CA", weight_percentage=0)
        self.assertNotEqual(learning.comparable_component_signature(self.period),
                            learning.comparable_component_signature(self.midterm))
        self.assertEqual(self.raw(offering_id=self.offering_a.id)["analyzed"], 0)

    def test_bsa_prefixed_progression_uses_only_the_matched_students(self):
        self.complete_students(6)
        self.bsa_period_definitions()
        StudentActivityScore.objects.filter(activity__offering=self.offering_a, activity__template_period=self.midterm,
            student=self.student_a1).update(is_excused=True)
        StudentActivityScore.objects.filter(activity=self.activity_a, student=self.student_a1).update(raw_score=100, computed_score=100)
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.period,
            student=self.student_a1).update(class_standing_grade=100, exam_grade=100)
        result = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(result["classifications"][0]["matched_students"], 5)
        self.assertEqual(result["classifications"][0]["detail"], "Declined together")
        self.assertEqual(result["charts"]["line"][0]["quiz"], Decimal("57.69"))
        self.assertEqual(result["charts"]["line"][0]["standing"], Decimal("84.65"))
        self.assertEqual(result["charts"]["line"][0]["exam"], Decimal("83.96"))
        self.assertNotEqual(result["rows"][0]["exam"], result["charts"]["line"][0]["exam"])

    def test_bsa_normalized_comparisons_keep_inclusive_three_point_thresholds(self):
        self.bsa_period_definitions()
        for current, direction in (("83.00", "IMPROVING"), ("77.00", "DECLINING"),
                                   ("82.99", "STABLE"), ("77.01", "STABLE")):
            with self.subTest(current=current):
                for period, value in ((self.period, "80.00"), (self.midterm, current)):
                    StudentActivityScore.objects.filter(activity__template_period=period).update(raw_score=value,
                        computed_score=learning.FacultyGradingService.compute_activity_score(raw_score=Decimal(value),
                            total_score=Decimal("100"), base_value=Decimal("50")))
                    StudentPeriodGrade.objects.filter(template_period=period).update(class_standing_grade=value, exam_grade=value)
                result = self.raw(offering_id=self.offering_a.id)
                self.assertEqual(result["classifications"][0]["changes"], dict.fromkeys(("quiz", "standing", "exam"), direction))
                self.assertEqual([row["count"] for row in result["patterns"]], [0, 0, 1] if direction == "STABLE" else [1, 0, 0])

    def test_comparability_policy_change_invalidates_old_report_tokens_and_cache(self):
        self.bsa_period_definitions()
        with patch.object(learning, "COMPARABILITY_POLICY_VERSION", "previous-policy"):
            previous = self.report(offering_id=self.offering_a.id)
        self.assertEqual(self.post("page", token=previous["token"]).status_code, 409)
        with patch.object(views, "build_report", wraps=views.build_report) as build:
            self.report(offering_id=self.offering_a.id)
            self.assertEqual(build.call_count, 1)

    def test_bsa_real_format_quizzes_reconcile_with_gradebook_rounded_records(self):
        students = self.complete_students(26)
        self.bsa_period_definitions()
        first = GradeActivity.objects.get(offering=self.offering_a, template_period=self.midterm)
        first.title, first.total_score = "Q1", 30
        first.save(update_fields=["title", "total_score"])
        second = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.midterm, template_component=self.mid_cs, template_subcomponent=self.mid_quiz,
            title="Q2", total_score=44)
        q1 = [13,17,12,8,11,8,0,21,7,10,11,0,11,6,0,11,13,7,8,18,10,17,10,0,16,12]
        q2 = [5,39,22,17,18,10,0,37,17,21,11,0,24,9,0,22,9,10,19,20,24,26,34,0,29,25]
        numeric = {}
        for student, one, two in zip(students, q1, q2):
            for activity, raw in ((first, one), (second, two)):
                converted = learning.FacultyGradingService.compute_activity_score(raw_score=Decimal(raw),
                    total_score=Decimal(activity.total_score), base_value=Decimal("50"))
                StudentActivityScore.objects.update_or_create(activity=activity, student=student,
                    defaults={"raw_score": raw, "computed_score": converted})
                numeric[(student.pk, activity.pk)] = converted
        layout = learning.PeriodSummaryLayoutService.build_layout(self.midterm, [first, second])
        official = learning.PeriodSummaryLayoutService.average([
            learning.PeriodSummaryLayoutService.print_group_average(layout,
                learning.PeriodSummaryLayoutService.build_row_values({"student": student}, layout, numeric), quizzes=True)
            for student in students])
        self.assertEqual(official, Decimal("68.03"))
        for extra in ({"offering_id": self.offering_a.id}, {}):
            result = self.raw(**extra)
            target = next(row for row in result["comparison_rows"] if row["section"].startswith(self.offering_a.section.code))
            self.assertEqual((target["quiz_grade"], target["quiz"]), (official, Decimal("36.05")))
            bar = next(row for row in result["charts"]["bars"] if row["section"] == target["section"])
            self.assertEqual(bar["quiz_grade"], official)
            if extra:
                self.assertEqual(result["rows"][1]["quiz_grade"], official)
                self.assertEqual(result["charts"]["line"][1]["quiz_grade"], official)
                response = self.report(**extra)
                self.assertIn("Gradebook points", response["html"])
                self.assertIn("Raw attainment", response["html"])
        overall = self.raw(course_code="")
        self.assertEqual(overall["rows"][1]["quiz_grade"], learning.PeriodSummaryLayoutService.average([official, Decimal("68.02")]))

    def test_quiz_grade_uses_actual_mode_and_base_without_second_conversion(self):
        override = CourseBaseValueOverride.objects.create(course=self.course, effective_from_term=self.term, base_value=50)
        for mode, base, raw, expected in (("RAW_BASE50", 50, "0", "50.00"),
                                         ("RAW_BASE50", 20, "20", "84.00"),
                                         ("DIRECT_PERCENTAGE", 50, "36.05", "36.05"),
                                         ("DIRECT_PERCENTAGE", 50, "0", "0.00")):
            with self.subTest(mode=mode, base=base, raw=raw):
                CourseBaseValueOverride.objects.filter(pk=override.pk).update(base_value=base)
                GradingTemplateSubcomponent.objects.filter(pk__in=[self.quiz_sub.pk, self.mid_quiz.pk]).update(score_input_mode=mode)
                value = learning.FacultyGradingService.compute_activity_score(raw_score=Decimal(raw), total_score=Decimal("100"),
                    base_value=Decimal(base), score_input_mode=mode)
                StudentActivityScore.objects.filter(activity__offering=self.offering_a).update(raw_score=raw, computed_score=value)
                result = self.raw(offering_id=self.offering_a.id)
                self.assertEqual(result["rows"][0]["quiz_grade"], Decimal(expected))
                self.assertEqual(result["rows"][0]["quiz"], Decimal(raw))
                self.assertEqual(result["charts"]["line"][0]["quiz_grade"], Decimal(expected))

    def test_gradebook_nested_quiz_weights_and_average_activities_use_official_rollups(self):
        first = GradingTemplateDetail.objects.create(template_subcomponent=self.quiz_sub, code="Q1", name="Q1", weight_percentage=80)
        second = GradingTemplateDetail.objects.create(template_subcomponent=self.quiz_sub, code="Q2", name="Q2", weight_percentage=20)
        self.activity_a.template_detail = first
        self.activity_a.save(update_fields=["template_detail"])
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.period, template_component=self.component, template_subcomponent=self.quiz_sub,
            template_detail=second, title="Q2", total_score=100)
        students = [self.student_a1, self.student_a2]
        numeric = {}
        for student in students:
            for row, raw, converted in ((self.activity_a, 100, 100), (activity, 0, 50)):
                StudentActivityScore.objects.update_or_create(activity=row, student=student,
                    defaults={"raw_score": raw, "computed_score": converted})
                numeric[(student.pk, row.pk)] = Decimal(converted)
        for mode, expected in (("WEIGHTED_DETAILS", "90.00"), ("AVERAGE_ACTIVITIES", "75.00")):
            with self.subTest(mode=mode):
                GradingTemplateSubcomponent.objects.filter(pk=self.quiz_sub.pk).update(detail_computation_mode=mode)
                layout = learning.PeriodSummaryLayoutService.build_layout(self.period, [self.activity_a, activity])
                official = learning.PeriodSummaryLayoutService.print_group_average(layout,
                    learning.PeriodSummaryLayoutService.build_row_values({"student": self.student_a1}, layout, numeric), quizzes=True)
                result = self.raw(offering_id=self.offering_a.id)
                self.assertEqual(result["rows"][0]["quiz_grade"], official)
                self.assertEqual(official, Decimal(expected))
                self.assertEqual(result["rows"][0]["quiz"], Decimal(50))

    def mixed_weighted_quiz_details(self, midterm=False):
        """The reviewed mixed category has no standalone Gradebook quiz rollup."""
        period, component, sub = (self.midterm, self.mid_cs, self.mid_quiz) if midterm else (self.period, self.component, self.quiz_sub)
        quiz_activity = GradeActivity.objects.get(offering=self.offering_a, template_period=period)
        sub.code, sub.name = "ASSESSMENTS", "Assessments"
        sub.detail_computation_mode = "WEIGHTED_DETAILS"
        sub.save()
        quiz = GradingTemplateDetail.objects.create(template_subcomponent=sub,
            code="QUIZZES", name="Quizzes", weight_percentage=50)
        project = GradingTemplateDetail.objects.create(template_subcomponent=sub,
            code="PROJECT", name="Project", weight_percentage=50)
        quiz_activity.template_detail = quiz
        quiz_activity.save(update_fields=["template_detail"])
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=period, template_component=component, template_subcomponent=sub,
            template_detail=project, title="Project", total_score=100)
        numeric = {}
        for student in (self.student_a1, self.student_a2):
            for row, raw, converted in ((quiz_activity, 70, 85), (activity, 100, 100)):
                StudentActivityScore.objects.update_or_create(activity=row, student=student,
                    defaults={"raw_score": raw, "computed_score": converted})
                numeric[(student.pk, row.pk)] = Decimal(converted)
        return activity, numeric

    def test_mixed_weighted_quiz_matches_unavailable_official_gradebook_average(self):
        project, numeric = self.mixed_weighted_quiz_details()
        layout = learning.PeriodSummaryLayoutService.build_layout(self.period, [self.activity_a, project])
        official = learning.PeriodSummaryLayoutService.average([
            learning.PeriodSummaryLayoutService.print_group_average(layout,
                learning.PeriodSummaryLayoutService.build_row_values({"student": student}, layout, numeric), quizzes=True)
            for student in (self.student_a1, self.student_a2)])
        self.assertIsNone(official)
        result = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(result["rows"][0]["quiz_grade"], official)
        self.assertIsNone(result["rows"][0]["quiz"])

    def test_mixed_weighted_quiz_is_unavailable_across_scopes_charts_and_patterns(self):
        self.mixed_weighted_quiz_details()
        self.mixed_weighted_quiz_details(midterm=True)
        for filters in ({"offering_id": self.offering_a.id}, {}, {"course_code": ""}):
            with self.subTest(filters=filters):
                result = self.raw(**filters)
                first = result["rows"][0]
                self.assertIsNone(first["quiz_grade"])
                self.assertIsNone(first["quiz"])
                self.assertEqual((first["standing"], first["exam"], first["periodic"]),
                    (Decimal("85"), Decimal("75"), Decimal("82")))
                if filters.get("offering_id"):
                    self.assertEqual((first["quizzes"], first["activities"]), (1, 2))
                self.assertEqual(result["analyzed"], 0)
                self.assertTrue(all(item["category"] == INSUFFICIENT for item in result["classifications"]))
                self.assertTrue(all(item["count"] == 0 and item["percentage"] is None for item in result["patterns"]))
                self.assertIsNone(result["charts"]["line"][0]["quiz_grade"])
                self.assertIsNone(result["charts"]["line"][0]["quiz"])
                self.assertEqual(result["charts"]["line"][0]["standing"], Decimal("85"))
                if result["charts"]["bars"]:
                    target = next(row for row in result["charts"]["bars"] if row["section"].startswith(self.offering_a.section.code))
                    self.assertIsNone(target["quiz_grade"])
                    self.assertIsNone(target["quiz"])
        response = self.report(offering_id=self.offering_a.id)
        self.assertNotIn("42.50", str(response))
        self.assertIn("Not Available", response["html"])

    def test_mixed_weighted_quiz_does_not_change_valid_section_votes(self):
        self.mixed_weighted_quiz_details()
        self.mixed_weighted_quiz_details(midterm=True)
        for period, component, mixed in ((self.period, self.component, self.quiz_sub),
                                         (self.midterm, self.mid_cs, self.mid_quiz)):
            mixed.weight_percentage = 50
            mixed.save(update_fields=["weight_percentage"])
            supported = GradingTemplateSubcomponent.objects.create(template_component=component,
                code="QUIZZES", name="Quizzes", weight_percentage=50)
            GradeActivity.objects.filter(offering=self.offering_b, template_period=period).update(template_subcomponent=supported)
        result = self.raw(course_code="")
        self.assertEqual((result["analyzed"], result["insufficient"]), (1, 1))
        self.assertEqual([item["count"] for item in result["patterns"]], [1, 0, 0])
        self.assertEqual(result["patterns"][0]["percentage"], Decimal("100"))
        self.assertEqual(result["rows"][0]["quiz_grade_sections"], 1)
        self.assertEqual(result["rows"][0]["quiz_grade"], Decimal("85"))

    def test_mixed_weighted_quiz_privacy_membership_matches_numeric_path(self):
        self.analysis_inventory(1)
        self.mixed_weighted_quiz_details()
        self.deny_aggregate_access()
        projections = []
        original = learning.disclosed_populations
        def observe(histories, enrolled, leadership):
            projections.append({pk: [(row["contributors"], set(row["student_values"]),
                set(row["pattern_values"])) for row in rows] for pk, rows in histories.items()})
            return original(histories, enrolled, leadership)
        with patch.object(learning, "disclosed_populations", side_effect=observe):
            self.raw()
            self.raw(course_code="")
        self.assertEqual(projections[0], projections[1])

    def test_gradebook_quiz_rounding_is_not_conversion_of_rounded_raw_average(self):
        self.assertEqual(learning.FacultyGradingService.compute_activity_score(raw_score=Decimal("36.05"),
            total_score=Decimal(100), base_value=Decimal(50)), Decimal("68.02"))
        for raw, computed in (("36.05", "68.02"), ("36.07", "68.04")):
            StudentActivityScore.objects.filter(activity=self.activity_a).update(raw_score=raw, computed_score=computed)
            result = self.raw(offering_id=self.offering_a.id)
            self.assertEqual(result["rows"][0]["quiz_grade"], Decimal(computed))

    def test_missing_or_invalid_gradebook_score_never_uses_raw_as_a_grade(self):
        StudentActivityScore.objects.filter(activity=self.activity_a, student=self.student_a1).update(computed_score=101)
        result = self.raw(offering_id=self.offering_a.id)
        self.assertIsNone(result["rows"][0]["quiz_grade"])
        self.assertEqual(result["rows"][0]["quiz"], Decimal("70.00"))
        self.assertIsNone(result["charts"]["line"][0]["quiz_grade"])
        StudentActivityScore.objects.filter(activity=self.activity_a, student=self.student_a1).delete()
        result = self.raw(offering_id=self.offering_a.id)
        self.assertIsNone(result["rows"][0]["quiz_grade"])
        self.assertIsNone(result["rows"][0]["quiz"])

    def test_unreliable_quiz_rollup_is_unavailable_without_inventing_a_grade(self):
        for changes in ({"detail_computation_mode": "UNKNOWN"}, {"weight_percentage": -1}):
            with self.subTest(changes=changes):
                GradingTemplateSubcomponent.objects.filter(pk=self.quiz_sub.pk).update(
                    **{"detail_computation_mode": "WEIGHTED_DETAILS", "weight_percentage": 100, **changes})
                result = self.raw(offering_id=self.offering_a.id)
                self.assertIsNone(result["rows"][0]["quiz_grade"])
                self.assertEqual(result["rows"][0]["quiz"], Decimal("70.00"))

    def test_converted_display_keeps_raw_three_point_classification_basis(self):
        for period, raw, converted, grade in ((self.period, "80", "90", "80"), (self.midterm, "83", "91.5", "83")):
            StudentActivityScore.objects.filter(activity__template_period=period).update(raw_score=raw, computed_score=converted)
            StudentPeriodGrade.objects.filter(template_period=period).update(class_standing_grade=grade, exam_grade=grade)
        result = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(result["classifications"][0]["changes"], dict.fromkeys(("quiz", "standing", "exam"), "IMPROVING"))
        self.assertEqual([row["quiz_grade"] for row in result["charts"]["line"]], [Decimal("90"), Decimal("91.5")])
        self.assertEqual([row["quiz"] for row in result["charts"]["line"]], [Decimal("80"), Decimal("83")])
        self.assertEqual([row["count"] for row in result["patterns"]], [1, 0, 0])

    def test_descriptive_line_keeps_concluded_values_when_formal_comparison_is_unavailable(self):
        self.mid_cs.weight_percentage = 60
        self.mid_cs.save()
        result = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(result["analyzed"], 0)
        self.assertEqual(result["charts"]["line_mode"], "descriptive")
        self.assertEqual([row["period"] for row in result["charts"]["line"]], ["Prelim", "Midterm"])
        self.assertIn("Descriptive", result["line_note"])
        self.assertTrue(all(row["percentage"] is None for row in result["patterns"]))

    def test_display_policy_invalidates_tokens_and_snapshots(self):
        with patch.object(learning, "QUIZ_DISPLAY_POLICY_VERSION", "old-display-policy"):
            previous = self.report()
        self.assertEqual(self.post("page", token=previous["token"]).status_code, 409)

    def test_raw_only_context_does_not_create_an_empty_grade_chart(self):
        StudentActivityScore.objects.filter(activity__offering=self.offering_a).update(computed_score=101)
        StudentPeriodGrade.objects.filter(offering=self.offering_a).update(class_standing_grade=None, exam_grade=None, period_grade=160)
        result = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(result["rows"][0]["quiz"], Decimal("70.00"))
        self.assertIsNone(result["rows"][0]["quiz_grade"])
        self.assertEqual(result["charts"]["line"], [])

    def test_overall_converted_series_marks_changing_section_denominators_descriptive(self):
        StudentActivityScore.objects.filter(activity=self.activity_b).update(computed_score=101)
        result = self.raw(course_code="")
        self.assertEqual([row["quiz_grade_sections"] for row in result["rows"]], [1, 2])
        self.assertEqual(result["charts"]["line_mode"], "descriptive")
        self.assertIn("different contributing sections", result["line_note"])
        self.assertEqual(result["analyzed"], 2)
        self.assertTrue(all("quiz_grade_basis" not in row for row in result["rows"]))

    def deny_aggregate_access(self):
        UserPermission.objects.create(user=self.area_chair, permission=self.permission, tenant=self.tenant,
            campus=self.campus, grant_type="DENY")

    def selected_offerings(self, selection_type=LearningSelection):
        request = self.client.get(self.url).wsgi_request
        return set(selection_type(request).offerings)

    def analysis_inventory(self, count=12, start=0):
        """Real finalized unrelated classes; no mocked calculation/query paths."""
        offerings = []
        course, _ = Course.objects.get_or_create(tenant=self.tenant, code="UNRELATED", defaults={"title": "Unrelated course"})
        for index in range(start, start + count):
            offering = self._offering(f"ANALYSIS-{index}", self.campus, self.department, self.program)
            offering.course = course
            offering.save(update_fields=["course"])
            self._assign(offering, self.faculty)
            FacultyAssignment.objects.filter(offering=offering).update(tenant=None, campus=None)
            students = [self._student(offering, f"ANALYSIS-{index}-{sid}", "Synthetic") for sid in range(6)]
            for period, component, quiz, raw, standing, exam, periodic in (
                (self.period, self.component, self.quiz_sub, 70, 85, 75, 82),
                (self.midterm, self.mid_cs, self.mid_quiz, 80, 90, 85, 89),
            ):
                activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=offering,
                    template_period=period, template_component=component, template_subcomponent=quiz, title="Quiz", total_score=100)
                StudentActivityScore.objects.bulk_create([
                    StudentActivityScore(activity=activity, student=student, raw_score=raw, computed_score=standing)
                    for student in students])
                StudentPeriodGrade.objects.bulk_create([
                    StudentPeriodGrade(tenant=self.tenant, campus=self.campus, offering=offering, template_period=period,
                        student=student, class_standing_grade=standing, exam_grade=exam, period_grade=periodic, is_finalized=True)
                    for student in students])
                GradeSubmission.objects.create(tenant=self.tenant, campus=self.campus, offering=offering, template_period=period,
                    status="SUBMITTED", submitted_at=timezone.now() - timedelta(days=2))
            offerings.append(offering)
        return offerings

    def test_filtered_numeric_analysis_preserves_siblings_and_skips_unrelated_averages(self):
        self.analysis_inventory()
        original = learning.activity_values
        for overrides, sections, numeric in (({"offering_id": self.offering_a.id}, 1, 2), ({}, 2, 2),
                                             ({"course_code": ""}, 14, 14)):
            with self.subTest(filters=overrides), patch.object(learning, "activity_values", wraps=original) as calculate:
                raw = self.raw(**overrides)
                self.assertEqual((raw["sections"], raw["analyzed"]), (sections, sections))
                # Two concluded periods per class. Wrapping executes the real work.
                self.assertEqual(calculate.call_count, numeric * 2)
                if overrides.get("course_code") != "":
                    self.assertEqual(len(raw["comparison_rows"]), 2)
                for call in calculate.call_args_list:
                    if overrides.get("course_code") != "":
                        self.assertTrue(all(a.offering_id in {self.offering_a.id, self.offering_b.id} for a in call.args[0]))

    def test_report_query_growth_is_bounded_for_shared_configurations(self):
        request = self.client.get(self.url).wsgi_request
        def measured():
            with CaptureQueriesContext(connection) as captured:
                raw = build_report(LearningSelection(request), self.filters)
            sql = [row["sql"] for row in captured]
            self.assertEqual(sum('FROM "grading_period_locks"' in query for query in sql), 1)
            self.assertEqual((raw["sections"], raw["analyzed"]), (2, 2))
            return len(sql)
        self.analysis_inventory(2)
        small = measured()
        self.analysis_inventory(12, start=2)
        large = measured()
        self.assertLessEqual(large, small + 2)

    def test_unrelated_course_privacy_membership_withholds_recoverable_complement(self):
        students = self.complete_students(6)
        unrelated = self.analysis_inventory(1)[0]
        # A related authorized aggregate excludes one member of the selected six.
        for student in students[:5]:
            Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
                term=self.term, student=student, course_offering=unrelated)
            for period, standing, exam, periodic in ((self.period, 85, 75, 82), (self.midterm, 90, 85, 89)):
                activity = GradeActivity.objects.get(offering=unrelated, template_period=period)
                self._score(activity, student, 70, standing)
                StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=unrelated,
                    template_period=period, student=student, class_standing_grade=standing, exam_grade=exam,
                    period_grade=periodic, is_finalized=True)
        self.deny_aggregate_access()
        filtered = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(filtered["analyzed"], 0)
        self.assertTrue(all(row["exam"] is None for row in filtered["rows"]))
        overall = self.raw(course_code="")
        self.assertEqual(filtered["classifications"][0], next(c for c in overall["classifications"] if c["id"] == self.offering_a.id))

    def test_membership_only_rows_match_the_full_numeric_disclosure_basis(self):
        unrelated = self.analysis_inventory(3)
        self.deny_aggregate_access()
        first = StudentPeriodGrade.objects.filter(offering=unrelated[0]).first()
        StudentPeriodGrade.objects.filter(pk=first.pk).update(exam_grade=None)
        StudentPeriodGrade.objects.filter(offering=unrelated[1], template_period=self.midterm).update(class_standing_grade=101)
        score = StudentActivityScore.objects.filter(activity__offering=unrelated[2]).first()
        StudentActivityScore.objects.filter(pk=score.pk).update(is_excused=True)
        projections = []
        original = learning.disclosed_populations
        def observe(histories, enrolled, leadership):
            projections.append({pk: [(row["eligible"], row["cohort"], row["contributors"], row["signature"],
                {metric: row["scales"][metric] for metric in ("standing", "exam")},
                set(row["student_values"]), set(row["pattern_values"])) for row in rows] for pk, rows in histories.items()})
            return original(histories, enrolled, leadership)
        with patch.object(learning, "disclosed_populations", side_effect=observe):
            self.raw()
            self.raw(course_code="")
        self.assertEqual(projections[0], projections[1])

    def test_batched_locks_preserve_official_precedence_and_period_normalization(self):
        common = {"tenant": self.tenant, "campus": self.campus, "academic_year": self.academic_year, "term": self.term}
        GradingPeriodLock.objects.create(**common, scope_type="COURSE", course_offering=self.offering_a,
            period_code="FINAL", is_locked=False)
        GradingPeriodLock.objects.create(**common, period_code="Final", is_locked=True,
            deadline_at=timezone.now() - timedelta(hours=1))
        GradingPeriodLock.objects.create(**common, period_code="Pre-Final", is_locked=True)
        request = self.client.get(self.url).wsgi_request
        selection = LearningSelection(request)
        expected = {(o.id, p.id): learning.GradingGovernanceService.resolve_lock(offering=o, template_period=p)
                    for o in selection.matching(self.filters) for p in selection.periods(o)}
        with patch.object(learning, "governance_reason", wraps=learning.governance_reason) as governance:
            build_report(selection, self.filters)
        for call in governance.call_args_list:
            offering, period = call.args[:2]
            actual = call.args[5]
            official = expected[(offering.id, period.id)]
            self.assertEqual(actual.pk if actual else None, official.pk if official else None)

    def test_unchanged_report_reuses_public_snapshot_but_checks_live_generation(self):
        original = views.build_report
        with patch.object(views, "build_report", wraps=original) as build:
            first = self.report(offering_id=self.offering_a.id)
            second = self.report(offering_id=self.offering_a.id)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(first["charts"], second["charts"])
            self.assertEqual(first["html"], second["html"])
            StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(exam_grade=70)
            self.assertEqual(self.post("page", token=first["token"]).status_code, 409)
            self.report(offering_id=self.offering_a.id)
            self.assertEqual(build.call_count, 2)

    def test_cached_report_reauthorizes_and_detects_revocation_during_render(self):
        self.report()
        original = views.response_data
        def revoke(*args, **kwargs):
            payload = original(*args, **kwargs)
            UserPermission.objects.create(user=self.area_chair, permission=self.permission, tenant=self.tenant,
                campus=self.campus, grant_type="DENY")
            return payload
        with patch.object(views, "response_data", side_effect=revoke), patch.object(views, "build_report", wraps=views.build_report) as build:
            self.assertEqual(self.post().status_code, 409)
            self.assertEqual(build.call_count, 0)

    def test_request_reuses_selection_with_independent_closing_reauthorization(self):
        with patch.object(views, "LearningSelection", wraps=LearningSelection) as selections:
            self.report()
        self.assertEqual(selections.call_count, 2)

    def test_rehydration_does_not_use_pre_digest_offering_values(self):
        request = self.client.get(self.url).wsgi_request
        selection = LearningSelection(request)
        Course.objects.filter(pk=self.course.id).update(title="Changed before opening digest")
        selection.refresh_offerings()
        self.assertEqual(selection.offerings[self.offering_a.id].course.title, "Changed before opening digest")
        self.assertEqual(selection._periods, {})
        self.assertEqual(selection._contexts, {})

    def other_department_class(self, campus=None):
        campus = campus or self.campus
        department = Department.objects.create(tenant=self.tenant, campus=campus, code="OTHER", name="Other")
        program = Program.objects.create(tenant=self.tenant, campus=campus, department=department,
                                         code="OTHER", name="Other")
        faculty = self._user("other-faculty", campus, department)
        UserRole.objects.create(user=faculty, role=self.faculty_role, tenant=self.tenant,
                                campus=campus, department=department)
        offering = self._offering("OTHER", campus, department, program)
        self._assign(offering, faculty)
        return offering, faculty, department

    def monitoring_user(self, code, *, superuser=False, department=None):
        role = self._role_with_access(code, code)
        RolePermission.objects.get_or_create(role=role, permission=self.permission)
        user = self._user("monitor-" + code.lower(), self.campus, self.department)
        user.is_superuser = superuser
        user.save(update_fields=["is_superuser"])
        UserRole.objects.create(user=user, role=role, tenant=self.tenant,
                                campus=self.campus, department=department)
        return user, role

    def test_legacy_five_offerings_keep_four_eligible_without_writes(self):
        # Explicit 55, legacy 64/73/82 and inactive 506, with finalized evidence.
        FacultyAssignment.objects.filter(offering=self.offering_b).update(tenant=None, campus=None)
        extras = []
        for code in ("LEGACY-73", "LEGACY-82", "INACTIVE-506"):
            offering = self._offering(code, self.campus, self.department, self.program)
            self._assign(offering, self.faculty)
            FacultyAssignment.objects.filter(offering=offering).update(
                tenant=None, campus=None, is_active=code != "INACTIVE-506")
            for index in range(2):
                student = self._student(offering, f"{code}-{index}", "Legacy")
                for period, standing, exam, periodic in ((self.period, 85, 75, 82), (self.midterm, 90, 85, 89)):
                    StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=offering,
                        template_period=period, student=student, class_standing_grade=standing,
                        exam_grade=exam, period_grade=periodic, is_finalized=True)
            for period, component, subcomponent, score in (
                (self.period, self.component, self.quiz_sub, 70), (self.midterm, self.mid_cs, self.mid_quiz, 80),
            ):
                activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=offering,
                    template_period=period, template_component=component, template_subcomponent=subcomponent,
                    title="Quiz", total_score=100)
                for enrollment in Enrollment.objects.filter(course_offering=offering):
                    self._score(activity, enrollment.student, score, score)
                GradeSubmission.objects.create(tenant=self.tenant, campus=self.campus, offering=offering,
                    template_period=period, status="SUBMITTED", submitted_at=timezone.now() - timedelta(days=2))
            extras.append(offering)
        expected = {self.offering_a.id, self.offering_b.id, extras[0].id, extras[1].id}
        request = self.client.get(self.url).wsgi_request
        before = list(FacultyAssignment.objects.order_by("id").values())
        with CaptureQueriesContext(connection) as queries:
            selection = LearningSelection(request)
            raw = build_report(selection, {**self.filters, "course_code": ""})
        self.assertEqual(set(selection.offerings), expected)
        self.assertEqual((raw["sections"], raw["analyzed"]), (4, 4))
        self.assertFalse(any(q["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries))
        self.assertEqual(list(FacultyAssignment.objects.order_by("id").values()), before)
        self.assertEqual(self.selected_offerings(Selection), {self.offering_a.id})
        options = self.post("options").json()["options"]
        self.assertEqual({row["value"] for row in options["offering_id"]}, expected)

    def test_legacy_partial_and_conflicting_assignment_scopes_rejected(self):
        foreign = Tenant.objects.create(code="FOREIGN", name="Foreign")
        other = Campus.objects.create(tenant=self.tenant, code="SECOND", name="Second")
        for tenant_id, campus_id in ((None, self.campus.id), (self.tenant.id, None),
                                     (foreign.id, self.campus.id), (self.tenant.id, other.id)):
            with self.subTest(tenant_id=tenant_id, campus_id=campus_id):
                FacultyAssignment.objects.filter(offering=self.offering_b).update(tenant_id=tenant_id, campus_id=campus_id)
                self.assertEqual(self.selected_offerings(), {self.offering_a.id})

    def test_legacy_requires_active_accepted_assignment_and_active_faculty(self):
        assignments = FacultyAssignment.objects.filter(offering=self.offering_b)
        assignments.update(tenant=None, campus=None)
        accepted_at = assignments.get().accepted_at
        for changes in ({"is_active": False}, {"response_status": "PENDING"}, {"accepted_at": None}):
            with self.subTest(changes=changes):
                assignments.update(**{"is_active": True, "response_status": "ACCEPTED",
                                      "accepted_at": accepted_at, **changes})
                self.assertNotIn(self.offering_b.id, self.selected_offerings())
        assignments.update(is_active=True, response_status="ACCEPTED", accepted_at=accepted_at)
        self.faculty.is_active = False
        self.faculty.save(update_fields=["is_active"])
        self.assertEqual(self.selected_offerings(), set())

    def test_legacy_does_not_bypass_related_academic_scope(self):
        FacultyAssignment.objects.filter(offering=self.offering_b).update(tenant=None, campus=None)
        foreign = Tenant.objects.create(code="FOREIGN", name="Foreign")
        # Bulk updates deliberately represent inconsistent imported records.
        for obj, changes in ((self.offering_b, {"tenant_id": foreign.id}),
                             (self.academic_year, {"tenant_id": foreign.id}),
                             (self.term, {"tenant_id": foreign.id}),
                             (self.course, {"tenant_id": foreign.id}),
                             (self.offering_b.section, {"campus_id": Campus.objects.create(
                                 tenant=self.tenant, code="OTHER", name="Other").id})):
            original = {field: getattr(obj, field) for field in changes}
            with self.subTest(model=obj._meta.label):
                type(obj).objects.filter(pk=obj.pk).update(**changes)
                self.assertNotIn(self.offering_b.id, self.selected_offerings())
                type(obj).objects.filter(pk=obj.pk).update(**original)

    def test_legacy_preserves_area_chair_and_college_dean_supervision(self):
        offering, _, _ = self.other_department_class()
        FacultyAssignment.objects.update(tenant=None, campus=None)
        expected = {self.offering_a.id, self.offering_b.id}
        self.assertEqual(self.selected_offerings(), expected)
        dean, _ = self.monitoring_user("COLLEGE_DEAN", department=self.department)
        self.client.force_login(dean)
        for superuser in (False, True):
            with self.subTest(superuser=superuser):
                dean.is_superuser = superuser
                dean.save(update_fields=["is_superuser"])
                self.assertEqual(self.selected_offerings(), expected)
                self.assertNotIn(offering.id, self.selected_offerings())
        # Even a campus-wide Dean membership retains the Area Chairman chain.
        UserRole.objects.filter(user=dean).update(department=None)
        UserRole.objects.filter(user=self.area_chair).update(is_active=False)
        self.assertEqual(self.selected_offerings(), set())

    def test_legacy_cross_campus_lecturer_uses_offering_campus_role(self):
        campus = Campus.objects.create(tenant=self.tenant, code="SECOND", name="Second")
        offering, _, department = self.other_department_class(campus)
        FacultyAssignment.objects.filter(offering=offering).update(
            faculty_user=self.faculty, tenant=None, campus=None)
        cao, role = self.monitoring_user("CAO")
        UserRole.objects.create(user=cao, role=role, tenant=self.tenant, campus=campus)
        self.client.force_login(cao)
        self.assertNotIn(offering.id, self.selected_offerings())
        membership = UserRole.objects.create(user=self.faculty, role=self.faculty_role,
            tenant=self.tenant, campus=campus, department=department)
        self.assertIn(offering.id, self.selected_offerings())
        self.assertEqual(self.faculty.default_campus_id, self.campus.id)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        self.assertNotIn(offering.id, self.selected_offerings())

    def test_superadmin_matches_cao_without_area_chair_inventory_limit(self):
        offering, _, _ = self.other_department_class()
        FacultyAssignment.objects.filter(offering=offering).update(tenant=None, campus=None)
        self.assertNotIn(offering.id, self.selected_offerings())
        cao, _ = self.monitoring_user("CAO")
        self.client.force_login(cao)
        expected = self.selected_offerings()
        self.assertEqual(expected, {self.offering_a.id, self.offering_b.id, offering.id})
        user, _ = self.monitoring_user("SUPER_ADMIN", department=self.department)
        for superuser in (False, True):
            with self.subTest(superuser=superuser):
                user.is_superuser = superuser
                user.save(update_fields=["is_superuser"])
                self.client.force_login(user)
                self.assertEqual(self.selected_offerings(), expected)
                self.assertEqual(self.raw(course_code="")["sections"], 3)
                self.assertEqual(self.raw()["analyzed"], 2)

    def test_superuser_flag_does_not_expand_area_chair_scope(self):
        offering, _, _ = self.other_department_class()
        self.area_chair.is_superuser = True
        self.area_chair.save(update_fields=["is_superuser"])
        self.assertEqual(self.selected_offerings(), {self.offering_a.id, self.offering_b.id})
        self.assertNotIn(offering.id, self.selected_offerings())

    def test_superadmin_requires_actual_feature_grants_and_active_role(self):
        user, role = self.monitoring_user("SUPER_ADMIN", superuser=True)
        self.client.force_login(user)
        permission = Permission.objects.get(code="grading_analytics.read")
        for code in ("admin_portal.access", "grading_analytics.read"):
            with self.subTest(permission=code):
                removed = Permission.objects.get(code=code)
                RolePermission.objects.filter(role=role, permission=removed).delete()
                self.assertEqual(self.selected_offerings(), set())
                self.assertEqual(self.post().status_code, 400)
                RolePermission.objects.create(role=role, permission=removed)
        UserRole.objects.filter(user=user).update(is_active=False)
        for granted in ("admin_portal.access", "grading_analytics.read"):
            UserPermission.objects.create(user=user, permission=Permission.objects.get(code=granted),
                tenant=self.tenant, campus=self.campus, grant_type="ALLOW")
        self.assertEqual(self.selected_offerings(), set())
        self.assertTrue(permission.is_active)

    def test_superadmin_campus_tenant_limits_and_revocation(self):
        campus = Campus.objects.create(tenant=self.tenant, code="SECOND", name="Second")
        offering, _, _ = self.other_department_class(campus)
        user, role = self.monitoring_user("SUPER_ADMIN", superuser=True)
        self.client.force_login(user)
        self.assertNotIn(offering.id, self.selected_offerings())
        foreign = Tenant.objects.create(code="FOREIGN", name="Foreign")
        foreign_campus = Campus.objects.create(tenant=foreign, code="FOREIGN", name="Foreign")
        UserRole.objects.create(user=user, role=role, tenant=foreign, campus=foreign_campus)
        self.assertNotIn(foreign_campus.id, [c["value"] for c in self.post("options").json()["options"]["campus_id"]])
        membership = UserRole.objects.create(user=user, role=role, tenant=self.tenant, campus=campus)
        self.assertIn(offering.id, self.selected_offerings())
        prior = self.report()
        membership.delete()
        self.assertEqual(self.post("page", token=prior["token"]).status_code, 409)
        self.assertNotIn(offering.id, self.selected_offerings())

    def test_superadmin_direct_and_global_denies_override_grants(self):
        user, _ = self.monitoring_user("SUPER_ADMIN", superuser=True)
        self.client.force_login(user)
        for tenant, campus in ((self.tenant, self.campus), (None, None)):
            for code in ("admin_portal.access", "grading_analytics.read"):
                with self.subTest(tenant=tenant, permission=code):
                    prior = self.report()
                    denial = UserPermission.objects.create(user=user, permission=Permission.objects.get(code=code),
                        tenant=tenant, campus=campus, grant_type="DENY")
                    self.assertEqual(self.selected_offerings(), set())
                    self.assertEqual(self.post("page", token=prior["token"]).status_code, 409)
                    denial.delete()

    def test_superadmin_aggregate_deny_and_permission_revocation(self):
        user, role = self.monitoring_user("SUPER_ADMIN", superuser=True)
        self.client.force_login(user)
        prior = self.report()
        denial = UserPermission.objects.create(user=user, permission=self.permission,
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        self.assertEqual(self.post("page", token=prior["token"]).status_code, 409)
        self.assertEqual(self.raw()["analyzed"], 0)
        denial.delete()
        prior = self.report()
        RolePermission.objects.filter(role=role, permission=self.permission).delete()
        self.assertEqual(self.post("page", token=prior["token"]).status_code, 409)
        self.assertEqual(self.raw()["analyzed"], 0)

    def test_blocker_sparse_component_uses_actual_contributors(self):
        students = self.complete_students(5)
        self.deny_aggregate_access()
        StudentPeriodGrade.objects.filter(offering=self.offering_a).exclude(student=students[0]).update(exam_grade=None)
        StudentPeriodGrade.objects.filter(offering=self.offering_a, student=students[0]).update(exam_grade="61.37")
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertTrue(all(row["exam"] is None for row in raw["rows"]))
        self.assertTrue(all(row["exam"] is None for row in raw["comparison_rows"]))
        self.assertEqual(raw["analyzed"], 0)
        self.assertTrue(all(row["exam"] is None for row in self.raw(course_code="")["rows"]))
        response = self.post(offering_id=self.offering_a.id)
        self.assertNotIn("61.37", response.content.decode())
        data = response.json()
        self.assertNotIn("61.37", self.post("page", token=data["token"]).content.decode())
        state = signing.loads(data["token"], salt=views.TOKEN_SALT)
        snapshot = cache.get("learning-insights:" + state["nonce"])
        self.assertNotIn("61.37", str(snapshot))
        for row in snapshot["rows"]:
            for internal in ("contributors", "student_values", "pattern_values", "cohort", "comparison_values"):
                self.assertNotIn(internal, row)

    def test_blocker_matched_five_cannot_expose_excluded_single_student(self):
        students = self.complete_students(6)
        self.deny_aggregate_access()
        excluded = students[-1]
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.period,
            student=excluded).update(exam_grade="61.37")
        StudentActivityScore.objects.filter(activity__offering=self.offering_a,
            activity__template_period=self.midterm, student=excluded).delete()
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(raw["charts"]["line"], [])
        self.assertTrue(all(row["quiz_grade"] is None for row in raw["rows"]))
        self.assertEqual(raw["classifications"][0]["category"], INSUFFICIENT)
        self.assertEqual(raw["analyzed"], 0)
        self.assertEqual(self.raw(course_code="")["analyzed"], 0)
        self.assertEqual(self.report(offering_id=self.offering_a.id)["charts"]["line"], [])

    def test_blocker_inactive_student_does_not_invalidate_remaining_class(self):
        students = self.complete_students(6)
        self.deny_aggregate_access()
        inactive = students[-1]
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(raw["analyzed"], 1)
        self.assertEqual(raw["rows"][1]["exam"], Decimal("85.00"))
        self.assertEqual([row["matched_students"] for row in raw["charts"]["line"]], [5, 5])
        self.assertEqual(StudentPeriodGrade.objects.filter(offering=self.offering_a, student=inactive).count(), 2)

    def test_leadership_sparse_component_visibility_and_direct_deny_reauthorization(self):
        students = self.complete_students(5)
        StudentPeriodGrade.objects.filter(offering=self.offering_a).exclude(student=students[0]).update(exam_grade=None)
        StudentPeriodGrade.objects.filter(offering=self.offering_a, student=students[0]).update(exam_grade="61.37")
        self.assertEqual(self.raw(offering_id=self.offering_a.id)["rows"][0]["exam"], Decimal("61.37"))
        prior = self.report(offering_id=self.offering_a.id)
        self.area_chair.is_superuser = True
        self.area_chair.save(update_fields=["is_superuser"])
        self.deny_aggregate_access()
        self.assertEqual(self.post("page", token=prior["token"]).status_code, 409)
        self.assertNotIn("61.37", self.post(offering_id=self.offering_a.id).content.decode())

    def test_leadership_matched_five_visibility_is_preserved(self):
        students = self.complete_students(6)
        StudentActivityScore.objects.filter(activity__offering=self.offering_a,
            activity__template_period=self.midterm, student=students[-1]).delete()
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(raw["analyzed"], 1)
        self.assertEqual([row["matched_students"] for row in raw["charts"]["line"]], [5, 5])
        self.deny_aggregate_access()
        self.assertEqual(self.raw(offering_id=self.offering_a.id)["charts"]["line"], [])

    def test_matched_five_and_excluded_five_are_safe_without_leadership(self):
        students = self.complete_students(10)
        self.deny_aggregate_access()
        StudentActivityScore.objects.filter(activity__offering=self.offering_a,
            activity__template_period=self.midterm, student__in=students[-5:]).delete()
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(raw["analyzed"], 1)
        self.assertEqual([row["matched_students"] for row in raw["charts"]["line"]], [5, 5])
        self.assertEqual(raw["rows"][0]["exam"], Decimal("75.00"))

    def test_component_minimum_and_complement_are_checked_independently(self):
        students = self.complete_students(10)
        self.deny_aggregate_access()
        StudentPeriodGrade.objects.filter(offering=self.offering_a, student__in=students[5:]).update(exam_grade=None)
        self.assertEqual(self.raw(offering_id=self.offering_a.id)["rows"][0]["exam"], Decimal("75.00"))
        StudentPeriodGrade.objects.filter(offering=self.offering_a, student=students[4]).update(exam_grade=None)
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertIsNone(raw["rows"][0]["exam"])
        self.assertEqual(raw["rows"][0]["standing"], Decimal("85.00"))
        self.assertEqual(raw["analyzed"], 0)

    def test_full_active_roster_from_related_report_protects_its_complement(self):
        students = self.complete_students(6)
        self.deny_aggregate_access()
        StudentPeriodGrade.objects.filter(offering=self.offering_a, student=students[-1]).update(is_finalized=False)
        exam = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.period, template_component=self.pre_exam, title="Related report exam", total_score=100)
        for student in students:
            self._score(exam, student, 75, 75)
        # The original report can release a full six-student aggregate from its
        # official calculator. The preview must not add the complementary five.
        legacy = self.client.post(reverse("admin_portal:academic_performance_insights"), {
            **{k: v for k, v in self.filters.items() if v is not None and k != "offering_id"},
            "period_code": "PRELIM", "action": "report",
        })
        self.assertEqual(legacy.status_code, 200, legacy.content)
        self.assertEqual(legacy.json()["summary"]["disclosed"], 1)
        self.assertFalse(StudentPeriodGrade.objects.get(offering=self.offering_a,
            student=students[-1], template_period=self.period).is_finalized)
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(raw["charts"]["line"], [])
        self.assertEqual(raw["analyzed"], 0)

    def test_privacy_denominator_retains_only_the_safe_section(self):
        students = self.complete_students(6)
        self.complete_students(5, self.offering_b)
        self.deny_aggregate_access()
        StudentActivityScore.objects.filter(activity__offering=self.offering_a,
            activity__template_period=self.midterm, student=students[-1]).delete()
        raw = self.raw(course_code="")
        self.assertEqual((raw["sections"], raw["analyzed"], raw["insufficient"]), (2, 1, 1))
        self.assertEqual([p["count"] for p in raw["patterns"]], [1, 0, 0])
        self.assertEqual([p["percentage"] for p in raw["patterns"]], [Decimal("100.00"), Decimal("0.00"), Decimal("0.00")])

    def test_inactive_historical_student_does_not_bypass_real_scope_failure(self):
        students = self.complete_students(6)
        students[-1].is_active = False
        students[-1].save(update_fields=["is_active"])
        foreign = Tenant.objects.create(code="INVALID-SAVED-SCOPE", name="Invalid saved scope")
        StudentPeriodGrade.objects.filter(offering=self.offering_a, student=students[-1]).update(tenant=foreign)
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(raw["analyzed"], 0)
        self.assertIn("scope is unavailable", raw["rows"][0]["reason"])

    def test_saved_student_eligibility_and_policy_changes_invalidate_snapshots(self):
        students = self.complete_students(6)
        student = students[-1]
        Enrollment.objects.filter(course_offering=self.offering_a, student=student).delete()
        student.is_active = False
        student.save(update_fields=["is_active"])
        data = self.report(offering_id=self.offering_a.id)
        self.assertEqual(len(data["charts"]["line"]), 2)
        student.is_active = True
        student.save(update_fields=["is_active"])
        self.assertEqual(self.post("page", token=data["token"]).status_code, 409)
        self.assertEqual(self.raw(offering_id=self.offering_a.id)["analyzed"], 0)
        with patch("apps.admin_portal.learning_insights.DISCLOSURE_POLICY_VERSION", "pre-remediation"):
            previous_policy = self.report(offering_id=self.offering_b.id)
        self.assertEqual(self.post("page", token=previous_policy["token"]).status_code, 409)

    def test_initial_get_is_filters_only_and_old_menu_is_unchanged(self):
        with patch.object(views, "build_report") as compute:
            response = self.client.get(self.url, {**{k: v for k, v in self.filters.items() if v is not None}, "action": "report"})
        compute.assert_not_called()
        self.assertContains(response, '<h1 class="h3 mb-2">Academic Performance Insights</h1>', html=True)
        self.assertContains(response, '<title>Academic Performance Insights | TeacherMate+</title>', html=True)
        self.assertContains(response, '<p class="text-muted mb-3"><em>Exploring relationships between learning activities, class standing, and examination performance.</em></p>', html=True)
        self.assertNotContains(response, '<h1 class="h3 mb-2">Learning Performance Insights</h1>', html=True)
        self.assertContains(response, "Authorized preview")
        self.assertContains(response, "View Performance")
        self.assertContains(response, reverse("admin_portal:academic_performance_insights"))
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_post_requires_csrf_and_other_methods_are_rejected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.area_chair)
        self.assertEqual(client.post(self.url, {"action": "report"}).status_code, 403)
        self.assertEqual(self.client.put(self.url).status_code, 405)

    def test_five_original_buttons_keep_labels_order_and_destinations(self):
        html = self.client.get(self.url).content.decode()
        labels = ["Midterm Exam Performance", "Activity Consistency", "Export Sections CSV", "Export Student Review CSV", "Back to Dashboard"]
        positions = [html.index(">" + label + "</a>") for label in labels]
        self.assertEqual(positions, sorted(positions))
        for route in ("midterm_exam_performance", "academic_activity_consistency", "dashboard"):
            self.assertIn(reverse("admin_portal:" + route), html)
        self.assertIn(reverse("admin_portal:academic_performance_insights") + "?export=sections", html)
        self.assertIn("does not filter either CSV", html)

    def test_five_accepted_course_offerings_remain_selectable_without_period_grades(self):
        self.course.code = "AE105-FAR"
        self.course.save(update_fields=["code"])
        inactive_offering_program = Program.objects.create(
            tenant=self.tenant, campus=self.campus, department=self.department,
            code="OLD-PROGRAM", name="Inactive offering program", is_active=False,
        )
        extra = []
        for index in range(3):
            offering = self._offering(f"AE105-{index}", self.campus, self.department, self.program)
            self._assign(offering, self.faculty)
            offering.status = "CLOSED" if index == 0 else "ARCHIVED"
            if index == 0:
                offering.program = inactive_offering_program
            offering.save(update_fields=["status", "program"])
            extra.append(offering)
        selection = LearningSelection(self.client.get(self.url).wsgi_request)
        filters, options = selection.options({**self.filters, "course_code": "AE105-FAR"})
        self.assertEqual(filters["course_code"], "AE105-FAR")
        self.assertEqual(len(options["offering_id"]), 5)
        result = build_report(selection, {**filters, "offering_id": None})
        self.assertEqual(result["sections"], 5)
        self.assertEqual(len(result["comparison_rows"]), 5)
        unavailable = [row for row in result["comparison_rows"] if row["status"] != "Comparable results available."]
        self.assertEqual(len(unavailable), 3)
        self.assertEqual(len(result["charts"]["bars"]), 5)
        self.assertEqual(result["view"], "Course Performance Comparison")
        section = build_report(selection, {**filters, "offering_id": self.offering_a.id})
        self.assertEqual(section["view"], "Individual Section Progression")
        overall = build_report(selection, {**filters, "course_code": "", "offering_id": None})
        self.assertEqual(overall["view"], "Overall Academic Performance")
        self.assertEqual(overall["sections"], 5)

    def test_paired_comparisons_recalculate_on_matched_students_including_dropped_links(self):
        midterm_activity = GradeActivity.objects.get(offering=self.offering_a, template_period=self.midterm)
        matched_students = []
        for index in range(3):
            student = self._student(self.offering_a, f"PAIR-{index}", f"Pair{index}")
            matched_students.append(student)
            if index == 0:
                Enrollment.objects.filter(course_offering=self.offering_a, student=student).update(
                    is_active=False, enrollment_status=Enrollment.Status.DRP,
                )
            for activity, score in ((self.activity_a, 20), (midterm_activity, 23)):
                self._score(activity, student, score, score)
            for period, standing, exam, periodic in ((self.period, 60, 70, 60), (self.midterm, 63, 73, 63)):
                StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
                    template_period=period, student=student, class_standing_grade=standing, exam_grade=exam,
                    period_grade=periodic, is_finalized=True)
        dropped_only = self._student(self.offering_a, "PAIR-DROPPED", "Dropped")
        Enrollment.objects.filter(course_offering=self.offering_a, student=dropped_only).update(
            is_active=False, enrollment_status=Enrollment.Status.DRP,
        )
        self._score(self.activity_a, dropped_only, 99, 99)
        StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.period, student=dropped_only, class_standing_grade=99, exam_grade=99,
            period_grade=99, is_finalized=True)

        result = self.raw(offering_id=self.offering_a.id)
        classification = result["classifications"][0]
        self.assertEqual(classification["category"], "Moved Together")
        self.assertEqual(classification["matched_students"], 5)
        self.assertEqual([row["quiz"] for row in result["charts"]["line"]], [Decimal("40.00"), Decimal("45.80")])
        self.assertEqual([row["matched_students"] for row in result["charts"]["line"]], [5, 5])
        self.assertNotIn("pattern_values", result["rows"][0])
        self.assertNotIn("student_values", result["rows"][0])
        self.assertNotIn(dropped_only.student_no, str(result))

    def test_concluded_periods_only_in_charts(self):
        data = self.report()
        self.assertEqual([r["period"] for r in data["charts"]["line"]], ["Prelim", "Midterm"])
        self.assertEqual([r["quiz"] for r in data["charts"]["line"]], ["70.00", "80.00"])
        self.assertEqual([r["count"] for r in data["charts"]["patterns"]], [2, 0, 0])

    def test_ongoing_future_locked_and_expired_periods_do_not_conclude(self):
        self.active.period = self.term_periods["PRELIM"]
        self.active.save()
        for code in ("PRELIM", "MIDTERM"):
            GradingPeriodLock.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
                term=self.term, period_code=code, is_locked=True, deadline_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.report()["charts"]["line"], [])
        self.assertEqual(self.raw()["analyzed"], 0)

    def test_missing_active_authority_is_unavailable_despite_submitted_finalized_grades(self):
        self.active.delete()
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(raw["analyzed"], 0)
        self.assertIn("active grading period is unavailable", raw["rows"][0]["reason"])

    def test_complete_unsubmitted_or_unfinalized_results_are_not_eligible(self):
        GradeSubmission.objects.filter(offering=self.offering_a, template_period=self.midterm).update(status="DRAFT")
        StudentPeriodGrade.objects.filter(offering=self.offering_b, template_period=self.midterm).update(is_finalized=False)
        self.assertEqual(self.raw()["analyzed"], 0)

    def test_missing_component_result_is_not_zero(self):
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(exam_grade=None)
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertIsNone(raw["rows"][1]["exam"])
        self.assertEqual(raw["analyzed"], 0)

    def test_reopened_and_active_approved_reopen_excluded(self):
        GradeSubmission.objects.filter(offering=self.offering_a, template_period=self.midterm).update(status="REOPENED")
        submission = GradeSubmission.objects.get(offering=self.offering_b, template_period=self.midterm)
        GradeSubmissionReopenRequest.objects.create(tenant=self.tenant, campus=self.campus, submission=submission,
            offering=self.offering_b, template_period=self.midterm, requested_by_user=self.faculty,
            justification="Approved reopen", status="APPROVED", reviewed_at=timezone.now())
        self.assertEqual(self.raw()["analyzed"], 0)

    def correction_window(self, **kwargs):
        request = GradeCorrectionRequest.objects.create(tenant=self.tenant, campus=self.campus,
            offering=self.offering_a, template_period=self.midterm, requested_by_user=self.faculty,
            status="APPROVED", justification="test")
        return GradeCorrectionUnlockWindow.objects.create(correction_request=request, offering=self.offering_a,
            template_period=self.midterm, start_at=timezone.now() - timedelta(hours=1),
            end_at=timezone.now() + timedelta(hours=1), **kwargs)

    def test_active_correction_excluded_without_lifecycle_writes(self):
        self.correction_window()
        request = self.client.get(self.url).wsgi_request
        selection = LearningSelection(request)
        with CaptureQueriesContext(connection) as captured:
            raw = build_report(selection, self.filters)
        self.assertEqual(raw["analyzed"], 1)
        self.assertFalse([q["sql"] for q in captured if q["sql"].lstrip().upper().startswith(("UPDATE ", "INSERT ", "DELETE "))])

    def test_expired_correction_does_not_require_mutating_cleanup(self):
        window = self.correction_window()
        GradeCorrectionUnlockWindow.objects.filter(pk=window.pk).update(end_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.raw()["analyzed"], 2)
        window.refresh_from_db()
        self.assertFalse(window.is_consumed)

    def test_terminal_final_requires_explicit_lock_deadline_and_finalized_submission(self):
        self.active.period = self.term_periods["FINAL"]
        self.active.save()
        # Copy the known final configuration/evidence. Date alone cannot conclude it.
        for component, is_exam in ((self.component, False), (self.pre_exam, True)):
            GradingTemplateComponent.objects.create(template_period=self.final, code=component.code, name=component.name,
                weight_percentage=component.weight_percentage, is_exam_component=is_exam)
        for g in StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.period):
            g.pk = None; g.template_period = self.final; g.save()
        GradeSubmission.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.final, status="SUBMITTED", submitted_at=timezone.now() - timedelta(days=1))
        self.assertFalse(self.raw(offering_id=self.offering_a.id)["rows"][-1]["eligible"])
        GradingPeriodLock.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, period_code="FINAL", is_locked=True, deadline_at=timezone.now() - timedelta(hours=1))
        self.assertTrue(self.raw(offering_id=self.offering_a.id)["rows"][-1]["eligible"])

    def test_saved_zero_is_valid_and_missing_quiz_score_is_unavailable(self):
        StudentActivityScore.objects.filter(activity=self.activity_a).update(raw_score=0, computed_score=50)
        row = self.raw(offering_id=self.offering_a.id)["rows"][0]
        self.assertEqual(row["quiz"], 0)
        self.assertEqual(row["quiz_grade"], Decimal("50"))
        self.assertEqual(row["quizzes"], 1)
        StudentActivityScore.objects.filter(activity=self.activity_a, student=self.student_a1).delete()
        row = self.raw(offering_id=self.offering_a.id)["rows"][0]
        self.assertIsNone(row["quiz"])
        self.assertIsNone(row["quiz_grade"])

    def test_counts_are_distinct_and_class_standing_includes_quiz_subset_not_exam(self):
        homework = GradingTemplateSubcomponent.objects.create(template_component=self.component, code="HOMEWORK", name="Homework", weight_percentage=0)
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.period, template_component=self.component, template_subcomponent=homework, title="Quiz title does not classify this", total_score=100)
        exam = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.period, template_component=self.pre_exam, title="Quiz", total_score=100)
        for a in (activity, exam):
            self._score(a, self.student_a1, 0, 50)
            self._score(a, self.student_a2, 50, 75)
        row = self.raw(offering_id=self.offering_a.id)["rows"][0]
        self.assertEqual((row["quizzes"], row["activities"]), (1, 2))

    def test_excused_inactive_and_invalid_evidence_is_excluded(self):
        for value in ({"is_excused": True}, {"is_active": False}, {"raw_score": 101}):
            StudentActivityScore.objects.filter(activity=self.activity_a).update(is_excused=False, is_active=True, raw_score=70)
            StudentActivityScore.objects.filter(activity=self.activity_a).update(**value)
            row = self.raw(offering_id=self.offering_a.id)["rows"][0]
            self.assertIsNone(row["quiz"])
            self.assertIsNone(row["quizzes"])

    def test_multiple_major_buckets_keep_original_points_off_chart(self):
        GradingTemplateComponent.objects.create(template_period=self.period, code="LAB", name="Lab", weight_percentage=0)
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.period).update(class_standing_grade=160)
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(raw["rows"][0]["standing"], 160)
        self.assertFalse(raw["rows"][0]["scales"]["standing"])
        self.assertEqual(raw["charts"]["line_mode"], "descriptive")
        self.assertIsNone(raw["charts"]["line"][0]["standing"])

    def test_changing_configuration_or_cohort_prevents_longitudinal_classification(self):
        self.mid_cs.weight_percentage = 60
        self.mid_cs.save()
        self.assertEqual(self.raw()["analyzed"], 0)
        self.mid_cs.weight_percentage = 70
        self.mid_cs.save()
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm, student=self.student_a2).delete()
        result = self.raw()
        self.assertEqual(result["analyzed"], 2)
        self.assertEqual(result["classifications"][0]["matched_students"], 1)

    def test_pattern_rules_include_decline_opposite_mixed_and_stable(self):
        def row(sequence, quiz, standing, exam):
            return {"eligible": True, "period": str(sequence), "sequence": sequence, "signature": (1,),
                    "cohort": {1, 2}, "quiz": Decimal(quiz), "standing": Decimal(standing), "exam": Decimal(exam),
                    "scales": {"standing": True, "exam": True}, "quizzes": 2, "activities": 3}
        first = row(1, 70, 80, 75)
        for values, expected, detail in (((80, 85, 90), PATTERNS[0], "Improved"), ((60, 70, 65), PATTERNS[0], "Declined"),
                                         ((80, 85, 70), PATTERNS[1], "opposite"), ((70, 85, 90), PATTERNS[2], "stable"),
                                         ((80, 75, 90), PATTERNS[2], "Mixed")):
            result = classify([first, row(2, *values)])
            self.assertEqual(result["category"], expected)
            self.assertIn(detail, result["detail"])
        self.assertEqual(classify([first])["category"], INSUFFICIENT)

    def test_most_recent_comparable_pair_skips_incompatible_period(self):
        base = {"eligible": True, "signature": (1,), "cohort": {1}, "quiz": Decimal(50), "standing": Decimal(60), "exam": Decimal(70),
                "scales": {"standing": True, "exam": True}, "quizzes": 1, "activities": 1}
        history = [{**base, "period": "Prelim", "sequence": 1}, {**base, "period": "Midterm", "sequence": 2},
                   {**base, "period": "Pre-Final", "sequence": 3, "signature": (2,)}]
        result = classify(history)
        self.assertEqual((result["from"], result["to"]), ("Prelim", "Midterm"))

    def test_percentages_use_sections_once_and_exclude_insufficient(self):
        StudentActivityScore.objects.filter(activity__offering=self.offering_b, activity__template_period=self.midterm).delete()
        raw = self.raw()
        self.assertEqual((raw["sections"], raw["analyzed"], raw["insufficient"]), (2, 1, 1))
        self.assertEqual(raw["patterns"][0]["percentage"], 100)
        self.assertEqual(raw["rows"][1]["quiz_sections"], 1)

    def test_unrelated_courses_do_not_pool_raw_examination_averages(self):
        other = Course.objects.create(tenant=self.tenant, code="OTHER", title="Other")
        self.offering_b.course = other
        self.offering_b.save()
        raw = self.raw(course_code="")
        self.assertEqual(raw["analyzed"], 2)
        self.assertIsNone(raw["rows"][0]["exam"])
        self.assertEqual(raw["rows"][0]["quizzes"], 1)
        self.assertEqual(raw["charts"]["bars"], [])

    def test_null_offering_program_uses_valid_section_program_only_in_preview(self):
        self.offering_a.program = None
        self.offering_a.save()
        request = self.client.get(self.url).wsgi_request
        self.assertIn(self.offering_a.id, LearningSelection(request).offerings)
        self.assertNotIn(self.offering_a.id, Selection(request).offerings)
        self.assertEqual(self.raw()["sections"], 2)
        self.program.is_active = False
        self.program.save()
        self.assertEqual(self.raw()["sections"], 0)

    def test_faculty_assignment_and_section_filters_cannot_expand_supervision(self):
        FacultyAssignment.objects.filter(offering=self.offering_b).update(response_status="PENDING")
        data = self.post("options").json()
        self.assertEqual([r["value"] for r in data["options"]["offering_id"]], [self.offering_a.id])
        self.assertEqual(self.post(offering_id=self.offering_b.id).status_code, 400)

    def test_invalid_campus_tenant_course_section_and_numeric_filters_fail_closed(self):
        foreign = Tenant.objects.create(code="FOREIGN", name="Foreign")
        for filters in ({"campus_id": 999999}, {"term_id": foreign.id + 999999}, {"course_code": "NOPE"},
                        {"offering_id": 999999}, {"campus_id": "all"}, {"offering_id": "-1"}):
            self.assertEqual(self.post(**filters).status_code, 400)

    def test_cao_multiple_campuses_and_campus_deny_keep_actual_assignment_coverage(self):
        campus = Campus.objects.create(tenant=self.tenant, code="SECOND", name="Second campus")
        department = Department.objects.create(tenant=self.tenant, campus=campus, code="OTHER", name="Other department")
        program = Program.objects.create(tenant=self.tenant, campus=campus, department=department, code="OTHER", name="Other program")
        faculty = self._user("other-campus-faculty", campus, department)
        UserRole.objects.create(user=faculty, role=self.faculty_role, tenant=self.tenant, campus=campus, department=department)
        offering = self._offering("OTHER-SECTION", campus, department, program)
        offering.program = None; offering.save()
        self._assign(offering, faculty)
        cao = self._user("cao-preview", self.campus, self.department)
        role = self._role_with_access("CAO", "CAO")
        for c in (self.campus, campus):
            UserRole.objects.create(user=cao, role=role, tenant=self.tenant, campus=c)
        self.client.force_login(cao)
        options = self.post("options", campus_id=campus.id).json()["options"]
        self.assertEqual([r["value"] for r in options["offering_id"]], [offering.id])
        permission = Permission.objects.get(code="grading_analytics.read")
        UserPermission.objects.create(user=cao, permission=permission, tenant=self.tenant, campus=campus, grant_type="DENY")
        options = self.post("options", campus_id="").json()["options"]
        self.assertNotIn(campus.id, [r["value"] for r in options["campus_id"]])
        self.assertEqual(self.post(campus_id=campus.id).status_code, 400)

    def test_real_cross_tenant_related_records_are_excluded(self):
        foreign = Tenant.objects.create(code="FOREIGN", name="Foreign")
        foreign_course = Course.objects.create(tenant=foreign, code="FOREIGN", title="Foreign course")
        self.offering_b.course = foreign_course; self.offering_b.save()
        self.assertEqual(self.raw()["sections"], 1)
        self.assertEqual(self.post(offering_id=self.offering_b.id).status_code, 400)

    def test_changed_excluded_class_correction_still_invalidates_full_scope_privacy_basis(self):
        data = self.report(offering_id=self.offering_b.id)
        self.correction_window()  # Different class, still in the full authorized disclosure basis.
        self.assertEqual(self.post("page", token=data["token"]).status_code, 409)

    def test_active_period_and_window_reads_never_call_mutating_governance_helpers(self):
        with patch("apps.academics.services.AcademicGovernanceService.resolve_active_grading_period", side_effect=AssertionError("mutating reader")), \
             patch("apps.grading.services.GradingGovernanceService.get_active_unlock_window", side_effect=AssertionError("cleanup")), \
             patch("apps.grading.services.FacultyGradingService.recompute_period_summary", side_effect=AssertionError("grade write")):
            self.assertEqual(self.raw()["analyzed"], 2)

    def test_direct_deny_overrides_even_superuser_and_no_aggregate_role_title_bypass(self):
        self.area_chair.is_superuser = True
        self.area_chair.save()
        permission = Permission.objects.get(code="grading_analytics.read")
        UserPermission.objects.create(user=self.area_chair, permission=permission, tenant=self.tenant,
            campus=self.campus, grant_type="DENY")
        self.assertIn(self.post().status_code, (400, 403))

    def test_small_class_aggregate_permission_and_separate_privacy_rules(self):
        self.assertEqual(self.raw()["analyzed"], 2)
        RolePermission.objects.filter(role=self.area_role, permission=self.permission).delete()
        raw = self.raw()
        self.assertEqual(raw["analyzed"], 0)
        self.assertEqual(raw["charts"]["line"], [])

    def test_direct_aggregate_deny_reinstates_small_class_suppression(self):
        UserPermission.objects.create(user=self.area_chair, permission=self.permission, tenant=self.tenant,
            campus=self.campus, grant_type="DENY")
        self.assertEqual(self.raw()["analyzed"], 0)

    def test_longitudinal_membership_suppression_precedes_course_and_section_filters(self):
        RolePermission.objects.filter(role=self.area_role, permission=self.permission).delete()
        # Both classes have five students, but one shared member creates a small disclosure atom.
        for offering in (self.offering_a, self.offering_b):
            for i in range(3):
                student = self._student(offering, f"EXTRA-{offering.id}-{i}", "Extra")
                for period, cs, exam, periodic in ((self.period, 85, 75, 82), (self.midterm, 90, 85, 89)):
                    StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=offering,
                        template_period=period, student=student, class_standing_grade=cs, exam_grade=exam,
                        period_grade=periodic, is_finalized=True)
                    activity = GradeActivity.objects.get(offering=offering, template_period=period)
                    self._score(activity, student, 70 if period == self.period else 80, cs)
        self.assertEqual(self.raw()["analyzed"], 2)
        Enrollment.objects.create(tenant=self.tenant, campus=self.campus, academic_year=self.academic_year,
            term=self.term, course_offering=self.offering_b, student=self.student_a1)
        for period in (self.period, self.midterm):
            source = StudentPeriodGrade.objects.get(offering=self.offering_a, template_period=period, student=self.student_a1)
            source.pk = None; source.offering = self.offering_b; source.save()
            activity = GradeActivity.objects.get(offering=self.offering_b, template_period=period)
            self._score(activity, self.student_a1, 70 if period == self.period else 80, source.class_standing_grade)
        self.assertEqual(self.raw(offering_id=self.offering_a.id)["analyzed"], 0)

    def test_clock_boundary_invalidates_cached_window_decision(self):
        window = self.correction_window()
        past = timezone.now() - timedelta(minutes=1)
        GradeCorrectionUnlockWindow.objects.filter(pk=window.pk).update(start_at=past - timedelta(hours=1), end_at=past)
        data = self.report()
        with patch("apps.admin_portal.learning_insights.timezone.now", return_value=past - timedelta(seconds=1)):
            self.assertEqual(self.post("page", token=data["token"]).status_code, 409)

    def test_large_section_lists_are_paginated_without_truncating_pattern_denominator(self):
        for i in range(25):
            offering = self._offering(f"EXTRA-SECTION-{i}", self.campus, self.department, self.program)
            self._assign(offering, self.faculty)
        data = self.report()
        self.assertNotIn("EXTRA-SECTION-9:", data["html"])
        self.assertIn("Section page 1 of 2", data["html"])
        self.assertEqual(sum(p["count"] for p in data["charts"]["patterns"]), 2)
        response = self.post("page", token=data["token"], page=2)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Section page 2 of 2", response.json()["html"])

    def test_student_identities_never_appear_in_response(self):
        response = self.post()
        for student in (self.student_a1, self.student_a2, self.student_b1):
            self.assertNotIn(student.student_no, response.content.decode())
        self.assertNotIn('"cohort"', response.content.decode())
        self.assertNotIn('"signature"', response.content.decode())
        self.assertNotIn('"comparison_values"', response.content.decode())
        self.assertNotIn('"pattern_values"', response.content.decode())
        self.assertIn("Period averages describe each period’s valid class data", response.json()["html"])

    def test_unrounded_class_and_quiz_means_do_not_cross_threshold(self):
        # Displayed changes round to +3, but the saved observations establish +2.995.
        StudentActivityScore.objects.filter(activity__offering=self.offering_a, activity__template_period=self.midterm).update(raw_score="73.00")
        StudentActivityScore.objects.filter(activity__offering=self.offering_a, activity__template_period=self.midterm,
            student=self.student_a1).update(raw_score="72.99")
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(class_standing_grade="88.00", exam_grade="78.00")
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm,
            student=self.student_a1).update(class_standing_grade="87.99", exam_grade="77.99")
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual(raw["classifications"][0]["category"], "Mixed / Stable")
        self.assertEqual(raw["classifications"][0]["changes"], dict.fromkeys(("quiz", "standing", "exam"), "STABLE"))
        self.assertEqual([r["quiz"] for r in raw["charts"]["line"]], [Decimal("70.00"), Decimal("73.00")])
        self.assertEqual([r["standing"] for r in raw["charts"]["line"]], [Decimal("85.00"), Decimal("88.00")])
        self.assertEqual([r["exam"] for r in raw["charts"]["line"]], [Decimal("75.00"), Decimal("78.00")])
        section_bar = next((bar for bar in raw["charts"]["bars"]
                            if bar["section"].startswith(f"{self.offering_a.section.code} /")), None)
        self.assertIsNotNone(section_bar, (raw["charts"]["bar_period"], raw["comparison_note"], raw["comparison_rows"]))
        self.assertEqual(section_bar["quiz"], Decimal("73.00"))
        self.assertNotIn("comparison_values", raw["rows"][0])

    def test_repeating_quiz_percentages_retain_exact_three_point_boundaries(self):
        GradeActivity.objects.filter(offering=self.offering_a).update(total_score=300)
        for first, second, grade, expected, status in ((100, 109, (88, 78), "Improved together", "IMPROVING"),
                                                       (109, 100, (82, 72), "Declined together", "DECLINING")):
            StudentActivityScore.objects.filter(activity=self.activity_a).update(raw_score=first)
            StudentActivityScore.objects.filter(activity__offering=self.offering_a, activity__template_period=self.midterm).update(raw_score=second)
            StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(class_standing_grade=grade[0], exam_grade=grade[1])
            result = self.raw(offering_id=self.offering_a.id)["classifications"][0]
            self.assertEqual(result["category"], "Moved Together")
            self.assertEqual(result["detail"], expected)
            self.assertEqual(result["changes"], dict.fromkeys(("quiz", "standing", "exam"), status))

    def test_multi_bucket_original_units_are_excluded_from_pattern_denominator(self):
        for period in (self.period, self.midterm):
            GradingTemplateComponent.objects.create(template_period=period, code="LAB", name="Lab", weight_percentage=0)
        StudentPeriodGrade.objects.filter(template_period=self.period).update(class_standing_grade=150)
        StudentPeriodGrade.objects.filter(template_period=self.midterm).update(class_standing_grade=160)
        raw = self.raw(offering_id=self.offering_a.id)
        self.assertEqual((raw["analyzed"], raw["insufficient"]), (0, 1))
        self.assertEqual(raw["rows"][0]["standing"], 150)
        self.assertEqual(raw["classifications"][0]["category"], INSUFFICIENT)
        self.assertTrue(all(p["count"] == 0 and p["percentage"] is None for p in raw["patterns"]))

    def test_threshold_pattern_counts_and_denominators_exclude_unavailable_sections(self):
        for period, raw_score in ((self.period, "70.00"), (self.midterm, "72.99")):
            StudentActivityScore.objects.filter(activity__offering=self.offering_a, activity__template_period=period).update(raw_score=raw_score)
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(class_standing_grade="87.99", exam_grade="77.99")
        offering = self._offering("NO-FINALIZED-RESULTS", self.campus, self.department, self.program)
        self._assign(offering, self.faculty)
        raw = self.raw()
        self.assertEqual((raw["sections"], raw["analyzed"], raw["insufficient"]), (3, 2, 1))
        self.assertEqual([(p["label"], p["count"], p["percentage"]) for p in raw["patterns"]],
                         [("Moved Together", 1, Decimal("50.00")), ("Moved Opposite", 0, Decimal("0.00")), ("Mixed / Stable", 1, Decimal("50.00"))])
        data = self.report()
        self.assertEqual(data["charts"]["threshold"], "3.00")
        self.assertEqual([p["count"] for p in data["charts"]["patterns"]], [1, 0, 1])
        self.assertIn("at least 3.00 percentage points", data["html"])
        self.assertIn("Mixed / Stable", data["html"])
        self.assertNotIn("Mixed / Unchanged", data["html"])

    def test_scope_bound_token_cache_miss_and_record_changes(self):
        data = self.report()
        cache.clear()
        self.assertEqual(self.post("page", token=data["token"], page=1).status_code, 200)
        StudentActivityScore.objects.filter(activity=self.activity_a).update(raw_score=20)
        response = self.post("page", token=data["token"], page=1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "records_changed")

    def test_threshold_policy_change_invalidates_cached_classification(self):
        with patch("apps.admin_portal.learning_insights.MEANINGFUL_CHANGE", Decimal("2.00")):
            data = self.report()
        self.assertEqual(self.post("page", token=data["token"]).status_code, 409)

    def test_governance_and_correction_changes_invalidate_report(self):
        data = self.report()
        self.active.period = self.term_periods["MIDTERM"]
        self.active.save()
        self.assertEqual(self.post("page", token=data["token"]).status_code, 409)
        self.active.period = self.term_periods["PREFINAL"]
        self.active.save()
        data = self.report()
        self.correction_window()
        self.assertEqual(self.post("page", token=data["token"]).status_code, 409)

    def test_change_during_report_rejected(self):
        original = views.build_report
        def changing(*args, **kwargs):
            result = original(*args, **kwargs)
            StudentPeriodGrade.objects.filter(offering=self.offering_a).update(exam_grade=20)
            return result
        with patch.object(views, "build_report", side_effect=changing):
            self.assertEqual(self.post().status_code, 409)

    def test_expired_forged_or_another_users_token_is_rejected(self):
        data = self.report()
        self.assertEqual(self.post("page", token="forged").status_code, 409)
        with patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 400):
            self.assertEqual(self.post("page", token=data["token"]).status_code, 409)
        other = self._user("other-chair", self.campus, self.department)
        UserRole.objects.create(user=other, role=self.area_role, tenant=self.tenant, campus=self.campus, department=self.department)
        self.client.force_login(other)
        self.assertEqual(self.post("page", token=data["token"]).status_code, 409)

    def test_feature_gate_remains_shared_and_original_export_content_unchanged(self):
        old_url = reverse("admin_portal:academic_performance_insights")
        before = self.client.get(old_url, {"academic_year_id": self.academic_year.id, "term_id": self.term.id, "period_code": "PRELIM", "export": "sections"}).content
        self.report()
        after = self.client.get(old_url, {"academic_year_id": self.academic_year.id, "term_id": self.term.id, "period_code": "PRELIM", "export": "sections"}).content
        self.assertEqual(before, after)
        SystemSettingService.set(FeatureSettingsService.ACADEMIC_PERFORMANCE_INSIGHTS_ENABLED_KEY, False,
            tenant_id=self.tenant.id, value_type="BOOL", is_active=True)
        self.assertEqual(self.client.get(self.url).status_code, 404)


class PercentagePointThresholdTests(SimpleTestCase):
    @staticmethod
    def row(sequence, quiz="80", standing="80", exam="80", **overrides):
        return {"eligible": True, "period": str(sequence), "sequence": sequence, "signature": (1,),
                "cohort": {1, 2}, "scales": {"standing": True, "exam": True},
                "quiz": Decimal(quiz) if quiz is not None else None,
                "standing": Decimal(standing) if standing is not None else None,
                "exam": Decimal(exam) if exam is not None else None,
                "quizzes": 1, "activities": 2, **overrides}

    def test_exact_positive_boundary_is_improving(self):
        self.assertEqual(performance_change(Decimal("80"), Decimal("83.00")), "IMPROVING")

    def test_exact_negative_boundary_is_declining(self):
        self.assertEqual(performance_change(Decimal("80"), Decimal("77.00")), "DECLINING")

    def test_immediately_inside_boundaries_and_unchanged_are_stable(self):
        for value in ("82.99", "77.01", "80", "82.999999999999999999", "77.000000000000000001"):
            with self.subTest(value=value):
                self.assertEqual(performance_change(Decimal("80"), Decimal(value)), "STABLE")

    def test_mean_comparisons_do_not_round_before_classification(self):
        for previous, current in (("80.004", "83.003"), ("79.996", "76.997")):
            self.assertEqual(performance_change(Decimal(previous), Decimal(current)), "STABLE")
        self.assertEqual(performance_change(Fraction(241, 3), Fraction(250, 3)), "IMPROVING")
        self.assertEqual(performance_change(Fraction(250, 3), Fraction(241, 3)), "DECLINING")

    def test_missing_values_have_no_stable_classification(self):
        self.assertIsNone(performance_change(None, Decimal("80")))
        self.assertIsNone(performance_change(Decimal("80"), None))

    def test_both_together_and_opposite_patterns_use_meaningful_directions(self):
        for values, category, statuses in (
            (("83", "83", "83"), "Moved Together", ("IMPROVING",) * 3),
            (("77", "77", "77"), "Moved Together", ("DECLINING",) * 3),
            (("83", "83", "77"), "Moved Opposite", ("IMPROVING", "IMPROVING", "DECLINING")),
            (("77", "77", "83"), "Moved Opposite", ("DECLINING", "DECLINING", "IMPROVING")),
            (("82.99", "82.99", "77.01"), "Mixed / Stable", ("STABLE",) * 3),
            (("83", "82.99", "83"), "Mixed / Stable", ("IMPROVING", "STABLE", "IMPROVING")),
            (("83", "77", "83"), "Mixed / Stable", ("IMPROVING", "DECLINING", "IMPROVING")),
            (("80", "80", "80"), "Mixed / Stable", ("STABLE",) * 3),
        ):
            with self.subTest(values=values):
                result = classify([self.row(1), self.row(2, *values)])
                self.assertEqual(result["category"], category)
                self.assertEqual(tuple(result["changes"][m] for m in ("quiz", "standing", "exam")), statuses)

    def test_unverified_units_missing_evidence_and_incompatible_periods_are_insufficient(self):
        for overrides in ({"scales": {}}, {"scales": {"standing": False, "exam": True}},
                          {"scales": {"standing": True, "exam": False}}, {"signature": (2,)},
                          {"cohort": {1}}, {"eligible": False}, {"quiz": None}, {"exam": None}):
            with self.subTest(overrides=overrides):
                self.assertEqual(classify([self.row(1), self.row(2, **overrides)])["category"], INSUFFICIENT)
