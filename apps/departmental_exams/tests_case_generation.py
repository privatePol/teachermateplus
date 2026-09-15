"""Phase 3 focused whole-unit, immutable rendering and lifecycle contracts."""
import itertools
import random
from collections import Counter
from dataclasses import replace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.services.features import FeatureSettingsService
from apps.core.services.settings import SystemSettingService

from .automatic_workflow import AutomaticExamDeadlineService
from .contribution_services import QuestionMutationService
from .generation_algorithms import IdentityBlock, IdentityMember, order_selected_blocks
from .generation_readiness import Stage6ReadinessService
from .generation_services import ExamGenerationService, GenerationConflict
from .models import (CourseExamConfiguration, ExamGenerationRevision, ExamScenario,
                     ExamScenarioMember, GeneratedExamItem, Question)
from .questionnaire_printing import _sanitized_questionnaire_context
from .setup_services import CourseSetupService
from .stage4_test_support import Stage4TestCase
from .structured_snapshots import ALGORITHM_VERSION, verify_structured_set
from .tests_faculty_cases import FacultyCaseFixtureMixin
from . import tests_faculty_cases as case_fixtures
from .whole_unit_selection import solve_whole_unit_two_sets


class WholeUnitSelectionTests(SimpleTestCase):
    def block(self, identity, size=1, section=1, campus=1, difficulty="EASY", fingerprints=None):
        members = tuple(IdentityMember(identity * 10 + i, 1, campus, difficulty, section, i + 1)
                        for i in range(size))
        return IdentityBlock(str(identity), (size, size if campus == 1 else 0,
                             size if campus == 2 else 0, size if section == 1 else 0,
                             size if section == 2 else 0), members,
                             logical_fingerprints=tuple(fingerprints or (f"q{m.source_id}" for m in members)))

    def solve(self, blocks, *, total=4, campus=(4, 0), section=(4, 0), budget=100000,
              optimize=True):
        return solve_whole_unit_two_sets(
            margins=(total, *campus, *section), blocks=blocks,
            campus_quotas={1: campus[0], 2: campus[1]},
            difficulty_quotas={"EASY": 1, "MODERATE": total - 2, "DIFFICULT": 1},
            secret="test", hmac_context={}, max_states=budget, optimize_soft=optimize)

    def test_whole_cases_exact_order_and_minimum_overlap(self):
        blocks = [self.block(i, 2) for i in range(1, 4)]
        result = self.solve(blocks)
        self.assertTrue(result.feasible)
        self.assertEqual(result.overlap, 2)
        for code, ids in (("A", result.set_a_block_ids), ("B", result.set_b_block_ids)):
            ordered = order_selected_blocks(blocks=blocks, selected_block_ids=ids,
                set_code=code, secret="test", hmac_context={}, section_order=(1, 2))
            self.assertEqual([m.member_order for m in ordered], [1, 2, 1, 2])
        self.assertEqual(self.solve(list(reversed(blocks))), result)

    def test_case_size_mismatch_and_budget_are_distinct(self):
        blocks = [self.block(i, 3) for i in range(1, 4)]
        result = self.solve(blocks)
        self.assertFalse(result.feasible)
        self.assertFalse(result.limit_hit)
        self.assertTrue(self.solve(blocks, budget=0).limit_hit)

    def test_hard_valid_result_survives_soft_budget(self):
        blocks = [self.block(i, 2) for i in range(1, 4)]
        hard = self.solve(blocks, optimize=False)
        result = self.solve(blocks, budget=hard.states_explored + 1)
        self.assertTrue(result.feasible)
        self.assertFalse(result.limit_hit)
        self.assertTrue(result.optimization_limit_hit)
        self.assertFalse(result.difficulty_target_met)

    def test_mixed_sections_campuses_and_singletons(self):
        blocks = [self.block(1, 2), self.block(2, 1, 2, 2), self.block(3, 1, 2, 2),
                  self.block(4, 2), self.block(5, 1, 2, 2), self.block(6, 1, 2, 2)]
        result = self.solve(blocks, campus=(2, 2), section=(2, 2))
        self.assertTrue(result.feasible)
        self.assertEqual(result.overlap, 0)

    def test_duplicate_members_reserve_entire_units(self):
        blocks = [self.block(1, 2, fingerprints=("shared", "a")),
                  self.block(2, 2, fingerprints=("shared", "b")), self.block(3, 2)]
        result = self.solve(blocks)
        self.assertTrue(result.feasible)
        self.assertEqual(result.overlap, 4)
        self.assertFalse({"1", "2"} <= set(result.set_a_block_ids + result.set_b_block_ids))

    def test_small_exhaustive_oracle(self):
        rng = random.Random(731)
        for trial in range(24):
            blocks = [self.block(i + 1, rng.choice((1, 2)), rng.choice((1, 2)),
                                 rng.choice((1, 2)), rng.choice(("EASY", "MODERATE", "DIFFICULT")))
                      for i in range(6)]
            expected = None
            for states in itertools.product(range(4), repeat=len(blocks)):
                selected = [[b for b, state in zip(blocks, states) if state & bit] for bit in (1, 2)]
                if any(tuple(sum(b.vector[j] for b in rows) for j in range(5)) != (4, 2, 2, 2, 2)
                       for rows in selected):
                    continue
                overlap = sum(b.size for b, state in zip(blocks, states) if state == 3)
                deviation = sum(sum(abs(Counter(m.difficulty for b in rows for m in b.members)[d] - n)
                                    for d, n in {"EASY": 1, "MODERATE": 2, "DIFFICULT": 1}.items())
                                for rows in selected)
                score = (overlap, deviation)
                expected = score if expected is None else min(expected, score)
            result = self.solve(blocks, campus=(2, 2), section=(2, 2))
            self.assertEqual(result.feasible, expected is not None, trial)
            self.assertFalse(result.limit_hit, trial)
            if expected:
                self.assertEqual((result.overlap, result.difficulty_deviation), expected, trial)


