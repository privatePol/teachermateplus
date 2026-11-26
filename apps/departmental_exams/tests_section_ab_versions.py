"""Paired section output, exact saved routing and historical compatibility."""
import copy
from collections import Counter
from dataclasses import dataclass, replace
from importlib import import_module
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase
from types import SimpleNamespace
from .generation_algorithms import IdentityBlock, IdentityMember
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.urls import reverse
from django.utils import timezone
from django.apps import apps

from . import set_modes
from .answer_key_release import AnswerKeyReleaseService, FacultyAnswerKeyReleaseService
from .generation_readiness import Stage6ReadinessService
from .generation_reporting import GenerationAnswerKeyService
from .generation_services import ExamGenerationService
from .models import ExamSectionSetAssignment, FacultyContribution, Question
from .personalized_answer_sheets import PersonalizedAnswerSheetService
from .questionnaire_printing import AdminQuestionnairePrintService, FacultyQuestionnairePrintService, QuestionnairePrintReleaseService
from . import tests_set_mode_remediation as output_fixtures
from . import tests_set_modes as fixtures
from .stage4_test_support import Stage4TestCase
from .tests_stage6_generation import Stage6BGenerationFixtureMixin


def historical_v1_plan(problem, report):
    """Build old single-section snapshot evidence before persistence, never rewrite a revision."""
    problem, report = ORIGINAL_APPLY(problem, report)
    snapshot = copy.deepcopy(problem.set_policy_snapshot)
    plans = []
    for plan in problem.section_set_plan:
        if plan["version"] == "A":
            plan = dict(plan, set_code=plan["set_code"][:-1])
            plan.pop("version")
            plans.append(plan)
    snapshot["schema"] = "section-sets-v1"
    snapshot["offerings"] = [{k:v for k,v in p.items() if k != "source_ids"} for p in plans]
    snapshot["difficulty_actual"] = {code[:-1]: value for code,value in snapshot["difficulty_actual"].items() if code.endswith("A")}
    snapshot["overlap"] = [{**row, "left":row["left"][:-1], "right":row["right"][:-1]} for row in snapshot["overlap"]]
    return replace(problem, set_policy_snapshot=snapshot, section_set_plan=tuple(plans)), report


ORIGINAL_APPLY = set_modes.apply_policy


