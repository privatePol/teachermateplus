"""Representative finalized records; all databases are disposable Django test databases."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import ActiveGradingPeriodSetting, Course, FacultyAssignment, TenantTermGradingPeriod
from apps.admin_portal import tests_academic_performance_insights as fixtures
from apps.admin_portal import learning_insights_views as views
from apps.admin_portal.insights import Selection
from apps.admin_portal.learning_insights import INSUFFICIENT, PATTERNS, LearningSelection, build_report, classify
from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService
from apps.enrollment.models import Enrollment
from apps.grading.models import (
    GradeActivity, GradeCorrectionRequest, GradeCorrectionUnlockWindow, GradeSubmission, GradeSubmissionReopenRequest,
    GradingPeriodLock, GradingTemplateComponent, GradingTemplatePeriod, GradingTemplateSubcomponent,
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

    def test_initial_get_is_filters_only_and_old_menu_is_unchanged(self):
        with patch.object(views, "build_report") as compute:
            response = self.client.get(self.url, {**{k: v for k, v in self.filters.items() if v is not None}, "action": "report"})
        compute.assert_not_called()
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
        self.assertEqual(row["quizzes"], 1)
        StudentActivityScore.objects.filter(activity=self.activity_a, student=self.student_a1).delete()
        self.assertIsNone(self.raw(offering_id=self.offering_a.id)["rows"][0]["quiz"])

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
        self.assertIsNone(raw["charts"]["line"][0]["standing"])

    def test_changing_configuration_or_cohort_prevents_longitudinal_classification(self):
        self.mid_cs.weight_percentage = 60
        self.mid_cs.save()
        self.assertEqual(self.raw()["analyzed"], 0)
        self.mid_cs.weight_percentage = 70
        self.mid_cs.save()
        StudentPeriodGrade.objects.filter(offering=self.offering_a, template_period=self.midterm, student=self.student_a2).delete()
        self.assertEqual(self.raw()["analyzed"], 1)

    def test_pattern_rules_include_decline_opposite_mixed_and_unchanged(self):
        def row(sequence, quiz, standing, exam):
            return {"eligible": True, "period": str(sequence), "sequence": sequence, "signature": (1,),
                    "cohort": {1, 2}, "quiz": Decimal(quiz), "standing": Decimal(standing), "exam": Decimal(exam), "quizzes": 2, "activities": 3}
        first = row(1, 70, 80, 75)
        for values, expected, detail in (((80, 85, 90), PATTERNS[0], "Improved"), ((60, 70, 65), PATTERNS[0], "Declined"),
                                         ((80, 85, 70), PATTERNS[1], "opposite"), ((70, 85, 90), PATTERNS[2], "unchanged"),
                                         ((80, 75, 90), PATTERNS[2], "Mixed")):
            result = classify([first, row(2, *values)])
            self.assertEqual(result["category"], expected)
            self.assertIn(detail, result["detail"])
        self.assertEqual(classify([first])["category"], INSUFFICIENT)

    def test_most_recent_comparable_pair_skips_incompatible_period(self):
        base = {"eligible": True, "signature": (1,), "cohort": {1}, "quiz": Decimal(50), "standing": Decimal(60), "exam": Decimal(70), "quizzes": 1, "activities": 1}
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

    def test_scope_bound_token_cache_miss_and_record_changes(self):
        data = self.report()
        cache.clear()
        self.assertEqual(self.post("page", token=data["token"], page=1).status_code, 200)
        StudentActivityScore.objects.filter(activity=self.activity_a).update(raw_score=20)
        response = self.post("page", token=data["token"], page=1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "records_changed")

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