class CaseGenerationTests(FacultyCaseFixtureMixin, Stage4TestCase):
    _linked_question_form = case_fixtures.FacultyCaseWorkflowTests._linked_question_form
    _complete_question_form = case_fixtures.FacultyCaseWorkflowTests._complete_question_form
    _question_mutation_snapshot = case_fixtures.FacultyCaseWorkflowTests._question_mutation_snapshot
    test_automatic_direct_deny = case_fixtures.FacultyCaseWorkflowTests.test_direct_deny_blocks_preview_save_and_guessed_case_routes
    test_automatic_tampered_link = case_fixtures.FacultyCaseWorkflowTests.test_linked_form_rejects_missing_tampered_case_and_fixed_section
    test_automatic_feature_off = case_fixtures.FacultyCaseWorkflowTests.test_feature_off_preserves_existing_question_ui_and_case_route_denies
    def make_cycle(self, **kwargs):
        self.configurer = self.admin
        cycle = super().make_cycle(**kwargs)
        cycle.processing_mode = "AUTOMATIC_GENERATION"
        cycle.automatic_contributor_completion_policy = "SUFFICIENT_POOL"
        cycle.save()
        return cycle

    def make_course(self, **kwargs):
        parent = super().make_course(**kwargs)
        CourseSetupService.classify(course_id=parent.id, tenant_id=self.tenant.id, actor=self.admin,
            classification="DEPARTMENTAL", expected_state=CourseSetupService.fingerprint(parent))
        parent.refresh_from_db()
        return parent

    def fill_and_submit(self, *, count=50, submit=True, first_narrative=None):
        cases = []
        for i in range(count // 5):
            self.contribution.refresh_from_db()
            section = self.section_a if i < 6 else self.section_b
            scenario = self.save_case(title=f"Case {i}", section=section,
                html=first_narrative if i == 0 and first_narrative else '<p>Accounting ₱500</p><table><tr><td class="tmp-rule-double">Total</td><td>500</td></tr></table>')
            for j in range(5):
                self.add_question(scenario=scenario, section=section, text=f"Faculty {self.faculty.id} Case {i} linked MCQ {j}")
            # Explicitly reverse encoded membership to prove source IDs are
            # never used to sort members in selection or rendering.
            from .faculty_case_services import FacultyCaseMutationService
            scenario.refresh_from_db()
            self.contribution.refresh_from_db()
            FacultyCaseMutationService.reorder_members(
                contribution_id=self.contribution.id, scenario_id=scenario.id,
                user=self.faculty, tenant_id=self.tenant.id, campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision,
                expected_scenario_revision=scenario.revision,
                ordered_question_ids=list(scenario.members.order_by('-position').values_list('question_id', flat=True)))
            cases.append(scenario)
        self.contribution.refresh_from_db()
        if submit:
            QuestionMutationService.submit(contribution_id=self.contribution.id, user=self.faculty,
                tenant_id=self.tenant.id, campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision)
        self.contribution.refresh_from_db()
        return cases

    def generate(self):
        CourseExamConfiguration.objects.filter(pk=self.configuration.id).update(
            reopened_contribution_deadline=timezone.now() - timezone.timedelta(minutes=1))
        result = AutomaticExamDeadlineService.process_course(cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id)
        self.assertEqual(result.status, "GENERATED", result)
        return ExamGenerationRevision.objects.get(cycle_course=self.parent, current_marker=1)

    def test_authoring_deadline_generation_snapshots_print_and_retry(self):
        cases = self.fill_and_submit()
        before = AutomaticExamDeadlineService.process_course(cycle_course_id=self.parent.id,
            tenant_id=self.tenant.id)
        self.assertEqual(before.code, "NOT_DUE")
        revision = self.generate()
        self.assertEqual(revision.algorithm_version, ALGORITHM_VERSION)
        from .questionnaire_printing import QuestionnairePrintReleaseService, FacultyQuestionnairePrintService
        release = QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=revision.id, tenant_id=self.tenant.id,
            actor=self.admin, print_from=timezone.now() - timezone.timedelta(minutes=1),
            print_until=timezone.now() + timezone.timedelta(hours=1))
        for generated_set in revision.generated_sets.order_by('set_code'):
            items = list(generated_set.items.order_by('position'))
            verify_structured_set(generated_set, items, algorithm_version=revision.algorithm_version)
            for scenario in cases:
                actual = [q.source_question_id for q in items if q.scenario_id_snapshot == scenario.id]
                expected = list(scenario.members.order_by('position').values_list('question_id', flat=True))
                self.assertEqual(actual, expected)
            context = _sanitized_questionnaire_context(revision=revision, generated_set=generated_set)
            faculty_context = FacultyQuestionnairePrintService.build_safe_context(
                contribution=self.contribution, release_id=release.id, set_code=generated_set.set_code,
                actor=self.faculty)
            self.assertEqual(context["items"], faculty_context["items"])
            html = render_to_string('departmental_exams/faculty/questionnaire_print.html', context)
            self.assertEqual(html.count('Accounting ₱500'), 10)
            self.assertIn('tmp-rule-double', html)
            self.assertIn('questionnaire-ending', html)
            self.assertNotIn('correct_answer', context)
        retry = AutomaticExamDeadlineService.process_course(cycle_course_id=self.parent.id, tenant_id=self.tenant.id)
        self.assertEqual(retry.code, 'CURRENT_GENERATION_EXISTS')

    def test_unusable_case_is_excluded_whole_with_warning(self):
        self.fill_and_submit()
        question = self.contribution.questions.first()
        case_id = question.exam_scenario_membership.scenario_id
        Question.objects.filter(pk=question.pk).update(choice_a='')
        with patch("apps.departmental_exams.whole_unit_selection.solve_whole_unit_two_sets", side_effect=AssertionError("GET must not solve")):
            report = Stage6ReadinessService.evaluate_automatic_pool(cycle_course=self.parent, exact_feasibility=False)
        self.assertIn('UNUSABLE_CASE_EXCLUDED', [w['code'] for w in report['warnings']])
        self.assertEqual(report['eligible_question_count'], 45)
        self.assertEqual(report['scenario_count'], 9)

    def test_snapshot_corruption_fails_without_live_case_fallback(self):
        long_narrative = "<p>" + "Preserved accounting explanation. " * 220 + "</p>"
        cases = self.fill_and_submit(first_narrative=long_narrative)
        revision = self.generate()
        generated_set = revision.generated_sets.first()
        before = _sanitized_questionnaire_context(revision=revision, generated_set=generated_set)
        self.assertTrue(any(len(item["case_content"]) > 5000 for item in before["items"]))
        ExamScenario.objects.filter(pk=cases[0].pk).update(stimulus='<p>Changed live content</p>')
        after = _sanitized_questionnaire_context(revision=revision, generated_set=generated_set)
        self.assertEqual(before['items'], after['items'])
        from .questionnaire_printing import QuestionnairePrintReleaseService, FacultyQuestionnairePrintService
        release = QuestionnairePrintReleaseService.release(
            cycle_course_id=self.parent.id, revision_id=revision.id, tenant_id=self.tenant.id,
            actor=self.admin, print_from=timezone.now() - timezone.timedelta(minutes=1),
            print_until=timezone.now() + timezone.timedelta(hours=1))
        item = generated_set.items.first()
        GeneratedExamItem.objects.filter(pk=item.pk).update(scenario_stimulus_snapshot='<p>Corrupt</p>')
        with self.assertRaises(PermissionDenied):
            _sanitized_questionnaire_context(revision=revision, generated_set=generated_set)
        with patch("apps.departmental_exams.questionnaire_printing.AuditService.log_event") as audit:
            with self.assertRaises(PermissionDenied):
                FacultyQuestionnairePrintService.build_safe_context(
                    contribution=self.contribution, release_id=release.id,
                    set_code=generated_set.set_code, actor=self.faculty)
            audit.assert_not_called()

    def test_malformed_case_order_and_duplicate_members_rejected_before_submission(self):
        from .faculty_case_services import FacultyCasePolicy
        cases = self.fill_and_submit(submit=False)
        members = list(cases[0].members.order_by("position"))
        ExamScenarioMember.objects.filter(pk=members[0].pk).update(position=6)
        with self.assertRaisesMessage(ValidationError, "restore a complete sequence"):
            FacultyCasePolicy.validate_submission(contribution=self.contribution,
                questions=list(self.contribution.questions.all()), tenant_id=self.tenant.id)
        ExamScenarioMember.objects.filter(pk=members[0].pk).update(position=1)
        Question.objects.filter(pk=members[0].question_id).update(question_text=members[1].question.question_text)
        with self.assertRaisesMessage(ValidationError, "duplicate logical MCQs"):
            FacultyCasePolicy.validate_submission(contribution=self.contribution,
                questions=list(self.contribution.questions.all()), tenant_id=self.tenant.id)

    def test_excluded_case_does_not_block_sufficient_remaining_units(self):
        cases = self.fill_and_submit()
        self.faculty, self.contribution = self.other_faculty, self.other_contribution
        self.fill_and_submit()
        bad_member = cases[0].members.first()
        Question.objects.filter(pk=bad_member.question_id).update(choice_a="")
        revision = self.generate()
        self.assertEqual(revision.minimum_overlap, 5)
        self.assertFalse(GeneratedExamItem.objects.filter(generated_set__generation_revision=revision,
                                                        scenario_id_snapshot=cases[0].id).exists())
        report = Stage6ReadinessService.evaluate_automatic_pool(cycle_course=self.parent, exact_feasibility=False)
        self.assertTrue(report["aggregate_requirements_met"], report)
        self.assertIn("UNUSABLE_CASE_EXCLUDED", [w["code"] for w in report["warnings"]])
        from .automatic_workflow import AutomaticGenerationSummaryService
        summary = AutomaticGenerationSummaryService.build(cycle=self.cycle)
        self.assertIn("UNUSABLE_CASE_EXCLUDED", [w["code"] for w in summary["generated"][0]["warnings"]])
        from .automatic_generation_audit import AutomaticGenerationAuditService
        findings, _counts = AutomaticGenerationAuditService._build_findings(revision=revision)
        self.assertNotIn("FAIL", [row["status"] for row in findings])

    def test_stale_rich_inputs_and_audit_failure_preserve_history(self):
        cases = self.fill_and_submit()
        first = self.generate()
        problem, report = Stage6ReadinessService.build_problem(cycle_course=self.parent)
        self.assertTrue(report["ready"], report)
        ExamScenario.objects.filter(pk=cases[0].id).update(title="Changed after readiness")
        kwargs = dict(cycle_course_id=self.parent.id, tenant_id=self.tenant.id, actor=self.admin,
                      expected_current_revision=1, expected_input_fingerprint=problem.input_fingerprint,
                      request_token="phase3-regeneration" * 4, regeneration=True)
        with self.assertRaises(GenerationConflict):
            ExamGenerationService.generate(**kwargs)
        fresh, report = Stage6ReadinessService.build_problem(cycle_course=self.parent)
        kwargs["expected_input_fingerprint"] = fresh.input_fingerprint
        with patch.object(ExamGenerationService, "_audit", side_effect=RuntimeError("test audit failure")):
            with self.assertRaises(RuntimeError):
                ExamGenerationService.generate(**kwargs)
        first.refresh_from_db()
        self.assertEqual(first.current_marker, 1)
        self.assertEqual(ExamGenerationRevision.objects.count(), 1)
        second = ExamGenerationService.generate(**kwargs).revision
        self.assertEqual(second.revision_number, 2)
        self.assertTrue(first.generated_sets.first().items.filter(scenario_title_snapshot="Case 0").exists())

    def test_unfinished_case_submission_and_post_deadline_edits_denied(self):
        self.fill_and_submit(submit=False)
        empty = self.save_case(title="Incomplete Case")
        self.contribution.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "at least one Linked Question"):
            QuestionMutationService.submit(contribution_id=self.contribution.id, user=self.faculty,
                tenant_id=self.tenant.id, campus_id=self.campus.id,
                expected_contribution_revision=self.contribution.revision)
        CourseExamConfiguration.objects.filter(pk=self.configuration.id).update(
            reopened_contribution_deadline=timezone.now() - timezone.timedelta(seconds=1))
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.save_case(title="Too late")

    def test_blocked_nonempty_draft_is_excluded_from_whole_case_generation(self):
        from .models import FacultyContribution
        cases = self.fill_and_submit()
        self.faculty, self.contribution = self.other_faculty, self.other_contribution
        draft_case = self.save_case(title="Excluded unfinished Draft")
        self.add_question(scenario=draft_case, text="Draft-only question")
        FacultyContribution.objects.filter(pk=self.contribution.id).update(
            roster_status="BLOCKED", roster_blocked_at=timezone.now())
        self.contribution.eligibility_sources.update(is_current=False, invalidated_at=timezone.now())
        before = self._question_mutation_snapshot()[:5]
        revision = self.generate()
        self.assertEqual(before, self._question_mutation_snapshot()[:5])
        self.assertFalse(GeneratedExamItem.objects.filter(scenario_id_snapshot=draft_case.id).exists())
        for case in cases:
            expected = list(case.members.order_by("position").values_list("question_id", flat=True))
            for generated_set in revision.generated_sets.all():
                self.assertEqual(expected, list(generated_set.items.filter(scenario_id_snapshot=case.id)
                    .order_by("position").values_list("source_question_id", flat=True)))

    def test_section_surplus_does_not_cover_shortage_with_blocked_draft_warning(self):
        from .models import ExamSection, FacultyContribution, _exam_structure_lifecycle_service_scope
        # Construct the reported historical shape only in this disposable fixture.
        with _exam_structure_lifecycle_service_scope():
            ExamSection.objects.filter(pk=self.section_a.id).update(item_quota=10)
            ExamSection.objects.filter(pk=self.section_b.id).update(item_quota=40)
        scenario = self.save_case(title="Eleven linked items")
        for i in range(11):
            self.add_question(scenario=scenario, text=f"Case member {i}")
        for i in range(39):
            self.add_question(section=self.section_b, text=f"Standalone {i}")
        self.contribution.refresh_from_db()
        QuestionMutationService.submit(contribution_id=self.contribution.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=self.contribution.revision)
        FacultyContribution.objects.filter(pk=self.other_contribution.id).update(
            roster_status="BLOCKED", roster_blocked_at=timezone.now())
        self.other_contribution.eligibility_sources.update(is_current=False, invalidated_at=timezone.now())
        CourseExamConfiguration.objects.filter(pk=self.configuration.id).update(workflow_status="CLOSED")
        before = self._question_mutation_snapshot()
        with patch("apps.departmental_exams.whole_unit_selection.solve_whole_unit_two_sets") as solver:
            problem, report = Stage6ReadinessService.build_problem(cycle_course=self.parent)
            solver.assert_not_called()
        self.assertIsNone(problem)
        self.assertIn("QUESTION_SHORTAGES", [b["code"] for b in report["blockers"]])
        self.assertIn("BLOCKED_DRAFTS_UNRESOLVED", [w["code"] for w in report["warnings"]])
        self.assertTrue(any(s["dimension"] == "section" and s["available"] == 39 and s["required"] == 40
                            for s in report["shortages"]))
        self.assertEqual([(s["available"], s["required"]) for s in report["section_quotas"]], [(11, 10), (39, 40)])
        self.assertEqual(before, self._question_mutation_snapshot())

    def test_admin_preview_and_print_use_immutable_rich_blocks(self):
        cases = self.fill_and_submit()
        revision = self.generate()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("departmental_exams:generated_revision_detail", args=[revision.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Accounting \u20b1500", count=20)
        for generated_set in response.context["generated_sets"]:
            for scenario in cases:
                self.assertEqual([item.source_question_id for item in generated_set.ordered_items
                                  if item.scenario_id_snapshot == scenario.id],
                                 list(scenario.members.order_by("position").values_list("question_id", flat=True)))
        from .questionnaire_printing import AdminQuestionnairePrintService
        context = AdminQuestionnairePrintService.build_safe_context(revision=revision, set_code="B", actor=self.admin)
        self.assertEqual(sum(item["case_start"] for item in context["items"]), 10)

    def test_preopen_guidance_and_feature_gate(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("departmental_exams:course_setup", args=[self.cycle.id]))
        self.assertContains(response, "four 5-question Cases")
        self.assertContains(response, "not a required Case size")
        self.assertContains(response, "Mixed preparation is supported")
        from .setup_services import automatic_structure_blockers
        self.assertEqual(automatic_structure_blockers(self.parent), [])
        SystemSettingService.set(FeatureSettingsService.DEPARTMENTAL_EXAM_STRUCTURED_LIFECYCLE_ENABLED_KEY,
                                False, tenant_id=self.tenant.id, value_type="BOOL")
        self.assertTrue(automatic_structure_blockers(self.parent))
        self.configuration.refresh_from_db()
        state = self.configuration.workflow_status
        result = AutomaticExamDeadlineService.process_course(cycle_course_id=self.parent.id, tenant_id=self.tenant.id)
        self.assertEqual(result.code, "AUTOMATIC_STRUCTURE_UNSUPPORTED")
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.workflow_status, state)

    def test_equivalent_contributor_uses_primary_structure_with_exact_ownership(self):
        from .exam_units import ExamCourseEquivalencyService, resolve_examination_unit
        from .blueprint_services import BlueprintMutationService
        from .faculty_case_services import FacultyCaseMutationService, FacultyCasePolicy
        from .models import ExamBlueprint, FacultyContribution
        from .services import CourseExamConfigurationService
        cycle = self.make_cycle(status="OPEN", scope_suffix="CASE-EQUIV",
                                default_questions_required_per_faculty=50,
                                default_final_item_count=50,
                                default_contribution_deadline=self.future_deadline(), default_coverage="Cases")
        primary = self.make_course(cycle=cycle, code="CASE-PRIMARY")
        secondary = self.make_course(cycle=cycle, code="CASE-MEMBER")
        configs = [self.make_configuration(course, deadline=cycle.default_contribution_deadline) for course in (primary, secondary)]
        for course in (primary, secondary):
            self.make_assignment(course, self.faculty)
        ExamCourseEquivalencyService.create_group(cycle_id=cycle.id, name="Case unit",
            primary_cycle_course_id=primary.id, member_ids=[primary.id, secondary.id], actor=self.admin)
        blueprint, _ = BlueprintMutationService.save_structure(cycle_course_id=primary.id,
            tenant_id=self.tenant.id, actor=self.admin, expected_revision=0,
            mode=ExamBlueprint.Mode.NO_SECTIONS, sections=[])
        CourseExamConfigurationService.open_for_contribution(cycle_course_id=secondary.id,
            tenant_id=self.tenant.id, user=self.admin, expected_revision=configs[1].revision)
        contribution = FacultyContribution.objects.get(cycle_course=secondary, faculty_user=self.faculty)
        scenario, _ = FacultyCaseMutationService.save(contribution_id=contribution.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id,
            expected_contribution_revision=contribution.revision, expected_scenario_revision=0,
            title="Member-owned Case", raw_content="<p>Member narrative</p>", section_id=None)
        contribution.refresh_from_db()
        question = QuestionMutationService.create(contribution_id=contribution.id, user=self.faculty,
            tenant_id=self.tenant.id, campus_id=self.campus.id, expected_contribution_revision=contribution.revision,
            payload=self.payload("Equivalent member MCQ"), scenario_id=scenario.id)
        contribution.refresh_from_db()
        FacultyCasePolicy.validate_submission(contribution=contribution, questions=[question], tenant_id=self.tenant.id)
        self.assertEqual(scenario.blueprint_id, blueprint.id)
        self.assertEqual(scenario.contribution_id, contribution.id)
        from .structured_generation import assess_whole_units
        from .models import QuestionBlueprintPlacement
        contribution.status = "SUBMITTED"
        contribution.submitted_at = timezone.now()
        contribution.save(update_fields=["status", "submitted_at"])
        assessed = assess_whole_units(blueprint=blueprint, unit=resolve_examination_unit(primary),
                                     questions=[question], audit_questions=[], sections=[])
        self.assertEqual([row.id for row in assessed[0]], [question.id])
        self.assertEqual(len(assessed[3]), 1)
