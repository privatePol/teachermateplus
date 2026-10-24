"""Disposable records only; no staging data or scanner-provenance assumptions."""
from decimal import Decimal
from datetime import timedelta
from itertools import product
from unittest.mock import patch
import time

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.admin_portal import tests_learning_insights as fixtures
from apps.admin_portal import assessment_alignment as alignment
from apps.admin_portal.learning_insights import LearningSelection, PATTERNS, INSUFFICIENT
from apps.departmental_exams.models import ExaminationCycle, CycleCourse, CycleCourseOffering
from apps.grading.models import (GradingTemplateComponent, GradingTemplateSubcomponent,
    GradingTemplateDetail, GradeActivity, StudentActivityScore, StudentPeriodGrade,
    GradeSubmission, GradingPeriodLock, CourseBaseValueOverride)
from apps.enrollment.models import Enrollment
from apps.rbac.models import UserPermission


class AlignmentTests(TestCase):
    _enable_feature = fixtures.LearningInsightsTests._enable_feature
    _role_with_access = fixtures.LearningInsightsTests._role_with_access
    _user = fixtures.LearningInsightsTests._user
    _offering = fixtures.LearningInsightsTests._offering
    _assign = fixtures.LearningInsightsTests._assign
    _student = fixtures.LearningInsightsTests._student
    _activity = fixtures.LearningInsightsTests._activity
    _score = staticmethod(fixtures.LearningInsightsTests._score)
    complete_students = fixtures.LearningInsightsTests.complete_students
    bsa_period_definitions = fixtures.LearningInsightsTests.bsa_period_definitions
    post = fixtures.LearningInsightsTests.post
    report = fixtures.LearningInsightsTests.report

    def setUp(self):
        fixtures.LearningInsightsTests.setUp(self)
        self.url = reverse("admin_portal:assessment_alignment_preview")
        self.tenant.code = "NCBA"
        self.tenant.save(update_fields=["code"])
        self.students = self.complete_students(6)
        self.bsa_period_definitions()
        self.filters["offering_id"] = self.offering_a.id
        self.pre_cs = GradingTemplateComponent.objects.create(template_period=self.prefinal,
            code="PREFINAL_CA", name="Class Standing", weight_percentage=100, score_input_mode="RAW_BASE50")
        # Exact independent clones of the Midterm subcomponent computation rules.
        for sub in list(self.mid_cs.subcomponents.all()):
            original = sub.pk
            sub.pk = None
            sub.template_component = self.pre_cs
            sub.code = sub.code.replace("MIDTERM_", "PREFINAL_")
            sub.save()
            for detail in GradingTemplateDetail.objects.filter(template_subcomponent_id=original):
                detail.pk = None
                detail.template_subcomponent = sub
                detail.code = detail.code.replace("MIDTERM_", "PREFINAL_")
                detail.save()
        self.final_exam = GradingTemplateComponent.objects.create(template_period=self.final,
            code="FINAL_EXAM", name="Final Exam", weight_percentage=100, is_exam_component=True, score_input_mode="RAW_BASE50")
        for period, cs in ((self.period, self.component), (self.midterm, self.mid_cs), (self.prefinal, self.pre_cs)):
            for sub in cs.subcomponents.all():
                if "PARTICIPATION_OUTPUT" in sub.code or period == self.prefinal:
                    activity = GradeActivity.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
                        template_period=period, template_component=cs, template_subcomponent=sub,
                        template_detail=sub.details.first(), title="Synthetic", total_score=100)
                    for student in self.students:
                        self._score(activity, student, 80 if period == self.prefinal else 70, 90 if period == self.prefinal else 85)
        for period in (self.prefinal, self.final):
            for student in self.students:
                StudentPeriodGrade.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
                    template_period=period, student=student, class_standing_grade=90 if period == self.prefinal else None,
                    exam_grade=90 if period == self.final else None, period_grade=10 if period == self.final else 90,
                    is_finalized=True)
            GradeSubmission.objects.create(tenant=self.tenant, campus=self.campus, offering=self.offering_a,
                template_period=period, status="SUBMITTED", submitted_at=timezone.now()-timedelta(days=1))
        self.active.period = self.term_periods["FINAL"]
        self.active.save()
        self.lock = GradingPeriodLock.objects.create(tenant=self.tenant, campus=self.campus,
            academic_year=self.academic_year, term=self.term, period_code="FINAL", scope_type="CAMPUS",
            is_locked=True, deadline_at=timezone.now()-timedelta(days=1))
        self.cycles = {}
        for code in ("MIDTERM", "FINAL"):
            cycle = ExaminationCycle.objects.create(tenant=self.tenant, academic_year=self.academic_year,
                term=self.term, exam_period=code, created_by=self.area_chair)
            course = CycleCourse.objects.create(cycle=cycle, course=self.course, exam_classification="DEPARTMENTAL")
            self.cycles[code] = course
            CycleCourseOffering.objects.create(cycle_course=course, offering=self.offering_a, campus=self.campus)

    def raw(self):
        request = self.client.get(self.url).wsgi_request
        return alignment.build_report(LearningSelection(request), self.filters)

    def entry(self):
        result = self.raw()
        self.assertEqual(result["sections"], 1)
        return result["classifications"][0]

    def test_six_ncba_templates_resolve_without_ids_or_program_blacklists(self):
        for name in ("BSA", "BSBA", "BSHM", "BSIS", "BSCS", "LA"):
            with self.subTest(template=name):
                self.template.code = name + "_GRADING_TEMPLATE"
                self.template.save(update_fields=["code"])
                row = self.entry()
                self.assertEqual(row["category"], "Moved Together", row)
                self.assertEqual([(p["source"], p["destination"]) for p in row["pairs"]],
                                 [("MIDTERM", "MIDTERM"), ("PREFINAL", "FINAL")])

    def test_saved_exam_is_used_not_periodic_or_overall_final(self):
        row = self.entry()
        self.assertEqual(row["pairs"][-1]["exam"], 90)
        self.assertEqual(row["pairs"][-1]["participation"], 90)
        self.assertEqual(row["pairs"][0]["quiz"], Decimal("68.02"))
        self.assertEqual(row["matched"], 6)
        self.assertEqual(row["exams"][0]["type"], "Regular")
        self.assertEqual(row["exam_comparisons"][0]["difference"], Decimal("-3.96"))

    def test_four_categories_and_all_27_combinations_with_inclusive_boundaries(self):
        counts = dict.fromkeys(PATTERNS, 0)
        a = dict.fromkeys(alignment.METRICS, Decimal(80))
        for changes in product((-3, 0, 3), repeat=3):
            b = dict(zip(alignment.METRICS, (Decimal(80+x) for x in changes)))
            expected = (PATTERNS[0] if changes in {(-3,-3,-3),(3,3,3)} else
                        PATTERNS[1] if changes in {(-3,-3,3),(3,3,-3)} else
                        PATTERNS[3] if changes == (0,0,0) else PATTERNS[2])
            self.assertEqual(alignment.movement(a,b),expected)
            counts[expected]+=1
        self.assertEqual(list(counts.values()), [2,2,22,1])
        self.assertEqual(alignment.movement(a, dict.fromkeys(alignment.METRICS,Decimal("82.999"))), PATTERNS[3])
        self.assertEqual(alignment.movement(a, dict.fromkeys(alignment.METRICS,Decimal("77.001"))), PATTERNS[3])

    def test_ongoing_final_and_unfinalized_scores_do_not_classify(self):
        self.lock.is_locked=False
        self.lock.save()
        row=self.entry()
        self.assertEqual(row["category"], INSUFFICIENT)
        self.assertIsNone(row["pairs"][-1]["exam"])
        self.lock.is_locked=True
        self.lock.save()
        StudentPeriodGrade.objects.filter(template_period=self.final).update(is_finalized=False)
        self.assertEqual(self.entry()["category"], INSUFFICIENT)

    def test_legacy_is_descriptive_and_exempt_is_excluded(self):
        from apps.departmental_exams.models import _classification_service_scope
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.cycles["MIDTERM"].pk).update(exam_classification="UNCLASSIFIED_LEGACY")
        row=self.entry()
        self.assertEqual(row["category"], INSUFFICIENT)
        self.assertEqual(row["pairs"][0]["type"], "Legacy Exam \u2014 Type Not Recorded")
        self.assertIsNotNone(row["pairs"][0]["quiz"])
        CycleCourse.objects.filter(pk=self.cycles["FINAL"].pk).update(inclusion_status="EXEMPT")
        row=self.entry()
        self.assertEqual(row["pairs"][-1]["type"], "Exempt")
        self.assertIsNone(row["pairs"][-1]["exam"])

    def test_standardized_policy_and_scope_snapshot(self):
        from apps.departmental_exams.models import _classification_service_scope
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=self.cycles["FINAL"].pk).update(exam_classification="STANDARDIZED")
        self.assertEqual(self.entry()["pairs"][-1]["type"], "Standardized")
        from apps.tenants.models import Campus
        other = Campus.objects.create(tenant=self.tenant, code="OTHER", name="Other")
        CycleCourseOffering.objects.filter(cycle_course=self.cycles["FINAL"]).update(campus=other)
        self.assertEqual(self.entry()["category"], INSUFFICIENT)

    def test_independent_participation_required_not_other_nonquiz(self):
        GradingTemplateSubcomponent.objects.filter(template_component=self.pre_cs,code__contains="PARTICIPATION_OUTPUT").update(code="PREFINAL_PROJECT")
        row=self.entry()
        self.assertEqual(row["category"], INSUFFICIENT)
        self.assertIsNone(row["pairs"][-1]["participation"])

    def test_deny_small_and_overlapping_populations_and_authorized_leadership(self):
        self.assertEqual(self.entry()["matched"],6)
        # Five plus one complement reveals the omitted student's grade.
        StudentPeriodGrade.objects.filter(template_period=self.final,student=self.students[-1]).update(exam_grade=None)
        self.assertEqual(self.entry()["matched"],5)
        UserPermission.objects.create(user=self.area_chair, permission=self.permission, tenant=self.tenant,
                                      campus=self.campus, grant_type="DENY")
        row=self.entry()
        self.assertEqual(row["category"],INSUFFICIENT)
        self.assertIsNone(row["pairs"][-1]["exam"])
        StudentPeriodGrade.objects.filter(template_period=self.final).exclude(student=self.students[0]).update(exam_grade=None)
        self.assertIsNone(self.entry()["pairs"][-1]["exam"])

    def test_missing_excused_zero_and_rules(self):
        activity=GradeActivity.objects.filter(template_period=self.prefinal,template_subcomponent__code__contains="QUIZZES").get()
        scores=StudentActivityScore.objects.filter(activity=activity)
        scores.update(raw_score=0,computed_score=50)
        self.assertEqual(self.entry()["pairs"][-1]["quiz"],50)
        scores.update(is_excused=True)
        self.assertIsNone(self.entry()["pairs"][-1]["quiz"])
        scores.delete()
        self.assertIsNone(self.entry()["pairs"][-1]["quiz"])

    def test_incompatible_modes_withhold_movement(self):
        self.final_exam.score_input_mode="DIRECT_PERCENTAGE"
        self.final_exam.save()
        self.assertEqual(self.entry()["category"],INSUFFICIENT)

    def test_other_school_same_period_three_period_policy_without_cycles(self):
        self.tenant.code="OTHER_SCHOOL"
        self.tenant.save()
        CycleCourseOffering.objects.all().delete()
        self.prefinal.is_active=False
        self.prefinal.save()
        # A conventional three-period template has independent formative buckets
        # in Final itself; reuse its configured definitions, not NCBA's mapping.
        self.pre_cs.template_period=self.final
        self.pre_cs.code="FINAL_CA"
        self.pre_cs.save()
        for sub in self.pre_cs.subcomponents.all():
            sub.code=sub.code.replace("PREFINAL_","FINAL_"); sub.save()
        GradeActivity.objects.filter(template_period=self.prefinal).update(template_period=self.final)
        row=self.entry()
        self.assertEqual([(p["source"],p["destination"]) for p in row["pairs"]],
                         [("PRELIM","PRELIM"),("MIDTERM","MIDTERM"),("FINAL","FINAL")])
        self.assertEqual(row["pairs"][-1]["type"],"Template examination")

    def test_cache_policy_invalidation_and_no_individual_records_in_response(self):
        result=self.report()
        self.assertIn("Participation/Output",result["html"])
        self.assertNotIn('"student_values"',str(result))
        self.assertNotIn(self.students[0].student_no,result["html"])
        CycleCourse.objects.filter(pk=self.cycles["FINAL"].pk).update(inclusion_status="EXEMPT")
        self.assertEqual(self.post("page",token=result["token"]).status_code,409)

    def test_new_analysis_query_profile_and_cache_reuse(self):
        started=time.perf_counter()
        with CaptureQueriesContext(connection) as service:
            self.raw()
        with CaptureQueriesContext(connection) as initial:
            self.report()
        with patch("apps.admin_portal.assessment_alignment_views.build_report",side_effect=AssertionError("cached report rebuilt")):
            with CaptureQueriesContext(connection) as repeated:
                self.report()
        print(f"Alignment disposable fixture queries service={len(service)}, initial={len(initial)}, repeat={len(repeated)}; elapsed={time.perf_counter()-started:.3f}s")
        self.assertLess(len(repeated),len(initial))

    def test_feature_access_and_direct_analytics_deny(self):
        from apps.rbac.models import Permission
        permission=Permission.objects.get(code="grading_analytics.read")
        UserPermission.objects.create(user=self.area_chair,permission=permission,tenant=self.tenant,campus=self.campus,grant_type="DENY")
        response=self.post()
        self.assertTrue(response.status_code in (302,403) or (response.status_code==400 and not response.json().get("charts")))

    def test_effective_profile_base_override_and_unsupported_formula(self):
        from apps.grading.models import TenantGradingProfile
        profile=TenantGradingProfile.objects.create(tenant=self.tenant,course=self.course,
            profile_code="ALIGNMENT",profile_name="Alignment test",grading_template=self.template,
            default_base_value=40,period_grade_formula_mode="WEIGHTED_COMPONENTS")
        request=self.client.get(self.url).wsgi_request
        selection=LearningSelection(request)
        offering=selection.offerings[self.offering_a.pk]
        context,base=selection.grading_context(offering,self.midterm)
        self.assertEqual(context["profile_id"],profile.pk)
        self.assertEqual(Decimal(base),40)
        prior=self.report()
        CourseBaseValueOverride.objects.create(course=self.course,base_value=50,effective_from_term=self.term)
        self.assertEqual(self.post("page",token=prior["token"]).status_code,409)
        selection=LearningSelection(request)
        context,base=selection.grading_context(selection.offerings[self.offering_a.pk],self.midterm)
        self.assertEqual(Decimal(base),50)
        scales,components=alignment.learning.scale_definitions(self.midterm)
        mapping=alignment.resolve_mapping(self.midterm,components,alignment.learning.comparable_component_signature(self.midterm),
            {**context,"formula_mode":"UNKNOWN"},base)
        self.assertIsNone(mapping["quiz"])
        self.assertIsNone(mapping["exam"])

    def test_weighted_independent_output_uses_gradebook_rollup(self):
        sub=self.mid_cs.subcomponents.get(code__contains="PARTICIPATION_OUTPUT")
        sub.detail_computation_mode="WEIGHTED_DETAILS"; sub.save()
        details=list(sub.details.order_by("id"))
        sub.details.update(weight_percentage=0)
        details[0].weight_percentage=60; details[0].save()
        details[1].weight_percentage=40; details[1].save()
        activity=GradeActivity.objects.get(offering=self.offering_a,template_subcomponent=sub)
        activity.template_detail=details[0]; activity.save()
        StudentActivityScore.objects.filter(activity=activity).update(raw_score=60,computed_score=80)
        second=GradeActivity.objects.create(tenant=self.tenant,campus=self.campus,offering=self.offering_a,
            template_period=self.midterm,template_component=self.mid_cs,template_subcomponent=sub,
            template_detail=details[1],title="Second independent output",total_score=100)
        for student in self.students:
            self._score(second,student,80,90)
        row=self.entry()
        expected=alignment.learning.FacultyGradingService.aggregate_detail_scores(subcomponent=sub,
            detail_scores=[(Decimal(60),Decimal(80)),(Decimal(40),Decimal(90))])
        self.assertEqual(row["pairs"][0]["participation"],expected)
        self.assertEqual(expected,84)
        self.assertEqual(row["pairs"][0]["quiz"],Decimal("68.02"))
        self.assertEqual(row["category"],INSUFFICIENT)  # genuine cross-period rollup mismatch

    def test_actual_direction_changes_and_denominator(self):
        for target,expected in (((90,90,90),PATTERNS[0]),((90,90,70),PATTERNS[1]),
                                ((90,85,90),PATTERNS[2]),((85,85,80),PATTERNS[3])):
            with self.subTest(expected=expected):
                StudentActivityScore.objects.filter(activity__offering=self.offering_a,activity__template_period=self.midterm).update(raw_score=70,computed_score=85)
                for suffix,value in (("QUIZZES",target[0]),("PARTICIPATION_OUTPUT",target[1])):
                    StudentActivityScore.objects.filter(activity__template_period=self.prefinal,
                        activity__template_subcomponent__code__endswith=suffix).update(raw_score=(value-50)*2,computed_score=value)
                StudentPeriodGrade.objects.filter(template_period=self.final).update(exam_grade=target[2])
                result=self.raw()
                self.assertEqual(result["classifications"][0]["category"],expected)
                self.assertEqual(result["analyzed"],1)
                self.assertEqual(sum(p["count"] for p in result["patterns"]),1)
                self.assertEqual(sum(p["percentage"] for p in result["patterns"]),100)

    def test_inactive_historical_student_excluded_and_tenant_failure_protected(self):
        self.students[-1].is_active=False; self.students[-1].save()
        self.assertEqual(self.entry()["matched"],5)
        from apps.tenants.models import Tenant
        other=Tenant.objects.create(code="FOREIGN",name="Foreign")
        StudentPeriodGrade.objects.filter(template_period=self.final,student=self.students[0]).update(tenant=other)
        row=self.entry()
        self.assertEqual(row["category"],INSUFFICIENT)
        self.assertIsNone(row["pairs"][-1]["exam"])

    def test_final_lock_and_mapping_changes_invalidate_tokens(self):
        token=self.report()["token"]
        self.lock.is_locked=False; self.lock.save()
        self.assertEqual(self.post("page",token=token).status_code,409)
        self.lock.is_locked=True; self.lock.save()
        token=self.report()["token"]
        self.final_exam.score_input_mode="DIRECT_PERCENTAGE"; self.final_exam.save()
        self.assertEqual(self.post("page",token=token).status_code,409)

    def test_get_options_are_lazy_and_record_reads_do_not_write(self):
        with patch("apps.admin_portal.assessment_alignment_views.build_report",side_effect=AssertionError("unexpected report")):
            self.assertEqual(self.client.get(self.url).status_code,200)
            self.assertEqual(self.post("options").status_code,200)
        models=(StudentActivityScore,StudentPeriodGrade,GradeSubmission,GradeActivity,GradingPeriodLock)
        before={m:list(m.objects.order_by("pk").values()) for m in models}
        self.raw()
        self.assertEqual(before,{m:list(m.objects.order_by("pk").values()) for m in models})

    def test_scope_bound_tokens_cannot_cross_preview_or_user(self):
        token=self.report()["token"]
        original=reverse("admin_portal:learning_performance_insights_preview")
        response=self.client.post(original,{**{k:v for k,v in self.filters.items() if v is not None},"action":"page","token":token})
        self.assertEqual(response.status_code,409)
        UserPermission.objects.create(user=self.area_chair,permission=self.permission,tenant=self.tenant,campus=self.campus,grant_type="DENY")
        self.assertEqual(self.post("page",token=token).status_code,409)

    def test_explicit_equivalency_keeps_course_scope_and_rejects_inconsistent_unit(self):
        from apps.departmental_exams.models import (_equivalency_lifecycle_service_scope,
            ExamCourseEquivalencyGroup, ExamCourseEquivalencyMembership, CourseExamConfiguration)
        from apps.academics.models import Course
        course=self.cycles["FINAL"]
        cycle=course.cycle; cycle.processing_mode="AUTOMATIC_GENERATION"; cycle.save()
        equivalent=Course.objects.create(tenant=self.tenant,code="EXPLICIT-OTHER",title="Equivalent course")
        member=CycleCourse.objects.create(cycle=cycle,course=equivalent,exam_classification="DEPARTMENTAL")
        with _equivalency_lifecycle_service_scope():
            group=ExamCourseEquivalencyGroup.objects.create(cycle=cycle,name="Explicit unit",primary_cycle_course=course,
                created_by=self.area_chair,updated_by=self.area_chair)
            for row in (course,member):
                ExamCourseEquivalencyMembership.objects.create(group=group,cycle_course=row,added_by=self.area_chair)
                CourseExamConfiguration.objects.create(cycle_course=row)
        self.assertEqual(self.entry()["category"],PATTERNS[0])
        # No result/roster from the equivalent course is blended with this class.
        self.assertEqual(self.entry()["matched"],6)
        token=self.report()["token"]
        CycleCourse.objects.filter(pk=member.pk).update(inclusion_status="EXEMPT")
        self.assertEqual(self.post("page",token=token).status_code,409)
        self.assertEqual(self.entry()["category"],INSUFFICIENT)

    def test_duplicate_mapping_and_unknown_scoring_modes_fail_closed(self):
        sub=GradingTemplateSubcomponent.objects.create(template_component=self.pre_cs,
            code="QUIZ",name="Another quiz bucket",weight_percentage=10)
        self.assertEqual(self.entry()["category"],INSUFFICIENT)
        sub.is_active=False; sub.save()
        self.final_exam.score_input_mode="UNKNOWN"; self.final_exam.save()
        self.assertEqual(self.entry()["category"],INSUFFICIENT)

    def test_supported_exam_plus_unsupported_bucket_is_unavailable(self):
        self.assertEqual(self.entry()["category"], PATTERNS[0])
        extra = GradingTemplateComponent.objects.create(template_period=self.final,
            code="FINAL_OTHER_EXAM", name="Another exam", weight_percentage=0,
            is_exam_component=True, score_input_mode="RAW_BASE50")
        self.assertEqual(self.entry()["category"], INSUFFICIENT)
        extra.score_input_mode = "UNKNOWN"
        extra.save(update_fields=["score_input_mode"])
        row = self.entry()
        self.assertEqual(row["category"], INSUFFICIENT)
        self.assertIsNone(row["pairs"][-1]["exam"])
        self.assertIn("exactly one active supported exam bucket", row["detail"])
        self.assertEqual(row["pairs"][0]["quiz"], Decimal("68.02"))
        self.assertEqual(row["pairs"][0]["participation"], 85)
        extra.is_active = False
        extra.save(update_fields=["is_active"])
        self.assertEqual(self.entry()["category"], PATTERNS[0])

    def test_single_exam_modes_preserve_saved_manual_midterm_and_fx(self):
        saved = list(StudentPeriodGrade.objects.order_by("pk").values())
        self.final_exam.code = "FINAL_FX"
        self.final_exam.save(update_fields=["code"])
        self.assertFalse(GradeActivity.objects.filter(template_component=self.final_exam).exists())
        for mode, expected in (("UNKNOWN", None), ("RAW_BASE50", 90), ("DIRECT_PERCENTAGE", 90)):
            with self.subTest(mode=mode):
                self.final_exam.score_input_mode = mode
                self.final_exam.save(update_fields=["score_input_mode"])
                row = self.entry()
                self.assertEqual(row["pairs"][-1]["exam"], expected)
                self.assertEqual(row["pairs"][0]["exam"], 80)
                if expected is None:
                    self.assertEqual(row["category"], INSUFFICIENT)
                    self.assertIn("supported exam bucket", row["exams"][-1]["reason"])
        self.assertEqual(saved, list(StudentPeriodGrade.objects.order_by("pk").values()))

    def conflicting_final_snapshot(self):
        from apps.academics.models import Term
        term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year,
            code="OTHER-TERM", name="Other term", sequence_no=2)
        cycle = ExaminationCycle.objects.create(tenant=self.tenant, academic_year=self.academic_year,
            term=term, exam_period="FINAL", created_by=self.area_chair)
        course = CycleCourse.objects.create(cycle=cycle, course=self.course,
            exam_classification="DEPARTMENTAL")
        return CycleCourseOffering.objects.create(cycle_course=course,
            offering=self.offering_a, campus=self.campus)

    def test_conflicting_out_of_term_snapshot_invalidates_cached_token(self):
        self.assertEqual(self.entry()["category"], PATTERNS[0])
        token = self.report()["token"]
        self.conflicting_final_snapshot()
        self.assertEqual(self.entry()["category"], INSUFFICIENT)
        self.assertEqual(self.post("page", token=token).status_code, 409)
        fresh = self.report()
        self.assertEqual(sum(p["count"] for p in fresh["charts"]["patterns"]), 0)
        self.assertIsNone(fresh["charts"]["bars"][-1]["exam"])

    def test_policy_classification_and_snapshot_links_invalidate_tokens(self):
        from apps.departmental_exams.models import _classification_service_scope
        from apps.academics.models import Course, Term
        other = Course.objects.create(tenant=self.tenant, code="UNRELATED", title="Unrelated")
        term = Term.objects.create(tenant=self.tenant, academic_year=self.academic_year,
            code="OTHER-LINK", name="Other link")
        outside_cycle = ExaminationCycle.objects.create(tenant=self.tenant, academic_year=self.academic_year,
            term=term, exam_period="FINAL", created_by=self.area_chair)
        course = self.cycles["FINAL"]
        snapshot = CycleCourseOffering.objects.get(cycle_course=course)
        checks = (
            (CycleCourse.objects.filter(pk=course.pk), "exam_classification", "STANDARDIZED"),
            (CycleCourse.objects.filter(pk=course.pk), "inclusion_status", "EXEMPT"),
            (CycleCourse.objects.filter(pk=course.pk), "course_id", other.pk),
            (CycleCourse.objects.filter(pk=course.pk), "cycle_id", outside_cycle.pk),
            (CycleCourseOffering.objects.filter(pk=snapshot.pk), "offering_id", self.offering_b.pk),
            (ExaminationCycle.objects.filter(pk=course.cycle_id), "is_active", False),
        )
        for queryset, field, value in checks:
            with self.subTest(model=queryset.model.__name__, field=field), _classification_service_scope():
                previous = queryset.values_list(field, flat=True).get()
                token = self.report()["token"]
                queryset.update(**{field: value})
                self.assertEqual(self.post("page", token=token).status_code, 409)
                if field == "exam_classification":
                    self.assertEqual(self.entry()["pairs"][-1]["type"], "Standardized")
                else:
                    self.assertEqual(self.entry()["category"], INSUFFICIENT)
                queryset.update(**{field: previous})

    def test_conflicting_snapshot_edits_deletion_and_relink_invalidate_tokens(self):
        from apps.academics.models import Course
        conflict = self.conflicting_final_snapshot()
        token = self.report()["token"]
        CycleCourse.objects.filter(pk=conflict.cycle_course_id).update(inclusion_status="EXEMPT")
        self.assertEqual(self.post("page", token=token).status_code, 409)
        token = self.report()["token"]
        other = Course.objects.create(tenant=self.tenant, code="OTHER-SNAPSHOT", title="Other snapshot")
        alternate = CycleCourse.objects.create(cycle=conflict.cycle_course.cycle,
            course=other, exam_classification="DEPARTMENTAL")
        CycleCourseOffering.objects.filter(pk=conflict.pk).update(cycle_course=alternate)
        self.assertEqual(self.post("page", token=token).status_code, 409)
        self.assertEqual(self.entry()["category"], INSUFFICIENT)
        token = self.report()["token"]
        conflict.delete()
        self.assertEqual(self.post("page", token=token).status_code, 409)
        self.assertEqual(self.entry()["category"], PATTERNS[0])

    def test_unrelated_tenant_and_unlinked_cycle_do_not_invalidate_safe_cache(self):
        from apps.tenants.models import Tenant
        from apps.academics.models import AcademicYear, Term, Course
        token = self.report()["token"]
        tenant = Tenant.objects.create(code="UNRELATED-TENANT", name="Unrelated tenant")
        year = AcademicYear.objects.create(tenant=tenant, code="OTHER", name="Other",
            start_date=self.academic_year.start_date, end_date=self.academic_year.end_date)
        term = Term.objects.create(tenant=tenant, academic_year=year, code="OTHER", name="Other")
        cycle = ExaminationCycle.objects.create(tenant=tenant, academic_year=year,
            term=term, exam_period="FINAL", created_by=self.area_chair)
        course = Course.objects.create(tenant=tenant, code="PRIVATE-POLICY-CODE", title="Private")
        foreign = CycleCourse.objects.create(cycle=cycle, course=course, exam_classification="DEPARTMENTAL")
        # Even within this tenant, an unlinked course policy cannot affect any
        # authorized offering in the report's year/term population.
        local_course = Course.objects.create(tenant=self.tenant, code="UNLINKED", title="Unlinked")
        CycleCourse.objects.create(cycle=self.cycles["FINAL"].cycle,
            course=local_course, exam_classification="DEPARTMENTAL")
        with patch("apps.admin_portal.assessment_alignment_views.build_report",
                   side_effect=AssertionError("unchanged safe cache was rebuilt")):
            response = self.post("page", token=token)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("PRIVATE-POLICY-CODE", response.content.decode())
        self.assertNotIn(self.students[0].student_no, response.json()["html"])
        # A corrupt cross-tenant reference does affect eligibility, but only its
        # opaque digest and an unavailable result may escape the policy loader.
        CycleCourseOffering.objects.create(cycle_course=foreign,
            offering=self.offering_a, campus=self.campus)
        self.assertEqual(self.post("page", token=token).status_code, 409)
        fresh = self.report()
        self.assertEqual(sum(p["count"] for p in fresh["charts"]["patterns"]), 0)
        self.assertNotIn("PRIVATE-POLICY-CODE", str(fresh))
        self.assertNotIn(self.students[0].student_no, fresh["html"])

    def test_equivalency_dependency_graph_including_conflicting_cycles_is_fingerprinted(self):
        from apps.departmental_exams.models import (_equivalency_lifecycle_service_scope,
            ExamCourseEquivalencyGroup, ExamCourseEquivalencyMembership, CourseExamConfiguration)
        from apps.academics.models import Course
        course = self.cycles["FINAL"]
        cycle = course.cycle
        cycle.processing_mode = "AUTOMATIC_GENERATION"
        cycle.save(update_fields=["processing_mode"])
        equivalent = Course.objects.create(tenant=self.tenant, code="LINKED", title="Linked")
        member = CycleCourse.objects.create(cycle=cycle, course=equivalent, exam_classification="DEPARTMENTAL")
        conflict = self.conflicting_final_snapshot()
        outside_cycle = conflict.cycle_course.cycle
        conflict.delete()
        with _equivalency_lifecycle_service_scope():
            group = ExamCourseEquivalencyGroup.objects.create(cycle=cycle, name="Linked unit",
                primary_cycle_course=course, created_by=self.area_chair, updated_by=self.area_chair)
            for row in (course, member):
                ExamCourseEquivalencyMembership.objects.create(group=group, cycle_course=row,
                    added_by=self.area_chair)
                CourseExamConfiguration.objects.create(cycle_course=row)
        checks = (
            (CycleCourse.objects.filter(pk=member.pk), "cycle_id", outside_cycle.pk),
            (CourseExamConfiguration.objects.filter(cycle_course=member), "general_instructions", "Changed instructions"),
            (ExamCourseEquivalencyGroup.objects.filter(pk=group.pk), "cycle_id", outside_cycle.pk),
            (ExamCourseEquivalencyGroup.objects.filter(pk=group.pk), "primary_cycle_course_id", self.cycles["MIDTERM"].pk),
            (ExamCourseEquivalencyMembership.objects.filter(cycle_course=member), "active_marker", None),
        )
        for queryset, field, value in checks:
            with self.subTest(model=queryset.model.__name__, field=field), _equivalency_lifecycle_service_scope():
                previous = queryset.values_list(field, flat=True).get()
                self.assertEqual(self.entry()["category"], PATTERNS[0])
                token = self.report()["token"]
                queryset.update(**{field: value})
                self.assertEqual(self.post("page", token=token).status_code, 409)
                self.assertEqual(self.entry()["category"], INSUFFICIENT)
                queryset.update(**{field: previous})
        token = self.report()["token"]
        with _equivalency_lifecycle_service_scope():
            ExamCourseEquivalencyGroup.objects.filter(pk=group.pk).update(is_active=False,
                retired_by=self.area_chair, retired_at=timezone.now(), retirement_reason="Synthetic retirement")
        self.assertEqual(self.post("page", token=token).status_code, 409)
        self.assertEqual(self.entry()["category"], PATTERNS[0])


    def test_four_period_other_school_infers_same_period_not_ncba_prefinal_mapping(self):
        self.tenant.code="OTHER"; self.tenant.save()
        CycleCourseOffering.objects.all().delete()
        row=self.entry()
        self.assertEqual([(p["source"],p["destination"]) for p in row["pairs"]],
            [("PRELIM","PRELIM"),("MIDTERM","MIDTERM"),("PREFINAL","PREFINAL"),("FINAL","FINAL")])
        self.assertEqual(row["category"],INSUFFICIENT)
        self.assertIsNone(row["pairs"][-1]["quiz"])