class SectionABVersionTests(Stage6BGenerationFixtureMixin, Stage4TestCase):
    policy_course = fixtures.SectionSetIntegrationTests.policy_course
    def generate(self, parent):
        problem, report = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(report["ready"], report["blockers"])
        automatic = parent.cycle.processing_mode == "AUTOMATIC_GENERATION"
        revision = ExamGenerationService.generate(cycle_course_id=parent.id,tenant_id=self.tenant.id,
            actor=None if automatic else self.reviewer,generation_trigger="AUTOMATIC" if automatic else "MANUAL",
            expected_current_revision=0,expected_input_fingerprint=problem.input_fingerprint,
            request_token="section-ab-new-tests-request-token").revision
        return problem, report, revision

    def automatic_course(self):
        parent, _ = self.policy_course()
        parent.cycle.processing_mode = "AUTOMATIC_GENERATION"
        parent.cycle.save(update_fields=["processing_mode"])
        parent.refresh_from_db()
        return parent
    enroll = output_fixtures.SavedSetOutputRemediationTests.enroll
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.rbac.models import Permission
        Permission.objects.get_or_create(code="faculty_portal.access", defaults={"module":"faculty_portal","action":"access","description":"Faculty access"})

    def extra_offering(self, parent, offering, code):
        from apps.academics.models import Section, CourseOffering
        from .models import CycleCourseOffering
        section = Section.objects.create(tenant=self.tenant,campus=offering.campus,department=offering.department,
            program=offering.program,code=code,name="Other section")
        other = CourseOffering.objects.create(tenant=self.tenant,campus=offering.campus,department=offering.department,
            program=offering.program,academic_year=offering.academic_year,term=offering.term,course=offering.course,section=section)
        CycleCourseOffering.objects.create(cycle_course=parent,offering=other,campus=offering.campus)
        return other

    def release(self, parent, revision, campus):
        now = timezone.now()
        return QuestionnairePrintReleaseService.release(cycle_course_id=parent.id, revision_id=revision.id,
            tenant_id=self.tenant.id, actor=self.admin, target_campus_id=campus,
            print_from=now-timezone.timedelta(minutes=1), print_until=now+timezone.timedelta(hours=1))

    def test_three_sections_select_once_and_produce_six_questionnaires_and_keys(self):
        parent, _ = self.policy_course(shuffle=True)
        questions = list(Question.objects.filter(contribution__cycle_course=parent).order_by("id"))
        for index, question in enumerate(questions):
            Question.objects.filter(pk=question.pk).update(choices_fixed_order=index%2==0,
                choice_a="Hydrogen", choice_b="Helium", choice_c="Lithium", choice_d="Beryllium", correct_answer="B")
        seen = []
        select = set_modes.select_policy_blocks
        def recording(problem, usage, **kwargs):
            seen.append(dict(usage))
            return select(problem, usage, **kwargs)
        with patch.object(set_modes, "select_policy_blocks", side_effect=recording):
            problem, preview = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertEqual(len(seen), 3)
        first = problem.section_set_plan[0]
        self.assertEqual(seen[1], dict(Counter(problem.questions[q].normalized_fingerprint or f"q:{q}" for q in first["source_ids"])))
        _, _, revision = self.generate(parent)
        self.assertEqual(revision.generated_sets.count(), 6)
        self.assertEqual(len(preview["pairwise_overlap"]), 3)
        self.client.force_login(self.reviewer)
        for offering in parent.offering_snapshots.all():
            pair = list(revision.section_set_assignments.filter(offering_id=offering.offering_id).select_related("generated_set").order_by("version_code"))
            self.assertEqual([row.version_code for row in pair], ["A", "B"])
            source_orders = []
            for binding in pair:
                saved = binding.generated_set
                source_orders.append(list(saved.items.order_by("position").values_list("source_question_id", flat=True)))
                paper = self.client.get(reverse("departmental_exams:admin_questionnaire_print", args=[revision.id,saved.set_code]))
                self.assertEqual(paper.status_code, 200)
                self.assertEqual(paper.context["section_label"], binding.section_label_snapshot)
                self.assertEqual(paper.context["set_version"], binding.version_code)
                html = paper.content.decode()
                self.assertLess(html.index('class="course-code"'), html.index('class="section-name"'))
                self.assertLess(html.index('class="section-name"'), html.index('class="set-label"'))
                self.assertIn("SET "+binding.version_code, html)
                key = GenerationAnswerKeyService.build_context(revision=revision,set_code=saved.set_code)
                for item, answer, printed in zip(saved.items.order_by("position"), key["items"], paper.context["items"]):
                    self.assertEqual(answer["correct_answer_snapshot"], item.correct_answer_snapshot)
                    self.assertEqual(printed["choices"]["ABCD".index(item.correct_answer_snapshot)],
                                     item.original_choices_snapshot["ABCD".index(item.original_correct_answer_snapshot)])
                    if item.choices_fixed_order_snapshot:
                        self.assertEqual(item.choice_order_snapshot, [0,1,2,3])
            self.assertEqual(set(source_orders[0]), set(source_orders[1]))
            self.assertNotEqual(source_orders[0], source_orders[1])

    def test_pair_order_changes_even_with_choice_shuffle_disabled(self):
        parent, _ = self.policy_course(mode="SAME_ORDER",shuffle=False)
        _, _, revision = self.generate(parent)
        orders = {}
        for binding in revision.section_set_assignments.select_related("generated_set"):
            items = list(binding.generated_set.items.order_by("position"))
            self.assertTrue(all(i.choice_order_snapshot == [0,1,2,3] for i in items))
            orders.setdefault(binding.version_code, []).append(tuple(i.source_question_id for i in items))
        self.assertEqual(len(set(orders["A"])),1)
        self.assertEqual(len(set(orders["B"])),1)
        self.assertNotEqual(orders["A"][0], orders["B"][0])

    def test_full_length_case_does_not_hide_orderable_standalone_alternatives(self):
        from .blueprint_services import ScenarioMutationService
        from .contribution_services import ContributionRosterService
        from .models import CycleCourse, ExamGeneratorPolicy, _classification_service_scope
        from .services import CourseExamConfigurationService

        parent, configuration, campuses, offerings = self.make_stage6_open_course()
        parent.reviewer = self.reviewer
        parent.save(update_fields=["reviewer"])
        with _classification_service_scope():
            CycleCourse.objects.filter(pk=parent.id).update(exam_classification="DEPARTMENTAL")
        parent.refresh_from_db()
        for suffix in ("full-case", "alternatives"):
            self.add_faculty_source(parent=parent, campus=campuses["CUBAO"],
                                    offering=offerings["CUBAO"], suffix=suffix)
        ContributionRosterService.initialize(cycle_course_id=parent.id, tenant_id=self.tenant.id,
                                            actor=self.configurer)
        contributions = list(FacultyContribution.objects.filter(cycle_course=parent).order_by("id"))
        self.assertEqual(len(contributions), 2)
        for contribution in contributions:
            self.add_questions(contribution)
            contribution.status = FacultyContribution.Status.SUBMITTED
            contribution.submitted_at = timezone.now()
            contribution.save(update_fields=["status", "submitted_at"])
        configuration.refresh_from_db()
        CourseExamConfigurationService.close_contribution(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, user=self.configurer,
            expected_revision=configuration.revision,
            expected_roster_revision=configuration.contributor_roster_revision,
            reason="Both full-length contributions are complete.")
        self.no_sections_blueprint(parent)
        ExamGeneratorPolicy.objects.create(cycle=parent.cycle, departmental=True, mode="SAME_ORDER")
        problem, _ = Stage6ReadinessService.build_problem(cycle_course=parent)
        case_ids = list(Question.objects.filter(contribution=contributions[0])
                        .order_by("position").values_list("id", flat=True))
        alternative_ids = set(Question.objects.filter(contribution=contributions[1])
                              .values_list("id", flat=True))
        self.assertEqual(len(case_ids), problem.final_count)
        self.assertTrue(alternative_ids.isdisjoint(case_ids))
        scenario, _ = ScenarioMutationService.save(
            cycle_course_id=parent.id, tenant_id=self.tenant.id, actor=self.reviewer,
            title="Full-length indivisible Case", stimulus="Keep every linked member together.",
            question_ids=case_ids, section_id=None, expected_revision=0)

        problem, report, revision = self.generate(parent)
        self.assertEqual(revision.generated_sets.count(), 2)
        full_case = next(block for block in problem.blocks if str(block.block_id) == f"scenario:{scenario.id}")
        self.assertEqual(full_case.vector, problem.margins)
        alternatives = [block for block in problem.blocks
                        if block.members[0].source_id in alternative_ids]
        self.assertTrue(all(block.size == 1 for block in alternatives))
        self.assertEqual(tuple(sum(block.vector[j] for block in alternatives)
                               for j in range(len(problem.margins))), problem.margins)
        repeated, _ = Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertEqual(repeated.section_set_plan, problem.section_set_plan)
        self.assertTrue(report["ready"])
        for offering_id in revision.section_set_assignments.values_list("offering_id", flat=True).distinct():
            pair = list(revision.section_set_assignments.filter(offering_id=offering_id)
                        .select_related("generated_set").order_by("version_code"))
            self.assertEqual([row.version_code for row in pair], ["A", "B"])
            orders = [tuple(row.generated_set.items.order_by("position")
                            .values_list("source_question_id", flat=True)) for row in pair]
            self.assertEqual(len(orders[0]), problem.final_count)
            self.assertEqual(set(orders[0]), set(orders[1]))
            self.assertNotEqual(orders[0], orders[1])
            self.assertTrue(set(orders[0]).isdisjoint(case_ids))

    def test_both_versions_authorized_but_other_section_same_campus_denied(self):
        parent = self.automatic_course()
        contribution = FacultyContribution.objects.select_related("faculty_user").filter(cycle_course=parent).first()
        offering = parent.offering_snapshots.get(campus_id=contribution.source_campus_id).offering
        foreign = self.extra_offering(parent, offering, "OTHER-SECTION")
        _, _, revision = self.generate(parent)
        release = self.release(parent,revision,offering.campus_id)
        now=timezone.now()
        key_release = AnswerKeyReleaseService.release(cycle_course_id=parent.id,revision_id=revision.id,
            tenant_id=self.tenant.id,actor=self.admin,target_campus_id=offering.campus_id,recipient_course_id=parent.id,
            available_from=now-timezone.timedelta(minutes=1),available_until=now+timezone.timedelta(hours=1),attestation_confirmed=True)
        for binding in revision.section_set_assignments.filter(offering__in=[offering,foreign]).select_related("generated_set"):
            kwargs=dict(contribution=contribution,release_id=release.id,set_code=binding.generated_set.set_code,actor=contribution.faculty_user)
            if binding.offering_id==foreign.id:
                with self.assertRaises(PermissionDenied):
                    FacultyQuestionnairePrintService.build_safe_context(**kwargs)
                with self.assertRaises(PermissionDenied):
                    FacultyAnswerKeyReleaseService.build_safe_context(**{**kwargs,"release_id":key_release.id},printable=False)
            else:
                self.assertEqual(FacultyQuestionnairePrintService.build_safe_context(**kwargs)["set_version"],binding.version_code)
                self.assertEqual(FacultyAnswerKeyReleaseService.build_safe_context(**{**kwargs,"release_id":key_release.id},printable=False)["set_version"],binding.version_code)
                master = FacultyAnswerKeyReleaseService.build_checking_master_context(**{**kwargs,"release_id":key_release.id})
                self.assertEqual(master["set_version"], binding.version_code)
                self.assertEqual(master["section_label"], binding.section_label_snapshot)

    def test_personalized_sheets_balance_only_own_pair_and_reprint_persistently(self):
        parent=self.automatic_course()
        _, _, revision=self.generate(parent)
        contribution=FacultyContribution.objects.select_related("faculty_user").filter(cycle_course=parent).first()
        offering=parent.offering_snapshots.get(campus_id=contribution.source_campus_id).offering
        for index in range(3):
            self.enroll(offering,"PAIR-"+str(index))
        release=self.release(parent,revision,offering.campus_id)
        kwargs=dict(contribution=contribution,release_id=release.id,offering_id=offering.id,actor=contribution.faculty_user)
        PersonalizedAnswerSheetService.prepare(**kwargs)
        first=dict(revision.personalized_answer_sheet_assignments.values_list("enrollment_id","set_code"))
        allowed=set(revision.section_set_assignments.filter(offering=offering).values_list("generated_set__set_code",flat=True))
        self.assertEqual(set(first.values()),allowed)
        self.assertEqual(sorted(Counter(first.values()).values()),[1,2])
        for version in "AB":
            paper=PersonalizedAnswerSheetService.print_context(**kwargs,set_filter=version)
            self.assertTrue(paper["sheets"])
            self.assertTrue(all(s["set_version"]==version for s in paper["sheets"]))
        wrong=revision.section_set_assignments.exclude(offering=offering).first().generated_set.set_code
        with self.assertRaises(PermissionDenied):
            PersonalizedAnswerSheetService.print_context(**kwargs,set_filter=wrong)
        self.enroll(offering,"PAIR-LATE")
        PersonalizedAnswerSheetService.prepare(**kwargs)
        after=dict(revision.personalized_answer_sheet_assignments.values_list("enrollment_id","set_code"))
        self.assertTrue(all(after[k]==v for k,v in first.items()))
        self.assertEqual(sorted(Counter(after.values()).values()),[2,2])

    def test_v1_revision_reprints_unchanged_after_new_paired_revision_and_live_edits(self):
        parent, _=self.policy_course()
        with patch.object(set_modes,"apply_policy",side_effect=historical_v1_plan):
            _, _, old=self.generate(parent)
        self.assertEqual(old.generated_sets.count(),3)
        before={s.set_code:AdminQuestionnairePrintService.build_safe_context(revision=old,set_code=s.set_code,actor=self.reviewer) for s in old.generated_sets.all()}
        first=parent.offering_snapshots.select_related("offering__section").first().offering.section
        first.name="Renamed live section"
        first.save(update_fields=["name"])
        problem,report=Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(report["ready"])
        current=ExamGenerationService.generate(cycle_course_id=parent.id,tenant_id=self.tenant.id,actor=self.reviewer,
            expected_current_revision=old.revision_number,expected_input_fingerprint=problem.input_fingerprint,
            regeneration=True,regeneration_reason="Create section A and B pairs",request_token="paired-historical-regeneration-token").revision
        self.assertEqual(current.generated_sets.count(),6)
        for code,context in before.items():
            reprint=AdminQuestionnairePrintService.build_safe_context(revision=old,set_code=code,actor=self.reviewer)
            self.assertEqual(reprint["items"],context["items"])
            self.assertEqual(reprint["section_label"],context["section_label"])
            self.assertEqual(len(GenerationAnswerKeyService.build_context(revision=old,set_code=code)["items"]),old.final_item_count_snapshot)
        migration=import_module("apps.departmental_exams.migrations.0038_section_ab_versions")
        with self.assertRaises(IrreversibleError):
            migration.preserve_section_pairs(apps,type("Editor",(),{"connection":connection})())

    def test_paired_historical_reprint_keeps_saved_section_and_both_keys(self):
        parent, _=self.policy_course(shuffle=True)
        _, _, old=self.generate(parent)
        before={s.set_code:AdminQuestionnairePrintService.build_safe_context(revision=old,set_code=s.set_code,actor=self.reviewer)
                for s in old.generated_sets.all()}
        keys={code:GenerationAnswerKeyService.build_context(revision=old,set_code=code)["items"] for code in before}
        section=parent.offering_snapshots.select_related("offering__section").first().offering.section
        section.name="Changed after final output"
        section.save(update_fields=["name"])
        problem, report=Stage6ReadinessService.build_problem(cycle_course=parent)
        self.assertTrue(report["ready"])
        new=ExamGenerationService.generate(cycle_course_id=parent.id,tenant_id=self.tenant.id,actor=self.reviewer,
            expected_current_revision=old.revision_number,expected_input_fingerprint=problem.input_fingerprint,
            regeneration=True,regeneration_reason="Regenerate section paired papers",request_token="new-pair-historical-reprint-token").revision
        self.assertNotEqual(new.pk,old.pk)
        for code, context in before.items():
            printed=AdminQuestionnairePrintService.build_safe_context(revision=old,set_code=code,actor=self.reviewer)
            self.assertEqual(printed["items"],context["items"])
            self.assertEqual(printed["section_label"],context["section_label"])
            self.assertEqual(printed["set_version"],context["set_version"])
            self.assertEqual(GenerationAnswerKeyService.build_context(revision=old,set_code=code)["items"],keys[code])


