"""Finalized disposable fixtures for the unified period-wide Gradebook table."""
from datetime import timedelta
from copy import deepcopy
from decimal import Decimal
import json
import re
from unittest.mock import patch

from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.admin_portal import tests_unified_insights as unified_fixtures
from apps.admin_portal import assessment_table, learning_insights
from apps.grading.models import (GradeActivity, GradeSubmission, GradingTemplateSubcomponent,
    GradingTemplateDetail, StudentActivityScore, StudentPeriodGrade, CourseBaseValueOverride)
from apps.rbac.models import UserPermission


class AssessmentTableTests(TestCase):
    for _name in ("_enable_feature", "_role_with_access", "_user", "_offering", "_assign", "_student",
                  "_activity", "_score", "complete_students", "bsa_period_definitions", "post", "report", "source"):
        locals()[_name] = unified_fixtures.UnifiedTests.__dict__[_name]

    def setUp(self):
        unified_fixtures.UnifiedTests.setUp(self)
        self.seal()

    def seal(self):
        GradeSubmission.objects.filter(offering=self.offering_a).update(submitted_at=timezone.now())

    def data(self, **filters):
        return self.source(tab="overview", **filters)[0]

    def period_row(self, period=None, result=None):
        period = period or self.midterm
        return next(row for row in (result or self.data())["assessment_period_rows"]
                    if row["id"] == self.offering_a.pk and row["period_id"] == period.pk)

    def metric(self, code, period=None, result=None):
        return next(m for m in self.period_row(period, result)["measures"] if m["code"] == code)

    def output(self, period=None, kind="grade", result=None):
        return next(m for m in self.period_row(period, result)["measures"]
                    if m["code"].startswith("output_") and m["kind"] == kind)

    def test_all_seven_measures_and_template_periods_precede_charts(self):
        data = self.data()
        self.assertEqual({r["period_id"] for r in data["assessment_period_rows"]},
                         {self.period.pk, self.midterm.pk, self.prefinal.pk, self.final.pk})
        row = self.period_row(result=data)
        self.assertEqual(len(row["measures"]), 7)
        self.assertEqual(self.metric("quiz_grade", result=data)["value"], Decimal("68.02"))
        self.assertEqual(self.output(result=data)["value"], 85)
        self.assertEqual(self.output(kind="count", result=data)["value"], 1)
        response = self.report(tab="overview")
        html = response["html"]
        self.assertLess(html.index("Class Assessment Performance by Period"), html.index('id="learning-line"'))
        self.assertIn("Class Period Results", html)
        self.assertIn("Gradebook Points", html)
        self.assertNotIn("population", str(data["assessment_period_rows"]))

    def test_saved_official_grades_are_not_replaced_by_periodic_or_recalculation(self):
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(
            exam_grade=61, class_standing_grade=83, period_grade=72)
        with patch.object(learning_insights.FacultyGradingService, "recompute_period_summary", side_effect=AssertionError("write calculator")):
            self.assertEqual(self.metric("exam")["value"], 61)
            self.assertEqual(self.metric("standing")["value"], 83)
            self.assertEqual(self.metric("periodic")["value"], 72)

    def test_actual_base_and_direct_mode_never_reconvert_saved_grades(self):
        sub = self.mid_cs.subcomponents.get(code__contains="PARTICIPATION_OUTPUT")
        for mode, base, raw, computed in (("RAW_BASE50", 50, 0, 50),
                                        ("RAW_BASE50", 20, 20, 84),
                                        ("DIRECT_PERCENTAGE", 50, 36, 36)):
            with self.subTest(mode=mode, base=base):
                sub.score_input_mode = mode
                sub.save()
                CourseBaseValueOverride.objects.update_or_create(course=self.course, effective_from_term=self.term,
                    defaults={"base_value": base})
                StudentActivityScore.objects.filter(activity__offering=self.offering_a,
                    activity__template_subcomponent=sub).update(raw_score=raw, computed_score=computed)
                self.seal()
                self.assertEqual(self.output()["value"], computed)

    def weighted_output(self):
        sub = self.mid_cs.subcomponents.get(code__contains="PARTICIPATION_OUTPUT")
        details = list(sub.details.order_by("pk"))
        sub.details.update(weight_percentage=0)
        for d, weight in zip(details, (60, 40)):
            d.weight_percentage = weight
            d.save()
        first = GradeActivity.objects.get(offering=self.offering_a, template_subcomponent=sub)
        first.template_detail = details[0]
        first.save()
        StudentActivityScore.objects.filter(activity=first).update(raw_score=60, computed_score=80)
        second = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.midterm, template_component=self.mid_cs, template_subcomponent=sub,
            template_detail=details[1], title="Output 2", total_score=100)
        for student in self.students:
            self._score(second, student, 80, 90)
        sub.detail_computation_mode = "WEIGHTED_DETAILS"
        sub.save()
        self.seal()
        return sub, details

    def weighted_quiz_observation(self):
        self.active.period = self.term_periods["PREFINAL"]
        self.active.save()
        self.mid_quiz.detail_computation_mode = "WEIGHTED_DETAILS"
        self.mid_quiz.save()
        details = [GradingTemplateDetail.objects.create(template_subcomponent=self.mid_quiz,
            code=f"MIDTERM_CA_QUIZZES_{label}", name=label, weight_percentage=weight)
            for label, weight in (("A", 60), ("B", 40))]
        first = GradeActivity.objects.get(offering=self.offering_a, template_subcomponent=self.mid_quiz)
        first.template_detail = details[0]
        first.save()
        StudentActivityScore.objects.filter(activity=first).update(raw_score=60, computed_score=80)
        second = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.midterm, template_component=self.mid_cs, template_subcomponent=self.mid_quiz,
            template_detail=details[1], title="Quiz B", total_score=100)
        for student in self.students:
            self._score(second, student, 80, 90)
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(exam_grade=Decimal("68.48"))
        self.seal()
        return details

    def test_post_submission_quiz_retirement_never_enters_observation(self):
        from apps.admin_portal.unified_insights import present
        details = self.weighted_quiz_observation()
        result, selection, params = self.source(tab="overview")
        context, charts = present(result, selection, params, "overview", 1)
        self.assertEqual(self.metric("quiz_grade", result=result)["value"], 84)
        self.assertIn("Quiz average 84.00", str(context["section_observations"]))
        unaffected = {m["code"]: m["value"] for m in self.period_row(result=result)["measures"]
                      if m["kind"] == "grade" and m["code"] != "quiz_grade"}
        previous = self.report(tab="overview")
        saved = list(StudentPeriodGrade.objects.filter(offering=self.offering_a).order_by("pk").values())
        details[1].is_active = False
        details[1].save()
        self.assertGreater(details[1].updated_at, GradeSubmission.objects.get(
            offering=self.offering_a, template_period=self.midterm).submitted_at)
        self.assertEqual(self.post("verify", tab="overview", token=previous["token"]).status_code, 409)
        result, selection, params = self.source(tab="overview")
        self.assertEqual(self.metric("quiz_grade", result=result)["state"], assessment_table.HISTORICAL_CHANGED)
        context, charts = present(result, selection, params, "overview", 1)
        self.assertEqual(charts["bars"][0]["quiz_grade"], 80)  # Existing chart contract is preserved.
        self.assertNotIn("Quiz average 80.00", str(context["section_observations"]))
        self.assertNotIn("11.52", str(context["section_observations"]))
        self.assertIn("cannot be verified", str(context["section_observations"]))
        self.assertEqual(unaffected, {m["code"]: m["value"] for m in self.period_row(result=result)["measures"]
                                     if m["kind"] == "grade" and m["code"] != "quiz_grade"})
        for filters in ({}, {"offering_id": ""}, {"offering_id": "", "course_code": ""}):
            result, selection, params = self.source(tab="overview", **filters)
            context, charts = present(result, selection, params, "overview", 1)
            self.assertNotIn("Quiz average 80.00", str(context["section_observations"]))
            self.assertNotIn("11.52", str(context["section_observations"]))
            payload = self.report(tab="overview", **filters)
            observations = re.findall(r'<div class="section-observations.*?</div>', payload["html"], re.DOTALL)
            self.assertNotIn("Quiz average 80.00", str(observations))
            self.assertNotIn("11.52", str(observations))
            if filters.get("course_code", self.course.code):
                self.assertTrue(observations)
                self.assertIn("cannot be verified", str(observations))
        self.assertEqual(saved, list(StudentPeriodGrade.objects.filter(offering=self.offering_a).order_by("pk").values()))

    def test_quiz_retirement_before_submission_preserves_actual_observation(self):
        from apps.admin_portal.unified_insights import present
        details = self.weighted_quiz_observation()
        details[1].is_active = False
        details[1].save()
        self.seal()
        result, selection, params = self.source(tab="overview")
        context, charts = present(result, selection, params, "overview", 1)
        self.assertEqual(self.metric("quiz_grade", result=result)["value"], 80)
        self.assertEqual(self.metric("quiz_grade", result=result)["state"], assessment_table.AVAILABLE)
        self.assertIn("Quiz average 80.00", str(context["section_observations"]))
        self.assertIn("11.52 points higher", str(context["section_observations"]))

    def test_unrelated_output_edit_preserves_valid_quiz_exam_observation(self):
        from apps.admin_portal.unified_insights import present
        self.weighted_quiz_observation()
        output = self.mid_cs.subcomponents.get(code__contains="PARTICIPATION_OUTPUT")
        output.detail_computation_mode = "WEIGHTED_DETAILS"
        output.save()
        result, selection, params = self.source(tab="overview")
        context, charts = present(result, selection, params, "overview", 1)
        self.assertEqual(self.output(result=result)["state"], assessment_table.HISTORICAL_CHANGED)
        self.assertEqual(self.metric("quiz_grade", result=result)["value"], 84)
        self.assertIn("Quiz average 84.00", str(context["section_observations"]))
        self.assertIn("15.52 points higher", str(context["section_observations"]))

    def test_previous_observation_policy_cannot_restore_an_invalid_interpretation(self):
        from apps.admin_portal import unified_insights
        details = self.weighted_quiz_observation()
        details[1].is_active = False
        details[1].save()
        with patch.object(learning_insights, "ASSESSMENT_TABLE_POLICY_VERSION", "period-gradebook-measures-v2-historical-definitions"), \
             patch.object(unified_insights, "section_observations", return_value=["Quiz average 80.00; 11.52 points higher"]):
            previous = self.report(tab="overview")
        self.assertIn("Quiz average 80.00", previous["html"])
        self.assertEqual(self.post("verify", tab="overview", token=previous["token"]).status_code, 409)
        current = self.report(tab="overview")
        self.assertIn("cannot be verified", current["html"])
        self.assertNotIn("11.52 points higher", current["html"])

    def test_post_submission_retirement_does_not_publish_surviving_80_as_historical_grade(self):
        sub, details = self.weighted_output()
        self.assertEqual(self.output()["value"], 84)
        previous = self.report(tab="overview")
        saved = list(StudentPeriodGrade.objects.filter(offering=self.offering_a).order_by("pk").values())
        details[1].is_active = False
        details[1].save()
        submission = GradeSubmission.objects.get(offering=self.offering_a, template_period=self.midterm)
        self.assertGreater(details[1].updated_at, submission.submitted_at)
        self.assertEqual(self.post("verify", tab="overview", token=previous["token"]).status_code, 409)
        result = self.data()
        grade = self.output(result=result)
        self.assertEqual((grade["value"], grade["state"]),
                         (None, "Unavailable — Historical Definition Changed"))
        self.assertEqual(self.output(kind="count", result=result)["value"], 1)
        self.assertEqual(self.metric("quiz_grade", result=result)["value"], Decimal("68.02"))
        self.assertEqual(saved, list(StudentPeriodGrade.objects.filter(offering=self.offering_a).order_by("pk").values()))
        self.assertIn("Historical Definition Changed", self.report(tab="overview")["html"])

    def test_weighted_details_and_average_activities_reuse_gradebook(self):
        sub, details = self.weighted_output()
        for mode, expected in (("WEIGHTED_DETAILS", 84), ("AVERAGE_ACTIVITIES", 85)):
            sub.detail_computation_mode = mode
            sub.save()
            self.seal()
            self.assertEqual(self.output()["value"], expected)
            self.assertEqual(self.output(kind="count")["value"], 2)

    def test_pre_submission_retirement_uses_remaining_applicable_definition(self):
        sub, details = self.weighted_output()
        details[1].is_active = False
        details[1].save()
        self.seal()
        self.assertEqual((self.output()["value"], self.output()["state"]), (80, assessment_table.AVAILABLE))
        self.assertEqual(self.output(kind="count")["value"], 1)

    def test_post_submission_weight_edit_withholds_only_affected_branch(self):
        sub, details = self.weighted_output()
        saved = list(StudentPeriodGrade.objects.filter(offering=self.offering_a).order_by("pk").values())
        before = {code: self.metric(code)["value"] for code in ("exam", "standing", "periodic", "quiz_grade")}
        details[0].weight_percentage = 20
        details[0].save()
        result = self.data()
        self.assertEqual(self.output(result=result)["state"], assessment_table.HISTORICAL_CHANGED)
        self.assertIsNone(self.output(result=result)["value"])
        self.assertEqual(self.output(kind="count", result=result)["value"], 2)
        self.assertEqual(before, {code: self.metric(code, result=result)["value"] for code in before})
        self.assertEqual(saved, list(StudentPeriodGrade.objects.filter(offering=self.offering_a).order_by("pk").values()))

    def test_unrelated_exam_and_quiz_edits_preserve_output(self):
        self.weighted_output()
        exam = self.midterm.components.get(is_exam_component=True)
        exam.weight_percentage = 10
        exam.save()
        self.assertEqual(self.output()["value"], 84)
        self.assertEqual(self.metric("quiz_grade")["value"], Decimal("68.02"))
        self.mid_quiz.weight_percentage = 10
        self.mid_quiz.save()
        self.assertEqual(self.output()["value"], 84)
        self.assertEqual(self.metric("quiz_grade")["state"], assessment_table.HISTORICAL_CHANGED)
        self.assertIsNone(self.metric("quiz_grade")["value"])

    def test_average_activities_retirement_also_cannot_rebuild_incomplete_history(self):
        sub, details = self.weighted_output()
        sub.detail_computation_mode = "AVERAGE_ACTIVITIES"
        sub.save()
        self.seal()
        self.assertEqual(self.output()["value"], 85)
        details[1].is_active = False
        details[1].save()
        self.assertEqual(self.output()["state"], assessment_table.HISTORICAL_CHANGED)
        self.assertIsNone(self.output()["value"])

    def test_post_submission_subcomponent_retirement_retains_historical_applicability(self):
        sub, details = self.weighted_output()
        sub.is_active = False
        sub.save()
        self.assertEqual(self.output()["state"], assessment_table.HISTORICAL_CHANGED)
        self.assertIsNone(self.output()["value"])
        self.assertEqual(self.output(kind="count")["state"], assessment_table.MISSING)
        self.assertEqual(self.metric("quiz_grade")["value"], Decimal("68.02"))

    def test_post_submission_component_retirement_preserves_saved_period_grades(self):
        self.weighted_output()
        before = {code: self.metric(code)["value"] for code in ("exam", "standing", "periodic")}
        self.mid_cs.is_active = False
        self.mid_cs.save()
        result = self.data()
        self.assertEqual(self.output(result=result)["state"], assessment_table.HISTORICAL_CHANGED)
        self.assertEqual(self.metric("quiz_grade", result=result)["state"], assessment_table.HISTORICAL_CHANGED)
        self.assertEqual(before, {code: self.metric(code, result=result)["value"] for code in before})

    def test_historical_changed_value_is_not_published_in_course_or_all_course_groups(self):
        from apps.admin_portal.unified_insights import assessment_presentation
        sub, details = self.weighted_output()
        details[1].is_active = False
        details[1].save()
        for level, filters in (("course", {"offering_id": ""}), ("all", {"offering_id": "", "course_code": ""})):
            with self.subTest(level=level):
                result = self.data(**filters)
                row = self.period_row(result=result)
                grade = self.output(result=result)
                self.assertEqual((grade["value"], grade["state"]), (None, assessment_table.HISTORICAL_CHANGED))
                context = assessment_presentation({"assessment_period_rows": [row]}, level, {self.offering_a.pk}, 1)
                headers = [h["code"] for h in context["assessment_headers"]]
                cell = context["assessment_rows"][0]["cells"][headers.index(grade["code"])]
                self.assertEqual(cell["text"], assessment_table.HISTORICAL_CHANGED)
                self.assertEqual(self.metric("quiz_grade", result=result)["value"], Decimal("68.02"))
                self.assertIn("Historical Definition Changed", self.report(tab="overview", **filters)["html"])

    def test_distinct_evidenced_counts_exclude_inactive_excused_and_unscored(self):
        sub = self.mid_cs.subcomponents.get(code__contains="PARTICIPATION_OUTPUT")
        original = GradeActivity.objects.get(offering=self.offering_a, template_subcomponent=sub)
        for is_active, excused in ((False, False), (True, True)):
            activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
                template_period=self.midterm, template_component=self.mid_cs, template_subcomponent=sub,
                title="Another recorded output", total_score=100, is_active=is_active)
            for student in self.students:
                self._score(activity, student, 70, 85)
            StudentActivityScore.objects.filter(activity=activity).update(is_excused=excused)
        self.assertEqual(self.output(kind="count")["value"], 1)
        # A configured active but unscored/excused activity prevents complete
        # component grades; it is not quietly removed from the denominator.
        self.assertIsNone(self.output()["value"])
        self.assertEqual(self.metric("quizzes")["value"], 1)
        self.assertEqual(StudentActivityScore.objects.filter(activity=original).count(), 6)

    def test_separate_participation_and_output_keep_independent_values(self):
        sub = self.mid_cs.subcomponents.get(code__contains="PARTICIPATION_OUTPUT")
        sub.code = "MIDTERM_CA_PARTICIPATION"
        sub.save()
        output = GradingTemplateSubcomponent.objects.create(template_component=self.mid_cs, code="MIDTERM_CA_OUTPUT",
            name="Output", weight_percentage=20, score_input_mode="RAW_BASE50", detail_computation_mode="AVERAGE_ACTIVITIES")
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.midterm, template_component=self.mid_cs, template_subcomponent=output,
            title="Output", total_score=100)
        for student in self.students:
            self._score(activity, student, 60, 80)
        self.seal()
        measures = self.period_row()["measures"]
        self.assertCountEqual([(m["label"], m["value"]) for m in measures if m["code"].startswith("output_") and m["kind"] == "grade"],
                         [("Participation Average (Gradebook Points)", Decimal(85)),
                          ("Output Average (Gradebook Points)", Decimal(80))])
        self.assertEqual(len(measures), 9)
        output.weight_percentage = 10
        output.save()
        grades = {m["label"]: m for m in self.period_row()["measures"] if m["kind"] == "grade"}
        self.assertEqual(grades["Participation Average (Gradebook Points)"]["value"], 85)
        self.assertEqual(grades["Output Average (Gradebook Points)"]["state"], assessment_table.HISTORICAL_CHANGED)
        self.assertIsNone(grades["Output Average (Gradebook Points)"]["value"])

    def test_period_wide_output_does_not_require_alignment_exam_pair(self):
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(exam_grade=None)
        self.assertEqual(self.output()["value"], 85)
        self.assertIsNone(self.metric("exam")["value"])
        self.assertEqual(self.output()["contributors"], 6)

    def test_applicability_pending_and_missing_remain_distinct(self):
        self.assertEqual(self.metric("exam", self.prefinal)["state"], assessment_table.NA)
        self.assertEqual(self.metric("standing", self.final)["state"], assessment_table.NA)
        self.assertEqual(self.metric("quiz_grade", self.final)["state"], assessment_table.NA)
        GradeSubmission.objects.filter(offering=self.offering_a, template_period=self.midterm).update(status="DRAFT")
        self.assertEqual(self.output()["state"], assessment_table.PENDING)

    def test_recorded_definition_drift_never_reconstructs_historical_component(self):
        sub = self.mid_cs.subcomponents.get(code__contains="PARTICIPATION_OUTPUT")
        GradingTemplateSubcomponent.objects.filter(pk=sub.pk).update(updated_at=timezone.now()+timedelta(days=1))
        self.assertEqual(self.output()["state"], assessment_table.HISTORICAL_CHANGED)
        self.assertIsNone(self.output()["value"])
        self.assertIsNotNone(self.metric("exam")["value"])

    def test_snapshot_template_mismatch_is_historical_unavailable(self):
        GradeSubmission.objects.filter(offering=self.offering_a, template_period=self.midterm).update(
            template_snapshot_json={"template_id": 999999})
        self.assertEqual(self.output()["state"], assessment_table.HISTORICAL)

    def test_small_component_and_complement_groups_are_planned_before_filters(self):
        activity = GradeActivity.objects.get(offering=self.offering_a,
            template_subcomponent__code__contains="MIDTERM_CA_PARTICIPATION_OUTPUT")
        StudentActivityScore.objects.filter(activity=activity, student=self.students[-1]).delete()
        self.assertEqual(self.output()["value"], 85)  # Existing leadership grant.
        UserPermission.objects.create(user=self.area_chair, permission=self.permission,
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        for filters in ({}, {"offering_id": ""}, {"offering_id": "", "course_code": ""}):
            data = self.data(**filters)
            self.assertEqual(self.output(result=data)["state"], assessment_table.WITHHELD)
            self.assertIsNone(self.output(result=data)["contributors"])
            self.assertIsNone(self.metric("standing", result=data)["value"])
        alignment = self.source(tab='alignment')[0]
        self.assertTrue(all(pair['participation'] is None for row in alignment['classifications'] for pair in row['pairs']))

    def test_four_contributors_are_withheld_without_leadership(self):
        activity = GradeActivity.objects.get(offering=self.offering_a,
            template_subcomponent__code__contains="MIDTERM_CA_PARTICIPATION_OUTPUT")
        StudentActivityScore.objects.filter(activity=activity, student__in=self.students[-2:]).delete()
        UserPermission.objects.create(user=self.area_chair, permission=self.permission,
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        self.assertEqual(self.output()["state"], assessment_table.WITHHELD)

    def test_filters_grouping_pagination_and_no_duplicate_alignment_table(self):
        for filters in ({}, {"offering_id": ""}, {"offering_id": "", "course_code": ""}):
            response = self.report(tab="overview", **filters)
            self.assertIn("Class Assessment Performance by Period", response["html"])
            if not filters.get("course_code", self.filters["course_code"]):
                self.assertIn("total recorded assessments", response["html"])
        self.assertNotIn("Class Assessment Performance by Period", self.report(tab="alignment")["html"])

    def test_new_component_grade_change_invalidates_signed_generation(self):
        first = self.report(tab="overview")
        StudentActivityScore.objects.filter(activity__offering=self.offering_a,
            activity__template_subcomponent__code__contains="PARTICIPATION_OUTPUT").update(computed_score=86)
        self.assertEqual(self.post("verify", tab="overview", token=first["token"]).status_code, 409)

    def test_prior_table_policy_cannot_reuse_cached_payload_or_signed_generation(self):
        with patch.object(learning_insights, 'ASSESSMENT_TABLE_POLICY_VERSION', 'prior-table-policy'):
            first = self.report(tab='overview')
        self.assertEqual(self.post('verify', tab='overview', token=first['token']).status_code, 409)
        self.assertIn('Class Assessment Performance by Period', self.report(tab='overview')['html'])

    def test_saved_zero_is_valid_and_unfinalized_grades_do_not_contribute(self):
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(exam_grade=0)
        self.assertEqual(self.metric("exam")["value"], 0)
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm).update(is_finalized=False)
        self.assertIsNone(self.metric("exam")["value"])
        self.assertIsNone(self.output()["value"])
        self.assertEqual(self.output(kind="count")["value"], 1)

    def test_table_follows_non_ncba_template_period_names(self):
        self.period.code, self.period.name = "QUARTER_ONE", "Quarter One"
        self.period.save()
        self.term_periods["PRELIM"].code = "QUARTER_ONE"
        self.term_periods["PRELIM"].save()
        self.seal()
        self.assertIn("Quarter One", [r["period"] for r in self.data()["assessment_period_rows"]])

    def test_compatible_groups_use_section_means_and_incompatible_groups_stay_separate(self):
        from copy import deepcopy
        from apps.admin_portal.unified_insights import assessment_presentation
        a = self.period_row()
        b, c = deepcopy(a), deepcopy(a)
        b["id"], c["id"], c["group"] = 999, 1000, "different-effective-configuration"
        for row, value in ((b, 80), (c, 20)):
            next(m for m in row["measures"] if m["code"] == "quiz_grade")["value"] = Decimal(value)
        context = assessment_presentation({"assessment_period_rows": [a, b, c]}, "all", set(), 1)
        self.assertEqual(sorted(r["sections"] for r in context["assessment_rows"]), [1, 2])
        group = next(r for r in context["assessment_rows"] if r["sections"] == 2)
        headers = [r["code"] for r in context["assessment_headers"]]
        self.assertEqual(group["cells"][headers.index("quiz_grade")]["text"], "74.01")
        self.assertEqual(group["cells"][headers.index("quizzes")]["text"], "2")
        many = [dict(deepcopy(a), course=f"C-{index:03d}") for index in range(60)]
        page = assessment_presentation({"assessment_period_rows": many}, "all", set(), 2)
        self.assertEqual(len(page["assessment_rows"]), 20)
        self.assertEqual(page["assessment_page"].paginator.num_pages, 3)
        self.assertEqual(page["assessment_rows"][0]["label"], "C-020")

    def test_inactive_student_is_excluded_without_invalidating_remaining_results(self):
        self.students[-1].is_active = False
        self.students[-1].save()
        self.assertEqual(self.output()["contributors"], 5)
        self.assertEqual(self.output()["value"], 85)
        self.assertEqual(self.metric("quiz_grade")["contributors"], 5)

    def test_historical_roster_evidence_counts_without_fabricating_complete_quiz_grades(self):
        from apps.enrollment.models import Enrollment
        student = self.students[-1]
        Enrollment.objects.filter(course_offering=self.offering_a, student=student).update(
            is_active=False, enrollment_status="DROPPED")
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.midterm, template_component=self.mid_cs, template_subcomponent=self.mid_quiz,
            title="Recorded historical quiz", total_score=100)
        self._score(activity, student, 70, 85)
        self.assertEqual(self.metric("quizzes")["value"], 2)
        self.assertIsNone(self.metric("quiz_grade")["value"])

    def test_component_level_output_reuses_its_configured_child_rollup(self):
        from apps.grading.models import GradingTemplateComponent
        component = GradingTemplateComponent.objects.create(template_period=self.final,
            code='FINAL_PARTICIPATION_OUTPUT', name='Participation and Output',
            weight_percentage=40, score_input_mode='RAW_BASE50')
        sub = GradingTemplateSubcomponent.objects.create(template_component=component,
            code='FINAL_ASSIGNMENTS', name='Assignments', weight_percentage=100,
            detail_computation_mode='AVERAGE_ACTIVITIES')
        activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
            template_period=self.final, template_component=component, template_subcomponent=sub,
            title='Output', total_score=100)
        for student in self.students:
            self._score(activity, student, 60, 80)
        self.seal()
        self.assertEqual(self.output(self.final)["value"], 80)
        self.assertEqual(self.output(self.final, kind='count')["value"], 1)

    def test_unverified_historical_scales_do_not_merge_saved_section_grades(self):
        self.complete_students(6, self.offering_b)
        GradingTemplateSubcomponent.objects.filter(template_component=self.mid_cs).update(
            updated_at=timezone.now()+timedelta(days=1))
        rows = [r for r in self.data(offering_id='')["assessment_period_rows"] if r['period_id'] == self.midterm.pk]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r['group'] for r in rows}), 2)

    def test_period_measurement_mapping_adds_no_database_queries(self):
        original = assessment_table.period_measures
        def measured(*args, **kwargs):
            with self.assertNumQueries(0):
                return original(*args, **kwargs)
        with patch.object(assessment_table, 'period_measures', side_effect=measured):
            self.assertEqual(self.metric('quiz_grade')['value'], Decimal('68.02'))

    def test_primary_layouts_and_chart_period_use_current_authorized_report(self):
        from apps.admin_portal.unified_insights import present
        self.active.period = self.term_periods["PREFINAL"]
        self.active.save()
        for filters, level in (({}, "section"), ({"offering_id": ""}, "course"),
                               ({"offering_id": "", "course_code": ""}, "all")):
            with self.subTest(level=level):
                result, selection, params = self.source(tab="overview", **filters)
                original = deepcopy(result)
                context, charts = present(result, selection, params, "overview", 1)
                self.assertEqual(result, original)
                if level == "section":
                    self.assertEqual(len(context["assessment_matrix"]), 5)
                    self.assertTrue(context["assessment_heading"])
                elif level == "course":
                    self.assertEqual(len(context["assessment_sections"]), 2)
                    self.assertEqual(len(context["assessment_sections"][0]["headers"]), 5)
                    self.assertEqual(charts["bar_period"], result["charts"]["bar_period"])
                else:
                    self.assertNotIn("assessment_sections", context)
                    self.assertTrue(context["assessment_blocks"])
                response = self.report(tab="overview", **filters)
                primary = response["html"].split("data-assessment-period-table>", 1)[1].split("<details class=\"small text-muted mt-2\">", 1)[0]
                self.assertNotIn("Class Standing Average", primary)
                self.assertNotIn("Periodic Grade Average", primary)
                self.assertIn("table-striped", primary)
                self.assertIn('scope="col"', primary)
                if level != "all":
                    self.assertIn("Section Chart Values — " + result["charts"]["bar_period"], response["html"])
                    self.assertEqual(response["charts"]["bars"], json.loads(json.dumps(charts["bars"], cls=DjangoJSONEncoder)))
                if level == "course":
                    self.assertIn("Course Period Averages — Equal Section Weight", response["html"])

    def test_chart_interpretation_never_recovers_a_withheld_grade(self):
        self.active.period = self.term_periods["PREFINAL"]
        self.active.save()
        activity = GradeActivity.objects.get(offering=self.offering_a, template_subcomponent=self.mid_quiz)
        StudentActivityScore.objects.filter(activity=activity, student=self.students[-1]).delete()
        UserPermission.objects.create(user=self.area_chair, permission=self.permission,
            tenant=self.tenant, campus=self.campus, grant_type="DENY")
        from apps.admin_portal.unified_insights import present
        result, selection, params = self.source(tab="overview")
        context, charts = present(result, selection, params, "overview", 1)
        self.assertTrue(all(b["quiz_grade"] is None for b in charts["bars"]))
        self.assertEqual(context["section_observations"], [
            "Comparable Quiz and Examination averages are not available for a section comparison."])