class SectionPairOrderTests(SimpleTestCase):
    @staticmethod
    def selection_problem(blocks):
        @dataclass
        class Problem:
            blocks: tuple
            margins: tuple
            questions: dict
            automatic_selection: object = None
            difficulty_quotas: dict | None = None

        return Problem(blocks=blocks, margins=(2,),
            questions={m.source_id: SimpleNamespace(normalized_fingerprint=f"q:{m.source_id}",
                                                    difficulty=m.difficulty)
                       for block in blocks for m in block.members})

    def test_budget_after_unorderable_solution_is_not_an_impossibility_proof(self):
        members = tuple(IdentityMember(q,q,"C","EASY",0) for q in (1,2,3,4))
        blocks = (IdentityBlock("case", (2,), members[:2]),
                  IdentityBlock("one", (1,), members[2:3]),
                  IdentityBlock("two", (1,), members[3:]))
        problem = self.selection_problem(blocks)
        with self.assertRaisesMessage(ValidationError, "computation budget reached") as error:
            set_modes.select_blocks(problem, Counter(), seed="budget", budget=2, require_orderable=True)
        self.assertNotIsInstance(error.exception, (set_modes.SectionOrderInfeasible,
                                                  set_modes.SectionQuotaInfeasible))
        selected = set_modes.select_blocks(problem, Counter(), seed="budget", budget=100, require_orderable=True)
        self.assertEqual(set(selected), {"one", "two"})

    def test_orderable_selection_retains_exact_reuse_minimum_and_determinism(self):
        from itertools import combinations

        blocks = (IdentityBlock("case", (2,), tuple(IdentityMember(q,q,"C","EASY",0) for q in (1,2))),
                  IdentityBlock("a", (1,), (IdentityMember(3,3,"C","EASY",1),)),
                  IdentityBlock("b", (1,), (IdentityMember(4,4,"C","EASY",2),)),
                  IdentityBlock("c", (1,), (IdentityMember(5,5,"C","EASY",1),)),
                  IdentityBlock("d", (1,), (IdentityMember(6,6,"C","EASY",2),)))
        problem = self.selection_problem(blocks)
        for costs in ((0,0,0,0,2,3), (0,0,5,1,3,2), (1,1,0,0,0,0)):
            usage = Counter({f"q:{q}": cost for q,cost in enumerate(costs,1)})
            expected = []
            for count in range(1,len(blocks)+1):
                for rows in combinations(blocks,count):
                    sections = Counter(b.members[0].section_id for b in rows)
                    if sum(b.size for b in rows)==2 and max(sections.values())>=2:
                        expected.append(sum(usage[f"q:{m.source_id}"] for b in rows for m in b.members))
            chosen = set_modes.select_blocks(problem,usage,seed="oracle",budget=1000,require_orderable=True)
            selected = [b for b in blocks if b.block_id in chosen]
            self.assertGreaterEqual(max(Counter(b.members[0].section_id for b in selected).values()),2)
            self.assertEqual(sum(usage[f"q:{m.source_id}"] for b in selected for m in b.members),min(expected))
            reversed_problem = self.selection_problem(tuple(reversed(blocks)))
            self.assertEqual(set_modes.select_blocks(reversed_problem,usage,seed="oracle",budget=1000,
                                                    require_orderable=True),chosen)

    def test_unorderable_preferred_mix_falls_back_without_relaxing_hard_quotas(self):
        blocks = (IdentityBlock("case", (2,), tuple(IdentityMember(q,q,"C","EASY",0) for q in (1,2))),
                  IdentityBlock("easy", (1,), (IdentityMember(3,3,"C","EASY",0),)),
                  IdentityBlock("moderate", (1,), (IdentityMember(4,4,"C","MODERATE",0),)))
        problem = self.selection_problem(blocks)
        problem.automatic_selection = True
        problem.difficulty_quotas = {"EASY":2,"MODERATE":0}
        self.assertEqual(set(set_modes.select_policy_blocks(problem,Counter(),seed="preferred",budget=100)),
                         {"easy","moderate"})

    def test_rank_collision_fallback_moves_whole_passage(self):
        members=tuple(IdentityMember(q,q,"C","EASY",0) for q in (1,2,3))
        blocks=(IdentityBlock(block_id="case",vector=(2,),members=members[:2]),
                IdentityBlock(block_id="single",vector=(1,),members=members[2:]))
        problem=SimpleNamespace(blocks=blocks,section_order=())
        with patch.object(set_modes,"order_selected_blocks",return_value=members):
            other=set_modes.paired_order(problem,("case","single"),code="S1B",fingerprint="test",reference=members)
        self.assertEqual([m.source_id for m in other],[3,1,2])

    def test_single_indivisible_passage_reports_order_blocker(self):
        members=tuple(IdentityMember(q,q,"C","EASY",0) for q in (1,2))
        problem=SimpleNamespace(blocks=(IdentityBlock(block_id="case",vector=(2,),members=members),),section_order=())
        with patch.object(set_modes,"order_selected_blocks",return_value=members):
            with self.assertRaisesMessage(ValidationError,"Cases/passages cannot be split"):
                set_modes.paired_order(problem,("case",),code="S1B",fingerprint="test",reference=members)
        with self.assertRaisesMessage(set_modes.SectionOrderInfeasible,"Blueprint quotas are satisfiable"):
            set_modes.select_blocks(self.selection_problem(problem.blocks),Counter(),seed="case-only",budget=100,
                                    require_orderable=True)