class AssessmentPresentationTests(SimpleTestCase):
    def row(self, index=1):
        def measure(code, label, kind, value):
            return {"code": code, "label": label, "kind": kind, "value": Decimal(value),
                    "state": assessment_table.AVAILABLE, "contributors": 6}
        return {"id": index, "course": "COURSE", "section": "Very long section name " * 8,
            "campus": "FAIRVIEW", "period": "Midterm", "period_code": "MIDTERM", "period_id": 2,
            "sequence": 2, "group": "compatible", "measures": [
                measure("quizzes", "Quiz Count", "count", 2),
                measure("quiz_grade", "Quiz Average (Gradebook Points)", "grade", "92.03"),
                measure("output_CA_PARTICIPATION_OUTPUT_count", "Participation/Output Activity Count", "count", 3),
                measure("output_CA_PARTICIPATION_OUTPUT_grade", "Participation/Output Average (Gradebook Points)", "grade", 85),
                measure("exam", "Examination Average", "grade", "68.48"),
                measure("standing", "Class Standing Average", "grade", 94),
                measure("periodic", "Periodic Grade Average", "grade", 84)]}

    def projected(self, rows, level="all", visible=None, page=1):
        from apps.admin_portal.unified_insights import assessment_presentation
        return assessment_presentation({"assessment_period_rows": rows}, level,
                                       {r["id"] for r in rows} if visible is None else visible, page)

    def test_period_aliases_do_not_duplicate_columns_or_modify_source_values(self):
        from copy import deepcopy
        a, b = self.row(), self.row()
        b.update(period="Prelim", period_id=1, sequence=1)
        for m in b["measures"]:
            m["code"] = m["code"].replace("output_CA_", "output_CS_")
        original = deepcopy([a, b])
        context = self.projected([a, b], "section")
        self.assertEqual(len(context["assessment_matrix"]), 5)
        self.assertEqual(context["assessment_periods"], ["Prelim", "Midterm"])
        self.assertEqual([a, b], original)
        self.assertEqual(context["assessment_matrix"][1]["cells"][0]["text"], "92.03")

    def test_genuine_separate_components_keep_values_in_local_column_groups(self):
        from copy import deepcopy
        combined, separate = self.row(), self.row(2)
        separate["group"] = "separate-template"
        separate["measures"] = [m for m in separate["measures"] if not m["code"].startswith("output_")]
        for label, value in (("Participation", 82), ("Output", 78)):
            for kind in ("count", "grade"):
                m = deepcopy(combined["measures"][2 if kind == "count" else 3])
                m.update(code=f"output_{label}_{kind}", label=f"{label} " + ("Activity Count" if kind == "count" else "Average (Gradebook Points)"), value=Decimal(value))
                separate["measures"].insert(-3, m)
        context = self.projected([combined, separate])
        self.assertEqual(sorted(len(b["headers"]) for b in context["assessment_blocks"]), [5, 7])
        section = self.projected([separate], "section")
        self.assertEqual(len(section["assessment_matrix"]), 7)
        self.assertTrue(any("Participation Average" in m["label"] for m in section["assessment_matrix"]))
        self.assertTrue(any("Output Average" in m["label"] for m in section["assessment_matrix"]))

    def test_course_heads_once_per_section_and_respects_visible_page(self):
        from copy import deepcopy
        from django.template.loader import render_to_string
        rows = []
        for index in range(25):
            a = self.row(index); b = deepcopy(a); b.update(period="Prelim", period_id=1, sequence=1)
            rows.extend([a, b])
        context = self.projected(rows, "course", visible=set(range(20)))
        self.assertEqual(len(context["assessment_sections"]), 20)
        self.assertTrue(all(len(s["rows"]) == 2 for s in context["assessment_sections"]))
        html = render_to_string("admin_portal/grading/_assessment_performance_table.html", {**context, "level": "course"})
        self.assertEqual(html.count("data-assessment-section"), 20)
        self.assertEqual(html.count(self.row()["section"] + " / FAIRVIEW</h4>"), 20)

    def test_all_courses_pagination_and_incompatible_groups_are_preserved(self):
        rows = [dict(self.row(index), course=f"C-{index:03d}") for index in range(42)]
        context = self.projected(rows, page=2)
        self.assertEqual(len(context["assessment_rows"]), 20)
        self.assertEqual(context["assessment_page"].paginator.num_pages, 3)
        self.assertEqual(context["assessment_rows"][0]["label"], "C-020")
        self.assertNotIn("assessment_sections", context)

    def test_availability_text_stays_distinct_and_unavailable_values_are_excluded(self):
        states = (assessment_table.NA, assessment_table.PENDING, assessment_table.MISSING,
                  assessment_table.WITHHELD, assessment_table.HISTORICAL_CHANGED)
        classes = set()
        for state in states:
            row = self.row()
            row["measures"][1].update(value=None, state=state, contributors=None)
            cell = self.projected([row], "section")["assessment_matrix"][1]["cells"][0]
            self.assertEqual(cell["text"], state)
            self.assertIsNone(cell["contributors"])
            classes.add(cell["state_class"])
        self.assertEqual(len(classes), 5)

    def test_observations_use_released_values_exact_period_and_approved_display_cutoff(self):
        from apps.admin_portal.unified_insights import section_observations
        row = self.row()
        label = f"{row['section']} / {row['campus']}"
        bars = [{"section": label, "quiz_grade": Decimal("92.03"), "exam": Decimal("68.48")}]
        text = section_observations(bars, "Midterm", [row])[0]
        self.assertIn(label + " — Midterm", text)
        self.assertIn("23.55 points higher", text)
        self.assertNotIn("caused", text)
        row["period_code"] = "PRELIM"
        row["measures"][4]["value"] = Decimal("90")
        self.assertIn("less than 3", section_observations([dict(bars[0], exam=Decimal("90"))], "Prelim", [row])[0])
        row["measures"][4]["value"] = Decimal("89.03")
        self.assertIn("3.00 points higher", section_observations([dict(bars[0], exam=Decimal("89.03"))], "Prelim", [row])[0])
        self.assertNotIn(label, str(section_observations([dict(bars[0], quiz_grade=None)], "Midterm", [row])))
        self.assertIn("not available", section_observations([], "")[0])

    def test_observation_rejects_each_unavailable_component_even_with_chart_numbers(self):
        from apps.admin_portal.unified_insights import section_observations
        for code in ("quiz_grade", "exam"):
            for state in (assessment_table.NA, assessment_table.PENDING, assessment_table.MISSING,
                          assessment_table.WITHHELD, assessment_table.HISTORICAL, assessment_table.HISTORICAL_CHANGED):
                with self.subTest(code=code, state=state):
                    row = self.row()
                    next(m for m in row["measures"] if m["code"] == code).update(value=None, state=state)
                    bars = [{"section": f"{row['section']} / {row['campus']}", "quiz_grade": Decimal("92.03"), "exam": Decimal("68.48")}]
                    text = str(section_observations(bars, "Midterm", [row]))
                    self.assertIn("cannot be verified", text)
                    for value in ("92.03", "68.48", "23.55"):
                        self.assertNotIn(value, text)

    def test_observation_requires_unique_section_period_component_and_matching_value(self):
        from apps.admin_portal.unified_insights import section_observations
        original = self.row()
        bars = [{"section": f"{original['section']} / {original['campus']}", "quiz_grade": Decimal("92.03"), "exam": Decimal("68.48")}]
        for change in ("missing", "period", "section", "campus", "duplicate", "component", "value"):
            row = deepcopy(original)
            rows = [row]
            if change == "missing": rows = []
            elif change == "period": row["period_code"] = "PRELIM"
            elif change == "section": row["section"] = "Another section"
            elif change == "campus": row["campus"] = "Another campus"
            elif change == "duplicate": rows.append(dict(row, id=999))
            elif change == "component": row["measures"].append(deepcopy(row["measures"][1]))
            elif change == "value": row["measures"][1]["value"] = Decimal(80)
            with self.subTest(change=change):
                self.assertIn("cannot be verified", str(section_observations(bars, "Midterm", rows)))
        self.assertIn("cannot be verified", str(section_observations(bars, "Midterm")))

    def test_observation_keeps_other_section_and_period_with_verified_components(self):
        from apps.admin_portal.unified_insights import section_observations
        unavailable = self.row()
        unavailable["section"] = "Unsupported section"
        unavailable["measures"][1].update(value=None, state=assessment_table.HISTORICAL_CHANGED)
        valid = self.row(2)
        valid["section"] = "Verified section"
        previous = deepcopy(valid)
        previous.update(period_code="PRELIM", period="Prelim", period_id=1)
        previous["measures"][1].update(value=None, state=assessment_table.HISTORICAL_CHANGED)
        for m in valid["measures"]:
            if m["code"] not in {"quiz_grade", "exam"}:
                m.update(value=None, state=assessment_table.HISTORICAL_CHANGED)
        bars = [{"section": f"{r['section']} / {r['campus']}", "quiz_grade": Decimal("92.03"), "exam": Decimal("68.48")}
                for r in (unavailable, valid)]
        text = str(section_observations(bars, "Midterm", [unavailable, previous, valid]))
        self.assertIn("Verified section", text)
        self.assertNotIn("Unsupported section", text)
        self.assertIn("23.55 points higher", text)
